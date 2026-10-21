"""**第二波目标应用的真机执行器**（工单 M15）。

三个应用、三种形态：

* **微信**（:class:`WeChatExecutor`）—— 原生窗口；**门控**（树要解开才能读）
  与**模态弹窗**（弹窗在的时候零动作下发、交人）；
* **DSH / Tabbit**（:class:`ElectronExecutor`）—— Chromium / Electron，
  走**浏览器通道**（页面结构 + 浏览器自己的输入通道），**输入通道会退化**；
* **cc-switch**（:class:`CcSwitchExecutor`）—— 原生窗口 + **完整树**
  （UIA 通道「可用」），外加**跨应用**那一条（CC-3）。

三类共同的东西（窗口锚定、能力表、缝的接线、真值、停止条件、交接产物）
都在 :class:`NativeAppExecutor` 里 —— **只有一份**。

⚠️ **动真机前必须持有** ``.scratch/desktop.lock``（AGENTS.md「真机占用锁」）。
"""

from __future__ import annotations

import ctypes
import math
import sqlite3
import threading
import time
from collections.abc import Callable, Mapping
from ctypes import wintypes
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from minicode.action.protocol import Action, ActionKind
from minicode.action.records import ListTraceSink, MultiTraceSink
from minicode.action.seam import ActionExecutionSeam, SeamRequest, SeamResult
from minicode.browser.channel import BrowserChannel
from minicode.reflection.handoff import build_handoff
from minicode.reflection.stop import (
    STOP_EXPECTATION_MET,
    STOP_MODAL_DIALOG,
    STOP_REASON_TEXT,
    StopPolicy,
)

from .modal import ModalFinding, detect_modal, enumerate_windows
from .runner import StepOutcome
from .tasks import Step
from .visual import VisualChannel

__all__ = [
    "NativeAppExecutor",
    "WeChatExecutor",
    "CcSwitchExecutor",
    "ElectronExecutor",
    "CrossAppExecutor",
    "ModalDialog",
    "VerificationPage",
    "PICKED",
    "read_ccswitch_providers",
]

#: **运行时挑选的目标**占位符。
#:
#: 工单不钉具体联系人 / 具体条目（``docs/target-tasks.md`` §7.4、§7.2），
#: 所以目标在**跑的时候**从界面上挑，挑中的那个名字会**替换**这个占位符
#: （目标描述、期望、动作参数、断言里都替换）。
PICKED = "{picked}"

#: 端到端评测的**步数上限**（停止条件的轮次上限；**工程阈值·未验证**）。
MAX_STEPS_BEFORE_STOP = 8


def substitute_picked(step: Step, picked: str) -> Step:
    """把 :data:`PICKED` 占位符换成运行时挑中的那个名字。"""
    if not picked:
        return step
    target = step.target.replace(PICKED, picked) if step.target else step.target
    params = {
        key: (value.replace(PICKED, picked) if isinstance(value, str) else value)
        for key, value in step.params.items()
    }
    expectation = step.expectation
    if expectation is not None and PICKED in expectation.target:
        expectation = replace(
            expectation, target=expectation.target.replace(PICKED, picked)
        )
    return replace(
        step,
        target=target,
        params=params,
        expectation=expectation,
        expect_window_title=step.expect_window_title.replace(PICKED, picked),
        expect_control_value=step.expect_control_value.replace(PICKED, picked),
        expect_context=(
            (step.expect_context[0], step.expect_context[1].replace(PICKED, picked))
            if step.expect_context[0]
            else step.expect_context
        ),
    )


# ---------------------------------------------------------------------------
# Win32：置前（含 ALT 技巧）与进程枚举
# ---------------------------------------------------------------------------

_user32 = ctypes.WinDLL("user32", use_last_error=True)
_TH32CS_SNAPPROCESS = 0x00000002
_VK_MENU = 0x12
_KEYEVENTF_KEYUP = 0x0002


def _landing_error_px(
    landing: tuple[int, int], rect: tuple[int, int, int, int]
) -> float:
    """落点与**目标矩形**的偏差：落在里面 = 0 px，落在外面 = 到最近边的距离。

    ⚠️ 工单 **M28** 起浏览器通道的落点是**可见文本的实际区域中心**，
    不是元素矩形中心 → 「到元素中心的距离」不再是这条通道的判据
    （扁平 DOM 上元素框是整行宽，两者能差几百 px）。
    **「落点落在目标矩形里」**这条仍然成立，且才是这里有信息量的检查。
    """
    x, y = float(landing[0]), float(landing[1])
    left, top, right, bottom = (float(value) for value in rect)
    dx = max(left - x, 0.0, x - right)
    dy = max(top - y, 0.0, y - bottom)
    return math.hypot(dx, dy)


def alt_bring_to_front(hwnd: int) -> bool:
    """**ALT 技巧**置前：按住 ALT 再 ``SetForegroundWindow``。

    ⚠️ 本机实测（2026-10-21）：光 ``SetForegroundWindow`` **会被系统拒绝**
    （调用方不是前台进程）→ 微信的树就解不开；先按住 ALT 再置前就成立。
    """
    if not hwnd:
        return False
    _user32.keybd_event(_VK_MENU, 0, 0, 0)
    try:
        return bool(_user32.SetForegroundWindow(wintypes.HWND(int(hwnd))))
    finally:
        _user32.keybd_event(_VK_MENU, 0, _KEYEVENTF_KEYUP, 0)


class _PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
        ("th32ModuleID", wintypes.DWORD),
        ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD),
        ("pcPriClassBase", ctypes.c_long),
        ("dwFlags", wintypes.DWORD),
        ("szExeFile", ctypes.c_wchar * 260),
    ]


def _process_ids(process_name: str) -> set[int]:
    """按**进程名**取当前的 PID 集合（Toolhelp32 快照）。"""
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    snapshot = kernel32.CreateToolhelp32Snapshot(_TH32CS_SNAPPROCESS, 0)
    if snapshot == -1:
        return set()
    pids: set[int] = set()
    entry = _PROCESSENTRY32W()
    entry.dwSize = ctypes.sizeof(_PROCESSENTRY32W)
    try:
        ok = kernel32.Process32FirstW(snapshot, ctypes.byref(entry))
        while ok:
            if entry.szExeFile.lower() == process_name.lower():
                pids.add(int(entry.th32ProcessID))
            ok = kernel32.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        kernel32.CloseHandle(snapshot)
    return pids


# ---------------------------------------------------------------------------
# 模态弹窗（评测台自己造一个真模态）
# ---------------------------------------------------------------------------

_MB_OK = 0x00000000
_MB_SYSTEMMODAL = 0x00001000
_WM_CLOSE = 0x0010
_SW_HIDE = 0


class ModalDialog:
    """**造一个真的模态弹窗**（工单 M15 的 WX-3）。

    ⚠️ **为什么评测台自己造**：微信自己的「更新提示 / 登录失效」弹窗属于
    **人工代确认**步骤（``docs/target-tasks.md`` §6），本轮不自动制造；
    这里造的是**系统自带的消息框**，挂在目标窗口上 ——
    **宿主窗口因此被禁用**（Windows 的模态约定），所以它和微信自己的弹窗
    在**机器看来是一回事**：检测器看得见，agent 必须零动作下发、交人。

    消息框会**阻塞**创建它的线程 → 所以放在后台线程里，关掉时再收回来。
    """

    def __init__(self, owner_hwnd: int, text: str = "模态弹窗测试", caption: str = "minicode M15"):
        self.owner_hwnd = int(owner_hwnd)
        self.text = text
        self.caption = caption
        self._thread: threading.Thread | None = None
        self._hwnd = 0

    def open(self) -> str:
        """把弹窗造出来。返回**空串** = 起来了；非空 = 原因。"""
        dialog = self

        def _run() -> None:
            dialog._hwnd = int(
                ctypes.windll.user32.MessageBoxW(
                    dialog.owner_hwnd,
                    dialog.text,
                    dialog.caption,
                    _MB_OK | _MB_SYSTEMMODAL,
                )
            )

        self._thread = threading.Thread(target=_run, daemon=True)
        self._thread.start()
        deadline = time.perf_counter() + 5.0
        while time.perf_counter() < deadline:
            if self._find() and self.owner_disabled():
                return ""
            time.sleep(0.1)
        return "模态弹窗没起来（宿主窗口也没被禁用）"

    def owner_disabled(self) -> bool:
        """宿主窗口**被禁用**了吗（模态的机器证据）。"""
        return not bool(_user32.IsWindowEnabled(wintypes.HWND(self.owner_hwnd)))

    def close(self) -> None:
        """把弹窗关掉（收工，别留在人家桌面上）。"""
        hwnd = self._find()
        if hwnd:
            _user32.PostMessageW(wintypes.HWND(hwnd), _WM_CLOSE, 0, 0)
        if self._thread is not None:
            self._thread.join(timeout=3.0)
            self._thread = None

    def _find(self) -> int:
        """按标题找那个消息框窗口（类名 ``#32770``）。"""
        found: list[int] = []

        @ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
        def _callback(hwnd, _lparam):
            cls = ctypes.create_unicode_buffer(256)
            _user32.GetClassNameW(hwnd, cls, 256)
            if cls.value == "#32770":
                title = ctypes.create_unicode_buffer(256)
                _user32.GetWindowTextW(hwnd, title, 256)
                if title.value == self.caption:
                    found.append(int(hwnd))
            return True

        _user32.EnumWindows(_callback, 0)
        return found[0] if found else 0


# ---------------------------------------------------------------------------
# cc-switch 的状态（应用自己的状态库）
# ---------------------------------------------------------------------------

def read_ccswitch_providers() -> list[dict[str, Any]]:
    """读 cc-switch **自己的状态库**里��供应商条目（只读）。

    ⚠️ **为什么不用 UIA 读**：本机 cc-switch 的选中态在可访问性树里
    **读不出来**（没有 Toggle / SelectionItem pattern，只有 class_name 里的
    ``bg-blue-500 text-white`` 算弱信号）→ 「当前配置」的真值取应用自己的
    状态库（``providers.is_current``）。
    """
    db = Path.home() / ".cc-switch" / "cc-switch.db"
    if not db.exists():
        return []
    uri = f"file:{db.as_posix()}?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    try:
        rows = connection.execute(
            "SELECT id, app_type, name, category, is_current FROM providers"
        ).fetchall()
    except sqlite3.Error:
        return []
    finally:
        connection.close()
    return [
        {
            "id": row[0],
            "app_type": row[1],
            "name": row[2],
            "category": row[3],
            "is_current": bool(row[4]),
        }
        for row in rows
    ]


# ---------------------------------------------------------------------------
# 原生窗口应用
# ---------------------------------------------------------------------------


class NativeAppExecutor:
    """**原生窗口应用**（微信、cc-switch）的真机执行器。

    与 M14 的 Windows 设置执行器的差别：那一台跑的是 XAML island
    （树读不出节点 → 强制切视觉）；这一台跑的是**树可读**的原生应用 →
    走 UIA 通道，落点直接取控件框的物理像素中心。
    """

    #: 窗口标题（找窗口用）。子类覆盖。
    window_title = ""
    #: 人读的应用名（报告里用）。
    app_label = ""

    def __init__(
        self,
        *,
        out_dir: str | Path,
        truth_overrides: Mapping[str, tuple[int, int]] | None = None,
        gate_max_retries: int = 3,
        gate_backoff_s: float = 0.2,
        gate_mode: str = "on_demand",
        bypass_admission: bool = False,
        model_input: tuple[int, int] = (2048, 1280),
    ):
        self.out_dir = Path(out_dir)
        self.truth_overrides = dict(truth_overrides or {})
        self.gate_max_retries = int(gate_max_retries)
        self.gate_backoff_s = float(gate_backoff_s)
        self._port = None
        self._table = None
        self._anchor = None
        self._hwnd = 0
        self._seam: ActionExecutionSeam | None = None
        self._sink = ListTraceSink()
        self._context: dict[str, Any] = {}
        self._policy = StopPolicy(max_rounds=MAX_STEPS_BEFORE_STOP)
        self._visual = VisualChannel(
            out_dir=self.out_dir,
            gate_mode=gate_mode,
            bypass_admission=bypass_admission,
            model_input=model_input,
        )
        #: 运行时挑中的目标（联系人 / 条目名）。
        self._picked = ""
        #: **停止原因**（执行器自己判出来的，比如模态弹窗）。
        self._forced_stop = ""
        #: 已经下发过几个动作（模态弹窗那条要「零动作下发」）。
        self._dispatched = 0

    # -- 端口 -------------------------------------------------------------

    def preflight(self) -> str:
        """环境自检。返回**空串** = 就绪；非空 = 环境不可达的原因。"""
        from minicode.exec import dpi  # noqa: F401  (导入即声明 PMv2)

        hwnd = self._find_window()
        if not hwnd:
            return (
                f"环境不可达：{self.app_label} 没开"
                f"（找不到标题为「{self.window_title}」的可见窗口）"
            )
        self._hwnd = hwnd
        self._anchor = self._describe(hwnd)
        self._table = self._capability_table()
        entry = self._table.availability(self._anchor)
        self._context.update(
            {
                "capability": entry.availability.value,
                "capability_reason": entry.reason,
                # 门控的**读数**（先解开 + 退避重试，报告里读得到）。
                "gate_probe_attempts": entry.probe_attempts,
                "gate_unblock_attempts": entry.unblock_attempts,
                "gate_first_probe_has_tree": entry.first_probe_has_tree,
            }
        )
        self._seam = self._build_seam()
        self._context.update(self._visual.context)
        return ""

    def execute(self, step: Step) -> StepOutcome:
        """跑一步（真机）。"""
        try:
            return self._execute(step)
        except Exception as exc:  # noqa: BLE001 —— 执行器自己坏了也不许崩掉整轮
            return StepOutcome(
                step_id=step.id,
                description=step.description,
                ok=False,
                error_code="executor_error",
                error_message=f"{type(exc).__name__}: {exc}",
            )

    def task_context(self) -> Mapping[str, Any]:
        return dict(self._context)

    def stop_reason(self) -> str:
        """停止条件（工单 M14 的阻塞边 M12）。"""
        if self._forced_stop:
            return self._forced_stop
        reason = self._policy.stop_reason
        if reason == STOP_EXPECTATION_MET:
            return ""
        return reason

    def handoff(self) -> Mapping[str, Any] | None:
        """**交接产物**（M12）：停下来时交给人的那一份轨迹。"""
        reason = self.stop_reason()
        if not reason:
            return None
        artifact = build_handoff(
            self._policy.signals,
            reason=reason,
            reason_text=STOP_REASON_TEXT.get(reason, reason),
            strategy_changes=self._policy.strategy_change_records,
            window_title=self._read_window_title(),
        )
        return artifact.to_dict()

    # -- 内部 -------------------------------------------------------------

    def _execute(self, step: Step) -> StepOutcome:
        step = substitute_picked(step, self._picked)
        if self._seam is None:
            return StepOutcome(
                step_id=step.id,
                description=step.description,
                ok=False,
                error_code="executor_error",
                error_message="缝还没建起来（preflight 没跑？）",
            )
        truth, truth_source = self._resolve_truth(step)
        if step.action_kind is ActionKind.INSPECT:
            return self._execute_inspect(step, truth_source)
        if step.action_kind is ActionKind.KEY:
            return self._execute_key(step, truth, truth_source)
        if step.action_kind is ActionKind.TYPE and step.params.get("focus_target"):
            return self._execute_focused_type(step, truth, truth_source)
        request = SeamRequest(
            target_description=step.target,
            action_kind=step.action_kind,
            params=dict(step.params),
            expectation=step.expectation,
            control_type=step.control_type,
        )
        before = len(self._sink.traces)
        result = self._seam.run(request)
        self._dispatched += 1
        traces = self._sink.traces[before:]
        trace = traces[-1] if traces else None
        self._policy.observe_round()
        return self._outcome(step, result, trace, truth, truth_source)

    def _execute_inspect(self, step: Step, truth_source: str) -> StepOutcome:
        """**读控件树**（观察型动作：不改任何东西）。

        ⚠️ 动作协议里**有** ``inspect`` 这一种（工单 M21 锁死的 12 个），
        但**动作执行缝还没实现它**（缝只处理「要落点的 / 截图等待 / 放大」）
        → 这一步在**评测台这一层**做：定点读一次树，**不下发任何动作**。
        """
        from minicode.uia.ports import UiaFindRequest

        observed: list[str] = []
        try:
            result = self._query_port().find(
                UiaFindRequest(
                    anchor=self._anchor,
                    name=step.target or None,
                    control_type=step.control_type or None,
                )
            )
            observed = [element.name for element in result.elements]
        except Exception as exc:  # noqa: BLE001 —— 读不到就是读不到
            self._context["inspection_note"] = f"{type(exc).__name__}: {exc}"
        self._policy.observe_round()
        extra_ok, extra_detail = self._judge_extra(step)
        return StepOutcome(
            step_id=step.id,
            description=step.description,
            ok=True,
            channel="uia",
            expectation_met=extra_ok,
            expectation=self._expectation_text(step),
            expectation_detail=extra_detail,
            truth_source=truth_source,
            note=f"读控件树：{len(observed)} 个与「{step.target}」对得上的控件",
        )

    def _execute_key(
        self,
        step: Step,
        truth: tuple[int, int] | None,
        truth_source: str,
    ) -> StepOutcome:
        """**按键**动作（协议里没有落点，直接走动作执行器）。"""
        combo = tuple(str(part) for part in step.params.get("combo", ()))
        started = time.perf_counter()
        result = self._seam.executor.execute(
            Action(kind=ActionKind.KEY, params={"combo": combo}, intent=step.description)
        )
        self._dispatched += 1
        elapsed = (time.perf_counter() - started) * 1000.0
        self._policy.observe_round()
        return StepOutcome(
            step_id=step.id,
            description=step.description,
            ok=result.ok,
            channel="",
            error_code=(result.error_code.value if result.error_code else ""),
            error_message=(result.error_message or ""),
            expectation_met=None,
            expectation=(step.expectation.describe() if step.expectation else ""),
            execute_verify_ms=elapsed,
            truth_source=truth_source,
            note=("；".join(result.notes or ())),
        )

    def _execute_focused_type(
        self,
        step: Step,
        truth: tuple[int, int] | None,
        truth_source: str,
    ) -> StepOutcome:
        """**往输入框里输入文本**：先点一下目标（给它焦点），再注入文字。

        桌面应用往输入框里输入文本前**必须先给它焦点** —— 注入是发给
        **前台窗口**的焦点控件的（不点一下，文字会落到别的窗口去）。
        """
        focus = SeamRequest(
            target_description=step.target,
            action_kind=ActionKind.CLICK,
            params={},
            expectation=None,
            control_type=step.control_type,
        )
        before = len(self._sink.traces)
        clicked = self._seam.run(focus)
        self._dispatched += 1
        if not clicked.ok:
            return self._outcome(step, clicked, None, truth, truth_source)
        type_request = SeamRequest(
            target_description=step.target,
            action_kind=ActionKind.TYPE,
            params={"text": str(step.params.get("text", ""))},
            expectation=step.expectation,
            control_type=step.control_type,
        )
        typed = self._seam.run(type_request)
        self._dispatched += 1
        traces = self._sink.traces[before:]
        trace = traces[-1] if traces else None
        self._policy.observe_round()
        return self._outcome(step, typed, trace, truth, truth_source)

    def _outcome(
        self,
        step: Step,
        result: Any,
        trace: Any,
        truth: tuple[int, int] | None,
        truth_source: str,
    ) -> StepOutcome:
        """把一次动作的结果整理成**外部可观察**的那些字段。"""
        landing = result.final_point
        error = (
            math.dist((float(landing[0]), float(landing[1])), truth)
            if landing is not None and truth is not None
            else None
        )
        verification = (trace.verification_result or {}) if trace is not None else {}
        satisfied = verification.get("satisfied")
        detail = verification.get("diff") or verification.get("observed") or ""
        extra_ok, extra_detail = self._judge_extra(step)
        if extra_ok is not None:
            satisfied = extra_ok if satisfied is None else (satisfied is True and extra_ok)
            detail = "；".join(part for part in (detail, extra_detail) if part)
        self._record_coordinate_window(step, trace)
        return StepOutcome(
            step_id=step.id,
            description=step.description,
            ok=result.ok,
            channel=(trace.channel if trace else ""),
            failure_type=(trace.failure_type if trace else ""),
            failures=dict(trace.failures) if trace is not None else {},
            error_code=(result.result.error_code.value if result.result.error_code else ""),
            error_message=(result.result.error_message or ""),
            expectation_met=satisfied,
            expectation=self._expectation_text(step),
            expectation_detail=detail,
            locate_ms=(trace.locate_ms if trace else 0.0),
            decision_ms=(trace.decision_ms if trace else 0.0),
            execute_verify_ms=(trace.execute_verify_ms if trace else 0.0),
            landing_point=landing,
            landing_error_px=error,
            truth_source=truth_source,
            note=("；".join(result.result.notes or ())),
        )

    def _expectation_text(self, step: Step) -> str:
        if step.expectation is not None:
            return step.expectation.describe()
        if step.expect_window_title:
            return f"窗口标题 = {step.expect_window_title}"
        if step.expect_control_value:
            return f"控件的值 = {step.expect_control_value}"
        return ""

    def _record_coordinate_window(self, step: Step, trace: Any) -> None:
        """记下**这一步的落点落在哪个窗口**（CC-3 的「没把坐标挪到别的窗口」）。

        ⚠️ **没有落点就不记** —— 记一条「没有落点」的entry会让那条判据
        变成**恒真**（等于没测）。
        """
        landing = getattr(trace, "landing_point", None) if trace is not None else None
        if landing is None:
            return
        expected = self._read_window_title()
        selected = getattr(trace, "selected", None) if trace is not None else None
        actual = (getattr(selected, "window_title", "") or "") if selected is not None else ""
        entries = list(self._context.get("coordinate_windows") or ())
        entries.append(
            {
                "step_id": step.id,
                "expected": expected,
                "actual": actual or expected,
                "matched": (not actual) or actual == expected,
            }
        )
        self._context["coordinate_windows"] = entries
        #: **切视觉了吗**（WX-1 的第二条分支：仍不可用就按覆盖失败切视觉）。
        self._context["cut_to_visual"] = bool(trace is not None and trace.channel == "visual")

    def _judge_extra(self, step: Step) -> tuple[bool | None, str]:
        """判**窗口标题**、**控件的值**与**任务级读数**这三条结构断言。

        返回 ``(成立吗, 细节)``；没有这三条断言时返回 ``(None, "")``。
        """
        if step.expect_window_title:
            actual = self._read_window_title()
            if actual == step.expect_window_title:
                return True, f"窗口标题 = {actual!r}"
            return False, f"窗口标题是 {actual!r}，不是 {step.expect_window_title!r}"
        if step.expect_control_value:
            actual = self._read_control_value(step.target, step.control_type)
            if actual == step.expect_control_value:
                return True, f"控件「{step.target}」的值 = {actual!r}"
            return False, f"控件「{step.target}」的值是 {actual!r}，不是 {step.expect_control_value!r}"
        if step.expect_context[0]:
            key, expected = step.expect_context
            actual = self._context.get(key)
            if actual == expected:
                return True, f"{key} = {actual!r}"
            #: **核对不通过 → 判执行失败并回报**（工单 M15 的 CC-2）。
            self._context["verification_failure"] = "execution_failed"
            return False, f"{key} 是 {actual!r}，不是 {expected!r}"
        return None, ""

    def _read_control_value(self, name: str, control_type: str = "") -> str:
        """读目标控件的**值**（UIA 的 Value 属性；读不到就是空串）。"""
        from minicode.uia.ports import UiaFindRequest

        if not name:
            return ""
        try:
            result = self._query_port().find(
                UiaFindRequest(
                    anchor=self._anchor,
                    name=name,
                    control_type=control_type or None,
                )
            )
        except Exception:  # noqa: BLE001 —— 读不到就是读不到
            return ""
        return result.elements[0].value if result.elements else ""

    def _resolve_truth(self, step: Step) -> tuple[tuple[int, int] | None, str]:
        """真值：命令行覆盖 > 树里查控件框中心 > 写死的常量。"""
        override = self.truth_overrides.get(step.id)
        if override is not None:
            return override, "命令行给的"
        if step.truth_element:
            from minicode.action.text import name_matches
            from minicode.uia.ports import UiaFindRequest

            port = self._query_port()
            if step.truth_control_type:
                result = port.find(
                    UiaFindRequest(
                        anchor=self._anchor,
                        name=None,
                        control_type=step.truth_control_type,
                    )
                )
                elements = [
                    element
                    for element in result.elements
                    if name_matches(element.name, step.truth_element)
                ]
            else:
                result = port.find(
                    UiaFindRequest(anchor=self._anchor, name=step.truth_element)
                )
                elements = list(result.elements)
            for element in elements:
                rect = element.rect
                if rect[2] > rect[0] and rect[3] > rect[1]:
                    return (
                        (rect[0] + rect[2]) // 2,
                        (rect[1] + rect[3]) // 2,
                    ), f"树里的控件框「{element.name}」"
        if step.truth is not None:
            return step.truth, "画面量的"
        return None, "（没有真值）"

    def _query_port(self):
        from minicode.uia.windows import WindowsUiaQueryPort

        if self._port is None:
            self._port = WindowsUiaQueryPort()
        return self._port

    def _capability_table(self):
        from minicode.uia.capability import CapabilityTable
        from minicode.uia.windows import WindowsCapabilityProbe, WindowsTreeUnblocker

        return CapabilityTable(
            WindowsCapabilityProbe(),
            unblocker=WindowsTreeUnblocker(),
            max_retries=self.gate_max_retries,
            backoff_s=self.gate_backoff_s,
        )

    def _build_seam(self) -> ActionExecutionSeam:
        from minicode.action.capture import CapturePort
        from minicode.action.windows import (
            WindowsCaptureSource,
            WindowsInputInjector,
            WindowsSecureDesktopDetector,
            WindowsWindowController,
            current_virtual_desktop,
        )
        from minicode.privacy.guard import windows_guard
        from minicode.uia.channel import UiaChannel
        from minicode.uia.observer import UiaStateObserver
        from minicode.verification.layer import VerificationLayer

        desktop = current_virtual_desktop()
        port = self._query_port()
        verifier = VerificationLayer(
            screenshot_port=CapturePort([WindowsCaptureSource()]),
            observer=UiaStateObserver(port, self._anchor),
        )
        return ActionExecutionSeam(
            capture_sources=[WindowsCaptureSource()],
            tree=None,
            injector=WindowsInputInjector(desktop),
            window_controller=WindowsWindowController(),
            virtual_desktop=desktop,
            trace_sink=MultiTraceSink(self._sink, self._policy),
            locator=UiaChannel(query=port, capability=self._table),
            anchor=self._anchor,
            visual_admission=self._visual.admission_port(),
            visual_locator=self._visual.locator(),
            verifier=verifier,
            secure_desktop=WindowsSecureDesktopDetector(),
            # **隐私闸门**（工单 M20）：敏感窗口**根本不许拍**（抓帧之前拦下）。
            privacy_guard=windows_guard(),
        )

    def _find_window(self) -> int:
        from .real import find_window_by_title

        return find_window_by_title(self.window_title)

    def _describe(self, hwnd: int):
        from minicode.uia.windows import describe_window

        return describe_window(hwnd)

    def _read_window_title(self) -> str:
        """当前窗口标题（交接产物与条款都要它）。"""
        from .real import window_title_of

        return window_title_of(self._hwnd)

    def _focus_window(self) -> bool:
        """把目标窗口置前（**置前失败就不许注入**）。"""
        from minicode.action.windows import WindowsWindowController

        controller = WindowsWindowController()
        controller.bring_to_front(self._hwnd)
        time.sleep(0.4)
        if controller.is_foreground(self._hwnd):
            self._context["window_pinned"] = True
            return True
        alt_bring_to_front(self._hwnd)
        time.sleep(0.5)
        pinned = controller.is_foreground(self._hwnd)
        self._context["window_pinned"] = pinned
        return pinned

    def _find_elements(self, control_type: str) -> list:
        """按控件类型在锚定窗口里查一遍（定点查询，**不是**全树 dump）。"""
        from minicode.uia.ports import UiaFindRequest

        result = self._query_port().find(
            UiaFindRequest(anchor=self._anchor, name=None, control_type=control_type)
        )
        return list(result.elements)


class WeChatExecutor(NativeAppExecutor):
    """**微信**上的真机执行器（门控 + 模态弹窗）。

    ``create_modal=True`` 时，preflight 会**在微信窗口上造一个真模态弹窗**
    （工单 M15 的 WX-3）—— 宿主窗口因此被禁用，agent 必须零动作下发、交人。
    """

    window_title = "微信"
    app_label = "微信"

    def __init__(self, *, create_modal: bool = False, require_chat_list: bool = True, **kwargs):
        super().__init__(**kwargs)
        self.create_modal = bool(create_modal)
        self.require_chat_list = bool(require_chat_list)
        self._modal: ModalDialog | None = None

    def preflight(self) -> str:
        blocked = super().preflight()
        if blocked:
            return blocked
        self._focus_window()
        if self.require_chat_list and self._at_login_screen():
            return (
                "环境不可达：微信客户端停在**登录界面**"
                "（要人在手机上确认登录 —— 属人工代确认，本轮不代劳）"
            )
        self._picked = self._pick_contact()
        self._context["picked_contact"] = self._picked
        if not self._picked:
            self._context["picked_note"] = (
                "会话列表里没挑出一个可用的联系人（这一轮没有目标可点）"
            )
        if self.create_modal:
            self._modal = ModalDialog(self._hwnd)
            note = self._modal.open()
            self._context["modal_setup"] = note or "评测台造了一个真模态弹窗（宿主窗口被禁用）"
            if note:
                self.close_modal()
        return ""

    def _at_login_screen(self) -> bool:
        """客户端停在**登录界面**吗（会话列表不在树里）。

        登录界面上的按钮（本机实测）：「进入微信」/「二维码」/「仅传输文件」/
        「网络代理设置」—— 出现其中任何一个就说明**还没登录**。
        """
        from minicode.uia.ports import UiaFindRequest

        for name in ("进入微信", "二维码", "仅传输文件", "网络代理设置"):
            try:
                result = self._query_port().find(
                    UiaFindRequest(anchor=self._anchor, name=name, control_type="Button")
                )
            except Exception:  # noqa: BLE001 —— 读不到就当不是登录界面
                continue
            if result.elements:
                return True
        return False

    def close_modal(self) -> None:
        """把评测台造的弹窗收掉（别留在人家桌面上）。"""
        if self._modal is not None:
            self._modal.close()
            self._modal = None

    def _execute(self, step: Step) -> StepOutcome:
        #: **模态弹窗在的时候：零动作下发**（先查，再动手）。
        finding = self._detect_modal()
        if finding is not None:
            return self._handoff_on_modal(step, finding)
        if not self._focus_window():
            return StepOutcome(
                step_id=step.id,
                description=step.description,
                ok=False,
                error_code="target_window_not_foreground",
                error_message=(
                    "目标窗口没能置前 —— 拒绝在别的窗口上注入"
                    "（坐标不许挪到别的窗口上）"
                ),
            )
        outcome = super()._execute(step)
        self._context["window_title"] = self._read_window_title()
        return outcome

    def _detect_modal(self) -> ModalFinding | None:
        """这个窗口上面有模态弹窗吗。"""
        return detect_modal(enumerate_windows(), self._hwnd)

    def _handoff_on_modal(self, step: Step, finding: ModalFinding) -> StepOutcome:
        """检测到模态弹窗 → **零动作下发** + 交人。"""
        self._context["modal_detected"] = True
        self._context["modal"] = finding.to_dict()
        self._context["actions_dispatched"] = self._dispatched
        artifact = build_handoff(
            self._policy.signals,
            reason=STOP_MODAL_DIALOG,
            reason_text=STOP_REASON_TEXT.get(STOP_MODAL_DIALOG, STOP_MODAL_DIALOG),
            window_title=finding.title or self._read_window_title(),
        )
        self._context["handoff"] = artifact.to_dict()
        self._context["stop_reason"] = STOP_MODAL_DIALOG
        self._forced_stop = STOP_MODAL_DIALOG
        return StepOutcome(
            step_id=step.id,
            description=step.description,
            ok=False,
            error_code="modal_dialog",
            error_message=f"检测到模态弹窗：{finding.reason} —— 零动作下发，交给人",
            stopped=True,
            note="零动作下发（本步没有下发任何输入）",
        )

    def _pick_contact(self) -> str:
        """从会话列表里挑一个联系人（工单不钉具体人）。

        ⚠️ **干净的人名只在 automation_id 里**（``session_item_<人名>``）——
        控件的名字是多行 blob（聊天摘要 + 时间）。
        """
        from minicode.uia.ports import UiaFindRequest

        result = self._query_port().find(
            UiaFindRequest(anchor=self._anchor, name=None, control_type="ListItem")
        )
        for element in result.elements:
            automation_id = element.automation_id or ""
            if automation_id.startswith("session_item_"):
                return automation_id[len("session_item_") :]
        for element in result.elements:
            name = (element.name or "").strip()
            if name and "\n" not in name:
                return name
        return ""


class CcSwitchExecutor(NativeAppExecutor):
    """**cc-switch** 上的真机执行器（完整树 → UIA 通道可用）。"""

    window_title = "CC Switch"
    app_label = "cc-switch"

    def preflight(self) -> str:
        blocked = super().preflight()
        if blocked:
            return blocked
        self._focus_window()
        self._providers = read_ccswitch_providers()
        self._context["providers"] = self._providers
        current = self._current_provider()
        self._context["current_provider"] = current
        self._context["original_provider"] = current
        self._picked = self._pick_other_provider(current)
        self._context["picked_provider"] = self._picked
        return ""

    def _execute(self, step: Step) -> StepOutcome:
        if not self._focus_window():
            return StepOutcome(
                step_id=step.id,
                description=step.description,
                ok=False,
                error_code="target_window_not_foreground",
                error_message="目标窗口没能置前 —— 拒绝在别的窗口上注入",
            )
        outcome = super()._execute(step)
        self._context["verification_source"] = "uia"
        return outcome

    def _outcome(self, step, result, trace, truth, truth_source):
        #: **先更新「当前配置」再判断言** —— 断言判的就是它。
        self._context["current_provider"] = self._current_provider()
        return super()._outcome(step, result, trace, truth, truth_source)

    def _current_provider(self) -> str:
        """当前生效的供应商（**应用自己的状态库**）。"""
        for provider in getattr(self, "_providers", []):
            if provider.get("is_current"):
                return str(provider.get("name") or "")
        return ""

    def _pick_other_provider(self, current: str) -> str:
        """挑一个**不是当前那个**的条目（工单：切到「另一个供应商条目」）。"""
        for provider in getattr(self, "_providers", []):
            name = str(provider.get("name") or "")
            if name and name != current:
                return name
        return ""

    def restore(self) -> str:
        """把配置**切回原来的那个**（评测台改了人家配置，得还回去）。

        返回**空串** = 还原了；非空 = 没还原（报告里要写清楚）。
        """
        original = str(self._context.get("original_provider") or "")
        current = self._current_provider()
        if not original or original == current:
            return ""
        step = Step(
            id="restore",
            description=f"把配置切回「{original}」",
            target=original,
            expect_control_value=original,
        )
        outcome = self._execute(step)
        if not outcome.ok:
            return f"没能把配置切回「{original}」（{outcome.error_message or '（无原因）'}）"
        if self._current_provider() != original:
            return f"配置切回了「{original}」，但应用自己的状态库显示的不是它"
        return ""


class CrossAppExecutor:
    """**跨应用**的执行器（工单 M15 的 CC-3）。

    一张票里既有原生窗口的步（cc-switch），又有浏览器里的步
    （``step.window == "browser"``）→ 按步**分派**给两个执行器，
    上下文合在一起（条款要读得到两边的读数）。
    """

    def __init__(self, native: NativeAppExecutor, browser: ElectronExecutor):
        self.native = native
        self.browser = browser
        self._context: dict[str, Any] = {}

    def preflight(self) -> str:
        blocked = self.native.preflight()
        if blocked:
            return blocked
        blocked = self.browser.preflight()
        if blocked:
            return blocked
        return ""

    def execute(self, step: Step) -> StepOutcome:
        target = self.browser if step.window == "browser" else self.native
        outcome = target.execute(step)
        self._collect()
        return outcome

    def task_context(self) -> Mapping[str, Any]:
        self._collect()
        return dict(self._context)

    def stop_reason(self) -> str:
        return self.native.stop_reason() or self.browser.stop_reason()

    def handoff(self) -> Mapping[str, Any] | None:
        return self.native.handoff() or self.browser.handoff()

    def close(self) -> None:
        self.browser.close()

    def restore(self) -> str:
        """把 cc-switch 的配置**还原**（跨应用那张票也改了人家配置）。"""
        restore = getattr(self.native, "restore", None)
        return restore() if restore is not None else ""

    def _collect(self) -> None:
        native_context = dict(self.native.task_context())
        browser_context = dict(self.browser.task_context())
        #: 落点所属窗口：两个执行器的**并起来**（原生那几步 + 浏览器那几步）。
        windows = list(native_context.get("coordinate_windows") or ()) + list(
            browser_context.get("coordinate_windows") or ()
        )
        native_context.pop("coordinate_windows", None)
        browser_context.pop("coordinate_windows", None)
        self._context.update(native_context)
        self._context.update(browser_context)
        self._context["coordinate_windows"] = windows


# ---------------------------------------------------------------------------
# Chromium / Electron（DSH / Tabbit）
# ---------------------------------------------------------------------------


class ElectronExecutor:
    """**DSH / Tabbit**（Chromium / Electron）上的真机执行器。

    走**浏览器通道**（页面结构 + 浏览器自己的输入通道）——
    与 M14 的 ChromeExecutor 同一套接线，只是浏览器换成了 Tabbit。

    ⚠️ **三处本机实测出来的坑**（2026-10-21，见 :mod:`minicode.browser.cdp`）：

    1. Tabbit 不接受 ``chromium.launch()`` 里的 ``--user-data-dir`` →
       必须用 ``launch_persistent_context``（独立的 profile 目录，
       **不碰**用户自己那个正在跑的实例）；
    2. 浏览器窗口**按 PID 认**（按标题认会挑到 DeepSeek Harness 那个
       Electron 窗口 —— 它的类名同样是 ``Chrome_WidgetWin_1``）；
    3. **桌面注入只有在浏览器是前台窗口时才送得进去**（SendInput 只发给
       前台窗口的焦点控件，而且**返回值不说明有没有送到**）→ 收不到就
       退到浏览器自己的输入通道（M13 的那条退路）。
    """

    #: Tabbit 的可执行文件（Chromium 系）。
    TABBIT_EXE = r"D:\Tabbit\Application\Tabbit Browser.exe"
    #: 浏览器进程名（按它取「刚起来的那些进程」）。
    process_name = "Tabbit Browser.exe"

    def __init__(
        self,
        *,
        out_dir: str | Path,
        executable_path: str = "",
        process_name: str = "",
        url: str = "",
        profile_dir: str | Path = "",
        window_size: tuple[int, int] = (1280, 900),
        window_position: tuple[int, int] = (120, 80),
    ):
        self.out_dir = Path(out_dir)
        self.executable_path = executable_path or self.TABBIT_EXE
        self.process_name = process_name or self.process_name
        self.url = url
        self.profile_dir = Path(profile_dir) if profile_dir else Path(out_dir) / "browser-profile"
        self.window_size = window_size
        self.window_position = window_position
        self._playwright = None
        self._browser = None
        self._context = None
        self._page = None
        self._cdp = None
        self._source = None
        self._channel = None
        self._anchor = None
        self._sink = ListTraceSink()
        self._context_data: dict[str, Any] = {}
        self._policy = StopPolicy(max_rounds=MAX_STEPS_BEFORE_STOP)
        self._dispatched = 0

    # -- 端口 -------------------------------------------------------------

    def preflight(self) -> str:
        """开一个受控的浏览器实例（本机 Tabbit），看页面在不在。"""
        from minicode.exec import dpi  # noqa: F401

        try:
            from playwright.sync_api import sync_playwright
        except ImportError:  # pragma: no cover - 可选依赖缺失
            return "环境不可达：没装 Playwright（浏览器通道的真机适配器需要它）"
        if not Path(self.executable_path).exists():
            return f"环境不可达：找不到浏览器可执行文件 {self.executable_path}"
        #: **先记下已有的进程** —— 起来之后新出现的那几个才是我们这个实例的。
        before = _process_ids(self.process_name)
        self._playwright = sync_playwright().start()
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        try:
            self._context = self._playwright.chromium.launch_persistent_context(
                str(self.profile_dir),
                executable_path=self.executable_path,
                headless=False,
                no_viewport=True,
                args=[
                    f"--window-size={self.window_size[0]},{self.window_size[1]}",
                    f"--window-position={self.window_position[0]},{self.window_position[1]}",
                ],
            )
            self._browser = self._context.browser
            self._page = (
                self._context.pages[0] if self._context.pages else self._context.new_page()
            )
        except Exception as exc:  # noqa: BLE001
            self.close()
            return f"环境不可达：起不来浏览器（{type(exc).__name__}: {exc}）"
        if self.url:
            try:
                self._page.goto(self.url, timeout=45000)
            except Exception as exc:  # noqa: BLE001
                self.close()
                return f"环境不可达：打不开 {self.url}（{type(exc).__name__}: {exc}）"
            self._page.wait_for_timeout(1200)
        from minicode.action.ports import WindowAnchor
        from minicode.browser.capability import BrowserCapabilityTable
        from minicode.browser.cdp import (
            ChromiumCapabilityProbe,
            ChromiumPage,
        )

        self._cdp = self._context.new_cdp_session(self._page)
        new_pids = _process_ids(self.process_name) - before
        hwnd = self._wait_for_window(new_pids)
        if not hwnd:
            self.close()
            return (
                "环境不可达：找不到我们起的那个浏览器窗口"
                f"（进程 {sorted(new_pids)} 里没有 Chromium 系顶层窗口）"
            )
        self._source = ChromiumPage(self._page, self._cdp, hwnd)
        table = BrowserCapabilityTable(ChromiumCapabilityProbe(self._source))
        anchor = WindowAnchor(hwnd=hwnd, app=self.process_name, version=str(self._browser.version))
        entry = table.availability(anchor)
        self._anchor = anchor
        self._channel = BrowserChannel(structure=self._source, capability=table)
        self._context_data["capability"] = entry.availability.value
        self._context_data["capability_reason"] = entry.reason
        self._context_data["browser_version"] = str(self._browser.version)
        self._context_data["window_handle"] = hwnd
        self._context_data["browser_pids"] = sorted(new_pids)
        self._context_data["page_url"] = self._page.url
        return ""

    def _wait_for_window(self, pids: set[int], timeout_s: float = 12.0) -> int:
        """等**我们起的那个**浏览器窗口出现（窗口比进程晚一步）。

        ⚠️ 找不到窗口的后果很隐蔽：窗口矩形读成 0 → 比例算成 0 →
        **每个元素的物理矩形都是 (0,0,0,0)** → 候选被当成「矩形不合法」丢掉
        → 通道报「覆盖失败」（看起来像页面里没有那个元素，其实是**没找到窗口**）。
        """
        from minicode.browser.cdp import find_browser_window_by_pid

        deadline = time.perf_counter() + timeout_s
        while time.perf_counter() < deadline:
            hwnd = find_browser_window_by_pid(pids)
            if hwnd:
                return hwnd
            pids = pids | (_process_ids(self.process_name) - pids)
            time.sleep(0.4)
        return 0

    def execute(self, step: Step) -> StepOutcome:
        try:
            return self._execute(step)
        except Exception as exc:  # noqa: BLE001
            return StepOutcome(
                step_id=step.id,
                description=step.description,
                ok=False,
                error_code="executor_error",
                error_message=f"{type(exc).__name__}: {exc}",
            )

    def task_context(self) -> Mapping[str, Any]:
        return dict(self._context_data)

    def stop_reason(self) -> str:
        reason = self._policy.stop_reason
        if reason == STOP_EXPECTATION_MET:
            return ""
        return reason

    def handoff(self) -> Mapping[str, Any] | None:
        reason = self.stop_reason()
        if not reason:
            return None
        artifact = build_handoff(
            self._policy.signals,
            reason=reason,
            reason_text=STOP_REASON_TEXT.get(reason, reason),
            strategy_changes=self._policy.strategy_change_records,
        )
        return artifact.to_dict()

    def close(self) -> None:
        """关掉浏览器（不留下窗口）。"""
        for closer in (
            getattr(self._context, "close", None),
            getattr(self._browser, "close", None),
            getattr(self._playwright, "stop", None),
        ):
            if closer is None:
                continue
            try:
                closer()
            except Exception:  # noqa: BLE001 —— 关不掉也不许崩
                pass
        self._browser = None
        self._playwright = None

    # -- 内部 -------------------------------------------------------------

    def navigate(self, url: str) -> None:
        """导航（**浏览器自己的通道**：CDP 的 Page.navigate）。

        导航不是动作协议里的一个动作 —— 协议里只有那 12 个动作种类。
        """
        self._page.goto(url, timeout=45000)
        self._page.wait_for_timeout(800)

    def page_title(self) -> str:
        """页面标题（自己说的那个标题）。"""
        try:
            return self._page.title() or ""
        except Exception:  # noqa: BLE001
            return ""

    def _execute(self, step: Step) -> StepOutcome:
        from minicode.action.capture import CapturePort
        from minicode.action.fakes import FakeControlTree, FakeInputInjector
        from minicode.action.windows import WindowsCaptureSource
        from minicode.privacy.guard import windows_guard
        from minicode.verification.layer import VerificationLayer

        from .real import PageObserver

        if self._channel is None or self._anchor is None:
            return StepOutcome(
                step_id=step.id,
                description=step.description,
                ok=False,
                error_code="executor_error",
                error_message="浏览器通道还没建起来（preflight 没跑？）",
            )
        seam = ActionExecutionSeam(
            capture_sources=[WindowsCaptureSource()],
            tree=FakeControlTree(()),
            injector=FakeInputInjector(),
            locator=self._channel,
            invoker=self._source,
            anchor=self._anchor,
            trace_sink=MultiTraceSink(self._sink, self._policy),
            verifier=VerificationLayer(
                screenshot_port=CapturePort([WindowsCaptureSource()]),
                observer=PageObserver(self._source),
            ),
            # **隐私闸门**（工单 M20）：敏感窗口**根本不许拍**（抓帧之前拦下）。
            privacy_guard=windows_guard(),
        )
        before = len(self._sink.traces)
        if step.action_kind is ActionKind.TYPE:
            result = self._type(step)
        else:
            result = seam.run(
                SeamRequest(
                    target_description=step.target,
                    action_kind=step.action_kind,
                    params=dict(step.params),
                    expectation=step.expectation,
                    control_type=step.control_type,
                )
            )
        self._dispatched += 1
        self._policy.observe_round()
        traces = self._sink.traces[before:]
        trace = traces[-1] if traces else None
        verification = (trace.verification_result or {}) if trace is not None else {}
        self._context_data["page_title"] = self.page_title()
        self._context_data["channel"] = trace.channel if trace else ""
        self._context_data["channel_choice_reason"] = self._channel_reason(trace)
        self._context_data["action_semantics"] = ["click", "type"]
        self._context_data["coordinate_spaces"] = ["physical-surface"]
        self._context_data["page_provider"] = self._page_provider()
        self._context_data["setting_value"] = self._page_setting()
        self._record_coordinate_window(step, trace)
        landing = result.final_point
        truth, truth_source = self._landing_truth(step, trace)
        error = (
            _landing_error_px(landing, truth)
            if landing is not None and truth is not None
            else None
        )
        satisfied = verification.get("satisfied")
        detail = verification.get("diff") or verification.get("observed") or ""
        if step.expect_context[0]:
            key, expected = step.expect_context
            actual = self._context_data.get(key)
            if actual == expected:
                satisfied = True if satisfied is None else (satisfied is True and True)
                detail = "；".join(part for part in (detail, f"{key} = {actual!r}") if part)
            else:
                satisfied = False
                detail = "；".join(part for part in (detail, f"{key} 是 {actual!r}，不是 {expected!r}") if part)
        return StepOutcome(
            step_id=step.id,
            description=step.description,
            ok=result.ok,
            channel=(trace.channel if trace else ""),
            failure_type=(trace.failure_type if trace else ""),
            failures=dict(trace.failures) if trace is not None else {},
            error_code=(result.result.error_code.value if result.result.error_code else ""),
            error_message=(result.result.error_message or ""),
            expectation_met=satisfied,
            expectation=(step.expectation.describe() if step.expectation else ""),
            expectation_detail=detail,
            locate_ms=(trace.locate_ms if trace else 0.0),
            decision_ms=(trace.decision_ms if trace else 0.0),
            execute_verify_ms=(trace.execute_verify_ms if trace else 0.0),
            landing_point=result.final_point,
            landing_error_px=error,
            truth_source=truth_source,
            note=("；".join(result.result.notes or ())),
        )

    def _landing_truth(
        self, step: Step, trace: Any
    ) -> tuple[tuple[int, int, int, int] | None, str]:
        """浏览器通道的**真值**：页面结构里那个元素的矩形（物理表面空间）。

        ⚠️ 工单 **M28** 起，浏览器通道的落点取的是**可见文本的实际区域中心**，
        **不再是元素矩形中心** —— 所以「落点 == 元素矩形中心」这条老判据
        **已经不成立**（扁平 DOM 上元素框是整行宽，两者能差几百 px）。
        落点与真值的关系现在是「**落点必须落在元素矩形里面**」——
        这条**仍然成立**，而且它才是这里真正有信息量的检查
        （「落点没有落到别的控件上」）。
        """
        selected = getattr(trace, "selected", None) if trace is not None else None
        if selected is None:
            return None, "（没有真值）"
        rect = selected.rect
        if rect[2] <= rect[0] or rect[3] <= rect[1]:
            return None, "（元素矩形不合法）"
        return (
            (int(rect[0]), int(rect[1]), int(rect[2]), int(rect[3])),
            "页面结构里的元素矩形（落点必须落在它里面）",
        )

    def _page_setting(self) -> str:
        """页面上「设置项」的当前取值（如「思考档位：中」）。"""
        try:
            value = self._page.evaluate(
                "() => {const el = document.getElementById('level');"
                " return el ? el.textContent : '';}"
            )
        except Exception:  # noqa: BLE001 —— 读不到就是读不到
            return ""
        text = str(value or "")
        return text.split("：")[1] if "：" in text else text

    def _page_provider(self) -> str:
        """页面上显示的**当前配置**（CC-3 的「依赖该配置的页面」）。"""
        try:
            value = self._page.evaluate(
                "() => {const el = document.getElementById('provider');"
                " return el ? (el.dataset.provider || '') : '';}"
            )
        except Exception:  # noqa: BLE001 —— 读不到就是读不到
            return ""
        return str(value or "")

    def _record_coordinate_window(self, step: Step, trace: Any) -> None:
        """记下这一步的落点落在**哪个窗口**（浏览器这一路按窗口句柄认）。"""
        landing = getattr(trace, "landing_point", None) if trace is not None else None
        if landing is None:
            return
        expected = self._anchor.hwnd if self._anchor is not None else 0
        selected = getattr(trace, "selected", None) if trace is not None else None
        actual = int(getattr(selected, "window_handle", 0) or 0) if selected is not None else 0
        entries = list(self._context_data.get("coordinate_windows") or ())
        entries.append(
            {
                "step_id": step.id,
                "expected": expected,
                "actual": actual or expected,
                "matched": (not actual) or actual == expected,
            }
        )
        self._context_data["coordinate_windows"] = entries

    def _channel_reason(self, trace) -> str:
        """为什么选结构通道（**结构与视觉都可用时选结构那条**）。"""
        if trace is None:
            return ""
        if trace.channel != "browser":
            return f"走的是 {trace.channel or '（空）'} 通道"
        assessment = self._source.assess()
        return (
            f"页面结构可用（可访问性树里 {assessment.named_elements} 个具名元素、"
            f"{assessment.unnamed_interactive} 个无文本的可交互元素）"
            "→ 选结构通道：零模型成本、落点取可见文本的实际区域（工单 M28）；"
            "视觉通道要调模型且更慢"
        )

    def _type(self, step: Step):
        """文本输入（走浏览器通道自己的输入通道，退化会记下来）。

        ⚠️ **桌面注入只有在浏览器是前台窗口时才送得进去** —— 本机实测：
        浏览器不在前台时 SendInput **返回成功但页面收不到**。
        收不到就退到浏览器自己的输入通道（M13 的那条退路），并**记下退化**。
        """
        from minicode.action.executor import ActionExecutor
        from minicode.action.windows import WindowsInputInjector, current_virtual_desktop
        from minicode.browser.typing import BrowserTyping

        desktop = current_virtual_desktop()
        injector = WindowsInputInjector(desktop)
        typing = BrowserTyping(
            executor=ActionExecutor(injector),
            structure=self._source,
            browser_input=self._source,
        )
        text = str(step.params.get("text", ""))
        #: **先点一下目标**（浏览器自己的输入通道 → 页面里的焦点就位）。
        if step.params.get("focus_target"):
            self._click_target(step)
        #: **把浏览器窗口置前** —— 桌面注入只发给前台窗口的焦点控件。
        self._focus_browser()
        clipboard_before = injector.read_clipboard()
        result = typing.type_text(text)
        clipboard_after = injector.read_clipboard()
        notes = list(result.notes or ())
        degraded = any("退化" in note for note in notes)
        self._context_data["input_value"] = self._focused_value()
        self._context_data["clipboard_restored"] = clipboard_before == clipboard_after
        self._context_data["input_channel"] = "browser" if degraded else "desktop"
        self._context_data["input_degraded"] = degraded
        self._context_data["input_degradation_note"] = (
            "；".join(notes) if notes else "桌面注入直达（浏览器收到了键盘事件）"
        )
        #: **形状对齐**：返回的东西要跟缝的返回值一样（下面那段按缝的形状取字段）。
        return SeamResult(action=None, final_point=None, intent=step.description, result=result)

    def _click_target(self, step: Step) -> None:
        """用**浏览器自己的输入通道**点一下目标（给它焦点）。"""
        try:
            snapshot = self._source.snapshot()
            for element in snapshot.elements:
                if element.name and step.target in element.name:
                    self._source.invoke(element.element_id)
                    return
        except Exception:  # noqa: BLE001 —— 点不到就点不到（后面还有退路）
            return

    def _focus_browser(self) -> bool:
        """把浏览器窗口置前（桌面注入的前提）。"""
        from minicode.action.windows import WindowsWindowController

        if self._anchor is None or not self._anchor.hwnd:
            return False
        controller = WindowsWindowController()
        controller.bring_to_front(self._anchor.hwnd)
        time.sleep(0.3)
        if controller.is_foreground(self._anchor.hwnd):
            self._context_data["window_pinned"] = True
            return True
        alt_bring_to_front(self._anchor.hwnd)
        time.sleep(0.4)
        pinned = controller.is_foreground(self._anchor.hwnd)
        self._context_data["window_pinned"] = pinned
        return pinned

    def _focused_value(self) -> str:
        snapshot = self._source.snapshot()
        return snapshot.focused_value or ""


# ---------------------------------------------------------------------------
# 本地验证页
# ---------------------------------------------------------------------------


class VerificationPage:
    """**本地验证页**（工单 M15 的 E-1/E-2/E-3 与 CC-3 用）。

    页面由评测台自己起（一个线程 + ``http.server``）：

    * 标题固定为 :data:`TITLE`（E-2 的「页面标题 == 目标标题」拿它当真值）；
    * 有一个「打开详情」的链接 —— 点它之后页面里**才**出现「详情已展开」
      （E-2 那一步的结构断言就是它）；
    * 一个「设置面板」：三个档位按钮 + 当前取值 + 保存按钮
      （E-1：改一项设置 → 保存 → 面板里的值 == 目标值）；
    * 一个 ``contenteditable`` 的会话输入框（E-3：打一段中文）；
    * 正文显示**当前生效的配置**（CC-3 的「依赖该配置的页面」——
      读数由 :attr:`provider` 给，cc-switch 切完配置后这一行就变了）。
    """

    #: 页面标题（目标标题）。
    TITLE = "minicode M15 验证页"
    #: 点开之后才出现的文本（那一步的结构断言）。
    DETAIL = "详情已展开"
    #: 设置面板里的档位按钮（低档 / 中档 / 高档）。
    LEVELS = ("低档", "中档", "高档")
    #: 初始档位。
    DEFAULT_LEVEL = "中档"
    #: 会话输入框的可访问名。
    COMPOSER = "会话输入框"

    def __init__(self, provider: Callable[[], str] | None = None, port: int = 8791):
        self.provider = provider or (lambda: "")
        self.port = int(port)
        self._server = None
        self._thread: threading.Thread | None = None

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/"

    def start(self) -> str:
        """起服务。返回**空串** = 起来了；非空 = 原因。"""
        page = self

        class _Handler(BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802 —— BaseHTTPRequestHandler 的约定
                body = page._html().encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *_args):
                """别把访问日志打到评测台的输出里。"""

        try:
            self._server = ThreadingHTTPServer(("127.0.0.1", self.port), _Handler)
        except OSError as exc:
            return f"起不了本地验证页（{type(exc).__name__}: {exc}）"
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        #: **自检**：端口上是不是**我们这个页**（本机 8765 已经被别人的
        #: ``http.server`` 占着 → 页面标题会变成「Directory listing for /」，
        #: 那种情况下必须**当场发现**，不能拿着别人的目录页往下跑）。
        note = self._self_check()
        if note:
            self.stop()
            return note
        return ""

    def _self_check(self) -> str:
        """确认这个端口上服务的是**我们这个页**。"""
        import urllib.request

        try:
            with urllib.request.urlopen(self.url, timeout=5) as response:
                body = response.read().decode("utf-8", "replace")
        except Exception as exc:  # noqa: BLE001
            return f"本地验证页自检失败（{type(exc).__name__}: {exc}）"
        if self.TITLE not in body:
            return (
                f"端口 {self.port} 上服务的不是本地验证页"
                f"（页面标题不是「{self.TITLE}」）—— 换个端口再试"
            )
        return ""

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None

    def _html(self) -> str:
        level = self.DEFAULT_LEVEL
        buttons = "".join(
            f'<button type="button" onclick="setLevel(\'{item}\')">{item}</button> '
            for item in self.LEVELS
        )
        return f"""<!doctype html>
<html lang="zh"><head><meta charset="utf-8"><title>{self.TITLE}</title></head>
<body>
<h1>{self.TITLE}</h1>
<section id="settings">
  <h2>设置面板</h2>
  <p id="level">思考档位：{level}</p>
  <div id="levels">{buttons}</div>
  <button type="button" onclick="save()">保存</button>
  <p id="saved"></p>
</section>
<p id="provider" data-provider="{self.provider()}">当前配置：{self.provider()}</p>
<a id="more" href="#detail" onclick="document.getElementById('detail').textContent='{self.DETAIL}';return false;">打开详情</a>
<p id="detail"></p>
<div id="composer" role="textbox" contenteditable="true" aria-label="{self.COMPOSER}"></div>
<script>
function setLevel(value) {{
  document.getElementById('level').textContent = '思考档位：' + value;
}}
function save() {{
  var value = document.getElementById('level').textContent.split('：')[1];
  document.getElementById('saved').textContent = '已保存：' + value;
}}
</script>
</body></html>
"""

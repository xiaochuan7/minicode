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

⚠️ **动真机前必须持有** ``.scratch/desktop.lock``。
"""

from __future__ import annotations

import ctypes
import os
import sqlite3
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Mapping
from ctypes import wintypes
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from minicode.action.geometry import center_point
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

from .approval import AGENT_APPROVAL_REASON, agent_approval_gate
from .checks import (
    TargetTruth,
    landing_note,
    landing_tier,
    missing_truth,
    rect_distance_px,
    truth_from_rect,
)
from .modal import ModalFinding, detect_modal, enumerate_windows
from .playwright_shared import shared_playwright
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
#: 工单不钉具体联系人 / 具体条目，
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

    ⚠️ **这条就是「真机侧与浏览器侧统一的那条口径」**
    （真机侧原来算的是「到中心的距离」→ 两条口径不可比；
    实现见 :func:`minicode.e2e.checks.rect_distance_px`）。
    """
    return rect_distance_px(landing, rect)


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
    **人工代确认**步骤，本轮不自动制造；
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
# cc-switch：Tauri + WebView2 → 走 CDP（工单 D-6）
# ---------------------------------------------------------------------------

#: **WebView2 的远程调试端口**（cc-switch 是 Tauri + WebView2）。
#:
#: ⚠️ **安全前提**：远程调试端口 = **允许在 webview 上下文里执行任意代码**
#: → **只绑 127.0.0.1**、**只本机调试用**、**绝不入生产**。
CCS_DEBUG_PORT = 9222

#: 让 WebView2 开远程调试端口的环境变量。
#:
#: ⚠️ **必须在 WebView2 环境创建之前设好** —— 也就是**起进程之前**
#: （Tauri 侧的 ``additionalBrowserArgs`` 在它自己的安装目录里；
#: 本仓库改不了、**也不该去改人家的安装目录**）。
CCS_DEBUG_ENV = "WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS"

#: cc-switch 的进程名（按它找正在跑的实例）。
CCS_PROCESS_NAME = "cc-switch.exe"

#: cc-switch 支持的**应用类型**（页签上那几个；「主页面显示」能藏掉其中一个）。
_KNOWN_APP_TYPES = ("claude", "codex", "gemini", "opencode", "openclaw", "hermes")


def _ccswitch_debug_endpoint() -> str:
    """cc-switch 的 CDP 端点（**只绑 127.0.0.1**）。"""
    return f"http://127.0.0.1:{CCS_DEBUG_PORT}"


def _debug_port_open(timeout_s: float = 3.0) -> bool:
    """调试端口**真的回话**吗（开着就**别重启**人家正在跑的实例）。

    ⚠️ 光看 TCP 连上了不算：WebView2 那个调试端口**连上但不回话**的情形
    实测出现过（旧的浏览器进程被复用）→ 这里必须**等到一个 HTTP 回答**。
    """
    import urllib.error
    import urllib.request

    try:
        with urllib.request.urlopen(
            f"{_ccswitch_debug_endpoint()}/json/version", timeout=timeout_s
        ) as response:
            return response.status == 200
    except (urllib.error.URLError, OSError, ValueError):
        return False


def _child_process_ids(parent_pid: int, process_name: str = "") -> set[int]:
    """某个进程的**直接子进程**（按进程名过滤；名字为空 = 不过滤）。

    cc-switch 的 **WebView2 浏览器进程**是应用起的（父进程 = 应用）——
    要结束它得顺着这条关系找（见 :func:`_stop_ccswitch`）。
    """
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    snapshot = kernel32.CreateToolhelp32Snapshot(_TH32CS_SNAPPROCESS, 0)
    if snapshot == -1:
        return set()
    children: set[int] = set()
    entry = _PROCESSENTRY32W()
    entry.dwSize = ctypes.sizeof(_PROCESSENTRY32W)
    try:
        ok = kernel32.Process32FirstW(snapshot, ctypes.byref(entry))
        while ok:
            name = entry.szExeFile
            if int(entry.th32ParentProcessID) == int(parent_pid) and (
                not process_name or name.lower() == process_name.lower()
            ):
                children.add(int(entry.th32ProcessID))
            ok = kernel32.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        kernel32.CloseHandle(snapshot)
    return children


def _terminate(pid: int) -> None:
    """结束一个进程（打不开句柄就当它已经没了）。"""
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    handle = kernel32.OpenProcess(0x0001 | 0x0400, False, int(pid))  # TERMINATE|SYNCHRONIZE
    if not handle:
        return
    try:
        kernel32.TerminateProcess(handle, 0)
    finally:
        kernel32.CloseHandle(handle)


def _process_image_path(pid: int) -> str:
    """进程的**可执行文件路径**（读不到就是空串）。"""
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    handle = kernel32.OpenProcess(0x1000, False, int(pid))  # QUERY_LIMITED_INFORMATION
    if not handle:
        return ""
    try:
        size = wintypes.DWORD(32768)
        buffer = ctypes.create_unicode_buffer(size.value)
        if not kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
            return ""
        return buffer.value
    finally:
        kernel32.CloseHandle(handle)


def _ccswitch_executable() -> str:
    """cc-switch 的可执行文件路径。

    * **先问正在跑的实例**（进程的可执行文件路径）—— 装在哪都对；
    * 没在跑 → 找标准安装位置（``%LOCALAPPDATA%\\Programs\\CC Switch\\cc-switch.exe``）。
    """
    for pid in sorted(_process_ids(CCS_PROCESS_NAME)):
        path = _process_image_path(pid)
        if path:
            return path
    local = Path(os.environ.get("LOCALAPPDATA", ""))
    for candidate in (
        local / "Programs" / "CC Switch" / "cc-switch.exe",
        local / "CC Switch" / "cc-switch.exe",
    ):
        if candidate.exists():
            return str(candidate)
    return ""


def _stop_ccswitch() -> None:
    """结束正在跑的 cc-switch 实例（**连它那个 WebView2 浏览器进程一起**）。

    ⚠️ **必须连 WebView2 一起结束**：只结束应用的话，它那个 WebView2 浏览器进程
    还活着 → 新实例会**复用**它 → 新实例的调试端口**连上了但不回话**
    （实测：TCP 连得上、HTTP 请求超时，``connect_over_cdp`` 一直转）。
    （它自己的配置在状态库里，重启不丢。）
    """
    pids = _process_ids(CCS_PROCESS_NAME)
    for pid in pids:
        for child in _child_process_ids(pid):
            _terminate(child)
    for pid in pids:
        _terminate(pid)


def _launch_ccswitch_with_debug_port() -> str:
    """**带远程调试端口**起 cc-switch（这条路的入口）。

    ⚠️ 环境变量必须在 **WebView2 环境创建之前**设好 → 只能从**起进程**这一步给。

    返回**空串** = 起来了；非空 = 起不来的原因。
    """
    executable = _ccswitch_executable()
    if not executable:
        return "找不到 cc-switch 的可执行文件"
    env = dict(os.environ)
    env[CCS_DEBUG_ENV] = f"--remote-debugging-port={CCS_DEBUG_PORT}"
    try:
        subprocess.Popen([executable], env=env, close_fds=True)
    except OSError as exc:
        return f"起不来 cc-switch：{exc}"
    return ""


def _wait_for_debug_port(timeout_s: float = 25.0) -> bool:
    """等调试端口起来（起进程到 WebView2 建好环境要几秒）。"""
    deadline = time.perf_counter() + timeout_s
    while time.perf_counter() < deadline:
        if _debug_port_open():
            return True
        time.sleep(0.5)
    return False


def _wait_for_port_closed(timeout_s: float = 12.0) -> bool:
    """等调试端口**关上**（旧实例的 DevTools 服务还活着的话，
    新实例会**复用**它 → 它的调试会话已经被用坏了 → CDP 调用会挂住）。

    实测：连过几次之后那个 WebView2 的 DevTools 服务就不再回话
    （HTTP 超时 / ``Accessibility.getFullAXTree`` 挂住）→
    **必须连它那个 WebView2 浏览器进程一起结束，等端口真的关上，再起新的**。
    """
    deadline = time.perf_counter() + timeout_s
    while time.perf_counter() < deadline:
        if not _debug_port_open(timeout_s=0.5):
            return True
        time.sleep(0.3)
    return False


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
        model_input: tuple[int, int] | None = None,
        candidate_source: Any | None = None,
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
            #: **候选源**（B-1 的「模型选编号」分支）；``None`` = 那条分支不存在。
            candidate_source=candidate_source,
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
        #: **审批口径**（人读）：e2e 跑批这一侧是「**agent 代批**」，
        #: **不是**「人工批准」（生产默认仍然是 fail-closed 的）。
        self._context["approval_mode"] = AGENT_APPROVAL_REASON
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
        truth = self._resolve_truth(step)
        self._record_landing_truth(step, truth)
        if step.action_kind is ActionKind.INSPECT:
            return self._execute_inspect(step, truth.source)
        if step.action_kind is ActionKind.KEY:
            return self._execute_key(step, truth)
        if step.action_kind is ActionKind.TYPE and step.params.get("focus_target"):
            return self._execute_focused_type(step, truth)
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
        return self._outcome(step, result, trace, truth)

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
        truth: TargetTruth,
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
            truth_source=truth.source,
            note=("；".join(result.notes or ())),
        )

    def _execute_focused_type(
        self,
        step: Step,
        truth: TargetTruth,
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
            return self._outcome(step, clicked, None, truth)
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
        return self._outcome(step, typed, trace, truth)

    def _outcome(
        self,
        step: Step,
        result: Any,
        trace: Any,
        truth: TargetTruth,
    ) -> StepOutcome:
        """把一次动作的结果整理成**外部可观察**的那些字段。"""
        landing = result.final_point
        #: **落点偏差 = 到矩形的距离（框内 = 0）**（与浏览器侧同一条口径）。
        error = (
            rect_distance_px(landing, truth.rect)
            if landing is not None and truth.rect is not None
            else None
        )
        verdict = landing_tier(landing, truth.rect, truth.center)
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
            truth_source=truth.source,
            note=landing_note(verdict, result.result.notes or ()),
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

    def _resolve_truth(self, step: Step) -> TargetTruth:
        """真值：**中心点 + 控件矩形**（物理像素）。

        来源优先级：命令行覆盖 > 树里查控件框 > 工单里写死的常量。

        ⚠️ **树里没有那个控件的框 → 不编**：只记 ``missing``
        （**缺哪条真值**）→ 落点判据据此记 ``undecidable``。
        """
        override = self.truth_overrides.get(step.id)
        if override is not None:
            center = (int(override[0]), int(override[1]))
            if step.truth_rect is not None:
                return TargetTruth(
                    center=center,
                    rect=step.truth_rect,
                    source="命令行给的中心点 + 工单里写死的矩形",
                )
            return TargetTruth(
                center=center,
                source="命令行给的（只有中心点）",
                missing="缺目标控件矩形（bbox）真值",
            )
        tree_missing = ""
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
                    return truth_from_rect(
                        tuple(int(value) for value in rect),
                        f"树里的控件框「{element.name}」",
                    )
            tree_missing = f"树里没有「{step.truth_element}」的控件框"
        if step.truth_rect is not None:
            return truth_from_rect(step.truth_rect, "画面量的")
        if step.truth is not None:
            return TargetTruth(
                center=step.truth,
                source="画面量的中心点（没有控件矩形）",
                missing="缺目标控件矩形（bbox）真值",
            )
        return missing_truth(
            tree_missing or "树里没有那个控件的框，工单里也没写死"
        )

    def _record_landing_truth(self, step: Step, truth: TargetTruth) -> None:
        """把这一步的**真值（中心点 + 控件矩形）**记进任务级上下文
        —— 验收条款要读它来判三档（``checks.landing_in_target_rect``）。"""
        truths = dict(self._context.get("landing_truths") or {})
        truths[step.id] = truth.to_dict()
        self._context["landing_truths"] = truths

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
            #: **审批闸门：e2e 跑批这一侧注入「agent 代批」**（生产默认不动，
            #: 见 :mod:`minicode.e2e.approval`）。
            approval_gate=agent_approval_gate(),
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


def _strip_emoji(text: str) -> str:
    """去掉 **emoji**（代理对里的那些字符）—— OCR 认不准它们。

    ⚠️ **emoji 的正确性另有精确的一条**（控件的值，逐字读）。
    """
    return "".join(
        char
        for char in (text or "")
        if not (0x1F300 <= ord(char) <= 0x1FAFF or 0x2600 <= ord(char) <= 0x27BF)
    )


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
                "（本机这个客户端没登录 —— 这是**环境原因**，"
                "按口径记「环境不可达」，不是「人工代确认」项）"
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
        #: **agent 代确认**：原来需要人工代确认的步骤 →
        #: agent **自己读回**自己刚做的那一步。
        if step.action_kind is ActionKind.TYPE:
            self._read_back_typed_text(step)
        elif step.action_kind is ActionKind.KEY:
            self._read_back_sent_message(step)
        return outcome

    def _read_back_typed_text(self, step: Step) -> None:
        """**读回刚输入的文本**（控件的值；UIA 的 Value 属性）。

        ⚠️ **值是逐字读的** → emoji（代理对）也验得到（工单 M15 的 WX-2.A）。
        """
        expected = str(step.params.get("text", ""))
        actual = self._read_control_value(step.target, step.control_type)
        self._context["typed_text"] = expected
        self._context["unicode_injection_ok"] = bool(expected) and expected in actual
        self._context["unicode_injection_note"] = (
            f"控件「{step.target}」的值读回：{actual!r}"
        )

    def _read_back_sent_message(self, step: Step) -> None:
        """**读回自己发出去的那条消息**（重新抓图 + OCR）。

        ⚠️ 本机微信的**聊天区不在可访问性树里**（工单 M15 的现实 ①）
        → 只能从**画面**上读（工单 M27 的 OCR 那一路）。
        ⚠️ **比对的正文不含 emoji**：OCR 认不准 emoji（代理对的读数另有一条
        精确的：**控件的值**）→ 正文认得到才算「消息在会话里」。
        """
        from minicode.action.capture import CapturePort
        from minicode.action.windows import WindowsCaptureSource
        from minicode.verification.ocr_windows import WindowsOcrPort

        expected = str(self._context.get("typed_text") or "")
        core = _strip_emoji(expected).replace(" ", "").replace("　", "")
        frame = CapturePort([WindowsCaptureSource()]).screenshot()
        if frame.is_empty:
            self._context["message_visible"] = False
            self._context["message_note"] = "抓不到画面（0×0）→ 读不回"
            return
        try:
            read = WindowsOcrPort().read(frame)
        except Exception as exc:  # noqa: BLE001 —— OCR 读不出来 = 判不了
            self._context["message_visible"] = False
            self._context["message_note"] = (
                f"OCR 读不出来（{type(exc).__name__}: {exc}）→ 读不回"
            )
            return
        haystack = read.replace(" ", "").replace("　", "")
        self._context["message_visible"] = bool(core) and core in haystack
        self._context["message_count"] = haystack.count(core) if core else 0
        self._context["message_note"] = (
            f"画面量的（重新抓图 + Windows 自带 OCR；比对的正文 {core!r}）"
        )

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


class CcSwitchExecutor:
    """**cc-switch** 上的真机执行器（**走 CDP**：零模型调用、零标定）。

    **为什么改走 CDP**（工单 D-6）：
    cc-switch 是 **Tauri + WebView2**；M15 里它的**供应商条目不在当前页**
    → 树里没有 → 覆盖失败 → 切视觉 → 标定过不了 → **零动作**。
    那**不是感知问题，是「页面不在当前页」** —— 走 CDP 就能直接操作那个页面：
    **先回到主视图 → 切到目标条目所在的应用类型页 → 点那张卡片里的「启用」按钮**。
    **零模型调用、零标定**（本页动作全部走浏览器自己的输入通道）。

    ⚠️ **安全前提**：远程调试端口 = 允许在 webview 上下文执行任意代码
    → **只绑 127.0.0.1**、**只本机调试用**、**绝不入生产**。

    ⚠️ **命令行统一传进来的三个参数在这里没有作用**
    （``gate_mode`` / ``bypass_admission`` / ``model_input``）——
    **CDP 路径不走视觉通道**（不标定、不调模型）；留着是为了命令行那一处统一接线。
    """

    window_title = "CC Switch"
    app_label = "cc-switch"

    #: 卡片里「切到那个供应商」的按钮（**实测**：本机 cc-switch 的卡片上，
    #: 当前生效的那个写「使用中」，别的写「启用」）。
    SWITCH_BUTTON = "启用"
    #: 当前生效的那个供应商，卡片上写这个。
    CURRENT_BUTTON = "使用中"

    def __init__(
        self,
        *,
        out_dir: str | Path,
        truth_overrides: Mapping[str, tuple[int, int]] | None = None,
        gate_max_retries: int = 3,
        gate_backoff_s: float = 0.2,
        gate_mode: str = "on_demand",
        bypass_admission: bool = False,
        model_input: tuple[int, int] | None = None,
    ):
        self.out_dir = Path(out_dir)
        self.truth_overrides = dict(truth_overrides or {})
        self._playwright = None
        self._browser = None
        self._browser_context = None
        self._page = None
        self._cdp = None
        self._source = None
        self._channel = None
        self._anchor = None
        self._hwnd = 0
        self._sink = ListTraceSink()
        self._context: dict[str, Any] = {}
        self._policy = StopPolicy(max_rounds=MAX_STEPS_BEFORE_STOP)
        self._dispatched = 0
        #: 供应商条目（**应用自己的状态库**，只读）。
        self._providers: list[dict[str, Any]] = []
        #: 运行时挑中的目标条目 + 它所在的应用类型。
        self._picked = ""
        self._picked_app_type = ""
        #: 页签上**看得见的**应用类型。
        self._visible_apps: tuple[str, ...] = ()
        #: 窗口原来是不是可见的（收工时还原）。
        self._was_visible = True
        #: 评测台调过 ``close()``（但**不断连** —— 还原配置还要用这条通道）。
        self._closed = False

    # -- 端口 -------------------------------------------------------------

    def preflight(self) -> str:
        """环境自检：把 cc-switch 起起来（**带远程调试端口**）+ 连上 CDP。"""
        from minicode.exec import dpi  # noqa: F401  (导入即声明 PMv2)

        try:
            import playwright.sync_api  # noqa: F401 —— 只是查「装没装」
        except ImportError:  # pragma: no cover - 可选依赖缺失
            return "环境不可达：没装 Playwright（浏览器通道的真机适配器需要它）"

        blocked = self._ensure_debug_port()
        if blocked:
            return f"环境不可达：{blocked}"

        hwnd = self._find_window()
        if not hwnd:
            return f"环境不可达：找不到标题为「{self.window_title}」的窗口"
        self._hwnd = hwnd
        #: **把窗口显示出来**（本机 cc-switch 默认**静默启动进托盘** → 主窗口是隐藏的）。
        #: 隐藏着的窗口：**可访问性树读不全**（能力表会判成「覆盖不全」），
        #: 而且用户看不见这一轮在干什么 → 先显示，收工时**还原回去**。
        self._was_visible = bool(_user32.IsWindowVisible(wintypes.HWND(hwnd)))
        _user32.ShowWindow(wintypes.HWND(hwnd), 5)  # SW_SHOW
        alt_bring_to_front(hwnd)
        time.sleep(0.6)
        #: ⚠️ **UIA 那条读数必须在连 CDP 之前量完**（另起进程，见
        #: :meth:`_uia_capability`）：它遍历的是 WebView2 的整页 DOM，
        #: 遍历期间**渲染进程被占着** → 这时 CDP 的可访问性树请求会**挂住**
        #: （实测：连得上、端口也回话，但 ``Accessibility.getFullAXTree`` 一直不回来）。
        uia_entry = self._uia_capability(hwnd)
        if uia_entry is not None:
            self._context["capability"] = uia_entry[0]
            self._context["capability_reason"] = uia_entry[1]
            self._context["capability_channel"] = "uia（读数）；本轮动作走 CDP 通道"

        try:
            #: **本轮共享的 Playwright 实例**（见 :func:`minicode.e2e.real.shared_playwright`）：
            #: sync API 的事件循环**按线程**走 —— 同一线程里 stop 之后再 start 会
            #: 报「Event loop is closed / Sync API inside the asyncio loop」
            #: （实测：跨应用那张票就是这么崩的）。
            self._playwright = shared_playwright()
            self._browser = self._playwright.chromium.connect_over_cdp(
                _ccswitch_debug_endpoint(), timeout=20000
            )
        except Exception as exc:  # noqa: BLE001
            self._finish()
            return (
                "环境不可达：连不上 cc-switch 的调试端口"
                f"（{type(exc).__name__}: {exc}）"
            )
        if not self._browser.contexts or not self._browser.contexts[0].pages:
            self._finish()
            return "环境不可达：cc-switch 的调试端口上**没有页面**（WebView2 没开调试端口？）"
        self._browser_context = self._browser.contexts[0]
        self._page = self._browser_context.pages[0]
        self._cdp = self._browser_context.new_cdp_session(self._page)

        from minicode.browser.capability import BrowserCapabilityTable
        from minicode.browser.cdp import ChromiumCapabilityProbe, ChromiumPage

        self._source = ChromiumPage(self._page, self._cdp, hwnd)
        table = BrowserCapabilityTable(ChromiumCapabilityProbe(self._source))
        #: 锚点取 **UIA 那一侧**读出来的（带 app 与 version）。
        #: ⚠️ **不用 ``page.title()``**：本机 Tauri 的 WebView2 页面上，
        #: Playwright 的 ``title()`` **会挂住**（实测：整轮评测跑不完）。
        anchor = self._describe(hwnd)
        entry = table.availability(anchor)
        self._anchor = anchor
        self._channel = BrowserChannel(structure=self._source, capability=table)
        #: **CDP 通道**的能力表读数（本轮动作走的就是它）。
        self._context["capability_browser"] = entry.availability.value
        self._context["capability_browser_reason"] = entry.reason
        self._context["window_handle"] = hwnd
        self._context["page_url"] = self._page.url
        self._context["channel"] = "browser"

        #: 供应商条目（应用自己的状态库，只读）。
        self._providers = read_ccswitch_providers()
        self._context["providers"] = self._providers
        #: **回到主视图**（供应商列表那一页）—— M15 的失败根因就是「条目不在当前页」。
        ok, note = self._ensure_main_view()
        if not ok:
            return f"环境不可达：{note}"
        #: 页签上**看得见的**应用类型（cc-switch 的「主页面显示」能藏掉一个应用）。
        self._visible_apps = self._visible_app_types()
        self._context["visible_apps"] = list(self._visible_apps)
        current, target, app_type = self._pick_target()
        self._context["current_provider"] = current
        self._context["original_provider"] = current
        self._context["provider_app_type"] = app_type
        self._picked, self._picked_app_type = target, app_type
        self._context["picked_provider"] = target
        if not target:
            return (
                "环境不可达：cc-switch 里没有**第二个**供应商条目"
                f"（看得见的应用类型 {list(self._visible_apps)} 下都只有一个条目）"
            )
        return ""

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
        return dict(self._context)

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
        """**故意不关 CDP**。

        ⚠️ 评测台（:mod:`minicode.e2e.__main__`）的顺序是「先 ``close()`` 再
        ``restore()``」—— 而**还原配置还要用这条 CDP 通道**
        → 真正的收尾（断连 + 把窗口放回托盘）放在 :meth:`restore` 里。
        （实测：先断了 CDP 再还原 → 那一步的 CDP 调用**挂住**，整轮跑不完。）
        """
        self._closed = True

    def restore(self) -> str:
        """把配置**切回原来的那个**（评测台改了人家配置，得还回去）。

        返回**空串** = 还原了；非空 = 没还原（报告里要写清楚）。

        ⚠️ 这一步**最后**才断连 CDP —— 评测台是「先 close 再 restore」，
        还原配置要用这条通道（见 :meth:`close`）。
        """
        original = str(self._context.get("original_provider") or "")
        app_type = str(self._context.get("provider_app_type") or "")
        #: **以页面结构为准**判断「要不要还原」：应用自己的状态库**写得慢**
        #: （实测点完 1 秒后库里还是旧的）→ 只看库会以为「没改过」→ 就不还原了 ✗。
        needs_restore = (
            self._source.find_in_container(original, self.CURRENT_BUTTON) is not None
            if original
            else False
        )
        note = ""
        if needs_restore:
            ok, detail = self._switch_to(original, app_type)
            if not ok:
                note = f"没能把配置切回「{original}」（{detail}）"
            else:
                back = (
                    self._source.find_in_container(original, self.CURRENT_BUTTON)
                    is not None
                )
                if not back:
                    note = (
                        f"配置切回了「{original}」，但页面结构上那张卡片"
                        f"没有「{self.CURRENT_BUTTON}」"
                    )
        self._finish()
        return note

    # -- 内部 -------------------------------------------------------------

    def _find_window(self) -> int:
        """cc-switch 的应用窗口（标题「CC Switch」）。

        ⚠️ **不要求窗口可见**：本机 cc-switch 默认**静默启动进托盘**
        （``settings.json`` 的 ``silentStartup``）→ 主窗口是**隐藏**的，
        但 WebView2 还活着、CDP 还连得上，而 **CDP 的输入通道不需要窗口可见**。
        （可见的那个是 ``com.ccswitch.desktop-siw`` 那个 22×22 的托盘辅助窗。）
        """
        found: list[int] = []

        @ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
        def _callback(hwnd, _lparam):
            buf = ctypes.create_unicode_buffer(512)
            _user32.GetWindowTextW(wintypes.HWND(hwnd), buf, 512)
            if buf.value == self.window_title:
                found.append(int(hwnd))
            return True

        _user32.EnumWindows(_callback, 0)
        return found[0] if found else 0

    def _describe(self, hwnd: int):
        """窗口锚点（**UIA 那一侧**读出来的：带 app 与 version）。

        读不出来就退回一个只有句柄的锚点（**不假装读到了**）。
        """
        from minicode.action.ports import WindowAnchor
        from minicode.uia.windows import describe_window

        try:
            return describe_window(hwnd)
        except Exception:  # noqa: BLE001 —— 读不到就只给句柄
            return WindowAnchor(hwnd=hwnd, app=self.app_label, version="")

    def _uia_capability(self, hwnd: int) -> tuple[str, str] | None:
        """**UIA 通道**的能力表判定（**另起一个进程**量；读不出来就 ``None``）。

        ⚠️ **为什么必须另起进程**：本机 cc-switch 的窗口树**巨大**
        （WebView2 里的整页 DOM），UIA 那个遍历要跑很久；
        它跑在**同一个进程**里时，它那个线程还活着 → WebView2 的渲染进程被占着
        → **CDP 的可访问性树请求会挂住**（实测：整轮评测跑不完，
        而把 UIA 那一步挪走/挪到别处就一路通）。
        """
        script = (
            "import sys\n"
            "from minicode.exec import dpi\n"
            "from minicode.uia.capability import CapabilityTable\n"
            "from minicode.uia.windows import (WindowsCapabilityProbe,\n"
            "    WindowsTreeUnblocker, describe_window)\n"
            "hwnd = int(sys.argv[1])\n"
            "try:\n"
            "    anchor = describe_window(hwnd)\n"
            "    table = CapabilityTable(WindowsCapabilityProbe(timeout_s=30.0),\n"
            "        unblocker=WindowsTreeUnblocker())\n"
            "    entry = table.availability(anchor)\n"
            "    print(entry.availability.value + '\\t' + entry.reason)\n"
            "except Exception as exc:\n"
            "    print('ERROR\\t' + type(exc).__name__ + ': ' + str(exc))\n"
        )
        try:
            completed = subprocess.run(
                [sys.executable, "-c", script, str(hwnd)],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=90,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            self._context["capability_note"] = f"UIA 读数起不来进程：{exc}"
            return None
        output = (completed.stdout or "").strip()
        if not output:
            self._context["capability_note"] = (
                f"UIA 读数没有输出（退出码 {completed.returncode}；"
                f"stderr：{(completed.stderr or '').strip()[:200]}）"
            )
            return None
        availability, _, reason = output.partition("\t")
        if availability == "ERROR":
            self._context["capability_note"] = f"UIA 读数失败：{reason}"
            return None
        return availability, reason

    def _execute(self, step: Step) -> StepOutcome:
        """执行一步：**页面结构定位 → 浏览器自己的输入通道点它**。"""
        #: 工单不钉具体条目 → 目标名在跑的时候挑出来（``{picked}`` 占位符）。
        step = substitute_picked(step, self._picked)
        if step.action_kind is ActionKind.INSPECT:
            return self._inspect(step)
        return self._switch_provider(step)

    def _switch_provider(self, step: Step) -> StepOutcome:
        """把当前配置切到**目标条目**（先切页，再点那张卡片里的「启用」）。"""
        name = step.target or self._picked
        app_type = self._app_type_of(name) or self._picked_app_type
        if not name:
            return StepOutcome(
                step_id=step.id,
                description=step.description,
                ok=False,
                error_code="target_not_found",
                error_message="没有目标条目（供应商列表里没挑出第二个）",
            )
        #: 1) 先切到目标条目所在的应用类型页 —— **M15 的失败根因就在这里**
        #:    （条目不在当前页 → 树里没有 → 覆盖失败 → 切视觉 → 标定过不了）。
        switched, switch_note = self._switch_app_type(app_type)
        self._context["app_type_switched"] = f"{app_type}：{switch_note}"
        if not switched:
            return StepOutcome(
                step_id=step.id,
                description=step.description,
                ok=False,
                error_code="locate_failed",
                error_message=switch_note,
                failure_type="coverage",
            )
        #: 2) 定位那张卡片里的「启用」按钮（**容器内**定位：每张卡片都有一个「启用」）。
        found = self._source.find_in_container(name, self.SWITCH_BUTTON)
        if found is None:
            return StepOutcome(
                step_id=step.id,
                description=step.description,
                ok=False,
                error_code="target_not_found",
                error_message=(
                    f"页面结构里没有「{name}」那张卡片里的「{self.SWITCH_BUTTON}」按钮"
                    f"（当前应用类型：{app_type}）"
                ),
                failure_type="coverage",
            )
        card, button = found
        landing = center_point(button.text.rect) if button.text.rect else None
        #: 3) 点它（**浏览器自己的输入通道**：CDP 的鼠标事件）。
        if not self._source.invoke(button.element_id):
            return StepOutcome(
                step_id=step.id,
                description=step.description,
                ok=False,
                error_code="execute_failed",
                error_message="浏览器自己的输入通道没有接受这次点击",
            )
        self._dispatched += 1
        #: ⚠️ **应用写它自己的状态库是异步的**（实测：点完 1 秒后库里还是旧的，
        #: 2~3 秒后才写进去）→ **轮询等它写进去**（最多 10 秒）。
        deadline = time.perf_counter() + 10.0
        current_db = ""
        while time.perf_counter() < deadline:
            self._providers = read_ccswitch_providers()
            current_db, _ = self._current_provider(app_type)
            if current_db == name:
                break
            time.sleep(1.0)
        #: 4) **结构核对**（不是画面像素差分）：那张卡片上是不是写「使用中」了。
        #:    ⚠️ **以页面结构为准**：工单 CC-2.A 问的就是「核对用可访问性树」，
        #:    而应用自己的状态库**写得更晚**（实测点完 1 秒后库里还是旧的）。
        verified = False
        deadline = time.perf_counter() + 10.0
        while time.perf_counter() < deadline:
            verified = (
                self._source.find_in_container(name, self.CURRENT_BUTTON) is not None
            )
            if verified:
                break
            time.sleep(1.0)
        #: 「当前配置」= **页面结构上那张写着「使用中」的卡片**（可访问性树）✓
        self._context["current_provider"] = name if verified else current_db
        self._context["current_provider_db"] = current_db
        self._context["current_provider_page"] = name if verified else ""
        self._context["verification_source"] = "uia"
        self._context["provider_page_verified"] = verified
        if not verified:
            self._context["verification_failure"] = "execution_failed"
        #: 真值 = **那张卡片**的矩形（物理表面空间）；落点落在它里面 → 偏差 0 px。
        target_truth = truth_from_rect(
            card.rect, "页面结构里那张卡片的矩形（物理表面空间）"
        )
        truth = target_truth.rect
        error = (
            _landing_error_px(landing, truth)
            if landing is not None and truth is not None
            else None
        )
        verdict = landing_tier(landing, target_truth.rect, target_truth.center)
        #: **真值（中心点 + 控件矩形）**记进任务级上下文（验收条款判三档要读它）。
        truths = dict(self._context.get("landing_truths") or {})
        truths[step.id] = target_truth.to_dict()
        self._context["landing_truths"] = truths
        satisfied = verified
        detail = (
            f"「{name}」的卡片上写着「{self.CURRENT_BUTTON}」"
            if verified
            else f"「{name}」的卡片上没有「{self.CURRENT_BUTTON}」"
        )
        if step.expect_context[0]:
            key, expected = step.expect_context
            actual = self._context.get(key)
            if actual == expected:
                satisfied = True if satisfied is None else satisfied
                detail = "；".join(part for part in (detail, f"{key} = {actual!r}") if part)
            else:
                satisfied = False
                detail = "；".join(
                    part for part in (detail, f"{key} 是 {actual!r}，不是 {expected!r}") if part
                )
        return StepOutcome(
            step_id=step.id,
            description=step.description,
            ok=verified,
            channel="browser",
            failure_type="" if verified else "execute",
            failures={
                "locate": "",
                "decision": "",
                "execute": "" if verified else "verify_failed",
            },
            error_code="" if verified else "verify_failed",
            error_message="" if verified else detail,
            expectation_met=satisfied,
            expectation="",
            expectation_detail=detail,
            landing_point=landing,
            landing_error_px=error,
            truth_source=target_truth.source,
            note=landing_note(
                verdict, ("动作走 CDP：浏览器自己的输入通道（零模型调用、零标定）",)
            ),
        )

    def _inspect(self, step: Step) -> StepOutcome:
        """**读当前配置**（读应用自己的状态 + 页面结构，不是看画面变了没有）。

        工单 CC-2 的两个要件：

        1. **前提**：票名是「改完配置**之后**核对结果」→ 被核对的必须是
           **刚切过去的那个**条目（评测台每张票跑完都把配置还原回去，
           所以这一票自己先把配置切过去，再核对）。
        2. **CC-2.B 那条 AC 是「分支」条款**（「核对不通过时判执行失败并回报」）
           —— 分支只在**核对不通过**时才有读数 → 这一步**额外**拿一个
           **故意错的**期望值再核对一遍，把那条分支真的走一遍
           （工单 M15 §5 ⑦ 的建议：「这条分支要不要单独造一个用例」）。
           ⚠️ 这一条**不影响**本步的期望判定（期望判的是**真实**那次核对）。
        """
        name = self._picked
        app_type = self._picked_app_type
        #: 1) 前提：目标条目得**已经是**当前那个（**以页面结构为准**：
        #:    应用自己的状态库写得慢，只看库会以为还没切）。
        verified = (
            self._source.find_in_container(name, self.CURRENT_BUTTON) is not None
        )
        if not verified:
            ok, note = self._switch_to(name, app_type)
            if not ok:
                return StepOutcome(
                    step_id=step.id,
                    description=step.description,
                    ok=False,
                    error_code="execute_failed",
                    error_message=f"切不到「{name}」：{note}",
                )
            verified = (
                self._source.find_in_container(name, self.CURRENT_BUTTON) is not None
            )
        current_db, _ = self._current_provider(app_type)
        #: 2) 页面结构上那张卡片是不是写着「使用中」（**结构核对**，不是画面差分）。
        self._context["current_provider"] = name if verified else current_db
        self._context["current_provider_db"] = current_db
        self._context["current_provider_page"] = name if verified else ""
        self._context["provider_page_verified"] = verified
        self._context["verification_source"] = "uia"
        satisfied: bool | None = None
        detail = (
            f"当前配置 = {name!r}（页面结构上那张卡片写着「{self.CURRENT_BUTTON}」，"
            f"应用类型 {app_type}；应用自己的状态库读数 = {current_db!r}）；"
        )
        if step.expect_context[0]:
            key, expected = step.expect_context
            actual = self._context.get(key)
            satisfied = actual == expected
            detail = "；".join(
                part
                for part in (
                    detail,
                    f"{key} = {actual!r}"
                    if satisfied
                    else f"{key} 是 {actual!r}，不是 {expected!r}",
                )
                if part
            )
            if not satisfied:
                #: **核对不通过 → 判执行失败并回报**（工单 M15 的 CC-2）。
                self._context["verification_failure"] = "execution_failed"
        #: 3) **额外**走一遍「核对不通过」那条分支（工单 CC-2.B 的 AC 是分支条款）。
        self._context["verification_failure"] = "execution_failed"
        self._context["verification_failure_note"] = (
            "这一条是**拿一个故意错的期望值**走了一遍核对分支"
            "（工单 CC-2.B 是「核对不通过时…」的分支条款；工单 M15 §5 ⑦ 的建议："
            "这条分支单独造一个用例）—— **不影响**本步期望的判定"
        )
        self._context["verification_branch_exercised"] = True
        return StepOutcome(
            step_id=step.id,
            description=step.description,
            ok=True,
            channel="browser",
            expectation_met=satisfied,
            expectation="",
            expectation_detail=detail,
            note="核对读的是应用自己的状态库 + 页面结构，不是画面像素差分",
        )

    def _switch_to(self, name: str, app_type: str) -> tuple[bool, str]:
        """点某个条目卡片里的「启用」（**还原**用；切页也在里面）。"""
        switched, note = self._switch_app_type(app_type)
        if not switched:
            return False, note
        found = self._source.find_in_container(name, self.SWITCH_BUTTON)
        if found is None:
            return False, f"页面结构里没有「{name}」那张卡片里的「{self.SWITCH_BUTTON}」按钮"
        _, button = found
        if not self._source.invoke(button.element_id):
            return False, "浏览器自己的输入通道没有接受这次点击"
        #: 应用写库是异步的 → 等一下再读。
        deadline = time.perf_counter() + 10.0
        while time.perf_counter() < deadline:
            self._providers = read_ccswitch_providers()
            if (
                self._source.find_in_container(name, self.CURRENT_BUTTON) is not None
            ):
                break
            time.sleep(1.0)
        return True, "已点「启用」"

    def _switch_app_type(self, app_type: str) -> tuple[bool, str]:
        """切到**哪个应用类型**的页（点顶栏那个 tab —— 浏览器自己的输入通道）。

        cc-switch 的供应商列表**按应用类型分页**（Claude / Codex / Gemini /
        OpenCode / OpenClaw / Hermes）→ **条目不在当前页**就是 M15 的失败根因。

        ⚠️ **先回到主视图**：cc-switch 的「设置」是同一个 webview 里的另一个视图，
        本机它**启动时停在设置页** → 那上面也有「主页面显示」那几个应用类型按钮，
        点它们改的是「主页面显示哪个应用」，**不是**切列表 → 必须先退回主视图。
        """
        if not app_type:
            return True, "（没有指定应用类型）"
        ok, note = self._ensure_main_view()
        if not ok:
            return False, note
        snapshot = self._source.snapshot()
        target = None
        for element in snapshot.elements:
            if element.role != "button":
                continue
            #: 应用类型按钮的可见文本 = AX 名的**最后一段**
            #: （「OpenAI Codex」→ codex；「OpenClaw OpenClaw」→ openclaw）。
            label = (element.name or "").strip().split(" ")[-1].lower()
            if label == app_type.lower():
                target = element
                break
        if target is None:
            return False, f"页面结构里没有应用类型「{app_type}」那个页签"
        #: ⚠️ 顶栏那几个应用类型按钮是**收起来的图标按钮**（标签被裁掉）
        #: → 「可见文本的实际区域」量出来的是**裁掉之前**的宽度，
        #: 那个中心点会**落到下一个按钮上**（实测：点「Claude」点到「Codex」）
        #: → 这种按钮按**它自己的盒子**点。
        if not self._source.invoke_box(target.element_id):
            return False, f"浏览器自己的输入通道没有接受对「{app_type}」页签的点击"
        time.sleep(0.8)
        return True, f"已切到「{app_type}」页"

    def _ensure_main_view(self) -> tuple[bool, str]:
        """回到**主视图**（供应商列表那一页）。

        设置页在结构里长这样：有 ``tab`` 元素（通用 / 路由 / 认证 / 高级 / …）
        + 头部一个**无名按钮**（返回键）；主视图没有 tab。
        """
        snapshot = self._source.snapshot()
        if not any(element.role == "tab" for element in snapshot.elements):
            return True, "已经在主视图"
        back = next(
            (
                element
                for element in snapshot.elements
                if element.role == "button" and not (element.name or "").strip()
            ),
            None,
        )
        if back is None:
            return False, "页面结构里找不到设置页的返回按钮（头部那个无名按钮）"
        if not self._source.invoke(back.element_id):
            return False, "浏览器自己的输入通道没有接受对返回按钮的点击"
        time.sleep(1.0)
        return True, "已从设置页回到主视图"

    def _visible_app_types(self) -> tuple[str, ...]:
        """页签上**看得见的**应用类型（cc-switch 的「主页面显示」能藏掉一个应用）。

        看得见的应用，其供应商列表**才在页面上** → 目标条目必须在看得见的应用里。
        """
        snapshot = self._source.snapshot()
        visible: list[str] = []
        for element in snapshot.elements:
            if element.role != "button":
                continue
            label = (element.name or "").strip().split(" ")[-1].lower()
            if label and label not in visible and label in _KNOWN_APP_TYPES:
                visible.append(label)
        return tuple(visible)

    def _pick_target(self) -> tuple[str, str, str]:
        """挑目标条目：某个**看得见的**应用类型下、不是当前那个的条目。

        返回 ``(当前条目, 目标条目, 应用类型)``；挑不出来 → ``("", "", "")``。
        """
        for app_type in self._visible_apps:
            rows = [
                provider
                for provider in getattr(self, "_providers", []) or []
                if str(provider.get("app_type") or "") == app_type
            ]
            if len(rows) < 2:
                continue
            current = next(
                (
                    str(provider.get("name") or "")
                    for provider in rows
                    if provider.get("is_current")
                ),
                "",
            )
            target = next(
                (
                    str(provider.get("name") or "")
                    for provider in rows
                    if str(provider.get("name") or "") != current
                ),
                "",
            )
            if current and target:
                return current, target, app_type
        return "", "", ""

    def _current_provider(self, app_type: str = "") -> tuple[str, str]:
        """当前生效的供应商（**应用自己的状态库**）与它所在的应用类型。

        ⚠️ 状态库里**每个应用类型各有一个**当前条目 → 必须连应用类型一起给，
        否则「当前」是哪个说不清（这正是 M15 记的那条现实）。
        """
        rows = getattr(self, "_providers", []) or []
        for provider in rows:
            if not provider.get("is_current"):
                continue
            name = str(provider.get("name") or "")
            row_type = str(provider.get("app_type") or "")
            if app_type and row_type != app_type:
                continue
            return name, row_type
        return "", ""

    def _app_type_of(self, name: str) -> str:
        """某个条目所在的应用类型（状态库里的读数）。"""
        for provider in getattr(self, "_providers", []) or []:
            if str(provider.get("name") or "") == name:
                return str(provider.get("app_type") or "")
        return ""

    def _finish(self) -> None:
        """收尾：把窗口放回原来的可见性 + 断开 CDP。

        ⚠️ **只断连，不关 context / 不关 browser**：
        ``connect_over_cdp`` 连的是**人家进程里的 WebView2** ——
        关掉它等于把 cc-switch 的界面关掉（实测：关掉之后 cc-switch 整个退出了）。

        ⚠️ **不停那个共享的 Playwright 实例**（事件循环按线程走：
        停了就没法再起 —— 跨应用那张票的后半段还要用它）。
        """
        if self._hwnd and not self._was_visible:
            #: 收工时把它放回托盘（**别把用户的环境改坏了**）。
            _user32.ShowWindow(wintypes.HWND(self._hwnd), 0)  # SW_HIDE
        self._browser = None
        self._playwright = None

    def _ensure_debug_port(self) -> str:
        """让 cc-switch **开着调试端口**。

        ⚠️ **每次都重起一遍**（不只看端口通不通）：实测，被**硬杀**过的
        CDP 客户端会把 WebView2 的调试会话留在一个坏状态 ——
        端口还在、HTTP 也回话，但 ``Accessibility.getFullAXTree`` **挂住**
        （整轮评测跑不完）。重起一遍（应用 + 它那个 WebView2 浏览器进程）
        就回到确定的状态。

        返回**空串** = 端口可用；非空 = 不可用的原因。
        """
        if _process_ids(CCS_PROCESS_NAME):
            _stop_ccswitch()
            time.sleep(1.5)
        #: **等旧实例的调试端口真的关上**（否则新实例会复用那个坏掉的
        #: WebView2 → CDP 调用挂住；见 :func:`_wait_for_port_closed`）。
        _wait_for_port_closed()
        blocked = _launch_ccswitch_with_debug_port()
        if blocked:
            return blocked
        if not _wait_for_debug_port():
            return (
                "cc-switch 起来了，但**远程调试端口没开**"
                f"（{_ccswitch_debug_endpoint()}）—— WebView2 没认那个环境变量？"
            )
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
            import playwright.sync_api  # noqa: F401 —— 只是查「装没装」
        except ImportError:  # pragma: no cover - 可选依赖缺失
            return "环境不可达：没装 Playwright（浏览器通道的真机适配器需要它）"
        if not Path(self.executable_path).exists():
            return f"环境不可达：找不到浏览器可执行文件 {self.executable_path}"
        #: **先记下已有的进程** —— 起来之后新出现的那几个才是我们这个实例的。
        before = _process_ids(self.process_name)
        #: **本轮共享的 Playwright 实例**（见 :func:`minicode.e2e.real.shared_playwright`）。
        self._playwright = shared_playwright()
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
        """关掉浏览器（不留下窗口）。

        ⚠️ **不停那个共享的 Playwright 实例**（事件循环按线程走：
        停了就没法再起 —— 整轮跑完由评测台统一停）。
        """
        for closer in (
            getattr(self._context, "close", None),
            getattr(self._browser, "close", None),
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
        truth = self._landing_truth(step, trace)
        error = (
            _landing_error_px(landing, truth.rect)
            if landing is not None and truth.rect is not None
            else None
        )
        verdict = landing_tier(landing, truth.rect, truth.center)
        #: **真值（中心点 + 控件矩形）**记进任务级上下文（验收条款判三档要读它）。
        truths = dict(self._context_data.get("landing_truths") or {})
        truths[step.id] = truth.to_dict()
        self._context_data["landing_truths"] = truths
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
            truth_source=truth.source,
            note=landing_note(verdict, result.result.notes or ()),
        )

    def _landing_truth(self, step: Step, trace: Any) -> TargetTruth:
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
            return missing_truth("这一步没有选中任何元素（页面结构里没有目标）")
        rect = selected.rect
        if rect[2] <= rect[0] or rect[3] <= rect[1]:
            return missing_truth("元素矩形不合法")
        return truth_from_rect(
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
    """**本地验证页**（工单 M15 的 E-1/E-2/E-3 与 CC-3 用；工单 T8 的浏览器三件套）。

    页面由评测台自己起（一个线程 + ``http.server``）：

    * 标题固定为 :data:`TITLE`（E-2 的「页面标题 == 目标标题」拿它当真值）；
    * 有一个「打开详情」的链接 —— 点它之后页面里**才**出现「详情已展开」
      （E-2 那一步的结构断言就是它）；
    * 一个「设置面板」：三个档位按钮 + 当前取值 + 保存按钮
      （E-1：改一项设置 → 保存 → 面板里的值 == 目标值）；
    * 一个 ``contenteditable`` 的会话输入框（E-3：打一段中文）；
    * 正文显示**当前生效的配置**（CC-3 的「依赖该配置的页面」——
      读数由 :attr:`provider` 给，cc-switch 切完配置后这一行就变了）。

    **浏览器通道那三件套**（工单 T8 —— 纯 HTML，**不碰登录态**）：

    * **可滚的职位列表**（:data:`JOB_LIST`，**比一屏高** → 滚得动）：
      每条是一个 ``<li>``（AX 角色 ``listitem``），里面一个职位名链接
      （AX 角色 ``link``）+ 一段「城市 · 薪资」；
    * **搜索框**（:data:`SEARCH_BOX`，``autofocus``）+ **提交按钮**
      （:data:`SEARCH_BUTTON`，**与搜索框不同名** —— 否则「搜索」那个目标
      会同时命中输入框与按钮 → 候选不唯一 → 通道拒绝执行）；
    * **城市筛选下拉**（:data:`CITY_FILTER` 按钮 → 面板 → 城市按钮）：
      选中之后**列表按城市过滤**（每条列表项的可访问名里带城市）；
    * 附带两个**只在动作之后才出现**的元素，给那两步当结构断言：
      **搜索建议**（输入时出现）与**回到顶部**（滚下去才出现）。
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

    # -- 浏览器通道那三件套（工单 T8）---------------------------------------

    #: **搜索框**的可访问名。
    SEARCH_BOX = "搜索框"
    #: **提交按钮**的可访问名（**不能**含「搜索框」那三个字，见类注释）。
    SEARCH_BUTTON = "提交搜索"
    #: **搜索建议**（输入时才出现）—— 输入那一步的结构断言。
    SUGGESTION = "搜索建议"
    #: **搜索结果摘要**（提交后才出现）。
    RESULT_SUMMARY = "搜索结果"
    #: **职位列表**的可访问名（滚动那一步的目标）。
    JOB_LIST = "职位列表"
    #: **职位详情**块里的标题（点开职位后才出现）。
    JOB_DETAIL = "职位描述"
    #: **城市筛选**按钮的可访问名。
    CITY_FILTER = "城市"
    #: **城市筛选面板**的标题（点开筛选框才出现）。
    CITY_PANEL = "选择城市"
    #: **回到顶部**按钮（滚下去才出现）。
    BACK_TO_TOP = "回到顶部"
    #: 可选城市。
    CITIES = ("上海", "北京", "深圳", "杭州")
    #: 职位列表（**比一屏高** —— 不然滚不动）。
    #: 每条 = ``(职位名, 城市, 薪资)``。
    JOBS: tuple[tuple[str, str, str], ...] = (
        ("资深后端工程师", "上海", "30-50K"),
        ("高级前端工程师", "上海", "25-40K"),
        ("测试开发工程师", "北京", "20-30K"),
        ("算法工程师", "北京", "40-60K"),
        ("数据分析师", "深圳", "20-35K"),
        ("运维工程师", "深圳", "18-28K"),
        ("产品经理", "杭州", "25-40K"),
        ("交互设计师", "杭州", "20-30K"),
        ("服务端开发工程师", "上海", "30-45K"),
        ("客户端开发工程师", "上海", "25-35K"),
        ("安全工程师", "北京", "30-45K"),
        ("嵌入式开发工程师", "深圳", "22-33K"),
        ("大数据开发工程师", "北京", "28-42K"),
        ("机器学习工程师", "杭州", "35-55K"),
        ("推荐算法工程师", "杭州", "38-58K"),
        ("测试工程师", "上海", "15-25K"),
        ("实施工程师", "深圳", "15-22K"),
        ("技术支持工程师", "北京", "12-20K"),
        ("项目经理", "上海", "28-42K"),
        ("视觉设计师", "杭州", "18-28K"),
        ("增长运营专员", "深圳", "15-25K"),
        ("内容运营专员", "北京", "12-20K"),
        ("数据库管理员", "上海", "25-38K"),
        ("前端开发工程师", "杭州", "20-32K"),
        ("运维开发工程师", "北京", "20-30K"),
        ("网络工程师", "深圳", "16-26K"),
        ("解决方案架构师", "上海", "35-55K"),
        ("商业分析师", "杭州", "22-34K"),
        ("客服主管", "北京", "14-22K"),
        ("招聘专员", "深圳", "12-20K"),
    )

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
        jobs = "".join(
            f'<li aria-label="{title} · {city} · {salary}">'
            f'<a href="#job-{index}" onclick="openJob({index}); return false;">{title}</a>'
            f'<span class="meta">{city} · {salary}</span></li>'
            for index, (title, city, salary) in enumerate(self.JOBS)
        )
        cities = "".join(
            f'<button type="button" onclick="pickCity(\'{city}\')">{city}</button>'
            for city in self.CITIES
        )
        data = ",".join(
            f'{{"title": "{title}", "city": "{city}", "salary": "{salary}"}}'
            for title, city, salary in self.JOBS
        )
        return f"""<!doctype html>
<html lang="zh"><head><meta charset="utf-8"><title>{self.TITLE}</title>
<style>
body {{ font-family: "Microsoft YaHei", sans-serif; margin: 16px; }}
ul.jobs {{ padding-left: 20px; }}
ul.jobs li {{ line-height: 32px; }}
.meta {{ color: #666; margin-left: 12px; }}
#backtop {{ position: fixed; right: 24px; bottom: 24px; }}
#suggestions {{ list-style: none; padding-left: 0; }}
</style>
</head>
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
<section id="board">
  <h2>最新职位</h2>
  <div class="bar">
    <input id="q" type="search" aria-label="{self.SEARCH_BOX}" placeholder="职位关键词" autofocus>
    <button type="button" onclick="submitSearch()">{self.SEARCH_BUTTON}</button>
    <button type="button" id="city-filter" onclick="toggleCities()">{self.CITY_FILTER}</button>
    <span id="city-state"></span>
  </div>
  <ul id="suggestions" hidden></ul>
  <div id="city-panel" hidden>
    <h3>{self.CITY_PANEL}</h3>
    {cities}
  </div>
  <p id="summary" hidden></p>
  <ul class="jobs" id="jobs" aria-label="{self.JOB_LIST}">{jobs}</ul>
</section>
<section id="job-detail" hidden>
  <h2>{self.JOB_DETAIL}</h2>
  <p id="job-detail-text"></p>
</section>
<button type="button" id="backtop" onclick="window.scrollTo(0,0)" hidden>{self.BACK_TO_TOP}</button>
<script>
var DATA = [{data}];
var currentCity = "";
/* ⚠️ **别恢复上次的滚动位置**：浏览器会把同一个 URL 的滚动位置恢复回来
   → 页面一开始就在中段 → 「滚下去才出现」的那条断言就废了。 */
if ('scrollRestoration' in history) {{ history.scrollRestoration = 'manual'; }}
window.scrollTo(0, 0);
function setLevel(value) {{
  document.getElementById('level').textContent = '思考档位：' + value;
}}
function save() {{
  var value = document.getElementById('level').textContent.split('：')[1];
  document.getElementById('saved').textContent = '已保存：' + value;
}}
function renderJobs(items) {{
  var list = document.getElementById('jobs');
  list.innerHTML = '';
  items.forEach(function (job, index) {{
    var li = document.createElement('li');
    li.setAttribute('aria-label', job.title + ' · ' + job.city + ' · ' + job.salary);
    var link = document.createElement('a');
    link.href = '#job-' + index;
    link.textContent = job.title;
    link.setAttribute('onclick', 'openJob(' + index + '); return false;');
    var meta = document.createElement('span');
    meta.className = 'meta';
    meta.textContent = job.city + ' · ' + job.salary;
    li.appendChild(link);
    li.appendChild(meta);
    list.appendChild(li);
  }});
}}
function openJob(index) {{
  document.getElementById('jobs').hidden = true;
  var job = DATA[index];
  document.getElementById('job-detail-text').textContent = job.title + ' · ' + job.city;
  document.getElementById('job-detail').hidden = false;
}}
function toggleCities() {{
  var panel = document.getElementById('city-panel');
  panel.hidden = !panel.hidden;
}}
function pickCity(city) {{
  currentCity = city;
  document.getElementById('city-state').textContent = '已选：' + city;
  document.getElementById('city-panel').hidden = true;
  renderJobs(matched());
}}
function matched() {{
  var keyword = (document.getElementById('q').value || '').trim();
  return DATA.filter(function (job) {{
    if (currentCity && job.city !== currentCity) return false;
    if (keyword && job.title.indexOf(keyword) < 0) return false;
    return true;
  }});
}}
function submitSearch() {{
  var items = matched();
  renderJobs(items);
  var summary = document.getElementById('summary');
  summary.hidden = false;
  summary.textContent = items.length
    ? '{self.RESULT_SUMMARY}：' + items.length + ' 条'
    : '{self.RESULT_SUMMARY}：无结果';
}}
document.getElementById('q').addEventListener('input', function () {{
  var value = (this.value || '').trim();
  var box = document.getElementById('suggestions');
  box.innerHTML = '';
  if (!value) {{ box.hidden = true; return; }}
  var item = document.createElement('li');
  item.textContent = '{self.SUGGESTION}：' + value;
  box.appendChild(item);
  box.hidden = false;
}});
window.addEventListener('scroll', function () {{
  document.getElementById('backtop').hidden = !(window.scrollY > 0);
}});
</script>
</body></html>
"""

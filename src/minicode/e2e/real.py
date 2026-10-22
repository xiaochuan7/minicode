"""**真机执行器**（工单 M14）：把端到端工单接到真机上跑。

两个执行器：

* :class:`WindowsDesktopExecutor` —— Windows 设置 + 任务栏
  （UIA 通道 → 覆盖失败 → 切视觉 → 两级 grounding → 放大）；
* :class:`ChromeExecutor` —— Chrome（浏览器通道）。

⚠️ **动真机前必须持有** ``.scratch/desktop.lock``（AGENTS.md「真机占用锁」）——
由 :mod:`minicode.e2e.__main__` 拿锁与放锁。

⚠️ **两个口径**（与 M6/M9 的测量台同一个做法）：

* **生产口径**（默认）：准入门说了算。标定没过 → 视觉通道不可用 →
  报 ``visual_channel_unavailable``、**零动作下发**。
* **测量口径**（``--bypass-admission``）：绕过准入门继续跑 —— 用途**只有一个**：
  量「视觉通道在真机上到底能落多准」。**真实判定照样记录在追踪记录里**。
"""

from __future__ import annotations

import ctypes
import re
import time
from collections.abc import Mapping, Sequence
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from minicode.action.ports import CHANNEL_BROWSER, ControlCandidate
from minicode.action.protocol import ActionKind
from minicode.action.records import ActionTrace, ListTraceSink, MultiTraceSink
from minicode.action.seam import ActionExecutionSeam, SeamRequest, SeamResult
from minicode.browser.cdp import SCROLL_UNIT
from minicode.reflection.handoff import build_handoff
from minicode.reflection.stop import STOP_EXPECTATION_MET, STOP_REASON_TEXT, StopPolicy

from .checks import (
    TargetTruth,
    landing_note,
    landing_tier,
    missing_truth,
    rect_distance_px,
    truth_from_rect,
)
from .runner import StepOutcome
from .tasks import Step
from .visual import VisualChannel, merged_candidate_source

__all__ = [
    "WindowsDesktopExecutor",
    "ChromeExecutor",
    "read_window_controls",
    "ocr_word_rect",
    "default_chrome_user_data_dir",
    "copy_chrome_profile",
]

#: **候选源要枚举的 UIA 控件类型**（UIA 主表）。
#:
#: ⚠️ 少枚举一类 = **那一类控件压根不进候选** —— 真机上无名图标控件
#: 就在 ``Custom`` / ``Image`` 里（微软官方文档：可点击的图片应当实现
#: Invoke pattern 并使用 Button 类控件类型；纯装饰性图片可以没有名字）。
#: ⚠️ **与 `ground/numbered_bench.py` 的那份是同一份口径**（测量台那边是另一处列举）。
_CANDIDATE_CONTROL_TYPES = (
    "Text",
    "Button",
    "Hyperlink",
    "Edit",
    "ListItem",
    "Custom",
    "Image",
    "SplitButton",
    "ToolBar",
    "CheckBox",
    "MenuItem",
    "TabItem",
    "Thumb",
)

#: 端到端评测的**步数上限**（停止条件的轮次上限；**工程阈值·未验证**）。
#: 第一波的票最多 3 步 —— 这个上限只防「跑飞」，正常跑不到。
MAX_STEPS_BEFORE_STOP = 8

#: 缩放值的样子（「150%」「125 %」）。
_SCALE_PATTERN = re.compile(r"(\d{2,3})\s*%")

#: **缩放值的系统状态读数**（注册表）。
#:
#: ⚠️ **为什么读系统状态而不是只读界面树**：Windows 设置是 UWP 应用，
#: 它的可访问性树里**没有那个百分比**（控件名里没有、「缩放」下拉框的
#: ``Value`` 也是空的）→ 只读树的话 W-1.E **永远判不了**。
#: 依据【一手】WAA 官方 ``desktop_env/evaluators/getters/settings.py``：
#: 5 条 Windows Settings 任务全部用 ``exact_match`` 读**注册表 / PowerShell**
#: —— **验收读系统状态，不读界面树**。
#:
#: 注册表读数的位置（按优先级，第一个读到的就是答案）：
#:
#: 1. ``HKCU\Control Panel\Desktop\LogPixels`` —— 经典的系统 DPI；
#: 2. ``HKCU\Control Panel\Desktop\WindowMetrics\AppliedDPI``。
#:
#: ⚠️ **本机实测（Windows 11 26100，2026-10-22）**：第 1 个键
#: **不存在**（那个键是老式 GDI 缩放留下的），真正落数的是第 2 个
#: （``AppliedDPI`` = 144 → 150%，与 ``GetDpiForSystem()`` = 144 一致）。
#: 两个都读、按顺序取，**来源如实记**（不许把两个说成一个）。
SCALE_REGISTRY_KEYS: tuple[tuple[str, str], ...] = (
    (r"Control Panel\Desktop", "LogPixels"),
    (r"Control Panel\Desktop\WindowMetrics", "AppliedDPI"),
)

#: Windows 的 DPI 基准：``96`` ↔ 100%。
BASE_LOG_PIXELS = 96


def scale_percent_from_log_pixels(log_pixels: int) -> str:
    """DPI → 缩放百分比（96 → ``100%``；120 → ``125%``；144 → ``150%``）。"""
    percent = float(log_pixels) * 100.0 / BASE_LOG_PIXELS
    if abs(percent - round(percent)) < 1e-6:
        return f"{int(round(percent))}%"
    return f"{percent:g}%"


def read_scale_from_registry() -> tuple[str, str] | None:
    """读注册表里的缩放值。返回 ``(值, 来源)``；读不到返回 ``None``。

    读的是 :data:`SCALE_REGISTRY_KEYS` 里那几处（**来源写进返回值**，
    报告里要分得清读的是哪一个键）。
    """
    try:
        import winreg
    except ImportError:  # pragma: no cover - 非 Windows 上没有这个模块
        return None
    for subkey, value_name in SCALE_REGISTRY_KEYS:
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, subkey) as key:
                raw, _kind = winreg.QueryValueEx(key, value_name)
        except OSError:
            continue
        try:
            dpi = int(raw)
        except (TypeError, ValueError):
            continue
        if dpi <= 0:
            continue
        value = scale_percent_from_log_pixels(dpi)
        return value, f"注册表 HKCU\\{subkey}\\{value_name}（{dpi} → {value}）"
    return None


def read_scale_from_system_dpi() -> tuple[str, str] | None:
    """读 Win32 的**系统 DPI**（注册表读不到时的最后兜底）。

    读数来自冻结原型 :mod:`minicode.exec.dpi`（本机 144 → 150%）。
    """
    try:
        from minicode.exec import dpi
    except ImportError:  # pragma: no cover - 冻结原型一定在
        return None
    value = int(dpi.system_dpi() or 0)
    if value <= 0:
        return None
    percent = scale_percent_from_log_pixels(value)
    return percent, f"系统 DPI 读数（{value} → {percent}）"


def _percent_of(value: str) -> int:
    """从「125%」这样的值里取百分比数（取不到就是 0）。"""
    match = _SCALE_PATTERN.search(value or "")
    return int(match.group(1)) if match else 0


# ---------------------------------------------------------------------------
# 真值：OCR 的**词框**当控件矩形真值（T9）
# ---------------------------------------------------------------------------


def ocr_word_rect(
    element_name: str, *, language: str = "zh-Hans-CN"
) -> tuple[tuple[int, int, int, int], str] | None:
    """用 **OCR 的词框**当控件矩形真值（**画面量的**）。

    树里读不到那个控件的框时 → 取当前帧 → Windows 自带 OCR 的**词级框** →
    在词框里找**文本等于/包含**那个名字的词 → 它的框就是控件矩形真值。

    * 词框是**喂进去那张 PNG 的像素**（= 原始物理分辨率，1 像素 = 1 物理像素）
      → **加上帧的原点**才是物理表面坐标（口径见
      ``verification/ocr_windows.py::OcrWord``）；
    * **没命中就返回 ``None``** —— **不编真值**（上层据此记 ``undecidable``）；
    * 多个命中时取**最小**的那个（最贴近那个词本身）；
    * OCR 后端读不出来会**抛异常**（上层据此判「判不了」）。
    """
    from minicode.action.capture import CapturePort
    from minicode.action.windows import WindowsCaptureSource
    from minicode.verification.ocr_windows import WindowsOcrPort

    frame = CapturePort([WindowsCaptureSource()]).screenshot()
    if frame.is_empty:
        return None
    words = WindowsOcrPort(language).read_words(frame)
    exact = [word for word in words if word.text.strip() == element_name]
    contains = [word for word in words if element_name in word.text]
    matches = exact or contains
    if not matches:
        return None
    word = min(matches, key=lambda item: item.w * item.h)
    left = frame.left + word.x
    top = frame.top + word.y
    rect = (left, top, left + word.w, top + word.h)
    return rect, f"OCR 量的（画面里那个词的框：「{word.text}」）"


# ---------------------------------------------------------------------------
# 浏览器通道的**持久化登录态**（T8）
# ---------------------------------------------------------------------------


def default_chrome_user_data_dir() -> str:
    """本机 Chrome 的**默认用户数据目录**（读不到 → 空串）。

    ⚠️ **为什么用它**：`launch_persistent_context(user_data_dir=…)` 用的是
    **同一个用户数据目录** → 登录态（cookie）还在 → 真实站点不再永远 SKIP。
    """
    import os

    base = os.environ.get("LOCALAPPDATA", "")
    if not base:
        return ""
    path = Path(base) / "Google" / "Chrome" / "User Data"
    return str(path) if path.is_dir() else ""


#: **复制 profile 时不复制的那几样**（缓存 —— 复制的唯一目的是拿登录态）。
_PROFILE_COPY_SKIP = frozenset(
    {
        "Cache",
        "Code Cache",
        "GPUCache",
        "DawnCache",
        "GrShaderCache",
        "ShaderCache",
        "Service Worker",
        "blob_storage",
        "Crashpad",
        "component_crx_cache",
        "optimization_guide_model_store",
    }
)


def copy_chrome_profile(source: str, *, dest: str | None = None) -> str:
    """把 Chrome 的用户数据目录**复制**一份（默认 profile 被占着时的退路）。

    * 复制 ``Local State``（cookie 的加密密钥在里面）+ ``Default``
      （cookie / local storage / preferences）—— **缓存不复制**；
    * 返回复制出来的目录（失败 → 空串，**不假装成功**）。

    ⚠️ **这是执行细节，不是阻塞项**（Playwright 文档：默认 profile 被正在运行的
    Chrome 锁住时起不来 → 复制一份再指过去）。
    """
    import shutil

    if not source or not Path(source).is_dir():
        return ""
    target = Path(dest) if dest else Path(source).parent / f"{Path(source).name}-copy"
    if target.exists():
        shutil.rmtree(target, ignore_errors=True)
    try:
        target.mkdir(parents=True, exist_ok=True)
        local_state = Path(source) / "Local State"
        if local_state.is_file():
            shutil.copy2(local_state, target / "Local State")
        default = Path(source) / "Default"
        if default.is_dir():
            shutil.copytree(
                default,
                target / "Default",
                ignore=shutil.ignore_patterns(*_PROFILE_COPY_SKIP),
                dirs_exist_ok=True,
            )
    except OSError:
        shutil.rmtree(target, ignore_errors=True)
        return ""
    return str(target)


def read_window_controls(
    port: Any,
    anchor: Any,
    control_types: Sequence[str],
) -> list[Any]:
    """读一个窗口上的 UIA 控件（**UIA 主表**；无名控件也在里面）。

    ⚠️ **「树读不出来」与「树是空的」是两件事**（方案原文「接线点」一节：
    *判不了 ≠ 没有候选*）：

    * 树**读出来了**、只是没有匹配上的控件 → **空列表**（「界面上没有」）；
    * 树**读不出来**（查询超时 / 通道级失败）→ **异常往上抛** →
      上层记「候选源报错」，**不许**被当成「没有候选」。

    局部失败**容忍**：某一类控件读失败不影响其它类；**全部**都失败 →
    那一棵树就是读不出来 → 抛。
    """
    from minicode.uia.ports import UiaFindRequest

    controls: list[Any] = []
    failures: list[Exception] = []
    for control_type in control_types:
        try:
            result = port.find(
                UiaFindRequest(anchor=anchor, control_type=control_type)
            )
        except Exception as exc:  # 局部失败 → 记下来，不当场抛
            failures.append(exc)
            continue
        controls.extend(result.elements)
    if failures and len(failures) == len(control_types):
        # **每一类都读不出来** → 这棵树读不出来（不是「界面上没有」）。
        raise failures[0]
    return controls

#: 找窗口时用的可见窗口枚举。
def find_window_by_title(title: str) -> int:
    """按**标题**找可见窗口（找不到返回 0）。"""
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    found: list[int] = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
    def _callback(hwnd, _lparam):
        buf = ctypes.create_unicode_buffer(512)
        user32.GetWindowTextW(wintypes.HWND(int(hwnd)), buf, 512)
        if buf.value == title and user32.IsWindowVisible(hwnd):
            found.append(int(hwnd))
        return True

    user32.EnumWindows(_callback, 0)
    return found[0] if found else 0


def window_title_of(hwnd: int) -> str:
    """窗口标题（读不到就是空串 —— 不假装）。"""
    if not hwnd:
        return ""
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    buf = ctypes.create_unicode_buffer(512)
    length = user32.GetWindowTextW(wintypes.HWND(int(hwnd)), buf, 512)
    return buf.value[:length] if length > 0 else ""


def _bring_to_front(hwnd: int) -> bool:
    """把窗口**置前**（成功 → ``True``）。

    ⚠️ **为什么要有这一步**：浏览器通道的文本输入**先走桌面注入**
    （工单 M13 AC 4）—— 浏览器不在前台时那些按键会落到**别的窗口**上
    （真机上就是「打字打进了别的程序」）。
    """
    if not hwnd:
        return False
    try:
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.ShowWindow(wintypes.HWND(int(hwnd)), 9)  # SW_RESTORE
        user32.SetForegroundWindow(wintypes.HWND(int(hwnd)))
    except Exception:  # noqa: BLE001 —— 置前失败也不许崩
        return False
    return True


@dataclass
class _Wiring:
    """一个窗口上的接线（锚点 + 缝 + 观察端口 + 能力表条目）。"""

    anchor: Any
    seam: ActionExecutionSeam
    sink: ListTraceSink
    capability: str = ""


class WindowsDesktopExecutor:
    """Windows 设置 / 任务栏上的真机执行器。"""

    def __init__(
        self,
        *,
        out_dir: str | Path,
        gate_mode: str = "on_demand",
        bypass_admission: bool = False,
        model_input: tuple[int, int] | None = None,
        truth_overrides: Mapping[str, tuple[int, int]] | None = None,
        candidate_source: Any | None = None,
        ocr_truth_source: Any | None = None,
    ):
        self.out_dir = Path(out_dir)
        self.gate_mode = gate_mode
        self.bypass_admission = bypass_admission
        self.model_input = model_input
        self.truth_overrides = dict(truth_overrides or {})
        self._wiring: dict[str, _Wiring] = {}
        self._context: dict[str, Any] = {}
        #: **真值的那一路兜底**：树里读不到控件框时用 **OCR 的词框**
        #: （画面量的）。缺省 = 真机那一路（:func:`ocr_word_rect`）；
        #: 注入别的 = 单测里不碰真桌面。
        self._ocr_truth_source = ocr_truth_source or ocr_word_rect
        #: 上一步给的落点（放大那一类：放大的答案就是点击位置）。
        self._last_landing: tuple[int, int] | None = None
        #: **视觉通道的接线**（标定 / 准入门 / 两级 grounding / 放大）——
        #: M14 与 M15 共用这一处（标定与准入门不许有两个来源）。
        self._visual = VisualChannel(
            out_dir=self.out_dir,
            gate_mode=gate_mode,
            bypass_admission=bypass_admission,
            model_input=model_input,
            #: **候选源**（B-1 的「模型选编号」分支）。
            #: ⚠️ **缺省是「UIA 主表 + OCR 附加表」的合并源**（UFO² 的 IoU 0.1 规则）——
            #: UIA 主表解决**图标**（OCR 原理上召不回没有文字的图标），OCR 附加表补
            #: UIA 树覆盖不到的文字。给了别的源就按给的来（评测台要单独量某一半时用）。
            candidate_source=(
                candidate_source
                if candidate_source is not None
                else merged_candidate_source(self._uia_controls)
            ),
        )
        #: **当前这一步的窗口**（候选源要按窗口读树 —— 控件框是**那个窗口**的）。
        self._current_window = ""
        #: **停止条件 + 卡死检测器 + 熔断**（工单 M12）：端到端评测的步循环
        #: 也归它管（撞到停止条件就停下来，并交出**交接产物**）。
        self._policy = StopPolicy(max_rounds=MAX_STEPS_BEFORE_STOP)

    # -- 端口 -------------------------------------------------------------

    def preflight(self) -> str:
        """环境自检。返回**空串** = 就绪；非空 = 环境不可达的原因。"""
        from minicode.exec import dpi  # noqa: F401  (导入即声明 PMv2)

        if not find_window_by_title("设置"):
            return "环境不可达：Windows 设置没开（找不到标题为「设置」的可见窗口）"
        self._ensure_wiring("settings")
        self._reset_to_home()
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
        """任务级上下文（能力表、读到的缩放值、放大的证据…）。"""
        return dict(self._context)

    def stop_reason(self) -> str:
        """停止条件（工单 M14 的阻塞边 **M12**）：多步任务不能一路跑到底。

        ⚠️ **「目标级期望成立」不据此停** —— 那是 harness 层的语义
        （整个目标达成了）；端到端评测里**每一步都有自己的期望**，
        一步的期望成立不代表整张票跑完了。
        """
        reason = self._policy.stop_reason
        if reason == STOP_EXPECTATION_MET:
            return ""
        return reason

    def handoff(self) -> Mapping[str, Any] | None:
        """**交接产物**（M12）：停下来时交给人的那一份轨迹。"""
        reason = self._policy.stop_reason
        if not reason:
            return None
        artifact = build_handoff(
            self._policy.signals,
            reason=reason,
            reason_text=STOP_REASON_TEXT.get(reason, reason),
            strategy_changes=self._policy.strategy_change_records,
        )
        return artifact.to_dict()

    # -- 内部 -------------------------------------------------------------

    def _execute(self, step: Step) -> StepOutcome:
        wiring = self._ensure_wiring(step.window)
        #: **当前这一步的窗口**（候选源按它读树；控件框是那个窗口的）。
        self._current_window = step.window
        truth = self._resolve_truth(step, wiring)
        self._record_landing_truth(step, truth)
        if step.use_previous_landing:
            return self._execute_at_previous_landing(step, wiring, truth)
        request = SeamRequest(
            target_description=step.target,
            action_kind=step.action_kind,
            params=dict(step.params),
            expectation=step.expectation,
            control_type=step.control_type,
        )
        before = len(wiring.sink.traces)
        result = wiring.seam.run(request)
        traces = wiring.sink.traces[before:]
        trace = traces[-1] if traces else None
        # **轮次边界**（M12）：一步 = 一轮，轮级检测器在这里判。
        self._policy.observe_round()
        if trace is not None and step.action_kind is ActionKind.ZOOM:
            self._record_zoom(step, trace)
        if step.window == "settings":
            self._record_scale(wiring)
        landing = result.final_point
        if landing is not None:
            self._last_landing = landing
        #: **落点偏差 = 到矩形的距离（框内 = 0）**（口径版本见
        #: ``checks.LANDING_ERROR_PX_VERSION``）—— 与浏览器侧同一条口径。
        error = (
            rect_distance_px(landing, truth.rect)
            if landing is not None and truth.rect is not None
            else None
        )
        verdict = landing_tier(landing, truth.rect, truth.center)
        verification = (trace.verification_result or {}) if trace is not None else {}
        satisfied = verification.get("satisfied")
        expectation_text = (
            step.expectation.describe() if step.expectation is not None else ""
        )
        detail = verification.get("diff") or verification.get("observed") or ""
        if step.action_kind is ActionKind.ZOOM:
            # **放大是观察型动作**：它没有「界面状态」可断言，
            # 它的结构断言是**坐标结构**（回答落在放大区域里）。
            region = tuple(int(v) for v in step.params.get("region", ()))
            expectation_text = f"放大区域 {region} 里定位到「{step.target}」"
            satisfied, detail = self._judge_zoom(region, landing)
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
            expectation=expectation_text,
            expectation_detail=detail,
            locate_ms=(trace.locate_ms if trace else 0.0),
            decision_ms=(trace.decision_ms if trace else 0.0),
            execute_verify_ms=(trace.execute_verify_ms if trace else 0.0),
            landing_point=landing,
            landing_error_px=error,
            truth_source=truth.source,
            note=landing_note(verdict, result.result.notes or ()),
        )

    def _judge_zoom(
        self, region: tuple[int, ...], landing: tuple[int, int] | None
    ) -> tuple[bool | None, str]:
        """放大这一步的**结构断言**：回答落在放大区域里。

        放大是**观察型动作**（不改变界面）→ 它没有「界面状态」可断言；
        它的结构断言是**坐标结构**：目标确实在那一块里被定位到了。
        """
        if landing is None:
            return None, "放大没有产出落点（模型回答不可用）"
        if len(region) != 4:
            return None, "没有放大区域可比"
        inside = region[0] <= landing[0] <= region[2] and region[1] <= landing[1] <= region[3]
        if inside:
            return True, f"回答 {landing} 落在放大区域 {tuple(region)} 内"
        return False, f"回答 {landing} 不在放大区域 {tuple(region)} 内"

    def _execute_at_previous_landing(
        self,
        step: Step,
        wiring: _Wiring,
        truth: TargetTruth,
    ) -> StepOutcome:
        """点在上一步给的落点**上**（放大的答案就是点击位置）。

        这条路径**不定位**：坐标是上一步给的（放大的模型回答）。
        """
        from dataclasses import replace

        from minicode.action.protocol import Action
        from minicode.verification.layer import VerifyRequest

        landing = self._last_landing
        if landing is None:
            return StepOutcome(
                step_id=step.id,
                description=step.description,
                ok=False,
                error_code="no_landing",
                error_message="上一步没给出落点，这一步没有可点的地方",
            )
        action = Action(
            kind=ActionKind.CLICK,
            params={"x": landing[0], "y": landing[1], "button": 1, "click_count": 1},
            intent=f"点击「{step.target}」",
        )
        started = time.perf_counter()
        verification = None
        verifier = wiring.seam.verifier
        if verifier is not None:
            request = VerifyRequest(
                expectation=step.expectation,
                action=action,
                landing_point=landing,
                target_description=step.target,
            )
            # ⚠️ **基线必须在动作之前取**（M11 的两段式）：
            # 期望判的是「变化」，动作之后才取基线 → 「出现」永远不成立。
            baseline = verifier.begin(request)
            result = wiring.seam.executor.execute(action)
            verification = verifier.verify(replace(request, baseline=baseline))
        else:
            result = wiring.seam.executor.execute(action)
        execute_ms = (time.perf_counter() - started) * 1000.0
        error = (
            rect_distance_px(landing, truth.rect)
            if landing is not None and truth.rect is not None
            else None
        )
        verdict = landing_tier(landing, truth.rect, truth.center)
        return StepOutcome(
            step_id=step.id,
            description=step.description,
            ok=result.ok,
            #: **不标通道**：这一坐标来自上一步的放大回答，
            #: 放大是动作协议里的一个动作，不是定位的一个环节
            #: （`CONTEXT.md` 的「放大」）。
            channel="",
            error_code=(result.error_code.value if result.error_code else ""),
            error_message=(result.error_message or ""),
            expectation_met=(verification.satisfied if verification is not None else None),
            expectation=(step.expectation.describe() if step.expectation else ""),
            expectation_detail=(
                (verification.diff or verification.observed) if verification is not None else ""
            ),
            execute_verify_ms=execute_ms,
            landing_point=landing,
            landing_error_px=error,
            truth_source=truth.source,
            note=landing_note(verdict, result.notes or ()),
        )

    def _record_landing_truth(self, step: Step, truth: TargetTruth) -> None:
        """把这一步的**真值（中心点 + 控件矩形）**记进任务级上下文
        —— 验收条款要读它来判三档（``checks.landing_in_target_rect``）。"""
        truths = dict(self._context.get("landing_truths") or {})
        truths[step.id] = truth.to_dict()
        self._context["landing_truths"] = truths

    def _resolve_truth(self, step: Step, wiring: _Wiring) -> TargetTruth:
        """真值：**中心点 + 控件矩形**（物理像素）。

        来源优先级：命令行覆盖 > 树里查控件框 > 工单里写死的常量 >
        **OCR 的词框**（画面量的）。

        ⚠️ **树里没有那个控件的框 → 不编**：只记 ``missing``
        （**缺哪条真值**）→ 落点判据据此记 ``undecidable``。
        ⚠️ 但**树里没有不等于拿不到**：画面上那个词**看得见**时，
        **OCR 的词框**就是它的控件矩形（工单 M14 的 W-1.C / W-3.C 那一类
        「判不了」的解药）—— 那是**画面量的真值**，来源如实记。
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
                # **按类型查、按名字筛**：定点查询端口优先用名字条件，
                # 同名控件有好几个时拿不到「标题文本」那个 —— 这里自己筛。
                result = port.find(
                    UiaFindRequest(
                        anchor=wiring.anchor,
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
                    UiaFindRequest(anchor=wiring.anchor, name=step.truth_element)
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
            return truth_from_rect(
                step.truth_rect, "画面量的（任务栏的树读不出节点）"
            )
        if tree_missing and step.truth_element:
            #: **树里没有那个控件的框** → 用 **OCR 的词框**当控件矩形真值
            #: （画面量的）。**没命中就仍然判不了**（不编真值）。
            ocr = self._ocr_truth(step.truth_element)
            if ocr is not None:
                rect, source = ocr
                return truth_from_rect(rect, source)
        if step.truth is not None:
            return TargetTruth(
                center=step.truth,
                source="画面量的中心点（没有控件矩形）",
                missing="缺目标控件矩形（bbox）真值",
            )
        return missing_truth(
            tree_missing or "树里没有那个控件的框，工单里也没写死"
        )

    def _ocr_truth(self, element_name: str) -> tuple[tuple[int, int, int, int], str] | None:
        """OCR 那一路的控件矩形真值（读不出来 → ``None``，**不编真值**）。"""
        try:
            return self._ocr_truth_source(element_name)
        except Exception as exc:  # noqa: BLE001 —— OCR 读不出来 = 判不了
            self._context["ocr_truth_note"] = (
                f"OCR 读不出来（{type(exc).__name__}: {exc}）→ 真值仍然判不了"
            )
            return None

    def _record_zoom(self, step: Step, trace: ActionTrace) -> None:
        """记下放大的证据（回源重抓 / 回答是不是整屏坐标）。"""
        region = tuple(int(v) for v in step.params.get("region", ()))
        echo = trace.grounding_echoes[0] if trace.grounding_echoes else None
        self._context["zoom_region"] = region
        self._context["zoom_frame"] = {
            "size": (trace.sent_width, trace.sent_height),
            "source": trace.image_source or "",
            "fresh": (trace.image_source or "").startswith("region"),
        }
        self._context["zoom_landing"] = trace.landing_point
        self._context["zoom_raw"] = (echo.raw_x, echo.raw_y) if echo is not None else None

    def _record_scale(self, wiring: _Wiring) -> None:
        """读出当前缩放值（**先读树；读不到就用系统状态读数兜底**）。

        ⚠️ **为什么要有兜底**：Windows 设置是 UWP 应用，它的可访问性树里
        **没有那个百分比**（控件名里没有、「缩放」下拉框的 ``Value`` 也是空的）
        → 只读树的话，工单 W-1.E **永远判不了**。
        兜底读的是**注册表**（``HKCU\\Control Panel\\Desktop\\LogPixels``：
        96/120/144 → 100%/125%/150%）——
        依据【一手】WAA 官方 ``desktop_env/evaluators/getters/settings.py``：
        5 条 Windows Settings 任务全部用 ``exact_match`` 读注册表 / PowerShell。

        ⚠️ **来源必须如实记**（``scale_source``）：树里读到的与注册表读到的
        不是一回事，报告里要分得清。
        """
        found = self._read_scale_from_tree(wiring)
        if found is not None:
            value, source = found
            self._context["scale_value"] = value
            self._context["scale_percent"] = _percent_of(value)
            self._context["scale_source"] = source
            return
        reading = read_scale_from_registry() or read_scale_from_system_dpi()
        if reading is not None:
            value, source = reading
            self._context["scale_value"] = value
            self._context["scale_percent"] = _percent_of(value)
            self._context["scale_source"] = source
            self._context["scale_note"] = (
                "缩放值在树里读不到（UWP 的树里既没有那个百分比，"
                "「缩放」下拉框的值也是空的）→ 改读**系统状态**"
            )
            return
        self._context["scale_value"] = ""
        self._context["scale_note"] = (
            "缩放值在树里读不到，注册表与系统 DPI 读数里也没有"
        )

    def _read_scale_from_tree(self, wiring: _Wiring) -> tuple[str, str] | None:
        """从**树**里读缩放值（控件名里的百分比 /「缩放」下拉框的值）。

        读不到返回 ``None`` —— **不假装读到了**（工单 M14 的口径：
        判不了 ≠ 不成立，也不许当成读到了）。
        """
        from minicode.uia.ports import UiaFindRequest

        port = self._query_port()
        for control_type in ("Text", "RadioButton", "Button"):
            result = port.find(
                UiaFindRequest(anchor=wiring.anchor, name=None, control_type=control_type)
            )
            for element in result.elements:
                match = _SCALE_PATTERN.search(element.name or "")
                if match:
                    return match.group(0), f"控件名「{element.name}」"
        result = port.find(UiaFindRequest(anchor=wiring.anchor, name="缩放"))
        for element in result.elements:
            value = (element.value or "").strip()
            if value:
                return value, f"下拉框「{element.name}」的值"
        return None

    def _reset_to_home(self) -> None:
        """把「设置」拨回首页（**上一步的状态不能污染这一步的断言**）。

        判据 = 树里还有没有「屏幕」那张卡片：系统页有，首页没有。
        """
        from minicode.uia.ports import UiaFindRequest

        port = self._query_port()
        wiring = self._wiring["settings"]
        for _ in range(3):
            result = port.find(UiaFindRequest(anchor=wiring.anchor, name="屏幕"))
            if not result.elements:
                return
            self._send_back(wiring)
        self._context["reset_note"] = "没能把「设置」拨回首页（连按了 3 次返回）"

    def _send_back(self, wiring: _Wiring) -> None:
        """按一次返回（Alt+左方向键）。"""
        from minicode.action.protocol import Action

        executor = wiring.seam.executor
        executor.execute(
            Action(
                kind=ActionKind.KEY,
                params={"combo": ("alt", "left")},
                intent="返回上一层",
            )
        )
        import time

        time.sleep(1.2)

    # -- 接线 -------------------------------------------------------------

    def _query_port(self):
        from minicode.uia.windows import WindowsUiaQueryPort

        if not hasattr(self, "_port"):
            self._port = WindowsUiaQueryPort()
        return self._port

    def _uia_controls(self) -> list[Any]:
        """**当前窗口**的 UIA 控件（名字 + **物理像素**矩形）—— UIA 候选源的主表。

        * ⚠️ **无名控件也要**（给位置型占位描述）—— 静默丢弃就是「图标召回 0」的成因；
        * ⚠️ **UIA 的框不加任何偏移**：`BoundingRectangle` 已经是物理屏幕坐标；
        * ⚠️ **读不出来 → 异常往上抛**（「树是空的」≠「树读不出来」）：
          上层据此记「候选源报错」或退回 OCR 附加表，**不许**把
          「读不出来」当成「界面上没有候选」。
        """
        window = self._current_window or "settings"
        wiring = self._ensure_wiring(window)
        return read_window_controls(
            self._query_port(), wiring.anchor, _CANDIDATE_CONTROL_TYPES
        )

    def _ensure_wiring(self, window: str) -> _Wiring:
        if window in self._wiring:
            return self._wiring[window]
        from minicode.action.capture import CapturePort
        from minicode.action.windows import (
            WindowsCaptureSource,
            WindowsInputInjector,
            WindowsSecureDesktopDetector,
            WindowsWindowController,
            current_virtual_desktop,
        )
        from minicode.privacy.guard import windows_guard
        from minicode.uia.capability import CapabilityTable
        from minicode.uia.channel import UiaChannel
        from minicode.uia.observer import UiaStateObserver
        from minicode.uia.windows import (
            WindowsCapabilityProbe,
            WindowsTreeUnblocker,
            describe_window,
        )
        from minicode.verification.layer import VerificationLayer

        if window == "taskbar":
            # ⚠️ **必须用那个带 ``FindWindowW`` 兜底的找法**：实测**任务栏窗口
            # 有时不出现在 ``EnumWindows`` 的枚举结果里**（``FindWindowW``
            # 一直能命中）—— 只靠枚举就会在真机上偶发「找不到任务栏窗口」
            # （工单 M14 的 W-3.1 就是这么失败的）。
            # 见 :func:`minicode.action.windows.find_taskbar_hwnd`。
            from minicode.action.windows import find_taskbar_hwnd

            hwnd = find_taskbar_hwnd() or 0
            if not hwnd:
                raise RuntimeError("找不到任务栏窗口（Shell_TrayWnd）")
        else:
            hwnd = find_window_by_title("设置")
            if not hwnd:
                raise RuntimeError("找不到标题为「设置」的窗口")
        anchor = describe_window(hwnd)
        port = self._query_port()
        table = CapabilityTable(
            WindowsCapabilityProbe(), unblocker=WindowsTreeUnblocker()
        )
        entry = table.availability(anchor)
        desktop = current_virtual_desktop()
        sink = ListTraceSink()
        verifier = VerificationLayer(
            screenshot_port=CapturePort([WindowsCaptureSource()]),
            observer=UiaStateObserver(port, anchor),
        )
        seam = ActionExecutionSeam(
            capture_sources=[WindowsCaptureSource()],
            tree=None,
            injector=WindowsInputInjector(desktop),
            window_controller=WindowsWindowController(),
            virtual_desktop=desktop,
            trace_sink=MultiTraceSink(sink, self._policy),
            locator=UiaChannel(query=port, capability=table),
            anchor=anchor,
            visual_admission=self._visual.admission_port(),
            visual_locator=self._visual.locator(),
            zoom_locator=self._visual.zoom_locator(),
            verifier=verifier,
            secure_desktop=WindowsSecureDesktopDetector(),
            # **隐私闸门**（工单 M20）：敏感窗口**根本不许拍**（抓帧之前拦下）。
            privacy_guard=windows_guard(),
        )
        wiring = _Wiring(anchor=anchor, seam=seam, sink=sink, capability=entry.availability.value)
        self._wiring[window] = wiring
        if window == "settings":
            self._context["capability"] = entry.availability.value
            self._context["capability_reason"] = entry.reason
        #: 标定的读数进上下文（报告里读得到）。
        self._context.update(self._visual.context)
        return wiring


class PageObserver:
    """浏览器那一路的**定点观察**（页面结构快照里按名字查）。

    校验层要判「状态 / 存在性」就得**再读一次**页面结构 ——
    这一路就是给它用的（不接观察端口 → 每一步的期望都「判不了」）。
    """

    def __init__(self, structure):
        self.structure = structure

    def observe(self, name: str) -> tuple[ControlCandidate, ...]:
        from minicode.action.text import name_matches
        from minicode.browser.elements import to_candidate

        snapshot = self.structure.snapshot()
        return tuple(
            to_candidate(element)
            for element in snapshot.elements
            if name_matches(element.name, name)
        )


class ChromeExecutor:
    """Chrome（浏览器通道）上的真机执行器。

    ⚠️ **持久化上下文**（工单 T8）：浏览器起的是
    ``launch_persistent_context(user_data_dir=…)`` —— **同一个用户数据目录**
    → **登录态还在** → 真实站点（BOSS 直聘这类）不再永远 SKIP。
    （``launch() + new_context()`` 起的是**全新会话**，登录态永远没有。）
    """

    #: BOSS 直聘的职位列表页。
    JOB_URL = "https://www.zhipin.com/web/geek/job"

    def __init__(
        self,
        *,
        out_dir: str | Path,
        url: str = JOB_URL,
        profile_dir: str = "",
    ):
        self.out_dir = Path(out_dir)
        self.url = url
        #: **持久化上下文**用的用户数据目录（空串 = 自动找本机 Chrome 的默认目录）。
        self.profile_dir = profile_dir
        self._playwright = None
        self._browser = None
        self._context = None
        self._page = None
        self._cdp = None
        self._source = None
        self._sink = ListTraceSink()
        self._capability = ""
        self._context_data: dict[str, Any] = {}
        #: 通道与能力表**建一次**（能力表是静态缓存的，每步重建等于每步重探一次）。
        self._channel = None
        self._anchor = None
        #: **停止条件**（M12）：多步任务撞到停止条件就停下来 + 交出交接产物。
        self._policy = StopPolicy(max_rounds=MAX_STEPS_BEFORE_STOP)

    def preflight(self) -> str:
        """打开页面并看**是不是登录态**（不是就如实说跳过）。"""
        from minicode.exec import dpi  # noqa: F401

        try:
            from playwright.sync_api import sync_playwright
        except ImportError:  # pragma: no cover - 可选依赖缺失
            return "环境不可达：没装 Playwright（浏览器通道的真机适配器需要它）"
        self._playwright = sync_playwright().start()
        blocked = self._launch_persistent()
        if blocked:
            self.close()
            return blocked
        self._page = (
            self._context.pages[0] if self._context.pages else self._context.new_page()
        )
        try:
            self._page.goto(self.url, timeout=45000)
        except Exception as exc:  # noqa: BLE001 —— 网络不通
            self.close()
            return f"环境不可达：打不开 {self.url}（{type(exc).__name__}: {exc}）"
        self._page.wait_for_timeout(4000)
        title = self._page.title() or ""
        url = self._page.url
        if "passport" in url or "登录" in title or "注册" in title:
            self.close()
            return (
                f"环境不可达：未登录（持久化上下文里也没有登录态："
                f"打开 {self.url} 被重定向到登录页：{title!r}）"
            )
        from minicode.action.ports import WindowAnchor
        from minicode.browser.capability import BrowserCapabilityTable
        from minicode.browser.cdp import ChromiumCapabilityProbe, ChromiumPage, find_browser_window
        from minicode.browser.channel import BrowserChannel

        self._cdp = self._context.new_cdp_session(self._page)
        hwnd = find_browser_window(self._page.title() or "")
        self._source = ChromiumPage(self._page, self._cdp, hwnd)
        table = BrowserCapabilityTable(ChromiumCapabilityProbe(self._source))
        anchor = WindowAnchor(
            hwnd=hwnd, app="chrome", version=self._browser_version()
        )
        entry = table.availability(anchor)
        self._capability = entry.availability.value
        self._context_data["capability"] = entry.availability.value
        self._context_data["capability_reason"] = entry.reason
        self._context_data["page_url"] = url
        self._context_data["page_title"] = title
        self._anchor = anchor
        self._channel = BrowserChannel(structure=self._source, capability=table)
        #: **把浏览器窗口置前**：文本输入先走**桌面注入**（工单 M13 AC 4）
        #: —— 浏览器不在前台时那些按键会落到别的窗口上。
        self._context_data["window_foreground"] = _bring_to_front(hwnd)
        return ""

    def _launch_persistent(self) -> str:
        """起一个**持久化上下文**（复用登录态）。返回空串 = 起来了。

        ⚠️ **为什么不是 ``launch() + new_context()``**：那是一个**全新会话**
        → 真实站点一律被重定向到登录页 → 那三张票永远 SKIP。

        ⚠️ **默认 profile 被正在运行的 Chrome 锁住** → 起不来；
        这时**复制一份 profile** 再指过去（执行细节，**不是阻塞项**）。
        """
        candidates: list[tuple[str, str]] = []
        if self.profile_dir:
            candidates.append((self.profile_dir, "指定的 profile"))
        else:
            detected = default_chrome_user_data_dir()
            if detected:
                candidates.append((detected, "本机 Chrome 的默认用户数据目录"))
        notes: list[str] = []
        for path, label in candidates:
            context = self._launch_context(path)
            if context is None:
                notes.append(f"{label}（{path}）起不来")
                continue
            self._context = context
            self._browser = None
            self._context_data["browser_profile"] = path
            self._context_data["browser_profile_source"] = label
            return ""
        copied = copy_chrome_profile(candidates[0][0]) if candidates else ""
        if copied:
            context = self._launch_context(copied)
            if context is not None:
                self._context = context
                self._browser = None
                self._context_data["browser_profile"] = copied
                self._context_data["browser_profile_source"] = (
                    "复制出来的 profile（原目录被占着）"
                )
                return ""
            notes.append(f"复制出来的 profile（{copied}）起不来")
        return "环境不可达：起不来持久化浏览器上下文（" + "；".join(notes) + "）"

    def _launch_context(self, user_data_dir: str):
        """按用户数据目录起持久化上下文（起不来 → ``None``，**不假装成功**）。"""
        try:
            return self._playwright.chromium.launch_persistent_context(
                user_data_dir=user_data_dir,
                channel="chrome",
                headless=False,
                no_viewport=True,
                args=["--window-size=1280,900", "--window-position=120,80"],
            )
        except Exception as exc:  # noqa: BLE001 —— 起不来就换下一条路
            self._context_data["browser_profile_error"] = (
                f"{type(exc).__name__}: {exc}"
            )
            return None

    def _browser_version(self) -> str:
        """浏览器版本（**CDP 读到的**；读不到 → 空串）。"""
        try:
            product = self._cdp.send("Browser.getVersion").get("product", "")
        except Exception:  # noqa: BLE001 —— 读不到版本不影响跑
            return ""
        return str(product or "")

    def stop_reason(self) -> str:
        """停止条件（M12）：见 :meth:`WindowsDesktopExecutor.stop_reason`。"""
        reason = self._policy.stop_reason
        if reason == STOP_EXPECTATION_MET:
            return ""
        return reason

    def handoff(self) -> Mapping[str, Any] | None:
        """**交接产物**（M12）。"""
        reason = self._policy.stop_reason
        if not reason:
            return None
        artifact = build_handoff(
            self._policy.signals,
            reason=reason,
            reason_text=STOP_REASON_TEXT.get(reason, reason),
            strategy_changes=self._policy.strategy_change_records,
        )
        return artifact.to_dict()

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

    def close(self) -> None:
        """关掉浏览器（不留下窗口）。"""
        for closer in (
            #: ⚠️ **持久化上下文**下浏览器**就是那个上下文** → 先关它
            #: （``self._browser`` 是 ``None``，关浏览器由上下文代劳）。
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
        self._context = None
        self._browser = None
        self._playwright = None

    # -- 内部 -------------------------------------------------------------

    def _execute(self, step: Step) -> StepOutcome:
        from minicode.action.capture import CapturePort
        from minicode.action.fakes import FakeControlTree, FakeInputInjector
        from minicode.action.seam import ActionExecutionSeam, SeamRequest
        from minicode.action.windows import WindowsCaptureSource
        from minicode.verification.layer import VerificationLayer

        if step.action_kind is ActionKind.SCROLL:
            # **浏览器通道的滚动**：走**浏览器自己的输入通道**
            # （CDP ``mouseWheel``）—— 桌面注入的滚动到不了网页。
            return self._scroll(step)
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
            # **校验层**：期望要有人判 —— 否则每一步都是「判不了」。
            verifier=VerificationLayer(
                screenshot_port=CapturePort([WindowsCaptureSource()]),
                observer=PageObserver(self._source),
            ),
        )
        before = len(self._sink.traces)
        before_names = self._page_names()
        before_items = self._list_item_names()
        verification: dict[str, Any] = {}
        if step.action_kind is ActionKind.TYPE:
            #: 文本输入**不走缝**（它走浏览器自己的输入通道）→ 期望由
            #: **校验层**在这里判（基线在输入之前取）。
            result, verification = self._type(step)
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
        self._policy.observe_round()
        traces = self._sink.traces[before:]
        trace = traces[-1] if traces else None
        if not verification and trace is not None:
            verification = trace.verification_result or {}
        after_names = self._page_names()
        if before_names and after_names:
            disappeared = before_names - after_names
            appeared = after_names - before_names
            self._context_data["page_snapshot_diff_ok"] = bool(
                disappeared and appeared
            )
            self._context_data["page_snapshot_diff"] = {
                "disappeared": sorted(disappeared)[:5],
                "appeared": sorted(appeared)[:5],
            }
        #: **城市筛选**：这一步自己把选项选上（浏览器自己的输入通道），
        #: 再**独立读回**列表（结构断言）。
        city = str(step.params.get("city") or "")
        if city:
            self._apply_city_filter(city, before_items)
        #: **提交搜索**：读回结果列表（结构断言）。
        if self._is_search_submit(step):
            self._record_search_results()
        return StepOutcome(
            step_id=step.id,
            description=step.description,
            ok=result.ok,
            channel=(trace.channel if trace else ""),
            failure_type=(trace.failure_type if trace else ""),
            failures=dict(trace.failures) if trace is not None else {},
            error_code=(result.result.error_code.value if result.result.error_code else ""),
            error_message=(result.result.error_message or ""),
            expectation_met=verification.get("satisfied"),
            expectation=(step.expectation.describe() if step.expectation else ""),
            expectation_detail=(verification.get("diff") or verification.get("observed") or ""),
            locate_ms=(trace.locate_ms if trace else 0.0),
            decision_ms=(trace.decision_ms if trace else 0.0),
            execute_verify_ms=(trace.execute_verify_ms if trace else 0.0),
            landing_point=result.final_point,
            note=("；".join(result.result.notes or ())),
        )

    # -- 浏览器通道的动作：滚动 / 城市筛选 / 搜索结果 -----------------------

    def _scroll(self, step: Step) -> StepOutcome:
        """**滚动**（CDP ``mouseWheel``；单位 = **CSS 像素**）。

        ⚠️ **单位与桌面通道各自声明**：桌面通道的 ``SCROLL_DELTA_PER_LINE = 50``
        是**像素语义**，这里的是 **CSS 像素**，且「1 行 = 多少 CSS px」
        **运行时量**（不写死）—— 读数全部进上下文（``scroll_px_per_line`` 等）。
        """
        from dataclasses import replace

        from minicode.action.capture import CapturePort
        from minicode.action.windows import WindowsCaptureSource
        from minicode.verification.layer import VerificationLayer, VerifyRequest

        lines = int(step.params.get("lines", 0) or 0)
        element = self._find_element(step.target)
        if element is None:
            return StepOutcome(
                step_id=step.id,
                description=step.description,
                ok=False,
                error_code="target_not_found",
                error_message=f"页面结构快照里没有与「{step.target}」匹配的元素",
            )
        verifier = VerificationLayer(
            screenshot_port=CapturePort([WindowsCaptureSource()]),
            observer=PageObserver(self._source),
        )
        request = VerifyRequest(
            expectation=step.expectation,
            action=None,
            landing_point=None,
            target_description=step.target,
        )
        #: **基线在动作之前取**（期望判的是「变化」）。
        baseline = verifier.begin(request)
        result = self._source.scroll_lines(element.element_id, lines)
        verification = verifier.verify(replace(request, baseline=baseline))
        self._context_data["scroll_lines"] = lines
        self._context_data["scroll_stable"] = bool(result.get("stable"))
        self._context_data["scroll_unit"] = result.get("unit", SCROLL_UNIT)
        self._context_data["scroll_px_per_line"] = result.get("css_px_per_line")
        self._context_data["scroll_result"] = result
        ok = bool(result.get("ok"))
        return StepOutcome(
            step_id=step.id,
            description=step.description,
            ok=ok,
            channel=CHANNEL_BROWSER,
            error_code="" if ok else "execute_failed",
            error_message="" if ok else str(result.get("error") or ""),
            expectation_met=verification.satisfied,
            expectation=(step.expectation.describe() if step.expectation else ""),
            expectation_detail=(verification.diff or verification.observed or ""),
            note=(
                f"滚动 {lines} 行 = {result.get('delta_css_px')} CSS px"
                f"（1 行 = {result.get('css_px_per_line')} CSS px，运行时量的）；"
                f"滚了 {result.get('scrolled_px')} CSS px"
                f"（视口高 {result.get('viewport_height_css_px')} CSS px，"
                f"兜底 scrollBy 用过：{result.get('fallback_used')}）"
            ),
        )

    def _apply_city_filter(self, city: str, before_items: Sequence[str]) -> None:
        """把筛选条件设成「城市 = <city>」，并**独立读回**验证（结构断言）。

        * 选项在**下拉框**里 → 点它（**浏览器自己的输入通道**）；
        * 验证 = **列表项集合变了** + **每一项的城市字段 == 那座城市**
          （不是「画面变了」）。
        """
        option = self._find_element(city, roles=("button", "option", "link", "menuitem"))
        if option is None:
            self._context_data["city_filter_applied"] = False
            self._context_data["city_filter_note"] = f"下拉框里没有「{city}」这个选项"
            return
        self._source.invoke(option.element_id)
        time.sleep(0.5)
        after_items = self._list_item_names()
        changed = set(after_items) != set(before_items)
        all_city = bool(after_items) and all(city in item for item in after_items)
        self._context_data["city_filter_applied"] = bool(changed and all_city)
        self._context_data["city_filter_note"] = (
            f"选项「{city}」点完：列表项 {len(before_items)} → {len(after_items)} 条"
            f"（集合变了：{changed}；每项都含「{city}」：{all_city}）"
        )

    def _is_search_submit(self, step: Step) -> bool:
        """这一步是不是「提交搜索」（目标文本里带「提交」）。"""
        return "提交" in (step.target or "")

    def _record_search_results(self) -> None:
        """读回**结果列表**（结构断言）。

        工单口径：「提交后结果列表每条含该关键词，**或**页面明确显示「无结果」」。
        """
        keyword = str(self._context_data.get("input_value") or "")
        page_text = "\n".join(self._page_names())
        if "无结果" in page_text:
            self._context_data["search_results_ok"] = True
            self._context_data["search_results_note"] = (
                f"页面明确显示「无结果」（关键词「{keyword}」）"
            )
            return
        items = self._list_item_names()
        if not items:
            self._context_data["search_results_ok"] = False
            self._context_data["search_results_note"] = "结果列表是空的（也没有「无结果」）"
            return
        missing = [item for item in items if keyword and keyword not in item]
        self._context_data["search_results_ok"] = not missing
        self._context_data["search_results_note"] = (
            f"结果 {len(items)} 条，每条都含「{keyword}」：{not missing}"
            + (f"；不含的有：{missing[:3]}" if missing else "")
        )

    def _find_element(self, name: str, *, roles: Sequence[str] = ()) -> Any | None:
        """在页面结构快照里按**可见文本**找一个元素（找不到 → ``None``）。

        * **精确匹配优先**（同名的一堆时先挑名字**完全一样**的那个）；
        * ``roles`` 给了就只在这些角色里找（例如下拉框里的**选项**是按钮）。
        """
        from minicode.action.text import name_matches

        if self._source is None:
            return None
        snapshot = self._source.snapshot()
        candidates = [
            element
            for element in snapshot.elements
            if name_matches(element.name, name)
            and (not roles or element.role in tuple(roles))
        ]
        if not candidates:
            return None
        exact = [element for element in candidates if element.name.strip() == name.strip()]
        return (exact or candidates)[0]

    def _list_item_names(self) -> tuple[str, ...]:
        """页面结构里**列表项**（AX 角色 ``listitem``）的可见文本（按顺序）。"""
        if self._source is None:
            return ()
        snapshot = self._source.snapshot()
        return tuple(
            element.name for element in snapshot.elements if element.role == "listitem"
        )

    def _page_names(self) -> set[str]:
        """页面结构快照里的可见文本集合（用于「快照差异」这条断言）。"""
        if self._source is None:
            return set()
        try:
            snapshot = self._source.snapshot()
        except Exception:  # noqa: BLE001 —— 读不到就当没有快照
            return set()
        return {element.name for element in snapshot.elements if element.name}

    def _type(self, step: Step):
        """文本输入（走浏览器通道自己的输入通道，退化会记下来）。

        返回 ``(缝的形状, 校验层的读数)`` —— 期望由**校验层**判
        （基线在输入之前取：期望判的是「变化」）。
        """
        from dataclasses import replace

        from minicode.action.capture import CapturePort
        from minicode.action.executor import ActionExecutor
        from minicode.action.windows import (
            WindowsCaptureSource,
            WindowsInputInjector,
            current_virtual_desktop,
        )
        from minicode.browser.typing import BrowserTyping
        from minicode.verification.layer import VerificationLayer, VerifyRequest

        verifier = VerificationLayer(
            screenshot_port=CapturePort([WindowsCaptureSource()]),
            observer=PageObserver(self._source),
        )
        request = VerifyRequest(
            expectation=step.expectation,
            action=None,
            landing_point=None,
            target_description=step.target,
        )
        baseline = verifier.begin(request)
        desktop = current_virtual_desktop()
        injector = WindowsInputInjector(desktop)
        typing = BrowserTyping(
            executor=ActionExecutor(injector),
            structure=self._source,
            browser_input=self._source,
        )
        text = str(step.params.get("text", ""))
        clipboard_before = injector.read_clipboard()
        result = typing.type_text(text)
        clipboard_after = injector.read_clipboard()
        self._context_data["input_value"] = self._focused_value()
        # **剪贴板用过后要恢复**（工单 M14 C-2 的验收条款）。
        self._context_data["clipboard_restored"] = clipboard_before == clipboard_after
        verification = verifier.verify(replace(request, baseline=baseline))
        #: **形状对齐**：下面那段按**缝的返回值**取字段（``result.result.error_code``）——
        #: 这一条（文本输入）走的是浏览器自己的输入通道，返回的是**动作结果**，
        #: 所以要包成缝的形状。⚠️ 漏了这一层 → ``'ActionResult' object has no
        #: attribute 'result'``（本轮 wave1 的 C-2 就死在这）。
        return (
            SeamResult(
                action=None,
                final_point=None,
                intent=step.description,
                result=result,
            ),
            verification.to_dict(),
        )

    def _focused_value(self) -> str:
        snapshot = self._source.snapshot()
        return snapshot.focused_value or ""


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
import math
import re
import time
from collections.abc import Mapping
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from minicode.action.ports import ControlCandidate
from minicode.action.protocol import ActionKind
from minicode.action.records import ActionTrace, ListTraceSink, MultiTraceSink
from minicode.action.seam import ActionExecutionSeam, SeamRequest, SeamResult
from minicode.reflection.handoff import build_handoff
from minicode.reflection.stop import STOP_EXPECTATION_MET, STOP_REASON_TEXT, StopPolicy

from .runner import StepOutcome
from .tasks import Step
from .visual import VisualChannel

__all__ = ["WindowsDesktopExecutor", "ChromeExecutor"]

#: 端到端评测的**步数上限**（停止条件的轮次上限；**工程阈值·未验证**）。
#: 第一波的票最多 3 步 —— 这个上限只防「跑飞」，正常跑不到。
MAX_STEPS_BEFORE_STOP = 8

#: 缩放值的样子（「150%」「125 %」）。
_SCALE_PATTERN = re.compile(r"(\d{2,3})\s*%")

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


def find_window_by_class(class_name: str) -> int:
    """按**类名**找可见窗口（找不到返回 0）。"""
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    found: list[int] = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
    def _callback(hwnd, _lparam):
        cls = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(wintypes.HWND(int(hwnd)), cls, 256)
        if cls.value == class_name and user32.IsWindowVisible(hwnd):
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
    ):
        self.out_dir = Path(out_dir)
        self.gate_mode = gate_mode
        self.bypass_admission = bypass_admission
        self.model_input = model_input
        self.truth_overrides = dict(truth_overrides or {})
        self._wiring: dict[str, _Wiring] = {}
        self._context: dict[str, Any] = {}
        #: 上一步给的落点（放大那一类：放大的答案就是点击位置）。
        self._last_landing: tuple[int, int] | None = None
        #: **视觉通道的接线**（标定 / 准入门 / 两级 grounding / 放大）——
        #: M14 与 M15 共用这一处（标定与准入门不许有两个来源）。
        self._visual = VisualChannel(
            out_dir=self.out_dir,
            gate_mode=gate_mode,
            bypass_admission=bypass_admission,
            model_input=model_input,
        )
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
        truth, truth_source = self._resolve_truth(step, wiring)
        if step.use_previous_landing:
            return self._execute_at_previous_landing(step, wiring, truth, truth_source)
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
        error = (
            math.dist((float(landing[0]), float(landing[1])), truth)
            if landing is not None and truth is not None
            else None
        )
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
            truth_source=truth_source,
            note=("；".join(result.result.notes or ())),
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
        truth: tuple[int, int] | None,
        truth_source: str = "",
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
            math.dist((float(landing[0]), float(landing[1])), truth)
            if truth is not None
            else None
        )
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
            truth_source=truth_source,
            note="；".join(result.notes or ()),
        )

    def _resolve_truth(
        self, step: Step, wiring: _Wiring
    ) -> tuple[tuple[int, int] | None, str]:
        """真值：命令行覆盖 > 树里查控件框中心 > 写死的常量。

        返回 ``(真值, 来源)`` —— **来源要写进报告**：
        真值从哪来必须看得见（树里的控件框 / 画面量的 / 命令行给的）。
        """
        override = self.truth_overrides.get(step.id)
        if override is not None:
            return override, "命令行给的"
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
                    return (
                        (rect[0] + rect[2]) // 2,
                        (rect[1] + rect[3]) // 2,
                    ), f"树里的控件框「{element.name}」"
        if step.truth is not None:
            return step.truth, "画面量的（任务栏的树读不出节点）"
        return None, "（没有真值）"

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
        """读出界面上的缩放值（**从树里读**：控件名里的百分比，或「缩放」那个下拉框的值）。

        ⚠️ **读不到就如实记空** —— 不拿别处的值来充数（工单 M14 的口径：
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
                    self._context["scale_value"] = match.group(0)
                    self._context["scale_percent"] = int(match.group(1))
                    self._context["scale_source"] = f"控件名「{element.name}」"
                    return
        result = port.find(UiaFindRequest(anchor=wiring.anchor, name="缩放"))
        for element in result.elements:
            value = (element.value or "").strip()
            if value:
                self._context["scale_value"] = value
                self._context["scale_source"] = f"下拉框「{element.name}」的值"
                return
        self._context["scale_value"] = ""
        self._context["scale_note"] = (
            "缩放值在树里读不到：界面上显示的百分比既不在控件名里，"
            "「缩放」那个下拉框的 Value 也是空的（本层没有 OCR）"
        )

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
            hwnd = find_window_by_class("Shell_TrayWnd")
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
    """Chrome（浏览器通道）上的真机执行器。"""

    #: BOSS 直聘的职位列表页。
    JOB_URL = "https://www.zhipin.com/web/geek/job"

    def __init__(self, *, out_dir: str | Path, url: str = JOB_URL):
        self.out_dir = Path(out_dir)
        self.url = url
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
        self._browser = self._playwright.chromium.launch(
            channel="chrome",
            headless=False,
            args=["--window-size=1280,900", "--window-position=120,80"],
        )
        self._context = self._browser.new_context(no_viewport=True)
        self._page = self._context.new_page()
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
                f"环境不可达：未登录（浏览器通道起的是全新会话，"
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
            hwnd=hwnd, app="chrome", version=str(self._browser.version)
        )
        entry = table.availability(anchor)
        self._capability = entry.availability.value
        self._context_data["capability"] = entry.availability.value
        self._context_data["capability_reason"] = entry.reason
        self._context_data["page_url"] = url
        self._anchor = anchor
        self._channel = BrowserChannel(structure=self._source, capability=table)
        return ""

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

    def _execute(self, step: Step) -> StepOutcome:
        from minicode.action.capture import CapturePort
        from minicode.action.fakes import FakeControlTree, FakeInputInjector
        from minicode.action.seam import ActionExecutionSeam, SeamRequest
        from minicode.action.windows import WindowsCaptureSource
        from minicode.verification.layer import VerificationLayer

        if step.action_kind is ActionKind.SCROLL:
            # **浏览器通道没有滚动动作**（M13 只做了点击与文本输入）——
            # 桌面注入的滚动不会落到网页上（浏览器不是前台窗口）。
            # 不许拿假注入器糊过去：**显式失败**，报告里写明缺什么。
            return StepOutcome(
                step_id=step.id,
                description=step.description,
                ok=False,
                error_code="channel_unsupported",
                error_message=(
                    "浏览器通道没有滚动动作（M13 未实现）："
                    "桌面注入的滚动到不了网页，需要浏览器自己的输入通道"
                ),
            )
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
        self._policy.observe_round()
        traces = self._sink.traces[before:]
        trace = traces[-1] if traces else None
        verification = (trace.verification_result or {}) if trace is not None else {}
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
        """文本输入（走浏览器通道自己的输入通道，退化会记下来）。"""
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
        clipboard_before = injector.read_clipboard()
        result = typing.type_text(text)
        clipboard_after = injector.read_clipboard()
        self._context_data["input_value"] = self._focused_value()
        # **剪贴板用过后要恢复**（工单 M14 C-2 的验收条款）。
        self._context_data["clipboard_restored"] = clipboard_before == clipboard_after
        #: **形状对齐**：下面那段按**缝的返回值**取字段（``result.result.error_code``）——
        #: 这一条（文本输入）走的是浏览器自己的输入通道，返回的是**动作结果**，
        #: 所以要包成缝的形状。⚠️ 漏了这一层 → ``'ActionResult' object has no
        #: attribute 'result'``（本轮 wave1 的 C-2 就死在这）。
        return SeamResult(
            action=None,
            final_point=None,
            intent=step.description,
            result=result,
        )

    def _focused_value(self) -> str:
        snapshot = self._source.snapshot()
        return snapshot.focused_value or ""


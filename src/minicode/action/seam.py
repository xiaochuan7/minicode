"""**动作执行缝** —— 本项目唯一的测试缝（ADR-0007）。

端口形状（**换掉替身不改变它**）::

    输入：目标描述（自然语言）
        + 一帧截图（物理像素）
        + 可选的控件树快照
    输出：动作（种类 + 物理像素坐标 + 修饰键/文本参数）
        + 最终落点（物理表面空间）
        + 意图

决策层（L3）不在缝内。缝以下的抓屏、DPI 感知、输入注入、控件树查询全部由
:class:`~minicode.action.ports` 里的端口接出去，单测用假实现替换它们，
因此整条链路**不碰真桌面、不需要凭据、不产生费用**。

坐标流::

    控件框（物理表面空间）
        → 截图像素空间（动作协议统一在截图像素空间）
        → 最终落点（物理表面空间，唯一真值空间）

中间那一跳只走 :class:`~minicode.action.surface.SurfaceSpec`，
即缩放系数的**唯一真值来源**。
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Sequence

from minicode.coord.coordinates import CoordinateOutOfBounds

from .errors import ErrorCode
from .executor import ActionExecutor, DoubleClickParams, TimingPolicy
from .geometry import VirtualDesktop
from .ports import (
    CapturedFrame,
    CaptureSource,
    ControlCandidate,
    ControlTree,
    ControlTreeSnapshot,
    InputInjector,
    WindowController,
)
from .protocol import POINT_KINDS, Action, ActionKind, ActionResult
from .records import ActionTrace, TraceSink
from .surface import SurfaceSpec

__all__ = ["SeamRequest", "SeamResult", "ActionExecutionSeam"]

#: 动作种类 → 意图动词。
_INTENT_VERBS = {
    ActionKind.CLICK: "点击",
    ActionKind.DOUBLE_CLICK: "双击",
    ActionKind.RIGHT_CLICK: "右键点击",
    ActionKind.MOVE: "移动到",
    ActionKind.SCROLL: "滚动",
    ActionKind.DRAG: "拖拽",
}


@dataclass(frozen=True)
class SeamRequest:
    """动作执行缝的输入。"""

    target_description: str
    #: 一帧截图。缺省时由截图源抓取。
    frame: CapturedFrame | None = None
    #: 可选的控件树快照。缺省时由控件树通道读一次。
    tree_snapshot: ControlTreeSnapshot | None = None
    #: 动作种类。只接受需要一个落点的动作种类。
    action_kind: ActionKind = ActionKind.CLICK
    #: 动作的额外参数（如 scroll 的 lines）。
    params: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.action_kind not in POINT_KINDS:
            raise ValueError(
                f"动作执行缝只处理需要一个落点的动作种类，收到 {self.action_kind.value!r}；"
                "输入文本 / 按键等动作请直接用动作执行器。"
            )


@dataclass(frozen=True)
class SeamResult:
    """动作执行缝的输出（窄返回类型）。"""

    action: Action | None
    final_point: tuple[int, int] | None
    intent: str
    result: ActionResult
    #: 唯一性拒绝时回报的候选集。
    rejected_candidates: tuple[ControlCandidate, ...] = ()

    @property
    def ok(self) -> bool:
        return self.result.ok


class _SelectionFailure(Exception):
    """候选集里选不出唯一选中项。"""

    def __init__(self, code: ErrorCode, message: str, candidates: tuple[ControlCandidate, ...] = ()):
        super().__init__(message)
        self.code = code
        self.message = message
        self.candidates = candidates


class ActionExecutionSeam:
    """动作执行缝。"""

    def __init__(
        self,
        *,
        capture_sources: Sequence[CaptureSource],
        tree: ControlTree,
        injector: InputInjector,
        window_controller: WindowController | None = None,
        virtual_desktop: VirtualDesktop | None = None,
        sleeper: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.perf_counter,
        timing: TimingPolicy | None = None,
        double_click_params: DoubleClickParams | None = None,
        trace_sink: TraceSink | None = None,
        capture_to_physical_scale: float = 1.0,
    ):
        self.capture_sources = tuple(capture_sources)
        self.tree = tree
        self.injector = injector
        self.window_controller = window_controller
        self.virtual_desktop = virtual_desktop or VirtualDesktop()
        self.executor = ActionExecutor(
            injector,
            sleeper=sleeper,
            clock=clock,
            timing=timing,
            double_click_params=double_click_params,
        )
        self.trace_sink = trace_sink
        self.capture_to_physical_scale = float(capture_to_physical_scale)

    # ------------------------------------------------------------------
    # 主流程
    # ------------------------------------------------------------------

    def run(self, request: SeamRequest) -> SeamResult:
        """跑一次动作，返回「动作 + 最终落点 + 意图」。"""
        skipped: list[str] = []
        frame = request.frame
        if frame is None:
            frame = self._capture_usable(skipped)
            if frame is None:
                return self._fail(
                    request,
                    ErrorCode.CAPTURE_EMPTY,
                    "所有捕获源都只拿到 0×0 画面，跳过该捕获源后仍无可用画面",
                    skipped=skipped,
                )
        surface = self._surface_for(frame)

        # -- 定位 ---------------------------------------------------------
        locate_started = self.executor._clock()
        try:
            snapshot = (
                request.tree_snapshot
                if request.tree_snapshot is not None
                else self.tree.snapshot()
            )
            selected, rejected = self._select(snapshot.candidates, request.target_description)
        except _SelectionFailure as exc:
            locate_ms = (self.executor._clock() - locate_started) * 1000.0
            return self._fail(
                request,
                exc.code,
                exc.message,
                locate_ms=locate_ms,
                rejected=exc.candidates,
                skipped=skipped,
                frame=frame,
            )
        locate_ms = (self.executor._clock() - locate_started) * 1000.0

        # -- 落点（截图像素空间 → 物理表面空间）-----------------------------
        capture_rect = surface.rect_to_capture(selected.rect)
        landing_capture = (
            (capture_rect[0] + capture_rect[2]) / 2.0,
            (capture_rect[1] + capture_rect[3]) / 2.0,
        )
        px, py = surface.capture_to_physical(*landing_capture)
        final_point = (math.floor(px), math.floor(py))

        try:
            surface.assert_within_surface(px, py)
        except CoordinateOutOfBounds as exc:
            return self._fail(
                request,
                ErrorCode.CLIPPED_BY_DESKTOP,
                str(exc),
                locate_ms=locate_ms,
                selected=selected,
                skipped=skipped,
                frame=frame,
            )

        # -- 置前（目标窗口被遮挡时）--------------------------------------
        if self.window_controller is not None and selected.window_handle:
            foreground = self.window_controller.foreground_window()
            if foreground != selected.window_handle:
                raised = self.window_controller.bring_to_front(selected.window_handle)
                now = self.window_controller.foreground_window()
                if not raised or now != selected.window_handle:
                    return self._fail(
                        request,
                        ErrorCode.EXECUTE_FAILED,
                        f"目标窗口 {selected.window_handle} 置前失败"
                        f"（当前前台窗口 {now}）",
                        locate_ms=locate_ms,
                        selected=selected,
                        skipped=skipped,
                        frame=frame,
                    )

        # -- 执行 + 校验 ---------------------------------------------------
        action = self._build_action(request, final_point)
        execute_started = self.executor._clock()
        result = self.executor.execute(action)
        execute_verify_ms = (self.executor._clock() - execute_started) * 1000.0

        failures = {name: None for name in ("locate", "decision", "execute")}
        if not result.ok and result.error_code is not None:
            failures["execute"] = result.error_code.value

        trace = ActionTrace(
            action=action,
            intent=action.intent,
            target_description=request.target_description,
            candidates=snapshot.candidates,
            selected=selected,
            landing_point=final_point,
            capture_width=frame.width,
            capture_height=frame.height,
            screenshot_sha256=frame.digest(),
            sent_width=frame.width,
            sent_height=frame.height,
            image_source=f"{frame.source}|{frame.backend}",
            skipped_capture_sources=tuple(skipped),
            locate_ms=locate_ms,
            decision_ms=0.0,
            execute_verify_ms=execute_verify_ms,
            failures=failures,
            result=result,
        )
        self._emit(trace)
        return SeamResult(
            action=action,
            final_point=final_point,
            intent=action.intent,
            result=result,
        )

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------

    def _capture_usable(self, skipped: list[str]) -> CapturedFrame | None:
        """依次尝试各捕获源，跳过只拿到 0×0 画面的源。"""
        for source in self.capture_sources:
            frame = source.capture()
            if frame.is_empty:
                skipped.append(getattr(source, "name", source.__class__.__name__))
                continue
            return frame
        return None

    def _surface_for(self, frame: CapturedFrame) -> SurfaceSpec:
        return SurfaceSpec(
            virtual_desktop=self.virtual_desktop,
            capture_width=frame.width,
            capture_height=frame.height,
            capture_origin_x=frame.left,
            capture_origin_y=frame.top,
            capture_to_physical_scale=self.capture_to_physical_scale,
        )

    @staticmethod
    def _select(
        candidates: Sequence[ControlCandidate], description: str
    ) -> tuple[ControlCandidate, tuple[ControlCandidate, ...]]:
        """从候选集里选出**唯一**选中项。

        先按名字精确匹配；精确匹配为空时才退到子串匹配。
        匹配到多个就是**唯一性拒绝** —— 拒绝执行并回报候选集，绝不随便挑一个。
        """
        target = description.strip().lower()
        exact = [c for c in candidates if c.name.strip().lower() == target]
        matches = exact or [c for c in candidates if target in c.name.strip().lower()]
        if not matches:
            raise _SelectionFailure(
                ErrorCode.TARGET_NOT_FOUND,
                f"控件树里找不到与「{description}」匹配的候选",
            )
        if len(matches) > 1:
            raise _SelectionFailure(
                ErrorCode.AMBIGUOUS_TARGET,
                f"「{description}」匹配到 {len(matches)} 个同名候选，拒绝执行",
                candidates=tuple(matches),
            )
        return matches[0], ()

    def _build_action(self, request: SeamRequest, final_point: tuple[int, int]) -> Action:
        verb = _INTENT_VERBS.get(request.action_kind, request.action_kind.value)
        intent = f"{verb}「{request.target_description}」"
        params: dict[str, Any] = dict(request.params)
        params.update({"x": final_point[0], "y": final_point[1], "space": "physical-surface"})
        return Action(kind=request.action_kind, params=params, intent=intent)

    def _fail(
        self,
        request: SeamRequest,
        code: ErrorCode,
        message: str,
        *,
        locate_ms: float = 0.0,
        selected: ControlCandidate | None = None,
        rejected: tuple[ControlCandidate, ...] = (),
        skipped: Sequence[str] = (),
        frame: CapturedFrame | None = None,
    ) -> SeamResult:
        """失败路径：产出一条失败记录，**不下发任何动作**。"""
        failures = {name: None for name in ("locate", "decision", "execute")}
        if code in (ErrorCode.TARGET_NOT_FOUND, ErrorCode.AMBIGUOUS_TARGET, ErrorCode.CLIPPED_BY_DESKTOP):
            failures["locate"] = code.value
        elif code is ErrorCode.CAPTURE_EMPTY:
            failures["execute"] = code.value
        else:
            failures["execute"] = code.value
        result = ActionResult(ok=False, error_code=code, error_message=message)
        trace = ActionTrace(
            action=None,
            intent="",
            target_description=request.target_description,
            selected=selected,
            rejected_candidates=rejected,
            capture_width=frame.width if frame else None,
            capture_height=frame.height if frame else None,
            screenshot_sha256=frame.digest() if frame else None,
            sent_width=frame.width if frame else None,
            sent_height=frame.height if frame else None,
            image_source=f"{frame.source}|{frame.backend}" if frame else None,
            skipped_capture_sources=tuple(skipped),
            locate_ms=locate_ms,
            decision_ms=0.0,
            execute_verify_ms=0.0,
            failures=failures,
            result=result,
        )
        self._emit(trace)
        return SeamResult(
            action=None,
            final_point=None,
            intent="",
            result=result,
            rejected_candidates=rejected,
        )

    def _emit(self, trace: ActionTrace) -> None:
        if self.trace_sink is not None:
            self.trace_sink.emit(trace)

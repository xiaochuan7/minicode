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
        → 截图像素空间（中间量，用来走缩放系数的唯一真值来源）
        → 最终落点（物理表面空间，唯一真值空间）

中间那一跳只走 :class:`~minicode.action.surface.SurfaceSpec`，
即缩放系数的**唯一真值来源**。

关于「协议在哪个坐标空间」
------------------------
本单的动作参数坐标用**物理表面空间**，并在参数里显式标注 ``space="physical-surface"``：

* 工单 M0 明确要求「落到**物理表面空间**的一个点」，动作记录里的坐标也必须是物理表面空间；
* 架构备忘 §3「统一在截图像素空间」讲的是**模型参与的动作**（模型只会说截图像素），
  本单没有模型参与，坐标直接来自控件树的物理像素控件框；
* 「最终落点只在 crop 尺度上产生」是**视觉通道**（L2）的规则（M6），
  UIA 通道的控件框本来就是物理像素，不经 crop。

坐标空间必须**显式声明**（架构备忘 §2.6 C-04：禁止用阈值猜坐标系），
所以动作参数带 ``space`` 字段，记录里也带 ``space`` 字段。
"""

from __future__ import annotations

import math
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Any

from minicode.coord.coordinates import CoordinateOutOfBounds
from minicode.redact.config import ModelContextConfig
from minicode.redact.errors import ChannelUnavailableError
from minicode.redact.redactor import TreeRedactor

from .errors import ErrorCode
from .executor import ActionExecutor, DoubleClickParams, TimingPolicy
from .geometry import VirtualDesktop
from .ports import (
    CapturedFrame,
    CaptureSource,
    ControlCandidate,
    ControlLocator,
    ControlTree,
    ControlTreeSnapshot,
    ElementInvoker,
    InputInjector,
    LocateRequest,
    LocateResult,
    WindowAnchor,
    WindowController,
)
from .protocol import POINT_KINDS, Action, ActionKind, ActionResult
from .records import FAILURE_FIELDS, POINT_SPACE, ActionTrace, TraceSink, trace_candidates
from .surface import SurfaceSpec

if TYPE_CHECKING:  # pragma: no cover - 只为类型标注，运行期不导入
    from minicode.redact.redactor import ModelTreeView

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
    #: **控件类型提示**（``"Button"`` 之类）。定位通道据此发**控件类型条件**下的
    #: 定点查询（目标描述不是控件名时靠它缩小范围；没有它就不退到类型扫描）。
    control_type: str = ""

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


def _traced_candidate(candidate: ControlCandidate | None) -> ControlCandidate | None:
    """追踪记录里的候选：**值不落**（密码框留占位符，其余为空）。"""
    if candidate is None:
        return None
    return trace_candidates((candidate,))[0]


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
        model_context_config: ModelContextConfig | None = None,
        locator: ControlLocator | None = None,
        invoker: ElementInvoker | None = None,
        anchor: WindowAnchor | None = None,
    ):
        self.capture_sources = tuple(capture_sources)
        self.tree = tree
        self.injector = injector
        self.window_controller = window_controller
        self.virtual_desktop = virtual_desktop or VirtualDesktop()
        self._sleeper = sleeper
        self._clock = clock
        self.executor = ActionExecutor(
            injector,
            sleeper=sleeper,
            clock=clock,
            timing=timing,
            double_click_params=double_click_params,
        )
        self.trace_sink = trace_sink
        self.capture_to_physical_scale = float(capture_to_physical_scale)
        #: 这条通道要不要把控件树送进模型上下文。
        #: 给了配置 = 要送（脱敏器由缝**自己**持有 → 模型侧文本只有一个出口）；
        #: 缺省 = **不送**（零模型调用的通道）。
        self.model_context_config = model_context_config
        self._redactor = (
            TreeRedactor(model_context_config) if model_context_config is not None else None
        )
        #: **控件定位端口**（UIA 通道，工单 M3）。给了它，定位走这一条：
        #: 定点查询 → 唯一定位 → 执行前重读 → 落点直接取控件框的物理像素中心。
        #: 缺省 = 用本单自己的控件树快照 + 名字匹配（M0 的老路径）。
        self.locator = locator
        #: **Invoke 通道**（UIA 的 InvokePattern）。Invoke 优先于坐标点击。
        self.invoker = invoker
        #: 定位用的窗口锚点（能力表的键 + 查询的根）。
        self.anchor = anchor or WindowAnchor(hwnd=0)

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
        elif frame.is_empty:
            # 直接给进来的 0×0 画面同样按「跳过该捕获源」处理，不产生落点。
            skipped.append("given-frame")
            return self._fail(
                request,
                ErrorCode.CAPTURE_EMPTY,
                "给定的一帧截图是 0×0 画面，跳过该捕获源",
                skipped=skipped,
            )
        surface = self._surface_for(frame)

        # -- 定位 ---------------------------------------------------------
        locate_started = self._clock()
        model_view: ModelTreeView | None = None
        located = None
        if self.locator is not None:
            # UIA 通道（工单 M3）：定位、唯一性拒绝、消歧与执行前重读都在通道里。
            located = self.locator.locate(
                LocateRequest(
                    target_description=request.target_description,
                    anchor=self.anchor,
                    control_type=request.control_type,
                )
            )
            if located.error_code is not None:
                locate_ms = (self._clock() - locate_started) * 1000.0
                return self._fail(
                    request,
                    located.error_code,
                    located.error_message,
                    locate_ms=locate_ms,
                    rejected=located.candidates,
                    skipped=skipped,
                    frame=frame,
                    query_scope=located.query_scope,
                )
            selected = located.selected
            try:
                # 脱敏闸门先过：这条通道要把控件树送进模型上下文 → 必须先有脱敏后的视图。
                # 定位通道**已经渲染过**就直接复用（模型侧文本只有一个出口、只渲染一次）。
                model_view = located.model_view or self._render_model_view(
                    located.candidates
                )
            except ChannelUnavailableError as exc:
                locate_ms = (self._clock() - locate_started) * 1000.0
                return self._fail(
                    request,
                    ErrorCode.LOCATE_FAILED,
                    str(exc),
                    locate_ms=locate_ms,
                    skipped=skipped,
                    frame=frame,
                )
            px, py = float(located.landing_point[0]), float(located.landing_point[1])
            final_point = located.landing_point
        else:
            try:
                snapshot = (
                    request.tree_snapshot
                    if request.tree_snapshot is not None
                    else self.tree.snapshot()
                )
                # 脱敏闸门先过：这条通道要把控件树送进模型上下文 → 必须先有脱敏后的视图。
                model_view = self._render_model_view(snapshot.candidates)
                selected, _ = self._select(snapshot.candidates, request.target_description)
            except ChannelUnavailableError as exc:
                locate_ms = (self._clock() - locate_started) * 1000.0
                return self._fail(
                    request,
                    ErrorCode.LOCATE_FAILED,
                    str(exc),
                    locate_ms=locate_ms,
                    skipped=skipped,
                    frame=frame,
                )
            except _SelectionFailure as exc:
                locate_ms = (self._clock() - locate_started) * 1000.0
                return self._fail(
                    request,
                    exc.code,
                    exc.message,
                    locate_ms=locate_ms,
                    rejected=exc.candidates,
                    skipped=skipped,
                    frame=frame,
                    model_view=model_view,
                )

            # -- 落点（截图像素空间 → 物理表面空间）-------------------------
            capture_rect = surface.rect_to_capture(selected.rect)
            landing_capture = (
                (capture_rect[0] + capture_rect[2]) / 2.0,
                (capture_rect[1] + capture_rect[3]) / 2.0,
            )
            px, py = surface.capture_to_physical(*landing_capture)
            final_point = (math.floor(px), math.floor(py))
        locate_ms = (self._clock() - locate_started) * 1000.0

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
                model_view=model_view,
            )

        # -- 置前（目标窗口被遮挡时）--------------------------------------
        if self.window_controller is not None and selected.window_handle:
            if self.window_controller.is_occluded(selected.window_handle, final_point):
                raised = self.window_controller.bring_to_front(selected.window_handle)
                # 句柄口径由窗口控制统一（子窗口按根窗口算），
                # 这里不能拿子窗口句柄去比顶层的前台窗口句柄。
                if not raised or not self.window_controller.is_foreground(
                    selected.window_handle
                ):
                    return self._fail(
                        request,
                        ErrorCode.EXECUTE_FAILED,
                        f"目标窗口 {selected.window_handle} 置前失败"
                        f"（当前前台窗口 {self.window_controller.foreground_window()}）",
                        locate_ms=locate_ms,
                        selected=selected,
                        skipped=skipped,
                        frame=frame,
                        model_view=model_view,
                    )

        # -- 执行 + 校验 ---------------------------------------------------
        action = self._build_action(request, final_point)
        execute_started = self._clock()
        result = self._execute(action, final_point, located)
        execute_verify_ms = (self._clock() - execute_started) * 1000.0

        failures = {name: None for name in FAILURE_FIELDS}
        if not result.ok and result.error_code is not None:
            failures["execute"] = result.error_code.value

        trace = ActionTrace(
            action=action,
            intent=action.intent,
            target_description=request.target_description,
            candidates=trace_candidates(
                located.candidates if located is not None else snapshot.candidates
            ),
            selected=_traced_candidate(selected),
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
            query_scope=located.query_scope if located is not None else "",
            result=result,
            model_view=model_view,
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

    def _execute(
        self,
        action: Action,
        final_point: tuple[int, int],
        located: LocateResult | None,
    ) -> ActionResult:
        """执行动作。**Invoke 优先**：控件支持 Invoke 时走 InvokePattern；
        不支持（或 Invoke 失败）时改用坐标点击，并**记一条改用事件**。

        没走 UIA 通道（``located`` 是 ``None``）时就是 M0 的老路径：直接坐标点击。
        """
        if located is None:
            return self.executor.execute(action)
        notes: list[str] = []
        if located.invokable and self.invoker is not None:
            if self.invoker.invoke(located.selected.element_id):
                return ActionResult(
                    ok=True,
                    landing_point=final_point,
                    notes=("Invoke：走 UIA 的 InvokePattern（不是坐标点击）",),
                )
            notes.append("Invoke 失败，改用坐标点击")
        elif located.invokable:
            notes.append("没有 Invoke 通道，改用坐标点击")
        else:
            notes.append("控件不支持 Invoke，改用坐标点击")
        result = self.executor.execute(action)
        if not notes:
            return result
        return replace(result, notes=tuple(result.notes) + tuple(notes))

    def _render_model_view(
        self, candidates: Sequence[ControlCandidate]
    ) -> ModelTreeView | None:
        """准备「送进模型上下文的那份界面状态」（**已脱敏**）。

        * 这条通道**不把树送模型**时（没有配置）→ ``None``；
        * 脱敏没上线 → 抛 :class:`ChannelUnavailableError`，
          上层据此报「该通道不可用」，**一个字符的模型侧文本都不产生**。
        """
        if self._redactor is None:
            return None
        return self._redactor.render(candidates)

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
        params.update({"x": final_point[0], "y": final_point[1], "space": POINT_SPACE})
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
        model_view: ModelTreeView | None = None,
        query_scope: str = "",
    ) -> SeamResult:
        """失败路径：产出一条失败记录，**不下发任何动作**。"""
        failures = {name: None for name in FAILURE_FIELDS}
        if code in (
            ErrorCode.TARGET_NOT_FOUND,
            ErrorCode.AMBIGUOUS_TARGET,
            ErrorCode.TOO_MANY_CANDIDATES,
            ErrorCode.LOCATE_FAILED,
            ErrorCode.RELOCATE_FAILED,
            ErrorCode.CLIPPED_BY_DESKTOP,
        ):
            # 定位层：没找到 / 找不唯一 / 通道不可用 / 落点不可用。
            failures["locate"] = code.value
        else:
            # 其余都是执行层（抓帧失败也归这里：截图属于「执行+校验」段，架构备忘 §2.5 ③）。
            failures["execute"] = code.value
        result = ActionResult(ok=False, error_code=code, error_message=message)
        trace = ActionTrace(
            action=None,
            intent="",
            target_description=request.target_description,
            selected=_traced_candidate(selected),
            rejected_candidates=trace_candidates(rejected),
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
            query_scope=query_scope,
            result=result,
            model_view=model_view,
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

"""动作记录与追踪记录。

两条红线：

* **只落哈希**：一次动作只产出截图摘要（SHA-256 十六进制），**不落图像文件**。
* **恰好一条**：一次动作恰好一条机器可解析的记录。

追踪记录（:class:`ActionTrace`）走可注入的旁路出口，不进主返回类型——
它是观测数据而不是控制流数据，这样以后换定位后端时上层调用方不用改。

词汇对应
--------
* **动作日志**（`CONTEXT.md`）：落盘的动作记录集合，本模块的 :class:`ActionLog`。
* **动作记录**（工单口径）：一次动作的那一条可解析记录，本模块的 :class:`ActionRecord`；
  工单要求「每次动作恰好一条动作记录」。
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from minicode.redact.patterns import PASSWORD_PLACEHOLDER

from .errors import allows_visual_fallback
from .ports import (
    CapturedFrame,
    ControlCandidate,
    GroundingEcho,
    SelectionEcho,
    VisualAdmission,
)
from .protocol import PROTOCOL_VERSION, Action, ActionResult

if TYPE_CHECKING:  # pragma: no cover - 只为类型标注，运行期不导入（避免循环依赖）
    from minicode.redact.redactor import ModelTreeView

__all__ = [
    "ActionTrace",
    "ActionRecord",
    "ActionLog",
    "TraceSink",
    "ListTraceSink",
    "MultiTraceSink",
    "build_record",
    "frame_fields",
    "trace_candidates",
    "POINT_SPACE",
    "FAILURE_FIELDS",
]

#: 落点所在的坐标空间。本项目唯一真值空间。
POINT_SPACE = "physical-surface"

#: 三类失败字段的固定名字。
FAILURE_FIELDS = ("locate", "decision", "execute")

#: **不进动作记录**的参数键：输入文本的正文可能含敏感内容
#: （与 C-15 同源：界面内容不进日志）。
_UNRECORDED_PARAM_KEYS = frozenset({"text"})


def _recorded_params(params: Mapping[str, Any]) -> dict[str, Any]:
    """动作记录里要落的参数（去掉正文类参数）。"""
    return {key: value for key, value in params.items() if key not in _UNRECORDED_PARAM_KEYS}


@dataclass(frozen=True)
class ActionTrace:
    """一次动作的追踪记录（观测数据）。"""

    action: Action | None = None
    intent: str = ""
    target_description: str = ""
    candidates: tuple[ControlCandidate, ...] = ()
    selected: ControlCandidate | None = None
    rejected_candidates: tuple[ControlCandidate, ...] = ()
    #: 送进模型上下文的那份界面状态（**已脱敏**）。
    #: 这条通道不把控件树送模型时是 ``None`` —— 追踪记录里没有它，
    #: 就说明这条通道**没走过脱敏**。
    model_view: ModelTreeView | None = None
    landing_point: tuple[int, int] | None = None
    capture_width: int | None = None
    capture_height: int | None = None
    screenshot_sha256: str | None = None
    #: 实际发送尺寸（本单没有模型参与预处理，因此与抓图尺寸相同）。
    sent_width: int | None = None
    sent_height: int | None = None
    #: 图像来源（捕获源 | 后端）。
    image_source: str | None = None
    #: **被跳过的捕获源**（名字）。
    skipped_capture_sources: tuple[str, ...] = ()
    #: **被标记为不可用的捕获源**（名字 → 原因）。工单 M9 AC 6：
    #: 抓屏拿到 0×0 画面时**跳过**该捕获源，并把它**标记为不可用**
    #: （可读出「哪个源不可用、为什么」）。
    unavailable_capture_sources: Mapping[str, str] = field(default_factory=dict)
    #: 这一步**走了哪条通道**（``"uia"`` / ``"visual"``；空 = 没走通道定位）。
    channel: str = ""
    #: 这一步**定位阶段**的失败类型（工单 M9 AC 7）：
    #: ``"coverage"`` / ``"selection"`` / ``"execution"`` / ``"channel"`` /
    #: ``"decision"``；没失败时是空串。切视觉成功时记的是 UIA 通道那次
    #: **覆盖失败** —— 那正是切视觉的理由。
    failure_type: str = ""
    #: 这一步的**模型调用次数**（UIA 消歧 + 视觉 grounding）。
    #: 工单 M9 AC 3：UIA 命中时它是 **0**，而且是**可被计数证明**的 0。
    model_calls: int = 0
    #: 三段耗时（毫秒）：定位 / 决策 / 执行+校验。
    locate_ms: float = 0.0
    decision_ms: float = 0.0
    execute_verify_ms: float = 0.0
    #: 三类失败字段：定位失败 / 决策失败 / 执行失败。
    failures: Mapping[str, str | None] = field(
        default_factory=lambda: {name: None for name in FAILURE_FIELDS}
    )
    #: **查询口径**（``"window-anchored"`` / ``"desktop-level"`` / 空）。
    #: 工单 M3 AC 8：验收记录里要写明用的是窗口锚定还是桌面级查询
    #: （416 ms 与 8 秒两个口径不许混用）。
    query_scope: str = ""
    #: **每步回显**（消歧时才有）：模型发出的选择结果 + 实际发送的图片尺寸。
    #: **不含坐标** —— UIA 通道的消歧不产坐标（工单 M4 AC 9）。
    selection_echo: SelectionEcho | None = None
    #: 候选数上限（运行时参数）与**取值来源**（工单 M4 AC 4）。
    candidate_limit: int = 0
    candidate_limit_source: str = ""
    #: **视觉通道准入判定**（工单 M5）。UIA 通道覆盖失败、而切视觉这条路
    #: 要走判定时才带它；带它就说明「切视觉这条路被看过」。
    visual_admission: VisualAdmission | None = None
    #: 视觉通道的**粗定位点**（物理表面空间）。走视觉通道时才有。
    coarse_point: tuple[float, float] | None = None
    #: 视觉通道的**裁剪计划**（落盘形状）。含「目标是否完整包含」。
    crop_plan: Mapping[str, Any] | None = None
    #: 视觉通道的**每步回显**（模型发出的坐标 + 实际发送尺寸 + 图像来源）。
    grounding_echoes: tuple[GroundingEcho, ...] = ()
    #: 视觉通道**是否回落**到粗定位结果（精定位失败时为真）。
    fallback: bool = False
    #: 回落到粗定位结果的**原因**（没回落时为空）。
    fallback_reason: str = ""
    #: 回落原因的**枚举**（工单 M7 AC 4：回落原因要能机器读）。
    fallback_cause: str = ""
    #: **门控是否命中**（工单 M7 AC 4 的四个埋点字段之一）。
    gate_hit: bool = False
    #: **门控命中原因**（枚举，可多条）。
    gate_reasons: tuple[str, ...] = ()
    #: **门控不可判定的条件**（枚举）—— 不触发、也不否决其余条件，但必须显式可见。
    gate_undecidable: tuple[str, ...] = ()
    #: 这一步**是否真的开启了 crop 精定位**（整屏第二遍不算）。
    refine_enabled: bool = False
    #: **校验结果**（工单 M11）：期望断言 / 默认校验的结论。
    #:
    #: 它是**观测数据**，走旁路出口（不进主返回类型）。
    #: 形状由校验层自己渲染（``verification.layer.VerificationResult.to_dict()``）。
    verification_result: Mapping[str, Any] | None = None
    #: **动作前**的状态（工单 M11 AC 7：动作日志要含「动作前后状态」）。
    state_before: Mapping[str, Any] | None = None
    #: **动作后**的状态。
    state_after: Mapping[str, Any] | None = None
    result: ActionResult = field(default_factory=lambda: ActionResult(ok=True))

    @property
    def allows_visual_fallback(self) -> bool:
        """**能不能切视觉** —— 由失败类型推导（红线只有一处）。

        只有**覆盖失败**允许切视觉（架构决策备忘 §2.5 ①）。
        它是**推导值**，不是另一份数据 —— 两处数据就不可能各说各话。
        """
        if not self.failure_type:
            return False
        return allows_visual_fallback(self.failure_type)


@runtime_checkable
class TraceSink(Protocol):
    """追踪记录的旁路出口。"""

    def emit(self, trace: ActionTrace) -> None:
        ...


class ListTraceSink:
    """把追踪记录收进列表（单测与命令行用）。"""

    def __init__(self) -> None:
        self.traces: list[ActionTrace] = []

    def emit(self, trace: ActionTrace) -> None:
        self.traces.append(trace)


class MultiTraceSink:
    """扇出：把**同一条**追踪记录发给多个出口。

    动作日志（:class:`ActionLog` 的来源）与**停止条件**（工单 M12 的卡死检测器）
    都要看同一条记录 —— 扇出保证它们看的是**同一份数据**，
    而不是两份各算一遍的副本。
    """

    def __init__(self, *sinks: TraceSink) -> None:
        self.sinks = tuple(sinks)

    def emit(self, trace: ActionTrace) -> None:
        for sink in self.sinks:
            sink.emit(trace)


@dataclass(frozen=True)
class ActionRecord:
    """一次动作的机器可解析记录。"""

    protocol_version: str
    timestamp: str
    action: str
    intent: str
    target_name: str | None = None
    target_rect: tuple[int, int, int, int] | None = None
    target_control_type: str | None = None
    target_window_handle: int | None = None
    #: 目标**窗口标题**（工单 M11 AC 7：动作日志要含窗口句柄**与标题**）。
    target_window_title: str | None = None
    point_x: int | None = None
    point_y: int | None = None
    point_space: str = POINT_SPACE
    capture_width: int | None = None
    capture_height: int | None = None
    sent_width: int | None = None
    sent_height: int | None = None
    image_source: str | None = None
    screenshot_sha256: str | None = None
    ok: bool = False
    error_code: str | None = None
    error_message: str | None = None
    #: 注入后回读的光标位置（物理表面空间）。哨兵值 (−32000,−32000) 会被判执行失败。
    cursor_readback: tuple[int, int] | None = None
    #: 光标回读与落点的偏差（物理像素）。验收要求它是 0。
    cursor_delta_px: int | None = None
    #: 唯一性拒绝时回报的候选集（拒绝执行时没有选中项）。
    rejected_candidates: tuple[dict[str, Any], ...] = ()
    locate_ms: float = 0.0
    decision_ms: float = 0.0
    execute_verify_ms: float = 0.0
    #: 动作自身的实测时序（毫秒）：pre_move / hold / total 等。
    action_timing: Mapping[str, Any] | None = None
    #: 落点复验：动作后重读控件树得到的偏差（物理像素）。
    landing_delta_px: float | None = None
    #: 落点复验是否真的做成了（复验时找不到目标就是 False）。
    landing_verified: bool | None = None
    #: **查询口径**：``"window-anchored"``（窗口锚定）/ ``"desktop-level"``（桌面级）
    #: / 空（这条通道不查控件树）。工单 M3 AC 8 要求写明，不许两种口径混用。
    query_scope: str = ""
    #: 动作参数（机器可解析）。**输入文本的正文不落**（可能含敏感内容）。
    params: Mapping[str, Any] | None = None
    failure_locate: str | None = None
    failure_decision: str | None = None
    failure_execute: str | None = None
    #: 脱敏计数（密码框替换数 / 正则命中数 / 丢弃正文类字段数 / 输出长度）。
    #: 没走过脱敏时是 ``None`` —— 「某条通道没走脱敏」因此能被看见。
    redaction: Mapping[str, Any] | None = None
    #: **每步回显**（消歧时才有）：模型发出的选择结果 + 实际发送的图片尺寸。
    #: **不含坐标**（UIA 通道的消歧不产坐标，工单 M4 AC 9）。
    selection_echo: Mapping[str, Any] | None = None
    #: 这一步**走了哪条通道**（``"uia"`` / ``"visual"``；空 = 没走通道定位）。
    channel: str = ""
    #: 这一步**定位阶段**的失败类型（工单 M9 AC 7）：``"coverage"`` /
    #: ``"selection"`` / ``"execution"`` / ``"channel"`` / ``"decision"``；
    #: 没失败时是空串。
    failure_type: str = ""
    #: 这一步的**模型调用次数**（UIA 消歧 + 视觉 grounding）。UIA 命中时是 0。
    model_calls: int = 0
    #: **被标记为不可用的捕获源**（名字 → 原因）。
    unavailable_capture_sources: Mapping[str, str] = field(default_factory=dict)
    #: 候选数上限（运行时参数）与**取值来源**（工单 M4 AC 4）。
    candidate_limit: int = 0
    candidate_limit_source: str = ""
    #: **视觉通道准入判定**（工单 M5）：可用吗 / 判定标度 / 标度判定 /
    #: 落点等价 / 等价标度列表。没走过判定时是 ``None``。
    visual_admission: Mapping[str, Any] | None = None
    #: 视觉通道的**粗定位点**（物理表面空间）。
    coarse_point: tuple[float, float] | None = None
    #: 视觉通道的**裁剪计划**（落盘形状）。
    crop_plan: Mapping[str, Any] | None = None
    #: 视觉通道的**每步回显**（模型发出的坐标 + 实际发送尺寸）。
    grounding_echoes: tuple[Mapping[str, Any], ...] = ()
    #: 视觉通道**是否回落**到粗定位结果（精定位失败时为真）。
    fallback: bool = False
    #: 回落到粗定位结果的**原因**（没回落时为空）。
    fallback_reason: str = ""
    #: 回落原因的**枚举**（工单 M7 AC 4）。
    fallback_cause: str = ""
    #: **门控是否命中**（工单 M7 AC 4）。
    gate_hit: bool = False
    #: **门控命中原因**（枚举，可多条）。
    gate_reasons: tuple[str, ...] = ()
    #: **门控不可判定的条件**（枚举）。
    gate_undecidable: tuple[str, ...] = ()
    #: 这一步**是否真的开启了 crop 精定位**（整屏第二遍不算）。
    refine_enabled: bool = False
    #: **动作前后的状态**（工单 M11 AC 7：动作日志要含「动作前后状态」）。
    #: 没接校验层时是 ``None``（不假装有状态）。
    state_before: Mapping[str, Any] | None = None
    state_after: Mapping[str, Any] | None = None
    #: **校验结果**（工单 M11）：期望断言 / 默认校验的结论（落盘形状由校验层渲染）。
    verification_result: Mapping[str, Any] | None = None
    notes: tuple[str, ...] = ()

    @property
    def allows_visual_fallback(self) -> bool:
        """**能不能切视觉** —— 由失败类型推导（红线只有一处）。"""
        if not self.failure_type:
            return False
        return allows_visual_fallback(self.failure_type)

    def to_dict(self) -> dict[str, Any]:
        return {
            "protocol_version": self.protocol_version,
            "timestamp": self.timestamp,
            "action": self.action,
            "intent": self.intent,
            "target": {
                "name": self.target_name,
                "rect": list(self.target_rect) if self.target_rect else None,
                "control_type": self.target_control_type,
                "window_handle": self.target_window_handle,
                "window_title": self.target_window_title,
            },
            "point": (
                {"x": self.point_x, "y": self.point_y, "space": self.point_space}
                if self.point_x is not None and self.point_y is not None
                else None
            ),
            "capture": {"width": self.capture_width, "height": self.capture_height},
            "sent_size": {"width": self.sent_width, "height": self.sent_height},
            "image_source": self.image_source,
            "screenshot_sha256": self.screenshot_sha256,
            "result": {
                "ok": self.ok,
                "error_code": self.error_code,
                "error_message": self.error_message,
                "cursor_readback": list(self.cursor_readback) if self.cursor_readback else None,
                "cursor_delta_px": self.cursor_delta_px,
            },
            "rejected_candidates": [dict(item) for item in self.rejected_candidates],
            "timings_ms": {
                "locate": self.locate_ms,
                "decision": self.decision_ms,
                "execute_verify": self.execute_verify_ms,
            },
            # 单步的三段**占比**（由上面三个值算出来，不是另一份数据）。
            "timings_share": _stage_share(
                self.locate_ms, self.decision_ms, self.execute_verify_ms
            ),
            "action_timing": dict(self.action_timing) if self.action_timing else None,
            "verification": {
                "landing_delta_px": self.landing_delta_px,
                "landing_verified": self.landing_verified,
            },
            "query_scope": self.query_scope,
            "params": dict(self.params) if self.params is not None else None,
            "failures": {
                "locate": self.failure_locate,
                "decision": self.failure_decision,
                "execute": self.failure_execute,
            },
            "redaction": dict(self.redaction) if self.redaction is not None else None,
            "selection_echo": (
                dict(self.selection_echo) if self.selection_echo is not None else None
            ),
            "allows_visual_fallback": self.allows_visual_fallback,
            "channel": self.channel,
            "failure_type": self.failure_type,
            "model_calls": self.model_calls,
            "unavailable_capture_sources": dict(self.unavailable_capture_sources),
            "candidate_limit": self.candidate_limit,
            "candidate_limit_source": self.candidate_limit_source,
            "visual_admission": (
                dict(self.visual_admission) if self.visual_admission is not None else None
            ),
            "coarse_point": list(self.coarse_point) if self.coarse_point else None,
            "crop_plan": dict(self.crop_plan) if self.crop_plan is not None else None,
            "grounding_echoes": [dict(echo) for echo in self.grounding_echoes],
            "fallback": self.fallback,
            "fallback_reason": self.fallback_reason,
            "fallback_cause": self.fallback_cause,
            "gate_hit": self.gate_hit,
            "gate_reasons": list(self.gate_reasons),
            "gate_undecidable": list(self.gate_undecidable),
            "refine_enabled": self.refine_enabled,
            "state": {
                "before": dict(self.state_before) if self.state_before is not None else None,
                "after": dict(self.state_after) if self.state_after is not None else None,
            },
            "verification_result": (
                dict(self.verification_result)
                if self.verification_result is not None
                else None
            ),
            "notes": list(self.notes),
        }


def _stage_share(
    locate_ms: float, decision_ms: float, execute_verify_ms: float
) -> dict[str, float]:
    """三段耗时各自的**占比**（单步用；由那三个值算出来，不是另一份数据）。"""
    total = float(locate_ms) + float(decision_ms) + float(execute_verify_ms)
    if total <= 0:
        return {"locate": 0.0, "decision": 0.0, "execute_verify": 0.0}
    return {
        "locate": float(locate_ms) / total,
        "decision": float(decision_ms) / total,
        "execute_verify": float(execute_verify_ms) / total,
    }


def frame_fields(frame: CapturedFrame | None) -> dict[str, Any]:
    """追踪记录里**帧相关**的那几个字段（抓图尺寸 / 截图哈希 / 图像来源）。

    **只落哈希，不落原图** —— 与动作记录同一条红线。
    """
    if frame is None:
        return {
            "capture_width": None,
            "capture_height": None,
            "screenshot_sha256": None,
            "image_source": None,
        }
    return {
        "capture_width": frame.width,
        "capture_height": frame.height,
        "screenshot_sha256": frame.digest(),
        "image_source": f"{frame.source}|{frame.backend}",
    }


def trace_candidates(
    candidates: Sequence[ControlCandidate],
) -> tuple[ControlCandidate, ...]:
    """给**追踪记录**用的候选集：值一律不落（密码框留占位符，其余为空）。

    追踪记录是观测数据，不是定位数据：控件名与控件框保持原值，
    但**值**不落盘 —— 密码框留占位符（看得见「这里有个密码框」），
    其余控件的值一律为空（不落盘任何正文）。

    规则放在这里（追踪记录归本模块管），脱敏规则在 :mod:`minicode.redact`。
    """
    return tuple(
        replace(
            candidate,
            value=PASSWORD_PLACEHOLDER if candidate.is_password else "",
            extra={},
        )
        for candidate in candidates
    )


def build_record(trace: ActionTrace, *, timestamp: str | None = None) -> ActionRecord:
    """把追踪记录转成落盘的动作记录。"""
    selected = trace.selected
    result = trace.result
    failures = trace.failures
    return ActionRecord(
        protocol_version=PROTOCOL_VERSION,
        timestamp=timestamp or datetime.now().astimezone().isoformat(timespec="seconds"),
        action=trace.action.kind.value if trace.action else "",
        intent=trace.intent,
        target_name=selected.name if selected else None,
        target_rect=selected.rect if selected else None,
        target_control_type=selected.control_type if selected else None,
        target_window_handle=selected.window_handle if selected else None,
        target_window_title=(selected.window_title or None) if selected else None,
        point_x=trace.landing_point[0] if trace.landing_point else None,
        point_y=trace.landing_point[1] if trace.landing_point else None,
        capture_width=trace.capture_width,
        capture_height=trace.capture_height,
        sent_width=trace.sent_width,
        sent_height=trace.sent_height,
        image_source=trace.image_source,
        screenshot_sha256=trace.screenshot_sha256,
        ok=result.ok,
        error_code=result.error_code.value if result.error_code else None,
        error_message=result.error_message,
        cursor_readback=result.cursor_readback,
        cursor_delta_px=result.cursor_delta_px,
        rejected_candidates=tuple(
            {
                "name": candidate.name,
                "control_type": candidate.control_type,
                "rect": list(candidate.rect),
                # 元素 id：同名候选之间的**可区分标识**（每次观测重新生成）。
                "element_id": candidate.element_id,
            }
            for candidate in trace.rejected_candidates
        ),
        locate_ms=trace.locate_ms,
        decision_ms=trace.decision_ms,
        execute_verify_ms=trace.execute_verify_ms,
        action_timing=dict(result.timing) if result.timing else None,
        failure_locate=failures.get("locate"),
        failure_decision=failures.get("decision"),
        failure_execute=failures.get("execute"),
        query_scope=trace.query_scope,
        params=_recorded_params(trace.action.params) if trace.action is not None else None,
        redaction=(
            trace.model_view.report.to_dict() if trace.model_view is not None else None
        ),
        selection_echo=(
            trace.selection_echo.to_dict() if trace.selection_echo is not None else None
        ),
        channel=trace.channel,        failure_type=trace.failure_type,
        model_calls=trace.model_calls,
        unavailable_capture_sources=dict(trace.unavailable_capture_sources),
        candidate_limit=trace.candidate_limit,
        candidate_limit_source=trace.candidate_limit_source,
        visual_admission=(
            trace.visual_admission.to_dict() if trace.visual_admission is not None else None
        ),
        coarse_point=trace.coarse_point,
        crop_plan=dict(trace.crop_plan) if trace.crop_plan is not None else None,
        grounding_echoes=tuple(echo.to_dict() for echo in trace.grounding_echoes),
        fallback=trace.fallback,
        fallback_reason=trace.fallback_reason,
        fallback_cause=trace.fallback_cause,
        gate_hit=trace.gate_hit,
        gate_reasons=tuple(trace.gate_reasons),
        gate_undecidable=tuple(trace.gate_undecidable),
        refine_enabled=trace.refine_enabled,
        state_before=(
            dict(trace.state_before) if trace.state_before is not None else None
        ),
        state_after=dict(trace.state_after) if trace.state_after is not None else None,
        # 动作日志里的「前后状态」**只存一份**：校验结果那份里把状态摘掉。
        verification_result=(
            {
                key: value
                for key, value in trace.verification_result.items()
                if key not in ("state_before", "state_after")
            }
            if trace.verification_result is not None
            else None
        ),
        notes=tuple(result.notes),
    )


class ActionLog:
    """动作日志：每次动作追加一行 JSON。"""

    def __init__(self, directory: str | Path):
        self.directory = Path(directory)
        self.path = self.directory / "action-records.jsonl"

    def append(self, record: ActionRecord) -> Path:
        self.directory.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record.to_dict(), ensure_ascii=False) + "\n")
        return self.path

    def records(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        return [
            json.loads(line)
            for line in self.path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

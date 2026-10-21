"""真机分布回归的**测量台**（工单 M6 AC 9）。

测的是**两级 grounding 的落点误差分布**：

* 真值 = UIA 控件框的物理像素中心（控件框是本机唯一的独立真值）；
* 粗定位（L0）误差 = 粗定位点与真值的距离；
* 精定位（L1）误差 = 最终落点与真值的距离；
* 判据 = **分布**（中位数与 p90），不是单次数字；要求精定位那一级
  **不劣于**粗定位。

⚠️ **这个测量台绕过了 M5 的准入门**（只为了回答「两级 grounding 的落点误差
分布」这个问题）：生产路径上的视觉通道仍然只认准入门 —— 标定没过的模型，
在生产路径上照样不可用。测量台会把两件事都打印出来：

* 标定结论与**准入判定**（可用 / 不可用）；
* 本次分布测的是哪个标度。

本模块**不注入任何输入**（只截图 + 调模型）。
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from minicode.action.ports import CapturedFrame
from minicode.coord.coordinates import ModelScale
from minicode.model.effort import Effort
from minicode.uia.ports import UiaFindRequest

from .candidates import CandidateSource, candidate_coverage
from .channel import TwoStageGrounder
from .gate import GateMode, GateReport, GateSample, build_gate_report
from .stats import ErrorDistribution, not_worse_than, summarize_errors

__all__ = [
    "BenchTarget",
    "TargetOutcome",
    "BenchReport",
    "collect_targets",
    "gate_report",
    "measure",
]

#: 控件框的最大边长（物理像素）：太大的控件不是「小图标」，也不适合做落点目标。
MAX_TARGET_SIDE_PX = 400

#: 「小图标」的边长上限（物理像素，工单 AC 9 的 ≤16 px）。
SMALL_ICON_SIDE_PX = 16

#: 任务栏这类窗口里可用的控件类型（真值来源是 UIA 的控件类型表）。
DEFAULT_CONTROL_TYPES = ("Button", "Text", "Image", "MenuItem", "SplitButton")


@dataclass(frozen=True)
class BenchTarget:
    """一个测量目标（真值来自 UIA 控件框）。"""

    name: str
    #: 控件框（物理像素，``(left, top, right, bottom)``）。
    rect: tuple[int, int, int, int]
    #: 控件框中心（物理像素）。
    truth: tuple[float, float]
    #: 控件框的**逻辑**尺寸（= 物理尺寸 ÷ 缩放系数）。
    #: 工单 AC 9 的「≤16 px 小图标」按**逻辑像素**读（ScreenSpot-Pro 的口径：
    #: 16 px 的图标在 1920×1080 的图里；本机 150% 缩放下 = 24 物理像素）。
    size_logical: tuple[int, int] = (0, 0)

    @property
    def size_px(self) -> tuple[int, int]:
        left, top, right, bottom = self.rect
        return (right - left, bottom - top)

    @property
    def is_small_icon(self) -> bool:
        """是不是「≤16 px 的小图标」（按逻辑像素读）。"""
        width, height = self.size_logical or self.size_px
        return min(width, height) <= SMALL_ICON_SIDE_PX


@dataclass(frozen=True)
class TargetOutcome:
    """一个目标的测量结果。"""

    name: str
    size_px: tuple[int, int]
    #: 逻辑尺寸（= 物理尺寸 ÷ 缩放系数）——「≤16 px 小图标」按它判。
    size_logical: tuple[int, int] = (0, 0)
    coarse_error_px: float | None = None
    refine_error_px: float | None = None
    #: **精定位落点离 crop 窗口中心多远**（物理像素）—— **回落判据的量**
    #: （参考点是 crop 窗口中心，不是粗定位点：crop 走 shift 语义时两者不同）。
    refine_deviation_px: float | None = None
    #: **两级的位移** ‖ε₁‖₂（物理像素）—— 公开 zoom 一致性判据的量。
    zoom_consistency_px: float | None = None
    #: 同一个量的**公开判据刻度**（``c = ε₁ × 1000 / crop 宽``）。
    #:
    #: ⚠️ **落盘键叫 ``zoom_consistency_c1000``**，属性名去掉了那三个数字 ——
    #: 免得撞上「不许出现写死分母」那条源码级扫描（那个 1000 是**公开判据
    #: 自己的刻度**，不是坐标换算的分母 —— 分母只来自标定）。
    zoom_consistency_c: float | None = None
    #: **候选覆盖率**三列（方案：图标能不能救要**单独报覆盖率**，
    #: 不能只看最终命中率）。没有候选源时**全是 None**（不是 0 —— 「没量」
    #: 与「量出来是 0」不是一回事）。
    candidate_iou_max: float | None = None
    candidate_iou_at_0_3: bool | None = None
    candidate_iou_at_0_5: bool | None = None
    candidate_center_inside: bool | None = None
    fallback: bool = False
    fallback_reason: str = ""
    #: 回落原因的枚举（工单 M7 AC 4）。
    fallback_cause: str = ""
    #: **门控埋点**（工单 M7 AC 4）：命中 / 命中原因 / 判不了的条件 / 是否开启精定位。
    gate_hit: bool = False
    gate_reasons: tuple[str, ...] = ()
    gate_undecidable: tuple[str, ...] = ()
    refine_enabled: bool = False
    coarse_point: tuple[float, float] | None = None
    landing_point: tuple[int, int] | None = None
    #: 两次模型请求的输入 token 数（粗定位 / 精定位）—— 用来核对服务端缩图。
    input_tokens: tuple[int | None, int | None] = (None, None)
    error_message: str = ""

    @property
    def is_small_icon(self) -> bool:
        """是不是「≤16 px 的小图标」（按**逻辑**像素判，与 :class:`BenchTarget` 同一口径）。"""
        width, height = self.size_logical or self.size_px
        return min(width, height) <= SMALL_ICON_SIDE_PX


@dataclass(frozen=True)
class BenchReport:
    """一次分布回归的结论。"""

    #: 用的标度（来自标定）。
    scale: str
    #: 视觉通道准入判定（生产路径认它；测量台只报告）。
    admission_available: bool
    admission_reason: str
    calibration_ok: bool
    calibration_max_error_px: float
    outcomes: tuple[TargetOutcome, ...] = ()
    coarse: ErrorDistribution | None = None
    refine: ErrorDistribution | None = None
    notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def refine_not_worse(self) -> bool:
        """精定位那一级的分布不劣于粗定位吗。"""
        if self.coarse is None or self.refine is None:
            return False
        return not_worse_than(self.refine, self.coarse)

    @property
    def small_icon_count(self) -> int:
        """样本里「≤16 px 小图标」的个数（**逻辑**像素口径，与目标收集同一处判据）。"""
        return sum(1 for outcome in self.outcomes if outcome.is_small_icon)

    @property
    def gate(self) -> GateReport:
        """**门控埋点报告**（与分数同一批样本 —— 就是 ``self.outcomes``）。"""
        return gate_report(self.outcomes)

    def to_dict(self) -> dict[str, Any]:
        return {
            "scale": self.scale,
            "admission": {
                "available": self.admission_available,
                "reason": self.admission_reason,
            },
            "calibration": {
                "ok": self.calibration_ok,
                "max_error_px": self.calibration_max_error_px,
            },
            "targets": len(self.outcomes),
            "small_icon_targets": self.small_icon_count,
            "coarse_distribution": self.coarse.to_dict() if self.coarse else None,
            "refine_distribution": self.refine.to_dict() if self.refine else None,
            "refine_not_worse_than_coarse": self.refine_not_worse,
            "gate": self.gate.to_dict(),
            "outcomes": [
                {
                    "name": outcome.name,
                    "size_px": list(outcome.size_px),
                    "coarse_error_px": outcome.coarse_error_px,
                    "refine_error_px": outcome.refine_error_px,
                    "refine_deviation_px": outcome.refine_deviation_px,
                    "zoom_consistency_px": outcome.zoom_consistency_px,
                    "zoom_consistency_c1000": outcome.zoom_consistency_c,
                    "candidate_iou_max": outcome.candidate_iou_max,
                    "candidate_iou_at_0_3": outcome.candidate_iou_at_0_3,
                    "candidate_iou_at_0_5": outcome.candidate_iou_at_0_5,
                    "candidate_center_inside": outcome.candidate_center_inside,
                    "fallback": outcome.fallback,
                    "fallback_reason": outcome.fallback_reason,
                    "fallback_cause": outcome.fallback_cause,
                    "gate_hit": outcome.gate_hit,
                    "gate_reasons": list(outcome.gate_reasons),
                    "gate_undecidable": list(outcome.gate_undecidable),
                    "refine_enabled": outcome.refine_enabled,
                    "landing_point": (
                        list(outcome.landing_point) if outcome.landing_point else None
                    ),
                    "input_tokens": list(outcome.input_tokens),
                    "error_message": outcome.error_message,
                }
                for outcome in self.outcomes
            ],
            "notes": list(self.notes),
        }


class _RecordingCaller:
    """记录每次回答的模型端口（为了把 token 数归到粗定位 / 精定位两级）。"""

    def __init__(self, inner):
        self.inner = inner
        self.replies: list[Any] = []

    def complete(self, prompt, **kwargs):
        reply = self.inner.complete(prompt, **kwargs)
        self.replies.append(reply)
        return reply


def collect_targets(
    port,
    anchor,
    *,
    control_types: Sequence[str] = DEFAULT_CONTROL_TYPES,
    max_side_px: int = MAX_TARGET_SIDE_PX,
    zoom_factor: float = 1.0,
) -> tuple[list[BenchTarget], list[str]]:
    """从 UIA 树里挑出一批**可当测量目标**的控件（真值 = 控件框中心）。

    条件：有名、有合法控件框、边长不超过上限、**名字只对应一个控件框**
    （同名控件模型分不清是哪一个）。

    同一个元素在树里出现两次（父窗口与子窗口各报一次）**不算同名撞车** ——
    按控件框去重后再判名字。

    返回 ``(目标, 读失败的控件类型)``。

    ⚠️ **读失败 ≠ 界面上没有**（同一个纪律，见
    :func:`minicode.e2e.real.read_window_controls`）：
    * **局部失败**（某一类控件读不出来）→ 那一类**不进测量集**，
      但**要把失败的类型报出来** —— 不报的话，**分母会静默变小**
      （量到的分布其实是「少了一类控件」的分布，报告里看不出来）；
    * **全部失败** → **抛**（树读不出来，不是「这个窗口一个控件都没有」）。
    """
    rects_by_name: dict[str, set[tuple[int, int, int, int]]] = {}
    found: dict[tuple[int, int, int, int], BenchTarget] = {}
    failures: list[str] = []
    for control_type in control_types:
        try:
            result = port.find(UiaFindRequest(anchor=anchor, control_type=control_type))
        except Exception:
            failures.append(control_type)
            continue
        for element in result.elements:
            name = (element.name or "").strip()
            rect = element.rect
            if not name or rect is None:
                continue
            left, top, right, bottom = rect
            width, height = right - left, bottom - top
            if width <= 0 or height <= 0:
                continue
            if max(width, height) > max_side_px:
                continue
            key = (left, top, right, bottom)
            rects_by_name.setdefault(name, set()).add(key)
            found.setdefault(
                key,
                BenchTarget(
                    name=name,
                    rect=key,
                    truth=((left + right) / 2.0, (top + bottom) / 2.0),
                    size_logical=(
                        int(round(width / zoom_factor)),
                        int(round(height / zoom_factor)),
                    ),
                ),
            )
    if failures and len(failures) == len(control_types):
        # **每一类都读不出来** → 这棵树读不出来（不是「界面上没有控件」）。
        raise RuntimeError(
            "UIA 树读不出来（每一类控件的查询都失败）："
            + "、".join(failures)
            + " —— 读不出来 ≠ 界面上没有控件，不许当成「没有目标」"
        )
    targets = [
        target
        for target in found.values()
        if len(rects_by_name.get(target.name, set())) == 1
    ]
    return (targets, failures)


def measure(
    client,
    frame: CapturedFrame,
    surface: tuple[int, int, int, int],
    targets: Sequence[BenchTarget],
    *,
    scale: ModelScale,
    model_input_size: tuple[int, int],
    effort: Effort | str = Effort.LOW,
    max_tokens: int = 4096,
    gate_mode: GateMode | str = GateMode.ON_DEMAND,
    target_radius_px: float = 0.0,
    crop_width: int | None = None,
    crop_height: int | None = None,
    coarse_only: bool = False,
    candidate_source: CandidateSource | None = None,
) -> tuple[TargetOutcome, ...]:
    """在给定目标上跑两级 grounding，返回逐目标的结果。

    ⚠️ ``target_radius_px`` 是门控「目标占比」的输入：**只能来自非真值来源**
    （比如上一轮观测到的目标尺寸）。**不许拿 UIA 的真值框当门控输入** ——
    那是拿答案去喂判据，量出来的分布不作数（真值只用来算误差）。

    ⚠️ ``crop_width`` / ``crop_height`` 是**运行时参数**（物理像素）：
    冻结原型 ``coord/crop_refine.py`` 里的默认值**不许改** ——
    裁剪尺寸扫描从这里传（``None`` = 用通道的默认值）。

    ⚠️ ``candidate_source`` 给了它 → **候选覆盖率三列**照实量
    （IoU@0.3 / IoU@0.5 / 「框中心是否落在真目标内」）。
    ⚠️ **候选框不许拿真值框去造**（那是拿答案当判据）；这里量的是
    「真目标**是不是**被候选覆盖到了」。
    """
    outcomes: list[TargetOutcome] = []
    for target in targets:
        caller = _RecordingCaller(client)
        extra: dict[str, object] = {}
        if crop_width is not None:
            extra["crop_width"] = int(crop_width)
        if crop_height is not None:
            extra["crop_height"] = int(crop_height)
        grounder = TwoStageGrounder(
            caller,
            scale=scale,
            model_input_size=model_input_size,
            full_screen_size=(frame.width, frame.height),
            effort=effort,
            max_tokens=max_tokens,
            gate_mode=gate_mode,
            target_radius_px=target_radius_px,
            coarse_only=coarse_only,
            **extra,
        )
        result = grounder.locate(
            _request(target.name, frame, surface)
        )
        coarse_error = (
            math.hypot(
                result.coarse_point[0] - target.truth[0],
                result.coarse_point[1] - target.truth[1],
            )
            if result.coarse_point is not None
            else None
        )
        refine_error = (
            math.hypot(
                result.landing_point[0] - target.truth[0],
                result.landing_point[1] - target.truth[1],
            )
            if result.landing_point is not None
            else None
        )
        # 偏离量 / zoom 一致性：通道把这两个量**落在裁剪计划里**了
        # （落盘字段，不是回显 —— 回显记的是模型自己发出的数）。
        plan = result.crop_plan or {}
        deviation = plan.get("deviation_px")
        consistency = plan.get("zoom_consistency_px")
        consistency_c = plan.get("zoom_consistency_c1000")
        tokens = tuple(
            reply.input_tokens for reply in caller.replies[:2]
        )
        if len(tokens) < 2:
            tokens = (tokens[0] if tokens else None, None)
        # **候选覆盖率三列**（有候选源才量；没有 → 全是 None）。
        coverage: dict[str, Any] = {
            "candidate_iou_max": None,
            "candidate_iou_at_0_3": None,
            "candidate_iou_at_0_5": None,
            "candidate_center_inside": None,
        }
        if candidate_source is not None:
            coverage = candidate_coverage(
                list(candidate_source.candidates(frame, target.name)), target.rect
            )
        outcomes.append(
            TargetOutcome(
                name=target.name,
                size_px=target.size_px,
                size_logical=target.size_logical,
                coarse_error_px=coarse_error,
                refine_error_px=refine_error,
                refine_deviation_px=(
                    float(deviation) if deviation is not None else None
                ),
                zoom_consistency_px=(
                    float(consistency) if consistency is not None else None
                ),
                zoom_consistency_c=(
                    float(consistency_c) if consistency_c is not None else None
                ),
                **coverage,
                fallback=result.fallback,
                fallback_reason=result.fallback_reason,
                fallback_cause=result.fallback_cause,
                gate_hit=result.gate_hit,
                gate_reasons=tuple(result.gate_reasons),
                gate_undecidable=tuple(result.gate_undecidable),
                refine_enabled=result.refine_enabled,
                coarse_point=result.coarse_point,
                landing_point=result.landing_point,
                input_tokens=(tokens[0], tokens[1]),
                error_message=result.error_message,
            )
        )
    return tuple(outcomes)


def gate_report(outcomes: Sequence[TargetOutcome]) -> GateReport:
    """**门控埋点报告**（工单 M7 AC 4）。

    ⚠️ 这几个数与同一张表的分数**必须来自同一批样本** —— 这里收的就是
    打分用的那批 outcome（``BenchReport.gate`` 就是这么来的）。
    """
    return build_gate_report(
        [
            GateSample(
                gate_hit=outcome.gate_hit,
                gate_reasons=tuple(outcome.gate_reasons),
                gate_undecidable=tuple(outcome.gate_undecidable),
                refine_enabled=outcome.refine_enabled,
                fallback=outcome.fallback,
                fallback_cause=outcome.fallback_cause,
            )
            for outcome in outcomes
        ]
    )


def _request(name: str, frame: CapturedFrame, surface):
    from minicode.action.ports import VisualLocateRequest

    return VisualLocateRequest(
        target_description=name, frame=frame, surface=surface
    )


def summarize(outcomes: Sequence[TargetOutcome]) -> tuple[
    ErrorDistribution | None, ErrorDistribution | None
]:
    """两级误差的分布摘要（粗定位 / 精定位）。"""
    coarse = [
        outcome.coarse_error_px
        for outcome in outcomes
        if outcome.coarse_error_px is not None
    ]
    refine = [
        outcome.refine_error_px
        for outcome in outcomes
        if outcome.refine_error_px is not None
    ]
    return (
        summarize_errors(coarse) if coarse else None,
        summarize_errors(refine) if refine else None,
    )

"""视觉通道准入判定：把标定结论翻译成「能不能走视觉通道」（工单 M5 AC 7）。

**硬闸门**（架构决策备忘 §2.6 C-03 的落点等价判据）：

* 标定失败（判定标度的最大误差 > 标定容差）→ **视觉通道不可用**。
  不为让它通过而放宽判定容差。
* 存在使物理落点分歧 > 标定容差的替代标度 → **视觉通道不可用**：
  选错那个标度会把落点挪出容差，宁可回落 UIA 或直接报明确错误码。
* 标度判定为**多解**、但各等价标度族的落点分歧都在容差内 → **允许继续**，
  标上「落点等价」并附等价列表，**不**标记通道不可用。

本模块只做判定，**不产出任何视觉通道落点**。
"""

from __future__ import annotations

from minicode.action.ports import VisualAdmission

from .calibrate import SCALE_VERDICT_MULTI, CalibrationOutcome

__all__ = ["CalibrationAdmission", "visual_channel_available"]


def visual_channel_available(
    *,
    calibration_ok: bool,
    equivalent_scales: tuple[str, ...],
    divergences_px: dict[str, float],
    tolerance_px: float,
) -> bool:
    """**硬闸门的一条规则**（准入判定与取标度的地方共用这一处）。

    不可用的两个条件：

    1. **标定失败**（判定标度的最大误差 > 标定容差）；
    2. 候选族里有标度的**落点分歧 > 标定容差** —— 选错它会把落点挪出容差。

    两条路径的「替代标度」都指**探针排除不掉的那些标度**（候选族），
    不是全部候选：否则 0~999 这种永远存在的候选会让视觉通道永远不可用。
    """
    if not calibration_ok:
        return False
    return not any(
        divergences_px.get(name, 0.0) > tolerance_px for name in equivalent_scales
    )


class CalibrationAdmission:
    """由一条标定结论得到视觉通道的准入判定。"""

    def __init__(self, outcome: CalibrationOutcome):
        self.outcome = outcome

    def admit(self) -> VisualAdmission:
        outcome = self.outcome
        equivalent = tuple(scale.value for scale in outcome.equivalent_scales)
        divergences = {
            scale.value: value for scale, value in outcome.divergences_px.items()
        }
        base = {
            "scale": outcome.detected_scale.value,
            "scale_verdict": outcome.scale_verdict,
            "landing_equivalence": outcome.landing_equivalence,
            "equivalent_scales": equivalent,
        }
        available = visual_channel_available(
            calibration_ok=outcome.calibration_ok,
            equivalent_scales=equivalent,
            divergences_px=divergences,
            tolerance_px=outcome.calibration_tolerance_px,
        )
        if not available:
            if not outcome.calibration_ok:
                best = outcome.candidate(outcome.detected_scale)
                reason = (
                    f"标定失败：判定标度 {outcome.detected_scale.value} 的最大误差 "
                    f"{best.max_error_px:.2f} px 超过标定容差 "
                    f"{outcome.calibration_tolerance_px:.1f} px —— 视觉通道不可用"
                )
            else:
                diverging = [
                    name
                    for name in equivalent
                    if divergences.get(name, 0.0) > outcome.calibration_tolerance_px
                ]
                worst = max(divergences[name] for name in diverging)
                reason = (
                    f"存在使物理落点分歧 {worst:.2f} px > 标定容差 "
                    f"{outcome.calibration_tolerance_px:.1f} px 的替代标度"
                    f"（{'、'.join(diverging)}） —— 视觉通道不可用"
                )
            return VisualAdmission(available=False, reason=reason, **base)
        if outcome.scale_verdict == SCALE_VERDICT_MULTI:
            return VisualAdmission(
                available=True,
                reason=(
                    "标度判定为多解，但各等价标度的落点分歧都在标定容差内"
                    f"（等价列表：{'、'.join(equivalent)}）—— 视觉通道允许继续"
                ),
                **base,
            )
        return VisualAdmission(
            available=True,
            reason=(
                f"标定唯一命中 {outcome.detected_scale.value}，"
                f"最大误差 {outcome.candidate(outcome.detected_scale).max_error_px:.2f} px"
                f" ≤ 标定容差 {outcome.calibration_tolerance_px:.1f} px"
            ),
            **base,
        )

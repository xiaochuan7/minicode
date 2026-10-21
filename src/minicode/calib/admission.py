"""视觉通道准入判定：把标定结论翻译成「能不能走视觉通道」（工单 M5 AC 7 / A-1）。

**硬闸门**（A-1 的口径：1 px 归位）：

* **落点等价族分歧 > 落点等价容差（4 px）** → **视觉通道不可用**：
  选错那个标度会把落点挪出可接受范围，宁可回落 UIA 或直接报明确错误码。
* **K 轮命中率 < 命中率下限（0.6）** → **视觉通道不可用**：
  模型的落点落不进目标的方块，通道没有用。

两条**同时**满足才放行。**1 px 的可分辨容差不参与这个闸门** ——
它管的是「两个候选标度能不能分开」（可分辨性），不是「落点准不准」。

标度判定为**多解**、但落点等价族的分歧都在容差内 → **允许继续**，
标上「落点等价」并附等价列表，**不**标记通道不可用。

本模块只做判定，**不产出任何视觉通道落点**。
"""

from __future__ import annotations

from minicode.action.ports import VisualAdmission

from .calibrate import SCALE_VERDICT_MULTI, CalibrationOutcome
from .tolerance import CALIBRATION_MIN_HIT_RATE

__all__ = [
    "CalibrationAdmission",
    "visual_channel_available",
    "visual_channel_reason",
]


def _family_divergence(
    equivalent_scales: tuple[str, ...], divergences_px: dict[str, float]
) -> tuple[float, str]:
    """落点等价族里的**最大分歧**（物理像素）与达成它的那个标度。"""
    worst = 0.0
    worst_name = ""
    for name in equivalent_scales:
        value = divergences_px.get(name, 0.0)
        if value > worst:
            worst = value
            worst_name = name
    return worst, worst_name


def visual_channel_available(
    *,
    equivalent_scales: tuple[str, ...],
    divergences_px: dict[str, float],
    equivalence_tolerance_px: float,
    hit_rate: float,
    min_hit_rate: float,
) -> bool:
    """**硬闸门的一条规则**（准入判定与取标度的地方共用这一处）。

    放行条件（**两条都要满足**）：

    1. 落点等价族里最大的**物理落点分歧** ≤ 落点等价容差（4 px）；
    2. K 轮**命中率** ≥ 命中率下限（0.6）。

    两条路径的「替代标度」都指**落点等价族**（探针排除不掉、且落点分歧
    在容差内的那些标度），不是全部候选：否则 0~999 这种永远存在的候选
    会让视觉通道永远不可用。
    """
    worst, _ = _family_divergence(equivalent_scales, divergences_px)
    if worst > equivalence_tolerance_px:
        return False
    return hit_rate >= min_hit_rate


def visual_channel_reason(
    *,
    equivalent_scales: tuple[str, ...],
    divergences_px: dict[str, float],
    equivalence_tolerance_px: float,
    hit_rate: float,
    min_hit_rate: float,
    rounds: int = 0,
) -> str:
    """人可读的理由（放行与拒绝都给）。

    例：「候选标度分歧 2.87 px ≤ 4 px，但 5 轮命中率 0.4 < 0.6」。
    """
    worst, worst_name = _family_divergence(equivalent_scales, divergences_px)
    rounds_text = f"{rounds} 轮" if rounds else "本轮"
    if worst > equivalence_tolerance_px:
        return (
            f"存在使物理落点分歧 {worst:.2f} px > 落点等价容差 "
            f"{equivalence_tolerance_px:.1f} px 的替代标度（{worst_name}）"
            f" —— 视觉通道不可用"
        )
    if hit_rate < min_hit_rate:
        return (
            f"候选标度分歧 {worst:.2f} px ≤ {equivalence_tolerance_px:.1f} px，"
            f"但 {rounds_text}命中率 {hit_rate:.2f} < {min_hit_rate:.2f}"
            f" —— 视觉通道不可用"
        )
    return (
        f"落点等价族最大分歧 {worst:.2f} px ≤ {equivalence_tolerance_px:.1f} px"
        f"｜{rounds_text}命中率 {hit_rate:.2f} ≥ {min_hit_rate:.2f}"
        f" —— 视觉通道可用"
    )


class CalibrationAdmission:
    """由一条标定结论得到视觉通道的准入判定。"""

    def __init__(
        self,
        outcome: CalibrationOutcome,
        *,
        min_hit_rate: float = CALIBRATION_MIN_HIT_RATE,
    ):
        self.outcome = outcome
        self.min_hit_rate = float(min_hit_rate)

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
            equivalent_scales=equivalent,
            divergences_px=divergences,
            equivalence_tolerance_px=outcome.equivalence_tolerance_px,
            hit_rate=outcome.hit_rate,
            min_hit_rate=self.min_hit_rate,
        )
        reason = visual_channel_reason(
            equivalent_scales=equivalent,
            divergences_px=divergences,
            equivalence_tolerance_px=outcome.equivalence_tolerance_px,
            hit_rate=outcome.hit_rate,
            min_hit_rate=self.min_hit_rate,
            rounds=outcome.rounds,
        )
        if not available:
            return VisualAdmission(available=False, reason=reason, **base)
        if outcome.scale_verdict == SCALE_VERDICT_MULTI:
            return VisualAdmission(
                available=True,
                reason=(
                    f"{reason}（标度判定为多解，但落点等价族的分歧都在容差内："
                    f"等价列表 {'、'.join(equivalent)}）"
                ),
                **base,
            )
        return VisualAdmission(
            available=True,
            reason=f"{reason}（标度判定唯一：{outcome.detected_scale.value}）",
            **base,
        )

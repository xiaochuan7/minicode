"""视觉通道准入判定：把标定结论翻译成「能不能走视觉通道」（工单 M5 AC 7 / A-1）。

**硬闸门**（A-1 的口径：1 px 归位）：

* **落点等价族分歧 > 落点等价容差（4 px）** → **视觉通道不可用**：
  选错那个标度会把落点挪出可接受范围，宁可回落 UIA 或直接报明确错误码。
* **K 轮命中率 < 命中率下限（0.6）** → **视觉通道不可用**：
  模型的落点落不进目标的方块，通道没有用。

两条**同时**满足才放行。**1 px 的可分辨容差不参与这个闸门** ——
它管的是「两个候选标度能不能分开」（可分辨性），不是「落点准不准」。

⚠️ **第一条按定义恒成立**（A-1 归位后的**结构事实**，**不是**放松判据）：
落点等价族**就是**「分歧 ≤ 4 px 的族」→ 族内最大分歧按定义 ≤ 4 px
→ 标定结论里的 ``calibration_ok`` / ``landing_equivalence`` **恒为真**
（见 :mod:`minicode.calib.calibrate` 的说明）。
**真正起闸门作用的是命中率**（方案原话：**视觉通道由命中率决定**）：
模型答得准不准，全看 K 轮里有多少个预测落进了目标的方块。

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
    median_error_px: float | None = None,
    hit_radius_px: float | None = None,
) -> bool:
    """**硬闸门的一条规则**（准入判定与取标度的地方共用这一处）。

    放行条件（**两条都要满足**）：

    1. 落点等价族里最大的**物理落点分歧** ≤ 落点等价容差（4 px）；
       ⚠️ 这一条**按定义恒成立**（族就是按 4 px 定义的）→ **它不是闸门**；
    2. K 轮**命中率** ≥ 命中率下限（0.6）—— **这条才是真正的闸门**
       （方案原话：视觉通道由命中率决定）。

    两条路径的「替代标度」都指**落点等价族**（探针排除不掉、且落点分歧
    在容差内的那些标度），不是全部候选：否则 0~999 这种永远存在的候选
    会让视觉通道永远不可用。

    ⚠️ **闸门用的落点误差统计是**百分位（中位），**不是 max**
    （:paramref:`median_error_px` / 命中半径 :paramref:`hit_radius_px`）：
    **max 口径下，一个野答（几十~几百 px 的错答）就能把整轮标定判死** ——
    那就是 M15 记的「标定是抽签」（1.06 / 2.66 / 68.00 / 68.81 px）。
    这一条**不收紧也不放宽**闸门：命中率 ≥ 0.6 意味着**至少 60% 的预测
    落在方块内** → **中位必然也在方块内**（它是命中率那条的**蕴含**）；
    它只是把闸门用的误差统计**钉在中位口径上**，不再留 max 口径的口子。
    """
    worst, _ = _family_divergence(equivalent_scales, divergences_px)
    if worst > equivalence_tolerance_px:
        return False
    if hit_rate < min_hit_rate:
        return False
    if (
        median_error_px is not None
        and hit_radius_px is not None
        and median_error_px > hit_radius_px
    ):
        return False
    return True


def visual_channel_reason(
    *,
    equivalent_scales: tuple[str, ...],
    divergences_px: dict[str, float],
    equivalence_tolerance_px: float,
    hit_rate: float,
    min_hit_rate: float,
    rounds: int = 0,
    median_error_px: float | None = None,
    p90_error_px: float | None = None,
    hit_radius_px: float | None = None,
) -> str:
    """人可读的理由（放行与拒绝都给）。

    例：「候选标度分歧 2.87 px ≤ 4 px，但 21 轮命中率 0.4 < 0.6」。

    ⚠️ **落点误差报的是中位 / p90（稳健统计），不是 max** —— 报 max 会让人
    以为「一个野答就判死」，实际判的是中位口径。
    """
    worst, worst_name = _family_divergence(equivalent_scales, divergences_px)
    rounds_text = f"{rounds} 轮" if rounds else "本轮"
    error_text = ""
    if median_error_px is not None:
        error_text = f"｜落点误差 中位 {median_error_px:.2f} px"
        if p90_error_px is not None:
            error_text += f" / p90 {p90_error_px:.2f} px"
        if hit_radius_px is not None:
            error_text += f"（命中半径 {hit_radius_px:.0f} px）"
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
            f"{error_text}"
            f" —— 视觉通道不可用"
        )
    return (
        f"落点等价族最大分歧 {worst:.2f} px ≤ {equivalence_tolerance_px:.1f} px"
        f"｜{rounds_text}命中率 {hit_rate:.2f} ≥ {min_hit_rate:.2f}"
        f"{error_text}"
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
            median_error_px=outcome.median_error_px,
            hit_radius_px=outcome.hit_radius_px or None,
        )
        reason = visual_channel_reason(
            equivalent_scales=equivalent,
            divergences_px=divergences,
            equivalence_tolerance_px=outcome.equivalence_tolerance_px,
            hit_rate=outcome.hit_rate,
            min_hit_rate=self.min_hit_rate,
            rounds=outcome.rounds,
            median_error_px=outcome.median_error_px,
            p90_error_px=outcome.p90_error_px,
            hit_radius_px=outcome.hit_radius_px or None,
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

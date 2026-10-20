"""落点误差的**分布**统计（工单 M6 AC 9）。

单次数字不能当验收线 —— 本机同一目标的单次粗定位误差在 3.4~7.6 px 之间浮动。
验收判据用**一组目标的分布**：

* **中位数** —— 分布的中心；
* **p90** —— 分布的尾部（九成的样本比它好）；
* **最大值** —— 最坏情况（只报告，不作为判据：单点抖动不该判不合格）。

「精定位那一级的分布不劣于粗定位」= **中位数与 p90 都不劣**。
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

__all__ = ["ErrorDistribution", "summarize_errors", "not_worse_than"]


@dataclass(frozen=True)
class ErrorDistribution:
    """一组落点误差的分布摘要（单位：物理像素）。"""

    count: int
    minimum: float
    median: float
    p90: float
    maximum: float

    def to_dict(self) -> dict[str, float | int]:
        return {
            "count": self.count,
            "min": self.minimum,
            "median": self.median,
            "p90": self.p90,
            "max": self.maximum,
        }


def _percentile(sorted_values: Sequence[float], fraction: float) -> float:
    """最近秩法取百分位（``fraction`` ∈ (0, 1]）。"""
    if not sorted_values:
        raise ValueError("空样本没有百分位")
    rank = max(1, math.ceil(fraction * len(sorted_values)))
    return float(sorted_values[min(rank, len(sorted_values)) - 1])


def summarize_errors(errors: Sequence[float]) -> ErrorDistribution:
    """一组误差 → 分布摘要。"""
    values = sorted(float(value) for value in errors)
    if not values:
        raise ValueError("没有样本就没有分布")
    middle = len(values) // 2
    if len(values) % 2:
        median = values[middle]
    else:
        median = (values[middle - 1] + values[middle]) / 2.0
    return ErrorDistribution(
        count=len(values),
        minimum=values[0],
        median=median,
        p90=_percentile(values, 0.9),
        maximum=values[-1],
    )


def not_worse_than(fine: ErrorDistribution, coarse: ErrorDistribution) -> bool:
    """精定位那一级的分布**不劣于**粗定位（中位数与 p90 都不劣）。"""
    return fine.median <= coarse.median and fine.p90 <= coarse.p90

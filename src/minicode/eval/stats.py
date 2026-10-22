"""**评测统计模块**（施工清单 ⑨）—— 四个纯函数，零依赖、可单测。

为什么要有这个模块：评测台原来的数**没有区间、没有检验、没有配对**——
「两半各跑一遍再比」把**构成差**当成了效应（见
``docs/research/vision-root-cause.md`` 的「为什么必须换设计」）。
本模块只提供**算术**，不做任何判断，也不碰任何判定口径：

============================  ============================================
函数                          语义
============================  ============================================
:func:`mcnemar_midp`          **mid-p McNemar**（双侧）的 p 值
:func:`wilson_ci`             单臂命中率的 **Wilson 95% 区间**
:func:`paired_bootstrap_delta`  **配对 bootstrap** 的 Δ 与 95% CI
:func:`mde`                  **最小可检测效应**（百分点）
============================  ============================================

⚠️ **本模块不引入第三方依赖**（仓库的 ``core`` 路径要求零第三方依赖）——
二项分布用**整数二项式系数**精确算，bootstrap 用标准库 ``random``。
"""

from __future__ import annotations

import math
import random
from collections.abc import Sequence
from typing import NamedTuple

__all__ = [
    "BootstrapDelta",
    "mcnemar_midp",
    "mde",
    "paired_bootstrap_delta",
    "wilson_ci",
]

#: **95% 双侧 z 值**（正态分位数，标准值）。
Z95 = 1.959963984540054

#: **配对近似 MDE 的常数**：``N ≈ (z_{1-α/2} + z_{1-β})² · π_d / Δ²``，
#: 其中 ``(z_{1-α/2} + z_{1-β})² = (1.96 + 0.84)² ≈ 7.849``（α=0.05、power=0.80）。
MDE_CONSTANT = 7.849

#: **配对 bootstrap 的默认重抽次数**。
BOOTSTRAP_ITERATIONS = 2000

#: **配对 bootstrap 的默认种子**（固定 → 可复现）。
BOOTSTRAP_SEED = 20251020


def _binom_counts(n: int, upto: int) -> list[int]:
    """``C(n, 0) … C(n, upto)``（**整数**，精确；不经过浮点）。

    用递推 ``C(n,k+1) = C(n,k)·(n-k)/(k+1)`` —— 全部整除，无舍入。
    """
    counts = [1]
    for k in range(upto):
        counts.append(counts[-1] * (n - k) // (k + 1))
    return counts


def mcnemar_midp(b: int, c: int) -> float:
    """**mid-p McNemar 检验**（双侧）的 p 值。

    ``b`` / ``c`` 是 2×2 表里两个**不一致格**的计数
    （``b`` = 只在 A 臂命中，``c`` = 只在 B 臂命中）。
    零假设下 ``X ~ Binomial(n = b + c, p = 0.5)``。

    **mid-p 与普通 exact 的差别就在边界那一半**：
    普通 exact 的双侧 p = ``2 · P(X ≤ min(b,c))``；
    mid-p 把**观测值那一格的概率质量折半**，即

    ``p_mid = min(1, 2 · min(P(X < min(b,c)) + ½·P(X = min(b,c)),
    P(X > max(b,c)) + ½·P(X = max(b,c))))``

    （二项分布在 ``n = b+c`` 下关于 ``n/2`` 对称 → 两侧尾部相等，
    实现里只算一侧。）

    * ``b = c = 0``（没有不一致对）→ 返回 ``1.0``；
    * 结果夹在 ``[0, 1]``。
    """
    if b < 0 or c < 0:
        raise ValueError("b 与 c 必须是非负计数")
    n = b + c
    if n == 0:
        return 1.0
    m = min(b, c)
    counts = _binom_counts(n, m)
    #: ``2 · Σ_{k<m} C(n,k) + C(n,m)`` —— 边界格只算**一半**。
    numerator = 2 * sum(counts[:m]) + counts[m]
    return min(1.0, numerator / (2**n))


def wilson_ci(
    successes: int, n: int, z: float = Z95
) -> tuple[float, float]:
    """命中率的 **Wilson 95% 区间**（单臂）。

    * ``successes`` —— 命中条数，``n`` —— 样本条数；
    * ``n = 0`` → 返回 ``(0.0, 1.0)``（没有样本 → 区间是整个 ``[0,1]``）；
    * 返回 ``(low, high)``，两端夹在 ``[0, 1]``。

    Wilson 区间在小样本 / 命中率贴近 0 或 1 时**仍然有效**（不像正态近似
    会越界）—— 单臂命中率用它报。
    """
    if n < 0:
        raise ValueError("n 必须是非负整数")
    if not 0 <= successes <= n:
        raise ValueError("successes 必须在 0..n 之间")
    if n == 0:
        return (0.0, 1.0)
    p = successes / n
    denominator = 1.0 + z * z / n
    center = (p + z * z / (2 * n)) / denominator
    half = (z / denominator) * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return (max(0.0, center - half), min(1.0, center + half))


class BootstrapDelta(NamedTuple):
    """配对 bootstrap 的结果：``Δ``（百分点）与它的 95% 区间。"""

    #: Δ = B 臂命中率 − A 臂命中率（**比例**，不是百分点）。
    delta: float
    #: 95% CI 的下界（比例）。
    ci_low: float
    #: 95% CI 的上界（比例）。
    ci_high: float


def paired_bootstrap_delta(
    pairs: Sequence[tuple[bool, bool]],
    *,
    iterations: int = BOOTSTRAP_ITERATIONS,
    seed: int = BOOTSTRAP_SEED,
) -> BootstrapDelta:
    """**配对 bootstrap**：Δ 与它的 95% 区间。

    ``pairs`` 是**逐条**的 ``(arm_a_hit, arm_b_hit)`` 布尔对 ——
    **每条样本是一个聚类单元**（按 task 聚类：同一条样本的两臂不可拆开）。
    Δ = B 臂命中率 − A 臂命中率。

    重抽：对**样本**（不是对臂）有放回地重抽 ``iterations`` 次，
    每次算一遍 Δ → 取 2.5% / 97.5% 分位（线性插值）。
    固定 ``seed`` → **可复现**（同一个 seed 永远给同一个区间）。
    """
    if iterations <= 0:
        raise ValueError("iterations 必须为正")
    count = len(pairs)
    if count == 0:
        return BootstrapDelta(delta=0.0, ci_low=0.0, ci_high=0.0)
    a_hits = [1 if pair[0] else 0 for pair in pairs]
    b_hits = [1 if pair[1] else 0 for pair in pairs]
    delta = (sum(b_hits) - sum(a_hits)) / count
    rng = random.Random(seed)
    draws: list[float] = []
    for _ in range(iterations):
        total = 0
        for _ in range(count):
            index = rng.randrange(count)
            total += b_hits[index] - a_hits[index]
        draws.append(total / count)
    draws.sort()
    return BootstrapDelta(
        delta=delta,
        ci_low=_percentile(draws, 0.025),
        ci_high=_percentile(draws, 0.975),
    )


def _percentile(sorted_values: Sequence[float], q: float) -> float:
    """分位数（线性插值，与常见实现的默认口径一致）。"""
    if not sorted_values:
        return 0.0
    if len(sorted_values) == 1:
        return sorted_values[0]
    position = q * (len(sorted_values) - 1)
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return sorted_values[lower]
    weight = position - lower
    return sorted_values[lower] * (1 - weight) + sorted_values[upper] * weight


def mde(n: int, discordance: float, delta: float | None = None) -> float:
    """**最小可检测效应**（**百分点**）。

    配对近似 ``N ≈ 7.849 · π_d / Δ²`` 反解出 Δ：

    ``mde = sqrt(7.849 · π_d / n) · 100``（返回值是**百分点**）。

    * ``n`` —— 样本量（配对样本数）；
    * ``discordance`` —— 不一致率 ``π_d``（0~1）；
    * ``delta`` —— **可选**：目标效应（百分点）。它**不改变**返回值；
      它的用处是让调用点自解释 —— 把 ``delta`` 与返回的 MDE 比：
      ``delta ≥ MDE`` → 这个样本量**够**判出它；``delta < MDE`` → 不够
      （要多少条看 ``N = 7.849 · π_d / Δ²``）。
    """
    if n <= 0:
        raise ValueError("n 必须为正")
    if not 0.0 <= discordance <= 1.0:
        raise ValueError("discordance 必须在 0..1 之间")
    if delta is not None and delta <= 0:
        raise ValueError("delta 必须是正的百分点")
    return math.sqrt(MDE_CONSTANT * discordance / n) * 100

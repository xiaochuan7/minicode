"""**统计模块**的单测。

钉死四件事：

* **mid-p McNemar** 在**手算样例**上的值正确，且与**普通 exact** 的关系
  符合定义（mid-p 恒 ≤ exact，差 = 观测格那一格的概率质量）；
* **Wilson 区间**在小样本 / 边界命中率上不越界，且与手算值一致；
* **配对 bootstrap** 的 Δ 就是「B 命中率 − A 命中率」，区间**可复现**
  （同 seed 同一个结果）；
* **MDE** 与公式 ``sqrt(7.849·π_d/n)·100`` 一致（183 条、π_d=0.10 → 6.55 pp）。

⚠️ **本机没有 scipy**（``ModuleNotFoundError: No module named 'scipy'``）→
「普通 exact」在这里用**手算二项分布**算（``math.comb`` 精确整数），
这正是 ``scipy.stats.binomtest`` 双侧分支的定义：
``p = min(1, 2 · Σ_{k≤min(b,c)} C(n,k) / 2^n)``。
"""

from __future__ import annotations

import math
import unittest

from minicode.eval.stats import (
    mcnemar_chisq_cc,
    mcnemar_exact,
    mcnemar_midp,
    mde,
    paired_bootstrap_delta,
    wilson_ci,
)


def exact_two_sided(b: int, c: int) -> float:
    """**普通 exact**（= ``scipy.stats.binomtest`` 的双侧分支）：手算二项分布。"""
    n = b + c
    if n == 0:
        return 1.0
    m = min(b, c)
    tail = sum(math.comb(n, k) for k in range(m + 1))
    return min(1.0, 2 * tail / (2**n))


def mid_p_hand(b: int, c: int) -> float:
    """**mid-p** 的手算值（边界格折半）：``(2·Σ_{k<m} C(n,k) + C(n,m)) / 2^n``。"""
    n = b + c
    if n == 0:
        return 1.0
    m = min(b, c)
    numerator = 2 * sum(math.comb(n, k) for k in range(m)) + math.comb(n, m)
    return min(1.0, numerator / (2**n))


class McNemarMidPTests(unittest.TestCase):
    """mid-p McNemar：手算样例 + 与普通 exact 的关系。"""

    def test_hand_computed_examples(self):
        """三个手算样例（分母都是 2^n，整数精确）。"""
        # (b, c) = (16, 4)：n = 20，m = 4
        #   Σ_{k<4} C(20,k) = 1 + 20 + 190 + 1140 = 1351，C(20,4) = 4845
        #   mid-p = (2·1351 + 4845) / 2^20 = 7547 / 1048576
        self.assertAlmostEqual(mcnemar_midp(16, 4), 7547 / 1048576, places=12)
        # (b, c) = (1, 0)：n = 1 → mid-p = (2·0 + C(1,0)) / 2^1 = 0.5
        self.assertAlmostEqual(mcnemar_midp(1, 0), 0.5, places=12)
        # (b, c) = (0, 3)：n = 3 → mid-p = C(3,0) / 2^3 = 0.125
        self.assertAlmostEqual(mcnemar_midp(0, 3), 0.125, places=12)

    def test_symmetric_case_gives_one(self):
        """b = c → 两侧尾部相等 → p = 1.0（没有方向性证据）。"""
        self.assertAlmostEqual(mcnemar_midp(7, 7), 1.0, places=12)
        self.assertAlmostEqual(mcnemar_midp(30, 30), 1.0, places=12)

    def test_no_discordant_pair_gives_one(self):
        """没有不一致对 → p = 1.0。"""
        self.assertEqual(mcnemar_midp(0, 0), 1.0)

    def test_matches_the_hand_computed_mid_p_on_a_grid(self):
        """一批 (b, c) 上与手算 mid-p 完全一致。"""
        for b, c in (
            (0, 0),
            (1, 0),
            (0, 1),
            (2, 5),
            (5, 2),
            (10, 10),
            (16, 4),
            (30, 12),
            (120, 60),
        ):
            self.assertAlmostEqual(
                mcnemar_midp(b, c), mid_p_hand(b, c), places=12, msg=f"({b},{c})"
            )

    def test_mid_p_is_never_larger_than_exact(self):
        """mid-p ≤ 普通 exact（差 = 观测格那一格的概率质量）。"""
        for b, c in ((1, 0), (0, 3), (5, 2), (16, 4), (30, 12), (60, 60)):
            self.assertLessEqual(mcnemar_midp(b, c), exact_two_sided(b, c) + 1e-12)

    def test_close_to_exact_on_the_hand_computed_example(self):
        """手算例 (16, 4)：mid-p 与 exact 的差 < 0.01。"""
        mid = mcnemar_midp(16, 4)
        exact = exact_two_sided(16, 4)
        self.assertAlmostEqual(mid, 7547 / 1048576, places=12)
        self.assertLess(abs(mid - exact), 0.01)
        # 差恰好是观测格那一格的概率质量：C(20,4)/2^20
        self.assertAlmostEqual(exact - mid, math.comb(20, 4) / 2**20, places=12)

    def test_exact_reference_values(self):
        """普通 exact 的手算值（对照用）。"""
        self.assertAlmostEqual(exact_two_sided(16, 4), 12392 / 1048576, places=12)
        self.assertAlmostEqual(exact_two_sided(1, 0), 1.0, places=12)
        self.assertAlmostEqual(exact_two_sided(0, 3), 0.25, places=12)


class McNemarExactTests(unittest.TestCase):
    """**普通精确二项**（保守对照）：手算 + 与 mid-p 的关系。

    ⚠️ **主报是 mid-p**；这个是**对照**（Fagerland 2013：普通 exact
    与渐近+连续性校正表现差）。
    """

    def test_hand_computed_examples(self):
        """三个手算样例（分母都是 2^n，整数精确）。"""
        # (b, c) = (16, 4)：n = 20，m = 4
        #   Σ_{k≤4} C(20,k) = 1 + 20 + 190 + 1140 + 4845 = 6196
        #   exact = 2 · 6196 / 2^20 = 12392 / 1048576
        self.assertAlmostEqual(mcnemar_exact(16, 4), 12392 / 1048576, places=12)
        # (b, c) = (1, 0)：n = 1 → 2 · C(1,0) / 2 = 1.0
        self.assertAlmostEqual(mcnemar_exact(1, 0), 1.0, places=12)
        # (b, c) = (0, 3)：n = 3 → 2 · 1 / 8 = 0.25
        self.assertAlmostEqual(mcnemar_exact(0, 3), 0.25, places=12)

    def test_no_discordant_pair_gives_one(self):
        """没有不一致对 → p = 1.0。"""
        self.assertEqual(mcnemar_exact(0, 0), 1.0)

    def test_exact_is_mid_p_plus_the_observed_cell_mass(self):
        """exact − mid-p = **观测格那一格的概率质量**（边界格折半的那一半）。"""
        for b, c in ((16, 4), (30, 12), (0, 3), (38, 11)):
            n = b + c
            m = min(b, c)
            self.assertAlmostEqual(
                mcnemar_exact(b, c) - mcnemar_midp(b, c),
                math.comb(n, m) / 2**n,
                places=12,
                msg=f"({b},{c})",
            )

    def test_negative_counts_are_rejected(self):
        with self.assertRaises(ValueError):
            mcnemar_exact(-1, 2)


class McNemarChisqCcTests(unittest.TestCase):
    """**χ² + 连续性校正**（保守对照）：公式 + 关系。"""

    def test_the_statistic_matches_the_formula(self):
        """χ² = ``(|b−c| − 1)² / (b+c)``；自由度 1 的上尾 = ``erfc(√(χ²/2))``。"""
        for b, c in ((38, 11), (16, 4), (30, 12), (60, 5)):
            n = b + c
            statistic = (abs(b - c) - 1) ** 2 / n
            self.assertAlmostEqual(
                mcnemar_chisq_cc(b, c),
                math.erfc(math.sqrt(statistic / 2)),
                places=12,
                msg=f"({b},{c})",
            )

    def test_no_discordant_pair_gives_one(self):
        """没有不一致对 → p = 1.0。"""
        self.assertEqual(mcnemar_chisq_cc(0, 0), 1.0)

    def test_the_result_is_a_probability(self):
        for b, c in ((0, 0), (1, 0), (38, 11), (100, 3)):
            value = mcnemar_chisq_cc(b, c)
            self.assertGreaterEqual(value, 0.0)
            self.assertLessEqual(value, 1.0)

    def test_negative_counts_are_rejected(self):
        with self.assertRaises(ValueError):
            mcnemar_chisq_cc(1, -2)

    def test_the_continuity_correction_makes_the_equal_case_less_than_one(self):
        """``b = c``（无方向性证据）时统计量**不为零** → p 略小于 1。

        ⚠️ 这是那条公式的**定义**（不是 bug）：报数时按原样报。
        """
        self.assertLess(mcnemar_chisq_cc(7, 7), 1.0)
        self.assertGreater(mcnemar_chisq_cc(7, 7), 0.5)


class McNemarThreeWayTests(unittest.TestCase):
    """**三个检验的关系**（mid-p 主报 + 两个保守对照）。"""

    def test_the_degenerate_case_agrees(self):
        """``b = c = 0`` → 三个都是 1.0。"""
        for function in (mcnemar_midp, mcnemar_exact, mcnemar_chisq_cc):
            self.assertEqual(function(0, 0), 1.0)

    def test_the_three_are_in_the_same_ballpark(self):
        """``b = 38, c = 11``：三者同一量级，且 exact/χ² ≥ mid-p。

        ⚠️ 「exact/χ² ≥ mid-p」只在 **|b − c| ≥ 1** 时成立：
        ``b = c`` 时连续性校正把统计量从 0 抬到 1（见上面那条测试）。
        """
        mid = mcnemar_midp(38, 11)
        exact = mcnemar_exact(38, 11)
        chisq = mcnemar_chisq_cc(38, 11)
        self.assertGreaterEqual(exact, mid)
        self.assertGreaterEqual(chisq, mid)
        for value in (mid, exact, chisq):
            self.assertLess(value, 1e-3)
            self.assertGreater(value, 1e-5)

    def test_the_controls_are_never_smaller_than_mid_p(self):
        """一批 (b, c)（|b − c| ≥ 1）上，两个对照都 **不小于** mid-p。"""
        for b, c in ((1, 0), (0, 3), (2, 5), (16, 4), (30, 12), (38, 11)):
            mid = mcnemar_midp(b, c)
            self.assertGreaterEqual(mcnemar_exact(b, c), mid, msg=f"({b},{c})")
            self.assertGreaterEqual(mcnemar_chisq_cc(b, c), mid, msg=f"({b},{c})")


class WilsonTests(unittest.TestCase):
    """Wilson 区间：手算 + 边界。"""

    def test_hand_computed_example(self):
        """183 条里命中 82 → p = 0.4481；区间 ≈ [37.78%, 52.05%]。

        手算：denom = 1 + z²/n = 1.020992；center = (p + z²/2n)/denom = 0.449153；
        half = z/denom · √(p(1−p)/n + z²/4n²) = 0.071315。
        """
        low, high = wilson_ci(82, 183)
        self.assertAlmostEqual(low, 0.377840, places=5)
        self.assertAlmostEqual(high, 0.520468, places=5)

    def test_empty_sample_gives_the_whole_range(self):
        self.assertEqual(wilson_ci(0, 0), (0.0, 1.0))

    def test_zero_successes_never_goes_below_zero(self):
        low, high = wilson_ci(0, 10)
        self.assertEqual(low, 0.0)
        self.assertGreater(high, 0.0)

    def test_all_successes_never_goes_above_one(self):
        low, high = wilson_ci(10, 10)
        self.assertAlmostEqual(high, 1.0, places=12)
        self.assertLess(low, 1.0)

    def test_width_shrinks_as_n_grows(self):
        small = wilson_ci(50, 100)
        large = wilson_ci(500, 1000)
        self.assertLess(large[1] - large[0], small[1] - small[0])


class PairedBootstrapTests(unittest.TestCase):
    """配对 bootstrap：Δ 的定义 + 可复现。"""

    def test_delta_is_b_minus_a(self):
        pairs = [(True, True), (True, False), (False, False), (False, True)]
        result = paired_bootstrap_delta(pairs, iterations=50, seed=1)
        self.assertAlmostEqual(result.delta, 0.0)

    def test_delta_sign_follows_the_arms(self):
        pairs = [(False, True), (False, True), (True, False), (False, False)]
        result = paired_bootstrap_delta(pairs, iterations=50, seed=1)
        self.assertAlmostEqual(result.delta, 0.25)

    def test_same_seed_gives_the_same_interval(self):
        pairs = [(True, False)] * 30 + [(False, False)] * 70
        first = paired_bootstrap_delta(pairs, iterations=200, seed=7)
        second = paired_bootstrap_delta(pairs, iterations=200, seed=7)
        self.assertEqual(first, second)

    def test_interval_brackets_the_estimate(self):
        pairs = [(True, True)] * 40 + [(False, False)] * 40 + [(True, False)] * 10
        result = paired_bootstrap_delta(pairs, iterations=500, seed=3)
        self.assertLessEqual(result.ci_low, result.delta)
        self.assertGreaterEqual(result.ci_high, result.delta)

    def test_empty_input_is_neutral(self):
        result = paired_bootstrap_delta([], iterations=10, seed=1)
        self.assertEqual((result.delta, result.ci_low, result.ci_high), (0.0, 0.0, 0.0))


class MdeTests(unittest.TestCase):
    """MDE：公式自检。"""

    def test_formula_on_the_plan_numbers(self):
        """n = 183、π_d = 0.10 → 6.55 pp；n = 1581 → 2.23 pp。"""
        self.assertAlmostEqual(mde(183, 0.10), 6.5490, places=3)
        self.assertAlmostEqual(mde(1581, 0.10), 2.2285, places=3)

    def test_inverts_the_required_n_formula(self):
        """``N = 7.849·π_d/Δ²`` 反解回 MDE（自洽）。"""
        for n, discordance in ((183, 0.10), (330, 0.10), (1581, 0.05)):
            value = mde(n, discordance)
            required = 7.849 * discordance / ((value / 100) ** 2)
            self.assertAlmostEqual(required, n, places=6)

    def test_invalid_input_is_rejected(self):
        with self.assertRaises(ValueError):
            mde(0, 0.10)
        with self.assertRaises(ValueError):
            mde(183, 1.5)

    def test_the_signature_has_no_target_effect_argument(self):
        """**没有**「目标效应」参数（2026-10-22 删掉：它不改变返回值）。"""
        with self.assertRaises(TypeError):
            mde(183, 0.10, 3.3)  # type: ignore[call-arg]


if __name__ == "__main__":
    unittest.main()

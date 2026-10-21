"""**落点误差统一口径**的单测（方案 D0）。

要钉死的三件事：

1. **落点误差 = 最终落点 vs 真值**（唯一口径）；
2. **L1 误差只在精定位真开了时才有** —— 门控未命中时它是 ``None``，
   **不是 0**（0 会被误读成「精定位很准」）；
3. 报告里**必须同时给出精定位开启率与回落率** —— 它们是两套口径差异的来源。
"""

from __future__ import annotations

import unittest

from minicode.ground.caliber import (
    CaliberSample,
    coarse_error_px,
    landing_error_px,
    refine_error_px,
    summarize_samples,
)


class TestUnifiedCaliber(unittest.TestCase):
    def _sample(self, **kwargs) -> CaliberSample:
        base = {
            "name": "s",
            "truth": (100.0, 100.0),
            "coarse_point": (110.0, 100.0),
            "landing_point": (102.0, 100.0),
        }
        base.update(kwargs)
        return CaliberSample(**base)

    def test_landing_error_uses_final_landing_point(self):
        sample = self._sample()
        self.assertAlmostEqual(landing_error_px(sample), 2.0)
        self.assertAlmostEqual(coarse_error_px(sample), 10.0)

    def test_refine_error_is_none_when_refine_did_not_run(self):
        """门控未命中 → L1 根本没发生 → 报 None（不是 0）。"""
        sample = self._sample(refine_enabled=False)
        self.assertIsNone(refine_error_px(sample))

    def test_refine_error_is_none_when_it_fell_back(self):
        """精定位开了但回落 → 最终落点就是粗定位点 → L1 无从谈起。"""
        sample = self._sample(refine_enabled=True, fallback=True)
        self.assertIsNone(refine_error_px(sample))

    def test_refine_error_present_when_refine_ran(self):
        sample = self._sample(refine_enabled=True, fallback=False)
        self.assertAlmostEqual(refine_error_px(sample), 2.0)

    def test_report_states_enable_and_fallback_rates(self):
        samples = [
            self._sample(name="a", refine_enabled=True, fallback=False),
            self._sample(name="b", refine_enabled=True, fallback=True),
            self._sample(name="c", refine_enabled=False, fallback=False),
        ]
        report = summarize_samples(samples, source="unit")
        self.assertEqual(report.samples, 3)
        self.assertEqual(report.refine_enabled, 2)
        self.assertEqual(report.fallbacks, 1)
        self.assertAlmostEqual(report.refine_enabled_rate, 2 / 3)
        self.assertAlmostEqual(report.fallback_rate, 0.5)
        # 落点误差三个样本都有；L1 只有两个（其中一个回落）。
        self.assertIsNotNone(report.landing)
        self.assertIsNotNone(report.refine)
        self.assertEqual(report.refine.count, 1)

    def test_precomputed_errors_are_used_when_points_are_absent(self):
        """真机记录里存的是误差、没存点 —— 那条路也要走得通。"""
        sample = CaliberSample(
            name="s",
            precomputed_coarse_error_px=7.5,
            precomputed_refine_error_px=2.5,
            refine_enabled=True,
        )
        self.assertAlmostEqual(coarse_error_px(sample), 7.5)
        self.assertAlmostEqual(refine_error_px(sample), 2.5)


if __name__ == "__main__":
    unittest.main()

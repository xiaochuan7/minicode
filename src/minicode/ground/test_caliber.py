"""**落点误差统一口径**的单测（方案 D0）。

要钉死的三件事：

1. **落点误差 = 最终落点 vs 真值**（唯一口径）；
2. **L1 误差只在精定位真开了时才有** —— 门控未命中时它是 ``None``，
   **不是 0**（0 会被误读成「精定位很准」）；
3. 报告里**必须同时给出精定位开启率与回落率** —— 它们是两套口径差异的来源。
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from minicode.ground.caliber import (
    CaliberSample,
    coarse_error_px,
    landing_error_px,
    refine_error_px,
    summarize_samples,
)
from minicode.ground.caliber import main as caliber_main


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


class CoarseOnlyArmTests(unittest.TestCase):
    """**第三臂（只用粗定位）**也要能被**统一口径**读出来。

    ⚠️ 那一臂是「从来没量过的基线」（1.00 次模型调用/目标）——
    它的落点误差读不出来，就没法回答「精定位到底值不值那多出来的一次调用」。
    """

    def _dataset(self, root: Path) -> None:
        """造一份最小数据集（一条样本，GT 框 = (0,0)-(20,20)）。"""
        (root / "annotations").mkdir(parents=True)
        (root / "images").mkdir(parents=True)
        (root / "annotations" / "a.json").write_text(
            json.dumps(
                [
                    {
                        "id": "s1",
                        "img_filename": "s1.png",
                        "bbox": [0, 0, 20, 20],
                        "img_size": [100, 100],
                        "instruction": "甲",
                        "ui_type": "icon",
                        "platform": "windows",
                        "group": "Dev",
                        "application": "app",
                    }
                ]
            ),
            encoding="utf-8",
        )

    def test_the_cli_reads_the_coarse_only_arm(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            dataset = root / "sspro"
            self._dataset(dataset)
            report_path = root / "sspro-report.json"
            report_path.write_text(
                json.dumps(
                    {
                        "outcomes": {
                            "coarse_only": [
                                {
                                    "sample_id": "s1",
                                    "landing_point": [10, 10],
                                    "coarse_point": [10, 10],
                                    "refine_enabled": False,
                                    "fallback": False,
                                    "hit": True,
                                }
                            ]
                        }
                    }
                ),
                encoding="utf-8",
            )
            out_path = root / "caliber.json"
            code = caliber_main(
                [
                    "--sspro-report",
                    str(report_path),
                    "--dataset",
                    str(dataset),
                    "--arm",
                    "coarse_only",
                    "--out",
                    str(out_path),
                ]
            )
            self.assertEqual(code, 0)
            reports = json.loads(out_path.read_text(encoding="utf-8"))
            self.assertEqual(len(reports), 1)
            self.assertEqual(reports[0]["samples"], 1)
            self.assertAlmostEqual(reports[0]["click_accuracy"], 1.0)
            self.assertIsNotNone(reports[0]["landing_error_px"])


if __name__ == "__main__":
    unittest.main()

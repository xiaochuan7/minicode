"""**ScreenSpot-Pro 门控评测**的外部行为单测（工单 M8）。

钉死三件事：

* **数据集读得对**（字段、GT bbox、单位 —— 读错了分数就是假的）；
* **两臂对比的判据算得对**（硬判据 + 防掩盖的逐子集容忍度）；
* **那张 7 行表**的形状与标注（官方 5 行必须标「官方自报，不作硬对比」）。

本模块**不打真桌面、不花钱**：全部用构造出来的样本与结果。
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from minicode.eval.sspro import (
    OFFICIAL_ROWS,
    ArmOutcome,
    SsproSample,
    compare_arms,
    load_samples,
    render_table,
    stratified_subset,
    summarize,
    table_rows,
)


def _sample(
    sample_id: str,
    *,
    platform: str = "windows",
    ui_type: str = "text",
    hit: bool = True,
    refine_enabled: bool = False,
    gate_hit: bool = False,
    size: tuple[int, int] = (2560, 1440),
) -> ArmOutcome:
    return ArmOutcome(
        sample_id=sample_id,
        ui_type=ui_type,
        platform=platform,
        group="Dev",
        application="app",
        hit=hit,
        landing_point=(10, 10),
        coarse_point=(10.0, 10.0),
        gate_hit=gate_hit,
        gate_reasons=("target_tiny",) if gate_hit else (),
        gate_undecidable=("target_area_ratio",),
        refine_enabled=refine_enabled,
        fallback=False,
        fallback_cause="",
        model_calls=3,
        error="",
    )


class LoadSamplesTests(unittest.TestCase):
    """数据集读得对（字段、单位、路径）。"""

    def test_reads_annotations_into_samples(self):
        payload = [
            {
                "id": "app_0",
                "img_filename": "app_win/shot.png",
                "bbox": [10, 20, 30, 40],
                "img_size": [2560, 1440],
                "instruction": "click the save button",
                "ui_type": "icon",
                "platform": "windows",
                "group": "Dev",
                "application": "app",
            }
        ]
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "annotations").mkdir()
            (root / "annotations" / "app.json").write_text(
                json.dumps(payload), encoding="utf-8"
            )
            samples = load_samples(root)
        self.assertEqual(len(samples), 1)
        sample = samples[0]
        self.assertEqual(sample.sample_id, "app_0")
        self.assertEqual(sample.bbox, (10, 20, 30, 40))
        self.assertEqual(sample.image_size, (2560, 1440))
        self.assertEqual(sample.ui_type, "icon")
        self.assertEqual(sample.instruction, "click the save button")
        self.assertEqual(sample.image_path.name, "shot.png")

    def test_bbox_is_kept_in_original_pixels(self):
        """GT bbox **不换算**（内部一律原图像素）。"""
        payload = [
            {
                "id": "app_0",
                "img_filename": "a/b.png",
                "bbox": [1774, 1586, 2113, 1618],
                "img_size": [3840, 2160],
                "instruction": "x",
                "ui_type": "text",
                "platform": "macos",
                "group": "Dev",
                "application": "app",
            }
        ]
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "annotations").mkdir()
            (root / "annotations" / "app.json").write_text(
                json.dumps(payload), encoding="utf-8"
            )
            samples = load_samples(root)
        self.assertEqual(samples[0].bbox, (1774, 1586, 2113, 1618))


class StratifiedSubsetTests(unittest.TestCase):
    """分层抽样：**确定性的**，每层条数有上限。"""

    def _samples(self) -> list[SsproSample]:
        out: list[SsproSample] = []
        for platform in ("windows", "macos"):
            for ui_type in ("text", "icon"):
                for index in range(5):
                    out.append(
                        SsproSample(
                            sample_id=f"{platform}-{ui_type}-{index}",
                            image_path=Path("a.png"),
                            bbox=(0, 0, 10, 10),
                            image_size=(2560, 1440),
                            instruction="x",
                            ui_type=ui_type,
                            platform=platform,
                            group="Dev",
                            application="app",
                        )
                    )
        return out

    def test_takes_at_most_per_stratum(self):
        chosen = stratified_subset(self._samples(), 2)
        counts: dict[tuple[str, str], int] = {}
        for sample in chosen:
            key = (sample.platform, sample.ui_type)
            counts[key] = counts.get(key, 0) + 1
        self.assertEqual(len(chosen), 8)
        self.assertTrue(all(count == 2 for count in counts.values()))

    def test_same_seed_same_selection(self):
        first = stratified_subset(self._samples(), 3)
        second = stratified_subset(self._samples(), 3)
        self.assertEqual(
            [sample.sample_id for sample in first],
            [sample.sample_id for sample in second],
        )


class SummarizeTests(unittest.TestCase):
    """汇总：整体 / 文本 / 图标 / 平台 + 门控埋点（与分数同一批样本）。"""

    def test_accuracy_and_splits(self):
        outcomes = [
            _sample("a", hit=True),
            _sample("b", hit=False),
            _sample("c", platform="macos", ui_type="icon", hit=True),
        ]
        summary = summarize(outcomes)
        self.assertEqual(summary["samples"], 3)
        self.assertAlmostEqual(summary["accuracy"], 2 / 3)
        self.assertEqual(summary["hits"], 2)
        self.assertAlmostEqual(summary["accuracy_by_ui_type"]["text"], 0.5)
        self.assertAlmostEqual(summary["accuracy_by_ui_type"]["icon"], 1.0)
        self.assertAlmostEqual(summary["accuracy_by_platform"]["windows"], 0.5)

    def test_gate_stats_come_from_the_same_outcomes(self):
        outcomes = [
            _sample("a", gate_hit=True, refine_enabled=True),
            _sample("b"),
        ]
        summary = summarize(outcomes)
        self.assertEqual(summary["gate"]["samples"], 2)
        self.assertEqual(summary["gate"]["refine_enabled"], 1)
        self.assertEqual(summary["gate"]["gate_hits"], 1)


class CompareArmsTests(unittest.TestCase):
    """两臂对比：硬判据 + 防掩盖。"""

    def test_criterion_met_when_on_demand_not_worse(self):
        on_demand = [_sample("a", hit=True), _sample("b", hit=True)]
        always = [_sample("a", hit=True), _sample("b", hit=False)]
        comparison = compare_arms(on_demand, always)
        self.assertTrue(comparison["overall"]["criterion_met"])
        self.assertTrue(comparison["anti_masking_met"])

    def test_criterion_fails_when_on_demand_is_worse(self):
        on_demand = [_sample("a", hit=False), _sample("b", hit=True)]
        always = [_sample("a", hit=True), _sample("b", hit=True)]
        comparison = compare_arms(on_demand, always)
        self.assertFalse(comparison["overall"]["criterion_met"])

    def test_anti_masking_catches_a_bad_stratum(self):
        """整体持平，但某个子集比门控关低超过 1 pp → 防掩盖不通过。"""
        on_demand = [
            _sample("w1", hit=True),
            _sample("w2", hit=True),
            _sample("w3", hit=True),
            _sample("w4", hit=False),
            _sample("m1", platform="macos", hit=True),
            _sample("m2", platform="macos", hit=True),
            _sample("m3", platform="macos", hit=True),
            _sample("m4", platform="macos", hit=True),
        ]
        always = [
            _sample("w1", hit=True),
            _sample("w2", hit=True),
            _sample("w3", hit=True),
            _sample("w4", hit=True),
            _sample("m1", platform="macos", hit=True),
            _sample("m2", platform="macos", hit=True),
            _sample("m3", platform="macos", hit=False),
            _sample("m4", platform="macos", hit=False),
        ]
        comparison = compare_arms(on_demand, always)
        self.assertTrue(comparison["overall"]["criterion_met"])
        self.assertFalse(comparison["anti_masking_met"])
        bad = [item for item in comparison["strata"] if not item["within_tolerance"]]
        self.assertEqual(len(bad), 1)
        self.assertEqual(bad[0]["stratum"], "windows/text")

    def test_tolerance_is_one_percentage_point(self):
        self.assertEqual(compare_arms([], [])["tolerance_pp"], 1.0)


class TableTests(unittest.TestCase):
    """那张 7 行表：形状与标注。"""

    def test_seven_rows_with_official_ones_labeled(self):
        on_demand = [_sample("a", hit=True)]
        always = [_sample("a", hit=False)]
        comparison = compare_arms(on_demand, always)
        rows = table_rows(
            comparison=comparison,
            official=OFFICIAL_ROWS,
            model_id="test-model",
            crop_size=(512, 512),
        )
        self.assertEqual(len(rows), 7)
        self.assertEqual(len(OFFICIAL_ROWS), 5)
        for row in OFFICIAL_ROWS:
            self.assertIn("不作硬对比", row["source"])
            self.assertTrue(row["crop"].strip())
        self.assertIn("1024×1024", OFFICIAL_ROWS[0]["crop"])
        self.assertIn("512×512", rows[5]["crop"])

    def test_render_table_has_header_and_rows(self):
        on_demand = [_sample("a", hit=True)]
        always = [_sample("a", hit=False)]
        comparison = compare_arms(on_demand, always)
        rows = table_rows(
            comparison=comparison,
            official=OFFICIAL_ROWS,
            model_id="test-model",
            crop_size=(512, 512),
        )
        table = render_table(rows)
        self.assertEqual(len(table.splitlines()), 9)  # 表头 + 分隔线 + 7 行
        self.assertIn("门控开", table)
        self.assertIn("门控关", table)


if __name__ == "__main__":
    unittest.main()

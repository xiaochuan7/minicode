"""**ScreenSpot-Pro 门控评测**的外部行为单测（工单 M8）。

钉死三件事：

* **数据集读得对**（字段、GT bbox、单位 —— 读错了分数就是假的）；
* **两臂对比的判据算得对**（硬判据 + 防掩盖的逐子集容忍度）；
* **那张 7 行表**的形状与标注（官方 5 行必须标「官方自报，不作硬对比」）。

外加施工清单第 ⑨ 条与第 ⑥ 条落在本模块的三处接线：
**配对分层键**、**近失带离线校准**、**记忆缓存的阶段标签**，
以及标定链的**相等断言**（实现住在 :mod:`minicode.calib.calibrate`；
用例放在这里是因为**本轮的写权限只给了 eval 的测试文件**）。

本模块**不打真桌面、不花钱**：全部用构造出来的样本与结果。
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from minicode.calib.calibrate import (
    IMAGE_TOKEN_ASSERTION_TOLERANCE_TOKENS,
    assert_server_kept_image_tokens,
)
from minicode.eval.sspro import (
    GATE_INPUT_SOURCES,
    OFFICIAL_ROWS,
    ArmOutcome,
    MemoizingModelCaller,
    SsproSample,
    compare_arms,
    load_samples,
    near_miss_calibration,
    prompt_stage,
    render_table,
    stratified_subset,
    summarize,
    table_rows,
)
from minicode.ground.gate import (
    GateCondition,
    GateInputs,
    evaluate_gate,
)
from minicode.model.client import ModelReply
from minicode.model.effort import Effort


def _sample(
    sample_id: str,
    *,
    platform: str = "windows",
    ui_type: str = "text",
    hit: bool = True,
    refine_enabled: bool = False,
    gate_hit: bool = False,
    size: tuple[int, int] = (2560, 1440),
    coarse_point: tuple[float, float] | None = (10.0, 10.0),
    dist_to_bbox: float | None = None,
) -> ArmOutcome:
    return ArmOutcome(
        sample_id=sample_id,
        ui_type=ui_type,
        platform=platform,
        group="Dev",
        application="app",
        hit=hit,
        landing_point=(10, 10),
        coarse_point=coarse_point,
        gate_hit=gate_hit,
        gate_reasons=("target_tiny",) if gate_hit else (),
        gate_undecidable=("target_area_ratio",),
        refine_enabled=refine_enabled,
        fallback=False,
        fallback_cause="",
        model_calls=3,
        error="",
        dist_to_bbox=dist_to_bbox,
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
            crop_label="crop 512×512",
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
            crop_label="crop 512×512",
        )
        table = render_table(rows)
        self.assertEqual(len(table.splitlines()), 9)  # 表头 + 分隔线 + 7 行
        # ⚠️ 门控降级为**记录器**之后，两臂的名字改了（用户 2026-10-21 拍板）：
        # 「门控开 / 门控关」→「门控记录器 / 无条件两段式」。
        self.assertIn("门控记录器", table)
        self.assertIn("无条件两段式", table)

    def test_the_table_has_the_coarse_only_baseline_arm(self):
        """**只用粗定位**那一臂（1.00 次/目标）要在表里 —— 我们从来没量过这个基线。"""
        on_demand = [_sample("a", hit=True)]
        always = [_sample("a", hit=True)]
        coarse_only = [_sample("a", hit=False)]
        comparison = compare_arms(on_demand, always, coarse_only=coarse_only)
        rows = table_rows(
            comparison=comparison,
            official=OFFICIAL_ROWS,
            model_id="test-model",
            crop_label="crop 512×512",
        )
        table = render_table(rows)
        self.assertIn("只用粗定位", table)
        self.assertEqual(len(table.splitlines()), 10)  # 多了一行基线臂


class GateInputSourceTests(unittest.TestCase):
    """报告抬头那份「门控输入来源」必须与**代码实际判的**一致。

    ⚠️ 它进 ``sspro-report.json`` 的 ``gate.input_sources`` —— 读报告的人
    **照着它判断「哪条条件当时判了」**。口径变过（门控降级为记录器、
    粗定位出框）却没回来改这里的话，报告就会**说反话**。
    """

    def test_the_code_judges_what_the_report_claims(self):
        decision = evaluate_gate(
            GateInputs(
                coarse_point=(100.0, 100.0),
                coarse_out_of_bounds=False,
                #: 粗定位**出框** → 通道用框推出半径 → 占比**第一次可判定**。
                target_radius_px=8.0,
                coarse_confidence=None,
                #: 没有第二次独立粗定位（那次调用已被删掉）→ 一致性**不可判定**。
                second_coarse_point=None,
                surface_size=(1000, 1000),
            )
        )
        self.assertNotIn(GateCondition.TARGET_AREA_RATIO, decision.undecidable)
        self.assertIn(GateCondition.CONSISTENCY, decision.undecidable)

    def test_the_report_text_matches_that_behaviour(self):
        self.assertTrue(
            GATE_INPUT_SOURCES["target_area_ratio"].startswith("可判定"),
            msg=GATE_INPUT_SOURCES["target_area_ratio"],
        )
        self.assertTrue(
            GATE_INPUT_SOURCES["consistency"].startswith("不可判定"),
            msg=GATE_INPUT_SOURCES["consistency"],
        )


class PairedStrataTests(unittest.TestCase):
    """配对分层的**键** = **粗定位命中与否**（施工清单第 ⑨ 条）。

    ⚠️ 分层变量必须**先于**被测步确定（粗定位在精定位之前）→ 不构成 collider。
    """

    def _strata(self, always, coarse_only):
        comparison = compare_arms(always, always, coarse_only=coarse_only)
        return comparison["paired"]["always_vs_coarse_only"]["strata"]

    def test_each_sample_lands_in_its_own_bucket(self):
        strata = self._strata(
            [_sample("a", hit=True), _sample("b", hit=False)],
            [_sample("a", hit=True), _sample("b", hit=False)],
        )
        self.assertEqual(strata["coarse_hit"]["samples"], 1)
        self.assertEqual(strata["coarse_miss"]["samples"], 1)

    def test_stratum_is_not_keyed_on_whether_a_coarse_point_exists(self):
        """**没有粗点但粗定位命中** → 归「粗定位命中」那一桶。

        （旧口径按「有没有粗点」分层 → 这一条会掉进另一桶 → 结论不可用。）
        """
        strata = self._strata(
            [_sample("a", hit=True)],
            [_sample("a", hit=True, coarse_point=None)],
        )
        self.assertEqual(strata["coarse_hit"]["samples"], 1)
        self.assertEqual(strata["coarse_miss"]["samples"], 0)

    def test_a_coarse_point_that_missed_lands_in_coarse_miss(self):
        """**有粗点但粗定位挑错了** → 归「粗定位没命中」那一桶。"""
        strata = self._strata(
            [_sample("a", hit=False)],
            [_sample("a", hit=False)],
        )
        self.assertEqual(strata["coarse_miss"]["samples"], 1)
        self.assertEqual(strata["coarse_hit"]["samples"], 0)

    def test_the_stratum_variable_is_written_down(self):
        strata = self._strata([_sample("a")], [_sample("a")])
        self.assertIn("hit", strata["variable"])
        self.assertIn("collider", strata["note"])


class NearMissCalibrationTests(unittest.TestCase):
    """**近失带的离线校准**（验收线 B 组）—— 纯算术，不发模型调用。"""

    def test_sweep_counts_hits_failed_and_misses_rescued(self):
        outcomes = [
            _sample("a", hit=True, dist_to_bbox=0.0),
            _sample("b", hit=False, dist_to_bbox=8.0),
            _sample("c", hit=False, dist_to_bbox=30.0),
            _sample("d", hit=False, dist_to_bbox=226.0),
        ]
        calibration = near_miss_calibration(outcomes)
        by_tau = {item["tau_px"]: item for item in calibration["taus"]}
        self.assertEqual(sorted(by_tau), [5, 10, 20, 50, 100])
        self.assertEqual(by_tau[5]["misses_rescued"], 0)
        self.assertEqual(by_tau[10]["misses_rescued"], 1)
        self.assertEqual(by_tau[50]["misses_rescued"], 2)
        self.assertEqual(by_tau[100]["misses_rescued"], 2)
        self.assertEqual(calibration["band_36px"]["misses_rescued"], 2)

    def test_a_hit_far_from_the_box_would_be_judged_failed(self):
        outcomes = [
            _sample("a", hit=True, dist_to_bbox=100.0),
            _sample("b", hit=False, dist_to_bbox=8.0),
        ]
        calibration = near_miss_calibration(outcomes, taus=(5,))
        self.assertEqual(calibration["taus"][0]["hits_failing_band"], 1)
        self.assertEqual(calibration["taus"][0]["misses_rescued"], 0)

    def test_distance_percentiles_and_subset_medians(self):
        outcomes = [
            _sample("a", hit=True, dist_to_bbox=0.0),
            _sample("b", hit=False, dist_to_bbox=8.0),
            _sample("c", hit=False, dist_to_bbox=30.0),
            _sample("d", hit=False, dist_to_bbox=226.0),
        ]
        calibration = near_miss_calibration(outcomes)
        self.assertEqual(calibration["dist_to_bbox"]["p50"], 30.0)
        self.assertEqual(calibration["dist_to_bbox"]["p90"], 226.0)
        #: 「找对」子集（命中）→ 距离恒为 0（框内 = 0）。
        self.assertEqual(calibration["p50_by_subset"]["hit"], 0.0)
        self.assertEqual(calibration["p50_by_subset"]["miss"], 30.0)


class _FakeCaller:
    """假模型端口（只回固定文本，不发真请求）。"""

    def __init__(self) -> None:
        self.prompts: list[str] = []

    def complete(
        self,
        prompt: str,
        *,
        effort: Effort | str | None = None,
        max_tokens: int = 2000,
        image_base64: str | None = None,
        images_base64=(),
        thinking_disabled: bool = False,
    ) -> ModelReply:
        self.prompts.append(prompt)
        return ModelReply(
            text='{"bbox": [1, 2, 3, 4]}',
            model="fake-model",
            endpoint="fake-endpoint",
            effort=Effort.LOW,
            http_status=200,
            latency_ms=1.0,
            input_tokens=10,
            output_tokens=5,
        )


class MemoStageTests(unittest.TestCase):
    """记忆缓存的**阶段标签**（施工清单第 ⑨ 条）。"""

    COARSE_PROMPT = "这是一张 Windows 桌面截图。\n这张图是 2560×1440。\n请找出元素。"

    def test_stage_is_read_off_the_prompt(self):
        self.assertEqual(prompt_stage(self.COARSE_PROMPT), "coarse")
        self.assertEqual(
            prompt_stage("这是一张 Windows 桌面截图。\n（上一次没能给出可用的位置：空）"),
            "whole2nd",
        )
        self.assertEqual(
            prompt_stage("这是一张从 Windows 桌面截图里裁出的小区域（未缩放）。"),
            "refine",
        )
        self.assertEqual(prompt_stage("这是一张标定图，这张图是 2560×1440。"), "calib")
        self.assertEqual(prompt_stage("认不出来的提示词"), "unknown")

    def test_cache_entry_carries_the_stage_label(self):
        with TemporaryDirectory() as directory:
            cache = Path(directory) / "memo.jsonl"
            caller = MemoizingModelCaller(_FakeCaller(), cache_path=cache)
            caller.complete(self.COARSE_PROMPT)
            records = [
                json.loads(line)
                for line in cache.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0]["stage"], "coarse")
            self.assertEqual(caller.stage_report()["sent"], {"coarse": 1})

    def test_legacy_cache_without_a_label_reads_as_unknown(self):
        with TemporaryDirectory() as directory:
            cache = Path(directory) / "memo.jsonl"
            first = MemoizingModelCaller(_FakeCaller(), cache_path=cache)
            first.complete(self.COARSE_PROMPT)
            record = json.loads(cache.read_text(encoding="utf-8").splitlines()[0])
            record.pop("stage")
            cache.write_text(
                json.dumps(record, ensure_ascii=False) + "\n", encoding="utf-8"
            )
            second = MemoizingModelCaller(_FakeCaller(), cache_path=cache)
            second.complete(self.COARSE_PROMPT)
            self.assertEqual(second.calls, 0)
            self.assertEqual(second.replays, 1)
            self.assertEqual(second.stage_report()["replayed"], {"unknown": 1})


class ImageTokenAssertionTests(unittest.TestCase):
    """标定链的**相等断言**（施工清单第 ⑥ 条）—— 闸门，与「±28 px 盒子」分开。"""

    def test_equal_tokens_mean_the_server_kept_the_size(self):
        assertion = assert_server_kept_image_tokens(3406, (2048, 1280))
        self.assertEqual(assertion.expected_image_tokens, 3404)
        self.assertEqual(assertion.difference_tokens, 2)
        self.assertTrue(assertion.server_kept_size)

    def test_unequal_tokens_mean_the_coordinate_system_is_untrustworthy(self):
        assertion = assert_server_kept_image_tokens(1334, (2048, 1280))
        self.assertFalse(assertion.server_kept_size)

    def test_unmeasured_stays_unknown(self):
        assertion = assert_server_kept_image_tokens(None, (2048, 1280))
        self.assertIsNone(assertion.server_kept_size)
        self.assertIsNone(assertion.difference_tokens)

    def test_the_tolerance_is_a_named_constant(self):
        self.assertEqual(IMAGE_TOKEN_ASSERTION_TOLERANCE_TOKENS, 2)


if __name__ == "__main__":
    unittest.main()

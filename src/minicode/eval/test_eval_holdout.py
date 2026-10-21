"""**留出集重评**的外部行为单测（工单 M23）。

钉死六件事：

* **切分**是规则驱动的（层内按样本 ID 字典序交替）、可复核、每次都一样；
* **一致性距离判不了时给区间，不给点估计**（宁可低估，不许高估）；
* **偏离容差**同样按「记录给不出的部分给区间」处理；
* **调参只看 A**，选中的参数带取值来源串（未校准的写「工程阈值·未验证」）；
* **结论判据**：B 上的差值 ≤ 噪声量级 → 判「不成立」；
* **离线重建与当时的跑批是同一回事**（用记录参数重放，两臂的命中逐条复现）。

本模块**不打真桌面、不花钱、不调模型**：全部用构造出来的记录与样本。
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from minicode.eval.holdout import (
    CONSISTENT,
    INCONSISTENT,
    PATH_REFINE,
    PATH_WHOLE_SCREEN,
    SPLIT_EVAL,
    SPLIT_TUNING,
    UNDECIDABLE,
    HoldoutSample,
    RefinePath,
    build_report,
    evaluate_half,
    gate_opens_at,
    load_recorded_run,
    reconstruct,
    render_report,
    replay_check,
    run_holdout,
    score_always,
    score_on_demand,
    split_samples,
    tune_gate_params,
)

#: 记录那一轮的参数（测试里固定）。
RECORDED_TOLERANCE = 16.0
RECORDED_DEVIATION = 128.0


def _sample(
    sample_id: str,
    *,
    split: str = SPLIT_TUNING,
    stratum: str = "windows/text",
    consistency: str = CONSISTENT,
    coarse_invalid: bool = False,
    coarse_hit: bool | None = True,
    refine_hit: bool = True,
    always_hit: bool = True,
    flip: bool = False,
    coarse_differs: bool = False,
    deviation: float | None = 10.0,
) -> HoldoutSample:
    """构造一条样本事实（默认：门控开与门控关都命中）。"""
    gate_open = consistency == INCONSISTENT or coarse_invalid
    if coarse_invalid:
        od_path = RefinePath(
            kind=PATH_WHOLE_SCREEN, hit=refine_hit, coarse_hit=None, deviation=None
        )
    else:
        od_path = RefinePath(
            kind=PATH_REFINE, hit=refine_hit, coarse_hit=coarse_hit, deviation=deviation
        )
    always_path = RefinePath(
        kind=PATH_REFINE, hit=always_hit, coarse_hit=coarse_hit, deviation=deviation
    )
    return HoldoutSample(
        sample_id=sample_id,
        stratum=stratum,
        split=split,
        coarse_point=None if coarse_invalid else (10.0, 10.0),
        coarse_hit=None if coarse_invalid else coarse_hit,
        always_coarse_hit=None if coarse_invalid else coarse_hit,
        od_path=od_path,
        always_path=always_path,
        recorded_hit=refine_hit if gate_open else bool(coarse_hit),
        recorded_always_hit=always_hit,
        recorded_gate_hit=gate_open,
        consistency=consistency,
        coarse_invalid=coarse_invalid,
        coarse_differs=coarse_differs,
        flip=flip,
        area_ratio=0.0004,
        gate_reasons=("coarse_invalid",) if coarse_invalid else (),
        model_calls=3,
    )


class SplitTests(unittest.TestCase):
    """切分：规则写在代码里，可复核，每次都一样。"""

    def test_alternates_inside_each_stratum_by_sample_id(self):
        split = split_samples({"windows/text": ["d", "b", "a", "c"]})
        self.assertEqual(split.of("a"), SPLIT_TUNING)
        self.assertEqual(split.of("b"), SPLIT_EVAL)
        self.assertEqual(split.of("c"), SPLIT_TUNING)
        self.assertEqual(split.of("d"), SPLIT_EVAL)

    def test_both_halves_keep_the_same_stratum_composition(self):
        split = split_samples(
            {
                "windows/text": ["w1", "w2", "w3", "w4"],
                "macos/icon": ["x1", "x2", "x3", "x4"],
            }
        )
        self.assertEqual(len(split.ids(SPLIT_TUNING)), 4)
        self.assertEqual(len(split.ids(SPLIT_EVAL)), 4)
        windows = [sid for sid in split.assignment if sid.startswith("w")]
        macos = [sid for sid in split.assignment if sid.startswith("x")]
        self.assertEqual(len(windows), 4)
        self.assertEqual(len(macos), 4)

    def test_same_ids_always_give_the_same_assignment(self):
        strata = {"windows/text": ["a", "b", "c"], "linux/icon": ["p", "q"]}
        first = split_samples(strata)
        second = split_samples(dict(reversed(list(strata.items()))))
        self.assertEqual(dict(first.assignment), dict(second.assignment))

    def test_rule_is_written_down(self):
        split = split_samples({"windows/text": ["a"]})
        self.assertIn("样本 ID", split.rule)
        self.assertIn("交替", split.rule)


class GateDecisionTests(unittest.TestCase):
    """换容差时门控判定的三态（判不了 → ``None``）。"""

    def test_coarse_invalid_always_opens_the_gate(self):
        sample = _sample("a", coarse_invalid=True)
        self.assertIs(gate_opens_at(sample, 0.0, RECORDED_TOLERANCE), True)
        self.assertIs(gate_opens_at(sample, 1e9, RECORDED_TOLERANCE), True)

    def test_inconsistent_stays_inconsistent_for_smaller_tolerances(self):
        sample = _sample("a", consistency=INCONSISTENT)
        self.assertIs(gate_opens_at(sample, RECORDED_TOLERANCE, RECORDED_TOLERANCE), True)
        self.assertIs(gate_opens_at(sample, 4.0, RECORDED_TOLERANCE), True)

    def test_inconsistent_is_undetermined_for_larger_tolerances(self):
        """距离只知道「> 16」→ 容差更大时判不了。"""
        sample = _sample("a", consistency=INCONSISTENT)
        self.assertIsNone(gate_opens_at(sample, 32.0, RECORDED_TOLERANCE))

    def test_consistent_stays_consistent_for_larger_tolerances(self):
        sample = _sample("a", consistency=CONSISTENT)
        self.assertIs(gate_opens_at(sample, RECORDED_TOLERANCE, RECORDED_TOLERANCE), False)
        self.assertIs(gate_opens_at(sample, 128.0, RECORDED_TOLERANCE), False)

    def test_consistent_is_undetermined_for_smaller_tolerances(self):
        """距离只知道「≤ 16」→ 容差更小时判不了。"""
        sample = _sample("a", consistency=CONSISTENT)
        self.assertIsNone(gate_opens_at(sample, 8.0, RECORDED_TOLERANCE))

    def test_undecidable_consistency_never_triggers(self):
        sample = _sample("a", consistency=UNDECIDABLE)
        self.assertIs(gate_opens_at(sample, 0.0, RECORDED_TOLERANCE), False)
        self.assertIs(gate_opens_at(sample, 1e9, RECORDED_TOLERANCE), False)


class ScoreTests(unittest.TestCase):
    """打分：区间而不是点估计（判不了的部分）。"""

    def _score(self, samples, tolerance, deviation=RECORDED_DEVIATION):
        return score_on_demand(
            samples,
            tolerance,
            RECORDED_TOLERANCE,
            deviation,
            RECORDED_DEVIATION,
        )

    def test_recorded_tolerance_gives_a_point_estimate(self):
        samples = [
            _sample("a", consistency=INCONSISTENT),
            _sample("b", consistency=CONSISTENT),
        ]
        score = self._score(samples, RECORDED_TOLERANCE)
        self.assertTrue(score.determined)
        self.assertEqual(score.hits_lower, 2)
        self.assertEqual(score.hits_upper, 2)

    def test_unknown_distance_becomes_an_interval(self):
        """距离判不了 → 命中数给区间，不给点估计。"""
        samples = [
            _sample("a", consistency=INCONSISTENT, coarse_hit=False, refine_hit=True),
        ]
        score = self._score(samples, 32.0)
        self.assertFalse(score.determined)
        self.assertEqual(score.hits_lower, 0)
        self.assertEqual(score.hits_upper, 1)
        self.assertEqual(score.undetermined, 1)

    def test_interval_uses_the_pessimistic_bound(self):
        samples = [
            _sample("a", consistency=INCONSISTENT, coarse_hit=False, refine_hit=True),
        ]
        score = self._score(samples, 32.0)
        self.assertAlmostEqual(score.accuracy, 0.0)

    def test_deviation_fallback_is_reproduced_at_the_recorded_tolerance(self):
        """记录里的偏离回落 → 记录容差下**必然**仍回落。"""
        sample = _sample(
            "a",
            consistency=INCONSISTENT,
            coarse_hit=False,
            refine_hit=True,
            deviation=None,
        )
        score = self._score([sample], RECORDED_TOLERANCE)
        self.assertEqual(score.hits_lower, 0)

    def test_always_arm_scores_the_refine_path(self):
        samples = [_sample("a", consistency=INCONSISTENT, always_hit=True)]
        score = score_always(samples, RECORDED_DEVIATION, RECORDED_DEVIATION)
        self.assertEqual(score.hits_lower, 1)


class TuningTests(unittest.TestCase):
    """调参：只在 A 上，结果带取值来源串。"""

    def test_picks_the_largest_tolerance_among_the_tied_best(self):
        """A 上各个容差打平 → 取**最大**的那个（门控开得最少 = 省调用）。"""
        samples = [
            _sample("a1", consistency=CONSISTENT, coarse_hit=True, refine_hit=True),
            _sample("a2", consistency=INCONSISTENT, coarse_hit=False, refine_hit=True),
        ]
        tuning = tune_gate_params(samples, RECORDED_TOLERANCE, RECORDED_DEVIATION)
        self.assertEqual(tuning.tolerance_px, RECORDED_TOLERANCE)

    def test_picks_the_tolerance_with_the_best_pessimistic_bound(self):
        """容差太大 → 判不了的样本变多 → 悲观下界更差 → 不选它。"""
        samples = [
            _sample("a1", consistency=CONSISTENT, coarse_hit=True, refine_hit=True),
            _sample("a2", consistency=INCONSISTENT, coarse_hit=False, refine_hit=True),
            _sample("a3", consistency=INCONSISTENT, coarse_hit=True, refine_hit=True),
        ]
        tuning = tune_gate_params(samples, RECORDED_TOLERANCE, RECORDED_DEVIATION)
        self.assertEqual(tuning.tolerance_px, RECORDED_TOLERANCE)

    def test_uncalibrated_thresholds_are_marked_as_unverified(self):
        samples = [_sample("a1", consistency=CONSISTENT)]
        tuning = tune_gate_params(samples, RECORDED_TOLERANCE, RECORDED_DEVIATION)
        self.assertIn("工程阈值·未验证", tuning.params.consistency_source)
        self.assertIn("工程阈值·未验证", tuning.params.deviation_source)
        self.assertIn("未在评估半 B 上调", tuning.params.consistency_source)
        self.assertIn("未在评估半 B 上调", tuning.params.deviation_source)

    def test_other_params_keep_their_own_sources(self):
        samples = [_sample("a1", consistency=CONSISTENT)]
        tuning = tune_gate_params(samples, RECORDED_TOLERANCE, RECORDED_DEVIATION)
        self.assertIn("未验证", tuning.params.area_ratio_source)
        self.assertIn("工程阈值·未验证", tuning.params.confidence_source)

    def test_only_the_tuning_half_is_used(self):
        """调参只看 A —— 扫参表里的样本数就是 A 半的条数（不是全批）。"""
        on_demand = [
            _outcome(
                "s1",
                hit=True,
                coarse_point=[10.0, 10.0],
                gate_hit=True,
                gate_reasons=["coarse_inconsistent"],
            ),
            _outcome(
                "s2",
                hit=True,
                coarse_point=[10.0, 10.0],
                gate_hit=True,
                gate_reasons=["coarse_inconsistent"],
            ),
            _outcome(
                "s3",
                hit=True,
                coarse_point=[10.0, 10.0],
                gate_hit=True,
                gate_reasons=["coarse_inconsistent"],
            ),
            _outcome(
                "s4",
                hit=True,
                coarse_point=[10.0, 10.0],
                gate_hit=True,
                gate_reasons=["coarse_inconsistent"],
            ),
        ]
        always = list(on_demand)
        with TemporaryDirectory() as directory:
            root = Path(directory)
            _dataset(root, [_annotation(name) for name in ("s1", "s2", "s3", "s4")])
            report_path = root / "sspro-report.json"
            report_path.write_text(
                json.dumps(_recorded_payload(on_demand, always)), encoding="utf-8"
            )
            run = load_recorded_run(report_path)
            evaluation = run_holdout(run, root)
        self.assertEqual(evaluation.report["split"]["counts"], {"A": 2, "B": 2})
        for candidate in evaluation.tuning.candidates:
            self.assertEqual(candidate.on_demand.samples, 2)
            self.assertEqual(candidate.always.samples, 2)
        self.assertEqual(evaluation.result_a.samples, 2)
        self.assertEqual(evaluation.result_b.samples, 2)


class HalfEvaluationTests(unittest.TestCase):
    """评估与判定：差值必须超过噪声量级。"""

    def _evaluate(self, samples):
        return evaluate_half(
            SPLIT_EVAL,
            samples,
            RECORDED_TOLERANCE,
            RECORDED_TOLERANCE,
            RECORDED_DEVIATION,
            RECORDED_DEVIATION,
        )

    def test_delta_within_noise_is_not_a_conclusion(self):
        """B 上差值 0，噪声（两臂粗定位点不同）> 0 → 判「不成立」。"""
        samples = [
            _sample("b1", split=SPLIT_EVAL, flip=True, coarse_differs=True),
            _sample("b2", split=SPLIT_EVAL),
        ]
        result = self._evaluate(samples)
        self.assertEqual(result.delta_pp, 0.0)
        self.assertEqual(result.noise_magnitude_pp, 50.0)
        self.assertFalse(result.criterion_met)
        self.assertEqual(result.conclusion, "不成立")

    def test_delta_above_noise_and_not_worse_is_a_conclusion(self):
        """B 上差值 50 pp > 噪声 0 pp，且门控开 ≥ 门控关 → 判「成立」。"""
        samples = [
            _sample(
                "b1",
                split=SPLIT_EVAL,
                consistency=INCONSISTENT,
                coarse_hit=False,
                refine_hit=True,
                always_hit=False,
            ),
            _sample(
                "b2",
                split=SPLIT_EVAL,
                consistency=INCONSISTENT,
                coarse_hit=False,
                refine_hit=True,
                always_hit=True,
            ),
        ]
        result = self._evaluate(samples)
        self.assertAlmostEqual(result.delta_pp, 50.0)
        self.assertGreater(result.delta_pp, result.noise_magnitude_pp)
        self.assertTrue(result.criterion_met)
        self.assertEqual(result.conclusion, "成立")

    def test_gate_off_rate_is_reported(self):
        samples = [
            _sample("b1", split=SPLIT_EVAL, consistency=CONSISTENT),
            _sample("b2", split=SPLIT_EVAL, consistency=INCONSISTENT),
        ]
        result = self._evaluate(samples)
        self.assertAlmostEqual(result.gate_off_rate, 0.5)

    def test_noise_reports_both_measures(self):
        samples = [
            _sample("b1", split=SPLIT_EVAL, flip=True, coarse_differs=True),
            _sample("b2", split=SPLIT_EVAL, coarse_differs=True),
            _sample("b3", split=SPLIT_EVAL),
            _sample("b4", split=SPLIT_EVAL),
        ]
        result = self._evaluate(samples)
        self.assertEqual(result.flips, 1)
        self.assertEqual(result.coarse_differs, 2)
        self.assertAlmostEqual(result.noise_flip_pp, 25.0)
        self.assertAlmostEqual(result.noise_coarse_differs_pp, 50.0)
        self.assertAlmostEqual(result.noise_magnitude_pp, 50.0)

    def test_flips_are_attributed_to_the_gate_or_to_the_noise(self):
        """两臂粗定位点**不同**的翻转 → 噪声；相同的 → 只能来自门控。"""
        samples = [
            _sample("b1", split=SPLIT_EVAL, flip=True, coarse_differs=True),
            _sample("b2", split=SPLIT_EVAL, flip=True, coarse_differs=False),
        ]
        result = self._evaluate(samples)
        self.assertEqual(result.flips_noise_attributable, 1)
        self.assertEqual(result.flips_gate_attributable, 1)


def _recorded_payload(
    on_demand: list[dict],
    always: list[dict],
    tolerance_px: float = RECORDED_TOLERANCE,
    deviation_px: float = RECORDED_DEVIATION,
) -> dict:
    """构造一份「已记录的跑批产物」。"""
    return {
        "dataset": {"root": ".scratch/sspro", "evaluated_samples": len(on_demand)},
        "model": {"id": "test-model", "effort": "low"},
        "gate": {
            "params": {
                "consistency_tolerance_px": tolerance_px,
                "deviation_tolerance_px": deviation_px,
                "consistency_source": "工程阈值·未验证",
            }
        },
        "model_calls": {"sent": 10, "replayed": 4},
        "outcomes": {"on_demand": on_demand, "always": always},
    }


def _outcome(
    sample_id: str,
    *,
    hit: bool,
    coarse_point: list[float] | None,
    gate_hit: bool,
    gate_reasons: list[str],
    gate_undecidable: list[str] | None = None,
    fallback: bool = False,
    fallback_cause: str = "",
) -> dict:
    """构造一条记录里的 outcome。"""
    return {
        "sample_id": sample_id,
        "ui_type": "text",
        "platform": "windows",
        "group": "Dev",
        "application": "app",
        "hit": hit,
        "landing_point": [10, 10],
        "coarse_point": coarse_point,
        "gate_hit": gate_hit,
        "gate_reasons": gate_reasons,
        "gate_undecidable": gate_undecidable or ["target_area_ratio", "confidence"],
        "refine_enabled": gate_hit,
        "fallback": fallback,
        "fallback_cause": fallback_cause,
        "model_calls": 3,
        "error": "",
    }


def _dataset(root: Path, items: list[dict]) -> None:
    """写一份最小的标注目录。"""
    (root / "annotations").mkdir(parents=True, exist_ok=True)
    (root / "annotations" / "app.json").write_text(json.dumps(items), encoding="utf-8")


def _annotation(sample_id: str) -> dict:
    """一条最小的标注。"""
    return {
        "id": sample_id,
        "img_filename": "a.png",
        "bbox": [0, 0, 100, 100],
        "img_size": [2560, 1440],
        "instruction": "x",
        "ui_type": "text",
        "platform": "windows",
        "group": "Dev",
        "application": "app",
    }


class ReconstructionTests(unittest.TestCase):
    """离线重建：与当时的跑批是同一回事。"""

    def test_recomputed_coarse_hit_matches_the_recorded_one(self):
        """门控没开的那条：重算的「粗定位直接落点」命中 == 记录命中。"""
        on_demand = [
            _outcome("s1", hit=True, coarse_point=[10.0, 10.0], gate_hit=False, gate_reasons=[])
        ]
        always = [
            _outcome("s1", hit=True, coarse_point=[10.0, 10.0], gate_hit=False, gate_reasons=[])
        ]
        with TemporaryDirectory() as directory:
            root = Path(directory)
            _dataset(root, [_annotation("s1")])
            report_path = root / "sspro-report.json"
            report_path.write_text(
                json.dumps(_recorded_payload(on_demand, always)), encoding="utf-8"
            )
            run = load_recorded_run(report_path)
            split = split_samples({"windows/text": ["s1"]})
            samples = reconstruct(run, root, split)
        self.assertEqual(samples[0].coarse_hit, True)
        self.assertEqual(samples[0].split, SPLIT_TUNING)
        self.assertTrue(
            replay_check(samples, RECORDED_TOLERANCE, RECORDED_DEVIATION)["consistent"]
        )

    def test_replay_reproduces_both_arms(self):
        """用记录参数重放 → 两臂的命中逐条复现。"""
        on_demand = [
            _outcome(
                "s1",
                hit=True,
                coarse_point=[10.0, 10.0],
                gate_hit=True,
                gate_reasons=["coarse_inconsistent"],
            ),
            _outcome("s2", hit=False, coarse_point=[900.0, 900.0], gate_hit=True, gate_reasons=["coarse_invalid"]),
        ]
        always = [
            _outcome(
                "s1",
                hit=True,
                coarse_point=[10.0, 10.0],
                gate_hit=True,
                gate_reasons=["coarse_inconsistent"],
            ),
            _outcome("s2", hit=False, coarse_point=None, gate_hit=True, gate_reasons=["coarse_invalid"]),
        ]
        with TemporaryDirectory() as directory:
            root = Path(directory)
            _dataset(root, [_annotation("s1"), _annotation("s2")])
            report_path = root / "sspro-report.json"
            report_path.write_text(
                json.dumps(_recorded_payload(on_demand, always)), encoding="utf-8"
            )
            run = load_recorded_run(report_path)
            split = split_samples({"windows/text": ["s1", "s2"]})
            samples = reconstruct(run, root, split)
        check = replay_check(samples, RECORDED_TOLERANCE, RECORDED_DEVIATION)
        self.assertTrue(check["consistent"])
        self.assertEqual(check["on_demand_matches"], 2)
        self.assertEqual(check["always_matches"], 2)

    def test_records_the_recorded_tolerances(self):
        payload = _recorded_payload([], [], tolerance_px=16.0, deviation_px=128.0)
        with TemporaryDirectory() as directory:
            path = Path(directory) / "sspro-report.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            run = load_recorded_run(path)
        self.assertEqual(run.recorded_tolerance_px, 16.0)
        self.assertEqual(run.recorded_deviation_px, 128.0)


class ReportTests(unittest.TestCase):
    """报告：每条样本的归属、两半的数、噪声、体检都要在里面。"""

    def _report(self) -> dict:
        samples = [
            _sample("a1", split=SPLIT_TUNING),
            _sample("b1", split=SPLIT_EVAL, flip=True, coarse_differs=True),
        ]
        tuning = tune_gate_params(samples[:1], RECORDED_TOLERANCE, RECORDED_DEVIATION)
        result_a = evaluate_half(
            SPLIT_TUNING,
            samples[:1],
            RECORDED_TOLERANCE,
            RECORDED_TOLERANCE,
            RECORDED_DEVIATION,
            RECORDED_DEVIATION,
        )
        result_b = evaluate_half(
            SPLIT_EVAL,
            samples[1:],
            RECORDED_TOLERANCE,
            RECORDED_TOLERANCE,
            RECORDED_DEVIATION,
            RECORDED_DEVIATION,
        )
        split = split_samples({"windows/text": ["a1", "b1"]})
        run = load_recorded_run_from_payload(_recorded_payload([], []))
        return build_report(run, ".scratch/sspro", split, samples, tuning, result_a, result_b)

    def test_every_sample_carries_its_half(self):
        report = self._report()
        self.assertEqual(report["split"]["assignment"], {"a1": "A", "b1": "B"})
        self.assertEqual(report["split"]["counts"], {"A": 1, "B": 1})

    def test_report_states_which_delta_is_the_conclusion(self):
        report = self._report()
        self.assertTrue(report["conclusion"]["delta_pp_on_a_is_tuning_process_only"])
        self.assertIn("结论", report["conclusion"]["rule"])

    def test_report_carries_the_comparability_health_checks(self):
        report = self._report()
        self.assertIn("coarse_shared_a", report["comparability"])
        self.assertIn("coarse_shared_b", report["comparability"])
        self.assertEqual(report["comparability"]["model_calls"]["replayed"], 4)

    def test_markdown_has_the_split_the_sweep_and_the_samples(self):
        markdown = render_report(self._report())
        self.assertIn("切分", markdown)
        self.assertIn("调参", markdown)
        self.assertIn("a1", markdown)
        self.assertIn("b1", markdown)


def load_recorded_run_from_payload(payload: dict):
    """把构造好的记录写进临时文件再读回来（走真实的读盘路径）。"""
    with TemporaryDirectory() as directory:
        path = Path(directory) / "sspro-report.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return load_recorded_run(path)


if __name__ == "__main__":
    unittest.main()

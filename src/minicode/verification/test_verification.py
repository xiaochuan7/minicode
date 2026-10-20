"""工单 M11 的验收标准（**外部行为**单测）。

七条验收标准逐条对应下面的测试类：

* :class:`ExpectationAssertionTest` —— AC 1（期望断言可判定）；
* :class:`DefaultCheckTest` —— AC 2（没有期望时的默认校验）；
* :class:`SemanticBoundaryTest` —— AC 3（两条语义边界）；
* :class:`TimingTest` —— AC 4（三段耗时齐全）；
* :class:`FailureStatisticsTest` —— AC 5（三类失败分开统计）；
* :class:`MeansTest` —— AC 6（验证手段的优先级 + 模型不参与裁判）；
* :class:`ActionLogTest` —— AC 7（追踪记录走旁路 + 动作日志字段）。

只断言**对外可见的契约字段**（测试决策 §2），不测内部算法。
"""

from __future__ import annotations

import unittest

from minicode.action.fakes import (
    FakeCaptureSource,
    FakeControlTree,
    FakeInputInjector,
    VirtualClock,
)
from minicode.action.ports import ControlCandidate
from minicode.action.records import ListTraceSink, build_record
from minicode.action.seam import ActionExecutionSeam, SeamRequest
from minicode.verification.diff import change_rate, frame_signature
from minicode.verification.expect import Expectation, ExpectationKind, Verdict
from minicode.verification.fakes import (
    FakeStateObserver,
    ScriptedScreenshotPort,
    make_frame,
)
from minicode.verification.layer import (
    OUTCOME_CHANGED,
    OUTCOME_EXPECTATION_MET,
    OUTCOME_EXPECTATION_NOT_MET,
    OUTCOME_EXPECTATION_UNKNOWN,
    OUTCOME_NO_CHANGE,
    VerificationLayer,
    VerifyRequest,
)
from minicode.verification.telemetry import TelemetryAccumulator

_WIDTH = 64
_HEIGHT = 48


def _layer(frames, observer=None, clock=None):
    """造一个校验层（虚拟时钟 → 单测毫秒级跑完）。"""
    virtual = clock or VirtualClock()
    return (
        VerificationLayer(
            screenshot_port=ScriptedScreenshotPort(frames),
            observer=observer,
            clock=virtual.time,
            sleeper=virtual.sleep,
        ),
        virtual,
    )


def _verify(layer, request):
    """跑一次校验（**两段式**：先取基线，再判）。"""
    baseline = layer.begin(request)
    return layer.verify(
        VerifyRequest(
            expectation=request.expectation,
            baseline_frame=request.baseline_frame,
            baseline=baseline,
        )
    )


class ExpectationAssertionTest(unittest.TestCase):
    """AC 1：期望断言可判定。"""

    def test_a_state_expectation_that_happened_is_met(self):
        layer, _ = _layer(
            [make_frame(_WIDTH, _HEIGHT)],
            observer=FakeStateObserver(
                sequence=[
                    {"保存": [ControlCandidate(name="保存", enabled=True)]},
                    {"保存": [ControlCandidate(name="保存", enabled=False)]},
                ]
            ),
        )
        result = _verify(
            layer,
            VerifyRequest(
                expectation=Expectation(
                    ExpectationKind.STATE_CHANGE, "保存", enabled=False
                ),
                baseline_frame=make_frame(_WIDTH, _HEIGHT),
            ),
        )
        self.assertTrue(result.satisfied)
        self.assertEqual(result.outcome, OUTCOME_EXPECTATION_MET)
        self.assertEqual(result.means["tree"], Verdict.MET.value)

    def test_a_state_expectation_that_did_not_happen_is_not_met(self):
        layer, _ = _layer(
            [make_frame(_WIDTH, _HEIGHT)],
            observer=FakeStateObserver(
                sequence=[
                    {"保存": [ControlCandidate(name="保存", enabled=True)]},
                    {"保存": [ControlCandidate(name="保存", enabled=True)]},
                ]
            ),
        )
        result = _verify(
            layer,
            VerifyRequest(
                expectation=Expectation(
                    ExpectationKind.STATE_CHANGE, "保存", enabled=False
                ),
                baseline_frame=make_frame(_WIDTH, _HEIGHT),
            ),
        )
        self.assertFalse(result.satisfied)
        self.assertEqual(result.outcome, OUTCOME_EXPECTATION_NOT_MET)
        # 断言失败时能读到**期望内容**与**实际观测的差异**。
        self.assertIn("保存", result.expected)
        self.assertIn("enabled=True", result.observed)
        self.assertIn("期望", result.diff)
        self.assertIn("实际", result.diff)

    def test_a_text_expectation_is_judged_by_the_text(self):
        layer, _ = _layer(
            [make_frame(_WIDTH, _HEIGHT)],
            observer=FakeStateObserver(
                sequence=[
                    {"已保存": []},
                    {"已保存": [ControlCandidate(name="已保存")]},
                ]
            ),
        )
        result = _verify(
            layer,
            VerifyRequest(
                expectation=Expectation(ExpectationKind.TEXT_APPEARS, "已保存"),
                baseline_frame=make_frame(_WIDTH, _HEIGHT),
            ),
        )
        self.assertTrue(result.satisfied)
        self.assertEqual(result.means["text"], Verdict.MET.value)

    def test_a_text_that_was_there_already_is_not_an_appearance(self):
        """本来就在的文本**不算**「出现」了。"""
        layer, _ = _layer(
            [make_frame(_WIDTH, _HEIGHT)],
            observer=FakeStateObserver(
                sequence=[
                    {"已保存": [ControlCandidate(name="已保存")]},
                    {"已保存": [ControlCandidate(name="已保存")]},
                ]
            ),
        )
        result = _verify(
            layer,
            VerifyRequest(
                expectation=Expectation(ExpectationKind.TEXT_APPEARS, "已保存"),
                baseline_frame=make_frame(_WIDTH, _HEIGHT),
            ),
        )
        self.assertFalse(result.satisfied)
        self.assertEqual(result.outcome, OUTCOME_EXPECTATION_NOT_MET)

    def test_a_missing_text_expectation_fails(self):
        layer, _ = _layer(
            [make_frame(_WIDTH, _HEIGHT)],
            observer=FakeStateObserver(sequence=[{}, {}]),
        )
        result = _verify(
            layer,
            VerifyRequest(
                expectation=Expectation(ExpectationKind.TEXT_APPEARS, "已保存"),
                baseline_frame=make_frame(_WIDTH, _HEIGHT),
            ),
        )
        self.assertFalse(result.satisfied)
        self.assertEqual(result.outcome, OUTCOME_EXPECTATION_NOT_MET)

    def test_an_unreadable_tree_makes_the_assertion_unknown_not_failed(self):
        layer, _ = _layer(
            [make_frame(_WIDTH, _HEIGHT)],
            observer=FakeStateObserver(error=OSError("读树失败")),
        )
        result = _verify(
            layer,
            VerifyRequest(
                expectation=Expectation(
                    ExpectationKind.STATE_CHANGE, "保存", enabled=False
                ),
                baseline_frame=make_frame(_WIDTH, _HEIGHT),
            ),
        )
        # 判不了 ≠ 不成立。
        self.assertIsNone(result.satisfied)
        self.assertEqual(result.outcome, OUTCOME_EXPECTATION_UNKNOWN)


class DefaultCheckTest(unittest.TestCase):
    """AC 2：没有期望时的默认校验。"""

    def test_a_changed_screen_is_reported_as_changed_and_settled(self):
        changed = make_frame(_WIDTH, _HEIGHT, changed_blocks=[0])
        layer, _ = _layer([changed, changed, changed])
        result = layer.verify(VerifyRequest(baseline_frame=make_frame(_WIDTH, _HEIGHT)))
        self.assertEqual(result.outcome, OUTCOME_CHANGED)
        self.assertTrue(result.change.reacted)
        self.assertTrue(result.change.settled)
        self.assertGreaterEqual(result.change.change_rate, 0.01)

    def test_an_unchanged_screen_is_reported_as_no_change_after_two_quiet_frames(self):
        layer, virtual = _layer([make_frame(_WIDTH, _HEIGHT)])
        result = layer.verify(VerifyRequest(baseline_frame=make_frame(_WIDTH, _HEIGHT)))
        self.assertEqual(result.outcome, OUTCOME_NO_CHANGE)
        self.assertFalse(result.change.reacted)
        # 「变化率低于 1% 且持续两帧」→ 两帧就收工。
        self.assertEqual(result.change.frames, 2)
        self.assertEqual(len(virtual.sleeps), 1)

    def test_the_threshold_is_one_percent_of_the_eight_by_eight_blocks(self):
        before = frame_signature(make_frame(_WIDTH, _HEIGHT))
        # 48 个块（8 列 × 6 行）：变 1 块 = 2.08% ≥ 1% → 有反应。
        one = frame_signature(make_frame(_WIDTH, _HEIGHT, changed_blocks=[0]))
        self.assertGreaterEqual(change_rate(before, one), 0.01)
        self.assertEqual(change_rate(before, before), 0.0)

    def test_the_poll_cadence_is_immediate_then_15ms_then_20hz(self):
        # 连续变化的帧 → 先走 15 ms 节奏，过了 100 ms 走 50 ms（20 Hz）。
        frames = [
            make_frame(_WIDTH, _HEIGHT, changed_blocks=[index]) for index in range(12)
        ]
        frames += [frames[-1], frames[-1]]
        layer, virtual = _layer(frames)
        result = layer.verify(VerifyRequest(baseline_frame=make_frame(_WIDTH, _HEIGHT)))
        self.assertEqual(result.outcome, OUTCOME_CHANGED)
        self.assertTrue(virtual.sleeps)
        for sleep in virtual.sleeps:
            self.assertTrue(
                abs(sleep - 0.015) < 1e-9 or abs(sleep - 0.05) < 1e-9,
                msg=f"轮询节奏只该是 15 ms 或 50 ms，实际 {sleep}",
            )
        # 节奏切换点：累计时间过了 100 ms 之后才用 50 ms。
        elapsed = 0.0
        switched = False
        for sleep in virtual.sleeps:
            elapsed += sleep
            if abs(sleep - 0.05) < 1e-9:
                switched = True
                self.assertGreaterEqual(elapsed, 0.1)
                break
            self.assertLess(elapsed, 0.1 + 0.015)
        self.assertTrue(switched, msg="没走到 20 Hz 的稳态节奏")

    def test_the_thresholds_carry_their_provenance_into_the_record(self):
        """每个阈值都要能说清「这个数是哪来的」。"""
        layer, _ = _layer([make_frame(_WIDTH, _HEIGHT)])
        result = layer.verify(VerifyRequest(baseline_frame=make_frame(_WIDTH, _HEIGHT)))
        params = result.change.to_dict()["params"]
        self.assertEqual(params["block_size"], 8)
        self.assertIn("工单 M11", params["block_size_source"])
        self.assertEqual(params["change_threshold"], 0.01)
        self.assertIn("工单 M11", params["change_threshold_source"])
        self.assertIn("未验证", params["reaction_timeout_source"])


class SemanticBoundaryTest(unittest.TestCase):
    """AC 3：两条语义边界。"""

    def test_clicking_the_wrong_thing_opening_another_dialog_is_not_success(self):
        """画面变了并安定，但期望没成立 → **不得判成功**。"""
        observer = FakeStateObserver(
            sequence=[
                {"保存": [ControlCandidate(name="保存", enabled=True)]},
                {"保存": [ControlCandidate(name="保存", enabled=True)]},
            ]
        )
        changed = make_frame(_WIDTH, _HEIGHT, changed_blocks=[3])
        layer, _ = _layer([changed, changed, changed], observer=observer)
        result = _verify(
            layer,
            VerifyRequest(
                expectation=Expectation(
                    ExpectationKind.STATE_CHANGE, "保存", enabled=False
                ),
                baseline_frame=make_frame(_WIDTH, _HEIGHT),
            ),
        )
        # 像素这一路看到「有反应」，但它**不判具体期望** → 结论仍是不成立。
        self.assertEqual(result.change.outcome, OUTCOME_CHANGED)
        self.assertFalse(result.satisfied)
        self.assertEqual(result.means["pixels"], Verdict.UNKNOWN.value)

    def test_a_ui_that_really_does_not_change_is_not_a_failure(self):
        """界面本来就无变化 → **不得直接判失败**。"""
        layer, _ = _layer([make_frame(_WIDTH, _HEIGHT)])
        result = layer.verify(VerifyRequest(baseline_frame=make_frame(_WIDTH, _HEIGHT)))
        self.assertEqual(result.outcome, OUTCOME_NO_CHANGE)
        self.assertIsNone(result.satisfied)
        self.assertFalse(result.failed)

    def test_the_seam_does_not_turn_no_change_into_a_failed_step(self):
        """缝这一层：默认校验报「界面无变化」时，**动作仍然算成功**。"""
        seam, sink = _build_seam(
            verifier=_layer([make_frame(_WIDTH, _HEIGHT)])[0],
        )
        result = seam.run(SeamRequest(target_description="开始"))
        self.assertTrue(result.ok)
        self.assertIsNone(result.result.error_code)
        verification = sink.traces[-1].verification_result
        self.assertEqual(verification["outcome"], OUTCOME_NO_CHANGE)


class TimingTest(unittest.TestCase):
    """AC 4：三段耗时齐全。"""

    def test_every_step_carries_the_three_stages(self):
        seam, sink = _build_seam()
        seam.run(SeamRequest(target_description="开始", decision_ms=123.0))
        trace = sink.traces[-1]
        self.assertGreaterEqual(trace.locate_ms, 0.0)
        self.assertEqual(trace.decision_ms, 123.0)
        self.assertGreater(trace.execute_verify_ms, 0.0)

    def test_the_action_record_carries_the_three_stages(self):
        seam, sink = _build_seam()
        seam.run(SeamRequest(target_description="开始", decision_ms=123.0))
        record = build_record(sink.traces[-1]).to_dict()
        self.assertIn("locate", record["timings_ms"])
        self.assertIn("decision", record["timings_ms"])
        self.assertIn("execute_verify", record["timings_ms"])
        self.assertEqual(record["timings_ms"]["decision"], 123.0)
        # 单步的三段**占比**也在动作日志里。
        self.assertAlmostEqual(
            sum(record["timings_share"].values()), 1.0, places=6
        )

    def test_the_task_report_shares_the_three_stages(self):
        telemetry = TelemetryAccumulator()
        telemetry.add_step(locate_ms=100.0, decision_ms=200.0, execute_verify_ms=300.0)
        report = telemetry.report()
        self.assertEqual(report.steps, 1)
        self.assertEqual(report.stages_ms["locate"], 100.0)
        self.assertEqual(report.stages_ms["decision"], 200.0)
        self.assertEqual(report.stages_ms["execute_verify"], 300.0)
        self.assertAlmostEqual(report.stage_share["locate"], 1 / 6)
        self.assertAlmostEqual(report.stage_share["decision"], 2 / 6)
        self.assertAlmostEqual(report.stage_share["execute_verify"], 3 / 6)


class FailureStatisticsTest(unittest.TestCase):
    """AC 5：三类失败分开统计。"""

    def test_the_three_failure_classes_are_counted_separately(self):
        telemetry = TelemetryAccumulator()
        telemetry.add_step(failures={"locate": "target_not_found"})
        telemetry.add_step(failures={"execute": "expectation_failed"})
        telemetry.add_step(failures={"execute": "execute_failed"})
        telemetry.add_decision(10.0, failure="program_rejected")
        report = telemetry.report()
        self.assertEqual(report.failures["locate"], 1)
        self.assertEqual(report.failures["execute"], 2)
        self.assertEqual(report.failures["decision"], 1)
        # 占比：各自占全部失败的比例。
        self.assertAlmostEqual(report.failure_share["locate"], 0.25)
        self.assertAlmostEqual(report.failure_share["execute"], 0.5)
        self.assertAlmostEqual(report.failure_share["decision"], 0.25)

    def test_the_task_report_does_not_merge_the_classes_into_one_number(self):
        telemetry = TelemetryAccumulator()
        telemetry.add_step(failures={"locate": "target_not_found"})
        report = telemetry.report()
        self.assertEqual(set(report.failures), {"locate", "decision", "execute"})
        self.assertEqual(report.failures["execute"], 0)

    def test_a_failed_expectation_is_counted_as_an_execution_failure(self):
        observer = FakeStateObserver(
            sequence=[
                {"保存": [ControlCandidate(name="保存", enabled=True)]},
                {"保存": [ControlCandidate(name="保存", enabled=True)]},
            ]
        )
        seam, sink = _build_seam(
            candidates=(
                ControlCandidate(
                    name="保存", control_type="Button", rect=(10, 20, 60, 70)
                ),
            ),
            verifier=_layer([make_frame(_WIDTH, _HEIGHT)], observer=observer)[0],
        )
        result = seam.run(
            SeamRequest(
                target_description="保存",
                expectation=Expectation(
                    ExpectationKind.STATE_CHANGE, "保存", enabled=False
                ),
            )
        )
        self.assertFalse(result.ok)
        self.assertEqual(result.result.error_code.value, "expectation_failed")
        self.assertEqual(sink.traces[-1].failures["execute"], "expectation_failed")
        self.assertIsNone(sink.traces[-1].failures["locate"])


class MeansTest(unittest.TestCase):
    """AC 6：验证手段的优先级 + 模型不参与裁判。"""

    def test_the_tree_judgement_is_not_overridden_by_the_pixel_difference(self):
        """树能判的场景，不以像素差分作为唯一判据。"""
        observer = FakeStateObserver(
            sequence=[
                {"保存": [ControlCandidate(name="保存", enabled=True)]},
                {"保存": [ControlCandidate(name="保存", enabled=True)]},
            ]
        )
        changed = make_frame(_WIDTH, _HEIGHT, changed_blocks=[5])
        layer, _ = _layer([changed, changed], observer=observer)
        result = _verify(
            layer,
            VerifyRequest(
                expectation=Expectation(
                    ExpectationKind.STATE_CHANGE, "保存", enabled=False
                ),
                baseline_frame=make_frame(_WIDTH, _HEIGHT),
            ),
        )
        self.assertEqual(result.means["tree"], Verdict.NOT_MET.value)
        self.assertFalse(result.satisfied)

    def test_the_verdict_is_the_union_of_the_means(self):
        """判据取三者的**或**：任一手段说成立即成立。"""
        observer = FakeStateObserver(
            sequence=[
                {"保存": [ControlCandidate(name="保存", enabled=True)]},
                {"保存": [ControlCandidate(name="保存", enabled=False)]},
            ]
        )
        layer, _ = _layer([make_frame(_WIDTH, _HEIGHT)], observer=observer)
        result = _verify(
            layer,
            VerifyRequest(
                expectation=Expectation(
                    ExpectationKind.STATE_CHANGE, "保存", enabled=False
                ),
                baseline_frame=make_frame(_WIDTH, _HEIGHT),
            ),
        )
        self.assertEqual(result.means["tree"], Verdict.MET.value)
        self.assertTrue(result.satisfied)

    def test_the_model_is_not_the_judge(self):
        """校验层**零模型调用**。"""
        observer = FakeStateObserver(
            sequence=[
                {"保存": [ControlCandidate(name="保存", enabled=True)]},
                {"保存": [ControlCandidate(name="保存", enabled=False)]},
            ]
        )
        layer, _ = _layer([make_frame(_WIDTH, _HEIGHT)], observer=observer)
        result = _verify(
            layer,
            VerifyRequest(
                expectation=Expectation(
                    ExpectationKind.STATE_CHANGE, "保存", enabled=False
                ),
                baseline_frame=make_frame(_WIDTH, _HEIGHT),
            ),
        )
        self.assertEqual(result.model_calls, 0)


class ActionLogTest(unittest.TestCase):
    """AC 7：追踪记录走旁路出口 + 动作日志字段齐全。"""

    def test_the_verification_result_travels_through_the_trace_sink(self):
        seam, sink = _build_seam(verifier=_layer([make_frame(_WIDTH, _HEIGHT)])[0])
        result = seam.run(SeamRequest(target_description="开始"))
        # 追踪记录走**可注入的旁路出口**：结论在那儿读得到。
        self.assertEqual(len(sink.traces), 1)
        self.assertIsNotNone(sink.traces[-1].verification_result)
        # 但它**不进主返回类型**。
        self.assertFalse(hasattr(result, "verification_result"))

    def test_the_action_log_carries_the_required_fields(self):
        candidate = ControlCandidate(
            name="开始",
            control_type="Button",
            rect=(10, 20, 60, 70),
            window_handle=4242,
            window_title="任务栏",
        )
        seam, sink = _build_seam(
            candidates=(candidate,),
            verifier=_layer([make_frame(_WIDTH, _HEIGHT)])[0],
        )
        seam.run(SeamRequest(target_description="开始"))
        record = build_record(sink.traces[-1]).to_dict()
        self.assertTrue(record["timestamp"])
        self.assertEqual(record["action"], "click")
        self.assertEqual(record["target"]["window_handle"], 4242)
        self.assertEqual(record["target"]["window_title"], "任务栏")
        self.assertEqual(record["point"]["space"], "physical-surface")
        self.assertIsNotNone(record["point"]["x"])
        self.assertIsNotNone(record["screenshot_sha256"])
        self.assertIn("before", record["state"])
        self.assertIn("after", record["state"])
        self.assertIn("result", record)
        self.assertTrue(record["result"]["ok"])


class ExpectationParsingTest(unittest.TestCase):
    """用户怎么把一句期望写下来（命令行那条路）。"""

    def test_the_state_form_is_parsed(self):
        from minicode.uia.__main__ import _expectation

        expectation = _expectation("state:保存:不可用")
        self.assertEqual(expectation.kind, ExpectationKind.STATE_CHANGE)
        self.assertEqual(expectation.target, "保存")
        self.assertFalse(expectation.enabled)

    def test_the_text_form_is_parsed(self):
        from minicode.uia.__main__ import _expectation

        expectation = _expectation("text:已保存")
        self.assertEqual(expectation.kind, ExpectationKind.TEXT_APPEARS)
        self.assertEqual(expectation.target, "已保存")
        self.assertIsNone(_expectation(""))


def _build_seam(*, candidates=(), verifier=None):
    """造一个最小闭环的缝（假截图源 / 假控件树 / 假注入）。"""
    sink = ListTraceSink()
    tree_candidates = tuple(candidates) or (
        ControlCandidate(
            name="开始", control_type="Button", rect=(10, 20, 60, 70), window_handle=1
        ),
    )
    seam = ActionExecutionSeam(
        capture_sources=[FakeCaptureSource(width=_WIDTH, height=_HEIGHT)],
        tree=FakeControlTree(tree_candidates),
        injector=FakeInputInjector(),
        trace_sink=sink,
        verifier=verifier,
    )
    return seam, sink


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

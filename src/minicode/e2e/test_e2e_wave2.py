"""**第二波端到端工单的判定口径**（工单 M15）。

只测**外部行为**：工单的形状、条款的判据、模态弹窗的检测、
人工代确认的短路 —— **不测实现细节**。
"""

from __future__ import annotations

import unittest

from minicode.e2e.checks import (
    capability_usable_or_cut_visual,
    channel_and_failure_readable,
    coordinates_stayed_in_window,
    cross_stack_semantics_stable,
    failure_types_separated,
    gate_unlocked_before_unavailable,
    input_channel_recorded,
    modal_handoff_ok,
    page_title_is,
    structure_channel_preferred,
    verification_source_is_structure,
    window_title_matches_picked,
)
from minicode.e2e.modal import WindowRecord, detect_modal
from minicode.e2e.report import build_report
from minicode.e2e.runner import (
    VERDICT_FAIL,
    VERDICT_HANDOFF,
    VERDICT_PASS,
    VERDICT_PENDING_HUMAN,
    VERDICT_SKIP,
    StepOutcome,
    TaskEvidence,
    TaskRunner,
)
from minicode.e2e.tasks import Step, Task
from minicode.e2e.wave2 import WAVE2_TASKS, task_by_id
from minicode.verification.expect import Expectation, ExpectationKind


def _step(**kwargs) -> Step:
    defaults = {
        "id": "S-1",
        "description": "做一件事",
        "expectation": Expectation(ExpectationKind.TEXT_APPEARS, "出现了"),
    }
    defaults.update(kwargs)
    return Step(**defaults)


class _FakeExecutor:
    """**假执行器**：只记「有没有被调用过」与「跑出来的步结果」。"""

    def __init__(self, *, preflight: str = "", steps=None, context=None, stop: str = ""):
        self._preflight = preflight
        self._steps = list(steps or [])
        self._context = dict(context or {})
        self._stop = stop
        self.executed: list[str] = []
        self.closed = False

    def preflight(self) -> str:
        return self._preflight

    def execute(self, step: Step) -> StepOutcome:
        self.executed.append(step.id)
        if self._steps:
            return self._steps.pop(0)
        return StepOutcome(step_id=step.id, ok=True, expectation_met=True)

    def task_context(self):
        return dict(self._context)

    def stop_reason(self) -> str:
        return self._stop

    def handoff(self):
        if not self._stop:
            return None
        return {"reason": self._stop, "window_title": "（假执行器）"}


class Wave2ShapeTests(unittest.TestCase):
    """**工单的形状**：九张票、每一步都有结构断言。"""

    def test_wave2_has_the_nine_tickets(self):
        self.assertEqual(
            [task.id for task in WAVE2_TASKS],
            ["WX-1", "WX-2", "WX-3", "E-1", "E-2", "E-3", "CC-1", "CC-2", "CC-3"],
        )

    def test_every_step_has_a_structural_assertion(self):
        for task in WAVE2_TASKS:
            for step in task.steps:
                self.assertTrue(
                    step.has_structural_assertion,
                    f"{task.id} 的步骤 {step.id} 没有结构断言",
                )

    def test_a_step_without_any_structural_assertion_cannot_be_built(self):
        with self.assertRaises(ValueError):
            Step(id="X-1", description="只有画面变了")

    def test_a_step_may_assert_on_the_window_title_or_the_control_value(self):
        """**窗口标题**与**控件的值**也是结构断言（工单 M15 补的两种）。"""
        step = Step(id="X-2", description="进聊天", expect_window_title="某人")
        self.assertTrue(step.has_structural_assertion)
        other = Step(id="X-3", description="输入文本", expect_control_value="某字")
        self.assertTrue(other.has_structural_assertion)

    def test_the_ticket_lookup_finds_by_id(self):
        self.assertEqual(task_by_id("E-2").id, "E-2")
        with self.assertRaises(KeyError):
            task_by_id("E-9")


class Wave2RunnerTests(unittest.TestCase):
    """**判定口径**：人工代确认短路、跳过不算通过。"""

    def test_the_human_confirm_ticket_is_never_executed(self):
        task = task_by_id("WX-2")
        executor = _FakeExecutor()
        outcome = TaskRunner(executor).run(task)
        self.assertEqual(outcome.verdict, VERDICT_PENDING_HUMAN)
        self.assertEqual(executor.executed, [])

    def test_the_unreachable_environment_is_a_skip(self):
        task = task_by_id("E-1")
        executor = _FakeExecutor(preflight="环境不可达：没装 Playwright")
        outcome = TaskRunner(executor).run(task)
        self.assertEqual(outcome.verdict, VERDICT_SKIP)
        self.assertIn("环境不可达", outcome.reason)
        self.assertEqual(executor.executed, [])

    def test_a_failed_step_fails_the_ticket(self):
        task = Task(
            id="X",
            title="一��",
            app="测试",
            steps=(_step(),),
        )
        executor = _FakeExecutor(
            steps=[StepOutcome(step_id="S-1", ok=False, error_code="locate_failed")]
        )
        outcome = TaskRunner(executor).run(task)
        self.assertEqual(outcome.verdict, VERDICT_FAIL)

    def test_an_undecidable_expectation_is_not_a_pass(self):
        task = Task(id="X", title="一张", app="测试", steps=(_step(),))
        executor = _FakeExecutor(
            steps=[StepOutcome(step_id="S-1", ok=True, expectation_met=None)]
        )
        outcome = TaskRunner(executor).run(task)
        self.assertEqual(outcome.verdict, VERDICT_FAIL)

    def test_a_satisfied_expectation_passes_the_ticket(self):
        task = Task(id="X", title="一张", app="测试", steps=(_step(),))
        executor = _FakeExecutor(
            steps=[StepOutcome(step_id="S-1", ok=True, expectation_met=True)]
        )
        outcome = TaskRunner(executor).run(task)
        self.assertEqual(outcome.verdict, VERDICT_PASS)


class Wave2CheckTests(unittest.TestCase):
    """**条款的判据**。"""

    def test_the_gate_clause_says_the_retry_path_was_not_triggered(self):
        evidence = TaskEvidence(
            task=task_by_id("WX-1"),
            context={
                "gate_probe_attempts": 1,
                "gate_unblock_attempts": 0,
                "gate_first_probe_has_tree": True,
            },
        )
        ok, detail = gate_unlocked_before_unavailable(evidence)
        self.assertFalse(ok)
        self.assertIn("没被触发", detail)

    def test_the_gate_clause_passes_after_unlock_and_backoff(self):
        evidence = TaskEvidence(
            task=task_by_id("WX-1"),
            context={
                "gate_probe_attempts": 3,
                "gate_unblock_attempts": 2,
                "gate_first_probe_has_tree": False,
            },
        )
        ok, detail = gate_unlocked_before_unavailable(evidence)
        self.assertTrue(ok, detail)

    def test_the_capability_clause_accepts_both_branches(self):
        usable = TaskEvidence(
            task=task_by_id("WX-1"),
            context={"capability": "usable", "cut_to_visual": False},
        )
        ok, _ = capability_usable_or_cut_visual(usable)
        self.assertTrue(ok)
        unavailable = TaskEvidence(
            task=task_by_id("WX-1"),
            context={"capability": "unavailable", "cut_to_visual": True},
        )
        ok, _ = capability_usable_or_cut_visual(unavailable)
        self.assertTrue(ok)
        contradiction = TaskEvidence(
            task=task_by_id("WX-1"),
            context={"capability": "usable", "cut_to_visual": True},
        )
        ok, detail = capability_usable_or_cut_visual(contradiction)
        self.assertFalse(ok)
        self.assertIn("矛盾", detail)

    def test_the_window_title_clause_compares_with_the_picked_contact(self):
        evidence = TaskEvidence(
            task=task_by_id("WX-1"),
            context={"picked_contact": "某人", "window_title": "某人"},
        )
        ok, _ = window_title_matches_picked(evidence)
        self.assertTrue(ok)
        other = TaskEvidence(
            task=task_by_id("WX-1"),
            context={"picked_contact": "某人", "window_title": "微信"},
        )
        ok, detail = window_title_matches_picked(other)
        self.assertFalse(ok)
        self.assertIn("微信", detail)

    def test_the_modal_clause_requires_zero_actions_and_a_handoff(self):
        evidence = TaskEvidence(
            task=task_by_id("WX-3"),
            context={
                "modal_detected": True,
                "actions_dispatched": 0,
                "handoff": {
                    "window_title": "微信",
                    "last_action": "点「搜索」",
                    "sent_points": [],
                    "executed_actions": [],
                },
            },
        )
        ok, detail = modal_handoff_ok(evidence)
        self.assertTrue(ok, detail)
        dispatched = TaskEvidence(
            task=task_by_id("WX-3"),
            context={"modal_detected": True, "actions_dispatched": 2, "handoff": {}},
        )
        ok, detail = modal_handoff_ok(dispatched)
        self.assertFalse(ok)
        self.assertIn("2 个动作", detail)

    def test_the_page_title_clause(self):
        evidence = TaskEvidence(task=task_by_id("E-2"), context={"page_title": "验证页"})
        ok, _ = page_title_is("验证页")(evidence)
        self.assertTrue(ok)
        missing = TaskEvidence(task=task_by_id("E-2"), context={})
        ok, detail = page_title_is("验证页")(missing)
        self.assertFalse(ok)
        self.assertIn("没测过", detail)

    def test_the_structure_channel_clause_needs_a_reason(self):
        evidence = TaskEvidence(
            task=task_by_id("E-2"),
            context={"channel": "browser", "channel_choice_reason": "页面结构可用 → 选结构通道"},
        )
        ok, _ = structure_channel_preferred(evidence)
        self.assertTrue(ok)
        without = TaskEvidence(
            task=task_by_id("E-2"),
            context={"channel": "browser", "channel_choice_reason": ""},
        )
        ok, detail = structure_channel_preferred(without)
        self.assertFalse(ok)
        self.assertIn("没测过", detail)

    def test_the_cross_stack_clause_checks_semantics_and_space(self):
        evidence = TaskEvidence(
            task=task_by_id("E-2"),
            context={
                "action_semantics": ["click", "type"],
                "coordinate_spaces": ["physical-surface"],
            },
        )
        ok, detail = cross_stack_semantics_stable(evidence)
        self.assertTrue(ok, detail)
        wrong = TaskEvidence(
            task=task_by_id("E-2"),
            context={
                "action_semantics": ["click", "type"],
                "coordinate_spaces": ["model-space"],
            },
        )
        ok, _ = cross_stack_semantics_stable(wrong)
        self.assertFalse(ok)

    def test_the_input_channel_clause_accepts_both_outcomes(self):
        degraded = TaskEvidence(
            task=task_by_id("E-3"),
            context={
                "input_channel": "browser",
                "input_degraded": True,
                "input_degradation_note": "输入通道退化：浏览器收不到键盘事件",
            },
        )
        ok, detail = input_channel_recorded(degraded)
        self.assertTrue(ok, detail)
        normal = TaskEvidence(
            task=task_by_id("E-3"),
            context={
                "input_channel": "desktop",
                "input_degraded": False,
                "input_degradation_note": "桌面注入直达",
            },
        )
        ok, detail = input_channel_recorded(normal)
        self.assertTrue(ok, detail)
        self.assertIn("未发生退化", detail)

    def test_the_verification_source_clause_rejects_pixels(self):
        evidence = TaskEvidence(
            task=task_by_id("CC-2"), context={"verification_source": "uia"}
        )
        ok, _ = verification_source_is_structure(evidence)
        self.assertTrue(ok)
        pixels = TaskEvidence(
            task=task_by_id("CC-2"), context={"verification_source": "pixels"}
        )
        ok, _ = verification_source_is_structure(pixels)
        self.assertFalse(ok)

    def test_the_failure_types_clause_locates_the_failing_step(self):
        steps = (
            StepOutcome(step_id="A", ok=True, failures={"locate": None, "decision": None, "execute": None}),
            StepOutcome(
                step_id="B",
                ok=False,
                error_code="locate_failed",
                failures={"locate": "locate_failed", "decision": None, "execute": None},
            ),
        )
        evidence = TaskEvidence(task=task_by_id("CC-3"), steps=steps)
        ok, detail = failure_types_separated(evidence)
        self.assertFalse(ok)
        self.assertIn("B", detail)

    def test_the_coordinate_clause_rejects_a_moved_landing(self):
        moved = TaskEvidence(
            task=task_by_id("CC-3"),
            context={
                "coordinate_windows": [
                    {"step_id": "CC-3.1", "expected": "CC Switch", "actual": "别的窗口", "matched": False}
                ],
                "window_pinned": True,
            },
        )
        ok, detail = coordinates_stayed_in_window(moved)
        self.assertFalse(ok)
        self.assertIn("别的窗口", detail)
        stayed = TaskEvidence(
            task=task_by_id("CC-3"),
            context={
                "coordinate_windows": [
                    {"step_id": "CC-3.1", "expected": "CC Switch", "actual": "CC Switch", "matched": True}
                ],
                "window_pinned": True,
            },
        )
        ok, detail = coordinates_stayed_in_window(stayed)
        self.assertTrue(ok, detail)

    def test_the_channel_clause_needs_both_readings(self):
        steps = (StepOutcome(step_id="A", ok=True, channel="browser", failure_type=""),)
        evidence = TaskEvidence(task=task_by_id("E-1"), steps=steps)
        ok, detail = channel_and_failure_readable(evidence)
        self.assertFalse(ok)
        self.assertIn("A", detail)


class Wave2ScoringTests(unittest.TestCase):
    """**记分口径**（对齐公开基准）：模态弹窗那一票**不许计成自动通过**。"""

    #: WX-3 的条款（模态弹窗在场 → 零动作下发 + 交人）**都成立**时的读数。
    _MODAL_CONTEXT = {
        "modal_detected": True,
        "actions_dispatched": 0,
        "stop_reason": "modal_dialog",
        "handoff": {
            "window_title": "微信",
            "last_action": "",
            "sent_points": [],
            "executed_actions": [],
        },
    }

    def test_the_modal_dialog_ticket_is_a_handoff_not_an_auto_pass(self):
        """WX-3 撞上模态弹窗 → **合规停手**：进分母、记 0 分（条款全成立也是）。"""
        executor = _FakeExecutor(stop="modal_dialog", context=dict(self._MODAL_CONTEXT))
        outcome = TaskRunner(executor).run(task_by_id("WX-3"))
        self.assertEqual(outcome.verdict, VERDICT_HANDOFF)
        self.assertEqual(outcome.stop_reason, "modal_dialog")
        #: 条款本身是成立的（证据在报告里），但**判定不是通过**。
        self.assertTrue(all(clause.ok for clause in outcome.clauses))

    def test_the_handoff_is_counted_apart_from_the_manual_run(self):
        """「人工代跑」（WX-2）与「合规停手」（WX-3）是**两个计数**。"""
        manual = TaskRunner(_FakeExecutor()).run(task_by_id("WX-2"))
        executor = _FakeExecutor(stop="modal_dialog", context=dict(self._MODAL_CONTEXT))
        handoff = TaskRunner(executor).run(task_by_id("WX-3"))
        report = build_report([manual, handoff])
        self.assertEqual(report["human_confirm"]["count"], 1)
        self.assertEqual(report["handoff"]["count"], 1)
        self.assertEqual(report["auto"]["passed"], 0)
        #: 合规停手进分母（0 分）；人工代跑不进分母。
        self.assertEqual(report["auto"]["denominator"], 1)
        self.assertEqual(report["auto"]["pass_rate"], 0.0)


class ModalDetectionTests(unittest.TestCase):
    """**模态弹窗的检测**（纯函数，不碰真桌面）。"""

    def _records(self, **overrides) -> list[WindowRecord]:
        base = WindowRecord(
            hwnd=100,
            title="微信",
            class_name="Qt51514QWindowIcon",
            pid=42,
            owner_hwnd=0,
            enabled=True,
            visible=True,
        )
        return [base, overrides.get("other", WindowRecord(0, "", "", 0, 0, True, False))]

    def test_a_disabled_host_window_means_a_modal_is_on_top(self):
        records = self._records()
        records[0] = WindowRecord(100, "微信", "Qt51514QWindowIcon", 42, 0, False, True)
        finding = detect_modal(records, 100)
        self.assertIsNotNone(finding)
        self.assertIn("被禁用", finding.reason)

    def test_an_owned_top_level_window_is_the_dialog(self):
        records = self._records(
            other=WindowRecord(200, "更新提示", "#32770", 42, 100, True, True)
        )
        finding = detect_modal(records, 100)
        self.assertIsNotNone(finding)
        self.assertEqual(finding.hwnd, 200)
        self.assertEqual(finding.title, "更新提示")

    def test_no_modal_when_nothing_else_is_there(self):
        self.assertIsNone(detect_modal(self._records(), 100))

    def test_an_unrelated_process_does_not_count(self):
        records = self._records(
            other=WindowRecord(300, "记事本", "Notepad", 999, 100, True, True)
        )
        self.assertIsNone(detect_modal(records, 100))

    def test_an_unknown_target_window_is_not_a_modal(self):
        self.assertIsNone(detect_modal(self._records(), 999))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

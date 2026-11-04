"""Completeness 判定器的单测：两段式、直接证据、界面稳定、驳回写「缺哪条」。"""

from __future__ import annotations

import unittest

from minicode.reflection.completeness import (
    CompletenessChecker,
    CompletenessVerdict,
    Criterion,
    CriterionResult,
    criterion_of,
)


def _criterion_ok(name="file-diff"):
    return criterion_of(
        name=name,
        predicate=lambda ctx: ctx["file_content"] == "expected",
        evidence_fn=lambda ctx: f"diff={ctx['file_content']!r}",
    )


class TwoStageTest(unittest.TestCase):
    """第一段每步自查（零调用）；第二段 done() 时逐条核验。"""

    def test_all_criteria_pass_and_ui_stable_accepts(self):
        checker = CompletenessChecker([_criterion_ok()])
        checker.note_observation("fp-A")
        checker.note_observation("fp-A")  # 界面稳定
        context = {"file_content": "expected"}
        self.assertTrue(checker.in_progress_check(context))
        verdict = checker.final_gate(context)
        self.assertIsInstance(verdict, CompletenessVerdict)
        self.assertTrue(verdict.accepted)
        self.assertEqual(verdict.missing, ())
        self.assertTrue(verdict.ui_stable)
        self.assertEqual(verdict.per_criterion[0]["evidence"], "diff='expected'")

    def test_failed_criterion_rejects_and_lists_missing_evidence(self):
        checker = CompletenessChecker([_criterion_ok()])
        checker.note_observation("fp-A")
        checker.note_observation("fp-A")
        verdict = checker.final_gate({"file_content": "stale-buffer"})
        self.assertFalse(verdict.accepted)
        self.assertEqual(len(verdict.missing), 1)
        self.assertIn("file-diff", verdict.missing[0])

    def test_unstable_ui_rejects_even_when_criteria_pass(self):
        checker = CompletenessChecker([_criterion_ok()])
        checker.note_observation("fp-A")
        checker.note_observation("fp-B")  # 界面仍在变
        verdict = checker.final_gate({"file_content": "expected"})
        self.assertFalse(verdict.accepted)
        self.assertFalse(verdict.ui_stable)
        self.assertTrue(any("界面稳定" in item for item in verdict.missing))

    def test_criterion_exception_counts_as_not_ok(self):
        def boom(context):
            raise RuntimeError("读数端口炸了")

        checker = CompletenessChecker([Criterion(name="boom", verify=boom)])
        checker.note_observation("fp")
        checker.note_observation("fp")
        verdict = checker.final_gate({})
        self.assertFalse(verdict.accepted)
        self.assertIn("判据异常", verdict.missing[0])

    def test_empty_criteria_is_a_config_error(self):
        with self.assertRaises(ValueError):
            CompletenessChecker([])

    def test_criteria_list_is_frozen_after_registration(self):
        """判据开跑前写死（预登记口径）：登记后改清单不影响本实例。"""
        first = [_criterion_ok()]
        checker = CompletenessChecker(first)
        first.append(_criterion_ok(name="late-add"))
        self.assertEqual(len(checker.criteria), 1)


class SideEffectTest(unittest.TestCase):
    """副作用类动作（保存/导出/发送）必须有可见确认。"""

    def test_side_effect_without_evidence_is_rejected(self):
        def verify(ctx):
            return CriterionResult(ok=bool(ctx.get("saved")), evidence="")

        checker = CompletenessChecker([Criterion(name="save", verify=verify, side_effect=True)])
        checker.note_observation("fp")
        checker.note_observation("fp")
        verdict = checker.final_gate({"saved": True, "evidence": ""})
        self.assertFalse(verdict.accepted)
        self.assertTrue(any("缺可见确认" in item for item in verdict.missing))

    def test_side_effect_with_evidence_passes(self):
        def verify(ctx):
            return CriterionResult(ok=bool(ctx.get("saved")), evidence="标题栏出现 * 消失")

        checker = CompletenessChecker([Criterion(name="save", verify=verify, side_effect=True)])
        checker.note_observation("fp")
        checker.note_observation("fp")
        verdict = checker.final_gate({"saved": True})
        self.assertTrue(verdict.accepted)


class ZeroModelCallTest(unittest.TestCase):
    """第一段零模型调用：判定器结构上没有模型端口。"""

    def test_checker_has_no_model_attribute(self):
        checker = CompletenessChecker([_criterion_ok()])
        self.assertFalse(hasattr(checker, "model"))
        self.assertFalse(hasattr(checker, "client"))
        self.assertFalse(hasattr(checker, "complete"))

    def test_advice_suggests_wait_not_redo_on_unstable_ui(self):
        checker = CompletenessChecker([_criterion_ok()])
        checker.note_observation("A")
        checker.note_observation("B")
        verdict = checker.final_gate({"file_content": "expected"})
        self.assertIn("先 wait 再复查", verdict.advice)


if __name__ == "__main__":
    unittest.main()

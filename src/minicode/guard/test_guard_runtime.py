"""护栏 4（预算）与护栏 5（连续失败熔断）的账本单测（W2）。

时间用**假钟**驱动（不 sleep），熔断用同目标/异目标的序列区分「连续」口径。
"""

import unittest

from minicode.guard.policy import GuardBudget
from minicode.guard.runtime import BudgetExceeded, BudgetLedger, FailureFuse


class FakeClock:
    """假钟：手动推进（秒）。"""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class BudgetLedgerTest(unittest.TestCase):
    """护栏 4：步数 / 时长 / 模型调用，任一超限即停。"""

    def test_a_fresh_ledger_is_within_budget(self):
        clock = FakeClock()
        ledger = BudgetLedger(budget=GuardBudget(), clock=clock)
        self.assertIsNone(ledger.check())

    def test_step_budget_exhaustion_stops_with_a_dimension(self):
        clock = FakeClock()
        ledger = BudgetLedger(budget=GuardBudget(max_steps=2), clock=clock)
        ledger.record_step(model_calls=1)
        self.assertIsNone(ledger.check())
        ledger.record_step(model_calls=0)
        exceeded = ledger.check()
        self.assertIsNotNone(exceeded)
        self.assertEqual(exceeded.dimension, "steps")

    def test_wall_clock_budget_exhaustion_uses_the_injected_clock(self):
        clock = FakeClock()
        ledger = BudgetLedger(
            budget=GuardBudget(max_seconds=10.0), clock=clock
        )
        clock.advance(10.0)
        exceeded = ledger.check()
        self.assertIsNotNone(exceeded)
        self.assertEqual(exceeded.dimension, "seconds")
        clock.advance(0.5)
        self.assertGreater(ledger.elapsed_s(), 10.0)

    def test_model_call_budget_accumulates_across_steps(self):
        clock = FakeClock()
        ledger = BudgetLedger(budget=GuardBudget(max_model_calls=3), clock=clock)
        ledger.record_step(model_calls=2)
        self.assertIsNone(ledger.check())
        ledger.record_step(model_calls=1)
        exceeded = ledger.check()
        self.assertIsNotNone(exceeded)
        self.assertEqual(exceeded.dimension, "model_calls")

    def test_the_ledger_snapshot_carries_the_budget_shape(self):
        clock = FakeClock()
        ledger = BudgetLedger(budget=GuardBudget(max_steps=5), clock=clock)
        ledger.record_step(model_calls=2)
        snapshot = ledger.to_dict()
        self.assertEqual(snapshot["steps"], 1)
        self.assertEqual(snapshot["model_calls"], 2)
        self.assertEqual(snapshot["max_steps"], 5)


class FailureFuseTest(unittest.TestCase):
    """护栏 5：同一目标连续 2 次失败即停；换目标打断「连续」。"""

    def test_two_consecutive_failures_on_the_same_target_blow_the_fuse(self):
        fuse = FailureFuse(threshold=2)
        fuse.record("记事本", ok=False)
        self.assertIsNone(fuse.check())
        fuse.record("记事本", ok=False)
        blown = fuse.check()
        self.assertIsNotNone(blown)
        self.assertIn("记事本", blown)

    def test_a_success_resets_the_consecutive_count(self):
        fuse = FailureFuse(threshold=2)
        fuse.record("记事本", ok=False)
        fuse.record("记事本", ok=True)
        fuse.record("记事本", ok=False)
        self.assertIsNone(fuse.check())

    def test_a_different_target_interrupts_the_streak(self):
        fuse = FailureFuse(threshold=2)
        fuse.record("记事本", ok=False)
        fuse.record("计算器", ok=False)
        self.assertIsNone(fuse.check())

    def test_once_blown_the_fuse_stays_blown_for_the_run(self):
        fuse = FailureFuse(threshold=2)
        fuse.record("记事本", ok=False)
        fuse.record("记事本", ok=False)
        self.assertIsNotNone(fuse.check())
        fuse.record("记事本", ok=True)  # 成功也不恢复：熔断是本次运行的终态
        self.assertIsNotNone(fuse.check())

    def test_budget_exceeded_exception_carries_its_dimension(self):
        error = BudgetExceeded("steps", "已用 30 步，上限 30")
        self.assertEqual(error.dimension, "steps")
        self.assertIn("30", str(error))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

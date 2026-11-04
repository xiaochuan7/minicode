"""VisionCallGuard 的单测：耗尽冷却、连续升级、成功清零、累计口径。"""

from __future__ import annotations

import unittest

from minicode.model.call_guard import CooldownPolicy, VisionCallGuard


class GuardTest(unittest.TestCase):
    def test_cooldown_in_range_and_counted(self):
        sleeps: list[float] = []
        guard = VisionCallGuard(
            CooldownPolicy(min_s=60.0, max_s=120.0),
            sleep=sleeps.append,
            rand=lambda: 0.5,
            clock=lambda: 0.0,
        )
        delay = guard.on_exhausted("429")
        self.assertAlmostEqual(delay, 90.0)
        self.assertEqual(sleeps, [90.0])
        self.assertEqual(guard.exhausted_events, 1)
        self.assertEqual(guard.consecutive_exhaustions, 1)
        self.assertFalse(guard.paused)

    def test_pause_after_threshold_consecutive_exhaustions(self):
        guard = VisionCallGuard(
            CooldownPolicy(min_s=1.0, max_s=2.0, pause_threshold=5),
            sleep=lambda s: None,
            rand=lambda: 0.0,
            clock=lambda: 0.0,
        )
        for _ in range(4):
            guard.on_exhausted()
            self.assertFalse(guard.paused)
        guard.on_exhausted()
        self.assertTrue(guard.paused)
        self.assertEqual(guard.exhausted_events, 5)

    def test_success_resets_consecutive_but_keeps_totals(self):
        guard = VisionCallGuard(
            CooldownPolicy(min_s=1.0, max_s=2.0, pause_threshold=3),
            sleep=lambda s: None,
            rand=lambda: 0.0,
            clock=lambda: 0.0,
        )
        guard.on_exhausted()
        guard.on_exhausted()
        guard.on_success()
        self.assertEqual(guard.consecutive_exhaustions, 0)
        self.assertEqual(guard.exhausted_events, 2)  # 累计口径不清零
        guard.on_exhausted()
        self.assertFalse(guard.paused)  # 连续才升级

    def test_cooldown_total_uses_clock_deltas(self):
        ticks = iter([0.0, 10.0, 10.0, 25.0])  # 两次冷却各 10s、15s
        guard = VisionCallGuard(
            CooldownPolicy(min_s=1.0, max_s=2.0),
            sleep=lambda s: None,
            rand=lambda: 0.0,
            clock=lambda: next(ticks),
        )
        guard.on_exhausted()
        guard.on_exhausted()
        self.assertAlmostEqual(guard.cooldown_total_s, 25.0)


if __name__ == "__main__":
    unittest.main()

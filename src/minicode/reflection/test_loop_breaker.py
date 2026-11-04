"""Loop Breaker 的单测：双计数器语义、三层触发、落盘纪律、三个分开的数。

**强制触发的合成测试**（工单 5.1 要求）：构造卡死场景验证「策略本身能触发」，
与「现实里多久触发一次」解耦 —— 触发率的数据要靠真跑的计数器分布说话。
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from minicode.reflection.loop_breaker import (
    DigestComparator,
    LoopBreaker,
    LoopThresholds,
    PhashComparator,
)


class _StaticComparator(DigestComparator):
    """固定判等的假比较器（确定性合成场景）。"""


class _Loop:
    """合成步序的支架：走一步 = 同键动作 + 观测无变化。"""

    def __init__(self, breaker):
        self.breaker = breaker
        self.step = 0

    def step_with(self, action_key, before, after):
        self.step += 1
        return self.breaker.observe(
            step=self.step,
            action_key=action_key,
            obs_before=before,
            obs_after=after,
        )


class ActionRepeatTest(unittest.TestCase):
    """层 1：同键动作 × 2 + 观测无变化 → 窗口填满即触发。"""

    def test_two_identical_actions_with_no_change_trigger_level_1(self):
        breaker = LoopBreaker()
        loop = _Loop(breaker)
        # 步 1：动作 A，屏幕 X → X（无变化）→ n_a=1，未触发。
        v1 = loop.step_with("click@100,200", "X", "X")
        self.assertEqual(v1.action_repeat_count, 1)
        self.assertIsNone(v1.level)
        # 步 2：动作 A，屏幕 X → X → n_a=2 → 层 1。
        v2 = loop.step_with("click@100,200", "X", "X")
        self.assertEqual(v2.action_repeat_count, 2)
        self.assertEqual(v2.level, 1)
        self.assertIn("click@100,200", v2.blacklist_next)
        self.assertTrue(v2.triggered)

    def test_action_change_resets_the_window(self):
        breaker = LoopBreaker()
        loop = _Loop(breaker)
        loop.step_with("click@100,200", "X", "X")
        v2 = loop.step_with("click@300,400", "X", "X")  # 换了动作键
        self.assertEqual(v2.action_repeat_count, 1)
        self.assertIsNone(v2.level)

    def test_observation_change_breaks_the_streak(self):
        breaker = LoopBreaker()
        loop = _Loop(breaker)
        loop.step_with("click@100,200", "X", "Y")  # 有可见变化
        v2 = loop.step_with("click@100,200", "Y", "Z")
        self.assertEqual(v2.action_repeat_count, 0)
        self.assertIsNone(v2.level)


class ObservationRepeatTest(unittest.TestCase):
    """层 2：最近三次观测 ≈ → 更换整体策略。"""

    def test_three_identical_observations_trigger_level_2(self):
        breaker = LoopBreaker()
        loop = _Loop(breaker)
        loop.step_with("click@1,1", "X", "X")
        loop.step_with("click@2,2", "X", "X")  # n_o 此刻 = 2（o1,o2,o3 全 X 但窗口未满）
        v3 = loop.step_with("click@3,3", "X", "X")
        # 动作键每步都换 → n_a=1；观测三次全 X → n_o=3 → 层 2。
        self.assertEqual(v3.action_repeat_count, 1)
        self.assertEqual(v3.obs_repeat_count, 3)
        self.assertEqual(v3.level, 2)

    def test_diverse_observations_never_trigger(self):
        breaker = LoopBreaker()
        loop = _Loop(breaker)
        for i, obs in enumerate(("A", "B", "C", "D", "E"), start=1):
            verdict = loop.step_with(f"act@{i}", obs, obs)
            self.assertIsNone(verdict.level)


class ModelVerdictTest(unittest.TestCase):
    """层 3：前两层未触发时的一次性模型裁决。"""

    def test_forced_gate_with_switch_verdict_gives_one_step_blacklist(self):
        breaker = LoopBreaker(
            model_verdict=lambda action_key: "switch",
            stall_model_verdict=lambda: True,  # 强制触发（合成测试）
        )
        loop = _Loop(breaker)
        verdict = loop.step_with("click@1,1", "X", "Y")
        self.assertEqual(verdict.level, 3)
        self.assertIn("click@1,1", verdict.blacklist_next)
        self.assertEqual(breaker.consume_blacklist(), verdict.blacklist_next)
        self.assertEqual(breaker.consume_blacklist(), "")  # 取后即清（只作用一步）

    def test_keep_verdict_does_not_trigger(self):
        breaker = LoopBreaker(
            model_verdict=lambda action_key: "keep",
            stall_model_verdict=lambda: True,
        )
        loop = _Loop(breaker)
        verdict = loop.step_with("click@1,1", "X", "Y")
        self.assertIsNone(verdict.level)

    def test_no_model_port_never_reaches_level_3(self):
        breaker = LoopBreaker()
        loop = _Loop(breaker)
        verdict = loop.step_with("click@1,1", "X", "Y")
        self.assertIsNone(verdict.level)


class LedgerTest(unittest.TestCase):
    """落盘纪律：每步无条件写；三个数分开。"""

    def test_every_step_is_logged_unconditionally(self):
        records: list[dict] = []
        breaker = LoopBreaker(sink=records.append)
        loop = _Loop(breaker)
        loop.step_with("click@1,1", "A", "B")  # 正常步
        loop.step_with("click@1,1", "B", "B")  # 计数非零
        loop.step_with("click@1,1", "B", "B")  # 触发
        self.assertEqual(len(records), 3)  # 无条件：正常步也有记录
        self.assertEqual(records[0]["level"], None)
        self.assertEqual(records[0]["action_repeat_count"], 0)
        self.assertEqual(records[2]["level"], 1)
        self.assertTrue(all("ts" in r for r in records))

    def test_sink_to_jsonl_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "loop-counters.jsonl"
            breaker = LoopBreaker(sink=lambda r: path.open("a", encoding="utf-8").write(
                json.dumps(r, ensure_ascii=False) + "\n"
            ))
            loop = _Loop(breaker)
            loop.step_with("click@1,1", "X", "X")
            loop.step_with("click@1,1", "X", "X")
            lines = path.read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(lines), 2)
            self.assertEqual(json.loads(lines[1])["action_repeat_count"], 2)

    def test_three_counters_are_separate(self):
        records: list[dict] = []
        breaker = LoopBreaker(sink=records.append)
        loop = _Loop(breaker)
        # 步 1 无变化（encountered=1）→ 步 2 窗口填满触发 → 步 3 观测变化（recovered）。
        loop.step_with("click@1,1", "X", "X")
        self.assertEqual(breaker.stats["encountered"], 1)
        self.assertEqual(breaker.stats["triggered"], 0)
        loop.step_with("click@1,1", "X", "X")
        self.assertEqual(breaker.stats["encountered"], 2)
        self.assertEqual(breaker.stats["triggered"], 1)
        loop.step_with("click@2,2", "X", "Y")  # 触发后观测变化 → 救回
        self.assertEqual(breaker.stats["recovered"], 1)
        self.assertEqual(breaker.stats["encountered"], 3)
        self.assertEqual(breaker.stats["triggered"], 1)

    def test_loop_break_is_never_a_fail_verdict(self):
        """纪律：loop-break ≠ fail —— 裁决结构里没有「失败」语义。"""
        breaker = LoopBreaker()
        loop = _Loop(breaker)
        loop.step_with("click@1,1", "X", "X")
        verdict = loop.step_with("click@1,1", "X", "X")
        self.assertTrue(verdict.triggered)
        self.assertEqual(verdict.level, 1)
        # 触发产出的是「换方式」指令，不是失败判定。
        self.assertIn("换一种交互方式", verdict.blacklist_next)
        self.assertFalse(hasattr(verdict, "failed"))
        self.assertFalse(hasattr(verdict, "is_failure"))


class ComparatorTest(unittest.TestCase):
    """``≈`` 的两个自定度量。"""

    def test_digest_comparator_requires_exact_equality(self):
        comparator = DigestComparator()
        self.assertTrue(comparator("same", "same"))
        self.assertFalse(comparator("same", "other"))

    def test_phash_comparator_uses_hamming_distance(self):
        comparator = PhashComparator(hash_fn=lambda obs: int(obs), max_hamming=8)
        # 0b0001 vs 0b0011 → 汉明距 1 → ≈
        self.assertTrue(comparator(0b0001, 0b0011))
        # 0b00000001 vs 0b11111111 → 汉明距 7 → 仍 ≤ 8
        self.assertTrue(comparator(0b00000001, 0b11111111))
        # 距离 9 → 不 ≈
        self.assertFalse(comparator(0b000000000, 0b111111111))


class ThresholdTest(unittest.TestCase):
    """阈值与窗口是常量，且窗口大小恰等于阈值（论文口径）。"""

    def test_thresholds_match_the_paper(self):
        thresholds = LoopThresholds()
        self.assertEqual(thresholds.action_window, 2)
        self.assertEqual(thresholds.action_repeat, 2)
        self.assertEqual(thresholds.obs_window, 3)
        self.assertEqual(thresholds.obs_repeat, 3)
        self.assertEqual(thresholds.action_window, thresholds.action_repeat)
        self.assertEqual(thresholds.obs_window, thresholds.obs_repeat)


if __name__ == "__main__":
    unittest.main()

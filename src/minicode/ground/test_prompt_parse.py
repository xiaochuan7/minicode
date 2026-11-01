"""`parse_optional_point` 的负出口判定语义（W7 P2 #3 的红/绿测试）。

负出口（返回 ``None``）的**唯一**合法形态是「模型明确说没找到」——
即 JSON 对象**同时带** ``x`` 与 ``y`` 键且值为 ``null``。
其他任何形状都**不许**被说成「没有这个元素」：要么读出坐标，
要么 :class:`GroundingParseError` 显式失败——否则负出口率（评测口径）
被无关形状污染，且下游「干净回落粗定位」的语义被误触发。
"""

from __future__ import annotations

import unittest

from minicode.ground.prompt import GroundingParseError, parse_optional_point


class NegativeExitOnlyForExplicitNullXYTest(unittest.TestCase):
    """只有 `{"x": null, "y": null}` 是负出口；别的形状显式失败。"""

    def test_explicit_null_xy_is_the_negative_exit(self):
        self.assertIsNone(parse_optional_point('{"x": null, "y": null}'))

    def test_a_bbox_object_is_not_a_negative_exit(self):
        """`{"bbox": [...]}` 没有 x/y 键 —— 不是「没找到」，是读不出来。

        旧实现用 ``.get("x") is None`` 判负出口 → 框形状被误判成
        「模型说没找到」，负出口率被污染。
        """
        with self.assertRaises(GroundingParseError):
            parse_optional_point('{"bbox": [1, 2, 3, 4]}')

    def test_an_object_with_other_keys_is_not_a_negative_exit(self):
        with self.assertRaises(GroundingParseError):
            parse_optional_point('{"text": "开始"}')

    def test_a_plain_xy_pair_still_parses(self):
        self.assertEqual(parse_optional_point('{"x": 5, "y": 6}'), (5.0, 6.0))

    def test_truncated_answer_still_fails_loudly(self):
        with self.assertRaises(GroundingParseError):
            parse_optional_point('{"x": 5, "y"')


if __name__ == "__main__":
    unittest.main()

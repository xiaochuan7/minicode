"""落点取整的**统一口径**（W7 P2 #7 的红/绿测试）。

同一份浮点坐标在三条通道（视觉 ground/channel.py、动作缝 action/seam.py、
桌面中心 action/geometry.py）必须取整出**同一个**整数落点。
旧实现三套口径并存：``int(round())``（银行家）、``math.floor``（纯向下）、
``floor(x+0.5)`` —— 偶数宽控件的中心必然是 x.5，跨通道 ±1 px 系统性分歧。
"""

from __future__ import annotations

import unittest

from minicode.action.geometry import center_point, to_physical_point


class UnifiedRoundingTest(unittest.TestCase):
    """floor(x+0.5) 是唯一口径：半点向上、负坐标对称。"""

    def test_half_points_round_up(self):
        # 偶数宽控件中心 = x.5：2.5 → 3（银行家舍入会给 2 —— 这就是分歧源）。
        self.assertEqual(to_physical_point(2.5, 3.5), (3, 4))

    def test_negative_half_points_round_toward_zero_consistently(self):
        # 副屏在主屏左侧时坐标为负：-2.5 → -2（纯 floor 会给 -3，反向偏移）。
        self.assertEqual(to_physical_point(-2.5, -0.5), (-2, 0))

    def test_integers_pass_through(self):
        self.assertEqual(to_physical_point(10.0, -7.0), (10, -7))

    def test_center_point_uses_the_same_convention(self):
        # 宽 5 的控件（left=0, right=5）中心 = 2.5 → 3。
        self.assertEqual(center_point((0, 0, 5, 5)), (3, 3))

    def test_the_three_channels_share_one_function(self):
        """结构性收敛：视觉通道与动作缝都必须引用同一份取整函数。"""
        import inspect
        import sys

        for module_name, relative in (
            ("minicode.ground.channel", "ground/channel.py"),
            ("minicode.action.seam", "action/seam.py"),
        ):
            with self.subTest(module=module_name):
                module = sys.modules.get(module_name) or __import__(
                    module_name, fromlist=["to_physical_point"]
                )
                source = inspect.getsource(module)
                self.assertIn(
                    "to_physical_point",
                    source,
                    f"{relative} 不再引用统一取整口径 —— 落点三套口径要回归了",
                )
                self.assertNotIn(
                    "math.floor(py)",
                    source,
                    f"{relative} 还在用纯 floor（W7 P2 #7 的旧口径）",
                )


if __name__ == "__main__":
    unittest.main()

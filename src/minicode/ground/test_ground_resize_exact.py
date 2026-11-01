"""`_resize_exact` 的混合方向缩放（W7 P2 #5 的红/绿测试）。

T3 放大后的 crop 会被「声明尺寸」再钳一次（服务端上限只钳一条轴），
一轴放大一轴缩小的**混合方向**是真实可达的输入。旧实现把它交给
``downscale_frame`` → 未捕获 ``ValueError`` 穿透整个 locate，
动作层拿到裸异常而不是分类失败。
"""

from __future__ import annotations

import unittest

from minicode.ground.channel import _resize_exact
from minicode.verification.fakes import make_frame


class ResizeExactMixedDirectionTest(unittest.TestCase):
    """混合方向 = 精确出图；纯放大/纯缩小行为不变。"""

    def test_mixed_direction_produces_the_exact_declared_size(self):
        frame = make_frame(800, 400)
        out = _resize_exact(frame, (1000, 300))  # 宽放大、高缩小
        self.assertEqual((out.width, out.height), (1000, 300))

    def test_mixed_direction_other_way_also_exact(self):
        frame = make_frame(800, 400)
        out = _resize_exact(frame, (600, 500))  # 宽缩小、高放大
        self.assertEqual((out.width, out.height), (600, 500))

    def test_pure_downscale_and_pure_upscale_unchanged(self):
        frame = make_frame(800, 400)
        down = _resize_exact(frame, (400, 200))
        self.assertEqual((down.width, down.height), (400, 200))
        up = _resize_exact(frame, (1600, 800))
        self.assertEqual((up.width, up.height), (1600, 800))

    def test_same_size_is_a_noop(self):
        frame = make_frame(800, 400)
        self.assertIs(_resize_exact(frame, (800, 400)), frame)


if __name__ == "__main__":
    unittest.main()

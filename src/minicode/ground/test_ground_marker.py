"""**标记图**的单测（审查报告 Bug #2：BGRA 帧上 draw_stars 越写越短）。

Bug 形状：``_fill_polygon`` 按 ``channels`` 字节切片赋值，但颜色字节只有
BGR 3 字节 —— bytearray 切片赋值允许不等长，BGRA 帧上**每画一个像素整帧
收缩 1 字节**，stride 全部错位；返回的 payload 比 ``width*height*4`` 短，
下游 PNG 编码抛 ``ValueError``。BGR 路径恰好等长所以从未暴露；
无 numpy 机器的真实抓帧路径正是 BGRA。
"""

from __future__ import annotations

import unittest

from minicode.action.ports import CapturedFrame, PixelFormat
from minicode.ground.marker import MARKER_RGB, draw_stars


def _frame(
    width: int = 64,
    height: int = 48,
    pixel_format: PixelFormat = PixelFormat.BGR,
) -> CapturedFrame:
    """一张纯色合成帧（每个像素同一颜色，便于断言「谁被改了、谁没被改」）。"""
    channels = 4 if pixel_format == PixelFormat.BGRA else 3
    return CapturedFrame(
        width=width,
        height=height,
        left=0,
        top=0,
        payload=bytes(((30, 60, 90, 255) if channels == 4 else (30, 60, 90)) * (width * height)),
        backend="fake",
        source="synthetic",
        pixel_format=pixel_format,
    )


class BgrMarkerTests(unittest.TestCase):
    """BGR 路径（原本就对的行为钉死）。"""

    def test_a_bgr_frame_keeps_its_size_and_marks_in_bgr_order(self):
        frame = _frame()
        marked = draw_stars(frame, [(32.0, 24.0)])
        self.assertEqual(len(marked.payload), len(frame.payload))
        self.assertEqual(marked.pixel_format, PixelFormat.BGR)
        # 中心像素被涂成 MARKER_RGB 的 BGR 顺序：(B, G, R) = (180, 105, 255)。
        offset = (24 * 64 + 32) * 3
        self.assertEqual(
            bytes(marked.payload[offset : offset + 3]),
            bytes((MARKER_RGB[2], MARKER_RGB[1], MARKER_RGB[0])),
        )

    def test_the_original_frame_is_not_mutated(self):
        frame = _frame()
        original = bytes(frame.payload)
        draw_stars(frame, [(32.0, 24.0)])
        self.assertEqual(bytes(frame.payload), original)

    def test_points_outside_the_frame_are_ignored(self):
        frame = _frame()
        marked = draw_stars(frame, [(-50.0, -50.0), (500.0, 500.0)])
        self.assertEqual(bytes(marked.payload), bytes(frame.payload))


class BgraMarkerTests(unittest.TestCase):
    """BGRA 路径（Bug #2 的现场：无 numpy 机器的真实抓帧排布）。"""

    def test_a_bgra_frame_keeps_its_exact_size_after_marking(self):
        """**Bug 断言**：画完之后 payload 长度必须一个字节都不少。

        修之前的形状：每画一个像素 bytearray 收缩 1 字节，
        返回的 payload 比 ``width*height*4`` 短。
        """
        frame = _frame(pixel_format=PixelFormat.BGRA)
        marked = draw_stars(frame, [(32.0, 24.0)])
        self.assertEqual(
            len(marked.payload),
            frame.width * frame.height * 4,
            "BGRA 帧画标记后 payload 必须保持 4 通道 stride",
        )

    def test_a_bgra_frame_marks_the_center_pixel_and_keeps_alpha_opaque(self):
        frame = _frame(pixel_format=PixelFormat.BGRA)
        marked = draw_stars(frame, [(32.0, 24.0)])
        offset = (24 * 64 + 32) * 4
        pixel = bytes(marked.payload[offset : offset + 4])
        # BGR 三通道 = MARKER_RGB 反序；alpha 不被破坏（保持 255 不透明）。
        self.assertEqual(pixel, bytes((MARKER_RGB[2], MARKER_RGB[1], MARKER_RGB[0], 255)))

    def test_a_bgra_frame_outside_the_star_keeps_its_original_pixels(self):
        """stride 错位的另一种暴露方式：星星之外的像素**必须原样**。

        修之前：每画一个像素后面的所有字节整体前移 1 字节，
        星星之外的区域也会被改写。
        """
        frame = _frame(pixel_format=PixelFormat.BGRA)
        marked = draw_stars(frame, [(10.0, 10.0)])
        # 远离星星的一个像素（右下角）必须与原帧逐字节一致。
        offset = ((48 - 1) * 64 + 63) * 4
        self.assertEqual(
            bytes(marked.payload[offset : offset + 4]),
            bytes(frame.payload[offset : offset + 4]),
        )
        # 且整帧里不属于星形的像素仍然都是原色 —— 数一遍非原色像素，
        # 它们必须都落在星星的包围盒内。
        radius = 14
        xs = range(max(0, 10 - radius - 2), min(64, 10 + radius + 3))
        ys = range(max(0, 10 - radius - 2), min(48, 10 + radius + 3))
        for y in range(48):
            for x in range(64):
                offset = (y * 64 + x) * 4
                pixel = bytes(marked.payload[offset : offset + 4])
                if pixel != bytes((30, 60, 90, 255)):
                    self.assertIn(x, xs, f"星星之外被改写的像素：({x}, {y})")
                    self.assertIn(y, ys, f"星星之外被改写的像素：({x}, {y})")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

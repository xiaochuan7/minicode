"""**帧 → PNG** 编码的单测（工单 M4：消歧要把画面送给模型）。

只测外部行为：编出来的是不是一张**颜色没反**的 PNG；
像素数与尺寸对不上时**报错**而不是送一张错的画面。
"""

from __future__ import annotations

import unittest
import zlib

from minicode.action.image import encode_frame_base64, encode_png
from minicode.action.ports import CapturedFrame


def _png_pixels(data: bytes) -> tuple[int, int, bytes]:
    """从 PNG 里取出宽、高与**已解出来的 RGB 像素**（自测用）。"""
    assert data.startswith(b"\x89PNG\r\n\x1a\n")
    width = int.from_bytes(data[16:20], "big")
    height = int.from_bytes(data[20:24], "big")
    # 找 IDAT 块（唯一的块），解开滤波（每行一个 0 过滤字节）。
    start = data.index(b"IDAT") + 4
    length = int.from_bytes(data[start - 8 : start - 4], "big")
    raw = zlib.decompress(data[start : start + length])
    stride = width * 3
    pixels = b"".join(raw[row * (stride + 1) + 1 : (row + 1) * (stride + 1)] for row in range(height))
    return width, height, pixels


class PngEncodingTest(unittest.TestCase):
    def test_the_encoded_image_has_the_frame_size(self):
        frame = CapturedFrame(width=2, height=1, payload=bytes(6))
        width, height, _ = _png_pixels(encode_png(frame))
        self.assertEqual((width, height), (2, 1))

    def test_bgr_pixels_are_swapped_into_rgb(self):
        # 一个像素：蓝 10、绿 20、红 30（BGR 顺序）。
        frame = CapturedFrame(width=1, height=1, payload=bytes((10, 20, 30)))
        _, _, pixels = _png_pixels(encode_png(frame))
        self.assertEqual(pixels, bytes((30, 20, 10)))

    def test_bgra_pixels_are_swapped_into_rgb(self):
        # 一个像素：蓝 10、绿 20、红 30、透明度 40。
        frame = CapturedFrame(
            width=1, height=1, payload=bytes((10, 20, 30, 40)), pixel_format="bgra"
        )
        _, _, pixels = _png_pixels(encode_png(frame))
        self.assertEqual(pixels, bytes((30, 20, 10)))

    def test_a_payload_that_does_not_match_the_size_is_refused(self):
        frame = CapturedFrame(width=2560, height=1600, payload=b"\x01" * 64)
        with self.assertRaises(ValueError):
            encode_png(frame)

    def test_an_unusable_frame_is_not_sent(self):
        self.assertIsNone(encode_frame_base64(None))
        self.assertIsNone(
            encode_frame_base64(CapturedFrame(width=2560, height=1600, payload=b"\x01" * 64))
        )

    def test_a_usable_frame_is_sent_as_base64(self):
        import base64

        frame = CapturedFrame(width=1, height=1, payload=bytes((1, 2, 3)))
        encoded = encode_frame_base64(frame)
        self.assertIsNotNone(encoded)
        self.assertTrue(base64.b64decode(encoded).startswith(b"\x89PNG"))


if __name__ == "__main__":
    unittest.main()

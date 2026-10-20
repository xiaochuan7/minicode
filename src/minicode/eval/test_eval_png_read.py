"""**PNG 读**的外部行为单测（工单 M8 的评测侧前置）。

评测量的是「数据集里的 PNG 能不能被还原成像素」——
读错了像素，评测的分数就是假的（画面根本没送对）。
所以这里钉死三件事：

* 编码 → 读的**往返一致**（用仓库自己的编码器产出，再读回来）；
* **行滤波**（PNG 的 5 种 filter）都能正确还原；
* **不支持的格式显式报错**（不静默送一张错的画面）。
"""

from __future__ import annotations

import struct
import unittest
import zlib
from pathlib import Path
from tempfile import TemporaryDirectory

from minicode.action.image import encode_png
from minicode.action.ports import CapturedFrame, PixelFormat
from minicode.eval.png_read import PngError, read_png, read_png_frame

_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def _chunk(tag: bytes, body: bytes) -> bytes:
    return (
        len(body).to_bytes(4, "big")
        + tag
        + body
        + zlib.crc32(tag + body).to_bytes(4, "big")
    )


def _png_bytes(
    width: int,
    height: int,
    rows: list[bytes],
    *,
    color_type: int = 2,
    filter_types: list[int] | None = None,
) -> bytes:
    """手工拼一张 PNG（能指定每行的 filter 类型）。"""
    raw = bytearray()
    for index, row in enumerate(rows):
        filter_type = filter_types[index] if filter_types else 0
        raw.append(filter_type)
        raw += row
    header = struct.pack(">IIBBBBB", width, height, 8, color_type, 0, 0, 0)
    return (
        _PNG_SIGNATURE
        + _chunk(b"IHDR", header)
        + _chunk(b"IDAT", zlib.compress(bytes(raw)))
        + _chunk(b"IEND", b"")
    )


class RoundTripTests(unittest.TestCase):
    """编码 → 读，往返必须逐字节一致。"""

    def test_round_trip_matches_source_pixels(self):
        width, height = 7, 3
        rgb = bytes(
            value
            for index in range(width * height)
            for value in (index * 7 % 256, index * 13 % 256, index * 29 % 256)
        )
        frame = CapturedFrame(
            width=width,
            height=height,
            payload=_bgr(rgb),
            backend="test",
            source="unit-test",
            pixel_format=PixelFormat.BGR,
        )
        with TemporaryDirectory() as directory:
            path = Path(directory) / "frame.png"
            path.write_bytes(encode_png(frame))
            image = read_png(path)
        self.assertEqual((image.width, image.height), (width, height))
        self.assertEqual(image.rgb, rgb)

    def test_read_png_frame_is_bgr_and_keeps_size(self):
        width, height = 4, 2
        rgb = bytes(range(width * height * 3))
        with TemporaryDirectory() as directory:
            path = Path(directory) / "frame.png"
            path.write_bytes(_png_bytes(width, height, [rgb[: width * 3]] * 2))
            frame = read_png_frame(path)
        self.assertEqual((frame.width, frame.height), (width, height))
        self.assertEqual(frame.pixel_format, PixelFormat.BGR)
        self.assertEqual(frame.payload[:3], bytes((2, 1, 0)))
        self.assertEqual(len(frame.payload), width * height * 3)


class FilterTests(unittest.TestCase):
    """PNG 的 5 种行滤波都要能还原。"""

    def test_all_filter_types_decode_to_same_pixels(self):
        width, height = 4, 5
        pixels = bytes((index * 11 % 256) for index in range(width * height * 3))
        rows = [pixels[row * width * 3 : (row + 1) * width * 3] for row in range(height)]
        for filter_type in range(5):
            with TemporaryDirectory() as directory:
                path = Path(directory) / "f.png"
                path.write_bytes(
                    _png_bytes(
                        width,
                        height,
                        _filtered_rows(rows, filter_type),
                        filter_types=[filter_type] * height,
                    )
                )
                image = read_png(path)
            self.assertEqual(image.rgb, pixels, f"filter {filter_type} 解错了")

    def test_sub_filter_reconstructs_left_neighbour(self):
        """filter 1（Sub）：每像素减去左边那个。"""
        width, height = 3, 1
        row = bytes((10, 20, 30, 5, 6, 7, 1, 2, 3))
        with TemporaryDirectory() as directory:
            path = Path(directory) / "sub.png"
            path.write_bytes(_png_bytes(width, height, [row], filter_types=[1]))
            image = read_png(path)
        self.assertEqual(
            image.rgb, bytes((10, 20, 30, 15, 26, 37, 16, 28, 40))
        )


class TwoPathsAgreeTests(unittest.TestCase):
    """**两条解码路径必须逐字节相同**（Pillow 快路径 vs 纯标准库路径）。"""

    def test_pillow_path_matches_pure_python_path(self):
        from minicode.eval.png_read import _read_with_pillow, _read_with_stdlib

        width, height = 9, 5
        pixels = bytes((index * 17 % 256) for index in range(width * height * 3))
        rows = [pixels[row * width * 3 : (row + 1) * width * 3] for row in range(height)]
        with TemporaryDirectory() as directory:
            path = Path(directory) / "both.png"
            path.write_bytes(
                _png_bytes(
                    width,
                    height,
                    _filtered_rows(rows, 4),
                    filter_types=[4] * height,
                )
            )
            fast = _read_with_pillow(path)
            slow = _read_with_stdlib(path)
        if fast is None:
            self.skipTest("本机没有 Pillow，只跑标准库路径")
        self.assertEqual((fast.width, fast.height), (slow.width, slow.height))
        self.assertEqual(fast.rgb, slow.rgb)


class FormatGuardTests(unittest.TestCase):
    """不支持的格式**显式报错**。"""

    def test_grayscale_is_rejected_by_stdlib_path(self):
        """标准库路径只认 RGB / RGBA（数据集就是这两种）。"""
        from minicode.eval.png_read import _read_with_stdlib

        with TemporaryDirectory() as directory:
            path = Path(directory) / "gray.png"
            path.write_bytes(_png_bytes(2, 2, [bytes(2), bytes(2)], color_type=0))
            with self.assertRaises(PngError):
                _read_with_stdlib(path)

    def test_non_png_is_rejected(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "not.png"
            path.write_bytes(b"not a png at all")
            with self.assertRaises(PngError):
                read_png(path)


def _paeth(left: int, up: int, upper_left: int) -> int:
    estimate = left + up - upper_left
    distance_left = abs(estimate - left)
    distance_up = abs(estimate - up)
    distance_upper_left = abs(estimate - upper_left)
    if distance_left <= distance_up and distance_left <= distance_upper_left:
        return left
    if distance_up <= distance_upper_left:
        return up
    return upper_left


def _filtered_rows(rows: list[bytes], filter_type: int, channels: int = 3) -> list[bytes]:
    """按 PNG 的行滤波规则把原始行编码成「滤波后的字节」。"""
    encoded: list[bytes] = []
    previous = bytes(len(rows[0]))
    for row in rows:
        line = bytearray(len(row))
        for index in range(len(row)):
            left = row[index - channels] if index >= channels else 0
            up = previous[index]
            upper_left = previous[index - channels] if index >= channels else 0
            if filter_type == 0:
                predictor = 0
            elif filter_type == 1:
                predictor = left
            elif filter_type == 2:
                predictor = up
            elif filter_type == 3:
                predictor = (left + up) >> 1
            else:
                predictor = _paeth(left, up, upper_left)
            line[index] = (row[index] - predictor) & 0xFF
        encoded.append(bytes(line))
        previous = row
    return encoded


def _bgr(rgb: bytes) -> bytes:
    """RGB → BGR（帧的像素排布）。"""
    out = bytearray(rgb)
    out[0::3], out[2::3] = rgb[2::3], rgb[0::3]
    return bytes(out)


if __name__ == "__main__":
    unittest.main()

"""**PNG 读**（评测侧专用，纯标准库）。

编码那一侧是 :mod:`minicode.action.image`（``encode_png``）；这一侧是它的逆：
**把磁盘上的 PNG 读成像素**。

⚠️ **为什么评测侧要自己读 PNG**：工单 M8 要在公开数据集（ScreenSpot-Pro）
上跑门控评测，而**数据集本身是 PNG 文件** —— 它不是「一帧画面」。
:mod:`minicode.action.ports` 的 ``CapturedFrame`` 装的是**原始像素**，
所以得有人把 PNG 解成像素。

**只用标准库**（``zlib`` 解 IDAT + 自己走一遍 PNG 的行滤波）——
主链路的依赖纪律不允许为了评测引入第三方解码器（工单 M8 AC 7：
评测用的第三方依赖只能是可选附加项）。

支持范围**故意窄**：8 位深、真彩色（RGB）与带 alpha 的真彩色（RGBA）、
非隔行。别的组合**显式报错**（宁可报错也不送一张错的画面给模型）。

**可选加速**：装了 Pillow 时直接用它解（**C 速度**，3840×2160 的图纯 Python
逐字节解要 30 多秒，评测跑不动）。**没有 Pillow 也能跑**（纯标准库路径，
只是慢）—— 工单 M8 AC 7 要求评测用的第三方依赖只能是**可选附加项**，
核心链路仍然只依赖标准库。
"""

from __future__ import annotations

import struct
import zlib
from pathlib import Path

from minicode.action.ports import CapturedFrame, PixelFormat

__all__ = ["PngError", "PngImage", "read_png", "read_png_frame"]

_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"

#: 颜色类型 → 每像素的通道数。
_CHANNELS = {2: 3, 6: 4}


class PngError(ValueError):
    """PNG 读不了（格式不支持 / 文件损坏）。"""


class PngImage:
    """一张读开的 PNG：尺寸 + **RGB** 像素（行优先，每像素 3 字节）。"""

    __slots__ = ("width", "height", "rgb")

    def __init__(self, width: int, height: int, rgb: bytes):
        self.width = width
        self.height = height
        self.rgb = rgb

    def to_frame(self, *, source: str = "") -> CapturedFrame:
        """转成一帧画面（**BGR** 排布 —— 与抓屏后端一致，PNG 编码那一侧会换回来）。"""
        bgr = bytearray(self.rgb)
        bgr[0::3], bgr[2::3] = self.rgb[2::3], self.rgb[0::3]
        return CapturedFrame(
            width=self.width,
            height=self.height,
            left=0,
            top=0,
            payload=bytes(bgr),
            backend="dataset",
            source=source or "png",
            pixel_format=PixelFormat.BGR,
        )


def read_png(path: str | Path) -> PngImage:
    """读一张 PNG → 尺寸 + RGB 像素。"""
    path = Path(path)
    fast = _read_with_pillow(path)
    if fast is not None:
        return fast
    return _read_with_stdlib(path)


def _read_with_pillow(path: Path) -> PngImage | None:
    """装了 Pillow 就用它解（**可选附加项**）；没有 Pillow 返回 ``None``。"""
    try:
        from PIL import Image, UnidentifiedImageError
    except ImportError:  # pragma: no cover - 环境没装 Pillow 时走标准库路径
        return None
    try:
        with Image.open(path) as image:
            rgb = image.convert("RGB")
            return PngImage(rgb.width, rgb.height, rgb.tobytes())
    except (UnidentifiedImageError, OSError) as exc:
        raise PngError(f"读不了这张图（{path}）：{exc}") from exc


def _read_with_stdlib(path: Path) -> PngImage:
    """纯标准库读一张 PNG → 尺寸 + RGB 像素。"""
    data = path.read_bytes()
    if not data.startswith(_PNG_SIGNATURE):
        raise PngError(f"不是 PNG 文件：{path}")
    width = height = 0
    bit_depth = color_type = interlace = 0
    idat = bytearray()
    offset = len(_PNG_SIGNATURE)
    while offset + 8 <= len(data):
        (length,) = struct.unpack(">I", data[offset : offset + 4])
        tag = data[offset + 4 : offset + 8]
        body = data[offset + 8 : offset + 8 + length]
        offset += 12 + length
        if tag == b"IHDR":
            width, height, bit_depth, color_type, _comp, _filt, interlace = struct.unpack(
                ">IIBBBBB", body
            )
        elif tag == b"IDAT":
            idat += body
        elif tag == b"IEND":
            break
    if width <= 0 or height <= 0:
        raise PngError(f"PNG 头不完整：{path}")
    if bit_depth != 8:
        raise PngError(f"只支持 8 位深的 PNG，收到 {bit_depth} 位：{path}")
    if color_type not in _CHANNELS:
        raise PngError(f"只支持 RGB / RGBA 的 PNG，收到颜色类型 {color_type}：{path}")
    if interlace:
        raise PngError(f"不支持隔行 PNG：{path}")
    raw = zlib.decompress(bytes(idat))
    channels = _CHANNELS[color_type]
    stride = width * channels
    pixels = _unfilter(raw, width, height, channels, stride)
    if color_type == 6:
        pixels = _drop_alpha(pixels, width, height)
    return PngImage(width, height, pixels)

def read_png_frame(path: str | Path) -> CapturedFrame:
    """读一张 PNG → 一帧画面（:class:`CapturedFrame`）。"""
    return read_png(path).to_frame(source=str(path))


def _unfilter(raw: bytes, width: int, height: int, channels: int, stride: int) -> bytes:
    """走一遍 PNG 的行滤波（5 种 filter 类型），还原成像素流（纯标准库路径）。"""
    out = bytearray(height * stride)
    previous = bytearray(stride)
    position = 0
    for row in range(height):
        filter_type = raw[position]
        position += 1
        line = bytearray(raw[position : position + stride])
        position += stride
        if len(line) != stride:
            raise PngError("PNG 像素数据长度与尺寸对不上")
        if filter_type == 0:
            pass
        elif filter_type == 1:
            for index in range(channels, stride):
                line[index] = (line[index] + line[index - channels]) & 0xFF
        elif filter_type == 2:
            for index in range(stride):
                line[index] = (line[index] + previous[index]) & 0xFF
        elif filter_type == 3:
            for index in range(stride):
                left = line[index - channels] if index >= channels else 0
                line[index] = (line[index] + ((left + previous[index]) >> 1)) & 0xFF
        elif filter_type == 4:
            for index in range(stride):
                left = line[index - channels] if index >= channels else 0
                up = previous[index]
                upper_left = previous[index - channels] if index >= channels else 0
                estimate = left + up - upper_left
                distance_left = abs(estimate - left)
                distance_up = abs(estimate - up)
                distance_upper_left = abs(estimate - upper_left)
                if distance_left <= distance_up and distance_left <= distance_upper_left:
                    predictor = left
                elif distance_up <= distance_upper_left:
                    predictor = up
                else:
                    predictor = upper_left
                line[index] = (line[index] + predictor) & 0xFF
        else:
            raise PngError(f"未知的 PNG 行滤波类型：{filter_type}")
        out[row * stride : (row + 1) * stride] = line
        previous = line
    return bytes(out)


def _drop_alpha(pixels: bytes, width: int, height: int) -> bytes:
    """RGBA → RGB（丢掉 alpha 通道）。"""
    out = bytearray(width * height * 3)
    out[0::3] = pixels[0::4]
    out[1::3] = pixels[1::4]
    out[2::3] = pixels[2::4]
    return bytes(out)

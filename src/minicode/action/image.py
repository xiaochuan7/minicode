"""把一帧画面编成 PNG（**纯标准库**）。

送模型的那张图要有编码：模型端只收 base64 的 PNG。本模块是
**「一帧 → PNG」的唯一实现**（工单 M4 的消歧要把画面送给模型）。

像素排布随抓屏后端而变：mss（带 numpy）给 **BGR**（3 字节/像素），
没有 numpy 时冻结原型的垫片给 **BGRA**（4 字节/像素）。PNG 要 RGB，
所以这里做一次通道交换 —— **换算只有这一处**。

> 为什么不引第三方库：动作执行缝与模型客户端都是「只靠标准库」的路子
> （``pyproject.toml`` 的 ``core`` 依赖为空），图片编码也不例外。
"""

from __future__ import annotations

import base64
import struct
import zlib

from .ports import CapturedFrame, PixelFormat

__all__ = ["encode_png", "encode_frame_base64"]

#: PNG 文件头（固定 8 字节）。
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"

#: zlib 压缩级别：6 = 默认（够快也够小）。
_COMPRESSION_LEVEL = 6

def encode_png(frame: CapturedFrame) -> bytes:
    """一帧 → PNG 字节。

    帧的像素数与声明尺寸对不上时抛 :class:`ValueError` ——
    **宁可报错也不送一张错的画面**给模型。
    """
    rgb = _rgb_bytes(frame)
    width, height = frame.width, frame.height
    stride = width * 3
    raw = b"".join(
        b"\x00" + rgb[row * stride : (row + 1) * stride] for row in range(height)
    )
    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (
        _PNG_SIGNATURE
        + _chunk(b"IHDR", header)
        + _chunk(b"IDAT", zlib.compress(raw, _COMPRESSION_LEVEL))
        + _chunk(b"IEND", b"")
    )


def encode_frame_base64(frame: CapturedFrame | None) -> str | None:
    """一帧 → base64 的 PNG（**模型端要的就是这个形状**）。

    没有画面（``None``）或画面编不成（像素数与尺寸对不上）时返回 ``None``：
    **送不出图就不送图**，回显里的「实际发送尺寸」因此是空的（不伪造）。
    """
    if frame is None or frame.is_empty:
        return None
    try:
        png = encode_png(frame)
    except ValueError:
        return None
    return base64.b64encode(png).decode("ascii")


def _rgb_bytes(frame: CapturedFrame) -> bytes:
    """帧的像素 → **RGB**（PNG 要的顺序）。"""
    channels = 4 if frame.pixel_format == PixelFormat.BGRA else 3
    expected = frame.width * frame.height * channels
    if len(frame.payload) != expected:
        raise ValueError(
            f"画面像素数与尺寸对不上：{frame.width}x{frame.height}x{channels} "
            f"应为 {expected} 字节，实际 {len(frame.payload)} 字节"
        )
    out = bytearray(frame.payload)
    if channels == 4:
        # BGRA → RGB
        out[0::4] = frame.payload[2::4]
        out[1::4] = frame.payload[1::4]
        out[2::4] = frame.payload[0::4]
        del out[3::4]
    else:
        # BGR → RGB
        out[0::3], out[2::3] = frame.payload[2::3], frame.payload[0::3]
    return bytes(out)


def _chunk(tag: bytes, data: bytes) -> bytes:
    """PNG 的一个块（长度 + 类型 + 数据 + CRC）。"""
    return (
        len(data).to_bytes(4, "big")
        + tag
        + data
        + zlib.crc32(tag + data).to_bytes(4, "big")
    )

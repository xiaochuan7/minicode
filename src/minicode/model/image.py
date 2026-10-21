"""**送模型的那张图**：缩放与尺寸读数。

**为什么缩放由我们自己做**：服务端会把大图缩小（本机实测 MiniMax 端点把
2560 宽的图缩到约 2048 宽），而**服务端缩了多少是不透明的**——
于是「模型发的坐标对应哪一张图」这件事就没有确定的基准。
我们自己把图缩到**声明给模型的那个尺寸**再送出去，声明就等于服务端实际看到的尺寸
（Anthropic 的坐标契约：``display_width_px/height_px`` 说的就是模型发坐标的那一帧）。

⚠️ **本模块不做任何坐标换算**：缩放后一个像素不再等于一个物理像素，
比例由 :class:`minicode.coord.coordinates.FrameGeometry` 的
``model_input_width/height`` 表达（**坐标换算链路只有一个真值来源**）。

**依赖**：numpy 是**可选**的（本仓的既有做法，见 ``minicode.exec.capture``）：
有 numpy 走**盒式平均**（快、画质好），没有就退化成**最近邻**
（纯标准库，慢一些但零依赖）。两种路径的**声明尺寸完全一样**，
坐标契约不受影响。
"""

from __future__ import annotations

from minicode.action.ports import CapturedFrame, PixelFormat

__all__ = ["downscale_frame"]


def downscale_frame(frame: CapturedFrame, width: int, height: int) -> CapturedFrame:
    """把一帧缩到指定尺寸。

    * 只支持**整数倍**缩放（本机整屏 2560×1600 → 1280×800 正好 ×2）；
      不是整数倍 → :class:`ValueError`（**显式失败**，不悄悄用最近邻糊过去）。
    * ``left/top`` **不变**（还是同一块屏幕区域），``source`` 加后缀标出缩放过。
    * 有 numpy 走盒式平均，没有走最近邻（纯标准库）。
    """
    if width <= 0 or height <= 0:
        raise ValueError(f"目标尺寸必须为正数，收到 {width}x{height}")
    if frame.is_empty:
        raise ValueError("画面是 0×0，放大不了")
    factor_x, factor_y = frame.width // width, frame.height // height
    if factor_x < 1 or factor_y < 1:
        raise ValueError(f"只支持缩小：{frame.width}x{frame.height} → {width}x{height}")
    if width * factor_x != frame.width or height * factor_y != frame.height:
        raise ValueError(
            f"只支持整数倍缩放：{frame.width}x{frame.height} → {width}x{height}"
            f"（{frame.width / width:.3f}× / {frame.height / height:.3f}× 不是整数）"
        )
    if factor_x == 1 and factor_y == 1:
        return frame
    channels = 4 if frame.pixel_format == PixelFormat.BGRA else 3
    expected = frame.width * frame.height * channels
    if len(frame.payload) != expected:
        raise ValueError(
            f"画面像素数与尺寸对不上：{frame.width}x{frame.height}x{channels} "
            f"应为 {expected} 字节，实际 {len(frame.payload)} 字节"
        )
    payload = _downscale_payload(
        frame.payload,
        frame.width,
        frame.height,
        width,
        height,
        channels,
        factor_x,
        factor_y,
    )
    return CapturedFrame(
        width=width,
        height=height,
        left=frame.left,
        top=frame.top,
        payload=payload,
        backend=frame.backend,
        source=f"{frame.source}|downscaled",
        pixel_format=frame.pixel_format,
    )


def _downscale_payload(
    payload: bytes,
    src_width: int,
    src_height: int,
    dst_width: int,
    dst_height: int,
    channels: int,
    factor_x: int,
    factor_y: int,
) -> bytes:
    """缩放像素（盒式平均 / 最近邻，二选一）。"""
    try:
        import numpy as np
    except ImportError:
        np = None
    if np is not None:
        array = np.frombuffer(payload, dtype=np.uint8).reshape(
            src_height, src_width, channels
        )
        grouped = array.reshape(
            dst_height, factor_y, dst_width, factor_x, channels
        ).sum(axis=(1, 3), dtype=np.uint32)
        return bytes((grouped // (factor_x * factor_y)).astype(np.uint8).tobytes())
    return _downscale_nearest(
        payload, src_width, src_height, dst_width, dst_height, channels, factor_x, factor_y
    )


def _downscale_nearest(
    payload: bytes,
    src_width: int,
    src_height: int,
    dst_width: int,
    dst_height: int,
    channels: int,
    factor_x: int,
    factor_y: int,
) -> bytes:
    """最近邻缩放（纯标准库；切片都在 C 里跑，够快）。"""
    src_stride = src_width * channels
    dst_stride = dst_width * channels
    out = bytearray(dst_height * dst_stride)
    for y in range(dst_height):
        source_row = (y * factor_y) * src_stride
        row = payload[source_row : source_row + src_stride]
        base = y * dst_stride
        for channel in range(channels):
            out[base + channel : base + dst_stride : channels] = row[
                channel :: channels * factor_x
            ]
    return bytes(out)

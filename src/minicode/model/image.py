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
有 numpy 走**盒式平均**（快、画质好、任意比例都支持），没有就退化成**最近邻**
（纯标准库，慢一些）。两种路径的**声明尺寸完全一样**，坐标契约不受影响。
"""

from __future__ import annotations

from minicode.action.ports import CapturedFrame, PixelFormat

__all__ = ["downscale_frame", "model_input_size_for"]

#: **服务端单侧上限**（物理像素）—— 我们自己缩图时的目标宽度上界。
#:
#: 来源是**实测**：本机把 2560 宽的图发给 MiniMax 端点，服务端把它缩到约
#: **2048** 宽（``docs/00-现状.md`` §6.7 记着这条；请求日志的图像 token 数可复核）。
#: ⚠️ 这是**实测值，不是猜的**；但服务端换了版本就可能变 —— 所以**我们自己**把图
#: 缩到这条线以内，**声明的尺寸就与服务端实际看到的尺寸一致**。
SERVER_MAX_INPUT_WIDTH_PX = 2048


def model_input_size_for(
    width: int, height: int, *, max_width: int = SERVER_MAX_INPUT_WIDTH_PX
) -> tuple[int, int]:
    """**声明给模型的尺寸**：把 ``width×height`` 缩到不超过 ``max_width`` 的那一档。

    * 缩放比例**由整数因子给出**（因子必须同时整除宽高 → 缩放是精确的）；
    * 在所有满足「宽高都能被因子整除」的因子里取**最小**的那个
      （= 缩得最小、画质最好、token 最省）；
    * 找不到可用的因子（宽高互质且都大于上限）→ **返回原尺寸**，
      由调用方决定怎么办（**不静默给一个算不准的数**）。

    本机桌面 2560×1600 → **1280×800**（正好 ×2、16:10 不变形）——
    与 Anthropic 坐标契约里「声明发出去那张图的尺寸」一致。
    """
    if width <= 0 or height <= 0:
        raise ValueError(f"尺寸必须为正数，收到 {width}x{height}")
    for factor in range(1, min(width, height) + 1):
        if width % factor or height % factor:
            continue
        if width // factor <= max_width:
            return width // factor, height // factor
    return width, height


def downscale_frame(frame: CapturedFrame, width: int, height: int) -> CapturedFrame:
    """把一帧缩到指定尺寸（**盒式平均**；没有 numpy 时退化成最近邻）。

    * 支持**任意**比例（不只整数倍）—— ScreenSpot-Pro 里有 3456×2234 这种
      宽高互质、整数倍缩不到上限以下的图；
    * ``left/top`` **不变**（还是同一块屏幕区域），``source`` 加后缀标出缩放过。
    """
    if width <= 0 or height <= 0:
        raise ValueError(f"目标尺寸必须为正数，收到 {width}x{height}")
    if frame.is_empty:
        raise ValueError("画面是 0×0，放大不了")
    if width > frame.width or height > frame.height:
        raise ValueError(
            f"只支持缩小：{frame.width}x{frame.height} → {width}x{height}"
        )
    if (width, height) == (frame.width, frame.height):
        return frame
    channels = 4 if frame.pixel_format == PixelFormat.BGRA else 3
    expected = frame.width * frame.height * channels
    if len(frame.payload) != expected:
        raise ValueError(
            f"画面像素数与尺寸对不上：{frame.width}x{frame.height}x{channels} "
            f"应为 {expected} 字节，实际 {len(frame.payload)} 字节"
        )
    payload = _resize_payload(
        frame.payload,
        frame.width,
        frame.height,
        width,
        height,
        channels,
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


def _resize_payload(
    payload: bytes,
    src_width: int,
    src_height: int,
    dst_width: int,
    dst_height: int,
    channels: int,
) -> bytes:
    """缩放像素（盒式平均 / 最近邻，二选一）。"""
    try:
        import numpy as np
    except ImportError:
        np = None
    if np is not None:
        return _resize_boxes_numpy(
            np, payload, src_width, src_height, dst_width, dst_height, channels
        )
    return _resize_nearest(
        payload, src_width, src_height, dst_width, dst_height, channels
    )


def _resize_boxes_numpy(
    np,
    payload: bytes,
    src_width: int,
    src_height: int,
    dst_width: int,
    dst_height: int,
    channels: int,
) -> bytes:
    """**盒式平均**（任意比例）：每个输出像素 = 它覆盖的源像素块的均值。

    用累积和（cumsum）算矩形块的和 —— O(源像素数)，不逐像素循环。
    """
    array = np.frombuffer(payload, dtype=np.uint8).reshape(
        src_height, src_width, channels
    ).astype(np.uint64)
    # 横向：每个输出列对应源图的一段 [xs[u], xs[u+1])
    xs = (np.arange(dst_width + 1, dtype=np.int64) * src_width) // dst_width
    col_sums = np.concatenate(
        [np.zeros((src_height, 1, channels), dtype=np.uint64), array.cumsum(axis=1)], axis=1
    )
    h_sums = col_sums[:, xs[1:], :] - col_sums[:, xs[:-1], :]
    h_counts = np.diff(xs).astype(np.uint64)
    # 纵向：同理
    ys = (np.arange(dst_height + 1, dtype=np.int64) * src_height) // dst_height
    row_sums = np.concatenate(
        [np.zeros((1, dst_width, channels), dtype=np.uint64), h_sums.cumsum(axis=0)], axis=0
    )
    totals = row_sums[ys[1:], :, :] - row_sums[ys[:-1], :, :]
    counts = (np.diff(ys).astype(np.uint64)[:, None] * h_counts[None, :])[:, :, None]
    return (totals // counts).astype(np.uint8).tobytes()


def _resize_nearest(
    payload: bytes,
    src_width: int,
    src_height: int,
    dst_width: int,
    dst_height: int,
    channels: int,
) -> bytes:
    """最近邻（纯标准库；逐像素取样，慢但一样准）。"""
    src_stride = src_width * channels
    dst_stride = dst_width * channels
    out = bytearray(dst_height * dst_stride)
    for y in range(dst_height):
        source_row = ((y * src_height) // dst_height) * src_stride
        row = payload[source_row : source_row + src_stride]
        base = y * dst_stride
        for x in range(dst_width):
            source_x = (x * src_width) // dst_width
            start = source_x * channels
            out[base + x * channels : base + (x + 1) * channels] = row[start : start + channels]
    return bytes(out)

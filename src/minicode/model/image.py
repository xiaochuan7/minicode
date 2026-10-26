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

__all__ = [
    "CONTROL_MODEL_INPUT_MAX_HEIGHT_PX",
    "CONTROL_MODEL_INPUT_MAX_WIDTH_PX",
    "MODEL_INPUT_MAX_HEIGHT_PX",
    "MODEL_INPUT_MAX_WIDTH_PX",
    "SERVER_MAX_INPUT_WIDTH_PX",
    "downscale_frame",
    "model_input_size_for",
    "upscale_frame",
]

#: **服务端单侧上限**（物理像素）—— 我们自己缩图时的目标宽度上界。
#:
#: 来源是**实测**（探针，产物 ``runs/m6-token-probe-sweep2/token-probe.json``）：
#: 服务端会把宽超过 **2016 px** 的图再缩到 2016 宽
#: （2548 宽 → 实测 3240 图像 token；2016 宽 → 实测 3242，与 28 px 网格
#: ``72×45 = 3240`` 相符）。
#: ⚠️ 这是**实测值，不是猜的**；但服务端换了版本就可能变 —— 所以**我们自己**
#: 把图缩到这条线以内，**声明的尺寸就与服务端实际看到的尺寸一致**。
SERVER_MAX_INPUT_WIDTH_PX = 2016

#: **我们自己缩图的目标档**（物理像素）：**长边 ≤ 2016、短边 ≤ 1260**（保持宽高比）。
#:
#: **为什么是这一档**（探针先于改动）：
#:
#: * **2048×1280 与 2044×1260 封死**：服务端宽度上限 **≈2016 px** 实测 ——
#:   2048 档图像 token 期望 3404 / 实测 **3242**（差 **−162**）、2044 档
#:   期望 3285 / 实测 3242（差 **−45**）→ **那两档的坐标系不可信**
#:   （产物 ``runs/m6-token-probe-2048exact/token-probe.json``）；
#: * **2016×1260 探针通过**：期望 3240 / 实测 **3242**（差 +2 = 本端点每张图
#:   固定多记的网关开销，扣除后差 **0**）→ 服务端**没有再缩** → 该档坐标系
#:   可信（产物 ``runs/m6-token-probe-2016/token-probe.json``）；
#: * **同批 183 条配对显著**：always 臂 1536 独赢 16 / 2016 独赢 32，
#:   McNemar 精确二项 **p = 0.0293**（净 +16 条 = +8.7 pp）；
#:   **整体 +8.7 pp 全部来自 text 子集**（4/18，p = 0.0043），
#:   **icon 子集不显著**（12/14，p = 0.845）→ icon 的战场在 crop 侧，不在整屏档位；
#: * 2016×1260 在 2560×1600 的屏上是 **0.7875** 的统一比例（**双轴同一个常数**，
#:   16:10 不形变）→ 换算链仍是单一常数。
#:
#: ⚠️ **1536×960 保留为对照臂**（它是配对判定的左臂，不能删）。
MODEL_INPUT_MAX_WIDTH_PX = 2016
MODEL_INPUT_MAX_HEIGHT_PX = 1260

#: **对照臂的档位**（物理像素）：评测台**同时保留**
#: 1536×960 作对照臂（配对判定的左臂）。
CONTROL_MODEL_INPUT_MAX_WIDTH_PX = 1536
CONTROL_MODEL_INPUT_MAX_HEIGHT_PX = 960


def model_input_size_for(
    width: int,
    height: int,
    *,
    max_width: int = MODEL_INPUT_MAX_WIDTH_PX,
    max_height: int = MODEL_INPUT_MAX_HEIGHT_PX,
) -> tuple[int, int]:
    """**声明给模型的尺寸**：把 ``width×height`` 等比缩到 ``max_width×max_height`` 之内。

    * **等比缩放**（两个轴同一个比例 → 换算链仍是**单一常数**，
      坐标契约不受影响）；
    * **不放大**（原图比目标档还小 → 原样送）；
    * 目标是**服务端不会再缩**的那一档（探针实测：服务端把宽 > 2016 px 的图
      缩到 2016 宽 → 那一档的坐标系不可信）。

    本机桌面 2560×1600 → **2016×1260**（比例 0.7875，16:10 不变形）——
    与 Anthropic 坐标契约里「声明发出去那张图的尺寸」一致。
    """
    if width <= 0 or height <= 0:
        raise ValueError(f"尺寸必须为正数，收到 {width}x{height}")
    scale = min(max_width / width, max_height / height, 1.0)
    if scale >= 1.0:
        return width, height
    return (max(1, round(width * scale)), max(1, round(height * scale)))


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


def upscale_frame(frame: CapturedFrame, width: int, height: int) -> CapturedFrame:
    """把一帧**放大**到指定尺寸（重采样）。

    ⚠️ :func:`downscale_frame` **只支持缩小** —— 它是「整屏塞进声明尺寸」那一步，
    放大是**另一件事**：把 crop 里那个 20 物理 px 的图标放大到 ≥30 px。
    两者**不能合成一个函数**（缩小的语义与放大的语义相反）。

    * 支持**任意**比例（不只整数倍）；
    * ``left`` / ``top`` **不变**（还是同一块屏幕区域，只是像素更密了），
      ``source`` 加后缀标出放大过；
    * 放大倍数由**调用方**给（按目标的表观像素算，不是固定值）。
    """
    if width <= 0 or height <= 0:
        raise ValueError(f"目标尺寸必须为正数，收到 {width}x{height}")
    if frame.is_empty:
        raise ValueError("画面是 0×0，放大不了")
    if width < frame.width or height < frame.height:
        raise ValueError(
            f"只支持放大：{frame.width}x{frame.height} → {width}x{height}"
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
    payload = _upscale_payload(
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
        source=f"{frame.source}|upscaled",
        pixel_format=frame.pixel_format,
    )


def _upscale_payload(
    payload: bytes,
    src_width: int,
    src_height: int,
    dst_width: int,
    dst_height: int,
    channels: int,
) -> bytes:
    """**放大**的像素重采样（**双线性** / 最近邻，二选一）。

    ⚠️ **不能复用缩小的盒式平均**：那套算法按「每个输出像素覆盖一段源像素」
    算，放大时**一段源像素会被多个输出像素共享** → 计数会出现 0 → 除零。
    放大用**双线性**：目标像素的中心映射回源坐标 → 四邻域加权。
    """
    try:
        import numpy as np
    except ImportError:
        np = None
    if np is not None:
        return _upscale_bilinear_numpy(
            np, payload, src_width, src_height, dst_width, dst_height, channels
        )
    return _resize_nearest(
        payload, src_width, src_height, dst_width, dst_height, channels
    )


def _upscale_bilinear_numpy(
    np,
    payload: bytes,
    src_width: int,
    src_height: int,
    dst_width: int,
    dst_height: int,
    channels: int,
) -> bytes:
    """**双线性放大**（numpy 路径）—— 目标像素中心映射回源坐标后四邻域加权。"""
    array = np.frombuffer(payload, dtype=np.uint8).reshape(
        src_height, src_width, channels
    ).astype(np.float64)
    xs = (np.arange(dst_width, dtype=np.float64) + 0.5) * src_width / dst_width - 0.5
    ys = (np.arange(dst_height, dtype=np.float64) + 0.5) * src_height / dst_height - 0.5
    xs = np.clip(xs, 0.0, src_width - 1.0)
    ys = np.clip(ys, 0.0, src_height - 1.0)
    x0 = np.floor(xs).astype(np.int64)
    y0 = np.floor(ys).astype(np.int64)
    x1 = np.minimum(x0 + 1, src_width - 1)
    y1 = np.minimum(y0 + 1, src_height - 1)
    wx = (xs - x0).reshape(1, dst_width, 1)
    wy = (ys - y0).reshape(dst_height, 1, 1)
    top = array[np.ix_(y0, x0)] * (1 - wx) + array[np.ix_(y0, x1)] * wx
    bottom = array[np.ix_(y1, x0)] * (1 - wx) + array[np.ix_(y1, x1)] * wx
    out = top * (1 - wy) + bottom * wy
    return np.clip(np.round(out), 0, 255).astype(np.uint8).tobytes()


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

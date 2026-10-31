"""**在图上画标记**（「标记图选择」用）。

**为什么要有这一层**：精定位现在给**多个**候选点（crop 内 N 个固定偏移子区域
各问一次 → 空间聚类）。让模型「在几个坐标里挑一个」很难 —— 公开证据
（RegionFocus）表明：**把候选动作画成标记、让模型在标记图上选**，
比让模型自己数坐标可靠得多【二手·公开 https://arxiv.org/abs/2505.00684】。

**画什么**：**粉红五角星**（RegionFocus 的做法）—— 一个标记 = 一个候选点，
模型只答**编号**（``0`` = 「清单里没有可 信的」）。

**为什么是纯标准库**：动作执行缝那一层是「只靠标准库」的（见
``action/test_action_seam.py``），这里的像素操作也没必要引第三方库 ——
一个五角星的填充是**扫描线 + 奇偶规则**，几十行就够。

⚠️ **坐标契约**：标记画在**送模型那张图**的像素上（图内坐标），
**不改任何坐标换算** —— 换算链仍然只有 ``CropRefiner`` 那一条。
"""

from __future__ import annotations

import math

from minicode.action.ports import CapturedFrame, PixelFormat

__all__ = ["draw_stars", "star_vertices"]

#: **标记的半径**（送模型那张图的像素）。
#:
#: 取值：28 px 的视觉 token 格 —— 标记要**跨过一格**才看得见，
#: 又不能大到把候选点之间的相对位置盖掉。
MARKER_RADIUS_PX = 14

#: **标记的颜色**（RGB）—— **粉红**（RegionFocus 的标记色）。
#:
#: ⚠️ 界面截图里**很少有粉红色** → 与被标注的元素**不撞色**。
MARKER_RGB = (255, 105, 180)


def star_vertices(
    cx: float, cy: float, radius: float = MARKER_RADIUS_PX
) -> list[tuple[float, float]]:
    """**五角星**的 10 个顶点（外 5 + 内 5，逆时针）。

    内半径取外半径的 **0.382**（正五角星的经典比例）—— 那个比例画出来
    才是「尖角够尖、还不至于散」的星。
    """
    inner = radius * 0.382
    points: list[tuple[float, float]] = []
    for index in range(10):
        angle = -math.pi / 2 + index * math.pi / 5
        r = radius if index % 2 == 0 else inner
        points.append((cx + r * math.cos(angle), cy + r * math.sin(angle)))
    return points


def draw_stars(
    frame: CapturedFrame,
    points: list[tuple[float, float]],
    *,
    radius: float = MARKER_RADIUS_PX,
    color: tuple[int, int, int] = MARKER_RGB,
) -> CapturedFrame:
    """在一帧上**画若干五角星**，返回新的一帧（**原帧不变**）。

    * 标记画在**图内坐标**（``points`` 是这张图的像素）；
    * 超出图外的点**不画**（不报错 —— 候选点越界是常态，不是异常）；
    * 像素排布（``BGR`` / ``BGRA``）原样保留。
    """
    if not points:
        return frame
    channels = 4 if frame.pixel_format == PixelFormat.BGRA else 3
    expected = frame.width * frame.height * channels
    if len(frame.payload) != expected:
        raise ValueError(
            f"画面像素数与尺寸对不上：{frame.width}x{frame.height}x{channels} "
            f"应为 {expected} 字节，实际 {len(frame.payload)} 字节"
        )
    #: **BGR 顺序**（帧的排布是 BGR / BGRA）。
    #:
    #: ⚠️ **颜色字节数必须与帧的通道数一致**（审查 Bug #2）：
    #: ``_fill_polygon`` 按 ``channels`` 字节切片赋值，bytearray 的切片赋值
    #: **允许不等长** —— 3 字节颜色写进 4 字节切片会把整帧**每像素收缩 1 字节**，
    #: stride 全错、PNG 编码直接炸。BGRA 的第 4 字节（alpha）写 255（不透明）。
    pixel = bytes(
        (color[2], color[1], color[0], 255)
        if channels == 4
        else (color[2], color[1], color[0])
    )
    payload = bytearray(frame.payload)
    for cx, cy in points:
        _fill_polygon(
            payload,
            frame.width,
            frame.height,
            channels,
            star_vertices(cx, cy, radius),
            pixel,
        )
    return CapturedFrame(
        width=frame.width,
        height=frame.height,
        left=frame.left,
        top=frame.top,
        payload=bytes(payload),
        backend=frame.backend,
        source=f"{frame.source}|marked",
        pixel_format=frame.pixel_format,
    )


def _fill_polygon(
    payload: bytearray,
    width: int,
    height: int,
    channels: int,
    vertices: list[tuple[float, float]],
    color: bytes,
) -> None:
    """把一个**简单多边形**填进像素缓冲（扫描线 + 奇偶规则）。

    只需要「点在多边形内」这一件事 —— 不用抗锯齿（标记要的是**看得见**，
    不是好看）。

    ⚠️ ``color`` 的长度必须等于 ``channels``（等长切片赋值）——
    不等长时 bytearray 会收缩/膨胀，整帧 stride 错位（审查 Bug #2）。
    """
    if len(color) != channels:
        raise ValueError(
            f"颜色字节数 {len(color)} 与帧通道数 {channels} 不一致 —— "
            "切片赋值会改变缓冲长度，整帧 stride 错位"
        )
    ys = [point[1] for point in vertices]
    y_start = max(0, int(math.floor(min(ys))))
    y_end = min(height - 1, int(math.ceil(max(ys))))
    edges = list(zip(vertices, vertices[1:] + vertices[:1], strict=True))
    for y in range(y_start, y_end + 1):
        scan = y + 0.5
        crossings: list[float] = []
        for (x1, y1), (x2, y2) in edges:
            if (y1 <= scan < y2) or (y2 <= scan < y1):
                crossings.append(x1 + (scan - y1) * (x2 - x1) / (y2 - y1))
        if not crossings:
            continue
        crossings.sort()
        for index in range(0, len(crossings) - 1, 2):
            x_start = max(0, int(math.ceil(crossings[index] - 0.5)))
            x_end = min(width - 1, int(math.floor(crossings[index + 1] - 0.5)))
            if x_end < x_start:
                continue
            base = y * width * channels
            for x in range(x_start, x_end + 1):
                offset = base + x * channels
                payload[offset : offset + channels] = color

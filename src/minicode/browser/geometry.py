"""**页面坐标 → 物理表面空间**的换算（只有这一处）。

页面里的坐标是 **CSS 像素**，而本项目的唯一真值空间是**物理表面空间**
（物理像素）。两者差两个量：

* **比例**：物理像素 / CSS 像素。**量出来的**，不是写死的 ——
  本机实测（Windows 11 / 2560×1600 / 150% 缩放 / Chrome 142）：
  浏览器窗口物理宽 1920 px、CSS 外框宽 1280 → 比例 **1.5**；
* **原点**：视口左上角在物理表面空间的位置。由浏览器窗口的**物理矩形**
  （Win32，PMv2 感知 → 物理像素）加上视口在窗口内的偏移算出。

⚠️ 这条换算**只此一处**（架构决策备忘 §2.5 ②：换算链路单一来源）。
"""

from __future__ import annotations

from .elements import Rect

__all__ = [
    "PageViewport",
    "css_rect_to_physical",
    "viewport_from_window",
]


class PageViewport:
    """页面视口在物理表面空间的位置与比例。

    * ``origin_physical`` —— 视口左上角（物理像素）；
    * ``scale`` —— 物理像素 / CSS 像素；
    * ``css_size`` —— 视口的 CSS 尺寸。
    """

    __slots__ = ("origin_physical", "scale", "css_size")

    def __init__(
        self,
        origin_physical: tuple[int, int] = (0, 0),
        scale: float = 1.0,
        css_size: tuple[int, int] = (0, 0),
    ):
        self.origin_physical = (int(origin_physical[0]), int(origin_physical[1]))
        self.scale = float(scale)
        self.css_size = (int(css_size[0]), int(css_size[1]))

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return (
            f"PageViewport(origin_physical={self.origin_physical!r}, "
            f"scale={self.scale!r}, css_size={self.css_size!r})"
        )


def css_rect_to_physical(rect: tuple[float, float, float, float], viewport: PageViewport) -> Rect:
    """CSS 矩形（相对视口左上角）→ **物理表面空间**的矩形。

    CSS 矩形的形状是 ``(x, y, width, height)``（``getBoundingClientRect`` 的形状）。
    """
    x, y, width, height = (float(value) for value in rect)
    origin_x, origin_y = viewport.origin_physical
    scale = viewport.scale
    left = origin_x + x * scale
    top = origin_y + y * scale
    return (
        int(round(left)),
        int(round(top)),
        int(round(left + width * scale)),
        int(round(top + height * scale)),
    )


def viewport_from_window(
    window_rect: Rect,
    css_outer: tuple[int, int],
    css_inner: tuple[int, int],
) -> PageViewport:
    """由**浏览器窗口的物理矩形**与它的 CSS 尺寸推出视口的物理位置。

    * 比例 = 窗口物理宽 ÷ CSS 外框宽（**量出来的**）；
    * 视口原点的横坐标 = 窗口左边 + 左右边框的一半；
    * 视口原点的纵坐标 = 窗口上边 + (窗口物理高 − 视口物理高)
      —— 差的那一段就是标签栏 + 工具栏（Chrome 自己画边框，底部没有系统边框）。
    """
    left, top, right, bottom = (int(value) for value in window_rect)
    outer_w = float(css_outer[0])
    outer_h = float(css_outer[1])
    inner_w = float(css_inner[0])
    inner_h = float(css_inner[1])
    if outer_w <= 0 or outer_h <= 0:
        return PageViewport((left, top), 1.0, (int(inner_w), int(inner_h)))
    scale = (right - left) / outer_w
    viewport_w = inner_w * scale
    viewport_h = inner_h * scale
    origin_x = left + (right - left - viewport_w) / 2.0
    origin_y = top + (bottom - top - viewport_h)
    return PageViewport(
        (int(round(origin_x)), int(round(origin_y))),
        scale,
        (int(inner_w), int(inner_h)),
    )

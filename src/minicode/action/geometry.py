"""几何与桌面度量。

坐标系约定（全文统一，不要混用）：

* **物理表面空间** —— 唯一真值空间，原点是虚拟桌面左上角，单位物理像素。
* **截图像素空间** —— 一次抓取所得图像自身的像素位置。
* **相对空间** —— 归一化到 [0,1]×[0,1] 的无量纲坐标，本项目里相对**那一帧**。
"""

from __future__ import annotations

import math
from dataclasses import dataclass

__all__ = [
    "SENTINEL_CURSOR",
    "is_sentinel",
    "rect_size",
    "is_valid_rect",
    "rect_center",
    "center_point",
    "VirtualDesktop",
]

#: Windows 在无法给出有效坐标时返回的哨兵值。它看起来像坐标但不是坐标。
SENTINEL_CURSOR = (-32000, -32000)


def is_sentinel(point: tuple[int, int] | None) -> bool:
    """判断一个坐标是不是哨兵值。"""
    return point is not None and (int(point[0]), int(point[1])) == SENTINEL_CURSOR


def rect_size(rect: tuple[int, int, int, int]) -> tuple[int, int]:
    """矩形 → ``(width, height)``。

    矩形一律是 ``(left, top, right, bottom)``（与控件框同一口径，物理像素）。
    **矩形 → 尺寸的换算只此一处** —— 别在各处各写一遍
    （``right - left`` 与 ``bottom - top`` 写错就是抓错一块画面）。
    """
    left, top, right, bottom = (int(value) for value in rect)
    return (right - left, bottom - top)


def is_valid_rect(rect: tuple[int, int, int, int]) -> bool:
    """矩形是不是**有面积**的合法矩形。

    ⚠️ **矩形全 0 / 零面积一律判成无效** —— 那种形态必须被丢弃，
    不能当作「它在原点」（bbox 全 0 的候选必须被丢弃而不是当作「在原点」）。
    """
    if len(rect) != 4:
        return False
    left, top, right, bottom = (int(value) for value in rect)
    return right > left and bottom > top


def rect_center(rect: tuple[int, int, int, int]) -> tuple[float, float]:
    """矩形中心（物理像素，浮点）。

    **矩形 → 落点的换算只此一处**：桌面通道（UIA 控件框）与浏览器通道
    （**可见文本的实际包围盒**，工单 M28 起不再是元素矩形）都用它 ——
    同一句目标描述在两个通道里落到同一类控件上（工单 M13 AC 2）。
    """
    left, top, right, bottom = (int(value) for value in rect)
    return ((left + right) / 2.0, (top + bottom) / 2.0)


def center_point(rect: tuple[int, int, int, int]) -> tuple[int, int]:
    """矩形中心（物理像素，整数）。

    取**就近**取整：与真实中心的偏差 ≤ 0.5 物理像素（验收线是 ≤2）。
    """
    x, y = rect_center(rect)
    return (int(math.floor(x + 0.5)), int(math.floor(y + 0.5)))


@dataclass(frozen=True)
class VirtualDesktop:
    """虚拟桌面度量（物理像素）。

    归一化基准用的是**虚拟桌面度量族**，不是主屏度量：
    副屏在主屏左侧或上方时 ``origin_x`` / ``origin_y`` 为负。
    """

    origin_x: int = 0
    origin_y: int = 0
    width: int = 2560
    height: int = 1600

    @property
    def left(self) -> int:
        return self.origin_x

    @property
    def top(self) -> int:
        return self.origin_y

    @property
    def right(self) -> int:
        return self.origin_x + self.width

    @property
    def bottom(self) -> int:
        return self.origin_y + self.height

    def contains(self, px: float, py: float, tolerance_px: float = 0.0) -> bool:
        """物理点是否落在虚拟桌面内（含容差外扩）。

        桌面覆盖 ``[left, right] × [top, bottom]``（连续边界，右边是 ``origin + width``），
        与冻结原型 ``FrameGeometry`` 的表面边界口径一致。
        """
        return (
            self.left - tolerance_px <= px <= self.right + tolerance_px
            and self.top - tolerance_px <= py <= self.bottom + tolerance_px
        )

    def contains_rect(
        self, rect: tuple[int, int, int, int], tolerance_px: float = 0.0
    ) -> bool:
        """矩形是否**整个**落在虚拟桌面内（含容差外扩）。

        越界**不夹取**：宁可报错，也不给一块不是用户要的画面。
        """
        left, top, right, bottom = rect
        return (
            self.left - tolerance_px <= left
            and self.top - tolerance_px <= top
            and right <= self.right + tolerance_px
            and bottom <= self.bottom + tolerance_px
        )

    def to_absolute_input(self, px: float, py: float) -> tuple[int, int]:
        """物理表面像素 → SendInput 的 0..65535 绝对坐标。

        分母必须是 ``(维度 − 1)``，否则最后一个像素永远点不到。
        必须带 ``MOUSEEVENTF_VIRTUALDESK``，否则只映射到主显示器。

        这是本项目代码里**唯一**一份这个公式（换算链路单一来源）。
        """
        nx = round((float(px) - self.origin_x) * 65535 / max(self.width - 1, 1))
        ny = round((float(py) - self.origin_y) * 65535 / max(self.height - 1, 1))
        return max(0, min(65535, int(nx))), max(0, min(65535, int(ny)))

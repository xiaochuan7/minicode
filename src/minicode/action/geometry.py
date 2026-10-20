"""几何与桌面度量。

坐标系约定（全文统一，不要混用）：

* **物理表面空间** —— 唯一真值空间，原点是虚拟桌面左上角，单位物理像素。
* **截图像素空间** —— 一次抓取所得图像自身的像素位置。
* **相对空间** —— 归一化到 [0,1]×[0,1] 的无量纲坐标，本项目里相对**那一帧**。
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["SENTINEL_CURSOR", "is_sentinel", "VirtualDesktop"]

#: Windows 在无法给出有效坐标时返回的哨兵值。它看起来像坐标但不是坐标。
SENTINEL_CURSOR = (-32000, -32000)


def is_sentinel(point: tuple[int, int] | None) -> bool:
    """判断一个坐标是不是哨兵值。"""
    return point is not None and (int(point[0]), int(point[1])) == SENTINEL_CURSOR


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

    def to_absolute_input(self, px: float, py: float) -> tuple[int, int]:
        """物理表面像素 → SendInput 的 0..65535 绝对坐标。

        分母必须是 ``(维度 − 1)``，否则最后一个像素永远点不到。
        必须带 ``MOUSEEVENTF_VIRTUALDESK``，否则只映射到主显示器。
        """
        nx = round((float(px) - self.origin_x) * 65535 / max(self.width - 1, 1))
        ny = round((float(py) - self.origin_y) * 65535 / max(self.height - 1, 1))
        return max(0, min(65535, int(nx))), max(0, min(65535, int(ny)))

    def from_absolute_input(self, nx: int, ny: int) -> tuple[int, int]:
        """SendInput 的 0..65535 绝对坐标 → 物理表面像素（:meth:`to_absolute_input` 的逆）。

        0..65535 的量化精度有限（虚拟桌面越宽每像素摊到的刻度越少），
        因此往返允许 1 物理像素的误差。
        """
        px = self.origin_x + round(int(nx) * (self.width - 1) / 65535)
        py = self.origin_y + round(int(ny) * (self.height - 1) / 65535)
        return int(px), int(py)

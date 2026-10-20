"""第二级 grounding：在**原始物理分辨率**截图上开 crop 窗口二次定位。

对应架构备忘 §1.3 的 L2 视觉定位层"精定位"半边：

    整屏粗定位 → 粗点 p0（物理像素，±30 px 足够）
              → 以 p0 为中心开 512×512 物理像素窗口（越界 shift/shrink，**不裁切目标**）
              → 原图 1:1 裁剪（不缩放）
              → 模型在 crop 内输出 0~1000 → ×(crop_size/1000) → crop 内物理像素
              → + crop 原点 → **最终物理像素坐标**

关键性质
--------
* crop 里的 1 归一化单位 = 512/1000 = 0.512 物理 px，量化误差 ±0.26 px
  （整屏是 ±1.28 px）。
* crop 从**原始物理分辨率截图**裁，16 px 的图标进模型仍是 16 px，
  不会被整屏降采样抹成 7.9 px。

坐标系
------
本模块的"crop 像素"就是**物理像素**（1:1 裁剪，不缩放）。
为了复用同一个 :class:`~coordinates.CoordinateAdapter`，
crop 被建模成一个**独立的 FrameGeometry**，其虚拟桌面原点 = crop 的物理原点，
于是同一个适配器直接把 crop 内坐标映射回全屏物理坐标，无需另写一套换算。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional, Tuple

if __package__:  # 包化：作为 minicode.coord 的一部分被导入
    from .coordinates import (
        CoordinateAdapter,
        CoordinateError,
        FrameGeometry,
        ModelScale,
        Rect,
    )
else:  # 目录内直接运行时的平铺导入
    from coordinates import (
        CoordinateAdapter,
        CoordinateError,
        FrameGeometry,
        ModelScale,
        Rect,
    )

__all__ = [
    "CropPlan",
    "CropRefiner",
    "plan_crop",
    "DEFAULT_CROP_WIDTH_PX",
    "DEFAULT_CROP_HEIGHT_PX",
]

#: 默认 crop 尺寸（物理像素）。架构备忘 §1.3 / §2 定为 512×512。
DEFAULT_CROP_WIDTH_PX = 512
DEFAULT_CROP_HEIGHT_PX = 512


@dataclass(frozen=True)
class CropPlan:
    """一次 crop 窗口的规划结果（全部为物理像素）。"""

    origin_x: int
    origin_y: int
    width: int
    height: int
    surface_left: float
    surface_top: float
    surface_right: float
    surface_bottom: float
    shifted: bool = False
    shrunk: bool = False
    target_radius_px: float = 0.0
    target_fully_contained: bool = True
    upscale: float = 1.0

    @property
    def right(self) -> int:
        return self.origin_x + self.width

    @property
    def bottom(self) -> int:
        return self.origin_y + self.height

    @property
    def center(self) -> Tuple[float, float]:
        return (self.origin_x + self.width / 2.0, self.origin_y + self.height / 2.0)

    @property
    def rect(self) -> Rect:
        """crop 窗口在物理表面上的矩形。"""
        return Rect(self.origin_x, self.origin_y, self.width, self.height)

    def contains_physical(self, px: float, py: float) -> bool:
        """物理点是否落在 crop 内。"""
        return self.origin_x <= px < self.right and self.origin_y <= py < self.bottom

    def describe(self) -> str:
        flags = []
        if self.shifted:
            flags.append("shifted")
        if self.shrunk:
            flags.append("shrunk")
        if not self.target_fully_contained:
            flags.append("target-clipped")
        if self.upscale != 1.0:
            flags.append(f"upscale={self.upscale}")
        flag_str = (" [" + ",".join(flags) + "]") if flags else ""
        return (
            f"CropPlan(origin=({self.origin_x},{self.origin_y}), "
            f"size={self.width}x{self.height}{flag_str})"
        )


def _fit_axis(center: float, size: int, lo: float, hi: float, radius: float) -> Tuple[float, bool, bool]:
    """在 [lo, hi] 内放一个 size 长的窗口，尽量以 center 为中心并包含目标。

    返回 ``(start, shifted, shrunk)``。
    """
    shrunk = False
    if size > (hi - lo):
        size = int(math.floor(hi - lo))
        shrunk = True
    if size <= 0:
        raise CoordinateError(f"表面区间 [{lo}, {hi}] 太小，放不下 crop")

    start = center - size / 2.0
    shifted = False
    if start < lo:
        start = lo
        shifted = True
    if start + size > hi:
        start = hi - size
        shifted = True

    # 目标半径约束：窗口应覆盖 [center-radius, center+radius]
    if radius > 0 and size >= 2 * radius:
        lo_start = center + radius - size
        hi_start = center - radius
        lo_start = max(lo_start, lo)
        hi_start = min(hi_start, hi - size)
        if lo_start <= hi_start:
            start = min(max(start, lo_start), hi_start)
            if start != center - size / 2.0:
                shifted = True
    return start, shifted, shrunk


def plan_crop(
    center_x: float,
    center_y: float,
    surface: Rect,
    *,
    crop_width: int = DEFAULT_CROP_WIDTH_PX,
    crop_height: int = DEFAULT_CROP_HEIGHT_PX,
    target_radius_px: float = 0.0,
    upscale: float = 1.0,
) -> CropPlan:
    """规划一个以 ``(center_x, center_y)`` 为中心的 crop 窗口。

    输入坐标系
    ----------
    center_x, center_y
        粗定位得到的物理像素坐标（**物理表面空间**）。
    surface
        可用于裁剪的区域（物理表面空间的矩形）。crop 必须落在它内部。

    输出坐标系
    ----------
    :class:`CropPlan` —— crop 窗口在物理表面上的位置与尺寸（物理像素）。

    越界处理
    --------
    * **shift**：窗口越过 surface 边界时整体平移，尺寸不变。
    * **shrink**：surface 本身比 crop 还小时，把窗口缩到 surface 大小。
    * 两种情况都**保证粗定位点仍在窗口内**；给定 ``target_radius_px`` 时
      还会尽量保证整个目标在窗口内（放不下时 ``target_fully_contained=False``）。
    """
    if crop_width <= 0 or crop_height <= 0:
        raise ValueError("crop 尺寸必须为正数")
    if not (surface.left <= center_x <= surface.right and surface.top <= center_y <= surface.bottom):
        raise CoordinateError(
            f"粗定位点 ({center_x}, {center_y}) 不在可裁剪表面 "
            f"[{surface.left}, {surface.right}] × [{surface.top}, {surface.bottom}] 内"
        )

    start_x, shifted_x, shrunk_x = _fit_axis(
        center_x, crop_width, surface.left, surface.right, target_radius_px
    )
    start_y, shifted_y, shrunk_y = _fit_axis(
        center_y, crop_height, surface.top, surface.bottom, target_radius_px
    )

    origin_x = int(round(start_x))
    origin_y = int(round(start_y))
    width = int(crop_width if not shrunk_x else surface.width)
    height = int(crop_height if not shrunk_y else surface.height)
    # 取整后再夹一次，保证不越界
    origin_x = max(surface.left, min(origin_x, surface.right - width))
    origin_y = max(surface.top, min(origin_y, surface.bottom - height))
    origin_x = int(origin_x)
    origin_y = int(origin_y)

    target_contained = True
    if target_radius_px > 0:
        target_contained = (
            origin_x <= center_x - target_radius_px
            and origin_x + width >= center_x + target_radius_px
            and origin_y <= center_y - target_radius_px
            and origin_y + height >= center_y + target_radius_px
        )

    return CropPlan(
        origin_x=origin_x,
        origin_y=origin_y,
        width=width,
        height=height,
        surface_left=surface.left,
        surface_top=surface.top,
        surface_right=surface.right,
        surface_bottom=surface.bottom,
        shifted=bool(shifted_x or shifted_y),
        shrunk=bool(shrunk_x or shrunk_y),
        target_radius_px=target_radius_px,
        target_fully_contained=target_contained,
        upscale=upscale,
    )


class CropRefiner:
    """两级 grounding 的第二级：crop 内二次定位 → 全屏物理坐标。

    参数
    ----
    adapter
        已标定好量纲的 :class:`~coordinates.CoordinateAdapter`。
    crop_width / crop_height
        crop 窗口尺寸（物理像素），默认 512×512。
    upscale
        送给模型前把 crop 放大的倍数（1.0 = 不放大）。
        放大只影响**绝对像素**标度；归一化标度不受影响。
    tolerance_px
        越界容差（物理像素），默认 2.0。
    """

    def __init__(
        self,
        adapter: CoordinateAdapter,
        *,
        crop_width: int = DEFAULT_CROP_WIDTH_PX,
        crop_height: int = DEFAULT_CROP_HEIGHT_PX,
        upscale: float = 1.0,
        tolerance_px: float = 2.0,
    ):
        if upscale <= 0:
            raise ValueError("upscale 必须为正数")
        self.adapter = adapter
        self.crop_width = int(crop_width)
        self.crop_height = int(crop_height)
        self.upscale = float(upscale)
        self.tolerance_px = float(tolerance_px)

    # -- crop 坐标系 ↔ 物理表面 -------------------------------------------

    def crop_frame(self, plan: CropPlan, *, model_input_size: Optional[Tuple[int, int]] = None) -> FrameGeometry:
        """构造 crop 对应的 :class:`FrameGeometry`。

        虚拟桌面原点 = crop 的物理原点，于是 crop 像素与物理像素 1:1，
        同一个适配器即可把 crop 内坐标映射回全屏物理坐标。
        """
        miw, mih = model_input_size or (
            int(round(plan.width * self.upscale)),
            int(round(plan.height * self.upscale)),
        )
        return FrameGeometry(
            capture_width=plan.width,
            capture_height=plan.height,
            model_input_width=miw,
            model_input_height=mih,
            capture_to_physical_scale=1.0,
            virtual_origin_x=plan.origin_x,
            virtual_origin_y=plan.origin_y,
            surface_width=plan.width,
            surface_height=plan.height,
        )

    @staticmethod
    def crop_to_physical(cx: float, cy: float, plan: CropPlan) -> Tuple[float, float]:
        """**crop 像素 → 物理表面像素**。"""
        return (cx + plan.origin_x, cy + plan.origin_y)

    @staticmethod
    def physical_to_crop(px: float, py: float, plan: CropPlan) -> Tuple[float, float]:
        """**物理表面像素 → crop 像素**。"""
        return (px - plan.origin_x, py - plan.origin_y)

    # -- 主流程 -----------------------------------------------------------

    def plan(
        self,
        coarse_physical: Tuple[float, float],
        surface: Rect,
        *,
        target_radius_px: float = 0.0,
    ) -> CropPlan:
        """按粗定位点规划 crop 窗口。输入/输出均为物理表面空间。"""
        return plan_crop(
            coarse_physical[0],
            coarse_physical[1],
            surface,
            crop_width=self.crop_width,
            crop_height=self.crop_height,
            target_radius_px=target_radius_px,
            upscale=self.upscale,
        )

    def refine(
        self,
        coarse_physical: Tuple[float, float],
        raw_output: Tuple[float, float],
        surface: Rect,
        *,
        target_radius_px: float = 0.0,
        model_input_size: Optional[Tuple[int, int]] = None,
    ) -> Tuple[float, float]:
        """在 crop 上二次定位，把模型输出换算成**全屏物理像素**。

        输入坐标系
        ----------
        coarse_physical
            一级粗定位的物理像素坐标（物理表面空间）。
        raw_output
            模型在 crop 图上的原始输出（量纲由 ``self.adapter.model_scale`` 决定）。
        surface
            可裁剪区域（物理表面空间）。

        输出坐标系
        ----------
        ``(px, py)``：全屏物理像素（物理表面空间）。
        """
        plan = self.plan(coarse_physical, surface, target_radius_px=target_radius_px)
        frame = self.crop_frame(plan, model_input_size=model_input_size)
        return self.adapter.to_screen(raw_output[0], raw_output[1], frame)

    def refine_relative(
        self,
        coarse_physical: Tuple[float, float],
        rel_output: Tuple[float, float],
        surface: Rect,
        *,
        target_radius_px: float = 0.0,
    ) -> Tuple[float, float]:
        """便捷版：直接给 crop 内的 0~1 相对坐标，返回全屏物理像素。"""
        raw = (rel_output[0], rel_output[1])
        if self.adapter.model_scale is not ModelScale.RELATIVE_0_1:
            raise CoordinateError(
                f"refine_relative 要求适配器为 RELATIVE_0_1，当前是 {self.adapter.model_scale.value}"
            )
        return self.refine(
            coarse_physical, raw, surface, target_radius_px=target_radius_px
        )

    def describe(self) -> str:
        return (
            f"CropRefiner(scale={self.adapter.model_scale.value}, "
            f"crop={self.crop_width}x{self.crop_height}, upscale={self.upscale})"
        )

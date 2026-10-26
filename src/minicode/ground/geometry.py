"""两级 grounding 的几何：帧元数据、裁剪计划字段与往返自检。

**坐标换算链路的唯一真值来源是冻结原型** :mod:`minicode.coord`：
本模块只负责把「这一张图」描述成它自己的 :class:`FrameGeometry`，
换算本身一律走 ``CoordinateAdapter`` / ``CropRefiner``。

两套契约在这里分开：

* **整屏**：capture = 整屏抓图（1 像素 = 1 物理像素），模型输入尺寸来自
  **标定记录**（模型实际看到的那张整屏图）；
* **crop**：capture = 从原始帧 1:1 裁下的窗口，**虚拟桌面原点 = crop 的物理原点**
  —— 于是 crop 内坐标 + 原点偏移 = 全屏物理坐标（不用另写一套换算）。
"""

from __future__ import annotations

import math
from typing import Any

from minicode.coord.coordinates import CoordinateAdapter, FrameGeometry, Rect
from minicode.coord.crop_refine import CropPlan, CropRefiner

__all__ = [
    "crop_plan_fields",
    "crop_roundtrip_error",
    "full_screen_geometry",
    "surface_rect",
]


def surface_rect(surface: tuple[int, int, int, int]) -> Rect:
    """物理表面矩形 ``(left, top, right, bottom)`` → 冻结原型的 :class:`Rect`。"""
    left, top, right, bottom = (int(value) for value in surface)
    return Rect.from_bounds(left, top, right, bottom)


def full_screen_geometry(
    *,
    full_screen_size: tuple[int, int],
    model_input_size: tuple[int, int],
    surface: tuple[int, int, int, int],
    origin: tuple[int, int] | None = None,
) -> FrameGeometry:
    """整屏的帧元数据。

    * ``capture_width/height`` —— 整屏抓图的尺寸（物理像素，1:1）；
    * ``model_input_width/height`` —— **模型实际看到的那张图**的尺寸
      （来自标定记录；服务端可能把大图缩小）；
    * ``virtual_origin`` —— 抓图原点在物理表面上的位置（默认取表面原点）；
    * ``surface_*`` —— 物理表面（越界判定用）。
    """
    left, top, right, bottom = (int(value) for value in surface)
    origin_x, origin_y = origin if origin is not None else (left, top)
    return FrameGeometry(
        capture_width=int(full_screen_size[0]),
        capture_height=int(full_screen_size[1]),
        model_input_width=int(model_input_size[0]),
        model_input_height=int(model_input_size[1]),
        capture_to_physical_scale=1.0,
        virtual_origin_x=origin_x,
        virtual_origin_y=origin_y,
        surface_width=right - left,
        surface_height=bottom - top,
        surface_origin_x=left,
        surface_origin_y=top,
    )


def crop_plan_fields(plan: CropPlan) -> dict[str, Any]:
    """裁剪计划的落盘形状。

    含「目标是否完整包含」这一属性（工单 M6 AC 2 要求它暴露出来并被单测钉死）。
    """
    return {
        "origin_x": plan.origin_x,
        "origin_y": plan.origin_y,
        "width": plan.width,
        "height": plan.height,
        "shifted": plan.shifted,
        "shrunk": plan.shrunk,
        "target_fully_contained": plan.target_fully_contained,
        "target_radius_px": plan.target_radius_px,
    }


def crop_roundtrip_error(
    adapter: CoordinateAdapter,
    plan: CropPlan,
    model_input_size: tuple[int, int],
    physical: tuple[float, float],
) -> float:
    """**物理像素 → crop 内 → 物理像素**的往返误差（物理像素）。

    工单 M6 AC 3 要求这个自检存在、且往返误差为 0。
    """
    frame = CropRefiner(adapter).crop_frame(plan, model_input_size=model_input_size)
    raw = adapter.from_screen(physical[0], physical[1], frame)
    back = adapter.to_screen(raw[0], raw[1], frame)
    return math.hypot(back[0] - physical[0], back[1] - physical[1])

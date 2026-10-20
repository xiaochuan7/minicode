"""换算链路：**缩放系数的唯一真值来源**。

架构备忘 §2.5 ②：缩放系数、标度、crop 偏移都只能有一个真值来源。
本模块把这件事落到一个对象上：

* :class:`SurfaceSpec` 持有 **一个** 缩放系数（``capture_to_physical_scale``），
  含义是「截图像素与物理表面像素 1:1」；非 1.0 时打警告日志——
  因为它意味着某个环节没做对，而不是正常配置。
* 所有换算入口（截图像素 ↔ 物理表面、相对空间 ↔ 物理表面）都从这里出发，
  换算公式本身复用冻结原型 ``minicode.coord.coordinates``，
  **本项目不写第二份**。

虚拟桌面度量族（多屏、负原点、``(维度 − 1)`` 分母）在
:class:`~minicode.action.geometry.VirtualDesktop` 里，是另一个空间的事，不受缩放系数影响。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from minicode.coord.coordinates import (
    CoordinateAdapter,
    CoordinateOutOfBounds,
    FrameGeometry,
    ModelScale,
    DEFAULT_OUT_OF_BOUNDS_TOLERANCE_PX,
)

from .geometry import VirtualDesktop

__all__ = ["SurfaceSpec", "DEFAULT_OUT_OF_BOUNDS_TOLERANCE_PX"]

_LOG = logging.getLogger("minicode.action.surface")

#: M0 没有模型参与决策；这里只借用冻结原型的换算实现，
#: 而「截图像素 ↔ 物理表面」两层与量纲无关（见 CoordinateAdapter 的实现）。
_PROTOCOL_SPACE_SCALE = ModelScale.ABSOLUTE_CAPTURE


@dataclass(frozen=True)
class SurfaceSpec:
    """一帧的物理变换元数据 + 换算入口。

    字段
    ----
    virtual_desktop
        虚拟桌面度量（原点 + 尺寸）。多屏时原点为负。
    capture_width / capture_height
        这一帧的尺寸（**截图像素**）。
    capture_origin_x / capture_origin_y
        这一帧左上角在物理表面空间的位置。缺省 = 虚拟桌面原点。
    capture_to_physical_scale
        **1 个截图像素对应多少物理像素**。默认 1.0。
        PMv2 感知路径下恒为 1.0；非 1.0 会打警告日志。
    out_of_bounds_tolerance_px
        越界容差（物理像素）。默认 2.0，**与标定探针的 1 px 标定容差是两个独立常量**。
    """

    virtual_desktop: VirtualDesktop
    capture_width: int
    capture_height: int
    capture_origin_x: int | None = None
    capture_origin_y: int | None = None
    capture_to_physical_scale: float = 1.0
    out_of_bounds_tolerance_px: float = DEFAULT_OUT_OF_BOUNDS_TOLERANCE_PX

    _frame: FrameGeometry = field(init=False, repr=False, compare=False)
    _adapter: CoordinateAdapter = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        if self.capture_width <= 0 or self.capture_height <= 0:
            raise ValueError(f"截图尺寸必须为正数：{self.capture_width}x{self.capture_height}")
        if self.capture_to_physical_scale <= 0:
            raise ValueError("capture_to_physical_scale 必须为正数")
        origin_x = (
            self.virtual_desktop.origin_x if self.capture_origin_x is None else self.capture_origin_x
        )
        origin_y = (
            self.virtual_desktop.origin_y if self.capture_origin_y is None else self.capture_origin_y
        )
        object.__setattr__(
            self,
            "_frame",
            FrameGeometry(
                capture_width=self.capture_width,
                capture_height=self.capture_height,
                capture_to_physical_scale=self.capture_to_physical_scale,
                virtual_origin_x=origin_x,
                virtual_origin_y=origin_y,
                surface_width=self.virtual_desktop.width,
                surface_height=self.virtual_desktop.height,
                surface_origin_x=self.virtual_desktop.origin_x,
                surface_origin_y=self.virtual_desktop.origin_y,
            ),
        )
        object.__setattr__(
            self,
            "_adapter",
            CoordinateAdapter(
                _PROTOCOL_SPACE_SCALE,
                out_of_bounds_tolerance_px=self.out_of_bounds_tolerance_px,
            ),
        )
        if self.capture_to_physical_scale != 1.0:
            _LOG.warning(
                "capture_to_physical_scale=%s（非 1.0）：截图像素与物理表面像素不是 1:1，"
                "说明某个环节没做对（例如走了 DPI-unaware 抓图路径）。",
                self.capture_to_physical_scale,
            )

    # -- 换算入口（全部经由同一个缩放系数）------------------------------------

    def capture_to_physical(self, cx: float, cy: float) -> tuple[float, float]:
        """**截图像素 → 物理表面像素**。"""
        return self._adapter.capture_to_physical(cx, cy, self._frame)

    def physical_to_capture(self, px: float, py: float) -> tuple[float, float]:
        """**物理表面像素 → 截图像素**。"""
        return self._adapter.physical_to_capture(px, py, self._frame)

    def normalized_to_physical(self, rx: float, ry: float) -> tuple[float, float]:
        """**相对空间 [0,1]（相对这一帧）→ 物理表面像素**。"""
        return self.capture_to_physical(
            rx * self.capture_width, ry * self.capture_height
        )

    def physical_to_normalized(self, px: float, py: float) -> tuple[float, float]:
        """**物理表面像素 → 相对空间 [0,1]（相对这一帧）**。"""
        cx, cy = self.physical_to_capture(px, py)
        return (cx / self.capture_width, cy / self.capture_height)

    def to_absolute_input(self, px: float, py: float) -> tuple[int, int]:
        """**物理表面像素 → SendInput 的 0..65535 绝对坐标**（相对虚拟桌面）。"""
        return self.virtual_desktop.to_absolute_input(px, py)

    # -- 矩形换算 -----------------------------------------------------------

    def rect_to_capture(self, rect: tuple[float, float, float, float]) -> tuple[float, ...]:
        """物理表面空间的矩形 → 截图像素空间的矩形。"""
        left, top, right, bottom = rect
        cx0, cy0 = self.physical_to_capture(left, top)
        cx1, cy1 = self.physical_to_capture(right, bottom)
        return (cx0, cy0, cx1, cy1)

    # -- 越界判定（不夹取）---------------------------------------------------

    def assert_within_surface(self, px: float, py: float) -> None:
        """物理落点必须落在虚拟桌面内（容差 ``out_of_bounds_tolerance_px``）。

        越界**直接报错**，不挪到最近的显示器、也不夹到边缘像素。
        """
        tol = self.out_of_bounds_tolerance_px
        if not self.virtual_desktop.contains(px, py, tolerance_px=tol):
            raise CoordinateOutOfBounds(
                f"物理坐标 ({px:.1f}, {py:.1f}) 越出虚拟桌面 "
                f"[{self.virtual_desktop.left}, {self.virtual_desktop.right - 1}] × "
                f"[{self.virtual_desktop.top}, {self.virtual_desktop.bottom - 1}]"
                f"（越界容差 {tol} px）",
                tolerance_px=tol,
                value=(px, py),
            )

    @property
    def frame(self) -> FrameGeometry:
        """这一帧的物理变换元数据（冻结原型 ``FrameGeometry``）。"""
        return self._frame

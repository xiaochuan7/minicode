"""坐标换算层：模型输出 → 截图像素 → 物理屏幕像素。

本模块是纯标准库实现（无第三方依赖），不 import 任何 GUI / 截图 / 鼠标库，
因此可以离线单测，也可以被任意后端（L0 设备层）复用。

坐标系定义（全文统一，不要混用）
--------------------------------
1. **模型空间 (model space)**
   模型返回的原始数值。量纲由 :class:`ModelScale` 决定，可能是 0~1、0~999、
   0~1000、截图像素绝对值、或模型输入图像的绝对像素。
   *本层不做任何假设*——量纲必须由外部显式配置（见
   ``docs/01-架构决策备忘.md`` 附录 B④：五种坐标协议并存，混用即静默偏移）。

2. **相对空间 (relative space)**
   归一化到 [0,1] 的比例坐标，(0,0)=左上角，(1,1)=右下角。
   与图像分辨率无关，是换算链的中枢。

3. **截图像素空间 (capture space)**
   本次截图这张图的像素坐标，左上原点，单位 = 截图像素。
   截图本身可能是物理像素（推荐），也可能是被 DPI 虚拟化过的逻辑像素。

4. **模型输入空间 (model-input space)**
   真正送进模型的那张图的像素坐标。它可能已经被预处理缩放过
   （例：Qwen2.5-VL 的 ``smart_resize``）。

5. **物理表面空间 (physical surface space)**
   真实显示器的物理像素坐标，原点 = 虚拟桌面左上角（多屏时可能为负）。
   **这是本系统唯一的真值空间。**

换算链
------
    模型空间 --model_to_relative--> 相对空间 --relative_to_capture--> 截图像素
        --capture_to_physical--> 物理表面空间

反向链同样提供（``from_screen`` 等），用于自检与往返断言。

越界策略
--------
按行动协议（``docs/01-架构决策备忘.md`` §3）：**越界不 clamp，直接报错，容差 2 px**。
本模块所有对外换算方法在越界时抛 :class:`CoordinateOutOfBounds`，
绝不静默地把坐标拉回边界——静默 clamp 是 GUI agent 最难查的一类 bug。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Iterable, Optional, Sequence

__all__ = [
    "ModelScale",
    "Rect",
    "FrameGeometry",
    "CoordinateError",
    "CoordinateOutOfBounds",
    "ModelScaleError",
    "SafeAreaViolation",
    "CoordinateAdapter",
    "DEFAULT_OUT_OF_BOUNDS_TOLERANCE_PX",
]

#: 越界容差（物理像素）。所有越界判定默认用它。
DEFAULT_OUT_OF_BOUNDS_TOLERANCE_PX = 2.0


class CoordinateError(Exception):
    """本模块所有异常的基类。"""


class CoordinateOutOfBounds(CoordinateError):
    """坐标越界（模型输出越界，或换算后的物理坐标落在表面之外）。

    按行动协议**不做 clamp**，直接抛出。`tolerance_px` 记录允许的越界量。
    """

    def __init__(self, message: str, *, tolerance_px: float = 0.0, value: Optional[tuple] = None):
        super().__init__(message)
        self.tolerance_px = tolerance_px
        self.value = value


class ModelScaleError(CoordinateError):
    """模型空间量纲缺失或与帧元数据冲突（例如绝对像素模式却没给模型输入尺寸）。"""


class SafeAreaViolation(CoordinateError):
    """物理坐标落在 `safe_area` 白名单之外。"""


class ModelScale(Enum):
    """模型 grounding 输出的量纲。

    枚举值即"运行时标定"要判定的目标。**不要把它写死成常量**——
    官方文档自相矛盾（Qwen3-VL 的 grounding 页写 0~1，技术报告写 0~1000），
    且同一厂商不同 API 版本也可能不同，故必须运行时标定（§1.4）。
    """

    #: 归一化到 [0,1]。例：Kimi-VL、Qwen3-VL 官方 docs 页的表述。
    RELATIVE_0_1 = "relative_0_1"
    #: 归一化到 [0,1000]。例：Qwen3-VL 技术报告、Gemini、GLM-4.1V、百炼 gui-plus。
    RELATIVE_0_1000 = "relative_0_1000"
    #: 归一化到 [0,999]。例：GLM-4.5V GUI agent（官方 agent.md 明写 0-999）。
    RELATIVE_0_999 = "relative_0_999"
    #: 绝对像素，单位 = **截图像素**。例：Anthropic Claude computer use、OpenAI GA computer。
    ABSOLUTE_CAPTURE = "absolute_capture"
    #: 绝对像素，单位 = **模型输入图像像素**（可能被 smart_resize 缩放过）。
    #: 例：Qwen2.5-VL、UI-TARS（官方 action_parser 的 ``qwen25vl`` 分支）。
    ABSOLUTE_MODEL_INPUT = "absolute_model_input"

    @property
    def is_relative(self) -> bool:
        """该标度是否属于"归一化比例"族（可经除分母进入相对空间）。"""
        return self in _RELATIVE_DENOMINATORS

    @property
    def denominator(self) -> float:
        """归一化标度的名义分母；绝对像素标度无分母，抛 :class:`ModelScaleError`。"""
        try:
            return _RELATIVE_DENOMINATORS[self]
        except KeyError as exc:  # pragma: no cover - 由 is_relative 守卫
            raise ModelScaleError(f"{self.value} 是绝对像素标度，没有归一化分母") from exc

    @classmethod
    def parse(cls, raw: str) -> "ModelScale":
        """从字符串解析标度，容忍 ``"0-1000"`` / ``"1000"`` / ``"relative_0_1000"`` 等写法。"""
        key = raw.strip().lower().replace("-", "_").replace(" ", "_")
        if key in {"0_1", "1", "rel_0_1", "relative_0_1"}:
            return cls.RELATIVE_0_1
        if key in {"0_999", "999", "rel_0_999", "relative_0_999"}:
            return cls.RELATIVE_0_999
        if key in {"0_1000", "1000", "rel_0_1000", "relative_0_1000"}:
            return cls.RELATIVE_0_1000
        if key in {"abs_capture", "absolute_capture", "capture_px", "screenshot_px"}:
            return cls.ABSOLUTE_CAPTURE
        if key in {"abs_model_input", "absolute_model_input", "model_input_px"}:
            return cls.ABSOLUTE_MODEL_INPUT
        raise ModelScaleError(f"无法识别的标度：{raw!r}")


#: 归一化标度族 → 名义分母。定义在 ModelScale 之后。
_RELATIVE_DENOMINATORS = {
    ModelScale.RELATIVE_0_1: 1.0,
    ModelScale.RELATIVE_0_1000: 1000.0,
    ModelScale.RELATIVE_0_999: 999.0,
}


@dataclass(frozen=True)
class Rect:
    """物理表面上的一个矩形（左上原点，物理像素）。用于 `safe_area` 白名单。"""

    x: float
    y: float
    width: float
    height: float

    @property
    def left(self) -> float:
        return self.x

    @property
    def top(self) -> float:
        return self.y

    @property
    def right(self) -> float:
        return self.x + self.width

    @property
    def bottom(self) -> float:
        return self.y + self.height

    @property
    def center(self) -> tuple:
        return (self.x + self.width / 2.0, self.y + self.height / 2.0)

    def contains(self, px: float, py: float, *, tolerance_px: float = 0.0) -> bool:
        """点是否落在矩形内（含 `tolerance_px` 容差外扩）。"""
        return (
            self.left - tolerance_px <= px <= self.right + tolerance_px
            and self.top - tolerance_px <= py <= self.bottom + tolerance_px
        )

    def clamp_point(self, px: float, py: float) -> tuple:
        """把点夹进矩形内。**仅供 safe_area 提示使用**，坐标换算链不会调用它。"""
        cx = min(max(px, self.left), self.right)
        cy = min(max(py, self.top), self.bottom)
        return (cx, cy)

    @staticmethod
    def from_bounds(left: float, top: float, right: float, bottom: float) -> "Rect":
        return Rect(left, top, right - left, bottom - top)


@dataclass(frozen=True)
class FrameGeometry:
    """一帧截图的元数据。描述"这张图"与"物理表面"之间的关系。

    对应架构备忘 §1.3 / §1.5 的要求：抓图层解决 DPI/多屏，动作层只见物理像素。

    字段
    ----
    capture_width / capture_height
        截图本身的宽高（**截图像素**）。
    model_input_width / model_input_height
        真正送进模型的那张图的宽高。缺省等于截图尺寸（即未做预处理缩放）。
        仅在 ``ModelScale.ABSOLUTE_MODEL_INPUT`` 下是必需的。
    capture_to_physical_scale
        **1 个截图像素对应多少物理像素**。默认 1.0。

        ⚠️ 重要：在 **Per-Monitor-V2 DPI 感知**的进程里，抓图拿到的本来就是
        物理像素，此值**恒为 1.0**，整套换算里根本不需要出现缩放系数
        （架构备忘 §1.5 明确"不要写 dpi_scale 这种配置项"）。
        它只服务于**DPI-unaware 抓图路径**（该路径下位图被虚拟化成逻辑像素）。
    virtual_origin_x / virtual_origin_y
        虚拟桌面左上角在物理坐标里的位置。多屏时**可能为负**（副屏在主屏左侧）。
    surface_width / surface_height
        物理坐标面的尺寸（多屏时 = 虚拟桌面尺寸）。用于越界判定；None 表示不判定。
    """

    capture_width: int
    capture_height: int
    model_input_width: Optional[int] = None
    model_input_height: Optional[int] = None
    capture_to_physical_scale: float = 1.0
    virtual_origin_x: int = 0
    virtual_origin_y: int = 0
    surface_width: Optional[int] = None
    surface_height: Optional[int] = None
    surface_origin_x: Optional[int] = None
    surface_origin_y: Optional[int] = None

    def __post_init__(self) -> None:
        if self.capture_width <= 0 or self.capture_height <= 0:
            raise ValueError("截图尺寸必须为正数")
        if self.capture_to_physical_scale <= 0:
            raise ValueError("capture_to_physical_scale 必须为正数")
        # 未给模型输入尺寸时，默认与截图同尺寸（等价于"预处理未缩放"）。
        object.__setattr__(self, "model_input_width", self.model_input_width or self.capture_width)
        object.__setattr__(self, "model_input_height", self.model_input_height or self.capture_height)
        # 物理坐标面的原点：缺省用虚拟桌面原点。
        object.__setattr__(self, "surface_origin_x", self.surface_origin_x if self.surface_origin_x is not None else self.virtual_origin_x)
        object.__setattr__(self, "surface_origin_y", self.surface_origin_y if self.surface_origin_y is not None else self.virtual_origin_y)

    # ---- 便捷派生量 ----

    @property
    def surface_left(self) -> float:
        return float(self.surface_origin_x)

    @property
    def surface_top(self) -> float:
        return float(self.surface_origin_y)

    @property
    def surface_right(self) -> float:
        if self.surface_width is None:
            return math.inf
        return self.surface_left + self.surface_width

    @property
    def surface_bottom(self) -> float:
        if self.surface_height is None:
            return math.inf
        return self.surface_top + self.surface_height

    @property
    def capture_scale_x(self) -> float:
        """截图 → 模型输入 的横向缩放系数（模型输入像素 / 截图像素）。"""
        return self.model_input_width / self.capture_width

    @property
    def capture_scale_y(self) -> float:
        """截图 → 模型输入 的纵向缩放系数。"""
        return self.model_input_height / self.capture_height

    def with_capture_origin(self, origin_x: int, origin_y: int) -> "FrameGeometry":
        """派生一个副本，把截图原点改到 (origin_x, origin_y)（物理像素，虚拟桌面坐标）。"""
        return FrameGeometry(
            capture_width=self.capture_width,
            capture_height=self.capture_height,
            model_input_width=self.model_input_width,
            model_input_height=self.model_input_height,
            capture_to_physical_scale=self.capture_to_physical_scale,
            virtual_origin_x=self.virtual_origin_x,
            virtual_origin_y=self.virtual_origin_y,
            surface_width=self.surface_width,
            surface_height=self.surface_height,
            surface_origin_x=self.surface_origin_x,
            surface_origin_y=self.surface_origin_y,
        )

    def physical_width(self) -> float:
        """截图在物理表面上的宽度（物理像素）。"""
        return self.capture_width * self.capture_to_physical_scale

    def physical_height(self) -> float:
        """截图在物理表面上的高度（物理像素）。"""
        return self.capture_height * self.capture_to_physical_scale


class CoordinateAdapter:
    """把模型 grounding 输出换算成物理屏幕坐标。

    量纲通过构造参数 ``model_scale`` 显式配置，**不写死**。
    典型用法::

        adapter = CoordinateAdapter(model_scale=ModelScale.RELATIVE_0_1000)
        px, py = adapter.to_screen(781.0, 432.0, frame)

    每一层换算都是独立公开方法，可单独调用、单独断言。
    """

    def __init__(
        self,
        model_scale: ModelScale,
        *,
        out_of_bounds_tolerance_px: float = DEFAULT_OUT_OF_BOUNDS_TOLERANCE_PX,
        strict_model_range: bool = True,
    ):
        if not isinstance(model_scale, ModelScale):
            raise ModelScaleError(f"model_scale 必须是 ModelScale，收到 {type(model_scale)!r}")
        self.model_scale = model_scale
        self.out_of_bounds_tolerance_px = float(out_of_bounds_tolerance_px)
        self.strict_model_range = bool(strict_model_range)

    # ------------------------------------------------------------------
    # 第 0 层：截图 ↔ 模型输入（预处理缩放）
    # ------------------------------------------------------------------

    def capture_to_model_input(self, cx: float, cy: float, frame: FrameGeometry) -> tuple:
        """**截图像素 → 模型输入像素**。

        用于断言"预处理到底缩了多少"，以及在绝对像素标度下建立基准。
        """
        return (cx * frame.capture_scale_x, cy * frame.capture_scale_y)

    def model_input_to_capture(self, mx: float, my: float, frame: FrameGeometry) -> tuple:
        """**模型输入像素 → 截图像素**（``capture_to_model_input`` 的逆变换）。

        绝对像素标度（``ABSOLUTE_MODEL_INPUT``）正是靠这一步回到截图空间。
        """
        return (mx / frame.capture_scale_x, my / frame.capture_scale_y)

    # ------------------------------------------------------------------
    # 第 1 层：模型空间 → 相对空间
    # ------------------------------------------------------------------

    def model_to_relative(self, mx: float, my: float, frame: FrameGeometry) -> tuple:
        """**模型空间 → 相对空间 [0,1]×[0,1]**。

        归一化标度按名义分母相除；绝对标度先换算到截图空间再除以截图尺寸。
        """
        scale = self.model_scale
        if scale.is_relative:
            denom = scale.denominator
            return (mx / denom, my / denom)
        if scale is ModelScale.ABSOLUTE_CAPTURE:
            return (mx / frame.capture_width, my / frame.capture_height)
        # ABSOLUTE_MODEL_INPUT
        cx, cy = self.model_input_to_capture(mx, my, frame)
        return (cx / frame.capture_width, cy / frame.capture_height)

    # ------------------------------------------------------------------
    # 第 2 层：相对空间 → 截图像素
    # ------------------------------------------------------------------

    def relative_to_capture(self, rx: float, ry: float, frame: FrameGeometry) -> tuple:
        """**相对空间 [0,1] → 截图像素**。"""
        return (rx * frame.capture_width, ry * frame.capture_height)

    # ------------------------------------------------------------------
    # 第 3 层：截图像素 → 物理表面
    # ------------------------------------------------------------------

    def capture_to_physical(self, cx: float, cy: float, frame: FrameGeometry) -> tuple:
        """**截图像素 → 物理表面像素**。

        换算内容：``物理 = 截图像素 × capture_to_physical_scale + 虚拟桌面原点``。
        截图像素与物理像素 1:1 时（PMv2 感知路径），scale = 1.0。
        """
        return (
            cx * frame.capture_to_physical_scale + frame.virtual_origin_x,
            cy * frame.capture_to_physical_scale + frame.virtual_origin_y,
        )

    def physical_to_capture(self, px: float, py: float, frame: FrameGeometry) -> tuple:
        """**物理表面像素 → 截图像素**（``capture_to_physical`` 的逆变换）。"""
        return (
            (px - frame.virtual_origin_x) / frame.capture_to_physical_scale,
            (py - frame.virtual_origin_y) / frame.capture_to_physical_scale,
        )

    # ------------------------------------------------------------------
    # 组合换算
    # ------------------------------------------------------------------

    def model_to_capture(self, mx: float, my: float, frame: FrameGeometry) -> tuple:
        """**模型空间 → 截图像素**（第 1、2 层合并）。"""
        rx, ry = self.model_to_relative(mx, my, frame)
        return self.relative_to_capture(rx, ry, frame)

    def to_screen(
        self,
        rel_x: float,
        rel_y: float,
        frame: FrameGeometry,
        *,
        safe_area: Optional[Rect] = None,
    ) -> tuple:
        """**模型空间 → 物理表面像素**（完整链路 + 越界判定）。

        参数
        ----
        rel_x, rel_y
            模型返回的原始数值。量纲由 ``self.model_scale`` 决定
            （0~1 / 0~999 / 0~1000 / 绝对像素）。
        frame
            该帧的元数据（截图宽高、模型输入宽高、物理缩放因子、虚拟桌面原点）。
        safe_area
            可选的物理坐标白名单矩形；给了就额外做白名单校验。

        越界行为
        --------
        **不 clamp，直接抛 :class:`CoordinateOutOfBounds`**（容差
        ``out_of_bounds_tolerance_px``，默认 2 物理像素）。

        返回
        ----
        ``(px, py)``：物理表面像素，浮点。原点在虚拟桌面左上角。
        """
        self.assert_model_output_in_range(rel_x, rel_y, frame)
        cx, cy = self.model_to_capture(rel_x, rel_y, frame)
        px, py = self.capture_to_physical(cx, cy, frame)
        self.assert_within_surface(px, py, frame)
        if safe_area is not None:
            self.assert_safe_area(px, py, safe_area)
        return (px, py)

    def from_screen(self, px: float, py: float, frame: FrameGeometry) -> tuple:
        """**物理表面像素 → 模型空间**（``to_screen`` 的逆变换，不做越界判定）。

        用于把"已知的物理位置"编码成模型坐标，或做往返自检。
        """
        cx, cy = self.physical_to_capture(px, py, frame)
        rx, ry = (cx / frame.capture_width, cy / frame.capture_height)
        scale = self.model_scale
        if scale.is_relative:
            denom = scale.denominator
            return (rx * denom, ry * denom)
        if scale is ModelScale.ABSOLUTE_CAPTURE:
            return (cx, cy)
        mx, my = self.capture_to_model_input(cx, cy, frame)
        return (mx, my)

    # ------------------------------------------------------------------
    # 越界 / 白名单判定
    # ------------------------------------------------------------------

    def _model_unit_tolerance(self, frame: FrameGeometry) -> tuple:
        """把"物理像素容差"换算成模型单位的容差 (tol_x, tol_y)。"""
        tol = self.out_of_bounds_tolerance_px
        if self.model_scale.is_relative:
            denom = self.model_scale.denominator
            return (tol / frame.capture_width * denom, tol / frame.capture_height * denom)
        if self.model_scale is ModelScale.ABSOLUTE_CAPTURE:
            return (tol / frame.capture_to_physical_scale, tol / frame.capture_to_physical_scale)
        return (
            tol / frame.capture_to_physical_scale * frame.capture_scale_x,
            tol / frame.capture_to_physical_scale * frame.capture_scale_y,
        )

    def model_output_bounds(self, frame: FrameGeometry) -> tuple:
        """模型空间下该帧合法的数值区间 ``(min_x, max_x, min_y, max_y)``（含容差外扩）。"""
        tol_x, tol_y = self._model_unit_tolerance(frame)
        scale = self.model_scale
        if scale is ModelScale.RELATIVE_0_1:
            return (-tol_x, 1.0 + tol_x, -tol_y, 1.0 + tol_y)
        if scale.is_relative:
            denom = scale.denominator
            return (-tol_x, denom + tol_x, -tol_y, denom + tol_y)
        if scale is ModelScale.ABSOLUTE_CAPTURE:
            return (
                -tol_x,
                frame.capture_width * frame.capture_to_physical_scale + tol_x,
                -tol_y,
                frame.capture_height * frame.capture_to_physical_scale + tol_y,
            )
        # ABSOLUTE_MODEL_INPUT
        return (
            -tol_x,
            frame.model_input_width + tol_x,
            -tol_y,
            frame.model_input_height + tol_y,
        )

    def assert_model_output_in_range(self, mx: float, my: float, frame: FrameGeometry) -> None:
        """校验模型输出在其标度声明的数值区间内；越界抛 :class:`CoordinateOutOfBounds`。"""
        if not self.strict_model_range:
            return
        min_x, max_x, min_y, max_y = self.model_output_bounds(frame)
        if not (min_x <= mx <= max_x and min_y <= my <= max_y):
            raise CoordinateOutOfBounds(
                f"模型输出 ({mx}, {my}) 超出 {self.model_scale.value} 的合法区间 "
                f"({min_x:.3f}, {max_x:.3f}) × ({min_y:.3f}, {max_y:.3f})",
                tolerance_px=self.out_of_bounds_tolerance_px,
                value=(mx, my),
            )

    def assert_within_surface(self, px: float, py: float, frame: FrameGeometry) -> None:
        """校验物理坐标落在物理表面（含容差）内；越界抛 :class:`CoordinateOutOfBounds`。"""
        tol = self.out_of_bounds_tolerance_px
        if not (
            frame.surface_left - tol <= px <= frame.surface_right + tol
            and frame.surface_top - tol <= py <= frame.surface_bottom + tol
        ):
            raise CoordinateOutOfBounds(
                f"物理坐标 ({px:.1f}, {py:.1f}) 越出物理表面 "
                f"[{frame.surface_left}, {frame.surface_right}] × "
                f"[{frame.surface_top}, {frame.surface_bottom}]（容差 {tol} px）",
                tolerance_px=tol,
                value=(px, py),
            )

    def in_safe_area(self, px: float, py: float, safe_area: Rect, *, tolerance_px: Optional[float] = None) -> bool:
        """物理坐标是否落在 `safe_area` 白名单内（不抛异常，供上层决策）。"""
        tol = self.out_of_bounds_tolerance_px if tolerance_px is None else tolerance_px
        return safe_area.contains(px, py, tolerance_px=tol)

    def assert_safe_area(self, px: float, py: float, safe_area: Rect) -> None:
        """物理坐标必须落在 `safe_area` 内，否则抛 :class:`SafeAreaViolation`。"""
        if not self.in_safe_area(px, py, safe_area):
            raise SafeAreaViolation(
                f"物理坐标 ({px:.1f}, {py:.1f}) 不在 safe_area "
                f"[{safe_area.left}, {safe_area.right}] × [{safe_area.top}, {safe_area.bottom}] 内"
            )

    # ------------------------------------------------------------------

    def describe(self) -> str:
        """一行式描述当前配置，便于写进动作日志。"""
        return (
            f"CoordinateAdapter(scale={self.model_scale.value}, "
            f"tol={self.out_of_bounds_tolerance_px}px)"
        )

"""**两级 grounding**（视觉通道 L2，工单 M6）。

一次定位分两级：

1. **粗定位（L0）** —— 在整屏图上定位目标，得到一个粗略位置。
   整屏坐标**只用来决定「看哪一块」**，永远不直接点击。
2. **精定位（L1）/ crop 精定位** —— 以粗定位点为中心，从**原始物理分辨率图**
   上 1:1 裁一块窗口送给模型；**最终落点只在 crop 尺度上产生**
   （crop 内坐标 + 裁剪原点偏移 = 全屏物理坐标）。

精定位失败（无输出 / 输出越界 / 调用失败）→ **回落到粗定位结果**并继续执行该步，
追踪记录标记回落与回落原因。粗定位本身为空或越界时**不存在可回落结果**：
报定位失败，不落点、不挪到最近的可行位置。

**分母来自标定**：换算用的标度由调用方从标定结果给出（构造参数，**没有默认值**）；
代码里不出现写死的 1000 / 999，也不按数值大小猜量纲（架构决策备忘 §2.6 C-04）。
"""

from __future__ import annotations

from minicode.action.errors import ErrorCode
from minicode.action.image import encode_frame_base64
from minicode.action.ports import (
    CapturedFrame,
    GroundingEcho,
    VisualLocateRequest,
    VisualLocateResult,
)
from minicode.coord.coordinates import (
    DEFAULT_OUT_OF_BOUNDS_TOLERANCE_PX,
    CoordinateAdapter,
    CoordinateError,
    ModelScale,
)
from minicode.coord.crop_refine import DEFAULT_CROP_HEIGHT_PX, DEFAULT_CROP_WIDTH_PX, CropRefiner
from minicode.model.effort import Effort
from minicode.model.ports import ModelCaller

from .crop import crop_frame
from .geometry import crop_plan_fields, full_screen_geometry, surface_rect
from .prompt import GroundingParseError, coarse_prompt, parse_point, refine_prompt

__all__ = ["TwoStageGrounder"]

#: 粗定位这一级的名字（回显与追踪记录里用）。
STAGE_COARSE = "coarse"
#: 精定位这一级的名字（回显与追踪记录里用）。
STAGE_REFINE = "refine"


def _image_source(frame: CapturedFrame) -> str:
    """图像来源（截图 / 从原始帧裁出的 crop / 回源重抓）—— 追踪记录据此判定裁剪来源。"""
    return f"{frame.source}|{frame.backend}"


class TwoStageGrounder:
    """两级 grounding 的通道本体。

    参数
    ----
    model
        模型调用端口（:class:`minicode.model.ports.ModelCaller`）。
    scale
        **标定判定的标度**（换算链路的分母来源）。**必填**，没有默认值。
    model_input_size
        **模型实际看到的那张整屏图**的尺寸（来自标定记录）。
        服务端可能把大图缩小，本机实测 MiniMax 端点把 2560 宽的图缩到约 2048 宽 ——
        绝对像素标度的基准必须是**模型看到的那张图**，不是我们发出去的那张。
    full_screen_size
        整屏物理帧应有的尺寸（= 标定探针的尺寸）。整屏帧尺寸对不上就**拒绝换算**：
        模型输入尺寸不可复用时继续跑只会得到静默偏移。
    crop_width / crop_height
        裁剪窗口尺寸（物理像素）。缺省取冻结原型的默认值（架构备忘 §1.3：512×512）。
    crop_model_input_size
        **模型实际看到的那张 crop 图**的尺寸。缺省 = crop 尺寸（1:1，服务端不缩放
        这张小图；本机实测）。
    effort
        思考预算档位（**必须显式给**，架构备忘 §2.6 C-12）。
    """

    def __init__(
        self,
        model: ModelCaller,
        *,
        scale: ModelScale,
        model_input_size: tuple[int, int],
        full_screen_size: tuple[int, int],
        crop_width: int = DEFAULT_CROP_WIDTH_PX,
        crop_height: int = DEFAULT_CROP_HEIGHT_PX,
        crop_model_input_size: tuple[int, int] | None = None,
        effort: Effort | str = Effort.LOW,
        max_tokens: int = 2000,
        out_of_bounds_tolerance_px: float = DEFAULT_OUT_OF_BOUNDS_TOLERANCE_PX,
        target_radius_px: float = 0.0,
    ):
        self.model = model
        self.scale = scale
        self.model_input_size = (int(model_input_size[0]), int(model_input_size[1]))
        self.full_screen_size = (int(full_screen_size[0]), int(full_screen_size[1]))
        self.crop_model_input_size = crop_model_input_size
        self.effort = effort
        self.max_tokens = max_tokens
        self.out_of_bounds_tolerance_px = float(out_of_bounds_tolerance_px)
        #: 目标半径（物理像素）。本单的两级 grounding 的粗定位**只给点不给框**，
        #: 因此缺省 0 —— 裁剪计划保证的是**粗定位点**完整落在窗口内；
        #: 给了半径才会判定「目标框是否完整包含」（``target_fully_contained``）。
        self.target_radius_px = float(target_radius_px)
        self.adapter = CoordinateAdapter(
            scale, out_of_bounds_tolerance_px=self.out_of_bounds_tolerance_px
        )
        self.refiner = CropRefiner(
            self.adapter, crop_width=crop_width, crop_height=crop_height
        )

    # ------------------------------------------------------------------
    # 主流程
    # ------------------------------------------------------------------

    def locate(self, request: VisualLocateRequest) -> VisualLocateResult:
        """跑一次两级定位，返回最终落点 + 观测数据。"""
        frame = request.frame
        if (frame.width, frame.height) != self.full_screen_size:
            return VisualLocateResult(
                error_code=ErrorCode.LOCATE_FAILED,
                error_message=(
                    f"整屏帧尺寸 {frame.width}×{frame.height} 与标定探针尺寸 "
                    f"{self.full_screen_size[0]}×{self.full_screen_size[1]} 不一致 —— "
                    "模型输入尺寸不可复用，拒绝换算"
                ),
                scale=self.scale.value,
            )

        echoes: list[GroundingEcho] = []
        model_calls = 0
        # -- 粗定位（L0）：整屏 ------------------------------------------------
        try:
            raw = self._ask(coarse_prompt(request.target_description), frame)
        except Exception as exc:
            return self._failure(f"粗定位失败：{exc}", echoes, model_calls)
        model_calls += 1
        echoes.append(
            GroundingEcho(
                stage=STAGE_COARSE,
                raw_x=raw[0],
                raw_y=raw[1],
                # **模型实际看到的那张图**的尺寸（服务端可能把大图缩小 ——
                # 本机实测 2560 宽 → 约 2048 宽）。回显报这个尺寸，模型才能
                # 对账自己给的坐标。
                sent_width=self.model_input_size[0],
                sent_height=self.model_input_size[1],
                image_source=_image_source(frame),
            )
        )
        geometry = full_screen_geometry(
            full_screen_size=self.full_screen_size,
            model_input_size=self.model_input_size,
            surface=request.surface,
            origin=(frame.left, frame.top),
        )
        try:
            coarse = self.adapter.to_screen(raw[0], raw[1], geometry)
        except CoordinateError as exc:
            return self._failure(f"粗定位结果不可用：{exc}", echoes, model_calls)

        # -- 裁剪计划：从原始物理分辨率图上取窗口 --------------------------
        try:
            plan = self.refiner.plan(
                coarse,
                surface_rect(request.surface),
                target_radius_px=self.target_radius_px,
            )
            crop = crop_frame(frame, plan)
        except (CoordinateError, ValueError) as exc:
            # 裁剪计划/裁剪本身失败 → **报定位失败**（不落点、不挪到别处）。
            return self._failure(f"裁剪窗口不可用：{exc}", echoes, model_calls)

        # -- 精定位（L1）：crop 内 -------------------------------------------
        crop_input = self.crop_model_input_size or (plan.width, plan.height)
        try:
            raw_refine = self._ask(refine_prompt(request.target_description), crop)
        except Exception as exc:
            return self._fallback(coarse, plan, echoes, model_calls, f"精定位失败：{exc}")
        model_calls += 1
        echoes.append(
            GroundingEcho(
                stage=STAGE_REFINE,
                raw_x=raw_refine[0],
                raw_y=raw_refine[1],
                sent_width=crop_input[0],
                sent_height=crop_input[1],
                image_source=_image_source(crop),
            )
        )
        crop_geometry = self.refiner.crop_frame(
            plan,
            model_input_size=crop_input,
        )
        try:
            refined = self.adapter.to_screen(raw_refine[0], raw_refine[1], crop_geometry)
        except CoordinateError as exc:
            return self._fallback(
                coarse, plan, echoes, model_calls, f"精定位结果不可用：{exc}"
            )

        return VisualLocateResult(
            landing_point=(int(round(refined[0])), int(round(refined[1]))),
            coarse_point=coarse,
            crop_plan=crop_plan_fields(plan),
            echoes=tuple(echoes),
            model_calls=model_calls,
            scale=self.scale.value,
        )

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------

    def _ask(self, prompt: str, frame: CapturedFrame) -> tuple[float, float]:
        """问模型一次坐标。**数值原样带回**（不乘分母、不猜量纲）。"""
        image_base64 = encode_frame_base64(frame)
        if image_base64 is None:
            raise GroundingParseError("画面编不成 PNG（像素数与尺寸对不上），送不出图")
        reply = self.model.complete(
            prompt,
            effort=self.effort,
            max_tokens=self.max_tokens,
            image_base64=image_base64,
        )
        return parse_point(reply.text)

    def _fallback(
        self,
        coarse: tuple[float, float],
        plan,
        echoes: list[GroundingEcho],
        model_calls: int,
        reason: str,
    ) -> VisualLocateResult:
        """精定位失败 → **回落到粗定位结果**并继续执行该步。"""
        return VisualLocateResult(
            landing_point=(int(round(coarse[0])), int(round(coarse[1]))),
            coarse_point=coarse,
            crop_plan=crop_plan_fields(plan),
            fallback=True,
            fallback_reason=reason,
            echoes=tuple(echoes),
            model_calls=model_calls,
            scale=self.scale.value,
        )

    def _failure(
        self, message: str, echoes: list[GroundingEcho], model_calls: int
    ) -> VisualLocateResult:
        """粗定位失败 → **没有可回落的结果**（不落点、不挪到最近的可行位置）。"""
        return VisualLocateResult(
            error_code=ErrorCode.LOCATE_FAILED,
            error_message=message,
            echoes=tuple(echoes),
            model_calls=model_calls,
            scale=self.scale.value,
        )

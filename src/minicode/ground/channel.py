"""**两级 grounding**（视觉通道 L2，工单 M6）+ **精定位门控**（工单 M7）。

一次定位分两级：

1. **粗定位（L0）** —— 在整屏图上定位目标，得到一个粗略位置。
   整屏坐标**只用来决定「看哪一块」**，永远不直接点击。
2. **精定位（L1）/ crop 精定位** —— 以粗定位点为中心，从**原始物理分辨率图**
   上 1:1 裁一块窗口送给模型；**最终落点只在 crop 尺度上产生**
   （crop 内坐标 + 裁剪原点偏移 = 全屏物理坐标）。

**第二遍不是每步必做**（工单 M7；架构决策备忘 §2.6 C-16）：先过**门控**
（:mod:`minicode.ground.gate`），命中才开；不命中就直接拿粗定位结果落点
（省一次模型调用，也避免在本来就找对的文字上引入漂移）。
⚠️ **这是坐标契约的一条例外**：门控未命中时最终落点取自**整屏粗定位点**，
不是 crop 尺度上的点（见 `docs/00-现状.md` §3 与 ADR-0006 的补充说明）。

**失败回落**（工单 M7 AC 3）：精定位失败（模型无输出 / 输出越界 / 调用失败 /
**结果明显偏离粗定位**）→ 回落到粗定位结果并继续执行该步，追踪记录标记
回落与回落原因。粗定位本身为空或越界时**没有可回落的结果**：开的是一次
**整屏第二遍**（不是围绕粗点的裁剪窗口；也不是逐字重发同一条请求），
它也失败就**报定位失败** —— 不落点、不挪到最近的可行位置。

**分母来自标定**：换算用的标度由调用方从标定结果给出（构造参数，**没有默认值**）；
代码里不出现写死的 1000 / 999，也不按数值大小猜量纲（架构决策备忘 §2.6 C-04）。
"""

from __future__ import annotations

import math
from collections.abc import Callable

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
    CoordinateOutOfBounds,
    ModelScale,
)
from minicode.coord.crop_refine import DEFAULT_CROP_HEIGHT_PX, DEFAULT_CROP_WIDTH_PX, CropRefiner
from minicode.model.effort import Effort
from minicode.model.ports import ModelCaller

from .crop import crop_frame
from .gate import (
    FallbackCause,
    GateDecision,
    GateInputs,
    GateMode,
    GateParams,
    default_gate_params,
    evaluate_gate,
)
from .geometry import crop_plan_fields, full_screen_geometry, surface_rect
from .prompt import (
    GroundingParseError,
    coarse_prompt,
    parse_point,
    refine_prompt,
    whole_screen_prompt,
)

__all__ = [
    "TwoStageGrounder",
    "SecondOpinion",
    "ask_model_point",
    "default_crop_size",
]

#: **第二次独立粗定位**的端口形状（工单 M8 用它把 C-16 的「一致性」条件变成可判定）。
#:
#: 拿到的是**模型自己那个坐标系**里的原始坐标（不换算）—— 换算由本模块的
#: 适配器做（**坐标换算链路只有一个真值来源**）。
#: 拿不到就返回 ``None`` → 一致性条件**不可判定**（照旧显式记进追踪记录）。
SecondOpinion = Callable[[CapturedFrame, str], "tuple[float, float] | None"]


def default_crop_size() -> tuple[int, int]:
    """**裁剪窗口的默认尺寸**（物理像素）—— 取冻结原型的那份默认值。

    评测台要报「裁剪参数」，就得报**实际用的那个尺寸**（不另写一份常量）。
    """
    return (DEFAULT_CROP_WIDTH_PX, DEFAULT_CROP_HEIGHT_PX)


def ask_model_point(
    model: ModelCaller,
    prompt: str,
    frame: CapturedFrame,
    *,
    effort: Effort | str,
    max_tokens: int,
) -> tuple[float, float]:
    """问模型要一个点（**数值原样带回**，不乘分母、不猜量纲）。

    粗定位与「第二次独立粗定位」都走这里 —— **「问模型要一个点」只有这一处实现**。
    """
    image_base64 = encode_frame_base64(frame)
    if image_base64 is None:
        raise GroundingParseError("画面编不成 PNG（像素数与尺寸对不上），送不出图")
    reply = model.complete(
        prompt,
        effort=effort,
        max_tokens=max_tokens,
        image_base64=image_base64,
    )
    return parse_point(reply.text)

#: 粗定位这一级的名字（回显与追踪记录里用）。
STAGE_COARSE = "coarse"
#: 精定位这一级的名字（回显与追踪记录里用）。
STAGE_REFINE = "refine"
#: **整屏第二遍**这一级的名字（粗定位为空 / 越界时开的那一遍）。
STAGE_WHOLE_SCREEN = "whole_screen"


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
        思考预算档位（**必须显式给**，架构决策备忘 §2.6 C-12）。
    target_radius_px
        目标半径（物理像素）。**给了它，门控的「目标占比」才判得了**；
        没给就是「只给点不给框」→ 占比**不可判定**（工单 M7 AC 1）。
    second_opinion
        **第二次独立粗定位**（可空）。给了它，C-16 的「两次粗定位不一致」
        这一条才**可判定**；不给就是**不可判定**（工单 M7 已记下的现状：
        生产接线拿不到第二个粗定位结果）。工单 M8 的评测用它当门控输入 ——
        两次独立粗定位互相矛盾，通常意味着这个目标小或含糊，值不值得再看一眼。
        ⚠️ 它**必须是另一次问**（逐字重发同一条请求会拿到同一个答案，
        确定性模型下「一致性」永远成立）。
    gate_params
        **门控参数**（运行时参数 + 取值来源）。缺省 = 带来源的默认值。
    gate_mode
        ``GateMode.ON_DEMAND``（**门控开**：命中才做第二遍，工单 M7 默认）/
        ``GateMode.ALWAYS``（**门控关**：无条件精定位 = M6 的行为）。
        ⚠️ 门控关这一档是**工单 M8「门控开不低于门控关」的对照组**用的，
        兼作 M6 那批「精定位路径」单测的入口 —— 没有它，M6 的用例无法单独盯精定位。
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
        gate_params: GateParams | None = None,
        gate_mode: GateMode | str = GateMode.ON_DEMAND,
        second_opinion: SecondOpinion | None = None,
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
        #: 给了半径才会判定「目标框是否完整包含」（``target_fully_contained``），
        #: 也才判得了门控的「目标占比」。
        self.target_radius_px = float(target_radius_px)
        self.gate_params = gate_params or default_gate_params()
        self.gate_mode = GateMode(gate_mode)
        self.second_opinion = second_opinion
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
        geometry = full_screen_geometry(
            full_screen_size=self.full_screen_size,
            model_input_size=self.model_input_size,
            surface=request.surface,
            origin=(frame.left, frame.top),
        )

        # -- 粗定位（L0）：整屏 ------------------------------------------------
        coarse: tuple[float, float] | None = None
        coarse_issue = ""
        coarse_out_of_bounds = False
        try:
            raw = self._ask(coarse_prompt(request.target_description), frame)
        except Exception as exc:
            raw = None
            coarse_issue = f"粗定位失败：{exc}"
        if raw is not None:
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
            try:
                coarse = self.adapter.to_screen(raw[0], raw[1], geometry)
            except CoordinateOutOfBounds as exc:
                coarse_out_of_bounds = True
                coarse_issue = f"粗定位结果越界：{exc}"
            except CoordinateError as exc:
                coarse_issue = f"粗定位结果不可用：{exc}"

        # -- 门控：命中才开第二遍（工单 M7 AC 1）-----------------------------
        left, top, right, bottom = (int(value) for value in request.surface)
        second_coarse = self._second_coarse_point(request, frame, geometry)
        decision = evaluate_gate(
            GateInputs(
                coarse_point=coarse,
                coarse_out_of_bounds=coarse_out_of_bounds,
                target_radius_px=self.target_radius_px or None,
                coarse_confidence=None,
                second_coarse_point=second_coarse,
                surface_size=(right - left, bottom - top),
            ),
            self.gate_params,
        )
        refine_enabled = decision.hit or self.gate_mode is GateMode.ALWAYS

        if not refine_enabled:
            # 不命中门控 → 直接拿粗定位结果落点（**省一次模型调用**）。
            # ⚠️ 这是**坐标契约的一条例外**（门控开时的正常路径）：最终落点
            # 取自整屏粗定位点，不是 crop 尺度上的点。见 `docs/00-现状.md` §3
            # 与 `docs/adr/0006-crop-local-coordinate-contract.md` 的补充说明。
            if coarse is None:
                return self._failure(coarse_issue or "粗定位不可用", echoes, model_calls, decision)
            return self._result(
                landing_point=(int(round(coarse[0])), int(round(coarse[1]))),
                coarse=coarse,
                plan=None,
                decision=decision,
                refine_enabled=False,
                echoes=echoes,
                model_calls=model_calls,
            )

        if coarse is None:
            # 粗定位本身为空 / 越界 → 开的是**整屏第二遍**（不是围绕粗点的裁剪窗口）。
            return self._whole_screen(request, frame, geometry, echoes, model_calls, decision, coarse_issue)

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
            return self._failure(f"裁剪窗口不可用：{exc}", echoes, model_calls, decision)

        # -- 精定位（L1）：crop 内 -------------------------------------------
        crop_input = self.crop_model_input_size or (plan.width, plan.height)
        try:
            raw_refine = self._ask(refine_prompt(request.target_description), crop)
        except GroundingParseError as exc:
            return self._result(
                landing_point=(int(round(coarse[0])), int(round(coarse[1]))),
                coarse=coarse,
                plan=plan,
                decision=decision,
                refine_enabled=True,
                fallback=True,
                fallback_cause=FallbackCause.NO_OUTPUT.value,
                fallback_reason=f"精定位失败：{exc}",
                echoes=echoes,
                model_calls=model_calls,
            )
        except Exception as exc:
            return self._result(
                landing_point=(int(round(coarse[0])), int(round(coarse[1]))),
                coarse=coarse,
                plan=plan,
                decision=decision,
                refine_enabled=True,
                fallback=True,
                fallback_cause=FallbackCause.CALL_FAILED.value,
                fallback_reason=f"精定位失败：{exc}",
                echoes=echoes,
                model_calls=model_calls,
            )
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
            return self._result(
                landing_point=(int(round(coarse[0])), int(round(coarse[1]))),
                coarse=coarse,
                plan=plan,
                decision=decision,
                refine_enabled=True,
                fallback=True,
                fallback_cause=FallbackCause.OUT_OF_BOUNDS.value,
                fallback_reason=f"精定位结果不可用：{exc}",
                echoes=echoes,
                model_calls=model_calls,
            )

        # -- 偏离粗定位的量级（工单 M7 AC 3）：选到相邻的同类控件 → 回落 ----
        deviation = math.hypot(refined[0] - coarse[0], refined[1] - coarse[1])
        if deviation > self.gate_params.deviation_tolerance_px:
            return self._result(
                landing_point=(int(round(coarse[0])), int(round(coarse[1]))),
                coarse=coarse,
                plan=plan,
                decision=decision,
                refine_enabled=True,
                fallback=True,
                fallback_cause=FallbackCause.DEVIATION.value,
                fallback_reason=(
                    f"精定位结果偏离粗定位 {deviation:.1f} px"
                    f"（阈值 {self.gate_params.deviation_tolerance_px:.1f} px）"
                ),
                echoes=echoes,
                model_calls=model_calls,
            )

        return self._result(
            landing_point=(int(round(refined[0])), int(round(refined[1]))),
            coarse=coarse,
            plan=plan,
            decision=decision,
            refine_enabled=True,
            echoes=echoes,
            model_calls=model_calls,
        )

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------

    def _second_coarse_point(
        self,
        request: VisualLocateRequest,
        frame: CapturedFrame,
        geometry,
    ) -> tuple[float, float] | None:
        """**第二次独立粗定位**的结果（物理表面空间）。

        拿不到（没给端口 / 调用失败 / 坐标不可用）→ ``None``
        → 一致性条件**不可判定**（照旧显式记进追踪记录，不静默）。
        """
        if self.second_opinion is None:
            return None
        try:
            raw = self.second_opinion(frame, request.target_description)
        except Exception:
            return None
        if raw is None:
            return None
        try:
            return self.adapter.to_screen(raw[0], raw[1], geometry)
        except CoordinateError:
            return None

    def _ask(self, prompt: str, frame: CapturedFrame) -> tuple[float, float]:
        """问模型一次坐标（走 :func:`ask_model_point` —— 只有那一处实现）。"""
        return ask_model_point(
            self.model,
            prompt,
            frame,
            effort=self.effort,
            max_tokens=self.max_tokens,
        )

    def _whole_screen(
        self,
        request: VisualLocateRequest,
        frame: CapturedFrame,
        geometry,
        echoes: list[GroundingEcho],
        model_calls: int,
        decision: GateDecision,
        coarse_issue: str,
    ) -> VisualLocateResult:
        """粗定位为空 / 越界 → **整屏第二遍**（不是围绕粗点的裁剪窗口）。

        ⚠️ 它**不是逐字重发**同一条请求：提示词里带上了「上一次没能给出可用的位置」
        这条信息（见 :func:`minicode.ground.prompt.whole_screen_prompt`），
        且这一遍**显式记进回显**（``stage="whole_screen"``）—— 不是静默重试。

        它也失败时 → **报定位失败**：粗定位本身不可用，**没有可回落的结果**
        （工单 M7 AC 3：不得落点、不得挪到最近的可行位置）。
        """
        retry_reason = coarse_issue or "没有给出坐标"
        try:
            raw = self._ask(
                whole_screen_prompt(request.target_description, retry_reason=retry_reason),
                frame,
            )
        except Exception as exc:
            return self._failure(f"整屏第二遍失败：{exc}", echoes, model_calls, decision)
        model_calls += 1
        echoes.append(
            GroundingEcho(
                stage=STAGE_WHOLE_SCREEN,
                raw_x=raw[0],
                raw_y=raw[1],
                sent_width=self.model_input_size[0],
                sent_height=self.model_input_size[1],
                image_source=_image_source(frame),
            )
        )
        try:
            point = self.adapter.to_screen(raw[0], raw[1], geometry)
        except CoordinateError as exc:
            return self._failure(f"整屏第二遍结果不可用：{exc}", echoes, model_calls, decision)
        return self._result(
            landing_point=(int(round(point[0])), int(round(point[1]))),
            coarse=None,
            plan=None,
            decision=decision,
            refine_enabled=False,
            echoes=echoes,
            model_calls=model_calls,
        )

    def _result(
        self,
        *,
        landing_point: tuple[int, int],
        coarse: tuple[float, float] | None,
        plan,
        decision: GateDecision,
        refine_enabled: bool,
        echoes: list[GroundingEcho],
        model_calls: int,
        fallback: bool = False,
        fallback_cause: str = "",
        fallback_reason: str = "",
    ) -> VisualLocateResult:
        """一次定位的结果（门控字段 + 回落字段一起落）。"""
        return VisualLocateResult(
            landing_point=landing_point,
            coarse_point=coarse,
            crop_plan=crop_plan_fields(plan) if plan is not None else None,
            gate_hit=decision.hit,
            gate_reasons=tuple(reason.value for reason in decision.reasons),
            gate_undecidable=tuple(condition.value for condition in decision.undecidable),
            refine_enabled=refine_enabled,
            fallback=fallback,
            fallback_cause=fallback_cause,
            fallback_reason=fallback_reason,
            echoes=tuple(echoes),
            model_calls=model_calls,
            scale=self.scale.value,
        )

    def _failure(
        self,
        message: str,
        echoes: list[GroundingEcho],
        model_calls: int,
        decision: GateDecision | None = None,
    ) -> VisualLocateResult:
        """粗定位失败 → **没有可回落的结果**（不落点、不挪到最近的可行位置）。

        ⚠️ **门控的判定字段照样落盘**（哪怕这次定位失败了）：
        「判不了的条件必须显式记进追踪记录」—— 失败的那一次也该记
        （否则最需要查的那类失败恰好没有埋点）。
        """
        return VisualLocateResult(
            error_code=ErrorCode.LOCATE_FAILED,
            error_message=message,
            gate_hit=decision.hit if decision is not None else False,
            gate_reasons=tuple(
                reason.value for reason in (decision.reasons if decision else ())
            ),
            gate_undecidable=tuple(
                condition.value for condition in (decision.undecidable if decision else ())
            ),
            refine_enabled=False,
            echoes=tuple(echoes),
            model_calls=model_calls,
            scale=self.scale.value,
        )

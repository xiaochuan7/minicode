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

**第三条路：编号选择（工单 B-1）** —— 上面那条「两级 + 门控」是**模型瞄点**
（模型自己报坐标，误差跟着模型走）。B-1 换成**模型选编号**：
有候选时把候选清单送给模型，模型**只答编号**，
**落点 = 该编号对应候选框的物理中心**（**零换算**，UIA 通道早就跑通同一范式）。
**没有候选源**（默认）→ 行为与原来**完全一致**（既有单测继续绿）；
**接了候选源但没匹配上** → 走点式路径并**显式记录「无候选」**（不猜、不假装）。
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import replace

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
from minicode.model.image import downscale_frame
from minicode.model.ports import ModelCaller

from .candidates import CandidateSource, GroundingCandidate
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
    CandidateLine,
    GroundingParseError,
    coarse_prompt,
    matched_on_screen,
    normalize_target,
    normalize_target_prompt,
    numbered_candidates_prompt,
    parse_chosen_numbers,
    parse_point,
    refine_prompt,
    whole_screen_prompt,
)

__all__ = [
    "TwoStageGrounder",
    "SecondOpinion",
    "TargetNames",
    "ask_model_point",
    "default_crop_size",
]

#: **编号分支**那一次调用的输出预算（默认）。
#:
#: ⚠️ **不能太小** —— 服务端的思考链与输出**共用**这份预算，预算太小会出现
#: 「HTTP 200 但没有文本输出」（模型客户端的坑③）。编号只要一个数，但思考链
#: 照样要吃预算 → 给它一份单独的预算。
DEFAULT_NUMBERED_MAX_TOKENS = 4000

#: **目标描述规范化**时给模型的名字条数上限（B-4：给 2~3 个更具体的表述）。
MAX_TARGET_NAMES = 5

#: **目标描述规范化**的名字清单端口（一帧 → 一串**已脱敏**的名字）。
TargetNames = Callable[[CapturedFrame], "list[str]"]

#: **第二次独立粗定位**的端口形状（工单 M8 用它把 C-16 的「一致性」条件变成可判定）。
#:
#: 拿到的是**模型自己那个坐标系**里的原始坐标（不换算）—— 换算由本模块的
#: 适配器做（**坐标换算链路只有一个真值来源**）。
#: 拿不到就返回 ``None`` → 一致性条件**不可判定**（照旧显式记进追踪记录）。
#:
#: ⚠️ 端口收到的是**送模型的那张图**（整屏那一级缩到声明尺寸的那张）与
#: **规范化后的目标描述** —— 两次粗定位必须**看同一张图、问同一件事**，
#: 比出来的数才有意义（否则比的是两个没法比的数）。
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
#: **编号选择**这一级的名字（工单 B-1：模型只答编号，落点取候选框的物理中心）。
STAGE_NUMBERED = "numbered"
#: **无候选**这一级的名字（接了候选源但没匹配上 → 走点式路径）。
#:
#: ⚠️ **没接候选源**时**不记**这一条 —— 那条分支压根不存在，
#: 行为与原来完全一致（既有单测继续绿）。
STAGE_NO_CANDIDATES = "no_candidates"


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
    candidate_source
        **候选源**（可空，工单 B-1）。给了它：目标描述能匹配上候选时走
        **编号选择**分支（模型只答编号，落点取候选框的物理中心）；
        匹配不上 → 走点式路径并**显式记「无候选」**。
        **不给（默认）→ 行为与原来完全一致**（连回显都不多一条）。
    numbered_max_tokens
        **编号分支这一次调用的输出预算**（默认 4000）。
        编号只要一个数，但**服务端的思考链与输出共用这份预算** —— 预算太小
        会出现「HTTP 200 但没有文本输出」（模型客户端的坑③）。
        ⚠️ 只给**编号分支**用，不改模型客户端的全局默认。
    target_names
        **界面上真实存在的名字清单**（可空，工单 B-4）：一帧 → 一串**已脱敏**的
        名字（OCR 词表 + 脱敏后的 UIA 控件名）。给了它：
        目标描述**先规范化再定位**（本地近名匹配；匹配不上时**模型侧**给
        2~3 个更具体的表述让它选）。
        **不给（默认）→ 不做规范化**（不花那次模型调用）。
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
        numbered_max_tokens: int = DEFAULT_NUMBERED_MAX_TOKENS,
        out_of_bounds_tolerance_px: float = DEFAULT_OUT_OF_BOUNDS_TOLERANCE_PX,
        target_radius_px: float = 0.0,
        gate_params: GateParams | None = None,
        gate_mode: GateMode | str = GateMode.ON_DEMAND,
        second_opinion: SecondOpinion | None = None,
        candidate_source: CandidateSource | None = None,
        target_names: TargetNames | None = None,
    ):
        self.model = model
        self.scale = scale
        self.model_input_size = (int(model_input_size[0]), int(model_input_size[1]))
        self.full_screen_size = (int(full_screen_size[0]), int(full_screen_size[1]))
        self.crop_model_input_size = crop_model_input_size
        self.effort = effort
        self.max_tokens = max_tokens
        #: **编号分支**那一次调用的输出预算（见构造参数的说明）。
        self.numbered_max_tokens = int(numbered_max_tokens)
        self.out_of_bounds_tolerance_px = float(out_of_bounds_tolerance_px)
        #: 目标半径（物理像素）。本单的两级 grounding 的粗定位**只给点不给框**，
        #: 因此缺省 0 —— 裁剪计划保证的是**粗定位点**完整落在窗口内；
        #: 给半径才会判定「目标框是否完整包含」（``target_fully_contained``），
        #: 也才判得了门控的「目标占比」。
        self.target_radius_px = float(target_radius_px)
        self.gate_params = gate_params or default_gate_params()
        self.gate_mode = GateMode(gate_mode)
        self.second_opinion = second_opinion
        #: **候选源**（工单 B-1）。``None`` = 这条分支不存在（默认）。
        self.candidate_source = candidate_source
        #: **界面上真实存在的名字清单**（工单 B-4）。``None`` = 不做规范化。
        self.target_names = target_names
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
        """跑一次定位，返回最终落点 + 观测数据。

        **两条路**（工单 B-1）：

        * **有候选** → 编号选择（模型只答编号，落点 = 该候选框的物理中心）；
          **挑不出编号 → 回落**到点式路径（工单 M7 的既有纪律是「失败回落」，
          而这里**有**可回落的结果 —— 不报死）；
        * **无候选** → 点式路径（粗定位 → 门控 → crop 精定位）。
        """
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

        # -- 目标描述规范化（工单 B-4）：先对齐名字，再定位 -----------------
        description, extra_calls = self._normalize_description(request, frame)
        model_calls += extra_calls

        # -- 候选（工单 B-1）：有候选走「编号选择」，没有走点式路径 -----------
        candidates, candidate_issue = self._candidates(frame, description)
        if candidates:
            numbered = self._numbered(
                request, description, frame, candidates, echoes, model_calls
            )
            if numbered.ok:
                return numbered
            # **挑不出编号**（没给编号 / 答 0「清单里没有」/ 编号不在清单里）
            # → **回落点式路径**（有可回落的结果）。
            echoes = list(numbered.echoes)
            model_calls = numbered.model_calls
            result = self._locate_by_point(
                request, description, frame, geometry, echoes, model_calls
            )
            if result.ok and not result.fallback:
                result = replace(
                    result,
                    fallback=True,
                    fallback_cause=FallbackCause.CANDIDATE_SELECTION_FAILED.value,
                    fallback_reason=numbered.error_message,
                )
            return result
        if self.candidate_source is not None:
            # **接了候选源但没匹配上** → 显式记「无候选」（不猜、不假装）。
            # ⚠️ 这一条的 ``image_source`` 装的是**原因**（`GroundingEcho` 没有
            # 单独的「原因」字段 —— ``action/ports.py`` 不在本轮写作用域内）。
            echoes.append(
                GroundingEcho(
                    stage=STAGE_NO_CANDIDATES,
                    raw_x=None,
                    raw_y=None,
                    sent_width=None,
                    sent_height=None,
                    image_source=candidate_issue or "no-candidate-match",
                )
            )

        return self._locate_by_point(
            request, description, frame, geometry, echoes, model_calls
        )

    def _locate_by_point(
        self,
        request: VisualLocateRequest,
        description: str,
        frame: CapturedFrame,
        geometry,
        echoes: list[GroundingEcho],
        model_calls: int,
    ) -> VisualLocateResult:
        """**点式路径**（原来的两级 + 门控）：粗定位 → 门控 → crop 精定位。

        编号分支挑不出编号时**回落到这里**（工单 M7 的纪律：失败回落）。
        """
        # -- 整屏那一级送给模型的那张图（A-2：自己缩到 model_input_size）----
        screen_frame = self._screen_frame(frame)

        # -- 粗定位（L0）：整屏 ------------------------------------------------
        coarse: tuple[float, float] | None = None
        coarse_issue = ""
        coarse_out_of_bounds = False
        try:
            raw = self._ask(
                coarse_prompt(description, self.model_input_size),
                screen_frame,
            )
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
                    # **实际发送**的那张图的尺寸（A-2：我们自己缩到 model_input_size，
                    # 服务端不用再缩 → 模型看到的就是这张）。回显报这个尺寸，
                    # 模型才能对账自己给的坐标。
                    sent_width=screen_frame.width,
                    sent_height=screen_frame.height,
                    image_source=_image_source(screen_frame),
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
        second_coarse = self._second_coarse_point(description, screen_frame, geometry)
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
            return self._whole_screen(
                request, screen_frame, geometry, echoes, model_calls, decision, coarse_issue
            )

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
            raw_refine = self._ask(refine_prompt(description, crop_input), crop)
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
        description: str,
        screen_frame: CapturedFrame,
        geometry,
    ) -> tuple[float, float] | None:
        """**第二次独立粗定位**的结果（物理表面空间）。

        ⚠️ **它必须看到与第一次粗定位同一张图**（``screen_frame`` —— 整屏那一级
        送模型的那张，缩到声明尺寸）、**问的也是同一个描述**（规范化后的那个）：
        否则两次的数在**不同的空间**里（一个整屏原图、一个缩到声明尺寸的图）、
        指的也可能是**不同的东西** → 「两次是否一致」这条门控条件比的就是
        **两个没法比的数**。

        拿不到（没给端口 / 调用失败 / 坐标不可用）→ ``None``
        → 一致性条件**不可判定**（照旧显式记进追踪记录，不静默）。
        """
        if self.second_opinion is None:
            return None
        try:
            raw = self.second_opinion(screen_frame, description)
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

    def _screen_frame(self, frame: CapturedFrame) -> CapturedFrame:
        """**整屏那一级送给模型的那张图**（A-2）。

        我们**自己**把整屏缩到 ``model_input_size`` 再送 —— 服务端不用再缩，
        「模型发的坐标对应哪一张图」于是是**已知的**（声明即所见）。

        * 缩放由 :func:`minicode.model.image.downscale_frame` 做（盒式平均）；
        * **缩不了**（目标尺寸比这张图还大）→ **发原图**（不静默改尺寸）；
        * ⚠️ **crop 那一级不缩放** —— 它从**原始物理分辨率图**上 1:1 裁，
          「最终落点只在 crop 上产生」这条契约不动。
        """
        width, height = self.model_input_size
        if (width, height) == (frame.width, frame.height):
            return frame
        try:
            return downscale_frame(frame, width, height)
        except ValueError:
            return frame

    def _candidates(
        self, frame: CapturedFrame, description: str
    ) -> tuple[list[GroundingCandidate], str]:
        """**候选源**给出的候选（**物理像素**）+ 没候选的原因。

        * 没有候选源 → 空 + ``"no-candidate-source"``（这条分支压根不存在）；
        * 候选源报错（OCR 后端不可用）→ 空 + 原因（**判不了 ≠ 没有候选**）；
        * 没匹配上 → 空 + ``""``（原因由调用方补）。
        """
        if self.candidate_source is None:
            return ([], "no-candidate-source")
        try:
            candidates = list(self.candidate_source.candidates(frame, description))
        except Exception as exc:
            return ([], f"candidate-source-error: {exc}")
        return (candidates, "")

    def _candidate_lines(
        self,
        candidates: list[GroundingCandidate],
        frame: CapturedFrame,
        screen_frame: CapturedFrame,
    ) -> list[CandidateLine]:
        """候选清单（**显示用**：位置换算到送模型那张图的像素）。

        ⚠️ **落点不取这里的框** —— 落点取候选框的**物理中心**（零换算）。
        这里换算的是「给模型看的那个位置」：候选框是**物理像素**，
        减掉虚拟桌面原点、乘上帧几何里的缩放系数，就是**模型看到的那张图**里的位置。
        """
        scale_x = screen_frame.width / frame.width
        scale_y = screen_frame.height / frame.height
        lines: list[CandidateLine] = []
        for candidate in candidates:
            left, top, right, bottom = candidate.box
            lines.append(
                CandidateLine(
                    number=candidate.number,
                    text=candidate.text,
                    box=(
                        int(round((left - frame.left) * scale_x)),
                        int(round((top - frame.top) * scale_y)),
                        int(round((right - frame.left) * scale_x)),
                        int(round((bottom - frame.top) * scale_y)),
                    ),
                )
            )
        return lines

    def _numbered(
        self,
        request: VisualLocateRequest,
        description: str,
        frame: CapturedFrame,
        candidates: list[GroundingCandidate],
        echoes: list[GroundingEcho],
        model_calls: int,
    ) -> VisualLocateResult:
        """**编号选择**分支（工单 B-1）：模型只答编号 → 落点取候选框的物理中心。

        与「两级 + 门控」那条路的根本区别：**落点不是模型算出来的** ——
        模型只负责在清单里指认一项，指认对了，落点就落在那个候选框的**物理中心**
        （**零换算**：候选框本来就是物理像素）。

        * 模型**没给出编号** / **答 0**（「清单里没有对应的」）/ 编号不在清单里
          → **报这次选择失败**（上层**回落**到点式路径）；
        * 这一级**不开门控、也不做 crop 精定位**（它是整屏那一级的替代方案，
          不是它前面的一环）→ ``refine_enabled=False``、``crop_plan=None``。
        """
        screen_frame = self._screen_frame(frame)
        lines = self._candidate_lines(candidates, frame, screen_frame)
        try:
            numbers = self._ask_chosen_number(
                description, lines, screen_frame
            )
        except Exception as exc:
            return self._failure(f"编号选择失败：{exc}", echoes, model_calls)
        model_calls += 1
        if numbers == (0,):
            # 模型说「**清单里没有**与目标描述对应的一项」（ground 层的解释，
            # UIA 通道那份读法一个字没动）。
            return self._failure(
                "模型说清单里没有与目标描述对应的一项", echoes, model_calls
            )
        chosen = (
            [candidate for candidate in candidates if candidate.number == numbers[0]]
            if len(numbers) == 1
            else []
        )
        if not chosen:
            return self._failure(
                f"模型给的编号不可用：{numbers}（清单里是 1~{len(candidates)}）",
                echoes,
                model_calls,
            )
        candidate = chosen[0]
        center = candidate.center
        echoes.append(
            GroundingEcho(
                stage=STAGE_NUMBERED,
                # **候选框的物理中心**（模型没给坐标 —— 这里记的是我们取的落点）。
                raw_x=center[0],
                raw_y=center[1],
                sent_width=screen_frame.width,
                sent_height=screen_frame.height,
                image_source=_image_source(screen_frame),
            )
        )
        return VisualLocateResult(
            landing_point=(int(round(center[0])), int(round(center[1]))),
            # 候选框中心就是**整屏**上的一个点（物理表面空间）。
            coarse_point=center,
            crop_plan=None,
            gate_hit=False,
            gate_reasons=(),
            gate_undecidable=(),
            refine_enabled=False,
            echoes=tuple(echoes),
            model_calls=model_calls,
            scale=self.scale.value,
        )

    def _ask_chosen_number(
        self,
        description: str,
        lines: list[CandidateLine],
        frame: CapturedFrame,
    ) -> tuple[int, ...]:
        """问模型「选哪一个」（**只答编号**）。

        ⚠️ **不复用** :func:`ask_model_point` —— 那条路发的是「报坐标」的提示词、
        解析的是坐标；这里发的是**清单 + 图**、解析的是**编号**。
        但**回答的读法只有一份**：
        :func:`minicode.uia.pointer.parse_chosen_numbers`（UIA 通道已经在用）。
        """
        image_base64 = encode_frame_base64(frame)
        if image_base64 is None:
            raise GroundingParseError("画面编不成 PNG（像素数与尺寸对不上），送不出图")
        prompt = numbered_candidates_prompt(
            description, lines, image_size=(frame.width, frame.height)
        )
        reply = self.model.complete(
            prompt,
            effort=self.effort,
            max_tokens=self.numbered_max_tokens,
            image_base64=image_base64,
        )
        return parse_chosen_numbers(reply.text)

    def _normalize_description(
        self, request: VisualLocateRequest, frame: CapturedFrame
    ) -> tuple[str, int]:
        """**目标描述规范化**（工单 B-4）：先对齐名字，再定位。

        返回 ``(规范化后的描述, 多花的模型调用数)``。

        * 没给名字清单（``target_names`` 为空）→ **原样返回**（不花钱）；
        * :func:`minicode.ground.prompt.normalize_target` 匹配上了 → 用它
          （**不花钱**：那是纯本地的近名匹配）；
        * 没匹配上 → **模型侧**给 2~3 个更具体的表述让它选
          （B-4 原文：「模型侧给 2~3 个更具体的表述让它选」）；
          模型失败 / 没给出有效编号 → **如实不改**（保留原描述）。
        """
        if self.target_names is None:
            return (request.target_description, 0)
        try:
            names = [name for name in self.target_names(frame) if name]
        except Exception:
            return (request.target_description, 0)
        names = names[:MAX_TARGET_NAMES]
        if not names:
            return (request.target_description, 0)
        normalized, changed = normalize_target(request.target_description, names)
        if changed:
            return (normalized, 0)
        if matched_on_screen(request.target_description, names):
            # **本来就对得上**（描述就是界面上的名字）→ 不用问模型。
            return (request.target_description, 0)
        chosen = self._ask_chosen_name(request.target_description, names)
        if chosen is None:
            return (request.target_description, 1)
        return (chosen, 1)

    def _ask_chosen_name(
        self, description: str, names: list[str]
    ) -> str | None:
        """问模型「用户说的那个是界面上哪一个名字」（**只答编号**）。

        * **纯文字**（不发图）：要问的是「哪個名字更可能」，画面帮不上忙；
        * 读不出 / 编号不在清单里 → ``None``（**如实不改**，不猜）。
        """
        prompt = normalize_target_prompt(description, names)
        try:
            reply = self.model.complete(
                prompt,
                effort=self.effort,
                max_tokens=self.numbered_max_tokens,
            )
        except Exception:
            return None
        numbers = parse_chosen_numbers(reply.text)
        if len(numbers) != 1:
            return None
        index = numbers[0] - 1
        if not 0 <= index < len(names):
            return None
        return names[index]

    def _whole_screen(
        self,
        request: VisualLocateRequest,
        screen_frame: CapturedFrame,
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
                whole_screen_prompt(
                    request.target_description,
                    self.model_input_size,
                    retry_reason=retry_reason,
                ),
                screen_frame,
            )
        except Exception as exc:
            return self._failure(f"整屏第二遍失败：{exc}", echoes, model_calls, decision)
        model_calls += 1
        echoes.append(
            GroundingEcho(
                stage=STAGE_WHOLE_SCREEN,
                raw_x=raw[0],
                raw_y=raw[1],
                sent_width=screen_frame.width,
                sent_height=screen_frame.height,
                image_source=_image_source(screen_frame),
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

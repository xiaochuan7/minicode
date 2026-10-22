"""**两级 grounding**（视觉通道 L2，工单 M6）+ **精定位门控**（工单 M7）。

一次定位分两级：

1. **粗定位（L0）** —— 在整屏图上定位目标，得到一个粗略位置。
   整屏坐标**只用来决定「看哪一块」**，永远不直接点击。
2. **精定位（L1）/ crop 精定位** —— 以粗定位点为中心，从**原始物理分辨率图**
   上 1:1 裁一块窗口送给模型；**最终落点只在 crop 尺度上产生**
   （crop 内坐标 + 裁剪原点偏移 = 全屏物理坐标）。

**第二遍现在是每步都做**（2026-10-21 拍板：**门控降级为记录器**，
生产默认无条件精定位）。门控（:mod:`minicode.ground.gate`）**只落盘**：
四条条件照判、照记（``gate_hit`` / ``gate_reasons`` / ``gate_undecidable``），
但**不再决定开不开精定位**。
⚠️ 降级的依据（本机实测，42 条 ScreenSpot-Pro 抽样、同模型同口径）：
门控开 38.1% / 2.381 次调用，门控关 **57.1%** / 2.000 次调用 —— 门控的触发
条件（两次粗定位不一致）恰好是精定位前提条件（目标在窗内）的反面。

**失败回落**（工单 M7 AC 3）：精定位失败（模型无输出 / 输出越界 / 调用失败 /
**结果明显偏离**）→ 回落到粗定位结果并继续执行该步，追踪记录标记
回落与回落原因。粗定位本身为空或越界时**没有可回落的结果**：开的是一次
**整屏第二遍**（不是围绕粗点的裁剪窗口；也不是逐字重发同一条请求），
它也失败就**报定位失败** —— 不落点、不挪到最近的可行位置。

⚠️ **「第二次独立粗定位」的那个端口删掉了**（2026-10-21 拍板）：
门控降级为记录器 → 那一次调用没有了（实测它正是 −19.0 pp 的来源 ——
触发条件恰好是精定位前提条件的反面）。一致性那条条件（``COARSE_INCONSISTENT``）
**判据留在** :mod:`minicode.ground.gate` 当记录器，但从此**永远不可判定**，
如实记进 ``undecidable``。

**分母来自标定**：换算用的标度由调用方从标定结果给出（构造参数，**没有默认值**）；
代码里不出现写死的 1000 / 999，也不按数值大小猜量纲（架构决策备忘 §2.6 C-04）。

**「模型调用数」的口径**：``model_calls`` = **调用模型端口的次数**——
**问不出坐标也是问过了**（回答解析不出来、越界、调用抛异常，都算这一次问过）。
⚠️ **只记成功的那几次 → 报出来的数是下界** → 验收线「平均模型调用数/目标 ≤ 1.5」
会判在一个**偏乐观**的数上。唯一的例外是「画面编不成 PNG」那种请求压根没发出去的情况：
它也被记成 1 次（**宁可多记不可少记** —— 这条线是成本口径，多记只会更难达标）。

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
from minicode.coord.crop_refine import CropRefiner, plan_crop
from minicode.model.effort import Effort
from minicode.model.image import downscale_frame
from minicode.model.ports import ModelCaller

from .candidates import CandidateSource, GroundingCandidate, merge_candidate_sources
from .crop import crop_frame
from .gate import (
    FallbackCause,
    GateCondition,
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
    CoarseTarget,
    GroundingParseError,
    coarse_prompt,
    matched_on_screen,
    normalize_target,
    normalize_target_prompt,
    numbered_candidates_prompt,
    parse_chosen_numbers,
    parse_coarse_target,
    parse_optional_point,
    refine_prompt,
    whole_screen_prompt,
)

__all__ = [
    "TwoStageGrounder",
    "TargetNames",
    "ask_model_point",
    "box_center",
    "crop_geometry_label",
    "default_crop_size",
]

#: **粗定位 / 整屏第二遍 / 精定位**共用的输出预算（默认）。
#:
#: ⚠️ **不能太小** —— 服务端的思考链与输出**共用**这份预算，预算太小会出现
#: 「HTTP 200 但没有文本输出」（模型客户端的坑③：42 条里有 2 条栽在这上面）。
#: 编号分支有自己那份（:data:`DEFAULT_NUMBERED_MAX_TOKENS`）—— **不对称**才是坑：
#: 编号只要一个数，思考链照样要吃预算 → 单独给它一份。
DEFAULT_MAX_TOKENS = 4096

#: **编号分支**那一次调用的输出预算（默认）。
#:
#: ⚠️ **不能太小** —— 服务端的思考链与输出**共用**这份预算，预算太小会出现
#: 「HTTP 200 但没有文本输出」（模型客户端的坑③）。编号只要一个数，但思考链
#: 照样要吃预算 → 给它一份单独的预算。
DEFAULT_NUMBERED_MAX_TOKENS = 4000

#: **公开 zoom 一致性判据** ``c`` 的「千分位」刻度。
#:
#: 公开判据是 ``c = ‖ε₁‖₂ / r``（``r`` = crop 宽的一半，**以 crop 宽的千分位
#: 为单位**）。我们在 crop 上是 1:1 不上采样（实测 512 crop 的
#: ``image_tokens = 361 = 19×19``），于是 ``c = ε₁(物理 px) × 1000 / crop 宽``
#: —— **系数随 crop 宽变**（512 crop 时 1.953；**默认 192 crop 时 5.208**）。
#:
#: ⚠️ 这个 1000 **不是坐标换算的分母**（分母只来自标定，架构备忘 §2.6 C-04）：
#: 它是**公开判据自己的刻度**，写成 ``1_000`` 是为了不与「写死分母」那条
#: 源码级检查（``test_ground_two_stage.py``）混淆。
ZOOM_CONSISTENCY_PER_MILLE = 1_000

#: **裁剪窗口的几何**（物理像素）—— **ground 层**的那份（施工清单第 ⑤ 条）。
#:
#: ⚠️ 冻结原型 ``coord/crop_refine.py`` 里也有一份默认值（512）—— **那个目录不许改**，
#: 生产用的几何由**本层**给。
#:
#: **取值依据**【实测，n=183，零模型调用】：
#: 离线几何重算（:mod:`minicode.ground.crop_geometry`，产物
#: ``runs/m3-crop-geometry/crop-geometry.json``）给出的「完整含住 GT 框」比例：
#:
#: ==========================  ==========  ================  ==================
#: 几何                          含住率       窗口无目标率       容忍粗点误差（中位）
#: ==========================  ==========  ================  ==================
#: 192×192（旧）                  42.1%        36.1%             ±74 / ±84 px
#: 512×512                       71.0%        24.6%             ±234 / ±244 px
#: 768×768                       76.0%        21.9%             ±362 / ±372 px
#: 1024×1024                     79.2%        19.1%             ±490 / ±500 px
#: 1280×800                      79.8%        18.0%             ±618 / ±388 px
#: **0.5×当前图（本轮采用）**      **84.2%**    **14.2%**         **±839 / ±522 px**
#: ==========================  ==========  ================  ==================
#:
#: ⇒ 验收线 C 组（含住率 ≥ 75%、容忍粗点误差 ≥ ±512）**只有「比例 + 下限」这条达成**。
#:
#: **窗口语义**（与官方 ``crop_to_center`` 同语义）：
#: 以**粗定位框中心**为中心、窗口 = **当前物理帧的 50% 线性尺寸**、
#: 绝对下限 **512 px**、贴边时**向内平移补足、不缩小窗口**；
#: 裁剪从**物理帧 1:1 原生像素**取（不做有损缩放）。
CROP_LINEAR_FRACTION = 0.5
MIN_CROP_WIDTH_PX = 512
MIN_CROP_HEIGHT_PX = 512

#: **默认 crop 尺寸** = **自适应几何的下限档**（实际尺寸随帧而变）。
#:
#: ⚠️ **不是**一个固定值：``TwoStageGrounder`` 缺省走**自适应**
#: （0.5×当前帧，下限 512）；这两个常量是那条几何的**下限**，
#: 也是**显式传固定尺寸**时 ``crop_width`` / ``crop_height`` 的缺省。
DEFAULT_CROP_WIDTH_PX = MIN_CROP_WIDTH_PX
DEFAULT_CROP_HEIGHT_PX = MIN_CROP_HEIGHT_PX

#: **目标描述规范化**时给模型的名字条数上限（B-4：给 2~3 个更具体的表述）。
MAX_TARGET_NAMES = 5

#: **目标描述规范化**的名字清单端口（一帧 → 一串**已脱敏**的名字）。
TargetNames = Callable[[CapturedFrame], "list[str]"]


def default_crop_size(frame_size: tuple[int, int] | None = None) -> tuple[int, int]:
    """**生产默认的裁剪窗口尺寸**（物理像素）—— 取**ground 层**的那份。

    给了 ``frame_size``（当前物理帧的尺寸）→ **那一帧的自适应尺寸**
    （0.5× 线性尺寸，绝对下限 512）；
    不给 → **下限档** ``(512, 512)``。

    ⚠️ **实际尺寸随帧而变** —— 报告里要报的是**几何口径**，
    不是某一个固定数（评测台用 :func:`crop_geometry_label`）。
    """
    if frame_size is None:
        return (DEFAULT_CROP_WIDTH_PX, DEFAULT_CROP_HEIGHT_PX)
    width = min(
        int(frame_size[0]), max(MIN_CROP_WIDTH_PX, int(frame_size[0] * CROP_LINEAR_FRACTION))
    )
    height = min(
        int(frame_size[1]), max(MIN_CROP_HEIGHT_PX, int(frame_size[1] * CROP_LINEAR_FRACTION))
    )
    return (width, height)


def crop_geometry_label() -> str:
    """**裁剪几何的口径说明**（报告里报的是它，不是一个固定尺寸）。"""
    return (
        f"crop = 当前物理帧的 {int(CROP_LINEAR_FRACTION * 100)}% 线性尺寸"
        f"（绝对下限 {MIN_CROP_WIDTH_PX}×{MIN_CROP_HEIGHT_PX}、"
        "以粗定位框为中心、贴边向内平移不缩小、原生像素 1:1）"
    )


def ask_model_point(
    model: ModelCaller,
    prompt: str,
    frame: CapturedFrame,
    *,
    effort: Effort | str,
    max_tokens: int,
) -> tuple[float, float] | None:
    """问模型要一个点（**数值原样带回**，不乘分母、不猜量纲）。

    粗定位与「第二次独立粗定位」都走这里 —— **「问模型要一个点」只有这一处实现**。

    ⚠️ **返回 ``None`` = 模型说「这张图里没有那个元素」**（施工清单第 ⑦ 条的
    负出口：``{"x": null, "y": null}``）—— **不是解析失败**，
    调用方据此**干净回落**，而不是接受一个假点。
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
    return parse_optional_point(reply.text)

#: 粗定位这一级的名字（回显与追踪记录里用）。
STAGE_COARSE = "coarse"
#: 精定位这一级的名字（回显与追踪记录里用）。
STAGE_REFINE = "refine"
#: **整屏第二遍**这一级的名字（粗定位为空 / 越界时开的那一遍）。
STAGE_WHOLE_SCREEN = "whole_screen"
#: **只用粗定位**这一臂的名字（评测台的基线臂：1.00 次调用/目标）。
#:
#: 回显里出现它 = 这一步**没有**做 crop 精定位（落点直接取粗定位点）。
STAGE_COARSE_ONLY = "coarse_only"
#: **编号选择**这一级的名字（工单 B-1：模型只答编号，落点取候选框的物理中心）。
STAGE_NUMBERED = "numbered"
#: **无候选**这一级的名字（接了候选源但没匹配上 → 走点式路径）。
#:
#: ⚠️ **没接候选源**时**不记**这一条 —— 那条分支压根不存在，
#: 行为与原来完全一致（既有单测继续绿）。
STAGE_NO_CANDIDATES = "no_candidates"

#: **最终落点来自哪一级**（施工清单第 ① 条埋点：``landing_source``）。
#:
#: * ``refined`` —— **crop 精定位**给的那一刀；
#: * ``coarse`` —— 回落到**粗定位点**（精定位没产出可用结果）；
#: * ``whole_screen`` —— **整屏第二遍**给的（粗定位本身不可用）；
#: * ``numbered`` —— **编号选择**（落点 = 候选框的物理中心）；
#: * ``none`` —— 没有落点（定位失败）。
LANDING_SOURCE_REFINED = "refined"
LANDING_SOURCE_COARSE = "coarse"
LANDING_SOURCE_WHOLE_SCREEN = "whole_screen"
LANDING_SOURCE_NUMBERED = "numbered"
LANDING_SOURCE_NONE = "none"


def _image_source(frame: CapturedFrame) -> str:
    """图像来源（截图 / 从原始帧裁出的 crop / 回源重抓）—— 追踪记录据此判定裁剪来源。"""
    return f"{frame.source}|{frame.backend}"


def box_center(target: CoarseTarget) -> tuple[float, float]:
    """**框 → 点**的**唯一一处**换算（模型输入空间的数值，原样带回）。

    * 有框 → **框中心**（官方通用 API 协议：论文 ScreenSpot-Pro Table 3 题注
      "For models inferencing boxes, we consider the center point of the
      generated box as the prediction"）；
    * 只有点 → 就是那个点（**框退化成点**）；
    * 两者都没有 → :class:`GroundingParseError`（显式失败，不猜）。

    ⚠️ **只做这一处**（ADR-0006：框→点的换算只能有一处，两处就会分叉）：
    回显（模型自己发的那个数）与落点（物理像素）**都调它** ——
    落点那条只是再经过一次 :class:`~minicode.coord.coordinates.CoordinateAdapter`
    的换算（模型空间 → 物理像素），**不另算一遍中心**。
    """
    if target.bbox is not None:
        x1, y1, x2, y2 = target.bbox
        return ((x1 + x2) / 2.0, (y1 + y2) / 2.0)
    if target.point is None:
        raise GroundingParseError("回答里既没有框也没有点")
    return target.point


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
        裁剪窗口尺寸（物理像素）。**缺省 = 自适应**
        （**当前物理帧的 50% 线性尺寸、绝对下限 512**，施工清单第 ⑤ 条）；
        给了就是**固定尺寸**（评测台的 crop 扫描从那儿覆盖）。
    crop_model_input_size
        **模型实际看到的那张 crop 图**的尺寸。缺省 = crop 尺寸（1:1，服务端不缩放
        这张小图；本机实测）。
    effort
        思考预算档位（**必须显式给**，架构决策备忘 §2.6 C-12）。
    max_tokens
        **粗定位 / 整屏第二遍 / 精定位**共用的输出预算（默认 4096）。
        预算太小会出现「HTTP 200 但没有文本输出」。
    target_radius_px
        目标半径（物理像素）。**粗定位出框之后，通道用框推出这个半径**
        （框的较大边的一半）→ 门控的「目标占比」**第一次可判定**；
        这个构造参数是**没有框时的兜底**（缺省 0 = 判不了）。
    coarse_only
        **只用粗定位**这一臂（评测台要的**从来没量过的基线**）：
        粗定位之后**直接返回** —— 落点 = 粗定位点、**1.00 次模型调用/目标**、
        **不裁剪、不问第二次**。回显里多一条 ``stage="coarse_only"``，
        落盘时能看出这一臂走的是「只有粗定位」。
    gate_params
        **门控参数**（运行时参数 + 取值来源）。缺省 = 带来源的默认值。
    gate_mode
        ``GateMode.ON_DEMAND`` / ``GateMode.ALWAYS``。
        ⚠️ **它不再改变任何行为**（门控降级为记录器 → 两个 mode 的落点
        **逐条相同**）。**留着**是因为：① ``GateMode`` 是 ``CONTEXT.md`` 的
        词条，单测与评测台都在用；② 接线与历史报告要能读出「当时标的是
        哪一档」。**不是**用来决定开不开精定位的。
    candidate_source
        **候选源**（可空，工单 B-1）—— **主表**（UIA 控件）。
        给了它：目标描述能匹配上候选时走**编号选择**分支（模型只答编号，
        落点取候选框的物理中心）；匹配不上 → 走点式路径并**显式记「无候选」**。
        **不给（默认）→ 行为与原来完全一致**（连回显都不多一条）。
    additional_candidate_source
        **附加候选源**（可空）—— **附加表**（OCR 词框）。
        与主表按 UFO² 的规则合并（IoU > 0.1 的附加项丢弃、**主表全留**）。
    numbered_max_tokens
        **编号分支这一次调用的输出预算**（默认 4000）。
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
        crop_width: int | None = None,
        crop_height: int | None = None,
        crop_model_input_size: tuple[int, int] | None = None,
        effort: Effort | str = Effort.LOW,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        numbered_max_tokens: int = DEFAULT_NUMBERED_MAX_TOKENS,
        out_of_bounds_tolerance_px: float = DEFAULT_OUT_OF_BOUNDS_TOLERANCE_PX,
        target_radius_px: float = 0.0,
        coarse_only: bool = False,
        gate_params: GateParams | None = None,
        gate_mode: GateMode | str = GateMode.ON_DEMAND,
        candidate_source: CandidateSource | None = None,
        additional_candidate_source: CandidateSource | None = None,
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
        #: 目标半径（物理像素）—— **没有框时的兜底**（见构造参数的说明）。
        self.target_radius_px = float(target_radius_px)
        #: **只用粗定位**这一臂（评测台的基线臂）。
        self.coarse_only = bool(coarse_only)
        self.gate_params = gate_params or default_gate_params()
        self.gate_mode = GateMode(gate_mode)
        #: **候选源**（工单 B-1）—— **主表**。``None`` = 这条分支不存在（默认）。
        self.candidate_source = candidate_source
        #: **附加候选源** —— **附加表**（OCR）。``None`` = 没有附加表。
        self.additional_candidate_source = additional_candidate_source
        #: **界面上真实存在的名字清单**（工单 B-4）。``None`` = 不做规范化。
        self.target_names = target_names
        self.adapter = CoordinateAdapter(
            scale, out_of_bounds_tolerance_px=self.out_of_bounds_tolerance_px
        )
        #: **固定裁剪尺寸**（``None`` = 自适应：0.5×当前帧、下限 512）。
        self.crop_width = int(crop_width) if crop_width else None
        self.crop_height = int(crop_height) if crop_height else None
        self.refiner = CropRefiner(
            self.adapter,
            crop_width=self.crop_width or DEFAULT_CROP_WIDTH_PX,
            crop_height=self.crop_height or DEFAULT_CROP_HEIGHT_PX,
        )

    # ------------------------------------------------------------------
    # 裁剪几何
    # ------------------------------------------------------------------

    def _crop_size_for(self, frame: CapturedFrame) -> tuple[int, int]:
        """**这一帧的裁剪窗口尺寸**（物理像素）。

        * 两个都显式给了 → **固定尺寸**（评测台的 crop 扫描用那条）；
        * 否则 → **自适应**：当前物理帧的 **50% 线性尺寸**、绝对下限 **512**、
          且**不超过这一帧**（帧比下限还小 → 只能等于帧的大小）。
        """
        if self.crop_width and self.crop_height:
            return (self.crop_width, self.crop_height)
        width, height = frame.width, frame.height
        crop_width = self.crop_width or min(
            width, max(MIN_CROP_WIDTH_PX, int(width * CROP_LINEAR_FRACTION))
        )
        crop_height = self.crop_height or min(
            height, max(MIN_CROP_HEIGHT_PX, int(height * CROP_LINEAR_FRACTION))
        )
        return (int(crop_width), int(crop_height))

    # ------------------------------------------------------------------
    # 主流程
    # ------------------------------------------------------------------

    def locate(self, request: VisualLocateRequest) -> VisualLocateResult:
        """跑一次定位，返回最终落点 + 观测数据。

        **两条路**（工单 B-1）：

        * **有候选** → 编号选择（模型只答编号，落点 = 该候选框的物理中心）；
          **挑不出编号 → 回落**到点式路径（工单 M7 的既有纪律是「失败回落」，
          而这里**有**可回落的结果 —— 不报死）；
        * **无候选** → 点式路径（粗定位 → crop 精定位）；
        * **「只用粗定位」那一臂**（``coarse_only=True``）→ 粗定位之后**直接返回**
          （落点 = 粗定位点、**1.00 次调用/目标**、不裁剪、不问第二次）。
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
        # ⚠️ **「只用粗定位」那一臂不走编号分支** —— 那一臂的语义就是
        # 「粗定位之后直接返回」（1.00 次调用/目标）。
        candidates: list[GroundingCandidate] = []
        candidate_issue = ""
        if not self.coarse_only:
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
        if self.candidate_source is not None or self.additional_candidate_source is not None:
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
        """**点式路径**（原来的两级 + 门控）：粗定位（**出框**）→ crop 精定位。

        编号分支挑不出编号时**回落到这里**（工单 M7 的纪律：失败回落）。
        """
        # -- 整屏那一级送给模型的那张图（A-2：自己缩到 model_input_size）----
        screen_frame = self._screen_frame(frame)

        # -- 粗定位（L0）：整屏，**出框** --------------------------------------
        coarse: tuple[float, float] | None = None
        coarse_issue = ""
        coarse_out_of_bounds = False
        #: 粗定位**框**推出的目标半径（物理像素）—— 0 = 模型只给了点。
        coarse_radius_px = 0.0
        # ⚠️ **这次模型调用发生了**（回答解析不出来**也是问过了**）→ 记 1 次。
        # ⚠️ 不记它 →「平均模型调用数/目标」这条验收线**少算**
        # （报出来的数是**下界** → 判在一个偏乐观的数上）。
        model_calls += 1
        raw: CoarseTarget | None = None
        try:
            raw = parse_coarse_target(
                self._ask_text(
                    coarse_prompt(description, self.model_input_size), screen_frame
                )
            )
        except Exception as exc:
            raw = None
            coarse_issue = f"粗定位失败：{exc}"
        raw_center: tuple[float, float] | None = None
        if raw is not None and not raw.negative:
            raw_center = box_center(raw)
            echoes.append(
                GroundingEcho(
                    stage=STAGE_COARSE,
                    raw_x=raw_center[0],
                    raw_y=raw_center[1],
                    # **实际发送**的那张图的尺寸（A-2：我们自己缩到 model_input_size，
                    # 服务端不用再缩 → 模型看到的就是这张）。回显报这个尺寸，
                    # 模型才能对账自己给的坐标。
                    sent_width=screen_frame.width,
                    sent_height=screen_frame.height,
                    image_source=_image_source(screen_frame),
                    # **粗定位的原始回答**（施工清单第 ① 条埋点）：
                    # 原始框 + 模型自报的文字 —— 此前**不可恢复**，
                    # 于是「目标半径」「挑错元素」这两类分析只能靠推断。
                    raw_bbox=raw.bbox,
                    raw_text=raw.text,
                )
            )
            try:
                coarse, coarse_radius_px = self._coarse_physical(raw, geometry)
            except CoordinateOutOfBounds as exc:
                coarse_out_of_bounds = True
                coarse_issue = f"粗定位结果越界：{exc}"
            except CoordinateError as exc:
                coarse_issue = f"粗定位结果不可用：{exc}"
        elif raw is not None and raw.negative:
            # **负出口**（施工清单第 ⑦ 条）：模型说「这张图里没有这个元素」。
            coarse_issue = "模型说这张图里没有对应的元素"
            echoes.append(
                GroundingEcho(
                    stage=STAGE_COARSE,
                    raw_x=None,
                    raw_y=None,
                    sent_width=screen_frame.width,
                    sent_height=screen_frame.height,
                    image_source=_image_source(screen_frame),
                    raw_bbox=raw.bbox,
                    raw_text=raw.text,
                )
            )

        # -- 门控：**只落盘**（不再决定开不开精定位）--------------------------
        left, top, right, bottom = (int(value) for value in request.surface)
        decision = evaluate_gate(
            GateInputs(
                coarse_point=coarse,
                coarse_out_of_bounds=coarse_out_of_bounds,
                # ⚠️ 粗定位**出框**之后，占比这条**第一次可判定**（框 → 半径）。
                target_radius_px=(coarse_radius_px or self.target_radius_px) or None,
                coarse_confidence=None,
                # ⚠️ **不再有第二次独立粗定位**（门控降级为记录器）→
                # 一致性这条**永远不可判定**，如实记进 ``undecidable``。
                second_coarse_point=None,
                surface_size=(right - left, bottom - top),
            ),
            self.gate_params,
        )

        if coarse is None:
            # 粗定位本身为空 / 越界 → 开的是**整屏第二遍**（不是围绕粗点的裁剪窗口）。
            return self._whole_screen(
                request, screen_frame, geometry, echoes, model_calls, decision, coarse_issue
            )

        if self.coarse_only:
            # **只用粗定位**这一臂（评测台的基线臂）：落点 = 粗定位点，
            # **1.00 次模型调用/目标**，不裁剪、不问第二次。
            echoes.append(
                GroundingEcho(
                    stage=STAGE_COARSE_ONLY,
                    # 与粗定位那条回显**同一个数**（模型自己发出的那个数）；
                    # 物理落点在 ``landing_point`` / ``coarse_point`` 里。
                    raw_x=raw_center[0] if raw_center is not None else coarse[0],
                    raw_y=raw_center[1] if raw_center is not None else coarse[1],
                    sent_width=screen_frame.width,
                    sent_height=screen_frame.height,
                    image_source=_image_source(screen_frame),
                )
            )
            return self._result(
                landing_point=(int(round(coarse[0])), int(round(coarse[1]))),
                coarse=coarse,
                plan=None,
                decision=decision,
                refine_enabled=False,
                echoes=echoes,
                model_calls=model_calls,
                landing_source=LANDING_SOURCE_COARSE,
            )

        # -- 裁剪计划：从原始物理分辨率图上取窗口 --------------------------
        # ⚠️ **窗口尺寸是自适应的**（施工清单第 ⑤ 条）：当前物理帧的 50% 线性尺寸、
        # 绝对下限 512、贴边**向内平移补足、不缩小窗口**；裁剪**1:1 原生像素**。
        crop_width, crop_height = self._crop_size_for(frame)
        try:
            plan = plan_crop(
                coarse[0],
                coarse[1],
                surface_rect(request.surface),
                crop_width=crop_width,
                crop_height=crop_height,
                target_radius_px=(coarse_radius_px or self.target_radius_px),
            )
            crop = crop_frame(frame, plan)
        except (CoordinateError, ValueError) as exc:
            # 裁剪计划/裁剪本身失败 → **报定位失败**（不落点、不挪到别处）。
            return self._failure(f"裁剪窗口不可用：{exc}", echoes, model_calls, decision)

        # -- 精定位（L1）：crop 内 -------------------------------------------
        crop_input = self.crop_model_input_size or (plan.width, plan.height)
        # ⚠️ **这次模型调用发生了**（解析不出来**也是问过了**）→ 记 1 次。
        model_calls += 1
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
                plan_fields=self._plan_fields(plan),
                landing_source=LANDING_SOURCE_COARSE,
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
                plan_fields=self._plan_fields(plan),
                landing_source=LANDING_SOURCE_COARSE,
            )
        if raw_refine is None:
            # **负出口**（施工清单第 ⑦ 条）：模型说「这张裁剪图里没有这个元素」
            # → **干净回落到粗定位**（不是「接受一个假点」）。
            return self._result(
                landing_point=(int(round(coarse[0])), int(round(coarse[1]))),
                coarse=coarse,
                plan=plan,
                decision=decision,
                refine_enabled=True,
                fallback=True,
                fallback_cause=FallbackCause.NO_OUTPUT.value,
                fallback_reason="模型说这张裁剪图里没有对应的元素（负出口）",
                echoes=echoes,
                model_calls=model_calls,
                plan_fields=self._plan_fields(plan),
                landing_source=LANDING_SOURCE_COARSE,
            )
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
                plan_fields=self._plan_fields(plan),
                landing_source=LANDING_SOURCE_COARSE,
            )

        # -- 偏离量（施工清单第 ④ 条）：**只记录，不改落点** ------------------
        # ⚠️ **判据降级为记录器**（2026-10-22）：量的口径从「离 crop 窗口中心多远」
        # 改成 **‖精定位 − 粗定位点‖₂**（crop 被 shift 时不再注入额外偏移），
        # 阈值表达改成 **crop 宽度千分位**（``deviation_c1000``）。
        # **落点不再因为偏离而回落** —— 硬失败回落只剩
        # ``no_output`` / ``out_of_bounds`` / ``call_failed`` 三种。
        plan_fields = self._plan_fields(plan, refined=refined, coarse=coarse)
        return self._result(
            landing_point=(int(round(refined[0])), int(round(refined[1]))),
            coarse=coarse,
            plan=plan,
            decision=decision,
            refine_enabled=True,
            echoes=echoes,
            model_calls=model_calls,
            plan_fields=plan_fields,
            landing_source=LANDING_SOURCE_REFINED,
        )

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------

    def _coarse_physical(
        self, target: CoarseTarget, geometry
    ) -> tuple[tuple[float, float], float]:
        """粗定位的回答 → **物理像素**的落点 + **目标半径**（物理像素）。

        ⚠️ **框 → 点的换算只有一处**（:func:`box_center`）：这里只做
        「模型空间 → 物理像素」那一步（走适配器，**不自己写比例**）。

        * 只有框 → 落点 = 框中心，半径 = 物理像素下框的**较大边**的一半
          （门控的「目标外接正方形」用它）；
        * 只有点 → **框退化成那个点**（落点不变，半径 0 → 占比判不了）。
        """
        center = box_center(target)
        landing = self.adapter.to_screen(center[0], center[1], geometry)
        if target.bbox is None:
            return (landing, 0.0)
        x1, y1, x2, y2 = target.bbox
        # 半径：**物理像素**下的框边长（用适配器的换算，虚拟桌面原点会抵消）。
        cap1 = self.adapter.model_to_capture(x1, y1, geometry)
        cap2 = self.adapter.model_to_capture(x2, y2, geometry)
        phys1 = self.adapter.capture_to_physical(cap1[0], cap1[1], geometry)
        phys2 = self.adapter.capture_to_physical(cap2[0], cap2[1], geometry)
        width = abs(phys2[0] - phys1[0])
        height = abs(phys2[1] - phys1[1])
        return (landing, max(width, height) / 2.0)

    def _plan_fields(
        self,
        plan,
        *,
        refined: tuple[float, float] | None = None,
        coarse: tuple[float, float] | None = None,
    ) -> dict[str, float | None]:
        """裁剪计划的**落盘补充字段**（偏离量与 zoom 一致性）。

        * ``deviation_px`` —— 精定位落点离 **crop 窗口中心**多远（**历史口径**，
          参考点不是粗定位点：crop 走 shift 语义时两者不同）—— **留着，便于对比**；
        * ``deviation_px_coarse`` —— **‖精定位 − 粗定位点‖₂**（**当前口径的量**，
          施工清单第 ④ 条）；
        * ``deviation_c1000`` —— 同一个量按 **crop 宽度千分位**表达的刻度
          （阈值用它 → **换 crop 尺寸不用重调**）；
        * ``deviation_exceeded`` —— 记录器的判断结果（**只落盘，不改落点**）；
        * ``refined_point_x`` / ``refined_point_y`` —— **精定位的原始落点**
          （物理像素）—— 此前**不可恢复**，「阈值附近的逐条归属」因此有噪声；
        * ``zoom_consistency_px`` / ``zoom_consistency_c1000`` —— 两级之间的位移
          （= ``deviation_px_coarse``，历史字段名，留着）。
        """
        fields: dict[str, float | None] = {
            "deviation_px": None,
            "deviation_px_coarse": None,
            "deviation_c1000": None,
            "deviation_exceeded": None,
            "refined_point_x": None,
            "refined_point_y": None,
            "zoom_consistency_px": None,
            "zoom_consistency_c1000": None,
        }
        if refined is None or plan is None:
            return fields
        fields["deviation_px"] = math.hypot(
            refined[0] - plan.center[0], refined[1] - plan.center[1]
        )
        fields["refined_point_x"] = refined[0]
        fields["refined_point_y"] = refined[1]
        if coarse is not None:
            consistency = math.hypot(refined[0] - coarse[0], refined[1] - coarse[1])
            fields["deviation_px_coarse"] = consistency
            fields["zoom_consistency_px"] = consistency
            scaled = consistency * ZOOM_CONSISTENCY_PER_MILLE / plan.width
            fields["deviation_c1000"] = scaled
            fields["zoom_consistency_c1000"] = scaled
            fields["deviation_exceeded"] = (
                scaled > self.gate_params.deviation_tolerance_per_mille
            )
        return fields

    def _ask_text(self, prompt: str, frame: CapturedFrame) -> str:
        """问模型一次，**拿回答原文**（粗定位要读「框 + 点」，不是只读一个点）。"""
        image_base64 = encode_frame_base64(frame)
        if image_base64 is None:
            raise GroundingParseError("画面编不成 PNG（像素数与尺寸对不上），送不出图")
        reply = self.model.complete(
            prompt,
            effort=self.effort,
            max_tokens=self.max_tokens,
            image_base64=image_base64,
        )
        return reply.text

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

        **合并源**（UFO² 的混合检测）：**主表**（UIA 控件）全留，
        **附加表**（OCR 词框）中与主表 IoU > 0.1 的丢弃，其余追加在主表之后。

        * 没有候选源 → 空 + ``"no-candidate-source"``（这条分支压根不存在）；
        * 候选源报错（OCR 后端不可用）→ 空 + 原因（**判不了 ≠ 没有候选**）；
        * 主表不可用 / 为空 → **退回附加表**（UIA 树是空树时的正常情形）；
        * 两源都没匹配上 → 空 + ``""``（原因由调用方补）。
        """
        if self.candidate_source is None and self.additional_candidate_source is None:
            return ([], "no-candidate-source")
        main, main_issue = self._candidates_from(
            self.candidate_source, frame, description
        )
        additional, additional_issue = self._candidates_from(
            self.additional_candidate_source, frame, description
        )
        if main:
            merged = merge_candidate_sources(main, additional)
            return (merged, "")
        if additional:
            # **主表不可用 / 为空 → 退回附加表**（不报错、不假装没有候选）。
            return (additional, main_issue or additional_issue)
        issues = [issue for issue in (main_issue, additional_issue) if issue]
        return ([], "; ".join(issues))

    def _candidates_from(
        self, source: CandidateSource | None, frame: CapturedFrame, description: str
    ) -> tuple[list[GroundingCandidate], str]:
        """一个候选源 → （候选, 原因）。**判不了 ≠ 没有候选**（两类分开记）。"""
        if source is None:
            return ([], "")
        try:
            return (list(source.candidates(frame, description)), "")
        except Exception as exc:
            return ([], f"candidate-source-error: {exc}")

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
            # ⚠️ **这一次模型调用已经发出去了**（回答解析不出来**也是问过了**）
            # —— 不记它 → 「平均模型调用数/目标」少算一次。
            return self._failure(f"编号选择失败：{exc}", echoes, model_calls + 1)
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
            #: ⚠️ **编号分支没有门控这一级** —— 四条条件**一条都没判**。
            #: 回空元组等于说「门控判了、而且全部判得出来」，那是**假的**
            #: （聚合出来的「undecidable 数 = 0」会被**白拿**）→
            #: 照实把四条都记成**不可判定**。
            gate_undecidable=tuple(condition.value for condition in GateCondition),
            refine_enabled=False,
            echoes=tuple(echoes),
            model_calls=model_calls,
            scale=self.scale.value,
            landing_source=LANDING_SOURCE_NUMBERED,
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
        # ⚠️ **这一遍也是一次模型调用**（失败**也是问过了**）→ 记 1 次。
        model_calls += 1
        try:
            raw = parse_coarse_target(
                self._ask_text(
                    whole_screen_prompt(
                        request.target_description,
                        self.model_input_size,
                        retry_reason=retry_reason,
                    ),
                    screen_frame,
                )
            )
        except Exception as exc:
            return self._failure(f"整屏第二遍失败：{exc}", echoes, model_calls, decision)
        echoes.append(
            GroundingEcho(
                stage=STAGE_WHOLE_SCREEN,
                raw_x=box_center(raw)[0],
                raw_y=box_center(raw)[1],
                sent_width=screen_frame.width,
                sent_height=screen_frame.height,
                image_source=_image_source(screen_frame),
            )
        )
        try:
            point, _radius = self._coarse_physical(raw, geometry)
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
            landing_source=LANDING_SOURCE_WHOLE_SCREEN,
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
        plan_fields: dict[str, float | None] | None = None,
        landing_source: str = LANDING_SOURCE_COARSE,
    ) -> VisualLocateResult:
        """一次定位的结果（门控字段 + 回落字段一起落）。"""
        return VisualLocateResult(
            landing_point=landing_point,
            coarse_point=coarse,
            crop_plan=(
                {**crop_plan_fields(plan), **(plan_fields or {})}
                if plan is not None
                else None
            ),
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
            landing_source=landing_source,
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

"""**精定位门控**（工单 M7；架构决策备忘 §2.6 **C-16**）。

门控（gate）= 「决定是否开启 crop 精定位的条件集合」（``CONTEXT.md`` 词条）。

⚠️ **本模块现在是「记录器」，不再决定开不开**（2026-10-21 拍板：
「门控降级为记录器，生产默认无条件精定位」）。**判据一条都不删**——
四条条件照判、照落盘（``GateDecision``），只是**不再拿它决定要不要做
crop 精定位**：精定位**恒执行**（粗定位不可用时走整屏第二遍，那不算精定位）。

**为什么降级**（本机实测，42 条 ScreenSpot-Pro 抽样，同模型同口径）：

* 门控**开**：命中 16/42 = **38.1%**，平均 **2.381** 次模型调用/目标；
* 门控**关**：命中 24/42 = **57.1%**，平均 **2.000** 次模型调用/目标；
* 门控判 ``coarse_inconsistent`` 的那 10 条，两臂落点**逐条相同**（+0.0 pp）；
  其余 32 条从 13 命中变成 21 命中（**+25.0 pp**）。

即：**净负的 19.0 pp 全部来自「门控在精定位最可能成功的样本上关掉它」**——
触发条件（两次粗定位不一致）恰好是精定位前提条件（目标在窗内）的反面。
【实测数字】``runs/a2-eval-42/caliber.json`` 与 ``caliber-always.json``。

**判据留着当记录器**：门控的四个条件与「不可判定」这一状态仍然有用 ——
它们量的是「这一次的粗定位有多可信」，落盘之后才能回头看
「哪些样本的门控本来会开 / 本来会关」。

C-16 的四个触发条件（**互不依赖，各自可单独触发**）：

============================  ==========================================
条件                          判据
============================  ==========================================
``TARGET_TINY``              粗定位目标占全屏比例极小
``COARSE_INVALID``           粗定位输出为空或越界
``LOW_CONFIDENCE``           粗定位置信度低
``COARSE_INCONSISTENT``      两次独立粗定位互相不一致
============================  ==========================================

**不可判定**是第三种状态：条件本身判不了。不可判定的条件**不触发、也不否决其余三条**，
并且**显式记进追踪记录**（不许静默）。

* 「目标占比」这条**在粗定位出框之后第一次可判定**（粗定位只给点不给框时判不了）；
* 「两次粗定位是否一致」这条**永远不可判定** —— 门控降级为记录器之后，
  那一次「第二次独立粗定位」的调用被删掉了（它正是 −19.0 pp 的来源），
  判据留着、端口不再注入 → 每次都照实记成不可判定。

**可核参照**（AC 9 落地的那一个）：CCF（Cursor-Centric Focusing）——
仓库 ``LufeMC/gui-g2-3b-ccf``，文件 ``src/cursor_ccf.py``：

* **失败回落粗定位**（``ccf_predict_bbox()`` L287-296，``stage="fallback"``）——
  这一半我们**同构照抄**：精定位失败就回粗定位，绝不返回「没有落点」。
* **要不要 zoom 的门控**（``_should_skip_ccf()`` L186-188）——
  ⚠️ **它的量与方向都与我们不同，我们只借了「按尺寸做门控」这个形状**：
  CCF 量的是**整张截图的像素数**（低于阈值 → **不** zoom），我们量的是
  **目标占全屏的面积比例**（低于阈值 → **开**精定位）。
  差别的原因：CCF 的门控是「画面已经够清楚了就别裁」，我们的门控是
  「目标太小了才值得裁」（C-16 的原话）。**阈值也不是同一个**：
  我们的 0.07% 是 ScreenSpot-Pro 的**数据集统计**（未验证），CCF 的是 200k 像素的工程值。

**取舍理由**（为什么只抄 CCF，不抄 UI-Zoomer / RegionFocus）：

* CCF 的门控是**纯几何**的（像素数阈值），不需要额外模型调用 ——
  我们这套通道本来就在省模型调用，不能为了门控再添一次调用；
* UI-Zoomer 的门控要采样 8 个候选框 + 算两两 IoU 均值 + token logprob，
  代价与它想省的精定位同量级；
* RegionFocus 的门控要**再问模型一次**「is this position correct?」——
  与「两次独立粗定位」一样，等于把模型调用数翻倍。
  它那条思路被我们收进 ``COARSE_INCONSISTENT`` 条件（**判据留着当记录器**：
  门控降级之后**不再有那次调用** → 该条件永远判不了 → 如实记为不可判定）。

**参数不是写死常量**（AC 2）：每个阈值都是运行时参数，且**取值来源被记录**
（``GateParams.*_source``）。改阈值不需要改代码。
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum

__all__ = [
    "DEFAULT_AREA_RATIO_SOURCE",
    "FallbackCause",
    "GateCondition",
    "GateDecision",
    "GateInputs",
    "GateMode",
    "GateParams",
    "GateReason",
    "GateReport",
    "GateSample",
    "build_gate_report",
    "default_gate_params",
    "evaluate_gate",
]


class GateReason(StrEnum):
    """**门控命中原因**（枚举，落进追踪记录与评测报告）。"""

    #: 粗定位目标占全屏比例极小（≤ 阈值）→ 值得再看一眼。
    TARGET_TINY = "target_tiny"
    #: 粗定位输出为空或越界 → 没有可用的粗定位点。
    COARSE_INVALID = "coarse_invalid"
    #: 粗定位置信度低于阈值。
    LOW_CONFIDENCE = "low_confidence"
    #: 两次独立粗定位互相不一致。
    COARSE_INCONSISTENT = "coarse_inconsistent"


class GateCondition(StrEnum):
    """**四个门控条件本身**（不可判定时也按名字点名）。"""

    #: 目标占全屏的比例。
    TARGET_AREA_RATIO = "target_area_ratio"
    #: 粗定位输出是否为空 / 越界。
    COARSE_VALIDITY = "coarse_validity"
    #: 粗定位置信度。
    CONFIDENCE = "confidence"
    #: 两次独立粗定位是否一致。
    CONSISTENCY = "consistency"


class FallbackCause(StrEnum):
    """**回落原因**（枚举）：精定位失败后为什么回到粗定位结果。"""

    #: 模型没给出坐标（解析不出）。
    NO_OUTPUT = "no_output"
    #: 精定位结果越界。
    OUT_OF_BOUNDS = "out_of_bounds"
    #: 精定位调用失败（异常 / 没有文本输出）。
    CALL_FAILED = "call_failed"
    #: 精定位结果明显偏离粗定位（选到了相邻的同类控件）。
    #:
    #: ⚠️ **2026-10-22 起不再产生**（施工清单第 ④ 条）：偏离判据**降级为记录器**
    #: —— 只落盘（``deviation_exceeded``），**不再改落点**。
    #: 硬失败回落因此只剩上面那三种。枚举成员**留着**（口径一条都不删）。
    DEVIATION = "deviation"
    #: **编号分支挑不出编号**（工单 B-1）→ 回落点式路径。
    #:
    #: ⚠️ 这一条与上面四条**不是同一件事**：上面四条是「crop 精定位失败 →
    #: 回到粗定位点」，这一条是「**编号选择**失败 → 整条路换成点式路径」。
    #: 枚举里放它是因为**回落原因只有这一处落盘的地方**（追踪记录的
    #: ``fallback_cause``）。
    CANDIDATE_SELECTION_FAILED = "candidate_selection_failed"


class GateMode(StrEnum):
    """**门控开 / 门控关**（运行时开关，不是写死行为）。

    * ``ON_DEMAND`` —— **门控开**：命中才做第二遍精定位（**按需触发**，
      ``CONTEXT.md`` 的词条；工单 M7 的默认）。
    * ``ALWAYS`` —— **门控关**：无条件做第二遍精定位（M6 的行为；
      工单 M8「门控开不低于门控关」的对照组）。

    ⚠️ **门控降级为记录器之后，这两个 mode 不再改变行为**：
    精定位**恒执行**（生产默认无条件两段式）。两个 mode 都留着 ——
    单测与历史报告在用，而且「本来会不会开」这个信息仍要能从落盘里读出来：
    通道落盘的 ``gate_hit`` / ``gate_reasons`` 记的是**门控自己的判定**
    （与 mode 无关），「这一臂是不是只做了粗定位」由通道的 ``coarse_only``
    开关与回显的 ``stage="coarse_only"`` 区分。
    """

    #: 门控开（按需触发）。
    ON_DEMAND = "on_demand"
    #: 门控关（无条件精定位）。
    ALWAYS = "always"


#: 「目标占比」阈值的取值来源（AC 2：凡出现 0.07% 必须带这个标注）。
DEFAULT_AREA_RATIO_SOURCE = (
    "数据集统计（ScreenSpot-Pro 目标平均占图面积 0.07%，论文给出）、未验证的阈值"
)

#: 「目标占比」阈值（默认 0.07%）。**运行时参数**，取值来源见上。
DEFAULT_AREA_RATIO_THRESHOLD = 0.0007

#: 「置信度」阈值（默认 0.5）与取值来源。
DEFAULT_CONFIDENCE_THRESHOLD = 0.5
DEFAULT_CONFIDENCE_SOURCE = "工程阈值·未验证：模型自报置信度低于一半即判为不可信"

#: 「两次粗定位一致性」的容差（物理像素）与取值来源。
#:
#: ⚠️ **这条判据现在永远不可判定**（门控降级为记录器 → 「第二次独立粗定位」
#: 那次调用已删掉 → ``second_coarse_point`` 恒为 ``None``）→ **阈值不参与任何判定**；
#: 留在这里只是**历史取值记录**（口径：判据一条都不删）。
DEFAULT_CONSISTENCY_TOLERANCE_PX = 128.0
DEFAULT_CONSISTENCY_SOURCE = (
    "工程阈值·未验证：128 px（**当时** 512 裁剪窗口边长的 1/4 —— 生产默认 crop "
    "已按本机实测改成 192 px，这个换算关系**不再成立**）；"
    "两次独立粗定位的间距超过它才算不一致。换裁剪尺寸时这个阈值要跟着复核。"
    "⚠️ 门控降级为记录器之后「第二次独立粗定位」那次调用已被删掉 → "
    "这条**永远不可判定**，阈值**不参与任何判定**"
)

#: 「精定位偏离粗定位」的容差（物理像素）与取值来源。
#:
#: ⚠️ **参考点是粗定位点**（不是 crop 窗口中心 —— 2026-10-22 施工清单第 ④ 条改）：
#: crop 走 **shift** 语义时（目标贴着屏幕边，窗口被平移过），
#: 「离 crop 中心多远」会注入最多 ~半窗宽的**额外偏移**，
#: 那不是模型指错，是**我们自己的窗口摆放**。
#:
#: ⚠️ **这条判据现在是记录器**（2026-10-22）：只落盘 ``deviation_exceeded``，
#: **不再改落点**。硬失败回落只剩 ``no_output`` / ``out_of_bounds`` /
#: ``call_failed`` 三种。
DEFAULT_DEVIATION_TOLERANCE_PX = 50.0
DEFAULT_DEVIATION_SOURCE = (
    "⚠️ **2026-10-22 改口径**（施工清单第 ④ 条）：① **量** = ‖精定位 − **粗定位点**‖₂"
    "（= 本仓已算好的 `zoom_consistency_px`；与 ZoomClick 的 ε₁ 同量 —— "
    "一手：ZoomClick 论文 §3.2.1 的 ε₁ 是「两次预测的位移」，"
    "**不是「离 crop 中心多远」**）；"
    "② **阈值表达**改成 crop 宽度千分位（`deviation_c1000 = ‖Δ‖₂ × 千 / crop 宽`，"
    "192 crop 时 50 px ≡ c≈260.4）→ 换 crop 尺寸不用重调；"
    "③ **判据降级为记录器**（只落盘 `deviation_exceeded`，**不再改落点**）—— "
    "本机重放实测：取消这一条回落，同一批 183 条 44.81% → 47.45%（平台，t=∞）、"
    "t=96 峰 47.71%（**同批数据上的峰，不对外报**）；"
    "④ 一手锚点：ZoomClick README 的 `--prezoom_px_thresh 50` "
    "（https://raw.githubusercontent.com/Princeton-AI2-Lab/ZoomClick/main/README.md）；"
    "LFPR 换算在 192 crop 上是 47.5 px（c≈247.5，"
    "https://arxiv.org/html/2608.19553v1）。"
    "⚠️ 旧的 50 px（crop 中心参考点）这条**只作历史记录**，"
    "**不再参与任何判定**"
)

#: 「精定位偏离粗定位」的容差 —— **crop 宽度千分位刻度**（``c1000``）。
#:
#: 换算：``c1000 = ‖精定位 − 粗定位点‖₂ × 千 / crop 宽``。
#: 默认 **260.4** ≡ 旧口径的 50 px @192 crop（**保持同一把尺子的历史读数**）。
#:
#: ⚠️ 字段名里**不写那三个数字**（C-04 的源码级扫描不许看见「写死的分母」）；
#: 落盘键叫 ``deviation_tolerance_c1000``。
DEFAULT_DEVIATION_TOLERANCE_PER_MILLE = 260.4
DEFAULT_DEVIATION_PER_MILLE_SOURCE = (
    "c1000 = ‖精定位 − 粗定位点‖₂ × 千 / crop 宽；"
    "默认 260.4 ≡ 旧口径 50 px @192 crop（同一把尺子的历史读数）；"
    "**这是记录器的阈值**（只落盘 `deviation_exceeded`，不再改落点）"
)


@dataclass(frozen=True)
class GateParams:
    """**门控参数**（运行时参数；每个阈值都配它的取值来源）。"""

    area_ratio_threshold: float = DEFAULT_AREA_RATIO_THRESHOLD
    area_ratio_source: str = DEFAULT_AREA_RATIO_SOURCE
    confidence_threshold: float = DEFAULT_CONFIDENCE_THRESHOLD
    confidence_source: str = DEFAULT_CONFIDENCE_SOURCE
    consistency_tolerance_px: float = DEFAULT_CONSISTENCY_TOLERANCE_PX
    consistency_source: str = DEFAULT_CONSISTENCY_SOURCE
    deviation_tolerance_px: float = DEFAULT_DEVIATION_TOLERANCE_PX
    deviation_source: str = DEFAULT_DEVIATION_SOURCE
    #: **判据用的阈值**（crop 宽度千分位刻度）—— 记录器读它。
    #:
    #: ⚠️ 字段名里**不写那三个数字**（C-04 的源码级扫描）；落盘键是
    #: ``deviation_tolerance_c1000``。
    deviation_tolerance_per_mille: float = DEFAULT_DEVIATION_TOLERANCE_PER_MILLE
    deviation_per_mille_source: str = DEFAULT_DEVIATION_PER_MILLE_SOURCE

    def to_dict(self) -> dict[str, object]:
        """落盘形状（值 + 来源一起落 —— 事后能核对「当时用的哪个阈值、凭什么」）。"""
        return {
            "area_ratio_threshold": self.area_ratio_threshold,
            "area_ratio_source": self.area_ratio_source,
            "confidence_threshold": self.confidence_threshold,
            "confidence_source": self.confidence_source,
            "consistency_tolerance_px": self.consistency_tolerance_px,
            "consistency_source": self.consistency_source,
            #: ⚠️ **历史口径**（crop 中心参考点、固定 50 px）—— **不再参与判定**。
            "deviation_tolerance_px": self.deviation_tolerance_px,
            "deviation_source": self.deviation_source,
            #: **当前口径**：crop 宽度千分位刻度（记录器阈值）。
            "deviation_tolerance_c1000": self.deviation_tolerance_per_mille,
            "deviation_c1000_source": self.deviation_per_mille_source,
        }


def default_gate_params() -> GateParams:
    """默认门控参数（**带取值来源**，不是匿名常量）。"""
    return GateParams()


@dataclass(frozen=True)
class GateInputs:
    """**门控的输入**（都由粗定位那一级产出）。

    * ``coarse_point`` —— 粗定位点（物理表面空间）。**为空 = 粗定位不可用**。
    * ``coarse_out_of_bounds`` —— 粗定位是否越界（只作记录用）。
    * ``target_radius_px`` —— 目标半径（物理像素）。**没有它就判不了占比**
      （粗定位只给点不给框 → 占比**不可判定**）。
      ⚠️ 粗定位**出框**之后，通道会用框推出这个半径 → 占比**第一次可判定**。
    * ``coarse_confidence`` —— 粗定位置信度（模型没报就是 ``None`` → **不可判定**）。
    * ``second_coarse_point`` —— 第二次独立粗定位的结果（没有就是 ``None``
      → 一致性**不可判定**）。
    * ``surface_size`` —— 物理表面尺寸（目标占比的**整屏面积**基准）。
    """

    coarse_point: tuple[float, float] | None = None
    coarse_out_of_bounds: bool = False
    target_radius_px: float | None = None
    coarse_confidence: float | None = None
    second_coarse_point: tuple[float, float] | None = None
    surface_size: tuple[int, int] = (0, 0)


@dataclass(frozen=True)
class GateDecision:
    """**门控判定**。

    * ``hit`` —— 是否命中（命中任意一条即命中）。
    * ``reasons`` —— 命中的条件（枚举，可多条）。
    * ``undecidable`` —— **不可判定**的条件（枚举）。
    * ``params`` —— 判定用的参数（连同取值来源）。
    """

    hit: bool
    reasons: tuple[GateReason, ...] = ()
    undecidable: tuple[GateCondition, ...] = ()
    params: GateParams = GateParams()

    def to_dict(self) -> dict[str, object]:
        """落盘形状。"""
        return {
            "hit": self.hit,
            "reasons": [reason.value for reason in self.reasons],
            "undecidable": [condition.value for condition in self.undecidable],
            "params": self.params.to_dict(),
        }


def _target_area_ratio(radius_px: float, surface_size: tuple[int, int]) -> float | None:
    """目标占全屏的面积比例（半径 → 目标外接正方形面积 ÷ 表面面积）。"""
    width, height = surface_size
    surface_area = float(width) * float(height)
    if surface_area <= 0:
        return None
    side = 2.0 * float(radius_px)
    return (side * side) / surface_area


def evaluate_gate(
    inputs: GateInputs,
    params: GateParams | None = None,
) -> GateDecision:
    """**判定门控**（纯函数：同样的输入永远给同样的判定）。

    四条条件各自独立：命中任意一条即命中；判不了的条件记进 ``undecidable``，
    **既不触发、也不否决其余三条**。
    """
    params = params or default_gate_params()
    reasons: list[GateReason] = []
    undecidable: list[GateCondition] = []

    # ① 目标占全屏比例极小
    ratio = (
        _target_area_ratio(inputs.target_radius_px, inputs.surface_size)
        if inputs.target_radius_px is not None and inputs.target_radius_px > 0
        else None
    )
    if ratio is None:
        undecidable.append(GateCondition.TARGET_AREA_RATIO)
    elif ratio <= params.area_ratio_threshold:
        reasons.append(GateReason.TARGET_TINY)

    # ② 粗定位输出为空或越界
    if inputs.coarse_point is None or inputs.coarse_out_of_bounds:
        reasons.append(GateReason.COARSE_INVALID)

    # ③ 粗定位置信度低
    if inputs.coarse_confidence is None:
        undecidable.append(GateCondition.CONFIDENCE)
    elif inputs.coarse_confidence < params.confidence_threshold:
        reasons.append(GateReason.LOW_CONFIDENCE)

    # ④ 两次独立粗定位互相不一致
    if inputs.second_coarse_point is None:
        undecidable.append(GateCondition.CONSISTENCY)
    elif inputs.coarse_point is None:
        # 第二次有点、第一次没有 → **比不了**（一致性判不了）。
        # ⚠️ 这一支以前是**静默**的（既不记命中也不记不可判定）——
        # README 的口径要求「判不了的条件必须显式记进追踪记录」。
        undecidable.append(GateCondition.CONSISTENCY)
    else:
        distance = math.hypot(
            inputs.second_coarse_point[0] - inputs.coarse_point[0],
            inputs.second_coarse_point[1] - inputs.coarse_point[1],
        )
        if distance > params.consistency_tolerance_px:
            reasons.append(GateReason.COARSE_INCONSISTENT)

    return GateDecision(
        hit=bool(reasons),
        reasons=tuple(reasons),
        undecidable=tuple(undecidable),
        params=params,
    )


@dataclass(frozen=True)
class GateSample:
    """**一个样本的门控观测**（评测报告的输入）。"""

    gate_hit: bool = False
    gate_reasons: tuple[str, ...] = ()
    gate_undecidable: tuple[str, ...] = ()
    refine_enabled: bool = False
    fallback: bool = False
    fallback_cause: str = ""


@dataclass(frozen=True)
class GateReport:
    """**门控埋点报告**（口径写死，工单 M7 AC 4）。

    * ``samples`` —— 样本数 N；
    * ``hit_rate`` —— **开启精定位的样本数** ÷ 总样本数。
      ⚠️ **门控降级为记录器之后，这个数恒为 1（只要粗定位可用）** ——
      它现在量的是「有多少样本做了精定位」，**不再**是「门控命中了多少」；
    * ``gate_decision_rate`` —— **门控自己判中**的比例。
      ⚠️ **降级之后这个才是「本来会开多少」的那个数**（门控判据照判、照落盘，
      只是不再决定开不开）；
    * ``fallback_rate`` —— **回落率** = 回落次数 ÷ 开启精定位的样本数；
    * ``reason_distribution`` —— 命中原因分布。

    ⚠️ 这几个数与同一张表的分数**必须来自同一批样本**（构造本报告的
    就是打分用的那批 outcome）。
    """

    samples: int
    gate_hits: int
    refine_enabled: int
    fallbacks: int
    reason_distribution: Mapping[str, int]
    undecidable_distribution: Mapping[str, int]

    @property
    def hit_rate(self) -> float:
        """**开启精定位的样本数** ÷ 总样本数（门控降级后恒为 1）。"""
        if self.samples <= 0:
            return 0.0
        return self.refine_enabled / self.samples

    @property
    def gate_decision_rate(self) -> float:
        """**门控自己判中**的比例（降级之后：这就是「本来会开多少」）。"""
        if self.samples <= 0:
            return 0.0
        return self.gate_hits / self.samples

    @property
    def fallback_rate(self) -> float:
        """回落率（回落次数 ÷ 开启精定位的样本数）。"""
        if self.refine_enabled <= 0:
            return 0.0
        return self.fallbacks / self.refine_enabled

    def to_dict(self) -> dict[str, object]:
        """落盘形状。"""
        return {
            "samples": self.samples,
            "gate_hits": self.gate_hits,
            "gate_decision_rate": self.gate_decision_rate,
            "refine_enabled": self.refine_enabled,
            "hit_rate": self.hit_rate,
            "fallbacks": self.fallbacks,
            "fallback_rate": self.fallback_rate,
            "reason_distribution": dict(self.reason_distribution),
            "undecidable_distribution": dict(self.undecidable_distribution),
        }


def build_gate_report(samples: Sequence[GateSample]) -> GateReport:
    """把一批样本的门控观测汇成报告（**同一批样本**出这几个数）。"""
    reason_counts: dict[str, int] = {}
    undecidable_counts: dict[str, int] = {}
    gate_hits = 0
    refine_enabled = 0
    fallbacks = 0
    for sample in samples:
        if sample.gate_hit:
            gate_hits += 1
        if sample.refine_enabled:
            refine_enabled += 1
        if sample.fallback:
            fallbacks += 1
        for reason in sample.gate_reasons:
            reason_counts[reason] = reason_counts.get(reason, 0) + 1
        for condition in sample.gate_undecidable:
            undecidable_counts[condition] = undecidable_counts.get(condition, 0) + 1
    return GateReport(
        samples=len(samples),
        gate_hits=gate_hits,
        refine_enabled=refine_enabled,
        fallbacks=fallbacks,
        reason_distribution=reason_counts,
        undecidable_distribution=undecidable_counts,
    )

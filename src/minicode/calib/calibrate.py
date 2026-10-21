"""运行时标定探针：判定模型输出的**量纲**（工单 M5 / A-1）。

一次标定的形状：

1. 画一张与物理表面同尺寸的探针图（:mod:`minicode.calib.probe`），
   **送模型前**缩到 ``model_input_size``（A-2：自己缩，声明即服务端看到的大小），
   向模型问四个已知目标的坐标 —— **模型给的数值原样保留**，
   本模块不乘任何分母、不按数值大小猜量纲；
2. **K 轮独立问**（K = :data:`CALIBRATION_ROUNDS`）：每轮提示词不同
   （逐字重发在确定性模型下会拿到同一答案 —— ``ground/channel.py`` 的
   ``second_opinion`` 注释记过这个坑），K 轮的数值一起解释；
3. 用五个候选标度（0~1 / 0~999 / 0~1000 / 截图像素绝对 / 模型输入像素绝对）
   各自去解释这批数值，看哪个**总误差**最小 → 判定标度；
4. 报**三个互不相同的维度**：

   * **标度判定**（唯一命中 / 多解）—— 探针能不能唯一确定量纲。
     判据是**可分辨性**：与判定标度分歧 < 2 px（= 可分辨安全系数 × 标定容差
     1 px）的候选算「分不开」→ multi。**1 px 不许放松**。
   * **落点等价**（等价 / 分歧）—— 换一个标度解释，落点会挪多远。判据 4 px
     （:data:`SCALE_EQUIVALENCE_TOLERANCE_PX`）。
   * **命中率** —— K 轮里有多少个预测落进目标的方块内（半径 48 px）。

   三者**不许混成一个字段**（工单 M5 AC 5）：多解不等于视觉通道不可用；
   ``calibration_ok`` 的语义是「**落点等价族判定成功**」，
   **不再表示「模型误差 ≤ 1 px」**。

⚠️ **为什么 ``calibration_ok`` / ``landing_equivalence`` 在新口径下恒为真**
（A-1 归位后的**结构事实**，不是放松判据）：

    落点等价族 = {判定标度} ∪ {分歧 ≤ 落点等价容差(4 px) 的候选}
                 ↑ 按定义，族内最大分歧 ≤ 4 px

所以「族内最大分歧 ≤ 4 px」**按定义恒成立** → ``calibration_ok`` 恒 True、
``landing_equivalence`` 恒 ``"equivalent"``。**真正起闸门作用的是命中率**
（方案原话：**视觉通道由命中率决定**）——
准入判据见 :mod:`minicode.calib.admission`：
「等价族最大分歧 ≤ 4 px **且** K 轮命中率 ≥ 0.6」。

**越界记为无穷大误差、不中断**（AC 6）：把 0~1000 的数值当 0~1 读时，
模型空间那层先判越界，该候选的最大误差记 ``inf``，标定继续跑完。
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from math import isqrt

from minicode.action.image import encode_frame_base64
from minicode.action.ports import CapturedFrame
from minicode.coord.coordinates import (
    CoordinateAdapter,
    CoordinateError,
    FrameGeometry,
    ModelScale,
)
from minicode.model.effort import Effort
from minicode.model.image import downscale_frame
from minicode.model.ports import ModelCaller
from minicode.model.request_log import IMAGE_PATCH_PX, RequestLog, total_input_tokens

from .probe import ProbeSpec, build_probe_frame, probe_digest, probe_hit_radius
from .prompt import image_prompt, parse_points, text_prompt
from .tolerance import (
    CALIBRATION_ROUNDS,
    CALIBRATION_TOLERANCE_PX,
    DISCRIMINABILITY_SAFETY_FACTOR,
    OUT_OF_BOUNDS_TOLERANCE_PX,
    SCALE_EQUIVALENCE_TOLERANCE_PX,
)

__all__ = [
    "CandidateEvaluation",
    "CalibrationOutcome",
    "CalibrationProbe",
    "SCALE_VERDICT_UNIQUE",
    "SCALE_VERDICT_MULTI",
    "EQUIVALENCE_EQUIVALENT",
    "EQUIVALENCE_DIVERGENT",
    "estimate_model_input_from_tokens",
    "estimate_model_input_from_log",
]

#: 标度判定：唯一命中。
SCALE_VERDICT_UNIQUE = "unique"
#: 标度判定：多解。
SCALE_VERDICT_MULTI = "multi"
#: 落点等价：等价。
EQUIVALENCE_EQUIVALENT = "equivalent"
#: 落点等价：分歧。
EQUIVALENCE_DIVERGENT = "divergent"


@dataclass(frozen=True)
class CandidateEvaluation:
    """某个候选标度在探针上的表现（**跨全部 K 轮汇总**）。"""

    #: 候选标度。
    scale: ModelScale
    #: 该标度下的最大误差（物理像素）。越界 → ``inf``。
    max_error_px: float
    #: 该标度下的平均误差（物理像素）。
    mean_error_px: float
    #: 有没有目标在这个标度下越界（模型空间先判）。任一轮越界即为真。
    out_of_bounds: bool
    #: 该标度下换算出来的**物理落点**（越界那个是 ``nan``）。
    #: **逐轮展开**（轮次优先：第 r 轮的 4 个目标挨着放），
    #: 长度 = 轮数 × 目标数。
    landing_points: tuple[tuple[float, float], ...] = ()
    #: 该标度下的**总误差**（物理像素，跨全部轮次与目标）—— 判定标度用它。
    total_error_px: float = 0.0


@dataclass(frozen=True)
class CalibrationOutcome:
    """一次标定的结论。"""

    #: 判定标度（**总误差**最小的那个候选）。
    detected_scale: ModelScale
    #: **标度判定**：``"unique"``（唯一命中）/ ``"multi"``（多解）。
    #: 判据是**可分辨性**（2 px），与落点等价（4 px）是两回事。
    scale_verdict: str
    #: **落点等价**：``"equivalent"``（等价）/ ``"divergent"``（分歧）。
    landing_equivalence: str
    #: **落点等价族**：探针排除不掉、且落点分歧 ≤ 落点等价容差的那些标度。
    equivalent_scales: tuple[ModelScale, ...] = ()
    #: 五个候选各自的误差明细（跨全部轮次汇总）。
    candidates: tuple[CandidateEvaluation, ...] = ()
    #: 每个候选与判定标度之间的**物理落点分歧**（物理像素，跨 K 轮取最大）。
    divergences_px: Mapping[ModelScale, float] = field(default_factory=dict)
    #: **可分辨容差**（物理像素）。它只管「两个候选标度能不能分开」。
    calibration_tolerance_px: float = CALIBRATION_TOLERANCE_PX
    #: 越界容差（物理像素）。与标定容差**互不共用**。
    out_of_bounds_tolerance_px: float = OUT_OF_BOUNDS_TOLERANCE_PX
    #: **落点等价容差**（物理像素）。与标定容差**互不共用**。
    equivalence_tolerance_px: float = SCALE_EQUIVALENCE_TOLERANCE_PX
    #: 标定成功吗？= **落点等价族判定成功**（等价族分歧 ≤ 落点等价容差）。
    #:
    #: ⚠️ 它**不再表示「模型误差 ≤ 1 px」** —— 模型落点误差由
    #: :attr:`hit_rate` 判（准入闸门看的是这两个）。
    #:
    #: ⚠️⚠️ **它在新口径下恒为真**（**结构事实**，不是放松判据）：
    #: 落点等价族**就是**「分歧 ≤ 4 px 的族」→ 族内最大分歧按定义 ≤ 4 px。
    #: **真正起闸门作用的是命中率**（方案原话：视觉通道由命中率决定）。
    calibration_ok: bool = False
    #: 标定问了几轮（K）。
    rounds: int = 1
    #: **命中率** = 命中数 /（K 轮 × 目标数）。
    #: 命中判据：判定标度换算出的物理落点与目标中心的距离 ≤ 命中半径。
    hit_rate: float = 0.0
    #: 判定标度下每个预测（K × 目标数 个）的物理误差分布：**中位数**。
    median_error_px: float = 0.0
    #: 同上：**p90**（最近秩）。
    p90_error_px: float = 0.0
    #: **落点等价判定**（与 :attr:`landing_equivalence` 同一个结论的新名字）。
    #:
    #: ⚠️ 同 :attr:`calibration_ok`：**它在新口径下恒为 ``"equivalent"``**
    #: （族是按 4 px 定义的）→ 真正的闸门是 :attr:`hit_rate`。
    scale_equivalence_verdict: str = EQUIVALENCE_EQUIVALENT
    #: **两个「带分母的相对标度」之间的物理落点分歧**（物理像素）。
    #:
    #: 0~999 与 0~1000 这两个候选，在**探针真值位置**上按各自口径解释时
    #: 落点差多少 —— **探针几何常量**，与模型答成什么样无关
    #: （模型按像素答时，它仍然是从记录里**直接读得出**的那个基线值：
    #: 2560 宽探针上 ≈ **2.87 px**）。
    #:
    #: 它就是「落点等价容差 4 px」的**基线**：
    #: 2.87 px ≤ 4 px → 这两个标度**对落点没有影响**（换标度只挪 2.87 px）。
    denominator_pair_divergence_px: float = 0.0
    #: 探针图哈希（**只落哈希，不落原图**）。
    probe_digest: str = ""
    #: 探针尺寸（物理像素）。
    probe_size: tuple[int, int] = (0, 0)
    #: **实际发出的那张图**的尺寸（绝对像素候选的基准）。
    model_input_size: tuple[int, int] = (0, 0)
    #: 模型标识。
    model_id: str = ""
    #: 调用路径：``"image"``（图像调用）/ ``"text"``（文本调用）。
    call_path: str = "image"
    #: 模型原始回答（``(名字, x, y)``）—— **逐轮展开**（轮次优先），
    #: 长度 = 轮数 × 目标数。记录里有它才能复盘。
    raw_points: tuple[tuple[str, float, float], ...] = ()
    #: 探针真值（``(名字, x, y)``，物理像素）—— 记录里有它才能复算误差。
    targets: tuple[tuple[str, float, float], ...] = ()

    def candidate(self, scale: ModelScale) -> CandidateEvaluation:
        for candidate in self.candidates:
            if candidate.scale is scale:
                return candidate
        raise KeyError(scale)


def _max_divergence(
    left: Sequence[tuple[float, float]], right: Sequence[tuple[float, float]]
) -> float:
    """两组物理落点之间的最大分歧（欧氏）。有一方越界（``nan``）→ 无穷大。

    两组落点**逐轮对齐**（轮次优先展开），所以结果就是「跨 K 轮取最大」。
    """
    worst = 0.0
    for (ax, ay), (bx, by) in zip(left, right, strict=True):
        if math.isnan(ax) or math.isnan(ay) or math.isnan(bx) or math.isnan(by):
            return math.inf
        worst = max(worst, math.hypot(ax - bx, ay - by))
    return worst


def _denominator_pair_divergence(spec: ProbeSpec, frame: FrameGeometry) -> float:
    """两个「带分母的相对标度」在**探针真值位置**上的物理落点分歧。

    **探针几何常量**（与模型答成什么样无关）：
    把目标的真值位置按 0~1000 口径编码成模型空间数值，
    再用 0~999 口径解释**同一个数值** → 落点挪了多远（取四个目标里的最大）。

    2560 宽探针上 = **2.87 px**（架构备忘记的基线）——
    它就是「落点等价容差 4 px」的**基线**：
    2.87 px ≤ 4 px → 这两个标度**对落点没有影响**。
    """
    reference = CoordinateAdapter(ModelScale.RELATIVE_0_1000)
    alternative = CoordinateAdapter(ModelScale.RELATIVE_0_999)
    worst = 0.0
    for target in spec.targets:
        raw_x, raw_y = reference.from_screen(target.x, target.y, frame)
        try:
            px, py = alternative.to_screen(raw_x, raw_y, frame)
        except CoordinateError:
            # 换一种口径解释就落到表面外 → 分歧无穷大（如实记）。
            return math.inf
        worst = max(worst, math.hypot(px - target.x, py - target.y))
    return worst


def _percentile(values: Sequence[float], fraction: float) -> float:
    """最近秩百分位（``rank = ceil(fraction × n)``，钳到 n）。

    **本包自己实现**：``minicode.ground.stats`` 那份在 ground 包里，
    跨包引用会倒挂（ground 依赖 calib）。
    """
    if not values:
        raise ValueError("空样本没有百分位")
    ordered = sorted(float(value) for value in values)
    rank = max(1, math.ceil(fraction * len(ordered)))
    return ordered[min(rank, len(ordered)) - 1]


def estimate_model_input_from_tokens(
    observed_input_tokens: int | None,
    text_tokens: int | None,
    aspect_ratio: float,
) -> tuple[int, int] | None:
    """用**图像 token 数**反查服务端实际看到的那张图有多大。

    参数
    ----
    observed_input_tokens
        服务端报的这次请求的**总**输入 token 数（文字 + 图像）。未知 → ``None``。

        ⚠️ **必须给「总和」**：服务端开了**提示词缓存**时，图像那部分的
        token 会落在 ``cache_read`` / ``cache_creation`` 里，只看
        ``input_tokens`` 会小到几十（图像 token 进了缓存）→ 相减会算出
        **负的图像 token**。口径的唯一实现是
        :func:`minicode.model.request_log.total_input_tokens`
        （= ``input + cache_read + cache_creation``）；
        :func:`estimate_model_input_from_log` 直接吃请求日志，**用不会错**。
    text_tokens
        **文字部分**的 token 数（调用方量出来给）。未知 → ``None``。
    aspect_ratio
        **发送尺寸**的宽高比（``宽 / 高``）。

    做法
    ----
    ``observed_image_tokens = observed_input_tokens - text_tokens``；
    按 28 px patch 规则，图像 token 数 = ``ceil(W/28) × ceil(H/28)``，
    于是 ``W`` 落在格子 ``[(pw-1)×28+1, pw×28]``、``H`` 落在
    ``[(ph-1)×28+1, ph×28]``（``pw × ph = observed_image_tokens``）。
    格子的**可容纳宽高比区间**是 ``[W_min/H_max, W_max/H_min]`` ——
    声明的宽高比落在里面就算**相容**。

    返回
    ----
    唯一相容时返回**格子上界** ``(pw×28, ph×28)``（与观测 token 数一致的
    最大尺寸，真实值每边差 ≤ 28 px）；**反查不出**（没有相容的格子、
    或有多个、或文本 token 数未知）→ 返回 ``None``（**如实标 unknown**，
    不猜、不返回「最接近的」）。

    ⚠️ **【二手】这条 28 px patch 规则来自 Anthropic**，
    **是否适用于本机用的 MiniMax 端点未验证** —— 它是**反查用的模型**，
    **不是真值来源**。坐标基准仍然是「我们自己缩放后声明的尺寸」。
    """
    if observed_input_tokens is None or text_tokens is None:
        return None
    observed_image_tokens = observed_input_tokens - text_tokens
    if observed_image_tokens <= 0 or aspect_ratio <= 0:
        return None
    matches: list[tuple[int, int]] = []
    for patch_width in range(1, isqrt(observed_image_tokens) + 1):
        if observed_image_tokens % patch_width:
            continue
        patch_height = observed_image_tokens // patch_width
        for width_patches, height_patches in (
            (patch_width, patch_height),
            (patch_height, patch_width),
        ):
            if width_patches == height_patches:
                # 同一个格子（token 数是完全平方数）→ **只算一次**，
                # 否则「唯一相容」会被自己数成两个 → 反查白丢一个真答案。
                continue
            min_width = (width_patches - 1) * IMAGE_PATCH_PX + 1
            max_width = width_patches * IMAGE_PATCH_PX
            min_height = (height_patches - 1) * IMAGE_PATCH_PX + 1
            max_height = height_patches * IMAGE_PATCH_PX
            if min_width / max_height <= aspect_ratio <= max_width / min_height:
                matches.append((max_width, max_height))
    if len(matches) != 1:
        return None
    return matches[0]


def estimate_model_input_from_log(
    record: RequestLog,
    text_tokens: int | None,
    aspect_ratio: float,
) -> tuple[int, int] | None:
    """用**请求日志**反查服务端实际看到的那张图有多大（**用总和**）。

    输入是 :class:`minicode.model.request_log.RequestLog`（请求日志那条记录），
    观测 token 数取 :attr:`minicode.model.request_log.RequestLog.total_input_tokens`
    —— 也就是 ``input + cache_read + cache_creation`` 的**总和**
    （服务端开了提示词缓存时，图像那部分的 token 落在缓存项里；
    只看 ``input_tokens`` 会小到几十 → 相减出负数）。

    其余语义与 :func:`estimate_model_input_from_tokens` 完全一致：
    **反查不出就返回 ``None``**（不猜、不返回「最接近的」）。
    """
    observed = total_input_tokens(
        record.input_tokens,
        record.cache_read_input_tokens,
        record.cache_creation_input_tokens,
    )
    return estimate_model_input_from_tokens(observed, text_tokens, aspect_ratio)


class CalibrationProbe:
    """运行时标定探针。

    参数
    ----
    model
        模型调用端口（:class:`minicode.model.ports.ModelCaller`）。
        **思考预算（effort）由本类显式给出**，不依赖服务端缺省。
    spec
        探针几何（尺寸 + 四个目标）。**尺寸必须等于物理表面尺寸**。
    model_input_size
        **实际发出的那张图**的尺寸（绝对像素候选的基准）。
        缺省 = 探针尺寸（没做预处理缩放）。
        探针图**画在物理表面尺寸**，**送模型前**缩到这个尺寸；
        真值目标仍用物理表面坐标，换算走
        :class:`minicode.coord.coordinates.CoordinateAdapter` +
        :class:`minicode.coord.coordinates.FrameGeometry`
        （``capture=表面尺寸``、``model_input=model_input_size``）。
        ⚠️ **声明尺寸只有一个来源**：
        :func:`minicode.model.image.model_input_size_for`（缩放本身由
        :func:`minicode.model.image.downscale_frame` 负责）。
    call_path
        ``"image"``（图像调用）/ ``"text"``（文本调用）。两条路径各标定一次，
        **标度各自独立**（C-04）。
    rounds
        问几轮（K）。每轮是**另一次问**（提示词不同）。
    session
        会话号：让不同次独立标定会话的提示词**轮换起点不同** →
        6 次会话**相互独立**（逐字重发会拿到同一答案）。
    """

    #: 五个候选标度（判定顺序固定，并列时取靠前的那个）。
    CANDIDATES: tuple[ModelScale, ...] = (
        ModelScale.RELATIVE_0_1,
        ModelScale.RELATIVE_0_999,
        ModelScale.RELATIVE_0_1000,
        ModelScale.ABSOLUTE_CAPTURE,
        ModelScale.ABSOLUTE_MODEL_INPUT,
    )

    def __init__(
        self,
        model: ModelCaller,
        *,
        spec: ProbeSpec,
        model_input_size: tuple[int, int] | None = None,
        call_path: str = "image",
        model_id: str = "",
        effort: Effort | str = Effort.LOW,
        max_tokens: int = 2000,
        calibration_tolerance_px: float = CALIBRATION_TOLERANCE_PX,
        out_of_bounds_tolerance_px: float = OUT_OF_BOUNDS_TOLERANCE_PX,
        equivalence_tolerance_px: float = SCALE_EQUIVALENCE_TOLERANCE_PX,
        hit_radius_px: float | None = None,
        rounds: int = CALIBRATION_ROUNDS,
        session: int = 0,
    ):
        if rounds < 1:
            raise ValueError(f"标定轮数必须为正数，收到 {rounds}")
        self.model = model
        self.spec = spec
        self.model_input_size = model_input_size or (spec.width, spec.height)
        self.call_path = call_path
        self.model_id = model_id
        self.effort = effort
        self.max_tokens = max_tokens
        self.calibration_tolerance_px = float(calibration_tolerance_px)
        self.out_of_bounds_tolerance_px = float(out_of_bounds_tolerance_px)
        self.equivalence_tolerance_px = float(equivalence_tolerance_px)
        #: **命中半径**（物理像素）：默认按**探针自己的几何**算（方块边长的一半）——
        #: 探针尺寸变了它跟着变，**不会**与实际方块失配。
        #: :data:`minicode.calib.tolerance.PROBE_HIT_RADIUS_PX` 是**标准探针**
        #: （2560 宽 → 96 px 方块）上的那个值，两者一致。
        self.hit_radius_px = (
            float(hit_radius_px)
            if hit_radius_px is not None
            else probe_hit_radius(self.spec)
        )
        self.rounds = int(rounds)
        self.session = int(session)

    # -- 帧元数据 ---------------------------------------------------------

    def frame(self) -> FrameGeometry:
        """探针对应的帧元数据（截图 1:1 物理像素，原点 0,0）。

        ``model_input_*`` 是**实际发出的那张图**的尺寸 —— 绝对像素标度的基准。
        """
        return FrameGeometry(
            capture_width=self.spec.width,
            capture_height=self.spec.height,
            model_input_width=self.model_input_size[0],
            model_input_height=self.model_input_size[1],
            capture_to_physical_scale=1.0,
            virtual_origin_x=0,
            virtual_origin_y=0,
            surface_width=self.spec.width,
            surface_height=self.spec.height,
        )

    # -- 主流程 -----------------------------------------------------------

    def run(self) -> CalibrationOutcome:
        """跑一遍探针（K 轮独立问），判定标度。"""
        frame_image = build_probe_frame(self.spec)
        send_image = self._model_input_frame(frame_image)
        frame = self.frame()

        # -- K 轮独立问（每轮提示词不同 → 不是逐字重发）------------------
        raw_rounds: list[tuple[tuple[str, float, float], ...]] = []
        for round_index in range(self.rounds):
            raw_rounds.append(self._ask_model(send_image, round_index))

        # -- 逐轮、逐候选评估 ---------------------------------------------
        per_round: list[dict[ModelScale, tuple[CandidateEvaluation, list[float]]]] = []
        for raw_points in raw_rounds:
            evaluations: dict[ModelScale, tuple[CandidateEvaluation, list[float]]] = {}
            for scale in self.CANDIDATES:
                evaluations[scale] = self._evaluate(scale, raw_points, frame)
            per_round.append(evaluations)

        # -- 跨全部轮次汇总（总误差最小的候选 = 判定标度）-----------------
        candidates = tuple(self._aggregate(per_round, scale) for scale in self.CANDIDATES)
        best = min(candidates, key=lambda candidate: candidate.total_error_px)

        # -- 每个候选与判定标度之间的落点分歧（跨 K 轮取最大）--------------
        divergences: dict[ModelScale, float] = {}
        for candidate in candidates:
            if candidate.scale is best.scale:
                continue
            divergences[candidate.scale] = _max_divergence(
                candidate.landing_points, best.landing_points
            )

        # -- **标度判定**（可分辨性，2 px）：分歧 ≤ 2 px 的候选算「分不开」→
        #    multi。**1 px 容差不许放松**。
        threshold = DISCRIMINABILITY_SAFETY_FACTOR * self.calibration_tolerance_px
        indistinguishable = [
            candidate.scale
            for candidate in candidates
            if candidate.scale is not best.scale
            and divergences[candidate.scale] <= threshold
        ]
        scale_verdict = (
            SCALE_VERDICT_UNIQUE if not indistinguishable else SCALE_VERDICT_MULTI
        )

        # -- **落点等价族**（4 px）：{判定标度} ∪ {分歧 ≤ 4 px 的候选} -------
        equivalent = (best.scale,) + tuple(
            candidate.scale
            for candidate in candidates
            if candidate.scale is not best.scale
            and divergences[candidate.scale] <= self.equivalence_tolerance_px
        )
        family_divergence = max(
            (divergences[scale] for scale in equivalent if scale is not best.scale),
            default=0.0,
        )
        landing_equivalence = (
            EQUIVALENCE_EQUIVALENT
            if family_divergence <= self.equivalence_tolerance_px
            else EQUIVALENCE_DIVERGENT
        )

        # -- **命中率**与误差分布（判定标度下，每个预测逐个算）-------------
        best_errors = [
            error
            for evaluations in per_round
            for error in evaluations[best.scale][1]
        ]
        hits = sum(1 for error in best_errors if error <= self.hit_radius_px)
        total_predictions = self.rounds * len(self.spec.targets)
        hit_rate = (hits / total_predictions) if total_predictions else 0.0

        return CalibrationOutcome(
            detected_scale=best.scale,
            scale_verdict=scale_verdict,
            landing_equivalence=landing_equivalence,
            equivalent_scales=equivalent,
            candidates=candidates,
            divergences_px=divergences,
            calibration_tolerance_px=self.calibration_tolerance_px,
            out_of_bounds_tolerance_px=self.out_of_bounds_tolerance_px,
            equivalence_tolerance_px=self.equivalence_tolerance_px,
            calibration_ok=landing_equivalence == EQUIVALENCE_EQUIVALENT,
            rounds=self.rounds,
            hit_rate=hit_rate,
            median_error_px=_percentile(best_errors, 0.5),
            p90_error_px=_percentile(best_errors, 0.9),
            scale_equivalence_verdict=landing_equivalence,
            denominator_pair_divergence_px=_denominator_pair_divergence(
                self.spec, frame
            ),
            probe_digest=probe_digest(frame_image),
            probe_size=(self.spec.width, self.spec.height),
            model_input_size=self.model_input_size,
            model_id=self.model_id,
            call_path=self.call_path,
            raw_points=tuple(point for raw in raw_rounds for point in raw),
            targets=tuple(
                (target.name, target.x, target.y) for target in self.spec.targets
            ),
        )

    # -- 内部 -------------------------------------------------------------

    def _model_input_frame(self, frame_image: CapturedFrame) -> CapturedFrame:
        """**送模型的那张图**：探针图画在物理表面尺寸，缩到 ``model_input_size``。

        缩放只做**尺寸**这一件事，**不做任何坐标换算**（换算链的唯一真值
        来源是 :class:`minicode.coord.coordinates.FrameGeometry`）。
        """
        target = (int(self.model_input_size[0]), int(self.model_input_size[1]))
        if target == (frame_image.width, frame_image.height):
            return frame_image
        return downscale_frame(frame_image, *target)

    def _ask_model(
        self, frame_image: CapturedFrame, round_index: int
    ) -> tuple[tuple[str, float, float], ...]:
        """向模型问四个目标的坐标。**数值原样带回**，不做任何解释。

        ``round_index`` 决定这一轮的**提示词**（报告顺序与措辞随轮次轮换）
        → 每轮是**另一次问**。
        """
        if self.call_path == "text":
            prompt = text_prompt(
                self.spec, round_index=round_index, session=self.session
            )
            image_base64 = None
        else:
            prompt = image_prompt(
                self.spec,
                self.model_input_size,
                round_index=round_index,
                session=self.session,
            )
            image_base64 = encode_frame_base64(frame_image)
        reply = self.model.complete(
            prompt,
            effort=self.effort,
            max_tokens=self.max_tokens,
            image_base64=image_base64,
        )
        return parse_points(reply.text, self.spec)

    def _aggregate(
        self,
        per_round: Sequence[Mapping[ModelScale, tuple[CandidateEvaluation, list[float]]]],
        scale: ModelScale,
    ) -> CandidateEvaluation:
        """把某个候选在**全部轮次**上的表现汇总成一条。"""
        errors: list[float] = []
        landing: list[tuple[float, float]] = []
        max_error = 0.0
        out_of_bounds = False
        for evaluations in per_round:
            candidate, round_errors = evaluations[scale]
            errors.extend(round_errors)
            landing.extend(candidate.landing_points)
            max_error = max(max_error, candidate.max_error_px)
            out_of_bounds = out_of_bounds or candidate.out_of_bounds
        total = sum(errors)
        return CandidateEvaluation(
            scale=scale,
            max_error_px=max_error,
            mean_error_px=(total / len(errors)) if errors else 0.0,
            out_of_bounds=out_of_bounds,
            landing_points=tuple(landing),
            total_error_px=total,
        )

    def _evaluate(
        self,
        scale: ModelScale,
        raw_points: Sequence[tuple[str, float, float]],
        frame: FrameGeometry,
    ) -> tuple[CandidateEvaluation, list[float]]:
        """用某个候选标度去解释这批数值，算它与真值的误差。"""
        adapter = CoordinateAdapter(
            scale, out_of_bounds_tolerance_px=self.out_of_bounds_tolerance_px
        )
        errors: list[float] = []
        predictions: list[tuple[float, float]] = []
        out_of_bounds = False
        for target, (_, raw_x, raw_y) in zip(self.spec.targets, raw_points, strict=True):
            try:
                px, py = adapter.to_screen(raw_x, raw_y, frame)
            except CoordinateError:
                # 模型空间越界 → 记无穷大误差，**不让异常中断整个标定**。
                out_of_bounds = True
                errors.append(math.inf)
                predictions.append((math.nan, math.nan))
                continue
            predictions.append((px, py))
            errors.append(math.hypot(px - target.x, py - target.y))
        candidate = CandidateEvaluation(
            scale=scale,
            max_error_px=max(errors) if errors else 0.0,
            mean_error_px=(sum(errors) / len(errors)) if errors else 0.0,
            out_of_bounds=out_of_bounds,
            landing_points=tuple(predictions),
            total_error_px=sum(errors) if errors else 0.0,
        )
        return candidate, errors

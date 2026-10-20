"""运行时标定探针：判定模型输出的**量纲**（工单 M5）。

一次标定的形状：

1. 画一张与物理表面同尺寸的探针图（:mod:`minicode.calib.probe`），
   向模型问四个已知目标的坐标 —— **模型给的数值原样保留**，
   本模块不乘任何分母、不按数值大小猜量纲；
2. 用五个候选标度（0~1 / 0~999 / 0~1000 / 截图像素绝对 / 模型输入像素绝对）
   各自去解释这批数值，看哪个与真值吻合；
3. 报**两个互不相同的维度**：

   * **标度判定**（唯一命中 / 多解）—— 探针能不能唯一确定量纲；
   * **落点等价**（容差内等价 / 分歧）—— 换一个标度解释，落点会挪多远。

两者**不许混成一个字段**（工单 M5 AC 5）：多解不等于视觉通道不可用，
只有「存在使物理落点分歧 > 标定容差的替代标度」才不可用
（架构决策备忘 §2.6 C-03 的落点等价判据）。

**越界记为无穷大误差、不中断**（AC 6）：把 0~1000 的数值当 0~1 读时，
模型空间那层先判越界，该候选的最大误差记 ``inf``，标定继续跑完。
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from minicode.action.image import encode_frame_base64
from minicode.action.ports import CapturedFrame
from minicode.coord.coordinates import (
    CoordinateAdapter,
    CoordinateError,
    FrameGeometry,
    ModelScale,
)
from minicode.model.effort import Effort
from minicode.model.ports import ModelCaller

from .probe import ProbeSpec, build_probe_frame, probe_digest
from .prompt import image_prompt, parse_points, text_prompt
from .tolerance import (
    CALIBRATION_TOLERANCE_PX,
    DISCRIMINABILITY_SAFETY_FACTOR,
    OUT_OF_BOUNDS_TOLERANCE_PX,
)

__all__ = [
    "CandidateEvaluation",
    "CalibrationOutcome",
    "CalibrationProbe",
    "SCALE_VERDICT_UNIQUE",
    "SCALE_VERDICT_MULTI",
    "EQUIVALENCE_EQUIVALENT",
    "EQUIVALENCE_DIVERGENT",
]

#: 标度判定：唯一命中。
SCALE_VERDICT_UNIQUE = "unique"
#: 标度判定：多解。
SCALE_VERDICT_MULTI = "multi"
#: 落点等价：容差内等价。
EQUIVALENCE_EQUIVALENT = "equivalent"
#: 落点等价：分歧。
EQUIVALENCE_DIVERGENT = "divergent"


@dataclass(frozen=True)
class CandidateEvaluation:
    """某个候选标度在探针上的表现。"""

    #: 候选标度。
    scale: ModelScale
    #: 该标度下的最大误差（物理像素）。越界 → ``inf``。
    max_error_px: float
    #: 该标度下的平均误差（物理像素）。
    mean_error_px: float
    #: 有没有目标在这个标度下越界（模型空间先判）。
    out_of_bounds: bool
    #: 该标度下四个目标换算出来的**物理落点**（越界那个是 ``nan``）。
    landing_points: tuple[tuple[float, float], ...] = ()


@dataclass(frozen=True)
class CalibrationOutcome:
    """一次标定的结论。"""

    #: 判定标度（误差最小的那个候选）。
    detected_scale: ModelScale
    #: **标度判定**：``"unique"``（唯一命中）/ ``"multi"``（多解）。
    scale_verdict: str
    #: **落点等价**：``"equivalent"``（容差内等价）/ ``"divergent"``（分歧）。
    landing_equivalence: str
    #: **等价标度列表**：探针排除不掉的那些标度。
    equivalent_scales: tuple[ModelScale, ...] = ()
    #: 五个候选各自的误差明细。
    candidates: tuple[CandidateEvaluation, ...] = ()
    #: 每个候选与判定标度之间的**物理落点分歧**（物理像素）。
    divergences_px: Mapping[ModelScale, float] = field(default_factory=dict)
    #: 标定容差（物理像素）。
    calibration_tolerance_px: float = CALIBRATION_TOLERANCE_PX
    #: 越界容差（物理像素）。与标定容差**互不共用**。
    out_of_bounds_tolerance_px: float = OUT_OF_BOUNDS_TOLERANCE_PX
    #: 标定成功吗？判定标度的最大误差 ≤ 标定容差才算成功。
    calibration_ok: bool = False
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
    #: 模型原始回答（``(名字, x, y)``）—— 记录里有它才能复盘。
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
    """两组物理落点之间的最大分歧（欧氏）。有一方越界（``nan``）→ 无穷大。"""
    worst = 0.0
    for (ax, ay), (bx, by) in zip(left, right, strict=True):
        if math.isnan(ax) or math.isnan(ay) or math.isnan(bx) or math.isnan(by):
            return math.inf
        worst = max(worst, math.hypot(ax - bx, ay - by))
    return worst


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
        **模型实际看到的那张图**的尺寸（绝对像素候选的基准）。
        缺省 = 探针尺寸（没做预处理缩放）。
        ⚠️ 服务端可能把大图缩小（实测 MiniMax 端点把本机 2560 宽的图缩到
        约 2048 宽），这时模型的绝对像素坐标落在**缩小后那张图**上——
        基准错了会判成另一个标度（实测误差 571.79 px → 2.66 px）。
    call_path
        ``"image"``（图像调用）/ ``"text"``（文本调用）。两条路径各标定一次，
        **标度各自独立**（C-04）。
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
    ):
        self.model = model
        self.spec = spec
        self.model_input_size = model_input_size or (spec.width, spec.height)
        self.call_path = call_path
        self.model_id = model_id
        self.effort = effort
        self.max_tokens = max_tokens
        self.calibration_tolerance_px = float(calibration_tolerance_px)
        self.out_of_bounds_tolerance_px = float(out_of_bounds_tolerance_px)

    # -- 帧元数据 ---------------------------------------------------------

    def frame(self) -> FrameGeometry:
        """探针对应的帧元数据（截图 1:1 物理像素，原点 0,0）。"""
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
        """跑一遍探针，判定标度。"""
        frame_image = build_probe_frame(self.spec)
        raw_points = self._ask_model(frame_image)
        frame = self.frame()
        candidates = tuple(
            self._evaluate(scale, raw_points, frame) for scale in self.CANDIDATES
        )
        best = min(candidates, key=lambda candidate: candidate.max_error_px)

        divergences: dict[ModelScale, float] = {}
        for candidate in candidates:
            if candidate.scale is best.scale:
                continue
            divergences[candidate.scale] = _max_divergence(
                candidate.landing_points, best.landing_points
            )

        # **候选族（等价标度列表）**：探针**排除不掉**的那些标度
        # （判定标度自己当然在族里）。
        # 判据：落点分歧 ≥ 可分辨安全系数 × 标定容差 才算「探针能分辨」。
        # 分辨力不足时按等价处理（进列表、如实报多解），不假装能分辨。
        threshold = DISCRIMINABILITY_SAFETY_FACTOR * self.calibration_tolerance_px
        equivalent = (best.scale,) + tuple(
            candidate.scale
            for candidate in candidates
            if candidate.scale is not best.scale
            and divergences[candidate.scale] <= threshold
        )
        scale_verdict = (
            SCALE_VERDICT_UNIQUE if len(equivalent) == 1 else SCALE_VERDICT_MULTI
        )

        # **落点等价**：候选族里有没有「分歧 > 标定容差」的标度。
        alternatives = tuple(scale for scale in equivalent if scale is not best.scale)
        landing_equivalence = (
            EQUIVALENCE_EQUIVALENT
            if all(
                divergences[scale] <= self.calibration_tolerance_px
                for scale in alternatives
            )
            else EQUIVALENCE_DIVERGENT
        )

        return CalibrationOutcome(
            detected_scale=best.scale,
            scale_verdict=scale_verdict,
            landing_equivalence=landing_equivalence,
            equivalent_scales=equivalent,
            candidates=candidates,
            divergences_px=divergences,
            calibration_tolerance_px=self.calibration_tolerance_px,
            out_of_bounds_tolerance_px=self.out_of_bounds_tolerance_px,
            calibration_ok=best.max_error_px <= self.calibration_tolerance_px,
            probe_digest=probe_digest(frame_image),
            probe_size=(self.spec.width, self.spec.height),
            model_input_size=self.model_input_size,
            model_id=self.model_id,
            call_path=self.call_path,
            raw_points=raw_points,
            targets=tuple(
                (target.name, target.x, target.y) for target in self.spec.targets
            ),
        )

    # -- 内部 -------------------------------------------------------------

    def _ask_model(
        self, frame_image: CapturedFrame
    ) -> tuple[tuple[str, float, float], ...]:
        """向模型问四个目标的坐标。**数值原样带回**，不做任何解释。"""
        if self.call_path == "text":
            prompt = text_prompt(self.spec)
            image_base64 = None
        else:
            prompt = image_prompt(self.spec)
            image_base64 = encode_frame_base64(frame_image)
        reply = self.model.complete(
            prompt,
            effort=self.effort,
            max_tokens=self.max_tokens,
            image_base64=image_base64,
        )
        return parse_points(reply.text, self.spec)

    def _evaluate(
        self,
        scale: ModelScale,
        raw_points: Sequence[tuple[str, float, float]],
        frame: FrameGeometry,
    ) -> CandidateEvaluation:
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
        return CandidateEvaluation(
            scale=scale,
            max_error_px=max(errors) if errors else 0.0,
            mean_error_px=(sum(errors) / len(errors)) if errors else 0.0,
            out_of_bounds=out_of_bounds,
            landing_points=tuple(predictions),
        )

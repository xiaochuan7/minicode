"""运行时标定探针（架构备忘 §1.4）：判定模型 grounding 输出到底用哪套量纲。

为什么需要它
------------
各家坐标协议并存（架构备忘 附录 B④），而且**官方文档自身会前后矛盾**：
Qwen3-VL 的 grounding 文档页写 "0~1 相对坐标"，技术报告与 model card 写 "[0,1000]"；
GLM-4.5V GUI agent 写 0~999；Qwen2.5-VL 又是 smart_resize 后的绝对像素。
把标度写死在代码里，等于把"静默错位"这种最难查的 bug 写进产品。

所以：启动时用一张**已知真值**的合成图反推量纲，把结果存进
:class:`~coordinates.CoordinateAdapter` 的运行时配置。

完全离线
--------
本模块**不写任何真实 API 调用**。模型通过 :class:`GroundModel` 协议注入：

* :class:`StubGroundModel` —— 离线桩，按指定标度把真值编码成模型输出。
* :class:`CallableGroundModel` —— 把任意 ``callable(image, instruction) -> GroundingResult``
  包成模型。**接真实模型时只需要用这个类把 HTTP/SDK 客户端包一层。**

命令行::

    python calibrate.py                      # 默认桩(0~1000)，跑通全流程并自检
    python calibrate.py --scale relative_0_999
    python calibrate.py --scale absolute_model_input --model-input 1260x784
    python calibrate.py --all-scales         # 遍历所有标度，验证检测器
    python calibrate.py --write-probe probe.png
"""

from __future__ import annotations

import argparse
import math
import random
import re
import struct
import sys
import zlib
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from coordinates import (
    CoordinateAdapter,
    CoordinateError,
    FrameGeometry,
    ModelScale,
    Rect,
)

__all__ = [
    "ProbeTarget",
    "RawPoint",
    "GroundingResult",
    "GroundModel",
    "StubGroundModel",
    "CallableGroundModel",
    "CandidateResult",
    "CalibrationResult",
    "CalibrationProbe",
    "encode_png",
    "build_probe_image",
    "default_targets",
    "main",
]

# ---------------------------------------------------------------------------
# 纯标准库 PNG 编码（探针图要能喂给真实 VLM，所以必须是真 PNG）
# ---------------------------------------------------------------------------


def _png_chunk(tag: bytes, data: bytes) -> bytes:
    return (
        struct.pack(">I", len(data))
        + tag
        + data
        + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    )


def encode_png(width: int, height: int, pixels: bytearray) -> bytes:
    """把 RGB 像素缓冲编码成 PNG 字节流（纯标准库）。

    输入 ``pixels`` 长度必须是 ``width * height * 3``，行优先。
    """
    expected = width * height * 3
    if len(pixels) != expected:
        raise ValueError(f"像素缓冲长度应为 {expected}，收到 {len(pixels)}")
    stride = width * 3
    raw = bytearray()
    for y in range(height):
        raw.append(0)  # filter type 0 (None)
        raw += pixels[y * stride : (y + 1) * stride]
    return (
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + _png_chunk(b"IDAT", zlib.compress(bytes(raw), 6))
        + _png_chunk(b"IEND", b"")
    )


# ---------------------------------------------------------------------------
# 探针图
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ProbeTarget:
    """探针上的一个高对比目标。

    x / y 是目标**中心的物理像素坐标**（探针图按 1:1 物理像素绘制）。
    """

    name: str
    x: int
    y: int
    size: int = 48
    color: Tuple[int, int, int] = (0, 0, 0)


def default_targets(
    width: int,
    height: int,
    *,
    margin: int = 96,
    size: int = 48,
) -> List[ProbeTarget]:
    """在四角放 4 个目标（架构备忘 §1.4）。

    刻意贴近四角：右下角目标的 x 接近图像宽度，是区分 0~1000 与 0~999
    的关键（见 :func:`CalibrationProbe.run` 的 notes）。
    """
    half = size / 2.0
    return [
        ProbeTarget("tl", int(margin + half), int(margin + half), size),
        ProbeTarget("tr", int(width - margin - half), int(margin + half), size),
        ProbeTarget("bl", int(margin + half), int(height - margin - half), size),
        ProbeTarget("br", int(width - margin - half), int(height - margin - half), size),
    ]


def build_probe_image(
    width: int,
    height: int,
    targets: Sequence[ProbeTarget],
    *,
    background: Tuple[int, int, int] = (255, 255, 255),
    border_px: int = 4,
) -> bytes:
    """绘制探针图并返回 PNG 字节流。

    绘制方式：白底 + 实心深色方块 + 黑色描边，保证任何 VLM 都能看清边界。
    """
    pixels = bytearray(bytes(background) * (width * height))
    stride = width * 3
    for target in targets:
        half = target.size / 2.0
        x0 = int(round(target.x - half))
        y0 = int(round(target.y - half))
        x1 = int(round(target.x + half))
        y1 = int(round(target.y + half))
        x0 = max(0, x0)
        y0 = max(0, y0)
        x1 = min(width, x1)
        y1 = min(height, y1)
        for y in range(y0, y1):
            base = y * stride
            for bx in range(x0, x1):
                off = base + bx * 3
                pixels[off] = target.color[0]
                pixels[off + 1] = target.color[1]
                pixels[off + 2] = target.color[2]
        # 描边（黑色），让方块边界在高对比下更锐利
        for y in range(max(0, y0 - border_px), min(height, y0 + border_px)):
            base = y * stride
            for bx in range(x0, x1):
                off = base + bx * 3
                pixels[off] = pixels[off + 1] = pixels[off + 2] = 0
        for y in range(max(0, y1 - border_px), min(height, y1 + border_px)):
            base = y * stride
            for bx in range(x0, x1):
                off = base + bx * 3
                pixels[off] = pixels[off + 1] = pixels[off + 2] = 0
        for y in range(y0, y1):
            base = y * stride
            for bx in list(range(max(0, x0 - border_px), x0)) + list(range(x1, min(width, x1 + border_px))):
                off = base + bx * 3
                pixels[off] = pixels[off + 1] = pixels[off + 2] = 0
    return encode_png(width, height, pixels)


# ---------------------------------------------------------------------------
# 模型接口
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RawPoint:
    """模型返回的原始点。量纲由外部的 ``ModelScale`` 决定，此处不做任何解释。"""

    x: float
    y: float


@dataclass(frozen=True)
class GroundingResult:
    """一次 grounding 调用的结果。"""

    points: Tuple[RawPoint, ...]
    input_width: int
    input_height: int
    model_id: str = "stub"

    def first(self) -> RawPoint:
        if not self.points:
            raise ValueError("grounding 结果里没有任何点")
        return self.points[0]


class GroundModel:
    """模型接口（结构化类型；鸭子类型，鸭子鸭子）。

    真实模型接入时**只需要实现这一个方法**::

        class MyClient:
            def ground(self, image: bytes, instruction: str) -> GroundingResult:
                ...  # 调你的 API，把返回的数值原样放进 RawPoint

    注意：``RawPoint`` 里放**模型原样返回的数值**，不要提前换算——
    换算正是标定探针要判定的事情。
    """

    def ground(self, image: bytes, instruction: str) -> GroundingResult:  # pragma: no cover
        raise NotImplementedError


@dataclass
class StubGroundModel:
    """离线桩模型：按指定标度把已知真值编码成"模型输出"。

    参数
    ----
    scale
        桩要模拟的量纲。标定器应该能把 ``scale`` 判定出来。
    targets
        探针目标（真值）。
    capture_width / capture_height
        探针图尺寸（= 物理像素，1:1）。
    model_input_width / model_input_height
        桩"实际看到"的图像尺寸。给成不同于 capture 的值即可模拟
        Qwen2.5-VL 那种 smart_resize 缩放。
    noise_px
        在**物理像素**上叠加的确定性噪声（用 seed 固定，可复现）。
    """

    scale: ModelScale
    targets: Sequence[ProbeTarget]
    capture_width: int
    capture_height: int
    model_input_width: Optional[int] = None
    model_input_height: Optional[int] = None
    noise_px: float = 0.0
    seed: int = 20261019
    model_id: str = "stub"

    def __post_init__(self) -> None:
        self.model_input_width = self.model_input_width or self.capture_width
        self.model_input_height = self.model_input_height or self.capture_height
        self._rng = random.Random(self.seed)
        self._by_name: Dict[str, ProbeTarget] = {t.name: t for t in self.targets}

    def ground(self, image: bytes, instruction: str) -> GroundingResult:
        """按配置标度输出目标坐标。"""
        match = re.search(r"click the (\w+) target", instruction)
        if not match:
            raise ValueError(f"桩模型无法解析指令：{instruction!r}")
        target = self._by_name.get(match.group(1))
        if target is None:
            raise KeyError(f"未知目标：{match.group(1)}")

        px = target.x + self._rng.gauss(0.0, self.noise_px)
        py = target.y + self._rng.gauss(0.0, self.noise_px)

        if self.scale is ModelScale.RELATIVE_0_1:
            out_x, out_y = px / self.capture_width, py / self.capture_height
        elif self.scale.is_relative:
            denom = self.scale.denominator
            out_x, out_y = px / self.capture_width * denom, py / self.capture_height * denom
        elif self.scale is ModelScale.ABSOLUTE_CAPTURE:
            out_x, out_y = px, py
        else:  # ABSOLUTE_MODEL_INPUT
            sx = self.model_input_width / self.capture_width
            sy = self.model_input_height / self.capture_height
            out_x, out_y = px * sx, py * sy
        return GroundingResult(
            points=(RawPoint(out_x, out_y),),
            input_width=self.model_input_width,
            input_height=self.model_input_height,
            model_id=self.model_id,
        )


class CallableGroundModel:
    """把任意可调用对象包成 :class:`GroundModel`。

    **接真实模型时的替换点就在这里**::

        client = MyRealVlmClient()          # 你自己的 HTTP/SDK 封装
        model = CallableGroundModel(
            lambda image, instruction: GroundingResult(
                points=(RawPoint(*client.ground(image, instruction)),),
                input_width=..., input_height=...,
            )
        )
        probe = CalibrationProbe(model, ...)
        result = probe.run()
        adapter = CoordinateAdapter(result.detected_scale)
    """

    def __init__(self, fn: Callable[[bytes, str], GroundingResult], model_id: str = "callable"):
        self._fn = fn
        self.model_id = model_id

    def ground(self, image: bytes, instruction: str) -> GroundingResult:
        return self._fn(image, instruction)


# ---------------------------------------------------------------------------
# 标定
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CandidateResult:
    """某个候选标度在探针上的表现。"""

    scale: ModelScale
    max_error_px: float
    mean_error_px: float
    errors_px: Tuple[float, ...]
    predictions: Tuple[Tuple[float, float], ...]

    @property
    def acceptable(self) -> bool:
        return self.max_error_px <= 0.0 or True  # 由外部 tolerance 判定，见 CalibrationResult


@dataclass(frozen=True)
class CalibrationResult:
    """标定结论。"""

    detected_scale: ModelScale
    candidates: Tuple[CandidateResult, ...]
    tolerance_px: float
    confident: bool
    equivalent_scales: Tuple[ModelScale, ...]
    divergence_px: Dict[ModelScale, float]
    roundtrip_ok: bool
    notes: Tuple[str, ...]

    @property
    def unique(self) -> bool:
        """选中标度是否是**唯一**可判定的那一个。

        False 表示存在其它标度在容差内同样成立（例如模型输入尺寸 == 截图尺寸时，
        "截图像素绝对" 与 "模型输入像素绝对" 根本无法区分）。
        """
        return not self.equivalent_scales

    def best(self) -> CandidateResult:
        for c in self.candidates:
            if c.scale is self.detected_scale:
                return c
        raise KeyError(self.detected_scale)

    def summary(self) -> str:
        lines = [
            f"标定结果: {self.detected_scale.value}  "
            f"(max_error={self.best().max_error_px:.2f}px, tol={self.tolerance_px}px, "
            f"confident={self.confident}, unique={self.unique})",
            "候选明细:",
        ]
        for c in sorted(self.candidates, key=lambda c: c.max_error_px):
            flag = " <= 选中" if c.scale is self.detected_scale else ""
            lines.append(
                f"  {c.scale.value:<24} max_err={c.max_error_px:10.2f}px "
                f"mean={c.mean_error_px:10.2f}px{flag}"
            )
        if self.equivalent_scales:
            lines.append(
                "在容差内等价(选谁都不影响落点): "
                + ", ".join(s.value for s in self.equivalent_scales)
            )
        for scale, div in self.divergence_px.items():
            lines.append(
                f"  与 {self.detected_scale.value} 的最大物理分歧: "
                f"{scale.value} = {div:.2f}px"
            )
        lines.append(f"往返自检(物理→模型→物理): {'通过' if self.roundtrip_ok else '失败'}")
        for note in self.notes:
            lines.append(f"  note: {note}")
        return "\n".join(lines)


class CalibrationProbe:
    """运行时标定探针。

    参数
    ----
    model
        任意 :class:`GroundModel` 实现。
    width / height
        探针图尺寸（= 物理像素）。默认 2560×1600，与目标机一致。
    targets
        探针目标；缺省为四角各一。
    model_input_width / model_input_height
        模型实际看到的图像尺寸（用于 ABSOLUTE_MODEL_INPUT 候选的换算）。
    capture_to_physical_scale
        截图→物理的缩放因子。探针图按 1:1 物理像素绘制，缺省 1.0。
    tolerance_px
        判定"等价"的容差（物理像素），缺省 2.0（行动协议的值）。
    """

    CANDIDATES: Tuple[ModelScale, ...] = (
        ModelScale.RELATIVE_0_1,
        ModelScale.RELATIVE_0_999,
        ModelScale.RELATIVE_0_1000,
        ModelScale.ABSOLUTE_CAPTURE,
        ModelScale.ABSOLUTE_MODEL_INPUT,
    )

    def __init__(
        self,
        model: GroundModel,
        *,
        width: int = 2560,
        height: int = 1600,
        targets: Optional[Sequence[ProbeTarget]] = None,
        model_input_width: Optional[int] = None,
        model_input_height: Optional[int] = None,
        capture_to_physical_scale: float = 1.0,
        tolerance_px: float = 1.0,
    ):
        self.model = model
        self.width = width
        self.height = height
        self.targets = list(targets) if targets is not None else default_targets(width, height)
        self.model_input_width = model_input_width or width
        self.model_input_height = model_input_height or height
        self.capture_to_physical_scale = capture_to_physical_scale
        self.tolerance_px = tolerance_px

    # -- 帧元数据 -----------------------------------------------------------

    def frame(self) -> FrameGeometry:
        """探针对应的 :class:`FrameGeometry`（截图 1:1 物理像素，原点 0,0）。"""
        return FrameGeometry(
            capture_width=self.width,
            capture_height=self.height,
            model_input_width=self.model_input_width,
            model_input_height=self.model_input_height,
            capture_to_physical_scale=self.capture_to_physical_scale,
            virtual_origin_x=0,
            virtual_origin_y=0,
            surface_width=self.width,
            surface_height=self.height,
        )

    def build_image(self) -> bytes:
        """渲染探针 PNG。"""
        return build_probe_image(self.width, self.height, self.targets)

    # -- 主流程 -------------------------------------------------------------

    def run(self) -> CalibrationResult:
        """跑一遍探针，判定标度。"""
        image = self.build_image()
        frame = self.frame()

        # 1) 向模型问每个目标的坐标（原样保留，不做任何换算）
        raw_points: List[RawPoint] = []
        for target in self.targets:
            result = self.model.ground(image, f"click the {target.name} target")
            raw_points.append(result.first())

        # 2) 用每个候选标度去解释这批数值，看哪个与真值吻合
        candidates: List[CandidateResult] = []
        for scale in self.CANDIDATES:
            adapter = CoordinateAdapter(scale, out_of_bounds_tolerance_px=self.tolerance_px)
            errors: List[float] = []
            preds: List[Tuple[float, float]] = []
            for target, raw in zip(self.targets, raw_points):
                try:
                    px, py = adapter.to_screen(raw.x, raw.y, frame)
                except CoordinateError:
                    # 该候选下这批数值根本越界（例如把 0~1000 的 953 当 0~1 读）。
                    # 记为无穷大误差，而不是让异常中断整个标定。
                    errors.append(math.inf)
                    preds.append((math.nan, math.nan))
                    continue
                preds.append((px, py))
                errors.append(math.hypot(px - target.x, py - target.y))
            candidates.append(
                CandidateResult(
                    scale=scale,
                    max_error_px=max(errors) if errors else 0.0,
                    mean_error_px=(sum(errors) / len(errors)) if errors else 0.0,
                    errors_px=tuple(errors),
                    predictions=tuple(preds),
                )
            )

        winner = min(candidates, key=lambda c: c.max_error_px)
        confident = winner.max_error_px <= self.tolerance_px
        equivalent = tuple(
            c.scale for c in candidates if c.scale is not winner.scale and c.max_error_px <= self.tolerance_px
        )

        # 3) 分歧度：选错标度会把落点挪多远？（这决定"要不要较真"）
        divergence: Dict[ModelScale, float] = {}
        for c in candidates:
            if c.scale is winner.scale:
                continue
            pairs = list(zip(c.predictions, winner.predictions))
            if any(math.isnan(a[0]) or math.isnan(b[0]) for a, b in pairs):
                divergence[c.scale] = math.inf
                continue
            div = max(
                math.hypot(a[0] - b[0], a[1] - b[1])
                for a, b in pairs
            )
            divergence[c.scale] = div

        # 4) 往返自检：物理 → 模型 → 物理 是否还原
        roundtrip_ok = self._roundtrip(winner.scale, frame, raw_points)

        notes: List[str] = []
        if not confident:
            notes.append(
                f"标定不可信：最佳候选 {winner.scale.value} 的最大误差 "
                f"{winner.max_error_px:.2f}px > 容差 {self.tolerance_px}px"
            )
        immaterial = [s for s, d in divergence.items() if d <= self.tolerance_px]
        if immaterial:
            notes.append(
                "这些标度与选中标度的落点分歧 ≤ 容差，选谁都行: "
                + ", ".join(s.value for s in immaterial)
            )
        material = [s for s, d in divergence.items() if d > self.tolerance_px]
        if material:
            notes.append(
                "以下标度与选中标度的落点分歧 > 容差，必须区分: "
                + ", ".join(f"{s.value}({divergence[s]:.2f}px)" for s in material)
            )
        notes.append(
            f"探针宽度 {self.width}px：0~1000 与 0~999 的最大物理分歧约 "
            f"{self.denominator_divergence_estimate():.2f}px"
        )

        return CalibrationResult(
            detected_scale=winner.scale,
            candidates=tuple(candidates),
            tolerance_px=self.tolerance_px,
            confident=confident,
            equivalent_scales=equivalent,
            divergence_px=divergence,
            roundtrip_ok=roundtrip_ok,
            notes=tuple(notes),
        )

    def denominator_divergence_estimate(self) -> float:
        """估算 0~1000 与 0~999 在本探针上的物理分歧（px）。

        推导：同一个原始数值 r，两种解释相差
            |r/1000 - r/999| * W = r * W / 999000
        而 r = 物理x / W * 1000，故 x 方向分歧 = 物理x / 999。
        y 方向同理 = 物理y / 999。取各目标的最大 hypot。
        """
        best = 0.0
        for t in self.targets:
            best = max(best, math.hypot(t.x / 999.0, t.y / 999.0))
        return best

    def _roundtrip(
        self,
        scale: ModelScale,
        frame: FrameGeometry,
        raw_points: Sequence[RawPoint],
    ) -> bool:
        """物理 → 模型 → 物理 的往返自检。"""
        adapter = CoordinateAdapter(scale, out_of_bounds_tolerance_px=self.tolerance_px)
        for target, raw in zip(self.targets, raw_points):
            mx, my = adapter.from_screen(target.x, target.y, frame)
            px, py = adapter.to_screen(mx, my, frame)
            if math.hypot(px - target.x, py - target.y) > 1e-6:
                return False
        return True


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_stub(
    scale: ModelScale,
    probe_width: int,
    probe_height: int,
    model_input: Optional[Tuple[int, int]],
    noise_px: float,
) -> Tuple[StubGroundModel, List[ProbeTarget]]:
    targets = default_targets(probe_width, probe_height)
    miw = model_input[0] if model_input else probe_width
    mih = model_input[1] if model_input else probe_height
    stub = StubGroundModel(
        scale=scale,
        targets=targets,
        capture_width=probe_width,
        capture_height=probe_height,
        model_input_width=miw,
        model_input_height=mih,
        noise_px=noise_px,
    )
    return stub, targets


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="运行时坐标标定探针（离线可跑，不调任何真实 API）",
    )
    parser.add_argument("--scale", default="relative_0_1000", help="桩模型要模拟的标度")
    parser.add_argument(
        "--all-scales",
        action="store_true",
        help="遍历所有标度各跑一遍，验证检测器（返回码非 0 表示有标度未被正确识别）",
    )
    parser.add_argument("--width", type=int, default=2560)
    parser.add_argument("--height", type=int, default=1600)
    parser.add_argument("--model-input", default=None, help="模型实际看到的尺寸，如 1260x784")
    parser.add_argument("--noise", type=float, default=0.0, help="桩模型噪声（物理像素）")
    parser.add_argument(
        "--tolerance",
        type=float,
        default=1.0,
        help=(
            "等价判定容差（物理像素）。默认 1.0：0~999 与 0~1000 的物理分歧 = 物理x/999，"
            "2560 宽探针边缘 2.56px、中部 1.28px，容差必须 < 1.28px 才能稳定区分；"
            "容差取 2.0 时 1280 宽探针会静默判错"
        ),
    )
    parser.add_argument("--write-probe", default=None, help="把探针 PNG 写到该路径")
    args = parser.parse_args(list(argv) if argv is not None else None)

    model_input = None
    if args.model_input:
        parts = args.model_input.lower().replace("*", "x").split("x")
        if len(parts) != 2:
            parser.error("--model-input 格式应为 WxH")
        model_input = (int(parts[0]), int(parts[1]))

    scales = list(CalibrationProbe.CANDIDATES) if args.all_scales else [ModelScale.parse(args.scale)]

    failures = 0
    for scale in scales:
        stub, targets = _build_stub(scale, args.width, args.height, model_input, args.noise)
        probe = CalibrationProbe(
            stub,
            width=args.width,
            height=args.height,
            targets=targets,
            model_input_width=stub.model_input_width,
            model_input_height=stub.model_input_height,
            tolerance_px=args.tolerance,
        )
        result = probe.run()
        print(f"=== 桩标度: {scale.value} ===")
        print(result.summary())
        print()
        if result.detected_scale is not scale:
            if scale in result.equivalent_scales:
                # 不可区分：两者在容差内同样成立（例如模型输入尺寸 == 截图尺寸时，
                # "截图像素绝对" 与 "模型输入像素绝对" 是同一组数字）。不算失败。
                print(
                    f"== {scale.value} 与选中标度在本探针下**不可区分**"
                    f"（两者误差均 <= 容差），视为通过"
                )
            else:
                failures += 1
                print(f"!! 检测失败：期望 {scale.value}，实际 {result.detected_scale.value}")
        if not result.confident:
            failures += 1
            print("!! 标定不可信（误差超出容差）")

    if args.write_probe:
        stub, targets = _build_stub(scales[0], args.width, args.height, model_input, args.noise)
        with open(args.write_probe, "wb") as fh:
            fh.write(build_probe_image(args.width, args.height, targets))
        print(f"探针图已写入 {args.write_probe}")

    return 0 if failures == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

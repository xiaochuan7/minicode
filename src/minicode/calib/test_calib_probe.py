"""标定探针的**外部行为**单测（工单 M5 / A-1）。

断言的对象是**对外可见的契约字段**：探针几何、标度判定、落点等价、
等价标度列表、**K 轮命中率与误差分布**、落盘记录、视觉通道准入判定。

⚠️ **口径归位**（A-1，用户已签字）：1 px 只管**可分辨性**
（「两个候选标度能不能分开」），**不管模型落点误差**。因此：

* ``equivalent_scales`` 是**落点等价族**（4 px）：0~999 与 0~1000 的
  2.87 px 分歧落在族里（不是「分开了就排除」）；
* ``scale_verdict`` 仍按**可分辨性**（2 px）判 unique / multi；
* ``calibration_ok`` = **落点等价族判定成功**，不再是「模型误差 ≤ 1 px」；
* **准入**看的是「落点等价族分歧 ≤ 4 px」**且**「K 轮命中率 ≥ 0.6」。

另有三类**源码级**检查（工单 AC 10 / AC 11 的字面要求就是源码级判据，
与 `minicode/model/test_citation_discipline.py` 同一路子）：
换算/标定链路不许出现写死分母、不许有按数值大小猜量纲的分支、
本包不许引第三方包。

判据来源：架构决策备忘 §2.6 的 **C-03**（探针几何 / 判定容差 / 落盘）与
**C-04**（不写死分母 / 不按数值大小猜量纲 / 两条路径各标定一次）。
"""

from __future__ import annotations

import base64
import json
import math
import re
import unittest
from pathlib import Path

from minicode.action.errors import ErrorCode
from minicode.action.fakes import (
    FakeCaptureSource,
    FakeControlTree,
    FakeInputInjector,
    FakeWindowController,
)
from minicode.action.geometry import VirtualDesktop
from minicode.action.ports import LocateRequest, LocateResult
from minicode.action.records import ListTraceSink
from minicode.action.seam import ActionExecutionSeam, SeamRequest
from minicode.calib import (
    CALIBRATION_MIN_HIT_RATE,
    CALIBRATION_ROUNDS,
    CALIBRATION_TOLERANCE_PX,
    DISCRIMINABILITY_SAFETY_FACTOR,
    OUT_OF_BOUNDS_TOLERANCE_PX,
    PROBE_HIT_RADIUS_PX,
    SCALE_EQUIVALENCE_TOLERANCE_PX,
    CalibrationAdmission,
    CalibrationLog,
    CalibrationProbe,
    CalibrationStore,
    default_probe_spec,
    diagnose_server_model_input_from_tokens,
    estimate_model_input_from_log,
    estimate_model_input_from_tokens,
    probe_hit_radius,
    visual_channel_available,
)
from minicode.calib.probe import build_probe_frame, probe_png
from minicode.coord.coordinates import CoordinateAdapter, FrameGeometry, ModelScale
from minicode.model.client import ModelReply
from minicode.model.effort import Effort
from minicode.model.request_log import RequestLog, png_image_size

#: 本机物理表面尺寸（真实值；探针尺寸必须等于它，不是写死的 1280×720）。
SURFACE_W, SURFACE_H = 2560, 1600

#: 被测包源码目录（扫描「不许出现写死分母」用）。
_CALIB_DIR = Path(__file__).resolve().parent


def _spec(width: int = SURFACE_W, height: int = SURFACE_H):
    return default_probe_spec(width, height)


class _StubModel:
    """按指定标度作答的桩模型（只实现 ``complete``，形状与真客户端一致）。"""

    def __init__(
        self,
        scale: ModelScale,
        spec,
        *,
        model_input_size: tuple[int, int] | None = None,
        shift: tuple[float, float] = (0.0, 0.0),
        reply_form: str = "json",
    ):
        self.scale = scale
        self.spec = spec
        self.model_input_size = model_input_size or (spec.width, spec.height)
        self.shift = shift
        self.reply_form = reply_form
        self.calls: list[dict] = []

    def _frame(self) -> FrameGeometry:
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

    def complete(
        self,
        prompt: str,
        *,
        effort: Effort | str | None = None,
        max_tokens: int = 2000,
        image_base64: str | None = None,
        thinking_disabled: bool = False,
    ) -> ModelReply:
        self.calls.append(
            {
                "prompt": prompt,
                "effort": effort,
                "image_base64": image_base64,
            }
        )
        adapter = CoordinateAdapter(
            self.scale, out_of_bounds_tolerance_px=OUT_OF_BOUNDS_TOLERANCE_PX
        )
        frame = self._frame()
        points: dict[str, list[float]] = {}
        for target in self.spec.targets:
            mx, my = adapter.from_screen(target.x, target.y, frame)
            points[target.name] = [mx + self.shift[0], my + self.shift[1]]
        if self.reply_form == "json":
            text = json.dumps({"points": points}, ensure_ascii=False)
        else:
            text = "\n".join(
                f"{name}: ({values[0]:.1f}, {values[1]:.1f})" for name, values in points.items()
            )
        return ModelReply(
            text=text,
            model="stub",
            endpoint="stub://model",
            effort=Effort.LOW,
            http_status=200,
            latency_ms=1.0,
        )


def _run(
    scale: ModelScale = ModelScale.RELATIVE_0_1000,
    *,
    width: int = SURFACE_W,
    height: int = SURFACE_H,
    model_input_size: tuple[int, int] | None = None,
    calibration_tolerance_px: float = CALIBRATION_TOLERANCE_PX,
    out_of_bounds_tolerance_px: float = OUT_OF_BOUNDS_TOLERANCE_PX,
    equivalence_tolerance_px: float = SCALE_EQUIVALENCE_TOLERANCE_PX,
    shift: tuple[float, float] = (0.0, 0.0),
    call_path: str = "image",
    model_id: str = "stub-model",
    reply_form: str = "json",
    rounds: int = CALIBRATION_ROUNDS,
    session: int = 0,
):
    """跑一次标定，返回 ``(outcome, stub)``。"""
    spec = _spec(width, height)
    stub = _StubModel(
        scale,
        spec,
        model_input_size=model_input_size,
        shift=shift,
        reply_form=reply_form,
    )
    probe = CalibrationProbe(
        stub,
        spec=spec,
        model_input_size=model_input_size or (width, height),
        call_path=call_path,
        model_id=model_id,
        calibration_tolerance_px=calibration_tolerance_px,
        out_of_bounds_tolerance_px=out_of_bounds_tolerance_px,
        equivalence_tolerance_px=equivalence_tolerance_px,
        rounds=rounds,
        session=session,
    )
    return probe.run(), stub


class _CoverageFailureLocator:
    """UIA 通道的**覆盖失败**（候选集为空 → 允许切视觉）。"""

    def locate(self, request: LocateRequest) -> LocateResult:
        return LocateResult(
            error_code=ErrorCode.TARGET_NOT_FOUND,
            error_message="控件树里没有这个目标",
            failure_type="coverage",
        )


class _StubAdmission:
    """视觉通道准入判定的替身。"""

    def __init__(self, admission):
        self.admission = admission

    def admit(self):
        return self.admission


class TestProbeGeometry(unittest.TestCase):
    """AC 1：探针几何。"""

    def test_probe_size_equals_surface_size(self):
        spec = _spec()
        self.assertEqual((spec.width, spec.height), (SURFACE_W, SURFACE_H))
        frame = build_probe_frame(spec)
        self.assertEqual((frame.width, frame.height), (SURFACE_W, SURFACE_H))
        self.assertTrue(probe_png(spec).startswith(b"\x89PNG\r\n\x1a\n"))

    def test_source_has_no_hardcoded_small_probe(self):
        """源码里不许出现 1280×720 这样的探针默认值。"""
        offenders = []
        for path in sorted(_CALIB_DIR.glob("*.py")):
            if path.name.startswith("test_"):
                continue
            text = path.read_text(encoding="utf-8")
            for needle in ("1280", "720"):
                if needle in text:
                    offenders.append(f"{path.name}:{needle}")
        self.assertEqual(offenders, [])

    def test_rightmost_target_is_at_the_right_edge(self):
        spec = _spec()
        rightmost = spec.rightmost
        self.assertGreaterEqual(rightmost.x, 0.95 * spec.width)
        self.assertEqual(len(spec.targets), 4)

    def test_hit_radius_is_half_the_target_size(self):
        """命中半径 = 目标方块边长的一半（标准探针 = 48 px）。"""
        spec = _spec()
        self.assertEqual(probe_hit_radius(spec), PROBE_HIT_RADIUS_PX)
        self.assertEqual(probe_hit_radius(spec), spec.targets[0].size / 2.0)

    def test_denominator_divergence_has_two_x_margin(self):
        """0~999 与 0~1000 的落点分歧：x 轴 ≥ 2.0 px、最坏点欧氏 ≥ 2.5 px、余量 ≥ 2 倍。

        「最坏点」= 分歧最大的那个目标（右下角）：x 轴分歧随 x 线性增长，
        欧氏分歧还要叠上 y 方向的那一份 —— 架构备忘 §1.4 给的 2.86 px 就是它。
        """
        outcome, _ = _run(ModelScale.RELATIVE_0_1000)
        by_scale = {c.scale: c for c in outcome.candidates}
        p1000 = by_scale[ModelScale.RELATIVE_0_1000].landing_points
        p999 = by_scale[ModelScale.RELATIVE_0_999].landing_points
        right_edge_index = max(
            range(len(outcome.targets)), key=lambda i: outcome.targets[i][1]
        )
        dx = abs(p1000[right_edge_index][0] - p999[right_edge_index][0])
        euclid = max(
            math.hypot(a[0] - b[0], a[1] - b[1]) for a, b in zip(p1000, p999, strict=True)
        )
        self.assertGreaterEqual(dx, 2.0)
        self.assertGreaterEqual(euclid, 2.5)
        self.assertGreaterEqual(
            euclid / outcome.calibration_tolerance_px,
            DISCRIMINABILITY_SAFETY_FACTOR,
        )


class TestSilentFailureIsDetectable(unittest.TestCase):
    """AC 2：静默失败必须可测出来。"""

    def test_wide_tolerance_makes_999_indistinguishable(self):
        """判定容差放宽到 2.0 px（探针仍 2560 宽）→ 探针分不开 999/1000。

        注意：**可分辨性**（2 px）与**落点等价**（4 px）是两回事 ——
        2.87 px 的分歧在 4 px 的落点等价族里，所以 `equivalent_scales`
        仍含 999；但**可分辨**判据放宽到 2 px 后 999 与 1000 **分不开**了
        → `scale_verdict` 变 multi。
        """
        outcome, _ = _run(
            ModelScale.RELATIVE_0_1000, calibration_tolerance_px=2.0
        )
        self.assertEqual(outcome.scale_verdict, "multi")
        self.assertIn(ModelScale.RELATIVE_0_999, outcome.equivalent_scales)

    def test_small_probe_makes_999_equivalent(self):
        """探针缩到 1280 宽（容差仍 1.0）→ 0~999 与 0~1000 判为等价。"""
        outcome, _ = _run(
            ModelScale.RELATIVE_0_1000, width=1280, height=800
        )
        self.assertIn(ModelScale.RELATIVE_0_999, outcome.equivalent_scales)

    def test_full_probe_separates_999_from_1000(self):
        """真实配置（2560 宽 + 1 px 容差）必须能把两者分开。

        A-1 归位后：``equivalent_scales`` 是**落点等价族**（4 px），
        2.87 px 的分歧**在**族里（换标度只挪 2.87 px，不影响落点）；
        但**可分辨性**（1 px × 安全系数 2 = 2 px）仍然能把两者分开
        → `scale_verdict == "unique"`。**1 px 没有放松。**
        """
        outcome, _ = _run(ModelScale.RELATIVE_0_1000)
        self.assertEqual(outcome.scale_verdict, "unique")
        self.assertIn(ModelScale.RELATIVE_0_999, outcome.equivalent_scales)
        self.assertLessEqual(
            outcome.divergences_px[ModelScale.RELATIVE_0_999],
            SCALE_EQUIVALENCE_TOLERANCE_PX,
        )


class TestToleranceIndependence(unittest.TestCase):
    """AC 3：容差互不共用。"""

    def test_two_constants_are_distinct(self):
        self.assertEqual(CALIBRATION_TOLERANCE_PX, 1.0)
        self.assertEqual(OUT_OF_BOUNDS_TOLERANCE_PX, 2.0)
        self.assertNotEqual(CALIBRATION_TOLERANCE_PX, OUT_OF_BOUNDS_TOLERANCE_PX)

    def test_new_constants_are_independent(self):
        """A-1 的四个新常量与既有的两个**互不共用**。"""
        self.assertEqual(SCALE_EQUIVALENCE_TOLERANCE_PX, 4.0)
        self.assertEqual(PROBE_HIT_RADIUS_PX, 48)
        self.assertEqual(CALIBRATION_ROUNDS, 5)
        self.assertEqual(CALIBRATION_MIN_HIT_RATE, 0.6)
        for value in (
            SCALE_EQUIVALENCE_TOLERANCE_PX,
            float(PROBE_HIT_RADIUS_PX),
            CALIBRATION_MIN_HIT_RATE,
        ):
            self.assertNotEqual(value, CALIBRATION_TOLERANCE_PX)
            self.assertNotEqual(value, OUT_OF_BOUNDS_TOLERANCE_PX)

    def test_changing_calibration_tolerance_keeps_out_of_bounds_verdicts(self):
        narrow, _ = _run(ModelScale.RELATIVE_0_1000, calibration_tolerance_px=1.0)
        wide, _ = _run(ModelScale.RELATIVE_0_1000, calibration_tolerance_px=2.0)
        self.assertEqual(
            [c.out_of_bounds for c in narrow.candidates],
            [c.out_of_bounds for c in wide.candidates],
        )

    def test_changing_out_of_bounds_tolerance_keeps_calibration_verdict(self):
        base, _ = _run(ModelScale.RELATIVE_0_1000, out_of_bounds_tolerance_px=2.0)
        other, _ = _run(ModelScale.RELATIVE_0_1000, out_of_bounds_tolerance_px=4.0)
        self.assertEqual(base.detected_scale, other.detected_scale)
        self.assertEqual(base.scale_verdict, other.scale_verdict)
        self.assertEqual(base.equivalent_scales, other.equivalent_scales)
        self.assertEqual(base.calibration_ok, other.calibration_ok)

    def test_changing_equivalence_tolerance_changes_only_the_family(self):
        """落点等价容差只管**落点等价族**，不碰可分辨性。"""
        base, _ = _run(ModelScale.RELATIVE_0_1000)
        tight, _ = _run(
            ModelScale.RELATIVE_0_1000, equivalence_tolerance_px=1.0
        )
        self.assertIn(ModelScale.RELATIVE_0_999, base.equivalent_scales)
        self.assertNotIn(ModelScale.RELATIVE_0_999, tight.equivalent_scales)
        self.assertEqual(base.scale_verdict, tight.scale_verdict)


class TestFiveScales(unittest.TestCase):
    """AC 4：五种标度全部可判。"""

    def test_all_five_scales_are_detected(self):
        for scale in (
            ModelScale.RELATIVE_0_1,
            ModelScale.RELATIVE_0_999,
            ModelScale.RELATIVE_0_1000,
            ModelScale.ABSOLUTE_CAPTURE,
            ModelScale.ABSOLUTE_MODEL_INPUT,
        ):
            with self.subTest(scale=scale.value):
                model_input = (1280, 800) if scale.is_relative else (1280, 800)
                outcome, _ = _run(scale, model_input_size=model_input)
                self.assertIs(outcome.detected_scale, scale)
                self.assertEqual(len(outcome.candidates), 5)
                for candidate in outcome.candidates:
                    self.assertIsInstance(candidate.max_error_px, float)

    def test_candidate_errors_are_recorded(self):
        outcome, _ = _run(ModelScale.RELATIVE_0_1000)
        errors = {c.scale: c.max_error_px for c in outcome.candidates}
        self.assertAlmostEqual(errors[ModelScale.RELATIVE_0_1000], 0.0, places=6)
        self.assertGreater(errors[ModelScale.RELATIVE_0_999], 0.0)
        self.assertTrue(math.isinf(errors[ModelScale.RELATIVE_0_1]))


class TestScaleVerdictAndLandingEquivalence(unittest.TestCase):
    """AC 5：标度判定与落点等价必须分开报。"""

    def test_absolute_scales_are_multi_solution_when_input_equals_capture(self):
        outcome, _ = _run(
            ModelScale.ABSOLUTE_CAPTURE,
            model_input_size=(SURFACE_W, SURFACE_H),
        )
        self.assertEqual(outcome.scale_verdict, "multi")
        self.assertEqual(outcome.landing_equivalence, "equivalent")
        self.assertIn(ModelScale.ABSOLUTE_CAPTURE, outcome.equivalent_scales)
        self.assertIn(ModelScale.ABSOLUTE_MODEL_INPUT, outcome.equivalent_scales)
        self.assertNotEqual(outcome.scale_verdict, "unique")

    def test_verdict_and_equivalence_are_two_fields(self):
        """两个维度给出不同结论，不许混成一个字段。

        窄探针（1280 宽）上 0~999 与 0~1000 的分歧 1.43 px：
        **可分辨性**（2 px）说「分不开」→ multi；
        **落点等价**（4 px）说「等价」→ equivalent。
        """
        outcome, _ = _run(ModelScale.RELATIVE_0_1000, width=1280, height=800)
        self.assertEqual(outcome.scale_verdict, "multi")
        self.assertEqual(outcome.landing_equivalence, "equivalent")
        self.assertEqual(outcome.scale_equivalence_verdict, "equivalent")

    def test_equivalence_verdict_and_landing_equivalence_agree(self):
        outcome, _ = _run(ModelScale.RELATIVE_0_1000)
        self.assertEqual(outcome.landing_equivalence, outcome.scale_equivalence_verdict)


class TestKRoundIndependentAsks(unittest.TestCase):
    """A-1：K 轮**独立**问（每轮提示词不同）。"""

    def test_model_is_called_once_per_round(self):
        outcome, stub = _run(ModelScale.RELATIVE_0_1000, rounds=5)
        self.assertEqual(outcome.rounds, 5)
        self.assertEqual(len(stub.calls), 5)

    def test_prompts_of_each_round_differ(self):
        """逐字重发在确定性模型下会拿到同一答案 → 每轮提示词必须不同。"""
        _, stub = _run(ModelScale.RELATIVE_0_1000, rounds=5)
        prompts = [call["prompt"] for call in stub.calls]
        self.assertEqual(len(set(prompts)), len(prompts))

    def test_sessions_shift_the_prompt_rotation(self):
        """不同会话的提示词轮换起点不同 → 6 次会话相互独立。"""
        _, first = _run(ModelScale.RELATIVE_0_1000, rounds=5, session=0)
        _, second = _run(ModelScale.RELATIVE_0_1000, rounds=5, session=1)
        self.assertNotEqual(
            [call["prompt"] for call in first.calls],
            [call["prompt"] for call in second.calls],
        )

    def test_rounds_can_be_overridden(self):
        outcome, stub = _run(ModelScale.RELATIVE_0_1000, rounds=3)
        self.assertEqual(outcome.rounds, 3)
        self.assertEqual(len(stub.calls), 3)

    def test_prompt_declares_the_sent_image_size(self):
        """A-2：提示词里写明那张图是 W×H。"""
        _, stub = _run(
            ModelScale.RELATIVE_0_1000, model_input_size=(SURFACE_W // 2, SURFACE_H // 2)
        )
        prompt = stub.calls[0]["prompt"]
        self.assertIn(f"{SURFACE_W // 2}×{SURFACE_H // 2}", prompt)

    def test_sent_image_is_the_declared_size(self):
        """送出去的那张图就是**声明**的那个尺寸（我们自己缩）。"""
        model_input = (SURFACE_W // 2, SURFACE_H // 2)
        _, stub = _run(ModelScale.RELATIVE_0_1000, model_input_size=model_input)
        sent = base64.b64decode(stub.calls[0]["image_base64"])
        self.assertEqual(png_image_size(sent), model_input)


class TestHitRate(unittest.TestCase):
    """A-1：命中率判据（落点落在 96 px 方块内 = 距中心 ≤ 48 px）。"""

    def test_perfect_answers_hit_every_target(self):
        outcome, _ = _run(ModelScale.RELATIVE_0_1000)
        self.assertEqual(outcome.hit_rate, 1.0)
        self.assertEqual(outcome.rounds, CALIBRATION_ROUNDS)

    def test_hit_radius_boundary_counts_as_hit(self):
        """距离**正好等于**命中半径 → 命中（≤ 半径）。"""
        outcome, _ = _run(ModelScale.ABSOLUTE_CAPTURE, shift=(48.0, 0.0))
        self.assertEqual(outcome.hit_rate, 1.0)

    def test_just_outside_the_hit_radius_is_a_miss(self):
        outcome, _ = _run(ModelScale.ABSOLUTE_CAPTURE, shift=(48.5, 0.0))
        self.assertEqual(outcome.hit_rate, 0.0)

    def test_hit_rate_counts_over_rounds_and_targets(self):
        """命中率 = 命中数 /（K 轮 × 目标数）。"""
        outcome, _ = _run(ModelScale.ABSOLUTE_CAPTURE, shift=(48.0, 0.0), rounds=4)
        self.assertEqual(len(outcome.raw_points), 4 * len(outcome.targets))
        self.assertEqual(outcome.hit_rate, 1.0)

    def test_error_distribution_is_reported(self):
        outcome, _ = _run(ModelScale.RELATIVE_0_1000)
        self.assertAlmostEqual(outcome.median_error_px, 0.0, places=6)
        self.assertAlmostEqual(outcome.p90_error_px, 0.0, places=6)

    def test_error_distribution_follows_the_shift(self):
        outcome, _ = _run(ModelScale.ABSOLUTE_CAPTURE, shift=(10.0, 0.0))
        self.assertAlmostEqual(outcome.median_error_px, 10.0, places=6)
        self.assertAlmostEqual(outcome.p90_error_px, 10.0, places=6)


class TestDenominatorPairDivergence(unittest.TestCase):
    """落盘里能**直接读出**「0~999 与 0~1000 的分歧」（落点等价容差的基线）。"""

    def test_the_baseline_is_the_documented_value(self):
        """2560 宽探针上 ≈ 2.87 px（架构备忘记的基线）→ ≤ 4 px 容差。"""
        outcome, _ = _run(ModelScale.RELATIVE_0_1000)
        self.assertAlmostEqual(outcome.denominator_pair_divergence_px, 2.87, delta=0.05)
        self.assertLessEqual(
            outcome.denominator_pair_divergence_px, SCALE_EQUIVALENCE_TOLERANCE_PX
        )

    def test_it_is_a_probe_geometry_constant(self):
        """与模型答成什么样无关（模型按像素答时它照样是 2.87 px）。"""
        relative, _ = _run(ModelScale.RELATIVE_0_1000)
        absolute, _ = _run(ModelScale.ABSOLUTE_CAPTURE)
        self.assertAlmostEqual(
            relative.denominator_pair_divergence_px,
            absolute.denominator_pair_divergence_px,
            places=6,
        )

    def test_it_is_readable_from_the_record(self):
        from minicode.calib.record import CalibrationRecord

        outcome, _ = _run(ModelScale.RELATIVE_0_1000)
        data = CalibrationRecord.from_outcome(outcome).to_dict()
        self.assertIn("denominator_pair_divergence_px", data)
        self.assertAlmostEqual(data["denominator_pair_divergence_px"], 2.87, delta=0.05)


class TestOutOfRangeAnswer(unittest.TestCase):
    """AC 6：越界记为无穷大误差、不中断。"""

    def test_out_of_range_candidate_is_infinite_error(self):
        outcome, _ = _run(ModelScale.RELATIVE_0_1000)
        by_scale = {c.scale: c for c in outcome.candidates}
        self.assertTrue(math.isinf(by_scale[ModelScale.RELATIVE_0_1].max_error_px))
        self.assertTrue(by_scale[ModelScale.RELATIVE_0_1].out_of_bounds)
        for scale in (
            ModelScale.RELATIVE_0_999,
            ModelScale.RELATIVE_0_1000,
            ModelScale.ABSOLUTE_CAPTURE,
            ModelScale.ABSOLUTE_MODEL_INPUT,
        ):
            self.assertFalse(math.isinf(by_scale[scale].max_error_px))

    def test_out_of_range_does_not_stop_the_calibration(self):
        outcome, stub = _run(ModelScale.RELATIVE_0_1000)
        self.assertIsNotNone(outcome.detected_scale)
        self.assertEqual(len(stub.calls), outcome.rounds)

    def test_line_form_answer_is_parsed(self):
        """模型不按 JSON 答时（逐行写法）也要读得出四个坐标。"""
        outcome, _ = _run(ModelScale.RELATIVE_0_1000, reply_form="line")
        self.assertIs(outcome.detected_scale, ModelScale.RELATIVE_0_1000)
        self.assertEqual(len(outcome.raw_points), outcome.rounds * 4)


class TestVisualAdmission(unittest.TestCase):
    """AC 7：硬闸门。"""

    def test_admission_conditions_are_independent(self):
        """两条条件各自独立生效：

        ① 落点等价族分歧 ≤ 4 px；② K 轮命中率 ≥ 0.6。
        """
        ok = visual_channel_available(
            equivalent_scales=("relative_0_1000", "relative_0_999"),
            divergences_px={"relative_0_1000": 0.0, "relative_0_999": 2.87},
            equivalence_tolerance_px=SCALE_EQUIVALENCE_TOLERANCE_PX,
            hit_rate=0.8,
            min_hit_rate=CALIBRATION_MIN_HIT_RATE,
        )
        self.assertTrue(ok)
        # 只有分歧超标 → 拒绝（命中率达标也不行）
        self.assertFalse(
            visual_channel_available(
                equivalent_scales=("relative_0_1000", "relative_0_999"),
                divergences_px={"relative_0_1000": 0.0, "relative_0_999": 5.0},
                equivalence_tolerance_px=SCALE_EQUIVALENCE_TOLERANCE_PX,
                hit_rate=1.0,
                min_hit_rate=CALIBRATION_MIN_HIT_RATE,
            )
        )
        # 只有命中率不达标 → 拒绝（分歧达标也不行）
        self.assertFalse(
            visual_channel_available(
                equivalent_scales=("relative_0_1000", "relative_0_999"),
                divergences_px={"relative_0_1000": 0.0, "relative_0_999": 2.87},
                equivalence_tolerance_px=SCALE_EQUIVALENCE_TOLERANCE_PX,
                hit_rate=0.4,
                min_hit_rate=CALIBRATION_MIN_HIT_RATE,
            )
        )

    def test_unavailable_when_alternative_diverges_more_than_tolerance(self):
        """窄探针（1280 宽）上 999 与 1000 的分歧 1.43 px ≤ 4 px → 判**等价**。

        新口径下**准入放行**（只要命中率达标）—— 1.43 px 的分歧换掉标度
        也只挪动落点 1.43 px，不影响落点。
        """
        outcome, _ = _run(ModelScale.RELATIVE_0_1000, width=1280, height=800)
        admission = CalibrationAdmission(outcome).admit()
        self.assertTrue(admission.available)
        self.assertTrue(admission.reason)
        self.assertEqual(admission.landing_equivalence, "equivalent")

    def test_allowed_when_equivalent_scales_share_the_landing_point(self):
        outcome, _ = _run(
            ModelScale.ABSOLUTE_CAPTURE,
            model_input_size=(SURFACE_W, SURFACE_H),
        )
        admission = CalibrationAdmission(outcome).admit()
        self.assertTrue(admission.available)
        self.assertEqual(admission.landing_equivalence, "equivalent")
        self.assertTrue(admission.equivalent_scales)

    def test_unavailable_when_hit_rate_below_threshold(self):
        """落点偏移到 48 px 命中半径之外 → 命中率 0 < 0.6 → 视觉通道不可用。

        （旧口径是「模型误差 > 1 px → 标定失败」；A-1 归位后 1 px 只管
        可分辨性，落点准不准由**命中率**判。）
        """
        outcome, _ = _run(ModelScale.ABSOLUTE_CAPTURE, shift=(60.0, 0.0))
        self.assertLess(outcome.hit_rate, CALIBRATION_MIN_HIT_RATE)
        admission = CalibrationAdmission(outcome).admit()
        self.assertFalse(admission.available)
        self.assertIn("命中率", admission.reason)

    def test_calibration_ok_is_structurally_true_and_the_hit_rate_is_the_gate(self):
        """A-1 归位后的**结构事实**（不是放松判据）：

        落点等价族**就是**「分歧 ≤ 4 px 的族」→ ``calibration_ok`` 恒真；
        **真正起闸门作用的是命中率**（方案原话：视觉通道由命中率决定）。
        """
        small, _ = _run(ModelScale.ABSOLUTE_CAPTURE, shift=(3.0, 0.0))
        self.assertTrue(small.calibration_ok)
        self.assertGreaterEqual(small.hit_rate, CALIBRATION_MIN_HIT_RATE)
        self.assertTrue(CalibrationAdmission(small).admit().available)

        big, _ = _run(ModelScale.ABSOLUTE_CAPTURE, shift=(60.0, 0.0))
        self.assertTrue(big.calibration_ok)  # 仍然恒真 —— 它不是闸门
        self.assertLess(big.hit_rate, CALIBRATION_MIN_HIT_RATE)
        self.assertFalse(CalibrationAdmission(big).admit().available)

    def test_unavailable_reason_is_human_readable(self):
        outcome, _ = _run(ModelScale.ABSOLUTE_CAPTURE, shift=(60.0, 0.0))
        admission = CalibrationAdmission(outcome).admit()
        self.assertIn("px", admission.reason)
        self.assertIn("命中率", admission.reason)
        self.assertIn("视觉通道不可用", admission.reason)

    def _seam(self, admission):
        sink = ListTraceSink()
        seam = ActionExecutionSeam(
            capture_sources=(FakeCaptureSource(width=2560, height=1600),),
            tree=FakeControlTree(()),
            injector=FakeInputInjector(),
            window_controller=FakeWindowController(foreground=1),
            virtual_desktop=VirtualDesktop(0, 0, 2560, 1600),
            locator=_CoverageFailureLocator(),
            visual_admission=_StubAdmission(admission),
            trace_sink=sink,
        )
        return seam, sink

    def test_seam_reports_visual_channel_unavailable(self):
        outcome, _ = _run(ModelScale.ABSOLUTE_CAPTURE, shift=(60.0, 0.0))
        admission = CalibrationAdmission(outcome).admit()
        seam, sink = self._seam(admission)
        result = seam.run(SeamRequest(target_description="开始"))
        self.assertFalse(result.ok)
        self.assertIs(result.result.error_code, ErrorCode.VISUAL_CHANNEL_UNAVAILABLE)
        self.assertIsNone(result.action)
        self.assertIsNone(result.final_point)
        self.assertEqual(len(sink.traces), 1)
        self.assertIsNotNone(sink.traces[0].visual_admission)
        self.assertFalse(sink.traces[0].visual_admission.available)

    def test_seam_records_landing_equivalence_without_blocking(self):
        outcome, _ = _run(
            ModelScale.ABSOLUTE_CAPTURE,
            model_input_size=(SURFACE_W, SURFACE_H),
        )
        admission = CalibrationAdmission(outcome).admit()
        seam, sink = self._seam(admission)
        result = seam.run(SeamRequest(target_description="开始"))
        self.assertIsNot(result.result.error_code, ErrorCode.VISUAL_CHANNEL_UNAVAILABLE)
        self.assertEqual(len(sink.traces), 1)
        traced = sink.traces[0].visual_admission
        self.assertIsNotNone(traced)
        self.assertTrue(traced.available)
        self.assertEqual(traced.landing_equivalence, "equivalent")
        self.assertTrue(traced.equivalent_scales)


class TestRecord(unittest.TestCase):
    """AC 8：落盘。"""

    def _record(self):
        outcome, _ = _run(ModelScale.RELATIVE_0_1000, model_id="stub-model")
        from minicode.calib.record import CalibrationRecord

        return CalibrationRecord.from_outcome(outcome), outcome

    def test_record_has_all_required_fields(self):
        record, _ = self._record()
        data = record.to_dict()
        for key in (
            "timestamp",
            "probe_sha256",
            "probe_width",
            "probe_height",
            "model_input_width",
            "model_input_height",
            "detected_scale",
            "calibration_tolerance_px",
            "candidates",
            "equivalent_scales",
            "scale_verdict",
            "landing_equivalence",
            "model_id",
            "call_path",
            "raw_points",
            "targets",
            "rounds",
            "hit_rate",
            "median_error_px",
            "p90_error_px",
            "equivalence_tolerance_px",
            "scale_equivalence_verdict",
            "denominator_pair_divergence_px",
        ):
            self.assertIn(key, data)
        self.assertEqual(len(data["candidates"]), 5)
        for candidate in data["candidates"]:
            self.assertIn("scale", candidate)
            self.assertIn("max_error_px", candidate)

    def test_record_carries_the_new_fields(self):
        record, outcome = self._record()
        self.assertEqual(record.rounds, outcome.rounds)
        self.assertAlmostEqual(record.hit_rate, outcome.hit_rate)
        self.assertAlmostEqual(record.median_error_px, outcome.median_error_px)
        self.assertAlmostEqual(record.p90_error_px, outcome.p90_error_px)
        self.assertEqual(
            record.equivalence_tolerance_px, SCALE_EQUIVALENCE_TOLERANCE_PX
        )
        self.assertEqual(record.scale_equivalence_verdict, outcome.scale_equivalence_verdict)

    def test_old_records_are_readable(self):
        """旧记录（没有 A-1 的新字段）**读得回来**（用默认值，不报错）。"""
        from minicode.calib.record import CalibrationRecord

        data = self._record()[0].to_dict()
        for key in (
            "rounds",
            "hit_rate",
            "median_error_px",
            "p90_error_px",
            "equivalence_tolerance_px",
            "scale_equivalence_verdict",
            "denominator_pair_divergence_px",
            "error",
        ):
            data.pop(key)
        back = CalibrationRecord.from_dict(data)
        self.assertEqual(back.rounds, 1)
        self.assertEqual(back.hit_rate, 0.0)
        self.assertEqual(back.median_error_px, 0.0)
        self.assertEqual(back.p90_error_px, 0.0)
        self.assertEqual(back.error, "")
        self.assertEqual(
            back.equivalence_tolerance_px, SCALE_EQUIVALENCE_TOLERANCE_PX
        )

    def test_a_failed_session_leaves_a_record(self):
        """**失败的会话也要留痕** —— 验收线「6 次会话中 ≥ 5 次可用」的分母不能静默变小。

        ⚠️ 失败时不落记录 → 跑 6 次炸 2 次 → 日志里只剩 4 条 → 读成
        「4/4 = 100% 达成」，真相是 4/6 = 67% → **不达成**。
        """
        from minicode.calib.record import CalibrationRecord, CalibrationStore

        record = CalibrationRecord.failed(
            error="CalibrationParseError: 模型回答里缺少这些目标的坐标",
            probe_size=(2560, 1600),
            model_input_size=(1280, 800),
            model_id="stub-model",
            call_path="image",
            rounds=5,
        )
        # 记录里**没有一个测量值**（不编数）。
        self.assertEqual(record.hit_rate, 0.0)
        self.assertEqual(record.raw_points, ())
        self.assertEqual(record.probe_sha256, "")
        self.assertFalse(record.calibration_ok)
        self.assertEqual(record.rounds, 5)
        self.assertNotEqual(record.error, "")
        # 读得回来。
        back = CalibrationRecord.from_dict(record.to_dict())
        self.assertEqual(back.error, record.error)
        # **失败的会话给不出标度**（不能拿一个空标度去换算）。
        store = CalibrationStore([record])
        self.assertIsNone(store.scale_for("stub-model", "image"))

    def test_session_verdicts_count_failed_sessions_in_the_denominator(self):
        """验收线「N 次会话中 ≥ M 次可用」**直接数这个**。

        分母 = **跑了几次会话**（含失败的）；分子 = 准入放行的条数。
        """
        from minicode.calib.record import CalibrationRecord, session_verdicts

        good, _ = self._record()
        failed = CalibrationRecord.failed(
            error="boom", model_id="stub-model", call_path="image"
        )
        verdicts = session_verdicts([good, failed])
        self.assertEqual(len(verdicts), 2)
        self.assertTrue(verdicts[0]["available"])
        self.assertFalse(verdicts[1]["available"])
        self.assertFalse(verdicts[1]["ok"])
        self.assertEqual(verdicts[1]["error"], "boom")

    def test_record_keeps_only_the_probe_hash(self):
        record, _ = self._record()
        blob = json.dumps(record.to_dict(), ensure_ascii=False)
        self.assertNotIn("data:image", blob)
        self.assertNotIn("iVBORw0KGgo", blob)
        self.assertEqual(len(record.probe_sha256), 64)

    def test_record_can_replay_the_scale(self):
        """从记录能复原「当时按哪个量纲算的」。"""
        record, outcome = self._record()
        data = record.to_dict()
        raw = {name: (x, y) for name, x, y in data["raw_points"]}
        truth = {name: (x, y) for name, x, y in data["targets"]}
        frame = FrameGeometry(
            capture_width=data["probe_width"],
            capture_height=data["probe_height"],
            model_input_width=data["model_input_width"],
            model_input_height=data["model_input_height"],
            capture_to_physical_scale=1.0,
            surface_width=data["probe_width"],
            surface_height=data["probe_height"],
        )
        adapter = CoordinateAdapter(
            ModelScale(data["detected_scale"]),
            out_of_bounds_tolerance_px=data["out_of_bounds_tolerance_px"],
        )
        for name, (x, y) in raw.items():
            px, py = adapter.to_screen(x, y, frame)
            self.assertAlmostEqual(px, truth[name][0], places=3)
            self.assertAlmostEqual(py, truth[name][1], places=3)
        self.assertEqual(len(data["raw_points"]), data["rounds"] * len(data["targets"]))
        self.assertEqual(len(outcome.raw_points), data["rounds"] * len(data["targets"]))

    def test_log_round_trips(self):
        record, _ = self._record()
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            log = CalibrationLog(tmp)
            path = log.append(record)
            self.assertTrue(path.exists())
            rows = log.records()
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["detected_scale"], record.detected_scale)

    def test_record_can_be_read_back_offline(self):
        """记录可离线读回：读回来还能复算落点。"""
        record, _ = self._record()
        import tempfile

        from minicode.calib.record import CalibrationRecord, CalibrationStore

        with tempfile.TemporaryDirectory() as tmp:
            log = CalibrationLog(tmp)
            log.append(record)
            store = CalibrationStore.from_log(log)
            back = store.lookup(record.model_id, record.call_path)
            self.assertIsNotNone(back)
            self.assertEqual(back.detected_scale, record.detected_scale)
            self.assertEqual(back.raw_points, record.raw_points)
            self.assertEqual(back.targets, record.targets)
            self.assertEqual(back.candidates, record.candidates)
            self.assertEqual(back.hit_rate, record.hit_rate)
            self.assertEqual(
                CalibrationRecord.from_dict(record.to_dict()).to_dict(),
                record.to_dict(),
            )


class TestTwoCallPaths(unittest.TestCase):
    """AC 9：两条调用路径各标定一次。"""

    def test_paths_produce_independent_records(self):
        image_outcome, image_stub = _run(
            ModelScale.RELATIVE_0_1000, call_path="image", model_id="m1"
        )
        text_outcome, text_stub = _run(
            ModelScale.ABSOLUTE_CAPTURE, call_path="text", model_id="m1"
        )
        self.assertEqual(image_outcome.call_path, "image")
        self.assertEqual(text_outcome.call_path, "text")
        self.assertIsNotNone(image_stub.calls[0]["image_base64"])
        self.assertIsNone(text_stub.calls[0]["image_base64"])

        from minicode.calib.record import CalibrationRecord

        store = CalibrationStore(
            [
                CalibrationRecord.from_outcome(image_outcome),
                CalibrationRecord.from_outcome(text_outcome),
            ]
        )
        self.assertIsNotNone(store.lookup("m1", "image"))
        self.assertIsNotNone(store.lookup("m1", "text"))
        self.assertIs(
            store.scale_for("m1", "image"),
            ModelScale.RELATIVE_0_1000,
        )
        self.assertIs(
            store.scale_for("m1", "text"),
            ModelScale.ABSOLUTE_CAPTURE,
        )

    def test_missing_path_does_not_borrow_the_other_path(self):
        outcome, _ = _run(ModelScale.RELATIVE_0_1000, call_path="image", model_id="m1")
        from minicode.calib.record import CalibrationRecord

        store = CalibrationStore([CalibrationRecord.from_outcome(outcome)])
        self.assertIsNone(store.lookup("m1", "text"))
        self.assertIsNone(store.scale_for("m1", "text"))
        self.assertIsNone(store.scale_for("other-model", "image"))

    def test_scale_is_withheld_when_the_hit_rate_is_too_low(self):
        """硬闸门不只在缝的端口上生效：取标度的地方同样给不出不达标的标度。"""
        from minicode.calib.record import CalibrationRecord

        outcome, _ = _run(ModelScale.ABSOLUTE_CAPTURE, shift=(60.0, 0.0))
        record = CalibrationRecord.from_outcome(outcome)
        store = CalibrationStore([record])
        self.assertIsNotNone(record.equivalent_scales)
        self.assertLess(record.hit_rate, CALIBRATION_MIN_HIT_RATE)
        self.assertIsNone(store.scale_for(record.model_id, record.call_path))

    def test_scale_is_given_when_equivalent_and_hit_rate_is_fine(self):
        """等价族分歧 ≤ 4 px 且命中率达标 → 标度给得出来。"""
        from minicode.calib.record import CalibrationRecord

        outcome, _ = _run(ModelScale.RELATIVE_0_1000, width=1280, height=800)
        record = CalibrationRecord.from_outcome(outcome)
        store = CalibrationStore([record])
        self.assertIsNotNone(record.equivalent_scales)
        self.assertIs(store.scale_for(record.model_id, record.call_path), ModelScale.RELATIVE_0_1000)


class TestEstimateModelInputFromTokens(unittest.TestCase):
    """服务端实际看到的那张图：**按相等断言判定**（唯一真值来源）。

    ⚠️ **新契约**（2026-10-22）：这个函数**不再**返回「±28 px 的盒子」——
    它按**相等断言**（观测图像 token == 期望图像 token，容差 ±2）判定：
    相等 ⇒ 服务端**没再缩** ⇒ 返回**声明尺寸**；不等 ⇒ **坐标系不可信**
    ⇒ 返回 ``None``。盒子降级为**诊断/证据**
    （:func:`diagnose_server_model_input_from_tokens`），**不作准入依据**。
    """

    #: 声明 1280×800 → 期望图像 token = ceil(1280/28) × ceil(800/28) = 46×29。
    DECLARED = (1280, 800)
    EXPECTED = 1334

    def test_equal_tokens_mean_the_model_saw_the_declared_size(self):
        """观测 == 期望（容差内）→ 服务端没再缩 → 返回**声明尺寸**。"""
        self.assertEqual(
            estimate_model_input_from_tokens(self.EXPECTED + 10, 10, self.DECLARED),
            self.DECLARED,
        )

    def test_a_shrunk_image_gives_none(self):
        """服务端再缩了那张图 → **坐标系不可信** → ``None``。

        ⚠️ **不许**返回盒子的答案（「大概缩到了多大」）—— 那不是准入判据。
        """
        self.assertIsNone(
            estimate_model_input_from_tokens(861, 10, self.DECLARED)  # 图像 851
        )

    def test_the_box_answer_is_never_returned_as_the_size(self):
        """**盒子与闸门是两件事**：盒子查得出一个像样的尺寸，
        但相等断言不通过 → 仍然 ``None``（坐标系不可信）。"""
        box = diagnose_server_model_input_from_tokens(861, 10, 1280 / 800)
        self.assertIsNotNone(box)
        self.assertNotEqual(box, self.DECLARED)
        self.assertIsNone(estimate_model_input_from_tokens(861, 10, self.DECLARED))

    def test_unknown_text_tokens_gives_none(self):
        """文本 token 数未知 → 如实标 unknown（不猜）。"""
        self.assertIsNone(
            estimate_model_input_from_tokens(self.EXPECTED + 10, None, self.DECLARED)
        )

    def test_unknown_observed_tokens_gives_none(self):
        self.assertIsNone(
            estimate_model_input_from_tokens(None, 10, self.DECLARED)
        )

    def test_the_tolerance_is_the_named_constant(self):
        """容差 = ±2（网关固定开销），不是严格相等。"""
        from minicode.calib.calibrate import IMAGE_TOKEN_ASSERTION_TOLERANCE_TOKENS

        self.assertEqual(IMAGE_TOKEN_ASSERTION_TOLERANCE_TOKENS, 2)
        inside = self.EXPECTED + IMAGE_TOKEN_ASSERTION_TOLERANCE_TOKENS
        self.assertEqual(
            estimate_model_input_from_tokens(inside + 10, 10, self.DECLARED),
            self.DECLARED,
        )
        self.assertIsNone(
            estimate_model_input_from_tokens(inside + 11, 10, self.DECLARED)
        )

    def test_the_log_variant_uses_the_total_input_tokens(self):
        """服务端开了**提示词缓存**时，观测值必须取**总和**。

        这条日志照抄本机实测：``input_tokens=403``、``cache_read=1280``
        → 总和 1683；减去文字 349 → 图像 1334 = 期望 → 相等 → 声明尺寸。

        **只看 ``input_tokens``** 会把图像那部分漏掉 → 算出来的图像 token
        不是期望值 → **坐标系不可信**（而不是给一个错的尺寸）。
        """
        record = RequestLog(
            effort="low",
            model="stub",
            endpoint="stub://model",
            http_status=200,
            latency_ms=1.0,
            input_tokens=403,
            output_tokens=1,
            cache_read_input_tokens=1280,
        )
        self.assertEqual(record.total_input_tokens, 1683)
        self.assertEqual(
            estimate_model_input_from_log(record, 349, self.DECLARED), self.DECLARED
        )
        # 只看 input_tokens（漏掉缓存里那部分）→ 图像 token 不是期望值 → 不可信
        self.assertIsNone(
            estimate_model_input_from_tokens(record.input_tokens, 349, self.DECLARED)
        )


class TestServerModelInputDiagnostic(unittest.TestCase):
    """**盒子反查**（诊断/证据）—— **不作准入依据**。

    ⚠️ 它回答的是「服务端**大概**缩到了多大」，不是「能不能信」。
    """

    def test_reverse_lookup_finds_the_patch_grid(self):
        """观测 3404 个图像 token + 宽高比 1.6 → 反查出格子上界。"""
        size = diagnose_server_model_input_from_tokens(3417, 13, 1.6)
        self.assertIsNotNone(size)
        width, height = size
        self.assertEqual(-(-width // 28) * -(-height // 28), 3404)
        self.assertAlmostEqual(width / height, 1.6, delta=0.05)

    def test_reverse_lookup_keeps_the_declared_aspect_ratio(self):
        """格子的可容纳比值区间必须容得下声明的宽高比。"""
        size = diagnose_server_model_input_from_tokens(1334, 0, 1280 / 800)
        self.assertIsNotNone(size)
        width, height = size
        self.assertEqual(-(-width // 28) * -(-height // 28), 1334)

    def test_unknown_text_tokens_gives_none(self):
        """文本 token 数未知 → 如实标 unknown（不猜）。"""
        self.assertIsNone(diagnose_server_model_input_from_tokens(3417, None, 1.6))

    def test_unknown_observed_tokens_gives_none(self):
        self.assertIsNone(diagnose_server_model_input_from_tokens(None, 13, 1.6))

    def test_no_compatible_cell_gives_none(self):
        """没有任何格子的宽高比与声明相容 → None。"""
        self.assertIsNone(diagnose_server_model_input_from_tokens(100, 90, 1.6))

    def test_the_total_input_tokens_are_the_ones_to_subtract(self):
        """服务端开了提示词缓存时，观测值必须取**总和**。

        这条日志照抄本机实测：``input_tokens=403``、``cache_read=1280``
        → 总和 1683；减去文字 349 → 图像 1334 = 46×29（28 px 规则）
        → 反查得 (1288, 812)（与声明的 1280×800 每边差 ≤ 28 px）。
        """
        record = RequestLog(
            effort="low",
            model="stub",
            endpoint="stub://model",
            http_status=200,
            latency_ms=1.0,
            input_tokens=403,
            output_tokens=1,
            cache_read_input_tokens=1280,
        )
        self.assertEqual(record.total_input_tokens, 1683)
        size = diagnose_server_model_input_from_tokens(
            record.total_input_tokens, 349, 1.6
        )
        self.assertEqual(size, (1288, 812))
        # 只看 input_tokens（漏掉缓存里那部分）→ 结果完全不同（错的）
        self.assertNotEqual(
            diagnose_server_model_input_from_tokens(record.input_tokens, 349, 1.6),
            size,
        )


class TestNoHardcodedDenominator(unittest.TestCase):
    """AC 10：无写死分母、无按大小猜量纲。"""

    def _code_only(self, path: Path) -> str:
        """只留**代码**（去掉注释与字符串字面量）—— 散文里的标度名不算分母。"""
        import tokenize

        chunks: list[str] = []
        with path.open("rb") as handle:
            for token in tokenize.tokenize(handle.readline):
                if token.type in (tokenize.COMMENT, tokenize.STRING):
                    continue
                chunks.append(token.string)
        return " ".join(chunks)

    def test_business_sources_have_no_denominator_literals(self):
        offenders = []
        for path in sorted(_CALIB_DIR.glob("*.py")):
            if path.name.startswith("test_"):
                continue
            code = self._code_only(path)
            for needle in ("1000", "999"):
                if re.search(rf"\b{needle}\b", code):
                    offenders.append(f"{path.name}:{needle}")
        self.assertEqual(offenders, [])

    def test_adapter_has_no_default_scale(self):
        with self.assertRaises(TypeError):
            CoordinateAdapter()  # type: ignore[call-arg]

    def test_scale_is_not_guessed_from_magnitude(self):
        """喂进 0~1000 的数值，适配器不会自动改判成绝对像素标度。"""
        frame = FrameGeometry(
            capture_width=SURFACE_W,
            capture_height=SURFACE_H,
            model_input_width=SURFACE_W,
            model_input_height=SURFACE_H,
            capture_to_physical_scale=1.0,
            surface_width=SURFACE_W,
            surface_height=SURFACE_H,
        )
        adapter = CoordinateAdapter(
            ModelScale.RELATIVE_0_1000,
            out_of_bounds_tolerance_px=OUT_OF_BOUNDS_TOLERANCE_PX,
        )
        px, py = adapter.to_screen(953.0, 925.0, frame)
        self.assertAlmostEqual(px, 953.0 / 1000 * SURFACE_W, places=6)
        self.assertAlmostEqual(py, 925.0 / 1000 * SURFACE_H, places=6)

    def test_no_magnitude_based_branch_in_sources(self):
        offenders = []
        pattern = re.compile(r"(>=|>|<=|<)\s*(1000|999)\b")
        for path in sorted(_CALIB_DIR.glob("*.py")):
            if path.name.startswith("test_"):
                continue
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if pattern.search(line):
                    offenders.append(f"{path.name}:{number}")
        self.assertEqual(offenders, [])


class TestStandardLibraryOnly(unittest.TestCase):
    """AC 11：只依赖标准库。"""

    def test_sources_import_no_third_party_package(self):
        third_party = ("PIL", "numpy", "requests", "httpx", "mss", "cv2")
        offenders = []
        for path in sorted(_CALIB_DIR.glob("*.py")):
            if path.name.startswith("test_"):
                continue
            text = path.read_text(encoding="utf-8")
            for name in third_party:
                if re.search(rf"^\s*(import|from)\s+{name}\b", text, re.MULTILINE):
                    offenders.append(f"{path.name}:{name}")
        self.assertEqual(offenders, [])


class TestSharedAnswerText(unittest.TestCase):
    """答案行 / 截断的读法**只有一份**（施工清单第 ⑦ 条：两边不许各写一份）。"""

    def test_the_calibration_prompt_reads_it_from_the_shared_module(self):
        from minicode.calib import prompt as calib_prompt
        from minicode.model import answer_text

        self.assertIs(calib_prompt.answer_line_body, answer_text.answer_line_body)
        self.assertIs(calib_prompt.is_truncated, answer_text.is_truncated)

    def test_the_loose_form_fallback_is_still_kept_here(self):
        """**有意**与生产链路不同：标定这边保留散写法的兜底（丢一条标定更贵）。"""
        from minicode.calib.prompt import parse_points

        spec = _spec()
        text = "\n".join(
            f"{target.name}: ({target.x:.1f}, {target.y:.1f})" for target in spec.targets
        )
        points = parse_points(text, spec)
        self.assertEqual(len(points), len(spec.targets))


if __name__ == "__main__":
    unittest.main()

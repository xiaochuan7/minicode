"""标定探针的**外部行为**单测（工单 M5）。

断言的对象是**对外可见的契约字段**：探针几何、标度判定、落点等价、
等价标度列表、落盘记录、视觉通道准入判定。

另有三类**源码级**检查（工单 AC 10 / AC 11 的字面要求就是源码级判据，
与 `minicode/model/test_citation_discipline.py` 同一路子）：
换算/标定链路不许出现写死分母、不许有按数值大小猜量纲的分支、
本包不许引第三方包。

判据来源：架构决策备忘 §2.6 的 **C-03**（探针几何 / 判定容差 / 落盘）与
**C-04**（不写死分母 / 不按数值大小猜量纲 / 两条路径各标定一次）。
"""

from __future__ import annotations

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
    CALIBRATION_TOLERANCE_PX,
    DISCRIMINABILITY_SAFETY_FACTOR,
    OUT_OF_BOUNDS_TOLERANCE_PX,
    CalibrationAdmission,
    CalibrationLog,
    CalibrationProbe,
    CalibrationStore,
    default_probe_spec,
)
from minicode.calib.probe import build_probe_frame, probe_png
from minicode.coord.coordinates import CoordinateAdapter, FrameGeometry, ModelScale
from minicode.model.client import ModelReply
from minicode.model.effort import Effort

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
    shift: tuple[float, float] = (0.0, 0.0),
    call_path: str = "image",
    model_id: str = "stub-model",
    reply_form: str = "json",
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
    )
    return probe.run(), stub


class _CoverageFailureLocator:
    """UIA 通道的**覆盖失败**（候选集为空 → 允许切视觉）。"""

    def locate(self, request: LocateRequest) -> LocateResult:
        return LocateResult(
            error_code=ErrorCode.TARGET_NOT_FOUND,
            error_message="控件树里没有这个目标",
            allows_visual_fallback=True,
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

    def test_wide_tolerance_makes_999_equivalent(self):
        """判定容差放宽到 2.0 px（探针仍 2560 宽）→ 0~999 进等价标度列表。"""
        outcome, _ = _run(
            ModelScale.RELATIVE_0_1000, calibration_tolerance_px=2.0
        )
        self.assertIn(ModelScale.RELATIVE_0_999, outcome.equivalent_scales)

    def test_small_probe_makes_999_equivalent(self):
        """探针缩到 1280 宽（容差仍 1.0）→ 0~999 与 0~1000 判为等价。"""
        outcome, _ = _run(
            ModelScale.RELATIVE_0_1000, width=1280, height=800
        )
        self.assertIn(ModelScale.RELATIVE_0_999, outcome.equivalent_scales)

    def test_full_probe_separates_999_from_1000(self):
        """真实配置（2560 宽 + 1 px 容差）必须能把两者分开。"""
        outcome, _ = _run(ModelScale.RELATIVE_0_1000)
        self.assertNotIn(ModelScale.RELATIVE_0_999, outcome.equivalent_scales)
        self.assertEqual(outcome.scale_verdict, "unique")


class TestToleranceIndependence(unittest.TestCase):
    """AC 3：两个容差互不共用。"""

    def test_two_constants_are_distinct(self):
        self.assertEqual(CALIBRATION_TOLERANCE_PX, 1.0)
        self.assertEqual(OUT_OF_BOUNDS_TOLERANCE_PX, 2.0)
        self.assertNotEqual(CALIBRATION_TOLERANCE_PX, OUT_OF_BOUNDS_TOLERANCE_PX)

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
        """多解 + 落点分歧：两个维度给出不同结论，不许混成一个字段。"""
        outcome, _ = _run(ModelScale.RELATIVE_0_1000, width=1280, height=800)
        self.assertEqual(outcome.scale_verdict, "multi")
        self.assertEqual(outcome.landing_equivalence, "divergent")


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
        self.assertEqual(len(stub.calls), 1)

    def test_line_form_answer_is_parsed(self):
        """模型不按 JSON 答时（逐行写法）也要读得出四个坐标。"""
        outcome, _ = _run(ModelScale.RELATIVE_0_1000, reply_form="line")
        self.assertIs(outcome.detected_scale, ModelScale.RELATIVE_0_1000)
        self.assertEqual(len(outcome.raw_points), 4)


class TestVisualAdmission(unittest.TestCase):
    """AC 7：硬闸门。"""

    def test_unavailable_when_alternative_diverges_more_than_tolerance(self):
        outcome, _ = _run(ModelScale.RELATIVE_0_1000, width=1280, height=800)
        admission = CalibrationAdmission(outcome).admit()
        self.assertFalse(admission.available)
        self.assertTrue(admission.reason)
        self.assertEqual(admission.landing_equivalence, "divergent")

    def test_allowed_when_equivalent_scales_share_the_landing_point(self):
        outcome, _ = _run(
            ModelScale.ABSOLUTE_CAPTURE,
            model_input_size=(SURFACE_W, SURFACE_H),
        )
        admission = CalibrationAdmission(outcome).admit()
        self.assertTrue(admission.available)
        self.assertEqual(admission.landing_equivalence, "equivalent")
        self.assertTrue(admission.equivalent_scales)

    def test_unavailable_when_calibration_failed(self):
        """真机单点误差超过 1.0 px → 标定失败 → 视觉通道不可用（不猜）。"""
        outcome, _ = _run(ModelScale.RELATIVE_0_1000, shift=(3.0, 0.0))
        self.assertFalse(outcome.calibration_ok)
        admission = CalibrationAdmission(outcome).admit()
        self.assertFalse(admission.available)

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
        outcome, _ = _run(ModelScale.RELATIVE_0_1000, width=1280, height=800)
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
        ):
            self.assertIn(key, data)
        self.assertEqual(len(data["candidates"]), 5)
        for candidate in data["candidates"]:
            self.assertIn("scale", candidate)
            self.assertIn("max_error_px", candidate)

    def test_record_keeps_only_the_probe_hash(self):
        record, _ = self._record()
        blob = json.dumps(record.to_dict(), ensure_ascii=False)
        self.assertNotIn("data:image", blob)
        self.assertNotIn("iVBORw0KGgo", blob)
        self.assertEqual(len(record.probe_sha256), 64)

    def test_record_can_replay_the_scale(self):
        """从记录能复原「当时按哪个量纲算的」。"""
        record, _ = self._record()
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

    def test_scale_is_withheld_when_an_alternative_diverges(self):
        """硬闸门不只在缝的端口上生效：取标度的地方同样给不出有分歧的标度。"""
        from minicode.calib.record import CalibrationRecord

        outcome, _ = _run(ModelScale.RELATIVE_0_1000, width=1280, height=800)
        record = CalibrationRecord.from_outcome(outcome)
        store = CalibrationStore([record])
        self.assertIsNotNone(record.equivalent_scales)
        self.assertIsNone(store.scale_for(record.model_id, record.call_path))


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
            text = path.read_text(encoding="utf-8")
            for name in third_party:
                if re.search(rf"^\s*(import|from)\s+{name}\b", text, re.MULTILINE):
                    offenders.append(f"{path.name}:{name}")
        self.assertEqual(offenders, [])


if __name__ == "__main__":
    unittest.main()

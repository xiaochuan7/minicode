"""两级 grounding 与 crop 坐标契约的**外部行为**单测（工单 M6）。

断言的对象是**对外可见的契约字段**：最终落点、粗定位点、裁剪计划、
每步回显、回落标记与原因、错误码、放大路径的契约。

另有三类**源码级**检查（工单 AC 5 / AC 7 / AC 11 的字面要求就是源码级判据，
与 `minicode/model/test_citation_discipline.py` 同一路子）：
换算链路不许出现写死分母、不许出现写死的图像像素上限常量、本包不许引第三方包。

判据来源：架构决策备忘 §2.6 的 **C-03**（分母来自标定）、**C-04**（不写死分母 /
不按数值大小猜量纲）、**C-06**（服务端缩图 → 实际发送尺寸）、**C-16**（crop 精定位）。
"""

from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

from minicode.action.errors import ErrorCode
from minicode.action.ports import CapturedFrame, GroundingEcho, PixelFormat
from minicode.coord.coordinates import ModelScale
from minicode.ground import (
    TwoStageGrounder,
    VisualLocateRequest,
    ZoomRequest,
    crop_frame,
    crop_plan_fields,
    crop_roundtrip_error,
    not_worse_than,
    summarize_errors,
    zoom_locate,
)
from minicode.model.client import ModelReply
from minicode.model.effort import Effort
from minicode.model.errors import NoTextOutputError

#: 被测包源码目录（扫描「不许出现写死分母 / 写死像素上限」用）。
_GROUND_DIR = Path(__file__).resolve().parent


def _reply(x: float, y: float) -> ModelReply:
    """一份「模型给了坐标」的回答。"""
    return ModelReply(
        text=json.dumps({"x": x, "y": y}),
        model="stub",
        endpoint="stub://",
        effort=Effort.LOW,
        http_status=200,
        latency_ms=1.0,
    )


def _text_reply(text: str) -> ModelReply:
    return ModelReply(
        text=text,
        model="stub",
        endpoint="stub://",
        effort=Effort.LOW,
        http_status=200,
        latency_ms=1.0,
    )


class _StubModel:
    """按调用顺序给出预置回答的桩模型（形状与真客户端一致）。"""

    def __init__(self, *outcomes: ModelReply | Exception):
        self._outcomes = list(outcomes)
        self.calls: list[dict] = []

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
                "max_tokens": max_tokens,
                "image_base64": image_base64,
            }
        )
        if not self._outcomes:
            raise AssertionError("桩模型没有预置回答")
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def _detail_frame(width: int, height: int, left: int = 0, top: int = 0) -> CapturedFrame:
    """一张**每个像素都不同**的合成帧（细节足够分辨「裁原始图」与「降采样再上采样」）。"""
    if width * height <= 4096:
        payload = bytearray()
        for y in range(height):
            for x in range(width):
                payload += bytes(((x * 7 + y * 3) % 256, (x * 13 + y * 5) % 256, (x + y) % 256))
        data = bytes(payload)
    else:
        # 大帧走「一行字节重复 + 逐行错位」的快路径（细节同样足够分辨两种裁法）。
        row = bytes((index * 37) % 256 for index in range(width * 3))
        rows = []
        for y in range(height):
            shift = (y % 64) * 3
            rows.append(row[shift:] + row[:shift])
        data = b"".join(rows)
    return CapturedFrame(
        width=width,
        height=height,
        left=left,
        top=top,
        payload=data,
        backend="fake",
        source="synthetic",
        pixel_format=PixelFormat.BGR,
    )


def _downsampled_then_upsampled(frame: CapturedFrame) -> CapturedFrame:
    """把帧**降采样一半再上采样**（最近邻）—— 用来证明它与「直接裁原始图」不同。"""
    half_w = frame.width // 2
    half_h = frame.height // 2
    small = bytearray()
    for y in range(half_h):
        for x in range(half_w):
            sx, sy = x * 2, y * 2
            offset = (sy * frame.width + sx) * 3
            small += frame.payload[offset : offset + 3]
    big = bytearray()
    for y in range(half_h):
        row = bytes(small[y * half_w * 3 : (y + 1) * half_w * 3])
        expanded = bytearray()
        for x in range(half_w):
            expanded += row[x * 3 : x * 3 + 3] * 2
        big += expanded * 2
    return CapturedFrame(
        width=frame.width,
        height=frame.height,
        left=frame.left,
        top=frame.top,
        payload=bytes(big),
        backend="fake",
        source="downsampled",
        pixel_format=frame.pixel_format,
    )


def _grounder(
    model: _StubModel,
    *,
    scale: ModelScale = ModelScale.RELATIVE_0_1000,
    model_input_size: tuple[int, int] | None = None,
    full_screen_size: tuple[int, int] = (2560, 1600),
) -> TwoStageGrounder:
    return TwoStageGrounder(
        model,
        scale=scale,
        model_input_size=model_input_size or full_screen_size,
        full_screen_size=full_screen_size,
        effort=Effort.LOW,
    )


class CropSourceTests(unittest.TestCase):
    """AC 1 · 裁剪源正确（C-06）：裁剪窗口取自原始物理分辨率截图。"""

    def test_crop_is_taken_from_the_original_physical_frame(self):
        frame = _detail_frame(64, 64)
        plan = _plan(origin_x=8, origin_y=8, width=16, height=16, surface=(0, 0, 64, 64))
        crop = crop_frame(frame, plan)

        expected = bytearray()
        for y in range(8, 24):
            offset = (y * 64 + 8) * 3
            expected += frame.payload[offset : offset + 16 * 3]
        self.assertEqual(crop.payload, bytes(expected))
        self.assertEqual((crop.width, crop.height), (16, 16))
        # 裁剪结果的原点是它在物理表面上的原点（crop 内坐标 + 原点偏移）。
        self.assertEqual((crop.left, crop.top), (8, 8))
        self.assertEqual(crop.source, "crop-from-capture")

    def test_crop_differs_from_downsampled_then_upsampled(self):
        frame = _detail_frame(64, 64)
        plan = _plan(origin_x=8, origin_y=8, width=16, height=16, surface=(0, 0, 64, 64))
        direct = crop_frame(frame, plan)
        resampled = crop_frame(_downsampled_then_upsampled(frame), plan)
        self.assertNotEqual(direct.payload, resampled.payload)


class CropPlanTests(unittest.TestCase):
    """AC 2 · 裁剪计划不裁掉目标：平移保尺寸 / 表面不够大时缩小 / 目标是否完整包含。"""

    def test_plan_shifts_at_edge_and_keeps_size(self):
        model = _StubModel(_reply(0.0, 0.0), _reply(0.0, 0.0))
        grounder = _grounder(model)
        frame = _detail_frame(2560, 1600)
        result = grounder.locate(
            VisualLocateRequest(
                target_description="开始",
                frame=frame,
                surface=(0, 0, 2560, 1600),
            )
        )
        plan = result.crop_plan
        self.assertIsNotNone(plan)
        self.assertTrue(plan["shifted"])
        self.assertFalse(plan["shrunk"])
        self.assertEqual(plan["width"], 512)
        self.assertEqual(plan["height"], 512)
        # 粗定位点在窗口内（不裁掉目标）。
        self.assertTrue(plan["target_fully_contained"])
        self.assertGreaterEqual(plan["origin_x"], 0)

    def test_plan_shrinks_when_surface_is_too_small(self):
        model = _StubModel(_reply(150.0, 150.0), _reply(150.0, 150.0))
        grounder = _grounder(model, full_screen_size=(300, 300))
        frame = _detail_frame(300, 300)
        result = grounder.locate(
            VisualLocateRequest(
                target_description="小按钮",
                frame=frame,
                surface=(0, 0, 300, 300),
            )
        )
        plan = result.crop_plan
        self.assertTrue(plan["shrunk"])
        self.assertEqual((plan["width"], plan["height"]), (300, 300))

    def test_target_fully_contained_property_is_exposed(self):
        """目标框比窗口还大时，「目标是否完整包含」必须是假的（属性暴露且被钉死）。"""
        from minicode.coord.coordinates import Rect
        from minicode.coord.crop_refine import plan_crop

        plan = plan_crop(
            100.0,
            100.0,
            Rect(0, 0, 2560, 1600),
            crop_width=512,
            crop_height=512,
            target_radius_px=400,
        )
        self.assertFalse(plan.target_fully_contained)
        fields = crop_plan_fields(plan)
        self.assertIn("target_fully_contained", fields)
        self.assertFalse(fields["target_fully_contained"])


class CropContractTests(unittest.TestCase):
    """AC 3 · crop 契约（crop 内坐标 + 原点偏移）与往返自检。"""

    def test_refine_answer_maps_back_to_the_same_physical_point(self):
        # 粗定位：目标在 (1000, 800)（0~1000 归一化 → 2560×1600 画面）。
        # 裁剪窗口以它为中心；精定位在 crop 内报 (500, 500) → crop 内中心 → 物理中心。
        model = _StubModel(_reply(390.625, 500.0), _reply(500.0, 500.0))
        grounder = _grounder(model)
        frame = _detail_frame(2560, 1600)
        result = grounder.locate(
            VisualLocateRequest(
                target_description="开始",
                frame=frame,
                surface=(0, 0, 2560, 1600),
            )
        )
        plan = result.crop_plan
        expected = (plan["origin_x"] + 256.0, plan["origin_y"] + 256.0)
        self.assertAlmostEqual(result.landing_point[0], expected[0], places=6)
        self.assertAlmostEqual(result.landing_point[1], expected[1], places=6)

    def test_physical_crop_physical_roundtrip_error_is_zero(self):
        from minicode.coord.coordinates import CoordinateAdapter, Rect
        from minicode.coord.crop_refine import plan_crop

        adapter = CoordinateAdapter(ModelScale.RELATIVE_0_1000)
        plan = plan_crop(1000.0, 800.0, Rect(0, 0, 2560, 1600))
        for point in ((1000.0, 800.0), (744.0, 544.0), (1256.0, 1056.0)):
            error = crop_roundtrip_error(adapter, plan, (512, 512), point)
            self.assertEqual(error, 0.0)


class FallbackTests(unittest.TestCase):
    """AC 4 · 粗定位回落：精定位失败 → 回到粗定位结果并继续执行该步。"""

    def test_trace_marks_the_fallback_and_its_reason(self):
        """追踪记录里能读到「回落 = 是 + 回落原因」（AC 4 的字面要求）。"""
        from minicode.action.fakes import (
            FakeCaptureSource,
            FakeControlTree,
            FakeInputInjector,
            FakeWindowController,
        )
        from minicode.action.ports import LocateResult, VisualAdmission
        from minicode.action.records import ListTraceSink, build_record
        from minicode.action.seam import ActionExecutionSeam, SeamRequest

        class _CoverageFailureLocator:
            def locate(self, request):
                return LocateResult(
                    error_code=ErrorCode.TARGET_NOT_FOUND,
                    error_message="控件树里找不到这个目标",
                    allows_visual_fallback=True,
                )

        class _AllowingAdmission:
            def admit(self):
                return VisualAdmission(available=True, reason="测试替身：放行")

        model = _StubModel(_reply(390.625, 500.0), _reply(9000.0, 9000.0))
        sink = ListTraceSink()
        seam = ActionExecutionSeam(
            capture_sources=[FakeCaptureSource(2560, 1600)],
            tree=FakeControlTree(),
            injector=FakeInputInjector(),
            window_controller=FakeWindowController(),
            trace_sink=sink,
            locator=_CoverageFailureLocator(),
            visual_admission=_AllowingAdmission(),
            visual_locator=_grounder(model),
        )
        result = seam.run(SeamRequest(target_description="开始"))
        self.assertTrue(result.ok, msg=result.result.error_message)
        trace = sink.traces[0]
        self.assertTrue(trace.fallback)
        self.assertTrue(trace.fallback_reason)
        payload = build_record(trace).to_dict()
        self.assertTrue(payload["fallback"])
        self.assertTrue(payload["fallback_reason"])

    def test_crop_failure_reports_locate_failed_instead_of_crashing(self):
        """裁剪计划/裁剪本身失败 → **报定位失败**，不抛穿定位层。"""
        model = _StubModel(_reply(390.625, 500.0))
        grounder = _grounder(model)
        frame = _detail_frame(2560, 1600)
        # 表面比整屏小很多：裁剪窗口会越过这一帧 → 裁剪失败。
        result = grounder.locate(
            VisualLocateRequest(
                target_description="开始",
                frame=frame,
                surface=(0, 0, 100, 100),
            )
        )
        self.assertFalse(result.ok)
        self.assertEqual(result.error_code, ErrorCode.LOCATE_FAILED)
        self.assertIsNone(result.landing_point)

    def _locate(self, *outcomes):
        model = _StubModel(*outcomes)
        grounder = _grounder(model)
        frame = _detail_frame(2560, 1600)
        return grounder.locate(
            VisualLocateRequest(
                target_description="开始",
                frame=frame,
                surface=(0, 0, 2560, 1600),
            )
        )

    def test_refine_without_output_falls_back_to_coarse(self):
        result = self._locate(_reply(390.625, 500.0), _text_reply("我看不清那个按钮"))
        self.assertTrue(result.fallback)
        self.assertTrue(result.fallback_reason)
        self.assertIsNotNone(result.landing_point)
        self.assertAlmostEqual(result.landing_point[0], result.coarse_point[0], places=6)

    def test_refine_out_of_bounds_falls_back_to_coarse(self):
        result = self._locate(_reply(390.625, 500.0), _reply(9000.0, 9000.0))
        self.assertTrue(result.fallback)
        self.assertIsNotNone(result.landing_point)
        self.assertAlmostEqual(result.landing_point[0], result.coarse_point[0], places=6)

    def test_refine_call_failure_falls_back_to_coarse(self):
        result = self._locate(
            _reply(390.625, 500.0),
            NoTextOutputError("思考链吃光了 token 预算", usage={}),
        )
        self.assertTrue(result.fallback)
        self.assertIsNotNone(result.landing_point)

    def test_coarse_failure_reports_locate_failed_without_landing_point(self):
        result = self._locate(_text_reply("我不知道那个按钮在哪"))
        self.assertFalse(result.ok)
        self.assertEqual(result.error_code, ErrorCode.LOCATE_FAILED)
        self.assertIsNone(result.landing_point)
        self.assertFalse(result.fallback)

    def test_coarse_out_of_bounds_reports_locate_failed(self):
        result = self._locate(_reply(5000.0, 5000.0))
        self.assertFalse(result.ok)
        self.assertEqual(result.error_code, ErrorCode.LOCATE_FAILED)
        self.assertIsNone(result.landing_point)


class EchoTests(unittest.TestCase):
    """AC 5 · 每步回显：模型发出的坐标 + 实际发送尺寸。"""

    def test_echo_carries_model_coordinates_and_actual_sent_size(self):
        model = _StubModel(_reply(390.625, 500.0), _reply(123.0, 45.0))
        # 服务端把整屏图缩到 2048×1280（模型实际看到的是这张）。
        grounder = _grounder(model, model_input_size=(2048, 1280))
        frame = _detail_frame(2560, 1600)
        result = grounder.locate(
            VisualLocateRequest(
                target_description="开始",
                frame=frame,
                surface=(0, 0, 2560, 1600),
            )
        )
        stages = {echo.stage: echo for echo in result.echoes}
        self.assertEqual(set(stages), {"coarse", "refine"})
        self.assertEqual((stages["coarse"].raw_x, stages["coarse"].raw_y), (390.625, 500.0))
        self.assertEqual((stages["refine"].raw_x, stages["refine"].raw_y), (123.0, 45.0))
        # 实际发送尺寸 = **模型实际看到的那张图**（不是原图宽）。
        self.assertEqual((stages["coarse"].sent_width, stages["coarse"].sent_height), (2048, 1280))
        self.assertEqual((stages["refine"].sent_width, stages["refine"].sent_height), (512, 512))
        # 图像来源可判定（裁剪取自原始物理分辨率图 / 整屏抓图）。
        self.assertIn("crop", stages["refine"].image_source)
        self.assertNotEqual(stages["refine"].image_source, stages["coarse"].image_source)

    def test_echo_dict_shape(self):
        echo = GroundingEcho(
            stage="refine",
            raw_x=1.0,
            raw_y=2.0,
            sent_width=512,
            sent_height=512,
            image_source="crop-from-capture|fake",
        )
        self.assertEqual(
            echo.to_dict(),
            {
                "stage": "refine",
                "raw_x": 1.0,
                "raw_y": 2.0,
                "sent_width": 512,
                "sent_height": 512,
                "image_source": "crop-from-capture|fake",
            },
        )


class ZoomTests(unittest.TestCase):
    """AC 6 · 放大动作与裁剪不是一回事。"""

    def test_zoom_does_not_use_a_crop_plan(self):
        model = _StubModel(_reply(1000.0, 500.0))
        region_frame = _detail_frame(200, 200, left=300, top=400)
        result = zoom_locate(
            model,
            ZoomRequest(
                target_description="开始",
                region=(300, 400, 500, 600),
                region_frame=region_frame,
                full_screen_size=(2560, 1600),
                surface=(0, 0, 2560, 1600),
            ),
            scale=ModelScale.RELATIVE_0_1000,
            model_input_size=(2560, 1600),
            effort=Effort.LOW,
        )
        self.assertTrue(result.ok)
        # 放大路径**不产生裁剪计划**。
        self.assertIsNone(result.crop_plan)
        # 模型回答的是**整屏坐标**：直接按整屏换算，不再加任何裁剪原点偏移。
        self.assertEqual(result.landing_point, (2560, 800))
        self.assertEqual(result.echo.stage, "zoom")
        self.assertEqual((result.echo.sent_width, result.echo.sent_height), (200, 200))

    def test_zoom_prompt_states_full_screen_coordinates_and_no_crop_offset(self):
        from minicode.ground.prompt import zoom_prompt

        prompt = zoom_prompt("开始", origin_in_model_space=(150.0, 200.0))
        self.assertIn("整屏", prompt)
        self.assertNotIn("裁剪", prompt)
        self.assertNotIn("300, 400", prompt)


class DenominatorTests(unittest.TestCase):
    """AC 7 · 分母来自标度：换算链路不出现写死的 1000/999，也不按数值大小猜量纲。"""

    def test_ground_package_has_no_hardcoded_denominators(self):
        offenders: list[str] = []
        for path in sorted(_GROUND_DIR.glob("*.py")):
            if path.name.startswith("test_"):
                continue
            text = _strip_comments_and_strings(path.read_text(encoding="utf-8"))
            for literal in ("1000", "999"):
                if literal in text:
                    offenders.append(f"{path.name}: {literal}")
        self.assertEqual(offenders, [])

    def test_no_branch_guessing_scale_by_magnitude(self):
        offenders: list[str] = []
        for path in sorted(_GROUND_DIR.glob("*.py")):
            if path.name.startswith("test_"):
                continue
            text = _strip_comments_and_strings(path.read_text(encoding="utf-8"))
            for pattern in (r">\s*1000", r">=\s*999", r">\s*999"):
                if re.search(pattern, text):
                    offenders.append(f"{path.name}: {pattern}")
        self.assertEqual(offenders, [])

    def test_scale_must_come_from_calibration_explicitly(self):
        """换算适配器没有默认标度：通道必须显式给出标度。"""
        with self.assertRaises(TypeError):
            TwoStageGrounder(_StubModel(_reply(1.0, 1.0)))  # type: ignore[call-arg]

    def test_953_is_not_treated_as_absolute_pixels(self):
        """喂 0~1000 的 953 时，换算按 0~1000 走（不按数值大小改判量纲）。"""
        model = _StubModel(_reply(390.625, 500.0), _reply(953.0, 953.0))
        grounder = _grounder(model)
        frame = _detail_frame(2560, 1600)
        result = grounder.locate(
            VisualLocateRequest(
                target_description="开始",
                frame=frame,
                surface=(0, 0, 2560, 1600),
            )
        )
        plan = result.crop_plan
        # 按 0~1000 换算：crop 内 953 → 物理 = 原点 + 953/1000 × 窗口宽。
        expected_x = round(plan["origin_x"] + 953.0 / 1000.0 * plan["width"])
        self.assertEqual(result.landing_point[0], expected_x)
        # 不能被改判成绝对像素（那会是「原点 + 953 物理像素」）。
        self.assertNotEqual(result.landing_point[0], round(plan["origin_x"] + 953.0))


class TraceTests(unittest.TestCase):
    """AC 8 · 追踪记录可判定：粗定位点 / 裁剪计划 / 最终落点 / 实际发送尺寸 / 图像来源。"""

    def test_result_carries_the_four_judgement_fields(self):
        model = _StubModel(_reply(390.625, 500.0), _reply(200.0, 300.0))
        grounder = _grounder(model)
        frame = _detail_frame(2560, 1600)
        result = grounder.locate(
            VisualLocateRequest(
                target_description="开始",
                frame=frame,
                surface=(0, 0, 2560, 1600),
            )
        )
        self.assertIsNotNone(result.coarse_point)
        self.assertIsNotNone(result.crop_plan)
        self.assertIsNotNone(result.landing_point)
        self.assertTrue(result.echoes)
        for echo in result.echoes:
            self.assertIsNotNone(echo.sent_width)
            self.assertTrue(echo.image_source)

    def test_seam_trace_carries_grounding_fields(self):
        """缝里的追踪记录能读到视觉通道的观测数据（旁路出口，不进主返回类型）。"""
        from minicode.action.fakes import (
            FakeCaptureSource,
            FakeControlTree,
            FakeInputInjector,
            FakeWindowController,
        )
        from minicode.action.ports import LocateResult, VisualAdmission
        from minicode.action.records import ListTraceSink
        from minicode.action.seam import ActionExecutionSeam, SeamRequest

        class _CoverageFailureLocator:
            """UIA 通道的覆盖失败替身（候选集为空 → 允许切视觉）。"""

            def locate(self, request):
                return LocateResult(
                    error_code=ErrorCode.TARGET_NOT_FOUND,
                    error_message="控件树里找不到这个目标",
                    allows_visual_fallback=True,
                    query_scope="window-anchored",
                )

        class _AllowingAdmission:
            def admit(self):
                return VisualAdmission(available=True, reason="测试替身：放行")

        model = _StubModel(_reply(390.625, 500.0), _reply(200.0, 300.0))
        grounder = _grounder(model)
        sink = ListTraceSink()
        seam = ActionExecutionSeam(
            capture_sources=[FakeCaptureSource(2560, 1600)],
            tree=FakeControlTree(),
            injector=FakeInputInjector(),
            window_controller=FakeWindowController(),
            trace_sink=sink,
            locator=_CoverageFailureLocator(),
            visual_admission=_AllowingAdmission(),
            visual_locator=grounder,
        )
        result = seam.run(SeamRequest(target_description="开始"))
        self.assertTrue(result.ok, msg=result.result.error_message)
        self.assertEqual(len(sink.traces), 1)
        trace = sink.traces[0]
        self.assertIsNotNone(trace.coarse_point)
        self.assertIsNotNone(trace.crop_plan)
        self.assertIsNotNone(trace.landing_point)
        self.assertTrue(trace.grounding_echoes)
        self.assertIsNotNone(trace.image_source)

    def test_visual_channel_is_unreachable_without_the_admission_gate(self):
        """**没接判定端口 = 闸门没开**（fail-closed）：不许绕过硬闸门走视觉。"""
        from minicode.action.fakes import (
            FakeCaptureSource,
            FakeControlTree,
            FakeInputInjector,
            FakeWindowController,
        )
        from minicode.action.ports import LocateResult
        from minicode.action.seam import ActionExecutionSeam, SeamRequest

        class _CoverageFailureLocator:
            def locate(self, request):
                return LocateResult(
                    error_code=ErrorCode.TARGET_NOT_FOUND,
                    error_message="控件树里找不到这个目标",
                    allows_visual_fallback=True,
                )

        model = _StubModel(_reply(390.625, 500.0), _reply(200.0, 300.0))
        seam = ActionExecutionSeam(
            capture_sources=[FakeCaptureSource(2560, 1600)],
            tree=FakeControlTree(),
            injector=FakeInputInjector(),
            window_controller=FakeWindowController(),
            locator=_CoverageFailureLocator(),
            visual_locator=_grounder(model),
        )
        result = seam.run(SeamRequest(target_description="开始"))
        self.assertFalse(result.ok)
        self.assertEqual(result.result.error_code, ErrorCode.TARGET_NOT_FOUND)
        self.assertEqual(len(model.calls), 0)


class DistributionTests(unittest.TestCase):
    """AC 9 · 分布判据：精定位那一级的分布不许劣于粗定位。"""

    def test_summary_reports_median_p90_and_max(self):
        summary = summarize_errors([1.0, 2.0, 3.0, 4.0, 100.0])
        self.assertEqual(summary.count, 5)
        self.assertEqual(summary.median, 3.0)
        self.assertEqual(summary.maximum, 100.0)
        self.assertGreater(summary.p90, summary.median)

    def test_not_worse_compares_distributions(self):
        coarse = summarize_errors([10.0, 12.0, 14.0, 16.0, 18.0])
        better = summarize_errors([1.0, 2.0, 3.0, 4.0, 5.0])
        worse = summarize_errors([20.0, 22.0, 24.0, 26.0, 28.0])
        self.assertTrue(not_worse_than(better, coarse))
        self.assertFalse(not_worse_than(worse, coarse))


class DependencyTests(unittest.TestCase):
    """只依赖标准库（工单 AC 7 的字面要求之外，核心路径不许引第三方包）。"""

    def test_ground_package_has_no_third_party_imports(self):
        forbidden = ("PIL", "numpy", "requests", "httpx", "mss", "cv2")
        offenders: list[str] = []
        for path in sorted(_GROUND_DIR.glob("*.py")):
            text = path.read_text(encoding="utf-8")
            for name in forbidden:
                if re.search(rf"^\s*(import|from)\s+{name}\b", text, re.MULTILINE):
                    offenders.append(f"{path.name}: {name}")
        self.assertEqual(offenders, [])


def _strip_comments_and_strings(text: str) -> str:
    """去掉注释与字符串字面量（源码级检查只看真代码）。"""
    text = re.sub(r"\"\"\"(?:.|\n)*?\"\"\"", "", text)
    text = re.sub(r"'''(?:.|\n)*?'''", "", text)
    text = re.sub(r"#.*", "", text)
    text = re.sub(r"\"(?:\\.|[^\"\\])*\"", '""', text)
    text = re.sub(r"'(?:\\.|[^'\\])*'", "''", text)
    return text


def _plan(*, origin_x: int, origin_y: int, width: int, height: int, surface):
    from minicode.coord.crop_refine import CropPlan

    return CropPlan(
        origin_x=origin_x,
        origin_y=origin_y,
        width=width,
        height=height,
        surface_left=surface[0],
        surface_top=surface[1],
        surface_right=surface[2],
        surface_bottom=surface[3],
    )


if __name__ == "__main__":
    unittest.main()


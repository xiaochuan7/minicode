"""坐标换算与运行时标定的单元测试（标准库 unittest，无需 pytest）。

一条命令跑完::

    python -m unittest discover -s . -p "test_*.py" -v
    # 或
    python test_coordinates.py

覆盖范围
--------
* 标度换算往返（0~1 / 0~999 / 0~1000 / 截图像素绝对 / 模型输入像素绝对）
* DPI 1.5× 场景（物理 ↔ 逻辑混用的正确路径与残留误差）
* 0~999 与 0~1000 分母差异（两者都能还原同一物理点，但彼此有可量化的分歧）
* crop 边界三种情况（居中 / 贴边 shift / 表面小于 crop 时 shrink）
* 越界报错（模型输出越界 + 物理坐标越界）与 safe_area 校验
* 运行时标定：桩模型用每种标度时都能被正确识别
* 两级 grounding 全链路：粗定位 → crop → 精定位 → 全屏物理坐标
"""

from __future__ import annotations

import math
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from calibrate import (  # noqa: E402
    CalibrationProbe,
    CallableGroundModel,
    GroundingResult,
    ProbeTarget,
    RawPoint,
    StubGroundModel,
    build_probe_image,
    default_targets,
)
from coordinates import (  # noqa: E402
    CoordinateAdapter,
    CoordinateOutOfBounds,
    FrameGeometry,
    ModelScale,
    ModelScaleError,
    Rect,
    SafeAreaViolation,
)
from crop_refine import CropRefiner, plan_crop  # noqa: E402

# ---------------------------------------------------------------------------
# 公共夹具
# ---------------------------------------------------------------------------

SURFACE_W = 2560
SURFACE_H = 1600
CROP = 512


def physical_frame(
    *,
    capture_width: int = SURFACE_W,
    capture_height: int = SURFACE_H,
    model_input_width: int = None,
    model_input_height: int = None,
    capture_to_physical_scale: float = 1.0,
    virtual_origin_x: int = 0,
    virtual_origin_y: int = 0,
    surface_width: int = SURFACE_W,
    surface_height: int = SURFACE_H,
) -> FrameGeometry:
    return FrameGeometry(
        capture_width=capture_width,
        capture_height=capture_height,
        model_input_width=model_input_width,
        model_input_height=model_input_height,
        capture_to_physical_scale=capture_to_physical_scale,
        virtual_origin_x=virtual_origin_x,
        virtual_origin_y=virtual_origin_y,
        surface_width=surface_width,
        surface_height=surface_height,
    )


# ---------------------------------------------------------------------------


class TestScaleRoundTrip(unittest.TestCase):
    """各种标度下：物理 → 模型 → 物理 必须精确往返。"""

    def _roundtrip(self, scale: ModelScale, frame: FrameGeometry, px: float, py: float) -> tuple:
        adapter = CoordinateAdapter(scale)
        mx, my = adapter.from_screen(px, py, frame)
        back = adapter.to_screen(mx, my, frame)
        return back

    def test_relative_0_1_roundtrip(self):
        frame = physical_frame()
        px, py = self._roundtrip(ModelScale.RELATIVE_0_1, frame, 1234.5, 678.25)
        self.assertAlmostEqual(px, 1234.5, places=6)
        self.assertAlmostEqual(py, 678.25, places=6)

    def test_relative_0_999_roundtrip(self):
        frame = physical_frame()
        px, py = self._roundtrip(ModelScale.RELATIVE_0_999, frame, 1234.5, 678.25)
        self.assertAlmostEqual(px, 1234.5, places=6)
        self.assertAlmostEqual(py, 678.25, places=6)

    def test_relative_0_1000_roundtrip(self):
        frame = physical_frame()
        px, py = self._roundtrip(ModelScale.RELATIVE_0_1000, frame, 1234.5, 678.25)
        self.assertAlmostEqual(px, 1234.5, places=6)
        self.assertAlmostEqual(py, 678.25, places=6)

    def test_absolute_capture_roundtrip(self):
        frame = physical_frame()
        px, py = self._roundtrip(ModelScale.ABSOLUTE_CAPTURE, frame, 1234.5, 678.25)
        self.assertAlmostEqual(px, 1234.5, places=6)
        self.assertAlmostEqual(py, 678.25, places=6)

    def test_absolute_model_input_roundtrip(self):
        # 模型输入被 smart_resize 缩到 1260x784
        frame = physical_frame(model_input_width=1260, model_input_height=784)
        px, py = self._roundtrip(ModelScale.ABSOLUTE_MODEL_INPUT, frame, 1234.5, 678.25)
        self.assertAlmostEqual(px, 1234.5, places=6)
        self.assertAlmostEqual(py, 678.25, places=6)

    def test_capture_model_input_layer_is_inverse(self):
        frame = physical_frame(model_input_width=1260, model_input_height=784)
        adapter = CoordinateAdapter(ModelScale.ABSOLUTE_MODEL_INPUT)
        mx, my = adapter.capture_to_model_input(2560.0, 1600.0, frame)
        self.assertAlmostEqual(mx, 1260.0, places=6)
        self.assertAlmostEqual(my, 784.0, places=6)
        cx, cy = adapter.model_input_to_capture(mx, my, frame)
        self.assertAlmostEqual(cx, 2560.0, places=6)
        self.assertAlmostEqual(cy, 1600.0, places=6)

    def test_relative_scale_values_are_as_expected(self):
        frame = physical_frame()
        adapter = CoordinateAdapter(ModelScale.RELATIVE_0_1000)
        # 物理 2000px / 2560 宽 => 781.25 个单位
        mx, my = adapter.from_screen(2000.0, 800.0, frame)
        self.assertAlmostEqual(mx, 781.25, places=6)
        self.assertAlmostEqual(my, 500.0, places=6)

    def test_scale_parse(self):
        self.assertIs(ModelScale.parse("0-1000"), ModelScale.RELATIVE_0_1000)
        self.assertIs(ModelScale.parse("1000"), ModelScale.RELATIVE_0_1000)
        self.assertIs(ModelScale.parse("0_999"), ModelScale.RELATIVE_0_999)
        self.assertIs(ModelScale.parse("relative_0_1"), ModelScale.RELATIVE_0_1)
        self.assertIs(ModelScale.parse("absolute_capture"), ModelScale.ABSOLUTE_CAPTURE)
        with self.assertRaises(ModelScaleError):
            ModelScale.parse("banana")

    def test_absolute_model_input_without_model_input_size_uses_capture(self):
        frame = physical_frame()
        adapter = CoordinateAdapter(ModelScale.ABSOLUTE_MODEL_INPUT)
        px, py = self._roundtrip(ModelScale.ABSOLUTE_MODEL_INPUT, frame, 100.0, 200.0)
        self.assertAlmostEqual(px, 100.0, places=6)
        self.assertAlmostEqual(py, 200.0, places=6)


class TestDpiScenario(unittest.TestCase):
    """Windows 150% DPI：截图可能是逻辑像素，点击必须是物理像素。"""

    def test_exact_dpi_2x(self):
        # 截图 1280x800 逻辑，scale=2.0 => 物理 2560x1600
        frame = physical_frame(
            capture_width=1280,
            capture_height=800,
            capture_to_physical_scale=2.0,
        )
        adapter = CoordinateAdapter(ModelScale.RELATIVE_0_1000)
        rel_x, rel_y = adapter.from_screen(2000.0, 1200.0, frame)
        px, py = adapter.to_screen(rel_x, rel_y, frame)
        self.assertAlmostEqual(px, 2000.0, places=6)
        self.assertAlmostEqual(py, 1200.0, places=6)

    def test_realistic_dpi_1_5x(self):
        # 目标机：物理 2560x1600，逻辑 1707x1067（2560/1.5 = 1706.67 被取整）
        frame = physical_frame(
            capture_width=1707,
            capture_height=1067,
            capture_to_physical_scale=1.5,
        )
        adapter = CoordinateAdapter(ModelScale.RELATIVE_0_1000)
        rel_x, rel_y = adapter.from_screen(2000.0, 1200.0, frame)
        px, py = adapter.to_screen(rel_x, rel_y, frame)
        # 残差来自逻辑宽 1707 vs 精确 1706.67 的取整，应 < 1 物理 px
        self.assertLess(abs(px - 2000.0), 1.0)
        self.assertLess(abs(py - 1200.0), 1.0)

    def test_dpi_1_5x_wrong_way_diverges_hugely(self):
        """反例：截图/点击两侧对 DPI 的理解不一致 => 偏差数百 px。

        两种真实翻车姿势：
        A. 截图是**物理**的，反归一化却用了**逻辑**宽高（1707）。
        B. 截图是**逻辑**的，反归一化却用了**物理**宽高（2560）。
        """
        physical_w, physical_h = 2560, 1600
        logical_w, logical_h = 1707, 1067
        true_x, true_y = 2000.0, 1200.0

        # A：模型按物理截图给相对坐标，harness 却按逻辑宽高反归一化
        rel_x = true_x / physical_w * 1000.0
        wrong_a_x = rel_x / 1000.0 * logical_w
        self.assertGreater(abs(wrong_a_x - true_x), 500.0)

        # B：模型按逻辑截图给相对坐标，harness 却按物理宽高反归一化
        rel_x_b = true_x / logical_w * 1000.0
        wrong_b_x = rel_x_b / 1000.0 * physical_w
        self.assertGreater(abs(wrong_b_x - true_x), 500.0)

    def test_dpi_aware_capture_needs_no_scale_factor(self):
        """PMv2 感知路径：截图就是物理像素，scale 必须恒为 1.0。"""
        frame = physical_frame(capture_to_physical_scale=1.0)
        self.assertAlmostEqual(frame.physical_width(), 2560.0)
        self.assertAlmostEqual(frame.physical_height(), 1600.0)

    def test_multi_monitor_negative_origin(self):
        """副屏在主屏左侧：虚拟桌面原点为负。"""
        frame = physical_frame(
            capture_width=SURFACE_W,
            capture_height=SURFACE_H,
            virtual_origin_x=-1920,
            virtual_origin_y=0,
            surface_width=4480,
            surface_height=1600,
        )
        adapter = CoordinateAdapter(ModelScale.RELATIVE_0_1000)
        rel_x, rel_y = adapter.from_screen(-1920.0 + 100.0, 200.0, frame)
        px, py = adapter.to_screen(rel_x, rel_y, frame)
        self.assertAlmostEqual(px, -1820.0, places=6)
        self.assertAlmostEqual(py, 200.0, places=6)


class TestDenominatorDifference(unittest.TestCase):
    """0~999 与 0~1000 的分母差异。"""

    def test_both_denominators_restore_same_physical_point(self):
        frame = physical_frame()
        a1000 = CoordinateAdapter(ModelScale.RELATIVE_0_1000)
        a999 = CoordinateAdapter(ModelScale.RELATIVE_0_999)
        target = (2440.0, 900.0)
        v1000 = a1000.from_screen(*target, frame)
        v999 = a999.from_screen(*target, frame)
        self.assertNotAlmostEqual(v1000[0], v999[0], places=3)
        back1000 = a1000.to_screen(*v1000, frame)
        back999 = a999.to_screen(*v999, frame)
        self.assertAlmostEqual(back1000[0], target[0], places=6)
        self.assertAlmostEqual(back999[0], target[0], places=6)

    def test_denominator_misread_divergence_at_right_edge(self):
        """把 0~1000 的数值当 0~999 读，在靠右的目标上会偏好几 px。"""
        frame = physical_frame()
        a1000 = CoordinateAdapter(ModelScale.RELATIVE_0_1000)
        a999 = CoordinateAdapter(ModelScale.RELATIVE_0_999)
        raw_x, raw_y = a1000.from_screen(2440.0, 100.0, frame)
        p1000 = a1000.to_screen(raw_x, raw_y, frame)
        p999 = a999.to_screen(raw_x, raw_y, frame)
        divergence = math.hypot(p1000[0] - p999[0], p1000[1] - p999[1])
        # 在 2560 宽的探针上，靠右目标的分歧 > 2px 容差，必须区分
        self.assertGreater(divergence, 2.0)

    def test_denominator_misread_divergence_is_small_on_narrow_probe(self):
        """对照：探针只有 1280 宽时，同样的分歧 < 2px，选谁都不影响落点。"""
        frame = physical_frame(capture_width=1280, capture_height=720, surface_width=1280, surface_height=720)
        a1000 = CoordinateAdapter(ModelScale.RELATIVE_0_1000)
        a999 = CoordinateAdapter(ModelScale.RELATIVE_0_999)
        raw_x, raw_y = a1000.from_screen(1160.0, 50.0, frame)
        p1000 = a1000.to_screen(raw_x, raw_y, frame)
        p999 = a999.to_screen(raw_x, raw_y, frame)
        divergence = math.hypot(p1000[0] - p999[0], p1000[1] - p999[1])
        self.assertLess(divergence, 2.0)


class TestOutOfBounds(unittest.TestCase):
    """越界不 clamp，直接报错，容差 2px。"""

    def test_model_output_out_of_range_raises(self):
        frame = physical_frame()
        adapter = CoordinateAdapter(ModelScale.RELATIVE_0_1000)
        with self.assertRaises(CoordinateOutOfBounds):
            adapter.to_screen(1200.0, 500.0, frame)
        with self.assertRaises(CoordinateOutOfBounds):
            adapter.to_screen(500.0, -30.0, frame)

    def test_model_output_within_tolerance_is_accepted(self):
        frame = physical_frame()
        adapter = CoordinateAdapter(ModelScale.RELATIVE_0_1000)
        # 1000 单位正好是右边界，容差 2px 内应通过
        px, py = adapter.to_screen(1000.0, 1000.0, frame)
        self.assertAlmostEqual(px, 2560.0, places=6)
        self.assertAlmostEqual(py, 1600.0, places=6)

    def test_absolute_scale_out_of_range_raises(self):
        frame = physical_frame()
        adapter = CoordinateAdapter(ModelScale.ABSOLUTE_CAPTURE)
        with self.assertRaises(CoordinateOutOfBounds):
            adapter.to_screen(5000.0, 100.0, frame)

    def test_physical_point_outside_surface_raises(self):
        # 截图比表面大（截到了桌面之外的部分）=> 换算后越界
        frame = physical_frame(
            capture_width=SURFACE_W,
            capture_height=SURFACE_H,
            surface_width=2000,
            surface_height=1200,
        )
        adapter = CoordinateAdapter(ModelScale.RELATIVE_0_1000)
        with self.assertRaises(CoordinateOutOfBounds):
            adapter.to_screen(900.0, 900.0, frame)

    def test_no_clamping_happens(self):
        """越界时不能偷偷把坐标拉回边界。"""
        frame = physical_frame()
        adapter = CoordinateAdapter(ModelScale.RELATIVE_0_1000)
        raised = False
        try:
            adapter.to_screen(5000.0, 500.0, frame)
        except CoordinateOutOfBounds as exc:
            raised = True
            self.assertIn("5000", str(exc))
        self.assertTrue(raised, "越界必须抛异常而不是 clamp")

    def test_safe_area_violation_raises(self):
        frame = physical_frame()
        adapter = CoordinateAdapter(ModelScale.RELATIVE_0_1000)
        safe = Rect(0, 0, 1000, 1000)
        # 物理 (2000, 200) 在 safe_area 之外
        with self.assertRaises(SafeAreaViolation):
            adapter.to_screen(781.25, 125.0, frame, safe_area=safe)
        # 物理 (500, 200) 在 safe_area 之内
        px, py = adapter.to_screen(195.3125, 125.0, frame, safe_area=safe)
        self.assertAlmostEqual(px, 500.0, places=3)

    def test_in_safe_area_returns_bool(self):
        frame = physical_frame()
        adapter = CoordinateAdapter(ModelScale.RELATIVE_0_1000)
        safe = Rect(0, 0, 1000, 1000)
        self.assertTrue(adapter.in_safe_area(500.0, 500.0, safe))
        self.assertFalse(adapter.in_safe_area(1500.0, 500.0, safe))


class TestCropBoundary(unittest.TestCase):
    """crop 边界三种情况：居中 / 贴边 shift / 表面小于 crop 时 shrink。"""

    def setUp(self):
        self.surface = Rect(0, 0, SURFACE_W, SURFACE_H)

    def test_center_no_shift(self):
        plan = plan_crop(1280.0, 800.0, self.surface, crop_width=CROP, crop_height=CROP)
        self.assertEqual(plan.origin_x, 1280 - CROP // 2)
        self.assertEqual(plan.origin_y, 800 - CROP // 2)
        self.assertEqual((plan.width, plan.height), (CROP, CROP))
        self.assertFalse(plan.shifted)
        self.assertFalse(plan.shrunk)

    def test_left_top_edge_shifts_right_down(self):
        plan = plan_crop(10.0, 12.0, self.surface, crop_width=CROP, crop_height=CROP)
        self.assertTrue(plan.shifted)
        self.assertFalse(plan.shrunk)
        self.assertEqual(plan.origin_x, 0)
        self.assertEqual(plan.origin_y, 0)
        self.assertEqual((plan.width, plan.height), (CROP, CROP))
        # 粗定位点必须仍在窗口内
        self.assertTrue(plan.origin_x <= 10 <= plan.right)
        self.assertTrue(plan.origin_y <= 12 <= plan.bottom)

    def test_right_bottom_edge_shifts_left_up(self):
        plan = plan_crop(SURFACE_W - 5.0, SURFACE_H - 7.0, self.surface, crop_width=CROP, crop_height=CROP)
        self.assertTrue(plan.shifted)
        self.assertEqual(plan.right, SURFACE_W)
        self.assertEqual(plan.bottom, SURFACE_H)
        self.assertTrue(plan.origin_x <= SURFACE_W - 5.0 <= plan.right)
        self.assertTrue(plan.origin_y <= SURFACE_H - 7.0 <= plan.bottom)

    def test_surface_smaller_than_crop_shrinks(self):
        small = Rect(0, 0, 300, 200)
        plan = plan_crop(150.0, 100.0, small, crop_width=CROP, crop_height=CROP)
        self.assertTrue(plan.shrunk)
        self.assertEqual((plan.width, plan.height), (300, 200))
        self.assertEqual((plan.origin_x, plan.origin_y), (0, 0))
        self.assertTrue(plan.contains_physical(150.0, 100.0))

    def test_target_radius_keeps_visible_target_contained(self):
        """目标一部分已在屏外：crop 至少要完整盖住目标的**可见部分**。"""
        plan = plan_crop(
            5.0, 5.0, self.surface, crop_width=CROP, crop_height=CROP, target_radius_px=20.0
        )
        # 目标物理跨度 [-15, 25]，屏外部分不可见；可见部分 [0, 25] 必须在 crop 内
        self.assertLessEqual(plan.origin_x, 0.0)
        self.assertGreaterEqual(plan.right, 25.0)
        # 目标整体确实无法完全包含（它在屏外）
        self.assertFalse(plan.target_fully_contained)

    def test_target_radius_shifts_crop_to_contain_onscreen_target(self):
        """目标在屏内但贴近左边缘：crop 必须右移，把目标整个包住。"""
        plan = plan_crop(
            30.0, 800.0, self.surface, crop_width=CROP, crop_height=CROP, target_radius_px=20.0
        )
        self.assertTrue(plan.shifted)
        self.assertTrue(plan.target_fully_contained)
        self.assertLessEqual(plan.origin_x, 30.0 - 20.0)
        self.assertGreaterEqual(plan.right, 30.0 + 20.0)

    def test_center_outside_surface_raises(self):
        from coordinates import CoordinateError

        with self.assertRaises(CoordinateError):
            plan_crop(-100.0, 800.0, self.surface, crop_width=CROP, crop_height=CROP)

    def test_crop_pixel_to_physical_and_back(self):
        plan = plan_crop(1280.0, 800.0, self.surface, crop_width=CROP, crop_height=CROP)
        from crop_refine import CropRefiner

        px, py = CropRefiner.crop_to_physical(10.0, 20.0, plan)
        self.assertAlmostEqual(px, plan.origin_x + 10.0)
        self.assertAlmostEqual(py, plan.origin_y + 20.0)
        cx, cy = CropRefiner.physical_to_crop(px, py, plan)
        self.assertAlmostEqual(cx, 10.0)
        self.assertAlmostEqual(cy, 20.0)


class TestCalibration(unittest.TestCase):
    """运行时标定：桩模型用哪种标度，检测器就应判出哪种。"""

    def _probe(self, scale: ModelScale, **kwargs) -> CalibrationProbe:
        width = kwargs.pop("width", 640)
        height = kwargs.pop("height", 400)
        model_input = kwargs.pop("model_input", None)
        targets = default_targets(width, height, margin=32, size=24)
        miw = model_input[0] if model_input else width
        mih = model_input[1] if model_input else height
        stub = StubGroundModel(
            scale=scale,
            targets=targets,
            capture_width=width,
            capture_height=height,
            model_input_width=miw,
            model_input_height=mih,
            noise_px=kwargs.pop("noise_px", 0.0),
        )
        return CalibrationProbe(
            stub,
            width=width,
            height=height,
            targets=targets,
            model_input_width=miw,
            model_input_height=mih,
            **kwargs,
        )

    def test_detects_relative_0_1(self):
        result = self._probe(ModelScale.RELATIVE_0_1).run()
        self.assertIs(result.detected_scale, ModelScale.RELATIVE_0_1)
        self.assertTrue(result.confident)
        self.assertTrue(result.roundtrip_ok)

    def test_detects_relative_0_999(self):
        result = self._probe(ModelScale.RELATIVE_0_999).run()
        self.assertIs(result.detected_scale, ModelScale.RELATIVE_0_999)
        self.assertTrue(result.confident)

    def test_detects_relative_0_1000(self):
        result = self._probe(ModelScale.RELATIVE_0_1000).run()
        self.assertIs(result.detected_scale, ModelScale.RELATIVE_0_1000)
        self.assertTrue(result.confident)

    def test_detects_absolute_capture(self):
        result = self._probe(ModelScale.ABSOLUTE_CAPTURE).run()
        self.assertIs(result.detected_scale, ModelScale.ABSOLUTE_CAPTURE)
        self.assertTrue(result.confident)

    def test_detects_absolute_model_input(self):
        """模型输入被缩放过时，绝对像素必须解释成"模型输入像素"而非"截图像素"。"""
        result = self._probe(
            ModelScale.ABSOLUTE_MODEL_INPUT, model_input=(315, 196)
        ).run()
        self.assertIs(result.detected_scale, ModelScale.ABSOLUTE_MODEL_INPUT)
        self.assertTrue(result.confident)

    def test_absolute_capture_and_model_input_equivalent_when_same_size(self):
        """模型输入尺寸 == 截图尺寸时，两种绝对标度无法区分，应报等价。"""
        result = self._probe(ModelScale.ABSOLUTE_CAPTURE).run()
        self.assertIn(ModelScale.ABSOLUTE_CAPTURE, result.equivalent_scales + (result.detected_scale,))
        self.assertIn(ModelScale.ABSOLUTE_MODEL_INPUT, result.equivalent_scales)

    def test_noise_within_tolerance_still_confident(self):
        result = self._probe(ModelScale.RELATIVE_0_1000, noise_px=0.3).run()
        self.assertIs(result.detected_scale, ModelScale.RELATIVE_0_1000)
        self.assertTrue(result.confident)

    def test_probe_image_is_valid_png(self):
        png = build_probe_image(64, 48, default_targets(64, 48, margin=8, size=8))
        self.assertTrue(png.startswith(b"\x89PNG\r\n\x1a\n"))
        self.assertIn(b"IEND", png)

    def test_denominator_estimate_matches_observed_divergence(self):
        """报告里印的"0~1000 vs 0~999 分歧"估算必须与实际观测一致。"""
        probe = self._probe(ModelScale.RELATIVE_0_1000, width=2560, height=1600)
        result = probe.run()
        observed = result.divergence_px[ModelScale.RELATIVE_0_999]
        estimate = probe.denominator_divergence_estimate()
        self.assertAlmostEqual(observed, estimate, delta=0.01)

    def test_denominator_estimate_below_tolerance_on_narrow_probe(self):
        """探针只有 1280 宽时，0~1000 与 0~999 的分歧 < 2px，选谁都不影响落点。

        这里显式传 `tolerance_px=2.0`，用来钉住"容差取 2px 时窄探针会静默判错"这一失败模式；
        默认容差是 1.0（见下一个测试）。
        """
        probe = self._probe(
            ModelScale.RELATIVE_0_1000, width=1280, height=720, tolerance_px=2.0
        )
        self.assertLess(probe.denominator_divergence_estimate(), 2.0)
        result = probe.run()
        self.assertIn(
            ModelScale.RELATIVE_0_999,
            result.equivalent_scales,
            "窄探针 + 2px 容差下 0~999 应被判为与 0~1000 等价",
        )

    def test_default_tolerance_separates_999_from_1000_on_full_width_probe(self):
        """默认容差 1.0px + 2560 宽探针，必须能把 0~999 与 0~1000 分开。

        这是架构规格 §1.4 的硬要求：容差取 2px 时中部目标分歧 1.28px 会被误判为"等价"，
        从而让标度判定静默失效。
        """
        probe = self._probe(ModelScale.RELATIVE_0_1000, width=2560, height=1600)
        self.assertLessEqual(probe.tolerance_px, 1.0)
        result = probe.run()
        self.assertNotIn(
            ModelScale.RELATIVE_0_999,
            result.equivalent_scales,
            "2560 宽探针 + 1px 容差下 0~999 不应被判为与 0~1000 等价",
        )
        self.assertTrue(result.confident)

    def test_narrow_probe_cannot_separate_999_from_1000(self):
        """640 宽的探针分不出 0~999 与 0~1000 —— 探针宽度必须够。"""
        result = self._probe(ModelScale.RELATIVE_0_1000, width=640, height=400).run()
        self.assertFalse(result.unique)
        self.assertIn(ModelScale.RELATIVE_0_999, result.equivalent_scales)

    def test_wide_probe_separates_999_from_1000(self):
        """2560 宽（= 目标机物理宽度）可以分开 0~999 与 0~1000。"""
        result = self._probe(ModelScale.RELATIVE_0_1000, width=2560, height=1600).run()
        self.assertTrue(result.unique)
        self.assertNotIn(ModelScale.RELATIVE_0_999, result.equivalent_scales)
        self.assertGreater(result.divergence_px[ModelScale.RELATIVE_0_999], 2.0)

    def test_callable_model_can_replace_stub(self):
        """CallableGroundModel：接真实模型时只需替换这一处。"""
        width, height = 320, 200
        targets = default_targets(width, height, margin=16, size=12)
        truth = {t.name: (t.x, t.y) for t in targets}

        def fake_api(image: bytes, instruction: str) -> GroundingResult:
            name = instruction.split()[2]
            x, y = truth[name]
            return GroundingResult(
                points=(RawPoint(x / width, y / height),),
                input_width=width,
                input_height=height,
                model_id="fake",
            )

        model = CallableGroundModel(fake_api, model_id="fake")
        probe = CalibrationProbe(
            model, width=width, height=height, targets=targets
        )
        result = probe.run()
        self.assertIs(result.detected_scale, ModelScale.RELATIVE_0_1)
        self.assertTrue(result.confident)


class TestTwoStageGrounding(unittest.TestCase):
    """两级 grounding 全链路：粗定位 → crop → 精定位 → 全屏物理坐标。"""

    def setUp(self):
        self.surface = Rect(0, 0, SURFACE_W, SURFACE_H)
        self.frame = physical_frame()
        self.adapter = CoordinateAdapter(ModelScale.RELATIVE_0_1000)
        self.refiner = CropRefiner(self.adapter, crop_width=CROP, crop_height=CROP)

    def test_end_to_end_physical_accuracy(self):
        true_x, true_y = 1832.0, 967.0
        # 一级：整屏粗定位（故意带 ±30px 误差）
        coarse_x, coarse_y = true_x + 18.0, true_y - 22.0
        # 二级：crop 内精定位（crop 内 0~1000，误差 0.2 单位 => 0.1 物理 px）
        plan = self.refiner.plan((coarse_x, coarse_y), self.surface)
        crop_rel_x = (true_x - plan.origin_x) / plan.width * 1000.0
        crop_rel_y = (true_y - plan.origin_y) / plan.height * 1000.0
        final_x, final_y = self.refiner.refine(
            (coarse_x, coarse_y), (crop_rel_x, crop_rel_y), self.surface
        )
        self.assertAlmostEqual(final_x, true_x, delta=0.2)
        self.assertAlmostEqual(final_y, true_y, delta=0.2)

    def test_crop_improves_quantization(self):
        """crop 512 后 1 个归一化单位 = 0.512 物理 px（整屏是 2.56）。"""
        frame = self.frame
        whole = CoordinateAdapter(ModelScale.RELATIVE_0_1000)
        rel_x, rel_y = whole.from_screen(1832.0, 967.0, frame)
        self.assertAlmostEqual(whole.to_screen(rel_x, rel_y, frame)[0], 1832.0, places=6)

        plan = self.refiner.plan((1832.0, 967.0), self.surface)
        crop_frame = self.refiner.crop_frame(plan)
        crop_rel_x, crop_rel_y = whole.from_screen(1832.0, 967.0, crop_frame)
        # 0.2 单位的误差 -> 0.2 * 512/1000 = 0.1024 物理 px
        perturbed = (crop_rel_x + 0.2, crop_rel_y - 0.2)
        final = whole.to_screen(*perturbed, crop_frame)
        self.assertAlmostEqual(final[0], 1832.0 + 0.2 * plan.width / 1000.0, places=6)

    def test_refine_outside_crop_raises(self):
        with self.assertRaises(CoordinateOutOfBounds):
            self.refiner.refine(
                (1280.0, 800.0), (1500.0, 500.0), self.surface
            )

    def test_crop_frame_origin_equals_plan_origin(self):
        plan = self.refiner.plan((100.0, 100.0), self.surface)
        frame = self.refiner.crop_frame(plan)
        self.assertEqual(frame.virtual_origin_x, plan.origin_x)
        self.assertEqual(frame.virtual_origin_y, plan.origin_y)
        self.assertEqual(frame.capture_width, plan.width)
        self.assertEqual(frame.capture_to_physical_scale, 1.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)

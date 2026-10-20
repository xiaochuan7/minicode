"""动作协议 v1 的形状与换算链路的单测。

只测外部行为：协议形状、错误码、换算入口的对外结果。
"""

from __future__ import annotations

import inspect
import unittest

from minicode.action.errors import ErrorCode
from minicode.action.geometry import SENTINEL_CURSOR, VirtualDesktop, is_sentinel
from minicode.action.protocol import (
    DEFERRED_KINDS,
    EXECUTABLE_KINDS,
    PROTOCOL_VERSION,
    Action,
    ActionKind,
    ActionResult,
)
from minicode.action.surface import SurfaceSpec
from minicode.coord.calibrate import CalibrationProbe
from minicode.coord.coordinates import DEFAULT_OUT_OF_BOUNDS_TOLERANCE_PX, CoordinateOutOfBounds


class ProtocolShapeTest(unittest.TestCase):
    """协议 v1 立住形状：版本号 / 动作种类 / 参数 / 结果 / 错误码。"""

    def test_protocol_version_is_v1(self):
        self.assertEqual(PROTOCOL_VERSION, "cua.action/1")

    def test_twelve_kinds_split_into_eight_executable_and_four_deferred(self):
        self.assertEqual(len(ActionKind), 12)
        self.assertEqual(EXECUTABLE_KINDS | DEFERRED_KINDS, set(ActionKind))
        self.assertEqual(EXECUTABLE_KINDS & DEFERRED_KINDS, set())
        self.assertEqual(len(EXECUTABLE_KINDS), 8)
        self.assertEqual(len(DEFERRED_KINDS), 4)

    def test_deferred_kinds_are_exactly_the_four_this_ticket_does_not_implement(self):
        self.assertEqual(
            DEFERRED_KINDS,
            {ActionKind.ZOOM, ActionKind.INSPECT, ActionKind.EXECUTE_CODE, ActionKind.ASK_USER},
        )

    def test_action_exposes_kind_params_and_intent(self):
        action = Action(kind=ActionKind.CLICK, params={"x": 1, "y": 2}, intent="点击「开始」")
        payload = action.to_dict()
        self.assertEqual(payload["kind"], "click")
        self.assertEqual(payload["params"], {"x": 1, "y": 2})
        self.assertEqual(payload["intent"], "点击「开始」")

    def test_action_result_carries_ok_and_error_code(self):
        failed = ActionResult(ok=False, error_code=ErrorCode.CLIPPED_BY_DESKTOP, error_message="越界")
        self.assertFalse(failed.ok)
        self.assertEqual(failed.error_code, ErrorCode.CLIPPED_BY_DESKTOP)
        succeeded = ActionResult(ok=True)
        self.assertTrue(succeeded.ok)
        self.assertIsNone(succeeded.error_code)

    def test_clipped_by_desktop_error_code_is_named_as_the_protocol_requires(self):
        self.assertEqual(ErrorCode.CLIPPED_BY_DESKTOP.value, "clipped_by_desktop")


class SentinelTest(unittest.TestCase):
    """哨兵值 (−32000,−32000) 不是合法坐标。"""

    def test_sentinel_value_is_recognised(self):
        self.assertTrue(is_sentinel(SENTINEL_CURSOR))
        self.assertEqual(SENTINEL_CURSOR, (-32000, -32000))

    def test_ordinary_coordinates_are_not_sentinel(self):
        self.assertFalse(is_sentinel((0, 0)))
        self.assertFalse(is_sentinel((-32001, 100)))
        self.assertFalse(is_sentinel((100, -32000)))


class SurfaceScaleTest(unittest.TestCase):
    """换算链路必须单一来源（验收项 3）。"""

    def _spec(self, scale: float) -> SurfaceSpec:
        return SurfaceSpec(
            virtual_desktop=VirtualDesktop(0, 0, 2560, 1600),
            capture_width=2560,
            capture_height=1600,
            capture_to_physical_scale=scale,
        )

    def test_scale_defaults_to_one(self):
        self.assertEqual(self._spec(1.0).capture_to_physical_scale, 1.0)

    def test_non_default_scale_logs_a_warning(self):
        with self.assertLogs("minicode.action.surface", level="WARNING"):
            self._spec(1.5)

    def test_every_capture_conversion_entry_point_moves_with_the_single_scale(self):
        one = self._spec(1.0)
        two = self._spec(2.0)
        self.assertNotEqual(one.capture_to_physical(100, 50), two.capture_to_physical(100, 50))
        self.assertNotEqual(one.physical_to_capture(100, 50), two.physical_to_capture(100, 50))
        self.assertNotEqual(one.normalized_to_physical(0.25, 0.5), two.normalized_to_physical(0.25, 0.5))
        self.assertNotEqual(one.physical_to_normalized(100, 50), two.physical_to_normalized(100, 50))

    def test_scale_change_scales_the_conversion_by_the_same_factor(self):
        one = self._spec(1.0)
        two = self._spec(2.0)
        self.assertEqual(
            two.capture_to_physical(100, 50),
            (one.capture_to_physical(100, 50)[0] * 2, one.capture_to_physical(100, 50)[1] * 2),
        )

    def test_round_trip_through_the_single_scale_is_stable(self):
        two = self._spec(2.0)
        px, py = two.capture_to_physical(100, 50)
        cx, cy = two.physical_to_capture(px, py)
        self.assertAlmostEqual(cx, 100, places=6)
        self.assertAlmostEqual(cy, 50, places=6)


class ToleranceIndependenceTest(unittest.TestCase):
    """动作的 2 px 越界容差与标定探针的 1 px 标定容差是两个独立常量。"""

    def test_action_tolerance_is_two_physical_pixels(self):
        self.assertEqual(DEFAULT_OUT_OF_BOUNDS_TOLERANCE_PX, 2.0)

    def test_calibration_probe_tolerance_is_one_physical_pixel(self):
        default = inspect.signature(CalibrationProbe.__init__).parameters["tolerance_px"].default
        self.assertEqual(default, 1.0)

    def test_the_two_tolerances_are_not_the_same_value(self):
        calibration = inspect.signature(CalibrationProbe.__init__).parameters["tolerance_px"].default
        self.assertNotEqual(DEFAULT_OUT_OF_BOUNDS_TOLERANCE_PX, calibration)


class SurfaceBoundsTest(unittest.TestCase):
    """越界不夹取（验收项 10）。"""

    def setUp(self):
        self.spec = SurfaceSpec(
            virtual_desktop=VirtualDesktop(0, 0, 2560, 1600),
            capture_width=2560,
            capture_height=1600,
        )

    def test_point_inside_the_desktop_is_accepted(self):
        self.spec.assert_within_surface(1280, 800)

    def test_point_up_to_two_pixels_outside_is_accepted(self):
        self.spec.assert_within_surface(2562, 800)
        self.spec.assert_within_surface(-2, 800)
        self.spec.assert_within_surface(1280, 1602)

    def test_point_more_than_two_pixels_outside_is_rejected(self):
        with self.assertRaises(CoordinateOutOfBounds):
            self.spec.assert_within_surface(2563, 800)
        with self.assertRaises(CoordinateOutOfBounds):
            self.spec.assert_within_surface(-3, 800)

    def test_rejection_keeps_the_original_coordinate_instead_of_a_clamped_one(self):
        with self.assertRaises(CoordinateOutOfBounds) as ctx:
            self.spec.assert_within_surface(3000, 800)
        self.assertEqual(ctx.exception.value, (3000.0, 800.0))


class VirtualDesktopTest(unittest.TestCase):
    """多屏语义（验收项 11）。"""

    def test_normalisation_denominator_is_dimension_minus_one(self):
        desktop = VirtualDesktop(origin_x=-1920, origin_y=0, width=4480, height=1600)
        self.assertEqual(desktop.to_absolute_input(-1920 + 4479, 0 + 1599), (65535, 65535))
        self.assertEqual(desktop.to_absolute_input(-1920, 0), (0, 0))

    def test_last_row_and_column_are_reachable(self):
        desktop = VirtualDesktop(0, 0, 2560, 1600)
        self.assertEqual(desktop.to_absolute_input(2559, 1599), (65535, 65535))

    def test_negative_virtual_origin_keeps_secondary_screen_points_correct(self):
        desktop = VirtualDesktop(origin_x=-1920, origin_y=0, width=4480, height=1600)
        nx, ny = desktop.to_absolute_input(-1900, 100)
        px, py = desktop.from_absolute_input(nx, ny)
        self.assertLessEqual(abs(px - (-1900)), 1)
        self.assertLessEqual(abs(py - 100), 1)

    def test_absolute_input_is_relative_to_the_whole_virtual_desktop_not_the_primary_screen(self):
        desktop = VirtualDesktop(origin_x=-1920, origin_y=0, width=4480, height=1600)
        nx, _ = desktop.to_absolute_input(-1900, 0)
        self.assertGreater(nx, 0)
        self.assertLess(nx, 65535)

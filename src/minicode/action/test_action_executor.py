"""动作执行器的单测：时序、动作集、双击、滚动、粘贴。

只测外部行为：注入序列、时序、结果里的错误码、剪贴板与光标的对外状态。
"""

from __future__ import annotations

import unittest

from minicode.action.capture import CapturePort
from minicode.action.errors import ErrorCode
from minicode.action.executor import (
    SCROLL_DELTA_PER_LINE,
    ActionExecutor,
    DoubleClickParams,
    TimingPolicy,
)
from minicode.action.fakes import FakeCaptureSource, FakeInputInjector, VirtualClock
from minicode.action.ports import CapturedFrame
from minicode.action.protocol import (
    BUTTON_LEFT,
    BUTTON_RIGHT,
    CLICK_DOUBLE,
    DEFERRED_KINDS,
    EXECUTABLE_KINDS,
    POINT_KINDS,
    Action,
    ActionKind,
)

#: 每个动作种类所需的参数。
_PARAMS = {
    ActionKind.SCREENSHOT: {},
    ActionKind.CLICK: {"x": 100, "y": 200},
    ActionKind.MOVE: {"x": 100, "y": 200},
    ActionKind.DRAG: {"from_x": 10, "from_y": 20, "to_x": 30, "to_y": 40},
    ActionKind.SCROLL: {"x": 100, "y": 200, "lines": 3},
    ActionKind.TYPE: {"text": "你好"},
    ActionKind.KEY: {"combo": ["ctrl", "s"]},
    ActionKind.WAIT: {"ms": 100},
}

#: 双击：`click` 的参数（**不是**独立动作种类）。
_DOUBLE_CLICK_PARAMS = {"x": 100, "y": 200, "click_count": CLICK_DOUBLE}


class ClickTimingTest(unittest.TestCase):
    """C-11 点击时序。"""

    def setUp(self):
        self.clock = VirtualClock()
        self.injector = FakeInputInjector()
        self.executor = ActionExecutor(
            self.injector, sleeper=self.clock.sleep, clock=self.clock.time
        )

    def _click(self):
        return self.executor.execute(
            Action(kind=ActionKind.CLICK, params=dict(_PARAMS[ActionKind.CLICK]), intent="点击")
        )

    def test_injection_sequence_is_move_then_down_then_up(self):
        result = self._click()
        self.assertTrue(result.ok)
        self.assertEqual([e[0] for e in self.injector.events], ["move", "button_down", "button_up"])

    def test_move_and_down_are_two_separate_injections(self):
        self._click()
        move_event, down_event = self.injector.events[0], self.injector.events[1]
        self.assertNotEqual(move_event, down_event)
        self.assertEqual(move_event[1:], (100, 200))

    def test_pause_between_move_and_down_is_at_least_60ms(self):
        result = self._click()
        self.assertGreaterEqual(self.clock.sleeps[0], 0.060)
        self.assertGreaterEqual(result.timing["pre_move_ms"], 60.0)

    def test_pause_between_down_and_up_is_at_least_40ms(self):
        result = self._click()
        self.assertGreaterEqual(self.clock.sleeps[1], 0.040)
        self.assertGreaterEqual(result.timing["hold_ms"], 40.0)

    def test_measured_timing_is_reported_not_just_requested(self):
        result = self._click()
        self.assertIn("total_ms", result.timing)
        self.assertGreaterEqual(result.timing["total_ms"], 100.0)


class ActionSetTest(unittest.TestCase):
    """动作集接线（验收项 13）。"""

    def setUp(self):
        self.clock = VirtualClock()

    def _executor(self, injector, **kwargs):
        kwargs.setdefault("double_click_params", DoubleClickParams(time_ms=500, distance_px=4))
        kwargs.setdefault("screenshot_port", CapturePort((FakeCaptureSource(2560, 1600),)))
        return ActionExecutor(injector, sleeper=self.clock.sleep, clock=self.clock.time, **kwargs)

    def test_all_eight_executable_kinds_run_without_error(self):
        for kind in EXECUTABLE_KINDS:
            with self.subTest(kind=kind.value):
                injector = FakeInputInjector()
                result = self._executor(injector).execute(
                    Action(kind=kind, params=dict(_PARAMS[kind]), intent=kind.value)
                )
                self.assertTrue(result.ok, msg=f"{kind.value} 未执行成功")
                if kind in POINT_KINDS:
                    self.assertIsNotNone(result.landing_point)

    def test_the_four_deferred_kinds_return_an_explicit_error_code(self):
        for kind in DEFERRED_KINDS:
            with self.subTest(kind=kind.value):
                injector = FakeInputInjector()
                result = self._executor(injector).execute(
                    Action(kind=kind, params={}, intent=kind.value)
                )
                self.assertFalse(result.ok)
                self.assertEqual(result.error_code, ErrorCode.ACTION_NOT_IMPLEMENTED)

    def test_double_click_without_runtime_queried_parameters_fails(self):
        """系统双击参数必须运行时查询后注入，本单不写死。"""
        injector = FakeInputInjector()
        executor = ActionExecutor(injector, sleeper=self.clock.sleep, clock=self.clock.time)
        result = executor.execute(
            Action(
                kind=ActionKind.CLICK,
                params=dict(_DOUBLE_CLICK_PARAMS),
                intent="双击",
            )
        )
        self.assertFalse(result.ok)
        self.assertEqual(result.error_code, ErrorCode.EXECUTE_FAILED)
        self.assertEqual(injector.events, [])

    def test_deferred_kinds_do_not_inject_anything(self):
        for kind in DEFERRED_KINDS:
            with self.subTest(kind=kind.value):
                injector = FakeInputInjector()
                self._executor(injector).execute(Action(kind=kind, params={}, intent=kind.value))
                self.assertEqual(injector.events, [])


class DoubleClickTest(unittest.TestCase):
    """双击与两次独立单击可区分（验收项 14）。"""

    def setUp(self):
        self.clock = VirtualClock()
        self.params = DoubleClickParams(time_ms=500, distance_px=4)
        self.injector = FakeInputInjector()
        self.executor = ActionExecutor(
            self.injector,
            sleeper=self.clock.sleep,
            clock=self.clock.time,
            double_click_params=self.params,
        )

    def test_double_click_injects_two_presses_with_a_small_gap(self):
        result = self.executor.execute(
            Action(kind=ActionKind.CLICK, params=dict(_DOUBLE_CLICK_PARAMS), intent="双击")
        )
        self.assertTrue(result.ok)
        self.assertEqual([e[0] for e in self.injector.events].count("button_down"), 2)
        self.assertLess(result.timing["double_click_gap_ms"], self.params.time_ms)

    def test_the_recorded_gap_is_the_measured_interval_between_the_two_presses(self):
        result = self.executor.execute(
            Action(kind=ActionKind.CLICK, params=dict(_DOUBLE_CLICK_PARAMS), intent="双击")
        )
        # 第二次点击序列自带一段停顿，所以实测间隔 > 我们请求的那段 sleep。
        self.assertGreater(
            result.timing["double_click_gap_ms"], result.timing["double_click_requested_gap_ms"]
        )
        self.assertLess(result.timing["double_click_gap_ms"], self.params.time_ms)

    def test_double_click_does_not_move_between_the_two_presses(self):
        self.executor.execute(
            Action(kind=ActionKind.CLICK, params=dict(_DOUBLE_CLICK_PARAMS), intent="双击")
        )
        moves = [e for e in self.injector.events if e[0] == "move"]
        self.assertEqual(len(moves), 2)
        self.assertEqual(moves[0][1:], moves[1][1:])
        self.assertLessEqual(
            max(abs(moves[0][1] - moves[1][1]), abs(moves[0][2] - moves[1][2])),
            self.params.distance_px,
        )

    def test_two_independent_clicks_farther_apart_than_the_threshold_stay_two_clicks(self):
        self.executor.execute(
            Action(kind=ActionKind.CLICK, params=dict(_PARAMS[ActionKind.CLICK]), intent="点击")
        )
        self.clock.sleep(self.params.time_ms / 1000.0 + 0.1)
        self.executor.execute(
            Action(kind=ActionKind.CLICK, params=dict(_PARAMS[ActionKind.CLICK]), intent="点击")
        )
        self.assertEqual([e[0] for e in self.injector.events].count("button_down"), 2)
        gap_ms = self.clock.sleeps[2] * 1000.0
        self.assertGreater(gap_ms, self.params.time_ms)

    def test_double_click_stays_inside_a_short_system_threshold(self):
        """间隔预算要算上点击序列自带的停顿与按住，否则会超过系统阈值。"""
        params = DoubleClickParams(time_ms=150, distance_px=4)
        clock = VirtualClock()
        injector = FakeInputInjector()
        executor = ActionExecutor(
            injector,
            sleeper=clock.sleep,
            clock=clock.time,
            double_click_params=params,
        )
        result = executor.execute(
            Action(
                kind=ActionKind.CLICK,
                params=dict(_DOUBLE_CLICK_PARAMS),
                intent="双击",
            )
        )
        self.assertTrue(result.ok)
        self.assertLess(result.timing["double_click_gap_ms"], params.time_ms)

    def test_a_system_threshold_too_short_for_one_click_sequence_fails_cleanly(self):
        params = DoubleClickParams(time_ms=90, distance_px=4)
        clock = VirtualClock()
        injector = FakeInputInjector()
        executor = ActionExecutor(
            injector,
            sleeper=clock.sleep,
            clock=clock.time,
            double_click_params=params,
        )
        result = executor.execute(
            Action(
                kind=ActionKind.CLICK,
                params=dict(_DOUBLE_CLICK_PARAMS),
                intent="双击",
            )
        )
        self.assertFalse(result.ok)
        self.assertEqual(result.error_code, ErrorCode.EXECUTE_FAILED)

    def test_a_runtime_error_from_the_port_becomes_an_execution_failure(self):
        """端口实现抛出的运行时错误不许崩掉整个进程。"""
        injector = FakeInputInjector()
        executor = ActionExecutor(
            injector,
            sleeper=self.clock.sleep,
            clock=self.clock.time,
            double_click_params=DoubleClickParams(time_ms=500, distance_px=4),
        )

        def boom(combo):
            raise ValueError("未知按键名：'super'")

        injector.press_combo = boom
        result = executor.execute(
            Action(kind=ActionKind.KEY, params=dict(_PARAMS[ActionKind.KEY]), intent="按键")
        )
        self.assertFalse(result.ok)
        self.assertEqual(result.error_code, ErrorCode.EXECUTE_FAILED)


class ScrollTest(unittest.TestCase):
    """滚动量以「行」为对外单位（验收项 15）。"""

    def setUp(self):
        self.clock = VirtualClock()
        self.injector = FakeInputInjector()

    def _executor(self, **kwargs):
        return ActionExecutor(
            self.injector, sleeper=self.clock.sleep, clock=self.clock.time, **kwargs
        )

    def test_scroll_converts_lines_to_wheel_delta_by_factor_50(self):
        result = self._executor().execute(
            Action(kind=ActionKind.SCROLL, params=dict(_PARAMS[ActionKind.SCROLL]), intent="滚动")
        )
        self.assertTrue(result.ok)
        delta = [e for e in self.injector.events if e[0] == "scroll_delta"]
        self.assertEqual(len(delta), 1)
        self.assertEqual(delta[0][3], 3 * SCROLL_DELTA_PER_LINE)

    def test_scroll_waits_between_300_and_600ms_afterwards_by_default(self):
        result = self._executor().execute(
            Action(kind=ActionKind.SCROLL, params=dict(_PARAMS[ActionKind.SCROLL]), intent="滚动")
        )
        self.assertGreaterEqual(result.timing["scroll_settle_ms"], 300.0)
        self.assertLessEqual(result.timing["scroll_settle_ms"], 600.0)

    def test_scroll_wait_can_be_injected_to_zero(self):
        result = self._executor(timing=TimingPolicy(scroll_settle_s=0.0)).execute(
            Action(kind=ActionKind.SCROLL, params=dict(_PARAMS[ActionKind.SCROLL]), intent="滚动")
        )
        self.assertEqual(result.timing["scroll_settle_ms"], 0.0)


class TypeTextTest(unittest.TestCase):
    """粘贴不污染剪贴板（验收项 16）。"""

    def setUp(self):
        self.clock = VirtualClock()

    def _executor(self, injector):
        return ActionExecutor(injector, sleeper=self.clock.sleep, clock=self.clock.time)

    def test_clipboard_content_is_identical_before_and_after(self):
        injector = FakeInputInjector()
        injector.write_clipboard("用户原来的内容")
        result = self._executor(injector).execute(
            Action(kind=ActionKind.TYPE, params=dict(_PARAMS[ActionKind.TYPE]), intent="输入")
        )
        self.assertTrue(result.ok)
        self.assertEqual(injector.read_clipboard(), "用户原来的内容")

    def test_empty_clipboard_is_restored_to_empty(self):
        injector = FakeInputInjector()
        injector.write_clipboard(None)
        self._executor(injector).execute(
            Action(kind=ActionKind.TYPE, params=dict(_PARAMS[ActionKind.TYPE]), intent="输入")
        )
        self.assertIsNone(injector.read_clipboard())

    def test_paste_channel_failure_falls_back_to_unicode_injection(self):
        injector = FakeInputInjector(fail_paste=True)
        result = self._executor(injector).execute(
            Action(kind=ActionKind.TYPE, params=dict(_PARAMS[ActionKind.TYPE]), intent="输入")
        )
        self.assertTrue(result.ok)
        kinds = [e[0] for e in injector.events]
        self.assertIn("type_unicode", kinds)
        self.assertNotIn("press_combo", kinds)

    def test_clipboard_is_restored_even_when_the_paste_channel_fails(self):
        injector = FakeInputInjector(fail_paste=True)
        injector.write_clipboard("用户原来的内容")
        self._executor(injector).execute(
            Action(kind=ActionKind.TYPE, params=dict(_PARAMS[ActionKind.TYPE]), intent="输入")
        )
        self.assertEqual(injector.read_clipboard(), "用户原来的内容")

    def test_a_clipboard_holding_non_text_content_is_never_touched(self):
        """剪贴板里有图片/文件时，粘贴通道会把它们清掉 —— 改走 Unicode 通道。"""
        injector = FakeInputInjector(non_text_clipboard=True)
        injector.write_clipboard("用户原来的内容")
        result = self._executor(injector).execute(
            Action(kind=ActionKind.TYPE, params=dict(_PARAMS[ActionKind.TYPE]), intent="输入")
        )
        self.assertTrue(result.ok)
        kinds = [e[0] for e in injector.events]
        self.assertIn("type_unicode", kinds)
        self.assertNotIn("press_combo", kinds)
        self.assertEqual(injector.read_clipboard(), "用户原来的内容")


class ClickParamsTest(unittest.TestCase):
    """双击与右键是 `click` 的**参数**（工单 M21）。"""

    def setUp(self):
        self.clock = VirtualClock()
        self.injector = FakeInputInjector()
        self.executor = ActionExecutor(
            self.injector,
            sleeper=self.clock.sleep,
            clock=self.clock.time,
            double_click_params=DoubleClickParams(time_ms=500, distance_px=4),
        )

    def test_a_left_click_presses_the_left_button(self):
        result = self.executor.execute(
            Action(
                kind=ActionKind.CLICK,
                params={"x": 100, "y": 200, "button": BUTTON_LEFT},
                intent="点击",
            )
        )
        self.assertTrue(result.ok)
        self.assertEqual(
            [e for e in self.injector.events if e[0] == "button_down"],
            [("button_down", "left")],
        )

    def test_a_right_click_is_a_click_with_the_right_button_parameter(self):
        result = self.executor.execute(
            Action(
                kind=ActionKind.CLICK,
                params={"x": 100, "y": 200, "button": BUTTON_RIGHT},
                intent="右键点击",
            )
        )
        self.assertTrue(result.ok)
        self.assertEqual(
            [e for e in self.injector.events if e[0] == "button_down"],
            [("button_down", "right")],
        )

    def test_an_unknown_button_value_is_rejected_without_injecting(self):
        result = self.executor.execute(
            Action(
                kind=ActionKind.CLICK,
                params={"x": 100, "y": 200, "button": 2},
                intent="点击",
            )
        )
        self.assertFalse(result.ok)
        self.assertEqual(result.error_code, ErrorCode.EXECUTE_FAILED)
        self.assertEqual(self.injector.events, [])

    def test_an_unsupported_click_count_is_rejected_without_injecting(self):
        result = self.executor.execute(
            Action(
                kind=ActionKind.CLICK,
                params={"x": 100, "y": 200, "click_count": 3},
                intent="点击",
            )
        )
        self.assertFalse(result.ok)
        self.assertEqual(result.error_code, ErrorCode.EXECUTE_FAILED)
        self.assertEqual(self.injector.events, [])


class ScreenshotTest(unittest.TestCase):
    """`screenshot` 动作：给了 region 是一块物理像素矩形，不给是整屏（工单 M21）。"""

    def setUp(self):
        self.clock = VirtualClock()
        self.source = FakeCaptureSource(2560, 1600)
        self.port = CapturePort((self.source,))
        self.executor = ActionExecutor(
            FakeInputInjector(),
            sleeper=self.clock.sleep,
            clock=self.clock.time,
            screenshot_port=self.port,
        )

    def test_a_screenshot_without_a_region_covers_the_whole_screen(self):
        result = self.executor.execute(
            Action(kind=ActionKind.SCREENSHOT, params={}, intent="截一张图")
        )
        self.assertTrue(result.ok)
        self.assertIsNotNone(result.captured)
        self.assertEqual((result.captured.width, result.captured.height), (2560, 1600))

    def test_a_screenshot_with_a_region_covers_only_that_rectangle(self):
        result = self.executor.execute(
            Action(
                kind=ActionKind.SCREENSHOT,
                params={"region": (100, 200, 500, 600)},
                intent="截取区域",
            )
        )
        self.assertTrue(result.ok)
        self.assertEqual((result.captured.width, result.captured.height), (400, 400))
        self.assertEqual((result.captured.left, result.captured.top), (100, 200))

    def test_the_captured_frame_is_reported_as_a_digest_not_as_pixels(self):
        result = self.executor.execute(
            Action(kind=ActionKind.SCREENSHOT, params={}, intent="截一张图")
        )
        payload = result.to_dict()["captured"]
        self.assertEqual(payload["sha256"], result.captured.digest())
        self.assertNotIn("payload", payload)

    def test_a_screenshot_without_a_capture_port_fails(self):
        executor = ActionExecutor(FakeInputInjector())
        result = executor.execute(
            Action(kind=ActionKind.SCREENSHOT, params={}, intent="截一张图")
        )
        self.assertFalse(result.ok)
        self.assertEqual(result.error_code, ErrorCode.EXECUTE_FAILED)

    def test_an_empty_frame_is_reported_as_capture_empty(self):
        class _EmptyPort:
            def screenshot(self, region=None):
                return CapturedFrame(width=0, height=0, payload=b"")

        executor = ActionExecutor(FakeInputInjector(), screenshot_port=_EmptyPort())
        result = executor.execute(
            Action(kind=ActionKind.SCREENSHOT, params={}, intent="截一张图")
        )
        self.assertFalse(result.ok)
        self.assertEqual(result.error_code, ErrorCode.CAPTURE_EMPTY)

    def test_a_degenerate_region_is_rejected(self):
        result = self.executor.execute(
            Action(
                kind=ActionKind.SCREENSHOT,
                params={"region": (100, 200, 100, 600)},
                intent="截取区域",
            )
        )
        self.assertFalse(result.ok)
        self.assertEqual(result.error_code, ErrorCode.EXECUTE_FAILED)


class WaitTest(unittest.TestCase):
    """`wait` 动作：等待时长**可注入**，可压到 0（工单 M21）。"""

    def setUp(self):
        self.clock = VirtualClock()
        self.executor = ActionExecutor(
            FakeInputInjector(), sleeper=self.clock.sleep, clock=self.clock.time
        )

    def test_the_wait_length_comes_from_the_ms_parameter(self):
        result = self.executor.execute(
            Action(kind=ActionKind.WAIT, params={"ms": 300}, intent="等 300 ms")
        )
        self.assertTrue(result.ok)
        self.assertEqual(self.clock.sleeps, [0.3])

    def test_a_zero_length_wait_does_not_wait_at_all(self):
        result = self.executor.execute(
            Action(kind=ActionKind.WAIT, params={"ms": 0}, intent="等 0 ms")
        )
        self.assertTrue(result.ok)
        self.assertEqual(self.clock.sleeps, [0.0])

    def test_a_wait_without_ms_is_rejected(self):
        result = self.executor.execute(
            Action(kind=ActionKind.WAIT, params={}, intent="等")
        )
        self.assertFalse(result.ok)
        self.assertEqual(result.error_code, ErrorCode.EXECUTE_FAILED)

    def test_a_negative_wait_is_rejected(self):
        result = self.executor.execute(
            Action(kind=ActionKind.WAIT, params={"ms": -1}, intent="等 -1 ms")
        )
        self.assertFalse(result.ok)
        self.assertEqual(result.error_code, ErrorCode.EXECUTE_FAILED)

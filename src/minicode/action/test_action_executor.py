"""动作执行器的单测：时序、动作集、双击、滚动、粘贴。

只测外部行为：注入序列、时序、结果里的错误码、剪贴板与光标的对外状态。
"""

from __future__ import annotations

import unittest

from minicode.action.errors import ErrorCode
from minicode.action.executor import (
    SCROLL_DELTA_PER_LINE,
    ActionExecutor,
    DoubleClickParams,
    TimingPolicy,
)
from minicode.action.fakes import FakeInputInjector, VirtualClock
from minicode.action.protocol import (
    DEFERRED_KINDS,
    EXECUTABLE_KINDS,
    POINT_KINDS,
    Action,
    ActionKind,
)

#: 每个动作种类所需的参数。
_PARAMS = {
    ActionKind.CLICK: {"x": 100, "y": 200},
    ActionKind.DOUBLE_CLICK: {"x": 100, "y": 200},
    ActionKind.RIGHT_CLICK: {"x": 100, "y": 200},
    ActionKind.MOVE: {"x": 100, "y": 200},
    ActionKind.DRAG: {"from_x": 10, "from_y": 20, "to_x": 30, "to_y": 40},
    ActionKind.SCROLL: {"x": 100, "y": 200, "lines": 3},
    ActionKind.TYPE_TEXT: {"text": "你好"},
    ActionKind.KEY: {"combo": ["ctrl", "s"]},
}


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

    def _executor(self, injector):
        return ActionExecutor(injector, sleeper=self.clock.sleep, clock=self.clock.time)

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
            Action(kind=ActionKind.DOUBLE_CLICK, params=dict(_PARAMS[ActionKind.DOUBLE_CLICK]), intent="双击")
        )
        self.assertTrue(result.ok)
        self.assertEqual([e[0] for e in self.injector.events].count("button_down"), 2)
        self.assertLess(result.timing["double_click_gap_ms"], self.params.time_ms)

    def test_double_click_does_not_move_between_the_two_presses(self):
        self.executor.execute(
            Action(kind=ActionKind.DOUBLE_CLICK, params=dict(_PARAMS[ActionKind.DOUBLE_CLICK]), intent="双击")
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
            Action(kind=ActionKind.TYPE_TEXT, params=dict(_PARAMS[ActionKind.TYPE_TEXT]), intent="输入")
        )
        self.assertTrue(result.ok)
        self.assertEqual(injector.read_clipboard(), "用户原来的内容")

    def test_empty_clipboard_is_restored_to_empty(self):
        injector = FakeInputInjector()
        injector.write_clipboard(None)
        self._executor(injector).execute(
            Action(kind=ActionKind.TYPE_TEXT, params=dict(_PARAMS[ActionKind.TYPE_TEXT]), intent="输入")
        )
        self.assertIsNone(injector.read_clipboard())

    def test_paste_channel_failure_falls_back_to_unicode_injection(self):
        injector = FakeInputInjector(fail_paste=True)
        result = self._executor(injector).execute(
            Action(kind=ActionKind.TYPE_TEXT, params=dict(_PARAMS[ActionKind.TYPE_TEXT]), intent="输入")
        )
        self.assertTrue(result.ok)
        kinds = [e[0] for e in injector.events]
        self.assertIn("type_unicode", kinds)
        self.assertNotIn("press_combo", kinds)

    def test_clipboard_is_restored_even_when_the_paste_channel_fails(self):
        injector = FakeInputInjector(fail_paste=True)
        injector.write_clipboard("用户原来的内容")
        self._executor(injector).execute(
            Action(kind=ActionKind.TYPE_TEXT, params=dict(_PARAMS[ActionKind.TYPE_TEXT]), intent="输入")
        )
        self.assertEqual(injector.read_clipboard(), "用户原来的内容")

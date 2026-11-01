"""护栏 1（急停热键 + 释放已按键）、护栏 6（空闲让位）、护栏 10（dry-run）的单测（W2）。

键状态 / 时刻 / 前台窗口全部用假依赖（见 :mod:`minicode.guard.fakes`），
不碰真 Win32。
"""

import unittest

from minicode.action.fakes import FakeInputInjector
from minicode.guard.estop import EstopWatchdog
from minicode.guard.fakes import fake_key_state_fn, fake_tick_pair
from minicode.guard.idle import IdleWatcher
from minicode.guard.injector import EstopTriggered, GuardedInjector


class EstopWatchdogTest(unittest.TestCase):
    """护栏 1：watchdog 置位后永久为真；线程启停幂等。"""

    def test_watchdog_triggers_when_the_whole_combo_is_down(self):
        pressed = {0x11, 0x12, 0x10, 0x4B}  # Ctrl+Alt+Shift+K 全按住
        watchdog = EstopWatchdog(
            key_state_fn=fake_key_state_fn(pressed), vkeys=(0x11, 0x12, 0x10, 0x4B)
        )
        watchdog.start()
        try:
            self.assertTrue(
                watchdog.triggered, "组合键全按下时 watchdog 应立刻置位"
            )
        finally:
            watchdog.stop()

    def test_watchdog_does_not_trigger_on_a_partial_combo(self):
        pressed = {0x11, 0x12}  # 只按了 Ctrl+Alt
        watchdog = EstopWatchdog(
            key_state_fn=fake_key_state_fn(pressed), vkeys=(0x11, 0x12, 0x10, 0x4B)
        )
        watchdog.start()
        try:
            self.assertFalse(watchdog.triggered)
        finally:
            watchdog.stop()

    def test_trigger_is_permanent_for_the_run(self):
        watchdog = EstopWatchdog(
            key_state_fn=fake_key_state_fn(set()), vkeys=(0x11,)
        )
        watchdog.trigger_for_test()
        self.assertTrue(watchdog.triggered)


class GuardedInjectorTest(unittest.TestCase):
    """急停感知注入器：急停后零输入 + 松开悬空键钮；dry-run 只记录不执行。"""

    def test_input_is_refused_and_pressed_buttons_released_after_estop(self):
        inner = FakeInputInjector()
        watchdog = EstopWatchdog(key_state_fn=fake_key_state_fn(set()), vkeys=(0x11,))
        watchdog.trigger_for_test()
        injector = GuardedInjector(inner, estop=watchdog)

        with self.assertRaises(EstopTriggered):
            injector.move(100, 200)
        # 零动作下发：真注入器一个输入事件都没收到。
        self.assertEqual(
            [event for event in inner.events if event[0] != "button_up"],
            [],
            f"急停后不允许任何输入注入，实际：{inner.events}",
        )

    def test_a_drag_interrupted_by_estop_releases_the_held_button(self):
        inner = FakeInputInjector()
        watchdog = EstopWatchdog(key_state_fn=fake_key_state_fn(set()), vkeys=(0x11,))
        injector = GuardedInjector(inner, estop=watchdog)
        injector.move(10, 10)
        injector.button_down("left")
        watchdog.trigger_for_test()  # 拖拽中间急停
        with self.assertRaises(EstopTriggered):
            injector.move(20, 20)
        ups = [event for event in inner.events if event[0] == "button_up"]
        self.assertEqual(
            ups,
            [("button_up", "left")],
            "急停要把按着的鼠标键松开（不留悬空的 button_down）",
        )

    def test_dry_run_prints_actions_but_never_injects(self):
        inner = FakeInputInjector()
        printed: list[str] = []
        injector = GuardedInjector(
            inner, dry_run=True, printer=printed.append
        )
        injector.move(100, 200)
        injector.type_unicode("你好")
        injector.button_down("left")
        injector.button_up("left")
        self.assertEqual(inner.events, [], "dry-run 不许碰真注入器")
        self.assertEqual(len(injector.journal), 4, "dry-run 的注入序列照样记录")
        self.assertIn("dry-run", printed[0])
        self.assertIn("type_unicode", "\n".join(printed))

    def test_readonly_operations_pass_even_after_estop(self):
        inner = FakeInputInjector(cursor=(5, 6))
        watchdog = EstopWatchdog(key_state_fn=fake_key_state_fn(set()), vkeys=(0x11,))
        watchdog.trigger_for_test()
        injector = GuardedInjector(inner, estop=watchdog)
        self.assertEqual(injector.read_cursor(), (5, 6), "只读操作不是输入，不拦")

    def test_the_journal_records_every_input_attempt(self):
        inner = FakeInputInjector()
        injector = GuardedInjector(inner, estop=None)
        injector.move(1, 2)
        injector.scroll_delta(3, 4, 120)
        self.assertEqual(
            injector.journal,
            [("move", (1, 2)), ("scroll_delta", (3, 4, 120))],
        )


class IdleWatcherTest(unittest.TestCase):
    """护栏 6：用户刚动过键鼠 → 暂停让位。"""

    def test_recent_user_input_pauses_the_agent(self):
        # last_input=900, now=1000 → 空闲 100ms < 1500ms 阈值 → 正在用。
        tick_fn, now_fn = fake_tick_pair(last_input=900, now=1000)
        watcher = IdleWatcher(tick_fn=tick_fn, now_tick_fn=now_fn)
        self.assertTrue(watcher.user_recently_active(active_ms=1500))

    def test_a_long_idle_user_yields_control_to_the_agent(self):
        # last_input=100, now=1000 → 空闲 900ms ≥ 1500ms？否——900 < 1500 仍在窗内。
        tick_fn, now_fn = fake_tick_pair(last_input=100, now=1000)
        watcher = IdleWatcher(tick_fn=tick_fn, now_tick_fn=now_fn)
        self.assertFalse(watcher.user_recently_active(active_ms=500))
        self.assertTrue(watcher.user_recently_active(active_ms=1500))

    def test_tick_wraparound_is_handled_by_modular_arithmetic(self):
        # 32 位 TickCount 回绕：last_input 靠近 2^32，now 已回绕到小值。
        tick_fn, now_fn = fake_tick_pair(last_input=0xFFFF_FFF0, now=0x0000_0008)
        watcher = IdleWatcher(tick_fn=tick_fn, now_tick_fn=now_fn)
        self.assertEqual(watcher.idle_ms(), 0x18)

    def test_the_same_clock_question_is_answered_consistently(self):
        tick_fn, now_fn = fake_tick_pair(last_input=500, now=500)
        watcher = IdleWatcher(tick_fn=tick_fn, now_tick_fn=now_fn)
        self.assertEqual(watcher.idle_ms(), 0)
        self.assertTrue(watcher.user_recently_active(active_ms=1))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

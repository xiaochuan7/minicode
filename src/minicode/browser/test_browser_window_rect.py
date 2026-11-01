"""窗口矩形读取的**显式失败**（W7 P2 #8 的红/绿测试）。

``GetWindowRect`` 失败必须显式（``None``），不许静默产出 ``(0,0,0,0)``
→ 比例 0 → 元素全零框 → 被「页面结构快照读不出元素」之外的路
（零比例坐标污染）误读。
"""

from __future__ import annotations

import unittest
from unittest import mock

from minicode.browser import cdp
from minicode.browser.ports import PageSnapshot


class _FakeMetricsPage:
    """page.evaluate 只回窗口度量（其余按需抛）。"""

    def evaluate(self, _script):
        return {
            "outerWidth": 1200,
            "outerHeight": 800,
            "innerWidth": 1184,
            "innerHeight": 720,
        }


class _FakeCdp:
    def send(self, _method, _params=None):
        return {}


class WindowRectExplicitFailureTest(unittest.TestCase):
    """Win32 失败 → ``None``；视口退化 → 空快照。"""

    def test_window_rect_returns_none_when_win32_fails(self):
        class _FailingUser32:
            def GetWindowRect(self, _hwnd, _rect):
                return 0  # Win32 失败

        with mock.patch.object(cdp, "_user32", lambda: _FailingUser32()):
            self.assertIsNone(cdp.window_rect(1234))

    def test_snapshot_is_empty_when_the_window_rect_is_unavailable(self):
        """hwnd=0（找不到窗口）→ 视口量不出来 → **空快照**（端口契约）。

        旧实现拿 (0,0,0,0) 算出 scale=0 → 元素坐标全零框被逐个丢掉，
        看起来像「解析正常但页面没元素」。
        """
        page = cdp.ChromiumPage(_FakeMetricsPage(), _FakeCdp(), hwnd=0)
        snapshot = page.snapshot()
        self.assertIsInstance(snapshot, PageSnapshot)
        self.assertEqual(snapshot.elements, ())

    def test_snapshot_is_empty_for_a_degenerate_window_rect(self):
        """最小化窗口（-32000）的矩形是退化的 → 空快照，不是坏比例。"""
        def minimized_rect(hwnd):
            return (-32000, -32000, -31800, -31700)

        page = cdp.ChromiumPage(_FakeMetricsPage(), _FakeCdp(), hwnd=4242)
        with mock.patch.object(cdp, "window_rect", staticmethod(minimized_rect)):
            snapshot = page.snapshot()
        self.assertIsInstance(snapshot, PageSnapshot)
        self.assertEqual(snapshot.elements, ())
        self.assertTrue(page.last_error)

    def test_a_healthy_window_rect_still_produces_elements(self):
        """好矩形不受影响：元素照常产出（回归保护）。"""
        page = cdp.ChromiumPage(_FakeMetricsPage(), _FakeCdp(), hwnd=4242)
        with mock.patch.object(
            cdp, "window_rect", staticmethod(lambda hwnd: (100, 100, 1300, 900))
        ), mock.patch.object(
            cdp.ChromiumPage, "_parse_tree", lambda self: []
        ), mock.patch.object(
            cdp.ChromiumPage, "_focused", lambda self: ("", "")
        ):
            snapshot = page.snapshot()
        self.assertEqual(snapshot.elements, ())
        self.assertIsNotNone(snapshot.viewport)
        self.assertAlmostEqual(snapshot.viewport.scale, 1200 / 1200.0)


if __name__ == "__main__":
    unittest.main()

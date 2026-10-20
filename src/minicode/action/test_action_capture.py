"""截图端口（把若干截图源接成一个端口）的单测。

只测外部行为：抓到的帧、被跳过的源名、区域抓取。
"""

from __future__ import annotations

import unittest

from minicode.action.capture import CapturePort
from minicode.action.fakes import FakeCaptureSource


class CapturePortTest(unittest.TestCase):
    """整屏 / 区域 / 跳过 0×0 的源。"""

    def test_a_whole_screen_capture_comes_from_the_first_source(self):
        source = FakeCaptureSource(width=2560, height=1600, name="first")
        port = CapturePort((source,))
        frame = port.screenshot()
        self.assertEqual((frame.width, frame.height), (2560, 1600))
        self.assertEqual(frame.source, source.source)

    def test_a_source_that_only_returns_an_empty_frame_is_skipped(self):
        empty = FakeCaptureSource(width=0, height=0, name="empty")
        good = FakeCaptureSource(width=2560, height=1600, name="good")
        port = CapturePort((empty, good))
        frame = port.screenshot()
        self.assertEqual((frame.width, frame.height), (2560, 1600))
        self.assertEqual(port.skipped, ("empty",))

    def test_all_sources_empty_gives_an_empty_frame_and_names_the_skipped_ones(self):
        empty = FakeCaptureSource(width=0, height=0, name="empty")
        port = CapturePort((empty,))
        frame = port.screenshot()
        self.assertTrue(frame.is_empty)
        self.assertEqual(port.skipped, ("empty",))

    def test_a_region_capture_returns_only_that_rectangle(self):
        source = FakeCaptureSource(width=2560, height=1600, name="first")
        port = CapturePort((source,))
        frame = port.screenshot((100, 200, 500, 600))
        self.assertEqual((frame.width, frame.height), (400, 400))
        self.assertEqual((frame.left, frame.top), (100, 200))

    def test_a_region_capture_without_a_capable_source_fails_loudly(self):
        class _WholeScreenOnly:
            name = "whole-screen-only"

            def capture(self):
                from minicode.action.ports import CapturedFrame

                return CapturedFrame(width=2560, height=1600, payload=b"")

        port = CapturePort((_WholeScreenOnly(),))
        with self.assertRaises(ValueError):
            port.screenshot((0, 0, 10, 10))

"""**词级 OCR 读数**的单测（工单 B-1 的第一块）。

判据：**读得出**（有词 / 没词）、**读不出**（格式不对 / 后端失败）——
**读不出必须抛异常**（上层据此判「判不了」），**不假装读到空**。
"""

from __future__ import annotations

import unittest
from unittest import mock

from minicode.action.ports import CapturedFrame, PixelFormat
from minicode.verification.ocr_windows import OcrWord, WindowsOcrPort, parse_words


def _frame() -> CapturedFrame:
    return CapturedFrame(
        width=8,
        height=4,
        left=0,
        top=0,
        payload=bytes(8 * 4 * 3),
        backend="fake",
        source="synthetic",
        pixel_format=PixelFormat.BGR,
    )


class OcrWordParsingTests(unittest.TestCase):
    """**词级读数的解析**。"""

    def test_parses_words_with_pixel_boxes(self):
        raw = "OCR_WORDS_V1\t2\nStart\t10\t20\t30\t12\nSave\t50\t60\t40\t14\n"
        words = parse_words(raw)
        self.assertEqual(len(words), 2)
        self.assertEqual(words[0], OcrWord(text="Start", x=10, y=20, w=30, h=12))
        self.assertEqual(words[1].box, (50, 60, 90, 74))

    def test_no_words_is_a_legitimate_empty_result(self):
        """图上没有字 → **空列表**（那与「读不出来」是两件事）。"""
        self.assertEqual(parse_words("OCR_WORDS_V1\t0\n"), [])

    def test_unreadable_reading_raises_instead_of_faking_empty(self):
        """读数**读不出来** → 抛异常（上层据此判「判不了」），不假装读到空。"""
        for raw in (
            "",  # 空的
            "OCR_WORDS_V1\t1\n",  # 声明 1 条、实际 0 条
            "OCR_WORDS_V1\tx\n",  # 条数不是整数
            "NOPE\t1\nStart\t1\t2\t3\t4\n",  # 头不对
            "OCR_WORDS_V1\t1\nStart\t1\t2\t3\n",  # 字段数不对
            "OCR_WORDS_V1\t1\nStart\ta\tb\tc\td\n",  # 坐标不是整数
        ):
            with self.subTest(raw=raw):
                with self.assertRaises(OSError):
                    parse_words(raw)

    def test_backend_failure_raises(self):
        """后端跑不起来 → 抛异常（**不假装读到空**）。"""
        completed = mock.Mock(returncode=1, stdout=b"", stderr=b"boom")
        with mock.patch(
            "minicode.verification.ocr_windows.subprocess.run", return_value=completed
        ):
            with self.assertRaises(OSError):
                WindowsOcrPort().read_words(_frame())

    def test_read_still_returns_plain_text(self):
        """**``read()`` 不破坏**（现有调用方还在用）：整段文本照旧。"""
        completed = mock.Mock(returncode=0, stdout=b"", stderr=b"")
        with mock.patch(
            "minicode.verification.ocr_windows.subprocess.run", return_value=completed
        ), mock.patch(
            "minicode.verification.ocr_windows.encode_png", return_value=b"png"
        ), mock.patch(
            "pathlib.Path.exists", return_value=True
        ), mock.patch(
            "pathlib.Path.read_text", return_value="整段文本"
        ):
            self.assertEqual(WindowsOcrPort().read(_frame()), "整段文本")


if __name__ == "__main__":
    unittest.main()

"""ScreenParser 检测候选源（V3 第 2 步）的单测：注入假 predictor，零 torch。

覆盖：物理像素偏移、编号与来源标签、描述（标签+邻近词）、IoU 去重、
上限裁剪、空帧。
"""

from __future__ import annotations

import unittest

from minicode.action.ports import CapturedFrame, PixelFormat
from minicode.model.detector_source import (
    DETECTOR_SOURCE,
    Detection,
    ScreenParserCandidateSource,
)


def _blank_frame(*, left: int = 0, top: int = 0, size: tuple[int, int] = (400, 300)):
    numpy = __import__("numpy")
    width, height = size
    canvas = numpy.full((height, width, 3), 255, dtype=numpy.uint8)
    return CapturedFrame(
        width=width,
        height=height,
        left=left,
        top=top,
        payload=canvas.tobytes(),
        pixel_format=PixelFormat.BGR,
    )


def _predictor(detections):
    def predict(image):
        return list(detections)

    return predict


class _Word:
    def __init__(self, text, x, y, w, h):
        self.text = text
        self.x = x
        self.y = y
        self.w = w
        self.h = h


class DetectorSourceTest(unittest.TestCase):
    def test_detections_become_numbered_physical_candidates(self):
        source = ScreenParserCandidateSource(
            predictor=_predictor(
                [
                    Detection(box=(10, 20, 60, 70), label="button", confidence=0.9),
                    Detection(box=(100, 20, 150, 70), label="icon", confidence=0.8),
                ]
            )
        )
        frame = _blank_frame(left=100, top=50)
        candidates = source.candidates(frame, "点击按钮")
        self.assertEqual([c.number for c in candidates], [1, 2])
        for candidate in candidates:
            self.assertEqual(candidate.source, DETECTOR_SOURCE)
            self.assertGreaterEqual(candidate.box[0], 100)
            self.assertGreaterEqual(candidate.box[1], 50)
        # 第一个候选 = 检测框 + 帧原点。
        self.assertEqual(candidates[0].box, (110, 70, 160, 120))

    def test_description_uses_label_and_nearest_ocr_word(self):
        source = ScreenParserCandidateSource(
            predictor=_predictor(
                [Detection(box=(10, 20, 60, 70), label="icon", confidence=0.9)]
            ),
            word_reader=lambda frame: [_Word("设置", 12, 74, 40, 14)],
        )
        candidates = source.candidates(_blank_frame(), "打开设置")
        self.assertEqual(candidates[0].text, "设置（icon）")

    def test_overlapping_detections_are_deduped(self):
        source = ScreenParserCandidateSource(
            predictor=_predictor(
                [
                    Detection(box=(10, 10, 80, 80), label="button", confidence=0.9),
                    Detection(box=(12, 12, 82, 82), label="icon", confidence=0.8),
                    Detection(box=(200, 10, 260, 70), label="link", confidence=0.7),
                ]
            )
        )
        candidates = source.candidates(_blank_frame(), "目标")
        self.assertEqual(len(candidates), 2)

    def test_candidate_cap_is_respected(self):
        detections = [
            Detection(box=(i * 30, 10, i * 30 + 24, 40), label="icon", confidence=0.5)
            for i in range(30)
        ]
        source = ScreenParserCandidateSource(
            predictor=_predictor(detections), max_candidates=20
        )
        self.assertEqual(len(source.candidates(_blank_frame(), "目标")), 20)

    def test_degenerate_boxes_are_dropped(self):
        source = ScreenParserCandidateSource(
            predictor=_predictor(
                [Detection(box=(10, 10, 10, 10), label="icon", confidence=0.9)]
            )
        )
        self.assertEqual(source.candidates(_blank_frame(), "目标"), [])

    def test_empty_frame_yields_no_candidates(self):
        source = ScreenParserCandidateSource(predictor=_predictor([]))
        self.assertEqual(
            source.candidates(CapturedFrame(width=0, height=0, payload=b""), "目标"), []
        )


if __name__ == "__main__":
    unittest.main()

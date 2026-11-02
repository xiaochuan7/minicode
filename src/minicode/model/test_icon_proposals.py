"""V3 零模型图标候选源的单测：合成帧上的确定性用例。

覆盖：提议召回（每个图标都被提出）、物理坐标偏移、去重
（不同位置的相似图标**不**合并 —— 两个一样的垃圾桶是两个目标）、
OCR 邻近词描述、空帧不炸。
"""

from __future__ import annotations

import unittest

from minicode.action.ports import CapturedFrame, PixelFormat
from minicode.ground.candidates import iou
from minicode.model.icon_proposals import (
    IconProposalCandidateSource,
    hamming_distance,
    phash_of_frame,
)

_TRUE_BOXES = [
    (50, 50, 80, 80),
    (200, 60, 240, 100),
    (100, 200, 140, 240),
]


def _icon_frame(*, left: int = 0, top: int = 0, size: tuple[int, int] = (400, 300)):
    """白底 + 三个深色方块（图标）的合成帧（BGR）。"""
    numpy = __import__("numpy")
    width, height = size
    canvas = numpy.full((height, width, 3), 255, dtype=numpy.uint8)
    for x1, y1, x2, y2 in _TRUE_BOXES:
        canvas[y1:y2, x1:x2] = 30
    return CapturedFrame(
        width=width,
        height=height,
        left=left,
        top=top,
        payload=canvas.tobytes(),
        pixel_format=PixelFormat.BGR,
    )


class IconProposalTest(unittest.TestCase):
    """图标提议：召回 / 坐标 / 编号 / 去重 / 描述。"""

    def test_every_synthetic_icon_is_proposed(self):
        source = IconProposalCandidateSource()
        frame = _icon_frame()
        candidates = source.candidates(frame, "点击设置图标")
        self.assertTrue(candidates)
        for truth in _TRUE_BOXES:
            best = max((iou(candidate.box, truth) for candidate in candidates), default=0.0)
            self.assertGreaterEqual(
                best, 0.3, f"真值框 {truth} 没有被任何候选盖住（最好 IoU={best:.2f}）"
            )

    def test_candidates_are_numbered_and_tagged(self):
        source = IconProposalCandidateSource()
        candidates = source.candidates(_icon_frame(), "目标")
        self.assertEqual(
            [candidate.number for candidate in candidates],
            list(range(1, len(candidates) + 1)),
        )
        for candidate in candidates:
            self.assertEqual(candidate.source, "icon-proposal")
            self.assertTrue(candidate.text)

    def test_boxes_are_physical_pixels_including_frame_origin(self):
        """候选框是**物理像素**（含帧原点偏移）—— 落点直接取中心，零换算。"""
        source = IconProposalCandidateSource()
        frame = _icon_frame(left=100, top=50)
        candidates = source.candidates(frame, "目标")
        self.assertTrue(candidates)
        for candidate in candidates:
            self.assertGreaterEqual(candidate.box[0], 100)
            self.assertGreaterEqual(candidate.box[1], 50)
        # 至少一个候选落在第一个方块的物理位置附近（+100/+50 偏移）。
        truth = tuple(value + offset for value, offset in zip(_TRUE_BOXES[0], (100, 50, 100, 50), strict=True))
        best = max((iou(candidate.box, truth) for candidate in candidates), default=0.0)
        self.assertGreaterEqual(best, 0.3)

    def test_identical_icons_at_different_locations_are_both_kept(self):
        """**不同位置的相似图标不去重** —— 那是两个真实目标。"""
        numpy = __import__("numpy")
        canvas = numpy.full((200, 300, 3), 255, dtype=numpy.uint8)
        canvas[40:70, 40:70] = 30
        canvas[40:70, 200:230] = 30
        frame = CapturedFrame(
            width=300,
            height=200,
            payload=canvas.tobytes(),
            pixel_format=PixelFormat.BGR,
        )
        source = IconProposalCandidateSource()
        candidates = source.candidates(frame, "目标")
        hit_left = any(
            iou(candidate.box, (40, 40, 70, 70)) >= 0.3 for candidate in candidates
        )
        hit_right = any(
            iou(candidate.box, (200, 40, 230, 70)) >= 0.3 for candidate in candidates
        )
        self.assertTrue(hit_left, "左边的图标被丢了")
        self.assertTrue(hit_right, "右边的图标被丢了（不同位置不许合并）")

    def test_nearest_ocr_word_becomes_the_description(self):
        """图标正下方的 OCR 词挂成候选文字（可指认的名字）。"""
        class _Word:
            def __init__(self, text, x, y, w, h):
                self.text = text
                self.x = x
                self.y = y
                self.w = w
                self.h = h

        source = IconProposalCandidateSource(
            word_reader=lambda frame: [_Word("Settings", 45, 82, 40, 12)]
        )
        candidates = source.candidates(_icon_frame(), "打开 Settings")
        with_word = [candidate for candidate in candidates if "Settings" in candidate.text]
        self.assertTrue(with_word, "最近的 OCR 词没有被挂成描述")

    def test_empty_frame_yields_no_candidates(self):
        source = IconProposalCandidateSource()
        frame = CapturedFrame(width=0, height=0, payload=b"")
        self.assertEqual(source.candidates(frame, "目标"), [])


class PhashTest(unittest.TestCase):
    """pHash 的两个用途：去重判等 + 测试。"""

    def test_identical_frames_have_zero_distance(self):
        frame = _icon_frame()
        self.assertEqual(hamming_distance(phash_of_frame(frame), phash_of_frame(frame)), 0)

    def test_different_frames_have_larger_distance(self):
        distance = hamming_distance(
            phash_of_frame(_icon_frame()), phash_of_frame(_icon_frame(size=(320, 240)))
        )
        self.assertGreater(distance, 0)


if __name__ == "__main__":
    unittest.main()

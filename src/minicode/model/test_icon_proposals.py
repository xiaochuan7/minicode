"""V3 零模型图标候选源的单测：合成帧上的确定性用例。

覆盖：提议召回（每个图标都被提出）、物理坐标偏移、去重
（不同位置的相似图标**不**合并 —— 两个一样的垃圾桶是两个目标）、
OCR 邻近词描述、空帧不炸；W7 追加：连通域（numpy 两趟并查集）
的形状等价性、**物理像素域**尺寸过滤（分辨率污染修复）。
"""

from __future__ import annotations

import unittest

from minicode.action.ports import CapturedFrame, PixelFormat
from minicode.ground.candidates import iou
from minicode.model.icon_proposals import (
    IconProposalCandidateSource,
    _connected_components,
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


class ConnectedComponentsTest(unittest.TestCase):
    """连通域（numpy 两趟并查集）：形状语义与旧 BFS 同口径。"""

    @staticmethod
    def _mask(pixels):
        numpy = __import__("numpy")
        mask = numpy.zeros((12, 14), dtype=bool)
        for y, x in pixels:
            mask[y, x] = True
        return mask

    def test_empty_mask_yields_no_components(self):
        self.assertEqual(_connected_components(self._mask([])), [])

    def test_single_pixel_is_one_one_by_one_component(self):
        boxes = _connected_components(self._mask([(3, 5)]))
        self.assertEqual(boxes, [(5, 3, 6, 4, 1)])

    def test_diagonal_pixels_are_not_connected_at_4_adjacency(self):
        boxes = _connected_components(self._mask([(0, 0), (1, 1)]))
        self.assertEqual(len(boxes), 2)

    def test_l_shape_is_one_component_with_full_bbox(self):
        pixels = [(y, 0) for y in range(10)] + [(9, x) for x in range(10)]
        boxes = _connected_components(self._mask(pixels))
        self.assertEqual(len(boxes), 1)
        left, top, right, bottom, area = boxes[0]
        self.assertEqual((left, top, right, bottom), (0, 0, 10, 10))
        self.assertEqual(area, 19)  # 10 竖 + 10 横 - 1 交点

    def test_two_rows_merge_only_when_columns_overlap(self):
        # 行 0 的 [2,4) 与行 1 的 [3,6) 重叠 → 一个连通域；与 [8,10) 无关。
        pixels = [(0, 2), (0, 3), (1, 3), (1, 4), (1, 5), (5, 8), (5, 9)]
        boxes = _connected_components(self._mask(pixels))
        self.assertEqual(len(boxes), 2)

    def test_component_touching_last_column_has_closed_bbox(self):
        numpy = __import__("numpy")
        mask = numpy.zeros((6, 10), dtype=bool)
        mask[2, 8:10] = True  # 贴到最后一列
        boxes = _connected_components(mask)
        self.assertEqual(boxes, [(8, 2, 10, 3, 2)])


class PhysicalDomainFilterTest(unittest.TestCase):
    """W7 分辨率污染修复：尺寸判定在**物理像素**域。

    旧行为：4K 帧整屏缩到 640 工作宽（×0.167）后在工作分辨率里卡
    ``6 <= w <= 200`` —— 16px 物理小图标只剩 2.7 工作像素，机械排除。
    新行为：阈值换算到物理域后判定，小图标进得了候选清单。
    """

    def _tiny_icon_frame(self, size: tuple[int, int], icon: int) -> CapturedFrame:
        """白底大帧 + 一个 ``icon``×``icon`` 的深色小方块（居中）。"""
        numpy = __import__("numpy")
        width, height = size
        canvas = numpy.full((height, width, 3), 255, dtype=numpy.uint8)
        half = icon // 2
        cy, cx = height // 2, width // 2
        canvas[cy - half : cy + half, cx - half : cx + half] = 30
        return CapturedFrame(
            width=width,
            height=height,
            payload=canvas.tobytes(),
            pixel_format=PixelFormat.BGR,
        )

    def test_tiny_icon_survives_on_4k_frame(self):
        """4K 帧（3840×2160）里 16px 的物理小图标必须被提出。"""
        source = IconProposalCandidateSource()
        frame = self._tiny_icon_frame((3840, 2160), icon=16)
        truth = (1920 - 8, 1080 - 8, 1920 + 8, 1080 + 8)
        candidates = source.candidates(frame, "小图标")
        best = max((iou(candidate.box, truth) for candidate in candidates), default=0.0)
        self.assertGreaterEqual(
            best, 0.3, f"4K 帧上 16px 小图标没进候选（最好 IoU={best:.2f}）"
        )

    def test_tiny_icon_survives_on_small_frame(self):
        """小帧上同尺寸图标也提出（阈值物理域，两档分辨率行为一致）。"""
        source = IconProposalCandidateSource()
        frame = self._tiny_icon_frame((400, 300), icon=16)
        truth = (200 - 8, 150 - 8, 200 + 8, 150 + 8)
        candidates = source.candidates(frame, "小图标")
        best = max((iou(candidate.box, truth) for candidate in candidates), default=0.0)
        self.assertGreaterEqual(best, 0.3)

    def test_noise_dots_below_min_physical_px_are_filtered(self):
        """孤立噪点（膨胀后仍低于物理最小边 6px）仍被滤掉（阈值没有失效）。

        ⚠️ 半径 1 的膨胀把任何深色块放大 ~4px（每边 +1 再加边缘扩散）——
        能稳定低于阈值的只有单像素级噪点。
        """
        numpy = __import__("numpy")
        canvas = numpy.full((400, 300, 3), 255, dtype=numpy.uint8)
        canvas[150, 150] = 30  # 单像素噪点
        frame = CapturedFrame(
            width=300, height=200, payload=canvas[:200].tobytes(),
            pixel_format=PixelFormat.BGR,
        )
        source = IconProposalCandidateSource()
        candidates = source.candidates(frame, "目标")
        self.assertEqual(candidates, [])


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

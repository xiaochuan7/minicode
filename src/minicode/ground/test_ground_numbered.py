"""**编号选择**分支（工单 B-1）的单测：候选源 → 编号 → 候选框的物理中心。

判据对象是**对外可见的契约**：落点、回显、模型调用数、候选框的坐标系。

覆盖：

* **OCR 词级读数的解析** —— 有词 / 没词 / 后端失败 / 读数格式不对；
* **IoU 去重** —— UFO² 的 0.1 阈值（**严格大于**才算同一个）；
* **候选框从帧坐标到物理坐标** —— **加上帧的原点**（唯一真值 = 物理像素 × 虚拟桌面原点）；
* **编号分支的落点 = 候选框中心** —— 偏差 ≤ 1.0 px；
* **无候选** —— 走点式分支，并**显式记录「无候选」**；
* **A-2** —— 整屏那一级自己缩到 ``model_input_size``；**crop 那一级不缩放**。
"""

from __future__ import annotations

import base64
import json
import struct
import unittest

from minicode.action.ports import CapturedFrame, PixelFormat
from minicode.coord.coordinates import ModelScale
from minicode.ground.candidates import (
    IOU_THRESHOLD_FOR_MERGE,
    GroundingCandidate,
    OcrCandidateSource,
    UiaCandidateSource,
    dedupe_by_iou,
    iou,
    merge_candidate_sources,
)
from minicode.ground.channel import TwoStageGrounder, default_crop_size
from minicode.ground.gate import GateMode
from minicode.ground.prompt import (
    CandidateLine,
    normalize_target,
    numbered_candidates_prompt,
    parse_chosen_numbers,
)
from minicode.model.client import ModelReply
from minicode.model.effort import Effort
from minicode.verification.ocr_windows import OcrWord

#: 一帧小图（够用就行；单测不碰真机）。
_FRAME_WIDTH = 64
_FRAME_HEIGHT = 48


def _reply(text: str) -> ModelReply:
    return ModelReply(
        text=text,
        model="stub",
        endpoint="stub://",
        effort=Effort.LOW,
        http_status=200,
        latency_ms=1.0,
    )


class _StubModel:
    """按调用顺序给出预置回答的桩模型。"""

    def __init__(self, *outcomes: ModelReply | Exception):
        self._outcomes = list(outcomes)
        self.calls: list[dict] = []

    def complete(
        self,
        prompt: str,
        *,
        effort: Effort | str | None = None,
        max_tokens: int = 2000,
        image_base64: str | None = None,
        thinking_disabled: bool = False,
    ) -> ModelReply:
        self.calls.append(
            {
                "prompt": prompt,
                "effort": effort,
                "max_tokens": max_tokens,
                "image_base64": image_base64,
            }
        )
        if not self._outcomes:
            raise AssertionError("桩模型没有预置回答")
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def _frame(left: int = 0, top: int = 0) -> CapturedFrame:
    payload = bytes((x * 7 + y * 3) % 256 for y in range(_FRAME_HEIGHT) for x in range(_FRAME_WIDTH) for _ in range(3))
    return CapturedFrame(
        width=_FRAME_WIDTH,
        height=_FRAME_HEIGHT,
        left=left,
        top=top,
        payload=payload,
        backend="fake",
        source="synthetic",
        pixel_format=PixelFormat.BGR,
    )


def _png_size(image_base64: str) -> tuple[int, int]:
    """从 PNG 的头里读出尺寸（看看**实际发出去的那张图**多大）。"""
    raw = base64.b64decode(image_base64)
    width, height = struct.unpack(">II", raw[16:24])
    return (width, height)


class IouTests(unittest.TestCase):
    """**IoU 去重**：UFO² 的 0.1 阈值（**物理像素**空间里比）。"""

    def test_iou_of_identical_boxes_is_one(self):
        self.assertAlmostEqual(iou((0, 0, 10, 10), (0, 0, 10, 10)), 1.0)

    def test_iou_of_disjoint_boxes_is_zero(self):
        self.assertEqual(iou((0, 0, 10, 10), (20, 20, 30, 30)), 0.0)

    def test_threshold_boundary_is_strictly_greater(self):
        """**IoU 恰好等于 0.1 不算同一个**（严格大于才算）。"""
        # 两个 10×10 的框横向错开 8 px：交集 2×10=20，并集 180 → IoU = 0.111…
        near = (0, 0, 10, 10)
        far = (8, 0, 18, 10)
        self.assertGreater(iou(near, far), IOU_THRESHOLD_FOR_MERGE)
        # 交集 1×10=10、并集 190 → IoU ≈ 0.0526 < 0.1 → 不算同一个
        farther = (9, 0, 19, 10)
        self.assertLess(iou(near, farther), IOU_THRESHOLD_FOR_MERGE)
        kept = dedupe_by_iou(
            [
                GroundingCandidate(number=1, text="a", box=near),
                GroundingCandidate(number=2, text="b", box=far),
                GroundingCandidate(number=3, text="c", box=farther),
            ]
        )
        self.assertEqual([item.text for item in kept], ["a", "c"])

    def test_dedupe_keeps_the_first_one(self):
        kept = dedupe_by_iou(
            [
                GroundingCandidate(number=1, text="先", box=(0, 0, 10, 10)),
                GroundingCandidate(number=2, text="后", box=(1, 1, 11, 11)),
            ]
        )
        self.assertEqual(len(kept), 1)
        self.assertEqual(kept[0].text, "先")


class CandidateCoordinateTests(unittest.TestCase):
    """**候选框从帧坐标到物理坐标** —— 必须加上帧的原点。"""

    def test_frame_origin_is_added_to_the_candidate_box(self):
        words = [OcrWord(text="Start", x=10, y=20, w=30, h=12)]
        source = OcrCandidateSource(lambda frame: words)
        frame = _frame(left=100, top=200)
        candidates = source.candidates(frame, "Start")
        self.assertEqual(len(candidates), 1)
        # 帧像素 (10,20)-(40,32) + 帧原点 (100,200) → 物理 (110,220)-(140,232)
        self.assertEqual(candidates[0].box, (110, 220, 140, 232))
        self.assertEqual(candidates[0].center, (125.0, 226.0))
        self.assertEqual(candidates[0].number, 1)

    def test_matching_uses_substrings_and_adjacent_words(self):
        """描述里的词能在 OCR 词（或相邻词的拼接）里找到 → 命中。"""
        words = [
            OcrWord(text="Save", x=0, y=0, w=40, h=12),
            OcrWord(text="As", x=44, y=0, w=20, h=12),
            OcrWord(text="Other", x=0, y=40, w=40, h=12),
        ]
        source = OcrCandidateSource(lambda frame: words)
        # 「Save」与「As」相邻且成串出现在描述里 → 取**最长的一段**。
        candidates = source.candidates(_frame(), "click the Save As button")
        self.assertEqual([item.text for item in candidates], ["Save As"])
        # 描述里只有「Save」→ 只取到「Save」（不把「As」也算进来）。
        candidates = source.candidates(_frame(), "click the Save button")
        self.assertEqual([item.text for item in candidates], ["Save"])

    def test_a_short_fragment_does_not_match_inside_a_word(self):
        """「as」不该命中「task」里的 as（ASCII 的词按**词边界**匹配）。"""
        words = [OcrWord(text="as", x=0, y=0, w=20, h=12)]
        source = OcrCandidateSource(lambda frame: words)
        self.assertEqual(source.candidates(_frame(), "click the task bar"), [])

    def test_no_match_gives_no_candidate(self):
        words = [OcrWord(text="Other", x=0, y=0, w=40, h=12)]
        source = OcrCandidateSource(lambda frame: words)
        self.assertEqual(source.candidates(_frame(), "click the Save button"), [])


class UiaCandidateSourceTests(unittest.TestCase):
    """**UIA 主表**：控件框是**物理像素**、**无名控件不丢**。

    ⚠️ **无名控件给位置型占位描述** —— 静默丢弃就是「图标召回 0」的成因
    （图标本来就没有文字，OCR 原理上召不回）。
    """

    class _Control:
        def __init__(self, name: str, rect: tuple[int, int, int, int], control_type: str = ""):
            self.name = name
            self.rect = rect
            self.control_type = control_type

    def test_unnamed_controls_are_kept_with_a_positional_placeholder(self):
        controls = [
            self._Control("", (10, 10, 40, 40), "Button"),
            self._Control("", (60, 10, 90, 40), "Custom"),
        ]
        source = UiaCandidateSource(controls)
        candidates = source.candidates(_frame(), "开始")
        self.assertEqual(len(candidates), 2)
        self.assertTrue(all(item.text for item in candidates))
        self.assertIn("自上而下第 1 个", candidates[0].text)
        self.assertIn("自上而下第 2 个", candidates[1].text)

    def test_the_boxes_are_physical_pixels(self):
        controls = [self._Control("开始", (100, 200, 140, 240))]
        source = UiaCandidateSource(controls)
        candidates = source.candidates(_frame(left=50, top=60), "开始")
        # UIA 的框**已经是物理屏幕坐标** → **不加任何偏移**。
        self.assertEqual(candidates[0].box, (100, 200, 140, 240))
        self.assertEqual(candidates[0].source, "uia")

    def test_matching_names_come_first(self):
        controls = [
            self._Control("", (0, 0, 200, 200)),
            self._Control("开始", (300, 300, 340, 340)),
            self._Control("", (400, 400, 460, 460)),
        ]
        source = UiaCandidateSource(controls)
        candidates = source.candidates(_frame(), "开始")
        self.assertEqual(candidates[0].text, "开始")

    def test_the_list_is_capped(self):
        controls = [
            self._Control(f"项{index}", (index * 10, 0, index * 10 + 8, 8))
            for index in range(50)
        ]
        source = UiaCandidateSource(controls)
        candidates = source.candidates(_frame(), "项3")
        self.assertEqual(len(candidates), 20)
        self.assertEqual(candidates[0].number, 1)
        self.assertEqual(candidates[-1].number, 20)


class MergeCandidateSourcesTests(unittest.TestCase):
    """**合并源**（UFO² 的混合检测）：主表全留、附加表按 IoU 0.1 去重。"""

    def _uia(self, number: int, box) -> GroundingCandidate:
        return GroundingCandidate(number=number, text=f"控件{number}", box=box, source="uia")

    def _ocr(self, number: int, box) -> GroundingCandidate:
        return GroundingCandidate(number=number, text=f"文字{number}", box=box, source="ocr")

    def test_the_main_table_is_kept_and_the_overlapping_extra_is_dropped(self):
        """⚠️ **方向不能反**：UIA 框与 OCR 框重叠时**保留 UIA 框**。

        绝不能学 OmniParser 的「图标落在 OCR 框内就删图标」——
        那正好会**精确复现**「图标召回 0/13」。
        """
        merged = merge_candidate_sources(
            [self._uia(1, (0, 0, 40, 40))],
            [self._ocr(1, (2, 2, 30, 30))],
        )
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0].source, "uia")
        # **框留 UIA 的，字取 OCR 的**（方案：OCR 文字挂成它的 text）。
        self.assertEqual(merged[0].box, (0, 0, 40, 40))
        self.assertIn("控件1", merged[0].text)
        self.assertIn("文字1", merged[0].text)

    def test_a_non_overlapping_extra_is_appended_after_the_main_table(self):
        merged = merge_candidate_sources(
            [self._uia(1, (0, 0, 40, 40))],
            [self._ocr(1, (200, 200, 240, 240))],
        )
        self.assertEqual(len(merged), 2)
        self.assertEqual([item.source for item in merged], ["uia", "ocr"])
        # 编号**重新连续**（1 起）。
        self.assertEqual([item.number for item in merged], [1, 2])

    def test_the_iou_threshold_is_strictly_greater_than(self):
        """IoU **恰好等于 0.1 不算重叠**（UFO² 的口径：严格大于才丢）。"""
        near = (0, 0, 10, 10)
        #: 交集 2×10=20，并集 180 → IoU ≈ 0.111 > 0.1 → **丢**。
        overlapping = (8, 0, 18, 10)
        #: 交集 1×10=10，并集 190 → IoU ≈ 0.0526 < 0.1 → **留**。
        separate = (9, 0, 19, 10)
        merged = merge_candidate_sources(
            [self._uia(1, near)], [self._ocr(1, overlapping), self._ocr(2, separate)]
        )
        self.assertEqual(len(merged), 2)
        self.assertEqual(merged[1].box, separate)

    def test_the_comparison_is_in_physical_pixels(self):
        """比较**一律在物理像素空间**（红线①）：两个框坐标相同 → 必然重叠。"""
        merged = merge_candidate_sources(
            [self._uia(1, (100, 100, 140, 140))],
            [self._ocr(1, (100, 100, 140, 140))],
        )
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0].source, "uia")


class MergedCandidateSourceTests(unittest.TestCase):
    """通道侧：**单源 → 合并源**（UIA 主表 + OCR 附加表）。"""

    def _grounder(self, model: _StubModel, *, main=None, additional=None):
        return TwoStageGrounder(
            model,
            scale=ModelScale.RELATIVE_0_1000,
            model_input_size=(_FRAME_WIDTH, _FRAME_HEIGHT),
            full_screen_size=(_FRAME_WIDTH, _FRAME_HEIGHT),
            effort=Effort.LOW,
            gate_mode=GateMode.ALWAYS,
            candidate_source=main,
            additional_candidate_source=additional,
        )

    class _Source:
        def __init__(self, items):
            self._items = list(items)

        def candidates(self, frame, description):
            return list(self._items)

    def test_the_two_sources_are_merged(self):
        """主表全留；附加表里与主表重叠的**丢弃**，其余追加在后面。"""
        main = self._Source(
            [GroundingCandidate(number=1, text="甲", box=(0, 0, 20, 20), source="uia")]
        )
        additional = self._Source(
            [
                GroundingCandidate(number=1, text="重叠", box=(1, 1, 18, 18), source="ocr"),
                GroundingCandidate(number=2, text="别处", box=(30, 30, 50, 50), source="ocr"),
            ]
        )
        model = _StubModel(_reply(json.dumps({"chosen": 1})))
        grounder = self._grounder(model, main=main, additional=additional)
        result = grounder.locate(_request("甲", _frame()))
        self.assertTrue(result.ok, msg=result.error_message)
        # 清单里两项：主表的「甲」（**OCR 的重叠文字挂上去了**）+ 附加表里不重叠的「别处」。
        prompt = model.calls[0]["prompt"]
        self.assertIn("「甲", prompt)
        self.assertIn("重叠", prompt)
        self.assertIn("「别处」", prompt)

    def test_a_broken_additional_source_does_not_kill_the_main_one(self):
        """**判不了 ≠ 没有候选**：附加表报错时，主表照用。"""

        class _Broken:
            def candidates(self, frame, description):
                raise OSError("OCR 后端不可用")

        main = self._Source(
            [GroundingCandidate(number=1, text="甲", box=(0, 0, 20, 20), source="uia")]
        )
        model = _StubModel(_reply(json.dumps({"chosen": 1})))
        grounder = self._grounder(model, main=main, additional=_Broken())
        result = grounder.locate(_request("甲", _frame()))
        self.assertTrue(result.ok, msg=result.error_message)
        self.assertEqual(result.landing_point, (10, 10))

    def test_a_broken_main_source_falls_back_to_the_additional_one(self):
        """主表不可用 → **退回附加表**（不报错、不假装没有候选）。"""

        class _Broken:
            def candidates(self, frame, description):
                raise OSError("UIA 树读不出来")

        additional = self._Source(
            [GroundingCandidate(number=1, text="甲", box=(0, 0, 20, 20), source="ocr")]
        )
        model = _StubModel(_reply(json.dumps({"chosen": 1})))
        grounder = self._grounder(model, main=_Broken(), additional=additional)
        result = grounder.locate(_request("甲", _frame()))
        self.assertTrue(result.ok, msg=result.error_message)
        self.assertEqual(result.landing_point, (10, 10))


class NumberedBranchTests(unittest.TestCase):
    """**编号分支**：模型只答编号 → 落点 = 候选框的物理中心。"""

    def _grounder(self, model: _StubModel, source, **kwargs) -> TwoStageGrounder:
        return TwoStageGrounder(
            model,
            scale=ModelScale.RELATIVE_0_1000,
            model_input_size=kwargs.pop("model_input_size", (_FRAME_WIDTH, _FRAME_HEIGHT)),
            full_screen_size=(_FRAME_WIDTH, _FRAME_HEIGHT),
            effort=Effort.LOW,
            gate_mode=GateMode.ALWAYS,
            candidate_source=source,
            **kwargs,
        )

    def test_landing_is_the_chosen_candidate_box_center(self):
        class _Source:
            def candidates(self, frame, description):
                return [
                    GroundingCandidate(number=1, text="甲", box=(10, 10, 30, 20)),
                    GroundingCandidate(number=2, text="乙", box=(100, 200, 140, 240)),
                ]

        model = _StubModel(_reply(json.dumps({"chosen": 2})))
        grounder = self._grounder(model, _Source())
        result = grounder.locate(
            _request("乙", _frame(left=50, top=60))
        )
        self.assertTrue(result.ok, msg=result.error_message)
        # 落点 = 候选 2 的框中心（**零换算**，取整误差 ≤ 1 px）
        expected = (120.0, 220.0)
        self.assertLessEqual(
            ((result.landing_point[0] - expected[0]) ** 2
             + (result.landing_point[1] - expected[1]) ** 2) ** 0.5,
            1.0,
        )
        self.assertEqual(result.landing_point, (120, 220))
        # **只问了一次**模型（编号分支不发粗定位）。
        self.assertEqual(len(model.calls), 1)
        self.assertEqual(result.model_calls, 1)
        # 回显：stage="numbered" + 候选框中心 + 实际发送尺寸
        stages = {echo.stage: echo for echo in result.echoes}
        self.assertIn("numbered", stages)
        self.assertEqual((stages["numbered"].raw_x, stages["numbered"].raw_y), (120.0, 220.0))
        # 这一级**不开精定位、也没有裁剪计划**。
        self.assertFalse(result.refine_enabled)
        self.assertIsNone(result.crop_plan)

    def test_the_numbered_branch_reports_the_gate_as_undecidable(self):
        """编号分支**没有门控这一级** → 四条条件照实记成「不可判定」。

        ⚠️ 回空元组 = 说「门控判了、而且全部判得出来」→ 聚合出来的
        「``undecidable`` 数 = 0」这条验收线会被**白拿**。
        """

        class _Source:
            def candidates(self, frame, description):
                return [GroundingCandidate(number=1, text="甲", box=(10, 10, 30, 20))]

        model = _StubModel(_reply(json.dumps({"chosen": 1})))
        grounder = self._grounder(model, _Source())
        result = grounder.locate(_request("甲", _frame()))
        self.assertFalse(result.gate_hit)
        self.assertEqual(result.gate_reasons, ())
        self.assertEqual(
            set(result.gate_undecidable),
            {"target_area_ratio", "coarse_validity", "confidence", "consistency"},
        )

    def test_prompt_asks_for_a_number_only(self):
        class _Source:
            def candidates(self, frame, description):
                return [GroundingCandidate(number=1, text="甲", box=(10, 10, 30, 20))]

        model = _StubModel(_reply(json.dumps({"chosen": 1})))
        grounder = self._grounder(model, _Source())
        grounder.locate(_request("甲", _frame()))
        prompt = model.calls[0]["prompt"]
        self.assertIn("chosen", prompt)
        self.assertIn("只回答它的行号", prompt)
        self.assertNotIn("归一化", prompt)

    def test_unusable_number_reports_locate_failed(self):
        class _Source:
            def candidates(self, frame, description):
                return [GroundingCandidate(number=1, text="甲", box=(10, 10, 30, 20))]

        model = _StubModel(
            _reply(json.dumps({"chosen": 9})),
            _reply(json.dumps({"x": 10.0, "y": 10.0})),
            _reply(json.dumps({"x": 20.0, "y": 20.0})),
        )
        grounder = self._grounder(model, _Source())
        result = grounder.locate(_request("甲", _frame()))
        self.assertTrue(result.ok, msg=result.error_message)
        # **挑不出编号 → 回落点式路径**（有可回落的结果，不报死）。
        self.assertTrue(result.fallback)
        self.assertEqual(result.fallback_cause, "candidate_selection_failed")
        # 回落之后走的是点式路径（粗定位 + 精定位）。
        self.assertEqual(len(model.calls), 3)
        self.assertEqual(result.model_calls, 3)

    def test_answering_zero_means_no_candidate_matches(self):
        """模型答 **0** = 「清单里没有对应的」→ 同样回落点式路径。"""

        class _Source:
            def candidates(self, frame, description):
                return [GroundingCandidate(number=1, text="甲", box=(10, 10, 30, 20))]

        model = _StubModel(
            _reply(json.dumps({"chosen": 0})),
            _reply(json.dumps({"x": 10.0, "y": 10.0})),
            _reply(json.dumps({"x": 20.0, "y": 20.0})),
        )
        grounder = self._grounder(model, _Source())
        result = grounder.locate(_request("甲", _frame()))
        self.assertTrue(result.ok, msg=result.error_message)
        self.assertTrue(result.fallback)
        self.assertEqual(result.fallback_cause, "candidate_selection_failed")
        self.assertEqual(len(model.calls), 3)

    def test_a_failed_numbered_call_is_still_a_model_call(self):
        """编号分支**问过了**就算一次模型调用（回答解析失败也是问过了）。

        ⚠️ 不记它 →「平均模型调用数/目标」这条验收线**少算一次**。
        """

        class _Source:
            def candidates(self, frame, description):
                return [GroundingCandidate(number=1, text="甲", box=(10, 10, 30, 20))]

        model = _StubModel(
            RuntimeError("模型回答里没有编号"),
            _reply(json.dumps({"x": 10.0, "y": 10.0})),
            _reply(json.dumps({"x": 20.0, "y": 20.0})),
        )
        grounder = self._grounder(model, _Source())
        result = grounder.locate(_request("甲", _frame()))
        # 编号分支炸了 → 回落点式路径（粗定位 + 精定位）。
        self.assertTrue(result.fallback)
        self.assertEqual(result.fallback_cause, "candidate_selection_failed")
        self.assertEqual(len(model.calls), 3)
        # **炸掉的那一次也算一次模型调用**（问过了）。
        self.assertEqual(result.model_calls, 3)

    def test_the_numbered_call_gets_its_own_budget(self):
        """**编号分支单独给一份预算**（默认 4000）—— 思考链与输出共用预算。"""

        class _Source:
            def candidates(self, frame, description):
                return [GroundingCandidate(number=1, text="甲", box=(10, 10, 30, 20))]

        model = _StubModel(_reply(json.dumps({"chosen": 1})))
        grounder = self._grounder(model, _Source())
        grounder.locate(_request("甲", _frame()))
        self.assertEqual(model.calls[0]["max_tokens"], 4000)

    def test_without_candidates_the_point_path_is_used_and_recorded(self):
        """**无候选** → 走点式分支，并**显式记录「无候选」**（不猜、不假装）。"""

        class _EmptySource:
            def candidates(self, frame, description):
                return []

        model = _StubModel(
            _reply(json.dumps({"x": 10.0, "y": 10.0})),
            _reply(json.dumps({"x": 20.0, "y": 20.0})),
        )
        grounder = self._grounder(model, _EmptySource())
        result = grounder.locate(_request("甲", _frame()))
        self.assertTrue(result.ok, msg=result.error_message)
        stages = [echo.stage for echo in result.echoes]
        self.assertIn("no_candidates", stages)
        self.assertIn("coarse", stages)
        # 走的是**点式**分支（粗定位 + 精定位）。
        self.assertEqual(len(model.calls), 2)

    def test_broken_candidate_source_is_recorded_not_guessed(self):
        class _BrokenSource:
            def candidates(self, frame, description):
                raise OSError("OCR 后端不可用")

        model = _StubModel(
            _reply(json.dumps({"x": 10.0, "y": 10.0})),
            _reply(json.dumps({"x": 20.0, "y": 20.0})),
        )
        grounder = self._grounder(model, _BrokenSource())
        result = grounder.locate(_request("甲", _frame()))
        self.assertTrue(result.ok, msg=result.error_message)
        stages = {echo.stage: echo for echo in result.echoes}
        self.assertIn("no_candidates", stages)
        self.assertIn("OCR 后端不可用", stages["no_candidates"].image_source)

    def test_without_a_candidate_source_nothing_changes(self):
        """**默认没有候选源** → 行为与原来完全一致（连回显都不多一条）。"""
        model = _StubModel(
            _reply(json.dumps({"x": 10.0, "y": 10.0})),
            _reply(json.dumps({"x": 20.0, "y": 20.0})),
        )
        grounder = self._grounder(model, None)
        result = grounder.locate(_request("甲", _frame()))
        self.assertTrue(result.ok, msg=result.error_message)
        self.assertEqual([echo.stage for echo in result.echoes], ["coarse", "refine"])


class WholeScreenDownscaleTests(unittest.TestCase):
    """**A-2**：整屏那一级自己缩到 ``model_input_size``；crop 那一级**不缩放**。"""

    def test_whole_screen_image_is_downscaled_and_crop_is_not(self):
        frame = CapturedFrame(
            width=1024,
            height=512,
            left=0,
            top=0,
            payload=bytes(1024 * 512 * 3),
            backend="fake",
            source="synthetic",
            pixel_format=PixelFormat.BGR,
        )
        model = _StubModel(
            _reply(json.dumps({"x": 10.0, "y": 10.0})),
            _reply(json.dumps({"x": 20.0, "y": 20.0})),
        )
        grounder = TwoStageGrounder(
            model,
            scale=ModelScale.RELATIVE_0_1000,
            model_input_size=(512, 256),
            full_screen_size=(1024, 512),
            effort=Effort.LOW,
            gate_mode=GateMode.ALWAYS,
        )
        result = grounder.locate(_request("甲", frame))
        self.assertTrue(result.ok, msg=result.error_message)
        # 粗定位那张图 = 缩放后的整屏（512×256）。
        self.assertEqual(_png_size(model.calls[0]["image_base64"]), (512, 256))
        # 精定位那张图 = 从**原图**上 1:1 裁的 crop（**不缩放**）。
        self.assertEqual(
            _png_size(model.calls[1]["image_base64"]), default_crop_size()
        )
        stages = {echo.stage: echo for echo in result.echoes}
        self.assertEqual((stages["coarse"].sent_width, stages["coarse"].sent_height), (512, 256))
        self.assertEqual(
            (stages["refine"].sent_width, stages["refine"].sent_height),
            default_crop_size(),
        )


class PromptTests(unittest.TestCase):
    """**提示词**与**目标描述规范化**。"""

    def test_numbered_prompt_lists_numbered_candidates(self):
        lines = [
            CandidateLine(number=1, text="Save", box=(10, 20, 30, 40)),
            CandidateLine(number=2, text="Save As", box=(50, 60, 90, 80)),
        ]
        prompt = numbered_candidates_prompt("保存", lines, image_size=(1280, 800))
        self.assertIn("1. 「Save」", prompt)
        self.assertIn("2. 「Save As」", prompt)
        self.assertIn("(10, 20) - (30, 40)", prompt)
        self.assertIn("1280×800", prompt)
        self.assertIn('{"chosen": <行号>}', prompt)

    def test_chosen_numbers_reuses_the_uia_reader(self):
        """**回答的读法只有一份**（复用 UIA 通道那一份）。"""
        from minicode.uia.pointer import parse_chosen_numbers as uia_reader

        self.assertIs(parse_chosen_numbers, uia_reader)
        self.assertEqual(parse_chosen_numbers('{"chosen": 3}'), (3,))
        self.assertEqual(parse_chosen_numbers("第 2 项"), (2,))
        self.assertEqual(parse_chosen_numbers("我随便说个 42"), ())

    def test_normalize_target_keeps_a_matching_description(self):
        self.assertEqual(normalize_target("开始", ["开始", "取消"]), ("开始", False))

    def test_normalize_target_replaces_a_near_name(self):
        """近名（错字级）→ 换成界面上的原名。"""
        normalized, changed = normalize_target("Sav", ["Save", "Cancel"])
        self.assertTrue(changed)
        self.assertEqual(normalized, "Save")

    def test_normalize_target_does_not_invent_a_synonym(self):
        """「显示」→「屏幕」是**同义不同名** → 找不到就**如实说不改**。"""
        self.assertEqual(normalize_target("显示", ["屏幕", "蓝牙"]), ("显示", False))

    def test_normalize_target_does_not_change_when_ambiguous(self):
        normalized, changed = normalize_target("ab", ["ac", "ad"])
        self.assertFalse(changed)
        self.assertEqual(normalized, "ab")


class TargetNormalizationTests(unittest.TestCase):
    """**B-4 的目标描述规范化**：先对齐名字，再定位。"""

    def _grounder(self, model: _StubModel, **kwargs) -> TwoStageGrounder:
        return TwoStageGrounder(
            model,
            scale=ModelScale.RELATIVE_0_1000,
            model_input_size=(_FRAME_WIDTH, _FRAME_HEIGHT),
            full_screen_size=(_FRAME_WIDTH, _FRAME_HEIGHT),
            effort=Effort.LOW,
            gate_mode=GateMode.ALWAYS,
            **kwargs,
        )

    def test_without_names_nothing_is_normalized(self):
        """**默认不开**规范化 → 不多花那次模型调用。"""
        model = _StubModel(
            _reply(json.dumps({"x": 10.0, "y": 10.0})),
            _reply(json.dumps({"x": 20.0, "y": 20.0})),
        )
        grounder = self._grounder(model)
        grounder.locate(_request("显示", _frame()))
        self.assertEqual(len(model.calls), 2)  # 只有粗定位 + 精定位

    def test_the_model_picks_the_name_when_the_local_match_fails(self):
        """本地近名匹配不上 → **模型侧**给几个名字让它选。"""
        model = _StubModel(
            _reply(json.dumps({"chosen": 2})),
            _reply(json.dumps({"x": 10.0, "y": 10.0})),
            _reply(json.dumps({"x": 20.0, "y": 20.0})),
        )
        grounder = self._grounder(
            model, target_names=lambda frame: ["蓝牙", "屏幕", "夜间模式"]
        )
        grounder.locate(_request("显示", _frame()))
        # 第一次调用是**名字选择**（纯文字，不发图）。
        self.assertIn("显示", model.calls[0]["prompt"])
        self.assertIn("屏幕", model.calls[0]["prompt"])
        self.assertIsNone(model.calls[0]["image_base64"])
        # 规范化后的描述进了后面的提示词。
        self.assertIn("屏幕", model.calls[1]["prompt"])

    def test_the_local_match_is_used_without_a_model_call(self):
        """本地近名匹配上了 → **不花**那次模型调用。"""
        model = _StubModel(
            _reply(json.dumps({"x": 10.0, "y": 10.0})),
            _reply(json.dumps({"x": 20.0, "y": 20.0})),
        )
        grounder = self._grounder(model, target_names=lambda frame: ["Sav", "Cancel"])
        grounder.locate(_request("Sav", _frame()))
        self.assertEqual(len(model.calls), 2)  # 没有多出那次名字选择

    def test_a_failed_name_choice_keeps_the_original_description(self):
        """模型没给出有效编号 → **如实不改**（保留原描述）。"""
        model = _StubModel(
            _reply("我看不出来"),
            _reply(json.dumps({"x": 10.0, "y": 10.0})),
            _reply(json.dumps({"x": 20.0, "y": 20.0})),
        )
        grounder = self._grounder(
            model, target_names=lambda frame: ["蓝牙", "屏幕", "夜间模式"]
        )
        result = grounder.locate(_request("显示", _frame()))
        self.assertTrue(result.ok, msg=result.error_message)
        self.assertIn("显示", model.calls[1]["prompt"])


def _request(description: str, frame: CapturedFrame):
    from minicode.action.ports import VisualLocateRequest

    return VisualLocateRequest(
        target_description=description,
        frame=frame,
        surface=(0, 0, frame.width, frame.height),
    )


if __name__ == "__main__":
    unittest.main()

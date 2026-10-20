"""UIA 通道的单测（工单 M3 AC 1、AC 2、AC 4~AC 8、AC 12）。

只测**外部行为**：一句目标描述进去，一个选中项 + 一个物理像素落点出来；
失败时给出**哪一类**失败与**为什么**。不测内部实现细节。
"""

from __future__ import annotations

import unittest

from minicode.action.errors import ErrorCode
from minicode.action.ports import LocateRequest, WindowAnchor
from minicode.redact.config import ModelContextConfig
from minicode.redact.redactor import TreeRedactor
from minicode.uia.capability import CapabilityTable
from minicode.uia.channel import UiaChannel
from minicode.uia.elements import UiaElement
from minicode.uia.fakes import (
    FakeCandidatePointer,
    FakeCapabilityProbe,
    FakeUiaQueryPort,
    ScriptedRead,
)
from minicode.uia.ports import TreeAssessment

_ANCHOR = WindowAnchor(hwnd=1234, app="explorer", version="10.0.26100")


def _element(name: str = "开始", **kwargs) -> UiaElement:
    payload = {
        "name": name,
        "control_type": "Button",
        "class_name": "Button",
        "rect": (1000, 1520, 1080, 1600),
        "window_handle": 1234,
    }
    payload.update(kwargs)
    return UiaElement(**payload)


def _channel(
    reads,
    *,
    pointer=None,
    redactor=None,
    assessment=None,
    max_candidates=8,
) -> UiaChannel:
    probe = FakeCapabilityProbe((assessment,) if assessment else ())
    table = CapabilityTable(probe, sleeper=lambda seconds: None)
    return UiaChannel(
        query=FakeUiaQueryPort(reads),
        capability=table,
        pointer=pointer,
        redactor=redactor,
        max_candidates=max_candidates,
    )


def _redactor() -> TreeRedactor:
    return TreeRedactor(
        ModelContextConfig(send_tree_to_model=True, redaction_enabled=True)
    )


class UniqueCandidateTest(unittest.TestCase):
    """AC 1 / AC 2：候选唯一 → 零模型调用；落点取控件框中心。"""

    def test_a_unique_candidate_is_selected_without_any_model_call(self):
        channel = _channel([ScriptedRead(elements=(_element(),))])
        result = channel.locate(
            LocateRequest(target_description="开始", anchor=_ANCHOR)
        )
        self.assertIsNone(result.error_code)
        self.assertEqual(result.model_calls, 0)
        self.assertIsNotNone(result.selected)
        self.assertEqual(result.selected.name, "开始")

    def test_the_landing_point_is_the_center_of_the_control_rect(self):
        channel = _channel(
            [ScriptedRead(elements=(_element(rect=(1000, 1520, 1080, 1600)),))]
        )
        result = channel.locate(
            LocateRequest(target_description="开始", anchor=_ANCHOR)
        )
        self.assertEqual(result.landing_point, (1040, 1560))

    def test_the_landing_point_is_within_two_pixels_of_the_rect_center(self):
        rect = (1001, 1523, 1079, 1597)
        channel = _channel([ScriptedRead(elements=(_element(rect=rect),))])
        result = channel.locate(
            LocateRequest(target_description="开始", anchor=_ANCHOR)
        )
        center = ((rect[0] + rect[2]) / 2.0, (rect[1] + rect[3]) / 2.0)
        delta = (
            (result.landing_point[0] - center[0]) ** 2
            + (result.landing_point[1] - center[1]) ** 2
        ) ** 0.5
        self.assertLessEqual(delta, 2.0)


class InvalidRectTest(unittest.TestCase):
    """AC 4：全 0 / 零面积的候选被丢弃。"""

    def test_an_all_zero_candidate_is_dropped(self):
        channel = _channel(
            [ScriptedRead(elements=(_element(rect=(0, 0, 0, 0)),))]
        )
        result = channel.locate(
            LocateRequest(target_description="开始", anchor=_ANCHOR)
        )
        self.assertEqual(result.error_code, ErrorCode.TARGET_NOT_FOUND)
        self.assertIsNone(result.landing_point)
        self.assertIsNone(result.selected)

    def test_a_zero_area_candidate_is_dropped(self):
        channel = _channel(
            [ScriptedRead(elements=(_element(rect=(10, 10, 10, 60)),))]
        )
        result = channel.locate(
            LocateRequest(target_description="开始", anchor=_ANCHOR)
        )
        self.assertEqual(result.error_code, ErrorCode.TARGET_NOT_FOUND)
        self.assertIsNone(result.landing_point)

    def test_no_landing_point_at_the_origin_is_produced(self):
        channel = _channel(
            [ScriptedRead(elements=(_element(rect=(0, 0, 0, 0)),))]
        )
        result = channel.locate(
            LocateRequest(target_description="开始", anchor=_ANCHOR)
        )
        self.assertNotEqual(result.landing_point, (0, 0))

    def test_an_invalid_candidate_does_not_hide_a_valid_one(self):
        channel = _channel(
            [
                ScriptedRead(
                    elements=(
                        _element(name="开始", rect=(0, 0, 0, 0)),
                        _element(name="开始", rect=(10, 20, 110, 60)),
                    )
                )
            ]
        )
        result = channel.locate(
            LocateRequest(target_description="开始", anchor=_ANCHOR)
        )
        self.assertIsNone(result.error_code)
        self.assertEqual(result.landing_point, (60, 40))


class DisambiguationTest(unittest.TestCase):
    """AC 5：候选不唯一 → 先消歧；消歧不成 → 唯一性拒绝（零动作）。"""

    def _two_candidates(self) -> ScriptedRead:
        return ScriptedRead(
            elements=(
                _element(name="开始", rect=(1000, 1520, 1080, 1600)),
                _element(name="开始", rect=(2000, 1520, 2080, 1600)),
            )
        )

    def test_the_pointer_is_asked_when_the_candidates_are_not_unique(self):
        pointer = FakeCandidatePointer()
        channel = _channel([self._two_candidates()], pointer=pointer, redactor=_redactor())
        result = channel.locate(
            LocateRequest(target_description="开始", anchor=_ANCHOR)
        )
        self.assertEqual(pointer.calls, 1)
        self.assertEqual(result.model_calls, 1)

    def test_the_pointer_only_ever_sees_the_candidate_set(self):
        pointer = FakeCandidatePointer()
        channel = _channel([self._two_candidates()], pointer=pointer, redactor=_redactor())
        channel.locate(LocateRequest(target_description="开始", anchor=_ANCHOR))
        request = pointer.requests[0]
        self.assertEqual(len(request.candidates), 2)

    def test_without_a_redactor_the_model_is_never_asked(self):
        # fail-closed：脱敏没上线，不许把候选集送给模型（架构决策备忘 §2.6 C-15）。
        pointer = FakeCandidatePointer()
        channel = _channel([self._two_candidates()], pointer=pointer)
        result = channel.locate(
            LocateRequest(target_description="开始", anchor=_ANCHOR)
        )
        self.assertEqual(pointer.calls, 0)
        self.assertEqual(result.error_code, ErrorCode.AMBIGUOUS_TARGET)

    def test_a_unique_pointer_answer_selects_that_candidate(self):
        pointer = FakeCandidatePointer(chosen_ids=("stub",))
        channel = _channel(
            [self._two_candidates()], pointer=pointer, redactor=_redactor()
        )
        # 指认必须落在候选集内：先跑一次拿到 id，再按 id 指认。
        probe = channel.locate(LocateRequest(target_description="开始", anchor=_ANCHOR))
        ids = [candidate.element_id for candidate in probe.candidates]
        pointer.chosen_ids = (ids[1],)
        channel = _channel(
            [self._two_candidates()], pointer=pointer, redactor=_redactor()
        )
        result = channel.locate(
            LocateRequest(target_description="开始", anchor=_ANCHOR)
        )
        self.assertIsNone(result.error_code)
        self.assertEqual(result.landing_point, (2040, 1560))

    def test_an_empty_pointer_answer_is_a_uniqueness_rejection(self):
        pointer = FakeCandidatePointer(chosen_ids=())
        channel = _channel([self._two_candidates()], pointer=pointer)
        result = channel.locate(
            LocateRequest(target_description="开始", anchor=_ANCHOR)
        )
        self.assertEqual(result.error_code, ErrorCode.AMBIGUOUS_TARGET)
        self.assertIsNone(result.landing_point)

    def test_an_answer_outside_the_candidate_set_is_a_uniqueness_rejection(self):
        pointer = FakeCandidatePointer(chosen_ids=("obs1#99",))
        channel = _channel([self._two_candidates()], pointer=pointer)
        result = channel.locate(
            LocateRequest(target_description="开始", anchor=_ANCHOR)
        )
        self.assertEqual(result.error_code, ErrorCode.AMBIGUOUS_TARGET)
        self.assertIsNone(result.landing_point)

    def test_two_answers_are_a_uniqueness_rejection(self):
        pointer = FakeCandidatePointer(chosen_ids=("a", "b"))
        channel = _channel([self._two_candidates()], pointer=pointer)
        result = channel.locate(
            LocateRequest(target_description="开始", anchor=_ANCHOR)
        )
        self.assertEqual(result.error_code, ErrorCode.AMBIGUOUS_TARGET)
        self.assertIsNone(result.landing_point)

    def test_the_rejection_reports_each_candidate_rect_and_id(self):
        pointer = FakeCandidatePointer(chosen_ids=())
        channel = _channel([self._two_candidates()], pointer=pointer)
        result = channel.locate(
            LocateRequest(target_description="开始", anchor=_ANCHOR)
        )
        self.assertEqual(len(result.candidates), 2)
        self.assertEqual(
            [candidate.rect for candidate in result.candidates],
            [(1000, 1520, 1080, 1600), (2000, 1520, 2080, 1600)],
        )
        self.assertEqual(len({c.element_id for c in result.candidates}), 2)

    def test_without_a_pointer_the_channel_refuses_to_act(self):
        channel = _channel([self._two_candidates()])
        result = channel.locate(
            LocateRequest(target_description="开始", anchor=_ANCHOR)
        )
        self.assertEqual(result.error_code, ErrorCode.AMBIGUOUS_TARGET)
        self.assertEqual(result.model_calls, 0)

    def test_too_many_candidates_refuses_without_calling_the_model(self):
        pointer = FakeCandidatePointer()
        elements = tuple(
            _element(name="开始", rect=(100 * index, 0, 100 * index + 50, 40))
            for index in range(9)
        )
        channel = _channel([ScriptedRead(elements=elements)], pointer=pointer)
        result = channel.locate(
            LocateRequest(target_description="开始", anchor=_ANCHOR)
        )
        self.assertEqual(result.error_code, ErrorCode.TOO_MANY_CANDIDATES)
        self.assertEqual(pointer.calls, 0)
        self.assertEqual(result.model_calls, 0)
        self.assertEqual(len(result.candidates), 9)
        self.assertIsNone(result.landing_point)


class ChannelFailureTest(unittest.TestCase):
    """AC 6：通道级失败两态（0 节点 / 超时）——不是「目标不存在」。"""

    def test_zero_nodes_means_the_channel_is_unavailable(self):
        channel = _channel([ScriptedRead(elements=(), nodes=False)])
        result = channel.locate(
            LocateRequest(target_description="开始", anchor=_ANCHOR)
        )
        self.assertEqual(result.error_code, ErrorCode.LOCATE_FAILED)
        self.assertNotEqual(result.error_code, ErrorCode.TARGET_NOT_FOUND)

    def test_a_timeout_is_also_a_channel_level_failure(self):
        channel = _channel([ScriptedRead(timed_out=True)])
        result = channel.locate(
            LocateRequest(target_description="开始", anchor=_ANCHOR)
        )
        self.assertEqual(result.error_code, ErrorCode.LOCATE_FAILED)
        self.assertIsNone(result.landing_point)

    def test_a_channel_failure_costs_no_model_call(self):
        pointer = FakeCandidatePointer()
        channel = _channel([ScriptedRead(timed_out=True)], pointer=pointer)
        result = channel.locate(
            LocateRequest(target_description="开始", anchor=_ANCHOR)
        )
        self.assertEqual(pointer.calls, 0)
        self.assertEqual(result.model_calls, 0)

    def test_a_readable_tree_without_the_target_is_a_coverage_failure(self):
        channel = _channel(
            [ScriptedRead(elements=(_element(name="别的按钮"),), nodes=True)]
        )
        result = channel.locate(
            LocateRequest(target_description="开始", anchor=_ANCHOR)
        )
        self.assertEqual(result.error_code, ErrorCode.TARGET_NOT_FOUND)


class RereadBeforeExecutionTest(unittest.TestCase):
    """AC 7：执行前重读 + 元素 id 每次观测重新生成。"""

    def test_the_tree_is_read_again_before_the_action(self):
        channel = _channel(
            [
                ScriptedRead(elements=(_element(),)),
                ScriptedRead(elements=(_element(),)),
            ]
        )
        result = channel.locate(
            LocateRequest(target_description="开始", anchor=_ANCHOR)
        )
        self.assertIsNone(result.error_code)
        self.assertEqual(result.observations, 2)

    def test_a_changed_tree_means_a_relocation_failure(self):
        channel = _channel(
            [
                ScriptedRead(elements=(_element(),)),
                ScriptedRead(elements=(_element(name="别的控件"),)),
            ]
        )
        result = channel.locate(
            LocateRequest(target_description="开始", anchor=_ANCHOR)
        )
        self.assertEqual(result.error_code, ErrorCode.RELOCATE_FAILED)
        self.assertIsNone(result.landing_point)

    def test_the_landing_point_comes_from_the_second_read(self):
        channel = _channel(
            [
                ScriptedRead(elements=(_element(rect=(1000, 1520, 1080, 1600)),)),
                ScriptedRead(elements=(_element(rect=(1000, 1520, 1080, 1608)),)),
            ]
        )
        result = channel.locate(
            LocateRequest(target_description="开始", anchor=_ANCHOR)
        )
        self.assertEqual(result.landing_point, (1040, 1564))

    def test_the_previous_observation_element_id_does_not_resolve(self):
        channel = _channel(
            [
                ScriptedRead(elements=(_element(),)),
                ScriptedRead(elements=(_element(),)),
            ]
        )
        result = channel.locate(
            LocateRequest(target_description="开始", anchor=_ANCHOR)
        )
        candidate_ids = {candidate.element_id for candidate in result.candidates}
        self.assertTrue(candidate_ids)
        # 选中项来自**第二次**观测：它的元素 id 不在第一次观测的 id 集合里。
        self.assertNotIn(result.selected.element_id, candidate_ids)


class ConditionalQueryTest(unittest.TestCase):
    """AC 8：只做定点查询（带属性条件）。"""

    def test_the_query_carries_a_property_condition(self):
        channel = _channel([ScriptedRead(elements=(_element(),))])
        channel.locate(LocateRequest(target_description="开始", anchor=_ANCHOR))
        request = channel.query.requests[0]
        self.assertTrue(request.has_condition)

    def test_an_unconditional_query_is_refused(self):
        from minicode.uia.errors import UnconditionalQueryError
        from minicode.uia.ports import UiaFindRequest

        channel = _channel([])
        with self.assertRaises(UnconditionalQueryError):
            channel.query.find(UiaFindRequest(anchor=_ANCHOR))


class FailureClassificationTest(unittest.TestCase):
    """AC 12：三类失败分开报。"""

    def test_the_three_failure_kinds_have_three_different_codes(self):
        coverage = _channel([ScriptedRead(elements=(), nodes=True)])
        self.assertEqual(
            coverage.locate(
                LocateRequest(target_description="开始", anchor=_ANCHOR)
            ).error_code,
            ErrorCode.TARGET_NOT_FOUND,
        )

        selection = _channel(
            [
                ScriptedRead(
                    elements=(_element(), _element(rect=(2000, 0, 2100, 40)))
                )
            ]
        )
        self.assertEqual(
            selection.locate(
                LocateRequest(target_description="开始", anchor=_ANCHOR)
            ).error_code,
            ErrorCode.AMBIGUOUS_TARGET,
        )

        channel_level = _channel([ScriptedRead(elements=(), nodes=False)])
        self.assertEqual(
            channel_level.locate(
                LocateRequest(target_description="开始", anchor=_ANCHOR)
            ).error_code,
            ErrorCode.LOCATE_FAILED,
        )


class CapabilityGateTest(unittest.TestCase):
    """能力表判不可用时，通道直接报通道级失败。"""

    def test_an_unavailable_app_is_a_channel_level_failure(self):
        channel = _channel(
            [ScriptedRead(elements=(_element(),))],
            assessment=TreeAssessment(has_tree=False),
        )
        result = channel.locate(
            LocateRequest(target_description="开始", anchor=_ANCHOR)
        )
        self.assertEqual(result.error_code, ErrorCode.LOCATE_FAILED)

    def test_a_sparse_app_is_still_usable(self):
        channel = _channel(
            [ScriptedRead(elements=(_element(),))],
            assessment=TreeAssessment(
                has_tree=True,
                class_names=("Chrome_WidgetWin_1",),
                interactive_class_names=(),
                has_automation_id=False,
                name_collisions=2,
            ),
        )
        result = channel.locate(
            LocateRequest(target_description="开始", anchor=_ANCHOR)
        )
        self.assertIsNone(result.error_code)
        self.assertEqual(result.observations, 2)


if __name__ == "__main__":
    unittest.main()

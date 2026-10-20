"""**动作执行缝**上的 UIA 通道单测（工单 M3 AC 1、AC 2、AC 5、AC 12）。

全程假实现：不碰真桌面、不需要 API key、不产生费用。
"""

from __future__ import annotations

import unittest

from minicode.action.errors import ErrorCode
from minicode.action.fakes import FakeCaptureSource, FakeInputInjector
from minicode.action.ports import LocateRequest, WindowAnchor
from minicode.action.protocol import ActionKind
from minicode.action.records import ListTraceSink, build_record
from minicode.action.seam import ActionExecutionSeam, SeamRequest
from minicode.uia.capability import CapabilityTable
from minicode.uia.channel import UiaChannel
from minicode.uia.elements import UiaElement
from minicode.uia.fakes import (
    FakeCandidatePointer,
    FakeCapabilityProbe,
    FakeUiaQueryPort,
    ScriptedRead,
)

_ANCHOR = WindowAnchor(hwnd=1234, app="explorer", version="10.0.26100")
_RECT = (1000, 1520, 1080, 1600)


def _element(name: str = "开始", **kwargs) -> UiaElement:
    payload = {
        "name": name,
        "control_type": "Button",
        "class_name": "Button",
        "rect": _RECT,
        "window_handle": 1234,
    }
    payload.update(kwargs)
    return UiaElement(**payload)


def _seam(channel: UiaChannel, injector: FakeInputInjector, sink: ListTraceSink):
    return ActionExecutionSeam(
        capture_sources=[FakeCaptureSource(width=2560, height=1600)],
        tree=None,
        injector=injector,
        trace_sink=sink,
        locator=channel,
        anchor=_ANCHOR,
    )


def _channel(reads, *, pointer=None):
    table = CapabilityTable(FakeCapabilityProbe(), sleeper=lambda seconds: None)
    return UiaChannel(
        query=FakeUiaQueryPort(reads),
        capability=table,
        pointer=pointer,
    )


class SeamLandingTest(unittest.TestCase):
    """AC 2：假输入注入记录的最终落点 = 选中项控件框中心（物理像素）。"""

    def test_the_injected_point_is_the_control_rect_center(self):
        sink = ListTraceSink()
        injector = FakeInputInjector()
        seam = _seam(_channel([ScriptedRead(elements=(_element(),))]), injector, sink)
        result = seam.run(SeamRequest(target_description="开始"))
        self.assertTrue(result.ok)
        moves = [event for event in injector.events if event[0] == "move"]
        self.assertEqual(moves[-1], ("move", 1040, 1560))
        self.assertEqual(result.final_point, (1040, 1560))

    def test_the_landing_point_is_within_two_pixels_of_the_rect_center(self):
        sink = ListTraceSink()
        injector = FakeInputInjector()
        seam = _seam(_channel([ScriptedRead(elements=(_element(),))]), injector, sink)
        result = seam.run(SeamRequest(target_description="开始"))
        center = ((_RECT[0] + _RECT[2]) / 2.0, (_RECT[1] + _RECT[3]) / 2.0)
        delta = (
            (result.final_point[0] - center[0]) ** 2
            + (result.final_point[1] - center[1]) ** 2
        ) ** 0.5
        self.assertLessEqual(delta, 2.0)


class SeamZeroModelCallTest(unittest.TestCase):
    """AC 1：零模型调用 + 追踪记录里没有视觉通道的产物。"""

    def test_the_trace_has_no_visual_channel_fields(self):
        sink = ListTraceSink()
        injector = FakeInputInjector()
        seam = _seam(_channel([ScriptedRead(elements=(_element(),))]), injector, sink)
        seam.run(SeamRequest(target_description="开始"))
        trace = sink.traces[0]
        for field in ("scale", "coarse_point", "crop_plan", "标度", "粗定位点", "裁剪计划"):
            self.assertFalse(hasattr(trace, field), field)

    def test_the_record_has_no_visual_channel_fields(self):
        sink = ListTraceSink()
        injector = FakeInputInjector()
        seam = _seam(_channel([ScriptedRead(elements=(_element(),))]), injector, sink)
        seam.run(SeamRequest(target_description="开始"))
        payload = sink.traces[0].result.to_dict()
        for key in ("scale", "coarse_point", "crop_plan"):
            self.assertNotIn(key, payload)

    def test_a_unique_candidate_costs_no_model_call(self):
        sink = ListTraceSink()
        pointer = FakeCandidatePointer()
        injector = FakeInputInjector()
        seam = _seam(
            _channel([ScriptedRead(elements=(_element(),))], pointer=pointer),
            injector,
            sink,
        )
        seam.run(SeamRequest(target_description="开始"))
        self.assertEqual(pointer.calls, 0)

    def test_the_decision_segment_costs_nothing(self):
        sink = ListTraceSink()
        injector = FakeInputInjector()
        seam = _seam(_channel([ScriptedRead(elements=(_element(),))]), injector, sink)
        seam.run(SeamRequest(target_description="开始"))
        self.assertEqual(sink.traces[0].decision_ms, 0.0)

    def test_the_locate_segment_is_measured(self):
        sink = ListTraceSink()
        injector = FakeInputInjector()
        seam = _seam(_channel([ScriptedRead(elements=(_element(),))]), injector, sink)
        seam.run(SeamRequest(target_description="开始"))
        self.assertGreater(sink.traces[0].locate_ms, 0.0)


class SeamUniquenessRejectionTest(unittest.TestCase):
    """AC 5：唯一性拒绝 → 零输入注入 + 回报候选集。"""

    def test_no_input_is_injected_when_the_candidates_are_not_unique(self):
        sink = ListTraceSink()
        injector = FakeInputInjector()
        elements = (_element(), _element(rect=(2000, 1520, 2080, 1600)))
        seam = _seam(_channel([ScriptedRead(elements=elements)]), injector, sink)
        result = seam.run(SeamRequest(target_description="开始"))
        self.assertFalse(result.ok)
        self.assertEqual(result.result.error_code, ErrorCode.AMBIGUOUS_TARGET)
        self.assertEqual([e for e in injector.events if e[0] == "button_down"], [])

    def test_the_record_reports_each_candidate_rect_and_id(self):
        sink = ListTraceSink()
        injector = FakeInputInjector()
        elements = (_element(), _element(rect=(2000, 1520, 2080, 1600)))
        seam = _seam(_channel([ScriptedRead(elements=elements)]), injector, sink)
        seam.run(SeamRequest(target_description="开始"))
        record = build_record(sink.traces[0]).to_dict()
        rejected = record["rejected_candidates"]
        self.assertEqual(len(rejected), 2)
        self.assertEqual(rejected[0]["rect"], list(_RECT))
        self.assertTrue(rejected[0]["element_id"])
        self.assertNotEqual(rejected[0]["element_id"], rejected[1]["element_id"])


class SeamFailureClassificationTest(unittest.TestCase):
    """AC 12：三类失败分开报，且都归「定位失败」。"""

    def _trace(self, reads):
        sink = ListTraceSink()
        injector = FakeInputInjector()
        seam = _seam(_channel(reads), injector, sink)
        seam.run(SeamRequest(target_description="开始"))
        return sink.traces[0]

    def test_a_coverage_failure_is_a_locate_failure(self):
        trace = self._trace([ScriptedRead(elements=(_element(name="别的"),), nodes=True)])
        self.assertEqual(trace.failures["locate"], ErrorCode.TARGET_NOT_FOUND.value)
        self.assertIsNone(trace.failures["execute"])

    def test_a_selection_failure_is_a_locate_failure(self):
        elements = (_element(), _element(rect=(2000, 1520, 2080, 1600)))
        trace = self._trace([ScriptedRead(elements=elements)])
        self.assertEqual(trace.failures["locate"], ErrorCode.AMBIGUOUS_TARGET.value)
        self.assertIsNone(trace.failures["execute"])

    def test_a_channel_level_failure_is_a_locate_failure(self):
        trace = self._trace([ScriptedRead(elements=(), nodes=False)])
        self.assertEqual(trace.failures["locate"], ErrorCode.LOCATE_FAILED.value)
        self.assertIsNone(trace.failures["execute"])

    def test_the_decision_failure_is_always_empty(self):
        trace = self._trace([ScriptedRead(elements=(_element(),))])
        self.assertIsNone(trace.failures["decision"])


class SeamInvokeTest(unittest.TestCase):
    """AC 12：Invoke 优先；不支持时改用坐标点击并**记一条改用事件**。"""

    def test_the_fallback_to_a_coordinate_click_is_recorded(self):
        sink = ListTraceSink()
        injector = FakeInputInjector()
        seam = _seam(_channel([ScriptedRead(elements=(_element(),))]), injector, sink)
        seam.run(SeamRequest(target_description="开始"))
        notes = sink.traces[0].result.notes
        self.assertTrue(any("改用坐标点击" in note for note in notes), notes)

    def test_invoke_is_preferred_when_the_control_supports_it(self):
        class _Invoker:
            def __init__(self):
                self.calls = 0

            def invoke(self, element_id: str) -> bool:
                self.calls += 1
                return True

        sink = ListTraceSink()
        injector = FakeInputInjector()
        invoker = _Invoker()
        seam = ActionExecutionSeam(
            capture_sources=[FakeCaptureSource(width=2560, height=1600)],
            tree=None,
            injector=injector,
            trace_sink=sink,
            locator=_channel(
                [ScriptedRead(elements=(_element(supports_invoke=True),))]
            ),
            invoker=invoker,
            anchor=_ANCHOR,
        )
        result = seam.run(SeamRequest(target_description="开始"))
        self.assertTrue(result.ok)
        self.assertEqual(invoker.calls, 1)
        self.assertEqual([e for e in injector.events if e[0] == "button_down"], [])

    def test_a_failing_invoke_falls_back_to_a_coordinate_click_and_says_so(self):
        class _FailingInvoker:
            def invoke(self, element_id: str) -> bool:
                return False

        sink = ListTraceSink()
        injector = FakeInputInjector()
        seam = ActionExecutionSeam(
            capture_sources=[FakeCaptureSource(width=2560, height=1600)],
            tree=None,
            injector=injector,
            trace_sink=sink,
            locator=_channel(
                [ScriptedRead(elements=(_element(supports_invoke=True),))]
            ),
            invoker=_FailingInvoker(),
            anchor=_ANCHOR,
        )
        result = seam.run(SeamRequest(target_description="开始"))
        self.assertTrue(result.ok)
        self.assertTrue([e for e in injector.events if e[0] == "button_down"])
        notes = sink.traces[0].result.notes
        self.assertTrue(any("改用坐标点击" in note for note in notes), notes)


class SeamActionKindTest(unittest.TestCase):
    """UIA 通道照样支持别的落点动作（移动 / 滚动……）。"""

    def test_a_move_action_uses_the_same_landing_point(self):
        sink = ListTraceSink()
        injector = FakeInputInjector()
        seam = _seam(_channel([ScriptedRead(elements=(_element(),))]), injector, sink)
        result = seam.run(
            SeamRequest(target_description="开始", action_kind=ActionKind.MOVE)
        )
        self.assertTrue(result.ok)
        self.assertEqual(result.final_point, (1040, 1560))


class SeamQueryScopeTest(unittest.TestCase):
    """AC 8：验收记录里要写明用的是窗口锚定还是桌面级查询。"""

    def test_the_record_states_the_query_scope(self):
        sink = ListTraceSink()
        injector = FakeInputInjector()
        seam = _seam(_channel([ScriptedRead(elements=(_element(),))]), injector, sink)
        seam.run(SeamRequest(target_description="开始"))
        record = build_record(sink.traces[0]).to_dict()
        self.assertEqual(record["query_scope"], "window-anchored")

    def test_the_channel_reports_the_scope_it_used(self):
        channel = _channel([ScriptedRead(elements=(_element(),))])
        result = channel.locate(
            LocateRequest(target_description="开始", anchor=_ANCHOR)
        )
        self.assertEqual(result.query_scope, "window-anchored")


class SeamControlTypeTest(unittest.TestCase):
    """控件类型提示要**真的**传到查询上（否则退路会退成全子树扫描）。"""

    def test_the_control_type_hint_reaches_the_query(self):
        channel = _channel([ScriptedRead(elements=(_element(),))])
        sink = ListTraceSink()
        injector = FakeInputInjector()
        seam = _seam(channel, injector, sink)
        seam.run(SeamRequest(target_description="开始", control_type="Button"))
        self.assertEqual(channel.query.requests[0].control_type, "Button")


if __name__ == "__main__":
    unittest.main()

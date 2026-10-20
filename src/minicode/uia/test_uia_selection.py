"""**候选选择**的单测（工单 M4）。

主题：**模型只负责「选」，不负责「瞄」**。

全程假实现：不碰真桌面、不需要 API key、不产生费用。
只测外部行为：一句目标描述进去 → 一个动作 + 一个最终落点 + 一个意图出来；
拒绝时给出**哪一类**失败、**为什么**、以及候选集。
"""

from __future__ import annotations

import unittest

from minicode.action.errors import ErrorCode
from minicode.action.fakes import FakeInputInjector
from minicode.action.ports import CapturedFrame, LocateRequest, WindowAnchor
from minicode.action.protocol import ActionKind
from minicode.action.records import ListTraceSink, build_record
from minicode.action.seam import ActionExecutionSeam, SeamRequest
from minicode.model.client import ModelReply
from minicode.model.effort import Effort
from minicode.model.errors import NoTextOutputError
from minicode.model.fakes import FakeModelClient
from minicode.redact.config import ModelContextConfig
from minicode.redact.redactor import TreeRedactor
from minicode.uia.capability import CapabilityTable
from minicode.uia.channel import UiaChannel
from minicode.uia.elements import UiaElement
from minicode.uia.fakes import FakeCapabilityProbe, FakeUiaQueryPort, ScriptedRead
from minicode.uia.pointer import ModelCandidatePointer

_ANCHOR = WindowAnchor(hwnd=1234, app="explorer", version="10.0.26100")

#: 工单 M4 AC 1 给的控件框（物理像素）：中心 = (140, 220)。
_RECT = (100, 200, 180, 240)
#: 第二个同名候选（另一个位置）。
_OTHER_RECT = (600, 200, 680, 240)

#: 一帧**能编码**的画面（BGR，3 字节/像素）。
_FRAME = CapturedFrame(width=64, height=48, payload=bytes(64 * 48 * 3))


def _element(name: str = "同名项", **kwargs) -> UiaElement:
    payload = {
        "name": name,
        "control_type": "Button",
        "class_name": "Button",
        "rect": _RECT,
        "window_handle": 1234,
    }
    payload.update(kwargs)
    return UiaElement(**payload)


def _two_candidates() -> ScriptedRead:
    """两个同名候选（消歧场景）。"""
    return ScriptedRead(
        elements=(
            _element(rect=_RECT),
            _element(rect=_OTHER_RECT),
        )
    )


def _reply(text: str) -> ModelReply:
    return ModelReply(
        text=text,
        model="fake-model",
        endpoint="https://example.invalid/v1/messages",
        effort=Effort.LOW,
        http_status=200,
        latency_ms=1.0,
    )


def _redactor(*, redaction_enabled: bool = True) -> TreeRedactor:
    if not redaction_enabled:
        # 脱敏没上线的形态：**两个开关都关**（只开一个在配置层就被拒了）。
        return TreeRedactor(ModelContextConfig())
    return TreeRedactor(
        ModelContextConfig(
            send_tree_to_model=True,
            redaction_enabled=True,
        )
    )


def _channel(reads, *, model=None, redactor=None, max_candidates: int = 8) -> UiaChannel:
    table = CapabilityTable(FakeCapabilityProbe(), sleeper=lambda seconds: None)
    pointer = ModelCandidatePointer(model) if model is not None else None
    return UiaChannel(
        query=FakeUiaQueryPort(reads),
        capability=table,
        pointer=pointer,
        redactor=redactor if redactor is not None else _redactor(),
        max_candidates=max_candidates,
    )


def _seam(channel: UiaChannel, injector: FakeInputInjector, sink: ListTraceSink):
    return ActionExecutionSeam(
        capture_sources=[],
        tree=None,
        injector=injector,
        trace_sink=sink,
        locator=channel,
        anchor=_ANCHOR,
    )


class LandingOnTheSelectedBoxTest(unittest.TestCase):
    """AC 1：落点 = 被选中控件的控件框中心（物理像素），与框中心偏差 ≤2 px。"""

    def _run(self, reply_text: str = '{"chosen": 1}'):
        sink = ListTraceSink()
        injector = FakeInputInjector()
        model = FakeModelClient(_reply(reply_text))
        seam = _seam(
            _channel([_two_candidates()], model=model),
            injector,
            sink,
        )
        result = seam.run(
            SeamRequest(target_description="同名项", frame=_FRAME)
        )
        return result, injector, sink

    def test_the_action_is_a_click(self):
        result, _, _ = self._run()
        self.assertTrue(result.ok)
        self.assertEqual(result.action.kind, ActionKind.CLICK)

    def test_the_landing_point_is_the_center_of_the_selected_box(self):
        result, _, _ = self._run()
        self.assertEqual(result.final_point, (140, 220))

    def test_the_injected_coordinates_are_the_physical_surface_coordinates(self):
        _, injector, _ = self._run()
        moves = [event for event in injector.events if event[0] == "move"]
        self.assertEqual(moves[-1], ("move", 140, 220))

    def test_the_landing_point_is_within_two_pixels_of_the_box_center(self):
        result, _, _ = self._run()
        center = ((_RECT[0] + _RECT[2]) / 2.0, (_RECT[1] + _RECT[3]) / 2.0)
        delta = (
            (result.final_point[0] - center[0]) ** 2
            + (result.final_point[1] - center[1]) ** 2
        ) ** 0.5
        self.assertLessEqual(delta, 2.0)


class ModelDoesNotAimTest(unittest.TestCase):
    """AC 2：模型坐标不参与；请求里只有「选哪一个」这一项任务。"""

    def test_coordinates_in_the_model_reply_do_not_move_the_landing_point(self):
        sink = ListTraceSink()
        injector = FakeInputInjector()
        model = FakeModelClient(_reply('{"chosen": 1, "x": 1234, "y": 5678}'))
        seam = _seam(_channel([_two_candidates()], model=model), injector, sink)
        result = seam.run(SeamRequest(target_description="同名项", frame=_FRAME))
        self.assertTrue(result.ok)
        self.assertEqual(result.final_point, (140, 220))

    def test_the_request_asks_only_which_candidate_to_choose(self):
        model = FakeModelClient(_reply('{"chosen": 1}'))
        channel = _channel([_two_candidates()], model=model)
        channel.locate(
            LocateRequest(target_description="同名项", anchor=_ANCHOR, frame=_FRAME)
        )
        prompt = model.calls[0]["prompt"]
        self.assertIn("行号", prompt)
        self.assertIn("同名项", prompt)

    def test_the_request_carries_no_coordinate_conversion_hint(self):
        model = FakeModelClient(_reply('{"chosen": 1}'))
        channel = _channel([_two_candidates()], model=model)
        channel.locate(
            LocateRequest(target_description="同名项", anchor=_ANCHOR, frame=_FRAME)
        )
        prompt = model.calls[0]["prompt"]
        for word in ("坐标", "归一化", "分母", "像素", "0~1000", "0-1000"):
            self.assertNotIn(word, prompt, word)

    def test_the_candidate_boxes_are_listed_for_the_model(self):
        model = FakeModelClient(_reply('{"chosen": 1}'))
        channel = _channel([_two_candidates()], model=model)
        channel.locate(
            LocateRequest(target_description="同名项", anchor=_ANCHOR, frame=_FRAME)
        )
        prompt = model.calls[0]["prompt"]
        self.assertIn("rect=(100,200,180,240)", prompt)
        self.assertIn("rect=(600,200,680,240)", prompt)

    def test_a_coordinate_in_a_free_text_reply_is_not_taken_as_a_line_number(self):
        # 「文本里随便找个整数」的兜底会把模型回复里的坐标当成行号 → 静默选错候选。
        # 读不出就是读不出（工单 M4 AC 2：模型坐标不参与）。
        sink = ListTraceSink()
        injector = FakeInputInjector()
        model = FakeModelClient(_reply("坐标是 x=1, y=2"))
        seam = _seam(_channel([_two_candidates()], model=model), injector, sink)
        result = seam.run(SeamRequest(target_description="同名项", frame=_FRAME))
        self.assertEqual(result.result.error_code, ErrorCode.AMBIGUOUS_TARGET)
        self.assertIsNone(result.final_point)

    def test_a_plain_line_number_is_still_accepted(self):
        sink = ListTraceSink()
        injector = FakeInputInjector()
        model = FakeModelClient(_reply("2"))
        seam = _seam(_channel([_two_candidates()], model=model), injector, sink)
        result = seam.run(SeamRequest(target_description="同名项", frame=_FRAME))
        self.assertTrue(result.ok)
        self.assertEqual(result.final_point, (640, 220))


class UniquenessRejectionTest(unittest.TestCase):
    """AC 3：唯一性拒绝 → 选择失败 + 候选集（标识 / 名字 / 控件框）。"""

    def _reject(self, reply_text: str):
        sink = ListTraceSink()
        injector = FakeInputInjector()
        model = FakeModelClient(_reply(reply_text))
        seam = _seam(_channel([_two_candidates()], model=model), injector, sink)
        result = seam.run(SeamRequest(target_description="同名项", frame=_FRAME))
        return result, sink

    def test_an_empty_answer_is_a_selection_failure(self):
        result, _ = self._reject('{"chosen": []}')
        self.assertFalse(result.ok)
        self.assertEqual(result.result.error_code, ErrorCode.AMBIGUOUS_TARGET)

    def test_an_answer_outside_the_candidate_set_is_a_selection_failure(self):
        result, _ = self._reject('{"chosen": 7}')
        self.assertEqual(result.result.error_code, ErrorCode.AMBIGUOUS_TARGET)

    def test_two_answers_are_a_selection_failure(self):
        result, _ = self._reject('{"chosen": [1, 2]}')
        self.assertEqual(result.result.error_code, ErrorCode.AMBIGUOUS_TARGET)

    def test_the_rejection_reports_the_candidate_set(self):
        result, _ = self._reject('{"chosen": []}')
        self.assertEqual(len(result.rejected_candidates), 2)
        self.assertEqual(
            [candidate.rect for candidate in result.rejected_candidates],
            [_RECT, _OTHER_RECT],
        )
        self.assertEqual(
            [candidate.name for candidate in result.rejected_candidates],
            ["同名项", "同名项"],
        )
        self.assertEqual(
            len({candidate.element_id for candidate in result.rejected_candidates}),
            2,
        )

    def test_the_failure_reason_is_reported(self):
        result, _ = self._reject('{"chosen": []}')
        self.assertTrue(result.result.error_message)
        self.assertIn("同名项", result.result.error_message)


class CandidateLimitTest(unittest.TestCase):
    """AC 4：候选数超过上限 → 不调模型 + 回报候选集；上限是运行时参数且来源被记录。"""

    def _too_many(self):
        elements = tuple(
            _element(rect=(100 * index, 0, 100 * index + 50, 40)) for index in range(9)
        )
        sink = ListTraceSink()
        injector = FakeInputInjector()
        model = FakeModelClient(_reply('{"chosen": 1}'))
        seam = _seam(
            _channel([ScriptedRead(elements=elements)], model=model),
            injector,
            sink,
        )
        result = seam.run(SeamRequest(target_description="同名项", frame=_FRAME))
        return result, model, sink

    def test_too_many_candidates_never_call_the_model(self):
        result, model, _ = self._too_many()
        self.assertEqual(result.result.error_code, ErrorCode.TOO_MANY_CANDIDATES)
        self.assertEqual(model.calls, [])

    def test_the_candidate_set_is_reported(self):
        result, _, _ = self._too_many()
        self.assertEqual(len(result.rejected_candidates), 9)

    def test_the_limit_and_its_source_are_recorded(self):
        _, _, sink = self._too_many()
        trace = sink.traces[0]
        self.assertEqual(trace.candidate_limit, 8)
        self.assertTrue(trace.candidate_limit_source)

    def test_an_overridden_limit_records_that_it_was_overridden(self):
        # 上限是运行时参数：改了值，记录里的来源必须说清是**覆盖**来的，
        # 不能还写着「默认 8」（工单 M4 AC 4）。
        elements = tuple(
            _element(rect=(100 * index, 0, 100 * index + 50, 40)) for index in range(9)
        )
        sink = ListTraceSink()
        injector = FakeInputInjector()
        channel = _channel(
            [ScriptedRead(elements=elements)], max_candidates=20
        )
        seam = _seam(channel, injector, sink)
        seam.run(SeamRequest(target_description="同名项", frame=_FRAME))
        trace = sink.traces[0]
        self.assertEqual(trace.candidate_limit, 20)
        self.assertIn("覆盖", trace.candidate_limit_source)


class ZeroActionOnRejectionTest(unittest.TestCase):
    """AC 5：拒绝时零动作（无移动 / 无按下 / 无抬起），且不产生动作意图。"""

    def test_no_input_is_injected(self):
        sink = ListTraceSink()
        injector = FakeInputInjector()
        model = FakeModelClient(_reply('{"chosen": []}'))
        seam = _seam(_channel([_two_candidates()], model=model), injector, sink)
        seam.run(SeamRequest(target_description="同名项", frame=_FRAME))
        self.assertEqual(
            [event for event in injector.events if event[0] != "probe"],
            [],
        )

    def test_no_action_intent_is_produced(self):
        sink = ListTraceSink()
        injector = FakeInputInjector()
        model = FakeModelClient(_reply('{"chosen": []}'))
        seam = _seam(_channel([_two_candidates()], model=model), injector, sink)
        result = seam.run(SeamRequest(target_description="同名项", frame=_FRAME))
        self.assertIsNone(result.action)
        self.assertEqual(result.intent, "")


class FailureClassificationTest(unittest.TestCase):
    """AC 6：覆盖失败与选择失败分得开；只有覆盖失败被标记为允许切视觉。"""

    def _locate(self, reads, *, model=None):
        channel = _channel(reads, model=model)
        return channel.locate(
            LocateRequest(target_description="同名项", anchor=_ANCHOR, frame=_FRAME)
        )

    def test_an_empty_candidate_set_is_a_coverage_failure(self):
        result = self._locate([ScriptedRead(elements=(), nodes=True)])
        self.assertEqual(result.error_code, ErrorCode.TARGET_NOT_FOUND)
        self.assertTrue(result.allows_visual_fallback)

    def test_a_non_unique_candidate_set_is_a_selection_failure(self):
        model = FakeModelClient(_reply('{"chosen": []}'))
        result = self._locate([_two_candidates()], model=model)
        self.assertEqual(result.error_code, ErrorCode.AMBIGUOUS_TARGET)
        self.assertFalse(result.allows_visual_fallback)

    def test_too_many_candidates_is_a_selection_failure(self):
        elements = tuple(
            _element(rect=(100 * index, 0, 100 * index + 50, 40)) for index in range(9)
        )
        result = self._locate([ScriptedRead(elements=elements)])
        self.assertEqual(result.error_code, ErrorCode.TOO_MANY_CANDIDATES)
        self.assertFalse(result.allows_visual_fallback)

    def test_the_two_failure_kinds_have_different_codes(self):
        coverage = self._locate([ScriptedRead(elements=(), nodes=True)])
        selection = self._locate([_two_candidates()])
        self.assertNotEqual(coverage.error_code, selection.error_code)

    def test_an_empty_description_is_not_a_coverage_failure(self):
        # 空描述是「根本没得找」，不是「通道产不出候选」→ 不许切视觉。
        channel = _channel([ScriptedRead(elements=(), nodes=True)])
        result = channel.locate(
            LocateRequest(target_description="   ", anchor=_ANCHOR, frame=_FRAME)
        )
        self.assertEqual(result.error_code, ErrorCode.INVALID_TARGET)
        self.assertFalse(result.allows_visual_fallback)

    def test_a_model_failure_is_a_selection_failure_with_no_action(self):
        # 模型侧失败（HTTP 错误 / 思考链吃光预算没有输出）→ 候选里有但选不出
        # → **选择失败** + 零动作，模型侧错误**如实回传**在消息里。
        sink = ListTraceSink()
        injector = FakeInputInjector()
        model = FakeModelClient(NoTextOutputError("HTTP 200 但本次没有文本输出"))
        seam = _seam(_channel([_two_candidates()], model=model), injector, sink)
        result = seam.run(SeamRequest(target_description="同名项", frame=_FRAME))
        self.assertEqual(result.result.error_code, ErrorCode.AMBIGUOUS_TARGET)
        self.assertIn("没有文本输出", result.result.error_message)
        self.assertEqual(
            [event for event in injector.events if event[0] != "probe"],
            [],
        )

    def test_a_channel_refusal_is_not_a_coverage_failure(self):
        model = FakeModelClient(_reply('{"chosen": 1}'))
        channel = UiaChannel(
            query=FakeUiaQueryPort([_two_candidates()]),
            capability=CapabilityTable(
                FakeCapabilityProbe(), sleeper=lambda seconds: None
            ),
            pointer=ModelCandidatePointer(model),
            redactor=None,
        )
        result = channel.locate(
            LocateRequest(target_description="同名项", anchor=_ANCHOR, frame=_FRAME)
        )
        self.assertEqual(result.error_code, ErrorCode.CHANNEL_DISABLED)
        self.assertFalse(result.allows_visual_fallback)


class RedactionGateTest(unittest.TestCase):
    """AC 7：脱敏没上线 → 该通道被拒绝启用，且零模型调用。"""

    def test_the_channel_refuses_to_be_enabled_without_a_redactor(self):
        model = FakeModelClient(_reply('{"chosen": 1}'))
        channel = UiaChannel(
            query=FakeUiaQueryPort([_two_candidates()]),
            capability=CapabilityTable(
                FakeCapabilityProbe(), sleeper=lambda seconds: None
            ),
            pointer=ModelCandidatePointer(model),
            redactor=None,
        )
        result = channel.locate(
            LocateRequest(target_description="同名项", anchor=_ANCHOR, frame=_FRAME)
        )
        self.assertEqual(result.error_code, ErrorCode.CHANNEL_DISABLED)
        self.assertIn("脱敏未上线", result.error_message)
        self.assertEqual(result.model_calls, 0)

    def test_the_model_is_not_called_when_the_gate_is_closed(self):
        model = FakeModelClient(_reply('{"chosen": 1}'))
        channel = UiaChannel(
            query=FakeUiaQueryPort([_two_candidates()]),
            capability=CapabilityTable(
                FakeCapabilityProbe(), sleeper=lambda seconds: None
            ),
            pointer=ModelCandidatePointer(model),
            redactor=None,
        )
        channel.locate(
            LocateRequest(target_description="同名项", anchor=_ANCHOR, frame=_FRAME)
        )
        self.assertEqual(model.calls, [])

    def test_the_channel_refuses_when_the_redaction_is_switched_off(self):
        model = FakeModelClient(_reply('{"chosen": 1}'))
        channel = _channel(
            [_two_candidates()],
            model=model,
            redactor=_redactor(redaction_enabled=False),
        )
        result = channel.locate(
            LocateRequest(target_description="同名项", anchor=_ANCHOR, frame=_FRAME)
        )
        self.assertEqual(result.error_code, ErrorCode.CHANNEL_DISABLED)
        self.assertEqual(model.calls, [])

    def test_a_unique_candidate_does_not_need_the_gate(self):
        model = FakeModelClient(_reply('{"chosen": 1}'))
        channel = UiaChannel(
            query=FakeUiaQueryPort([ScriptedRead(elements=(_element(),))]),
            capability=CapabilityTable(
                FakeCapabilityProbe(), sleeper=lambda seconds: None
            ),
            pointer=ModelCandidatePointer(model),
            redactor=None,
        )
        result = channel.locate(
            LocateRequest(target_description="同名项", anchor=_ANCHOR, frame=_FRAME)
        )
        self.assertIsNone(result.error_code)
        self.assertEqual(model.calls, [])

    def test_a_channel_refusal_is_recorded_as_a_locate_failure(self):
        # 三类失败要分开记：通道被拒绝启用发生在**定位阶段**（动作没下发）
        # → 归「定位失败」，不许记成「执行失败」。
        sink = ListTraceSink()
        injector = FakeInputInjector()
        model = FakeModelClient(_reply('{"chosen": 1}'))
        channel = UiaChannel(
            query=FakeUiaQueryPort([_two_candidates()]),
            capability=CapabilityTable(
                FakeCapabilityProbe(), sleeper=lambda seconds: None
            ),
            pointer=ModelCandidatePointer(model),
            redactor=None,
        )
        seam = _seam(channel, injector, sink)
        seam.run(SeamRequest(target_description="同名项", frame=_FRAME))
        trace = sink.traces[0]
        self.assertEqual(trace.failures["locate"], ErrorCode.CHANNEL_DISABLED.value)
        self.assertIsNone(trace.failures["execute"])
        self.assertIsNone(trace.failures["decision"])


class RelocationTest(unittest.TestCase):
    """AC 8：执行前重读重定位（跨观测稳定的身份，不用上一次观测的元素 id）。"""

    def test_a_missing_candidate_on_the_second_read_is_a_relocation_failure(self):
        sink = ListTraceSink()
        injector = FakeInputInjector()
        model = FakeModelClient(_reply('{"chosen": 1}'))
        channel = _channel(
            [
                _two_candidates(),
                # 重读时被选中的那个控件**没了**，只剩另一个同名控件。
                ScriptedRead(elements=(_element(rect=_OTHER_RECT),)),
            ],
            model=model,
        )
        seam = _seam(channel, injector, sink)
        result = seam.run(SeamRequest(target_description="同名项", frame=_FRAME))
        self.assertEqual(result.result.error_code, ErrorCode.RELOCATE_FAILED)
        self.assertEqual(
            [event for event in injector.events if event[0] != "probe"],
            [],
        )

    def test_a_moved_control_box_is_used_for_the_landing_point(self):
        moved = (100, 320, 180, 360)
        sink = ListTraceSink()
        injector = FakeInputInjector()
        model = FakeModelClient(_reply('{"chosen": 1}'))
        channel = _channel(
            [
                _two_candidates(),
                ScriptedRead(
                    elements=(
                        _element(rect=moved),
                        _element(rect=_OTHER_RECT),
                    )
                ),
            ],
            model=model,
        )
        seam = _seam(channel, injector, sink)
        result = seam.run(SeamRequest(target_description="同名项", frame=_FRAME))
        self.assertTrue(result.ok)
        self.assertEqual(result.final_point, (140, 340))

    def test_the_relocation_does_not_reuse_the_previous_element_id(self):
        sink = ListTraceSink()
        injector = FakeInputInjector()
        model = FakeModelClient(_reply('{"chosen": 1}'))
        channel = _channel([_two_candidates(), _two_candidates()], model=model)
        seam = _seam(channel, injector, sink)
        result = seam.run(SeamRequest(target_description="同名项", frame=_FRAME))
        self.assertTrue(result.ok)
        trace = sink.traces[0]
        # 选中项来自**第二次**观测：它的元素 id 不在第一次观测的 id 集合里。
        first_read_ids = {candidate.element_id for candidate in trace.candidates}
        self.assertNotIn(trace.selected.element_id, first_read_ids)


class TraceAndEchoTest(unittest.TestCase):
    """AC 9：追踪记录带候选集与选中项；每步回显带选择结果与实际发送的图片尺寸。"""

    def _run(self):
        sink = ListTraceSink()
        injector = FakeInputInjector()
        model = FakeModelClient(_reply('{"chosen": 2}'))
        seam = _seam(_channel([_two_candidates()], model=model), injector, sink)
        result = seam.run(SeamRequest(target_description="同名项", frame=_FRAME))
        return result, sink

    def test_the_trace_carries_the_candidate_set_and_the_selected_item(self):
        _, sink = self._run()
        trace = sink.traces[0]
        self.assertEqual(len(trace.candidates), 2)
        self.assertIsNotNone(trace.selected)
        self.assertEqual(trace.selected.rect, _OTHER_RECT)

    def test_the_main_return_type_carries_no_trace_fields(self):
        result, _ = self._run()
        for field in ("candidates", "selected", "selection_echo", "trace"):
            self.assertFalse(hasattr(result, field), field)

    def test_the_echo_reports_the_selection_and_the_sent_image_size(self):
        _, sink = self._run()
        echo = sink.traces[0].selection_echo
        self.assertIsNotNone(echo)
        self.assertEqual(echo.chosen_numbers, (2,))
        self.assertEqual(echo.chosen_names, ("同名项",))
        self.assertEqual(echo.sent_width, 64)
        self.assertEqual(echo.sent_height, 48)

    def test_the_echo_carries_no_coordinates(self):
        _, sink = self._run()
        echo = sink.traces[0].selection_echo
        rendered = repr(echo)
        for word in ("100", "200", "600", "680"):
            self.assertNotIn(word, rendered, word)

    def test_the_echo_reports_no_sent_image_when_no_image_was_sent(self):
        # 画面编不成（像素数与尺寸对不上）→ 图压根没发 → 回显里的
        # 「实际发送尺寸」必须为空，**不许拿抓图尺寸冒充**。
        sink = ListTraceSink()
        injector = FakeInputInjector()
        model = FakeModelClient(_reply('{"chosen": 1}'))
        broken_frame = CapturedFrame(width=2560, height=1600, payload=b"\x01" * 64)
        seam = _seam(_channel([_two_candidates()], model=model), injector, sink)
        seam.run(SeamRequest(target_description="同名项", frame=broken_frame))
        echo = sink.traces[0].selection_echo
        self.assertIsNone(echo.sent_width)
        self.assertIsNone(echo.sent_height)


class NoVisionSwitchTest(unittest.TestCase):
    """AC 11：本单只标记「允许切视觉」，不实现切视觉。"""

    def test_a_coverage_failure_costs_no_model_call_and_no_action(self):
        sink = ListTraceSink()
        injector = FakeInputInjector()
        model = FakeModelClient(_reply('{"chosen": 1}'))
        seam = _seam(
            _channel([ScriptedRead(elements=(), nodes=True)], model=model),
            injector,
            sink,
        )
        result = seam.run(SeamRequest(target_description="同名项", frame=_FRAME))
        self.assertEqual(result.result.error_code, ErrorCode.TARGET_NOT_FOUND)
        self.assertEqual(model.calls, [])
        self.assertEqual(
            [event for event in injector.events if event[0] != "probe"],
            [],
        )

    def test_the_marker_is_carried_in_the_record(self):
        sink = ListTraceSink()
        injector = FakeInputInjector()
        seam = _seam(
            _channel([ScriptedRead(elements=(), nodes=True)]),
            injector,
            sink,
        )
        seam.run(SeamRequest(target_description="同名项", frame=_FRAME))
        record = build_record(sink.traces[0]).to_dict()
        self.assertTrue(record["allows_visual_fallback"])


if __name__ == "__main__":
    unittest.main()

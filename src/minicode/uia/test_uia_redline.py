"""**切视觉的红线**与三类失败分开报（工单 M9）。

主题：**切视觉只认覆盖失败**（架构决策备忘 §2.5 ①）。

预置五种**非覆盖失败**场景（候选不唯一 / 候选被遮挡 / 候选尺寸异常 / 查询超时 /
名称匹配不上）：每一种都必须走错误处理，**视觉通道的调用次数为 0**，
动作按错误处理的规定处置（不静默重试、不落到别处）。

全程假实现：不碰真桌面、不需要 API key、不产生费用。
只测外部行为：一句目标描述进去 → 一个动作 / 一个明确错误码 / 一个最终落点出来，
外加追踪记录里读得到的「走了哪条通道」与「哪一类失败」。
"""

from __future__ import annotations

import unittest
from dataclasses import dataclass, field

from minicode.action.errors import ErrorCode, FailureType, allows_visual_fallback
from minicode.action.fakes import (
    FakeCaptureSource,
    FakeInputInjector,
    FakeWindowController,
)
from minicode.action.ports import (
    VisualAdmission,
    VisualLocateRequest,
    VisualLocateResult,
    WindowAnchor,
)
from minicode.action.records import ListTraceSink, build_record
from minicode.action.seam import ActionExecutionSeam, SeamRequest
from minicode.redact.config import ModelContextConfig
from minicode.redact.redactor import TreeRedactor
from minicode.uia.capability import Availability, CapabilityTable
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


@dataclass
class _FakeVisualLocator:
    """记调用次数的假视觉通道。"""

    landing_point: tuple[int, int] = (700, 500)
    calls: int = 0
    requests: list[VisualLocateRequest] = field(default_factory=list)

    def locate(self, request: VisualLocateRequest) -> VisualLocateResult:
        self.calls += 1
        self.requests.append(request)
        # 视觉通道**要调模型**：如实报 1 次（追踪记录据此把模型调用数记全）。
        return VisualLocateResult(landing_point=self.landing_point, model_calls=1)


@dataclass
class _FakeAdmission:
    """假准入判定（工单 M5 的硬闸门在单测里的替身）。"""

    available: bool = True
    reason: str = "测试准入：放行"
    calls: int = 0

    def admit(self) -> VisualAdmission:
        self.calls += 1
        return VisualAdmission(available=self.available, reason=self.reason)


def _redactor() -> TreeRedactor:
    return TreeRedactor(
        ModelContextConfig(send_tree_to_model=True, redaction_enabled=True)
    )


def _channel(reads, *, pointer=None, redactor=None) -> UiaChannel:
    table = CapabilityTable(FakeCapabilityProbe(), sleeper=lambda seconds: None)
    return UiaChannel(
        query=FakeUiaQueryPort(reads),
        capability=table,
        pointer=pointer,
        redactor=redactor,
    )


def _seam(
    channel: UiaChannel,
    injector: FakeInputInjector,
    sink: ListTraceSink,
    *,
    visual: _FakeVisualLocator | None = None,
    admission: _FakeAdmission | None = None,
    window_controller=None,
    capture_sources=None,
):
    return ActionExecutionSeam(
        capture_sources=(
            capture_sources
            if capture_sources is not None
            else [FakeCaptureSource(width=2560, height=1600)]
        ),
        tree=None,
        injector=injector,
        window_controller=window_controller,
        trace_sink=sink,
        locator=channel,
        anchor=_ANCHOR,
        visual_admission=admission,
        visual_locator=visual,
    )


class RedLineTest(unittest.TestCase):
    """AC 2：切视觉**只**认覆盖失败 —— 五种非覆盖失败场景一律零视觉调用。"""

    def _run_scenario(
        self,
        reads,
        *,
        pointer=None,
        redactor=None,
        window_controller=None,
    ):
        sink = ListTraceSink()
        injector = FakeInputInjector()
        visual = _FakeVisualLocator()
        admission = _FakeAdmission()
        seam = _seam(
            _channel(reads, pointer=pointer, redactor=redactor),
            injector,
            sink,
            visual=visual,
            admission=admission,
            window_controller=window_controller,
        )
        result = seam.run(SeamRequest(target_description="开始"))
        return result, sink.traces[0], visual, injector

    def _assert_rejected_without_visual(self, result, trace, visual, injector):
        self.assertFalse(result.ok)
        self.assertIsNone(result.action)
        self.assertIsNone(result.final_point)
        # **视觉通道的调用次数为 0** —— 红线是可数的，不是靠约定。
        self.assertEqual(visual.calls, 0)
        # 零动作下发（不静默重试、不落到别处）。
        self.assertEqual(
            [event for event in injector.events if event[0] != "probe"],
            [],
        )
        self.assertFalse(trace.allows_visual_fallback)
        return trace

    def test_ambiguous_candidates_are_rejected_without_calling_visual(self):
        """候选不唯一（先做消歧，消歧没给出唯一指认）→ 选择失败。"""
        pointer = FakeCandidatePointer(chosen_ids=())
        result, trace, visual, injector = self._run_scenario(
            [ScriptedRead(elements=(_element(), _element(rect=(600, 200, 680, 240))))],
            pointer=pointer,
            redactor=_redactor(),
        )
        self._assert_rejected_without_visual(result, trace, visual, injector)
        self.assertEqual(result.result.error_code, ErrorCode.AMBIGUOUS_TARGET)
        self.assertEqual(trace.failure_type, FailureType.SELECTION.value)
        # 消歧那一次模型调用**如实计数**（0 模型调用只在「候选唯一」时成立）。
        self.assertEqual(trace.model_calls, 1)
        self.assertEqual(pointer.calls, 1)

    def test_an_occluded_candidate_is_rejected_without_calling_visual(self):
        """候选被遮挡（置前失败）→ 执行失败。"""
        result, trace, visual, injector = self._run_scenario(
            [ScriptedRead(elements=(_element(),))],
            window_controller=FakeWindowController(foreground=999, bring_fails=True),
        )
        self._assert_rejected_without_visual(result, trace, visual, injector)
        self.assertEqual(result.result.error_code, ErrorCode.EXECUTE_FAILED)
        self.assertEqual(trace.failure_type, FailureType.EXECUTION.value)

    def test_an_abnormal_candidate_size_is_rejected_without_calling_visual(self):
        """候选尺寸异常（控件框读不成合法矩形）→ 选择失败。"""
        result, trace, visual, injector = self._run_scenario(
            [ScriptedRead(elements=(_element(rect=(0, 0, 0, 0)),))],
        )
        self._assert_rejected_without_visual(result, trace, visual, injector)
        self.assertEqual(result.result.error_code, ErrorCode.INVALID_GEOMETRY)
        self.assertEqual(trace.failure_type, FailureType.SELECTION.value)

    def test_a_query_timeout_is_rejected_without_calling_visual(self):
        """查询超时 → 通道级失败（**不是**覆盖失败）。"""
        result, trace, visual, injector = self._run_scenario(
            [ScriptedRead(timed_out=True)],
        )
        self._assert_rejected_without_visual(result, trace, visual, injector)
        self.assertEqual(result.result.error_code, ErrorCode.QUERY_TIMEOUT)
        self.assertNotEqual(trace.failure_type, FailureType.COVERAGE.value)

    def test_a_name_mismatch_is_rejected_without_calling_visual(self):
        """名称匹配不上（条件查询找到了候选，但没有一个与描述对得上）。"""
        result, trace, visual, injector = self._run_scenario(
            [ScriptedRead(elements=(), unmatched=3)],
        )
        self._assert_rejected_without_visual(result, trace, visual, injector)
        self.assertEqual(result.result.error_code, ErrorCode.NAME_MISMATCH)
        self.assertEqual(trace.failure_type, FailureType.SELECTION.value)

    def test_the_three_failure_kinds_have_different_identifiers(self):
        """AC 7：三类失败**标识不同、互不混淆**（不是同一个值换三种叫法）。"""
        self.assertNotEqual(
            FailureType.COVERAGE.value, FailureType.SELECTION.value
        )
        self.assertNotEqual(
            FailureType.COVERAGE.value, FailureType.EXECUTION.value
        )
        self.assertNotEqual(
            FailureType.SELECTION.value, FailureType.EXECUTION.value
        )
        # 红线只有一处：只有覆盖失败允许切视觉。
        self.assertTrue(allows_visual_fallback(FailureType.COVERAGE.value))
        for other in (FailureType.SELECTION, FailureType.EXECUTION, FailureType.CHANNEL):
            self.assertFalse(allows_visual_fallback(other.value))


class CoverageFailureCutsToVisualTest(unittest.TestCase):
    """AC 1：覆盖失败 → 切视觉通道；追踪记录里通道 = 视觉、失败类型 = 覆盖失败。"""

    def _run(self, *, nodes: bool = True):
        sink = ListTraceSink()
        injector = FakeInputInjector()
        visual = _FakeVisualLocator(landing_point=(700, 500))
        admission = _FakeAdmission()
        seam = _seam(
            _channel([ScriptedRead(elements=(), nodes=nodes)]),
            injector,
            sink,
            visual=visual,
            admission=admission,
        )
        result = seam.run(SeamRequest(target_description="开始"))
        return result, sink.traces[0], visual, admission

    def test_the_visual_channel_produces_the_landing_point(self):
        result, trace, visual, admission = self._run()
        self.assertTrue(result.ok)
        self.assertEqual(visual.calls, 1)
        self.assertEqual(admission.calls, 1)
        self.assertEqual(result.final_point, (700, 500))

    def test_the_trace_records_the_channel_and_the_failure_type(self):
        _result, trace, _visual, _admission = self._run()
        self.assertEqual(trace.channel, "visual")
        self.assertEqual(trace.failure_type, FailureType.COVERAGE.value)
        self.assertTrue(trace.allows_visual_fallback)

    def test_an_empty_tree_is_a_coverage_failure_not_a_missing_target(self):
        """0 节点 = **通道不可用**（覆盖失败），不是「目标不存在」（C-09）。"""
        _result, trace, _visual, _admission = self._run(nodes=False)
        self.assertEqual(trace.failure_type, FailureType.COVERAGE.value)
        self.assertEqual(trace.channel, "visual")


class UiaHitTest(unittest.TestCase):
    """AC 3：UIA 命中时**零模型调用**，端到端延迟 ≤1 s（亚秒级）。"""

    def test_no_model_is_called_and_the_latency_is_sub_second(self):
        sink = ListTraceSink()
        injector = FakeInputInjector()
        seam = _seam(
            _channel([ScriptedRead(elements=(_element(),))]),
            injector,
            sink,
        )
        result = seam.run(SeamRequest(target_description="开始"))
        self.assertTrue(result.ok)
        trace = sink.traces[0]
        # **可被计数证明**：这一步的模型调用次数是 0。
        self.assertEqual(trace.model_calls, 0)
        self.assertEqual(trace.channel, "uia")
        self.assertEqual(trace.failure_type, "")
        total_ms = trace.locate_ms + trace.decision_ms + trace.execute_verify_ms
        self.assertLessEqual(total_ms, 1000.0)


class CalibrationGateTest(unittest.TestCase):
    """AC 4：标定失败即视觉通道不可用 → 回落 UIA 通道或报明确错误码。"""

    def test_the_visual_channel_is_refused_when_the_gate_says_unavailable(self):
        sink = ListTraceSink()
        injector = FakeInputInjector()
        visual = _FakeVisualLocator()
        admission = _FakeAdmission(
            available=False, reason="标定失败：最大误差 2.66 px 超过标定容差 1.0 px"
        )
        seam = _seam(
            _channel([ScriptedRead(elements=(), nodes=True)]),
            injector,
            sink,
            visual=visual,
            admission=admission,
        )
        result = seam.run(SeamRequest(target_description="开始"))
        self.assertFalse(result.ok)
        self.assertEqual(
            result.result.error_code, ErrorCode.VISUAL_CHANNEL_UNAVAILABLE
        )
        self.assertEqual(visual.calls, 0)
        self.assertEqual(
            [event for event in injector.events if event[0] != "probe"],
            [],
        )
        trace = sink.traces[0]
        self.assertIsNotNone(trace.visual_admission)
        self.assertFalse(trace.visual_admission.available)
        # 不猜一个标度继续跑：没有落点、没有动作。
        self.assertIsNone(result.final_point)


class CaptureEmptyTest(unittest.TestCase):
    """AC 6：抓屏 0×0 → 跳过该捕获源、标记为不可用、动作不产生落点。"""

    def test_the_empty_capture_source_is_skipped_and_marked_unavailable(self):
        sink = ListTraceSink()
        injector = FakeInputInjector()
        empty = FakeCaptureSource(width=0, height=0, name="empty-capture")
        seam = _seam(
            _channel([ScriptedRead(elements=(_element(),))]),
            injector,
            sink,
            capture_sources=[empty],
        )
        result = seam.run(SeamRequest(target_description="开始"))
        self.assertFalse(result.ok)
        self.assertEqual(result.result.error_code, ErrorCode.CAPTURE_EMPTY)
        self.assertIsNone(result.final_point)
        self.assertIsNone(result.action)
        trace = sink.traces[0]
        self.assertIn("empty-capture", trace.unavailable_capture_sources)
        self.assertIn("empty-capture", trace.skipped_capture_sources)


class RecordCarriesTheChannelTest(unittest.TestCase):
    """AC 7：每一步的追踪记录里能读到**本次走的通道**与**失败类型**。"""

    def test_the_record_carries_the_channel_and_the_failure_type(self):
        sink = ListTraceSink()
        injector = FakeInputInjector()
        seam = _seam(
            _channel([ScriptedRead(elements=(), nodes=True)]),
            injector,
            sink,
            visual=_FakeVisualLocator(),
            admission=_FakeAdmission(),
        )
        seam.run(SeamRequest(target_description="开始"))
        record = build_record(sink.traces[0]).to_dict()
        self.assertEqual(record["channel"], "visual")
        self.assertEqual(record["failure_type"], FailureType.COVERAGE.value)
        self.assertEqual(record["model_calls"], 1)


class CapabilityTableTest(unittest.TestCase):
    """AC 5：能力表三态；**不出现类别级结论**。"""

    def _table(self, assessments):
        return CapabilityTable(
            FakeCapabilityProbe(assessments=assessments), sleeper=lambda seconds: None
        )

    def test_the_three_states_are_distinguishable(self):
        from minicode.uia.ports import TreeAssessment

        table = self._table(
            (
                TreeAssessment(has_tree=True, usable_controls=3, has_automation_id=True),
                TreeAssessment(has_tree=True, usable_controls=0),
                TreeAssessment(has_tree=False),
            )
        )
        anchor = WindowAnchor(hwnd=1, app="app.exe", version="1.0")
        self.assertEqual(table.availability(anchor).availability, Availability.USABLE)
        other = WindowAnchor(hwnd=2, app="app.exe", version="2.0")
        self.assertEqual(table.availability(other).availability, Availability.SPARSE)
        empty = WindowAnchor(hwnd=3, app="other.exe", version="1.0")
        self.assertEqual(
            table.availability(empty).availability, Availability.UNAVAILABLE
        )

    def test_the_same_app_with_two_versions_gets_two_independent_entries(self):
        """类别级结论（「UWP 一律可用」）在结构上写不进去：键里有版本。"""
        from minicode.uia.ports import TreeAssessment

        table = self._table(
            (
                TreeAssessment(has_tree=True, usable_controls=3, has_automation_id=True),
                TreeAssessment(has_tree=False),
            )
        )
        first = table.availability(WindowAnchor(hwnd=1, app="app.exe", version="1.0"))
        second = table.availability(WindowAnchor(hwnd=2, app="app.exe", version="2.0"))
        self.assertEqual(first.availability, Availability.USABLE)
        self.assertEqual(second.availability, Availability.UNAVAILABLE)

    def test_no_entry_states_a_category_level_conclusion(self):
        from minicode.uia.ports import TreeAssessment

        table = self._table(
            (
                TreeAssessment(has_tree=True, usable_controls=3, has_automation_id=True),
                TreeAssessment(has_tree=False),
            )
        )
        for version in ("1.0", "2.0"):
            entry = table.availability(
                WindowAnchor(hwnd=1, app="app.exe", version=version)
            )
            self.assertNotIn("一律", entry.reason)
            self.assertNotIn("所有", entry.reason)
            self.assertNotIn("全部", entry.reason)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

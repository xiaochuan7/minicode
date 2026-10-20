"""缝级验证：脱敏器在**动作执行缝**上生效（工单 AC 6、AC 7）。

全程不需要真桌面、不需要 API key、不产生费用——控件树、截图源、输入注入
全是 M0 留下的假实现。

* AC 6 —— 追踪记录不含密码值（密码字段值是占位符），且脱敏可观测（记脱敏计数）；
* AC 7 —— 缝上用假控件树提供含敏感内容的树快照跑一次，断言
  「模型侧观察文本不含密钥模式且密码值是占位符」。
"""

from __future__ import annotations

import unittest

from minicode.action.errors import ErrorCode
from minicode.action.fakes import (
    FakeCaptureSource,
    FakeControlTree,
    FakeInputInjector,
    FakeWindowController,
    VirtualClock,
)
from minicode.action.geometry import VirtualDesktop
from minicode.action.ports import ControlCandidate
from minicode.action.records import ListTraceSink, build_record
from minicode.action.seam import ActionExecutionSeam, SeamRequest
from minicode.redact.config import ModelContextConfig
from minicode.redact.patterns import PASSWORD_PLACEHOLDER
from minicode.redact.redactor import TreeRedactor

_DESKTOP = VirtualDesktop(0, 0, 2560, 1600)

_SECRET = "sk-abcdef0123456789abcdef"
_PASSWORD_PLAINTEXT = "hunter2-correct-horse"
_DRAFT = "我正在输入的草稿"

#: 走脱敏的合法配置（送树进模型上下文 + 脱敏都开）。
_ENABLED = ModelContextConfig(send_tree_to_model=True, redaction_enabled=True)

#: 含敏感内容的树快照：按钮、密码框、正文编辑框、带密钥的值。
_SENSITIVE_CANDIDATES = (
    ControlCandidate(
        name="确定", control_type="Button", rect=(220, 50, 300, 80), window_handle=65784
    ),
    ControlCandidate(
        name="密码",
        control_type="Edit",
        rect=(10, 50, 210, 80),
        value=_PASSWORD_PLAINTEXT,
        is_password=True,
    ),
    ControlCandidate(name="收件人", control_type="Edit", rect=(10, 10, 210, 40), value=_DRAFT),
    ControlCandidate(name="来源", control_type="ComboBox", rect=(10, 90, 210, 120), value=_SECRET),
)


def _trace_dump(trace) -> str:
    """把追踪记录里所有可能带文本的地方拼成一段，便于断言「明文不出现」。

    这里读的是**字段的值**（不是 repr）—— repr 已经把值藏起来了，
    拿 repr 断言等于什么都没断言。
    """
    parts: list[str] = []
    for candidate in (*trace.candidates, *trace.rejected_candidates):
        parts.append(f"{candidate.name}|{candidate.value}|{candidate.help_text}")
    if trace.selected is not None:
        parts.append(f"{trace.selected.name}|{trace.selected.value}")
    if trace.model_view is not None:
        parts.append(trace.model_view.text)
        for control in trace.model_view.controls:
            parts.append(f"{control.name}|{control.value}|{control.help_text}")
    return "\n".join(parts)


class SeamLevelRedactionTest(unittest.TestCase):
    """AC 7：缝上跑一次，模型侧观察文本干净、密码值是占位符。"""

    def _build(self, config, sink, candidates=_SENSITIVE_CANDIDATES):
        clock = VirtualClock()
        injector = FakeInputInjector(journal=[])
        seam = ActionExecutionSeam(
            capture_sources=(FakeCaptureSource(),),
            tree=FakeControlTree(candidates),
            injector=injector,
            window_controller=FakeWindowController(foreground=65784),
            virtual_desktop=_DESKTOP,
            sleeper=clock.sleep,
            clock=clock.time,
            trace_sink=sink,
            model_context_config=config,
        )
        return seam, injector

    def test_the_model_side_text_carries_no_secret_pattern(self):
        sink = ListTraceSink()
        seam, _ = self._build(_ENABLED, sink)
        result = seam.run(SeamRequest(target_description="确定"))
        self.assertTrue(result.ok)
        text = sink.traces[-1].model_view.text
        self.assertNotIn(_SECRET, text)
        self.assertNotIn(_PASSWORD_PLAINTEXT, text)
        self.assertNotIn(_DRAFT, text)
        self.assertIn(PASSWORD_PLACEHOLDER, text)

    def test_the_password_value_is_a_placeholder_in_the_trace_record(self):
        sink = ListTraceSink()
        seam, _ = self._build(_ENABLED, sink)
        seam.run(SeamRequest(target_description="确定"))
        trace = sink.traces[-1]
        passwords = [c for c in trace.candidates if c.is_password]
        self.assertEqual(len(passwords), 1)
        self.assertEqual(passwords[0].value, PASSWORD_PLACEHOLDER)

    def test_the_password_plaintext_appears_in_no_output(self):
        sink = ListTraceSink()
        seam, _ = self._build(_ENABLED, sink)
        seam.run(SeamRequest(target_description="确定"))
        self.assertNotIn(_PASSWORD_PLAINTEXT, _trace_dump(sink.traces[-1]))

    def test_the_redaction_counters_are_recorded(self):
        sink = ListTraceSink()
        seam, _ = self._build(_ENABLED, sink)
        seam.run(SeamRequest(target_description="确定"))
        report = sink.traces[-1].model_view.report
        self.assertEqual(report.password_replacements, 1)
        self.assertEqual(report.dropped_body_fields, 1)
        self.assertEqual(report.regex_hits, 1)
        self.assertGreater(report.output_length, 0)

    def test_the_counters_reach_the_action_record(self):
        sink = ListTraceSink()
        seam, _ = self._build(_ENABLED, sink)
        seam.run(SeamRequest(target_description="确定"))
        record = build_record(sink.traces[-1])
        self.assertIsNotNone(record.redaction)
        self.assertEqual(record.redaction["password_replacements"], 1)
        self.assertGreater(record.redaction["output_length"], 0)

    def test_the_counters_survive_a_failure_after_the_model_view(self):
        # 脱敏已经跑过 → 后面定位失败时，计数**不能**被记成「没走过脱敏」。
        sink = ListTraceSink()
        out_of_bounds = (
            ControlCandidate(
                name="确定", control_type="Button", rect=(3000, 500, 3100, 600), window_handle=65784
            ),
        )
        seam, injector = self._build(_ENABLED, sink, candidates=out_of_bounds)
        result = seam.run(SeamRequest(target_description="确定"))
        self.assertFalse(result.ok)
        self.assertEqual(result.result.error_code, ErrorCode.CLIPPED_BY_DESKTOP)
        self.assertEqual(injector.events, [])
        trace = sink.traces[-1]
        self.assertIsNotNone(trace.model_view)
        self.assertEqual(trace.model_view.report.password_replacements, 0)
        self.assertIsNotNone(build_record(trace).redaction)

    def test_the_model_side_view_is_exactly_the_redactor_output(self):
        sink = ListTraceSink()
        seam, _ = self._build(_ENABLED, sink)
        seam.run(SeamRequest(target_description="确定"))
        expected = TreeRedactor(_ENABLED).render(_SENSITIVE_CANDIDATES)
        self.assertEqual(sink.traces[-1].model_view.text, expected.text)

    def test_a_channel_without_redaction_is_refused_and_nothing_is_executed(self):
        sink = ListTraceSink()
        seam, injector = self._build(ModelContextConfig(), sink)
        result = seam.run(SeamRequest(target_description="确定"))
        self.assertFalse(result.ok)
        self.assertEqual(result.result.error_code, ErrorCode.LOCATE_FAILED)
        self.assertIn("脱敏", result.result.error_message)
        self.assertEqual(injector.events, [])
        self.assertIsNone(sink.traces[-1].model_view)

    def test_a_seam_without_a_model_view_keeps_working(self):
        # 这条通道不把树送模型 → 不需要脱敏器，动作照跑（零模型调用）。
        sink = ListTraceSink()
        clock = VirtualClock()
        injector = FakeInputInjector(journal=[])
        seam = ActionExecutionSeam(
            capture_sources=(FakeCaptureSource(),),
            tree=FakeControlTree(_SENSITIVE_CANDIDATES),
            injector=injector,
            window_controller=FakeWindowController(foreground=65784),
            virtual_desktop=_DESKTOP,
            sleeper=clock.sleep,
            clock=clock.time,
            trace_sink=sink,
        )
        result = seam.run(SeamRequest(target_description="确定"))
        self.assertTrue(result.ok)
        self.assertIsNone(sink.traces[-1].model_view)
        self.assertIsNone(build_record(sink.traces[-1]).redaction)


if __name__ == "__main__":
    unittest.main()

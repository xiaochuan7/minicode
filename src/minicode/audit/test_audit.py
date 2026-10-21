"""**动作日志回放与机器可读导出**的单测（工单 M17）。

只断言**对外可见**的行为：落盘的日志内容、回放报告、导出文件。
不测内部算法。

五条验收标准各对应一个测试类：

* :class:`FieldCompletenessTest` —— AC1（字段完整，缺字段即不合格）；
* :class:`ReplayTest` —— AC2（可回放，重放结果与当时的记录一致）；
* :class:`ExportTest` —— AC3（机器可读导出，能算出成功率 / 三类失败 / 三段耗时分布）；
* :class:`ScreenshotOnlyHashTest` —— AC4（截图只落哈希）；
* :class:`SensitiveContentTest` —— AC5（敏感内容不外泄）。
"""

from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path

from minicode.action.errors import ErrorCode, failure_type_of
from minicode.action.fakes import (
    FakeCaptureSource,
    FakeControlTree,
    FakeInputInjector,
    FakeWindowController,
    VirtualClock,
)
from minicode.action.geometry import VirtualDesktop
from minicode.action.ports import ControlCandidate
from minicode.action.protocol import ActionResult
from minicode.action.records import ActionLog, ListTraceSink, build_record, stage_share
from minicode.action.seam import ActionExecutionSeam, SeamRequest
from minicode.audit.__main__ import read_log, read_telemetry
from minicode.audit.export import export_records, load_export, summarize_records
from minicode.audit.replay import missing_fields, replay_records
from minicode.redact.log import sanitize_text, scan_record
from minicode.redact.patterns import redact_secrets
from minicode.verification.layer import VerificationBaseline, VerificationResult

_DESKTOP = VirtualDesktop(0, 0, 2560, 1600)
_HASH = "a" * 64


class _FakeVerifier:
    """假校验器：动作前后各给一份状态（M11 的形状）。"""

    def begin(self, request):
        return VerificationBaseline(candidates=(), reachable=True, error="")

    def verify(self, request):
        return VerificationResult(
            outcome="changed",
            satisfied=None,
            state_before={"target": request.target_description, "present": True},
            state_after={"target": request.target_description, "present": True},
        )


def _candidate(name="开始", rect=(1016, 1528, 1083, 1600), window_handle=65784):
    """一个**带窗口标题**的控件候选（窗口标题是 UIA 通道给的那一份）。"""
    return ControlCandidate(
        name=name,
        control_type="Button",
        rect=rect,
        window_handle=window_handle,
        window_title="任务栏",
    )


def _build_seam(candidates=None, injector=None, trace_sink=None, verifier=None):
    clock = VirtualClock()
    injector = injector or FakeInputInjector()
    controller = FakeWindowController(foreground=65784)
    seam = ActionExecutionSeam(
        capture_sources=(FakeCaptureSource(width=2560, height=1600),),
        tree=FakeControlTree(candidates if candidates is not None else (_candidate(),)),
        injector=injector,
        window_controller=controller,
        virtual_desktop=_DESKTOP,
        sleeper=clock.sleep,
        clock=clock.time,
        trace_sink=trace_sink,
        verifier=verifier,
    )
    return seam, injector


def _run_steps(target="开始", candidates=None, injector=None, with_verifier=False):
    """跑一步，返回那条动作记录（**落盘形状**的 dict）。"""
    sink = ListTraceSink()
    seam, _ = _build_seam(
        candidates=candidates,
        injector=injector,
        trace_sink=sink,
        verifier=_FakeVerifier() if with_verifier else None,
    )
    seam.run(SeamRequest(target_description=target))
    return [build_record(trace).to_dict() for trace in sink.traces]


def _synthetic_record(
    *,
    ok=True,
    error_code=None,
    failures=None,
    locate_ms=10.0,
    decision_ms=0.0,
    execute_verify_ms=5.0,
    action="click",
    timestamp="2026-10-21T10:00:00+08:00",
):
    """一条记录（统计与导出的输入）。

    ⚠️ **从一条真实记录派生**（跑一遍缝得到的），再改要改的字段 ——
    手抄整份落盘形状的话，形状一漂这些测试就成空断言。
    三段耗时的**占比**由那三个值算出来（占比是算出来的，不是另一份数据）；
    失败类型只在**定位阶段**失败时才有（与缝里的做法一样）。
    """
    record = copy.deepcopy(_run_steps(with_verifier=True)[0])
    failures = failures or {"locate": None, "decision": None, "execute": None}
    record["timestamp"] = timestamp
    record["action"] = action
    record["result"] = {
        **record["result"],
        "ok": ok,
        "error_code": error_code,
        "error_message": None if ok else "失败了",
    }
    record["timings_ms"] = {
        "locate": locate_ms,
        "decision": decision_ms,
        "execute_verify": execute_verify_ms,
    }
    record["timings_share"] = stage_share(locate_ms, decision_ms, execute_verify_ms)
    record["failures"] = failures
    record["failure_type"] = ""
    if failures.get("locate") and error_code:
        record["failure_type"] = failure_type_of(ErrorCode(error_code)).value
    return record


class FieldCompletenessTest(unittest.TestCase):
    """AC1：字段完整 —— 时间 / 动作类型 / 目标窗口句柄与标题 / 落点坐标 /
    截图哈希 / 动作前后状态 / 结果（含错误码）；缺字段即不合格。"""

    def test_a_real_record_has_every_required_field(self):
        records = _run_steps()
        self.assertEqual(len(records), 1)
        self.assertEqual(missing_fields(records[0]), [])

    def test_a_missing_field_is_named(self):
        record = dict(_run_steps()[0])
        del record["screenshot_sha256"]
        self.assertIn("截图哈希", missing_fields(record))

    def test_the_target_window_handle_and_title_are_required_once_a_target_was_picked(self):
        record = dict(_run_steps()[0])
        record["target"] = dict(record["target"])
        record["target"]["window_title"] = None
        self.assertIn("目标窗口标题", missing_fields(record))

    def test_the_state_before_and_after_are_required_once_the_verifier_ran(self):
        record = dict(_run_steps(with_verifier=True)[0])
        record["state"] = {"before": None, "after": None}
        self.assertIn("动作前状态", missing_fields(record))
        self.assertIn("动作后状态", missing_fields(record))

    def test_the_landing_point_must_be_in_the_physical_surface_space(self):
        record = dict(_run_steps()[0])
        record["point"] = {"x": 1049, "y": 1564, "space": "crop"}
        self.assertIn("落点坐标空间", missing_fields(record))

    def test_an_action_that_needs_no_landing_point_must_not_carry_one(self):
        record = dict(_run_steps()[0])
        record["action"] = "wait"
        self.assertIn("落点坐标", missing_fields(record))

    def test_a_failed_step_must_carry_an_error_code(self):
        record = dict(_run_steps()[0])
        record["result"] = dict(record["result"])
        record["result"]["ok"] = False
        record["result"]["error_code"] = None
        self.assertIn("结果.error_code", missing_fields(record))


class ReplayTest(unittest.TestCase):
    """AC2：可回放 —— 用日志能重放出每一步的动作与结果，
    且重放结果与当时的记录一致。"""

    def test_replay_reproduces_every_step(self):
        records = _run_steps()
        report = replay_records(records)
        self.assertEqual(len(report.steps), 1)
        step = report.steps[0]
        self.assertEqual(step.action_kind, "click")
        self.assertEqual(step.point, (1049, 1564))
        self.assertTrue(step.ok)
        self.assertEqual(step.timestamp, records[0]["timestamp"])

    def test_replay_reports_a_consistent_log(self):
        report = replay_records(_run_steps())
        self.assertFalse(report.problems)
        self.assertTrue(report.consistent)

    def test_replaying_the_actions_reproduces_the_recorded_results(self):
        records = _run_steps()

        def execute(action):
            return ActionResult(ok=True)

        report = replay_records(records, execute=execute)
        self.assertTrue(report.consistent)

    def test_a_replay_that_disagrees_with_the_record_is_reported(self):
        records = _run_steps()

        def execute(action):
            return ActionResult(ok=False, error_code=ErrorCode.EXECUTE_FAILED)

        report = replay_records(records, execute=execute)
        self.assertFalse(report.consistent)
        self.assertTrue(any("回放" in problem for problem in report.problems))

    def test_a_tampered_result_is_reported(self):
        record = _synthetic_record()
        record["result"] = dict(record["result"])
        record["result"]["ok"] = False
        report = replay_records([record])
        self.assertFalse(report.consistent)

    def test_a_failure_that_is_not_classified_is_reported(self):
        record = _synthetic_record(
            ok=False,
            error_code=ErrorCode.TARGET_NOT_FOUND.value,
            failures={"locate": None, "decision": None, "execute": None},
        )
        report = replay_records([record])
        self.assertFalse(report.consistent)
        self.assertTrue(any("三类失败" in problem for problem in report.problems))

    def test_a_timestamp_going_backwards_is_reported(self):
        first = _synthetic_record(timestamp="2026-10-21T10:00:02+08:00")
        second = _synthetic_record(timestamp="2026-10-21T10:00:01+08:00")
        report = replay_records([first, second])
        self.assertFalse(report.consistent)

    def test_a_wrong_stage_share_is_reported(self):
        record = _synthetic_record(locate_ms=10.0, execute_verify_ms=10.0)
        record["timings_share"] = {"locate": 1.0, "decision": 0.0, "execute_verify": 0.0}
        report = replay_records([record])
        self.assertFalse(report.consistent)
        self.assertTrue(any("占比" in problem for problem in report.problems))

    def test_a_landing_point_outside_the_recorded_control_box_is_reported(self):
        """落点与控件框**都是日志里的字段** —— 两者的关系不必信任何外部说法。"""
        record = _synthetic_record()
        record["point"] = {"x": 10, "y": 10, "space": "physical-surface"}
        report = replay_records([record])
        self.assertFalse(report.consistent)
        self.assertTrue(any("控件框外" in problem for problem in report.problems))


class ExportTest(unittest.TestCase):
    """AC3：机器可读导出 —— 导出格式可被脚本解析，且从导出文件能算出
    成功率、三类失败分布、三段耗时分布（不依赖人工看）。"""

    def _records(self):
        return [
            _synthetic_record(locate_ms=10.0, execute_verify_ms=5.0),
            _synthetic_record(
                ok=False,
                error_code=ErrorCode.TARGET_NOT_FOUND.value,
                failures={"locate": ErrorCode.TARGET_NOT_FOUND.value, "decision": None, "execute": None},
                locate_ms=20.0,
                execute_verify_ms=0.0,
            ),
            _synthetic_record(
                ok=False,
                error_code=ErrorCode.EXECUTE_FAILED.value,
                failures={"locate": None, "decision": None, "execute": ErrorCode.EXECUTE_FAILED.value},
                locate_ms=30.0,
                execute_verify_ms=15.0,
            ),
        ]

    def test_the_export_file_is_parseable_by_a_script(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = export_records(self._records(), Path(tmp) / "export.json")
            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(payload["format"], "minicode.action-log/1")
            self.assertEqual(len(payload["records"]), 3)

    def test_the_success_rate_is_computable_from_the_export_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = export_records(self._records(), Path(tmp) / "export.json")
            payload = json.loads(path.read_text(encoding="utf-8"))
            records = payload["records"]
            ok = sum(1 for record in records if record["result"]["ok"])
            self.assertAlmostEqual(ok / len(records), payload["summary"]["success_rate"])
            self.assertAlmostEqual(payload["summary"]["success_rate"], 1 / 3)

    def test_the_three_failure_classes_are_distributed_in_the_export_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = export_records(self._records(), Path(tmp) / "export.json")
            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(
                payload["summary"]["failures"],
                {"locate": 1, "decision": 0, "execute": 1},
            )

    def test_the_three_stage_timing_distribution_is_in_the_export_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = export_records(self._records(), Path(tmp) / "export.json")
            payload = json.loads(path.read_text(encoding="utf-8"))
            stages = payload["summary"]["stages_ms"]
            self.assertEqual(stages["locate"]["median"], 20.0)
            self.assertEqual(stages["locate"]["p90"], 30.0)
            self.assertEqual(stages["locate"]["max"], 30.0)
            self.assertEqual(stages["execute_verify"]["median"], 5.0)

    def test_the_summary_can_be_recomputed_from_the_exported_records(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = export_records(self._records(), Path(tmp) / "export.json")
            payload = load_export(path)
            self.assertEqual(
                summarize_records(payload["records"]).to_dict(),
                payload["summary"],
            )

    def test_the_export_carries_the_log_it_came_from(self):
        with tempfile.TemporaryDirectory() as tmp:
            records = _run_steps()
            path = export_records(records, Path(tmp) / "export.json")
            payload = load_export(path)
            self.assertEqual(payload["records"], records)

    def test_the_export_desensitises_the_records_it_is_given(self):
        """导出**自己也要脱敏** —— 不能指望调用方已经洗过一遍。"""
        secret = "sk-abcdefghijklmnop"
        with tempfile.TemporaryDirectory() as tmp:
            record = _synthetic_record()
            record["target"] = {**record["target"], "name": f"登录 {secret}"}
            path = export_records([record], Path(tmp) / "export.json")
            text = path.read_text(encoding="utf-8")
            self.assertNotIn(secret, text)
            payload = json.loads(text)
            self.assertEqual(payload["summary"]["sensitive_hits"], 0)

    def test_the_decision_failures_come_from_the_telemetry_report(self):
        """**决策失败不在动作记录里**（它由决策层单独报）→ 给了遥测才算得全。"""
        telemetry = {"failures": {"locate": 0, "decision": 2, "execute": 0}}
        with tempfile.TemporaryDirectory() as tmp:
            path = export_records(
                self._records(), Path(tmp) / "export.json", telemetry=telemetry
            )
            payload = load_export(path)
            self.assertEqual(payload["summary"]["failures"]["decision"], 2)
            self.assertEqual(payload["summary"]["failure_sources"]["decision"], "telemetry")

    def test_without_a_telemetry_report_the_decision_failures_are_marked_unavailable(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = export_records(self._records(), Path(tmp) / "export.json")
            payload = load_export(path)
            self.assertEqual(payload["summary"]["failures"]["decision"], 0)
            self.assertEqual(
                payload["summary"]["failure_sources"]["decision"], "unavailable"
            )


class ScreenshotOnlyHashTest(unittest.TestCase):
    """AC4：截图只落哈希 —— 日志与导出里只有截图哈希，没有图像内容。"""

    def test_no_image_content_lands_in_the_log(self):
        with tempfile.TemporaryDirectory() as tmp:
            sink = ListTraceSink()
            seam, _ = _build_seam(trace_sink=sink)
            seam.run(SeamRequest(target_description="开始"))
            ActionLog(tmp).append(build_record(sink.traces[-1]))
            payload = ActionLog(tmp).records()[0]
            self.assertEqual(len(payload["screenshot_sha256"]), 64)
            int(payload["screenshot_sha256"], 16)
            for name in Path(tmp).iterdir():
                self.assertFalse(name.suffix.lower() in (".png", ".jpg", ".jpeg", ".bmp"))

    def test_the_export_has_no_image_content_either(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = export_records(_run_steps(), Path(tmp) / "export.json")
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("data:image", text)
            self.assertNotIn("base64", text)
            payload = json.loads(text)
            self.assertEqual(len(payload["records"][0]["screenshot_sha256"]), 64)


class SensitiveContentTest(unittest.TestCase):
    """AC5：敏感内容不外泄 —— 日志里不出现密码、密钥等模式的明文
    （用脱敏器同一套规则检查）。"""

    def test_a_secret_in_the_control_name_is_redacted_before_it_lands(self):
        secret = "sk-abcdefghijklmnop"
        with tempfile.TemporaryDirectory() as tmp:
            sink = ListTraceSink()
            seam, _ = _build_seam(
                candidates=(_candidate(name=f"登录 {secret}"),),
                trace_sink=sink,
            )
            seam.run(SeamRequest(target_description="登录"))
            ActionLog(tmp).append(build_record(sink.traces[-1]))
            payload = ActionLog(tmp).records()[0]
            self.assertNotIn(secret, json.dumps(payload, ensure_ascii=False))
            self.assertEqual(payload["target"]["name"], "登录 [REDACTED]")

    def test_the_redactor_rules_are_the_ones_the_desensitiser_uses(self):
        secret = "token sk-abcdefghijklmnop"
        cleaned = sanitize_text(secret)
        self.assertNotIn("sk-abcdefghijklmnop", cleaned)
        # 脱敏器自己怎么说，就是怎么说（**同一套规则**，不是另写一遍）。
        self.assertEqual(cleaned, redact_secrets(secret)[0])

    def test_the_screenshot_hash_is_not_mangled_by_the_redactor(self):
        with tempfile.TemporaryDirectory() as tmp:
            sink = ListTraceSink()
            seam, _ = _build_seam(trace_sink=sink)
            seam.run(SeamRequest(target_description="开始"))
            ActionLog(tmp).append(build_record(sink.traces[-1]))
            payload = ActionLog(tmp).records()[0]
            self.assertEqual(len(payload["screenshot_sha256"]), 64)
            self.assertNotEqual(payload["screenshot_sha256"], "[REDACTED]")

    def test_a_written_log_has_no_sensitive_plaintext_left(self):
        secret = "sk-abcdefghijklmnop"
        with tempfile.TemporaryDirectory() as tmp:
            sink = ListTraceSink()
            seam, _ = _build_seam(
                candidates=(_candidate(name=f"登录 {secret}"),),
                trace_sink=sink,
            )
            seam.run(SeamRequest(target_description="登录"))
            ActionLog(tmp).append(build_record(sink.traces[-1]))
            for record in ActionLog(tmp).records():
                self.assertEqual(scan_record(record), [])

    def test_the_export_has_no_sensitive_plaintext_left(self):
        secret = "sk-abcdefghijklmnop"
        with tempfile.TemporaryDirectory() as tmp:
            path = export_records(
                _run_steps(candidates=(_candidate(name=f"登录 {secret}"),)),
                Path(tmp) / "export.json",
            )
            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(payload["summary"]["sensitive_hits"], 0)
            for record in payload["records"]:
                self.assertEqual(scan_record(record), [])


class LogReaderTest(unittest.TestCase):
    """离线工具读日志：两种落盘形状都认（两种都在本项目里真的产生）。"""

    def test_a_jsonl_log_is_read(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "action-records.jsonl"
            path.write_text(
                "\n".join(json.dumps(record, ensure_ascii=False) for record in _run_steps()),
                encoding="utf-8",
            )
            self.assertEqual(len(read_log(path)), 1)

    def test_a_json_array_log_is_read(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "action-records.json"
            path.write_text(
                json.dumps(_run_steps(), ensure_ascii=False),
                encoding="utf-8",
            )
            self.assertEqual(len(read_log(path)), 1)

    def test_a_decision_report_is_read_as_the_telemetry_report(self):
        """决策层写的 ``decision-report.json`` 里有 ``telemetry`` 一节。"""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "decision-report.json"
            path.write_text(
                json.dumps(
                    {"stop_reason": "done", "telemetry": {"failures": {"decision": 2}}},
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            self.assertEqual(read_telemetry(path)["failures"]["decision"], 2)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

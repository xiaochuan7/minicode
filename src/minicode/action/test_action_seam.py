"""动作执行缝与动作记录的单测。

缝上只断言对外可见的契约字段：动作、最终落点、意图、错误码、注入序列、记录内容。
"""

from __future__ import annotations

import dataclasses
import json
import sys
import tempfile
import unittest
from pathlib import Path

from minicode.action.errors import ErrorCode
from minicode.action.executor import DoubleClickParams
from minicode.action.fakes import (
    FakeCaptureSource,
    FakeControlTree,
    FakeInputInjector,
    FakeWindowController,
    VirtualClock,
)
from minicode.action.geometry import VirtualDesktop
from minicode.action.ports import CapturedFrame, ControlCandidate, ControlTreeSnapshot
from minicode.action.protocol import ActionKind
from minicode.action.records import ActionLog, ListTraceSink, build_record
from minicode.action.seam import ActionExecutionSeam, SeamRequest, SeamResult

_DESKTOP = VirtualDesktop(0, 0, 2560, 1600)
_FRAME = CapturedFrame(width=2560, height=1600, payload=b"\x01" * 64, backend="fake")


def _candidate(name="开始", rect=(1016, 1528, 1083, 1600), window_handle=65784):
    return ControlCandidate(
        name=name, control_type="Button", rect=rect, window_handle=window_handle
    )


def _build_seam(
    candidates=None,
    injector=None,
    controller=None,
    sources=None,
    tree=None,
    desktop=_DESKTOP,
    trace_sink=None,
    double_click_params=None,
):
    clock = VirtualClock()
    journal = []
    injector = injector or FakeInputInjector(journal=journal)
    controller = controller or FakeWindowController(foreground=65784, journal=journal)
    seam = ActionExecutionSeam(
        capture_sources=sources if sources is not None else (FakeCaptureSource(),),
        tree=tree or FakeControlTree(candidates if candidates is not None else (_candidate(),)),
        injector=injector,
        window_controller=controller,
        virtual_desktop=desktop,
        sleeper=clock.sleep,
        clock=clock.time,
        trace_sink=trace_sink,
        double_click_params=double_click_params,
    )
    return seam, injector, controller, clock


class SeamBasicsTest(unittest.TestCase):
    """验收项 1：动作执行缝可跑且零依赖。"""

    def test_one_call_returns_action_landing_point_and_intent(self):
        seam, _, _, _ = _build_seam()
        result = seam.run(SeamRequest(target_description="开始"))
        self.assertTrue(result.ok)
        self.assertEqual(result.action.kind, ActionKind.CLICK)
        self.assertIsNotNone(result.final_point)
        self.assertIn("开始", result.intent)

    def test_landing_point_falls_inside_the_selected_candidate_box(self):
        seam, _, _, _ = _build_seam()
        result = seam.run(SeamRequest(target_description="开始"))
        left, top, right, bottom = _candidate().rect
        x, y = result.final_point
        self.assertTrue(left <= x < right and top <= y < bottom)

    def test_landing_point_is_the_candidate_box_centre(self):
        seam, _, _, _ = _build_seam()
        result = seam.run(SeamRequest(target_description="开始"))
        left, top, right, bottom = _candidate().rect
        self.assertEqual(result.final_point, ((left + right) // 2, (top + bottom) // 2))

    def test_importing_the_seam_does_not_pull_in_the_real_desktop_modules(self):
        seam, _, _, _ = _build_seam()
        seam.run(SeamRequest(target_description="开始"))
        self.assertNotIn("minicode.exec.input", sys.modules)
        self.assertNotIn("minicode.exec.capture", sys.modules)

    def test_frame_and_tree_snapshot_can_be_given_directly(self):
        seam, _, _, _ = _build_seam()
        result = seam.run(
            SeamRequest(
                target_description="开始",
                frame=_FRAME,
                tree_snapshot=ControlTreeSnapshot((_candidate(),)),
            )
        )
        self.assertTrue(result.ok)
        self.assertEqual(result.final_point, (1049, 1564))

    def test_target_that_matches_nothing_is_a_locate_failure(self):
        seam, injector, _, _ = _build_seam()
        result = seam.run(SeamRequest(target_description="不存在的按钮"))
        self.assertFalse(result.ok)
        self.assertEqual(result.result.error_code, ErrorCode.TARGET_NOT_FOUND)
        self.assertEqual(injector.events, [])


class AmbiguityTest(unittest.TestCase):
    """验收项 5：同名多候选拒绝。"""

    def test_two_same_name_candidates_are_rejected_and_the_candidate_set_is_returned(self):
        candidates = (_candidate(rect=(10, 20, 60, 70)), _candidate(rect=(100, 200, 150, 250)))
        seam, injector, _, _ = _build_seam(candidates=candidates)
        result = seam.run(SeamRequest(target_description="开始"))
        self.assertFalse(result.ok)
        self.assertEqual(result.result.error_code, ErrorCode.AMBIGUOUS_TARGET)
        self.assertEqual(len(result.rejected_candidates), 2)

    def test_ambiguity_rejection_injects_nothing(self):
        candidates = (_candidate(rect=(10, 20, 60, 70)), _candidate(rect=(100, 200, 150, 250)))
        seam, injector, _, _ = _build_seam(candidates=candidates)
        seam.run(SeamRequest(target_description="开始"))
        self.assertEqual(injector.events, [])

    def test_ambiguity_rejection_produces_no_landing_point(self):
        candidates = (_candidate(rect=(10, 20, 60, 70)), _candidate(rect=(100, 200, 150, 250)))
        seam, _, _, _ = _build_seam(candidates=candidates)
        result = seam.run(SeamRequest(target_description="开始"))
        self.assertIsNone(result.final_point)


class OutOfBoundsTest(unittest.TestCase):
    """验收项 10：越界不夹取。"""

    def test_landing_point_outside_the_virtual_desktop_is_rejected(self):
        seam, injector, _, _ = _build_seam(
            candidates=(_candidate(rect=(3000, 500, 3100, 600)),)
        )
        result = seam.run(SeamRequest(target_description="开始"))
        self.assertFalse(result.ok)
        self.assertEqual(result.result.error_code, ErrorCode.CLIPPED_BY_DESKTOP)

    def test_out_of_bounds_rejection_injects_nothing(self):
        seam, injector, _, _ = _build_seam(
            candidates=(_candidate(rect=(3000, 500, 3100, 600)),)
        )
        seam.run(SeamRequest(target_description="开始"))
        self.assertEqual(injector.events, [])

    def test_out_of_bounds_rejection_does_not_clamp_to_the_edge(self):
        seam, _, _, _ = _build_seam(candidates=(_candidate(rect=(3000, 500, 3100, 600)),))
        result = seam.run(SeamRequest(target_description="开始"))
        self.assertIsNone(result.final_point)

    def test_landing_point_two_pixels_outside_is_still_executed(self):
        seam, injector, _, _ = _build_seam(
            candidates=(_candidate(rect=(2558, 500, 2566, 600)),)
        )
        result = seam.run(SeamRequest(target_description="开始"))
        self.assertTrue(result.ok)
        self.assertTrue(injector.events)

    def test_out_of_bounds_is_recorded_as_a_locate_failure(self):
        sink = ListTraceSink()
        seam, _, _, _ = _build_seam(
            candidates=(_candidate(rect=(3000, 500, 3100, 600)),), trace_sink=sink
        )
        seam.run(SeamRequest(target_description="开始"))
        trace = sink.traces[-1]
        self.assertEqual(trace.failures["locate"], ErrorCode.CLIPPED_BY_DESKTOP.value)


class CaptureSourceTest(unittest.TestCase):
    """验收项 4 / 12：抓屏后端有规矩、0×0 画面跳过该捕获源。"""

    def test_zero_by_zero_source_is_skipped_and_the_next_source_is_used(self):
        empty = FakeCaptureSource(width=0, height=0, name="empty")
        good = FakeCaptureSource(width=2560, height=1600, name="good")
        seam, _, _, _ = _build_seam(sources=(empty, good))
        result = seam.run(SeamRequest(target_description="开始"))
        self.assertTrue(result.ok)
        self.assertEqual(empty.calls, 1)
        self.assertEqual(good.calls, 1)

    def test_all_sources_empty_reports_capture_empty_and_produces_no_landing_point(self):
        empty = FakeCaptureSource(width=0, height=0, name="empty")
        seam, injector, _, _ = _build_seam(sources=(empty,))
        result = seam.run(SeamRequest(target_description="开始"))
        self.assertFalse(result.ok)
        self.assertEqual(result.result.error_code, ErrorCode.CAPTURE_EMPTY)
        self.assertIsNone(result.final_point)
        self.assertEqual(injector.events, [])

    def test_a_given_zero_by_zero_frame_is_reported_as_capture_empty(self):
        seam, injector, _, _ = _build_seam()
        result = seam.run(
            SeamRequest(
                target_description="开始",
                frame=CapturedFrame(width=0, height=0, payload=b""),
            )
        )
        self.assertFalse(result.ok)
        self.assertEqual(result.result.error_code, ErrorCode.CAPTURE_EMPTY)
        self.assertIsNone(result.final_point)
        self.assertEqual(injector.events, [])


class SentinelTest(unittest.TestCase):
    """验收项 12：哨兵值不当作合法坐标。"""

    def test_cursor_sentinel_is_reported_as_execution_failure(self):
        injector = FakeInputInjector(readback_override=(-32000, -32000))
        seam, _, _, _ = _build_seam(injector=injector)
        result = seam.run(SeamRequest(target_description="开始"))
        self.assertFalse(result.ok)
        self.assertEqual(result.result.error_code, ErrorCode.CURSOR_SENTINEL)

    def test_cursor_sentinel_is_recorded_in_the_execution_failure_field(self):
        injector = FakeInputInjector(readback_override=(-32000, -32000))
        sink = ListTraceSink()
        seam, _, _, _ = _build_seam(injector=injector, trace_sink=sink)
        seam.run(SeamRequest(target_description="开始"))
        trace = sink.traces[-1]
        self.assertEqual(trace.failures["execute"], ErrorCode.CURSOR_SENTINEL.value)
        self.assertIsNone(trace.failures["locate"])
        self.assertIsNone(trace.failures["decision"])


class WindowFrontTest(unittest.TestCase):
    """验收项 17：目标窗口被遮挡时先置前。"""

    def test_occluded_target_window_is_brought_to_front_before_the_click(self):
        controller = FakeWindowController(foreground=11111, journal=[])
        injector = FakeInputInjector(journal=controller.journal)
        seam, _, controller, _ = _build_seam(injector=injector, controller=controller)
        result = seam.run(SeamRequest(target_description="开始"))
        self.assertTrue(result.ok)
        self.assertEqual(controller.foreground_window(), 65784)
        kinds = [e[0] for e in controller.journal]
        self.assertLess(kinds.index("bring_to_front"), kinds.index("button_down"))

    def test_already_foreground_target_window_is_not_raised_again(self):
        controller = FakeWindowController(foreground=65784, journal=[])
        injector = FakeInputInjector(journal=controller.journal)
        seam, _, controller, _ = _build_seam(injector=injector, controller=controller)
        seam.run(SeamRequest(target_description="开始"))
        self.assertEqual(controller.brought, [])

    def test_failed_raise_aborts_before_injecting(self):
        controller = FakeWindowController(foreground=11111, bring_fails=True, journal=[])
        injector = FakeInputInjector(journal=controller.journal)
        seam, _, controller, _ = _build_seam(injector=injector, controller=controller)
        result = seam.run(SeamRequest(target_description="开始"))
        self.assertFalse(result.ok)
        injected = [e for e in injector.events if e[0] in ("move", "button_down", "button_up")]
        self.assertEqual(injected, [])

    def test_a_child_window_candidate_is_not_mistaken_for_a_failed_raise(self):
        """候选句柄是子窗口时，前台窗口是它的根窗口——句柄口径不同但并不算失败。"""
        controller = FakeWindowController(
            foreground=11111, journal=[], root_of={2001: 1000}
        )
        injector = FakeInputInjector(journal=controller.journal)
        candidate = ControlCandidate(
            name="开始", control_type="Button", rect=(1016, 1528, 1083, 1600), window_handle=2001
        )
        seam, _, controller, _ = _build_seam(
            candidates=(candidate,), injector=injector, controller=controller
        )
        result = seam.run(SeamRequest(target_description="开始"))
        self.assertTrue(result.ok)
        self.assertEqual(controller.brought, [2001])
        injected = [e for e in injector.events if e[0] in ("move", "button_down", "button_up")]
        self.assertEqual(len(injected), 3)


class InjectionBlockedTest(unittest.TestCase):
    """验收项 20：已知欠账如实写进记录。"""

    def test_blocked_injection_is_reported_as_execution_failure(self):
        injector = FakeInputInjector(fail_injection=True)
        seam, _, _, _ = _build_seam(injector=injector)
        result = seam.run(SeamRequest(target_description="开始"))
        self.assertFalse(result.ok)
        self.assertEqual(result.result.error_code, ErrorCode.INJECTION_BLOCKED)

    def test_blocked_injection_records_the_c07_debt(self):
        injector = FakeInputInjector(fail_injection=True)
        seam, _, _, _ = _build_seam(injector=injector)
        result = seam.run(SeamRequest(target_description="开始"))
        self.assertTrue(any("C-07" in note for note in result.result.notes))


class PortShapeTest(unittest.TestCase):
    """验收项 2：换掉替身不改变缝的端口形状。"""

    def test_request_and_result_keep_their_public_fields(self):
        self.assertEqual(
            [f.name for f in dataclasses.fields(SeamRequest)],
            [
                "target_description",
                "frame",
                "tree_snapshot",
                "action_kind",
                "params",
                "control_type",
            ],
        )
        self.assertEqual(
            [f.name for f in dataclasses.fields(SeamResult)],
            ["action", "final_point", "intent", "result", "rejected_candidates"],
        )

    def test_swapping_the_fakes_does_not_change_the_output_shape(self):
        seam_a, _, _, _ = _build_seam()
        result_a = seam_a.run(SeamRequest(target_description="开始"))
        seam_b, _, _, _ = _build_seam(
            sources=(FakeCaptureSource(width=800, height=600, name="other"),)
        )
        result_b = seam_b.run(SeamRequest(target_description="开始"))
        self.assertEqual(
            [f.name for f in dataclasses.fields(result_a)],
            [f.name for f in dataclasses.fields(result_b)],
        )
        self.assertEqual(result_a.action.kind, result_b.action.kind)
        self.assertIsInstance(result_a.final_point, tuple)
        self.assertIsInstance(result_b.final_point, tuple)

    def test_the_seam_needs_only_the_standard_library(self):
        package = Path(__file__).resolve().parent
        for path in package.glob("*.py"):
            if path.name.startswith("test_"):
                continue
            text = path.read_text(encoding="utf-8")
            for banned in ("import numpy", "import mss", "import PIL", "from PIL"):
                self.assertNotIn(banned, text, msg=f"{path.name} 引入了第三方依赖 {banned}")


class MultiScreenTest(unittest.TestCase):
    """验收项 11：多屏语义（用可注入的虚拟桌面度量构造）。"""

    def test_landing_point_is_correct_on_a_negative_origin_virtual_desktop(self):
        desktop = VirtualDesktop(origin_x=-1920, origin_y=0, width=4480, height=1600)
        candidate = ControlCandidate(
            name="开始", control_type="Button", rect=(-1900, 1528, -1800, 1600), window_handle=1
        )
        frame = CapturedFrame(
            width=4480, height=1600, left=-1920, top=0, payload=b"\x01" * 16, backend="fake"
        )
        seam, injector, _, _ = _build_seam(candidates=(candidate,), desktop=desktop)
        result = seam.run(SeamRequest(target_description="开始", frame=frame))
        self.assertTrue(result.ok)
        self.assertEqual(result.final_point, (-1850, 1564))
        move = [e for e in injector.events if e[0] == "move"][0]
        self.assertEqual(move[1:], (-1850, 1564))

    def test_a_point_outside_the_negative_origin_desktop_is_still_rejected(self):
        desktop = VirtualDesktop(origin_x=-1920, origin_y=0, width=4480, height=1600)
        candidate = ControlCandidate(
            name="开始", control_type="Button", rect=(3000, 1528, 3100, 1600), window_handle=1
        )
        frame = CapturedFrame(
            width=4480, height=1600, left=-1920, top=0, payload=b"\x01" * 16, backend="fake"
        )
        seam, injector, _, _ = _build_seam(candidates=(candidate,), desktop=desktop)
        result = seam.run(SeamRequest(target_description="开始", frame=frame))
        self.assertFalse(result.ok)
        self.assertEqual(result.result.error_code, ErrorCode.CLIPPED_BY_DESKTOP)
        self.assertEqual(injector.events, [])


class ActionRecordTest(unittest.TestCase):
    """验收项 18 / 19：只落哈希 + 恰好一条动作记录。"""

    def _run_once(self, log_dir, frame=_FRAME):
        sink = ListTraceSink()
        seam, _, _, _ = _build_seam(trace_sink=sink)
        seam.run(SeamRequest(target_description="开始", frame=frame))
        log = ActionLog(log_dir)
        record = build_record(sink.traces[-1])
        log.append(record)
        return log, record

    def test_exactly_one_record_is_written_per_action(self):
        with tempfile.TemporaryDirectory() as tmp:
            log, _ = self._run_once(tmp)
            self.assertEqual(len(log.records()), 1)

    def test_output_directory_contains_no_image_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            log, _ = self._run_once(tmp)
            files = [p.name for p in Path(tmp).iterdir()]
            self.assertTrue(files)
            for name in files:
                self.assertFalse(name.lower().endswith((".png", ".jpg", ".jpeg", ".bmp", ".gif")))

    def test_same_frame_twice_gives_the_same_digest(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, first = self._run_once(tmp)
            _, second = self._run_once(tmp)
            self.assertEqual(first.screenshot_sha256, second.screenshot_sha256)

    def test_different_frames_give_different_digests(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, first = self._run_once(tmp)
            _, second = self._run_once(tmp, frame=CapturedFrame(2560, 1600, payload=b"\x02" * 64))
            self.assertNotEqual(first.screenshot_sha256, second.screenshot_sha256)

    def test_screenshot_digest_is_sha256_hex(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, record = self._run_once(tmp)
            self.assertEqual(len(record.screenshot_sha256), 64)
            int(record.screenshot_sha256, 16)

    def test_record_carries_every_required_field(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, record = self._run_once(tmp)
            payload = record.to_dict()
            self.assertEqual(payload["protocol_version"], "cua.action/1")
            self.assertTrue(payload["timestamp"])
            self.assertEqual(payload["action"], "click")
            self.assertEqual(payload["target"]["name"], "开始")
            self.assertEqual(payload["target"]["rect"], [1016, 1528, 1083, 1600])
            self.assertEqual(payload["point"]["space"], "physical-surface")
            self.assertEqual(payload["capture"], {"width": 2560, "height": 1600})
            self.assertTrue(payload["screenshot_sha256"])
            self.assertIn("error_code", payload["result"])

    def test_record_carries_the_actual_sent_size_and_the_image_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, record = self._run_once(tmp)
            payload = record.to_dict()
            self.assertEqual(payload["sent_size"], {"width": 2560, "height": 1600})
            self.assertEqual(payload["image_source"], "virtual-desktop|fake")

    def test_decision_segment_is_always_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, record = self._run_once(tmp)
            self.assertEqual(record.decision_ms, 0.0)

    def test_three_failure_fields_are_present(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, record = self._run_once(tmp)
            payload = record.to_dict()
            self.assertEqual(set(payload["failures"]), {"locate", "decision", "execute"})

    def test_record_is_machine_parsable_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            log, _ = self._run_once(tmp)
            for line in log.path.read_text(encoding="utf-8").splitlines():
                json.loads(line)

    def test_double_click_and_two_independent_clicks_produce_different_records(self):
        with tempfile.TemporaryDirectory() as tmp:
            sink = ListTraceSink()
            seam, _, _, clock = _build_seam(
                trace_sink=sink,
                double_click_params=DoubleClickParams(time_ms=500, distance_px=4),
            )
            seam.run(
                SeamRequest(target_description="开始", action_kind=ActionKind.DOUBLE_CLICK)
            )
            seam.run(SeamRequest(target_description="开始", action_kind=ActionKind.CLICK))
            clock.sleep(0.6)
            seam.run(SeamRequest(target_description="开始", action_kind=ActionKind.CLICK))
            log = ActionLog(tmp)
            for trace in sink.traces:
                log.append(build_record(trace))
            actions = [record["action"] for record in log.records()]
            self.assertEqual(actions, ["double_click", "click", "click"])
            self.assertTrue(log.records()[0]["result"]["ok"])

    def test_the_record_carries_the_cursor_readback_and_its_delta(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, record = self._run_once(tmp)
            payload = record.to_dict()
            self.assertEqual(payload["result"]["cursor_readback"], [1049, 1564])
            self.assertEqual(payload["result"]["cursor_delta_px"], 0)

    def test_a_rejection_records_the_candidate_set(self):
        candidates = (_candidate(rect=(10, 20, 60, 70)), _candidate(rect=(100, 200, 150, 250)))
        sink = ListTraceSink()
        seam, _, _, _ = _build_seam(candidates=candidates, trace_sink=sink)
        seam.run(SeamRequest(target_description="开始"))
        record = build_record(sink.traces[-1])
        payload = record.to_dict()
        self.assertEqual(len(payload["rejected_candidates"]), 2)
        self.assertEqual(payload["rejected_candidates"][0]["name"], "开始")
        self.assertIsNone(payload["point"])

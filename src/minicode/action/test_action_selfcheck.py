"""启动自检（C-01 / C-02）与最小闭环运行器的单测。"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from minicode.action.fakes import (
    FakeCaptureSource,
    FakeControlTree,
    FakeInputInjector,
    FakeMetrics,
    FakeWindowController,
)
from minicode.action.geometry import VirtualDesktop
from minicode.action.ports import ControlCandidate
from minicode.action.records import ActionLog
from minicode.action.runner import run_minimal_loop
from minicode.action.selfcheck import InjectionSelfCheck, StartupSelfCheck

_CANDIDATE = ControlCandidate(
    name="开始", control_type="Button", rect=(1016, 1528, 1083, 1600), window_handle=65784
)


class StartupSelfCheckTest(unittest.TestCase):
    """C-01 启动自检：三项全成立才允许进入动作层。"""

    def test_all_conditions_holding_passes(self):
        result = StartupSelfCheck(
            FakeMetrics(), FakeCaptureSource(width=2560, height=1600)
        ).run()
        self.assertTrue(result.ok)
        self.assertEqual(result.failures, ())

    def test_dpi_awareness_must_be_per_monitor_v2(self):
        metrics = FakeMetrics(awareness="SYSTEM_AWARE")
        result = StartupSelfCheck(metrics, FakeCaptureSource(width=2560, height=1600)).run()
        self.assertFalse(result.ok)
        self.assertIn("dpi-awareness", result.failures)

    def test_physical_metrics_must_be_2560x1600(self):
        metrics = FakeMetrics(physical=(1707, 1067))
        result = StartupSelfCheck(metrics, FakeCaptureSource(width=2560, height=1600)).run()
        self.assertFalse(result.ok)
        self.assertIn("physical-metrics", result.failures)

    def test_device_caps_dpi_must_be_144(self):
        metrics = FakeMetrics(device_caps_dpi=96)
        result = StartupSelfCheck(metrics, FakeCaptureSource(width=2560, height=1600)).run()
        self.assertFalse(result.ok)
        self.assertIn("device-caps-dpi", result.failures)

    def test_capture_size_must_match_the_physical_size(self):
        metrics = FakeMetrics()
        result = StartupSelfCheck(metrics, FakeCaptureSource(width=1707, height=1067)).run()
        self.assertFalse(result.ok)
        self.assertIn("capture-size", result.failures)

    def test_zero_by_zero_capture_fails_the_check(self):
        metrics = FakeMetrics()
        result = StartupSelfCheck(metrics, FakeCaptureSource(width=0, height=0)).run()
        self.assertFalse(result.ok)
        self.assertIn("capture-size", result.failures)


class InjectionSelfCheckTest(unittest.TestCase):
    """C-02 注入自检：结构体大小与返回值。"""

    def test_struct_size_40_and_non_zero_probe_pass(self):
        result = InjectionSelfCheck(FakeInputInjector(struct_size=40, probe_return=1)).run()
        self.assertTrue(result.ok)
        self.assertEqual(result.failures, ())

    def test_wrong_struct_size_fails(self):
        result = InjectionSelfCheck(FakeInputInjector(struct_size=64, probe_return=1)).run()
        self.assertFalse(result.ok)
        self.assertIn("input-struct-size", result.failures)

    def test_probe_returning_zero_fails(self):
        result = InjectionSelfCheck(FakeInputInjector(struct_size=40, probe_return=0)).run()
        self.assertFalse(result.ok)
        self.assertIn("probe-return-value", result.failures)

    def test_probe_returning_more_than_one_event_fails(self):
        result = InjectionSelfCheck(FakeInputInjector(struct_size=40, probe_return=2)).run()
        self.assertFalse(result.ok)
        self.assertIn("probe-return-value", result.failures)


class MinimalLoopTest(unittest.TestCase):
    """一条命令跑完最小闭环。"""

    def _run(self, tmp, **overrides):
        params = dict(
            target_description="开始",
            capture_sources=(FakeCaptureSource(width=2560, height=1600),),
            tree=FakeControlTree((_CANDIDATE,)),
            injector=FakeInputInjector(),
            window_controller=FakeWindowController(foreground=65784),
            virtual_desktop=VirtualDesktop(0, 0, 2560, 1600),
            metrics=FakeMetrics(),
            log_directory=tmp,
        )
        params.update(overrides)
        return run_minimal_loop(**params)

    def test_a_successful_loop_writes_exactly_one_action_record(self):
        with tempfile.TemporaryDirectory() as tmp:
            outcome = self._run(tmp)
            self.assertTrue(outcome.ok)
            records = ActionLog(tmp).records()
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0]["action"], "click")
            self.assertEqual(records[0]["point"], {"x": 1049, "y": 1564, "space": "physical-surface"})

    def test_failed_startup_check_produces_no_action_record(self):
        with tempfile.TemporaryDirectory() as tmp:
            outcome = self._run(tmp, metrics=FakeMetrics(physical=(1707, 1067)))
            self.assertFalse(outcome.ok)
            self.assertIsNone(outcome.record)
            self.assertEqual(ActionLog(tmp).records(), [])

    def test_failed_startup_check_does_not_inject_anything(self):
        with tempfile.TemporaryDirectory() as tmp:
            injector = FakeInputInjector()
            outcome = self._run(tmp, injector=injector, metrics=FakeMetrics(physical=(1707, 1067)))
            self.assertFalse(outcome.ok)
            self.assertEqual(injector.events, [])

    def test_failed_injection_self_check_produces_no_action_record(self):
        with tempfile.TemporaryDirectory() as tmp:
            injector = FakeInputInjector(struct_size=64)
            outcome = self._run(tmp, injector=injector)
            self.assertFalse(outcome.ok)
            self.assertIsNone(outcome.record)
            self.assertEqual(ActionLog(tmp).records(), [])

    def test_the_decision_segment_is_zero_in_the_record(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._run(tmp)
            record = ActionLog(tmp).records()[0]
            self.assertEqual(record["timings_ms"]["decision"], 0.0)

    def test_no_image_file_lands_in_the_log_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._run(tmp)
            for path in Path(tmp).iterdir():
                self.assertFalse(path.name.lower().endswith((".png", ".jpg", ".bmp", ".gif")))


class StdlibOnlyTest(unittest.TestCase):
    """验收项 1：只装标准库即可跑。"""

    def test_the_whole_loop_runs_with_third_party_imports_blocked(self):
        src = str(Path(__file__).resolve().parents[2])
        env = dict(os.environ)
        env["PYTHONPATH"] = src
        code = "\n".join(
            [
                "import sys",
                "class Blocker:",
                "    def find_spec(self, name, path=None, target=None):",
                "        if name.split('.')[0] in ('numpy', 'mss', 'PIL'):",
                "            raise ImportError('third-party blocked: ' + name)",
                "        return None",
                "sys.meta_path.insert(0, Blocker())",
                "import tempfile",
                "from minicode.action.fakes import (FakeCaptureSource, FakeControlTree,",
                "    FakeInputInjector, FakeMetrics, FakeWindowController)",
                "from minicode.action.geometry import VirtualDesktop",
                "from minicode.action.ports import ControlCandidate",
                "from minicode.action.runner import run_minimal_loop",
                "candidate = ControlCandidate(name='开始', control_type='Button',",
                "    rect=(1016, 1528, 1083, 1600), window_handle=1)",
                "with tempfile.TemporaryDirectory() as tmp:",
                "    out = run_minimal_loop(target_description='开始',",
                "        capture_sources=(FakeCaptureSource(width=2560, height=1600),),",
                "        tree=FakeControlTree((candidate,)), injector=FakeInputInjector(),",
                "        window_controller=FakeWindowController(foreground=1),",
                "        virtual_desktop=VirtualDesktop(0, 0, 2560, 1600),",
                "        metrics=FakeMetrics(), log_directory=tmp)",
                "    print('LOOP_OK' if out.ok else 'LOOP_FAIL')",
            ]
        )
        result = subprocess.run(
            [sys.executable, "-c", code], capture_output=True, text=True, env=env
        )
        self.assertIn("LOOP_OK", result.stdout, msg=result.stderr)


class CaptureBackendRuleTest(unittest.TestCase):

    def test_no_printwindow_call_exists_in_the_capture_path(self):
        package = Path(__file__).resolve().parent
        frozen = package.parent / "exec"
        needle = "Print" + "Window("
        for folder in (package, frozen):
            for path in folder.glob("*.py"):
                if path.name.startswith("test_"):
                    continue
                text = path.read_text(encoding="utf-8")
                self.assertNotIn(needle, text, msg=f"{path.name} 里出现了 PrintWindow 调用")

"""能力表的单测（工单 M3 AC 9、AC 10、AC 11、AC 12 的一部分）。

三态：**完全可用 / 部分覆盖 / 不可用**；键是「**应用 + 版本**」；
同一次启动内只探测一次；「不可用」必须能区分「**解开失败**」与「**从未可用**」。
"""

from __future__ import annotations

import unittest

from minicode.action.ports import WindowAnchor
from minicode.uia.capability import Availability, CapabilityTable
from minicode.uia.fakes import FakeCapabilityProbe, FakeUnblocker
from minicode.uia.ports import TreeAssessment, UnblockOutcome


class _Clock:
    """记录退避时长的假 sleeper（不真的等待）。"""

    def __init__(self):
        self.sleeps: list[float] = []

    def __call__(self, seconds: float) -> None:
        self.sleeps.append(float(seconds))


def _anchor(app: str = "explorer", version: str = "10.0.26100") -> WindowAnchor:
    return WindowAnchor(hwnd=1234, app=app, version=version)


_USABLE = TreeAssessment(
    has_tree=True,
    class_names=("Button", "Shell_TrayWnd"),
    interactive_class_names=("Button",),
    usable_controls=12,
    has_automation_id=True,
    name_collisions=0,
    child_windows=3,
)

_SPARSE = TreeAssessment(
    has_tree=True,
    class_names=("Chrome_WidgetWin_1",),
    interactive_class_names=(),
    usable_controls=0,
    has_automation_id=False,
    name_collisions=4,
    child_windows=1,
)


class ThreeStatesTest(unittest.TestCase):
    """AC 11：三态取值。"""

    def test_a_healthy_tree_is_usable(self):
        table = CapabilityTable(FakeCapabilityProbe((_USABLE,)))
        entry = table.availability(_anchor())
        self.assertEqual(entry.availability, Availability.USABLE)

    def test_a_flat_tree_is_sparse(self):
        table = CapabilityTable(FakeCapabilityProbe((_SPARSE,)))
        entry = table.availability(_anchor())
        self.assertEqual(entry.availability, Availability.SPARSE)

    def test_an_empty_tree_is_unavailable(self):
        table = CapabilityTable(FakeCapabilityProbe((TreeAssessment(has_tree=False),)))
        entry = table.availability(_anchor())
        self.assertEqual(entry.availability, Availability.UNAVAILABLE)

    def test_only_three_states_exist(self):
        self.assertEqual(
            sorted(state.value for state in Availability),
            ["sparse", "unavailable", "usable"],
        )


class KeyAndCacheTest(unittest.TestCase):
    """AC 11：键是「应用 + 版本」；同一次启动内只探测一次。"""

    def test_the_same_app_is_probed_only_once(self):
        probe = FakeCapabilityProbe((_USABLE,))
        table = CapabilityTable(probe)
        table.availability(_anchor())
        table.availability(_anchor())
        self.assertEqual(table.probe_count, 1)

    def test_the_probe_count_stays_at_one_after_a_second_action(self):
        probe = FakeCapabilityProbe((_USABLE,))
        table = CapabilityTable(probe)
        table.availability(_anchor())
        table.availability(_anchor())
        self.assertEqual(table.probe_count, 1)

    def test_a_different_version_is_a_different_entry(self):
        probe = FakeCapabilityProbe((_USABLE,))
        table = CapabilityTable(probe)
        table.availability(_anchor(version="10.0.1"))
        table.availability(_anchor(version="10.0.2"))
        self.assertEqual(table.probe_count, 2)

    def test_the_entry_carries_the_app_and_the_version(self):
        table = CapabilityTable(FakeCapabilityProbe((_USABLE,)))
        entry = table.availability(_anchor(app="chrome", version="120.0"))
        self.assertEqual(entry.app, "chrome")
        self.assertEqual(entry.version, "120.0")

    def test_the_table_lists_its_entries(self):
        table = CapabilityTable(FakeCapabilityProbe((_USABLE,)))
        table.availability(_anchor(app="chrome", version="120.0"))
        table.availability(_anchor(app="edge", version="120.0"))
        self.assertEqual(len(table.entries()), 2)


class DetectionPostureTest(unittest.TestCase):
    """AC 12：判定姿势是递归子窗口 + 看 ClassName，**不数候选数**。"""

    def test_a_bare_shell_is_not_usable(self):
        # 裸外壳：**3 个边框按钮**（最小化/最大化/关闭）。
        # 它们确实是 Button、类名也是 Button，但过不了可用性判据
        # （无名字/类名不成立/控件框退化）→ usable_controls = 0 → 不是可用。
        bare_shell = TreeAssessment(
            has_tree=True,
            class_names=("Button", "Button", "Button"),
            interactive_class_names=("Button",),
            usable_controls=0,
            has_automation_id=False,
            name_collisions=0,
        )
        table = CapabilityTable(FakeCapabilityProbe((bare_shell,)))
        entry = table.availability(_anchor())
        self.assertNotEqual(entry.availability, Availability.USABLE)

    def test_a_bare_shell_is_reported_as_sparse(self):
        bare_shell = TreeAssessment(
            has_tree=True,
            class_names=("Button",),
            interactive_class_names=("Button",),
            usable_controls=0,
        )
        table = CapabilityTable(FakeCapabilityProbe((bare_shell,)))
        entry = table.availability(_anchor())
        self.assertEqual(entry.availability, Availability.SPARSE)
        self.assertIn("裸外壳", entry.reason)

    def test_a_sparse_tree_says_what_is_missing(self):
        table = CapabilityTable(FakeCapabilityProbe((_SPARSE,)))
        entry = table.availability(_anchor())
        self.assertIn("AutomationId", entry.reason)


class BackoffRetryTest(unittest.TestCase):
    """AC 9：树未就绪时先激活窗口并做指数退避重试。"""

    def test_a_tree_that_appears_later_is_not_called_unsupported(self):
        probe = FakeCapabilityProbe((TreeAssessment(has_tree=False), _USABLE))
        table = CapabilityTable(probe, max_retries=3, backoff_s=0.1, sleeper=_Clock())
        entry = table.availability(_anchor())
        self.assertEqual(entry.availability, Availability.USABLE)

    def test_the_backoff_grows_exponentially(self):
        clock = _Clock()
        probe = FakeCapabilityProbe((TreeAssessment(has_tree=False),))
        table = CapabilityTable(probe, max_retries=3, backoff_s=0.1, sleeper=clock)
        table.availability(_anchor())
        self.assertEqual(clock.sleeps, [0.1, 0.2, 0.4])

    def test_the_window_is_activated_before_retrying(self):
        unblocker = FakeUnblocker((UnblockOutcome.UNBLOCKED,))
        probe = FakeCapabilityProbe((TreeAssessment(has_tree=False), _USABLE))
        table = CapabilityTable(
            probe, unblocker=unblocker, max_retries=2, backoff_s=0.1, sleeper=_Clock()
        )
        table.availability(_anchor())
        self.assertGreaterEqual(unblocker.calls, 1)


class GatedTreeTest(unittest.TestCase):
    """AC 10：树被门控时先试着解开；「不可用」要能区分两种原因。"""

    def test_a_gated_tree_that_cannot_be_unlocked_says_so(self):
        unblocker = FakeUnblocker((UnblockOutcome.FAILED,))
        probe = FakeCapabilityProbe((TreeAssessment(has_tree=False),))
        table = CapabilityTable(
            probe, unblocker=unblocker, max_retries=2, backoff_s=0.1, sleeper=_Clock()
        )
        entry = table.availability(_anchor())
        self.assertEqual(entry.availability, Availability.UNAVAILABLE)
        self.assertEqual(entry.reason, "gated-unlock-failed")

    def test_a_tree_that_was_never_available_says_so(self):
        probe = FakeCapabilityProbe((TreeAssessment(has_tree=False),))
        table = CapabilityTable(probe, max_retries=2, backoff_s=0.1, sleeper=_Clock())
        entry = table.availability(_anchor())
        self.assertEqual(entry.availability, Availability.UNAVAILABLE)
        self.assertEqual(entry.reason, "no-tree")

    def test_the_two_reasons_are_different(self):
        blocked = CapabilityTable(
            FakeCapabilityProbe((TreeAssessment(has_tree=False),)),
            unblocker=FakeUnblocker((UnblockOutcome.FAILED,)),
            max_retries=1,
            backoff_s=0.0,
            sleeper=_Clock(),
        ).availability(_anchor())
        never = CapabilityTable(
            FakeCapabilityProbe((TreeAssessment(has_tree=False),)),
            max_retries=1,
            backoff_s=0.0,
            sleeper=_Clock(),
        ).availability(_anchor())
        self.assertNotEqual(blocked.reason, never.reason)


if __name__ == "__main__":
    unittest.main()

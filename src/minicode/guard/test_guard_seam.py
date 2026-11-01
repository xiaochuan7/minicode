"""护栏缝（GuardedSeam）与授权级审批门（LevelApprovalGate）的测试（W2）。

全部跑在**假桌面**上（假截图源 / 假控件树 / 假注入器，与
``serve.fakes.fake_wiring`` 同一套装配），断言全部落在**外部可见**的证据上：
响应错误码、注入器 journal（「零动作下发」的唯一可信证据源）、
trace.jsonl 行、审批决定列表。

前置拦截每条护栏都有「拦下」与「放行」两面。
"""

import tempfile
import unittest
from pathlib import Path

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
from minicode.action.protocol import ActionKind
from minicode.action.records import ListTraceSink
from minicode.action.seam import ActionExecutionSeam, SeamRequest
from minicode.approval.fakes import AutoApprover
from minicode.approval.gate import ApprovalGate
from minicode.guard.estop import EstopWatchdog
from minicode.guard.fakes import fake_key_state_fn, fake_tick_pair, fake_window_info_fn
from minicode.guard.gate import LevelApprovalGate
from minicode.guard.idle import IdleWatcher
from minicode.guard.injector import GuardedInjector
from minicode.guard.levels import AuthLevel
from minicode.guard.policy import GuardBudget, GuardPolicy
from minicode.guard.recorder import SessionRecorder
from minicode.guard.runtime import BudgetLedger, FailureFuse
from minicode.guard.seam import GuardSession, make_guarded_factory
from minicode.guard.win32 import ForegroundWindowInfo
from minicode.serve.fakes import FAKE_LANDING, FAKE_TARGET, fake_candidate

_FAKE_WINDOW = ForegroundWindowInfo(
    hwnd=65784, class_name="Shell_TrayWnd", title="任务栏"
)


class _Sink:
    """收集追踪记录（动作日志出口的替身）。"""

    def __init__(self) -> None:
        self.traces: list = []

    def emit(self, trace) -> None:
        self.traces.append(trace)


def _click_request(target: str = FAKE_TARGET) -> SeamRequest:
    return SeamRequest(target_description=target, action_kind=ActionKind.CLICK)


def _wiring(*targets: ControlCandidate, injector=None):
    """假桌面缝工厂（可指定候选集；缺省 = 假桌面的「开始」按钮）。"""
    clock = VirtualClock()
    real_injector = injector if injector is not None else FakeInputInjector()
    controller = FakeWindowController(foreground=65784)
    tree = FakeControlTree(targets or (fake_candidate(),))

    def factory(sink: ListTraceSink) -> ActionExecutionSeam:
        return ActionExecutionSeam(
            capture_sources=(FakeCaptureSource(width=2560, height=1600),),
            tree=tree,
            injector=real_injector,
            window_controller=controller,
            virtual_desktop=VirtualDesktop(0, 0, 2560, 1600),
            sleeper=clock.sleep,
            clock=clock.time,
            trace_sink=sink,
        )

    return factory, real_injector


def _session(
    *,
    policy: GuardPolicy | None = None,
    window_info_fn=None,
    estop: EstopWatchdog | None = None,
    idle_watcher: IdleWatcher | None = None,
    session_dir: Path | None = None,
    dry_run: bool = False,
) -> GuardSession:
    resolved_policy = policy or GuardPolicy(level=AuthLevel.L2)
    if window_info_fn is None:
        window_info_fn = fake_window_info_fn(_FAKE_WINDOW)
    return GuardSession(
        policy=resolved_policy,
        # 账本与策略用**同一份**预算（两份各存一份就会各说各话）。
        ledger=BudgetLedger(budget=resolved_policy.budget, clock=lambda: 1000.0),
        fuse=FailureFuse(threshold=resolved_policy.fuse_threshold),
        estop=estop,
        idle_watcher=idle_watcher,
        recorder=SessionRecorder(session_dir) if session_dir is not None else None,
        window_info_fn=window_info_fn,
        dry_run=dry_run,
    )


def _guarded(
    session: GuardSession,
    *,
    targets: tuple[ControlCandidate, ...] | None = None,
    injector=None,
    approver=None,
):
    """装配一条护栏缝，返回（缝，真注入器，授权级门）。"""
    wiring, fake_injector = _wiring(*(targets or ()), injector=injector)
    level_gate = LevelApprovalGate(
        ApprovalGate(approver=approver), level=session.policy.level
    )
    seam = make_guarded_factory(wiring, session=session, level_gate=level_gate)(
        _Sink()
    )
    return seam, fake_injector, level_gate


class GuardedSeamPassthroughTest(unittest.TestCase):
    """没有护栏命中时，护栏缝与内层缝行为一致。"""

    def test_an_ordinary_click_runs_end_to_end_on_the_fake_desktop(self):
        session = _session()
        seam, injector, _ = _guarded(session)
        result = seam.run(_click_request())
        self.assertTrue(result.ok, f"假桌面上的普通点击应当成功：{result.result}")
        self.assertEqual(result.final_point, FAKE_LANDING)
        # 记账：一步、点击成功进熔断账本。
        self.assertEqual(session.ledger.steps, 1)
        self.assertIsNone(session.fuse.check())


class GuardedSeamDenialTest(unittest.TestCase):
    """前置拦截：每条护栏都有「拦下 → 零动作下发 + 错误码」的一面。"""

    def test_estop_blocks_everything_with_zero_injection(self):
        watchdog = EstopWatchdog(key_state_fn=fake_key_state_fn(set()), vkeys=(0x11,))
        watchdog.trigger_for_test()
        session = _session(estop=watchdog)
        seam, injector, _ = _guarded(session)
        result = seam.run(_click_request())
        self.assertFalse(result.ok)
        self.assertEqual(result.result.error_code, ErrorCode.EMERGENCY_STOPPED)
        self.assertEqual(injector.events, [])
        self.assertEqual(len(session.denials), 1)
        self.assertEqual(session.denials[0]["guard"], "estop")

    def test_step_budget_exhaustion_blocks_the_next_step(self):
        session = _session(
            policy=GuardPolicy(level=AuthLevel.L2, budget=GuardBudget(max_steps=1))
        )
        seam, injector, _ = _guarded(session)
        first = seam.run(_click_request())
        self.assertTrue(first.ok)
        second = seam.run(_click_request())
        self.assertFalse(second.ok)
        self.assertEqual(second.result.error_code, ErrorCode.BUDGET_EXCEEDED)
        self.assertEqual(injector.events.count(("button_up", "left")), 1)

    def test_fuse_blows_after_two_consecutive_failures_on_the_same_target(self):
        session = _session()
        failing = FakeInputInjector(fail_injection=True)
        seam, _, _ = _guarded(session, injector=failing)
        for _ in range(2):
            result = seam.run(_click_request())
            self.assertFalse(result.ok)
        third = seam.run(_click_request())
        self.assertEqual(third.result.error_code, ErrorCode.FUSE_BLOWN)

    def test_recent_user_input_pauses_with_zero_injection(self):
        # last_input=900, now=1000：用户 100ms 前动过键鼠。
        tick_fn, now_fn = fake_tick_pair(last_input=900, now=1000)
        session = _session(
            idle_watcher=IdleWatcher(tick_fn=tick_fn, now_tick_fn=now_fn)
        )
        seam, injector, _ = _guarded(session)
        result = seam.run(_click_request())
        self.assertEqual(result.result.error_code, ErrorCode.USER_ACTIVE_PAUSED)
        self.assertEqual(injector.events, [])

    def test_a_long_idle_user_does_not_pause(self):
        tick_fn, now_fn = fake_tick_pair(last_input=0, now=100000)
        session = _session(
            idle_watcher=IdleWatcher(tick_fn=tick_fn, now_tick_fn=now_fn)
        )
        seam, injector, _ = _guarded(session)
        result = seam.run(_click_request())
        self.assertTrue(result.ok)
        self.assertNotEqual(injector.events, [])

    def test_blacklisted_foreground_window_is_refused_with_a_reason(self):
        session = _session(
            window_info_fn=fake_window_info_fn(
                ForegroundWindowInfo(
                    hwnd=1, class_name="TaskmgrWindow", title="任务管理器"
                )
            )
        )
        seam, injector, _ = _guarded(session)
        result = seam.run(_click_request())
        self.assertEqual(result.result.error_code, ErrorCode.WINDOW_BLACKLISTED)
        self.assertIn("任务管理器", result.result.error_message)
        self.assertEqual(injector.events, [])

    def test_l1_whitelist_refuses_a_window_outside_the_allow_set(self):
        session = _session(
            policy=GuardPolicy(level=AuthLevel.L1, window_whitelist=("记事本",))
        )
        seam, injector, _ = _guarded(session)
        result = seam.run(_click_request())
        self.assertEqual(
            result.result.error_code, ErrorCode.WINDOW_WHITELIST_MISMATCH
        )
        self.assertEqual(injector.events, [])

    def test_l1_whitelist_allows_a_window_inside_the_allow_set(self):
        session = _session(
            policy=GuardPolicy(level=AuthLevel.L1, window_whitelist=("任务栏",))
        )
        seam, injector, _ = _guarded(session)
        result = seam.run(_click_request())
        self.assertTrue(result.ok)
        self.assertNotEqual(injector.events, [])

    def test_l1_without_a_readable_window_fails_closed(self):
        session = _session(
            policy=GuardPolicy(level=AuthLevel.L1, window_whitelist=("任务栏",)),
            window_info_fn=fake_window_info_fn(None),
        )
        seam, injector, _ = _guarded(session)
        result = seam.run(_click_request())
        self.assertEqual(
            result.result.error_code, ErrorCode.WINDOW_WHITELIST_MISMATCH
        )
        self.assertEqual(injector.events, [])


class IrreversibleByLevelTest(unittest.TestCase):
    """护栏 3 走审批门：L1 拒、L2 交人工（fail-closed）、L3 放行记录。

    候选集里放一个名为「保存」的按钮（影响面 = 写文件），
    让不可逆动作能真的走到审批门。
    """

    _SAVE_BUTTON = ControlCandidate(
        name="保存",
        control_type="Button",
        rect=(800, 1200, 900, 1260),
        window_handle=65784,
        window_title="任务栏",
    )

    def _run(self, level: AuthLevel, target: str = "保存", save_tree: bool = True, **kwargs):
        session = _session(policy=GuardPolicy(level=level), **kwargs)
        targets = (self._SAVE_BUTTON,) if save_tree else None
        seam, injector, level_gate = _guarded(session, targets=targets)
        result = seam.run(_click_request(target))
        return result, injector, level_gate

    def test_l1_refuses_an_irreversible_action_without_consulting_anyone(self):
        result, injector, level_gate = self._run(AuthLevel.L1)
        self.assertFalse(result.ok)
        self.assertEqual(result.result.error_code, ErrorCode.APPROVAL_DENIED)
        self.assertEqual(injector.events, [])
        self.assertEqual(level_gate.decisions[0]["verdict"], "deny")

    def test_l2_fails_closed_when_no_human_approver_is_wired(self):
        # 假桌面装配里没有审批人：L2 的不可逆动作到了人类闸门，没人点头
        # → approval_denied（这正是「必须人工确认」的 fail-closed 形态）。
        result, injector, level_gate = self._run(AuthLevel.L2)
        self.assertFalse(result.ok)
        self.assertEqual(result.result.error_code, ErrorCode.APPROVAL_DENIED)
        self.assertEqual(injector.events, [])
        self.assertEqual(level_gate.decisions[0]["verdict"], "approval_required")

    def test_l2_lets_irreversible_actions_through_when_a_human_approves(self):
        session = _session(policy=GuardPolicy(level=AuthLevel.L2))
        wiring, fake_injector = _wiring(self._SAVE_BUTTON)
        # 审批人给**级联门**（与真机接线一致：ConsoleApprover 交给级联门，
        # 不是塞进内层闸门——级联门的强制审批走自己的请求通道）。
        level_gate = LevelApprovalGate(
            ApprovalGate(), level=AuthLevel.L2, approver=AutoApprover()
        )
        seam = make_guarded_factory(wiring, session=session, level_gate=level_gate)(
            _Sink()
        )
        result = seam.run(_click_request("保存"))
        self.assertTrue(result.ok, f"人批了就放行：{result.result}")
        self.assertNotEqual(fake_injector.events, [])

    def test_l3_records_but_allows_irreversible_actions(self):
        result, injector, level_gate = self._run(AuthLevel.L3)
        self.assertTrue(result.ok, f"L3 无人值守放行：{result.result}")
        self.assertNotEqual(injector.events, [])
        self.assertEqual(level_gate.decisions[0]["verdict"], "allow")
        self.assertEqual(level_gate.decisions[0]["impact"], "write_file")

    def test_l2_ordinary_actions_do_not_need_approval(self):
        # 默认假树（「开始」按钮 = 可逆动作）：不惊动审批门。
        result, injector, level_gate = self._run(
            AuthLevel.L2, target=FAKE_TARGET, save_tree=False
        )
        self.assertTrue(result.ok)
        self.assertNotEqual(injector.events, [])
        self.assertEqual(level_gate.decisions, [])


class RecordingTest(unittest.TestCase):
    """护栏 7：全程录制（JSONL）与护栏 9 的素材。"""

    def test_every_step_lands_in_trace_jsonl_with_the_required_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = _session(session_dir=Path(tmp) / "run1")
            seam, _, _ = _guarded(session)
            seam.run(_click_request())
            rows = session.recorder.read_rows()
            self.assertEqual(len(rows), 1)
            row = rows[0]
            self.assertEqual(row["type"], "step")
            self.assertEqual(row["action"]["kind"], "click")
            self.assertEqual(row["target"], FAKE_TARGET)
            self.assertEqual(row["model_calls"], 0)
            self.assertEqual(row["landing_point"], list(FAKE_LANDING))
            self.assertIsInstance(row["screenshot_sha256"], str)
            self.assertTrue(row["result"]["ok"])
            self.assertIsInstance(row["tree"], list)

    def test_denials_land_in_the_same_jsonl_as_guard_events(self):
        watchdog = EstopWatchdog(key_state_fn=fake_key_state_fn(set()), vkeys=(0x11,))
        watchdog.trigger_for_test()
        with tempfile.TemporaryDirectory() as tmp:
            session = _session(estop=watchdog, session_dir=Path(tmp) / "run2")
            seam, _, _ = _guarded(session)
            seam.run(_click_request())
            rows = session.recorder.read_rows()
            # 急停先记一笔事实（estop），再记拒绝（denial）——同一份 JSONL。
            self.assertEqual([row["type"] for row in rows], ["estop", "denial"])
            self.assertEqual(rows[1]["ts_code"], "emergency_stopped")


class DryRunInjectionTest(unittest.TestCase):
    """护栏 10 + L0：管线照跑（定位/落点都在），但注入器零动作。"""

    def test_dry_run_locates_and_lands_without_injecting(self):
        with tempfile.TemporaryDirectory() as tmp:
            printed: list[str] = []
            dry = FakeInputInjector()
            session = _session(session_dir=Path(tmp) / "run3", dry_run=True)
            guarded_injector = GuardedInjector(dry, dry_run=True, printer=printed.append)
            wiring, _ = _wiring(injector=guarded_injector)
            seam = make_guarded_factory(wiring, session=session)(_Sink())
            result = seam.run(_click_request())
            # 落点照算、注入为零；校验层**诚实地**报「光标没动」——
            # 这正是 dry-run 的预期形态（不 ok，但落点与定位结果都在）。
            self.assertEqual(result.final_point, FAKE_LANDING, "落点照算")
            self.assertEqual(result.result.error_code, ErrorCode.CURSOR_MISMATCH)
            self.assertEqual(dry.events, [], "零注入")
            self.assertEqual(
                len(guarded_injector.journal),
                3,
                "move + button_down + button_up 有记录（dry-run 的记录在护栏注入器上）",
            )
            self.assertTrue(any("dry-run" in line for line in printed))
            # dry-run 的「校验失败」不进熔断（否则只读模式两步就熔断自己）。
            self.assertIsNone(session.fuse.check())
            self.assertEqual(session.ledger.steps, 1, "预算步数照记")


class LevelGateUnitBasicsTest(unittest.TestCase):
    """LevelApprovalGate 的直接单测（不经缝）：判定记录可审计。"""

    def test_the_gate_records_every_irreversible_decision(self):
        gate = LevelApprovalGate(ApprovalGate(), level=AuthLevel.L1)
        outcome = gate.check(
            intent="click", target_description="保存按钮", action_kind="click"
        )
        self.assertFalse(outcome.approved)
        self.assertEqual(len(gate.decisions), 1)
        self.assertEqual(gate.decisions[0]["impact"], "write_file")
        self.assertEqual(gate.decisions[0]["verdict"], "deny")

    def test_an_ordinary_action_is_delegated_without_guard_decisions(self):
        gate = LevelApprovalGate(ApprovalGate(), level=AuthLevel.L2)
        outcome = gate.check(
            intent="click", target_description=FAKE_TARGET, action_kind="click"
        )
        self.assertFalse(outcome.required)
        self.assertTrue(outcome.approved)
        self.assertEqual(gate.decisions, [])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

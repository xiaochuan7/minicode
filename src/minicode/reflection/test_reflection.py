"""M12（反思与停止条件）的单测 —— **只测外部行为**。

工单 M12 的九条验收标准逐条对应下面的类：

* :class:`CircuitBreakTest` —— AC1 重复动作熔断（换策略 + 读得到记录）
* :class:`RoundCapTest` —— AC2 轮次硬上限（模型自称完成不算数）
* :class:`RetryReversibilityTest` —— AC3 重试只针对可逆动作
* :class:`ReperceiveBeforeRetryTest` —— AC4 重试前重新感知
* :class:`StallDetectorTest` —— AC5 四类卡死检测器
* :class:`ElevationHandoffTest` —— AC6 提权场景零动作交人
* :class:`HandoffArtifactTest` —— AC7 交接产物可读
* :class:`DangerGateTest` —— AC8 置信度不作放行条件
* :class:`ScopeBoundaryTest` —— AC9 边界写清（不假装已做审批）

全部用**假截图源 / 假定位通道 / 假注入 / 虚拟时钟 / 假模型**：
毫秒级跑完，不碰真桌面、不花钱。
"""

from __future__ import annotations

import ast
import inspect
import unittest
from pathlib import Path

from minicode.action.errors import ErrorCode
from minicode.action.fakes import (
    FakeCaptureSource,
    FakeInputInjector,
    FakeSecureDesktopDetector,
    VirtualClock,
)
from minicode.action.geometry import VirtualDesktop
from minicode.action.ports import (
    CapturedFrame,
    ControlCandidate,
    InjectionBlocked,
    LocateRequest,
    LocateResult,
)
from minicode.action.protocol import Action, ActionKind, ActionResult
from minicode.action.records import (
    ActionTrace,
    ListTraceSink,
    MultiTraceSink,
    build_record,
)
from minicode.action.seam import ActionExecutionSeam, SeamRequest
from minicode.approval.fakes import AutoApprover, DenyApprover
from minicode.approval.gate import ApprovalGate
from minicode.decision.api import ProtocolApi
from minicode.decision.harness import DecisionHarness
from minicode.model.client import ModelReply
from minicode.model.effort import Effort
from minicode.model.fakes import FakeModelClient
from minicode.reflection import gate as gate_module
from minicode.reflection.gate import ActionContext, DangerGate
from minicode.reflection.handoff import HANDOFF_MARKER, build_handoff
from minicode.reflection.retry import (
    RETRY_BACKOFF_MS,
    RetryingSeam,
    classify_reversibility,
)
from minicode.reflection.stop import (
    DETECTOR_FRAME_UNCHANGED,
    DETECTOR_PROGRESS_STALLED,
    DETECTOR_REPEATED_ACTION,
    DETECTOR_ROUND_CAP,
    STOP_ASKED_HUMAN,
    STOP_DANGEROUS_ACTION,
    STOP_ELEVATED,
    STOP_EXPECTATION_MET,
    STOP_REASONS,
    STOP_ROUND_CAP,
    STOP_STRATEGY_EXHAUSTED,
    StopPolicy,
    action_hash,
)
from minicode.verification.fakes import FakeStateObserver, ScriptedScreenshotPort, make_frame
from minicode.verification.layer import VerificationLayer

_DESKTOP = VirtualDesktop(0, 0, 2560, 1600)
_REPO_ROOT = Path(__file__).resolve().parents[3]


# ---------------------------------------------------------------------------
# 假实现
# ---------------------------------------------------------------------------


class _FakeLocator:
    """假定位通道：按脚本给控件框（**每次调用前进一份**，最后一份重复用）。

    「最后一份重复用」正好造出「重新感知之后目标还在原地」的情形。
    """

    def __init__(self, rects=((100, 200, 140, 240),), name="开始", window_title="记事本"):
        self.rects = list(rects)
        self.name = name
        self.window_title = window_title
        self.calls = 0

    def locate(self, request: LocateRequest) -> LocateResult:
        index = min(self.calls, len(self.rects) - 1)
        self.calls += 1
        rect = self.rects[index]
        candidate = ControlCandidate(
            name=self.name,
            control_type="Button",
            rect=rect,
            window_handle=4242,
            window_title=self.window_title,
        )
        return LocateResult(
            selected=candidate,
            landing_point=((rect[0] + rect[2]) // 2, (rect[1] + rect[3]) // 2),
            candidates=(candidate,),
            channel="uia",
        )


class _FlakyInjector(FakeInputInjector):
    """前 N 次**移动**失败（模拟 UIPI 拦截），但**照记坐标**。

    坐标照记是刻意的：重试那条路径必须能断言「注入的坐标换过了」。
    """

    def __init__(self, fail_moves: int = 0, **kwargs):
        super().__init__(**kwargs)
        self.fail_moves = fail_moves
        self.moves = 0

    def move(self, x: int, y: int) -> None:
        self.moves += 1
        self.events.append(("move", int(x), int(y)))
        if self.moves <= self.fail_moves:
            raise InjectionBlocked("模拟 UIPI 拦截")
        self.cursor = (int(x), int(y))


def _trace(
    *,
    kind: str = "click",
    target: str = "开始",
    point: tuple[int, int] | None = (120, 220),
    frame_changed: bool | None = False,
    expectation_met: bool | None = None,
    error_code: str | None = None,
    window_title: str = "记事本",
    intent: str = "",
    ok: bool | None = None,
):
    """造一条追踪记录（**公开的观测形状**，不是内部实现）。"""
    verification = None
    if frame_changed is not None or expectation_met is not None:
        outcome = "changed"
        if expectation_met is True:
            outcome = "expectation_met"
        elif expectation_met is False:
            outcome = "expectation_not_met"
        elif frame_changed is False:
            outcome = "no_change"
        verification = {
            "outcome": outcome,
            "change": {
                "outcome": "changed" if frame_changed else "no_change",
            },
        }
    return ActionTrace(
        action=Action(
            kind=ActionKind(kind), params={}, intent=intent or f"点击「{target}」"
        ),
        intent=intent or f"点击「{target}」",
        target_description=target,
        selected=ControlCandidate(
            name=target, rect=(100, 200, 140, 240), window_title=window_title
        ),
        landing_point=point,
        verification_result=verification,
        result=ActionResult(
            ok=(error_code is None) if ok is None else ok,
            error_code=ErrorCode(error_code) if error_code else None,
        ),
    )


def _build_seam(
    *,
    locator=None,
    injector=None,
    detector=None,
    trace_sink=None,
    frame: CapturedFrame | None = None,
    verifier: bool = True,
    approval_gate=None,
):
    """搭一条缝（**全假**：不碰真桌面）。"""
    clock = VirtualClock()
    picture = frame if frame is not None else make_frame(64, 64)
    seam = ActionExecutionSeam(
        capture_sources=[FakeCaptureSource(64, 64)],
        tree=None,
        injector=injector or FakeInputInjector(),
        virtual_desktop=_DESKTOP,
        sleeper=clock.sleep,
        clock=clock.time,
        trace_sink=trace_sink,
        locator=locator if locator is not None else _FakeLocator(),
        secure_desktop=detector,
        approval_gate=approval_gate,
        verifier=(
            VerificationLayer(
                screenshot_port=ScriptedScreenshotPort([picture]),
                observer=FakeStateObserver(),
                clock=clock.time,
                sleeper=clock.sleep,
            )
            if verifier
            else None
        ),
    )
    return seam, clock


def _reply(text: str) -> ModelReply:
    """假模型回复。"""
    return ModelReply(
        text=text,
        model="fake",
        endpoint="local",
        effort=Effort.HIGH,
        http_status=200,
        latency_ms=1.0,
        input_tokens=10,
        output_tokens=5,
    )


# ---------------------------------------------------------------------------
# AC1 重复动作熔断
# ---------------------------------------------------------------------------


class CircuitBreakTest(unittest.TestCase):
    """AC1：同一动作哈希连续 ≥3 次且帧无变化 → 强制换策略，且读得到记录。"""

    def test_repeating_the_same_action_on_a_dead_target_switches_strategy(self):
        policy = StopPolicy(max_rounds=10)
        traces = ListTraceSink()
        seam, _ = _build_seam(trace_sink=MultiTraceSink(traces, policy))
        for _ in range(3):
            seam.run(SeamRequest(target_description="开始"))
        self.assertEqual(len(policy.strategy_change_records), 1)
        change = policy.strategy_change_records[0]
        self.assertEqual(change.strategy, "crop_size")
        self.assertIn("已换策略", change.describe())

    def test_the_strategy_change_is_readable_in_the_report_shape(self):
        policy = StopPolicy(max_rounds=10)
        seam, _ = _build_seam(trace_sink=policy)
        for _ in range(3):
            seam.run(SeamRequest(target_description="开始"))
        payload = policy.strategy_change_records[0].to_dict()
        self.assertIn("已换策略", payload["description"])
        self.assertEqual(payload["label"], "换裁剪尺寸")

    def test_repeated_action_detector_reports_the_count(self):
        policy = StopPolicy(max_rounds=10)
        seam, _ = _build_seam(trace_sink=policy)
        for _ in range(3):
            seam.run(SeamRequest(target_description="开始"))
        verdicts = {item.detector: item for item in policy.detectors}
        self.assertTrue(verdicts[DETECTOR_REPEATED_ACTION].triggered)
        self.assertEqual(verdicts[DETECTOR_REPEATED_ACTION].count, 3)

    def test_two_identical_actions_do_not_switch_strategy(self):
        policy = StopPolicy(max_rounds=10)
        seam, _ = _build_seam(trace_sink=policy)
        for _ in range(2):
            seam.run(SeamRequest(target_description="开始"))
        self.assertEqual(policy.strategy_change_records, ())

    def test_the_strategy_change_reaches_the_next_plan_prompt(self):
        """换策略**有人消费**：它进了下一轮的规划提示词（模型据此换招）。"""
        policy = StopPolicy(max_rounds=10)
        seam, _ = _build_seam(trace_sink=policy)
        api = ProtocolApi(seam=seam, locator=_FakeLocator())
        caller = FakeModelClient(
            _reply("```python\nclick('开始')\n```"),
            _reply("```python\nclick('开始')\n```"),
        )
        harness = DecisionHarness(
            caller=caller,
            api=api,
            max_rounds=2,
            keep_frames=2,
            stop_policy=policy,
        )
        # 第一轮点三次（触发熔断），第二轮的提示词里应当有「已换策略」。
        for _ in range(3):
            seam.run(SeamRequest(target_description="开始"))
        harness.run("点一下开始")
        second_prompt = caller.calls[1]["prompt"]
        self.assertIn("已换策略", second_prompt)


# ---------------------------------------------------------------------------
# AC2 轮次硬上限
# ---------------------------------------------------------------------------


class RoundCapTest(unittest.TestCase):
    """AC2：达到上限就停下来交人；模型自称完成**不构成**停止理由。"""

    def _run(self, replies, *, rounds: int = 2):
        policy = StopPolicy(max_rounds=rounds)
        traces = ListTraceSink()
        seam, _ = _build_seam(trace_sink=MultiTraceSink(traces, policy))
        api = ProtocolApi(seam=seam, locator=_FakeLocator())
        harness = DecisionHarness(
            caller=FakeModelClient(*replies),
            api=api,
            max_rounds=rounds,
            keep_frames=2,
            stop_policy=policy,
        )
        return harness.run("点一下开始")

    def test_task_runs_to_the_round_cap_and_stops_with_that_reason(self):
        report = self._run(
            [
                _reply("```python\nclick('开始')\n```"),
                _reply("```python\nclick('开始')\n```"),
            ],
            rounds=2,
        )
        self.assertEqual(report.stop_reason, STOP_ROUND_CAP)
        self.assertEqual(report.rounds, 2)

    def test_the_model_claiming_completion_does_not_stop_the_task(self):
        report = self._run(
            [
                _reply("任务已完成\n```python\nclick('开始')\n```"),
                _reply("任务已完成\n```python\nclick('开始')\n```"),
            ],
            rounds=2,
        )
        self.assertGreaterEqual(report.completion_claims_ignored, 1)
        self.assertEqual(report.stop_reason, STOP_ROUND_CAP)
        self.assertEqual(report.rounds, 2)

    def test_the_handoff_says_the_round_cap_is_the_reason(self):
        report = self._run(
            [_reply("```python\nclick('开始')\n```")] * 2,
            rounds=2,
        )
        self.assertIsNotNone(report.handoff)
        assert report.handoff is not None
        self.assertEqual(report.handoff.reason, STOP_ROUND_CAP)
        self.assertIn("轮次上限", report.handoff.to_text())


# ---------------------------------------------------------------------------
# AC3 重试只针对可逆动作
# ---------------------------------------------------------------------------


class RetryReversibilityTest(unittest.TestCase):
    """AC3：移动 / 滚动 / 聚焦可以重试；发消息 / 支付 / 删除**不重试**。"""

    def test_a_reversible_action_is_retried_with_the_backoff_schedule(self):
        delays: list[float] = []
        injector = _FlakyInjector(fail_moves=10)
        seam, _ = _build_seam(injector=injector)
        retrying = RetryingSeam(seam, sleeper=delays.append)
        result = retrying.run(
            SeamRequest(target_description="目标", action_kind=ActionKind.MOVE)
        )
        self.assertFalse(result.ok)
        self.assertEqual(retrying.retries, 3)
        self.assertEqual(
            [record.delay_ms for record in retrying.attempts[1:]],
            list(RETRY_BACKOFF_MS),
        )
        self.assertEqual([round(delay, 3) for delay in delays], [0.2, 0.5, 1.2])

    def test_an_irreversible_action_is_not_retried(self):
        injector = _FlakyInjector(fail_moves=10)
        # 📌 **工单 M16 起**：「发送消息」是危险动作 → 闸门会问一次。
        # 本测的是**重试策略**（不可逆动作不重试），所以闸门接一个**全都批**的审批人，
        # 让动作真的发出去 —— 被拦下的话测的就不是重试了。
        seam, _ = _build_seam(
            injector=injector,
            approval_gate=ApprovalGate(approver=AutoApprover()),
        )
        retrying = RetryingSeam(seam, sleeper=lambda _seconds: None)
        result = retrying.run(
            SeamRequest(target_description="发送消息", action_kind=ActionKind.CLICK)
        )
        self.assertFalse(result.ok)
        self.assertEqual(retrying.retries, 0)
        self.assertEqual(len(retrying.attempts), 1)
        self.assertEqual(len([event for event in injector.events if event[0] == "move"]), 1)

    def test_reversibility_classification_covers_the_three_named_kinds(self):
        for keyword in ("发送消息", "支付", "删除"):
            verdict = classify_reversibility("click", target_description=keyword)
            self.assertFalse(verdict.reversible, keyword)
        self.assertTrue(classify_reversibility("move").reversible)
        self.assertTrue(classify_reversibility("scroll").reversible)
        self.assertTrue(classify_reversibility("click", target_description="输入框").reversible)

    def test_unknown_action_kinds_are_not_retried(self):
        verdict = classify_reversibility("type", target_description="正文")
        self.assertFalse(verdict.reversible)


# ---------------------------------------------------------------------------
# AC4 重试前重新感知
# ---------------------------------------------------------------------------


class ReperceiveBeforeRetryTest(unittest.TestCase):
    """AC4：重试走的是**重新感知**后的新坐标，复用旧坐标的那条路径不存在。"""

    def test_the_retry_injects_a_new_coordinate(self):
        injector = _FlakyInjector(fail_moves=1)
        locator = _FakeLocator(rects=((100, 200, 140, 240), (400, 500, 440, 540)))
        seam, _ = _build_seam(locator=locator, injector=injector)
        retrying = RetryingSeam(seam, sleeper=lambda _seconds: None)
        result = retrying.run(
            SeamRequest(target_description="开始", action_kind=ActionKind.CLICK)
        )
        self.assertTrue(result.ok)
        moves = [event for event in injector.events if event[0] == "move"]
        self.assertGreaterEqual(len(moves), 2)
        self.assertNotEqual(moves[0][1:], moves[1][1:])
        self.assertEqual(locator.calls, 2)

    def test_the_attempt_records_keep_both_coordinates(self):
        """每一次尝试的落点都在记录里 —— **能核验**「重试换了新坐标」。"""
        injector = _FlakyInjector(fail_moves=1)
        locator = _FakeLocator(rects=((100, 200, 140, 240), (400, 500, 440, 540)))
        seam, _ = _build_seam(locator=locator, injector=injector)
        retrying = RetryingSeam(seam, sleeper=lambda _seconds: None)
        retrying.run(SeamRequest(target_description="开始", action_kind=ActionKind.CLICK))
        self.assertEqual(len(retrying.attempts), 2)
        self.assertEqual(retrying.attempts[0].point, (120, 220))
        self.assertNotEqual(retrying.attempts[0].point, retrying.attempts[1].point)
        self.assertEqual(retrying.attempts[1].delay_ms, RETRY_BACKOFF_MS[0])


# ---------------------------------------------------------------------------
# AC5 四类卡死检测器
# ---------------------------------------------------------------------------


class StallDetectorTest(unittest.TestCase):
    """AC5：帧无变化 / 重复动作 / 进度停滞 / 轮次硬上限，各有一条可复现的用例。"""

    def _verdict(self, policy, detector):
        return {item.detector: item for item in policy.detectors}[detector]

    def test_frame_unchanged_detector(self):
        policy = StopPolicy()
        policy.emit(_trace(frame_changed=False))
        policy.emit(_trace(frame_changed=False))
        verdict = self._verdict(policy, DETECTOR_FRAME_UNCHANGED)
        self.assertTrue(verdict.triggered)
        self.assertEqual(verdict.count, 2)

    def test_repeated_action_detector(self):
        policy = StopPolicy()
        for _ in range(3):
            policy.emit(_trace(target="开始", frame_changed=True))
        verdict = self._verdict(policy, DETECTOR_REPEATED_ACTION)
        self.assertTrue(verdict.triggered)
        self.assertEqual(verdict.count, 3)

    def test_different_targets_are_not_a_repeated_action(self):
        policy = StopPolicy()
        policy.emit(_trace(target="甲", frame_changed=True))
        policy.emit(_trace(target="乙", frame_changed=True))
        verdict = self._verdict(policy, DETECTOR_REPEATED_ACTION)
        self.assertFalse(verdict.triggered)

    def test_progress_stalled_detector(self):
        policy = StopPolicy(stalled_rounds_threshold=2)
        for _ in range(2):
            policy.emit(_trace(frame_changed=False))
            policy.observe_round()
        verdict = self._verdict(policy, DETECTOR_PROGRESS_STALLED)
        self.assertTrue(verdict.triggered)
        self.assertEqual(verdict.count, 2)

    def test_progress_resets_when_the_interface_changes(self):
        policy = StopPolicy(stalled_rounds_threshold=2)
        policy.emit(_trace(frame_changed=False))
        policy.observe_round()
        policy.emit(_trace(frame_changed=True))
        policy.observe_round()
        verdict = self._verdict(policy, DETECTOR_PROGRESS_STALLED)
        self.assertFalse(verdict.triggered)

    def test_round_cap_detector(self):
        policy = StopPolicy(max_rounds=2)
        policy.observe_round()
        policy.observe_round()
        verdict = self._verdict(policy, DETECTOR_ROUND_CAP)
        self.assertTrue(verdict.triggered)
        self.assertEqual(policy.stop_reason, STOP_ROUND_CAP)

    def test_the_action_hash_ignores_the_coordinate(self):
        self.assertEqual(action_hash("click", "开始"), action_hash("click", " 开始 "))
        self.assertNotEqual(action_hash("click", "开始"), action_hash("click", "结束"))


# ---------------------------------------------------------------------------
# AC6 提权场景零动作交人
# ---------------------------------------------------------------------------


class ElevationHandoffTest(unittest.TestCase):
    """AC6：检测到提权 / 安全桌面 → 注入序列为空、交人、代码里没有降安全级别的路径。"""

    def test_no_input_is_injected_when_the_secure_desktop_is_detected(self):
        injector = FakeInputInjector()
        detector = FakeSecureDesktopDetector(elevated=True)
        seam, _ = _build_seam(injector=injector, detector=detector)
        result = seam.run(SeamRequest(target_description="开始"))
        self.assertFalse(result.ok)
        self.assertEqual(result.result.error_code, ErrorCode.UAC_OR_SECURE_DESKTOP)
        self.assertEqual(injector.events, [])
        self.assertEqual(detector.calls, 1)

    def test_the_action_log_has_a_handed_off_to_human_record(self):
        detector = FakeSecureDesktopDetector(elevated=True)
        traces = ListTraceSink()
        seam, _ = _build_seam(detector=detector, trace_sink=traces)
        seam.run(SeamRequest(target_description="开始"))
        record = build_record(traces.traces[0]).to_dict()
        self.assertTrue(
            any(HANDOFF_MARKER in note for note in record["notes"]),
            record["notes"],
        )

    def test_the_stop_policy_stops_with_the_elevated_reason(self):
        policy = StopPolicy()
        seam, _ = _build_seam(trace_sink=policy)
        policy.emit(
            _trace(error_code=ErrorCode.UAC_OR_SECURE_DESKTOP.value, frame_changed=None)
        )
        self.assertEqual(policy.stop_reason, STOP_ELEVATED)

    def test_a_broken_detector_is_treated_as_elevated(self):
        """**fail-closed**：判不了提权 → 当提权处理（宁可交人，也不盲打）。"""

        class _BrokenDetector:
            def detect(self):
                raise OSError("读不到桌面")

        injector = FakeInputInjector()
        seam, _ = _build_seam(injector=injector, detector=_BrokenDetector())
        result = seam.run(SeamRequest(target_description="开始"))
        self.assertFalse(result.ok)
        self.assertEqual(result.result.error_code, ErrorCode.UAC_OR_SECURE_DESKTOP)
        self.assertEqual(injector.events, [])

    def test_the_windows_detector_treats_undeterminable_as_elevated(self):
        from minicode.action.windows import WindowsSecureDesktopDetector

        state = WindowsSecureDesktopDetector._undeterminable("OpenInputDesktop 失败")
        self.assertTrue(state.elevated)
        self.assertIn("fail-closed", state.reason)

    def test_the_repository_has_no_path_that_lowers_the_secure_desktop_level(self):
        """C-07：**代码里不得默认开启** ``PromptOnSecureDesktop=0``。

        判据是「**真的去写那个设置**」——
        文档字符串与测试里提到这两个字串不算（那是在说这件事，不是在做这件事）。
        """
        offenders = []
        for path in sorted((_REPO_ROOT / "src" / "minicode").rglob("*.py")):
            source = path.read_text(encoding="utf-8")
            if _writes_the_secure_desktop_setting(source):
                offenders.append(path.name)
        self.assertEqual(offenders, [])


#: 改「安全桌面提示级别」的注册表 / 系统调用（**只查这几个**）。
_SECURE_DESKTOP_WRITERS = (
    "setvalue",
    "setvalueex",
    "systemparametersinfo",
    "regsetvalue",
)

#: 那个设置的名字与它所在的注册表位置。
_SECURE_DESKTOP_SETTING = "PromptOnSecureDesktop"
_SECURE_DESKTOP_KEY = "Control Panel\\Desktop"


def _writes_the_secure_desktop_setting(source: str) -> bool:
    """源码里有没有**真的去写**「安全桌面提示级别」这个设置。"""
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = (
            func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
        ).lower()
        if not any(writer in name for writer in _SECURE_DESKTOP_WRITERS):
            continue
        for argument in ast.walk(node):
            if isinstance(argument, ast.Constant) and isinstance(argument.value, str):
                if (
                    _SECURE_DESKTOP_SETTING in argument.value
                    or _SECURE_DESKTOP_KEY in argument.value
                ):
                    return True
    return False


# ---------------------------------------------------------------------------
# AC7 交接产物可读
# ---------------------------------------------------------------------------


class HandoffArtifactTest(unittest.TestCase):
    """AC7：交出去的东西含窗口标题 / 最后一步动作 / 已发坐标 / 已执行动作清单。"""

    def _artifact(self):
        policy = StopPolicy()
        policy.emit(_trace(target="开始", point=(120, 220), window_title="记事本"))
        policy.emit(_trace(target="结束", point=(300, 400), window_title="记事本"))
        return build_handoff(
            policy.signals,
            reason=STOP_ROUND_CAP,
            reason_text="达到轮次上限，停下来交给人",
            strategy_changes=policy.strategy_change_records,
        )

    def test_the_artifact_carries_the_four_required_pieces(self):
        artifact = self._artifact()
        self.assertEqual(artifact.window_title, "记事本")
        self.assertIn("结束", artifact.last_action)
        self.assertEqual(artifact.sent_points, ((120, 220), (300, 400)))
        self.assertEqual(len(artifact.executed_actions), 2)

    def test_the_artifact_is_readable_by_a_human(self):
        text = self._artifact().to_text()
        self.assertIn(HANDOFF_MARKER, text)
        self.assertIn("记事本", text)
        self.assertIn("(120, 220)", text)
        self.assertIn("结束", text)

    def test_the_artifact_is_json_serialisable(self):
        payload = self._artifact().to_dict()
        self.assertEqual(payload["reason"], STOP_ROUND_CAP)
        self.assertEqual(payload["sent_points"], [[120, 220], [300, 400]])

    def test_the_interface_calls_are_in_the_artifact(self):
        """定位失败的那些尝试也要看得见（人要知道最后在干什么）。"""
        artifact = build_handoff(
            (),
            reason=STOP_ROUND_CAP,
            api_calls=[
                {"kind": "find", "target": "开始", "ok": False, "error": "ambiguous_target"},
                {"kind": "ask_user", "target": "定不下来", "ok": True, "error": None},
            ],
        )
        self.assertIn("find(开始) → 失败:ambiguous_target", artifact.to_text())
        self.assertIn("ask_user(定不下来) → 成功", artifact.api_calls)
        self.assertIn("ask_user", artifact.last_action)

    def test_only_the_coordinates_that_were_really_sent_are_listed(self):
        """被拒的动作（零下发）**不算**「已发坐标」。"""
        policy = StopPolicy()
        policy.emit(_trace(target="开始", point=(120, 220), ok=True))
        policy.emit(
            _trace(
                target="发送消息",
                point=(300, 400),
                ok=False,
                error_code=ErrorCode.UAC_OR_SECURE_DESKTOP.value,
            )
        )
        artifact = build_handoff(policy.signals, reason=STOP_ELEVATED)
        self.assertEqual(artifact.sent_points, ((120, 220),))


# ---------------------------------------------------------------------------
# AC8 置信度不作放行条件
# ---------------------------------------------------------------------------


class DangerGateTest(unittest.TestCase):
    """AC8：模型自评置信度**不出现**在任何危险动作的放行判据里。"""

    def test_a_dangerous_action_still_needs_a_human_with_high_confidence(self):
        decision = DangerGate().evaluate(
            target_description="发送消息",
            intent="点击「发送消息」",
            context=ActionContext(model_confidence=0.99),
        )
        self.assertTrue(decision.requires_human)
        self.assertEqual(decision.action_class, "dangerous")

    def test_the_decision_does_not_depend_on_the_confidence_value(self):
        gate = DangerGate()
        high = gate.evaluate(
            target_description="支付",
            context=ActionContext(model_confidence=0.99),
        )
        low = gate.evaluate(
            target_description="支付",
            context=ActionContext(model_confidence=0.01),
        )
        self.assertEqual(high, low)

    def test_the_release_criterion_source_mentions_no_confidence(self):
        source = inspect.getsource(DangerGate.evaluate).lower()
        self.assertNotIn("confidence", source)

    def test_an_ordinary_action_needs_no_human(self):
        decision = DangerGate().evaluate(
            target_description="开始",
            context=ActionContext(model_confidence=0.99),
        )
        self.assertFalse(decision.requires_human)


class DangerGateOnTheChainTest(unittest.TestCase):
    """AC8 / AC 9：判据在**真实链路**上生效 —— 危险动作**不执行**，停下来交人。

    📌 **工单 M16 起**：链路上换成了**审批闸门**（``minicode.approval``）——
    M12 的 ``DangerGate`` 只判「要不要人工确认」，M16 的闸门**真的去问人**
    （没批就零动作下发）。本测试因此改成「闸门 + 拒绝的审批人」。
    """

    def _run(self, target: str):
        policy = StopPolicy(max_rounds=2)
        gate = ApprovalGate(approver=DenyApprover())
        seam, _ = _build_seam(trace_sink=policy, approval_gate=gate)
        api = ProtocolApi(seam=seam, locator=_FakeLocator(), approval_gate=gate)
        harness = DecisionHarness(
            caller=FakeModelClient(
                _reply(f"```python\nclick('{target}')\n```"),
                _reply(f"```python\nclick('{target}')\n```"),
            ),
            api=api,
            max_rounds=2,
            keep_frames=2,
            stop_policy=policy,
        )
        return harness.run("点一下"), seam

    def test_a_dangerous_action_is_not_executed_and_the_task_is_handed_over(self):
        report, seam = self._run("发送消息")
        self.assertEqual(report.stop_reason, "dangerous_action")
        self.assertIsNotNone(report.handoff)
        assert report.handoff is not None
        self.assertEqual(report.handoff.reason, "dangerous_action")
        self.assertIn("危险动作", report.handoff.to_text())
        # **没有动作下发**（一个输入都没发出去）。
        self.assertEqual(report.calls[0]["ok"], False)
        self.assertEqual(report.calls[0]["error"], "approval_denied")
        self.assertEqual(seam.injector.events, [])

    def test_an_ordinary_action_still_runs(self):
        report, _ = self._run("开始")
        self.assertNotEqual(report.stop_reason, "dangerous_action")


# ---------------------------------------------------------------------------
# AC9 边界写清
# ---------------------------------------------------------------------------


class ScopeBoundaryTest(unittest.TestCase):
    """AC9：危险动作**审批**与 **dry-run** 是后票（M16）的活，本单只判「要不要人工确认」。"""

    def test_the_gate_has_no_approval_entry_point(self):
        public = {
            name
            for name in dir(gate_module)
            if not name.startswith("_") and callable(getattr(gate_module, name))
        }
        self.assertEqual(
            {name for name in public if "approve" in name or "dry" in name}, set()
        )

    def test_the_decision_only_says_whether_a_human_is_needed(self):
        decision = DangerGate().evaluate(target_description="删除文件")
        self.assertTrue(decision.requires_human)
        self.assertIn("人工确认", decision.reason)

    def test_the_expectation_met_is_a_stop_reason_of_its_own(self):
        policy = StopPolicy()
        policy.emit(_trace(expectation_met=True, frame_changed=True))
        self.assertEqual(policy.stop_reason, STOP_EXPECTATION_MET)

    def test_every_stop_reason_is_in_the_published_table(self):
        """停止原因的取值集合是**公开**的（报告与交接产物照它读）。"""
        for reason in (
            STOP_ROUND_CAP,
            STOP_EXPECTATION_MET,
            STOP_ASKED_HUMAN,
            STOP_ELEVATED,
            STOP_STRATEGY_EXHAUSTED,
            STOP_DANGEROUS_ACTION,
        ):
            self.assertIn(reason, STOP_REASONS)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

"""M16（危险动作审批）的单测 —— **只测外部行为**。

工单 M16 的六条验收标准逐条对应下面的类：

* :class:`DangerCatalogTest` —— AC1 危险动作清单生效（十一类，每类都要审批）
* :class:`ApprovalBindingTest` —— AC2 审批绑定「意图 + 目标」与这一次动作
* :class:`DryRunTest` —— AC3 dry-run（呈现将要作用的对象；未确认零动作下发）
* :class:`ElevationTest` —— AC4 提权场景（零动作下发 + 交人 + 留痕）
* :class:`ConfidenceTest` —— AC5 置信度不作放行条件
* :class:`ClipboardApprovalTest` —— AC6 读剪贴板需审批
* :class:`GateOnTheChainTest` —— 闸门在**真实链路**上生效（缝 / 决策层）

全部用**假注入 / 假审批人 / 假窗口**：毫秒级跑完，不碰真桌面、不花钱。
"""

from __future__ import annotations

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
    ControlCandidate,
    LocateRequest,
    LocateResult,
)
from minicode.action.protocol import Action, ActionKind
from minicode.action.records import ListTraceSink, build_record
from minicode.action.seam import ActionExecutionSeam, SeamRequest
from minicode.decision.api import ProtocolApi
from minicode.decision.harness import DecisionHarness
from minicode.model.client import ModelReply
from minicode.model.effort import Effort
from minicode.model.fakes import FakeModelClient
from minicode.reflection.stop import STOP_DANGEROUS_ACTION, StopPolicy

from .catalog import (
    CATEGORY_TITLES,
    DANGER_RULES,
    DangerCategory,
    classify,
)
from .console import ConsoleApprover
from .fakes import (
    AutoApprover,
    DenyApprover,
    FakeSensitivePolicy,
    FakeWindowInspector,
    ScriptedApprover,
)
from .gate import APPROVAL_HANDOFF_NOTE, ApprovalGate
from .ports import WindowInfo
from .request import ApprovalDecision, TargetPreview

_DESKTOP = VirtualDesktop(0, 0, 2560, 1600)
_REPO_ROOT = Path(__file__).resolve().parents[3]


# ---------------------------------------------------------------------------
# 假实现
# ---------------------------------------------------------------------------


class _FakeLocator:
    """假定位通道：给一个固定控件。"""

    def __init__(self, name: str = "发送消息", window_title: str = "微信"):
        self.name = name
        self.window_title = window_title
        self.calls = 0

    def locate(self, request: LocateRequest) -> LocateResult:
        self.calls += 1
        rect = (100, 200, 140, 240)
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


class _CountingInjector(FakeInputInjector):
    """记下**剪贴板被读了几次**（AC6 要证明「未获批不读取」）。"""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.clipboard_reads = 0

    def read_clipboard(self) -> str | None:
        self.clipboard_reads += 1
        return super().read_clipboard()


def _build_seam(*, injector=None, gate=None, sink=None, locator=None):
    """搭一条缝（**全假**：不碰真桌面）。"""
    clock = VirtualClock()
    seam = ActionExecutionSeam(
        capture_sources=[FakeCaptureSource(64, 64)],
        tree=None,
        injector=injector if injector is not None else FakeInputInjector(),
        virtual_desktop=_DESKTOP,
        sleeper=clock.sleep,
        clock=clock.time,
        trace_sink=sink,
        locator=locator if locator is not None else _FakeLocator(),
        approval_gate=gate,
    )
    return seam


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
# AC1 危险动作清单生效
# ---------------------------------------------------------------------------


class DangerCatalogTest(unittest.TestCase):
    """AC1：十一类危险动作，每类在执行前都产生审批请求。"""

    #: 每一类各一句代表性的话（关键词触发的九类 + 状态触发的两类）。
    SAMPLES = (
        (DangerCategory.DELETE, "删除这个文件"),
        (DangerCategory.PAYMENT, "点支付按钮"),
        (DangerCategory.SEND_MESSAGE, "发送消息"),
        (DangerCategory.REGISTER, "注册一个新账号"),
        (DangerCategory.SYSTEM_SETTINGS, "打开系统设置改一下"),
        (DangerCategory.COMMAND_LINE, "在命令行里运行命令"),
        (DangerCategory.INSTALL_SOFTWARE, "安装这个软件"),
        (DangerCategory.REGISTRY, "改一下注册表"),
        (DangerCategory.ELEVATION, "以管理员身份运行"),
        (DangerCategory.CLIPBOARD_READ, "读剪贴板内容"),
    )

    def test_every_listed_category_is_recognized(self):
        for category, phrase in self.SAMPLES:
            with self.subTest(category=category.value):
                match = classify(intent=phrase)
                self.assertIsNotNone(match, phrase)
                self.assertEqual(match.category, category)
                self.assertEqual(match.title, CATEGORY_TITLES[category])

    def test_the_catalog_covers_the_eleven_categories_of_the_ticket(self):
        """工单 AC1 点名十一类 —— 清单里不许少。"""
        self.assertEqual(len(CATEGORY_TITLES), 11)
        self.assertEqual(
            set(CATEGORY_TITLES),
            {
                DangerCategory.DELETE,
                DangerCategory.PAYMENT,
                DangerCategory.SEND_MESSAGE,
                DangerCategory.REGISTER,
                DangerCategory.SYSTEM_SETTINGS,
                DangerCategory.COMMAND_LINE,
                DangerCategory.INSTALL_SOFTWARE,
                DangerCategory.REGISTRY,
                DangerCategory.ELEVATION,
                DangerCategory.CLIPBOARD_READ,
                DangerCategory.SENSITIVE_SCREENSHOT,
            },
        )

    def test_the_sensitive_screenshot_category_is_triggered_by_the_window(self):
        """「截含敏感窗口的屏」由**前台窗口**触发，不由关键词触发。"""
        gate = ApprovalGate(
            approver=DenyApprover(),
            window_inspector=FakeWindowInspector(
                WindowInfo(hwnd=7, title="密码管理器")
            ),
            sensitive_policy=FakeSensitivePolicy(),
        )
        outcome = gate.check_capture()
        self.assertTrue(outcome.required)
        self.assertEqual(outcome.category, DangerCategory.SENSITIVE_SCREENSHOT)
        self.assertFalse(outcome.approved)

    def test_an_ordinary_window_is_not_sensitive(self):
        gate = ApprovalGate(
            approver=DenyApprover(),
            window_inspector=FakeWindowInspector(
                WindowInfo(hwnd=7, title="记事本")
            ),
            sensitive_policy=FakeSensitivePolicy(),
        )
        outcome = gate.check_capture()
        self.assertFalse(outcome.required)
        self.assertTrue(outcome.approved)

    def test_no_policy_means_no_sensitive_window_concept(self):
        """**敏感窗口的词表归 M20** —— 没接策略就没有这个概念。"""
        gate = ApprovalGate(approver=DenyApprover())
        outcome = gate.check_capture()
        self.assertFalse(outcome.required)

    def test_every_category_produces_an_approval_request(self):
        """**不存在静默执行的类别**：十一类都要产生审批请求。"""
        for category in DangerCategory:
            with self.subTest(category=category.value):
                gate = ApprovalGate(approver=DenyApprover())
                outcome = gate.check(
                    intent="做一件事",
                    target_description="某个目标",
                    category=category,
                )
                self.assertTrue(outcome.required)
                self.assertIsNotNone(outcome.request)
                self.assertEqual(outcome.request.category, category)
                self.assertFalse(outcome.approved)

    def test_an_ordinary_action_needs_no_approval(self):
        approver = DenyApprover()
        gate = ApprovalGate(approver=approver)
        outcome = gate.check(intent="点击「开始」", target_description="开始")
        self.assertFalse(outcome.required)
        self.assertTrue(outcome.approved)
        # **没人被问过**（审批人一次都没收到请求）。
        self.assertEqual(approver.requests, [])

    def test_the_m12_keyword_table_is_a_subset_of_this_catalog(self):
        """M12 的不可逆词表是本表的子集 —— 两处判据不许各说各话。"""
        from minicode.reflection.keywords import IRREVERSIBLE_KEYWORDS

        for keyword in IRREVERSIBLE_KEYWORDS:
            with self.subTest(keyword=keyword):
                self.assertIsNotNone(classify(intent=keyword))

    def test_every_rule_carries_its_source(self):
        """每个词表都要能说清「这些词是哪来的」（仓库规矩）。"""
        for rule in DANGER_RULES:
            with self.subTest(category=rule.category.value):
                self.assertTrue(rule.source)
                self.assertTrue(rule.keywords)


# ---------------------------------------------------------------------------
# AC2 审批绑定意图 + 目标
# ---------------------------------------------------------------------------


class ApprovalBindingTest(unittest.TestCase):
    """AC2：请求里有「要做什么」与「对哪个目标」；结论绑定这一次动作。"""

    def test_the_request_carries_both_the_intent_and_the_target(self):
        gate = ApprovalGate(approver=AutoApprover())
        preview = TargetPreview(
            control_name="发送",
            control_rect=(100, 200, 140, 240),
            point=(120, 220),
            window_title="微信",
            screenshot_sha256="deadbeef",
        )
        outcome = gate.check(
            intent="把这条消息发出去",
            target_description="发送按钮",
            preview=preview,
        )
        request = outcome.request
        assert request is not None
        self.assertEqual(request.intent, "把这条消息发出去")
        self.assertEqual(request.target_description, "发送按钮")
        # 「对哪个目标」要**可定位**：控件名、控件框、窗口标题都在。
        self.assertEqual(request.preview.control_name, "发送")
        self.assertEqual(request.preview.control_rect, (100, 200, 140, 240))
        self.assertEqual(request.preview.window_title, "微信")

    def test_the_decision_is_bound_to_this_one_action(self):
        """拿上一次的结论来批这一次 —— **不认**。"""
        stale = ApprovalDecision(approved=True, request_id="别的动作", reason="上次的批准")
        gate = ApprovalGate(approver=ScriptedApprover([stale]))
        outcome = gate.check(intent="发送消息", target_description="发送")
        self.assertTrue(outcome.required)
        self.assertFalse(outcome.approved)
        self.assertIn("对不上", outcome.reason)

    def test_two_identical_actions_get_two_different_requests(self):
        """审批不是「一次性授权后续同类动作」—— 每次都要重新问。"""
        approver = AutoApprover()
        gate = ApprovalGate(approver=approver)
        first = gate.check(intent="发送消息", target_description="发送")
        second = gate.check(intent="发送消息", target_description="发送")
        assert first.request is not None and second.request is not None
        self.assertNotEqual(first.request.request_id, second.request.request_id)
        self.assertEqual(len(approver.requests), 2)

    def test_no_approver_means_not_approved(self):
        """fail-closed：没人背书就不许执行。"""
        gate = ApprovalGate()
        outcome = gate.check(intent="发送消息", target_description="发送")
        self.assertTrue(outcome.required)
        self.assertFalse(outcome.approved)
        self.assertIn("没有接审批人", outcome.reason)


# ---------------------------------------------------------------------------
# AC3 dry-run
# ---------------------------------------------------------------------------


class DryRunTest(unittest.TestCase):
    """AC3：不可逆动作执行前先呈现将要作用的对象；未确认时零动作下发。"""

    def test_the_request_presents_the_target_control_the_point_and_the_hash(self):
        gate = ApprovalGate(approver=DenyApprover())
        preview = TargetPreview(
            control_name="删除",
            control_rect=(10, 20, 30, 40),
            point=(20, 30),
            window_title="资源管理器",
            screenshot_sha256="abc123",
        )
        outcome = gate.check(
            intent="删除这个文件", target_description="删除", preview=preview
        )
        request = outcome.request
        assert request is not None
        self.assertEqual(request.preview.control_rect, (10, 20, 30, 40))
        self.assertEqual(request.preview.point, (20, 30))
        self.assertEqual(request.preview.screenshot_sha256, "abc123")
        # 人读版里也要看得见（人就是看这份的）。
        text = request.to_text()
        self.assertIn("删除", text)
        self.assertIn("abc123", text)

    def test_not_approved_means_zero_actions_are_dispatched(self):
        injector = FakeInputInjector()
        gate = ApprovalGate(approver=DenyApprover())
        seam = _build_seam(injector=injector, gate=gate)
        result = seam.run(SeamRequest(target_description="发送消息"))
        self.assertFalse(result.ok)
        self.assertEqual(result.result.error_code, ErrorCode.APPROVAL_DENIED)
        # **一个输入都没发出去**。
        self.assertEqual(injector.events, [])
        self.assertIn(APPROVAL_HANDOFF_NOTE, result.result.notes)

    def test_approved_means_the_action_really_runs(self):
        injector = FakeInputInjector()
        gate = ApprovalGate(approver=AutoApprover())
        seam = _build_seam(injector=injector, gate=gate)
        result = seam.run(SeamRequest(target_description="发送消息"))
        self.assertTrue(result.ok)
        self.assertTrue(injector.events)

    def test_the_action_record_carries_the_approval(self):
        sink = ListTraceSink()
        gate = ApprovalGate(approver=DenyApprover())
        seam = _build_seam(gate=gate, sink=sink)
        seam.run(SeamRequest(target_description="发送消息"))
        self.assertTrue(sink.traces)
        record = build_record(sink.traces[-1]).to_dict()
        self.assertIsNotNone(record["approval"])
        assert record["approval"] is not None
        self.assertEqual(record["approval"]["category"], "send_message")
        self.assertFalse(record["approval"]["approved"])


# ---------------------------------------------------------------------------
# AC4 提权场景
# ---------------------------------------------------------------------------


class ElevationTest(unittest.TestCase):
    """AC4：提权 → 零动作下发 + 交人 + 日志里有「已交接给人」。"""

    def test_the_elevation_detection_produces_an_approval_request(self):
        gate = ApprovalGate(approver=DenyApprover())
        outcome = gate.check(
            intent="在提权 / 安全桌面上执行动作",
            target_description="提权弹窗",
            category=DangerCategory.ELEVATION,
        )
        self.assertTrue(outcome.required)
        self.assertEqual(outcome.category, DangerCategory.ELEVATION)
        assert outcome.request is not None
        self.assertEqual(
            outcome.request.category_title, CATEGORY_TITLES[DangerCategory.ELEVATION]
        )

    def test_detected_elevation_means_zero_actions_and_a_handoff_record(self):
        """检测到提权 → **零动作下发** + 交人 + 日志里有「已交接给人」。"""
        from minicode.action.executor import ActionExecutor

        injector = FakeInputInjector()
        approver = DenyApprover()
        gate = ApprovalGate(approver=approver)
        executor = ActionExecutor(
            injector,
            secure_desktop=FakeSecureDesktopDetector(elevated=True),
            approval_gate=gate,
        )
        result = executor.execute(
            Action(ActionKind.CLICK, {"x": 10, "y": 20}, intent="点击「删除」")
        )
        self.assertFalse(result.ok)
        self.assertEqual(result.error_code, ErrorCode.UAC_OR_SECURE_DESKTOP)
        self.assertEqual(injector.events, [])
        self.assertTrue(
            any("已交接给人" in note for note in result.notes),
            result.notes,
        )
        # 提权也是**一类危险动作** → 产生了审批请求（AC1：不存在静默执行的类别）。
        self.assertEqual(len(approver.requests), 1)
        assert approver.requests[0].category == DangerCategory.ELEVATION

    def test_the_new_code_has_no_path_that_lowers_the_elevation_security_level(self):
        """代码里**没有**默认开启「降低提权安全级别」的路径。

        ⚠️ 判据与 M12 那条**同一处**（``reflection/test_reflection.py``
        的 ``test_the_repository_has_no_path_that_lowers_the_secure_desktop_level``）——
        它扫的是**整个** ``src/minicode``（含本包），是 AST 级的判据
        （文档字符串里提到那个设置不算）。本条只钉住**本包**没引入它。
        """
        from minicode.reflection.test_reflection import (
            _writes_the_secure_desktop_setting,
        )

        offenders = []
        for path in sorted((_REPO_ROOT / "src" / "minicode" / "approval").rglob("*.py")):
            if _writes_the_secure_desktop_setting(path.read_text(encoding="utf-8")):
                offenders.append(path.name)
        self.assertEqual(offenders, [])


# ---------------------------------------------------------------------------
# AC5 置信度不作放行条件
# ---------------------------------------------------------------------------


class ConfidenceTest(unittest.TestCase):
    """AC5：危险动作的放行判据里**不出现**模型自评置信度。"""

    def test_the_release_criterion_source_mentions_no_confidence(self):
        source = inspect.getsource(ApprovalGate.check).lower()
        self.assertNotIn("confidence", source)
        source = inspect.getsource(ApprovalGate.classify).lower()
        self.assertNotIn("confidence", source)

    def test_a_high_confidence_but_wrong_scenario_still_needs_a_human(self):
        """高置信度但错误 —— 仍然要人工确认。"""
        gate = ApprovalGate(approver=DenyApprover())
        outcome = gate.check(
            intent="发送消息", target_description="发送", extra_text="置信度 0.99"
        )
        self.assertTrue(outcome.required)
        self.assertFalse(outcome.approved)

    def test_the_verdict_does_not_depend_on_the_confidence_text(self):
        """带不带置信度，结论一样（判据里没有它）。"""
        gate = ApprovalGate(approver=DenyApprover())
        high = gate.check(intent="发送消息", extra_text="置信度 0.99")
        low = gate.check(intent="发送消息", extra_text="置信度 0.01")
        self.assertEqual(high.required, low.required)
        self.assertEqual(high.approved, low.approved)
        self.assertEqual(high.category, low.category)

    def test_the_gate_takes_no_confidence_input_at_all(self):
        """判据的**入参**里就没有置信度 —— 上下文里带着也没地方放。"""
        parameters = inspect.signature(ApprovalGate.check).parameters
        self.assertNotIn("confidence", parameters)
        self.assertNotIn("context", parameters)


# ---------------------------------------------------------------------------
# AC6 读剪贴板需审批
# ---------------------------------------------------------------------------


class ClipboardApprovalTest(unittest.TestCase):
    """AC6：读剪贴板前产生审批请求；未获批不读取。"""

    def _executor(self, gate, injector):
        from minicode.action.executor import ActionExecutor

        return ActionExecutor(injector, approval_gate=gate)

    def test_not_approved_means_the_clipboard_is_not_read(self):
        injector = _CountingInjector()
        injector.write_clipboard("用户原来的内容")
        executor = self._executor(ApprovalGate(approver=DenyApprover()), injector)
        result = executor.execute(
            Action(ActionKind.TYPE, {"text": "端到端"}, intent="输入文本")
        )
        self.assertTrue(result.ok)
        self.assertEqual(injector.clipboard_reads, 0)
        # 剪贴板内容一个字都没被取走。
        self.assertEqual(injector.read_clipboard.__self__._clipboard, "用户原来的内容")

    def test_approved_means_the_clipboard_is_read_and_restored(self):
        injector = _CountingInjector()
        injector.write_clipboard("用户原来的内容")
        executor = self._executor(ApprovalGate(approver=AutoApprover()), injector)
        result = executor.execute(
            Action(ActionKind.TYPE, {"text": "端到端"}, intent="输入文本")
        )
        self.assertTrue(result.ok)
        self.assertEqual(injector.clipboard_reads, 1)
        self.assertEqual(injector.read_clipboard(), "用户原来的内容")

    def test_the_clipboard_read_produces_an_approval_request(self):
        approver = DenyApprover()
        gate = ApprovalGate(approver=approver)
        injector = _CountingInjector()
        executor = self._executor(gate, injector)
        executor.execute(Action(ActionKind.TYPE, {"text": "端到端"}, intent="输入文本"))
        self.assertEqual(len(approver.requests), 1)
        assert approver.requests[0].category == DangerCategory.CLIPBOARD_READ


# ---------------------------------------------------------------------------
# 闸门在真实链路上生效
# ---------------------------------------------------------------------------


class GateOnTheChainTest(unittest.TestCase):
    """闸门在**真实链路**上生效：动作不执行，任务停下来交人。"""

    def _run(self, target: str, *, approver):
        policy = StopPolicy(max_rounds=2)
        gate = ApprovalGate(approver=approver)
        seam = _build_seam(gate=gate, sink=policy)
        api = ProtocolApi(seam=seam, approval_gate=gate)
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
        return harness.run("点一下")

    def test_a_dangerous_action_is_not_executed_and_the_task_is_handed_over(self):
        report = self._run("发送消息", approver=DenyApprover())
        self.assertEqual(report.stop_reason, STOP_DANGEROUS_ACTION)
        self.assertIsNotNone(report.handoff)
        assert report.handoff is not None
        self.assertEqual(report.handoff.reason, STOP_DANGEROUS_ACTION)
        self.assertEqual(report.calls[0]["ok"], False)
        self.assertEqual(report.calls[0]["error"], ErrorCode.APPROVAL_DENIED.value)

    def test_an_ordinary_action_still_runs(self):
        report = self._run("开始", approver=DenyApprover())
        self.assertNotEqual(report.stop_reason, STOP_DANGEROUS_ACTION)

    def test_typing_a_command_is_also_gated(self):
        """输入文本不走缝 —— 闸门也得在**那条路**上。"""
        gate = ApprovalGate(approver=DenyApprover())
        seam = _build_seam(gate=gate)
        api = ProtocolApi(seam=seam, approval_gate=gate)
        record = api.type_text("cmd /c del file")
        self.assertFalse(record["ok"])
        self.assertEqual(record["error"], ErrorCode.APPROVAL_DENIED.value)
        self.assertTrue(api.halted)
        self.assertEqual(seam.injector.events, [])

    def test_the_typed_text_does_not_land_in_the_approval_request(self):
        """C-15：**用户输入的正文不进日志**（审批请求会落进动作记录）。

        正文只交给**分类**（判「这是不是一条命令」），不进请求、不进记录。
        """
        approver = DenyApprover()
        gate = ApprovalGate(approver=approver)
        seam = _build_seam(gate=gate)
        api = ProtocolApi(seam=seam, approval_gate=gate)
        secret = "cmd /c del 机密文件"
        api.type_text(secret)
        self.assertEqual(len(approver.requests), 1)
        request = approver.requests[0]
        self.assertNotIn(secret, request.intent)
        self.assertNotIn(secret, request.target_description)
        self.assertNotIn(secret, request.to_text())


# ---------------------------------------------------------------------------
# 命令行里问人
# ---------------------------------------------------------------------------


class ConsoleApproverTest(unittest.TestCase):
    """命令行里问人：非交互一律不批（fail-closed）。"""

    def test_a_non_interactive_session_is_never_approved(self):
        approver = ConsoleApprover(interactive=lambda: False)
        gate = ApprovalGate(approver=approver)
        outcome = gate.check(intent="发送消息", target_description="发送")
        self.assertFalse(outcome.approved)

    def test_a_human_can_approve(self):
        approver = ConsoleApprover(
            reader=lambda _prompt: "y", interactive=lambda: True, writer=lambda _t: None
        )
        gate = ApprovalGate(approver=approver)
        outcome = gate.check(intent="发送消息", target_description="发送")
        self.assertTrue(outcome.approved)
        self.assertTrue(outcome.required)

    def test_the_human_sees_the_intent_the_target_and_the_dry_run(self):
        printed: list[str] = []
        approver = ConsoleApprover(
            reader=lambda _prompt: "n",
            interactive=lambda: True,
            writer=printed.append,
        )
        gate = ApprovalGate(approver=approver)
        gate.check(
            intent="把这条消息发出去",
            target_description="发送按钮",
            preview=TargetPreview(
                control_name="发送",
                control_rect=(1, 2, 3, 4),
                point=(2, 3),
                screenshot_sha256="hash123",
            ),
        )
        shown = "\n".join(printed)
        self.assertIn("把这条消息发出去", shown)
        self.assertIn("发送按钮", shown)
        self.assertIn("hash123", shown)

    def test_the_request_is_not_swallowed_by_the_program_output_capture(self):
        """决策层跑动作程序时会把 ``stdout`` **重定向**（那段输出还会回灌给模型）。

        审批请求是**给人看的** —— 它必须落到**真实的标准输出**上，
        既不能被重定向吞掉，也不能回灌进模型上下文。
        """
        import io
        from contextlib import redirect_stdout

        original = io.StringIO()
        approver = ConsoleApprover(interactive=lambda: False, stdout=original)
        gate = ApprovalGate(approver=approver)
        redirected = io.StringIO()
        with redirect_stdout(redirected):
            gate.check(intent="发送消息", target_description="发送")
        self.assertIn("发送消息", original.getvalue())
        self.assertEqual(redirected.getvalue(), "")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

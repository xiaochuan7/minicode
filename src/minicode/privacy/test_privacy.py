"""M20（隐私策略）的单测 —— **只测外部行为**。

工单 M20 的四条验收标准逐条对应下面的类：

* :class:`BlacklistTest` —— AC1 敏感窗口黑名单（标题命中敏感词表 **或** 对话框类名）
* :class:`GuardTest` —— AC1 抓帧之前的**硬闸门**（根本不许拍、不许发）
* :class:`SensitiveWindowSkipTest` —— AC1 不被抓图、不进模型上下文、动作记录记一笔
* :class:`ClipboardApprovalTest` —— AC2 剪贴板读取需审批（未获批不读取）
* :class:`PolicyDocumentTest` —— AC3 策略文档成文（每条都能在实现里找到对应行为）
* :class:`RedactionConsistencyTest` —— AC4 与脱敏器一致（同一处规则，不写两套）

全部用**假窗口 / 假截图源 / 假注入器**：毫秒级跑完，不碰真桌面、不花钱。
"""

from __future__ import annotations

import unittest
from pathlib import Path

from minicode.action.errors import ErrorCode
from minicode.action.fakes import FakeCaptureSource, FakeInputInjector, VirtualClock
from minicode.action.geometry import VirtualDesktop
from minicode.action.ports import ControlCandidate, LocateRequest, LocateResult
from minicode.action.protocol import Action, ActionKind
from minicode.action.records import ListTraceSink, build_record
from minicode.action.seam import ActionExecutionSeam, SeamRequest
from minicode.approval.fakes import AutoApprover, DenyApprover
from minicode.approval.gate import ApprovalGate
from minicode.approval.ports import WindowInfo
from minicode.redact.patterns import PASSWORD_PLACEHOLDER, SECRET_PATTERNS
from minicode.redact.redactor import BODY_CONTROL_TYPES

from .document import render_policy_document, resolve_implementation
from .guard import SENSITIVE_WINDOW_SKIPPED_NOTE, SensitiveWindowGuard
from .policy import SensitiveWindowBlacklist, default_policy

_DESKTOP = VirtualDesktop(0, 0, 2560, 1600)
_REPO_ROOT = Path(__file__).resolve().parents[3]
_POLICY_DOC = _REPO_ROOT / "docs" / "privacy-policy.md"


# ---------------------------------------------------------------------------
# 假实现
# ---------------------------------------------------------------------------


class _FakeInspector:
    """假的前台窗口读取（可模拟「读不到窗口」）。

    ``visible`` —— 这一帧里**看得见**的窗口（M20 的闸门问的是这个：
    抓的是整屏帧，前台背后的窗口照样在画面里）。
    """

    def __init__(
        self,
        window: WindowInfo | None = None,
        error: Exception | None = None,
        *,
        visible: tuple[WindowInfo, ...] | None = None,
    ):
        self.window = window or WindowInfo()
        self.error = error
        self.visible = visible
        self.calls = 0

    def foreground(self) -> WindowInfo:
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.window

    def visible_windows(self) -> tuple[WindowInfo, ...]:
        self.calls += 1
        if self.error is not None:
            raise self.error
        if self.visible is not None:
            return self.visible
        return (self.window,)


class _CountingInjector(FakeInputInjector):
    """记下**剪贴板被读了几次**（AC2 要证明「未获批不读取」）。"""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.clipboard_reads = 0

    def read_clipboard(self) -> str | None:
        self.clipboard_reads += 1
        return super().read_clipboard()


class _FakeLocator:
    """假定位通道：给一个固定控件。"""

    def __init__(self, name: str = "开始", window_title: str = "任务栏"):
        self.name = name
        self.window_title = window_title

    def locate(self, request: LocateRequest) -> LocateResult:
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


def _guard(
    window: WindowInfo | None = None,
    *,
    error: Exception | None = None,
    visible: tuple[WindowInfo, ...] | None = None,
) -> SensitiveWindowGuard:
    """装一个闸门（默认黑名单 = 仓库那一份）。"""
    return SensitiveWindowGuard(
        blacklist=SensitiveWindowBlacklist(),
        window_inspector=_FakeInspector(window, error, visible=visible),
    )


def _seam(
    *,
    guard: SensitiveWindowGuard | None = None,
    gate: ApprovalGate | None = None,
    injector: FakeInputInjector | None = None,
    capture: FakeCaptureSource | None = None,
    sink: ListTraceSink | None = None,
) -> ActionExecutionSeam:
    """搭一条缝（**全假**：不碰真桌面）。"""
    clock = VirtualClock()
    return ActionExecutionSeam(
        capture_sources=[capture if capture is not None else FakeCaptureSource(64, 64)],
        tree=None,
        injector=injector if injector is not None else FakeInputInjector(),
        virtual_desktop=_DESKTOP,
        sleeper=clock.sleep,
        clock=clock.time,
        trace_sink=sink,
        locator=_FakeLocator(),
        approval_gate=gate,
        privacy_guard=guard,
    )


# ---------------------------------------------------------------------------
# AC1 · 敏感窗口黑名单
# ---------------------------------------------------------------------------


class BlacklistTest(unittest.TestCase):
    """AC1：标题命中敏感词表 **或** 对话框类名的窗口被拉黑。"""

    def test_a_title_hitting_the_word_list_is_blacklisted(self):
        blacklist = SensitiveWindowBlacklist()
        self.assertIsNotNone(blacklist.match(WindowInfo(title="招商银行 - 个人中心")))
        self.assertIsNotNone(blacklist.match(WindowInfo(title="1Password - 保险箱")))
        self.assertIsNotNone(blacklist.match(WindowInfo(title="微信支付 - 收银台")))

    def test_a_dialog_class_name_is_blacklisted(self):
        blacklist = SensitiveWindowBlacklist()
        self.assertIsNotNone(blacklist.match(WindowInfo(title="", class_name="#32770")))
        self.assertIsNotNone(
            blacklist.match(WindowInfo(title="登录", class_name="Credential Dialog Xaml Host"))
        )

    def test_an_ordinary_window_is_not_blacklisted(self):
        blacklist = SensitiveWindowBlacklist()
        self.assertIsNone(
            blacklist.match(WindowInfo(title="任务栏", class_name="Shell_TrayWnd"))
        )
        self.assertIsNone(blacklist.match(WindowInfo()))

    def test_the_class_name_rule_does_not_match_a_prefix(self):
        """类名是**精确**匹配：``#3277`` 不是 ``#32770``（不做前缀猜）。"""
        blacklist = SensitiveWindowBlacklist()
        self.assertIsNone(blacklist.match(WindowInfo(title="普通窗口", class_name="#3277")))

    def test_the_word_list_covers_chinese_and_english(self):
        """模型有时写英文 —— 只收中文等于放行英文的同类窗口。"""
        blacklist = SensitiveWindowBlacklist()
        lowered = {keyword.lower() for keyword in blacklist.title_keywords}
        self.assertTrue(any(keyword.isascii() for keyword in lowered))
        self.assertTrue(any(not keyword.isascii() for keyword in lowered))

    def test_the_word_list_says_where_it_came_from(self):
        """仓库规矩：词表要能说清词是哪来的。"""
        self.assertTrue(SensitiveWindowBlacklist().source())

    def test_the_match_names_the_rule_that_fired(self):
        """命中了哪条判据要能读出来（动作记录里靠它定位是哪条规则生效）。"""
        match = SensitiveWindowBlacklist().match(WindowInfo(title="招商银行"))
        self.assertIsNotNone(match)
        self.assertEqual(match.kind, "title_keyword")
        self.assertIn("银行", match.value)


# ---------------------------------------------------------------------------
# AC1 · 抓帧之前的硬闸门
# ---------------------------------------------------------------------------


class GuardTest(unittest.TestCase):
    """AC1：黑名单窗口**根本不许拍** —— 抓帧之前就被拦下。"""

    def test_a_blacklisted_window_is_blocked(self):
        verdict = _guard(WindowInfo(title="招商银行")).check()
        self.assertTrue(verdict.blocked)
        self.assertEqual(verdict.match.kind, "title_keyword")

    def test_an_ordinary_window_is_not_blocked(self):
        verdict = _guard(WindowInfo(title="任务栏", class_name="Shell_TrayWnd")).check()
        self.assertFalse(verdict.blocked)

    def test_an_unreadable_window_is_treated_as_sensitive(self):
        """fail-closed：读不到当前窗口，我们**不知道**它是不是敏感窗口。"""
        verdict = _guard(error=OSError("读不到前台窗口")).check()
        self.assertTrue(verdict.blocked)

    def test_a_sensitive_window_behind_the_foreground_one_is_blocked_too(self):
        """抓的是**整屏**帧 —— 前台背后的密码管理器窗口照样在画面里。"""
        verdict = _guard(
            visible=(
                WindowInfo(title="任务栏", class_name="Shell_TrayWnd"),
                WindowInfo(title="1Password - 保险箱", class_name="Chrome_WidgetWin_1"),
            )
        ).check()
        self.assertTrue(verdict.blocked)
        self.assertEqual(verdict.match.window_title, "1Password - 保险箱")

    def test_a_dialog_class_name_in_the_background_is_blocked_too(self):
        verdict = _guard(
            visible=(
                WindowInfo(title="记事本", class_name="Notepad"),
                WindowInfo(title="", class_name="#32770"),
            )
        ).check()
        self.assertTrue(verdict.blocked)
        self.assertEqual(verdict.match.kind, "window_class")

    def test_the_verdict_says_what_was_matched(self):
        verdict = _guard(WindowInfo(title="招商银行", class_name="X")).check()
        payload = verdict.to_dict()
        self.assertTrue(payload["blocked"])
        self.assertEqual(payload["kind"], "title_keyword")
        self.assertIn("银行", payload["matched"])
        self.assertEqual(payload["window_title"], "招商银行")

# ---------------------------------------------------------------------------
# AC1 · 不被抓图、不进模型上下文、动作记录记一笔
# ---------------------------------------------------------------------------


class SensitiveWindowSkipTest(unittest.TestCase):
    """AC1：被拉黑的窗口 —— 不被抓图、不进模型上下文、动作记录里记下被跳过。"""

    def _run(self, request: SeamRequest, **kwargs):
        sink = ListTraceSink()
        seam = _seam(sink=sink, **kwargs)
        result = seam.run(request)
        return result, sink

    def test_the_frame_is_never_captured(self):
        capture = FakeCaptureSource(64, 64)
        result, _ = self._run(
            SeamRequest(target_description="开始"),
            guard=_guard(WindowInfo(title="招商银行")),
            capture=capture,
        )
        self.assertEqual(capture.calls, 0)
        self.assertFalse(result.ok)
        self.assertEqual(result.result.error_code, ErrorCode.SENSITIVE_WINDOW_BLOCKED)

    def test_no_input_is_injected(self):
        injector = FakeInputInjector()
        result, _ = self._run(
            SeamRequest(target_description="开始"),
            guard=_guard(WindowInfo(title="招商银行")),
            injector=injector,
        )
        self.assertEqual(injector.events, [])

    def test_the_screenshot_action_is_blocked_too(self):
        capture = FakeCaptureSource(64, 64)
        result, _ = self._run(
            SeamRequest(target_description="截图", action_kind=ActionKind.SCREENSHOT),
            guard=_guard(WindowInfo(title="招商银行")),
            capture=capture,
        )
        self.assertEqual(capture.calls, 0)
        self.assertEqual(result.result.error_code, ErrorCode.SENSITIVE_WINDOW_BLOCKED)

    def test_the_zoom_action_is_blocked_too(self):
        capture = FakeCaptureSource(64, 64)
        result, _ = self._run(
            SeamRequest(
                target_description="区域",
                action_kind=ActionKind.ZOOM,
                params={"region": [0, 0, 100, 100]},
            ),
            guard=_guard(WindowInfo(title="招商银行")),
            capture=capture,
        )
        self.assertEqual(capture.calls, 0)
        self.assertEqual(result.result.error_code, ErrorCode.SENSITIVE_WINDOW_BLOCKED)

    def test_the_action_log_records_the_skipped_window(self):
        """动作记录里要能看出「这个窗口被跳过了、为什么」。"""
        _, sink = self._run(
            SeamRequest(target_description="开始"),
            guard=_guard(WindowInfo(title="招商银行", class_name="X")),
        )
        self.assertEqual(len(sink.traces), 1)
        record = build_record(sink.traces[0]).to_dict()
        privacy = record["privacy"]
        self.assertTrue(privacy["blocked"])
        self.assertIn("银行", privacy["matched"])
        self.assertEqual(privacy["window_title"], "招商银行")
        # 画面一个字都不落：记录里只有哈希，没有图片数据。
        self.assertIsNone(record["screenshot_sha256"])
        self.assertTrue(
            any("敏感窗口" in note for note in record["notes"]),
            f"动作记录里没有「敏感窗口被跳过」这一笔：{record['notes']}",
        )
        self.assertIn(SENSITIVE_WINDOW_SKIPPED_NOTE, record["notes"])

    def test_an_ordinary_window_still_runs(self):
        """黑名单之外的窗口**不受影响**（闸门不许变成「什么都不许做」）。"""
        capture = FakeCaptureSource(64, 64)
        result, _ = self._run(
            SeamRequest(target_description="开始"),
            guard=_guard(WindowInfo(title="任务栏", class_name="Shell_TrayWnd")),
            capture=capture,
        )
        self.assertEqual(capture.calls, 1)
        self.assertTrue(result.ok)


# ---------------------------------------------------------------------------
# AC2 · 剪贴板读取需审批
# ---------------------------------------------------------------------------


class ClipboardApprovalTest(unittest.TestCase):
    """AC2：读剪贴板前产生审批请求，未获批不读取。"""

    def test_without_the_approval_the_clipboard_is_not_read(self):
        injector = _CountingInjector()
        injector.write_clipboard("用户原来的内容")
        seam = _seam(
            injector=injector,
            gate=ApprovalGate(approver=DenyApprover()),
        )
        result = seam.executor.execute(
            Action(kind=ActionKind.TYPE, params={"text": "你好"})
        )
        self.assertEqual(injector.clipboard_reads, 0)
        self.assertEqual(injector.read_clipboard.__self__._clipboard, "用户原来的内容")
        self.assertTrue(result.ok)

    def test_the_approval_request_is_produced_before_reading(self):
        injector = _CountingInjector()
        seam = _seam(
            injector=injector,
            gate=ApprovalGate(approver=DenyApprover()),
        )
        result = seam.executor.execute(
            Action(kind=ActionKind.TYPE, params={"text": "你好"})
        )
        self.assertIsNotNone(result.approval)
        self.assertEqual(result.approval["category"], "clipboard_read")
        self.assertTrue(result.approval["required"])
        self.assertFalse(result.approval["approved"])

    def test_approved_means_the_clipboard_is_read_and_restored(self):
        injector = _CountingInjector()
        injector.write_clipboard("用户原来的内容")
        seam = _seam(
            injector=injector,
            gate=ApprovalGate(approver=AutoApprover()),
        )
        seam.executor.execute(Action(kind=ActionKind.TYPE, params={"text": "你好"}))
        self.assertEqual(injector.clipboard_reads, 1)
        self.assertEqual(injector.read_clipboard(), "用户原来的内容")


# ---------------------------------------------------------------------------
# AC3 · 策略文档成文
# ---------------------------------------------------------------------------


class PolicyDocumentTest(unittest.TestCase):
    """AC3：文档里的每一条都能在实现里找到对应行为。"""

    def test_every_clause_points_at_an_existing_implementation(self):
        policy = default_policy()
        clauses = (
            policy.sent_to_model + policy.withheld + policy.persisted + policy.clipboard
        )
        self.assertTrue(clauses)
        for clause in clauses:
            with self.subTest(clause=clause.key):
                # 指向的位置必须**真的存在** —— 文档不许指着一个不存在的行为。
                self.assertIsNotNone(resolve_implementation(clause.implementation))

    def test_the_document_has_the_three_groups(self):
        document = render_policy_document(default_policy())
        self.assertIn("送进模型", document)
        self.assertIn("不送", document)
        self.assertIn("落盘", document)
        self.assertIn("剪贴板", document)

    def test_the_document_says_only_the_hash_is_persisted(self):
        document = render_policy_document(default_policy())
        self.assertIn("哈希", document)
        self.assertIn("不落原图", document)

    def test_the_document_lists_the_sensitive_window_rules(self):
        document = render_policy_document(default_policy())
        self.assertIn("敏感词表", document)
        self.assertIn("对话框类名", document)

    def test_the_checked_in_document_is_up_to_date(self):
        """文档由实现生成 —— 手改了就会与实现对不上（这里钉住）。"""
        self.assertTrue(_POLICY_DOC.is_file(), f"缺少策略文档：{_POLICY_DOC}")
        self.assertEqual(
            _POLICY_DOC.read_text(encoding="utf-8"),
            render_policy_document(default_policy()),
        )


# ---------------------------------------------------------------------------
# AC4 · 与脱敏器一致（同一处规则，不写两套）
# ---------------------------------------------------------------------------


class RedactionConsistencyTest(unittest.TestCase):
    """AC4：策略文档描述的边界 = 脱敏器的实际行为。"""

    def test_the_policy_quotes_the_redactors_own_constants(self):
        """文档里的脱敏边界**引用脱敏器自己的常量**，不是另抄一份。"""
        document = render_policy_document(default_policy())
        self.assertIn(PASSWORD_PLACEHOLDER, document)
        for control_type in BODY_CONTROL_TYPES:
            self.assertIn(control_type, document)
        for label, _pattern in SECRET_PATTERNS:
            self.assertIn(label, document)

    def test_the_redactor_actually_does_what_the_policy_says(self):
        """文档说的三件事，脱敏器真的在做（同一处规则，可对照）。"""
        from minicode.redact.config import ModelContextConfig
        from minicode.redact.redactor import TreeRedactor

        redactor = TreeRedactor(
            ModelContextConfig(send_tree_to_model=True, redaction_enabled=True)
        )
        view = redactor.render(
            (
                ControlCandidate(
                    name="用户",
                    control_type="Edit",
                    rect=(0, 0, 10, 10),
                    value="这是一段草稿正文",
                ),
                ControlCandidate(
                    name="口令",
                    control_type="Edit",
                    rect=(0, 0, 10, 10),
                    value="hunter2",
                    is_password=True,
                ),
                ControlCandidate(
                    name="令牌",
                    control_type="Label",
                    rect=(0, 0, 10, 10),
                    value="sk-abcdefghijklmnop",
                ),
            )
        )
        # ① 密码框的值是占位符；② 正文类控件的值不出现；③ 密钥形态被替换。
        self.assertIn(PASSWORD_PLACEHOLDER, view.text)
        self.assertNotIn("hunter2", view.text)
        self.assertNotIn("这是一段草稿正文", view.text)
        self.assertNotIn("sk-abcdefghijklmnop", view.text)
        self.assertIn("[REDACTED]", view.text)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

"""浏览器通道的单测（工单 M13 的六条验收标准）。

只测**外部行为**：一句目标描述进去，一个选中项 + 一个物理像素落点出来；
失败时给出**哪一类**失败与**为什么**；输入时给出「送达 / 退化 / 判不了」。
不测内部实现细节。
"""

from __future__ import annotations

import unittest

from minicode.action.errors import ErrorCode
from minicode.action.executor import ActionExecutor
from minicode.action.fakes import FakeCaptureSource, FakeControlTree, FakeInputInjector
from minicode.action.ports import LocateRequest, WindowAnchor
from minicode.action.protocol import ActionKind
from minicode.action.seam import ActionExecutionSeam, SeamRequest
from minicode.browser.capability import BrowserCapabilityTable
from minicode.browser.channel import BrowserChannel
from minicode.browser.elements import BrowserElement
from minicode.browser.fakes import FakeBrowserInput, FakePageProbe, FakePageStructure
from minicode.browser.ports import PageSnapshot
from minicode.browser.typing import BrowserTyping
from minicode.capability import Availability
from minicode.uia.capability import CapabilityTable
from minicode.uia.channel import UiaChannel
from minicode.uia.elements import UiaElement
from minicode.uia.fakes import FakeCapabilityProbe, FakeUiaQueryPort, ScriptedRead

_ANCHOR = WindowAnchor(hwnd=4242, app="chrome", version="142.0.7444.60")


def _element(name: str = "提交订单", **kwargs) -> BrowserElement:
    payload = {
        "name": name,
        "role": "button",
        "rect": (300, 200, 400, 240),
        "element_id": "s1#0",
    }
    payload.update(kwargs)
    return BrowserElement(**payload)


def _snapshot(*elements: BrowserElement, **kwargs) -> PageSnapshot:
    payload = {"elements": tuple(elements)}
    payload.update(kwargs)
    return PageSnapshot(**payload)


def _channel(snapshots, *, probe=None, pointer=None, redactor=None, max_candidates=8):
    table = BrowserCapabilityTable(probe or FakePageProbe([_flat_assessment()]))
    return BrowserChannel(
        structure=FakePageStructure(snapshots),
        capability=table,
        pointer=pointer,
        redactor=redactor,
        max_candidates=max_candidates,
    )


def _flat_assessment():
    from minicode.browser.capability import PageAssessment

    return PageAssessment(
        has_snapshot=True, named_elements=3, unnamed_interactive=2, duplicate_names=1
    )


class StructureLocateTest(unittest.TestCase):
    """AC 1：按可见文本定位到一个元素并操作它。"""

    def test_the_element_is_found_by_its_visible_text(self):
        channel = _channel([_snapshot(_element())])
        result = channel.locate(
            LocateRequest(target_description="提交订单", anchor=_ANCHOR)
        )
        self.assertIsNone(result.error_code)
        self.assertIsNotNone(result.selected)
        self.assertEqual(result.selected.name, "提交订单")
        self.assertEqual(result.selected.control_type, "button")

    def test_the_landing_point_is_the_element_rect_center_in_physical_space(self):
        channel = _channel([_snapshot(_element(rect=(300, 200, 400, 240)))])
        result = channel.locate(
            LocateRequest(target_description="提交订单", anchor=_ANCHOR)
        )
        self.assertEqual(result.landing_point, (350, 220))

    def test_the_basis_is_the_page_structure_not_a_picture(self):
        """定位依据是结构快照里**有**这个元素 —— 没有就是没有（覆盖失败）。"""
        channel = _channel([_snapshot(_element(name="取消订单"))])
        result = channel.locate(
            LocateRequest(target_description="提交订单", anchor=_ANCHOR)
        )
        self.assertEqual(result.error_code, ErrorCode.TARGET_NOT_FOUND)
        self.assertEqual(result.failure_type, "coverage")
        # 覆盖失败是**唯一**允许切视觉的情形（架构决策备忘 §2.5 ①）。
        self.assertTrue(result.allows_visual_fallback)

    def test_a_unique_candidate_costs_no_model_call(self):
        channel = _channel([_snapshot(_element())])
        result = channel.locate(
            LocateRequest(target_description="提交订单", anchor=_ANCHOR)
        )
        self.assertEqual(result.model_calls, 0)


class SameSemanticsAsDesktopTest(unittest.TestCase):
    """AC 2：与桌面通道同语义 —— 同一个动作与换算入口。"""

    def _uia_channel(self):
        table = CapabilityTable(FakeCapabilityProbe(), sleeper=lambda seconds: None)
        return UiaChannel(
            query=FakeUiaQueryPort(
                [ScriptedRead(elements=(UiaElement(name="提交订单", rect=(300, 200, 400, 240)),))]
            ),
            capability=table,
        )

    def _run_seam(self, locator):
        injector = FakeInputInjector()
        seam = ActionExecutionSeam(
            capture_sources=[FakeCaptureSource()],
            tree=FakeControlTree(()),
            injector=injector,
            locator=locator,
        )
        result = seam.run(
            SeamRequest(target_description="提交订单", action_kind=ActionKind.CLICK)
        )
        return result, injector

    def test_both_channels_emit_the_same_action_for_the_same_rect(self):
        desktop, desktop_injector = self._run_seam(self._uia_channel())
        web, web_injector = self._run_seam(
            _channel([_snapshot(_element(rect=(300, 200, 400, 240)))])
        )
        self.assertIsNotNone(desktop.action)
        self.assertIsNotNone(web.action)
        self.assertEqual(desktop.action.kind, web.action.kind)
        self.assertEqual(set(desktop.action.params), set(web.action.params))
        self.assertEqual(desktop.action.params["x"], web.action.params["x"])
        self.assertEqual(desktop.action.params["y"], web.action.params["y"])
        self.assertEqual(desktop.action.params["space"], web.action.params["space"])

    def test_both_channels_inject_the_same_point(self):
        _, desktop_injector = self._run_seam(self._uia_channel())
        _, web_injector = self._run_seam(
            _channel([_snapshot(_element(rect=(300, 200, 400, 240)))])
        )
        desktop_moves = [event for event in desktop_injector.events if event[0] == "move"]
        web_moves = [event for event in web_injector.events if event[0] == "move"]
        self.assertEqual(desktop_moves, web_moves)

    def test_the_trace_records_which_channel_was_used(self):
        class _Sink:
            def __init__(self):
                self.traces = []

            def emit(self, trace):
                self.traces.append(trace)

        sink = _Sink()
        for locator in (
            self._uia_channel(),
            _channel([_snapshot(_element())]),
        ):
            seam = ActionExecutionSeam(
                capture_sources=[FakeCaptureSource()],
                tree=FakeControlTree(()),
                injector=FakeInputInjector(),
                locator=locator,
                trace_sink=sink,
            )
            seam.run(SeamRequest(target_description="提交订单"))
        self.assertEqual([trace.channel for trace in sink.traces], ["uia", "browser"])


class StaleElementReferenceTest(unittest.TestCase):
    """AC 3：元素引用有时效 —— 页面变了就重取快照再定位。"""

    def test_the_stale_reference_is_not_reused_after_a_page_change(self):
        channel = _channel(
            [
                _snapshot(_element(element_id="s1#0", rect=(300, 200, 400, 240))),
                _snapshot(_element(element_id="s2#0", rect=(320, 220, 420, 260))),
            ]
        )
        result = channel.locate(
            LocateRequest(target_description="提交订单", anchor=_ANCHOR)
        )
        self.assertIsNone(result.error_code)
        # 用的是**新快照**里的引用与新位置，不是过期的那一个。
        self.assertEqual(result.selected.element_id, "s2#0")
        self.assertEqual(result.landing_point, (370, 240))

    def test_a_page_refresh_does_not_report_an_unknown_error(self):
        channel = _channel([_snapshot(_element()), _snapshot()])
        result = channel.locate(
            LocateRequest(target_description="提交订单", anchor=_ANCHOR)
        )
        self.assertEqual(result.error_code, ErrorCode.RELOCATE_FAILED)
        self.assertEqual(result.failure_type, "selection")
        # 选择失败**禁止**切视觉（候选在，但定不下来）。
        self.assertFalse(result.allows_visual_fallback)


class KeyboardDegradationTest(unittest.TestCase):
    """AC 4：浏览器收不到键盘事件时退到浏览器自己的输入通道，并记下这次退化。"""

    def _typing(self, snapshots, *, accepted=True):
        injector = FakeInputInjector()
        browser_input = FakeBrowserInput(accepted=accepted)
        typing = BrowserTyping(
            executor=ActionExecutor(injector, sleeper=lambda seconds: None),
            structure=FakePageStructure(snapshots),
            browser_input=browser_input,
            sleeper=lambda seconds: None,
            attempts=2,
        )
        return typing, injector, browser_input

    def test_it_falls_back_to_the_browser_input_channel(self):
        typing, _injector, browser_input = self._typing(
            [_snapshot(_element(), focused_element_id="s1#1", focused_value="")]
        )
        result = typing.type_text("张三")
        self.assertTrue(result.ok)
        self.assertEqual(browser_input.inserted, ["张三"])

    def test_the_degradation_is_recorded(self):
        typing, _injector, _browser_input = self._typing(
            [_snapshot(_element(), focused_element_id="s1#1", focused_value="")]
        )
        result = typing.type_text("张三")
        self.assertTrue(any("输入通道退化" in note for note in result.notes))

    def test_no_fallback_when_the_browser_received_the_keys(self):
        typing, _injector, browser_input = self._typing(
            [_snapshot(_element(), focused_element_id="s1#1", focused_value="张三")]
        )
        result = typing.type_text("张三")
        self.assertTrue(result.ok)
        self.assertEqual(browser_input.inserted, [])

    def test_an_undecidable_delivery_is_reported_as_undecidable(self):
        typing, _injector, browser_input = self._typing([_snapshot(_element())])
        result = typing.type_text("张三")
        self.assertTrue(result.ok)
        self.assertEqual(browser_input.inserted, [])
        self.assertTrue(any("判不了" in note for note in result.notes))

    def test_a_late_arriving_value_is_not_treated_as_a_failure(self):
        """注入是异步到达的：第一次没看到、第二次看到了 → **不退化**。

        否则会**重复输入一遍**（真机上实测过这个坑）。
        """
        typing, _injector, browser_input = self._typing(
            [
                _snapshot(_element(), focused_element_id="s1#1", focused_value=""),
                _snapshot(_element(), focused_element_id="s1#1", focused_value="张三"),
            ]
        )
        result = typing.type_text("张三")
        self.assertTrue(result.ok)
        self.assertEqual(browser_input.inserted, [])
        self.assertFalse(any("输入通道退化" in note for note in result.notes))


class ClipboardTest(unittest.TestCase):
    """AC 5：用粘贴通道输入后剪贴板内容被恢复。

    📌 **工单 M16 起**：粘贴通道要**读一次剪贴板**（备份）→ 读剪贴板**要审批**
    （工单 M16 AC 6）。没批就**不读**、改走 Unicode 注入通道。
    本类测的是「**批了**之后」的行为 → 闸门接一个**全都批**的审批人。
    """

    def _executor(self, injector):
        from minicode.approval.fakes import AutoApprover
        from minicode.approval.gate import ApprovalGate

        return ActionExecutor(
            injector,
            sleeper=lambda seconds: None,
            approval_gate=ApprovalGate(approver=AutoApprover()),
        )

    def test_the_clipboard_is_the_same_before_and_after(self):
        injector = FakeInputInjector()
        injector.write_clipboard("用户原来的剪贴板")
        typing = BrowserTyping(
            executor=self._executor(injector),
            structure=FakePageStructure([_snapshot(_element(), focused_element_id="s1#1")]),
            browser_input=FakeBrowserInput(),
        )
        result = typing.type_text("张三")
        self.assertTrue(result.ok)
        self.assertEqual(injector.read_clipboard(), "用户原来的剪贴板")

    def test_the_paste_channel_was_used(self):
        injector = FakeInputInjector()
        typing = BrowserTyping(
            executor=self._executor(injector),
            structure=FakePageStructure([_snapshot(_element(), focused_element_id="s1#1")]),
            browser_input=FakeBrowserInput(),
        )
        typing.type_text("张三")
        self.assertIn(("press_combo", ("ctrl", "v")), injector.events)

    def test_without_the_approval_the_clipboard_is_not_read_at_all(self):
        """工单 M16 AC 6：没批 → **不读**剪贴板 → 改走 Unicode 注入通道。"""
        from minicode.approval.fakes import DenyApprover
        from minicode.approval.gate import ApprovalGate

        injector = FakeInputInjector()
        typing = BrowserTyping(
            executor=ActionExecutor(
                injector,
                sleeper=lambda seconds: None,
                approval_gate=ApprovalGate(approver=DenyApprover()),
            ),
            structure=FakePageStructure([_snapshot(_element(), focused_element_id="s1#1")]),
            browser_input=FakeBrowserInput(),
        )
        typing.type_text("张三")
        self.assertNotIn(("press_combo", ("ctrl", "v")), injector.events)
        self.assertIn(("type_unicode", "张三"), injector.events)


class CapabilityTableTest(unittest.TestCase):
    """AC 6：能力表按实测填，不出现类别级结论。"""

    def test_a_flat_structure_is_recorded_as_sparse(self):
        table = BrowserCapabilityTable(FakePageProbe([_flat_assessment()]))
        entry = table.availability(_ANCHOR)
        self.assertEqual(entry.availability, Availability.SPARSE)
        self.assertIn("没有可见文本", entry.reason)

    def test_the_entry_is_keyed_by_app_and_version(self):
        table = BrowserCapabilityTable(FakePageProbe([_flat_assessment()]))
        first = table.availability(_ANCHOR)
        other = table.availability(
            WindowAnchor(hwnd=1, app="chrome", version="99.0.0.0")
        )
        self.assertEqual(first.app, "chrome")
        self.assertEqual(other.version, "99.0.0.0")
        self.assertEqual(table.probe_count, 2)

    def test_the_same_app_and_version_is_probed_only_once(self):
        table = BrowserCapabilityTable(FakePageProbe([_flat_assessment()]))
        table.availability(_ANCHOR)
        table.availability(_ANCHOR)
        self.assertEqual(table.probe_count, 1)

    def test_a_page_without_structure_is_unavailable(self):
        from minicode.browser.capability import PageAssessment

        table = BrowserCapabilityTable(FakePageProbe([PageAssessment(has_snapshot=False)]))
        entry = table.availability(_ANCHOR)
        self.assertEqual(entry.availability, Availability.UNAVAILABLE)


class RedactionGateTest(unittest.TestCase):
    """C-15：脱敏没上线就不许把页面结构送模型（零模型调用）。"""

    def test_the_channel_is_refused_when_redaction_is_not_wired(self):
        from minicode.uia.fakes import FakeCandidatePointer

        pointer = FakeCandidatePointer()
        channel = _channel(
            [
                _snapshot(_element(name="提交"), _element(name="提交", element_id="s1#1")),
                _snapshot(_element(name="提交"), _element(name="提交", element_id="s2#1")),
            ],
            pointer=pointer,
        )
        result = channel.locate(LocateRequest(target_description="提交", anchor=_ANCHOR))
        self.assertEqual(result.error_code, ErrorCode.CHANNEL_DISABLED)
        self.assertEqual(pointer.calls, 0)
        self.assertEqual(result.model_calls, 0)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

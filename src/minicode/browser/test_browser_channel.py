"""浏览器通道的单测（工单 M13 的六条验收标准）。

只测**外部行为**：一句目标描述进去，一个选中项 + 一个物理像素落点出来；
失败时给出**哪一类**失败与**为什么**；输入时给出「送达 / 退化 / 判不了」。
不测内部实现细节。
"""

from __future__ import annotations

import unittest
from dataclasses import replace

from minicode.action.errors import ErrorCode
from minicode.action.executor import ActionExecutor
from minicode.action.fakes import FakeCaptureSource, FakeControlTree, FakeInputInjector
from minicode.action.ports import LocateRequest, WindowAnchor
from minicode.action.protocol import ActionKind
from minicode.action.seam import ActionExecutionSeam, SeamRequest
from minicode.browser.capability import BrowserCapabilityTable
from minicode.browser.channel import BrowserChannel
from minicode.browser.elements import BrowserElement, TextMeasurement
from minicode.browser.fakes import FakeBrowserInput, FakePageProbe, FakePageStructure
from minicode.browser.geometry import PageViewport
from minicode.browser.ports import PageSnapshot
from minicode.browser.textmetrics import measure_visible_text
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
    element = BrowserElement(**payload)
    if "text" not in kwargs:
        # 默认：可见文本铺满整个控件框（文本区域 = 控件框）。
        # 真实页面里两者**不一样**（扁平 DOM 的 div 是整行宽、文字只有几个字）。
        element = replace(element, text=TextMeasurement(rect=element.rect))
    return element


def _text(rect=None, reason: str = "", element_box: bool = False) -> TextMeasurement:
    """一次**文本度量**的结果（``rect`` 为 ``None`` = 判不了）。"""
    return TextMeasurement(rect=rect, reason=reason, element_box=element_box)


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

    def test_the_landing_point_is_the_visible_text_box_center_in_physical_space(self):
        """工单 M28：落点取**可见文本的实际包围盒**的中心。"""
        channel = _channel(
            [
                _snapshot(
                    _element(
                        rect=(300, 200, 400, 240),
                        text=_text((310, 210, 350, 230)),
                    )
                )
            ]
        )
        result = channel.locate(
            LocateRequest(target_description="提交订单", anchor=_ANCHOR)
        )
        self.assertEqual(result.landing_point, (330, 220))

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


class LandingOnVisibleTextTest(unittest.TestCase):
    """工单 M28：落点取**可见文本的实际区域**，不是元素矩形中心。

    * 元素**没有**可见文本（文本被裁 / 字体未加载 / 压根没有文本）→
      **判不了**，并记下原因；
    * **不许**静默回落到元素矩形中心。
    """

    def _locate(self, element: BrowserElement):
        channel = _channel([_snapshot(element)])
        return channel.locate(
            LocateRequest(target_description="提交订单", anchor=_ANCHOR)
        )

    def test_the_landing_point_is_the_text_box_center_not_the_element_rect_center(self):
        """扁平 DOM 上元素框是整行宽、文字只有几个字 → 落点必须落在文字上。"""
        result = self._locate(
            _element(rect=(300, 200, 400, 240), text=_text((310, 210, 350, 230)))
        )
        self.assertIsNone(result.error_code)
        self.assertEqual(result.landing_point, (330, 220))
        # 元素矩形的中心是 (350, 220) —— **不是**落点。
        self.assertNotEqual(result.landing_point, (350, 220))

    def test_an_element_without_visible_text_is_undecidable(self):
        result = self._locate(
            _element(text=_text(None, "元素没有可见文本"))
        )
        self.assertIsNotNone(result.error_code)
        self.assertIsNone(result.landing_point)
        # 候选在那儿，只是定不下落点 → **选择失败**（禁止切视觉）。
        self.assertEqual(result.failure_type, "selection")
        self.assertFalse(result.allows_visual_fallback)

    def test_the_reason_is_recorded(self):
        result = self._locate(
            _element(text=_text(None, "可见文本被裁掉了（可见部分为空）"))
        )
        self.assertIn("可见文本被裁掉了", result.error_message)
        self.assertIn("判不了", result.error_message)

    def test_an_element_without_a_text_node_lands_on_its_own_rect_center(self):
        """输入框这类（**没有文本子节点**）→ 落点取**它自己的矩形中心**。

        ⚠️ 这**不是**「静默回落到矩形中心」—— 元素框本身就是它的真值
        （口径 2026-10-21）。「有文本子节点但量不到」才判不了。
        """
        result = self._locate(
            _element(
                rect=(300, 200, 400, 240),
                text=_text(
                    (300, 200, 400, 240),
                    "元素没有文本子节点 → 落点取它自己的矩形中心",
                    element_box=True,
                ),
            )
        )
        self.assertIsNone(result.error_code)
        self.assertEqual(result.landing_point, (350, 220))

    def test_it_does_not_fall_back_to_the_element_rect_center(self):
        """判不了时**不许**拿元素矩形中心凑一个落点。"""
        result = self._locate(_element(text=_text(None, "元素没有可见文本")))
        self.assertIsNone(result.landing_point)
        self.assertNotEqual(result.landing_point, (350, 220))

    def test_the_error_code_is_one_of_the_existing_ones(self):
        """不新增错误码（动作协议 v1 的错误码集合不变）。"""
        result = self._locate(_element(text=_text(None, "元素没有可见文本")))
        self.assertEqual(result.error_code, ErrorCode.INVALID_GEOMETRY)

    def test_the_landing_point_comes_from_the_second_read(self):
        """重取快照后再定位 → 落点取**新快照**里的文本区域。"""
        channel = _channel(
            [
                _snapshot(
                    _element(
                        element_id="s1#0",
                        rect=(300, 200, 400, 240),
                        text=_text((310, 210, 350, 230)),
                    )
                ),
                _snapshot(
                    _element(
                        element_id="s2#0",
                        rect=(320, 220, 420, 260),
                        text=_text((330, 230, 370, 250)),
                    )
                ),
            ]
        )
        result = channel.locate(
            LocateRequest(target_description="提交订单", anchor=_ANCHOR)
        )
        self.assertEqual(result.landing_point, (350, 240))


class TextMeasurementTest(unittest.TestCase):
    """文本度量这一层：CDP 的读数 → **物理表面空间**的可见文本区域。"""

    _VIEWPORT = PageViewport(origin_physical=(100, 200), scale=1.5, css_size=(1280, 800))

    def test_the_css_rect_is_converted_to_the_physical_surface_space(self):
        measurement = measure_visible_text(
            {"status": "ok", "text": {"x": 10, "y": 20, "w": 40, "h": 10}},
            self._VIEWPORT,
        )
        self.assertTrue(measurement.ok)
        self.assertEqual(measurement.rect, (115, 230, 175, 245))

    def test_no_text_is_undecidable_with_a_reason(self):
        measurement = measure_visible_text({"status": "no-text"}, self._VIEWPORT)
        self.assertFalse(measurement.ok)
        self.assertIsNone(measurement.rect)
        self.assertIn("可见文本", measurement.reason)

    def test_a_clipped_text_is_undecidable_with_a_reason(self):
        measurement = measure_visible_text({"status": "clipped"}, self._VIEWPORT)
        self.assertFalse(measurement.ok)
        self.assertIn("裁", measurement.reason)

    def test_an_unloaded_font_is_undecidable_with_a_reason(self):
        measurement = measure_visible_text({"status": "font-loading"}, self._VIEWPORT)
        self.assertFalse(measurement.ok)
        self.assertIn("字体", measurement.reason)

    def test_an_element_without_a_text_node_lands_on_its_own_box(self):
        """⚠️ **元素框本身就是它的真值**（口径 2026-10-21）。

        输入框 / 按钮这类：屏上的字来自 value / placeholder / 默认标签，
        **不是文本节点** → 文本度量层给的是**元素盒子的中心**，
        **不是**「静默回落到矩形中心」（那个是判不了时的事）。
        """
        measurement = measure_visible_text(
            {"status": "no-text-node", "box": {"x": 10, "y": 20, "w": 40, "h": 10}},
            self._VIEWPORT,
        )
        self.assertTrue(measurement.ok)
        self.assertTrue(measurement.element_box)
        self.assertEqual(measurement.rect, (115, 230, 175, 245))
        self.assertIn("文本子节点", measurement.reason)

    def test_the_desktop_channel_does_not_use_this_layer(self):
        """AC 3：文本度量是**浏览器通道独有**的一层 —— 桌面通道的落点仍取控件框中心。"""
        from minicode.uia.capability import CapabilityTable
        from minicode.uia.channel import UiaChannel
        from minicode.uia.elements import UiaElement
        from minicode.uia.fakes import FakeCapabilityProbe, FakeUiaQueryPort, ScriptedRead

        table = CapabilityTable(FakeCapabilityProbe(), sleeper=lambda seconds: None)
        channel = UiaChannel(
            query=FakeUiaQueryPort(
                [
                    ScriptedRead(
                        elements=(UiaElement(name="提交订单", rect=(300, 200, 400, 240)),)
                    )
                ]
            ),
            capability=table,
        )
        result = channel.locate(
            LocateRequest(target_description="提交订单", anchor=_ANCHOR)
        )
        self.assertEqual(result.landing_point, (350, 220))


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

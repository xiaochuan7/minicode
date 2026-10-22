"""T7：XAML **root-walk**（桌面根遍历 + PID 过滤）与**选中项读值**的单测。

两条实现路径都不动冻结目录（``src/minicode/exec/``），只用它的**公开接口**：

* ``uia.UIA().root()``（桌面根）、``Element.process_id``、
  ``controller.build_condition()``（无参即 TrueCondition）；
* ``Element.get_pattern()``（取模式的 COM 指针）。

⚠️ **单条件**：冻结原型的 ``build_condition`` 两个条件并存会走
``CreateAndCondition``，**在本机崩** access violation → root-walk 只用
**TrueCondition**，**先按 PID 过滤，再进 Python 侧的名字匹配**。
"""

from __future__ import annotations

import unittest
from unittest import mock

from minicode.action.ports import WindowAnchor
from minicode.uia import windows
from minicode.uia.errors import UnconditionalQueryError
from minicode.uia.ports import UiaFindRequest


class _FakeRect:
    def __init__(self, rect=(0, 0, 10, 10)):
        self.left, self.top, self.right, self.bottom = rect


class _FakeElement:
    """假的 UIA 元素（COM 那一层的最小替身）。"""

    def __init__(
        self,
        name: str = "",
        *,
        pid: int = 0,
        control_type: int = 50000,
        automation_id: str = "",
        class_name: str = "Button",
        rect=(0, 0, 10, 10),
    ):
        self._name = name
        self._pid = pid
        self._control_type = control_type
        self._automation_id = automation_id
        self._class_name = class_name
        self._rect = rect
        self.released = False

    # -- 冻结原型 ``Element`` 的读法 -------------------------------------
    @property
    def name(self) -> str:
        return self._name

    @property
    def process_id(self) -> int:
        return self._pid

    @property
    def control_type(self) -> int:
        return self._control_type

    @property
    def control_type_name(self) -> str:
        return "Button"

    @property
    def automation_id(self) -> str:
        return self._automation_id

    @property
    def class_name(self) -> str:
        return self._class_name

    @property
    def bounding_rect(self) -> _FakeRect:
        return _FakeRect(self._rect)

    @property
    def native_window_handle(self) -> int:
        return 0

    def release(self) -> None:
        self.released = True

    def find_all(self, condition, scope):
        return []


class _FakeRoot:
    """假的根元素（``find_all`` 返回固定的元素表）。"""

    def __init__(self, elements: list):
        self.elements = elements
        self.released = False

    def find_all(self, condition, scope):
        return list(self.elements)

    def release(self) -> None:
        self.released = True


class _FakeController:
    """假的 ``uia.UIA``（只实现 root-walk 用得到的那几个方法）。"""

    def __init__(self, root_elements: list | None = None, handle_elements: list | None = None):
        self._root_elements = root_elements or []
        self._handle_elements = handle_elements or []
        self.conditions: list = []
        self.root_calls = 0

    def root(self):
        self.root_calls += 1
        return _FakeRoot(self._root_elements)

    def element_from_handle(self, hwnd):
        return _FakeRoot(self._handle_elements)

    def build_condition(self, **kwargs):
        self.conditions.append(kwargs)
        return 1234  # 假的指针（``_release`` 在测试里被换掉）


class PatternIdTest(unittest.TestCase):
    """**别照抄错值**：模式 id 与属性 id 是两套号。"""

    def test_the_pattern_ids_are_the_documented_ones(self):
        # 10006 是 Grid —— Selection 是 10001。
        self.assertEqual(windows.UIA_SELECTION_PATTERN_ID, 10001)
        self.assertEqual(windows.UIA_TEXT_PATTERN_ID, 10014)
        self.assertEqual(windows.UIA_LEGACY_IACCESSIBLE_PATTERN_ID, 10018)

    def test_the_vtable_indices_follow_the_header_order(self):
        """下标来自 ``UIAutomationClient.h`` 的**方法声明顺序**（IUnknown 占 0~2）。"""
        self.assertEqual(windows._SELECTION_GET_SELECTION_INDEX, 3)
        self.assertEqual(windows._ELEMENT_ARRAY_LENGTH_INDEX, 3)
        self.assertEqual(windows._ELEMENT_ARRAY_GET_ELEMENT_INDEX, 4)
        self.assertEqual(windows._LEGACY_GET_VALUE_INDEX, 8)
        self.assertEqual(windows._TEXT_GET_DOCUMENT_RANGE_INDEX, 7)
        self.assertEqual(windows._TEXT_RANGE_GET_TEXT_INDEX, 12)
        self.assertEqual(windows._ELEMENT_GET_NAME_INDEX, 23)


class ValueFallbackChainTest(unittest.TestCase):
    """取值兜底链：选中项 Name → Value 属性 → LegacyIAccessible → TextPattern。"""

    def test_the_chain_order_is_the_documented_one(self):
        calls: list[str] = []

        def _reader(tag: str, value: str):
            def _read(element) -> str:
                calls.append(tag)
                return value

            return _read

        with (
            mock.patch.object(
                windows, "_read_selection_name", _reader("selection", "")
            ),
            mock.patch.object(
                windows, "_read_value_property", _reader("value_property", "")
            ),
            mock.patch.object(
                windows, "_read_legacy_value", _reader("legacy", "")
            ),
            mock.patch.object(windows, "_read_text_value", _reader("text", "")),
        ):
            self.assertEqual(windows._read_value(object()), "")
        self.assertEqual(calls, ["selection", "value_property", "legacy", "text"])

    def test_the_first_non_empty_wins(self):
        with (
            mock.patch.object(windows, "_read_selection_name", lambda element: "150 ％ (推荐)"),
            mock.patch.object(windows, "_read_value_property", lambda element: "别的"),
            mock.patch.object(windows, "_read_legacy_value", lambda element: "别的"),
            mock.patch.object(windows, "_read_text_value", lambda element: "别的"),
        ):
            self.assertEqual(windows._read_value(object()), "150 ％ (推荐)")

    def test_a_failing_reader_does_not_stop_the_chain(self):
        def _boom(element) -> str:
            raise OSError("COM 挂了")

        with (
            mock.patch.object(windows, "_read_selection_name", _boom),
            mock.patch.object(windows, "_read_value_property", lambda element: ""),
            mock.patch.object(windows, "_read_legacy_value", lambda element: "旧值"),
            mock.patch.object(windows, "_read_text_value", lambda element: "别的"),
        ):
            self.assertEqual(windows._read_value(object()), "旧值")


class RootWalkTest(unittest.TestCase):
    """桌面根遍历：**单条件 + 按 PID 过滤**。"""

    def test_only_the_elements_of_that_pid_are_kept(self):
        mine = _FakeElement("缩放", pid=42)
        theirs = _FakeElement("别的东西", pid=7)
        controller = _FakeController(root_elements=[mine, theirs])
        with (
            mock.patch("minicode.exec.uia.UIA", return_value=controller),
            mock.patch.object(windows, "_release", lambda ptr: None),
        ):
            found = windows._root_walk_by_pid(42)
        self.assertEqual([element.name for element in found], ["缩放"])
        self.assertTrue(theirs.released)
        self.assertFalse(mine.released)

    def test_the_condition_is_a_single_true_condition(self):
        """⚠️ **只用单条件**（两个条件并存会走 CreateAndCondition → 本机崩）。"""
        controller = _FakeController(root_elements=[])
        with (
            mock.patch("minicode.exec.uia.UIA", return_value=controller),
            mock.patch.object(windows, "_release", lambda ptr: None),
        ):
            windows._root_walk_by_pid(42)
        self.assertEqual(controller.conditions, [{}])


class QueryPortFallbackTest(unittest.TestCase):
    """定点查询端口：窗口那一遍查不到 → **桌面根遍历兜底**。"""

    def _request(self, name: str = "缩放") -> UiaFindRequest:
        return UiaFindRequest(
            anchor=WindowAnchor(hwnd=1234, app="ApplicationFrameHost.exe", version="1"),
            name=name,
        )

    def test_the_root_walk_is_used_when_the_window_tree_is_empty(self):
        controller = _FakeController(handle_elements=[])
        walked = _FakeElement("缩放", pid=42)
        with (
            mock.patch("minicode.exec.uia.UIA", return_value=controller),
            mock.patch.object(windows, "_release", lambda ptr: None),
            mock.patch.object(windows, "_process_id", lambda hwnd: 42),
            mock.patch.object(windows, "_root_walk_by_pid", lambda pid: [walked]),
            mock.patch.object(
                windows.WindowsUiaQueryPort, "_to_element", lambda self, e, i, t="": e
            ),
        ):
            port = windows.WindowsUiaQueryPort()
            result = port.find(self._request())
        self.assertEqual([element.name for element in result.elements], ["缩放"])

    def test_the_window_tree_is_enough_when_it_has_the_target(self):
        controller = _FakeController(handle_elements=[_FakeElement("缩放", pid=42)])
        with (
            mock.patch("minicode.exec.uia.UIA", return_value=controller),
            mock.patch.object(windows, "_release", lambda ptr: None),
            mock.patch.object(windows, "_process_id", lambda hwnd: 42),
            mock.patch.object(
                windows,
                "_root_walk_by_pid",
                mock.Mock(side_effect=AssertionError("不该遍历桌面根")),
            ),
            mock.patch.object(
                windows.WindowsUiaQueryPort, "_to_element", lambda self, e, i, t="": e
            ),
        ):
            port = windows.WindowsUiaQueryPort()
            result = port.find(self._request())
        self.assertEqual([element.name for element in result.elements], ["缩放"])

    def test_an_unconditional_query_is_still_rejected(self):
        """C-09 的禁令不变：root-walk 是**有条件请求的兜底**，不是全树 dump 的入口。"""
        port = windows.WindowsUiaQueryPort()
        with self.assertRaises(UnconditionalQueryError):
            port.find(UiaFindRequest(anchor=WindowAnchor(hwnd=1, app="a", version="1")))

    def test_the_name_filter_is_reused_on_the_root_walk(self):
        """桌面根那一遍拿回来的是**整个进程的元素** → 名字过滤在 Python 侧做。"""
        elements = [
            _FakeElement("缩放", pid=42),
            _FakeElement("系统", pid=42),
            _FakeElement("缩放", pid=42, rect=(0, 0, 5, 5)),
        ]
        request = UiaFindRequest(
            anchor=WindowAnchor(hwnd=1, app="a", version="1"), name="缩放"
        )
        port = windows.WindowsUiaQueryPort()
        matched, unmatched = port._filter(elements, request)
        self.assertEqual([element.name for element in matched], ["缩放", "缩放"])
        # 没对上的那个被释放掉了（不攥着整棵桌面树）。
        self.assertTrue(elements[1].released)


class CapabilityFallbackTest(unittest.TestCase):
    """能力探测：窗口那一遍没判成「完全可用」→ 桌面根兜底。"""

    def test_the_root_walk_is_not_run_when_the_window_tree_is_usable(self):
        probe = windows.WindowsCapabilityProbe()
        with mock.patch.object(
            windows.WindowsCapabilityProbe,
            "_assess",
            lambda self, anchor, extra=(): windows.TreeAssessment(
                has_tree=True, usable_controls=12, name_collisions=0
            ),
        ), mock.patch.object(
            windows, "_root_walk_by_pid", mock.Mock(side_effect=AssertionError("不该遍历"))
        ):
            assessment = probe.assess(
                WindowAnchor(hwnd=1, app="a", version="1")
            )
        self.assertEqual(assessment.usable_controls, 12)

    def test_the_better_of_the_two_readings_wins(self):
        window = windows.TreeAssessment(has_tree=True, usable_controls=3, name_collisions=2)
        walked = windows.TreeAssessment(has_tree=True, usable_controls=40, name_collisions=0)
        self.assertIs(windows._better_assessment(window, walked), walked)
        self.assertIs(windows._better_assessment(walked, window), walked)


if __name__ == "__main__":
    unittest.main()

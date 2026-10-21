"""真机分布回归台（:mod:`minicode.ground.bench`）的**目标收集**口径。

⚠️ 这一层量的是「两级 grounding 的落点误差分布」——
**分母（测量集）是这套结论的全部依据**：某一类控件读不出来却被静默丢掉，
量到的分布就悄悄变成了「少了一类控件」的分布，报告里还看不出来。
"""

from __future__ import annotations

import unittest

from minicode.ground.bench import collect_targets


class _Element:
    """UIA 元素的最小形状（名字 + 物理像素矩形）。"""

    def __init__(self, name: str, rect: tuple[int, int, int, int]):
        self.name = name
        self.rect = rect


class _Result:
    def __init__(self, elements: list[_Element]):
        self.elements = elements


class _Port:
    """假 UIA 端口：按控件类型给读数；``broken`` 里的类型**读不出来**。"""

    def __init__(
        self,
        elements_by_type: dict[str, list[_Element]] | None = None,
        broken: tuple[str, ...] = (),
    ):
        self._elements_by_type = elements_by_type or {}
        self._broken = set(broken)

    def find(self, request):
        if request.control_type in self._broken:
            raise RuntimeError(f"{request.control_type} 读不出来")
        return _Result(self._elements_by_type.get(request.control_type, []))


class CollectTargetsTests(unittest.TestCase):
    def test_a_partial_read_failure_is_reported_not_swallowed(self):
        """**局部失败要被报出来** —— 不报的话分母会静默变小。"""
        port = _Port(
            {"Button": [_Element("确定", (0, 0, 40, 20))]},
            broken=("Text",),
        )
        targets, failures = collect_targets(
            port, object(), control_types=("Button", "Text")
        )
        self.assertEqual([target.name for target in targets], ["确定"])
        self.assertEqual(failures, ["Text"])

    def test_every_read_failing_raises_instead_of_reporting_no_targets(self):
        """**树读不出来 ≠ 界面上没有控件** → 抛，不当成「没有目标」。"""
        port = _Port(broken=("Button", "Text"))
        with self.assertRaises(RuntimeError):
            collect_targets(port, object(), control_types=("Button", "Text"))

    def test_an_empty_but_readable_tree_is_no_targets(self):
        """树**读出来了**、只是没有控件 → 空列表 + 没有失败类型。"""
        port = _Port()
        targets, failures = collect_targets(port, object(), control_types=("Button",))
        self.assertEqual(targets, [])
        self.assertEqual(failures, [])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

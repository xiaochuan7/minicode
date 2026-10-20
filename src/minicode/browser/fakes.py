"""浏览器通道的**假实现**。

有了它们，浏览器通道可以在**不装浏览器、不碰真桌面**的条件下被观测
（单测只测外部行为）。

* :class:`FakePageStructure` —— 页面结构快照（按脚本给一串快照）；
* :class:`FakePageProbe` —— 能力探测读数；
* :class:`FakeBrowserInput` —— 浏览器自己的输入通道（记下插过什么）。
"""

from __future__ import annotations

from .capability import PageAssessment
from .ports import PageSnapshot

__all__ = ["FakePageStructure", "FakePageProbe", "FakeBrowserInput"]


class FakePageStructure:
    """假页面结构端口：按脚本给出快照序列。

    快照给完就重复最后一个（这样「重取一次快照」拿到的还是它）。
    """

    def __init__(self, snapshots=()):
        self.snapshots = list(snapshots)
        self.calls = 0

    def snapshot(self) -> PageSnapshot:
        if not self.snapshots:
            self.calls += 1
            return PageSnapshot()
        index = min(self.calls, len(self.snapshots) - 1)
        self.calls += 1
        return self.snapshots[index]


class FakePageProbe:
    """假能力探测：按脚本给出页面评估读数。"""

    def __init__(self, assessments=()):
        self.assessments = list(assessments)
        self.calls = 0

    def assess(self, anchor) -> PageAssessment:
        if not self.assessments:
            self.calls += 1
            return PageAssessment()
        index = min(self.calls, len(self.assessments) - 1)
        self.calls += 1
        return self.assessments[index]


class FakeBrowserInput:
    """假浏览器输入通道：记下插过什么，可以设定「不接受」。"""

    def __init__(self, accepted: bool = True):
        self.accepted = bool(accepted)
        self.inserted: list[str] = []

    def insert_text(self, text: str) -> bool:
        self.inserted.append(text)
        return self.accepted

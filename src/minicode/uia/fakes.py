"""UIA 通道端口的假实现。

有了它们，整条 UIA 通道可以在**不碰真桌面、不需要凭据、不产生费用**的条件下被观测。

假实现只替换缝以下的外部依赖，**不改变端口的形状**。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from minicode.action.ports import WindowAnchor

from .elements import UiaElement, rename_elements
from .errors import UiaQueryTimeout, UnconditionalQueryError
from .ports import (
    PointerRequest,
    PointerResult,
    TreeAssessment,
    UiaFindRequest,
    UiaFindResult,
    UnblockOutcome,
)

__all__ = [
    "ScriptedRead",
    "FakeUiaQueryPort",
    "FakeCandidatePointer",
    "FakeUnblocker",
    "FakeCapabilityProbe",
]


@dataclass(frozen=True)
class ScriptedRead:
    """一次脚本化的读树。"""

    elements: tuple[UiaElement, ...] = ()
    #: 这一读超时了。
    timed_out: bool = False
    #: 这一读里树有没有节点（0 节点 = 静默退化）。
    nodes: bool = True


class FakeUiaQueryPort:
    """假控件树：按脚本逐次返回观测。

    * **无条件全树查询直接拒绝** —— 禁止全树 dump（架构决策备忘 §2.6 C-09）；
    * 每次观测的元素 id **重新生成**（上一次观测的 id 不解析）；
    * ``calls`` 是读树次数（执行前重读 → ≥2）。
    """

    def __init__(self, reads: Sequence[ScriptedRead] = ()):
        self.reads: list[ScriptedRead] = list(reads)
        self.calls = 0
        self.requests: list[UiaFindRequest] = []
        self.has_nodes_calls = 0
        self._last: ScriptedRead | None = None

    def find(self, request: UiaFindRequest) -> UiaFindResult:
        if not request.has_condition:
            raise UnconditionalQueryError("无条件全树查询被拒绝（只做定点查询）")
        self.calls += 1
        self.requests.append(request)
        if self.reads:
            read = self.reads.pop(0)
        elif self._last is not None:
            # 脚本用完了：树没变（除非显式脚本了变化）。
            read = self._last
        else:
            read = ScriptedRead()
        self._last = read
        if read.timed_out:
            raise UiaQueryTimeout(f"查询超时（第 {self.calls} 次读树）")
        elements = rename_elements(read.elements, f"obs{self.calls}")
        return UiaFindResult(elements=elements, elapsed_ms=1.0)

    def has_nodes(self, anchor: WindowAnchor) -> bool:
        self.has_nodes_calls += 1
        if self._last is not None:
            return self._last.nodes
        if self.reads:
            return self.reads[0].nodes
        return True


@dataclass
class FakeCandidatePointer:
    """假消歧指认：按脚本回答，并记录调用次数。

    ``chosen_ids`` 默认空 —— 模拟「模型没给出唯一指认」。
    """

    chosen_ids: tuple[str, ...] = ()
    requests: list[PointerRequest] = field(default_factory=list)
    calls: int = 0

    def point(self, request: PointerRequest) -> PointerResult:
        self.calls += 1
        self.requests.append(request)
        return PointerResult(chosen_ids=self.chosen_ids)


@dataclass
class FakeUnblocker:
    """假解门控：按脚本给结果。"""

    outcomes: tuple[UnblockOutcome, ...] = (UnblockOutcome.UNBLOCKED,)
    calls: int = 0

    def unblock(self, anchor: WindowAnchor) -> UnblockOutcome:
        outcome = self.outcomes[min(self.calls, len(self.outcomes) - 1)]
        self.calls += 1
        return outcome


@dataclass
class FakeCapabilityProbe:
    """假可用性探测：按脚本给读数。"""

    assessments: tuple[TreeAssessment, ...] = ()
    calls: int = 0

    def assess(self, anchor: WindowAnchor) -> TreeAssessment:
        assessment = (
            self.assessments[min(self.calls, len(self.assessments) - 1)]
            if self.assessments
            else TreeAssessment(has_tree=True)
        )
        self.calls += 1
        return assessment

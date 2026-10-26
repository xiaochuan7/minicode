"""UIA 通道的端口。

端口规定**形状**（要什么、返回什么），注入点决定接哪一个实现
（真机实现还是假实现）—— 换了注入点，**端口的形状不许变**。

四个端口：

* :class:`UiaQueryPort` —— **定点查询**（动作路径；带属性条件，无条件全树查询一律拒绝）；
* :class:`CandidatePointer` —— **消歧指认**（模型只在候选集内指认，不产生新坐标）；
* :class:`TreeUnblocker` —— **解门控**（树被门控时先试着解开）；
* :class:`CapabilityProbePort` —— **可用性探测**（递归子窗口 + 看 ClassName）。

> ⚠️ **「只做定点查询」这条禁令只管动作路径**（``UiaQueryPort``）。
> 能力探测按「递归子窗口 + 看 UIA ClassName」的判定姿势**必须**走一遍应用自己的
> 窗口树（不遍历就看不到类名），它每个「应用 + 版本」只跑一次，且不参与动作路径。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol, runtime_checkable

from minicode.action.ports import (
    CandidatePointer,
    PointerRequest,
    PointerResult,
    WindowAnchor,
)

from .elements import UiaElement

__all__ = [
    "UiaFindRequest",
    "UiaFindResult",
    "UiaQueryPort",
    "PointerRequest",
    "PointerResult",
    "CandidatePointer",
    "UnblockOutcome",
    "TreeUnblocker",
    "TreeAssessment",
    "CapabilityProbePort",
]


@dataclass(frozen=True)
class UiaFindRequest:
    """一次定点查询。

    **必须带属性条件**（名字 / 自动化标识 / 控件类型至少有一个）：
    无条件全树查询一律拒绝（禁止全树 dump）。

    ``name`` 的语义是**包含**：元素名出现在条件串里，或条件串出现在元素名里
    （自然语言描述「任务栏的开始按钮」要能命中控件「开始」）。
    """

    anchor: WindowAnchor
    name: str | None = None
    automation_id: str | None = None
    control_type: str | None = None

    @property
    def has_condition(self) -> bool:
        """这次查询带属性条件吗。"""
        return any((self.name, self.automation_id, self.control_type))


@dataclass(frozen=True)
class UiaFindResult:
    """一次定点查询的结果。"""

    elements: tuple[UiaElement, ...] = ()
    #: 查询超时了（通道级失败）。
    timed_out: bool = False
    #: 本次查询的耗时（毫秒）。
    elapsed_ms: float = 0.0
    #: **条件查询找到、却没与名字条件对上**的元素个数（工单 M9 AC 2）。
    #:
    #: 它把两种情形分开：``0`` = 树里确实没有这个目标（**覆盖失败**，
    #: 允许切视觉）；``> 0`` = 候选在那儿但名字对不上（**选择失败**，
    #: 禁止切视觉）。缺省 ``0`` —— 旧实现不报这个数，按「没找到别的候选」算。
    unmatched_count: int = 0


@runtime_checkable
class UiaQueryPort(Protocol):
    """控件树通道（UIA）。"""

    def find(self, request: UiaFindRequest) -> UiaFindResult:
        """定点查询。**必须带属性条件**；无条件查询直接拒绝。

        超时抛 :class:`~minicode.uia.errors.UiaQueryTimeout`（通道级失败）。
        """
        ...

    def has_nodes(self, anchor: WindowAnchor) -> bool:
        """这棵树里有没有节点。

        用「取第一个节点」实现，**不是**全树遍历：0 节点 = 静默退化
        （C-09：0 节点必须被识别成「通道不可用」，不是「目标不存在」）。
        """
        ...


@dataclass(frozen=True)
class UnblockOutcome(StrEnum):
    """解门控的结果。"""

    #: 没有可解的门（这个应用压根没有 UIA 树）——「从未可用」。
    NOT_APPLICABLE = "not-applicable"
    #: 解开了。
    UNBLOCKED = "unblocked"
    #: 解不开（树还是空的）。
    FAILED = "failed"


@runtime_checkable
class TreeUnblocker(Protocol):
    """树被门控时的「已知手段」（激活窗口之类）。"""

    def unblock(self, anchor: WindowAnchor) -> UnblockOutcome:
        ...


@dataclass(frozen=True)
class TreeAssessment:
    """一次可用性探测的读数。

    ⚠️ **不数候选数**：判定姿势是
    **递归子窗口 + 看 UIA ClassName**。裸外壳只有 3 个边框按钮 ≠ 不可用；
    有 1000 个元素也不等于可用。

    ``usable_controls`` 走的是冻结原型 ``exec/uia.py`` 的 :func:`is_usable`
    判据（有名字或自动化标识 + 类名非空 + 控件框有面积 + 在屏幕上 +
    属于可交互控件类型）—— **裸外壳那 3 个边框按钮过不了这一关**。
    """

    #: 树里有没有节点。
    has_tree: bool = False
    #: 见到的 UIA ClassName（去重）。
    class_names: tuple[str, ...] = ()
    #: 其中属于**可交互**控件的 ClassName（去重）。
    interactive_class_names: tuple[str, ...] = ()
    #: **真能用**的控件数（过 :func:`is_usable` 判据的）。
    usable_controls: int = 0
    #: 元素带不带 AutomationId（Chromium 扁平树不带）。
    has_automation_id: bool = False
    #: 同名元素撞车的对数（同名会撞车 = 覆盖不全）。
    name_collisions: int = 0
    #: 递归到的子窗口数。
    child_windows: int = 0


@runtime_checkable
class CapabilityProbePort(Protocol):
    """可用性探测。"""

    def assess(self, anchor: WindowAnchor) -> TreeAssessment:
        ...

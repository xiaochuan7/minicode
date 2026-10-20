"""**端到端工单的形状**（工单 M14）。

一张工单 = **若干步**，每一步都带一条**结构断言**；工单本身再带若干条
**验收条款**（= 工单验收标准里的一条）。

两条不变量（在这里钉住，不靠运行时的自觉）：

1. **每一步都必须有结构断言** —— 没有期望的步骤一律拒绝建出来
   （工单 M14：「不允许只用『画面变了』当通过判据」）。
2. **每张工单至少一步** —— 空工单没有意义。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from minicode.action.protocol import ActionKind
from minicode.verification.expect import Expectation

from .runner import TaskEvidence

__all__ = ["Step", "Clause", "Task"]


@dataclass(frozen=True)
class Step:
    """工单里的**一步**。"""

    #: 步号（如 ``W-1.2``）。
    id: str
    #: 人读的一句话（做什么）。
    description: str
    #: 目标描述（给定位通道用）。
    target: str = ""
    #: 动作种类（默认点击）。
    action_kind: ActionKind = ActionKind.CLICK
    #: 动作参数（滚动行数、放大区域…）。
    params: Mapping[str, Any] = field(default_factory=dict)
    #: **结构断言**（这一步之后应当成立、可被确定性验证的状态）。
    #: 没有它 = 只剩「画面变了」→ **不许建出这样的步**。
    expectation: Expectation | None = None
    #: 控件类型提示（给定位通道缩小范围用）。
    control_type: str = ""
    #: **真值**：目标控件中心（物理像素）。给了才能算落点偏差。
    truth: tuple[int, int] | None = None
    #: **真值从树里取**时的控件名（跑之前按名字查一次控件框，取它的中心）。
    #: 比写死像素更稳（控件挪了位也不失效）。
    truth_element: str = ""
    #: 真值控件的类型提示（同名控件有好几个时用它挑）。
    truth_control_type: str = ""
    #: 这一步操作**哪个窗口**（``settings`` / ``taskbar``）。
    window: str = "settings"
    #: 这一步是不是要**人工代确认**（改了系统设置那种）。
    human_confirm: bool = False
    #: 这一步是不是**点在上一步给的落点**上（放大那一类：放大的答案就是点击位置）。
    use_previous_landing: bool = False
    #: 补充说明。
    note: str = ""

    def __post_init__(self) -> None:
        if self.expectation is None:
            raise ValueError(
                f"步骤 {self.id} 没有结构断言："
                "不允许只用「画面变了」当通过判据（工单 M14 验收标准）"
            )


@dataclass(frozen=True)
class Clause:
    """工单的一条**验收条款**。

    ``check`` 收到判条款时能看到的全部证据（步骤结果 + 任务级上下文），
    返回 ``(成立吗, 人读的细节)``。
    """

    id: str
    description: str
    check: Callable[[TaskEvidence], tuple[bool, str]]


@dataclass(frozen=True)
class Task:
    """端到端工单清单里的一张。"""

    id: str
    title: str
    app: str
    steps: tuple[Step, ...] = ()
    clauses: tuple[Clause, ...] = ()
    #: 这是不是一张「人工代确认」的任务（**不自动执行**）。
    human_confirm: bool = False
    #: 静态已知的跳过原因（环境不可达）。非空 = 这张票直接记 ``skip``。
    skip_reason: str = ""

    def __post_init__(self) -> None:
        if not self.steps:
            raise ValueError(f"工单 {self.id} 一步都没有")
        for step in self.steps:
            if step.expectation is None:
                raise ValueError(
                    f"工单 {self.id} 的步骤 {step.id} 没有结构断言："
                    "不允许只用「画面变了」当通过判据（工单 M14 验收标准）"
                )

"""**护栏运行状态**（护栏 4 预算 + 护栏 5 熔断）：一次真机运行的账本。

预算（护栏 4）与熔断（护栏 5）都是**跨步骤**的状态 —— 单看一步不知道超没超，
所以要有一本账。这本账只在这里记（唯一真值来源），判定只读账本：

* :class:`BudgetLedger` —— 步数 / 时长 / 模型调用三个维度，任一超限即停；
* :class:`FailureFuse` —— **同一目标**连续失败达到阈值（默认 2）才熔断；
  中间穿插一次别的目标成功/失败都会打断「连续」（那是不同的目标）。
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field

from .policy import GuardBudget

__all__ = ["BudgetLedger", "BudgetExceeded", "FailureFuse"]


class BudgetExceeded(Exception):
    """预算用尽（带超的是哪个维度，进错误信息）。"""

    def __init__(self, dimension: str, detail: str):
        super().__init__(f"预算用尽（{dimension}）：{detail}")
        self.dimension = dimension
        self.detail = detail


@dataclass
class BudgetLedger:
    """预算账本（每步记一笔，判定读账本）。"""

    #: 预算配置（步数 / 秒 / 模型调用）。
    budget: GuardBudget
    #: 已用步数。
    steps: int = 0
    #: 已累计模型调用次数（从追踪记录 ``model_calls`` 累加）。
    model_calls: int = 0
    #: 运行开始时刻（``clock()`` 口径）。
    started_at: float = 0.0
    #: 时钟函数（缺省 ``time.monotonic``；单测注入假钟）。
    clock: Callable[[], float] | None = None

    def __post_init__(self) -> None:
        if self.clock is None:
            self.clock = time.monotonic
        if self.started_at == 0.0:
            self.started_at = self.clock()

    def elapsed_s(self) -> float:
        """已运行时长（秒）。"""
        return self.clock() - self.started_at

    def record_step(self, model_calls: int = 0) -> None:
        """记一步（含这一步的模型调用次数）。"""
        self.steps += 1
        self.model_calls += model_calls

    def check(self) -> BudgetExceeded | None:
        """预算还够吗。够 → ``None``；不够 → 带维度的 :class:`BudgetExceeded`。"""
        if self.steps >= self.budget.max_steps:
            return BudgetExceeded(
                "steps", f"已用 {self.steps} 步，上限 {self.budget.max_steps}"
            )
        elapsed = self.elapsed_s()
        if elapsed >= self.budget.max_seconds:
            return BudgetExceeded(
                "seconds",
                f"已运行 {elapsed:.1f}s，上限 {self.budget.max_seconds:.1f}s",
            )
        if self.model_calls >= self.budget.max_model_calls:
            return BudgetExceeded(
                "model_calls",
                f"已调用模型 {self.model_calls} 次，上限 {self.budget.max_model_calls}",
            )
        return None

    def to_dict(self) -> dict:
        """账本快照（进运行摘要）。"""
        return {
            "steps": self.steps,
            "model_calls": self.model_calls,
            "elapsed_s": round(self.elapsed_s(), 3),
            "max_steps": self.budget.max_steps,
            "max_seconds": self.budget.max_seconds,
            "max_model_calls": self.budget.max_model_calls,
        }


@dataclass
class FailureFuse:
    """连续失败熔断（护栏 5）：同一目标连续失败达到阈值即停。"""

    #: 熔断阈值（默认 2：同一目标连续 2 次失败即停）。
    threshold: int = 2
    #: 当前连续失败的目标（换目标就清零重计）。
    _target: str | None = field(default=None, repr=False)
    #: 当前连续失败次数。
    consecutive: int = 0
    #: 是否已熔断（熔断后**本次运行不再恢复**——恢复了也不知道它修没修好）。
    blown: bool = False

    def record(self, target: str, ok: bool) -> None:
        """记一步结果（成功也记——它打断「连续」）。"""
        if ok:
            self._target = None
            self.consecutive = 0
            return
        if self._target == target:
            self.consecutive += 1
        else:
            self._target = target
            self.consecutive = 1
        if self.consecutive >= self.threshold:
            self.blown = True

    def check(self) -> str | None:
        """熔断了吗。没熔 → ``None``；熔了 → 人读原因。"""
        if self.blown:
            return (
                f"同一目标「{self._target}」连续失败 {self.consecutive} 次"
                f"（阈值 {self.threshold}），熔断停止"
            )
        return None

    def to_dict(self) -> dict:
        """账本快照（进运行摘要）。"""
        return {
            "consecutive_failures": self.consecutive,
            "last_failed_target": self._target,
            "blown": self.blown,
            "threshold": self.threshold,
        }

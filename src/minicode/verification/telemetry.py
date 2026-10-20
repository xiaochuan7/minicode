"""**三段耗时**与**三类失败**的统计（工单 M11 AC 4 / AC 5）。

架构决策备忘 §2.5 ③：这两样必须**拆开埋点** —— 否则任务掉了不知道该改哪一层。

* **三段耗时**：定位 / 决策 / 执行+校验。**单步**有值（追踪记录与动作记录里），
  **整任务**能读出三段各自的**占比**（谁慢一目了然）。
* **三类失败**：定位失败 / 决策失败 / 执行失败。**各自计数、互不混淆**，
  一次任务跑完能给出三类各自的**次数与占比**（不是笼统的「失败 N 次」）。

⚠️ 这一套是**埋点轴**（`CONTEXT.md` 的「三类失败」词条），
**别与** ``minicode.action.errors`` 的**失败类型**（覆盖 / 选择 / 执行 / 通道 / 决策）
混用 —— 那是**定位阶段的分类轴**，两套正交。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from minicode.action.records import FAILURE_FIELDS

__all__ = [
    "STAGE_FIELDS",
    "TelemetryReport",
    "TelemetryAccumulator",
]

#: 三段耗时的字段名（与动作记录 ``timings_ms`` 的键同名）。
STAGE_FIELDS = ("locate", "decision", "execute_verify")


@dataclass(frozen=True)
class TelemetryReport:
    """一个任务的三段耗时与三类失败。"""

    #: 统计了多少步。
    steps: int = 0
    #: 三段耗时合计（毫秒）。
    stages_ms: Mapping[str, float] = field(default_factory=dict)
    #: 三段耗时各自的**占比**（0~1；三段合计为 0 时全是 0）。
    stage_share: Mapping[str, float] = field(default_factory=dict)
    #: 三类失败各自的**次数**。
    failures: Mapping[str, int] = field(default_factory=dict)
    #: 三类失败各自的**占比**（占全部失败的比例；一次失败都没有时全是 0）。
    failure_share: Mapping[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """落盘形状。"""
        return {
            "steps": self.steps,
            "stages_ms": dict(self.stages_ms),
            "stage_share": dict(self.stage_share),
            "failures": dict(self.failures),
            "failure_share": dict(self.failure_share),
        }


class TelemetryAccumulator:
    """把一步一步的埋点累加成**整任务**的报告。"""

    def __init__(self) -> None:
        self._stages: dict[str, float] = {name: 0.0 for name in STAGE_FIELDS}
        self._failures: dict[str, int] = {name: 0 for name in FAILURE_FIELDS}
        self._steps = 0

    def add_step(
        self,
        *,
        locate_ms: float = 0.0,
        decision_ms: float = 0.0,
        execute_verify_ms: float = 0.0,
        failures: Mapping[str, str | None] | None = None,
    ) -> None:
        """记一步（三段耗时 + 三类失败）。"""
        self._steps += 1
        self._stages["locate"] += float(locate_ms)
        self._stages["decision"] += float(decision_ms)
        self._stages["execute_verify"] += float(execute_verify_ms)
        for name in FAILURE_FIELDS:
            code = (failures or {}).get(name)
            if code:
                # 一次失败只记一次（三个字段互斥：一个字段一个错误码）。
                self._failures[name] += 1

    def add_trace(self, trace: Any) -> None:
        """记一步**追踪记录**。

        ⚠️ 这里**只取定位段与执行+校验段**：**决策段由决策层报**
        （:meth:`add_decision`）。动作追踪记录里的 ``decision_ms`` 是
        **同一轮**的决策耗时（记在轮内第一个动作上），
        在这里再计一次就会**重复计数**。
        """
        self.add_step(
            locate_ms=getattr(trace, "locate_ms", 0.0),
            execute_verify_ms=getattr(trace, "execute_verify_ms", 0.0),
            failures=getattr(trace, "failures", None),
        )

    def add_decision(
        self, decision_ms: float, failure: str | None = None
    ) -> None:
        """记一次**决策**（决策层的模型调用）。

        ``failure``
            决策层的失败码（如 ``program_rejected``）；给了就记进「决策失败」。
        """
        self._stages["decision"] += float(decision_ms)
        if failure:
            self._failures["decision"] += 1

    def report(self) -> TelemetryReport:
        """整任务报告：三段耗时与占比 + 三类失败的次数与占比。"""
        total_ms = sum(self._stages.values())
        total_failures = sum(self._failures.values())
        return TelemetryReport(
            steps=self._steps,
            stages_ms=dict(self._stages),
            stage_share={
                name: (value / total_ms if total_ms > 0 else 0.0)
                for name, value in self._stages.items()
            },
            failures=dict(self._failures),
            failure_share={
                name: (count / total_failures if total_failures > 0 else 0.0)
                for name, count in self._failures.items()
            },
        )

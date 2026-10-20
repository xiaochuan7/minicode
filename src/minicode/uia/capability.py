"""**能力表**（工单 M3 AC 9~AC 12）。

能力表按「**应用 + 版本**」记录每个应用 UIA 的可用性，三态：

* **完全可用**（USABLE）—— 递归子窗口见得到**可交互的 UIA ClassName**，且没有同名撞车；
* **部分覆盖**（SPARSE）—— 树在，但**覆盖不全**（扁平树、无 AutomationId、同名会撞车）；
* **不可用**（UNAVAILABLE）—— 树读不出来（0 节点 / 门控解不开）。

判定姿势：**递归子窗口 + 看 UIA ClassName**，**不数候选数**
（架构决策备忘 §2.6 **C-08**：裸外壳只有 3 个边框按钮 ≠ 不可用；
有 1000 个元素 ≠ 可用）。键里有版本，所以「Chromium 一律失效」「UWP 一律可用」
这类**类别级结论**根本写不进去。

「不可用」必须能区分两种原因：

* ``gated-unlock-failed`` —— 树**被门控**，按已知手段试着解开仍失败；
* ``no-tree`` —— **从未可用**（压根没有 UIA 树）。

表是**静态缓存**的：同一次启动内每个应用只探测一次（不每步重判）。
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum

from minicode.action.ports import WindowAnchor

from .ports import CapabilityProbePort, TreeAssessment, TreeUnblocker, UnblockOutcome

__all__ = ["Availability", "CapabilityEntry", "CapabilityTable"]


class Availability(StrEnum):
    """能力表的三态（**不可压成二元**）。"""

    #: 完全可用。
    USABLE = "usable"
    #: 部分覆盖：树存在但覆盖不全。
    SPARSE = "sparse"
    #: 不可用。
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class CapabilityEntry:
    """能力表的一条记录。"""

    app: str
    version: str
    availability: Availability
    #: 为什么是这个结论（**不可用**时区分「解开失败」与「从未可用」）。
    reason: str = ""
    #: 探测时见到的 UIA ClassName（去重）。
    class_names: tuple[str, ...] = ()


class CapabilityTable:
    """按「应用 + 版本」缓存 UIA 可用性。"""

    def __init__(
        self,
        probe: CapabilityProbePort,
        *,
        unblocker: TreeUnblocker | None = None,
        max_retries: int = 3,
        backoff_s: float = 0.1,
        sleeper: Callable[[float], None] = time.sleep,
    ):
        self._probe = probe
        self._unblocker = unblocker
        self._max_retries = int(max_retries)
        self._backoff_s = float(backoff_s)
        self._sleeper = sleeper
        self._entries: dict[tuple[str, str], CapabilityEntry] = {}
        self._probes = 0

    @property
    def probe_count(self) -> int:
        """**表级**探测次数：同一个应用在同一次启动内只探测一次。"""
        return self._probes

    def entries(self) -> tuple[CapabilityEntry, ...]:
        """表里的全部记录。"""
        return tuple(self._entries.values())

    def availability(self, anchor: WindowAnchor) -> CapabilityEntry:
        """这个应用（+版本）的 UIA 可用性。**首次调用才探测**，之后走缓存。"""
        key = (anchor.app, anchor.version)
        cached = self._entries.get(key)
        if cached is not None:
            return cached
        self._probes += 1
        entry = self._probe_entry(anchor)
        self._entries[key] = entry
        return entry

    # -- 内部 -------------------------------------------------------------

    def _probe_entry(self, anchor: WindowAnchor) -> CapabilityEntry:
        assessment = self._probe.assess(anchor)
        if assessment.has_tree:
            return self._verdict(anchor, assessment)
        return self._retry_until_decided(anchor, assessment)

    def _retry_until_decided(
        self, anchor: WindowAnchor, assessment: TreeAssessment
    ) -> CapabilityEntry:
        """树未就绪 → **先激活窗口并做指数退避重试**，再判定不可用。

        「第一次读不到」**不等于**「不支持」（工单 M3 AC 9）。
        """
        outcome = UnblockOutcome.NOT_APPLICABLE
        for attempt in range(self._max_retries):
            self._sleeper(self._backoff_s * (2**attempt))
            if self._unblocker is not None:
                outcome = self._unblocker.unblock(anchor)
            assessment = self._probe.assess(anchor)
            if assessment.has_tree:
                return self._verdict(anchor, assessment)
        reason = (
            "gated-unlock-failed"
            if outcome is UnblockOutcome.FAILED
            else "no-tree"
        )
        return CapabilityEntry(
            app=anchor.app,
            version=anchor.version,
            availability=Availability.UNAVAILABLE,
            reason=reason,
        )

    def _verdict(self, anchor: WindowAnchor, assessment: TreeAssessment) -> CapabilityEntry:
        """按**类名**与撞车情况判定（不数候选数）。

        「裸外壳只有 3 个边框按钮」判为**部分覆盖**而不是可用：
        边框按钮过不了 :func:`is_usable` 那一关（类名/名字/控件框不成立），
        于是 ``usable_controls`` 为 0 —— 有树 ≠ 覆盖得住。
        """
        if assessment.usable_controls and not assessment.name_collisions:
            availability = Availability.USABLE
            reason = "递归子窗口见到可交互的 UIA ClassName，且没有同名撞车"
        else:
            availability = Availability.SPARSE
            missing: list[str] = []
            if not assessment.usable_controls:
                missing.append("没有通过可用性判据的控件（裸外壳形态）")
            if not assessment.has_automation_id:
                missing.append("元素无 AutomationId")
            if assessment.name_collisions:
                missing.append(f"同名会撞车 {assessment.name_collisions} 处")
            reason = "树在但覆盖不全：" + "、".join(missing)
        return CapabilityEntry(
            app=anchor.app,
            version=anchor.version,
            availability=availability,
            reason=reason,
            class_names=assessment.class_names,
        )

"""minicode.reflection —— **反思与停止条件**（工单 M12）。

这一包回答一个问题：**任务什么时候该停，停之前该做什么，交出去的东西够不够人看懂。**

四块：

* :mod:`minicode.reflection.stop` —— **停止条件**、四类**卡死检测器**
  （帧无变化 / 重复动作 / 进度停滞 / 轮次硬上限）与**熔断**（强制换策略）。
* :mod:`minicode.reflection.retry` —— **重试策略**：只重试可逆动作，
  退避 200 / 500 / 1200 ms（上限 3 次），**每次重试前重新感知**。
* :mod:`minicode.reflection.handoff` —— **交接产物**（人能看懂发生了什么）。
* :mod:`minicode.reflection.gate` —— **危险动作的放行判据**
  （**不含模型自评置信度**；审批与 dry-run 是后票 M16 的活）。

> **词汇**：本包说的「停止条件」「卡死检测器」「熔断」「重试」「退避」
> 「可逆动作 / 不可逆动作」「危险动作」「交接给人」「交接产物」
> 「目标级期望」「换策略」按根目录 ``CONTEXT.md`` 的定义。
>
> **本包新造的词都已经进了 ``CONTEXT.md``**（换策略 / 目标级期望 /
> 交接产物 / 退避）—— 不留「只在代码里、文档里没有」的词。
"""

from __future__ import annotations

__all__ = [
    "gate",
    "handoff",
    "keywords",
    "retry",
    "stop",
]

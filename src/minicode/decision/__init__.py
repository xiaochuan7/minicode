"""minicode.decision —— 决策层（L3）：**代码执行 harness**（工单 M10）。

决策层的产出形态是**一段动作程序**，不是逐条动作（ADR-0005）：
模型针对当前观察写一段程序（可含 if/for、可调用定位与动作接口），
harness 在**受控环境**里执行，把标准输出、异常与新截图回灌给模型，
多步任务一次跑完，不用每步问一次用户。

对外入口：

* :mod:`minicode.decision.harness` —— 决策层的主循环与停止条件。
* :mod:`minicode.decision.api` —— 动作程序能看到的接口（只有协议暴露的动作）。
* :mod:`minicode.decision.context` —— 上下文窗口（完整图只留最近 2~4 张）。
* :mod:`minicode.decision.prompt` —— 规划步与反思步的提示词。

> **分工**：决策层**不在单测缝内**（工单 M10 AC 7）—— 它的行为由**端到端评测**
> 承担（``python -m minicode.decision``）。受控执行环境的契约
> （能不能 import / 能不能写文件）在 :mod:`minicode.action.program` 里有单测。
>
> **词汇**：「决策层」「动作程序」「停止条件」「思考预算（effort）」按根目录
> ``CONTEXT.md`` 的定义。
"""

from __future__ import annotations

__all__ = [
    "api",
    "context",
    "harness",
    "prompt",
]

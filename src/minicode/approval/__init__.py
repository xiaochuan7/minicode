"""minicode.approval —— **危险动作审批**（工单 M16）。

这一包回答一个问题：**这件事要不要人点头，点了才做。**

五块：

* :mod:`minicode.approval.catalog` —— **危险动作清单**（十一类）与判定
  （命中即生效，fail-closed）。
* :mod:`minicode.approval.request` —— **审批请求**（绑定「意图 + 目标」）
  与**审批结论**（绑定这一次动作）。
* :mod:`minicode.approval.ports` —— 端口：**谁来批** / **当前前台窗口**
  / **哪些窗口敏感**。
* :mod:`minicode.approval.gate` —— **审批闸门**（唯一一处决定批不批；
  **置信度不作放行条件**）。
* :mod:`minicode.approval.console` / :mod:`minicode.approval.windows` ——
  命令行里问人 / 真机读前台窗口。

> **词汇**：本包说的「危险动作」「审批」「dry-run」「意图」「交接给人」
> 这几个词。
"""

from __future__ import annotations

__all__ = [
    "catalog",
    "console",
    "fakes",
    "gate",
    "ports",
    "request",
    "windows",
]

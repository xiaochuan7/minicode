"""minicode.redact —— 控件树脱敏器（M2）。

**送进模型之前的最后一道闸**：控件的类型、名字、位置照常保留，
草稿、密码、凭据一律不外发。

对外入口：

* :mod:`minicode.redact.config` —— 模型上下文的开关与**启用期校验**。
* :mod:`minicode.redact.redactor` —— 把树变成模型侧文本的**唯一出口**。
* :mod:`minicode.redact.patterns` —— 五类敏感模式与两个固定占位符。
* :mod:`minicode.redact.report` —— 脱敏计数。

> 词汇：**「模型侧文本」「模型上下文」「缝级验证」这三个词条 `CONTEXT.md` 尚未收录**
> —— 补词条属于领域文档的活儿（domain-modeling），这里按
> `action/ports.py` 的既有做法就地标注。三个词的出处都是工单 M2 正文
> （「脱敏后的模型侧文本」「送进模型上下文」「缝级验证」），
> 不是本轮自造的语言。
"""

from __future__ import annotations

__all__ = [
    "config",
    "errors",
    "patterns",
    "redactor",
    "report",
]

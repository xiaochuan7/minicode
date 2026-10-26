"""minicode.redact —— 脱敏：敏感模式与它的两个应用点。

**两个应用点**：

* **送模型之前**（M2）：控件的类型、名字、位置照常保留，草稿、密码、凭据
  一律不外发 —— 送进模型之前的最后一道闸；
* **落盘之前**（M17）：动作日志里不出现密钥形态的明文。

两层**不重叠**，但**规则是同一套**（本包）。

对外入口：

* :mod:`minicode.redact.config` —— 模型上下文的开关与**启用期校验**。
* :mod:`minicode.redact.redactor` —— 把树变成模型侧文本的**唯一出口**。
* :mod:`minicode.redact.patterns` —— 五类敏感模式与两个固定占位符。
* :mod:`minicode.redact.log` —— **落盘前的脱敏**（日志那一侧，工单 M17）。
* :mod:`minicode.redact.report` —— 脱敏计数。

> 词汇：**「模型侧文本」「模型上下文」「缝级验证」**三个词的出处都是
> 工单 M2 正文（「脱敏后的模型侧文本」「送进模型上下文」「缝级验证」），
> 按 `action/ports.py` 的既有做法就地标注，不是本轮自造的语言。
"""

from __future__ import annotations

__all__ = [
    "config",
    "errors",
    "log",
    "patterns",
    "redactor",
    "report",
]

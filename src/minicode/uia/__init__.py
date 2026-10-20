"""minicode.uia —— UIA 通道（结构感知通道，工单 M3）。

对外入口：

* :mod:`minicode.uia.ports` —— 通道的端口（定点查询 / 消歧指认 / 解门控 / 能力探测）。
* :mod:`minicode.uia.elements` —— 元素与**控件框的读法**（按 4 个 int 读）。
* :mod:`minicode.uia.capability` —— **能力表**（三态 + 「应用 + 版本」+ 静态缓存）。
* :mod:`minicode.uia.channel` —— 通道本体：描述 → 唯一定位 → 执行前重读 → 落点。
* :mod:`minicode.uia.windows` —— 真机适配器（惰性接冻结原型的 UIA）。
* :mod:`minicode.uia.fakes` —— 端口的假实现。

> **词汇**：本包说的「定位」「选中项」「唯一性拒绝」「消歧」「能力表」
> 「覆盖失败」「选择失败」「通道级失败」按根目录 ``CONTEXT.md`` 的定义。
>
> **``CONTEXT.md`` 尚未收录、本包自己引入的词**（补词条属于领域文档的活儿，
> 见 ``docs/agents/domain.md``）：
>
> * **定点查询** —— 带属性条件的 UIA 查询（禁止全树 dump）；出处是工单 M3 正文与
>   架构决策备忘 §2.6 C-09。
> * **重定位** —— 执行前重读一遍树、认出「还是那个控件」；出处是工单 M3 正文
>   （「执行前系统会重新读一遍树再定位一次」）。
> * **元素 id** —— 控件候选在**一次观测**里的标识，每次观测重新生成；
>   出处是工单 M3 正文（AC 7）。
> * **门控** —— ⚠️ **与 ``CONTEXT.md`` 的「门控（gate）」同词不同义**：
>   ``CONTEXT.md`` 指的是**精定位门控**（是否开启 crop 精定位）；
>   本包指的是「控件树被门控、需要先解开才读得到」（工单 M3 AC 10）。
>   本包内一律写「**树被门控**」，别与精定位门控混。
"""

from __future__ import annotations

__all__ = [
    "capability",
    "channel",
    "elements",
    "errors",
    "fakes",
    "ports",
]

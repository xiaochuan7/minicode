"""**策略文档的渲染**（工单 M20 AC 3）。

文档**由实现生成** —— 不是手写的第二份规则：

* 条款来自 :mod:`minicode.privacy.policy` 的默认策略；
* 条款里的脱敏边界**引用脱敏器自己的常量**（同一处规则，工单 M20 AC 4）；
* 每条条款都带一个**实现位置**（``模块:符号``），
  :func:`resolve_implementation` 能把它解析回实现里的那个对象 ——
  文档里写的每一条都能在实现里找到对应行为。

因此文档**不会与实现漂移**：改了实现里的规则，重跑
``python -m minicode.privacy`` 就能把文档更新到一致；单测还会钉住
「仓库里那份文档 = 现在渲染出来的那份」。
"""

from __future__ import annotations

import importlib

from .policy import PolicyClause, PrivacyPolicy, default_policy

__all__ = ["render_policy_document", "resolve_implementation", "DOC_HEADER"]


#: 文档抬头。**不写生成时间** —— 写进去每次生成都不同，diff 全是噪音。
DOC_HEADER = """# 隐私策略（工单 M20）

> ⚠️ **本文件由 `python -m minicode.privacy` 从实现里生成 —— 不要手改。**
> 每一行都指向实现里的一个位置（末列），可以逐条对照（工单 M20 AC 3）。
> 改了实现里的规则，重跑那个命令就能把本文更新到一致。
>
> 涉及**脱敏**的条款引用的是脱敏器自己描述的那份边界
> （`minicode.redact.redactor:redaction_boundary`）—— **同一处规则，不写两套**
> （工单 M20 AC 4）。
"""


def resolve_implementation(reference: str):
    """把「模块:符号」解析成实现里的那个对象。

    解析不到就**报错**（不静默放过）—— 文档指向一个不存在的行为，
    比没有文档更坏。
    """
    module_name, _, qualname = reference.partition(":")
    if not module_name or not qualname:
        raise LookupError(f"实现位置写法不对（要「模块:符号」）：{reference!r}")
    try:
        target = importlib.import_module(module_name)
    except ImportError as exc:
        raise LookupError(f"实现位置指向的模块不存在：{reference!r}") from exc
    for part in qualname.split("."):
        try:
            target = getattr(target, part)
        except AttributeError as exc:
            raise LookupError(f"实现位置指向的符号不存在：{reference!r}") from exc
    return target


def _clause_table(clauses: tuple[PolicyClause, ...]) -> str:
    """条款表（人读的一栏 + 实现位置一栏）。"""
    lines = ["| 条款 | 说明 | 实现位置 |", "|---|---|---|"]
    for clause in clauses:
        lines.append(
            f"| **{clause.title}** | {clause.detail} | `{clause.implementation}` |"
        )
    return "\n".join(lines)


def render_policy_document(policy: PrivacyPolicy | None = None) -> str:
    """渲染策略文档（markdown）。"""
    current = policy if policy is not None else default_policy()
    blacklist = current.sensitive_windows
    sections = [
        DOC_HEADER.rstrip(),
        "",
        "## 1. 送进模型的内容",
        "",
        _clause_table(current.sent_to_model),
        "",
        "## 2. 不送的内容",
        "",
        _clause_table(current.withheld),
        "",
        "## 3. 落盘的内容",
        "",
        _clause_table(current.persisted),
        "",
        "## 4. 敏感窗口黑名单（发送前）",
        "",
        "* **标题命中敏感词表**（命中即拉黑）："
        + "、".join(f"`{keyword}`" for keyword in blacklist.title_keywords)
        + "。",
        "* **对话框类名**（精确匹配，不做前缀猜）："
        + "、".join(f"`{name}`" for name in blacklist.window_classes)
        + "。",
        "* **取值来源**：" + blacklist.source() + "。",
        "* 被拉黑的窗口**根本不许拍、不许发**：抓帧之前就被拦下，"
        "画面不进模型上下文，动作记录里记一笔「该窗口被跳过」。",
        "",
        "### 4.1 两层闸门：黑名单先行、审批二次确认",
        "",
        _clause_table(current.gate_layers),
        "",
        "## 5. 剪贴板",
        "",
        _clause_table(current.clipboard),
        "",
    ]
    return "\n".join(sections)

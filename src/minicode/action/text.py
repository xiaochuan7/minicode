"""文本匹配的**唯一一处**规则。

「控件名 / 可见文本」与「目标描述」是否对得上，规则只有一份：
桌面通道（UIA 的控件名）与浏览器通道（页面的可见文本）都用它 ——
这样同一句目标描述在两个通道里挑候选的口径一致（工单 M13 AC 2：
与桌面通道同语义）。

语义是**包含**：控件名出现在条件串里，或条件串出现在控件名里
（自然语言描述「任务栏的开始按钮」要能命中控件「开始」）。
"""

from __future__ import annotations

__all__ = ["name_matches"]


def name_matches(element_name: str, condition: str | None) -> bool:
    """控件名 / 可见文本与名字条件是否对得上（**包含**语义）。

    条件为空时一律为真（没有条件就没有筛选）。
    """
    if not condition:
        return True
    target = condition.strip().lower()
    actual = (element_name or "").strip().lower()
    if not actual:
        return False
    if actual == target:
        return True
    return actual in target or target in actual

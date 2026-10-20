"""**危险动作的放行判据**（工单 M12 AC 8 / AC 9）。

**模型自评置信度不得作为危险动作的放行条件。** 工单给的理由是实测事实：

* 存在**高置信度的错误**；
* **答对时的置信度均值反而低于答错时** —— 绝对值不可当把握度，
  **排序**可用。

因此本模块的判据里**没有任何置信度输入**：
:class:`ActionContext` 里可以带模型自评的置信度（那是上下文），
但 :meth:`DangerGate.evaluate` **不读它**。
单测 ``reflection/test_reflection.py`` 直接扫这段源码，
断言判据里**不出现**置信度字样 —— 这条红线由机器守着，不靠自觉。

⚠️ **边界（工单 M12 AC 9）**：**危险动作审批**与 **dry-run** 属于
**后置的安全与审计工单**（M16）。本模块**只判「要不要人工确认」**，
**不假装已做审批**：它不给"放行"的许可，只说"这件事得人来"。

**词表只有一份**（:mod:`minicode.reflection.keywords`）——
「不可逆」（不许重试）与「危险」（要人工确认）工单 M12 点名的词相同，
两份拷贝必然各说各话。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .keywords import IRREVERSIBLE_KEYWORDS, IRREVERSIBLE_KEYWORDS_SOURCE

__all__ = [
    "DANGEROUS_KEYWORDS",
    "DANGEROUS_KEYWORDS_SOURCE",
    "ActionContext",
    "GateDecision",
    "DangerGate",
]

#: **危险动作词表**（命中 → 要人工确认）。
#:
#: ⚠️ 与**不可逆词表**是**同一份**（见 :mod:`minicode.reflection.keywords`）：
#: 工单 M12 点名的三类（发消息 / 支付 / 删除）两者皆是。
#: M16 落地后**危险**那一侧要按「意图 + 目标」重新界定 —— 那时再拆。
DANGEROUS_KEYWORDS = IRREVERSIBLE_KEYWORDS

#: 危险动作词表的**取值来源**。
DANGEROUS_KEYWORDS_SOURCE = IRREVERSIBLE_KEYWORDS_SOURCE


@dataclass(frozen=True)
class ActionContext:
    """动作的上下文。

    ⚠️ ``model_confidence`` 是**模型自评的置信度** —— 它可以放在这里
    （那是上下文的一部分），但**放行判据不读它**
    （工单 M12 AC 8：绝对值不可当把握度，排序可用）。
    """

    model_confidence: float | None = None
    note: str = ""


@dataclass(frozen=True)
class GateDecision:
    """放行判据的结论。"""

    #: 要不要人工确认。
    requires_human: bool
    #: 动作的类别：``"dangerous"``（危险）/ ``"ordinary"``（普通）。
    action_class: str
    #: 人读的理由。
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "requires_human": self.requires_human,
            "action_class": self.action_class,
            "reason": self.reason,
        }


class DangerGate:
    """判「这个动作要不要人工确认」。

    ⚠️ **本单只判、不批**：审批与 dry-run 是 M16 的活（工单 M12 AC 9）。
    """

    def evaluate(
        self,
        *,
        target_description: str = "",
        intent: str = "",
        context: ActionContext | None = None,
    ) -> GateDecision:
        """这个动作**要不要人工确认**。

        判据只有一条：**目标描述或意图命中危险动作词表** → 要人工确认。

        ⚠️ ``context``（可能带着模型自评的置信度）**不参与判据** ——
        这一句是工单 M12 AC 8 的红线，单测扫源码把住它。
        """
        text = f"{target_description} {intent}"
        for keyword in DANGEROUS_KEYWORDS:
            if keyword in text:
                return GateDecision(
                    requires_human=True,
                    action_class="dangerous",
                    reason=(
                        f"命中危险动作词「{keyword}」—— 要人工确认"
                        f"（{DANGEROUS_KEYWORDS_SOURCE}）"
                    ),
                )
        return GateDecision(
            requires_human=False,
            action_class="ordinary",
            reason="未命中危险动作词表（普通动作）",
        )

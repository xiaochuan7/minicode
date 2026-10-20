"""**期望**（expect）：动作之后应当成立、可被确定性验证的状态。

工单 M11 AC 1。词条按 ``CONTEXT.md`` 的「期望（expect）」：

    动作之后应当成立、可被确定性验证的状态。

⚠️ **期望是「状态」，但工单的字面是「状态变化 / 文本出现」** ——
本轮按工单的**变化**口径实现（见下），同时保留 `CONTEXT.md` 那句「状态」的落点：
期望断言的是「动作之后 X 现在是这个状态」，**并且**这次动作**促成了**它
（动作之前它不是这个状态）。

⚠️ **别把「期望」说成「后置条件」** —— `CONTEXT.md` 的「期望」词条把
「结果、后置条件」列为 avoid。

三种形态（每种形态由**能判它的手段**给判定，见 :mod:`minicode.verification.layer`）：

======================  ==========================================  ====================
形态                    含义                                        能判它的手段
======================  ==========================================  ====================
``state_change``       控件的**可用状态**变了（可用 ↔ 不可用）        可访问性树
``text_appears``       某段文本**出现**                             文本比对
``text_disappears``    某段文本**消失**                             文本比对
======================  ==========================================  ====================

⚠️ **像素差分不判具体期望**（「像素变化 ≠ 成功」）—— 它只判「界面有没有变」，
那是**默认校验**（没有期望时）用的通用条件。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

__all__ = ["ExpectationKind", "Expectation", "Verdict", "parse_expectation"]


class ExpectationKind(StrEnum):
    """期望的三种形态。"""

    #: 控件的可用状态变了（期望 ``enabled`` 那个状态**成立**）。
    STATE_CHANGE = "state_change"
    #: 某段文本出现。
    TEXT_APPEARS = "text_appears"
    #: 某段文本消失。
    TEXT_DISAPPEARS = "text_disappears"


class Verdict(StrEnum):
    """一个手段对某个期望的判定。"""

    #: 成立。
    MET = "met"
    #: 不成立。
    NOT_MET = "not_met"
    #: **判不了**（这个手段对这件事给不出结论）。
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class Expectation:
    """一条可判定的期望。

    * ``kind`` —— 期望的形态（见 :class:`ExpectationKind`）；
    * ``target`` —— 控件名或文本（判定时按**名字包含**匹配，规则与 UIA 通道同一份）；
    * ``enabled`` —— 只对 ``state_change`` 有意义：期望的可用状态
      （``False`` = 期望它**不可用**，如「保存按钮变成灰色」）。
    """

    kind: ExpectationKind
    target: str
    enabled: bool | None = None

    def __post_init__(self) -> None:
        if not self.target.strip():
            raise ValueError("期望必须有目标（控件名或文本）")
        if self.kind is ExpectationKind.STATE_CHANGE and self.enabled is None:
            raise ValueError("state_change 期望必须说清期望的可用状态（enabled）")

    def describe(self) -> str:
        """人读的一句话（进断言结果与动作日志）。"""
        if self.kind is ExpectationKind.STATE_CHANGE:
            want = "不可用" if self.enabled is False else "可用"
            return f"「{self.target}」{want}"
        if self.kind is ExpectationKind.TEXT_APPEARS:
            return f"文本「{self.target}」出现"
        return f"文本「{self.target}」消失"

    def to_dict(self) -> dict[str, object]:
        """落盘形状。"""
        return {
            "kind": self.kind.value,
            "target": self.target,
            "enabled": self.enabled,
            "description": self.describe(),
        }


# ---------------------------------------------------------------------------
# 命令行写法（**唯一一处** —— 两个命令行工具共用同一套口径）
# ---------------------------------------------------------------------------

#: ``state:`` 第三段里表示「**可用**」的写法（中英文都认）。
_ENABLED_WORDS = ("可用", "enabled", "true", "on")

#: ``state:`` 第三段里表示「**不可用**」的写法。
_DISABLED_WORDS = ("不可用", "disabled", "false", "off")

#: ``text:`` 第三段里表示「**消失**」的写法。
_GONE_WORDS = ("消失", "disappear", "gone", "absent")


def parse_expectation(text: str) -> Expectation | None:
    """把命令行上的期望写法解析成 :class:`Expectation`（**唯一一处**）。

    写法：

    * ``state:<控件名>[:可用|不可用]`` —— 那个控件**变成**该状态
      （第三段缺省是「**不可用**」，与工单 M11 AC 1 的例子一致）；
    * ``text:<文本>[:消失]`` —— 那段文本**出现**（第三段写「消失」就是**消失**）。

    空串 → ``None``（没有期望）。
    写法看不懂 → 抛 :class:`ValueError`（**不猜**）。
    """
    raw = (text or "").strip()
    if not raw:
        return None
    parts = [part.strip() for part in raw.split(":", 2)]
    head = parts[0].lower()
    if head == "state" and len(parts) >= 2:
        want = (parts[2] if len(parts) > 2 else "不可用").lower()
        if want in _ENABLED_WORDS:
            enabled = True
        elif want in _DISABLED_WORDS:
            enabled = False
        else:
            raise ValueError(
                f"state 的第三段只能是「可用」或「不可用」，收到 {parts[2]!r}"
            )
        return Expectation(ExpectationKind.STATE_CHANGE, parts[1], enabled=enabled)
    if head == "text" and len(parts) >= 2:
        gone = len(parts) > 2 and parts[2].lower() in _GONE_WORDS
        return Expectation(
            ExpectationKind.TEXT_DISAPPEARS if gone else ExpectationKind.TEXT_APPEARS,
            parts[1],
        )
    raise ValueError(
        f"看不懂的期望写法：{raw!r}（例：state:保存:不可用 / text:已保存）"
    )

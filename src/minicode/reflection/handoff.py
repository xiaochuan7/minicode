"""**交接给人**：停下动作，把轨迹交给人（工单 M12 AC 7）。

**交接给人** —— 遇到无法安全自动处理的场景时
停止动作并把轨迹交给人。

交出去的东西要**人能看懂发生了什么**，因此固定包含四样（工单 M12 AC 7）：

1. **当前窗口标题** —— 人在哪个窗口里；
2. **最后一步动作** —— 停在哪一步；
3. **已发坐标** —— 已经往哪儿点过（物理表面空间）；
4. **已执行动作清单** —— 按顺序做过什么。

⚠️ **轨迹里没有界面正文**：控件的值与正文不落盘
（与动作记录同一条规矩）。
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

__all__ = [
    "HANDOFF_MARKER",
    "HandoffArtifact",
    "build_handoff",
]

#: 「已交接给人」这句话（**日志里有一条这样的记录** —— 工单 M12 AC 6）。
HANDOFF_MARKER = "已交接给人"


@dataclass(frozen=True)
class HandoffArtifact:
    """交出去的轨迹（**人能看懂**）。"""

    #: 停止原因（:mod:`minicode.reflection.stop` 里的那几种）。
    reason: str
    #: 停止原因的人读说法。
    reason_text: str = ""
    #: 当前窗口标题（读不到就是空串 —— 不假装）。
    window_title: str = ""
    #: 最后一步动作（人读）。
    last_action: str = ""
    #: 已发坐标（物理表面空间，按发送顺序）。
    sent_points: tuple[tuple[int, int], ...] = ()
    #: 已执行动作清单（按顺序）。
    executed_actions: tuple[str, ...] = ()
    #: 换策略的记录（人读）。
    strategy_changes: tuple[str, ...] = ()
    #: **接口调用记录**（定位 / 输入 / 请求人工……）——
    #: 让「人」看清最后到底在干什么（定位失败的那些尝试也在里面）。
    api_calls: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        """落盘形状。"""
        return {
            "reason": self.reason,
            "reason_text": self.reason_text,
            "window_title": self.window_title,
            "last_action": self.last_action,
            "sent_points": [list(point) for point in self.sent_points],
            "executed_actions": list(self.executed_actions),
            "strategy_changes": list(self.strategy_changes),
            "api_calls": list(self.api_calls),
        }

    def to_text(self) -> str:
        """人读的版本（直接贴给人看）。"""
        lines = [
            f"{HANDOFF_MARKER}：{self.reason_text or self.reason}",
            f"停止原因：{self.reason}",
            f"当前窗口标题：{self.window_title or '（读不到）'}",
            f"最后一步动作：{self.last_action or '（没有动作）'}",
            "已发坐标（物理表面空间）："
            + (
                "、".join(f"({x}, {y})" for x, y in self.sent_points)
                if self.sent_points
                else "（无）"
            ),
            "已执行动作清单："
            + ("；".join(self.executed_actions) if self.executed_actions else "（无）"),
        ]
        if self.api_calls:
            lines.append("接口调用记录：")
            lines.extend(f"- {item}" for item in self.api_calls)
        if self.strategy_changes:
            lines.append("换策略记录：")
            lines.extend(f"- {item}" for item in self.strategy_changes)
        return "\n".join(lines)


def _render_call(call: Any) -> str:
    """一条接口调用的人读说法（``find(开始) → 失败:ambiguous_target``）。"""
    if isinstance(call, str):
        return call
    kind = call.get("kind", "")
    target = call.get("target", "")
    if call.get("ok"):
        return f"{kind}({target}) → 成功"
    error = call.get("error")
    return f"{kind}({target}) → 失败" + (f":{error}" if error else "")


def build_handoff(
    signals: Iterable[Any],
    *,
    reason: str,
    reason_text: str = "",
    strategy_changes: Sequence[Any] = (),
    api_calls: Sequence[Any] = (),
    window_title: str = "",
) -> HandoffArtifact:
    """从走过的每一步的信号拼出**交接产物**。

    ``strategy_changes`` 可以给 :class:`~minicode.reflection.stop.StrategyChange`
    （自动取人读说法），也可以直接给字符串。

    ``window_title`` —— 调用方**已经知道**当前在哪个窗口时用它
    （比如「模态弹窗」那条路径：停下来的时候人正对着那个弹窗）。
    缺省 = 从走过的步骤里取最后一个有标题的。
    """
    steps = list(signals)
    titles = [step.window_title for step in steps if step.window_title]
    #: **已发坐标** = **真正下发过**的那些（步骤成功 → 坐标确实发出去了）。
    #: 被拒的动作（提权零下发、注入被拦）**不算**「已发」——
    #: 把没发出去的坐标写进「已发坐标」是在骗人。
    points = tuple(
        tuple(step.landing_point)
        for step in steps
        if step.landing_point and step.ok
    )
    actions = tuple(
        step.intent or step.target_description or step.action_kind
        for step in steps
    )
    calls = tuple(_render_call(call) for call in api_calls)
    changes: list[str] = []
    for change in strategy_changes:
        describe = getattr(change, "describe", None)
        changes.append(describe() if callable(describe) else str(change))
    # **最后一步动作**：优先取**最后一次接口调用**（那才是「最后在干什么」——
    # 定位失败的那些尝试也算），没有就退回最后一条动作。
    last_action = calls[-1] if calls else ""
    if not last_action:
        last = steps[-1] if steps else None
        last_action = (
            (last.intent or last.target_description or last.action_kind)
            if last is not None
            else ""
        )
    return HandoffArtifact(
        reason=reason,
        reason_text=reason_text,
        window_title=window_title or (titles[-1] if titles else ""),
        last_action=last_action,
        sent_points=points,
        executed_actions=actions,
        strategy_changes=tuple(changes),
        api_calls=calls,
    )

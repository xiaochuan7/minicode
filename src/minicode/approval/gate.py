"""**审批闸门**（工单 M16 AC 1 / AC 2 / AC 3 / AC 5）。

这是**唯一**一处决定「这个动作要不要人工批准、批不批」的地方：
动作下发路径（动作执行缝 / 决策层接口面 / 动作执行器）都问它。

三条硬规矩：

1. **命中即要审批**（AC 1）—— 危险动作清单里的每一类，执行前都产生审批请求；
   **没有静默执行的类别**。
2. **结论绑定这一次动作**（AC 2）—— 审批结论必须带**同一个** ``request_id``；
   对不上就是没批。**没有审批人 = 没批**（fail-closed）。
3. **置信度不作放行条件**（AC 5）—— 判据里**没有任何置信度输入**：
   工单给的理由是实测事实（存在高置信度的错误，且答对时的置信度均值反而低于
   答错时 → 绝对值不可当把握度，排序可用）。单测直接扫这段源码把住它。

⚠️ **dry-run 在请求里**（AC 3）：不可逆动作执行前，请求里带着**将要作用的
对象**（控件框、位置、截图哈希）；**没批就零动作下发**。

⚠️ **闸门永远在**（fail-closed）：没接审批人 = 危险动作**一律没批**。
「不装闸门」不是一种配置 —— 装上但**没人批**，危险动作照样发不出去。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from .catalog import CATEGORY_TITLES, DangerCategory, DangerMatch, classify
from .ports import Approver, SensitiveWindowPolicy, WindowInspector
from .request import ApprovalRequest, TargetPreview

__all__ = [
    "ApprovalOutcome",
    "ApprovalGate",
    "NO_APPROVER_REASON",
    "STALE_DECISION_REASON",
    "APPROVAL_HANDOFF_NOTE",
]


#: **没有接审批人**时的说法（fail-closed：没人为它背书，就不许执行）。
NO_APPROVER_REASON = "没有接审批人 → 按未获批处理（fail-closed）"

#: 审批结论与请求**对不上**时的说法。
STALE_DECISION_REASON = "审批结论与这一次动作对不上（绑定失效）→ 按未获批处理"

#: **审批未获批**时写进结果里的话（**零动作下发**，交人）。
#:
#: ⚠️ 它落进动作记录的 ``notes`` —— 「日志里有一条『已交接给人』的记录」
#: 由它承载（与 M12 的 ``ELEVATION_HANDOFF_NOTE`` 同一形状）。
APPROVAL_HANDOFF_NOTE = "已交接给人：危险动作的审批未获批（零动作下发）"


@dataclass(frozen=True)
class ApprovalOutcome:
    """审批闸门的结论。"""

    #: 这个动作**要不要**审批。
    required: bool
    #: **批了没有**（不需要审批时恒为真）。
    approved: bool
    #: 危险动作的类别（不需要审批时是 ``None``）。
    category: DangerCategory | None = None
    #: 人读的理由。
    reason: str = ""
    #: 产生的**审批请求**（不需要审批时是 ``None``）。
    request: ApprovalRequest | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "required": self.required,
            "approved": self.approved,
            "category": self.category.value if self.category is not None else None,
            "category_title": (
                CATEGORY_TITLES[self.category] if self.category is not None else ""
            ),
            "reason": self.reason,
            "request": self.request.to_dict() if self.request is not None else None,
        }

    @property
    def denied(self) -> bool:
        """要不要审批、但**没批**（→ 零动作下发）。"""
        return self.required and not self.approved


class ApprovalGate:
    """**危险动作的审批闸门**。

    问它两件事：

    * :meth:`check` —— 这个动作要不要审批、批不批（附 dry-run 的呈现）；
    * :meth:`check_capture` —— 抓帧（送模型观察）前问一次：
      **敏感窗口**的画面不许进上下文。
    """

    def __init__(
        self,
        *,
        approver: Approver | None = None,
        window_inspector: WindowInspector | None = None,
        sensitive_policy: SensitiveWindowPolicy | None = None,
    ):
        self._approver = approver
        self._window_inspector = window_inspector
        self._sensitive_policy = sensitive_policy

    # ------------------------------------------------------------------
    # 判定
    # ------------------------------------------------------------------

    def classify(
        self,
        *,
        intent: str = "",
        target_description: str = "",
        extra_text: str = "",
        category: DangerCategory | str | None = None,
    ) -> DangerMatch | None:
        """这个动作属于哪一类危险动作（不危险返回 ``None``）。

        ⚠️ **判据里没有置信度**（工单 M16 AC 5）—— 这里也**没有**接收它的参数。
        """
        return classify(
            intent=intent,
            target_description=target_description,
            extra_text=extra_text,
            category=category,
        )

    def check(
        self,
        *,
        intent: str = "",
        target_description: str = "",
        action_kind: str = "",
        extra_text: str = "",
        preview: TargetPreview | None = None,
        category: DangerCategory | str | None = None,
    ) -> ApprovalOutcome:
        """这个动作**批不批**。

        * 不危险 → :attr:`ApprovalOutcome.required` 是假、``approved`` 是真
          （**普通动作不受影响**）；
        * 危险 → 产生一份**审批请求**（带 dry-run 的呈现）→ 问审批人 →
          **批了才放行**。没批 / 没有审批人 / 结论对不上 → **零动作下发**。
        """
        match = self.classify(
            intent=intent,
            target_description=target_description,
            extra_text=extra_text,
            category=category,
        )
        if match is None:
            return ApprovalOutcome(
                required=False,
                approved=True,
                reason="未命中危险动作清单（普通动作，不需要审批）",
            )
        request = ApprovalRequest(
            request_id=uuid.uuid4().hex,
            category=match.category,
            intent=intent,
            target_description=target_description,
            preview=preview or TargetPreview(),
            rule_source=match.source,
        )
        if self._approver is None:
            return ApprovalOutcome(
                required=True,
                approved=False,
                category=match.category,
                reason=NO_APPROVER_REASON,
                request=request,
            )
        decision = self._approver.approve(request)
        if decision.request_id != request.request_id:
            # **绑定失效**：拿别的动作的结论来批这一次 —— 不认。
            return ApprovalOutcome(
                required=True,
                approved=False,
                category=match.category,
                reason=STALE_DECISION_REASON,
                request=request,
            )
        if not decision.approved:
            return ApprovalOutcome(
                required=True,
                approved=False,
                category=match.category,
                reason=decision.reason or f"审批未通过（{match.title}）",
                request=request,
            )
        return ApprovalOutcome(
            required=True,
            approved=True,
            category=match.category,
            reason=decision.reason or f"审批通过（{match.title}）",
            request=request,
        )

    def check_capture(
        self,
        *,
        intent: str = "抓一帧画面（送模型观察）",
        target_description: str = "整屏",
    ) -> ApprovalOutcome:
        """**抓帧之前**问一次：敏感窗口的画面不许进模型上下文。

        ⚠️ 这一问在**抓帧之前**（不是动作下发之前）：敏感窗口的画面一旦被抓走
        送进模型，审批就已经晚了 —— 那时要紧的**不是**动作，是**画面**。
        """
        if not self._foreground_is_sensitive():
            return ApprovalOutcome(
                required=False,
                approved=True,
                reason="前台窗口不敏感（不需要审批）",
            )
        return self.check(
            intent=intent,
            target_description=target_description,
            category=DangerCategory.SENSITIVE_SCREENSHOT,
        )

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------

    def _foreground_is_sensitive(self) -> bool:
        """**当前前台窗口是不是敏感窗口**。

        没接窗口读取端口或敏感策略 → **判不了**：没有策略就没有「敏感窗口」
        这个概念，判定留给策略接进来时做（**策略内容归 M20**）。

        ⚠️ **读不到窗口 → 按敏感处理**（fail-closed）：读不到当前窗口，
        我们**不知道**它是不是敏感窗口 —— 那就不许悄悄拍下来。
        """
        if self._window_inspector is None or self._sensitive_policy is None:
            return False
        try:
            window = self._window_inspector.foreground()
        except Exception:
            return True
        return bool(self._sensitive_policy.is_sensitive(window))

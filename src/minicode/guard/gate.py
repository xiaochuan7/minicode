"""**授权级别审批门**（护栏 3 的落点）：把「影响面 × 授权级别」接进审批闸门。

实现的是审批闸门的**同款接口**（``check`` / ``check_capture`` / ``classify``，
鸭子类型——缝只调这三个方法，不知道外面多了一层）。判定顺序：

1. **影响面判定**（:func:`minicode.guard.irreversible.classify_impact`，
   真实控件名与窗口标题一起进判定——审查安全 #2 同一条纪律）；
2. 不可逆动作按级别处置：
   * **L0/L1** → 拒绝（``irreversible_denied`` 经缝转 ``approval_denied``，
     零动作下发——L1 没有审批人在场，宁可不干）；
   * **L2** → **必须人工确认**：直接拿本门的审批人问（``ApprovalRequest``
     带 dry-run 的目标呈现），没接审批人 = 没人能批 = fail-closed 拒绝；
   * **L3** → 放行，判定记录在案（无人值守实验，事后可审计）；
3. 其余（可逆）动作**原样委托内层闸门**——M16 词表、无审批人 fail-closed、
   request_id 绑定校验全在内层，这里不复制第二份判定规则。

判定记录（:attr:`decisions`）只记**不可逆**的那几笔——可逆动作委托内层，
不是护栏的干预，不进记录。
"""

from __future__ import annotations

import uuid
from typing import Any

from minicode.approval.catalog import DangerCategory
from minicode.approval.gate import ApprovalOutcome
from minicode.approval.request import ApprovalRequest

from .irreversible import classify_impact, impact_verdict
from .levels import AuthLevel

__all__ = ["LevelApprovalGate"]

#: 影响面 → 审批请求的类别（审批请求要一个 :class:`DangerCategory`；
#: W2 已把缺口的三类补进目录，这里是一张完整映射）。
_IMPACT_TO_DANGER: dict[str, DangerCategory] = {
    "write_file": DangerCategory.WRITE_FILE,
    "submit_form": DangerCategory.SUBMIT_FORM,
    "close_unsaved": DangerCategory.CLOSE_UNSAVED,
    "delete": DangerCategory.DELETE,
    "payment": DangerCategory.PAYMENT,
    "send": DangerCategory.SEND_MESSAGE,
    "system_change": DangerCategory.SYSTEM_SETTINGS,
}


class LevelApprovalGate:
    """按授权级别处置不可逆动作的审批门（包装内层闸门 + 自己的审批人）。"""

    def __init__(
        self,
        inner: Any,
        *,
        level: AuthLevel,
        approver: Any | None = None,
    ):
        self._inner = inner
        self._level = level
        #: **L2 强制审批用的审批人**（与内层闸门同源：真机接线给
        #: ``ConsoleApprover``；测试给脚本化审批人）。``None`` = 没有人能批。
        self._approver = approver
        #: 本门的判定记录（只记不可逆那几笔，进运行摘要）。
        self.decisions: list[dict] = []

    def classify(self, **kwargs):  # noqa: ANN003 - 透传内层签名
        """分类：原样委托内层（M16 词表的判定规则只有一份）。"""
        return self._inner.classify(**kwargs)

    def check_capture(self, **kwargs):  # noqa: ANN003 - 透传内层签名
        """抓帧审批：与级别无关，原样委托内层（M20 硬闸门在那里面）。"""
        return self._inner.check_capture(**kwargs)

    def check(
        self,
        *,
        intent: str = "",
        target_description: str = "",
        action_kind: str = "",
        extra_text: str = "",
        preview: Any = None,
        category: Any = None,
    ) -> ApprovalOutcome:
        """审批判定：不可逆动作按授权级别处置，其余原样委托内层。"""
        real_element_text = ""
        if preview is not None:
            real_element_text = (
                f"{getattr(preview, 'control_name', '') or ''} "
                f"{getattr(preview, 'window_title', '') or ''}"
            )
        impact = classify_impact(
            intent=intent,
            target_description=target_description,
            params_text=extra_text,
            real_element_text=real_element_text,
            action_kind=action_kind,
        )
        verdict = impact_verdict(impact, self._level)
        if not verdict.irreversible:
            # 可逆动作：不是护栏的干预，直接走内层（M16 语义原样）。
            return self._inner.check(
                intent=intent,
                target_description=target_description,
                action_kind=action_kind,
                extra_text=extra_text,
                preview=preview,
                category=category,
            )

        self.decisions.append(
            {
                "impact": impact.value,
                "verdict": verdict.verdict,
                "reason": verdict.reason,
                "target": target_description,
            }
        )
        if verdict.verdict == "deny":
            return ApprovalOutcome(
                required=True,
                approved=False,
                category=None,
                reason=f"{verdict.reason}（护栏 3：irreversible_denied）",
                request=None,
            )
        if verdict.verdict == "allow":
            # L3 无人值守：放行，判定已记录（上面那笔 decisions）。
            return ApprovalOutcome(
                required=True,
                approved=True,
                category=None,
                reason=f"{verdict.reason}（护栏 3：L3 记录放行）",
                request=None,
            )
        # -- L2：必须人工确认（自持审批请求 → 问本门的审批人）----------------
        request = ApprovalRequest(
            request_id=uuid.uuid4().hex,
            category=_IMPACT_TO_DANGER[impact.value],
            intent=intent or action_kind,
            target_description=target_description,
            preview=preview,
            rule_source="W2 影响面判定（guard.irreversible）",
        )
        if self._approver is None:
            return ApprovalOutcome(
                required=True,
                approved=False,
                category=request.category,
                reason="不可逆动作必须人工确认，但没有接审批人（fail-closed）",
                request=request,
            )
        decision = self._approver.approve(request)
        if decision.request_id != request.request_id:
            return ApprovalOutcome(
                required=True,
                approved=False,
                category=request.category,
                reason="审批结论与请求对不上（绑定失效）→ 不认",
                request=request,
            )
        return ApprovalOutcome(
            required=True,
            approved=bool(decision.approved),
            category=request.category,
            reason=decision.reason or ("人工批准" if decision.approved else "人工未批准"),
            request=request,
        )

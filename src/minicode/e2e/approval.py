"""**e2e 跑批那一侧的审批人**：agent 代批。

⚠️ **生产默认一个字不动**：``approval/console.py::console_gate()`` 的
fail-closed 语义保持不变（非交互终端一律不批 —— 「绝不把『没人回答』当成
『批准』」）。**只有 e2e 跑批**在这里注入一个「agent 代批」的审批人：
用户本轮明确授权「真机完全授权，不需要等人工」。

⚠️ **如实记**：报告里写的是「**agent 代批**」，**不是**「人工批准」——
代批请求会进追踪记录（``approval.requests`` → ``ApprovalGate`` 的记录），
读得到是哪一条被代批的。
"""

from __future__ import annotations

from minicode.approval.fakes import AutoApprover
from minicode.approval.gate import ApprovalGate

__all__ = ["AGENT_APPROVAL_REASON", "agent_approval_gate"]

#: 代批的**理由**（人读；进审批记录 → 进追踪记录 → 进报告）。
AGENT_APPROVAL_REASON = "agent 代批（用户本轮授权：真机完全授权，不需要等人工）"


def agent_approval_gate() -> ApprovalGate:
    """e2e 跑批用的审批闸门：**agent 代批**（敏感动作也放行）。

    ⚠️ **只在 e2e 跑批这一侧用**（生产路径仍然是 fail-closed 的
    ``console_gate()``）。
    """
    return ApprovalGate(approver=AutoApprover(reason=AGENT_APPROVAL_REASON))

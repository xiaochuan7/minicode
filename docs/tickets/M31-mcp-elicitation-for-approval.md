M31 | MCP 的危险动作走 elicitation 拿批准 | Blocked by: M16, M19 | Status: ready-for-agent

## Parent

- 本规格 Issue：https://github.com/xiaochuan7/minicode/issues/2
- 本单 Issue 号：<待回填>

## What to build

MCP 是 stdio 形态：宿主把本进程当 MCP server 拉起，**标准输入是协议流、不是终端** → 没有人能批 → 危险动作**永远未获批**（fail-closed 本身没错，但没记一笔，宿主也不知道为什么被拒）。

本单让 MCP 侧的危险动作走 **elicitation**：宿主（DSH / Claude Code）能在对话里点头，批准结论沿用同一套 `ApprovalRequest` / `ApprovalDecision` 形状。

## Acceptance criteria

- [ ] **走 elicitation**：MCP 侧的危险动作请求批准时，通过 MCP 的 elicitation 机制问宿主；宿主批准 → 动作继续；宿主拒绝或不应答 → **零动作下发**、结果里写明「未获批」。
- [ ] **结论形状沿用同一份**：`ApprovalRequest` / `ApprovalDecision` 的字段与错误码**与命令行/HTTP 完全一致**（MCP 不新增动作语义与坐标换算）。
- [ ] **绑定这一次动作**：批准**不跨动作复用**（拿旧结论批新动作 = 没批）；单测钉住。
- [ ] **没有 elicitation 能力时也 fail-closed**：宿主不支持 elicitation → 危险动作**未获批**、零动作下发，**并且在结果里明确记一笔**（不是静默失败）。
- [ ] **与 M16 的闸门是同一份实现**：MCP 不自建判据，只是换一个「谁来批」的端口；十一类清单、关键词表、置信度不得作放行条件这些约束**一条都不松**。
- [ ] **不暴露 streamable HTTP**（D-42 维持现状）：本单只做 elicitation，传输形态仍是 stdio。

## Blocked by

- M16 — 危险动作审批（闸门与十一类清单）
- M19 — MCP server（对外形态与工具集）

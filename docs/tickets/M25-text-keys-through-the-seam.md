M25 | 文本与按键不再绕过动作执行缝；对外形态完整暴露 12 个动作种类 | Blocked by: M10, M17, M19, M21 | Status: ready-for-agent

## Parent

- 本规格 Issue：https://github.com/xiaochuan7/minicode/issues/2
- 本单 Issue 号：<待回填>

## What to build

**这是真 bug，不是口径分歧**：决策层与 MCP 的程序能执行 `type_text` / `press_key`，但这两类动作**不经过动作执行缝**，于是**没有动作记录**。M17 的字段完整性规定「动作下发过却没有动作类型 = 缺字段」——所以在 MCP 这一层，「出了事看日志」目前是**假的**。

本单让文本与按键也走缝：对齐 D-38（对外形态**完整暴露 12 个动作种类**，`type` / `key` 不再返回 `action_not_implemented`）。

## Acceptance criteria

- [ ] **文本与按键走缝**：`type` / `key` 经决策层与 MCP 发起时，最终落进**动作执行缝**（与其它动作种类同一条路），因而**必然产生动作记录**。
- [ ] **动作记录完整**：跑一段含 `type` / `key` 的动作程序 → 动作记录里这些步骤**有动作类型**、有目标描述、有结果（含错误码）、有截图哈希（有抓帧时）；不再出现「有动作下发但记录里没有动作类型」。
- [ ] **字段完整性回归**：M17 的审计器对含文本/按键的记录判「字段完整」通过；单测预置「动作下发过却缺动作类型」的记录 → 审计器**点名**（这项回归守住，不许因为本单改坏）。
- [ ] **12 个动作种类完整暴露**：对外形态（CLI / HTTP / MCP）的动作种类集合 = M21 锁死的那 12 个（screenshot / click / move / drag / type / key / scroll / wait / zoom / inspect / execute_code / ask_user），**不多不少**；加一条断言把这份集合锁死（沿用 M21 的做法）。
- [ ] **未实现即报错**：`screenshot` / `wait` / `zoom` / `inspect` / `execute_code` / `ask_user` 里凡本轮仍未接线的，返回**明确的错误码**（`action_not_implemented` 之类），**零动作下发**，**不许半实现**。
- [ ] 剪贴板通道（`type` 的一种）仍然**备份并恢复**剪贴板（不回归 M0 的「粘贴不污染剪贴板」）。

## Blocked by

- M10 — 决策层 harness（动作程序的执行路径）
- M17 — 动作日志回放（字段完整性的判据在这里）
- M19 — MCP server（对外形态的工具集）
- M21 — 动作协议缺口（12 个动作种类的集合断言）

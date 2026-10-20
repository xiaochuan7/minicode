M19 | MCP server 对外暴露 | Blocked by: M18 | Status: ready-for-agent

## Parent

- 本规格 Issue：https://github.com/xiaochuan7/minicode/issues/2
- 本单 Issue 号：<待回填>（联网补发时回填；届时本单作为子单挂在上面这单之下）

## What to build

桌面控制能作为 MCP server 暴露，让宿主（DSH / Claude Code）在对话里直接调用，不用切窗口。

MCP 侧是薄适配：动作语义、坐标口径、错误码与既有协议一致，不另起一套。

## Acceptance criteria

- [ ] MCP 工具暴露：宿主能列出并调用桌面控制能力，至少覆盖「执行一段动作程序」与「读观察」两类。
- [ ] 语义一致：经 MCP 发起的一次动作，与经 CLI / HTTP 发起同一请求得到相同结果。
- [ ] 版本与错误码沿用：MCP 层不新增动作语义与坐标换算；协议版本不匹配时明确报错。

## Blocked by

- M18 — 独立 CLI + HTTP 暴露（协议稳定后才 MCP 化）

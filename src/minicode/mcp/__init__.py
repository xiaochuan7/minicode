"""**MCP server 对外暴露**（工单 M19）。

桌面控制作为 **MCP server** 暴露出去，宿主（DSH / Claude Code）在对话里
直接调用，不用切窗口。

⚠️ **MCP 侧是薄适配**：动作语义、坐标口径、错误码与既有协议**一致**，
**不另起一套**（工单 M19 的「What to build」）。

* :mod:`minicode.mcp.server` —— JSON-RPC 2.0 的 MCP server 本体（stdio 传输）；
* :mod:`minicode.mcp.tools` —— 工具目录（三个工具 = 服务里已有的三种调用）；
* :mod:`minicode.mcp.__main__` —— ``python -m minicode.mcp``。

三个前端（命令行 / HTTP / MCP）**共用**
:mod:`minicode.serve.service` 里的那一份实现 ——
MCP 这一层只做「解析 → 调它 → 渲染」。
"""

from __future__ import annotations

from .server import McpError, McpServer
from .tools import TOOL_ACTION, TOOL_OBSERVE, TOOL_PROGRAM, ToolSpec, build_tools

__all__ = [
    "McpError",
    "McpServer",
    "TOOL_ACTION",
    "TOOL_OBSERVE",
    "TOOL_PROGRAM",
    "ToolSpec",
    "build_tools",
]

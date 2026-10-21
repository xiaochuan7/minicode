"""**MCP server 本体**（工单 M19）：JSON-RPC 2.0，stdio 传输。

这一层只做三件事：

1. **认 JSON-RPC 的信封**（``jsonrpc`` / ``id`` / ``method`` / ``params``）；
2. **把方法名派到工具上**（``tools/list`` / ``tools/call``）；
3. **把工具结果渲染成 MCP 的形状**（``content`` + ``isError``）。

⚠️ **它不懂动作语义**：动作语义、坐标口径与错误码都在
:mod:`minicode.serve.service` 那一份里（命令行 / HTTP / MCP 共用）。

## 传输

**stdio**：一行一条 JSON（JSON Lines）。标准输出**只**走协议消息 ——
横幅与日志一律走标准错误（否则会把协议流弄坏）。

## 错误

JSON-RPC 的错误码是**传输层**的说法，动作协议的错误码是**动作语义** ——
两者分开（与 HTTP 前端那套口径一样）：

* **JSON-RPC 错误**（``error.code``）：方法不存在、参数不对、消息不是 JSON；
* **动作协议错误码**（工具结果里的 ``error_code``）：动作失败、版本对不上。
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from minicode.serve.protocol import PROTOCOL_VERSION

__all__ = [
    "MCP_PROTOCOL_VERSION",
    "McpError",
    "McpServer",
    "run_stdio",
]

#: JSON-RPC 的版本号。
JSONRPC_VERSION = "2.0"

#: **MCP 协议**版本号（本实现说的那一版）。
#:
#: ⚠️ **别与动作协议版本混**（那是 ``cua.action/1``，另一个东西）：
#: MCP 协议版本说的是「宿主与本 server 怎么说话」，动作协议版本说的是
#: 「动作的语义与坐标口径是哪一版」。
MCP_PROTOCOL_VERSION = "2025-06-18"

# -- JSON-RPC 错误码（传输层） ---------------------------------------------
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603


class McpError(Exception):
    """JSON-RPC 层的错误（**传输层**，不是动作语义）。"""

    def __init__(self, code: int, message: str, data: Any = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.data = data


class McpServer:
    """MCP server。

    :param tools: 工具目录（:func:`minicode.mcp.tools.build_tools` 的结果）。
    :param server_name: 服务名（握手时告诉客户端）。
    :param action_protocol_version: 本实现覆盖的**动作协议**版本
        （``cua.action/1``）—— 握手时作为 ``serverInfo.version`` 报出去。
        ⚠️ 它**不是** MCP 协议版本（那是 :data:`MCP_PROTOCOL_VERSION`）。
    """

    def __init__(
        self,
        tools: Any,
        *,
        server_name: str = "minicode",
        action_protocol_version: str = PROTOCOL_VERSION,
    ):
        self._tools = {tool.name: tool for tool in tools}
        self._server_name = server_name
        self._action_protocol_version = action_protocol_version

    # ------------------------------------------------------------------
    # 消息层
    # ------------------------------------------------------------------

    def handle_message(self, message: Any) -> dict[str, Any] | None:
        """处理一条 JSON-RPC 消息。

        **通知**（没有 ``id`` 的消息）→ 返回 ``None``（不回复）。
        其余情况都回一条消息（成功是 ``result``，失败是 ``error``）。
        """
        if not isinstance(message, Mapping):
            return _error_message(None, INVALID_REQUEST, "消息不是一张键值表")
        message_id = message.get("id")
        method = message.get("method")
        if not isinstance(method, str) or not method:
            return _error_message(message_id, INVALID_REQUEST, "消息缺 method 字段")
        params = message.get("params", {})
        if params is None:
            params = {}
        if not isinstance(params, Mapping):
            return _error_message(message_id, INVALID_PARAMS, "params 不是一张键值表")
        try:
            result = self.handle(method, params)
        except McpError as exc:
            return _error_message(message_id, exc.code, exc.message, exc.data)
        except Exception as exc:  # 不让异常逃到传输层（客户端只看到连接断掉）
            return _error_message(
                message_id, INTERNAL_ERROR, f"{type(exc).__name__}: {exc}"
            )
        if message_id is None:
            # **通知**：不回。
            return None
        return {"jsonrpc": JSONRPC_VERSION, "id": message_id, "result": result}

    def handle(self, method: str, params: Mapping[str, Any]) -> dict[str, Any]:
        """派发一个方法。方法不认识 → :class:`McpError`。"""
        if method == "initialize":
            return self._initialize(params)
        if method == "ping":
            return {}
        if method.startswith("notifications/"):
            # 通知：客户端说「我准备好了」之类 —— 认下来，不做事。
            return {}
        if method == "tools/list":
            return {"tools": [tool.to_dict() for tool in self._tools.values()]}
        if method == "tools/call":
            return self._call_tool(params)
        raise McpError(METHOD_NOT_FOUND, f"方法不支持：{method}")

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------

    def _initialize(self, params: Mapping[str, Any]) -> dict[str, Any]:
        """握手。

        ⚠️ **本实现只说一个 MCP 协议版本**：客户端要别的版本，我们仍然报
        我们这版（由客户端决定接不接）—— 少认几个版本比「假装都支持」诚实。
        """
        return {
            "protocolVersion": MCP_PROTOCOL_VERSION,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {
                "name": self._server_name,
                # ⚠️ 报的是**动作协议**版本（``cua.action/1``）——
                # MCP 协议版本在上面那个字段里。
                "version": self._action_protocol_version,
            },
        }

    def _call_tool(self, params: Mapping[str, Any]) -> dict[str, Any]:
        name = params.get("name")
        if not isinstance(name, str) or not name:
            raise McpError(INVALID_PARAMS, "tools/call 缺 name 字段")
        tool = self._tools.get(name)
        if tool is None:
            raise McpError(INVALID_PARAMS, f"没有这个工具：{name}")
        arguments = params.get("arguments", {})
        if arguments is None:
            arguments = {}
        if not isinstance(arguments, Mapping):
            raise McpError(INVALID_PARAMS, "arguments 不是一张键值表")
        payload = tool.run(arguments)
        return {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps(payload, ensure_ascii=False, indent=2),
                }
            ],
            # **工具失败**标出来（动作协议的错误码在载荷里，调用方按它分流）。
            "isError": not bool(payload.get("ok", True)),
        }


def _error_message(
    message_id: Any, code: int, message: str, data: Any = None
) -> dict[str, Any]:
    """一条 JSON-RPC 错误响应。"""
    error: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    return {"jsonrpc": JSONRPC_VERSION, "id": message_id, "error": error}


def run_stdio(server: McpServer, stdin: Any = None, stdout: Any = None) -> int:
    """在标准输入输出上跑 MCP 协议（一行一条 JSON）。

    ⚠️ **标准输出只走协议消息**：横幅与日志走标准错误 ——
    混在一起会把协议流弄坏（客户端解析不了）。

    ⚠️ **两个流都固定成 UTF-8**：Windows 上**管道**的默认编码是 GBK，
    而 MCP 的消息流是 UTF-8 的 JSON —— 编码不对宿主就解析不了
    （中文目标描述尤其明显）。
    """
    import sys

    source = stdin if stdin is not None else sys.stdin
    sink = stdout if stdout is not None else sys.stdout
    _force_utf8(source)
    _force_utf8(sink)
    for line in source:
        stripped = line.strip()
        if not stripped:
            continue
        try:
            message = json.loads(stripped)
        except json.JSONDecodeError as exc:
            response: dict[str, Any] | None = _error_message(
                None, PARSE_ERROR, f"不是合法 JSON：{exc}"
            )
        else:
            response = server.handle_message(message)
        if response is not None:
            sink.write(json.dumps(response, ensure_ascii=False) + "\n")
            sink.flush()
    return 0


def _force_utf8(stream: Any) -> None:
    """把一个文本流**固定成 UTF-8**（重配不了就随它去）。"""
    reconfigure = getattr(stream, "reconfigure", None)
    if reconfigure is None:
        return
    try:
        reconfigure(encoding="utf-8")
    except (ValueError, OSError):  # pragma: no cover - 有些流不支持重配
        pass

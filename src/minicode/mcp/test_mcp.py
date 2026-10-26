"""**MCP server 对外暴露**的单测（工单 M19）。

只断言**对外可见**的行为：宿主能列出的工具、工具调用后的结果、
错误码与版本核对。不测内部实现细节。

四条验收标准各对应一个测试类：

* :class:`ToolCatalogTest` —— AC1（宿主能列出并调用桌面控制能力：
  至少覆盖「执行一段动作程序」与「读观察」两类）；
* :class:`ToolCallTest` —— AC1（工具真的能被调用：动作 / 动作程序 / 观察）；
* :class:`SameResultAsCliTest` —— AC2（经 MCP 发起的一次动作，与经 CLI
  发起同一请求得到相同结果）；
* :class:`VersionAndErrorCodeTest` —— AC3（版本与错误码沿用既有协议：
  版本不匹配时明确报错、零动作下发）。

全程假桌面（假截图源 / 假控件树 / 假注入器）：不碰真桌面、不调模型、不花钱。
"""

from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

from minicode.action.errors import ErrorCode
from minicode.action.fakes import FakeInputInjector
from minicode.serve import cli
from minicode.serve.fakes import FAKE_LANDING, FAKE_TARGET, fake_service
from minicode.serve.protocol import PROTOCOL_VERSION

from .server import McpServer
from .tools import TOOL_ACTION, TOOL_OBSERVE, TOOL_PROGRAM, build_tools

#: 协议里的错误码取值集合（对外暴露的错误码必须是它的子集）。
_PROTOCOL_ERROR_CODES = {code.value for code in ErrorCode}


def _server(directory: Path, injector: FakeInputInjector | None = None) -> McpServer:
    """一个跑在假桌面上的 MCP server。"""
    service = fake_service(directory, injector=injector)
    return McpServer(build_tools(service))


def _call(
    server: McpServer,
    method: str,
    params: dict[str, Any] | None = None,
    request_id: int = 1,
) -> dict[str, Any]:
    """发一条 JSON-RPC 请求，返回 ``result``。"""
    message: dict[str, Any] = {"jsonrpc": "2.0", "id": request_id, "method": method}
    if params is not None:
        message["params"] = params
    response = server.handle_message(message)
    assert response is not None, "请求必须有一条响应"
    assert "result" in response, f"请求应成功，实际：{response}"
    return response["result"]


def _call_tool(
    server: McpServer, name: str, arguments: dict[str, Any]
) -> dict[str, Any]:
    """调一个工具，返回**工具结果里的那份载荷**（JSON 文本解析回来）。"""
    result = _call(server, "tools/call", {"name": name, "arguments": arguments})
    assert result["content"], "工具结果必须带内容"
    return json.loads(result["content"][0]["text"])


def _run_cli(argv: list[str]) -> tuple[int, str]:
    """跑一次命令行，拿到（退出码，标准输出）。"""
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        code = cli.main(argv)
    return code, buffer.getvalue()


class ToolCatalogTest(unittest.TestCase):
    """AC1：宿主能**列出**桌面控制能力（至少两类：动作程序与读观察）。"""

    def test_the_server_lists_the_action_the_program_and_the_observe_tools(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = _call(_server(Path(tmp)), "tools/list")
            names = [tool["name"] for tool in result["tools"]]
            self.assertIn(TOOL_ACTION, names)
            self.assertIn(TOOL_PROGRAM, names)
            self.assertIn(TOOL_OBSERVE, names)

    def test_every_tool_tells_the_host_what_it_takes(self):
        """每个工具都要有**说明与入参形状** —— 宿主靠它决定怎么调。"""
        with tempfile.TemporaryDirectory() as tmp:
            result = _call(_server(Path(tmp)), "tools/list")
            for tool in result["tools"]:
                with self.subTest(tool=tool["name"]):
                    self.assertTrue(tool["description"])
                    self.assertEqual(tool["inputSchema"]["type"], "object")
                    self.assertIn("protocol", tool["inputSchema"]["properties"])

    def test_the_server_answers_the_initialize_handshake(self):
        """宿主连上来先握手：服务端要说清自己的协议版本与能力。"""
        with tempfile.TemporaryDirectory() as tmp:
            result = _call(
                _server(Path(tmp)),
                "initialize",
                {"protocolVersion": "2025-06-18", "capabilities": {}},
            )
            self.assertEqual(result["protocolVersion"], "2025-06-18")
            self.assertIn("tools", result["capabilities"])
            self.assertEqual(result["serverInfo"]["name"], "minicode")

    def test_an_unknown_method_is_reported_as_a_json_rpc_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            server = _server(Path(tmp))
            response = server.handle_message(
                {"jsonrpc": "2.0", "id": 7, "method": "resources/list"}
            )
            self.assertIsNotNone(response)
            self.assertEqual(response["error"]["code"], -32601)


class ToolCallTest(unittest.TestCase):
    """AC1：宿主能**调用**这些能力。"""

    def test_the_action_tool_runs_one_action_and_reports_the_landing_point(self):
        with tempfile.TemporaryDirectory() as tmp:
            server = _server(Path(tmp))
            payload = _call_tool(
                server,
                TOOL_ACTION,
                {"protocol": PROTOCOL_VERSION, "action": "click", "target": FAKE_TARGET},
            )
            self.assertTrue(payload["ok"])
            self.assertIsNone(payload["error_code"])
            self.assertEqual(payload["landing_point"], list(FAKE_LANDING))
            self.assertEqual(payload["action"]["kind"], "click")
            self.assertEqual(payload["protocol_version"], PROTOCOL_VERSION)

    def test_the_program_tool_runs_a_program_in_the_controlled_environment(self):
        """「执行一段动作程序」：程序在**受控环境**里跑，动作真的下发。"""
        with tempfile.TemporaryDirectory() as tmp:
            injector = FakeInputInjector()
            server = _server(Path(tmp), injector=injector)
            payload = _call_tool(
                server,
                TOOL_PROGRAM,
                {
                    "protocol": PROTOCOL_VERSION,
                    "program": f'click("{FAKE_TARGET}")\nprint("做完了")\n',
                },
            )
            self.assertTrue(payload["ok"])
            self.assertIsNone(payload["error_code"])
            self.assertIn("做完了", payload["stdout"])
            self.assertEqual(len(payload["calls"]), 1)
            self.assertEqual(payload["calls"][0]["kind"], "click")
            self.assertEqual(payload["calls"][0]["point"], list(FAKE_LANDING))
            # **动作真的下发了**（受控环境里的 click 走的是动作执行缝）。
            self.assertTrue(injector.events)

    def test_a_program_that_tries_to_escape_is_rejected_before_any_action(self):
        """程序想碰文件系统 → **执行前**被拒，零动作下发。"""
        with tempfile.TemporaryDirectory() as tmp:
            injector = FakeInputInjector()
            server = _server(Path(tmp), injector=injector)
            payload = _call_tool(
                server,
                TOOL_PROGRAM,
                {"protocol": PROTOCOL_VERSION, "program": "open('C:/Windows/win.ini')\n"},
            )
            self.assertFalse(payload["ok"])
            self.assertEqual(payload["error_code"], ErrorCode.PROGRAM_REJECTED.value)
            self.assertEqual(injector.events, [])

    def test_a_dangerous_action_inside_a_program_is_denied_and_runs_nothing(self):
        """危险动作在 MCP 这一层**照样要人点头**（M16 的闸门永远在）。

        没人能批（stdio 形态下标准输入是协议流，不是终端）→ 不批 →
        **零动作下发**。
        """
        with tempfile.TemporaryDirectory() as tmp:
            injector = FakeInputInjector()
            server = _server(Path(tmp), injector=injector)
            payload = _call_tool(
                server,
                TOOL_PROGRAM,
                {
                    "protocol": PROTOCOL_VERSION,
                    "program": "type_text('删除所有文件')\n",
                },
            )
            self.assertEqual(len(payload["calls"]), 1)
            self.assertFalse(payload["calls"][0]["ok"])
            self.assertEqual(
                payload["calls"][0]["error"], ErrorCode.APPROVAL_DENIED.value
            )
            self.assertEqual(injector.events, [])
            # **停止信号**：危险动作没获批 → 停下、交人（宿主看得见）。
            self.assertTrue(payload["halted"])
            self.assertTrue(payload["halt_reason"])

    def test_the_program_tool_reports_the_stop_signal(self):
        """程序**请求交还给人** → 宿主看得见。

        ⚠️ 协议里没有「完成」这个动作（工单 M10）：``ok=True`` 只说
        「程序跑完了」，「交给人了」是**另一个信号**。
        """
        with tempfile.TemporaryDirectory() as tmp:
            server = _server(Path(tmp))
            payload = _call_tool(
                server,
                TOOL_PROGRAM,
                {
                    "protocol": PROTOCOL_VERSION,
                    "program": "ask_user('这一步要人来定')\n",
                },
            )
            self.assertTrue(payload["ok"])
            self.assertTrue(payload["asked_human"])
            self.assertFalse(payload["halted"])

    def test_the_observe_tool_reads_a_screenshot_and_the_redacted_tree(self):
        """「读观察」：截图摘要 + **已脱敏**的控件树。"""
        with tempfile.TemporaryDirectory() as tmp:
            server = _server(Path(tmp))
            payload = _call_tool(
                server,
                TOOL_OBSERVE,
                {"protocol": PROTOCOL_VERSION, "name": FAKE_TARGET},
            )
            self.assertTrue(payload["ok"])
            self.assertIsNone(payload["error_code"])
            self.assertEqual(payload["screenshot"]["width"], 2560)
            self.assertEqual(payload["screenshot"]["height"], 1600)
            self.assertTrue(payload["screenshot"]["sha256"])
            # 控件树是**已脱敏**的模型侧文本（脱敏器是模型侧文本的唯一出口）。
            self.assertIn(f'name="{FAKE_TARGET}"', payload["controls"])
            self.assertIsNotNone(payload["redaction"])

    def test_the_observe_tool_refuses_to_read_the_tree_without_redaction(self):
        """脱敏没上线 → 控件树**不许**进模型上下文。"""
        from minicode.redact.config import ModelContextConfig
        from minicode.redact.redactor import TreeRedactor
        from minicode.serve.fakes import fake_wiring
        from minicode.serve.service import ActionService

        with tempfile.TemporaryDirectory() as tmp:
            wiring, _injector = fake_wiring()
            service = ActionService(
                seam_factory=wiring,
                log_directory=tmp,
                redactor=TreeRedactor(ModelContextConfig()),
            )
            server = McpServer(build_tools(service))
            payload = _call_tool(
                server,
                TOOL_OBSERVE,
                {"protocol": PROTOCOL_VERSION, "name": FAKE_TARGET},
            )
            self.assertFalse(payload["ok"])
            self.assertEqual(payload["error_code"], ErrorCode.CHANNEL_DISABLED.value)
            self.assertIsNone(payload["controls"])

    def test_the_observe_tool_needs_a_name(self):
        """定点观察**必须带属性条件**（禁止全树 dump）。"""
        with tempfile.TemporaryDirectory() as tmp:
            server = _server(Path(tmp))
            payload = _call_tool(server, TOOL_OBSERVE, {"protocol": PROTOCOL_VERSION})
            self.assertFalse(payload["ok"])
            self.assertEqual(payload["error_code"], ErrorCode.INVALID_REQUEST.value)

    def test_the_observe_tool_reports_target_not_found_like_the_action_path(self):
        """候选集为空 = **覆盖失败** → 与走动作那一条路**同一个错误码**。

        ⚠️ 不另立一套：「观察没找到」与「动作没找到」是同一件事。
        """
        with tempfile.TemporaryDirectory() as tmp:
            server = _server(Path(tmp))
            payload = _call_tool(
                server,
                TOOL_OBSERVE,
                {"protocol": PROTOCOL_VERSION, "name": "树上没有的按钮"},
            )
            self.assertFalse(payload["ok"])
            self.assertEqual(payload["error_code"], ErrorCode.TARGET_NOT_FOUND.value)
            self.assertIsNone(payload["controls"])
            self.assertEqual(payload["controls_count"], 0)


class SameResultAsCliTest(unittest.TestCase):
    """AC2：经 MCP 发起的一次动作，与经 CLI 发起同一请求得到**相同结果**。"""

    def test_the_same_request_gives_the_same_action_and_landing_point(self):
        with tempfile.TemporaryDirectory() as cli_dir, tempfile.TemporaryDirectory() as mcp_dir:
            _code, out = _run_cli(
                ["action", "--target", FAKE_TARGET, "--fake", "--out", cli_dir, "--json"]
            )
            cli_payload = json.loads(out)

            server = _server(Path(mcp_dir))
            mcp_payload = _call_tool(
                server,
                TOOL_ACTION,
                {"protocol": PROTOCOL_VERSION, "action": "click", "target": FAKE_TARGET},
            )

            self.assertEqual(mcp_payload["action"], cli_payload["action"])
            self.assertEqual(mcp_payload["landing_point"], cli_payload["landing_point"])
            self.assertEqual(mcp_payload["ok"], cli_payload["ok"])
            self.assertEqual(
                mcp_payload["protocol_version"], cli_payload["protocol_version"]
            )


class VersionAndErrorCodeTest(unittest.TestCase):
    """AC3：版本与错误码沿用既有协议；版本不匹配时明确报错。"""

    def test_a_mismatched_protocol_version_is_reported_and_no_action_runs(self):
        with tempfile.TemporaryDirectory() as tmp:
            injector = FakeInputInjector()
            server = _server(Path(tmp), injector=injector)
            payload = _call_tool(
                server,
                TOOL_ACTION,
                {"protocol": "cua.action/0", "action": "click", "target": FAKE_TARGET},
            )
            self.assertFalse(payload["ok"])
            self.assertEqual(
                payload["error_code"], ErrorCode.PROTOCOL_VERSION_MISMATCH.value
            )
            # **没有按旧语义猜着执行**：一个输入事件都没下发。
            self.assertEqual(injector.events, [])

    def test_every_error_code_the_mcp_layer_reports_is_a_protocol_one(self):
        """MCP 层**不新增**错误码 —— 报出去的都是动作协议里的取值。"""
        with tempfile.TemporaryDirectory() as tmp:
            server = _server(Path(tmp))
            cases = [
                (TOOL_ACTION, {"protocol": "cua.action/0", "target": FAKE_TARGET}),
                (TOOL_ACTION, {"protocol": PROTOCOL_VERSION, "target": "树上没有的按钮"}),
                (TOOL_ACTION, {"protocol": PROTOCOL_VERSION, "action": "teleport"}),
                (
                    TOOL_PROGRAM,
                    {"protocol": PROTOCOL_VERSION, "program": "open('x')"},
                ),
                (TOOL_OBSERVE, {"protocol": PROTOCOL_VERSION}),
            ]
            for name, arguments in cases:
                with self.subTest(name=name, arguments=arguments):
                    payload = _call_tool(server, name, arguments)
                    self.assertFalse(payload["ok"])
                    self.assertIn(payload["error_code"], _PROTOCOL_ERROR_CODES)

    def test_the_mcp_layer_does_not_convert_coordinates(self):
        """落点**不由调用方给**（给了也不认）：坐标口径只有动作执行缝那一份。"""
        with tempfile.TemporaryDirectory() as tmp:
            server = _server(Path(tmp))
            payload = _call_tool(
                server,
                TOOL_ACTION,
                {
                    "protocol": PROTOCOL_VERSION,
                    "action": "click",
                    "target": FAKE_TARGET,
                    "params": {"x": 1, "y": 2},
                },
            )
            self.assertTrue(payload["ok"])
            # 落点是**缝**给的（控件框中心），不是请求里那两个数。
            self.assertEqual(payload["landing_point"], list(FAKE_LANDING))
            self.assertEqual(payload["action"]["params"]["space"], "physical-surface")

    def test_the_server_writes_only_protocol_messages_to_stdout(self):
        """**标准输出只走协议消息** —— 横幅与日志一律走标准错误。

        ⚠️ 真机上拿锁与接线都会往标准输出打横幅；混进协议流里，
        宿主读到的是一行乱码而不是 JSON。
        """
        from minicode.mcp.__main__ import main

        with tempfile.TemporaryDirectory() as tmp:
            buffer = io.StringIO()
            original_stdin = sys.stdin
            sys.stdin = io.StringIO("")
            try:
                with contextlib.redirect_stdout(buffer):
                    code = main(["--fake", "--out", tmp])
            finally:
                sys.stdin = original_stdin
            self.assertEqual(code, 0)
            # 没有协议消息（标准输入是空的）→ 标准输出必须是空的。
            self.assertEqual(buffer.getvalue(), "")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

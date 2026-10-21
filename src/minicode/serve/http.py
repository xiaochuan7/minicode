"""**对外形态的 HTTP 前端**（工单 M18）。

一个最小的 HTTP 服务：``POST /v1/actions`` 收一张键值表（动作请求），
回一张键值表（动作响应）。

⚠️ **本模块只做「解析 → 调服务 → 渲染」**：动作语义在
:mod:`minicode.serve.service` 里，与命令行前端**共用**（AC3）。

## 状态码怎么定

**HTTP 状态码是传输层的说法，协议错误码才是动作语义** —— 两者分开：

=================  =====================================================
HTTP 状态码         含义（响应体里的 ``error_code`` 仍是协议取值）
=================  =====================================================
``200``            请求被受理了（动作成功与否看响应体 ``ok``）
``400``            请求层被拒（版本对不上 / 形状不对 / 动作种类这一层不接）
                    → 零动作下发
``404``            路径不对
``405``            方法不对
=================  =====================================================

⚠️ **动作失败（目标找不到、点不动…）仍是 ``200``**：请求**被受理并执行了**，
失败是动作层的事实（协议错误码在响应体里）。这样「一次调用一次结论」，
调用方按 ``ok`` / ``error_code`` 分流，不必把协议错误码翻译成 HTTP 状态码。

⚠️ **每条响应都是同一个形状**（``protocol_version`` / ``ok`` / ``error_code`` /
``error_message`` / ``action`` / ``landing_point`` / ``record``）——连 404 与 405
也是：调用方**只有一种解析方式**。
"""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from minicode.action.errors import ErrorCode

from .protocol import PROTOCOL_VERSION, ActionResponse
from .service import ActionService

__all__ = ["build_server", "serve_forever", "close_server", "ACTIONS_PATH"]

#: 动作请求的路径。
ACTIONS_PATH = "/v1/actions"

#: **请求层**被拒的错误码（这些 → HTTP 400；动作层失败仍是 200）。
#:
#: ⚠️ 「动作失败」不在这里：目标点不动是**动作层**的事实，请求**被受理并执行了**。
_REJECTION_CODES = frozenset(
    {
        ErrorCode.INVALID_REQUEST.value,
        ErrorCode.PROTOCOL_VERSION_MISMATCH.value,
        ErrorCode.ACTION_NOT_IMPLEMENTED.value,
    }
)


def _rejected(message: str) -> dict[str, Any]:
    """一条**请求层**被拒的响应（形状与动作响应**完全一样**）。

    ⚠️ **调用方只有一种解析方式**：404 / 405 / 坏 JSON 与动作响应的字段一模一样，
    错误码取**协议里的取值**（这里用 ``invalid_request``）。
    """
    return ActionResponse(
        protocol_version=PROTOCOL_VERSION,
        ok=False,
        error_code=ErrorCode.INVALID_REQUEST.value,
        error_message=message,
    ).to_dict()


def close_server(server: ThreadingHTTPServer) -> None:
    """把服务停干净。

    ⚠️ **必须先 ``shutdown()`` 再 ``server_close()``**：只 ``server_close()``
    的话，阻塞在 ``accept()`` 上的 ``serve_forever`` 线程**醒不过来** ——
    进程会挂在那儿等一个永远不会来的连接。
    """
    server.shutdown()
    server.server_close()


def build_server(
    service: ActionService,
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
) -> ThreadingHTTPServer:
    """造一个 HTTP 服务（**只监听回环地址**：桌面控制不该暴露到网络上）。

    ⚠️ **非回环地址直接拒绝**（fail-closed）：这个接口能真的点鼠标，
    放到局域网里等于把电脑交出去 —— 不给「换个 host 就能开」的余地。
    """
    if host not in ("127.0.0.1", "localhost", "::1"):
        raise SystemExit(
            f"只允许监听回环地址（127.0.0.1 / localhost / ::1），收到 {host!r}："
            "桌面控制不能暴露到网络上"
        )

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            # 名字是 BaseHTTPRequestHandler 定的（不是本项目的命名口径）。
            if self.path != ACTIONS_PATH:
                self._send(404, _rejected(f"路径不对（动作请求在 {ACTIONS_PATH}）"))
                return
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                self._send(400, _rejected("Content-Length 不是整数"))
                return
            raw = self.rfile.read(length) if length > 0 else b""
            try:
                payload = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                self._send(400, _rejected(f"请求体不是合法 JSON：{exc}"))
                return
            response = service.execute(payload)
            # **请求层被拒 → 400**（版本对不上 / 形状不对，零动作下发）；
            # **动作执行了 → 200**（成败看响应体 ok 与协议错误码）。
            status = 400 if response.error_code in _REJECTION_CODES else 200
            self._send(status, response.to_dict())

        def do_GET(self) -> None:
            # 名字是 BaseHTTPRequestHandler 定的（不是本项目的命名口径）。
            self._send(405, _rejected("动作请求用 POST"))

        def _send(self, status: int, body: dict[str, Any]) -> None:
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, fmt: str, *args: Any) -> None:
            """请求日志打到标准输出（服务跑在命令行里，人要看得见）。"""
            print(f"[http] {self.address_string()} {fmt % args}")

    return ThreadingHTTPServer((host, port), Handler)


def serve_forever(
    log_directory: str | Path,
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
    fake: bool = False,
) -> int:
    """跑起 HTTP 服务（直到被中断）。

    ⚠️ **动真机前必须持有真机占用锁**（AGENTS.md）：服务运行期间**一直占着**
    真机 —— 人要用电脑就得先停服务。
    """
    from minicode.lock import acquire, release

    from .cli import build_service

    if fake:
        service = build_service(log_directory=log_directory, fake=True)
        holds_lock = False
    else:
        holds_lock = acquire("M18")
        try:
            service = build_service(log_directory=log_directory, fake=False)
        except BaseException:
            if holds_lock:
                release()
            raise
    try:
        server = build_server(service, host=host, port=port)
        print("========== 对外形态 · HTTP ==========")
        print(f"动作请求：POST http://{host}:{server.server_address[1]}{ACTIONS_PATH}")
        print("按 Ctrl-C 停服务。")
        print("===================================")
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            print("停了。")
        finally:
            close_server(server)
    finally:
        if holds_lock:
            release()
    return 0

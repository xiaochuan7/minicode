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
``403``            信任边界被拒（Host 不是回环 / Content-Type 不是
                    application/json / token 缺失或不配 / 服务端没配 token）
                    → 零动作下发
``404``            路径不对
``405``            方法不对
``413``            请求体超过 :data:`MAX_BODY_BYTES`
``503``            并发写请求超过 :data:`MAX_IN_FLIGHT`
=================  =====================================================

## 信任边界（为什么绑了回环还要这些门）

**只绑 127.0.0.1 不等于只有本机的人能驱动这个服务**：

* 任意网页可以对回环地址发``Content-Type: text/plain``的 POST ——
  这类「简单请求」**不触发 CORS 预检**，浏览器照样把请求打进来；
* DNS rebinding 之后，攻击者域名解析到 127.0.0.1，请求看起来就像同源。

所以写接口有四道门，全部 fail-closed（见 :func:`build_server`）：
``Host`` 必须回环 → ``Content-Type`` 必须 application/json →
必须带自定义头 ``X-Minicode-Token``（自定义头迫使跨域先过预检，
而预检**不应**——响应里永远没有 ``Access-Control-*``）→
服务端没配 token（环境变量 ``MINICODE_SERVE_TOKEN``）时**全部 403**。

⚠️ **动作失败（目标找不到、点不动…）仍是 ``200``**：请求**被受理并执行了**，
失败是动作层的事实（协议错误码在响应体里）。这样「一次调用一次结论」，
调用方按 ``ok`` / ``error_code`` 分流，不必把协议错误码翻译成 HTTP 状态码。

⚠️ **每条响应都是同一个形状**（``protocol_version`` / ``ok`` / ``error_code`` /
``error_message`` / ``action`` / ``landing_point`` / ``record``）——连 404 与 405
也是：调用方**只有一种解析方式**。
"""

from __future__ import annotations

import hmac
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from minicode.action.errors import ErrorCode

from .protocol import PROTOCOL_VERSION, ActionResponse
from .service import ActionService

__all__ = [
    "build_server",
    "serve_forever",
    "close_server",
    "ACTIONS_PATH",
    "TOKEN_HEADER",
    "MAX_BODY_BYTES",
    "MAX_IN_FLIGHT",
]

#: 动作请求的路径。
ACTIONS_PATH = "/v1/actions"

#: 写操作必须带的自定义鉴权头（服务端 token 从 ``MINICODE_SERVE_TOKEN`` 环境变量读）。
#:
#: ⚠️ **为什么是自定义头**：浏览器不允许网页随便带自定义头跨域 —— 带了就必须先过
#: 预检，而预检我们不应（响应里永远没有 ``Access-Control-*``），跨域写请求根本到不了这里。
TOKEN_HEADER = "X-Minicode-Token"

#: 请求体大小上限（字节）。动作请求是一张键值表，1 MiB 绰绰有余；
#: 超了的直接 413，不给「一个大 body 拖垮服务」的余地。
MAX_BODY_BYTES = 1_048_576

#: 同时在处理的写请求数上限；超了的直接 503（连接洪水拖不垮）。
MAX_IN_FLIGHT = 8

#: 答 413 前最多排空多少请求体。不排空就答，客户端可能还堵在 ``write()``
#: 上，先看到连接被掐断而不是 413；超过这个排水上限的（真洪水）直接关连接。
_DRAIN_LIMIT = 8 * 1024 * 1024

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


def _host_is_loopback(host_header: str | None) -> bool:
    """``Host`` 头是否指向回环地址（防 DNS rebinding：攻击者域名解析到 127.0.0.1）。

    ⚠️ **看 ``Host`` 头而不是看连接目标**：rebinding 攻击里 TCP 连接确实落在
    127.0.0.1 上，唯一能暴露「用户以为在访问谁的」的就是 ``Host`` 头本身。
    缺 ``Host``（HTTP/1.0 裸请求）按不信任处理。
    """
    if not host_header:
        return False
    host = host_header.strip().lower()
    if host.startswith("["):  # RFC 3986：IPv6 在 Host 里必须带方括号，如 [::1]:8765
        host = host[1:].split("]", 1)[0]
    elif host.count(":") == 1:  # host:port（IPv6 裸地址有两个以上冒号，不会被误切）
        host = host.rsplit(":", 1)[0]
    return host in ("127.0.0.1", "localhost", "::1")


def _content_type_is_json(content_type: str | None) -> bool:
    """``Content-Type`` 是否 ``application/json``（允许 ``; charset=…`` 后缀）。"""
    if not content_type:
        return False
    return content_type.split(";", 1)[0].strip().lower() == "application/json"


def build_server(
    service: ActionService,
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
    token: str | None = None,
    max_body_bytes: int = MAX_BODY_BYTES,
    max_in_flight: int = MAX_IN_FLIGHT,
) -> ThreadingHTTPServer:
    """造一个 HTTP 服务（**只监听回环地址**：桌面控制不该暴露到网络上）。

    ⚠️ **非回环地址直接拒绝**（fail-closed）：这个接口能真的点鼠标，
    放到局域网里等于把电脑交出去 —— 不给「换个 host 就能开」的余地。

    ⚠️ **写接口的信任边界**（fail-closed，四道门全过才碰服务）：
    ``Host`` 必须回环 → ``Content-Type`` 必须 ``application/json`` →
    必须带 :data:`TOKEN_HEADER` 且与 ``token`` 相等（常数时间比较）→
    ``token is None`` 时**所有写请求 403**（宁可起个没用的服务，
    也不给一个不设防的默认态）。
    """
    if host not in ("127.0.0.1", "localhost", "::1"):
        raise SystemExit(
            f"只允许监听回环地址（127.0.0.1 / localhost / ::1），收到 {host!r}："
            "桌面控制不能暴露到网络上"
        )

    server_token = token
    in_flight = threading.BoundedSemaphore(max_in_flight)

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            # 名字是 BaseHTTPRequestHandler 定的（不是本项目的命名口径）。
            if not _host_is_loopback(self.headers.get("Host")):
                self._send(
                    403,
                    _rejected(
                        "Host 不是回环地址（防 DNS rebinding）："
                        f"{self.headers.get('Host')!r} 不被接受"
                    ),
                )
                return
            if self.path != ACTIONS_PATH:
                self._send(404, _rejected(f"路径不对（动作请求在 {ACTIONS_PATH}）"))
                return
            if not _content_type_is_json(self.headers.get("Content-Type")):
                self._send(
                    403,
                    _rejected(
                        "Content-Type 必须是 application/json"
                        "（text/plain 这类简单跨域请求不许绕过预检）"
                    ),
                )
                return
            if server_token is None:
                self._send(
                    403,
                    _rejected(
                        "写接口未解锁：服务端未配置 token"
                        "（设 MINICODE_SERVE_TOKEN 后重启服务）"
                    ),
                )
                return
            supplied = self.headers.get(TOKEN_HEADER) or ""
            if not hmac.compare_digest(
                supplied.encode("utf-8"), server_token.encode("utf-8")
            ):
                self._send(403, _rejected(f"{TOKEN_HEADER} 缺失或不匹配"))
                return
            if not in_flight.acquire(blocking=False):
                self._send(503, _rejected("服务忙：并发写请求超过上限"))
                return
            try:
                try:
                    length = int(self.headers.get("Content-Length") or 0)
                except ValueError:
                    self._send(400, _rejected("Content-Length 不是整数"))
                    return
                if length > max_body_bytes:
                    # 先排一部分再答 413：让客户端把 write() 走完、看得到 413。
                    # 超过排水上限的真洪水不再陪它读，直接关连接。
                    drain_to = min(length, _DRAIN_LIMIT)
                    drained = 0
                    while drained < drain_to:
                        chunk = self.rfile.read(min(65536, drain_to - drained))
                        if not chunk:
                            break
                        drained += len(chunk)
                    if length > _DRAIN_LIMIT:
                        self.close_connection = True
                    self._send(413, _rejected(f"请求体超过上限 {max_body_bytes} 字节"))
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
            finally:
                in_flight.release()

        def do_GET(self) -> None:
            # 名字是 BaseHTTPRequestHandler 定的（不是本项目的命名口径）。
            if not _host_is_loopback(self.headers.get("Host")):
                self._send(403, _rejected("Host 不是回环地址（防 DNS rebinding）"))
                return
            self._send(405, _rejected("动作请求用 POST"))

        def do_OPTIONS(self) -> None:
            # CORS 预检**不应**：本服务没有跨域调用方，答了预检等于给浏览器发通行证。
            # ⚠️ 响应里**永远不带** ``Access-Control-*`` 头。
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

    ⚠️ **动真机前必须持有真机占用锁**（真机占用锁：人机互斥）：服务运行期间**一直占着**
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
        # P0-A：token 只从环境变量读（``MINICODE_SERVE_TOKEN``），
        # 不给命令行参数 —— token 出现在 argv 里会被 shell 历史与进程列表记住。
        token = os.environ.get("MINICODE_SERVE_TOKEN") or None
        server = build_server(service, host=host, port=port, token=token)
        print("========== 对外形态 · HTTP ==========")
        print(f"动作请求：POST http://{host}:{server.server_address[1]}{ACTIONS_PATH}")
        if token:
            print(f"写接口鉴权：已启用（{TOKEN_HEADER}）")
        else:
            print(
                "⚠️ 未配置 MINICODE_SERVE_TOKEN：写接口全部 403（fail-closed）。"
                "设置该环境变量并重启服务以解锁。"
            )
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

"""**独立 CLI + HTTP 暴露**的单测（工单 M18）。

只断言**对外可见**的行为：命令行的退出码与输出、HTTP 的状态码与响应体。
不测内部实现细节。

四条验收标准各对应一个测试类：

* :class:`CliSurfaceTest` —— AC1（CLI 能发起一次动作、读回结果与动作日志；
  退出码区分成功与失败）；
* :class:`HttpSurfaceTest` —— AC2（HTTP 能发起一次动作、读回结果；
  错误码是协议里的取值）；
* :class:`SameRequestSameResultTest` —— AC3（同一条请求经 CLI 与 HTTP 得到
  相同的动作与落点：两条入口共用**一个**实现）；
* :class:`ProtocolVersionTest` —— AC4（协议带版本号；版本不匹配明确报错）。

全程假桌面（假截图源 / 假控件树 / 假注入器）：不碰真桌面、不调模型、不花钱。
"""

from __future__ import annotations

import contextlib
import http.client as httpclient
import io
import json
import tempfile
import threading
import unittest
from pathlib import Path

from minicode.action.errors import ErrorCode
from minicode.action.fakes import FakeInputInjector
from minicode.serve import cli, http
from minicode.serve.fakes import FAKE_LANDING, FAKE_TARGET, fake_wiring
from minicode.serve.protocol import ActionRequest, RequestError
from minicode.serve.service import ActionService

#: 协议里的错误码取值集合（对外暴露的错误码必须是它的子集）。
_PROTOCOL_ERROR_CODES = {code.value for code in ErrorCode}


def _run_cli(argv: list[str]) -> tuple[int, str]:
    """跑一次命令行，拿到（退出码，标准输出）。"""
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        code = cli.main(argv)
    return code, buffer.getvalue()


def _service(directory: Path, injector: FakeInputInjector | None = None) -> ActionService:
    """一个跑在假桌面上的对外服务。"""
    wiring, _injector = fake_wiring(injector=injector)
    return ActionService(seam_factory=wiring, log_directory=directory)


class CliSurfaceTest(unittest.TestCase):
    """AC1：命令行能发起一次动作、读回结果与动作日志；退出码区分成功与失败。"""

    def test_the_cli_runs_one_action_and_reads_back_the_result_and_the_action_log(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, out = _run_cli(
                ["action", "--target", FAKE_TARGET, "--fake", "--out", tmp, "--json"]
            )
            self.assertEqual(code, 0)
            payload = json.loads(out)
            self.assertTrue(payload["ok"])
            self.assertIsNone(payload["error_code"])
            self.assertEqual(payload["landing_point"], list(FAKE_LANDING))
            self.assertEqual(payload["action"]["kind"], "click")
            self.assertIsNotNone(payload["record"])

            # 动作日志落盘了，且那条记录就是这次动作的。
            log_path = Path(tmp) / "action-records.jsonl"
            self.assertTrue(log_path.exists())
            records = [
                json.loads(line)
                for line in log_path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0]["target"]["name"], FAKE_TARGET)
            self.assertEqual(records[0]["protocol_version"], payload["protocol_version"])

    def test_the_cli_exit_code_tells_success_from_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, out = _run_cli(
                ["action", "--target", "树上没有的按钮", "--fake", "--out", tmp, "--json"]
            )
            self.assertNotEqual(code, 0)
            payload = json.loads(out)
            self.assertFalse(payload["ok"])
            self.assertEqual(payload["error_code"], ErrorCode.TARGET_NOT_FOUND.value)

    def test_the_cli_reads_the_action_log_back(self):
        with tempfile.TemporaryDirectory() as tmp:
            _run_cli(["action", "--target", FAKE_TARGET, "--fake", "--out", tmp, "--json"])
            code, out = _run_cli(["log", "--out", tmp, "--json"])
            self.assertEqual(code, 0)
            entries = json.loads(out)
            self.assertEqual(len(entries), 1)
            self.assertEqual(entries[0]["target"]["name"], FAKE_TARGET)

    def test_every_action_kind_the_seam_does_not_take_is_reported_not_crashed(self):
        """缝不接的 5 个种类（type / key / inspect / execute_code / ask_user）。

        ⚠️ **不许让异常逃到前端**（两轴审查抓出来的）：那样调用方只看到
        一段 traceback，拿不到协议错误码。
        """
        for kind in ("type", "key", "inspect", "execute_code", "ask_user"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as tmp:
                code, out = _run_cli(
                    [
                        "action",
                        "--target",
                        FAKE_TARGET,
                        "--action",
                        kind,
                        "--fake",
                        "--out",
                        tmp,
                        "--json",
                    ]
                )
                self.assertNotEqual(code, 0)
                payload = json.loads(out)
                self.assertFalse(payload["ok"])
                self.assertEqual(
                    payload["error_code"], ErrorCode.ACTION_NOT_IMPLEMENTED.value
                )

    def test_an_action_that_needs_no_target_is_accepted_without_one(self):
        """截图 / 等待**不需要定位目标**（协议里它们是「独立」动作种类）。"""
        with tempfile.TemporaryDirectory() as tmp:
            code, out = _run_cli(
                ["action", "--action", "wait", "--param", "ms=1", "--fake", "--out", tmp, "--json"]
            )
            self.assertEqual(code, 0)
            payload = json.loads(out)
            self.assertTrue(payload["ok"])
            self.assertIsNone(payload["landing_point"])


class HttpSurfaceTest(unittest.TestCase):
    """AC2：HTTP 能发起一次动作、读回结果；错误码是协议里的取值。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._dir = Path(self._tmp.name)
        self._service = _service(self._dir)
        self._server = http.build_server(self._service, host="127.0.0.1", port=0)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        self.addCleanup(self._stop)
        self.addCleanup(self._tmp.cleanup)

    def _stop(self) -> None:
        """把服务停干净。

        ⚠️ **先停服务再 join 线程**：反过来的话 ``join()`` 会一直等
        一个还阻塞在 ``accept()`` 上的线程（永远等不到）。
        """
        http.close_server(self._server)
        self._thread.join(timeout=10)

    def _post(self, payload: dict) -> tuple[int, dict]:
        connection = httpclient.HTTPConnection(*self._server.server_address, timeout=10)
        try:
            connection.request(
                "POST",
                "/v1/actions",
                body=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
            )
            response = connection.getresponse()
            return response.status, json.loads(response.read().decode("utf-8"))
        finally:
            connection.close()

    def test_the_http_surface_runs_one_action_and_reads_back_the_result(self):
        status, body = self._post(
            {"protocol": _version(), "target": FAKE_TARGET, "action": "click"}
        )
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])
        self.assertIsNone(body["error_code"])
        self.assertEqual(body["landing_point"], list(FAKE_LANDING))
        self.assertEqual(body["action"]["kind"], "click")

    def test_the_http_surface_reports_a_protocol_error_code_when_the_action_fails(self):
        status, body = self._post({"protocol": _version(), "target": "树上没有的按钮"})
        self.assertEqual(status, 200)
        self.assertFalse(body["ok"])
        self.assertEqual(body["error_code"], ErrorCode.TARGET_NOT_FOUND.value)
        self.assertIn(body["error_code"], _PROTOCOL_ERROR_CODES)

    def test_the_http_surface_writes_the_action_log(self):
        self._post({"protocol": _version(), "target": FAKE_TARGET})
        log_path = self._dir / "action-records.jsonl"
        self.assertTrue(log_path.exists())

    def test_the_http_surface_rejects_a_mismatched_protocol_version(self):
        status, body = self._post({"protocol": "cua.action/0", "target": FAKE_TARGET})
        self.assertEqual(status, 400)
        self.assertFalse(body["ok"])
        self.assertEqual(body["error_code"], ErrorCode.PROTOCOL_VERSION_MISMATCH.value)

    def test_the_http_surface_rejects_an_unsupported_action_kind(self):
        status, body = self._post(
            {"protocol": _version(), "target": FAKE_TARGET, "action": "teleport"}
        )
        self.assertEqual(status, 400)
        self.assertEqual(body["error_code"], ErrorCode.ACTION_NOT_IMPLEMENTED.value)

    def test_every_action_kind_the_seam_does_not_take_is_reported_not_crashed(self):
        """缝不接的 5 个种类（type / key / inspect / execute_code / ask_user）。

        ⚠️ **不许让异常逃到前端** —— 那样客户端只看到连接断掉，
        既没有状态码也没有错误码（两轴审查抓出来的）。
        """
        for kind in ("type", "key", "inspect", "execute_code", "ask_user"):
            with self.subTest(kind=kind):
                status, body = self._post(
                    {"protocol": _version(), "target": FAKE_TARGET, "action": kind}
                )
                self.assertEqual(status, 400)
                self.assertFalse(body["ok"])
                self.assertEqual(
                    body["error_code"], ErrorCode.ACTION_NOT_IMPLEMENTED.value
                )

    def test_an_action_that_needs_no_target_is_accepted_without_one(self):
        """截图 / 等待**不需要定位目标**（协议里它们是「独立」动作种类）。"""
        for kind in ("wait", "screenshot"):
            with self.subTest(kind=kind):
                status, body = self._post(
                    {"protocol": _version(), "action": kind, "params": {"ms": 1}}
                )
                self.assertEqual(status, 200)
                self.assertTrue(body["ok"])
                self.assertIsNone(body["landing_point"])

    def test_the_http_surface_rejects_a_malformed_body(self):
        status, body = self._post({"target": FAKE_TARGET})
        self.assertEqual(status, 400)
        self.assertFalse(body["ok"])

    def test_every_response_has_the_same_shape(self):
        """**每条响应都是同一个形状** —— 调用方只有一种解析方式。"""
        cases = [
            ({"target": FAKE_TARGET}, 400),
            ({"protocol": "cua.action/0", "target": FAKE_TARGET}, 400),
            ({"protocol": _version(), "target": FAKE_TARGET}, 200),
        ]
        for payload, expected in cases:
            with self.subTest(payload=payload):
                status, body = self._post(payload)
                self.assertEqual(status, expected)
                self.assertEqual(
                    set(body),
                    {
                        "protocol_version",
                        "ok",
                        "error_code",
                        "error_message",
                        "action",
                        "landing_point",
                        "record",
                    },
                )

    def test_the_http_surface_answers_unknown_paths_with_404(self):
        connection = httpclient.HTTPConnection(*self._server.server_address, timeout=10)
        try:
            connection.request(
                "POST",
                "/v1/nope",
                body=json.dumps({"protocol": _version(), "target": FAKE_TARGET}).encode(
                    "utf-8"
                ),
                headers={"Content-Type": "application/json"},
            )
            response = connection.getresponse()
            self.assertEqual(response.status, 404)
            # 形状与动作响应**一样**（含协议错误码）。
            body = json.loads(response.read().decode("utf-8"))
            self.assertEqual(body["protocol_version"], _version())
            self.assertFalse(body["ok"])
            self.assertEqual(body["error_code"], ErrorCode.INVALID_REQUEST.value)
        finally:
            connection.close()


class SameRequestSameResultTest(unittest.TestCase):
    """AC3：同一条请求经 CLI 与 HTTP 得到**相同的动作与落点**（同一个实现）。"""

    def test_the_same_request_gives_the_same_action_and_landing_point(self):
        with tempfile.TemporaryDirectory() as cli_dir, tempfile.TemporaryDirectory() as http_dir:
            _code, out = _run_cli(
                ["action", "--target", FAKE_TARGET, "--fake", "--out", cli_dir, "--json"]
            )
            cli_payload = json.loads(out)

            service = _service(Path(http_dir))
            server = http.build_server(service, host="127.0.0.1", port=0)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                connection = httpclient.HTTPConnection(*server.server_address, timeout=10)
                try:
                    connection.request(
                        "POST",
                        "/v1/actions",
                        body=json.dumps(
                            {
                                "protocol": _version(),
                                "target": FAKE_TARGET,
                                "action": "click",
                            }
                        ).encode("utf-8"),
                        headers={"Content-Type": "application/json"},
                    )
                    response = connection.getresponse()
                    http_payload = json.loads(response.read().decode("utf-8"))
                finally:
                    connection.close()
            finally:
                http.close_server(server)
                thread.join(timeout=10)

            self.assertEqual(cli_payload["action"], http_payload["action"])
            self.assertEqual(cli_payload["landing_point"], http_payload["landing_point"])
            self.assertEqual(cli_payload["ok"], http_payload["ok"])
            self.assertEqual(cli_payload["protocol_version"], http_payload["protocol_version"])


class ProtocolVersionTest(unittest.TestCase):
    """AC4：协议带版本号；版本不匹配时明确报错，而不是按旧语义猜。"""

    def test_the_request_carries_the_protocol_version(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, out = _run_cli(
                ["action", "--target", FAKE_TARGET, "--fake", "--out", tmp, "--json"]
            )
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(out)["protocol_version"], _version())

    def test_a_mismatched_version_is_reported_and_no_action_runs(self):
        injector = FakeInputInjector()
        with tempfile.TemporaryDirectory() as tmp:
            service = _service(Path(tmp), injector=injector)
            response = service.execute(
                {
                    "protocol": "cua.action/0",
                    "action": "click",
                    "target": FAKE_TARGET,
                }
            )
            self.assertFalse(response.ok)
            self.assertEqual(response.error_code, ErrorCode.PROTOCOL_VERSION_MISMATCH.value)
            # **没有按旧语义猜着执行**：一个输入事件都没下发。
            self.assertEqual(injector.events, [])

    def test_a_request_without_a_version_is_rejected(self):
        with self.assertRaises(RequestError) as caught:
            ActionRequest.from_mapping({"target": FAKE_TARGET})
        self.assertEqual(caught.exception.code, ErrorCode.INVALID_REQUEST)


def _version() -> str:
    """协议版本号（动作协议的唯一真值来源）。"""
    from minicode.action.protocol import PROTOCOL_VERSION

    return PROTOCOL_VERSION

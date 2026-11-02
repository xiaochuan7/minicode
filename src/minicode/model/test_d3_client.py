"""d3 客户端（V4）的单测：``/v1/systemone`` 的请求形状、响应解析、失败形态。

**零网络**：urlopen 换成桩（固定响应体 / 固定异常）。
"""

from __future__ import annotations

import json
import unittest
from unittest import mock

from minicode.model.config import D3Config
from minicode.model.d3 import D3CallRecord, D3Client, D3Error

_CONFIG = D3Config(
    base_url="https://gateway.example/api/v1",
    api_key="d3-secret-key",
    model="d3",
    source="test.env",
)


def _ok_body(**overrides):
    payload = {
        "answers": {
            "rerank": {
                "type": "choice",
                "choice": "2",
                "probabilities": {"1": 0.3, "2": 0.7, "3": 0.0},
                "confidence": 0.7,
            }
        },
        "usage": {"input_tokens": 100, "output_tokens": 0},
        "model": "d3",
        "object": "structured_decision",
    }
    payload.update(overrides)
    return json.dumps(payload).encode()


def _client(sink, body=b"", status=200, urlopen=None):
    client = D3Client(_CONFIG, sink)
    if urlopen is None:
        response = mock.MagicMock()
        response.status = status
        response.read.return_value = body
        response.__enter__.return_value = response
        urlopen = mock.MagicMock(return_value=response)
    return client, urlopen


class DecideTests(unittest.TestCase):
    """成功路径：请求形状 + 响应解析 + 日志。"""

    def test_posts_the_verified_systemone_shape(self):
        sink = InMemoryD3Log()
        client, urlopen = _client(sink, body=_ok_body())
        with mock.patch("minicode.model.d3.urllib.request.urlopen", urlopen):
            reply = client.decide("state 文本", "rerank", {"1": "甲", "2": "乙"})
        request = urlopen.call_args.args[0]
        self.assertEqual(request.full_url, "https://gateway.example/api/v1/systemone")
        payload = json.loads(request.data.decode("utf-8"))
        self.assertEqual(payload["model"], "d3")
        self.assertEqual(payload["state"], "state 文本")
        self.assertEqual(
            payload["questions"]["rerank"],
            {"type": "choice", "criteria": {"1": "甲", "2": "乙"}},
        )
        self.assertIn("d3-secret-key", request.headers["Authorization"])
        # 响应解析：概率 / confidence / argmax 原样带回。
        self.assertEqual(reply.probabilities, {"1": 0.3, "2": 0.7, "3": 0.0})
        self.assertEqual(reply.confidence, 0.7)
        self.assertEqual(reply.chosen, "2")
        self.assertEqual(reply.http_status, 200)

    def test_every_call_is_logged(self):
        sink = InMemoryD3Log()
        client, urlopen = _client(sink, body=_ok_body())
        with mock.patch("minicode.model.d3.urllib.request.urlopen", urlopen):
            client.decide("state", "rerank", {"1": "甲", "2": "乙", "3": "丙"})
        record = sink.records[-1]
        self.assertIsInstance(record, D3CallRecord)
        self.assertEqual(record.option_count, 3)
        self.assertEqual(record.confidence, 0.7)
        self.assertFalse(record.rate_limited)
        self.assertEqual(record.error, "")
        # 日志里没有 key（key 只在请求头，从不进日志）。
        self.assertNotIn("d3-secret-key", json.dumps(record.to_dict()))


class FailureTests(unittest.TestCase):
    """失败形态：429 / 5xx / 形状错 / 传输层 —— 全部如实抛，日志照样落。"""

    def _assert_error(self, urlopen, *, status_code, rate_limited):
        sink = InMemoryD3Log()
        client, urlopen = _client(sink, urlopen=urlopen)
        with mock.patch("minicode.model.d3.urllib.request.urlopen", urlopen):
            with self.assertRaises(D3Error) as ctx:
                client.decide("state", "rerank", {"1": "甲", "2": "乙"})
        self.assertEqual(ctx.exception.status, status_code)
        record = sink.records[-1]
        self.assertEqual(record.http_status, status_code)
        self.assertEqual(record.rate_limited, rate_limited)

    def test_429_is_marked_rate_limited(self):
        import urllib.error

        error = urllib.error.HTTPError(
            "https://gateway.example/api/v1/systemone",
            429,
            "Too Many Requests",
            {},
            io_bytes(b'{"error": {"message": "rate"}}'),
        )
        self._assert_error(
            mock.MagicMock(side_effect=error), status_code=429, rate_limited=True
        )

    def test_500_is_not_rate_limited(self):
        import urllib.error

        error = urllib.error.HTTPError(
            "https://gateway.example/api/v1/systemone",
            500,
            "Internal",
            {},
            io_bytes(b'{"error": {"message": "boom"}}'),
        )
        self._assert_error(
            mock.MagicMock(side_effect=error), status_code=500, rate_limited=False
        )

    def test_malformed_body_is_an_error(self):
        sink = InMemoryD3Log()
        client, urlopen = _client(sink, body=b'{"answers": {}}')
        with mock.patch("minicode.model.d3.urllib.request.urlopen", urlopen):
            with self.assertRaises(D3Error) as ctx:
                client.decide("state", "rerank", {"1": "甲"})
        self.assertEqual(ctx.exception.status, 200)
        self.assertIn("形状", str(ctx.exception))

    def test_transport_failure_has_unknown_status(self):
        sink = InMemoryD3Log()
        client, urlopen = _client(sink)
        urlopen.side_effect = OSError("网络不可达")
        with mock.patch("minicode.model.d3.urllib.request.urlopen", urlopen):
            with self.assertRaises(D3Error) as ctx:
                client.decide("state", "rerank", {"1": "甲"})
        self.assertIsNone(ctx.exception.status)
        self.assertIsNone(sink.records[-1].rate_limited)


def io_bytes(data: bytes):
    import io

    return io.BytesIO(data)


class InMemoryD3Log:
    """内存日志（单测用）。"""

    def __init__(self):
        self.records: list[D3CallRecord] = []

    def write(self, record: D3CallRecord) -> None:
        self.records.append(record)


if __name__ == "__main__":
    unittest.main()

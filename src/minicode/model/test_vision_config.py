"""V1 视觉通道可插拔的单测：视觉配置组（``MINICODE_VISION_*``）与结构化调用日志。

只测外部行为：

* **配置装载**：视觉组逐字段覆盖决策层、没配的字段回落（向后兼容）、
  ``origins`` 如实记录实际生效的键名、协议校验、决策层缺项照样报错；
* **结构化日志**：每次调用落 ``channel`` / 实际图尺寸 / 429 标记 / 端点主机名。

**零网络、零费用**：所有用例都不发真实请求（传输层换成固定响应体）。
"""

from __future__ import annotations

import base64
import json
import tempfile
import unittest
from pathlib import Path

from minicode.model.client import ModelClient
from minicode.model.config import (
    PROTOCOL_ANTHROPIC,
    PROTOCOL_OPENAI,
    VISION_CONFIG_KEYS,
    ModelConfigError,
    load_vision_config,
)
from minicode.model.errors import ModelSideError
from minicode.model.request_log import InMemoryRequestLog

#: 决策层（也是 V2 配对实验的对照臂）：一组 ``MINICODE_*`` 键。
_DECISION_ENV = """\
MINICODE_PROVIDER=openai
MINICODE_BASE_URL=https://opencode.ai/zen/go/v1
MINICODE_API_KEY=oc_sk_decision
MINICODE_MODEL=deepseek-v4.1-flash
MINICODE_SESSION_ID=sess-decision
MINICODE_HTTP_USER_AGENT=opencode/1.0.0
"""

#: 决策层 + 视觉组（实验臂走另一个网关、另一个模型）。
_VISION_ENV = _DECISION_ENV + """\
MINICODE_VISION_PROVIDER=openai
MINICODE_VISION_BASE_URL=https://developer.amd.com.cn/radeon/api/v1
MINICODE_VISION_API_KEY=rc_sk_vision
MINICODE_VISION_MODEL=Qwen3.8-27B
"""


def _write_env(tmp: str, text: str) -> Path:
    path = Path(tmp) / ".env"
    path.write_text(text, encoding="utf-8")
    return path


def _load_vision(text: str):
    with tempfile.TemporaryDirectory() as tmp:
        return load_vision_config(_write_env(tmp, text))


def _png_of(width: int, height: int) -> bytes:
    """构造一个只含 IHDR 宽高的最小 PNG（``png_image_size`` 只读这段）。"""
    return (
        b"\x89PNG\r\n\x1a\n"
        + b"\x00\x00\x00\x0d"
        + b"IHDR"
        + width.to_bytes(4, "big")
        + height.to_bytes(4, "big")
        + b"\x08\x02\x00\x00\x00"
        + b"\x00\x00\x00\x00"
    )


def _png_base64(width: int, height: int) -> str:
    return base64.b64encode(_png_of(width, height)).decode()


class _StubTransportClient(ModelClient):
    """把网络换成一个固定的响应体（**零网络、零费用**）。"""

    def __init__(self, config, sink, body: bytes, status: int = 200, **kwargs):
        super().__init__(config, sink, **kwargs)
        self._body = body
        self._status = status

    def _post(self, endpoint, headers, body):
        return self._status, self._body


class VisionConfigTest(unittest.TestCase):
    """视觉配置组：逐字段覆盖 + 逐字段回落。"""

    def test_without_vision_keys_behaves_exactly_like_decision(self):
        """一组 VISION 键都没配 → 与决策层同一份（向后兼容的契约）。"""
        decision = _load_vision(_DECISION_ENV)
        vision = _load_vision(_DECISION_ENV)
        self.assertEqual(vision.base_url, decision.base_url)
        self.assertEqual(vision.api_key, decision.api_key)
        self.assertEqual(vision.model, decision.model)
        self.assertEqual(vision.protocol, decision.protocol)
        self.assertEqual(vision.endpoint, decision.endpoint)
        # origins 指向决策层的键（「这个值到底从哪个键来」要能对账）。
        self.assertEqual(vision.origins["model"], "MINICODE_MODEL")
        self.assertEqual(vision.origins["base_url"], "MINICODE_BASE_URL")

    def test_vision_keys_override_field_by_field(self):
        """配了的字段覆盖，没配的字段回落决策层（两组键混用是合法形态）。"""
        env = _DECISION_ENV + "MINICODE_VISION_MODEL=Qwen3.8-27B\n"
        vision = _load_vision(env)
        self.assertEqual(vision.model, "Qwen3.8-27B")
        # api_key / base_url 没配 → 回落决策层。
        self.assertEqual(vision.api_key, "oc_sk_decision")
        self.assertEqual(
            vision.base_url, "https://opencode.ai/zen/go/v1"
        )
        self.assertEqual(vision.origins["model"], "MINICODE_VISION_MODEL")
        self.assertEqual(vision.origins["api_key"], "MINICODE_API_KEY")

    def test_full_vision_group_points_at_the_vision_gateway(self):
        vision = _load_vision(_VISION_ENV)
        self.assertEqual(
            vision.base_url, "https://developer.amd.com.cn/radeon/api/v1"
        )
        self.assertEqual(vision.api_key, "rc_sk_vision")
        self.assertEqual(vision.model, "Qwen3.8-27B")
        self.assertEqual(vision.protocol, PROTOCOL_OPENAI)
        self.assertEqual(
            vision.endpoint,
            "https://developer.amd.com.cn/radeon/api/v1/chat/completions",
        )
        for field, key in VISION_CONFIG_KEYS.items():
            self.assertEqual(vision.origins[field], key)

    def test_vision_protocol_override_changes_endpoint(self):
        """视觉协议可以与决策层不同（覆盖 protocol 字段）。"""
        env = _DECISION_ENV + "MINICODE_VISION_PROVIDER=anthropic\n"
        vision = _load_vision(env)
        self.assertEqual(vision.protocol, PROTOCOL_ANTHROPIC)
        # 端点 = base_url + 协议路径（base 里已有 /v1 → 拼出 /v1/v1/messages，
        # 这正是「端点按协议拼」的字面行为；base_url 该怎么填由配置的人负责）。
        self.assertEqual(
            vision.endpoint, "https://opencode.ai/zen/go/v1/v1/messages"
        )
        # 决策层还是 openai。
        decision = _load_vision(_DECISION_ENV)
        self.assertEqual(decision.protocol, PROTOCOL_OPENAI)

    def test_invalid_vision_provider_raises(self):
        env = _DECISION_ENV + "MINICODE_VISION_PROVIDER=grpc\n"
        with self.assertRaises(ModelConfigError) as ctx:
            _load_vision(env)
        message = str(ctx.exception)
        self.assertIn("grpc", message)
        self.assertIn("anthropic / openai", message)

    def test_decision_keys_missing_still_raises(self):
        """决策层缺项照样报错 —— 视觉组**不替决策层兜底**。"""
        env = (
            "MINICODE_MODEL=deepseek-v4.1-flash\n"
            "MINICODE_VISION_PROVIDER=openai\n"
            "MINICODE_VISION_BASE_URL=https://developer.amd.com.cn/radeon/api/v1\n"
            "MINICODE_VISION_API_KEY=rc_sk_vision\n"
            "MINICODE_VISION_MODEL=Qwen3.8-27B\n"
        )
        with self.assertRaises(ModelConfigError) as ctx:
            _load_vision(env)
        message = str(ctx.exception)
        self.assertIn("MINICODE_BASE_URL", message)
        self.assertIn("MINICODE_API_KEY", message)

    def test_session_header_does_not_fall_back_user_agent_does(self):
        """``x-opencode-*`` 是网关语义的头：**不回落**（发给别的网关是噪声）；
        UA 是过边缘层的通行证：**回落**。"""
        vision = _load_vision(_VISION_ENV)
        # 决策层有 session_id=sess-decision，视觉组没配 → 视觉客户端不发这个头。
        self.assertEqual(vision.session_id, "")
        # UA 没配 VISION 键 → 回落决策层的。
        self.assertEqual(vision.user_agent, "opencode/1.0.0")

    def test_vision_session_and_user_agent_keys_take_effect(self):
        env = _VISION_ENV + (
            "MINICODE_VISION_SESSION_ID=sess-vision\n"
            "MINICODE_VISION_HTTP_USER_AGENT=Mozilla/5.0 vision\n"
        )
        vision = _load_vision(env)
        self.assertEqual(vision.session_id, "sess-vision")
        self.assertEqual(vision.user_agent, "Mozilla/5.0 vision")


class VisionCallLogTest(unittest.TestCase):
    """结构化视觉调用日志：channel / 图尺寸 / 429 标记 / 端点主机名。"""

    def _client(self, sink, status=200, body=None, **kwargs):
        config = _load_vision(_VISION_ENV)
        if body is None:
            body = json.dumps(
                {"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}]}
            ).encode()
        return _StubTransportClient(
            config, sink, body, status=status, channel="vision", **kwargs
        )

    def test_every_call_is_stamped_with_channel_image_size_and_host(self):
        sink = InMemoryRequestLog()
        client = self._client(sink)
        client.complete("定位目标", effort="low", image_base64=_png_base64(640, 480))
        record = sink.records[-1]
        self.assertEqual(record.channel, "vision")
        self.assertEqual(record.model, "Qwen3.8-27B")
        # 图尺寸 = **实际发出的那张图**的 PNG 直读（不是估计）。
        self.assertEqual(record.image_width_px, 640)
        self.assertEqual(record.image_height_px, 480)
        self.assertFalse(record.rate_limited)
        # 主机名不含路径、不含任何凭据。
        self.assertEqual(record.endpoint_host, "developer.amd.com.cn")
        self.assertNotIn("rc_sk_vision", record.endpoint)
        self.assertNotIn("rc_sk_vision", record.endpoint_host)

    def test_text_only_call_records_no_image_size(self):
        sink = InMemoryRequestLog()
        client = self._client(sink)
        client.complete("只回复：就绪", effort="low")
        record = sink.records[-1]
        self.assertIsNone(record.image_width_px)
        self.assertIsNone(record.image_height_px)

    def test_rate_limited_flag_is_true_on_429(self):
        """429 → ``rate_limited=True``（与模型失败**分开计数**的口径）。"""
        sink = InMemoryRequestLog()
        body = json.dumps(
            {"error": {"type": "token_rate_limit_exceeded", "message": "慢一点"}}
        ).encode()
        client = self._client(sink, status=429, body=body)
        with self.assertRaises(ModelSideError):
            client.complete("定位目标", effort="low", image_base64=_png_base64(32, 32))
        record = sink.records[-1]
        self.assertEqual(record.http_status, 429)
        self.assertTrue(record.rate_limited)
        self.assertEqual(record.channel, "vision")

    def test_transport_failure_leaves_rate_limited_unknown(self):
        """传输层失败（没拿到状态码）→ ``rate_limited=None``（不知道就是不知道）。"""
        sink = InMemoryRequestLog()
        config = _load_vision(_VISION_ENV)
        from minicode.model.errors import ModelTransportError

        class _Broken(ModelClient):
            def _post(self, endpoint, headers, body):
                raise ModelTransportError(f"请求 {endpoint} 失败（假）")

        client = _Broken(config, sink, channel="vision")
        with self.assertRaises(ModelTransportError):
            client.complete("定位目标", effort="low")
        record = sink.records[-1]
        self.assertIsNone(record.http_status)
        self.assertIsNone(record.rate_limited)
        self.assertEqual(record.channel, "vision")


if __name__ == "__main__":
    unittest.main()

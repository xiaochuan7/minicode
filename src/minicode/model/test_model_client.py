"""模型客户端（M1）的单测。

只测外部行为：配置装载、档位强制、请求构造、响应解析、请求日志、假实现的失败形态。
**两套协议**（``anthropic`` / ``openai``）的 URL、头、正文、图像字段形状与响应解析
各有覆盖 —— 这是发布版「换任意主流大模型 API 都能跑」的合同。

**零网络、零费用**：所有用例都不发真实请求——真调 API 的四条回归在
``python -m minicode.model.regressions``（单独入口，不被单测命令带跑）。
"""

from __future__ import annotations

import base64
import contextlib
import io
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from minicode.model.client import (
    ModelClient,
    build_headers,
    build_request_body,
    image_tokens_of,
    parse_response,
    require_effort,
)
from minicode.model.config import (
    DEFAULT_USER_AGENT,
    PROTOCOL_ANTHROPIC,
    PROTOCOL_OPENAI,
    PROTOCOLS,
    ModelConfig,
    find_env_file,
    load_config,
    parse_env_file,
    repo_root,
    resolve_endpoint,
)
from minicode.model.effort import EFFORT_VALUES, Effort
from minicode.model.errors import (
    EffortRequiredError,
    ModelConfigError,
    ModelError,
    ModelSideError,
    ModelTransportError,
    NoTextOutputError,
)
from minicode.model.fakes import FakeModelClient, no_text_output_outcome
from minicode.model.ports import ModelCaller
from minicode.model.request_log import (
    InMemoryRequestLog,
    JsonlRequestLog,
    build_request_log,
    count_unset_effort,
)

_PACKAGE_DIR = Path(__file__).resolve().parent

#: 客户端侧的模块（标度常量只许出现在诊断脚本里，不许出现在客户端里）。
_CLIENT_MODULES = (
    "client.py",
    "config.py",
    "effort.py",
    "errors.py",
    "fakes.py",
    "ports.py",
    "request_log.py",
)

#: 旧键（Anthropic 时代）—— 继续可用，缺省协议就是 anthropic。
_VALID_ENV = """\
MINICODE_ANTHROPIC_BASE_URL=https://opencode.ai/zen/go
MINICODE_ANTHROPIC_API_KEY=key-from-repo-env
MINICODE_MODEL=minimax-m3.1-flash-free
MINICODE_SESSION_ID=dsh-union-alpha
MINICODE_HTTP_USER_AGENT=opencode/1.0.0
MINICODE_HTTP_CLIENT=cli
"""

#: 新键（通用 provider）—— 只填必需的三个，可选键全部缺省。
_GENERIC_ENV = """\
MINICODE_BASE_URL=https://api.deepseek.com/v1
MINICODE_API_KEY=sk-1234567890wxyz
MINICODE_MODEL=deepseek-chat
"""


def _config(**overrides: object) -> ModelConfig:
    values: dict[str, object] = {
        "base_url": "https://opencode.ai/zen/go",
        "api_key": "key-from-repo-env",
        "model": "minimax-m3.1-flash-free",
        "session_id": "dsh-union-alpha",
        "user_agent": "opencode/1.0.0",
        "client_header": "cli",
    }
    values.update(overrides)
    return ModelConfig(**values)


def _write_env(tmp: str, text: str) -> Path:
    path = Path(tmp) / ".env"
    path.write_text(text, encoding="utf-8")
    return path


def _load_text(text: str) -> ModelConfig:
    with tempfile.TemporaryDirectory() as tmp:
        return load_config(_write_env(tmp, text))


def _png_of(width: int, height: int) -> bytes:
    """构造一个只到 IHDR 宽高的最小 PNG（``png_image_size`` 只读这段）。

    故意不生成真实像素数据：图像 token 估计只依赖尺寸，不依赖画面内容。
    """
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


def _anthropic_body(text: str = '{"x": 1234, "y": 5678}') -> bytes:
    return json.dumps({"content": [{"type": "text", "text": text}]}).encode()


def _openai_body(**overrides: object) -> bytes:
    """OpenAI 兼容的 200 响应体（默认：有文本 + 有思考链 + 完整 usage）。"""
    payload: dict[str, object] = {
        "model": "gpt-x",
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": '{"x": 1, "y": 2}',
                },
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": 900,
            "completion_tokens": 40,
            "completion_tokens_details": {"reasoning_tokens": 32},
        },
    }
    payload.update(overrides)
    return json.dumps(payload).encode()


class _HandleSpy:
    """包一层文件句柄：记录 ``write`` 写了几次、每次多少字节、有没有 ``flush``。"""

    def __init__(self, handle):
        self._handle = handle
        self.writes: list[bytes] = []
        self.flushed = False

    def write(self, data):
        self.writes.append(data)
        return self._handle.write(data)

    def flush(self):
        self.flushed = True
        return self._handle.flush()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self._handle.close()
        return False


class _StubTransportClient(ModelClient):
    """把网络换成一个固定的响应体（**零网络、零费用**）。"""

    def __init__(self, config: ModelConfig, sink, body: bytes, status: int = 200):
        super().__init__(config, sink)
        self._body = body
        self._status = status
        #: 每次发出去的请求体（用来断言「请求体里到底带了什么」）。
        self.sent_bodies: list[dict] = []
        #: 每次发出去的请求头与端点。
        self.sent_headers: list[dict] = []
        self.sent_endpoints: list[str] = []

    def _post(self, endpoint, headers, body):
        self.sent_endpoints.append(endpoint)
        self.sent_headers.append(dict(headers))
        self.sent_bodies.append(dict(body))
        return self._status, self._body


class _TimeoutTransportClient(_StubTransportClient):
    """``_post`` 抛传输错误（模拟超时 / 网络不可达）。"""

    def _post(self, endpoint, headers, body):
        raise ModelTransportError(f"请求 {endpoint} 超时（假）")


class ConfigTest(unittest.TestCase):
    """凭据单一来源：仓库 ``.env`` 是唯一的凭据来源。"""

    def test_loads_every_required_item_from_the_env_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = load_config(_write_env(tmp, _VALID_ENV))
        self.assertEqual(config.base_url, "https://opencode.ai/zen/go")
        self.assertEqual(config.api_key, "key-from-repo-env")
        self.assertEqual(config.model, "minimax-m3.1-flash-free")
        self.assertEqual(config.session_id, "dsh-union-alpha")
        self.assertEqual(config.user_agent, "opencode/1.0.0")
        self.assertEqual(config.client_header, "cli")
        # 旧键-only 的配置按缺省协议 anthropic 起，端点仍是 Messages。
        self.assertEqual(config.protocol, PROTOCOL_ANTHROPIC)
        self.assertEqual(
            config.endpoint, "https://opencode.ai/zen/go/v1/messages"
        )
        self.assertEqual(config.origins["base_url"], "MINICODE_ANTHROPIC_BASE_URL")
        self.assertEqual(config.origins["api_key"], "MINICODE_ANTHROPIC_API_KEY")

    def test_missing_api_key_names_both_the_new_and_legacy_keys(self):
        """缺端点/密钥 → 报错要**同时点名新旧两个键**（可读）。"""
        with tempfile.TemporaryDirectory() as tmp:
            path = _write_env(
                tmp,
                "MINICODE_ANTHROPIC_BASE_URL=https://opencode.ai/zen/go\n"
                "MINICODE_MODEL=minimax-m3.1-flash-free\n",
            )
            with self.assertRaises(ModelConfigError) as ctx:
                load_config(path)
        message = str(ctx.exception)
        self.assertIn("MINICODE_API_KEY", message)
        self.assertIn("MINICODE_ANTHROPIC_API_KEY", message)
        # 会话标识 / 两个兼容头 / UA 现在是**可选**的，不该再被点名。
        for optional in (
            "MINICODE_SESSION_ID",
            "MINICODE_HTTP_USER_AGENT",
            "MINICODE_HTTP_CLIENT",
        ):
            self.assertNotIn(optional, message)

    def test_empty_value_counts_as_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = _write_env(
                tmp, _VALID_ENV.replace("MINICODE_MODEL=minimax-m3.1-flash-free", "MINICODE_MODEL=")
            )
            with self.assertRaises(ModelConfigError) as ctx:
                load_config(path)
        self.assertIn("MINICODE_MODEL", str(ctx.exception))

    def test_missing_env_file_is_reported_not_silently_defaulted(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "no-such.env"
            with self.assertRaises(ModelConfigError) as ctx:
                load_config(missing)
        self.assertIn(".env", str(ctx.exception))

    def test_env_parsing_ignores_comments_and_blank_lines(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".env"
            path.write_text("# 注释\n\nMINICODE_MODEL='m'\n", encoding="utf-8")
            values = parse_env_file(path)
        self.assertEqual(values, {"MINICODE_MODEL": "m"})

    def test_client_package_has_no_second_credential_source(self):
        """客户端里不允许出现第二个凭据来源（用户目录下的凭据文件）。"""
        for path in sorted(_PACKAGE_DIR.glob("*.py")):
            if path.name.startswith("test_"):
                continue
            text = path.read_text(encoding="utf-8")
            self.assertNotIn(".credentials.yaml", text, path.name)
            self.assertNotIn(".dsh", text, path.name)

    def test_env_file_is_only_looked_up_in_the_repo_root(self):
        """不从当前工作目录向上找 —— 那会读到仓库外的 .env（第二个凭据来源）。"""
        self.assertEqual(find_env_file().parent, repo_root())
        self.assertTrue((repo_root() / "pyproject.toml").is_file())

    def test_temperature_defaults_to_zero_when_the_env_does_not_mention_it(self):
        """``MINICODE_TEMPERATURE`` 是**可选**的：缺省 0（关采样）。"""
        with tempfile.TemporaryDirectory() as tmp:
            config = load_config(_write_env(tmp, _VALID_ENV))
        self.assertEqual(config.temperature, 0.0)

    def test_temperature_can_be_overridden_from_the_env(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = load_config(_write_env(tmp, _VALID_ENV + "MINICODE_TEMPERATURE=0.3\n"))
        self.assertEqual(config.temperature, 0.3)

    def test_non_numeric_temperature_is_refused_by_name(self):
        """给了但不是数字 → **显式报错**，不静默按 0 跑。"""
        with tempfile.TemporaryDirectory() as tmp:
            path = _write_env(tmp, _VALID_ENV + "MINICODE_TEMPERATURE=hot\n")
            with self.assertRaises(ModelConfigError) as ctx:
                load_config(path)
        self.assertIn("MINICODE_TEMPERATURE", str(ctx.exception))

    def test_config_object_defaults_to_zero_temperature(self):
        self.assertEqual(_config().temperature, 0.0)


class ProtocolConfigTest(unittest.TestCase):
    """两套配置键并存：新键优先、旧键兜底；协议与可选键都可测。"""

    def test_old_keys_only_still_start_as_anthropic(self):
        config = _load_text(_VALID_ENV)
        self.assertEqual(config.protocol, PROTOCOL_ANTHROPIC)
        self.assertEqual(config.origins["base_url"], "MINICODE_ANTHROPIC_BASE_URL")
        self.assertEqual(config.origins["api_key"], "MINICODE_ANTHROPIC_API_KEY")

    def test_new_keys_override_the_old_keys(self):
        env = _VALID_ENV + (
            "MINICODE_BASE_URL=https://api.anthropic.com\n"
            "MINICODE_API_KEY=sk-new-key-1234\n"
            "MINICODE_MODEL=claude-sonnet-4-5\n"
        )
        config = _load_text(env)
        self.assertEqual(config.base_url, "https://api.anthropic.com")
        self.assertEqual(config.api_key, "sk-new-key-1234")
        self.assertEqual(config.model, "claude-sonnet-4-5")
        self.assertEqual(config.origins["base_url"], "MINICODE_BASE_URL")
        self.assertEqual(config.origins["api_key"], "MINICODE_API_KEY")
        self.assertEqual(config.endpoint, "https://api.anthropic.com/v1/messages")

    def test_missing_both_key_pairs_names_every_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = _write_env(tmp, "MINICODE_MODEL=m\n")
            with self.assertRaises(ModelConfigError) as ctx:
                load_config(path)
        message = str(ctx.exception)
        for key in (
            "MINICODE_BASE_URL",
            "MINICODE_ANTHROPIC_BASE_URL",
            "MINICODE_API_KEY",
            "MINICODE_ANTHROPIC_API_KEY",
        ):
            self.assertIn(key, message)

    def test_invalid_provider_lists_the_allowed_values(self):
        env = _GENERIC_ENV + "MINICODE_PROVIDER=deepseek\n"
        with self.assertRaises(ModelConfigError) as ctx:
            _load_text(env)
        message = str(ctx.exception)
        self.assertIn("MINICODE_PROVIDER", message)
        for allowed in PROTOCOLS:
            self.assertIn(allowed, message)

    def test_session_and_compat_headers_are_optional(self):
        config = _load_text(_GENERIC_ENV)
        self.assertEqual(config.session_id, "")
        self.assertEqual(config.client_header, "")
        self.assertEqual(config.user_agent, DEFAULT_USER_AGENT)
        for field_name in ("session_id", "client_header", "user_agent"):
            self.assertEqual(config.origins[field_name], "")

    def test_provider_openai_selects_the_openai_endpoint(self):
        config = _load_text(_GENERIC_ENV + "MINICODE_PROVIDER=openai\n")
        self.assertEqual(config.protocol, PROTOCOL_OPENAI)
        self.assertEqual(config.origins["protocol"], "MINICODE_PROVIDER")
        self.assertEqual(
            config.endpoint, "https://api.deepseek.com/v1/chat/completions"
        )

    def test_describe_masks_the_key_and_names_its_source(self):
        config = _load_text(_GENERIC_ENV + "MINICODE_PROVIDER=openai\n")
        text = config.describe()
        self.assertIn("protocol=openai", text)
        self.assertIn("endpoint=https://api.deepseek.com/v1/chat/completions", text)
        self.assertIn("api_key=sk-1****wxyz", text)
        self.assertIn("来源 MINICODE_API_KEY", text)
        self.assertNotIn("sk-1234567890wxyz", text)

    def test_invalid_protocol_on_a_direct_config_is_refused(self):
        with self.assertRaises(ModelConfigError) as ctx:
            _config(protocol="gemini")
        for allowed in PROTOCOLS:
            self.assertIn(allowed, str(ctx.exception))


class EndpointRuleTest(unittest.TestCase):
    """端点按协议拼：anthropic → /v1/messages；openai → /chat/completions。"""

    def test_anthropic_endpoint_is_messages(self):
        self.assertEqual(
            resolve_endpoint("https://api.anthropic.com"),
            "https://api.anthropic.com/v1/messages",
        )

    def test_openai_endpoint_is_chat_completions(self):
        self.assertEqual(
            resolve_endpoint("https://api.deepseek.com/v1", PROTOCOL_OPENAI),
            "https://api.deepseek.com/v1/chat/completions",
        )

    def test_internal_gateway_url_without_the_go_suffix_is_accepted(self):
        """取消「必须以 /go 结尾」的内部网关规则（会挡住通用端点）。"""
        self.assertEqual(
            resolve_endpoint("https://opencode.ai/zen"),
            "https://opencode.ai/zen/v1/messages",
        )

    def test_trailing_slash_is_tolerated(self):
        self.assertEqual(
            resolve_endpoint("https://opencode.ai/zen/go/"),
            "https://opencode.ai/zen/go/v1/messages",
        )
        self.assertEqual(
            resolve_endpoint("https://api.openai.com/v1/", PROTOCOL_OPENAI),
            "https://api.openai.com/v1/chat/completions",
        )

    def test_empty_base_url_is_refused_by_naming_the_keys(self):
        with self.assertRaises(ModelConfigError) as ctx:
            resolve_endpoint("   ")
        message = str(ctx.exception)
        self.assertIn("MINICODE_BASE_URL", message)
        self.assertIn("MINICODE_ANTHROPIC_BASE_URL", message)

    def test_unknown_protocol_is_refused(self):
        with self.assertRaises(ModelConfigError) as ctx:
            resolve_endpoint("https://example.invalid", "gemini")
        self.assertIn("gemini", str(ctx.exception))


class EffortTest(unittest.TestCase):
    """思考档位必须显式给出（五档，缺省即最高——所以省略就报错）。"""

    def test_five_levels(self):
        self.assertEqual(EFFORT_VALUES, ("low", "medium", "high", "xhigh", "max"))

    def test_omitting_effort_is_an_error_not_a_silent_default(self):
        client = ModelClient(_config(), InMemoryRequestLog())
        with self.assertRaises(EffortRequiredError) as ctx:
            client.complete("只回复两个字：就绪")
        message = str(ctx.exception)
        for level in EFFORT_VALUES:
            self.assertIn(level, message)

    def test_unknown_effort_value_is_refused(self):
        client = ModelClient(_config(), InMemoryRequestLog())
        with self.assertRaises(EffortRequiredError):
            client.complete("hi", effort="turbo")

    def test_string_alias_of_a_level_is_accepted(self):
        self.assertEqual(require_effort("low"), Effort.LOW)


class AnthropicRequestConstructionTest(unittest.TestCase):
    """anthropic 的请求构造：头集合、请求体、档位字段（**逐字保持原实现**）。"""

    def test_all_required_headers_are_present(self):
        headers = build_headers(_config())
        self.assertEqual(headers["user-agent"], "opencode/1.0.0")
        self.assertEqual(headers["x-opencode-client"], "cli")
        self.assertEqual(headers["x-api-key"], "key-from-repo-env")
        self.assertEqual(headers["x-opencode-session"], "dsh-union-alpha")
        self.assertEqual(headers["anthropic-version"], "2023-06-01")
        self.assertEqual(headers["content-type"], "application/json")

    def test_compat_headers_are_omitted_when_the_config_is_empty(self):
        headers = build_headers(
            _config(session_id="", client_header="", user_agent=DEFAULT_USER_AGENT)
        )
        self.assertNotIn("x-opencode-session", headers)
        self.assertNotIn("x-opencode-client", headers)
        self.assertEqual(headers["user-agent"], DEFAULT_USER_AGENT)

    def test_body_carries_the_effort_field_the_server_actually_supports(self):
        body = build_request_body(
            model="minimax-m3.1-flash-free",
            prompt="hi",
            effort=Effort.LOW,
            max_tokens=512,
        )
        self.assertEqual(body["output_config"], {"effort": "low"})

    def test_body_carries_temperature_zero_by_default(self):
        """请求体**显式**带 temperature=0 —— 不带就走服务端缺省（有采样）。"""
        body = build_request_body(model="m", prompt="hi", effort=Effort.LOW, max_tokens=512)
        self.assertEqual(body["temperature"], 0)

    def test_body_temperature_can_be_set_explicitly(self):
        body = build_request_body(
            model="m",
            prompt="hi",
            effort=Effort.LOW,
            max_tokens=512,
            temperature=0.3,
        )
        self.assertEqual(body["temperature"], 0.3)

    def test_client_sends_the_configured_temperature(self):
        """客户端把配置里的 temperature 传进请求体。"""
        client = _StubTransportClient(_config(), InMemoryRequestLog(), _anthropic_body())
        client.complete("hi", effort=Effort.LOW, max_tokens=64)
        self.assertEqual(client.sent_bodies[0]["temperature"], 0)

        hot = _config(temperature=0.5)
        client = _StubTransportClient(hot, InMemoryRequestLog(), _anthropic_body())
        client.complete("hi", effort=Effort.LOW, max_tokens=64)
        self.assertEqual(client.sent_bodies[0]["temperature"], 0.5)

    def test_body_never_uses_the_ignored_alias(self):
        body = build_request_body(model="m", prompt="hi", effort=Effort.LOW, max_tokens=512)
        self.assertNotIn("reasoning_effort", body)
        self.assertNotIn("thinking", body)

    def test_body_omits_the_effort_field_when_effort_is_none(self):
        body = build_request_body(model="m", prompt="hi", effort=None, max_tokens=512)
        self.assertNotIn("output_config", body)

    def test_thinking_disabled_does_not_touch_the_effort_field(self):
        body = build_request_body(
            model="minimax-m3.1-flash-free",
            prompt="hi",
            effort=Effort.LOW,
            max_tokens=512,
            thinking_disabled=True,
        )
        self.assertEqual(body["output_config"], {"effort": "low"})
        self.assertEqual(body["thinking"], {"type": "disabled"})
        self.assertEqual(body["model"], "minimax-m3.1-flash-free")

    def test_image_content_block_is_base64_shaped(self):
        body = build_request_body(
            model="m",
            prompt="hi",
            effort=Effort.LOW,
            max_tokens=512,
            image_base64="QUJD",
        )
        content = body["messages"][0]["content"]
        self.assertEqual(content[0]["type"], "image")
        self.assertEqual(content[0]["source"]["data"], "QUJD")
        self.assertEqual(content[0]["source"]["media_type"], "image/png")
        self.assertEqual(content[1]["type"], "text")

    def test_client_source_has_no_scale_constants(self):
        """客户端里不出现任何标度常量（量纲只能来自标定探针）。

        唯一允许出现 ``1000`` 的地方是耗时换算（秒 → 毫秒），它出现在
        带 ``latency_ms`` 的行上 —— 那不是坐标的标度。
        """
        for name in _CLIENT_MODULES:
            lines = (_PACKAGE_DIR / name).read_text(encoding="utf-8").splitlines()
            for number, line in enumerate(lines, start=1):
                for needle in ("1000", "999"):
                    if needle in line:
                        self.assertIn(
                            "latency_ms",
                            line,
                            f"{name}:{number} 出现了疑似标度常量 {needle}：{line.strip()}",
                        )


class OpenAIRequestConstructionTest(unittest.TestCase):
    """openai 的请求构造：Bearer 头、文本在前图片在后、不猜思考字段。"""

    def test_headers_use_bearer_and_no_anthropic_headers(self):
        headers = build_headers(_config(protocol=PROTOCOL_OPENAI))
        self.assertEqual(
            headers["authorization"], "Bearer key-from-repo-env"
        )
        self.assertNotIn("x-api-key", headers)
        self.assertNotIn("anthropic-version", headers)
        self.assertEqual(headers["content-type"], "application/json")
        self.assertEqual(headers["accept"], "application/json")
        self.assertEqual(headers["user-agent"], "opencode/1.0.0")

    def test_compat_headers_are_optional_for_gateway_endpoints(self):
        with_compat = build_headers(_config(protocol=PROTOCOL_OPENAI))
        self.assertEqual(with_compat["x-opencode-session"], "dsh-union-alpha")
        self.assertEqual(with_compat["x-opencode-client"], "cli")

        without = build_headers(
            _config(protocol=PROTOCOL_OPENAI, session_id="", client_header="")
        )
        self.assertNotIn("x-opencode-session", without)
        self.assertNotIn("x-opencode-client", without)

    def test_body_puts_text_first_then_image_url(self):
        body = build_request_body(
            model="deepseek-chat",
            prompt="hi",
            effort=Effort.LOW,
            max_tokens=512,
            image_base64="QUJD",
            protocol=PROTOCOL_OPENAI,
        )
        content = body["messages"][0]["content"]
        self.assertEqual(content[0], {"type": "text", "text": "hi"})
        self.assertEqual(content[1]["type"], "image_url")
        self.assertEqual(
            content[1]["image_url"]["url"], "data:image/png;base64,QUJD"
        )
        self.assertEqual(body["model"], "deepseek-chat")
        self.assertEqual(body["max_tokens"], 512)
        self.assertEqual(body["temperature"], 0)

    def test_body_never_guesses_thinking_fields(self):
        body = build_request_body(
            model="m",
            prompt="hi",
            effort=Effort.MAX,
            max_tokens=512,
            thinking_disabled=True,
            protocol=PROTOCOL_OPENAI,
        )
        self.assertNotIn("output_config", body)
        self.assertNotIn("thinking", body)
        self.assertNotIn("reasoning_effort", body)

    def test_multiple_images_keep_the_send_order(self):
        body = build_request_body(
            model="m",
            prompt="hi",
            effort=Effort.LOW,
            max_tokens=512,
            image_base64="MAIN",
            images_base64=("EXTRA-1", "EXTRA-2"),
            protocol=PROTOCOL_OPENAI,
        )
        content = body["messages"][0]["content"]
        self.assertEqual(
            [block["type"] for block in content],
            ["text", "image_url", "image_url", "image_url"],
        )
        self.assertIn("MAIN", content[1]["image_url"]["url"])
        self.assertIn("EXTRA-1", content[2]["image_url"]["url"])
        self.assertIn("EXTRA-2", content[3]["image_url"]["url"])

    def test_client_autoselects_the_protocol_from_the_config(self):
        """``ModelClient.complete`` 自动用 ``config.protocol``（端点/头/正文全换）。"""
        config = _config(protocol=PROTOCOL_OPENAI)
        client = _StubTransportClient(config, InMemoryRequestLog(), _openai_body())
        reply = client.complete(
            "hi", effort=Effort.LOW, max_tokens=64, image_base64=_png_base64(400, 300)
        )
        self.assertTrue(client.sent_endpoints[0].endswith("/chat/completions"))
        self.assertEqual(
            client.sent_headers[0]["authorization"], "Bearer key-from-repo-env"
        )
        body = client.sent_bodies[0]
        self.assertEqual(body["messages"][0]["content"][0]["type"], "text")
        self.assertEqual(body["messages"][0]["content"][1]["type"], "image_url")
        self.assertNotIn("output_config", body)
        self.assertEqual(reply.image_tokens, 85)


class ResponseParsingTest(unittest.TestCase):
    """anthropic 响应解析：模型侧错误如实回传；HTTP 200 但没有文本输出 = 显式失败。"""

    def test_model_side_error_is_passed_through(self):
        body = json.dumps(
            {
                "type": "error",
                "error": {"type": "invalid_request_error", "message": "model requires adaptive thinking"},
            }
        ).encode()
        with self.assertRaises(ModelSideError) as ctx:
            parse_response(
                status=400,
                body_bytes=body,
                model="m",
                endpoint="https://example.invalid/v1/messages",
                effort=Effort.LOW,
                latency_ms=1.0,
            )
        self.assertEqual(ctx.exception.status, 400)
        self.assertEqual(ctx.exception.error_type, "invalid_request_error")
        self.assertIn("adaptive thinking", ctx.exception.message)

    def test_no_text_output_is_an_explicit_failure_not_an_empty_success(self):
        body = json.dumps(
            {
                "content": [],
                "usage": {
                    "input_tokens": 100,
                    "output_tokens": 1200,
                    "output_tokens_details": {"thinking_tokens": 1200},
                },
            }
        ).encode()
        with self.assertRaises(NoTextOutputError) as ctx:
            parse_response(
                status=200,
                body_bytes=body,
                model="m",
                endpoint="https://example.invalid/v1/messages",
                effort=Effort.MAX,
                latency_ms=1.0,
            )
        self.assertIn("thinking_tokens", json.dumps(ctx.exception.usage))

    def test_successful_reply_carries_text_and_usage(self):
        body = json.dumps(
            {
                "content": [{"type": "text", "text": "{\"x\": 1234, \"y\": 5678}"}],
                "usage": {
                    "input_tokens": 900,
                    "output_tokens": 40,
                    "output_tokens_details": {"thinking_tokens": 32},
                },
            }
        ).encode()
        reply = parse_response(
            status=200,
            body_bytes=body,
            model="m",
            endpoint="https://example.invalid/v1/messages",
            effort=Effort.LOW,
            latency_ms=12.5,
        )
        self.assertEqual(reply.text, "{\"x\": 1234, \"y\": 5678}")
        self.assertEqual(reply.input_tokens, 900)
        self.assertEqual(reply.output_tokens, 40)
        self.assertEqual(reply.thinking_tokens, 32)

    def test_reply_carries_stop_reason_and_block_types(self):
        """响应体里的形态字段要跟到回复上（**只有响应体里有**）。"""
        body = json.dumps(
            {
                "content": [
                    {"type": "thinking", "thinking": "……"},
                    {"type": "text", "text": "{\"x\": 1, \"y\": 2}"},
                ],
                "stop_reason": "end_turn",
            }
        ).encode()
        reply = parse_response(
            status=200,
            body_bytes=body,
            model="m",
            endpoint="https://example.invalid/v1/messages",
            effort=Effort.LOW,
            latency_ms=1.0,
        )
        self.assertEqual(reply.stop_reason, "end_turn")
        self.assertEqual(reply.content_block_types, ("thinking", "text"))

    def test_model_coordinates_are_passed_through_verbatim(self):
        body = json.dumps(
            {"content": [{"type": "text", "text": "坐标是 {\"x\": 1234, \"y\": 5678}"}]}
        ).encode()
        reply = parse_response(
            status=200,
            body_bytes=body,
            model="m",
            endpoint="https://example.invalid/v1/messages",
            effort=Effort.LOW,
            latency_ms=1.0,
        )
        self.assertEqual(reply.json_payload(), {"x": 1234, "y": 5678})

    def test_reply_without_json_is_an_explicit_failure(self):
        body = json.dumps({"content": [{"type": "text", "text": "就绪"}]}).encode()
        reply = parse_response(
            status=200,
            body_bytes=body,
            model="m",
            endpoint="https://example.invalid/v1/messages",
            effort=Effort.LOW,
            latency_ms=1.0,
        )
        with self.assertRaises(ModelError):
            reply.json_payload()

    def test_non_json_body_is_a_model_side_error_not_a_bare_exception(self):
        with self.assertRaises(ModelSideError) as ctx:
            parse_response(
                status=200,
                body_bytes=b"<html>not json</html>",
                model="m",
                endpoint="https://example.invalid/v1/messages",
                effort=Effort.LOW,
                latency_ms=1.0,
            )
        self.assertEqual(ctx.exception.status, 200)


class OpenAIResponseParsingTest(unittest.TestCase):
    """openai 响应解析：只认 content，reasoning_content 只做形态字段。"""

    _ENDPOINT = "https://api.deepseek.com/v1/chat/completions"

    def _parse(self, body: bytes, status: int = 200):
        return parse_response(
            status=status,
            body_bytes=body,
            model="m",
            endpoint=self._ENDPOINT,
            effort=Effort.LOW,
            latency_ms=1.0,
            protocol=PROTOCOL_OPENAI,
        )

    def test_successful_reply_carries_text_usage_and_shape(self):
        reply = self._parse(_openai_body())
        self.assertEqual(reply.text, '{"x": 1, "y": 2}')
        self.assertEqual(reply.model, "gpt-x")
        self.assertEqual(reply.input_tokens, 900)  # prompt_tokens
        self.assertEqual(reply.output_tokens, 40)  # completion_tokens
        self.assertEqual(reply.thinking_tokens, 32)  # reasoning_tokens
        self.assertIsNone(reply.cache_read_input_tokens)
        self.assertIsNone(reply.cache_creation_input_tokens)
        self.assertEqual(reply.stop_reason, "stop")  # finish_reason
        self.assertEqual(reply.content_block_types, ("text",))

    def test_reasoning_content_is_not_the_answer(self):
        body = _openai_body(
            choices=[
                {
                    "message": {
                        "role": "assistant",
                        "content": '{"x": 1, "y": 2}',
                        "reasoning_content": "先想想要不要给坐标……",
                    },
                    "finish_reason": "stop",
                }
            ]
        )
        reply = self._parse(body)
        self.assertEqual(reply.text, '{"x": 1, "y": 2}')
        self.assertNotIn("先想想", reply.text)
        self.assertEqual(reply.thinking_tokens, 32)

    def test_content_parts_are_joined(self):
        body = _openai_body(
            choices=[
                {
                    "message": {
                        "role": "assistant",
                        "content": [
                            {"type": "text", "text": '{"x": 1,'},
                            {"type": "text", "text": ' "y": 2}'},
                        ],
                    },
                    "finish_reason": "stop",
                }
            ]
        )
        reply = self._parse(body)
        self.assertEqual(reply.json_payload(), {"x": 1, "y": 2})

    def test_reasoning_only_is_an_explicit_failure_with_usage(self):
        body = _openai_body(
            choices=[
                {
                    "message": {
                        "role": "assistant",
                        "content": "",
                        "reasoning_content": "思考链吃光了预算",
                    },
                    "finish_reason": "length",
                }
            ]
        )
        with self.assertRaises(NoTextOutputError) as ctx:
            self._parse(body)
        usage = ctx.exception.usage or {}
        self.assertEqual(usage.get("thinking_tokens"), 32)
        self.assertIn("content", ctx.exception.message)

    def test_reasoning_only_failure_still_logs_the_shape(self):
        """「只有思考链」那一次**照样**要能从日志看出形态（不是猜的）。"""
        body = _openai_body(
            choices=[
                {
                    "message": {
                        "role": "assistant",
                        "content": "",
                        "reasoning_content": "思考链吃光了预算",
                    },
                    "finish_reason": "length",
                }
            ]
        )
        sink = InMemoryRequestLog()
        client = _StubTransportClient(
            _config(protocol=PROTOCOL_OPENAI), sink, body
        )
        with self.assertRaises(NoTextOutputError):
            client.complete("hi", effort=Effort.LOW, max_tokens=64)
        record = sink.records[0]
        self.assertEqual(record.stop_reason, "length")
        self.assertEqual(record.content_block_types, ("reasoning",))
        self.assertEqual(record.text_len, 0)

    def test_empty_content_is_an_explicit_failure(self):
        body = _openai_body(
            choices=[{"message": {"role": "assistant", "content": ""}, "finish_reason": "stop"}]
        )
        with self.assertRaises(NoTextOutputError):
            self._parse(body)

    def test_missing_choices_is_an_explicit_failure(self):
        with self.assertRaises(NoTextOutputError):
            self._parse(json.dumps({"model": "m"}).encode())

    def test_4xx_error_is_passed_through(self):
        body = json.dumps(
            {"error": {"type": "invalid_request_error", "message": "bad api key"}}
        ).encode()
        with self.assertRaises(ModelSideError) as ctx:
            self._parse(body, status=401)
        self.assertEqual(ctx.exception.status, 401)
        self.assertEqual(ctx.exception.error_type, "invalid_request_error")
        self.assertIn("bad api key", ctx.exception.message)

    def test_5xx_error_is_passed_through(self):
        with self.assertRaises(ModelSideError) as ctx:
            self._parse(b"<html>upstream exploded</html>", status=502)
        self.assertEqual(ctx.exception.status, 502)
        self.assertIn("upstream exploded", ctx.exception.message)

    def test_non_json_200_is_a_model_side_error(self):
        with self.assertRaises(ModelSideError) as ctx:
            self._parse(b"<html>not json</html>")
        self.assertEqual(ctx.exception.status, 200)


class TransportFailureTest(unittest.TestCase):
    """传输失败（超时 / 不可达）如实抛出并落日志，不伪装成模型侧错误。"""

    def test_timeout_is_reported_as_transport_error_and_logged(self):
        for protocol in PROTOCOLS:
            with self.subTest(protocol=protocol):
                sink = InMemoryRequestLog()
                client = _TimeoutTransportClient(
                    _config(protocol=protocol), sink, b"{}"
                )
                with self.assertRaises(ModelTransportError):
                    client.complete("hi", effort=Effort.LOW, max_tokens=64)
                record = sink.records[0]
                self.assertIsNone(record.http_status)
                self.assertIn("超时", record.error or "")


class StartupLogTest(unittest.TestCase):
    """启动日志：``ModelClient.__init__`` 往 stderr 打一行（脱敏、只打一次）。"""

    def test_logs_one_masked_line_at_construction(self):
        config = _config(api_key="sk-1234567890wxyz")
        sink = InMemoryRequestLog()
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            client = _StubTransportClient(
                config, sink, _anthropic_body()
            )
            client.complete("hi", effort=Effort.LOW, max_tokens=64)
        lines = [line for line in stderr.getvalue().splitlines() if line]
        self.assertEqual(len(lines), 1, "启动日志只该打一次（不是每次请求都打）")
        line = lines[0]
        self.assertIn("模型配置：", line)
        self.assertIn("protocol=anthropic", line)
        self.assertIn("api_key=sk-1****wxyz", line)
        self.assertIn("来源", line)
        self.assertNotIn("sk-1234567890wxyz", line, "完整密钥不许出现在日志里")

    def test_openai_startup_log_shows_the_openai_endpoint(self):
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            ModelClient(_config(protocol=PROTOCOL_OPENAI), InMemoryRequestLog())
        line = stderr.getvalue()
        self.assertIn("protocol=openai", line)
        self.assertIn("/chat/completions", line)


class ImageTokensTest(unittest.TestCase):
    """图像 token 估计按协议分支：anthropic = 28 px patch；openai = 512 tile。"""

    def test_no_image_is_none_for_both_protocols(self):
        for protocol in PROTOCOLS:
            with self.subTest(protocol=protocol):
                self.assertIsNone(image_tokens_of(protocol=protocol))
                self.assertIsNone(image_tokens_of(None, "", protocol=protocol))

    def test_anthropic_uses_the_28px_patch_rule(self):
        self.assertEqual(
            image_tokens_of(_png_base64(400, 300)), 15 * 11
        )
        self.assertEqual(image_tokens_of(_png_base64(28, 28)), 1)

    def test_openai_uses_the_tile_rule(self):
        self.assertEqual(
            image_tokens_of(_png_base64(400, 300), protocol=PROTOCOL_OPENAI),
            85,
        )
        # 1024×1024 短边缩到 768 → 2×2 tile → 85 + 170×4。
        self.assertEqual(
            image_tokens_of(_png_base64(1024, 1024), protocol=PROTOCOL_OPENAI),
            85 + 170 * 4,
        )
        # 3000×1000 先缩进 2048 → 2048×682.67 → 4×2 tile。
        self.assertEqual(
            image_tokens_of(_png_base64(3000, 1000), protocol=PROTOCOL_OPENAI),
            85 + 170 * 8,
        )

    def test_openai_small_image_floor_is_85(self):
        self.assertEqual(
            image_tokens_of(_png_base64(512, 512), protocol=PROTOCOL_OPENAI), 85
        )

    def test_the_two_protocols_disagree_for_the_same_image(self):
        """同一张图两套规则给出不同估计 —— 分支确实生效了。"""
        image = _png_base64(400, 300)
        self.assertEqual(image_tokens_of(image), 15 * 11)
        self.assertEqual(
            image_tokens_of(image, protocol=PROTOCOL_OPENAI), 85
        )

    def test_multiple_images_are_summed(self):
        image = _png_base64(400, 300)
        self.assertEqual(image_tokens_of(image, image), 2 * 15 * 11)
        self.assertEqual(
            image_tokens_of(image, image, protocol=PROTOCOL_OPENAI), 2 * 85
        )

    def test_undecodable_image_is_none_not_guessed(self):
        not_png = base64.b64encode(b"not a png").decode()
        for protocol in PROTOCOLS:
            with self.subTest(protocol=protocol):
                self.assertIsNone(image_tokens_of(not_png, protocol=protocol))
        # 一张好图 + 一张坏图：整次记 None（不拿好图那个数冒充整次）。
        self.assertIsNone(image_tokens_of(_png_base64(400, 300), not_png))


class RequestLogTest(unittest.TestCase):
    """请求日志：每条请求一条，档位字段非空且是五档之一。"""

    def test_record_carries_every_required_field(self):
        record = build_request_log(
            effort=Effort.LOW,
            model="minimax-m3.1-flash-free",
            endpoint="https://opencode.ai/zen/go/v1/messages",
            http_status=200,
            latency_ms=1234.5,
            input_tokens=10,
            output_tokens=20,
            thinking_tokens=30,
        )
        self.assertEqual(record.effort, "low")
        self.assertEqual(record.model, "minimax-m3.1-flash-free")
        self.assertEqual(record.endpoint, "https://opencode.ai/zen/go/v1/messages")
        self.assertEqual(record.http_status, 200)
        self.assertEqual(record.latency_ms, 1234.5)
        self.assertEqual(record.input_tokens, 10)
        self.assertEqual(record.output_tokens, 20)
        self.assertEqual(record.thinking_tokens, 30)

    def test_unset_effort_is_counted(self):
        records = [
            build_request_log(
                effort=Effort.LOW,
                model="m",
                endpoint="e",
                http_status=200,
                latency_ms=1.0,
            ),
            build_request_log(
                effort="",
                model="m",
                endpoint="e",
                http_status=200,
                latency_ms=1.0,
            ),
        ]
        self.assertEqual(count_unset_effort(records), 1)

    def test_jsonl_log_writes_one_line_per_record(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "model-requests.jsonl"
            sink = JsonlRequestLog(path)
            for effort in EFFORT_VALUES:
                sink.write(
                    build_request_log(
                        effort=Effort(effort),
                        model="m",
                        endpoint="e",
                        http_status=200,
                        latency_ms=1.0,
                    )
                )
            lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line]
        self.assertEqual(len(lines), 5)
        self.assertEqual(count_unset_effort([json.loads(line) for line in lines]), 0)

    def test_shape_fields_default_to_unknown(self):
        """四个形态字段**默认可空**（不改变既有字段的语义）。"""
        record = build_request_log(
            effort=Effort.LOW,
            model="m",
            endpoint="e",
            http_status=200,
            latency_ms=1.0,
        )
        self.assertIsNone(record.stop_reason)
        self.assertEqual(record.content_block_types, ())
        self.assertIsNone(record.text_len)
        self.assertIsNone(record.parse_ok)

    def test_text_without_a_parse_verdict_is_still_writable(self):
        """``text_len > 0`` 且 ``parse_ok is None`` **要能落盘**（两件事不是一件事）。"""
        record = build_request_log(
            effort=Effort.LOW,
            model="m",
            endpoint="e",
            http_status=200,
            latency_ms=1.0,
            stop_reason="end_turn",
            content_block_types=("text",),
            text_len=42,
            parse_ok=None,
        )
        payload = record.to_dict()
        self.assertEqual(payload["text_len"], 42)
        self.assertIsNone(payload["parse_ok"])
        self.assertEqual(payload["content_block_types"], ("text",))
        self.assertEqual(payload["stop_reason"], "end_turn")

    def test_jsonl_log_writes_the_whole_line_in_one_write_and_flushes_it(self):
        """整行**一次**写出去（不切成几块）+ **立刻 flush**（不被缓冲拖住）。"""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "model-requests.jsonl"
            sink = JsonlRequestLog(path)
            real_open = Path.open
            spies: list[_HandleSpy] = []

            def spy_open(self, *args, **kwargs):
                spy = _HandleSpy(real_open(self, *args, **kwargs))
                spies.append(spy)
                return spy

            record = build_request_log(
                effort=Effort.LOW,
                model="m",
                endpoint="e",
                http_status=200,
                latency_ms=1.0,
                error="x" * 30000,
            )
            with mock.patch.object(Path, "open", spy_open):
                sink.write(record)
        self.assertEqual(len(spies), 1)
        self.assertEqual(len(spies[0].writes), 1, "整行必须一次写出去")
        self.assertIn('"effort": "low"', spies[0].writes[0])
        self.assertTrue(spies[0].writes[0].endswith("\n"))
        self.assertTrue(spies[0].flushed, "写完必须立刻 flush")

    def test_jsonl_log_keeps_every_line_whole_under_concurrent_writers(self):
        """并发写同一个文件：**每行都要完整**（不能被别的行交错切断）。

        旧写法把长行切成几块分次写出去，两个进程/线程同时写时行与行会交错
        （本仓历史上 5259 条里撕过 1 条）。

        ⚠️ **只断言「没有撕裂的行」**：本机实测（8 线程 × 20 行 × 100 KB）
        Windows 上多个句柄并发追加同一个文件时，会在 EOF 处**整行互相覆盖**
        （156/160 行，不是撕裂）—— 那是另一个问题，不在本条断言里。
        """
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "model-requests.jsonl"
            sink = JsonlRequestLog(path)
            payload = "x" * 30000

            def writer() -> None:
                for _ in range(5):
                    sink.write(
                        build_request_log(
                            effort=Effort.LOW,
                            model="m",
                            endpoint="e",
                            http_status=200,
                            latency_ms=1.0,
                            error=payload,
                        )
                    )

            threads = [threading.Thread(target=writer) for _ in range(4)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()

            lines = [
                line for line in path.read_text(encoding="utf-8").splitlines() if line
            ]
        self.assertGreater(len(lines), 0)
        for line in lines:
            try:
                json.loads(line)
            except json.JSONDecodeError as exc:
                self.fail(f"日志行被撕开了：{exc}")


class RequestLogShapeTest(unittest.TestCase):
    """客户端把响应体的形态字段一路落到日志里（两种协议）。"""

    def test_log_carries_stop_reason_block_types_and_text_length(self):
        body = json.dumps(
            {
                "content": [
                    {"type": "thinking", "thinking": "……"},
                    {"type": "text", "text": "abc"},
                ],
                "stop_reason": "end_turn",
            }
        ).encode()
        sink = InMemoryRequestLog()
        client = _StubTransportClient(_config(), sink, body)
        client.complete("hi", effort=Effort.LOW, max_tokens=64)
        record = sink.records[0]
        self.assertEqual(record.stop_reason, "end_turn")
        self.assertEqual(record.content_block_types, ("thinking", "text"))
        self.assertEqual(record.text_len, 3)
        self.assertIsNone(record.parse_ok)

    def test_no_text_failure_still_logs_the_response_shape(self):
        """「思考链吃光预算 → 没有文本」那一次**照样**要能看出形态。"""
        body = json.dumps(
            {
                "content": [{"type": "thinking", "thinking": "……"}],
                "stop_reason": "max_tokens",
                "usage": {
                    "input_tokens": 100,
                    "output_tokens": 64,
                    "output_tokens_details": {"thinking_tokens": 64},
                },
            }
        ).encode()
        sink = InMemoryRequestLog()
        client = _StubTransportClient(_config(), sink, body)
        with self.assertRaises(NoTextOutputError):
            client.complete("hi", effort=Effort.LOW, max_tokens=64)
        record = sink.records[0]
        self.assertEqual(record.stop_reason, "max_tokens")
        self.assertEqual(record.content_block_types, ("thinking",))
        self.assertEqual(record.text_len, 0)


class FakeModelClientTest(unittest.TestCase):
    """假模型响应：可预置「思考吃光预算导致无输出」的失败形态。"""

    def test_preset_no_text_output_shape_is_an_explicit_failure(self):
        fake = FakeModelClient(no_text_output_outcome(thinking_tokens=1200, max_tokens=1200))
        with self.assertRaises(NoTextOutputError) as ctx:
            fake.complete("hi", effort=Effort.LOW)
        self.assertIn("1200", json.dumps(ctx.exception.usage))

    def test_fake_never_returns_a_successful_empty_reply(self):
        fake = FakeModelClient(no_text_output_outcome(thinking_tokens=1200, max_tokens=1200))
        with self.assertRaises(NoTextOutputError):
            fake.complete("hi", effort=Effort.LOW)
        self.assertEqual(len(fake.replies), 0)

    def test_fake_records_the_effort_it_was_called_with(self):
        fake = FakeModelClient(no_text_output_outcome(thinking_tokens=1, max_tokens=1))
        with self.assertRaises(NoTextOutputError):
            fake.complete("hi", effort=Effort.HIGH)
        self.assertEqual(fake.calls[0]["effort"], Effort.HIGH)

    def test_fake_is_a_drop_in_for_the_model_port(self):
        fake = FakeModelClient(no_text_output_outcome(thinking_tokens=1, max_tokens=1))
        self.assertTrue(isinstance(fake, ModelCaller))
        self.assertTrue(isinstance(ModelClient(_config(), InMemoryRequestLog()), ModelCaller))

    def test_fake_rejects_a_missing_effort_like_the_real_client(self):
        fake = FakeModelClient(no_text_output_outcome(thinking_tokens=1, max_tokens=1))
        with self.assertRaises(EffortRequiredError):
            fake.complete("hi")

    def test_fake_usage_shape_matches_the_real_client(self):
        fake = FakeModelClient(no_text_output_outcome(thinking_tokens=1200, max_tokens=1200))
        with self.assertRaises(NoTextOutputError) as ctx:
            fake.complete("hi", effort=Effort.LOW)
        self.assertEqual(ctx.exception.usage["thinking_tokens"], 1200)


if __name__ == "__main__":
    unittest.main()

"""模型客户端（M1）的单测。

只测外部行为：配置装载、档位强制、请求构造、响应解析、请求日志、假实现的失败形态。

**零网络、零费用**：所有用例都不发真实请求——真调 API 的四条回归在
``python -m minicode.model.regressions``（单独入口，不被单测命令带跑）。
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from minicode.model.client import (
    ModelClient,
    build_headers,
    build_request_body,
    parse_response,
    require_effort,
)
from minicode.model.config import (
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

_VALID_ENV = """\
MINICODE_ANTHROPIC_BASE_URL=https://opencode.ai/zen/go
MINICODE_ANTHROPIC_API_KEY=key-from-repo-env
MINICODE_MODEL=minimax-m3.1-flash-free
MINICODE_SESSION_ID=dsh-union-alpha
MINICODE_HTTP_USER_AGENT=opencode/1.0.0
MINICODE_HTTP_CLIENT=cli
"""


def _config(**overrides: str) -> ModelConfig:
    values = {
        "base_url": "https://opencode.ai/zen/go",
        "api_key": "key-from-repo-env",
        "model": "minimax-m3.1-flash-free",
        "session_id": "dsh-union-alpha",
        "user_agent": "opencode/1.0.0",
        "client_header": "cli",
    }
    values.update(overrides)
    return ModelConfig(**values)


class ConfigTest(unittest.TestCase):
    """凭据单一来源：仓库 ``.env`` 是唯一的凭据来源。"""

    def test_loads_every_required_item_from_the_env_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".env"
            path.write_text(_VALID_ENV, encoding="utf-8")
            config = load_config(path)
        self.assertEqual(config.base_url, "https://opencode.ai/zen/go")
        self.assertEqual(config.api_key, "key-from-repo-env")
        self.assertEqual(config.model, "minimax-m3.1-flash-free")
        self.assertEqual(config.session_id, "dsh-union-alpha")
        self.assertEqual(config.user_agent, "opencode/1.0.0")
        self.assertEqual(config.client_header, "cli")

    def test_missing_items_are_named_in_the_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".env"
            path.write_text(
                "MINICODE_ANTHROPIC_BASE_URL=https://opencode.ai/zen/go\n"
                "MINICODE_MODEL=minimax-m3.1-flash-free\n",
                encoding="utf-8",
            )
            with self.assertRaises(ModelConfigError) as ctx:
                load_config(path)
        message = str(ctx.exception)
        for key in (
            "MINICODE_ANTHROPIC_API_KEY",
            "MINICODE_SESSION_ID",
            "MINICODE_HTTP_USER_AGENT",
            "MINICODE_HTTP_CLIENT",
        ):
            self.assertIn(key, message)

    def test_empty_value_counts_as_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".env"
            path.write_text(_VALID_ENV.replace("MINICODE_MODEL=minimax-m3.1-flash-free", "MINICODE_MODEL="), encoding="utf-8")
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


class EndpointRuleTest(unittest.TestCase):
    """「选哪条通道」是可测行为：免费档只能走带 ``/go`` 后缀的那条。"""

    def test_free_tier_channel_is_accepted(self):
        self.assertEqual(
            resolve_endpoint("https://opencode.ai/zen/go"),
            "https://opencode.ai/zen/go/v1/messages",
        )

    def test_non_free_tier_channel_is_refused_by_name(self):
        with self.assertRaises(ModelConfigError) as ctx:
            resolve_endpoint("https://opencode.ai/zen")
        self.assertIn("/go", str(ctx.exception))

    def test_empty_base_url_is_refused(self):
        with self.assertRaises(ModelConfigError):
            resolve_endpoint("   ")

    def test_trailing_slash_is_tolerated(self):
        self.assertEqual(
            resolve_endpoint("https://opencode.ai/zen/go/"),
            "https://opencode.ai/zen/go/v1/messages",
        )


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


class RequestConstructionTest(unittest.TestCase):
    """请求构造：头集合、请求体、档位字段。"""

    def test_all_required_headers_are_present(self):
        headers = build_headers(_config())
        self.assertEqual(headers["user-agent"], "opencode/1.0.0")
        self.assertEqual(headers["x-opencode-client"], "cli")
        self.assertEqual(headers["x-api-key"], "key-from-repo-env")
        self.assertEqual(headers["x-opencode-session"], "dsh-union-alpha")
        self.assertEqual(headers["anthropic-version"], "2023-06-01")
        self.assertEqual(headers["content-type"], "application/json")

    def test_body_carries_the_effort_field_the_server_actually_supports(self):
        body = build_request_body(
            model="minimax-m3.1-flash-free",
            prompt="hi",
            effort=Effort.LOW,
            max_tokens=512,
        )
        self.assertEqual(body["output_config"], {"effort": "low"})

    def test_body_never_uses_the_ignored_alias(self):
        body = build_request_body(
            model="m", prompt="hi", effort=Effort.LOW, max_tokens=512
        )
        self.assertNotIn("reasoning_effort", body)
        self.assertNotIn("thinking", body)

    def test_body_omits_the_effort_field_when_effort_is_none(self):
        body = build_request_body(
            model="m", prompt="hi", effort=None, max_tokens=512
        )
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


class ResponseParsingTest(unittest.TestCase):
    """响应解析：模型侧错误如实回传；HTTP 200 但没有文本输出 = 显式失败。"""

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

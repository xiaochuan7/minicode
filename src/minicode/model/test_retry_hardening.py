"""W7 重试硬化的单测：429 单独计数、重试落盘、串行 in-flight=1、策略可配。

验收口径（工单原文）：

* 造假客户端前 5 次抛 429、第 6 次成功 → **拿到结果** 且 429 **不计入失败数**；
* 413 → **不重试**、立刻暴露（限流数 = 0，失败数 = 1）；
* 每轮尝试（成功或耗尽）都经 ``attempt_sink`` 落盘；
* ``serial=True`` 时并发调用 **in-flight = 1**（视觉通道纪律）；
* :class:`RetryPolicySettings` 缺省值与 :class:`RetryPolicy` 字段缺省**逐一对齐**
  （守两处同步 —— 错位会让「没配 = 行为不变」悄悄失效）。

**零网络、零等待**：睡眠注入成记账，随机源固定。
"""

from __future__ import annotations

import json
import tempfile
import threading
import unittest
from pathlib import Path

from minicode.model.client import ModelReply
from minicode.model.config import (
    ModelConfigError,
    RetryPolicySettings,
    load_retry_settings,
)
from minicode.model.effort import Effort
from minicode.model.errors import ModelSideError, ModelTransportError
from minicode.model.retry import RetryingModelCaller, RetryLogWriter, RetryPolicy


def _reply(text: str = "ok") -> ModelReply:
    return ModelReply(
        text=text,
        model="fake-model",
        endpoint="https://fake.invalid/v1",
        effort=Effort.LOW,
        http_status=200,
        latency_ms=1.0,
    )


class _ScriptedCaller:
    """按脚本逐次抛错/返回的假调用方（与 test_model_retry.py 同款）。"""

    def __init__(self, script):
        self.script = list(script)
        self.calls: list[dict] = []

    def complete(self, prompt, *, effort=None, max_tokens=2000,
                 image_base64=None, images_base64=(), thinking_disabled=False,
                 parse_ok=None):
        self.calls.append({"prompt": prompt, "max_tokens": max_tokens})
        outcome = self.script.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def _policy(**kw) -> RetryPolicy:
    """不真睡的策略：退避记账（丢弃），不等待。"""
    return RetryPolicy(
        sleep=lambda seconds: None, rand=lambda: 0.0, jitter_s=0.0, **kw
    )


class RateLimitAccountingTest(unittest.TestCase):
    """429 与「模型失败」**分开计数**：限流是配额问题，不计入失败率。"""

    def test_five_429_then_success_counts_zero_failures(self):
        inner = _ScriptedCaller([ModelSideError(429, "限流")] * 5 + [_reply("第 6 次")])
        records: list[dict] = []
        caller = RetryingModelCaller(
            inner, _policy(max_attempts=16), attempt_sink=records.append
        )
        reply = caller.complete("p", effort=Effort.LOW)
        self.assertEqual(reply.text, "第 6 次")
        self.assertEqual(len(inner.calls), 6)
        self.assertEqual(caller.rate_limited_attempts, 5)
        self.assertEqual(caller.failure_attempts, 0)

    def test_413_never_retries_and_counts_as_failure(self):
        inner = _ScriptedCaller([ModelSideError(413, "请求体过大")])
        records: list[dict] = []
        caller = RetryingModelCaller(
            inner, _policy(max_attempts=16), attempt_sink=records.append
        )
        with self.assertRaises(ModelSideError):
            caller.complete("p", effort=Effort.LOW)
        self.assertEqual(len(inner.calls), 1)
        self.assertEqual(caller.rate_limited_attempts, 0)
        self.assertEqual(caller.failure_attempts, 1)

    def test_transport_failures_count_as_failures_not_rate_limits(self):
        inner = _ScriptedCaller(
            [ModelTransportError("超时"), ModelTransportError("断连"), _reply()]
        )
        caller = RetryingModelCaller(inner, _policy(max_attempts=3))
        self.assertEqual(caller.complete("p", effort=Effort.LOW).text, "ok")
        self.assertEqual(caller.rate_limited_attempts, 0)
        self.assertEqual(caller.failure_attempts, 2)


class AttemptSinkTest(unittest.TestCase):
    """每轮 complete（成功或耗尽）都把逐次尝试交给落盘缝。"""

    def test_sink_receives_record_on_success_and_exhaustion(self):
        records: list[dict] = []
        ok_inner = _ScriptedCaller([ModelSideError(429, "限流"), _reply()])
        RetryingModelCaller(
            ok_inner, _policy(max_attempts=2), attempt_sink=records.append
        ).complete("p", effort=Effort.LOW)
        bad_inner = _ScriptedCaller(
            [ModelTransportError("坏 1"), ModelTransportError("坏 2")]
        )
        caller = RetryingModelCaller(
            bad_inner, _policy(max_attempts=2), attempt_sink=records.append
        )
        with self.assertRaises(ModelTransportError):
            caller.complete("p", effort=Effort.LOW)
        self.assertEqual(len(records), 2)
        self.assertEqual(records[0]["outcome"], "ok")
        self.assertEqual(records[0]["rate_limited_attempts"], 1)
        self.assertEqual(records[1]["outcome"], "exhausted")
        self.assertEqual(
            [a["attempt"] for a in records[1]["attempts"]], [1, 2]
        )
        self.assertEqual(
            [a["delay_s"] is not None for a in records[1]["attempts"]],
            [True, False],
        )

    def test_sink_failure_never_breaks_the_call(self):
        def boom(record):
            raise RuntimeError("落盘坏了")

        inner = _ScriptedCaller([_reply()])
        caller = RetryingModelCaller(inner, _policy(), attempt_sink=boom)
        self.assertEqual(caller.complete("p", effort=Effort.LOW).text, "ok")

    def test_retry_log_writer_appends_jsonl(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "runs" / "model-retries.jsonl"
            writer = RetryLogWriter(path)
            writer.write({"outcome": "ok", "attempts": []})
            writer.write({"outcome": "exhausted", "attempts": [{"attempt": 1}]})
            lines = path.read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(lines), 2)
            first = json.loads(lines[0])
            self.assertEqual(first["outcome"], "ok")
            self.assertEqual(
                json.loads(lines[1])["attempts"][0]["attempt"], 1
            )


class SerialInFlightTest(unittest.TestCase):
    """serial=True → 并发调用的 in-flight = 1（不重叠）。"""

    def test_serial_calls_never_overlap(self):
        inflight = 0
        max_inflight = 0
        guard = threading.Lock()

        class _SlowInner:
            def complete(self, prompt, **kw):
                nonlocal inflight, max_inflight
                with guard:
                    inflight += 1
                    max_inflight = max(max_inflight, inflight)
                # 模拟慢调用（不睡真时间：自旋足够让并发重叠暴露出来）
                for _ in range(2000):
                    pass
                with guard:
                    inflight -= 1
                return _reply()

        caller = RetryingModelCaller(_SlowInner(), _policy(), serial=True)
        threads = [
            threading.Thread(
                target=lambda i=i: caller.complete(f"p{i}", effort=Effort.LOW)
            )
            for i in range(4)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)
        self.assertEqual(max_inflight, 1)


class RetrySettingsConfigTest(unittest.TestCase):
    """策略可配：键解析、视觉键回落通用键、非法数字报错、缺省对齐。"""

    def test_defaults_align_with_retry_policy(self):
        settings = RetryPolicySettings()
        policy = RetryPolicy()
        self.assertEqual(settings.max_attempts, policy.max_attempts)
        self.assertEqual(settings.base_delay_s, policy.base_delay_s)
        self.assertEqual(settings.max_delay_s, policy.max_delay_s)
        self.assertEqual(settings.jitter_s, policy.jitter_s)

    def _write_env(self, tmp: str, text: str) -> Path:
        path = Path(tmp) / ".env"
        path.write_text(text, encoding="utf-8")
        return path

    _BASE = "MINICODE_PROVIDER=openai\nMINICODE_BASE_URL=https://a.invalid\nMINICODE_API_KEY=k\nMINICODE_MODEL=m\n"

    def test_generic_keys_apply_to_decision_layer(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = self._write_env(
                tmp,
                self._BASE
                + "MINICODE_RETRY_MAX_ATTEMPTS=9\n"
                + "MINICODE_RETRY_BASE_DELAY_S=2\n",
            )
            settings = load_retry_settings(env)
            self.assertEqual(settings.max_attempts, 9)
            self.assertEqual(settings.base_delay_s, 2.0)
            self.assertEqual(settings.max_delay_s, 8.0)  # 未配 → 缺省

    def test_vision_keys_override_generic_and_fall_back(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = self._write_env(
                tmp,
                self._BASE
                + "MINICODE_RETRY_MAX_ATTEMPTS=9\n"
                + "MINICODE_VISION_RETRY_MAX_ATTEMPTS=16\n"
                + "MINICODE_VISION_RETRY_BASE_DELAY_S=1.5\n",
            )
            vision = load_retry_settings(env, vision=True)
            self.assertEqual(vision.max_attempts, 16)  # 视觉键优先
            self.assertEqual(vision.base_delay_s, 1.5)
            # 视觉没配的 max_delay_s：没有通用键 → 缺省（视觉不继承决策专属值）
            self.assertEqual(vision.max_delay_s, 8.0)
            decision = load_retry_settings(env)
            self.assertEqual(decision.max_attempts, 9)

    def test_vision_missing_falls_back_to_generic_keys(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = self._write_env(
                tmp,
                self._BASE
                + "MINICODE_RETRY_MAX_ATTEMPTS=12\n"
                + "MINICODE_RETRY_BASE_DELAY_S=2.5\n",
            )
            vision = load_retry_settings(env, vision=True)
            self.assertEqual(vision.max_attempts, 12)
            self.assertEqual(vision.base_delay_s, 2.5)

    def test_all_missing_returns_defaults(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = self._write_env(tmp, self._BASE)
            self.assertEqual(load_retry_settings(env), RetryPolicySettings())
            self.assertEqual(
                load_retry_settings(env, vision=True), RetryPolicySettings()
            )

    def test_non_numeric_value_raises_config_error(self):
        for key in (
            "MINICODE_RETRY_MAX_ATTEMPTS",
            "MINICODE_RETRY_BASE_DELAY_S",
        ):
            with self.subTest(key=key):
                with tempfile.TemporaryDirectory() as tmp:
                    env = self._write_env(tmp, self._BASE + f"{key}=abc\n")
                    with self.assertRaises(ModelConfigError):
                        load_retry_settings(env)

    def test_missing_env_file_returns_defaults(self):
        settings = load_retry_settings(Path("Z:/nonexistent/.env"))
        self.assertEqual(settings, RetryPolicySettings())


if __name__ == "__main__":
    unittest.main()

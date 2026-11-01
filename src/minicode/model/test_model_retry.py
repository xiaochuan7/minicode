"""重试缝的测试：分流口径、指数退避+抖动、尝试记录、参数透传。"""

from __future__ import annotations

import unittest

from minicode.model.client import ModelReply
from minicode.model.effort import Effort
from minicode.model.errors import (
    EffortRequiredError,
    ModelConfigError,
    ModelSideError,
    ModelTransportError,
    NoTextOutputError,
)
from minicode.model.retry import RETRYABLE_STATUSES, RetryingModelCaller, RetryPolicy


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
    """按脚本逐次抛错/返回的假调用方（记录每次收到的参数）。"""

    def __init__(self, script):
        self.script = list(script)
        self.calls: list[dict] = []

    def complete(self, prompt, *, effort=None, max_tokens=2000,
                 image_base64=None, images_base64=(), thinking_disabled=False,
                 parse_ok=None):
        self.calls.append({
            "prompt": prompt, "effort": effort, "max_tokens": max_tokens,
            "image_base64": image_base64, "images_base64": tuple(images_base64),
            "thinking_disabled": thinking_disabled, "parse_ok": parse_ok,
        })
        outcome = self.script.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class _NoSleepPolicy(RetryPolicy):
    """不真睡的策略（单测不等待）；退避值照算，只把睡眠换成记账。"""

    def __init__(self, **kw):
        super().__init__(**kw, sleep=lambda seconds: sleeps.append(seconds))


sleeps: list[float] = []


class RetryClassificationTest(unittest.TestCase):
    """哪些失败重试、哪些如实抛。"""

    def test_transport_error_retries_then_succeeds(self):
        sleeps.clear()
        inner = _ScriptedCaller([
            ModelTransportError("超时"),
            _reply("第二次成功"),
        ])
        caller = RetryingModelCaller(inner, _NoSleepPolicy(max_attempts=3))
        reply = caller.complete("提示词", effort=Effort.LOW)
        self.assertEqual(reply.text, "第二次成功")
        self.assertEqual(len(inner.calls), 2)
        self.assertEqual([a.attempt for a in caller.attempts], [1])

    def test_retryable_server_statuses_retry_others_fail_fast(self):
        for status in sorted(RETRYABLE_STATUSES):
            with self.subTest(status=status):
                sleeps.clear()
                inner = _ScriptedCaller([ModelSideError(status, "服务端"), _reply()])
                caller = RetryingModelCaller(inner, _NoSleepPolicy(max_attempts=2))
                self.assertEqual(caller.complete("p", effort=Effort.LOW).text, "ok")

    def test_non_retryable_4xx_raises_immediately_without_second_call(self):
        for status in (400, 401, 403, 404, 409, 413, 422):
            with self.subTest(status=status):
                inner = _ScriptedCaller([ModelSideError(status, "请求有问题")])
                caller = RetryingModelCaller(inner, _NoSleepPolicy(max_attempts=3))
                with self.assertRaises(ModelSideError):
                    caller.complete("p", effort=Effort.LOW)
                self.assertEqual(len(inner.calls), 1)

    def test_empty_text_output_retries(self):
        sleeps.clear()
        inner = _ScriptedCaller([NoTextOutputError("思考链吃光预算"), _reply()])
        caller = RetryingModelCaller(inner, _NoSleepPolicy(max_attempts=2))
        self.assertEqual(caller.complete("p", effort=Effort.LOW).text, "ok")
        self.assertEqual(len(inner.calls), 2)

    def test_config_and_effort_errors_never_retry(self):
        for exc in (ModelConfigError("缺 api_key"), EffortRequiredError("没给 effort")):
            with self.subTest(exc=type(exc).__name__):
                inner = _ScriptedCaller([exc])
                caller = RetryingModelCaller(inner, _NoSleepPolicy(max_attempts=3))
                with self.assertRaises(type(exc)):
                    caller.complete("p")
                self.assertEqual(len(inner.calls), 1)


class ExhaustionTest(unittest.TestCase):
    """次数用尽：最后一次的异常如实抛出，不吞不改。"""

    def test_all_attempts_fail_then_original_error_raises(self):
        sleeps.clear()
        inner = _ScriptedCaller([
            ModelTransportError("坏 1"),
            ModelTransportError("坏 2"),
            ModelTransportError("坏 3"),
        ])
        caller = RetryingModelCaller(inner, _NoSleepPolicy(max_attempts=3))
        with self.assertRaises(ModelTransportError) as ctx:
            caller.complete("p", effort=Effort.LOW)
        self.assertIn("坏 3", str(ctx.exception))
        self.assertEqual(len(inner.calls), 3)
        # 尝试记录：前两次有退避，最后一次没有。
        self.assertEqual(
            [(a.attempt, a.delay_s is not None) for a in caller.attempts],
            [(1, True), (2, True), (3, False)],
        )


class BackoffTest(unittest.TestCase):
    """指数退避 + 抖动的数值形状。"""

    def test_delays_grow_exponentially_and_are_capped(self):
        policy = RetryPolicy(base_delay_s=0.5, max_delay_s=4.0, jitter_s=0.0,
                             sleep=lambda s: None, rand=lambda: 0.0)
        self.assertAlmostEqual(policy.delay_for(1), 0.5)
        self.assertAlmostEqual(policy.delay_for(2), 1.0)
        self.assertAlmostEqual(policy.delay_for(3), 2.0)
        self.assertAlmostEqual(policy.delay_for(4), 4.0)
        self.assertAlmostEqual(policy.delay_for(9), 4.0, msg="封顶不超 max_delay_s")

    def test_jitter_is_uniform_additive(self):
        policy = RetryPolicy(base_delay_s=0.5, max_delay_s=8.0, jitter_s=0.25,
                             sleep=lambda s: None, rand=lambda: 0.5)
        self.assertAlmostEqual(policy.delay_for(1), 0.5 + 0.125)

    def test_retry_sleeps_use_the_computed_delays(self):
        sleeps.clear()
        inner = _ScriptedCaller([
            ModelTransportError("x"), ModelTransportError("y"), _reply(),
        ])
        policy = RetryPolicy(base_delay_s=0.5, max_delay_s=8.0, jitter_s=0.0,
                             sleep=lambda s: sleeps.append(s), rand=lambda: 0.0)
        caller = RetryingModelCaller(inner, policy)
        caller.complete("p", effort=Effort.LOW)
        self.assertAlmostEqual(sleeps[0], 0.5)
        self.assertAlmostEqual(sleeps[1], 1.0)


class PassThroughTest(unittest.TestCase):
    """参数原样透传：**不重试成别的参数**（仓库红线）。"""

    def test_all_kwargs_reach_the_inner_caller_unchanged(self):
        sleeps.clear()
        inner = _ScriptedCaller([ModelTransportError("x"), _reply()])
        caller = RetryingModelCaller(inner, _NoSleepPolicy(max_attempts=2))
        caller.complete(
            "提示词",
            effort=Effort.MEDIUM,
            max_tokens=4096,
            image_base64="abc",
            images_base64=("d", "e"),
            thinking_disabled=True,
            parse_ok=True,
        )
        self.assertEqual(len(inner.calls), 2)
        for call in inner.calls:
            self.assertEqual(call["prompt"], "提示词")
            self.assertEqual(call["effort"], Effort.MEDIUM)
            self.assertEqual(call["max_tokens"], 4096)
            self.assertEqual(call["image_base64"], "abc")
            self.assertEqual(call["images_base64"], ("d", "e"))
            self.assertTrue(call["thinking_disabled"])
            self.assertTrue(call["parse_ok"])


if __name__ == "__main__":
    unittest.main()

"""**模型调用的重试缝**（W5 可靠性 1/4）：指数退避 + 抖动，按异常分流。

设计（与 ：mod:`minicode.reflection.retry` 的动作重试同一取向——**装饰器缝**，
``ModelCaller`` 端口不变，调用方零改动）：

* **可重试**（失败是暂时的，同样的请求再发一次有意义）：
  - :class:`~minicode.model.errors.ModelTransportError`——没拿到 HTTP 响应
    （超时 / 网络不可达 / 连接被掐）；
  - :class:`~minicode.model.errors.ModelSideError` 且状态 ∈ {429, 500, 502,
    503, 504}——限流与服务端暂时性故障；
  - :class:`~minicode.model.errors.NoTextOutputError`——HTTP 200 但文本为空
    （思考链吃光预算；重发一次可能就有文本）。
* **不可重试**（重发同样的请求只会得到同样的结果）：
  - ``ModelSideError`` 其余 4xx（400/401/403/404/413/422…——请求本身有问题）；
  - :class:`~minicode.model.errors.ModelConfigError` /
    :class:`~minicode.model.errors.EffortRequiredError`——本地配置问题。

如实记录：每次尝试的异常、退避时长都留在 :attr:`attempts` 里（最后一次
调用清空重来）；**不重试成别的参数**（仓库红线：失败如实回传，不改写请求）。
重试天然有代价（每次尝试都按原样计费）——预算敏感的调用方把
``max_attempts`` 设成 1 即等价于不重试。
"""

from __future__ import annotations

import random
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from .client import ModelReply
from .effort import Effort
from .errors import (
    EffortRequiredError,
    ModelConfigError,
    ModelSideError,
    ModelTransportError,
    NoTextOutputError,
)

__all__ = ["RetryPolicy", "RetryingModelCaller", "RetryAttempt"]

#: 服务端**暂时性**故障的状态码（重试有意义的那几个）。
RETRYABLE_STATUSES: frozenset[int] = frozenset({429, 500, 502, 503, 504})


@dataclass(frozen=True)
class RetryPolicy:
    """重试策略（指数退避 + 均匀抖动）。

    :param max_attempts: 总尝试次数上限（**含首次**；1 = 不重试）。
    :param base_delay_s: 第 1 次重试前的基础退避。
    :param max_delay_s: 单次退避上限（指数增长封顶）。
    :param jitter_s: 均匀抖动幅度（``[0, jitter_s)`` 加在退避上，防惊群）。
    :param sleep: 睡眠函数（**注入**：单测传不睡的假钟）。
    :param rand: ``[0,1)`` 随机源（**注入**：单测固定序列）。
    """

    max_attempts: int = 3
    base_delay_s: float = 0.5
    max_delay_s: float = 8.0
    jitter_s: float = 0.25
    sleep: Callable[[float], None] = time.sleep
    rand: Callable[[], float] = field(default=random.random)

    def delay_for(self, attempt: int) -> float:
        """第 ``attempt`` 次重试（从 1 起）前的退避秒数。

        指数 = ``base_delay_s * 2**(attempt-1)``，封顶 ``max_delay_s``，
        再加 ``[0, jitter_s)`` 抖动。
        """
        exponential = self.base_delay_s * (2 ** max(0, attempt - 1))
        capped = min(exponential, self.max_delay_s)
        return capped + (self.rand() * self.jitter_s if self.jitter_s > 0 else 0.0)


@dataclass(frozen=True)
class RetryAttempt:
    """一次尝试的记录（进了 :attr:`RetryingModelCaller.attempts`）。"""

    attempt: int
    error: str
    delay_s: float | None  # 这次失败后睡了多久（``None`` = 没睡，直接抛）


class RetryingModelCaller:
    """包住任意 ``ModelCaller`` 的重试缝（端口形状不变，调用方零改动）。"""

    def __init__(self, inner, policy: RetryPolicy | None = None):
        self.inner = inner
        self.policy = policy or RetryPolicy()
        #: 最近一次 ``complete`` 的逐次尝试记录（每次调用前清空）。
        self.attempts: list[RetryAttempt] = []

    def complete(
        self,
        prompt: str,
        *,
        effort: Effort | str | None = None,
        max_tokens: int = 2000,
        image_base64: str | None = None,
        images_base64: Sequence[str] = (),
        thinking_disabled: bool = False,
        parse_ok: bool | None = None,
    ) -> ModelReply:
        """带重试的模型调用（参数原样透传，**不重试成别的参数**）。"""
        self.attempts = []
        for attempt in range(1, self.policy.max_attempts + 1):
            try:
                return self.inner.complete(
                    prompt,
                    effort=effort,
                    max_tokens=max_tokens,
                    image_base64=image_base64,
                    images_base64=images_base64,
                    thinking_disabled=thinking_disabled,
                    parse_ok=parse_ok,
                )
            except (
                ModelTransportError,
                NoTextOutputError,
            ) as exc:
                delay = self._after_failure(attempt, exc)
                if delay is None:
                    raise
                self.policy.sleep(delay)
            except ModelSideError as exc:
                if exc.status not in RETRYABLE_STATUSES:
                    # 4xx（除 429）= 请求本身有问题：重发一样的只会一样，如实抛。
                    self.attempts.append(
                        RetryAttempt(attempt=attempt, error=_brief(exc), delay_s=None)
                    )
                    raise
                delay = self._after_failure(attempt, exc)
                if delay is None:
                    raise
                self.policy.sleep(delay)
            except (ModelConfigError, EffortRequiredError) as exc:
                # 本地配置问题：不重试（重试前也记一笔，审计口径一致）。
                self.attempts.append(
                    RetryAttempt(attempt=attempt, error=_brief(exc), delay_s=None)
                )
                raise
        raise AssertionError("不可达：max_attempts 循环必在其中一次返回或抛出")

    def _after_failure(self, attempt: int, exc: Exception) -> float | None:
        """失败后决定「再睡多久重试」；次数用尽返回 ``None``（上层如实抛）。"""
        if attempt >= self.policy.max_attempts:
            self.attempts.append(
                RetryAttempt(attempt=attempt, error=_brief(exc), delay_s=None)
            )
            return None
        delay = self.policy.delay_for(attempt)
        self.attempts.append(RetryAttempt(attempt=attempt, error=_brief(exc), delay_s=delay))
        return delay


def _brief(exc: Exception) -> str:
    """异常的**单行摘要**（进尝试记录；不吞类型）。"""
    text = str(exc)
    return f"{type(exc).__name__}: {text[:200]}"

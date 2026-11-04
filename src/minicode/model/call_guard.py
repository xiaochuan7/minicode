"""**视觉调用的外层守卫**（W7 重试硬化）：重试耗尽 ≠ 任务失败。

内层（:class:`~minicode.model.retry.RetryingModelCaller`）耗尽重试后如实抛 ——
那是**这一次调用**的失败。外层守卫接住它：

* **冷却**：耗尽后睡一个冷却窗（缺省 60~120 秒，均匀随机），再继续后面的工作
  —— 免费共享网关的限流窗口以分钟计，立即重发只会继续撞墙；
* **升级**：**连续** N 次（缺省 5）都没拿到响应 → 才升级为「通道暂停」
  （:attr:`paused`）—— 上层据此暂停本主线、写进度、换任务，**绝不因为
  限流终止整夜**；
* **成功清零**：任何一次成功都把连续失败计数归零（「连续」的口径）。

三个数分开记（审计口径）：:attr:`exhausted_events`（耗尽次数）/
:attr:`cooldown_total_s`（累计冷却）/ :attr:`consecutive_exhaustions`
（当前连续失败；≥ :attr:`pause_threshold` 即 :attr:`paused`）。
冷却**注入**（:param sleep:）—— 单测不真睡。
"""

from __future__ import annotations

import random
import time
from collections.abc import Callable
from dataclasses import dataclass

__all__ = ["CooldownPolicy", "VisionCallGuard"]


@dataclass(frozen=True)
class CooldownPolicy:
    """冷却策略（数值与工单口径一致，全部可调）。

    :param min_s: 冷却窗下界（秒）。
    :param max_s: 冷却窗上界（秒）；实际冷却在 ``[min_s, max_s)`` 均匀取。
    :param pause_threshold: 连续耗尽多少次后升级为「通道暂停」（≥5）。
    """

    min_s: float = 60.0
    max_s: float = 120.0
    pause_threshold: int = 5

    def draw(self, rand: Callable[[], float]) -> float:
        """抽一个冷却时长（``[min_s, max_s)``）。"""
        return self.min_s + rand() * (self.max_s - self.min_s)


class VisionCallGuard:
    """包住「重试耗尽后怎么办」的外层守卫（**不做任何模型调用**）。"""

    def __init__(
        self,
        policy: CooldownPolicy | None = None,
        *,
        sleep: Callable[[float], None] = time.sleep,
        rand: Callable[[], float] = random.random,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.policy = policy or CooldownPolicy()
        self._sleep = sleep
        self._rand = rand
        self._clock = clock
        #: **累计**：耗尽事件数 / 冷却总时长（审计口径）。
        self.exhausted_events = 0
        self.cooldown_total_s = 0.0
        #: 当前**连续**耗尽数（成功清零）。
        self.consecutive_exhaustions = 0
        #: 最近一次冷却的实际时长（0 = 没冷却过）。
        self.last_cooldown_s = 0.0

    @property
    def paused(self) -> bool:
        """通道是否已升级为「暂停」（连续耗尽 ≥ 阈值）。"""
        return self.consecutive_exhaustions >= self.policy.pause_threshold

    def on_exhausted(self, error: str = "") -> float:
        """一次调用耗尽重试：**先冷却再返回**（调用方拿到时长继续干活）。

        冷却时长进 :attr:`cooldown_total_s`；连续计数 +1；
        达到阈值后 :attr:`paused` 变 ``True`` —— **但不抛异常、不终止**，
        升级动作用 :attr:`paused` 轮询（上层决定换主线还是睡更久）。
        返回实际冷却的秒数。
        """
        self.exhausted_events += 1
        self.consecutive_exhaustions += 1
        delay = self.policy.draw(self._rand)
        started = self._clock()
        self._sleep(delay)
        self.cooldown_total_s += self._clock() - started
        self.last_cooldown_s = delay
        return delay

    def on_success(self) -> None:
        """一次成功：连续计数归零（**不影响**累计口径）。"""
        self.consecutive_exhaustions = 0

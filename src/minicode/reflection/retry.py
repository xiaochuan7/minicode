"""**重试策略**：只重试可逆动作，退避有上限，重试前必须重新感知。

工单 M12 AC 3 / AC 4。词条按 ``CONTEXT.md``：

* **可逆动作 / 不可逆动作** —— 可逆动作失败后重试不产生额外后果；
  不可逆动作重试可能重复生效，因而不可自动重试。
* **重试** —— 重新执行失败的动作。**每次重试前必须重新感知当前界面**。

三条规矩落在这一处：

1. **只重试可逆动作** —— 移动 / 滚动 / 聚焦（点击一个不是「发消息 / 支付 / 删除」
   的目标）可以重试；**发消息 / 支付 / 删除这类不可逆动作不重试**
   （重试可能重复生效 —— 那比失败更糟）。
2. **退避有上限** —— 最多 **3** 次，退避 **200 / 500 / 1200 ms**。
3. **每次重试前重新感知** —— 重试走的是**重新走一遍动作执行缝**
   （重新定位 → 新坐标），**复用旧坐标的那条路径不存在**
   （旧帧与旧控件树快照在重试前被丢掉）。

⚠️ **可逆性的判定是 fail-closed 的**：只有**明确**在可重试清单里的动作种类
才重试，其余一律不重试（宁可少重试，也不重复执行一个不可逆动作）。
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Any

from minicode.action.errors import ErrorCode
from minicode.action.protocol import ActionKind

from .keywords import IRREVERSIBLE_KEYWORDS, IRREVERSIBLE_KEYWORDS_SOURCE

__all__ = [
    "RETRY_BACKOFF_MS",
    "RETRY_BACKOFF_SOURCE",
    "MAX_RETRIES",
    "IRREVERSIBLE_KEYWORDS",
    "IRREVERSIBLE_KEYWORDS_SOURCE",
    "RETRYABLE_KINDS",
    "NOT_RETRYABLE_CODES",
    "Reversibility",
    "classify_reversibility",
    "RetryDecision",
    "RetryPolicy",
    "AttemptRecord",
    "RetryingSeam",
]

#: 退避表（毫秒）。**取值来源**：工单 M12 AC 3「重试带退避与上限（3 次，
#: 200 / 500 / 1200 ms）」。
RETRY_BACKOFF_MS: tuple[int, ...] = (200, 500, 1200)

#: 退避表的**取值来源**（随记录落盘 —— 仓库规矩：每个阈值都要说清出处）。
RETRY_BACKOFF_SOURCE = "工单 M12 AC 3（重试带退避与上限：3 次，200 / 500 / 1200 ms）"

#: 重试次数上限（**不含**首次尝试）。
MAX_RETRIES = 3

#: **天然可重试**的动作种类（失败后重试不产生额外后果）。
#:
#: * ``move`` —— 移动（悬停）：指针位置随时可以再挪。
#: * ``scroll`` —— 滚动：滚出去可以再滚回来。
#:
#: ⚠️ ``click`` **不在这里**：点击可不可逆取决于**点的是谁**
#: （点一个输入框是聚焦，点「发送」就是不可逆）—— 由
#: :func:`classify_reversibility` 按目标描述判。
RETRYABLE_KINDS = frozenset({ActionKind.MOVE, ActionKind.SCROLL})

#: **明确不重试**的错误码。
#:
#: * ``uac_or_secure_desktop`` —— 提权 / 安全桌面：重试只是白等（交人）。
#: * ``program_rejected`` —— 决策层产出被拒：重试同一个程序没有意义。
#: * ``expectation_failed`` —— 动作做了但期望不成立：那是**反思层**的事。
#: * ``invalid_target`` —— 请求本身非法：重试同一个非法请求没有意义。
NOT_RETRYABLE_CODES = frozenset(
    {
        ErrorCode.UAC_OR_SECURE_DESKTOP,
        ErrorCode.PROGRAM_REJECTED,
        ErrorCode.EXPECTATION_FAILED,
        ErrorCode.INVALID_TARGET,
    }
)


def _kind_value(kind: ActionKind | str) -> str:
    """动作种类的字符串值（认得的就用协议里的值，认不得的原样留着）。"""
    if isinstance(kind, ActionKind):
        return kind.value
    try:
        return ActionKind(str(kind)).value
    except ValueError:
        return str(kind)


@dataclass(frozen=True)
class Reversibility:
    """一个动作**可不可逆**的判定。"""

    reversible: bool
    #: 判定的类别：``"reversible"`` / ``"irreversible"``。
    action_class: str
    #: 人读的理由（为什么可 / 不可逆）。
    reason: str


def classify_reversibility(
    kind: ActionKind | str,
    target_description: str = "",
    intent: str = "",
) -> Reversibility:
    """这个动作**可不可逆**（能不能自动重试）。

    判据只有两条，顺序固定：

    1. 目标描述或意图**命中不可逆词表** → 不可逆（不重试）；
    2. 否则看**动作种类**：``move`` / ``scroll`` 可逆；
       ``click`` 可逆（已排除不可逆目标）；**其余一律不可逆**。
    """
    text = f"{target_description} {intent}"
    for keyword in IRREVERSIBLE_KEYWORDS:
        if keyword in text:
            return Reversibility(
                reversible=False,
                action_class="irreversible",
                reason=f"目标命中不可逆词「{keyword}」（重试可能重复生效）",
            )
    value = _kind_value(kind)
    if value in RETRYABLE_KINDS:
        return Reversibility(
            reversible=True,
            action_class="reversible",
            reason=f"{value}（移动 / 滚动天然可逆）",
        )
    if value == ActionKind.CLICK.value:
        return Reversibility(
            reversible=True,
            action_class="reversible",
            reason="点击（目标不是发消息 / 支付 / 删除这类不可逆动作）",
        )
    return Reversibility(
        reversible=False,
        action_class="irreversible",
        reason=f"{value or '（未知动作）'} 不在可重试的动作种类清单里（默认不可逆）",
    )


@dataclass(frozen=True)
class RetryDecision:
    """要不要重试的判定。"""

    retry: bool
    #: 这次重试前要等多久（毫秒）。
    delay_ms: int = 0
    #: 人读的理由。
    reason: str = ""


@dataclass(frozen=True)
class AttemptRecord:
    """一次**尝试**的记录。

    * ``attempt`` —— 第几次尝试（1 = 首次）；
    * ``delay_ms`` —— 这次尝试**之前**等了多久（首次 = 0）；
    * ``point`` —— **这一次**的落点（物理表面空间）——
      重试那一次的落点与上一次**不同**，正是「重新感知」的证据；
    * ``reason`` —— 失败后为什么重试（成功时为空）。
    """

    attempt: int
    delay_ms: int
    ok: bool
    point: tuple[int, int] | None
    error_code: str | None
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "attempt": self.attempt,
            "delay_ms": self.delay_ms,
            "ok": self.ok,
            "point": list(self.point) if self.point else None,
            "error_code": self.error_code,
            "reason": self.reason,
        }


class RetryPolicy:
    """重试策略：**只重试可逆动作**，退避 200 / 500 / 1200 ms，上限 3 次。"""

    def __init__(
        self,
        *,
        backoff_ms: tuple[int, ...] = RETRY_BACKOFF_MS,
        max_retries: int = MAX_RETRIES,
    ):
        self.backoff_ms = tuple(int(value) for value in backoff_ms)
        self.max_retries = int(max_retries)

    def decide(
        self,
        *,
        attempt: int,
        kind: ActionKind | str,
        target_description: str = "",
        intent: str = "",
        error_code: ErrorCode | None = None,
    ) -> RetryDecision:
        """这一步失败之后要不要重试。

        ``attempt`` 是**已经试过几次**（1 = 刚失败的是首次尝试）。
        """
        if attempt > self.max_retries:
            return RetryDecision(
                retry=False,
                reason=f"重试次数已到上限（{self.max_retries} 次）",
            )
        if error_code is not None and error_code in NOT_RETRYABLE_CODES:
            return RetryDecision(
                retry=False, reason=f"错误码 {error_code.value} 不重试（换策略 / 交人）"
            )
        reversibility = classify_reversibility(kind, target_description, intent)
        if not reversibility.reversible:
            return RetryDecision(
                retry=False,
                reason=f"不可逆动作，不自动重试：{reversibility.reason}",
            )
        delay = self.backoff_ms[min(attempt, len(self.backoff_ms)) - 1]
        return RetryDecision(
            retry=True,
            delay_ms=delay,
            reason=f"可重试（{reversibility.reason}），退避 {delay} ms",
        )


class RetryingSeam:
    """给**动作执行缝**套一层重试（工单 M12 AC 3 / AC 4）。

    * **只重试可逆动作**（判据在 :class:`RetryPolicy`）；
    * **退避**：200 / 500 / 1200 ms，**上限 3 次**；
    * **每次重试前重新感知**：重试是**重新走一遍动作执行缝**
      （重新定位 → 新坐标），上一帧画面与上一份控件树快照在重试前被丢掉
      —— **复用旧坐标的那条路径不存在**。

    ⚠️ 本类**不是第二个缝**：它包的就是那条缝（端口形状不变），
    只是把「失败之后怎么办」这一段策略接在上面。
    """

    def __init__(
        self,
        seam: Any,
        *,
        policy: RetryPolicy | None = None,
        sleeper: Callable[[float], None] = time.sleep,
    ):
        self._seam = seam
        self.policy = policy or RetryPolicy()
        self._sleep = sleeper
        #: 每一次**尝试**的记录（供断言与落盘）。
        self.attempts: list[AttemptRecord] = []

    @property
    def executor(self) -> Any:
        """透传执行器（``type_text`` / ``press_key`` 走它 —— 那两类**不重试**）。"""
        return self._seam.executor

    @property
    def seam(self) -> Any:
        """被包的缝（需要别的端口时从这里拿）。"""
        return self._seam

    @property
    def retries(self) -> int:
        """**重试**了几次（= 尝试次数 − 1）。"""
        return max(0, len(self.attempts) - 1)

    def run(self, request: Any) -> Any:
        """跑一次动作；失败且可重试时按退避重试（**重新感知**）。"""
        attempt = 0
        pending_delay = 0
        current = request
        while True:
            attempt += 1
            result = self._seam.run(current)
            error_code = (
                result.result.error_code if result.result.error_code else None
            )
            point = result.final_point
            if result.ok:
                self.attempts.append(
                    AttemptRecord(
                        attempt=attempt,
                        delay_ms=pending_delay,
                        ok=True,
                        point=point,
                        error_code=None,
                    )
                )
                return result
            decision = self.policy.decide(
                attempt=attempt,
                kind=request.action_kind,
                target_description=request.target_description,
                intent=result.intent or "",
                error_code=error_code,
            )
            self.attempts.append(
                AttemptRecord(
                    attempt=attempt,
                    delay_ms=pending_delay,
                    ok=False,
                    point=point,
                    error_code=error_code.value if error_code else None,
                    reason=decision.reason,
                )
            )
            if not decision.retry:
                return result
            pending_delay = decision.delay_ms
            self._sleep(pending_delay / 1000.0)
            # **重新感知**：丢掉上一帧画面与上一份控件树快照，
            # 让这一轮重新定位（→ 新坐标），**不复用旧坐标**。
            current = replace(request, frame=None, tree_snapshot=None)

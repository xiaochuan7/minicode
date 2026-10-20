"""模型调用的**假实现**。

替掉真实模型 HTTP 调用，让单测不花钱、不联网。预置的响应按调用顺序消费。

其中一条预置形态是**坑④ 的替身**：「思考吃光预算导致无输出」。
它表现为**显式失败**（:class:`~minicode.model.errors.NoTextOutputError`），
和真实客户端在同样形态下的行为一致 —— 系统不会把它当成「成功但没找到」。

**端口形状与真实现一致**（ADR-0007：换注入点，端口的形状不许变）：

* 省略思考预算（effort）→ 同样抛 :class:`EffortRequiredError`；
* 用量明细是**扁平**形状（``input_tokens`` / ``output_tokens`` / ``thinking_tokens``），
  与真客户端 :func:`minicode.model.client.usage_of` 的返回一致。
"""

from __future__ import annotations

from typing import Any

from .client import ModelReply
from .effort import Effort
from .errors import EffortRequiredError, NoTextOutputError

__all__ = ["FakeModelClient", "no_text_output_outcome"]


def no_text_output_outcome(*, thinking_tokens: int, max_tokens: int) -> NoTextOutputError:
    """预置「思考吃光预算 → 没有文本输出」的失败形态。

    用量明细与真客户端同形状（扁平）。
    """
    usage = {
        "input_tokens": 0,
        "output_tokens": thinking_tokens,
        "thinking_tokens": thinking_tokens,
    }
    return NoTextOutputError(
        f"思考链吃光了 token 预算（思考 {thinking_tokens} / 预算 {max_tokens}），本次没有文本输出",
        usage=usage,
    )


class FakeModelClient:
    """模型调用的假实现。

    预置的每一项要么是一个 :class:`ModelReply`（正常返回），
    要么是一个异常（按原样抛出）。
    """

    def __init__(self, *outcomes: ModelReply | Exception):
        self._outcomes = list(outcomes)
        #: 每次调用的参数（供断言）。
        self.calls: list[dict[str, Any]] = []
        #: 已经返回的回复。
        self.replies: list[ModelReply] = []

    def complete(
        self,
        prompt: str,
        *,
        effort: Effort | str | None = None,
        max_tokens: int = 2000,
        image_base64: str | None = None,
        thinking_disabled: bool = False,
    ) -> ModelReply:
        if effort is None:
            # 与真实现一致：省略档位即报错，不静默按最高档跑。
            raise EffortRequiredError(
                "思考预算（effort）必须显式给出；省略会按服务端缺省的最高档跑，因此直接报错"
            )
        self.calls.append(
            {
                "prompt": prompt,
                "effort": effort,
                "max_tokens": max_tokens,
                "image_base64": image_base64,
                "thinking_disabled": thinking_disabled,
            }
        )
        if not self._outcomes:
            raise AssertionError("假模型实现没有预置响应")
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        self.replies.append(outcome)
        return outcome

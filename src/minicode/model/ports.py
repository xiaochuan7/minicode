"""模型调用的端口。

动作执行缝下面挂的是**真实模型 HTTP 调用**；这条端口规定它的形状，
真实现是 :class:`~minicode.model.client.ModelClient`，
假实现是 :class:`~minicode.model.fakes.FakeModelClient`。

**端口形状不随替身变**：换了注入点，返回类型还是
:class:`~minicode.model.client.ModelReply`，失败还是那几类异常，
省略思考预算（effort）一样报错。
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from .client import ModelReply
from .effort import Effort

__all__ = ["ModelCaller"]


@runtime_checkable
class ModelCaller(Protocol):
    """模型调用端口。"""

    def complete(
        self,
        prompt: str,
        *,
        effort: Effort | str | None = None,
        max_tokens: int = ...,
        image_base64: str | None = None,
        images_base64: Sequence[str] = (),
        thinking_disabled: bool = ...,
    ) -> ModelReply:
        """发一次请求；思考预算（effort）必须显式给出（省略即报错）。

        ``images_base64`` 是**追加**的那几张图（工单 M10 的上下文窗口要把
        最近几张完整图一起带上）；``image_base64`` 是主图。
        """
        ...

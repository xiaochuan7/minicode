"""模型客户端的异常。

分层：**配置问题**（本地）→ **传输问题**（网络）→ **模型侧错误**（服务端回的）
→ **没有文本输出**（服务端回 200 但内容为空）。四类不混成一个码。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

__all__ = [
    "ModelError",
    "ModelConfigError",
    "EffortRequiredError",
    "ModelTransportError",
    "ModelSideError",
    "NoTextOutputError",
]


class ModelError(Exception):
    """模型客户端的异常基类。"""


class ModelConfigError(ModelError):
    """仓库配置有问题：缺项、为空、或者指向了不允许的通道。

    消息里**点名缺哪一项**（配置项名 + 取值来源），不静默回退默认值。
    """


class EffortRequiredError(ModelError):
    """思考档位没给或给错了。

    * 省略档位 → 报错（服务端缺省是最高档，等于每步烧思考链）；
    * 档位不在五档之内 → 报错并列出五档。
    """


class ModelTransportError(ModelError):
    """请求没拿到 HTTP 响应：网络不可达、超时、DNS 失败等。"""


class ModelSideError(ModelError):
    """模型侧返回了错误。

    **如实回传**：HTTP 状态、错误类型、错误文本都不改写、不重试成别的参数。
    """

    def __init__(
        self,
        status: int,
        message: str,
        *,
        error_type: str | None = None,
        body: str | None = None,
        usage: Mapping[str, Any] | None = None,
    ):
        super().__init__(message)
        self.status = status
        self.message = message
        self.error_type = error_type
        self.body = body
        self.usage = usage


class NoTextOutputError(ModelError):
    """HTTP 200，但**本次没有文本输出**。

    典型成因是**思考链把 token 预算吃光**（默认档 = 最高档时最常见）。
    这必须表现为**显式失败**，不能呈现成「成功但坐标为空」。
    """

    def __init__(self, message: str, *, usage: Mapping[str, Any] | None = None):
        super().__init__(message)
        self.message = message
        self.usage = usage

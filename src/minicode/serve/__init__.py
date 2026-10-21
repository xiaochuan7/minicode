"""**对外形态**（工单 M18）：桌面控制的独立命令行与 HTTP 暴露。

同一套动作协议经两个前端被调用：

* :mod:`minicode.serve.cli` —— 命令行（``action`` 发起一次动作 / ``log`` 读回动作日志 /
  ``http`` 起一个 HTTP 服务）；
* :mod:`minicode.serve.http` —— HTTP（``POST /v1/actions``）。

两个前端都只做「解析 → 调 :mod:`minicode.serve.service` → 渲染」：
**动作语义只有一份**（工单 M18 AC3），坐标口径由动作执行缝保证。
"""

from __future__ import annotations

from .protocol import PROTOCOL_VERSION, ActionRequest, ActionResponse, RequestError
from .service import ActionService

__all__ = [
    "PROTOCOL_VERSION",
    "ActionRequest",
    "ActionResponse",
    "ActionService",
    "RequestError",
]

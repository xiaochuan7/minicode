"""minicode.model —— 模型客户端（M1）。

对外入口：

* :mod:`minicode.model.config` —— 仓库配置（凭据的唯一来源）。
* :mod:`minicode.model.effort` —— 思考档位（五档显式枚举）。
* :mod:`minicode.model.client` —— 模型客户端（主链路唯一的模型调用入口）。
* :mod:`minicode.model.request_log` —— 请求日志（每条请求一条，带档位字段）。
* :mod:`minicode.model.fakes` —— 模型调用的假实现（含坑④ 的失败形态）。
* :mod:`minicode.model.regressions` —— 四个坑的**真调 API** 集成回归
  （单独入口 ``python -m minicode.model.regressions``，不被单测命令带跑）。
"""

from __future__ import annotations

__all__ = [
    "client",
    "config",
    "effort",
    "errors",
    "fakes",
    "ports",
    "request_log",
]

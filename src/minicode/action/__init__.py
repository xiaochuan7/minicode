"""minicode.action —— 动作协议 v1 与动作执行缝（M0）。

对外入口：

* :mod:`minicode.action.protocol` —— 协议形状（版本号 / 动作种类 / 参数 / 结果 / 错误码）。
* :mod:`minicode.action.seam` —— **动作执行缝**（本项目唯一的测试缝）。
* :mod:`minicode.action.records` —— 动作记录（只落截图哈希，不落原图）。
* :mod:`minicode.action.fakes` —— 三个假实现（截图源 / 控件树 / 输入注入）。
"""

from __future__ import annotations

__all__ = [
    "errors",
    "geometry",
    "protocol",
    "surface",
]

"""脱敏器的三类异常。

* :class:`RedactionError` —— 底座。
* :class:`RedactionConfigError` —— **启用期校验**失败（配置层拒绝非法组合）。
* :class:`ChannelUnavailableError` —— **硬闸门**拒绝：脱敏未上线，
  「把控件树送进模型上下文」这条通道不可用。
"""

from __future__ import annotations

__all__ = ["RedactionError", "RedactionConfigError", "ChannelUnavailableError"]


class RedactionError(Exception):
    """脱敏相关的异常底座。"""


class RedactionConfigError(RedactionError):
    """配置层拒绝了一个非法组合（明确报错，不静默放过）。"""


class ChannelUnavailableError(RedactionError):
    """通道不可用：脱敏没上线，不许把控件树送进模型上下文。"""

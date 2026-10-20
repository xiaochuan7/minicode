"""脱敏计数（可观测性）。

工单 M2 AC 6：记下**密码框替换数 / 正则命中数 / 丢弃正文类字段数 / 输出长度**，
使「某条通道没走脱敏」这件事能被看见——追踪记录里没有这份计数，就是没走过脱敏。
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["RedactionReport"]


@dataclass(frozen=True)
class RedactionReport:
    """一次脱敏的计数。"""

    #: 密码框的值被替换成占位符的次数。
    password_replacements: int = 0
    #: 正则命中的敏感片段数。
    regex_hits: int = 0
    #: 因控件是正文类而丢弃的值字段数。
    dropped_body_fields: int = 0
    #: 因未登记而丢弃的字段数（fail-closed）。
    #: 工单点名的是四个计数，这一个是为 **fail-closed 的丢弃量可见**加的。
    dropped_unknown_fields: int = 0
    #: 模型侧文本的长度（字符数）。
    output_length: int = 0

    def to_dict(self) -> dict[str, int]:
        """落盘用的字典形状。"""
        return {
            "password_replacements": self.password_replacements,
            "regex_hits": self.regex_hits,
            "dropped_body_fields": self.dropped_body_fields,
            "dropped_unknown_fields": self.dropped_unknown_fields,
            "output_length": self.output_length,
        }

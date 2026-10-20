"""敏感模式与占位符。

五类敏感模式（架构决策备忘 §2.6 **C-15** 与工单 M2 AC 1 点名的五类）：

============ ==================================================
类别          形态
============ ==================================================
密钥前缀形态  ``sk-`` / ``ghp_`` / ``github_pat_`` / ``AKIA`` / ``xox*-`` / ``AIza``
JWT 三段      ``eyJ`` 开头的三段 base64url
长 base64     40 位以上的 base64 串
长十六进制串  32 位以上的十六进制串
私钥头        ``-----BEGIN … PRIVATE KEY-----``
============ ==================================================

两个占位符都是**固定**的：

* :data:`PASSWORD_PLACEHOLDER` —— 密码框的值（架构决策备忘 §2 L5 ③ 写死成 ``***``）；
* :data:`SECRET_PLACEHOLDER` —— 正则命中的敏感片段。

命中一律替换成占位符，**不保留长度、不保留前后文**。
"""

from __future__ import annotations

import re

__all__ = [
    "PASSWORD_PLACEHOLDER",
    "SECRET_PLACEHOLDER",
    "SECRET_PATTERNS",
    "redact_secrets",
]

#: 密码框的值在模型侧一律是它。
PASSWORD_PLACEHOLDER = "***"

#: 正则命中的敏感片段在模型侧一律是它。
SECRET_PLACEHOLDER = "[REDACTED]"

#: 敏感模式表。**顺序敏感**：先整体（私钥头 / JWT）后片段（十六进制 / base64），
#: 否则 JWT 的第三段会被当成一段长 base64 先吃掉。
SECRET_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "私钥头",
        re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----"),
    ),
    (
        "密钥前缀形态",
        re.compile(
            r"(?:sk-[A-Za-z0-9_-]{8,}|ghp_[A-Za-z0-9]{8,}|github_pat_[A-Za-z0-9_]{8,}"
            r"|AKIA[0-9A-Z]{12,}|xox[baprs]-[A-Za-z0-9-]{8,}|AIza[0-9A-Za-z_-]{10,})"
        ),
    ),
    (
        "JWT 三段",
        re.compile(r"eyJ[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]{6,}"),
    ),
    (
        "长十六进制串",
        re.compile(r"(?<![0-9A-Fa-f])[0-9A-Fa-f]{32,}(?![0-9A-Fa-f])"),
    ),
    (
        "长 base64",
        re.compile(r"(?<![A-Za-z0-9+/])[A-Za-z0-9+/]{40,}={0,2}(?![A-Za-z0-9+/=])"),
    ),
)


def redact_secrets(text: str) -> tuple[str, int]:
    """把 ``text`` 里命中的敏感片段替换成固定占位符。

    返回 ``(替换后的文本, 命中次数)``。
    """
    if not text:
        return text, 0
    hits = 0
    for _label, pattern in SECRET_PATTERNS:
        text, count = pattern.subn(SECRET_PLACEHOLDER, text)
        hits += count
    return text, hits

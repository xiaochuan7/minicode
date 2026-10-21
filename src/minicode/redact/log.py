"""**落盘前的脱敏**（工单 M17 AC 5）。

**日志里不出现密码、密钥等模式的明文** —— 用的是**脱敏器同一套规则**
（:data:`minicode.redact.patterns.SECRET_PATTERNS`，五类敏感模式）。
不另写一套判据：两套判据迟早各说各话。

⚠️ **它放在脱敏器包里，不在审计包里**：审计层（``minicode.audit``）在动作层
**之上**，动作层落盘时要脱敏 → 脱敏规则得住在**下层**（``minicode.redact``），
否则两个包会互相依赖（动作层 → 审计层 → 动作层）。

为什么日志要单独脱敏一遍
------------------------

脱敏器（M2）排在**送模型之前**，管的是「界面内容不进模型上下文」。
**日志是另一条出路**：控件名里的密钥明文会照原样落盘
（``docs/00-现状.md`` §6.3 记着这条欠账，本单补上）。

两层**不重叠**（一层管「界面内容进不进模型」，一层管「日志里有没有明文」），
但**规则是同一套**。

⚠️ **摘要字段放行**：截图哈希是 64 位十六进制，按「长十六进制串」那条规则
会被**整条抹掉** —— 但它是**摘要**不是明文，所以放行。
⚠️ **光看键名不够**：任何叫 ``*_sha256`` 的字段都放行的话，明文藏在里面就
查不出来了 → **键名与值都像摘要**才算（见 :func:`is_digest_key`）。
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from .patterns import SECRET_PATTERNS, redact_secrets

__all__ = [
    "DIGEST_KEY_SUFFIX",
    "is_digest_key",
    "sensitive_hits",
    "sanitize_text",
    "sanitize_value",
    "sanitize_record",
    "scan_record",
]

#: 键名以此结尾 = **可能**装的是摘要（不是明文）→ 要再验一次值。
DIGEST_KEY_SUFFIX = "sha256"

#: 摘要的样子：**64 位十六进制**（SHA-256 的十六进制写法）。
_HEX_DIGEST = re.compile(r"[0-9a-f]{64}")


def is_digest_key(key: str, value: Any = None) -> bool:
    """这个键装的是**摘要**（截图哈希 / 帧哈希）—— 不是明文。

    ⚠️ **键名与值都像摘要**才算：光看键名会留下一个洞 ——
    任何叫 ``*_sha256`` 的字段都会被放行，明文藏在里面就查不出来了。
    """
    if not key.lower().endswith(DIGEST_KEY_SUFFIX):
        return False
    return isinstance(value, str) and _HEX_DIGEST.fullmatch(value) is not None


def sensitive_hits(text: str) -> list[str]:
    """这段文本命中了哪几类敏感模式（**空 = 干净**）。"""
    return [label for label, pattern in SECRET_PATTERNS if pattern.search(text)]


def sanitize_text(text: str) -> str:
    """把命中的敏感片段换成固定占位符（不保留长度、不保留前后文）。

    **替换用的是脱敏器自己的那个函数** —— 占位符与替换规则都只在
    :mod:`minicode.redact.patterns` 里有一份。
    """
    cleaned, _hits = redact_secrets(text)
    return cleaned


def sanitize_value(value: Any, *, key: str = "") -> Any:
    """递归脱敏一个值（按动作记录的形状：dict / list / 标量）。"""
    if is_digest_key(key, value):
        return value
    if isinstance(value, str):
        return sanitize_text(value)
    if isinstance(value, Mapping):
        return {name: sanitize_value(item, key=str(name)) for name, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [sanitize_value(item, key=key) for item in value]
    return value


def sanitize_record(record: Mapping[str, Any]) -> dict[str, Any]:
    """脱敏一条动作记录（落盘形状）。"""
    sanitized = sanitize_value(dict(record))
    if not isinstance(sanitized, dict):  # pragma: no cover - 顶层一定是 dict
        raise TypeError("动作记录必须是 dict")
    return sanitized


def scan_record(record: Mapping[str, Any]) -> list[str]:
    """扫一条记录里**残留**的敏感内容（**空 = 干净**）。

    与 :func:`sanitize_record` 走同一套规则、同样的摘要字段放行 ——
    所以「脱敏过一遍的记录再扫一遍」一定是干净的。
    """
    hits: list[str] = []
    _scan(record, "", hits)
    return hits


def _scan(value: Any, key: str, hits: list[str]) -> None:
    if is_digest_key(key, value):
        return
    if isinstance(value, str):
        hits.extend(sensitive_hits(value))
    elif isinstance(value, Mapping):
        for name, item in value.items():
            _scan(item, str(name), hits)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _scan(item, key, hits)

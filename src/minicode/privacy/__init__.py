"""**隐私策略**（工单 M20）。

* :mod:`minicode.privacy.policy` —— 策略本体：敏感窗口黑名单 + 送模型 / 不送 / 落盘
  三组条款（每条都指向实现里的一个位置）。
* :mod:`minicode.privacy.guard` —— **硬闸门**：黑名单窗口在抓帧之前被拦下。
* :mod:`minicode.privacy.document` —— **策略文档的渲染**（文档由实现生成）。

术语按 `CONTEXT.md`：**敏感窗口**、**脱敏**、**动作记录**。
"""

from __future__ import annotations

from .document import render_policy_document, resolve_implementation
from .guard import SENSITIVE_WINDOW_SKIPPED_NOTE, PrivacyVerdict, SensitiveWindowGuard
from .policy import (
    SENSITIVE_TITLE_KEYWORDS,
    SENSITIVE_WINDOW_CLASSES,
    SENSITIVE_WINDOW_SOURCE,
    PolicyClause,
    PrivacyPolicy,
    SensitiveWindowBlacklist,
    SensitiveWindowMatch,
    default_policy,
)

__all__ = [
    "PolicyClause",
    "PrivacyPolicy",
    "PrivacyVerdict",
    "SENSITIVE_TITLE_KEYWORDS",
    "SENSITIVE_WINDOW_CLASSES",
    "SENSITIVE_WINDOW_SKIPPED_NOTE",
    "SENSITIVE_WINDOW_SOURCE",
    "SensitiveWindowBlacklist",
    "SensitiveWindowGuard",
    "SensitiveWindowMatch",
    "default_policy",
    "render_policy_document",
    "resolve_implementation",
]

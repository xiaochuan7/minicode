"""**审批的端口**（工单 M16）。

三样外部依赖，都是**缝上的可替换依赖**（``CONTEXT.md`` 的「端口 / 注入点」：

* :class:`Approver` —— **谁来批**（人 / 脚本 / 策略）；
* :class:`WindowInspector` —— **当前前台窗口是谁**（判「敏感窗口」要用）；
* :class:`SensitiveWindowPolicy` —— **哪些窗口敏感**（M20 会把真黑名单接进来）。

端口规定形状，注入点决定接哪一个实现 —— **换注入点，端口的形状不许变**。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from .request import ApprovalDecision, ApprovalRequest

__all__ = [
    "Approver",
    "WindowInfo",
    "WindowInspector",
    "SensitiveWindowPolicy",
]


@runtime_checkable
class Approver(Protocol):
    """**审批人**：拿到一份审批请求，给一份结论。"""

    def approve(self, request: ApprovalRequest) -> ApprovalDecision:
        ...


@dataclass(frozen=True)
class WindowInfo:
    """一个窗口的**身份**：句柄 + 标题。"""

    hwnd: int = 0
    title: str = ""


@runtime_checkable
class WindowInspector(Protocol):
    """读**当前前台窗口**（判「这一帧里有没有敏感窗口」要用）。"""

    def foreground(self) -> WindowInfo:
        ...


@runtime_checkable
class SensitiveWindowPolicy(Protocol):
    """**哪些窗口不许拍**（工单 M16 AC 1 的「截含敏感窗口的屏」）。

    ⚠️ **策略内容归 M20**（敏感窗口黑名单 + 策略成文）——
    本单只给**判据的位置**（这个端口）与**审批请求的生成**，
    **不自带词表**：自带一份词表就是替 M20 做决定，两层会重叠
    （`docs/tickets/README.md`：「M20 管『哪些窗口根本不许拍 / 不许发』」）。

    没接这个端口 = **没有「敏感窗口」这个概念**（判定留给策略接进来时做）。
    """

    def is_sensitive(self, window: WindowInfo) -> bool:
        ...

    def source(self) -> str:
        ...

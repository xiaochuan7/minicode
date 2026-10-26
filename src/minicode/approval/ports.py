"""**审批的端口**（工单 M16）。

三样外部依赖，都是**缝上的可替换依赖**（即端口 / 注入点）：

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
    """一个窗口的**身份**：句柄 + 标题 + 类名。"""

    hwnd: int = 0
    title: str = ""
    #: 窗口**类名**（如 ``#32770`` = Win32 标准对话框）。
    #:
    #: 工单 M20 的「对话框类名」判据要用它（敏感窗口黑名单）——
    #: 标题之外的第二条判据，判的是**窗口的种类**而不是它的内容。
    class_name: str = ""


@runtime_checkable
class WindowInspector(Protocol):
    """读**窗口的身份**（判「这一帧里有没有敏感窗口」要用）。

    两个问题，两个方法：

    * :meth:`foreground` —— **当前前台窗口**是谁（M16 的审批闸门问这个）；
    * :meth:`visible_windows` —— **这一帧里看得见的**窗口有哪些
      （M20 的隐私闸门问这个）。
    """

    def foreground(self) -> WindowInfo:
        ...

    def visible_windows(self) -> tuple[WindowInfo, ...]:
        """**看得见的**顶层窗口（标题 + 类名）。

        抓的是**整屏**帧 —— 画面里有什么，取决于**屏幕上有哪些窗口**，
        不只是前台那一个：前台背后的密码管理器窗口照样在那一帧里。
        ⚠️ 只判前台窗口会漏（那是静默漏判，隐私面最不能要的那种）。
        """
        ...


@runtime_checkable
class SensitiveWindowPolicy(Protocol):
    """**哪些窗口不许拍**（工单 M16 AC 1 的「截含敏感窗口的屏」）。

    ⚠️ **策略内容归 M20**（敏感窗口黑名单 + 策略成文）——
    本单只给**判据的位置**（这个端口）与**审批请求的生成**，
    **不自带词表**：自带一份词表就是替 M20 做决定，两层会重叠。

    ✅ **工单 M24：这个端口现在由 M20 的黑名单实现提供**
    （``minicode.privacy.policy:SensitiveWindowBlacklist.is_sensitive``）——
    **词表只有那一份**（端口规定形状，实现给出判据）。

    没接这个端口 = **没有「敏感窗口」这个概念**（判定留给策略接进来时做）。
    """

    def is_sensitive(self, window: WindowInfo) -> bool:
        ...

    def source(self) -> str:
        ...

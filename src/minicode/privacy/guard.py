"""**敏感窗口的硬闸门**（工单 M20 AC 1）。

**根本不许拍、不许发**：黑名单窗口（标题命中敏感词表 **或** 对话框类名）
在**抓帧之前**就被拦下 —— 那一帧根本没拍，画面因此不进模型上下文
（敏感窗口的画面一旦被抓走，事后拦就晚了）。

四条硬规矩：

1. **判的是「这一帧里看得见的窗口」**，不只是前台那一个 ——
   抓的是**整屏**帧，前台背后的密码管理器窗口照样在那一帧里
   （只判前台 = 静默漏判，隐私面最不能要的那种漏）。
2. **硬闸门，不是审批**：M16 的机制是「人点头才拍」，本单是**根本不许拍** ——
   两者互斥，因此这里**不接** M16 的审批闸门（见 :mod:`minicode.privacy.policy`）。
3. **fail-closed**：**读不到**窗口列表 → 按敏感处理（我们不知道屏幕上有没有
   敏感窗口，那就不许悄悄拍下来）。
4. **留下一笔**：被跳过时给出 :class:`PrivacyVerdict`，动作记录里据此记一笔
   （窗口标题 + 命中的判据 + 判据来源）；**画面一个字都不落**。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from minicode.approval.ports import WindowInspector

from .policy import SensitiveWindowBlacklist, SensitiveWindowMatch

__all__ = [
    "PrivacyVerdict",
    "SensitiveWindowGuard",
    "SENSITIVE_WINDOW_SKIPPED_NOTE",
    "windows_guard",
]


#: **敏感窗口被跳过**时写进结果里的话（落进动作记录的 ``notes``）。
#:
#: ⚠️ 它落进动作记录 —— 「记录里有一条『该窗口被跳过』的记录」由它承载
#: （与 M16 的 ``APPROVAL_HANDOFF_NOTE`` 同一形状）。
SENSITIVE_WINDOW_SKIPPED_NOTE = (
    "敏感窗口被跳过：黑名单窗口根本不许拍、不许发（工单 M20）"
)


@dataclass(frozen=True)
class PrivacyVerdict:
    """隐私闸门的结论。"""

    #: 这一步**能不能拍**（被拉黑 → 假）。
    blocked: bool
    #: 人读的理由。
    reason: str = ""
    #: 命中的是**哪一条**判据（没命中就是 ``None``；读不到窗口时也是 ``None``）。
    match: SensitiveWindowMatch | None = None

    def to_dict(self) -> dict[str, Any]:
        """落盘形状（进动作记录的 ``privacy`` 字段）。"""
        payload: dict[str, Any] = {"blocked": self.blocked, "reason": self.reason}
        if self.match is not None:
            payload.update(self.match.to_dict())
        return payload


class SensitiveWindowGuard:
    """**敏感窗口的硬闸门**：问一次「现在能不能拍」。"""

    def __init__(
        self,
        *,
        blacklist: SensitiveWindowBlacklist,
        window_inspector: WindowInspector,
    ):
        self.blacklist = blacklist
        self.window_inspector = window_inspector

    def check(self) -> PrivacyVerdict:
        """这一帧能不能拍。

        * 屏幕上**有**黑名单窗口 → **被拦下**（带命中的判据）；
        * 没有 → 放行；
        * **读不到**窗口列表 → 按敏感处理（fail-closed）。
        """
        try:
            windows = self.window_inspector.visible_windows()
        except Exception as exc:
            return PrivacyVerdict(
                blocked=True,
                reason=(
                    f"读不到屏幕上的窗口（{type(exc).__name__}: {exc}）"
                    "→ 按敏感窗口处理（fail-closed）"
                ),
            )
        for window in windows:
            match = self.blacklist.match(window)
            if match is None:
                continue
            return PrivacyVerdict(
                blocked=True,
                reason=(
                    f"敏感窗口：{match.describe()}"
                    f"（窗口标题「{match.window_title}」）→ 这一步根本不许拍、不许发"
                ),
                match=match,
            )
        return PrivacyVerdict(
            blocked=False,
            reason="屏幕上看得见的窗口都不在敏感窗口黑名单里",
        )


def windows_guard() -> SensitiveWindowGuard:
    """**真机闸门**：仓库那一份黑名单 + 真窗口读取。

    ⚠️ **接线只有这一份**：所有会抓真屏的入口都用它
    （命令行 / HTTP / MCP 的真机接线 · 动作 CLI · UIA 活体测试台 ·
    端到端评测台 · 浏览器通道的命令行）——
    漏接一处就是一处隐私洞。
    """
    from minicode.approval.windows import WindowsWindowInspector

    return SensitiveWindowGuard(
        blacklist=SensitiveWindowBlacklist(),
        window_inspector=WindowsWindowInspector(),
    )

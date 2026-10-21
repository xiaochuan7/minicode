"""**审批的假实现**（单测与端到端评测用）。

跟 :mod:`minicode.action.fakes` 一个路子：**缝以下**的外部依赖换成
可编程行为，让整条链路不需要真人、不需要真桌面就能被观测。
"""

from __future__ import annotations

from collections.abc import Sequence

from .ports import WindowInfo
from .request import ApprovalDecision, ApprovalRequest

__all__ = [
    "AutoApprover",
    "DenyApprover",
    "ScriptedApprover",
    "FakeWindowInspector",
    "FakeSensitivePolicy",
]


class AutoApprover:
    """**全都批**（端到端评测里「不危险的动作照跑」用）。

    ⚠️ 它**只**在评测与单测里接上 —— 生产路径上「全都批」等于没有闸门。
    """

    def __init__(self, reason: str = "假审批人：批准"):
        self.reason = reason
        #: 收到过的请求（断言用）。
        self.requests: list[ApprovalRequest] = []

    def approve(self, request: ApprovalRequest) -> ApprovalDecision:
        self.requests.append(request)
        return ApprovalDecision(
            approved=True, request_id=request.request_id, reason=self.reason
        )


class DenyApprover:
    """**全都不批**（fail-closed 那一侧的默认值）。"""

    def __init__(self, reason: str = "假审批人：拒绝"):
        self.reason = reason
        self.requests: list[ApprovalRequest] = []

    def approve(self, request: ApprovalRequest) -> ApprovalDecision:
        self.requests.append(request)
        return ApprovalDecision(
            approved=False, request_id=request.request_id, reason=self.reason
        )


class ScriptedApprover:
    """按**脚本**给结论（第 N 次调用给第 N 条；用完的按最后一条）。"""

    def __init__(self, decisions: Sequence[bool | ApprovalDecision] = ()):
        self._decisions = list(decisions)
        self.requests: list[ApprovalRequest] = []

    def approve(self, request: ApprovalRequest) -> ApprovalDecision:
        self.requests.append(request)
        index = min(len(self.requests) - 1, len(self._decisions) - 1)
        if index < 0:
            return ApprovalDecision(
                approved=False, request_id=request.request_id, reason="脚本用完了"
            )
        scripted = self._decisions[index]
        if isinstance(scripted, ApprovalDecision):
            return scripted
        return ApprovalDecision(
            approved=bool(scripted),
            request_id=request.request_id,
            reason="脚本结论",
        )


class FakeWindowInspector:
    """假的前台窗口读取。

    ``visible_windows`` 默认**只有前台那一个**；要多给几个后台窗口就传
    ``visible=``（M20 的隐私闸门问的是「这一帧里看得见的窗口有哪些」）。
    """

    def __init__(
        self,
        window: WindowInfo | None = None,
        *,
        visible: Sequence[WindowInfo] = (),
    ):
        self.window = window or WindowInfo()
        self._visible = tuple(visible)

    def foreground(self) -> WindowInfo:
        return self.window

    def visible_windows(self) -> tuple[WindowInfo, ...]:
        if self._visible:
            return self._visible
        return (self.window,)


class FakeSensitivePolicy:
    """假的**敏感窗口策略**：按标题关键词判（真黑名单由 M20 接手）。"""

    def __init__(self, keywords: tuple[str, ...] = ("密码", "password"), *, source: str = "假策略"):
        self._keywords = tuple(keywords)
        self._source = source

    def is_sensitive(self, window: WindowInfo) -> bool:
        title = (window.title or "").lower()
        if not title:
            return False
        return any(keyword.lower() in title for keyword in self._keywords)

    def source(self) -> str:
        return self._source

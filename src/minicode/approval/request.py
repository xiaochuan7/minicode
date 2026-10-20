"""**审批请求与结论**（工单 M16 AC 2 / AC 3）。

审批请求绑定**「要做什么」+「对哪个目标」**（工单 AC 2）：

* ``intent`` —— **意图**：这一次动作要达成什么；
* ``target_description`` + :class:`TargetPreview` —— **目标**：对哪个目标
  （可定位到具体控件 / 窗口：控件名、控件框、窗口标题）。

**dry-run**（工单 AC 3）：不可逆动作执行前先把**将要作用的对象**呈现出来
—— :class:`TargetPreview` 里有**目标控件的框**、**位置**与**截图哈希**，
人据此能看出「它点的是不是自己想的那个」。**没批就零动作下发。**

**结论绑定这一次动作**：审批结论必须带**同一个** ``request_id``
（:class:`ApprovalDecision`）—— 拿上一次动作的结论来批这一次，不认。

⚠️ **用词**：这一套东西叫「审批」，**不叫「确认」**
（`CONTEXT.md` 的「审批」词条把「确认 / 许可」列为 avoid 词）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .catalog import CATEGORY_TITLES, DangerCategory

__all__ = [
    "TargetPreview",
    "ApprovalRequest",
    "ApprovalDecision",
]


@dataclass(frozen=True)
class TargetPreview:
    """**dry-run 的呈现内容**：将要作用的对象。

    * ``control_name`` / ``control_rect`` —— 目标控件的**名字**与**框**
      （物理表面空间）；
    * ``point`` —— **位置**（最终落点，物理表面空间）；
    * ``window_title`` —— 目标所在的**窗口**；
    * ``screenshot_sha256`` —— 那一帧的**截图哈希**（与「只落哈希」同一条红线：
      **不落原图**）。
    """

    control_name: str = ""
    control_rect: tuple[int, int, int, int] | None = None
    point: tuple[int, int] | None = None
    window_title: str = ""
    screenshot_sha256: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "control_name": self.control_name,
            "control_rect": list(self.control_rect) if self.control_rect else None,
            "point": list(self.point) if self.point else None,
            "window_title": self.window_title,
            "screenshot_sha256": self.screenshot_sha256,
        }


@dataclass(frozen=True)
class ApprovalRequest:
    """一次**审批请求**。

    ⚠️ **它不是「继续」**：它问的是「**这一条**动作（意图 + 目标）批不批」。
    """

    #: **这一次动作**的标识。审批结论必须带同一个 id（工单 AC 2：
    #: 结论绑定到这一次动作，不是一次性授权后续同类动作）。
    request_id: str
    #: 危险动作的类别（机器可读；人读名由 :data:`CATEGORY_TITLES` 派生，
    #: **不存第二份** —— 两处各存一份就会各说各话）。
    category: DangerCategory
    #: **要做什么**（意图）。
    intent: str
    #: **对哪个目标**（目标描述）。
    target_description: str
    #: **dry-run**：将要作用的对象。
    preview: TargetPreview = field(default_factory=TargetPreview)
    #: 判定依据的来源串（这份清单的词是哪来的）。
    rule_source: str = ""

    @property
    def category_title(self) -> str:
        """类别人读名（派生，不另存）。"""
        return CATEGORY_TITLES[self.category]

    def to_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "category": self.category.value,
            "category_title": self.category_title,
            "intent": self.intent,
            "target_description": self.target_description,
            "preview": self.preview.to_dict(),
            "rule_source": self.rule_source,
        }

    def to_text(self) -> str:
        """**人读版**（交给人的那份：意图、目标、dry-run 的对象）。"""
        lines = [
            f"【审批请求】{self.category_title}（类别 {self.category.value}）",
            f"  要做什么：{self.intent or '（未说明）'}",
            f"  对哪个目标：{self.target_description or '（未说明）'}",
        ]
        preview = self.preview
        if preview.control_name:
            lines.append(f"  目标控件：{preview.control_name}")
        if preview.control_rect:
            lines.append(f"  控件框（物理像素）：{list(preview.control_rect)}")
        if preview.point:
            lines.append(f"  落点（物理像素）：{list(preview.point)}")
        if preview.window_title:
            lines.append(f"  所在窗口：{preview.window_title}")
        if preview.screenshot_sha256:
            lines.append(f"  截图哈希：{preview.screenshot_sha256}")
        lines.append(f"  判定依据：{self.rule_source}")
        return "\n".join(lines)


@dataclass(frozen=True)
class ApprovalDecision:
    """一次**审批结论**。

    ⚠️ ``request_id`` 必须与请求的一致 —— **对不上就是没批**
    （防止拿上一次的结论批这一次的动作）。
    """

    approved: bool
    #: 结论针对的**那一次**请求。
    request_id: str
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "approved": self.approved,
            "request_id": self.request_id,
            "reason": self.reason,
        }

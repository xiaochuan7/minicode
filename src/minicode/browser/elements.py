"""浏览器通道的**页面元素**与它到**控件候选**的映射。

页面元素与 UIA 元素是**两样东西**，但它们在缝里长得一样 ——
两条通道都产出 :class:`~minicode.action.ports.ControlCandidate`，
所以动作协议、坐标换算、脱敏器、动作记录**全都只有一套**（工单 M13 AC 2）。

坐标：``rect`` 一律是**物理表面空间**的 ``(left, top, right, bottom)``（物理像素）。
页面里的坐标是 CSS 像素，换算到物理表面空间只有一处
（:func:`minicode.browser.geometry.css_rect_to_physical`）。
"""

from __future__ import annotations

from dataclasses import dataclass

from minicode.action.ports import ControlCandidate

__all__ = [
    "BrowserElement",
    "Rect",
    "element_identity",
    "to_candidate",
]

#: 矩形：``(left, top, right, bottom)``（物理像素）。
Rect = tuple[int, int, int, int]


@dataclass(frozen=True)
class BrowserElement:
    """页面结构快照里的一个元素（读数）。

    * ``name`` —— **可见文本**（AX 节点的 accessible name）；
    * ``role`` —— **可访问性角色**（``button`` / ``textbox`` / ``link`` …）；
    * ``rect`` —— 物理表面空间的矩形；
    * ``element_id`` —— **页面元素引用**。它**有时效**：页面变了之后
      上一次的引用不再可用（工单 M13 AC 3）—— 所以它只在**本次快照**里有效；
    * ``value`` —— 元素当前的值（编辑框正文……）。**敏感面**：只进脱敏后的
      模型侧文本，不进日志（架构决策备忘 §2.6 C-15）。
    """

    name: str
    role: str = ""
    rect: Rect = (0, 0, 0, 0)
    element_id: str = ""
    value: str = ""
    enabled: bool = True
    window_handle: int | None = None

    @property
    def center(self) -> tuple[float, float]:
        from minicode.action.geometry import rect_center

        return rect_center(self.rect)


def element_identity(element: BrowserElement) -> tuple[str, str]:
    """元素的**身份**（跨快照可比）：角色 + 可见文本。

    ⚠️ **不用元素引用**（``element_id``）：页面元素引用有时效，
    页面一变它就不解析了（工单 M13 AC 3）——
    跨快照认出「还是那个元素」靠的是身份族 + 位置最近。
    """
    return (element.role, element.name)


def to_candidate(element: BrowserElement) -> ControlCandidate:
    """页面元素 → **控件候选**（两条通道共用的那一种）。"""
    return ControlCandidate(
        name=element.name,
        control_type=element.role,
        rect=element.rect,
        element_id=element.element_id,
        value=element.value,
        enabled=element.enabled,
        window_handle=element.window_handle,
    )

"""浏览器通道的**页面元素**与它到**控件候选**的映射。

页面元素与 UIA 元素是**两样东西**，但它们在缝里长得一样 ——
两条通道都产出 :class:`~minicode.action.ports.ControlCandidate`，
所以动作协议、坐标换算、脱敏器、动作记录**全都只有一套**（工单 M13 AC 2）。

坐标：``rect`` 一律是**物理表面空间**的 ``(left, top, right, bottom)``（物理像素）。
页面里的坐标是 CSS 像素，换算到物理表面空间只有一处
（:func:`minicode.browser.geometry.css_rect_to_physical`）。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from minicode.action.ports import ControlCandidate

__all__ = [
    "BrowserElement",
    "Rect",
    "TextMeasurement",
    "element_identity",
    "to_candidate",
]

#: 矩形：``(left, top, right, bottom)``（物理像素）。
Rect = tuple[int, int, int, int]


@dataclass(frozen=True)
class TextMeasurement:
    """**可见文本的实际区域**（文本度量这一层的产物，工单 M28）。

    * ``rect`` —— 可见文本的包围盒（**物理表面空间**）；``None`` = **判不了**；
    * ``reason`` —— 人读的说明：判不了时是**判不了的原因**（元素没有可见文本 /
      文本被裁 / 字体未加载），``element_box`` 那条记「为什么用元素框」；
    * ``element_box`` —— ``rect`` **不是**可见文本的区域，而是**元素自己的盒子**。
      这只在「元素没有文本子节点」时发生（输入框 / 按钮这类：屏上的字来自
      value / placeholder / 默认标签，**不是文本节点**）——
      **元素框本身就是它的真值**，落点取它的中心（**这不是回落到矩形中心**）。

    ⚠️ **判不了 ≠ 落点落矩形中心**：拿不到就是拿不到，
    上层据此**判不了**（不静默回落到元素矩形中心）。
    """

    rect: Rect | None = None
    reason: str = ""
    element_box: bool = False

    @property
    def ok(self) -> bool:
        return self.rect is not None


@dataclass(frozen=True)
class BrowserElement:
    """页面结构快照里的一个元素（读数）。

    * ``name`` —— **可见文本**（AX 节点的 accessible name）；
    * ``role`` —— **可访问性角色**（``button`` / ``textbox`` / ``link`` …）；
    * ``rect`` —— 物理表面空间的矩形；
    * ``element_id`` —— **页面元素引用**。它**有时效**：页面变了之后
      上一次的引用不再可用（工单 M13 AC 3）—— 所以它只在**本次快照**里有效；
    * ``value`` —— 元素当前的值（编辑框正文……）。**敏感面**：只进脱敏后的
      模型侧文本，不进日志；
    * ``text`` —— **可见文本的实际区域**（文本度量这一层的产物，工单 M28）。
      落点取它，不取 ``rect`` 的中心。
    """

    name: str
    role: str = ""
    rect: Rect = (0, 0, 0, 0)
    element_id: str = ""
    value: str = ""
    enabled: bool = True
    window_handle: int | None = None
    text: TextMeasurement = field(default_factory=TextMeasurement)

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

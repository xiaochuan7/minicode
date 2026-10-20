"""浏览器通道的端口。

端口规定**形状**，注入点决定接哪一个实现（真机还是假实现）——
换了注入点，**端口的形状不许变**。

两个端口：

* :class:`PageStructurePort` —— **页面结构快照**（可访问性树）；
* :class:`BrowserInputPort` —— **浏览器自己的输入通道**
  （浏览器收不到键盘事件时的退路，工单 M13 AC 4）。

> ⚠️ 端口的形状与桌面通道**共用**同一套动作语义：
> 页面元素最终也变成 :class:`~minicode.action.ports.ControlCandidate`，
> 走同一个动作协议、同一个坐标换算入口（工单 M13 AC 2）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from .elements import BrowserElement
from .geometry import PageViewport

__all__ = [
    "PageSnapshot",
    "PageStructurePort",
    "BrowserInputPort",
]


@dataclass(frozen=True)
class PageSnapshot:
    """一次**页面结构快照**。

    * ``elements`` —— 快照里的元素（矩形已是**物理表面空间**）；
    * ``viewport`` —— 视口在物理表面空间的位置与比例；
    * ``generation`` —— 快照代号。**元素引用有时效**：换一次快照，
      上一次快照里的元素引用就不再可用（工单 M13 AC 3）；
    * ``focused_value`` / ``focused_element_id`` —— 当前聚焦元素的取值与引用
      （用来判断「浏览器收没收到键盘事件」）。
    """

    elements: tuple[BrowserElement, ...] = ()
    viewport: PageViewport = field(default_factory=PageViewport)
    generation: int = 0
    focused_value: str = ""
    focused_element_id: str = ""


@runtime_checkable
class PageStructurePort(Protocol):
    """页面结构快照。"""

    def snapshot(self) -> PageSnapshot:
        """取一次页面结构快照（可访问性树）。

        拿不到就如实返回**空快照**（元素为空）—— 由上层判成覆盖失败，
        不许拿上一份快照顶替。
        """
        ...


@runtime_checkable
class BrowserInputPort(Protocol):
    """**浏览器自己的输入通道**。

    浏览器收不到桌面注入的键盘事件时，退到这条通道
    （Chromium/Electron 可能注入返回成功却完全收不到按键事件）。
    """

    def insert_text(self, text: str) -> bool:
        """把一段文字送进页面（浏览器自己的输入路径）。

        返回**有没有被接受** —— 不许假装成功。
        """
        ...

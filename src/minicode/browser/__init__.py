"""**浏览器通道**（工单 M13）：页面结构定位与操作。

`CONTEXT.md` 的词条：**浏览器通道 = 通过页面结构与浏览器自身的输入通道
操作网页的通路**。

本包的结构：

* :mod:`minicode.browser.geometry` —— **页面坐标 → 物理表面空间**的换算（只有一处）；
* :mod:`minicode.browser.elements` —— 页面元素 → **控件候选**（两条通道共用的那一种）；
* :mod:`minicode.browser.textmetrics` —— **文本度量**这一层（可见文本的实际区域；
  **浏览器通道独有**，工单 M28）；
* :mod:`minicode.browser.ports` —— 页面结构快照 / 浏览器自己的输入通道；
* :mod:`minicode.browser.channel` —— 通道本体（定位 → 重取快照再定位 → 落点）；
* :mod:`minicode.browser.capability` —— 能力表（按「应用 + 版本」，按实测填）；
* :mod:`minicode.browser.typing` —— 文本输入（键盘事件退化可退 + 剪贴板不污染）；
* :mod:`minicode.browser.cdp` —— 真机适配器（走 CDP；Playwright 是**可选**依赖）。
"""

from __future__ import annotations

from .capability import (
    BrowserCapabilityEntry,
    BrowserCapabilityTable,
    PageAssessment,
    PageProbePort,
)
from .channel import CHANNEL_NAME, QUERY_SCOPE_PAGE_STRUCTURE, BrowserChannel
from .elements import BrowserElement, TextMeasurement, element_identity, to_candidate
from .geometry import PageViewport, css_rect_to_physical, viewport_from_window
from .ports import BrowserInputPort, PageSnapshot, PageStructurePort
from .textmetrics import measure_visible_text
from .typing import BrowserTyping

__all__ = [
    "BrowserChannel",
    "BrowserCapabilityEntry",
    "BrowserCapabilityTable",
    "BrowserElement",
    "BrowserInputPort",
    "BrowserTyping",
    "CHANNEL_NAME",
    "PageAssessment",
    "PageProbePort",
    "PageSnapshot",
    "PageStructurePort",
    "PageViewport",
    "QUERY_SCOPE_PAGE_STRUCTURE",
    "TextMeasurement",
    "css_rect_to_physical",
    "element_identity",
    "measure_visible_text",
    "to_candidate",
    "viewport_from_window",
]

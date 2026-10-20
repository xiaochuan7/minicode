"""浏览器通道的**能力表**（工单 M13 AC 6）。

能力表按「**应用 + 版本**」记录**这条通道**的可用性，三态共用
:class:`minicode.capability.Availability`：

* **完全可用**（USABLE）—— 页面的结构快照里，交互元素**既有角色又有可见文本**，
  且没有同名撞车；
* **部分覆盖**（SPARSE）—— 结构快照拿得到，但**覆盖不全**：
  有可交互元素**没有可见文本**（按文本定位不到）、或可见文本**撞车**；
  **扁平 DOM 的典型形态就是这一档**；
* **不可用**（UNAVAILABLE）—— 结构快照拿不到，或里面根本没有可按
  可见文本定位的元素。

⚠️ 键里有版本，所以「Chromium 一律失效」这类**类别级结论**写不进去
（架构决策备忘 §2.6 C-08：不得出现「Chromium 一律失效」或
「UWP 一律可用」的类别级结论）。取值**按实测填**，不按类别猜。

表是**静态缓存**的：同一个「应用 + 版本」在同一次启动内只探测一次。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from minicode.action.ports import WindowAnchor
from minicode.capability import Availability

__all__ = [
    "PageAssessment",
    "PageProbePort",
    "BrowserCapabilityEntry",
    "BrowserCapabilityTable",
]


@dataclass(frozen=True)
class PageAssessment:
    """一次页面结构探测的读数。

    ⚠️ **不按元素个数判可用性**：判据是「结构里有没有**带角色与可见文本**的
    交互元素」以及「有没有覆盖不全的地方」。
    """

    #: 结构快照拿得到吗。
    has_snapshot: bool = False
    #: **有可见文本**的元素数（按文本定位得到的那些）。
    named_elements: int = 0
    #: 可交互但**没有可见文本**的元素数（按文本定位不到 = 覆盖不全）。
    unnamed_interactive: int = 0
    #: **没有可见文本**的元素数（无语义的容器多 → **扁平结构**的迹象）。
    unnamed_elements: int = 0
    #: 可见文本**撞车**的对数（同名的多个元素 → 定位要消歧）。
    duplicate_names: int = 0


@runtime_checkable
class PageProbePort(Protocol):
    """页面结构探测。"""

    def assess(self, anchor: WindowAnchor) -> PageAssessment:
        ...


@dataclass(frozen=True)
class BrowserCapabilityEntry:
    """能力表的一条记录。"""

    app: str
    version: str
    availability: Availability
    #: 为什么是这个结论（人读）。
    reason: str = ""
    #: 实测读数（跟着记录落盘，便于事后追溯这个结论是怎么来的）。
    named_elements: int = 0
    unnamed_interactive: int = 0
    unnamed_elements: int = 0
    duplicate_names: int = 0


class BrowserCapabilityTable:
    """按「应用 + 版本」缓存**浏览器通道**的可用性。"""

    def __init__(self, probe: PageProbePort):
        self._probe = probe
        self._entries: dict[tuple[str, str], BrowserCapabilityEntry] = {}
        self._probes = 0

    @property
    def probe_count(self) -> int:
        """**表级**探测次数：同一个应用在同一次启动内只探测一次。"""
        return self._probes

    def entries(self) -> tuple[BrowserCapabilityEntry, ...]:
        """表里的全部记录。"""
        return tuple(self._entries.values())

    def availability(self, anchor: WindowAnchor) -> BrowserCapabilityEntry:
        """这个应用（+版本）的浏览器通道可用性。**首次调用才探测**，之后走缓存。"""
        key = (anchor.app, anchor.version)
        cached = self._entries.get(key)
        if cached is not None:
            return cached
        self._probes += 1
        entry = self._verdict(anchor, self._probe.assess(anchor))
        self._entries[key] = entry
        return entry

    # -- 内部 -------------------------------------------------------------

    def _verdict(self, anchor: WindowAnchor, assessment: PageAssessment) -> BrowserCapabilityEntry:
        """按**实测读数**判定三态。"""
        if not assessment.has_snapshot:
            return BrowserCapabilityEntry(
                app=anchor.app,
                version=anchor.version,
                availability=Availability.UNAVAILABLE,
                reason="页面结构快照拿不到",
            )
        if assessment.named_elements <= 0:
            return BrowserCapabilityEntry(
                app=anchor.app,
                version=anchor.version,
                availability=Availability.UNAVAILABLE,
                reason="页面结构里没有既有角色又有可见文本的元素（按文本定位不到）",
                named_elements=assessment.named_elements,
            )
        missing: list[str] = []
        if assessment.unnamed_interactive:
            missing.append(f"{assessment.unnamed_interactive} 个可交互元素没有可见文本")
        if assessment.duplicate_names:
            missing.append(f"{assessment.duplicate_names} 处可见文本撞车")
        if assessment.unnamed_elements > assessment.named_elements:
            missing.append(
                f"没有可见文本的元素 {assessment.unnamed_elements} 个"
                f"多于有可见文本的 {assessment.named_elements} 个（扁平结构）"
            )
        availability = Availability.SPARSE if missing else Availability.USABLE
        reason = (
            "页面结构覆盖不全：" + "、".join(missing)
            if missing
            else "页面的元素都带可见文本，且没有撞车"
        )
        return BrowserCapabilityEntry(
            app=anchor.app,
            version=anchor.version,
            availability=availability,
            reason=reason,
            named_elements=assessment.named_elements,
            unnamed_interactive=assessment.unnamed_interactive,
            unnamed_elements=assessment.unnamed_elements,
            duplicate_names=assessment.duplicate_names,
        )

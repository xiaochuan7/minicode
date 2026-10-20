"""**能力表**的三态（跨通道共用）。

`CONTEXT.md` 的「能力表」词条说它是「按应用（乃至应用 + 版本）记录
**各通道**可用性的表」—— 所以三态是**共用**的，不按通道各写一份。

* **完全可用**（USABLE）—— 通道能覆盖得住这个应用的主要交互面；
* **部分覆盖**（SPARSE）—— 通道在，但**覆盖不全**（扁平结构、无稳定标识、同名撞车）；
* **不可用**（UNAVAILABLE）—— 通道根本产不出东西。

「部分覆盖」是**真实存在的中间态**，不可压成二元。
键里有版本，所以「Chromium 一律失效」「UWP 一律可用」这类
**类别级结论**根本写不进去（架构决策备忘 §2.6 C-08）。
"""

from __future__ import annotations

from enum import StrEnum

__all__ = ["Availability"]


class Availability(StrEnum):
    """能力表的三态（**不可压成二元**）。"""

    #: 完全可用。
    USABLE = "usable"
    #: 部分覆盖：通道在，但覆盖不全。
    SPARSE = "sparse"
    #: 不可用。
    UNAVAILABLE = "unavailable"

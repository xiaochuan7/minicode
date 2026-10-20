"""UIA 元素与**控件框的读法**。

控件框按 **4 个 int** 读（架构决策备忘 §2.6 **C-08**）：
``BoundingRectangle`` 是 ``RECT``（4 个 LONG）；按 ``double`` 读会得到
**1e-311 量级**的非规格化数，四舍五入后全为 0 —— 那种形态必须被判成
**无效坐标**，不能当作合法的左上角 ``(0, 0)`` 用（那是纯静默失败）。

本模块是**控件框读法的唯一出处**：通道里任何地方都不再自己解析坐标。
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, replace

# ⚠️ 下面四个名字的**实现**住在共用的地方（桌面通道与浏览器通道共用同一条，
# 工单 M13 AC 2：动作协议与坐标换算只有一套）：
#   * 矩形 → 落点、矩形合法性 → :mod:`minicode.action.geometry`
#   * 文本匹配规则       → :mod:`minicode.action.text`
# 这里再导出一次，是为了不打断既有引用（``from .elements import center_point``）。
from minicode.action.geometry import center_point, is_valid_rect, rect_center
from minicode.action.text import name_matches

__all__ = [
    "Rect",
    "UiaElement",
    "read_rect",
    "is_valid_rect",
    "is_same_place",
    "rect_center",
    "center_point",
    "name_matches",
    "element_identity",
    "make_element_id",
    "rename_elements",
]

#: 控件框：**物理表面空间**的 ``(left, top, right, bottom)``，四个 **int**。
Rect = tuple[int, int, int, int]


def read_rect(raw: Sequence[float]) -> Rect | None:
    """把控件框按 4 个 int 读出来；读不成合法控件框时返回 ``None``。

    判无效的三种形态：

    * **按 double 读出来的 1e-311 量级** —— 四舍五入后全 0（``None``）；
    * **四个值全 0**（``None``）；
    * **退化成零面积**（``right <= left`` 或 ``bottom <= top``，``None``）。

    返回 ``None`` 的意思是「这个候选没有坐标」，**不是**「它在原点」。
    """
    if raw is None or len(raw) != 4:
        return None
    values: list[int] = []
    for item in raw:
        try:
            number = float(item)
        except (TypeError, ValueError):
            return None
        if not math.isfinite(number):
            return None
        values.append(int(round(number)))
    left, top, right, bottom = values
    if not is_valid_rect((left, top, right, bottom)):
        return None
    return (left, top, right, bottom)


# ⚠️ 下面四个名字的**实现**住在共用的地方（桌面通道与浏览器通道共用同一条，
# 工单 M13 AC 2：动作协议与坐标换算只有一套）：
#   * 矩形 → 落点、矩形合法性 → :mod:`minicode.action.geometry`
#   * 文本匹配规则       → :mod:`minicode.action.text`
# 这里再导出一次，是为了不打断既有引用（``from .elements import center_point``）。

def element_identity(element: UiaElement) -> tuple[str, str, str, str]:
    """元素的**身份**（跨观测可比）。

    执行前重读树时靠它认出「还是那个控件」：类型 / 类名 / 自动化标识 / 名字。
    **不用元素 id** —— 元素 id 每次观测重新生成，跨观测不可比。
    """
    return (
        element.control_type,
        element.class_name,
        element.automation_id or "",
        element.name,
    )


def is_same_place(left: Rect, right: Rect) -> bool:
    """两个控件框是不是**同一个位置**（有重叠）。

    重定位时用它把「同名的那一个」和「别的同名控件」分开：
    两个同名按钮位置不同 → 位置不重叠 → 只认得出真正被选中的那一个。
    """
    width = min(left[2], right[2]) - max(left[0], right[0])
    height = min(left[3], right[3]) - max(left[1], right[1])
    return width > 0 and height > 0


def make_element_id(prefix: str, index: int) -> str:
    """元素 id 的**唯一**格式：``<观测记号>#<序号>``。

    每次观测换一个前缀 → 上一次观测的 id 在本次观测里**不解析**
    （工单 M3 AC 7：元素 id 每次观测重新生成）。
    """
    return f"{prefix}#{index}"


def rename_elements(elements: Sequence[UiaElement], prefix: str) -> tuple[UiaElement, ...]:
    """给一批元素**重新生成**元素 id（前缀 = 本次观测的记号）。"""
    return tuple(
        replace(element, element_id=make_element_id(prefix, index))
        for index, element in enumerate(elements)
    )


@dataclass(frozen=True)
class UiaElement:
    """控件树里的一个元素（读数）。

    坐标一律是**物理表面空间**的物理像素（前提是进程已 PMv2 感知）。
    """

    name: str
    control_type: str = ""
    class_name: str = ""
    automation_id: str | None = None
    rect: Rect = (0, 0, 0, 0)
    window_handle: int | None = None
    #: 元素 id —— **每次观测重新生成**（只在本次观测里有效）。
    element_id: str = ""
    #: 是不是密码框（``IsPassword=True``）。
    is_password: bool = False
    #: 控件的值（编辑框正文、密码框字符……）。
    value: str = ""
    #: 帮助文本。
    help_text: str = ""
    #: 支不支持 InvokePattern（Invoke 优先于坐标点击）。
    supports_invoke: bool = False
    #: 控件**当前可用吗**（``IsEnabled``）。
    #:
    #: 工单 M11 的「某个按钮变成不可用」要靠它判定（可访问性树查**状态**）。
    #: 读不出来时按**可用**算（读不到 ≠ 不可用）。
    enabled: bool = True
    #: 目标**窗口标题**（工单 M11 AC 7：动作日志要含窗口句柄**与标题**）。
    window_title: str = ""

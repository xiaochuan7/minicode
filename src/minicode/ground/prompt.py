"""两级 grounding 的提示词与回答解析（工单 M6）。

**提示词里不许出现量纲暗示**（不写「归一化」「0~1000」「分母」这类**答案单位**的说法），
否则测出来的就不是模型自己的量纲，而是我们告诉它的量纲 —— 而**换算链路的分母
只能来自标定结果**（架构决策备忘 §2.6 C-04）。标定探针的提示词
（:mod:`minicode.calib.prompt`）也是同一个规矩，因此生产链路与标定链路
**测的是同一个约定**：模型按自己惯用的坐标系作答。

三个提示词对应三条**不同的契约**：

* :func:`coarse_prompt` —— 整屏粗定位（L0）：模型报**整屏**位置；
* :func:`refine_prompt` —— crop 内精定位（L1）：模型报**这张图里**的位置，
  **不许提裁剪原点**（提了模型就会改报整屏坐标，那是另一套契约）；
* :func:`zoom_prompt` —— 放大动作：模型报**整屏**位置（回源重抓的物理画面）。

回答解析只认 JSON（``{"x": .., "y": ..}`` 或 ``[x, y]``）与一行 ``x: .., y: ..``；
取不出就**显式失败**（不猜、不按数值大小猜量纲）。
"""

from __future__ import annotations

import json
import re
from typing import Any

__all__ = [
    "GroundingParseError",
    "coarse_prompt",
    "refine_prompt",
    "second_opinion_prompt",
    "whole_screen_prompt",
    "zoom_prompt",
    "parse_point",
]


class GroundingParseError(Exception):
    """模型回答里取不出坐标。"""


def _answer_format() -> str:
    return '用 JSON 回答，格式：{"x": 数字, "y": 数字}'


def coarse_prompt(target_description: str) -> str:
    """整屏粗定位的提示词（工单 M6 的 L0）。

    **不许提量纲**（不写「0~1000」「归一化」），也不许提别的空间 ——
    模型报的是**整屏**位置，按它自己惯用的坐标系给。
    """
    return (
        "这是一张 Windows 桌面截图。\n"
        f"请给出界面上名为「{target_description}」的元素【中心】的位置。\n"
        f"{_answer_format()}\n"
        "坐标按你惯用的那种坐标系给出即可（回答里不要解释你的坐标范围）。"
    )


def refine_prompt(target_description: str) -> str:
    """crop 内精定位的提示词（工单 M6 的 L1）。

    模型报的是**这张 crop 图里**的位置（crop 内坐标）；**系统**负责加裁剪原点偏移
    映射回全屏 —— 所以提示词里**不许出现裁剪原点**（出现了模型就会改报整屏坐标，
    两套契约混用会让落点偏一整个裁剪窗口，而且是静默偏移）。

    「在这张图里」说的是**参照系**（哪张图），不是**答案单位**；
    答案单位由标定探针判定，不在这里写死。
    """
    return (
        "这是一张从 Windows 桌面截图里裁出的小区域（未缩放，1 像素 = 1 屏幕像素）。\n"
        f"请给出这张图里名为「{target_description}」的元素【中心】的位置。\n"
        f"{_answer_format()}\n"
        "坐标按你惯用的那种坐标系给出即可（回答里不要解释你的坐标范围）。"
    )


def whole_screen_prompt(target_description: str, *, retry_reason: str) -> str:
    """**整屏第二遍**的提示词（工单 M7：粗定位为空 / 越界时开的那一遍）。

    它与 :func:`coarse_prompt` **不是同一条请求**：

    * 说的是**整屏**（不围绕粗点裁剪 —— 没有粗点就没有窗口中心）；
    * 带上一次失败的原因（模型知道上一次没给出可用坐标），**不含任何量纲暗示**
      （不写「0~1000」「归一化」，也不写「上次的坐标超出范围」这种带单位的说法）——
      换算链路的分母只能来自标定（架构决策备忘 §2.6 C-04）。

    ⚠️ **不是逐字重发**：逐字重发同一条请求，确定性模型会给出同一个答案
    （工单 M7 AC 3：不得静默重试）。带上失败原因后它是**另一次问**。
    """
    return (
        "这是一张 Windows 桌面截图。\n"
        f"请给出界面上名为「{target_description}」的元素【中心】的位置。\n"
        f"（上一次没能给出可用的位置：{retry_reason}。请再看一遍这张图。）\n"
        f"{_answer_format()}\n"
        "坐标按你惯用的那种坐标系给出即可（回答里不要解释你的坐标范围）。"
    )


def second_opinion_prompt(target_description: str) -> str:
    """**第二次独立粗定位**的提示词（工单 M8）。

    **不是逐字重发**（逐字重发同一条请求，确定性模型会给出同一个答案 →
    「两次粗定位是否一致」永远判不出）。措辞与粗定位那条不同，
    但**不含任何量纲暗示**（量纲只由标定判定，架构备忘 §2.6 C-04）。
    """
    return (
        "这是一张桌面软件的界面截图。\n"
        f"请在图里找到符合这个描述的元素：{target_description}\n"
        f"{_answer_format()}\n"
        "坐标按你惯用的那种坐标系给出即可（回答里不要解释你的坐标范围）。"
    )


def zoom_prompt(
    target_description: str,
    *,
    origin_in_model_space: tuple[float, float],
) -> str:
    """放大动作的提示词（工单 M6）。

    放大是**回源重抓**后送模型看清楚，模型回答的仍是**整屏坐标** ——
    因此提示词要告诉模型这块区域在整屏的哪儿（``origin_in_model_space``
    是换算到**模型坐标系**后的区域左上角，与整屏坐标同一套口径）。

    ⚠️ 这里出现了一个坐标数字，**但它不是量纲暗示**：它是模型**自己那个坐标系**
    里的一个点（模型的整屏坐标就是这套），模型要靠它把区域位置与自己给的
    整屏坐标对上。提示词**不写**「0~1000」「归一化」这类答案单位 ——
    量纲只由标定判定。

    与 :func:`refine_prompt` 的契约区别：那里模型报 **crop 内坐标**、
    系统加偏移；这里模型**直接报整屏坐标**、系统不加任何偏移。
    """
    origin_x, origin_y = origin_in_model_space
    return (
        "这是从整屏回源重抓的一块区域（未缩放，1 像素 = 1 屏幕像素）。\n"
        f"这块区域的左上角在整屏坐标里是 ({origin_x:.1f}, {origin_y:.1f})。\n"
        f"请给出名为「{target_description}」的元素【中心】在**整屏**上的位置。\n"
        f"{_answer_format()}\n"
        "下面所有坐标都按你惯用的那种坐标系给出即可"
        "（回答里不要解释你的坐标范围）。"
    )


def _json_payload(text: str) -> Any:
    """从回答里取出 JSON（允许前后有话）。"""
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        return json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None


def _as_pair(value: Any) -> tuple[float, float] | None:
    """把 ``[x, y]`` / ``{"x": .., "y": ..}`` 读成一对数值。"""
    if isinstance(value, dict):
        if "x" in value and "y" in value:
            value = [value["x"], value["y"]]
        else:
            return None
    if isinstance(value, (list, tuple)) and len(value) == 2:
        try:
            return float(value[0]), float(value[1])
        except (TypeError, ValueError):
            return None
    return None


def parse_point(text: str) -> tuple[float, float]:
    """把模型回答读成 ``(x, y)``。

    解析不出来 → :class:`GroundingParseError`（**显式失败**，不猜）。
    """
    pair = _as_pair(_json_payload(text))
    if pair is not None:
        return pair
    match = re.search(
        r"x\s*[:：=]\s*(-?\d+(?:\.\d+)?)\s*[,，]\s*y\s*[:：=]\s*(-?\d+(?:\.\d+)?)",
        text,
        re.IGNORECASE,
    )
    if match is None:
        match = re.search(
            r"[\(（]\s*(-?\d+(?:\.\d+)?)\s*[,，]\s*(-?\d+(?:\.\d+)?)\s*[\)）]",
            text,
        )
    if match is not None:
        return float(match.group(1)), float(match.group(2))
    raise GroundingParseError(f"模型回答里取不出坐标：{text[:160]!r}")

"""两级 grounding 的提示词与回答解析（工单 M6 / B-1 / B-4）。

**提示词里不许出现量纲暗示**（不写「归一化」「0~1000」「分母」这类**答案单位**的说法），
否则测出来的就不是模型自己的量纲，而是我们告诉它的量纲 —— 而**换算链路的分母
只能来自标定结果**（架构决策备忘 §2.6 C-04）。标定探针的提示词
（:mod:`minicode.calib.prompt`）也是同一个规矩，因此生产链路与标定链路
**测的是同一个约定**：模型按自己惯用的坐标系作答。

⚠️ **但「尺寸声明」是允许且要求的**（A-2）：**声明这张图是 W×H**
（「这张图是 1280×800」），让「模型发的坐标对应哪一张图」不再是猜的。
声明的是**图本身的大小**，**不是答案单位** —— 不写「0~1000」「归一化」「分母」。
（标定探针那边是同一个写法，见 :func:`minicode.calib.prompt.image_prompt`。）

四个提示词对应四条**不同的契约**：

* :func:`coarse_prompt` —— 整屏粗定位（L0）：模型报**整屏**位置；
* :func:`refine_prompt` —— crop 内精定位（L1）：模型报**这张图里**的位置，
  **不许提裁剪原点**（提了模型就会改报整屏坐标，那是另一套契约）；
* :func:`whole_screen_prompt` —— 整屏第二遍（带上一次失败的原因）；
* :func:`numbered_candidates_prompt` —— **编号清单**（B-1）：模型**只答编号**，
  **不产坐标**；
* :func:`zoom_prompt` —— 放大动作：模型报**整屏**位置（回源重抓的物理画面）。

回答解析只认 JSON（``{"x": .., "y": ..}`` 或 ``[x, y]``）与一行 ``x: .., y: ..``；
取不出就**显式失败**（不猜、不按数值大小猜量纲）。

**编号的回答解析只有一份**：:func:`parse_chosen_numbers` 直接复用
:func:`minicode.uia.pointer.parse_chosen_numbers`（UIA 通道已经在用同一个读法）。
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from minicode.uia.pointer import parse_chosen_numbers
from minicode.verification.ocr import normalize_ocr_text

__all__ = [
    "CandidateLine",
    "GroundingParseError",
    "coarse_prompt",
    "normalize_target",
    "normalize_target_prompt",
    "numbered_candidates_prompt",
    "parse_chosen_numbers",
    "parse_point",
    "refine_prompt",
    "second_opinion_prompt",
    "whole_screen_prompt",
    "zoom_prompt",
]


class GroundingParseError(Exception):
    """模型回答里取不出坐标。"""


@dataclass(frozen=True)
class CandidateLine:
    """**编号清单**里的一项（位置是「送模型那张图」的像素）。

    ⚠️ 这里的框是**显示用**的（模型看到的那张图里的位置）；
    **落点不取它** —— 落点取候选框的**物理中心**（零换算，见
    :mod:`minicode.ground.candidates`）。
    """

    #: 编号（1 起）—— **模型只答这个编号**。
    number: int
    #: 命中的文字（界面上的原文）。
    text: str
    #: 框 ``(left, top, right, bottom)``（送模型那张图的像素）。
    box: tuple[int, int, int, int]


def _answer_format() -> str:
    return '用 JSON 回答，格式：{"x": 数字, "y": 数字}'


def _size_line(image_size: tuple[int, int]) -> str:
    """**尺寸声明**（A-2）：这张图是 W×H。"""
    return f"这张图是 {int(image_size[0])}×{int(image_size[1])}。"


def coarse_prompt(target_description: str, image_size: tuple[int, int]) -> str:
    """整屏粗定位的提示词（工单 M6 的 L0 / A-2）。

    * ``image_size`` —— **送模型的那张整屏图**的尺寸（**必填**）。
      提示词里声明它，模型报的坐标就知道对应哪一张图。
    * **不许提量纲**（不写「0~1000」「归一化」），也不许提别的空间 ——
      模型报的是**整屏**位置。
    """
    return (
        "这是一张 Windows 桌面截图。\n"
        f"{_size_line(image_size)}\n"
        f"请给出界面上名为「{target_description}」的元素【中心】的位置。\n"
        f"{_answer_format()}\n"
        "请给这张图的像素坐标（直接给坐标即可，不用解释你的坐标范围）。"
    )


def refine_prompt(target_description: str, image_size: tuple[int, int]) -> str:
    """crop 内精定位的提示词（工单 M6 的 L1 / A-2）。

    模型报的是**这张 crop 图里**的位置（crop 内坐标）；**系统**负责加裁剪原点偏移
    映射回全屏 —— 所以提示词里**不许出现裁剪原点**（出现了模型就会改报整屏坐标，
    两套契约混用会让落点偏一整个裁剪窗口，而且是静默偏移）。

    「在这张图里」说的是**参照系**（哪张图），不是**答案单位**；
    答案单位由标定探针判定，不在这里写死。
    """
    return (
        "这是一张从 Windows 桌面截图里裁出的小区域（未缩放，1 像素 = 1 屏幕像素）。\n"
        f"{_size_line(image_size)}\n"
        f"请给出这张图里名为「{target_description}」的元素【中心】的位置。\n"
        f"{_answer_format()}\n"
        "请给这张图的像素坐标（直接给坐标即可，不用解释你的坐标范围）。"
    )


def whole_screen_prompt(
    target_description: str,
    image_size: tuple[int, int],
    *,
    retry_reason: str,
) -> str:
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
        f"{_size_line(image_size)}\n"
        f"请给出界面上名为「{target_description}」的元素【中心】的位置。\n"
        f"（上一次没能给出可用的位置：{retry_reason}。请再看一遍这张图。）\n"
        f"{_answer_format()}\n"
        "请给这张图的像素坐标（直接给坐标即可，不用解释你的坐标范围）。"
    )


def second_opinion_prompt(
    target_description: str, image_size: tuple[int, int] | None = None
) -> str:
    """**第二次独立粗定位**的提示词（工单 M8）。

    **不是逐字重发**（逐字重发同一条请求，确定性模型会给出同一个答案 →
    「两次粗定位是否一致」永远判不出）。措辞与粗定位那条不同，
    但**不含任何量纲暗示**（量纲只由标定判定，架构备忘 §2.6 C-04）。

    ⚠️ **问的必须是同一个量**（与 :func:`coarse_prompt` 一致）：
    第一次粗定位问的是「**这张图的像素坐标**」，这一条也必须问同一个量 ——
    否则两次的答案**没法比**（一个像素、一个「模型惯用的坐标系」），
    「两次粗定位是否一致」这条门控条件就成了**空判**。
    **尺寸声明也给上**（``image_size`` 给了就写「这张图是 W×H」）：
    模型答的像素必须是**它看到的那张图**的像素。
    """
    lines = [
        "这是一张桌面软件的界面截图。",
        f"请在图里找到符合这个描述的元素：{target_description}",
    ]
    if image_size is not None:
        lines.append(f"{_size_line(image_size)}下面所有位置都是这张图的像素坐标。")
    lines.append(_answer_format())
    lines.append("请给这张图的像素坐标（直接给坐标即可，不用解释你的坐标范围）。")
    return "\n".join(lines)


def numbered_candidates_prompt(
    description: str,
    candidates: Sequence[CandidateLine],
    *,
    image_size: tuple[int, int] | None = None,
) -> str:
    """**编号清单**的提示词（工单 B-1）：模型**只答编号**，不产坐标。

    这与 UIA 通道的消歧是**同一个范式**（模型只负责「选」，不负责「瞄」）：
    清单列出候选，模型答一个编号，**落点由候选框的物理中心决定** ——
    模型的坐标误差从此不进落点。

    * ``candidates`` —— 清单（编号 1 起）。每项带文字与它在画面上的位置；
    * ``image_size`` —— 送模型的那张图的尺寸（给了就声明它，模型才知道
      那些位置对应哪一张图）。
    """
    lines = [
        "下面是一份界面候选清单。清单里每一行是一个候选项，按行号从 1 开始编号。",
        "附上的图片是当前屏幕画面；清单里每项后面的矩形是它在画面上的位置。",
    ]
    if image_size is not None:
        lines.append(
            f"{_size_line(image_size)}"
            "下面每个矩形的位置都是这张图的像素坐标。"
        )
    lines.append(f"目标描述：{description}")
    lines.append(
        f"任务：从清单里选出与目标描述对应的那一项，"
        f"只回答它的行号（1~{len(candidates)} 之间的一个整数）。"
    )
    lines.append(
        "如果清单里没有任何一项与目标描述对应，答 0（表示清单里没有）。"
    )
    lines.append('只回答一个 JSON 对象，不要任何解释：{"chosen": <行号>}')
    for item in candidates:
        left, top, right, bottom = item.box
        lines.append(
            f"{item.number}. 「{item.text}」 位于 ({left}, {top}) - ({right}, {bottom})"
        )
    return "\n".join(lines)


def matched_on_screen(description: str, screen_names: Sequence[str]) -> bool:
    """描述**对得上**界面上的某个名字吗？

    * **同名** —— 描述就是界面上的名字；
    * 描述里**含有**界面上的名字（原名就一致）。

    这是 :func:`normalize_target` 的「匹配上了没有」那一条口径
    （**规则只有一份**，两处都用它）。
    """
    target = normalize_ocr_text(description)
    if len(target) < 2:
        return False
    for name in screen_names:
        normalized = normalize_ocr_text(name)
        if not normalized:
            continue
        if normalized == target:
            return True
        if len(normalized) >= 2 and normalized in target:
            return True
    return False


def normalize_target(
    description: str, screen_names: Sequence[str]
) -> tuple[str, bool]:
    """**目标描述规范化**（工单 B-4）：先对齐名字，再定位。

    背景（本机实测，工单 M14 §4.2/§4.6）：工单写「显示」，本机设置里的卡片
    实际叫「屏幕」→ 树里没有 → 覆盖失败 → 切视觉；而模型按「显示」点到副标题
    「显示器、亮度、夜间模式…」→ **落点偏 27~30 px**。
    **那不是坐标精度问题，是描述与界面不一致。**

    规则（**保守**，不发明同义词表）：

    1. **同名** —— 描述就是界面上的名字 → 不改；
    2. 描述里**已经含有**界面上的名字（原名就一致）→ 不改；
    3. **近名**（错字级：编辑距离 ≤ 1；名字长（≥5 字）时 ≤ 2），
       且**只有一个**最优匹配 → 换成界面上的原名；
    4. 找不到 / 分不清 → **如实说不改**。

    ⚠️ **红线④**：``screen_names`` 必须是**已脱敏**的名字
    （脱敏是调用方的活儿，见 :mod:`minicode.redact`）——
    本模块不脱敏，也不该收到未脱敏的名字。

    ⚠️ 规则 3 只认**错字级**的近名：「显示」→「屏幕」那种**同义不同名**
    改不了（那需要一张同义词表 —— 明确不发明）。**找不到就如实说不改。**

    返回 ``(规范化后的描述, 是否发生了替换)``。
    """
    target = normalize_ocr_text(description)
    names = [
        (name, normalize_ocr_text(name))
        for name in screen_names
        if normalize_ocr_text(name)
    ]
    if len(target) < 2 or not names:
        return (description, False)
    # ① 同名
    for _name, normalized in names:
        if normalized == target:
            return (description, False)
    # ② 描述里已经含有界面上的名字（原名就一致）
    for _name, normalized in names:
        if len(normalized) >= 2 and normalized in target:
            return (description, False)    # ③ 近名（错字级）：唯一的最优匹配 → 换成界面上的原名
    scored: list[tuple[int, str]] = []
    for name, normalized in names:
        limit = 1 if len(normalized) <= 4 else 2
        distance = _edit_distance(target, normalized)
        if distance <= limit:
            scored.append((distance, name))
    if not scored:
        return (description, False)
    scored.sort(key=lambda item: (item[0], len(item[1])))
    if len(scored) > 1 and scored[0][0] == scored[1][0]:
        # 分不清 → 不改（**不猜**）
        return (description, False)
    return (scored[0][1], True)


def normalize_target_prompt(description: str, names: Sequence[str]) -> str:
    """**目标描述规范化**的模型侧提示词（工单 B-4 的另一半）。

    B-4 原文：「名字来源 = OCR 词表 + **脱敏后**的 UIA 控件名；
    **模型侧给 2~3 个更具体的表述让它选**」。

    ⚠️ **不许发明同义词表** —— 这一步问的是模型「哪个更像」，
    不是我们硬编码「显示→屏幕」那种映射。
    ⚠️ 名字必须是**已脱敏**的（脱敏是调用方的活儿，见 :mod:`minicode.redact`）。
    """
    lines = [
        "下面是界面上真实存在的几个名字（已脱敏）。",
        f"用户说的目标描述是：{description}",
        "用户说的更可能指下面哪一个？只回答它的行号"
        f"（1~{len(names)} 之间的一个整数）。",
        '只回答一个 JSON 对象，不要任何解释：{"chosen": <行号>}',
    ]
    for index, name in enumerate(names, start=1):
        lines.append(f"{index}. {name}")
    return "\n".join(lines)


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


def _edit_distance(left: str, right: str) -> int:
    """两个字符串的编辑距离（Levenshtein）。"""
    if left == right:
        return 0
    if not left:
        return len(right)
    if not right:
        return len(left)
    previous = list(range(len(right) + 1))
    for index, left_char in enumerate(left, start=1):
        current = [index]
        for offset, right_char in enumerate(right, start=1):
            current.append(
                min(
                    current[offset - 1] + 1,
                    previous[offset] + 1,
                    previous[offset - 1] + (0 if left_char == right_char else 1),
                )
            )
        previous = current
    return previous[-1]


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

"""两级 grounding 的提示词与回答解析（工单 M6 / B-1 / B-4）。

**提示词里不许出现量纲暗示**（不写「归一化」「0~1000」「分母」这类**答案单位**的说法），
否则测出来的就不是模型自己的量纲，而是我们告诉它的量纲 —— 而**换算链路的分母
只能来自标定结果**。标定探针的提示词
（:mod:`minicode.calib.prompt`）也是同一个规矩，因此生产链路与标定链路
**测的是同一个约定**：模型按自己惯用的坐标系作答。

⚠️ **但「尺寸声明」是允许且要求的**（A-2）：**声明这张图是 W×H**
（「这张图是 1280×800」），让「模型发的坐标对应哪一张图」不再是猜的。
声明的是**图本身的大小**，**不是答案单位** —— 不写「0~1000」「归一化」「分母」。
（标定探针那边是同一个写法，见 :func:`minicode.calib.prompt.image_prompt`。）

四个提示词对应四条**不同的契约**：

* :func:`coarse_prompt` —— 整屏粗定位（L0）：模型报**整屏**位置，
  **出框**（``{"bbox": [...], "point": [...]}``；**落点取框中心**）；
* :func:`refine_prompt` —— crop 内精定位（L1）：模型报**这张图里**的位置，
  **不许提裁剪原点**（提了模型就会改报整屏坐标，那是另一套契约）；
* :func:`whole_screen_prompt` —— 整屏第二遍（带上一次失败的原因）；
* :func:`numbered_candidates_prompt` —— **编号清单**（B-1）：模型**只答编号**，
  **不产坐标**。

⚠️ **放大（zoom）的提示词不在本模块**：它的坐标契约已经改成
「图内坐标 + 物理原点」（M14 实测），提示词住在 :mod:`minicode.ground.zoom`
（:func:`minicode.ground.zoom.zoom_region_prompt`）——
**两套坐标契约不许并存**（ADR-0006：混用会静默偏移一整个窗口）。

回答解析只认 JSON（``{"x": .., "y": ..}`` 或 ``[x, y]``）与一行 ``x: .., y: ..``；
取不出就**显式失败**（不猜、不按数值大小猜量纲）。

**编号的回答解析只有一份**：:func:`parse_chosen_numbers` 直接复用
:func:`minicode.uia.pointer.parse_chosen_numbers`（UIA 通道已经在用同一个读法）。
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from minicode.ground.response_pool import (
    COARSE_PROMPT_VARIANTS,
    REFINE_PROMPT_VARIANTS,
)
from minicode.model.answer_text import (
    answer_line_body,
    answer_line_payload,
    is_truncated,
    json_array,
    json_payload,
)
from minicode.uia.pointer import parse_chosen_numbers
from minicode.verification.ocr import normalize_ocr_text

__all__ = [
    "CandidateLine",
    "CoarseTarget",
    "GroundingParseError",
    "aggregate_candidates_prompt",
    "answer_line_payload",
    "coarse_prompt",
    "is_truncated",
    "normalize_target",
    "normalize_target_prompt",
    "numbered_candidates_prompt",
    "parse_chosen_index",
    "parse_chosen_numbers",
    "parse_coarse_target",
    "parse_optional_point",
    "parse_point",
    "refine_prompt",
    "rescreen_prompt",
    "whole_screen_prompt",
]

#: **整屏再搜**这一级的标志 —— 提示词里带着这句。
#:
#: ⚠️ 它同时是**阶段标签**的判据（``eval/sspro.py::prompt_stage``）。
RESCREEN_MARKER = "上一个候选是错的"


class GroundingParseError(Exception):
    """模型回答里取不出坐标。"""


@dataclass(frozen=True)
class CoarseTarget:
    """粗定位「**出框**」的回答（**模型输入空间**的数值，原样带回，不换算）。

    * ``bbox`` —— 模型给的**外接矩形** ``(x1, y1, x2, y2)``；没有就是 ``None``；
    * ``point`` —— 模型自己报的**中心点**；没有就是 ``None``。
      ⚠️ **2026-10-22 起生产提示词不再问这个字段**（
      官方全部是「每对象一个几何」，``point`` 是死字段 —— 本机实测 18/18 条
      模型自报的 point 恰等于它自己的 bbox 中心，|Δ| ≤ 1 px，丢掉它是恒等变换）。
      **解析仍然认它**（旧产物/旧模型回答要能读）。
    * ``text`` —— 模型**自报**的「这个元素上可见的文字」。
      用来把「挑错元素」从**静默的错**变成**可检出的错**。
    * ``negative`` —— 模型说了「**这张图里没有这个元素**」（负出口）。
      此时 ``bbox``/``point`` 都是 ``None``，**不是解析失败**。

    ⚠️ **落点取框中心**（官方通用 API 协议的做法）：论文 ScreenSpot-Pro
    Table 3 题注逐字 "For models inferencing boxes, we consider the center
    point of the generated box as the prediction"；官方仓 ``models/gpt4x.py``
    的解析也是 ``click_point = [(bbox[0]+bbox[2])/2, (bbox[1]+bbox[3])/2]``。
    **框 → 点的换算只在通道里做一处**（:mod:`minicode.ground.channel`）。

    ⚠️ **框可以退化成点**：模型只给得出点时（``bbox`` 为 ``None``），
    那个点就是中心点 —— 落点不变，只是「目标占比」判不了。
    """

    bbox: tuple[float, float, float, float] | None = None
    point: tuple[float, float] | None = None
    #: 模型自报的「这个元素上可见的文字」（可以为空串）。
    text: str = ""
    #: 模型说「**这张图里没有这个元素**」（负出口）。
    negative: bool = False


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


def coarse_prompt(
    target_description: str, image_size: tuple[int, int], *, variant: int = 0
) -> str:
    """整屏粗定位的提示词（工单 M6 的 L0 / A-2）—— **出框**。

    * 问的是 **bounding box**（官方通用 API 协议的做法）。官方仓
      ``models/gpt4x.py`` 的措辞逐字："You are asked to find the bounding box
      of an UI element in the given screenshot corresponding to a given
      instruction."；官方解析取框中心，落点 = 框中心
      （论文 ScreenSpot-Pro Table 3 题注："For models inferencing boxes, we
      consider the center point of the generated box as the prediction"）。
    * ``image_size`` —— **送模型的那张整屏图**的尺寸（**必填**）。
      提示词里声明它，模型报的坐标就知道对应哪一张图。
    * **不许提量纲**（不写「0~1000」「归一化」「分母」），也不许提别的空间 ——
      模型报的是**整屏**位置。**尺寸声明照旧**（A-2）。
    * **2026-10-22**：**删掉 ``point`` 字段**（官方全部是
      「每对象一个几何」；本机实测 18/18 条模型自报的 point 恰等于它自己的
      bbox 中心，|Δ| ≤ 1 px → 删掉是恒等变换，而留着它会与 bbox 矛盾）、
      **加 ``text`` 字段**（模型自报选中元素上的可见文字）、**加负出口**
      （元素不在这张图里 → 答 ``{"bbox": null, "text": ""}``）。
    * **``variant``** —— **换一种措辞再问一次**。
      同提示词重发**拿不到独立样本**（本机实测：同图同提示词 10 次原文一致率
      只有 10%）→ 采样必须换措辞。**变体 0 与改造前逐字相同**。
    """
    lead = COARSE_PROMPT_VARIANTS[variant % len(COARSE_PROMPT_VARIANTS)]
    return (
        "这是一张 Windows 桌面截图。\n"
        f"{_size_line(image_size)}\n"
        f"{lead}\n"
        f"指令：{target_description}\n"
        '用 JSON 回答，格式：{"bbox": [左, 上, 右, 下], "text": "这个元素上可见的文字"}'
        "（坐标是这张图的像素）。\n"
        "bbox 是这个元素在图里的外接矩形的左上角与右下角。\n"
        "text 是这个元素上能看到的文字；这个元素没有文字就填空字符串。\n"
        '如果这张图里找不到这个元素，答 {"bbox": null, "text": ""}。\n'
        "不要输出分析。"
    )


def refine_prompt(
    target_description: str,
    image_size: tuple[int, int],
    *,
    upscale_factor: float = 1.0,
    variant: int = 0,
) -> str:
    """crop 内精定位的提示词（工单 M6 的 L1 / A-2）。

    模型报的是**这张 crop 图里**的位置（crop 内坐标）；**系统**负责加裁剪原点偏移
    映射回全屏 —— 所以提示词里**不许出现裁剪原点**（出现了模型就会改报整屏坐标，
    两套契约混用会让落点偏一整个裁剪窗口，而且是静默偏移）。

    「在这张图里」说的是**参照系**（哪张图），不是**答案单位**；
    答案单位由标定探针判定，不在这里写死。

    ⚠️ **2026-10-22 加了「没找到」逃生口**：
    元素不在这张裁剪图里 → 答 ``{"x": null, "y": null}`` → **干净回落到粗定位**，
    而不是接受一个假点（这是「假命中归零」的手段）。

    ⚠️ **2026-10-22 改了首句**：crop 放大之后，
    「**未缩放，1 像素 = 1 屏幕像素**」这句话**是假话** → 改成
    「**已放大约 N 倍，1 像素 = 1/N 屏幕像素**」。
    ⚠️ **首句的前缀不变**（「这是一张从 Windows 桌面截图里裁出的小区域」）——
    阶段标签（``eval/sspro.py::prompt_stage``）靠它判「这一条属于精定位那一级」。
    ⚠️ **坐标契约不变**：仍然是**图内坐标**，换算链由 ``CropRefiner`` 那一条处理。

    ⚠️ **``variant``** —— 换一种措辞再问一次；
    **变体 0 与改造前逐字相同**。
    """
    lead = REFINE_PROMPT_VARIANTS[variant % len(REFINE_PROMPT_VARIANTS)].format(
        desc=target_description
    )
    if upscale_factor > 1.0:
        first = (
            "这是一张从 Windows 桌面截图里裁出的小区域"
            f"（已放大约 {upscale_factor:.2f} 倍，"
            f"1 像素 = 1/{upscale_factor:.2f} 屏幕像素）。\n"
        )
    else:
        first = "这是一张从 Windows 桌面截图里裁出的小区域（未缩放，1 像素 = 1 屏幕像素）。\n"
    return (
        first
        + f"{_size_line(image_size)}\n"
        + f"{lead}\n"
        + f"{_answer_format()}\n"
        + '如果这张图里没有这个元素，答 {"x": null, "y": null}。\n'
        + "请给这张图的像素坐标（直接给坐标即可，不用解释你的坐标范围）。"
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
      换算链路的分母只能来自标定。

    ⚠️ **不是逐字重发**：逐字重发同一条请求，确定性模型会给出同一个答案
    （工单 M7 AC 3：不得静默重试）。带上失败原因后它是**另一次问**。

    ⚠️ **问的量与 :func:`coarse_prompt` 一样**（**框 + 自报文字**）：它是粗定位那一级的
    重试 —— 契约不同的话，两遍的结果就没法比。
    """
    return (
        "这是一张 Windows 桌面截图。\n"
        f"{_size_line(image_size)}\n"
        "请在图里找到与下面这个指令对应的界面元素，"
        "并给出它的外接矩形（bounding box）。\n"
        f"指令：{target_description}\n"
        f"（上一次没能给出可用的位置：{retry_reason}。请再看一遍这张图。）\n"
        '用 JSON 回答，格式：{"bbox": [左, 上, 右, 下], "text": "这个元素上可见的文字"}'
        "（坐标是这张图的像素）。\n"
        "bbox 是这个元素在图里的外接矩形的左上角与右下角。\n"
        "text 是这个元素上能看到的文字；这个元素没有文字就填空字符串。\n"
        '如果这张图里找不到这个元素，答 {"bbox": null, "text": ""}。\n'
        "不要输出分析。"
    )


def aggregate_candidates_prompt(
    target_description: str,
    image_size: tuple[int, int],
    *,
    count: int,
    upscale_factor: float = 1.0,
) -> str:
    """**标记图选择**的提示词 —— 模型**只答编号**。

    图上画了 ``count`` 个**粉红五角星**，每个星标出一个候选位置；
    模型回答「哪个星是目标」的**编号**（**答 0 = 清单里没有可信的**）。

    **依据**：RegionFocus —— "we visually mark the candidate action coordinates
    on the snapshot … this significantly reduces the model's workload"
    【二手·公开 https://arxiv.org/abs/2505.00684】。

    ⚠️ **答 0 是合法答案**（「不可信」）—— 那是**整屏再搜**的入口，
    **不是解析失败**。
    """
    if upscale_factor > 1.0:
        first = (
            "这是一张从 Windows 桌面截图里裁出的小区域"
            f"（已放大约 {upscale_factor:.2f} 倍，"
            f"1 像素 = 1/{upscale_factor:.2f} 屏幕像素）。\n"
        )
    else:
        first = "这是一张从 Windows 桌面截图里裁出的小区域（未缩放，1 像素 = 1 屏幕像素）。\n"
    return (
        first
        + f"{_size_line(image_size)}\n"
        + f"这张图上画了 {count} 个粉红色五角星，每个星标出一个候选位置。\n"
        + f"请判断哪一个星标的是「{target_description}」这个元素。\n"
        + f"只回答它的编号（1~{count} 之间的一个整数）。\n"
        + "如果这些星里没有一个是「"
        + target_description
        + "」，答 0（表示清单里没有可信的）。\n"
        + '只回答一个 JSON 对象，不要任何解释：{"chosen": <编号>}'
    )


def rescreen_prompt(
    target_description: str,
    image_size: tuple[int, int],
    *,
    wrong_text: str = "",
) -> str:
    """**整屏再搜**的提示词 —— **不是回落**。

    精定位的候选**不可信**时（标记图上模型答 0）→ **整屏再搜一遍**，
    提示词里带着「**上一个候选是错的**」+ **候选自报的文字**
    （模型自己说过那是什么 → 让它**避开**那一个）。

    ⚠️ **问的量与 :func:`coarse_prompt` 一样**（框 + 自报文字）→
    契约一致，两遍的结果能比。

    **依据**：SafeGround 自陈的失败模式「模型一致地错时低方差的错答案看起来很自信」
    【二手·公开】→ 我们的 20 条
    假命中正是「一致地错」→ **离散度拒答对它无效**，只能换问法。
    """
    hint = (
        f"（上一个候选是错的，请找一个**不同的**元素。"
        f"那个候选自报的文字是「{wrong_text}」—— 不要选它。）\n"
        if wrong_text
        else "（上一个候选是错的，请找一个**不同的**元素。）\n"
    )
    return (
        "这是一张 Windows 桌面截图。\n"
        f"{_size_line(image_size)}\n"
        "请在图里找到与下面这个指令对应的界面元素，"
        "并给出它的外接矩形（bounding box）。\n"
        f"指令：{target_description}\n"
        f"{hint}"
        '用 JSON 回答，格式：{"bbox": [左, 上, 右, 下], "text": "这个元素上可见的文字"}'
        "（坐标是这张图的像素）。\n"
        "bbox 是这个元素在图里的外接矩形的左上角与右下角。\n"
        "text 是这个元素上能看到的文字；这个元素没有文字就填空字符串。\n"
        '如果这张图里找不到这个元素，答 {"bbox": null, "text": ""}。\n'
        "不要输出分析。"
    )


def parse_chosen_index(text: str, *, count: int) -> int | None:
    """**标记图选择**的回答 → 编号（``0`` = 清单里没有可信的）。

    * 读法与编号分支同一套纪律（**答案行优先**、截断 = 0 分）；
    * 返回 ``None`` = **读不出来**（**不是**「答 0」）—— 调用方据此
      「不重搜、走最密簇」；
    * 越界（``> count``）→ ``None``（**不猜**）。
    """
    if is_truncated(text):
        return None
    payload = answer_line_payload(text)
    if payload is None:
        payload = json_payload(text)
    value: Any = None
    if isinstance(payload, dict):
        value = payload.get("chosen")
    elif isinstance(payload, (int, float)):
        value = payload
    if value is None:
        return None
    try:
        index = int(value)
    except (TypeError, ValueError):
        return None
    if index < 0 or index > count:
        return None
    return index


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
            return (description, False)
    # ③ 近名（错字级）：唯一的最优匹配 → 换成界面上的原名
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
    pair = _as_pair(json_payload(text))
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


def parse_optional_point(text: str) -> tuple[float, float] | None:
    """把模型回答读成 ``(x, y)``；**模型说「没找到」** → ``None``。

    「没找到」的形态（逃生口）：

    * ``{"x": null, "y": null}`` —— 元素不在这张图里 → ``None``；
    * 别的读不出来 → :class:`GroundingParseError`（**显式失败**，不猜）。

    ⚠️ **截断 = 0 分**：括号不配对的回答**不抢救** → **显式失败**
    （抛 :class:`GroundingParseError`）。

    ⚠️ **截断不是 ``None``**：``None`` 的语义是「**模型说这张图里没有那个
    元素**」（负出口）；截断是「**没读完**」—— 两者不是一回事，
    混起来就把「没答完」说成了「没有这个元素」。
    调用方据此**干净回落到粗定位**（不落一个抢救出来的假点）。
    """
    body = answer_line_body(text)
    segment = body if body is not None else text
    if is_truncated(segment):
        raise GroundingParseError(
            f"模型回答被截断（括号不配对），按 0 分处理：{text[:160]!r}"
        )
    payload = answer_line_payload(text)
    if payload is None:
        payload = json_payload(text)
    if isinstance(payload, dict) and payload.get("x") is None and payload.get("y") is None:
        return None
    pair = _as_pair(payload)
    if pair is not None:
        return pair
    raise GroundingParseError(f"模型回答里取不出坐标：{text[:160]!r}")


def _numbers(value: Any) -> list[float] | None:
    """一串数字（``[1, 2, 3]``）；不是数字列表就是 ``None``。"""
    if not isinstance(value, (list, tuple)) or not value:
        return None
    numbers: list[float] = []
    for item in value:
        if isinstance(item, bool) or not isinstance(item, (int, float)):
            return None
        numbers.append(float(item))
    return numbers


def _pair_of(value: Any) -> tuple[float, float] | None:
    """读成一对坐标 ``(x, y)``。"""
    numbers = _numbers(value)
    if numbers is None or len(numbers) != 2:
        return None
    return (numbers[0], numbers[1])


def _box_of(value: Any) -> tuple[float, float, float, float] | None:
    """读成一个外接矩形 ``(x1, y1, x2, y2)``（**自动摆正**左上/右下）。

    ⚠️ **允许退化**：四个数相同时它就是一个点（框 → 点的换算取它的中心）。
    """
    numbers = _numbers(value)
    if numbers is None or len(numbers) != 4:
        return None
    x1, y1, x2, y2 = numbers
    return (min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2))


def parse_coarse_target(text: str) -> CoarseTarget:
    """把粗定位的回答读成**框 + 自报文字**（**模型输入空间**的数值，原样带回）。

    认得的几种形状：

    * ``{"bbox": [x1, y1, x2, y2], "text": "…"}`` —— 框 + 自报文字（**要的就是这个**）；
    * ``{"bbox": [x1, y1, x2, y2]}`` —— 只有框（**落点取框中心**）；
    * ``{"bbox": [x, y, x, y]}`` —— **退化成点**的框（中心即该点）；
    * ``{"bbox": null, "text": ""}`` —— **负出口**（元素不在这张图里；
      **只认这一种形状**：``bbox`` 键存在且值就是 ``null``）；
    * ``{"point": [x, y]}`` / ``{"x": .., "y": ..}`` / ``[x, y]`` —— 只有点
      （``point`` 字段生产提示词已不再问，**但仍然认**）；
    * ``[[x1, y1, x2, y2]]`` —— 官方协议那种顶层数组。

    **解析纪律**（照官方通用 API 评测协议的读法，
    一手：https://github.com/modelscope/evalscope/blob/main/evalscope/benchmarks/screenspot_pro/screenspot_pro_adapter.py）：

    * **答案行优先** —— 有 ``Answer:`` / ``答案：`` 那一行就只读那一行；
    * **答案行不是 JSON → 回落扫全文** —— 那一行上没有可读的东西时，
      **扫整段回答**（不直接放弃）；
    * **截断 = 0 分** —— 括号不配对的回答**不抢救** → 显式失败；
    * **松散写法仅在答案行接受** —— ``x: .., y: ..`` 这种只在答案行上认。

    读不出来 → :class:`GroundingParseError`（**显式失败**，不猜、不按数值大小猜量纲）。
    """
    body = answer_line_body(text)
    segment = body if body is not None else text
    if is_truncated(segment):
        raise GroundingParseError(
            f"模型回答被截断（括号不配对），按 0 分处理：{text[:160]!r}"
        )
    payload = answer_line_payload(text)
    if payload is None:
        payload = json_payload(text)
    if isinstance(payload, dict):
        bbox = _box_of(payload.get("bbox"))
        point = _pair_of(payload.get("point"))
        if point is None:
            point = _pair_of([payload.get("x"), payload.get("y")])
        reported = payload.get("text")
        reported_text = reported if isinstance(reported, str) else ""
        if (
            bbox is None
            and point is None
            and "bbox" in payload
            and payload["bbox"] is None
        ):
            # **负出口**：``{"bbox": null, "text": ""}`` —— 元素不在这张图里。
            #
            # ⚠️ **只认「bbox 键存在且值就是 null」这一种**（2026-10-22 拍板）：
            # ``{"text": "x"}`` / ``{}`` 这种**格式不符**的回答**不是**负出口 →
            # 读不出就**显式失败**（抛 :class:`GroundingParseError`），
            # 不许把它说成「模型说没有这个元素」。
            return CoarseTarget(text=reported_text, negative=True)
        if bbox is not None or point is not None:
            return CoarseTarget(bbox=bbox, point=point, text=reported_text)
    if isinstance(payload, list):
        # 官方协议：``[[x1, y1, x2, y2]]``（外面多一层数组）。
        inner = payload[0] if len(payload) == 1 and isinstance(payload[0], list) else payload
        bbox = _box_of(inner)
        if bbox is not None:
            return CoarseTarget(bbox=bbox)
        point = _pair_of(inner)
        if point is not None:
            return CoarseTarget(point=point)
    # 顶层的方括号数组（前面可能还有一句话）。
    # ⚠️ **扫全文**（不是只扫答案行）：答案行**存在但不是 JSON** 时，
    # 那一行上没有可读的东西 → **回落扫全文**（官方协议那种
    # ``[[x1, y1, x2, y2]]`` 也在全文里）。
    # 「答案行优先」管的是「答案行**有 JSON** 时只读那一行」——
    # 答案行里没有 JSON 时，不该直接放弃。
    array = json_array(text)
    if array is not None:
        inner = array[0] if len(array) == 1 and isinstance(array[0], list) else array
        bbox = _box_of(inner)
        if bbox is not None:
            return CoarseTarget(bbox=bbox)
        point = _pair_of(inner)
        if point is not None:
            return CoarseTarget(point=point)
    # 老读法：``x: .., y: ..`` / ``(x, y)``（只有点）—— **仅在答案行上接受**。
    if body is not None:
        try:
            return CoarseTarget(point=parse_point(body))
        except GroundingParseError as exc:
            raise GroundingParseError(f"模型回答里取不出框或坐标：{text[:160]!r}") from exc
    raise GroundingParseError(f"模型回答里取不出框或坐标：{text[:160]!r}")


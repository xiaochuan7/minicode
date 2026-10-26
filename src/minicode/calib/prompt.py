"""标定探针的提示词与回答解析。

**提示词里不许出现量纲暗示**（不写「归一化」「0~1000」「分母」这类**答案单位**的说法），
否则测出来的就不是模型自己的量纲，而是我们告诉它的量纲。
（描述画面几何时用到的「像素」是**画面本身的度量**、不是对答案单位的暗示；
但它确实会让模型倾向用像素作答 —— 文本路径的固有污染。）

⚠️ **尺寸声明是允许的**（A-2 明确要求）：**声明这张图是 W×H**，
让「模型发的坐标对应哪一张图」这件事不再是猜的。声明的是**图本身的大小**，
**不是答案单位**（不许写「0~1000」「归一化」「分母」）。

两条调用路径各有一份提示词（工单 M5 AC 9）：

* **图像路径**：把探针图发给模型，问四个方块中心的坐标；
* **文本路径**：不发明图，只用文字描述探针布局（屏幕尺寸 + 四个方块离各条边的距离），
  问同样的问题 —— 测的是模型在**纯文本**上下文里的坐标习惯。

**每轮提示词必须不同**（A-1）：逐字重发同一条请求在确定性模型下会拿到**同一答案**
（``ground/channel.py`` 的 ``second_opinion`` 注释记过这个坑），
所以 K 轮独立问靠**轮换报告顺序** + **轮换措辞**实现。
:func:`parse_points` **按名字取值**，与回答里的顺序无关 → 轮换顺序是安全的。

回答解析接受两种形态：**JSON**（首选）与**逐行写法**（``tl: (120, 120)``）；
两种形态都按**目标名字**取值，取不到就**显式失败**，不猜、不按数值大小猜量纲。

**与生产链路的同步**（「改 grounding 提示词必须同步改标定探针」，
否则标定测的就不是生产那条约定，**分母来源当场失效**）：

* **负出口**同款：生产链路的精定位提示词有「没找到 → ``{"x": null, "y": null}``」，
  本探针的提示词也写明「看不到就把值写成 null」；
* **解析纪律**同款：**答案行优先** / **截断 = 0 分** —— 这两条的实现**只有一份**
  （:mod:`minicode.model.answer_text`，生产链路与标定链路都从它 import）；
  **散写法**这边**保留兜底**（生产链路那条「散写法仅在答案行接受」是**纪律**，
  标定这边**丢掉一条标定比丢一条评测样本更贵** —— 标定定的是**分母**）。
  见 :func:`parse_points`。

⚠️ **口径说明（本轮已知的一处缺口）**：本探针问的是**方块中心**（一个点），
对应生产链路的**精定位**契约；生产链路的**粗定位**那一级现在问的是
**外接矩形**（落点取框中心）。两者的**坐标契约**（JSON + 本图像素 + 不给量纲暗示）
一致，**标度**也一致（同一个模型、同一条通道）→ **分母仍然有效**；
但「框 → 框中心」这条换算**没有**被本探针直接度量 —— 留给下一轮。
"""

from __future__ import annotations

import re
from itertools import permutations
from typing import Any

from minicode.model.answer_text import answer_line_body, is_truncated, json_payload

from .probe import ProbeSpec
from .tolerance import CALIBRATION_ROUNDS

__all__ = [
    "CalibrationParseError",
    "image_prompt",
    "text_prompt",
    "parse_points",
]


class CalibrationParseError(Exception):
    """模型回答里取不出四个目标的坐标。"""


#: 四个目标的**报告顺序**排列表（4! = 24 种，字典序）。
#:
#: 按轮次 / 会话号取用，保证每轮提示词不同（逐字重发会拿到同一答案）。
_ORDERINGS: tuple[tuple[int, ...], ...] = tuple(permutations(range(4)))

#: 请求句的**措辞变体**（轮换用）。语义相同、字面不同 → 提示词逐轮不同。
_REQUEST_TEMPLATES: tuple[str, ...] = (
    "请按 {order} 的顺序给出这四个方块中心的坐标。",
    "请按 {order} 的次序依次给出这四个方块中心的坐标。",
    "请依照 {order} 的先后顺序给出这四个方块中心的坐标。",
)


def _report_order(spec: ProbeSpec, round_index: int, session: int) -> tuple[str, ...]:
    """本轮的**报告顺序**（目标名字的元组）。

    * 轮次之间：顺序不同（轮换）→ 提示词不同 → 每轮是**另一次问**；
    * 会话之间：起点不同（``session`` 偏移）→ 不同会话的提示词不同 →
      6 次独立标定会话**相互独立**（不是逐字重发同一份请求）。
    """
    key = session * CALIBRATION_ROUNDS + round_index
    if len(spec.targets) != len(_ORDERINGS[0]):
        # 目标数不是 4（排列表是按 4 个目标建的）→ 不轮换，顺序照旧。
        return tuple(target.name for target in spec.targets)
    ordering = _ORDERINGS[key % len(_ORDERINGS)]
    return tuple(spec.targets[index].name for index in ordering)


def _request_sentence(order: tuple[str, ...], round_index: int) -> str:
    """本轮的请求句（措辞随轮次轮换）。"""
    template = _REQUEST_TEMPLATES[round_index % len(_REQUEST_TEMPLATES)]
    return template.format(order="、".join(order))


def _answer_format(spec: ProbeSpec, order: tuple[str, ...] | None = None) -> str:
    names = ", ".join(
        f'"{name}": [x, y]'
        for name in (order or tuple(target.name for target in spec.targets))
    )
    return "用 JSON 回答，格式：" + '{"points": {' + names + "}}"


def image_prompt(
    spec: ProbeSpec,
    model_input_size: tuple[int, int],
    *,
    round_index: int = 0,
    session: int = 0,
) -> str:
    """图像路径的提示词（探针图 + 这段文字）。

    **声明这张图的尺寸**（A-2）：「这张图是 W×H」—— 让模型发的坐标
    对应哪一张图变成**已知比例**，而不是猜的。

    ``round_index`` / ``session`` 轮换**报告顺序与措辞**：
    每轮提示词因此不同 → K 轮是 K 次**独立**的问。
    """
    width, height = model_input_size
    order = _report_order(spec, round_index, session)
    return (
        f"这是一张标定图，这张图是 {width}×{height}。\n"
        "图中有 4 个实心深色方块，请给出每个方块【中心】的坐标。\n"
        "其中 tl = 左上角那个方块，tr = 右上角，bl = 左下角，br = 右下角。\n"
        f"{_answer_format(spec, order)}\n"
        f"{_request_sentence(order, round_index)}\n"
        '如果某个方块在这张图里看不到，把它的值写成 null。\n'
        "请给这张图的像素坐标（直接给坐标即可，不用解释你的坐标范围）。"
    )


def text_prompt(
    spec: ProbeSpec,
    *,
    round_index: int = 0,
    session: int = 0,
) -> str:
    """文本路径的提示词（**不发图**，只有文字描述）。"""
    order = _report_order(spec, round_index, session)
    lines = [
        f"下面是一张 {spec.width}×{spec.height} 屏幕的文字描述（不附图片），"
        f"图中有 4 个边长 {spec.targets[0].size} 像素的实心深色方块：",
    ]
    for target in spec.targets:
        left_gap = target.x - target.size / 2.0
        right_gap = spec.width - (target.x + target.size / 2.0)
        top_gap = target.y - target.size / 2.0
        bottom_gap = spec.height - (target.y + target.size / 2.0)
        gaps = []
        if left_gap <= right_gap:
            gaps.append(f"离左边 {left_gap:.0f} 像素")
        else:
            gaps.append(f"离右边 {right_gap:.0f} 像素")
        if top_gap <= bottom_gap:
            gaps.append(f"离上边 {top_gap:.0f} 像素")
        else:
            gaps.append(f"离下边 {bottom_gap:.0f} 像素")
        lines.append(f"- {target.name}：{'、'.join(gaps)}")
    lines.append("请给出这 4 个方块【中心】的坐标。")
    lines.append(_answer_format(spec, order))
    lines.append(_request_sentence(order, round_index))
    lines.append("坐标按你惯用的那种坐标系给出即可（回答里不要解释你的坐标范围）。")
    return "\n".join(lines)


def _as_pair(value: Any) -> tuple[float, float] | None:
    """把 ``[x, y]`` / ``{"x":.., "y":..}`` 读成一对数值。"""
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


def _named_points(payload: Any) -> dict[str, tuple[float, float]]:
    """从回答里取出「名字 → (x, y)」。"""
    points: dict[str, tuple[float, float]] = {}
    if isinstance(payload, dict):
        inner = payload.get("points", payload)
        if isinstance(inner, dict):
            for name, value in inner.items():
                pair = _as_pair(value)
                if pair is not None:
                    points[str(name).strip().lower()] = pair
        elif isinstance(inner, list):
            for item in inner:
                if not isinstance(item, dict):
                    continue
                name = item.get("id") or item.get("name") or item.get("label")
                pair = _as_pair(item)
                if name is not None and pair is not None:
                    points[str(name).strip().lower()] = pair
    return points


def _line_points(text: str, names: tuple[str, ...]) -> dict[str, tuple[float, float]]:
    """兜底：从「tl: (120.0, 120.0)」这种逐行写法里取。"""
    points: dict[str, tuple[float, float]] = {}
    for name in names:
        match = re.search(
            rf"{re.escape(name)}\s*[:：=]?\s*[（(]?\s*(-?\d+(?:\.\d+)?)\s*[,，]\s*(-?\d+(?:\.\d+)?)",
            text,
            re.IGNORECASE,
        )
        if match:
            points[name] = (float(match.group(1)), float(match.group(2)))
    return points


def parse_points(
    text: str, spec: ProbeSpec
) -> tuple[tuple[str, float, float], ...]:
    """把模型回答读成 ``((名字, x, y), ...)``，**按探针的目标顺序**给出。

    **解析纪律**（与生产链路的 :mod:`minicode.ground.prompt` 一同，
    「改提示词必须同步改标定探针」）：

    * **答案行优先** —— 有 ``Answer:`` / ``答案：`` 那一行就只读那一行；
    * **截断 = 0 分** —— 括号不配对的回答**不抢救** → 显式失败；
    * **散写法的兜底保留**（生产链路那条「散写法仅在答案行接受」是**纪律**；
      标定这边**丢掉一条标定比丢一条评测样本更贵** —— 标定定的是**分母**，
      丢一条分母就失效）。有答案行时**只认答案行**。

    解析不出来 → :class:`CalibrationParseError`（**显式失败**，不猜）。
    """
    names = tuple(target.name for target in spec.targets)
    body = answer_line_body(text)
    segment = body if body is not None else text
    if is_truncated(segment):
        raise CalibrationParseError(
            f"模型回答被截断（括号不配对），按 0 分处理：{text[:160]!r}"
        )
    points: dict[str, tuple[float, float]] = {}
    payload: Any = None
    for candidate in (segment, text):
        payload = json_payload(candidate)
        if payload is not None:
            break
    try:
        points = _named_points(payload)
    except CalibrationParseError:
        points = {}
    if len(points) < len(names):
        # **散写法的兜底**：标定探针**保留**它（生产链路那条「散写法仅在答案行接受」
        # 是**纪律**，标定这边**丢掉一条标定比丢一条评测样本更贵** ——
        # 标定定的是**分母**，丢一条分母就失效）。有答案行时**只认答案行**。
        fallback = _line_points(segment if body is not None else text, names)
        for name, pair in fallback.items():
            points.setdefault(name, pair)
    missing = [name for name in names if name not in points]
    if missing:
        raise CalibrationParseError(
            f"模型回答里缺少这些目标的坐标：{'、'.join(missing)}；回答原文：{text[:200]!r}"
        )
    return tuple((name, points[name][0], points[name][1]) for name in names)

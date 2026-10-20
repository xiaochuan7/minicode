"""标定探针的提示词与回答解析。

**提示词里不许出现量纲暗示**（不写「归一化」「0~1000」「分母」这类**答案单位**的说法），
否则测出来的就不是模型自己的量纲，而是我们告诉它的量纲。
（描述画面几何时用到的「像素」是**画面本身的度量**、不是对答案单位的暗示；
但它确实会让模型倾向用像素作答 —— 文本路径的固有污染，见 ``docs/reviews/M5.md`` §3。）

两条调用路径各有一份提示词（工单 M5 AC 9）：

* **图像路径**：把探针图发给模型，问四个方块中心的坐标；
* **文本路径**：不发明图，只用文字描述探针布局（屏幕尺寸 + 四个方块离各条边的距离），
  问同样的问题 —— 测的是模型在**纯文本**上下文里的坐标习惯。

回答解析接受两种形态：**JSON**（首选）与**逐行写法**（``tl: (120, 120)``）；
两种形态都按**目标名字**取值，取不到就**显式失败**，不猜、不按数值大小猜量纲。
"""

from __future__ import annotations

import json
import re
from typing import Any

from .probe import ProbeSpec

__all__ = [
    "CalibrationParseError",
    "image_prompt",
    "text_prompt",
    "parse_points",
]


class CalibrationParseError(Exception):
    """模型回答里取不出四个目标的坐标。"""


def _answer_format(spec: ProbeSpec) -> str:
    names = ", ".join(f'"{target.name}": [x, y]' for target in spec.targets)
    return (
        "用 JSON 回答，格式："
        '{"points": {' + names + "}}"
    )


def image_prompt(spec: ProbeSpec) -> str:
    """图像路径的提示词（探针图 + 这段文字）。"""
    return (
        "这是一张标定图。图中有 4 个实心深色方块，请给出每个方块【中心】的坐标。\n"
        "其中 tl = 左上角那个方块，tr = 右上角，bl = 左下角，br = 右下角。\n"
        f"{_answer_format(spec)}\n"
        "坐标按你惯用的那种坐标系给出即可（回答里不要解释你的坐标范围）。"
    )


def text_prompt(spec: ProbeSpec) -> str:
    """文本路径的提示词（**不发图**，只有文字描述）。"""
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
    lines.append(_answer_format(spec))
    lines.append("坐标按你惯用的那种坐标系给出即可（回答里不要解释你的坐标范围）。")
    return "\n".join(lines)


def _json_payload(text: str) -> Any:
    """从回答里取出 JSON（允许前后有话）。"""
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        raise CalibrationParseError(f"模型回答里没有 JSON 对象：{text[:160]!r}")
    try:
        return json.loads(text[start : end + 1])
    except json.JSONDecodeError as exc:
        raise CalibrationParseError(f"模型回答里的 JSON 解析失败：{exc}") from exc


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

    解析不出来 → :class:`CalibrationParseError`（**显式失败**，不猜）。
    """
    names = tuple(target.name for target in spec.targets)
    points: dict[str, tuple[float, float]] = {}
    try:
        points = _named_points(_json_payload(text))
    except CalibrationParseError:
        points = {}
    if len(points) < len(names):
        fallback = _line_points(text, names)
        for name, pair in fallback.items():
            points.setdefault(name, pair)
    missing = [name for name in names if name not in points]
    if missing:
        raise CalibrationParseError(
            f"模型回答里缺少这些目标的坐标：{'、'.join(missing)}；回答原文：{text[:200]!r}"
        )
    return tuple((name, points[name][0], points[name][1]) for name in names)

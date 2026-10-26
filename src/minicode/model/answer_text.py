"""**模型回答文本的读法**（答案行 / 截断 / 取 JSON）—— **唯一一份实现**。

**改 grounding 提示词必须同步改标定探针提示词**；但**解析纪律**
（答案行优先 / 截断 = 0 分）本来是**两份逐字相同**的实现 ——
手工同步两份必然漂 → 这里只留**一份**，两条链路都从它 import：

* 生产链路：:mod:`minicode.ground.prompt`；
* 标定链路：:mod:`minicode.calib.prompt`。

纪律的出处（官方通用 API 评测协议的读法，一手：
https://github.com/modelscope/evalscope/blob/main/evalscope/benchmarks/screenspot_pro/screenspot_pro_adapter.py）：

* **答案行优先** —— 有 ``Answer:`` / ``答案：`` 那一行就**只读那一行**
  （取**最后**一条答案行：官方协议读的是末行）；
* **截断 = 0 分** —— 括号不配对的回答**不抢救**（不许被"抢救"成一个坐标）；
* **读不出来就显式失败**（抛什么错由调用方决定 —— 本模块不抛）。

⚠️ **两边有意不同的一处**（**不是**漂移，是刻意的）：
「**散写法**（``x: .., y: ..``）仅在答案行接受」是**生产链路**的纪律；
标定链路**保留散写法的兜底** —— 标定定的是**分母**，
丢一条标定比丢一条评测样本更贵。
**本模块只管两条链路共用的那部分**，那条差异留在各自模块里。
"""

from __future__ import annotations

import json
import re
from typing import Any

__all__ = [
    "answer_line_body",
    "answer_line_payload",
    "is_truncated",
    "json_array",
    "json_payload",
]

#: **答案行**的行首标记（官方通用 API 评测协议的读法：末行 ``Answer: [...]``）。
#:
#: 一手：https://github.com/modelscope/evalscope/blob/main/evalscope/benchmarks/screenspot_pro/screenspot_pro_adapter.py
_ANSWER_LINE = re.compile(
    r"^\s*(?:answer|answer\.|final answer|答案)\s*[:：]?\s*(?P<body>.+)$",
    re.IGNORECASE | re.MULTILINE,
)


def answer_line_body(text: str) -> str | None:
    """回答里**最后一条答案行**的正文（没有答案行 → ``None``）。"""
    matches = list(_ANSWER_LINE.finditer(text or ""))
    if not matches:
        return None
    return matches[-1].group("body")


def json_payload(text: str) -> Any | None:
    """从回答里取出 JSON **对象**（允许前后有话）；取不出 → ``None``。"""
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        return json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None


def json_array(text: str) -> Any | None:
    """从回答里取出**顶层方括号**数组（官方协议那种 ``[[x1,y1,x2,y2]]``）。"""
    start = text.find("[")
    end = text.rfind("]")
    if start < 0 or end <= start:
        return None
    try:
        return json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None


def answer_line_payload(text: str) -> Any | None:
    """**答案行优先**：回答里有一行 ``Answer: …`` / ``答案：…`` → 只读那一行。

    没有答案行 → 返回 ``None``（调用方读整段回答）。

    ⚠️ **取最后一条**答案行（官方协议读的是**末行**答案）。
    """
    body = answer_line_body(text)
    if body is None:
        return None
    payload = json_payload(body)
    if payload is not None:
        return payload
    return json_array(body)


def is_truncated(text: str) -> bool:
    """回答**被截断**了吗（括号不配对）？

    官方协议：**截断 = 0 分**（截断的回答不许被"抢救"成一个坐标）。
    """
    if not text:
        return True
    return text.count("{") > text.count("}") or text.count("[") > text.count("]")

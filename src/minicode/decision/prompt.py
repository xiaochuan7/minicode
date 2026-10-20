"""决策层的提示词：规划步与反思步各自要什么形状的输入。

三件事在这里定死（都是**纯函数**，便于核对）：

* 程序能用的接口长什么样（:data:`PROGRAM_API_DOC`）——
  它是**动作协议暴露的那几个**，别的都没有；
* 规划步要什么（目标 + 历史 + 接口说明 + 「只输出一个代码块」）；
* 反思步要什么（目标 + 历史 + 上一次失败的原因）。

**模型自称完成不算数**：提示词里明确写了「不要输出完成结论」，
harness 那一侧**不拿完成类措辞当停止判据**（只计数，见
:func:`minicode.decision.harness._completion_claims`）；终止只由 harness 侧的
停止条件承载（工单 M10 AC 3）。
"""

from __future__ import annotations

import re

__all__ = [
    "PROGRAM_API_DOC",
    "build_plan_prompt",
    "build_reflect_prompt",
    "extract_program",
]

#: 程序里可以用的接口（**只有动作协议暴露的动作** + 定位 + 标准输出）。
PROGRAM_API_DOC = """程序里可以用的接口（只有这些，没有别的名字）：
- click(target) -> dict：按目标描述定位并点击。返回 {"ok": bool, "point": [x, y] 或 None, "error": 错误码或 None}
- find(target) -> dict：只定位不动作，返回同上（观察，不改界面）
- type_text(text) -> dict：输入一段文字
- press_key(*keys) -> dict：按键，例如 press_key('enter')
- wait(ms) -> dict：等待若干毫秒
- screenshot() -> dict：截图，只给摘要（宽、高、sha256），不给图像数据
- ask_user(reason) -> dict：把任务交还给人（harness 会停止本轮）
另外有 print(...) 输出标准输出。没有 import、没有 open、没有 eval/exec。"""

#: 规划步的固定要求。
_PLAN_RULES = """要求：
1. 只输出一个 Python 代码块（```python ... ```），不要解释、不要写完成结论。
2. 程序里可以用 if / for 做判断与循环 —— 需要看结果再决定下一步时就这样写。
3. 每一步之后 print 一句你依据什么做了判断（标准输出会回灌给你）。
4. 任务是否完成由 harness 判定；你只要把能做的动作做掉。
5. 如果某一步做不了（返回的 ok 是 false），换一个做法再试；实在做不了就 ask_user。"""

_FENCE_RE = re.compile(r"```(?:python|py)?\s*\n(.*?)```", re.DOTALL | re.IGNORECASE)


def build_plan_prompt(goal: str, context: str) -> str:
    """规划步的提示词：给目标与历史，要一段动作程序。"""
    return (
        "你在一个 Windows 桌面 computer-use 系统里当决策层。\n"
        f"任务目标：{goal}\n\n"
        f"当前上下文：\n{context}\n\n"
        f"{PROGRAM_API_DOC}\n\n"
        f"{_PLAN_RULES}\n\n"
        "现在给出这一轮要执行的程序："
    )


def build_reflect_prompt(goal: str, context: str, failure: str) -> str:
    """反思步的提示词：上一轮的程序没跑成，说清原因，要下一步。"""
    return (
        "你在一个 Windows 桌面 computer-use 系统里当决策层。\n"
        f"任务目标：{goal}\n\n"
        f"当前上下文：\n{context}\n\n"
        f"上一轮的程序没有跑成，原因是：\n{failure}\n\n"
        f"{PROGRAM_API_DOC}\n\n"
        "先说清上一轮为什么失败（一句），再给出这一轮要执行的程序"
        "（只输出一个 Python 代码块，不要写完成结论）："
    )


def extract_program(text: str) -> str:
    """从模型回复里取出程序源码。

    * 有 ```` ```python ... ``` ```` 代码块 → 取块里的内容；
    * 没有代码块 → 整段回复都当程序（模型可能直接给裸代码）。

    取不到就是空串（调用方据此判「这不是程序」）。
    """
    if not text:
        return ""
    match = _FENCE_RE.search(text)
    if match is not None:
        return match.group(1).strip()
    return text.strip()

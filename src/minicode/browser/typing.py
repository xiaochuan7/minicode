"""**在浏览器里输入文本**（工单 M13 AC 4 / AC 5）。

流程：

1. 先走**桌面通道同一套**输入注入（同一个 `type` 动作、同一个动作执行器）——
   粘贴通道带剪贴板备份与恢复，**动作前后剪贴板一致**（AC 5）；
2. 输入后**重新感知**（取一次新的页面结构快照），看浏览器**收没收到**键盘事件；
3. **收不到** → 退到**浏览器自己的输入通道**（:class:`BrowserInputPort`），
   并把这次**退化记下来**（不静默、不整条任务失败）—— AC 4。

⚠️ **判不了 ≠ 不成立**：页面里没有聚焦元素时**判不了**这时不许瞎退——
如实记「判不了」，按原通道的结果走。
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import replace

from minicode.action.errors import ErrorCode
from minicode.action.executor import ActionExecutor
from minicode.action.protocol import Action, ActionKind, ActionResult

from .ports import BrowserInputPort, PageStructurePort

__all__ = [
    "BrowserTyping",
    "DEGRADED_NOTE",
    "UNDECIDABLE_NOTE",
    "RECEIVE_CHECK_ATTEMPTS",
    "RECEIVE_CHECK_INTERVAL_S",
]

#: 退化时写进结果的那句话（含「输入通道退化」，便于事后检索）。
DEGRADED_NOTE = "输入通道退化：浏览器收不到键盘事件 → 改走浏览器自己的输入通道"

#: 判不了时写进结果的那句话。
UNDECIDABLE_NOTE = "输入是否送达：判不了（页面里没有聚焦元素）"

#: 判断「浏览器收没收到」时**重新感知**的次数与间隔。
#:
#: ⚠️ **不能只查一次**：注入是异步到达的，页面上的值可能慢几十毫秒才更新 ——
#: 只查一次会把「还没到」误判成「收不到」，然后**重复输入一遍**。
RECEIVE_CHECK_ATTEMPTS = 5
RECEIVE_CHECK_INTERVAL_S = 0.1


class BrowserTyping:
    """浏览器里的文本输入。"""

    def __init__(
        self,
        *,
        executor: ActionExecutor,
        structure: PageStructurePort,
        browser_input: BrowserInputPort,
        sleeper: Callable[[float], None] = time.sleep,
        attempts: int = RECEIVE_CHECK_ATTEMPTS,
        interval_s: float = RECEIVE_CHECK_INTERVAL_S,
    ):
        self.executor = executor
        self.structure = structure
        self.browser_input = browser_input
        self._sleeper = sleeper
        self._attempts = int(attempts)
        self._interval_s = float(interval_s)

    def type_text(self, text: str) -> ActionResult:
        """把 ``text`` 输入到浏览器里。返回动作结果（退化会记在 ``notes`` 里）。"""
        action = Action(
            kind=ActionKind.TYPE,
            params={"text": text},
            intent=f"在网页里输入「{text}」",
        )
        result = self.executor.execute(action)
        if not result.ok:
            return result
        received, decided = self._browser_received(text)
        if not decided:
            # 判不了 ≠ 不成立：不瞎退，如实记一笔。
            return replace(result, notes=result.notes + (UNDECIDABLE_NOTE,))
        if received:
            return result
        # 退化：改走浏览器自己的输入通道。
        accepted = self.browser_input.insert_text(text)
        note = (
            f"{DEGRADED_NOTE}（{'成功' if accepted else '失败'}）"
        )
        if not accepted:
            return replace(
                result,
                ok=False,
                error_code=ErrorCode.EXECUTE_FAILED,
                error_message="浏览器自己的输入通道也没接受这段文字",
                notes=result.notes + (note,),
            )
        return replace(result, notes=result.notes + (note,))

    def _browser_received(self, text: str) -> tuple[bool, bool]:
        """浏览器**收没收到**这段键盘事件。

        返回 ``(收没收到, 判没判得了)``：页面里没有聚焦元素时**判不了**。

        ⚠️ **要重新感知若干次**：注入是异步到达的，页面上的值可能慢几十毫秒
        才更新 —— 只查一次会把「还没到」误判成「收不到」，然后**重复输入一遍**。
        """
        decided = False
        for attempt in range(self._attempts):
            if attempt:
                self._sleeper(self._interval_s)
            snapshot = self.structure.snapshot()
            if not snapshot.focused_element_id:
                # 没有聚焦元素 → **判不了**（不是「收不到」）。
                return False, False
            decided = True
            if text in (snapshot.focused_value or ""):
                return True, True
        return False, decided

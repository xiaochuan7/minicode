"""动作程序能看到的接口：**动作协议暴露的动作** + 定位。

模型写的程序调的就是这里（工单 M10 AC 6：程序只能调用动作协议暴露的动作）。

* 走**动作执行缝**的动作（点击）由缝负责定位与执行；
* 文本 / 按键 / 等待 / 截图走**动作执行器**（协议里那些不需要定位的动作）；
* ``find`` 只定位不动作（``ControlLocator`` 端口）；
* ``screenshot`` **只给摘要**（宽高 + 截图哈希）—— 与「截图只落哈希」同一条红线，
  程序拿不到图像数据。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from minicode.action.ports import (
    CapturedFrame,
    ControlLocator,
    LocateRequest,
    WindowAnchor,
)
from minicode.action.program import PROGRAM_API_NAMES
from minicode.action.protocol import Action, ActionKind
from minicode.action.seam import ActionExecutionSeam, SeamRequest
from minicode.reflection.gate import DangerGate
from minicode.verification.expect import Expectation

__all__ = ["ProtocolApi", "DANGEROUS_ACTION_ERROR"]

#: **危险动作被拦下**时的错误码（工单 M12 AC 8 / AC 9）。
#:
#: ⚠️ 它**不是**协议里的错误码 —— 动作压根没下发（连缝都没进），
#: 所以不进 :class:`~minicode.action.errors.ErrorCode`：
#: 那是**动作协议**的错误码集合，这里是**决策层**拦下动作时说的话。
DANGEROUS_ACTION_ERROR = "dangerous_action_requires_human"


class ProtocolApi:
    """动作程序的接口实现（记录每一次调用，供回灌与审计）。"""

    def __init__(
        self,
        *,
        seam: ActionExecutionSeam,
        locator: ControlLocator | None = None,
        anchor: WindowAnchor | None = None,
        frame_provider: Callable[[], CapturedFrame | None] | None = None,
        goal_expectation: Expectation | None = None,
        danger_gate: DangerGate | None = None,
    ):
        self._seam = seam
        self._locator = locator
        self._anchor = anchor or WindowAnchor(hwnd=0)
        self._frame_provider = frame_provider
        #: **目标级期望**（工单 M12）：用户对**整个目标**写的一句期望。
        #: 它跟着每一步走（缝去校验它），**不成立不算这一步失败**
        #: —— 成立则任务达成（由 :mod:`minicode.reflection.stop` 读出来）。
        self.goal_expectation = goal_expectation
        #: **危险动作的放行判据**（工单 M12 AC 8）。
        #: 给了它：命中危险词表的**动作不执行**，任务**停下来交给人**
        #: （审批与 dry-run 是 M16 的活 —— 本单只做「检测 + 停手 + 交接」）。
        self.danger_gate = danger_gate
        #: 程序每一次接口调用的记录。
        self.calls: list[dict[str, Any]] = []
        #: 程序请求交还给人了吗。
        self.asked_human = False
        self.asked_human_reason = ""
        #: **被危险动作判据拦下**了吗（停下、交人）。
        self.halted = False
        self.halt_reason = ""
        #: 最近一次截图拿到的帧（供上下文窗口留完整图）。
        self.last_frame: CapturedFrame | None = None
        #: 本轮的**决策耗时**（毫秒）与「还没记到动作上」的标记。
        #:
        #: 决策耗时是**轮级**的量（一次规划调用 → 一轮），
        #: 因此它**只记在这一轮的第一个动作上** —— 同一轮的后续动作记 0。
        #: 这样整任务的三段耗时合计 = 各段真实耗时之和（**不重复计数**）。
        #:
        #: ⚠️ 多次 :meth:`begin_decision` 是**累加**的：反思步的耗时也要落到
        #: 某个动作记录上（否则单步与整任务的两份数字对不上）。
        self._decision_ms = 0.0
        self._decision_pending = False

    # ------------------------------------------------------------------
    # 决策耗时（工单 M11：三段耗时必须齐全）
    # ------------------------------------------------------------------

    def begin_decision(self, decision_ms: float) -> None:
        """告诉接口面「决策花了这么久」。

        下一条动作记录会**带上**它（只带一次，见 :meth:`_take_decision_ms`）。
        多次调用是**累加**的（规划步 + 反思步）。
        """
        self._decision_ms += float(decision_ms)
        self._decision_pending = True

    def _take_decision_ms(self) -> float:
        """取本轮的决策耗时（**只给第一个动作**）。"""
        if not self._decision_pending:
            return 0.0
        self._decision_pending = False
        return self._decision_ms

    # ------------------------------------------------------------------
    # 暴露给程序的接口
    # ------------------------------------------------------------------

    def as_mapping(self) -> dict[str, Callable[..., Any]]:
        """程序作用域里能看到的名字（**必须与协议暴露的那份白名单一致**）。"""
        mapping: dict[str, Callable[..., Any]] = {
            "click": self.click,
            "find": self.find,
            "type_text": self.type_text,
            "press_key": self.press_key,
            "wait": self.wait,
            "screenshot": self.screenshot,
            "ask_user": self.ask_user,
        }
        if set(mapping) != set(PROGRAM_API_NAMES):
            # 白名单只有一份（在 action/program.py）；这里对不上就直接炸，
            # 不让「多一个能调的名字」悄悄进程序作用域。
            raise ValueError(
                "动作程序的接口面与白名单不一致："
                f"{sorted(mapping)} ≠ {sorted(PROGRAM_API_NAMES)}"
            )
        return mapping

    def click(self, target: str) -> dict[str, Any]:
        """按目标描述定位并点击（走动作执行缝）。"""
        decision_ms = self._take_decision_ms()
        # -- 危险动作判据（工单 M12 AC 8）--------------------------------
        # 命中危险词表 → **不执行**，停下来交给人。
        # ⚠️ 判据里**没有置信度**（绝对值不可当把握度，排序可用）。
        if self.danger_gate is not None:
            verdict = self.danger_gate.evaluate(target_description=str(target))
            if verdict.requires_human:
                self.halted = True
                self.halt_reason = verdict.reason
                record = {
                    "kind": "click",
                    "target": str(target),
                    "ok": False,
                    "point": None,
                    "error": DANGEROUS_ACTION_ERROR,
                }
                self.calls.append(record)
                return record
        result = self._seam.run(
            SeamRequest(
                target_description=str(target),
                action_kind=ActionKind.CLICK,
                decision_ms=decision_ms,
                goal_expectation=self.goal_expectation,
            )
        )
        record: dict[str, Any] = {
            "kind": "click",
            "target": str(target),
            "ok": result.ok,
            "point": list(result.final_point) if result.final_point else None,
            "error": (
                result.result.error_code.value if result.result.error_code else None
            ),
        }
        self.calls.append(record)
        return record

    def find(self, target: str) -> dict[str, Any]:
        """只定位，不动作。"""
        if self._locator is None:
            record = {
                "kind": "find",
                "target": str(target),
                "ok": False,
                "point": None,
                "error": "没有定位通道",
            }
            self.calls.append(record)
            return record
        located = self._locator.locate(
            LocateRequest(
                target_description=str(target),
                anchor=self._anchor,
                frame=self._frame(),
            )
        )
        record = {
            "kind": "find",
            "target": str(target),
            "ok": located.ok,
            "point": (
                list(located.landing_point) if located.landing_point else None
            ),
            "error": located.error_code.value if located.error_code else None,
        }
        self.calls.append(record)
        return record

    def type_text(self, text: str) -> dict[str, Any]:
        """输入文本。

        ⚠️ **文本与按键不走动作执行缝**：缝的契约里这两种动作是
        「请直接用动作执行器」（`seam.py` 的 ``SeamRequest`` 明确不收它们）。
        因此这两种动作**不产追踪记录** —— 动作日志的欠账记在
        ``docs/reviews/M10.md``（M11/M17 接手动作日志时补齐）。
        """
        result = self._seam.executor.execute(
            Action(ActionKind.TYPE, {"text": str(text)}, intent="输入文本")
        )
        return self._record_action("type_text", result, detail=str(text))

    def press_key(self, *keys: str) -> dict[str, Any]:
        """按键（``press_key('ctrl', 'c')``）。同 :meth:`type_text`：不走缝。"""
        combo = tuple(str(key) for key in keys)
        result = self._seam.executor.execute(
            Action(ActionKind.KEY, {"combo": combo}, intent="按键")
        )
        return self._record_action("press_key", result, detail=",".join(combo))

    def wait(self, ms: int) -> dict[str, Any]:
        """等待若干毫秒（**走动作执行缝**：等待是「不需要定位目标」的动作）。"""
        decision_ms = self._take_decision_ms()
        result = self._seam.run(
            SeamRequest(
                target_description="等待",
                action_kind=ActionKind.WAIT,
                params={"ms": int(ms)},
                decision_ms=decision_ms,
            )
        )
        return self._record_action("wait", result.result, detail=str(ms))

    def screenshot(self) -> dict[str, Any]:
        """截图：**只给摘要**，不给图像数据（**走动作执行缝**）。"""
        result = self._seam.run(
            SeamRequest(target_description="截图", action_kind=ActionKind.SCREENSHOT)
        )
        frame = result.result.captured
        if frame is not None:
            self.last_frame = frame
        record = self._record_action("screenshot", result.result)
        record["width"] = frame.width if frame is not None else None
        record["height"] = frame.height if frame is not None else None
        record["sha256"] = frame.digest() if frame is not None else None
        return record

    def capture_frame(self) -> CapturedFrame | None:
        """抓一帧新画面（harness 每轮结束时取它当**新观察**）。

        走的是 ``screenshot`` 动作 —— 与程序里调 ``screenshot()`` 同一个端口，
        同样经过动作执行缝（因此有追踪记录）。
        """
        result = self._seam.run(
            SeamRequest(target_description="截图", action_kind=ActionKind.SCREENSHOT)
        )
        frame = result.result.captured
        if frame is not None:
            self.last_frame = frame
            return frame
        return None

    def ask_user(self, reason: str) -> dict[str, Any]:
        """把任务交还给人（harness 侧的停止条件承载终止）。"""
        self.asked_human = True
        self.asked_human_reason = str(reason)
        record = {
            "kind": "ask_user",
            "target": str(reason),
            "ok": True,
            "point": None,
            "error": None,
        }
        self.calls.append(record)
        return record

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------

    def _record_action(
        self, kind: str, result: Any, *, detail: str = ""
    ) -> dict[str, Any]:
        record = {
            "kind": kind,
            "target": detail,
            "ok": bool(result.ok),
            "point": (
                list(result.landing_point) if result.landing_point else None
            ),
            "error": result.error_code.value if result.error_code else None,
        }
        self.calls.append(record)
        return record

    def _frame(self) -> CapturedFrame | None:
        if self._frame_provider is None:
            return None
        return self._frame_provider()

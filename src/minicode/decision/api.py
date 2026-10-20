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

from minicode.action.errors import ErrorCode
from minicode.action.ports import (
    CapturedFrame,
    ControlLocator,
    LocateRequest,
    WindowAnchor,
)
from minicode.action.program import PROGRAM_API_NAMES
from minicode.action.protocol import Action, ActionKind
from minicode.action.seam import ActionExecutionSeam, SeamRequest
from minicode.approval.gate import ApprovalGate
from minicode.approval.request import TargetPreview
from minicode.verification.expect import Expectation

__all__ = ["ProtocolApi", "DANGEROUS_ACTION_ERROR"]

#: **危险动作被拦下**时的错误码（工单 M16）。
#:
#: ⚠️ 它**就是动作协议的错误码**（``ErrorCode.APPROVAL_DENIED``）——
#: 动作被闸门拦下、**零动作下发**，上层按码分流。
DANGEROUS_ACTION_ERROR = ErrorCode.APPROVAL_DENIED.value


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
        approval_gate: ApprovalGate | None = None,
    ):
        self._seam = seam
        self._locator = locator
        self._anchor = anchor or WindowAnchor(hwnd=0)
        self._frame_provider = frame_provider
        #: **目标级期望**（工单 M12）：用户对**整个目标**写的一句期望。
        #: 它跟着每一步走（缝去校验它），**不成立不算这一步失败**
        #: —— 成立则任务达成（由 :mod:`minicode.reflection.stop` 读出来）。
        self.goal_expectation = goal_expectation
        #: **审批闸门**（工单 M16）。给了它：
        #: * **点击 / 截图**这类走缝的动作，闸门在**缝里**问（那里才有
        #:   dry-run 要的目标控件框、位置与截图哈希）；
        #: * **输入文本 / 按键**不走缝，闸门在**这里**问；
        #: 没批 → **零动作下发**，任务**停下来交给人**。
        self.approval_gate = approval_gate or ApprovalGate()
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
        result = self._seam.run(
            SeamRequest(
                target_description=str(target),
                action_kind=ActionKind.CLICK,
                decision_ms=decision_ms,
                goal_expectation=self.goal_expectation,
            )
        )
        # **审批未获批**（工单 M16）：动作**零下发**（缝在闸门那儿就停了），
        # 任务**停下来交给人**。
        if result.result.error_code == ErrorCode.APPROVAL_DENIED:
            self.halted = True
            self.halt_reason = result.result.error_message or DANGEROUS_ACTION_ERROR
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

        ⚠️ **审批在这里问**（工单 M16）：这两种动作**不经过缝**，
        闸门若只装在缝上就会漏掉它们（输入一条命令、发一条消息都在这条路上）。
        """
        approval = self._ask_approval(
            # ⚠️ **意图里不带输入的正文**（C-15：用户输入的正文不进日志，
            #: 审批请求会落进动作记录）。正文只交给**分类**（extra_text），
            #: 用来判「这是不是一条命令」—— 判完不进请求、不进记录。
            intent="输入文本",
            target_description="当前焦点控件",
            action_kind=ActionKind.TYPE.value,
            extra_text=str(text),
        )
        if approval.denied:
            self.halted = True
            self.halt_reason = approval.reason
            record = {
                "kind": "type_text",
                "target": str(text),
                "ok": False,
                "point": None,
                "error": DANGEROUS_ACTION_ERROR,
            }
            self.calls.append(record)
            return record
        result = self._seam.executor.execute(
            Action(ActionKind.TYPE, {"text": str(text)}, intent="输入文本")
        )
        return self._record_action("type_text", result, detail=str(text))

    def press_key(self, *keys: str) -> dict[str, Any]:
        """按键（``press_key('ctrl', 'c')``）。同 :meth:`type_text`：不走缝。"""
        combo = tuple(str(key) for key in keys)
        approval = self._ask_approval(
            intent=f"按键「{','.join(combo)}」",
            target_description="当前焦点控件",
            action_kind=ActionKind.KEY.value,
        )
        if approval.denied:
            self.halted = True
            self.halt_reason = approval.reason
            record = {
                "kind": "press_key",
                "target": ",".join(combo),
                "ok": False,
                "point": None,
                "error": DANGEROUS_ACTION_ERROR,
            }
            self.calls.append(record)
            return record
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

    def _ask_approval(
        self,
        *,
        intent: str,
        target_description: str,
        action_kind: str,
        extra_text: str = "",
    ):
        """问一次审批（工单 M16）。"""
        return self.approval_gate.check(
            intent=intent,
            target_description=target_description,
            action_kind=action_kind,
            extra_text=extra_text,
            preview=self._preview(),
        )

    def _preview(self) -> TargetPreview:
        """dry-run 的呈现：**这一轮看到的画面**的截图哈希。

        ⚠️ 输入文本 / 按键**没有定位目标**（那里根本没有控件）→
        拿不到控件框与落点；有的是**画面**（哈希）。
        """
        frame = self.last_frame
        return TargetPreview(
            screenshot_sha256=frame.digest() if frame is not None else "",
        )

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

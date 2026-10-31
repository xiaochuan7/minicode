"""动作程序能看到的接口：**动作协议暴露的动作** + 定位。

模型写的程序调的就是这里（工单 M10 AC 6：程序只能调用动作协议暴露的动作）。

* 走**动作执行缝**的动作（点击）由缝负责定位与执行；
* 文本 / 按键 / 等待 / 截图走**动作执行器**（协议里那些不需要定位的动作）；
* ``find`` 只定位不动作（``ControlLocator`` 端口）；
* ``screenshot`` **只给摘要**（宽高 + 截图哈希）—— 与「截图只落哈希」同一条红线，
  程序拿不到图像数据；
* ``observe`` **只看不动**：抓一帧当前画面，告诉程序「界面变没变」与
  「画面上有哪些字」（工单 D-1）—— 让模型自己决定要不要新画面；
* ``last_check`` 报**上一次动作的校验读数**（工单 D-2）——
  程序读得到「上一步到底成没成」，才能分支。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
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
from minicode.verification.diff import (
    OUTCOME_CHANGED,
    ChangeDetectParams,
    change_rate,
    frame_signature,
)
from minicode.verification.expect import Expectation
from minicode.verification.ocr import OcrPort

__all__ = ["ProtocolApi", "DANGEROUS_ACTION_ERROR"]

#: **危险动作被拦下**时的错误码（工单 M16）。
#:
#: ⚠️ 它**就是动作协议的错误码**（``ErrorCode.APPROVAL_DENIED``）——
#: 动作被闸门拦下、**零动作下发**，上层按码分流。
DANGEROUS_ACTION_ERROR = ErrorCode.APPROVAL_DENIED.value

#: **落点偏离量**：校验层**没有**这个读数（``VerificationResult`` 里没有，
#: ``action/ports.py`` 的 ``LocateResult`` 里也没有）。
#:
#: 所以 ``last_check()`` 如实返回 ``None``（判不了）—— **不编一个数**（工单 D-2）。
NO_LANDING_DEVIATION_REASON = (
    "校验层没有落点偏离量这个读数（VerificationResult / LocateResult 里都没有）"
)


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
        ocr_port: OcrPort | None = None,
        verification_reader: Callable[[], Mapping[str, Any] | None] | None = None,
        keyboard_input_allowed: bool = True,
    ):
        self._seam = seam
        self._locator = locator
        self._anchor = anchor or WindowAnchor(hwnd=0)
        self._frame_provider = frame_provider
        #: **键盘输入开关**（审查安全 #2）：非交互前端（HTTP / MCP 的动作程序）
        #: 一律 ``False`` —— 键盘注入在没有人值守的前端不给这条路。
        #: 审批门兜的是「危险词」，兜不住「普通文本打到危险焦点」；
        #: 交互前端（本地命令行）保持 ``True``，行为不变。
        self._keyboard_input_allowed = bool(keyboard_input_allowed)
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
        #: **OCR 端口**（工单 D-1）：``observe()`` 用它从画面上读文字。
        #: 没接 → ``observe()`` 如实说「读不到」（``text_available=False``，
        #: ``text`` 是空串）—— **不假装读到**。
        self._ocr_port = ocr_port
        #: **校验读数的旁路出口**（工单 D-2）。
        #:
        #: 校验结果**只走旁路**（追踪记录），**不进动作协议的返回类型** ——
        #: 那是仓库的一条边界纪律（动作协议保持窄返回类型）。
        #: 程序要读「上一步的校验读数」，就从**追踪记录**里读：
        #: 这里给一个**读取器**（返回最新一条追踪记录的 ``verification_result``）。
        #:
        #: ⚠️ 接口面在**每个动作之后立刻读一次并存下来** —— 不能等到
        #: ``last_check()`` 才读（那时最新的一条记录可能已经是别的观察了）。
        self._verification_reader = verification_reader
        #: **上一次观察**的那一帧（``observe()`` 拿它比「界面变没变」）。
        self._last_observed: CapturedFrame | None = None
        #: **上一次动作的校验读数**（``last_check()`` 读它）。
        self._last_verification: Mapping[str, Any] | None = None
        #: **上一次动作的错误码**。
        self._last_error_code: str | None = None
        #: 上一次动作为什么没有校验读数（人读）。
        self._last_check_reason = "还没有做过任何动作"
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
            "observe": self.observe,
            "last_check": self.last_check,
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
        #: 记下「上一次动作」的校验读数与错误码（``last_check()`` 读它）。
        #: 校验读数从**旁路出口**（追踪记录）读 —— 动作协议保持窄返回类型。
        self._note_last_action(self._read_verification(), result.result.error_code)
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
        因此这两种动作**不产追踪记录**（M11/M17 接手动作日志时补齐）。

        ⚠️ **审批在这里问**（工单 M16）：这两种动作**不经过缝**，
        闸门若只装在缝上就会漏掉它们（输入一条命令、发一条消息都在这条路上）。

        ⚠️ **非交互前端一律拒绝**（审查安全 #2）：``keyboard_input_allowed=False``
        （HTTP / MCP 的动作程序都是）时**连审批都不问**——键盘注入在
        没有人值守的前端没有合法路径（``injection_blocked``，零动作下发、任务停）。
        """
        if not self._keyboard_input_allowed:
            reason = (
                "非交互前端（HTTP / MCP 的动作程序）不接受键盘输入："
                "type_text/press_key 一律拒绝 —— 需要输入时改用交互前端或人工完成"
            )
            self.halted = True
            self.halt_reason = reason
            record = {
                "kind": "type_text",
                "target": str(text),
                "ok": False,
                "point": None,
                "error": ErrorCode.INJECTION_BLOCKED.value,
            }
            self.calls.append(record)
            return record
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
        """按键（``press_key('ctrl', 'c')``）。同 :meth:`type_text`：不走缝。

        ⚠️ 同 :meth:`type_text`：**非交互前端一律拒绝**（审查安全 #2）。
        """
        combo = tuple(str(key) for key in keys)
        if not self._keyboard_input_allowed:
            reason = (
                "非交互前端（HTTP / MCP 的动作程序）不接受键盘输入："
                "type_text/press_key 一律拒绝 —— 需要按键时改用交互前端或人工完成"
            )
            self.halted = True
            self.halt_reason = reason
            record = {
                "kind": "press_key",
                "target": ",".join(combo),
                "ok": False,
                "point": None,
                "error": ErrorCode.INJECTION_BLOCKED.value,
            }
            self.calls.append(record)
            return record
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
        return self._record_action(
            "wait",
            result.result,
            detail=str(ms),
            verification=self._read_verification(),
        )

    def screenshot(self) -> dict[str, Any]:
        """截图：**只给摘要**，不给图像数据（**走动作执行缝**）。"""
        result = self._seam.run(
            SeamRequest(target_description="截图", action_kind=ActionKind.SCREENSHOT)
        )
        frame = result.result.captured
        if frame is not None:
            self.last_frame = frame
        record = self._record_action(
            "screenshot",
            result.result,
            verification=self._read_verification(),
        )
        record["width"] = frame.width if frame is not None else None
        record["height"] = frame.height if frame is not None else None
        record["sha256"] = frame.digest() if frame is not None else None
        return record

    def observe(self) -> dict[str, Any]:
        """**观察**当前画面（**只观察、不注入输入**，与 ``zoom`` 动作同性质）。

        给模型的读数：

        * ``changed`` —— 相对**上一次观察**，画面变没变；
        * ``changed_available`` —— 判不判得出来（第一次观察 / 画面抓不到 →
          ``False``）；
        * ``text`` —— 画面里读到的文字；
        * ``text_available`` —— OCR **读到了吗**（没接 OCR / 后端读失败 →
          ``False``，此时 ``text`` 是空串 —— **不假装读到**）；
        * ``size`` —— 画面尺寸（物理像素）。

        ⚠️ 它**不注入任何输入**：动作协议里没有「观察」这个副作用，
        程序只是**看一眼**（工单 D-1）。
        """
        frame = self.capture_frame()
        if frame is None:
            record = {
                "kind": "observe",
                "target": "",
                "ok": False,
                "point": None,
                "error": "抓不到画面",
                "changed": False,
                "changed_available": False,
                "text": "",
                "text_available": False,
                "size": [0, 0],
            }
            self.calls.append(record)
            return record
        changed, changed_available = self._changed_since_last_observation(frame)
        text, text_available = self._read_text(frame)
        record = {
            "kind": "observe",
            "target": "",
            "ok": True,
            "point": None,
            "error": None,
            "changed": changed,
            "changed_available": changed_available,
            "text": text,
            "text_available": text_available,
            "size": [frame.width, frame.height],
        }
        self.calls.append(record)
        #: **这一次观察**成为「上一次观察」（下一次 ``observe()`` 拿它比）。
        self._last_observed = frame
        return record

    def last_check(self) -> dict[str, Any]:
        """**上一次动作的校验读数**（工单 D-2：程序要能看结果才能分支）。

        * ``available`` —— 有没有读数（**还没做过动作 / 这一步不产生校验 → False**，
          **不返回空字典冒充成功**）；
        * ``changed`` —— 界面有没有变（来自校验层那次差分的结论）；
        * ``expectation_held`` —— 期望断言成不成立（**没有期望时是 ``None``**，
          不是 ``False``）；
        * ``landing_deviation_px`` —— 落点偏离量（**校验层没有这个读数 → ``None``**，
          **不编一个数**）；
        * ``error_code`` —— 上一次动作的错误码。
        """
        verification = self._last_verification
        if verification is None:
            record = {
                "kind": "last_check",
                "target": "",
                "ok": False,
                "point": None,
                "error": None,
                "available": False,
                "changed": None,
                "expectation_held": None,
                "landing_deviation_px": None,
                "error_code": self._last_error_code,
                "reason": self._last_check_reason,
            }
            self.calls.append(record)
            return record
        change = verification.get("change") or {}
        record = {
            "kind": "last_check",
            "target": "",
            "ok": True,
            "point": None,
            "error": None,
            "available": True,
            #: 「界面变没变」取**差分结论**（``changed`` / ``no_change``）——
            #: 与校验层、卡死检测器**同一份判据**。
            "changed": (
                change.get("outcome") == OUTCOME_CHANGED if change else None
            ),
            #: 期望断言成不成立（**没有期望 → None**，不是 False）。
            "expectation_held": verification.get("satisfied"),
            "landing_deviation_px": None,
            "error_code": self._last_error_code,
            "reason": (
                "上一次动作的校验读数｜" + NO_LANDING_DEVIATION_REASON
            ),
        }
        self.calls.append(record)
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
        self,
        kind: str,
        result: Any,
        *,
        detail: str = "",
        verification: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        #: 记下「上一次动作」的校验读数与错误码（``last_check()`` 读它）。
        self._note_last_action(verification, result.error_code)
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

    def _read_verification(self) -> Mapping[str, Any] | None:
        """从**旁路出口**（追踪记录）读最新一条的校验读数。

        没接读取器 / 还没有追踪记录 / 这一步没做校验 → ``None``（**不编**）。

        ⚠️ **校验只有一处**：这里**不重跑校验**，只是把校验层已经算好的
        那一份读出来（动作协议保持窄返回类型，校验结果走旁路）。
        """
        if self._verification_reader is None:
            return None
        reading = self._verification_reader()
        return reading

    def _note_last_action(
        self,
        verification: Mapping[str, Any] | None,
        error_code: ErrorCode | None,
    ) -> None:
        """记下**上一次动作**的校验读数与错误码（``last_check()`` 读它）。

        ⚠️ 没有校验读数时**不保留旧的** —— ``last_check()`` 报的是
        「**上一次**动作」的读数，拿更早那一步的读数冒充是撒谎。
        """
        self._last_verification = dict(verification) if verification is not None else None
        self._last_error_code = error_code.value if error_code is not None else None
        self._last_check_reason = (
            "上一次动作没有产生校验读数"
            "（输入文本 / 按键 / 截图 / 等待这一类不产生期望断言）"
            if verification is None
            else "上一次动作的校验读数"
        )

    def _changed_since_last_observation(self, frame: CapturedFrame) -> tuple[bool, bool]:
        """相对**上一次观察**，画面变没变（判不了就如实说判不了）。

        判据用**校验层那一份**（``minicode.verification.diff``）：
        8×8 像素块 + 1% 变化率阈值 —— 不另立一套。
        """
        previous = self._last_observed
        if previous is None or previous.is_empty or frame.is_empty:
            return False, False
        params = ChangeDetectParams()
        try:
            before = frame_signature(previous, params.block_size)
            after = frame_signature(frame, params.block_size)
        except Exception:  # 算不出就是判不了（不假装「没变」）
            return False, False
        rate = change_rate(before, after)
        return rate >= params.change_threshold, True

    def _read_text(self, frame: CapturedFrame) -> tuple[str, bool]:
        """从画面上读文字（**只读**：不产生动作、不注入输入）。

        读不出来（没接 OCR / 后端不可用 / 识别失败）→ 空串 + ``False``：
        **不假装读到**（工单 D-1）。
        """
        if self._ocr_port is None:
            return "", False
        try:
            text = self._ocr_port.read(frame)
        except Exception:
            return "", False
        return str(text), True

    def _frame(self) -> CapturedFrame | None:
        if self._frame_provider is None:
            return None
        return self._frame_provider()

"""**对外形态的动作服务**（工单 M18 / M19）。

这是 CLI、HTTP 与 MCP **共用**的一份实现（工单 M18 AC3「同一入口实现，不是两份」）：
三个前端都只做「解析 → 调这里 → 渲染」，动作语义与坐标口径**只有这一份**。

动作怎么执行：**走动作执行缝**（:mod:`minicode.action.seam`）——
项目唯一的测试缝，坐标口径由它保证（架构备忘 §2.5 ②）。
本模块**不自己**实现定位、注入与校验。

一次调用产出三样东西：

1. **响应**（:class:`~minicode.serve.protocol.ActionResponse`）；
2. **动作记录**（:class:`~minicode.action.records.ActionRecord`，落盘形状）；
3. **动作日志**的一行（追加到 :class:`~minicode.action.records.ActionLog`）。

⚠️ **动作记录有且仅有一条**（缝的契约）：成功与失败都写 ——
失败的那条带着错误码，事后查得到（工单 M17 的回放靠它）。

## 三种调用（工单 M19）

* :meth:`ActionService.execute` —— **一次动作**（CLI / HTTP / MCP 共用）；
* :meth:`ActionService.execute_program` —— **一段动作程序**（受控环境里跑）；
* :meth:`ActionService.observe` —— **读观察**（截图摘要 + 已脱敏的控件树）。

⚠️ **MCP 层不新增动作语义**：它只是把这些调用**暴露成工具**。
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from minicode.action.errors import ErrorCode
from minicode.action.protocol import ActionKind
from minicode.action.records import ActionLog, ActionRecord, ListTraceSink, build_record
from minicode.action.seam import ActionExecutionSeam, SeamRequest, SeamResult
from minicode.redact.errors import ChannelUnavailableError
from minicode.redact.redactor import ModelTreeView, TreeRedactor
from minicode.verification.layer import StateObserver

from .protocol import (
    PROTOCOL_VERSION,
    ActionRequest,
    ActionResponse,
    ObservationRequest,
    ObservationResponse,
    ProgramRequest,
    ProgramResponse,
    RequestError,
)

__all__ = ["ActionService", "SeamFactory", "request_error_response"]


#: 缝工厂：给一条追踪记录出口，造一条**缝**。
#:
#: 为什么要工厂而不是一条缝：缝的追踪记录出口是**构造时定死**的，
#: 而一次调用要只拿**这一次**的记录 —— 工厂让每次调用有自己的一条缝。
SeamFactory = Callable[[ListTraceSink], ActionExecutionSeam]


def request_error_response(error: RequestError) -> ActionResponse:
    """请求层被拒 → 一份失败响应（**零动作下发**）。"""
    return ActionResponse(
        protocol_version=PROTOCOL_VERSION,
        ok=False,
        error_code=error.code.value,
        error_message=error.message,
    )


class _ObservationError(Exception):
    """观察失败（**带协议错误码**）。"""

    def __init__(self, code: ErrorCode, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


class ActionService:
    """对外形态的动作服务。

    :param seam_factory: 造缝的工厂（每次调用造一条带自己追踪出口的缝）。
    :param log_directory: 动作日志目录（动作记录追加到这里）。
    :param observer: **定点**观察端口（读控件树）。缺省 = 这一层没有观察通道。
    :param redactor: 脱敏器（模型侧文本的**唯一出口**）。
        缺省 = 控件树**不许**进模型上下文（架构决策备忘 §2.6 C-15）。
    """

    def __init__(
        self,
        *,
        seam_factory: SeamFactory,
        log_directory: str | Path,
        observer: StateObserver | None = None,
        redactor: TreeRedactor | None = None,
    ):
        self._seam_factory = seam_factory
        self._log = ActionLog(log_directory)
        self._observer = observer
        self._redactor = redactor

    def execute(self, payload: Any) -> ActionResponse:
        """执行一次动作请求。

        请求层被拒（形状不对 / 版本对不上）→ 失败响应，**零动作下发**。
        动作执行失败 → 失败响应，**动作记录照写**（失败也在日志里）。
        """
        try:
            request = ActionRequest.from_mapping(payload)
        except RequestError as error:
            return request_error_response(error)

        result, record = self._run_seam(
            SeamRequest(
                target_description=request.target,
                action_kind=ActionKind(request.action),
                params=dict(request.params),
            )
        )

        action = result.action.to_dict() if result.action is not None else None
        return ActionResponse(
            protocol_version=PROTOCOL_VERSION,
            ok=result.ok,
            error_code=(
                result.result.error_code.value
                if result.result.error_code is not None
                else None
            ),
            error_message=result.result.error_message,
            action=action,
            landing_point=result.final_point,
            record=record.to_dict() if record is not None else None,
        )

    def execute_program(self, payload: Any) -> ProgramResponse:
        """执行**一段动作程序**（工单 M19：MCP 的「执行一段动作程序」）。

        程序在**受控环境**里跑（:mod:`minicode.action.program`）：
        只能调用动作协议暴露的动作，任意系统调用与文件写入**执行前**就被拒。

        ⚠️ **程序源码由调用方给**（宿主的模型写那段程序）——
        本层不生成程序，也不在这里调模型。
        """
        from minicode.action.program import run_program
        from minicode.decision.api import ProtocolApi

        try:
            request = ProgramRequest.from_mapping(payload)
        except RequestError as error:
            return ProgramResponse(
                protocol_version=PROTOCOL_VERSION,
                ok=False,
                error_code=error.code.value,
                error_message=error.message,
            )

        sink = ListTraceSink()
        seam = self._seam_factory(sink)
        # 接口面用的是**同一条缝**（因此同一套坐标口径、同一道审批闸门）。
        api = ProtocolApi(
            seam=seam,
            locator=seam.locator,
            anchor=seam.anchor,
            approval_gate=seam.approval_gate,
        )
        outcome = run_program(request.program, api.as_mapping())

        # 程序里的每个动作都产出一条动作记录（落盘形状）。
        records = []
        for trace in sink.traces:
            record = build_record(trace)
            self._log.append(record)
            records.append(record.to_dict())

        return ProgramResponse(
            protocol_version=PROTOCOL_VERSION,
            ok=outcome.ok,
            error_code=(
                outcome.error_code.value if outcome.error_code is not None else None
            ),
            error_message=outcome.error_message,
            stdout=outcome.stdout,
            stderr=outcome.stderr,
            calls=tuple(api.calls),
            records=tuple(records),
            # **停止信号**（不是「完成」）：程序请求交还给人 / 被停下交人，
            # 宿主据此知道「不是跑完了」——协议里没有「完成」这个动作（工单 M10）。
            asked_human=api.asked_human,
            halted=api.halted,
            halt_reason=api.halt_reason,
        )

    def observe(self, payload: Any) -> ObservationResponse:
        """**读观察**（工单 M19：MCP 的「读观察」）。

        两样东西：

        * **截图摘要** —— 走动作执行缝（同一个通道、同一道审批），
          只给尺寸与**哈希**（与「截图只落哈希」同一条红线）；
        * **控件树** —— **定点**观察（必须带名字条件），
          经脱敏器渲染成模型侧文本（脱敏器是模型侧文本的**唯一出口**）。

        ⚠️ **脱敏没上线 → 控件树不许外发**（架构决策备忘 §2.6 C-15）：
        那一路报 ``channel_disabled``，**一个字符的模型侧文本都不产生**。
        """
        try:
            request = ObservationRequest.from_mapping(payload)
        except RequestError as error:
            return ObservationResponse(
                protocol_version=PROTOCOL_VERSION,
                ok=False,
                error_code=error.code.value,
                error_message=error.message,
            )

        # -- 截图（走动作执行缝） -------------------------------------------
        result, record = self._run_seam(
            SeamRequest(target_description="截图", action_kind=ActionKind.SCREENSHOT)
        )
        frame = result.result.captured
        screenshot = None
        error_code = None
        error_message = None
        if result.ok and frame is not None:
            screenshot = {
                "width": frame.width,
                "height": frame.height,
                "sha256": frame.digest(),
            }
        else:
            error_code = (
                result.result.error_code.value
                if result.result.error_code is not None
                else ErrorCode.EXECUTE_FAILED.value
            )
            error_message = result.result.error_message

        # -- 控件树（定点 + 脱敏） -------------------------------------------
        controls = None
        redaction = None
        controls_count = 0
        if error_code is None:
            try:
                view = self._observe_controls(request.name)
            except _ObservationError as exc:
                error_code = exc.code.value
                error_message = exc.message
            else:
                controls = view.text
                redaction = view.report.to_dict()
                controls_count = len(view.controls)

        return ObservationResponse(
            protocol_version=PROTOCOL_VERSION,
            ok=error_code is None,
            error_code=error_code,
            error_message=error_message,
            screenshot=screenshot,
            controls=controls,
            redaction=redaction,
            controls_count=controls_count,
        )

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------

    def _run_seam(
        self, request: SeamRequest
    ) -> tuple[SeamResult, ActionRecord | None]:
        """跑一次缝，把追踪记录转成动作记录并落盘。

        **恰好一条**动作记录：成功与失败都写（缝的契约）。
        """
        sink = ListTraceSink()
        seam = self._seam_factory(sink)
        result = seam.run(request)
        traces = sink.traces
        record = build_record(traces[-1]) if traces else None
        if record is not None:
            self._log.append(record)
        return result, record

    def _observe_controls(self, name: str) -> ModelTreeView:
        """定点观察控件并**脱敏**（返回脱敏器渲染的模型侧视图）。"""
        if self._observer is None:
            raise _ObservationError(
                ErrorCode.CHANNEL_DISABLED,
                "这一层没有控件树观察通道（观察要定点查询，缺端口就是没有）",
            )
        if self._redactor is None:
            raise _ObservationError(
                ErrorCode.CHANNEL_DISABLED,
                "脱敏器没上线：控件树不许进模型上下文"
                "（架构决策备忘 §2.6 C-15：脱敏必须排在所有通道之前）",
            )
        try:
            candidates = self._observer.observe(name)
        except Exception as exc:  # 读树失败 = 通道不可用，不是「没找到」
            # 观察端口的异常**带协议错误码**时按它报（UIA 那一路就是这么给的）。
            code = getattr(exc, "code", None)
            raise _ObservationError(
                code if code is not None else ErrorCode.QUERY_TIMEOUT,
                str(exc),
            ) from exc
        if not candidates:
            # **候选集为空 = 覆盖失败**（`docs/tickets/README.md` 的口径）：
            # 树读到了，但**一个匹配的控件都没有** → 报 `target_not_found`，
            # 与走动作那一条路**同一个错误码**（不另立一套）。
            raise _ObservationError(
                ErrorCode.TARGET_NOT_FOUND,
                f"控件树里没有与「{name}」匹配的候选",
            )
        try:
            return self._redactor.render(candidates)
        except ChannelUnavailableError as exc:
            raise _ObservationError(ErrorCode.CHANNEL_DISABLED, str(exc)) from exc

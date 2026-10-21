"""**对外形态的动作服务**（工单 M18）。

这是 CLI 与 HTTP **共用**的一份实现（工单 M18 AC3「同一入口实现，不是两份」）：
两个前端都只做「解析 → 调这里 → 渲染」，动作语义与坐标口径**只有这一份**。

动作怎么执行：**走动作执行缝**（:mod:`minicode.action.seam`）——
项目唯一的测试缝，坐标口径由它保证（架构备忘 §2.5 ②）。
本模块**不自己**实现定位、注入与校验。

一次调用产出三样东西：

1. **响应**（:class:`~minicode.serve.protocol.ActionResponse`）；
2. **动作记录**（:class:`~minicode.action.records.ActionRecord`，落盘形状）；
3. **动作日志**的一行（追加到 :class:`~minicode.action.records.ActionLog`）。

⚠️ **动作记录有且仅有一条**（缝的契约）：成功与失败都写 ——
失败的那条带着错误码，事后查得到（工单 M17 的回放靠它）。
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from minicode.action.protocol import ActionKind
from minicode.action.records import ActionLog, ListTraceSink, build_record
from minicode.action.seam import ActionExecutionSeam, SeamRequest

from .protocol import PROTOCOL_VERSION, ActionRequest, ActionResponse, RequestError

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


class ActionService:
    """对外形态的动作服务。

    :param seam_factory: 造缝的工厂（每次调用造一条带自己追踪出口的缝）。
    :param log_directory: 动作日志目录（动作记录追加到这里）。
    """

    def __init__(
        self,
        *,
        seam_factory: SeamFactory,
        log_directory: str | Path,
    ):
        self._seam_factory = seam_factory
        self._log = ActionLog(log_directory)

    def execute(self, payload: Any) -> ActionResponse:
        """执行一次动作请求。

        请求层被拒（形状不对 / 版本对不上）→ 失败响应，**零动作下发**。
        动作执行失败 → 失败响应，**动作记录照写**（失败也在日志里）。
        """
        try:
            request = ActionRequest.from_mapping(payload)
        except RequestError as error:
            return request_error_response(error)

        sink = ListTraceSink()
        seam = self._seam_factory(sink)
        result = seam.run(
            SeamRequest(
                target_description=request.target,
                action_kind=ActionKind(request.action),
                params=dict(request.params),
            )
        )

        # **恰好一条**动作记录：成功与失败都写（缝的契约）。
        traces = sink.traces
        record = build_record(traces[-1]) if traces else None
        if record is not None:
            self._log.append(record)

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

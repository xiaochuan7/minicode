"""**对外形态的请求/响应协议**（工单 M18）。

这是「动作请求」在**外部**的形状：一张键值表进，一张键值表出。

## 协议带版本号（AC4）

请求里必须带 ``protocol`` 字段，取值是**动作协议的版本号**
（:data:`minicode.action.protocol.PROTOCOL_VERSION`，即 ``cua.action/1``）。
**版本对不上 → 明确报错，不按旧语义猜着执行**（:data:`ErrorCode.PROTOCOL_VERSION_MISMATCH`），
**零动作下发**。

## 请求的形状

==================  ==========================================================
字段                 含义
==================  ==========================================================
``protocol``         **必填**：动作协议版本号（``cua.action/1``）
``action``           **必填**：动作种类（:class:`~minicode.action.protocol.ActionKind` 的取值）
``target``           **必填**：目标描述（自然语言，如「开始」）
``params``           可选：动作参数（键值表）
==================  ==========================================================

⚠️ **目标描述是唯一的定位途径**：坐标**不由调用方给**（给了也不认）——
坐标口径只有动作执行缝那一份（架构备忘 §2.5 ②）。

## 响应的形状

==================  ==========================================================
字段                 含义
==================  ==========================================================
``protocol``         本实现支持的协议版本号
``ok``               动作是否成功
``error_code``       失败时的**协议错误码**（:class:`~minicode.action.errors.ErrorCode` 的取值）
``error_message``    失败原因（人读）
``action``           实际下发的动作（种类 / 参数 / 意图）
``landing_point``    最终落点（物理表面空间，``[x, y]``）
==================  ==========================================================

⚠️ **错误码只有一份**：HTTP 与命令行报的都是**动作协议里的**错误码，
不另立一套（工单 M18 AC2）。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from minicode.action.errors import ErrorCode
from minicode.action.protocol import (
    NEEDS_TARGET_KINDS,
    PROTOCOL_VERSION,
    SEAM_ACTION_KINDS,
    ActionKind,
)

__all__ = [
    "PROTOCOL_VERSION",
    "ActionRequest",
    "ActionResponse",
    "ObservationRequest",
    "ObservationResponse",
    "ProgramRequest",
    "ProgramResponse",
    "RequestError",
    "ResponseBase",
    "checked_protocol_version",
]

#: **动作执行缝接的动作种类**（取值来自 :mod:`minicode.action.protocol` ——
#: 缝认哪几种是**一份**真值，本模块不另写一份清单）。
_SEAM_ACTION_VALUES = frozenset(kind.value for kind in SEAM_ACTION_KINDS)

#: **需要目标描述**的动作种类（其余的不要：截图 / 等待不定位目标，
#: 放大的落点来自 ``params["region"]``、目标描述只进意图串）。
_NEEDS_TARGET_VALUES = frozenset(kind.value for kind in NEEDS_TARGET_KINDS)


class RequestError(Exception):
    """请求层被拒（**没有动作下发**）。

    与动作执行失败分开：动作执行失败有错误码、有动作记录；
    请求层被拒**什么都没有**（连动作记录都没有 —— 没发生过动作）。
    """

    def __init__(self, code: ErrorCode, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def checked_protocol_version(payload: Any) -> str:
    """核对请求带的**协议版本号**（**唯一一处**版本核对）。

    请求里必须带 ``protocol`` 字段，取值是动作协议的版本号
    （:data:`minicode.action.protocol.PROTOCOL_VERSION`，即 ``cua.action/1``）。
    **版本对不上 → 明确报错，不按旧语义猜着执行**
    （:data:`ErrorCode.PROTOCOL_VERSION_MISMATCH`），**零动作下发**。

    ⚠️ **版本核对只有这一份**：动作请求、动作程序请求、观察请求都走它 ——
    三处各写一遍时，迟早有一处漏掉「不匹配就报错」这条。
    """
    if not isinstance(payload, Mapping):
        raise RequestError(
            ErrorCode.INVALID_REQUEST,
            f"请求不是一张键值表（收到 {type(payload).__name__}）",
        )
    version = payload.get("protocol")
    if not isinstance(version, str) or not version:
        raise RequestError(
            ErrorCode.INVALID_REQUEST,
            f"请求缺 protocol 字段（必须是 {PROTOCOL_VERSION}）",
        )
    if version != PROTOCOL_VERSION:
        # **不按旧语义猜着执行**（AC4）：版本对不上 → 明确报错。
        raise RequestError(
            ErrorCode.PROTOCOL_VERSION_MISMATCH,
            f"协议版本不匹配：请求 {version}，本实现只支持 {PROTOCOL_VERSION}",
        )
    return version


@dataclass(frozen=True)
class ActionRequest:
    """一次动作请求（外部形状）。

    ⚠️ **只能经 :meth:`from_mapping` 造**：校验（版本、动作种类、目标描述）
    都在那里，绕过它就绕过了「明确报错、不猜着执行」。
    """

    #: 动作协议版本号（必填）。
    protocol_version: str
    #: 动作种类（:class:`~minicode.action.protocol.ActionKind` 的取值）。
    action: str
    #: 目标描述（自然语言）。**坐标不由调用方给**。
    target: str
    #: 动作参数（键值表）。
    params: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, payload: Any) -> ActionRequest:
        """把一张外部载荷变成请求。

        形状不对 → :class:`RequestError`（错误码取协议里的值）。
        """
        version = checked_protocol_version(payload)
        action = payload.get("action", ActionKind.CLICK.value)
        if not isinstance(action, str) or not action:
            raise RequestError(
                ErrorCode.INVALID_REQUEST,
                "请求的 action 字段不是动作种类",
            )
        if action not in {kind.value for kind in ActionKind}:
            raise RequestError(
                ErrorCode.ACTION_NOT_IMPLEMENTED,
                f"动作种类 {action!r} 不在动作协议里（协议的动作种类只有 12 个）",
            )
        if action not in _SEAM_ACTION_VALUES:
            # **缝不接的种类在这里就说清楚**，不许让它一路走到缝里抛
            # ValueError（异常逃到前端 = 客户端只看到连接断掉，没有错误码）。
            raise RequestError(
                ErrorCode.ACTION_NOT_IMPLEMENTED,
                f"动作种类 {action!r} 这一层还不接"
                "（本层只做需要落点的动作、截图/等待与放大）",
            )
        target = payload.get("target", "")
        if not isinstance(target, str):
            raise RequestError(
                ErrorCode.INVALID_REQUEST,
                "请求的 target 字段不是字符串",
            )
        if action in _NEEDS_TARGET_VALUES and not target.strip():
            # **只有需要定位目标的动作种类才要 target**（截图 / 等待不要）。
            raise RequestError(
                ErrorCode.INVALID_REQUEST,
                "请求缺 target 字段（目标描述）",
            )
        params = payload.get("params", {})
        if not isinstance(params, Mapping):
            raise RequestError(
                ErrorCode.INVALID_REQUEST,
                f"params 不是一张键值表（收到 {type(params).__name__}）",
            )
        return cls(
            protocol_version=version,
            action=action,
            target=target,
            params=dict(params),
        )


@dataclass(frozen=True)
class ResponseBase:
    """对外形态三种响应的**公共部分**。

    协议版本、成败、错误码、错误信息 —— **三个响应都一样**，
    写三遍迟早有一处漏改（所以只写这一份）。
    """

    #: 本实现支持的协议版本号。
    protocol_version: str
    #: 这次调用是否成功。
    ok: bool
    #: 失败时的**协议错误码**（成功时为 ``None``）。
    error_code: str | None = None
    #: 失败原因（人读）。
    error_message: str | None = None

    def common_fields(self) -> dict[str, Any]:
        """公共部分的机器可读形状。"""
        return {
            "protocol_version": self.protocol_version,
            "ok": self.ok,
            "error_code": self.error_code,
            "error_message": self.error_message,
        }


@dataclass(frozen=True)
class ActionResponse(ResponseBase):
    """一次动作的响应（外部形状）。"""

    #: 实际下发的动作（``kind`` / ``params`` / ``intent``）。
    action: Mapping[str, Any] | None = None
    #: 最终落点（物理表面空间）。
    landing_point: tuple[int, int] | None = None
    #: 动作记录（**落盘形状**，动作日志的一行）。
    record: Mapping[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        """机器可读的形状（HTTP 响应体 / 命令行 ``--json`` 输出）。"""
        return {
            **self.common_fields(),
            "action": dict(self.action) if self.action is not None else None,
            "landing_point": (
                list(self.landing_point) if self.landing_point is not None else None
            ),
            "record": dict(self.record) if self.record is not None else None,
        }


@dataclass(frozen=True)
class ProgramRequest:
    """一次**动作程序**请求（外部形状）。

    动作程序是决策层唯一的产出形态（ADR-0005）：它在**受控环境**里跑
    （:mod:`minicode.action.program`），只能调用动作协议暴露的动作。

    ⚠️ **程序源码由调用方给**（宿主的模型写那段程序）——
    本层**不生成**程序，也不在这里调模型。
    """

    #: 动作协议版本号（必填）。
    protocol_version: str
    #: 动作程序源码（受控环境里跑的 Python 片段）。
    program: str

    @classmethod
    def from_mapping(cls, payload: Any) -> ProgramRequest:
        """把一张外部载荷变成程序请求。

        形状不对 → :class:`RequestError`（错误码取协议里的值）。
        """
        version = checked_protocol_version(payload)
        source = payload.get("program")
        if not isinstance(source, str) or not source.strip():
            raise RequestError(
                ErrorCode.INVALID_REQUEST,
                "请求缺 program 字段（动作程序源码）",
            )
        return cls(protocol_version=version, program=source)


@dataclass(frozen=True)
class ProgramResponse(ResponseBase):
    """一次动作程序执行的响应（外部形状）。

    ⚠️ **协议里没有「完成」**（工单 M10）：``ok`` 只表示「程序跑完了且没抛异常」，
    **不代表任务完成** —— 终止只由 harness 侧的停止条件承载。
    """

    #: 程序的标准输出。
    stdout: str = ""
    #: 程序的标准错误。
    stderr: str = ""
    #: 程序每一次接口调用的记录（种类 / 目标 / 成败 / 落点 / 错误码）。
    calls: tuple[Mapping[str, Any], ...] = ()
    #: 动作记录（**落盘形状**，动作日志的一行一条）。
    records: tuple[Mapping[str, Any], ...] = ()
    #: 程序**请求交还给人**了吗（``ask_user``）。
    #:
    #: ⚠️ **这不是「完成」**：它是**停止信号**——宿主据此知道
    #: 「程序不是跑完了，是把任务交还给人了」（工单 M12 的交接语义）。
    asked_human: bool = False
    #: 程序被**停下**了吗（危险动作没获批 → 零动作下发、交人）。
    halted: bool = False
    #: 停下 / 交还给人的原因（人读）。
    halt_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        """机器可读的形状。"""
        return {
            **self.common_fields(),
            "stdout": self.stdout,
            "stderr": self.stderr,
            "calls": [dict(call) for call in self.calls],
            "records": [dict(record) for record in self.records],
            "asked_human": self.asked_human,
            "halted": self.halted,
            "halt_reason": self.halt_reason,
        }


@dataclass(frozen=True)
class ObservationRequest:
    """一次**观察**请求（外部形状）。

    ⚠️ **观察是定点的**：必须带名字条件（架构决策备忘 §2.6 C-09：
    禁止全树 dump —— 全桌面遍历约 1000 个元素要 ≥8 秒）。
    """

    #: 动作协议版本号（必填）。
    protocol_version: str
    #: 控件名条件（自然语言，如「开始」）。
    name: str

    @classmethod
    def from_mapping(cls, payload: Any) -> ObservationRequest:
        """把一张外部载荷变成观察请求。"""
        version = checked_protocol_version(payload)
        name = payload.get("name", "")
        if not isinstance(name, str):
            raise RequestError(
                ErrorCode.INVALID_REQUEST,
                "请求的 name 字段不是字符串",
            )
        if not name.strip():
            raise RequestError(
                ErrorCode.INVALID_REQUEST,
                "请求缺 name 字段：观察是**定点**的，必须带属性条件"
                "（架构决策备忘 §2.6 C-09：禁止全树 dump）",
            )
        return cls(protocol_version=version, name=name)


@dataclass(frozen=True)
class ObservationResponse(ResponseBase):
    """一次观察的响应（外部形状）。

    两样东西：**截图摘要**（只给尺寸与哈希 —— 与「截图只落哈希」同一条红线）
    与**已脱敏的控件树**（模型侧文本，脱敏器是它的唯一出口）。
    """

    #: 截图摘要（``width`` / ``height`` / ``sha256``）。
    screenshot: Mapping[str, Any] | None = None
    #: **已脱敏**的控件树（模型侧文本）。
    controls: str | None = None
    #: 脱敏计数。
    redaction: Mapping[str, Any] | None = None
    #: 观察到的控件数。
    controls_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        """机器可读的形状。"""
        return {
            **self.common_fields(),
            "screenshot": dict(self.screenshot) if self.screenshot is not None else None,
            "controls": self.controls,
            "redaction": dict(self.redaction) if self.redaction is not None else None,
            "controls_count": self.controls_count,
        }

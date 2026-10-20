"""动作协议 v1：版本号 / 动作种类 / 参数 / 结果。

协议形状一次立住，后续工单按 v1 扩展，不各写一套。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping

from .errors import ErrorCode

__all__ = [
    "PROTOCOL_VERSION",
    "ActionKind",
    "EXECUTABLE_KINDS",
    "DEFERRED_KINDS",
    "POINT_KINDS",
    "Action",
    "ActionResult",
]

#: 动作协议版本号。协议形状变化时升版本，不在 v1 上加字段。
PROTOCOL_VERSION = "cua.action/1"


class ActionKind(str, Enum):
    """动作种类。"""

    #: 点击。
    CLICK = "click"
    #: 双击。
    DOUBLE_CLICK = "double_click"
    #: 右键点击。
    RIGHT_CLICK = "right_click"
    #: 移动（悬停）。
    MOVE = "move"
    #: 拖拽。
    DRAG = "drag"
    #: 滚动（对外单位是行）。
    SCROLL = "scroll"
    #: 输入文本。
    TYPE_TEXT = "type_text"
    #: 按键。
    KEY = "key"

    # -- 本单不实现：先返回明确错误码，不做半实现 --
    #: 放大（回源重抓，模型仍按整屏坐标回答）。
    ZOOM = "zoom"
    #: 读控件树。
    INSPECT = "inspect"
    #: 执行代码。
    EXECUTE_CODE = "execute_code"
    #: 请求人工。
    ASK_USER = "ask_user"


#: 本单已接线、可执行的动作种类。
EXECUTABLE_KINDS = frozenset(
    {
        ActionKind.CLICK,
        ActionKind.DOUBLE_CLICK,
        ActionKind.RIGHT_CLICK,
        ActionKind.MOVE,
        ActionKind.DRAG,
        ActionKind.SCROLL,
        ActionKind.TYPE_TEXT,
        ActionKind.KEY,
    }
)

#: 本单不实现的动作种类 —— 由后续工单按协议 v1 扩展启用。
DEFERRED_KINDS = frozenset(
    {
        ActionKind.ZOOM,
        ActionKind.INSPECT,
        ActionKind.EXECUTE_CODE,
        ActionKind.ASK_USER,
    }
)

#: 需要一个「落点」的动作种类（其余动作不带坐标）。
POINT_KINDS = frozenset(
    {
        ActionKind.CLICK,
        ActionKind.DOUBLE_CLICK,
        ActionKind.RIGHT_CLICK,
        ActionKind.MOVE,
        ActionKind.DRAG,
        ActionKind.SCROLL,
    }
)


@dataclass(frozen=True)
class Action:
    """一次动作：种类 + 参数 + 意图。"""

    kind: ActionKind
    params: Mapping[str, Any] = field(default_factory=dict)
    intent: str = ""

    def to_dict(self) -> dict:
        return {
            "kind": self.kind.value,
            "params": dict(self.params),
            "intent": self.intent,
        }


@dataclass(frozen=True)
class ActionResult:
    """一次动作的结果（含错误码）。"""

    ok: bool
    error_code: ErrorCode | None = None
    error_message: str | None = None
    landing_point: tuple[int, int] | None = None
    cursor_readback: tuple[int, int] | None = None
    notes: tuple[str, ...] = ()
    #: 实测时序（毫秒）。键固定：pre_move_ms / hold_ms / total_ms /
    #: double_click_gap_ms / scroll_settle_ms / scroll_delta。
    timing: Mapping[str, Any] | None = None

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "error_code": self.error_code.value if self.error_code else None,
            "error_message": self.error_message,
            "landing_point": list(self.landing_point) if self.landing_point else None,
            "cursor_readback": list(self.cursor_readback) if self.cursor_readback else None,
            "notes": list(self.notes),
            "timing": dict(self.timing) if self.timing else None,
        }

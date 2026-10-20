"""动作协议 v1：版本号 / 动作种类 / 参数 / 结果。

协议形状一次立住，后续工单按 v1 扩展，不各写一套。

**动作种类集合 = 架构决策备忘 §3 的 12 个**（工单 M21）::

    screenshot / click / move / drag / type / key / scroll / wait /
    zoom / inspect / execute_code / ask_user

**双击与右键是 `click` 的参数**（``click_count`` / ``button``），
不是独立动作种类 —— 协议里不再有 ``double_click`` / ``right_click``。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from .errors import ErrorCode
from .ports import CapturedFrame

__all__ = [
    "PROTOCOL_VERSION",
    "ActionKind",
    "EXECUTABLE_KINDS",
    "DEFERRED_KINDS",
    "POINT_KINDS",
    "OBSERVATION_KINDS",
    "STANDALONE_KINDS",
    "BUTTON_LEFT",
    "BUTTON_RIGHT",
    "CLICK_SINGLE",
    "CLICK_DOUBLE",
    "int_param",
    "parse_click_params",
    "Action",
    "ActionResult",
]

#: 动作协议版本号。
#:
#: **版本口径**（工单 M21 写死）：**只增不改**地在 v1 上加字段
#: （如 :attr:`ActionResult.captured`）**不升版本**；
#: **改已有字段的含义或删字段**才升版本。
PROTOCOL_VERSION = "cua.action/1"

#: `click` 的按键参数：1 = 左键，3 = 右键。
#: （协议自己的取值口径，**不是** Win32 的 ``MK_*`` 标志位。）
BUTTON_LEFT = 1
BUTTON_RIGHT = 3

#: `click` 的次数参数：1 = 单击，2 = 双击。
CLICK_SINGLE = 1
CLICK_DOUBLE = 2


class ActionKind(StrEnum):
    """动作种类（架构决策备忘 §3 的 12 个，逐字对齐）。"""

    #: 截图。
    SCREENSHOT = "screenshot"
    #: 点击。双击与右键是它的参数（``click_count`` / ``button``）。
    CLICK = "click"
    #: 移动（悬停）。
    MOVE = "move"
    #: 拖拽。
    DRAG = "drag"
    #: 输入文本。
    TYPE = "type"
    #: 按键。
    KEY = "key"
    #: 滚动（对外单位是行）。
    SCROLL = "scroll"
    #: 等待。
    WAIT = "wait"

    # -- 本单不实现：先返回明确错误码，不做半实现 --
    #: 放大（回源重抓，模型仍按整屏坐标回答）—— 工单 M6 接线。
    ZOOM = "zoom"
    #: 读控件树。
    INSPECT = "inspect"
    #: 执行代码。
    EXECUTE_CODE = "execute_code"
    #: 请求人工。
    ASK_USER = "ask_user"


#: 已接线、可执行的动作种类。
EXECUTABLE_KINDS = frozenset(
    {
        ActionKind.SCREENSHOT,
        ActionKind.CLICK,
        ActionKind.MOVE,
        ActionKind.DRAG,
        ActionKind.TYPE,
        ActionKind.KEY,
        ActionKind.SCROLL,
        ActionKind.WAIT,
        ActionKind.ZOOM,
    }
)

#: 本单不实现的动作种类 —— 由后续工单按协议 v1 扩展启用。
DEFERRED_KINDS = frozenset(
    {
        ActionKind.INSPECT,
        ActionKind.EXECUTE_CODE,
        ActionKind.ASK_USER,
    }
)

#: 需要一个「落点」的动作种类（其余动作不带坐标）。
POINT_KINDS = frozenset(
    {
        ActionKind.CLICK,
        ActionKind.MOVE,
        ActionKind.DRAG,
        ActionKind.SCROLL,
    }
)

#: **观察型**动作种类：产出一个落点，但**不注入任何输入**（工单 M6 的放大）。
#: 它与 :data:`POINT_KINDS` 分开：点一下要动指针，放大只是「看清楚」。
OBSERVATION_KINDS = frozenset({ActionKind.ZOOM})

#: **不需要定位目标**的动作种类（截图 / 等待）：它们没有最终落点。
STANDALONE_KINDS = frozenset({ActionKind.SCREENSHOT, ActionKind.WAIT})


def int_param(params: Mapping[str, Any], name: str, default: int) -> int:
    """取一个整数参数。给了但不是整数 → 抛 ``ValueError``（不猜）。"""
    raw = params.get(name, default)
    try:
        return int(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"参数 {name} 非法：{exc}") from exc


def parse_click_params(params: Mapping[str, Any]) -> tuple[int, int]:
    """``click`` 的两个参数：``(button, click_count)``。

    **协议里只有这一份读法** —— 执行器与动作执行缝都调它，
    两边不会对同一个参数得出不同的结论。非法值抛 ``ValueError``。
    """
    button = int_param(params, "button", BUTTON_LEFT)
    click_count = int_param(params, "click_count", CLICK_SINGLE)
    if button not in (BUTTON_LEFT, BUTTON_RIGHT):
        raise ValueError(
            f"click 的 button 参数只能是 {BUTTON_LEFT}（左键）或 {BUTTON_RIGHT}（右键），"
            f"收到 {button}"
        )
    if click_count not in (CLICK_SINGLE, CLICK_DOUBLE):
        raise ValueError(
            f"click 的 click_count 参数只能是 {CLICK_SINGLE}（单击）或 "
            f"{CLICK_DOUBLE}（双击），收到 {click_count}"
        )
    return button, click_count


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
    #: 光标回读与落点的偏差（物理像素）。验收要求它是 0。
    cursor_delta_px: int | None = None
    notes: tuple[str, ...] = ()
    #: 实测时序（毫秒）。键固定：pre_move_ms / hold_ms / total_ms /
    #: double_click_gap_ms / scroll_settle_ms / scroll_delta / wait_ms。
    timing: Mapping[str, Any] | None = None
    #: 观察到的一帧画面（``screenshot`` 动作的结果）。
    #: **只落它的摘要，不落原图** —— 与动作记录同一条红线。
    captured: CapturedFrame | None = None

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "error_code": self.error_code.value if self.error_code else None,
            "error_message": self.error_message,
            "landing_point": list(self.landing_point) if self.landing_point else None,
            "cursor_readback": list(self.cursor_readback) if self.cursor_readback else None,
            "cursor_delta_px": self.cursor_delta_px,
            "notes": list(self.notes),
            "timing": dict(self.timing) if self.timing else None,
            "captured": (
                {
                    "width": self.captured.width,
                    "height": self.captured.height,
                    "sha256": self.captured.digest(),
                }
                if self.captured is not None
                else None
            ),
        }

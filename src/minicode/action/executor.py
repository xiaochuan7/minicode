"""动作执行器：把一个 :class:`~minicode.action.protocol.Action` 变成注入序列。

协议层的时间与顺序策略都在这里，**端口以下**的实现（真机 ``SendInput`` 或假实现）
只负责「把一件事做出去」，不决定顺序。这样时序既能在真机上被实测，
也能在单测里被断言。

时序（架构备忘 C-11）::

    移动 → 停顿 ≥60ms → 按下 → 停顿 ≥40ms → 抬起

移动与按下**不得合并进同一次注入**。Windows 定时器精度 15.6 ms，
因此断言一律用 ``>=`` 而不是 ``==``。
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from .errors import ActionError, ErrorCode
from .geometry import is_sentinel
from .ports import InjectionBlocked, InputInjector
from .protocol import DEFERRED_KINDS, Action, ActionKind, ActionResult

__all__ = [
    "ActionExecutor",
    "TimingPolicy",
    "DoubleClickParams",
    "SCROLL_DELTA_PER_LINE",
    "CURSOR_READBACK_TOLERANCE_PX",
    "C07_DEBT_NOTE",
]

#: 对外单位是「行」，下发前按 ×50 换算到滚轮刻度（架构备忘 §2 L0 的项目约定）。
SCROLL_DELTA_PER_LINE = 50

#: 光标回读与落点的允许偏差（物理像素）。超过即判执行失败。
CURSOR_READBACK_TOLERANCE_PX = 2.0

#: 注入被拦下时写进记录里的已知欠账。
C07_DEBT_NOTE = (
    "C-07 未满足：目标窗口完整性级别更高时注入会被拦下（SendInput 返回 0）。"
    "本单只如实记为执行失败，**零动作下发并交人**欠到安全与审计层工单。"
)


@dataclass(frozen=True)
class TimingPolicy:
    """动作协议的时间参数。"""

    #: 移动到按下之间的停顿（秒）。C-11 要求 ≥60 ms。
    settle_s: float = 0.060
    #: 按下到抬起之间的按住时长（秒）。C-11 要求 ≥40 ms。
    hold_s: float = 0.040
    #: 滚完之后等多久再取下一帧（秒）。规避滚轮惯性，落在 300~600 ms。
    scroll_settle_s: float = 0.400


@dataclass(frozen=True)
class DoubleClickParams:
    """系统的双击参数。

    **必须运行时查询，不写死**（架构备忘 §3：系统双击参数在运行时查询）。
    因此这里**不给默认值**：没有注入参数就说明没有查过系统，
    双击动作会直接报错，而不是拿写死的 500 ms / 4 px 凑合。
    真机的查询入口是 :func:`minicode.action.windows.system_double_click_params`。
    """

    #: 两次按下之间的最大间隔（毫秒）。
    time_ms: int
    #: 两次按下之间允许的最大位移（像素）。
    distance_px: int


def _empty_timing() -> dict[str, Any]:
    return {
        "pre_move_ms": 0.0,
        "hold_ms": 0.0,
        "total_ms": 0.0,
        "double_click_gap_ms": None,
        "double_click_requested_gap_ms": None,
        "scroll_settle_ms": 0.0,
        "scroll_delta": 0,
    }


class ActionExecutor:
    """执行动作协议 v1 里的动作。"""

    def __init__(
        self,
        injector: InputInjector,
        *,
        sleeper: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.perf_counter,
        timing: TimingPolicy | None = None,
        double_click_params: DoubleClickParams | None = None,
    ):
        self.injector = injector
        self.timing = timing or TimingPolicy()
        self.double_click_params = double_click_params
        self._sleep = sleeper
        self._clock = clock

    # ------------------------------------------------------------------
    # 主入口
    # ------------------------------------------------------------------

    def execute(self, action: Action) -> ActionResult:
        """执行一个动作，返回结果（含错误码）。"""
        kind = action.kind
        if kind in DEFERRED_KINDS:
            return ActionResult(
                ok=False,
                error_code=ErrorCode.ACTION_NOT_IMPLEMENTED,
                error_message=(
                    f"动作「{kind.value}」本单不实现；"
                    "按协议 v1 由后续工单扩展启用，本单不做半实现。"
                ),
            )
        try:
            if kind is ActionKind.CLICK:
                return self._click(action, "left")
            if kind is ActionKind.RIGHT_CLICK:
                return self._click(action, "right")
            if kind is ActionKind.DOUBLE_CLICK:
                return self._double_click(action)
            if kind is ActionKind.MOVE:
                return self._move(action)
            if kind is ActionKind.DRAG:
                return self._drag(action)
            if kind is ActionKind.SCROLL:
                return self._scroll(action)
            if kind is ActionKind.TYPE_TEXT:
                return self._type_text(action)
            if kind is ActionKind.KEY:
                return self._key(action)
        except InjectionBlocked as exc:
            return ActionResult(
                ok=False,
                error_code=ErrorCode.INJECTION_BLOCKED,
                error_message=str(exc),
                notes=(C07_DEBT_NOTE,),
            )
        except ActionError as exc:
            return ActionResult(
                ok=False, error_code=exc.code, error_message=exc.message
            )
        raise ActionError(  # pragma: no cover - 枚举全覆盖
            ErrorCode.EXECUTE_FAILED, f"未处理的动作种类：{kind!r}"
        )

    # ------------------------------------------------------------------
    # 各类动作
    # ------------------------------------------------------------------

    def _click(self, action: Action, button: str) -> ActionResult:
        x, y = self._point(action)
        timing, _, _ = self._click_sequence(x, y, button)
        return self._finish(x, y, timing)

    def _double_click(self, action: Action) -> ActionResult:
        x, y = self._point(action)
        params = self.double_click_params
        if params is None:
            raise ActionError(
                ErrorCode.EXECUTE_FAILED,
                "双击需要系统的双击参数（时间与位移），必须运行时查询后注入；"
                "本单不写死这两个值。",
            )
        # 两次按下的间隔必须**小于**系统双击时间；位移为 0（两次点在同一个位置）。
        gap_s = min(0.080, params.time_ms / 2000.0)
        _first, first_pressed_at, _ = self._click_sequence(x, y, "left")
        self._sleep(gap_s)
        second, second_pressed_at, _ = self._click_sequence(x, y, "left")
        timing = second
        # 记**实测**的两次按下间隔（Windows 判双击看的是按下与按下之间），
        # 而不是我们请求的那段 sleep——第二次点击序列自带一段停顿。
        timing["double_click_gap_ms"] = (second_pressed_at - first_pressed_at) * 1000.0
        timing["double_click_requested_gap_ms"] = gap_s * 1000.0
        return self._finish(x, y, timing)

    def _move(self, action: Action) -> ActionResult:
        x, y = self._point(action)
        started = self._clock()
        self.injector.move(x, y)
        timing = _empty_timing()
        timing["total_ms"] = (self._clock() - started) * 1000.0
        return self._finish(x, y, timing)

    def _drag(self, action: Action) -> ActionResult:
        params = action.params
        try:
            fx, fy = int(params["from_x"]), int(params["from_y"])
            tx, ty = int(params["to_x"]), int(params["to_y"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ActionError(ErrorCode.EXECUTE_FAILED, f"drag 动作参数非法：{exc}") from exc
        started = self._clock()
        self.injector.move(fx, fy)
        self._sleep(self.timing.settle_s)
        self.injector.button_down("left")
        self._sleep(self.timing.hold_s)
        self.injector.move(tx, ty)
        self._sleep(self.timing.settle_s)
        self.injector.button_up("left")
        timing = _empty_timing()
        timing["total_ms"] = (self._clock() - started) * 1000.0
        return self._finish(tx, ty, timing)

    def _scroll(self, action: Action) -> ActionResult:
        x, y = self._point(action)
        try:
            lines = int(action.params.get("lines", 0))
        except (TypeError, ValueError) as exc:
            raise ActionError(ErrorCode.EXECUTE_FAILED, f"scroll 的 lines 参数非法：{exc}") from exc
        delta = lines * SCROLL_DELTA_PER_LINE
        started = self._clock()
        self.injector.move(x, y)
        self.injector.scroll_delta(x, y, delta)
        # 滚完等一会儿再取下一帧，规避滚轮惯性。
        self._sleep(self.timing.scroll_settle_s)
        timing = _empty_timing()
        timing["total_ms"] = (self._clock() - started) * 1000.0
        timing["scroll_settle_ms"] = self.timing.scroll_settle_s * 1000.0
        timing["scroll_delta"] = delta
        return self._finish(x, y, timing)

    def _type_text(self, action: Action) -> ActionResult:
        text = str(action.params.get("text", ""))
        backup: str | None = None
        backup_read = False
        channel = "clipboard"
        notes: list[str] = []
        try:
            backup = self.injector.read_clipboard()
            backup_read = True
            self.injector.write_clipboard(text)
            self.injector.press_combo(("ctrl", "v"))
        except Exception as exc:  # 粘贴通道失败 -> 退回 Unicode 注入通道
            channel = "unicode"
            notes.append(f"粘贴通道失败（{exc}），已退回 Unicode 注入通道")
            self.injector.type_unicode(text)
        finally:
            # 剪贴板**必须**在 finally 路径恢复，否则污染用户环境。
            if backup_read:
                try:
                    self.injector.write_clipboard(backup)
                except Exception:
                    notes.append("剪贴板恢复失败")
        notes.insert(0, f"注入通道={channel}")
        return ActionResult(ok=True, notes=tuple(notes))

    def _key(self, action: Action) -> ActionResult:
        combo = tuple(str(part) for part in action.params.get("combo", ()))
        if not combo:
            raise ActionError(ErrorCode.EXECUTE_FAILED, "key 动作缺少 combo 参数")
        self.injector.press_combo(combo)
        return ActionResult(ok=True)

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------

    def _click_sequence(self, x: int, y: int, button: str) -> tuple[dict[str, Any], float, float]:
        """移动 → 停顿 → 按下 → 停顿 → 抬起。

        移动与按下是**两次独立注入**，不合并。

        返回 ``(时序, 按下时刻, 抬起时刻)``；后两个是时钟读数（秒），
        供双击算真实的两次按下间隔。
        """
        timing = _empty_timing()
        started = self._clock()
        self.injector.move(x, y)
        self._sleep(self.timing.settle_s)
        pressed_at = self._clock()
        self.injector.button_down(button)
        self._sleep(self.timing.hold_s)
        released_at = self._clock()
        self.injector.button_up(button)
        timing["pre_move_ms"] = (pressed_at - started) * 1000.0
        timing["hold_ms"] = (released_at - pressed_at) * 1000.0
        timing["total_ms"] = (self._clock() - started) * 1000.0
        return timing, pressed_at, released_at

    def _point(self, action: Action) -> tuple[int, int]:
        params: Mapping[str, Any] = action.params
        try:
            return int(params["x"]), int(params["y"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ActionError(
                ErrorCode.EXECUTE_FAILED, f"{action.kind.value} 动作缺少合法的 x/y 参数：{exc}"
            ) from exc

    def _finish(self, x: int, y: int, timing: dict[str, Any]) -> ActionResult:
        """执行后校验：回读光标，确认与落点一致且不是哨兵值。"""
        landing = (int(x), int(y))
        readback = self.injector.read_cursor()
        if readback is None:
            return ActionResult(
                ok=False,
                error_code=ErrorCode.CURSOR_MISMATCH,
                error_message="光标回读失败（读不到当前位置）",
                landing_point=landing,
                timing=timing,
            )
        readback = (int(readback[0]), int(readback[1]))
        if is_sentinel(readback):
            return ActionResult(
                ok=False,
                error_code=ErrorCode.CURSOR_SENTINEL,
                error_message="光标回读落在哨兵值 (−32000,−32000)，不当作合法坐标",
                landing_point=landing,
                cursor_readback=readback,
                timing=timing,
            )
        delta = max(abs(readback[0] - landing[0]), abs(readback[1] - landing[1]))
        if delta > CURSOR_READBACK_TOLERANCE_PX:
            return ActionResult(
                ok=False,
                error_code=ErrorCode.CURSOR_MISMATCH,
                error_message=f"光标回读 {readback} 与落点 {landing} 偏差 {delta} px",
                landing_point=landing,
                cursor_readback=readback,
                cursor_delta_px=delta,
                timing=timing,
            )
        return ActionResult(
            ok=True,
            landing_point=landing,
            cursor_readback=readback,
            cursor_delta_px=delta,
            timing=timing,
        )

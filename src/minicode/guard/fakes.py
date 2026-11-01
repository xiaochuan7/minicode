"""**护栏测试的假依赖**：键状态 / 输入时刻 / 前台窗口 / 时钟。

全部是**纯函数替换**（不碰真 Win32），形状与
:mod:`minicode.guard.win32` 的真实现一致 —— 换实现不改端口形状。
"""

from __future__ import annotations

from minicode.guard.win32 import ForegroundWindowInfo

__all__ = ["fake_key_state_fn", "fake_tick_pair", "fake_window_info_fn"]


def fake_key_state_fn(pressed: set[int]):
    """按键状态查询：``pressed`` 集合里的虚拟键算按下。"""

    def state(vkey: int) -> bool:
        return vkey in pressed

    return state


def fake_tick_pair(last_input: int, now: int):
    """空闲让位的一对假查询：``(last_input_tick, now_tick)`` 固定值。"""

    def tick_fn() -> int:
        return last_input

    def now_fn() -> int:
        return now

    return tick_fn, now_fn


def fake_window_info_fn(info: ForegroundWindowInfo | None):
    """前台窗口查询：固定返回给定信息（``None`` = 读不到）。"""

    def info_fn() -> ForegroundWindowInfo | None:
        return info

    return info_fn

"""**空闲让位**（护栏 6）：用户动键鼠，agent 就暂停。

判定口径（标准式）：**空闲时长 = ``GetTickCount()`` − ``GetLastInputInfo.dwTime``**，
两个值是**同一套时钟**（系统启动起的毫秒 TickCount，含 32 位回绕）。
空闲时长**小于**判定窗（默认 1500 ms）→ 用户正在用 → 暂停
（``user_active_paused``，零动作下发）。

这不是「永远不碰用户正在用的机器」——真机占用锁已经管了「人在不在」；
这一层管「**人刚刚还在动**」：锁是分钟级的约定，空闲让位是秒级的礼让。

两个查询都**注入**：单测传 fake，真机接线传
:func:`minicode.guard.win32.last_input_tick` 与 :func:`minicode.guard.win32.now_tick`。
"""

from __future__ import annotations

from collections.abc import Callable

__all__ = ["IdleWatcher", "TICK_WRAP"]


#: 32 位 TickCount 的回绕模数（系统连续运行 49.7 天回绕一次，必须按模运算）。
TICK_WRAP = 0x1_0000_0000


def _tick_delta(later: int, earlier: int) -> int:
    """TickCount 差值（按 32 位回绕取模）。"""
    return (later - earlier) % TICK_WRAP


class IdleWatcher:
    """空闲让位判定器（**只读系统状态，不注入任何输入**）。

    用法::

        watcher = IdleWatcher(tick_fn=last_input_tick, now_tick_fn=now_tick)
        if watcher.user_recently_active(active_ms=1500):
            ...  # 暂停让位（零动作下发）
    """

    def __init__(
        self,
        *,
        tick_fn: Callable[[], int],
        now_tick_fn: Callable[[], int],
    ):
        self._tick_fn = tick_fn
        self._now_tick_fn = now_tick_fn

    @property
    def now_tick_fn(self) -> Callable[[], int]:
        """当前时刻查询（Tick 口径；空闲归因用）。"""
        return self._now_tick_fn

    def idle_ms(self) -> int:
        """当前空闲时长（毫秒）：现在 − 最后一次输入。"""
        return _tick_delta(self._now_tick_fn(), self._tick_fn())

    def user_recently_active(self, active_ms: int) -> bool:
        """用户在最近 ``active_ms`` 毫秒内动过键鼠吗。"""
        return self.idle_ms() < active_ms

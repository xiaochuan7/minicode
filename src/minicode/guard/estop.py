"""**急停热键**（护栏 1）：watchdog 轮询虚拟键状态，按下立刻停。

设计要点：

* **轮询而不是注册热键**：``RegisterHotKey`` 需要消息循环且会与宿主抢键，
  轮询 ``GetAsyncKeyState`` 只读状态、零副作用，出错也不影响系统。
* **触发后的释放**由注入器负责（:class:`minicode.guard.injector.GuardedInjector`
  记着自己按下了哪些键/键钮，急停一触发就把它们全部松开）——
  watchdog 本身不碰输入通道（**只读状态**，职责单一）。
* 轮询函数是**注入的**：单测传 fake，真机接线传
  :func:`minicode.guard.win32.async_key_state`。

默认热键 **Ctrl + Alt + Shift + K**（``GuardPolicy.estop_vkeys``）。
"""

from __future__ import annotations

import threading
from collections.abc import Callable

__all__ = ["EstopWatchdog"]


class EstopWatchdog:
    """急停热键 watchdog（后台守护线程轮询键状态）。

    用法::

        watchdog = EstopWatchdog(key_state_fn=..., vkeys=(0x11, 0x12, 0x10, 0x4B))
        watchdog.start()
        try:
            ...  # 真机运行；每步动作前查 watchdog.triggered
        finally:
            watchdog.stop()

    :param key_state_fn: 单键状态查询（真机 = ``GetAsyncKeyState``）。
    :param vkeys: 组合键的虚拟键码集合（**全部按下**才算触发）。
    :param interval_s: 轮询间隔（默认 50 ms）。
    :param on_trigger: 触发回调（可选；watchdog 自己只置位事件）。
    """

    def __init__(
        self,
        *,
        key_state_fn: Callable[[int], bool],
        vkeys: tuple[int, ...],
        interval_s: float = 0.05,
        on_trigger: Callable[[], None] | None = None,
    ):
        self._key_state_fn = key_state_fn
        self._vkeys = tuple(vkeys)
        self._interval_s = interval_s
        self._on_trigger = on_trigger
        self._event = threading.Event()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    @property
    def triggered(self) -> bool:
        """急停是否已触发（触发后**永久为真**——急停不可解除，重开进程）。"""
        return self._event.is_set()

    @property
    def vkeys(self) -> tuple[int, ...]:
        """监听的虚拟键码组合（进运行摘要，事后可审计按的是哪组键）。"""
        return self._vkeys

    def _poll_loop(self) -> None:  # pragma: no cover - 线程体，fake 驱动测
        while not self._stop.is_set() and not self._event.is_set():
            if all(self._key_state_fn(vk) for vk in self._vkeys):
                self._event.set()
                if self._on_trigger is not None:
                    try:
                        self._on_trigger()
                    except Exception:  # noqa: BLE001 - 回调失败不吞急停事实
                        pass
                return
            self._stop.wait(self._interval_s)

    def start(self) -> None:
        """启动 watchdog（幂等：已启动就什么都不做）。"""
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._poll_loop, name="minicode-estop", daemon=True
        )
        self._thread.start()

    def stop(self, *, timeout_s: float = 2.0) -> None:
        """停 watchdog（幂等；不 join 已死的线程）。"""
        self._stop.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=timeout_s)
        self._thread = None

    def trigger_for_test(self) -> None:
        """**仅供测试**：直接置位急停（绕过线程，测试不依赖时序）。"""
        self._event.set()

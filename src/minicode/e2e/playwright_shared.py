"""**本轮共享的 Playwright 实例**（e2e 跑批那一侧）。

⚠️ **为什么共享**（本机实测，2026-10-22）：Playwright 的 **sync API** 的
事件循环是**按线程**的 —— 同一个线程里 ``stop()`` 之后再 ``start()`` 会报

* 「Event loop is closed! Is Playwright already stopped?」（第二张票的前置检查）
* 「It looks like you are using Playwright Sync API inside the asyncio loop」
  （跨应用那张票：cc-switch 那半段 stop 之后，浏览器那半段再 start）

⇒ **起一次，整轮共用**：每个执行器开自己的 context / 浏览器，
**整轮跑完**由评测台统一停一次（:func:`stop_shared_playwright`）。
"""

from __future__ import annotations

from typing import Any

__all__ = ["shared_playwright", "stop_shared_playwright"]

#: 本进程共享的那个实例（``None`` = 还没起）。
_INSTANCE: dict[str, Any] = {}


def shared_playwright() -> Any:
    """本进程共享的 Playwright 实例（第一次调用时起；**不要 stop**）。"""
    from playwright.sync_api import sync_playwright

    instance = _INSTANCE.get("instance")
    if instance is None:
        instance = sync_playwright().start()
        _INSTANCE["instance"] = instance
    return instance


def stop_shared_playwright() -> None:
    """把共享的 Playwright 实例停掉（**一轮跑完时**调一次）。"""
    instance = _INSTANCE.pop("instance", None)
    if instance is None:
        return
    try:
        instance.stop()
    except Exception:  # noqa: BLE001 —— 停不掉也不许崩
        pass

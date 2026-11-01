"""**桌面锁扩展**（护栏 8）：写入操作者与意图，acquire/release 成对，异常也释放。

在 :mod:`minicode.lock`（人机互斥锁）上加一层**会话语义**：

* 拿锁时把「操作者是谁、要干什么」写进锁文件（``--note``）——
  半夜看锁文件不用猜这把锁是谁的、在跑什么；
* ``with`` 形态：异常路径**必定**放锁（try/finally 不靠调用方自觉）；
* 未拿到锁（别人持锁）→ 上下文管理器把 ``holds`` 暴露出来，调用方决定
  是跳过还是退出 —— 默认行为是**拒绝动真机**（fail-closed）。
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from minicode.lock import acquire, release

__all__ = ["hold_desktop_lock"]


@contextmanager
def hold_desktop_lock(
    operator: str,
    intent: str,
    *,
    ticket: str = "W2",
) -> Iterator[bool]:
    """持锁上下文：拿锁（带操作者/意图备注）→ 交付「是否持锁」→ 必定放锁。

    用法::

        with hold_desktop_lock("nightly-agent", "W3 真机首跑：记事本输入") as holds:
            if not holds:
                raise SystemExit("拿不到真机占用锁，拒绝动真机")
            ...  # 动真机

    :param operator: 操作者标识（人读，进锁文件）。
    :param intent: 本轮意图（人读，进锁文件）。
    :param ticket: 票号（沿用锁脚本的 ``--ticket``）。
    """
    holds = acquire(ticket, note=f"operator={operator}; intent={intent}")
    try:
        yield holds
    finally:
        if holds:
            release()

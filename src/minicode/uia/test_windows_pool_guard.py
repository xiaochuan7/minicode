"""UIA 查询池的**饱和与熔断**（W7 P2 #9 的红/绿测试）。

挂死的 COM 调用会永久占住 worker；旧实现既不挡池满也不熔断——
8 个线程占满后通道**永久**退化为「每次白等一个超时预算」。
"""

from __future__ import annotations

import threading
import time
import unittest

from minicode.uia.errors import UiaQueryTimeout
from minicode.uia.windows import _PoolGuard


class PoolGuardTest(unittest.TestCase):
    """守卫的语义：占满即拒、连续超时熔断、成功复位。"""

    def test_a_full_pool_fails_immediately(self):
        guard = _PoolGuard(max_workers=2, threshold=3, cooldown_s=30.0,
                           clock=time.monotonic)
        guard.acquire()
        guard.acquire()
        with self.assertRaises(UiaQueryTimeout) as ctx:
            guard.acquire()
        self.assertIn("已满", str(ctx.exception))

    def test_consecutive_timeouts_open_the_breaker(self):
        now = [1000.0]
        guard = _PoolGuard(max_workers=8, threshold=3, cooldown_s=30.0,
                           clock=lambda: now[0])
        for _ in range(3):
            guard.acquire()
            guard.release(ok=False)
        with self.assertRaises(UiaQueryTimeout) as ctx:
            guard.acquire()
        self.assertIn("熔断中", str(ctx.exception))

    def test_the_breaker_closes_after_the_cooldown(self):
        now = [1000.0]
        guard = _PoolGuard(max_workers=8, threshold=3, cooldown_s=30.0,
                           clock=lambda: now[0])
        for _ in range(3):
            guard.acquire()
            guard.release(ok=False)
        now[0] += 31.0  # 冷却过去
        guard.acquire()  # 不抛
        guard.release(ok=True)

    def test_a_success_resets_the_consecutive_counter(self):
        guard = _PoolGuard(max_workers=8, threshold=3, cooldown_s=30.0,
                           clock=time.monotonic)
        for _ in range(2):
            guard.acquire()
            guard.release(ok=False)
        guard.acquire()
        guard.release(ok=True)
        for _ in range(2):
            guard.acquire()
            guard.release(ok=False)
        guard.acquire()  # 计数被打断过，还不到阈值
        guard.release(ok=True)


class PoolSaturationTest(unittest.TestCase):
    """真 executor 上的饱和路径：占满后下一次**立刻**失败（不等超时预算）。"""

    def test_a_saturated_pool_fails_fast_not_after_the_timeout(self):
        import minicode.uia.windows as w

        stuck = threading.Event()
        release = threading.Event()

        def hang():
            stuck.set()
            release.wait(timeout=15)

        # **经真路径**占满：8 个线程各跑一次长超时的挂死查询
        # （守卫的 in_flight 只统计 _with_deadline 的流量——这正是要测的口径）。
        errors: list[Exception] = []

        def fill():
            try:
                w._with_deadline(hang, 30.0)
            except UiaQueryTimeout as exc:  # 被熔断/池满拒掉也算占住失败
                errors.append(exc)

        threads = [threading.Thread(target=fill, daemon=True) for _ in range(w._EXECUTOR._max_workers)]
        for thread in threads:
            thread.start()
        try:
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline and w._POOL_GUARD.in_flight < w._EXECUTOR._max_workers:
                time.sleep(0.02)
            self.assertEqual(w._POOL_GUARD.in_flight, w._EXECUTOR._max_workers)
            started = time.monotonic()
            with self.assertRaises(UiaQueryTimeout) as ctx:
                w._with_deadline(lambda: 1, 5.0)
            elapsed = time.monotonic() - started
            self.assertLess(elapsed, 1.0, "池满必须立刻失败，不是等满超时预算")
            self.assertIn("已满", str(ctx.exception))
        finally:
            release.set()
            for thread in threads:
                thread.join(timeout=15)
            # 池子回到空位（别的测试还要用）。
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline and w._POOL_GUARD.in_flight:
                time.sleep(0.05)


if __name__ == "__main__":
    unittest.main()

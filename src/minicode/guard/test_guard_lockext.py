"""护栏 8（桌面锁扩展：操作者/意图入锁文件，异常也释放）的测试。

⚠️ 本文件碰**真实锁文件**（``.scratch/desktop.lock``）——它已 gitignore；
测试保证 acquire/release 成对，测完锁一定是释放状态。若测试进程被硬杀，
锁会在 30 分钟后被脚本按「残留」抢占，不会卡死后续真机票。
"""

import json
import subprocess
import sys
import unittest

from minicode.guard.lockext import hold_desktop_lock
from minicode.lock import LOCK_SCRIPT, lock_path


def _lock_payload() -> dict | None:
    try:
        raw = lock_path().read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    return json.loads(raw)


def _release_quietly() -> None:
    subprocess.run(
        [sys.executable, str(LOCK_SCRIPT), "release"],
        capture_output=True,
        text=True,
    )


class DesktopLockNoteTest(unittest.TestCase):
    """护栏 8：拿锁带备注 → 锁文件里有操作者与意图；释放成对。"""

    def setUp(self) -> None:
        _release_quietly()

    def tearDown(self) -> None:
        _release_quietly()

    def test_the_lock_file_records_operator_and_intent(self):
        payload = _lock_payload()
        self.assertIsNone(payload, "测试前提：锁是空闲的")
        with hold_desktop_lock("nightly-agent", "W2 测试：锁备注") as holds:
            self.assertTrue(holds)
            payload = _lock_payload()
            self.assertIsNotNone(payload)
            self.assertEqual(payload.get("owner"), "agent")
            self.assertIn("nightly-agent", payload.get("note", ""))
            self.assertIn("W2 测试：锁备注", payload.get("note", ""))
        self.assertIsNone(_lock_payload(), "with 块正常退出 → 锁已释放")

    def test_an_exception_inside_the_block_still_releases_the_lock(self):
        try:
            with hold_desktop_lock("nightly-agent", "W2 测试：异常路径"):
                raise RuntimeError("模拟真机运行中途崩溃")
        except RuntimeError:
            pass
        self.assertIsNone(_lock_payload(), "异常退出也必须释放（护栏 8）")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

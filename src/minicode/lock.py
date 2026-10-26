"""**真机占用锁**的调用封装（人机互斥：人类在用电脑时 agent 不得动真机）。

判定规则只有一份，在 ``scripts/desktop_lock.py`` 里（它是实现）；本模块是它的
**调用封装**：拿锁、放锁、以及「**我是不是持锁者**」。

> **为什么要有第三处**：以前三个命令行各自判「放不放锁」，判法不一样 ——
> 「锁文件原先是否存在」判不出**抢占**的情形（脚本抢占上次崩溃残留的锁时会
> **重写**锁文件，这时本进程**确实持锁**，但旧判法会说「不是我的，不放」，
> 锁就残留在盘上 30 分钟）。本模块改成**拿锁前后读锁文件比一遍**。

用法::

    from minicode.lock import acquire, release

    holds = acquire("M9")
    try:
        ...  # 动真机
    finally:
        if holds:
            release()
"""

from __future__ import annotations

import json
import locale
import subprocess
import sys
from pathlib import Path

__all__ = ["acquire", "release", "lock_path", "LOCK_SCRIPT"]

#: 仓库根目录（锁脚本与 ``.scratch/`` 都在它下面）。
REPO_ROOT = Path(__file__).resolve().parents[2]

#: 真机占用锁的**唯一**实现（判定规则只有这一份）。
LOCK_SCRIPT = REPO_ROOT / "scripts" / "desktop_lock.py"


def lock_path() -> Path:
    """锁文件的位置（``.scratch/desktop.lock``）。"""
    return REPO_ROOT / ".scratch" / "desktop.lock"


def _read() -> str | None:
    """锁文件的原始内容（读不到 = 没有锁）。"""
    try:
        return lock_path().read_text(encoding="utf-8")
    except (FileNotFoundError, OSError):
        return None


def acquire(ticket: str) -> bool:
    """拿锁。返回 ``True`` 表示**本进程是持锁者**（用完必须 :func:`release`）。

    人类占用真机时脚本以退出码 3 拒绝 —— 本函数据此**直接退出**
    （拒绝动真机；判定规则见 ``scripts/desktop_lock.py``）。
    """
    before = _read()
    encoding = locale.getpreferredencoding() or "utf-8"
    result = subprocess.run(
        [sys.executable, str(LOCK_SCRIPT), "acquire", "--ticket", ticket],
        capture_output=True,
        text=True,
        encoding=encoding,
        errors="replace",
    )
    if result.returncode != 0:
        raise SystemExit(
            (result.stderr or result.stdout).strip()
            or "拒绝动真机：拿不到真机占用锁"
        )
    print(result.stdout.strip())
    after = _read()
    if before is None:
        # 本进程创建的锁。
        return True
    # 脚本抢占残留锁时**会重写**锁文件 → 内容变了 = 本进程现在持锁。
    return after != before


def release() -> None:
    """放锁（只放 agent 的锁；脚本自己判断归属）。"""
    encoding = locale.getpreferredencoding() or "utf-8"
    subprocess.run(
        [sys.executable, str(LOCK_SCRIPT), "release"],
        capture_output=True,
        text=True,
        encoding=encoding,
        errors="replace",
    )


def status() -> str:
    """锁的当前状态（人读的一行）。"""
    content = _read()
    if content is None:
        return "free"
    try:
        return json.dumps(json.loads(content), ensure_ascii=False)
    except json.JSONDecodeError:
        return content

"""真机占用锁（人机互斥状态位）。

本项目唯一的人机互斥约定：任何要动真机的动作（真机截图 / 真机点击 / 真机读控件树 /
真机跑端到端任务）之前必须先拿锁，做完必须释放。

锁文件固定在 ``.scratch/desktop.lock``（``.scratch/`` 已 gitignore，锁文件永远不进 git），
内容是一行 JSON：``{"owner": "agent" | "human", "ticket": "M<N>", "since": "<ISO8601>"}``。

用法（仓库根目录）::

    python scripts/desktop_lock.py status                  # 看当前占用
    python scripts/desktop_lock.py acquire --ticket M3     # 动真机前：拿锁
    python scripts/desktop_lock.py release                 # 做完：释放（只放 agent 自己的）
    python scripts/desktop_lock.py human                   # 人类要用电脑：占锁
    python scripts/desktop_lock.py release --owner human   # 人类用完：释放

判定规则就是本脚本的实现（真机占用锁：人机互斥 —— 人类要用电脑时 agent 不得动真机）。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

LOCK_PATH = Path(__file__).resolve().parent.parent / ".scratch" / "desktop.lock"

#: agent 持有的锁超过这个时长就当作上次崩溃残留，允许抢占。
STALE_AFTER = timedelta(minutes=30)

#: 人类占用真机时，agent 拿锁的退出码。
EXIT_HUMAN_OWNS = 3


def _now() -> datetime:
    return datetime.now(UTC).astimezone()


def _stamp() -> str:
    return _now().isoformat(timespec="seconds")


def _read() -> dict | None:
    """读锁；文件不存在或内容坏掉都当作「没有锁」。"""
    try:
        raw = LOCK_PATH.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _write_atomic(payload: dict) -> None:
    """先写临时文件再原子改名，避免别的进程读到半个文件。"""
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = LOCK_PATH.with_name(LOCK_PATH.name + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, LOCK_PATH)


def _age(data: dict) -> timedelta | None:
    raw = data.get("since")
    if not isinstance(raw, str):
        return None
    try:
        since = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if since.tzinfo is None:
        since = since.astimezone()
    return _now() - since


def cmd_status(_args: argparse.Namespace) -> int:
    data = _read()
    if data is None:
        print(f"free  {LOCK_PATH}")
        return 0
    age = _age(data)
    age_text = "未知" if age is None else f"{int(age.total_seconds() // 60)} 分钟前"
    print(f"held  {json.dumps(data, ensure_ascii=False)}  （{age_text}）")
    return 0


def cmd_acquire(args: argparse.Namespace) -> int:
    data = _read()
    if data is None:
        _write_atomic({"owner": "agent", "ticket": args.ticket, "since": _stamp()})
        print(f"acquired  {LOCK_PATH}  （本轮票号 {args.ticket}）")
        return 0

    owner = data.get("owner")
    if owner == "human":
        print(
            "拒绝：人类占用真机（owner=human）。本轮禁止动真机 —— "
            "跳过所有真机票，改做纯软件票，并在报告里写明「因人类占用真机而跳过」。",
            file=sys.stderr,
        )
        return EXIT_HUMAN_OWNS

    if owner == "agent":
        age = _age(data)
        if age is not None and age < STALE_AFTER:
            minutes = int(age.total_seconds() // 60)
            print(f"held by agent  （{minutes} 分钟前，视为本进程自己在用）")
            return 0
        _write_atomic({"owner": "agent", "ticket": args.ticket, "since": _stamp()})
        print(f"preempted  抢占上次崩溃残留的 agent 锁（本轮票号 {args.ticket}）")
        return 0

    print(f"拒绝：锁文件内容无法识别（{json.dumps(data, ensure_ascii=False)}）", file=sys.stderr)
    return 1


def cmd_release(args: argparse.Namespace) -> int:
    data = _read()
    if data is None:
        print(f"free  没有锁可放（{LOCK_PATH}）")
        return 0
    owner = data.get("owner")
    if owner != args.owner:
        print(
            f"拒绝：锁属于 {owner!r}，本次只放 {args.owner!r} 的锁。",
            file=sys.stderr,
        )
        return EXIT_HUMAN_OWNS
    LOCK_PATH.unlink(missing_ok=True)
    print(f"released  {LOCK_PATH}")
    return 0


def cmd_human(_args: argparse.Namespace) -> int:
    _write_atomic({"owner": "human", "since": _stamp()})
    print(f"acquired  {LOCK_PATH}  （人类占用；用完执行 release --owner human）")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="真机占用锁（人机互斥状态位）")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("status", help="看当前占用").set_defaults(func=cmd_status)

    acquire = sub.add_parser("acquire", help="动真机前拿锁")
    acquire.add_argument("--ticket", required=True, help="本轮票号，如 M3")
    acquire.set_defaults(func=cmd_acquire)

    release = sub.add_parser("release", help="释放锁")
    release.add_argument("--owner", choices=("agent", "human"), default="agent")
    release.set_defaults(func=cmd_release)

    sub.add_parser("human", help="人类占用真机").set_defaults(func=cmd_human)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())

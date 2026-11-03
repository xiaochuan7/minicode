"""等桌面持续空闲（>60s）再执行任务命令（真机段的让位友好包装）。

用法：python scripts/wait_idle_then_run.py --min-idle-ms 60000 --timeout-s 3600 -- <命令...>
用户一直活跃到超时 → 退出码 3（如实报告「未跑」）。
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "src"))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from minicode.guard.win32 import last_input_tick, now_tick  # noqa: E402


def idle_ms() -> float:
    return (now_tick() - last_input_tick()) % 0x1_0000_0000


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--min-idle-ms", type=float, default=60000.0)
    parser.add_argument("--timeout-s", type=float, default=3600.0)
    parser.add_argument("command", nargs="+")
    args = parser.parse_args()

    started = time.monotonic()
    while True:
        current = idle_ms()
        if current >= args.min_idle_ms:
            print(f"=== 桌面空闲 {current / 1000:.0f}s ≥ {args.min_idle_ms / 1000:.0f}s，启动 ===", flush=True)
            break
        if time.monotonic() - started > args.timeout_s:
            print(f"=== 等待空闲超时（{args.timeout_s:.0f}s），放弃本轮（未跑） ===", flush=True)
            return 3
        time.sleep(10)
    return subprocess.call(args.command)


if __name__ == "__main__":
    raise SystemExit(main())

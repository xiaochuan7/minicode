"""time.sleep 是否被吃掉：分阶段 import 对照（临时，跑完删）。"""
from __future__ import annotations

import sys
import time

sys.path.insert(0, "src")
sys.path.insert(0, "scripts")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def measure(tag: str) -> None:
    t0 = time.monotonic()
    time.sleep(1.0)
    t1 = time.monotonic()
    print(f"{tag}: sleep(1.0) 实测 {(t1 - t0):.3f}s  monotonic={t1:.3f}")


measure("baseline")

import minicode.guard.win32  # noqa: E402,F401

measure("after guard.win32")

import minicode.exec.dpi  # noqa: E402,F401

measure("after exec.dpi")

import minicode.exec.input  # noqa: E402,F401

measure("after exec.input")

from minicode.exec import uia  # noqa: E402,F401

measure("after exec.uia")

from minicode.uia.desktop import build_desktop  # noqa: E402,F401

measure("after uia.desktop")

from minicode.serve.real import build_real_service  # noqa: E402,F401

measure("after serve.real")

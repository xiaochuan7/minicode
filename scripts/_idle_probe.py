"""空闲 tick 采样（临时，跑完删）。"""
from __future__ import annotations

import ctypes
import sys
import time

sys.path.insert(0, "src")
from minicode.guard.win32 import last_input_tick, now_tick  # noqa: E402

sys.stdout.reconfigure(encoding="utf-8", errors="replace")


class _LastInputInfo(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]


for _ in range(10):
    info = _LastInputInfo()
    info.cbSize = ctypes.sizeof(_LastInputInfo)
    ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info))
    raw = info.dwTime
    ours = last_input_tick()
    idle = (now_tick() - ours) % 0x1_0000_0000
    print(f"raw={raw} ours={ours} idle={idle:.0f}")
    time.sleep(0.7)

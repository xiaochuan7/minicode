"""W3 急停热键真机自检（护栏 1 的真机验证）。

流程：启动真 watchdog（轮询真实 ``GetAsyncKeyState``）→ 用 ``SendInput``
合成按下 Ctrl+Alt+Shift+K → watchdog 必须在 2 秒内置位 → 合成松开。

⚠️ 合成的组合键没有系统语义（不是任何热键），只被本进程的 watchdog 看到；
键按下后**必定**松开（finally）。这一步不过关，后面所有真机步骤都不做。

用法（仓库根目录）::

    python scripts/w3_estop_selftest.py runs/w3/estop-selftest.json
"""

from __future__ import annotations

import ctypes
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from minicode.guard.estop import EstopWatchdog  # noqa: E402
from minicode.guard.win32 import async_key_state, now_tick  # noqa: E402

# Ctrl+Alt+Shift+K 的虚拟键码（与 GuardPolicy.estop_vkeys 一致）。
VK_CTRL, VK_MENU, VK_SHIFT, VK_K = 0x11, 0x12, 0x10, 0x4B


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", ctypes.c_ushort),
        ("wScan", ctypes.c_ushort),
        ("dwFlags", ctypes.c_uint),
        ("time", ctypes.c_uint),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    ]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("ki", KEYBDINPUT), ("padding", ctypes.c_byte * 32)]


class INPUT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [("type", ctypes.c_uint), ("u", _INPUTUNION)]


_USER32 = ctypes.windll.user32


def _key_event(vk: int, up: bool) -> None:
    inp = INPUT()
    inp.type = 1  # INPUT_KEYBOARD
    inp.ki = KEYBDINPUT(
        wVk=vk,
        wScan=0,
        dwFlags=2 if up else 0,  # KEYEVENTF_KEYUP
        time=0,
        dwExtraInfo=None,
    )
    sent = _USER32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))
    if sent != 1:
        raise OSError(f"SendInput 失败（vk={vk:#x}, up={up}）")


def main(out_path: str) -> int:
    vkeys = (VK_CTRL, VK_MENU, VK_SHIFT, VK_K)
    watchdog = EstopWatchdog(key_state_fn=async_key_state, vkeys=vkeys)
    result: dict = {"vkeys": list(vkeys), "estop_triggered": False}
    try:
        watchdog.start()
        # 合成按下（顺序：修饰键在前）。
        for vk in vkeys:
            _key_event(vk, up=False)
            time.sleep(0.01)
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            if watchdog.triggered:
                break
            time.sleep(0.02)
        result["estop_triggered"] = watchdog.triggered
        result["trigger_within_s"] = round(
            max(0.0, 2.0 - (deadline - time.monotonic())), 3
        )
        result["now_tick"] = now_tick()
    finally:
        # 必定松开（逆序）。
        for vk in reversed(vkeys):
            _key_event(vk, up=True)
            time.sleep(0.01)
        watchdog.stop()
    passed = result["estop_triggered"]
    result["pass"] = passed
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1] if len(sys.argv) > 1 else "runs/w3/estop-selftest.json"))

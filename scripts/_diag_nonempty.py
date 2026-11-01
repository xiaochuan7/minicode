"""f02 之谜对照：非空文件 vs 空文件，type 落点与 Ctrl+S（临时，跑完删）。"""
from __future__ import annotations

import ctypes
import subprocess
import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "src"))
sys.path.insert(0, str(_ROOT / "scripts"))

import os  # noqa: E402

os.environ.setdefault("MINICODE_TYPE_PER_CHAR_DELAY", "0.05")

from w3_probe import find_new_window, visible_windows  # noqa: E402

from minicode.action.protocol import Action, ActionKind  # noqa: E402
from minicode.action.records import ListTraceSink  # noqa: E402
from minicode.guard.lockext import hold_desktop_lock  # noqa: E402
from minicode.uia.desktop import build_desktop  # noqa: E402
from minicode.uia.observer import UiaStateObserver  # noqa: E402
from minicode.uia.ports import UiaFindRequest  # noqa: E402

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
USER32 = ctypes.windll.user32
WM_CLOSE = 0x0010


def title_of(hwnd: int) -> str:
    buf = ctypes.create_unicode_buffer(512)
    USER32.GetWindowTextW(hwnd, buf, 512)
    return buf.value


def probe(tag: str, name: str, base: str) -> None:
    tmp = _ROOT / "runs" / "w4" / "tmp"
    tmp.mkdir(parents=True, exist_ok=True)
    path = tmp / name
    path.write_text(base, encoding="utf-8")

    before = {w[0] for w in visible_windows(("Notepad",))}
    proc = subprocess.Popen(["notepad.exe", str(path)])
    found = find_new_window(before, class_filter=("Notepad",))
    if found is None:
        print(f"[{tag}] !! 没等到窗口")
        proc.terminate()
        return
    hwnd = found[0]
    frag = name
    deadline = time.monotonic() + 8.0
    while frag not in title_of(hwnd):
        if time.monotonic() > deadline:
            print(f"[{tag}] !! 标题没等到：{title_of(hwnd)!r}")
            USER32.PostMessageW(hwnd, WM_CLOSE, 0, 0)
            return
        time.sleep(0.3)
    time.sleep(1.0)
    desktop = build_desktop(hwnd=hwnd)
    seam = desktop.new_seam(ListTraceSink())
    fg_ok = USER32.GetForegroundWindow() == hwnd
    seam.executor.execute(
        Action(kind=ActionKind.TYPE, params={"text": "探针追加XYZ"}, intent=""))
    time.sleep(0.5)
    obs = UiaStateObserver(desktop.port, desktop.anchor)
    rows = obs.query.find(UiaFindRequest(anchor=desktop.anchor, control_type="Document"))
    docs = [getattr(el, "value", "") for el in rows.elements]
    seam.executor.execute(Action(kind=ActionKind.KEY, params={"combo": ("ctrl", "s")}, intent=""))
    time.sleep(1.2)
    print(f"[{tag}] fg_ok={fg_ok} 标题={title_of(hwnd)!r}")
    print(f"[{tag}] docs={docs}")
    print(f"[{tag}] 磁盘={path.read_text(encoding='utf-8')!r}")
    USER32.PostMessageW(hwnd, WM_CLOSE, 0, 0)
    time.sleep(1.0)
    print(f"[{tag}] 关闭 IsWindow={USER32.IsWindow(hwnd)}")


def main() -> int:
    with hold_desktop_lock("nightly-diag", "W4 诊断：非空文件 type 落点") as holds:
        if not holds:
            print("!! 拿不到桌面锁")
            return 2
        probe("非空", "probe-nonempty.txt", "基础内容一行\n")
        probe("空", "probe-empty.txt", "")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

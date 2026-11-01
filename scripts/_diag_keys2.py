"""W4 干净环境 ctrl+s 决定性实验（临时脚本，跑完删）。"""
from __future__ import annotations

import ctypes
import os
import subprocess
import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "src"))
sys.path.insert(0, str(_ROOT / "scripts"))

os.environ.setdefault("MINICODE_TYPE_PER_CHAR_DELAY", "0.05")

from w3_probe import find_new_window, visible_windows  # noqa: E402

from minicode.action.protocol import Action, ActionKind  # noqa: E402
from minicode.action.records import ListTraceSink  # noqa: E402
from minicode.exec import input as win_input  # noqa: E402
from minicode.guard.lockext import hold_desktop_lock  # noqa: E402
from minicode.uia.desktop import build_desktop  # noqa: E402
from minicode.uia.observer import UiaStateObserver  # noqa: E402
from minicode.uia.ports import UiaFindRequest  # noqa: E402

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

USER32 = ctypes.windll.user32
WM_CLOSE = 0x0010
VK_CONTROL, VK_S, VK_A, VK_DELETE = 0x11, 0x53, 0x41, 0x2E


def wtext(hwnd: int) -> str:
    buf = ctypes.create_unicode_buffer(512)
    USER32.GetWindowTextW(hwnd, buf, 512)
    return buf.value


def doc_values(desktop) -> list[str]:
    observer = UiaStateObserver(desktop.port, desktop.anchor)
    result = observer.query.find(
        UiaFindRequest(anchor=desktop.anchor, control_type="Document"))
    return [getattr(el, "value", "") for el in result.elements]


def main() -> int:
    tmp = _ROOT / "runs" / "w4" / "tmp"
    tmp.mkdir(parents=True, exist_ok=True)
    diag = tmp / "diag2.txt"
    diag.write_text("v2 base\n", encoding="utf-8")

    with hold_desktop_lock("nightly-diag", "W4 诊断：干净环境 ctrl+s") as holds:
        if not holds:
            print("!! 拿不到桌面锁")
            return 2
        cfg_classes = ("Notepad",)
        before = {w[0] for w in visible_windows(cfg_classes)}
        proc = subprocess.Popen(["notepad.exe", str(diag)])
        found = find_new_window(before, class_filter=cfg_classes)
        if found is None:
            print("!! 没等到窗口")
            proc.terminate()
            return 1
        hwnd, _cls, _t = found
        time.sleep(1.5)
        desktop = build_desktop(hwnd=hwnd)
        seam = desktop.new_seam(ListTraceSink())

        print(f"spawn hwnd={hwnd} 标题={wtext(hwnd)!r} fg_ours={USER32.GetForegroundWindow() == hwnd}")

        print("== 1) type 'T1' → 读回（期望 T1 在值里）")
        seam.executor.execute(Action(kind=ActionKind.TYPE, params={"text": "T1"}, intent=""))
        time.sleep(0.4)
        print(f"   标题={wtext(hwnd)!r} docs={doc_values(desktop)}")

        print("== 2) 扫描码 ctrl+s → 磁盘/标题")
        win_input.press_scancode(win_input.scancode_of(VK_S), (VK_CONTROL,))
        time.sleep(1.2)
        print(f"   磁盘={diag.read_text(encoding='utf-8')!r} 标题={wtext(hwnd)!r}")

        print("== 3) 再 type 'T2' → VK hotkey ctrl+s → 磁盘/标题")
        seam.executor.execute(Action(kind=ActionKind.TYPE, params={"text": "T2"}, intent=""))
        time.sleep(0.4)
        win_input.hotkey(VK_CONTROL, VK_S)
        time.sleep(1.2)
        print(f"   磁盘={diag.read_text(encoding='utf-8')!r} 标题={wtext(hwnd)!r}")

        print("== 4) undo 路径：扫描码 ctrl+a → Delete → 读回（期望空）")
        win_input.press_scancode(win_input.scancode_of(VK_A), (VK_CONTROL,))
        time.sleep(0.3)
        win_input.press_scancode(win_input.scancode_of(VK_DELETE), ())
        time.sleep(0.4)
        print(f"   docs={doc_values(desktop)} 标题={wtext(hwnd)!r}")

        print("== 5) 干净关闭 → WM_CLOSE → 应无对话框无新备份")
        USER32.PostMessageW(hwnd, WM_CLOSE, 0, 0)
        time.sleep(1.5)
        print(f"   IsWindow={USER32.IsWindow(hwnd)} 磁盘={diag.read_text(encoding='utf-8')!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

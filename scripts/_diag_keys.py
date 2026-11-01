# -*- coding: utf-8 -*-
"""W4 键盘通道对照实验（临时脚本，跑完删）。

验证：VK(scan=0) 注入 vs 扫描码注入 vs Unicode 控制字符，在 Win11 记事本上
哪些真的生效。每步都用 UIA 读回文档值 + 磁盘 + 标题三方印证。
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes
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
from minicode.guard.lockext import hold_desktop_lock  # noqa: E402
from minicode.exec import input as win_input  # noqa: E402
from minicode.uia.desktop import build_desktop  # noqa: E402
from minicode.uia.observer import UiaStateObserver  # noqa: E402
from minicode.uia.ports import UiaFindRequest  # noqa: E402

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

USER32 = ctypes.windll.user32
WM_CLOSE = 0x0010
VK_CONTROL, VK_RETURN, VK_TAB = 0x11, 0x0D, 0x09


def wtext(hwnd: int) -> str:
    buf = ctypes.create_unicode_buffer(512)
    USER32.GetWindowTextW(hwnd, buf, 512)
    return buf.value


def doc_value(desktop) -> str:
    observer = UiaStateObserver(desktop.port, desktop.anchor)
    result = observer.query.find(
        UiaFindRequest(anchor=desktop.anchor, control_type="Document"))
    return "".join(getattr(el, "value", "") for el in result.elements)


def key_vk(desktop, combo: tuple[str, ...]) -> None:
    """产品现路径：hotkey（VK scan=0）。"""
    seam = desktop.new_seam(ListTraceSink())
    outcome = seam.executor.execute(
        Action(kind=ActionKind.KEY, params={"combo": combo}, intent="诊断"))
    print(f"    [vk {combo} ok={outcome.ok}]")


def key_scan(mods: tuple[int, ...], vk: int) -> None:
    """扫描码路径：press_scancode（KEYEVENTF_SCANCODE）。"""
    win_input.press_scancode(win_input.scancode_of(vk), mods)
    print(f"    [scan mod={mods} vk={vk:#x} sent]")


def main() -> int:
    tmp = _ROOT / "runs" / "w4" / "tmp"
    tmp.mkdir(parents=True, exist_ok=True)
    diag = tmp / "diag-keys.txt"
    diag.write_text("base\n", encoding="utf-8")

    with hold_desktop_lock("nightly-diag", "W4 诊断：键盘通道对照") as holds:
        if not holds:
            print("!! 拿不到桌面锁")
            return 2
        cfg_classes = ("Notepad", "ApplicationFrameWindow")
        before = {w[0] for w in visible_windows(cfg_classes)}
        proc = subprocess.Popen(["notepad.exe", str(diag)])
        found = find_new_window(before, class_filter=cfg_classes)
        if found is None:
            print("!! 没等到窗口")
            proc.terminate()
            return 1
        hwnd, _cls, _t = found
        time.sleep(1.5)
        print(f"spawn hwnd={hwnd} title={wtext(hwnd)!r} fg_ours={USER32.GetForegroundWindow() == hwnd}")
        desktop = build_desktop(hwnd=hwnd)
        seam = desktop.new_seam(ListTraceSink())

        print("== 1) type_unicode 'T1' → 读回")
        seam.executor.execute(Action(kind=ActionKind.TYPE, params={"text": "T1"}, intent=""))
        time.sleep(0.5)
        v = doc_value(desktop)
        print(f"   doc={v!r}  含T1={'T1' in v}")

        print("== 2) ctrl+s 走 VK hotkey → 磁盘/标题")
        key_vk(desktop, ("ctrl", "s"))
        time.sleep(1.2)
        print(f"   磁盘={diag.read_text(encoding='utf-8')!r} 标题={wtext(hwnd)!r}")

        print("== 3) ctrl+s 走扫描码 → 磁盘/标题")
        key_scan((VK_CONTROL,), 0x53)
        time.sleep(1.2)
        print(f"   磁盘={diag.read_text(encoding='utf-8')!r} 标题={wtext(hwnd)!r}")

        print("== 4) type_unicode 'X\\r\\nY'（Unicode 控制字符换行？）")
        seam.executor.execute(Action(kind=ActionKind.TYPE, params={"text": "X\r\nY"}, intent=""))
        time.sleep(0.5)
        v = doc_value(desktop)
        print(f"   doc={v!r}  行数={v.count(chr(10))}")

        print("== 5) 回车走扫描码 → 行数")
        key_scan((), VK_RETURN)
        time.sleep(0.5)
        v = doc_value(desktop)
        print(f"   doc={v!r}  行数={v.count(chr(10))}")

        print("== 6) ctrl+end 走扫描码 → 再 type 'Z' → 读回尾部")
        key_scan((VK_CONTROL,), 0x23)
        time.sleep(0.3)
        seam.executor.execute(Action(kind=ActionKind.TYPE, params={"text": "Z"}, intent=""))
        time.sleep(0.5)
        print(f"   doc={doc_value(desktop)!r}")

        print("== 7) Tab 走扫描码 → 值里出现 \\t？")
        key_scan((), VK_TAB)
        time.sleep(0.4)
        v = doc_value(desktop)
        print(f"   doc={v!r}  含\\t={chr(9) in v}")

        print("== 8) 关闭：WM_CLOSE → 有无对话框（文档已修改）")
        USER32.PostMessageW(hwnd, WM_CLOSE, 0, 0)
        time.sleep(1.5)
        alive = USER32.IsWindow(hwnd)
        print(f"   IsWindow={alive} 标题={wtext(hwnd)!r}")
        if alive:
            try:
                cands = UiaStateObserver(desktop.port, desktop.anchor).observe("不保存")
                names = [(c.name, c.rect, c.window_handle) for c in cands]
                print(f"   候选: {names}")
                exact = [c for c in cands if c.name == "不保存" and c.rect != (0, 0, 0, 0)]
                if exact:
                    r = exact[0].rect
                    cx, cy = (r[0] + r[2]) // 2, (r[1] + r[3]) // 2
                    out = seam.executor.execute(
                        Action(kind=ActionKind.CLICK, params={"x": cx, "y": cy}, intent="不保存"))
                    print(f"   点不保存 ok={out.ok} landing={out.landing_point}")
                    time.sleep(1.0)
                    print(f"   IsWindow after={USER32.IsWindow(hwnd)}")
            except Exception as exc:  # noqa: BLE001
                print("   observe 异常:", type(exc).__name__, exc)
        print("== 磁盘终值:", repr(diag.read_text(encoding="utf-8")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

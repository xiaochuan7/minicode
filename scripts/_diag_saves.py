"""W4 一次跑清 5 个诊断问题（临时脚本，跑完删）。

Q1 现在桌面上有哪些 notepad/calc 残留；空标题 ApplicationFrameWindow 是谁的。
Q2 last_input_tick 此刻是否在持续推进（判「连续 paused」是真人还是自触发）。
Q3 带 file 参数 spawn 的 notepad 是否拿到前台（GetForegroundWindow == 自己）。
Q4 ctrl+end → type → ctrl+s 是否真落盘（标题 * 消失 + 磁盘内容）。
Q5 WM_CLOSE 后保存对话框的「不保存」按钮：观察端口能不能看到、rect 是什么、
   点下去窗口关不关。
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
from minicode.guard.win32 import last_input_tick, now_tick  # noqa: E402
from minicode.uia.desktop import build_desktop  # noqa: E402
from minicode.uia.observer import UiaStateObserver  # noqa: E402

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

USER32 = ctypes.windll.user32
WM_CLOSE = 0x0010


def wtext(hwnd: int) -> str:
    buf = ctypes.create_unicode_buffer(512)
    USER32.GetWindowTextW(hwnd, buf, 512)
    return buf.value


def wclass(hwnd: int) -> str:
    buf = ctypes.create_unicode_buffer(256)
    USER32.GetClassNameW(hwnd, buf, 256)
    return buf.value


def pid_of(hwnd: int) -> int:
    pid = ctypes.wintypes.DWORD()
    USER32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value


def rect_of(hwnd: int) -> tuple:
    rect = ctypes.wintypes.RECT()
    USER32.GetWindowRect(hwnd, ctypes.byref(rect))
    return (rect.left, rect.top, rect.right, rect.bottom)


def fg() -> int:
    return USER32.GetForegroundWindow()


def main() -> int:
    print("=== Q1 桌面残留 ===")
    mine_pids: set[int] = set()
    for hwnd, cls, _t in visible_windows(("Notepad", "ApplicationFrameWindow")):
        title, pid = wtext(hwnd), pid_of(hwnd)
        print(f"  hwnd={hwnd} class={cls} pid={pid} title={title!r} rect={rect_of(hwnd)}")

    print("=== Q2 空闲采样 6s（500ms 一次）===")
    for _ in range(12):
        idle = (now_tick() - last_input_tick()) % 0x1_0000_0000
        print(f"  idle_ms={idle:8.1f}")
        time.sleep(0.5)

    with hold_desktop_lock("nightly-diag", "W4 诊断：保存/前台/对话框") as holds:
        if not holds:
            print("!! 拿不到桌面锁")
            return 2

        # ---- Q3/Q4：file 参数 spawn + 前台 + 保存 --------------------------
        tmp = _ROOT / "runs" / "w4" / "tmp"
        tmp.mkdir(parents=True, exist_ok=True)
        diag = tmp / "diag-save.txt"
        base = "diag 基础行\n"
        diag.write_text(base, encoding="utf-8")

        cfg_classes = ("Notepad", "ApplicationFrameWindow")
        before = {w[0] for w in visible_windows(cfg_classes)}
        proc = subprocess.Popen(["notepad.exe", str(diag)])
        found = find_new_window(before, class_filter=cfg_classes)
        if found is None:
            print("!! 没等到 diag notepad 窗口")
            proc.terminate()
            return 1
        hwnd, cls, _title = found
        mine_pids.add(pid_of(hwnd))
        print(f"=== Q3 spawn 得 hwnd={hwnd} class={cls} title={wtext(hwnd)!r}")
        for delay in (0.5, 1.0, 1.5, 2.0):
            time.sleep(0.5)
            f = fg()
            print(f"  t+{delay}s foreground={f} ({wclass(f)!r},{wtext(f)!r}) ours={f == hwnd}")

        # 前台不归宿主就显式拉前台（这是要修的 harness 行为，先验证手段有效）。
        if fg() != hwnd:
            print("  → 前台不是自己，尝试 SetForegroundWindow 修正")
            USER32.ShowWindow(hwnd, 9)  # SW_RESTORE
            USER32.SetForegroundWindow(hwnd)
            time.sleep(0.5)
            print(f"  修正后 foreground={fg()} ours={fg() == hwnd}")

        desktop = build_desktop(hwnd=hwnd)
        seam = desktop.new_seam(ListTraceSink())

        outcome = seam.executor.execute(
            Action(kind=ActionKind.KEY, params={"combo": ("ctrl", "end")}, intent="光标到末尾"))
        print("=== Q4 ctrl+end ok=", outcome.ok, outcome.notes)
        outcome = seam.executor.execute(
            Action(kind=ActionKind.TYPE, params={"text": "diag 追加行"}, intent="追加"))
        print("  type ok=", outcome.ok, list(outcome.notes)[:1])
        time.sleep(0.3)
        outcome = seam.executor.execute(
            Action(kind=ActionKind.KEY, params={"combo": ("ctrl", "s")}, intent="保存"))
        print("  ctrl+s ok=", outcome.ok)
        time.sleep(1.2)
        print("  标题（* 应消失）:", repr(wtext(hwnd)))
        print("  磁盘:", repr(diag.read_text(encoding="utf-8")))
        f = fg()
        print("  前台现在:", f, wclass(f), repr(wtext(f)))

        # ---- Q5：关闭 + 对话框诊断 ----------------------------------------
        USER32.PostMessageW(hwnd, WM_CLOSE, 0, 0)
        time.sleep(1.2)
        alive = USER32.IsWindow(hwnd)
        print("=== Q5 WM_CLOSE 后 IsWindow=", alive, " 标题:", repr(wtext(hwnd)))
        if alive:
            observer = UiaStateObserver(desktop.port, desktop.anchor)
            try:
                cands = observer.observe("不保存")
                for c in cands:
                    print(f"  候选 name={c.name!r} rect={c.rect} hwnd={c.window_handle} "
                          f"ctype={c.control_type}")
            except Exception as exc:
                print("  observe 异常:", type(exc).__name__, exc)
            # 也看看对话框到底在哪个窗口：枚举自己进程的顶层窗口。
            @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.wintypes.HWND,
                                ctypes.wintypes.LPARAM)
            def _cb(h, _lp):
                if pid_of(h) == pid_of(hwnd) and USER32.IsWindowVisible(h):
                    print(f"  同进程可见窗口: hwnd={h} class={wclass(h)!r} title={wtext(h)!r}")
                return True
            USER32.EnumWindows(_cb, 0)
            # 精确名 + rect 点击（harness 的路径），点击后验证。
            try:
                cands = observer.observe("不保存")
            except Exception:
                cands = ()
            exact = [c for c in cands if c.name == "不保存" and c.rect != (0, 0, 0, 0)]
            if exact:
                r = exact[0].rect
                cx, cy = (r[0] + r[2]) // 2, (r[1] + r[3]) // 2
                print(f"  点「不保存」@ ({cx},{cy})")
                seam2 = desktop.new_seam(ListTraceSink())
                outcome = seam2.executor.execute(
                    Action(kind=ActionKind.CLICK, params={"x": cx, "y": cy}, intent="不保存"))
                print("  click ok=", outcome.ok, "landing=", outcome.landing_point)
                time.sleep(1.0)
                print("  IsWindow after click =", USER32.IsWindow(hwnd))
            else:
                print("  !! 没拿到可点的「不保存」")
        else:
            print("  （直接关掉了，无对话框——预期外：文件已保存成功？）")

    print("=== 收尾残留检查 ===")
    for hwnd, _cls, _t in visible_windows(("Notepad",)):
        if pid_of(hwnd) in mine_pids or "diag" in wtext(hwnd):
            print(f"  残留: hwnd={hwnd} title={wtext(hwnd)!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

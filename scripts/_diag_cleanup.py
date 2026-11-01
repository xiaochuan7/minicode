"""清理 W4 实验残留 + 查 pid 归属（临时脚本，跑完删）。"""
from __future__ import annotations

import ctypes
import ctypes.wintypes
import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "src"))
sys.path.insert(0, str(_ROOT / "scripts"))

from w3_probe import visible_windows  # noqa: E402

from minicode.action.protocol import Action, ActionKind  # noqa: E402
from minicode.action.records import ListTraceSink  # noqa: E402
from minicode.guard.lockext import hold_desktop_lock  # noqa: E402
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


def exe_of(pid: int) -> str:
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return f"<OpenProcess 失败 err={kernel32.GetLastError()}>"
    buf = ctypes.create_unicode_buffer(512)
    size = ctypes.wintypes.DWORD(512)
    ok = kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size))
    kernel32.CloseHandle(handle)
    return buf.value if ok else "<QueryFullProcessImageName 失败>"


def enum_visible_of_pid(pid: int) -> list[tuple[int, str, str]]:
    out: list[tuple[int, str, str]] = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM)
    def cb(h, _lp):
        if USER32.IsWindowVisible(h) and pid_of(h) == pid:
            out.append((h, wclass(h), wtext(h)))
        return True

    USER32.EnumWindows(cb, 0)
    return out


def close_discard(hwnd: int) -> bool:
    """WM_CLOSE → 「不保存」（root walk 找对话框按钮）→ rect 点击。返回关掉了没。"""
    desktop = build_desktop(hwnd=hwnd)
    seam = desktop.new_seam(ListTraceSink())
    USER32.PostMessageW(hwnd, WM_CLOSE, 0, 0)
    for round_no in range(5):
        time.sleep(1.0)
        if not USER32.IsWindow(hwnd):
            print(f"  round{round_no}: 窗口已关")
            return True
        try:
            cands = UiaStateObserver(desktop.port, desktop.anchor).observe("不保存")
        except Exception as exc:  # noqa: BLE001
            print(f"  round{round_no}: observe 异常 {type(exc).__name__}: {exc}")
            cands = ()
        exact = [c for c in cands if c.name == "不保存" and c.rect != (0, 0, 0, 0)]
        print(f"  round{round_no}: 候选 {len(cands)} 个，可点 {len(exact)} 个")
        for c in exact[:3]:
            print(f"    rect={c.rect} hwnd={c.window_handle} ctype={c.control_type}")
        if exact:
            r = exact[0].rect
            cx, cy = (r[0] + r[2]) // 2, (r[1] + r[3]) // 2
            outcome = seam.executor.execute(
                Action(kind=ActionKind.CLICK, params={"x": cx, "y": cy}, intent="不保存"))
            print(f"    click ok={outcome.ok} landing={outcome.landing_point}")
    return not USER32.IsWindow(hwnd)


def main() -> int:
    print("=== pid 归属 ===")
    for hwnd, cls, _t in visible_windows(("Notepad", "ApplicationFrameWindow")):
        pid = pid_of(hwnd)
        print(f"  hwnd={hwnd} class={cls} pid={pid} title={wtext(hwnd)!r}")
        print(f"    exe={exe_of(pid)}")
        for h, c, t in enum_visible_of_pid(pid):
            print(f"    同进程可见窗口 hwnd={h} class={c!r} title={t!r}")

    with hold_desktop_lock("nightly-diag", "W4 诊断：残留清理") as holds:
        if not holds:
            print("!! 拿不到桌面锁")
            return 2
        for hwnd, _cls, title in visible_windows(("Notepad",)):
            if "f04.txt" in title or "diag" in title or "x25" in title:
                print(f"=== 清理实验残留 hwnd={hwnd} title={title!r}")
                ok = close_discard(hwnd)
                print(f"  清理结果: {'OK' if ok else 'FAIL'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

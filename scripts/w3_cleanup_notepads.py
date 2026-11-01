"""W3 清理：只关闭**自己产生的**已修改记事本（标题含本次实验字符串）。

方法：WM_CLOSE → 等保存对话框 → UIA 定点查「不保存」按钮（**精确名匹配**，
绕开「保存 ⊂ 不保存」的双向包含歧义）→ 按 rect 中心原始点击（cleanup 专用，
不走缝 —— 这是收拾自己现场，不是产品动作）。用户的窗口（657518）绝不碰。
"""

import ctypes
import ctypes.wintypes as wt
import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "src"))

from minicode.action.geometry import VirtualDesktop  # noqa: E402
from minicode.action.windows import WindowsInputInjector  # noqa: E402
from minicode.uia.desktop import build_desktop  # noqa: E402
from minicode.uia.ports import UiaFindRequest  # noqa: E402

USER32 = ctypes.windll.user32
WM_CLOSE = 0x0010

#: 我本次实验产生的标题特征（用户的 *7zip-installer-COPYING.txt 不在集合内）。
_MINE_MARKERS = ("真机首跑", "首跑", "真机", "机机首跑", "minicode W3")


def my_notepads() -> list[tuple[int, str]]:
    buf = ctypes.create_unicode_buffer(512)
    rows = []
    EnumWindowsProc = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)

    def on_window(hwnd, _lp):
        if USER32.IsWindowVisible(hwnd):
            USER32.GetClassNameW(hwnd, buf, 256)
            if buf.value == "Notepad":
                USER32.GetWindowTextW(hwnd, buf, 512)
                title = buf.value
                if title != "*7zip-installer-COPYING.txt - Notepad" and any(
                    marker in title for marker in _MINE_MARKERS
                ):
                    rows.append((int(hwnd), title))
        return True

    USER32.EnumWindows(EnumWindowsProc(on_window), 0)
    return rows


def click_button_exact(hwnd: int, exact_name: str) -> bool:
    """在 hwnd 的 UIA 树里找**名字恰好等于** ``exact_name`` 的按钮并点它。"""
    desktop = build_desktop(hwnd=hwnd)
    result = desktop.port.find(
        UiaFindRequest(anchor=desktop.anchor, name=exact_name)
    )
    targets = [
        el for el in result.elements
        if getattr(el, "name", "") == exact_name
        and getattr(el, "rect", (0, 0, 0, 0)) != (0, 0, 0, 0)
    ]
    if not targets:
        return False
    rect = targets[0].rect
    cx, cy = (rect[0] + rect[2]) // 2, (rect[1] + rect[3]) // 2
    injector = WindowsInputInjector(VirtualDesktop())
    injector.move(cx, cy)
    time.sleep(0.05)
    injector.button_down("left")
    injector.button_up("left")
    return True


def main() -> int:
    closed = []
    for _round_no in range(12):
        mine = my_notepads()
        if not mine:
            break
        hwnd, title = mine[0]
        USER32.PostMessageW(hwnd, WM_CLOSE, 0, 0)
        time.sleep(0.8)
        if not USER32.IsWindow(hwnd):
            closed.append((hwnd, title, "closed-directly"))
            continue
        try:
            clicked = click_button_exact(hwnd, "不保存")
        except Exception as exc:
            closed.append((hwnd, title, f"error:{exc}"))
            continue
        time.sleep(0.8)
        if not USER32.IsWindow(hwnd):
            closed.append((hwnd, title, "closed-after-dont-save"))
        else:
            closed.append((hwnd, title, f"still-open(clicked={clicked})"))
        time.sleep(0.3)
    # 终态报告
    remaining = my_notepads()
    user_window_alive = USER32.IsWindow(657518)
    print("closed:", len(closed))
    for row in closed:
        print("  ", row)
    print("remaining-mine:", remaining)
    print("user_window_657518_alive:", bool(user_window_alive))
    return 0 if not remaining and user_window_alive else 1


if __name__ == "__main__":
    raise SystemExit(main())

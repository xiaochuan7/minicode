"""一次性会话卫生：清掉今晚实验留下的记事本恢复标签（临时脚本，跑完删）。

原则：
* 只处理「内容带本夜实验标记」或「空文档」的标签；标题含 7zip（用户标签）硬排除。
* 清理 = 全选+退格弄空 → Ctrl+W 关标签（空标签关闭不留 TabState 备份）。
* 不匹配的标签一律不动，如实列出。
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes
import subprocess
import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "src"))
sys.path.insert(0, str(_ROOT / "scripts"))

from w3_probe import find_new_window, visible_windows  # noqa: E402

from minicode.action.records import ListTraceSink  # noqa: E402
from minicode.exec.input import press_scancode, scancode_of  # noqa: E402
from minicode.guard.lockext import hold_desktop_lock  # noqa: E402
from minicode.uia.desktop import build_desktop  # noqa: E402
from minicode.uia.observer import UiaStateObserver  # noqa: E402
from minicode.uia.ports import UiaFindRequest  # noqa: E402

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

USER32 = ctypes.windll.user32
WM_CLOSE = 0x0010
VK_CTRL, VK_A, VK_S, VK_W, VK_BACK, VK_TAB = 0x11, 0x41, 0x53, 0x57, 0x08, 0x09

_MARKERS = ("f0", "f1", "c0", "c1", "i1", "i2", "x2", "n2", "diag",
            "追加行", "基础行", "minicode", "T1", "T2")


def title_of(hwnd: int) -> str:
    buf = ctypes.create_unicode_buffer(512)
    USER32.GetWindowTextW(hwnd, buf, 512)
    return buf.value


def docs_of(desktop, hwnd: int) -> list[str]:
    try:
        result = UiaStateObserver(desktop.port, desktop.anchor).query.find(
            UiaFindRequest(anchor=desktop.anchor, control_type="Document"))
    except Exception:  # noqa: BLE001
        return []
    values = []
    for el in result.elements:
        rect = getattr(el, "rect", ())
        if rect and tuple(rect) != (0, 0, 0, 0):
            left, top, right, bottom = (int(v) for v in rect)
            wr = ctypes.wintypes.RECT()
            USER32.GetWindowRect(hwnd, ctypes.byref(wr))
            cx, cy = (left + right) / 2, (top + bottom) / 2
            if not (wr.left <= cx <= wr.right and wr.top <= cy <= wr.bottom):
                continue
        values.append(getattr(el, "value", ""))
    return values


def is_mine(title: str, values: list[str]) -> tuple[bool, str]:
    if "7zip" in title.lower():
        return False, "用户标签，硬排除"
    blob = title + "\n" + "\n".join(values)
    if blob.strip() == "":
        return True, "空标签"
    if any(m in blob for m in _MARKERS):
        return True, "实验标记"
    return False, "无标记"


def clean_and_close_tab(desktop, seam) -> None:
    press_scancode(scancode_of(VK_A), (VK_CTRL,))
    time.sleep(0.25)
    press_scancode(scancode_of(VK_BACK), ())
    time.sleep(0.4)
    press_scancode(scancode_of(VK_W), (VK_CTRL,))
    time.sleep(0.8)


def main() -> int:
    with hold_desktop_lock("nightly-diag", "W4 一次性会话卫生") as holds:
        if not holds:
            print("!! 拿不到桌面锁")
            return 2

        # -- 0) 先收掉还开着的实验窗口（diag2 等）--------------------------
        for hwnd, _cls, title in visible_windows(("Notepad",)):
            if "diag" in title:
                desktop = build_desktop(hwnd=hwnd)
                press_scancode(scancode_of(VK_S), (VK_CTRL,))  # 文件承载，保存即干净
                time.sleep(0.8)
                USER32.PostMessageW(hwnd, WM_CLOSE, 0, 0)
                time.sleep(1.0)
                print(f"预清理: {title!r} 关闭（IsWindow={USER32.IsWindow(hwnd)}）")

        # -- 1) 无参数 spawn（会恢复 TabState 旧标签），逐个判定清理 -------
        cfg_classes = ("Notepad",)
        before = {w[0] for w in visible_windows(cfg_classes)}
        proc = subprocess.Popen(["notepad.exe"])
        found = find_new_window(before, class_filter=cfg_classes)
        if found is None:
            print("没有恢复出窗口（TabState 干净，无需清理）")
            proc.terminate()
            return 0
        hwnd, _cls, _t = found
        time.sleep(1.5)
        desktop = build_desktop(hwnd=hwnd)
        seam = desktop.new_seam(ListTraceSink())
        print(f"恢复窗口 hwnd={hwnd}")
        handled = skipped = 0
        for i in range(12):
            title = title_of(hwnd)
            values = docs_of(desktop, hwnd)
            mine, reason = is_mine(title, values)
            print(f"[{i}] 活动标签 {title!r} docs={values} → {reason}")
            if not mine:
                skipped += 1
                # 切下一个标签；绕一圈没我的就停（用 seen 去重）。
                press_scancode(scancode_of(VK_TAB), (VK_CTRL,))
                time.sleep(0.6)
                if title_of(hwnd) == title:  # 单标签或绕回
                    if skipped > 1:
                        break
                continue
            handled += 1
            clean_and_close_tab(desktop, seam)
            if not USER32.IsWindow(hwnd):
                print("  窗口已随最后一个标签关闭")
                break
        print(f"清理完成：处理 {handled} 个标签，跳过 {skipped} 次")
        # 留下的窗口：空的无标题窗关掉（是我们 spawn 的）。
        if USER32.IsWindow(hwnd):
            values = docs_of(desktop, hwnd)
            mine, reason = is_mine(title_of(hwnd), values)
            if mine:
                if values and any(v.strip() for v in values):
                    press_scancode(scancode_of(VK_A), (VK_CTRL,))
                    time.sleep(0.2)
                    press_scancode(scancode_of(VK_BACK), ())
                    time.sleep(0.3)
                USER32.PostMessageW(hwnd, WM_CLOSE, 0, 0)
                time.sleep(1.0)
                print(f"收尾窗口已关（{reason}）")
            else:
                print(f"收尾窗口保留（{reason}；标题 {title_of(hwnd)!r}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

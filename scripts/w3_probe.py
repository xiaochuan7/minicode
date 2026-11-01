"""W3 真机探针：开一个自己的记事本实例，dump UIA 定点查询结果（只读）。

用法（仓库根目录）::

    python -I scripts/w3_probe.py notepad "文本编辑"
    python -I scripts/w3_probe.py calc "7"
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from minicode.uia.desktop import build_desktop  # noqa: E402
from minicode.uia.windows import describe_window  # noqa: E402

_APP_COMMANDS = {
    "notepad": ["notepad.exe"],
    "calc": ["calc.exe"],
}


def visible_windows(class_filter: tuple[str, ...] | None = None):
    rows = []
    user32 = ctypes.windll.user32
    buf = ctypes.create_unicode_buffer(256)
    EnumWindowsProc = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)

    def on_window(hwnd, _lparam):
        if user32.IsWindowVisible(hwnd):
            user32.GetClassNameW(hwnd, buf, 256)
            cls = buf.value
            if class_filter is None or cls in class_filter:
                user32.GetWindowTextW(hwnd, buf, 256)
                rows.append((int(hwnd), cls, buf.value))
        return True

    user32.EnumWindows(EnumWindowsProc(on_window), 0)
    return rows


def find_new_window(
    before: set[int],
    class_filter: tuple[str, ...] = ("Notepad", "ApplicationFrameWindow"),
    timeout_s: float = 30.0,
):
    """差分法找 spawn 出来的新窗口。

    ⚠️ **类过滤前后必须一致**（W3 事故教训：before 只收
    ``ApplicationFrameWindow``、after 却扫 ``Notepad`` → 把用户已开的
    记事本当成了「新窗口」锚定）。找到的窗口还必须**类名在过滤集合内**。
    """
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        for hwnd, cls, title in visible_windows(class_filter):
            if hwnd not in before and cls in class_filter:
                return hwnd, cls, title
        time.sleep(0.3)
    return None


def main(app: str, name: str, control_type: str = "") -> int:
    command = _APP_COMMANDS[app]
    before = {w[0] for w in visible_windows(("Notepad", "ApplicationFrameWindow"))}
    proc = subprocess.Popen(command)
    found = find_new_window(before)
    if found is None:
        print(json_dumps({"error": f"{app} 没等到新窗口", "pid": proc.pid}))
        proc.terminate()
        return 1
    hwnd, cls, title = found
    info = {"pid": proc.pid, "hwnd": hwnd, "class": cls, "title": title}
    print(json_dumps(info))
    desktop = build_desktop(hwnd=hwnd)
    anchor_info = describe_window(hwnd)
    print("anchor:", anchor_info.app, anchor_info.version, anchor_info.hwnd)
    try:
        from minicode.uia.ports import UiaFindRequest

        request = UiaFindRequest(
            anchor=desktop.anchor,
            name=name or None,
            control_type=control_type or None,
        )
        result = desktop.port.find(request)
        print(
            json_dumps(
                {
                    "timed_out": result.timed_out,
                    "elapsed_ms": result.elapsed_ms,
                    "elements": len(result.elements),
                    "unmatched": result.unmatched_count,
                }
            )
        )
        for element in result.elements[:20]:
            info2 = element.info() if hasattr(element, "info") else {}
            print(json_dumps({"name": getattr(element, "name", ""), "extra": str(info2)[:200]}))
        if not result.elements:
            print(f"（没有匹配：name={name!r} control_type={control_type!r}）")
    finally:
        # 关掉自己的实例（WM_CLOSE，不 taskkill）。
        ctypes.windll.user32.PostMessageW(hwnd, 0x0010, 0, 0)  # WM_CLOSE
        time.sleep(0.5)
        if proc.poll() is None:
            proc.terminate()
    return 0


def json_dumps(obj) -> str:
    import json

    return json.dumps(obj, ensure_ascii=False)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1], sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else ""))

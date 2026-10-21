"""**真机**：读窗口的身份（句柄 + 标题 + 类名）。

⚠️ **只读标题与类名，不读内容** —— 它服务于「这一帧里有没有敏感窗口」的判定，
不服务于把窗口内容取走。

两个问题，两个方法：

* :meth:`WindowsWindowInspector.foreground` —— **当前前台窗口**是谁；
* :meth:`WindowsWindowInspector.visible_windows` —— **这一帧里看得见的**顶层窗口
  （抓的是整屏帧 —— 画面里有什么，取决于屏幕上有哪些窗口，不只是前台那一个）。
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes

from .ports import WindowInfo

__all__ = ["WindowsWindowInspector"]

#: ``DwmGetWindowAttribute`` 的 ``DWMWA_CLOAKED`` —— 窗口是不是**被藏起来**的
#: （在别的虚拟桌面上：看得见但不在这块屏幕上）。
_DWMWA_CLOAKED = 14


def _window_text(user32, hwnd: int) -> str:
    """窗口标题（读不到就是空串）。"""
    buffer = ctypes.create_unicode_buffer(512)
    length = int(user32.GetWindowTextW(wintypes.HWND(hwnd), buffer, 512) or 0)
    return buffer.value[:length] if length > 0 else ""


def _window_class(user32, hwnd: int) -> str:
    """窗口类名（读不到就是空串）。

    工单 M20 的「对话框类名」判据要靠它（标题之外的第二条判据：
    判的是**窗口的种类**，不是它的内容）。
    """
    buffer = ctypes.create_unicode_buffer(256)
    length = int(user32.GetClassNameW(wintypes.HWND(hwnd), buffer, 256) or 0)
    return buffer.value[:length] if length > 0 else ""


def _is_cloaked(hwnd: int) -> bool:
    """这个窗口是不是**被藏起来**的（在别的虚拟桌面上）。

    ⚠️ **读不到 → 按没藏处理**（fail-closed：宁可多挡一次，也不漏判）。
    """
    try:
        dwmapi = ctypes.WinDLL("dwmapi", use_last_error=True)
    except OSError:  # pragma: no cover - 没有 dwmapi 的环境
        return False
    cloaked = ctypes.c_int(0)
    result = dwmapi.DwmGetWindowAttribute(
        wintypes.HWND(hwnd),
        ctypes.c_uint(_DWMWA_CLOAKED),
        ctypes.byref(cloaked),
        ctypes.sizeof(cloaked),
    )
    if result != 0:
        return False
    return bool(cloaked.value)


class WindowsWindowInspector:
    """真机实现：``GetForegroundWindow`` + ``GetWindowTextW`` + ``GetClassNameW``。"""

    def foreground(self) -> WindowInfo:
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        hwnd = int(user32.GetForegroundWindow() or 0)
        if not hwnd:
            return WindowInfo()
        return WindowInfo(
            hwnd=hwnd,
            title=_window_text(user32, hwnd),
            class_name=_window_class(user32, hwnd),
        )

    def visible_windows(self) -> tuple[WindowInfo, ...]:
        """**看得见的**顶层窗口（枚举，不漏后台那些）。

        抓的是**整屏**帧 —— 前台背后的密码管理器窗口照样在那一帧里，
        所以「只判前台」会漏（隐私面最不能要的那种漏）。
        """
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        found: list[WindowInfo] = []

        @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        def _collect(hwnd, _lparam):
            if not user32.IsWindowVisible(hwnd):
                return True
            if _is_cloaked(int(hwnd)):
                # 在别的虚拟桌面上：这块屏幕上**看不见**它。
                return True
            found.append(
                WindowInfo(
                    hwnd=int(hwnd),
                    title=_window_text(user32, int(hwnd)),
                    class_name=_window_class(user32, int(hwnd)),
                )
            )
            return True

        # ⚠️ 回调必须**在本函数返回前活着** —— 局部变量持有它（GC 不会提前收走）。
        user32.EnumWindows(_collect, 0)
        return tuple(found)

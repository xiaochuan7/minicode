"""**护栏的 Win32 轮询端口**（真机实现）。

三个纯查询函数，**只读系统状态、不注入任何输入**：

* :func:`async_key_state` —— ``GetAsyncKeyState``（急停热键轮询用）；
* :func:`last_input_tick` —— ``GetLastInputInfo``（空闲让位用）；
* :func:`foreground_window_info` —— 前台窗口的类名与标题（黑名单 / 白名单用）。

单测不碰这些真函数（ctypes 调用注入 fake 即可）；本模块只在真机接线上用。
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
from dataclasses import dataclass

__all__ = [
    "ForegroundWindowInfo",
    "async_key_state",
    "last_input_tick",
    "now_tick",
    "foreground_window_info",
]

_USER32 = ctypes.WinDLL("user32", use_last_error=True)
_KERNEL32 = ctypes.WinDLL("kernel32", use_last_error=True)


def async_key_state(vkey: int) -> bool:
    """该虚拟键**此刻**是否按下（``GetAsyncKeyState`` 最高位）。"""
    return bool(_USER32.GetAsyncKeyState(vkey) & 0x8000)


def now_tick() -> int:
    """系统 TickCount（毫秒，开机起算；与 ``last_input_tick`` 同一套时钟）。"""
    return int(_KERNEL32.GetTickCount())


def last_input_tick() -> int:
    """系统最后一次输入的时间戳（毫秒TickCount，``GetLastInputInfo``）。"""
    class LASTINPUTINFO(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.UINT),
            ("dwTime", wintypes.DWORD),
        ]

    info = LASTINPUTINFO()
    info.cbSize = ctypes.sizeof(LASTINPUTINFO)
    if not _USER32.GetLastInputInfo(ctypes.byref(info)):
        return 0
    return int(info.dwTime)


@dataclass(frozen=True)
class ForegroundWindowInfo:
    """前台窗口的**事实**（句柄 / 类名 / 标题）。"""

    hwnd: int
    class_name: str
    title: str


def foreground_window_info() -> ForegroundWindowInfo | None:
    """读前台窗口的类名与标题（读不到就 ``None`` —— 不猜）。"""
    hwnd = _USER32.GetForegroundWindow()
    if not hwnd:
        return None
    buf = ctypes.create_unicode_buffer(256)
    _USER32.GetClassNameW(hwnd, buf, 256)
    class_name = buf.value
    _USER32.GetWindowTextW(hwnd, buf, 256)
    title = buf.value
    return ForegroundWindowInfo(hwnd=int(hwnd), class_name=class_name, title=title)

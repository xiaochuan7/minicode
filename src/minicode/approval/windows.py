"""**真机**：读当前前台窗口的句柄与标题。

⚠️ **只读标题，不读内容** —— 它服务于「这一帧里有没有敏感窗口」的判定，
不服务于把窗口内容取走。
"""

from __future__ import annotations

from .ports import WindowInfo

__all__ = ["WindowsWindowInspector"]


class WindowsWindowInspector:
    """真机实现：``GetForegroundWindow`` + ``GetWindowTextW``。"""

    def foreground(self) -> WindowInfo:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.WinDLL("user32", use_last_error=True)
        hwnd = int(user32.GetForegroundWindow() or 0)
        if not hwnd:
            return WindowInfo()
        buffer = ctypes.create_unicode_buffer(512)
        length = int(user32.GetWindowTextW(wintypes.HWND(hwnd), buffer, 512) or 0)
        title = buffer.value[:length] if length > 0 else ""
        return WindowInfo(hwnd=hwnd, title=title)

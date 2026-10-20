"""**模态弹窗的检测**（工单 M15）。

微信这类应用会自己弹窗（「更新提示 / 登录失效」）。弹窗在的时候还往下点，
就是在**盲点** —— 弹窗上的坐标根本不是人以为的那个界面。

两条判据，都是**读出来的**（不是猜的）：

1. **宿主窗口被禁用**（``IsWindowEnabled`` 为假）—— Windows 在模态弹窗出现时
   会禁用它的宿主窗口；
2. **同进程里有一个以宿主窗口为 owner 的可见顶层窗口** —— 弹窗自己也是一个
   顶层窗口，但它的 **owner** 是宿主。

检测到 → **零动作下发** + **交人**（工单 M15 的 WX-3）。

⚠️ 判定逻辑（:func:`detect_modal`）是**纯函数** —— 输入是一串窗口读数，
输出是「有没有模态弹窗」。真机那侧只负责**把读数取回来**（:func:`enumerate_windows`），
所以这条判据能被单测钉住，不碰真桌面。
"""

from __future__ import annotations

import ctypes
from collections.abc import Sequence
from ctypes import wintypes
from dataclasses import dataclass

__all__ = [
    "WindowRecord",
    "ModalFinding",
    "detect_modal",
    "enumerate_windows",
]


@dataclass(frozen=True)
class WindowRecord:
    """一个顶层窗口的**读数**（判定模态弹窗只需要这些）。"""

    hwnd: int
    title: str
    class_name: str
    pid: int
    #: owner 窗口（``GetWindow(hwnd, GW_OWNER)``；0 = 没有 owner）。
    owner_hwnd: int
    #: 窗口当前**可用**吗（``IsWindowEnabled``）。
    enabled: bool
    visible: bool


@dataclass(frozen=True)
class ModalFinding:
    """检测到的模态弹窗。"""

    #: 弹窗自己的句柄（0 = 没找到弹窗窗口，是**宿主窗口被禁用**这条判据命中的）。
    hwnd: int
    title: str
    class_name: str
    #: 为什么判定它是模态弹窗（人读）。
    reason: str

    def to_dict(self) -> dict[str, object]:
        return {
            "hwnd": self.hwnd,
            "title": self.title,
            "class_name": self.class_name,
            "reason": self.reason,
        }


def detect_modal(
    records: Sequence[WindowRecord], target_hwnd: int
) -> ModalFinding | None:
    """这个窗口上面有模态弹窗吗（没有就返回 ``None``）。

    判据按顺序：

    1. **宿主窗口被禁用** → 弹窗在上面（Windows 的模态约定）；
    2. 同进程里有一个**以宿主为 owner** 的可见顶层窗口 → 那就是弹窗。
    """
    target = next((record for record in records if record.hwnd == target_hwnd), None)
    if target is None:
        return None
    if not target.enabled:
        return ModalFinding(
            hwnd=0,
            title=target.title,
            class_name=target.class_name,
            reason=f"宿主窗口 {target_hwnd}「{target.title}」被禁用（模态弹窗在上面）",
        )
    for record in records:
        if record.hwnd == target_hwnd:
            continue
        if record.pid != target.pid:
            continue
        if not record.visible:
            continue
        if record.owner_hwnd == target_hwnd:
            return ModalFinding(
                hwnd=record.hwnd,
                title=record.title,
                class_name=record.class_name,
                reason=(
                    f"同进程里有一个以宿主窗口为 owner 的可见顶层窗口："
                    f"「{record.title}」（{record.class_name}）"
                ),
            )
    return None


# ---------------------------------------------------------------------------
# 真机那侧：把读数取回来
# ---------------------------------------------------------------------------

_user32 = ctypes.WinDLL("user32", use_last_error=True)

#: ``GW_OWNER`` —— 取 owner 窗口。
_GW_OWNER = 4


def enumerate_windows(pid: int | None = None) -> list[WindowRecord]:
    """枚举**可见的顶层窗口**（可选：只看某个进程的）。

    只取读数，不做任何改动。
    """
    found: list[WindowRecord] = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
    def _callback(hwnd, _lparam):
        process_id = wintypes.DWORD()
        _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(process_id))
        if pid is not None and int(process_id.value) != int(pid):
            return True
        title_buffer = ctypes.create_unicode_buffer(512)
        _user32.GetWindowTextW(hwnd, title_buffer, 512)
        class_buffer = ctypes.create_unicode_buffer(256)
        _user32.GetClassNameW(hwnd, class_buffer, 256)
        owner = _user32.GetWindow(hwnd, _GW_OWNER)
        found.append(
            WindowRecord(
                hwnd=int(hwnd),
                title=title_buffer.value,
                class_name=class_buffer.value,
                pid=int(process_id.value),
                owner_hwnd=int(owner or 0),
                enabled=bool(_user32.IsWindowEnabled(hwnd)),
                visible=bool(_user32.IsWindowVisible(hwnd)),
            )
        )
        return True

    _user32.EnumWindows(_callback, 0)
    return found

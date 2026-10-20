"""input.py -- 基于 SendInput 的鼠标与键盘注入（纯标准库 + ctypes）。

必须记住的坑（已在本机实测踩过）
--------------------------------
1. ``INPUT`` 必须用 **union** 表达（MOUSEINPUT / KEYBDINPUT 二选一）。
   写成两个独立 struct 成员时，x64 下 ``sizeof(INPUT) == 64``（正确值 40），
   ``SendInput`` 因为 ``cbSize`` 对不上会**直接返回 0 且不报错**——
   所有点击静默失效。
2. 所有 ``argtypes`` / ``restype`` 必须显式声明。ctypes 默认按 ``c_int``
   传参/返回，句柄（HDC/HBITMAP）在 64 位下会被截断成 32 位。
3. 绝对坐标归一化用 ``round((x - VX) * 65535 / (W - 1))``。
   本机实测 6 个目标点 x 12 次采样，误差全为 0；
   变体 ``x * 65535 // W`` 有稳定的 -1px 偏差。
4. 一次绝对移动后要**回读 GetCursorPos** 校验，因为真实用户可能在你两次
   调用之间动了鼠标。

坐标约定
--------
所有入参都是**物理像素**，原点在虚拟桌面左上角（前提：进程 PMv2 感知）。
"""

from __future__ import annotations

import ctypes
import time
from ctypes import wintypes

import dpi  # noqa: F401  -- 必须最先 import，保证进程 DPI 感知已生效

__all__ = [
    "InputSelfCheckError", "SendInputError",
    "self_check", "cursor_position",
    "move_to", "move_by", "click", "double_click", "right_click",
    "scroll", "drag", "ClickTiming",
    "press_vk", "hotkey", "type_unicode", "press_scancode", "scancode_of",
    "get_clipboard_text", "set_clipboard_text", "paste_text",
    "CLICK_SETTLE", "CLICK_HOLD",
]

# ---------------------------------------------------------------------------
# 时序常量（点击序列：move -> settle -> down -> hold -> up）
# ---------------------------------------------------------------------------

#: 移动后到按下之间的停顿（秒）。给 UI 留出 hover / 菜单弹出时间。
CLICK_SETTLE = 0.060
#: 按下到抬起之间的按住时长（秒）。
CLICK_HOLD = 0.040

# ---------------------------------------------------------------------------
# Win32 常量
# ---------------------------------------------------------------------------

INPUT_MOUSE = 0
INPUT_KEYBOARD = 1

MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_RIGHTDOWN = 0x0008
MOUSEEVENTF_RIGHTUP = 0x0010
MOUSEEVENTF_MIDDLEDOWN = 0x0020
MOUSEEVENTF_MIDDLEUP = 0x0040
MOUSEEVENTF_XDOWN = 0x0080
MOUSEEVENTF_XUP = 0x0100
MOUSEEVENTF_WHEEL = 0x0800
MOUSEEVENTF_HWHEEL = 0x1000
MOUSEEVENTF_ABSOLUTE = 0x8000
MOUSEEVENTF_VIRTUALDESK = 0x4000

KEYEVENTF_EXTENDEDKEY = 0x0001
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004
KEYEVENTF_SCANCODE = 0x0008

WHEEL_DELTA = 120
MAPVK_VK_TO_VSC = 0

VK_CONTROL = 0x11
VK_SHIFT = 0x10
VK_MENU = 0x12  # Alt
VK_LWIN = 0x5B
VK_RWIN = 0x5C
VK_V = 0x56
VK_RETURN = 0x0D
VK_ESCAPE = 0x1B
VK_TAB = 0x09
VK_BACK = 0x08
VK_DELETE = 0x2E

CF_UNICODETEXT = 13
GMEM_MOVEABLE = 0x0002


class InputSelfCheckError(RuntimeError):
    """启动自检失败——SendInput 在本进程/本环境下不可用。"""


class SendInputError(OSError):
    """SendInput 调用失败（UIPI 阻断 / cbSize 不对 / 输入流被占）。"""


# ---------------------------------------------------------------------------
# 结构体
# ---------------------------------------------------------------------------

_ULONG_PTR = ctypes.c_ulonglong if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_ulong


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", _ULONG_PTR),
    ]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", _ULONG_PTR),
    ]


class _INPUTUNION(ctypes.Union):
    # 关键：必须是 union。若写成两个独立成员，x64 下 sizeof(INPUT)=64，
    # SendInput 会因为 cbSize 不匹配静默失败。
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT)]


class INPUT(ctypes.Structure):
    _anonymous_ = ("u",)  # 之后可直接用 inp.mi / inp.ki
    _fields_ = [("type", wintypes.DWORD), ("u", _INPUTUNION)]


_EXPECTED_INPUT_SIZE = 40 if ctypes.sizeof(ctypes.c_void_p) == 8 else 28

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
user32.SendInput.restype = wintypes.UINT

user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
user32.GetCursorPos.restype = wintypes.BOOL

user32.MapVirtualKeyW.argtypes = [wintypes.UINT, wintypes.UINT]
user32.MapVirtualKeyW.restype = wintypes.UINT

user32.OpenClipboard.argtypes = [wintypes.HWND]
user32.OpenClipboard.restype = wintypes.BOOL
user32.CloseClipboard.argtypes = []
user32.CloseClipboard.restype = wintypes.BOOL
user32.EmptyClipboard.argtypes = []
user32.EmptyClipboard.restype = wintypes.BOOL
user32.GetClipboardData.argtypes = [wintypes.UINT]
user32.GetClipboardData.restype = wintypes.HANDLE
user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
user32.SetClipboardData.restype = wintypes.HANDLE
user32.IsClipboardFormatAvailable.argtypes = [wintypes.UINT]
user32.IsClipboardFormatAvailable.restype = wintypes.BOOL

kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
kernel32.GlobalLock.restype = ctypes.c_void_p
kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
kernel32.GlobalUnlock.restype = wintypes.BOOL
kernel32.GlobalFree.argtypes = [wintypes.HGLOBAL]
kernel32.GlobalFree.restype = wintypes.HGLOBAL


# ---------------------------------------------------------------------------
# 底层发送
# ---------------------------------------------------------------------------


def _send(*inputs: INPUT) -> int:
    """发送一批输入事件。数量不匹配就抛异常（绝不静默）。"""
    if not inputs:
        return 0
    arr = (INPUT * len(inputs))(*inputs)
    sent = user32.SendInput(len(inputs), arr, ctypes.sizeof(INPUT))
    if sent != len(inputs):
        # 注意：UIPI 阻断时 GetLastError 也不指示原因，只能靠返回值判断。
        raise SendInputError(
            f"SendInput 只插入了 {sent}/{len(inputs)} 个事件"
            f"（cbSize={ctypes.sizeof(INPUT)}，last_error={ctypes.get_last_error()}）"
        )
    return sent


def _mouse(flags: int, dx: int = 0, dy: int = 0, mouse_data: int = 0) -> INPUT:
    i = INPUT(type=INPUT_MOUSE)
    i.mi = MOUSEINPUT(dx, dy, mouse_data, flags, 0, 0)
    return i


def _keyboard(vk: int = 0, scan: int = 0, flags: int = 0) -> INPUT:
    i = INPUT(type=INPUT_KEYBOARD)
    i.ki = KEYBDINPUT(vk, scan, flags, 0, 0)
    return i


# ---------------------------------------------------------------------------
# 坐标换算
# ---------------------------------------------------------------------------


def _normalize(x: int, y: int) -> tuple[int, int]:
    """物理屏幕坐标 -> SendInput 的 0..65535 归一化坐标（相对虚拟桌面）。

    公式 ``round((x - VX) * 65535 / (W - 1))`` 在本机实测像素级精确。
    必须带 ``MOUSEEVENTF_VIRTUALDESK``，否则多显示器时只会映射到主显示器。
    """
    ox, oy = dpi.virtual_desktop_origin()
    vw, vh = dpi.virtual_desktop_size()
    nx = round((int(x) - ox) * 65535 / max(vw - 1, 1))
    ny = round((int(y) - oy) * 65535 / max(vh - 1, 1))
    return max(0, min(65535, nx)), max(0, min(65535, ny))


def cursor_position() -> tuple[int, int]:
    """当前光标位置（物理像素）。"""
    p = wintypes.POINT()
    if not user32.GetCursorPos(ctypes.byref(p)):
        raise ctypes.WinError(ctypes.get_last_error())
    return p.x, p.y


# ---------------------------------------------------------------------------
# 启动自检
# ---------------------------------------------------------------------------


def self_check(move_probe: bool = True) -> dict:
    """自检 SendInput 是否可用。

    做法：把光标**移到它当前所在的位置**（无害，不产生可见移动），
    检查 ``SendInput`` 的返回值是否非 0。

    失败时抛 :class:`InputSelfCheckError`，不做静默降级。
    """
    result = {
        "sizeof_input": ctypes.sizeof(INPUT),
        "sizeof_input_expected": _EXPECTED_INPUT_SIZE,
        "sizeof_ok": ctypes.sizeof(INPUT) == _EXPECTED_INPUT_SIZE,
        "awareness": dpi.dpi_awareness_context(),
        "send_input_ok": False,
        "cursor": None,
        "readback_delta": None,
    }
    if not result["sizeof_ok"]:
        raise InputSelfCheckError(
            f"sizeof(INPUT)={ctypes.sizeof(INPUT)}，期望 {_EXPECTED_INPUT_SIZE}。"
            "INPUT 必须用 union 定义，否则 SendInput 会静默失败。"
        )
    if not move_probe:
        return result

    x, y = cursor_position()
    result["cursor"] = (x, y)
    nx, ny = _normalize(x, y)
    sent = user32.SendInput(1, (INPUT * 1)(_mouse(MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE |
                                                  MOUSEEVENTF_VIRTUALDESK, nx, ny)),
                            ctypes.sizeof(INPUT))
    if sent != 1:
        raise InputSelfCheckError(
            f"SendInput 自检失败：返回 {sent}（期望 1）。"
            f"last_error={ctypes.get_last_error()}。"
            "常见原因：UIPI 完整性级别不足（目标窗口权限更高）、"
            "或 cbSize 与 sizeof(INPUT) 不一致。"
        )
    result["send_input_ok"] = True
    time.sleep(0.02)
    ax, ay = cursor_position()
    result["readback_delta"] = (ax - x, ay - y)
    return result


# ---------------------------------------------------------------------------
# 鼠标
# ---------------------------------------------------------------------------


def move_to(x: int, y: int) -> None:
    """绝对移动到物理坐标 (x, y)。"""
    nx, ny = _normalize(x, y)
    _send(_mouse(MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK, nx, ny))


def move_by(dx: int, dy: int) -> None:
    """相对移动。**注意**相对位移受鼠标加速曲线影响，可能被放大。"""
    _send(_mouse(MOUSEEVENTF_MOVE, int(dx), int(dy)))


class ClickTiming:
    """一次点击的实测时序。"""

    __slots__ = ("pre_move_ms", "hold_ms", "total_ms", "target", "down_sent", "up_sent")

    def __init__(self, pre_move_ms, hold_ms, total_ms, target):
        self.pre_move_ms = pre_move_ms
        self.hold_ms = hold_ms
        self.total_ms = total_ms
        self.target = target
        self.down_sent = False
        self.up_sent = False

    def to_dict(self):
        return {
            "pre_move_ms": round(self.pre_move_ms, 2),
            "hold_ms": round(self.hold_ms, 2),
            "total_ms": round(self.total_ms, 2),
            "target": self.target,
            "down_sent": self.down_sent,
            "up_sent": self.up_sent,
        }

    def __repr__(self):
        return (f"ClickTiming(pre_move={self.pre_move_ms:.1f}ms, "
                f"hold={self.hold_ms:.1f}ms, total={self.total_ms:.1f}ms)")


_BUTTONS = {
    "left": (MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP),
    "right": (MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP),
    "middle": (MOUSEEVENTF_MIDDLEDOWN, MOUSEEVENTF_MIDDLEUP),
}


def click(x: int, y: int, button: str = "left",
          settle: float = CLICK_SETTLE, hold: float = CLICK_HOLD) -> ClickTiming:
    """带防抖的点击：**move -> settle -> down -> hold -> up**。

    停顿是必须的：很多 UI 需要先收到 WM_MOUSEMOVE（hover 生效、菜单弹出）
    才响应按钮消息；瞬移点击常常只触发 hover 不触发 click。

    返回实测时序，供自检断言。
    """
    if button not in _BUTTONS:
        raise ValueError(f"未知按键: {button!r}")
    down_flag, up_flag = _BUTTONS[button]

    t0 = time.perf_counter()
    move_to(x, y)
    time.sleep(settle)
    t_down = time.perf_counter()
    _send(_mouse(down_flag))
    time.sleep(hold)
    t_up = time.perf_counter()
    _send(_mouse(up_flag))

    timing = ClickTiming(
        pre_move_ms=(t_down - t0) * 1000.0,
        hold_ms=(t_up - t_down) * 1000.0,
        total_ms=(time.perf_counter() - t0) * 1000.0,
        target=(int(x), int(y)),
    )
    timing.down_sent = True
    timing.up_sent = True
    return timing


def right_click(x: int, y: int, **kw) -> ClickTiming:
    return click(x, y, button="right", **kw)


def double_click(x: int, y: int, gap: float = 0.08) -> ClickTiming:
    """双击：两次 down-up 间隔必须短于系统双击时间（默认 500ms）且位移 < 4px。"""
    t0 = time.perf_counter()
    first = click(x, y, settle=0.02, hold=0.02)
    time.sleep(gap)
    second = click(x, y, settle=0.02, hold=0.02)
    first.total_ms = (time.perf_counter() - t0) * 1000.0
    return first


def scroll(x: int, y: int, clicks: int = -3) -> None:
    """在 (x, y) 处滚动。clicks > 0 向上。"""
    move_to(x, y)
    time.sleep(0.03)
    _send(_mouse(MOUSEEVENTF_WHEEL, mouse_data=clicks * WHEEL_DELTA))


def drag(start_xy, end_xy, steps: int = 20, step_delay: float = 0.015) -> None:
    """按下 -> 分步移动 -> 抬起。"""
    sx, sy = start_xy
    ex, ey = end_xy
    move_to(sx, sy)
    time.sleep(0.06)
    _send(_mouse(MOUSEEVENTF_LEFTDOWN))
    time.sleep(0.06)
    for k in range(1, steps + 1):
        move_to(round(sx + (ex - sx) * k / steps),
                round(sy + (ey - sy) * k / steps))
        time.sleep(step_delay)
    time.sleep(0.06)
    _send(_mouse(MOUSEEVENTF_LEFTUP))


# ---------------------------------------------------------------------------
# 键盘
# ---------------------------------------------------------------------------


def press_vk(vk: int, hold: float = 0.02) -> None:
    """按一次虚拟键码。"""
    _send(_keyboard(vk, 0, 0))
    time.sleep(hold)
    _send(_keyboard(vk, 0, KEYEVENTF_KEYUP))


def hotkey(*vks: int, hold: float = 0.02) -> None:
    """组合键，如 ``hotkey(VK_CONTROL, VK_V)``。"""
    if not vks:
        return
    for vk in vks[:-1]:
        _send(_keyboard(vk, 0, 0))
    time.sleep(0.01)
    _send(_keyboard(vks[-1], 0, 0))
    time.sleep(hold)
    _send(_keyboard(vks[-1], 0, KEYEVENTF_KEYUP))
    for vk in reversed(vks[:-1]):
        _send(_keyboard(vk, 0, KEYEVENTF_KEYUP))
    time.sleep(0.01)


def type_unicode(text: str, per_char_delay: float = 0.012) -> None:
    """直接注入 Unicode 字符，**绕过 IME 与键盘布局**（可打中文）。

    两个必须遵守的细节：
    * down 与 up 要**分两次** SendInput；合成一个 INPUT[2] 数组可能一个字符都不产生。
    * 非 BMP 字符（emoji）是 UTF-16 代理对，需要两组事件。
    """
    for ch in text:
        data = ch.encode("utf-16-le")
        for off in range(0, len(data), 2):
            cp = data[off] | (data[off + 1] << 8)
            _send(_keyboard(0, cp, KEYEVENTF_UNICODE))
            _send(_keyboard(0, cp, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP))
        time.sleep(per_char_delay)


def scancode_of(vk: int) -> int:
    """虚拟键码 -> 扫描码（MapVirtualKey）。"""
    return int(user32.MapVirtualKeyW(vk, MAPVK_VK_TO_VSC))


def press_scancode(scan: int, modifiers: tuple[int, ...] = (), hold: float = 0.03) -> None:
    """按扫描码发键，``modifiers`` 是修饰键的**虚拟键码**元组。

    扫描码与键盘布局无关，游戏（用 DirectInput 原始扫描码）只认这个。
    """
    mod_scans = [scancode_of(vk) for vk in modifiers]
    for s in mod_scans:
        _send(_keyboard(0, s, KEYEVENTF_SCANCODE))
    time.sleep(0.01)
    _send(_keyboard(0, scan, KEYEVENTF_SCANCODE))
    time.sleep(hold)
    _send(_keyboard(0, scan, KEYEVENTF_SCANCODE | KEYEVENTF_KEYUP))
    for s in reversed(mod_scans):
        _send(_keyboard(0, s, KEYEVENTF_SCANCODE | KEYEVENTF_KEYUP))
    time.sleep(0.01)


# ---------------------------------------------------------------------------
# 剪贴板通道（绕过输入法的最稳路径）
# ---------------------------------------------------------------------------


def _open_clipboard(retries: int = 20, delay: float = 0.02) -> bool:
    for _ in range(retries):
        if user32.OpenClipboard(None):
            return True
        time.sleep(delay)
    return False


def get_clipboard_text() -> str | None:
    """读剪贴板文本。没有文本内容时返回 None。"""
    if not user32.IsClipboardFormatAvailable(CF_UNICODETEXT):
        return None
    if not _open_clipboard():
        raise OSError("OpenClipboard 失败（可能被其它进程占用）")
    try:
        handle = user32.GetClipboardData(CF_UNICODETEXT)
        if not handle:
            return None
        ptr = kernel32.GlobalLock(handle)
        if not ptr:
            return None
        try:
            return ctypes.wstring_at(ptr)
        finally:
            kernel32.GlobalUnlock(handle)
    finally:
        user32.CloseClipboard()


def set_clipboard_text(text: str) -> None:
    """写剪贴板文本。"""
    if not _open_clipboard():
        raise OSError("OpenClipboard 失败（可能被其它进程占用）")
    try:
        user32.EmptyClipboard()
        data = text.encode("utf-16-le") + b"\x00\x00"
        hmem = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(data))
        if not hmem:
            raise ctypes.WinError(ctypes.get_last_error())
        ptr = kernel32.GlobalLock(hmem)
        if not ptr:
            kernel32.GlobalFree(hmem)
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            ctypes.memmove(ptr, data, len(data))
        finally:
            kernel32.GlobalUnlock(hmem)
        if not user32.SetClipboardData(CF_UNICODETEXT, hmem):
            kernel32.GlobalFree(hmem)
            raise ctypes.WinError(ctypes.get_last_error())
        # 剪贴板接管了 HGLOBAL 的所有权，不再 Free
    finally:
        user32.CloseClipboard()


def paste_text(text: str, restore: bool = True, settle: float = 0.08) -> bool:
    """剪贴板通道：设剪贴板 -> Ctrl+V -> **恢复原剪贴板内容**。

    这是"绕过输入法"最稳的通道：不受 IME 状态、键盘布局影响。
    代价是会占用剪贴板，所以默认在粘贴后把原内容写回去。
    """
    previous = get_clipboard_text() if restore else None
    set_clipboard_text(text)
    time.sleep(0.05)
    hotkey(VK_CONTROL, VK_V)
    time.sleep(settle)
    if restore and previous is not None:
        try:
            set_clipboard_text(previous)
        except OSError:
            pass
    return True


# ---------------------------------------------------------------------------
# 导入即自检
# ---------------------------------------------------------------------------

_SELF_CHECK_RESULT = self_check()

if __name__ == "__main__":
    import json
    print(json.dumps(_SELF_CHECK_RESULT, indent=2, ensure_ascii=False, default=str))

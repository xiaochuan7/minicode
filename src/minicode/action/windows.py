"""真实 Windows 适配器：把端口接到本机。

复用冻结原型 ``minicode.exec``（DPI 感知 / 抓屏 / SendInput / UIA），
本模块只补**缺失的接线**：端口形状、归一化的唯一来源、置前与遮挡判定。

冻结原型一律**惰性导入**——这样 ``import minicode.action.windows``
本身不会碰真桌面（不设 DPI 感知、不抓屏、不注入）。
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes

from .executor import DoubleClickParams
from .geometry import VirtualDesktop, rect_size
from .ports import (
    CapturedFrame,
    ControlCandidate,
    ControlTreeSnapshot,
    ElevationState,
    InjectionBlocked,
    PixelFormat,
)

__all__ = [
    "WindowsMetrics",
    "WindowsCaptureSource",
    "WindowsControlTree",
    "WindowsInputInjector",
    "WindowsWindowController",
    "WindowsSecureDesktopDetector",
    "system_double_click_params",
    "current_virtual_desktop",
    "find_taskbar_hwnd",
]

# ---------------------------------------------------------------------------
# Win32 常量与绑定
# ---------------------------------------------------------------------------

SW_RESTORE = 9

#: ``GetAncestor`` 的 ``GA_ROOT``：取根窗口（顶层窗口）。
GA_ROOT = 2

_user32 = ctypes.WinDLL("user32", use_last_error=True)
_user32.GetForegroundWindow.restype = wintypes.HWND
_user32.SetForegroundWindow.argtypes = [wintypes.HWND]
_user32.SetForegroundWindow.restype = wintypes.BOOL
_user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
_user32.ShowWindow.restype = wintypes.BOOL
_user32.IsWindow.argtypes = [wintypes.HWND]
_user32.IsWindow.restype = wintypes.BOOL
_user32.WindowFromPoint.argtypes = [wintypes.POINT]
_user32.WindowFromPoint.restype = wintypes.HWND
_user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
_user32.GetAncestor.restype = wintypes.HWND
_user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
_user32.FindWindowW.restype = wintypes.HWND
_user32.GetDoubleClickTime.argtypes = []
_user32.GetDoubleClickTime.restype = wintypes.UINT
_user32.OpenClipboard.argtypes = [wintypes.HWND]
_user32.OpenClipboard.restype = wintypes.BOOL
_user32.CloseClipboard.argtypes = []
_user32.CloseClipboard.restype = wintypes.BOOL
_user32.EnumClipboardFormats.argtypes = [wintypes.UINT]
_user32.EnumClipboardFormats.restype = wintypes.UINT
_user32.OpenInputDesktop.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
_user32.OpenInputDesktop.restype = wintypes.HANDLE
_user32.GetUserObjectInformationW.argtypes = [
    wintypes.HANDLE,
    ctypes.c_int,
    ctypes.c_void_p,
    wintypes.DWORD,
    ctypes.POINTER(wintypes.DWORD),
]
_user32.GetUserObjectInformationW.restype = wintypes.BOOL
_user32.CloseDesktop.argtypes = [wintypes.HANDLE]
_user32.CloseDesktop.restype = wintypes.BOOL

#: ``OpenInputDesktop`` 的 ``DESKTOP_READOBJECTS``（只读桌面名，够用了）。
DESKTOP_READOBJECTS = 0x0001

#: ``GetUserObjectInformation`` 的 ``UOI_NAME``（取桌面的名字）。
UOI_NAME = 2

#: UAC 安全桌面的名字（提权弹窗出现时输入桌面被切到它上面）。
SECURE_DESKTOP_NAME = "Winlogon"


# ---------------------------------------------------------------------------
# 度量
# ---------------------------------------------------------------------------


class WindowsMetrics:
    """屏幕度量与 DPI 状态（走冻结原型的 DPI 感知路径）。"""

    def awareness(self) -> str:
        from minicode.exec import dpi

        return dpi.dpi_awareness_context()

    def physical_size(self) -> tuple[int, int]:
        from minicode.exec import dpi

        return dpi.physical_screen_size()

    def device_caps_dpi(self) -> int:
        from minicode.exec import dpi

        return int(dpi.device_caps_report()["logpixelsx"])


def current_virtual_desktop() -> VirtualDesktop:
    """当前虚拟桌面度量（原点可能为负）。"""
    from minicode.exec import dpi

    ox, oy = dpi.virtual_desktop_origin()
    vw, vh = dpi.virtual_desktop_size()
    return VirtualDesktop(ox, oy, vw, vh)


def system_double_click_params() -> DoubleClickParams:
    """**运行时查询**系统的双击参数（时间与位移），不写死。

    * 时间：``GetDoubleClickTime()``（Win32 API）。
    * 位移：双击矩形的大小。Windows **没有**对应的 Win32 API
      （既没有 ``GetDoubleClickSize``，``SPI_GETDOUBLECLICKWIDTH`` 也不是有效动作），
      唯一的运行时来源是注册表 ``HKCU\\Control Panel\\Mouse\\DoubleClickWidth``。
    """
    return DoubleClickParams(
        time_ms=int(_user32.GetDoubleClickTime()),
        distance_px=_double_click_rectangle_width(),
    )


def _double_click_rectangle_width() -> int:
    """从注册表读双击矩形的宽（像素）。读不到就返回 0。"""
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Control Panel\Mouse") as key:
            for name in ("DoubleClickWidth", "DoubleClickHeight"):
                try:
                    value, _ = winreg.QueryValueEx(key, name)
                except FileNotFoundError:
                    continue
                return int(value)
    except OSError:
        return 0
    return 0


# ---------------------------------------------------------------------------
# 抓屏
# ---------------------------------------------------------------------------


class WindowsCaptureSource:
    """默认抓屏路径：物理像素的 GDI 系抓屏（mss 走 GDI BitBlt，无 mss 时纯 ctypes GDI）。

    **不使用 PrintWindow 顶替抓屏**——硬件加速 / 分层窗口会返回黑或空白，
    属于静默失败。抓屏拿到 0×0 画面时如实返回，由上层跳过该捕获源。
    """

    name = "windows-gdi"

    def capture(self) -> CapturedFrame:
        from minicode.exec import capture

        frame, meta = capture.grab_frame()
        if hasattr(frame, "tobytes"):
            payload = frame.tobytes()
        else:
            payload = frame.to_bytes()
        return CapturedFrame(
            width=meta.width,
            height=meta.height,
            left=meta.left,
            top=meta.top,
            payload=payload,
            backend=meta.backend,
            source=meta.source,
            pixel_format=_pixel_format(frame),
        )

    def capture_region(self, rect: tuple[int, int, int, int]) -> CapturedFrame:
        """抓一块**物理像素**矩形（``screenshot`` 动作给了 region 时用）。

        ``rect`` 是 ``(left, top, right, bottom)``；冻结原型的
        ``capture.grab_frame`` 收的是 ``(left, top, width, height)`` ——
        换算只此一处。
        """
        from minicode.exec import capture

        left, top, right, bottom = (int(value) for value in rect)
        width, height = rect_size((left, top, right, bottom))
        frame, meta = capture.grab_frame((left, top, width, height))
        if hasattr(frame, "tobytes"):
            payload = frame.tobytes()
        else:
            payload = frame.to_bytes()
        return CapturedFrame(
            width=meta.width,
            height=meta.height,
            left=meta.left,
            top=meta.top,
            payload=payload,
            backend=meta.backend,
            source=meta.source,
            pixel_format=_pixel_format(frame),
        )


def _pixel_format(frame) -> PixelFormat:
    """抓屏后端给的像素排布：``BGR``（3 字节/像素）或 ``BGRA``（4 字节/像素）。

    冻结原型的 ``_to_array`` 有 numpy 时给 BGR 三通道，没有 numpy 时给
    ``RawFrame``（带 ``.bgra`` 的四通道字节）—— **两种排布要分清**，
    否则编码出来的图颜色是反的。
    """
    return PixelFormat.BGRA if hasattr(frame, "bgra") else PixelFormat.BGR


# ---------------------------------------------------------------------------
# 控件树
# ---------------------------------------------------------------------------


def find_taskbar_hwnd() -> int | None:
    """找任务栏窗口（``Shell_TrayWnd``）。

    用 ``FindWindowW`` 按类名直接命中，而不是枚举：**实测任务栏窗口有时不出现在
    ``EnumWindows`` 的枚举结果里**（冻结原型的 ``find_window`` 基于枚举，会漏掉它），
    ``FindWindowW`` 则一直能命中。枚举只作为兜底。
    """
    hwnd = _user32.FindWindowW("Shell_TrayWnd", None)
    if hwnd:
        return int(hwnd)
    from minicode.exec import uia

    windows = uia.find_window(class_substr="Shell_TrayWnd", visible_only=True)
    return int(windows[0].hwnd) if windows else None


class WindowsControlTree:
    """UIA 通道：定点查询控件候选。

    只做**定点查询**（不 dump 全树），候选的包围盒是**物理像素**
    （前提是进程已 PMv2 感知）。
    """

    def __init__(self, hwnd: int | None = None, limit: int = 200):
        self.hwnd = int(hwnd) if hwnd is not None else find_taskbar_hwnd()
        self.limit = int(limit)

    def snapshot(self) -> ControlTreeSnapshot:
        from minicode.exec import uia

        if self.hwnd is None:
            return ControlTreeSnapshot(())
        elements = uia.find_controls(hwnd=self.hwnd, limit=self.limit)
        candidates: list[ControlCandidate] = []
        try:
            for element in elements:
                try:
                    usable, _ = uia.is_usable(element, interactive_only=True)
                    if not usable:
                        continue
                    rect = element.bounding_rect
                    candidates.append(
                        ControlCandidate(
                            name=element.name,
                            control_type=element.control_type_name,
                            rect=(rect.left, rect.top, rect.right, rect.bottom),
                            window_handle=int(element.native_window_handle) or self.hwnd,
                            automation_id=element.automation_id or None,
                        )
                    )
                except Exception:
                    continue
                finally:
                    element.release()
        finally:
            del elements
        return ControlTreeSnapshot(tuple(candidates))


# ---------------------------------------------------------------------------
# 输入注入
# ---------------------------------------------------------------------------

#: 组合键名 → 虚拟键码。
_VK_NAMES: dict[str, int] = {
    "ctrl": 0x11,
    "control": 0x11,
    "shift": 0x10,
    "alt": 0x12,
    "win": 0x5B,
    "lwin": 0x5B,
    "enter": 0x0D,
    "return": 0x0D,
    "esc": 0x1B,
    "escape": 0x1B,
    "tab": 0x09,
    "backspace": 0x08,
    "delete": 0x2E,
    "space": 0x20,
    "home": 0x24,
    "end": 0x23,
    "pageup": 0x21,
    "pagedown": 0x22,
    "up": 0x26,
    "down": 0x28,
    "left": 0x25,
    "right": 0x27,
}
_VK_NAMES.update({chr(ord("a") + i): ord("a") + i for i in range(26)})
_VK_NAMES.update({str(i): 0x30 + i for i in range(10)})

_BUTTON_FLAGS = {
    ("left", True): "MOUSEEVENTF_LEFTDOWN",
    ("left", False): "MOUSEEVENTF_LEFTUP",
    ("right", True): "MOUSEEVENTF_RIGHTDOWN",
    ("right", False): "MOUSEEVENTF_RIGHTUP",
    ("middle", True): "MOUSEEVENTF_MIDDLEDOWN",
    ("middle", False): "MOUSEEVENTF_MIDDLEUP",
}

#: 剪贴板里的「非文本用户内容」格式：图片、文件列表等。
#: 这些内容没法用 EmptyClipboard 之外的手段还原，所以一旦存在就不走粘贴通道。
_CF_BITMAP = 2
_CF_ENHMETAFILE = 14
_CF_METAFILEPICT = 3
_CF_PALETTE = 9
_CF_HDROP = 15
_NON_TEXT_CLIPBOARD_FORMATS = frozenset(
    {_CF_BITMAP, _CF_ENHMETAFILE, _CF_METAFILEPICT, _CF_PALETTE, _CF_HDROP}
)


class WindowsInputInjector:
    """``SendInput`` 注入（复用冻结原型的结构体与 API 绑定）。

    * 归一化公式（``(维度 − 1)`` 分母、虚拟桌面原点、``MOUSEEVENTF_VIRTUALDESK``）
      在**本项目的代码里只有一份实现**：
      :meth:`~minicode.action.geometry.VirtualDesktop.to_absolute_input`。
      冻结原型 ``exec/input.py`` 里另有一份同名公式（它的私有 ``_normalize``），
      但**动作层不调用它**——不用它的 ``move_to`` / ``click``，
      只借它的 ``INPUT`` 结构体、``MOUSEEVENTF_*`` 常量与 ``user32`` 绑定。
      因此运行期不存在两份被同时使用的换算逻辑。
      这一点由单测 ``test_the_action_layer_does_not_call_the_frozen_normaliser`` 钉住。
    * 失败判定**只依据返回值**：``SendInput`` 返回 0 时不查 ``GetLastError``
      （UIPI 阻断时它不指示原因）。
    """

    def __init__(self, virtual_desktop: VirtualDesktop | None = None):
        self.virtual_desktop = virtual_desktop or current_virtual_desktop()

    # -- 底层 -------------------------------------------------------------

    @staticmethod
    def _win_input():
        from minicode.exec import input as win_input

        return win_input

    def _send_mouse(self, flags: int, dx: int = 0, dy: int = 0, mouse_data: int = 0) -> int:
        win_input = self._win_input()
        event = win_input.INPUT(type=win_input.INPUT_MOUSE)
        event.mi = win_input.MOUSEINPUT(dx, dy, mouse_data, flags, 0, 0)
        array = (win_input.INPUT * 1)(event)
        sent = win_input.user32.SendInput(1, array, ctypes.sizeof(win_input.INPUT))
        if sent != 1:
            # 只依据返回值判失败；GetLastError 在 UIPI 阻断时不指示原因。
            raise InjectionBlocked(
                f"SendInput 只插入了 {sent}/1 个事件"
                f"（cbSize={ctypes.sizeof(win_input.INPUT)}）"
            )
        return sent

    # -- 端口 -------------------------------------------------------------

    def input_struct_size(self) -> int:
        win_input = self._win_input()
        return ctypes.sizeof(win_input.INPUT)

    def probe_injection(self) -> int:
        """把光标移到它当前所在的位置（无害），返回 ``SendInput`` 的返回值。"""
        win_input = self._win_input()
        x, y = win_input.cursor_position()
        nx, ny = self.virtual_desktop.to_absolute_input(x, y)
        flags = (
            win_input.MOUSEEVENTF_MOVE
            | win_input.MOUSEEVENTF_ABSOLUTE
            | win_input.MOUSEEVENTF_VIRTUALDESK
        )
        event = win_input.INPUT(type=win_input.INPUT_MOUSE)
        event.mi = win_input.MOUSEINPUT(nx, ny, 0, flags, 0, 0)
        array = (win_input.INPUT * 1)(event)
        return int(win_input.user32.SendInput(1, array, ctypes.sizeof(win_input.INPUT)))

    def move(self, x: int, y: int) -> None:
        win_input = self._win_input()
        nx, ny = self.virtual_desktop.to_absolute_input(x, y)
        flags = (
            win_input.MOUSEEVENTF_MOVE
            | win_input.MOUSEEVENTF_ABSOLUTE
            | win_input.MOUSEEVENTF_VIRTUALDESK
        )
        self._send_mouse(flags, nx, ny)

    def button_down(self, button: str = "left") -> None:
        self._send_mouse(self._button_flag(button, True))

    def button_up(self, button: str = "left") -> None:
        self._send_mouse(self._button_flag(button, False))

    def scroll_delta(self, x: int, y: int, delta: int) -> None:
        win_input = self._win_input()
        self._send_mouse(win_input.MOUSEEVENTF_WHEEL, mouse_data=int(delta))

    def read_cursor(self) -> tuple[int, int] | None:
        win_input = self._win_input()
        try:
            return win_input.cursor_position()
        except Exception:
            return None

    def press_combo(self, combo: tuple[str, ...]) -> None:
        win_input = self._win_input()
        vks = []
        for name in combo:
            key = str(name).strip().lower()
            if key not in _VK_NAMES:
                raise ValueError(f"未知按键名：{name!r}")
            vks.append(_VK_NAMES[key])
        if not vks:
            return
        win_input.hotkey(*vks)

    def type_unicode(self, text: str) -> None:
        win_input = self._win_input()
        win_input.type_unicode(text)

    def read_clipboard(self) -> str | None:
        win_input = self._win_input()
        try:
            return win_input.get_clipboard_text()
        except OSError:
            return None

    def write_clipboard(self, text: str | None) -> None:
        win_input = self._win_input()
        if text is not None:
            win_input.set_clipboard_text(text)
            return
        # 清空剪贴板（动作前本来就是空的）。
        if not win_input.user32.OpenClipboard(None):
            raise OSError("OpenClipboard 失败")
        try:
            win_input.user32.EmptyClipboard()
        finally:
            win_input.user32.CloseClipboard()

    def has_non_text_clipboard_content(self) -> bool:
        """剪贴板里有没有非文本内容（图片、文件列表等）。

        只看「用户内容」格式：系统自带的伴随格式（Shell IDList Array 等）
        跟着文本走，不算非文本内容。
        """
        if not _user32.OpenClipboard(None):
            return False
        try:
            fmt = _user32.EnumClipboardFormats(0)
            while fmt:
                if fmt in _NON_TEXT_CLIPBOARD_FORMATS:
                    return True
                fmt = _user32.EnumClipboardFormats(fmt)
            return False
        finally:
            _user32.CloseClipboard()

    @staticmethod
    def _button_flag(button: str, down: bool) -> int:
        win_input = WindowsInputInjector._win_input()
        name = _BUTTON_FLAGS.get((button, down))
        if name is None:
            raise ValueError(f"未知按键：{button!r}")
        return getattr(win_input, name)


# ---------------------------------------------------------------------------
# 提权 / 安全桌面（C-07）
# ---------------------------------------------------------------------------


class WindowsSecureDesktopDetector:
    """检测当前是不是 **UAC 安全桌面**。

    判据：**输入桌面的名字**。正常运行时输入桌面叫 ``Default``；
    UAC 提权弹窗出现时，输入桌面被切到 ``Winlogon``（安全桌面）——
    那一刻**截不到也点不到**（C-07 的原话），所以必须**零动作下发、交给人**。

    ⚠️ **本类只检测，不降低**：仓库里**没有**任何改
    ``HKCU\\Control Panel\\Desktop\\PromptOnSecureDesktop`` 的路径，
    也不许加 —— 那等于关掉 UAC 的视觉提示（**降低安全性**），
    必须有人**书面同意**才存在（工单 M12 AC 6）。
    单测 ``reflection/test_reflection.py`` 扫描全仓把这条钉死。
    """

    def detect(self) -> ElevationState:
        """检测当前是不是 UAC 安全桌面。

        ⚠️ **fail-closed**：**读不到**输入桌面时按「提权」处理（``elevated=True``）——
        安全桌面上 ``OpenInputDesktop`` 会返回 ``ACCESS_DENIED``，
        那**正是**要判的那个场景。
        宁可停下交给人，也不盲目往一个看不见的桌面上打点。
        """
        handle = _user32.OpenInputDesktop(0, False, DESKTOP_READOBJECTS)
        if not handle:
            return self._undeterminable(
                f"OpenInputDesktop 失败（Win32 错误码 {ctypes.get_last_error()}）"
            )
        try:
            needed = wintypes.DWORD(0)
            _user32.GetUserObjectInformationW(
                handle, UOI_NAME, None, 0, ctypes.byref(needed)
            )
            if needed.value == 0:
                return self._undeterminable("读不到输入桌面的名字")
            buffer = ctypes.create_unicode_buffer(needed.value // ctypes.sizeof(wintypes.WCHAR) + 1)
            ok = _user32.GetUserObjectInformationW(
                handle,
                UOI_NAME,
                ctypes.byref(buffer),
                needed.value,
                ctypes.byref(needed),
            )
            if not ok:
                return self._undeterminable("读输入桌面名字失败")
            name = buffer.value or ""
        finally:
            _user32.CloseDesktop(handle)
        if name == SECURE_DESKTOP_NAME:
            return ElevationState(
                elevated=True,
                reason="输入桌面已切到 Winlogon（UAC 安全桌面）：截不到也点不到",
                desktop_name=name,
            )
        return ElevationState(
            elevated=False,
            reason=f"输入桌面是 {name or '（无名）'}，不是安全桌面",
            desktop_name=name,
        )

    @staticmethod
    def _undeterminable(detail: str) -> ElevationState:
        """**判不了**提权 → 按提权处理（fail-closed）。"""
        return ElevationState(
            elevated=True,
            reason=(
                f"{detail} —— 判不了提权，按提权处理（fail-closed："
                "宁可停下交给人，也不往看不见的桌面上打点）"
            ),
            desktop_name="",
        )


# ---------------------------------------------------------------------------
# 窗口控制
# ---------------------------------------------------------------------------


class WindowsWindowController:
    """置前与遮挡判定。

    句柄口径统一成**顶层窗口**（``GetAncestor(hwnd, GA_ROOT)``）：
    控件树给出的候选句柄可能是子窗口（按钮自己的 HWND），
    而命中测试给出的是顶层窗口——不归一化就会把子窗口候选恒判成「被遮挡」，
    随后对子窗口 ``SetForegroundWindow`` 必然失败。
    """

    def foreground_window(self) -> int | None:
        hwnd = _user32.GetForegroundWindow()
        return int(hwnd) if hwnd else None

    def is_foreground(self, hwnd: int) -> bool:
        """目标窗口此刻是不是前台窗口（子窗口按它的根窗口算）。"""
        foreground = self.foreground_window()
        if foreground is None:
            return False
        return self._root(foreground) == self._root(int(hwnd))

    def is_occluded(self, hwnd: int, point: tuple[int, int] = (0, 0)) -> bool:
        """目标窗口在 ``point`` 处是否被别的窗口压住。

        判据是**命中测试**：``WindowFromPoint`` 给出该点最上层的窗口，
        它的根窗口（``GA_ROOT``）不是目标窗口的根窗口就说明目标被压住了。
        不用矩形相交——那会把「最大化窗口的矩形盖住任务栏」误判成遮挡，
        而任务栏永远在最上层。
        """
        hit = _user32.WindowFromPoint(wintypes.POINT(int(point[0]), int(point[1])))
        if not hit:
            return False
        return self._root(hit) != self._root(hwnd)

    def bring_to_front(self, hwnd: int) -> bool:
        """把目标窗口置前（对顶层窗口操作；子窗口会被归一化到它的根窗口）。"""
        root = self._root(int(hwnd))
        if not root or not _user32.IsWindow(root):
            return False
        _user32.ShowWindow(root, SW_RESTORE)
        return bool(_user32.SetForegroundWindow(root))

    @staticmethod
    def _root(hwnd: int) -> int:
        """取顶层窗口（根窗口）；取不到就原样返回。"""
        if not hwnd:
            return 0
        return int(_user32.GetAncestor(hwnd, GA_ROOT) or hwnd)

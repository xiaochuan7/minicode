"""uia.py -- UI Automation 定点查询封装（纯标准库 + ctypes，零第三方依赖）。

为什么不用 uiautomation / pywinauto
-----------------------------------
本机没有装这两个包（``pip install uiautomation`` 可用但会引入 COM 依赖）。
这里直接用 ctypes 调 COM：``CoCreateInstance`` 拿到 ``IUIAutomation``，
再按 vtable 下标调用方法。

vtable 下标来自 Windows SDK 头文件 ``UIAutomationClient.h``（Win10 16299），
不是猜的：

* ``IUIAutomation``          3=CompareElements 4=CompareRuntimeIds 5=GetRootElement
                              6=ElementFromHandle 7=ElementFromPoint 8=GetFocusedElement
                              21=CreateTrueCondition 23=CreatePropertyCondition
                              25=CreateAndCondition 28=CreateOrCondition
* ``IUIAutomationElement``    5=FindFirst 6=FindAll 16=GetCurrentPattern
                              20=get_CurrentProcessId 21=get_CurrentControlType
                              23=get_CurrentName 29=get_CurrentAutomationId
                              30=get_CurrentClassName 36=get_CurrentNativeWindowHandle
                              38=get_CurrentIsOffscreen 43=get_CurrentBoundingRectangle
* ``IUIAutomationElementArray`` 3=get_Length 4=GetElement
* ``IUIAutomationInvokePattern`` 3=Invoke

关键设计
--------
* **只做定点查，不做全树 dump**。全桌面 ``FindAll`` 约 1000 个元素要 >= 8 秒；
  本模块所有查询都锚定在一个具体窗口（``ElementFromHandle``）上，
  或用具体属性条件（Name / AutomationId）让 provider 端做过滤。
* **递归子窗口**：``TreeScope_Descendants`` 会一路下钻，包括**不可见**的中间层
  （czdcode 实测：豆包有 18 个候选藏在不可见子窗口里，只看顶层窗口会全丢）。
* **"是否真的可用"看 ClassName 与可见性，不看候选数量**。裸 Qt 外壳
  （微信）只能给出 3 个边框按钮——数量少不等于不可用，数量多也不等于可用。
  判据见 :func:`is_usable`。
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes

if __package__:  # 包化：作为 minicode.exec 的一部分被导入
    from . import dpi  # noqa: F401  -- 保证进程 PMv2，UIA 坐标才是物理像素
else:  # 目录内直接运行时的平铺导入
    import dpi  # noqa: F401  -- 保证进程 PMv2，UIA 坐标才是物理像素

__all__ = [
    "UIAError", "Element", "UIA",
    "find_controls", "find_one", "enumerate_windows", "find_window",
    "is_usable", "assess_window",
    "CONTROL_TYPE_NAMES", "INTERACTIVE_CONTROL_TYPES",
]


class UIAError(RuntimeError):
    """UIA COM 调用失败。"""


# ---------------------------------------------------------------------------
# COM 基础设施
# ---------------------------------------------------------------------------


class GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", wintypes.DWORD),
        ("Data2", wintypes.WORD),
        ("Data3", wintypes.WORD),
        ("Data4", ctypes.c_ubyte * 8),
    ]

    def __str__(self) -> str:
        return "{%08X-%04X-%04X-%s-%s}" % (
            self.Data1, self.Data2, self.Data3,
            "".join("%02X" % b for b in self.Data4[:2]),
            "".join("%02X" % b for b in self.Data4[2:]),
        )


def _guid(text: str) -> GUID:
    hexs = text.replace("{", "").replace("}", "").replace("-", "")
    return GUID(
        int(hexs[0:8], 16), int(hexs[8:12], 16), int(hexs[12:16], 16),
        (ctypes.c_ubyte * 8)(*[int(hexs[i:i + 2], 16) for i in range(16, 32, 2)]),
    )


CLSID_CUIAutomation = _guid("{FF48DBA4-60EF-4201-AA87-54103EEF594E}")
IID_IUIAutomation = _guid("{30CBE57D-D9D0-452A-AB13-7AC5AC4825EE}")
IID_IUIAutomationElement = _guid("{D22108AA-8AC5-49A5-837B-37BBB3D7591E}")

ole32 = ctypes.WinDLL("ole32", use_last_error=True)
ole32.CoCreateInstance.argtypes = [ctypes.POINTER(GUID), ctypes.c_void_p,
                                   wintypes.DWORD, ctypes.POINTER(GUID),
                                   ctypes.POINTER(ctypes.c_void_p)]
ole32.CoCreateInstance.restype = ctypes.c_long
ole32.CoInitializeEx.argtypes = [ctypes.c_void_p, wintypes.DWORD]
ole32.CoInitializeEx.restype = ctypes.c_long
ole32.CoUninitialize.argtypes = []
ole32.CoUninitialize.restype = None

# BSTR 相关函数在 oleaut32.dll（OLE Automation），不在 ole32.dll
oleaut32 = ctypes.WinDLL("oleaut32", use_last_error=True)
oleaut32.SysAllocStringLen.argtypes = [ctypes.c_wchar_p, wintypes.UINT]
oleaut32.SysAllocStringLen.restype = ctypes.c_void_p
oleaut32.SysFreeString.argtypes = [ctypes.c_void_p]
oleaut32.SysFreeString.restype = None

HRESULT = ctypes.c_long
S_OK = 0

COINIT_APARTMENTTHREADED = 0x2
COINIT_DISABLE_OLE1DDE = 0x4
RPC_E_CHANGED_MODE = 0x80010106

# 关键：CoCreateInstance 之前**必须**初始化 COM。
# 未初始化时它返回 0x800401F0（CO_E_NOTINITIALIZED），
# 看起来像"类没注册"，其实只是没 CoInitializeEx。
_com_state = __import__("threading").local()


def ensure_com() -> bool:
    """在**当前线程**初始化 COM（STA）。每个线程都要单独初始化。"""
    hr = ole32.CoInitializeEx(None, COINIT_APARTMENTTHREADED | COINIT_DISABLE_OLE1DDE)
    if hr in (S_OK, RPC_E_CHANGED_MODE):
        if hr == S_OK:
            _com_state.count = getattr(_com_state, "count", 0) + 1
        return True
    return False


def release_com() -> None:
    count = getattr(_com_state, "count", 0)
    if count > 0:
        if count == 1:
            try:
                del _com_state.count
            except AttributeError:
                pass
        else:
            _com_state.count = count - 1
        ole32.CoUninitialize()


def _check(hr: int, what: str) -> None:
    if hr < 0:
        raise UIAError(f"{what} 失败, HRESULT=0x{hr & 0xFFFFFFFF:08X}")


def _method(ptr: int, index: int, restype, *argtypes):
    """取 COM 对象 vtable 第 index 个方法。"""
    vtbl = ctypes.cast(ptr, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
    proto = ctypes.WINFUNCTYPE(restype, ctypes.c_void_p, *argtypes)
    return proto(vtbl[index])


def _release(ptr: int) -> None:
    if ptr:
        # vtable 原型第一个参数是 this，必须传回去
        _method(ptr, 2, ctypes.c_ulong)(ptr)  # IUnknown::Release


# ---------------------------------------------------------------------------
# VARIANT / PROPERTYID
# ---------------------------------------------------------------------------


class _VARIANT_UNION(ctypes.Union):
    _fields_ = [
        ("bstrVal", ctypes.c_void_p),   # 真实 BSTR（必须由 SysAllocStringLen 分配）
        ("lVal", wintypes.LONG),
        ("iVal", ctypes.c_int),
        ("boolVal", wintypes.BOOL),
    ]


class VARIANT(ctypes.Structure):
    _anonymous_ = ("_v",)
    _fields_ = [
        ("vt", ctypes.c_ushort),        # VT_BSTR = 8
        ("wReserved1", ctypes.c_ushort),
        ("wReserved2", ctypes.c_ushort),
        ("wReserved3", ctypes.c_ushort),
        ("_v", _VARIANT_UNION),
    ]


class PROPERTYID(ctypes.Structure):
    _fields_ = [("pid", wintypes.DWORD)]


VT_BSTR = 8
VT_I4 = 3
VT_BOOL = 11


class _BSTR:
    """真实 BSTR 句柄（带长度前缀），用完必须 SysFreeString。"""

    __slots__ = ("ptr",)

    def __init__(self, text: str):
        self.ptr = oleaut32.SysAllocStringLen(text, len(text))
        if not self.ptr:
            raise UIAError("SysAllocStringLen 失败")

    def variant(self) -> VARIANT:
        v = VARIANT()
        v.vt = VT_BSTR
        v.bstrVal = self.ptr
        return v

    def close(self) -> None:
        if self.ptr:
            oleaut32.SysFreeString(self.ptr)
            self.ptr = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


# ---------------------------------------------------------------------------
# UIA 常量
# ---------------------------------------------------------------------------

TreeScope_Element = 0x1
TreeScope_Children = 0x2
TreeScope_Descendants = 0x4
TreeScope_Parent = 0x8
TreeScope_Ancestors = 0x10
TreeScope_Subtree = 0x7

UIA_BoundingRectanglePropertyId = 30001
UIA_ProcessIdPropertyId = 30002
UIA_ControlTypePropertyId = 30003
UIA_NamePropertyId = 30005
UIA_AutomationIdPropertyId = 30011
UIA_ClassNamePropertyId = 30012
UIA_NativeWindowHandlePropertyId = 30020
UIA_IsOffscreenPropertyId = 30022

UIA_InvokePatternId = 10000
UIA_ValuePatternId = 10002

CONTROL_TYPE_NAMES = {
    50000: "Button", 50001: "Calendar", 50002: "CheckBox", 50003: "ComboBox",
    50004: "Edit", 50005: "Hyperlink", 50006: "Image", 50007: "ListItem",
    50008: "List", 50009: "Menu", 50010: "MenuBar", 50011: "MenuItem",
    50012: "ProgressBar", 50013: "RadioButton", 50014: "ScrollBar",
    50015: "Slider", 50016: "Spinner", 50017: "StatusBar", 50018: "Tab",
    50019: "TabItem", 50020: "Text", 50021: "ToolBar", 50022: "ToolTip",
    50023: "Tree", 50024: "TreeItem", 50025: "Custom", 50026: "Group",
    50027: "Thumb", 50028: "DataGrid", 50029: "DataItem", 50030: "Document",
    50031: "SplitButton", 50032: "Window", 50033: "Pane", 50034: "Header",
    50035: "Footer", 50036: "TextBlock", 50037: "DataGridHeader",
    50038: "DataGridCell", 50039: "TitleBar", 50040: "Separator",
    50041: "SemanticZoom", 50042: "AppBar",
}

#: 真正可交互/可输入的控件类型。
INTERACTIVE_CONTROL_TYPES = {
    50000,  # Button
    50002,  # CheckBox
    50003,  # ComboBox
    50004,  # Edit
    50005,  # Hyperlink
    50007,  # ListItem
    50011,  # MenuItem
    50013,  # RadioButton
    50015,  # Slider
    50016,  # Spinner
    50019,  # TabItem
    50024,  # TreeItem
    50029,  # DataItem
    50030,  # Document
    50031,  # SplitButton
}

#: 纯容器类型——光有它们说明目标是个"外壳"。
CONTAINER_CONTROL_TYPES = {50025, 50026, 50032, 50033, 50008, 50009, 50010, 50021}


# ---------------------------------------------------------------------------
# Element
# ---------------------------------------------------------------------------


class RECT(ctypes.Structure):
    _fields_ = [("left", wintypes.LONG), ("top", wintypes.LONG),
                ("right", wintypes.LONG), ("bottom", wintypes.LONG)]


class Element:
    """一个 UIA 元素的轻量包装。坐标一律是**物理像素**。"""

    __slots__ = ("_ptr", "_released")

    def __init__(self, ptr: int):
        self._ptr = ptr
        self._released = False

    # -- 生命周期 ---------------------------------------------------------
    def release(self) -> None:
        if self._ptr and not self._released:
            _release(self._ptr)
            self._released = True
            self._ptr = 0

    def __del__(self):
        try:
            self.release()
        except Exception:
            pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.release()

    # -- 属性读取 ---------------------------------------------------------
    def _get_bstr(self, index: int) -> str:
        out = ctypes.c_void_p()
        hr = _method(self._ptr, index, HRESULT, ctypes.POINTER(ctypes.c_void_p))(
            self._ptr, ctypes.byref(out))
        _check(hr, "get_BSTR")
        if not out.value:
            return ""
        try:
            return ctypes.wstring_at(out.value)
        finally:
            oleaut32.SysFreeString(out.value)

    def _get_int(self, index: int) -> int:
        out = ctypes.c_int()
        hr = _method(self._ptr, index, HRESULT, ctypes.POINTER(ctypes.c_int))(
            self._ptr, ctypes.byref(out))
        _check(hr, "get_int")
        return int(out.value)

    def _get_bool(self, index: int) -> bool:
        out = wintypes.BOOL()
        hr = _method(self._ptr, index, HRESULT, ctypes.POINTER(wintypes.BOOL))(
            self._ptr, ctypes.byref(out))
        _check(hr, "get_bool")
        return bool(out.value)

    @property
    def name(self) -> str:
        return self._get_bstr(23)

    @property
    def automation_id(self) -> str:
        return self._get_bstr(29)

    @property
    def class_name(self) -> str:
        return self._get_bstr(30)

    @property
    def is_offscreen(self) -> bool:
        return self._get_bool(38)

    @property
    def native_window_handle(self) -> int:
        return self._get_int(36)

    @property
    def process_id(self) -> int:
        return self._get_int(20)

    @property
    def control_type(self) -> int:
        return self._get_int(21)

    @property
    def control_type_name(self) -> str:
        return CONTROL_TYPE_NAMES.get(self.control_type, f"Unknown({self.control_type})")

    @property
    def bounding_rect(self) -> RECT:
        """物理像素的包围盒。UIA 不用逻辑坐标，所以这里就是物理像素。"""
        r = RECT()
        hr = _method(self._ptr, 43, HRESULT, ctypes.POINTER(RECT))(
            self._ptr, ctypes.byref(r))
        _check(hr, "get_CurrentBoundingRectangle")
        return r

    @property
    def center(self) -> tuple[int, int]:
        r = self.bounding_rect
        return ((r.left + r.right) // 2, (r.top + r.bottom) // 2)

    # -- 查找子元素 -------------------------------------------------------
    def find_all(self, condition_ptr: int, scope: int = TreeScope_Descendants) -> list["Element"]:
        out = ctypes.c_void_p()
        hr = _method(self._ptr, 6, HRESULT, ctypes.c_int, ctypes.c_void_p,
                     ctypes.POINTER(ctypes.c_void_p))(
            self._ptr, int(scope), ctypes.c_void_p(condition_ptr), ctypes.byref(out))
        _check(hr, "FindAll")
        if not out.value:
            return []
        try:
            length = ctypes.c_int()
            _method(out.value, 3, HRESULT, ctypes.POINTER(ctypes.c_int))(
                out.value, ctypes.byref(length))
            elements = []
            get_element = _method(out.value, 4, HRESULT, ctypes.c_int,
                                  ctypes.POINTER(ctypes.c_void_p))
            for i in range(int(length.value)):
                el = ctypes.c_void_p()
                hr = get_element(out.value, i, ctypes.byref(el))
                if hr < 0 or not el.value:
                    continue
                elements.append(Element(el.value))
            return elements
        finally:
            _release(out.value)

    def find_first(self, condition_ptr: int, scope: int = TreeScope_Descendants) -> "Element | None":
        out = ctypes.c_void_p()
        hr = _method(self._ptr, 5, HRESULT, ctypes.c_int, ctypes.c_void_p,
                     ctypes.POINTER(ctypes.c_void_p))(
            self._ptr, int(scope), ctypes.c_void_p(condition_ptr), ctypes.byref(out))
        _check(hr, "FindFirst")
        if not out.value:
            return None
        return Element(out.value)

    # -- 模式 -------------------------------------------------------------
    def get_pattern(self, pattern_id: int) -> int:
        out = ctypes.c_void_p()
        hr = _method(self._ptr, 16, HRESULT, ctypes.c_int,
                     ctypes.POINTER(ctypes.c_void_p))(
            self._ptr, int(pattern_id), ctypes.byref(out))
        _check(hr, "GetCurrentPattern")
        return out.value or 0

    def invoke(self) -> bool:
        """走 InvokePattern 触发点击（比坐标点击更可靠）。"""
        pat = self.get_pattern(UIA_InvokePatternId)
        if not pat:
            return False
        try:
            hr = _method(pat, 3, HRESULT)(pat)
            _check(hr, "Invoke")
            return True
        finally:
            _release(pat)

    def set_value(self, text: str) -> bool:
        """走 ValuePattern 设置文本（比键盘输入更可靠）。"""
        pat = self.get_pattern(UIA_ValuePatternId)
        if not pat:
            return False
        try:
            with _BSTR(text) as b:
                # IUIAutomationValuePattern::SetValue = index 4
                hr = _method(pat, 4, HRESULT, ctypes.c_void_p)(pat, b.ptr)
                _check(hr, "SetValue")
                return True
        finally:
            _release(pat)

    def describe(self) -> dict:
        r = self.bounding_rect
        return {
            "name": self.name,
            "automation_id": self.automation_id,
            "class_name": self.class_name,
            "control_type": self.control_type_name,
            "rect": (r.left, r.top, r.right, r.bottom),
            "offscreen": self.is_offscreen,
            "hwnd": self.native_window_handle,
            "pid": self.process_id,
        }

    def __repr__(self) -> str:
        try:
            r = self.bounding_rect
            return (f"Element(name={self.name!r}, class={self.class_name!r}, "
                    f"type={self.control_type_name}, rect=({r.left},{r.top},"
                    f"{r.right},{r.bottom}))")
        except Exception:
            return "Element(<unreadable>)"


# ---------------------------------------------------------------------------
# UIA 主对象
# ---------------------------------------------------------------------------


class UIA:
    """``IUIAutomation`` 的薄封装。"""

    def __init__(self):
        ensure_com()
        self._ptr = ctypes.c_void_p()
        hr = ole32.CoCreateInstance(
            ctypes.byref(CLSID_CUIAutomation), None, 1,
            ctypes.byref(IID_IUIAutomation), ctypes.byref(self._ptr))
        _check(hr, "CoCreateInstance(CUIAutomation)")
        if not self._ptr.value:
            raise UIAError("CoCreateInstance 返回空指针")

    def __del__(self):
        try:
            if getattr(self, "_ptr", None) and self._ptr.value:
                _release(self._ptr.value)
                self._ptr.value = None
        except Exception:
            pass
        try:
            release_com()
        except Exception:
            pass

    # -- 根与元素 ---------------------------------------------------------
    def root(self) -> Element:
        out = ctypes.c_void_p()
        hr = _method(self._ptr.value, 5, HRESULT, ctypes.POINTER(ctypes.c_void_p))(
            self._ptr.value, ctypes.byref(out))
        _check(hr, "GetRootElement")
        return Element(out.value)

    def element_from_handle(self, hwnd: int) -> Element | None:
        out = ctypes.c_void_p()
        hr = _method(self._ptr.value, 6, HRESULT, ctypes.c_void_p,
                     ctypes.POINTER(ctypes.c_void_p))(
            self._ptr.value, ctypes.c_void_p(int(hwnd)), ctypes.byref(out))
        _check(hr, "ElementFromHandle")
        return Element(out.value) if out.value else None

    def element_at_point(self, x: int, y: int) -> Element | None:
        out = ctypes.c_void_p()
        hr = _method(self._ptr.value, 7, HRESULT, ctypes.c_double, ctypes.c_double,
                     ctypes.POINTER(ctypes.c_void_p))(
            self._ptr.value, ctypes.c_double(x), ctypes.c_double(y), ctypes.byref(out))
        _check(hr, "ElementAtPoint")
        return Element(out.value) if out.value else None

    def focused(self) -> Element | None:
        out = ctypes.c_void_p()
        hr = _method(self._ptr.value, 8, HRESULT, ctypes.POINTER(ctypes.c_void_p))(
            self._ptr.value, ctypes.byref(out))
        _check(hr, "GetFocusedElement")
        return Element(out.value) if out.value else None

    # -- 条件构造 ---------------------------------------------------------
    def _property_condition(self, prop_id: int, bstr_value: str) -> int:
        pid = PROPERTYID(int(prop_id))
        with _BSTR(bstr_value) as b:
            var = b.variant()
            out = ctypes.c_void_p()
            # 注意：PROPERTYID(4 字节) 和 VARIANT 都是**按值**传参。
            # 用 POINTER(...) + byref 会让被调方把"指针"当成值读，
            # 结果是 E_INVALIDARG(0x80070057)。
            # ctypes 按 MS x64 ABI 处理：4 字节结构进寄存器，16 字节 VARIANT 传指针。
            hr = _method(self._ptr.value, 23, HRESULT, PROPERTYID, VARIANT,
                         ctypes.POINTER(ctypes.c_void_p))(
                self._ptr.value, pid, var, ctypes.byref(out))
            _check(hr, "CreatePropertyCondition")
        return out.value or 0

    def _control_type_condition(self, control_type: int) -> int:
        pid = PROPERTYID(int(UIA_ControlTypePropertyId))
        var = VARIANT()
        var.vt = VT_I4
        var.lVal = int(control_type)
        out = ctypes.c_void_p()
        hr = _method(self._ptr.value, 23, HRESULT, PROPERTYID, VARIANT,
                     ctypes.POINTER(ctypes.c_void_p))(
            self._ptr.value, pid, var, ctypes.byref(out))
        _check(hr, "CreatePropertyCondition(ControlType)")
        return out.value or 0

    def _and_condition(self, conditions: list[int]) -> int:
        arr = (ctypes.c_void_p * len(conditions))(*conditions)
        out = ctypes.c_void_p()
        hr = _method(self._ptr.value, 25, HRESULT, ctypes.POINTER(ctypes.c_void_p),
                     ctypes.c_int, ctypes.POINTER(ctypes.c_void_p))(
            self._ptr.value, arr, len(conditions), ctypes.byref(out))
        _check(hr, "CreateAndCondition")
        return out.value or 0

    def build_condition(self, name=None, automation_id=None, control_type=None,
                        class_name=None) -> int:
        """把若干属性条件 AND 起来。全部为 None 时返回 TrueCondition。"""
        parts = []
        if name is not None:
            parts.append(self._property_condition(UIA_NamePropertyId, name))
        if automation_id is not None:
            parts.append(self._property_condition(UIA_AutomationIdPropertyId, automation_id))
        if class_name is not None:
            parts.append(self._property_condition(UIA_ClassNamePropertyId, class_name))
        if control_type is not None:
            parts.append(self._control_type_condition(control_type))
        if not parts:
            out = ctypes.c_void_p()
            hr = _method(self._ptr.value, 21, HRESULT,
                         ctypes.POINTER(ctypes.c_void_p))(
                self._ptr.value, ctypes.byref(out))
            _check(hr, "CreateTrueCondition")
            return out.value or 0
        if len(parts) == 1:
            return parts[0]
        return self._and_condition(parts)


# ---------------------------------------------------------------------------
# 顶层窗口枚举（纯 ctypes，不依赖 UIA）
# ---------------------------------------------------------------------------

user32 = ctypes.WinDLL("user32", use_last_error=True)
user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
user32.GetWindowTextLengthW.restype = ctypes.c_int
user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetWindowTextW.restype = ctypes.c_int
user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetClassNameW.restype = ctypes.c_int
user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.IsWindowVisible.restype = wintypes.BOOL
user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(RECT)]
user32.GetWindowRect.restype = wintypes.BOOL
user32.GetForegroundWindow.restype = wintypes.HWND
user32.IsWindow.argtypes = [wintypes.HWND]
user32.IsWindow.restype = wintypes.BOOL
user32.GetParent.argtypes = [wintypes.HWND]
user32.GetParent.restype = wintypes.HWND

_ENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)


class WindowInfo:
    """一个顶层窗口的快照。"""

    __slots__ = ("hwnd", "title", "class_name", "visible", "rect")

    def __init__(self, hwnd, title, class_name, visible, rect):
        self.hwnd = hwnd
        self.title = title
        self.class_name = class_name
        self.visible = visible
        self.rect = rect

    def to_dict(self):
        return {
            "hwnd": self.hwnd, "title": self.title, "class": self.class_name,
            "visible": self.visible,
            "rect": (self.rect.left, self.rect.top, self.rect.right, self.rect.bottom),
        }

    def __repr__(self):
        return f"WindowInfo(hwnd={self.hwnd}, {self.class_name!r}, {self.title!r})"


def enumerate_windows(visible_only: bool = True) -> list[WindowInfo]:
    """枚举顶层窗口（含不可见的，方便找隐藏宿主）。"""
    result: list[WindowInfo] = []

    def callback(hwnd, _lparam):
        length = user32.GetWindowTextLengthW(hwnd)
        buf = ctypes.create_unicode_buffer(max(length + 1, 2))
        user32.GetWindowTextW(hwnd, buf, len(buf))
        cbuf = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, cbuf, 256)
        visible = bool(user32.IsWindowVisible(hwnd))
        if not visible_only or visible:
            r = RECT()
            user32.GetWindowRect(hwnd, ctypes.byref(r))
            result.append(WindowInfo(int(hwnd), buf.value, cbuf.value, visible, r))
        return True

    user32.EnumWindows(_ENUMPROC(callback), 0)
    return result


def find_window(title_substr: str = "", class_substr: str = "",
                visible_only: bool = True) -> list[WindowInfo]:
    """按标题/类名子串找顶层窗口。"""
    out = []
    for w in enumerate_windows(visible_only=visible_only):
        if title_substr and title_substr.lower() not in w.title.lower():
            continue
        if class_substr and class_substr.lower() not in w.class_name.lower():
            continue
        out.append(w)
    return out


# ---------------------------------------------------------------------------
# 定点查询
# ---------------------------------------------------------------------------


#: 最近一次 find_controls() 的统计：命中总数、是否被 limit 截断。
LAST_FIND_STATS = {"total": 0, "limit": None, "truncated": False}


def find_controls(hwnd: int | None = None, name: str | None = None,
                  automation_id: str | None = None,
                  control_type: int | None = None,
                  class_name: str | None = None,
                  limit: int = 50, scope: int = TreeScope_Descendants,
                  uia: UIA | None = None) -> list[Element]:
    """定点查询控件。

    * 给了 ``hwnd``：以该窗口为根，``TreeScope_Descendants`` 递归下钻，
      会走进**不可见**的中间层（豆包 18 个候选就是这么找到的）。
    * 不给 ``hwnd``：从桌面根查，范围大、慢，只在不得已时用。
    """
    own = uia is None
    u = uia or UIA()
    try:
        if hwnd is not None:
            root = u.element_from_handle(hwnd)
            if root is None:
                return []
        else:
            root = u.root()
        cond = u.build_condition(name=name, automation_id=automation_id,
                                 control_type=control_type, class_name=class_name)
        try:
            found = root.find_all(cond, scope)
        finally:
            _release(cond)
        LAST_FIND_STATS["total"] = len(found)
        LAST_FIND_STATS["limit"] = limit
        if limit and len(found) > limit:
            for extra in found[limit:]:
                extra.release()
            found = found[:limit]
            LAST_FIND_STATS["truncated"] = True
        else:
            LAST_FIND_STATS["truncated"] = False
        return found
    finally:
        if own:
            del u


def find_one(hwnd: int | None = None, name: str | None = None,
             automation_id: str | None = None,
             control_type: int | None = None,
             class_name: str | None = None,
             scope: int = TreeScope_Descendants,
             uia: UIA | None = None) -> Element | None:
    """定点查询单个控件（FindFirst，快）。"""
    own = uia is None
    u = uia or UIA()
    try:
        if hwnd is not None:
            root = u.element_from_handle(hwnd)
            if root is None:
                return None
        else:
            root = u.root()
        cond = u.build_condition(name=name, automation_id=automation_id,
                                 control_type=control_type, class_name=class_name)
        try:
            return root.find_first(cond, scope)
        finally:
            _release(cond)
    finally:
        if own:
            del u


# ---------------------------------------------------------------------------
# "是否真的可用" 判定
# ---------------------------------------------------------------------------


def is_usable(element: Element, require_on_screen: bool = True,
              interactive_only: bool = False) -> tuple[bool, str]:
    """判断一个元素是不是**真正可操作**的目标。

    判据（**不按候选数量**）：

    1. 有名字或 AutomationId——否则无从定位；
    2. ClassName 非空——裸外壳/渲染层通常没有有意义的类名；
    3. 包围盒有面积；
    4. 包围盒与虚拟桌面有交集（滚出视野的元素不算）；
    5. ``interactive_only=True`` 时，控件类型必须属于可交互集合。

    返回 ``(是否可用, 原因)``。
    """
    try:
        name = element.name
        aid = element.automation_id
        cls = element.class_name
        ctype = element.control_type
        rect = element.bounding_rect
    except UIAError as exc:
        return False, f"read-failed: {exc}"

    if not name and not aid:
        return False, "no-name-and-no-automation-id"
    if not cls:
        return False, "empty-class-name"
    if rect.right <= rect.left or rect.bottom <= rect.top:
        return False, "empty-bounding-rect"

    if require_on_screen:
        ox, oy = dpi.virtual_desktop_origin()
        vw, vh = dpi.virtual_desktop_size()
        if rect.right <= ox or rect.left >= ox + vw or \
                rect.bottom <= oy or rect.top >= oy + vh:
            return False, "outside-virtual-desktop"

    if interactive_only and ctype not in INTERACTIVE_CONTROL_TYPES:
        return False, f"not-interactive({CONTROL_TYPE_NAMES.get(ctype, ctype)})"

    return True, "ok"


def assess_window(hwnd: int, max_controls: int = 200) -> dict:
    """评估一个窗口**是否值得用 UIA 驱动**。

    这是对"微信裸 Qt 外壳只有 3 个边框按钮"这类情况的正面回答：
    候选少不代表不可用，关键是**有没有真正的交互控件**。
    """
    info = {"hwnd": hwnd, "usable_controls": 0, "total": 0, "verdict": "unknown",
            "by_class": {}, "distinct_hwnds": 0}
    try:
        u = UIA()
    except UIAError as exc:
        info["verdict"] = f"uia-unavailable: {exc}"
        return info
    try:
        root = u.element_from_handle(hwnd)
        if root is None:
            info["verdict"] = "no-uia-element"
            return info
        info["window_name"] = root.name
        info["window_class"] = root.class_name
        cond = u.build_condition()
        try:
            all_elements = root.find_all(cond, TreeScope_Descendants)
        finally:
            _release(cond)
        info["total"] = len(all_elements)
        hwnds = set()
        for el in all_elements:
            try:
                cls = el.class_name
                info["by_class"][cls] = info["by_class"].get(cls, 0) + 1
                hwnds.add(el.native_window_handle)
                ok, _ = is_usable(el, interactive_only=True)
                if ok:
                    info["usable_controls"] += 1
            except UIAError:
                pass
            finally:
                el.release()
        # 命中的原生窗口数 > 1 说明**递归进了子窗口**（而不是只看顶层壳）
        info["distinct_hwnds"] = len(hwnds)
        if info["usable_controls"] >= 1:
            info["verdict"] = "uia-usable"
        elif info["total"] > 0:
            info["verdict"] = "shell-only"
        else:
            info["verdict"] = "no-uia-tree"
    except UIAError as exc:
        info["verdict"] = f"uia-error: {exc}"
    finally:
        del u
    return info


if __name__ == "__main__":
    import json
    wins = enumerate_windows()
    print(f"top-level visible windows: {len(wins)}")
    for w in wins[:15]:
        print(" ", w)


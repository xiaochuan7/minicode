"""真机 UIA 适配器（工单 M3）。

复用冻结原型 ``minicode.exec.uia`` 的 COM 接线（vtable 下标、属性条件、控件类型表），
本模块只补**冻结原型没有的那几件**：

* **控件框按 4 个 int 读** —— ``BoundingRectangle`` 是 ``RECT``（4 个 LONG），
  按 ``double`` 读会得到 1e-311 量级、四舍五入全 0 的非规格化数（架构决策备忘 §2.6 C-08）；
* **值类属性** —— 冻结原型的 ``Element`` **不暴露** ``Value`` / ``IsPassword`` /
  ``HelpText``，脱敏器的密码框规则在真机上要靠这里自己读 COM 属性才触发
  （``docs/01-现状.md`` §4 第 5 条）；
* **查询超时**（约 5 秒）与 **Invoke 优先**；
* **可用性探测**（递归子窗口 + 看 UIA ClassName，**不数候选数**）。

冻结原型一律**惰性导入** —— ``import minicode.uia.windows`` 本身不碰真桌面。
"""

from __future__ import annotations

import ctypes
import os
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from ctypes import wintypes

from minicode.action.ports import WindowAnchor

from .elements import UiaElement, make_element_id, name_matches, read_rect
from .errors import UiaQueryTimeout, UnconditionalQueryError
from .ports import TreeAssessment, UiaFindRequest, UiaFindResult, UnblockOutcome

__all__ = [
    "WindowsUiaQueryPort",
    "WindowsCapabilityProbe",
    "WindowsTreeUnblocker",
    "WindowsElementInvoker",
    "describe_window",
]

#: 查询超时阈值（秒）。架构决策备忘 §2.6 C-09：超时放宽到 ~5 s。
DEFAULT_TIMEOUT_S = 5.0

#: 探测时最多看多少个子窗口（Chromium 那种窗口树很宽）。
MAX_CHILD_WINDOWS = 40

# ---------------------------------------------------------------------------
# COM 小工具（只补冻结原型缺的那三个属性）
# ---------------------------------------------------------------------------

oleaut32 = ctypes.WinDLL("oleaut32", use_last_error=True)
oleaut32.SysFreeString.argtypes = [ctypes.c_void_p]
oleaut32.SysFreeString.restype = None

VT_BSTR, VT_I4, VT_BOOL = 8, 3, 11


class _VariantUnion(ctypes.Union):
    _fields_ = [
        ("bstrVal", ctypes.c_void_p),
        ("lVal", wintypes.LONG),
        ("iVal", ctypes.c_int),
        ("boolVal", wintypes.BOOL),
    ]


class _Variant(ctypes.Structure):
    _anonymous_ = ("_v",)
    _fields_ = [
        ("vt", ctypes.c_ushort),
        ("wReserved1", ctypes.c_ushort),
        ("wReserved2", ctypes.c_ushort),
        ("wReserved3", ctypes.c_ushort),
        ("_v", _VariantUnion),
    ]


class _PropertyId(ctypes.Structure):
    _fields_ = [("pid", ctypes.c_ulong)]


def _method(ptr: int, index: int, restype, *argtypes):
    """取 COM 对象 vtable 第 index 个方法。"""
    vtbl = ctypes.cast(ptr, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
    proto = ctypes.WINFUNCTYPE(restype, ctypes.c_void_p, *argtypes)
    return proto(vtbl[index])


def _release(ptr: int) -> None:
    """``IUnknown::Release``。"""
    if ptr:
        _method(ptr, 2, ctypes.c_ulong)(ptr)


#: ``IUIAutomationElement::GetCurrentPropertyValue`` 的 vtable 下标。
#: 冻结原型的 ``exec/uia.py`` 记着 ``FindFirst=5`` / ``FindAll=6`` / ``GetCurrentPattern=16``；
#: 按 ``UIAutomationClient.h`` 的方法顺序往下数（FindFirstBuildCache / FindAllBuildCache /
#: BuildCache / **GetCurrentPropertyValue** / GetCurrentPropertyValueEx / GetCachedPropertyValue /
#: GetCachedPropertyValueEx / GetCurrentPatternAs / GetCachedPatternAs / GetCurrentPattern）
#: 落在 **10**，与那两个已知下标自洽（5,6 之后 9 个方法 → 16 = GetCurrentPattern ✓）。
_GET_PROPERTY_INDEX = 10

#: 属性 ID（``UIAutomationClient.h`` 的常量，与 vtable 下标无关）。
#:
#: ⚠️ ``IsPassword`` 的 ID 是**在本机实测出来的**（不是抄来的）：临时造一个
#: ``ES_PASSWORD`` 的编辑框与一个普通编辑框，逐个 ID 读过去 —— **30019** 是
#: 唯一「密码框 True / 普通框 False」的布尔属性（30043 之类的读出来恒为 True，
#: 是别的布尔属性，读错会让**所有控件都被当成密码框**）。
UIA_VALUE_PROPERTY_ID = 30045
UIA_IS_PASSWORD_PROPERTY_ID = 30019
UIA_HELP_TEXT_PROPERTY_ID = 30013


def _get_property(ptr: int, property_id: int):
    """读一个 UIA 属性。失败抛 ``OSError``（HRESULT 带上）。"""
    variant = _Variant()
    hr = _method(
        ptr,
        _GET_PROPERTY_INDEX,
        ctypes.c_long,
        _PropertyId,
        ctypes.POINTER(_Variant),
    )(ptr, _PropertyId(property_id), ctypes.byref(variant))
    if hr < 0:
        raise OSError(
            f"GetCurrentPropertyValue(0x{property_id:X}) 失败 "
            f"HRESULT=0x{hr & 0xFFFFFFFF:08X}"
        )
    if variant.vt == VT_BSTR:
        raw = variant.bstrVal
        if not raw:
            return ""
        try:
            return ctypes.wstring_at(raw)
        finally:
            oleaut32.SysFreeString(raw)
    if variant.vt == VT_BOOL:
        return bool(variant.boolVal)
    if variant.vt == VT_I4:
        return int(variant.lVal)
    return None


def _element_ptr(element) -> int:
    """元素的原始 COM 指针。

    冻结原型的 ``Element`` 只在 ``__slots__`` 里留了它（``_ptr``），
    而它**没有**暴露 ``Value`` / ``IsPassword`` / ``HelpText`` —— 只能自己读。
    """
    ptr = int(getattr(element, "_ptr", 0) or 0)
    if not ptr:
        raise OSError("元素的 COM 指针为空")
    return ptr


def _read_is_password(element) -> bool | None:
    """是不是密码框。读不出来时返回 ``None``（**读不出来 ≠ 不是密码框**）。"""
    try:
        return bool(_get_property(_element_ptr(element), UIA_IS_PASSWORD_PROPERTY_ID))
    except OSError:
        return None


def _read_value(element) -> str:
    try:
        value = _get_property(_element_ptr(element), UIA_VALUE_PROPERTY_ID)
    except OSError:
        return ""
    return value if isinstance(value, str) else ""


def _read_help_text(element) -> str:
    try:
        value = _get_property(_element_ptr(element), UIA_HELP_TEXT_PROPERTY_ID)
    except OSError:
        return ""
    return value if isinstance(value, str) else ""


def _has_invoke(element) -> bool:
    """支不支持 InvokePattern。"""
    from minicode.exec import uia

    try:
        pattern = element.get_pattern(uia.UIA_InvokePatternId)
    except Exception:
        return False
    if not pattern:
        return False
    _release(int(pattern))
    return True


def _control_type_id(name: str | None) -> int | None:
    """控件类型名 → UIA 的控件类型 id。"""
    if not name:
        return None
    from minicode.exec import uia

    target = name.strip().lower()
    for type_id, type_name in uia.CONTROL_TYPE_NAMES.items():
        if type_name.lower() == target:
            return int(type_id)
    return None


def _name_matches(element_name: str, condition: str | None) -> bool:
    """名字条件按**包含**语义匹配（规则只有一份：:func:`name_matches`）。"""
    return name_matches(element_name, condition)


# ---------------------------------------------------------------------------
# 超时
# ---------------------------------------------------------------------------

_EXECUTOR = ThreadPoolExecutor(max_workers=8, thread_name_prefix="uia-query")


def _with_deadline(work, timeout_s: float):
    """在超时预算内跑一次 COM 调用；超时按**通道级失败**报。

    ⚠️ COM 调用一旦进去就**取消不掉**：超时时那个工作线程会继续占着
    （这是 UIA 访问层的固有性质，不是本项目的选择）。
    """

    def _call():
        return work()

    future = _EXECUTOR.submit(_call)
    try:
        return future.result(timeout=timeout_s)
    except FutureTimeout as exc:
        future.cancel()
        raise UiaQueryTimeout(f"查询超过 {timeout_s} 秒（通道级失败）") from exc


# ---------------------------------------------------------------------------
# 端口：定点查询
# ---------------------------------------------------------------------------


class WindowsUiaQueryPort:
    """真机定点查询端口。

    * 只发**带属性条件**的查询；无条件查询直接拒绝；
    * 精确名字条件查不到时，退到「控件类型条件 + 名字包含」的扫描
      （仍然带属性条件，不是全树 dump）；
    * 每次观测**重新生成**元素 id（上次观测的 id 不解析）。
    """

    def __init__(self, timeout_s: float = DEFAULT_TIMEOUT_S):
        self.timeout_s = float(timeout_s)
        self._sequence = 0
        self._live: dict[str, object] = {}
        #: 最近一次定点查询的耗时（毫秒）。
        self.last_query_ms = 0.0

    # -- 端口 -------------------------------------------------------------

    def find(self, request: UiaFindRequest) -> UiaFindResult:
        if not request.has_condition:
            raise UnconditionalQueryError("无条件全树查询被拒绝（只做定点查询）")
        started = time.perf_counter()
        elements, unmatched = _with_deadline(lambda: self._find(request), self.timeout_s)
        elapsed = (time.perf_counter() - started) * 1000.0
        self.last_query_ms = elapsed
        return UiaFindResult(
            elements=elements, elapsed_ms=elapsed, unmatched_count=unmatched
        )

    def has_nodes(self, anchor: WindowAnchor) -> bool:
        """树里有没有节点（取第一个节点，不是全树遍历）。"""
        return _with_deadline(lambda: self._has_nodes(anchor), self.timeout_s)

    def live_element(self, element_id: str):
        """按元素 id 取**最近一次观测**的 COM 元素（Invoke 用）。"""
        return self._live.get(element_id)

    # -- 内部 -------------------------------------------------------------

    def _has_nodes(self, anchor: WindowAnchor) -> bool:
        from minicode.exec import uia

        controller = uia.UIA()
        try:
            root = controller.element_from_handle(anchor.hwnd)
            if root is None:
                return False
            condition = controller.build_condition()
            try:
                first = root.find_first(condition, uia.TreeScope_Descendants)
            finally:
                _release(condition)
            if first is None:
                return False
            first.release()
            return True
        finally:
            del controller

    def _find(self, request: UiaFindRequest) -> tuple[tuple[UiaElement, ...], int]:
        from minicode.exec import uia

        controller = uia.UIA()
        try:
            root = controller.element_from_handle(request.anchor.hwnd)
            if root is None:
                return (), 0
            matched, unmatched = self._query(controller, root, request)
            return self._publish(matched), unmatched
        finally:
            del controller

    def _query(self, controller, root, request: UiaFindRequest) -> tuple[list, int]:
        """定点查询：**一条属性条件**一次查询（名字在 Python 侧过滤）。

        ⚠️ **不做 AND 条件**（名字 + 控件类型合成一个条件）：
        冻结原型的 ``build_condition`` 在两个条件并存时会走 ``CreateAndCondition``，
        **在本机崩**（access violation，见 ``docs/01-现状.md`` §6 第 6 条）。
        因此这里一律用**单条件**查询 + 名字包含过滤：

        1. 自动化标识（给了才查）；
        2. 精确名字条件；
        3. 控件类型条件（精确名字查不到时的退路）。

        返回 ``(与名字对上的元素, 名字没对上的元素数)`` —— 第二个数把
        「名称匹配不上」与「目标不在树里」分开（工单 M9 AC 2）：
        前者是**选择失败**（禁止切视觉），后者是**覆盖失败**（允许切视觉）。
        """
        attempts: list[dict] = []
        if request.automation_id:
            attempts.append({"automation_id": request.automation_id})
        if request.name:
            attempts.append({"name": request.name})
        type_id = _control_type_id(request.control_type)
        if type_id is not None:
            attempts.append({"control_type": type_id})
        unmatched = 0
        for kwargs in attempts:
            found = self._find_with(controller, root, kwargs)
            matched = [
                element
                for element in found
                if _name_matches(element.name or "", request.name)
            ]
            for element in found:
                if element not in matched:
                    element.release()
            unmatched += len(found) - len(matched)
            if matched:
                return matched, unmatched
        return [], unmatched

    def _find_with(self, controller, root, kwargs: dict) -> list:
        """按**一条**属性条件查一次（控件类型 / 名字 / 自动化标识）。"""
        from minicode.exec import uia

        condition = controller.build_condition(**kwargs)
        try:
            return list(root.find_all(condition, uia.TreeScope_Descendants))
        finally:
            _release(condition)

    def _publish(self, elements: list) -> tuple[UiaElement, ...]:
        """把 COM 元素转成读数，并**重新生成**元素 id。"""
        for old in self._live.values():
            try:
                old.release()
            except Exception:  # pragma: no cover - COM 释放失败不影响本次读数
                pass
        self._live = {}
        self._sequence += 1
        prefix = f"obs{self._sequence}"
        out: list[UiaElement] = []
        for index, element in enumerate(elements):
            element_id = make_element_id(prefix, index)
            self._live[element_id] = element
            out.append(self._to_element(element, element_id))
        return tuple(out)

    def _to_element(self, element, element_id: str) -> UiaElement:

        rect_struct = element.bounding_rect
        rect = read_rect(
            (
                rect_struct.left,
                rect_struct.top,
                rect_struct.right,
                rect_struct.bottom,
            )
        )
        is_password = _read_is_password(element)
        # fail-closed：**读不出 IsPassword 就不读 Value**（宁可当成密码框）。
        sensitive = is_password is not False
        return UiaElement(
            name=element.name or "",
            control_type=element.control_type_name,
            class_name=element.class_name or "",
            automation_id=element.automation_id or None,
            rect=rect if rect is not None else (0, 0, 0, 0),
            window_handle=int(element.native_window_handle) or None,
            element_id=element_id,
            is_password=bool(sensitive),
            value="" if sensitive else _read_value(element),
            help_text=_read_help_text(element),
            supports_invoke=_has_invoke(element),
        )


# ---------------------------------------------------------------------------
# 端口：可用性探测
# ---------------------------------------------------------------------------


class WindowsCapabilityProbe:
    """可用性探测：**递归子窗口 + 看 UIA ClassName**（不数候选数）。

    ⚠️ 这里**允许**按窗口遍历（无条件的 ``FindAll``）：判定姿势本身就是
    「递归子窗口 + 看 ClassName」，不遍历就看不到类名。
    它与动作路径的禁令不冲突：

    * 动作路径（:class:`~minicode.uia.ports.UiaQueryPort`）**永远**带属性条件；
    * 探测每个「应用 + 版本」只跑一次（静态缓存），且**不参与动作路径**。
    """

    def __init__(self, timeout_s: float = DEFAULT_TIMEOUT_S):
        self.timeout_s = float(timeout_s)

    def assess(self, anchor: WindowAnchor) -> TreeAssessment:
        return _with_deadline(lambda: self._assess(anchor), self.timeout_s)

    def _assess(self, anchor: WindowAnchor) -> TreeAssessment:
        from minicode.exec import uia

        windows = [anchor.hwnd, *_child_windows(anchor.hwnd)]
        class_names: list[str] = []
        interactive: list[str] = []
        names: list[str] = []
        seen: set[tuple[int, str, int, int]] = set()
        usable_controls = 0
        has_automation_id = False
        has_tree = False
        controller = uia.UIA()
        try:
            for hwnd in windows:
                try:
                    root = controller.element_from_handle(hwnd)
                except Exception:
                    continue
                if root is None:
                    continue
                condition = controller.build_condition()
                try:
                    elements = list(root.find_all(condition, uia.TreeScope_Descendants))
                except Exception:
                    continue
                finally:
                    _release(condition)
                has_tree = has_tree or bool(elements)
                for element in elements:
                    try:
                        class_name = element.class_name or ""
                        if class_name and class_name not in class_names:
                            class_names.append(class_name)
                        if element.control_type in uia.INTERACTIVE_CONTROL_TYPES:
                            if class_name and class_name not in interactive:
                                interactive.append(class_name)
                            name = element.name or ""
                            # 同一个元素会**同时**出现在父窗口与子窗口的子树里，
                            # 不去重就会把「同名撞车」数虚（父窗口数一遍、子窗口再数一遍）。
                            key = (
                                int(element.native_window_handle),
                                name,
                                element.bounding_rect.left,
                                element.bounding_rect.top,
                            )
                            if name and key not in seen:
                                seen.add(key)
                                names.append(name)
                            # **裸外壳只有 3 个边框按钮**的情形就是靠这一关判出来的：
                            # 边框按钮过不了 is_usable（有名字或自动化标识 + 类名非空 +
                            # 控件框有面积 + 在屏幕上 + 属于可交互控件类型）。
                            usable, _ = uia.is_usable(element, interactive_only=True)
                            if usable:
                                usable_controls += 1
                        if element.automation_id:
                            has_automation_id = True
                    except Exception:
                        continue
                    finally:
                        element.release()
        finally:
            del controller
        collisions = sum(
            count - 1 for count in Counter(names).values() if count > 1
        )
        return TreeAssessment(
            has_tree=has_tree,
            class_names=tuple(class_names),
            interactive_class_names=tuple(interactive),
            usable_controls=usable_controls,
            has_automation_id=has_automation_id,
            name_collisions=collisions,
            child_windows=len(windows) - 1,
        )


# ---------------------------------------------------------------------------
# 端口：解门控 / Invoke
# ---------------------------------------------------------------------------


class WindowsTreeUnblocker:
    """解门控的已知手段：**把窗口激活**（置前）。"""

    def unblock(self, anchor: WindowAnchor) -> UnblockOutcome:
        from minicode.action.windows import WindowsWindowController

        try:
            raised = WindowsWindowController().bring_to_front(anchor.hwnd)
        except Exception:
            return UnblockOutcome.FAILED
        return UnblockOutcome.UNBLOCKED if raised else UnblockOutcome.FAILED


class WindowsElementInvoker:
    """按元素 id 走 InvokePattern（**Invoke 优先**于坐标点击）。"""

    def __init__(self, port: WindowsUiaQueryPort):
        self._port = port

    def invoke(self, element_id: str) -> bool:
        element = self._port.live_element(element_id)
        if element is None:
            return False
        try:
            return bool(element.invoke())
        except Exception:
            return False


# ---------------------------------------------------------------------------
# 窗口与应用（能力表的键）
# ---------------------------------------------------------------------------

_user32 = ctypes.WinDLL("user32", use_last_error=True)
_user32.GetWindowThreadProcessId.argtypes = [
    wintypes.HWND,
    ctypes.POINTER(wintypes.DWORD),
]
_user32.GetWindowThreadProcessId.restype = wintypes.DWORD
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
_kernel32.OpenProcess.restype = wintypes.HANDLE
_kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
_kernel32.CloseHandle.restype = wintypes.BOOL
#: ``QueryFullProcessImageNameW`` 在 **kernel32**（不在 user32）。
_kernel32.QueryFullProcessImageNameW.argtypes = [
    wintypes.HANDLE,
    wintypes.DWORD,
    wintypes.LPWSTR,
    ctypes.POINTER(wintypes.DWORD),
]
_kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL

_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


def _process_id(hwnd: int) -> int:
    pid = wintypes.DWORD()
    _user32.GetWindowThreadProcessId(wintypes.HWND(int(hwnd)), ctypes.byref(pid))
    return int(pid.value)


def _process_image(pid: int) -> str:
    handle = _kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return ""
    try:
        size = wintypes.DWORD(32768)
        buffer = ctypes.create_unicode_buffer(size.value)
        if not _kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
            return ""
        return buffer.value
    finally:
        _kernel32.CloseHandle(handle)


class _VsFixedFileInfo(ctypes.Structure):
    """``VS_FIXEDFILEINFO``（版本资源里的定长部分）。"""

    _fields_ = [
        ("dwSignature", ctypes.c_ulong),
        ("dwStrucVersion", ctypes.c_ulong),
        ("dwFileVersionMS", ctypes.c_ulong),
        ("dwFileVersionLS", ctypes.c_ulong),
        ("dwProductVersionMS", ctypes.c_ulong),
        ("dwProductVersionLS", ctypes.c_ulong),
        ("dwFileFlagsMask", ctypes.c_ulong),
        ("dwFileFlags", ctypes.c_ulong),
        ("dwFileOS", ctypes.c_ulong),
        ("dwFileType", ctypes.c_ulong),
        ("dwFileSubtype", ctypes.c_ulong),
        ("dwFileDateMS", ctypes.c_ulong),
        ("dwFileDateLS", ctypes.c_ulong),
    ]


def _file_version(path: str) -> str:
    """可执行文件的文件版本（能力表的键要带版本）。"""
    if not path:
        return ""
    version = ctypes.WinDLL("version", use_last_error=True)
    version.GetFileVersionInfoSizeW.argtypes = [
        wintypes.LPCWSTR,
        ctypes.POINTER(wintypes.DWORD),
    ]
    version.GetFileVersionInfoSizeW.restype = wintypes.DWORD
    version.GetFileVersionInfoW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.c_void_p,
    ]
    version.GetFileVersionInfoW.restype = wintypes.BOOL
    version.VerQueryValueW.argtypes = [
        ctypes.c_void_p,
        wintypes.LPCWSTR,
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(wintypes.UINT),
    ]
    version.VerQueryValueW.restype = wintypes.BOOL

    handle = wintypes.DWORD(0)
    size = version.GetFileVersionInfoSizeW(path, ctypes.byref(handle))
    if not size:
        return ""
    buffer = ctypes.create_string_buffer(size)
    if not version.GetFileVersionInfoW(path, 0, size, ctypes.byref(buffer)):
        return ""
    pointer = ctypes.c_void_p()
    length = wintypes.UINT(0)
    if not version.VerQueryValueW(
        ctypes.byref(buffer), "\\", ctypes.byref(pointer), ctypes.byref(length)
    ):
        return ""
    info = ctypes.cast(pointer, ctypes.POINTER(_VsFixedFileInfo)).contents
    most = int(info.dwFileVersionMS)
    least = int(info.dwFileVersionLS)
    return f"{most >> 16}.{most & 0xFFFF}.{least >> 16}.{least & 0xFFFF}"


def describe_window(hwnd: int) -> WindowAnchor:
    """窗口 → 能力表的键（**应用 + 版本**）。"""
    image = _process_image(_process_id(hwnd))
    app = os.path.basename(image) if image else ""
    return WindowAnchor(hwnd=int(hwnd), app=app, version=_file_version(image))


def _child_windows(hwnd: int) -> list[int]:
    """递归子窗口（``EnumChildWindows`` 本身就是递归的）。"""
    result: list[int] = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def _callback(child, _lparam):
        result.append(int(child))
        return True

    _user32.EnumChildWindows(wintypes.HWND(int(hwnd)), _callback, 0)
    return result[:MAX_CHILD_WINDOWS]

"""真机 UIA 适配器（工单 M3）。

复用冻结原型 ``minicode.exec.uia`` 的 COM 接线（vtable 下标、属性条件、控件类型表），
本模块只补**冻结原型没有的那几件**：

* **控件框按 4 个 int 读** —— ``BoundingRectangle`` 是 ``RECT``（4 个 LONG），
  按 ``double`` 读会得到 1e-311 量级、四舍五入全 0 的非规格化数；
* **值类属性** —— 冻结原型的 ``Element`` **不暴露** ``Value`` / ``IsPassword`` /
  ``HelpText``，脱敏器的密码框规则在真机上要靠这里自己读 COM 属性才触发；
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

#: 查询超时阈值（秒）。超时放宽到 ~5 s。
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
#: ``IsEnabled``（控件当前**可用**吗）。工单 M11 的「某个按钮变成不可用」
#: 要靠它判定（可访问性树查**状态**）。
#:
#: ⚠️ 这个 ID 是**在本机实测出来的**（不是抄来的）：2026-10-21 造了两个原生
#: Win32 按钮（一个可用、一个 ``EnableWindow(FALSE)``），用
#: ``GetCurrentPropertyValue`` 把 30000~30060 逐个读过去 ——
#: **30010** 是唯一给出「可用=True / 不可用=False」的 ID
#: （与 ``IsWindowEnabled`` 的读数一致）。
#: ⚠️ 头文件里常见的那个号（30022）在本机读出来**恒为 False**（是别的属性）。
UIA_IS_ENABLED_PROPERTY_ID = 30010

# ---------------------------------------------------------------------------
# 模式（pattern）与它们的 vtable 下标
# ---------------------------------------------------------------------------
#
# ⚠️ **模式 id 与属性 id 是两套号**（``UIAutomationClient.h``）：
# ``UIA_PATTERN_ID`` 从 10000 起，``UIA_PROPERTY_ID`` 从 30000 起。
# ⚠️ **Selection = 10001**（10006 是 Grid —— 别照抄错值）。

#: ``SelectionPattern``（**容器**的选中项）。
UIA_SELECTION_PATTERN_ID = 10001
#: ``TextPattern``。
UIA_TEXT_PATTERN_ID = 10014
#: ``LegacyIAccessiblePattern``（老控件的值兜底）。
UIA_LEGACY_IACCESSIBLE_PATTERN_ID = 10018

#: ``IUIAutomationSelectionPattern::GetCurrentSelection`` 的 vtable 下标：
#: IUnknown 占 0~2，它是该接口的**第一个**方法 → **3**。
_SELECTION_GET_SELECTION_INDEX = 3
#: ``IUIAutomationElementArray``：``get_Length`` = **3**、``GetElement`` = **4**
#: （与冻结原型 ``exec/uia.py`` 的 ``find_all`` 用法自洽）。
_ELEMENT_ARRAY_LENGTH_INDEX = 3
_ELEMENT_ARRAY_GET_ELEMENT_INDEX = 4
#: ``IUIAutomationLegacyIAccessiblePattern::get_CurrentValue`` 的下标：
#: Select=3 / DoDefaultAction=4 / SetValue=5 / get_CurrentChildId=6 /
#: get_CurrentName=7 / **get_CurrentValue=8**。
_LEGACY_GET_VALUE_INDEX = 8
#: ``IUIAutomationTextPattern::get_DocumentRange`` 的下标：
#: RangeFromPoint=3 / RangeFromChild=4 / GetSelection=5 / GetVisibleRanges=6 /
#: **get_DocumentRange=7**。
_TEXT_GET_DOCUMENT_RANGE_INDEX = 7
#: ``IUIAutomationTextRange::GetText`` 的下标：
#: Clone=3 / Compare=4 / CompareEndpoints=5 / ExpandToEnclosingUnit=6 /
#: FindAttribute=7 / FindText=8 / GetAttributeValue=9 / GetBoundingRectangles=10 /
#: GetEnclosingElement=11 / **GetText=12**。
_TEXT_RANGE_GET_TEXT_INDEX = 12
#: ``IUIAutomationElement::get_CurrentName`` 的下标
#: （与冻结原型 ``exec/uia.py`` 的 ``Element.name`` 自洽）。
_ELEMENT_GET_NAME_INDEX = 23
#: ``TextRange.GetText`` 一次最多读多少个字符（读多了没意义，且会撑大 BSTR）。
_TEXT_MAX_LENGTH = 512


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
    """控件的值：**兜底链**（按顺序，读到第一个非空就停）。

    1. **选中项的 Name**（``SelectionPattern.GetSelection()``）——
       下拉框读出来就是「**当前选中的那一项**」（如「150 ％ (推荐)」）。
       ⚠️ ``SelectionItemPattern`` **本身没有数值**，有数值的是**容器**的
       ``SelectionPattern``（先取回选中项，再读它的 Name）。
    2. **ValuePattern 的值**（属性 30045）—— 编辑框正文那些。
    3. **LegacyIAccessiblePattern.Value** —— 老控件的值。
    4. **TextPattern.DocumentRange.GetText()** —— 文本类控件的正文。

    读不到就是空串（**读不到 ≠ 没有值**，调用方自己决定怎么记）。
    """
    for reader in (
        _read_selection_name,
        _read_value_property,
        _read_legacy_value,
        _read_text_value,
    ):
        try:
            value = reader(element)
        except Exception:
            value = ""
        if value:
            return value
    return ""


def _read_value_property(element) -> str:
    """ValuePattern 的值（属性 30045）。"""
    try:
        value = _get_property(_element_ptr(element), UIA_VALUE_PROPERTY_ID)
    except OSError:
        return ""
    return value if isinstance(value, str) else ""


def _read_element_name(ptr: int) -> str:
    """读一个元素的 Name（``IUIAutomationElement::get_CurrentName``）。"""
    out = ctypes.c_void_p()
    hr = _method(
        ptr,
        _ELEMENT_GET_NAME_INDEX,
        ctypes.c_long,
        ctypes.POINTER(ctypes.c_void_p),
    )(ptr, ctypes.byref(out))
    if hr < 0 or not out.value:
        return ""
    try:
        return ctypes.wstring_at(out.value)
    finally:
        oleaut32.SysFreeString(out.value)


def _read_selection_name(element) -> str:
    """**选中项的 Name**（容器走 ``SelectionPattern``）。

    ⚠️ ``SelectionItemPattern`` **没有数值** —— 有数值的是**容器**的
    ``SelectionPattern.GetSelection()``：取回选中项，再读它的 Name。
    下拉框（ComboBox）读出来就是「当前选中的那一项」的文字。
    """
    pattern = _get_pattern(element, UIA_SELECTION_PATTERN_ID)
    if not pattern:
        return ""
    try:
        array = ctypes.c_void_p()
        hr = _method(
            pattern,
            _SELECTION_GET_SELECTION_INDEX,
            ctypes.c_long,
            ctypes.POINTER(ctypes.c_void_p),
        )(pattern, ctypes.byref(array))
        if hr < 0 or not array.value:
            return ""
        try:
            length = ctypes.c_int()
            hr = _method(
                array.value,
                _ELEMENT_ARRAY_LENGTH_INDEX,
                ctypes.c_long,
                ctypes.POINTER(ctypes.c_int),
            )(array.value, ctypes.byref(length))
            if hr < 0 or length.value <= 0:
                return ""
            item = ctypes.c_void_p()
            hr = _method(
                array.value,
                _ELEMENT_ARRAY_GET_ELEMENT_INDEX,
                ctypes.c_long,
                ctypes.c_int,
                ctypes.POINTER(ctypes.c_void_p),
            )(array.value, 0, ctypes.byref(item))
            if hr < 0 or not item.value:
                return ""
            try:
                return _read_element_name(item.value)
            finally:
                _release(item.value)
        finally:
            _release(array.value)
    finally:
        _release(pattern)


def _read_legacy_value(element) -> str:
    """``LegacyIAccessiblePattern`` 的 Value（老控件的值兜底）。"""
    pattern = _get_pattern(element, UIA_LEGACY_IACCESSIBLE_PATTERN_ID)
    if not pattern:
        return ""
    try:
        out = ctypes.c_void_p()
        hr = _method(
            pattern,
            _LEGACY_GET_VALUE_INDEX,
            ctypes.c_long,
            ctypes.POINTER(ctypes.c_void_p),
        )(pattern, ctypes.byref(out))
        if hr < 0 or not out.value:
            return ""
        try:
            return ctypes.wstring_at(out.value)
        finally:
            oleaut32.SysFreeString(out.value)
    finally:
        _release(pattern)


def _read_text_value(element) -> str:
    """``TextPattern`` 的 ``DocumentRange.GetText()``（文本类控件的正文）。"""
    pattern = _get_pattern(element, UIA_TEXT_PATTERN_ID)
    if not pattern:
        return ""
    try:
        rng = ctypes.c_void_p()
        hr = _method(
            pattern,
            _TEXT_GET_DOCUMENT_RANGE_INDEX,
            ctypes.c_long,
            ctypes.POINTER(ctypes.c_void_p),
        )(pattern, ctypes.byref(rng))
        if hr < 0 or not rng.value:
            return ""
        try:
            out = ctypes.c_void_p()
            hr = _method(
                rng.value,
                _TEXT_RANGE_GET_TEXT_INDEX,
                ctypes.c_long,
                ctypes.c_int,
                ctypes.POINTER(ctypes.c_void_p),
            )(rng.value, _TEXT_MAX_LENGTH, ctypes.byref(out))
            if hr < 0 or not out.value:
                return ""
            try:
                return ctypes.wstring_at(out.value)
            finally:
                oleaut32.SysFreeString(out.value)
        finally:
            _release(rng.value)
    finally:
        _release(pattern)


def _get_pattern(element, pattern_id: int) -> int:
    """取一个模式的 COM 指针（**没有就是 0**）。

    样板沿用冻结原型 ``exec/uia.py`` 的 ``get_pattern``（**不引入新依赖**）。
    """
    try:
        return int(element.get_pattern(pattern_id) or 0)
    except Exception:
        return 0


def _read_help_text(element) -> str:
    try:
        value = _get_property(_element_ptr(element), UIA_HELP_TEXT_PROPERTY_ID)
    except OSError:
        return ""
    return value if isinstance(value, str) else ""


def _read_is_enabled(element) -> bool | None:
    """控件当前**可用**吗。读不出来时返回 ``None``（**读不到 ≠ 不可用**）。"""
    try:
        return bool(_get_property(_element_ptr(element), UIA_IS_ENABLED_PROPERTY_ID))
    except OSError:
        return None


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


def _window_process_ids(hwnd: int) -> set[int]:
    """这个窗口**与它的子窗口**的进程 id 集合。

    ⚠️ **XAML island 的控件注册在子窗口（CoreWindow）那个进程下**，
    不是外层那个：本机实测（Windows 设置，2026-10-22）——
    外层 ``ApplicationFrameWindow`` 的 pid=14712 在桌面根上只有 **8** 个元素，
    而 ``Windows.UI.Core.CoreWindow`` 的 pid=4116 有 **132** 个
    （就是「系统」「屏幕」「搜索框，查找设置」那些）。
    ⇒ **只按外层窗口的 pid 过滤，root-walk 会一个都找不到。**
    """
    pids = {_process_id(hwnd)}
    for child in _child_windows(hwnd):
        pid = _process_id(child)
        if pid:
            pids.add(pid)
    return pids


def _root_walk_by_pid(pids: set[int] | int) -> list:
    """**桌面根遍历** → **按 PID 过滤**（XAML island 的兜底）。

    Windows 设置、计算器这类 **CoreWindow** 应用的 XAML 树
    **注册在桌面根上**（与窗口**同进程**，见 :func:`_window_process_ids`），
    **不在窗口句柄下** —— 窗口那条路读到的树是空的
    （工单 M14 的 W-1.1 / W-1.2 就栽在这里）。

    ⚠️ **只用单条件**（``TrueCondition``）：冻结原型的 ``build_condition``
    两个条件并存会走 ``CreateAndCondition``，**在本机崩** access violation。
    ⚠️ **先按 PID 过滤，再进 Python 侧的名字匹配**（桌面树巨大，
    过滤必须在 Python 侧做完，不能带着整棵树去问名字）。

    返回的是**那些进程的元素**（调用方负责 :meth:`release`）。
    """
    from minicode.exec import uia

    wanted = {int(pids)} if isinstance(pids, int) else {int(pid) for pid in pids}
    controller = uia.UIA()
    try:
        root = controller.root()
        try:
            condition = controller.build_condition()
            try:
                elements = list(root.find_all(condition, uia.TreeScope_Descendants))
            finally:
                _release(condition)
        finally:
            root.release()
        mine: list = []
        for element in elements:
            try:
                if element.process_id in wanted:
                    mine.append(element)
                    continue
            except Exception:
                pass
            element.release()
        return mine
    finally:
        del controller


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
    * **窗口那条路查不到时**走**桌面根遍历兜底**（XAML island 的树不在
      窗口句柄下）—— 见 :meth:`_find_root_walk`；
    * 每次观测**重新生成**元素 id（上次观测的 id 不解析）。
    """

    def __init__(self, timeout_s: float = DEFAULT_TIMEOUT_S):
        self.timeout_s = float(timeout_s)
        self._sequence = 0
        self._live: dict[str, object] = {}
        #: 最近一次定点查询的耗时（毫秒）。
        self.last_query_ms = 0.0
        #: **窗口标题缓存**（按根窗口句柄）—— 一次查询里的元素多半在同一个窗口上，
        #: 逐个元素去问系统太浪费（每个元素两次 Win32 调用）。
        self._titles: dict[int, str] = {}

    # -- 端口 -------------------------------------------------------------

    def find(self, request: UiaFindRequest) -> UiaFindResult:
        if not request.has_condition:
            raise UnconditionalQueryError("无条件全树查询被拒绝（只做定点查询）")
        started = time.perf_counter()
        elements, unmatched = self._find_bounded(request)
        elapsed = (time.perf_counter() - started) * 1000.0
        self.last_query_ms = elapsed
        return UiaFindResult(
            elements=elements, elapsed_ms=elapsed, unmatched_count=unmatched
        )

    def _find_bounded(self, request: UiaFindRequest) -> tuple[tuple[UiaElement, ...], int]:
        """窗口那条路 →（**没查到时**）**桌面根遍历兜底**。

        ⚠️ 两段**各有自己的超时预算**：桌面根遍历很慢（桌面树巨大），
        不能跟窗口查询抢同一个 5 s（方案 §8.2 第 8 条）。
        """
        elements, unmatched = _with_deadline(lambda: self._find(request), self.timeout_s)
        if elements:
            return elements, unmatched
        try:
            walked, walked_unmatched = _with_deadline(
                lambda: self._find_root_walk(request), self.timeout_s
            )
        except UiaQueryTimeout:
            return elements, unmatched
        return walked, unmatched + walked_unmatched

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
            # **窗口标题**取锚定窗口的（XAML island 那些元素自己没有窗口句柄，
            # 标题只在锚定窗口上 —— 工单 M11 AC 7 的动作日志要它）。
            anchor_title = _window_title(request.anchor.hwnd)
            return self._publish(matched, anchor_title), unmatched
        finally:
            del controller

    def _query(self, controller, root, request: UiaFindRequest) -> tuple[list, int]:
        """定点查询：**一条属性条件**一次查询（名字在 Python 侧过滤）。

        ⚠️ **不做 AND 条件**（名字 + 控件类型合成一个条件）：
        冻结原型的 ``build_condition`` 在两个条件并存时会走 ``CreateAndCondition``，
        **在本机崩**（access violation）。
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

    # -- 桌面根遍历兜底 ---------------------------------------------------

    def _find_root_walk(
        self, request: UiaFindRequest
    ) -> tuple[tuple[UiaElement, ...], int]:
        """**桌面根遍历**兜底：XAML island 的树不在窗口句柄下。

        ⚠️ 请求**仍然必须带属性条件**（C-09 的禁令不变）：这里的全树遍历是
        **有条件请求的兜底**，且**按 PID 过滤**后只把那个进程的控件拿进匹配。
        """
        pids = _window_process_ids(request.anchor.hwnd)
        if not pids:
            return (), 0
        elements = _root_walk_by_pid(pids)
        if not elements:
            return (), 0
        matched, unmatched = self._filter(elements, request)
        # **窗口标题**取锚定窗口的（XAML island 那些元素自己没有窗口句柄）。
        anchor_title = _window_title(request.anchor.hwnd)
        return self._publish(matched, anchor_title), unmatched

    def _filter(self, elements: list, request: UiaFindRequest) -> tuple[list, int]:
        """**复用现有名字过滤**（Python 侧）—— 判据与 :meth:`_query` 同一套。

        桌面上取回来的是**这个进程的全部元素**（单条件遍历的必然结果），
        所以「自动化标识 / 精确名字 / 控件类型」三条退路都在 Python 侧做，
        **名字过滤的口径不变**（:func:`_name_matches`）。
        """
        attempts: list = []
        if request.automation_id:
            attempts.append(
                lambda element: element.automation_id == request.automation_id
            )
        if request.name:
            attempts.append(lambda element: (element.name or "") == request.name)
        type_id = _control_type_id(request.control_type)
        if type_id is not None:
            attempts.append(lambda element: element.control_type == type_id)
        unmatched = 0
        matched: list = []
        for predicate in attempts:
            found = []
            for element in elements:
                try:
                    if predicate(element):
                        found.append(element)
                except Exception:
                    continue
            candidates = [
                element
                for element in found
                if _name_matches(element.name or "", request.name)
            ]
            unmatched += len(found) - len(candidates)
            if candidates:
                matched = candidates
                break
        # **只留下对上的那些**，其余的释放掉（别把整棵桌面树攥在手里）。
        keep = {id(element) for element in matched}
        for element in elements:
            if id(element) not in keep:
                element.release()
        return matched, unmatched

    def _publish(self, elements: list, anchor_title: str = "") -> tuple[UiaElement, ...]:
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
            out.append(self._to_element(element, element_id, anchor_title))
        return tuple(out)

    def _to_element(
        self, element, element_id: str, anchor_title: str = ""
    ) -> UiaElement:

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
        # 读不出 IsEnabled 时按**可用**算（读不到 ≠ 不可用）。
        enabled = _read_is_enabled(element)
        window_handle = int(element.native_window_handle) or None
        return UiaElement(
            name=element.name or "",
            control_type=element.control_type_name,
            class_name=element.class_name or "",
            automation_id=element.automation_id or None,
            rect=rect if rect is not None else (0, 0, 0, 0),
            window_handle=window_handle,
            element_id=element_id,
            is_password=bool(sensitive),
            value="" if sensitive else _read_value(element),
            help_text=_read_help_text(element),
            supports_invoke=_has_invoke(element),
            enabled=True if enabled is None else enabled,
            window_title=self._title(window_handle) or anchor_title,
        )

    def _title(self, hwnd: int | None) -> str:
        """窗口标题（**按根窗口缓存**：一次查询里只问系统一次）。"""
        if not hwnd:
            return ""
        root = int(_user32.GetAncestor(wintypes.HWND(int(hwnd)), _GA_ROOT) or int(hwnd))
        if root not in self._titles:
            self._titles[root] = _window_title(root)
        return self._titles[root]


# ---------------------------------------------------------------------------
# 端口：可用性探测
# ---------------------------------------------------------------------------


def _better_assessment(
    window: TreeAssessment, walked: TreeAssessment
) -> TreeAssessment:
    """两遍读数（窗口那一遍 / 桌面根那一遍）取**更好的那一遍**。

    「更好」= 能力表的判定更高（完全可用 > 部分覆盖）；
    同档时取**可用控件更多**的那一遍（桌面根那一遍看得到更多）。
    """

    def _rank(assessment: TreeAssessment) -> tuple[int, int]:
        usable = 1 if (assessment.usable_controls and not assessment.name_collisions) else 0
        return (usable, assessment.usable_controls)

    return window if _rank(window) >= _rank(walked) else walked


class WindowsCapabilityProbe:
    """可用性探测：**递归子窗口 + 看 UIA ClassName**（不数候选数）。

    ⚠️ 这里**允许**按窗口遍历（无条件的 ``FindAll``）：判定姿势本身就是
    「递归子窗口 + 看 ClassName」，不遍历就看不到类名。
    它与动作路径的禁令不冲突：

    * 动作路径（:class:`~minicode.uia.ports.UiaQueryPort`）**永远**带属性条件；
    * 探测每个「应用 + 版本」只跑一次（静态缓存），且**不参与动作路径**。

    **桌面根兜底**：窗口那一遍没判成「完全可用」时，再走一遍**桌面根遍历**
    （XAML island 的树注册在桌面根上、与窗口同 PID —— Windows 设置就属这类），
    两遍读数取**更好的那一遍**。
    """

    def __init__(self, timeout_s: float = DEFAULT_TIMEOUT_S):
        self.timeout_s = float(timeout_s)

    def assess(self, anchor: WindowAnchor) -> TreeAssessment:
        window = _with_deadline(lambda: self._assess(anchor, ()), self.timeout_s)
        if window.usable_controls and not window.name_collisions:
            # 已经判「完全可用」→ 不必再遍历桌面（省一次昂贵的桌面根遍历）。
            return window
        # **兜底：桌面根遍历**（XAML island 的树注册在桌面根上、与窗口同进程）。
        # ⚠️ 它**自己一次超时预算**（桌面树巨大），不与窗口遍历抢同一个 5 s。
        pids = _window_process_ids(anchor.hwnd)
        if not pids:
            return window
        try:
            extra = _with_deadline(lambda: _root_walk_by_pid(pids), self.timeout_s)
        except UiaQueryTimeout:
            return window
        if not extra:
            return window
        try:
            walked = _with_deadline(
                lambda: self._assess(anchor, tuple(extra)), self.timeout_s
            )
        except UiaQueryTimeout:
            return window
        return _better_assessment(window, walked)

    def _assess(self, anchor: WindowAnchor, extra: tuple = ()) -> TreeAssessment:
        from minicode.exec import uia

        windows = [anchor.hwnd, *_child_windows(anchor.hwnd)]
        class_names: list[str] = []
        interactive: list[str] = []
        names: list[str] = []
        seen: set[tuple[int, str, int, int]] = set()
        usable_controls = 0
        has_automation_id = False
        has_tree = False

        def _consume(element) -> None:
            """一个元素的读数（**去重集合是同一个** → 不重复计数）。"""
            nonlocal usable_controls, has_automation_id
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
                pass
            finally:
                element.release()

        controller = uia.UIA()
        try:
            if extra:
                # **桌面根那一遍**已经是该进程的**全部**元素
                # （桌面根的后代包含窗口子树）→ **不再走窗口那一遍**，
                # 否则同一个元素会被数两遍。
                has_tree = bool(extra)
                for element in extra:
                    _consume(element)
            else:
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
                        _consume(element)
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
#: 窗口标题（工单 M11 AC 7：动作日志要含窗口句柄**与标题**）。
_user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
_user32.GetWindowTextW.restype = ctypes.c_int
#: ``GA_ROOT`` —— 取根窗口（子窗口自己没标题，标题在根窗口上）。
_user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
_user32.GetAncestor.restype = wintypes.HWND
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


#: ``GetAncestor`` 的 ``GA_ROOT``。
_GA_ROOT = 2


def _window_title(hwnd: int | None) -> str:
    """窗口标题（读根窗口的；读不到就是空串）。

    工单 M11 AC 7：动作日志要含「目标窗口句柄**与标题**」。
    """
    if not hwnd:
        return ""
    try:
        root = int(_user32.GetAncestor(wintypes.HWND(int(hwnd)), _GA_ROOT) or hwnd)
        buffer = ctypes.create_unicode_buffer(512)
        length = _user32.GetWindowTextW(wintypes.HWND(root), buffer, 512)
    except Exception:
        return ""
    return buffer.value[:length] if length > 0 else ""


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

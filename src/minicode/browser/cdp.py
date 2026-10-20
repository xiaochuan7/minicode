"""真机适配器：**走 CDP**（Chromium 的调试协议），不碰像素。

* **页面结构快照** = Chromium 的可访问性树（CDP ``Accessibility.getFullAXTree``）：
  每个节点给**角色**与**可见文本** —— 这就是「按可见文本 / 角色找到目标元素」的依据；
* 每个节点的矩形用 ``DOM.resolveNode`` + ``Runtime.callFunctionOn`` 取
  ``getBoundingClientRect()``（**CSS 像素**），再按**量出来的**比例换算到
  **物理表面空间**（:mod:`minicode.browser.geometry`）；
* **动作**走**浏览器自己的输入通道**（CDP ``Input.dispatchMouseEvent`` /
  ``Input.insertText``），不走桌面鼠标键盘注入 —— 这正是浏览器通道的定义。

⚠️ **Playwright 是可选依赖**：核心链路（端口 / 通道 / 能力表 / 假实现）只用标准库，
真机适配器缺 Playwright 时**显式报错**，不静默降级。
"""

from __future__ import annotations

import ctypes
from dataclasses import dataclass

from minicode.action.ports import WindowAnchor

from .capability import PageAssessment
from .elements import BrowserElement
from .geometry import PageViewport, css_rect_to_physical, viewport_from_window
from .ports import PageSnapshot

__all__ = [
    "ChromiumPage",
    "ChromiumCapabilityProbe",
    "find_browser_window",
    "find_browser_window_by_pid",
    "window_rect",
    "PLAYWRIGHT_REQUIRED",
]

#: 浏览器窗口的类名前缀（Chromium 系：Chrome / Edge / 夸克……）。
_WINDOW_CLASS_PREFIX = "Chrome_WidgetWin_"

#: 页面结构快照里**一律丢掉**的角色：
#: * ``InlineTextBox`` —— 具名元素的叶子文本节点（与祖先同名）。
_LEAF_ROLES = frozenset({"inlinetextbox"})

#: **没有语义**的容器角色（``none`` / ``generic`` / ``RootWebArea``）。
#: 它们**有可见文本时保留**（扁平 DOM 里 ``<div>提交订单</div>`` 的可见文本
#: 就挂在这样的节点上），**没有可见文本时丢掉**（纯噪声容器）。
_CONTAINER_ROLES = frozenset({"none", "generic", "rootwebarea"})

#: **可交互**的角色（按角色能操作、但没有可见文本的元素 = 覆盖不全）。
_INTERACTIVE_ROLES = frozenset(
    {
        "button",
        "textbox",
        "searchbox",
        "link",
        "checkbox",
        "radio",
        "combobox",
        "listbox",
        "menuitem",
        "menuitemcheckbox",
        "menuitemradio",
        "option",
        "slider",
        "spinbutton",
        "switch",
        "tab",
        "treeitem",
    }
)

PLAYWRIGHT_REQUIRED = (
    "真机浏览器通道需要 Playwright（可选依赖）："
    "pip install playwright && python -m playwright install chromium"
)


# ---------------------------------------------------------------------------
# Win32：找窗口、读它的**物理**矩形
# ---------------------------------------------------------------------------


class _RECT(ctypes.Structure):
    _fields_ = [
        ("left", ctypes.c_long),
        ("top", ctypes.c_long),
        ("right", ctypes.c_long),
        ("bottom", ctypes.c_long),
    ]


def _user32():
    return ctypes.windll.user32


def find_browser_window(title_contains: str) -> int:
    """按标题找**浏览器窗口**的句柄（找不到返回 0）。

    枚举顶层窗口，按「类名是 Chromium 系 + 标题里有给定的片段」挑一个。
    """
    user32 = _user32()
    found: list[int] = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def _callback(hwnd, _lparam):
        cls = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, cls, 256)
        if cls.value.startswith(_WINDOW_CLASS_PREFIX):
            title = ctypes.create_unicode_buffer(512)
            user32.GetWindowTextW(hwnd, title, 512)
            if title_contains and title_contains in title.value:
                found.append(int(hwnd))
        return True

    user32.EnumWindows(_callback, 0)
    return found[0] if found else 0


def find_browser_window_by_pid(pids: set[int]) -> int:
    """按**进程**找浏览器窗口（找不到返回 0）。

    ⚠️ **为什么不能只按标题找**（本机实测，2026-10-21）：
    ``find_browser_window`` 认的是「类名是 Chromium 系 + 标题里有片段」——
    本机同时跑着 DeepSeek Harness（Electron，类名同样是 ``Chrome_WidgetWin_1``）
    与用户自己的浏览器实例，按标题挑会**挑到别人的窗口**，
    拿回来的矩形还是错的（比例算出来 0.04 / 2.017 这种离谱值）。

    按 **PID** 认就只有一个解：刚由评测台起的那几个进程。
    """
    user32 = _user32()
    found: list[int] = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def _callback(hwnd, _lparam):
        cls = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, cls, 256)
        if not cls.value.startswith(_WINDOW_CLASS_PREFIX):
            return True
        pid = ctypes.c_ulong()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if int(pid.value) in pids:
            found.append(int(hwnd))
        return True

    user32.EnumWindows(_callback, 0)
    return found[0] if found else 0


def window_rect(hwnd: int) -> tuple[int, int, int, int]:
    """窗口的**物理**矩形 ``(left, top, right, bottom)``。

    ⚠️ 调用方**必须先**让进程 PMv2 感知（:func:`minicode.exec.dpi.enable_dpi_awareness`）——
    否则拿回来的是逻辑坐标（本机会差 1.5 倍）。
    """
    rect = _RECT()
    _user32().GetWindowRect(ctypes.c_void_p(hwnd), ctypes.byref(rect))
    return (rect.left, rect.top, rect.right, rect.bottom)


# ---------------------------------------------------------------------------
# 页面结构快照
# ---------------------------------------------------------------------------


@dataclass
class _AxNode:
    role: str
    name: str
    backend_id: int
    #: 祖先的可见文本（用来判断「这个叶子文本是不是某个祖先的重复」）。
    ancestor_names: tuple[str, ...] = ()


class ChromiumPage:
    """真机页面（走 CDP）：结构快照 + 浏览器自己的输入通道 + 元素调用。"""

    def __init__(self, page, cdp, hwnd: int):
        self.page = page
        self.cdp = cdp
        self.hwnd = int(hwnd)
        self._generation = 0
        #: 元素 id → 该元素的 **CSS 矩形**（浏览器自己的输入通道要用）。
        self._css_rects: dict[str, tuple[float, float, float, float]] = {}
        #: DOM 代理是否就位（``DOM.resolveNode`` 的前提）。
        self._dom_ready = False
        #: 最近一次读矩形失败的错误（如实记下来，便于排查）。
        self.last_error = ""

    # -- 页面结构端口 -----------------------------------------------------

    def snapshot(self) -> PageSnapshot:
        """取一次页面结构快照（可访问性树）。"""
        nodes = self._parse_tree()
        viewport = self.viewport()
        elements: list[BrowserElement] = []
        self._css_rects = {}
        for node in nodes:
            if not self._kept(node):
                continue
            css_rect = self._node_rect(node.backend_id)
            if css_rect is None:
                continue
            rect = css_rect_to_physical(css_rect, viewport)
            element_id = str(node.backend_id)
            self._css_rects[element_id] = css_rect
            elements.append(
                BrowserElement(
                    name=node.name,
                    role=node.role.lower(),
                    rect=rect,
                    element_id=element_id,
                    window_handle=self.hwnd,
                )
            )
        self._generation += 1
        focused_value, focused_id = self._focused()
        return PageSnapshot(
            elements=tuple(elements),
            viewport=viewport,
            generation=self._generation,
            focused_value=focused_value,
            focused_element_id=focused_id,
        )

    def viewport(self) -> PageViewport:
        """视口在物理表面空间的位置与比例（比例是**量出来的**）。"""
        metrics = self.page.evaluate(
            """() => ({
                outerWidth: window.outerWidth,
                outerHeight: window.outerHeight,
                innerWidth: window.innerWidth,
                innerHeight: window.innerHeight,
            })"""
        )
        rect = window_rect(self.hwnd) if self.hwnd else (0, 0, 0, 0)
        return viewport_from_window(
            rect,
            (int(metrics["outerWidth"]), int(metrics["outerHeight"])),
            (int(metrics["innerWidth"]), int(metrics["innerHeight"])),
        )

    # -- 浏览器自己的输入通道 ---------------------------------------------

    def insert_text(self, text: str) -> bool:
        """把文字送进页面（CDP 的 ``Input.insertText``）。"""
        try:
            self.cdp.send("Input.insertText", {"text": text})
        except Exception:  # noqa: BLE001 —— 端口实现失败如实回报，不假装成功
            return False
        return True

    def invoke(self, element_id: str) -> bool:
        """用**浏览器自己的输入通道**点这个元素（CDP 的鼠标事件）。"""
        css_rect = self._css_rects.get(element_id)
        if css_rect is None:
            return False
        x = css_rect[0] + css_rect[2] / 2.0
        y = css_rect[1] + css_rect[3] / 2.0
        for event_type in ("mousePressed", "mouseReleased"):
            self.cdp.send(
                "Input.dispatchMouseEvent",
                {
                    "type": event_type,
                    "x": x,
                    "y": y,
                    "button": "left",
                    "clickCount": 1,
                },
            )
        return True

    # -- 能力探测 ---------------------------------------------------------

    def assess(self) -> PageAssessment:
        """按**实测**读数评估这个页面的结构覆盖（能力表用）。"""
        nodes = self._parse_tree()
        kept_keys = {
            (node.role.lower(), node.name) for node in nodes if self._kept(node)
        }
        named = 0
        unnamed_interactive = 0
        unnamed = 0
        seen: dict[str, int] = {}
        for node in nodes:
            role = node.role.lower()
            if node.name and (role, node.name) in kept_keys:
                named += 1
                seen[node.name] = seen.get(node.name, 0) + 1
            elif role in _INTERACTIVE_ROLES:
                unnamed_interactive += 1
            elif not node.name and role not in _LEAF_ROLES:
                unnamed += 1
        duplicates = sum(count - 1 for count in seen.values() if count > 1)
        return PageAssessment(
            has_snapshot=True,
            named_elements=named,
            unnamed_interactive=unnamed_interactive,
            unnamed_elements=unnamed,
            duplicate_names=duplicates,
        )

    # -- 内部 -------------------------------------------------------------

    def _parse_tree(self) -> list[_AxNode]:
        """把 AX 树解析成节点列表，并记下每个节点的**祖先可见文本**。

        Chromium 的可访问性树里，``<button>提交订单</button>`` 会同时给出一个
        名为「提交订单」的 ``button`` 节点和一个同名的 ``StaticText`` 子节点。
        靠祖先的可见文本就能判断「这个叶子文本是不是某个祖先的重复」。
        """
        tree = self.cdp.send("Accessibility.getFullAXTree")
        raw = {int(node["nodeId"]): node for node in tree.get("nodes", [])}
        ancestors: dict[int, set[int]] = {}

        def _collect(node_id: int, chain: set[int]) -> None:
            ancestors[node_id] = chain
            for child in raw.get(node_id, {}).get("childIds", []) or []:
                child_id = int(child)
                if child_id in raw:
                    _collect(child_id, chain | {node_id})

        for node_id in raw:
            if node_id not in ancestors:
                _collect(node_id, set())

        def _name_of(node_id: int) -> str:
            node = raw.get(node_id, {})
            return ((node.get("name") or {}).get("value") or "").strip()

        nodes: list[_AxNode] = []
        for node_id, node in raw.items():
            role = ((node.get("role") or {}).get("value") or "").strip()
            name = ((node.get("name") or {}).get("value") or "").strip()
            backend_id = node.get("backendDOMNodeId")
            if not role or not backend_id:
                continue
            nodes.append(
                _AxNode(
                    role=role,
                    name=name,
                    backend_id=int(backend_id),
                    ancestor_names=tuple(
                        _name_of(a) for a in ancestors.get(node_id, set()) if _name_of(a)
                    ),
                )
            )
        return nodes

    @staticmethod
    def _kept(node: _AxNode) -> bool:
        """这个节点要不要进页面结构快照。

        * 叶子文本节点（``InlineTextBox``）—— 丢掉；
        * 与**具名祖先**同名的 ``StaticText`` —— 丢掉（那是祖先的重复）；
        * 没有可见文本的容器（``none`` / ``generic`` / ``RootWebArea``）—— 丢掉；
        * 其余（有可见文本的语义节点与容器）—— 保留。
        """
        role = node.role.lower()
        if role in _LEAF_ROLES:
            return False
        if role == "statictext" and node.name and node.name in node.ancestor_names:
            return False
        if role in _CONTAINER_ROLES and not node.name:
            return False
        return True

    def _node_rect(self, backend_id: int) -> tuple[float, float, float, float] | None:
        """元素的 CSS 矩形 ``(x, y, width, height)``（相对视口左上角）。"""
        self._ensure_dom()
        try:
            resolved = self.cdp.send("DOM.resolveNode", {"backendNodeId": backend_id})
            object_id = resolved["object"]["objectId"]
            result = self.cdp.send(
                "Runtime.callFunctionOn",
                {
                    "objectId": object_id,
                    "functionDeclaration": (
                        "function(){const el=(this.nodeType===3)?this.parentElement:this;"
                        "if(!el||!el.getBoundingClientRect){return null;}"
                        "const r=el.getBoundingClientRect();"
                        "return {x:r.x,y:r.y,w:r.width,h:r.height};}"
                    ),
                    "returnByValue": True,
                },
            )
            value = result["result"]["value"]
        except Exception as exc:  # noqa: BLE001 —— 读不到就当没有坐标（不猜）
            self.last_error = f"{type(exc).__name__}: {exc}"
            return None
        if not value:
            return None
        return (float(value["x"]), float(value["y"]), float(value["w"]), float(value["h"]))

    def _ensure_dom(self) -> None:
        """让 CDP 的 DOM 代理就位（``DOM.resolveNode`` 需要它）。

        没就位时 ``DOM.resolveNode`` 会报「找不到这个节点」——
        那样**每个元素的矩形都读不出来**，页面结构就成了一张没有位置的清单。
        """
        if self._dom_ready:
            return
        self.cdp.send("DOM.getDocument", {"depth": 0})
        self._dom_ready = True

    def _focused(self) -> tuple[str, str]:
        """当前聚焦元素的取值与引用（没有聚焦元素 → ``("", "")``）。"""
        self._ensure_dom()
        try:
            evaluated = self.cdp.send(
                "Runtime.evaluate",
                {
                    "expression": (
                        "document.activeElement === document.body "
                        "? null : document.activeElement"
                    )
                },
            )
            object_id = (evaluated.get("result") or {}).get("objectId")
            if not object_id:
                return ("", "")
            value = self.cdp.send(
                "Runtime.callFunctionOn",
                {
                    "objectId": object_id,
                    "functionDeclaration": (
                        "function(){return (this.value !== undefined && this.value !== null"
                        " ? this.value : (this.textContent || ''));}"
                    ),
                    "returnByValue": True,
                },
            )
            node = self.cdp.send("DOM.describeNode", {"objectId": object_id})
            backend_id = node.get("node", {}).get("backendNodeId")
            return (str(value["result"].get("value") or ""), str(backend_id or ""))
        except Exception as exc:  # noqa: BLE001 —— 读不到聚焦元素 = 判不了
            self.last_error = f"{type(exc).__name__}: {exc}"
            return ("", "")


class ChromiumCapabilityProbe:
    """浏览器通道的能力探测（按实测读数）。"""

    def __init__(self, page: ChromiumPage):
        self.page = page

    def assess(self, anchor: WindowAnchor) -> PageAssessment:
        return self.page.assess()

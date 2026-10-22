"""真机适配器：**走 CDP**（Chromium 的调试协议），不碰像素。

* **页面结构快照** = Chromium 的可访问性树（CDP ``Accessibility.getFullAXTree``）：
  每个节点给**角色**与**可见文本** —— 这就是「按可见文本 / 角色找到目标元素」的依据；
* 每个节点的矩形用 ``DOM.resolveNode`` + ``Runtime.callFunctionOn`` 取
  ``getBoundingClientRect()``（**CSS 像素**），再按**量出来的**比例换算到
  **物理表面空间**（:mod:`minicode.browser.geometry`）；
* **同一次往返**里还量出**可见文本的实际区域**（工单 M28）——
  落点落在文字上，不是落在元素框的中心（:mod:`minicode.browser.textmetrics`）；
* **动作**走**浏览器自己的输入通道**（CDP ``Input.dispatchMouseEvent`` /
  ``Input.insertText``），不走桌面鼠标键盘注入 —— 这正是浏览器通道的定义。

⚠️ **Playwright 是可选依赖**：核心链路（端口 / 通道 / 能力表 / 假实现）只用标准库，
真机适配器缺 Playwright 时**显式报错**，不静默降级。
"""

from __future__ import annotations

import ctypes
import time
from dataclasses import dataclass

from minicode.action.ports import WindowAnchor

from .capability import PageAssessment
from .elements import BrowserElement
from .geometry import PageViewport, css_rect_to_physical, viewport_from_window
from .ports import PageSnapshot
from .textmetrics import TEXT_MEASURE_JS, css_text_rect, measure_visible_text

__all__ = [
    "ChromiumPage",
    "ChromiumCapabilityProbe",
    "find_browser_window",
    "find_browser_window_by_pid",
    "window_rect",
    "PLAYWRIGHT_REQUIRED",
    "SCROLL_UNIT",
]

#: **浏览器通道的滚动单位**：**CSS 像素**。
#:
#: ⚠️ **三条通道的单位各自声明，不许混**（工单 M13 / 动作协议 v1）：
#:
#: * **桌面通道**：``action/executor.py::SCROLL_DELTA_PER_LINE = 50`` —— **像素语义**
#:   （Win32 的滚轮一格是 ``WHEEL_DELTA = 120``，见
#:   https://learn.microsoft.com/en-us/windows/win32/api/winuser/ns-winuser-mouseinput）；
#: * **浏览器通道**：**CSS 像素** —— CDP ``Input.dispatchMouseEvent`` 的
#:   ``mouseWheel`` 的 delta 就是 CSS 像素，**相对主框架的视口**
#:   （https://chromedevtools.github.io/devtools-protocol/tot/Input/#method-dispatchMouseEvent）；
#: * 「**1 行 = 多少 CSS px**」**运行时量**（见 :data:`_LINE_METRIC_JS`）—— **不写死**。
SCROLL_UNIT = "css-px"

#: 量「**1 行 = 多少 CSS 像素**」的脚本（**运行时量，不写死**）。
#:
#: 口径（按优先级取第一个非零的）：
#:
#: 1. **相邻子项的 y 差**（列表项之间的距离 = 这个列表的「一行」）；
#: 2. ``getComputedStyle(el).lineHeight``（元素自己的行高）；
#: 3. ``fontSize``（最后兜底：行高按字号算）。
#:
#: ⚠️ 这三个都是**当场量的**——页面换字号 / 换行距时读数跟着变。
_LINE_METRIC_JS = """
function(){
  var cs = getComputedStyle(this);
  var raw = cs.lineHeight;
  var lineHeight = (raw === 'normal' || !raw) ? 0 : (parseFloat(raw) || 0);
  var gap = 0;
  var kids = this.children;
  for (var i = 0; i + 1 < kids.length; i++) {
    var a = kids[i].getBoundingClientRect();
    var b = kids[i + 1].getBoundingClientRect();
    if (a.height > 0 && b.height > 0 && b.top > a.top) { gap = b.top - a.top; break; }
  }
  return {
    childGap: gap,
    lineHeight: lineHeight,
    fontSize: parseFloat(cs.fontSize) || 0
  };
}
"""

#: 读**当前滚动位置**（窗口的 + 目标元素自己的）。
#:
#: ⚠️ 两个都读：有的页面滚**窗口**，有的页面滚**内层容器**——
#: 哪个动了就算滚到了（读数如实记两个）。
_SCROLL_STATE_JS = """
function(){
  return { windowY: window.scrollY || 0, elementTop: this.scrollTop || 0 };
}
"""


def _ax_name(node: dict) -> str:
    """**AX 树节点的可见文本**（读不出来就是空串）。"""
    return ((node.get("name") or {}).get("value") or "").strip()


def _first_common(left: list[int], right: list[int]) -> int | None:
    """两条「自己 → 根」的祖先链上的**最近公共祖先**（没有 → ``None``）。"""
    seen = set(right)
    for node_id in left:
        if node_id in seen:
            return node_id
    return None

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

def _first_positive(*values: object) -> float:
    """取第一个**大于 0** 的数（都没有 → 0.0）。"""
    for value in values:
        try:
            number = float(value)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            continue
        if number > 0.0:
            return number
    return 0.0


def _visible_point(
    css_rect: tuple[float, float, float, float], view_w: float, view_h: float
) -> tuple[float, float]:
    """目标元素**在视口里可见的那一块**的中点（CSS 像素）。

    ⚠️ 可滚列表常常**比视口还高** → 直接取元素中心会落在视口外，
    wheel 事件就没人接 → 取「元素 ∩ 视口」的交集中心。
    """
    left, top, width, height = css_rect
    right = left + width
    bottom = top + height
    visible_left = max(left, 0.0)
    visible_top = max(top, 0.0)
    visible_right = min(right, view_w) if view_w > 0 else right
    visible_bottom = min(bottom, view_h) if view_h > 0 else bottom
    if visible_right <= visible_left or visible_bottom <= visible_top:
        # 整个元素都在视口外 → 夹到视口里（光标总得有个地方停）。
        return (
            min(max(left + width / 2.0, 0.0), max(view_w - 1.0, 0.0)),
            min(max(top + height / 2.0, 0.0), max(view_h - 1.0, 0.0)),
        )
    return (
        (visible_left + visible_right) / 2.0,
        (visible_top + visible_bottom) / 2.0,
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


def _ax_node(raw: dict) -> _AxNode:
    """由**原始 AX 节点**造出 :class:`_AxNode`（没有 DOM 引用 → backend_id = 0）。"""
    role = ((raw.get("role") or {}).get("value") or "").strip()
    name = _ax_name(raw)
    backend_id = raw.get("backendDOMNodeId")
    return _AxNode(
        role=role,
        name=name,
        backend_id=int(backend_id) if backend_id else 0,
    )


class ChromiumPage:
    """真机页面（走 CDP）：结构快照 + 浏览器自己的输入通道 + 元素调用。"""

    def __init__(self, page, cdp, hwnd: int):
        self.page = page
        self.cdp = cdp
        self.hwnd = int(hwnd)
        self._generation = 0
        #: 元素 id → 该元素的 **CSS 矩形**（浏览器自己的输入通道要用）。
        self._css_rects: dict[str, tuple[float, float, float, float]] = {}
        #: 元素 id → 该元素**可见文本的区域**（CSS 矩形；落点落在它上面）。
        self._css_text_rects: dict[str, tuple[float, float, float, float]] = {}
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
        self._css_text_rects = {}
        for node in nodes:
            if not self._kept(node):
                continue
            element = self._element_of(node, viewport)
            if element is None:
                continue
            elements.append(element)
        self._generation += 1
        focused_value, focused_id = self._focused()
        return PageSnapshot(
            elements=tuple(elements),
            viewport=viewport,
            generation=self._generation,
            focused_value=focused_value,
            focused_element_id=focused_id,
        )

    # -- 容器内的定位 -----------------------------------------------------

    def find_in_container(
        self, container_name: str, child_name: str
    ) -> tuple[BrowserElement, BrowserElement] | None:
        """在「可见文本含 ``container_name`` 的容器」里找「可见文本含 ``child_name`` 的后代」。

        **为什么需要它**：扁平的页面结构快照**分不出父子关系** ——
        cc-switch 的每张供应商卡片里都有一个「启用」按钮，
        按名字找会命中一堆（→ 候选超上限 → 通道直接拒绝）。
        而它的动作正是「点某个条目**里面**的按钮」。

        容器 = 那个**同时**含起点与目标后代的**最近公共祖先**
        （再往上的祖先会把别的卡片也含进来 → 不算数）。

        返回 ``(容器元素, 后代元素)``；找不到 → ``None``。
        """
        raw, parents = self._ax_nodes()
        names = {node_id: _ax_name(node) for node_id, node in raw.items()}
        chains = {node_id: self._ancestor_chain(parents, node_id) for node_id in raw}
        #: 起点：名字里含 ``container_name`` 的节点（**精确匹配优先**）。
        starts = [
            node_id for node_id, name in names.items() if name and container_name in name
        ]
        starts.sort(
            key=lambda node_id: (names[node_id] != container_name, len(names[node_id]))
        )
        #: 候选后代：名字里含 ``child_name`` 的节点。
        candidates = [
            node_id for node_id, name in names.items() if name and child_name in name
        ]
        viewport = self.viewport()
        for start in starts:
            for ancestor in chains[start][1:]:
                for child_id in candidates:
                    child_chain = chains[child_id]
                    if ancestor not in child_chain:
                        continue
                    if _first_common(chains[start], child_chain) != ancestor:
                        continue
                    container = self._element_of(_ax_node(raw[ancestor]), viewport)
                    child = self._element_of(_ax_node(raw[child_id]), viewport)
                    if container is not None and child is not None:
                        return container, child
        return None

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
        """用**浏览器自己的输入通道**点这个元素（CDP 的鼠标事件）。

        点的是**可见文本的实际区域**的中心（工单 M28）—— 与落点同一处，
        免得「记录的落点」与「真的点下去的地方」不是一回事。
        文本区域读不到时退回元素盒子（这时定位本来就判不了，不会走到这里）。
        """
        css_rect = self._css_text_rects.get(element_id) or self._css_rects.get(element_id)
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

    def invoke_box(self, element_id: str) -> bool:
        """按元素**自己的盒子**点它（CDP 的鼠标事件；CSS 像素）。

        ⚠️ **什么时候用它**：元素**没有可见文本**（图标按钮、标签被裁掉的
        折叠按钮）。那种情况下「可见文本的实际区域」要么读不出来，
        要么量出来的是**裁掉之前**的宽度 —— 那个中心点会**落到旁边的元素上**
        （实测：cc-switch 顶栏收起来的应用类型按钮，点「Claude」会点到「Codex」）。
        """
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

    def scroll_lines(self, element_id: str, lines: int) -> dict:
        """滚 ``lines`` 行（**浏览器自己的输入通道**：CDP ``mouseWheel``）。

        ⚠️ **单位是 CSS 像素**（CDP 的 ``mouseWheel`` delta 就是 CSS 像素，
        相对主框架视口），**不是**桌面通道那个像素语义的 ``SCROLL_DELTA_PER_LINE``
        —— 两条通道的单位**各自声明**（见 :data:`SCROLL_UNIT`）。
        「**1 行 = 多少 CSS px**」**运行时量**（:data:`_LINE_METRIC_JS`），不写死。

        流程（与点元素同构：都是**浏览器自己的输入通道**）：

        1. ``mouseMoved`` 到目标（**wheel 事件落在光标处**）；
        2. 量「1 行 = 多少 CSS px」→ ``delta = lines × 每行像素``；
        3. 发 ``mouseWheel``；
        4. 轮询到**滚动停住**（惯性结束）→ 读 ``window.scrollY``；
        5. 校验 ``0 < ΔscrollY < 视口高``；**Δ = 0** → 退
           ``el.scrollBy(0, dy)``（wheel 事件可能被页面的 handler 吃掉）。

        返回读数（进端到端的上下文）；**读不出来就如实记，不假装成功**。
        """
        result: dict = {
            "ok": False,
            "unit": SCROLL_UNIT,
            "lines": int(lines),
            "css_px_per_line": 0.0,
            "delta_css_px": 0.0,
            "scroll_y_before": 0.0,
            "scroll_y_after": 0.0,
            "element_scroll_before": 0.0,
            "element_scroll_after": 0.0,
            "scrolled_px": 0.0,
            "viewport_height_css_px": 0.0,
            "within_viewport": False,
            "fallback_used": False,
            "stable": False,
            "error": "",
        }
        css_rect = self._css_rects.get(element_id)
        if css_rect is None:
            result["error"] = "页面结构快照里没有这个元素的盒子（量不出滚动目标）"
            return result
        try:
            backend_id = int(element_id)
        except ValueError:
            result["error"] = f"元素引用不是数字：{element_id!r}"
            return result

        viewport = self.page.evaluate(
            "() => ({ width: window.innerWidth, height: window.innerHeight })"
        )
        view_w = float(viewport.get("width") or 0.0)
        view_h = float(viewport.get("height") or 0.0)
        result["viewport_height_css_px"] = view_h

        # -- 1. 光标移到目标（wheel 事件落在光标处）------------------------
        # ⚠️ 目标可能**比视口还高**（可滚列表）→ 落点取「元素 ∩ 视口」的交集，
        # 否则光标会落在视口外 → wheel 事件没人接。
        x, y = _visible_point(css_rect, view_w, view_h)
        self.cdp.send("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": x, "y": y})

        # -- 2. 量「1 行 = 多少 CSS px」（运行时量）--------------------------
        metric = self._call_on_element(backend_id, _LINE_METRIC_JS)
        if not metric:
            result["error"] = f"量不出「1 行 = 多少 CSS px」：{self.last_error or '读不到元素'}"
            return result
        per_line = _first_positive(
            metric.get("childGap"), metric.get("lineHeight"), metric.get("fontSize")
        )
        if per_line <= 0.0:
            result["error"] = "量不出「1 行 = 多少 CSS px」（行高 / 相邻项间距都是 0）"
            return result
        result["css_px_per_line"] = per_line
        delta = float(lines) * per_line
        result["delta_css_px"] = delta

        # -- 3. 发 wheel ----------------------------------------------------
        self.cdp.send(
            "Input.dispatchMouseEvent",
            {
                "type": "mouseWheel",
                "x": x,
                "y": y,
                "deltaX": 0.0,
                "deltaY": delta,
                "pointerType": "mouse",
            },
        )

        # -- 4. 等滚动停住 + 读位置 ------------------------------------------
        before = self._scroll_state(backend_id)
        result["scroll_y_before"] = before[0]
        result["element_scroll_before"] = before[1]
        after, stable = self._wait_scroll_stable(backend_id)
        result["scroll_y_after"] = after[0]
        result["element_scroll_after"] = after[1]
        result["stable"] = stable

        # -- 5. 校验；Δ = 0 → 退 scrollBy -----------------------------------
        scrolled = max(after[0] - before[0], after[1] - before[1], 0.0)
        if scrolled <= 0.0:
            result["fallback_used"] = True
            self._scroll_by(backend_id, delta)
            after, stable = self._wait_scroll_stable(backend_id)
            result["scroll_y_after"] = after[0]
            result["element_scroll_after"] = after[1]
            result["stable"] = stable
            scrolled = max(after[0] - before[0], after[1] - before[1], 0.0)
        result["scrolled_px"] = scrolled
        result["within_viewport"] = 0.0 < scrolled < view_h
        result["ok"] = scrolled > 0.0
        if not result["ok"]:
            result["error"] = (
                "滚了之后位置没变（wheel 事件可能被页面 handler 吃掉了，"
                "scrollBy 兜底也没动）"
            )
        return result

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

    def _element_of(self, node: _AxNode, viewport: PageViewport) -> BrowserElement | None:
        """由一个 AX 节点造出页面结构元素（矩形换算到**物理表面空间**）。

        读不出矩形 → ``None``（**不猜一个位置**）。
        """
        geometry = self._node_geometry(node.backend_id)
        if geometry is None:
            return None
        box = geometry.get("box")
        if not box:
            return None
        css_rect = (
            float(box["x"]),
            float(box["y"]),
            float(box["w"]),
            float(box["h"]),
        )
        element_id = str(node.backend_id)
        self._css_rects[element_id] = css_rect
        css_text = css_text_rect(geometry)
        if css_text is not None:
            self._css_text_rects[element_id] = css_text
        return BrowserElement(
            name=node.name,
            role=node.role.lower(),
            rect=css_rect_to_physical(css_rect, viewport),
            element_id=element_id,
            window_handle=self.hwnd,
            text=measure_visible_text(geometry, viewport),
        )

    def _ax_nodes(self) -> tuple[dict[int, dict], dict[int, int]]:
        """AX 树的原始节点 + **父节点表**（父子关系只有这里有）。"""
        tree = self.cdp.send("Accessibility.getFullAXTree")
        nodes = {int(node["nodeId"]): node for node in tree.get("nodes", [])}
        parents: dict[int, int] = {}
        for node_id, node in nodes.items():
            for child in node.get("childIds", []) or []:
                parents[int(child)] = node_id
        return nodes, parents

    @staticmethod
    def _ancestor_chain(parents: dict[int, int], node_id: int) -> list[int]:
        """「自己 → 根」的祖先链。"""
        chain = [node_id]
        current = node_id
        while current in parents:
            current = parents[current]
            chain.append(current)
        return chain

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

    def _node_geometry(self, backend_id: int) -> dict | None:
        """元素的**几何读数**：盒子 + **可见文本的实际区域**（一次 CDP 往返）。

        盒子是元素自己的 ``getBoundingClientRect()``；文本区域是**渲染出来的
        文本节点**的行盒并集（见 :data:`minicode.browser.textmetrics.TEXT_MEASURE_JS`）。
        """
        self._ensure_dom()
        try:
            resolved = self.cdp.send("DOM.resolveNode", {"backendNodeId": backend_id})
            object_id = resolved["object"]["objectId"]
            result = self.cdp.send(
                "Runtime.callFunctionOn",
                {
                    "objectId": object_id,
                    "functionDeclaration": TEXT_MEASURE_JS,
                    "returnByValue": True,
                },
            )
            value = result["result"]["value"]
        except Exception as exc:  # noqa: BLE001 —— 读不到就当没有坐标（不猜）
            self.last_error = f"{type(exc).__name__}: {exc}"
            return None
        if not isinstance(value, dict):
            return None
        return value

    def _ensure_dom(self) -> None:
        """让 CDP 的 DOM 代理就位（``DOM.resolveNode`` 需要它）。

        没就位时 ``DOM.resolveNode`` 会报「找不到这个节点」——
        那样**每个元素的矩形都读不出来**，页面结构就成了一张没有位置的清单。
        """
        if self._dom_ready:
            return
        self.cdp.send("DOM.getDocument", {"depth": 0})
        self._dom_ready = True

    def _call_on_element(self, backend_id: int, function_declaration: str) -> dict | None:
        """在这个元素上跑一段 JS，返回它的返回值（读不到 → ``None``）。"""
        self._ensure_dom()
        try:
            resolved = self.cdp.send("DOM.resolveNode", {"backendNodeId": backend_id})
            object_id = resolved["object"]["objectId"]
            result = self.cdp.send(
                "Runtime.callFunctionOn",
                {
                    "objectId": object_id,
                    "functionDeclaration": function_declaration,
                    "returnByValue": True,
                },
            )
            value = result["result"]["value"]
        except Exception as exc:  # noqa: BLE001 —— 读不到就当没有（不猜）
            self.last_error = f"{type(exc).__name__}: {exc}"
            return None
        return value if isinstance(value, dict) else None

    def _scroll_state(self, backend_id: int) -> tuple[float, float]:
        """当前滚动位置 ``(window.scrollY, 元素自己的 scrollTop)``（CSS 像素）。"""
        state = self._call_on_element(backend_id, _SCROLL_STATE_JS) or {}
        return (
            float(state.get("windowY") or 0.0),
            float(state.get("elementTop") or 0.0),
        )

    def _wait_scroll_stable(
        self, backend_id: int, *, attempts: int = 12, interval_s: float = 0.12
    ) -> tuple[tuple[float, float], bool]:
        """轮询到**滚动停住**为止（惯性结束才继续）。

        ⚠️ Playwright 的 ``mouse.wheel`` 原文「does not wait for the scrolling
        to finish」→ **停没停住要自己判**（连续两次读数相同 = 停住了）。

        返回 ``((windowY, elementTop), 停住了吗)``。
        """
        last: tuple[float, float] | None = None
        for attempt in range(attempts):
            current = self._scroll_state(backend_id)
            if current == last:
                return current, True
            last = current
            if attempt + 1 < attempts:
                time.sleep(interval_s)
        return (last or self._scroll_state(backend_id)), False

    def _scroll_by(self, backend_id: int, delta: float) -> None:
        """兜底：直接让元素（不行就让窗口）滚 ``delta`` 个 CSS 像素。"""
        self._call_on_element(
            backend_id,
            f"""
function(){{
  var before = this.scrollTop || 0;
  this.scrollBy(0, {float(delta)!r});
  if ((this.scrollTop || 0) === before) {{ window.scrollBy(0, {float(delta)!r}); }}
  return {{ windowY: window.scrollY || 0, elementTop: this.scrollTop || 0 }};
}}
""",
        )

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

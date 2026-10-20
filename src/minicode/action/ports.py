"""动作执行缝的端口与数据结构。

**端口形状**（ADR-0007）：输入是「目标描述 + 一帧截图 + 可选的控件树快照」，
输出是「动作 + 最终落点 + 意图」。缝以下的外部依赖全部由这里的端口接出去，
单测用假实现替换它们，不打真桌面、不需要凭据、不产生费用。

端口清单（本单）：截图源、控件树、输入注入、窗口控制。
假模型响应由模型客户端提供（``minicode.model.fakes.FakeModelClient``，M1 交付），不在本单。

> 词汇：本模块的「端口」= 工单口径的**假实现注入点**（「缝的假实现注入点齐备」）。
> ``CONTEXT.md`` 尚未收录该词条——补词条属于领域文档的活儿，留给 domain-modeling。
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from .errors import ErrorCode

if TYPE_CHECKING:  # pragma: no cover - 只为类型标注（运行期不导入，避免两个业务包互相咬住）
    from minicode.redact.redactor import ModelTreeView

__all__ = [
    "CapturedFrame",
    "ControlCandidate",
    "ControlTreeSnapshot",
    "InjectionBlocked",
    "CaptureSource",
    "RegionCaptureSource",
    "ScreenshotPort",
    "ControlTree",
    "InputInjector",
    "WindowController",
    "WindowAnchor",
    "LocateRequest",
    "LocateResult",
    "ControlLocator",
    "ElementInvoker",
]


class InjectionBlocked(Exception):
    """注入被拦下。

    典型成因是目标窗口的完整性级别更高（UIPI 拦截），此时 ``SendInput``
    返回 0 而**不**指示原因。**失败判定只依据返回值**。
    """


@dataclass(frozen=True)
class CapturedFrame:
    """一帧截图（物理像素）。

    ``payload`` 是原始像素字节。本项目**只落它的摘要，不落原图**。
    """

    width: int
    height: int
    left: int = 0
    top: int = 0
    payload: bytes = b""
    backend: str = "fake"
    source: str = "virtual-desktop"

    @property
    def is_empty(self) -> bool:
        """是不是 0×0 画面（间接显示驱动虚拟显示器会让抓屏拿到这种画面）。"""
        return self.width <= 0 or self.height <= 0

    def digest(self) -> str:
        """截图哈希（SHA-256 十六进制）。同帧两次相同，不同帧不同。"""
        hasher = hashlib.sha256()
        hasher.update(f"{self.width}x{self.height}@{self.left},{self.top}|".encode())
        hasher.update(self.payload)
        return hasher.hexdigest()


@dataclass(frozen=True)
class ControlCandidate:
    """控件候选（控件树里与目标描述匹配上的元素）。

    ``rect`` 是**物理表面空间**的包围矩形 ``(left, top, right, bottom)``。

    值类字段（``value`` / ``help_text`` / ``is_password`` / ``extra``）是**敏感面**：
    它们只在「要外发的表示」里出现，且必须先过脱敏（:mod:`minicode.redact`）。
    **定位用的一直是原值** —— 脱敏只改要外发的副本，不改这里。
    """

    name: str
    control_type: str = ""
    rect: tuple[int, int, int, int] = (0, 0, 0, 0)
    window_handle: int | None = None
    automation_id: str | None = None
    #: 元素 id —— **每次观测重新生成**（工单 M3）。
    #: 它只在**本次观测**里有效：拿上一次观测的 id 来重定位必然失败。
    element_id: str = ""
    #: 控件的值（编辑框正文、密码框字符、组合框选中项……）。
    value: str = ""
    #: 帮助文本。
    help_text: str = ""
    #: 是不是密码框（``IsPassword=True``）。
    is_password: bool = False
    #: **未登记**的原始属性（fail-closed：一律不外发）。
    extra: Mapping[str, Any] = field(default_factory=dict, compare=False)

    def __repr__(self) -> str:
        """**值不进 repr** —— 密码明文与草稿正文不许出现在任何输出里
        （异常信息、日志、`print` 都算输出）。"""
        return (
            f"{type(self).__name__}(name={self.name!r}, control_type={self.control_type!r}, "
            f"rect={self.rect!r}, window_handle={self.window_handle!r}, "
            f"automation_id={self.automation_id!r}, is_password={self.is_password!r})"
        )

    @property
    def center(self) -> tuple[float, float]:
        left, top, right, bottom = self.rect
        return ((left + right) / 2.0, (top + bottom) / 2.0)

    def contains(self, px: float, py: float) -> bool:
        left, top, right, bottom = self.rect
        return left <= px < right and top <= py < bottom


@dataclass(frozen=True)
class ControlTreeSnapshot:
    """一次控件树读取的结果。"""

    candidates: tuple[ControlCandidate, ...] = field(default_factory=tuple)


@runtime_checkable
class CaptureSource(Protocol):
    """截图源。抓屏返回**物理像素**。"""

    name: str

    def capture(self) -> CapturedFrame:
        """抓一帧。拿到 0×0 画面时如实返回，由上层跳过该捕获源。"""
        ...


@runtime_checkable
class RegionCaptureSource(Protocol):
    """能抓**指定区域**的截图源。

    ``screenshot`` 动作给了 ``region`` 时用它（工单 M21）。
    ``rect`` 是 ``(left, top, right, bottom)``，**物理像素**。
    """

    def capture_region(self, rect: tuple[int, int, int, int]) -> CapturedFrame:
        """抓一块矩形区域。拿到 0×0 画面时如实返回，由上层跳过该捕获源。"""
        ...


@runtime_checkable
class ScreenshotPort(Protocol):
    """抓一帧画面作为观察（``screenshot`` 动作的端口）。

    ``region`` 给了就抓那一块**物理像素**矩形 ``(left, top, right, bottom)``，
    不给就抓整屏。
    """

    def screenshot(self, region: tuple[int, int, int, int] | None = None) -> CapturedFrame:
        ...


@runtime_checkable
class ControlTree(Protocol):
    """控件树通道（UIA）。"""

    def snapshot(self) -> ControlTreeSnapshot:
        """读一次控件树。"""
        ...


@runtime_checkable
class InputInjector(Protocol):
    """输入注入。真实实现走 ``SendInput``；假实现记录注入序列与时序。"""

    def input_struct_size(self) -> int:
        """``sizeof(INPUT)``（x64 必须是 40）。"""
        ...

    def probe_injection(self) -> int:
        """做一次无害的注入探针，返回 ``SendInput`` 的返回值。"""
        ...

    def move(self, x: int, y: int) -> None:
        """绝对移动到物理坐标（物理表面空间）。"""
        ...

    def button_down(self, button: str = "left") -> None:
        ...

    def button_up(self, button: str = "left") -> None:
        ...

    def scroll_delta(self, x: int, y: int, delta: int) -> None:
        """下发滚轮刻度（已换算好的 ``WHEEL_DELTA`` 单位）。"""
        ...

    def read_cursor(self) -> tuple[int, int] | None:
        """回读光标位置（物理表面空间）。"""
        ...

    def press_combo(self, combo: tuple[str, ...]) -> None:
        """按一组组合键，如 ``("ctrl", "v")``。"""
        ...

    def type_unicode(self, text: str) -> None:
        """Unicode 码点注入（绕过输入法与键盘布局）。"""
        ...

    def read_clipboard(self) -> str | None:
        """读剪贴板文本；没有文本内容时返回 ``None``。"""
        ...

    def write_clipboard(self, text: str | None) -> None:
        """写剪贴板；``None`` 表示清空。"""
        ...

    def has_non_text_clipboard_content(self) -> bool:
        """剪贴板里有没有**非文本**内容（图片、文件列表等）。

        有的话就不能走粘贴通道：剪贴板通道改写的是文本格式，
        而「把文本格式还原成没有」在 Win32 里做不到（只能 ``EmptyClipboard``
        清掉全部格式），非文本内容会被一起清掉。
        这种情况下改走 Unicode 注入通道，压根不碰剪贴板。
        """
        ...


@runtime_checkable
class WindowController(Protocol):
    """窗口控制（置前 / 查前台窗口）。"""

    def foreground_window(self) -> int | None:
        """当前前台窗口句柄（顶层窗口）。"""
        ...

    def is_foreground(self, hwnd: int) -> bool:
        """目标窗口此刻**是不是**前台窗口。

        句柄口径由实现统一（子窗口按它的根窗口算），
        否则拿子窗口句柄去比前台窗口的顶层句柄，永远不相等。
        """
        ...

    def is_occluded(self, hwnd: int, point: tuple[int, int]) -> bool:
        """目标窗口在 ``point`` 处是否被别的窗口压住。"""
        ...

    def bring_to_front(self, hwnd: int) -> bool:
        """把目标窗口置前；置前成功后它就是前台窗口。"""
        ...


# ---------------------------------------------------------------------------
# 控件定位（UIA 通道，工单 M3）
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class WindowAnchor:
    """UIA 查询的**窗口锚点**：查询的根 + 能力表的键。

    * ``hwnd`` —— 查询的根窗口；
    * ``app`` / ``version`` —— **能力表的键**（「应用 + 版本」）。

    能力表按应用**逐个**探测并缓存，因此键里必须有版本：
    「Chromium 一律失效」「UWP 一律可用」这类**类别级结论**是错的。
    """

    hwnd: int
    app: str = ""
    version: str = ""


@dataclass(frozen=True)
class LocateRequest:
    """控件定位的输入。"""

    target_description: str
    anchor: WindowAnchor
    #: 控件类型提示（``"Button"`` 之类）。给了它，通道才能在**控件类型条件**下
    #: 做名字匹配（自然语言描述不是控件名时需要）。
    control_type: str = ""


@dataclass(frozen=True)
class LocateResult:
    """控件定位的输出。

    三类失败分开报（错误码即分类）：

    * **通道级失败** → ``LOCATE_FAILED``（树读不出来：0 节点 / 超时 / 门控解不开）；
    * **覆盖失败** → ``TARGET_NOT_FOUND``（树能读，但产不出这个目标的候选）；
    * **选择失败** → ``AMBIGUOUS_TARGET`` / ``TOO_MANY_CANDIDATES``。

    三类都归「定位失败」（动作协议 v1 的三类失败字段）。

    **不变量**：``error_code is None`` ⇔ 有选中项**且**有落点
    （构造时就检查，调用方不必再判空）。
    """

    selected: ControlCandidate | None = None
    #: 最终落点（**物理表面空间**）：选中项控件框的中心。
    landing_point: tuple[int, int] | None = None
    error_code: ErrorCode | None = None
    error_message: str = ""
    #: 本次定位考虑过的候选集（拒绝执行时回报给上层的就是它）。
    candidates: tuple[ControlCandidate, ...] = ()
    #: 送进模型上下文的那份界面状态（**已脱敏**）。
    #: 定位通道已经渲染过时就带在这里（模型侧文本**只有一个出口**）。
    model_view: ModelTreeView | None = None
    #: 选中项支不支持 Invoke（UIA 的 InvokePattern）。
    invokable: bool = False
    #: 本次定位的模型调用次数（**消歧**才会调模型；候选唯一时是 0）。
    model_calls: int = 0
    #: 读树次数（执行前重读 → 至少 2）。
    observations: int = 0
    #: **查询口径**：``"window-anchored"``（窗口锚定）还是 ``"desktop-level"``
    #: （桌面级）。工单 M3 AC 8 要求验收记录里写明用的是哪一种
    #: （416 ms 与 8 秒两个口径不许混用）。
    query_scope: str = ""

    def __post_init__(self) -> None:
        if self.error_code is None and (self.selected is None or self.landing_point is None):
            raise ValueError("定位成功就必须有选中项与落点（否则是失败结果）")

    @property
    def ok(self) -> bool:
        return self.error_code is None


@runtime_checkable
class ControlLocator(Protocol):
    """控件定位端口：**一句目标描述 → 一个选中项 + 一个物理像素落点**。

    决策层不在缝内：定位通道不产生模型调用（消歧时的**指认**除外）。
    """

    def locate(self, request: LocateRequest) -> LocateResult:
        ...


@runtime_checkable
class ElementInvoker(Protocol):
    """按元素 id 触发 InvokePattern（**Invoke 优先**于坐标点击）。"""

    def invoke(self, element_id: str) -> bool:
        ...


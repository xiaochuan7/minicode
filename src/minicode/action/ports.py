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
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from .errors import ErrorCode, allows_visual_fallback

if TYPE_CHECKING:  # pragma: no cover - 只为类型标注（运行期不导入，避免两个业务包互相咬住）
    from minicode.redact.redactor import ModelTreeView

__all__ = [
    "CapturedFrame",
    "ControlCandidate",
    "ControlTreeSnapshot",
    "InjectionBlocked",
    "PixelFormat",
    "CaptureSource",
    "RegionCaptureSource",
    "ScreenshotPort",
    "ControlTree",
    "InputInjector",
    "WindowController",
    "WindowAnchor",
    "LocateRequest",
    "LocateResult",
    "SelectionEcho",
    "ControlLocator",
    "PointerRequest",
    "PointerResult",
    "CandidatePointer",
    "ElementInvoker",
    "ElevationState",
    "SecureDesktopDetector",
    "VisualAdmission",
    "VisualAdmissionPort",
    "GroundingEcho",
    "VisualLocateRequest",
    "VisualLocateResult",
    "VisualLocator",
    "ZoomRequest",
    "ZoomResult",
    "ZoomLocator",
    "DEFAULT_MAX_CANDIDATES",
    "CANDIDATE_LIMIT_SOURCE",
    "candidate_limit_source",
]


#: **通道名**（追踪记录里读得出「这一步走了哪条通道」）。
#:
#: 同一个 :class:`ControlLocator` 端口被两条通道实现（桌面 UIA / 浏览器）——
#: 通道名是**裸串**，所以给它一处常量（不写在各处）。
CHANNEL_UIA = "uia"
CHANNEL_BROWSER = "browser"
#: **视觉通道**（两级 grounding 的那一路）。它没有 :class:`ControlLocator` 实现
#: ——它是 UIA 侧**覆盖失败**之后才走的另一条路（架构决策备忘 §2.5 ①）。
CHANNEL_VISUAL = "visual"


#: **候选数上限**（运行时参数）。取值 **8**。
#:
#: 来源：ADR-0004「消歧失败、候选数超过上限（默认 8）、或指认不唯一 → 走 L4
#: 错误处理并回报候选集」+ 工单 M4 AC 4。**它是运行时参数**：调用方可以给别的值，
#: 但**取值来源必须被记录**（进追踪记录与动作记录）。
#:
#: ⚠️ 这条上限对**所有通道**是同一条（桌面通道与浏览器通道共用），
#: 所以它住在**共用**的端口模块里，不在任何一个通道里。
DEFAULT_MAX_CANDIDATES = 8

#: 候选数上限默认值的**来源**（随记录落盘，便于事后追溯这个 8 是哪来的）。
CANDIDATE_LIMIT_SOURCE = "ADR-0004「候选数超过上限（默认 8）」+ 工单 M4 AC 4"


def candidate_limit_source(max_candidates: int) -> str:
    """候选数上限的**取值来源**（运行时参数 → 来源随记录落盘）。

    用了默认值 → 写默认值的出处；调用方改了值 → 说清是**覆盖**来的，
    不然记录里会写着「默认 8」而实际用的不是 8（工单 M4 AC 4）。
    """
    if max_candidates == DEFAULT_MAX_CANDIDATES:
        return CANDIDATE_LIMIT_SOURCE
    return (
        f"调用方覆盖（默认值 {DEFAULT_MAX_CANDIDATES} 的来源：{CANDIDATE_LIMIT_SOURCE}）"
    )


class InjectionBlocked(Exception):
    """注入被拦下。

    典型成因是目标窗口的完整性级别更高（UIPI 拦截），此时 ``SendInput``
    返回 0 而**不**指示原因。**失败判定只依据返回值**。
    """


class PixelFormat(StrEnum):
    """帧的像素排布（**行优先**，每像素固定字节数）。"""

    #: 蓝绿红（3 字节/像素）：mss 带 numpy 时的路径。
    BGR = "bgr"
    #: 蓝绿红 + 透明度（4 字节/像素）：没有 numpy 时冻结原型的垫片。
    BGRA = "bgra"


@dataclass(frozen=True)
class CapturedFrame:
    """一帧截图（物理像素）。

    ``payload`` 是原始像素字节。本项目**只落它的摘要，不落原图**。

    ``pixel_format`` 说明 ``payload`` 的排布：**行优先**，每像素 3 字节（``"bgr"``）
    或 4 字节（``"bgra"``）。抓屏后端决定它（有 numpy 的 mss 路径给 BGR，
    没有 numpy 时冻结原型的垫片给 BGRA）—— 要把这帧编码成图片送模型时必须知道。
    """

    width: int
    height: int
    left: int = 0
    top: int = 0
    payload: bytes = b""
    backend: str = "fake"
    source: str = "virtual-desktop"
    #: 像素排布：``"bgr"``（3 字节/像素）或 ``"bgra"``（4 字节/像素）。
    pixel_format: PixelFormat = PixelFormat.BGR

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
    #: 控件**当前可用吗**（``IsEnabled``）。
    #:
    #: 工单 M11 AC 1 的「某个按钮变成不可用」要靠它判定 —— 可访问性树这一路
    #: 查的就是**状态**（状态 / 存在性 / 矩形变化）。
    #: 读不出来时**按可用算**（fail-closed 的另一头：宁可说「可用」，
    #: 也不把「读不到」说成「不可用」）。
    enabled: bool = True
    #: 目标**窗口标题**（工单 M11 AC 7：动作日志要含窗口句柄**与标题**）。
    #: 读不出来时是空串。
    window_title: str = ""
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
class SelectionEcho:
    """**每步回显**：模型发出的**选择结果** + 实际发送的图片尺寸。

    架构决策备忘 §3 的回显格式是「模型自己发出的东西 + 实际发送尺寸」，
    便于模型对账。**UIA 通道的消歧不产坐标**（模型只做指认，工单 M4），
    因此这里**没有坐标** —— 不许伪造成坐标回显（工单 M4 AC 9）。

    * ``chosen_numbers`` —— 模型回答的**行号**（1 起，指候选清单里的第几项）；
    * ``chosen_names`` —— 那些行对应的控件名；
    * ``sent_width`` / ``sent_height`` —— **实际发送**的那张图的尺寸
      （**没发图就是 ``None``** —— 不拿抓图尺寸冒充）。
    """

    chosen_numbers: tuple[int, ...] = ()
    chosen_names: tuple[str, ...] = ()
    sent_width: int | None = None
    sent_height: int | None = None
    #: 本次回显背后的模型调用次数（消歧恰好 1 次）。
    model_calls: int = 0

    def to_dict(self) -> dict[str, Any]:
        """落盘形状（**回显自己知道怎么渲染**，跟动作记录一个规矩）。"""
        return {
            "chosen_numbers": list(self.chosen_numbers),
            "chosen_names": list(self.chosen_names),
            "sent_width": self.sent_width,
            "sent_height": self.sent_height,
            "model_calls": self.model_calls,
        }


@dataclass(frozen=True)
class LocateRequest:
    """控件定位的输入。"""

    target_description: str
    anchor: WindowAnchor
    #: 控件类型提示（``"Button"`` 之类）。给了它，通道才能在**控件类型条件**下
    #: 做名字匹配（自然语言描述不是控件名时需要）。
    control_type: str = ""
    #: 这一帧画面（物理像素）。**消歧时它会被送给模型**当上下文
    #: （模型只做指认，不从画面里取坐标）。
    frame: CapturedFrame | None = None


@dataclass(frozen=True)
class LocateResult:
    """控件定位的输出。

    失败**分类**（工单 M9 AC 7：标识不同、互不混淆）—— 错误码即分类，
    失败类型见 :attr:`failure_type`：

    * **覆盖失败**（``coverage``）→ ``TARGET_NOT_FOUND`` / ``LOCATE_FAILED``：
      通道产不出任何候选（树读不出来 / 目标不在树里）—— **只有它允许切视觉**；
    * **选择失败**（``selection``）→ ``AMBIGUOUS_TARGET`` / ``TOO_MANY_CANDIDATES`` /
      ``RELOCATE_FAILED`` / ``INVALID_GEOMETRY`` / ``NAME_MISMATCH``：
      候选里有，但选不出 —— **禁止切视觉**；
    * **通道级失败**（``channel``）→ ``QUERY_TIMEOUT`` / ``CHANNEL_DISABLED`` /
      ``INVALID_TARGET``：这一层整体不可用 —— **禁止切视觉**。

    它们都归「定位失败」（动作协议 v1 的三类失败字段）。

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
    #: **每步回显**（消歧时才有）：模型发出的选择结果 + 实际发送的图片尺寸。
    selection_echo: SelectionEcho | None = None
    #: **失败类型**（工单 M9 AC 7）：``"coverage"`` / ``"selection"`` /
    #: ``"execution"`` / ``"channel"`` / ``"decision"``；定位成功时为空串。
    #:
    #: **能不能切视觉由它推导**（:attr:`allows_visual_fallback`）——
    #: 红线只有一处，两处数据就不可能各说各话。
    failure_type: str = ""
    #: 候选数上限（运行时参数）与**取值来源**（工单 M4 AC 4）。
    candidate_limit: int = 0
    candidate_limit_source: str = ""
    #: **这条通道的名字**（``"uia"`` / ``"browser"``）。空 = 桌面 UIA 通道。
    #:
    #: 同一个端口（:class:`ControlLocator`）被两个通道实现 ——
    #: 追踪记录要读得出「这一步走了哪条通道」（工单 M13 AC 2）。
    channel: str = ""

    def __post_init__(self) -> None:
        if self.error_code is None and (self.selected is None or self.landing_point is None):
            raise ValueError("定位成功就必须有选中项与落点（否则是失败结果）")
        if self.error_code is None:
            if self.failure_type:
                raise ValueError("定位成功就没有失败类型")
            return
        if not self.failure_type:
            raise ValueError(
                f"定位失败必须报失败类型（错误码 {self.error_code.value}）"
                "—— 失败类型要分得开（工单 M9 AC 7）"
            )
        # 值域由红线函数把关：写错的名字在这里就炸（fail-closed）。
        allows_visual_fallback(self.failure_type)

    @property
    def allows_visual_fallback(self) -> bool:
        """**能不能切视觉** —— 由失败类型推导（红线只有一处）。

        只有**覆盖失败**允许切视觉（架构决策备忘 §2.5 ①）。
        """
        if not self.failure_type:
            return False
        return allows_visual_fallback(self.failure_type)

    @property
    def ok(self) -> bool:
        return self.error_code is None


@runtime_checkable
class ControlLocator(Protocol):
    """控件定位端口：**一句目标描述 → 一个选中项 + 一个物理像素落点**。

    决策层不在缝内：定位通道不产生模型调用（消歧时的**指认**除外）。

    ⚠️ **桌面通道与浏览器通道共用这一个端口**（工单 M13 AC 2）：
    换了通道不改变缝的形状，动作协议与坐标换算也只有一套实现。
    """

    def locate(self, request: LocateRequest) -> LocateResult:
        ...


@dataclass(frozen=True)
class PointerRequest:
    """消歧请求：模型**只做指认**。

    ``model_view`` 是**已脱敏**的模型侧文本（脱敏器是模型侧文本的唯一出口）；
    ``candidates`` 是同一批候选的结构化形状（元素 id 与之一一对应）。
    ``frame`` 是这一帧画面（物理像素）—— 送模型当上下文，
    **模型不从画面里取坐标**（工单 M4）。
    """

    target_description: str
    candidates: tuple[ControlCandidate, ...]
    model_view: str
    frame: CapturedFrame | None = None


@dataclass(frozen=True)
class PointerResult:
    """消歧结果：指认了哪些元素。

    **必须恰好一个**，且必须在候选集内；否则算消歧失败（选择失败）。
    """

    chosen_ids: tuple[str, ...] = ()
    #: **每步回显**：模型发出的选择结果 + 实际发送的图片尺寸（**不含坐标**）。
    echo: SelectionEcho | None = None


@runtime_checkable
class CandidatePointer(Protocol):
    """消歧指认（候选不唯一时用）。候选唯一时**不调**它（零模型调用）。"""

    def point(self, request: PointerRequest) -> PointerResult:
        ...


@runtime_checkable
class ElementInvoker(Protocol):
    """按元素 id 触发 InvokePattern（**Invoke 优先**于坐标点击）。"""

    def invoke(self, element_id: str) -> bool:
        ...


@dataclass(frozen=True)
class ElevationState:
    """**提权 / 安全桌面**的状态（架构决策备忘 §2.6 C-07）。

    * ``elevated`` —— 此刻是不是 UAC 安全桌面（提权弹窗把输入桌面切走了）；
    * ``reason`` —— 人读的原因（没检测到时也写清楚「怎么知道的」）；
    * ``desktop_name`` —— 输入桌面的名字（本机正常是 ``Default``，
      安全桌面是 ``Winlogon``）。

    ⚠️ **只检测，不降低**：C-07 明写「代码里不得默认开启
    ``PromptOnSecureDesktop=0``」（那等于关掉 UAC 的视觉提示）。
    仓库里没有这条路径，也不许加。
    """

    elevated: bool
    reason: str = ""
    desktop_name: str = ""


@runtime_checkable
class SecureDesktopDetector(Protocol):
    """检测当前是不是 **UAC 安全桌面**（C-07）。

    形状：**一次检测 → 一个状态**。换注入点时这个形状不许变。
    """

    def detect(self) -> ElevationState:
        ...


@dataclass(frozen=True)
class VisualAdmission:
    """**视觉通道准入判定**（工单 M5）。

    它是标定结论的翻译：视觉通道能不能用，取决于**落点等价**判据 ——
    只有「存在使物理落点分歧 > 标定容差的替代标度」才不可用。
    标度判定为**多解**、但各等价标度族的落点分歧都在容差内时，
    视觉通道**允许继续**（``available=True`` + ``landing_equivalence="equivalent"``）。

    字段
    ----
    available
        视觉通道可用吗？
    reason
        人读的理由（不可用时写清是「标定失败」还是「存在分歧的替代标度」）。
    scale
        判定标度（标度名）。
    scale_verdict
        标度判定：``"unique"``（唯一命中）/ ``"multi"``（多解）。
    landing_equivalence
        落点等价：``"equivalent"``（容差内等价）/ ``"divergent"``（分歧）。
    equivalent_scales
        等价标度列表（标度名）。
    """

    available: bool
    reason: str = ""
    scale: str = ""
    scale_verdict: str = ""
    landing_equivalence: str = ""
    equivalent_scales: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        """落盘形状（回显/追踪记录自己知道怎么渲染）。"""
        return {
            "available": self.available,
            "reason": self.reason,
            "scale": self.scale,
            "scale_verdict": self.scale_verdict,
            "landing_equivalence": self.landing_equivalence,
            "equivalent_scales": list(self.equivalent_scales),
        }


@runtime_checkable
class VisualAdmissionPort(Protocol):
    """视觉通道准入判定端口（工单 M5 的硬闸门）。

    形状：问一次「视觉通道现在能不能用」，答一个 :class:`VisualAdmission`。
    """

    def admit(self) -> VisualAdmission:
        ...


# ---------------------------------------------------------------------------
# 视觉通道（两级 grounding，工单 M6）
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GroundingEcho:
    """**每步回显**（视觉通道）：模型自己发出的坐标 + 实际发送的图片尺寸。

    与 :class:`SelectionEcho`（UIA 通道的消歧回显）同一条规矩：回显的是
    「模型自己发出的东西 + 实际发送尺寸」，让模型能对账自己的坐标。

    * ``stage`` —— 哪一级（``"coarse"`` 粗定位 / ``"refine"`` 精定位 / ``"zoom"`` 放大）；
    * ``raw_x`` / ``raw_y`` —— **模型发出的原始数值**（不换算、不解释）；
    * ``sent_width`` / ``sent_height`` —— **模型实际看到的那张图**的尺寸
      （服务端可能把大图缩小：本机实测 2560 宽 → 约 2048 宽）——
      报这个尺寸模型才能对账自己给的坐标；不是原图宽、也不是写死的常量；
    * ``image_source`` —— 那张图从哪来（整屏截图 / 从原始帧裁出的 crop / 回源重抓）。
    """

    stage: str
    raw_x: float | None = None
    raw_y: float | None = None
    sent_width: int | None = None
    sent_height: int | None = None
    image_source: str = ""

    def to_dict(self) -> dict[str, Any]:
        """落盘形状（回显自己知道怎么渲染）。"""
        return {
            "stage": self.stage,
            "raw_x": self.raw_x,
            "raw_y": self.raw_y,
            "sent_width": self.sent_width,
            "sent_height": self.sent_height,
            "image_source": self.image_source,
        }


@dataclass(frozen=True)
class VisualLocateRequest:
    """视觉通道（两级 grounding）的输入。"""

    #: 目标描述（自然语言）。
    target_description: str
    #: **整屏**物理帧（原始物理分辨率，1 像素 = 1 物理像素）。
    #: 裁剪窗口从这一帧上 1:1 裁下（不缩放）。
    frame: CapturedFrame
    #: 可裁剪区域（物理表面空间的矩形 ``(left, top, right, bottom)``）。
    surface: tuple[int, int, int, int]


@dataclass(frozen=True)
class VisualLocateResult:
    """视觉通道的输出（最终落点 + 观测数据）。

    * ``landing_point`` —— **最终落点**（物理表面空间）。视觉通道内它**只在
      crop 尺度上产生**；精定位失败并回落时它是粗定位点。
    * ``coarse_point`` —— 粗定位点（物理表面空间）。
    * ``crop_plan`` —— 裁剪计划（落盘形状，含「目标是否完整包含」）。
    * ``gate_hit`` / ``gate_reasons`` / ``gate_undecidable`` —— **门控埋点**
      （工单 M7 AC 4）：命中与否、命中原因（枚举）、**不可判定**的条件（枚举）。
    * ``refine_enabled`` —— 这一步**是否真的开启了 crop 精定位**（门控命中，
      且粗定位可用时才是真）。工单 AC 4 的「开启精定位的样本数」按它算 ——
      **整屏第二遍不算精定位**（它不是 crop 精定位，也永不回落）。
    * ``fallback`` / ``fallback_cause`` / ``fallback_reason`` —— 是否回落到
      粗定位结果 + 回落原因（枚举）+ 人读的原因。
    * ``echoes`` —— 每步回显（模型发出的坐标 + 实际发送尺寸）。
    """

    landing_point: tuple[int, int] | None = None
    coarse_point: tuple[float, float] | None = None
    crop_plan: Mapping[str, Any] | None = None
    fallback: bool = False
    fallback_cause: str = ""
    fallback_reason: str = ""
    gate_hit: bool = False
    gate_reasons: tuple[str, ...] = ()
    gate_undecidable: tuple[str, ...] = ()
    refine_enabled: bool = False
    error_code: ErrorCode | None = None
    error_message: str = ""
    echoes: tuple[GroundingEcho, ...] = ()
    model_calls: int = 0
    #: 这次换算用的**标度**（来自标定结果）。
    scale: str = ""

    @property
    def ok(self) -> bool:
        return self.error_code is None


@runtime_checkable
class VisualLocator(Protocol):
    """视觉通道端口（工单 M6）：一句目标描述 → 一个物理像素落点。

    两级 grounding：整屏粗定位 → 裁剪 → crop 内精定位。
    **最终落点只在 crop 尺度上产生**（精定位失败时回落到粗定位结果）。
    """

    def locate(self, request: VisualLocateRequest) -> VisualLocateResult:
        ...


@dataclass(frozen=True)
class ZoomRequest:
    """放大动作的输入。

    ⚠️ **放大与裁剪不是一回事**（工单 M6）：

    * 放大是**动作协议里的一个动作**：指定区域**回源重抓**后送模型看清楚，
      模型回答的仍是**整屏坐标**；
    * 裁剪是两级定位**内部**的取景：从原始物理分辨率图取窗口，
      模型报 **crop 内坐标**，由系统加裁剪原点偏移映射回全屏。
    """

    #: 目标描述（自然语言）。
    target_description: str
    #: 要放大的区域（物理表面空间的矩形 ``(left, top, right, bottom)``）。
    region: tuple[int, int, int, int]
    #: **回源重抓**拿到的区域画面（物理像素，1:1）。
    region_frame: CapturedFrame
    #: 整屏尺寸（物理像素）—— 模型的回答按整屏坐标解释。
    full_screen_size: tuple[int, int]
    #: 物理表面矩形（越界判定用）。
    surface: tuple[int, int, int, int]


@dataclass(frozen=True)
class ZoomResult:
    """放大动作的结果。"""

    #: 模型给的**整屏坐标**换算后的物理像素落点。
    landing_point: tuple[int, int] | None = None
    #: 放大路径**不产生裁剪计划**（恒为 ``None``）—— 与裁剪路径的契约区别。
    crop_plan: None = None
    error_code: ErrorCode | None = None
    error_message: str = ""
    echo: GroundingEcho | None = None
    model_calls: int = 0
    scale: str = ""

    @property
    def ok(self) -> bool:
        return self.error_code is None


@runtime_checkable
class ZoomLocator(Protocol):
    """放大通道端口（工单 M6）：指定区域 → 整屏坐标落点。"""

    def locate(self, request: ZoomRequest) -> ZoomResult:
        ...


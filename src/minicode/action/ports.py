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
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

__all__ = [
    "CapturedFrame",
    "ControlCandidate",
    "ControlTreeSnapshot",
    "InjectionBlocked",
    "CaptureSource",
    "ControlTree",
    "InputInjector",
    "WindowController",
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
    """

    name: str
    control_type: str = ""
    rect: tuple[int, int, int, int] = (0, 0, 0, 0)
    window_handle: int | None = None
    automation_id: str | None = None

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

"""假实现：截图源 / 控件树 / 输入注入 / 窗口控制。

有了它们，整条动作执行缝可以在**不碰真桌面、不需要凭据、不产生费用**的条件下被观测。
假实现只替换缝以下的外部依赖，**不改变缝的端口形状**。
"""

from __future__ import annotations

from .ports import (
    CapturedFrame,
    ControlTreeSnapshot,
    InjectionBlocked,
)

__all__ = [
    "VirtualClock",
    "FakeCaptureSource",
    "FakeControlTree",
    "FakeInputInjector",
    "FakeWindowController",
]


class VirtualClock:
    """虚拟时钟：``sleep`` 只推进时间，不真的等待。

    单测因此可以在毫秒级内跑完带 60 ms / 40 ms 时序的点击，
    同时让时序断言仍然看到真实的时间差。
    """

    def __init__(self, start: float = 0.0):
        self.now = float(start)
        self.sleeps: list[float] = []

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(float(seconds))
        self.now += float(seconds)

    def time(self) -> float:
        return self.now


class FakeCaptureSource:
    """假截图源：产出固定尺寸的合成画面。"""

    def __init__(
        self,
        width: int = 640,
        height: int = 480,
        payload: bytes | None = None,
        backend: str = "fake",
        source: str = "virtual-desktop",
        name: str = "fake-capture",
    ):
        self.name = name
        self.width = width
        self.height = height
        self.backend = backend
        self.source = source
        self._payload = payload if payload is not None else bytes(width * height * 3)
        self.calls = 0

    def capture(self) -> CapturedFrame:
        self.calls += 1
        return CapturedFrame(
            width=self.width,
            height=self.height,
            left=0,
            top=0,
            payload=self._payload,
            backend=self.backend,
            source=self.source,
        )


class FakeControlTree:
    """假控件树：产出预设的候选集。

    可以构造出「0 节点」「同名多候选」等边界态。
    """

    def __init__(self, candidates=()):
        self.candidates = tuple(candidates)
        self.calls = 0

    def snapshot(self) -> ControlTreeSnapshot:
        self.calls += 1
        return ControlTreeSnapshot(self.candidates)


class FakeInputInjector:
    """假输入注入：记录注入序列，供断言。

    * ``events`` 是按发生顺序记录的注入事件元组。
    * ``fail_injection`` 让注入抛出 :class:`InjectionBlocked`（模拟 UIPI 拦截）。
    * ``fail_paste`` 让粘贴通道失败（逼上层退回 Unicode 通道）。
    """

    def __init__(
        self,
        cursor: tuple[int, int] = (0, 0),
        struct_size: int = 40,
        probe_return: int = 1,
        fail_injection: bool = False,
        fail_paste: bool = False,
        readback_override: tuple[int, int] | None = None,
        journal: list | None = None,
        non_text_clipboard: bool = False,
    ):
        self.journal = journal if journal is not None else []
        self.events: list[tuple] = self.journal
        self.cursor = cursor
        self._struct_size = struct_size
        self._probe_return = probe_return
        self._fail_injection = fail_injection
        self._fail_paste = fail_paste
        self._readback_override = readback_override
        self._clipboard: str | None = None
        self._non_text_clipboard = non_text_clipboard

    # -- 探针 -------------------------------------------------------------

    def input_struct_size(self) -> int:
        return self._struct_size

    def probe_injection(self) -> int:
        self.events.append(("probe",))
        return self._probe_return

    # -- 鼠标 -------------------------------------------------------------

    def move(self, x: int, y: int) -> None:
        self._guard()
        self.events.append(("move", int(x), int(y)))
        self.cursor = (int(x), int(y))

    def button_down(self, button: str = "left") -> None:
        self._guard()
        self.events.append(("button_down", button))

    def button_up(self, button: str = "left") -> None:
        self._guard()
        self.events.append(("button_up", button))

    def scroll_delta(self, x: int, y: int, delta: int) -> None:
        self._guard()
        self.events.append(("scroll_delta", int(x), int(y), int(delta)))

    def read_cursor(self) -> tuple[int, int] | None:
        if self._readback_override is not None:
            return self._readback_override
        return self.cursor

    # -- 键盘 -------------------------------------------------------------

    def press_combo(self, combo: tuple[str, ...]) -> None:
        if self._fail_paste and tuple(combo) == ("ctrl", "v"):
            raise OSError("剪贴板通道不可用")
        self._guard()
        self.events.append(("press_combo", tuple(combo)))

    def type_unicode(self, text: str) -> None:
        self._guard()
        self.events.append(("type_unicode", text))

    # -- 剪贴板 -----------------------------------------------------------

    def read_clipboard(self) -> str | None:
        return self._clipboard

    def write_clipboard(self, text: str | None) -> None:
        self._clipboard = text

    def has_non_text_clipboard_content(self) -> bool:
        return self._non_text_clipboard

    # -- 内部 -------------------------------------------------------------

    def _guard(self) -> None:
        if self._fail_injection:
            raise InjectionBlocked("SendInput 只插入了 0/1 个事件（模拟 UIPI 拦截）")


class FakeWindowController:
    """假窗口控制：记录前台窗口的变更。

    ``root_of`` 建模「候选句柄是子窗口、置前后台窗口是它的根窗口」：
    子窗口句柄与顶层的前台窗口句柄**本来就不相等**，
    只有按同一个口径（根窗口）比，才判得出「已经是前台窗口」。
    """

    def __init__(
        self,
        foreground: int | None = None,
        bring_fails: bool = False,
        journal=None,
        root_of: dict[int, int] | None = None,
    ):
        self._foreground = foreground
        self._bring_fails = bring_fails
        self.brought: list[int] = []
        self.journal = journal if journal is not None else []
        self.root_of = dict(root_of or {})

    def foreground_window(self) -> int | None:
        return self._foreground

    def is_foreground(self, hwnd: int) -> bool:
        if self._foreground is None:
            return False
        return self._foreground == self.root_of.get(int(hwnd), int(hwnd))

    def is_occluded(self, hwnd: int, point: tuple[int, int] = (0, 0)) -> bool:
        """前台窗口不是目标窗口时，假定目标被遮挡。"""
        return self._foreground is not None and self._foreground != int(hwnd)

    def bring_to_front(self, hwnd: int) -> bool:
        self.brought.append(int(hwnd))
        self.journal.append(("bring_to_front", int(hwnd)))
        if self._bring_fails:
            return False
        self._foreground = self.root_of.get(int(hwnd), int(hwnd))
        return True


class FakeMetrics:
    """假屏幕度量（用于在 CI 上验证自检的判定逻辑）。"""

    def __init__(
        self,
        awareness: str = "PER_MONITOR_AWARE_V2",
        physical: tuple[int, int] = (2560, 1600),
        device_caps_dpi: int = 144,
    ):
        self._awareness = awareness
        self._physical = physical
        self._device_caps_dpi = device_caps_dpi

    def awareness(self) -> str:
        return self._awareness

    def physical_size(self) -> tuple[int, int]:
        return self._physical

    def device_caps_dpi(self) -> int:
        return self._device_caps_dpi

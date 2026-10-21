"""**真机 UIA 通道的接线**（一处）。

`minicode.uia` 的命令行（工单 M3/M4）与 `minicode.serve` 的对外形态
（工单 M18）都要「在某个窗口上起一条 UIA 通道的缝」——
接线只有**这一份**，两处都用它（否则接线会各说各话）。

接线包含：窗口锚点 → 定点查询端口 → 能力表 → UIA 通道 → 动作执行缝，
外加**提权检测**（C-07）与**审批闸门**（工单 M16）。
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass

from minicode.action.executor import DoubleClickParams
from minicode.action.geometry import VirtualDesktop
from minicode.action.ports import (
    CaptureSource,
    InputInjector,
    SecureDesktopDetector,
    WindowAnchor,
    WindowController,
)
from minicode.action.records import ListTraceSink
from minicode.action.seam import ActionExecutionSeam
from minicode.approval.gate import ApprovalGate
from minicode.uia.capability import CapabilityTable
from minicode.uia.channel import UiaChannel
from minicode.uia.ports import CandidatePointer
from minicode.uia.windows import (
    WindowsCapabilityProbe,
    WindowsElementInvoker,
    WindowsTreeUnblocker,
    WindowsUiaQueryPort,
    describe_window,
)
from minicode.verification.layer import Verifier

__all__ = ["Desktop", "build_desktop", "find_window_by_class"]


def find_window_by_class(class_name: str) -> int:
    """按**类名**找窗口（用 ``FindWindowW``，不枚举）。

    枚举会漏掉任务栏（``docs/00-现状.md`` §6.1 记着这条环境效应），
    所以这里走 ``FindWindowW``。
    """
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
    user32.FindWindowW.restype = wintypes.HWND
    hwnd = user32.FindWindowW(class_name, None)
    if not hwnd:
        raise SystemExit(f"找不到类名为 {class_name!r} 的窗口")
    return int(hwnd)


@dataclass(frozen=True)
class Desktop:
    """真机 UIA 通道的一次接线。

    :attr:`new_seam` 造一条**带指定追踪出口**的缝 ——
    每次调用要只拿**这一次**的追踪记录，出口就得是每次新建的。
    """

    anchor: WindowAnchor
    port: WindowsUiaQueryPort
    capability: CapabilityTable
    channel: UiaChannel
    capture_sources: tuple[CaptureSource, ...]
    injector: InputInjector
    window_controller: WindowController
    secure_desktop: SecureDesktopDetector
    approval_gate: ApprovalGate
    double_click_params: DoubleClickParams
    invoker: WindowsElementInvoker
    sleeper: Callable[[float], None]
    clock: Callable[[], float]
    virtual_desktop: VirtualDesktop

    def new_seam(
        self,
        sink: ListTraceSink,
        *,
        verifier: Verifier | None = None,
    ) -> ActionExecutionSeam:
        """造一条缝（追踪出口是 ``sink``）。"""
        return ActionExecutionSeam(
            capture_sources=self.capture_sources,
            tree=None,
            injector=self.injector,
            window_controller=self.window_controller,
            virtual_desktop=self.virtual_desktop,
            sleeper=self.sleeper,
            clock=self.clock,
            double_click_params=self.double_click_params,
            trace_sink=sink,
            locator=self.channel,
            invoker=self.invoker,
            anchor=self.anchor,
            verifier=verifier,
            # **提权 / 安全桌面检测**（C-07）：在安全桌面上零动作下发、交给人。
            secure_desktop=self.secure_desktop,
            # **审批闸门**（工单 M16）：危险动作要人点头，没批就零动作下发。
            approval_gate=self.approval_gate,
        )


def build_desktop(
    *,
    window_class: str = "Shell_TrayWnd",
    pointer: CandidatePointer | None = None,
    redactor: object | None = None,
) -> Desktop:
    """接一条真机 UIA 通道。

    :param window_class: 窗口类名（``FindWindowW`` 找）。
    :param pointer: 消歧指认端（``None`` = 不做消歧，候选不唯一就明确报错）。
    :param redactor: 脱敏器（做消歧时**必须**给 —— 脱敏没上线不许把控件树送模型）。
    """
    # ⚠️ **DPI 感知必须在任何依赖它的调用之前声明**（C-01）：
    # `from minicode.exec import dpi` 一导入就设 PMv2 —— 它得排在
    # `current_virtual_desktop()` / 截图 / 注入**之前**（导入顺序无关，
    # 调用顺序有关）。
    from minicode.action.windows import (
        WindowsCaptureSource,
        WindowsInputInjector,
        WindowsSecureDesktopDetector,
        WindowsWindowController,
        current_virtual_desktop,
        system_double_click_params,
    )
    from minicode.approval.console import console_gate
    from minicode.exec import dpi  # noqa: F401  (导入即声明 PMv2)
    from minicode.uia.windows import WindowsElementInvoker

    hwnd = find_window_by_class(window_class)
    anchor = describe_window(hwnd)
    port = WindowsUiaQueryPort()
    table = CapabilityTable(
        WindowsCapabilityProbe(), unblocker=WindowsTreeUnblocker()
    )
    channel = UiaChannel(query=port, capability=table, pointer=pointer, redactor=redactor)
    desktop = current_virtual_desktop()
    return Desktop(
        anchor=anchor,
        port=port,
        capability=table,
        channel=channel,
        capture_sources=(WindowsCaptureSource(),),
        injector=WindowsInputInjector(desktop),
        window_controller=WindowsWindowController(),
        secure_desktop=WindowsSecureDesktopDetector(),
        approval_gate=console_gate(),
        double_click_params=system_double_click_params(),
        invoker=WindowsElementInvoker(port),
        sleeper=time.sleep,
        clock=time.perf_counter,
        virtual_desktop=desktop,
    )

"""启动自检：C-01（进程 DPI 感知）与 C-02（注入可用性）。

**任一项不成立就中止，不进动作层、不产出动作记录。**

C-01 的三项判据：

1. 屏幕物理度量 == 2560×1600（返回 1707 说明 DPI 感知没生效）；
2. 设备 caps 的逻辑 DPI == 144；
3. 抓图尺寸 == 2560×1600 且**不为 0×0**。

C-02 的两项判据：

1. ``sizeof(INPUT) == 40``（x64；写成两个独立成员会是 64，``SendInput`` 静默失败）；
2. 每次注入的返回值 == 本次注入条数——**失败判定只依据返回值**，
   ``GetLastError`` 在 UIPI 阻断时并不指示原因。
"""

from __future__ import annotations

import ctypes
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from .ports import CaptureSource, InputInjector

__all__ = [
    "StartupExpectations",
    "StartupCheckResult",
    "StartupSelfCheck",
    "InjectionCheckResult",
    "InjectionSelfCheck",
    "MetricsProvider",
    "EXPECTED_INPUT_SIZE",
]

#: ``sizeof(INPUT)`` 的期望值：x64 是 40，x86 是 28。
EXPECTED_INPUT_SIZE = 40 if ctypes.sizeof(ctypes.c_void_p) == 8 else 28

#: 进程 DPI 感知模式必须落在这里。
REQUIRED_AWARENESS = "PER_MONITOR_AWARE_V2"


@runtime_checkable
class MetricsProvider(Protocol):
    """屏幕度量与 DPI 状态的来源。"""

    def awareness(self) -> str:
        """当前 DPI 感知模式名。"""
        ...

    def physical_size(self) -> tuple[int, int]:
        """屏幕物理度量（物理像素）。"""
        ...

    def device_caps_dpi(self) -> int:
        """``GetDeviceCaps(LOGPIXELSX)`` 的值。"""
        ...


@dataclass(frozen=True)
class StartupExpectations:
    """目标机上的期望值。"""

    physical_width: int = 2560
    physical_height: int = 1600
    device_caps_dpi: int = 144


@dataclass(frozen=True)
class StartupCheckResult:
    """C-01 的结果。"""

    ok: bool
    awareness: str = ""
    physical: tuple[int, int] | None = None
    device_caps_dpi: int | None = None
    capture_size: tuple[int, int] | None = None
    failures: tuple[str, ...] = ()
    #: 抓屏后端抛出来的原始错误（结构化失败，不崩进程）。
    error: str = ""

    def describe(self) -> str:
        parts = [
            f"awareness={self.awareness}",
            f"physical={self.physical}",
            f"device_caps_dpi={self.device_caps_dpi}",
            f"capture={self.capture_size}",
        ]
        if self.failures:
            parts.append("失败项=" + ",".join(self.failures))
        if self.error:
            parts.append(f"错误={self.error}")
        return " | ".join(parts)


class StartupSelfCheck:
    """C-01 启动自检。"""

    def __init__(
        self,
        metrics: MetricsProvider,
        capture: CaptureSource | Sequence[CaptureSource],
        expectations: StartupExpectations | None = None,
    ):
        self.metrics = metrics
        self.sources = tuple(capture) if isinstance(capture, (list, tuple)) else (capture,)
        self.expectations = expectations or StartupExpectations()

    def run(self) -> StartupCheckResult:
        failures: list[str] = []
        awareness = self.metrics.awareness()
        if awareness != REQUIRED_AWARENESS:
            failures.append("dpi-awareness")

        physical = self.metrics.physical_size()
        expected_physical = (self.expectations.physical_width, self.expectations.physical_height)
        if tuple(physical) != expected_physical:
            failures.append("physical-metrics")

        dpi = self.metrics.device_caps_dpi()
        if int(dpi) != self.expectations.device_caps_dpi:
            failures.append("device-caps-dpi")

        # 抓屏：拿到 0×0 画面的捕获源直接跳过（哨兵值一条）。
        capture_size: tuple[int, int] | None = None
        capture_error = ""
        for source in self.sources:
            try:
                frame = source.capture()
            except Exception as exc:  # 抓屏后端炸了也要给出结构化失败，不许崩进程
                failures.append("capture-error")
                capture_error = f"{type(exc).__name__}: {exc}"
                break
            if frame.is_empty:
                continue
            capture_size = (frame.width, frame.height)
            break
        if capture_size != expected_physical and "capture-error" not in failures:
            failures.append("capture-size")

        return StartupCheckResult(
            ok=not failures,
            awareness=awareness,
            physical=tuple(physical),
            device_caps_dpi=int(dpi),
            capture_size=capture_size,
            failures=tuple(failures),
            error=capture_error,
        )


@dataclass(frozen=True)
class InjectionCheckResult:
    """C-02 的结果。"""

    ok: bool
    sizeof_input: int = 0
    sizeof_expected: int = EXPECTED_INPUT_SIZE
    probe_return: int | None = None
    failures: tuple[str, ...] = ()

    def describe(self) -> str:
        parts = [
            f"sizeof(INPUT)={self.sizeof_input} (期望 {self.sizeof_expected})",
            f"probe_return={self.probe_return}",
        ]
        if self.failures:
            parts.append("失败项=" + ",".join(self.failures))
        return " | ".join(parts)


class InjectionSelfCheck:
    """C-02 注入自检。"""

    def __init__(self, injector: InputInjector, expected_struct_size: int | None = None):
        self.injector = injector
        self.expected_struct_size = expected_struct_size or EXPECTED_INPUT_SIZE

    def run(self) -> InjectionCheckResult:
        failures: list[str] = []
        sizeof_input = int(self.injector.input_struct_size())
        if sizeof_input != self.expected_struct_size:
            failures.append("input-struct-size")
        returned = int(self.injector.probe_injection())
        # 失败判定只依据返回值：返回值必须等于本次注入条数（探针注入 1 条）。
        if returned != 1:
            failures.append("probe-return-value")
        return InjectionCheckResult(
            ok=not failures,
            sizeof_input=sizeof_input,
            sizeof_expected=self.expected_struct_size,
            probe_return=returned,
            failures=tuple(failures),
        )

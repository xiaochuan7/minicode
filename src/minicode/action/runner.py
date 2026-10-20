"""最小闭环运行器：一条命令跑完 M0 的闭环。

顺序（架构备忘 §6 M0）::

    DPI 感知自检（不通过直接中止，不进动作层）
      → 注入自检
      → 抓一帧物理像素整屏
      → 从控件树唯一定位一个已知按钮
      → 落到物理表面空间的一个点
      → 「移动 → 停 60ms → 按下 → 停 40ms → 抬起」注入一次真实点击
      → 回读光标
      → 写出恰好一条动作记录

自检不过时**不产出动作记录**——动作记录只描述真正发生过的动作。
"""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from .executor import DoubleClickParams, TimingPolicy
from .geometry import VirtualDesktop
from .ports import CaptureSource, ControlTree, InputInjector, WindowController
from .records import ActionLog, ActionRecord, ListTraceSink, build_record
from .seam import ActionExecutionSeam, SeamRequest, SeamResult
from .selfcheck import (
    InjectionCheckResult,
    InjectionSelfCheck,
    MetricsProvider,
    StartupCheckResult,
    StartupExpectations,
    StartupSelfCheck,
)

__all__ = ["LoopOutcome", "run_minimal_loop"]


@dataclass(frozen=True)
class LoopOutcome:
    """一次闭环的结果。"""

    ok: bool
    startup: StartupCheckResult
    injection: InjectionCheckResult | None = None
    result: SeamResult | None = None
    record: ActionRecord | None = None
    message: str = ""


def run_minimal_loop(
    *,
    target_description: str,
    capture_sources: Sequence[CaptureSource],
    tree: ControlTree,
    injector: InputInjector,
    metrics: MetricsProvider,
    log_directory: str | Path,
    window_controller: WindowController | None = None,
    virtual_desktop: VirtualDesktop | None = None,
    expectations: StartupExpectations | None = None,
    sleeper: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.perf_counter,
    timing: TimingPolicy | None = None,
    double_click_params: DoubleClickParams | None = None,
    capture_to_physical_scale: float = 1.0,
) -> LoopOutcome:
    """跑一次最小闭环。"""
    startup = StartupSelfCheck(metrics, tuple(capture_sources), expectations).run()
    if not startup.ok:
        return LoopOutcome(
            ok=False,
            startup=startup,
            message=f"启动自检未通过（{', '.join(startup.failures)}），中止，不进动作层",
        )

    injection = InjectionSelfCheck(injector).run()
    if not injection.ok:
        return LoopOutcome(
            ok=False,
            startup=startup,
            injection=injection,
            message=f"注入自检未通过（{', '.join(injection.failures)}），中止，不进动作层",
        )

    sink = ListTraceSink()
    seam = ActionExecutionSeam(
        capture_sources=capture_sources,
        tree=tree,
        injector=injector,
        window_controller=window_controller,
        virtual_desktop=virtual_desktop,
        sleeper=sleeper,
        clock=clock,
        timing=timing,
        double_click_params=double_click_params,
        trace_sink=sink,
        capture_to_physical_scale=capture_to_physical_scale,
    )
    result = seam.run(SeamRequest(target_description=target_description))

    record = build_record(sink.traces[-1]) if sink.traces else None
    if record is not None:
        ActionLog(log_directory).append(record)
    return LoopOutcome(
        ok=result.ok,
        startup=startup,
        injection=injection,
        result=result,
        record=record,
        message="" if result.ok else (result.result.error_message or ""),
    )

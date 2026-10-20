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
from dataclasses import dataclass, replace
from pathlib import Path

from minicode.approval.gate import ApprovalGate

from .errors import ErrorCode
from .executor import DoubleClickParams, TimingPolicy
from .geometry import VirtualDesktop
from .ports import (
    CaptureSource,
    ControlTree,
    InputInjector,
    SecureDesktopDetector,
    WindowController,
)
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

__all__ = ["LoopOutcome", "LandingVerification", "run_minimal_loop", "LANDING_TOLERANCE_PX"]

#: 落点与控件框中心的允许偏差（物理像素）。验收项 6：≤2 px。
LANDING_TOLERANCE_PX = 2


@dataclass(frozen=True)
class LandingVerification:
    """落点复验（验收项 6）。

    动作执行完之后**再读一次控件树**，落点必须落在重读到的控件框中心附近。
    复验必须能给出明确结论——找不到目标也算不通过，不能静默跳过。
    """

    found: bool
    delta_px: float | None
    ok: bool
    message: str = ""


@dataclass(frozen=True)
class LoopOutcome:
    """一次闭环的结果。"""

    ok: bool
    startup: StartupCheckResult
    injection: InjectionCheckResult | None = None
    result: SeamResult | None = None
    record: ActionRecord | None = None
    message: str = ""
    #: 落点复验结果（动作后重读控件树，与落点比较）。
    verification: LandingVerification | None = None


def _verify_landing(tree, target_description, landing) -> LandingVerification:
    """动作后重读控件树，复验落点。"""
    try:
        snapshot = tree.snapshot()
    except Exception as exc:
        return LandingVerification(False, None, False, f"复验时读控件树失败：{exc}")
    target = target_description.strip().lower()
    for candidate in snapshot.candidates:
        if candidate.name.strip().lower() == target:
            cx, cy = candidate.center
            delta = max(abs(landing[0] - cx), abs(landing[1] - cy))
            ok = delta <= LANDING_TOLERANCE_PX
            return LandingVerification(
                True,
                delta,
                ok,
                ""
                if ok
                else f"落点与重读控件框中心相差 {delta:.1f} px（容差 {LANDING_TOLERANCE_PX} px）",
            )
    return LandingVerification(False, None, False, "复验时控件树里找不到目标，不能声称闭环打通")


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
    secure_desktop: SecureDesktopDetector | None = None,
    approval_gate: ApprovalGate | None = None,
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
        secure_desktop=secure_desktop,
        approval_gate=approval_gate,
    )
    result = seam.run(SeamRequest(target_description=target_description))

    # 落点复验：动作之后再读一次控件树，落点必须落在重读到的控件框中心附近。
    # **复验在写盘之前做**——没过就不能声称闭环打通，落盘的记录也不能说 ok。
    verification = None
    ok = result.ok
    message = "" if result.ok else (result.result.error_message or "")
    if result.ok and result.final_point is not None:
        verification = _verify_landing(tree, target_description, result.final_point)
        if not verification.ok:
            ok = False
            message = verification.message

    record = build_record(sink.traces[-1]) if sink.traces else None
    if record is not None:
        if verification is not None:
            record = replace(
                record,
                ok=ok,
                error_code=None if ok else ErrorCode.LANDING_MISMATCH.value,
                error_message=None if ok else verification.message,
                landing_delta_px=verification.delta_px,
                landing_verified=verification.found,
            )
        ActionLog(log_directory).append(record)
    return LoopOutcome(
        ok=ok,
        startup=startup,
        injection=injection,
        result=result,
        record=record,
        message=message,
        verification=verification,
    )

"""**真机接线**（工单 M18 / M19；W2 加运行护栏）。

命令行与 HTTP 两个前端在真机上跑的时候，**接线只有这一份**：
:func:`minicode.uia.desktop.build_desktop`（UIA 通道 + 真机注入 + 审批闸门 +
提权检测）—— 与 :mod:`minicode.uia` 的命令行**同一套口径**。

W2 起，给 ``guard_policy`` 就得到**带护栏的真机接线**（四级授权 + 十条护栏）：
急停 watchdog / 空闲让位 / 预算与熔断账本 / 全程录制 / L 级审批门 /
急停感知注入器（L0 与 dry-run 在注入器层换 dry-run 形态）。
不给 ``guard_policy``（缺省）→ 接线与 v0.1.4 **逐字节一致**——
护栏是**加法**，不改变既有调用方的任何行为。

⚠️ **动真机前必须持有真机占用锁**（真机占用锁：人机互斥）——
调用方（:mod:`minicode.serve.cli` / :mod:`minicode.serve.http` /
:mod:`minicode.mcp`）负责拿锁与放锁，本模块只管接线。
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from minicode.action.records import ListTraceSink

from .service import ActionService

__all__ = ["build_real_service"]


def build_real_service(
    log_directory: str | Path,
    *,
    window_class: str = "Shell_TrayWnd",
    guard_policy: object | None = None,
    dry_run: bool = False,
    session_directory: str | Path | None = None,
) -> ActionService:
    """真机上的动作服务。

    :param window_class: 目标窗口的类名（``FindWindowW`` 找）。
    :param guard_policy: 护栏策略
        (:class:`minicode.guard.policy.GuardPolicy`)。``None`` = 无护栏
        （行为与 v0.1.4 一致）。
    :param dry_run: 逐条打印将下发的动作但不执行（护栏 10）。
        L0 级别自动为真（L0 = 只读，见 :mod:`minicode.guard`）。
    :param session_directory: 护栏录制目录（``None`` = ``log_directory`` 旁的
        ``guard/`` 子目录）。
    """
    # ⚠️ **DPI 感知在 `uia/desktop.py::build_desktop` 里声明**（那里是真机接线的
    # 第一行）—— 本层不重复声明，两条入口走的是**同一份**接线。
    from minicode.redact.config import load_model_context_config
    from minicode.redact.redactor import TreeRedactor
    from minicode.uia.desktop import build_desktop
    from minicode.uia.observer import UiaStateObserver

    desktop = build_desktop(window_class=window_class)

    if guard_policy is None:
        # 无护栏路径：与 v0.1.4 逐字节一致（不做任何包装）。
        def factory(sink: ListTraceSink):
            return desktop.new_seam(sink)

        return ActionService(
            seam_factory=factory,
            log_directory=log_directory,
            # **定点观察**（读控件树）：与校验层用的是**同一个端口形状**
            # （`verification.layer.StateObserver`），真机实现是 UIA 那一路。
            observer=UiaStateObserver(desktop.port, desktop.anchor),
            # **脱敏器**：两个开关取自**仓库 `.env`**（缺省都关 = fail-closed：
            # 脱敏没上线，控件树不许进模型上下文）。
            redactor=TreeRedactor(load_model_context_config()),
        )

    # -- 带护栏的真机接线（W2）---------------------------------------------
    from minicode.guard.estop import EstopWatchdog
    from minicode.guard.gate import LevelApprovalGate
    from minicode.guard.idle import IdleWatcher
    from minicode.guard.injector import GuardedInjector
    from minicode.guard.levels import AuthLevel
    from minicode.guard.policy import GuardPolicy
    from minicode.guard.recorder import SessionRecorder
    from minicode.guard.runtime import BudgetLedger, FailureFuse
    from minicode.guard.seam import GuardSession, make_guarded_factory
    from minicode.guard.win32 import (
        async_key_state,
        foreground_window_info,
        last_input_tick,
        now_tick,
    )

    assert isinstance(guard_policy, GuardPolicy)
    session_dir = (
        Path(session_directory)
        if session_directory is not None
        else Path(log_directory) / "guard"
    )
    recorder = SessionRecorder(session_dir)
    ledger = BudgetLedger(budget=guard_policy.budget)
    fuse = FailureFuse(threshold=guard_policy.fuse_threshold)
    estop = (
        EstopWatchdog(
            key_state_fn=async_key_state,
            vkeys=guard_policy.estop_vkeys,
        )
        if guard_policy.estop_enabled
        else None
    )
    idle_watcher = (
        IdleWatcher(tick_fn=last_input_tick, now_tick_fn=now_tick)
        if guard_policy.idle_pause_enabled
        else None
    )
    session = GuardSession(
        policy=guard_policy,
        ledger=ledger,
        fuse=fuse,
        estop=estop,
        idle_watcher=idle_watcher,
        recorder=recorder,
        window_info_fn=foreground_window_info,
        # L0 = 只读：注入器换 dry-run 形态（管线照跑，零注入）。
        dry_run=dry_run or guard_policy.level is AuthLevel.L0,
    )

    if estop is not None:
        estop.start()

    # 注入器包急停感知层（L0 / dry-run 在这一层生效）。
    guarded_injector = GuardedInjector(
        desktop.injector, estop=estop, dry_run=session.dry_run
    )
    desktop = replace(desktop, injector=guarded_injector)

    # 审批闸门包授权级门（不可逆动作按级别处置；真实控件名照常进判定）。
    # ⚠️ L2 强制审批要有**人可问**：给级联门配同一个控制台审批人
    # （非交互环境下它本来就 fail-closed 拒绝，见 approval/console.py）。
    from minicode.approval.console import ConsoleApprover

    level_gate = LevelApprovalGate(
        desktop.approval_gate,
        level=guard_policy.level,
        approver=ConsoleApprover(),
    )

    guarded_seam_factory = make_guarded_factory(
        desktop.new_seam, session=session, level_gate=level_gate
    )

    return _attach_session(
        ActionService(
            seam_factory=guarded_seam_factory,
            log_directory=log_directory,
            observer=UiaStateObserver(desktop.port, desktop.anchor),
            redactor=TreeRedactor(load_model_context_config()),
        ),
        session,
    )


def _attach_session(service: ActionService, session) -> ActionService:
    """把护栏会话挂到服务上（护栏 9：CLI 执行完要打印运行摘要）。"""
    service.guard_session = session  # type: ignore[attr-defined]
    return service

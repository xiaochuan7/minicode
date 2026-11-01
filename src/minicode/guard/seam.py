"""**护栏缝**（W2 核心）：包在动作执行缝外面的一层，十条护栏的拦截点。

设计：**装饰器缝**（同 :class:`minicode.reflection.retry.RetryingSeam` 的先例）——
实现 ``run(request)`` 同款接口，包住内层 :class:`~minicode.action.seam.ActionExecutionSeam`。
缝以下的坐标口径、动作语义、审批判定**一个字不改**；护栏在这里做的是
「这一步**让不让做**」与「做完了**记账**」：

前置拦截（任一命中 → **零动作下发**，错误码可审计）：

* 急停已触发（护栏 1）→ ``emergency_stopped``；
* 预算用尽（护栏 4：步数 / 时长 / 模型调用）→ ``budget_exceeded``；
* 熔断已吹（护栏 5：同一目标连续失败）→ ``fuse_blown``；
* 用户正在动键鼠（护栏 6）→ ``user_active_paused``；
* 前台窗口命中黑名单（护栏 2）→ ``window_blacklisted``（原因进错误信息）；
* L1 白名单不命中（W2 四级授权）→ ``window_whitelist_mismatch``。

不可逆动作（护栏 3）不在这里拦 —— 它挂在**审批闸门**上
（:class:`minicode.guard.gate.LevelApprovalGate`），因为审批门能拿到
**真实控件名与窗口标题**（preview），判定质量比请求层高一截。

后置记账：每步结果记进预算账本与熔断、拒绝记进 ``guard.jsonl``；
一次运行结束由 CLI 写运行摘要（护栏 9）。

L0 / dry-run 不在缝层拦截 —— 它们的正确形态是**换注入器**
（:class:`minicode.guard.injector.GuardedInjector` 的 dry_run 形态）：
管线照跑（截图 / 读树 / 算落点 / 审批预演），只是不注入 ——
这正是「L0 只读」的定义，而不是「L0 报错」。
"""

from __future__ import annotations

from collections.abc import Callable

from minicode.action.errors import ErrorCode
from minicode.action.records import ActionTrace, ListTraceSink, MultiTraceSink, TraceSink
from minicode.action.seam import ActionExecutionSeam, SeamRequest, SeamResult
from minicode.approval.gate import ApprovalGate

from .estop import EstopWatchdog
from .gate import LevelApprovalGate
from .idle import IdleWatcher
from .injector import EstopTriggered
from .levels import AuthLevel
from .policy import GuardPolicy
from .recorder import SessionRecorder
from .runtime import BudgetLedger, FailureFuse
from .win32 import ForegroundWindowInfo

__all__ = ["GuardSession", "GuardedSeam", "make_guarded_factory"]


class GuardSession:
    """一次真机运行的护栏状态（策略 + 账本 + 急停 + 录制，唯一一份）。"""

    def __init__(
        self,
        *,
        policy: GuardPolicy,
        ledger: BudgetLedger,
        fuse: FailureFuse,
        estop: EstopWatchdog | None,
        idle_watcher: IdleWatcher | None,
        recorder: SessionRecorder | None,
        window_info_fn: Callable[[], ForegroundWindowInfo | None] | None,
        dry_run: bool = False,
        own_input_tick_fn: Callable[[], int | None] | None = None,
    ):
        self.policy = policy
        self.ledger = ledger
        self.fuse = fuse
        self.estop = estop
        self.idle_watcher = idle_watcher
        self.recorder = recorder
        self.window_info_fn = window_info_fn
        self.dry_run = dry_run
        #: **自己的输入时刻**查询（护栏注入器的 ``last_injection_tick``）——
        #: ``SendInput`` 也会推进 ``GetLastInputInfo``，空闲让位必须把
        #: 「自己的注入」与「用户动了键鼠」分开（W3 真机实测踩到）。
        self.own_input_tick_fn = own_input_tick_fn
        #: 拒绝记录（护栏给出的每一条，进运行摘要与 guard.jsonl）。
        self.denials: list[dict] = []

    def user_is_active(self) -> bool:
        """用户正在动键鼠吗（**排除自己的注入**之后的口径）。

        * 系统口径没检测到活动 → 不活跃；
        * 检测到活动、但**最后一次输入就是自己的注入**（系统输入时刻
          落在自己的注入时刻附近）→ 归因给自己 → 仍算不活跃；
        * 其余（自己的注入之后用户又动了）→ 活跃 → 让位。
        """
        if self.idle_watcher is None:
            return False
        if not self.idle_watcher.user_recently_active(self.policy.idle_active_ms):
            return False
        own_tick = self.own_input_tick_fn() if self.own_input_tick_fn else None
        if own_tick is None:
            return True
        now_tick = self.idle_watcher.now_tick_fn()
        # 系统的「最后输入」≈ 自己的注入（含 100ms 时钟/调度余量）→ 是自己。
        grace = self.policy.idle_active_ms + 100
        return not (0 <= (now_tick - own_tick) % 0x1_0000_0000 <= grace)

    # ------------------------------------------------------------------
    # 记账
    # ------------------------------------------------------------------

    def record_denial(
        self, code: ErrorCode, message: str, target: str, guard: str
    ) -> None:
        """记一次拒绝（摘要 + guard.jsonl 双写）。"""
        row = {
            "ts_code": code.value,
            "guard": guard,
            "target": target,
            "message": message,
        }
        self.denials.append(row)
        if self.recorder is not None:
            self.recorder.write_guard_event({"type": "denial", **row})

    def record_estop(self) -> None:
        """记急停触发（摘要 + guard.jsonl）。"""
        row = {"triggered": True, "vkeys": list(self.policy.estop_vkeys)}
        if self.recorder is not None:
            self.recorder.write_guard_event({"type": "estop", **row})

    def write_step_result(self, trace: ActionTrace) -> None:
        """每步结果记进预算账本与熔断（模型调用从追踪记录取）。

        ⚠️ dry-run / L0 模式下，落点校验**必然**报「光标没动」——那是
        dry-run 的预期形态，不是失败：熔断按「动作有效」记账
        （``ok or dry_run``），否则只读模式跑两步就把自己熔断了。
        预算步数照记（dry-run 也花了步子）。
        """
        self.ledger.record_step(model_calls=trace.model_calls)
        self.fuse.record(trace.target_description, ok=trace.result.ok or self.dry_run)


def _denied(request: SeamRequest, code: ErrorCode, message: str) -> SeamResult:
    """护栏拒绝的统一出口：**零动作下发**的失败结果。"""
    from minicode.action.protocol import ActionResult

    return SeamResult(
        action=None,
        final_point=None,
        intent=request.action_kind.value,
        result=ActionResult(ok=False, error_code=code, error_message=message),
    )


class _LastTraceTap:
    """每请求一个的轻量出口：抓住本请求**最后一条**追踪记录（记账用）。"""

    def __init__(self) -> None:
        self.last: ActionTrace | None = None

    def emit(self, trace: ActionTrace) -> None:
        self.last = trace


class GuardedSeam:
    """护栏缝：包住内层缝，前置拦截 + 后置记账（接口与内层完全一致）。"""

    def __init__(
        self,
        inner: ActionExecutionSeam,
        *,
        session: GuardSession,
        tap: _LastTraceTap,
    ):
        self._inner = inner
        self.session = session
        self._tap = tap

    # 审批闸门暴露给接口面（decision/api.py 等地方要读 seam.approval_gate）。
    @property
    def approval_gate(self) -> ApprovalGate:
        return self._inner.approval_gate

    @property
    def locator(self):
        return self._inner.locator

    @property
    def anchor(self):
        return self._inner.anchor

    @property
    def executor(self):
        """动作执行器（键盘输入这类不经缝请求的动作种类，真机驱动在
        护栏注入器之上用它 —— 急停照常生效）。"""
        return self._inner.executor

    @property
    def injector(self):
        return self._inner.injector

    def run(self, request: SeamRequest) -> SeamResult:
        session = self.session
        policy = session.policy

        # -- 护栏 1：急停（最高优先级：触发了就什么都不做）------------------
        if session.estop is not None and session.estop.triggered:
            session.record_estop()
            message = (
                f"急停热键已触发（vkeys={list(policy.estop_vkeys)}）：停止一切动作"
            )
            session.record_denial(ErrorCode.EMERGENCY_STOPPED, message,
                                  request.target_description, "estop")
            return _denied(request, ErrorCode.EMERGENCY_STOPPED, message)

        # -- 护栏 4：预算 ---------------------------------------------------
        exceeded = session.ledger.check()
        if exceeded is not None:
            message = f"{exceeded}（护栏 4：budget_exceeded）"
            session.record_denial(ErrorCode.BUDGET_EXCEEDED, message,
                                  request.target_description, "budget")
            return _denied(request, ErrorCode.BUDGET_EXCEEDED, message)

        # -- 护栏 5：熔断 ---------------------------------------------------
        blown = session.fuse.check()
        if blown is not None:
            message = f"{blown}（护栏 5：fuse_blown）"
            session.record_denial(ErrorCode.FUSE_BLOWN, message,
                                  request.target_description, "fuse")
            return _denied(request, ErrorCode.FUSE_BLOWN, message)

        # -- 护栏 6：空闲让位（排除自己的注入之后，用户真的动了键鼠）---------
        if session.idle_watcher is not None and policy.idle_pause_enabled \
                and session.user_is_active():
            message = (
                f"用户最近 {policy.idle_active_ms}ms 内动过键鼠，暂停让位"
                "（护栏 6：user_active_paused）"
            )
            session.record_denial(ErrorCode.USER_ACTIVE_PAUSED, message,
                                  request.target_description, "idle")
            return _denied(request, ErrorCode.USER_ACTIVE_PAUSED, message)

        # -- 护栏 2 + L1 白名单：窗口闸门 ------------------------------------
        # ⚠️ 黑名单是「读到窗口才判」（读不到就没得命中）；
        # 白名单是「**必须**读到且命中」（fail-closed：读不到 = 不在名单内）。
        info = session.window_info_fn() if session.window_info_fn is not None else None
        if info is not None:
            from .blacklist import match_blacklist

            reason = match_blacklist(info.class_name, info.title)
            if reason is not None:
                message = (
                    f"前台窗口命中黑名单（class={info.class_name!r} "
                    f"title={info.title!r}）：{reason}（护栏 2：window_blacklisted）"
                )
                session.record_denial(ErrorCode.WINDOW_BLACKLISTED, message,
                                      request.target_description, "blacklist")
                return _denied(request, ErrorCode.WINDOW_BLACKLISTED, message)
        if policy.level is AuthLevel.L1 and policy.window_whitelist:
            if info is None:
                message = (
                    "读不到前台窗口（window_info 为 None）：L1 白名单无法核实"
                    " → fail-closed 拒绝（window_whitelist_mismatch）"
                )
                session.record_denial(
                    ErrorCode.WINDOW_WHITELIST_MISMATCH, message,
                    request.target_description, "whitelist",
                )
                return _denied(request, ErrorCode.WINDOW_WHITELIST_MISMATCH, message)
            haystack = f"{info.class_name} {info.title}".lower()
            if not any(part.lower() in haystack for part in policy.window_whitelist):
                message = (
                    f"前台窗口不在 L1 白名单内（class={info.class_name!r} "
                    f"title={info.title!r}，白名单={list(policy.window_whitelist)}）"
                    "（window_whitelist_mismatch）"
                )
                session.record_denial(
                    ErrorCode.WINDOW_WHITELIST_MISMATCH, message,
                    request.target_description, "whitelist",
                )
                return _denied(request, ErrorCode.WINDOW_WHITELIST_MISMATCH, message)

        # -- 内层缝（真正的管线；不可逆动作在审批门那层处置）------------------
        try:
            result = self._inner.run(request)
        except EstopTriggered as exc:
            # 注入器在释放已按键后抛出 —— 到这里就是「停下了，零多余输入」。
            session.record_estop()
            session.record_denial(ErrorCode.EMERGENCY_STOPPED, str(exc),
                                  request.target_description, "estop")
            return _denied(request, ErrorCode.EMERGENCY_STOPPED, str(exc))

        # -- 后置记账 --------------------------------------------------------
        if self._tap.last is not None:
            session.write_step_result(self._tap.last)
        return result


def make_guarded_factory(
    inner_factory: Callable[[TraceSink], ActionExecutionSeam],
    *,
    session: GuardSession,
    level_gate: LevelApprovalGate | None = None,
) -> Callable[[ListTraceSink], GuardedSeam]:
    """把缝工厂包成护栏缝工厂（真机接线的唯一入口）。

    追踪出口扇出：调用方的 sink（动作日志用）、录制出口（护栏 7）、
    记账出口（预算/熔断）看的是**同一条**追踪记录
    （:class:`~minicode.action.records.MultiTraceSink`）。

    :param level_gate: 授权级审批门。给出时**替换内层缝的审批闸门**
        （``ActionExecutionSeam.approval_gate`` 是普通属性，直接赋值——
        不复制缝、不改缝内语义）。
    """

    def factory(sink: ListTraceSink) -> GuardedSeam:
        tap = _LastTraceTap()
        sinks: list[TraceSink] = [sink]
        if session.recorder is not None:
            sinks.append(session.recorder)
        sinks.append(tap)
        seam = inner_factory(MultiTraceSink(*sinks))
        if level_gate is not None:
            seam.approval_gate = level_gate
            # ⚠️ **执行器持有的是另一份引用**（seam 构造时传给它的同一个闸门）
            # —— 只换缝上的闸门，执行器那一份还是旧的（W3 真机首跑实测踩到：
            # 剪贴板审批绕过级联门直接弹到了控制台）。两处必须是同一个对象。
            if getattr(seam, "executor", None) is not None:
                seam.executor.approval_gate = level_gate
        return GuardedSeam(seam, session=session, tap=tap)

    return factory

"""**端到端评测的判定口径**（工单 M14）。

这一层只管**怎么判**，不管**怎么跑**：跑的动作由 :mod:`minicode.e2e.real`
接真机，判定口径在这里（假实现也能跑，所以口径本身可被单测钉住）。

四条硬规矩（都来自工单 M14 的验收标准）：

1. **每步都要有结构断言** —— 不许只用「画面变了」当通过判据
   （口径写在 :class:`~minicode.e2e.tasks.Task` 的校验里）。
2. **跳过（环境不可达）不算通过** —— 环境不可达（未登录 / 应用未装 / 网络不通）记
   ``skip``，并写明原因。⚠️ **它进分母**（口径对齐 OSWorld / WAA），
   但**单列**、**不混进自动通过率**。
3. **人工代确认单独统计** —— 标注「人工代确认」的任务**不自动执行**，
   记 ``pending_human``；人代跑完的结果由**人工记录**带进来（``manual``）。
   它**不进分母**（口径规定不自动执行 → agent 从未尝试）。
4. **多步任务有停止条件与交接**（工单 M14 的阻塞边 M12）—— 步数到上限或
   熔断命中就停下来，并把**交接产物**交出去。

判定取值有五个：``pass`` / ``fail`` / ``skip``（环境不可达）/ ``pending_human``
（人工代跑）/ ``handoff``（**合规停手**：撞停止条件 → 交人）。

⚠️ **「合规停手」不许算成自动通过**（公开工作没有「交人=通过」的先例：
UFO² ``ASK_QUESTION=False``；AndroidWorld 声明 FAIL 后可行任务 = 0 分）——
它进分母、记 0 分、单列。

「判不了」（断言给不出结论）**不算通过** —— 它落在 ``fail`` 里，
并在原因里写明是「判不了」而不是「不成立」。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:  # pragma: no cover - 只为类型标注
    from .tasks import Step, Task

__all__ = [
    "VERDICT_PASS",
    "VERDICT_FAIL",
    "VERDICT_SKIP",
    "VERDICT_PENDING_HUMAN",
    "VERDICT_HANDOFF",
    "StepOutcome",
    "ClauseOutcome",
    "TaskOutcome",
    "TaskEvidence",
    "StepExecutor",
    "TaskRunner",
]

#: 通过。
VERDICT_PASS = "pass"
#: 失败（含「判不了」）。
VERDICT_FAIL = "fail"
#: 跳过（环境不可达；**进分母、单列**，但不算通过）。
VERDICT_SKIP = "skip"
#: 待人工确认（「人工代确认」的任务；**不自动执行**、不进分母）。
VERDICT_PENDING_HUMAN = "pending_human"
#: **合规停手**（撞停止条件 → 零动作下发、交人）。
#:
#: ⚠️ 它**进分母、记 0 分**，**不许算成自动通过**（公开工作没有
#: 「交人=通过」的先例：UFO² ``ASK_QUESTION=False``；
#: AndroidWorld 声明 FAIL 后可行任务 = 0 分）。
VERDICT_HANDOFF = "handoff"


@dataclass(frozen=True)
class StepOutcome:
    """一步的执行结果（**外部可观察**的那些字段）。"""

    step_id: str
    description: str = ""
    #: 这一步的动作本身成功了吗。
    ok: bool = False
    #: 这一步走了哪条通道（``uia`` / ``visual`` / ``browser``）。
    #: **空串** = 这一步没有走定位通道（比如放大：它是动作协议里的一个动作，
    #: 不是定位的一个环节 —— 见 ``CONTEXT.md`` 的「放大」）。
    channel: str = ""
    #: **定位阶段**的失败类型（``coverage`` / ``selection`` / …）。
    #: ⚠️ 它与**三类失败**是**两套正交**的轴（README 的口径）：
    #: 这一列说「哪一类定位失败」，三类失败说「哪一段没成」。
    failure_type: str = ""
    #: **三类失败**（定位 / 决策 / 执行）—— 架构决策备忘 §2.5 ③ 要求每步都记。
    #: 值是错误码（没失败就是空串）。
    failures: Mapping[str, str | None] = field(default_factory=dict)
    error_code: str = ""
    error_message: str = ""
    #: 期望断言的结论：``True`` 成立 / ``False`` 不成立 / ``None`` **判不了**。
    expectation_met: bool | None = None
    #: 期望的人读描述。
    expectation: str = ""
    #: 断言的细节（差异、观测）。
    expectation_detail: str = ""
    #: 三段耗时（毫秒）。
    locate_ms: float = 0.0
    decision_ms: float = 0.0
    execute_verify_ms: float = 0.0
    #: 最终落点（物理表面空间）。
    landing_point: tuple[int, int] | None = None
    #: 落点与目标控件中心的偏差（物理像素）。**没有真值时是 ``None``**（判不了）。
    landing_error_px: float | None = None
    #: 真值从哪来（``树里的控件框`` / ``画面量的`` / ``命令行给的`` / ``（没有）``）。
    truth_source: str = ""
    #: 这一步的补充说明（回源重抓、退化、人工代确认…）。
    note: str = ""
    #: 这一步是**按停止条件**结束的（**不是失败**）——
    #: 例如「检测到模态弹窗 → 零动作下发并交人」（工单 M15 的 WX-3）。
    #: 这种一步**没有动作下发**、**期望没被判**（它压根没往下走），
    #: 判定时**不算它失败**。
    stopped: bool = False

    @property
    def expectation_held(self) -> bool:
        """期望**成立**吗（``None`` = 判不了 → 不算成立）。"""
        return self.expectation_met is True

    def to_dict(self) -> dict[str, Any]:
        """落盘形状。"""
        return {
            "step_id": self.step_id,
            "description": self.description,
            "ok": self.ok,
            "channel": self.channel,
            "failure_type": self.failure_type,
            "failures": dict(self.failures),
            "error_code": self.error_code,
            "error_message": self.error_message,
            "expectation": self.expectation,
            "expectation_met": self.expectation_met,
            "expectation_detail": self.expectation_detail,
            "locate_ms": round(self.locate_ms, 1),
            "decision_ms": round(self.decision_ms, 1),
            "execute_verify_ms": round(self.execute_verify_ms, 1),
            "landing_point": list(self.landing_point) if self.landing_point else None,
            "landing_error_px": (
                round(self.landing_error_px, 2) if self.landing_error_px is not None else None
            ),
            "truth_source": self.truth_source,
            "note": self.note,
            "stopped": self.stopped,
        }


@dataclass(frozen=True)
class ClauseOutcome:
    """一条**验收条款**的判定结果（工单 AC 的一条）。"""

    id: str
    description: str
    ok: bool
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "description": self.description,
            "ok": self.ok,
            "detail": self.detail,
        }


@dataclass(frozen=True)
class TaskEvidence:
    """判条款时能看到的**全部证据**。"""

    task: Task
    steps: tuple[StepOutcome, ...] = ()
    #: 任务级的上下文（能力表取值、读到的缩放值、剪贴板是否恢复…）。
    context: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class TaskOutcome:
    """一张工单的判定结果。"""

    task_id: str
    title: str
    verdict: str
    reason: str = ""
    steps: tuple[StepOutcome, ...] = ()
    clauses: tuple[ClauseOutcome, ...] = ()
    #: 这是不是一张「人工代确认」的任务。
    human_confirm: bool = False
    #: 跳过的原因（环境原因）。
    skip_reason: str = ""
    #: **人工代跑**的结果（人代跑之后带进来）：``verdict`` / ``note`` / ``context``。
    #: ⚠️ 它**永远不混进自动通过率**（工单口径：单独统计）。
    manual: Mapping[str, Any] = field(default_factory=dict)
    #: **交接产物**（M12）：多步任务被停止条件打断时交出去的那一份。
    handoff: Mapping[str, Any] | None = None
    #: 撞到的**停止原因**（合规停手那一类；空串 = 没撞停止条件）。
    stop_reason: str = ""
    #: 这张票的口径说明（工单级；报告里带出去）。
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "title": self.title,
            "verdict": self.verdict,
            "reason": self.reason,
            "human_confirm": self.human_confirm,
            "skip_reason": self.skip_reason,
            "manual": dict(self.manual),
            "handoff": dict(self.handoff) if self.handoff is not None else None,
            "stop_reason": self.stop_reason,
            "steps": [step.to_dict() for step in self.steps],
            "clauses": [clause.to_dict() for clause in self.clauses],
            "note": self.note,
        }


@runtime_checkable
class StepExecutor(Protocol):
    """跑一步的端口（**注入点**：真机实现或假实现）。"""

    def execute(self, step: Step) -> StepOutcome:
        """跑一步，返回**外部可观察**的结果。"""
        ...

    def preflight(self) -> str:
        """开工前的环境自检。

        返回**空串** = 环境就绪；返回非空 = 环境不可达，
        内容是**环境原因**（如「未登录」）—— 这一条任务会记 ``skip``。
        """
        ...

    def task_context(self) -> Mapping[str, Any]:
        """任务级上下文（能力表取值、读到的缩放值…），供验收条款判定。"""
        ...

    def stop_reason(self) -> str:
        """停止原因（工单 M14 的阻塞边 M12：多步任务要有停止条件）。

        空串 = 还没到停止条件。
        """
        ...

    def handoff(self) -> Mapping[str, Any] | None:
        """**交接产物**（M12）：停下来的时候交给人的那一份轨迹。"""
        ...


class TaskRunner:
    """按**判定口径**跑一张工单。"""

    def __init__(
        self,
        executor: StepExecutor,
        *,
        manual_results: Mapping[str, Mapping[str, Any]] | None = None,
    ):
        self.executor = executor
        #: **人工代跑**的结果（按工单编号）：``{"verdict": …, "note": …, "context": {…}}``。
        self.manual_results = dict(manual_results or {})

    def run(self, task: Task) -> TaskOutcome:
        """跑一张工单，返回判定结果。"""
        if task.skip_reason:
            return TaskOutcome(
                task_id=task.id,
                title=task.title,
                verdict=VERDICT_SKIP,
                reason=task.skip_reason,
                human_confirm=task.human_confirm,
                skip_reason=task.skip_reason,
            )
        # **人工代确认**：任务级或**步级**标了都不自动执行。
        manual_steps = [step.id for step in task.steps if step.human_confirm]
        if task.human_confirm or manual_steps:
            return self._pending_human(task, manual_steps)
        # -- 前置检查：**异常也要兜住** -------------------------------------
        # ⚠️ 回归守卫（2026-10-22 实测）：浏览器的 ``preflight`` 会在页面
        # 正在导航时抛 ``Execution context was destroyed``（playwright 的
        # 竞态）→ 异常从 ``run()`` 逃出去 → **整轮 wave 崩掉、报告没落盘**。
        # 一张票的前置检查失败 = **那一张票**的环境不可达，**不是**整轮的死。
        try:
            blocked = self.executor.preflight()
        except Exception as exc:  # noqa: BLE001 —— 前置检查的异常一律兜住
            blocked = f"前置检查失败（{type(exc).__name__}: {exc}）"
        if blocked:
            return TaskOutcome(
                task_id=task.id,
                title=task.title,
                verdict=VERDICT_SKIP,
                reason=blocked,
                human_confirm=task.human_confirm,
                skip_reason=blocked,
            )

        steps: list[StepOutcome] = []
        stopped = ""
        for step in task.steps:
            outcome = self.executor.execute(step)
            steps.append(outcome)
            #: **先读停止条件，再判失败** —— 一步可能**按停止条件结束**
            #: （模态弹窗 → 零动作下发并交人，工单 M15 的 WX-3）：
            #: 那种一步的 ``ok`` 是假，**但它的交接产物要交出去**。
            stopped = self.executor.stop_reason()
            if stopped:
                break
            if not outcome.ok:
                break

        context = dict(self.executor.task_context())
        evidence = TaskEvidence(task=task, steps=tuple(steps), context=context)
        clauses = tuple(
            ClauseOutcome(clause.id, clause.description, *clause.check(evidence))
            for clause in task.clauses
        )
        verdict, reason = _verdict(steps, clauses)
        if stopped:
            if verdict == VERDICT_PASS:
                # **合规停手**：撞到停止条件 → 交人 → **不算自动通过**。
                # 这一轮 agent **没有完成任务**（公开口径：交人 = 0 分，
                # 不是通过）→ 进分母、单列。
                verdict = VERDICT_HANDOFF
                reason = (
                    f"停止条件命中（{stopped}）：这一轮**没有完成任务**，"
                    "按口径交人（不算自动通过）"
                )
            elif reason:
                reason = f"停止条件命中（{stopped}）：{reason}"
            else:
                reason = f"停止条件命中（{stopped}）"
        return TaskOutcome(
            task_id=task.id,
            title=task.title,
            verdict=verdict,
            reason=reason,
            steps=tuple(steps),
            clauses=clauses,
            handoff=self.executor.handoff() if stopped else None,
            stop_reason=stopped,
            note=task.note,
        )

    def _pending_human(self, task: Task, manual_steps: Sequence[str]) -> TaskOutcome:
        """**人工代确认**的任务：不自动执行；人代跑完的结果带进来单独统计。"""
        manual = dict(self.manual_results.get(task.id, {}))
        if manual_steps:
            note = (
                "人工代确认：步骤 "
                + "、".join(manual_steps)
                + " 按口径**不自动执行**，等人工代跑后单独统计"
            )
        else:
            note = (
                "人工代确认：这一步要改系统设置，"
                "按口径**不自动执行**，等人工代跑后单独统计"
            )
        context = dict(manual.get("context", {}))
        evidence = TaskEvidence(task=task, steps=(), context=context)
        clauses = tuple(
            ClauseOutcome(clause.id, clause.description, *clause.check(evidence))
            for clause in task.clauses
        )
        if manual:
            note = (
                f"{note}；人工代跑结果：{manual.get('verdict', '（未写）')}"
                f"（{manual.get('note', '（无备注）')}）"
            )
        return TaskOutcome(
            task_id=task.id,
            title=task.title,
            verdict=VERDICT_PENDING_HUMAN,
            reason=note,
            clauses=clauses,
            human_confirm=True,
            manual=manual,
        )


def _verdict(
    steps: Sequence[StepOutcome], clauses: Sequence[ClauseOutcome]
) -> tuple[str, str]:
    """判定：所有步骤都成立**且**所有条款都成立 → 通过。

    ⚠️ **「判不了」不算成立**（``expectation_met is None``）——
    判不了就进不了通过率，也不许被记成通过。
    """
    reasons: list[str] = []
    for step in steps:
        if step.stopped:
            # **按停止条件结束的一步不算失败**（模态弹窗 → 零动作下发、交人，
            # 这正是那张票要的行为；工单 M15 的 WX-3）。
            continue
        if not step.ok:
            reasons.append(
                f"{step.step_id} 动作失败（{step.error_code or '（无错误码）'}）："
                f"{step.error_message or '（无错误信息）'}"
            )
            continue
        if not step.expectation_held:
            if step.expectation_met is None:
                reasons.append(f"{step.step_id} 的期望**判不了**：{step.expectation_detail}")
            else:
                reasons.append(
                    f"{step.step_id} 的期望不成立（{step.expectation}）："
                    f"{step.expectation_detail}"
                )
    for clause in clauses:
        if not clause.ok:
            reasons.append(f"验收条款 {clause.id} 不成立：{clause.detail}")
    if reasons:
        return VERDICT_FAIL, "；".join(reasons)
    return VERDICT_PASS, ""

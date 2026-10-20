"""决策层：模型产出**一段动作程序**，harness 在受控环境里执行并回灌。

工单 M10。三条硬规矩在这里落地：

1. **协议里没有「完成」** —— 模型自称完成不算数，终止只由 harness 侧的停止条件
   承载（**程序请求人工（ask_user）** / **轮次上限**）；
2. **思考预算（effort）显式** —— 规划步与反思步用高档位；**动作步**（消歧指认、
   grounding）的模型请求在**动作路径**里，最低档已钉死（``uia/pointer.py`` 的
   ``DEFAULT_EFFORT = Effort.LOW``）；本模块每次请求都显式给档位，
   省略即报错（模型客户端的既有规矩）；
3. **上下文成本可控** —— 完整截图只留最近 2~4 张，更早的压成结构摘要。

> **分工（工单 M10 AC 7）**：决策层**不在单测缝内**，它的行为（规划质量、
> 循环推进、上下文裁剪）由**端到端评测**承担 —— 本模块**没有单测覆盖**，
> 这是明确承担的分工，不是遗漏。受控执行环境（「能不能 import / 能不能写文件」）
> 的契约在 :mod:`minicode.action.program` 里有单测。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from minicode.action.image import encode_frame_base64
from minicode.action.program import ProgramOutcome, run_program
from minicode.model.effort import Effort
from minicode.model.ports import ModelCaller
from minicode.model.request_log import count_unset_effort, total_input_tokens

from .api import ProtocolApi
from .context import ContextWindow
from .prompt import build_plan_prompt, build_reflect_prompt, extract_program

__all__ = [
    "DECISION_EFFORT",
    "STEP_PLAN",
    "STEP_REFLECT",
    "StepRecord",
    "HarnessReport",
    "DecisionHarness",
]

#: 规划步：产出动作程序。
STEP_PLAN = "plan"
#: 反思步：上一轮没跑成，复盘并给出下一步。
STEP_REFLECT = "reflect"

#: 决策层自己发的模型请求的档位。
#:
#: **规划与反思用高档位**；**动作步用最低档** —— 但动作步的模型请求不在本模块，
#: 它在动作路径里（消歧指认 / grounding），那一侧已经钉死 ``low``
#: （``uia/pointer.py``）。本表只管决策层自己发的那两类。
DECISION_EFFORT: dict[str, Effort] = {
    STEP_PLAN: Effort.HIGH,
    STEP_REFLECT: Effort.HIGH,
}

#: 模型输出里出现这些词**不算**完成（协议里没有「完成」这个动作）。
_COMPLETION_PHRASES = (
    "已完成",
    "已经完成",
    "完成了",
    "任务完成",
    "全部完成",
    "done",
    "finished",
    "complete",
)


def _completion_claims(text: str) -> int:
    """文本里出现了几处「自称完成」的表述（只统计，不据此停止）。"""
    lowered = (text or "").lower()
    return sum(1 for phrase in _COMPLETION_PHRASES if phrase in lowered)


@dataclass(frozen=True)
class StepRecord:
    """一步（一次模型请求 + 它带来的执行结果）的记录。"""

    kind: str
    effort: str
    ok: bool
    input_tokens: int | None = None
    output_tokens: int | None = None
    thinking_tokens: int | None = None
    cache_read_input_tokens: int | None = None
    cache_creation_input_tokens: int | None = None
    program: str = ""
    stdout: str = ""
    stderr: str = ""
    error_code: str | None = None
    error_message: str | None = None
    calls: tuple[dict[str, Any], ...] = ()
    asked_human: bool = False
    completion_claims: int = 0

    @property
    def total_input_tokens(self) -> int | None:
        """上下文总大小（口径见 :func:`minicode.model.request_log.total_input_tokens`）。"""
        return total_input_tokens(
            self.input_tokens,
            self.cache_read_input_tokens,
            self.cache_creation_input_tokens,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "effort": self.effort,
            "ok": self.ok,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "thinking_tokens": self.thinking_tokens,
            "cache_read_input_tokens": self.cache_read_input_tokens,
            "cache_creation_input_tokens": self.cache_creation_input_tokens,
            "total_input_tokens": self.total_input_tokens,
            "program": self.program,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "error_code": self.error_code,
            "error_message": self.error_message,
            "calls": list(self.calls),
            "asked_human": self.asked_human,
            "completion_claims": self.completion_claims,
        }


@dataclass(frozen=True)
class HarnessReport:
    """一次决策层运行的报告。"""

    goal: str
    rounds: int
    steps: tuple[StepRecord, ...]
    stop_reason: str
    #: 每一步的（步序, 上下文总输入 token）—— 成本曲线。
    token_growth: tuple[tuple[int, int | None], ...]
    #: 程序接口的每一次调用（动作与观察）。
    calls: tuple[dict[str, Any], ...] = ()
    #: 档位分布（决策层自己发的请求）。
    effort_histogram: dict[str, int] = field(default_factory=dict)
    #: **未传档位**的调用数（必须恒为 0）。
    unset_effort_calls: int = 0
    #: 上下文窗口里保留 / 淘汰的完整图张数。
    kept_frames: int = 0
    dropped_frames: int = 0
    #: 模型自称「完成」的次数（**一律不据此停止**）。
    completion_claims_ignored: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "goal": self.goal,
            "rounds": self.rounds,
            "steps": [step.to_dict() for step in self.steps],
            "stop_reason": self.stop_reason,
            "token_growth": [list(item) for item in self.token_growth],
            "calls": list(self.calls),
            "effort_histogram": dict(self.effort_histogram),
            "unset_effort_calls": self.unset_effort_calls,
            "kept_frames": self.kept_frames,
            "dropped_frames": self.dropped_frames,
            "completion_claims_ignored": self.completion_claims_ignored,
        }


class DecisionHarness:
    """决策层的 harness：产出程序 → 受控执行 → 回灌 → 继续。"""

    def __init__(
        self,
        *,
        caller: ModelCaller,
        api: ProtocolApi,
        max_rounds: int = 4,
        keep_frames: int = 3,
        max_tokens: int = 4000,
        request_log: Any | None = None,
    ):
        self.caller = caller
        self.api = api
        #: 轮次上限 —— **工程值·未验证**（成本刹车；什么时候该停由 M11/M12 的
        #: 期望断言与卡死检测器接上来定）。
        self.max_rounds = int(max_rounds)
        #: 上下文保留几张完整图 —— **工程值·未验证**（2~4 由工单 M10 AC 5 限定）。
        self.keep_frames = int(keep_frames)
        #: 一次请求的输出预算 —— **工程值·未验证**（服务端思考链与输出共用这份预算，
        #: 太小会出现「HTTP 200 但没有文本输出」）。
        self.max_tokens = int(max_tokens)
        self.request_log = request_log

    # ------------------------------------------------------------------
    # 主循环
    # ------------------------------------------------------------------

    def run(self, goal: str) -> HarnessReport:
        """跑一个多步任务：模型产出程序 → 执行 → 回灌 → 继续。

        **整个过程不需要人工逐步确认** —— 人工只在程序请求人工（ask_user）时才介入。
        """
        context = ContextWindow(keep_frames=self.keep_frames)
        steps: list[StepRecord] = []
        growth: list[tuple[int, int | None]] = []
        all_calls: list[dict[str, Any]] = []
        stop_reason = "max_rounds"

        # **当前观察**：第 1 轮之前先抓一帧 —— 否则模型是**盲写**的程序
        # （模型针对当前观察产出程序，盲写就违背了这条）。
        initial = self.api.capture_frame()
        context.add_round(summary="第 0 轮：初始画面（还没做任何动作）", frame=initial)

        for round_index in range(1, self.max_rounds + 1):
            reply = self.caller.complete(
                build_plan_prompt(goal, context.render()),
                effort=DECISION_EFFORT[STEP_PLAN],
                max_tokens=self.max_tokens,
                images_base64=self._images(context),
            )
            program = extract_program(reply.text)
            outcome = run_program(program, self.api.as_mapping())
            calls = tuple(self.api.calls)
            self.api.calls.clear()
            all_calls.extend(calls)
            # **新观察**：这一轮结束后的画面（回灌给模型的那一帧）。
            frame = self.api.capture_frame()
            summary = _summarize(round_index, outcome, calls)
            context.add_round(summary=summary, frame=frame)

            steps.append(
                StepRecord(
                    kind=STEP_PLAN,
                    effort=str(DECISION_EFFORT[STEP_PLAN]),
                    ok=outcome.ok,
                    input_tokens=reply.input_tokens,
                    output_tokens=reply.output_tokens,
                    thinking_tokens=reply.thinking_tokens,
                    cache_read_input_tokens=reply.cache_read_input_tokens,
                    cache_creation_input_tokens=reply.cache_creation_input_tokens,
                    program=program,
                    stdout=outcome.stdout,
                    stderr=outcome.stderr,
                    error_code=(
                        outcome.error_code.value if outcome.error_code else None
                    ),
                    error_message=outcome.error_message,
                    calls=calls,
                    asked_human=self.api.asked_human,
                    completion_claims=_completion_claims(
                        reply.text + "\n" + outcome.stdout
                    ),
                )
            )
            growth.append((len(steps), steps[-1].total_input_tokens))

            if self.api.asked_human:
                stop_reason = "asked_human"
                break
            if not outcome.ok:
                # 反思步：上一轮没跑成 → 高档位复盘，给出下一步。
                reflect = self.caller.complete(
                    build_reflect_prompt(goal, context.render(), outcome.error_message or ""),
                    effort=DECISION_EFFORT[STEP_REFLECT],
                    max_tokens=self.max_tokens,
                    images_base64=self._images(context),
                )
                context.add_note(f"反思：{reflect.text.strip()[:600]}")
                steps.append(
                    StepRecord(
                        kind=STEP_REFLECT,
                        effort=str(DECISION_EFFORT[STEP_REFLECT]),
                        ok=True,
                        input_tokens=reflect.input_tokens,
                        output_tokens=reflect.output_tokens,
                        thinking_tokens=reflect.thinking_tokens,
                        cache_read_input_tokens=reflect.cache_read_input_tokens,
                        cache_creation_input_tokens=reflect.cache_creation_input_tokens,
                        program="",
                        completion_claims=_completion_claims(reflect.text),
                    )
                )
                growth.append((len(steps), steps[-1].total_input_tokens))

        return HarnessReport(
            goal=goal,
            rounds=sum(1 for step in steps if step.kind == STEP_PLAN),
            steps=tuple(steps),
            stop_reason=stop_reason,
            token_growth=tuple(growth),
            calls=tuple(all_calls),
            effort_histogram=_effort_histogram(steps),
            unset_effort_calls=self._unset_effort_calls(),
            kept_frames=context.kept_frames,
            dropped_frames=context.dropped_frames,
            completion_claims_ignored=sum(step.completion_claims for step in steps),
        )

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------

    def _images(self, context: ContextWindow) -> tuple[str, ...]:
        """上下文里保留的那几张完整图（base64 PNG）。"""
        encoded = [encode_frame_base64(frame) for frame in context.frames()]
        return tuple(data for data in encoded if data)

    def _unset_effort_calls(self) -> int:
        """请求日志里「未传档位」的调用数（必须恒为 0）。"""
        records = getattr(self.request_log, "records", None)
        if not records:
            return 0
        return count_unset_effort(records)


def _summarize(
    round_index: int,
    outcome: ProgramOutcome,
    calls: Sequence[dict[str, Any]],
) -> str:
    """一轮的结构摘要（进上下文；也进验收记录）。"""
    parts = [f"第{round_index}轮"]
    if calls:
        rendered = "；".join(
            f"{call.get('kind')}({call.get('target')})"
            f"→{'成功' if call.get('ok') else '失败:' + str(call.get('error'))}"
            for call in calls
        )
        parts.append(f"动作={rendered}")
    else:
        parts.append("动作=无")
    if outcome.stdout:
        parts.append(f"标准输出={outcome.stdout.strip()[:300]}")
    if outcome.stderr:
        parts.append(f"标准错误={outcome.stderr.strip()[:200]}")
    if not outcome.ok:
        parts.append(f"失败={outcome.error_message}")
    return "｜".join(parts)


def _effort_histogram(steps: Sequence[StepRecord]) -> dict[str, int]:
    histogram: dict[str, int] = {}
    for step in steps:
        histogram[step.effort] = histogram.get(step.effort, 0) + 1
    return histogram

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
>
> 📌 **工单 M12 补一句**：**停止条件**（四类卡死检测器 / 熔断 / 轮次上限）
> 落成了**独立的状态机**（:mod:`minicode.reflection.stop`）——
> 它**没有模型依赖**、不碰桌面，因此**有单测**（工单 M12 各条验收标准都要求
> 可复现的用例）。分工不变的部分是「**规划质量**仍由端到端评测承担」。
"""

from __future__ import annotations

import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from minicode.action.errors import FailureType, failure_type_of
from minicode.action.image import encode_frame_base64
from minicode.action.ports import CapturedFrame
from minicode.action.program import ProgramOutcome, run_program
from minicode.model.effort import Effort
from minicode.model.image import downscale_frame, model_input_size_for
from minicode.model.ports import ModelCaller
from minicode.model.request_log import count_unset_effort, total_input_tokens
from minicode.reflection.handoff import HandoffArtifact, build_handoff
from minicode.reflection.stop import (
    STOP_ASKED_HUMAN,
    STOP_DANGEROUS_ACTION,
    STOP_EXPECTATION_MET,
    STOP_REASON_TEXT,
    STOP_ROUND_CAP,
    StopPolicy,
)
from minicode.verification.telemetry import TelemetryAccumulator

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

#: **观察型调用**（工单 D-1）：本轮程序**自己要新画面**。
#:
#: 程序调了它们 → 这一轮结束时**必须**有一帧新的画面给模型看
#: （它点名要新画面，却不给它看最新的那一帧，说不过去）。
_OBSERVING_CALL_KINDS = frozenset({"observe", "screenshot"})

#: **定位/点击**调用（工单 D-1 的**兜底纪律**）。
#:
#: 本轮程序做过它们 → **强制抓一帧**：模型忘了调 ``observe()`` 就成了盲写，
#: 不让「省 token」换来「看不见」。
_LOCATING_CALL_KINDS = frozenset({"click", "find"})

#: **动界面**的调用（会改画面，因此手里那一帧可能过时）。
_ACTION_CALL_KINDS = frozenset(
    {"click", "find", "type_text", "press_key", "wait"}
)


def _completion_claims(text: str) -> int:
    """文本里出现了几处「自称完成」的表述（只统计，不据此停止）。"""
    lowered = (text or "").lower()
    return sum(1 for phrase in _COMPLETION_PHRASES if phrase in lowered)


def _decision_failure(outcome: ProgramOutcome) -> str | None:
    """这一步的失败**算不算决策失败**（工单 M11 AC 5 的三类之一）。

    判据只有一份：错误码 → 失败类型（``minicode.action.errors``），
    **决策失败**（``decision``）的那两个码才算 ——
    其余（动作执行失败等）由动作自己的追踪记录记进「执行失败」，
    这里**不重复计数**。
    """
    code = outcome.error_code
    if code is None:
        return None
    if failure_type_of(code) is FailureType.DECISION:
        return code.value
    return None


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
    #: **这一步的图像 token 数**（工单 D-7：图像占的输入 token 单列一列）。
    #:
    #: 它按 28 px patch 规则对**实际发出的那张图**估计（**估计值，不是服务端
    #: 报出来的数**）。拿不到就是 ``None``（**不猜**）。
    image_tokens: int | None = None
    program: str = ""
    stdout: str = ""
    stderr: str = ""
    error_code: str | None = None
    error_message: str | None = None
    calls: tuple[dict[str, Any], ...] = ()
    asked_human: bool = False
    completion_claims: int = 0
    #: **决策段耗时**（毫秒）—— 这一步的模型调用花了多久
    #: （三段耗时里的中间那一段，工单 M11 AC 4）。
    decision_ms: float = 0.0

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
            "image_tokens": self.image_tokens,
            "program": self.program,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "error_code": self.error_code,
            "error_message": self.error_message,
            "calls": list(self.calls),
            "asked_human": self.asked_human,
            "completion_claims": self.completion_claims,
            "decision_ms": self.decision_ms,
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
    #: 每一步的（步序, **图像 token 数**）—— 工单 D-7：
    #: 图像占的那部分输入 token **单列一列**，这样「按需观察省了多少」
    #: 能被**直接读出来**，而不是靠感觉。
    #:
    #: ⚠️ 图像 token 是**估计值**（28 px patch 规则，【二手】），拿不到是 ``None``。
    image_token_growth: tuple[tuple[int, int | None], ...] = ()
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
    #: **换策略**的记录（工单 M12 AC 1：「能读到『已换策略』的记录」）。
    strategy_changes: tuple[dict[str, Any], ...] = ()
    #: 四类**卡死检测器**的判定（工单 M12 AC 5）。
    detectors: tuple[dict[str, Any], ...] = ()
    #: **交接产物**（交给人时才有；任务达成时是 ``None``）。
    handoff: HandoffArtifact | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "goal": self.goal,
            "rounds": self.rounds,
            "steps": [step.to_dict() for step in self.steps],
            "stop_reason": self.stop_reason,
            "token_growth": [list(item) for item in self.token_growth],
            "image_token_growth": [list(item) for item in self.image_token_growth],
            "calls": list(self.calls),
            "effort_histogram": dict(self.effort_histogram),
            "unset_effort_calls": self.unset_effort_calls,
            "kept_frames": self.kept_frames,
            "dropped_frames": self.dropped_frames,
            "completion_claims_ignored": self.completion_claims_ignored,
            "strategy_changes": list(self.strategy_changes),
            "detectors": list(self.detectors),
            "handoff": self.handoff.to_dict() if self.handoff is not None else None,
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
        telemetry: TelemetryAccumulator | None = None,
        stop_policy: StopPolicy | None = None,
    ):
        self.caller = caller
        self.api = api
        #: 轮次上限 —— **工程值·未验证**（成本刹车）。
        #: 达到它就**停下来交人**（工单 M12 AC 2：停止原因写明是轮次上限）。
        self.max_rounds = int(max_rounds)
        #: 上下文保留几张完整图 —— **工程值·未验证**（2~4 由工单 M10 AC 5 限定）。
        self.keep_frames = int(keep_frames)
        #: 一次请求的输出预算 —— **工程值·未验证**（服务端思考链与输出共用这份预算，
        #: 太小会出现「HTTP 200 但没有文本输出」）。
        self.max_tokens = int(max_tokens)
        self.request_log = request_log
        #: **三段耗时与三类失败**的累加器（工单 M11 AC 4 / AC 5）。
        #: 给了它，整任务的三段占比与三类失败分布就有了。
        self.telemetry = telemetry
        #: **停止条件**（工单 M12）：四类卡死检测器 + 熔断 + 轮次上限。
        #: 缺省 = 不做停止判定（那时只有轮次上限兜着）。
        self.stop_policy = stop_policy
        #: **声明给模型的尺寸**（**算一次**，全任务共用一份；见
        #: :meth:`_declared_size`）。``None`` = 还没量到表面尺寸。
        self._model_input_size: tuple[int, int] | None = None

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
        image_growth: list[tuple[int, int | None]] = []
        all_calls: list[dict[str, Any]] = []
        #: 默认的停止原因是**轮次上限**（下面每一处 break 都会改写它）。
        stop_reason = STOP_ROUND_CAP
        #: 手里这一帧是不是**可能过时**（动过界面但没抓到新帧）。
        frame_may_be_stale = False

        # **当前观察**：第 1 轮之前先抓一帧 —— 否则模型是**盲写**的程序
        # （模型针对当前观察产出程序，盲写就违背了这条）。
        initial = self.api.capture_frame()
        context.add_round(summary="第 0 轮：初始画面（还没做任何动作）", frame=initial)

        for round_index in range(1, self.max_rounds + 1):
            plan_started = time.perf_counter()
            reply = self.caller.complete(
                build_plan_prompt(goal, context.render()),
                effort=DECISION_EFFORT[STEP_PLAN],
                max_tokens=self.max_tokens,
                images_base64=self._images(context),
            )
            decision_ms = (time.perf_counter() - plan_started) * 1000.0
            # 决策段的耗时：报给累加器（整任务的决策段合计），
            # 并**记在这一轮的第一个动作上**（动作日志里的 decision_ms）。
            self._record_decision(decision_ms)
            program = extract_program(reply.text)
            outcome = run_program(program, self.api.as_mapping())
            calls = tuple(self.api.calls)
            self.api.calls.clear()
            all_calls.extend(calls)
            # -- **条件抓帧**（工单 D-1）------------------------------------
            # 原来每轮结束都无条件抓一帧 → 每轮固定多一张完整图进上下文。
            # 现在**三条判据，缺一不抓**：
            #   1. **本轮程序调过 observe()/screenshot()** → 它自己要新画面；
            #   2. **手里这一帧可能过时**（上一轮动过界面，而那一轮末尾
            #      没抓到新帧）→ 抓（否则模型拿的是动作**之前**的画面）；
            #   3. **兜底纪律**：本轮程序做过任何**定位/点击**动作 → 强制抓一帧。
            #      模型忘了调 observe() 就成了盲写 —— **不让「省 token」换来
            #      「看不见」**。
            # 抓到新帧时上下文里的旧帧照常按张数上限淘汰；没抓到时
            # ``ContextWindow.add_round(frame=None)`` **沿用旧帧**（不淘汰）。
            kinds = {str(call.get("kind")) for call in calls}
            round_observed = bool(kinds & _OBSERVING_CALL_KINDS)
            round_located = bool(kinds & _LOCATING_CALL_KINDS)
            round_acted = bool(kinds & _ACTION_CALL_KINDS)
            need_frame = round_observed or frame_may_be_stale or round_located
            frame = self.api.capture_frame() if need_frame else None
            #: 动了界面却没抓到新帧 → 手里这帧从此**可能过时**。
            frame_may_be_stale = round_acted and not need_frame
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
                    image_tokens=reply.image_tokens,
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
                    decision_ms=decision_ms,
                )
            )
            growth.append((len(steps), steps[-1].total_input_tokens))
            image_growth.append((len(steps), steps[-1].image_tokens))

            if self.api.asked_human:
                stop_reason = STOP_ASKED_HUMAN
                break
            if self.api.halted:
                # **危险动作被拦下**（工单 M12 AC 8 / AC 9）：停下来交给人。
                stop_reason = STOP_DANGEROUS_ACTION
                break
            # -- 停止条件（工单 M12）----------------------------------------
            # 这一轮结束：交给**卡死检测器**判一次。
            # * **熔断**（同一动作连续无效）→ 换策略，并把「已换策略」写进上下文，
            #   模型下一轮据此换招；
            # * **轮次硬上限 / 期望成立 / 提权** → 停下来（后面交人或收工）。
            if self.stop_policy is not None:
                #: **自称完成**报给停止条件（工单 D-4「跳过验证」）。
                #:
                #: ⚠️ 自称完成**本身从不停止**（协议里没有「完成」这个动作，
                #: 它只计数）—— 判死走的是「自称完成 **且** 这一轮没有/不成立的
                #: 校验」这个**组合**。
                self.stop_policy.observe_claim(
                    claimed=steps[-1].completion_claims > 0
                )
                self.stop_policy.observe_round()
                for change in self.stop_policy.take_strategy_changes():
                    context.add_note(change.describe())
                for note in self.stop_policy.take_skip_verification_notes():
                    #: 「跳过验证」的记录进上下文（模型与人都读得到）。
                    context.add_note(note)
                reason = self.stop_policy.stop_reason
                if reason:
                    stop_reason = reason
                    break
            if not outcome.ok:
                # 反思步：上一轮没跑成 → 高档位复盘，给出下一步。
                reflect_started = time.perf_counter()
                reflect = self.caller.complete(
                    build_reflect_prompt(goal, context.render(), outcome.error_message or ""),
                    effort=DECISION_EFFORT[STEP_REFLECT],
                    max_tokens=self.max_tokens,
                    images_base64=self._images(context),
                )
                reflect_ms = (time.perf_counter() - reflect_started) * 1000.0
                self._record_decision(reflect_ms, failure=_decision_failure(outcome))
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
                        image_tokens=reflect.image_tokens,
                        program="",
                        completion_claims=_completion_claims(reflect.text),
                        decision_ms=reflect_ms,
                    )
                )
                growth.append((len(steps), steps[-1].total_input_tokens))
                image_growth.append((len(steps), steps[-1].image_tokens))

        handoff = self._build_handoff(stop_reason, all_calls)
        policy = self.stop_policy
        return HarnessReport(
            goal=goal,
            rounds=sum(1 for step in steps if step.kind == STEP_PLAN),
            steps=tuple(steps),
            stop_reason=stop_reason,
            token_growth=tuple(growth),
            image_token_growth=tuple(image_growth),
            calls=tuple(all_calls),
            effort_histogram=_effort_histogram(steps),
            unset_effort_calls=self._unset_effort_calls(),
            kept_frames=context.kept_frames,
            dropped_frames=context.dropped_frames,
            completion_claims_ignored=sum(step.completion_claims for step in steps),
            strategy_changes=(
                tuple(change.to_dict() for change in policy.strategy_change_records)
                if policy is not None
                else ()
            ),
            detectors=(
                tuple(verdict.to_dict() for verdict in policy.detectors)
                if policy is not None
                else ()
            ),
            handoff=handoff,
        )

    def _build_handoff(
        self, stop_reason: str, api_calls: Sequence[dict[str, Any]]
    ) -> HandoffArtifact | None:
        """**交接产物**（工单 M12 AC 7）。

        只有「停下来交给人」的停止原因才交出去 —— **任务达成不交**
        （那是收工，不是交人）。
        """
        if self.stop_policy is None:
            return None
        if stop_reason == STOP_EXPECTATION_MET:
            return None
        return build_handoff(
            self.stop_policy.signals,
            reason=stop_reason,
            reason_text=STOP_REASON_TEXT.get(stop_reason, ""),
            strategy_changes=self.stop_policy.strategy_change_records,
            api_calls=api_calls,
        )

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------

    def _images(self, context: ContextWindow) -> tuple[str, ...]:
        """上下文里保留的那几张完整图（base64 PNG）。

        ⚠️ **送模型的那一份是缩放过的副本**（缩到**声明尺寸**）——
        留在上下文里的帧**保持原始物理分辨率**（校验层的 OCR 与差分要全分辨率，
        缩了就判不准）。
        """
        encoded: list[str] = []
        for frame in context.frames():
            data = encode_frame_base64(self._model_frame(frame))
            if data:
                encoded.append(data)
        return tuple(encoded)

    def _model_frame(self, frame: CapturedFrame) -> CapturedFrame:
        """送模型的那一帧：缩到**声明尺寸**。

        缩不了（尺寸对不上 / 不是缩小）→ **送原图**（如实，不假装缩过）。
        """
        width, height = self._declared_size(frame)
        try:
            return downscale_frame(frame, width, height)
        except ValueError:
            return frame

    def _declared_size(self, frame: CapturedFrame) -> tuple[int, int]:
        """**声明给模型的那一帧的尺寸**（**算一次**，全任务共用一份）。

        来源只有一处：**量出来的表面尺寸**（抓到的那一帧的物理像素尺寸）——
        **不写死常数**（架构备忘 C-05）。缩放规则统一走
        :func:`minicode.model.image.model_input_size_for`（服务端单侧上限 2048；
        本机桌面 2560×1600 → **1280×800**）。

        ⚠️ 决策层的模型**不产坐标**（程序只按描述点名目标），
        所以这里的声明尺寸**不参与任何坐标换算** —— 坐标换算的真值
        仍然只在动作路径那一侧。
        """
        if self._model_input_size is None:
            self._model_input_size = model_input_size_for(frame.width, frame.height)
        return self._model_input_size

    def _unset_effort_calls(self) -> int:
        """请求日志里「未传档位」的调用数（必须恒为 0）。"""
        records = getattr(self.request_log, "records", None)
        if not records:
            return 0
        return count_unset_effort(records)

    def _record_decision(
        self, decision_ms: float, failure: str | None = None
    ) -> None:
        """记一次决策：累加器收**决策段耗时**；接口面收**这一轮的第一个动作**。

        决策耗时是**轮级**的量：它只记在轮内第一个动作上，
        整任务的三段合计因此**不重复计数**。
        """
        if self.telemetry is not None:
            self.telemetry.add_decision(decision_ms, failure)
        self.api.begin_decision(decision_ms)


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

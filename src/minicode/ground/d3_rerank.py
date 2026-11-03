"""**d3 候选重排 + 置信度闸门**（V4）：候选分数接近时才问 d3，置信度不够就不点。

**为什么是「锦标赛」**：d3 是**决策模型**（不生成文本，只要概率）——
拿它给候选编号打分。但每次调用都有成本，而且多数时候候选**并不接近**
（启发式已经分出高下）→ **只在分数接近时才调用**：

* ``top1 与 top2 的启发式得分差 ≥ :attr:`D3RerankGate.score_gap_threshold```
  → **直接选 top1**（不问 d3 —— 省调用、可审计：少一次黑盒）；
* 差 < 阈值（并列 / 接近）→ 问 d3（选项 = 候选编号，criteria = 候选描述）：
  - d3 ``confidence ≥ :attr:`D3RerankGate.min_confidence``` → 选 d3 的 argmax；
  - confidence 不够 → **不点，转人工**（fail-closed），记 ``low_confidence``；
  - d3 失败（429 / 500 / 响应形状错）→ **降级 = 不点**（**不是猜**），
    记 ``d3_unavailable``。

**0.0px 契约不可破**：本模块只决定「**选哪个候选**」，永远不输出坐标 ——
落点由调用方取候选框的**物理中心**（零换算）。

**启发式得分**：缺省实现是「候选文字与目标描述的匹配度」
（:func:`minicode.ground.candidates.candidate_match_score`：匹配 = 1.0，
不匹配 = 0.0）—— 与 UIA/OCR 源的排序规则同一把尺子；调用方可以注入自己的。
"""

from __future__ import annotations

import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from minicode.ground.candidates import GroundingCandidate, candidate_match_score
from minicode.model.d3 import D3Client, D3Error

__all__ = [
    "DEFAULT_MIN_CONFIDENCE",
    "DEFAULT_MIN_YES_PROBABILITY",
    "DEFAULT_PER_CANDIDATE_K",
    "DEFAULT_SCORE_GAP_THRESHOLD",
    "D3CandidateReranker",
    "D3PerCandidateReranker",
    "D3RerankGate",
    "RerankAction",
    "RerankOutcome",
]

#: **分数差阈值**（缺省）：top1−top2 ≥ 它 → 不问 d3。
#:
#: 得分是 0/1 的匹配度（匹配=1.0 / 不匹配=0.0）→ 差要么 0（并列）要么 1
#: （有唯一的匹配项）。0.1 的意思是：**只要有并列的最高分就问 d3**；
#: 唯一匹配 → 直接选。阈值做成配置项（任务书要求），调大 = 更少调用。
DEFAULT_SCORE_GAP_THRESHOLD = 0.1

#: **最低置信度**（缺省）：d3 的 confidence 低于它 → 不点，转人工。
DEFAULT_MIN_CONFIDENCE = 0.5

#: **逐候选 yes/no 的默认 K**（启发式排序前 K 个候选逐个问；K 之外不调用）。
DEFAULT_PER_CANDIDATE_K = 3

#: **逐候选 yes/no 的默认判定阈值**：最高 yes 概率低于它 → 不点（fail-closed）。
DEFAULT_MIN_YES_PROBABILITY = 0.6

#: **429 的退避纪律**（任务书）：60s 固定退避、单独计数、**不许计入模型失败**。
PER_CANDIDATE_RATE_LIMIT_BACKOFF_S = 60.0
PER_CANDIDATE_RATE_LIMIT_MAX_ATTEMPTS = 3

#: d3 的选项数上限（网关约束：选项 ≤ 26 —— 编号 1~26）。
MAX_D3_OPTIONS = 26


class RerankAction:
    """重排结果的动作类型（追踪记录里的 ``rerank_action``）。"""

    #: 分数拉开了 → 直接选启发式 top1（没问 d3）。
    DIRECT = "direct"
    #: 问了 d3，置信度够 → 选 d3 的 argmax。
    RERANKED = "reranked"
    #: 问了 d3，置信度不够 → **不点**（fail-closed）。
    LOW_CONFIDENCE = "low_confidence"
    #: d3 调用失败 → **不点**（降级不是猜）。
    D3_UNAVAILABLE = "d3_unavailable"
    #: d3 不可用 → **跳过重排，用启发式 top1**（W3：不许因 d3 挂了整条任务失败）。
    RERANK_UNAVAILABLE = "rerank_unavailable"
    #: 候选不足两个 → 没什么可重排的（直接选唯一/零个）。
    NOT_APPLICABLE = "not_applicable"


@dataclass(frozen=True)
class D3RerankGate:
    """重排的闸门参数（**配置项**；缺省值见模块文档）。"""

    #: top1−top2 的得分差 ≥ 它 → 不问 d3（直接选）。
    score_gap_threshold: float = DEFAULT_SCORE_GAP_THRESHOLD
    #: d3 confidence 低于它 → 不点（fail-closed）。
    min_confidence: float = DEFAULT_MIN_CONFIDENCE


@dataclass(frozen=True)
class RerankOutcome:
    """一次重排的结果（调用方照着它行动；全部字段可落 trace）。"""

    action: str
    #: 选中的候选编号（``None`` = 不点）。
    chosen_number: int | None
    #: 启发式选中的编号（``direct`` / ``not_applicable`` 时有值）。
    heuristic_number: int | None = None
    #: d3 的 confidence（问了才有）。
    confidence: float | None = None
    #: d3 的各选项概率（问了才有）。
    probabilities: dict[str, float] = field(default_factory=dict)
    #: 这次**有没有真的调用 d3**（成本口径）。
    called_d3: bool = False
    #: 失败原因（``low_confidence`` / ``d3_unavailable`` 时的说明）。
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


class D3CandidateReranker:
    """候选重排器：分数接近才问 d3；置信度不够不点（fail-closed）。

    参数
    ----
    client
        :class:`minicode.model.d3.D3Client`（已有调用日志 sink）。
    gate
        :class:`D3RerankGate`（两个阈值）。
    scorer
        **可选**打分函数 ``(description, candidate) → float``。
        缺省 = 文字匹配度（0.0 / 1.0）。
    """

    def __init__(
        self,
        client: D3Client,
        *,
        gate: D3RerankGate | None = None,
        scorer=None,
    ):
        self.client = client
        self.gate = gate or D3RerankGate()
        self._scorer = scorer or _match_scorer
        #: **累计统计**（V6 的口径直接读它）：d3 实际调用数、低置信拒绝数、
        #: 各动作的次数 —— 「d3 到底被调用了多少次、拒绝了多少次」要有
        #: 一个直读的答案，不靠翻日志数。
        self.stats: dict[str, int] = {
            "rerank_calls": 0,
            "d3_called": 0,
            "direct": 0,
            "reranked": 0,
            "low_confidence": 0,
            "d3_unavailable": 0,
            "not_applicable": 0,
        }

    def rerank(
        self,
        description: str,
        candidates: Sequence[GroundingCandidate],
    ) -> RerankOutcome:
        """重排一次：返回 :class:`RerankOutcome`（**永远不返回坐标**）。

        ⚠️ 候选超过 :data:`MAX_D3_OPTIONS` 时**不问 d3**（网关约束）——
        直接选启发式 top1（如实记 ``direct``，不静默截断候选清单）。
        """
        self.stats["rerank_calls"] += 1
        if len(candidates) < 2:
            top = candidates[0].number if candidates else None
            self.stats["not_applicable"] += 1
            return RerankOutcome(
                action=RerankAction.NOT_APPLICABLE, chosen_number=top,
                heuristic_number=top,
            )
        scored = sorted(
            (
                (float(self._scorer(description, candidate)), candidate)
                for candidate in candidates
            ),
            key=lambda item: (-item[0], item[1].number),
        )
        top_score, top = scored[0]
        second_score, _second = scored[1]
        gap = top_score - second_score
        if gap >= self.gate.score_gap_threshold or len(candidates) > MAX_D3_OPTIONS:
            # 分数拉开了（或选项太多）→ **直接选**，不问 d3。
            self.stats["direct"] += 1
            return RerankOutcome(
                action=RerankAction.DIRECT,
                chosen_number=top.number,
                heuristic_number=top.number,
                reason=f"score_gap={gap:.2f}",
            )

        # —— 分数接近 → 找 d3 ——
        criteria = {
            str(candidate.number): (candidate.text or None) for candidate in candidates
        }
        state = _state_text(description, candidates)
        try:
            reply = self.client.decide(state, "rerank", criteria)
        except D3Error as exc:
            kind = "限流（429）" if exc.rate_limited else "调用失败"
            self.stats["d3_called"] += 1
            self.stats["d3_unavailable"] += 1
            return RerankOutcome(
                action=RerankAction.D3_UNAVAILABLE,
                chosen_number=None,
                heuristic_number=top.number,
                called_d3=True,
                reason=f"d3 {kind}：{exc}",
            )
        self.stats["d3_called"] += 1
        winner = _argmax_option(reply.probabilities, candidates)
        if reply.confidence < self.gate.min_confidence:
            self.stats["low_confidence"] += 1
            return RerankOutcome(
                action=RerankAction.LOW_CONFIDENCE,
                chosen_number=None,
                heuristic_number=top.number,
                confidence=reply.confidence,
                probabilities=reply.probabilities,
                called_d3=True,
                reason=(
                    f"d3 confidence {reply.confidence:.3f} < 阈值 "
                    f"{self.gate.min_confidence}"
                ),
            )
        if winner is None:
            # d3 给的选项不在候选清单里（响应形状级别的意外）→ 降级不点。
            self.stats["d3_unavailable"] += 1
            return RerankOutcome(
                action=RerankAction.D3_UNAVAILABLE,
                chosen_number=None,
                heuristic_number=top.number,
                confidence=reply.confidence,
                probabilities=reply.probabilities,
                called_d3=True,
                reason=f"d3 的 argmax 不在候选清单里（{reply.chosen!r}）",
            )
        self.stats["reranked"] += 1
        return RerankOutcome(
            action=RerankAction.RERANKED,
            chosen_number=winner.number,
            heuristic_number=top.number,
            confidence=reply.confidence,
            probabilities=reply.probabilities,
            called_d3=True,
        )


def _match_scorer(description: str, candidate: GroundingCandidate) -> float:
    """缺省打分：候选文字与描述的匹配度（0.0 / 1.0）。"""
    return candidate_match_score(description, candidate.text)


class D3PerCandidateReranker:
    """**逐候选 yes/no 重排**（W3）：对启发式前 K 个候选各问一道 noul 题。

    **为什么改**（V4/V6 的实证）：16~20 个选项一次问（choice 题）实测
    3 超时 + 3 网关 400 + 2 低置信（n=8）→ 多选项模式已证伪。逐候选模式把
    一道大题拆成 K 道**二值小题**（``noul``），每次调用小而快、可逐条审计。

    **决策规则**（fail-closed）：

    * 取 **yes 概率最高**的候选；最高者 < :attr:`min_yes_probability` →
      **不点，转人工**（记 ``low_confidence``）—— d3 永远不输出坐标，
      落点仍由调用方取候选框物理中心；
    * 任一候选的 d3 调用不可用（超时 / 429 重试耗尽 / 400 / 网关错）→
      **跳过重排，用启发式 top1**（记 ``rerank_unavailable``）——
      **不许因为 d3 挂了就整条任务失败**；
    * 429 按 :data:`PER_CANDIDATE_RATE_LIMIT_BACKOFF_S` 固定退避重试，
      **单独计数，不计入模型失败**。

    **日志**：``D3Client`` 每次调用落一条 :class:`D3CallRecord`；本类再把
    每条「候选判定」落一份带候选序号的记录（``call_log`` sink 可选）。
    """

    def __init__(
        self,
        client: D3Client,
        *,
        k: int = DEFAULT_PER_CANDIDATE_K,
        min_yes_probability: float = DEFAULT_MIN_YES_PROBABILITY,
        scorer=None,
        call_log=None,
        sleep=time.sleep,
    ):
        self.client = client
        self.k = max(1, int(k))
        self.min_yes_probability = float(min_yes_probability)
        self._scorer = scorer or _match_scorer
        self._call_log = call_log
        self._sleep = sleep
        #: **累计统计**：与 :class:`D3CandidateReranker` 同名键 + 逐候选的计数。
        self.stats: dict[str, int] = {
            "rerank_calls": 0,
            "d3_called": 0,
            "direct": 0,
            "reranked": 0,
            "low_confidence": 0,
            "rerank_unavailable": 0,
            "not_applicable": 0,
            "rate_limited": 0,
        }

    def rerank(
        self,
        description: str,
        candidates: Sequence[GroundingCandidate],
    ) -> RerankOutcome:
        """重排一次（**永远不返回坐标**；接口与 :class:`D3CandidateReranker` 一致）。"""
        self.stats["rerank_calls"] += 1
        if len(candidates) < 2:
            top = candidates[0].number if candidates else None
            self.stats["not_applicable"] += 1
            return RerankOutcome(
                action=RerankAction.NOT_APPLICABLE, chosen_number=top,
                heuristic_number=top,
            )
        scored = sorted(
            (
                (float(self._scorer(description, candidate)), candidate)
                for candidate in candidates
            ),
            key=lambda item: (-item[0], item[1].number),
        )
        top_score, top = scored[0]
        shortlist = [candidate for _score, candidate in scored[: self.k]]

        yes_probabilities: dict[str, float] = {}
        for candidate in shortlist:
            outcome = self._ask_candidate(description, candidate)
            if outcome is None:
                # 任一候选不可用 → 整次重排不可用 → 启发式 top1（W3.6 的降级）。
                self.stats["d3_called"] += len(yes_probabilities)
                self.stats["rerank_unavailable"] += 1
                return RerankOutcome(
                    action=RerankAction.RERANK_UNAVAILABLE,
                    chosen_number=top.number,
                    heuristic_number=top.number,
                    probabilities=dict(yes_probabilities),
                    called_d3=True,
                    reason=f"d3 不可用：候选 {candidate.number} 判定失败（用启发式 top1 继续）",
                )
            yes_probabilities[str(candidate.number)] = outcome
        self.stats["d3_called"] += len(shortlist)

        best_number = max(
            yes_probabilities.items(), key=lambda item: (item[1], -int(item[0]))
        )[0]
        best_probability = yes_probabilities[best_number]
        if best_probability < self.min_yes_probability:
            self.stats["low_confidence"] += 1
            return RerankOutcome(
                action=RerankAction.LOW_CONFIDENCE,
                chosen_number=None,
                heuristic_number=top.number,
                confidence=best_probability,
                probabilities=dict(yes_probabilities),
                called_d3=True,
                reason=(
                    f"最高 yes 概率 {best_probability:.3f} < 阈值 "
                    f"{self.min_yes_probability}（不点，转人工）"
                ),
            )
        self.stats["reranked"] += 1
        return RerankOutcome(
            action=RerankAction.RERANKED,
            chosen_number=int(best_number),
            heuristic_number=top.number,
            confidence=best_probability,
            probabilities=dict(yes_probabilities),
            called_d3=True,
        )

    def _ask_candidate(
        self,
        description: str,
        candidate: GroundingCandidate,
    ) -> float | None:
        """问一个候选「是不是要点的目标」，返回 yes 概率（不可用 → ``None``）。

        429 → 60s 固定退避重试（单独计数）；其它失败**不重试**（V4/V6 实测
        网关 400 重试无用）→ 直接 ``None``。
        """
        left, top, right, bottom = candidate.box
        state = (
            f"任务：{description}\n"
            f"候选：{candidate.text or '（无文字）'}"
            f"（位置 left={left}, top={top}, right={right}, bottom={bottom}）\n"
            "这个候选是否是上述任务要点击的目标？"
        )
        for attempt in range(1, PER_CANDIDATE_RATE_LIMIT_MAX_ATTEMPTS + 1):
            try:
                reply = self.client.decide_noul(state)
            except D3Error as exc:
                self._log_call(
                    candidate, None, exc, attempt, rate_limited=exc.rate_limited
                )
                if exc.rate_limited:
                    self.stats["rate_limited"] += 1
                    if attempt < PER_CANDIDATE_RATE_LIMIT_MAX_ATTEMPTS:
                        self._sleep(PER_CANDIDATE_RATE_LIMIT_BACKOFF_S)
                        continue
                return None
            probability = reply.probabilities.get("yes")
            if probability is None:
                self._log_call(candidate, None, D3Error("noul 响应缺 yes 概率"), attempt)
                return None
            self._log_call(
                candidate,
                (probability, reply.confidence, reply.latency_ms, reply.http_status),
                None,
                attempt,
            )
            return float(probability)
        return None

    def _log_call(
        self,
        candidate: GroundingCandidate,
        success: tuple[float, float, float, int] | None,
        error: Exception | None,
        attempt: int,
        *,
        rate_limited: bool = False,
    ) -> None:
        """逐候选判定日志（候选序号 / yes 概率 / 耗时 / 状态 / 429 / 错误）。"""
        if self._call_log is None:
            return
        record: dict[str, Any] = {
            "candidate_number": candidate.number,
            "candidate_text": candidate.text or "",
            "attempt": attempt,
            "rate_limited": rate_limited,
            "error": "" if error is None else str(error)[:300],
        }
        if success is not None:
            probability, confidence, latency_ms, status = success
            record.update(
                {
                    "yes_probability": probability,
                    "confidence": confidence,
                    "latency_ms": round(latency_ms, 1),
                    "http_status": status,
                }
            )
        else:
            record.update({"yes_probability": None, "confidence": None,
                           "latency_ms": None, "http_status": None})
        self._call_log(record)


def _state_text(
    description: str, candidates: Sequence[GroundingCandidate]
) -> str:
    """d3 的 state：目标描述 + 候选清单（编号、描述、位置）。"""
    lines = [f"目标描述：{description}", "候选清单："]
    for candidate in candidates:
        left, top, right, bottom = candidate.box
        lines.append(
            f"{candidate.number}. {candidate.text or '（无文字）'}"
            f"（位置 left={left}, top={top}, right={right}, bottom={bottom}）"
        )
    return "\n".join(lines)


def _argmax_option(
    probabilities: dict[str, float], candidates: Sequence[GroundingCandidate]
) -> GroundingCandidate | None:
    """**全量概率**上取 argmax；最大项**不在候选清单里** → ``None``（降级）。

    ⚠️ **不**在清单内的选项上重新归一 —— d3 的 argmax 指着一个不存在的
    选项 = 这次回答不可信，fail-closed（调用方降级为不点），不是
    「挑清单里概率最大的凑合」。
    """
    if not probabilities:
        return None
    best_option = max(
        probabilities.items(), key=lambda item: (item[1], -int(item[0]) if item[0].isdigit() else 0)
    )[0]
    by_number = {str(candidate.number): candidate for candidate in candidates}
    return by_number.get(best_option)

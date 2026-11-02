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

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from minicode.ground.candidates import GroundingCandidate, candidate_match_score
from minicode.model.d3 import D3Client, D3Error

__all__ = [
    "DEFAULT_MIN_CONFIDENCE",
    "DEFAULT_SCORE_GAP_THRESHOLD",
    "D3CandidateReranker",
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

    def rerank(
        self,
        description: str,
        candidates: Sequence[GroundingCandidate],
    ) -> RerankOutcome:
        """重排一次：返回 :class:`RerankOutcome`（**永远不返回坐标**）。

        ⚠️ 候选超过 :data:`MAX_D3_OPTIONS` 时**不问 d3**（网关约束）——
        直接选启发式 top1（如实记 ``direct``，不静默截断候选清单）。
        """
        if len(candidates) < 2:
            top = candidates[0].number if candidates else None
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
            return RerankOutcome(
                action=RerankAction.D3_UNAVAILABLE,
                chosen_number=None,
                heuristic_number=top.number,
                called_d3=True,
                reason=f"d3 {kind}：{exc}",
            )
        winner = _argmax_option(reply.probabilities, candidates)
        if reply.confidence < self.gate.min_confidence:
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
            return RerankOutcome(
                action=RerankAction.D3_UNAVAILABLE,
                chosen_number=None,
                heuristic_number=top.number,
                confidence=reply.confidence,
                probabilities=reply.probabilities,
                called_d3=True,
                reason=f"d3 的 argmax 不在候选清单里（{reply.chosen!r}）",
            )
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

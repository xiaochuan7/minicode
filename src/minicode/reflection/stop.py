"""**停止条件**、**卡死检测器**与**熔断**（工单 M12）。

词条：

* **停止条件** —— 由 harness 强制、而非由模型自称的任务终止判据。
* **卡死检测器** —— 判定任务已陷入无效循环的四类判据：
  **帧无变化、重复动作、进度停滞、轮次硬上限**。
* **熔断** —— 在重复无效动作达到阈值时**强制打断当前策略**。

**四类检测器各管一件事**（信号不同，因此不会互相顶替）：

============  ==========  ==================================================
检测器        粒度        判据
============  ==========  ==================================================
帧无变化      步          这一步做完，界面**没变**（M11 的默认校验结论）
重复动作      步          **同一动作哈希**连续出现（动作种类 + 目标描述）
进度停滞      轮          连续若干轮**既没期望成立、也没界面变化**
轮次硬上限    轮          轮数达到上限
============  ==========  ==================================================

**检测器 ≠ 停止条件**：检测器命中先**换策略**（熔断），
只有**轮次硬上限**、**期望成立**、**程序请求人工**、**提权交人**
这几种才终止任务并把轨迹交出去。

⚠️ **模型自称完成不算数**：协议里没有「完成」这个动作，
终止只由这里的停止条件承载（工单 M12 AC 2）。

**用状态机而不是脚本**：本模块是纯状态机（:class:`StopPolicy`）——
喂进一步的信号，它给出判定；不控制流程、不调模型、不碰桌面。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any

from minicode.action.errors import ErrorCode
from minicode.action.protocol import ActionKind

__all__ = [
    "DETECTOR_FRAME_UNCHANGED",
    "DETECTOR_REPEATED_ACTION",
    "DETECTOR_PROGRESS_STALLED",
    "DETECTOR_ROUND_CAP",
    "DETECTORS",
    "STOP_ROUND_CAP",
    "STOP_EXPECTATION_MET",
    "STOP_ASKED_HUMAN",
    "STOP_ELEVATED",
    "STOP_STRATEGY_EXHAUSTED",
    "STOP_DANGEROUS_ACTION",
    "STOP_MODAL_DIALOG",
    "STOP_CLAIMED_UNVERIFIED",
    "STOP_REASONS",
    "STOP_REASON_TEXT",
    "STRATEGY_CROP_SIZE",
    "STRATEGY_LOCATE_LEVEL",
    "STRATEGY_KEYBOARD",
    "STRATEGY_ASK_HUMAN",
    "STRATEGY_LADDER",
    "STRATEGY_LABELS",
    "OBSERVATION_KINDS",
    "StepSignal",
    "DetectorVerdict",
    "StrategyChange",
    "action_hash",
    "signal_from_trace",
    "StopPolicy",
]

# ---------------------------------------------------------------------------
# 检测器与停止原因
# ---------------------------------------------------------------------------

#: 帧无变化（步级）。
DETECTOR_FRAME_UNCHANGED = "frame_unchanged"
#: 重复动作（步级）。
DETECTOR_REPEATED_ACTION = "repeated_action"
#: 进度停滞（轮级）。
DETECTOR_PROGRESS_STALLED = "progress_stalled"
#: 轮次硬上限（轮级）。
DETECTOR_ROUND_CAP = "round_cap"

#: 四类卡死检测器（顺序固定：报告里按这个顺序读）。
DETECTORS = (
    DETECTOR_FRAME_UNCHANGED,
    DETECTOR_REPEATED_ACTION,
    DETECTOR_PROGRESS_STALLED,
    DETECTOR_ROUND_CAP,
)

#: 停止原因：**轮次硬上限**（工单 M12 AC 2）。
#:
#: ⚠️ 取值与检测器 id ``round_cap`` **刻意不同** —— 一个是「检测器」，
#: 一个是「停止原因」；同名会让报告里两件事混在一起
#: （熔断与停止条件不许混）。
STOP_ROUND_CAP = "round_cap_reached"
#: 停止原因：**目标级期望成立**（任务达成）。
STOP_EXPECTATION_MET = "expectation_met"
#: 停止原因：**程序请求人工**（``ask_user``）。
STOP_ASKED_HUMAN = "asked_human"
#: 停止原因：**检测到提权 / 安全桌面**（交人，C-07）。
STOP_ELEVATED = "elevated"
#: 停止原因：**换策略的阶梯用尽**（交人）。
STOP_STRATEGY_EXHAUSTED = "strategy_exhausted"
#: 停止原因：**危险动作被拦下**（要人工确认；工单 M12 AC 8 / AC 9）。
STOP_DANGEROUS_ACTION = "dangerous_action"
#: 停止原因：**检测到模态弹窗**（零动作下发、交人；工单 M15 的 WX-3）。
STOP_MODAL_DIALOG = "modal_dialog"
#: 停止原因：**自称完成，但没有校验记录 / 界面没变**（工单 D-4「跳过验证」）。
#:
#: ⚠️ **判据是组合，不是「自称完成」本身** —— 协议里没有「完成」这个动作，
#: 「模型说做完了」照旧**只计数、从不据此停止**。
#: 判死的是「**自称完成** **且** 这一轮**没有校验记录**（没做过动作 /
#: 没读到校验读数）**或**校验读数说**界面没变**」——
#: 它的「完成」没有任何证据支撑，停下来交给人。
STOP_CLAIMED_UNVERIFIED = "claimed_unverified"

#: 全部停止原因（报告里能读到的取值集合）。
STOP_REASONS = (
    STOP_ROUND_CAP,
    STOP_EXPECTATION_MET,
    STOP_ASKED_HUMAN,
    STOP_ELEVATED,
    STOP_STRATEGY_EXHAUSTED,
    STOP_DANGEROUS_ACTION,
    STOP_MODAL_DIALOG,
    STOP_CLAIMED_UNVERIFIED,
)

#: 停止原因的人读说法（交接产物与报告里用）。
STOP_REASON_TEXT = {
    STOP_ROUND_CAP: "达到轮次上限，停下来交给人",
    STOP_EXPECTATION_MET: "目标级期望成立，任务达成",
    STOP_ASKED_HUMAN: "动作程序请求人工，停下来交给人",
    STOP_ELEVATED: "检测到提权 / 安全桌面，零动作下发并交给人",
    STOP_STRATEGY_EXHAUSTED: "换策略的阶梯用尽（还是没进展），交给人",
    STOP_DANGEROUS_ACTION: "遇到危险动作（发消息 / 支付 / 删除这类），交给人确认",
    STOP_MODAL_DIALOG: "检测到模态弹窗，零动作下发并交给人",
    STOP_CLAIMED_UNVERIFIED: "模型自称完成，但这一轮没有校验记录或界面没变（跳过验证），交给人",
}

#: 「跳过验证」的上下文记录（工单 D-4）。
#:
#: 两条都进上下文（模型与人都读得到）：
#:
#: * **没有校验记录** —— 这一轮没做过动作 / 没读到校验读数；
#: * **界面没变** —— 校验读数说了 ``changed=False``。
SKIP_VERIFICATION_NOTE = "本轮自称完成，但没有校验记录（跳过验证）"
SKIP_VERIFICATION_NO_CHANGE_NOTE = "本轮自称完成，但校验读数说界面没变（跳过验证）"

# ---------------------------------------------------------------------------
# 换策略的阶梯
# ---------------------------------------------------------------------------

#: 换裁剪尺寸。
STRATEGY_CROP_SIZE = "crop_size"
#: 换定位级别。
STRATEGY_LOCATE_LEVEL = "locate_level"
#: 改键盘。
STRATEGY_KEYBOARD = "keyboard"
#: 请求人工（阶梯的最后一步 —— 交人）。
STRATEGY_ASK_HUMAN = "ask_human"

#: **换策略的阶梯**（工单 M12 AC 1 点名的四种：换裁剪尺寸 / 换定位级别 /
#: 改键盘 / 请求人工）。走到最后一项 = 交人。
STRATEGY_LADDER = (
    STRATEGY_CROP_SIZE,
    STRATEGY_LOCATE_LEVEL,
    STRATEGY_KEYBOARD,
    STRATEGY_ASK_HUMAN,
)

#: 阶梯每一项的人读说法。
STRATEGY_LABELS = {
    STRATEGY_CROP_SIZE: "换裁剪尺寸",
    STRATEGY_LOCATE_LEVEL: "换定位级别",
    STRATEGY_KEYBOARD: "改键盘",
    STRATEGY_ASK_HUMAN: "请求人工",
}

#: **观察型动作种类**（不改变界面，因此不参与卡死判定）。
OBSERVATION_KINDS = frozenset(
    {
        ActionKind.SCREENSHOT.value,
        ActionKind.WAIT.value,
        ActionKind.ZOOM.value,
        ActionKind.INSPECT.value,
    }
)


# ---------------------------------------------------------------------------
# 一步的信号
# ---------------------------------------------------------------------------


def action_hash(kind: str, target_description: str) -> str:
    """**同一动作**的哈希。

    哈希里**只有动作种类与目标描述**，**没有坐标** ——
    重新感知之后坐标会变，而「同一个动作」指的是「同一件事」
    （点同一个目标），不是「同一个像素」。
    """
    raw = f"{kind}|{str(target_description).strip()}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True)
class StepSignal:
    """一步的信号（从追踪记录里提炼出来的）。

    * ``frame_changed`` —— 这一步之后界面变了吗？``None`` = **判不了**
      （没接校验层，或校验层判不了）；
    * ``expectation_met`` —— **目标级期望**成立了吗？``None`` = 没有期望 /
      判不了。
    """

    action_kind: str
    target_description: str
    action_hash: str
    frame_changed: bool | None = None
    expectation_met: bool | None = None
    ok: bool = True
    landing_point: tuple[int, int] | None = None
    window_title: str = ""
    intent: str = ""
    error_code: str | None = None


def signal_from_trace(trace: Any) -> StepSignal:
    """把一条**追踪记录**变成一步的信号。

    帧变化与期望结论都取自 M11 的校验结果（**只有那一份判据**）：
    有 ``change`` 就看它（``changed`` / ``no_change``），
    没有就看顶层结论（``expectation_met`` / ``expectation_not_met``）。
    """
    action = getattr(trace, "action", None)
    kind = action.kind.value if action is not None else ""
    target = getattr(trace, "target_description", "") or ""
    verification = getattr(trace, "verification_result", None) or {}
    change = verification.get("change") or {}
    change_outcome = change.get("outcome")
    outcome = verification.get("outcome")
    frame_changed: bool | None
    if change_outcome:
        frame_changed = change_outcome == "changed"
    elif outcome in ("changed", "no_change"):
        frame_changed = outcome == "changed"
    else:
        frame_changed = None
    expectation_met: bool | None
    if outcome == "expectation_met":
        expectation_met = True
    elif outcome == "expectation_not_met":
        expectation_met = False
    else:
        expectation_met = None
    result = getattr(trace, "result", None)
    error_code = None
    if result is not None and getattr(result, "error_code", None) is not None:
        error_code = result.error_code.value
    selected = getattr(trace, "selected", None)
    landing = getattr(trace, "landing_point", None)
    return StepSignal(
        action_kind=kind,
        target_description=target,
        action_hash=action_hash(kind, target),
        frame_changed=frame_changed,
        expectation_met=expectation_met,
        ok=bool(getattr(result, "ok", True)) if result is not None else True,
        landing_point=tuple(landing) if landing else None,
        window_title=(getattr(selected, "window_title", "") or "") if selected else "",
        intent=getattr(trace, "intent", "") or "",
        error_code=error_code,
    )


@dataclass(frozen=True)
class DetectorVerdict:
    """一个检测器的判定（**读得出**它有没有命中、命中几次、为什么）。"""

    detector: str
    triggered: bool
    count: int
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "detector": self.detector,
            "triggered": self.triggered,
            "count": self.count,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class StrategyChange:
    """一次**换策略**的记录（工单 M12 AC 1：「能读到『已换策略』的记录」）。"""

    #: 第几次换策略（1 起）。
    index: int
    #: 换成了哪一种策略（:data:`STRATEGY_LADDER` 里的一项）。
    strategy: str
    #: 人读的理由（为什么换）。
    reason: str
    #: 发生在第几轮。
    round: int = 0

    def describe(self) -> str:
        """人读的一句话（进上下文与交接产物）。"""
        label = STRATEGY_LABELS.get(self.strategy, self.strategy)
        return f"已换策略（第{self.index}次）：{label} —— {self.reason}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "strategy": self.strategy,
            "label": STRATEGY_LABELS.get(self.strategy, self.strategy),
            "reason": self.reason,
            "round": self.round,
            "description": self.describe(),
        }


# ---------------------------------------------------------------------------
# 停止条件（状态机）
# ---------------------------------------------------------------------------


class StopPolicy:
    """**停止条件 + 卡死检测器 + 熔断**（状态机）。

    它同时是**追踪记录的旁路出口**（:class:`~minicode.action.records.TraceSink`）：
    每来一条追踪记录就提炼成一步的信号（这样它与动作日志看的是**同一份数据**）。

    阈值都是**运行时参数**，取值来源写在字段旁边（仓库规矩：
    每个阈值都要能说清「这个数是哪来的」）。
    """

    def __init__(
        self,
        *,
        max_rounds: int = 4,
        repeated_action_threshold: int = 3,
        frame_unchanged_threshold: int = 2,
        stalled_rounds_threshold: int = 3,
        strategy_ladder: tuple[str, ...] = STRATEGY_LADDER,
    ):
        #: 轮次硬上限（**工程阈值·未验证**；工单 M12 只说「达到上限就停」）。
        self.max_rounds = int(max_rounds)
        #: 重复动作熔断的阈值（工单 M12 AC 1：**连续 ≥3 次**）。
        self.repeated_action_threshold = int(repeated_action_threshold)
        #: 帧无变化的判定阈值（**工程阈值·未验证**：连续几步没变算「无变化」）。
        self.frame_unchanged_threshold = int(frame_unchanged_threshold)
        #: 进度停滞的阈值（**工程阈值·未验证**：连续几轮没进展算「停滞」）。
        self.stalled_rounds_threshold = int(stalled_rounds_threshold)
        #: 换策略的阶梯（走到最后一项 = 交人）。
        self.strategy_ladder = tuple(strategy_ladder)

        #: 走过的每一步的信号。
        self.signals: list[StepSignal] = []
        #: 换策略的记录。
        self.strategy_changes: list[StrategyChange] = []
        #: 这一轮**有没有校验记录**（工单 D-4「跳过验证」）：
        #: 有一步的校验读数（``frame_changed`` 判得出来）就是有。
        self._round_verified = False
        #: 这一轮**最后一条**校验读数说界面变没变（``None`` = 没有校验记录）。
        self._round_changed: bool | None = None
        #: 这一轮**自称完成**吗（只记，不据此停止）。
        self._round_claimed = False
        #: 「跳过验证」的上下文记录（工单 D-4）。
        self._skip_verification_notes: list[str] = []
        self._last_hash: str = ""
        self._repeat_count: int = 0
        self._unchanged_count: int = 0
        self._rounds: int = 0
        self._stalled_rounds: int = 0
        self._round_progress: bool = False
        self._stop_reason: str = ""
        self._strategy_index: int = 0
        self._fresh_strategy_changes: list[StrategyChange] = []
        #: 各检测器**命中过的最长连续次数**（熔断之后计数会重新开始，
        #: 但「命中过」这件事不会消失 —— 报告里读的是这个数）。
        self._repeat_peak: int = 0
        self._unchanged_peak: int = 0
        self._stalled_peak: int = 0

    # -- 旁路出口 ---------------------------------------------------------

    def emit(self, trace: Any) -> None:
        """吃进一条追踪记录（:class:`~minicode.action.records.TraceSink`）。"""
        signal = signal_from_trace(trace)
        self.signals.append(signal)
        # **这一轮的校验事实**（工单 D-4「跳过验证」）：
        # ``frame_changed`` 判得出来 ⇔ 这一步**有校验读数**（``change`` 的结论）。
        # ⚠️ 观察型动作（截图 / 等待）没有校验记录 —— 不算。
        if signal.frame_changed is not None:
            self._round_verified = True
            self._round_changed = signal.frame_changed
        if signal.action_kind in OBSERVATION_KINDS:
            # 观察型动作不改界面 → 不参与卡死判定。
            return
        # 重复动作：同一动作哈希连续出现。
        if signal.action_hash == self._last_hash and self._last_hash:
            self._repeat_count += 1
        else:
            self._last_hash = signal.action_hash
            self._repeat_count = 1
        self._repeat_peak = max(self._repeat_peak, self._repeat_count)
        # 帧无变化：这一步做完界面没变。
        if signal.frame_changed is False:
            self._unchanged_count += 1
        else:
            self._unchanged_count = 0
        self._unchanged_peak = max(self._unchanged_peak, self._unchanged_count)
        # 进展：期望成立，或界面变了。
        if signal.expectation_met is True or signal.frame_changed is True:
            self._round_progress = True
        # 目标级期望成立 → 任务达成（不是「模型自称完成」）。
        if signal.expectation_met is True and not self._stop_reason:
            self._stop_reason = STOP_EXPECTATION_MET
        # 提权 / 安全桌面 → 零动作下发，交人（C-07）。
        if (
            signal.error_code == ErrorCode.UAC_OR_SECURE_DESKTOP.value
            and not self._stop_reason
        ):
            self._stop_reason = STOP_ELEVATED
        # 熔断：同一动作哈希连续 ≥N 次**且**帧无变化 → 强制换策略。
        if (
            self._repeat_count >= self.repeated_action_threshold
            and self._unchanged_count >= 1
        ):
            self._break_circuit(
                f"同一动作连续 {self._repeat_count} 次且界面无变化"
            )

    # -- 轮次边界 ---------------------------------------------------------

    def observe_claim(self, *, claimed: bool) -> None:
        """一轮结束：**自称完成**这件事报给停止条件（工单 D-4）。

        ⚠️ **自称完成本身从不停止** —— 协议里没有「完成」这个动作，
        它照旧**只计数**（见
        :func:`minicode.decision.harness._completion_claims`）。
        判死走的是「**自称完成 + 没有/不成立的校验**」这个**组合**
        （见 :meth:`observe_round`）。
        """
        self._round_claimed = bool(claimed)

    def observe_round(self) -> None:
        """一轮结束（**轮级**检测器在这里判）。"""
        self._rounds += 1
        if self._round_progress:
            self._stalled_rounds = 0
        else:
            self._stalled_rounds += 1
        self._stalled_peak = max(self._stalled_peak, self._stalled_rounds)
        self._round_progress = False
        if (
            self._stalled_rounds >= self.stalled_rounds_threshold
            and not self._stop_reason
        ):
            self._break_circuit(f"连续 {self._stalled_rounds} 轮没有任何进展")
        # -- 「跳过验证」（工单 D-4）---------------------------------------
        # 某一轮**自称完成**，而这一轮**没有校验记录**（没做过动作 /
        # 没读到校验读数）**或**校验读数说**界面没变** → 判死：
        # 它的「完成」没有任何证据支撑，停下来交给人。
        # ⚠️ 判据是**组合**：「自称完成」本身照旧只计数、从不据此停止。
        if self._round_claimed and not self._stop_reason:
            if not self._round_verified:
                self._skip_verification_notes.append(SKIP_VERIFICATION_NOTE)
                self._stop_reason = STOP_CLAIMED_UNVERIFIED
            elif self._round_changed is False:
                self._skip_verification_notes.append(
                    SKIP_VERIFICATION_NO_CHANGE_NOTE
                )
                self._stop_reason = STOP_CLAIMED_UNVERIFIED
        if self._rounds >= self.max_rounds and not self._stop_reason:
            self._stop_reason = STOP_ROUND_CAP
        # 下一轮从零开始（校验事实与自称完成都是**轮级**的量）。
        self._round_verified = False
        self._round_changed = None
        self._round_claimed = False

    # -- 读出 -------------------------------------------------------------

    @property
    def stop_reason(self) -> str:
        """停止原因（空串 = 还没到停止条件）。"""
        return self._stop_reason

    @property
    def rounds(self) -> int:
        return self._rounds

    @property
    def strategy_change_records(self) -> tuple[StrategyChange, ...]:
        """换策略的记录（全部）。"""
        return tuple(self.strategy_changes)

    def take_strategy_changes(self) -> tuple[StrategyChange, ...]:
        """取走**这一轮新增**的换策略记录（并清空待取队列）。"""
        fresh = tuple(self._fresh_strategy_changes)
        self._fresh_strategy_changes.clear()
        return fresh

    def take_skip_verification_notes(self) -> tuple[str, ...]:
        """取走「跳过验证」的上下文记录（工单 D-4）—— 调用方把它**加进上下文**。

        这是**给人与模型看的记录**（「本轮自称完成，但没有校验记录（跳过验证）」），
        与「判死」是两件事：记录说明**为什么**，判死说明**停不停**。
        """
        notes = tuple(self._skip_verification_notes)
        self._skip_verification_notes.clear()
        return notes

    @property
    def detectors(self) -> tuple[DetectorVerdict, ...]:
        """四类检测器的判定（报告里读得到）。

        ``count`` 是**命中过的最长连续次数** —— 熔断之后计数会重新开始，
        但「命中过」这件事不会消失（读的是事实，不是瞬时值）。
        """
        return (
            DetectorVerdict(
                detector=DETECTOR_FRAME_UNCHANGED,
                triggered=self._unchanged_peak >= self.frame_unchanged_threshold,
                count=self._unchanged_peak,
                reason=(
                    f"最长连续 {self._unchanged_peak} 步界面没变"
                    f"（阈值 {self.frame_unchanged_threshold}）"
                ),
            ),
            DetectorVerdict(
                detector=DETECTOR_REPEATED_ACTION,
                triggered=(
                    self._repeat_peak >= self.repeated_action_threshold
                ),
                count=self._repeat_peak,
                reason=(
                    f"同一动作最长连续 {self._repeat_peak} 次"
                    f"（阈值 {self.repeated_action_threshold}）"
                ),
            ),
            DetectorVerdict(
                detector=DETECTOR_PROGRESS_STALLED,
                triggered=(
                    self._stalled_peak >= self.stalled_rounds_threshold
                ),
                count=self._stalled_peak,
                reason=(
                    f"最长连续 {self._stalled_peak} 轮没有进展"
                    f"（阈值 {self.stalled_rounds_threshold}）"
                ),
            ),
            DetectorVerdict(
                detector=DETECTOR_ROUND_CAP,
                triggered=self._rounds >= self.max_rounds,
                count=self._rounds,
                reason=f"已跑 {self._rounds} 轮（上限 {self.max_rounds}）",
            ),
        )

    # -- 内部 -------------------------------------------------------------

    def _break_circuit(self, reason: str) -> None:
        """**熔断**：强制换策略（阶梯用尽 → 交人）。"""
        if self._strategy_index >= len(self.strategy_ladder):
            if not self._stop_reason:
                self._stop_reason = STOP_STRATEGY_EXHAUSTED
            return
        strategy = self.strategy_ladder[self._strategy_index]
        self._strategy_index += 1
        change = StrategyChange(
            index=len(self.strategy_changes) + 1,
            strategy=strategy,
            reason=reason,
            round=self._rounds,
        )
        self.strategy_changes.append(change)
        self._fresh_strategy_changes.append(change)
        # 换策略之后重新计数（新策略要有重新开始的机会）。
        self._repeat_count = 0
        self._unchanged_count = 0
        self._stalled_rounds = 0
        if strategy == STRATEGY_ASK_HUMAN and not self._stop_reason:
            self._stop_reason = STOP_ASKED_HUMAN

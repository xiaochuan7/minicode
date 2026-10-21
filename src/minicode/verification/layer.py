"""**校验层本体**（工单 M11）：动作之后，系统自己确认界面确实按预期变了。

两种模式：

1. **有期望** → **期望断言**：按期望的形态，用**能判它的手段**给判定，
   判据取**并**（任一手段说「成立」即成立）。
2. **没有期望** → **默认校验**：动作前后抓帧做差分（见 :mod:`minicode.verification.diff`）。

三种手段（按可靠性排序，架构决策备忘 §2 的口径）：

======================  ==========================================  ======================
手段                    能判什么                                    判不了什么
======================  ==========================================  ======================
可访问性树              状态 / 存在性                               文本的具体内容
文本比对                文本出现或消失                              状态
像素差分                **界面有没有变**（通用条件）                **具体期望**
======================  ==========================================  ======================

「文本比对」这一路有**两个来源**（工单 M27）：**树里的控件名** 与
**画面 OCR**。树覆盖不全时（Windows 设置的缩放百分比、界面上的时钟读数、
微信聊天区的标题 —— 它们**根本不在树里**），就从画面上读。

⚠️ **OCR 不是新的一种验证手段**：手段还是三个，断言形态还是三种
（状态变化 / 文本出现 / 文本消失）。OCR 只是「文本比对」多出来的一个来源，
而且**只读画面**（不产生动作、不注入输入）。

三条硬规矩：

* **像素变化 ≠ 成功** —— 像素差分**不判具体期望**（点错打开错对话框也会安定）；
  没有期望时它只给出「有反应 / 界面无变化」这个**信号**。
* **模型不参与裁判** —— 本层**没有任何模型依赖**（``model_calls`` 恒为 0）。
* **判不了 ≠ 不成立** —— 树读不出来（0 节点 = 通道不可用，C-09）时一律「判不了」；
  **OCR 不可用 / 读不出也一样**（工单 M27 AC 5）。

期望的语义是**变化**（工单：「某个按钮变成不可用」「某段文本出现」）：
断言的是「动作之后 X 现在是这个状态」，**并且**这次动作**促成了**它
（动作之前它不是这个状态）。因此校验要**两段**：动作之前取一次基线观测，
动作之后再观测一次，两次对照。
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from minicode.action.ports import (
    CapturedFrame,
    ControlCandidate,
    ScreenshotPort,
)
from minicode.action.protocol import Action
from minicode.uia.elements import name_matches

from .diff import (
    OUTCOME_CHANGED,
    OUTCOME_NO_CHANGE,
    ChangeDetectParams,
    ChangeResult,
    poll_change,
)
from .expect import Expectation, ExpectationKind, Verdict
from .ocr import (
    TRUTH_SOURCE_NONE,
    TRUTH_SOURCE_SCREEN,
    TRUTH_SOURCE_TREE,
    OcrPort,
    OcrReading,
    OcrStatus,
    normalize_ocr_text,
)

__all__ = [
    "StateObserver",
    "VerificationBaseline",
    "VerifyRequest",
    "VerificationResult",
    "Verifier",
    "VerificationLayer",
    "OUTCOME_EXPECTATION_MET",
    "OUTCOME_EXPECTATION_NOT_MET",
    "OUTCOME_EXPECTATION_UNKNOWN",
    "OUTCOME_CHANGED",
    "OUTCOME_NO_CHANGE",
]

#: 期望成立。
OUTCOME_EXPECTATION_MET = "expectation_met"
#: 期望**不成立**（断言失败）。
OUTCOME_EXPECTATION_NOT_MET = "expectation_not_met"
#: 判不了（没有能判它的手段 / 树读不出来）。
OUTCOME_EXPECTATION_UNKNOWN = "expectation_unknown"


@runtime_checkable
class StateObserver(Protocol):
    """**定点**观察一个控件的状态。

    ⚠️ **只做定点查询**（带属性条件），**禁止全树 dump**
    （架构决策备忘 §2.6 C-09：全桌面遍历约 1000 个元素要 ≥8 秒）。
    """

    def observe(self, name: str) -> tuple[ControlCandidate, ...]:
        """按名字查控件；查不到返回空元组。读树失败就**抛异常**。"""
        ...


@dataclass(frozen=True)
class VerificationBaseline:
    """**动作之前**的那次定点观测（基线）。

    期望判的是**变化**，所以要有「之前」这一份：
    * ``candidates`` —— 之前观测到的控件；
    * ``reachable`` —— 树读到了吗（读不到 → **判不了**，不是「没找到」；
      C-09：**0 节点 = 通道不可用**）；
    * ``error`` —— 读不到的原因（人读）；
    * ``ocr`` —— **画面 OCR** 那一路的基线读数（工单 M27）。
    """

    candidates: tuple[ControlCandidate, ...] = ()
    reachable: bool = True
    error: str = ""
    #: OCR 那一路的基线读数（动作**之前**从画面读的）。没读就是 ``None``。
    ocr: OcrReading | None = None


@dataclass(frozen=True)
class VerifyRequest:
    """一次校验的输入。"""

    #: 期望（``None`` = 走默认校验）。
    expectation: Expectation | None = None
    #: **基线帧**（动作**之前**抓的那一帧）。
    baseline_frame: CapturedFrame | None = None
    #: **动作之前**的定点观测（由 :meth:`Verifier.begin` 给出）。
    baseline: VerificationBaseline | None = None
    #: 刚做完的那个动作。
    action: Action | None = None
    #: 最终落点（物理表面空间）。
    landing_point: tuple[int, int] | None = None
    #: 目标窗口句柄与标题（进动作日志）。
    window_handle: int | None = None
    window_title: str = ""
    #: 动作的目标描述（没有期望时，动作日志里的「前后状态」用它当目标）。
    target_description: str = ""
    #: 要 OCR 读**哪一块**（**物理像素**矩形 ``(left, top, right, bottom)``）。
    #: ``None`` = 整屏（工单 M27）。
    ocr_region: tuple[int, int, int, int] | None = None
    #: 这一块的**来源**（人读，进 OCR 读数记录 —— 供人工核对）。
    ocr_region_source: str = ""


@runtime_checkable
class Verifier(Protocol):
    """校验端口：**两段式** —— 先取基线，再判。

    形状：**一次校验的输入 → 一次校验的结论**。
    换注入点时这个形状不许变。
    """

    def begin(self, request: VerifyRequest) -> VerificationBaseline:
        """**动作之前**取基线观测（动作之后的判定要拿它对照）。"""
        ...

    def verify(self, request: VerifyRequest) -> VerificationResult:
        """**动作之后**判一次。"""
        ...


@dataclass(frozen=True)
class VerificationResult:
    """一次校验的结果。"""

    #: 结论（见本模块的 ``OUTCOME_*``）。
    outcome: str
    #: 被断言的期望（没有期望时是 ``None``）。
    expectation: Expectation | None = None
    #: 断言结论：``True`` 成立 / ``False`` **不成立** / ``None`` 判不了
    #: （**没有期望时恒为 ``None``** —— 默认校验不判成功）。
    satisfied: bool | None = None
    #: 每个手段的判定（``"met"`` / ``"not_met"`` / ``"unknown"``）。
    means: Mapping[str, str] = field(default_factory=dict)
    #: 期望内容（人读）。
    expected: str = ""
    #: 实际观测（人读）。
    observed: str = ""
    #: 期望与实际观测的**差异**（断言失败时读它就知道差在哪）。
    diff: str = ""
    #: 无变化检测的结果（默认校验时是结论本身）。
    change: ChangeResult | None = None
    #: 动作**前**的状态。
    state_before: Mapping[str, Any] | None = None
    #: 动作**后**的状态。
    state_after: Mapping[str, Any] | None = None
    #: **OCR 的读数**（工单 M27）—— 基线那一次 + 动作后那一次。
    #: 每次读数都带**区域坐标 + 读到的文本 + 区域来源**，可人工核对。
    ocr_readings: tuple[OcrReading, ...] = ()
    #: **真值来源**：观测值是从哪儿来的
    #: （「画面量的」/「树里取的」/「（没有）」—— 判不了时没有真值来源）。
    truth_source: str = ""
    #: 模型调用次数。**恒为 0** —— 模型不参与裁判。
    model_calls: int = 0

    @property
    def failed(self) -> bool:
        """断言**明确不成立**吗（只有这一种情况才算这一步失败）。"""
        return self.satisfied is False

    def to_dict(self) -> dict[str, Any]:
        """落盘形状。"""
        return {
            "outcome": self.outcome,
            "expectation": (
                self.expectation.to_dict() if self.expectation is not None else None
            ),
            "satisfied": self.satisfied,
            "means": dict(self.means),
            "expected": self.expected,
            "observed": self.observed,
            "diff": self.diff,
            "change": self.change.to_dict() if self.change is not None else None,
            "state_before": (
                dict(self.state_before) if self.state_before is not None else None
            ),
            "state_after": dict(self.state_after) if self.state_after is not None else None,
            "ocr_readings": [reading.to_dict() for reading in self.ocr_readings],
            "truth_source": self.truth_source,
            "model_calls": self.model_calls,
        }


@dataclass(frozen=True)
class _Observation:
    """一次定点观测的结果。"""

    controls: tuple[ControlCandidate, ...] = ()
    #: 树读到了吗（读不到时所有手段都判不了）。
    reachable: bool = True
    error: str = ""


def _text_corpus(controls: Sequence[ControlCandidate]) -> str:
    """控件里的**文本**（**只取控件名**）。

    ⚠️ **控件的值不进语料** —— 界面正文不进日志（架构决策备忘 §2.6 C-15），
    与 :func:`minicode.action.records.trace_candidates` 同一条规矩：
    控件名与控件框可以落，**值不落**。
    """
    return "\n".join(candidate.name for candidate in controls if candidate.name)


def _match(candidates: Sequence[ControlCandidate], name: str) -> list[ControlCandidate]:
    """按名字（**包含**语义，规则与 UIA 通道同一份）挑控件。"""
    return [candidate for candidate in candidates if name_matches(candidate.name, name)]


def _frame_rect(frame: CapturedFrame) -> tuple[int, int, int, int]:
    """一帧画面在**物理表面空间**里的矩形 ``(left, top, right, bottom)``。

    OCR 读数要记**区域坐标**（工单 M27 AC 6）—— 人工核对时靠它回到屏幕上。
    """
    return (
        frame.left,
        frame.top,
        frame.left + frame.width,
        frame.top + frame.height,
    )


class VerificationLayer:
    """校验层：期望断言 + 默认校验。"""

    def __init__(
        self,
        *,
        screenshot_port: ScreenshotPort,
        observer: StateObserver | None = None,
        ocr_port: OcrPort | None = None,
        params: ChangeDetectParams | None = None,
        clock: Callable[[], float] = time.perf_counter,
        sleeper: Callable[[float], None] = time.sleep,
    ):
        self.screenshot_port = screenshot_port
        #: 定点观察端口（可访问性树那一路）。缺省 = 树这一路判不了。
        self.observer = observer
        #: **OCR 端口**（「文本比对」这一路的**第二个来源**，工单 M27）。
        #: 缺省 = 只有树里的文本进比对（工单 M11 的口径，一个字没改）。
        self.ocr_port = ocr_port
        self.params = params or ChangeDetectParams()
        self._clock = clock
        self._sleeper = sleeper

    # ------------------------------------------------------------------
    # 两段式：先取基线，再判
    # ------------------------------------------------------------------

    def begin(self, request: VerifyRequest) -> VerificationBaseline:
        """**动作之前**取基线观测（动作之后的判定要拿它对照）。

        没有期望时**不观测**（默认校验只需要基线帧）。
        """
        observation = self._observe(request)
        ocr = self._ocr_read(request)
        if observation is None:
            return VerificationBaseline(
                candidates=(), reachable=False, error="没有期望", ocr=ocr
            )
        return VerificationBaseline(
            candidates=observation.controls,
            reachable=observation.reachable,
            error=observation.error,
            ocr=ocr,
        )

    def verify(self, request: VerifyRequest) -> VerificationResult:
        """动作之后跑一次校验。"""
        change = self._detect_change(request)
        observation = self._observe(request)
        ocr_now = self._ocr_read(request)
        state_before = self._state_before(request)
        state_after = self._state_after(request, observation, change)

        if request.expectation is None:
            # **默认校验**：只有「有反应 / 界面无变化」这个信号，
            # **不判成功、也不判失败**（工单 M11 AC 2 / AC 3）。
            outcome = (
                OUTCOME_CHANGED
                if change is not None and change.changed
                else OUTCOME_NO_CHANGE
            )
            return VerificationResult(
                outcome=outcome,
                expectation=None,
                satisfied=None,
                means={
                    "tree": Verdict.UNKNOWN.value,
                    "text": Verdict.UNKNOWN.value,
                    "pixels": (
                        Verdict.MET.value
                        if outcome == OUTCOME_CHANGED
                        else Verdict.NOT_MET.value
                    ),
                },
                expected="界面按预期变了",
                observed=(
                    "界面有变化并已安定"
                    if outcome == OUTCOME_CHANGED
                    else "界面无变化（变化率低于阈值且持续两帧）"
                ),
                diff="",
                change=change,
                state_before=state_before,
                state_after=state_after,
                model_calls=0,
            )

        means = self._judge(request, observation, ocr_now)
        verdicts = [Verdict(value) for value in means.values()]
        decidable = [verdict for verdict in verdicts if verdict is not Verdict.UNKNOWN]
        if not decidable:
            satisfied: bool | None = None
            outcome = OUTCOME_EXPECTATION_UNKNOWN
        elif any(verdict is Verdict.MET for verdict in decidable):
            satisfied = True
            outcome = OUTCOME_EXPECTATION_MET
        else:
            satisfied = False
            outcome = OUTCOME_EXPECTATION_NOT_MET
        expected, observed, diff = self._describe(
            request.expectation, observation, request.baseline, satisfied, ocr_now
        )
        return VerificationResult(
            outcome=outcome,
            expectation=request.expectation,
            satisfied=satisfied,
            means=means,
            expected=expected,
            observed=observed,
            diff=diff,
            change=change,
            state_before=state_before,
            state_after=state_after,
            ocr_readings=self._ocr_readings(request, ocr_now),
            truth_source=self._truth_source(
                request.expectation, observation, ocr_now, means, satisfied
            ),
            model_calls=0,
        )

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------

    def _detect_change(self, request: VerifyRequest) -> ChangeResult | None:
        """像素差分这一路：没有基线帧就**判不了**（不假装）。"""
        if request.baseline_frame is None or request.baseline_frame.is_empty:
            return None
        return poll_change(
            self.screenshot_port,
            request.baseline_frame,
            self.params,
            clock=self._clock,
            sleeper=self._sleeper,
            # 有期望时**不早收工**：变化可能来得很慢，
            # 早收工会把「还没生效」误判成「没生效」。
            early_exit_on_no_change=request.expectation is None,
        )

    def _observe(self, request: VerifyRequest) -> _Observation | None:
        """定点观测（可访问性树那一路）。"""
        if request.expectation is None or self.observer is None:
            return None
        try:
            controls = self.observer.observe(request.expectation.target)
        except Exception as exc:  # 读树失败 = 判不了，不是「没找到」
            return _Observation(controls=(), reachable=False, error=str(exc))
        return _Observation(controls=tuple(controls), reachable=True)

    def _ocr_read(self, request: VerifyRequest) -> OcrReading | None:
        """OCR 那一路：从画面上读文字（**只读**）。

        没接 OCR 端口 / 没有期望 → **不读**（``None``）。
        读不出来（抓不到画面 / 后端不可用 / 识别失败）→ 记一条
        ``unavailable`` 的读数 —— 上层据此判「判不了」（工单 M27 AC 5）。
        """
        if self.ocr_port is None or request.expectation is None:
            return None
        region = request.ocr_region
        source = request.ocr_region_source or ("整屏" if region is None else "")
        try:
            frame = self.screenshot_port.screenshot(region)
        except Exception as exc:
            return OcrReading(
                text="",
                region=region or (0, 0, 0, 0),
                region_source=source,
                status=OcrStatus.UNAVAILABLE,
                backend=self.ocr_port.name,
                error=f"抓画面失败：{exc}",
            )
        if frame.is_empty:
            return OcrReading(
                text="",
                region=region or (0, 0, 0, 0),
                region_source=source,
                status=OcrStatus.UNAVAILABLE,
                backend=self.ocr_port.name,
                error="画面是 0×0（抓不到）",
            )
        read_region = region if region is not None else _frame_rect(frame)
        try:
            text = self.ocr_port.read(frame)
        except Exception as exc:
            return OcrReading(
                text="",
                region=read_region,
                region_source=source,
                status=OcrStatus.UNAVAILABLE,
                backend=self.ocr_port.name,
                error=f"OCR 读不出来：{exc}",
            )
        return OcrReading(
            text=text,
            region=read_region,
            region_source=source,
            status=OcrStatus.OK,
            backend=self.ocr_port.name,
        )

    def _ocr_readings(
        self, request: VerifyRequest, ocr_now: OcrReading | None
    ) -> tuple[OcrReading, ...]:
        """这一步的**全部 OCR 读数**（基线那一次 + 动作后那一次）。

        每次读数都带区域坐标 + 读到的文本 + 区域来源 —— 可人工核对
        （工单 M27 AC 6）。
        """
        baseline = request.baseline.ocr if request.baseline is not None else None
        return tuple(reading for reading in (baseline, ocr_now) if reading is not None)

    def _judge(
        self,
        request: VerifyRequest,
        observation: _Observation | None,
        ocr_now: OcrReading | None,
    ) -> dict[str, str]:
        """三个手段各给一个判定（判不了就给 ``unknown``）。"""
        expectation = request.expectation
        assert expectation is not None  # 只有有期望时才走到这里
        tree = self._tree_verdict(expectation, observation, request.baseline)
        text = self._text_verdict(expectation, observation, request.baseline, ocr_now)
        return {
            "tree": tree.value,
            "text": text.value,
            # 像素差分**不判具体期望**（「像素变化 ≠ 成功」）。
            "pixels": Verdict.UNKNOWN.value,
        }

    def _tree_verdict(
        self,
        expectation: Expectation,
        observation: _Observation | None,
        baseline: VerificationBaseline | None,
    ) -> Verdict:
        """可访问性树这一路：查**状态 / 存在性**。"""
        if observation is None or not observation.reachable:
            return Verdict.UNKNOWN
        if baseline is None or not baseline.reachable:
            # 基线读不到 → 判不了「是不是真的变了」。
            return Verdict.UNKNOWN
        now = _match(observation.controls, expectation.target)
        before = _match(baseline.candidates, expectation.target)
        if expectation.kind is ExpectationKind.STATE_CHANGE:
            if not now or not before:
                # 现在或之前观测不到 → 判不了（树覆盖不全 / 控件变了）。
                return Verdict.UNKNOWN
            want = expectation.enabled
            if before[0].enabled is want:
                # 本来就是这个状态 → **没有发生变化**。
                return Verdict.NOT_MET
            return Verdict.MET if now[0].enabled is want else Verdict.NOT_MET
        if expectation.kind is ExpectationKind.TEXT_APPEARS:
            # 存在性：现在在、之前不在 → 它**出现**了。
            if not now:
                return Verdict.UNKNOWN
            return Verdict.NOT_MET if before else Verdict.MET
        if expectation.kind is ExpectationKind.TEXT_DISAPPEARS:
            if now:
                return Verdict.NOT_MET
            return Verdict.MET if before else Verdict.UNKNOWN
        return Verdict.UNKNOWN

    def _text_verdict(
        self,
        expectation: Expectation,
        observation: _Observation | None,
        baseline: VerificationBaseline | None,
        ocr_now: OcrReading | None,
    ) -> Verdict:
        """文本比对这一路：查**文本出现或消失**。

        两个来源（工单 M27）：

        * **树里的控件名**（工单 M11 的口径）；
        * **画面 OCR**（读不出的那些值靠它：缩放百分比 / 时钟读数 /
          微信聊天区的标题）。

        ⚠️ **接了 OCR 之后，画面这一路是权威的** ——
        树覆盖不全（那些值**根本不在树里**），所以「树里没有」
        **不能**说「期望不成立」；画面读到了才算数，读不到就是**判不了**
        （工单 M27 AC 2 / AC 5）。
        """
        target = expectation.target
        tree_now = (
            _text_corpus(observation.controls)
            if observation is not None and observation.reachable
            else None
        )
        tree_before = (
            _text_corpus(baseline.candidates)
            if baseline is not None and baseline.reachable
            else None
        )

        if self.ocr_port is None:
            # **没接 OCR**：树的口径（工单 M11 的行为，一个字没改）。
            if tree_now is None or tree_before is None:
                return Verdict.UNKNOWN
            present_now = target in tree_now
            present_before = target in tree_before
            if expectation.kind is ExpectationKind.TEXT_APPEARS:
                if not present_now:
                    return Verdict.NOT_MET
                return Verdict.NOT_MET if present_before else Verdict.MET
            if expectation.kind is ExpectationKind.TEXT_DISAPPEARS:
                if present_now:
                    return Verdict.NOT_MET
                return Verdict.MET if present_before else Verdict.UNKNOWN
            # 状态类期望：文本这一路判不了。
            return Verdict.UNKNOWN

        # **接了 OCR**：画面这一路是权威的。
        ocr_before = baseline.ocr if baseline is not None else None
        norm_target = normalize_ocr_text(target)
        # 「读到了画面」与「读出了字」是两件事：
        # 前者能证明**之前那一面**是什么样，后者才够格说「那儿没有它」。
        ocr_now_ok = ocr_now is not None and ocr_now.readable
        ocr_before_ok = ocr_before is not None and ocr_before.readable
        ocr_now_decisive = ocr_now is not None and ocr_now.decisive
        # **正面证据**（读到了它）：树与 OCR 取并 —— 树是精确的，OCR 是有损的，
        # 谁看到都算数。
        present_now = (tree_now is not None and target in tree_now) or (
            ocr_now_ok and norm_target in normalize_ocr_text(ocr_now.text)
        )
        present_before = (tree_before is not None and target in tree_before) or (
            ocr_before_ok and norm_target in normalize_ocr_text(ocr_before.text)
        )
        if expectation.kind is ExpectationKind.TEXT_APPEARS:
            if present_now:
                if present_before:
                    return Verdict.NOT_MET
                if not ocr_before_ok:
                    # 「之前」那一面读不出来 → 说不出「它出现了」。
                    return Verdict.UNKNOWN
                return Verdict.MET
            # 现在没有它：**读出了字**才算「不成立」，
            # 读不到 / 没读出字 → 判不了（工单 M27 AC 5）。
            return Verdict.NOT_MET if ocr_now_decisive else Verdict.UNKNOWN
        if expectation.kind is ExpectationKind.TEXT_DISAPPEARS:
            if present_now:
                return Verdict.NOT_MET
            if not ocr_now_decisive:
                return Verdict.UNKNOWN
            return Verdict.MET if present_before else Verdict.UNKNOWN
        # 状态类期望：文本这一路判不了。
        return Verdict.UNKNOWN

    def _describe(
        self,
        expectation: Expectation,
        observation: _Observation | None,
        baseline: VerificationBaseline | None,
        satisfied: bool | None,
        ocr_now: OcrReading | None = None,
    ) -> tuple[str, str, str]:
        """（期望内容, 实际观测, 差异）—— 断言失败时靠这三样定位问题。"""
        expected = expectation.describe()
        if observation is None:
            observed = "没有可用的观测（没接观察端口）"
        elif not observation.reachable:
            observed = f"读控件树失败：{observation.error}"
        else:
            controls = observation.controls
            if not controls:
                observed = f"树里没有与「{expectation.target}」对得上的控件"
            elif expectation.kind is ExpectationKind.STATE_CHANGE:
                states = "、".join(
                    f"{candidate.name}(enabled={candidate.enabled})"
                    for candidate in controls
                )
                before = (
                    "、".join(
                        f"{candidate.name}(enabled={candidate.enabled})"
                        for candidate in _match(
                            baseline.candidates if baseline else (), expectation.target
                        )
                    )
                    or "（之前没观测到）"
                )
                observed = f"现在：{states}｜之前：{before}"
            else:
                before_text = (
                    _text_corpus(baseline.candidates) if baseline is not None else ""
                )
                observed = (
                    f"现在：{_text_corpus(controls) or '（空）'}"
                    f"｜之前：{before_text or '（空）'}"
                )
        if ocr_now is not None:
            # OCR 那一路的读数也进「实际观测」—— 人工核对时一眼看得到。
            screen = ocr_now.text.strip() or f"（没读出字：{ocr_now.error}）"
            observed = f"{observed}｜画面（{ocr_now.region_source or '整屏'}）：{screen}"
        if satisfied is None:
            diff = "判不了（没有能判它的手段）"
        elif satisfied:
            diff = ""
        else:
            diff = f"期望：{expected}；实际：{observed}"
        return expected, observed, diff

    def _truth_source(
        self,
        expectation: Expectation,
        observation: _Observation | None,
        ocr_now: OcrReading | None,
        means: Mapping[str, str],
        satisfied: bool | None,
    ) -> str:
        """**真值来源**：这次观测值是从哪儿来的（工单 M27 AC 3）。

        * 「画面量的」—— 从画面上量的（OCR 读到的那一份）；
        * 「树里取的」—— 从可访问性树里取的；
        * 「（没有）」—— **判不了**，没有真值来源。
        """
        if satisfied is None:
            return TRUTH_SOURCE_NONE
        tree_now = (
            _text_corpus(observation.controls)
            if observation is not None and observation.reachable
            else None
        )
        text_decided = means.get("text", Verdict.UNKNOWN.value) != Verdict.UNKNOWN.value
        if not text_decided:
            # 文本这一路判不了 → 是树那一路定的 → 树里取的。
            return TRUTH_SOURCE_TREE
        if tree_now is not None and expectation.target in tree_now:
            return TRUTH_SOURCE_TREE
        if ocr_now is not None and ocr_now.readable:
            return TRUTH_SOURCE_SCREEN
        return TRUTH_SOURCE_TREE

    def _state_before(self, request: VerifyRequest) -> dict[str, Any]:
        """动作**前**的状态（进动作日志）。"""
        target = (
            request.expectation.target
            if request.expectation is not None
            else request.target_description
        )
        candidates = (
            request.baseline.candidates if request.baseline is not None else ()
        )
        matched = _match(candidates, target)
        candidate = matched[0] if matched else None
        return {
            "target": target,
            "present": candidate is not None,
            "enabled": candidate.enabled if candidate is not None else None,
            "rect": list(candidate.rect) if candidate is not None else None,
            "frame_sha256": (
                request.baseline_frame.digest()
                if request.baseline_frame is not None
                else None
            ),
        }

    def _state_after(
        self,
        request: VerifyRequest,
        observation: _Observation | None,
        change: ChangeResult | None,
    ) -> dict[str, Any]:
        """动作**后**的状态（进动作日志）。"""
        target = (
            request.expectation.target
            if request.expectation is not None
            else request.target_description
        )
        controls = observation.controls if observation is not None else ()
        candidate = controls[0] if controls else None
        return {
            "target": target,
            "present": candidate is not None,
            "enabled": candidate.enabled if candidate is not None else None,
            "rect": list(candidate.rect) if candidate is not None else None,
            "frame_sha256": change.frame_sha256 if change is not None else None,
            "change_rate": change.change_rate if change is not None else None,
        }

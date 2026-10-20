"""**UIA 通道**（工单 M3）：一句目标描述 → 唯一定位 → 执行前重读 → 物理像素落点。

流程（每一步的失败都**分类**报，不切视觉）：

1. **能力表** —— 这个应用（+版本）的 UIA 能不能用？不可用 → 通道级失败；
2. **定点查询** —— 只发**带属性条件**的查询；0 节点 / 超时 → 通道级失败；
3. **丢无效候选** —— 控件框全 0 或零面积的候选不进候选集；
4. **名字匹配** —— 匹配不到 → **覆盖失败**；
5. **唯一性** —— 候选唯一 → 零模型调用；不唯一 → **先做消歧**
   （模型只在候选集内指认；指认必须落在候选集内且恰好一个）；
   候选数超过上限 → 直接拒绝，**不调模型**；
6. **执行前重读** —— 重读一遍树再定位一次；原来的控件不见了 → **重定位失败**
   （宁可不动作，也不点看起来相似的另一个）。

**不产生任何坐标换算**：UIA 通道的落点直接取控件框的物理像素中心
（架构决策备忘 §3：最终落点在 UIA 通道内不经过 crop）。
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import TYPE_CHECKING

from minicode.action.errors import ErrorCode
from minicode.action.ports import (
    ControlCandidate,
    LocateRequest,
    LocateResult,
    SelectionEcho,
)
from minicode.model.errors import ModelError
from minicode.redact.errors import ChannelUnavailableError

from .capability import Availability, CapabilityTable
from .elements import (
    UiaElement,
    center_point,
    element_identity,
    is_valid_rect,
    name_matches,
    rect_center,
)
from .errors import UiaError, UiaQueryTimeout
from .ports import (
    CandidatePointer,
    PointerRequest,
    UiaFindRequest,
    UiaQueryPort,
)

if TYPE_CHECKING:  # pragma: no cover - 只为类型标注
    from minicode.redact.redactor import ModelTreeView, TreeRedactor

__all__ = [
    "UiaChannel",
    "QUERY_SCOPE_WINDOW_ANCHORED",
    "DEFAULT_MAX_CANDIDATES",
    "CANDIDATE_LIMIT_SOURCE",
]

#: UIA 通道的查询口径：**窗口锚定**（在锚定窗口的子树里定点查）。
#: 桌面级查询（全桌面遍历）是另一条口径，**两者不许混用**
#: （架构决策备忘 §2.6 C-09：全桌面遍历约 1000 个元素要 ≥8 秒）。
QUERY_SCOPE_WINDOW_ANCHORED = "window-anchored"

#: **候选数上限**（运行时参数）。取值 **8**。
#:
#: 来源：ADR-0004「消歧失败、候选数超过上限（默认 8）、或指认不唯一 → 走 L4
#: 错误处理并回报候选集」+ 工单 M4 AC 4。**它是运行时参数**：调用方可以给别的值，
#: 但**取值来源必须被记录**（进追踪记录与动作记录）。
DEFAULT_MAX_CANDIDATES = 8

#: 候选数上限默认值的**来源**（随记录落盘，便于事后追溯这个 8 是哪来的）。
CANDIDATE_LIMIT_SOURCE = "ADR-0004「候选数超过上限（默认 8）」+ 工单 M4 AC 4"


def candidate_limit_source(max_candidates: int) -> str:
    """候选数上限的**取值来源**（运行时参数 → 来源随记录落盘）。

    用了默认值 → 写默认值的出处；调用方改了值 → 说清是**覆盖**来的，
    不然记录里会写着「默认 8」而实际用的不是 8（工单 M4 AC 4）。
    """
    if max_candidates == DEFAULT_MAX_CANDIDATES:
        return CANDIDATE_LIMIT_SOURCE
    return (
        f"调用方覆盖（默认值 {DEFAULT_MAX_CANDIDATES} 的来源：{CANDIDATE_LIMIT_SOURCE}）"
    )

#: **覆盖失败**的错误码：候选集为空（无 provider / 树被门控 / 目标不在树里）。
#: 只有它允许切视觉（架构决策备忘 §2.5 ①）；选择失败与「通道被拒绝启用」都不允许。
_COVERAGE_FAILURES = frozenset({ErrorCode.LOCATE_FAILED, ErrorCode.TARGET_NOT_FOUND})


class _ChannelRefused(Exception):
    """**通道被拒绝启用**（硬闸门拦下，如脱敏未上线）。

    与「定位失败」分开：这是**策略拒绝**，不是「找不到 / 选不出」。
    """


class UiaChannel:
    """UIA 通道（结构感知通道）：**零缩放换算、零模型成本**。"""

    def __init__(
        self,
        *,
        query: UiaQueryPort,
        capability: CapabilityTable,
        pointer: CandidatePointer | None = None,
        redactor: TreeRedactor | None = None,
        max_candidates: int = DEFAULT_MAX_CANDIDATES,
    ):
        self.query = query
        self.capability = capability
        #: 消歧指认（模型）。缺省 = 没有消歧通道（候选不唯一时直接拒绝）。
        self.pointer = pointer
        #: 脱敏器（模型侧文本的唯一出口）。缺省 = **不许**让模型看候选集。
        self.redactor = redactor
        #: 候选数上限（超过就直接拒绝，不调模型）。
        self.max_candidates = int(max_candidates)
        #: 这个上限的**来源**（跟着记录落盘；调用方改了值就写「覆盖」）。
        self.candidate_limit_source = candidate_limit_source(self.max_candidates)

    # ------------------------------------------------------------------
    # 主流程
    # ------------------------------------------------------------------

    def locate(self, request: LocateRequest) -> LocateResult:
        """定位一个控件。返回选中项 + 物理像素落点，或**分类**的失败。"""
        description = (request.target_description or "").strip()
        if not description:
            # 空描述不是「找不到」（那是覆盖失败），是**根本没得找**：
            # 不许切视觉（切视觉也一样没得找）。
            return _failure(
                ErrorCode.INVALID_TARGET,
                "目标描述为空：没法定位一个没有描述的目标",
                limit=self.max_candidates,
                limit_source=self.candidate_limit_source,
            )
        try:
            entry = self.capability.availability(request.anchor)
        except UiaError as exc:
            return _failure(exc.code, exc.message)
        if entry.availability is Availability.UNAVAILABLE:
            return _failure(
                ErrorCode.LOCATE_FAILED,
                f"UIA 通道不可用（{entry.app} {entry.version}）：{entry.reason}",
            )

        observations = 0
        try:
            elements, observations = self._observe(request, description, observations)
        except UiaError as exc:
            return _failure(exc.code, exc.message, observations=observations)

        # 无效控件框的候选不进候选集（全 0 / 零面积）。
        usable = [element for element in elements if is_valid_rect(element.rect)]
        matched = [element for element in usable if _name_matches(element, description)]
        if not matched:
            return self._no_candidate(request, description, matched, observations)
        if len(matched) > self.max_candidates:
            return _failure(
                ErrorCode.TOO_MANY_CANDIDATES,
                f"候选数 {len(matched)} 超过上限 {self.max_candidates}，直接拒绝执行",
                candidates=matched,
                observations=observations,
                limit=self.max_candidates,
                limit_source=self.candidate_limit_source,
            )

        model_calls = 0
        model_view: ModelTreeView | None = None
        selection_echo = None
        if len(matched) == 1:
            selected = matched[0]
        else:
            try:
                selected, model_calls, model_view, selection_echo = self._disambiguate(
                    request, description, matched
                )
            except _ChannelRefused as exc:
                # 脱敏闸门：这条通道被拒绝启用 → 明确报错，**零模型调用**。
                return _failure(
                    ErrorCode.CHANNEL_DISABLED,
                    str(exc),
                    candidates=matched,
                    observations=observations,
                    limit=self.max_candidates,
                    limit_source=self.candidate_limit_source,
                )
            except ModelError as exc:
                # 模型侧失败（如 HTTP 错误 / 思考链吃光预算没有输出）：
                # 候选里有、但选不出来 → **选择失败**（模型侧错误如实回传在消息里，
                # 请求日志那边也记了一条）。**零动作下发**。
                return _failure(
                    ErrorCode.AMBIGUOUS_TARGET,
                    f"消歧失败：模型调用失败（{exc}）",
                    candidates=matched,
                    model_calls=1,
                    observations=observations,
                    limit=self.max_candidates,
                    limit_source=self.candidate_limit_source,
                )
            if selected is None:
                return _failure(
                    ErrorCode.AMBIGUOUS_TARGET,
                    f"「{description}」匹配到 {len(matched)} 个候选，"
                    "消歧未给出唯一指认，拒绝执行",
                    candidates=matched,
                    model_calls=model_calls,
                    observations=observations,
                    selection_echo=selection_echo,
                    limit=self.max_candidates,
                    limit_source=self.candidate_limit_source,
                )

        # -- 执行前重读：重读一遍树再定位一次 ---------------------------
        try:
            second, observations = self._observe(request, description, observations)
        except UiaError as exc:
            return _failure(
                exc.code,
                exc.message,
                candidates=matched,
                model_calls=model_calls,
                observations=observations,
                selection_echo=selection_echo,
                limit=self.max_candidates,
                limit_source=self.candidate_limit_source,
            )
        relocated = self._relocate(selected, elements, second)
        if relocated is None:
            return _failure(
                ErrorCode.RELOCATE_FAILED,
                "重定位失败：重读控件树后找不到同一个控件，宁可不动作",
                candidates=matched,
                model_calls=model_calls,
                observations=observations,
                selection_echo=selection_echo,
                limit=self.max_candidates,
                limit_source=self.candidate_limit_source,
            )
        final = relocated
        return LocateResult(
            selected=to_candidate(final),
            landing_point=center_point(final.rect),
            candidates=tuple(to_candidate(element) for element in matched),
            model_view=model_view,
            invokable=final.supports_invoke,
            model_calls=model_calls,
            observations=observations,
            query_scope=QUERY_SCOPE_WINDOW_ANCHORED,
            selection_echo=selection_echo,
            allows_visual_fallback=False,
            candidate_limit=self.max_candidates,
            candidate_limit_source=self.candidate_limit_source,
        )

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------

    def _no_candidate(
        self,
        request: LocateRequest,
        description: str,
        candidates: Sequence[UiaElement],
        observations: int,
    ) -> LocateResult:
        """没有匹配候选时的**两态**判定。

        树里**一个节点都没有** → **通道级失败**（0 节点 = 静默退化，
        不是「目标不存在」，架构决策备忘 §2.6 C-09）；
        树能读但没有这个目标 → **覆盖失败**。
        """
        if not self.query.has_nodes(request.anchor):
            return _failure(
                ErrorCode.LOCATE_FAILED,
                f"通道不可用：控件树读不出节点（查询「{description}」返回 0 节点）",
                candidates=candidates,
                observations=observations,
                limit=self.max_candidates,
                limit_source=self.candidate_limit_source,
            )
        return _failure(
            ErrorCode.TARGET_NOT_FOUND,
            f"覆盖失败：控件树里没有与「{description}」匹配的候选",
            candidates=candidates,
            observations=observations,
            limit=self.max_candidates,
            limit_source=self.candidate_limit_source,
        )

    def _observe(
        self, request: LocateRequest, description: str, observations: int
    ) -> tuple[tuple[UiaElement, ...], int]:
        """读一次树（**定点查询**：带属性条件）。"""
        find_request = UiaFindRequest(
            anchor=request.anchor,
            name=description,
            control_type=request.control_type or None,
        )
        result = self.query.find(find_request)
        observations += 1
        if result.timed_out:
            raise UiaQueryTimeout("查询超时（约 5 秒阈值）")
        return tuple(result.elements), observations

    def _relocate(
        self,
        selected: UiaElement,
        first: Sequence[UiaElement],
        second: Sequence[UiaElement],
    ) -> UiaElement | None:
        """执行前重读之后的**重定位**（跨观测稳定的条件，不用元素 id）。

        规则（工单 M4 AC 8）：

        1. **身份族**（控件类型 / 类名 / 自动化标识 / 名字 相同）→ 同一族控件；
        2. 族里的**个数变了**（控件没了，或多出来一个同名控件）→ **认不准**，
           返回 ``None``（宁可不动作，也不点另一个像的）；
        3. 个数没变 → 取**离原位置最近**的那一个：控件**移动过**也是它
           （落点用**新框**）。

        ⚠️ 元素 id 每次观测重新生成，**跨观测不可比**，因此这里一次都不用它。
        """
        identity = element_identity(selected)
        before = [
            element
            for element in first
            if is_valid_rect(element.rect) and element_identity(element) == identity
        ]
        after = [
            element
            for element in second
            if is_valid_rect(element.rect) and element_identity(element) == identity
        ]
        if not after or len(before) != len(after):
            return None
        if len(after) == 1:
            return after[0]
        origin = rect_center(selected.rect)
        distances = [
            (math.dist(origin, rect_center(element.rect)), element) for element in after
        ]
        best = min(distance for distance, _ in distances)
        nearest = [element for distance, element in distances if distance == best]
        if len(nearest) != 1:
            # 分不出远近 → 认不准，宁可不动作。
            return None
        return nearest[0]

    def _disambiguate(
        self,
        request: LocateRequest,
        description: str,
        candidates: Sequence[UiaElement],
    ) -> tuple[UiaElement | None, int, ModelTreeView | None, SelectionEcho | None]:
        """**消歧**：模型只在候选集内指认，指认必须落在候选集内且恰好一个。

        脱敏闸门（架构决策备忘 §2.6 C-15）：**脱敏没上线就不许把控件树送模型**
        —— 这时这条通道被**拒绝启用**，抛 :class:`_ChannelRefused`，
        **一个模型调用都不发生**。

        返回的第三项是**已脱敏**的模型侧视图：上层直接复用它，
        模型侧文本因此**只有一个出口、只渲染一次**（M2 的硬要求）。
        """
        if self.pointer is None:
            return None, 0, None, None
        if self.redactor is None:
            raise _ChannelRefused(
                "脱敏未上线，禁止把控件树送模型"
                "（架构决策备忘 §2.6 C-15：脱敏必须排在所有通道之前）"
            )
        try:
            view = self.redactor.render([to_candidate(element) for element in candidates])
        except ChannelUnavailableError as exc:
            raise _ChannelRefused(str(exc)) from exc
        result = self.pointer.point(
            PointerRequest(
                target_description=description,
                candidates=view.controls,
                model_view=view.text,
                frame=request.frame,
            )
        )
        chosen = [
            element for element in candidates if element.element_id in result.chosen_ids
        ]
        if len(result.chosen_ids) != 1 or len(chosen) != 1:
            return None, 1, view, result.echo
        return chosen[0], 1, view, result.echo


def to_candidate(element: UiaElement) -> ControlCandidate:
    """UIA 元素 → **控件候选**（动作执行缝认识的那一种）。"""
    return ControlCandidate(
        name=element.name,
        control_type=element.control_type,
        rect=element.rect,
        window_handle=element.window_handle,
        automation_id=element.automation_id,
        element_id=element.element_id,
        value=element.value,
        help_text=element.help_text,
        is_password=element.is_password,
    )


def _name_matches(element: UiaElement, description: str) -> bool:
    """控件名与目标描述是否对得上（规则只有一份：:func:`name_matches`）。"""
    return name_matches(element.name, description)


def _failure(
    code: ErrorCode,
    message: str,
    *,
    candidates: Sequence[UiaElement] = (),
    model_calls: int = 0,
    observations: int = 0,
    selection_echo: SelectionEcho | None = None,
    limit: int = 0,
    limit_source: str = "",
) -> LocateResult:
    """失败结果：**零动作下发**，并带上要回报的候选集。

    ``allows_visual_fallback`` 由**错误码**决定：只有覆盖失败
    （候选集为空）为真；选择失败与「通道被拒绝启用」都不许切视觉
    （架构决策备忘 §2.5 ①）。
    """
    return LocateResult(
        error_code=code,
        error_message=message,
        candidates=tuple(to_candidate(element) for element in candidates),
        model_calls=model_calls,
        observations=observations,
        query_scope=QUERY_SCOPE_WINDOW_ANCHORED,
        selection_echo=selection_echo,
        allows_visual_fallback=code in _COVERAGE_FAILURES,
        candidate_limit=limit,
        candidate_limit_source=limit_source,
    )

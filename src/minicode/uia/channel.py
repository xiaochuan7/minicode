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

from collections.abc import Sequence
from typing import TYPE_CHECKING

from minicode.action.errors import ErrorCode
from minicode.action.ports import ControlCandidate, LocateRequest, LocateResult
from minicode.redact.errors import ChannelUnavailableError

from .capability import Availability, CapabilityTable
from .elements import (
    UiaElement,
    center_point,
    element_identity,
    is_same_place,
    is_valid_rect,
    name_matches,
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

__all__ = ["UiaChannel", "QUERY_SCOPE_WINDOW_ANCHORED"]

#: UIA 通道的查询口径：**窗口锚定**（在锚定窗口的子树里定点查）。
#: 桌面级查询（全桌面遍历）是另一条口径，**两者不许混用**
#: （架构决策备忘 §2.6 C-09：全桌面遍历约 1000 个元素要 ≥8 秒）。
QUERY_SCOPE_WINDOW_ANCHORED = "window-anchored"


class UiaChannel:
    """UIA 通道（结构感知通道）：**零缩放换算、零模型成本**。"""

    def __init__(
        self,
        *,
        query: UiaQueryPort,
        capability: CapabilityTable,
        pointer: CandidatePointer | None = None,
        redactor: TreeRedactor | None = None,
        max_candidates: int = 8,
    ):
        self.query = query
        self.capability = capability
        #: 消歧指认（模型）。缺省 = 没有消歧通道（候选不唯一时直接拒绝）。
        self.pointer = pointer
        #: 脱敏器（模型侧文本的唯一出口）。缺省 = **不许**让模型看候选集。
        self.redactor = redactor
        #: 候选数上限（超过就直接拒绝，不调模型）。
        self.max_candidates = int(max_candidates)

    # ------------------------------------------------------------------
    # 主流程
    # ------------------------------------------------------------------

    def locate(self, request: LocateRequest) -> LocateResult:
        """定位一个控件。返回选中项 + 物理像素落点，或**分类**的失败。"""
        description = (request.target_description or "").strip()
        if not description:
            return _failure(ErrorCode.TARGET_NOT_FOUND, "目标描述为空")
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
            )

        model_calls = 0
        model_view: ModelTreeView | None = None
        if len(matched) == 1:
            selected = matched[0]
        else:
            selected, model_calls, model_view = self._disambiguate(description, matched)
            if selected is None:
                return _failure(
                    ErrorCode.AMBIGUOUS_TARGET,
                    f"「{description}」匹配到 {len(matched)} 个候选，"
                    "消歧未给出唯一指认，拒绝执行",
                    candidates=matched,
                    model_calls=model_calls,
                    observations=observations,
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
            )
        same = [
            element
            for element in second
            if is_valid_rect(element.rect)
            and element_identity(element) == element_identity(selected)
            and is_same_place(element.rect, selected.rect)
        ]
        if len(same) != 1:
            return _failure(
                ErrorCode.RELOCATE_FAILED,
                "重定位失败：重读控件树后找不到同一个控件，宁可不动作",
                candidates=matched,
                model_calls=model_calls,
                observations=observations,
            )
        final = same[0]
        return LocateResult(
            selected=to_candidate(final),
            landing_point=center_point(final.rect),
            candidates=tuple(to_candidate(element) for element in matched),
            model_view=model_view,
            invokable=final.supports_invoke,
            model_calls=model_calls,
            observations=observations,
            query_scope=QUERY_SCOPE_WINDOW_ANCHORED,
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
            )
        return _failure(
            ErrorCode.TARGET_NOT_FOUND,
            f"覆盖失败：控件树里没有与「{description}」匹配的候选",
            candidates=candidates,
            observations=observations,
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

    def _disambiguate(
        self, description: str, candidates: Sequence[UiaElement]
    ) -> tuple[UiaElement | None, int, ModelTreeView | None]:
        """**消歧**：模型只在候选集内指认，指认必须落在候选集内且恰好一个。

        没有消歧通道 / 脱敏没上线 → 不调模型（fail-closed，架构决策备忘 §2.6 C-15）。

        返回的第三项是**已脱敏**的模型侧视图：上层直接复用它，
        模型侧文本因此**只有一个出口、只渲染一次**（M2 的硬要求）。
        """
        if self.pointer is None or self.redactor is None:
            return None, 0, None
        try:
            view = self.redactor.render([to_candidate(element) for element in candidates])
        except ChannelUnavailableError:
            return None, 0, None
        result = self.pointer.point(
            PointerRequest(
                target_description=description,
                candidates=view.controls,
                model_view=view.text,
            )
        )
        chosen = [
            element for element in candidates if element.element_id in result.chosen_ids
        ]
        if len(result.chosen_ids) != 1 or len(chosen) != 1:
            return None, 1, view
        return chosen[0], 1, view


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
) -> LocateResult:
    """失败结果：**零动作下发**，并带上要回报的候选集。"""
    return LocateResult(
        error_code=code,
        error_message=message,
        candidates=tuple(to_candidate(element) for element in candidates),
        model_calls=model_calls,
        observations=observations,
        query_scope=QUERY_SCOPE_WINDOW_ANCHORED,
    )

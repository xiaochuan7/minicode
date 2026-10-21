"""**浏览器通道**（工单 M13）：页面结构定位 → 浏览器自己的输入通道操作它。

流程（失败**分类**报，与桌面通道同一套口径）：

1. **能力表** —— 这个应用（+版本）的浏览器通道能不能用？不可用 → 通道级失败；
2. **页面结构快照** —— 按**可见文本 / 角色**找元素（不是像素比对）；
3. **唯一性** —— 候选唯一 → 零模型调用；不唯一 → **先做消歧**
   （模型只在候选集内指认，与桌面通道同一套实现）；
   候选数超过上限 → 直接拒绝，**不调模型**；
4. **重取快照再定位** —— **页面元素引用有时效**（页面一变旧引用就不解析了），
   所以执行前重新取一次快照、按身份族重新定位，**不复用过期引用**。

**不产生第二套坐标换算**：落点取**可见文本的实际包围盒**在**物理表面空间**的中心
（:func:`minicode.action.geometry.center_point` —— 与桌面通道同一处）。
文本区域由**文本度量**这一层算出来（:mod:`minicode.browser.textmetrics`，
**浏览器通道独有**）；文本度量拿不到 → **判不了**，
**不静默回落到元素矩形中心**（工单 M28）。
动作由**浏览器自己的输入通道**下发（浏览器通道的定义，
`CONTEXT.md`），不走桌面鼠标注入。
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import TYPE_CHECKING

from minicode.action.errors import ErrorCode, failure_type_of
from minicode.action.geometry import center_point, is_valid_rect
from minicode.action.ports import (
    CHANNEL_BROWSER,
    DEFAULT_MAX_CANDIDATES,
    CandidatePointer,
    LocateRequest,
    LocateResult,
    PointerRequest,
    SelectionEcho,
    candidate_limit_source,
)
from minicode.action.text import name_matches
from minicode.capability import Availability
from minicode.model.errors import ModelError
from minicode.redact.errors import ChannelUnavailableError

from .capability import BrowserCapabilityTable
from .elements import BrowserElement, element_identity, to_candidate
from .ports import PageSnapshot, PageStructurePort

if TYPE_CHECKING:  # pragma: no cover - 只为类型标注
    from minicode.redact.redactor import ModelTreeView, TreeRedactor

__all__ = [
    "BrowserChannel",
    "CHANNEL_NAME",
    "QUERY_SCOPE_PAGE_STRUCTURE",
]

#: 浏览器通道在追踪记录里的名字。
CHANNEL_NAME = CHANNEL_BROWSER

#: 浏览器通道的查询口径：**页面结构快照**（可访问性树）。
#: 与桌面通道的「窗口锚定」是**两套口径**，不许混用。
QUERY_SCOPE_PAGE_STRUCTURE = "page-structure"


class _ChannelRefused(Exception):
    """**通道被拒绝启用**（硬闸门拦下，如脱敏未上线）。"""


class BrowserChannel:
    """浏览器通道：按页面结构定位元素，落点落在该元素的**可见文本**上。"""

    def __init__(
        self,
        *,
        structure: PageStructurePort,
        capability: BrowserCapabilityTable,
        pointer: CandidatePointer | None = None,
        redactor: TreeRedactor | None = None,
        max_candidates: int = DEFAULT_MAX_CANDIDATES,
    ):
        self.structure = structure
        self.capability = capability
        #: 消歧指认（模型）。缺省 = 没有消歧通道（候选不唯一时直接拒绝）。
        self.pointer = pointer
        #: 脱敏器（模型侧文本的唯一出口）。缺省 = **不许**让模型看页面结构。
        self.redactor = redactor
        #: 候选数上限（超过就直接拒绝，不调模型）。**所有通道同一条**。
        self.max_candidates = int(max_candidates)
        self.candidate_limit_source = candidate_limit_source(self.max_candidates)

    # ------------------------------------------------------------------
    # 主流程
    # ------------------------------------------------------------------

    def locate(self, request: LocateRequest) -> LocateResult:
        """定位一个页面元素。返回选中项 + 物理像素落点，或**分类**的失败。"""
        description = (request.target_description or "").strip()
        if not description:
            return _failure(
                ErrorCode.INVALID_TARGET,
                "目标描述为空：没法定位一个没有描述的目标",
                limit=self.max_candidates,
                limit_source=self.candidate_limit_source,
            )
        entry = self.capability.availability(request.anchor)
        if entry.availability is Availability.UNAVAILABLE:
            return _failure(
                ErrorCode.LOCATE_FAILED,
                f"浏览器通道不可用（{entry.app} {entry.version}）：{entry.reason}",
            )

        first = self.structure.snapshot()
        if not first.elements:
            # **0 元素 = 静默退化**（架构决策备忘 §2.6 C-09），不是「目标不存在」——
            # 与桌面通道同一个分支（控件树读不出节点 → `locate_failed`）。
            return _failure(
                ErrorCode.LOCATE_FAILED,
                "通道不可用：页面结构快照读不出元素（0 元素 = 静默退化，不是「没找到」）",
                limit=self.max_candidates,
                limit_source=self.candidate_limit_source,
            )
        matched = self._match(first, description, request.control_type)
        if not matched:
            return _failure(
                ErrorCode.TARGET_NOT_FOUND,
                f"覆盖失败：页面结构快照里没有与「{description}」匹配的元素",
                limit=self.max_candidates,
                limit_source=self.candidate_limit_source,
            )
        if len(matched) > self.max_candidates:
            return _failure(
                ErrorCode.TOO_MANY_CANDIDATES,
                f"候选数 {len(matched)} 超过上限 {self.max_candidates}，直接拒绝执行",
                candidates=matched,
                limit=self.max_candidates,
                limit_source=self.candidate_limit_source,
            )

        model_calls = 0
        model_view: ModelTreeView | None = None
        selection_echo: SelectionEcho | None = None
        if len(matched) == 1:
            selected = matched[0]
        else:
            try:
                selected, model_calls, model_view, selection_echo = self._disambiguate(
                    request, description, matched
                )
            except _ChannelRefused as exc:
                return _failure(
                    ErrorCode.CHANNEL_DISABLED,
                    str(exc),
                    candidates=matched,
                    limit=self.max_candidates,
                    limit_source=self.candidate_limit_source,
                )
            except ModelError as exc:
                return _failure(
                    ErrorCode.AMBIGUOUS_TARGET,
                    f"消歧失败：模型调用失败（{exc}）",
                    candidates=matched,
                    model_calls=1,
                    selection_echo=selection_echo,
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
                    selection_echo=selection_echo,
                    limit=self.max_candidates,
                    limit_source=self.candidate_limit_source,
                )

        # -- 重取快照再定位（元素引用有时效，工单 M13 AC 3）----------------
        second = self.structure.snapshot()
        relocated = self._relocate(selected, second)
        if relocated is None:
            return _failure(
                ErrorCode.RELOCATE_FAILED,
                "重定位失败：重取页面结构后找不到同一个元素（页面可能变了），"
                "宁可不动作也不点相似的另一个",
                candidates=matched,
                model_calls=model_calls,
                selection_echo=selection_echo,
                limit=self.max_candidates,
                limit_source=self.candidate_limit_source,
            )
        # -- 落点：可见文本的**实际区域**（工单 M28）------------------------
        # 落点不再取**元素矩形中心**（扁平 DOM 上那个框是整行宽，离文字很远），
        # 改取**可见文本的包围盒中心**。
        # ⚠️ **文本度量拿不到 → 判不了**（元素没有可见文本 / 文本被裁 / 字体未加载）：
        # **不静默回落到元素矩形中心** —— 候选在那儿，只是定不下落点 → **选择失败**。
        text = relocated.text
        if text.rect is None:
            return _failure(
                ErrorCode.INVALID_GEOMETRY,
                "文本度量拿不到："
                f"{text.reason or '读不出可见文本的实际区域'}"
                " —— 判不了（不静默回落到元素矩形中心）",
                candidates=matched,
                model_calls=model_calls,
                selection_echo=selection_echo,
                limit=self.max_candidates,
                limit_source=self.candidate_limit_source,
            )
        return LocateResult(
            selected=to_candidate(relocated),
            landing_point=center_point(text.rect),
            candidates=tuple(to_candidate(element) for element in matched),
            model_view=model_view,
            #: 浏览器通道的动作走**浏览器自己的输入通道**（不是桌面鼠标注入）。
            invokable=True,
            model_calls=model_calls,
            observations=2,
            query_scope=QUERY_SCOPE_PAGE_STRUCTURE,
            selection_echo=selection_echo,
            candidate_limit=self.max_candidates,
            candidate_limit_source=self.candidate_limit_source,
            channel=CHANNEL_NAME,
        )

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------

    def _match(
        self, snapshot: PageSnapshot, description: str, role_hint: str
    ) -> tuple[BrowserElement, ...]:
        """按**可见文本 / 角色**挑候选（规则与桌面通道共用）。

        * 文本匹配用 :func:`minicode.action.text.name_matches`（**包含**语义）；
        * 角色提示给了就再加上角色条件；
        * 矩形读不成合法矩形的候选**丢掉**（架构决策备忘 §2.6 C-08）。
        """
        hint = (role_hint or "").strip().lower()
        matched = []
        for element in snapshot.elements:
            if not is_valid_rect(element.rect):
                continue
            if not name_matches(element.name, description):
                continue
            if hint and hint not in (element.role or "").lower():
                continue
            matched.append(element)
        return tuple(matched)

    def _relocate(
        self, selected: BrowserElement, second: PageSnapshot
    ) -> BrowserElement | None:
        """重取快照之后的**重定位**（跨快照稳定的条件，不用元素引用）。

        规则：

        1. **身份族**（角色 + 可见文本 相同）→ 同一个元素；
        2. 族里一个都没有 → 认不准，返回 ``None``（页面变了，宁可不动作）；
        3. 族里有好几个 → 取**离原位置最近**的那一个；分不出远近 → 认不准。

        ⚠️ **元素引用（``element_id``）一次都不用**：它有时效，
        页面一变就不解析了（工单 M13 AC 3）。
        """
        identity = element_identity(selected)
        after = [
            element
            for element in second.elements
            if is_valid_rect(element.rect) and element_identity(element) == identity
        ]
        if not after:
            return None
        if len(after) == 1:
            return after[0]
        origin = selected.center
        distances = [(math.dist(origin, element.center), element) for element in after]
        best = min(distance for distance, _ in distances)
        nearest = [element for distance, element in distances if distance == best]
        if len(nearest) != 1:
            return None
        return nearest[0]

    def _disambiguate(
        self,
        request: LocateRequest,
        description: str,
        candidates: Sequence[BrowserElement],
    ) -> tuple[BrowserElement | None, int, ModelTreeView | None, SelectionEcho | None]:
        """**消歧**：模型只在候选集内指认（与桌面通道同一套实现）。

        脱敏闸门（架构决策备忘 §2.6 C-15）：**脱敏没上线就不许把页面结构送模型**
        —— 这时这条通道被**拒绝启用**，抛 :class:`_ChannelRefused`，
        **一个模型调用都不发生**。
        """
        if self.pointer is None:
            return None, 0, None, None
        if self.redactor is None:
            raise _ChannelRefused(
                "脱敏未上线，禁止把页面结构送模型"
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


def _failure(
    code: ErrorCode,
    message: str,
    *,
    candidates: Sequence[BrowserElement] = (),
    model_calls: int = 0,
    selection_echo: SelectionEcho | None = None,
    limit: int = 0,
    limit_source: str = "",
) -> LocateResult:
    """失败结果：**零动作下发**，并带上要回报的候选集。

    失败类型由错误码推导（映射只有一处）—— 只有**覆盖失败**允许切视觉。
    """
    failure_type = failure_type_of(code)
    return LocateResult(
        error_code=code,
        error_message=message,
        candidates=tuple(to_candidate(element) for element in candidates),
        model_calls=model_calls,
        observations=1,
        query_scope=QUERY_SCOPE_PAGE_STRUCTURE,
        selection_echo=selection_echo,
        failure_type=failure_type.value,
        candidate_limit=limit,
        candidate_limit_source=limit_source,
        channel=CHANNEL_NAME,
    )

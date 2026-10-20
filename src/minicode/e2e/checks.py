"""**验收条款的判据**（工单 M14 的验收标准 → 可判的条款）。

每个函数返回 ``(成立吗, 人读的细节)``，细节里写清「差在哪」——
报告里要能看出**为什么**这条不成立。

⚠️ **三条口径**：

* **「判不了」一律判成不成立**（并写明是判不了）：判不了的条款
  进不了通过率，也不许被记成通过（口径见 :mod:`minicode.e2e.runner`）。
* **缺测量 ≠ 不成立**：上下文里**没有那个键** = **没测过** → 判「判不了」，
  不许当成「测了，结果是不成立」（那是把缺测量伪装成产品缺陷）。
* **落点偏差上限**用「上限」这个词（``CONTEXT.md`` 把「容差」列为 avoid：
  它指「越界容差」还是「标定容差」说不清）。
"""

from __future__ import annotations

from typing import Any

from minicode.action.ports import CHANNEL_VISUAL

from .runner import TaskEvidence

__all__ = [
    "channel_is",
    "channel_visual",
    "failure_type_coverage",
    "landing_within",
    "capability_is",
    "context_value",
    "zoom_fresh_capture",
    "zoom_fullscreen_answer",
    "gate_unlocked_before_unavailable",
    "capability_usable_or_cut_visual",
    "window_title_matches_picked",
    "modal_handoff_ok",
    "page_title_is",
    "structure_channel_preferred",
    "cross_stack_semantics_stable",
    "input_channel_recorded",
    "verification_source_is_structure",
    "context_matches_picked",
    "failure_types_separated",
    "coordinates_stayed_in_window",
    "channel_and_failure_readable",
]


def channel_is(expected: str) -> Any:
    """**这一步走了哪条通道**（如「定位依据是页面结构」→ 通道 = 浏览器通道）。"""

    def check(evidence: TaskEvidence) -> tuple[bool, str]:
        channels = [step.channel for step in evidence.steps if step.channel]
        if not channels:
            return False, "没有一步走过定位通道（**判不了**走的是哪条）"
        wrong = [channel for channel in channels if channel != expected]
        if wrong:
            return False, f"走的通道是 {'、'.join(sorted(set(channels)))}，不是 {expected}"
        return True, f"走的通道是 {expected}"

    return check


def channel_visual(evidence: TaskEvidence) -> tuple[bool, str]:
    """**追踪记录里通道 = 视觉通道**（至少一步走了视觉通道）。"""
    visual = [step for step in evidence.steps if step.channel == CHANNEL_VISUAL]
    if not visual:
        channels = "、".join(step.channel or "（空）" for step in evidence.steps) or "（无）"
        return False, f"没有一步走视觉通道（实际：{channels}）"
    ids = "、".join(step.step_id for step in visual)
    return True, f"走了视觉通道的步骤：{ids}"


def failure_type_coverage(evidence: TaskEvidence) -> tuple[bool, str]:
    """**UIA 侧失败类型 = 覆盖失败**（切视觉的理由必须是覆盖失败）。"""
    failed = [step for step in evidence.steps if step.failure_type]
    if not failed:
        return False, "没有任何一步在 UIA 阶段失败过（谈不上 UIA 侧失败类型）"
    wrong = [step for step in failed if step.failure_type != "coverage"]
    if wrong:
        detail = "、".join(
            f"{step.step_id} 的失败类型是 {step.failure_type}" for step in wrong
        )
        return False, f"UIA 侧的失败类型不是覆盖失败：{detail}"
    ids = "、".join(step.step_id for step in failed)
    return True, f"UIA 侧失败类型都是 coverage：{ids}"


def landing_within(limit_px: float) -> Any:
    """**落点与目标控件中心的偏差 ≤ 上限**。

    没有真值的步骤 → **判不了**（不是「偏差为 0」）。
    """

    def check(evidence: TaskEvidence) -> tuple[bool, str]:
        landed = [step for step in evidence.steps if step.landing_point is not None]
        if not landed:
            return False, "没有落点可比（一步都没落到东西上）"
        unknown = [step for step in landed if step.landing_error_px is None]
        if unknown:
            ids = "、".join(step.step_id for step in unknown)
            return False, f"落点偏差**判不了**（没有控件中心真值）：{ids}"
        worst = max(landed, key=lambda step: step.landing_error_px or 0.0)
        error = worst.landing_error_px or 0.0
        if error > limit_px:
            return (
                False,
                f"最大落点偏差 {error:.1f} px > {limit_px:.0f} px"
                f"（{worst.step_id}，落点 {worst.landing_point}）",
            )
        return True, f"落点偏差最大 {error:.1f} px ≤ {limit_px:.0f} px"

    return check


def capability_is(expected: str) -> Any:
    """**能力表里这一条记为某个值**（如 Windows 设置 = 不可用）。"""

    def check(evidence: TaskEvidence) -> tuple[bool, str]:
        actual = str(evidence.context.get("capability", "") or "")
        if not actual:
            return False, "能力表里没有这一条（读数是空的）"
        if actual != expected:
            return False, f"能力表记的是 {actual}，不是 {expected}"
        return True, f"能力表 = {actual}"

    return check


def context_value(key: str, expected: Any = None, *, nonempty: bool = False) -> Any:
    """**上下文里的某个读数**（如「读出的缩放值」「剪贴板是否恢复」）。

    ⚠️ **键不在上下文里 = 没测过** → 判「**判不了**」，
    不许当成「测了，结果不成立」。
    """

    def check(evidence: TaskEvidence) -> tuple[bool, str]:
        if key not in evidence.context:
            note = evidence.context.get(f"{key}_note")
            suffix = f"（{note}）" if note else ""
            return False, f"{key} **没测过**（上下文里没有这个读数）{suffix}"
        actual = evidence.context.get(key)
        if nonempty:
            if actual in (None, "", False):
                note = evidence.context.get(f"{key}_note")
                suffix = f"（{note}）" if note else ""
                return False, f"{key} 没有读到值{suffix}"
            return True, f"{key} = {actual}"
        if actual != expected:
            return False, f"{key} = {actual!r}，不是 {expected!r}"
        return True, f"{key} = {actual!r}"

    return check


def zoom_fresh_capture(evidence: TaskEvidence) -> tuple[bool, str]:
    """**放大是回源重抓**（不是把旧图放大）。"""
    frame = evidence.context.get("zoom_frame") or {}
    if not frame:
        return False, "没有放大的抓帧记录"
    if not frame.get("fresh"):
        return False, f"放大用的画面不是回源重抓：{frame}"
    region = evidence.context.get("zoom_region") or ()
    expected = (region[2] - region[0], region[3] - region[1]) if len(region) == 4 else None
    if expected and tuple(frame.get("size", ())) != tuple(expected):
        return (
            False,
            f"放大的画面尺寸 {frame.get('size')} ≠ 区域物理尺寸 {expected}"
            "（不是按物理分辨率重抓的）",
        )
    return True, f"放大按区域回源重抓：{frame.get('size')} 像素，来源 {frame.get('source')}"


def zoom_fullscreen_answer(evidence: TaskEvidence) -> tuple[bool, str]:
    """**放大后模型回答的仍是整屏坐标**（系统不加裁剪原点偏移）。"""
    landing = evidence.context.get("zoom_landing")
    region = evidence.context.get("zoom_region") or ()
    if landing is None:
        return False, "放大没有产出落点"
    if len(region) != 4:
        return False, "没有放大区域可比"
    x, y = landing
    inside = region[0] <= x <= region[2] and region[1] <= y <= region[3]
    if not inside:
        return (
            False,
            f"放大后的落点 {landing} 不在放大区域 {tuple(region)} 内 —— "
            "那不是整屏坐标（像是 crop 内坐标被当成了整屏）",
        )
    return True, f"放大后的落点 {landing} 落在放大区域 {tuple(region)} 内（整屏坐标）"


# ---------------------------------------------------------------------------
# 第二波（工单 M15）的条款判据
# ---------------------------------------------------------------------------


def gate_unlocked_before_unavailable(evidence: TaskEvidence) -> tuple[bool, str]:
    """**门控**：先尝试解开 + 退避重试，再判定不可用。

    读数取自**能力表的那次探测**（探测次数 / 解门控次数 / 第一次读有没有树）。
    第一次就读到树 → 这条路径本轮**没被触发**（如实说，不算成立）。
    """
    context = evidence.context
    attempts = context.get("gate_probe_attempts")
    unblocks = context.get("gate_unblock_attempts")
    first = context.get("gate_first_probe_has_tree")
    if attempts is None or unblocks is None or first is None:
        return False, "门控重试的读数**没测过**（上下文里没有这几项）"
    if first:
        return (
            False,
            f"第一次读就读到了树（探测 {attempts} 次、解门控 {unblocks} 次）—— "
            "「先解开 + 退避重试」这条路径本轮没被触发",
        )
    if unblocks < 1:
        return False, f"第一次读不到树却没有尝试解开（解门控 {unblocks} 次）"
    if attempts < 2:
        return False, f"第一次读不到树却没有退避重试（探测 {attempts} 次）"
    return True, (
        f"第一次读不到树 → 解门控 {unblocks} 次、退避重试共探测 {attempts} 次才判定"
    )


def capability_usable_or_cut_visual(evidence: TaskEvidence) -> tuple[bool, str]:
    """**重试成功记为可用；仍不可用则按覆盖失败切视觉**。

    两条分支**都算成立**（工单说的就是这两条），但**必须**与实际读数相符：
    能力表说「可用」时就不许同时说「切了视觉」。
    """
    context = evidence.context
    capability = str(context.get("capability", "") or "")
    if not capability:
        return False, "能力表里没有这一条（读数是空的）"
    cut_visual = bool(context.get("cut_to_visual", False))
    if capability == "usable":
        if cut_visual:
            return False, "能力表记的是「可用」，却又切了视觉（两说矛盾）"
        return True, "重试成功 → 能力表记为可用"
    if capability == "unavailable":
        if not cut_visual:
            return (
                False,
                "能力表记的是「不可用」，但没有按覆盖失败切视觉"
                f"（失败类型读数：{context.get('failure_type', '（没有）')}）",
            )
        return True, "仍不可用 → 能力表记为不可用并按覆盖失败切视觉"
    return (
        False,
        f"能力表记的是 {capability!r}，既不是「可用」也不是「不可用」"
        "（这两个取值之外的状态说不了这两条分支）",
    )


def window_title_matches_picked(evidence: TaskEvidence) -> tuple[bool, str]:
    """**窗口标题 == 运行时挑中的那个联系人**（真值不写死，从上下文取）。"""
    context = evidence.context
    picked = context.get("picked_contact")
    title = context.get("window_title")
    if picked is None or title is None:
        return False, "窗口标题**没测过**（上下文里没有挑中的联系人或窗口标题）"
    if title != picked:
        return False, f"窗口标题是 {title!r}，不是挑中的联系人 {picked!r}"
    return True, f"窗口标题 = {title!r}（== 挑中的联系人）"


def modal_handoff_ok(evidence: TaskEvidence) -> tuple[bool, str]:
    """**模态弹窗 → 零动作下发 + 交人 + 交接产物齐**（工单 M15 的 WX-3）。"""
    context = evidence.context
    if "modal_detected" not in context:
        return False, "模态弹窗的读数**没测过**（上下文里没有这一项）"
    if not context.get("modal_detected"):
        return False, "本轮没有检测到模态弹窗（这条无从验证）"
    dispatched = context.get("actions_dispatched", 0)
    if dispatched:
        return False, f"检测到模态弹窗后仍然下发了 {dispatched} 个动作（弹窗上不能有像素被点）"
    handoff = context.get("handoff") or {}
    required = ("window_title", "last_action", "sent_points", "executed_actions")
    missing = [name for name in required if name not in handoff]
    if missing:
        return False, f"交接产物缺这几项：{'、'.join(missing)}"
    if not handoff.get("window_title"):
        return False, "交接产物里的「当前窗口标题」是空的"
    return True, (
        f"检测到模态弹窗 → 零动作下发，交接产物四项齐全"
        f"（当前窗口标题 {handoff.get('window_title')!r}）"
    )


def page_title_is(expected: str) -> Any:
    """**页面标题 == 目标标题**（结构断言：导航之后页面自己说的标题）。"""

    def check(evidence: TaskEvidence) -> tuple[bool, str]:
        if "page_title" not in evidence.context:
            return False, "page_title **没测过**（上下文里没有这个读数）"
        actual = evidence.context.get("page_title")
        if actual != expected:
            return False, f"页面标题是 {actual!r}，不是 {expected!r}"
        return True, f"页面标题 = {actual!r}"

    return check


def structure_channel_preferred(evidence: TaskEvidence) -> tuple[bool, str]:
    """**结构与视觉都可用时选结构那条，并把理由记进追踪记录**。"""
    context = evidence.context
    reason = str(context.get("channel_choice_reason", "") or "")
    if not reason:
        return False, "通道选择的理由**没测过**（上下文里没有这一项）"
    channel = str(context.get("channel", "") or "")
    if channel != "browser":
        return False, f"走的是 {channel or '（空）'} 通道，不是结构通道"
    if "结构" not in reason:
        return False, f"选择理由里没有说为什么选结构通道：{reason!r}"
    return True, f"走了结构通道，选择理由：{reason}"


def cross_stack_semantics_stable(evidence: TaskEvidence) -> tuple[bool, str]:
    """**坐标与动作语义跨技术栈不变**（同一个动作协议 + 物理表面空间）。"""
    context = evidence.context
    if "action_semantics" not in context:
        return False, "动作语义的读数**没测过**（上下文里没有这一项）"
    semantics = context.get("action_semantics")
    expected = ["click", "type"]
    if list(semantics) != expected:
        return False, f"动作语义是 {list(semantics)}，不是 {expected}"
    spaces = set(context.get("coordinate_spaces") or ())
    if spaces != {"physical-surface"}:
        return False, f"落点的坐标空间是 {sorted(spaces) or '（空）'}，不是只有物理表面空间"
    return True, f"动作语义 {list(semantics)} 跨技术栈不变，落点都在物理表面空间"


def input_channel_recorded(evidence: TaskEvidence) -> tuple[bool, str]:
    """**输入通道的处置有据可查**（退化时记下退化并退到另一条通道）。

    没发生退化时**如实记「未发生退化」**—— 这一条验的是**记录**，
    退化分支本身由单测覆盖。
    """
    context = evidence.context
    for key in ("input_channel", "input_degraded", "input_degradation_note"):
        if key not in context:
            return False, f"{key} **没测过**（上下文里没有这个读数）"
    channel = context.get("input_channel")
    degraded = bool(context.get("input_degraded"))
    note = str(context.get("input_degradation_note") or "")
    if not note:
        return False, "输入通道的处置没有记下理由"
    if degraded and channel == "desktop":
        return False, f"记着退化发生，但最后仍走的是桌面注入通道：{note}"
    if degraded:
        return True, f"输入通道退化 → 退到 {channel} 通道（{note}）"
    return True, f"输入走的是 {channel} 通道，本轮**未发生退化**（{note}）"


def context_matches_picked(key: str, picked_key: str = "picked_provider") -> Any:
    """**任务级读数 == 运行时挑中的那个条目**（真值不写死）。

    工单不钉具体条目（``docs/target-tasks.md`` §7.2）→ 条款的期望值
    在跑的时候才知道 → 从上下文里取。
    """

    def check(evidence: TaskEvidence) -> tuple[bool, str]:
        context = evidence.context
        picked = context.get(picked_key)
        if picked is None:
            return False, f"{picked_key} **没测过**（上下文里没有这个读数）"
        actual = context.get(key)
        if actual is None:
            return False, f"{key} **没测过**（上下文里没有这个读数）"
        if actual != picked:
            return False, f"{key} 是 {actual!r}，不是挑中的那个 {picked!r}"
        return True, f"{key} = {actual!r}（== 挑中的那个）"

    return check


def verification_source_is_structure(evidence: TaskEvidence) -> tuple[bool, str]:
    """**核对用的是可访问性树 / 文本比对**，不是「画面变了」。"""
    source = evidence.context.get("verification_source")
    if source is None:
        return False, "核对手段的读数**没测过**（上下文里没有这一项）"
    if source not in ("uia", "text"):
        return False, f"核对用的是 {source!r}，不是可访问性树或文本比对"
    return True, f"核对用的是 {source}"


def failure_types_separated(evidence: TaskEvidence) -> tuple[bool, str]:
    """**三类失败分开统计**（定位 / 决策 / 执行），且失败能定位到哪一步。"""
    if not evidence.steps:
        return False, "一步都没跑（没有失败可定位）"
    failed = [step for step in evidence.steps if not step.ok]
    for step in evidence.steps:
        missing = [
            name
            for name in ("locate", "decision", "execute")
            if name not in step.failures
        ]
        if missing:
            return False, f"{step.step_id} 的三类失败字段缺 {'、'.join(missing)}"
    if len(failed) > 1:
        return (
            False,
            "有 "
            + "、".join(step.step_id for step in failed)
            + " 多步失败：失败定位不唯一",
        )
    if failed:
        step = failed[0]
        codes = {
            name: code for name, code in step.failures.items() if code
        }
        return (
            False,
            f"失败定位到 {step.step_id}（{codes or '（没有错误码）'}）—— "
            "这一步失败 → 整张票不算通过",
        )
    return True, f"{len(evidence.steps)} 步各自记着三类失败，没有失败的步骤"


def coordinates_stayed_in_window(evidence: TaskEvidence) -> tuple[bool, str]:
    """**全程没有把坐标挪到别的窗口**（被遮挡时先置前再操作）。"""
    context = evidence.context
    if "coordinate_windows" not in context:
        return False, "落点所属窗口的读数**没测过**（上下文里没有这一项）"
    entries = list(context.get("coordinate_windows") or ())
    mismatched = [entry for entry in entries if not entry.get("matched", False)]
    if mismatched:
        detail = "、".join(
            f"{entry.get('step_id')}: {entry.get('actual')} ≠ {entry.get('expected')}"
            for entry in mismatched
        )
        return False, f"落点落在了别的窗口上：{detail}"
    if not context.get("window_pinned", False):
        return False, "目标窗口被遮挡时**没有先置前**（window_pinned = 假）"
    return True, (
        f"{len(entries)} 个落点都在目标窗口里；目标窗口被遮挡时先置前了"
    )


def channel_and_failure_readable(evidence: TaskEvidence) -> tuple[bool, str]:
    """**追踪记录里能读到通道与失败类型**（每一步都要有）。

    ⚠️ **失败类型只在真失败时才有值**（没失败就是空串 —— 那是「没有失败」
    不是「读不出来」）；因此这里查的是：通道**读得到**，且**三类失败字段齐全**。
    """
    if not evidence.steps:
        return False, "一步都没跑"
    missing = [step.step_id for step in evidence.steps if not step.channel]
    if missing:
        return False, f"这些步骤的通道读不出来：{'、'.join(missing)}"
    incomplete = [
        step.step_id
        for step in evidence.steps
        if not set(("locate", "decision", "execute")).issubset(step.failures)
    ]
    if incomplete:
        return False, f"这些步骤的三类失败字段不齐全：{'、'.join(incomplete)}"
    return True, (
        "、".join(
            f"{step.step_id}: 通道={step.channel}"
            f"（失败类型 {step.failure_type or '（无失败）'}）"
            for step in evidence.steps
        )
    )

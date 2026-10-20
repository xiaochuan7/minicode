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

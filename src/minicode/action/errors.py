"""动作协议 v1 的错误码。

错误码是协议的一部分：**同一件事只有一个码**，上层按码分流，不解析错误文本。

**失败分类**（工单 M9 AC 7）：错误码 → **失败类型** → **能不能切视觉**。

* 映射只有一份：:data:`FAILURE_TYPE_BY_CODE`（错误码 → 失败类型）；
* 红线只有一处：:func:`allows_visual_fallback`（**只有覆盖失败允许切视觉**，
  架构决策备忘 §2.5 ①）。

失败类型（**定位阶段**那一维；工单 AC 7 点的三类之外另加两类，
理由见 `docs/reviews/M9.md` §8）：

===========================  ========================================  ============
失败类型                      含义                                      允许切视觉
===========================  ========================================  ============
``coverage``（覆盖失败）       通道**产不出任何候选**                      ✅ 是
``selection``（选择失败）     候选里有，但**选不出**唯一选中项            ❌ 否
``execution``（执行失败）     定位到了，**动作执行不了**                  ❌ 否
``channel``（通道级失败）     这一层**整体不可用**（查询超时 / 被拒启用）  ❌ 否
``decision``（决策失败）       **决策层**的产出被拒（程序被拒 / 决策失败）  ❌ 否
===========================  ========================================  ============

⚠️ **别和另一套「三类失败」混**（``CONTEXT.md`` 的词条）：
那一套是**埋点轴**（定位失败 / 决策失败 / 执行失败，记在追踪记录的
``failures`` 里），本模块这套是**定位阶段的分类轴**（失败类型）。
两套正交，**不許混着说**。
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum

__all__ = [
    "ErrorCode",
    "ActionError",
    "FailureType",
    "FAILURE_TYPE_BY_CODE",
    "failure_type_of",
    "allows_visual_fallback",
]


class ErrorCode(StrEnum):
    """动作协议 v1 的结果错误码。"""

    #: 成功。
    OK = "ok"

    # -- 定位失败（三类失败里的「定位失败」）--
    #: 目标描述在控件树里找不到任何候选。**覆盖失败** → 允许切视觉。
    TARGET_NOT_FOUND = "target_not_found"
    #: 候选不唯一 —— 唯一性拒绝：拒绝执行并回报候选集。**选择失败**。
    AMBIGUOUS_TARGET = "ambiguous_target"
    #: 候选数超过上限（默认 8）：直接拒绝，**不调模型**，回报候选集。**选择失败**。
    TOO_MANY_CANDIDATES = "too_many_candidates"
    #: 控件树读取本身失败（**通道产不出候选**：0 节点 / 门控解不开 / 没有 provider）。
    #: **覆盖失败** —— 通道根本产不出候选（架构决策备忘 §2.5 ① 的「无 provider /
    #: 树被门控」两条）。⚠️ 「0 节点」是**原因**，「覆盖失败」是**失败类型**——
    #: 两者不是一回事，别混着说。
    LOCATE_FAILED = "locate_failed"
    #: 执行前重读树时找不到原来的那个控件了：宁可不动作，也不点相似的另一个。
    #: **选择失败**。
    RELOCATE_FAILED = "relocate_failed"
    #: **查询超时**（约 5 秒阈值）。**通道级失败** —— 不是覆盖失败：
    #: 我们并不知道目标在不在树里，**不许**因此切视觉。
    QUERY_TIMEOUT = "query_timeout"
    #: **候选尺寸异常**：名字对得上，但控件框读不成合法矩形（全 0 / 零面积）。
    #: **选择失败** —— 候选在那儿，只是定不下来（架构决策备忘 §2.5 ①）。
    INVALID_GEOMETRY = "invalid_geometry"
    #: **名称匹配不上**：条件查询找到了候选，却没有一个与目标描述对得上。
    #: **选择失败** —— 候选集非空但选不出（架构决策备忘 §2.5 ①）。
    NAME_MISMATCH = "name_mismatch"

    # -- 定位阶段的另外三种「不是覆盖失败也不是选择失败」--
    #: **通道被拒绝启用**（硬闸门拦下，如脱敏未上线）。
    #: 发生在**定位阶段**（动作压根没下发）→ 归「定位失败」；
    #: 但它**既不是覆盖失败也不是选择失败**：是**策略拒绝**，不许切视觉。
    CHANNEL_DISABLED = "channel_disabled"
    #: 调用方给的**目标描述**没法用（空 / 只有空白）：不是找不到，是根本没得找。
    INVALID_TARGET = "invalid_target"
    #: **视觉通道不可用**（工单 M5 的硬闸门）：标定失败，或存在使物理落点
    #: 分歧 > 标定容差的替代标度。UIA 通道出现覆盖失败、而切视觉这条路
    #: 又被闸门拦下时，**报明确错误码**（零动作下发）。
    VISUAL_CHANNEL_UNAVAILABLE = "visual_channel_unavailable"

    # -- 决策失败（本单没有模型参与，恒不出现）--
    #: 决策失败。决策层（L3）不在动作执行缝内，本单用不上；
    #: 留给决策层工单（M10/M11）按协议 v1 启用。
    DECIDE_FAILED = "decide_failed"

    # -- 执行失败（三类失败里的「执行失败」）--
    #: 落点越出虚拟桌面超过越界容差，被桌面裁切。**不夹取、不挪屏。**
    CLIPPED_BY_DESKTOP = "clipped_by_desktop"
    #: 落点复验没过：动作后重读控件树，落点与控件框中心的距离超过容差，
    #: 或者复验时根本找不到目标（不能声称闭环打通）。
    LANDING_MISMATCH = "landing_mismatch"
    #: 注入被拦下（SendInput 返回值 != 注入条数，典型是 UIPI 完整性级别拦截）。
    INJECTION_BLOCKED = "injection_blocked"
    #: 光标回读落在哨兵值 (−32000,−32000)。
    CURSOR_SENTINEL = "cursor_sentinel"
    #: 光标回读与落点不一致。
    CURSOR_MISMATCH = "cursor_mismatch"
    #: 动作执行本身失败。
    EXECUTE_FAILED = "execute_failed"

    # -- 抓帧 --
    #: 所有捕获源都只拿到 0×0 画面。
    CAPTURE_EMPTY = "capture_empty"

    # -- 协议 --
    #: 该动作种类本单不实现（放大 / 读控件树 / 执行代码 / 请求人工）。
    ACTION_NOT_IMPLEMENTED = "action_not_implemented"

    # -- 动作程序（工单 M10）--
    #: 动作程序在**执行前**被拒：它要做的事超出受控执行环境允许的范围
    #: （任意系统调用 / 任意文件写入 / 不许暴露的接口名）。
    #: **零动作下发** —— 被拒的程序一个动作都没做。
    PROGRAM_REJECTED = "program_rejected"

    # -- 校验（工单 M11）--
    #: **期望断言失败**：动作做完了，但界面**没有**变成期望的样子。
    #: 归「**执行失败**」—— 动作在「执行+校验」这一段里没达成目标
    #: （架构决策备忘 §2.5 ③ 的三类失败之一）。
    #:
    #: ⚠️ 「界面无变化」**不是**这个错：没有期望时的默认校验只给**信号**
    #: （「无变化 ≠ 失败」—— 对已置位控件再点一次就属于那种）。
    EXPECTATION_FAILED = "expectation_failed"


class ActionError(Exception):
    """带错误码的动作异常。"""

    def __init__(self, code: ErrorCode, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


class FailureType(StrEnum):
    """**失败类型**（工单 M9 AC 7）：定位阶段的失败分类。

    它是「切视觉」这条红线的判据维度 —— **标识不同、互不混淆**。
    """

    #: 覆盖失败：通道产不出任何候选。**允许切视觉**。
    COVERAGE = "coverage"
    #: 选择失败：候选里有，但选不出唯一选中项。**禁止切视觉**。
    SELECTION = "selection"
    #: 执行失败：定位到了，动作执行不了。**禁止切视觉**。
    EXECUTION = "execution"
    #: 通道级失败：这一层整体不可用（查询超时 / 通道被拒绝启用）。**禁止切视觉**。
    CHANNEL = "channel"
    #: 决策失败：决策层的产出被拒（动作程序被受控环境拒绝 / 决策失败）。**禁止切视觉**。
    DECISION = "decision"


#: **错误码 → 失败类型**（唯一一处映射）。
#:
#: 上层按码分流，不解析错误文本；这张表是「哪一类失败」的唯一真值来源。
FAILURE_TYPE_BY_CODE: Mapping[ErrorCode, FailureType] = {
    # -- 覆盖失败：通道产不出候选 → 允许切视觉 ---------------------------
    ErrorCode.TARGET_NOT_FOUND: FailureType.COVERAGE,
    ErrorCode.LOCATE_FAILED: FailureType.COVERAGE,
    # -- 选择失败：候选里有但选不出 → 禁止切视觉 -------------------------
    ErrorCode.AMBIGUOUS_TARGET: FailureType.SELECTION,
    ErrorCode.TOO_MANY_CANDIDATES: FailureType.SELECTION,
    ErrorCode.RELOCATE_FAILED: FailureType.SELECTION,
    ErrorCode.INVALID_GEOMETRY: FailureType.SELECTION,
    ErrorCode.NAME_MISMATCH: FailureType.SELECTION,
    # -- 执行失败：定位到了但动作执行不了 → 禁止切视觉 -------------------
    ErrorCode.EXECUTE_FAILED: FailureType.EXECUTION,
    ErrorCode.CLIPPED_BY_DESKTOP: FailureType.EXECUTION,
    ErrorCode.CAPTURE_EMPTY: FailureType.EXECUTION,
    ErrorCode.INJECTION_BLOCKED: FailureType.EXECUTION,
    ErrorCode.CURSOR_MISMATCH: FailureType.EXECUTION,
    ErrorCode.CURSOR_SENTINEL: FailureType.EXECUTION,
    ErrorCode.LANDING_MISMATCH: FailureType.EXECUTION,
    #: **期望断言失败**（工单 M11）：动作做了但界面没变成期望的样子
    #: → 归「执行失败」（它在「执行+校验」这一段里没达成目标）。
    ErrorCode.EXPECTATION_FAILED: FailureType.EXECUTION,
    # -- 通道级失败：这一层整体不可用 → 禁止切视觉 -----------------------
    ErrorCode.QUERY_TIMEOUT: FailureType.CHANNEL,
    ErrorCode.CHANNEL_DISABLED: FailureType.CHANNEL,
    ErrorCode.INVALID_TARGET: FailureType.CHANNEL,
    ErrorCode.VISUAL_CHANNEL_UNAVAILABLE: FailureType.CHANNEL,
    ErrorCode.ACTION_NOT_IMPLEMENTED: FailureType.CHANNEL,
    # -- 决策失败：决策层的产出被拒 → 禁止切视觉 -------------------------
    ErrorCode.DECIDE_FAILED: FailureType.DECISION,
    ErrorCode.PROGRAM_REJECTED: FailureType.DECISION,
}


def failure_type_of(code: ErrorCode) -> FailureType:
    """这个错误码属于**哪一类失败**（未知码归「通道级」，不静默放过）。"""
    return FAILURE_TYPE_BY_CODE.get(code, FailureType.CHANNEL)


def allows_visual_fallback(failure_type: FailureType | str) -> bool:
    """**切视觉的红线**（架构决策备忘 §2.5 ①）：**只有覆盖失败**允许切视觉。

    候选不唯一、被遮挡、尺寸异常、查询超时、名称匹配不上 ——
    这些一律禁止切视觉，必须走校验与纠错层的错误处理。
    """
    if isinstance(failure_type, str):
        failure_type = FailureType(failure_type)
    return failure_type is FailureType.COVERAGE

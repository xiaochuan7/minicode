"""动作协议 v1 的错误码。

错误码是协议的一部分：**同一件事只有一个码**，上层按码分流，不解析错误文本。
"""

from __future__ import annotations

from enum import StrEnum

__all__ = ["ErrorCode", "ActionError"]


class ErrorCode(StrEnum):
    """动作协议 v1 的结果错误码。"""

    #: 成功。
    OK = "ok"

    # -- 定位失败（三类失败里的「定位失败」）--
    #: 目标描述在控件树里找不到任何候选。
    TARGET_NOT_FOUND = "target_not_found"
    #: 候选不唯一 —— 唯一性拒绝：拒绝执行并回报候选集。
    AMBIGUOUS_TARGET = "ambiguous_target"
    #: 候选数超过上限（默认 8）：直接拒绝，**不调模型**，回报候选集。
    TOO_MANY_CANDIDATES = "too_many_candidates"
    #: 控件树读取本身失败（超时、通道不可用）。
    LOCATE_FAILED = "locate_failed"
    #: 执行前重读树时找不到原来的那个控件了：宁可不动作，也不点相似的另一个。
    RELOCATE_FAILED = "relocate_failed"

    # -- 定位阶段的另外两种「不是覆盖失败也不是选择失败」--
    #: **通道被拒绝启用**（硬闸门拦下，如脱敏未上线）。
    #: 发生在**定位阶段**（动作压根没下发）→ 归「定位失败」；
    #: 但它**既不是覆盖失败也不是选择失败**：是**策略拒绝**，不许切视觉。
    CHANNEL_DISABLED = "channel_disabled"
    #: 调用方给的**目标描述**没法用（空 / 只有空白）：不是找不到，是根本没得找。
    INVALID_TARGET = "invalid_target"

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


class ActionError(Exception):
    """带错误码的动作异常。"""

    def __init__(self, code: ErrorCode, message: str):
        super().__init__(message)
        self.code = code
        self.message = message

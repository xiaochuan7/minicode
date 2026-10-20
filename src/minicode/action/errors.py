"""动作协议 v1 的错误码。

错误码是协议的一部分：**同一件事只有一个码**，上层按码分流，不解析错误文本。
"""

from __future__ import annotations

from enum import Enum

__all__ = ["ErrorCode", "ActionError"]


class ErrorCode(str, Enum):
    """动作协议 v1 的结果错误码。"""

    #: 成功。
    OK = "ok"

    # -- 定位失败（三类失败里的「定位失败」）--
    #: 目标描述在控件树里找不到任何候选。
    TARGET_NOT_FOUND = "target_not_found"
    #: 候选不唯一 —— 唯一性拒绝：拒绝执行并回报候选集。
    AMBIGUOUS_TARGET = "ambiguous_target"
    #: 控件树读取本身失败（超时、通道不可用）。
    LOCATE_FAILED = "locate_failed"

    # -- 决策失败（本单没有模型参与，恒不出现）--
    DECIDE_FAILED = "decide_failed"

    # -- 执行失败（三类失败里的「执行失败」）--
    #: 落点越出虚拟桌面超过越界容差，被桌面裁切。**不夹取、不挪屏。**
    CLIPPED_BY_DESKTOP = "clipped_by_desktop"
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

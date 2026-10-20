"""UIA 通道的失败分类（工单 M3）。

**三类失败分开报**（架构决策备忘 §2.5 ③），且都归「定位失败」：

===========================  ==========================  =========================
失败类别                     含义                        动作协议错误码
===========================  ==========================  =========================
**通道级失败**（channel）    树读不出来：0 节点 / 超时 / 门控解不开   ``LOCATE_FAILED``
**覆盖失败**（coverage）     树能读，但产不出这个目标的候选          ``TARGET_NOT_FOUND``
**选择失败**（selection）    候选里有，却选不出唯一选中项            ``AMBIGUOUS_TARGET``
===========================  ==========================  =========================

「决策失败」恒为空（决策层不在动作执行缝内）；「执行失败」只留给**动作下发**失败。

**切视觉只认覆盖失败**——但本单一律不切视觉（架构决策备忘 §2.5 ①）：
候选不唯一、被门控、查询超时，统统走这里的错误处理。

本模块只放**真的会被抛**的异常；定位流程里的三类失败用
:class:`~minicode.action.ports.LocateResult` 的错误码表达（不抛异常）。
"""

from __future__ import annotations

from minicode.action.errors import ErrorCode

__all__ = [
    "UiaError",
    "UiaQueryTimeout",
    "UnconditionalQueryError",
]


class UiaError(Exception):
    """UIA 通道的异常底座。带一个动作协议错误码（上层按码分流，不解析文本）。"""

    code: ErrorCode = ErrorCode.LOCATE_FAILED

    def __init__(self, message: str, code: ErrorCode | None = None):
        super().__init__(message)
        self.message = message
        if code is not None:
            self.code = code


class UiaQueryTimeout(UiaError):
    """查询超时（通道级失败；阈值约 5 秒）。"""

    code = ErrorCode.LOCATE_FAILED


class UnconditionalQueryError(UiaError):
    """无条件全树查询 —— 直接拒绝。

    架构决策备忘 §2.6 **C-09**：动作路径只做**定点查询**，禁止全树 dump
    （全桌面遍历约 1000 个元素要 ≥8 秒）。

    ⚠️ 这条禁令只管**动作路径**（:class:`~minicode.uia.ports.UiaQueryPort`）：
    **能力探测**（:class:`~minicode.uia.ports.CapabilityProbePort`）按
    「递归子窗口 + 看 UIA ClassName」的判定姿势**必须**走一遍应用自己的窗口树，
    它每个应用只跑一次，且不参与动作路径。
    """

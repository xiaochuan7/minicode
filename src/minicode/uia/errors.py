"""UIA 通道的失败分类（工单 M3 / M9）。

**失败类型**（工单 M9 AC 7；映射只有一份：
:func:`minicode.action.errors.failure_type_of`）：

===========================  ==========================  ====================
失败类型                      含义                        动作协议错误码
===========================  ==========================  ====================
**覆盖失败**（coverage）      通道**产不出任何候选**         ``TARGET_NOT_FOUND``
                             （目标不在树里 / 0 节点）    ``LOCATE_FAILED``
**选择失败**（selection）     候选里有，却选不出唯一选中项   ``AMBIGUOUS_TARGET``
                             （含尺寸异常 / 名称匹配不上）   ``INVALID_GEOMETRY``
                                                        ``NAME_MISMATCH``
                                                        ``TOO_MANY_CANDIDATES``
                                                        ``RELOCATE_FAILED``
**通道级失败**（channel）     这一层整体不可用（超时 / 被拒） ``QUERY_TIMEOUT``
                                                        ``CHANNEL_DISABLED``
===========================  ==========================  ====================

⚠️ **「0 节点」是原因，「覆盖失败」是失败类型** —— 两者不是一回事：
0 节点 = 树读不出东西（C-09：0 节点必须被识别成「通道不可用」，**不是**「目标不存在」），
它归到「覆盖失败」是因为**这一层压根产不出候选**（架构决策备忘 §2.5 ① 的「无 provider /
树被门控」两条）→ **允许切视觉**。
而「树读取超时」是**通道级失败** → **禁止切视觉**（同一条规则里的禁止清单）。

「决策失败」恒为空（决策层不在动作执行缝内）；「执行失败」只留给**动作下发**失败。

**切视觉只认覆盖失败**（架构决策备忘 §2.5 ①）：候选不唯一、被遮挡、尺寸异常、
查询超时、名称匹配不上 —— 统统走这里的错误处理，**零视觉通道调用**。

本模块只放**真的会被抛**的异常；定位流程里的失败用
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
    """查询超时（**通道级失败**；阈值约 5 秒）。

    ⚠️ 它**不是**覆盖失败：我们并不知道目标在不在树里（架构决策备忘
    §2.5 ① 把「树读取超时」列进禁止切视觉的那一串）。
    """

    code = ErrorCode.QUERY_TIMEOUT


class UnconditionalQueryError(UiaError):
    """无条件全树查询 —— 直接拒绝。

    架构决策备忘 §2.6 **C-09**：动作路径只做**定点查询**，禁止全树 dump
    （全桌面遍历约 1000 个元素要 ≥8 秒）。

    ⚠️ 这条禁令只管**动作路径**（:class:`~minicode.uia.ports.UiaQueryPort`）：
    **能力探测**（:class:`~minicode.uia.ports.CapabilityProbePort`）按
    「递归子窗口 + 看 UIA ClassName」的判定姿势**必须**走一遍应用自己的窗口树，
    它每个应用只跑一次，且不参与动作路径。
    """

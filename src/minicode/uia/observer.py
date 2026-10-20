"""**定点观察**：给校验层用的那一路「可访问性树」观测（工单 M11）。

校验层要判「状态 / 存在性 / 矩形变化」，就得**再读一次树** —— 但它**不许全树 dump**
（架构决策备忘 §2.6 C-09：全桌面遍历约 1000 个元素要 ≥8 秒）。

因此这一路也走**定点查询**（带名字条件），复用动作路径同一个查询端口：
* 端口形状不变（还是 :class:`~minicode.uia.ports.UiaQueryPort`）；
* 超时照旧抛 :class:`~minicode.uia.errors.UiaQueryTimeout`（校验层据此判「判不了」）。

⚠️ **观察端口与定位端口不是一回事**：定位端口回答「选中项 + 落点」，
观察端口只回答「这个控件现在什么样」。
"""

from __future__ import annotations

from minicode.action.ports import ControlCandidate, WindowAnchor

from .channel import to_candidate
from .errors import UiaQueryTimeout
from .ports import UiaFindRequest, UiaQueryPort

__all__ = ["UiaStateObserver"]


class UiaStateObserver:
    """按名字**定点**观察控件的状态（校验层那一路）。"""

    def __init__(self, query: UiaQueryPort, anchor: WindowAnchor | None = None):
        self.query = query
        self.anchor = anchor or WindowAnchor(hwnd=0)
        #: 观察了几次（供埋点与断言）。
        self.calls = 0

    def observe(self, name: str) -> tuple[ControlCandidate, ...]:
        """按名字查控件；查不到返回空元组。读树失败**抛异常**（上层判「判不了」）。

        ⚠️ **0 节点 = 通道不可用**（架构决策备忘 §2.6 C-09：
        「0 节点必须被识别成『通道不可用』，不得当成『目标不存在』」）——
        这种情况**抛异常**，校验层据此判「判不了」，而不是「没找到」。
        """
        self.calls += 1
        if not self.query.has_nodes(self.anchor):
            raise UiaQueryTimeout(
                "控件树是空的（0 节点 = 通道不可用，C-09）：这一路判不了"
            )
        result = self.query.find(
            UiaFindRequest(anchor=self.anchor, name=name or None)
        )
        return tuple(to_candidate(element) for element in result.elements)

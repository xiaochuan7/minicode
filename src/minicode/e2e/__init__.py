"""**端到端评测**（工单 M14）。

这一层把「任务清单」变成「可判定的结果」：

* :mod:`minicode.e2e.tasks` —— 工单的形状与不变量；
* :mod:`minicode.e2e.runner` —— **判定口径**（通过 / 失败 / 跳过 / 待人工确认）；
* :mod:`minicode.e2e.checks` —— 验收条款的判据；
* :mod:`minicode.e2e.wave1` —— 第一波的六张票；
* :mod:`minicode.e2e.report` —— 报告；
* :mod:`minicode.e2e.real` —— 真机执行器。
"""

from __future__ import annotations

__all__ = ["__doc__"]

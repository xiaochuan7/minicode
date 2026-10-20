"""**端到端评测**（工单 M14 + M15）。

这一层把「任务清单」变成「可判定的结果」：

* :mod:`minicode.e2e.tasks` —— 工单的形状与不变量（**每一步至少一条结构断言**）；
* :mod:`minicode.e2e.runner` —— **判定口径**（通过 / 失败 / 跳过 / 待人工确认）；
* :mod:`minicode.e2e.checks` —— 验收条款的判据；
* :mod:`minicode.e2e.wave1` —— 第一波的六张票（Windows 设置 + Chrome）；
* :mod:`minicode.e2e.wave2` —— 第二波的九张票（微信 + DSH/Tabbit + cc-switch）；
* :mod:`minicode.e2e.report` —— 报告；
* :mod:`minicode.e2e.real` —— 第一波的真机执行器；
* :mod:`minicode.e2e.apps` —— 第二波的真机执行器（微信 / DSH·Tabbit / cc-switch）
  与**本地验证页**；
* :mod:`minicode.e2e.modal` —— **模态弹窗的检测**；
* :mod:`minicode.e2e.visual` —— **视觉通道的接线**（两波共用这一处）。
"""

from __future__ import annotations

__all__ = ["__doc__"]

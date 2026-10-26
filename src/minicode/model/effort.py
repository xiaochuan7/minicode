"""思考档位（effort）。

五档来自 MiniMax 官方 API 文档：**low / medium / high / xhigh / max**，
且**缺省即 max**（官方原文 *"When omitted, the default is max"*）。

「缺省即最高档」是本项目要消掉的坑：
所以**调用入口必须显式给档位**，省略即报错，不允许悄悄按最高档跑。
"""

from __future__ import annotations

from enum import StrEnum

__all__ = ["Effort", "EFFORT_VALUES"]


class Effort(StrEnum):
    """思考预算档位（五档显式枚举）。"""

    #: 低 —— 动作步用这一档。
    LOW = "low"
    #: 中 —— 动作步可用，但更慢。
    MEDIUM = "medium"
    #: 高 —— 规划 / 反思步用。
    HIGH = "high"
    #: 极高 —— 规划 / 反思步用。
    XHIGH = "xhigh"
    #: 最高 —— 服务端的缺省值。本项目**禁止**靠省略档位拿到这一档。
    MAX = "max"


#: 五档的取值顺序（低 → 最高）。
EFFORT_VALUES: tuple[str, ...] = tuple(effort.value for effort in Effort)

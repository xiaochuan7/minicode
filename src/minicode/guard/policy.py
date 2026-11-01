"""**护栏策略**（W2）：一次真机运行的全部护栏参数，只有一个装载入口。

策略是**纯数据**（冻结 dataclass）—— 判定逻辑在各护栏模块里，
策略只回答「阈值是多少、开关开没开」。这样单测可以逐字段构造策略，
真机接线从 CLI 参数 / 环境变量装载，两边用的是同一份形状。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from .levels import AuthLevel

__all__ = ["GuardBudget", "GuardPolicy", "ESTOP_VKEYS", "load_policy"]


@dataclass(frozen=True)
class GuardBudget:
    """**预算**（护栏 4）：默认 ≤30 步、≤5 分钟、≤50 次模型调用。

    任一维度超限 → 停止并上报（``budget_exceeded``）。
    """

    #: 最大步数（一次缝调用 = 一步）。
    max_steps: int = 30
    #: 最大墙钟时长（秒）。
    max_seconds: float = 300.0
    #: 最大模型调用次数（从追踪记录的 ``model_calls`` 累加）。
    max_model_calls: int = 50


#: 急停热键（护栏 1）：Ctrl + Alt + Shift + K。
#: Win32 虚拟键码：VK_CONTROL=0x11、VK_MENU=0x12、VK_SHIFT=0x10、K=0x4B。
ESTOP_VKEYS: tuple[int, ...] = (0x11, 0x12, 0x10, 0x4B)


@dataclass(frozen=True)
class GuardPolicy:
    """一次真机运行的护栏策略（默认 L0 + 默认预算）。"""

    #: 授权级别（默认 L0 只读 —— fail-closed）。
    level: AuthLevel = AuthLevel.L0
    #: **L1 白名单**：窗口类名 / 标题的子串集合（大小写不敏感）。
    #: 空集合 = L1 下没有可操作窗口（fail-closed：想用 L1 就得显式给窗口）。
    window_whitelist: tuple[str, ...] = field(default_factory=tuple)
    #: 预算（步数 / 时长 / 模型调用）。
    budget: GuardBudget = field(default_factory=GuardBudget)
    #: **连续失败熔断**（护栏 5）：同一目标连续失败达到该次数即停（默认 2）。
    fuse_threshold: int = 2
    #: **空闲让位**（护栏 6）：检测到用户动键鼠就暂停。
    idle_pause_enabled: bool = True
    #: 空闲让位的判定窗：用户最后输入距今小于该毫秒数就视为「正在用」。
    idle_active_ms: int = 1500
    #: **急停热键**（护栏 1）：watchdog 是否启用。
    estop_enabled: bool = True
    #: 急停热键的虚拟键码组合（默认 Ctrl+Alt+Shift+K）。
    estop_vkeys: tuple[int, ...] = ESTOP_VKEYS
    #: **L3 单次上限**（分钟）：L3 是无人值守，超时就停 —— 这是硬上限，
    #: 不是策略参数能放开的（要放开就得改这里，改这里就得过审）。
    unattended_max_minutes: int = 20

    def to_dict(self) -> dict:
        """落盘形状（运行摘要里要带策略快照，事后可审计）。"""
        return {
            "level": self.level.value,
            "window_whitelist": list(self.window_whitelist),
            "budget": {
                "max_steps": self.budget.max_steps,
                "max_seconds": self.budget.max_seconds,
                "max_model_calls": self.budget.max_model_calls,
            },
            "fuse_threshold": self.fuse_threshold,
            "idle_pause_enabled": self.idle_pause_enabled,
            "idle_active_ms": self.idle_active_ms,
            "estop_enabled": self.estop_enabled,
            "estop_vkeys": list(self.estop_vkeys),
            "unattended_max_minutes": self.unattended_max_minutes,
        }


def load_policy(
    *,
    level: str | AuthLevel,
    whitelist: tuple[str, ...] = (),
) -> GuardPolicy:
    """从显式参数装载策略（CLI 的装载入口）。

    ⚠️ 环境变量只覆盖**预算**（``MINICODE_BUDGET_STEPS`` / ``MINICODE_BUDGET_SECONDS``
    / ``MINICODE_BUDGET_MODEL_CALLS``）；级别与白名单必须**显式给出** ——
    授权不该能从环境里「顺手」放大。
    """
    resolved = AuthLevel(level) if isinstance(level, str) else level
    budget = GuardBudget(
        max_steps=int(os.environ.get("MINICODE_BUDGET_STEPS", GuardBudget.max_steps)),
        max_seconds=float(
            os.environ.get("MINICODE_BUDGET_SECONDS", GuardBudget.max_seconds)
        ),
        max_model_calls=int(
            os.environ.get("MINICODE_BUDGET_MODEL_CALLS", GuardBudget.max_model_calls)
        ),
    )
    return GuardPolicy(
        level=resolved,
        window_whitelist=tuple(part.strip() for part in whitelist if part.strip()),
        budget=budget,
    )

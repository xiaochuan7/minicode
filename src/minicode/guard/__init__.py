"""**运行护栏**（W2 真机执行层）：四级授权 + 十条护栏。

目标：把「能不能真的操控桌面」变成**可配置、可审计、可急停**的产品能力，
而不是一个危险开关。本包不改坐标口径、动作语义与审批判定规则——
它决定的是「这一步**让不让做**」，并把每一次决定记下来。

## 四级授权（默认 L0，fail-closed）

* **L0 只读**：截图 / 读控件树 / 算落点，不下发输入（注入器换 dry-run 形态）。
* **L1 白名单**：只在指定窗口集合内操作；不可逆动作一律拒绝（没审批人）。
* **L2 审批**：全桌面可动；不可逆动作必须人工确认（沿用 M16 审批闸门）。
* **L3 无人值守**：全桌面、无人工确认；仅限明确指定的实验，单次 ≤ 20 分钟。

## 十条护栏 → 模块映射

1. 急停热键（Ctrl+Alt+Shift+K）→ :mod:`.estop`（watchdog）+
   :mod:`.injector`（释放已按键）；
2. 窗口黑名单（任务管理器 / 注册表 / UAC / 密码管理器 / 终端 / 资源管理器）
   → :mod:`.blacklist`；
3. 不可逆动作按影响面分类 → :mod:`.irreversible` +
   :mod:`.gate`（L1 拒 / L2 审批 / L3 记录放行）；
4. 预算（步数 / 时长 / 模型调用）→ :mod:`.runtime` 的
   :class:`.runtime.BudgetLedger`；
5. 连续失败熔断（同一目标连续 2 次）→ :mod:`.runtime` 的
   :class:`.runtime.FailureFuse`；
6. 空闲让位（用户动键鼠就暂停）→ :mod:`.idle`；
7. 全程录制（帧哈希 + 控件树摘要 + 动作 + 落点 + 结果 → JSONL）
   → :mod:`.recorder` 的 :class:`.recorder.SessionRecorder`；
8. 桌面锁扩展（操作者 / 意图入锁文件，成对释放）→ :mod:`.lockext`；
9. 运行摘要（模式 / 步数 / 耗时 / 拒绝次数 / 急停）→ :mod:`.recorder` 的
   :class:`.recorder.RunSummary`；
10. ``--dry-run``（逐条打印将下发的动作但不执行）→ :mod:`.injector` 的
    dry-run 形态。

拦截点统一在 :class:`.seam.GuardedSeam`（装饰器缝，同 RetryingSeam 先例），
接线入口 :func:`.seam.make_guarded_factory`——缝以下的三个冻结包
（coord / exec / verify）零改动。
"""

from . import (
    blacklist,
    estop,
    fakes,
    gate,
    idle,
    injector,
    irreversible,
    levels,
    lockext,
    policy,
    recorder,
    runtime,
    seam,
    win32,
)

__all__ = [
    "blacklist",
    "estop",
    "fakes",
    "gate",
    "idle",
    "injector",
    "irreversible",
    "levels",
    "lockext",
    "policy",
    "recorder",
    "runtime",
    "seam",
    "win32",
]

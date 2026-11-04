"""**Loop Breaker**（W7 主线 C1）：VLAA-GUI 式的**重复计数器**自愈层。

**为什么不是又一个结果分类器**（工单 5.1 的诊断）：既有的自愈层
（``scripts/w4_long_tasks.py::HealedStepExecutor``）是**结果分类器** ——
只在「刚好复现那个失败形态」（如 ``not_clickable``）时才响，整夜只响 2 次。
本组件是**重复计数器**：每步无条件记录、无条件计算，窗口被填满就一定触发。

**三层**（VLAA-GUI，arXiv 2604.21375；阈值与窗口 = 论文口径，常量在此）：

1. **动作重复**：``n_a_t = |{i ∈ [t-1, t] : a_i = a_t 且 o_{i+1} ≈ o_i}|`` ≥
   :data:`LoopThresholds.action_repeat`（=2，窗口大小恰等于阈值 →
   触发条件就是**窗口被填满**）→ 切换**交互方式**（键盘快捷键 → 菜单点击
   → 命令行）。
2. **观测重复**：``n_o_t = |{i ∈ [t-2, t] : o_i ≈ o_t}|`` ≥
   :data:`LoopThresholds.obs_repeat`（=3）→ 更换**整体策略**
   （菜单导航 → 直接改文件）。
3. **模型裁决**：前两层都没拦住但「连续 stall_trigger_steps 步动作重复
   计数非零」（自定触发条件 —— 论文未给出本层的确切触发式）→ 花**一次**
   模型调用返回 keep/switch；判 switch 时下发一条**只作用于下一步**的
   黑名单指令（禁止重复该动作，强制换替代方案），**不是永久封禁**。

**三条纪律（照论文，不许破）**：

* **loop-break ≠ fail**：重复本身不构成任务不可行的证据；失败终止需要
  单独证据。触发只产出「换方式」的指令，从不判任务失败。
* **两个计数器每步无条件落盘**（不是只在触发时写）—— 这正是分不开
  「触发太窄」和「策略无效」的原因：只在 heal 时留痕，就永远只有 heal 的样本。
* 阈值是常量；``≈`` 的度量是**自定**（论文未定义），见下。

**``≈`` 的自定度量（必须如实标注是自定）**：

* 缺省 :class:`DigestComparator` —— 观测摘要（帧字节 digest）**精确相等**。
  理由：确定性、零依赖、可复算；代价是对真机的光标闪烁/时钟变化过敏
  （比对论文更保守 —— 更难判「≈」，倾向于**少触发**，fail-closed 取向）。
* 真机可换 :class:`PhashComparator`（感知哈希汉明距 ≤ 8）—— 注入
  ``comparator=PhashComparator(hash_fn=...)``，阈值同样显式。

**三个分开的数**（工单要求，不许并成一个「触发率」）：

* :attr:`stats["encountered"]` —— 重复**发生**的步数（n_a ≥ 1 或 n_o ≥ 1）；
* :attr:`stats["triggered"]` —— **触发自愈**的次数（任一层）；
* :attr:`stats["recovered"]` —— 自愈**成功**次数（触发后 1 步内观测出现
  可见变化 —— 自定口径）。

用法::

    breaker = LoopBreaker(comparator=DigestComparator(), sink=loop_log.write)
    verdict = breaker.observe(step_index=12, action_key="click@1200,300",
                              obs_before=d1, obs_after=d1)
    if verdict.blacklist_next:
        # 下一步的执行器带上这条黑名单指令（只作用一步，用后即弃）。
        ...
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field

__all__ = [
    "LoopBreaker",
    "LoopRecord",
    "LoopThresholds",
    "LoopVerdict",
    "DigestComparator",
    "PhashComparator",
]

#: ``≈`` 观测判等的端口：``(prev, curr) -> bool``。
ObservationComparator = Callable[[object, object], bool]

#: 计数器落盘缝：每步一条（无条件）。
LoopSink = Callable[[dict], None]


@dataclass(frozen=True)
class LoopThresholds:
    """双计数器的窗口与阈值（**论文口径**；窗口大小恰等于阈值）。

    依据：VLAA-GUI 的定义 —— ``n_a`` 窗口 2 / 阈值 2，``n_o`` 窗口 3 /
    阈值 3。触发条件即「窗口被填满」。**不许放宽**（工单纪律）。
    """

    action_window: int = 2
    action_repeat: int = 2
    obs_window: int = 3
    obs_repeat: int = 3
    #: 层 3（模型裁决）的触发条件：连续 N 步 n_a ≥ 1 但前两层未触发。
    #: ⚠️ **自定**（论文未给出层 3 的确切触发式）；N=6 的理由：两层窗口
    #: （2、3）都填不满的「温水卡住」，6 步约等于一次典型 UI 操作链的长度。
    stall_trigger_steps: int = 6


class DigestComparator:
    """``≈`` 的缺省度量：观测摘要**精确相等**（自定；见模块 docstring）。"""

    def __call__(self, prev: object, curr: object) -> bool:
        return prev == curr


class PhashComparator:
    """``≈`` 的真机度量：感知哈希汉明距 ≤ 阈值（自定；真机噪声取 8/64）。"""

    def __init__(self, hash_fn: Callable[[object], int], max_hamming: int = 8):
        self.hash_fn = hash_fn
        self.max_hamming = max_hamming

    def __call__(self, prev: object, curr: object) -> bool:
        return bin(int(self.hash_fn(prev)) ^ int(self.hash_fn(curr))).count("1") <= self.max_hamming


@dataclass(frozen=True)
class LoopVerdict:
    """一步的裁决（``level`` = 1/2/3 层；``None`` = 无触发）。"""

    level: int | None
    #: 下一步的黑名单指令（**只作用于下一步**；无触发 → 空）。
    blacklist_next: str = ""
    #: 本步双计数器读数（审计口径）。
    action_repeat_count: int = 0
    obs_repeat_count: int = 0

    @property
    def triggered(self) -> bool:
        return self.level is not None


@dataclass(frozen=True)
class LoopRecord:
    """每步**无条件**落盘的记录（``LoopSink`` 收到的形状）。"""

    step: int
    action_key: str
    action_repeat_count: int
    obs_repeat_count: int
    level: int | None
    blacklist_next: str
    ts: str = field(default_factory=lambda: time.strftime("%Y-%m-%dT%H:%M:%S"))

    def to_dict(self) -> dict:
        return {
            "step": self.step,
            "action_key": self.action_key,
            "action_repeat_count": self.action_repeat_count,
            "obs_repeat_count": self.obs_repeat_count,
            "level": self.level,
            "blacklist_next": self.blacklist_next,
            "ts": self.ts,
        }


class LoopBreaker:
    """双计数器 + 三层裁决（每步 ``observe`` 一次；计数器无条件落盘）。"""

    def __init__(
        self,
        *,
        comparator: ObservationComparator | None = None,
        thresholds: LoopThresholds | None = None,
        sink: LoopSink | None = None,
        model_verdict: Callable[[str], str] | None = None,
        stall_model_verdict: Callable[[], bool] | None = None,
    ):
        self.comparator = comparator or DigestComparator()
        self.thresholds = thresholds or LoopThresholds()
        self.sink = sink
        #: 层 3 的模型端口：输入上下文摘要，返回 ``"keep"`` / ``"switch"``。
        self.model_verdict = model_verdict
        #: 层 3 的**触发开关**（要不要花这次调用）—— 缺省按 stall 常量算。
        self._force_model_verdict = stall_model_verdict
        # 观测/动作滚动窗口（语义见 observe() 内注释）。
        self._prev_before: object | None = None  # o_{t-2}
        self._last_before: object | None = None  # o_{t-1}
        self._last_after: object | None = None  # o_t（= 动作 t+1 的 before）
        self._last_action_key: str | None = None  # a_{t-1}
        self._stall_run = 0
        self._pending_blacklist = ""
        # 三个分开的数（审计口径）。
        self.stats = {"encountered": 0, "triggered": 0, "recovered": 0}
        self._last_triggered_step: int | None = None

    def observe(
        self,
        *,
        step: int,
        action_key: str,
        obs_before: object,
        obs_after: object,
    ) -> LoopVerdict:
        """动作 ``step`` 执行完、观测 ``obs_after`` 到来时调用（每步一次）。

        观测下标按论文：``obs_before`` = ``o_t``（动作前），``obs_after`` =
        ``o_{t+1}``（动作后 = 下一步的 before）。计数器读数（自定实现，
        已在测试钉死）：

        * ``n_a_t`` = 窗口 [t-1, t] 里「同键动作且执行前后观测 ≈」的位置数；
        * ``n_o_t`` = **最近三个观测**（o_{t-1}, o_t, o_{t+1}）里与当前观测 ≈
          的个数 —— 与论文窗口 [t-2, t] 相比索引平移 1 位（判定时机在动作
          执行完之后），「最近 3 个观测彼此 ≈」的语义相同。
        """
        approx_no_change_now = self.comparator(obs_before, obs_after)
        # n^a_t（i=t）：本次动作无可见变化（o_{t+1} ≈ o_t；a_t = a_t 恒真）。
        # n^a_t（i=t-1）：上次动作同键 且 o_t ≈ o_{t-1} —— 即**上次动作**执行
        # 前后观测无变化（o_{t-1} = 上次 before，o_t = 上次 after）。
        n_action = (
            1 if approx_no_change_now else 0
        ) + (
            1
            if self._last_action_key == action_key
            and self._last_before is not None
            and self._last_after is not None
            and self.comparator(self._last_before, self._last_after)
            else 0
        )
        # n^o_t（i ∈ [t-2, t]）：与 o_t ≈ 的观测数。o_t = 本次 after。
        n_obs = sum(
            1
            for obs in (self._prev_before, self._last_before, obs_after)
            if obs is not None and self.comparator(obs, obs_after)
        )

        level: int | None = None
        blacklist = ""
        if n_action >= self.thresholds.action_repeat:
            level = 1
            blacklist = (
                f"禁止重复动作 {action_key}（已连续 {n_action} 次无可见变化）；"
                "换一种交互方式：键盘快捷键 / 菜单点击 / 命令行"
            )
        elif n_obs >= self.thresholds.obs_repeat:
            level = 2
            blacklist = (
                "最近 3 次观测无变化；更换整体策略（如：菜单导航 → 直接改文件）"
            )
        elif (
            self.model_verdict is not None
            and self._stall_model_gate(step)
        ):
            verdict = str(self.model_verdict(action_key)).strip().lower()
            if verdict.startswith("switch"):
                level = 3
                blacklist = (
                    f"模型裁决 switch：禁止重复动作 {action_key}；"
                    "下一步必须换替代方案（只作用于下一步）"
                )

        # 触发后 1 步内观测出现可见变化 → 记「救回」（自定口径）。
        if (
            self._last_triggered_step is not None
            and step == self._last_triggered_step + 1
            and not approx_no_change_now
        ):
            self.stats["recovered"] += 1

        if level is not None:
            self.stats["triggered"] += 1
            self._last_triggered_step = step
            self._pending_blacklist = blacklist
        if n_action >= 1 or n_obs >= 1:
            self.stats["encountered"] += 1

        # stall 计数（层 3 触发条件用）。
        self._stall_run = self._stall_run + 1 if n_action >= 1 else 0

        record = LoopRecord(
            step=step,
            action_key=action_key,
            action_repeat_count=n_action,
            obs_repeat_count=n_obs,
            level=level,
            blacklist_next=blacklist,
        )
        if self.sink is not None:
            try:
                self.sink(record.to_dict())
            except Exception:  # noqa: BLE001 —— 落盘失败不影响裁决
                pass

        # 滚动窗口前进。
        self._prev_before = self._last_before
        self._last_before = obs_before
        self._last_after = obs_after
        self._last_action_key = action_key
        return LoopVerdict(
            level=level,
            blacklist_next=blacklist,
            action_repeat_count=n_action,
            obs_repeat_count=n_obs,
        )

    def consume_blacklist(self) -> str:
        """取走挂起的黑名单指令（**只作用于下一步**；取后即清）。"""
        blacklist, self._pending_blacklist = self._pending_blacklist, ""
        return blacklist

    def _stall_model_gate(self, step: int) -> bool:
        if self._force_model_verdict is not None:
            return self._force_model_verdict()
        return self._stall_run + 1 >= self.thresholds.stall_trigger_steps

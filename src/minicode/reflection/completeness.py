"""**Completeness 判定器**（W7 主线 C2）：两段式完成判定，治「判分口径不定」。

**背景**（工单 5.2）：长任务 T4 曾预登记两套读法（「终态符合意图」= pass，
「逐行精确相等」= fail）并排写、没裁决 —— 那不是诚实，是没定标准。
本组件把「怎样算成功」**先写成 K 条机器可验判据**，两段式核验：

* **第一段（每步、零额外调用）**：K 条判据每步自查 —— 全过且**界面稳定**
  → 才允许报 done（只是「允许」，不是「完成」）。不花模型调用。
* **第二段（只在 done() 时触发一次）**：:meth:`final_gate` 逐条核验并
  **必须给出直接证据**（文件 diff / 控件状态 / 退出码）才判通过；
  驳回时把「缺哪条证据」写回轨迹（:attr:`CompletenessVerdict.missing`），
  **保留部分进展**（不重做，不判任务失败）。
* **两段都同意才算终止**。

**纪律**：

* 判据必须**机器可验**（文件内容 diff、控件状态、退出码），**不是散文** ——
  结构上 :class:`Criterion.verify` 是必填的函数端口，散文塞不进来；
* 判定器**独立于执行器**（不 import 执行层，context 由调用方给）；
* **防误驳**（VLAA-GUI 口径）：靠「界面稳定」合取条件 + 驳回建议里写明
  「无可见变化 → 先 wait 再复查，不要立刻重做」；
* **不要对它期望过高**（论文口径）：它改善「错误的完成宣称」，但不解决
  —— 实测加它后仍有大量失败以错误的完成宣称收尾。本仓把它当**判分口径
  的确定性**工具（主口径 + 敏感性分析），不当成功率卖点。

参考：VLAA-GUI（arXiv 2604.21375）两段式；OSWorld（arXiv 2404.07972）的
确定性执行式判分（``diff_text_file`` 之类）。
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

__all__ = [
    "Criterion",
    "CriterionResult",
    "CompletenessChecker",
    "CompletenessVerdict",
]

#: 判据核验端口：context → 该条的结果（**必须带直接证据**）。
CriterionVerifier = Callable[[object], "CriterionResult"]


@dataclass(frozen=True)
class Criterion:
    """一条**机器可验**的完成判据。

    :param name: 短名（进 verdict 与驳回清单）。
    :param verify: 核验函数 —— 拿 context（任务台注入的读数端口）算出
      ``(ok, evidence)``。evidence 是**直接证据**（文件 diff、控件状态、
      退出码……），不是解释文字。
    :param side_effect: 是否副作用类判据（保存/导出/发送）——
      是 → 终止判定额外要求**可见确认**（见 :class:`CompletenessChecker`）。
    """

    name: str
    verify: CriterionVerifier
    side_effect: bool = False


@dataclass(frozen=True)
class CriterionResult:
    """一条判据的结果（``evidence`` 必填 —— 没有证据的通过不算通过）。"""

    ok: bool
    evidence: str
    error: str = ""


@dataclass(frozen=True)
class CompletenessVerdict:
    """终判（两段都同意才 ``accepted``）。"""

    accepted: bool
    #: 驳回时：缺哪条证据（判据名 + 缺的证据）—— 写回轨迹，保留部分进展。
    missing: tuple[str, ...] = ()
    #: 逐条核验记录（审计口径）。
    per_criterion: tuple[dict, ...] = ()
    #: 防误驳建议（无可见变化时给「先 wait 再复查」的提示）。
    advice: str = ""
    #: 「界面稳定」条件的状态（True = 连续观测一致）。
    ui_stable: bool = False


class CompletenessChecker:
    """两段式完成判定器（**零模型调用**：判据是函数，不是散文，不是 VLM）。

    :param criteria: K 条判据（任务开跑前**写死**预登记，跑中不改）。
    :param stable_rounds: 「界面稳定」要求最近 N 次观测一致（缺省 2；
      观测由调用方经 :meth:`note_observation` 喂进来 —— 判定器自己不发请求）。
    """

    def __init__(self, criteria: Sequence[Criterion], *, stable_rounds: int = 2):
        if not criteria:
            raise ValueError("判据清单不能为空 —— 没有判据就没有终止口径")
        self.criteria = tuple(criteria)
        self.stable_rounds = max(1, int(stable_rounds))
        self._recent_observations: list[object] = []

    def note_observation(self, observation: object) -> None:
        """喂一次界面观测（指纹/快照摘要）—— 每步一次，供「界面稳定」判。"""
        self._recent_observations.append(observation)
        self._recent_observations = self._recent_observations[-self.stable_rounds :]

    @property
    def ui_stable(self) -> bool:
        """最近 N 次观测一致（N 次以上且逐位相等）。"""
        window = self._recent_observations[-self.stable_rounds :]
        return len(window) >= self.stable_rounds and len(set(map(str, window))) == 1

    def in_progress_check(self, context: object) -> bool:
        """**第一段**（每步、零额外调用）：K 条全过 且 界面稳定 → 允许报 done。

        只做「允许」判断 —— 不产生终止，不产生失败；不花任何模型调用。
        判据抛异常按「未过」处理（如实，不吞）。
        """
        if not self.ui_stable:
            return False
        return all(self._verify_one(criterion, context).ok for criterion in self.criteria)

    def final_gate(self, context: object) -> CompletenessVerdict:
        """**第二段**（done() 时一次）：逐条核验 + 直接证据 + 界面稳定。

        * 副作用类判据（保存/导出/发送）的证据必须**非空** —— 没有可见确认
          就算没过（防「点了保存但没保存成功」的静默失败）；
        * 驳回 → :attr:`CompletenessVerdict.missing` 列出缺哪条证据
          （写回轨迹、保留部分进展），并附防误驳建议。
        """
        missing: list[str] = []
        per_criterion: list[dict] = []
        for criterion in self.criteria:
            result = self._verify_one(criterion, context)
            per_criterion.append({
                "name": criterion.name,
                "ok": result.ok,
                "evidence": result.evidence,
                "error": result.error,
                "side_effect": criterion.side_effect,
            })
            if not result.ok:
                missing.append(
                    f"{criterion.name}：{result.error or '证据不足'}"
                )
            elif criterion.side_effect and not result.evidence.strip():
                missing.append(
                    f"{criterion.name}：副作用动作缺可见确认（保存/导出/发送必须有可见回执）"
                )
        stable = self.ui_stable
        if not stable:
            missing.append("界面稳定：最近观测不一致（可能仍在变化）")
        advice = (
            "无可见变化 → 先 wait 再复查，不要立刻重做"
            if missing and not stable
            else ""
        )
        return CompletenessVerdict(
            accepted=not missing,
            missing=tuple(missing),
            per_criterion=tuple(per_criterion),
            advice=advice,
            ui_stable=stable,
        )

    def _verify_one(self, criterion: Criterion, context: object) -> CriterionResult:
        try:
            return criterion.verify(context)
        except Exception as exc:  # noqa: BLE001 —— 判据异常 = 未过（如实）
            return CriterionResult(
                ok=False, evidence="", error=f"判据异常 {type(exc).__name__}: {exc}"
            )


def criterion_of(
    name: str,
    predicate: Callable[[object], bool],
    evidence_fn: Callable[[object], str] | None = None,
    *,
    side_effect: bool = False,
) -> Criterion:
    """从「谓词 + 可选证据函数」快速造一条判据（判据清单的糖）。

    ⚠️ predicate 必须是**机器可验**的读数（文件 diff / 控件状态 / 退出码），
    不是散文判断 —— 散文判据在评审里会被直接打回。
    """
    def verify(context: object) -> CriterionResult:
        evidence = evidence_fn(context) if evidence_fn else ""
        ok = bool(predicate(context))
        return CriterionResult(
            ok=ok,
            evidence=evidence if ok else "",
            error="" if ok else "谓词不成立",
        )

    return Criterion(name=name, verify=verify, side_effect=side_effect)

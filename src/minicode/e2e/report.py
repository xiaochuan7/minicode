"""**端到端报告**（工单 M14 的「报告口径」）。

六张票逐个给「通过 / 失败 / 跳过」与原因；**人工代确认项单独统计**；
**跳过必须写明环境原因**，且**永远不算通过**。

⚠️ **记分口径（2026-10-22 对齐公开基准）** —— 报告**抬头**必须写明这一段，
否则 ``pass_rate`` 与公开数字**不可比**：

* **分母 = 自动通过 + 自动失败 + 环境不可达(infeasible) + 合规停手(handoff)**；
* **infeasible 进分母、单列**（OSWorld / WAA 口径：环境不可达**进分母**）——
  ⚠️ OSWorld 下 infeasible 题**只有 agent 声明 FAIL 才算成功**，
  而我们的动作协议里**没有「声明 FAIL」这个动作** → 一律**记 0 分**；
* **合规停手**（撞停止条件 → 零动作下发、交人）**进分母、记 0 分**，
  **不许算成自动通过**（公开工作没有「交人=通过」的先例：
  UFO² ``ASK_QUESTION=False``；AndroidWorld 声明 FAIL 后可行任务 = 0 分）；
* **人工代跑**（口径规定不自动执行、agent 从未尝试）**单列、不进分母**。

三条老口径在这一层落地（工单 M14 验收标准）：

* **跳过不算通过** —— ``skip`` 不进通过率的**分子**；
* **人工代确认单独统计** —— 不混进自动通过率；
* **判不了不算通过** —— 落在 ``fail`` 里，原因里写明是「判不了」。
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any

from .runner import (
    VERDICT_FAIL,
    VERDICT_HANDOFF,
    VERDICT_PASS,
    VERDICT_PENDING_HUMAN,
    VERDICT_SKIP,
    TaskOutcome,
)

__all__ = ["build_report", "render_markdown", "CALIBER"]

#: 判定取值 → 人读的中文。
_VERDICT_TEXT = {
    VERDICT_PASS: "通过",
    VERDICT_FAIL: "失败",
    VERDICT_SKIP: "跳过（环境不可达）",
    VERDICT_PENDING_HUMAN: "待人工确认",
    VERDICT_HANDOFF: "合规停手（交人）",
}

#: **报告抬头的口径说明**（分母口径 + 各桶的含义；对外可比的那一段）。
CALIBER: dict[str, Any] = {
    "denominator": (
        "自动通过 + 自动失败 + 环境不可达(infeasible) + 合规停手(handoff)"
    ),
    "buckets": {
        "passed": "自动通过：agent 自己跑完，期望成立且验收条款全成立",
        "failed": "自动失败：跑了但没达成（含「判不了」—— 判不了 ≠ 成立）",
        "infeasible": (
            "环境不可达（infeasible）：**进分母、单列**；OSWorld 口径下"
            "只有 agent 声明 FAIL 才算成功，而我们的动作协议里没有"
            "「声明 FAIL」这个动作 → 一律记 0 分"
        ),
        "handoff": (
            "合规停手：撞到停止条件（模态弹窗 / 轮次上限 / 提权…）→ 零动作下发、"
            "交人；**进分母、记 0 分**，不许算成自动通过"
        ),
        "manual": (
            "人工代跑：口径规定**不自动执行**、agent 从未尝试 → "
            "**单列、不进分母**、不混进自动通过率"
        ),
    },
    "note": (
        "口径对齐 OSWorld / WAA（环境不可达进分母 + 单列）。"
        "本报告的 pass_rate 与公开数字比较之前，**先看这一段**。"
    ),
}


def build_report(outcomes: Sequence[TaskOutcome], *, title: str = "") -> dict[str, Any]:
    """把逐张工单的结果汇成一份报告。"""
    tasks = [outcome.to_dict() for outcome in outcomes]
    passed = [o for o in outcomes if o.verdict == VERDICT_PASS]
    failed = [o for o in outcomes if o.verdict == VERDICT_FAIL]
    infeasible = [o for o in outcomes if o.verdict == VERDICT_SKIP]
    handoff = [o for o in outcomes if o.verdict == VERDICT_HANDOFF]
    manual = [o for o in outcomes if o.verdict == VERDICT_PENDING_HUMAN]
    #: **自动通过率**的分母 = 通过 + 失败 + **环境不可达** + **合规停手**
    #: （OSWorld / WAA 口径：环境不可达进分母；交人 = 0 分，不是通过）。
    #: **人工代跑不进分母**（口径规定不自动执行 → agent 从未尝试）。
    denominator = len(passed) + len(failed) + len(infeasible) + len(handoff)
    return {
        "title": title,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        #: **口径说明**（报告抬头；对外可比的那一段）。
        "caliber": dict(CALIBER),
        "tasks": tasks,
        "auto": {
            "passed": len(passed),
            "failed": len(failed),
            "infeasible": len(infeasible),
            "handoff": len(handoff),
            "denominator": denominator,
            "pass_rate": (len(passed) / denominator) if denominator else None,
            "denominator_note": (
                "分母 = 自动通过 + 自动失败 + 环境不可达(infeasible) "
                "+ 合规停手(handoff)；人工代跑不进分母"
            ),
        },
        "human_confirm": {
            "count": len(manual),
            #: 其中**人已经代跑完**的（结果由 ``--manual`` 带进来）。
            "completed": len([o for o in manual if o.manual]),
            #: 其中**还没代跑**的。
            "pending": len([o for o in manual if not o.manual]),
            "ids": [outcome.task_id for outcome in manual],
            "note": "人工代跑单独统计：不进分母、不混进自动通过率",
            "results": [
                {
                    "task_id": outcome.task_id,
                    "verdict": str(outcome.manual.get("verdict", "")),
                    "note": str(outcome.manual.get("note", "")),
                }
                for outcome in manual
                if outcome.manual
            ],
        },
        "handoff": {
            "count": len(handoff),
            "ids": [outcome.task_id for outcome in handoff],
            "note": "合规停手单独统计：进分母、记 0 分，不许算成自动通过",
            "results": [
                {
                    "task_id": outcome.task_id,
                    "stop_reason": outcome.stop_reason,
                    "reason": outcome.reason,
                }
                for outcome in handoff
            ],
        },
        "infeasible": [
            {"task_id": outcome.task_id, "reason": outcome.skip_reason or outcome.reason}
            for outcome in infeasible
        ],
    }


def render_markdown(report: dict[str, Any]) -> str:
    """人读版报告（Markdown）。"""
    lines: list[str] = []
    if report.get("title"):
        lines.append(f"# {report['title']}")
    lines.append("")
    #: **抬头写明口径**（分母口径 + 各桶的含义）—— 对外可比的那一段。
    caliber = report.get("caliber") or {}
    if caliber:
        lines.append("> **记分口径（对外可比）**")
        lines.append(">")
        lines.append(f"> - 分母 = {caliber['denominator']}")
        for name, text in (caliber.get("buckets") or {}).items():
            lines.append(f"> - {name}：{text}")
        lines.append(">")
        lines.append(f"> {caliber.get('note', '')}")
        lines.append("")
    lines.append("| 工单 | 判定 | 人工代确认 | 原因 |")
    lines.append("|---|---|---|---|")
    for task in report["tasks"]:
        reason = (task["reason"] or "").replace("|", "｜").replace("\n", " ")
        lines.append(
            f"| {task['task_id']} {task['title']} | "
            f"{_VERDICT_TEXT.get(task['verdict'], task['verdict'])} | "
            f"{'是' if task['human_confirm'] else '否'} | {reason} |"
        )
    auto = report["auto"]
    rate = auto["pass_rate"]
    lines.append("")
    lines.append(
        f"**自动通过率**：{auto['passed']}/{auto['denominator']}"
        f"（{('%.0f%%' % (rate * 100)) if rate is not None else '—'}）"
        f"｜失败 {auto['failed']}｜环境不可达 {auto['infeasible']}"
        f"｜合规停手 {auto['handoff']}"
    )
    lines.append(f"（分母口径：{auto['denominator_note']}）")
    human = report["human_confirm"]
    lines.append(
        f"**人工代跑（单独统计，不进分母）**：{human['count']} 项"
        f"（{'、'.join(human['ids']) if human['ids'] else '无'}）——{human['note']}"
        f"；其中已代跑完成 {human['completed']} 项、待代跑 {human['pending']} 项"
    )
    for item in human.get("results", []):
        lines.append(
            f"- {item['task_id']}：人工代跑 = {item['verdict'] or '（未写）'}"
            f"（{item['note'] or '（无备注）'}）"
        )
    handoff = report["handoff"]
    lines.append(
        f"**合规停手（单独统计，进分母记 0 分）**：{handoff['count']} 项"
        f"（{'、'.join(handoff['ids']) if handoff['ids'] else '无'}）——{handoff['note']}"
    )
    for item in handoff.get("results", []):
        lines.append(
            f"- {item['task_id']}：停止原因 = {item['stop_reason'] or '（未记）'}"
            f"（{item['reason'] or '（无备注）'}）"
        )
    if report["infeasible"]:
        lines.append("")
        lines.append("**环境不可达（infeasible，进分母、单列）**：")
        for item in report["infeasible"]:
            lines.append(f"- {item['task_id']}：{item['reason']}")
    for task in report["tasks"]:
        if not task["steps"]:
            continue
        lines.append("")
        lines.append(f"## {task['task_id']} {task['title']}")
        if task.get("note"):
            lines.append("")
            lines.append(f"> ⚠️ {task['note']}")
        lines.append("")
        lines.append("| 步 | 通道 | 失败类型 | 三类失败 | 期望 | 三段耗时（定位/决策/执行+校验） | 落点偏差 |")
        lines.append("|---|---|---|---|---|---|---|")
        for step in task["steps"]:
            if step["expectation_met"] is True:
                met = "成立"
            elif step["expectation_met"] is False:
                met = "不成立"
            else:
                met = "判不了"
            failures = "、".join(
                f"{name}={code}" for name, code in (step["failures"] or {}).items() if code
            ) or "—"
            error = step["landing_error_px"]
            lines.append(
                f"| {step['step_id']} {step['description']} | "
                f"{step['channel'] or '—'} | {step['failure_type'] or '—'} | "
                f"{failures} | "
                f"{met}（{step['expectation'] or '—'}） | "
                f"{step['locate_ms']:.0f} / {step['decision_ms']:.0f} / "
                f"{step['execute_verify_ms']:.0f} ms | "
                f"{f'{error:.1f} px' if error is not None else '—'} |"
            )
        if task["clauses"]:
            lines.append("")
            lines.append("**验收条款**：")
            for clause in task["clauses"]:
                mark = "✓" if clause["ok"] else "✗"
                lines.append(f"- {mark} {clause['id']} {clause['description']}：{clause['detail']}")
    return "\n".join(lines) + "\n"

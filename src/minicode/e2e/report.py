"""**端到端报告**（工单 M14 的「报告口径」）。

六张票逐个给「通过 / 失败 / 跳过」与原因；**人工代确认项单独统计**；
**跳过必须写明环境原因**，且**永远不算通过**。

三条口径在这一层落地（工单 M14 验收标准）：

* **跳过不算通过** —— ``skip`` 与 ``pending_human`` 都不进通过率；
* **人工代确认单独统计** —— 不混进自动通过率；
* **判不了不算通过** —— 落在 ``fail`` 里，原因里写明是「判不了」。
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any

from .runner import (
    VERDICT_FAIL,
    VERDICT_PASS,
    VERDICT_PENDING_HUMAN,
    VERDICT_SKIP,
    TaskOutcome,
)

__all__ = ["build_report", "render_markdown"]

#: 判定取值 → 人读的中文。
_VERDICT_TEXT = {
    VERDICT_PASS: "通过",
    VERDICT_FAIL: "失败",
    VERDICT_SKIP: "跳过",
    VERDICT_PENDING_HUMAN: "待人工确认",
}


def build_report(outcomes: Sequence[TaskOutcome], *, title: str = "") -> dict[str, Any]:
    """把逐张工单的结果汇成一份报告。"""
    tasks = [outcome.to_dict() for outcome in outcomes]
    passed = [o for o in outcomes if o.verdict == VERDICT_PASS]
    failed = [o for o in outcomes if o.verdict == VERDICT_FAIL]
    skipped = [o for o in outcomes if o.verdict == VERDICT_SKIP]
    pending = [o for o in outcomes if o.verdict == VERDICT_PENDING_HUMAN]
    #: **自动通过率**的分母 = 通过 + 失败（跳过与待人工确认都不算）。
    denominator = len(passed) + len(failed)
    return {
        "title": title,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "tasks": tasks,
        "auto": {
            "passed": len(passed),
            "failed": len(failed),
            "skipped": len(skipped),
            "denominator": denominator,
            "pass_rate": (len(passed) / denominator) if denominator else None,
        },
        "human_confirm": {
            "count": len(pending),
            "ids": [outcome.task_id for outcome in pending],
            "note": "人工代确认项单独统计，不混进自动通过率",
            "results": [
                {
                    "task_id": outcome.task_id,
                    "verdict": str(outcome.manual.get("verdict", "")),
                    "note": str(outcome.manual.get("note", "")),
                }
                for outcome in pending
                if outcome.manual
            ],
        },
        "skipped": [
            {"task_id": outcome.task_id, "reason": outcome.skip_reason or outcome.reason}
            for outcome in skipped
        ],
    }


def render_markdown(report: dict[str, Any]) -> str:
    """人读版报告（Markdown）。"""
    lines: list[str] = []
    if report.get("title"):
        lines.append(f"# {report['title']}")
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
        f"｜失败 {auto['failed']}｜跳过 {auto['skipped']}"
    )
    human = report["human_confirm"]
    lines.append(
        f"**人工代确认（单独统计）**：{human['count']} 项"
        f"（{'、'.join(human['ids']) if human['ids'] else '无'}）——{human['note']}"
    )
    for item in human.get("results", []):
        lines.append(
            f"- {item['task_id']}：人工代跑 = {item['verdict'] or '（未写）'}"
            f"（{item['note'] or '（无备注）'}）"
        )
    if report["skipped"]:
        lines.append("")
        lines.append("**跳过原因**：")
        for item in report["skipped"]:
            lines.append(f"- {item['task_id']}：{item['reason']}")
    for task in report["tasks"]:
        if not task["steps"]:
            continue
        lines.append("")
        lines.append(f"## {task['task_id']} {task['title']}")
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

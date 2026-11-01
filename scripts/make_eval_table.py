"""W4 评测汇总表生成器（脱敏、可复现）。

读 ``runs/w4/round*/`` 下的 ``<id>-summary.json``，**从 attempt 明细重算**
全部指标（不信任旧汇总里的布尔——旧轮次与新轮次的口径统一在这里收敛），
写 ``docs/w4-eval-summary.md``。对 ``runs/`` 只读，不修改任何既有产物。

口径（与 ``scripts/taskbench.py`` 的 task_metrics 一致）：

* attempt 状态五类：pass / fail / error（步骤或 judge 崩溃）/ blocked
  （环境前置：锁、reset、起实例）/ paused（护栏 6 空闲让位）；
* success@1 = pass / (pass + fail + error)，attempt 级按组池化，
  **paused 与 blocked 从分母剔除**；
* success@3 = 一条任务的 attempts 里至少 1 次 pass（任务级；
  overall 池化按「轮×任务」对计）；
* paused 次数 / blocked 次数 / 人工接管次数 / 危险动作次数单独列出；
* 没跑的任务写「未跑」，作废的轮次不进池、只在注记里说明——不估算。

用法（仓库根目录）::

    python -I scripts/make_eval_table.py --runs runs/w4 ^
        --rounds round1,round2,round3-redo ^
        --void "round3：桌面被用户占用（真实键鼠输入），paused 级联，作废；重跑为 round3-redo" ^
        --out docs/w4-eval-summary.md
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "scripts"))

from taskbench import aggregate  # noqa: E402

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

_CATEGORY_ORDER = ("文件类", "计算类", "浏览器类", "界面类", "复合类", "反例类")


def _load_round(round_dir: Path) -> list[dict]:
    summaries = []
    for path in sorted(round_dir.glob("*-summary.json")):
        summaries.append(json.loads(path.read_text(encoding="utf-8")))
    return summaries


def _fmt_rate(text: str) -> str:
    return text


def main() -> int:
    parser = argparse.ArgumentParser(description="W4 评测汇总表（docs/，脱敏）")
    parser.add_argument("--runs", default="runs/w4")
    parser.add_argument("--rounds", default="round1,round2",
                        help="进池的轮次目录名（相对 --runs），逗号分隔")
    parser.add_argument("--extra", action="append", default=[],
                        help="额外轮次 label=目录路径（可多次，如 round3-redo=runs/w4-redo/round1）")
    parser.add_argument("--override", action="append", default=[],
                        help="单任务替换 round:task=path（如 round3-redo:i16=runs/w4-redo/round1-i16fix2/round1/i16-summary.json）"
                             "——用于轮中修复判分缺陷后换入复验数据；表格注记原文披露")
    parser.add_argument("--override-note", default="",
                        help="替换原因注记（原文写入表格）")
    parser.add_argument("--negative-control", default="",
                        help="判分器负向对照结果行（原文写入，如「3/3 通过」）")
    parser.add_argument("--void", default="", help="作废轮次注记（原文写入表格）")
    parser.add_argument("--code-note", default="",
                        help="各轮代码版本的如实注记")
    parser.add_argument("--out", default="docs/w4-eval-summary.md")
    args = parser.parse_args()

    runs_dir = _ROOT / args.runs
    round_names = [r.strip() for r in args.rounds.split(",") if r.strip()]
    per_round: dict[str, list[dict]] = {}
    for name in round_names:
        round_dir = runs_dir / name
        if not round_dir.is_dir():
            print(f"警告：轮次目录不存在，跳过：{round_dir}")
            per_round[name] = []
            continue
        per_round[name] = _load_round(round_dir)
    for item in args.extra:
        label, _, path = item.partition("=")
        path = path.strip()
        round_names.append(label.strip())
        per_round[label.strip()] = (
            _load_round(Path(path)) if path and Path(path).is_dir() else []
        )
    override_notes: list[str] = []
    for item in args.override:
        spec, _, path = item.partition("=")
        round_label, _, task_id = spec.strip().partition(":")
        replacement = json.loads(Path(path).read_text(encoding="utf-8"))
        bucket = per_round.get(round_label.strip())
        if bucket is None:
            print(f"警告：override 的轮次不存在，跳过：{round_label}")
            continue
        bucket[:] = [s for s in bucket if s.get("task") != task_id.strip()]
        bucket.append(replacement)
        override_notes.append(f"{round_label.strip()} 的 {task_id.strip()} 换入 {path}")

    pooled: list[dict] = []
    for name in round_names:
        pooled.extend(per_round[name])
    overall = aggregate(pooled)

    lines: list[str] = []
    lines.append("# W4 任务级评测汇总（脱敏）")
    lines.append("")
    lines.append(
        f"生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M')}；"
        f"生成器：`scripts/make_eval_table.py`（从 attempt 明细重算，可复现）。"
    )
    lines.append("")
    lines.append("## 口径")
    lines.append("")
    lines.append(
        "* attempt 状态五类：**pass** / **fail** / **error**（步骤或 judge 崩溃，"
        "系统在任务中失败）/ **blocked**（环境前置不满足：桌面锁、reset、起实例）/"
        " **paused**（护栏 6 空闲让位：检测到真实用户键鼠输入，本轮未跑）。"
    )
    lines.append(
        "* **success@1** = pass / (pass + fail + error)，attempt 级按组池化；"
        "**paused 与 blocked 从分母剔除**（用户让位与环境失败不是被测系统的失败）。"
    )
    lines.append(
        "* **success@3** = 一条任务全部 attempts 中至少 1 次 pass；"
        "overall 行按「轮×任务」对池化。"
    )
    lines.append(
        "* 单独计数：paused 次数、blocked 次数、人工接管次数（走到审批人面前的"
        "请求）、危险动作触发次数（影响面判定命中且被放行执行，**必须 0**）。"
    )
    lines.append(
        "* 全部 attempt 被 paused/blocked 吃掉的任务**不计入分母**（如实单独可见）；"
        "没跑的任务写「未跑」，不估算。judge 只读状态（UIA 控件值/文件内容/"
        "窗口标题/磁盘），不读截图；判分器负向对照见文末。"
    )
    lines.append("")
    if args.code_note:
        lines.append(f"代码版本注记：{args.code_note}")
        lines.append("")
    if args.void:
        lines.append(f"**作废轮次**：{args.void}")
        lines.append("")
    for note in override_notes:
        lines.append(f"**单任务替换**：{note}"
                     + (f"——{args.override_note}" if args.override_note else ""))
        lines.append("")

    # ---- 轮次覆盖 ---------------------------------------------------------
    lines.append("## 轮次覆盖")
    lines.append("")
    lines.append("| 轮次 | 任务覆盖 | pass | fail | error | paused | blocked | 危险动作 | 人工接管 |")
    lines.append("|---|---|---|---|---|---|---|---|---|")
    for name in round_names:
        summaries = per_round[name]
        statuses = [
            r.get("attempt_status")
            for s in summaries
            for r in s.get("results", [])
        ]
        counts = {k: statuses.count(k)
                  for k in ("pass", "fail", "error", "paused", "blocked")}
        danger = sum(
            r.get("danger_actions", 0)
            for s in summaries for r in s.get("results", [])
        )
        takeover = sum(
            r.get("human_takeover", 0)
            for s in summaries for r in s.get("results", [])
        )
        lines.append(
            f"| {name} | {len(summaries)}/30 | {counts['pass']} | {counts['fail']} "
            f"| {counts['error']} | {counts['paused']} | {counts['blocked']} "
            f"| {danger} | {takeover} |"
        )
    lines.append("")

    # ---- 池化总表 ----------------------------------------------------------
    lines.append("## 汇总（含类别与通道拆分）")
    lines.append("")
    lines.append("| 组 | n(轮×任务) | 可判分 | success@1 | success@3 | 平均步数 | 平均耗时(s) | 危险动作 | 人工接管 | paused | blocked |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|---|")

    def _row(label: str, bucket: dict) -> str:
        wall = bucket["avg_wall_ms"]
        return (
            f"| {label} | {bucket['n_tasks']} | {bucket['n_scorable_tasks']} "
            f"| {_fmt_rate(bucket['success_at_1'])} | {_fmt_rate(bucket['success_at_3'])} "
            f"| {bucket['avg_steps'] if bucket['avg_steps'] is not None else '—'} "
            f"| {round(wall / 1000.0, 1) if wall is not None else '—'} "
            f"| {bucket['danger_actions_total']} | {bucket['human_takeover_total']} "
            f"| {bucket['paused_attempts']} | {bucket['blocked_attempts']} |"
        )

    lines.append(_row("**整体**", overall["total"]))
    for category in _CATEGORY_ORDER:
        bucket = overall["by_category"].get(category)
        if bucket:
            lines.append(_row(category, bucket))
    for category, bucket in overall["by_category"].items():
        if category not in _CATEGORY_ORDER:
            lines.append(_row(category, bucket))
    lines.append("")
    lines.append("注：success@1/success@3 的 `x/y = z.zzz` 中，x/y 为原始计数、"
                 "z 为比率；「无可判分 attempt」表示该组全部 attempt 都是 "
                 "paused/blocked。")
    lines.append("")

    by_channel = overall["by_channel"]
    if by_channel:
        lines.append("### 通道拆分（步骤级）")
        lines.append("")
        lines.append("| 通道 | 步数 | 步骤成功 | 步骤成功率 |")
        lines.append("|---|---|---|---|")
        for channel in sorted(by_channel):
            bucket = by_channel[channel]
            rate = (bucket["ok"] / bucket["steps"]) if bucket["steps"] else 0.0
            lines.append(
                f"| {channel} | {bucket['steps']} | {bucket['ok']} "
                f"| {rate:.3f} |"
            )
        lines.append("")
        lines.append("注：本评测台的任务步骤走 UIA / 键盘 / 窗口标题通道；"
                     "视觉通道不在 W4 任务里（视觉配对实验见 W6 报告）。")
        lines.append("")

    # ---- 每任务明细 --------------------------------------------------------
    lines.append("## 每任务明细（attempt 状态原样列出）")
    lines.append("")
    lines.append("| 任务 | 类别 | " + " | ".join(round_names) + " | s@3（池化） |")
    lines.append("|---|---|" + "---|" * len(round_names) + "---|")

    all_ids: list[str] = []
    seen: set[str] = set()
    for name in round_names:
        for s in per_round[name]:
            if s["task"] not in seen:
                seen.add(s["task"])
                all_ids.append(s["task"])
    # 按类别分组排序，类别内部按 id。
    by_id: dict[str, dict] = {}
    for name in round_names:
        for s in per_round[name]:
            by_id.setdefault(s["task"], {"category": s["category"]})[name] = s
    all_ids.sort(key=lambda t: (list(_CATEGORY_ORDER).index(by_id[t]["category"])
                                if by_id[t]["category"] in _CATEGORY_ORDER else 9, t))
    for tid in all_ids:
        row = by_id[tid]
        cells = []
        any_pass = False
        scorable_any = False
        for name in round_names:
            s = row.get(name)
            if s is None:
                cells.append("未跑")
                continue
            statuses = ",".join(s.get("attempt_status", [])) or "—"
            cells.append(statuses)
            metrics_sc = [r.get("attempt_status") in ("pass", "fail", "error")
                          for r in s.get("results", [])]
            if any(metrics_sc):
                scorable_any = True
            if any(r.get("attempt_status") == "pass"
                   for r in s.get("results", [])):
                any_pass = True
        s3 = "—" if not scorable_any else ("✓" if any_pass else "✗")
        lines.append(
            f"| {tid} | {row['category']} | " + " | ".join(cells) + f" | {s3} |"
        )
    lines.append("")
    lines.append("注：状态串如 `pass,pass,paused` = 该轮 3 次 attempt 的原样状态；"
                 "「未跑」= 该轮没有这条任务的产物（不估算）。")
    lines.append("")
    lines.append("## 判分器负向对照")
    lines.append("")
    if args.negative_control:
        lines.append(f"**判分器负向对照：{args.negative_control}**")
        lines.append("")
        lines.append("对照内容：① judge 断言改必然不成立（c09 期望 392，实况 391）→ 必报 fail；"
                     "② 脏状态（reset 写错内容 / reset 不跑）→ 必报 fail 或 blocked；"
                     "③ 伪造错误状态（计算器打到 999，judge 期望 391）→ 必须拒绝。"
                     "驱动：`scripts/_w4_negative_control.py`，产物 `runs/w4-negative/`（gitignored）。")
    else:
        lines.append("（未跑——负向对照由 scripts/_w4_negative_control.py 产出后经 "
                     "--negative-control 注入。）")
    lines.append("")

    out_path = _ROOT / args.out
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"已写 {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

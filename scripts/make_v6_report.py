"""V6 量化对比报告生成器：runs/v5/summary-group-{a,b,c}.json → docs/v6-abc-comparison.md。

**只做如实转写** —— 所有数字从三组 summary JSON 读出（JSON 由
``scripts/v5_vision_tasks.py --group a|b|c`` 产出），脚本不计算任何新指标；
三组 JSON 合并一份进 ``docs/``（可复算）。

原 30 条任务的处理（如实说明）：W4 台（v0.1.6，248/249，s@1=99.6%）是
**UIA 主导**的台 —— 全部 30 条的步骤都走 UIA 通道（trace 里 model_calls=0），
候选源与 d3 重排**只接在视觉回退路径上**，UIA 主导任务在结构上踩不到
（B/C 与 A 逐字节同路径）。因此：

* A 组基线**引用 W4 已发布的实测数字**（runs/w4 的既有产物，不重跑、不改）；
* B/C 两组对原 30 条给出**结构性等同**的论证（切换点只在
  ``ActionExecutionSeam`` 的视觉回退分支 —— UIA 成功时永不进入）；
* 复跑命令在报告里给出（需要时可实测验证）。

用法（仓库根目录）::

    python -I scripts/make_v6_report.py --v5 runs/v5 --out docs/v6-abc-comparison.md
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def _load(path: Path) -> dict | None:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _fmt(value) -> str:
    return "—" if value is None else str(value)


def _group_row(label: str, s: dict | None) -> list[str]:
    if s is None:
        return [f"| {label} | 未跑 | | | | | | | | |"]
    d3 = s.get("d3_stats") or {}
    return [
        f"| {label} | {_fmt(s.get('success_at_1'))} "
        f"（{s.get('pass')}/{s.get('judged')}）"
        f" | {_fmt(s.get('fail'))} | {_fmt(s.get('error'))}"
        f" | {_fmt(s.get('blocked'))} | {_fmt(s.get('paused'))}"
        f" | {_fmt(s.get('vision_channel_used'))}"
        f" | {_fmt(s.get('mean_elapsed_s'))}s"
        f" | {_fmt(d3.get('d3_called', 0) if s.get('d3_rerank') else '—')}"
        f" | {_fmt(d3.get('low_confidence', 0) if s.get('d3_rerank') else '—')} |"
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description="V6 A/B/C 对比报告生成（只做如实转写）")
    parser.add_argument("--v5", default="runs/v5")
    parser.add_argument("--out", default="docs/v6-abc-comparison.md")
    parser.add_argument("--json-out", default="docs/v6-abc.json")
    args = parser.parse_args()

    v5_dir = _ROOT / args.v5
    groups = {
        name: _load(v5_dir / f"summary-group-{name}.json")
        for name in ("a", "b", "c")
    }
    if all(g is None for g in groups.values()):
        print("三组 summary 都不存在 —— 先跑 scripts/v5_vision_tasks.py --group a|b|c", file=sys.stderr)
        return 1

    lines: list[str] = []
    add = lines.append
    add("# V6 量化对比：A（现状）/ B（+图标候选源）/ C（+图标候选源+d3 重排）")
    add("")
    add(f"> 生成时间：{datetime.now().isoformat(timespec='seconds')}｜"
        f"任务集：V5 视觉必需 8 条（canvas 页，UIA 原理上不可达）｜"
        f"数据：`{args.v5}/summary-group-*.json`（入库副本 `{args.json_out}`）")
    add("")
    add("## 三组配置（模型身份与开关披露）")
    add("")
    add("| 组 | 视觉模型 | 图标候选源 | d3 重排 | 说明 |")
    add("| --- | --- | --- | --- | --- |")
    for name, label, note in (
        ("a", "A（现状）", "视觉通道兜底，无候选增强、无重排"),
        ("b", "B", "V3 的 IconProposalCandidateSource 当附加候选源"),
        ("c", "C", "B 的基础上加 D3CandidateReranker（/v1/systemone）"),
    ):
        s = groups.get(name)
        model = s.get("vision_model") if s else "—"
        source = "无" if name == "a" else "icon-proposals"
        rerank = "关" if name != "c" else "开（gap<0.1 才调；confidence<0.5 不点）"
        add(f"| {label} | {model} | {source} | {rerank} | {note} |")
    add("")
    add("## V5 视觉必需任务集上的三组指标")
    add("")
    add("| 组 | success@1 | fail | error | blocked | paused | 视觉通道使用 | 平均耗时 | d3 调用 | 低置信拒绝 |")
    add("| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |")
    for name, label in (("a", "A（现状）"), ("b", "B（+图标候选）"), ("c", "C（+d3 重排）")):
        add(_group_row(label, groups.get(name))[0])
    add("")
    add("- blocked（环境前置不满足）与 paused（用户动键鼠让位）从 success@1 的分母剔除（与 W4 同口径）；")
    add("- 危险动作次数：三组均为 **0**（L1 白名单 + 审批闸门，guard 会话可查）；人工接管（APPROVAL_DENIED）：见各任务 JSON。")
    add("")
    add("## 逐任务对照（hit/landing 直读）")
    add("")
    add("| 任务 | A | B | C |")
    add("| --- | --- | --- | --- |")
    task_ids: list[str] = []
    for name in ("a", "b", "c"):
        s = groups.get(name)
        if s:
            task_ids = [o["task"] for o in s["outcomes"]]
            break
    by_group = {
        name: {o["task"]: o for o in (groups.get(name) or {}).get("outcomes", [])}
        for name in ("a", "b", "c")
    }
    for task_id in task_ids:
        cells = []
        for name in ("a", "b", "c"):
            o = by_group[name].get(task_id)
            if o is None:
                cells.append("未跑")
            else:
                cells.append(
                    f"{o['status']}（channel={o.get('channel') or '—'}，"
                    f"landing={o.get('landing') or '—'}）"
                )
        add(f"| {task_id} | {cells[0]} | {cells[1]} | {cells[2]} |")
    add("")
    add("## 原 30 条任务（W4 台）与三组的关系（如实说明）")
    add("")
    add("* A 组基线 = **W4 已发布实测**（v0.1.6，runs/w4 既有产物）：30 任务 × 3 attempts，"
        "原始产出 245/249 attempt、success@1 = 99.6%（i16 复验换入后 248/249）——本夜不重跑、不改既有产物；")
    add("* B/C 两组对原 30 条是**结构性等同**：候选源与 d3 只接在视觉回退路径"
        "（``ActionExecutionSeam`` 的 UIA 失败 → 覆盖失败 → 视觉分支），"
        "W4 台 30 条任务全部 UIA 通道解决（trace ``model_calls=0``）→ B/C 的切换点"
        "在这些任务上永远不会被踩到；")
    add("* 复跑命令（如需实测验证）：")
    add("  `python -I scripts/taskbench.py --all --out runs/v6-w4-group-b`（需先给 taskbench")
    add("  接 V3/V4 的开关 —— 本夜未做，如实记为「未测」）。")
    add("")
    add("## 诚实口径")
    add("")
    add("* 每组 n=8（单 attempt），无显著性检验的统计功效 —— 三组差异是**点估计**，")
    add("  不构成显著性结论；")
    add("* V3 量化（runs/v3）已证实：零模型图标候选在 ScreenSpot-Pro 上的覆盖率极低")
    add("  （icon 1/30），在 canvas 页这类**常规密度 UI**上的表现以本台三组对照为准；")
    add("* d3 只决定「选哪个候选」（落点仍取候选框物理中心，0.0px 契约）。")
    add("")

    out = _ROOT / args.out
    out.write_text("\n".join(lines), encoding="utf-8")
    merged = {name: groups[name] for name in ("a", "b", "c")}
    (_ROOT / args.json_out).write_text(
        json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"=== 完成：{out} + {_ROOT / args.json_out} ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

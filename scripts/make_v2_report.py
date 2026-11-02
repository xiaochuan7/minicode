"""V2 视觉配对实验报告生成器：runs/v2/v2-paired.json → docs/v2-vision-paired.md。

**只做如实转写** —— 所有数字从配对 JSON 读出（JSON 由
``scripts/v2_paired_models.py`` 跑批产出），脚本不计算任何新指标；
配对 JSON 同时复制一份进 ``docs/``（入库要求：配对数据必须提交）。

用法（仓库根目录）::

    python -I scripts/make_v2_report.py --in runs/v2/v2-paired.json ^
        --out docs/v2-vision-paired.md --json-out docs/v2-paired.json
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def _pct(rate: float | None) -> str:
    return "—" if rate is None else f"{rate * 100:.2f}%"


def _ci(ci: list) -> str:
    """比率型 CI（0~1）→ 百分比区间。"""
    return f"[{_pct(ci[0])}, {_pct(ci[1])}]" if ci and ci[0] is not None else "—"


def _ci_pp(ci: list) -> str:
    """**百分点**型 CI（已经是 pp 数值）→ 原样格式化（不要再乘 100）。"""
    if not ci or ci[0] is None:
        return "—"
    return f"[{ci[0]:+.2f}, {ci[1]:+.2f}] pp"


def _pp(value: float | None) -> str:
    return "—" if value is None else f"{value:+.2f} pp"


def main() -> int:
    parser = argparse.ArgumentParser(description="V2 配对报告生成（只做如实转写）")
    parser.add_argument("--in", dest="source", default="runs/v2/v2-paired.json")
    parser.add_argument("--out", default="docs/v2-vision-paired.md")
    parser.add_argument("--json-out", default="docs/v2-paired.json")
    args = parser.parse_args()

    source = _ROOT / args.source
    report = json.loads(source.read_text(encoding="utf-8"))
    stats = report["paired"]
    arm_a, arm_b = report["arm_a"], report["arm_b"]
    rate = report["rate_control"]
    n = stats["samples"]
    hits_a = sum(1 for o in report["outcomes"]["arm_a"] if o["hit"])
    hits_b = sum(1 for o in report["outcomes"]["arm_b"] if o["hit"])

    lines: list[str] = []
    add = lines.append
    add("# V2 视觉配对实验：决策层 LLM vs 视觉 VLM（换模型，n=60）")
    add("")
    add(f"> 生成时间：{datetime.now().isoformat(timespec='seconds')}｜"
        f"数据来源：`{args.source}`（入库副本：`{args.json_out}`，可复算）")
    add("")
    add("## 一句话结论")
    add("")
    add(
        f"同批 60 条（ScreenSpot-Pro，与 W6 同批同提示词同判定），"
        f"对照臂 **{arm_a['model']}**（opencode，线上现状）命中 **{hits_a}/{n}**，"
        f"实验臂 **{arm_b['model']}**（Radeon）命中 **{hits_b}/{n}**；"
        f"Δ = {_pp(stats['delta_pp'])}（95% CI {_ci_pp(stats['delta_ci95_pp'])}），"
        f"mid-p McNemar p = {stats['mcnemar_midp_p']:.4f}，"
        f"MDE = {stats['mde_pp']:.2f} pp"
        f"（|Δ| {'达到' if stats['delta_at_or_above_mde'] else '**未达到**'} MDE）。"
    )
    add("")
    add("## 两臂身份（模型身份披露）")
    add("")
    add("| 臂 | 模型 | 网关主机 | 来源 |")
    add("| --- | --- | --- | --- |")
    add(f"| A（对照） | {arm_a['model']} | {arm_a['endpoint_host']} | {arm_a['source']} |")
    add(f"| B（实验） | {arm_b['model']} | {arm_b['endpoint_host']} | 仓库 .env 的视觉键组 |")
    add("")
    add(f"- 提示词契约：{report['prompt_contract']}")
    add(f"- 判定口径：{report['judge']}")
    add(f"- 抽样：每层 {report['per_stratum']}、seed={report['seed']}（与 W6 完全同批）")
    add("")
    add("## 配对统计（`eval/sspro.py::paired_comparison` 一次给齐）")
    add("")
    add("| 指标 | 值 |")
    add("| --- | --- |")
    add(f"| n（配对样本） | {n} |")
    add(f"| 臂 A 命中（Wilson 95% CI） | {hits_a}/{n}（{_ci(stats['wilson_left'])}） |")
    add(f"| 臂 B 命中（Wilson 95% CI） | {hits_b}/{n}（{_ci(stats['wilson_right'])}） |")
    table = stats["table"]
    add(
        f"| 2×2 配对表 | 双中 {table['both_hit']}｜A 中 B 不中 {table['left_only']}"
        f"｜B 中 A 不中 {table['right_only']}｜双不中 {table['both_miss']} |"
    )
    add(f"| Δ（B − A） | {_pp(stats['delta_pp'])} |")
    add(f"| 配对 bootstrap 95% CI | {_ci_pp(stats['delta_ci95_pp'])} |")
    add(f"| mid-p McNemar（主报） | p = {stats['mcnemar_midp_p']:.4f} |")
    add(f"| exact McNemar（保守对照） | p = {stats['mcnemar_exact_p']:.4f} |")
    add(f"| χ²+连续性校正（保守对照） | p = {stats['mcnemar_chisq_cc_p']:.4f} |")
    add(f"| 不一致对占比（噪声地板） | {_pct(stats['discordance'])} |")
    add(f"| MDE（本样本量能判出的最小效应） | {stats['mde_pp']:.2f} pp |")
    add("")
    add("## 控速与限流（429 与模型失败分开计数）")
    add("")
    add(f"- 并发 ≤ {rate['workers']}，批间 {rate['batch_sleep_s']}s；"
        f"重试策略：{rate['retry_policy']}")
    req = rate["arm_b_requests"]
    add(
        f"- 最后一轮跑批的臂 B 请求：{req['requests']} 次，"
        f"其中 429 限流 **{req['rate_limited_429']}**、传输失败 {req['transport_errors']}"
        f"（p50 {req['latency_ms_p50']}ms / p95 {req['latency_ms_p95']}ms）、"
        f"HTTP 200 但无文本 {req['no_text_output']}"
    )
    add(f"- {rate['note']}")
    add("")
    add("## 诚实口径")
    add("")
    add("* **单级对比**（只比整屏粗定位那一级）；")
    add("* 两臂提示词**逐字相同**（生产粗定位提示词，像素契约），未为任何一臂特调；")
    add("* 单臂绝对分不构成结论 —— 结论只看配对差与它的 CI；")
    add("* n=60 的 MDE 较大：|Δ| 未达 MDE 时，正确读法是「**样本量不够**」，")
    add("  不是「没效果」。")
    error_b = [
        o for o in report["outcomes"]["arm_b"] if (o.get("error") or "").strip()
    ]
    add(
        f"* 臂 B 有 {len(error_b)} 条调用失败/坐标越界"
        f"（{', '.join(sorted({(o.get('error') or '').split(':')[0] for o in error_b})) or '无'}）"
        "—— **保守口径**：计入命中率分母（ miss ），并在此单独披露；"
        "429 限流次数另见控速表，不计入模型失败。"
    )
    add("")
    ( _ROOT / args.out).write_text("\n".join(lines), encoding="utf-8")
    shutil.copyfile(source, _ROOT / args.json_out)
    print(f"=== 完成：{_ROOT / args.out} + {_ROOT / args.json_out} ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

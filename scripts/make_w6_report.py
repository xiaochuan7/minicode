"""W6 视觉配对实验报告生成器：runs/w6/w6-paired.json → docs/w6-vision-paired.md。

**只做如实转写**——所有数字从配对 JSON 读出（该 JSON 由
``scripts/w6_paired_coords.py`` 跑批产出），脚本不计算任何新指标；
配对 JSON 同时复制一份进 ``docs/``（入库要求：配对数据必须提交）。

用法（仓库根目录）::

    python -I scripts/make_w6_report.py --in runs/w6/w6-paired.json ^
        --out docs/w6-vision-paired.md --json-out docs/w6-paired.json
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def _pct(rate: float) -> str:
    return f"{rate * 100:.2f}%"


def _ci(ci: list) -> str:
    return f"[{_pct(ci[0])}, {_pct(ci[1])}]" if ci and ci[0] is not None else "—"


def _strata_table(outcomes: dict) -> list[str]:
    """按 platform × ui_type 的分层数（次要分析）。"""
    buckets: dict[tuple, dict] = defaultdict(lambda: {"n": 0, "pixel": 0, "norm": 0})
    for pixel, norm in zip(outcomes["pixel"], outcomes["norm_0_1000"], strict=True):
        key = (pixel.get("platform", "?"), pixel.get("ui_type", "?"))
        bucket = buckets[key]
        bucket["n"] += 1
        bucket["pixel"] += 1 if pixel.get("hit") else 0
        bucket["norm"] += 1 if norm.get("hit") else 0
    lines = [
        "| 分层（platform × ui_type） | n | pixel 命中 | 0-1000 命中 | Δ（百分点） |",
        "|---|---|---|---|---|",
    ]
    for (platform, ui_type), b in sorted(buckets.items()):
        delta = (b["norm"] - b["pixel"]) / b["n"] * 100 if b["n"] else 0.0
        lines.append(
            f"| {platform} / {ui_type} | {b['n']} "
            f"| {b['pixel']}/{b['n']} | {b['norm']}/{b['n']} | {delta:+.1f} |"
        )
    return lines


def main() -> int:
    parser = argparse.ArgumentParser(description="W6 配对实验报告（docs/）")
    parser.add_argument("--in", dest="source", default="runs/w6/w6-paired.json")
    parser.add_argument("--out", default="docs/w6-vision-paired.md")
    parser.add_argument("--json-out", default="docs/w6-paired.json")
    args = parser.parse_args()

    report = json.loads((_ROOT / args.source).read_text(encoding="utf-8"))
    stats = report["paired"]
    outcomes = report["outcomes"]
    n = stats["samples"]
    table = stats["table"]

    errors = {
        arm: sum(1 for o in outcomes[arm] if (o.get("error") or "").strip())
        for arm in ("pixel", "norm_0_1000")
    }
    hits = {
        arm: sum(1 for o in outcomes[arm] if o.get("hit"))
        for arm in ("pixel", "norm_0_1000")
    }

    lines = [
        "# W6 视觉坐标契约配对实验（ScreenSpot-Pro）",
        "",
        f"生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M')}；"
        "生成器：`scripts/make_w6_report.py`（从配对 JSON 如实转写，可复现）。",
        "",
        "## 模型身份（如实披露）",
        "",
        f"* **model id**：`{report['model']}`；**端点**：`{report['endpoint']}`"
        "（OpenAI 兼容 `/chat/completions`；密钥在仓库外 .env，不入库）。",
        f"* **采样参数**：推理档位 effort=`{report['effort']}`（仓库端点的档位选择），"
        f"输出预算 max_tokens={report['max_tokens']}，温度等未传即服务端默认。",
        "* **提示词**：生产粗定位提示词（`ground/prompt.py::coarse_prompt`）逐字——"
        "两臂**唯一差异**是坐标声明一行（臂 A「坐标是这张图的像素」→ 臂 B"
        "「坐标是 0~1000 的归一化坐标」）；其余逐字相同。",
        "",
        "## 数据与判定口径",
        "",
        f"* 数据集：{report['dataset']}（MIT，非 gated；本地存于仓库外目录，"
        "本仓库不含任何图片）。",
        f"* 样本：{report['n_samples']} 条（每层 {report['per_stratum']}、"
        f"seed={report['seed']}，`stratified_subset` 确定性抽样）。",
        "* 判定：官方口径——落点落在 GT bbox 内（Surface 空间，等比缩放下与"
        "归一化判定等价）。**两臂同一判定**。",
        "* 设计：同一张缩放后的模型输入图、同一模型、同一采样参数，逐条配对；"
        "**本报告不拿单臂绝对分当结论**——单臂数字只用于配对上下文。",
        "",
        "## 结果（配对）",
        "",
        "| 臂 | 命中 | 命中率 | Wilson 95% CI |",
        "|---|---|---|---|",
        f"| pixel（现状） | {hits['pixel']}/{n} | {_pct(hits['pixel'] / n if n else 0)} "
        f"| {_ci([x * 100 for x in stats['wilson_left']])} |",
        f"| 0-1000 归一化 | {hits['norm_0_1000']}/{n} | {_pct(hits['norm_0_1000'] / n if n else 0)} "
        f"| {_ci([x * 100 for x in stats['wilson_right']])} |",
        "",
        f"* **2×2 配对表**（n={n}）：双中 {table['both_hit']} · 仅 pixel 中 "
        f"{table['left_only']} · 仅 0-1000 中 {table['right_only']} · 双不中 "
        f"{table['both_miss']}。",
        f"* **Δ**（0-1000 − pixel）：**{stats['delta_pp']:+.2f} 百分点**，"
        f"配对 bootstrap 95% CI {stats['delta_ci95_pp'][0]:+.2f} ~ "
        f"{stats['delta_ci95_pp'][1]:+.2f} 百分点"
        f"（{'不含 0' if stats['ci_excludes_zero'] else '含 0'}）。",
        f"* **mid-p McNemar（主报）**：p = {stats['mcnemar_midp_p']:.4f}"
        f"（保守对照：exact p = {stats['mcnemar_exact_p']:.4f}，"
        f"χ²+Yates p = {stats['mcnemar_chisq_cc_p']:.4f}）。",
        f"* 不一致对占比（噪声地板）π̂_d = {stats['discordance']:.3f}；"
        f"本样本量 MDE ≈ "
        f"{stats['mde_pp']:.1f} 百分点"
        + ("（|Δ| 未达 MDE——**样本量不够**下结论，不是「没差异」）"
           if not stats["delta_at_or_above_mde"] and stats["mde_pp"] is not None
           else "（|Δ| 达到 MDE）") + "。",
        "",
        "### 分层数（次要，样本小只看方向）",
        "",
        *_strata_table(outcomes),
        "",
        "## 诚实注记",
        "",
        f"* 调用错误：pixel 臂 {errors['pixel']} 条、0-1000 臂 {errors['norm_0_1000']}"
        " 条（计入未命中方向，明细在配对 JSON 的 outcomes 里）。",
        "* **单级对比**：只比整屏粗定位那一级；两级路径里 crop 精定位的坐标语言是"
        "另一个实验，未混入。",
        "* 两臂各按其**声明的契约**解释回答；未做逐尺寸标定探针（生产行为的标定是"
        "另一条线，此前已有记录）。",
        "* 原始样本与逐条回答落在 `runs/w6/`（gitignored，仓库外图片不出库）；"
        "**配对 JSON 已入库**为 `docs/w6-paired.json`。",
        "",
    ]
    out_path = _ROOT / args.out
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    json_out = _ROOT / args.json_out
    shutil.copyfile(_ROOT / args.source, json_out)
    print(f"已写 {out_path} 与 {json_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""生成 README 的 ScreenSpot-Pro benchmark 横向柱状图（docs/assets/benchmark.png）。

数据来源：README「实测能力」节的官方口径（ScreenSpot-Pro bbox 命中，n=183 分层抽样），
Wilson 95% CI 上下界一并写死在此脚本中，保证可复现。

用法（在工作区根目录）：
    python scripts/make_benchmark_chart.py
"""

from __future__ import annotations

import os

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

# ---------- 数据（来自 README「实测能力」节，勿改） ----------
# (标签, 命中率 %, CI 下界 %, CI 上界 %)
DATA = [
    ("文本类（n=96）", 69.79, 59.99, 78.07),
    ("整体（n=183）", 59.02, 51.78, 65.89),
    ("图标类（n=87）", 47.13, 36.98, 57.51),
    ("粗定位基线（n=183）", 43.17, 36.21, 50.41),
]

SUB_TITLE = "ScreenSpot-Pro · n=183 · 官方 bbox 命中"

# ---------- 视觉（与 docs/assets/hero.png 同色板：近黑底 + 青色/琥珀强调） ----------
BG = "#0c1015"          # 近黑背景
FG = "#e8eef5"          # 主文字
FG_DIM = "#8b98a5"      # 次要文字
GRID = "#1f2733"        # 网格线
BAR_CYAN = "#4f9db8"    # 青色（与 mermaid core 一致）
BAR_AMBER = "#c99a4a"   # 琥珀（整体指标高亮，与 mermaid dev 一致）
ERR = "#a9b8c4"

plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "Arial"]
plt.rcParams["axes.unicode_minus"] = False


def main() -> None:
    labels = [d[0] for d in DATA]
    values = [d[1] for d in DATA]
    lo = [d[2] for d in DATA]
    hi = [d[3] for d in DATA]

    fig, ax = plt.subplots(figsize=(16, 10), dpi=100)  # 1600x1000 px
    fig.patch.set_facecolor(BG)
    ax.set_facecolor(BG)
    fig.subplots_adjust(left=0.22)  # 给最长 y 轴标签（粗定位基线）留足左边距

    y = range(len(labels))
    colors = [BAR_AMBER if lbl.startswith("整体") else BAR_CYAN for lbl in labels]

    bars = ax.barh(
        list(y),
        values,
        height=0.55,
        color=colors,
        xerr=[
            [values[i] - lo[i] for i in y],
            [hi[i] - values[i] for i in y],
        ],
        error_kw=dict(ecolor=ERR, elinewidth=1.4, capsize=5, capthick=1.4),
        zorder=3,
    )
    _ = bars  # noqa: F841

    ax.set_yticks(list(y))
    ax.set_yticklabels(labels, fontsize=15, color=FG)
    ax.invert_yaxis()
    ax.set_xlim(0, 100)
    ax.set_xticks(range(0, 101, 20))
    ax.tick_params(axis="x", colors=FG_DIM, labelsize=13)
    ax.set_xlabel("bbox 命中率（%）", fontsize=14, color=FG_DIM, labelpad=10)

    for spine in ax.spines.values():
        spine.set_visible(False)
    ax.grid(axis="x", color=GRID, linewidth=0.8, alpha=0.9)
    ax.set_axisbelow(True)

    for i in y:
        ax.text(
            hi[i] + 1.6,
            i,
            f"{values[i]:.2f}%",
            va="center",
            ha="left",
            fontsize=16,
            fontweight="bold",
            color=FG,
        )
        ax.text(
            hi[i] + 1.6,
            i - 0.28,
            f"[{lo[i]:.2f}%, {hi[i]:.2f}%]",
            va="center",
            ha="left",
            fontsize=10.5,
            color=FG_DIM,
        )

    ax.set_title("实测能力", loc="left", x=0.015, pad=2, fontsize=24, fontweight="bold", color=FG)
    ax.text(0.015, 1.05, SUB_TITLE, transform=ax.transAxes, fontsize=14, color=FG_DIM)

    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "docs", "assets", "benchmark.png")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    fig.savefig(out, dpi=100, facecolor=fig.get_facecolor())
    w, h = fig.get_size_inches()
    print(f"saved {os.path.normpath(out)} {int(w * 100)}x{int(h * 100)}")


if __name__ == "__main__":
    main()

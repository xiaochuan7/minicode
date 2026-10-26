#!/usr/bin/env python3
"""生成 README 用的三张实测数据图（matplotlib 深色主题，中文字体 Microsoft YaHei）。

数据来源：runs/v2-eval-2016-control/sspro-report.json
（ScreenSpot-Pro 官方 bbox 命中口径，n=183 分层抽样）。数字写死在本脚本里以保证可复现，
每个数据点的来源字段都标注在对应条目的行尾注释中。

用法::

    python scripts/make_figures.py

输出：docs/assets/by-overall.png、docs/assets/by-category.png、docs/assets/by-platform.png
"""

from pathlib import Path

import matplotlib.pyplot as plt

# ── 视觉系统（与 docs/assets 下其余视觉资产同色板）──────────────────────────
BG = "#0d1117"        # 近黑底
FG = "#e6edf3"        # 主文字
MUTED = "#9aa7b3"     # 次要文字
GRID = "#8b949e"      # 网格线（低透明度使用）
CYAN = "#22d3ee"      # 主色：青
AMBER = "#f5a623"     # 强调色：琥珀（仅用于短板高亮）

ASSETS = Path(__file__).resolve().parent.parent / "docs" / "assets"

plt.rcParams.update({
    "font.family": ["Microsoft YaHei", "SimHei", "sans-serif"],
    "axes.unicode_minus": False,
    "figure.facecolor": BG,
    "axes.facecolor": BG,
    "savefig.facecolor": BG,
    "text.color": FG,
    "axes.labelcolor": FG,
    "xtick.color": MUTED,
    "ytick.color": FG,
    "axes.edgecolor": GRID,
})

# ── 数据（ScreenSpot-Pro 官方 bbox 命中口径，n=183 分层抽样）────────────────
# 条目：(标签, 命中率%, Wilson 95% CI 下界, 上界, 计数)
# 来源字段：comparison.overall.always = 108/183 = 59.02%
#           accuracy_by_ui_type.text  = 67/96  = 69.79%
#           accuracy_by_ui_type.icon  = 41/87  = 47.13%
#           粗定位基线（coarse-only） = 79/183 = 43.17%
OVERALL = [
    ("整体", 59.02, 51.78, 65.89, "108/183"),
    ("文本类", 69.79, 59.99, 78.07, "67/96"),
    ("图标类", 47.13, 36.98, 57.51, "41/87"),
    ("粗定位基线", 43.17, 36.21, 50.41, "79/183"),
]

# 条目：(类别, 命中率%, n)
# 来源字段：accuracy_by_category（runs/v2-eval-2016-control/sspro-report.json）
CATEGORY = [
    ("Office", 87.5, 16),
    ("Dev", 62.5, 16),
    ("OS", 61.8, 76),
    ("Scientific", 56.7, 30),
    ("CAD", 44.4, 9),
    ("Creative", 44.4, 36),
]

# 条目：(平台, 命中率%, n)
# 来源字段：accuracy_by_platform（windows 为当前短板，用琥珀色高亮）
PLATFORM = [
    ("linux", 70.0, 50),
    ("macos", 67.2, 64),
    ("windows", 43.5, 69),
]


def _style_axes(ax):
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.xaxis.grid(True, color=GRID, alpha=0.15, linewidth=0.8)
    ax.set_axisbelow(True)


def fig_by_overall():
    rows = OVERALL[::-1]
    labels = [r[0] for r in rows]
    values = [r[1] for r in rows]
    err_low = [r[1] - r[2] for r in rows]
    err_high = [r[3] - r[1] for r in rows]

    fig, ax = plt.subplots(figsize=(9.6, 3.4), dpi=200)
    # 粗定位基线（最后一行）用琥珀色，其余用青色
    colors = [AMBER if r[0] == "粗定位基线" else CYAN for r in rows]
    bars = ax.barh(labels, values, color=colors, height=0.62,
                   xerr=[err_low, err_high], error_kw={"ecolor": FG, "elinewidth": 1.1,
                                                       "capsize": 3, "alpha": 0.85})
    for row, bar in zip(rows, bars, strict=True):
        ax.text(bar.get_width() + 0.8, bar.get_y() + bar.get_height() / 2,
                f"{row[1]:.2f}%  ({row[4]})", va="center", fontsize=10.5, color=FG)
    ax.set_xlim(0, 88)
    ax.set_xlabel("命中率（%），误差棒为 Wilson 95% CI", fontsize=10, color=MUTED)
    ax.set_title("ScreenSpot-Pro 定位命中率（n=183 分层抽样，官方 bbox 命中口径）",
                 fontsize=12, color=FG, pad=12)
    _style_axes(ax)
    fig.tight_layout()
    fig.savefig(ASSETS / "by-overall.png")
    plt.close(fig)


def fig_by_category():
    rows = CATEGORY[::-1]
    labels = [f"{r[0]}" for r in rows]
    values = [r[1] for r in rows]

    fig, ax = plt.subplots(figsize=(9.6, 3.6), dpi=200)
    # CAD 与 Creative 是弱项组，用琥珀色
    colors = [AMBER if r[0] in ("CAD", "Creative") else CYAN for r in rows]
    bars = ax.barh(labels, values, color=colors, height=0.6)
    for row, bar in zip(rows, bars, strict=True):
        ax.text(bar.get_width() + 1.0, bar.get_y() + bar.get_height() / 2,
                f"{row[1]:.1f}%  (n={row[2]})", va="center", fontsize=10.5, color=FG)
    ax.set_xlim(0, 104)
    ax.set_xlabel("命中率（%）", fontsize=10, color=MUTED)
    ax.set_title("按应用类别（同口径；琥珀色为弱项组）", fontsize=12, color=FG, pad=12)
    _style_axes(ax)
    fig.tight_layout()
    fig.savefig(ASSETS / "by-category.png")
    plt.close(fig)


def fig_by_platform():
    rows = PLATFORM[::-1]
    labels = [r[0] for r in rows]
    values = [r[1] for r in rows]

    fig, ax = plt.subplots(figsize=(9.6, 2.7), dpi=200)
    # windows 明显低于 linux / macos，用琥珀色高亮
    colors = [AMBER if r[0] == "windows" else CYAN for r in rows]
    bars = ax.barh(labels, values, color=colors, height=0.56)
    for row, bar in zip(rows, bars, strict=True):
        ax.text(bar.get_width() + 1.0, bar.get_y() + bar.get_height() / 2,
                f"{row[1]:.1f}%  (n={row[2]})", va="center", fontsize=10.5, color=FG)
    ax.set_xlim(0, 88)
    ax.set_xlabel("命中率（%）", fontsize=10, color=MUTED)
    ax.set_title("按平台（同口径；windows 为当前短板）", fontsize=12, color=FG, pad=12)
    _style_axes(ax)
    fig.tight_layout()
    fig.savefig(ASSETS / "by-platform.png")
    plt.close(fig)


def main() -> None:
    ASSETS.mkdir(parents=True, exist_ok=True)
    fig_by_overall()
    fig_by_category()
    fig_by_platform()
    for name in ("by-overall.png", "by-category.png", "by-platform.png"):
        path = ASSETS / name
        print(f"{path.relative_to(path.parents[2])}: {path.stat().st_size / 1024:.0f} KB")


if __name__ == "__main__":
    main()

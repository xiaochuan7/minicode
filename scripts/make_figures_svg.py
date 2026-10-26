#!/usr/bin/env python3
"""README 数据图生成器：纯手写 SVG，无第三方依赖。

数字唯一来源：runs/v2-eval-2016-control/sspro-report.json（已固化在下面的常量里）。
改数字只改常量；几何全部由代码算，避免手调。

用法：python scripts/make_figures_svg.py
"""

from __future__ import annotations

from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "docs" / "assets"

FONT_MONO = '"SF Mono", "Cascadia Mono", Consolas, Menlo, monospace'
FONT_SANS = '"Inter", -apple-system, "Segoe UI", "Microsoft YaHei", sans-serif'

# ---- 配色 ----
BG = "#FFFFFF"
BORDER = "#E4E8EE"
TRACK = "#F1F4F8"
TEXT = "#0F172A"
MUTED = "#64748B"
ACCENT = "#0EA5E9"
WARM = "#F97316"

# ---- 唯一事实来源 ----
OVERALL = [
    ("整体", 59.02, 108, 183, (51.78, 65.89)),
    ("文本类", 69.79, 67, 96, (59.99, 78.07)),
    ("图标类", 47.13, 41, 87, (36.98, 57.51)),
    ("粗定位基线", 43.17, 79, 183, (36.21, 50.41)),
]
CATEGORY = [("Office", 87.5, 16), ("Dev", 62.5, 16), ("OS", 61.8, 76),
            ("Scientific", 56.7, 30), ("CAD", 44.4, 9), ("Creative", 44.4, 36)]
PLATFORM = [("linux", 70.0, 50), ("macos", 67.2, 64), ("windows", 43.5, 69)]
WEAK = {"图标类", "粗定位基线", "CAD", "Creative", "windows"}


def esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def head(w: int, h: int, kicker: str, title: str) -> list[str]:
    return [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}" role="img" aria-label="{esc(title)}">',
        "  <defs>",
        f'    <style>.mono{{font-family:{FONT_MONO};}}.sans{{font-family:{FONT_SANS};}}</style>',
        "  </defs>",
        f'  <rect width="{w}" height="{h}" rx="16" fill="{BG}" stroke="{BORDER}"/>',
        f'  <text class="mono" x="40" y="46" fill="{ACCENT}" font-size="13" letter-spacing="3" fill-opacity="0.9">{esc(kicker)}</text>',
        f'  <text class="sans" x="40" y="88" fill="{TEXT}" font-size="24" font-weight="600">{esc(title)}</text>',
    ]


def overall() -> str:
    w, h = 1400, 440
    x0, x1, bh = 300, 1040, 26
    span = x1 - x0
    rows: list[str] = []
    y = 142
    for name, pct, hit, total, (lo, hi) in OVERALL:
        color = WARM if name in WEAK else ACCENT
        bw = round(span * pct / 100)
        rows.append(f'  <text class="sans" x="{x0 - 30}" y="{y + bh - 4}" fill="{TEXT}" font-size="22" text-anchor="end">{esc(name)}</text>')
        rows.append(f'  <rect x="{x0}" y="{y}" width="{span}" height="{bh}" rx="13" fill="{TRACK}"/>')
        rows.append(f'  <rect x="{x0}" y="{y}" width="{bw}" height="{bh}" rx="13" fill="{color}"/>')
        cxo, cxi, cy = x0 + round(span * lo / 100), x0 + round(span * hi / 100), y + bh + 12
        rows.append(f'  <line x1="{cxo}" y1="{cy}" x2="{cxi}" y2="{cy}" stroke="{MUTED}" stroke-width="1.2"/>')
        rows.append(f'  <line x1="{cxo}" y1="{cy - 4}" x2="{cxo}" y2="{cy + 4}" stroke="{MUTED}" stroke-width="1.2"/>')
        rows.append(f'  <line x1="{cxi}" y1="{cy - 4}" x2="{cxi}" y2="{cy + 4}" stroke="{MUTED}" stroke-width="1.2"/>')
        rows.append(f'  <text class="mono" x="{x1 + 34}" y="{y + bh - 2}" fill="{TEXT}" font-size="25" font-weight="700">{pct:.2f}%</text>')
        rows.append(f'  <text class="mono" x="{x1 + 34}" y="{y + bh + 20}" fill="{MUTED}" font-size="13">{hit}/{total} · CI {lo:.2f}–{hi:.2f}</text>')
        y += 72
    return "\n".join(head(w, h, "GROUNDING · N=183 · OFFICIAL BBOX HIT", "ScreenSpot-Pro 定位命中率") + rows) + "\n</svg>\n"


def simple(data: list[tuple], title: str, kicker: str, note: str) -> str:
    w, bh = 1400, 24
    h = 124 + len(data) * 58 + 40
    x0, x1 = 220, 1040
    span = x1 - x0
    rows: list[str] = []
    y = 128
    for name, pct, n in data:
        color = WARM if name in WEAK else ACCENT
        bw = round(span * pct / 100)
        rows.append(f'  <text class="mono" x="{x0 - 26}" y="{y + bh - 4}" fill="{TEXT}" font-size="20" text-anchor="end">{esc(name)}</text>')
        rows.append(f'  <rect x="{x0}" y="{y}" width="{span}" height="{bh}" rx="12" fill="{TRACK}"/>')
        rows.append(f'  <rect x="{x0}" y="{y}" width="{bw}" height="{bh}" rx="12" fill="{color}"/>')
        rows.append(f'  <text class="mono" x="{x0 + bw + 18}" y="{y + bh - 4}" fill="{color}" font-size="20" font-weight="700">{pct}%</text>')
        rows.append(f'  <text class="mono" x="{x0 + bw + 122}" y="{y + bh - 4}" fill="{MUTED}" font-size="13">n={n}</text>')
        y += 58
    rows.append(f'  <text class="mono" x="{x0}" y="{h - 22}" fill="{MUTED}" font-size="13">{esc(note)}</text>')
    return "\n".join(head(w, h, kicker, title) + rows) + "\n</svg>\n"


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "chart-overall.svg").write_text(overall(), encoding="utf-8")
    (OUT / "chart-category.svg").write_text(
        simple(CATEGORY, "按应用类别", "CATEGORY · SAME PROTOCOL",
               "橙色 = 弱项组（CAD n=9、Creative n=36，单组样本小，结论要打折）"), encoding="utf-8")
    (OUT / "chart-platform.svg").write_text(
        simple(PLATFORM, "按平台", "PLATFORM · SAME PROTOCOL",
               "橙色 = 当前最短的板（windows n=69）"), encoding="utf-8")
    for p in sorted(OUT.glob("chart-*.svg")):
        print(f"{p.name}: {p.stat().st_size // 1024} KB")


if __name__ == "__main__":
    main()

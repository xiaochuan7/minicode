"""**落点误差的统一口径**（核心功能收口方案 D0）。

我们内部对「落点误差」一直有**两套口径**，历史数字因此互相打架：

* **L0 / L1**（:mod:`minicode.ground.stats`）——
  **分级**误差：粗定位点 vs 真值、精定位落点 vs 真值；
* **落点偏差** —— **最终落点** vs 真值。

两者在「**门控未命中 → 落点直接取粗定位点**」时**数值不同**（那条路径上
L1 = L0，而落点偏差也 = L0；但门控命中时 L1 ≠ 落点偏差，因为落点还可能
**回落**到粗定位点）。口径不统一 → 验收线不可判。

**本模块给唯一口径**：

======================  ==================================================
量                      定义
======================  ==================================================
``landing_error_px``    **落点误差** = ‖**最终落点** − 真值点‖
``coarse_error_px``    **L0 误差** = ‖粗定位点 − 真值点‖
``refine_error_px``    **L1 误差** = ‖精定位落点 − 真值点‖（**只在精定位真开了时才有**）
======================  ==================================================

单位一律**物理像素**；真值一律来自**独立来源**（UIA 控件框中心 /
ScreenSpot-Pro 的 GT 框中心）。报告里**同时**给出「精定位开启率」与
「回落率」—— 它们就是两套口径差异的来源，不给出就没法对账。

命令行::

    $env:PYTHONPATH="src"; python -m minicode.ground.caliber \\
        --sspro-report runs/m8/sspro-report.json --dataset .scratch/sspro \\
        --arm on_demand --out runs/caliber.json
"""

from __future__ import annotations

import json
import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .stats import ErrorDistribution, summarize_errors

__all__ = [
    "CaliberSample",
    "CaliberReport",
    "coarse_error_px",
    "landing_error_px",
    "refine_error_px",
    "summarize_samples",
    "samples_from_sspro_report",
    "samples_from_bench_report",
    "main",
]


@dataclass(frozen=True)
class CaliberSample:
    """**一个测量样本**（统一口径的输入）。

    * ``truth`` —— 真值点（物理像素）。**独立来源**。
    * ``coarse_point`` —— L0 粗定位点。
    * ``landing_point`` —— **最终落点**（模型与门控共同决定的落点）。
    * ``refine_enabled`` —— 这一轮**有没有真的开精定位**（门控命中或门控关）。
    * ``fallback`` —— 精定位开了但**回落**到了粗定位点。
    """

    name: str
    truth: tuple[float, float] | None = None
    coarse_point: tuple[float, float] | None = None
    landing_point: tuple[float, float] | None = None
    refine_enabled: bool = False
    fallback: bool = False
    source: str = ""
    #: **已经算好的误差**（记录里直接存了误差、没存点时用它）。
    precomputed_coarse_error_px: float | None = None
    precomputed_refine_error_px: float | None = None


def _distance(a: tuple[float, float] | None, b: tuple[float, float] | None) -> float | None:
    if a is None or b is None:
        return None
    return math.hypot(a[0] - b[0], a[1] - b[1])


def landing_error_px(sample: CaliberSample) -> float | None:
    """**落点误差** = ‖最终落点 − 真值点‖（统一口径的那一个数）。"""
    return _distance(sample.landing_point, sample.truth)


def coarse_error_px(sample: CaliberSample) -> float | None:
    """**L0 误差** = ‖粗定位点 − 真值点‖。"""
    value = _distance(sample.coarse_point, sample.truth)
    return sample.precomputed_coarse_error_px if value is None else value


def refine_error_px(sample: CaliberSample) -> float | None:
    """**L1 误差** = ‖精定位落点 − 真值点‖。

    ⚠️ **只在精定位真的开了的时候才有**：门控未命中时 L1 根本没发生，
    此时报 ``None``（**不是 0** —— 0 会被误读成「精定位很准」）。
    """
    if not sample.refine_enabled:
        return None
    value = sample.precomputed_refine_error_px
    if value is not None:
        return value
    # 精定位开了但回落了 → 最终落点就是粗定位点，L1 误差无从谈起。
    if sample.fallback:
        return None
    return _distance(sample.landing_point, sample.truth)


@dataclass(frozen=True)
class CaliberReport:
    """**统一口径下的落点误差报告**。"""

    source: str
    samples: int
    landing: ErrorDistribution | None
    coarse: ErrorDistribution | None
    refine: ErrorDistribution | None
    refine_enabled: int
    fallbacks: int
    #: click accuracy（点落在 GT 框内即命中）—— 生态的公开口径。
    click_hits: int | None = None
    rows: tuple[dict[str, Any], ...] = field(default_factory=tuple)

    @property
    def refine_enabled_rate(self) -> float:
        if self.samples <= 0:
            return 0.0
        return self.refine_enabled / self.samples

    @property
    def fallback_rate(self) -> float:
        if self.refine_enabled <= 0:
            return 0.0
        return self.fallbacks / self.refine_enabled

    @property
    def click_accuracy(self) -> float | None:
        if self.click_hits is None or self.samples <= 0:
            return None
        return self.click_hits / self.samples

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "samples": self.samples,
            "landing_error_px": self.landing.to_dict() if self.landing else None,
            "coarse_error_px": self.coarse.to_dict() if self.coarse else None,
            "refine_error_px": self.refine.to_dict() if self.refine else None,
            "refine_enabled": self.refine_enabled,
            "refine_enabled_rate": round(self.refine_enabled_rate, 4),
            "fallbacks": self.fallbacks,
            "fallback_rate": round(self.fallback_rate, 4),
            "click_hits": self.click_hits,
            "click_accuracy": (
                round(self.click_accuracy, 4) if self.click_accuracy is not None else None
            ),
            "rows": list(self.rows),
        }

    def render(self) -> str:
        """人读的一段（报告里直接贴）。"""
        def fmt(distribution: ErrorDistribution | None) -> str:
            if distribution is None:
                return "（无样本）"
            return (
                f"中位 {distribution.median:.2f} px｜p90 {distribution.p90:.2f} px"
                f"｜最大 {distribution.maximum:.2f} px"
            )

        lines = [
            f"=== 落点误差统一口径｜{self.source}｜N={self.samples} ===",
            f"落点误差（最终落点 vs 真值）：{fmt(self.landing)}",
            f"L0 误差（粗定位点 vs 真值）：{fmt(self.coarse)}",
            f"L1 误差（精定位落点 vs 真值）：{fmt(self.refine)}",
            f"精定位开启率：{self.refine_enabled}/{self.samples}"
            f" = {self.refine_enabled_rate:.1%}"
            f"｜回落率：{self.fallbacks}/{self.refine_enabled}"
            f" = {self.fallback_rate:.1%}",
        ]
        if self.click_accuracy is not None:
            lines.append(f"click accuracy（点落在 GT 框内）：{self.click_accuracy:.1%}")
        return "\n".join(lines)


def summarize_samples(
    samples: Sequence[CaliberSample], *, source: str = "", click_hits: int | None = None
) -> CaliberReport:
    """把一批样本汇成**统一口径**的报告。"""
    landings = [value for value in (landing_error_px(s) for s in samples) if value is not None]
    coarses = [value for value in (coarse_error_px(s) for s in samples) if value is not None]
    refines = [value for value in (refine_error_px(s) for s in samples) if value is not None]
    rows = tuple(
        {
            "name": sample.name,
            "landing_error_px": landing_error_px(sample),
            "coarse_error_px": coarse_error_px(sample),
            "refine_error_px": refine_error_px(sample),
            "refine_enabled": sample.refine_enabled,
            "fallback": sample.fallback,
        }
        for sample in samples
    )
    return CaliberReport(
        source=source,
        samples=len(samples),
        landing=summarize_errors(landings) if landings else None,
        coarse=summarize_errors(coarses) if coarses else None,
        refine=summarize_errors(refines) if refines else None,
        refine_enabled=sum(1 for s in samples if s.refine_enabled),
        fallbacks=sum(1 for s in samples if s.fallback),
        click_hits=click_hits,
        rows=rows,
    )


# ----------------------------------------------------------------------
# 记录读取（各生产者的落盘形状）
# ----------------------------------------------------------------------


def samples_from_sspro_report(
    report: dict[str, Any],
    *,
    dataset_root: str | Path,
    arm: str = "on_demand",
) -> tuple[list[CaliberSample], int | None]:
    """从 ScreenSpot-Pro 评测报告里取样本（**带上 GT 框中心**）。

    真值 = 数据集里那条样本的 **GT 框中心**（原图像素）——
    与 M7/M9 的「真值取控件框中心」同一口径。
    """
    from minicode.eval.sspro import load_samples

    outcomes = ((report.get("outcomes") or {}).get(arm)) or []
    if not outcomes:
        return [], None
    truth_by_id: dict[str, tuple[float, float]] = {}
    for sample in load_samples(Path(dataset_root)):
        x1, y1, x2, y2 = sample.bbox
        truth_by_id[sample.sample_id] = ((x1 + x2) / 2.0, (y1 + y2) / 2.0)
    samples: list[CaliberSample] = []
    hits = 0
    for outcome in outcomes:
        landing = outcome.get("landing_point")
        coarse = outcome.get("coarse_point")
        samples.append(
            CaliberSample(
                name=str(outcome.get("sample_id", "")),
                truth=truth_by_id.get(str(outcome.get("sample_id", ""))),
                coarse_point=tuple(coarse) if coarse else None,
                landing_point=tuple(landing) if landing else None,
                refine_enabled=bool(outcome.get("refine_enabled")),
                fallback=bool(outcome.get("fallback")),
                source=f"sspro/{arm}",
            )
        )
        if outcome.get("hit"):
            hits += 1
    return samples, hits


def samples_from_bench_report(
    report: dict[str, Any], *, source: str = "bench"
) -> list[CaliberSample]:
    """从真机分布回归台（``ground/bench.py``）的报告里取样本。

    那份记录里存的是**误差**（没存点），所以走 ``precomputed_*`` 字段。
    """
    samples: list[CaliberSample] = []
    for outcome in report.get("outcomes") or []:
        samples.append(
            CaliberSample(
                name=str(outcome.get("name", "")),
                precomputed_coarse_error_px=outcome.get("coarse_error_px"),
                precomputed_refine_error_px=outcome.get("refine_error_px"),
                landing_point=None,
                refine_enabled=bool(outcome.get("refine_enabled")),
                fallback=bool(outcome.get("fallback")),
                source=source,
            )
        )
    return samples


# ----------------------------------------------------------------------
# 命令行
# ----------------------------------------------------------------------


def _read_json(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def main(argv: Sequence[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        prog="minicode.ground.caliber",
        description="落点误差统一口径：一个脚本出全部数字",
    )
    parser.add_argument("--sspro-report", default=None, help="sspro-report.json 的路径")
    parser.add_argument("--dataset", default=".scratch/sspro", help="ScreenSpot-Pro 数据集根目录")
    parser.add_argument(
        "--arm",
        default="on_demand",
        choices=("on_demand", "always", "coarse_only"),
        help=(
            "读哪一臂（``coarse_only`` = 只用粗定位那一臂，1.00 次/目标；"
            "它是**统一口径**下也要能读的那一臂）"
        ),
    )
    parser.add_argument("--bench-report", default=None, help="ground bench 报告的路径")
    parser.add_argument("--out", default=None, help="报告落盘路径（JSON）")
    args = parser.parse_args(argv)

    reports: list[CaliberReport] = []
    if args.sspro_report:
        report = _read_json(args.sspro_report)
        samples, hits = samples_from_sspro_report(
            report, dataset_root=args.dataset, arm=args.arm
        )
        reports.append(
            summarize_samples(samples, source=f"sspro/{args.arm}", click_hits=hits)
        )
    if args.bench_report:
        samples = samples_from_bench_report(_read_json(args.bench_report))
        reports.append(summarize_samples(samples, source="bench"))

    if not reports:
        parser.error("至少要给 --sspro-report 或 --bench-report 之一")

    for report in reports:
        print(report.render())
    if args.out:
        path = Path(args.out)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps([report.to_dict() for report in reports], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"记录已写入：{path}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

"""**T3 第二步**：crop 比例扫描 {0.5, 0.35, 0.25} —— **离线几何重算，零模型调用**。

施工清单 T3 第二步（``docs/research/vision-next-plan.md:300``）：

    crop 比例扫描 {0.5, 0.35, 0.25}，**保持宽高比**（ScreenSpot-Pro 含多屏拼接
    全景图，固定比例会拉伸变形）；硬约束：crop 含住率 ≥76.5%。

**口径（本模块的数只在这一个定义下有意义）**

* **窗口尺寸 = 纯线性** ``⌊图宽×f⌋ × ⌊图高×f⌋`` —— **保持宽高比**。
  ⚠️ 生产那条自适应几何（:mod:`minicode.ground.channel`）还带一个
  **绝对下限 512×512**；那个下限**会把比例改方**（1536×960 的图在 0.5 档
  被下限顶成 768×480→512×512），所以本模块的**主表不套下限**，
  生产口径的那几行**另外列**（口径写在产物里，不混）。
* **完整含住** = crop 矩形四条边都在 GT 框内；
  **窗口无目标** = crop 矩形与 GT 框**完全不相交**；
  **容忍粗点误差** = 窗口以粗点为中心时，粗点最多偏多少目标仍被完整含住。
  ⇒ 三条判据与 :mod:`minicode.ground.crop_geometry` **逐字同口径**
  （那一份是**唯一出处**，这里 import，不重写一份）。

**锚点（重建口径必须对得上）**：0.5 档的「纯线性」窗口在
``runs/m3-crop-geometry/crop-geometry.json`` 里的对应行 = **84.2%**
（154/183，n=183，arm=always）。本模块的 0.5 行与它是**同一批粗定位点 +
同一套判据** ⇒ 可以直接比。

**硬约束**：含住率 **≥ 76.5%**（1536 基线那个数）—— 低于它的档**不许进生产**。

用法::

    $env:PYTHONPATH="src"
    python -m minicode.ground.crop_ratio_scan --out runs/v3-crop-ratio
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from .crop_geometry import (
    ADAPTIVE_SPECS,
    CropGeometrySample,
    evaluate_geometry,
)

__all__ = [
    "CONTAINMENT_FLOOR",
    "RATIOS",
    "linear_size",
    "main",
    "ratio_rows",
]

#: **要扫的线性比例**（施工清单 T3 第二步点名的三档）。
RATIOS: tuple[float, ...] = (0.5, 0.35, 0.25)

#: **硬约束**：crop 含住率不得低于它（1536 基线那个数，1536 实跑 140/183 = 76.5%）。
CONTAINMENT_FLOOR = 0.765


def linear_size(image_size: tuple[int, int], fraction: float) -> tuple[int, int]:
    """**纯线性**的窗口尺寸（**保持宽高比**）：``⌊图宽×f⌋ × ⌊图高×f⌋``。

    ⚠️ **不套生产的绝对下限**：那个下限（512×512）会把比例改方
    （1536×960 的图在 0.5 档被顶成 512×512）→ 扫描要比的是**比例**本身。
    """
    width = max(1, int(image_size[0] * fraction))
    height = max(1, int(image_size[1] * fraction))
    return (min(width, image_size[0]), min(height, image_size[1]))


def _ui_types(dataset_root: Path) -> dict[str, str]:
    """``sample_id → ui_type``（``text`` / ``icon``）—— 标注里就有。"""
    result: dict[str, str] = {}
    for path in sorted((Path(dataset_root) / "annotations").glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        for item in payload:
            result[str(item["id"])] = str(item.get("ui_type", ""))
    return result


def load_arm(
    report_path: Path, dataset_root: Path, *, arm: str = "always"
) -> tuple[list[CropGeometrySample], list[str], int]:
    """读一条臂的粗定位点 → ``(有粗点的样本, 没有粗点的样本 id, 该臂总条数)``。

    ⚠️ **分母口径**：实跑那个含住率的分母是**整条臂的条数**（183），
    粗定位失败（没开窗）的那些**算「没含住」** —— 离线重算必须**同一个分母**，
    否则两��数不可比（183 与 176 之间差的就是「粗定位失败」那几条）。
    """
    payload = json.loads(Path(report_path).read_text(encoding="utf-8"))
    outcomes = payload.get("outcomes", {}).get(arm, [])
    ground_truth = {
        sample_id: (bbox, size)
        for sample_id, (bbox, size) in _load_gt(dataset_root).items()
    }
    samples: list[CropGeometrySample] = []
    missing: list[str] = []
    for outcome in outcomes:
        sample_id = str(outcome["sample_id"])
        coarse = outcome.get("coarse_point")
        entry = ground_truth.get(sample_id)
        if not coarse or entry is None:
            missing.append(sample_id)
            continue
        bbox, image_size = entry
        samples.append(
            CropGeometrySample(
                sample_id=sample_id,
                coarse_point=(float(coarse[0]), float(coarse[1])),
                bbox=bbox,
                image_size=image_size,
            )
        )
    return samples, missing, len(outcomes)


def _load_gt(
    dataset_root: Path,
) -> dict[str, tuple[tuple[int, int, int, int], tuple[int, int]]]:
    """标注 → ``sample_id → (GT bbox, 图像尺寸)``（与 crop_geometry 同口径）。"""
    result: dict[str, tuple[tuple[int, int, int, int], tuple[int, int]]] = {}
    for path in sorted((Path(dataset_root) / "annotations").glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        for item in payload:
            width, height = (int(value) for value in item["img_size"])
            x1, y1, x2, y2 = (int(value) for value in item["bbox"])
            result[str(item["id"])] = ((x1, y1, x2, y2), (width, height))
    return result


def _stratified(
    samples: Sequence[CropGeometrySample],
    outcomes: Sequence[Any],
    ui_types: dict[str, str],
) -> dict[str, dict[str, Any]]:
    """按 ``ui_type`` 分层的含住率（**icon / text 各一份**）。

    分层口径与整体**完全一致**（同一条样本集合、同一条判据），
    只是**分子分母按 ``ui_type`` 切分**。
    """
    rows: dict[str, dict[str, Any]] = {}
    for kind in ("icon", "text"):
        picked = [
            (sample, outcome)
            for sample, outcome in zip(samples, outcomes, strict=True)
            if ui_types.get(sample.sample_id) == kind
        ]
        if not picked:
            continue
        rows[kind] = {
            "samples": len(picked),
            "fully_contains_bbox": sum(1 for _, o in picked if o.fully_contains_bbox),
            "fully_contains_rate": round(
                sum(1 for _, o in picked if o.fully_contains_bbox) / len(picked), 4
            ),
            "missing_target_rate": round(
                sum(1 for _, o in picked if not o.contains_any) / len(picked), 4
            ),
        }
    return rows


def ratio_rows(
    samples: Sequence[CropGeometrySample],
    *,
    ui_types: dict[str, str] | None = None,
    missing: Sequence[str] = (),
    total: int | None = None,
) -> list[dict[str, Any]]:
    """三档比例的逐档读数（**主表**：纯线性、保宽高比）。

    * ``missing`` —— 该臂里**没有粗定位点**（没开窗）的样本 id：
      它们**算「没含住」**（分母 = ``total``，即整条臂的条数），
      口径与实跑那个含住率一致（140/183 那个数）。
    * 另附**生产口径**那几行（带绝对下限 512×512 的自适应几何）——
      它们是**对照**，口径写在 ``kind`` 字段里，不与主表混。
    """
    ui_types = ui_types or {}
    denominator = total if total is not None else len(samples) + len(missing)
    rows: list[dict[str, Any]] = []
    for fraction in RATIOS:
        stats, outcomes = evaluate_geometry(
            samples,
            name=f"线性{fraction}×当前图",
            width=0,
            height=0,
            size_for=lambda size, f=fraction: linear_size(size, f),
        )
        rows.append(_row(stats, outcomes, samples, ui_types, missing, denominator))
    for spec in ADAPTIVE_SPECS:
        stats, outcomes = evaluate_geometry(
            samples,
            name=spec.name,
            width=0,
            height=0,
            size_for=spec.size_for,
        )
        rows.append(_row(stats, outcomes, samples, ui_types, missing, denominator))
    return rows


def _row(
    stats: Any,
    outcomes: Sequence[Any],
    samples: Sequence[CropGeometrySample],
    ui_types: dict[str, str],
    missing: Sequence[str],
    denominator: int,
) -> dict[str, Any]:
    """一行读数（**分母 = 整条臂的条数**：没开窗的那些算「没含住」）。"""
    row = stats.to_dict()
    row["samples"] = denominator
    row["samples_with_coarse_point"] = len(samples)
    row["samples_without_coarse_point"] = len(missing)
    row["fully_contains_bbox"] = stats.fully_contains_bbox
    row["fully_contains_rate"] = (
        round(stats.fully_contains_bbox / denominator, 4) if denominator else 0.0
    )
    row["missing_target_rate"] = (
        round((denominator - stats.contains_any) / denominator, 4) if denominator else 0.0
    )
    row["contains_center_rate"] = (
        round(stats.contains_center / denominator, 4) if denominator else 0.0
    )
    row["kind"] = (
        "纯线性（保宽高比）"
        if row["name"].startswith("线性")
        else "生产口径（自适应 + 绝对下限 512×512）"
    )
    row["fraction"] = (
        float(row["name"].removeprefix("线性").removesuffix("×当前图"))
        if row["name"].startswith("线性")
        else 0.5
    )
    row["meets_containment_floor"] = row["fully_contains_rate"] >= CONTAINMENT_FLOOR
    row["strata"] = _stratified(samples, outcomes, ui_types)
    return row


def _reconfigure_output() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):  # pragma: no cover
            pass


def main(argv: Sequence[str] | None = None) -> int:
    _reconfigure_output()
    parser = argparse.ArgumentParser(
        prog="minicode.ground.crop_ratio_scan",
        description="crop 比例扫描 {0.5, 0.35, 0.25}（离线几何重算，零模型调用）",
    )
    parser.add_argument(
        "--report",
        action="append",
        default=None,
        help=(
            "评测报告（读 coarse_point）—— 可给多次；"
            "默认跑两份：g2-eval-final（1536 实跑那份，硬约束 76.5% 的同源基准）"
            "与 g1-eval-330（m3-crop-geometry 锚点那份）"
        ),
    )
    parser.add_argument("--dataset", default=".scratch/sspro", help="数据集根目录")
    parser.add_argument("--arm", default="always", help="读哪一臂的粗定位点")
    parser.add_argument("--out", default="runs/v3-crop-ratio", help="产物目录")
    args = parser.parse_args(argv)

    reports = args.report or [
        "runs/g2-eval-final/sspro-report.json",
        "runs/g1-eval-330/sspro-report.json",
    ]
    ui_types = _ui_types(Path(args.dataset))
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    payload: dict[str, Any] = {
        "dataset": str(args.dataset),
        "arm": args.arm,
        "ratios": list(RATIOS),
        "containment_floor": CONTAINMENT_FLOOR,
        "caliber": {
            "window_size": "纯线性 ⌊图宽×f⌋ × ⌊图高×f⌋（保持宽高比，不套绝对下限）",
            "fully_contains_bbox": "crop 矩形四条边都在 GT 框内",
            "missing_target_rate": "crop 矩形与 GT 框完全不相交的比例",
            "median_tolerance": "窗口以粗点为中心时粗点最多偏多少目标仍被完整含住",
            "denominator": "整条臂的条数；粗定位失败（没开窗）的那些算「没含住」",
        },
        "reports": [],
    }
    for report in reports:
        samples, missing, total = load_arm(
            Path(report), Path(args.dataset), arm=args.arm
        )
        print(
            f"=== {report}｜{args.arm} 臂：共 {total} 条"
            f"（有粗定位点 {len(samples)} 条，没有 {len(missing)} 条）==="
        )
        rows = ratio_rows(
            samples, ui_types=ui_types, missing=missing, total=total
        )
        for row in rows:
            flag = "过" if row["meets_containment_floor"] else "**不过**"
            print(
                f"  {row['name']:<24} [{row['kind']}]"
                f" 完整含住 {row['fully_contains_rate'] * 100:5.1f}%"
                f"（{row['fully_contains_bbox']}/{row['samples']}）"
                f"｜窗口无目标 {row['missing_target_rate'] * 100:5.1f}%"
                f"｜容忍粗点误差 ±{row['median_tolerance_x_px']:.0f}/±{row['median_tolerance_y_px']:.0f} px"
                f"｜硬约束 76.5% {flag}"
            )
            for kind, strata in row.get("strata", {}).items():
                print(
                    f"      {kind:<5} n={strata['samples']:<4}"
                    f" 含住 {strata['fully_contains_rate'] * 100:5.1f}%"
                    f"（{strata['fully_contains_bbox']}/{strata['samples']}）"
                    f"｜窗口无目标 {strata['missing_target_rate'] * 100:5.1f}%"
                )
        payload["reports"].append(
            {
                "source_report": str(report),
                "arm": args.arm,
                "samples": total,
                "samples_with_coarse_point": len(samples),
                "samples_without_coarse_point": len(missing),
                "rows": rows,
            }
        )

    path = out_dir / "crop-ratio.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"记录已写入：{path}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

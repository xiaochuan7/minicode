"""**离线几何重算**：给定「粗定位点 + GT 框」，算各候选 crop 几何的含住率。

**零模型调用**（施工清单第 ③ 条）—— 只读**已经落盘**的粗定位点与 GT bbox，
把「crop 窗口有没有框住目标」这一条从一次性诊断变成**可离线重算的表**。

为什么需要它：crop 窗口的档位（192 / 512 / 768 / 1024² / 1280×800 / 0.5×当前图）
**不能靠猜** —— 施工清单第 ⑤ 条的档位由本模块给出的数决定。

**口径（本模块的数只在这一个定义下有意义）**

* ``fully_contains_bbox`` —— crop 矩形**完整含住 GT 框**
  （四条边都在框内；GT 框比窗口还大时恒为 False）；
* ``contains_center`` —— crop 矩形含住 **GT 框的中心**；
* ``contains_any`` —— crop 矩形与 GT 框**有交集**（等价于「窗口里看得见目标」）；
* ``tolerance_*`` —— **容忍的粗点误差**：窗口以粗点为中心时，粗点最多偏多少
  目标仍被完整含住（逐样本 ``(w − gt_w) / 2``，负数表示目标比窗口还大、
  放不下 → 记 0）。

**锚点**（重建口径必须对得上，否则说明重建错了）：

* 192 → 完整含住率 **42.1%**（n=183）；
* 512 → 完整含住率 **71.0%**（n=183）。

⚠️ **与生产同一条裁剪语义**：窗口摆放直接调用**冻结原型**的
:func:`minicode.coord.crop_refine.plan_crop`（**不重写一份**）——
贴边时**向内平移补足、不缩小窗口**。

⚠️ **自适应几何的比例与下限只有生产那一份**
（:mod:`minicode.ground.channel` 的 ``CROP_LINEAR_FRACTION`` /
``MIN_CROP_WIDTH_PX`` / ``MIN_CROP_HEIGHT_PX``）—— 本模块**从那里 import**，
**不许**在这里再写一遍（生产几何一改，离线锚点表就失真）。
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from minicode.coord.coordinates import Rect
from minicode.coord.crop_refine import plan_crop

from .channel import (
    CROP_LINEAR_FRACTION,
    MIN_CROP_HEIGHT_PX,
    MIN_CROP_WIDTH_PX,
)

__all__ = [
    "CropGeometrySample",
    "GeometryOutcome",
    "GeometryStats",
    "candidate_geometries",
    "evaluate_geometry",
    "load_report_samples",
    "load_gt",
    "main",
]


@dataclass(frozen=True)
class CropGeometrySample:
    """一条样本的离线几何输入（**全部来自已落盘的产物**）。"""

    sample_id: str
    #: 粗定位点（**物理像素**，与 GT bbox 同一空间）。
    coarse_point: tuple[float, float]
    #: GT bbox ``(x1, y1, x2, y2)``（原图像素）。
    bbox: tuple[int, int, int, int]
    #: 图像尺寸（原图像素）。
    image_size: tuple[int, int]


@dataclass(frozen=True)
class GeometryOutcome:
    """一条样本在一种几何下的判定。"""

    sample_id: str
    fully_contains_bbox: bool
    contains_center: bool
    contains_any: bool
    #: 窗口矩形（物理像素）。
    rect: tuple[int, int, int, int]
    #: 容忍的粗点误差（物理像素，负数夹到 0）。
    tolerance_x: float
    tolerance_y: float
    #: 粗点相对 GT 中心的偏差（物理像素）。
    coarse_error_px: float


@dataclass(frozen=True)
class GeometryStats:
    """一种几何的汇总。"""

    name: str
    width: int
    height: int
    samples: int
    fully_contains_bbox: int
    contains_center: int
    contains_any: int
    median_tolerance_x: float
    median_tolerance_y: float
    median_coarse_error_px: float
    #: **自适应几何**时，窗口尺寸逐样本不同 → 这里记「宽/高的最小与最大值」。
    size_range: tuple[int, int, int, int] = (0, 0, 0, 0)

    @property
    def fully_contains_rate(self) -> float:
        if self.samples <= 0:
            return 0.0
        return self.fully_contains_bbox / self.samples

    @property
    def contains_center_rate(self) -> float:
        if self.samples <= 0:
            return 0.0
        return self.contains_center / self.samples

    @property
    def missing_rate(self) -> float:
        """「窗口里压根没有目标」的比例（**完全不含**）。"""
        if self.samples <= 0:
            return 0.0
        return (self.samples - self.contains_any) / self.samples

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "crop_size": [self.width, self.height],
            "crop_size_range": list(self.size_range),
            "samples": self.samples,
            "fully_contains_bbox": self.fully_contains_bbox,
            "fully_contains_rate": round(self.fully_contains_rate, 4),
            "contains_center": self.contains_center,
            "contains_center_rate": round(self.contains_center_rate, 4),
            "contains_any": self.contains_any,
            "missing_target_rate": round(self.missing_rate, 4),
            "median_tolerance_x_px": round(self.median_tolerance_x, 1),
            "median_tolerance_y_px": round(self.median_tolerance_y, 1),
            "median_coarse_error_px": round(self.median_coarse_error_px, 1),
        }


#: **自适应几何**的参数：比例 + 绝对下限 + 绝对上限（``None`` = 不设上限）。
#:
#: ⚠️ **比例与下限取自生产**（:mod:`minicode.ground.channel`）——
#: 这里**不许**再写一份（生产几何一改，离线锚点表就失真）。
@dataclass(frozen=True)
class AdaptiveSpec:
    name: str
    fraction: float
    floor_width: int
    floor_height: int
    ceiling: int | None = None

    def size_for(self, image_size: tuple[int, int]) -> tuple[int, int]:
        width = max(self.floor_width, int(image_size[0] * self.fraction))
        height = max(self.floor_height, int(image_size[1] * self.fraction))
        if self.ceiling is not None:
            width = min(self.ceiling, width)
            height = min(self.ceiling, height)
        # 窗口不能比图还大（否则开不出来）。
        width = min(width, image_size[0])
        height = min(height, image_size[1])
        return (width, height)


#: 施工清单第 ⑤ 条点名的**自适应**几何（比例优先 + 绝对下限 512）。
#:
#: * ``0.5×当前图`` —— 第五条的原文形态（**无上限**）；
#: * ``0.5×当前图（上限 1024）`` —— 同一条，加一个**绝对上限**，
#:   用来回答「窗口要多大才够、又不能无限大」这个问题（**候选**，不是定案）。
ADAPTIVE_SPECS: tuple[AdaptiveSpec, ...] = (
    AdaptiveSpec(
        "0.5x当前图",
        CROP_LINEAR_FRACTION,
        MIN_CROP_WIDTH_PX,
        MIN_CROP_HEIGHT_PX,
        None,
    ),
    AdaptiveSpec(
        "0.5x当前图(上限1024)",
        CROP_LINEAR_FRACTION,
        MIN_CROP_WIDTH_PX,
        MIN_CROP_HEIGHT_PX,
        1024,
    ),
)


def candidate_geometries() -> list[tuple[str, int, int]]:
    """**固定边长**候选（施工清单第 ③ 条点名的那些）。"""
    fixed: list[tuple[str, int, int]] = [
        ("192", 192, 192),
        ("512", 512, 512),
        ("768", 768, 768),
        ("1024x1024", 1024, 1024),
        ("1280x800", 1280, 800),
    ]
    return fixed


def _crop_rect(
    coarse: tuple[float, float],
    image_size: tuple[int, int],
    width: int,
    height: int,
) -> tuple[int, int, int, int] | None:
    """窗口矩形（物理像素）—— **直接用冻结原型的 ``plan_crop``**（不重写一份）。

    粗点**不在可裁剪表面内**（越界）→ 返回 ``None``（那一帧的窗口开不出来，
    **如实记「窗口开不出来」**，不编一个矩形）。
    """
    surface = Rect.from_bounds(0, 0, image_size[0], image_size[1])
    try:
        plan = plan_crop(
            coarse[0],
            coarse[1],
            surface,
            crop_width=width,
            crop_height=height,
            target_radius_px=0.0,
        )
    except Exception:
        return None
    return (
        plan.origin_x,
        plan.origin_y,
        plan.origin_x + plan.width,
        plan.origin_y + plan.height,
    )


def evaluate_geometry(
    samples: Sequence[CropGeometrySample],
    *,
    name: str,
    width: int,
    height: int,
    size_for: Callable[[tuple[int, int]], tuple[int, int]] | None = None,
) -> tuple[GeometryStats, list[GeometryOutcome]]:
    """一种几何在一批样本上的判定与汇总。

    ``size_for`` 给了就是**自适应**几何（窗口尺寸逐样本算）。
    """
    outcomes: list[GeometryOutcome] = []
    sizes: list[tuple[int, int]] = []
    for sample in samples:
        x1, y1, x2, y2 = sample.bbox
        sample_width, sample_height = (
            size_for(sample.image_size) if size_for else (width, height)
        )
        sizes.append((sample_width, sample_height))
        rect = _crop_rect(sample.coarse_point, sample.image_size, sample_width, sample_height)
        if rect is None:
            # 窗口开不出来（粗点越界）→ 如实记「窗口里没有目标」。
            outcomes.append(
                GeometryOutcome(
                    sample_id=sample.sample_id,
                    fully_contains_bbox=False,
                    contains_center=False,
                    contains_any=False,
                    rect=(0, 0, 0, 0),
                    tolerance_x=0.0,
                    tolerance_y=0.0,
                    coarse_error_px=float("inf"),
                )
            )
            continue
        left, top, right, bottom = rect
        fully = left <= x1 and top <= y1 and right >= x2 and bottom >= y2
        center_x = (x1 + x2) / 2.0
        center_y = (y1 + y2) / 2.0
        has_center = left <= center_x <= right and top <= center_y <= bottom
        intersects = not (right <= x1 or left >= x2 or bottom <= y1 or top >= y2)
        outcomes.append(
            GeometryOutcome(
                sample_id=sample.sample_id,
                fully_contains_bbox=fully,
                contains_center=has_center,
                contains_any=intersects,
                rect=(left, top, right, bottom),
                tolerance_x=max(0.0, (sample_width - (x2 - x1)) / 2.0),
                tolerance_y=max(0.0, (sample_height - (y2 - y1)) / 2.0),
                coarse_error_px=(
                    (sample.coarse_point[0] - center_x) ** 2
                    + (sample.coarse_point[1] - center_y) ** 2
                )
                ** 0.5,
            )
        )
    widths = [size[0] for size in sizes]
    heights = [size[1] for size in sizes]
    stats = GeometryStats(
        name=name,
        width=width,
        height=height,
        samples=len(outcomes),
        fully_contains_bbox=sum(1 for item in outcomes if item.fully_contains_bbox),
        contains_center=sum(1 for item in outcomes if item.contains_center),
        contains_any=sum(1 for item in outcomes if item.contains_any),
        median_tolerance_x=(
            statistics.median([item.tolerance_x for item in outcomes]) if outcomes else 0.0
        ),
        median_tolerance_y=(
            statistics.median([item.tolerance_y for item in outcomes]) if outcomes else 0.0
        ),
        median_coarse_error_px=(
            statistics.median(
                [item.coarse_error_px for item in outcomes if item.coarse_error_px != float("inf")]
            )
            if outcomes
            else 0.0
        ),
        size_range=(
            min(widths) if widths else 0,
            min(heights) if heights else 0,
            max(widths) if widths else 0,
            max(heights) if heights else 0,
        ),
    )
    return (stats, outcomes)


def load_gt(
    dataset_root: Path,
) -> dict[str, tuple[tuple[int, int, int, int], tuple[int, int]]]:
    """从标注目录读 ``sample_id → (GT bbox, 图像尺寸)``。"""
    result: dict[str, tuple[tuple[int, int, int, int], tuple[int, int]]] = {}
    for path in sorted((Path(dataset_root) / "annotations").glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        for item in payload:
            width, height = (int(value) for value in item["img_size"])
            x1, y1, x2, y2 = (int(value) for value in item["bbox"])
            result[str(item["id"])] = ((x1, y1, x2, y2), (width, height))
    return result


def load_report_samples(
    report_path: Path, dataset_root: Path, *, arm: str = "always"
) -> list[CropGeometrySample]:
    """从一份评测报告里取「粗定位点」，与 GT 框配对。

    只读**已落盘**的字段：``coarse_point``（物理像素）+ 标注里的 GT bbox。
    """
    payload = json.loads(Path(report_path).read_text(encoding="utf-8"))
    ground_truth = load_gt(dataset_root)
    samples: list[CropGeometrySample] = []
    for outcome in payload.get("outcomes", {}).get(arm, []):
        coarse = outcome.get("coarse_point")
        if not coarse:
            continue
        entry = ground_truth.get(str(outcome["sample_id"]))
        if entry is None:
            continue
        bbox, image_size = entry
        samples.append(
            CropGeometrySample(
                sample_id=str(outcome["sample_id"]),
                coarse_point=(float(coarse[0]), float(coarse[1])),
                bbox=bbox,
                image_size=image_size,
            )
        )
    return samples


def _reconfigure_output() -> None:
    """控制台按 UTF-8 输出（默认代码页是 GBK，中文会变成乱码）。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):  # pragma: no cover
            pass


def main(argv: Sequence[str] | None = None) -> int:
    _reconfigure_output()
    parser = argparse.ArgumentParser(
        prog="minicode.ground.crop_geometry",
        description="离线几何重算：各候选 crop 几何的含住率（零模型调用）",
    )
    parser.add_argument(
        "--report",
        default="runs/g1-eval-330/sspro-report.json",
        help="评测报告（读 coarse_point）",
    )
    parser.add_argument("--dataset", default=".scratch/sspro", help="数据集根目录")
    parser.add_argument("--arm", default="always", help="读哪一臂的粗定位点")
    parser.add_argument("--out", default="runs/m3-crop-geometry", help="产物目录")
    args = parser.parse_args(argv)

    samples = load_report_samples(Path(args.report), Path(args.dataset), arm=args.arm)
    print(f"=== 样本 {len(samples)} 条（{args.report} 的 {args.arm} 臂）===")
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    rows: list[dict[str, Any]] = []
    details: dict[str, list[dict[str, Any]]] = {}

    def _emit(stats: GeometryStats, outcomes: list[GeometryOutcome]) -> None:
        rows.append(stats.to_dict())
        details[stats.name] = [
            {
                "sample_id": item.sample_id,
                "fully_contains_bbox": item.fully_contains_bbox,
                "contains_center": item.contains_center,
                "contains_any": item.contains_any,
                "rect": list(item.rect),
                "tolerance_x_px": round(item.tolerance_x, 1),
                "tolerance_y_px": round(item.tolerance_y, 1),
                "coarse_error_px": (
                    round(item.coarse_error_px, 1)
                    if item.coarse_error_px != float("inf")
                    else None
                ),
            }
            for item in outcomes
        ]
        print(
            f"  {stats.name:<24} 完整含住 {stats.fully_contains_rate * 100:5.1f}%"
            f"（{stats.fully_contains_bbox}/{stats.samples}）"
            f"｜含中心 {stats.contains_center_rate * 100:5.1f}%"
            f"｜窗口无目标 {stats.missing_rate * 100:5.1f}%"
            f"｜容忍粗点误差 ±{stats.median_tolerance_x:.0f}/±{stats.median_tolerance_y:.0f} px"
        )

    for name, width, height in candidate_geometries():
        stats, outcomes = evaluate_geometry(
            samples, name=name, width=width, height=height
        )
        _emit(stats, outcomes)

    for spec in ADAPTIVE_SPECS:
        stats, outcomes = evaluate_geometry(
            samples,
            name=spec.name,
            width=0,
            height=0,
            size_for=spec.size_for,
        )
        _emit(stats, outcomes)

    payload = {
        "source_report": str(args.report),
        "dataset": str(args.dataset),
        "arm": args.arm,
        "samples": len(samples),
        "geometry_rows": rows,
        "per_sample": details,
    }
    path = out_dir / "crop-geometry.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"记录已写入：{path}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

"""W2.3 离线 crop 尺寸对比：同批 60 条（W6 真实粗点为中心），三种尺寸的 GT 含住率。

**零模型调用**（纯几何）。三档（相对当前生产 crop = 0.5×帧、下限 512）：

* ``0.5x`` —— 生产尺寸的一半（0.25×帧、下限 512）；
* ``1x``   —— 生产尺寸（0.5×帧、下限 512，现状）；
* ``2x``   —— 生产的两倍（整帧封顶）。

含住口径与 ``ground/crop_geometry`` 一致：crop 矩形完全罩住 GT 框。
落盘 ``runs/w2/crop-size-offline.json``。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "src"))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from minicode.coord.crop_refine import plan_crop  # noqa: E402
from minicode.eval.sspro import load_samples, stratified_subset  # noqa: E402
from minicode.ground.geometry import surface_rect  # noqa: E402

DATASET = Path(r"C:\Users\22197\Desktop\ik评测\sspro-dataset")


def crop_size_for(frame: tuple[int, int], multiplier: float) -> tuple[int, int]:
    """生产尺寸（0.5×线性、下限 512）再乘 multiplier、不超过帧。"""
    width = min(frame[0], max(512, int(frame[0] * 0.5)))
    height = min(frame[1], max(512, int(frame[1] * 0.5)))
    width = min(frame[0], int(width * multiplier))
    height = min(frame[1], int(height * multiplier))
    return (width, height)


def contains(gt: tuple[float, float, float, float], plan) -> bool:
    return (
        plan.origin_x <= gt[0] and plan.origin_y <= gt[1]
        and plan.origin_x + plan.width >= gt[2]
        and plan.origin_y + plan.height >= gt[3]
    )


def main() -> int:
    w6_raw = json.loads((_ROOT / "runs" / "w6" / "w6-raw.json").read_text(encoding="utf-8"))
    coarse_by_id = {
        row["sample_id"]: row["pixel"]["surface_point"] for row in w6_raw
    }
    samples = load_samples(DATASET)
    subset = stratified_subset(samples, 10, seed=20251020)

    sizes = {"0.5x": 0.5, "1x": 1.0, "2x": 2.0}
    stats: dict[str, dict[str, int]] = {name: {"n": 0, "contained": 0} for name in sizes}
    rows: list[dict] = []
    for sample in subset:
        coarse = coarse_by_id.get(sample.sample_id)
        if coarse is None:
            continue
        for name, multiplier in sizes.items():
            size = crop_size_for(sample.image_size, multiplier)
            try:
                plan = plan_crop(
                    coarse[0], coarse[1],
                    surface_rect((0, 0, sample.image_size[0], sample.image_size[1])),
                    crop_width=size[0], crop_height=size[1],
                    target_radius_px=0.0,
                )
                hit = contains(sample.bbox, plan)
            except Exception:  # noqa: BLE001 —— 粗点越界等几何意外，计 0
                hit = False
            stats[name]["n"] += 1
            stats[name]["contained"] += int(hit)
            rows.append({
                "sample_id": sample.sample_id, "size": name,
                "crop": list(size), "contained": hit,
            })
    report = {
        "experiment": "W2.3 离线 crop 尺寸对比（含住率，零模型调用）",
        "batch": "S-Pro 同批 60（per_stratum=10, seed=20251020）",
        "coarse_source": "W6 pixel 臂真实粗点（runs/w6/w6-raw.json）",
        "judge": "crop 矩形完全罩住 GT 框（与 ground/crop_geometry 同口径）",
        "sizes": {name: "生产尺寸(0.5x帧,min512) × " + str(m) for name, m in sizes.items()},
        "stats": {
            name: {
                **data,
                "rate": round(data["contained"] / data["n"], 4) if data["n"] else None,
            }
            for name, data in stats.items()
        },
        "rows": rows,
    }
    out = _ROOT / "runs" / "w2" / "crop-size-offline.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(report["stats"], ensure_ascii=False, indent=1))
    print(f"=== 完成：{out} ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

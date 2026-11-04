"""W7 主线 B1 的**重测**：全部 icon 类样本（不再 30 条抽样）上的覆盖率。

修正口径（工单 4.1）：
* 旧结论「零模型图标提议 1/30」被**分辨率污染**（640 工作宽 + 工作分辨率
  里卡物理尺寸 → 21/30 样本在算法跑之前就被机械排除）；
* 本脚本用**全部** icon 类样本重跑（n=87 时 1/30 与 2/30 在统计上互相
  无法区分 —— n=30 的排序本身就是噪声）；
* 报告分**两列**：「可达上限」（多少样本的 GT 目标不低于尺寸阈值 ——
  算法跑之前有机会被提出）与「实际命中」（三列覆盖率）——
  否则永远说不清是算法问题还是门槛问题。

用法（仓库根目录）::

    python -I scripts/_night_icon_retest.py --out runs/w7-icon-retest
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "src"))

from minicode.eval.png_read import read_png_frame  # noqa: E402
from minicode.eval.sspro import load_samples  # noqa: E402
from minicode.ground.candidates import candidate_coverage  # noqa: E402
from minicode.model.icon_proposals import (  # noqa: E402
    MAX_ICON_PHYSICAL_PX,
    MIN_AREA_PHYSICAL_PX,
    MIN_ICON_PHYSICAL_PX,
    IconProposalCandidateSource,
)

sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def main() -> int:
    parser = argparse.ArgumentParser(description="W7 图标候选重测（全部 icon 样本）")
    parser.add_argument(
        "--dataset",
        default=r"C:\Users\22197\Desktop\ik评测\sspro-dataset",
        help="ScreenSpot-Pro 数据根（仓库外）",
    )
    parser.add_argument("--out", default="runs/w7-icon-retest")
    args = parser.parse_args()

    samples = load_samples(Path(args.dataset))
    icon_samples = [s for s in samples if s.ui_type == "icon"]
    print(f"=== 全部 icon 样本：{len(icon_samples)} 条（总样本 {len(samples)}）===")

    source = IconProposalCandidateSource(max_candidates=20)
    rows: list[dict] = []
    started = time.time()
    for index, sample in enumerate(icon_samples, start=1):
        row: dict = {
            "sample_id": sample.sample_id,
            "platform": sample.platform,
            "truth_box": [int(v) for v in sample.bbox],
        }
        truth_w = float(sample.bbox[2]) - float(sample.bbox[0])
        truth_h = float(sample.bbox[3]) - float(sample.bbox[1])
        # 「可达上限」：GT 目标不低于尺寸阈值（算法有机会把它提出来）。
        row["reachable"] = bool(
            MIN_ICON_PHYSICAL_PX <= truth_w <= MAX_ICON_PHYSICAL_PX
            and MIN_ICON_PHYSICAL_PX <= truth_h <= MAX_ICON_PHYSICAL_PX
            and truth_w * truth_h >= MIN_AREA_PHYSICAL_PX
        )
        row["truth_size"] = [round(truth_w, 1), round(truth_h, 1)]
        begin = time.time()
        try:
            frame = read_png_frame(sample.image_path)
            candidates = source.candidates(frame, sample.instruction)
            coverage = candidate_coverage(candidates, sample.bbox)
            row["hit"] = {
                **coverage,
                "count": len(candidates),
                "elapsed_s": round(time.time() - begin, 2),
            }
        except Exception as exc:  # noqa: BLE001 —— 单条失败如实记录
            row["hit"] = {"error": f"{type(exc).__name__}: {exc}", "count": 0}
        rows.append(row)
        if index % 10 == 0 or index == len(icon_samples):
            print(f"  [{index}/{len(icon_samples)}] ({time.time() - started:.0f}s)", flush=True)

    valid = [r for r in rows if "error" not in r["hit"]]
    reachable = [r for r in valid if r["reachable"]]
    report = {
        "experiment": "W7 主线 B1：icon 候选重测（物理像素域过滤 + 2016 工作宽 + 并查集连通域）",
        "n_icon_samples": len(icon_samples),
        "n_valid": len(valid),
        "n_reachable": len(reachable),
        "thresholds": {
            "min_icon_physical_px": MIN_ICON_PHYSICAL_PX,
            "max_icon_physical_px": MAX_ICON_PHYSICAL_PX,
            "min_area_physical_px": MIN_AREA_PHYSICAL_PX,
        },
        "summary": {
            "reachable_upper_bound": {
                "note": "可达上限 = GT 目标不低于尺寸阈值的样本数占比（门槛问题）",
                "fraction": round(len(reachable) / max(1, len(valid)), 4),
            },
            "actual_hit_all": {
                "note": "实际命中（全部样本，含不可达）",
                "iou_0_3": round(
                    sum(1 for r in valid if r["hit"]["candidate_iou_at_0_3"])
                    / max(1, len(valid)),
                    4,
                ),
                "iou_0_5": round(
                    sum(1 for r in valid if r["hit"]["candidate_iou_at_0_5"])
                    / max(1, len(valid)),
                    4,
                ),
                "center_inside": round(
                    sum(1 for r in valid if r["hit"]["candidate_center_inside"])
                    / max(1, len(valid)),
                    4,
                ),
            },
            "actual_hit_reachable_only": {
                "note": "实际命中（只算可达样本 —— 算法本身的能力）",
                "iou_0_3": round(
                    sum(1 for r in reachable if r["hit"]["candidate_iou_at_0_3"])
                    / max(1, len(reachable)),
                    4,
                ),
                "iou_0_5": round(
                    sum(1 for r in reachable if r["hit"]["candidate_iou_at_0_5"])
                    / max(1, len(reachable)),
                    4,
                ),
                "center_inside": round(
                    sum(1 for r in reachable if r["hit"]["candidate_center_inside"])
                    / max(1, len(reachable)),
                    4,
                ),
            },
            "candidates_mean": round(
                sum(r["hit"]["count"] for r in valid) / max(1, len(valid)), 1
            ),
            "elapsed_s_mean": round(
                sum(r["hit"].get("elapsed_s", 0) for r in valid) / max(1, len(valid)), 2
            ),
        },
        "rows": rows,
    }
    out_dir = _ROOT / args.out
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "icon-coverage-full.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    print(f"=== 完成：{path} ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

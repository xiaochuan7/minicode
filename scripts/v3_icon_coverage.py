"""V3 量化：零模型图标候选的**覆盖率**（对照 = OCR 词框候选）。

回答一个问题：**「缺的是候选不是模型」这个假设，数字上成不成立？**

* 同一批 60 条（与 W6/V2 同批：per_stratum=10，seed=20251020）；
* 两个候选源分别跑一遍：
  - **OCR 词框**（`WindowsOcrPort().read_words` + 既有的文本子串匹配）——
    现状的附加表（图标类原理上召不回无字图标）；
  - **图标提议**（`IconProposalCandidateSource`，零模型 CV 启发式）—— V3 的新源；
* 口径 = :func:`minicode.ground.candidates.candidate_coverage` 的三列
  （IoU@0.3 / IoU@0.5 / 框中心落在真目标内），**物理像素**空间；
* 分层报：icon 类样本（假设的正面战场）与非 icon 类。

用法（仓库根目录）::

    python -I scripts/v3_icon_coverage.py --out runs/v3
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
from minicode.eval.sspro import load_samples, stratified_subset  # noqa: E402
from minicode.ground.candidates import (  # noqa: E402
    OcrCandidateSource,
    candidate_coverage,
)
from minicode.model.icon_proposals import IconProposalCandidateSource  # noqa: E402

sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def _ocr_word_reader():
    """Windows OCR 词级读数（本机离线）；不可用 → ``None``（如实报告）。"""
    try:
        from minicode.verification.ocr_windows import WindowsOcrPort

        port = WindowsOcrPort()
        return lambda frame: port.read_words(frame)
    except Exception as exc:  # noqa: BLE001 —— OCR 不可用就只跑图标提议
        print(f"⚠️ Windows OCR 不可用（{exc}）—— OCR 基线缺失，报告里如实标注")
        return None


def _coverage_rows(samples, sources: dict[str, object]) -> list[dict]:
    rows: list[dict] = []
    started = time.time()
    for index, sample in enumerate(samples, start=1):
        frame = read_png_frame(sample.image_path)
        row: dict = {
            "sample_id": sample.sample_id,
            "ui_type": sample.ui_type,
            "platform": sample.platform,
            "truth_box": [int(value) for value in sample.bbox],
        }
        for name, source in sources.items():
            begin = time.time()
            try:
                candidates = source.candidates(frame, sample.instruction)
                coverage = candidate_coverage(candidates, sample.bbox)
                row[name] = {
                    **coverage,
                    "count": len(candidates),
                    "elapsed_s": round(time.time() - begin, 2),
                }
            except Exception as exc:  # noqa: BLE001 —— 单条失败如实记录
                row[name] = {"error": f"{type(exc).__name__}: {exc}", "count": 0}
        rows.append(row)
        if index % 10 == 0 or index == len(samples):
            print(f"  [{index}/{len(samples)}] {sample.sample_id} ({time.time() - started:.0f}s)", flush=True)
    return rows


def _summarize(rows: list[dict], name: str) -> dict:
    """一个候选源的三列覆盖率 + 数量分布（分层：icon / 非 icon / 全部）。"""
    summary: dict = {}
    for stratum, predicate in (
        ("icon", lambda row: row["ui_type"] == "icon"),
        ("non_icon", lambda row: row["ui_type"] != "icon"),
        ("all", lambda row: True),
    ):
        subset = [row for row in rows if predicate(row) and isinstance(row.get(name), dict) and "error" not in row[name]]
        total = len(subset)
        if not total:
            summary[stratum] = {"samples": 0}
            continue
        summary[stratum] = {
            "samples": total,
            "coverage_iou_0_3": round(
                sum(1 for row in subset if row[name]["candidate_iou_at_0_3"]) / total, 4
            ),
            "coverage_iou_0_5": round(
                sum(1 for row in subset if row[name]["candidate_iou_at_0_5"]) / total, 4
            ),
            "coverage_center_inside": round(
                sum(1 for row in subset if row[name]["candidate_center_inside"]) / total, 4
            ),
            "candidates_mean": round(
                sum(row[name]["count"] for row in subset) / total, 1
            ),
            "candidates_max": max(row[name]["count"] for row in subset),
            "elapsed_s_mean": round(
                sum(row[name]["elapsed_s"] for row in subset) / total, 2
            ),
        }
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="V3 图标候选覆盖率量化")
    parser.add_argument(
        "--dataset",
        default=r"C:\Users\22197\Desktop\ik评测\sspro-dataset",
        help="ScreenSpot-Pro 数据根（仓库外）",
    )
    parser.add_argument("--out", default="runs/v3")
    parser.add_argument("--per-stratum", type=int, default=10)
    parser.add_argument("--seed", type=int, default=20251020)
    args = parser.parse_args()

    samples = load_samples(Path(args.dataset))
    subset = stratified_subset(samples, args.per_stratum, seed=args.seed)
    icon_count = sum(1 for sample in subset if sample.ui_type == "icon")
    print(f"=== 样本：{len(subset)} 条（icon {icon_count} 条，seed={args.seed}，与 W6 同批）===", flush=True)

    reader = _ocr_word_reader()
    sources: dict[str, object] = {
        "icon_proposal": IconProposalCandidateSource(max_candidates=20),
    }
    if reader is not None:
        sources["ocr_words"] = OcrCandidateSource(reader, max_candidates=20)
    # 检测模型（V3 第 2 步，Apache-2.0）：环境里有 torch/ultralytics 才进对照；
    # 没有 → 如实缺失（报告 metrics_note 已声明「OCR 不可用 → 如实缺失」，
    # 检测器同理）。
    try:
        from minicode.model.detector_source import ScreenParserCandidateSource

        sources["screenparser"] = ScreenParserCandidateSource(
            max_candidates=20,
            word_reader=reader,
        )
        print("=== 检测模型候选源：ScreenParser（Apache-2.0）===", flush=True)
    except RuntimeError as exc:
        print(f"⚠️ ScreenParser 不可用（{exc}）—— 对照表里如实缺失", flush=True)

    print("=== 跑两个候选源 ===", flush=True)
    rows = _coverage_rows(subset, sources)

    report = {
        "experiment": "V3 候选源补齐：零模型图标提议 vs OCR 词框 —— 覆盖率量化",
        "dataset": "ScreenSpot-Pro（likaixin/ScreenSpot-Pro，仓库外）",
        "n_samples": len(subset),
        "per_stratum": args.per_stratum,
        "seed": args.seed,
        "icon_samples": icon_count,
        "metrics_note": (
            "覆盖率口径 = candidate_coverage：IoU@0.3 / IoU@0.5 / "
            "候选框中心落在真目标内（物理像素空间）。"
            "OCR 不可用 → ocr_words 列如实缺失"
        ),
        "summary": {
            name: _summarize(rows, name) for name in sources
        },
        "rows": rows,
    }
    out_dir = _ROOT / args.out
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "icon-coverage.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    print(f"=== 完成：{out_dir / 'icon-coverage.json'} ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

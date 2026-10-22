"""**T1 采样池探针**的离线分析 —— **零模型调用**。

读**采样池**（``pool.jsonl``，施工清单 T1）与数据集的 **GT**，
算出两个**决定性数**：

* **oracle-of-8** —— 采样里**至少一次命中**、而**单次没命中**的样本比例；
* **档位噪声基线** —— 同一次采样里 K 条回答的**命中条数分布**。

**判据**（预注册）：**oracle-of-8 ≥ 单次 + 8 pp** 才继续做 T5 的候选 B。

⚠️ **口径**：命中的判定 = **官方 bbox 命中**（``eval/formats.py::SCREENSPOT_PRO``），
与评测台**同一条**脚本；分母 = 采样池里**该级有采样**的那些样本。
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from .formats import SCREENSPOT_PRO
from .png_read import read_png_frame
from .sspro import load_samples

__all__ = ["analyze", "main"]

#: **判定门**：oracle-of-8 至少要比单次高这么多（百分点）。
ORACLE_GATE_PP = 8.0


def _hit(landing: tuple[float, float] | None, sample) -> bool:
    if landing is None:
        return False
    prediction = SCREENSPOT_PRO.wrap(
        (int(round(landing[0])), int(round(landing[1]))), sample.image_size
    )
    return SCREENSPOT_PRO.hit(prediction, sample.as_eval_sample())


def _coarse_center(entry: dict[str, Any]) -> tuple[float, float] | None:
    """粗定位的一次采样 → 落点（**框中心**，物理像素）。"""
    bbox = (entry.get("extra") or {}).get("bbox_px")
    if not bbox or len(bbox) != 4:
        return None
    return ((bbox[0] + bbox[2]) / 2.0, (bbox[1] + bbox[3]) / 2.0)


def _refine_point(entry: dict[str, Any]) -> tuple[float, float] | None:
    """精定位的一次采样 → 落点（**物理像素**）。"""
    physical = (entry.get("extra") or {}).get("physical_point")
    if physical and len(physical) == 2:
        return (float(physical[0]), float(physical[1]))
    return None


def _read_entries(pool_path: Path) -> list[dict[str, Any]]:
    """读采样池（JSONL，一行一条）。"""
    entries: list[dict[str, Any]] = []
    for line in pool_path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            entries.append(json.loads(line))
    return entries


def analyze(
    pool_path: Path, dataset_root: Path, *, stage_filter: str = ""
) -> dict[str, Any]:
    """算 oracle-of-8 与噪声基线。"""
    dataset = load_samples(dataset_root)
    entries = _read_entries(pool_path)
    #: 采样池的键 = 整屏帧的哈希 → 把它映回样本。
    #: ⚠️ **只给池子里出现过的键算哈希** —— 全量 939 张图解码一遍要好几分钟。
    wanted = {entry["key"] for entry in entries}
    by_digest: dict[str, Any] = {}
    for sample in dataset:
        if not sample.image_path.exists():
            continue
        digest = read_png_frame(sample.image_path).digest()
        if digest in wanted:
            by_digest[digest] = sample

    stages: dict[str, Any] = {}
    for stage in sorted({entry["stage"] for entry in entries}):
        if stage_filter and stage != stage_filter:
            continue
        #: key → 那条样本的 K 次采样
        grouped: dict[str, list[dict[str, Any]]] = {}
        for entry in entries:
            if entry["stage"] != stage:
                continue
            grouped.setdefault(entry["key"], []).append(entry)
        hits_per_sample: list[int] = []
        single_hits = 0
        oracle_hits = 0
        total = 0
        raw_texts_first: list[str] = []
        raw_texts_all: list[str] = []
        for key, group in grouped.items():
            sample = by_digest.get(key)
            if sample is None:
                continue
            group = sorted(group, key=lambda item: item["variant"])
            hits = 0
            for index, entry in enumerate(group):
                if stage == "coarse":
                    landing = _coarse_center(entry)
                else:
                    landing = _refine_point(entry)
                hit = _hit(landing, sample)
                hits += int(hit)
                if index == 0:
                    single_hits += int(hit)
                    raw_texts_first.append(entry["raw_text"])
                raw_texts_all.append(entry["raw_text"])
            hits_per_sample.append(hits)
            oracle_hits += int(hits > 0)
            total += 1
        distribution = Counter(hits_per_sample)
        stages[stage] = {
            "samples": total,
            "variants_per_sample": (
                round(len(raw_texts_all) / total, 2) if total else 0
            ),
            "single_hit_rate": (single_hits / total) if total else None,
            "oracle_of_8_hit_rate": (oracle_hits / total) if total else None,
            "oracle_gain_pp": (
                (oracle_hits - single_hits) / total * 100 if total else None
            ),
            "noise_baseline_hit_count_distribution": {
                str(key): value for key, value in sorted(distribution.items())
            },
            "noise_baseline_mean_hits": (
                sum(hits_per_sample) / len(hits_per_sample)
                if hits_per_sample
                else None
            ),
            "mean_variants": (
                len(raw_texts_all) / total if total else None
            ),
        }
    return {
        "pool": str(pool_path),
        "dataset": str(dataset_root),
        "stages": stages,
        "gate": {
            "rule": "oracle-of-8 ≥ 单次 + 8 pp 才继续做 T5 的候选 B",
            "threshold_pp": ORACLE_GATE_PP,
            "verdict": {
                stage: (
                    (info["oracle_gain_pp"] >= ORACLE_GATE_PP)
                    if info["oracle_gain_pp"] is not None
                    else None
                )
                for stage, info in stages.items()
            },
        },
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="minicode.eval.vote_probe",
        description="T1 采样池探针的离线分析（零模型调用）",
    )
    parser.add_argument("--pool", required=True, help="采样池（pool.jsonl）")
    parser.add_argument("--dataset", default=".scratch/sspro", help="数据集根")
    parser.add_argument("--stage", default="", help="只看某一级（coarse / refine）")
    parser.add_argument(
        "--out", default="", help="产物路径（默认与 --pool 同目录的 vote-probe.json）"
    )
    args = parser.parse_args(argv)

    pool_path = Path(args.pool)
    result = analyze(pool_path, Path(args.dataset), stage_filter=args.stage)
    out_path = (
        Path(args.out)
        if args.out
        else pool_path.parent / "vote-probe.json"
    )
    out_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"=== T1 采样池探针（零模型调用）：{pool_path} ===")
    for stage, info in result["stages"].items():
        gain = info["oracle_gain_pp"]
        print(
            f"{stage}：{info['samples']} 条｜单次 {info['single_hit_rate'] * 100:.2f}%"
            f"｜oracle-of-8 {info['oracle_of_8_hit_rate'] * 100:.2f}%"
            f"｜增益 {gain:+.2f} pp"
            f"｜命中条数分布 {info['noise_baseline_hit_count_distribution']}"
            f"｜**判定：{'过门' if result['gate']['verdict'].get(stage) else '不过门'}**"
        )
    print(f"记录已写入：{out_path}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())

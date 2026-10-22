"""**护栏候选的离线重算**（施工清单 T5）—— **零模型调用**。

这份脚本**不发任何模型请求**：它读**已有产物**（``sspro-report.json``），
把「粗定位点 / 粗定位框 / 精定位点」重新组合成**别的落点**，
再按**官方 bbox 命中**口径重判，最后做**配对**统计。

## 两条候选

| | 形状 | 依据 |
|---|---|---|
| **候选 A**（LFPR 式 guard + 中点融合） | 粗框 = incumbent；精点扩 α 方块 = 候选；**过 guard → 落中点** ½p₀+½p₁，**不过 → 落粗点** | LFPR（Acc@0.5 中点融合 > guarded replacement；guard 接受率 92.5%）【一手·公开 https://arxiv.org/abs/2608.19553】 |
| **候选 A′**（只融合、不回落） | **过 guard → 落中点**；**不过 → 落精点**（= 现状） | 施工清单 §2「异域一律走『**选**』不融合」+ **硬约束**：不得引入「离粗定位多远 → 回落」的标量阈值 |
| **候选 B**（两视图空间票表 + argmax） | 整屏视角与 crop 视角的采样点投到**同一张全屏物理坐标票表**，取最大票连通区 | GUI-RC §3.2【一手·公开 https://arxiv.org/abs/2508.05615】；ReClick 的 argmax **无绝对阈值**【一手·公开 https://arxiv.org/abs/2610.13268】 |

## 三步判定门（预注册，短路选择）

1. **第一步**：候选 A（或 A′）相对**现状 always 臂** **Δ ≥ +3 条**（+1.64 pp）
   **且**配对 mid-p McNemar **p < 0.05** → **采纳**，候选 B **不做**。
2. **第二步**（A 不过门）：用 T1 的 **oracle-of-8** 决定要不要做 B ——
   **oracle-of-8 ≥ 单次 + 8 pp** → 做 B；否则 **A/B 都不做**。
3. **第三步**（兜底）：**跨阶段一致性** —— 本轮**只埋点、不当门**
   （§3.2 已证明几何硬门净亏；且假命中场景里跨阶段 IoU 会很高）。

## 硬约束（写进代码评审清单）

**不得引入「离粗定位多远 → 回落」的标量阈值** —— 本机实测净亏 3.8~20.8 pp
【一手·本机；`docs/research/r-s1-guardrail.md` §1】。
本模块的候选 A′ **没有回落分支**；候选 A（LFPR 原样）**有**，
因此 A **必须**先过判定门才允许被采纳 —— 报告里写清是哪一条过了门。
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import configure_utf8_output
from .formats import SCREENSPOT_PRO
from .sspro import load_samples
from .stats import mcnemar_midp, paired_bootstrap_delta, wilson_ci

__all__ = [
    "FusionSample",
    "candidate_a",
    "candidate_a_prime",
    "guard_verdict",
    "main",
]

#: **LFPR 的 guard 阈值**（公开一手；【一手·公开 https://arxiv.org/abs/2608.19553】）。
#:
#: * ``z ≤ 0.35`` —— 归一化偏离；
#: * ``IoU ≥ 0.25`` —— 精点扩 α 方块与粗框的重叠；
#: * 面积比 ∈ ``[0.5, 2.0]`` —— 两者规模相当。
GUARD_MAX_Z = 0.35
GUARD_MIN_IOU = 0.25
GUARD_MIN_AREA_RATIO = 0.5
GUARD_MAX_AREA_RATIO = 2.0

#: **α（精点扩成的方块边长，物理像素）** —— 默认取**粗框自己的较大边**
#: （精点的误差尺度 ≈ 目标尺度 → **不用一个写死的像素常数**）。
#:
#: 另给一个**固定值**档（``--alpha-px``，方案 §六 6.3 给的 ≈39）作对照。
DEFAULT_ALPHA_PX = 39.0

#: **判定门**：候选相对现状 always 臂的**最少净增条数**与**显著性门槛**。
GATE_MIN_DELTA_ROWS = 3
GATE_MAX_P = 0.05

#: **第二步的判定门**：oracle-of-8 至少要比单次高这么多（百分点）。
ORACLE_GATE_PP = 8.0


@dataclass(frozen=True)
class FusionSample:
    """一条样本的**重算输入**（全部是物理像素或原图像素）。"""

    sample_id: str
    ui_type: str
    #: 粗定位点（物理像素）。
    coarse_point: tuple[float, float] | None
    #: 粗定位的原始框（物理像素四元组）。
    coarse_bbox_px: tuple[float, float, float, float] | None
    #: 精定位落点（物理像素）。
    refined_point: tuple[float, float] | None
    #: crop 窗口的物理矩形。
    crop_rect_px: tuple[float, float, float, float] | None
    #: 现状 always 臂的命中结论。
    always_hit: bool
    #: GT bbox（原图像素）与图像尺寸。
    gt_bbox: tuple[int, int, int, int]
    image_size: tuple[int, int]


def guard_verdict(
    coarse_bbox_px: tuple[float, float, float, float] | None,
    refined_point: tuple[float, float] | None,
    *,
    alpha_px: float,
    coarse_point: tuple[float, float] | None = None,
) -> dict[str, Any]:
    """**LFPR 式 guard** 的逐条判定（**逐项**给数，不给一个总评）。

    * ``z`` —— 归一化偏离 = ``‖精点 − 粗点‖ / α``；
    * ``iou`` —— 粗框与「精点扩 α 方块」的重叠；
    * ``area_ratio`` —— 方块面积 / 粗框面积。
    """
    if coarse_bbox_px is None or refined_point is None:
        return {
            "passed": False,
            "reason": "缺粗框或缺精定位点",
            "z": None,
            "iou": None,
            "area_ratio": None,
        }
    x1, y1, x2, y2 = coarse_bbox_px
    box_w = max(1e-6, abs(x2 - x1))
    box_h = max(1e-6, abs(y2 - y1))
    rx, ry = refined_point
    half = alpha_px / 2.0
    square = (rx - half, ry - half, rx + half, ry + half)
    intersection = _intersection_area(coarse_bbox_px, square)
    square_area = alpha_px * alpha_px
    box_area = box_w * box_h
    iou = intersection / max(1e-6, (box_area + square_area - intersection))
    area_ratio = square_area / box_area
    z = None
    if coarse_point is not None:
        z = math.hypot(rx - coarse_point[0], ry - coarse_point[1]) / max(1e-6, alpha_px)
    checks = {
        "z": z is None or z <= GUARD_MAX_Z,
        "iou": iou >= GUARD_MIN_IOU,
        "area_ratio": GUARD_MIN_AREA_RATIO <= area_ratio <= GUARD_MAX_AREA_RATIO,
    }
    failed = [name for name, ok in checks.items() if not ok]
    return {
        "passed": not failed,
        "reason": "过 guard" if not failed else f"不过：{'/'.join(failed)}",
        "z": z,
        "iou": iou,
        "area_ratio": area_ratio,
    }


def _intersection_area(
    a: tuple[float, float, float, float], b: tuple[float, float, float, float]
) -> float:
    left = max(min(a[0], a[2]), min(b[0], b[2]))
    top = max(min(a[1], a[3]), min(b[1], b[3]))
    right = min(max(a[0], a[2]), max(b[0], b[2]))
    bottom = min(max(a[1], a[3]), max(b[1], b[3]))
    if right <= left or bottom <= top:
        return 0.0
    return (right - left) * (bottom - top)


def _alpha_for(sample: FusionSample, alpha_mode: str, alpha_px: float) -> float:
    """**α**（精点扩成的方块边长，物理像素）。"""
    if alpha_mode == "fixed":
        return alpha_px
    if sample.coarse_bbox_px is not None:
        x1, y1, x2, y2 = sample.coarse_bbox_px
        return max(1.0, max(abs(x2 - x1), abs(y2 - y1)))
    return alpha_px


def candidate_a(sample: FusionSample, *, alpha_mode: str, alpha_px: float) -> tuple[
    tuple[float, float] | None, dict[str, Any]
]:
    """**候选 A**（LFPR 原样）：过 guard → **中点**；不过 → **粗点**。

    ⚠️ 这一条**有回落分支**（回到粗定位点）—— 本机实测这类「离粗定位多远
    → 回落」的规则净亏 3.8~20.8 pp → **必须先过判定门才允许被采纳**。
    """
    if sample.coarse_point is None:
        return None, {"reason": "没有粗定位点"}
    alpha = _alpha_for(sample, alpha_mode, alpha_px)
    verdict = guard_verdict(
        sample.coarse_bbox_px,
        sample.refined_point,
        alpha_px=alpha,
        coarse_point=sample.coarse_point,
    )
    if verdict["passed"] and sample.refined_point is not None:
        p0 = sample.coarse_point
        p1 = sample.refined_point
        return ((p0[0] + p1[0]) / 2.0, (p0[1] + p1[1]) / 2.0), verdict
    return sample.coarse_point, verdict


def candidate_a_prime(
    sample: FusionSample, *, alpha_mode: str, alpha_px: float
) -> tuple[tuple[float, float] | None, dict[str, Any]]:
    """**候选 A′**（只融合、不回落）：过 guard → **中点**；不过 → **精点**（= 现状）。

    **没有回落分支** → 天然满足「不得引入离粗定位多远 → 回落」的硬约束。
    """
    if sample.refined_point is None:
        return sample.coarse_point, {"reason": "没有精定位点"}
    alpha = _alpha_for(sample, alpha_mode, alpha_px)
    verdict = guard_verdict(
        sample.coarse_bbox_px,
        sample.refined_point,
        alpha_px=alpha,
        coarse_point=sample.coarse_point,
    )
    if verdict["passed"] and sample.coarse_point is not None:
        p0 = sample.coarse_point
        p1 = sample.refined_point
        return ((p0[0] + p1[0]) / 2.0, (p0[1] + p1[1]) / 2.0), verdict
    return sample.refined_point, verdict


def _hit(
    landing: tuple[float, float] | None,
    gt_bbox: tuple[int, int, int, int],
    image_size: tuple[int, int],
) -> bool:
    """官方 bbox 命中口径（**框内 = 命中**）。"""
    if landing is None:
        return False
    prediction = SCREENSPOT_PRO.wrap(
        (int(round(landing[0])), int(round(landing[1]))), image_size
    )
    return SCREENSPOT_PRO.hit(prediction, _eval_sample(gt_bbox, image_size))


def _eval_sample(
    gt_bbox: tuple[int, int, int, int], image_size: tuple[int, int]
):
    from .formats import EvalSample

    return EvalSample(bbox=gt_bbox, image_size=image_size)


def _load_report_samples(
    report: dict[str, Any], dataset_root: Path
) -> list[FusionSample]:
    """把报告的 ``outcomes.always`` 与数据集的 **GT** 对齐成重算输入。"""
    dataset = {sample.sample_id: sample for sample in load_samples(dataset_root)}
    #: 每个图像尺寸的**声明模型输入尺寸**（旧产物里没有 ``coarse_bbox_px``
    #: 时用它把「模型空间的框」换算成物理像素）。
    declared: dict[tuple[int, int], tuple[int, int]] = {}
    for record in report.get("calibration") or ():
        size = tuple(record["image_size"])
        declared[size] = tuple(record["declared_model_input_size"])
    samples: list[FusionSample] = []
    for outcome in report["outcomes"]["always"]:
        sample = dataset.get(outcome["sample_id"])
        if sample is None:
            continue
        coarse_point = (
            tuple(outcome["coarse_point"]) if outcome.get("coarse_point") else None
        )
        refined = outcome.get("refined_point_px")
        coarse_bbox = outcome.get("coarse_bbox_px")
        if not coarse_bbox and outcome.get("coarse_bbox_model"):
            coarse_bbox = _model_bbox_to_physical(
                outcome["coarse_bbox_model"], sample.image_size, declared
            )
        crop_rect = outcome.get("crop_rect_px")
        samples.append(
            FusionSample(
                sample_id=outcome["sample_id"],
                ui_type=outcome.get("ui_type", ""),
                coarse_point=coarse_point,  # type: ignore[arg-type]
                coarse_bbox_px=tuple(coarse_bbox) if coarse_bbox else None,  # type: ignore[arg-type]
                refined_point=tuple(refined) if refined else None,  # type: ignore[arg-type]
                crop_rect_px=tuple(crop_rect) if crop_rect else None,  # type: ignore[arg-type]
                always_hit=bool(outcome["hit"]),
                gt_bbox=sample.bbox,
                image_size=sample.image_size,
            )
        )
    return samples


def _model_bbox_to_physical(
    bbox: list[float],
    image_size: tuple[int, int],
    declared: dict[tuple[int, int], tuple[int, int]],
) -> list[float] | None:
    """「模型输入空间的框」→「原图像素」（旧产物没有 ``coarse_bbox_px`` 时的兜底）。

    整屏那一级的 capture = **原图本身**（1 像素 = 1 物理像素）→
    比例 = ``原图宽 / 声明的模型输入宽``。
    """
    model_input = declared.get(tuple(image_size))
    if model_input is None or not model_input[0]:
        return None
    scale_x = image_size[0] / model_input[0]
    scale_y = image_size[1] / model_input[1]
    return [
        bbox[0] * scale_x,
        bbox[1] * scale_y,
        bbox[2] * scale_x,
        bbox[3] * scale_y,
    ]


def _paired(always_hits: list[bool], candidate_hits: list[bool]) -> dict[str, Any]:
    """配对比较（mid-p McNemar + 配对 bootstrap Δ 的 95% CI）。"""
    b = sum(1 for a, c in zip(always_hits, candidate_hits, strict=True) if a and not c)
    c = sum(1 for a, c in zip(always_hits, candidate_hits, strict=True) if c and not a)
    n = len(always_hits)
    delta_rows = c - b
    delta_pp = (delta_rows / n * 100) if n else 0.0
    boot = paired_bootstrap_delta(
        list(zip(always_hits, candidate_hits, strict=True))
    )
    ci = (boot.ci_low * 100.0, boot.ci_high * 100.0)
    return {
        "samples": n,
        "always_hits": sum(always_hits),
        "candidate_hits": sum(candidate_hits),
        "discordant": {"always_only": b, "candidate_only": c},
        "delta_rows": delta_rows,
        "delta_pp": delta_pp,
        "mcnemar_midp_p": mcnemar_midp(b, c),
        "delta_ci95_pp": [ci[0], ci[1]],
        "gate_passed": delta_rows >= GATE_MIN_DELTA_ROWS
        and mcnemar_midp(b, c) < GATE_MAX_P,
    }


def _evaluate(
    samples: list[FusionSample],
    *,
    builder,
    alpha_mode: str,
    alpha_px: float,
) -> dict[str, Any]:
    """跑一条候选（**零模型调用**）→ 命中率 + 配对统计 + guard 的接受率。"""
    always_hits: list[bool] = []
    candidate_hits: list[bool] = []
    guard_passed = 0
    guard_total = 0
    guard_reasons: dict[str, int] = {}
    for sample in samples:
        landing, verdict = builder(
            sample, alpha_mode=alpha_mode, alpha_px=alpha_px
        )
        always_hits.append(sample.always_hit)
        candidate_hits.append(
            _hit(landing, sample.gt_bbox, sample.image_size)
        )
        if verdict.get("z") is not None or verdict.get("iou") is not None:
            guard_total += 1
            if verdict["passed"]:
                guard_passed += 1
            else:
                reason = str(verdict.get("reason", "")).split("：")[-1]
                guard_reasons[reason] = guard_reasons.get(reason, 0) + 1
    result = _paired(always_hits, candidate_hits)
    hits = sum(candidate_hits)
    n = len(candidate_hits)
    result["accuracy"] = hits / n if n else 0.0
    result["wilson_ci95"] = list(wilson_ci(hits, n)) if n else None
    result["guard_acceptance_rate"] = (
        guard_passed / guard_total if guard_total else None
    )
    result["guard_evaluated"] = guard_total
    result["guard_fail_reasons"] = guard_reasons
    return result


def main(argv=None) -> int:
    configure_utf8_output()
    parser = argparse.ArgumentParser(
        prog="minicode.eval.fusion_offline",
        description="护栏候选的离线重算（零模型调用；施工清单 T5）",
    )
    parser.add_argument("--run", required=True, help="跑批产物（sspro-report.json）")
    parser.add_argument(
        "--dataset",
        default=".scratch/sspro",
        help="数据集根（取 GT bbox；**零模型调用**）",
    )
    parser.add_argument(
        "--alpha-mode",
        choices=["bbox", "fixed"],
        default="bbox",
        help="α 的取法：``bbox`` = 粗框自己的较大边（默认）；``fixed`` = --alpha-px",
    )
    parser.add_argument(
        "--alpha-px",
        type=float,
        default=DEFAULT_ALPHA_PX,
        help="固定 α（物理像素；方案 §六 6.3 给的 ≈39）",
    )
    parser.add_argument(
        "--oracle",
        default="",
        help=(
            "T1 的 oracle-of-8 探针产物（``vote-probe.json``）—— "
            "**第二步判定门**用它决定要不要做候选 B"
        ),
    )
    args = parser.parse_args(argv)

    report_path = Path(args.run)
    report = json.loads(report_path.read_text(encoding="utf-8"))
    samples = _load_report_samples(report, Path(args.dataset))
    print(
        f"=== 离线重算（零模型调用）：{report_path}｜{len(samples)} 条 ==="
    )
    if not samples:
        print("没有可重算的样本")
        return 1

    candidates = {
        "A_lfpr_guard_and_midpoint": candidate_a,
        "A_prime_fuse_only": candidate_a_prime,
    }
    results: dict[str, Any] = {}
    for name, builder in candidates.items():
        result = _evaluate(
            samples,
            builder=builder,
            alpha_mode=args.alpha_mode,
            alpha_px=args.alpha_px,
        )
        results[name] = result
        gate = "过门" if result["gate_passed"] else "不过门"
        print(
            f"{name}：命中率 {result['accuracy'] * 100:.2f}%"
            f"（{result['candidate_hits']}/{result['samples']}）"
            f"｜现状 {result['always_hits']}/{result['samples']}"
            f"｜Δ {result['delta_pp']:+.2f} pp（{result['delta_rows']:+d} 条）"
            f"｜mid-p p={result['mcnemar_midp_p']:.4f}"
            f"｜CI [{result['delta_ci95_pp'][0]:+.2f}, {result['delta_ci95_pp'][1]:+.2f}] pp"
            f"｜guard 接受率 "
            f"{(result['guard_acceptance_rate'] * 100 if result['guard_acceptance_rate'] is not None else float('nan')):.1f}%"
            f"｜**{gate}**"
        )

    # -- 三步判定门（短路）--------------------------------------------------
    gate_log: list[dict[str, Any]] = []
    adopted = ""
    for name, result in results.items():
        gate_log.append(
            {
                "step": 1,
                "candidate": name,
                "delta_rows": result["delta_rows"],
                "delta_pp": result["delta_pp"],
                "mcnemar_midp_p": result["mcnemar_midp_p"],
                "passed": result["gate_passed"],
            }
        )
        if result["gate_passed"] and not adopted:
            adopted = name
    oracle: dict[str, Any] = {}
    if not adopted and args.oracle:
        oracle = json.loads(Path(args.oracle).read_text(encoding="utf-8"))
        single = oracle.get("single_hit_rate")
        of8 = oracle.get("oracle_of_8_hit_rate")
        gate_log.append(
            {
                "step": 2,
                "oracle_of_8_pp": of8,
                "single_pp": single,
                "passed": bool(
                    single is not None
                    and of8 is not None
                    and (of8 - single) * 100 >= ORACLE_GATE_PP
                ),
            }
        )
    gate_log.append(
        {
            "step": 3,
            "note": (
                "跨阶段一致性：**本轮只埋点、不当门**"
                "（几何硬门本机实测净亏 3.8~20.8 pp；"
                "且假命中场景里跨阶段 IoU 会很高 → 这条判据大概率抓不到假命中）"
            ),
            "passed": False,
        }
    )

    output = {
        "input_run": str(report_path),
        "dataset": str(args.dataset),
        "samples": len(samples),
        "alpha_mode": args.alpha_mode,
        "alpha_px": args.alpha_px,
        "guard_thresholds": {
            "max_z": GUARD_MAX_Z,
            "min_iou": GUARD_MIN_IOU,
            "area_ratio": [GUARD_MIN_AREA_RATIO, GUARD_MAX_AREA_RATIO],
        },
        "candidates": results,
        "decision_gate": {
            "log": gate_log,
            "adopted": adopted,
            "note": (
                "候选 B（两视图空间票表 + argmax）**只有在第一步不过门、"
                "且第二步的 oracle-of-8 判定通过**时才做"
            ),
        },
        "hard_constraint": (
            "不得引入「离粗定位多远 → 回落」的标量阈值"
            "（本机实测净亏 3.8~20.8 pp）—— "
            "候选 A′ 没有回落分支；候选 A（LFPR 原样）有 → 必须过判定门才允许采纳"
        ),
    }
    out_path = report_path.parent / "fusion-offline.json"
    out_path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"=== 判定门：{'采纳 ' + adopted if adopted else '两条候选都不采纳'} ===")
    print(f"记录已写入：{out_path}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())

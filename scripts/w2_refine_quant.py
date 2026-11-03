"""W2.1 精定位专项量化：**只测精定位一级**（ScreenSpot-Pro 同批 60 条，配对口径）。

**要分开的两件事**：粗定位错 ≠ 精定位弱。做法：跳过真实粗定位，
把「已知粗点」注入生产两级链路（``TwoStageGrounder``），只让精定位一级真跑
（真裁剪、真放大、真问模型、真换算）—— 粗定位那一级被替换成注入的应答，
**其余每一行代码都是生产代码**（不复制管线，杜绝口径漂移）。

**三臂**（每臂同批 60 条；基线 = W6 pixel 臂 = 粗定位单独，零新调用复用）：

* ``real_nz`` —— 粗点 = W6 pixel 臂的真实落点（surface px → 模型输入 px），
  注入**零面积框**（半径 0 → 不放大）。W6 没粗点的样本跳过（如实记录）。
* ``gt_nz`` —— 粗点 = GT 框中心，注入零面积框（不放大）。
* ``gt_zoom`` —— 粗点 = GT 框中心，注入 **GT 框本身**（半径 = 真实目标半径
  → 走生产放大规则）。模拟「理想粗定位自报了正确的框」。

三个两两对比各自**单变量**：

* ``gt_nz`` vs ``real_nz`` —— 粗点质量的影响（同「不放大」口径）；
* ``gt_zoom`` vs ``gt_nz`` —— 放大的影响（同「GT 粗点」条件）;
* 各臂 vs W6 基线 —— 两级链路 vs 只用粗定位（配对翻转 + mid-p McNemar）。

**判定**：官方口径（落点落在 GT bbox 内）。**误差**：落点与 GT 中心的表面像素
距离（中位/均值/p90）。**变好/变差**：与基线逐样本配对。**回落**：精定位失败
回落到粗点的比例与原因分布。

⚠️ 诚实口径：``real_nz`` 注入的是零面积框 —— 生产里模型真框会触发放大，
这里是**下界口径**（zoom 的影响由 ``gt_zoom`` vs ``gt_nz`` 单独量）。

用法（仓库根目录）::

    python -I scripts/w2_refine_quant.py --out runs/w2
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import fields as dataclass_fields
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "src"))

from minicode.coord.coordinates import ModelScale  # noqa: E402
from minicode.eval.png_read import read_png_frame  # noqa: E402
from minicode.eval.sspro import (  # noqa: E402
    ArmOutcome,
    GateMode,
    _run_one,
    load_samples,
    paired_comparison,
    stratified_subset,
)
from minicode.model.client import ModelClient  # noqa: E402
from minicode.model.config import load_config  # noqa: E402
from minicode.model.image import model_input_size_for  # noqa: E402
from minicode.model.request_log import JsonlRequestLog  # noqa: E402
from minicode.model.retry import RetryingModelCaller  # noqa: E402

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

#: 精定位提示词的首句（prompt.py 里唯一的「裁出的小区域」开头）——
#: 注入客户端据此区分「粗定位（注入）」与「精定位（真问模型）」。
_REFINE_PROMPT_PREFIX = "这是一张从 Windows 桌面截图里裁出的小区域"


class _CannedReply:
    """注入应答的最小壳（``_ask_text`` 只读 ``reply.text``）。"""

    def __init__(self, text: str) -> None:
        self.text = text


class _ThreadLocalRealClient:
    """每线程一套真客户端（ModelClient 非线程安全；请求日志有锁可共享）。"""

    def __init__(self, log_path: Path) -> None:
        self._log_path = log_path
        self._local = threading.local()

    def client(self) -> RetryingModelCaller:
        holder = getattr(self._local, "holder", None)
        if holder is None:
            holder = RetryingModelCaller(
                ModelClient(load_config(), JsonlRequestLog(self._log_path))
            )
            self._local.holder = holder
        return holder


class CoarseInjectingClient:
    """**注入式客户端**：粗定位调用回罐头应答，精定位调用转发真模型。

    生产 ``TwoStageGrounder`` 对模型的唯一入口是 ``complete(prompt, ...)``；
    粗定位与精定位的提示词首句不同（见 :data:`_REFINE_PROMPT_PREFIX`）——
    据此路由。本实验里 variants=1、无候选源，链路上只有这两类调用。
    """

    def __init__(self, real: _ThreadLocalRealClient, coarse_text: str) -> None:
        self._real = real
        self._coarse_text = coarse_text

    def complete(self, prompt: str, **kwargs: Any):
        if prompt.startswith(_REFINE_PROMPT_PREFIX):
            return self._real.client().complete(prompt, **kwargs)
        return _CannedReply(self._coarse_text)


def _scale_to_model_input(value: float, surface_size: int, model_input_size: int) -> int:
    """表面像素 → 模型输入像素（整屏那一级的等比缩放）。"""
    return int(round(value * model_input_size / surface_size))


def _canned_coarse(bbox_model_input: list[int]) -> str:
    """注入的粗定位应答（与生产提示词要求的 JSON 逐字同格式）。"""
    return json.dumps({"bbox": bbox_model_input, "text": ""}, ensure_ascii=False)


def _surface_to_model_input_bbox(
    bbox: tuple[float, float, float, float],
    surface_size: tuple[int, int],
    model_input: tuple[int, int],
) -> list[int]:
    x1, y1, x2, y2 = bbox
    return [
        _scale_to_model_input(x1, surface_size[0], model_input[0]),
        _scale_to_model_input(y1, surface_size[1], model_input[1]),
        _scale_to_model_input(x2, surface_size[0], model_input[0]),
        _scale_to_model_input(y2, surface_size[1], model_input[1]),
    ]


def _outcome_from_dict(payload: dict[str, Any]) -> ArmOutcome:
    """W6 产物里的 dict → ``ArmOutcome``（字段交集；列表回元组不必须 ——
    配对统计只读 ``hit``，误差计算只做索引）。"""
    names = {f.name for f in dataclass_fields(ArmOutcome)}
    return ArmOutcome(**{k: v for k, v in payload.items() if k in names})


def _dist(a: tuple[float, float] | None, b: tuple[float, float]) -> float | None:
    if a is None:
        return None
    return ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5


def _percentile(values: list[float], q: float) -> float | None:
    """线性插值分位数（与 numpy 默认插值同口径）。"""
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * q
    low = int(position)
    high = min(low + 1, len(ordered) - 1)
    fraction = position - low
    return ordered[low] + (ordered[high] - ordered[low]) * fraction


def _error_stats(errors: list[float]) -> dict[str, Any]:
    return {
        "n": len(errors),
        "median": _percentile(errors, 0.5),
        "mean": (sum(errors) / len(errors)) if errors else None,
        "p90": _percentile(errors, 0.9),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="W2.1 精定位专项量化（注入粗点，只测精定位一级）")
    parser.add_argument(
        "--dataset",
        default=r"C:\Users\22197\Desktop\ik评测\sspro-dataset",
        help="ScreenSpot-Pro 数据根（仓库外）",
    )
    parser.add_argument("--out", default="runs/w2")
    parser.add_argument("--per-stratum", type=int, default=10)
    parser.add_argument("--seed", type=int, default=20251020)
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--effort", default="low")
    parser.add_argument("--max-tokens", type=int, default=8192)
    args = parser.parse_args()

    import os

    env = _ROOT / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, value = line.partition("=")
                os.environ.setdefault(key.strip(), value.strip())

    config = load_config()
    print(f"=== 模型：{config.model}（端点：{config.endpoint}）===", flush=True)

    # -- 基线：W6 pixel 臂（粗定位单独；同批同提示词同判定）---------------
    w6_path = _ROOT / "runs" / "w6" / "w6-paired.json"
    if not w6_path.is_file():
        raise SystemExit(f"找不到 W6 基线产物：{w6_path}")
    w6_report = json.loads(w6_path.read_text(encoding="utf-8"))
    baseline_rows = {row["sample_id"]: row for row in w6_report["outcomes"]["pixel"]}
    w6_raw = {
        row["sample_id"]: row
        for row in json.loads((_ROOT / "runs" / "w6" / "w6-raw.json").read_text(encoding="utf-8"))
    }
    baseline_outcomes = [_outcome_from_dict(row) for row in w6_report["outcomes"]["pixel"]]
    baseline_model = w6_report.get("model", "")
    print(
        f"=== 基线：W6 pixel 臂 {len(baseline_outcomes)} 条（model={baseline_model}）===",
        flush=True,
    )
    if baseline_model and baseline_model != config.model:
        raise SystemExit(
            f"W6 基线模型 {baseline_model!r} 与当前配置 {config.model!r} 不一致 —— 配对前提被破坏，拒绝跑"
        )

    samples = load_samples(Path(args.dataset))
    subset = stratified_subset(samples, args.per_stratum, seed=args.seed)
    print(f"=== 样本：{len(subset)} 条（每层 {args.per_stratum}，seed={args.seed}）===", flush=True)

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    real_clients = _ThreadLocalRealClient(out_dir / "model-requests.jsonl")

    ARM_NAMES = ("real_nz", "gt_nz", "gt_zoom")
    arm_outcomes: dict[str, list[ArmOutcome]] = {name: [] for name in ARM_NAMES}
    arm_rows: dict[str, list[dict[str, Any]]] = {name: [] for name in ARM_NAMES}
    skipped: dict[str, list[dict[str, str]]] = {name: [] for name in ARM_NAMES}
    started = time.time()
    done = 0

    def work(sample) -> None:
        nonlocal done
        frame = read_png_frame(sample.image_path)
        surface = (0, 0, frame.width, frame.height)
        model_input = model_input_size_for(sample.image_size[0], sample.image_size[1])
        scale = ModelScale.ABSOLUTE_MODEL_INPUT

        gt_center_surface = (
            (sample.bbox[0] + sample.bbox[2]) / 2.0,
            (sample.bbox[1] + sample.bbox[3]) / 2.0,
        )
        gt_bbox_mi = _surface_to_model_input_bbox(sample.bbox, sample.image_size, model_input)
        gt_center_mi = [
            _scale_to_model_input(gt_center_surface[0], sample.image_size[0], model_input[0]),
            _scale_to_model_input(gt_center_surface[1], sample.image_size[1], model_input[1]),
        ]

        base_row = baseline_rows.get(sample.sample_id)
        raw_row = w6_raw.get(sample.sample_id)
        real_point_surface = raw_row["pixel"]["surface_point"] if raw_row else None

        plans: dict[str, list[int] | None] = {
            "real_nz": None,
            "gt_nz": [gt_center_mi[0], gt_center_mi[1], gt_center_mi[0], gt_center_mi[1]],
            "gt_zoom": gt_bbox_mi,
        }
        if real_point_surface is not None:
            plans["real_nz"] = [
                _scale_to_model_input(real_point_surface[0], sample.image_size[0], model_input[0]),
                _scale_to_model_input(real_point_surface[1], sample.image_size[1], model_input[1]),
            ] * 2

        for arm in ARM_NAMES:
            bbox_mi = plans[arm]
            if bbox_mi is None:
                skipped[arm].append(
                    {
                        "sample_id": sample.sample_id,
                        "reason": "W6 pixel 臂无粗点（粗定位失败），精定位无处安窗",
                    }
                )
                continue
            client = CoarseInjectingClient(real_clients, _canned_coarse(bbox_mi))
            outcome = _run_one(
                sample,
                frame,
                surface,
                scale,
                model_input,
                GateMode.ALWAYS,
                args.effort,
                args.max_tokens,
                client,
            )
            arm_outcomes[arm].append(outcome)
            error_vs_gt = _dist(
                (tuple(outcome.landing_point) if outcome.landing_point is not None else None),
                gt_center_surface,
            )
            base_error = None
            if base_row is not None:
                base_error = _dist(
                    (
                        tuple(base_row["landing_point"])
                        if base_row["landing_point"] is not None
                        else None
                    ),
                    gt_center_surface,
                )
            arm_rows[arm].append(
                {
                    "sample_id": sample.sample_id,
                    "ui_type": sample.ui_type,
                    "platform": sample.platform,
                    "injected_coarse_model_input": bbox_mi,
                    "injected_coarse_surface": None if arm != "real_nz" else real_point_surface,
                    "landing_point": outcome.landing_point,
                    "hit": outcome.hit,
                    "fallback": outcome.fallback,
                    "fallback_cause": outcome.fallback_cause,
                    "error_vs_gt_center_px": error_vs_gt,
                    "baseline_error_vs_gt_center_px": base_error,
                    "refine_upscale_factor": outcome.refine_upscale_factor,
                    "crop_contains_target": outcome.crop_contains_target,
                    "model_calls": outcome.model_calls,
                    "error": outcome.error,
                }
            )
        done += 1
        if done % 5 == 0 or done == len(subset):
            print(
                f"[{done}/{len(subset)}] "
                + " ".join(
                    f"{arm}={sum(1 for o in arm_outcomes[arm] if o.hit)}/{len(arm_outcomes[arm])}"
                    for arm in ARM_NAMES
                )
                + f" ({time.time() - started:.0f}s)",
                flush=True,
            )

    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        list(pool.map(work, subset))

    # -- 汇总 ---------------------------------------------------------------
    baseline_errors = [
        row["baseline_error_vs_gt_center_px"]
        for arm in ARM_NAMES
        for row in arm_rows[arm]
        if row["baseline_error_vs_gt_center_px"] is not None
    ]
    arm_summaries: dict[str, Any] = {}
    pairings: dict[str, Any] = {}
    for arm in ARM_NAMES:
        rows = arm_rows[arm]
        errors = [
            row["error_vs_gt_center_px"] for row in rows if row["error_vs_gt_center_px"] is not None
        ]
        with_baseline = [
            row
            for row in rows
            if row["baseline_error_vs_gt_center_px"] is not None
            and row["error_vs_gt_center_px"] is not None
        ]
        improved = sum(
            1
            for row in with_baseline
            if row["error_vs_gt_center_px"] < row["baseline_error_vs_gt_center_px"]
        )
        worsened = sum(
            1
            for row in with_baseline
            if row["error_vs_gt_center_px"] > row["baseline_error_vs_gt_center_px"]
        )
        baseline_hits = sum(1 for row in with_baseline if baseline_rows[row["sample_id"]]["hit"])
        arm_hits = sum(1 for row in with_baseline if row["hit"])
        causes: dict[str, int] = {}
        for row in rows:
            if row["fallback"]:
                causes[row["fallback_cause"]] = causes.get(row["fallback_cause"], 0) + 1
        arm_summaries[arm] = {
            "n_run": len(rows),
            "n_skipped": len(skipped[arm]),
            "hits": sum(1 for o in arm_outcomes[arm] if o.hit),
            "hit_rate": (sum(1 for o in arm_outcomes[arm] if o.hit) / len(rows) if rows else None),
            "error_vs_gt_center": _error_stats(errors),
            "paired_vs_baseline": {
                "n_paired": len(with_baseline),
                "baseline_hits": baseline_hits,
                "arm_hits": arm_hits,
                "improved_error": improved,
                "worsened_error": worsened,
                "both_hit": sum(
                    1
                    for row in with_baseline
                    if row["hit"] and baseline_rows[row["sample_id"]]["hit"]
                ),
                "baseline_only_hit": sum(
                    1
                    for row in with_baseline
                    if baseline_rows[row["sample_id"]]["hit"] and not row["hit"]
                ),
                "arm_only_hit": sum(
                    1
                    for row in with_baseline
                    if row["hit"] and not baseline_rows[row["sample_id"]]["hit"]
                ),
                "neither_hit": sum(
                    1
                    for row in with_baseline
                    if not row["hit"] and not baseline_rows[row["sample_id"]]["hit"]
                ),
            },
            "fallback": {
                "count": sum(1 for row in rows if row["fallback"]),
                "causes": causes,
            },
            "zoom": {
                "upscale_factors": sorted(
                    {row["refine_upscale_factor"] for row in rows if row["refine_upscale_factor"]}
                ),
                "crop_contains_target_true": sum(1 for row in rows if row["crop_contains_target"]),
            },
        }
        pairings[arm] = paired_comparison(
            baseline_outcomes,
            arm_outcomes[arm],
            label_left="coarse_only_w6",
            label_right=f"coarse+refine:{arm}",
        )

    report = {
        "experiment": "W2.1 精定位专项量化：注入粗点，只测精定位一级（配对口径）",
        "model": config.model,
        "endpoint": config.endpoint,
        "baseline": {
            "source": "runs/w6/w6-paired.json 的 pixel 臂（粗定位单独，零新调用复用）",
            "model": baseline_model,
            "n": len(baseline_outcomes),
            "hits": sum(1 for o in baseline_outcomes if o.hit),
            "error_vs_gt_center": _error_stats(baseline_errors),
        },
        "sample_batch": {
            "dataset": "ScreenSpot-Pro（likaixin/ScreenSpot-Pro，存于仓库外）",
            "per_stratum": args.per_stratum,
            "seed": args.seed,
            "n": len(subset),
        },
        "arms": {
            "real_nz": "粗点 = W6 pixel 臂真实落点，零面积框（不放大）；W6 无粗点的样本跳过",
            "gt_nz": "粗点 = GT 框中心，零面积框（不放大）",
            "gt_zoom": "粗点 = GT 框中心 + GT 框（真实半径 → 生产放大规则）",
        },
        "judge": "官方口径：落点（表面像素）落在 GT bbox 内；误差 = 落点与 GT 中心的表面像素距离",
        "summaries": arm_summaries,
        "paired_stats_vs_baseline": pairings,
        "outcomes": {arm: [o.to_dict() for o in arm_outcomes[arm]] for arm in ARM_NAMES},
        "rows": arm_rows,
        "skipped": skipped,
    }
    (out_dir / "refine-quant.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(arm_summaries, ensure_ascii=False, indent=1)[:3000])
    print(f"=== 完成：{out_dir / 'refine-quant.json'} ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

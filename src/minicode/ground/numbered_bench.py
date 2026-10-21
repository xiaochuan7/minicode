"""**编号选择**分支的落点测量（工单 B-1）—— ScreenSpot-Pro 分层抽样。

**测什么**：视觉通道走「模型选编号」那条路时，**最终落点**离真值多远。

**真值口径**（与 M7/M9 同一套）：**GT 框的中心**（数据集标注的
``[x1, y1, x2, y2]`` 的中心，原图像素）；误差 = ‖落点 − GT 中心‖（物理像素）。

* **click accuracy**（点落在 GT 框内即命中）是**另一个口径**，单独报，
  **不混进像素误差那张表**（那是生态的公开口径，不是我们的像素误差）。

**抽样是决定性的**：按 (平台 × 文本/图标) 分层，每层固定条数，
种子写在命令行与记录里（重跑能复现同一批样本）。

命令::

    $env:PYTHONPATH="src"; python -m minicode.ground.numbered_bench \\
        --dataset .scratch/sspro --per-stratum 7 --seed 20251020 \\
        --out runs/b1-numbered

⚠️ **只读** :mod:`minicode.eval.sspro` 的抽样与样本读取（不改它）——
统计与落盘都在本模块里。
"""

from __future__ import annotations

import argparse
import json
import math
import os
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from minicode.action.ports import CapturedFrame, VisualLocateRequest
from minicode.coord.coordinates import ModelScale
from minicode.eval.png_read import read_png_frame
from minicode.eval.sspro import SsproSample, load_samples, stratified_subset
from minicode.ground.caliber import CaliberSample, summarize_samples
from minicode.ground.candidates import OcrCandidateSource
from minicode.ground.channel import TwoStageGrounder
from minicode.model.client import ModelClient
from minicode.model.config import load_config
from minicode.model.effort import Effort
from minicode.model.image import model_input_size_for
from minicode.model.request_log import JsonlRequestLog
from minicode.verification.ocr_windows import WindowsOcrPort

__all__ = ["main", "run_samples"]


def _truth(sample: SsproSample) -> tuple[float, float]:
    """**真值 = GT 框的中心**（原图像素）。"""
    x1, y1, x2, y2 = sample.bbox
    return ((x1 + x2) / 2.0, (y1 + y2) / 2.0)


def _inside(point: tuple[float, float], sample: SsproSample) -> bool:
    """点落在 GT 框内吗（click accuracy 的判据 —— **另一个口径**）。"""
    x1, y1, x2, y2 = sample.bbox
    return x1 <= point[0] <= x2 and y1 <= point[1] <= y2


class _FixedCandidates:
    """**候选源**的定值实现（这批样本的候选只算一次）。"""

    def __init__(self, candidates: Sequence[Any]):
        self._candidates = list(candidates)

    def candidates(self, frame: CapturedFrame, description: str) -> list[Any]:
        return list(self._candidates)


def _run_one(
    sample: SsproSample,
    client,
    ocr_port: WindowsOcrPort,
    *,
    effort: Effort | str,
    max_tokens: int,
) -> dict[str, Any]:
    """跑一条样本的**编号分支**，返回一条记录。"""
    frame: CapturedFrame = read_png_frame(sample.image_path)
    width, height = frame.width, frame.height
    model_input = model_input_size_for(width, height)
    words = ocr_port.read_words(frame)
    candidates = OcrCandidateSource(lambda _frame: words).candidates(
        frame, sample.instruction
    )
    source = _FixedCandidates(candidates)
    grounder = TwoStageGrounder(
        client,
        # 编号分支**不用标度**（模型只答编号，落点取候选框的物理中心，零换算）；
        # 构造参数必填，这里给一个**不参与换算**的标度。
        scale=ModelScale.ABSOLUTE_MODEL_INPUT,
        model_input_size=model_input,
        full_screen_size=(width, height),
        effort=effort,
        max_tokens=max_tokens,
        candidate_source=source,
    )
    result = grounder.locate(
        VisualLocateRequest(
            target_description=sample.instruction,
            frame=frame,
            surface=(0, 0, width, height),
        )
    )
    truth = _truth(sample)
    landing = result.landing_point
    # 候选框的**物理中心**（回显里记的就是它）→ 与落点的差 = 框中心取整误差。
    center = next((echo for echo in result.echoes if echo.stage == "numbered"), None)
    error = (
        math.hypot(landing[0] - truth[0], landing[1] - truth[1]) if landing else None
    )
    rounding = (
        math.hypot(landing[0] - center.raw_x, landing[1] - center.raw_y)
        if (landing and center is not None and center.raw_x is not None)
        else None
    )
    # **候选召回**：清单里有没有一个候选落在 GT 框内（= 目标真被找到了）。
    recall = any(
        _inside(
            (candidate.center[0], candidate.center[1]),
            sample,
        )
        for candidate in candidates
    )
    return {
        "sample_id": sample.sample_id,
        "platform": sample.platform,
        "ui_type": sample.ui_type,
        "group": sample.group,
        "application": sample.application,
        "instruction": sample.instruction,
        "image_size": [width, height],
        "model_input_size": list(model_input),
        "candidate_count": len(candidates),
        "candidate_texts": [candidate.text for candidate in candidates],
        "candidate_recall": recall,
        "landing_point": list(landing) if landing else None,
        "candidate_center": (
            [center.raw_x, center.raw_y]
            if (center is not None and center.raw_x is not None)
            else None
        ),
        "truth": [truth[0], truth[1]],
        "landing_error_px": error,
        "rounding_error_px": rounding,
        "hit": bool(landing and _inside((float(landing[0]), float(landing[1])), sample)),
        "model_calls": result.model_calls,
        "error": result.error_message,
    }


def run_samples(
    samples: Sequence[SsproSample],
    client,
    *,
    ocr_language: str = "en-US",
    effort: Effort | str = Effort.LOW,
    max_tokens: int = 2000,
    workers: int = 4,
    progress=None,
    checkpoint: Path | None = None,
) -> list[dict[str, Any]]:
    """跑一批样本（**按样本并行**：OCR 是本机进程、模型调用是网络往返）。

    ``checkpoint`` —— 逐条落盘的 JSONL（跑挂了也不丢已跑的）。
    """
    from concurrent.futures import ThreadPoolExecutor

    ocr_port = WindowsOcrPort(language=ocr_language)
    done: dict[str, dict[str, Any]] = {}
    if checkpoint is not None and checkpoint.exists():
        for line in checkpoint.read_text(encoding="utf-8").splitlines():
            if line.strip():
                record = json.loads(line)
                done[str(record["sample_id"])] = record
        if done:
            print(f"断点续跑：已跑过 {len(done)} 条")

    def run(sample: SsproSample) -> dict[str, Any]:
        try:
            return _run_one(
                sample, client, ocr_port, effort=effort, max_tokens=max_tokens
            )
        except Exception as exc:  # 一条样本跑挂（OCR 失败 / 模型调用异常）→ 记下来
            return {
                "sample_id": sample.sample_id,
                "platform": sample.platform,
                "ui_type": sample.ui_type,
                "group": sample.group,
                "application": sample.application,
                "instruction": sample.instruction,
                "candidate_count": 0,
                "candidate_texts": [],
                "candidate_recall": False,
                "landing_point": None,
                "candidate_center": None,
                "truth": list(_truth(sample)),
                "landing_error_px": None,
                "rounding_error_px": None,
                "hit": False,
                "model_calls": 0,
                "error": f"{type(exc).__name__}: {exc}",
            }

    outcomes: list[dict[str, Any]] = []
    pending = [sample for sample in samples if sample.sample_id not in done]
    if checkpoint is not None:
        checkpoint.parent.mkdir(parents=True, exist_ok=True)
    with open(checkpoint, "a", encoding="utf-8") if checkpoint is not None else open(
        os.devnull, "w", encoding="utf-8"
    ) as sink:
        def record(outcome: dict[str, Any]) -> None:
            outcomes.append(outcome)
            if checkpoint is not None:
                sink.write(json.dumps(outcome, ensure_ascii=False) + "\n")
                sink.flush()
            if progress is not None:
                progress(
                    f"[{len(outcomes)}/{len(pending)}] {outcome['sample_id']} "
                    f"误差 {outcome['landing_error_px']}"
                )

        if workers <= 1:
            for sample in pending:
                record(run(sample))
        else:
            with ThreadPoolExecutor(max_workers=workers) as pool:
                for outcome in pool.map(run, pending):
                    record(outcome)
    merged = [done[sample.sample_id] for sample in samples if sample.sample_id in done]
    merged.extend(outcomes)
    order = {sample.sample_id: index for index, sample in enumerate(samples)}
    merged.sort(key=lambda outcome: order.get(str(outcome["sample_id"]), 0))
    return merged


def _caliber_samples(outcomes: Sequence[dict[str, Any]]) -> list[CaliberSample]:
    """记录 → **统一口径**的样本（:mod:`minicode.ground.caliber`）。

    * ``truth`` —— GT 框中心；
    * ``coarse_point`` —— **候选框的物理中心**（这一级的「整屏点」）；
    * ``landing_point`` —— 最终落点（取整后的整数）。
    → 「L0 误差」与「落点误差」之差 = **框中心取整误差**。
    """
    return [
        CaliberSample(
            name=str(outcome["sample_id"]),
            truth=(outcome["truth"][0], outcome["truth"][1]),
            coarse_point=(
                (outcome["candidate_center"][0], outcome["candidate_center"][1])
                if outcome["candidate_center"]
                else None
            ),
            landing_point=(
                (outcome["landing_point"][0], outcome["landing_point"][1])
                if outcome["landing_point"]
                else None
            ),
            # 编号分支**没有** crop 精定位这一级。
            refine_enabled=False,
            fallback=False,
            source="sspro/numbered",
        )
        for outcome in outcomes
    ]


def _rounding_distribution(outcomes: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """**框中心取整误差**的分布（落点 vs 选中候选框的物理中心）。"""
    from minicode.ground.stats import summarize_errors

    values = [
        outcome["rounding_error_px"]
        for outcome in outcomes
        if outcome["rounding_error_px"] is not None
    ]
    if not values:
        return {}
    return summarize_errors(values).to_dict()


def _error_distribution(outcomes: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """**落点误差**的分布（落点 vs GT 框中心）—— 只有落点的样本才算。"""
    from minicode.ground.stats import summarize_errors

    values = [
        outcome["landing_error_px"]
        for outcome in outcomes
        if outcome["landing_error_px"] is not None
    ]
    if not values:
        return {}
    return summarize_errors(values).to_dict()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="minicode.ground.numbered_bench",
        description="编号选择分支的落点误差（真值 = GT 框中心）",
    )
    parser.add_argument("--dataset", default=".scratch/sspro", help="数据集根目录")
    parser.add_argument("--per-stratum", type=int, default=7, help="每个 (平台 × 文本/图标) 分层取几条")
    parser.add_argument("--seed", type=int, default=20251020, help="抽样种子（决定性）")
    parser.add_argument("--out", default="runs/b1-numbered", help="记录落盘目录")
    parser.add_argument("--effort", default=Effort.LOW.value, help="思考预算档位")
    parser.add_argument("--max-tokens", type=int, default=2000)
    parser.add_argument("--ocr-language", default="en-US", help="Windows OCR 的语言")
    parser.add_argument("--workers", type=int, default=4, help="按样本并行的线程数")
    parser.add_argument("--limit", type=int, default=0, help="最多跑多少条（调试用）")
    args = parser.parse_args(argv)

    started = time.time()
    dataset = Path(args.dataset)
    samples = load_samples(dataset)
    chosen = stratified_subset(samples, args.per_stratum, seed=args.seed)
    chosen = [sample for sample in chosen if sample.image_path.exists()]
    if args.limit:
        chosen = chosen[: args.limit]
    print(f"=== 抽样：{len(chosen)} 条（种子 {args.seed}，每层 {args.per_stratum}）===")
    for key in sorted({(s.platform, s.ui_type) for s in chosen}):
        count = sum(1 for s in chosen if (s.platform, s.ui_type) == key)
        print(f"  {key[0]}/{key[1]}: {count} 条")

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    config = load_config()
    client = ModelClient(config, JsonlRequestLog(out_dir / "model-requests.jsonl"))

    outcomes = run_samples(
        chosen,
        client,
        ocr_language=args.ocr_language,
        effort=args.effort,
        max_tokens=args.max_tokens,
        workers=args.workers,
        checkpoint=out_dir / "outcomes.jsonl",
        progress=lambda line: print(line, flush=True),
    )

    hits = sum(1 for outcome in outcomes if outcome["hit"])
    report = summarize_samples(
        _caliber_samples(outcomes), source="sspro/numbered", click_hits=hits
    )
    payload = report.to_dict()
    payload["seed"] = args.seed
    payload["per_stratum"] = args.per_stratum
    payload["strata"] = sorted(
        {f"{s.platform}/{s.ui_type}" for s in chosen}
    )
    payload["rounding_error_px"] = _rounding_distribution(outcomes)
    payload["candidates"] = {
        "with_candidates": sum(1 for o in outcomes if o["candidate_count"]),
        "without_candidates": sum(1 for o in outcomes if not o["candidate_count"]),
        "recall": sum(1 for o in outcomes if o.get("candidate_recall")),
        "errors": sum(1 for o in outcomes if o["error"]),
    }
    payload["by_ui_type"] = {
        ui_type: {
            "samples": sum(1 for o in outcomes if o["ui_type"] == ui_type),
            "with_landing": sum(
                1
                for o in outcomes
                if o["ui_type"] == ui_type and o["landing_point"] is not None
            ),
            "hits": sum(1 for o in outcomes if o["ui_type"] == ui_type and o["hit"]),
            "recall": sum(
                1 for o in outcomes if o["ui_type"] == ui_type and o.get("candidate_recall")
            ),
            "landing_error_px": _error_distribution(
                [o for o in outcomes if o["ui_type"] == ui_type]
            ),
        }
        for ui_type in sorted({o["ui_type"] for o in outcomes})
    }
    payload["outcomes"] = list(outcomes)
    (out_dir / "numbered-report.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(report.render())
    print(
        f"框中心取整误差（落点 vs 选中候选框中心）：{payload['rounding_error_px']}"
    )
    print(
        f"候选：有候选 {payload['candidates']['with_candidates']} 条 / "
        f"无候选 {payload['candidates']['without_candidates']} 条 / "
        f"报错 {payload['candidates']['errors']} 条"
    )
    print(f"记录已写入：{out_dir / 'numbered-report.json'}")
    print(f"用时 {time.time() - started:.1f} 秒")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

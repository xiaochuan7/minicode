"""W7 主线 A 的同批配对对照：新视觉模型（Qwen3.8-Flash-Next）像素臂 vs
历史 deepseek 像素臂（docs/w6-paired.json，同批 60 条，seed=20251020）。

复用 w6_paired_coords.py 的几何/提示词/判定口径（同一把尺）：
* 同批样本 stratified_subset(samples, 10, seed=20251020)；
* 同一缩放（model_input_size_for → downscale）与同一像素臂提示词；
* 同一判定（官方 bbox 包含口径）；
* 串行调用（视觉通道纪律 in-flight=1），可配重试 + 落盘。

输出：runs/w7-vision-switch/paired-qwen38/pixel-arm.json + 请求/重试日志。
McNemar 与 Wilson CI 在 _night_paired_analysis.py 里离线算（不重跑模型）。
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "src"))
sys.path.insert(0, str(_ROOT / "scripts"))

from w6_paired_coords import _ask_bBox_center, _geometry  # noqa: E402

from minicode.action.image import encode_frame_base64  # noqa: E402
from minicode.coord.coordinates import ModelScale  # noqa: E402
from minicode.eval.png_read import read_png_frame  # noqa: E402
from minicode.eval.sspro import (  # noqa: E402
    load_samples,
    stratified_subset,
)
from minicode.ground.prompt import coarse_prompt  # noqa: E402
from minicode.model.client import ModelClient  # noqa: E402
from minicode.model.config import load_retry_settings, load_vision_config  # noqa: E402
from minicode.model.image import downscale_frame, model_input_size_for  # noqa: E402
from minicode.model.request_log import JsonlRequestLog  # noqa: E402
from minicode.model.retry import RetryingModelCaller, RetryLogWriter, RetryPolicy  # noqa: E402

sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def main() -> int:
    parser = argparse.ArgumentParser(description="W7 新模型像素臂（同批 w6 配对）")
    parser.add_argument(
        "--dataset",
        default=r"C:\Users\22197\Desktop\ik评测\sspro-dataset",
    )
    parser.add_argument("--out", default="runs/w7-vision-switch/paired-qwen38")
    parser.add_argument("--per-stratum", type=int, default=10)
    parser.add_argument("--seed", type=int, default=20251020)
    parser.add_argument("--effort", default="low")
    parser.add_argument("--max-tokens", type=int, default=8192)
    args = parser.parse_args()

    config = load_vision_config()
    print(f"=== 模型：{config.model}（端点：{config.endpoint}）===", flush=True)
    settings = load_retry_settings(vision=True)
    print(f"=== 重试：attempts={settings.max_attempts} base={settings.base_delay_s}s ===", flush=True)

    samples = load_samples(Path(args.dataset))
    subset = stratified_subset(samples, args.per_stratum, seed=args.seed)
    print(f"=== 样本：{len(subset)} 条（每层 {args.per_stratum}，seed={args.seed}，与 w6 同批）===", flush=True)

    out_dir = _ROOT / args.out
    out_dir.mkdir(parents=True, exist_ok=True)
    client = RetryingModelCaller(
        ModelClient(config, JsonlRequestLog(out_dir / "model-requests.jsonl"), channel="vision"),
        policy=RetryPolicy(
            max_attempts=settings.max_attempts,
            base_delay_s=settings.base_delay_s,
            max_delay_s=settings.max_delay_s,
            jitter_s=settings.jitter_s,
        ),
        attempt_sink=RetryLogWriter(out_dir / "model-retries.jsonl").write,
        serial=True,
    )

    rows: list[dict] = []
    started = time.time()
    for index, sample in enumerate(subset, start=1):
        frame = read_png_frame(sample.image_path)
        model_input = model_input_size_for(sample.image_size[0], sample.image_size[1])
        screen_frame = downscale_frame(frame, model_input[0], model_input[1])
        image_b64 = encode_frame_base64(screen_frame)
        geometry = _geometry(sample.image_size, model_input)
        prompt = coarse_prompt(sample.instruction, model_input)
        error = ""
        raw_center = None
        point = None
        calls = 0
        try:
            raw_center, calls = _ask_bBox_center(
                client, prompt, image_b64,
                effort=args.effort, max_tokens=args.max_tokens,
            )
            if raw_center is not None:
                # pixel 臂：模型坐标 = 声明输入域绝对像素 → 物理换算走既有几何。
                from minicode.coord.coordinates import CoordinateAdapter

                adapter = CoordinateAdapter(geometry, ModelScale.ABSOLUTE_MODEL_INPUT)
                surface_x, surface_y = adapter.to_physical(raw_center)
                point = (surface_x, surface_y)
        except Exception as exc:  # noqa: BLE001 —— 单条失败如实记录
            error = f"{type(exc).__name__}: {exc}"
        hit = False
        if point is not None:
            x0, y0, x1, y1 = sample.bbox
            hit = bool(x0 <= point[0] <= x1 and y0 <= point[1] <= y1)
        rows.append({
            "sample_id": sample.sample_id,
            "ui_type": sample.ui_type,
            "platform": sample.platform,
            "hit": hit,
            "point": [round(point[0], 1), round(point[1], 1)] if point else None,
            "raw_center": list(raw_center) if raw_center else None,
            "model_calls": calls,
            "error": error,
        })
        print(
            f"[{index}/{len(subset)}] {sample.sample_id} hit={hit} "
            f"({time.time() - started:.0f}s)",
            flush=True,
        )

    hits = sum(1 for row in rows if row["hit"])
    errors = sum(1 for row in rows if row["error"])
    report = {
        "experiment": "W7 主线 A：新视觉模型像素臂（与 docs/w6-paired.json 的 deepseek 同批 60 条配对）",
        "model": config.model,
        "endpoint": config.endpoint,
        "n_samples": len(subset),
        "per_stratum": args.per_stratum,
        "seed": args.seed,
        "effort": args.effort,
        "max_tokens": args.max_tokens,
        "judge": "官方口径：落点（表面像素）落在 GT bbox 内",
        "hits": hits,
        "errors": errors,
        "rows": rows,
    }
    (out_dir / "pixel-arm.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"=== 完成：{hits}/{len(subset)} hits, {errors} errors → {out_dir / 'pixel-arm.json'} ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

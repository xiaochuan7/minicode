"""V2 视觉配对实验：**换模型**（臂 A opencode deepseek-v4.1-flash vs 臂 B Radeon Qwen3.8-27B）。

与 W6（`scripts/w6_paired_coords.py`，像素 vs 归一化契约）同一套纪律，被测变量换成**模型**：

* **同批 60 条**：ScreenSpot-Pro 分层抽样（per_stratum=10，seed=20251020，与 W6 相同）
  —— 臂 A **直接复用** W6 的 pixel 臂结果（同模型同提示词同判定同批样本；
  运行前核对 W6 报告里的 model/endpoint 与样本 id 全一致，不一致就拒绝跑）；
* **两臂提示词逐字相同**：都是生产粗定位提示词（``ground/prompt.py::coarse_prompt``，
  像素契约）—— 不为任何一臂特调（配对实验的铁律）；
* **判定口径同一**：官方口径 —— 落点（表面像素）落在 GT bbox 内；
* **控速**（限流不对称：Qwen 侧 key 级 8 并发 + TPM，DeepSeek 侧 opencode）：
  臂 B 并发 ≤ ``--workers``（默认 4）、每批之间留 ``--batch-sleep`` 秒间隔；
  429 按 **60s 固定退避**重试（最多 5 次，``RetryingModelCaller`` + 注入的
  ``RetryPolicy``），所有 429 与传输失败**单独计数**进报告，不许混进失败率。

用法（仓库根目录）::

    python -I scripts/v2_paired_models.py --out runs/v2
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from dataclasses import fields as dataclass_fields
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "src"))

from minicode.action.image import encode_frame_base64  # noqa: E402
from minicode.coord.coordinates import (  # noqa: E402
    CoordinateAdapter,
    FrameGeometry,
    ModelScale,
)
from minicode.eval.png_read import read_png_frame  # noqa: E402
from minicode.eval.sspro import (  # noqa: E402
    ArmOutcome,
    load_samples,
    paired_comparison,
    stratified_subset,
)
from minicode.ground.prompt import coarse_prompt, parse_coarse_target  # noqa: E402
from minicode.model.client import ModelClient  # noqa: E402
from minicode.model.config import load_config, load_vision_config  # noqa: E402
from minicode.model.image import downscale_frame, model_input_size_for  # noqa: E402
from minicode.model.request_log import JsonlRequestLog  # noqa: E402
from minicode.model.retry import RetryingModelCaller, RetryPolicy  # noqa: E402

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

#: 429 的退避纪律（任务书）：**60s 固定**，最多 5 次（含首次）。
RATE_LIMIT_POLICY = RetryPolicy(
    max_attempts=5, base_delay_s=60.0, max_delay_s=60.0, jitter_s=0.0
)


def _geometry(sample_size: tuple[int, int], model_input: tuple[int, int]) -> FrameGeometry:
    """整屏几何：截图=原图（表面 1:1），模型输入=缩放后的那张（与 W6 相同）。"""
    return FrameGeometry(
        capture_width=sample_size[0],
        capture_height=sample_size[1],
        model_input_width=model_input[0],
        model_input_height=model_input[1],
        capture_to_physical_scale=1.0,
        virtual_origin_x=0,
        virtual_origin_y=0,
        surface_width=sample_size[0],
        surface_height=sample_size[1],
        surface_origin_x=0,
        surface_origin_y=0,
    )


class _ThreadLocalClients:
    """每线程一套客户端（ModelClient 非线程安全；落盘日志有锁）。"""

    def __init__(self, config, log_path: Path):
        self._config = config
        self._log_path = log_path
        self._local = threading.local()

    def client(self) -> RetryingModelCaller:
        holder = getattr(self._local, "holder", None)
        if holder is None:
            holder = RetryingModelCaller(
                ModelClient(
                    self._config,
                    JsonlRequestLog(self._log_path),
                    channel="vision",
                ),
                policy=RATE_LIMIT_POLICY,
            )
            self._local.holder = holder
        return holder


def _outcome(
    sample,
    *,
    arm: str,
    hit: bool,
    point: tuple[float, float] | None,
    raw_center: tuple[float, float] | None,
    model_calls: int,
    error: str,
) -> ArmOutcome:
    return ArmOutcome(
        sample_id=sample.sample_id,
        ui_type=sample.ui_type,
        platform=sample.platform,
        group=sample.group,
        application=sample.application,
        hit=hit,
        landing_point=(
            (int(round(point[0])), int(round(point[1]))) if point else None
        ),
        coarse_point=point,
        gate_hit=False,
        gate_reasons=(),
        gate_undecidable=(),
        refine_enabled=False,
        fallback=False,
        fallback_cause="",
        model_calls=model_calls,
        error=error,
        coarse_bbox_model=None,
        coarse_point_model=(
            [raw_center[0], raw_center[1]] if raw_center else None
        ),
        coarse_text="",
        dist_to_bbox=None,
        image_tokens=None,
        landing_source=arm,
    )


def _run_arm_b_sample(
    sample,
    clients: _ThreadLocalClients,
    *,
    effort: str,
    max_tokens: int,
) -> tuple[ArmOutcome, dict]:
    """臂 B 的一条样本：**与臂 A 逐字相同的提示词**、同一份缩放图像、同一判定。"""
    frame = read_png_frame(sample.image_path)
    model_input = model_input_size_for(sample.image_size[0], sample.image_size[1])
    screen_frame = downscale_frame(frame, model_input[0], model_input[1])
    image_b64 = encode_frame_base64(screen_frame)
    geometry = _geometry(sample.image_size, model_input)
    prompt = coarse_prompt(sample.instruction, model_input)

    client = clients.client()
    started = time.time()
    calls = 0
    error = ""
    raw_center: tuple[float, float] | None = None
    point: tuple[float, float] | None = None
    try:
        reply = client.complete(
            prompt, effort=effort, max_tokens=max_tokens, image_base64=image_b64
        )
        calls += 1
        answer = parse_coarse_target(reply.text)
        if not answer.negative and answer.bbox is not None:
            raw_center = (
                (answer.bbox[0] + answer.bbox[2]) / 2.0,
                (answer.bbox[1] + answer.bbox[3]) / 2.0,
            )
            adapter = CoordinateAdapter(model_scale=ModelScale.ABSOLUTE_MODEL_INPUT)
            point = adapter.to_screen(raw_center[0], raw_center[1], geometry)
    except Exception as exc:  # noqa: BLE001 —— 单条失败如实记录，不炸整轮
        error = f"{type(exc).__name__}: {exc}"
    hit = False
    if point is not None:
        x1, y1, x2, y2 = sample.bbox
        hit = x1 <= point[0] <= x2 and y1 <= point[1] <= y2
    outcome = _outcome(
        sample, arm="qwen_vision", hit=hit, point=point, raw_center=raw_center,
        model_calls=calls, error=error,
    )
    raw = {
        "raw_center": list(raw_center) if raw_center else None,
        "surface_point": list(point) if point else None,
        "error": error,
        "latency_s": round(time.time() - started, 1),
    }
    return outcome, raw


def _chunks(seq: list, size: int):
    for start in range(0, len(seq), size):
        yield seq[start : start + size]


def _count_rate_limits(log_path: Path) -> dict:
    """从请求日志统计 429 / 传输失败 / 无文本（**单独计数，不混进失败率**）。"""
    rate_limited = 0
    transport_errors = 0
    no_text = 0
    total = 0
    latencies: list[float] = []
    if not log_path.exists():
        return {
            "requests": 0, "rate_limited_429": 0,
            "transport_errors": 0, "no_text_output": 0,
            "latency_ms_p50": None, "latency_ms_p95": None,
        }
    for line in log_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        total += 1
        if record.get("rate_limited"):
            rate_limited += 1
        if record.get("http_status") is None:
            transport_errors += 1
        if record.get("text_len") == 0:
            no_text += 1
        if record.get("latency_ms") is not None:
            latencies.append(float(record["latency_ms"]))
    latencies.sort()

    def _pct(values: list[float], fraction: float):
        if not values:
            return None
        index = min(len(values) - 1, int(round(fraction * (len(values) - 1))))
        return round(values[index], 1)

    return {
        "requests": total,
        "rate_limited_429": rate_limited,
        "transport_errors": transport_errors,
        "no_text_output": no_text,
        "latency_ms_p50": _pct(latencies, 0.5),
        "latency_ms_p95": _pct(latencies, 0.95),
    }


def _arm_outcome_from_dict(record: dict) -> ArmOutcome:
    """W6 报告里的臂 A 结果 → :class:`ArmOutcome`（字段一一对应，list/tuple 差异无碍）。"""
    names = {field.name for field in dataclass_fields(ArmOutcome)}
    return ArmOutcome(**{key: value for key, value in record.items() if key in names})


def main() -> int:
    parser = argparse.ArgumentParser(description="V2 视觉配对：换模型（deepseek vs Qwen）")
    parser.add_argument(
        "--dataset",
        default=r"C:\Users\22197\Desktop\ik评测\sspro-dataset",
        help="ScreenSpot-Pro 数据根（仓库外）",
    )
    parser.add_argument("--out", default="runs/v2")
    #: ⚠️ 这三个数与 W6 完全一致 —— 样本批必须能和 W6 的臂 A 对上。
    parser.add_argument("--per-stratum", type=int, default=10)
    parser.add_argument("--seed", type=int, default=20251020)
    parser.add_argument("--w6-json", default="runs/w6/w6-paired.json")
    parser.add_argument("--workers", type=int, default=4, help="臂 B 并发上限（≤4）")
    parser.add_argument("--batch-sleep", type=float, default=8.0, help="每批之间的间隔秒数")
    parser.add_argument("--effort", default="low")
    parser.add_argument("--max-tokens", type=int, default=8192)
    parser.add_argument("--limit", type=int, default=0, help="只跑前 N 条（0 = 全部 60；断点续跑用）")
    args = parser.parse_args()
    if args.workers > 4:
        raise SystemExit("并发上限是 4（任务书的限流纪律），--workers 给小一点")

    decision = load_config()
    vision = load_vision_config()
    print(f"=== 臂 A（对照，复用 W6）：{decision.model} @ {decision.endpoint} ===", flush=True)
    print(f"=== 臂 B（实验）：{vision.model} @ {vision.endpoint} ===", flush=True)

    # -- 臂 A：复用 W6 的 pixel 臂（同模型/同提示词/同判定），先核对一致性 ----
    w6_path = _ROOT / args.w6_json
    w6 = json.loads(w6_path.read_text(encoding="utf-8"))
    if w6.get("model") != decision.model or w6.get("endpoint") != decision.endpoint:
        raise SystemExit(
            f"W6 报告的模型/端点（{w6.get('model')} @ {w6.get('endpoint')}）"
            f"与当前决策配置（{decision.model} @ {decision.endpoint}）不一致 —— "
            "臂 A 不能复用，先核对 .env"
        )
    arm_a_records = w6["outcomes"]["pixel"]
    arm_a_by_id = {record["sample_id"]: record for record in arm_a_records}

    # -- 样本批：与 W6 相同的分层抽样（同 seed / per-stratum）----------------
    samples = load_samples(Path(args.dataset))
    subset = stratified_subset(samples, args.per_stratum, seed=args.seed)
    subset_ids = [sample.sample_id for sample in subset]
    if set(subset_ids) != set(arm_a_by_id):
        raise SystemExit(
            "重建的样本批与 W6 的 60 条对不上 —— 数据集变了或抽样参数变了，拒绝盲跑"
        )
    if args.limit:
        subset = subset[: args.limit]
    print(f"=== 样本：{len(subset)} 条（每层 {args.per_stratum}，seed={args.seed}，与 W6 同批）===", flush=True)

    out_dir = _ROOT / args.out
    out_dir.mkdir(parents=True, exist_ok=True)
    log_path = out_dir / "model-requests.jsonl"
    if log_path.exists():
        # 断点续跑的口径：日志按追加写 —— 重新整轮跑时先挪开旧日志。
        log_path.rename(out_dir / f"model-requests.prev-{int(time.time())}.jsonl")
    clients = _ThreadLocalClients(vision, log_path)

    # -- 臂 B：分批控速（并发 ≤ workers，批间 batch-sleep 秒）----------------
    arm_b_outcomes: list[ArmOutcome] = []
    arm_b_raw: list[dict] = []
    started = time.time()

    def work(sample):
        return _run_arm_b_sample(
            sample, clients, effort=args.effort, max_tokens=args.max_tokens
        )

    done = 0
    for chunk in _chunks(subset, args.workers):
        with ThreadPoolExecutor(max_workers=len(chunk)) as pool:
            for outcome, raw in pool.map(work, chunk):
                arm_b_outcomes.append(outcome)
                arm_b_raw.append({"sample_id": outcome.sample_id, **raw})
                done += 1
                print(
                    f"[{done}/{len(subset)}] {outcome.sample_id} "
                    f"hit={outcome.hit} ({time.time() - started:.0f}s)",
                    flush=True,
                )
        time.sleep(args.batch_sleep)

    # -- 429 / 传输失败 / 无文本：单独计数（不混进失败率）--------------------
    rate_report = _count_rate_limits(log_path)

    # -- 配对统计（与 W6 同一份实现，全部字段一次给齐）----------------------
    arm_a_outcomes = [
        _arm_outcome_from_dict(arm_a_by_id[outcome.sample_id])
        for outcome in arm_b_outcomes
    ]
    stats = paired_comparison(
        arm_a_outcomes,
        arm_b_outcomes,
        label_left="deepseek-v4.1-flash(armA,复用W6)",
        label_right=f"{vision.model}(armB)",
    )

    report = {
        "experiment": "V2 视觉配对：决策层 LLM（opencode deepseek-v4.1-flash）vs 视觉 VLM（Radeon Qwen3.8-27B）",
        "arm_a": {
            "model": decision.model,
            "endpoint_host": urllib.parse.urlsplit(decision.endpoint).hostname,
            "source": f"复用 W6 pixel 臂（{w6_path.name}，同模型同提示词同判定同批样本）",
        },
        "arm_b": {
            "model": vision.model,
            "endpoint_host": urllib.parse.urlsplit(vision.endpoint).hostname,
        },
        "prompt_contract": (
            "两臂逐字相同：生产粗定位提示词（ground/prompt.py::coarse_prompt，像素契约）；"
            "不为任何一臂特调"
        ),
        "judge": "官方口径：落点（表面像素）落在 GT bbox 内",
        "n_samples": len(subset),
        "per_stratum": args.per_stratum,
        "seed": args.seed,
        "effort": args.effort,
        "max_tokens": args.max_tokens,
        "rate_control": {
            "workers": args.workers,
            "batch_sleep_s": args.batch_sleep,
            "retry_policy": "429 → 60s 固定退避，最多 5 次（含首次）",
            "arm_b_requests": rate_report,
            "note": "429 是配额问题不是模型失败，单独计数；臂 A 沿用 W6 的请求日志",
        },
        "paired": stats,
        "outcomes": {
            "arm_a": [outcome.to_dict() for outcome in arm_a_outcomes],
            "arm_b": [outcome.to_dict() for outcome in arm_b_outcomes],
        },
        "raw_arm_b": arm_b_raw,
    }
    (out_dir / "v2-paired.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (out_dir / "v2-raw.json").write_text(
        json.dumps(arm_b_raw, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    hits_a = sum(1 for outcome in arm_a_outcomes if outcome.hit)
    hits_b = sum(1 for outcome in arm_b_outcomes if outcome.hit)
    print(
        f"=== 结果：臂A {hits_a}/{len(arm_a_outcomes)} vs 臂B {hits_b}/{len(arm_b_outcomes)} ==="
    )
    print(json.dumps(stats, ensure_ascii=False, indent=2)[:2000])
    print(f"=== 完成：{out_dir / 'v2-paired.json'} ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

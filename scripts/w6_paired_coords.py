"""W6 视觉配对实验：臂 A 像素契约 vs 臂 B 0–1000 归一化契约（ScreenSpot-Pro）。

**同批样本逐条配对**：同一张图（同一份缩放后的模型输入）、同一模型、同一档位
与输出预算，**唯一差异是坐标契约**——

* 臂 A（``pixel``，现状）：生产粗定位提示词（``ground/prompt.py::coarse_prompt``
  逐字——它声明「坐标是这张图的像素」），回答按**模型输入像素**解释
  （``ModelScale.ABSOLUTE_MODEL_INPUT``）。
* 臂 B（``norm_0_1000``）：与臂 A **逐字相同**的提示词，仅坐标声明一行换成
  「坐标是 0~1000 的归一化坐标」；回答按 ``ModelScale.RELATIVE_0_1000`` 解释。

判定：官方口径（落点落在 GT bbox 内，Surface 空间；bbox 随图等比缩放，
与官方「归一化点落在归一化框内」等价）。统计：``eval/sspro.py::paired_comparison``
（mid-p McNemar 主报 + Wilson CI + 配对 bootstrap 的 Δ 95% CI + MDE）。

⚠️ 诚实口径：
* **单级对比**（只比整屏粗定位那一级）——两级路径里 crop 精定位的坐标语言
  是另一个实验，不在本报告混入。
* 两臂各按其**声明的契约**解释回答；本实验不做逐尺寸标定探针
  （生产行为的标定是另一条线，此前已有记录）。
* 模型身份见 ``--model``（来自仓库 .env 的 ``MINICODE_MODEL``），**如实披露**。

用法（仓库根目录）::

    python -I scripts/w6_paired_coords.py --out runs/w6
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
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
from minicode.model.config import load_config  # noqa: E402
from minicode.model.image import downscale_frame, model_input_size_for  # noqa: E402
from minicode.model.request_log import JsonlRequestLog  # noqa: E402
from minicode.model.retry import RetryingModelCaller  # noqa: E402

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

#: 臂 B 的坐标声明（替换臂 A 的「（坐标是这张图的像素）」那一行——
#: **其余逐字相同**，配对的唯一差异才是坐标契约）。
_NORM_COORD_LINE = (
    "（坐标是 0~1000 的归一化坐标：图的左上角是 (0,0)，右下角是 (1000,1000)，"
    "与图片的像素尺寸无关）"
)
_PIXEL_COORD_LINE = "（坐标是这张图的像素）"


def _norm_prompt(target: str, model_input: tuple[int, int]) -> str:
    """臂 B 的提示词 = 臂 A 逐字换坐标声明行。"""
    pixel_prompt = coarse_prompt(target, model_input)
    if _PIXEL_COORD_LINE not in pixel_prompt:
        raise RuntimeError("生产提示词的坐标声明行变了——配对前提被破坏，拒绝盲跑")
    return pixel_prompt.replace(_PIXEL_COORD_LINE, _NORM_COORD_LINE)


def _geometry(sample_size: tuple[int, int], model_input: tuple[int, int]) -> FrameGeometry:
    """整屏几何：截图=原图（表面 1:1），模型输入=缩放后的那张。"""
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


class _ThreadLocalClient:
    """每线程一套客户端（ModelClient 非线程安全；共享落盘日志有锁）。"""

    def __init__(self, log_path: Path):
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


def _ask_bBox_center(
    client: RetryingModelCaller,
    prompt: str,
    image_b64: str,
    *,
    effort: str,
    max_tokens: int,
) -> tuple[tuple[float, float] | None, int]:
    """问一次，返回（模型空间中心点 | None, 该样本此臂调用数）。"""
    reply = client.complete(
        prompt,
        effort=effort,
        max_tokens=max_tokens,
        image_base64=image_b64,
    )
    answer = parse_coarse_target(reply.text)
    if answer.negative or answer.bbox is None:
        return None, 1
    center = (
        (answer.bbox[0] + answer.bbox[2]) / 2.0,
        (answer.bbox[1] + answer.bbox[3]) / 2.0,
    )
    return center, 1


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


def _run_sample(
    sample,
    clients: _ThreadLocalClient,
    *,
    effort: str,
    max_tokens: int,
) -> tuple[ArmOutcome, ArmOutcome, dict]:
    """一条样本的两臂（同一份缩放图像；判定 = 官方 bbox 包含口径）。"""
    frame = read_png_frame(sample.image_path)
    model_input = model_input_size_for(sample.image_size[0], sample.image_size[1])
    screen_frame = downscale_frame(frame, model_input[0], model_input[1])
    image_b64 = encode_frame_base64(screen_frame)
    geometry = _geometry(sample.image_size, model_input)

    results: dict[str, ArmOutcome] = {}
    raw_rows: dict[str, dict] = {}
    for arm, prompt, scale in (
        ("pixel", coarse_prompt(sample.instruction, model_input), ModelScale.ABSOLUTE_MODEL_INPUT),
        ("norm_0_1000", _norm_prompt(sample.instruction, model_input), ModelScale.RELATIVE_0_1000),
    ):
        client = clients.client()
        calls = 0
        error = ""
        raw_center: tuple[float, float] | None = None
        point: tuple[float, float] | None = None
        try:
            raw_center, used = _ask_bBox_center(
                client, prompt, image_b64, effort=effort, max_tokens=max_tokens
            )
            calls += used
            if raw_center is not None:
                adapter = CoordinateAdapter(model_scale=scale)
                px, py = adapter.to_screen(raw_center[0], raw_center[1], geometry)
                point = (px, py)
        except Exception as exc:  # noqa: BLE001 —— 单条失败如实记录，不炸整轮
            error = f"{type(exc).__name__}: {exc}"
        hit = False
        if point is not None:
            x1, y1, x2, y2 = sample.bbox
            hit = x1 <= point[0] <= x2 and y1 <= point[1] <= y2
        results[arm] = _outcome(
            sample, arm=arm, hit=hit, point=point, raw_center=raw_center,
            model_calls=calls, error=error,
        )
        raw_rows[arm] = {
            "raw_center": list(raw_center) if raw_center else None,
            "surface_point": list(point) if point else None,
            "error": error,
        }
    return results["pixel"], results["norm_0_1000"], raw_rows


def main() -> int:
    parser = argparse.ArgumentParser(description="W6 视觉配对实验（像素 vs 0-1000 归一化）")
    parser.add_argument(
        "--dataset",
        default=r"C:\Users\22197\Desktop\ik评测\sspro-dataset",
        help="ScreenSpot-Pro 数据根（仓库外）",
    )
    parser.add_argument("--out", default="runs/w6")
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

    from minicode.model.config import load_config as _lc

    config = _lc()
    print(f"=== 模型：{config.model}（端点：{config.endpoint}）===", flush=True)

    samples = load_samples(Path(args.dataset))
    subset = stratified_subset(samples, args.per_stratum, seed=args.seed)
    print(f"=== 样本：{len(subset)} 条（每层 {args.per_stratum}，seed={args.seed}）===", flush=True)

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    clients = _ThreadLocalClient(out_dir / "model-requests.jsonl")

    pixel_outcomes: list[ArmOutcome] = []
    norm_outcomes: list[ArmOutcome] = []
    raw_rows: list[dict] = []
    started = time.time()

    def work(sample):
        return _run_sample(sample, clients, effort=args.effort, max_tokens=args.max_tokens)

    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        for index, (pixel, norm, raw) in enumerate(
            pool.map(work, subset), start=1
        ):
            pixel_outcomes.append(pixel)
            norm_outcomes.append(norm)
            raw_rows.append(
                {
                    "sample_id": pixel.sample_id,
                    "platform": pixel.platform,
                    "ui_type": pixel.ui_type,
                    "pixel": raw["pixel"],
                    "norm_0_1000": raw["norm_0_1000"],
                }
            )
            print(
                f"[{index}/{len(subset)}] {pixel.sample_id} "
                f"pixel={pixel.hit} norm={norm.hit} "
                f"({time.time() - started:.0f}s)",
                flush=True,
            )

    stats = paired_comparison(
        pixel_outcomes,
        norm_outcomes,
        label_left="pixel",
        label_right="norm_0_1000",
    )
    report = {
        "experiment": "W6 视觉配对：像素契约 vs 0-1000 归一化契约（单级粗定位）",
        "model": config.model,
        "endpoint": config.endpoint,
        "dataset": "ScreenSpot-Pro（likaixin/ScreenSpot-Pro，经 hf-mirror 下载，存于仓库外）",
        "n_samples": len(subset),
        "per_stratum": args.per_stratum,
        "seed": args.seed,
        "effort": args.effort,
        "max_tokens": args.max_tokens,
        "judge": "官方口径：落点（表面像素）落在 GT bbox 内",
        "paired": stats,
        "outcomes": {
            "pixel": [o.to_dict() for o in pixel_outcomes],
            "norm_0_1000": [o.to_dict() for o in norm_outcomes],
        },
    }
    (out_dir / "w6-paired.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (out_dir / "w6-raw.json").write_text(
        json.dumps(raw_rows, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(stats, ensure_ascii=False, indent=2)[:2000])
    print(f"=== 完成：{out_dir / 'w6-paired.json'} ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

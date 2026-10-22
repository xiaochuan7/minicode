"""**负样本子集**（「目标不存在」）的逃逸口（负出口）评测。

**为什么需要它**：ScreenSpot-Pro 每一条都**保证目标存在** ⇒ 离线口径里
「假命中」「拒答 precision」**压根看不见** ⇒ 「假命中归零」在离线口径里是
**自证的**【一手·公开口径，``docs/research/r-s2-false-hit.md`` §5】。

**做法（施工清单 §六 6.2 最后一条）**：**同图取错位 crop** 当负样本 ——
在**同一张图**上取一块**不含目标**的窗口（与正样本**同样大小**），
问模型找那个目标 → **正确行为是负出口**（答「这张图里没有对应的元素」）。

**口径（与本仓既有口径逐字对齐）**

* **负出口** = 模型答 ``{"x": null, "y": null}``
  （:func:`minicode.ground.prompt.parse_optional_point` 返回 ``None``）；
  **截断/读不出来**是**另一回事**（:class:`GroundingParseError`），
  **不算拒答**，单独记（不许混进分子）；**没有文本输出**也算单独一档。
* **正确拒答** = 负样本上模型走了负出口；
* **误拒** = **正样本**上模型走了负出口（**含住了却拒**）——
  **只从已有跑批读**（``fallback_cause == "no_output"`` 且落在 ``crop_contains``），
  **不另跑正样本**；
* **recall** = 正确拒答 ÷ 负样本条数；
* **precision** = 正确拒答 ÷（正确拒答 + 误拒）。

⚠️ **命名纪律**（``src/minicode/eval/test_eval_formats.py``
``NamingDisciplineTests``）：本模块**不 import** :mod:`minicode.coord`
（评测包只做「评测脚本侧的坐标格式」，不碰模型量纲）。
⇒ 窗口几何**自己算**：crop 是从**原始物理分辨率帧** 1:1 裁的 →
**窗口坐标就是原图像素**；比例是**一条乘法**（模型输入宽 ÷ 原图宽）。
（说明：:mod:`minicode.eval.sspro` —— 评测包里**既有**的模块 —— 本来就
间接拉进了 :mod:`minicode.coord`；本模块**不新增**这条依赖。）

用法::

    $env:PYTHONPATH="src"
    python -m minicode.eval.refusal_eval --limit 30 --workers 2 --out runs/s2-refusal-eval
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from minicode.action.image import encode_frame_base64
from minicode.action.ports import CapturedFrame, PixelFormat
from minicode.eval.formats import SCREENSPOT_PRO
from minicode.eval.png_read import read_png_frame
from minicode.eval.sspro import load_samples
from minicode.ground.prompt import (
    GroundingParseError,
    parse_optional_point,
    refine_prompt,
)
from minicode.model.client import ModelClient
from minicode.model.config import load_config
from minicode.model.effort import Effort
from minicode.model.errors import ModelError
from minicode.model.image import SERVER_MAX_INPUT_WIDTH_PX
from minicode.model.request_log import JsonlRequestLog

__all__ = [
    "NegativeSample",
    "build_negative_sample",
    "escape_hatch_metrics",
    "main",
]

#: **生产那条**裁剪窗口的默认参数（当前物理帧的 50% 线性尺寸 + 绝对下限 512×512）。
#:
#: ⚠️ 这里**不 import** :mod:`minicode.ground.channel`（评测包不碰模型量纲那条链）
#: → 写成**参数**（命令行可改），默认值与生产一致，**出处写在这里**：
#: ``src/minicode/ground/channel.py`` 的 ``CROP_LINEAR_FRACTION = 0.5`` /
#: ``MIN_CROP_WIDTH_PX = 512`` / ``MIN_CROP_HEIGHT_PX = 512``。
#: （生产几何一改，这里**要跟着改** —— 产物里记着本次用的三个数。）
DEFAULT_CROP_FRACTION = 0.5
DEFAULT_CROP_MIN_WIDTH_PX = 512
DEFAULT_CROP_MIN_HEIGHT_PX = 512


@dataclass(frozen=True)
class NegativeSample:
    """一条**负样本**（同一张图 + 一块不含目标的错位窗口）。"""

    sample_id: str
    instruction: str
    ui_type: str
    #: GT bbox（原图像素）。
    bbox: tuple[int, int, int, int]
    image_size: tuple[int, int]
    #: 粗定位点（原图像素）。
    coarse_point: tuple[float, float]
    #: **正样本**窗口（生产几何，粗点居中）—— 只作对照记录。
    positive_rect: tuple[int, int, int, int]
    #: **负样本**窗口（同样大小，**不含 GT 框**）。
    negative_rect: tuple[int, int, int, int]
    #: 送模型的那张图（= 负样本窗口）的尺寸。
    crop_size: tuple[int, int]
    #: 这条样本在**同图**里挑错位窗口时用了第几个候选位置。
    negative_slot: int


def _crop_size(
    image_size: tuple[int, int],
    *,
    fraction: float = DEFAULT_CROP_FRACTION,
    min_width: int = DEFAULT_CROP_MIN_WIDTH_PX,
    min_height: int = DEFAULT_CROP_MIN_HEIGHT_PX,
) -> tuple[int, int]:
    """**生产那条**裁剪窗口尺寸（原图像素）—— 自己算，不引坐标类。"""
    width = min(image_size[0], max(min_width, int(image_size[0] * fraction)))
    height = min(image_size[1], max(min_height, int(image_size[1] * fraction)))
    return (int(width), int(height))


def _window_for(
    center: tuple[float, float],
    size: tuple[int, int],
    surface: tuple[int, int],
) -> tuple[int, int, int, int]:
    """以 ``center`` 为中心的窗口（**贴边向内平移，不缩窗口**）。

    ⚠️ 与生产那条裁剪**同语义**（冻结原型的 ``plan_crop``，``target_radius_px=0``）：
    越界时整体平移、**尺寸不变**。
    """
    width, height = size
    left = int(round(center[0] - width / 2))
    top = int(round(center[1] - height / 2))
    left = max(0, min(left, surface[0] - width))
    top = max(0, min(top, surface[1] - height))
    return (left, top, left + width, top + height)


def _intersects(
    left: int, top: int, right: int, bottom: int, bbox: tuple[int, int, int, int]
) -> bool:
    """两个矩形**有交集**吗。"""
    x1, y1, x2, y2 = bbox
    return not (right <= x1 or left >= x2 or bottom <= y1 or top >= y2)


def _negative_positions(
    image_size: tuple[int, int], size: tuple[int, int]
) -> list[tuple[int, int]]:
    """错位窗口的**候选位置**（确定性顺序：先四个角，再中线，最后中点）。"""
    width, height = image_size
    win_w, win_h = size
    return [
        (0, 0),
        (width - win_w, 0),
        (0, height - win_h),
        (width - win_w, height - win_h),
        ((width - win_w) // 2, 0),
        (0, (height - win_h) // 2),
        (width - win_w, (height - win_h) // 2),
        ((width - win_w) // 2, height - win_h),
        ((width - win_w) // 2, (height - win_h) // 2),
    ]


def build_negative_sample(
    *,
    sample_id: str,
    instruction: str,
    ui_type: str,
    bbox: tuple[int, int, int, int],
    image_size: tuple[int, int],
    coarse_point: tuple[float, float],
    fraction: float = DEFAULT_CROP_FRACTION,
    min_width: int = DEFAULT_CROP_MIN_WIDTH_PX,
    min_height: int = DEFAULT_CROP_MIN_HEIGHT_PX,
) -> NegativeSample | None:
    """造一条负样本；**造不出来**（找不到不含目标的错位窗口）→ ``None``。"""
    size = _crop_size(
        image_size, fraction=fraction, min_width=min_width, min_height=min_height
    )
    positive = _window_for(coarse_point, size, image_size)
    for slot, (left, top) in enumerate(_negative_positions(image_size, size)):
        right = left + size[0]
        bottom = top + size[1]
        if right > image_size[0] or bottom > image_size[1]:
            continue
        if _intersects(left, top, right, bottom, bbox):
            continue
        return NegativeSample(
            sample_id=sample_id,
            instruction=instruction,
            ui_type=ui_type,
            bbox=bbox,
            image_size=image_size,
            coarse_point=coarse_point,
            positive_rect=positive,
            negative_rect=(left, top, right, bottom),
            crop_size=size,
            negative_slot=slot,
        )
    return None


def _crop_frame(frame: CapturedFrame, rect: tuple[int, int, int, int]) -> CapturedFrame:
    """从一帧上裁一块（**1:1，不缩放**）—— 评测侧自己切。

    ⚠️ 不引坐标类的 ``CropPlan``（命名纪律）：只需要一个矩形。
    """
    left, top, right, bottom = rect
    channels = 4 if frame.pixel_format == PixelFormat.BGRA else 3
    stride = frame.width * channels
    if len(frame.payload) != stride * frame.height:
        raise ValueError(
            f"画面像素数与尺寸对不上：{frame.width}x{frame.height}x{channels}"
        )
    rows: list[bytes] = []
    row_bytes = (right - left) * channels
    for y in range(top, bottom):
        offset = y * stride + left * channels
        rows.append(frame.payload[offset : offset + row_bytes])
    return CapturedFrame(
        width=right - left,
        height=bottom - top,
        left=left,
        top=top,
        payload=b"".join(rows),
        backend=frame.backend,
        source=f"{frame.source}|negative-crop",
        pixel_format=frame.pixel_format,
    )


def _load_coarse_points(report_path: Path, arm: str = "always") -> dict[str, tuple[float, float]]:
    """报告里那一臂的**粗定位点**（原图像素）。"""
    payload = json.loads(Path(report_path).read_text(encoding="utf-8"))
    points: dict[str, tuple[float, float]] = {}
    for outcome in payload.get("outcomes", {}).get(arm, []):
        coarse = outcome.get("coarse_point")
        if coarse:
            points[str(outcome["sample_id"])] = (float(coarse[0]), float(coarse[1]))
    return points


def _report_sample_ids(report_path: Path, arm: str = "always") -> list[str]:
    """报告里那一臂的**样本顺序**（= 评测子集的顺序）。"""
    payload = json.loads(Path(report_path).read_text(encoding="utf-8"))
    return [
        str(outcome["sample_id"])
        for outcome in payload.get("outcomes", {}).get(arm, [])
    ]


def _false_refusals(report_path: Path, arm: str = "always") -> dict[str, Any]:
    """**误拒数**（正样本上模型走了负出口）—— **只从已有跑批读**，不另跑正样本。

    口径与 ``docs/research/r-s2-false-hit.md:33-34`` 逐字一致：
    ``fallback_cause == "no_output"`` **且** 落在 ``crop_contains``（含住了却拒）。
    """
    payload = json.loads(Path(report_path).read_text(encoding="utf-8"))
    rows = payload.get("outcomes", {}).get(arm, [])
    opened = [r for r in rows if r.get("crop_contains_target") is not None]
    miss = [r for r in opened if not r.get("crop_contains_target")]
    hit = [r for r in opened if r.get("crop_contains_target")]
    correct = [r for r in miss if (r.get("fallback_cause") or "") == "no_output"]
    wrong = [r for r in hit if (r.get("fallback_cause") or "") == "no_output"]
    return {
        "source_report": str(report_path),
        "arm": arm,
        "samples": len(rows),
        "crops_opened": len(opened),
        "crop_miss": len(miss),
        "correct_refusals": len(correct),
        "false_refusals": len(wrong),
        "recall": round(len(correct) / len(miss), 4) if miss else None,
        "precision": (
            round(len(correct) / (len(correct) + len(wrong)), 4)
            if (len(correct) + len(wrong))
            else None
        ),
    }


def escape_hatch_metrics(
    *, negative_total: int, correct_refusals: int, false_refusals: int
) -> dict[str, Any]:
    """逃逸口的 precision / recall（**分子分母都写出来**）。"""
    recall = correct_refusals / negative_total if negative_total else None
    precision = (
        correct_refusals / (correct_refusals + false_refusals)
        if (correct_refusals + false_refusals)
        else None
    )
    return {
        "negative_samples": negative_total,
        "correct_refusals": correct_refusals,
        "false_refusals": false_refusals,
        "recall": round(recall, 4) if recall is not None else None,
        "recall_numerator_denominator": f"{correct_refusals}/{negative_total}",
        "precision": round(precision, 4) if precision is not None else None,
        "precision_numerator_denominator": (
            f"{correct_refusals}/{correct_refusals + false_refusals}"
            if (correct_refusals + false_refusals)
            else ""
        ),
    }


def _ask(
    client: ModelClient,
    sample: NegativeSample,
    image_path: Path,
    *,
    effort: Effort,
    max_tokens: int,
) -> dict[str, Any]:
    """问一条负样本：送**错位 crop** + 生产的精定位提示词 → 判模型怎么答。"""
    frame = read_png_frame(image_path)
    crop = _crop_frame(frame, sample.negative_rect)
    prompt = refine_prompt(sample.instruction, (crop.width, crop.height))
    try:
        reply = client.complete(
            prompt,
            effort=effort,
            max_tokens=max_tokens,
            image_base64=encode_frame_base64(crop),
        )
    except ModelError as exc:
        # **没有文本输出**（思考吃光预算等）—— **既不是拒答，也不是假命中**。
        return {
            "sample_id": sample.sample_id,
            "verdict": "no_text_output",
            "note": f"{type(exc).__name__}: {exc}"[:200],
            "reply": None,
        }
    text = reply.text or ""
    try:
        point = parse_optional_point(text)
    except GroundingParseError as exc:
        return {
            "sample_id": sample.sample_id,
            "verdict": "unparseable",
            "note": str(exc)[:200],
            "reply": text,
        }
    if point is None:
        return {
            "sample_id": sample.sample_id,
            "verdict": "negative_exit",
            "point": None,
            "reply": text,
        }
    # **自检**：模型给的点映射回原图后**不可能**命中 GT（目标压根不在窗内）——
    # 命中就说明「负样本」造错了（窗口里其实有目标）。
    left, top, _right, _bottom = sample.negative_rect
    mapped = (point[0] + left, point[1] + top)
    hits_gt = SCREENSPOT_PRO.hit(
        mapped,  # type: ignore[arg-type]
        type("_S", (), {"bbox": sample.bbox, "image_size": sample.image_size})(),
    )
    return {
        "sample_id": sample.sample_id,
        "verdict": "false_hit",
        "point": [point[0], point[1]],
        "mapped_to_image": [mapped[0], mapped[1]],
        "hits_gt_bbox": bool(hits_gt),
        "reply": text,
    }


def _reconfigure_output() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):  # pragma: no cover
            pass


def main(argv: Sequence[str] | None = None) -> int:
    _reconfigure_output()
    parser = argparse.ArgumentParser(
        prog="minicode.eval.refusal_eval",
        description="负样本子集（同图错位 crop）的逃逸口（负出口）评测",
    )
    parser.add_argument("--dataset", default=".scratch/sspro", help="数据集根目录")
    parser.add_argument(
        "--report",
        default="runs/g3-eval-2016/sspro-report.json",
        help="读粗定位点的那份报告（正样本侧）",
    )
    parser.add_argument("--arm", default="always", help="读哪一臂")
    parser.add_argument("--limit", type=int, default=30, help="跑多少条负样本")
    parser.add_argument("--workers", type=int, default=2, help="并发数（默认 2）")
    parser.add_argument("--out", default="runs/s2-refusal-eval", help="产物目录")
    parser.add_argument(
        "--baseline-report",
        action="append",
        default=None,
        help="算**误拒数**用的已有跑批（可给多次）",
    )
    parser.add_argument(
        "--crop-fraction",
        type=float,
        default=DEFAULT_CROP_FRACTION,
        help="crop 窗口的线性比例（默认 = 生产的 0.5）",
    )
    parser.add_argument(
        "--crop-min-width",
        type=int,
        default=DEFAULT_CROP_MIN_WIDTH_PX,
        help="crop 窗口的绝对下限宽（默认 = 生产的 512）",
    )
    parser.add_argument(
        "--crop-min-height",
        type=int,
        default=DEFAULT_CROP_MIN_HEIGHT_PX,
        help="crop 窗口的绝对下限高（默认 = 生产的 512）",
    )
    parser.add_argument("--effort", default=Effort.LOW.value, help="思考预算档位")
    parser.add_argument("--max-tokens", type=int, default=2000)
    args = parser.parse_args(argv)

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    dataset_root = Path(args.dataset)
    samples = load_samples(dataset_root)
    by_id = {sample.sample_id: sample for sample in samples}
    coarse = _load_coarse_points(Path(args.report), args.arm)
    #: **只跑报告里那 183 条子集**（按报告里的顺序）—— 不在整个数据集上挑，
    #: 否则挑出来的是另一批样本（口径就变了）。
    subset_ids = _report_sample_ids(Path(args.report), args.arm)

    # -- 造负样本 -------------------------------------------------------
    negatives: list[NegativeSample] = []
    skipped: list[dict[str, Any]] = []
    for sample_id in subset_ids:
        if len(negatives) >= args.limit:
            break
        sample = by_id.get(sample_id)
        if sample is None:
            skipped.append({"sample_id": sample_id, "reason": "数据集里没有这条"})
            continue
        point = coarse.get(sample.sample_id)
        if point is None:
            skipped.append({"sample_id": sample.sample_id, "reason": "没有粗定位点"})
            continue
        size = _crop_size(
            sample.image_size,
            fraction=args.crop_fraction,
            min_width=args.crop_min_width,
            min_height=args.crop_min_height,
        )
        if size[0] > SERVER_MAX_INPUT_WIDTH_PX:
            skipped.append(
                {
                    "sample_id": sample.sample_id,
                    "reason": (
                        f"crop 宽 {size[0]} 超过服务端上限 "
                        f"{SERVER_MAX_INPUT_WIDTH_PX}（那张图太大）"
                    ),
                }
            )
            continue
        negative = build_negative_sample(
            sample_id=sample.sample_id,
            instruction=sample.instruction,
            ui_type=sample.ui_type,
            bbox=sample.bbox,
            image_size=sample.image_size,
            coarse_point=point,
            fraction=args.crop_fraction,
            min_width=args.crop_min_width,
            min_height=args.crop_min_height,
        )
        if negative is None:
            skipped.append({"sample_id": sample.sample_id, "reason": "这块图上找不到错位窗口"})
            continue
        negatives.append(negative)

    print(f"=== 负样本 {len(negatives)} 条（跳过 {len(skipped)} 条）===")

    # -- 问模型 ---------------------------------------------------------
    config = load_config()
    client = ModelClient(config, JsonlRequestLog(out_dir / "model-requests.jsonl"))
    effort = Effort(args.effort)
    started = time.perf_counter()
    results: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        futures = [
            pool.submit(
                _ask,
                client,
                negative,
                by_id[negative.sample_id].image_path,
                effort=effort,
                max_tokens=args.max_tokens,
            )
            for negative in negatives
        ]
        for future in futures:
            try:
                results.append(future.result())
            except Exception as exc:  # noqa: BLE001 —— 一条失败不该毁掉整批
                results.append(
                    {"sample_id": "?", "verdict": "error", "note": f"{type(exc).__name__}: {exc}"}
                )
    elapsed = time.perf_counter() - started
    results.sort(key=lambda row: str(row.get("sample_id")))

    correct = [r for r in results if r["verdict"] == "negative_exit"]
    false_hit = [r for r in results if r["verdict"] == "false_hit"]
    unparseable = [r for r in results if r["verdict"] == "unparseable"]
    no_text = [r for r in results if r["verdict"] == "no_text_output"]
    errors = [r for r in results if r["verdict"] == "error"]

    # -- 误拒数（只从已有跑批读）----------------------------------------
    baselines = []
    for report in args.baseline_report or [
        "runs/g3-eval-2016/sspro-report.json",
        "runs/g2-eval-final/sspro-report.json",
    ]:
        baselines.append(_false_refusals(Path(report), args.arm))

    metrics = {
        "negative_samples": len(negatives),
        "asked": len(results),
        "negative_exit_hits": len(correct),
        "false_hits": len(false_hit),
        "unparseable": len(unparseable),
        "no_text_output": len(no_text),
        "errors": len(errors),
        "negative_exit_rate": (
            round(len(correct) / len(negatives), 4) if negatives else None
        ),
        "negative_exit_rate_numerator_denominator": f"{len(correct)}/{len(negatives)}",
        "false_hit_lands_on_gt": sum(1 for r in false_hit if r.get("hits_gt_bbox")),
    }

    payload = {
        "source_report": str(args.report),
        "arm": args.arm,
        "dataset": str(args.dataset),
        "limit": args.limit,
        "workers": args.workers,
        "elapsed_s": round(elapsed, 1),
        "crop_geometry": {
            "fraction": args.crop_fraction,
            "min_width_px": args.crop_min_width,
            "min_height_px": args.crop_min_height,
            "source": "src/minicode/ground/channel.py（CROP_LINEAR_FRACTION / MIN_CROP_*_PX）",
        },
        "caliber": {
            "negative_sample": "同一张图 + 与正样本同样大小的错位窗口（不含 GT 框）",
            "negative_exit": '模型答 {"x": null, "y": null}（parse_optional_point 返回 None）',
            "unparseable": "回答读不出来/被截断 —— 不算拒答，单独记",
            "no_text_output": "这次没有文本输出（思考吃光预算等）—— 既不算拒答也不算假命中",
            "false_refusals": "正样本上的误拒数：只从已有跑批读（fallback_cause=no_output 且落在 crop_contains）",
        },
        "metrics": metrics,
        "escape_hatch": {
            f"误拒数取自 {baseline['source_report']}": escape_hatch_metrics(
                negative_total=len(negatives),
                correct_refusals=len(correct),
                false_refusals=baseline["false_refusals"],
            )
            for baseline in baselines
        },
        "existing_runs_baseline": baselines,
        "skipped": skipped,
        "negatives": [
            {
                "sample_id": item.sample_id,
                "instruction": item.instruction,
                "ui_type": item.ui_type,
                "bbox": list(item.bbox),
                "image_size": list(item.image_size),
                "coarse_point": list(item.coarse_point),
                "positive_rect": list(item.positive_rect),
                "negative_rect": list(item.negative_rect),
                "crop_size": list(item.crop_size),
                "negative_slot": item.negative_slot,
            }
            for item in negatives
        ],
    }
    (out_dir / "summary.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    with (out_dir / "replies.jsonl").open("w", encoding="utf-8") as handle:
        for row in results:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    (out_dir / "negative-samples.json").write_text(
        json.dumps(
            {
                "count": len(negatives),
                "samples": payload["negatives"],
                "skipped": skipped,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    print(
        f"负样本 {len(negatives)} 条｜负出口命中 {len(correct)} 条"
        f"｜假命中（模型给了点）{len(false_hit)} 条｜读不出来 {len(unparseable)} 条"
        f"｜没有文本 {len(no_text)} 条｜出错 {len(errors)} 条｜{elapsed:.0f} s"
    )
    for key, value in payload["escape_hatch"].items():
        print(
            f"  {key}：recall {value['recall']}（{value['recall_numerator_denominator']}）"
            f"｜precision {value['precision']}（{value['precision_numerator_denominator']}）"
            f"｜误拒 {value['false_refusals']} 条"
        )
    print(f"记录已写入：{out_dir}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

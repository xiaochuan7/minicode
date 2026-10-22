"""**图像 token 探针**：服务端到底看到了多大的那张图？

**为什么要有它**（施工清单第 ⑥ 条）：

Anthropic 官方明确说过「服务端会再缩 → 模型在一个我们没观察过的空间里出坐标
→ 系统性点击漂移」。我们自己的坐标契约是「声明的尺寸 = 模型看到的那张图」，
**一旦服务端再缩，这条契约就断了**。

所以改整屏输入尺寸**之前**必须先探一次：

* 送一张**指定档位**的图（档位按**原图的比例**算，不写死任何像素数）；
* 服务端报的图像 token 数必须**等于** ``ceil(W/28) × ceil(H/28)``
  （本端点每张图还固定多记 2 个 token，探针会**先量出这个开销**再比）；
* **相等** ⇒ 服务端没有再缩 ⇒ 那一次请求的坐标系**可信**；
* **不等** ⇒ 服务端缩了 ⇒ 那次请求的坐标系**不可信** ⇒ 退回更小的档位。

**怎么量**：图像那部分的 token 数 = 服务端报的**总**输入 token − 纯文字那部分；
纯文字那部分**量出来**（逐字相同的提示词不带图再发一次）。
反查与口径在 :mod:`minicode.calib.calibrate`（唯一一处）与
:mod:`minicode.e2e.visual`（唯一一处）。

⚠️ **28 px patch 规则在本端点是【二手】未验证的规则** —— 所以本探针
**验的是「等不等」**，不是「服务端到底缩到多少」：相等是强证据，
不等则是**这条坐标系不可信**的证据。
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from minicode.action.image import encode_frame_base64
from minicode.model.image import downscale_frame
from minicode.model.request_log import estimate_image_tokens

__all__ = ["TokenProbeResult", "main"]


class TokenProbeResult(dict):
    """探针结果（就是一份落盘的字典，键名自解释）。"""


#: **档位阶梯**（**原图的比例**，不是写死的像素数）。
#:
#: 本机生产屏 2560×1600 上，比例 1.0 / 0.8 / 0.7 / 0.6 / 0.5 对应的长边依次是
#: 2560 / 2048 / 1792 / 1536 / 一半屏（**这些数是比例算出来的，不写死在源码里**）。
SCALE_LADDER: tuple[float, ...] = (1.0, 0.8, 0.7, 0.6, 0.5)

#: **标定参照档**（比例）—— 服务端肯定不缩它（远低于它自己的上限），
#: 用来量出「服务端每张图多记的那几个 token」。
REFERENCE_SCALE = 0.48


def _fit_size(frame: Any, scale: float, *, grid: bool = True) -> Any:
    """把 ``frame`` 缩到原图的 ``scale`` 倍（保持宽高比）。

    ``grid=True``（默认）时目标尺寸取 **28 px 网格**上的值（``floor`` 到 28 的倍数）
    —— 这样「服务端没缩它」与「服务端缩了它」两种情形在 token 数上可分辨；
    ``grid=False`` 时**取精确目标尺寸**（用来验「真正要送的那一档」）。
    """
    target_w = max(1, round(frame.width * scale))
    target_h = max(1, round(frame.height * scale))
    if grid:
        target_w = max(28, target_w // 28 * 28)
        target_h = max(28, target_h // 28 * 28)
    return downscale_frame(frame, target_w, target_h)


def _measure(
    client: Any,
    frame: Any,
    *,
    effort: str,
    max_tokens: int,
) -> dict[str, Any]:
    """量一档尺寸（1 次带图 + 1 次纯文字 = 2 次调用）。"""
    from minicode.e2e.visual import measure_text_tokens

    prompt = (
        f"这是一张 {frame.width}×{frame.height} 的截图。"
        "请只回答一个 JSON 对象：{\"ok\": true}"
    )
    image_base64 = encode_frame_base64(frame)
    if image_base64 is None:
        raise RuntimeError("画面编不成 PNG（像素数与尺寸对不上）")
    reply = client.complete(
        prompt,
        effort=effort,
        max_tokens=max_tokens,
        image_base64=image_base64,
    )
    observed_total = (
        (reply.input_tokens or 0)
        + (reply.cache_read_input_tokens or 0)
        + (reply.cache_creation_input_tokens or 0)
    )
    text_tokens = measure_text_tokens(
        client, prompt, effort=effort, max_tokens=max_tokens
    )
    expected = estimate_image_tokens(frame.width, frame.height)
    observed = None if text_tokens is None else observed_total - text_tokens
    return {
        "sent_size": [frame.width, frame.height],
        "expected_image_tokens": expected,
        "observed_image_tokens": observed,
        "observed_total_input_tokens": observed_total,
        "text_tokens": text_tokens,
        "overhead_tokens": None if observed is None else observed - expected,
        "http_status": reply.http_status,
        "latency_ms": reply.latency_ms,
        "model": reply.model,
    }


#: **判定的容差带**（图像 token）。
#:
#: 实测：服务端每张图固定多记 **0~2** 个 token（网关记账，不是缩图）。
#: 所以「服务端有没有再缩」用**差值 ≤ 这个带子**判，别用严格相等。
TOKEN_TOLERANCE = 2


def _first_local_image(dataset_root: Path, size: tuple[int, int] | None = None) -> Path | None:
    """本机**有图**的那张（给了 ``size`` 就只要那个尺寸；找不到就 ``None``）。"""
    from minicode.eval.png_read import read_png_frame

    images = Path(dataset_root) / "images"
    if not images.is_dir():
        return None
    for path in sorted(images.rglob("*.png")):
        try:
            frame = read_png_frame(path)
        except Exception:
            continue
        if size is None or (frame.width, frame.height) == size:
            return path
    return None


def _reconfigure_output() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):  # pragma: no cover
            pass


def main(argv: Sequence[str] | None = None) -> int:
    _reconfigure_output()
    parser = argparse.ArgumentParser(
        prog="minicode.calib.token_probe",
        description="图像 token 探针：服务端到底看到了多大的那张图（施工清单第 ⑥ 条）",
    )
    parser.add_argument(
        "--scale",
        type=float,
        default=0.8,
        help="要探的那一档（**原图的比例**；0.8 = 生产屏上的 2K 档）",
    )
    parser.add_argument(
        "--source-size",
        default="2560x1600",
        help="取样那张原图的尺寸（本机有图的尺寸之一）",
    )
    parser.add_argument("--dataset", default=".scratch/sspro", help="数据集根目录")
    parser.add_argument("--out", default="runs/m6-token-probe", help="产物目录")
    parser.add_argument(
        "--sweep",
        action="store_true",
        help="扫一串档位（找出服务端不再缩的那一档）",
    )
    parser.add_argument("--max-tokens", type=int, default=512, help="输出预算")
    args = parser.parse_args(argv)

    from minicode.eval.png_read import read_png_frame
    from minicode.model.client import ModelClient
    from minicode.model.config import load_config
    from minicode.model.effort import Effort
    from minicode.model.request_log import JsonlRequestLog

    source = tuple(int(value) for value in args.source_size.lower().split("x"))
    path = _first_local_image(Path(args.dataset), source)
    if path is None:
        print(f"⚠️ 本机没有 {source[0]}×{source[1]} 的图 → 探针无法进行")
        return 2
    frame = read_png_frame(path)
    print(f"取样图：{path}（{frame.width}×{frame.height}）")

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    config = load_config()
    client = ModelClient(config, JsonlRequestLog(out_dir / "model-requests.jsonl"))
    effort = Effort.LOW.value

    # -- 参照档：量出「服务端每张图多记的 token」（实测 +2）-----------------
    reference_frame = _fit_size(frame, REFERENCE_SCALE)
    reference = _measure(
        client, reference_frame, effort=effort, max_tokens=args.max_tokens
    )
    overhead = reference["overhead_tokens"]
    print(
        f"参照档 {reference_frame.width}×{reference_frame.height}："
        f"期望 {reference['expected_image_tokens']}"
        f"｜实测 {reference['observed_image_tokens']}"
        f"｜**服务端每张图多记 {overhead} token**"
    )

    scales = SCALE_LADDER if args.sweep else (args.scale,)
    results: list[dict[str, Any]] = []
    for scale in scales:
        probe_frame = _fit_size(frame, scale, grid=args.sweep)
        result = _measure(
            client, probe_frame, effort=effort, max_tokens=args.max_tokens
        )
        observed = result["observed_image_tokens"]
        expected = result["expected_image_tokens"]
        #: **扣除服务端那点固定开销之后**，与服务端没缩时的应有值比较。
        result["server_kept_size"] = (
            observed - overhead if observed is not None and overhead is not None else None
        )
        #: **判据**：差值落在容差带里 ⇒ 服务端**没有再缩**。
        result["difference"] = (
            None if observed is None or overhead is None else observed - overhead - expected
        )
        result["equal"] = (
            abs(result["difference"]) <= TOKEN_TOLERANCE
            if result["difference"] is not None
            else None
        )
        result["verdict"] = (
            "unknown"
            if result["equal"] is None
            else (
                "服务端没有再缩（坐标系可信）"
                if result["equal"]
                else "服务端缩了（坐标系不可信）"
            )
        )
        results.append(result)
        print(
            f"  {probe_frame.width}×{probe_frame.height}（{scale} 档）："
            f"期望 {expected}"
            f"｜实测 {observed}（扣除开销后 {result['server_kept_size']}，"
            f"差 {result['difference']}）"
            f"｜{'相等 ✓' if result['equal'] else '不相等 ✗'}"
        )

    payload = {
        "sweep": bool(args.sweep),
        "source_image": str(path),
        "source_size": [frame.width, frame.height],
        "reference": reference,
        "overhead_tokens": overhead,
        "results": results,
    }
    path_out = out_dir / "token-probe.json"
    path_out.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"记录已写入：{path_out}")
    if args.sweep:
        return 0
    return 0 if results[0]["equal"] else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

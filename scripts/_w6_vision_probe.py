"""一次性视觉通道探针（W6 前置验证）：向 .env 端点发**一次**带图请求。

只验证三件事：① 端点连通且接受图像输入；② 回答里有文本（区分
content/reasoning_content 是 ModelClient 既有职责）；③ 文本能被生产
解析器 parse_coarse_target 解析出 bbox。**不做落点判定**——探针不是
实验，只验通道。密钥只从 .env 读进进程环境，绝不打印、绝不落盘。
"""

from __future__ import annotations

import os
import sys
import traceback
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "src"))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

_DATASET = Path(r"C:\Users\22197\Desktop\ik评测\sspro-dataset")


def _load_env() -> None:
    env = _ROOT / ".env"
    if not env.exists():
        raise RuntimeError(".env 不存在——模型配置缺失")
    for line in env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip())


def main() -> int:
    _load_env()

    from minicode.action.image import encode_frame_base64
    from minicode.eval.png_read import read_png_frame
    from minicode.eval.sspro import load_samples
    from minicode.ground.prompt import coarse_prompt, parse_coarse_target
    from minicode.model.client import ModelClient
    from minicode.model.config import load_config
    from minicode.model.image import downscale_frame, model_input_size_for
    from minicode.model.request_log import JsonlRequestLog
    from minicode.model.retry import RetryingModelCaller

    config = load_config()
    print(f"model={config.model}")
    print(f"endpoint={config.endpoint}")

    samples = load_samples(_DATASET)
    sample = next((s for s in samples if s.image_path.exists()), None)
    if sample is None:
        print("VISION PROBE: FAIL（数据集目录里没有一张已下载的图片）")
        return 2
    print(
        f"sample_id={sample.sample_id} image={sample.image_path.name} "
        f"size={sample.image_size}"
    )

    frame = read_png_frame(sample.image_path)
    model_input = model_input_size_for(*sample.image_size)
    screen = downscale_frame(frame, model_input[0], model_input[1])
    image_b64 = encode_frame_base64(screen)
    prompt = coarse_prompt(sample.instruction, model_input)
    print(f"image_b64_bytes={len(image_b64)} prompt_chars={len(prompt)}")

    log_path = _ROOT / "runs" / "w6" / "probe-requests.jsonl"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    caller = RetryingModelCaller(ModelClient(config, JsonlRequestLog(log_path)))

    reply = caller.complete(
        # 8192：首探 2048 时思考链把预算吃光（content 为空）——与 W6 runner
        # 同一输出预算，探针结论才能外推到正式实验。
        prompt, effort="low", max_tokens=8192, image_base64=image_b64
    )
    text = reply.text or ""
    print(f"reply_text_chars={len(text)}")
    print(f"reply_head={' '.join(text.split())[:200]}")

    answer = parse_coarse_target(text)
    print(f"parsed_bbox={answer.bbox} negative={answer.negative}")
    ok = bool(text.strip()) and not answer.negative and answer.bbox is not None
    print(f"VISION PROBE: {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception:  # noqa: BLE001 —— 探针失败要原样暴露
        traceback.print_exc()
        print("VISION PROBE: FAIL（异常见上）")
        raise SystemExit(1) from None

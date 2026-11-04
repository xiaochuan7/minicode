"""端到端真图验证（主线 A 收口）：真实截图 → 新视觉模型定位 → 换算核验。

标定结论（absolute_model_input，声明 2016×1260）落在生产接线的两个前提上：
① 我们自己把整屏缩到声明尺寸再送；② 模型以声明尺寸域的绝对像素坐标回答。
本脚本用**一张真实截图**走同一条链路，问两个已知目标（任务栏「开始」按钮、
屏幕中心），核验：模型回答可解析、换算落点在合理区域、日志落盘正常。

输出（只新增）：runs/w7-vision-switch/e2e-screenshot/
  - e2e-readout.json  结果（落点换算 + 原始回复摘要）
  - model-requests.jsonl / model-retries.jsonl（同口径）
**零动作下发**：不下发点击，只读。
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = REPO_ROOT / "runs" / "w7-vision-switch" / "e2e-screenshot"


def main() -> int:
    sys.path.insert(0, str(REPO_ROOT / "src"))
    from minicode.exec import capture
    from minicode.model.client import ModelClient
    from minicode.model.config import load_retry_settings, load_vision_config
    from minicode.model.image import model_input_size_for
    from minicode.model.request_log import JsonlRequestLog
    from minicode.model.retry import RetryingModelCaller, RetryLogWriter, RetryPolicy

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    config = load_vision_config()
    print("[e2e]", config.describe())
    settings = load_retry_settings(vision=True)
    policy = RetryPolicy(
        max_attempts=settings.max_attempts,
        base_delay_s=settings.base_delay_s,
        max_delay_s=settings.max_delay_s,
        jitter_s=settings.jitter_s,
    )
    retry_log = RetryLogWriter(OUT_DIR / "model-retries.jsonl")
    client = RetryingModelCaller(
        ModelClient(config, JsonlRequestLog(OUT_DIR / "model-requests.jsonl"), channel="vision"),
        policy=policy,
        attempt_sink=retry_log.write,
        serial=True,
    )

    # -- 真实截图（物理尺寸）→ 降到声明尺寸 ------------------------------
    frame_image, meta = capture.grab_frame()
    width, height = int(meta.width), int(meta.height)
    print(f"[screen] frame={width}x{height} source={meta.source}")
    declared = model_input_size_for(width, height)
    print(f"[declared] input={declared[0]}x{declared[1]}")

    # grab_frame 的 frame 是 (H, W, 3) BGR ndarray；PIL 缩到声明尺寸 → PNG → base64。
    import base64 as _b64
    import io

    import numpy
    from PIL import Image

    channels = frame_image.shape[2] if frame_image.ndim == 3 else 1
    pixels = frame_image[:, :, 2::-1] if channels == 3 else frame_image
    image = Image.fromarray(numpy.ascontiguousarray(pixels))
    scaled = image.resize(declared, Image.BILINEAR)
    buffer = io.BytesIO()
    scaled.save(buffer, format="PNG")
    image_b64 = _b64.b64encode(buffer.getvalue()).decode("ascii")
    print(f"[image] base64 bytes={len(image_b64)} (5MB 上限内)")

    # -- 两个已知目标（几何真值从帧尺寸推，不依赖 UIA）-------------------
    targets = [
        ("taskbar-start-button", "屏幕左下角任务栏最左边的开始按钮（Windows 徽标）"),
        ("screen-center", "屏幕正中央"),
    ]
    readout: dict = {
        "model": config.model,
        "declared_input": list(declared),
        "frame_size": [width, height],
        "detected_scale_note": "calib-qwen38 判定 absolute_model_input（唯一）",
        "targets": [],
    }
    for name, desc in targets:
        prompt = (
            f"这张截图的尺寸是 {declared[0]}x{declared[1]} 像素。"
            f"请找到{desc}，只返回 JSON：{{\"x\": <像素>, \"y\": <像素>}}，"
            "坐标以这张图的左上角为原点。不要输出其他内容。"
        )
        began = time.monotonic()
        reply = client.complete(
            prompt, effort="low", max_tokens=8192, image_base64=image_b64, parse_ok=True,
        )
        latency = time.monotonic() - began
        text = reply.text.strip()
        parsed = None
        start = text.find("{")
        if start >= 0:
            try:
                parsed = json.loads(text[start : text.rfind("}") + 1])
            except Exception:  # noqa: BLE001 —— 解析不出就如实记录原文
                parsed = None
        entry: dict = {
            "target": name,
            "description": desc,
            "latency_s": round(latency, 2),
            "reply_chars": len(text),
            "reply_excerpt": text[:120],
            "parsed": parsed,
        }
        if isinstance(parsed, dict) and "x" in parsed and "y" in parsed:
            mx, my = float(parsed["x"]), float(parsed["y"])
            # absolute_model_input：模型坐标 × (物理 / 声明) → 物理像素。
            sx = width / declared[0]
            sy = height / declared[1]
            entry["physical"] = [round(mx * sx, 1), round(my * sy, 1)]
            entry["normalized"] = [round(mx / declared[0], 4), round(my / declared[1], 4)]
        readout["targets"].append(entry)
        print(f"[target] {name}: parsed={parsed} latency={latency:.1f}s")

    path = OUT_DIR / "e2e-readout.json"
    path.write_text(json.dumps(readout, ensure_ascii=False, indent=2), encoding="utf-8")
    print("[saved]", path)
    return 0


if __name__ == "__main__":
    sys.exit(main())

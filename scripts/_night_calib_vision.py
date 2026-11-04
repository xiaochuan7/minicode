"""今夜主线 A 的**坐标标定**：对新视觉模型（Qwen3.8-Flash-Next）真跑探针。

参照 e2e/visual.py 的 _calibrate 拼装（同一个 client 构造方式：可配重试 +
重试落盘 + 串行），但**独立成脚本** —— 只跑标定与准入判定，不做任何动作。

输出（只新增不改）：``runs/w7-vision-switch/calib-qwen38/``
  - calib-readout.json   标定读数（detected_scale / verdict / 等价族 / 误差）
  - model-requests.jsonl 每次请求（既有 JsonlRequestLog 口径）
  - model-retries.jsonl  每轮重试尝试（W7 新口径）

**零动作下发**：本脚本不下发任何鼠标/键盘事件。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = REPO_ROOT / "runs" / "w7-vision-switch" / "calib-qwen38"


def main() -> int:
    from minicode.calib.admission import CalibrationAdmission
    from minicode.calib.calibrate import CalibrationProbe
    from minicode.calib.probe import default_probe_spec
    from minicode.e2e.visual import PromptCapturingCaller, RecordingSink
    from minicode.exec import dpi
    from minicode.model.client import ModelClient
    from minicode.model.config import load_retry_settings, load_vision_config
    from minicode.model.image import model_input_size_for
    from minicode.model.request_log import JsonlRequestLog
    from minicode.model.retry import RetryingModelCaller, RetryLogWriter, RetryPolicy

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    config = load_vision_config()
    print("[calib]", config.describe())

    sink = RecordingSink(JsonlRequestLog(OUT_DIR / "model-requests.jsonl"))
    settings = load_retry_settings(vision=True)
    policy = RetryPolicy(
        max_attempts=settings.max_attempts,
        base_delay_s=settings.base_delay_s,
        max_delay_s=settings.max_delay_s,
        jitter_s=settings.jitter_s,
    )
    print(f"[retry] attempts={settings.max_attempts} base={settings.base_delay_s}s "
          f"max={settings.max_delay_s}s jitter={settings.jitter_s}s")
    retry_log = RetryLogWriter(OUT_DIR / "model-retries.jsonl")
    client = PromptCapturingCaller(
        RetryingModelCaller(
            ModelClient(config, sink, channel="vision"),
            policy=policy,
            attempt_sink=retry_log.write,
            serial=True,
        )
    )

    width, height = dpi.physical_screen_size()
    declared = model_input_size_for(width, height)
    print(f"[screen] physical={width}x{height} declared_input={declared[0]}x{declared[1]}")

    probe = CalibrationProbe(
        client,
        spec=default_probe_spec(width, height),
        model_input_size=declared,
        call_path="image",
        model_id=config.model,
    )
    outcome = probe.run()

    admission = CalibrationAdmission(outcome)
    verdict = admission.admit()

    readout = {
        "model": config.model,
        "endpoint": config.endpoint,
        "physical_screen": [width, height],
        "model_input_size": list(declared),
        "rounds": outcome.rounds,
        "detected_scale": outcome.detected_scale.value,
        "scale_verdict": outcome.scale_verdict,
        "landing_equivalence": outcome.landing_equivalence,
        "equivalent_scales": [s.value for s in outcome.equivalent_scales],
        "divergences_px": {
            k.value: v for k, v in outcome.divergences_px.items()
        },
        "calibration_ok": outcome.calibration_ok,
        "hit_rate": outcome.hit_rate,
        "median_error_px": outcome.median_error_px,
        "p90_error_px": outcome.p90_error_px,
        "hit_radius_px": outcome.hit_radius_px,
        "admission_verdict": str(verdict),
        "admission_reason": getattr(verdict, "reason", None)
        or getattr(verdict, "message", None),
        "raw_points": [
            {"name": n, "x": x, "y": y} for (n, x, y) in outcome.raw_points
        ],
        "targets": [
            {"name": n, "x": x, "y": y} for (n, x, y) in outcome.targets
        ],
    }
    path = OUT_DIR / "calib-readout.json"
    path.write_text(
        json.dumps(readout, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print("[verdict] detected_scale =", outcome.detected_scale.value)
    print("[verdict] scale_verdict  =", outcome.scale_verdict)
    print("[verdict] equivalence    =", outcome.landing_equivalence)
    print("[verdict] equivalent     =",
          [s.value for s in outcome.equivalent_scales])
    print("[verdict] hit_rate       =", round(outcome.hit_rate, 4))
    print("[verdict] median_error   =", round(outcome.median_error_px, 2), "px")
    print("[verdict] p90_error      =", round(outcome.p90_error_px, 2), "px")
    print("[verdict] admission      =", verdict)
    print("[saved]  ", path)
    return 0


if __name__ == "__main__":
    sys.exit(main())

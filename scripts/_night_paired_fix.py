"""离线修复配对臂的换算错误（零模型调用）：raw_center 已全部落盘，
当时用错了 CoordinateAdapter 签名导致 57 条 error —— 用 w6 同款换算
（CoordinateAdapter(model_scale=scale).to_screen）重算 point 与 hit。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "src"))
sys.path.insert(0, str(_ROOT / "scripts"))

from w6_paired_coords import _geometry  # noqa: E402

from minicode.coord.coordinates import CoordinateAdapter, ModelScale  # noqa: E402
from minicode.eval.sspro import load_samples  # noqa: E402

sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def main() -> int:
    report_path = _ROOT / "runs/w7-vision-switch/paired-qwen38/pixel-arm.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    samples = load_samples(Path(r"C:\Users\22197\Desktop\ik评测\sspro-dataset"))
    by_id = {s.sample_id: s for s in samples}

    fixed = 0
    for row in report["rows"]:
        sample = by_id[row["sample_id"]]
        raw_center = row.get("raw_center")
        if not raw_center:
            continue
        from minicode.model.image import model_input_size_for

        geometry = _geometry(
            sample.image_size, model_input_size_for(sample.image_size[0], sample.image_size[1])
        )
        adapter = CoordinateAdapter(model_scale=ModelScale.ABSOLUTE_MODEL_INPUT)
        try:
            px, py = adapter.to_screen(raw_center[0], raw_center[1], geometry)
        except Exception as exc:  # noqa: BLE001 —— 越界按 w6 同款口径：如实记 error，落点 None = miss
            row["error"] = f"{type(exc).__name__}: {exc}"
            row["point"] = None
            row["hit"] = False
            continue
        x0, y0, x1, y1 = sample.bbox
        row["point"] = [round(px, 1), round(py, 1)]
        row["hit"] = bool(x0 <= px <= x1 and y0 <= py <= y1)
        row["error"] = ""
        fixed += 1

    hits = sum(1 for row in report["rows"] if row["hit"])
    errors = sum(1 for row in report["rows"] if row["error"])
    report["hits"] = hits
    report["errors"] = errors
    report["note"] = "换算签名错误已于离线修复（raw_center 全部落盘，零重跑）"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"fixed={fixed} hits={hits}/60 errors={errors}")
    print("命中样本：")
    for row in report["rows"]:
        if row["hit"]:
            print("  ", row["sample_id"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

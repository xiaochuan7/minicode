"""V5 最新 trace 详查 + model_input_size 核实（诊断脚本）。"""
import json
import sys
from pathlib import Path

sys.path.insert(0, "src")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
from minicode.model.image import model_input_size_for  # noqa: E402

print("model_input_size_for(2560,1600) =", model_input_size_for(2560, 1600))

lines = [
    json.loads(line)
    for line in Path("runs/v5-smoke/w01/action-records.jsonl")
    .read_text(encoding="utf-8")
    .splitlines()
    if line.strip()
]
print("records:", len(lines))
for index, r in enumerate(lines):
    echoes = r.get("grounding_echoes") or []
    print(
        f"#{index} channel={r.get('channel')} landing={r.get('point')} "
        f"fallback={r.get('fallback')} reason={(r.get('fallback_reason') or '')[:60]}"
    )
    for e in echoes:
        print(
            f"    echo stage={e.get('stage')} raw=({e.get('raw_x')},{e.get('raw_y')}) "
            f"sent={e.get('sent_width')}x{e.get('sent_height')}"
        )
    plan = r.get("crop_plan") or {}
    print(
        f"    crop origin=({plan.get('origin_x')},{plan.get('origin_y')}) "
        f"size={plan.get('width')}x{plan.get('height')}"
    )

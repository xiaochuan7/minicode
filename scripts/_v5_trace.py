"""V5 smoke trace 速查（诊断脚本）。"""
import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
lines = [
    json.loads(line)
    for line in Path("runs/v5-smoke/w01/action-records.jsonl")
    .read_text(encoding="utf-8")
    .splitlines()
    if line.strip()
]
r = lines[-1]
print("timings_ms:", r.get("timings_ms"))
print("channel:", r.get("channel"), "model_calls:", r.get("model_calls"))
print("fallback:", r.get("fallback"), (r.get("fallback_reason") or "")[:90])
echoes = r.get("grounding_echoes") or []
print("echoes:", [(e.get("stage"), e.get("raw_x"), e.get("raw_y")) for e in echoes])
print("crop_plan keys:", list((r.get("crop_plan") or {}).keys())[:8])

"""组 C 的 d3 调用日志速查（诊断脚本）。"""
import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
path = Path("runs/v5/group-c-d3-calls.jsonl")
rows = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
print(f"d3 调用 {len(rows)} 次")
for r in rows[:10]:
    probs = r.get("probabilities") or {}
    top = max(probs.items(), key=lambda kv: kv[1]) if probs else ("-", 0)
    print(
        f"  options={r.get('option_count')} conf={r.get('confidence')} "
        f"top={top[0]}:{top[1]:.3f} status={r.get('http_status')} "
        f"error={(r.get('error') or '')[:60]} latency={r.get('latency_ms')}ms"
    )

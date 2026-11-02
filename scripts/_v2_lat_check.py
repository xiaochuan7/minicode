"""V2 请求日志的延迟分布速查（诊断脚本，不入交付）。"""
import json
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
rows = [json.loads(line) for line in open("runs/v2/model-requests.jsonl", encoding="utf-8")]
lat = sorted(r["latency_ms"] for r in rows if r.get("latency_ms") is not None)
if lat:
    print(
        f"n={len(rows)} p50={lat[len(lat)//2]/1000:.1f}s "
        f"p90={lat[int(len(lat)*0.9)]/1000:.1f}s max={lat[-1]/1000:.1f}s "
        f"errors={sum(1 for r in rows if r.get('error'))}"
    )
else:
    print(f"n={len(rows)} 无延迟数据")

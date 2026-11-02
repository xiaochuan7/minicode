"""V2 最近请求诊断（诊断脚本，不入交付）。"""
import json
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
rows = [json.loads(line) for line in open("runs/v2/model-requests.jsonl", encoding="utf-8")]
print(f"总请求 {len(rows)}")
for r in rows[-6:]:
    print(
        f"  status={r.get('http_status')} latency={round((r.get('latency_ms') or 0)/1000,1)}s "
        f"text_len={r.get('text_len')} error={(r.get('error') or '')[:110]}"
    )

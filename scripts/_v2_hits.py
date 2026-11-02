"""V2 增量 raw 的即时命中统计（诊断脚本）。"""
import json
import sys
from pathlib import Path

sys.path.insert(0, "src")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
raw = json.loads(Path("runs/v2/v2-raw.json").read_text(encoding="utf-8"))
print(f"已完成 {len(raw)} 条，hit={sum(1 for r in raw if r.get('surface_point'))} 个有点，",
      f"错误 {sum(1 for r in raw if r.get('error'))} 条")
errors = [r["error"][:60] for r in raw if r.get("error")]
for e in errors[:5]:
    print("  err:", e)

"""W4 失败明细速查（临时脚本，跑完删）。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
for tid in sys.argv[1:]:
    path = ROOT / "runs" / "w4" / "round1" / f"{tid}-summary.json"
    if not path.exists():
        print(f"== {tid}: no summary")
        continue
    data = json.loads(path.read_text(encoding="utf-8"))
    for result in data["results"]:
        print(f"== {tid} attempt {result.get('attempt')} {result.get('attempt_status')}")
        for step in result.get("steps", []):
            print(
                f"  step {step.get('step')} {step.get('do')} ok={step.get('ok')} "
                f"code={step.get('error_code')} notes={str(step.get('notes'))[:150]}"
            )
        verdict = result.get("verdict") or {}
        print(f"  verdict ok={verdict.get('ok')} detail={str(verdict)[:220]}")

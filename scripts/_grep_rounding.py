"""搜取整口径（临时，跑完删）。"""
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parent.parent / "src" / "minicode"
total = 0
for sub in ("ground", "action"):
    for p in sorted((ROOT / sub).glob("*.py")):
        for i, raw_line in enumerate(p.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            stripped = raw_line.strip()
            if ("round(" in raw_line or "floor" in raw_line) and not stripped.startswith("#"):
                print(f"{p.name}:{i}: {stripped[:130]}")
                total += 1
print("total:", total)

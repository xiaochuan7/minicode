"""W6 数据准备：从 hf-mirror 下载 ScreenSpot-Pro 标注（临时脚本，跑完删）。

数据集放在**仓库外**（C:/Users/22197/Desktop/ik评测/sspro-dataset/）——
红线允许「样本可下载到仓库外」。
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
DATA = Path(r"C:\Users\22197\Desktop\ik评测\sspro-dataset")
BASE = "https://hf-mirror.com/datasets/likaixin/ScreenSpot-Pro/resolve/main/"


def fetch(path: str, dest: Path) -> bool:
    dest.parent.mkdir(parents=True, exist_ok=True)
    for attempt in range(3):
        result = subprocess.run(
            ["curl", "-sL", "--max-time", "120", BASE + path, "-o", str(dest)],
            capture_output=True,
        )
        if result.returncode == 0 and dest.exists() and dest.stat().st_size > 2:
            head = dest.read_bytes()[:64]
            if b"Invalid username" not in head and b"error" not in head.lower():
                return True
        time.sleep(2 + attempt * 3)
    return False


def main() -> int:
    listing = json.loads((ROOT / ".scratch" / "hf_ann2.json").read_text(encoding="utf-8"))
    ok = failed = 0
    for item in listing:
        rel = item["path"]  # annotations/xxx.json
        dest = DATA / rel
        if dest.exists() and dest.stat().st_size > 100:
            ok += 1
            continue
        if fetch(rel, dest):
            ok += 1
            print(f"ok  {rel} ({dest.stat().st_size} B)")
        else:
            failed += 1
            print(f"FAIL {rel}")
    print(f"annotations: {ok} ok, {failed} failed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())

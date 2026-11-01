"""W6 数据准备：下载分层子集需要的图（临时脚本，跑完删）。"""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import quote

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
DATA = Path(r"C:\Users\22197\Desktop\ik评测\sspro-dataset")
BASE = "https://hf-mirror.com/datasets/likaixin/ScreenSpot-Pro/resolve/main/"


def fetch(path: str, dest: Path) -> bool:
    dest.parent.mkdir(parents=True, exist_ok=True)
    # 文件名带空格（「Screenshot from ...」）→ URL 里必须编码。
    url = BASE + quote(path)
    for attempt in range(4):
        result = subprocess.run(
            ["curl", "-sL", "--max-time", "600", url, "-o", str(dest)],
            capture_output=True,
        )
        if result.returncode == 0 and dest.exists() and dest.stat().st_size > 10000:
            head = dest.read_bytes()[:64]
            if b"Invalid username" not in head:
                return True
        time.sleep(3 + attempt * 5)
    return False


def main() -> int:
    names = [
        line.strip()
        for line in (ROOT / ".scratch" / "w6_images.txt")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
    ]
    print(f"需要 {len(names)} 张图")
    ok = failed = 0
    for index, name in enumerate(names, start=1):
        rel = f"images/{name}"
        dest = DATA / rel
        if dest.exists() and dest.stat().st_size > 10000:
            ok += 1
            continue
        if fetch(rel, dest):
            ok += 1
            print(f"[{index}/{len(names)}] ok {name} ({dest.stat().st_size // 1024} KB)", flush=True)
        else:
            failed += 1
            print(f"[{index}/{len(names)}] FAIL {name}", flush=True)
    print(f"images: {ok} ok, {failed} failed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())

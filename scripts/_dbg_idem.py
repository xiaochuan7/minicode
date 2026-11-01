"""幂等 LRU 差一复现（临时，跑完删）。"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, "src")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from minicode.action.records import ActionLog  # noqa: E402
from minicode.serve.fakes import FAKE_TARGET, fake_wiring  # noqa: E402
from minicode.serve.service import ActionService  # noqa: E402


def main() -> int:
    n = 512
    with tempfile.TemporaryDirectory() as tmp:
        wiring, _inj = fake_wiring()
        service = ActionService(seam_factory=wiring, log_directory=Path(tmp))
        for index in range(n):
            resp = service.execute(
                {"protocol": "cua.action/1", "action": "click",
                 "target": FAKE_TARGET, "request_id": f"id-{index}"}
            )
            if not resp.ok:
                print(f"id-{index} FAILED: {resp.error_code} {resp.error_message}")
        log_path = Path(tmp) / "action-records.jsonl"
        raw_lines = log_path.read_text(encoding="utf-8").splitlines()
        records = ActionLog(Path(tmp)).records()
        print(f"executes={n} raw_lines={len(raw_lines)} records={len(records)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

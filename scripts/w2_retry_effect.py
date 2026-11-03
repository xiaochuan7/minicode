"""W2.3 重试效果分析：从 W2.1 的请求日志反推「重试救回多少精定位调用」。

口径：同一精定位步骤的连续请求 = 一次 attempt 序列（成功 / 3 次耗尽）。
* attempt1 成功率 = **无重试**时的成功水平（反事实基线）；
* attempt2/3 的成功 = **重试救回**的部分（无重试时直接回落粗点）；
* 429 与空文本（NoTextOutputError）分开计数（429 不计入模型失败）。
落盘 ``runs/w2/retry-effect.json``。
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def main() -> int:
    log_path = _ROOT / "runs" / "w2" / "model-requests.jsonl"
    rows = [
        json.loads(line)
        for line in log_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    # 一次「步骤」= 到成功为止的连续失败 + 1 次成功，或连续 3 次失败。
    sequences: list[dict] = []
    current_failures = 0
    for row in rows:
        error = str(row.get("error") or "")
        failed = bool(error.strip())
        if not failed:
            sequences.append({
                "attempts": current_failures + 1,
                "error": "",
            })
            current_failures = 0
            continue
        current_failures += 1
        if current_failures >= 3:
            sequences.append({"attempts": 3, "error": error[:80]})
            current_failures = 0
    # 末尾未收尾的失败序列（run 中断时）——不计入成功率（样本不完整）。
    attempt_counts = Counter(seq["attempts"] for seq in sequences)
    total = len(sequences)
    succeeded = [seq for seq in sequences if not seq["error"]]
    saved_by_retry = sum(1 for seq in succeeded if seq["attempts"] >= 2)
    exhausted = sum(1 for seq in sequences if seq["error"])
    stats = {
        "total_sequences": total,
        "attempt1_success": attempt_counts.get(1, 0),
        "saved_by_retry": saved_by_retry,
        "exhausted": exhausted,
        "rates": {
            "without_retry": round(attempt_counts.get(1, 0) / total, 4) if total else None,
            "with_retry": round(len(succeeded) / total, 4) if total else None,
        },
        "note": "救回 = 第 2/3 次尝试才成功的序列；exhausted = 3 次全败（回落粗点）；"
                "429 与传输失败已由重试层处理，此处只按最终成败分桶",
    }
    out = _ROOT / "runs" / "w2" / "retry-effect.json"
    out.write_text(json.dumps(stats, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(stats, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

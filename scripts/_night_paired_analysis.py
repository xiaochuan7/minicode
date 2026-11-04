"""配对分析（离线）：新模型像素臂 vs 历史 deepseek 像素臂 —— McNemar。

读 runs/w7-vision-switch/paired-qwen38/pixel-arm.json（新模型）与
docs/w6-paired.json（deepseek，同批 60 条同 seed），逐 sample_id 配对：

* 双臂各自命中率 + Δ（新 − 旧，百分点）；
* McNemar exact（二项检验）与 mid-p（工单口径：两个都给）；
* Δ 的 95% Wilson CI；
* MDE 对照（n=60 的 80% 功效 MDE ≈ 18.7pp —— 工单沿用 W2 口径）；
* 判定规则（预注册）：|Δ| < MDE → 「未达 MDE，判定无效」，不许事后改口径。
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent


def _wilson_ci(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    denom = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (max(0.0, center - half), min(1.0, center + half))


def _mcnemar(b: int, c: int) -> tuple[float, float]:
    """exact 二项 p 值与 mid-p。b/c = 两个方向的不一致格数。"""
    n = b + c
    if n == 0:
        return (1.0, 1.0)
    k = min(b, c)
    # exact 双侧：2 * P(X ≤ min(b,c) | B(n, 0.5))
    exact = 2.0 * sum(math.comb(n, i) for i in range(0, k + 1)) / 2**n
    exact = min(1.0, exact)
    # mid-p：把中间点的那份概率减半
    mid = exact - (math.comb(n, k) / 2**n)
    return (exact, mid)


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    new_path = _ROOT / "runs/w7-vision-switch/paired-qwen38/pixel-arm.json"
    old_path = _ROOT / "docs/w6-paired.json"
    new = json.loads(new_path.read_text(encoding="utf-8"))
    old = json.loads(old_path.read_text(encoding="utf-8"))

    new_rows = {row["sample_id"]: row for row in new["rows"]}
    old_rows = {row["sample_id"]: row for row in old["outcomes"]["pixel"]}
    shared = sorted(set(new_rows) & set(old_rows))
    n = len(shared)
    new_hits = sum(1 for sid in shared if new_rows[sid]["hit"])
    old_hits = sum(1 for sid in shared if old_rows[sid]["hit"])
    b = sum(1 for sid in shared if new_rows[sid]["hit"] and not old_rows[sid]["hit"])
    c = sum(1 for sid in shared if not new_rows[sid]["hit"] and old_rows[sid]["hit"])
    delta_pp = (new_hits - old_hits) / n * 100
    new_err = sum(1 for sid in shared if new_rows[sid].get("error"))
    ci = _wilson_ci(new_hits, n)

    exact_p, mid_p = _mcnemar(b, c)
    mde = 18.7  # W2 口径：n=60、80% 功效的 MDE（配对 McNemar）

    print(f"配对样本 n = {n}")
    print(f"新模型（{new['model']}）命中 = {new_hits}/{n}（错误 {new_err} 条）")
    print(f"deepseek（{old['model']}）命中 = {old_hits}/{n}")
    print(f"Δ = {delta_pp:+.1f}pp（新 − 旧）")
    print(f"不一致格：b（新对旧错）= {b}，c（新错旧对）= {c}")
    print(f"新模型命中率 Wilson 95% CI = [{ci[0]*100:.1f}, {ci[1]*100:.1f}]pp")
    print(f"McNemar exact p = {exact_p:.4f}；mid-p = {mid_p:.4f}")
    verdict = (
        "达到 MDE（18.7pp）—— 可下结论"
        if abs(delta_pp) >= mde
        else f"|Δ| = {abs(delta_pp):.1f}pp < MDE {mde}pp —— 判定无效，保留现模型口径"
    )
    print(verdict)

    out = {
        "n": n,
        "new_hits": new_hits,
        "old_hits": old_hits,
        "delta_pp": round(delta_pp, 2),
        "discordant_b_new_hit_old_miss": b,
        "discordant_c_new_miss_old_hit": c,
        "mcnemar_exact_p": round(exact_p, 5),
        "mcnemar_mid_p": round(mid_p, 5),
        "new_wilson_ci_pp": [round(ci[0] * 100, 1), round(ci[1] * 100, 1)],
        "mde_pp": mde,
        "verdict": verdict,
        "new_model": new["model"],
        "old_model": old["model"],
        "new_errors": new_err,
    }
    out_path = new_path.parent / "paired-analysis.json"
    out_path.write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print("[saved]", out_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

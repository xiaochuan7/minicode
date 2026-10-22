"""**两个跑批的配对比较**（零模型调用）—— 施工清单 §六 的 P2 层。

把两份 ``sspro-report.json`` 按 ``sample_id`` **对齐**，算一张 2×2 表：

* ``b`` —— 左臂命中、右臂不命中；
* ``c`` —— 左臂不命中、右臂命中；

然后出 **McNemar 精确二项 / mid-p / χ²+连续性校正**、**配对 bootstrap Δ 的 95% CI**、
两臂各自的 **Wilson 95% CI**，以及 **icon / text 分层**。

⚠️ **口径纪律**：

* 这是**同批跨跑批**的配对比较 —— 配对消掉「样本难度」的噪声，
  **消不掉**「模型重抽」的噪声（网关忽略 temperature：同图同提示词 10 次原文
  一致率只有 10%）→ 结论里**必须**写明这一条；
* **不许**把两次独立跑批的绝对值差当效应（只报数、不下结论的那一层）。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from . import configure_utf8_output
from .stats import (
    mcnemar_chisq_cc,
    mcnemar_exact,
    mcnemar_midp,
    paired_bootstrap_delta,
    wilson_ci,
)

__all__ = ["compare", "main"]


def _load_arm(report_path: Path, arm: str) -> dict[str, dict[str, Any]]:
    report = json.loads(report_path.read_text(encoding="utf-8"))
    outcomes = report.get("outcomes", {}).get(arm)
    if outcomes is None:
        raise SystemExit(f"{report_path} 里没有 outcomes.{arm}")
    return {str(item["sample_id"]): item for item in outcomes}


def _paired(
    left: dict[str, dict[str, Any]],
    right: dict[str, dict[str, Any]],
    *,
    ids: list[str],
    label_left: str,
    label_right: str,
) -> dict[str, Any]:
    b = c = 0
    left_hits = right_hits = 0
    for sample_id in ids:
        left_hit = bool(left[sample_id]["hit"])
        right_hit = bool(right[sample_id]["hit"])
        left_hits += int(left_hit)
        right_hits += int(right_hit)
        if left_hit and not right_hit:
            b += 1
        elif right_hit and not left_hit:
            c += 1
    n = len(ids)
    boot = paired_bootstrap_delta(
        [(bool(left[i]["hit"]), bool(right[i]["hit"])) for i in ids]
    )
    low = boot.ci_low * 100.0
    high = boot.ci_high * 100.0
    return {
        "left": label_left,
        "right": label_right,
        "samples": n,
        "left_hits": left_hits,
        "right_hits": right_hits,
        "left_accuracy": left_hits / n if n else None,
        "right_accuracy": right_hits / n if n else None,
        "left_wilson_ci95": list(wilson_ci(left_hits, n)) if n else None,
        "right_wilson_ci95": list(wilson_ci(right_hits, n)) if n else None,
        "discordant": {"left_only": b, "right_only": c},
        "delta_rows": c - b,
        "delta_pp": (c - b) / n * 100 if n else 0.0,
        "delta_ci95_pp": [low, high],
        "ci_excludes_zero": (low > 0) or (high < 0),
        "mcnemar_midp_p": mcnemar_midp(b, c),
        "mcnemar_exact_p": mcnemar_exact(b, c),
        "mcnemar_chisq_cc_p": mcnemar_chisq_cc(b, c),
    }


def compare(
    left_path: Path,
    right_path: Path,
    *,
    arm: str = "always",
    label_left: str = "",
    label_right: str = "",
) -> dict[str, Any]:
    """两份跑批的配对比较（含 **icon / text 分层**）。"""
    left = _load_arm(left_path, arm)
    right = _load_arm(right_path, arm)
    ids = sorted(set(left) & set(right))
    if not ids:
        raise SystemExit("两份跑批没有共同的 sample_id")
    overall = _paired(
        left,
        right,
        ids=ids,
        label_left=label_left or left_path.parent.name,
        label_right=label_right or right_path.parent.name,
    )
    strata: dict[str, Any] = {}
    for ui_type in ("icon", "text"):
        subset = [
            sample_id
            for sample_id in ids
            if str(left[sample_id].get("ui_type", "")) == ui_type
        ]
        if not subset:
            continue
        strata[ui_type] = _paired(
            left,
            right,
            ids=subset,
            label_left=overall["left"],
            label_right=overall["right"],
        )
    return {
        "left_run": str(left_path),
        "right_run": str(right_path),
        "arm": arm,
        "overall": overall,
        "strata": strata,
        "note": (
            "**同批跨跑批**的配对比较：配对消掉「样本难度」的噪声，"
            "**消不掉**「模型重抽」的噪声（网关忽略 temperature）→ "
            "绝对值差要标「混着随机性」；"
            "分层结果必须单列，不许只报总体"
        ),
    }


def main(argv=None) -> int:
    configure_utf8_output()
    parser = argparse.ArgumentParser(
        prog="minicode.eval.paired_runs",
        description="两个跑批的配对比较（零模型调用；施工清单 §六 P2 层）",
    )
    parser.add_argument("--left", required=True, help="左臂的 sspro-report.json")
    parser.add_argument("--right", required=True, help="右臂的 sspro-report.json")
    parser.add_argument("--arm", default="always", help="比哪条臂（默认 always）")
    parser.add_argument("--label-left", default="", help="左臂的口径名")
    parser.add_argument("--label-right", default="", help="右臂的口径名")
    parser.add_argument("--out", default="", help="产物路径（默认写到右臂的目录）")
    args = parser.parse_args(argv)

    left_path = Path(args.left)
    right_path = Path(args.right)
    result = compare(
        left_path,
        right_path,
        arm=args.arm,
        label_left=args.label_left,
        label_right=args.label_right,
    )
    out_path = (
        Path(args.out)
        if args.out
        else right_path.parent / "paired-vs-left.json"
    )
    out_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    overall = result["overall"]
    print(
        f"=== 配对比较（{overall['left']} → {overall['right']}，臂 {args.arm}）==="
    )
    print(
        f"{overall['samples']} 条｜左 {overall['left_hits']}"
        f"（{overall['left_accuracy'] * 100:.2f}%）"
        f"｜右 {overall['right_hits']}（{overall['right_accuracy'] * 100:.2f}%）"
        f"｜Δ {overall['delta_pp']:+.2f} pp（{overall['delta_rows']:+d} 条）"
        f"｜95% CI [{overall['delta_ci95_pp'][0]:+.2f}, "
        f"{overall['delta_ci95_pp'][1]:+.2f}] pp"
        f"｜mid-p p={overall['mcnemar_midp_p']:.4f}"
        f"｜精确二项 p={overall['mcnemar_exact_p']:.4f}"
        f"｜χ²+CC p={overall['mcnemar_chisq_cc_p']:.4f}"
        f"｜CI 跨 0：{'否' if overall['ci_excludes_zero'] else '是'}"
    )
    for name, item in result["strata"].items():
        print(
            f"  {name}（n={item['samples']}）：左 {item['left_hits']}"
            f"｜右 {item['right_hits']}"
            f"｜Δ {item['delta_pp']:+.2f} pp"
            f"｜mid-p p={item['mcnemar_midp_p']:.4f}"
        )
    print(f"记录已写入：{out_path}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())

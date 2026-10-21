"""动作日志的**离线工具**：回放 + 导出 + 统计（工单 M17）。

::

    $env:PYTHONPATH="src"; python -m minicode.audit --log runs/m17/action-records.jsonl `
        --export runs/m17/action-log-export.json

**只读日志、只写导出文件** —— 不碰桌面、不调模型、不花钱。
退出码：回放发现不一致 → ``1``（脚本能据此判成败）。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from .export import export_records, load_export
from .replay import replay_records

__all__ = ["main", "read_log", "read_telemetry"]


def read_log(path: str | Path) -> list[dict[str, Any]]:
    """读一份动作日志。

    两种落盘形状都认（**两种都在本项目里真的产生**）：

    * **JSONL**（每行一条）—— :class:`minicode.action.records.ActionLog` 写的；
    * **JSON 数组** —— 决策层写的 ``action-records.json``。
    """
    text = Path(path).read_text(encoding="utf-8")
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        payload = None
    if isinstance(payload, list):
        return payload
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def read_telemetry(path: str | Path) -> dict[str, Any]:
    """读决策层的**遥测报告**（决策失败只在这里）。

    认两种形状：决策层写的 ``decision-report.json``（里面有 ``telemetry`` 一节）
    与光秃秃的遥测报告本身。
    """
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(payload, dict) and isinstance(payload.get("telemetry"), dict):
        return payload["telemetry"]
    return payload


def _parse_args(argv):
    parser = argparse.ArgumentParser(
        prog="minicode.audit",
        description="动作日志回放与机器可读导出（工单 M17）",
    )
    parser.add_argument("--log", required=True, help="动作日志（JSONL，每行一条）")
    parser.add_argument("--export", default="", help="导出文件（JSON）")
    parser.add_argument(
        "--telemetry",
        default="",
        help="决策层的遥测报告（决策失败只在这里；不给则那一类统计为 0）",
    )
    parser.add_argument("--no-replay", action="store_true", help="只导出，不回放")
    parser.add_argument("--json", action="store_true", help="回放报告按 JSON 打印")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = _parse_args(argv)
    records = read_log(args.log)
    telemetry = read_telemetry(args.telemetry) if args.telemetry else None
    exit_code = 0

    if not args.no_replay:
        report = replay_records(records)
        if args.json:
            print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
        else:
            print(f"回放：{len(report.steps)} 步｜与记录一致：{report.consistent}")
            for problem in report.problems:
                print(f"  不一致：{problem}")
        if not report.consistent:
            exit_code = 1

    if args.export:
        path = export_records(records, args.export, telemetry=telemetry)
        summary = load_export(path)["summary"]
        print(f"导出：{path}")
        print(f"  步数={summary['steps']}｜成功={summary['ok_steps']}｜成功率={summary['success_rate']:.3f}")
        print(f"  三类失败：{summary['failures']}（来源：{summary['failure_sources']}）")
        for name, values in summary["stages_ms"].items():
            print(
                f"  {name} 耗时分布：中位数={values['median']} p90={values['p90']} "
                f"最大={values['max']}"
            )
        print(f"  敏感内容残留：{summary['sensitive_hits']}")

    return exit_code


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())

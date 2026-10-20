"""**端到端评测台**（工单 M14）：跑第一波的六张票并出报告。

```powershell
$env:PYTHONPATH="src"
python -m minicode.e2e --tasks W-1,W-3 --out runs/m14 --ticket M14
```

⚠️ **动真机**（点真桌面、开真浏览器）—— 必须先按 AGENTS.md 拿
``.scratch/desktop.lock`` 占用锁；人类占用真机时本命令**直接拒绝**（退出码非 0）。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from minicode.lock import acquire, release

from .report import build_report, render_markdown
from .runner import TaskRunner
from .wave1 import WAVE1_TASKS, task_by_id

__all__ = ["main"]


def _parse_size(text: str) -> tuple[int, int]:
    parts = text.lower().replace("*", "x").split("x")
    if len(parts) != 2:
        raise argparse.ArgumentTypeError(f"尺寸格式应为 WxH，收到 {text!r}")
    return (int(parts[0]), int(parts[1]))


def _parse_truth(text: str) -> tuple[str, tuple[int, int]]:
    """``W-3.1=2502,1551`` → (步号, 真值)。"""
    key, _, value = text.partition("=")
    x, _, y = value.partition(",")
    return key.strip(), (int(x), int(y))


def _parse_args(argv):
    parser = argparse.ArgumentParser(
        prog="minicode.e2e",
        description="端到端评测台（工单 M14）：Windows 设置 + Chrome 第一波",
    )
    parser.add_argument(
        "--tasks",
        default="",
        help="要跑的工单编号（逗号分隔，如 W-1,W-3）；缺省 = 全部六张",
    )
    parser.add_argument("--out", default="runs/m14", help="报告落盘目录")
    parser.add_argument("--ticket", default="M14", help="本轮票号（写进真机占用锁）")
    parser.add_argument(
        "--gate-mode",
        default="on_demand",
        choices=("on_demand", "always"),
        help="精定位门控：按需触发（生产口径）/ 无条件精定位（测量口径）",
    )
    parser.add_argument(
        "--bypass-admission",
        action="store_true",
        help=(
            "**测量口径**：绕过视觉通道的准入门（只为了量「视觉通道能落多准」；"
            "生产路径仍然只认准入门）"
        ),
    )
    parser.add_argument(
        "--model-input",
        default="2048x1280",
        help="声明「模型实际看到的那张整屏图」的尺寸（WxH）—— 量出来的，不是猜的",
    )
    parser.add_argument(
        "--truth",
        action="append",
        default=[],
        help="覆盖某一步的真值（如 W-3.1=2502,1551）",
    )
    parser.add_argument(
        "--manual",
        default="",
        help=(
            "**人工代跑**的结果（JSON 文件）："
            '{"W-2": {"verdict": "pass", "note": "人工把缩放改成 125%", '
            '"context": {"scale_value": "125%"}}} —— '
            "人代跑完的结果带进来**单独统计**，仍不混进自动通过率"
        ),
    )
    return parser.parse_args(argv)


def _executor_for(task, args):
    """按应用挑执行器。"""
    from .real import ChromeExecutor, WindowsDesktopExecutor

    if task.app == "Chrome":
        return ChromeExecutor(out_dir=args.out)
    return WindowsDesktopExecutor(
        out_dir=args.out,
        gate_mode=args.gate_mode,
        bypass_admission=args.bypass_admission,
        model_input=_parse_size(args.model_input),
        truth_overrides=dict(_parse_truth(item) for item in args.truth),
    )


def _load_manual(path: str) -> dict[str, dict]:
    """读**人工代跑**的结果文件（没有就空）。"""
    if not path:
        return {}
    raw = Path(path)
    if not raw.exists():
        raise SystemExit(f"人工代跑记录文件不存在：{raw}")
    data = json.loads(raw.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise SystemExit(f"人工代跑记录文件格式不对（要按工单编号的对象）：{raw}")
    return {str(key): dict(value) for key, value in data.items()}


def main(argv=None) -> int:
    args = _parse_args(argv)
    wanted = [item.strip() for item in args.tasks.split(",") if item.strip()]
    tasks = [task_by_id(item) for item in wanted] if wanted else list(WAVE1_TASKS)
    manual_results = _load_manual(args.manual)

    holds_lock = acquire(args.ticket)
    try:
        outcomes = []
        for task in tasks:
            print(f"=== {task.id} {task.title} ===")
            executor = _executor_for(task, args)
            try:
                outcome = TaskRunner(executor, manual_results=manual_results).run(task)
            finally:
                close = getattr(executor, "close", None)
                if close is not None:
                    close()
            outcomes.append(outcome)
            print(f"判定：{outcome.verdict}｜{outcome.reason or '（通过）'}")
            for step in outcome.steps:
                data = step.to_dict()
                failures = "、".join(
                    f"{name}={code}"
                    for name, code in data["failures"].items()
                    if code
                ) or "—"
                error = data["landing_error_px"]
                error_text = f"{error:.1f} px" if error is not None else "—"
                print(
                    f"  {data['step_id']} 通道={data['channel'] or '—'} "
                    f"失败类型={data['failure_type'] or '—'} "
                    f"三类失败={failures} "
                    f"期望={'成立' if data['expectation_met'] else ('不成立' if data['expectation_met'] is False else '判不了')} "
                    f"落点偏差={error_text}"
                    f"（真值来源：{data['truth_source'] or '—'}）"
                )
            for clause in outcome.clauses:
                print(f"  {'✓' if clause.ok else '✗'} {clause.id} {clause.description}：{clause.detail}")

        out_dir = Path(args.out)
        out_dir.mkdir(parents=True, exist_ok=True)
        report = build_report(outcomes, title="端到端任务第一波（Windows 设置 + Chrome）")
        (out_dir / "e2e-report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (out_dir / "e2e-report.md").write_text(
            render_markdown(report), encoding="utf-8"
        )
        auto = report["auto"]
        rate = auto["pass_rate"]
        print(
            f"=== 汇总 ===\n通过 {auto['passed']}｜失败 {auto['failed']}｜"
            f"跳过 {auto['skipped']}｜人工代确认 {report['human_confirm']['count']}｜"
            f"自动通过率 "
            f"{('%.0f%%' % (rate * 100)) if rate is not None else '—'}"
            f"（{auto['passed']}/{auto['denominator']}）"
        )
        print(f"报告：{out_dir / 'e2e-report.md'}")
        return 0
    finally:
        if holds_lock:
            release()


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())

"""**全程录制**（护栏 7）与**运行摘要**（护栏 9）。

录制：每步一行 JSONL（``trace.jsonl``）—— 帧哈希 + 控件树摘要（脱敏口径的
候选名/类型/框）+ 动作 + 落点 + 结果 + 通道 + 模型调用次数。它是
:class:`minicode.action.records.TraceSink` 的一个出口，用
:class:`minicode.action.records.MultiTraceSink` 与动作日志并联——
**同一条**追踪记录发给两个出口，不是两份各算的副本。

摘要：一次真机运行结束时写 ``summary.json`` 并打印人读版 ——
模式 / 步数 / 耗时 / 拒绝次数 / 是否触发急停 / 预算账本 / 熔断状态。
跑完一次，两份文件齐了，事后可回放、可审计。
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from minicode.action.records import ActionTrace

__all__ = ["SessionRecorder", "RunSummary", "render_summary"]


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


def _tree_summary(trace: ActionTrace, limit: int = 8) -> list[dict]:
    """控件树摘要（脱敏口径）：候选名 / 类型 / 框，截前 ``limit`` 条。

    ⚠️ 追踪记录里的候选已经是**值不落**的脱敏形态
    （``action.seam._traced_candidate``：密码框占位、其余为空）——
    这里只做形状裁剪，不做二次脱敏（单一出口纪律）。
    """
    rows = []
    for candidate in trace.candidates[:limit]:
        rect = candidate.rect
        rows.append(
            {
                "name": candidate.name,
                "control_type": candidate.control_type,
                "rect": [rect[0], rect[1], rect[2], rect[3]],
                "window_title": candidate.window_title,
            }
        )
    if len(trace.candidates) > limit:
        rows.append({"truncated": len(trace.candidates) - limit})
    return rows


class SessionRecorder:
    """一次真机运行的录制出口（``TraceSink`` 实现）。"""

    def __init__(self, session_directory: str | Path):
        self.directory = Path(session_directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self._path = self.directory / "trace.jsonl"
        self._steps = 0

    @property
    def path(self) -> Path:
        """trace.jsonl 的路径（运行摘要里引用）。"""
        return self._path

    @property
    def steps(self) -> int:
        """已录步数。"""
        return self._steps

    def emit(self, trace: ActionTrace) -> None:
        """一步 = 一行 JSONL（护栏 7 的字段清单见模块 docstring）。"""
        self._steps += 1
        result = trace.result
        row = {
            "type": "step",
            "step": self._steps,
            "ts": _now_iso(),
            "action": (
                {"kind": trace.action.kind.value, "intent": trace.intent}
                if trace.action is not None
                else {"kind": None, "intent": trace.intent}
            ),
            "target": trace.target_description,
            "channel": trace.channel,
            "model_calls": trace.model_calls,
            "landing_point": (
                list(trace.landing_point) if trace.landing_point is not None else None
            ),
            "screenshot_sha256": trace.screenshot_sha256,
            "capture_size": (
                [trace.capture_width, trace.capture_height]
                if trace.capture_width is not None
                else None
            ),
            "tree": _tree_summary(trace),
            "result": {
                "ok": result.ok,
                "error_code": (
                    result.error_code.value if result.error_code is not None else None
                ),
                "error_message": result.error_message,
            },
            "failure_type": trace.failure_type,
            "verification": trace.verification_result,
        }
        with self._path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")

    def write_guard_event(self, event: dict) -> None:
        """护栏事件（拒绝 / 急停）追加进同一份 JSONL（``type`` 区分行类型）。"""
        with self._path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(event, ensure_ascii=False) + "\n")

    def read_rows(self) -> list[dict]:
        """回读全部行（回放/测试用）。"""
        if not self._path.exists():
            return []
        rows = []
        for line in self._path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                rows.append(json.loads(line))
        return rows


@dataclass
class RunSummary:
    """一次真机运行的摘要（护栏 9）。"""

    #: 授权级别（L0~L3）。
    level: str
    #: 是否 dry-run。
    dry_run: bool
    #: 步数（含被拒的）。
    steps: int
    #: 总耗时（秒）。
    elapsed_s: float
    #: 拒绝次数（护栏给出的失败，逐条带错误码）。
    denials: list[dict] = field(default_factory=list)
    #: 是否触发急停。
    estop_triggered: bool = False
    #: 成功 / 失败步数。
    ok_steps: int = 0
    failed_steps: int = 0
    #: 模型调用总次数。
    model_calls: int = 0
    #: 预算账本快照。
    budget: dict | None = None
    #: 熔断状态快照。
    fuse: dict | None = None
    #: 录制文件路径。
    trace_path: str = ""
    #: 策略快照。
    policy: dict | None = None

    def to_dict(self) -> dict:
        return {
            "level": self.level,
            "dry_run": self.dry_run,
            "steps": self.steps,
            "elapsed_s": round(self.elapsed_s, 3),
            "ok_steps": self.ok_steps,
            "failed_steps": self.failed_steps,
            "denials": self.denials,
            "estop_triggered": self.estop_triggered,
            "model_calls": self.model_calls,
            "budget": self.budget,
            "fuse": self.fuse,
            "trace_path": self.trace_path,
            "policy": self.policy,
        }


def render_summary(summary: RunSummary) -> str:
    """人读的运行摘要（护栏 9：每次真机运行打印）。"""
    lines = [
        "========== 真机运行摘要（护栏 9）==========",
        f"模式      ：L{summary.level}" + ("（dry-run）" if summary.dry_run else ""),
        f"步数      ：{summary.steps}（成功 {summary.ok_steps} / 失败 {summary.failed_steps}）",
        f"耗时      ：{summary.elapsed_s:.1f}s",
        f"模型调用  ：{summary.model_calls} 次",
        f"拒绝次数  ：{len(summary.denials)}",
        f"急停触发  ：{'是' if summary.estop_triggered else '否'}",
    ]
    if summary.budget is not None:
        lines.append(
            f"预算      ：{summary.budget['steps']}/{summary.budget['max_steps']} 步，"
            f"{summary.budget['model_calls']}/{summary.budget['max_model_calls']} 次模型调用"
        )
    if summary.fuse is not None and summary.fuse.get("consecutive_failures"):
        lines.append(f"熔断      ：{summary.fuse}")
    if summary.trace_path:
        lines.append(f"录制      ：{summary.trace_path}")
    lines.append("========================================")
    return "\n".join(lines)


def write_summary(session_directory: str | Path, summary: RunSummary) -> Path:
    """摘要落盘（``summary.json``）并打印人读版。"""
    directory = Path(session_directory)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "summary.json"
    path.write_text(
        json.dumps(summary.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(render_summary(summary), file=sys.stderr)
    return path

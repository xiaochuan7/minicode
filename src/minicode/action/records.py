"""动作记录与追踪记录。

两条红线：

* **只落哈希**：一次动作只产出截图摘要（SHA-256 十六进制），**不落图像文件**。
* **恰好一条**：一次动作恰好一条机器可解析的记录。

追踪记录（:class:`ActionTrace`）走可注入的旁路出口，不进主返回类型——
它是观测数据而不是控制流数据，这样以后换定位后端时上层调用方不用改。

词汇对应
--------
* **动作日志**（`CONTEXT.md`）：落盘的动作记录集合，本模块的 :class:`ActionLog`。
* **动作记录**（工单口径）：一次动作的那一条可解析记录，本模块的 :class:`ActionRecord`；
  工单要求「每次动作恰好一条动作记录」。
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from .ports import ControlCandidate
from .protocol import PROTOCOL_VERSION, Action, ActionResult

__all__ = [
    "ActionTrace",
    "ActionRecord",
    "ActionLog",
    "TraceSink",
    "ListTraceSink",
    "build_record",
    "POINT_SPACE",
    "FAILURE_FIELDS",
]

#: 落点所在的坐标空间。本项目唯一真值空间。
POINT_SPACE = "physical-surface"

#: 三类失败字段的固定名字。
FAILURE_FIELDS = ("locate", "decision", "execute")


@dataclass(frozen=True)
class ActionTrace:
    """一次动作的追踪记录（观测数据）。"""

    action: Action | None = None
    intent: str = ""
    target_description: str = ""
    candidates: tuple[ControlCandidate, ...] = ()
    selected: ControlCandidate | None = None
    rejected_candidates: tuple[ControlCandidate, ...] = ()
    landing_point: tuple[int, int] | None = None
    capture_width: int | None = None
    capture_height: int | None = None
    screenshot_sha256: str | None = None
    #: 实际发送尺寸（本单没有模型参与预处理，因此与抓图尺寸相同）。
    sent_width: int | None = None
    sent_height: int | None = None
    #: 图像来源（捕获源 | 后端）。
    image_source: str | None = None
    skipped_capture_sources: tuple[str, ...] = ()
    #: 三段耗时（毫秒）：定位 / 决策 / 执行+校验。
    locate_ms: float = 0.0
    decision_ms: float = 0.0
    execute_verify_ms: float = 0.0
    #: 三类失败字段：定位失败 / 决策失败 / 执行失败。
    failures: Mapping[str, str | None] = field(
        default_factory=lambda: {name: None for name in FAILURE_FIELDS}
    )
    result: ActionResult = field(default_factory=lambda: ActionResult(ok=True))


@runtime_checkable
class TraceSink(Protocol):
    """追踪记录的旁路出口。"""

    def emit(self, trace: ActionTrace) -> None:
        ...


class ListTraceSink:
    """把追踪记录收进列表（单测与命令行用）。"""

    def __init__(self) -> None:
        self.traces: list[ActionTrace] = []

    def emit(self, trace: ActionTrace) -> None:
        self.traces.append(trace)


@dataclass(frozen=True)
class ActionRecord:
    """一次动作的机器可解析记录。"""

    protocol_version: str
    timestamp: str
    action: str
    intent: str
    target_name: str | None = None
    target_rect: tuple[int, int, int, int] | None = None
    target_control_type: str | None = None
    target_window_handle: int | None = None
    point_x: int | None = None
    point_y: int | None = None
    point_space: str = POINT_SPACE
    capture_width: int | None = None
    capture_height: int | None = None
    sent_width: int | None = None
    sent_height: int | None = None
    image_source: str | None = None
    screenshot_sha256: str | None = None
    ok: bool = False
    error_code: str | None = None
    error_message: str | None = None
    #: 注入后回读的光标位置（物理表面空间）。哨兵值 (−32000,−32000) 会被判执行失败。
    cursor_readback: tuple[int, int] | None = None
    #: 光标回读与落点的偏差（物理像素）。验收要求它是 0。
    cursor_delta_px: int | None = None
    #: 唯一性拒绝时回报的候选集（拒绝执行时没有选中项）。
    rejected_candidates: tuple[dict[str, Any], ...] = ()
    locate_ms: float = 0.0
    decision_ms: float = 0.0
    execute_verify_ms: float = 0.0
    #: 动作自身的实测时序（毫秒）：pre_move / hold / total 等。
    action_timing: Mapping[str, Any] | None = None
    failure_locate: str | None = None
    failure_decision: str | None = None
    failure_execute: str | None = None
    notes: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "protocol_version": self.protocol_version,
            "timestamp": self.timestamp,
            "action": self.action,
            "intent": self.intent,
            "target": {
                "name": self.target_name,
                "rect": list(self.target_rect) if self.target_rect else None,
                "control_type": self.target_control_type,
                "window_handle": self.target_window_handle,
            },
            "point": (
                {"x": self.point_x, "y": self.point_y, "space": self.point_space}
                if self.point_x is not None and self.point_y is not None
                else None
            ),
            "capture": {"width": self.capture_width, "height": self.capture_height},
            "sent_size": {"width": self.sent_width, "height": self.sent_height},
            "image_source": self.image_source,
            "screenshot_sha256": self.screenshot_sha256,
            "result": {
                "ok": self.ok,
                "error_code": self.error_code,
                "error_message": self.error_message,
                "cursor_readback": list(self.cursor_readback) if self.cursor_readback else None,
                "cursor_delta_px": self.cursor_delta_px,
            },
            "rejected_candidates": [dict(item) for item in self.rejected_candidates],
            "timings_ms": {
                "locate": self.locate_ms,
                "decision": self.decision_ms,
                "execute_verify": self.execute_verify_ms,
            },
            "action_timing": dict(self.action_timing) if self.action_timing else None,
            "failures": {
                "locate": self.failure_locate,
                "decision": self.failure_decision,
                "execute": self.failure_execute,
            },
            "notes": list(self.notes),
        }


def build_record(trace: ActionTrace, *, timestamp: str | None = None) -> ActionRecord:
    """把追踪记录转成落盘的动作记录。"""
    selected = trace.selected
    result = trace.result
    failures = trace.failures
    return ActionRecord(
        protocol_version=PROTOCOL_VERSION,
        timestamp=timestamp or datetime.now().astimezone().isoformat(timespec="seconds"),
        action=trace.action.kind.value if trace.action else "",
        intent=trace.intent,
        target_name=selected.name if selected else None,
        target_rect=selected.rect if selected else None,
        target_control_type=selected.control_type if selected else None,
        target_window_handle=selected.window_handle if selected else None,
        point_x=trace.landing_point[0] if trace.landing_point else None,
        point_y=trace.landing_point[1] if trace.landing_point else None,
        capture_width=trace.capture_width,
        capture_height=trace.capture_height,
        sent_width=trace.sent_width,
        sent_height=trace.sent_height,
        image_source=trace.image_source,
        screenshot_sha256=trace.screenshot_sha256,
        ok=result.ok,
        error_code=result.error_code.value if result.error_code else None,
        error_message=result.error_message,
        cursor_readback=result.cursor_readback,
        cursor_delta_px=result.cursor_delta_px,
        rejected_candidates=tuple(
            {
                "name": candidate.name,
                "control_type": candidate.control_type,
                "rect": list(candidate.rect),
            }
            for candidate in trace.rejected_candidates
        ),
        locate_ms=trace.locate_ms,
        decision_ms=trace.decision_ms,
        execute_verify_ms=trace.execute_verify_ms,
        action_timing=dict(result.timing) if result.timing else None,
        failure_locate=failures.get("locate"),
        failure_decision=failures.get("decision"),
        failure_execute=failures.get("execute"),
        notes=tuple(result.notes),
    )


class ActionLog:
    """动作日志：每次动作追加一行 JSON。"""

    def __init__(self, directory: str | Path):
        self.directory = Path(directory)
        self.path = self.directory / "action-records.jsonl"

    def append(self, record: ActionRecord) -> Path:
        self.directory.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record.to_dict(), ensure_ascii=False) + "\n")
        return self.path

    def records(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        return [
            json.loads(line)
            for line in self.path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

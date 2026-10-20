"""模型请求日志：**每发一次请求写一条**。

字段（工单 M1 要求的那几项一个不少）：档位、模型、端点、HTTP 状态、耗时、
输入 token、输出 token、思考 token 数。

「档位字段值是五档之一且非空」是**可判定**的：扫全部日志，
未显式设档位的调用数必须是 0（见 :func:`count_unset_effort`）。
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Protocol

from .effort import EFFORT_VALUES, Effort

__all__ = [
    "RequestLog",
    "RequestLogSink",
    "InMemoryRequestLog",
    "JsonlRequestLog",
    "build_request_log",
    "count_unset_effort",
]


@dataclass(frozen=True)
class RequestLog:
    """一条模型请求的日志。"""

    effort: str
    model: str
    endpoint: str
    http_status: int | None
    latency_ms: float
    input_tokens: int | None = None
    output_tokens: int | None = None
    thinking_tokens: int | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class RequestLogSink(Protocol):
    """请求日志的出口（旁路 sink，不进主返回类型）。"""

    def write(self, record: RequestLog) -> None:
        """写一条请求日志。"""
        ...


class InMemoryRequestLog:
    """内存实现：单测与回归用。"""

    def __init__(self) -> None:
        self.records: list[RequestLog] = []

    def write(self, record: RequestLog) -> None:
        self.records.append(record)


class JsonlRequestLog:
    """落盘实现：每行一条 JSON。"""

    def __init__(self, path: Path | str):
        self.path = Path(path)

    def write(self, record: RequestLog) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record.to_dict(), ensure_ascii=False) + "\n")


def build_request_log(
    *,
    effort: Effort | str,
    model: str,
    endpoint: str,
    http_status: int | None,
    latency_ms: float,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    thinking_tokens: int | None = None,
    error: str | None = None,
) -> RequestLog:
    """构造一条请求日志（纯函数）。"""
    return RequestLog(
        effort=str(effort),
        model=model,
        endpoint=endpoint,
        http_status=http_status,
        latency_ms=latency_ms,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        thinking_tokens=thinking_tokens,
        error=error,
    )


def count_unset_effort(records: Iterable[RequestLog | Mapping[str, Any]]) -> int:
    """统计「未显式设档位」的调用数。

    判据：档位字段为空，或取值不在五档之内。工单要求这个数**恒为 0**。
    """
    count = 0
    for record in records:
        effort = record["effort"] if isinstance(record, Mapping) else record.effort
        if not effort or effort not in EFFORT_VALUES:
            count += 1
    return count

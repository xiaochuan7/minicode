"""**机器可读导出**与离线统计（工单 M17 AC 3）。

导出格式是一个 JSON 文件：``records``（脱敏后的动作记录）+ ``summary``
（从这些记录算出来的统计）。**脚本解析它不需要任何本项目的代码** ——
标准库的 ``json`` 就够；统计也在文件里，不用人工看。

统计三样东西：

* **成功率** —— 成功步数 ÷ 总步数；
* **三类失败分布** —— 定位失败 / 决策失败 / 执行失败各自的次数与占比；
* **三段耗时分布** —— 定位 / 决策 / 执行+校验三段各自的分布
  （中位数 / p90 / 最大值 —— **分布，不报单点**）。

⚠️ **分位数与中位数的算法只有一份**：复用 ``minicode.ground.stats`` 的那个
（它算的就是「一组样本 → 分布摘要」，与内容无关；中位数取偶数样本中间两值的
平均，p90 取最近秩）。

⚠️ **决策失败不在动作记录里** —— 动作记录里只有「定位失败」与「执行失败」
两处（决策层那一步压根没进动作层）。决策失败由**决策层单独报**
（``verification.telemetry`` 的遥测报告）→ 给了 ``telemetry`` 就并进来，
并在 ``failure_sources`` 里写明每一类**来自哪儿**。
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from minicode.action.records import FAILURE_FIELDS
from minicode.ground.stats import summarize_errors as summarize_samples
from minicode.redact.log import sanitize_record, scan_record
from minicode.verification.telemetry import STAGE_FIELDS

__all__ = [
    "EXPORT_FORMAT",
    "LogSummary",
    "summarize_records",
    "export_records",
    "load_export",
]

#: 导出文件的格式标识（**只增不改**：改格式就换标识）。
EXPORT_FORMAT = "minicode.action-log/1"

#: 三类失败各自**来自哪儿**。
_FAILURE_SOURCES = {
    "locate": "action-records",
    "decision": "telemetry",
    "execute": "action-records",
}


@dataclass(frozen=True)
class LogSummary:
    """一份动作日志的统计摘要。"""

    #: 统计了多少步。
    steps: int = 0
    #: 其中成功了几步。
    ok_steps: int = 0
    #: **成功率**（成功步数 ÷ 总步数）。
    success_rate: float = 0.0
    #: 三类失败各自的**次数**。
    failures: Mapping[str, int] = field(default_factory=dict)
    #: 三类失败各自的**占比**（占全部失败的比例）。
    failure_share: Mapping[str, float] = field(default_factory=dict)
    #: 三类失败各自的**发生率**（占全部步数的比例）。
    failure_rate: Mapping[str, float] = field(default_factory=dict)
    #: 三类失败各自**来自哪儿**（``action-records`` / ``telemetry``）。
    failure_sources: Mapping[str, str] = field(default_factory=dict)
    #: 错误码各自的次数。
    error_codes: Mapping[str, int] = field(default_factory=dict)
    #: 三段耗时各自的**分布**（count / min / median / p90 / max / total）。
    stages_ms: Mapping[str, Mapping[str, float]] = field(default_factory=dict)
    #: 导出文件里**残留**的敏感内容条数（**必须为 0**）。
    sensitive_hits: int = 0

    def to_dict(self) -> dict[str, Any]:
        """落盘形状。"""
        return {
            "steps": self.steps,
            "ok_steps": self.ok_steps,
            "success_rate": self.success_rate,
            "failures": dict(self.failures),
            "failure_share": dict(self.failure_share),
            "failure_rate": dict(self.failure_rate),
            "failure_sources": dict(self.failure_sources),
            "error_codes": dict(self.error_codes),
            "stages_ms": {name: dict(values) for name, values in self.stages_ms.items()},
            "sensitive_hits": self.sensitive_hits,
        }


def summarize_records(
    records: Sequence[Mapping[str, Any]],
    *,
    telemetry: Mapping[str, Any] | None = None,
) -> LogSummary:
    """把一批动作记录算成统计摘要。

    ``telemetry``
        决策层的遥测报告（``verification.telemetry`` 的落盘形状）。
        **决策失败不在动作记录里**（决策层那一步压根没进动作层）→
        给了它，决策失败的次数才统计得全。
    """
    records = list(records)
    steps = len(records)
    ok_steps = sum(1 for record in records if (record.get("result") or {}).get("ok"))

    failures = {name: 0 for name in FAILURE_FIELDS}
    error_codes: dict[str, int] = {}
    for record in records:
        recorded = record.get("failures") or {}
        for name in FAILURE_FIELDS:
            code = recorded.get(name)
            if not code:
                continue
            failures[name] += 1
            error_codes[str(code)] = error_codes.get(str(code), 0) + 1

    sources = {name: _FAILURE_SOURCES[name] for name in FAILURE_FIELDS}
    if telemetry:
        reported = telemetry.get("failures") or {}
        failures["decision"] = int(reported.get("decision") or 0)
    else:
        sources["decision"] = "unavailable"

    total_failures = sum(failures.values())
    stages: dict[str, dict[str, float]] = {}
    for stage in STAGE_FIELDS:
        values = [
            float((record.get("timings_ms") or {}).get(stage) or 0.0) for record in records
        ]
        stages[stage] = _distribution(values)

    sensitive = sum(len(scan_record(record)) for record in records)

    return LogSummary(
        steps=steps,
        ok_steps=ok_steps,
        success_rate=(ok_steps / steps) if steps else 0.0,
        failures=failures,
        failure_share={
            name: (count / total_failures if total_failures else 0.0)
            for name, count in failures.items()
        },
        failure_rate={
            name: (count / steps if steps else 0.0) for name, count in failures.items()
        },
        failure_sources=sources,
        error_codes=error_codes,
        stages_ms=stages,
        sensitive_hits=sensitive,
    )


def export_records(
    records: Sequence[Mapping[str, Any]],
    path: str | Path,
    *,
    telemetry: Mapping[str, Any] | None = None,
) -> Path:
    """把一批动作记录导出成一个机器可读的 JSON 文件。

    **导出前脱敏**（工单 M17 AC 5）：导出文件里也不许有密钥形态的明文。
    """
    sanitized = [sanitize_record(record) for record in records]
    payload = {
        "format": EXPORT_FORMAT,
        "exported_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "record_count": len(sanitized),
        "records": sanitized,
        "summary": summarize_records(sanitized, telemetry=telemetry).to_dict(),
    }
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return target


def load_export(path: str | Path) -> dict[str, Any]:
    """读回一个导出文件（格式标识不对就报错 —— 不猜）。"""
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if payload.get("format") != EXPORT_FORMAT:
        raise ValueError(
            f"不是本项目的动作日志导出（format={payload.get('format')!r}，"
            f"期望 {EXPORT_FORMAT!r}）"
        )
    return payload


def _distribution(values: Sequence[float]) -> dict[str, float]:
    """一组样本 → 分布摘要（**分位数算法只有一份**）。"""
    if not values:
        return {"count": 0, "min": 0.0, "median": 0.0, "p90": 0.0, "max": 0.0, "total": 0.0}
    summary = summarize_samples(values)
    return {
        "count": summary.count,
        "min": summary.minimum,
        "median": summary.median,
        "p90": summary.p90,
        "max": summary.maximum,
        "total": float(sum(values)),
    }

"""标定记录：每次标定落一条，可离线读回（工单 M5 AC 8 / AC 9）。

记录里有什么：标定时间、探针图哈希、探针尺寸、判定标度、标定容差、
各候选标度与最大误差、等价标度列表、**标度判定**与**落点等价**两个字段、
模型标识、调用路径，外加模型的原始回答与探针真值 ——
有了后两者就能复盘「当时按哪个量纲算的」。

**只落探针图哈希，不落原图**（与动作记录同一条红线）。

存储按 **(模型, 调用路径)** 分开：文本调用与图像调用各一条，
**任一路径没记录或标定失败时，另一条路径的标度不许借给它**
（架构决策备忘 §2.6 C-04）。
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from minicode.coord.coordinates import ModelScale

from .admission import visual_channel_available
from .calibrate import CalibrationOutcome

__all__ = ["CalibrationRecord", "CalibrationLog", "CalibrationStore"]


@dataclass(frozen=True)
class CalibrationRecord:
    """一条标定记录。"""

    timestamp: str
    probe_sha256: str
    probe_width: int
    probe_height: int
    model_input_width: int
    model_input_height: int
    detected_scale: str
    scale_verdict: str
    landing_equivalence: str
    equivalent_scales: tuple[str, ...]
    candidates: tuple[dict[str, Any], ...]
    divergences_px: dict[str, float]
    calibration_tolerance_px: float
    out_of_bounds_tolerance_px: float
    calibration_ok: bool
    model_id: str
    call_path: str
    raw_points: tuple[tuple[str, float, float], ...]
    targets: tuple[tuple[str, float, float], ...]

    @classmethod
    def from_outcome(
        cls, outcome: CalibrationOutcome, *, timestamp: str | None = None
    ) -> CalibrationRecord:
        """把标定结论转成一条可落盘的记录。"""
        return cls(
            timestamp=timestamp or datetime.now().astimezone().isoformat(timespec="seconds"),
            probe_sha256=outcome.probe_digest,
            probe_width=outcome.probe_size[0],
            probe_height=outcome.probe_size[1],
            model_input_width=outcome.model_input_size[0],
            model_input_height=outcome.model_input_size[1],
            detected_scale=outcome.detected_scale.value,
            scale_verdict=outcome.scale_verdict,
            landing_equivalence=outcome.landing_equivalence,
            equivalent_scales=tuple(scale.value for scale in outcome.equivalent_scales),
            candidates=tuple(
                {
                    "scale": candidate.scale.value,
                    "max_error_px": candidate.max_error_px,
                    "mean_error_px": candidate.mean_error_px,
                    "out_of_bounds": candidate.out_of_bounds,
                }
                for candidate in outcome.candidates
            ),
            divergences_px={
                scale.value: value for scale, value in outcome.divergences_px.items()
            },
            calibration_tolerance_px=outcome.calibration_tolerance_px,
            out_of_bounds_tolerance_px=outcome.out_of_bounds_tolerance_px,
            calibration_ok=outcome.calibration_ok,
            model_id=outcome.model_id,
            call_path=outcome.call_path,
            raw_points=outcome.raw_points,
            targets=outcome.targets,
        )

    def to_dict(self) -> dict[str, Any]:
        """落盘形状。"""
        return {
            "timestamp": self.timestamp,
            "probe_sha256": self.probe_sha256,
            "probe_width": self.probe_width,
            "probe_height": self.probe_height,
            "model_input_width": self.model_input_width,
            "model_input_height": self.model_input_height,
            "detected_scale": self.detected_scale,
            "scale_verdict": self.scale_verdict,
            "landing_equivalence": self.landing_equivalence,
            "equivalent_scales": list(self.equivalent_scales),
            "candidates": [dict(candidate) for candidate in self.candidates],
            "divergences_px": dict(self.divergences_px),
            "calibration_tolerance_px": self.calibration_tolerance_px,
            "out_of_bounds_tolerance_px": self.out_of_bounds_tolerance_px,
            "calibration_ok": self.calibration_ok,
            "model_id": self.model_id,
            "call_path": self.call_path,
            "raw_points": [list(point) for point in self.raw_points],
            "targets": [list(target) for target in self.targets],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CalibrationRecord:
        """从落盘形状读回一条记录（**可离线读回**）。"""
        return cls(
            timestamp=str(data["timestamp"]),
            probe_sha256=str(data["probe_sha256"]),
            probe_width=int(data["probe_width"]),
            probe_height=int(data["probe_height"]),
            model_input_width=int(data["model_input_width"]),
            model_input_height=int(data["model_input_height"]),
            detected_scale=str(data["detected_scale"]),
            scale_verdict=str(data["scale_verdict"]),
            landing_equivalence=str(data["landing_equivalence"]),
            equivalent_scales=tuple(str(name) for name in data["equivalent_scales"]),
            candidates=tuple(dict(candidate) for candidate in data["candidates"]),
            divergences_px={
                str(name): float(value) for name, value in data["divergences_px"].items()
            },
            calibration_tolerance_px=float(data["calibration_tolerance_px"]),
            out_of_bounds_tolerance_px=float(data["out_of_bounds_tolerance_px"]),
            calibration_ok=bool(data["calibration_ok"]),
            model_id=str(data["model_id"]),
            call_path=str(data["call_path"]),
            raw_points=tuple(
                (str(name), float(x), float(y)) for name, x, y in data["raw_points"]
            ),
            targets=tuple(
                (str(name), float(x), float(y)) for name, x, y in data["targets"]
            ),
        )


class CalibrationLog:
    """标定记录的落盘出口（JSONL，一行一条）。"""

    def __init__(self, directory: str | Path):
        self.directory = Path(directory)
        self.path = self.directory / "calibration-records.jsonl"

    def append(self, record: CalibrationRecord) -> Path:
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


class CalibrationStore:
    """按 **(模型, 调用路径)** 取标定结果。

    **不跨路径借用**：文本调用没标定时，图像调用的标度不会顶上来
    （架构决策备忘 §2.6 C-04：两条路径各标定一次）。

    同一路径有多条记录时取**最新**那条（落盘是追加的，后写的更新）。
    """

    def __init__(self, records: Sequence[CalibrationRecord] = ()):
        self._records = tuple(records)

    @classmethod
    def from_log(cls, log: CalibrationLog) -> CalibrationStore:
        """从落盘的标定日志读回一个存储（**可离线读回**）。"""
        return cls([CalibrationRecord.from_dict(row) for row in log.records()])

    def lookup(self, model_id: str, call_path: str) -> CalibrationRecord | None:
        """取某条路径的标定记录（取不到就是 ``None``；多条取最新）。"""
        found: CalibrationRecord | None = None
        for record in self._records:
            if record.model_id == model_id and record.call_path == call_path:
                found = record
        return found

    def scale_for(self, model_id: str, call_path: str) -> ModelScale | None:
        """取某条路径的判定标度（**没有记录就没有标度**）。

        **硬闸门在这里也生效**：标定失败、或候选族里有落点分歧 > 标定容差的
        标度时，一律给不出标度 —— 免得有人绕过准入判定直接拿一个
        「会让落点挪出容差」的标度去换算（准入规则只有一处：
        :func:`minicode.calib.admission.visual_channel_available`）。
        """
        record = self.lookup(model_id, call_path)
        if record is None:
            return None
        if not visual_channel_available(
            calibration_ok=record.calibration_ok,
            equivalent_scales=record.equivalent_scales,
            divergences_px=record.divergences_px,
            tolerance_px=record.calibration_tolerance_px,
        ):
            return None
        return ModelScale(record.detected_scale)

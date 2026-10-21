"""**标定探针**（工单 M5）：运行时判定模型的量纲。

模型输出的坐标用哪套量纲（0~1 / 0~999 / 0~1000 / 截图像素绝对 /
模型输入像素绝对）**不能靠文档、也不能靠猜** —— 官方文档自身前后矛盾，
所以用一张已知几何的合成探针图在运行时反推。判出来的**标度**从此是
坐标换算链路的**唯一真值来源**（名义分母的定义表在冻结原型的标度枚举里，
本包不另写一份）。

模块地图：

* :mod:`minicode.calib.probe` —— 探针几何与探针图（纯标准库）；
* :mod:`minicode.calib.prompt` —— 两条调用路径的提示词与回答解析；
* :mod:`minicode.calib.calibrate` —— 判定标度、落点等价、候选误差、K 轮命中率；
* :mod:`minicode.calib.record` —— 落盘记录与按路径取用的存储；
* :mod:`minicode.calib.admission` —— 视觉通道准入判定（硬闸门）；
* :mod:`minicode.calib.tolerance` —— 可分辨容差 / 越界容差 / 落点等价容差 /
  命中半径 / 轮数与命中率下限。
"""

from __future__ import annotations

from .admission import (
    CalibrationAdmission,
    visual_channel_available,
    visual_channel_reason,
)
from .calibrate import (
    EQUIVALENCE_DIVERGENT,
    EQUIVALENCE_EQUIVALENT,
    SCALE_VERDICT_MULTI,
    SCALE_VERDICT_UNIQUE,
    CalibrationOutcome,
    CalibrationProbe,
    CandidateEvaluation,
    estimate_model_input_from_tokens,
)
from .probe import (
    ProbeSpec,
    ProbeTarget,
    build_probe_frame,
    default_probe_spec,
    probe_digest,
    probe_hit_radius,
    probe_png,
)
from .prompt import CalibrationParseError, image_prompt, parse_points, text_prompt
from .record import CalibrationLog, CalibrationRecord, CalibrationStore
from .tolerance import (
    CALIBRATION_MIN_HIT_RATE,
    CALIBRATION_ROUNDS,
    CALIBRATION_TOLERANCE_PX,
    DISCRIMINABILITY_SAFETY_FACTOR,
    OUT_OF_BOUNDS_TOLERANCE_PX,
    PROBE_HIT_RADIUS_PX,
    SCALE_EQUIVALENCE_TOLERANCE_PX,
)

__all__ = [
    "CALIBRATION_TOLERANCE_PX",
    "OUT_OF_BOUNDS_TOLERANCE_PX",
    "DISCRIMINABILITY_SAFETY_FACTOR",
    "SCALE_EQUIVALENCE_TOLERANCE_PX",
    "PROBE_HIT_RADIUS_PX",
    "CALIBRATION_ROUNDS",
    "CALIBRATION_MIN_HIT_RATE",
    "CalibrationAdmission",
    "CalibrationLog",
    "CalibrationOutcome",
    "CalibrationParseError",
    "CalibrationProbe",
    "CalibrationRecord",
    "CalibrationStore",
    "CandidateEvaluation",
    "EQUIVALENCE_DIVERGENT",
    "EQUIVALENCE_EQUIVALENT",
    "SCALE_VERDICT_MULTI",
    "SCALE_VERDICT_UNIQUE",
    "ProbeSpec",
    "ProbeTarget",
    "build_probe_frame",
    "default_probe_spec",
    "estimate_model_input_from_tokens",
    "image_prompt",
    "parse_points",
    "probe_digest",
    "probe_hit_radius",
    "probe_png",
    "text_prompt",
    "visual_channel_available",
    "visual_channel_reason",
]

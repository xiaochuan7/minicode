"""**两级 grounding**（视觉通道 L2，工单 M6）。

一次定位分两级：**整屏粗定位（L0）→ 裁剪 → crop 内精定位（L1）**。
**最终落点只在 crop 尺度上产生**；整屏坐标只用来「选哪块区域」，永远不直接点击。

模块地图：

* :mod:`minicode.ground.channel` —— 通道本体（粗定位 → 门控 → 裁剪计划 → 精定位 → 回落）；
* :mod:`minicode.ground.crop` —— 从**原始物理分辨率帧**上 1:1 裁一块；
* :mod:`minicode.ground.gate` —— **精定位门控**（四个触发条件 + 运行时参数与取值来源）；
* :mod:`minicode.ground.geometry` —— 帧元数据、裁剪计划字段、往返自检；
* :mod:`minicode.ground.prompt` —— 提示词与回答解析（粗定位**出框** / 精定位 / 编号清单）；
* :mod:`minicode.ground.zoom` —— **放大**动作（回源重抓 + 模型报**图内坐标**）。
* :mod:`minicode.ground.stats` —— 落点误差的**分布**统计（验收判据用分布，不用单点）。

坐标换算链路的唯一真值来源仍是冻结原型 :mod:`minicode.coord`；
本包只负责**流程**（取景、两级、回落、回显），不另写一套换算。
"""

from __future__ import annotations

from minicode.action.ports import (
    GroundingEcho,
    VisualLocateRequest,
    VisualLocateResult,
    VisualLocator,
    ZoomLocator,
    ZoomRequest,
    ZoomResult,
)

from .channel import TwoStageGrounder, default_crop_size
from .crop import crop_frame
from .gate import (
    FallbackCause,
    GateCondition,
    GateDecision,
    GateInputs,
    GateMode,
    GateParams,
    GateReason,
    GateReport,
    GateSample,
    build_gate_report,
    default_gate_params,
    evaluate_gate,
)
from .geometry import (
    crop_plan_fields,
    crop_roundtrip_error,
    full_screen_geometry,
    surface_rect,
)
from .prompt import (
    GroundingParseError,
    coarse_prompt,
    parse_coarse_target,
    parse_point,
    refine_prompt,
)
from .stats import ErrorDistribution, not_worse_than, summarize_errors
from .zoom import zoom_locate, zoom_region_prompt
from .zoom import zoom_region_prompt as zoom_prompt

__all__ = [
    "ErrorDistribution",
    "FallbackCause",
    "GateCondition",
    "GateDecision",
    "GateInputs",
    "GateMode",
    "GateParams",
    "GateReason",
    "GateReport",
    "GateSample",
    "GroundingEcho",
    "GroundingParseError",
    "TwoStageGrounder",
    "VisualLocateRequest",
    "VisualLocateResult",
    "VisualLocator",
    "ZoomLocator",
    "ZoomRequest",
    "ZoomResult",
    "build_gate_report",
    "coarse_prompt",
    "crop_frame",
    "crop_plan_fields",
    "crop_roundtrip_error",
    "default_crop_size",
    "default_gate_params",
    "evaluate_gate",
    "full_screen_geometry",
    "not_worse_than",
    "parse_coarse_target",
    "parse_point",
    "refine_prompt",
    "summarize_errors",
    "surface_rect",
    "zoom_locate",
    "zoom_prompt",
    "zoom_region_prompt",
]

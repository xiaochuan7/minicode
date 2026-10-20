"""**放大（zoom）** —— 动作协议里的一个动作（工单 M6 AC 6）。

与裁剪**不是一回事**（契约不同）：

* **放大**：指定区域**回源重抓**（重新抓一块物理画面）后送模型看清楚，
  模型回答的仍是**整屏坐标** —— 系统**不加任何裁剪原点偏移**；
* **裁剪**：两级定位**内部**的取景，从已经抓到的那一帧上 1:1 取窗口，
  模型报 **crop 内坐标**，由系统加裁剪原点偏移映射回全屏。

混用这两套契约会让落点偏一整个裁剪窗口，而且是**静默偏移** ——
所以本模块**不产生裁剪计划**（``ZoomResult.crop_plan`` 恒为 ``None``）。

放大**不注入任何输入**：它是一次观察（动作协议里的一个动作），
产出的是模型给的整屏坐标。
"""

from __future__ import annotations

from minicode.action.errors import ErrorCode
from minicode.action.image import encode_frame_base64
from minicode.action.ports import GroundingEcho, ZoomRequest, ZoomResult
from minicode.coord.coordinates import (
    DEFAULT_OUT_OF_BOUNDS_TOLERANCE_PX,
    CoordinateAdapter,
    CoordinateError,
    ModelScale,
)
from minicode.model.effort import Effort
from minicode.model.ports import ModelCaller

from .geometry import full_screen_geometry
from .prompt import GroundingParseError, parse_point, zoom_prompt

__all__ = ["zoom_locate"]

#: 放大这一级的名字（回显与追踪记录里用）。
STAGE_ZOOM = "zoom"


def zoom_locate(
    model: ModelCaller,
    request: ZoomRequest,
    *,
    scale: ModelScale,
    model_input_size: tuple[int, int],
    effort: Effort | str = Effort.LOW,
    max_tokens: int = 2000,
    out_of_bounds_tolerance_px: float = DEFAULT_OUT_OF_BOUNDS_TOLERANCE_PX,
) -> ZoomResult:
    """放大一块区域，问模型目标在**整屏**上的位置。

    参数
    ----
    scale
        **标定判定的标度**（换算链路的分母来源）。**必填**。
    model_input_size
        **模型实际看到的那张整屏图**的尺寸（来自标定记录）。
    """
    adapter = CoordinateAdapter(
        scale, out_of_bounds_tolerance_px=out_of_bounds_tolerance_px
    )
    geometry = full_screen_geometry(
        full_screen_size=request.full_screen_size,
        model_input_size=model_input_size,
        surface=request.surface,
    )
    # 区域左上角在**模型的整屏坐标系**里的位置（提示词要告诉模型这块区域在整屏哪儿）。
    origin_model = adapter.from_screen(request.region[0], request.region[1], geometry)
    image_base64 = encode_frame_base64(request.region_frame)
    if image_base64 is None:
        return ZoomResult(
            error_code=ErrorCode.LOCATE_FAILED,
            error_message="回源重抓的画面编不成 PNG，送不出图",
            scale=scale.value,
        )
    prompt = zoom_prompt(
        request.target_description, origin_in_model_space=origin_model
    )
    reply = model.complete(
        prompt,
        effort=effort,
        max_tokens=max_tokens,
        image_base64=image_base64,
    )
    try:
        raw = parse_point(reply.text)
    except GroundingParseError as exc:
        return ZoomResult(
            error_code=ErrorCode.LOCATE_FAILED,
            error_message=f"放大的模型回答取不出坐标：{exc}",
            scale=scale.value,
        )
    echo = GroundingEcho(
        stage=STAGE_ZOOM,
        raw_x=raw[0],
        raw_y=raw[1],
        sent_width=request.region_frame.width,
        sent_height=request.region_frame.height,
        image_source=f"{request.region_frame.source}|{request.region_frame.backend}",
    )
    try:
        # 模型报的是**整屏坐标** → 直接按整屏换算，**不加裁剪原点偏移**。
        px, py = adapter.to_screen(raw[0], raw[1], geometry)
    except CoordinateError as exc:
        return ZoomResult(
            error_code=ErrorCode.LOCATE_FAILED,
            error_message=f"放大的模型回答不可用：{exc}",
            echo=echo,
            model_calls=1,
            scale=scale.value,
        )
    return ZoomResult(
        landing_point=(int(round(px)), int(round(py))),
        echo=echo,
        model_calls=1,
        scale=scale.value,
    )

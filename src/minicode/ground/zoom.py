"""**放大（zoom）** —— 动作协议里的一个动作（工单 M6 AC 6）。

**坐标契约 = 图内坐标 + 物理原点**（2026-10-21 拍板，M14 实测）：

* 指定区域**回源重抓**（重新抓一块物理画面，未缩放，1 像素 = 1 物理像素）
  后送模型看清楚；
* 模型报的是**这张放大图里**的位置；
* **系统**加这块区域的**物理原点**映射回整屏。

⚠️ **为什么不是 Anthropic 那套「放大后仍报整屏坐标」**：本机模型在放大图里
报的是**那张图的像素**，照整屏口径解会**系统性越界**（模型的合法区间是
整屏的尺寸）→ 动作被拒。M14 实测：改用图内坐标后偏差 14.0 px
（对包围盒中心 1.1 px）。**契约服从实测**。

⚠️ **与裁剪不是一回事**（用途与取景来源都不同，**别把这两套混起来**）：

* **放大**：动作协议里的一个动作 —— 主动要看清某片视野，**回源重抓**；
* **裁剪**：两级定位**内部**的取景 —— 从已经抓到的那一帧上 1:1 取窗口。

现在两者**坐标契约相同**（都是「图内坐标 + 原点偏移」），但**原点各走各的**：
本模块的原点是**回源重抓**的那块区域自己的物理原点（``frame.left/top``），
**不产生裁剪计划**（``ZoomResult.crop_plan`` 恒为 ``None``）——
混用会静默偏移一整个放大区域。

放大**不注入任何输入**：它是一次观察（动作协议里的一个动作），
产出的是模型给的落点（物理表面空间）。
"""

from __future__ import annotations

from minicode.action.errors import ErrorCode
from minicode.action.image import encode_frame_base64
from minicode.action.ports import CapturedFrame, GroundingEcho, ZoomRequest, ZoomResult
from minicode.coord.coordinates import (
    DEFAULT_OUT_OF_BOUNDS_TOLERANCE_PX,
    CoordinateAdapter,
    CoordinateError,
    FrameGeometry,
    ModelScale,
)
from minicode.model.effort import Effort
from minicode.model.ports import ModelCaller

from .prompt import GroundingParseError, parse_point

__all__ = ["zoom_locate", "zoom_region_prompt", "region_geometry"]

#: 放大这一级的名字（回显与追踪记录里用）。
STAGE_ZOOM = "zoom"


def region_geometry(frame: CapturedFrame) -> FrameGeometry:
    """放大那一块的**帧几何**：图内 1 像素 = 1 物理像素，原点 = 这块区域的物理原点。

    * ``capture_*`` = 这块区域的尺寸（物理像素，未缩放）；
    * ``model_input_*`` = **同一个尺寸**（1:1 送出 —— 与裁剪那一套同一个假设：
      服务端把这张小图缩小的情形**不在这里处理**）；
    * ``virtual_origin_*`` = **这块区域的物理原点**（``frame.left`` / ``frame.top``）
      —— 于是「图内坐标 + 原点偏移 = 整屏物理坐标」；
    * ``surface_*`` = 这块区域本身 → **越界判定**：模型报的落点必须落在这块
      区域里，落在外面直接报错（不夹取）。

    ⚠️ **本模块不产生裁剪计划** —— 原点取自**回源重抓**的那块区域，
    与裁剪那一套**各走各的物理原点常量**。
    """
    return FrameGeometry(
        capture_width=frame.width,
        capture_height=frame.height,
        model_input_width=frame.width,
        model_input_height=frame.height,
        capture_to_physical_scale=1.0,
        virtual_origin_x=frame.left,
        virtual_origin_y=frame.top,
        surface_width=frame.width,
        surface_height=frame.height,
    )


def zoom_region_prompt(target_description: str, *, image_size: tuple[int, int]) -> str:
    """放大的提示词（**图内坐标**契约）。

    模型报的是**这张放大图里**的位置；**系统**加这块区域的物理原点
    映射回整屏 —— 所以提示词里**不许出现原点**（出现了模型就会改报整屏
    坐标，那是另一套契约；两套混用会让落点偏一整个放大区域）。

    「在这张图里」说的是**参照系**（哪张图），不是**答案单位**；
    答案单位由标定探针判定，不在这里写死。

    ⚠️ **这个提示词住在本模块**：坐标契约变了（整屏坐标 → 图内坐标），
    :mod:`minicode.ground.prompt` 里**旧契约的那份已经删掉**
    （两套坐标契约不许并存 —— ADR-0006：混用会静默偏移一整个窗口）。
    ``minicode.ground.zoom_prompt`` 现在是**本函数的别名**。
    """
    return (
        "这是一块从 Windows 桌面回源重抓的屏幕区域（未缩放，1 像素 = 1 屏幕像素）。\n"
        f"这张图是 {int(image_size[0])}×{int(image_size[1])}。\n"
        f"请给出这张图里名为「{target_description}」的元素【中心】的位置。\n"
        '用 JSON 回答，格式：{"x": 数字, "y": 数字}\n'
        "请给这张图的像素坐标（直接给坐标即可，不用解释你的坐标范围）。"
    )


def zoom_locate(
    model: ModelCaller,
    request: ZoomRequest,
    *,
    scale: ModelScale,
    model_input_size: tuple[int, int] | None = None,
    effort: Effort | str = Effort.LOW,
    max_tokens: int = 2000,
    out_of_bounds_tolerance_px: float = DEFAULT_OUT_OF_BOUNDS_TOLERANCE_PX,
) -> ZoomResult:
    """放大一块区域，问模型目标在**这块图里**的位置（系统加物理原点）。

    参数
    ----
    scale
        **标定判定的标度**（换算链路的分母来源）。**必填**。
    model_input_size
        **旧契约（整屏坐标）用的尺寸** —— 现在的契约下**不参与换算**：
        放大图按 1:1 物理像素送出、模型报的是**图内坐标** → 换算只认
        这块图自己的尺寸。**保留是为了不改调用方**（``e2e/visual.py``）。
    """
    adapter = CoordinateAdapter(
        scale, out_of_bounds_tolerance_px=out_of_bounds_tolerance_px
    )
    frame = request.region_frame
    geometry = region_geometry(frame)
    image_base64 = encode_frame_base64(frame)
    if image_base64 is None:
        return ZoomResult(
            error_code=ErrorCode.LOCATE_FAILED,
            error_message="回源重抓的画面编不成 PNG，送不出图",
            scale=scale.value,
        )
    prompt = zoom_region_prompt(
        request.target_description, image_size=(frame.width, frame.height)
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
        sent_width=frame.width,
        sent_height=frame.height,
        image_source=f"{frame.source}|{frame.backend}",
    )
    try:
        # 模型报的是**图内坐标** → 换算时**加这块区域的物理原点**
        # （越界直接报错，不夹取）。
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

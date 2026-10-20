"""从**原始物理分辨率帧**上裁下一块（工单 M6 AC 1）。

裁剪是**两级定位内部的取景**：从原始物理分辨率图 1:1 取一块窗口，
**不缩放、不重采样** —— 于是 16 px 的小图标进模型仍是 16 px，
不会被整屏降采样抹成 7.9 px。

与「放大（zoom）」的区别：放大是**回源重抓**（重新抓一块区域），
裁剪是从**已经抓到的那一帧**上切一块（同一个动作的两套契约，见工单 M6 AC 6）。

裁剪结果的 ``left`` / ``top`` 是它在物理表面上的原点 —— 这就是
「crop 内坐标 + 原点偏移」里那个**偏移**的来源。
"""

from __future__ import annotations

from minicode.action.ports import CapturedFrame, PixelFormat
from minicode.coord.crop_refine import CropPlan

__all__ = ["crop_frame"]


def crop_frame(frame: CapturedFrame, plan: CropPlan) -> CapturedFrame:
    """从 ``frame`` 上按 ``plan`` 1:1 裁下一块（字节级，不缩放）。

    * 裁剪窗口必须**整个落在这一帧内** —— 越界直接报错（不夹取：
      夹取会把目标裁出窗口）；
    * 像素排布原样保留（BGR / BGRA）；
    * 结果的 ``left`` / ``top`` = 裁剪窗口在物理表面上的原点。
    """
    channels = 4 if frame.pixel_format == PixelFormat.BGRA else 3
    stride = frame.width * channels
    if len(frame.payload) != stride * frame.height:
        raise ValueError(
            f"画面像素数与尺寸对不上：{frame.width}x{frame.height}x{channels} "
            f"应为 {stride * frame.height} 字节，实际 {len(frame.payload)} 字节"
        )
    left = frame.left
    top = frame.top
    if (
        plan.origin_x < left
        or plan.origin_y < top
        or plan.origin_x + plan.width > left + frame.width
        or plan.origin_y + plan.height > top + frame.height
    ):
        raise ValueError(
            f"裁剪窗口 ({plan.origin_x},{plan.origin_y}) "
            f"{plan.width}x{plan.height} 越出这一帧 "
            f"({left},{top}) {frame.width}x{frame.height}"
        )
    rows: list[bytes] = []
    row_bytes = plan.width * channels
    for y in range(plan.origin_y, plan.origin_y + plan.height):
        offset = (y - top) * stride + (plan.origin_x - left) * channels
        rows.append(frame.payload[offset : offset + row_bytes])
    return CapturedFrame(
        width=plan.width,
        height=plan.height,
        left=plan.origin_x,
        top=plan.origin_y,
        payload=b"".join(rows),
        backend=frame.backend,
        source="crop-from-capture",
        pixel_format=frame.pixel_format,
    )

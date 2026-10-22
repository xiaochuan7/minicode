"""从**原始物理分辨率帧**上裁下一块（工单 M6 AC 1）。

裁剪是**两级定位内部的取景**：从原始物理分辨率图 1:1 取一块窗口，
**不缩放、不重采样** —— 于是 16 px 的小图标进模型仍是 16 px，
不会被整屏降采样抹成 7.9 px。

与「放大（zoom）」的区别：放大是**回源重抓**（重新抓一块区域），
裁剪是从**已经抓到的那一帧**上切一块（同一个动作的两套契约，见工单 M6 AC 6）。

裁剪结果的 ``left`` / ``top`` 是它在物理表面上的原点 —— 这就是
「crop 内坐标 + 原点偏移」里那个**偏移**的来源。

⚠️ **施工清单 T3 补了一条**：crop 1:1 裁出之后**再重采样放大**
（:func:`upscale_crop`）—— 因为 1:1 的 crop 里那个 20 物理 px 的图标
进模型仍是 20 px（0.71 个视觉 token 格，亚格）。**裁剪本身仍是 1:1**，
放大是**裁完之后**的另一个动作。
"""

from __future__ import annotations

from minicode.action.ports import CapturedFrame, PixelFormat
from minicode.coord.crop_refine import CropPlan
from minicode.model.image import upscale_frame

__all__ = ["crop_frame", "crop_region", "upscale_crop"]

#: **放大后那张图的最大宽度**（物理像素）—— 服务端的单侧上限。
#:
#: ⚠️ 与 ``model/image.py::SERVER_MAX_INPUT_WIDTH_PX`` 是**同一个数**
#: （服务端会把宽超过它的图缩到这条线 → **那一次请求的坐标系不可信**）。
#: 这里**引用**它而不是重写一个常量。
from minicode.model.image import SERVER_MAX_INPUT_WIDTH_PX

#: **目标表观像素的下限**（施工清单 T3）—— 小于它的目标在模型输入图里
#: 连**一个视觉 token 格**（28 px）都占不满（icon 的 20 物理 px 只有 0.71 格）。
TARGET_APPARENT_MIN_PX = 30.0



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


def crop_region(
    frame: CapturedFrame, rect: tuple[int, int, int, int]
) -> CapturedFrame:
    """从一帧上按 ``(左, 上, 右, 下)`` 裁一块（**帧内像素**，不是物理表面）。

    **用途**：施工清单 T4 的「crop 内 N 个**固定偏移子区域**」——
    每个子区域**单独问一次** → 空间上互相独立的候选点。

    ⚠️ ``left`` / ``top`` **沿用原帧的**（子区域不是物理表面上的那块区域；
    它的物理原点是「原帧原点 + 子区域偏移 ÷ 放大倍数」，但**换算链不读这两个
    值**——它只读**模型给的坐标**与**帧的尺寸**）。
    """
    left, top, right, bottom = (int(value) for value in rect)
    if left < 0 or top < 0 or right > frame.width or bottom > frame.height:
        raise ValueError(
            f"子区域 ({left},{top})-({right},{bottom}) 越出这一帧 "
            f"{frame.width}x{frame.height}"
        )
    if right <= left or bottom <= top:
        raise ValueError(f"子区域 ({left},{top})-({right},{bottom}) 是空的")
    channels = 4 if frame.pixel_format == PixelFormat.BGRA else 3
    stride = frame.width * channels
    if len(frame.payload) != stride * frame.height:
        raise ValueError(
            f"画面像素数与尺寸对不上：{frame.width}x{frame.height}x{channels} "
            f"应为 {stride * frame.height} 字节，实际 {len(frame.payload)} 字节"
        )
    row_bytes = (right - left) * channels
    rows: list[bytes] = []
    for y in range(top, bottom):
        offset = y * stride + left * channels
        rows.append(frame.payload[offset : offset + row_bytes])
    return CapturedFrame(
        width=right - left,
        height=bottom - top,
        left=frame.left,
        top=frame.top,
        payload=b"".join(rows),
        backend=frame.backend,
        source=f"{frame.source}|sub-region",
        pixel_format=frame.pixel_format,
    )


def upscale_crop(
    frame: CapturedFrame,
    *,
    factor: float,
    target_size_px: float,
    max_width: int = SERVER_MAX_INPUT_WIDTH_PX,
) -> tuple[CapturedFrame, float]:
    """把 crop **重采样放大**（施工清单 T3）—— 返回 ``(放大后的帧, 实际倍数)``。

    **为什么放大**：crop 是从物理帧 **1:1** 裁下来的 → 里面那个 20 物理 px 的
    图标进模型仍是 20 px（**0.71 个视觉 token 格**，亚格）→ 换整屏档位对它
    **一点用都没有**（crop 与档位无关）。放大是 icon 的**主杠杆**。

    **倍数怎么定**（不是拍脑袋的常数）：

    * 目标是让**目标的表观像素** ≥ ``TARGET_APPARENT_MIN_PX``（30 px = 1.07 格），
      所以需要 ``factor ≈ 30 / target_size_px``（``target_size_px`` 是目标在
      **物理像素**下的边长）；
    * **上限**：放大后的图宽 ≤ ``max_width``（服务端上限，超了坐标系不可信）；
    * **下限**：``factor ≥ 1`` —— **大目标不放大**（text 子集本来就有 66~87 px，
      放大它只会浪费、还可能改坏落点）。

    ⚠️ **坐标契约不变**：放大的只是**那张图**，模型发的坐标仍然是**图内坐标**
    （换算链由 ``CropRefiner.crop_frame(plan, model_input_size=放大后的尺寸)``
    统一处理，见 ``ground/channel.py``）。**提示词文案必须同步改**
    （「1 像素 = 1 屏幕像素」放大后是假话 → 见 ``ground/prompt.py``）。
    """
    if factor <= 0:
        raise ValueError(f"放大倍数必须为正数，收到 {factor}")
    if target_size_px <= 0:
        raise ValueError(f"目标尺寸必须为正数，收到 {target_size_px}")
    #: **为了达标需要的倍数**。
    needed = TARGET_APPARENT_MIN_PX / target_size_px
    wanted = max(1.0, min(factor, needed))
    #: **宽度上限**决定的倍数（服务端那条线）。
    if frame.width > 0:
        allowed = max_width / frame.width
        if allowed < 1.0:
            raise ValueError(
                f"crop 宽 {frame.width} 已经超过服务端上限 {max_width} —— "
                "这一档的坐标系不可信，不能送"
            )
        wanted = min(wanted, allowed)
    if wanted <= 1.0:
        return frame, 1.0
    width = max(1, int(round(frame.width * wanted)))
    height = max(1, int(round(frame.height * wanted)))
    return upscale_frame(frame, width, height), width / frame.width

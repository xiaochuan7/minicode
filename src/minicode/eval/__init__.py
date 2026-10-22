"""**评测目标脚本的坐标口径**（工单 M7）。

内部一律用**原图像素**；各家评测脚本互不相同的坐标期望，**只在 wrapper
返回前按目标脚本转一次**（口径见 :mod:`minicode.eval.formats` 的模块文档）。

⚠️ 这是**评测脚本侧**的输出格式，**不是模型量纲分母**（那只能来自运行时
标定）。两套命名分属不同域，本包**不 import** :mod:`minicode.coord`。
"""

from __future__ import annotations

import sys

from .formats import (
    CCF_SCREENSPOT_V2,
    SCREENSPOT_PRO,
    SHOWUI,
    ZOOMCLICK,
    EvalBBoxFormat,
    EvalPointFormat,
    EvalSample,
    EvalTarget,
    bbox_xywh_to_xyxy,
    bbox_xyxy_to_xywh,
    point_in_bbox_xywh,
    point_in_bbox_xyxy,
    self_check_hit_rate,
    to_grid_1000,
    to_normalized,
    to_original_pixels,
)


def configure_utf8_output() -> None:
    """控制台按 **UTF-8** 输出（默认代码页是 GBK，中文与符号会变成乱码）。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):  # pragma: no cover - 老解释器没有这个方法
            pass


__all__ = [
    "CCF_SCREENSPOT_V2",
    "EvalBBoxFormat",
    "EvalPointFormat",
    "EvalSample",
    "EvalTarget",
    "SCREENSPOT_PRO",
    "SHOWUI",
    "ZOOMCLICK",
    "bbox_xywh_to_xyxy",
    "bbox_xyxy_to_xywh",
    "configure_utf8_output",
    "point_in_bbox_xywh",
    "point_in_bbox_xyxy",
    "self_check_hit_rate",
    "to_grid_1000",
    "to_normalized",
    "to_original_pixels",
]

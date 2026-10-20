"""**评测目标脚本的坐标口径**（工单 M7 AC 5 / AC 6 / AC 8）。

内部一律用**原图像素**；各家评测脚本互不相同的坐标期望，**只在 wrapper
返回前按目标脚本转一次**（工单 M7 的口径）。

⚠️ **两套命名分属不同域**（架构决策备忘 §2.6 的「口径纪律」）：

* **模型侧**叫「量纲分母」（0~1 / 0~999 / 0~1000 / 绝对像素）——
  只能来自**运行时标定**（C-03 / C-04），本模块**不碰**它，
  因此本模块**不 import** :mod:`minicode.coord`；
* **评测脚本侧**是**输出格式**（归一化 [0,1] / 原图像素 / 0~1000 网格 /
  ``[x,y,W,H]`` 宽高形式 bbox）—— 是**目标脚本要的形状**，不是分母。

下面四个目标的形状与出处（逐行读过本地 clone 的实际脚本）：

======================  ==================  ====================  ==========================
目标                    point 期望           GT bbox 格式          出处
======================  ==================  ====================  ==========================
``SCREENSPOT_PRO``      归一化 [0,1]        ``[x1,y1,x2,y2]`` 像素  ``eval_screenspot_pro.py``
                                                                  ``::eval_sample_positive_gt``
``ZOOMCLICK``           原图像素            ``[x1,y1,x2,y2]`` 像素  ``eval_sspro_zoomclick.py``
                                                                  ``::eval_sample_positive_gt``
``CCF_SCREENSPOT_V2``   原图像素            ``[x,y,W,H]`` 宽高形式  CCF 仓 ``src/eval.py``
                                                                  （ScreenSpot-v2 分支）
``SHOWUI``              0~1000 网格         ``[x,y,W,H]`` 宽高形式  ``main/eval_screenspot.py``
                                                                  ``::get_bbox``
======================  ==================  ====================  ==========================

**ShowUI 的网格值 = 1000**（``get_bbox`` 里写的是 ``int(item * 1000)``，
系统提示词也写「scaled from 1 to 1000」）——**不是 999**。
底稿里「0–999/1000 两说」的传闻到此为止：**以实际脚本为准 = 1000**。
0–999 与 0–1000 在 2560 宽的图上只差 2.56 px，**落错一档不报错**，
只会让压在边界上的样本悄悄判错 —— 所以它被单测钉死。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

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
    "point_in_bbox_xywh",
    "point_in_bbox_xyxy",
    "self_check_hit_rate",
    "to_grid_1000",
    "to_normalized",
    "to_original_pixels",
]


class EvalPointFormat(StrEnum):
    """**评测脚本期望的 point 格式**（输出格式，不是模型量纲分母）。"""

    #: 归一化 [0,1]（ScreenSpot-Pro 官方 ``eval_screenspot_pro.py``）。
    NORMALIZED_01 = "normalized_01"
    #: 原图像素（ZoomClick 副本 ``eval_sspro_zoomclick.py`` / CCF ``src/eval.py``）。
    ORIGINAL_PIXELS = "original_pixels"
    #: 0~1000 网格（ShowUI ``main/eval_screenspot.py``；**实际脚本是 1000**）。
    GRID_1000 = "grid_1000"


class EvalBBoxFormat(StrEnum):
    """**评测脚本的 GT bbox 格式**。"""

    #: ``[x1, y1, x2, y2]``（ScreenSpot-Pro 官方 / ZoomClick 副本）。
    XYXY = "xyxy"
    #: ``[x, y, W, H]``（宽高形式；CCF 仓 ScreenSpot-v2 脚本 / ShowUI 的 GT）。
    XYWH = "xywh"


@dataclass(frozen=True)
class EvalTarget:
    """**一个评测目标脚本的坐标口径**。

    * ``point_format`` —— 它期望的预测点形状；
    * ``bbox_format`` —— 它的 GT bbox 形状；
    * ``source`` —— 出处（仓库 + 文件 + 函数），**可核**。

    口径的**行为**挂在目标自己身上（:meth:`wrap` / :meth:`hit`）——
    每个脚本的判法只写一处，不散落在调用点。
    """

    name: str
    point_format: EvalPointFormat
    bbox_format: EvalBBoxFormat
    source: str

    def wrap(
        self, point_px: tuple[float, float], image_size: tuple[int, int]
    ) -> tuple[float, float] | tuple[int, int]:
        """**按目标脚本转一次**（内部一律原图像素，出口按脚本转）。"""
        return _WRAPPERS[self.point_format](point_px, image_size)

    def hit(self, point: tuple[float, float], sample: EvalSample) -> bool:
        """按**目标脚本的判定口径**判「预测点是否落在 GT 框内」。

        ``point`` 必须**已经是目标脚本期望的格式**（用 :meth:`wrap` 转）。
        口径错了不会报错，只会判错 —— 这正是要钉死格式的原因。
        """
        bbox = _bbox_in_target_format(sample, self)
        width, height = sample.image_size
        if self.point_format is EvalPointFormat.NORMALIZED_01:
            # ScreenSpot-Pro 官方：GT 按 img_size 归一化后与归一化点比。
            x1, y1, x2, y2 = bbox
            return (
                x1 / width <= point[0] <= x2 / width
                and y1 / height <= point[1] <= y2 / height
            )
        if self.point_format is EvalPointFormat.GRID_1000:
            # ShowUI：GT 归一化后 int(*1000)，与同套网格的点比。
            x1, y1, x2, y2 = bbox_xywh_to_xyxy(tuple(int(value) for value in bbox))
            gt = (
                int(x1 / width * 1000),
                int(y1 / height * 1000),
                int(x2 / width * 1000),
                int(y2 / height * 1000),
            )
            return gt[0] <= point[0] <= gt[2] and gt[1] <= point[1] <= gt[3]
        # 像素口径：ZoomClick 副本（xyxy）与 CCF（xywh）。
        if self.bbox_format is EvalBBoxFormat.XYWH:
            return point_in_bbox_xywh(point, tuple(int(value) for value in bbox))
        return point_in_bbox_xyxy(point, tuple(int(value) for value in bbox))


@dataclass(frozen=True)
class EvalSample:
    """**一个评测样本**。

    * ``bbox`` —— GT bbox，**我们的内部口径** ``[x1, y1, x2, y2]``（像素）；
    * ``image_size`` —— 这张图的尺寸（像素）。

    GT 的**脚本侧格式**由 :func:`point_in_bbox` 按目标脚本转一次（只转一次，
    不在两处各转一次）。
    """

    bbox: tuple[int, int, int, int]
    image_size: tuple[int, int]


#: ScreenSpot-Pro 官方评测脚本（`likaixin2000/ScreenSpot-Pro-GUI-Grounding`）。
SCREENSPOT_PRO = EvalTarget(
    name="screenspot_pro",
    point_format=EvalPointFormat.NORMALIZED_01,
    bbox_format=EvalBBoxFormat.XYXY,
    source=(
        "likaixin2000/ScreenSpot-Pro-GUI-Grounding · eval_screenspot_pro.py"
        " · eval_sample_positive_gt（bbox 按 img_size 归一化，point 必须归一化）"
    ),
)

#: ZoomClick 的副本脚本（`Princeton-AI2-Lab/ZoomClick`）。
ZOOMCLICK = EvalTarget(
    name="zoomclick",
    point_format=EvalPointFormat.ORIGINAL_PIXELS,
    bbox_format=EvalBBoxFormat.XYXY,
    source=(
        "Princeton-AI2-Lab/ZoomClick · grounding/eval_sspro_zoomclick.py"
        " · eval_sample_positive_gt（bbox 与 point 都是像素）"
    ),
)

#: CCF 仓的 ScreenSpot-v2 评测脚本（`LufeMC/gui-g2-3b-ccf`）。
CCF_SCREENSPOT_V2 = EvalTarget(
    name="ccf_screenspot_v2",
    point_format=EvalPointFormat.ORIGINAL_PIXELS,
    bbox_format=EvalBBoxFormat.XYWH,
    source=(
        "LufeMC/gui-g2-3b-ccf · src/eval.py · ScreenSpot-v2 分支"
        "（bbox 是 [x, y, W, H] 宽高形式，point 是像素）"
    ),
)

#: ShowUI 的 ScreenSpot 评测脚本（`showlab/ShowUI`）。
SHOWUI = EvalTarget(
    name="showui",
    point_format=EvalPointFormat.GRID_1000,
    bbox_format=EvalBBoxFormat.XYWH,
    source=(
        "showlab/ShowUI · main/eval_screenspot.py · get_bbox"
        "（GT 是 [x, y, W, H] → 归一化 → int(item * 1000)，即 **0~1000 网格**）"
    ),
)


# ---------------------------------------------------------------------------
# 四种边界转换（工单 M7 AC 5）
# ---------------------------------------------------------------------------


def to_normalized(
    point_px: tuple[float, float], image_size: tuple[int, int]
) -> tuple[float, float]:
    """**原图像素 → 归一化 [0,1]**（ScreenSpot-Pro 官方脚本的 point 期望）。"""
    width, height = image_size
    return (point_px[0] / width, point_px[1] / height)


def to_original_pixels(
    point_px: tuple[float, float], image_size: tuple[int, int]
) -> tuple[int, int]:
    """**原图像素 → 原图像素**（ZoomClick 副本的 point 期望；取整到像素）。"""
    return (int(round(point_px[0])), int(round(point_px[1])))


def to_grid_1000(
    point_px: tuple[float, float], image_size: tuple[int, int]
) -> tuple[int, int]:
    """**原图像素 → 0~1000 网格**（ShowUI 脚本的 point 期望）。

    ShowUI 的 ``get_bbox`` 写的是 ``int(item * 1000)`` —— **截断**不是四舍五入，
    网格值是 **1000**（不是 999）。这里与脚本逐字对齐。
    """
    width, height = image_size
    return (int(point_px[0] / width * 1000), int(point_px[1] / height * 1000))


#: point 格式 → 转换函数（**按目标脚本转一次**的映射表）。
_WRAPPERS = {
    EvalPointFormat.NORMALIZED_01: to_normalized,
    EvalPointFormat.ORIGINAL_PIXELS: to_original_pixels,
    EvalPointFormat.GRID_1000: to_grid_1000,
}


def bbox_xyxy_to_xywh(bbox: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
    """**GT bbox ``[x1,y1,x2,y2]`` → ``[x,y,W,H]``**（宽高形式）。"""
    x1, y1, x2, y2 = bbox
    return (x1, y1, x2 - x1, y2 - y1)


def bbox_xywh_to_xyxy(bbox: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
    """**GT bbox ``[x,y,W,H]`` → ``[x1,y1,x2,y2]``**（宽高形式的逆)。"""
    x, y, width, height = bbox
    return (x, y, x + width, y + height)


# ---------------------------------------------------------------------------
# 目标脚本的命中判定（工单 M7 AC 6）
# ---------------------------------------------------------------------------


def point_in_bbox_xyxy(
    point: tuple[float, float], bbox: tuple[int, int, int, int]
) -> bool:
    """命中判定（``[x1,y1,x2,y2]`` 口径，**像素**）。"""
    x1, y1, x2, y2 = bbox
    return x1 <= point[0] <= x2 and y1 <= point[1] <= y2


def point_in_bbox_xywh(
    point: tuple[float, float], bbox: tuple[int, int, int, int]
) -> bool:
    """命中判定（``[x,y,W,H]`` 宽高形式口径，**像素**）。

    ⚠️ 宽高形式**不能**当 ``[x1,y1,x2,y2]`` 读 —— 那样会得出相反结论
    （工单 M7 AC 5 的反例）。
    """
    x, y, width, height = bbox
    return x <= point[0] <= x + width and y <= point[1] <= y + height


def _bbox_in_target_format(sample: EvalSample, target: EvalTarget) -> tuple[int, ...]:
    """把内部口径的 GT bbox 转成**目标脚本**的 bbox 格式（只转一次）。"""
    if target.bbox_format is EvalBBoxFormat.XYWH:
        return bbox_xyxy_to_xywh(sample.bbox)
    return sample.bbox


def self_check_hit_rate(
    samples: Sequence[EvalSample], target: EvalTarget
) -> float:
    """**防假象自检**：把 GT bbox 中心当预测点喂给自己的 wrapper。

    命中率必须接近 **100%**，不是 0 —— 「接了官方脚本但分数全 0」那类假象
    的自检（工单 M7 AC 6）。
    """
    if not samples:
        return 0.0
    hits = 0
    for sample in samples:
        x1, y1, x2, y2 = sample.bbox
        center = ((x1 + x2) // 2, (y1 + y2) // 2)
        prediction = target.wrap(center, sample.image_size)
        if target.hit(prediction, sample):
            hits += 1
    return hits / len(samples)

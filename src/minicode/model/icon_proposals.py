"""**图标候选源**（V3，零模型）：在一帧截图里**无字图标**的位置提议。

⚠️ **住在 model 包**（不在 ground）：ground 包有「只许标准库」的源码级红线
（``test_ground_two_stage.py::DependencyTests``），而本模块的 CV 提议要
numpy/PIL（vision 附加项）—— model 包本就持有视觉预处理（``model/image.py``）。
它实现 :class:`minicode.ground.candidates.CandidateSource` 协议（鸭子类型），
由调用方**注入**给通道 —— ground 只认协议，不认实现。

**为什么需要它**（V3 的依据）：图标类目标 47.13% 命中的根因是
「**候选表里没有无字图标**」—— UIA 只给位置占位（还常被大窗口淹没）、
OCR 原理上召不回无字图标。补候选，而不是换更强的模型。

**这一步是零模型的**（当天下课的验证）：不引入任何检测模型 ——
用经典 CV 启发式把「图标样」的区域提出来当候选：

1. **降采样灰度**（最长边 ≤ :data:`PROPOSAL_MAX_WIDTH_PX`，结果按比例映射回
   物理像素）；
2. **梯度幅值**（Sobel 的廉价替代：一阶差分）→ **Otsu 自适应阈值** →
   边缘掩码 → 小半径膨胀（把图标的轮廓连成块）；
3. **连通域**（4 邻接 BFS，纯 Python —— 掩码稀疏，够快）→ 每个块一个
   候选框；按尺寸/密实度/边缘密度过滤（大窗口与细碎噪点都不是图标）；
4. **去重**：复用 :func:`minicode.ground.candidates.dedupe_by_iou`
   （同点重复提议）+ **pHash 汉明距**（同一目标的偏移双检出 ——
   IoU 低但内容相同的合并；**不同位置的相似图标不去重** ——
   两个一模一样的垃圾桶图标是两个真实目标）；
5. **可读描述**：注入 OCR 词读数端口时，把**最近的词**挂成候选的文字
   （工具栏图标下面的那行小字）；没有 OCR → 位置型占位描述
   （「无字图标（自上而下第 N 个）」，与 UIA 无名控件同一口径）。

**接线纪律**：产出的候选走既有的 :func:`minicode.ground.candidates.merge_candidate_sources`
（图标提议当**附加表**，UIA 主表全留）—— 与 OCR 附加表同一通道，
IoU 去重一律在**物理像素**空间（红线①）。

**许可边界**（V3 的要求）：本模块不引入任何第三方检测模型；
检测模型（ScreenParser 等）是第三候选源，许可证核实后另行接入，
接不上时本模块就是 V3 的答案（如实记录，不卡死）。
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

from minicode.action.ports import CapturedFrame
from minicode.ground.candidates import (
    GroundingCandidate,
    OcrWordLike,
    dedupe_by_iou,
)

if TYPE_CHECKING:
    # numpy 只在**函数体内懒加载**（core 零依赖红线：ground 包不许引第三方，
    # model 包的视觉预处理只有 vision 附加项装了才用得到）；
    # 类型标注借 TYPE_CHECKING 解析 —— 运行时 annotation 是字符串（见上）。
    import numpy

__all__ = [
    "ICON_PROPOSAL_SOURCE",
    "MAX_ICON_CANDIDATES",
    "MAX_ICON_PHYSICAL_PX",
    "MIN_AREA_PHYSICAL_PX",
    "MIN_ICON_PHYSICAL_PX",
    "PHASH_DEDUPE_HAMMING",
    "PROPOSAL_MAX_WIDTH_PX",
    "IconProposalCandidateSource",
    "hamming_distance",
    "phash_of_frame",
]

#: 提议所在的工作分辨率（最长边，像素）。
#:
#: ⚠️ **W7 修正（2026-11-04）**：原值 640 的理由是「连通域是纯 Python 逐像素
#: BFS，大了太慢」—— 连通域已重写为 numpy 两趟并查集（见
#: :func:`_connected_components`），640 的性能理由不复存在。
#: **留着 640 的代价是分辨率污染**：4K（3840 宽）→ 缩放 0.167，
#: 6 个工作像素 = 36 个物理像素 —— 尺寸过滤若在工作分辨率里卡物理阈值，
#: 21/30 的 S-Pro icon 样本在算法跑之前就被机械排除。
PROPOSAL_MAX_WIDTH_PX = 2016

#: 图标提议的**物理像素**域尺寸阈值（最小边）—— 16px 级小图标远高于它，
#: 只滤噪点。⚠️ 判定在**物理像素**域做（工作分辨率尺寸 × inv_scale），
#: 不许再回到「工作分辨率里卡物理尺寸」的老坑。
MIN_ICON_PHYSICAL_PX = 6

#: 图标提议的**物理像素**域尺寸阈值（最大边）—— 大窗口不是图标。
MAX_ICON_PHYSICAL_PX = 200

#: 连通域块的最小面积（**物理像素**²；工作分辨率面积 × inv_scale² 换算）。
MIN_AREA_PHYSICAL_PX = 12

#: 图标提议清单的长度上限（与 :data:`minicode.ground.candidates.MAX_CANDIDATES`
#: 同档 —— 上限是否够用由 V3 的量化脚本回答，配置项在这里可调）。
MAX_ICON_CANDIDATES = 20

#: pHash 去重的汉明距阈值（≤ 该值 = 同一目标的双检出）。
PHASH_DEDUPE_HAMMING = 4

#: 候选源的 ``source`` 标签（落盘与报告里认它）。
ICON_PROPOSAL_SOURCE = "icon-proposal"

#: OCR 词读数端口（与 :data:`minicode.ground.candidates.WordReader` 同形状）。
WordReader = Callable[[CapturedFrame], Sequence[OcrWordLike]]


def _require_numpy():
    """numpy 是 **vision/exec 附加项**（core 零依赖红线）→ 懒加载。"""
    try:
        import numpy
    except ImportError as exc:  # pragma: no cover - 环境缺附加项时
        raise RuntimeError(
            "图标候选源需要 numpy（pip install -e '.[vision]'）；"
            "core 路径不依赖它，只有视觉通道的这条分支用"
        ) from exc
    return numpy


def _gray_from_frame(frame: CapturedFrame):
    """帧的 BGR(A) 字节流 → 灰度数组（浮点 0~1，行优先）。"""
    numpy = _require_numpy()
    channels = 3 if frame.pixel_format.value == "bgr" else 4
    pixels = numpy.frombuffer(frame.payload, dtype=numpy.uint8)
    if pixels.size < frame.width * frame.height * channels:
        raise ValueError(
            f"帧 payload 太小：{pixels.size} 字节装不下 "
            f"{frame.width}×{frame.height}×{channels}"
        )
    pixels = pixels[: frame.width * frame.height * channels].reshape(
        frame.height, frame.width, channels
    )
    # BGR 排布：灰度 = 0.299R + 0.587G + 0.114B。
    return (
        0.299 * pixels[:, :, 2].astype(numpy.float32)
        + 0.587 * pixels[:, :, 1]
        + 0.114 * pixels[:, :, 0]
    ) / 255.0


def _otsu_threshold(values: numpy.ndarray) -> float:
    """Otsu 自适应阈值（直方图 256 桶；确定性，无随机）。"""
    numpy = _require_numpy()
    hist, _ = numpy.histogram(values, bins=256, range=(0.0, 1.0))
    total = values.size
    if total == 0:
        return 0.5
    probability = hist.astype(numpy.float64) / total
    centers = (numpy.arange(256) + 0.5) / 256.0
    omega = numpy.cumsum(probability)
    mu = numpy.cumsum(probability * centers)
    mu_total = mu[-1]
    between = (mu_total * omega - mu) ** 2 / numpy.maximum(
        omega * (1.0 - omega), 1e-12
    )
    return float(centers[int(numpy.argmax(between))])


def _dilate(mask: numpy.ndarray, radius: int) -> numpy.ndarray:
    """二值膨胀（3×3 OR 卷积迭代 ``radius`` 次；不做形态学依赖）。"""
    numpy = _require_numpy()
    result = mask.copy()
    for _ in range(max(0, int(radius))):
        padded = numpy.pad(result, 1, mode="constant", constant_values=False)
        grown = numpy.zeros_like(result)
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                grown |= padded[
                    1 + dy : 1 + dy + result.shape[0],
                    1 + dx : 1 + dx + result.shape[1],
                ]
        result = grown
    return result


def _connected_components(mask: numpy.ndarray) -> list[tuple[int, int, int, int, int]]:
    """4 邻接连通域（**numpy 两趟并查集**）→ ``(left, top, right, bottom, 像素数)`` 列表。

    坐标是**工作分辨率**；``right`` / ``bottom`` 是半开区间（旧 BFS 同口径）。

    ⚠️ 旧实现是纯 Python 逐像素 BFS —— 在 640 宽的工作分辨率上够用，
    但工作分辨率提到 2016 后是几十秒级的（这就是当初只敢取 640 的原因，
    也是 W7 分辨率污染的根因）。两趟并查集把逐像素工作交给 numpy
    （**第 1 趟**：逐行 run 长编码 —— 像素 → 远少于像素的 run），
    **第 2 趟**：相邻两行的 run 按列区间相交合并（区间相交矩阵也是 numpy），
    并查集只对 run 做 —— 纯 Python 部分只剩下对 run 的 union/find，毫秒级。
    """
    numpy = _require_numpy()
    height, _width = mask.shape
    # -- 第 1 趟：逐行 run 长编码（向量化：diff 找每行的起点/终点）--------
    # ``dx[y, x] = padded[y, x+1] - padded[y, x]``；+1 = run 起点，-1 = 终点。
    # 左右各补一列哨兵 0 —— 右端哨兵保证「贴到最后一列的段」也有终点可检。
    padded = numpy.zeros((height, _width + 2), dtype=numpy.int8)
    padded[:, 1 : _width + 1] = mask
    diffs = numpy.diff(padded, axis=1)
    row_of_start, x0_all = numpy.nonzero(diffs == 1)
    row_of_end, x1_all = numpy.nonzero(diffs == -1)
    # nonzero 按行主序返回 → 起点与终点逐位置配对（同一行内）。
    runs: list[tuple[int, int, int]] = [
        (int(y), int(x0), int(x1))
        for y, x0, x1 in zip(row_of_start, x0_all, x1_all, strict=True)
    ]
    if not runs:
        return []

    # -- 第 2 趟：并查集，相邻行的 run 列区间相交 → 合并 -------------------
    parent = list(range(len(runs)))

    def find(node: int) -> int:
        while parent[node] != node:
            parent[node] = parent[parent[node]]
            node = parent[node]
        return node

    by_row: dict[int, list[int]] = {}
    for index, (row, _x0, _x1) in enumerate(runs):
        by_row.setdefault(row, []).append(index)
    for row in sorted(by_row):
        previous = by_row.get(row - 1)
        if not previous:
            continue
        current = by_row[row]
        starts_a = numpy.fromiter((runs[i][1] for i in current), dtype=numpy.int64)
        ends_a = numpy.fromiter((runs[i][2] for i in current), dtype=numpy.int64)
        starts_b = numpy.fromiter((runs[i][1] for i in previous), dtype=numpy.int64)
        ends_b = numpy.fromiter((runs[i][2] for i in previous), dtype=numpy.int64)
        # 4 邻接：上下两行的像素在**同一列**才相邻 → 列区间相交。
        overlap = (starts_a[:, None] < ends_b[None, :]) & (
            starts_b[None, :] < ends_a[:, None]
        )
        for ia, ib in zip(*numpy.nonzero(overlap), strict=True):
            root_a, root_b = find(current[ia]), find(previous[ib])
            if root_a != root_b:
                parent[root_a] = root_b

    # -- 第 3 趟：按根汇总包围盒与面积（面积按 run 长累计 = 真实像素数）----
    boxes: dict[int, list[int]] = {}
    for index, (row, x0, x1) in enumerate(runs):
        root = find(index)
        box = boxes.get(root)
        if box is None:
            boxes[root] = [x0, row, x1, row + 1, x1 - x0]
            continue
        box[0] = min(box[0], x0)
        box[1] = min(box[1], row)
        box[2] = max(box[2], x1)
        box[3] = max(box[3], row + 1)
        box[4] += x1 - x0
    return [(box[0], box[1], box[2], box[3], box[4]) for box in boxes.values()]


def _dct_matrix(size: int) -> numpy.ndarray:
    """正交 DCT-II 变换矩阵（一维；二维 = 行列各乘一次）。"""
    numpy = _require_numpy()
    indexes = numpy.arange(size, dtype=numpy.float64)
    k = indexes.reshape(-1, 1)
    n = indexes.reshape(1, -1)
    matrix = numpy.cos((2 * n + 1) * k * math.pi / (2 * size)) * math.sqrt(2.0 / size)
    matrix[0, :] = math.sqrt(1.0 / size)
    return matrix


def phash_of_frame(frame: CapturedFrame, *, hash_size: int = 8) -> int:
    """一帧的 pHash（DCT 感知哈希，64 位整数）—— 测试与去重共用。"""
    numpy = _require_numpy()
    from PIL import Image

    gray = _gray_from_frame(frame)
    image = Image.fromarray((gray * 255.0).clip(0, 255).astype(numpy.uint8))
    small = image.resize((hash_size * 4, hash_size * 4), Image.BILINEAR)
    pixels = numpy.asarray(small, dtype=numpy.float64)
    matrix = _dct_matrix(pixels.shape[0])
    matrix_t = _dct_matrix(pixels.shape[1]).T
    dct = matrix @ pixels @ matrix_t
    low = dct[:hash_size, :hash_size]
    bits = low > numpy.median(low)
    value = 0
    for index, bit in enumerate(bits.flatten()):
        value |= int(bit) << index
    return value


def hamming_distance(left: int, right: int) -> int:
    """两个 pHash 的汉明距（64 位整数）。"""
    return bin(left ^ right).count("1")


@dataclass(frozen=True)
class _Proposal:
    """一个图标提议（工作分辨率坐标）。"""

    box: tuple[int, int, int, int]
    edge_density: float
    fill_ratio: float

    @property
    def score(self) -> float:
        """提议分：边缘密度 × 密实度（图标 = 边缘密、块内实）。"""
        return self.edge_density * self.fill_ratio


class IconProposalCandidateSource:
    """**零模型图标候选源**（CandidateSource 协议的实现）。

    参数
    ----
    max_candidates
        清单长度上限（默认 :data:`MAX_ICON_CANDIDATES` = 20）。
    word_reader
        **可选** OCR 词读数端口：把图标**最近的词**挂成描述（可指认的名字）。
        不给 → 位置型占位描述。
    """

    def __init__(
        self,
        *,
        max_candidates: int = MAX_ICON_CANDIDATES,
        word_reader: WordReader | None = None,
    ):
        self.max_candidates = int(max_candidates)
        self.word_reader = word_reader

    def candidates(
        self, frame: CapturedFrame, description: str
    ) -> list[GroundingCandidate]:
        """这一帧上的图标提议（**物理像素**，编号 1 起）。

        ⚠️ ``description`` **不参与过滤** —— 图标提议是**召回**用途：
        全部图标都进清单，让编号分支的模型去选（描述匹配是 OCR/UIA 源的事）。
        """
        numpy = _require_numpy()
        from PIL import Image

        if frame.is_empty:
            return []
        gray = _gray_from_frame(frame)
        # -- 降采样到工作分辨率 -------------------------------------------
        scale = min(1.0, PROPOSAL_MAX_WIDTH_PX / max(frame.width, frame.height))
        work_width = max(1, int(round(frame.width * scale)))
        work_height = max(1, int(round(frame.height * scale)))
        image = Image.fromarray((gray * 255.0).clip(0, 255).astype(numpy.uint8))
        small = numpy.asarray(
            image.resize((work_width, work_height), Image.BILINEAR), dtype=numpy.float32
        ) / 255.0

        # -- 梯度 → Otsu 阈值 → 膨胀 --------------------------------------
        gradient = numpy.zeros_like(small)
        gradient[:, :-1] = numpy.maximum(gradient[:, :-1], numpy.abs(numpy.diff(small, axis=1)))
        gradient[:-1, :] = numpy.maximum(gradient[:-1, :], numpy.abs(numpy.diff(small, axis=0)))
        threshold = max(_otsu_threshold(gradient), 0.12)
        edge_mask = gradient > threshold
        mask = _dilate(edge_mask, radius=1)

        # -- 连通域 → 过滤（尺寸 / 密实度 / 边缘密度）----------------------
        # 尺寸判定在**物理像素**域（W7 修正）：阈值是按物理图标尺寸定的，
        # 在工作分辨率里卡会让 4K 下的小图标在算法跑之前就被机械排除。
        inv_scale = 1.0 / scale
        proposals: list[_Proposal] = []
        for left, top, right, bottom, area in _connected_components(mask):
            width = right - left
            height = bottom - top
            width_physical = width * inv_scale
            height_physical = height * inv_scale
            if not (
                MIN_ICON_PHYSICAL_PX <= width_physical <= MAX_ICON_PHYSICAL_PX
                and MIN_ICON_PHYSICAL_PX <= height_physical <= MAX_ICON_PHYSICAL_PX
            ):
                continue
            if area * inv_scale * inv_scale < MIN_AREA_PHYSICAL_PX:
                continue
            box_area = width * height
            fill = area / box_area
            if fill < 0.12:
                continue
            # 边缘密度用**膨胀前**的边缘掩码数（图标的特征：小块里边缘很密）。
            edge_pixels = int(edge_mask[top:bottom, left:right].sum())
            proposals.append(
                _Proposal(
                    (left, top, right, bottom),
                    edge_density=edge_pixels / box_area,
                    fill_ratio=fill,
                )
            )
        # 分数高在前；同分小块在前（图标比大窗口更像目标）。
        proposals.sort(
            key=lambda item: (-item.score, item.box[2] * item.box[3])
        )
        proposals = proposals[: self.max_candidates * 3]

        # -- 工作分辨率 → 物理像素 ----------------------------------------

        def _physical(box: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
            left, top, right, bottom = box
            pad = 2
            return (
                frame.left + max(0, int((left - pad) * inv_scale)),
                frame.top + max(0, int((top - pad) * inv_scale)),
                frame.left + min(frame.width, int((right + pad) * inv_scale)),
                frame.top + min(frame.height, int((bottom + pad) * inv_scale)),
            )

        # -- 去重：IoU 同点合并 + pHash 偏移双检出 -------------------------
        words = self._words(frame)
        kept: list[tuple[GroundingCandidate, int]] = []
        for proposal in proposals:
            box = _physical(proposal.box)
            hash_value = self._hash_of_region(gray, proposal.box)
            duplicate = False
            for other, other_hash in kept:
                overlap = _iou(box, other.box)
                if overlap > 0.5:
                    duplicate = True
                    break
                if (
                    overlap > 0.1
                    and hamming_distance(hash_value, other_hash) <= PHASH_DEDUPE_HAMMING
                ):
                    duplicate = True
                    break
            if duplicate:
                continue
            candidate = GroundingCandidate(
                number=0,
                text=self._describe(box, words),
                box=box,
                source=ICON_PROPOSAL_SOURCE,
            )
            kept.append((candidate, hash_value))
            if len(kept) >= self.max_candidates:
                break

        # 物理空间再过一遍既有去重（与其它源同一口径），然后编号；
        # **占位描述在这里补上真实序号**（「自上而下第 N 个」—— 去重前序号会漂）。
        final = dedupe_by_iou([candidate for candidate, _ in kept], threshold=0.5)
        final.sort(key=lambda candidate: (candidate.box[1], candidate.box[0]))
        return [
            GroundingCandidate(
                number=index + 1,
                text=candidate.text.replace(
                    "无字图标（画面自上而下第 _ 个）",
                    f"无字图标（画面自上而下第 {index + 1} 个）",
                ),
                box=candidate.box,
                source=candidate.source,
            )
            for index, candidate in enumerate(final)
        ]

    def _hash_of_region(self, gray, box) -> int:
        """提议区域的 pHash（在**工作分辨率**的灰度上取，不必回原图）。"""
        numpy = _require_numpy()
        from PIL import Image

        left, top, right, bottom = box
        region = gray[top:bottom, left:right]
        if region.size == 0:
            return 0
        image = Image.fromarray((region * 255.0).clip(0, 255).astype(numpy.uint8))
        small = image.resize((32, 32), Image.BILINEAR)
        pixels = numpy.asarray(small, dtype=numpy.float64)
        matrix = _dct_matrix(32)
        dct = matrix @ pixels @ matrix.T
        low = dct[:8, :8]
        bits = low > numpy.median(low)
        value = 0
        for index, bit in enumerate(bits.flatten()):
            value |= int(bit) << index
        return value

    def _words(self, frame: CapturedFrame) -> list[OcrWordLike]:
        """OCR 词（帧像素空间）；读数端口没给 / 报错 → 空表（不炸主链路）。"""
        if self.word_reader is None:
            return []
        try:
            return list(self.word_reader(frame))
        except Exception:  # noqa: BLE001 —— OCR 不可用只影响描述质量
            return []

    def _describe(
        self, box: tuple[int, int, int, int], words: Sequence[OcrWordLike]
    ) -> str:
        """候选的可读描述：**最近的 OCR 词**；没有 → 位置型占位（序号后补）。"""
        if words:
            nearest = _nearest_word(box, words)
            if nearest:
                return f"{nearest}（图标）"
        return "无字图标（画面自上而下第 _ 个）"


def _nearest_word(
    box: tuple[int, int, int, int], words: Sequence[OcrWordLike]
) -> str:
    """离候选框**最近的词**（物理像素 vs 词框；先要求纵向相邻，再取距离最小）。"""
    left, top, right, bottom = box
    height = max(1, bottom - top)
    best: tuple[float, str] | None = None
    for word in words:
        word_left = int(word.x)
        word_top = int(word.y)
        word_right = word_left + int(word.w)
        word_bottom = word_top + int(word.h)
        # 纵向：图标下方/上方 2×图标高的范围（工具栏图标的标签就在那里）。
        if word_bottom < top - 2 * height or word_top > bottom + 2 * height:
            continue
        gap = max(word_left - right, left - word_right, 0)
        if best is None or gap < best[0]:
            best = (gap, str(word.text))
    if best is None or not best[1].strip():
        return ""
    return best[1].strip()


def _iou(
    a: tuple[int, int, int, int], b: tuple[int, int, int, int]
) -> float:
    """两个框的 IoU（物理像素；退化框 → 0）。"""
    left = max(a[0], b[0])
    top = max(a[1], b[1])
    right = min(a[2], b[2])
    bottom = min(a[3], b[3])
    if right <= left or bottom <= top:
        return 0.0
    intersection = (right - left) * (bottom - top)
    area_a = max(0, a[2] - a[0]) * max(0, a[3] - a[1])
    area_b = max(0, b[2] - b[0]) * max(0, b[3] - b[1])
    union = area_a + area_b - intersection
    if union <= 0:
        return 0.0
    return intersection / union

"""**候选源**：一句目标描述 → 一串**候选框**（工单 B-1）。

**为什么需要它**：视觉通道原来让模型「瞄点」（自己报坐标）—— 落点的精度
跟着模型走。改成「模型选编号」之后，**落点不再由模型算**，而是**候选框的
物理中心**（UIA 通道早就跑通了同一范式，见 :mod:`minicode.uia.pointer`：
模型只答行号 → 落点取控件框的物理像素中心 → 偏差 0.0 px）。

**候选从哪来**（UFO² 的混合检测：**UIA 主表 + OCR 附加表**）：

* **UIA 控件**（:class:`UiaCandidateSource`）—— 主表：控件框是**物理屏幕坐标**，
  名字可能为空（图标控件）→ 给位置型占位描述，**不丢**；
* **Windows 自带 OCR 的词级框**
  （:meth:`minicode.verification.ocr_windows.WindowsOcrPort.read_words`）——
  附加表：图标没有文字，**原理上召不回**（静态图 icon 0/13 的成因）。

两者按 :func:`merge_candidate_sources` 合并：**主表全留**，附加表里与主表
IoU > 0.1 的丢弃（**方向不能反**）。

**坐标系 —— 唯一真值 = 物理像素 × 虚拟桌面原点**：

* OCR 框在「喂进去的那张帧」的像素空间里 → 转物理像素**必须加上帧的原点**
  （:attr:`minicode.action.ports.CapturedFrame.left` / ``top``）；
* **IoU 去重也在物理像素空间里做** —— UFO² 的论文没写它在哪个坐标系比，
  我们自己定死（红线①）。

**匹配规则**：目标描述里出现的**文本子串**能在某个 OCR 词（或相邻词的拼接）
里找到 → 命中。**中文按子串匹配，不做分词**（分词会把「显示器、亮度」这种
界面用语切碎，切碎了就匹配不上）。
"""

from __future__ import annotations

import unicodedata
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from typing import Any, Protocol

from minicode.action.ports import CapturedFrame
from minicode.verification.ocr import normalize_ocr_text

__all__ = [
    "CandidateSource",
    "GroundingCandidate",
    "IOU_THRESHOLD_FOR_MERGE",
    "MAX_CANDIDATES",
    "MIN_MATCH_LENGTH",
    "OcrCandidateSource",
    "OcrWordLike",
    "UiaCandidateSource",
    "UiaControlLike",
    "WordReader",
    "candidate_coverage",
    "dedupe_by_iou",
    "iou",
    "merge_candidate_sources",
]

#: **IoU 去重阈值**（抄 UFO² 官方文档的 ``IOU_THRESHOLD_FOR_MERGE: 0.1``）。
#:
#: 出处：UFO² 官方文档 *Hybrid Detection* 一节（给出该常量与去重算法）。
#: 论文 Table 3：UIA 37.3 / OmniParser 127.3 / 合并 97.9 个检出。
#: ⚠️ **论文没写 IoU 比较发生在哪个坐标系** —— 我们定死：**物理像素**。
IOU_THRESHOLD_FOR_MERGE = 0.1

#: 命中所需的最小长度（归一化后的字符数）。
#:
#: 一个字/一个字母不当候选 —— 否则「的」「on」这种满屏都是的词会把清单淹掉。
MIN_MATCH_LENGTH = 2

#: **候选清单的长度上限**（提示词长度有上限）。
#:
#: 来源：UFO² 官方 ``config/ufo/system.yaml`` 的 ``CONTROL_FILTER_TOP_K_ICON: 15``
#: 是**图标**那一档的上限；我们清单里混着控件，取 **20**（与 OCR 源同一档）。
MAX_CANDIDATES = 20


@dataclass(frozen=True)
class GroundingCandidate:
    """一个候选（**物理像素**）。

    * ``number`` —— 清单里的编号（1 起）。**模型只答这个编号**；
    * ``text`` —— 命中的文字（界面上的原文，不做归一）；
    * ``box`` —— 候选框 ``(left, top, right, bottom)``，**物理像素**；
    * ``source`` —— 来源（``"ocr"`` …）。

    ⚠️ ``box`` 永远是**物理像素**（不是帧像素、不是模型输入像素）：
    落点直接取它的中心，**零换算**。
    """

    number: int
    text: str
    box: tuple[int, int, int, int]
    source: str = "ocr"

    @property
    def center(self) -> tuple[float, float]:
        """候选框的**物理中心**（落点就取它）。"""
        left, top, right, bottom = self.box
        return ((left + right) / 2.0, (top + bottom) / 2.0)


def iou(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> float:
    """两个框的 IoU（**物理像素**坐标系里比）。

    不相交 → 0.0；退化框（宽或高为 0）→ 0.0。
    """
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


def dedupe_by_iou(
    candidates: Sequence[GroundingCandidate],
    *,
    threshold: float = IOU_THRESHOLD_FOR_MERGE,
) -> list[GroundingCandidate]:
    """IoU 去重：**严格大于**阈值 → 当同一个，**保留先出现的那个**。

    * 阈值抄 UFO² 官方文档（:data:`IOU_THRESHOLD_FOR_MERGE`）；
    * 保留先出现的：OCR 的词序就是阅读顺序 → 同一段文字的第一处；
    * ⚠️ **比较必须在物理像素坐标系里做**（调用方先把框转成物理像素）。
    """
    kept: list[GroundingCandidate] = []
    for candidate in candidates:
        if any(iou(candidate.box, other.box) > threshold for other in kept):
            continue
        kept.append(candidate)
    return kept


class OcrWordLike(Protocol):
    """词级读数的形状（:class:`minicode.verification.ocr_windows.OcrWord` 满足它）。"""

    @property
    def text(self) -> str:
        """认出来的词。"""

    @property
    def x(self) -> int:
        """词框左上角 x（那张图的像素）。"""

    @property
    def y(self) -> int:
        """词框左上角 y（那张图的像素）。"""

    @property
    def w(self) -> int:
        """词框宽（那张图的像素）。"""

    @property
    def h(self) -> int:
        """词框高（那张图的像素）。"""


#: **词级读数的端口**（OCR 后端）：一帧 → 一串词。
WordReader = Callable[[CapturedFrame], Sequence[OcrWordLike]]


class CandidateSource(Protocol):
    """**候选源**端口：一句目标描述 + 一帧 → 一串候选框（**物理像素**）。

    实现可以是 OCR 词级框（:class:`OcrCandidateSource`），
    也可以是 UIA 控件（:class:`UiaCandidateSource`）——
    单测塞假候选、评测台塞真 OCR 源，都走这一个口。
    """

    def candidates(
        self, frame: CapturedFrame, description: str
    ) -> Sequence[GroundingCandidate]:
        """这一帧上与描述匹配的候选（**物理像素**，已编号）。"""
        ...


class UiaControlLike(Protocol):
    """UIA 控件的形状：**名字 + 物理像素矩形**。

    :class:`minicode.uia.elements.UiaElement` 与
    :class:`minicode.action.ports.ControlCandidate` 都满足它。
    """

    @property
    def name(self) -> str:
        """控件名（**可能为空** —— 图标控件常常没有名字）。"""

    @property
    def rect(self) -> tuple[int, int, int, int]:
        """控件框 ``(left, top, right, bottom)``，**物理像素**。"""


def _placeholder_names(controls: Sequence[UiaControlLike]) -> dict[int, str]:
    """**无名控件**的位置型占位描述（``{控件下标: 描述}``）。

    ⚠️ **不静默丢弃无名控件** —— 图标控件本来就没有文字，丢了它们
    「图标召回」就永远是 0。占位描述按画面**自上而下、同排自左而右**编号。
    """
    unnamed = [
        (index, control)
        for index, control in enumerate(controls)
        if not (control.name or "").strip()
    ]
    unnamed.sort(key=lambda item: (int(item[1].rect[1]), int(item[1].rect[0])))
    names: dict[int, str] = {}
    for order, (index, control) in enumerate(unnamed, start=1):
        kind = (getattr(control, "control_type", "") or "").strip()
        names[index] = f"无名{kind or '控件'}（自上而下第 {order} 个）"
    return names


class UiaCandidateSource:
    """**UIA 控件**当候选源（UFO² 的「UIA 主表」那一半）。

    * 输入：一批 UIA 控件（名字 + **物理像素**矩形，形状见 :class:`UiaControlLike`）；
    * 输出：``GroundingCandidate``（编号 1 起、框是**物理像素**、``source="uia"``）。

    ⚠️ **UIA 的框不要加任何偏移**：``BoundingRectangle`` 已经是物理屏幕坐标
    （微软官方文档：矩形 "is expressed in physical screen coordinates"）。

    ⚠️ **无名控件不丢**：名字为空时给**位置型占位描述**
    （如「无名Button（自上而下第 3 个）」）—— 静默丢弃就是「图标召回 0」的成因。

    清单长度上限 :data:`MAX_CANDIDATES`：**先按「名字与目标描述的匹配度」排**，
    匹配不上的**按面积升序**补位（面积小的先上 —— 图标比大窗口更像目标）。
    """

    def __init__(
        self,
        controls: Sequence[UiaControlLike],
        *,
        max_candidates: int = MAX_CANDIDATES,
    ):
        self._controls = list(controls)
        self.max_candidates = int(max_candidates)

    def candidates(
        self, frame: CapturedFrame, description: str
    ) -> list[GroundingCandidate]:
        """这一帧上的候选（**物理像素**，编号 1 起）。

        ⚠️ ``frame`` **不用**（UIA 的框本来就是物理屏幕坐标，不是帧像素）。
        """
        placeholders = _placeholder_names(self._controls)
        rows: list[tuple[str, tuple[int, int, int, int]]] = []
        for index, control in enumerate(self._controls):
            rect = tuple(int(value) for value in control.rect)
            if rect[2] <= rect[0] or rect[3] <= rect[1]:
                # 退化框（没有面积）不是候选。
                continue
            text = (control.name or "").strip() or placeholders.get(index, "")
            if not text:
                continue
            rows.append((text, rect))

        target_compact = _compact(description)
        target_spaced = _spaced(description)

        def _sort_key(item: tuple[str, tuple[int, int, int, int]]) -> tuple:
            text, rect = item
            matched = _is_target(text, target_compact, target_spaced)
            area = (rect[2] - rect[0]) * (rect[3] - rect[1])
            return (0 if matched else 1, area, text)

        rows.sort(key=_sort_key)
        kept = rows[: self.max_candidates]
        return [
            GroundingCandidate(
                number=index + 1,
                text=text,
                box=rect,
                source="uia",
            )
            for index, (text, rect) in enumerate(kept)
        ]


def merge_candidate_sources(
    main: Sequence[GroundingCandidate],
    additional: Sequence[GroundingCandidate],
    *,
    iou_threshold: float = IOU_THRESHOLD_FOR_MERGE,
) -> list[GroundingCandidate]:
    """**主表全留 + 附加表去重**（UFO² 的混合检测合并规则）。

    * ``main`` —— **主表**（UIA 控件）：**全部保留**；
    * ``additional`` —— **附加表**（OCR 词框）：与**任一**主表项
      **IoU > 阈值**的**丢弃**，其余**追加在主表之后**；
    * 阈值抄 UFO² 官方 ``config/ufo/system.yaml`` 的
      ``IOU_THRESHOLD_FOR_MERGE: 0.1``；论文 §3.4："Visual detections with
      IoU greater than 10% against any UIA-derived control are discarded"；
    * 比较**一律在物理像素空间**（红线①：唯一真值 = 物理像素 × 虚拟桌面原点）。

    ⚠️ **方向不能反**：UIA 框与 OCR 框重叠时**保留 UIA 框**。
    绝不能学 OmniParser 的「图标框落在 OCR 框内就删图标」
    （官方 ``util/utils.py::remove_overlap_new()``）—— 那正好会**精确复现**
    「图标召回 0/13」。

    ⚠️ **被丢掉的 OCR 项，它的文字挂到保留下来的 UIA 项上**
    （方案原文：「OCR 文字可挂成它的 ``text``」）—— 框留 UIA 的、字取 OCR 的，
    两边都不浪费：图标控件（UIA 无名）因此**有了可指认的名字**。
    """
    kept: list[GroundingCandidate] = list(main)
    for candidate in additional:
        overlap_index = next(
            (
                index
                for index, other in enumerate(main)
                if iou(candidate.box, other.box) > iou_threshold
            ),
            None,
        )
        if overlap_index is None:
            kept.append(candidate)
            continue
        # 与主表项重叠 → **丢附加项，但把它的文字挂到主表项上**
        # （``kept`` 的前 ``len(main)`` 项与 ``main`` 一一对应）。
        other = kept[overlap_index]
        text = other.text
        if candidate.text and candidate.text not in text:
            text = f"{text}｜{candidate.text}" if text else candidate.text
        kept[overlap_index] = replace(other, text=text)
    return [
        GroundingCandidate(
            number=index + 1,
            text=candidate.text,
            box=candidate.box,
            source=candidate.source,
        )
        for index, candidate in enumerate(kept)
    ]


def candidate_coverage(
    candidates: Sequence[GroundingCandidate], truth_box: tuple[int, int, int, int]
) -> dict[str, Any]:
    """**候选覆盖率**三列：IoU@0.3 / IoU@0.5 / 「框中心是否落在真目标内」。

    ⚠️ **为什么必须单列**：公开纯 CV 方案在 IoU>0.9 口径下 F1 只有
    **0.154~0.201**（REMAUI 0.201 / Xianyu 0.154）→ 「图标能不能救」要
    **单独报覆盖率**，不能只看最终命中率。

    ⚠️ **比较在物理像素空间**（候选框与真值框同一空间）。
    """
    best = 0.0
    center_inside = False
    left, top, right, bottom = truth_box
    for candidate in candidates:
        best = max(best, iou(candidate.box, truth_box))
        center_x, center_y = candidate.center
        if left <= center_x <= right and top <= center_y <= bottom:
            center_inside = True
    return {
        "candidate_iou_max": best,
        "candidate_iou_at_0_3": best >= 0.3,
        "candidate_iou_at_0_5": best >= 0.5,
        "candidate_center_inside": center_inside,
    }


class OcrCandidateSource:
    """按目标描述的**文本子串**匹配 OCR 词 → 候选框（**物理像素**）。

    匹配规则（**中文按子串匹配，不做分词**）：

    * 取**最长的一段**相邻词（同一行、挨着的），这一段的拼接在描述里成串出现；
    * **规则 A** —— 这一段的文字**出现在描述里**（描述「保存」、界面上的「保存」）；
    * **规则 B** —— **描述整个出现在**这一段的文字里
      （描述「保存」、界面上的词是「保存全部」）；
    * 纯 ASCII 的词按**词边界**匹配（「as」不该命中「task」里的 as），
      含中文的词按**子串**匹配。

    参数
    ----
    reader
        词级读数的端口（一帧 → 一串词）。真机上是
        ``minicode.verification.ocr_windows.WindowsOcrPort().read_words``。
    max_candidates
        清单最多带多少项（提示词长度有上限）。超了**保留更具体的**
        （文字更长的那些）。
    """

    def __init__(self, reader: WordReader, *, max_candidates: int = 20):
        self.reader = reader
        self.max_candidates = int(max_candidates)

    def candidates(
        self, frame: CapturedFrame, description: str
    ) -> list[GroundingCandidate]:
        """这一帧上与描述匹配的候选（**物理像素**，编号 1 起）。

        读不出词（OCR 后端不可用）→ **异常往上抛**：上层据此判「判不了」，
        **不假装「没有候选」**。
        """
        words = list(self.reader(frame))
        matched = _match_words(words, description)
        # 帧像素 → 物理像素：**加上帧的原点**（唯一真值 = 物理像素 × 虚拟桌面原点）。
        physical = [
            GroundingCandidate(
                number=0,
                text=text,
                box=(
                    left + frame.left,
                    top + frame.top,
                    right + frame.left,
                    bottom + frame.top,
                ),
                source="ocr",
            )
            for text, (left, top, right, bottom) in matched
        ]
        kept = dedupe_by_iou(physical)
        # 清单太长时**保留更具体的**那些（文字更长的 = 更可能是目标）；
        # 同样长的按阅读顺序排（排序是稳定的）。
        kept = sorted(kept, key=lambda item: -len(item.text))[: self.max_candidates]
        return [
            GroundingCandidate(
                number=index + 1,
                text=candidate.text,
                box=candidate.box,
                source=candidate.source,
            )
            for index, candidate in enumerate(kept)
        ]


def _match_words(
    words: Sequence[OcrWordLike], description: str
) -> list[tuple[str, tuple[int, int, int, int]]]:
    """把 OCR 词里与描述对得上的挑出来（**帧像素空间**）。

    做法：**贪心地取最长的一段**相邻词（这一段的拼接在描述里成串出现），
    再判断这一段是不是目标：

    * **规则 A** —— 这一段的文字**出现在描述里**（如描述「保存」、界面上的词「保存」）；
    * **规则 B** —— **描述整个出现在**这一段的文字里
      （如描述「保存」、界面上的词是「保存全部」）。

    返回 ``[(文字, 框)]``，按 OCR 的阅读顺序。
    """
    target_compact = _compact(description)
    if len(target_compact) < MIN_MATCH_LENGTH:
        return []
    target_spaced = _spaced(description)
    hits: list[tuple[str, tuple[int, int, int, int]]] = []
    index = 0
    while index < len(words):
        run, text, box = _longest_run(words, index, target_compact)
        if _is_target(text, target_compact, target_spaced):
            hits.append((text, box))
            index += len(run)
        else:
            index += 1
    return hits


def _longest_run(
    words: Sequence[OcrWordLike], start: int, target_compact: str
) -> tuple[list[OcrWordLike], str, tuple[int, int, int, int]]:
    """从 ``start`` 开始**最长**的一段相邻词（拼接在描述里成串出现的那一段）。"""
    end = start
    box = _word_box(words[start])
    while end + 1 < len(words):
        following = _word_box(words[end + 1])
        if not _adjacent(box, following):
            break
        joined = _compact("".join(word.text for word in words[start : end + 2]))
        if joined not in target_compact:
            break
        end += 1
        box = _union_box(box, following)
    run = list(words[start : end + 1])
    return (run, _join_text(run), box)


def _join_text(run: Sequence[OcrWordLike]) -> str:
    """把一段相邻词拼回一段文字（ASCII 之间补空格，中文之间不补）。"""
    parts: list[str] = []
    for word in run:
        if parts and _ascii_alnum(parts[-1][-1]) and _ascii_alnum(word.text[:1]):
            parts.append(" ")
        parts.append(word.text)
    return "".join(parts)


def _is_target(text: str, target_compact: str, target_spaced: str) -> bool:
    """这一段文字是不是目标（规则 A / 规则 B）。"""
    compact = _compact(text)
    if len(compact) < MIN_MATCH_LENGTH:
        return False
    spaced = _spaced(text)
    # 规则 A：这一段的文字**出现在描述里**。
    if _in_description(compact, spaced, target_compact, target_spaced):
        return True
    # 规则 B：**描述整个出现在**这一段的文字里。
    return _in_description(target_compact, target_spaced, compact, spaced)


def _in_description(
    needle_compact: str,
    needle_spaced: str,
    haystack_compact: str,
    haystack_spaced: str,
) -> bool:
    """``needle`` 在 ``haystack`` 里吗？

    * 纯 ASCII 的词要**按词**出现 —— 「as」不该命中「task」里的 as；
    * 含中文的词按**子串**出现即可（中文不分词）。
    """
    if needle_compact not in haystack_compact:
        return False
    if not _is_ascii(needle_compact):
        return True
    if _spaced_occurrence(needle_spaced, haystack_spaced):
        return True
    # 两边的空格形态对不上（描述写「SaveAs」、界面写「Save As」）→ 放行。
    return needle_spaced not in haystack_spaced


def _spaced_occurrence(needle: str, haystack: str) -> bool:
    """``needle`` 在 ``haystack`` 里**按词边界**出现过吗？"""
    start = 0
    while True:
        index = haystack.find(needle, start)
        if index < 0:
            return False
        before = haystack[index - 1] if index > 0 else " "
        after = (
            haystack[index + len(needle)]
            if index + len(needle) < len(haystack)
            else " "
        )
        if not _ascii_alnum(before) and not _ascii_alnum(after):
            return True
        start = index + 1


def _compact(text: str) -> str:
    """**去掉所有空白**的归一化形式（NFKC + 小写 + 去空白）。"""
    return normalize_ocr_text(text)


def _spaced(text: str) -> str:
    """**保留单词边界**的归一化形式（NFKC + 小写 + 空白压成单个空格）。"""
    folded = unicodedata.normalize("NFKC", text or "").casefold()
    return " ".join(folded.split())


def _is_ascii(text: str) -> bool:
    """是不是纯 ASCII（纯 ASCII 的词按**词边界**匹配）。"""
    return text.isascii()


def _ascii_alnum(char: str) -> bool:
    """是不是 ASCII 的字母/数字（中文的「击」不算 —— 它不是词边界的一部分）。"""
    return bool(char) and char.isascii() and char.isalnum()


def _adjacent(
    left: tuple[int, int, int, int], right: tuple[int, int, int, int]
) -> bool:
    """两个词是不是**同一行上挨着的**（拼接只允许同行相邻）。

    OCR 的词表是「逐行、逐词」摊平的 —— 不看位置就会把**上一行末尾**和
    **下一行开头**拼成一个不存在的词。
    """
    left_top, left_bottom = left[1], left[3]
    right_top, right_bottom = right[1], right[3]
    if min(left_bottom, right_bottom) - max(left_top, right_top) <= 0:
        return False
    gap = max(left[0], right[0]) - min(left[2], right[2])
    height = max(left_bottom - left_top, right_bottom - right_top)
    return gap <= height


def _word_box(word: OcrWordLike) -> tuple[int, int, int, int]:
    """一个词的框 ``(left, top, right, bottom)``。"""
    return (int(word.x), int(word.y), int(word.x) + int(word.w), int(word.y) + int(word.h))


def _union_box(
    left: tuple[int, int, int, int], right: tuple[int, int, int, int]
) -> tuple[int, int, int, int]:
    """两个框的并集（相邻词拼接时用）。"""
    return (
        min(left[0], right[0]),
        min(left[1], right[1]),
        max(left[2], right[2]),
        max(left[3], right[3]),
    )

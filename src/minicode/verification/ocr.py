"""**OCR 通道**：文本比对这一路多出来的那个**来源**（工单 M27）。

三句话定住这一层：

* **OCR 是文本比对的一种实现，不是新的一种验证手段** ——
  三层断言的形态不变（状态变化 / 文本出现 / 文本消失 / 文本比对），
  只是「文本比对」这一路多了一个来源：**从画面上读**。
  手段还是三个（树 / 文本 / 像素），OCR 不占新的一格。
* **只读**：OCR 只读画面，**不产生动作、不注入输入**（工单 M27 AC 7）。
* **读不出 ≠ 不成立**：OCR 不可用 / 读不出 → **判不了**（三档里的第三档），
  绝不判成「期望不成立」（工单 M27 AC 5）。

> **词汇**（``CONTEXT.md`` 尚未收录，补词条属于领域文档的活儿，
> 见 ``docs/agents/domain.md``）：
>
> * **OCR 读数** —— 一次从画面上读到的文字，连同它读的是**哪一块**。
> * **真值来源**（``truth_source``）—— 观测值是从哪儿来的：
>   「画面量的」（从画面上量的）/「树里取的」（从可访问性树里取的）。
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol, runtime_checkable

from minicode.action.ports import CapturedFrame

__all__ = [
    "OcrStatus",
    "OcrReading",
    "OcrPort",
    "normalize_ocr_text",
    "TRUTH_SOURCE_SCREEN",
    "TRUTH_SOURCE_TREE",
    "TRUTH_SOURCE_NONE",
]


class OcrStatus(StrEnum):
    """一次 OCR 读数的状态。"""

    #: 读到了（``text`` 可能是空串 —— 那一块里没有字）。
    OK = "ok"
    #: **读不出来**（后端不可用 / 识别失败）→ 这一路**判不了**。
    UNAVAILABLE = "unavailable"


#: 真值来源：**画面量的**（从画面上量的）。
TRUTH_SOURCE_SCREEN = "画面量的"
#: 真值来源：**树里取的**（从可访问性树里取的）。
TRUTH_SOURCE_TREE = "树里取的"
#: 真值来源：**（没有）**（判不了 → 没有真值来源）。
TRUTH_SOURCE_NONE = "（没有）"


@dataclass(frozen=True)
class OcrReading:
    """一次 OCR 读数（**可复核**：区域坐标 + 读到的文本 + 区域来源）。

    * ``text`` —— 读到的文字（OCR 认出来的**原文**，不美化）；
    * ``region`` —— 读的是哪一块（**物理像素**矩形 ``(left, top, right, bottom)``）；
    * ``region_source`` —— 这一块的**来源**（人读：``整屏`` / ``控件框「缩放」`` …）——
      人工核对时靠它知道这块是哪儿来的；
    * ``status`` —— 读到了，还是读不出来；
    * ``backend`` —— 哪个 OCR 后端读的。
    """

    text: str = ""
    region: tuple[int, int, int, int] = (0, 0, 0, 0)
    region_source: str = ""
    status: OcrStatus = OcrStatus.OK
    backend: str = ""
    error: str = ""

    @property
    def readable(self) -> bool:
        """这一读数**读到了画面**吗（后端跑起来了）。

        读到了但**一个字都没有**也算读到了 —— 那说明**那一块里没有字**，
        这是一条**证据**（之前那一面因此可以说出「它当时不在」）。
        """
        return self.status is OcrStatus.OK

    @property
    def decisive(self) -> bool:
        """能不能据此说「**那一块里没有它**」。

        ⚠️ **读到了但一个字都没读出来** → **不能**（工单 M27 AC 5）：
        那块画面里没有字，可能是识别失败（空画面 / 字体太小），
        也可能真的没有 —— **不许拿它说「期望不成立」**。
        """
        return self.status is OcrStatus.OK and bool(self.text.strip())

    def to_dict(self) -> dict[str, object]:
        """落盘形状。"""
        return {
            "text": self.text,
            "region": list(self.region),
            "region_source": self.region_source,
            "status": self.status.value,
            "backend": self.backend,
            "error": self.error,
        }


@runtime_checkable
class OcrPort(Protocol):
    """OCR 端口：把一帧画面读成文字。

    **只读**：它只读画面，**不产生动作、不注入输入**。

    形状：**一帧 → 一段文字**。读不出来（后端不可用 / 识别失败）就**抛异常** ——
    上层据此判「判不了」，不是「没读到」。
    """

    name: str

    def read(self, frame: CapturedFrame) -> str:
        """读这一帧里的文字。"""
        ...


def normalize_ocr_text(text: str) -> str:
    """OCR 读数 → **可比**的文本（**两边都要过这一道**）。

    OCR 认出来的字与界面上的字常有**系统性**差别：全角百分号（``％``）与
    半角（``%``）、字里多出来的空格、大小写。这一道只做三件事：
    NFKC 归一 → **去掉所有空白** → 小写。

    ⚠️ **只作用于 OCR 这一路**：树里那一路的文本是原样比对的
    （工单 M11 的口径一个字没改）。
    """
    folded = unicodedata.normalize("NFKC", text or "")
    return "".join(folded.split()).casefold()

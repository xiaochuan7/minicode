"""抓屏端口的实现：把若干**截图源**接成**一个**截图端口。

这是动作执行缝内唯一的抓屏入口：动作执行前取的那一帧与 ``screenshot`` 动作
都走它，因此「跳过只拿到 0×0 画面的捕获源」只有一份实现。

> **词汇**（``CONTEXT.md`` 尚未收录，补词条属于领域文档的活儿，见
> ``docs/agents/domain.md``）：
>
> * **截图源** —— 端口 :class:`~minicode.action.ports.CaptureSource`：抓一帧整屏。
> * **截图端口** —— 端口 :class:`~minicode.action.ports.ScreenshotPort`：
>   抓一帧，**可以指定区域**。本模块的 :class:`CapturePort` 是它的实现。
> * **区域截图源** —— 端口 :class:`~minicode.action.ports.RegionCaptureSource`：
>   截图源里**能抓指定区域**的那种（不是每个截图源都能）。
>
> ⚠️ **与 ``CONTEXT.md`` 的「放大（zoom）」同词不同义**：放大是动作协议里的一个
> **动作**（回源重抓后送模型，模型仍按整屏坐标回答）；这里的 ``region``
> 只说「这一帧里截哪一块」，**不涉及送模型**。
"""

from __future__ import annotations

from collections.abc import Sequence

from .ports import CapturedFrame, CaptureSource, RegionCaptureSource

__all__ = ["CapturePort"]


class CapturePort:
    """把若干截图源接成一个截图端口（整屏 / 区域）。

    * 依次尝试各截图源，**跳过只拿到 0×0 画面的源**；
    * 给了区域时，只用**支持区域抓取**的源（其余源跳过）；
    * 最近一次被跳过的源名记在 :attr:`skipped`（观测数据，进追踪记录）。
    """

    def __init__(self, sources: Sequence[CaptureSource]):
        self.sources = tuple(sources)
        self.skipped: tuple[str, ...] = ()

    def screenshot(self, region: tuple[int, int, int, int] | None = None) -> CapturedFrame:
        skipped: list[str] = []
        for source in self.sources:
            if region is not None and not isinstance(source, RegionCaptureSource):
                continue
            frame = source.capture() if region is None else source.capture_region(region)
            if frame.is_empty:
                skipped.append(getattr(source, "name", type(source).__name__))
                continue
            self.skipped = tuple(skipped)
            return frame
        self.skipped = tuple(skipped)
        if region is not None:
            raise ValueError("没有捕获源支持区域截图（region）")
        return CapturedFrame(width=0, height=0, payload=b"")

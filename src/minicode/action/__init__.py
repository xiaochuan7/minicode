"""minicode.action —— 动作协议 v1 与动作执行缝（M0，M21 补齐协议缺口）。

对外入口：

* :mod:`minicode.action.protocol` —— 协议形状（版本号 / 动作种类 / 参数 / 结果 / 错误码）。
* :mod:`minicode.action.seam` —— **动作执行缝**（本项目唯一的测试缝）。
* :mod:`minicode.action.records` —— 动作记录（只落截图哈希，不落原图）。
* :mod:`minicode.action.capture` —— 截图端口的实现（整屏 / 区域；跳过 0×0 的源）。
* :mod:`minicode.action.fakes` —— 端口的假实现（截图源 / 控件树 / 输入注入 / 窗口控制 / 屏幕度量）。

> **词汇**：本包统一使用「动作」「最终落点」「动作记录」「动作执行缝」「截图」
> 「等待」这几个词。
>
> **本包自己引入的词**：
>
> * **截图源** —— 端口（:class:`~minicode.action.ports.CaptureSource`）：抓一帧整屏。
> * **截图端口** —— 端口（:class:`~minicode.action.ports.ScreenshotPort`）：
>   抓一帧，**可以指定区域**（工单 M21）。
> * **区域截图源** —— 端口（:class:`~minicode.action.ports.RegionCaptureSource`）：
>   截图源里**能抓指定区域**的那种。
> * **不需要落点的动作** —— ``screenshot`` 与 ``wait``：它们不定位目标、
>   没有最终落点（工单 M21；协议层叫 ``STANDALONE_KINDS``）。
>
> ⚠️ **与「放大（zoom）」同词不同义**：放大是动作协议里的一个
> **动作**（回源重抓后送模型，模型仍按整屏坐标回答）；``screenshot`` 动作的
> ``region`` 只说「这一帧里截哪一块」，**不涉及送模型**。
"""

from __future__ import annotations

__all__ = [
    "capture",
    "errors",
    "geometry",
    "protocol",
    "surface",
]

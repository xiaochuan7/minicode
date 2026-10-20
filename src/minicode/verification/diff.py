"""**界面无变化检测**：8×8 像素块 + 32 位精确比较 + 轮询节奏。

工单 M11 AC 2 的判据**写死**在这里：

* 比较用 **8×8 像素块**，每块压成一个 **32 位**值，**精确比较**（不近似、不模糊）；
* **变化率** = 变化的块数 ÷ 总块数，阈值 **1%**；
* 轮询节奏：**首次立即** → **100 ms 内 15 ms 节奏** → **之后 20 Hz**（50 ms）；
* 判定顺序：**有反应 → 安定 → 返回**；
* **变化率低于 1% 且持续两帧** → 报「**界面无变化**」。

两条语义边界（工单 M11 AC 3）由本模块的**返回值**承载：

* 「画面变化并安定」只说明**有反应**，**不等于成功**（判成功要靠期望断言）；
* 「界面本来就无变化」报的是 ``no_change`` 这个**信号**，**不是失败**
  （无变化 ≠ 失败 —— 对已置位控件再点一次就属于这种）。

⚠️ **本模块不判成功**：它只回答「界面有没有变、变了之后安定了没有」。
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from minicode.action.ports import CapturedFrame, PixelFormat, ScreenshotPort

__all__ = [
    "ChangeDetectParams",
    "ChangeResult",
    "frame_signature",
    "change_rate",
    "poll_change",
    "OUTCOME_CHANGED",
    "OUTCOME_NO_CHANGE",
]

#: 检出了变化（有反应且已安定）。
OUTCOME_CHANGED = "changed"
#: 没检出变化（变化率低于阈值且持续两帧，或等到超时仍无反应）。
OUTCOME_NO_CHANGE = "no_change"


@dataclass(frozen=True)
class ChangeDetectParams:
    """无变化检测的运行时参数。**每个阈值都带取值来源**（随记录落盘）。"""

    #: 像素块边长。
    block_size: int = 8
    block_size_source: str = "工单 M11 AC 2 写死的 8×8 像素块"
    #: 变化率阈值（0.01 = 1%）。
    change_threshold: float = 0.01
    change_threshold_source: str = "工单 M11 AC 2 写死的 1% 像素变化阈值"
    #: 首次轮询的延迟（毫秒）。**0 = 动作后立即抓第一帧**。
    first_poll_delay_ms: float = 0.0
    first_poll_delay_source: str = "工单 M11 AC 2 写死的「首次立即」"
    #: 100 ms 窗口内的轮询节奏（毫秒）。
    fast_interval_ms: float = 15.0
    fast_interval_source: str = "工单 M11 AC 2 写死的「100 ms 内 15 ms 节奏」"
    #: 「快节奏」窗口的长度（毫秒）。
    fast_window_ms: float = 100.0
    fast_window_source: str = "工单 M11 AC 2 写死的 100 ms 窗口"
    #: 稳态轮询节奏（毫秒）。**50 ms = 20 Hz**。
    steady_interval_ms: float = 50.0
    steady_interval_source: str = "工单 M11 AC 2 写死的「之后 20 Hz」"
    #: 等「有反应」的上限（毫秒）。超时按「界面无变化」报（并记 ``timed_out``）。
    reaction_timeout_ms: float = 2000.0
    reaction_timeout_source: str = "工程阈值·未验证（工单 M11 没写死等待上限）"
    #: 等「安定」的上限（毫秒）。超时按「变了但没安定」报（并记 ``timed_out``）。
    settle_timeout_ms: float = 2000.0
    settle_timeout_source: str = "工程阈值·未验证（工单 M11 没写死安定上限）"


@dataclass(frozen=True)
class ChangeResult:
    """一次无变化检测的结果。"""

    #: ``"changed"`` 或 ``"no_change"``。
    outcome: str
    #: 整轮里观测到的**最大**变化率（「界面上看到了多大变化」）。
    change_rate: float = 0.0
    #: 最后一帧的变化率（安定之后通常是 0）。
    last_change_rate: float = 0.0
    #: 轮询了多少帧。
    frames: int = 0
    #: 整轮耗时（毫秒）。
    elapsed_ms: float = 0.0
    #: 见到过反应吗（变化率越过阈值）。
    reacted: bool = False
    #: 安定了吗（连续两帧变化率低于阈值）。
    settled: bool = False
    #: 是不是等到超时才收的。
    timed_out: bool = False
    #: 最后那一帧的截图哈希（**只落哈希，不落原图**）。
    frame_sha256: str | None = None
    #: 这一轮用的**阈值与它们的取值来源**（随记录落盘 ——
    #: 仓库规矩：每个阈值都要能说清「这个数是哪来的」）。
    params: Mapping[str, Any] = field(default_factory=dict)

    @property
    def changed(self) -> bool:
        """界面上看到变化了吗（**不等于成功**）。"""
        return self.outcome == OUTCOME_CHANGED

    def to_dict(self) -> dict[str, object]:
        """落盘形状。"""
        return {
            "outcome": self.outcome,
            "change_rate": self.change_rate,
            "last_change_rate": self.last_change_rate,
            "frames": self.frames,
            "elapsed_ms": self.elapsed_ms,
            "reacted": self.reacted,
            "settled": self.settled,
            "timed_out": self.timed_out,
            "frame_sha256": self.frame_sha256,
            "params": dict(self.params),
        }


def _channels(frame: CapturedFrame) -> int:
    return 4 if frame.pixel_format is PixelFormat.BGRA else 3


def _pack(sums: list[int], count: int) -> int:
    """把一个块的各通道均值打包成 **32 位**值（蓝绿红透明度各 8 位）。"""
    blue = (sums[0] // count) & 0xFF
    green = (sums[1] // count) & 0xFF
    red = (sums[2] // count) & 0xFF
    alpha = (sums[3] // count) & 0xFF if len(sums) > 3 else 0xFF
    return (blue << 24) | (green << 16) | (red << 8) | alpha


def _signature_numpy(frame: CapturedFrame, block: int) -> tuple[int, ...]:
    """块签名（**numpy 快路径**，可选附加项）。"""
    import numpy as np

    channels = _channels(frame)
    if len(frame.payload) < frame.width * frame.height * channels:
        return ()
    array = np.frombuffer(frame.payload, dtype=np.uint8).reshape(
        frame.height, frame.width, channels
    )
    rows = frame.height // block
    cols = frame.width // block
    if rows == 0 or cols == 0:
        return ()
    blocks = array[: rows * block, : cols * block].reshape(
        rows, block, cols, block, channels
    ).mean(axis=(1, 3))
    blue = blocks[:, :, 0].astype(np.uint32)
    green = blocks[:, :, 1].astype(np.uint32)
    red = blocks[:, :, 2].astype(np.uint32)
    alpha = (
        blocks[:, :, 3].astype(np.uint32)
        if channels > 3
        else np.full(blocks[:, :, 0].shape, 255, dtype=np.uint32)
    )
    packed = (blue << 24) | (green << 16) | (red << 8) | alpha
    return tuple(int(value) for value in packed.reshape(-1))


def _signature_stdlib(frame: CapturedFrame, block: int) -> tuple[int, ...]:
    """块签名（**纯标准库**路径；没有 numpy 时走它）。"""
    channels = _channels(frame)
    payload = frame.payload
    stride = frame.width * channels
    if len(payload) < frame.height * stride:
        return ()
    values: list[int] = []
    for top in range(0, frame.height - block + 1, block):
        for left in range(0, frame.width - block + 1, block):
            sums = [0] * channels
            for row in range(top, top + block):
                base = row * stride + left * channels
                line = payload[base : base + block * channels]
                for channel in range(channels):
                    sums[channel] += sum(line[channel::channels])
            values.append(_pack(sums, block * block))
    return tuple(values)


def frame_signature(frame: CapturedFrame, block_size: int = 8) -> tuple[int, ...]:
    """一帧的**块签名**：每 ``block_size``×``block_size`` 块压成一个 32 位值。

    签名是**可比的**：同一块内容相同 → 32 位值相同（**精确比较**，不做模糊匹配）。
    """
    if frame.is_empty:
        return ()
    try:
        return _signature_numpy(frame, block_size)
    except ImportError:
        return _signature_stdlib(frame, block_size)


def change_rate(before: tuple[int, ...], after: tuple[int, ...]) -> float:
    """两帧的**变化率**（变化的块数 ÷ 总块数）。

    尺寸对不上（块数不同）时按**整幅都变了**算（1.0）—— 画面换了尺寸就是变了。
    """
    if not before or not after:
        return 1.0 if before != after else 0.0
    if len(before) != len(after):
        return 1.0
    changed = sum(
        1 for left, right in zip(before, after, strict=True) if left != right
    )
    return changed / len(before)


def _params_fields(settings: ChangeDetectParams) -> dict[str, Any]:
    """阈值与它们的**取值来源**（随记录落盘）。

    仓库的规矩：每个阈值都要能说清「这个数是哪来的」——
    来源串与阈值**成对**落盘，事后查得到。
    """
    return {
        "block_size": settings.block_size,
        "block_size_source": settings.block_size_source,
        "change_threshold": settings.change_threshold,
        "change_threshold_source": settings.change_threshold_source,
        "fast_interval_ms": settings.fast_interval_ms,
        "fast_interval_source": settings.fast_interval_source,
        "fast_window_ms": settings.fast_window_ms,
        "fast_window_source": settings.fast_window_source,
        "steady_interval_ms": settings.steady_interval_ms,
        "steady_interval_source": settings.steady_interval_source,
        "reaction_timeout_ms": settings.reaction_timeout_ms,
        "reaction_timeout_source": settings.reaction_timeout_source,
        "settle_timeout_ms": settings.settle_timeout_ms,
        "settle_timeout_source": settings.settle_timeout_source,
    }


def poll_change(
    screenshot_port: ScreenshotPort,
    baseline: CapturedFrame,
    params: ChangeDetectParams | None = None,
    *,
    clock: Callable[[], float] = time.perf_counter,
    sleeper: Callable[[float], None] = time.sleep,
    early_exit_on_no_change: bool = True,
) -> ChangeResult:
    """动作之后轮询画面，直到**有反应 → 安定**或收工。

    ``early_exit_on_no_change``
        **没有期望时**为真：变化率低于阈值且**持续两帧**就收工并报「界面无变化」
        （工单 M11 AC 2）。
        **有期望时**为假：变化可能来得很慢，早收工会把「还没生效」误判成「没生效」，
        因此等到超时为止再判期望。
    """
    settings = params or ChangeDetectParams()
    started = clock()
    previous = frame_signature(baseline, settings.block_size)
    reacted = False
    stable = 0
    frames = 0
    last_rate = 0.0
    max_rate = 0.0
    digest: str | None = None
    next_at = started + settings.first_poll_delay_ms / 1000.0

    while True:
        now = clock()
        if next_at > now:
            sleeper(next_at - now)
        frame = screenshot_port.screenshot()
        frames += 1
        digest = frame.digest()
        signature = frame_signature(frame, settings.block_size)
        last_rate = change_rate(previous, signature)
        previous = signature
        max_rate = max(max_rate, last_rate)

        if last_rate >= settings.change_threshold:
            reacted = True
            stable = 0
        else:
            stable += 1
            if reacted and stable >= 2:
                # 有反应 → 安定 → 返回。
                return ChangeResult(
                    outcome=OUTCOME_CHANGED,
                    change_rate=max_rate,
                    last_change_rate=last_rate,
                    frames=frames,
                    elapsed_ms=(clock() - started) * 1000.0,
                    reacted=True,
                    settled=True,
                    frame_sha256=digest,
                    params=_params_fields(settings),
                )
            if not reacted and stable >= 2 and early_exit_on_no_change:
                # 变化率低于阈值且持续两帧 → 「界面无变化」。
                return ChangeResult(
                    outcome=OUTCOME_NO_CHANGE,
                    change_rate=max_rate,
                    last_change_rate=last_rate,
                    frames=frames,
                    elapsed_ms=(clock() - started) * 1000.0,
                    reacted=False,
                    settled=True,
                    frame_sha256=digest,
                    params=_params_fields(settings),
                )

        elapsed = (clock() - started) * 1000.0
        if not reacted and elapsed >= settings.reaction_timeout_ms:
            return ChangeResult(
                outcome=OUTCOME_NO_CHANGE,
                change_rate=max_rate,
                last_change_rate=last_rate,
                frames=frames,
                elapsed_ms=elapsed,
                reacted=False,
                settled=False,
                timed_out=True,
                frame_sha256=digest,
                params=_params_fields(settings),
            )
        if reacted and elapsed >= settings.settle_timeout_ms:
            return ChangeResult(
                outcome=OUTCOME_CHANGED,
                change_rate=max_rate,
                last_change_rate=last_rate,
                frames=frames,
                elapsed_ms=elapsed,
                reacted=True,
                settled=False,
                timed_out=True,
                frame_sha256=digest,
                params=_params_fields(settings),
            )
        interval = (
            settings.fast_interval_ms
            if elapsed < settings.fast_window_ms
            else settings.steady_interval_ms
        )
        next_at = clock() + interval / 1000.0

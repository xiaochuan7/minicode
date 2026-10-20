"""校验层的假实现：截图端口（按脚本给画面）与定点观察端口。

有了它们，校验层的整条链路可以在**不碰真桌面、不需要凭据、不产生费用**的条件下被观测。
"""

from __future__ import annotations

from collections.abc import Sequence

from minicode.action.ports import CapturedFrame, ControlCandidate

__all__ = [
    "ScriptedScreenshotPort",
    "FakeStateObserver",
    "make_frame",
]


class ScriptedScreenshotPort:
    """按脚本给画面的截图端口。

    脚本用完后**重复最后一个**（画面不再变化）—— 正好是「安定」的样子。
    """

    def __init__(self, frames: Sequence[CapturedFrame] = ()):
        self.frames = list(frames)
        self.calls = 0

    def screenshot(self, region=None) -> CapturedFrame:
        self.calls += 1
        if not self.frames:
            return CapturedFrame(width=0, height=0, payload=b"")
        if len(self.frames) == 1:
            return self.frames[0]
        return self.frames.pop(0)


class FakeStateObserver:
    """按名字给观测结果的观察端口。

    ``controls``
        「名字 → 控件集」的脚本（**每次调用都给同一份**）。
    ``sequence``
        按次序给不同的观测（**第一份 = 动作之前（基线），第二份 = 动作之后**）——
        期望判的是**变化**，所以基线与之后要不一样。
    ``error``
        非空时**读树失败**（校验层据此判「判不了」，而不是「没找到」）。
    """

    def __init__(
        self,
        controls: dict[str, Sequence[ControlCandidate]] | None = None,
        error: Exception | None = None,
        sequence: Sequence[dict[str, Sequence[ControlCandidate]]] | None = None,
    ):
        self.controls = {key: tuple(value) for key, value in (controls or {}).items()}
        self.error = error
        self.sequence = [
            {key: tuple(value) for key, value in script.items()}
            for script in (sequence or ())
        ]
        self.calls = 0

    def observe(self, name: str) -> tuple[ControlCandidate, ...]:
        self.calls += 1
        if self.error is not None:
            raise self.error
        if self.sequence:
            index = min(self.calls - 1, len(self.sequence) - 1)
            return self.sequence[index].get(name, ())
        return self.controls.get(name, ())


def make_frame(
    width: int = 64,
    height: int = 64,
    changed_blocks: Sequence[int] = (),
    block_size: int = 8,
) -> CapturedFrame:
    """造一帧（BGR 像素）。

    ``changed_blocks`` 是「哪些 8×8 块被涂白」的序号（从 0 起，按行优先）。
    用来造「画面变了 / 没变」两种观测。
    """
    payload = bytearray(width * height * 3)
    blocks_per_row = max(1, width // block_size)
    for index in changed_blocks:
        left = (index % blocks_per_row) * block_size
        top = (index // blocks_per_row) * block_size
        for y in range(top, min(top + block_size, height)):
            for x in range(left, min(left + block_size, width)):
                offset = (y * width + x) * 3
                payload[offset : offset + 3] = b"\xff\xff\xff"
    return CapturedFrame(
        width=width,
        height=height,
        payload=bytes(payload),
        backend="fake",
        source="virtual-desktop",
    )

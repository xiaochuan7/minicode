"""标定探针：几何 + 探针图（**纯标准库**）。

**探针尺寸 = 本机物理表面尺寸**（M0 启动自检量出来的那一份，本机 2560×1600），
**源码里不写死任何探针默认值** —— 调用方把量到的物理尺寸传进来，
这里只按比例摆四个高对比目标。

四个目标摆在四角，**最右边那个贴着右缘**（真值 x ≥ 0.95 × 探针宽）：
0~999 与 0~1000 的物理分歧随 x 线性增长、最大值在右边缘，
探针不覆盖最坏情况时，「落点等价」判据会悄悄失效（工单 M5 口径说明）。

PNG 编码复用 :mod:`minicode.action.image`（**「一帧 → PNG」的唯一编码处**），
因此本包不引任何第三方依赖。
"""

from __future__ import annotations

from dataclasses import dataclass

from minicode.action.image import encode_png
from minicode.action.ports import CapturedFrame, PixelFormat

__all__ = [
    "ProbeTarget",
    "ProbeSpec",
    "default_probe_spec",
    "build_probe_frame",
    "probe_png",
    "probe_digest",
    "MARGIN_RATIO",
    "TARGET_SIZE_RATIO",
]

#: 目标方块的边长占探针宽的比例（96 / 2560）。
TARGET_SIZE_RATIO = 0.0375

#: 目标方块离最近那条边的距离占探针宽的比例（64 / 2560）。
MARGIN_RATIO = 0.025


@dataclass(frozen=True)
class ProbeTarget:
    """探针上的一个高对比目标。

    ``x`` / ``y`` 是目标**中心的物理像素坐标**（探针图按 1:1 物理像素绘制）。
    """

    name: str
    x: float
    y: float
    size: int


@dataclass(frozen=True)
class ProbeSpec:
    """探针的几何：尺寸 + 四个目标。"""

    width: int
    height: int
    targets: tuple[ProbeTarget, ...]

    @property
    def rightmost(self) -> ProbeTarget:
        """最靠右的目标（落点分歧最大的那个点）。"""
        return max(self.targets, key=lambda target: target.x)


def default_probe_spec(width: int, height: int) -> ProbeSpec:
    """按物理表面尺寸摆四个目标（**尺寸由调用方给**）。

    目标方块贴在四角内侧：离最近那条边 :data:`MARGIN_RATIO` × 宽，
    边长 :data:`TARGET_SIZE_RATIO` × 宽。于是最右目标的中心在
    ``0.95625 × 宽`` 处（≥ 0.95 × 宽，满足工单 M5 AC 1）。
    """
    if width <= 0 or height <= 0:
        raise ValueError(f"探针尺寸必须为正数，收到 {width}x{height}")
    size = max(8, round(width * TARGET_SIZE_RATIO))
    margin = max(4, round(width * MARGIN_RATIO))
    half = size / 2.0
    targets = (
        ProbeTarget("tl", margin + half, margin + half, size),
        ProbeTarget("tr", width - margin - half, margin + half, size),
        ProbeTarget("bl", margin + half, height - margin - half, size),
        ProbeTarget("br", width - margin - half, height - margin - half, size),
    )
    return ProbeSpec(width=width, height=height, targets=targets)


def build_probe_frame(spec: ProbeSpec) -> CapturedFrame:
    """把探针画成一帧画面（白底 + 实心深色方块）。

    像素排布按 ``BGR``（与真机抓屏一致，:mod:`minicode.action.image` 负责换通道）。
    """
    payload = bytearray(bytes((255, 255, 255)) * (spec.width * spec.height))
    stride = spec.width * 3
    dark = bytes((0, 0, 0))
    for target in spec.targets:
        half = target.size / 2.0
        x0 = max(0, int(round(target.x - half)))
        y0 = max(0, int(round(target.y - half)))
        x1 = min(spec.width, int(round(target.x + half)))
        y1 = min(spec.height, int(round(target.y + half)))
        row = dark * max(0, x1 - x0)
        for y in range(y0, y1):
            base = y * stride
            payload[base + x0 * 3 : base + x1 * 3] = row
    return CapturedFrame(
        width=spec.width,
        height=spec.height,
        left=0,
        top=0,
        payload=bytes(payload),
        backend="probe",
        source="calibration-probe",
        pixel_format=PixelFormat.BGR,
    )


def probe_png(spec: ProbeSpec) -> bytes:
    """探针图的 PNG 字节（送模型的那一张的等价物）。"""
    return encode_png(build_probe_frame(spec))


def probe_digest(frame: CapturedFrame) -> str:
    """探针图哈希（**只落它，不落原图**）。

    用 :meth:`minicode.action.ports.CapturedFrame.digest` —— 那是全仓库
    「截图哈希」的唯一读法，本模块不另写一份。
    """
    return frame.digest()

"""capture.py -- 屏幕截图，输出**物理像素**。

后端优先级
----------
1. ``mss`` —— 装了 ``mss`` 就用（更快、多显示器元数据更全）。
2. 纯 ctypes GDI ``BitBlt`` —— 没有 ``mss`` 时的降级路径，**零第三方依赖**。

两者在本机都返回**物理像素**（前提是进程已 PMv2 感知，见 dpi.py）。
``mss`` 会在 ``MSS()`` 构造时自己调 ``SetProcessDpiAwareness(2)``；如果本模块
先于它被 import，那 PMv2 已经设好，mss 的调用会返回 ACCESS_DENIED 并被忽略，
进程保持 PMv2 —— 正是我们想要的。

numpy 是可选的：没有 numpy 时 ``grab()`` 返回一个提供同样只读接口的
``RawFrame`` 垫片，脚本不会崩。
"""

from __future__ import annotations

import ctypes
import time
from ctypes import wintypes
from typing import Optional

if __package__:  # 包化：作为 minicode.exec 的一部分被导入
    from . import dpi  # noqa: F401  -- 导入即设置进程 DPI 感知（必须在最前）
else:  # 目录内直接运行时的平铺导入
    import dpi  # noqa: F401  -- 导入即设置进程 DPI 感知（必须在最前）

__all__ = [
    "grab",
    "grab_frame",
    "FrameMeta",
    "RawFrame",
    "backend_name",
    "has_numpy",
    "last_frame",
]

# ---------------------------------------------------------------------------
# 可选依赖探测
# ---------------------------------------------------------------------------

try:
    import numpy as _np
except Exception:  # pragma: no cover - 没装 numpy 也能跑
    _np = None

try:
    import mss as _mss
except Exception:  # pragma: no cover
    _mss = None


def has_numpy() -> bool:
    return _np is not None


def backend_name() -> str:
    """当前使用的截图后端：``"mss"`` 或 ``"gdi"``。"""
    return "mss" if _mss is not None else "gdi"


# ---------------------------------------------------------------------------
# 元数据
# ---------------------------------------------------------------------------


class FrameMeta:
    """一帧截图的元数据。

    坐标系：物理像素，原点 = 虚拟桌面左上角。
    """

    __slots__ = ("width", "height", "left", "top", "backend", "physical_pixels",
                 "timestamp", "source", "capture_ms")

    def __init__(self, width, height, left, top, backend, physical_pixels, source, timestamp):
        self.width = width
        self.height = height
        self.left = left
        self.top = top
        self.backend = backend
        self.physical_pixels = physical_pixels
        self.source = source
        self.timestamp = timestamp
        self.capture_ms = 0.0

    @property
    def right(self) -> int:
        return self.left + self.width

    @property
    def bottom(self) -> int:
        return self.top + self.height

    @property
    def region(self):
        """(left, top, width, height) 物理像素。"""
        return (self.left, self.top, self.width, self.height)

    def to_dict(self) -> dict:
        return {
            "width": self.width, "height": self.height,
            "left": self.left, "top": self.top,
            "right": self.right, "bottom": self.bottom,
            "backend": self.backend, "physical_pixels": self.physical_pixels,
            "source": self.source, "timestamp": self.timestamp,
        }

    def __repr__(self) -> str:
        return (f"FrameMeta({self.width}x{self.height} @ ({self.left},{self.top}) "
                f"backend={self.backend} physical={self.physical_pixels})")


class RawFrame:
    """没有 numpy 时的垫片：提供 width/height/shape 与原始 BGRA 字节。"""

    __slots__ = ("width", "height", "bgra")

    def __init__(self, width: int, height: int, bgra: bytes):
        self.width = width
        self.height = height
        self.bgra = bgra

    @property
    def shape(self):
        return (self.height, self.width, 3)

    def to_bytes(self) -> bytes:
        return self.bgra

    def __len__(self) -> int:
        return len(self.bgra)

    def __repr__(self) -> str:
        return f"RawFrame({self.width}x{self.height}, BGRA, no numpy)"


# ---------------------------------------------------------------------------
# GDI 后端（纯 ctypes）
# ---------------------------------------------------------------------------

_gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
_user32 = ctypes.WinDLL("user32", use_last_error=True)

SRCCOPY = 0x00CC0020
CAPTUREBLT = 0x40000000  # 连同分层/叠加窗口一起抓

_user32.GetDesktopWindow.restype = wintypes.HWND
_user32.GetWindowDC.argtypes = [wintypes.HWND]
_user32.GetWindowDC.restype = wintypes.HDC
_user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
_gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
_gdi32.CreateCompatibleDC.restype = wintypes.HDC
_gdi32.CreateCompatibleBitmap.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int]
_gdi32.CreateCompatibleBitmap.restype = wintypes.HBITMAP
_gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
_gdi32.SelectObject.restype = wintypes.HGDIOBJ
_gdi32.BitBlt.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                          ctypes.c_int, wintypes.HDC, ctypes.c_int, ctypes.c_int,
                          wintypes.DWORD]
_gdi32.BitBlt.restype = wintypes.BOOL
_gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
_gdi32.DeleteDC.argtypes = [wintypes.HDC]


class _BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG),
                ("biHeight", wintypes.LONG), ("biPlanes", wintypes.WORD),
                ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
                ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD),
                ("biClrImportant", wintypes.DWORD)]


class _BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", _BITMAPINFOHEADER), ("bmiColors", wintypes.DWORD * 3)]


# 必须在 _BITMAPINFO 定义之后再设置（argtypes 需要真实的类型对象）
_gdi32.GetDIBits.argtypes = [wintypes.HDC, wintypes.HBITMAP, wintypes.UINT,
                             wintypes.UINT, ctypes.c_void_p,
                             ctypes.POINTER(_BITMAPINFO), wintypes.UINT]
_gdi32.GetDIBits.restype = ctypes.c_int


def _gdi_grab(left: int, top: int, width: int, height: int) -> bytes:
    """从桌面 DC 抓一块矩形，返回 BGRA 字节。"""
    hdesk = _user32.GetDesktopWindow()
    hdc = _user32.GetWindowDC(hdesk)
    memdc = _gdi32.CreateCompatibleDC(hdc)
    bmp = _gdi32.CreateCompatibleBitmap(hdc, width, height)
    old = _gdi32.SelectObject(memdc, bmp)
    try:
        if not _gdi32.BitBlt(memdc, 0, 0, width, height, hdc, left, top,
                             SRCCOPY | CAPTUREBLT):
            raise ctypes.WinError(ctypes.get_last_error())
        bi = _BITMAPINFO()
        bi.bmiHeader.biSize = ctypes.sizeof(_BITMAPINFOHEADER)
        bi.bmiHeader.biWidth = width
        bi.bmiHeader.biHeight = -height  # 负高度 = 自上而下
        bi.bmiHeader.biPlanes = 1
        bi.bmiHeader.biBitCount = 32
        bi.bmiHeader.biCompression = 0    # BI_RGB
        buf = ctypes.create_string_buffer(width * height * 4)
        if _gdi32.GetDIBits(memdc, bmp, 0, height, buf, ctypes.byref(bi), 0) == 0:
            raise ctypes.WinError(ctypes.get_last_error())
        return buf.raw
    finally:
        _gdi32.SelectObject(memdc, old)
        _gdi32.DeleteObject(bmp)
        _gdi32.DeleteDC(memdc)
        _user32.ReleaseDC(hdesk, hdc)


# ---------------------------------------------------------------------------
# mss 后端
# ---------------------------------------------------------------------------

_mss_instance = None


def _get_mss():
    """惰性创建并复用 MSS 实例。

    不要每次都重建：``MSS()`` 构造函数里带 DPI 设置和资源分配。
    """
    global _mss_instance
    if _mss_instance is None:
        _mss_instance = _mss.mss()
    return _mss_instance


# ---------------------------------------------------------------------------
# 公共 API
# ---------------------------------------------------------------------------

#: 最近一帧的元数据（``grab()`` 之后更新）
last_frame: Optional[FrameMeta] = None


def grab(region=None):
    """抓取屏幕。

    Parameters
    ----------
    region:
        ``None`` -> 整个虚拟桌面；否则 ``(left, top, width, height)``，
        单位**物理像素**，原点在虚拟桌面左上角。

    Returns
    -------
    numpy.ndarray (H, W, 3) BGR，或没有 numpy 时的 :class:`RawFrame`。
    """
    global last_frame
    frame, meta = grab_frame(region)
    last_frame = meta
    return frame


def grab_frame(region=None):
    """和 :func:`grab` 一样，但返回 ``(frame, meta)``。"""
    ox, oy = dpi.virtual_desktop_origin()
    vw, vh = dpi.virtual_desktop_size()
    if region is None:
        left, top, width, height = ox, oy, vw, vh
        source = "virtual-desktop"
    else:
        left, top, width, height = (int(v) for v in region)
        if width <= 0 or height <= 0:
            raise ValueError(f"region 尺寸非法: {region!r}")
        source = "region"

    ts = time.perf_counter()
    if _mss is not None:
        bgra = _mss_grab(left, top, width, height)
        backend = "mss"
    else:
        bgra = _gdi_grab(left, top, width, height)
        backend = "gdi"
    elapsed_ms = (time.perf_counter() - ts) * 1000.0

    meta = FrameMeta(
        width=width, height=height, left=left, top=top,
        backend=backend,
        # 后端返回的是 DWM redirection surface 的原始像素；进程 PMv2 时
        # 这就是物理像素。用 capture_mss 的实测宽度与 physical_screen_size
        # 对比可独立验证（见 selftest.py）。
        physical_pixels=dpi.is_per_monitor_aware_v2(),
        source=source,
        timestamp=time.time(),
    )
    meta.capture_ms = elapsed_ms  # type: ignore[attr-defined]
    return _to_array(bgra, width, height), meta


def _mss_grab(left: int, top: int, width: int, height: int) -> bytes:
    sct = _get_mss()
    shot = sct.grab({"left": left, "top": top, "width": width, "height": height})
    return bytes(shot.bgra)


def _to_array(bgra: bytes, width: int, height: int):
    """BGRA 字节 -> numpy (H, W, 3) BGR（连续、可写）。"""
    if _np is None:
        return RawFrame(width, height, bgra)
    arr = _np.frombuffer(bgra, dtype=_np.uint8).reshape(height, width, 4)
    return _np.ascontiguousarray(arr[:, :, :3])  # 去掉 alpha -> BGR


def describe() -> dict:
    """后端与能力概览，供上层打印/自检。"""
    return {
        "backend": backend_name(),
        "numpy": has_numpy(),
        "mss_available": _mss is not None,
        "awareness": dpi.dpi_awareness_context(),
        "physical_pixels": dpi.is_per_monitor_aware_v2(),
        "virtual_desktop": (dpi.virtual_desktop_origin(), dpi.virtual_desktop_size()),
    }


if __name__ == "__main__":
    import json
    print(json.dumps(describe(), indent=2, ensure_ascii=False))
    frame, meta = grab_frame()
    print(json.dumps(meta.to_dict(), indent=2, ensure_ascii=False))
    print("frame:", getattr(frame, "shape", None))

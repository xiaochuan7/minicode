"""dpi.py -- 进程级 DPI 感知设置与本机 DPI 状态报告。

设计要点
--------
* 导入本模块即生效：调用 ``SetProcessDpiAwarenessContext(PER_MONITOR_AWARE_V2)``。
  这必须在**创建任何窗口之前**、且在 **import pyautogui 之前**发生，因为
  pyautogui 在 import 时就会调用 ``SetProcessDPIAware()``，mss 在 ``MSS()``
  构造时会调用 ``SetProcessDpiAwareness(2)``，二者都会**锁死**进程的 DPI 模式。
  一旦被锁死，再设 PMv2 会返回 ``ERROR_ACCESS_DENIED``。

* 坐标系约定：进程为 PMv2 时，Windows 交出的所有屏幕坐标都是**物理像素**，
  原点为虚拟桌面左上角。本模块的所有尺寸函数都返回物理像素。

* 只依赖标准库 + ctypes。
"""

from __future__ import annotations

import ctypes
import os
import sys
from ctypes import wintypes

__all__ = [
    "enable_dpi_awareness",
    "dpi_awareness_context",
    "is_per_monitor_aware_v2",
    "physical_screen_size",
    "virtual_desktop_origin",
    "virtual_desktop_size",
    "logical_screen_size",
    "scale_factor",
    "system_dpi",
    "scale_report",
    "format_report",
]

# ---------------------------------------------------------------------------
# Win32 常量
# ---------------------------------------------------------------------------

DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE = ctypes.c_void_p(-3)
DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = ctypes.c_void_p(-4)

SM_CXSCREEN = 0
SM_CYSCREEN = 1
SM_XVIRTUALSCREEN = 76
SM_YVIRTUALSCREEN = 77
SM_CXVIRTUALSCREEN = 78
SM_CYVIRTUALSCREEN = 79
SM_CMONITORS = 80

LOGPIXELSX = 88
LOGPIXELSY = 90
HORZRES = 8
VERTRES = 10

USER_DEFAULT_SCREEN_DPI = 96

user32 = ctypes.WinDLL("user32", use_last_error=True)

user32.GetSystemMetrics.argtypes = [ctypes.c_int]
user32.GetSystemMetrics.restype = ctypes.c_int

user32.GetDC.restype = wintypes.HDC
user32.GetDC.argtypes = [wintypes.HWND]
user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]

gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
gdi32.GetDeviceCaps.argtypes = [wintypes.HDC, ctypes.c_int]
gdi32.GetDeviceCaps.restype = ctypes.c_int

try:  # Win10 1607+
    user32.GetDpiForSystem.argtypes = []
    user32.GetDpiForSystem.restype = wintypes.UINT
except AttributeError:  # pragma: no cover - 老系统
    user32.GetDpiForSystem = None

try:  # Win10 1607+
    user32.GetDpiForWindow.argtypes = [wintypes.HWND]
    user32.GetDpiForWindow.restype = wintypes.UINT
except AttributeError:  # pragma: no cover
    user32.GetDpiForWindow = None

try:
    user32.AreDpiAwarenessContextsEqual.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    user32.AreDpiAwarenessContextsEqual.restype = wintypes.BOOL
except AttributeError:  # pragma: no cover
    user32.AreDpiAwarenessContextsEqual = None

try:
    user32.GetThreadDpiAwarenessContext.argtypes = []
    user32.GetThreadDpiAwarenessContext.restype = ctypes.c_void_p
except AttributeError:  # pragma: no cover
    user32.GetThreadDpiAwarenessContext = None


# ---------------------------------------------------------------------------
# 核心：设置进程 DPI 感知
# ---------------------------------------------------------------------------

_AWARENESS_SET = False


def enable_dpi_awareness() -> str:
    """把进程设成 Per-Monitor DPI Aware V2。

    返回实际生效的模式字符串：
    ``"PER_MONITOR_AWARE_V2"`` / ``"PER_MONITOR_AWARE"`` / ``"SYSTEM_AWARE"`` /
    ``"UNAWARE"``。

    幂等：重复调用安全。已经在 import pyautogui / mss 之后调用可能失败
    （Windows 允许每个进程只设置一次），此时如实返回当前实际模式。
    """
    global _AWARENESS_SET
    if _AWARENESS_SET:
        return dpi_awareness_context()

    # 首选：SetProcessDpiAwarenessContext（Win10 1703+，唯一能设 V2 的 API）
    try:
        user32.SetProcessDpiAwarenessContext.argtypes = [ctypes.c_void_p]
        user32.SetProcessDpiAwarenessContext.restype = wintypes.BOOL
        if user32.SetProcessDpiAwarenessContext(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2):
            _AWARENESS_SET = True
            return "PER_MONITOR_AWARE_V2"
        err = ctypes.get_last_error()
        if err != 5:  # 5 = ERROR_ACCESS_DENIED（已被别处设置）
            # 其它错误（如 87 非法参数）继续走降级路径
            pass
    except AttributeError:
        pass

    # 降级 1：shcore.SetProcessDpiAwareness(2) = PROCESS_PER_MONITOR_DPI_AWARE
    try:
        shcore = ctypes.WinDLL("shcore")
        shcore.SetProcessDpiAwareness.argtypes = [ctypes.c_int]
        shcore.SetProcessDpiAwareness.restype = ctypes.c_long
        if shcore.SetProcessDpiAwareness(2) == 0:
            _AWARENESS_SET = True
            return "PER_MONITOR_AWARE"
    except (OSError, AttributeError):
        pass

    # 降级 2：SetProcessDPIAware = System aware（Vista+）
    try:
        user32.SetProcessDPIAware.restype = wintypes.BOOL
        if user32.SetProcessDPIAware():
            _AWARENESS_SET = True
            return "SYSTEM_AWARE"
    except AttributeError:
        pass

    return dpi_awareness_context()


def dpi_awareness_context() -> str:
    """返回当前线程的 DPI 感知模式名称。"""
    if user32.GetThreadDpiAwarenessContext is None:
        return "UNKNOWN"
    ctx = user32.GetThreadDpiAwarenessContext()
    if ctx is None:
        return "UNAWARE"
    if user32.AreDpiAwarenessContextsEqual is None:
        return "UNKNOWN"
    if user32.AreDpiAwarenessContextsEqual(ctx, DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2):
        return "PER_MONITOR_AWARE_V2"
    if user32.AreDpiAwarenessContextsEqual(ctx, DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE):
        return "PER_MONITOR_AWARE"
    if user32.AreDpiAwarenessContextsEqual(ctx, ctypes.c_void_p(-2)):  # SYSTEM_AWARE
        return "SYSTEM_AWARE"
    return "UNAWARE"


def is_per_monitor_aware_v2() -> bool:
    """PMv2 是否真的生效。这是整个坐标协议成立的前提。"""
    return dpi_awareness_context() == "PER_MONITOR_AWARE_V2"


# ---------------------------------------------------------------------------
# 尺寸查询（全部返回物理像素，前提是 PMv2 已生效）
# ---------------------------------------------------------------------------


def physical_screen_size() -> tuple[int, int]:
    """主显示器的**物理**像素尺寸。

    PMv2 进程下 ``GetSystemMetrics(SM_CXSCREEN)`` 返回真实像素数；
    DPI-unaware 进程下会被虚拟化到 96DPI 空间（2560 -> 1707）。
    """
    return user32.GetSystemMetrics(SM_CXSCREEN), user32.GetSystemMetrics(SM_CYSCREEN)


def virtual_desktop_origin() -> tuple[int, int]:
    """虚拟桌面原点（物理像素）。副屏在主屏左侧/上方时为负数。"""
    return user32.GetSystemMetrics(SM_XVIRTUALSCREEN), user32.GetSystemMetrics(SM_YVIRTUALSCREEN)


def virtual_desktop_size() -> tuple[int, int]:
    """虚拟桌面尺寸（物理像素），即所有显示器合并的矩形大小。"""
    return (
        user32.GetSystemMetrics(SM_CXVIRTUALSCREEN),
        user32.GetSystemMetrics(SM_CYVIRTUALSCREEN),
    )


def system_dpi() -> int:
    """系统有效 DPI。150% 缩放 => 144。"""
    if user32.GetDpiForSystem is not None:
        dpi = user32.GetDpiForSystem()
        if dpi:
            return int(dpi)
    return _device_caps(LOGPIXELSX) or USER_DEFAULT_SCREEN_DPI


def scale_factor() -> float:
    """缩放比。150% => 1.5。"""
    return system_dpi() / float(USER_DEFAULT_SCREEN_DPI)


def logical_screen_size() -> tuple[int, int]:
    """逻辑分辨率（= 物理 / 缩放比，取整）。

    2560x1600 @150% => 1707x1067。
    """
    w, h = physical_screen_size()
    s = scale_factor()
    return int(round(w / s)), int(round(h / s))


def _device_caps(index: int) -> int:
    """GetDeviceCaps(hdcDesktop, index)。"""
    hdc = user32.GetDC(None)
    try:
        return int(gdi32.GetDeviceCaps(hdc, index))
    finally:
        user32.ReleaseDC(None, hdc)


def device_caps_report() -> dict:
    """GetDeviceCaps 视角的尺寸/DPI，用于与 GetSystemMetrics 交叉验证。"""
    return {
        "horzres": _device_caps(HORZRES),
        "vertres": _device_caps(VERTRES),
        "logpixelsx": _device_caps(LOGPIXELSX),
        "logpixelsy": _device_caps(LOGPIXELSY),
    }


def scale_report() -> dict:
    """一次性收集本机 DPI 状态。键名固定，便于上层做断言。"""
    phys = physical_screen_size()
    caps = device_caps_report()
    dpi = system_dpi()
    return {
        "awareness": dpi_awareness_context(),
        "awareness_ok": is_per_monitor_aware_v2(),
        "physical": phys,
        "logical": logical_screen_size(),
        "scale": dpi / float(USER_DEFAULT_SCREEN_DPI),
        "dpi": dpi,
        "device_caps_horzres": caps["horzres"],
        "device_caps_vertres": caps["vertres"],
        "device_caps_dpi": caps["logpixelsx"],
        "virtual_desktop_origin": virtual_desktop_origin(),
        "virtual_desktop_size": virtual_desktop_size(),
        "monitors": user32.GetSystemMetrics(SM_CMONITORS),
    }


def format_report(report: dict | None = None, ascii_only: bool = False) -> str:
    """把 scale_report() 渲染成人类可读的多行文本。

    ``ascii_only=True`` 时输出纯 ASCII，用于不支持 UTF-8 的控制台
    （本机控制台代码页为 cp936，中文会显示成乱码）。
    """
    r = report if report is not None else scale_report()
    pw, ph = r["physical"]
    lw, lh = r["logical"]
    ox, oy = r["virtual_desktop_origin"]
    vw, vh = r["virtual_desktop_size"]
    caps_match = (r["device_caps_horzres"] == pw and r["device_caps_vertres"] == ph)
    if ascii_only:
        lines = [
            "========== DPI STATUS REPORT ==========",
            f"  awareness          : {r['awareness']}"
            f"{'  [OK]' if r['awareness_ok'] else '  [!! expected PER_MONITOR_AWARE_V2]'}",
            f"  physical resolution: {pw} x {ph} px",
            f"  logical resolution : {lw} x {lh} px",
            f"  scale factor       : {r['scale']:.4g}x  (effective DPI {r['dpi']})",
            f"  GetDeviceCaps      : HORZRES={r['device_caps_horzres']} "
            f"VERTRES={r['device_caps_vertres']} LOGPIXELSX={r['device_caps_dpi']}"
            f"{'  [matches GetSystemMetrics]' if caps_match else '  [!! mismatch]'}",
            f"  virtual desktop    : origin=({ox}, {oy})  size={vw} x {vh} px",
            f"  monitors           : {r['monitors']}",
            "  coordinate space   : physical pixels, origin = virtual desktop top-left",
            "=======================================",
        ]
    else:
        lines = [
            "========== 本机 DPI 状态报告 ==========",
            f"  DPI 感知模式      : {r['awareness']}"
            f"{'  [OK]' if r['awareness_ok'] else '  [!! 期望 PER_MONITOR_AWARE_V2]'}",
            f"  物理分辨率        : {pw} x {ph} px",
            f"  逻辑分辨率        : {lw} x {lh} px",
            f"  缩放比            : {r['scale']:.4g}x   (有效 DPI {r['dpi']})",
            f"  GetDeviceCaps     : HORZRES={r['device_caps_horzres']} "
            f"VERTRES={r['device_caps_vertres']} LOGPIXELSX={r['device_caps_dpi']}"
            f"{'  [与 GetSystemMetrics 一致]' if caps_match else '  [!! 与 GetSystemMetrics 不一致]'}",
            f"  虚拟桌面          : 原点=({ox}, {oy})  尺寸={vw} x {vh} px",
            f"  显示器数量        : {r['monitors']}",
            "  坐标约定          : 物理像素，原点=虚拟桌面左上角",
            "=========================================",
        ]
    return "\n".join(lines)


def _print_report() -> None:
    """把报告打到 stderr（stdout 留给数据）。自动降级为 ASCII。"""
    report = scale_report()
    stream = sys.stderr
    enc = getattr(stream, "encoding", None) or "ascii"
    ascii_only = True
    try:
        format_report(report).encode(enc)
        ascii_only = False
    except (UnicodeEncodeError, LookupError):
        ascii_only = True
    try:
        print(format_report(report, ascii_only=ascii_only), file=stream)
    except Exception:  # pragma: no cover - 报告失败不应影响主流程
        pass


def _force_utf8_stdio() -> None:
    """让 stdout/stderr 统一输出 UTF-8。

    本机系统区域设置是中文（cp936），Python 在管道里会按 cp936 编码输出，
    被 UTF-8 消费方（CI、日志采集、终端）读成乱码。

    策略：
    * 非 tty（被重定向到管道/文件）-> 直接 reconfigure 成 UTF-8。
    * tty（真实控制台）-> 尝试把控制台代码页切成 65001(UTF-8) 再 reconfigure；
      失败则保持原生编码（此时中文在控制台上显示正常）。
    """
    if os.environ.get("GUI_CU_NO_UTF8", "") == "1":
        return
    try:
        import ctypes as _c
        _k = _c.WinDLL("kernel32")
        _k.SetConsoleOutputCP.argtypes = [_c.c_uint]
        _k.SetConsoleOutputCP.restype = _c.c_int
        _k.SetConsoleCP.argtypes = [_c.c_uint]
        _k.SetConsoleCP.restype = _c.c_int
    except Exception:
        pass

    for name in ("stdout", "stderr"):
        stream = getattr(sys, name, None)
        if stream is None:
            continue
        try:
            is_tty = stream.isatty()
        except Exception:
            is_tty = False
        if is_tty:
            try:
                _k.SetConsoleOutputCP(65001)
            except Exception:
                pass
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


# ---------------------------------------------------------------------------
# 导入即生效 + 打印报告
# ---------------------------------------------------------------------------

enable_dpi_awareness()
_force_utf8_stdio()

if os.environ.get("GUI_CU_DPI_QUIET", "").lower() not in ("1", "true", "yes"):
    _print_report()

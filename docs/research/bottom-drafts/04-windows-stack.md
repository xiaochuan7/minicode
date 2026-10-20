# 04 · Windows GUI 智能体：感知层与执行层的工程事实

> 目标机器：Windows，**物理 2560×1600，系统缩放 150%（逻辑 1707×1067，144 DPI）**，单显示器，RTX 3060 Laptop 6GB。
> 调研日期：2026-10-19。结论均标注来源；官方文档优先，第三方结论单独标注。
> 标记约定：✅ = 官方文档/实测事实；⚠️ = 我的推算或工程估算（未实测）；❌ = 常见但错误的说法。

---

## 0. 结论速查（TL;DR）

| 问题 | 结论 |
|---|---|
| 进程 DPI 感知怎么设 | **进程启动第一行**调 `SetProcessDpiAwarenessContext(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2)`（= `(HANDLE)-4`）。必须在**导入 pyautogui 之前**、创建任何窗口之前调用。官方更推荐 manifest，但 Python 走 API。 |
| `GetCursorPos` 返回什么 | 返回**屏幕坐标**；在 **DPI 感知进程**里=物理像素，在 **DPI-unaware 进程**里=被虚拟化的逻辑像素。→ 结论：**必须 PMv2**，否则 1.5× 偏差。 |
| `SendInput` + `MOUSEEVENTF_ABSOLUTE` 的 0..65535 | **不带** `MOUSEEVENTF_VIRTUALDESK` → 归一化到**主显示器**；**带** `MOUSEEVENTF_VIRTUALDESK` → 归一化到**整个虚拟桌面**。官方原文见下。 |
| 归一化公式 | `round(x*65535/(W-1))` —— **本机实测像素级精确**（6 目标点 × 12 次采样，误差全 0）；`x*65535//W` 有稳定 −1px 偏差。 |
| 统一坐标空间 | **物理像素 × 虚拟桌面原点**。PMv2 进程里，截图 / UIA bbox / DWM bounds / `GetCursorPos` / `SendInput` 全在同一空间，**不需要任何 ×1.5 换算**。 |
| 截图用哪个 | **mss（GDI BitBlt）做默认**（纯 Python、返回物理像素、无需用户授权、~10-30ms/帧）；**WGC 只在需要抓「特定窗口/被遮挡窗口/硬件加速内容」时上**（需用户点一次系统选择器）。 |
| 鼠标点击用哪个 | **SendInput**（唯一现代 API，`mouse_event` 官方标注 "superseded"）。必须：**移动 → 停顿 → down → 停顿 → up**。 |
| UIA 能用吗 | Win32/WinForms/WPF/UWP **能用**且给**物理像素** bbox；**Electron/Chromium/游戏/Canvas 自绘 基本不能用**（无语义树）。 |
| 6GB 显存跑 VLM | **Qwen3-VL-2B**（llama.cpp GGUF：LLM Q4_K_M ~1.1GB + mmproj Q8_0 ~0.43GB ≈ 1.5GB）跑得动；单步端到端 **≈3–8 s** ⚠️估算。Ollama `qwen3-vl:2b` 整包 1.76GB。**4B (3.07GB) 也塞得下但吃紧**。 |

---

# A. 坐标系与 DPI（最重要）

## A1. 术语与本机参数

| 名称 | 本机值 | 说明 |
|---|---|---|
| 物理分辨率 | 2560×1600 px | 面板真实像素 |
| 系统缩放 | 150% | 1 DIP = 1.5 px |
| 逻辑分辨率 | 1707×1067 | `2560/1.5=1706.67→1707`，`1600/1.5=1066.67→1067` |
| 有效 DPI | 144 | 96 × 1.5 |
| 虚拟桌面原点 | (0,0) | 单显示器，主屏即虚拟桌面左上角 |

**Windows 的两套坐标**（官方定义）：

> "The screen now contains two coordinate systems: physical and logical. The physical coordinates of a point are the actual offset in pixels from the top left of the origin. The logical coordinates are the offsets as they would be if the pixels themselves were scaled."
> — [UI Automation and Screen Scaling](https://learn.microsoft.com/en-us/dotnet/framework/ui-automation/ui-automation-and-screen-scaling)

## A2. 哪些 API 会被 DPI 虚拟化

**官方唯一权威表述**（Microsoft 明确承认没有完整清单）：

> "When an HWND or process is running as either DPI unaware or system DPI aware, it can be bitmap stretched by Windows. When this happens, Windows scales and converts DPI-sensitive information from some APIs to the coordinate space of the calling thread. For example, if a DPI-unaware thread queries the screen size while running on a high-DPI display, Windows will virtualize the answer given to the application as if the screen were in 96 DPI units. […] **this information is not currently sufficiently documented by Microsoft.** Be aware that if you call any system API from a DPI-unaware or system-DPI-aware thread context, the return value might be virtualized."
> — [High DPI Desktop Application Development on Windows](https://learn.microsoft.com/en-us/windows/win32/hidpi/high-dpi-desktop-application-development-on-windows) §"Lack of documentation about virtualization"

> "Registering a process as running in PMv2 awareness mode results in: […] **2. The application seeing the raw pixels of each display** […] 3. The application never being bitmap scaled by Windows."
> — 同上

**因此规则很简单：PMv2 进程里，所有坐标 API 都给物理像素。** 下面是具体的 API 行为表（行为是实测共识 + 官方片段推得）：

| API | DPI-unaware 进程 | PMv2 进程 | 备注 |
|---|---|---|---|
| `GetCursorPos` | **逻辑像素**（被虚拟化） | **物理像素** | 官方只说"screen coordinates"，虚拟化行为由上面的通用规则决定。✅ |
| `SetCursorPos` | 逻辑（被虚拟化） | 物理 | 同上；输入目标是"screen coordinates"。 |
| `GetWindowRect` | 逻辑 | 物理 | 另见 §A2.1（DWM 差异） |
| `GetSystemMetrics(SM_CXSCREEN)` | 逻辑（当 96 DPI 虚拟化） | 物理 | 官方页脚：**"This API is not DPI aware, and should not be used if the calling thread is per-monitor DPI aware."**（指 DPI 相关的 SM_* 值；显示器像素数在 PMv2 下=物理）✅ |
| `GetSystemMetrics(SM_XVIRTUALSCREEN/SM_CXVIRTUALSCREEN)` | 逻辑 | 物理 | 虚拟桌面度量，**多显示器必须用它**，不是 `SM_CXSCREEN` |
| `DwmGetWindowAttribute(DWMWA_EXTENDED_FRAME_BOUNDS)` | **物理**（不虚拟化） | 物理 | ✅ 微软工程师在 dotnet/winforms#4776 明确："this API is NOT DPI virtualized and returns raw physical pixels regardless of the caller's DPI awareness mode" |
| UIA `BoundingRectangle` / `GetClickablePoint` / `FromPoint` | **物理**（UIA 不用逻辑坐标） | 物理 | 官方："The UI Automation API does not use logical coordinates."✅ |
| DXGI / DDA / WGC 帧尺寸 | 取决于捕获进程 | 物理（PMv2 捕获进程） | Magpie 对比表：Graphics Capture / Desktop Duplication **不忽略** DPI 虚拟化；GDI / DwmSharedSurface **忽略** ✅ |
| `GetDeviceCaps(hdc, HORZRES)` | 逻辑 | 物理 | 与 `SM_CXSCREEN` 等值 |
| `Cursor.Position`（WinForms） | **逻辑** | — | 官方警告："Do not use Cursor.Position. The behavior of this property outside client windows in a scaled environment is undefined."✅ |

### A2.1 `GetWindowRect` vs `DwmGetWindowAttribute` 的差

- `GetWindowRect`：含 drop shadow，且 Win10+ 含**不可见的 resize border**（约 7px）。
- `DWMWA_EXTENDED_FRAME_BOUNDS`（枚举值 9）：**去掉** shadow 和 resize border，返回**物理像素**的"视觉边框"。
- 官方措辞："Retrieves the extended frame bounds rectangle in screen space."（[DWMWINDOWATTRIBUTE](https://learn.microsoft.com/en-us/windows/win32/api/dwmapi/ne-dwmapi-dwmwindowattribute)）
- ⚠️ 窗口从未显示过时该值无效。

**对智能体的意义**：要「点某个窗口的标题栏 X 按钮」，用 DWM 的 extended bounds 裁剪，误差 <7px；用 GetWindowRect 裁剪会偏。

## A3. 设置进程 DPI 感知

### 官方推荐顺序

> "We recommended that you specify the default process DPI awareness via a manifest setting. While specifying the default via API is supported, **it is not recommended**."
> — [Setting the default DPI awareness for a process](https://learn.microsoft.com/en-us/windows/win32/hidpi/setting-the-default-dpi-awareness-for-a-process)

> "**You must call this API before you call any APIs that depend on the DPI awareness (including before creating any UI in your process).** Once API awareness is set for an app, any future calls to this API will fail. This is true regardless of whether you set the DPI awareness in the manifest or by using this API."
> — [SetProcessDpiAwarenessContext](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-setprocessdpiawarenesscontext)

失败码：`ERROR_ACCESS_DENIED (5)` = 已经设过了（manifest 或先前 API 调用）；`ERROR_INVALID_PARAMETER (87)` = 非法 context。

### API 家族对照（官方表格）

| API | 最低 Windows | 可设置的模式 |
|---|---|---|
| `SetProcessDPIAware` | Vista | 仅 System aware |
| `SetProcessDpiAwareness`（shcore） | 8.1 | Unaware / System / **Per-Monitor（仅 V1）** |
| `SetProcessDpiAwarenessContext`（user32） | **10 1703** | Unaware / System / Per-Monitor / **Per-Monitor V2** ✅ 唯一能设 V2 的 |

### 做法 1：manifest（官方首选）

```xml
<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<assembly xmlns="urn:schemas-microsoft-com:asm.v1" manifestVersion="1.0" xmlns:asmv3="urn:schemas-microsoft-com:asm.v3">
  <asmv3:application>
    <asmv3:windowsSettings>
      <dpiAware xmlns="http://schemas.microsoft.com/SMI/2005/WindowsSettings">true</dpiAware>
      <dpiAwareness xmlns="http://schemas.microsoft.com/SMI/2016/WindowsSettings">PerMonitorV2</dpiAwareness>
    </asmv3:windowsSettings>
  </asmv3:application>
</assembly>
```
Win10 1607+ 优先读 `<dpiAwareness>`；`<dpiAware>` 作为老系统回退。

### 做法 2：各语言可用做法

| 语言 | 做法 | 注意 |
|---|---|---|
| **Python** | `ctypes` 调 `user32.SetProcessDpiAwarenessContext(c_void_p(-4))`，**放在 `__main__.py` 第一行** | ⚠️ **pyautogui 在 import 时就调 `SetProcessDPIAware()`**（我读了源码确认），所以 **必须先设 PMv2 再 import pyautogui**；反之 pyautogui 先导入会锁成 System-aware，之后 PMv2 调用返回 ACCESS_DENIED。 |
| **.NET (WinForms)** | .NET Core 3.0+：`Application.SetHighDpiMode(HighDpiMode.PerMonitorV2)`；或 MSBuild 属性 `<ApplicationHighDpiMode>PerMonitorV2</ApplicationHighDpiMode>`（生成 `ApplicationConfiguration.Initialize()`） | .NET Framework 4.7+：`app.config` 里 `<System.Windows.Forms.ApplicationConfigurationSection><add key="DpiAwareness" value="PerMonitorV2"/></...>`。⚠️ manifest 里的 `<dpiAware>` 会**覆盖** app.config。 |
| **.NET (WPF)** | manifest 声明 PMv2；处理 `WM_DPICHANGED` 用 suggested rect | WPF 4.6.2+ 支持 Per-Monitor |
| **C++ / Rust / Go** | manifest（`/MANIFESTUAC` / `mt.exe` / `winres` / `rsrc`）或入口第一行 `SetProcessDpiAwarenessContext` | 见下方代码 |
| **Java** | JDK 9+ 才有真 HiDPI（JEP 263）；`-Dsun.java2d.uiScale=auto`；**真正的 per-monitor v2 仍需 manifest**（`true/pm` + `permonitorv2`，用 `mt.exe` 注入或 `jpackage` 生成） | AWT/Swing **不处理 `WM_DPICHANGED`**，跨不同 DPI 显示器会错位。JDK 9 前是"声明 aware 但不真缩放"，UI 会被 Windows 位图拉伸变糊。 |
| **Electron** | 进程默认已是 per-monitor aware；`screen.getCursorScreenPoint()` 返回 **DIP 而非物理像素**，需用 `screen.dipToScreenPoint()` | — |

### Python 参考实现

```python
# dpi.py —— 必须在任何其他 import 之前 import 这个模块
import ctypes
from ctypes import wintypes

user32 = ctypes.WinDLL("user32", use_last_error=True)

# DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 == (DPI_AWARENESS_CONTEXT)(-4)
DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE   = ctypes.c_void_p(-3)
DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = ctypes.c_void_p(-4)

user32.SetProcessDpiAwarenessContext.argtypes = [ctypes.c_void_p]
user32.SetProcessDpiAwarenessContext.restype  = wintypes.BOOL
user32.AreDpiAwarenessContextsEqual.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
user32.AreDpiAwarenessContextsEqual.restype  = wintypes.BOOL
user32.GetThreadDpiAwarenessContext.argtypes = []
user32.GetThreadDpiAwarenessContext.restype  = ctypes.c_void_p


def set_dpi_awareness_per_monitor_v2() -> bool:
    """把进程设成 PMv2。返回 True=本次设置成功；False=已被别处设置（可能已是 PMv2）。
    必须在 import pyautogui / 创建任何窗口之前调用。"""
    try:
        if user32.SetProcessDpiAwarenessContext(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2):
            return True
        if ctypes.get_last_error() == 5:      # ERROR_ACCESS_DENIED: 已经设过了
            return False
    except AttributeError:                    # Win10 1703 之前
        pass
    try:
        shcore = ctypes.WinDLL("shcore")
        shcore.SetProcessDpiAwareness(2)       # PROCESS_PER_MONITOR_DPI_AWARE (V1)
        return True
    except OSError:
        pass
    return bool(user32.SetProcessDPIAware())   # Vista+ 兜底


def describe_dpi_awareness() -> dict:
    """当前线程的 DPI 感知上下文（Win10 1607+ 才有这些 API）。"""
    if not hasattr(user32, "GetThreadDpiAwarenessContext"):
        return {"supported": False}
    ctx = user32.GetThreadDpiAwarenessContext()
    return {
        "supported": True,
        "context": ctypes.c_void_p(ctx).value if ctx is not None else None,
        "is_per_monitor_v2": bool(user32.AreDpiAwarenessContextsEqual(
            ctx, DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2)),
        "is_per_monitor_v1": bool(user32.AreDpiAwarenessContextsEqual(
            ctx, DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE)),
    }
```

⚠️ **import 顺序是硬约束**：`pyautogui` 在 import 时就调用 `SetProcessDPIAware()`（我读了 `_pyautogui_win.py` 源码确认），`mss` 在 `MSS()` 构造时调用 `SetProcessDpiAwareness(2)`（PMv1）。两者都会**锁死**进程 DPI 模式。
→ **唯一正确的入口顺序**：

```python
# main.py
from dpi import set_dpi_awareness_per_monitor_v2   # ① 第一行，先设 PMv2
set_dpi_awareness_per_monitor_v2()

import mss                                        # ② 之后才 import 任何 GUI 库
import pyautogui
```

## A4. 三个必须钉死的事实

### 事实 1：`GetCursorPos` 返回物理像素还是虚拟化坐标？

**答：取决于调用进程的 DPI 感知模式。**

- 官方文档只说 "Retrieves the position of the mouse cursor, in screen coordinates." / "The cursor position is always specified in screen coordinates and is not affected by the mapping mode of the window that contains the cursor."（[GetCursorPos](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-getcursorpos)）—— **它没说 DPI**。
- DPI 行为由通用虚拟化规则决定（§A2）：DPI-unaware 线程拿到的返回值**可能是虚拟化的**。
- 结论：**PMv2 进程 → 物理像素**。这是本项目必须采用的姿势。
- 附带：`GetPhysicalCursorPos`（Vista+，API set `ext-ms-win-ntuser-gui-l1-1-1`）官方定位是 "Retrieves the position of the cursor in physical coordinates"，但 Windows 8.1 之后 `PhysicalToLogicalPoint`/`LogicalToPhysicalPoint` 已不再做变换，**所以它和 `GetCursorPos` 在 PMv2 下等价**，不能当作"绕过 DPI 虚拟化"的银弹。✅ [GetPhysicalCursorPos](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-getphysicalcursorpos)

### 事实 2：`MOUSEEVENTF_ABSOLUTE` 的 0..65535 相对于主显示器还是虚拟桌面？

**官方原文（[MOUSEINPUT](https://learn.microsoft.com/en-us/windows/win32/api/winuser/ns-winuser-mouseinput)）**：

> "If the **MOUSEEVENTF_ABSOLUTE** value is specified, dx and dy contain normalized absolute coordinates between 0 and 65,535. The event procedure maps these coordinates onto the display surface. Coordinate (0,0) maps onto the upper-left corner of the display surface; coordinate (65535,65535) maps onto the lower-right corner. **In a multimonitor system, the coordinates map to the primary monitor.**"
>
> "If the **MOUSEEVENTF_VIRTUALDESK** is specified, the coordinates map to the entire virtual desktop."
>
> 标志位表：`MOUSEEVENTF_VIRTUALDESK 0x4000` — "Maps coordinates to the entire desktop. **Must be used with MOUSEEVENTF_ABSOLUTE.**"

✅ 结论：

| flags | 归一化基准 | 归一化区间对应 |
|---|---|---|
| `ABSOLUTE` | **主显示器**（不是虚拟桌面） | 主显示器 (0,0)-(pw,ph) |
| `ABSOLUTE \| VIRTUALDESK` | **整个虚拟桌面** | 虚拟桌面 (vx,vy)-(vx+vw,vy+vh) |

⚠️ 微软文档**没有**写清楚「这个归一化是在物理像素还是虚拟化后的逻辑像素上算」。工程上的稳妥做法：**PMv2 进程 + 用物理像素的虚拟桌面度量去归一化 + 发送后回读 `GetCursorPos` 做闭环校正**（见 §A5.3）。

### 事实 3：多显示器怎么处理

- 归一化基准**必须**用 `SM_XVIRTUALSCREEN / SM_YVIRTUALSCREEN / SM_CXVIRTUALSCREEN / SM_CYVIRTUALSCREEN`（虚拟桌面原点 + 尺寸），**不是** `SM_CXSCREEN/SM_CYSCREEN`（那只是主显示器）。官方定义："The virtual screen is the bounding rectangle of all display monitors. The SM_XVIRTUALSCREEN metric is the coordinates for the left side of the virtual screen."（[GetSystemMetrics](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-getsystemmetrics)）
- 必须带 `MOUSEEVENTF_VIRTUALDESK`。
- 副屏在主屏左侧/上方时虚拟桌面原点是负数，必须先减掉原点再归一化。
- 本机单显示器：`vx=vy=0, vw=2560, vh=1600`，此时带不带 `VIRTUALDESK` 结果相同，**但仍应带**，为将来接第二屏留出正确性。

## A5. 推荐的统一坐标协议

### A5.1 协议定义

> **内部唯一坐标空间 = 物理像素 × 虚拟桌面原点**（即 Windows 的 "screen coordinates"，在 PMv2 进程里）。

这个空间同时满足：

| 数据源 | 在 PMv2 进程里给出的就是 | ✅ |
|---|---|---|
| 截图（mss / GDI BitBlt 物理分辨率） | 物理像素，原点=虚拟桌面 | 是 |
| WGC 帧（PMv2 捕获进程） | 物理像素 | 是 |
| UIA `BoundingRectangle` | 物理像素 | 是（官方："UIA does not use logical coordinates"） |
| `DwmGetWindowAttribute(DWMWA_EXTENDED_FRAME_BOUNDS)` | 物理像素 | 是 |
| `GetCursorPos` | 物理像素 | 是（PMv2 下） |
| `SetCursorPos` / `SendInput` 绝对坐标 | 接收物理像素 | 是 |

**结论：不需要任何缩放换算（×1.5 或 ÷1.5）。** 150% 缩放在这个协议下"消失"——因为所有生产者和消费者都活在物理像素里。

### A5.2 换算公式

**(a) 截图坐标 → 屏幕坐标**

```
# 抓取时记录抓取矩形的物理原点
grab = (gx, gy, gw, gh)          # 物理像素，原点在虚拟桌面
# 目标在截图位图中的像素坐标
screen_x = gx + px
screen_y = gy + py
```

**(b) 模型输出的归一化坐标 → 屏幕坐标**（VLM 常用 0..1000 或 0..1）

```
# 0..1 浮点
screen_x = gx + round(nx * gw)
screen_y = gy + round(ny * gh)
# 0..1000 整数（Qwen2.5-VL / Qwen3-VL 的 grounding 格式）
screen_x = gx + round(nx / 1000 * gw)
screen_y = gy + round(ny / 1000 * gh)
```

**(c) 截图被下采样后再定位**（VLM 前处理必做，否则坐标错位）

```
# 截图原始 gw×gh，下采样到 sw×sh，缩放系数 s = sw / gw
screen_x = gx + round(px_img * gw / sw)
```
即：**必须把缩放系数一起回传**，否则模型给的坐标是"小图坐标"。

**(d) 屏幕坐标 → `SendInput` 归一化坐标**

```
vx, vy, vw, vh = 虚拟桌面原点与尺寸（物理像素）
nx = round((screen_x - vx) * 65535 / (vw - 1))
ny = round((screen_y - vy) * 65535 / (vh - 1))
# flags = MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK
```

⚠️ 变体：部分实现（pyautogui、OpenJDK 修复）用 `65536 * x // W` 或 `x * 65535 // W` 形式。
**本机实测（6 目标点 × 12 次采样，2026-10-19）**：`round(x*65535/(W-1))` **像素级精确（误差 0）**；`x*65535//W` 稳定 **−1 px**；`x*65536//W` 在右下角 **−1 px**。→ **就用 `65535/(W-1)`**，详见 §A6.2。

### A5.3 完整实现（可直接抄）

```python
# coords.py
import ctypes
from ctypes import wintypes

user32 = ctypes.WinDLL("user32", use_last_error=True)
user32.GetSystemMetrics.argtypes = [wintypes.INT]
user32.GetSystemMetrics.restype  = wintypes.INT
user32.GetCursorPos.argtypes    = [ctypes.POINTER(wintypes.POINT)]
user32.GetCursorPos.restype     = wintypes.BOOL

SM_CXSCREEN, SM_CYSCREEN               = 0, 1
SM_XVIRTUALSCREEN, SM_YVIRTUALSCREEN   = 76, 77
SM_CXVIRTUALSCREEN, SM_CYVIRTUALSCREEN = 78, 79


def virtual_screen() -> tuple[int, int, int, int]:
    """(vx, vy, vw, vh) —— 虚拟桌面在**物理像素**下的原点与尺寸。
    前提：进程已 PMv2 感知（见 dpi.py）。单显示器时 vx=vy=0。"""
    vx = user32.GetSystemMetrics(SM_XVIRTUALSCREEN)
    vy = user32.GetSystemMetrics(SM_YVIRTUALSCREEN)
    vw = user32.GetSystemMetrics(SM_CXVIRTUALSCREEN)
    vh = user32.GetSystemMetrics(SM_CYVIRTUALSCREEN)
    return vx, vy, vw, vh


def to_normalized(screen_x: int, screen_y: int) -> tuple[int, int]:
    """物理屏幕坐标 -> SendInput 的 0..65535 归一化坐标（相对虚拟桌面）。"""
    vx, vy, vw, vh = virtual_screen()
    nx = round((screen_x - vx) * 65535 / max(vw - 1, 1))
    ny = round((screen_y - vy) * 65535 / max(vh - 1, 1))
    return max(0, min(65535, nx)), max(0, min(65535, ny))


def img_to_screen(gx: int, gy: int, px: float, py: float,
                  scale: float = 1.0) -> tuple[int, int]:
    """截图内坐标 -> 物理屏幕坐标。
    gx,gy  = 抓取矩形的物理原点
    px,py  = 目标在**原始分辨率**截图中的坐标
    scale  = 送进模型前对截图的下采样系数（模型坐标 = 原图坐标 * scale）
    """
    return (gx + round(px / scale), gy + round(py / scale))
```

**闭环校正（强烈建议加上）**：绝对坐标归一化到 16 bit 存在 ≤1 px 量化误差，且微软未定义其与 DPI 的交互。**发送后回读 `GetCursorPos`，有偏差就再发一次绝对移动**：

```python
def move_exact(screen_x: int, screen_y: int, tries: int = 2) -> tuple[int, int]:
    """绝对移动 + 回读校正。返回回读到的实际位置。"""
    from input_win import send_mouse_absolute          # 见 §C1
    for _ in range(tries):
        send_mouse_absolute(screen_x, screen_y)
        ax, ay = get_cursor_pos()
        if abs(ax - screen_x) <= 1 and abs(ay - screen_y) <= 1:
            return ax, ay
    return get_cursor_pos()
```

⚠️ **不要用相对移动做校正**：`MOUSEINPUT` 官方明确 "Relative mouse motion is subject to the effects of the mouse speed and the two-mouse threshold values… the system may multiply specified relative mouse motion by up to four times"（加速曲线会放大）。相对移动只用于**故意**的滚动/拖拽增量。

## A6. 本机实测验证（2026-10-19，在目标机器上跑的）

以下结果**在本机实测得到**（2560×1600 / 150% / 单显示器 / Windows），不是推断。

### A6.1 DPI 虚拟化：同一台机器的两个视角

| 进程 | DPI 感知 | `GetSystemMetrics(SM_CXSCREEN/CYSCREEN)` | `GetDeviceCaps(LOGPIXELSX)` |
|---|---|---|---|
| PowerShell（未声明感知） | Unaware | **1707 × 1067** | **96** |
| Python（`SetProcessDpiAwarenessContext(-4)` 后） | **PMv2** | **2560 × 1600** | 144 |

虚拟桌面度量（PMv2 进程）：`SM_XVIRTUALSCREEN=0`、`SM_YVIRTUALSCREEN=0`、`SM_CXVIRTUALSCREEN=2560`、`SM_CYVIRTUALSCREEN=1600`、`SM_CMONITORS=1`。

👉 **这就是 1.5× 偏差的来源**：DPI-unaware 进程拿到的所有屏幕度量都被 ÷1.5。**不设 PMv2 就没有"统一坐标空间"可言。**

### A6.2 绝对坐标归一化公式：三选一实测

测试方法：每个目标点重复 12 次 → `SendInput` 绝对移动 → 立即 `GetCursorPos` 回读 → 取**众数误差**（消除真实用户移动鼠标的干扰）。

| 公式 | (0,0) | (100,100) | (640,400) | (1280,800) | (2000,1200) | (2559,1599) |
|---|---|---|---|---|---|---|
| **A. `round(x*65535/(W-1))`** | **0** | **0** | **0** | **0** | **0** | **0** |
| B. `x*65535//W` | 0 | −1 | −1 | −1 | −1 | −1 |
| C. `x*65536//W` | 0 | 0 | 0 | 0 | 0 | −1（角落） |

**结论：公式 A 在本机 6 个目标点 × 12 次采样中全部像素级精确（众数误差 0，命中率 12/12）。** 公式 B 有稳定 −1 px 偏差，公式 C 在右下角有 −1 px 偏差。

> ⚠️ 读数口径：实测**不需要**闭环校正就能精确命中。但工程上仍**建议保留回读校正**——因为真实用户可能在两帧之间移动鼠标，坐标会被"抢走"。校正时用**第二次绝对移动**（不要用相对移动，见上）。

### A6.3 `SendInput` 的 ctypes 结构体陷阱（实测踩到）

- `INPUT` **必须用 union**（`MOUSEINPUT` / `KEYBDINPUT` 二选一）。若写成两个独立 struct 成员，x64 下 `sizeof(INPUT)` = **64**（正确值是 **40**），`SendInput` 因 `cbSize` 不匹配**直接返回 0 且不报错**——所有点击静默失效。
- x64 实测：`sizeof(MOUSEINPUT)=32`、`sizeof(KEYBDINPUT)=24`、`sizeof(INPUT)=40`；x86：`28`。
- **必须显式声明 `argtypes`/`restype`**：ctypes 默认按 `c_int` 返回，`HDC`/`HBITMAP` 句柄在 64 位下会被截断（`GetWindowDC` 尤其容易踩）。
- `SendInput` 返回 0 有两种原因：UIPI 阻断（**`GetLastError` 不指示**）或 `cbSize` 错。**必须检查返回值**。

### A6.4 复现脚本（可直接跑）

```python
# verify_dpi.py —— 在目标机器上自检 DPI 与坐标链路
import ctypes, collections
from ctypes import wintypes

user32 = ctypes.WinDLL("user32", use_last_error=True)
user32.SetProcessDpiAwarenessContext.argtypes = [ctypes.c_void_p]
user32.SetProcessDpiAwarenessContext.restype  = wintypes.BOOL
user32.GetSystemMetrics.argtypes = [wintypes.INT]
user32.GetSystemMetrics.restype  = wintypes.INT
user32.GetCursorPos.argtypes    = [ctypes.POINTER(wintypes.POINT)]
user32.GetCursorPos.restype     = wintypes.BOOL

assert user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)) or \
       ctypes.get_last_error() == 5, "PMv2 设置失败"

gsm = user32.GetSystemMetrics
VX, VY, VW, VH = gsm(76), gsm(77), gsm(78), gsm(79)
print("virtual screen:", VX, VY, VW, VH)      # 期望 0 0 2560 1600

def cursor():
    p = wintypes.POINT(); user32.GetCursorPos(ctypes.byref(p)); return p.x, p.y

def norm(x, y):
    return (max(0, min(65535, round((x - VX) * 65535 / (VW - 1)))),
            max(0, min(65535, round((y - VY) * 65535 / (VH - 1)))))

# ... 构造 INPUT（union 版，见 §C1）后：
for tx, ty in [(0, 0), (VW - 1, VH - 1), (VW // 2, VH // 2)]:
    nx, ny = norm(tx, ty)
    send_absolute(nx, ny)
    ax, ay = cursor()
    print(f"target=({tx},{ty}) actual=({ax},{ay}) delta=({ax-tx},{ay-ty})")
```
> 实测输出：`target=(0,0) actual=(0,0) delta=(0,0)`、`target=(2559,1599) actual=(2559,1599) delta=(0,0)`、`target=(1280,800) actual=(1280,800) delta=(0,0)`。

---

# B. 感知层

## B1. 截图 API 对比

| 方案 | 分辨率 | 延迟 | 黑屏/受保护内容 | 需要 DPI 感知 | 需要用户授权 | 适合场景 |
|---|---|---|---|---|---|---|
| **GDI BitBlt**（mss / Pillow ImageGrab） | 物理像素（PMv2 下） | ~5–30 ms/帧 | 硬件加速窗口一般 OK（GDI 抓的是 DWM redirection surface）；**DRM 受保护内容=黑** | 是 | 否 | ✅ **默认选择** |
| **Windows Graphics Capture (WGC)** | 物理像素（PMv2 捕获进程） | ~1 帧（16ms@60Hz）+ 拷贝开销 | 可抓**被遮挡/最小化/硬件加速**窗口；DRM 仍黑 | 是 | **是**（系统选择器弹窗 + 黄色边框） | 抓特定窗口 |
| **DXGI Desktop Duplication (DDA)** | 物理像素 | ~1 帧 | 同上；但**只能抓显示器**，不能抓单个窗口 | 是 | 否 | 全屏录制/流 |
| **PrintWindow** | 取决于窗口 | 慢 | **硬件加速/DirectComposition/分层窗口返回黑或空白** | 是 | 否 | ❌ 不推荐 |

**Magpie 官方对比表**（[Comparison of capture methods](https://github.com/Blinue/Magpie/blob/dev/docs/Comparison%20of%20capture%20methods.md)）：

| | Graphics Capture | Desktop Duplication | GDI | DwmSharedSurface |
|---|---|---|---|---|
| Supports DirectComposition (e.g. UWP) | Yes | Yes | No | No |
| Supports recording/streaming | No under extreme conditions | No | No | No |
| Supports source window spanning multiple screens | No under extreme conditions | No | No | Yes |
| **Ignores DPI virtualization** | **No** | **No** | **Yes** | **Yes** |
| Not recommended due to unstable performance | Requires Win10 v2004 | | | Not recommended |

> 注：GDI/DwmSharedSurface "ignores DPI virtualization" 意味着它们**抓的是缩放前/原始画面**；Graphics Capture 给出的是系统**双三次插值放大后**的画面。截图尺寸还是以捕获进程的 DPI 感知为准。

**关于 WGC 抓不到的东西**：
- DRM / PlayReady / Widevine 受保护内容 → 只能拿到黑帧（三个方案都一样）。
- **UAC 安全桌面**（见 §C3）→ 全部方案都抓不到。
- WGC 的 `GraphicsCaptureItem.Size` 在 DPI-unaware 捕获进程里是虚拟化后的逻辑尺寸 → **捕获进程也必须 PMv2**。

### 最小可用 Python 示例（mss，返回物理像素）

```python
# capture_mss.py
import numpy as np
try:                       # mss >= 10.2 的新 API；11.0 起 mss.mss() 已废弃
    from mss import MSS
except ImportError:        # 老版本
    from mss import mss as MSS


def _geom(mon) -> tuple[int, int, int, int]:
    """mss>=10 用属性，老版本 Monitor 是 dict。用属性/字典都兼容。"""
    if isinstance(mon, dict):
        return mon["left"], mon["top"], mon["width"], mon["height"]
    return mon.left, mon.top, mon.width, mon.height


def grab_desktop() -> tuple[np.ndarray, tuple[int, int, int, int]]:
    """返回 (BGR ndarray, (gx, gy, gw, gh))，坐标=物理像素、原点=虚拟桌面。"""
    with MSS() as sct:
        mon = sct.monitors[0]        # 0 = 全部显示器合并的虚拟桌面；1..n = 各显示器
        shot = sct.grab(mon)
        arr = np.frombuffer(shot.bgra, dtype=np.uint8).reshape(shot.height, shot.width, 4)
        return arr[:, :, :3], _geom(mon)   # BGRA -> BGR（OpenCV/RapidOCR 直接吃）


if __name__ == "__main__":
    img, rect = grab_desktop()
    print("shape", img.shape, "rect", rect)   # 预期 (1600, 2560, 3) (0, 0, 2560, 1600)
```

> mss 用法要点（[官方文档](https://python-mss.readthedocs.io/latest/usage.html)）：
> - **不要每次 `with MSS() as sct:` 重建实例**——`MSS()` 构造里有 DPI 设置，重建浪费；把实例存成成员复用。
> - `sct.monitors[0]` = 虚拟桌面（全部显示器合并），`sct.primary_monitor` = 主显示器。
> - `MSS` 是**线程安全**的，可以多线程共用一个实例。

⚠️ **mss 的 DPI 行为（我读了源码确认）**：`mss/src/mss/windows/gdi.py` 的 `_set_dpi_awareness()` 在 `MSS()` 实例化时调用
`ctypes.windll.shcore.SetProcessDpiAwareness(2)`（= `PROCESS_PER_MONITOR_DPI_AWARE`，**V1 不是 V2**），失败则回退 `SetProcessDPIAware()`。
→ **mss 自己会把进程设成 DPI-aware**，所以 `sct.grab()` 返回物理像素 ✅（[python-mss issue #197](https://github.com/BoboTiG/python-mss/issues/197) 里维护者明确说明："mss instance will set High DPI awareness = PROCESS_PER_MONITOR_DPI_AWARE. Once mss instance created, the PROCESS is DPI aware, then you got the actual resolution 2560x1600."）

⚠️ **但这个"自动设置"会锁死进程 DPI 模式**：如果先 import 了 pyautogui（它 import 时就调 `SetProcessDPIAware()`），mss 的 PMv1 设置会失败（ACCESS_DENIED，被忽略），进程停留在 **System-aware**。
→ **本项目规则：入口第一行显式设 PMv2，mss/pyautogui 都在其后 import。**

### 纯 ctypes 的 GDI BitBlt 兜底（无第三方依赖）

```python
# capture_gdi.py  —— 纯 ctypes，无第三方依赖。需要 PMv2 进程；返回物理像素
import ctypes
from ctypes import wintypes
from coords import virtual_screen

user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32  = ctypes.WinDLL("gdi32",  use_last_error=True)

SRCCOPY   = 0x00CC0020
CAPTUREBLT = 0x40000000      # 包含所有叠加层（分层窗口）

# ⚠️ 64 位下必须显式声明 restype/argtypes，
#    否则 ctypes 默认按 c_int 返回，会把 HDC/HBITMAP 句柄截断成 32 位。
user32.GetDesktopWindow.restype            = wintypes.HWND
user32.GetWindowDC.argtypes                = [wintypes.HWND]
user32.GetWindowDC.restype                 = wintypes.HDC
user32.ReleaseDC.argtypes                  = [wintypes.HWND, wintypes.HDC]
gdi32.CreateCompatibleDC.argtypes           = [wintypes.HDC]
gdi32.CreateCompatibleDC.restype            = wintypes.HDC
gdi32.CreateCompatibleBitmap.argtypes       = [wintypes.HDC, ctypes.c_int, ctypes.c_int]
gdi32.CreateCompatibleBitmap.restype        = wintypes.HBITMAP
gdi32.SelectObject.argtypes                 = [wintypes.HDC, wintypes.HGDIOBJ]
gdi32.SelectObject.restype                  = wintypes.HGDIOBJ
gdi32.BitBlt.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                         wintypes.HDC, ctypes.c_int, ctypes.c_int, wintypes.DWORD]
gdi32.BitBlt.restype  = wintypes.BOOL
gdi32.GetDIBits.argtypes = [wintypes.HDC, wintypes.HBITMAP, wintypes.UINT, wintypes.UINT,
                            ctypes.c_void_p, ctypes.POINTER(BITMAPINFO), wintypes.UINT]
gdi32.GetDIBits.restype  = wintypes.INT
gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
gdi32.DeleteDC.argtypes     = [wintypes.HDC]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG),
                ("biHeight", wintypes.LONG), ("biPlanes", wintypes.WORD),
                ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
                ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD),
                ("biClrImportant", wintypes.DWORD)]

class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wintypes.DWORD * 3)]


def grab_desktop_bgra() -> tuple[bytes, int, int, int, int]:
    """返回 (BGRA 字节流, width, height, x_src, y_src)。坐标=物理像素。"""
    vx, vy, vw, vh = virtual_screen()
    hdesk = user32.GetDesktopWindow()
    hdc   = user32.GetWindowDC(hdesk)
    memdc = gdi32.CreateCompatibleDC(hdc)
    bmp   = gdi32.CreateCompatibleBitmap(hdc, vw, vh)
    old   = gdi32.SelectObject(memdc, bmp)
    gdi32.BitBlt(memdc, 0, 0, vw, vh, hdc, vx, vy, SRCCOPY | CAPTUREBLT)

    bi = BITMAPINFO()
    bi.bmiHeader.biSize       = ctypes.sizeof(BITMAPINFOHEADER)
    bi.bmiHeader.biWidth      = vw
    bi.bmiHeader.biHeight     = -vh        # 负高度 = 自上而下
    bi.bmiHeader.biPlanes     = 1
    bi.bmiHeader.biBitCount   = 32
    bi.bmiHeader.biCompression = 0          # BI_RGB
    buf = ctypes.create_string_buffer(vw * vh * 4)
    gdi32.GetDIBits(memdc, bmp, 0, vh, buf, ctypes.byref(bi), 0)

    gdi32.SelectObject(memdc, old)         # 还原旧位图
    gdi32.DeleteObject(bmp)
    gdi32.DeleteDC(memdc)
    user32.ReleaseDC(hdesk, hdc)
    return buf.raw, vw, vh, vx, vy
```

### WGC（C#/.NET，WinAppSDK 或 CsWinRT）

要点（依据 [Screen capture](https://learn.microsoft.com/en-us/windows/apps/develop/media-authoring-processing/screen-capture) 与 [robmikh/Win32CaptureSample](https://github.com/robmikh/Win32CaptureSample)）：

- 用 `GraphicsCapturePicker` 让用户选显示器/窗口 → 返回 `GraphicsCaptureItem`（**需要用户交互一次**）。
- `Direct3D11CaptureFramePool.Create(device, B8G8R8A8UNORM, BufferCount, item.Size)`。
- `FrameArrived` 事件里 `TryGetNextFrame()`；**不要在 UI 线程做重活**。
- 取最新帧以降延迟：循环 `TryGetNextFrame` 直到失败。
- 窗口尺寸变化时 `Recreate()`。
- **系统会在被捕获目标上画黄框**（多路捕获各画一圈）。
- 缺点：需要 D3D11 设备 + 显存 + 用户授权，Python 直接用要写 COM/WinRT 桥（`winrt` / `pywinrt` 包），工程量大 → **本项目只在必须抓特定窗口时用**。

## B2. UI Automation (UIA)

### 访问方式

| 方式 | 入口 | 备注 |
|---|---|---|
| .NET / PowerShell | `System.Windows.Automation`（`UIAutomationClient.dll` / `UIAutomationTypes.dll`） | 见下方 PowerShell 片段 |
| Python `uiautomation` | `pip install uiautomation`（[yinkaisheng/Python-UIAutomation-for-Windows](https://github.com/yinkaisheng/Python-UIAutomation-for-Windows)，Apache-2.0，2026-06-02 更新，3.6k star） | 薄封装，接近原生 UIA，支持 Win32/WinForms/WPF/UWP/Qt/Chrome/Electron |
| Python `pywinauto` | `pip install pywinauto`（BSD-3-Clause，2026-10-15 更新，6.2k star） | 高层框架（窗口规格、等待、重试），`backend="uia"` / `"win32"` |
| .NET `FlaUI` | [FlaUI/FlaUI](https://github.com/FlaUI/FlaUI)（MIT，2026-08-13） | .NET 生态最成熟的 UIA 封装 |
| MCP 化 | [dividduang/uiautomation_mcp](https://github.com/dividduang/uiautomation_mcp)（Apache-2.0，2026-08-24） | 把 uiautomation 包装成 MCP server |

### UIA 的 BoundingRectangle 是物理像素吗？

**是。** 官方原文：

> "**The UI Automation API does not use logical coordinates.** The following methods and properties either return physical coordinates or take them as parameters: `GetClickablePoint`, `TryGetClickablePoint`, `ClickablePointProperty`, `FromPoint`, `BoundingRectangle`."
> — [UI Automation and Screen Scaling](https://learn.microsoft.com/en-us/dotnet/framework/ui-automation/ui-automation-and-screen-scaling)

同页的注意事项：
> "By default, a UI Automation client application running in a non-96-dpi environment will not be able to obtain correct results from these methods and properties." → **客户端必须 DPI-aware**。
> "Do not use `Cursor.Position`. The behavior of this property outside client windows in a scaled environment is undefined." → 读光标用 `GetPhysicalCursorPos` / PMv2 下的 `GetCursorPos`。

### UIA 已知失效场景

| 场景 | 症状 | 原因 |
|---|---|---|
| **Electron / Chromium / CEF / WebView2** | 只能拿到顶层窗口 + 少量宿主控件，**网页内的按钮/输入框基本拿不到** | 页面是渲染出来的，没有语义控件树。官方 pywinauto 文档：Chrome 需要开 accessibility（`--force-renderer-accessibility`）才能被识别 |
| **DirectX / OpenGL / Vulkan 游戏** | 无 UIA 树 | 自绘 |
| **Canvas / 自绘 UI / 图表控件** | 整块画布是一个元素，内部无子节点 | 应用没实现 UIA provider |
| **Unity / Unreal 引擎 UI** | 同上 | 同上 |
| **UWP / Store 应用** | 支持，但需要 `GraphicsCaptureItem` 配合 | — |
| **跨完整性级别** | 读不到高权限窗口的内部结构 | UIPI |
| **远程会话** | Session 0 / 锁屏 / 服务环境下元素找不到 | 需要交互式已登录桌面 |

> 实践共识："Electron / CEF / WebView2 → HTML 层需要浏览器自动化（Playwright/CDP），不是 UIA"。
> 对策：Electron 应用加 `--remote-debugging-port=9222`，用 Playwright/CDP 直连 DOM；通用兜底走 OCR + 视觉。

### UIA 树多深、多慢

- **深度没有上限参数**：`TreeScope_Descendants` 是一次性深搜，API 不提供深度限制；实际深度 5–15 层常见（浏览器/Electron 可到 30+）。
- **慢**（跨进程 COM 调用，每次属性读取都是一次 IPC）：
  - 实测：~1000 个元素的树用 `FindAll` 遍历 **≥8 秒**；建 cache request 约 9 秒（[SO: UI Automation Performance](https://stackoverflow.com/questions/74796513/ui-automation-performance)）
  - 微软 Q&A：目标在深度 ~7 时 `FindFirst(TreeScope_Descendants)` 会卡；改成多次 `FindFirst(TreeScope_Children)` 更快（[Why UIAutomation FindFirst is slow](https://learn.microsoft.com/en-my/answers/questions/2151409/why-uiautomation-findfirst-is-slow-and-can-i-corre)）
- **优化**：用 `IUIAutomationCacheRequest`（缓存 `ControlType/Name/BoundingRectangle/IsOffscreen`）+ `FindAllBuildCache` 一次拉回；避免逐属性读。
- **线程**：自动化自己的 UI 时，UIA 调用必须在**独立线程**，否则死锁/卡死（[UI Automation Threading Issues](https://learn.microsoft.com/windows/win32/winauto/ui-automation-threading-issues)）。
- **实践建议**：UIA 只做"**定点查询**"（按 Name+ControlType 找 1 个控件），不要做"全屏 dump"。

### Python 示例

```python
# uia_dump.py
import uiautomation as auto

auto.SetGlobalSearchTimeout(1.0)          # 避免 FindFirst 卡死

def dump(win, depth=0, maxdepth=6):
    if depth > maxdepth:
        return
    r = win.GetBoundingRectangle()          # 物理像素
    print("  " * depth,
          f"{win.ControlTypeName:<16} {win.Name!r:<40} "
          f"rect=({r.left},{r.top},{r.right},{r.bottom})")
    for c in win.GetChildren():
        dump(c, depth + 1, maxdepth)

if __name__ == "__main__":
    win = auto.WindowControl(searchDepth=1, SubName="记事本")
    if win.Exists(2):
        dump(win)
```

### PowerShell / .NET 示例

```powershell
# 建议用 Windows PowerShell 5.1 (powershell.exe)；PowerShell 7 需手动加载 .NET Framework 的 UIA 程序集
Add-Type -AssemblyName UIAutomationClient, UIAutomationTypes

$root   = [System.Windows.Automation.AutomationElement]::RootElement
$cond   = New-Object System.Windows.Automation.PropertyCondition(
             [System.Windows.Automation.AutomationElement]::NameProperty, "确定")
$button = $root.FindFirst([System.Windows.Automation.TreeScope]::Descendants, $cond)

if ($button) {
    $r = $button.Current.BoundingRectangle     # 物理像素（前提：pwsh 进程 DPI-aware）
    "{0} @ ({1},{2}) {3}x{4}" -f $button.Current.Name, $r.X, $r.Y, $r.Width, $r.Height
    $button.GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern).Invoke()
}
```

## B3. OCR 对比

| 方案 | 模型体积 | CPU 速度 | 中文 | bbox | 部署 |
|---|---|---|---|---|---|
| **PaddleOCR 3.x**（PP-OCRv5_mobile_det + _rec） | det **4.7 MB** / rec **16 MB** | det **57.77 ms**（标准）/ **28.15 ms**（高性能）；rec **21.20 / 5.32 ms**（Intel Xeon Gold 6271C 8 线程）。GPU(T4) det 10.67/6.36 ms、rec 5.43/1.46 ms | 强 | ✅ | `pip install paddleocr` + paddlepaddle（CPU） |
| **RapidOCR**（ONNXRuntime，PP-OCRv5 权重） | ~10–30 MB | 端到端 **~0.93–1.26 s/张**（Apple M2，含检测+方向+识别）；⚠️ 有反例：Win11 + server 模型下 det 比 PaddleOCR **慢 2–3×**（~101–210 ms vs 48–68 ms 总耗时） | 强 | ✅ | `pip install rapidocr onnxruntime`，**无 paddle 依赖，CPU 友好** |
| **dots.ocr**（小红书 hi lab，2025-08） | 1.7B（1.2B 视觉 + 1.7B 解码器） | 无官方 CPU 数字；官方路线是 **vLLM/GPU** | 100+ 语言 | ✅（输出结构化 layout） | `git clone rednote-hilab/dots.ocr`，需 torch 2.7 + cu128，**显存 ≥6–8 GB 才好用** |
| **Windows.Media.Ocr**（系统内置） | 0（系统） | 无官方数字；`MaxImageDimension` **运行时查询**（不是常量） | 需装 **zh-Hans 语言包 + OCR 可选功能**，否则 `TryCreateFromLanguage` 返回 null | ✅（`OcrWord.BoundingRect`） | WinRT，零依赖、最轻 |

**选择建议（本机 6GB 显存）**：
- **屏幕 OCR 主力 → RapidOCR**（ONNXRuntime，CPU ~1 s/张，可接受；且与 OmniParser 流水线同生态）。
- **要准确率/多语言 → PaddleOCR 3.x mobile**（官方 CPU 28–58 ms det + 5–21 ms rec，比 RapidOCR 快一个量级，但依赖 paddlepaddle 体积大）。
- **要零依赖、延迟最低 → Windows.Media.Ocr**（前提是装好中文语言包）。
- **dots.ocr 是"文档解析"取向**，不是实时屏幕 OCR；在 6GB 显存上跑 vLLM 偏重，**不推荐做本机屏幕 OCR**。

```python
# ocr_rapid.py —— 返回 bbox（物理像素，需自行加抓取原点）
from rapidocr import RapidOCR
import numpy as np

ocr = RapidOCR()
def ocr_screen(arr_bgr: np.ndarray):
    result, _ = ocr(arr_bgr)
    if not result:
        return []
    # result: [ [box(4点), text, score], ... ]
    return [{"text": t, "score": float(s),
             "box": [(float(p[0]), float(p[1])) for p in box]}
            for box, t, s in result]
```

## B4. 元素检测：OmniParser v2

- 仓库：[microsoft/OmniParser](https://github.com/microsoft/OmniParser)（**仓库许可证 CC-BY-4.0**；模型权重的许可需另查 HF model card ⚠️），最后 push **2026-07-20**，25.5k star。
- 官方指标（[HF model card](https://huggingface.co/microsoft/OmniParser-v2.0)）：
  - 端到端延迟 **~0.6 s/帧（A100）**、**~0.8 s/帧（RTX 4090）**；比 v1 快约 **60%**（v2 把 caption 模型从 BLIP2 换成 **Florence-2**）。
  - **ScreenSpot-Pro 平均 39.6%**（配合 GPT-4o；纯 GPT-4o 只有 0.8%）。
- 组件体积：检测器是 YOLOv8 导出 ONNX，**~11.6 MB**（第三方实测：[mcp-screen grounding research](https://88plug.github.io/screen-mcp/v1.1-grounding-research/)）；caption 模型 Florence-2（0.23B / 0.77B 两档）。
- **对 6GB 显存的判断**：
  - 检测器 12MB + Florence-2-base(0.23B) 在 FP16 下 **~0.5–1 GB 显存**，**装得下** ✅
  - ⚠️ 端到端延迟在 RTX 3060 Laptop 上预计 **1.5–4 s/帧**（按 4090 的 0.8 s 线性外推到约 1/3 的算力，再加笔记本 TDP 降频）——**这是估算，必须实测**。
  - 结论：**OmniParser 适合"离线建图标库 / 冷启动标注"，不适合做每步实时 grounding**（39.6% 的准确率也不够高）。
  - 实时 grounding 的现实选择：**Qwen3-VL-2B/4B 自身的 grounding 能力**（Qwen2.5-VL 起就有 `<|box_start|>` grounding 格式）。

---

# C. 执行层

## C1. 鼠标

### API 选型

| API | 状态 | 结论 |
|---|---|---|
| **`SendInput`** | 现代 API | ✅ **唯一推荐**。支持 UIPI 感知、串行注入、`dwExtraInfo` 标记、绝对+虚拟桌面 |
| `mouse_event` | 官方标注 "**This function has been superseded. Use SendInput instead.**" | ❌ 不用。**pyautogui 就是用它的**（源码确认，且作者注释写着 "SendInput() doesn't seem to be working for me. I've switched back to mouse_event()"） |
| `pyautogui` | 库 | ⚠️ 可用但**自己实现**了 mouse_event + `65536*x//width+1` 归一化，且 **import 时调 `SetProcessDPIAware()`**（锁死 DPI 模式）。只当"快速原型"用 |

> [SendInput](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-sendinput) 官方注意事项：
> - "This function is subject to UIPI. Applications are permitted to inject input only into applications that are at an equal or lesser integrity level."
> - "The function returns the number of events that it successfully inserted… If the function returns zero, the input was already blocked by another thread. **Neither GetLastError nor the return value will indicate the failure was caused by UIPI blocking.**" → **必须检查返回值**。
> - "This function does not reset the keyboard's current state." → 前后用 `GetAsyncKeyState` 检查。
> - "The SendInput function inserts the events **serially**."

### 点击是否需要防抖 / 最小停留时间？

**需要。** 三个具体原因：

1. **菜单/悬停**：很多 UI 需要先收到 `WM_MOUSEMOVE`（让 hover 状态生效、菜单弹出）再收到按钮消息。瞬移（move+down 同批）常常只触发 hover 不触发 click。
2. **双击阈值**：官方 `SM_CXDOUBLECLK`/`SM_CYDOUBLECLK` 定义 "The width of the rectangle around the location of a first click in a double-click sequence… The second click must occur within the rectangle… **and within a specified time**"。Windows 默认双击时间 ~500ms、矩形 ~4px。所以：
   - 想做**双击**，两次 down-up 间隔要 **< 500ms**，且位移 **< 4px**；
   - 想做**两次独立单击**，间隔要 **> 500ms**（否则会被判成双击）。
3. **UAC 桌面切换**：提权弹窗切到 Secure Desktop，需要**几十到几百 ms** 让新桌面/新窗口完成创建，再注入输入否则无效（见 §C3）。

**推荐节奏**：`MOVE → sleep(60ms) → DOWN → sleep(40ms) → UP → sleep(60ms)`。

### 实现

```python
# input_win.py
import ctypes, time
from ctypes import wintypes
from coords import to_normalized, virtual_screen

user32 = ctypes.WinDLL("user32", use_last_error=True)

ULONG_PTR = ctypes.c_ulonglong if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_ulong

class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG),
                ("mouseData", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR)]

class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD),
                ("dwExtraInfo", ULONG_PTR)]

class _INPUTUNION(ctypes.Union):          # ⚠️ 必须是 union，不能是两个独立成员
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT)]

class INPUT(ctypes.Structure):
    _anonymous_ = ("u",)                  # 之后可直接用 inp.mi / inp.ki
    _fields_ = [("type", wintypes.DWORD), ("u", _INPUTUNION)]

# ⚠️ 必须显式声明，否则 ctypes 默认按 c_int 传参 / 按 int 返回
#    —— 句柄在 64 位下会被截断成 32 位。
user32.GetDesktopWindow.restype = wintypes.HWND
user32.GetWindowDC.argtypes = [wintypes.HWND]
user32.GetWindowDC.restype  = wintypes.HDC
user32.ReleaseDC.argtypes   = [wintypes.HWND, wintypes.HDC]
user32.SendInput.argtypes    = [wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
user32.SendInput.restype     = wintypes.UINT
user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
user32.GetCursorPos.restype  = wintypes.BOOL

# 实测：x64 上 sizeof(MOUSEINPUT)=32, sizeof(KEYBDINPUT)=24, sizeof(INPUT)=40
# ⚠️ 若 cbSize != sizeof(INPUT)，SendInput 直接失败返回 0，且不说明原因
assert ctypes.sizeof(INPUT) == (40 if ctypes.sizeof(ctypes.c_void_p) == 8 else 28)

INPUT_MOUSE, INPUT_KEYBOARD = 0, 1
MOUSEEVENTF_MOVE, MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP = 0x0001, 0x0002, 0x0004
MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP = 0x0008, 0x0010
MOUSEEVENTF_MIDDLEDOWN, MOUSEEVENTF_MIDDLEUP = 0x0020, 0x0040
MOUSEEVENTF_WHEEL, MOUSEEVENTF_HWHEEL = 0x0800, 0x1000
MOUSEEVENTF_ABSOLUTE, MOUSEEVENTF_VIRTUALDESK = 0x8000, 0x4000
KEYEVENTF_KEYUP, KEYEVENTF_UNICODE, KEYEVENTF_SCANCODE = 0x0002, 0x0004, 0x0008
WHEEL_DELTA = 120


def _send(*inputs: INPUT) -> int:
    arr = (INPUT * len(inputs))(*inputs)
    sent = user32.SendInput(len(inputs), arr, ctypes.sizeof(INPUT))
    if sent != len(inputs):            # UIPI 阻断 / 输入被占
        raise OSError(ctypes.get_last_error(), f"SendInput sent {sent}/{len(inputs)}")
    return sent


def get_cursor_pos() -> tuple[int, int]:
    p = wintypes.POINT()
    user32.GetCursorPos(ctypes.byref(p))
    return p.x, p.y


def send_mouse_absolute(screen_x: int, screen_y: int, flags: int = 0) -> None:
    """screen_x/y = 物理屏幕坐标（虚拟桌面原点系）。"""
    nx, ny = to_normalized(screen_x, screen_y)
    i = INPUT(type=INPUT_MOUSE)
    i.mi = MOUSEINPUT(nx, ny, 0, flags | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK, 0, 0)
    _send(i)


def click(screen_x: int, screen_y: int, button: str = "left",
          settle: float = 0.06, hold: float = 0.04) -> None:
    """带防抖的点击：先移动并停顿（让 hover/菜单生效），再 down/up。"""
    down, up = (MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP) if button == "left" \
               else (MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP)
    send_mouse_absolute(screen_x, screen_y)
    time.sleep(settle)
    i = INPUT(type=INPUT_MOUSE); i.mi = MOUSEINPUT(0, 0, 0, down, 0, 0); _send(i)
    time.sleep(hold)
    i = INPUT(type=INPUT_MOUSE); i.mi = MOUSEINPUT(0, 0, 0, up, 0, 0); _send(i)
    time.sleep(settle)


def double_click(screen_x: int, screen_y: int) -> None:
    """两次 down-up 间隔 < 系统双击时间（默认 500ms），位移 < SM_CXDOUBLECLK（默认 4px）。"""
    click(screen_x, screen_y, settle=0.02, hold=0.02)
    time.sleep(0.08)
    click(screen_x, screen_y, settle=0.02, hold=0.02)


def scroll(screen_x: int, screen_y: int, clicks: int = -3) -> None:
    """clicks>0 向上滚。必须先把鼠标移到目标位置。"""
    send_mouse_absolute(screen_x, screen_y)
    time.sleep(0.03)
    i = INPUT(type=INPUT_MOUSE)
    i.mi = MOUSEINPUT(0, 0, clicks * WHEEL_DELTA, MOUSEEVENTF_WHEEL, 0, 0)
    _send(i)


def drag(from_xy: tuple[int, int], to_xy: tuple[int, int], steps: int = 20) -> None:
    send_mouse_absolute(*from_xy); time.sleep(0.06)
    i = INPUT(type=INPUT_MOUSE); i.mi = MOUSEINPUT(0, 0, 0, MOUSEEVENTF_LEFTDOWN, 0, 0); _send(i)
    time.sleep(0.06)
    fx, fy = from_xy; tx, ty = to_xy
    for k in range(1, steps + 1):
        send_mouse_absolute(round(fx + (tx - fx) * k / steps),
                            round(fy + (ty - fy) * k / steps))
        time.sleep(0.015)
    time.sleep(0.06)
    i = INPUT(type=INPUT_MOUSE); i.mi = MOUSEINPUT(0, 0, 0, MOUSEEVENTF_LEFTUP, 0, 0); _send(i)
```

⚠️ **滚轮惯性**：Windows 的平滑滚动（触控板/部分应用）会让 `MOUSEEVENTF_WHEEL` 的效果被平滑动画延长，表现为"滚了还在动"。对策：滚完 sleep 300–600ms 再截图；或改用 `WM_MOUSEWHEEL`（消息注入）/`IUIAutomationValuePattern`/`ScrollPattern`。

## C2. 键盘

### 扫描码 vs 虚拟键码

| 方式 | `KEYBDINPUT` 字段 | 特点 |
|---|---|---|
| **虚拟键码 (VK)** | `wVk = VK_xxx`，`wScan = 0` | 依赖**当前键盘布局**。`VK_A` 永远打 "a"，但 `VK_4` 在法语 AZERTY 上是 `&`。中文 IME 打开时，字母键会被 IME 吃掉。**适合控制键**（Ctrl/Alt/Win/Enter/F1） |
| **扫描码 (SC)** | `wScan = 扫描码`，`dwFlags |= KEYEVENTF_SCANCODE` | 模拟物理键位，**与布局无关**（`Set` → 扫描码 0x1E）。**游戏**（很多用 DirectInput 原始扫描码）只认这个 |
| **Unicode** | `wScan = Unicode 码点`，`dwFlags |= KEYEVENTF_UNICODE` | 直接注入字符，**完全绕过 IME 和键盘布局**。可打中文/emoji |

`KEYEVENTF_UNICODE` 的机制：系统发 `WM_KEYDOWN`/`WM_KEYUP`，`wParam = VK_PACKET`；`TranslateMessage` 把它转成 `WM_CHAR`（Unicode 字符）。

### `KEYEVENTF_UNICODE` 能打中文吗？—— 能，但有坑

| 坑 | 现象 | 规避 |
|---|---|---|
| 事件合并 | down+up 放在**同一个** `SendInput` 数组里可能**一个字符都不产生** | **每个字符单独一次 `SendInput`**（down 一个、up 一个，或分两次调用） |
| `KEYEVENTF_KEYUP` 误用 | 在 down 事件上带了 `KEYEVENTF_KEYUP` | 只在 up 事件上带 `KEYEVENTF_KEYUP`；`WM_KEYDOWN` 才产生字符 |
| 代理对 | 非 BMP 字符（emoji）需要**两套** down/up | 用 `encode('utf-16-le')` 拆成 surrogate pair |
| GTK+ | GTK 应用可能忽略 `KEYEVENTF_UNICODE`（曾有 4.8.3 回归） | 换剪贴板方案 |
| **Chromium / Electron** | ⚠️ 已知场景：`SendInput` 返回"成功"但 **Chromium 完全收不到** keydown（连 `Ctrl+V` 剪贴板方案也失败）——注入进了系统队列，但浏览器不认 | 对 Electron/Chromium 走 **CDP `Input.dispatchKeyEvent` / `Input.insertText`**（注意 `insertText` 不产生 keydown，无法用 keydown 校验） |
| 目标窗口必须在前台 | 注入到当前前台窗口 | 先 `SetForegroundWindow`（Windows 会限制跨线程抢焦点，见下） |

### IME 干扰的规避 + 剪贴板通道

**结论：`KEYEVENTF_UNICODE` 是首选；剪贴板 + `Ctrl+V` 是最稳的"绕过输入法"通道。**

剪贴板方案要点：
1. `OpenClipboard` / `EmptyClipboard` / `SetClipboardData(CF_UNICODETEXT)`（Python 用 `pyperclip` 或 `tkinter`）→ **注意 clipboard 有 1 个 owner 窗口，pyperclip 用 `with_polling` 更稳**。
2. `SendInput` 发 `Ctrl+V`（VK_CONTROL down + 'V' down/up + VK_CONTROL up）。
3. 某些应用（终端、密码框、部分游戏）**不接受粘贴** → 退回 `KEYEVENTF_UNICODE`。
4. 剪贴板会**覆盖用户剪贴板内容** → 必须"写前备份、动作后恢复"（见 §C4）。

```python
# keyboard_win.py
import ctypes, time
from ctypes import wintypes
from input_win import (INPUT, INPUT_KEYBOARD, KEYBDINPUT,
                       KEYEVENTF_KEYUP, KEYEVENTF_UNICODE, _send)

user32 = ctypes.WinDLL("user32", use_last_error=True)
VK_CONTROL, VK_V, VK_A, VK_RETURN, VK_ESCAPE = 0x11, 0x56, 0x41, 0x0D, 0x1B


def _vk(vk: int, up: bool = False) -> INPUT:
    i = INPUT(type=INPUT_KEYBOARD)
    i.ki = KEYBDINPUT(vk, 0, KEYEVENTF_KEYUP if up else 0, 0, 0)
    return i


def _unicode_char(ch: str) -> list[INPUT]:
    """每个 UTF-16 码元一组 down/up。非 BMP 字符（emoji）会是两个代理项。"""
    out = []
    data = ch.encode("utf-16-le")
    for off in range(0, len(data), 2):
        cp = data[off] | (data[off + 1] << 8)
        i = INPUT(type=INPUT_KEYBOARD); i.ki = KEYBDINPUT(0, cp, KEYEVENTF_UNICODE, 0, 0)
        u = INPUT(type=INPUT_KEYBOARD); u.ki = KEYBDINPUT(0, cp, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP, 0, 0)
        out.extend([i, u])
    return out


def type_unicode(text: str, per_char_delay: float = 0.012) -> None:
    """直接注入 Unicode 字符，绕过 IME 与键盘布局。"""
    for ch in text:
        events = _unicode_char(ch)
        for idx in range(0, len(events), 2):   # down 和 up 分两次 SendInput
            _send(events[idx])
            _send(events[idx + 1])
        time.sleep(per_char_delay)


def hotkey_ctrl_v() -> None:
    _send(_vk(VK_CONTROL))
    time.sleep(0.01)
    _send(_vk(VK_V))
    time.sleep(0.01)
    _send(_vk(VK_V, up=True))
    time.sleep(0.01)
    _send(_vk(VK_CONTROL, up=True))


def type_text_via_clipboard(text: str) -> None:
    """最稳的"绕过输入法"通道。注意会覆盖用户剪贴板。"""
    import pyperclip
    pyperclip.copy(text)
    time.sleep(0.05)
    hotkey_ctrl_v()


def press(vk: int) -> None:
    _send(_vk(vk)); time.sleep(0.02); _send(_vk(vk, up=True))
```

## C3. 稳定性坑清单

| 坑 | 现象 | 对策 |
|---|---|---|
| **UAC 提权弹窗（Secure Desktop）** | 截图失败、UIA 返回 0 个窗口、`OpenInputDesktop` 返回 ACCESS_DENIED、注入输入无效 | **默认无解**。可选：组策略关闭 "User Account Control: Switch to the secure desktop when prompting for elevation"（`HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System` → `PromptOnSecureDesktop=0`）——**降低安全性，必须用户明确同意**。更优：让被控程序以管理员身份常驻运行，agent 本身不提权 |
| **锁屏 / 登录界面** | 全黑或只有锁屏 | 检测 `WTSGetActiveConsoleSessionId` / `OpenInputDesktop` 是否是当前桌面；锁屏时**不执行动作**，只报告 |
| **全屏独占 (Exclusive Fullscreen)** | 截图拿到黑屏 / 抓不到内容 | 独占全屏绕过 DWM 合成，WGC/DDA/BitBlt 都拿不到。只能让用户切到窗口化 |
| **RDP 断连** | 会话断开后截图黑、输入无效；重连后分辨率/DPI 可能变 | 检测会话状态；断连时停止动作；**每次动作前重新读虚拟桌面度量**（DPI/分辨率可能变） |
| **窗口最小化后坐标失效** | 目标窗口的 `BoundingRectangle` 变成 (0,0) 或无效 | 最小化的窗口 `DWMWA_CAPTION_BUTTON_BOUNDS` 官方明说 "the value of the RECT retrieved is undefined"；恢复窗口或放弃该目标 |
| **窗口被遮挡** | 截图上是遮挡物，UIA 元素却可见 | UIA 定位 + 坐标点击会点到遮挡物上 → **先 `SetForegroundWindow` 再点**，或用 WGC 抓目标窗口自己 |
| **滚动惯性 / 平滑滚动** | 滚动命令发出后画面还在动 | 滚完 sleep 300–600ms；或用 UIA `ScrollPattern` |
| **动画时序** | 菜单展开、页面切换需要时间 | 每个动作后**等"界面稳定"**：连续两帧截图差异 < 阈值再继续（见下方 `wait_stable`） |
| **UIPI（完整性级别低）** | `SendInput` 返回 0，且 `GetLastError` **不指示原因** | 检查目标窗口的完整性级别；agent 进程要 >= 目标 |
| **抢焦点被拒** | `SetForegroundWindow` 返回 FALSE | 先用 `AttachThreadInput` + `BringWindowToTop` + `SetForegroundWindow` 组合，或用 `AllowSetForegroundWindow` |
| **目标进程退出/重启** | 之前的 HWND 失效 | 每次动作前校验 HWND 仍存在（`IsWindow`） |
| **分辨率/DPI 运行时变化** | 用户改缩放或接了显示器 | 每次动作前重读 `virtual_screen()` |

```python
# wait_stable.py —— 等待界面稳定（抗动画/抗滚动惯性）
import time, numpy as np
from capture_mss import grab_desktop

def wait_stable(threshold: int = 3, max_wait: float = 3.0, poll: float = 0.15) -> bool:
    prev, _ = grab_desktop()
    t0 = time.time()
    while time.time() - t0 < max_wait:
        time.sleep(poll)
        cur, _ = grab_desktop()
        diff = int(np.abs(cur.astype(np.int16) - prev.astype(np.int16)).mean())
        if diff <= threshold:
            return True
        prev = cur
    return False     # 超时：仍可继续，但要降低置信度
```

## C4. 安全与可审计

### 动作日志（最小可用）

```python
# audit.py
import json, time, hashlib
from pathlib import Path

LOG = Path("actions.jsonl")

def log_action(kind: str, **fields) -> dict:
    rec = {"ts": time.time(), "iso": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
           "kind": kind, **fields}
    with LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return rec
```

每条记录建议含：`ts / kind / target_hwnd / target_title / screen_xy / screenshot_hash / before_state / after_state / result`。

**截图哈希**（把"我看到的那一帧"和"我点的那一帧"绑定）：

```python
import hashlib, numpy as np
def frame_hash(arr: np.ndarray) -> str:
    return hashlib.sha256(np.ascontiguousarray(arr).tobytes()).hexdigest()[:16]
```

### 危险动作确认

- **白名单/黑名单**：
  - 需人工确认：删除、支付、发送邮件/消息、注册、改系统设置、运行 `powershell`/`cmd`、安装软件、修改注册表、UAC 弹窗、剪贴板读取、截屏（含敏感窗口）。
  - 黑名单窗口标题关键词（`KeePass`、`1Password`、`银行`、`支付`…）+ 类名 `#32770`（对话框）。
- **确认必须绑定"意图 + 目标"**：`{"action": "click", "hwnd": ..., "rect": ..., "text": "确定"}` → 用户确认的是这一条，不是笼统的"继续"。
- **不可逆动作前先 dry-run**：先截屏 + 展示将要点击的元素，等确认。

### 幂等与重试

| 策略 | 说明 |
|---|---|
| **动作后必须验证** | 每个动作后重新感知（截图 diff / UIA 查属性）确认生效，**不要假设**点击成功 |
| **只重试可逆动作** | 移动鼠标、滚动、聚焦 → 可重试；发送消息、支付、删除 → **不可自动重试** |
| **重试带退避 + 上限** | 3 次，200/500/1200ms |
| **每次重试前重新感知** | UI 在两次尝试间可能已变，重用旧坐标 = 误点 |
| **状态机而非脚本** | 目标"页面已加载"而非"点了第 3 个按钮"；重入时按状态而非序号继续 |
| **动作序列记录** | 记录 `action_id`，重放时跳过已成功的步骤 |

---

# D. 现成的可复用件

## D1. 库与 MCP server 清单

> 许可证与最后更新时间取自 **GitHub API `repos/*`**（`pushed_at`），采集于 **2026-10-19**。

### 底层库 / 模型

| 项目 | 作用 | 许可证 | 最后 push | ⭐ |
|---|---|---|---|---|
| [BoboTiG/python-mss](https://github.com/BoboTiG/python-mss) | 极速多显示器截图（ctypes），**自动设 PMv1 DPI 感知**，返回物理像素 | MIT | 2026-10-19 | 1.3k |
| [yinkaisheng/Python-UIAutomation-for-Windows](https://github.com/yinkaisheng/Python-UIAutomation-for-Windows) | Python UIA 薄封装（`uiautomation`） | Apache-2.0 | 2026-06-02 | 3.6k |
| [pywinauto/pywinauto](https://github.com/pywinauto/pywinauto) | Win32/UIA 双后端自动化框架 | BSD-3-Clause | 2026-10-15 | 6.2k |
| [asweigart/pyautogui](https://github.com/asweigart/pyautogui) | 通用 GUI 自动化（鼠标/键盘/截图） | BSD-3-Clause | **2024-08-20（已停滞）** | 12.7k |
| [opencv/opencv](https://github.com/opencv/opencv) | 图像处理（模板匹配、颜色、形态学） | Apache-2.0 | 2026-10-19 | 91.2k |
| [PaddlePaddle/PaddleOCR](https://github.com/PaddlePaddle/PaddleOCR) | OCR（PP-OCRv5） | Apache-2.0 | 2026-09-16 | 91.4k |
| [RapidAI/RapidOCR](https://github.com/RapidAI/RapidOCR) | ONNXRuntime 版 OCR（无 paddle 依赖） | Apache-2.0 | 2026-10-19 | 8.2k |
| [microsoft/onnxruntime](https://github.com/microsoft/onnxruntime) | 推理运行时 | MIT | 2026-10-19 | 22.1k |
| [rednote-hilab/dots.ocr](https://github.com/rednote-hilab/dots.ocr) | 1.7B 文档 OCR VLM | MIT | 2026-03-24 | 9.2k |
| [microsoft/OmniParser](https://github.com/microsoft/OmniParser) | 图标检测 + caption（grounding） | **CC-BY-4.0**（权重许可另查） | 2026-07-20 | 25.5k |
| [ggml-org/llama.cpp](https://github.com/ggml-org/llama.cpp) | 量化推理（支持 Qwen3-VL） | MIT | 2026-10-19 | 131.7k |
| [ollama/ollama](https://github.com/ollama/ollama) | 本地模型服务 | MIT | 2026-10-19 | 183.8k |
| [FlaUI/FlaUI](https://github.com/FlaUI/FlaUI) | .NET UIA 封装 | MIT | 2026-08-13 | 3.2k |

### MCP server / 整机框架

| 项目 | 作用 | 许可证 | 最后 push | ⭐ |
|---|---|---|---|---|
| [trycua/cua](https://github.com/trycua/cua) | 跨 OS computer-use 框架：驱动 + 沙箱 + MCP server，**Windows 驱动带 permonitorv2 manifest** | MIT | 2026-10-19 | 30.2k |
| [bytedance/UI-TARS-desktop](https://github.com/bytedance/UI-TARS-desktop) | 字节 GUI Agent 桌面端（Local/Remote Operator），走 OpenAI 兼容端点 | Apache-2.0 | 2026-10-09 | 39.3k |
| [xlang-ai/OpenCUA](https://github.com/xlang-ai/OpenCUA) | OpenCUA-7B/32B/72B（Qwen2.5-VL 基座）+ AgentNet 数据集 | MIT | 2026-05-25 | 857 |
| [NanmiCoder/cc-haha](https://github.com/NanmiCoder/cc-haha) | 本地优先的 Claude Code 风格电脑操作工作台（pyautogui+mss+win32gui 桥） | **NOASSERTION**（需自行核对） | 2026-10-19 | 15.1k |
| [microsoft/mcp](https://github.com/microsoft/mcp) | 微软官方 MCP 服务器集合（Azure/Fabric/Template，**不含 Windows GUI**） | MIT | 2026-10-19 | 3.8k |
| [sandraschi/windows-computer-use-mcp](https://github.com/sandraschi/windows-computer-use-mcp) | Windows computer-use MCP：click/screenshot/type/drag/OCR | MIT | 2026-10-13 | 46 |
| [dcl632/windows-gui-mcp](https://github.com/dcl632/windows-gui-mcp) | UIA 优先、pyautogui 兜底的 12 工具 MCP | MIT | 2026-06-05 | 3 |
| [cenglin123/computer-use-mcp](https://github.com/cenglin123/computer-use-mcp) | Windows-only，40 工具（UIA 点击 / 视觉点击 / 审计 / 危险动作拦截） | MIT | 2026-09-09 | 0 |
| [dividduang/uiautomation_mcp](https://github.com/dividduang/uiautomation_mcp) | 把 `uiautomation` 包装成 MCP | Apache-2.0 | 2026-08-24 | 21 |
| [adkdev200/universal-computer-control](https://github.com/adkdev200/universal-computer-control) | 29 工具，降级链：UIA→OCR→模板→VLM→坐标→pyautogui | MIT | 2026-09-06 | 2 |
| [Anmol202005/kira-mcp](https://github.com/Anmol202005/kira-mcp) | 本地 MCP：`perceive_screen` 用 **OmniParser-v2 YOLO** 输出可直接点击的 (cx,cy) | MIT | 2026-06-21 | 1 |
| [wimi321/windows-computer-use-skill](https://github.com/wimi321/windows-computer-use-skill) | Node+Python MCP，pyautogui+mss+psutil+pywin32 | MIT | 2026-04-01 | 3 |

> **观察**：这些 MCP server 的技术栈高度趋同——`mss` 截图 + `pyautogui` 输入 + `pywinauto`/`uiautomation` 做语义定位 + 稳定化策略（UIA 优先、坐标兜底）。**这基本就是本项目的参考实现**。
> **注意 `microsoft/mcp` 不含 Windows GUI server**——别被名字误导。

## D2. 在 Windows + 6GB 显存上跑 VLM

### Qwen3-VL 支持情况

- **llama.cpp** ✅ 官方支持，架构名 `qwen3vl`，走 `libmtmd`（`tools/mtmd/`），需要 **b6907 或更新**。
  - 必须**两个文件**：语言模型 GGUF + 视觉投影 `mmproj-*.gguf`（**只给 LLM 文件会静默忽略图片**）。
  - ```bash
    llama-mtmd-cli -m Qwen3-VL-2B-Instruct-Q4_K_M.gguf \
                    --mmproj mmproj-Qwen3VL-2B-Instruct-Q8_0.gguf \
                    --image shot.jpg -p "Click the Save button" -n 512
    ```
  - 或 `llama-server -m ... --mmproj ...` → OpenAI 兼容 `/v1/chat/completions`。
- **Ollama** ✅ 支持 `qwen3-vl`（需 **Ollama ≥ 0.12.7**）。

### 体积（实测来自 Ollama registry API，2026-10-19）

| Tag | 层数 | 合计 |
|---|---|---|
| `qwen3-vl:2b` | 4 | **1.76 GB** |
| `qwen3-vl:2b-instruct-q8_0` | 4 | 2.48 GB |
| `qwen3-vl:4b` | 4 | **3.07 GB** |
| `qwen3-vl:4b-thinking-q4_K_M` | 4 | 3.07 GB |

### llama.cpp GGUF 体积（Qwen3-VL-2B）

| 文件 | 大小 |
|---|---|
| `Qwen3-VL-2B-Instruct-Q4_K_M.gguf` | **~1.1 GB** |
| `Qwen3-VL-2B-Instruct-F16.gguf` | ~3.3 GB |
| `mmproj-Qwen3VL-2B-Instruct-F16.gguf` | ~0.78–0.82 GB |
| `mmproj-Qwen3VL-2B-Instruct-Q8_0.gguf` | ~0.42–0.45 GB |

**推荐组合：Q4_K_M LLM（1.1GB）+ Q8_0 mmproj（0.43GB）≈ 1.5GB**，或 Q4_K_M mmproj（~267MB）≈ 1.4GB。

### 6GB 显存的可行路径

| 路径 | 配置 | 显存 | 速度 ⚠️估算 | 评价 |
|---|---|---|---|---|
| **A（推荐）llama.cpp + Qwen3-VL-2B** | Q4_K_M + mmproj Q8_0 | ~2–3 GB（含 KV cache 与激活） | 视觉编码 + prefill **~1–3 s**（2560×1600 需先下采样到 ~1280×800），解码 **~20–40 tok/s** → **单步 3–8 s** | ✅ **首选**。可控、可离线、OpenAI 兼容 API |
| **B Ollama + qwen3-vl:2b** | 1.76 GB 整包 | ~2.5–3.5 GB | 同量级 | ✅ **最省事**：`ollama run qwen3-vl:2b` / `/api/chat` 带 base64 图片 |
| **C llama.cpp/Ollama + 4B** | 3.07 GB | ~4.5–5.5 GB | ~1.5–2× 慢于 2B | ⚠️ **塞得下但吃紧**，Windows 桌面 + 浏览器 + 截图进程同时吃显存时容易 OOM |
| **D vLLM + UI-TARS-1.5-7B** | — | ≥16 GB | — | ❌ **6GB 跑不了**；且 vLLM 在 Windows 原生不支持，需 WSL2/Docker + Linux 主机 |
| **E dots.ocr 1.7B** | torch 2.7 cu128 | ~4–6 GB | 无官方 CPU 数字 | ⚠️ 文档解析取向，非屏幕 OCR，不划算 |
| **F OmniParser v2（YOLOv8 + Florence-2）** | 检测器 ~11.6MB ONNX | ~1 GB | **1.5–4 s/帧** ⚠️ | ⚠️ 可跑，但准确率仅 39.6% (ScreenSpot-Pro)，且比 2B VLM 慢；建议只做**离线建库** |

**落地方案建议**：

```
1. 截图（WGC/mss，物理像素）
2. 下采样到 1280×800（记住 scale=0.5）→ 送 Qwen3-VL-2B
3. 模型输出 grounding 坐标（0..1000 归一化 或 像素）
4. 用 img_to_screen(gx, gy, px, py, scale) 还原到物理屏幕坐标
5. SendInput 绝对移动 + 点击（带防抖），回读校正
6. 重新截图验证效果
```

⚠️ 所有速度均为**估算**（基于公开的 4090/A100 基准和 Qwen 官方量化表的算力外推），**必须在本机实测**。显存 6GB 的主要风险是：桌面合成器 + 浏览器 + 截图缓冲 + 模型同时占用。

---

# 附录 · 引用清单

**Microsoft Learn（官方）**
- [GetCursorPos](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-getcursorpos) · [GetPhysicalCursorPos](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-getphysicalcursorpos) · [SetCursorPos](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-setcursorpos)
- [SendInput](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-sendinput) · [MOUSEINPUT](https://learn.microsoft.com/en-us/windows/win32/api/winuser/ns-winuser-mouseinput) · [mouse_event（deprecated）](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-mouse_event)
- [GetSystemMetrics](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-getsystemmetrics) · [SetProcessDpiAwarenessContext](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-setprocessdpiawarenesscontext) · [Setting the default DPI awareness for a process](https://learn.microsoft.com/en-us/windows/win32/hidpi/setting-the-default-dpi-awareness-for-a-process) · [High DPI Desktop Application Development on Windows](https://learn.microsoft.com/en-us/windows/win32/hidpi/high-dpi-desktop-application-development-on-windows)
- [DWMWINDOWATTRIBUTE](https://learn.microsoft.com/en-us/windows/win32/api/dwmapi/ne-dwmapi-dwmwindowattribute)
- [UI Automation and Screen Scaling](https://learn.microsoft.com/en-us/dotnet/framework/ui-automation/ui-automation-and-screen-scaling) · [UI Automation Overview](https://learn.microsoft.com/en-us/windows/win32/winauto/uiauto-uiautomationoverview) · [UIA Caching](https://github.com/MicrosoftDocs/win32/blob/docs/desktop-src/WinAuto/uiauto-cachingforclients.md) · [UIA Threading Issues](https://learn.microsoft.com/windows/win32/winauto/ui-automation-threading-issues)
- [Screen capture (WGC)](https://learn.microsoft.com/en-us/windows/apps/develop/media-authoring-processing/screen-capture) · [OcrEngine (Windows.Media.Ocr)](https://learn.microsoft.com/en-us/uwp/api/windows.media.ocr.ocrengine) · [OcrEngine.MaxImageDimension](https://learn.microsoft.com/en-us/uwp/api/windows.media.ocr.ocrengine.maximagedimension)
- [Windows Forms High DPI Support](https://learn.microsoft.com/en-us/dotnet/framework/winforms/high-dpi-support-in-windows-forms) · [Power Automate 截图在无人值守失败](https://learn.microsoft.com/en-za/troubleshoot/power-platform/power-automate/desktop-flows/take-screenshot-action-fails-in-unattended-mode)

**源码 / 官方样例**
- [robmikh/Win32CaptureSample](https://github.com/robmikh/Win32CaptureSample) · [Blinue/Magpie 截图方案对比](https://github.com/Blinue/Magpie/blob/dev/docs/Comparison%20of%20capture%20methods.md) · [DirectX-Graphics-Samples](https://github.com/microsoft/DirectX-Graphics-Samples)

**关键源码事实（本机直接读取验证）**
- `mss/src/mss/windows/gdi.py` → `_set_dpi_awareness()` 调 `shcore.SetProcessDpiAwareness(2)`
- `pyautogui/pyautogui/_pyautogui_win.py` → import 时 `SetProcessDPIAware()`；`_size()` = `GetSystemMetrics(0/1)`；移动用 `SetCursorPos`，事件用 `mouse_event`，归一化 `65536*x//width+1`

**性能与对比**
- [python-mss issue #197（DPI 行为）](https://github.com/BoboTiG/python-mss/issues/197) · [UI Automation Performance (SO)](https://stackoverflow.com/questions/74796513/ui-automation-performance) · [Why FindFirst is slow](https://learn.microsoft.com/en-my/answers/questions/2151409/why-uiautomation-findfirst-is-slow-and-can-i-corre) · [RapidOCR vs PaddleOCR 速度对比](https://github.com/RapidAI/RapidOCRDocs/blob/main/docs/blog/posts/rapidocr_vs_paddleocr_speed.md) · [RapidOCR discussion #515（反例）](https://github.com/RapidAI/RapidOCR/discussions/515) · [PP-OCRv5 文本检测指标](http://www.paddleocr.ai/v3.1.0/en/version3.x/module_usage/text_detection.html) · [OmniParser v2 model card](https://huggingface.co/microsoft/OmniParser-v2.0) · [mcp-screen grounding research（OmniParser 检测器体积）](https://88plug.github.io/screen-mcp/v1.1-grounding-research/)

**模型分发**
- [Qwen/Qwen3-VL-2B-Instruct-GGUF](https://huggingface.co/Qwen/Qwen3-VL-2B-Instruct-GGUF) · [bartowski/Qwen3-VL-2B-Instruct-GGUF](https://huggingface.co/bartowski/Qwen_Qwen3-VL-2B-Instruct-GGUF) · [rednote-hilab/dots.ocr](https://github.com/rednote-hilab/dots.ocr) · [Ollama qwen3-vl](https://ollama.com/library/qwen3-vl)

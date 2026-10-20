# proto/exec — Windows GUI 智能体「执行层原型」

把 **截图 → 定位 → 动作 → 校验** 这条链路的底座落实成可运行代码。
纯标准库 + ctypes，第三方包全部可选。

配套调研文档：`docs/research/bottom-drafts/04-windows-stack.md`（本 README 的第 6 节列出了
与该文档**不一致**的地方，以本 README 的实测为准）。

---

## 1. 怎么跑

```bat
:: 自检（一条命令，出 PASS/FAIL 清单，退出码 0 = 全过）
python selftest.py

:: 不做真实点击（点击会在光标当前所在处落一次左键）
python selftest.py --skip-click

:: 打印更多细节
python selftest.py -v
```

单独跑各模块：

```bat
python dpi.py       :: 打印本机 DPI 状态报告
python capture.py   :: 打印后端信息 + 抓一帧的元数据
python input.py     :: 打印 SendInput 自检结果
python uia.py       :: 列出顶层窗口
```

环境变量：

| 变量 | 作用 |
|---|---|
| `GUI_CU_DPI_QUIET=1` | 不在 import 时打印 DPI 报告（`selftest.py` 默认已设） |
| `GUI_CU_NO_UTF8=1` | 关闭 stdout/stderr 的 UTF-8 重配置 |

### 依赖

| 包 | 必需？ | 缺失时的行为 |
|---|---|---|
| `numpy` | 否 | `capture.grab()` 返回 `RawFrame` 垫片（`width/height/shape/bgra`），不崩 |
| `mss` | 否 | 自动降级为纯 ctypes GDI `BitBlt`（本机当前走的就是这条） |
| `uiautomation` / `pywinauto` | 否 | **完全不需要**，UIA 由 ctypes 直接调 COM 实现 |

本机实测：`mss` **未安装**、`numpy` **已安装**、三个 UIA 包**全部未安装**——
所以 GDI 降级路径是当前的主路径，不是摆设。

---

## 2. 模块职责

### `dpi.py` — 坐标系的地基

- **导入即生效**：调用 `SetProcessDpiAwarenessContext(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2)`，
  必须在**创建任何窗口之前**、且在 **import pyautogui 之前**发生。
  - `pyautogui` 在 import 时就调 `SetProcessDPIAware()`，`mss` 在 `MSS()` 构造时调
    `SetProcessDpiAwareness(2)`，二者都会**锁死**进程 DPI 模式，之后再设 PMv2 会
    `ERROR_ACCESS_DENIED`。
  - 降级链：`SetProcessDpiAwarenessContext` → `shcore.SetProcessDpiAwareness(2)` → `SetProcessDPIAware()`。
- API：
  - `physical_screen_size()` → 主显示器**物理**像素 `(2560, 1600)`
  - `virtual_desktop_origin()` → 虚拟桌面原点 `(0, 0)`（副屏在左/上时为负）
  - `virtual_desktop_size()` → 虚拟桌面尺寸 `(2560, 1600)`
  - `logical_screen_size()` → 逻辑分辨率 `(1707, 1067)`
  - `scale_factor()` → `1.5`；`system_dpi()` → `144`
  - `scale_report()` → 结构化状态字典；`format_report()` → 可读文本
- 启动时向 **stderr** 打印一份「本机 DPI 状态报告」（stdout 留给数据）。
  控制台代码页不是 UTF-8 时自动降级为纯 ASCII，避免乱码。

**本机实测（2026-10-20）**：

| 进程 | `GetSystemMetrics(SM_CXSCREEN)` | `GetDeviceCaps(HORZRES)` | `LOGPIXELSX` |
|---|---|---|---|
| DPI-unaware（如 PowerShell） | 1707 | 1707 | **96** |
| 本原型（PMv2） | **2560** | **2560** | **144** |

> 这就是 1.5× 偏差的来源：不设 PMv2，所有屏幕度量都被 ÷1.5。

### `capture.py` — 感知层

- `grab(region=None)` → 抓屏。`region=None` 抓整个虚拟桌面；否则
  `(left, top, width, height)`，单位**物理像素**、原点在虚拟桌面左上角。
  返回 `numpy.ndarray (H, W, 3)` **BGR**（无 numpy 时返回 `RawFrame`）。
- `grab_frame(region=None)` → `(frame, FrameMeta)`。
- `FrameMeta`：`width/height/left/top/right/bottom/backend/physical_pixels/source/timestamp/capture_ms`。
- 后端：优先 `mss`，否则纯 ctypes GDI `BitBlt`（带 `CAPTUREBLT`，连分层窗口一起抓）。
- 实测：全屏 2560×1600 抓一帧 **≈ 6.7 ms**（GDI 路径）。

### `input.py` — 执行层

- **结构体**：`INPUT` 用 **union** 定义（`MOUSEINPUT`/`KEYBDINPUT` 二选一）。
  x64 上 `sizeof(INPUT) == 40`（x86 为 28）。
  - ⚠️ 写成两个独立 struct 成员 → `sizeof == 64` → `SendInput` 因 `cbSize` 不匹配
    **直接返回 0 且不报错**，所有点击静默失效。本原型在 import 时断言这一点。
- **启动自检** `self_check()`：把光标移到**它当前所在的位置**（无害），
  检查 `SendInput` 返回值非 0；失败抛 `InputSelfCheckError`（**不静默降级**）。
- 鼠标：`move_to` / `move_by` / `click` / `double_click` / `right_click` / `scroll` / `drag`。
  - 点击序列固定为 **move → 60ms → down → 40ms → up**（常量 `CLICK_SETTLE` / `CLICK_HOLD`）。
  - `click()` 返回实测时序 `ClickTiming`，供自检断言。
- 键盘：
  - `press_vk(vk)` / `hotkey(*vks)`：虚拟键码。
  - `press_scancode(scan, modifiers)`：**扫描码**组合键（与键盘布局无关，游戏只认这个）。
    `scancode_of(vk)` 用 `MapVirtualKeyW` 转换。
  - `type_unicode(text)`：`KEYEVENTF_UNICODE`，**绕过 IME 与键盘布局**，可打中文。
    down 与 up **分两次** `SendInput`（合成一个 `INPUT[2]` 数组可能一个字符都不产生）。
  - `paste_text(text)`：剪贴板通道 —— 设剪贴板 → `Ctrl+V` → **恢复原剪贴板内容**。
    这是"绕过输入法"最稳的路径。
- 坐标换算：`round((x - VX) * 65535 / (W - 1))`，并带 `MOUSEEVENTF_VIRTUALDESK`。
  - 本机实测 6 个目标点 × 12 次采样，误差**全为 0**；
    变体 `x * 65535 // W` 有稳定 **−1 px** 偏差，`x * 65536 // W` 在右下角 **−1 px**。

### `uia.py` — 定点定位

纯 ctypes 直接调 COM：`CoCreateInstance(CLSID_CUIAutomation, IID_IUIAutomation)`，
再按 vtable 下标调用。**vtable 下标来自 Windows SDK 头文件 `UIAutomationClient.h`，不是猜的。**

- `enumerate_windows(visible_only=True)` / `find_window(title_substr, class_substr)`：
  顶层窗口快照（纯 `EnumWindows`）。
- `find_controls(hwnd=None, name=None, automation_id=None, control_type=None, class_name=None, limit=50)`
  - 给 `hwnd` → 以该窗口为根，`TreeScope_Descendants` **递归下钻**，包括**不可见**的中间层。
  - 不给 → 从桌面根查。
- `find_one(...)`：`FindFirst`，更快，只要一个。
- `is_usable(element, require_on_screen=True, interactive_only=False)` → `(bool, 原因)`：
  判据是 **ClassName + 包围盒 + 控件类型**，**不看候选数量**：
  1. 有 name 或 AutomationId；2. ClassName 非空；3. 包围盒有面积；
  4. 包围盒与虚拟桌面有交集；5. `interactive_only` 时控件类型要属于可交互集合。
- `assess_window(hwnd)` → 窗口级评估：`total` / `usable_controls` / `distinct_hwnds` / `verdict`
  （`uia-usable` / `shell-only` / `no-uia-tree`）。
  `distinct_hwnds > 1` 是"**递归进了子窗口**"的证据。

> ⚠️ 三个必须记住的 COM 坑（本原型都踩过并修好了）：
> 1. **`CoCreateInstance` 之前必须 `CoInitializeEx`**，否则返回
>    `0x800401F0`（`CO_E_NOTINITIALIZED`）——看起来像"类没注册"，其实只是没初始化 COM。
> 2. `SysAllocStringLen` / `SysFreeString` 在 **oleaut32.dll**，不在 ole32.dll。
> 3. `PROPERTYID`(4 字节) 和 `VARIANT`(16 字节) 都是**按值**传参。
>    用 `POINTER(...) + byref` 会让被调方把"指针"当值读 → `E_INVALIDARG(0x80070057)`。

---

## 3. 自检覆盖

`selftest.py` 实测输出（本机 2026-10-20）：

```
========================================================================
Windows GUI agent - 执行层自检
========================================================================

[PASS] DPI 感知生效 (GetSystemMetrics vs GetDeviceCaps)
       awareness=PER_MONITOR_AWARE_V2 physical=2560x1600 logical=1707x1067
       scale=1.5 GetDeviceCaps=2560x1600@144dpi

[PASS] SendInput 自检 (sizeof(INPUT)==40 且返回值非 0)
       sizeof(INPUT)=40 (期望 40) send_input_ok=True 回读偏差=(0, 0)

[PASS] 截图分辨率是物理像素
       抓到 2560x1600 物理=2560x1600 逻辑=1707x1067 backend=gdi 用时=55.4ms shape=(1600, 2560, 3)
       | 物理≠逻辑，已确认不是虚拟化尺寸

[PASS] UIA 定点查询（按名字查控件 + 物理像素坐标）
       命中窗口 任务栏 Shell_TrayWnd (hwnd=65784)；控件 Button name='小组件 18°C 局部多云'
       class='ToggleButton' rect=(10,1528,238,1600) 物理像素判定=OK；该窗口可用交互控件 16/30

[PASS] 点击序列时序 (move -> 60ms -> down -> 40ms -> up)
       实测 pre_move=60.8ms (要求>=60ms) hold=49.6ms (要求>=40ms) total=110.9ms 目标=(2466,1500)

------------------------------------------------------------------------
合计: 5 PASS / 0 FAIL / 0 SKIP   耗时 0.3s
------------------------------------------------------------------------
```

关于时序断言用 `>=` 而不是 `==`：Windows 默认定时器精度 15.6 ms，
`time.sleep(0.040)` 实际会睡 ~49.6 ms（向上取整到时钟节拍）。

> `点击序列时序` 这一项会**在光标当前所在处真实落一次左键**（扰动最小）。
> 不接受就用 `--skip-click`，该项会记为 SKIP。

---

## 4. 已知边界

### 4.1 平台硬边界（改不了的）

| 场景 | 现象 | 出处/依据 |
|---|---|---|
| **UAC 安全桌面** | 提权弹窗切到 Secure Desktop，**无法截图、无法注入输入、UIA 枚举不到窗口**（`OpenInputDesktop` 返回 ACCESS_DENIED） | Windows 设计如此，防止恶意软件自动点"允许" |
| **锁屏 / 登录界面** | 截图黑屏或只有锁屏 | 桌面已切换，输入流不投递 |
| **全屏独占 (Exclusive Fullscreen)** | 绕过 DWM 合成，BitBlt / WGC / DDA 全都拿不到内容 | DirectX 独占交换链不经过 DWM |
| **RDP 断连** | 会话断开后截图黑、输入无效；重连后分辨率/DPI 可能变 | 每次动作前应重读 `virtual_screen()` |
| **DRM 受保护内容** | 三种截图方案都只给黑帧 | — |
| **UIPI 完整性级别** | `SendInput` 返回 0，且 `GetLastError` **不指示原因** | 目标窗口权限更高时，agent 也要提权 |

规避 UAC 的唯一"干净"做法是：**让被控程序以管理员身份常驻运行，agent 本身不提权**。
关掉 Secure Desktop（`PromptOnSecureDesktop=0`）能通，但降低安全性，需用户明确同意。

### 4.2 UIA 的可用性边界（本机实测，`uia.assess_window`）

| 应用 | total | usable | distinct_hwnds | verdict |
|---|---|---|---|---|
| 文件资源管理器 `CabinetWClass` | 239 | 163 | **11** | uia-usable |
| Chromium（DSH Web GUI）`Chrome_WidgetWin_1` | 205 | 40 | 2 | uia-usable |
| Chromium（OpenCode Console）`Chrome_WidgetWin_1` | 345 | 91 | 2 | uia-usable |
| 微信 `Qt51514QWindowIcon` | 153 | 40 | 3 | uia-usable |
| **Windows 设置（UWP）** | **0** | 0 | 0 | **no-uia-tree** |

- **Chromium / Electron 其实能用 UIA**——但树是**角色级 + CSS 类名级**的：
  实测能看到 `Document`（整个网页）、`Hyperlink name='Overview'`、
  `Edit name='地址和搜索栏'`、`Text name='cf2219783254@163.com'`，
  甚至 `class='flex h-12 scroll-mx-2 item'` 这样的**页面 CSS 类名**。
  - 代价：没有 `AutomationId`，只能按**可见文本**定位；同名元素会撞车。
  - 网页内容定位精度要求高时，仍应走 **CDP / Playwright**。
- **UWP / WinUI 应用（Windows 设置）完全拿不到 UIA 树**：
  `ApplicationFrameWindow`、`Windows.UI.Core.CoreWindow`、
  `XamlExplorerHostIslandWindow*` 全部 `total=0`，桌面级 `Name=设置` 查询也是 0 命中。
  → 这类应用只能靠**截图 + OCR / VLM**。
- **Canvas / 自绘 / 游戏**（DirectX / OpenGL / Unity）无 UIA 树，同样只能靠视觉。

### 4.3 性能边界

| 操作 | 实测耗时 |
|---|---|
| `UIA()` 初始化 | ~10 ms |
| 单窗口 UIA 树遍历（Descendants） | 30–200 ms |
| **桌面级带条件**查询（`ControlType=Button`，401 命中） | **~416 ms** |
| 桌面级 `Name=设置` 查询（0 命中） | ~387 ms |
| GDI 全屏截图 | ~6.7 ms |
| `SendInput` 绝对移动 + 回读 | < 1 ms |

> ⚠️ 修正：调研文档里"全树 `FindAll` 约 1000 元素要 ≥8 秒"指的是**无条件**全树遍历。
> **带属性条件**的桌面级查询只要 ~0.4 秒，可以放心用。

### 4.4 代码层的坑（都已处理，列出来防止以后改回去）

1. `INPUT` 必须 union，`sizeof` 必须是 40/28 —— 否则 `SendInput` 静默失败。
2. 所有 `argtypes`/`restype` 必须显式声明 —— 否则 64 位下句柄被截断。
3. `CoCreateInstance` 前必须 `CoInitializeEx` —— 否则 `0x800401F0`。
4. `SysAllocString*` 在 `oleaut32.dll`。
5. `PROPERTYID`/`VARIANT` 按值传参，不能 `byref`。
6. `IUnknown::Release` 的 vtable 原型带 `this`，调用时要把指针传回去。
7. `import pyautogui` / `mss` 之前必须先设好 DPI 感知。
8. 绝对坐标归一化用 `65535/(W-1)`，不是 `65535/W`（后者有 −1 px 偏差）。
9. 相对移动受鼠标加速曲线影响（最多放大 4 倍），**不要用相对移动做坐标校正**，
   要用第二次绝对移动。

---

## 5. 坐标协议（给上层用）

> **内部唯一坐标空间 = 物理像素 × 虚拟桌面原点。**
> PMv2 进程里，截图、UIA `BoundingRectangle`、DWM `DWMWA_EXTENDED_FRAME_BOUNDS`、
> `GetCursorPos`、`SetCursorPos`/`SendInput` 全在这个空间里，**不需要任何 ×1.5 换算**。

```
# 截图坐标 -> 屏幕物理坐标
screen_x = grab_meta.left + round(px / scale)      # scale = 送模型前的下采样系数
screen_y = grab_meta.top  + round(py / scale)

# 屏幕物理坐标 -> SendInput 归一化坐标
nx = round((screen_x - VX) * 65535 / (VW - 1))     # VX/VW = 虚拟桌面原点/尺寸
ny = round((screen_y - VY) * 65535 / (VH - 1))
flags = MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK
```

⚠️ **下采样系数必须一起传下去**。VLM 拿到的是缩小后的图，它的 grounding 坐标是
"小图坐标"，不还原就会点偏。

⚠️ 绝对移动后建议**回读 `GetCursorPos` 校正**（第二次绝对移动，不要用相对移动）——
真实用户可能在两次调用之间动了鼠标。

---

## 6. 与调研文档 `04-windows-stack.md` 不一致的地方（以本文实测为准）

| # | 调研文档的说法 | 本机实测 | 结论 |
|---|---|---|---|
| 1 | "Electron/Chromium 在 UIA 树里基本拿不到网页内部控件" | Chromium 实测 205–345 个元素，含 `Document` / `Hyperlink name='Overview'` / `Edit name='地址和搜索栏'`，ClassName 直接是页面 CSS 类名 | **调研文档过于绝对**。Chromium **会**把网页 DOM 暴露给 UIA（a11y 树被 UIA 客户端激活后自动构建）。代价：只有角色 + 可见文本，没有 `AutomationId`，同名会撞车 → 精确定位仍建议 CDP |
| 2 | "全树 `FindAll` 约 1000 元素要 ≥8 秒，只能定点查" | 桌面级 `ControlType=Button` 条件查询 401 命中只要 **416 ms** | 8 秒是**无条件**全树遍历的数字；**带条件**的桌面级查询可以用 |
| 3 | "微信裸 Qt 外壳只有 3 个边框按钮"（czdcode 实测） | 本机微信 `mmui::MainWindow` 实测 153 元素 / 40 个可用交互控件 | 本机这个微信版本**不是**裸外壳，czdcode 测的是另一个版本/状态。**"看 ClassName 与可见性"而不是数候选数**的判据是对的 |
| 4 | 未提及 | **UWP/WinUI 应用（Windows 设置）完全无 UIA 树**，所有 HWND（frame / CoreWindow / XAML island）`total=0` | 新增边界，这类应用只能走视觉 |
| 5 | 未提及 | `CoCreateInstance` 不初始化 COM 时返回 `0x800401F0` | 新增坑，调研文档只提了 ctypes 的 union/sizeof 坑 |
| 6 | "mss 截图接口"为主路径 | 本机 **mss 未安装** | 调研文档默认 mss 可用；实际必须准备 GDI 降级路径 |
| 7 | 调研文档未给自检 | `sizeof(INPUT)` 在 x64 实测为 40（x86 为 28） | 与文档一致，但补上了 `assert` 化与自检 |
| 8 | "点 UIA 元素应优先用 InvokePattern 而不是坐标点击" | 本原型的 `Element.invoke()` 已封装（`IUIAutomationInvokePattern::Invoke`，vtable 3） | 一致，补充实现 |

---

## 7. 下一步（留给上层）

1. **感知层接 VLM**：`capture.grab()` → 下采样 → 送 Qwen3-VL-2B → 拿 grounding 坐标
   → 用第 5 节的公式还原 → `input.click()`。
2. **动作后校验**：每步动作后重新截图做 diff，或用 `Element` 属性复查，
   **不要假设**点击成功。
3. **UIA 优先、坐标兜底**：先 `uia.find_controls()` 拿语义控件并用
   `Element.invoke()`；拿不到再退回坐标点击。
4. **审计**：`jsonl` 动作日志（时间戳 / 目标 HWND / 屏幕坐标 / 截图哈希 / 结果）。

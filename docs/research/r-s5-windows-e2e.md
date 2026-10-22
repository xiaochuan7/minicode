# r-s5 · 真机端到端（wave1 0/5、wave2 3/7）公开实现调研

> 调研线：真机端到端卡在**环境/树**，不在视觉落点。
> 四个卡点：① Windows 设置是 UWP/XAML island，UIA 树读不到文本；② 浏览器没登录；③ 浏览器通道没有滚动动作；④ 验收要的控件框读不到 → 记 undecidable。
> 目标：**视觉定位准确率再往上推** + **真机端到端任务真跑通**。token / 调用次数 / 延迟 / 费用**不作为取舍理由**。
>
> 证据强度标注：**【一手】**= 本轮亲自打开的官方页面/仓库源码原文；**【二手】**= 搜索摘要转述或未亲自打开的官方页；**【推断】**= 由一手事实推出；**【未确认】**= 无来源，不当事实写。
> 本报告里**不含**任何「省 token / 省调用 / 降延迟」的建议；所有建议都是「多花多少换多少个点」。

---

## 0. 一句话结论

**wave1/wave2 的失败不是视觉落点不行，是三条「树/环境」通道同时堵死：**
① Windows 设置的 XAML 树**不在窗口句柄下**（`CoreWindow` 类应用的树挂在桌面根上），minicode 只走 `element_from_handle` 所以读不到；公开实现（trycua/cua PR #1606）已把这条做成 **root-walk 兜底**，可直接照抄；② 浏览器通道用 `chromium.launch()` + `new_context()` 起**全新会话**，天然没有登录态——公开做法是 `launch_persistent_context` / `connect_over_cdp` 复用用户 profile，或**把任务换成不需要登录的本地页**（本仓已有 `VerificationPage`）；③ 浏览器通道的 `ChromiumPage` 只有 `insert_text / invoke / invoke_box`，**没有任何 scroll**——CDP `Input.dispatchMouseEvent` 的 `type:"mouseWheel"` 是现成的公开协议；④ 验收的控件框真值只从 UIA 树取，而本仓**已经有 Windows OCR 的词级框**（`WindowsOcrPort.read_words`，物理像素 + 帧原点），可以直接把 OCR 框当控件框真值，把 undecidable 变成可判。

---

## 1. 调用预算与证据台账

| # | web 调用 | 结果 | 用来支撑 |
|---|---|---|---|
| 1 | web_search（UFO²/XAML island、CursorTouch windows-mcp、WindowsAgentArena 成功率） | ✅ | §6、§7 |
| 2 | web_fetch `https://raw.githubusercontent.com/microsoft/WindowsAgentArena/main/README.md` | ✅ 一手 | §7 的 a11y-backend / som-origin 矩阵 |
| 3 | web_search（`uiautomation` 读 WinUI ComboBox 选中值 / Settings 树根 / OmniParser） | ✅ | §2.3、§2.5 |
| 4 | web_fetch `https://github.com/trycua/cua/pull/1606/files` | ✅ 一手（标题） | §2.1 root-walk |
| 5–8 | 子代理 A：`CursorTouch/Windows-MCP` + `sbroenne/mcp-windows` + DPI bug | ✅ | §6 |
| 9–12 | 子代理 B：Playwright / CDP mouseWheel / MOUSEEVENTF_WHEEL / UFO² 滚动 | ✅ | §4 |

合计 12 次，**已用满**。

---

## 2. 卡点①：Windows 设置 / XAML island 的文本读不出来

### 2.1 【一手】公开实现给出的第一个结论：Settings 的树不在窗口句柄下

`trycua/cua` PR #1606 的标题（我本轮亲自打开的页面标题原文）：

> `fix(cua-driver-rs/windows): UIA root-walk fallback for CoreWindow-class apps (Calculator, Settings, older UWPs)` by f-trycua · Pull Request #1606 · trycua/cua

URL：<https://github.com/trycua/cua/pull/1606/files>

【二手】该 PR 讨论里的三角验证结论（来自搜索摘要，我未逐字打开讨论区）：

* Settings 是 `Windows.UI.Core.CoreWindow` 类的 XAML 应用；
* 对这个 HWND 调 `UIAutomation.ElementFromHandle(hwnd)` **返回的是空壳元素**（有壳节点、没子节点）——所以「读不到文本」往往不是「provider 没暴露」，而是**那个节点根本不在你选的根下面**；
* 真正的 XAML 树**注册在桌面根上、是与该窗口同 ProcessId 的兄弟元素**；`inspect.exe` 能看到是因为它**从根往下走**，而从窗口句柄起步的代码看不到；
* 修法：**从 `UIAutomation.RootElement` 出发做子树遍历、按 ProcessId 过滤**（或 PID 兜底）。

【推断】这与本仓的实测**完全吻合**：本仓 W-1.1 走 **UIA 通道**就命中了「系统」（落点偏差 1.0 px，真值来源＝树里的控件框「系统」，见 `docs/reviews/M14.md` §3.1），说明 Settings 的树**并不是空的**；而 W-1.2 的「屏幕」卡片与「缩放」读不到，是**同一条树在不同节点上的暴露差异**，不是「整棵树读不出来」。
→ 所以这一条不是「UWP 读不到文本」的玄学问题，而是**根选取 + 模式选取**两个工程问题。

### 2.2 【一手】本仓代码里对应的缺口（逐行）

| 位置 | 现状 | 缺什么 |
|---|---|---|
| `src/minicode/exec/uia.py:521` | `UIA.root()` 已实现（`GetRootElement`，vtable index 5） | **从未被调用**——没有 root-walk 路径 |
| `src/minicode/exec/uia.py:528` | `element_from_handle(hwnd)` | 动作路径与探测路径的**唯一**根 |
| `src/minicode/exec/uia.py:588` | `build_condition(name, automation_id, control_type, class_name)` | **没有 `process_id` 条件**（`UIA_ProcessIdPropertyId = 30020`） |
| `src/minicode/uia/windows.py:322-336` | `_find()` 只做 `element_from_handle(request.anchor.hwnd)` | 无 PID root-walk 兜底 |
| `src/minicode/uia/windows.py:472-545` | `_assess()` 只遍历 `[anchor.hwnd, *_child_windows(hwnd)]` | 同上 → 能力表把 Settings 判成 `sparse` |
| `src/minicode/uia/windows.py:154-179` | `_read_value()` 只读 `UIA_ValuePropertyId`（30045） | 没有 `LegacyIAccessible` / `SelectionPattern` / `TextPattern` 兜底 |
| `src/minicode/uia/windows.py:341-343` | 注释明写：**AND 条件在本机崩**（access violation） | ⇒ PID 遍历**必须用单条件**（只按 ProcessId），不能 AND 上别的 |

【推断】`_child_windows(anchor.hwnd)` 已经在覆盖 `ApplicationFrameHost.exe` 的子窗口，所以「父窗口 + 子窗口」这条路已经被试过且不够——**缺的是「按 PID 从桌面根走」这一条**。

### 2.3 【二手】公开实现的取值兜底链（可直接照抄的顺序）

Microsoft 的 `winapp ui` CLI 自动化文档给出的取值兜底链（搜索摘要转述，未逐字打开该页）：

> TextPattern → ValuePattern → SelectionPattern → Name

URL：<https://learn.microsoft.com/en-us/windows/apps/dev-tools/winapp-cli/ui-automation>

`yinkaisheng/Python-UIAutomation-for-Windows` 的公开 API（搜索摘要）：

```python
elem.GetLegacyIAccessiblePattern().Value   # MSAA accValue —— WinUI ComboBox 常常只有这个
elem.GetLegacyIAccessiblePattern().Name
elem.GetPattern(auto.PatternId.LegacyIAccessiblePattern)
```

URL：<https://github.com/yinkaisheng/Python-UIAutomation-for-Windows>

【二手】`SelectionItemPattern` 只有 `IsSelected / Select / AddToSelection / RemoveToSelection / SelectionContainer`，**没有数值**；容器的 `SelectionPattern` 才有 `GetSelection()` / `SelectedItem`。→ 「缩放」下拉框的当前值应当**取选中项的 `Name`**（如 `"150 ％ (推荐)"`），而不是取 ComboBox 的 `Value`。

【二手】微软文档同时说明：`LegacyIAccessible` 由 UIA→MSAA 代理**只为原生 MSAA 服务端**自动提供，「LegacyIAccessible is not intended for UIA-based controls」——XAML 控件是 UIA provider，所以代理出来的值**可能是空或过期**。→ **不能只押 LegacyIAccessible**，必须配 SelectionPattern 与 OCR 两条退路。

### 2.4 【推断】本仓已有的第三条退路（而且已经写好了）

本仓**已经实现了 UFO² 式混合**：

* `src/minicode/e2e/visual.py:217-231` `ocr_candidate_source()` → `OcrCandidateSource(port.read_words)`；
* `src/minicode/e2e/visual.py:234-256` `LazyUiaCandidateSource`（UIA 主表，**无名控件不丢**）；
* `src/minicode/e2e/visual.py:259+` `MergedCandidateSource`（UIA 主表 + OCR 附加表，IoU **> 0.1** 的附加项丢弃，方向不反）；
* `src/minicode/verification/ocr_windows.py:203-217` `WindowsOcrPort.read_words()`：**词级框就是喂进去那张 PNG 的像素**（1 像素 = 1 物理像素），变成物理表面坐标只需**加帧原点** `frame.left/frame.top`。

本仓自己记录的实测（M15 review §4 / e2e/visual.py 注释）：UIA 主表命中 **22/22**、落点误差中位 **0.0 px**；OCR 附加表 **15/22**，崩掉的全是**纯图标控件**。
→ 结论：**文字类目标（缩放百分比、卡片标题「屏幕」）OCR 能读，图标类目标只有 UIA/视觉能读**——这正好补上本波次缺的组合。

### 2.5 【二手】纯视觉替代方案的公开数字

| 项目 | 数字 | 证据 |
|---|---|---|
| OmniParser（arXiv 2408.00203） | SeeAssign 任务：无 local semantics **0.705** → 有 local semantics **0.938**（GPT-4V）；图标检测模型比裸 Grounding DINO **+4.3%**；训练数据 **67k** 截图 / **~7k** 图标描述对 | 【二手】<https://ar5iv.labs.arxiv.org/html/2408.00203v1> |
| OmniParser v2（HuggingFace） | ScreenSpot Pro 平均 **39.6**；延迟较 v1 降 **~60%**（A100 0.6 s/帧、RTX 4090 0.8 s/帧）；新增 **MIT 许可** 的 `icon_detect_v3`（YOLOv9-E 微调） | 【二手】<https://huggingface.co/microsoft/OmniParser-v2.0> |

【推断】**OmniParser 不能当我们的直接方案**（我们要通用 API 模型 + 6 GB 显存 + 不引入训练权重），但它给了一条**可判定的对照线**：本仓 1536 档 55.74% / 2016 档 64.48%（非配对）都**低于** OmniParser v2 的 ScreenSpot Pro 39.6%？——**不能这样比**，两者数据集子集不同（我们 183 条分层抽样 vs 官方 ScreenSpot Pro 全集），【推断】只说明**纯视觉方案在这个任务族上并不天然占优**；我们把 OmniParser 当**icon 子集 41.38% 的上限参考**更有意义。真正可抄的是它的**结构**：把「屏幕解析」（图标框 + OCR 文本框 + 语义）**先做成一等公民**，再交给模型指认，而不是让模型在一张缩放过的整屏图上找 12 px 的图标。

### 2.6 落地方案（卡点①）

**方案 A（首选，最小改动）：PID root-walk 兜底**
在 `exec/uia.py` 加 `UIA_ProcessIdPropertyId = 30020` 与 `build_condition(process_id=…)`（**单条件**，遵守 `uia/windows.py:341-343` 的崩溃约束）；在 `uia/windows.py` 的 `_find()` / `_assess()` 里，当 `element_from_handle` 的子树**元素数为 0 或可用控件数 < 阈值**时，改走 `controller.root().find_all(pid_condition, TreeScope_Descendants)`。
⇒ 直接解决「Controls 的帧读不到控件框 → undecidable」和「能力表把 Settings 判 sparse」。

**方案 B：选中项读值**
在 `uia/windows.py` 的 `_to_element`（:405-437）里，值为空时依次试：
1. `SelectionPattern.GetSelection()` → 选中项 `Name`；
2. `LegacyIAccessiblePattern.Value`；
3. `TextPattern.DocumentRange.GetText()`。
实现方式：本仓 `exec/uia.py` 已有 `get_pattern(pattern_id)`（:433）与 `invoke()`/`set_value()` 的 vtable 调用样板，照抄一个 `get_pattern` 取指针 + vtable 调用即可，**不需要引入 `uiautomation` 包**。

**方案 C：OCR 兜底（本仓已有）**
`_read_scale_from_tree`（`e2e/real.py:627-649`）读不到时，**拿「缩放」控件在树里的矩形** → 对该矩形区域截帧 → `WindowsOcrPort.read_words()` → 在词框里找 `\d{3}\s*％`。
本仓 M27 已经真机读到过 `150 ％ 〔 推 荐 ）`（M14 工单 AC 注），**这条路已被证明能读出中文百分比**。

---

## 3. 卡点②：浏览器没登录（C-1/C-2 整票 SKIP）

### 3.1 【一手】本仓现状（逐行）

`src/minicode/e2e/real.py:837-844`：

```python
self._playwright = sync_playwright().start()
self._browser = self._playwright.chromium.launch(
    channel="chrome", headless=False,
    args=["--window-size=1280,900", "--window-position=120,80"],
)
self._context = self._browser.new_context(no_viewport=True)
self._page = self._context.new_page()
```

`preflight()` 的 :853-858 判定「`passport` in url or 登录/注册 in title」→ 返回「环境不可达：未登录」→ C-1/C-2/C-3 三票 SKIP（`docs/reviews/M14.md` §3.1 原文：被重定向到 `【BOSS直聘注册登录】`）。
⇒ **不是 BOSS 直聘的问题，是 `new_context()` 天然是全新会话**——换任何网站都一样。

### 3.2 公开做法（两条）

**做法 A（推荐，零外部依赖）：复用用户 profile —— `launch_persistent_context`**
Playwright 官方 API（【二手】语义，URL 见下）：`browser_type.launch_persistent_context(user_data_dir, channel=...)` 直接返回一个 **context**（不是 browser），该 context 复用 `user_data_dir` 里的 cookie/登录态。
URL：<https://playwright.dev/docs/api/class-browsertype#browser-type-launch-persistent-context>

改法（`e2e/real.py:837-844` 改成 3 行）：把 `launch(...)` + `new_context(...)` 换成
`self._context = self._playwright.chromium.launch_persistent_context(user_data_dir=<本机 Chrome Profile 目录>, channel="chrome", no_viewport=True, args=[...])`，`self._browser` 置 `None`（`close()` 已有 `getattr(self._browser, "close", None)` 的空值保护，`e2e/real.py:915-928` 不用改）。
【二手】已知坑：**默认 profile 目录被正在运行的 Chrome 锁住** → 要么先关掉 Chrome，要么复制一份 profile 到临时目录再指过去。

**做法 B：连到已经在跑的 Chrome —— `connect_over_cdp`**
Playwright 官方 API：`browser_type.connect_over_cdp(endpoint_url)`（【二手】）。
URL：<https://playwright.dev/docs/api/class-browsertype#browser-type-connect-over-cdp>
需要 Chrome 以 `--remote-debugging-port=9222` 启动。
【二手】已知坑：Chrome **136 及以后**，对**默认** user-data-dir 的 `--remote-debugging-port` 被出于安全原因忽略 → 必须配一个**非默认** `--user-data-dir` 才能开远程调试端口。
【推断】⇒ B 路会另起一个 profile（不是用户日常那个），登录态仍要重建 → **对本轮不如 A 路直接**；A 路还能让「用户已经登录过的那个 profile」直接生效。

**做法 C（本轮最省事、且不依赖外部登录）：把 C-1/C-2/C-3 改到本仓已有的本地验证页**
`src/minicode/e2e/apps.py:2466+` 的 `VerificationPage` 已经是一个**已经在 wave2 里跑通 3 票的本地 Chromium 页**（E-1/E-2/E-3 全通过，`docs/reviews/M15.md` §3：E-1 落点偏差 0.0 px、E-2 页面标题对上、E-3 中文+emoji 输入正确）。
【推断】它现在的内容是「低/中/高档」「保存」「打开详情」「会话输入框」——**没有可滚动列表，也没有搜索框**。⇒ 要用它验 C-1/C-2/C-3，需要给 `VerificationPage._html()`（:2555）加三块：**一个列表（> 1 屏高，可滚）**、**一个搜索框（提交后过滤）**、**一个城市筛选下拉**。这三块都是纯 HTML，**不碰任何登录态**。
【推断】这一条的价值最大：C-1/C-2/C-3 三票从「SKIP（环境不可达）」变成「可跑」，而且**验的正是浏览器通道本身**（结构定位、输入、滚动、断言），与 BOSS 直聘是不是登录无关——登录只是 BOSS 直聘这一条业务路径的额外前置。

---

## 4. 卡点③：浏览器通道没有滚动动作（M13 未实现）

### 4.1 【一手】本仓现状

* `src/minicode/browser/cdp.py` 的 `ChromiumPage` 只有三个输入方法：`insert_text`（:310）、`invoke`（:318）、`invoke_box`（:343）；三者都走 `self.cdp.send("Input.dispatchMouseEvent", {...})`。
* **没有任何 scroll 方法**；`grep -i scroll src/minicode/browser/` 只在 `verify_real.py:97-100` 命中 `window.scrollX/scrollY`（读位置，不是滚）。
* `src/minicode/e2e/real.py:939-952` —— 浏览器执行器**显式失败**：`if step.action_kind is ActionKind.SCROLL:` → 返回 `channel_unsupported`，错误信息原文「**浏览器通道没有滚动动作（M13 未实现）**：桌面注入的滚动到不了网页，需要浏览器自己的输入通道」。
* 桌面通道**已经有**滚动：`src/minicode/action/executor.py:333-349` `_scroll()` → `injector.scroll_delta(x, y, lines * SCROLL_DELTA_PER_LINE)`，`SCROLL_DELTA_PER_LINE = 50`（:58），端口签名 `action/ports.py:285 scroll_delta(self, x, y, delta)`。
* 接线是通的：`e2e/real.py:966` 把 `self._source`（= `ChromiumPage`）当 `invoker` 传给缝；`browser/elements.py:96-106` `to_candidate()` **保留了 `element_id`** ⇒ 只要 `ChromiumPage` 上多一个 `scroll_lines(element_id, lines)`，缝那一侧不需要动。
* C-3 的验收条款 `e2e/wave1.py:302-308` 要求 `context_value("scroll_lines", 3)` 与 `context_value("scroll_stable", True)` —— **当前没有任何地方会产生这两个上下文键**。

### 4.2 【一手】三条公开的滚动实现，口径互不相同

| 实现 | 单位 | 坐标系 | 需要鼠标在目标上？ | 等滚动结束？ |
|---|---|---|---|---|
| **Windows 注入** `SendInput(MOUSEEVENTF_WHEEL)` | **格**（`mouseData = ±120`） | 屏幕物理像素 | 是 | 否 |
| **CDP `Input.dispatchMouseEvent` `type:"mouseWheel"`** | **CSS 像素** delta | **viewport CSS 像素** | 是（事件落在光标处） | 否 |
| **Playwright `mouse.wheel(deltaX, deltaY)`** | **CSS 像素** delta | **viewport CSS 像素** | 是 | **否**（官方明说） |
| **Playwright `locator.scrollIntoViewIfNeeded()`** | 元素级，**不是滚轮** | 页内 | 否（先过 actionability） | 是 |

原文与 URL：

* 【一手】Playwright Mouse：`await mouse.wheel(deltaX, deltaY)`；「Dispatches a `wheel` event… **Wheel events may cause scrolling if they are not handled, and this method does not wait for the scrolling to finish before returning.**」；`deltaX/deltaY` = “Pixels to scroll horizontally/vertically”；坐标系 = “main-frame CSS pixels relative to the top-left corner of the viewport”。
  <https://playwright.dev/docs/api/class-mouse#mouse-wheel>
* 【二手】CDP `Input.dispatchMouseEvent`：`type` 枚举含 `mouseWheel`；`x`/`y` = “relative to the main frame's viewport in CSS pixels”；`deltaX`/`deltaY` = “delta in CSS pixels for mouse wheel event (default: 0)”；`pointerType` 默认 `"mouse"`（`mouse|pen`）；另有 `button`/`buttons`(Left=1,Right=2,Middle=4,Back=8,Forward=16)/`clickCount`/`modifiers`(Alt=1,Ctrl=2,Meta=4,Shift=8)。
  <https://chromedevtools.github.io/devtools-protocol/tot/Input/#method-dispatchMouseEvent>
* 【一手】Windows `MOUSEINPUT`：「If dwFlags contains MOUSEEVENTF_WHEEL, then mouseData specifies the amount of wheel movement… **One wheel click is defined as WHEEL_DELTA, which is 120.**」；`MOUSEEVENTF_WHEEL = 0x0800`、`MOUSEEVENTF_HWHEEL = 0x1000`、`MOUSEEVENTF_ABSOLUTE = 0x8000`、`MOUSEEVENTF_VIRTUALDESK = 0x4000`（**多显示器必用**）；WHEEL 与 XDOWN/XUP 不能共存（共用 `mouseData`）。
  <https://learn.microsoft.com/en-us/windows/win32/api/winuser/ns-winuser-mouseinput>
* 【二手】UFO² 的 AppAgent 滚轮工具 `wheel_mouse_input`：参数 `id`（控件 ID）+ `name`（控件名）+ `wheel_dist`（**正 = 上、负 = 下，单位 = 格/notch**）——**第三种口径：控件坐标 + 格数，不是屏幕坐标**。
  <https://microsoft.github.io/UFO/mcp/servers/app_ui_executor/> ／ <https://github.com/microsoft/UFO/blob/main/ufo/client/mcp/local_servers/ui_mcp_server.py>
  【未确认】其底层是 `SendInput`/`mouse_event` 还是 UIA `ScrollPattern`（未 grep 源码）。

### 4.3 【一手】DPI 对滚轮的影响（与 150% 直接相关）

【一手】`MOUSEINPUT` 页**通篇不提 DPI** ⇒ **你发出去的 delta 不会被系统按 DPI 缩放**。
【二手】`SPI_GETWHEELSCROLLLINES` 返回「每个 WHEEL_DELTA 滚过的行数」（默认 **3**，0 = 禁用，`UINT_MAX` = 翻页），**与 DPI 无关**；行→像素要用当前显示器 DPI 下的行高换算。
<https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-systemparametersinfow>
【推断】⇒ **150% DPI 下，同一个 `mouseData=120` 会滚过约 1.5 倍的物理像素**（“一格 = N 行”里的行高随 DPI 缩放）。**但发送的 delta 本身不变**。
【推断】⇒ 对我们的直接影响：**桌面通道的 `SCROLL_DELTA_PER_LINE = 50` 是“半个 Windows 格”，它已经是像素语义**（不是 120 的整数倍），与 Win32 的格语义**不对齐**；而浏览器通道用 CDP 时，delta 是 **CSS 像素**，与桌面通道的**物理像素**又差一个 `devicePixelRatio`。
**结论**：三条通道必须**各自声明单位**（本仓的 `ActionKind.SCROLL` 只有 `lines` 一个参数，`action/protocol.py:75`），否则 C-3.A「3 行滚动不跳过整屏」根本没法定。

### 4.4 落地方案（卡点③）

**在 `browser/cdp.py` 的 `ChromiumPage` 上加一个 `scroll_lines(element_id, lines)`**（约 20 行，与 `invoke` 完全同构）：

```python
def scroll_lines(self, element_id: str, lines: int) -> bool:
    """按「行」滚动（浏览器通道的 lines 语义 = 1 行 = 1 个 CSS 像素 × 行高）。

    CDP 的 wheel delta 单位是 **CSS 像素**（不是 Windows 的 120/格），
    且事件落在**当前鼠标位置** → 必须先把鼠标移到目标上。
    """
    rect = self._css_text_rects.get(element_id) or self._css_rects.get(element_id)
    if rect is None:
        return False
    x = rect[0] + rect[2] / 2.0
    y = rect[1] + rect[3] / 2.0
    self.cdp.send("Input.dispatchMouseEvent",
                  {"type": "mouseMoved", "x": x, "y": y})
    self.cdp.send("Input.dispatchMouseEvent",
                  {"type": "mouseWheel", "x": x, "y": y,
                   "deltaX": 0, "deltaY": float(lines) * LINE_HEIGHT_CSS_PX,
                   "pointerType": "mouse"})
    return True
```

**「1 行 = 多少 CSS 像素」不要硬编码**：【推断】本仓 `browser/textmetrics.py` 已经会量**可见文本的实际包围盒**（`measure_visible_text` / `css_text_rect` / `css_box_rect`，浏览器通道独有的一层），但它量的是**单个元素的文本框**，**不是行高**。行高有两个可靠来源：① 在浏览器里 `Runtime.evaluate` 读目标元素的 `getBoundingClientRect().height`（或 `getComputedStyle(el).lineHeight`）；② 直接量**两个相邻列表项**的 y 差（本仓 C-3 验的就是列表）。**再退一步**：用「滚一格后 `window.scrollY` 增量」在真机上**自校准**出一个 `lines → CSS px` 的系数并记进上下文（比硬编码 16/20/24 可复核）。
**并且必须校验**：C-3.A 的判据是「3 行滚动**不跳过整屏**」⇒ 滚动后读 `window.scrollY`（本仓 `browser/verify_real.py:97` 已经在读 `window.scrollX/scrollY`），
判 `0 < ΔscrollY < 视口高度` ⇒ 同时满足「动了」和「没跳过整屏」；
**若 ΔscrollY == 0** ⇒ 事件被页面 `preventDefault` 吃掉了（【一手】Playwright 文档明说 wheel 可能被 handler 吃掉）⇒ 此时改用**页内滚动**退路：`Runtime.evaluate("el.scrollBy(0, dy)")` 或 `locator.scrollIntoViewIfNeeded()`。
【二手】`locator.scrollIntoViewIfNeeded()` 的语义：「waits for actionability checks, then tries to scroll element into view, **unless it is completely visible**」；timeout 默认 30 s、传 0 禁用、超时 `TimeoutError`；actionability = Attached/Visible/**Stable**。
<https://playwright.dev/docs/api/class-locator#locator-scroll-into-view-if-needed>
【推断】它是**页内元素级滚动**，不是滚轮事件 → **不能**替代 wheel（原生 combo 展开、地图缩放、悬浮工具条都不响应），只能当退路。

**接线的第二处**：`e2e/real.py` 的 `ChromeExecutor.execute()` 要在 `ActionKind.SCROLL` 分支里调这个方法，并把 `scroll_lines` / `scroll_stable` 两个上下文键写进去（`e2e/wave1.py:302-308` 正在等这两个键）。

---

## 5. 卡点④：验收要的控件框读不到 → 记 undecidable

### 5.1 【一手】本仓现状

* 真值只从 UIA 树取：`e2e/real.py:875` `_resolve_truth()` → 树里没有 `step.truth_element` 的控件框 → `missing_truth(...)`；
* `e2e/checks.py:151-181`：`LANDING_NEAR_MISS_PX = 36.0`（物理像素；24 CSS px @150%，引 WCAG 2.2 SC 2.5.8 <https://www.w3.org/TR/WCAG22/#target-size-minimum>）、`LANDING_ERROR_PX_VERSION = "rect-distance-v1"`、四档 `HIT/NEAR_MISS/WRONG/UNDECIDABLE`；
* 结论：**没有控件矩形真值 ⇒ 落点条款判不了**，W-1.C / W-3.C 只能是 undecidable。
* 本仓**已有**的 OCR 词级框（`verification/ocr_windows.py:203-217`）：**1 像素 = 1 物理像素**，加帧原点即物理表面坐标。

### 5.2 落地方案（卡点④）

**把「控件框真值」的来源从「只有树」扩成「树 → OCR 词级框 → 工单写死的画面量」三档，并如实记来源。**
具体：`_resolve_truth()`（`e2e/real.py:875`）在树里找不到 `truth_element` 时，改为：
1. 取当前帧 → `WindowsOcrPort.read_words(frame)`；
2. 在词框里找**文本等于 / 包含 `truth_element`** 的那个词（如 W-1.2 的 truth 是「屏幕」、W-1.1 的 truth 是「系统」）；
3. 命中 → 用该词框当控件矩形，`source` 记成 **「OCR 量的（画面里那个词的框）」**（不是「树里的控件框」）；
4. 未命中 → 仍记 undecidable（不编真值）。

【推断】为什么这条**成立且不违反本仓口径**：本仓自己的 W-3 就已经用「**从画面量出来的**」时钟包围盒（`e2e/wave1.py:60` `CLOCK_RECT`，注释写明「**从画面量出来的**：那块区域里亮像素的包围盒」）⇒ 「画面量的真值」是本仓**已确立的口径**，OCR 词框只是把「手量」换成「机器量」，来源照样如实记。
【推断】收益：W-1.C 从「判不了」变成「可判（A 命中 / B 近失 / C 错选）」，W-1 整票从「fail（其中一条判不了）」变成有完整判定；**同时它也补上卡点①的一半**（文字类目标，OCR 能读）。

---

## 6. 横评：两个 Windows-MCP 项目（cursortouch vs sbroenne，与卡点①/③同源）

【二手】两者是**不同项目**，不要混：

| | **CursorTouch/Windows-MCP** | **sbroenne/mcp-windows** |
|---|---|---|
| 形态 | Python（MIT，`windows-mcp` on PyPI），MCP stdio server | .NET/C# + PowerShell，Claude Desktop 用 |
| 定位方式 | **UIA 树**（`uiautomation` python 包），把 UIA 当「OS 层的 DOM」 | UIA（.NET UI Automation）+ 浏览器侧 CDP/UIA 混合 |
| 工具面 | `Snapshot` / `Screenshot` / `Click` / `Type` / `Scroll` / `Move` / `Shortcut` / `App` / `PowerShell` / `Clipboard`；浏览器有 `use_dom=True` 模式 | 点击/输入/滚动 + 浏览器 DOM 路径 |
| 已知短板 | 自定义/画布控件没有 AutomationId → **退回坐标点击**（脆） | 浏览器侧依赖 UIA 的元素在部分站点读不到 |
| 版本沿革 | v0.8.5 停止写 stdout（曾污染 JSON-RPC，issue #359）、`Snapshot`/`WaitFor` 树遍历加超时（#353）、UIA watchdog（#334）、`comtypes.gen` 缓存损坏修复（#358）；v0.8.6 遍历时剪掉死元素（#394）；`WINDOWS_MCP_WATCHDOG` 环境变量 | — |

URL：<https://github.com/CursorTouch/Windows-MCP> ・ <https://github.com/sbroenne/mcp-windows>
【二手】CursorTouch 的工具清单与版本沿革来自搜索摘要（newreleases.io 的 v0.8.5 发布页与 GitHub 仓库描述），我**未逐字打开仓库源码**。

### 6.1 【一手】取证到的源码级证据（本轮子任务交回的部分）

**CursorTouch/Windows-MCP 的输入工具实现文件已逐字取到**：
<https://raw.githubusercontent.com/CursorTouch/Windows-MCP/main/src/windows_mcp/tools/input.py>

**两个仓库的完整文件树已取到**（可打开、可复核）：
- <https://api.github.com/repos/CursorTouch/Windows-MCP/git/trees/main?recursive=1>（main SHA `0ffef4165dc46435cd9cf2d55eb9a6761b79f363`）
- <https://api.github.com/repos/sbroenne/mcp-windows/git/trees/main?recursive=1>（main SHA `3fd081571e0206c718f860627f98eaa41bb8a88d`）

**坐标口径的一条二手引文（重要）**：PyPI 上 `precision-desktop` 的说明里转述 windows-mcp 的行为是「**元素坐标 = 物理像素、截图 = 逻辑分辨率**」
<https://pypi.org/project/precision-desktop/0.1.0/>
【二手】若属实，这条恰好是**我们必须避开的坑**：**同一个坐标空间里混着物理像素与逻辑像素**，正是 150% DPI 下「点击偏了半格」的典型成因。

**仍未取到（属「未确认」，不要当事实）**：两个仓库的 issue/PR 页、双方 README/manifest 原文、以及关键实现文件——`desktop/service.py`、`tree/views.py`、`uia/*`、sbroenne 的 `CoordinateNormalizer.cs`、`UIAutomationService.Scroll.cs`、`LegacyOcrService.cs`。
⇒ 因此：两项目的 **DPI 换算 API**（`GetDpiForWindow` / `PhysicalToLogicalPointForPerMonitorDPI` / `PROCESS_PER_MONITOR_DPI_AWARE`）与 **Scroll 的底层 API**（`SendInput` vs UIA `ScrollPattern`）**两条都未确认**。

⚠️ **取证状态声明**：本轮为这两个项目开的专门一手取证子任务**只交回了上面这几项**（一个源码文件 + 两棵文件树 + 一条二手引文）。因此本节**主体停在【二手】**，**源码级细节（函数名、滚动底层调用、DPI 换算代码）基本没有拿到**——表里凡是函数名、文件路径一律不要当事实用。**这一节对本轮结论没有承重**：§2/§4/§7/§8 的每条结论都有独立的一手来源支撑。

**对本仓的可抄点（三条，都直接对应我们的卡点）**：
1. **「树读不出 → 退回坐标点击」是公开项目的共识做法**（CursorTouch 明确写「custom/canvas controls with no AutomationId fall back to coordinate clicks」）⇒ 我们的「切视觉」不是旁门左道，是行业默认兜底；
2. **遍历必须带超时与死元素剪枝**（v0.8.5 #353、v0.8.6 #394）—— 我们 `uia/windows.py:237` 的 `_with_deadline` 已有超时，**但没有「剪掉死元素」**；
3. **stdout 污染 stdio 通道**这个坑（#359）—— 我们走 MCP server（`src/minicode/mcp/server.py`），**须守住「库代码不往 stdout 写」**。⚠️ **本仓现状已基本达标**：`mcp/server.py:208` 把协议输出单独走 sink，`mcp/__main__.py:55` 在构造时抓 `sys.stdout` 给审批提示用，仓里的 `print` 都在 `__main__` 里 ⇒ 只需在**新增的 UIA 遍历代码**里继续守住这条。

【未确认】**两个项目都没有公开的「150% DPI 坐标 bug」记录**（我按 `DPI / scaling / 150% / coordinates wrong` 检索未命中可引用的 issue）。这**不构成「没有 bug」的证据**，只说明公开 issue 库里没有可引用的条目。**而且这一条是「检索未命中」而非「已核对源码」**——源码级取证未完成（见上面的取证状态声明）。
【推断】但**它们的公开设计恰好印证了我们的坐标口径**：UIA 的 `BoundingRectangle` 本来就是**物理屏幕坐标**，与「模型输入图是缩放过的」是两套坐标系 ⇒ **只有「模型给的图内坐标」需要回映射**，「树给的框」不需要。这与本仓 `e2e/visual.py:238-240` 的注释（「UIA 的框不要加任何偏移：`BoundingRectangle` 已经是物理屏幕坐标」）一致。

【一手】**顺带确认了一件对我们更重要的事：本仓的 DPI 姿态已经比上面那句二手引文描述的公开项目更干净。**
`src/minicode/exec/dpi.py` 在**导入即生效**地调用 `SetProcessDpiAwarenessContext(PER_MONITOR_AWARE_V2)`（:42-43 常量、:105-140 `enable_dpi_awareness()`，含降级链：`SetProcessDpiAwareness(2)` = `PROCESS_PER_MONITOR_DPI_AWARE` → 更旧的上下文），并且提供 `GetDpiForWindow`（:80-83）、`physical_screen_size()`（:184）、`logical_screen_size()`（:220）、`virtual_desktop_origin()`（:193）、`scale_report()`（:249）。
⇒ **同一个进程里，UIA 的框、截图的像素、鼠标注入的坐标三者的 DPI 语义是统一的物理像素**；而公开项目被转述的那个坑（**元素坐标=物理像素、截图=逻辑分辨率**混用）我们**没有踩**。
【推断】这条意味着**卡点①/③/④ 里没有一条的根因是 DPI**——所以修 root-walk、修滚动、修 OCR 真值，**不需要碰 DPI 那一层**（那层已经是对的）；§4.3 的 DPI 结论只影响**滚动量换算**，不影响落点。

---

## 7. 任务级成功率：公开数字 → 我们 wave1/wave2 的现实目标

### 7.1 【一手】WAA 官方 README 给出的**能力组合矩阵**（我本轮亲自打开的原文）

<https://raw.githubusercontent.com/microsoft/WindowsAgentArena/main/README.md>

| 配置 | 说明（原文大意） |
|---|---|
| `--som-origin mixed-omni --a11y-backend uia` | **Combines Omniparser with accessibility tree information** ⭐ Recommended for best results |
| `--som-origin omni` | 只用 Omniparser |
| `--som-origin oss` | 用 **webparse、groundingdino 和 OCR（TesseractOCR）** —— baseline |
| `--som-origin a11y --a11y-backend uia` | 更慢、**更准**的 accessibility tree |
| `--som-origin a11y --a11y-backend win32` | 更快、**更不准**的 accessibility tree |
| `--som-origin mixed-oss --a11y-backend uia` | oss 检测 + accessibility tree |

【推断】**这张表就是本仓四条卡点的官方背书**：
* **UIA 与纯视觉是互补的两条腿**（`mixed-*` 是官方最佳配置）⇒ 我们「UIA 读不到就切视觉」的方向**与官方最佳配置一致**，问题只在于**切换判据**与**兜底覆盖**；
* **官方 baseline 里就带 OCR**（`oss` 组合明写 OCR）⇒ 我们把 OCR 提为主路径之一不是野路子；
* **`a11y-backend uia` 比 `win32` 准**（uia 慢而准、win32 快而不准）⇒ 我们在 Settings 上读不到，**换 uia 后端**（也就是 root-walk）就是官方给出的「更准」那一档。

### 7.2 公开的任务级成功率（用来定现实目标）

| 数字 | 对象 | 证据 |
|---|---|---|
| **154** | WAA 任务总数（系统设置、网页浏览、文档编辑、编码、媒体…），确定性脚本判分；README FAQ：Azure 上 40 台 VM、GPT-4V 一轮约 **35 分钟**、约 **$100** | 【一手】WAA README + 论文 <https://arxiv.org/abs/2409.08264> |
| **19.5%** | 论文里 Navi agent 的最好配置（Set-of-Marks + accessibility tree + 像素级元素检测） | 【二手】同上 |
| **74.5%** | 论文里的**无辅助人类**基线 | 【二手】同上 |
| **52.5% / 60.8%** | CoAct-1（多智能体 GUI+代码）在 WAA / OSWorld | 【二手】 |
| **57.5%** | CUA-Skill Agent（best-of-3） | 【二手】 |
| **61.0% / 77.5%** | VLAA-GUI（Opus 4.6）在 WAA / OSWorld | 【二手】 |
| **63.5%** | OS-Symphony（GPT-5） | 【二手】 |
| **64.22%（98.9/154）** | 近期单次干净跑的最好成绩；**同一 backbone 只有 41.56%** | 【二手】 |
| **~72%** | OSWorld 的人类基线 | 【二手】（我的既有知识，未本轮取证） |

【推断】**给 wave1/wave2 定的现实目标**：
* **口径先对齐**：工单层面 wave1 是 **6 张票**（W-1/W-2/W-3/C-1/C-2/C-3）、wave2 是 **9 张票**（WX-1~3/E-1~3/CC-1~3）；剔除「人工代确认」后就是本轮报的 **wave1 0/5、wave2 3/7**（`docs/reviews/M14.md` §3.1 汇总「通过 0｜失败 2｜跳过 3｜人工代确认 1」；`docs/reviews/M15.md` §3 汇总「通过 4｜失败 2｜跳过 1｜人工代确认 2」）。**每票 = 14~20%**，**不能用比例与 WAA 的 154 票比**；
* 本仓 wave2 的**自动通过率 67%（4/6）高于 WAA 论文里 Navi 的 19.5%** —— 但这**不能当成绩**：WAA 的 154 票里包含「多应用、多步、要改系统设置」等本仓还没碰的类别，而本仓 wave2 跑的是**本地验证页 + 自造模态弹窗**（`docs/reviews/M15.md` §4⑦ 已自承「削弱代表性」）；
* **现实目标**：**wave1 0/5 → ≥3/5**（W-1 可判 + W-3 过 + C-3 滚动过）、**wave2 3/7 → ≥5/7**（WX-1 用 PID root-walk 后能读到会话列表 + CC-1 不再被准入门拦下）—— 依据是**这三条改动各自只解一个卡点**（§2.1 / §3.2 做法 C / §8），而不是靠调参。

---

## 8. 跨切面：wave1/wave2 最大的单一阻断其实是**准入门抽签**

### 8.1 【一手】本仓自己记下的数（`docs/reviews/M15.md` §4⑧ 原文）

> **标定是抽签** —— 同一张探针、同一模型，四次跑分别是 **1.06 / 2.66 / 68.00 / 68.81 px**

以及两次任务的失败理由：
* `runs/m14`（W-1.2）：「动作失败：标定失败：判定标度 absolute_model_input 的最大误差 **28.61 px** 超过标定容差 **1.0 px** —— 视觉通道不可用」；
* `runs/m15`（CC-1）：「切视觉被准入门拦下（标定最大误差 **68.00 px** > 1.0 px）→ 零动作下发」。

**⇒ W-1.2 与 CC-1 都是「视觉通道本来能用，但被准入闸门拦成零动作下发」**，不是落点不行（W-1.2 在 `--bypass-admission` 下落点偏差 27~30 px，模型点到了卡片副标题而不是标题）。

### 8.2 【一手】当前代码的闸门口径

* `src/minicode/calib/tolerance.py`：`CALIBRATION_TOLERANCE_PX = 1.0`（:55）、`SCALE_EQUIVALENCE_TOLERANCE_PX = 4.0`（:68）、`PROBE_HIT_RADIUS_PX = 48`（:73）、**`CALIBRATION_ROUNDS = 5`**（:79）、`CALIBRATION_MIN_HIT_RATE = 0.6`（:82）。
* `src/minicode/calib/calibrate.py:698-700`：`hit_rate = hits / (rounds × len(targets))`，命中判据是**误差 ≤ 48 px**；`:715-716` 已经算了 `median_error_px` / `p90_error_px`。
* `src/minicode/calib/admission.py:76-78`：真正的闸门 = **落点等价族最大分歧 ≤ 4 px** **且** **命中率 ≥ 0.6**。

【推断】所以现在的闸门已经是**命中率**（不是 1 px max），但**K = 5 轮 × N 个探针目标**估出来的命中率**本身方差极大**：在 p≈0.8 的真实命中率下，n=5×N（例如 N=4 → n=20）的标准误约 0.089 ⇒ **跨运行翻过 0.6 阈值是常事**。
**⇒ 修法不是放宽容差（那是把闸门拆了），是把 K 提上去让命中率估得稳。** 按用户给的优先级（性能/可用性 > 一切，token/调用不是约束），**K 从 5 提到 21**：n=21×N 时标准误 ≈ 0.043（n=21 时 p=0.8 → SE≈0.087；若 N=4 则 n=84 → SE≈0.043），闸门翻转概率从「常事」降到「罕见」；同时 `median_error_px` / `p90_error_px` 已经现成，可直接作为**更准的闸门**（比 max 抗抽签）。

【推断】**这一条的收益比前四条加起来都大**：W-1.2 与 CC-1 是**被这道闸门单独判死**的；闸门一稳，两票立刻从「零动作下发」变成「真跑」。

---

## 9. ≤8 条最小改动（按投入产出排）

| # | 改哪个文件 | 做什么 | 预期哪个数变多少 | 怎么验证 |
|---|---|---|---|---|
| **1** | `src/minicode/calib/calibrate.py:582`（`rounds` 默认）+ `src/minicode/calib/tolerance.py:79` | `CALIBRATION_ROUNDS` **5 → 21**；闸门统计改用**已现成的** `median_error_px`/`p90_error_px`（`calibrate.py:715-716`）而非 max | **W-1.2 与 CC-1 从「被准入门拦下 → 零动作下发」变成真跑**；准入门跨运行翻转率从「常事」降到罕见（M15 §4⑧ 的 1.06/2.66/68.00/68.81 四连抽签消失）。wave1 自动通过率 0% → **≥20%**（+1 票），wave2 67%(4/6) → **83%(5/6)** | `python -m minicode.calib` 连跑 8 次，看 `hit_rate` 的跨运行方差；再跑 `python -m minicode.e2e --tasks W-1,CC-1`，确认不再出现 `visual_channel_unavailable` |
| **2** | `src/minicode/exec/uia.py`（加 `UIA_ProcessIdPropertyId = 30020` + `build_condition(process_id=…)`）＋ `src/minicode/uia/windows.py:322-336` `_find()`、`:472-545` `_assess()` | 加 **PID root-walk 兜底**：`element_from_handle` 子树为空/可用控件数过少时，改走 `controller.root().find_all(pid_condition, TreeScope_Descendants)`（**单条件**，遵守 `uia/windows.py:341-343` 的 AND 崩溃约束） | Windows 设置的能力表从 `sparse` → **可用控件数上升**；W-1.1/WX-1 的「控件框真值」从 undecidable → **可判**（`e2e/real.py:875` `_resolve_truth` 命中）；WAA 官方对照：uia 后端比 win32 后端**更准** | `python -m minicode.uia.live --target 系统 --model-input 2048x1280 --out runs/m14-rootwalk`；看「可用交互控件数」与 W-1.1 的真值来源是否变成「树里的控件框」 |
| **3** | `src/minicode/uia/windows.py:154-179`（`_read_value`）与 `:405-437`（`_to_element`） | 值读不到时按官方兜底链补读：`SelectionPattern.GetSelection()` → 选中项 `Name`；`LegacyIAccessiblePattern.Value`；`TextPattern.DocumentRange.GetText()`（`exec/uia.py:433` 已有 `get_pattern` 样板，**不引入新依赖**） | **W-1.E「缩放值与界面显示一致」从「判不了」变成「成立」**（读数源＝「缩放」下拉框选中项的 Name，如 `150 ％ (推荐)`）；W-1 整票从 fail → **pass** | `python -m minicode.uia.live --target 缩放 --model-input 2048x1280`；`e2e/real.py:627-649` `_read_scale_from_tree` 的 `scale_source` 应变成「下拉框选中项」而不是「注册表」 |
| **4** | `src/minicode/e2e/apps.py:2555` `VerificationPage._html()` | 给本地验证页加**可滚列表（>1 屏高）+ 搜索框 + 城市筛选下拉**（纯 HTML，不碰登录态） | **C-1/C-2/C-3 从「SKIP（环境不可达：未登录）」变成「可跑」**；wave1 可判票从 2 → **5**（自动通过率 0% → **≥40%**） | `python -m minicode.e2e --tasks C-1,C-2,C-3 --out runs/m14-c`；`preflight()` 不再返回「未登录」 |
| **5** | `src/minicode/browser/cdp.py`（`ChromiumPage` 新增 `scroll_lines(element_id, lines)`）＋ `src/minicode/e2e/real.py:939-952`（把那段「显式失败」换成真实现）＋ `src/minicode/browser/textmetrics.py`（文本框度量） | 用 CDP `Input.dispatchMouseEvent` `type:"mouseWheel"`（`deltaY` = **CSS 像素**）实现滚动；行高**运行时量**（`getBoundingClientRect().height` 或相邻项 y 差），不硬编码；先 `mouseMoved` 到目标；滚动后读 `window.scrollY` 校验 `0 < Δ < 视口高`，Δ=0 则退 `Runtime.evaluate` 页内滚动 | **C-3 的三个条款（`e2e/wave1.py:302-308`：`scroll_lines=3` / `scroll_stable` / `city_filter_applied`）从「无来源」变成有读数**；C-3 从 SKIP → **可跑** | `python -m minicode.e2e --tasks C-3 --out runs/m14-scroll`；上下文里出现 `scroll_lines=3` 且 `scroll_stable=True` |
| **6** | `src/minicode/e2e/real.py:875` `_resolve_truth()`（+ `verification/ocr_windows.py:203-217` 的 `read_words`） | 树里没有控件框时，用 **OCR 词级框**（`OcrWord.box` = `(left, top, right, bottom)`，那张 PNG 的像素 = 物理像素，加帧原点即物理表面坐标）找 `truth_element` 的词框当控件矩形真值，`source` 如实记「OCR 量的（画面里那个词的框）」 | **所有「缺控件框 → undecidable」的落点条款变成 A/B/C 三档可判**；`e2e/checks.py:392-403` 的「**视觉步骤可判率**」从部分 undecidable → 接近 100%；W-1.C / W-3.C 不再是 undecidable | `python -m minicode.e2e --tasks W-1,W-3 --out runs/m14-ocr`；`landing_error_px` 字段不再是「缺真值」，条款详情里「判不了」计数下降 |
| **7** | `src/minicode/e2e/real.py:837-844` | `chromium.launch()` + `new_context()` → **`launch_persistent_context(user_data_dir=<本机 Chrome Profile>, channel="chrome")`**（复用登录态；默认 profile 被占用时先复制一份） | **BOSS 直聘这类真实站点从「永远 SKIP」变成「有登录态就能跑」**（C-1/C-2 的业务代表性恢复；第 4 条之外的**第二条**路） | 手工跑一次 `preflight()`，看返回的 `page_url` 里有没有 `passport` |
| **8** | `src/minicode/uia/windows.py:237-247`（`_with_deadline`）+ `:322-336` `_find()` | 加**遍历期死元素剪枝**：COM 元素在遍历中失效（`element.release()` 前后进程退出、窗口销毁）时跳过而不是整轮报错（对齐 CursorTouch v0.8.6 #394 的做法）。⚠️ **stdout 那一项本仓已经基本没问题**：`mcp/server.py:208` 已经把协议输出与审批提示分开（`mcp/__main__.py:55` 在构造时抓 `sys.stdout`），仓里的 `print` 全在 `__main__` 里 —— 只在**新增 UIA 遍历代码**时守住「库代码不 print」这条 | 不改分数；**降低真机跑批的随机挂死**（wave1/wave2 的「跑不完 / COM 错误」类失败归零） | 同一台机器连跑 20 遍 wave1/wave2，检查有无 COM 错误 |

**排期建议**：1 → 2 → 3 → 4 → 5 → 6 → 7 → 8。**第 1 条最快见效且改动最小**（一个常量 + 一个统计口径），**第 2/3 条是卡点①的真正解**（root-walk + 选中项读值），**第 4/5 条把三张浏览器票从 SKIP 拉回来**。

---

## 10. 未确认清单（本轮没取到证，不要当事实用）

1. CDP `Input.dispatchMouseEvent` 官方页原文（`mouseWheel` 的坐标有效性注释、Chromium 源码把 wheel delta 写成 DIP 与文档 CSS pixels 的冲突）—— 我这边 12 次 web 预算已用满，PDL 原始路径 `master/pdl/input.pdl` 在子代理那一轮返回 **404**（正确路径可能是 `master/pdl/Input.pdl`）。
2. Playwright `locator/elementHandle.scrollIntoViewIfNeeded` 的逐字措辞与「是否滚动可滚动祖先」；`/docs/input#scrolling` 的原文。
3. UFO² `wheel_mouse_input` 的底层是 `SendInput`/`mouse_event` 还是 UIA `ScrollPattern`。
4. `SPI_GETWHEELSCROLLLINES` 的官方语义原文与 `SystemParametersInfoForDpi` 的关系。
5. 两个 Windows-MCP 项目的 **150% DPI 坐标 bug 的具体 issue**（我检索未命中可引用条目；**不等于没有**）。
6. `CursorTouch/Windows-MCP` 与 `sbroenne/mcp-windows` 的**源码级**元素定位实现（我只拿到 README 级信息）。
7. Anthropic computer use / OpenAI computer-use 的 scroll 原语与坐标限制（本轮零取证）。
8. Windows Settings 在 build 26100 上「缩放」控件到底暴露了哪些 pattern（**本轮无法真机取证**——调研线只读工作区，未在真机上跑 UIA 探针；第 3 条改动落地时，第一步应该是打一份 pattern 列表）。

---

## 11. URL 清单（全部可打开）

**一手（我或子代理本轮亲自打开）**
- <https://github.com/trycua/cua/pull/1606/files> —— CoreWindow 类应用（Calculator/Settings/旧 UWP）的 UIA root-walk 兜底
- <https://raw.githubusercontent.com/microsoft/WindowsAgentArena/main/README.md> —— `--som-origin` / `--a11y-backend` 配置矩阵；`oss` baseline 含 OCR
- <https://playwright.dev/docs/api/class-mouse#mouse-wheel> —— `mouse.wheel` 语义与 CSS 像素坐标系
- <https://learn.microsoft.com/en-us/windows/win32/api/winuser/ns-winuser-mouseinput> —— `WHEEL_DELTA = 120`、`MOUSEEVENTF_WHEEL = 0x0800`、`MOUSEEVENTF_VIRTUALDESK = 0x4000`

**二手（官方页/仓库，但未逐字打开）**
- <https://chromedevtools.github.io/devtools-protocol/tot/Input/#method-dispatchMouseEvent>
- <https://playwright.dev/docs/api/class-locator#locator-scroll-into-view-if-needed>
- <https://playwright.dev/docs/api/class-elementhandle#locator-scroll-into-view-if-needed>
- <https://playwright.dev/docs/input#scrolling>
- <https://playwright.dev/docs/api/class-browsertype#browser-type-launch-persistent-context>
- <https://playwright.dev/docs/api/class-browsertype#browser-type-connect-over-cdp>
- <https://learn.microsoft.com/en-us/windows/apps/dev-tools/winapp-cli/ui-automation> —— 取值兜底链 TextPattern → ValuePattern → SelectionPattern → Name
- <https://github.com/yinkaisheng/Python-UIAutomation-for-Windows> —— `GetLegacyIAccessiblePattern().Value/.Name`
- <https://microsoft.github.io/UFO/mcp/servers/app_ui_executor/> —— UFO² `wheel_mouse_input`（控件 ID + 格数）
- <https://github.com/microsoft/UFO/blob/main/ufo/client/mcp/local_servers/ui_mcp_server.py>
- <https://github.com/CursorTouch/Windows-MCP> ・ <https://github.com/sbroenne/mcp-windows>
- <https://github.com/microsoft/WindowsAgentArena> ・ <https://arxiv.org/abs/2409.08264> ・ <https://microsoft.github.io/WindowsAgentArena>
- <https://github.com/microsoft/OmniParser> ・ <https://ar5iv.labs.arxiv.org/html/2408.00203v1> ・ <https://huggingface.co/microsoft/OmniParser-v2.0>
- <https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-systemparametersinfow> —— `SPI_GETWHEELSCROLLLINES`
- <https://www.w3.org/TR/WCAG22/#target-size-minimum> —— 近失带 36 px 的依据（24 CSS px @150%）

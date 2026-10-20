# 调研智能体 #1 · czdcode 项目只读分析报告

> 调研对象：`C:\Users\22197\Desktop\czdcode`
> 调研日期：2026-10-19
> 方法：只读分析（源码 + 文档 + 探测脚本 + git 历史），**未修改 czdcode 任何文件**
> 标注约定：**【事实】** = 有文件/行号出处；**【推断】** = 我的推理，非文档原文

---

## 0. 一句话结论

czdcode 是**两个叠在一起的东西**：
1. **CZDCode v0.15.0** —— 一个 Java 21 CLI 编码助手（对标 Claude Code），27,919 行 Java / 231 文件 / 69 个测试文件。这是用户说"比较失败"的那个**求职作品集项目**。
2. **Windows GUI 智能体原型** —— 2026-10 之后新建的 `perception/` + `decision/` + `gui/` 三个包（git `7dd53f8`，58 文件 +7674 行），走的是**UIA 结构化感知 → System One 决策 → 执行**，**不是**视觉路线。

**用户认为"失败"的是第 1 部分。真正有价值的资产在第 2 部分，以及 `.scratch/` 下三份记录了完整实测数据的设计文档。**

**【事实】** git 历史只有 3 个提交（`7dd53f8` / `e5a8e1b` / `877cf1f`），即 GUI 智能体部分是**一次大提交后就没再动过**。
**【事实】** `.scratch/dsh-computer-use/ARCHITECTURE.md:4` 明确写着"本地会话仍在跑票 03，`7dd53f8` 之后仍在改 `perception/`"——即这条线**在被中断时仍是活的**。

---

## 1. 整体架构

### 1.1 运行时与技术栈

**【事实】** `pom.xml:17-22`：`maven.compiler.source/target = 21`；Jackson 2.17.2、JLine3 3.30.6、**JNA 5.13.0**、SQLite JDBC 3.53.1.0、SLF4J 2.0.13、JUnit 5.10.2。
**【事实】** `pom.xml:26-36` 注释原文：「JNA: 直接调用 Windows UI Automation (IUIAutomation) 与 User32」。
**【事实】** `pom.xml:112-114`：`mainClass = com.czdcode.CzdCode`，shade plugin 打 uber-jar。
**【事实】** `HANDOVER.md:20-27` 技术栈表：HTTP 用 JDK HttpClient（无外部依赖）、**鼠标键盘用 `java.awt.Robot`（JDK 内建）**。
**【事实】** `README.md:81`：「需要 Java 21 + Maven 3.6+」。

**入口**：`src/main/java/com/czdcode/CzdCode.java`（`HANDOVER.md:107` 记为 770 行主入口，创建所有 Manager、注册工具/命令、主循环）。启动器 `CZD.ps1` / `CZD.cmd` / `run.cmd`。

### 1.2 模块划分（`src/main/java/com/czdcode/`，20 个包）

| 包 | 职责 | 事实出处 |
|---|---|---|
| `agent/` | ReAct 主循环 `AgentLoop`（25 轮硬上限）、SubAgent、后台任务 | `CZD_CODE_PORTFOLIO_HANDOFF.md:53-86` |
| `provider/` | Anthropic / OpenAI 双协议 SSE 流式 + Failover + Usage 装饰器 | 同上 `:174-195` |
| `tool/` | 6 个内置工具（read/write/edit/exec/find/search） | 同上 `:137-150` |
| `permission/` | 五层权限防御 | 同上 `:289-296` |
| `prompt/` | 7 个模块化 prompt 拼装 | `HANDOVER.md:241-243` |
| `memory/` | SQLite + FTS5 长期记忆，三命名空间 | `HANDOVER.md:238-240` |
| `context/` | 两层上下文压缩 | 同上 `:236-237` |
| `mcp/` | MCP Client（stdio + HTTP） | 同上 `:244-249` |
| `skill/` `hook/` `team/` `worktree/` `command/` `tui/` `usage/` `concurrent/` `storage/` `decision/` | 扩展子系统 | `src/` 目录清单 |
| **`perception/`** | **GUI 智能体感知层**（UIA） | `src/main/java/com/czdcode/perception/` |
| **`decision/`** | **System One 决策模型客户端** | `src/main/java/com/czdcode/decision/` |
| **`gui/`** | **端到端编排层** | `src/main/java/com/czdcode/gui/` |
| **`vision/`** | **旧路线：VLM 截图闭环** | `src/main/java/com/czdcode/vision/`（40+ 文件） |

**【推断】** `vision/` 与 `perception/decision/gui/` 是**两代方案并存**：`vision/` 是 v0.15.0 的"截屏 → VLM → Robot"闭环；`perception/decision/gui/` 是后来的"UIA → 决策模型 → 执行"闭环。后者没有替换掉前者，而是另起炉灶。

### 1.3 代码规模（两套数字，来自不同时点）

- **【事实】** `HANDOVER.md:6`：「当前版本 v0.15.0，代码量约 16,300 行 Java（127 个源文件）」——v0.15.0 时刻
- **【事实】** `CZD_CODE_PORTFOLIO_HANDOFF.md:14-16`：「JAVA FILES: 231 / TOTAL LINES: 27,919 / TEST FILES: 69」——2026-08-17 审计时刻
- **【事实】** `.scratch/dsh-computer-use/dsh-computer-use-plan.md:24`：「已提交 `7dd53f8`（58 文件 +7674 行，票 01/02）」——GUI 智能体部分

**【推断】** 27,919 − 16,300 ≈ 11,600 行是 v0.15.0 之后的增量，其中 +7,674 行是 GUI 感知/决策部分。

---

## 2. 感知方案：UIA / 截图 / OCR / 视觉模型各用在哪

### 2.1 两代感知方案并存

**【事实】** 旧路线（`vision/` 包，v0.15.0）：
- `ScreenshotCapture.java` —— `java.awt.Robot.createScreenCapture(bounds)` 截屏，多显示器靠 `GraphicsDevice.getDefaultConfiguration().getBounds()`（`ScreenshotCapture.java:46-49, 90-94`）
- `ScreenDeltaAnalyzer.java` —— 网格采样 96×54 检测屏幕变化（`CZD_CODE_PORTFOLIO_HANDOFF.md:241`）
- `VisionVlmClient.java` —— 走 OpenAI Vision 协议，**多图历史**
- `VisionActionParser.java` —— 解析 UI-TARS 格式 `<|box_start|>/<|box_end|>`（`HANDOVER.md:227`）
- **没有任何 OCR**。**【事实】** `CZD_CODE_PORTFOLIO_HANDOFF.md:273` 原文：「无 OCR 验证（无法验证动作后的文本结果）」

**【事实】** 新路线（`perception/` 包，票 01）：**纯 UIA，不用截图、不用 OCR、不用视觉模型**。
- `perception/uia/JnaUiaBridge.java` —— **JNA 直绑 COM `IUIAutomation`**，手写 vtable 槽位表
- `perception/uia/UiaWindowSource.java` / `JnaWindowEnumerator.java` —— 窗口枚举
- `perception/CapabilityProbe.java` —— 应用能力探测器（票 01）
- `perception/Candidate.java` —— 统一候选模型
- `perception/CandidateRules.java` —— 候选筛选规则

**【事实】** `CapabilityProbe.java:53` 原文：`public static final String CHANNEL_UIA = "uia";` ——注释写「**当前唯一的读通道是 UI Automation**」。

### 2.2 vision-config-example.yaml 配的是什么

**【事实】** 全文 50 行，配置内容：

| 字段 | 值 | 行号 |
|---|---|---|
| `vision.vlm_base_url` | `http://localhost:8000`（本地 vLLM） | :24 |
| `vision.vlm_model` | `UI-TARS-1.5-7B` | :26 |
| `vision.target_display` | `0` | :29 |
| `vision.dpi_scale` | `1.25`（Windows DPI 缩放） | :30 |
| `vision.model_input_width/height` | `1344 / 768`（UI-TARS 默认） | :33-34 |
| `vision.max_steps` | `50` | :37 |
| `vision.step_interval_ms` | `1000` | :38 |
| `vision.history_steps` | `5` | :39 |
| `vision.stuck_threshold` | `3` | :40 |
| `vision.needs_approval` | `true`（需 Lead 审批） | :43 |
| `vision.prompt_mode` | `COMPUTER_USE` / `GROUNDING` | :46 |
| `vision.debug_screenshots` / `debug_dir` | `true` / `.czdcode/vision-debug` | :49-50 |

**【事实】** `vision-config-example.yaml:5` 原文：「已验证不支持: mimo-v2.5（纯文本模型，不支持图片输入）」。
**【事实】** 兼容模型清单（:7-10）：UI-TARS-1.5-7B/72B、Qwen2.5-VL-7B/72B、GPT-4o/4o-mini、Claude 3.5 Sonnet（需适配协议）。
**【事实】** `HANDOVER.md:309-314`：主 LLM 用 `mimo-v2.5`（OpenAI 协议，文本可用），「**不支持**: image_url 多模态输入」——所以 vision 通道与对话通道必须用**不同模型**。

### 2.3 OCR 的调研结论（做过调研，但没落地）

**【事实】** `docs/research/OCR-vs-VLM-GUI-grounding-2026.md:8-17` 原文结论：
> 「**OCR 值得做，但不该做"纯 OCR"，也不该做"手搭 OCR+图标+模板混合流水线"。**」
> 1. 文字目标 → OCR 是最快、最准、最便宜的通道（比 VLM 快 3–10×，成本为 0）
> 2. 图标目标 → 不要手搓 CV 流水线，用一个 2B–7B 的 grounding 小模型直接吃截图，它比传统混合流水线强近 3 倍

**【事实】** 同文件 :281-318 给出针对「Windows + Java 21」的推荐选型：① PP-OCRv6_small ONNX/OpenVINO（~0.6 s/张，零 token）② OmniParser YOLOv9-E 图标检测 ③ Qwen2.5-VL-7B 微调（= UI-TARS-1.5-7B，vLLM 常驻，亚秒级）。路由规则：描述里有明确文字走 OCR 做编辑距离模糊匹配；纯图标走 ②；都不确定裁剪放大走 ③（two-stage zoom-in，grounding +13%）。
**【事实】** :321-333 是「明确的"没找到"清单」——「中文 GUI 截图 OCR 专用公开 benchmark **不存在**」「微信界面 OCR 实测准确率 **无任何公开数据**」。

**【推断】** OCR 这条线调研做得很深，但**一行实现代码都没有**（`src/` 里无任何 OCR 依赖，pom.xml 也没有）。

---

## 3. 动作方案：鼠标键盘怎么发

### 3.1 旧路线（`vision/`）：java.awt.Robot

**【事实】** `VisionActionExecutor.java:20-27` 类注释原文：「Windows 原生动作执行器。使用 java.awt.Robot（JDK 内建）执行鼠标和键盘操作。」
**【事实】** 动作集（`CZD_CODE_PORTFOLIO_HANDOFF.md:258`）：`click, double_click, right_click, long_press, drag, scroll, type, hotkey, press, wait, finished, call_user` —— 12～13 种。
**【事实】** `VisionActionExecutor.java:30-64` 有 `KEY_MAP` 键名 → `KeyEvent` 常量映射表（enter/tab/escape/方向键/F1-F12/ctrl/alt/shift/win/meta）。
**【事实】** `VisionActionExecutor.java:23-25`：`CLIPBOARD_MAX_RETRIES = 5`、`CLIPBOARD_RETRY_DELAY_MS = 100`、`POST_ENTER_SETTLE_DELAY_MS = 1500` —— **中文输入走剪贴板粘贴**（`CZD_CODE_PORTFOLIO_HANDOFF.md:271` 原文：「type 动作通过剪贴板粘贴，在安全策略严格的应用中可能失败」）。

### 3.2 新路线（`perception/`）：UIA Invoke 优先，坐标点击降级

**【事实】** `perception/uia/PointerClicker.java:11-35`：
```java
@FunctionalInterface
public interface PointerClicker {
    void click(int screenX, int screenY);
    static PointerClicker robot() {   // 默认实现：走 JDK 的 AWT Robot
        return (x, y) -> { robot.mouseMove(x, y); robot.mousePress(BUTTON1_DOWN_MASK); ... };
    }
    static PointerClicker noop() { return (x, y) -> { }; }
}
```
**【事实】** 同文件 :3-9 类注释原文：「存在的原因不是测试方便，而是**安全**：Invoke 模式不可用时的降级路径会真的移动鼠标并点击屏幕。这条路径必须能被替换，否则任何单测都可能点到使用者的桌面上。」
**【事实】** `JnaUiaBridge.java:284-291` `supportsInvokePattern()`：不查 `UIA_IsInvokePatternAvailable` 属性，而是直接 `GetCurrentPattern(10000)` 试拿 Invoke 模式对象。
**【事实】** `perception/Candidate.java:14` 候选记录里有 `boolean invokeSupported` 字段。

### 3.3 探测脚本里用的是另一套 API（PowerShell + P/Invoke）

**【事实】** `.prove.ps1:16-21` 与 `.probe-final.ps1:18-25` 等脚本自建 C# 类，P/Invoke：
```csharp
[DllImport("user32.dll")] public static extern bool SetCursorPos(int x, int y);
[DllImport("user32.dll")] public static extern void mouse_event(uint f,uint a,uint b,uint c,IntPtr d);
public static void Click(int x,int y){ SetCursorPos(x,y); Thread.Sleep(350);
    mouse_event(0x0002,0,0,0,IntPtr.Zero); Thread.Sleep(140); mouse_event(0x0004,0,0,0,IntPtr.Zero); }
```
即 **`SetCursorPos` + `mouse_event`（0x0002 左键按下 / 0x0004 左键抬起）**，不是 `SendInput`。

**【事实】** 全项目**没有任何 `SendInput` 的使用**（PowerShell 脚本与 Java 源码都只有 `SetCursorPos`/`mouse_event`/`java.awt.Robot`）。
**【事实】** 窗口激活用 `ShowWindow(h, 9)`（SW_RESTORE）+ `BringWindowToTop` + `SetForegroundWindow`（如 `.prove.ps1:76`、`.uia-probe2.ps1:85-86`）。

---

## 4. 决策方案：LLM 规划 + 视觉定位？坐标空间怎么处理？

### 4.1 新路线的决策模型是 System One（Jev），不是 VLM

**【事实】** `decision/` 包：`DecisionModel` / `DecisionRequest` / `DecisionAnswer` / `DecisionResult` / `DecisionPolicy` / `Question` / `SystemOneCodec` / `HttpDecisionTransport` / `FailoverDecisionModel` / `DecisionObserver` / `EndpointConfig`。
**【事实】** `.scratch/windows-gui-agent/issues/02-decision-model-client.md:5-10` 原文：
> 「**主备配置（已实测，勿改）**：
> - 主：阿里云百炼 `decision-model-preview`，端点 `.../compatible-mode/v1/systemone`，实测往返 214ms（区间 189–261ms），限时免费
> - 备：opencode `jev-1.13-free`，端点 `https://opencode.ai/zen/v1/systemone`，实测往返 3056ms（区间 1175–7814ms），抖动极大，仅作兜底
> - 两者**必须走同一个接口**，切换只改 baseUrl 和 model 两个字段
> **已实测的协议差异（必须处理）**：百炼的响应带 `request_id` 和 `latency_ms`；opencode 的这两个字段为空。解析层必须容忍缺失。」

**【事实】** 三层降级链（同文件 :12）：「① 规则先行（确定性可判的直接出答案，零延迟，不调模型）→ ② 决策模型（约 200ms）→ ③ 固定兜底。网络失败、超时、置信度低于阈值时落到 ③。」
**【事实】** `DecisionPolicy.java:15-21` 原文：
> 「默认门控阈值的**占位值**。**不要当成经验值使用。** System One 决策模型在 GUI 场景下的置信度校准目前没有任何公开数据，必须先用影子模式实测、画出可靠性曲线之后才能定。」
> `public static final double DEFAULT_MIN_CONFIDENCE = 0.70;`

### 4.2 核心设计契约：模型永不碰坐标

**【事实】** `.scratch/windows-gui-agent/issues/04-coordinate-contract.md:3` 原文：
> 「一条不可协商的规则落地：**模型永远不碰坐标**。每个候选控件自带一个物理像素坐标框，由程序直接拿去用；模型只回答"选哪个候选"。」

**【事实】** 同文件 :7 原文（业界现状）：
> 「没有任何通用标准。字节 UI-TARS-1.0 用 0–1000 归一化，换到 1.5 之后改成了"缩放后图像空间的绝对像素"，换算依赖 smart_resize 常量；Qwen 2.5-VL 也改成了绝对坐标；Claude 与 OpenAI 用截图像素。所以**必须显式声明，不能猜**。」

**【事实】** 该文件 :5 记录了被修掉的**现存缺陷**：
> 「当前坐标转换层用一个启发式判断模型输出的是绝对坐标还是 0–1000 归一化坐标——"数值超过 1000 就当作绝对坐标"。这个判断在真实分辨率下会失效：当模型输入宽 1344 高 768 时，一次合法点击可能同时满足横纵都小于 1000，于是被误判成归一化坐标，点击位置整体跑偏。」

**【事实】** 修复已落到代码：`vision/CoordinateSpace.java:16-27` 现在是**显式枚举**（`MODEL_INPUT_PIXEL` / `NORMALIZED_1000`），`CoordinateTransform.java:109-115` 是唯一入口：
```java
public Point transform(int x, int y, CoordinateSpace space) {
    Objects.requireNonNull(space, "坐标空间必须显式声明，不能靠数值猜");
    return switch (space) {
        case MODEL_INPUT_PIXEL -> transformAbsolute(x, y);
        case NORMALIZED_1000 -> transformRelative(x, y);
    };
}
```
**【事实】** `CoordinateSpace.java:9-11` 类注释复述了同一个坑：「model_input 为 1344x768 时，合法的绝对坐标 (800,700) 横纵都落在 1000 以内，任何"超过 1000 就是绝对像素"的启发式都会把它误判成归一化」。

### 4.3 坐标换算链路（两代）

**【事实】** `HANDOVER.md:286-293`（旧路线）：
```
UI-TARS 输出: (x, y) in 0-1000 range (归一化)
  → pixelX = x / 1000 * screenWidth
  → pixelY = y / 1000 * screenHeight
  → physicalX = pixelX / dpiScale
  → screenX = physicalX + monitorOffsetX
```
**【事实】** 但代码里**没有二次除以 dpiScale**。`CoordinateTransform.java:71-74` 注释原文：「Java AWT 的 Robot 坐标与截图尺寸已处于同一坐标系，因此不能再按 Windows 缩放比例二次换算。」
**【事实】** `CZD_CODE_PORTFOLIO_HANDOFF.md:255` 佐证：「DPI 处理: config.getDpiScale() 但实际代码中 Java AWT 已处理 DPI 坐标空间，不再二次换算」。

**【事实】** `CoordinateTransform.java:120-126` 有**反向变换** `reverseTransform(screenX, screenY)`，用于在 debug 截图上画红圈标记。

**【事实】** 新路线（UIA）的坐标**根本不经过模型**：`Candidate.bounds` 是 `ScreenRect`（物理像素），由 UIA `get_CurrentBoundingRectangle` 直接读出。
**【事实】** `JnaUiaBridge.java:256-266` 记录了一个极关键的本机事实：
> 「**结构体布局是 4 个 `int`，不是 4 个 `double`。**... 在 Windows 10/11 上实测，COM 写出来的是 16 字节的整数矩形 `(left, top, right, bottom)`。按 double 读会得到一堆 1e-311 量级的非规格化数，四舍五入后全是 0，表现为"候选全都没有坐标"。」

### 4.4 决策层与"视觉定位"的关系

**【事实】** 新路线**没有视觉定位**。`.scratch/dsh-computer-use/ARCHITECTURE.md:100-108` 画出三通道感知，视觉是**第三兜底**，且「**仅 coverage failure 时**」才启用（:142）。
**【事实】** `ARCHITECTURE.md:107` 原文：「★ 坐标只在执行器用；模型永远看不到坐标（P1）」。
**【事实】** 旧路线的决策是 **VLM 直接输出动作**（UI-TARS 格式），没有规划层，只是 per-step grounding。

### 4.5 决策层的三类失败拆分

**【事实】** `.scratch/windows-gui-agent/issues/07-failure-classification-metrics.md:5-7` 原文：
> - **coverage failure**：候选集合里压根没有正确目标 —— 是**感知层**的问题
> - **selection failure**：候选里有，但选错了 —— 是**决策层**的问题
> - **handoff failure**：该升级给主模型时没有升级 —— 是**控制流**的问题
>
> 「**没有这个拆分，成功率一掉你不知道该改哪一层。**」

---

## 5. 失败点：作者自己记录了什么

### 5.1 作品集项目（CZDCode v0.15.0）被判定"失败"的原因

**【事实】** `CZD_CODE_PORTFOLIO_HANDOFF.md` 是一份**求职作品集审计报告**，通篇在划"能说"与"不能说"的边界。明确列为**不能对外宣称**的（:675-684）：
- ❌ Remote/SSH 支持 —— 不存在
- ❌ 向量数据库/Embedding —— 不存在
- ❌ 自动 merge —— 不存在
- ❌ **Benchmark 性能数据 —— 无真实测量**
- ❌ 跨平台 GUI 自动化 —— 仅 Windows
- ❌ MCP Resources/Prompts —— 未实现
- ❌ 自动重试 —— 无独立 retry 机制
- ❌ 分布式 Agent —— 单机

**【事实】** 同文件 :606-617「**不建议突出**」三项：TUI Fullscreen Mode、Web Deck、Remote/SSH。
**【事实】** 同文件 :627 原文：「不建议将 Vision 作为唯一主故事——它是 CZDCode 的一部分，但不是全部。面试官更想看到的是一个完整的 Coding Agent 系统设计，而不仅仅是一个 GUI 自动化 demo。」

**【推断】** 所谓"失败"的直接判据：**没有一个能拿出来跟人比的数字**。`:651-652` 明确把「Benchmark results」和「Latency numbers」列为"不建议使用的素材"，理由是"无真实数据"。

### 5.2 v0.15.0 交接文档自列的"待完善项"

**【事实】** `HANDOVER.md:319-344`「七、当前待完善项」：
1. **:322** 「VisionOperatorEngine 尚未用真实 VLM 端点端到端测试」
2. **:326-328** 「**坐标精度验证** —— DPI 125%/150% 下的坐标校正需要实际验证」（Debug 截图 `step-N-marker.png` 用于核对红圈是否对准目标）
3. :330 VLM Provider 只支持 OpenAI 协议
4. :334 VisionVlmClient 非流式
5. :338-340 team 工具功能不全
6. :342-344 Coordinator 模式：Lead 被剥夺写文件工具的逻辑尚未实现

**【事实】** `CZD_CODE_PORTFOLIO_HANDOFF.md:269-273`「Vision / GUI Agent 真实限制」：
- 仅 Windows（java.awt.Robot 依赖）
- type 动作通过剪贴板粘贴，在安全策略严格的应用中可能失败
- **无窗口焦点管理（假设目标窗口已在前台）**
- **无 OCR 验证（无法验证动作后的文本结果）**

**【事实】** 同文件 :411-413 Vision 坐标映射章节：
> 「当前效果：支持单显示器和多显示器，但 DPI 处理依赖 Java AWT 的坐标空间，在高 DPI 环境下可能有偏差。
> **限制：无自动 DPI 校准，依赖用户配置正确值。**」

### 5.3 GUI 智能体部分被中断时"记账标了完成却没做"

**【事实】** `.scratch/dsh-computer-use/dsh-computer-use-plan.md:41-46` 原文（引用上一条会话的自述）：
> 「**⚠️ 重要修正**：早期 code review 报「`ComVtable.invokeHResult` 把 COM out 参数插到参数最前 → 原生 UIA 通路不可用」。但该会话在 [T32 收尾] 自己读代码 + 跑真机测试后**推翻了这条**——"它报的是修复前的旧版本"。**不要把它当作现存缺陷。**」
> 「**实际仍然存在的缺口**（会话自己承认的原文）："但它报的**另两条是真的**：影子模式没实现、单次调用延迟没观测。**我记账标了『完成』却没做**"」
> 「即：**票 02 的影子模式验收项全仓无实现、票 05 的「置信度—正确率关系数据」因此落空、升级路径不存在、θ=0.70 是占位值。** 这正是"决策模型到底值不值"无法回答的根因 —— **不是没写，是没测。**」

**【事实】** **代码现状与该说法有出入**：`decision/DecisionObserver.java:4-7`（"影子模式的出口"）与 `FailoverDecisionModel.java:74-79`（`if (policy.shadowMode() && observer != null)`）**存在**；`DecisionPolicy.shadowMode()` 字段也**存在**。
**【事实】** 但 grep 全仓，`DecisionObserver` **没有任何实现类**（只有接口定义 + `FailoverDecisionModel` 持有引用），**没有任何地方把它接出去**。
**【推断】** 即：影子模式的**机制建好了，但没有落盘的观察者**，所以"置信度—正确率关系数据"依然产不出来。`θ=0.70` 仍然是占位值（`DecisionPolicy.java:21` 注释自认）。

**【事实】** `.scratch/dsh-computer-use/ARCHITECTURE.md:353` 风险表第 9 条：「本地会话仍在跑票 03，`7dd53f8` 之后仍在改 `perception/` —— **复用前先定基线，否则撞车**」。

### 5.4 隐私事故（真实发生过的失败）

**【事实】** `ARCHITECTURE.md:91-93` 原文：
> 「**UIA 会真实读到聊天正文与未发送草稿。** 本机实测捞到微信草稿里的 `sk-Yrhz4wq…`。
> ⇒ 字段白名单 + 脱敏**必须排在所有通道之前**，否则第一个跑起来的通道就是泄露通道。」

**【事实】** `dsh-computer-use-plan.md:54-58` 补充：还有两个 API key 明文在 `config.yaml` 且已进入会话日志（百炼 `sk-ws-H.PRED…`、opencode `oc_sk_b1120…`）。
**【事实】** `ARCHITECTURE.md:337` 原文：「**阶段 0 不可跳过**：UIA 会读到聊天正文与草稿明文。」
**【推断】** 这是**本项目里最硬的一条失败教训**，且它是一条**架构级前置条件**（脱敏必须排在所有通道之前）。

### 5.5 方向性的自我否定（作者推翻自己的设计）

**【事实】** `dsh-computer-use-plan.md:533-537` 原文：
> 「⭐ **结论：用户提出的方案优于本文原先的「方案 A」**
> 用户设想：Windows→UIA(MCP)、浏览器→CDP/DOM、视觉→兜底与反思，理由是"前两者效率高，视觉最拖垮时间"。
> **这个判断是对的。本文原先的方案 A（三通道同时在跑 → 统一候选表）在速度上确实不如它。** 方案 A 把"多路融合"实现成了**并行融合** —— 用户指出这确实慢，而且慢得没有收益。这个批评成立。」

**【事实】** `ARCHITECTURE.md:21` 决策表第 1 条：「**感知走三通道，但按需降级，不并行融合** —— 并行融合让每步多付 1.5–5s；按需降级让视觉的平均成本 ≈ 0」。

**【事实】** `ARCHITECTURE.md:67-69` 有一段诚实的自我限制声明：
> 「⚠️ **诚实声明**：本项目测出的 `direction` 4/4 **不能作为"Jev 能判断大方向"的证据** —— 那 4 条用例的 state 里直接写了 `uiHashStable` / `coverageFailure` / `progress` 标志位，测的是"会不会读标志"，不是"能不能理解任务语义"。大方向判断**必须**由能看屏的模型承担。」

---

## 6. 本机已验证的事实（可直接复用的"本机事实"清单）

> 这一节是本次调研**最有复用价值**的部分：全部是**在这台机器上实测过**的，不是文档转述。

### 6.1 UIA 能力表（2026-10 实测，4 个应用）

**【事实】** `dsh-computer-use-plan.md:28-33`：
| 应用 | 可交互候选数 | 通路 |
|---|---|---|
| Edge | **126** | UIA 单窗口 |
| 微信 4.1.15 | **26** | UIA，**需系统存在辅助技术进程** |
| 夸克 | **23** | UIA 单窗口 |
| 豆包 | **18** | UIA，**需递归不可见子窗口**（顶层只有 3 个按钮） |

**【事实】** `ARCHITECTURE.md:147-148` 复述并补充：
> 「微信类应用的 UIA 树**被门控** —— 需要启动时存在活着的辅助技术进程（NVDA）。实测：装 NVDA 后主窗口从 2 个元素 → `mmui::MainWindow` 90 后代 / 26 候选。」
> 「豆包的真实候选在**不可见的子窗口**里（顶层只有 3 个按钮，隐藏子窗口里有 18 个中文候选）⇒ **必须递归遍历子窗口**。」

### 6.2 关键 UIA 机制事实

**【事实】** `CapabilityProbe.java:24-27`（把教训写进了代码注释）：
> 「**核心教训：必须递归遍历子窗口。** 实测豆包的主窗口只有 3 个按钮（最小化/最大化/关闭），真正的 162 个元素、18 个中文候选控件在一个**不可见**的子窗口里。只看顶层窗口会把它误判成"不支持 UI 自动化"。」

**【事实】** `AppCapability.java:44-63` 记录了**假阳性陷阱**：
> 「**为什么必须优先于计数**：树没挂出来时并非读不到元素 —— 窗口边框上的"最小化 / 最大化 / 关闭"照样能读到。只按"候选数 ≥ 3"判会得到"可用"的**假阳性**……这种失败不报错，比直接报"不可用"难查得多。」
> 「Chrome 内核的应用（Edge、夸克、豆包）类名恒为 `Chrome_WidgetWin_*`，与是否暴露无关，因此不受这条规则约束。」

**【事实】** `qt-uia-research-report.md:12-14`（Qt 源码级取证）：
> 「**Qt 在 Windows 上没有任何"环境变量/命令行/注册表"开关。** 它是被动应答式：第一个 `WM_GETOBJECT(UiaRootObjectId)` 到达时才惰性建树。」
> 「**因此"UIA 树空"几乎不可能是"没检测到屏幕阅读器所以没建树"** —— 这与 Chromium/Electron 的模型根本不同。」

**【事实】** `qt-uia-research-report.md:137-142`「两个会静默失败的地方（实战踩坑点）」：
> 「**启动期保护**：`if (QCoreApplication::startingUp() || QCoreApplication::closingDown()) return false;` → 如果爬虫在窗口刚 `CreateWindowEx` 出来、Qt 还没进 event loop 时第一次连，**会失败**。客户端必须**重试**。」
> 「**accessibleRoot 闸门**：`if (QAccessibleInterface *accessible = window->accessibleRoot())` —— 返回 nullptr 就直接 `return false`」

**【事实】** `wechat4x-rendering-and-automation-report.md:296-307` 给出更简洁的竞争解释（**【推断】级**）：
```
Qt51514QWindowIcon          ← Qt 宿主/框架窗口（QWindow 级别）
└── MMUIRenderSubWindowHW    ← 微信自研渲染子窗口
└── mmui::MainWindow         ← QWidget 顶层窗口，UIA 控件树挂在这里
```
> 「**Qt 的 QWindow（QPlatformWindow）默认不注册任何 UIA/MSAA Provider —— 无论应用是否启用 accessibility。** 只有 QWidget（通过 QAccessibleWidget）才会生成控件节点。」

**【事实】** `wechat4x-rendering-and-automation-report.md:313-357` 给出**按成本排序的诊断路径**（Step 1 枚举所有顶层窗口找 `mmui::MainWindow` / Step 2 换 `RawViewWalker` / Step 3 检查 UIPI 完整性级别 / Step 4 触发无障碍客户端路径 / Step 5 降级版本验证）。

**【事实】** UIPI 完整性级别的具体探测代码在 `.probe-A.ps1:21-50`（`OpenProcessToken` + `GetTokenInformation(TokenIntegrityLevel=25)`），判定逻辑在 :60-64：「两边级别不一致 -> UIPI 可能单向阻断枚举」。

### 6.3 微信 UIA 门控的四种恢复手段（探测脚本实测）

**【事实】** 四个 PowerShell 脚本对应四种不同手段：

| 脚本 | 手段 | 关键行 |
|---|---|---|
| `.probe-A.ps1` | 零副作用诊断：UIPI 级别 + RawView vs ControlView + 订阅 UIA 事件 + `ElementFromPoint` 反查 | :54-152 |
| `.probe-A2.ps1` | 把窗口激活到前台，用**窗口矩形相对坐标**做 `ElementFromPoint` 命中测试 | :49-68 |
| `.probe-B1.ps1` / `.probe-B2.ps1` | 试 `SystemParametersInfo(SPI_SETSCREENREADER=0x47, true)` —— 试图直接打开系统的"屏幕阅读器已启用"标志 | `.probe-B1.ps1:8-19` |
| `.probe-current.ps1` / `.uia-*.ps1` / `.t1-capability.ps1` | 纯只读能力表：枚举窗口 → 递归子窗口 → 统计"可交互+可见+有名字"候选 | `.t1-capability.ps1:34-35, 99-109` |
| `.prep-at.ps1` | **一键准备环境**：关 NVDA → 启讲述人(Narrator) → 藏讲述人欢迎窗（否则抢焦点）→ 重启微信 → 复核 | :1-50 |
| `.prove.ps1` | **闭环验证**：启 NVDA → 重启微信 → 找"进入微信"按钮 → 点 → 等 25s → 检查主窗口候选 | :50-105 |

**【事实】** `.prep-at.ps1:17-18` 原文：「**藏起讲述人的欢迎窗（否则它会抢焦点，点不中微信）**」——一个纯本机事实。
**【事实】** `.probe-A.ps1:78-88` 原文：「订阅 UIA 事件（让 clientsAreListening() 为真）」——即**订阅 StructureChanged 事件本身可能是让应用挂出无障碍树的触发条件**。
**【事实】** `.probe-A.ps1:133-152`「ElementFromPoint 反查（绕过树，直接问"这个点是什么"）」——截图已知坐标 → 反查 UIA 元素，是绕过树问题的兜底手段。

### 6.4 决策模型端点延迟（实测）

**【事实】** `ARCHITECTURE.md:279-280`：
| 端点 | 服务端 p50 | 客户端 p50 | 备注 |
|---|---|---|---|
| **百炼 `decision-model-preview`** | **64ms**（51–73） | **219ms** → keep-alive **158ms** | 限时免费；10/10 同输入同答案同置信度 |
| opencode `jev-1.13-free` | 不返回该字段 | **942 / 1957 / 4102ms**（三次采样） | p50 波动 4.4×；比 VLM 还慢 |

**【事实】** `dsh-computer-use-plan.md:165-173`：连接复用（`HttpClient` 单例）把 219ms → 158ms，**免费省 30%**；并明确点名「本地会话现在的 `HttpDecisionTransport` 每次调用新建连接，**这一条要改**」。
**【事实】** `ARCHITECTURE.md:191-193`：「备端点只作兜底 —— 它比 VLM 还慢，会让"决策模型提速"的前提失效」。

### 6.5 决策模型准确率（实测，样本量小）

**【事实】** `ARCHITECTURE.md:294-303`：
| 任务 | 形状 | 选项数 | 成绩 |
|---|---|---|---|
| 方向是否仍正确 | keep/pivot | 2 | 4/4（**但有诚实声明，见 §5.5**） |
| 反思：继续/重试/回退/换通道 | choice | 4 | 3/3 |
| 后置条件：达到预期了吗 | noul | 2 | 8/10（t=0.5）／9/10（t=0.4） |
| 元素选择 | pick | ~26 | 21/27 = **78%** |
| 下一步做什么 | choice | 4 | 1/2 |
| 走哪条感知通道 | choice | 4 | **1/4** |

> 「**回溯类 15/17 ≈ 88%　vs　前向类 2/6 ≈ 33%**」

**【事实】** `ARCHITECTURE.md:311-313` 规则基线：「精确名匹配：**5/8 = 62.5%**，0ms、0 成本。」
**【事实】** `ARCHITECTURE.md:307-309` 阈值扫描：noul 最优阈值 **t=0.4（9/10）**，不是 0.5。
**【事实】** `ARCHITECTURE.md:73` 记下一个高置信度错误：「本机实测到一个 **conf=0.95 的高置信度错误**（百炼把「切到通讯录」选成「联系人」）。」
**【事实】** `ARCHITECTURE.md:76`：「实测答对时 P 均值 0.356 < 答错时 0.475（均值倒挂）⇒ **P 的绝对值不能当把握度，但排序有效**。」

**【事实】** `ARCHITECTURE.md:284-290` 打包效果：
| 一次前向带 N 问 | 客户端 p50 | 服务端 p50 |
|---|---|---|
| 1 | 143ms | 67ms |
| 3 | 123ms | 66ms |
| **6** | **134ms** | 80ms |
> 「同样 6 问拆成 6 次串行 = **710ms（贵 5.3×）**。官方文档说的"近线性增长"在 N≤6 未出现。」

**【事实】** `ARCHITECTURE.md:315-316`：复现脚本在 `%TEMP%\jevbench\`：`bench.ps1` / `accuracy2.ps1` / `multiquery.ps1` / `routing.ps1` / `verify.ps1`。
**【推断】** 这批脚本在 `%TEMP%` 下，很可能已被系统清理，**不在仓库里**——如果要用得重写。

### 6.6 坐标漂移（外部一手证据，非本机）

**【事实】** `computer-use-research.md:173-175` 引 Anthropic 官方 `image.py` 原文：
> 「The API's vision encoder tiles images into 28x28 patches and caps both the long edge (1568 px) and the total tile count (1568). If we send an image that violates either, **the server resizes it *again* before the model sees it, and then the model emits click coordinates in a space we never observed, producing systematic click drift (~14% on a 16:10 MacBook screen)**.」

**【事实】** `ARCHITECTURE.md:46` 记录了**公开资料最大的一处错误**：
> 「⚠️ 公开资料最大的一处错误：UI-TARS-1.5 常被说成 0–1000 归一化，但官方 `uitars15_v1.py` 证明它是 **smart_resize 空间的绝对像素**。搞错 = 100% 偏移。」

**【事实】** `dsh-computer-use-plan.md:87-97` 给出 OSWorld 官方代码里的 `coordinate_type` 枚举（5 种空间）：`relative` / `relative1000` / `absolute` / `qwen25`（smart_resize）/ OS-Atlas 无名（缩放到 1000×1000 再输出）。
**【事实】** `dsh-computer-use-plan.md:97`：「Qwen3-VL 又反向了（绝对→相对），**同一家 2.5→3 代就换了契约**。」

### 6.7 Windows 四个必踩坑（外部一手 + 官方 agent 代码）

**【事实】** `dsh-computer-use-plan.md:103-107`：
1. **DPI 虚拟化** —— 任何窗口/截图之前必须 `SetProcessDpiAwarenessContext(PER_MONITOR_AWARE_V2)`，否则 1920px 窗口被当 1280px
2. **多显示器负原点** —— 副屏在左侧时虚拟桌面 X 为负，用 `mss` 或 `ImageGrab(all_screens=True)`
3. **UAC Secure Desktop 不可见不可交互** —— 截图全黑、UIA 注入失效
4. **滚轮单位 Windows 要 ×50** —— OpenCUA 官方 agent 里硬编码了这个修补

**【推断】** 第 1 条**在本项目里没有落地**——`JnaUiaBridge` 直接 `CoCreateInstance` IUIAutomation，没有调 `SetProcessDpiAwarenessContext`；`ScreenshotCapture` 也只用 AWT 默认坐标空间。这是新项目要补的坑。

### 6.8 COM / JNA 直绑 UIA 的坑（代码注释即文档）

**【事实】** `JnaUiaBridge.java:20-50` 给出**完整 vtable 槽位表**（`IUIAutomation` / `IUIAutomationElement` / `IUIAutomationElementArray` 三个结构的 `DECLSPEC_XFGVIRT` 顺序），并注明「COM 接口只做尾部追加，不会重排已有方法，所以这些序号对 Win10/11 都成立」。
**【事实】** `JnaUiaBridge.java:52-66` COM 生命周期：
> 「**为什么每次调用都现开现关。** COM 引用是多引用计数的，Release 少了会泄漏，Release 多了会立刻访问已释放的 vtable 指针——表现是 JVM 在没有 Java 栈帧的地方直接崩，没有任何可捕获的异常。」
> 「**CoUninitialize 必须配对 CoInitializeEx。** S_OK 与 S_FALSE 都算"成功初始化"……唯一的例外是 `RPC_E_CHANGED_MODE`……那会把别人的 apartment 拆掉，后果是整个进程里所有 COM 使用方崩。」

**【事实】** `JnaUiaBridge.java:76-79` CLSID/IID 坑：
> 「这个 GUID 常被误当成 `IUIAutomation` 的接口 ID 直接拿去 `CoCreateInstance` 当 `riid`，结果是 `0x80040154 REGDB_E_CLASSNOTREG`——因为类确实能创建，只是 QI 不上。」

**【事实】** `JnaUiaBridge.java:400-408` GUID 解析坑（两个都踩过）：
> 「`new Guid.GUID(String)` 走 `Integer.decode`，第一段含十六进制字母时（比如 `FF48DBA4`）直接抛数字格式异常。」
> 「`Guid.GUID.fromBinary(byte[])` 收到的是**文本序**字节，自己会做小端转换；再手工交换一次就双重交换，最后得到 `A4DB48FF-...` 这种反的 GUID」

**【事实】** `JnaUiaBridge.java:96-97`：`MAX_NODES = 2000` —— 单次观测最多读 2000 个元素，防超大控件树拖死。
**【事实】** `JnaUiaBridge.java:215-217` 性能优化：「先读名字：绝大多数元素没有名字，读到空就直接放弃，省掉后面 4 次 COM 往返」。

---

## 7. 特别关注项：坐标漂移 / 缩放 DPI / 视觉模型选型 / "CLI anything" 思路

### 7.1 坐标漂移 —— 有，且被显式写成了硬约束

见 §6.6。三处一致：
- **【事实】** `issues/04-coordinate-contract.md:3`「模型永远不碰坐标」
- **【事实】** `ARCHITECTURE.md:43-46`（P1 设计原则）
- **【事实】** `coordinate-contract.md:20` 验收项：「有一个可复现的回归用例，覆盖"点击点落在 x<1000 且 y<1000 的区域"这个曾经会出错的场景」

**【推断】** 新项目若走"模型选 id + 程序执行"的路线，**坐标漂移这一整类 bug 会结构性消失**——这是本项目最有价值的一条可复用结论。

### 7.2 缩放 / DPI —— 识别了但**没有解决**

**【事实】** 现状：
- `vision-config-example.yaml:30` 有 `dpi_scale: 1.25` 手工配置项
- `CoordinateTransform.java:71-74` 注释说"Java AWT 已处理 DPI 坐标空间，不再二次换算"
- `CZD_CODE_PORTFOLIO_HANDOFF.md:413` 承认证据：「**限制：无自动 DPI 校准，依赖用户配置正确值。**」
- `issues/04-coordinate-contract.md:17` 仍是**未勾选**的验收项：「在 125% 与 150% DPI 缩放下，候选坐标与实际点击位置一致」
- `issues/04-coordinate-contract.md:18` 仍是未勾选项：「多显示器或虚拟桌面场景下，UIA 报告的坐标与截图坐标处于同一坐标系（若不成立，须显式报错而非静默偏移）」

**【事实】** `dsh-computer-use-plan.md:104` 给出正确做法：任何窗口/截图之前 `SetProcessDpiAwarenessContext(PER_MONITOR_AWARE_V2)`。
**【推断】** 这个坑在新项目里**必须先做**，否则 UIA 的 `BoundingRectangle` 与截图坐标会差一个缩放系数，而且是静默的。

### 7.3 视觉模型选型 —— 调研充分，但**结论是"别走视觉"**

**【事实】** 候选模型清单（`vision-config-example.yaml:7-10`）：UI-TARS-1.5-7B/72B、Qwen2.5-VL-7B/72B、GPT-4o/4o-mini、Claude 3.5 Sonnet。
**【事实】** 本机可用性结论（`HANDOVER.md:309-314`）：主 LLM `mimo-v2.5` **不支持 image_url**，所以 vision 通道必须另配端点。
**【事实】** `ARCHITECTURE.md:495`（「6GB 显存跑不了本地视觉栈」在 plan.md :494）：UI-TARS-1.5-7B 要 24GB FP16（社区 GGUF 约 12GB）；OpenCUA-7B 要 24GB ⇒ **只能走云 API**。
**【事实】** `dsh-computer-use-plan.md:505`：「2026 年这条路的领跑者是 **Qwen-CUA（📄 OSWorld-Verified 86.2）而非 UI-TARS**（UI-TARS-2 未开源）。」
**【事实】** `dsh-computer-use-plan.md:99`：「**共识三：像素路线没有输。** Qwen-CUA 纯截图在 OSWorld-Verified 拿到 📄 86.2，高于所有混合方案。**混合路线的真正价值在「延迟与 token 成本」，不在成功率上限。**」
**【事实】** `dsh-computer-use-plan.md:101`：「ComponentBench（COLM 2026）实测**无障碍树 83.1% vs 纯像素 48.9%，差 34.2 个百分点**」——但作者自己标注了口径警告。
**【事实】** `issues/09-ocr-and-pixel-fallback-evaluation.md:8-11` 给了「补 OCR」的已知背景与「成本约束」：「收益小于 5 个百分点的补充通道直接不做」。

**【推断】** 用户对视觉路线的真实态度是**"必须做，但放最后、只当兜底、先影子模式测覆盖率再决定要不要接进主链路"**，而不是"视觉是主力"。这与"作品集审计"里"不建议将 Vision 作为唯一主故事"（`CZD_CODE_PORTFOLIO_HANDOFF.md:627`）是一致的。

### 7.4 "CLI anything"（不用视觉、纯代码驱动 GUI）—— **项目里没有这个字面提法**

**【事实】** 我对整个仓库（含 `.scratch/`、`docs/`）做了多轮 grep：`CLI.?anything` / `cli-anything` / `CLI Anything` / `纯代码驱动` / `不靠视觉` / `code-driven` / `无视觉` / `不用视觉` / `非视觉` / `代码驱动` —— **零命中**。

**【事实】** 但存在**三处同构的实质**：

**（a）"非视觉程序化操作路径"——最接近"CLI anything"的章节**
**【事实】** `docs/research/wechat-4x-rendering-research.md:185` 标题原文：
> 「## 4. ★ **非视觉程序化操作路径** —— **这是本次调研最有价值的发现**」

**【事实】** `wechat4x-rendering-and-automation-report.md:152-192` 列举的非 UIA 可编程入口：
- IPC / 命名管道 / 本地 socket ——「**未找到任何证据**」
- 调试端口 / DevTools ——「**不存在**」
- 官方 API 个人微信 ——「**没有**」【证实】
- 企业微信 WeCom ——「**这是唯一官方可编程路径**」【证实】
- 注入 / Hook ——「3.x 生态已死」【证实】

**【事实】** `wechat-4x-rendering-research.md:187` 原文：「### 4.1 MMMojo IPC：**真实存在、已开源、已验证可调用**」；:240「消息/联系人获取：注入 Hook 路线」；:249「「开发者工具」/调试端口 —— 存在但需 hack 才能开启」。

**【推断】** 这就是"CLI anything"思路的本地版：**不截图、不看像素，直接走应用的 IPC / 官方 API / 命令行**。作者的结论是：这条路**只对有官方 API 的场景成立**（WeCom），个人微信 4.x 没有。而 §4.5 同一份报告的"路线选择建议"表（:361-368）把 UIA 排第一、OCR 排第二、WeCom 排第一（若业务可接受）、**明确 ❌ 掉数据库解密 / DLL 注入 / 协议破解**（法律风险"极高"）。

**（b）整个新架构本身就是"非视觉优先"**
**【事实】** `ARCHITECTURE.md:11-12`：「给 DSH 做**原生的 Windows/浏览器操控能力**，第一优先级是**速度**（时效 / 效率），次要目标是覆盖面（含糊地带用视觉兜底）……**走错了能及时止住**。」
**【事实】** `ARCHITECTURE.md:140-142`：UIA ~10ms / DOM-CDP ~10-30ms / 视觉 1.5-5s，视觉「**仅 coverage failure 时**」启用。
**【事实】** `ARCHITECTURE.md:144`：「能力表（票 01 的产物）是**静态的** ⇒ "该不该用 UIA"**每次应用启动判一次即可，不是每步**，摊销成本 ≈ 0。」
**【事实】** `ARCHITECTURE.md:57`：「推论：视觉通道**平均成本 ≈ 0**（本机 4 应用实测 UIA 给出 18–126 个候选，coverage 失败罕见）。」

**（c）"浏览器走 DOM/CDP 而不是视觉"——即网页端的"CLI anything"**
**【事实】** `ARCHITECTURE.md:141`：「**DOM/CDP** | 浏览器（Edge / Chromium 系） | ~10–30ms | 窗口类命中 `Chrome_WidgetWin_*` 且是浏览器进程」。

**【推断】** 综合三条：用户在 czdcode 里表达的"CLI anything"思路，**已经完整地、且是经过实测的**形式存在于新架构里 —— 即 **"能用结构化接口（UIA / DOM / CLI / IPC）就别用像素"**。新项目不需要重新发明这个想法，只需要把它落到具体接口上。

**【事实】** 唯一显式写着"要不要引入 CLI / 非视觉"取舍的文档是 `issues/09-ocr-and-pixel-fallback-evaluation.md`，其结论口径是「**决策前置，不允许先写实现**」「预估提升 < 5 个百分点的方案一律标记为不做」。

### 7.5 三段式耗时归因（值得单独拎出来）

**【事实】** `issues/03-end-to-end-click.md:22` 验收项：「单步耗时可观测：分别统计"取候选""决策""执行"三段耗时」。
**【事实】** `ARCHITECTURE.md:269` 原文：「**三段耗时必须分别统计** —— 否则提效幅度无法归因，也就无法证明 Jev 到底有没有赚回那 134ms。」
**【事实】** 代码里已有：`gui/StepTiming.java`（observeMs / decideMs / executeMs）、`gui/StepReport.java`；`GuiAgent.java:69-131` 严格按 观测→决策→执行 三段打点。

---

## 8. 我的推断（不属于文档原文）

**【推断】1｜"失败"的根因是"没有可对比的数字"，不是"技术没做出来"。**
证据：`CZD_CODE_PORTFOLIO_HANDOFF.md:651-652` 把「Benchmark results」「Latency numbers」列为"无真实数据"；而 16,300→27,919 行的增长里真正有实测数据支撑的只有 Vision 那条线（`HANDOVER.md:501-506` 有真实时延数字：CHAT 2.124s / 视觉步骤 8.842s / 端到端 14.775s）。**新项目第一优先级应该是"每一步都能报出可对比的数"。**

**【推断】2｜用户真正想做的不是"视觉 GUI 智能体"，是"快的 GUI 智能体"。**
证据链：作品集审计把 Vision 降级为"三个卖点之一"（`:499`）；新架构第一条决策就是"按需降级不并行融合"（`ARCHITECTURE.md:21`）；BUILD-PROMPT 第一节就写「**首要目标：速度**。单步操控的加权延迟必须控制在 ~50ms 量级。覆盖面（含糊地带靠视觉兜底）是次要目标，但必须有。」（`BUILD-PROMPT.md:7`）。**这与"用视觉模型驱动鼠标"的直觉是相反的。**

**【推断】3｜Java 那套 `perception/decision` 代码的可复用性主要是"知识"而不是"代码"。**
证据：`ARCHITECTURE.md:360-362` 明确把"复用还是重写"列为**待决问题**，倾向 Node 插件重写；`:353` 提醒"复用前先定基线，否则撞车"；`dsh-computer-use-plan.md:595` 说"本地会话的 `com.czdcode.perception` / `com.czdcode.decision` 有未修复的 COM 缺陷，且那条会话仍在跑、可能会继续改"。**但 vtable 槽位表、COM 生命周期规则、RECT 是 4 个 int 这些知识点，是纯赚的。**

**【推断】4｜"模型永不碰坐标"这条是整个项目最可复用的单一结论。**
它同时消灭了坐标漂移（Anthropic 14% 系统性偏移）、DPI 换算、多显示器负原点、5 种 coordinate space 兼容这四个大坑，且**不需要任何模型侧的配合**。新项目应当直接以此为地基，而不是先做 G1 坐标投影那套（G1 是给"模型必须吐坐标"的路准备的）。

**【推断】5｜被搁置的"决策模型值不值"是本项目最大的未结账。**
代码里 `DecisionObserver` 接口在、没有实现；`θ=0.70` 是自认的占位值；`issues/05` 和 `issues/06` 两张票都没关。`dsh-computer-use-plan.md:46` 自己下了判语：「**不是没写，是没测。**」**新项目如果想复用决策模型，第一件事是把它当成"要不要用"的前置实验，而不是当成既定组件。**

**【推断】6｜新项目最该抄的不是代码，是那三份 `.scratch` 文档的写法本身。**
每条设计决策后面都跟着一行「依据」（实测表格 / 一手源码链接 / 本机观测），并且显式标注"未验证"、"不可推广"、"样本量不足"（`ARCHITECTURE.md:348-349`：「准确率样本量小（n=27 / n=10 / n=8），且**候选集是合成构造而非真机 UIA 读取**」）。**这种"结论 + 证据等级 + 适用边界"三件套的写法，比代码值钱。**

---

## 9. 给新项目的可复用结论（Top 10）

| # | 结论 | 来源 | 类型 |
|---|---|---|---|
| 1 | **模型永不接触坐标空间**；候选自带物理像素 bbox，模型只答"选哪个 id" | `issues/04:3`、`ARCHITECTURE.md:43-46` | 设计原则（可复用） |
| 2 | **坐标空间必须显式声明，不能用阈值猜**（"数值>1000 即绝对坐标"在 1344×768 下会误判） | `CoordinateSpace.java:9-11` | 踩过的坑 |
| 3 | **UIA 必须递归遍历子窗口**（豆包 18 个候选藏在不可见子窗口，顶层只有 3 个按钮） | `CapabilityProbe.java:24-27` | 本机事实 |
| 4 | **判定"UIA 可用"优先看 UIA ClassName 而非候选数**（`Qt51514QWindowIcon` = 裸 Qt 外壳，只有 3 个边框按钮 → 假阳性） | `AppCapability.java:44-63` | 本机事实 |
| 5 | **微信类应用 UIA 树被门控，需活着的辅助技术进程**（NVDA/讲述人）；装上后 2 元素 → 90 后代 / 26 候选 | `ARCHITECTURE.md:147` | 本机事实 |
| 6 | **UIA 的 `BoundingRectangle` 是 4 个 int，不是 4 个 double**；按 double 读会得到 1e-311 → 全 0 | `JnaUiaBridge.java:256-266` | 本机事实 |
| 7 | **规则精确名匹配命中 62.5%，0ms 0 成本** —— 比换端点重要一个数量级 | `ARCHITECTURE.md:311-313` | 本机实测 |
| 8 | **多问题打包进一次决策模型前向几乎免费**（6 问=134ms ≈ 1 问=143ms；拆 6 次=710ms，贵 5.3×） | `ARCHITECTURE.md:284-290` | 本机实测 |
| 9 | **决策模型擅长回溯（88%）、不擅长前向路由（33%）**；路由类问题 8 条里 5 条是 0ms 的确定性查询 | `ARCHITECTURE.md:294-303`、`plan.md:263-274` | 本机实测 |
| 10 | **UIA 原文含聊天正文与草稿密钥** → 字段白名单 + 脱敏必须排在所有通道之前 | `ARCHITECTURE.md:91-93` | 真实事故 |

**补充（Windows 通用坑，新项目必须先做）**：`SetProcessDpiAwarenessContext(PER_MONITOR_AWARE_V2)` 在任何窗口/截图之前；多显示器负原点用 `mss`；UAC Secure Desktop 是硬墙；滚轮单位 ×50（`dsh-computer-use-plan.md:103-107`）。

---

## 10. 文档清单（本次实际读过的）

**根目录**：`HANDOVER.md`（506 行）、`CZD_CODE_PORTFOLIO_HANDOFF.md`（694 行）、`README.md`、`AGENTS.md`、`CLAUDE.md`、`vision-config-example.yaml`、`config.yaml.example`、`pom.xml`
**探测脚本**（根目录全部 16 个 PowerShell，**全部读完**）：`.probe-A.ps1`、`.probe-A2.ps1`、`.probe-B1.ps1`、`.probe-B2.ps1`、`.probe-current.ps1`、`.probe-final.ps1`、`.probe-login.ps1`、`.probe-login2.ps1`、`.t1-capability.ps1`、`.uia-allwin.ps1`、`.uia-childwin.ps1`、`.uia-probe.ps1`、`.uia-probe2.ps1`、`.prove.ps1`、`.isolated-test.ps1`、`.prep-at.ps1`
**`.scratch/`**：
- `windows-gui-agent/issues/01`–`09`（9 张票，全读）
- `dsh-computer-use/ARCHITECTURE.md`（380 行，全读）、`BUILD-PROMPT.md`（读前 180 行）、`dsh-computer-use-plan.md`（613 行，全读）
- `computer-use-research/computer-use-research.md`（读前 250 行）
**`docs/research/`**（读完章节标题 + 关键节）：`OCR-vs-VLM-GUI-grounding-2026.md`（§0/§6/§7）、`qt-uia-research-report.md`（§0/§3）、`UI-TARS-PERCEPTION-REPORT.md`（章节结构）、`wechat-4x-rendering-research.md`（章节结构）、`wechat4x-rendering-and-automation-report.md`（§5.3–§6.3）
**源码（关键文件）**：`vision/CoordinateTransform.java`、`vision/CoordinateSpace.java`、`vision/ScreenshotCapture.java`、`vision/VisionActionExecutor.java`、`perception/CapabilityProbe.java`、`perception/AppCapability.java`、`perception/Candidate.java`、`perception/uia/JnaUiaBridge.java`、`perception/uia/PointerClicker.java`、`decision/DecisionPolicy.java`、`gui/GuiAgent.java`
**git**：`git log --oneline`（3 条提交）、`git status --porcelain`

### 10.1 只读性自证

**【事实】** 本次调研对 `C:\Users\22197\Desktop\czdcode` **未做任何写操作**（全部工具调用为 `read` / `glob` / `grep` / 只读 `pwsh`）。
**【事实】** `git status` 显示工作区有 40 个文件处于 modified 状态，但**最后写入时间全部为 2026-10-04 11:12–12:02**（本次调研开始于 2026-10-19 23:25，相差 15 天）。这些是**上一条被中断的会话留下的未提交改动**，集中在 `perception/`、`vision/`、`team/` 与 `.scratch/windows-gui-agent/issues/04-coordinate-contract.md` —— 正好对应 `ARCHITECTURE.md:353` 记录的「本地会话仍在跑票 03，`7dd53f8` 之后仍在改 `perception/`」。
**【推断】** 这批未提交改动**是 czdcode 里最新的、比 `7dd53f8` 提交更新的实现**（例如 `CoordinateSpace.java` 的显式枚举改造、`AppCapability` 的裸 Qt 外壳判定，都在这批改动里）。复用时**必须以工作区当前状态为准，而不是以 `7dd53f8` 提交为准**。


**未读**：`docs/agents/*.md`（3 个小文件）、`web-deck/`、`index.html`/`main.js`/`style.css`（Web Deck 前端）、`src/main/java/com/czdcode/{agent,provider,tool,permission,memory,prompt,tui,team,hook,mcp,skill,worktree,command,usage,concurrent,storage}/`（与 GUI 智能体无直接关系，未逐文件读）、`.czdcode/` 运行时产物（会话 JSONL、截图、压缩结果）、`.git/` 历史细节。

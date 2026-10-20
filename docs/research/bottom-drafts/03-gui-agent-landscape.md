# GUI 智能体（Computer-Use Agent）格局调研 · 2026-10

> 调研员：#3（landscape-scout）　·　成稿日期：**2026-10-19**
> 调研方法：`web_search` + `web_fetch` + **直接下载一手数据文件**（GitHub README、HuggingFace API、OSWorld 官方榜单 xlsx、官方文档站）
> 特别说明：本轮环境**无法直连 huggingface.co**（DNS 解析到非公网地址），改用官方镜像 `hf-mirror.com` 的 HF API（同一份 HF 索引数据）取模型清单；所有关键数据均以**官方仓库 / 官方榜单 / 官方文档**为准，二手博客仅作线索。

**来源等级约定**：
`【仓库】`= 官方 GitHub/HF 仓库原文　`【榜单】`= 基准官方维护方数据　`【论文】`= arXiv/会议论文　`【官方文档】`= 厂商官方文档　`【媒体】`= 新闻/博客（需交叉验证）

---

## 0. 一页速览（TL;DR）

| 结论 | 一句话 |
|---|---|
| **"Tars 是字节开源" → 错误** | 腾讯 TARS = 微服务 RPC 框架（BSD-3，Linux Foundation），**与 AI 毫无关系**；字节的是 **UI-TARS / Agent TARS**（Apache-2.0），二者是纯名称撞车 |
| **UI-TARS 到底开源了什么** | 只开源了 **v1 家族（2B/7B/72B）+ 1.5-7B**。**UI-TARS-2 只有技术报告，权重至今（2026-10-19）未开源**；不存在"UI-TARS-1.5-2B"这个模型 |
| **2026 开源 SOTA 是谁** | **EvoCUA**（美团，Apache-2.0，8B/32B）OSWorld-Verified 56.7%；**Qwen-CUA**（Qwen×XLang，Apache-2.0，397B-A17B）自报 86.2 |
| **2026 最重要的范式转移** | **"CLI/代码执行优先"**。阿里 **OpenGCUA**（Apache-2.0，2026-10）证明：同模型、仅换 harness，OSWorld-v2 部分分 45.3 → 64.9，步数 −53% |
| **UIA 树的真实地位** | 官方 OSWorld-Verified 榜 111 条记录里，**只有 2 条用了 a11y tree**（最高 47.26%）。UIA 在 Windows 上是**工程护栏**，不是刷榜手段 |
| **Windows 落地推荐** | **纯 API 强模型 + 自建 UIA 优先执行层**（6GB 显存跑不动有竞争力的 GUI 模型） |

---

## A. 事实核查：Tars 与 UI-TARS 的归属

### A.1 腾讯 TARS 是什么

**结论：腾讯 TARS 是 2008 年起在腾讯内部使用的微服务 RPC 框架，2017-04 开源，2018 捐赠给 Linux 基金会。协议 BSD-3-Clause。跟大模型、GUI 智能体、数字人没有任何关系。**

官方仓库 README 原文【仓库】：

> "TARS is a Linux Foundation project. It is a high-performance RPC framework based on name service and Tars protocol, also integrated administration platform... Tars, aka TAF (Total Application Framework), has been used in Tencent since 2008. It supports C++, Java, Nodejs and PHP for now."
> "The open-source protocol Tars used is BSD-3-Clause."

- 仓库：<https://github.com/TarsCloud/Tars>（README 已本地核对全文，2026-10-19 拉取）
- 文档：<https://tarscloud.github.io/TarsDocs_en/>
- 名字来源：《星际穿越》里的机器人 Tars（这点常被误读成"AI 项目"）
- **子模块 `web`** 即运维管理控制台 → 独立仓库 [TarsCloud/TarsWeb](https://github.com/TarsCloud/TarsWeb)（Node.js + Koa，默认端口 3000）——**用户口中的"TarsUI"最可能是它**，但它是服务端管理台，不是 GUI 智能体前端
- 生态：TarsFramework(C++)、TarsJava/TarsGo/TarsCpp/TarsNodejs/TarsPHP、DCache、TSeer、TarsK8SFramework

**逐一核对用户列举的 "Tars" 子项**：

| 用户说法 | 核查结果 |
|---|---|
| 腾讯 Tars | ✅ 存在。RPC 微服务框架，BSD-3，Linux Foundation。**无任何 AI/模型内容** |
| tarsframework | ✅ 存在。Tars 2.1.0 起拆分的 C++ 核心框架仓库（[TarsCloud/TarsFramework](https://github.com/TarsCloud/TarsFramework)） |
| TarsUI | ⚠️ **没有叫这个的产品**。最接近的是 TarsWeb（运维控制台）。另有 [TarsCloud/TarsDocs](https://github.com/TarsCloud/TarsDocs) 等文档仓库 |
| TarsGPT | ❌ **不存在腾讯官方项目**。GitHub 上的 `metaforismo/TarsGPT` 是个人做的《星际穿越》TARS 机器人（树莓派 + 语音 + LLM），与腾讯无关。腾讯 AI 侧是**混元（Hunyuan）**体系，见下 |
| "Tars 数字人" | ❌ **不存在这个组合**。腾讯的"数字人"开源是另外两条线：<br>· [Tencent/DigitalHuman](https://github.com/Tencent/DigitalHuman)（混元数字人**算法研究仓库**，MIT：RLVER、HATE、RolePlay_Villain、CompanionBench 等）<br>· [Tencent-Hunyuan/HunyuanVideo-Avatar](https://github.com/Tencent-Hunyuan/HunyuanVideo-Avatar)（2025-05-28 开源，语音驱动 720p 数字人视频，含权重+LoRA 示例，FP8 量化 8G 显存可跑）<br>另：腾讯云"智能数智人"是**商业产品**，不以此名开源 |

### A.2 字节的对应物：UI-TARS / Agent TARS

**结论：字节的"对应物"确实叫 UI-TARS，且它**就是** GUI 智能体（不是数字人）。名字里的 "TARS" 与腾讯 TARS 无关（UI-TARS 的 TARS ≈ "原生智能体 Transformer"），纯属撞名。**

**A.2.1 UI-TARS 官方 README 原文（2026-10-19 拉取，base64 解码后逐字核对）【仓库】**

来源：<https://api.github.com/repos/bytedance/UI-TARS/readme> → `https://github.com/bytedance/UI-TARS`

| 日期 | 公告 | 是否给权重 |
|---|---|---|
| 2025-01-23 | UI-TARS 首发（arXiv 2501.12326） | ✅ v1: 2B / 7B / 72B |
| 2025-03-23 | 更新 OSWorld 官方推理脚本（`xlang-ai/OSWorld`） | — |
| 2025-04-16 | 发布 UI-TARS-1.5 技术博客，**开源 UI-TARS-1.5-7B**（HF `ByteDance-Seed/UI-TARS-1.5-7B`） | ✅ **仅 7B** |
| 2025-09-04 | **UI-TARS-2 发布**：全部能力集成（GUI/游戏/代码/工具） | ❌ **只给技术报告**（arXiv 2509.02544） |

README 关键原文：

> "🌟 2025.09.04: We are excited to announce the release of **UI-TARS-2**, which is a major upgrade from UI-TARS-1.5, featuring with enhanced capabilities in GUI, Game, Code and Tool Use. Please check our [new technical report](https://arxiv.org/abs/2509.02544) for more details."
> "🌟 2025.04.16: We shared the latest progress of the UI-TARS-1.5 model in our [blog](...). We also **open-sourced the [UI-TARS-1.5-7B](https://huggingface.co/ByteDance-Seed/UI-TARS-1.5-7B)**."

> "**Important: 1.5 requires a significant advance in GUI task capabilities**, please contact TARSByteDance.com for further collaborations or adaptions."
> —— 即 **UI-TARS-1.5 全尺寸（非 7B）走"early research access"申请制，不公开下载**。

README 的 "Model Scale Comparison" 表只列了三项：`UI-TARS-72B-DPO`、`UI-TARS-1.5-7B`、`UI-TARS-1.5`（第三项即上面的 gated full-size）。**表里没有 UI-TARS-2。**

**A.2.2 HF 官方 org 清单实测（2026-10-19 通过 hf-mirror 拉取 `api/models?author=ByteDance-Seed`）【仓库】**

`ByteDance-Seed` org 共 59 个模型，**全部 GUI 相关条目**：

| Model ID | 归属 | 许可 | 备注 |
|---|---|---|---|
| `ByteDance-Seed/UI-TARS-2B-SFT` | **v1**（2025-01） | Apache-2.0 | Qwen2-VL 架构，2B，32K 上下文 |
| `ByteDance-Seed/UI-TARS-7B-SFT` | v1 | Apache-2.0 | |
| `ByteDance-Seed/UI-TARS-7B-DPO` | v1 | Apache-2.0 | |
| `ByteDance-Seed/UI-TARS-72B-SFT` | v1 | Apache-2.0 | |
| `ByteDance-Seed/UI-TARS-72B-DPO` | v1 | Apache-2.0 | |
| `ByteDance-Seed/UI-TARS-1.5-7B` | 1.5 | Apache-2.0 | **1.5 家族唯一的公开 checkpoint** |

**清单里没有的（= 不存在公开权重）**：
- ❌ **UI-TARS-2**（任何尺寸）
- ❌ **UI-TARS-1.5-2B**（用户假设中的这个模型**并不存在**；2B 是 v1 的 `UI-TARS-2B-SFT`，无 1.5 的强化学习"think-then-act"能力）
- ❌ Seed1.5-VL / Seed 1.6 / Doubao 视觉模型权重（org 里有 SeedVR/Seed-Coder/Seed-OSS 等，但**没有 Seed-VL 系列权重**）

> ⚠️ 网上大量 SEO 站（TokenMix、ossaihub、toolify 等）宣称"UI-TARS-2 已开源、HF 可下载"，与官方 repo/org 清单**直接冲突**，属内容农场，**不可采信**。社区也早已在 [bytedance/UI-TARS issue #213](https://github.com/bytedance/UI-TARS/issues/213) 公开请求官方上传 UI-TARS-1.5 全尺寸与 UI-TARS-2 权重。

**A.2.3 许可与商用性**

| 项目 | 许可 | 能否商用 |
|---|---|---|
| `bytedance/UI-TARS`（模型+推理代码） | Apache-2.0 | ✅ 可商用（README "Model Scale Comparison" 处另有"只发引用"字样，与 Apache-2.0 条款叠加时需自行读 LICENSE，但仓库 LICENSE 文件为 Apache-2.0） |
| `bytedance/UI-TARS-desktop`（Agent TARS + UI-TARS Desktop，Electron/TS） | **Apache-2.0**（README 明确） | ✅ 可商用 |
| ByteDance-Seed/UI-TARS-* 权重 | Apache-2.0 | ✅ 可商用 |

**A.2.4 字节的 CUA / Doubao / Seed 是否开放权重 —— 没有**

| 东西 | 开放权重？ | 获取方式 |
|---|---|---|
| **Doubao-1.5-UI-TARS / UI-TARS-2** | ❌ 闭源 | 仅火山引擎 Ark API（`doubao-1-5-thinking-vision-pro-250428` 等） |
| **Seed1.5-VL** | ❌ **只开源了 cookbook** | [ByteDance-Seed/Seed1.5-VL](https://github.com/ByteDance-Seed/Seed1.5-VL) 仓库 Apache-2.0，但里面**只有 API 示例代码和技术报告 PDF，没有权重**；官方渠道 = 火山引擎 `doubao-1-5-thinking-vision-pro-250428` |
| **Seed 1.6 / Seed 1.8** | ❌ 闭源 | 仅 API。Seed-1.8 在 OSWorld-Verified 官方榜上 61.87%（2025-12-18） |
| **Seed-OSS-36B** | ✅ 但**不是 GUI agent** | 纯文本开源模型，与 computer-use 无关 |

> 一句话：**字节开源了 UI-TARS 的 v1 全家族 + 1.5-7B + 桌面端代码，但把最前沿的 UI-TARS-2、Seed-VL、Doubao-UI 全锁在 API 后面。**

### A.3 归属核实小结（可直接回复用户）

> 「Tars 是字节的开源项目」这个说法**把两个同名不同物的东西混了**。
> **腾讯 TARS**：微服务 RPC 框架，2008 年起在腾讯内部用，2017-04 开源，BSD-3-Clause，Linux Foundation 项目。跟 AI 一点关系没有。"TarsUI" 实际是它的 Web 运维控制台 TarsWeb；"TarsGPT" 不存在（GitHub 上同名项目是个人做的《星际穿越》机器人）；"Tars 数字人" 也不存在——腾讯的数字人开源是混元系的 `Tencent/DigitalHuman` 和 `HunyuanVideo-Avatar`。
> **字节的 UI-TARS** 才是 GUI 智能体，Apache-2.0，**但开源的只是 v1 全家族（2B/7B/72B）+ UI-TARS-1.5-7B**。旗舰 **UI-TARS-2 从未放权重**，只有技术报告（arXiv 2509.02544）；也**没有"UI-TARS-1.5-2B"这个模型**（2B 是 v1 的 UI-TARS-2B-SFT）。Seed1.5-VL / Doubao-UI-TARS 同样只有 API，仓库里只有 cookbook。

---

## B. 2025–2026 GUI 智能体完整格局

### B.1 微软系

| 项目 | 开源状态 | 许可 | 规模 | 动作空间 | Windows | 分数 |
|---|---|---|---|---|---|---|
| **Magentic-UI**（arXiv 2507.22358, 2025-07-30） | ✅ 全开源 | **MIT** | 框架（挂任意 LLM/VLM，多 agent + 人机协同 UI） | 浏览器 DOM/Playwright + 截图 | 浏览器为主，**不产 Windows 桌面控制** | GAIA 42.52 / AssistantBench 27.6 / WebVoyager 82.2 / WebGames 45.5。**从未在 OSWorld / WAA 上评测** |
| **UFO / UFO²**（arXiv 2504.14603, 2025-04） | ✅ 全开源 | **MIT** | 框架；HostAgent + AppAgent 多层 | **UIA + Win32 双后端**（可配 `uia`/`win32`）+ 原生 API + MCP；Gui-Agent-Use 模式带动作空间 | ✅ **纯 Windows 出身** | WAA：UFO²+GPT-4o 27.9%，UFO²+o1 **30.5%**；OSWorld-W：28.6% / 32.7%；coding 任务 58.3% |
| **OmniParser v2** | ✅ | MIT/Apache | YOLOv8 检测器 (~11.6MB ONNX) + BLIP-2 captioner + OCR | 产出 SoM 编号框 | 可 | ScreenSpot-Pro **39.6%**（微软自报）；~0.6s/帧 A100、~0.8s/帧 RTX4090 |

- Magentic-UI 官方仓库：<https://github.com/microsoft/magentic-ui>；论文 <https://arxiv.org/abs/2507.22358>
  - **重要限制（论文自述）**：系统在"需要高级编码能力、多模态理解、超长 web 动作序列、**通用 computer use**"上表现挣扎 → 这就是它没有 OSWorld 分数的原因，不是遗漏。
- UFO² 官方仓库：<https://github.com/microsoft/UFO>；文档 <https://microsoft.github.io/UFO/>；论文 <https://arxiv.org/abs/2504.14603>
  - 架构亮点：**Gui-Agent-Use 动作空间**（把 app 原生 API 抽象 + GUI 兜底统一成动作）、**推测式多动作规划**（LLM 调用数 −51%）、**PiP 隔离桌面**（RDP 虚拟桌面，用户可继续用电脑）、MCP 分发。

### B.2 学术/开源基座模型（2025–2026）

| 模型 | 组织 | 权重开源 | 许可 | 规模 / 底座 | 动作空间 | Windows | 官方分数 |
|---|---|---|---|---|---|---|---|
| **UI-TARS-1.5-7B** | ByteDance | ✅ | Apache-2.0 | 7B | 纯视觉 → 坐标/文本 | ✅ | ScreenSpot-v2 94.2；OSWorld **42.5**(@100步, 论文自报)；OSWorld-Verified 官方榜未见其条目 |
| **UI-TARS-2** | ByteDance | ❌ | — | ~72B（推测） | 同上 + 代码/游戏/工具 | ✅ | 论文自报 Online-Mind2Web 88.2 / OSWorld 47.5 / **WAA 50.6** / AndroidWorld 73.3。**官方榜实际记录 53.1%（@100步, 2025-10-14）** |
| **OpenCUA 7B/32B/72B** | XLang Lab | ✅ | **MIT**（明确可商用） | Qwen2.5-VL 底座 | 纯视觉 + 反思式 CoT | ✅ | OSWorld-Verified **45.0%**（72B，前开源 SOTA）；UI-Vision 37.3%；ScreenSpot-Pro 60.8% |
| **Aguvis-7B-720P** | XLang Lab | ⚠️ 仅 7B-720P 一个 checkpoint，**无 license tag** | 未标注 | Qwen2-VL 底座 | 统一纯视觉动作空间 | ✅ | README 未给分数（ Checklist 里"Model Weights/Inference Scripts/Eval Toolkit"仍标 🚧） |
| **EvoCUA-8B / 32B** | 美团 LongCat | ✅ | **Apache-2.0** | Qwen3-VL 底座 | 纯视觉（`coordinate_type=relative`） | ✅ **WAA 56.48%** | **OSWorld-Verified 56.73%**（32B@50步）/ 46.06%（8B@50步）；**开源模型第一** |
| **MAI-UI-2B / 8B**（+32B/235B-A22B） | 阿里通义 MAI | ✅（2B/8B 已公开） | **Apache-2.0** | Qwen3-VL 底座 | 纯视觉 grounding + 在线 RL | ✅ | ScreenSpot-Pro 73.5；MMBench-GUI L2 91.3；OSWorld-G 70.9；**UI-Vision 49.2**；**AndroidWorld 76.7**；MobileWorld 41.7 |
| **Qwen-CUA** | Qwen Team × XLang Lab | ✅ | **Apache-2.0** | **397B 总 / 17B 激活** MoE，BF16 ≈ 807GB | **纯截图**（无 DOM/a11y/shell），0–999 归一化坐标 | 官方 demo 仅 Chromium | 自报 OSWorld-Verified **86.2**；OSWorld 2.0 binary 18.5 / partial 48.4；RedTeamCUA ASR 16.4（GPT-5.5 15.6，Claude Opus 4.8 **0.7**） |
| **GUI-Owl-1.5-32B** | 阿里通义 | ✅ | Apache-2.0 | Qwen3-VL 底座 | 纯视觉 | ✅ | OSWorld-Verified **55.44%**（@50步） |
| **ShowUI-2B** | NUS Show Lab | ✅ | 见 HF repo | Qwen2-VL 底座 | 纯视觉（UI-guided token selection） | ✅（`computer_use_ootb`） | CVPR 2025 优秀论文；**ShowUI-π（拖拽）CVPR 2026**；ShowUI-Aloha 2025-12-31 |
| **Midscene `@midscene/computer`** | web-infra-dev | ✅ | **MIT** | 框架（挂任意 VLM；模型支持 Doubao Seed / Qwen / GLM / Gemini / GPT-5） | **语义化 API**（`aiQuery`/`aiAction`，`notepad.button("Save").click()` 风格）+ 视觉；多显示器、拖拽、滚轮、组合键 | ✅ **Windows / macOS / Linux** | 无官方 OSWorld 分数；AndroidWorld 93.1%（**改过环境与验证器**）；2026 新增：Playground、RDP 远程 Windows 控制、MCP server、可挂到 Claude Code / Cline 的 Agent Skills |
| **OS-Atlas-Base/Pro 4B/7B** | OS-Copilot | ✅ | 见 HF repo | Qwen2.5-VL 底座 | 纯视觉 grounding | ✅ | ScreenSpot-Pro 基线 18.9%（7B） |
| **SeeClick** | 南大 | ✅ | — | Qwen-VL 底座 | 纯视觉 grounding | ✅ | ShowUI 的上游基座 |
| **Ferret-UI** | inclusionAI | ⚠️ HF `inclusionAI` org 实测**未搜到 Ferret-UI 权重条目** | — | — | — | — | 需自行核验，勿直接引用 |
| **DeepMiner-Mano-7B/72B** | Mininglamp | ✅ | — | — | 纯视觉 | ✅ | OSWorld-Verified 53.91%(72B) / 40.15%(7B) |
| **AutoGLM-Phone-9B** | 智谱 | ✅ | Apache-2.0 | 9B | 手机端 Tap/Swipe/Type/Launch/Back | ❌ 仅移动端 | PhoneCLI SR 47.7；VAB-WebArena-Lite 55.2% |
| **ScaleCUA**（GLM-4.6V-Flash 底座） | 智谱 | ✅（`extreme1228/ScaleCUA-glm4.6v-osworld`） | — | GLM-4.6V-Flash 9B | 纯视觉 | ✅ | 自报 **OSWorld 66.5%** |
| **ShowUI-pi / ShowUI-Aloha** | NUS | ✅ | — | — | 拖拽 / 人演示 | ✅ | 2026 新动作空间扩展 |

### B.3 闭源 API（2026-10 现状）

| 厂商 | 形态 | 状态 | 动作空间 | Windows | 分数 |
|---|---|---|---|---|---|
| **Anthropic Claude** | `computer_toolset_20260801` | **2026-08-19 GA**（Claude API + Google Cloud；Bedrock/Foundry 仍 beta）。claude.ai computer use 2026-03 已 GA | **17 个成员工具**（screenshot / click 家族 / drag / scroll / type / key / hold_key / wait / **zoom**）；每个动作独立 `tool_use` 块；支持**批量动作**；坐标=截图像素空间 | ✅ | 官方榜：`claude-fable-5` 85.96%、`claude-opus-5` 83.39%、`claude-sonnet-4-6` 72.11%。Qwen-CUA 表自报 Claude Opus 4.8 OSWorld-Verified 83.4 / OSWorld2.0 binary 20.3 |
| **OpenAI** | Responses API 内置 **Computer Use** 工具 + Code Interpreter | GA（GPT-6 家族 2026-09） | 截图 + 归一化坐标 | ✅ | 官方榜旧条目 computer-use-preview 31.4%（@100步, 2025-07-28）；GPT-5.4 75% OSWorld-Verified |
| **Google Gemini** | **Gemini API computer use 工具**（推荐 `gemini-3.8-flash`；legacy `gemini-2.5-computer-use-preview-10-2025`） | **仍为 Preview** | 0–999 归一化坐标 + `require_confirmation` 安全分类 + prompt-injection 检测 | ✅ | 官方榜 `Muse Spark 1.1`(MSL) 80.67%、`OpenAPA w/ gemini-3.1-pro` 78.34% |
| **Google Project Mariner** | 独立产品 | ❌ **已于 2026-05-04 关停**（多来源一致；无官方公告）。能力分散到 Gemini Agent（2025-11-18）、Chrome auto browse（2026-01-28）、Gemini API computer use 工具 | — | — | — |
| **国内 API** | 火山引擎 Ark（`doubao-1-5-thinking-vision-pro-250428`、`Seed-1.8`）、阿里百炼（`qwen3-vl-plus` / `qwen3-vl-flash` / `qwen3.8-max` / `qwen3.8-flash`）、智谱 BigModel、Moonshot（Kimi K2.5/K2.6/K3）、MiniMax M3 | 全部闭源 API | 多数 0–999 归一化坐标 | ✅ | 官方榜：Seed-1.8 61.87%、Qwen 3.7 Plus 73.3%、Kimi K2.6 73.06%、Kimi K2.5 63.3%、MiniMax M3 75.19% |

### B.4 2026 年新出品的 GUI/通用 computer-use 栈（务必关注）

| 项目 | 时间 | 组织 | 许可 | 核心主张 |
|---|---|---|---|---|
| **OpenGCUA** | **2026-10**（arXiv 2610.22200） | 阿里通义 MAI | **Apache-2.0** | 正式提出 **GCUA（Generalist Computer-Use Agent）** 概念：模型自主决定"看什么/怎么动/怎么组织工作"，三决策从 harness 下放到模型。开源 OSWorld-v2 集成示例、macOS 真机 harness、跨设备 SDK、8 个前沿模型的执行轨迹 |
| **EvoCUA** | 2026-01-05/13/22（arXiv 2601.15876） | 美团 | Apache-2.0 | 开源 OSWorld 第一（56.7%）；合成数据 + SFT/RL；跨 OS 零样本泛化到 WAA 56.48% |
| **Qwen-CUA** | 2026-08-03（arXiv 2608.02352） | Qwen × XLang | Apache-2.0 | 纯截图、397B MoE、20 张活跃截图 + 10 步折叠、40k 可验证任务、10 万 vCPU rollout |
| **OSWorld 2.0** | **2026-06-26** | XLang Lab | 榜单 | 108 个长程任务，人类中位 1.6 小时/任务，平均 318 次工具调用 |
| **MAI-Gym-9B** | 2026-10-16（arXiv 2610.20939） | 阿里通义 MAI | Apache-2.0 | 疑似 gym/RL 训练环境向模型，**权重刚放出，值得持续跟踪** |
| **Agent S3** | 2025-10（arXiv 2510.02250） | Simular | 开源（`pip install gui-agents`） | bBoN（Behavior Best-of-N）+ grounding agent（UI-TARS-1.5-7B）；OSWorld 2.0 66%（bBoN 72.6%）、WAA 50.2%→56.6% |
| **GLM-5 / GLM-5.x** | 2026-02 起 | 智谱 | **MIT** | 744B/40B 激活，agentic 长程向。**但没有官方 computer-use 分数** |
| **GLM-4.6V / GLM-4.6V-Flash** | 2025-2026 | 智谱 | 开源 | 106B-A12B / 9B，**首次把 Function Call 原生融进视觉模型** |
| **Qwen3.5 / 3.6 / 3.8 系列** | 2026 | 阿里 | 开源（0.8B–397B-A17B，含 GGUF/FP8） | 通用多模态底座；Qwen-CUA 与 EvoCUA 都建立在 Qwen3-VL 系上 |
| **Claude Opus 5 / 5.5 / Sonnet 5 / Fable 5 / Mythos 5** | 2026 | Anthropic | 闭源 | 官方榜头部几乎全是 Claude 系 |
| **GPT-6 Astra / Sol / Luna** | 2026-09-23 | OpenAI | 闭源 | 带 Computer Use / MCP / Skills 工具；Sol $2/$10、Luna $0.10/$0.50 |

---

## C. Benchmark 现状与"刷榜可疑"分析

### C.1 OSWorld-Verified（官方榜，2026-10-19 实时拉取 xlsx）【榜单】

来源：<https://os-world.github.io/> → `static/data/osworld_verified_results.xlsx`（111 条去重记录，日期跨度 2025-07-28 → **2026-08-01**）

| 排名 | 方案 | 组织 | 类型 | 步数 | a11y树 | **代码执行** | 多rollout | 日期 | 分数 |
|---:|---|---|---|---:|:---:|:---:|:---:|---|---:|
| 1 | Intelligence-Indeed Agent | Intelligence Indeed | Agentic 框架 | 100 | 否 | **是** | 否 | 2026-07-25 | **90.19** |
| 2 | claude-fable-5[1m] | Anthropic | 通用模型 | 100 | 否 | 否 | 否 | 2026-08-01 | 85.96 |
| 3 | Pointer Agent w/ Opus 4.7 | Pointer | Agentic 框架 | 100 | 否 | **是** | 否 | 2026-05-21 | 83.64 |
| 4 | claude-opus-5[1m] | Anthropic | 通用模型 | 100 | 否 | 否 | 否 | 2026-08-01 | 83.39 |
| 5 | Coasty CUA v1 | Coasty Team | 通用模型 | 100 | 否 | **是** | 否 | 2026-07-01 | 82.81 |
| 6 | Holo3-35B-A3B | H Company | 专用模型 | 100 | 否 | 否 | 否 | 2026-04-20 | 82.56 |
| 7 | Pointer Agent w/ Sonnet 4.6 | Pointer | Agentic 框架 | 100 | 否 | **是** | 否 | 2026-05-21 | 81.45 |
| 8 | Muse Spark 1.1 | Meta Superintelligence Labs | 通用模型 | 100 | 否 | 否 | 否 | 2026-07-09 | 80.67 |
| 9 | OpenAPA w/ gemini-3.1-pro | 莱阅 | Agentic 框架 | 100 | 否 | **是** | 否 | 2026-04-17 | 78.34 |
| 10 | VLAA-GUI w/ Opus 4.5 | UCSC VLAA Lab | Agentic 框架 | 100 | 否 | **是** | 否 | 2026-04-20 | 76.26 |
| 11 | MiniMax M3 | MiniMax | 通用模型 | 100 | 否 | 否 | 否 | 2026-06-08 | 75.19 |
| 12 | HIPPO Agent w/ Opus 4.5 | 联想 | Agentic 框架 | 100 | 否 | **是** | 否 | 2026-02-25 | 74.48 |
| 13 | Qwen 3.7 Plus | 阿里通义 | 通用模型 | 100 | 否 | 否 | 否 | 2026-05-25 | 73.30 |
| 14 | Kimi K2.6 | Moonshot | 通用模型 | 100 | 否 | 否 | 否 | 2026-04-20 | 73.06 |
| 15 | agent s3 w/ Opus 4.5 + GPT-5 bBoN (N=10) | Simular | Agentic 框架 | 100 | 否 | **是** | **是** | 2025-12-11 | 72.58 |
| 16 | claude-sonnet-4-6 | Anthropic | 通用模型 | 100 | 否 | 否 | 否 | 2026-03-08 | 72.11 |
| 17 | agent s3 w/ GPT-5 bBoN (N=10) | Simular | Agentic 框架 | 100 | 否 | **是** | **是** | 2025-10-04 | 69.90 |
| 19 | UiPath Screen Agent w/ Opus 4.5 | UiPath | Agentic 框架 | 100 | 否 | 否 | 否 | 2025-12-24 | 67.14 |
| 20 | OS-Symphony w/ GPT-5 | 上海AI实验室 | Agentic 框架 | 50 | 否 | **是** | 否 | 2026-01-04 | 65.77 |
| 24 | GTA1 w/ GPT-5 | Salesforce+ANU+HKU | Agentic 框架 | 100 | 否 | **是** | 否 | 2025-10-03 | 63.41 |
| 28 | **Seed-1.8** | ByteDance Seed | 通用模型 | 100 | 否 | 否 | 否 | 2025-12-18 | 61.87 |
| 32 | aworldGUIAgent-v1 | inclusionAI | Agentic 框架 | 50 | 否 | **是** | 否 | 2025-09-18 | 58.04 |
| 35 | **EvoCUA-20260105 (32B)** | 美团 | 通用模型 | 50 | 否 | 否 | 否 | 2026-01-06 | 56.73 |
| 38 | GUI-Owl-1.5 32B | 阿里通义 | 专用模型 | 50 | 否 | 否 | 否 | 2026-03-27 | 55.44 |
| 40 | DeepMiner-Mano-72B | Mininglamp | 专用模型 | 100 | 否 | 否 | 否 | 2025-10-31 | 53.91 |
| 43 | **UI-TARS-2-2509** | ByteDance Seed | 专用模型 | 100 | 否 | 否 | 否 | 2025-10-14 | 53.10 |
| 45 | Jedi-7B w/ o3 | 港大 | Agentic 框架 | 50 | 否 | 否 | 否 | 2025-07-28 | 50.60 |
| 46 | EvoCUA | 美团 | 通用模型 | 50 | 否 | 否 | 否 | 2025-12-24 | 50.30 |
| 53 | **EvoCUA-8B-20260105** | 美团 | 通用模型 | 50 | 否 | 否 | 否 | 2026-01-13 | 46.06 |
| 59 | UI-TARS-250705 | ByteDance Seed | 通用模型 | 100 | 否 | 否 | 否 | 2025-09-16 | 41.84 |
| — | autoglm-os-9b | 智谱 | — | — | **是** | — | — | — | 47.26 |

**从这份官方榜能直接读出的三个结构性事实**（这比任何二手解读都硬）：

1. **a11y 树几乎没人用**：111 条记录里 **109 条 `Additional a11y tree used = No`，只有 2 条 = Yes**，其中最高分是 autoglm-os-9b 的 47.26%（榜中游）。
   → **结论：在 OSWorld 这种 Linux 桌面环境里，无障碍树不是加分项**（Linux 的 AT-SPI 覆盖度差、Electron/自绘控件不暴露）。UIA 的价值主要在 **Windows 真实桌面**，不在 benchmark 上。
2. **代码执行是当前分水岭**：Top 50 中 `Additional coding-based action = Yes` 占 **25/50**；全榜 Yes=27 / No=84。排 1、3、5、7、9、10、12、15、17、20 的框架级方案**全部带代码执行**。
   → **这是对"CLI anything"思路最强的官方背书**（见 D.4）。
3. **纯视觉专用模型的天花板约 50–56%**：UI-TARS-2 53.1、GUI-Owl-1.5 55.4、EvoCUA-32B 56.7；而**通用大模型 + 好 harness** 随便就 72–86%。

### C.2 OSWorld 2.0（2026-06-26 发布，官方站点）【榜单】

来源：<https://osworld-v2.xlang.ai/>（arXiv 2026，"OSWorld2.0: Benchmarking Computer Use Agents on Long-Horizon Real-World Tasks"）

- **108 个长程任务**，覆盖 7 个职业领域 / 21 个子类；人类完成中位 **1.6 小时**，**69.6% 的任务熟练用户需 1 小时以上**；平均需要 **318 次工具调用**（OSWorld 1.0 约 30 次）
- 主指标：**binary completion @ 500 步**
- 官方结论：
  - **Claude Opus 4.8（max thinking + 批量工具调用）最好，但 binary 仅 20.6%，partial 54.8%**
  - **GPT-5.5 token 效率最高（39K output token 即达 ~14% binary），但很快撞到 14% 天花板**
- **任务长度崩塌曲线**：<45 分钟任务 20–24% binary；137–163 分钟区间无一模型 >10%；**>163 分钟所有模型 binary 归零**
- 挑战标签分布：Cross-source Reasoning 42.6%、Visual-spatial Precision 41.7%、Tutorial Following 20.4%、Dynamic Environment 9.3%、Proactive Interaction 5.6%
- 失败归因（官方"暴露归因"分析）：**恢复并维持隐藏状态是最弱环节**——"lose track of constraints, miss information that arrives mid-task, guess rather than ask the user, and skip verification"
- 三个官方失败案例：Task035（晚到的 Slack 审批没被纳入提交表）、Task103（读了 CAD 图纸但导出的 STEP 几何不匹配）、Task008（收集报销证据耗尽步数预算，来不及提交）

> **判读**：OSWorld 2.0 是 2026 年最有效的"照妖镜"。任何在 OSWorld 1.0 上刷到 90% 的方案，在 2.0 上大概率 20% 都不到。

### C.3 WindowsAgentArena（WAA）

- 官方站点 <https://microsoft.github.io/WindowsAgentArena/>：**154 个任务**（LibreOffice Calc/Writer、Edge/Chrome、文件资源管理器/设置、VS Code、VLC），Azure 并行化后 **20 分钟跑完全量**
- **人类基线 74.5%**（分域：Windows 工具 91.7、文件资源管理器 83.3、web 76.7、Office 75.8、VS Code 68.4、**VLC 42.8**）；**Navi 基线仅 19.5%**
- **没有官方在线榜单**，成绩散落在论文/新闻里（这点很重要，见 C.5）
- 已报告成绩（自报，非榜上验证）：
  | 方案 | WAA | 备注 |
  |---|---|---|
  | SorTech AX Frontier + 仁济大学 MediaLab | **64.22%** (98.9/154) | 2026-07 报道，Gemini 3.1 Pro Preview + 自研 safetool 编排；同模型基础配置只 41.56%。**媒体自报**【媒体】 |
  | OS-Symphony w/ GPT-5 | 63.5% | 前最佳单次 |
  | BJudge N=3 (GPT-5) | 56.6% | 测试时扩展；Agent S3 单跑 50.2% |
  | **EvoCUA-32B** | **56.48%** | 美团自报，零样本跨 OS 泛化 |
  | UI-TARS-2 | 50.6% | 字节论文自报 |
  | Qwen3-VL-32B-Thinking | 42.9% | 底座基线 |
  | Qwen3-VL-32B-Instruct | 30.9% | |
  | Agent S3 (GPT-5) | 50.2% | @100 步 |
  | PC Agent-E | 36.0% | WAA-V2 子集(141 任务)，开源 SOTA（发布时） |
  | Claude 3.7 Sonnet (thinking) | 35.4% | WAA-V2 子集 |
- ⚠️ **口径混乱**：154 任务 vs 141 任务（WAA-V2 子集）、100 步 vs 无限步，单次 vs 3-rollout，**不可直接横向比较**。

### C.4 其余基准

| 基准 | 2026 现状 | 可信度提示 |
|---|---|---|
| **ScreenSpot-v2** | **已饱和**（~90%）：UI-TARS-7B 91.6、UI-TARS-72B 90.3（**7B 反超 72B**）、UI-TARS-1.5-7B 89.86 | 不再区分模型规模，**不适合选型** |
| **ScreenSpot-Pro** | 真正有区分度（目标框平均仅占图 0.07% vs ScreenSpot 的 2.01%，~30× 难）：官方榜第一 **Indeed-UI-32B-zoomin 82.7%**；厂商自报 GPT-6 Astra 92.7%、Claude Mythos 5.1 87.3、GPT-5.2 Thinking 86.3（开 Python 工具；不开则 GPT-5.1 掉到 64.2%）；研究基线 UI-TARS-1.5-7B 42.19、OS-Atlas-7B 18.9、GPT-4o ~0.8 | **污染风险高**（图像/标注全公开）；"zoom-in + 工具"会系统性抬高分数 |
| **UI-Vision** | 无独立榜单，零散出现在论文里。MAI-UI 报 49.2、OpenCUA-72B 报 37.3（自报 SOTA） | 口径不统一，别横向比 |
| **Online-Mind2Web** | 无官方榜。UI-TARS-2 自报 88.2；UI-TARS-1.5-7B 附近 42.9；OmniParser(LS+ID) 36.5/42.0/39.4 | 基本只被厂商自报使用 |
| **AndroidWorld** | MAI-UI 76.7（自报 SOTA，超 UI-TARS-2 / Gemini-2.5-Pro / Seed1.8）；UI-TARS-2 自报 73.3；Agent S3 68.1→71.6(bBoN)；**Midscene 报 93.1%（Gemini-3.5-Flash）** | Midscene 的 93.1% 用了**自己的环境与验证器调整**，官方页面明示"see AndroidWorld's report for its environment and validator adjustments" → **不是纯净对比** |
| **WindowsAgentArena** | 见 C.3 | 无官方榜 |
| **OSWorld / OSWorld-Verified / OSWorld 2.0** | 见 C.1 / C.2 | **唯一有官方维护方复现的榜**，可信度最高 |

### C.5 哪些是"刷榜可疑"的

| 可疑点 | 具体表现 | 建议 |
|---|---|---|
| **口径混用** | EvoCUA README 对比表把 UI-TARS-2 写成 **53.1%**，而 UI-TARS-2 论文自报 **47.5%**；53.1 其实是 OSWorld 官方榜的复现值。**自报表里混了"官方复现"和"自己论文"两种来源** | 引用 EvoCUA 的对比表时必须回查官方榜 |
| **自报 vs 复现** | Qwen-CUA 的 86.2（OSWorld-Verified）、OSWorld 2.0 18.5/48.4 **均未见官方榜收录**；EvoCUA 的 56.7 已被官方榜收录（56.73） | 优先信官方榜；自报数字标"自报" |
| **多 rollout / test-time scaling** | Agent S3 的 72.58 带 `Multiple rollout = Yes`（bBoN N=10）；与单次 50.2 不可比 | 必须看 `Multiple rollout` 标记 |
| **步数预算差异** | 50 步 vs 100 步 vs 500 步（OSWorld 2.0）能差 5–10 分 | 一定对齐步数 |
| **带工具 vs 不带工具** | ScreenSpot-Pro：GPT-5.2 Thinking 开 Python 工具 86.3，不开则 GPT-5.1 仅 64.2 | 注明工具配置 |
| **benchmark 污染** | ScreenSpot-v2 / ScreenSpot-Pro 图像与标注全公开，多篇 2026 论文（如 TRACE, arXiv 2609.10297）明确警告："在小模型上排第一并不意味着绝对精度高" | ScreenSpot 只作参考 |
| **厂商自建评测环境** | Midscene 的 AndroidWorld 93.1% 明示改过环境与验证器 | 视为厂商成绩 |
| **媒体自报无榜可查** | WAA 64.22%（SorTech，ETNews 报道） | 标注"媒体自报" |
| **规模不敏感** | ScreenSpot-v2 上 7B ≥ 72B；OSWorld 上纯视觉模型卡在 50–56 而通用模型 72–86 | 不要用 grounding 榜推 agent 能力 |

---

## D. 架构范式调研（比模型更重要）

### D.1 纯视觉 Screenshot→Action 循环

**代表**：UI-TARS、Claude computer use、Gemini computer use、Qwen-CUA、EvoCUA、OpenCUA。

**原理**：每步截屏 → 送 VLM → 模型输出归一化坐标 + 动作 → 执行 → 再截屏。

**动作空间（2026 现状）**：
- Claude `computer_toolset_20260801`：**17 个成员工具**（screenshot / click 家族 / drag / scroll / type / key / hold_key / wait / **zoom**），**每个动作独立 `tool_use` 块**，支持**批量动作**降往返；坐标永远是"你返回的那张截图的像素空间"
- Qwen-CUA：XML `<function=computer_use>`，**0–999 归一化网格**，与实际分辨率解耦；动作含 key down/up、type、mouse move、三键 click、drag、scroll、screenshot、wait、ask user、terminate
- Gemini：0–999 归一化坐标 + `require_confirmation` 分类
- UI-TARS：[0,1000]×[0,1000] 归一化坐标

**代价（都有硬证据）**：
1. **延迟**：每步 = 1 次网络往返 + 1 次推理。Claude 工具定义本身在 40 步任务上就占约 $0.53（约 4× 于旧单工具定义）；截图每张 1000–1800 token。OpenGCUA 实测 GUI-only 基线 **GPT-6 Astra 平均 102 步/任务**。
2. **误点**：Qwen-CUA 自述"models can misread screenshots, click the wrong target, enter incorrect text, **mishandle state changes, or falsely claim success**"。ScreenSpot-Pro 分数从 ScreenSpot-v2 的 ~90% 掉到 40–60% 就量化了这种精度衰减。
3. **不可恢复的代价**："native pixel interaction also requires repeated inference and serialized low-level actions, which can be **slower and more expensive than code, APIs, or shell tools**"（Qwen-CUA README 原文）。
4. **上下文膨胀**：Qwen-CUA 保留 20 张活跃截图，旧的每 10 张折叠成文本占位以保 KV-cache 前缀复用——这是**成本控制技巧**，也是纯视觉路线的固有负担。
5. **静默失败**：像素路线的 grounding 错误是**静默**的（DOM 点击会大声失败）。

### D.2 无障碍树 / UIA 树 + 视觉混合

**代表**：UFO²（Windows 原生，支持 UIA/Win32 双后端）、Magentic-UI（网页侧 a11y/ARIA）、Tarsier-AI、a11y-computer-use、OpenGCUA（`ax.observe()`）、LUMOS（arXiv 2606.30697）。

**怎么用 UIA**（三种典型用法）：
1. **Grounding 替代**：用 `role`+`name` 定位（`notepad.button("Save").click()`），不生成坐标 → 天然免疫窗口缩放/按钮位移
2. **Verification**：动作后从 UIA 读回状态，断言"按钮已消失/文本已变" → 把"模型自称成功"变成可验证事实
3. **观察补充**：UIA 树 + 截图一起给模型（OpenGCUA 明确"keeps accessibility and vision as **separate ways to observe and act**"）

**实际收益（有据）**：
- a11y-computer-use 项目自测（13 个浏览器任务）：**refs 方案 13/13 完成、0 误点、~7.04s；截图坐标方案 7/13 完成、27 次误点**（⚠️ 单方自测，未经第三方复现）
- Tarsier-AI：整个 UI 树"几百 token" vs 图像"几千 token"，**0 视觉 token、100% 确定性**（⚠️ 同样是自述）
- **MCP 化无障碍树的定性对比**（arXiv 2608.24898）：
  | 策略 | 语义保真 | 集成成本 | 延迟 | token 成本 |
  |---|---|---|---|---|
  | 截图/视觉 | 低–中 | 低 | 低–中 | **高** |
  | 各框架分别抓树 | 高 | **高** | 中 | 中 |
  | MCP 中介的树 | 高 | 低 | 中 | 低–中 |
  - 该论文的诚实警告：MCP 层只是**原生 a11y API 的下游**，降低集成与 token 成本，**但不能修延迟**——慢的 UIA 调用还是慢

**坑（必须处理）**：
1. **树不完整**：硬件加速 / 自绘 DirectX 画布、Electron 配置不当 → 空树（Tarsier-AI 自述限制）
2. **名称歧义/重名**：同名控件需额外消歧
3. **语义在观察与动作之间变化**（LUMOS 明确列为 caveat）→ 动作执行前要重新解析
4. **生态碎片**：Windows UIA / macOS AX / Linux AT-SPI / Chromium ARIA 四套 API，要一套抽象层成本不低
5. **最重要的一条**：**在 Linux 上的 OSWorld 榜单里 a11y 树几乎无人使用且不加分（109/111 = No）**。UIA 的 ROI 是在 **Windows 真实桌面**上，不是在 benchmark 上。把它当"刷榜手段"是错的方向。

### D.3 可点击元素清单（Set-of-Mark / OmniParser / SoM）

**原理**：
1. 用检测器（YOLOv8）找出可交互区域
2. 用 captioner（BLIP-2 微调）给图标生成语义描述
3. OCR 读文本
4. 把三者合并成 **SoM 叠加层**：给每个框编号 + 局部语义文字
5. VLM 只需输出**编号**而不是 xy 坐标 → 绕开"GPT-4V 不擅长直接预测坐标"这个老问题

**精度**：
- OmniParser V2 官方自报：ScreenSpot-Pro **39.6%**，标称 Windows Agent Arena 当时最佳
- 局部语义消融（GPT-4V）：无局部语义 0.913/0.692/0.620（易/中/难）→ 总体 0.705；加局部语义 1.000/0.949/0.900 → 总体 **0.938**。增益随框数量增大
- **2026 年的第三方结论相反**："GUI Grounding 实践指南"对 Gemini-3-Pro / Claude-Sonnet-4.5 / Seed1.8 / Kimi-K2.5 / MAI-UI 实测：**所有模型在 SoM 下的 OSWorld-G 都 <30**——密集 UI 的视觉噪声和大量重叠标注框把 SoM 打崩了
- 官方结论：**E2E 原生 grounding 明确优于 SoM**；但 E2E 跨基准方差巨大（Claude Sonnet 4.5：OSWorld-G 69.0 → ScreenSpot-Pro 35.0）

**延迟**：
- OmniParser V2 官方：比 V1 提升 60%，**A100 ~0.6s/帧、RTX 4090 ~0.8s/帧**
- 拆开看：`icon_detect` 是 ~11.6MB 的 YOLOv8 ONNX，**CPU 可跑**；captioner + OCR 才是重头
- 端到端还要叠加 VLM 推理（这才是大头）

**2026 判读**：SoM 属于 **2024–2025 的解法**，2026 的主流已转向 **E2E 原生 grounding + zoom-in**（Claude 的 `zoom` 工具默认开启，2026 指南的改进轴是 "**SoM → E2E → 推理期优化**"）。**不建议新项目采用 SoM 作为主 grounding 通道**；OmniParser 的 YOLO 检测器仍可作为"哪里可能有可点元素"的廉价先验。

### D.4 纯 CLI / 语义化接口驱动 —— **有对应工作，且是 2026 最强证据**

**结论：不仅有工作，而且这正是 2026 年的行业主线。** 三层证据：

**证据 1：OpenGCUA（阿里通义，Apache-2.0，arXiv 2610.22200，2026-10）** —— 最直接的一手材料：

> "**We call such a system a generalist computer-use agent (GCUA).** It uses a computer in more ways than you do. It reads the accessibility tree and calls app APIs, **runs shell commands**, and operates the GUI when it needs to. With **code execution**, it writes GUI operations as code and combines them with all of these in one program, with loops and conditions."

OpenGCUA 明确把三种既有设计降级为特例：

> "GUI agents, CLI agents, and GUI+CLI hybrids such as our earlier **Qwen-UI-Agent** are special cases that fix some of these decisions in advance."

其实测（OSWorld-v2 offline set，8 个前沿模型）：

| 指标 | GUI-only 基线 | OpenGCUA 设计 | 变化 |
|---|---:|---:|---|
| 平均 partial score | 45.3 | **64.9** | **+19.6** |
| 每任务步数 | — | — | **−53%**（GPT-6 Astra：102 → 34 步） |
| 最强−最弱模型差距 | 39.1 | **18.0** | 收窄 |
| 弱模型增益 | — | GLM-5.3-Flash **+35.2**；DeepSeek-V4.1-Flash **+29.8** | 越弱越受益 |
| **代码执行 vs GUI+CLI** | — | **+6.1 分，步数 −14.8%**（3 模型均值） | 代码 > 纯 CLI |

三决策对照表（原文）：

| 决策 | GUI agent | OpenGCUA |
|---|---|---|
| 👁️ 观察什么 | 每个动作后都截图 | `screenshot()`，**只在模型需要时** |
| ✋ 怎么动 | 一次一个点击/按键 | **一个 Python 程序**，组合 a11y 树 + app API + shell + GUI 动作，带循环/分支/检查 |
| 🗂️ 怎么组织 | harness 写死 | 模型自己维护 `todo` 列表，每段程序前声明 `<intent>` |

**证据 2：OSWorld 官方榜的编码列**（C.1）——Top 50 中 25/50 带 `Additional coding-based action = Yes`，前 20 名里带代码执行的占 10 个。

**证据 3：LUMOS（arXiv 2606.30697）** 提出"**semantic operating system layer**"论证：截图强迫模型**同时**解决感知与动作 grounding 两件事，"semantic blueprints" 把两者拆开 → 更易测试、更省 prompt。

**其他相关工作**：
- **Qwen-UI-Agent**（阿里，OpenGCUA 前作，明确是 GUI+CLI 混合）
- **Midscene.js**（web-infra-dev，**MIT**，15k+ stars）：**基于视觉的 GUI Agent + 自然语言 E2E 测试框架，支持 Web、移动端和桌面应用**；用 `aiQuery`/`aiAction` 这样的**语义化 API** 而不是坐标（`notepad.button("Save").click()` 风格）
- **UFO²** 的 Gui-Agent-Use 动作空间：app 原生 API + GUI 统一
- **Agent S3** 的 ACI（Agent-Computer Interface）：动作翻译成可执行 Python
- **open-codex-computer-use**（MIT，Swift/Node MCP server）：**基于无障碍树的桌面控制**暴露给 Claude/Codex/Gemini，Windows 用 UIA、macOS/Linux 用 AT-SPI

> **一句话回答用户的"CLI anything"直觉**：这个思路在 2026 已经被验证为**当前最有效的架构方向**，且已有可跑的开源参考实现（OpenGCUA / Qwen-UI-Agent / Midscene / UFO²）。在 Windows 上落地版本就是：**UIA 树 + PowerShell/CLI + 应用 API 优先，截图/坐标点击作为最后兜底**。

### D.5 2026 视角下"一份高质量 Windows GUI Agent 架构"长什么样

**共识分层**（综合 OSWorld 2.0 失败归因 + OpenGCUA 三决策 + Claude toolset + UFO² 经验）：

**L0 感知层（多通道，按需取用，不是全量喂）**
- 截图：**只在模型真正需要看像素时**取（OpenGCUA 明确：不要每个动作后都截）
- UIA 树：**默认主通道**，做语义定位 + 状态验证
- 控件级精确定位：`UIA ElementFromPoint`（LUMOS 提到作为 cursor-crop 的替代）
- 文本/剪贴板：读当前选中内容、读剪贴板 → 少打字、少错字
- 缩放：Claude 的 `zoom` 工具（默认开启）—— 坐标永远基于"你返回的那张截图"

**L1 动作层（按可靠性从高到低降级）**
1. 应用原生 API / COM / UIA Invoke 模式（可靠、可验证）
2. 键盘 + 剪贴板（比逐字点击快且不出错）
3. CLI / PowerShell（`Get-Process`、文件操作、注册表——"GUI 之外的第二条腿"）
4. 代码执行（把一批 GUI 操作写成脚本，带循环/分支/断言）
5. 坐标点击 / 拖拽（**最后手段**）
- 动作执行前必须做**参数校验**（坐标在窗口内、目标存在、参数 schema 合法）

**L2 规划层**
- 显式 `todo` 列表 + 每步前声明 `<intent>`（OpenGCUA 模式）
- **主动提问**：任务含糊时问用户，不要猜（OSWorld 2.0 把 "guess rather than ask the user" 列为主要失败模式）
- 动态环境重规划：新信息（邮件/IM）到达时**刷新工作表**，而不是凭记忆修表（OSWorld 2.0 Task035 就是这个失败）

**L3 验证层（最被低估的一层）**
- **不信任模型自称的成功**：动作后必须重新观测并断言环境状态（OpenGCUA 例子里的 `assert current==original` / `assert lead['status']==change['to']`）
- Qwen-CUA README 原文："a model's `terminate(success)` action is **not proof of real-world success**"
- 验证优先用**确定性检查**（UIA 读回、文件 hash、API 返回），而不是再看一次截图让模型自己判断

**L4 安全与恢复层**
- 不可逆动作确认：提交、支付、发送、删除、下载、上传、凭据输入、跨域跳转
- 提示注入防御：截图内容是不可信输入（Claude 会自动扫描但"不能完全阻止"）
- 独立重试/回滚：失败重试要有上限 + 死循环检测
- 保留审计轨迹：截图、原始响应、动作、验证证据

**必须处理的公认坑清单（Windows 专项）**

| 坑 | 现象 | 处理 |
|---|---|---|
| **DPI 缩放** | 截图是物理像素，`SendInput` 发的是逻辑坐标（线程非 per-monitor DPI aware）→ 点击偏移 ~1.5×（1.25×/2.0× 也常见）。**30–50% 的 Windows 设备缩放 >100%** | 统一用**物理像素**端到端；`GetDpiForWindow()/96.0`；截屏与派发必须同一坐标空间；**绝不能把"某些工具给物理坐标、截屏给逻辑分辨率"混用**（`windows-mcp` 就有这个 bug） |
| **多显示器异 DPI** | 单一 scale factor 错 | 逐显示器取缩放；虚拟桌面全局原点与 per-monitor 逻辑矩形会重叠 → 需按屏换算 |
| **焦点 / 前台窗口** | 某些原生控件只在"认为自己有焦点"时接受输入；`SendInput` 打到 root HWND 而非子控件；焦点恢复会覆盖用户更新的选择 | 每批输入前**重新确认目标仍拥有前台**；把后台输入路由到**最深的可用子 HWND**；焦点变更后释放已按住的键；清理只被部分接受的 `SendInput` 前缀 |
| **异步加载** | 页面/对话框还没渲染完就点 | **等状态，不等固定 sleep**；每次动作后重新截图/读 UIA 判断 |
| **弹窗** | 确认框/权限框/UAC 打断流程 | 显式建模弹窗状态；UAC 需要提权会话，agent 进程要处理 |
| **误操作不可逆** | 一次误点 = 邮件发出去了 | 动作分级（可逆/半可逆/不可逆）；不可逆动作必须人工确认；批量操作先 dry-run + 断言 |
| **循环检测** | 反复点同一个位置 | 动作+截图指纹去重；步数预算（50/100/500 步差异巨大）；同一目标连续失败 N 次就换策略或上报 |
| **隐藏状态** | "把凭证填了但没保存" | 动作后读 UIA/文件/数据库验证 |
| **屏幕捕获** | 远程桌面黑帧、硬件加速黑屏、标注覆盖 | 用 Windows Graphics Capture 而非 `mss` 抓黑屏；标注层单独通道 |
| **UIPI 提权隔离** | **非提权进程无法向提权（管理员）窗口发送输入——光标会动，但点击和按键被静默丢弃**（Midscene 官方文档明确记录） | 要么把目标程序以非提权运行，要么把 agent 进程也**以管理员身份运行**；agent 侧应检测"输入未生效"而非重试点击 |
| **单/多显示器** | UI-TARS-desktop 官方**只支持单显示器** | 自建时按屏处理；给目标 app 的窗口做局部截图；Midscene 的 `@midscene/computer` 原生支持 multi-display |

---

## E. 落地可行性：Windows + RTX 3060 Laptop 6GB + 可用 API

### E.0 硬约束

- **显存 6GB**：能跑的最强 GUI 模型是 2B（4bit 量化 ≈ 1.2–1.5GB，llama.cpp）或 7B 的 4bit（≈ 4.0–4.5GB，**KV cache + 视觉 token 激活后极易 OOM**）。
- **7B 视觉模型在 6GB 上不可用**：UI-TARS-1.5-7B / OpenCUA-7B / MAI-UI-8B 的 BF16 是 14–16GB，4bit 也塞不下长视觉上下文。
- **2026 年的开源 SOTA 全部不可本地化**：EvoCUA-32B（32B）、Qwen-CUA（397B，BF16 807GB，官方参考配置 H200×8）、MAI-UI-8B/32B（8B 可勉强但精度远低于 32B）。
- **但本机 6GB 有真用途**：跑 **OmniParser 的 YOLO 检测器（11.6MB ONNX，CPU 也够）** + **OCR（PaddleOCR / RapidOCR）** + **本地 UIA 树序列化**，作为"免费高精度结构化通道"。

### E.1 成本模型（用于三套方案对比）

**每步 token 估算**（基于 Anthropic 官方公式 `(w×h)/750`，1280×720 ≈ 1,230 tok/图）：
- 方案 A（**裁剪/短历史**，推荐）：最近 4 张截图（4,920）+ 文本 ~800 → **输入 ≈ 5,700 tok/步**；输出 ~600 tok/步（推理 + 动作 XML）
- 方案 B（**全历史 20 屏**）：20 × 1,230 + 文本 2,000 → **输入 ≈ 26,600 tok/步**；输出 ~600

**单价（2026-10 查证）**：
| 模型 | 输入 $/Mtok | 输出 $/Mtok | 来源 |
|---|---:|---:|---|
| Claude Sonnet 5 | 2.00 | 10.00 | 官方定价页 |
| Claude Opus 5.5 | 4.00 | 20.00 | 官方定价页 |
| Claude Haiku 4.5 | 1.00 | 5.00 | 官方定价页 |
| GPT-6 Sol | 2.00 | 10.00 | 媒体/聚合（建议核官方页） |
| GPT-6 Luna | 0.10 | 0.50 | 媒体/聚合（建议核官方页） |
| qwen3-vl-plus（≤32K 档） | 0.20 | 1.60 | 阿里云百炼定价页 |
| qwen3-vl-flash（≤32K 档） | 0.022 | 0.215 | 阿里云百炼定价页 |

**每 1,000 次动作的 API 费用（计算过程）**

| 模型 | 短历史（方案A） | 全历史 20 屏（方案B） |
|---|---:|---:|
| Claude Sonnet 5 | 5,700×2 + 600×10 = 17,400 → **≈ $17** | 26,600×2 + 600×10 = 59,200 → **≈ $59** |
| Claude Opus 5.5 | 5,700×4 + 600×20 = 34,800 → **≈ $35** | 26,600×4 + 600×20 = 118,400 → **≈ $118** |
| Claude Haiku 4.5 | **≈ $9** | **≈ $30** |
| GPT-6 Sol | **≈ $17** | **≈ $59** |
| GPT-6 Luna | **≈ $0.9** | **≈ $3** |
| qwen3-vl-plus | 5,700×0.2 + 600×1.6 = 2,100 → **≈ $2.1** | 26,600×0.2+600×1.6 = 6,280 → **≈ $6.3** |
| qwen3-vl-flash | **≈ $0.27** | **≈ $0.71** |

**结论**：
1. **历史长度是成本主控杆**（Sonnet 5 下 $17 vs $59，3.5× 差），比换模型更值得优化 → 用"裁剪/折叠/缓存"（Qwen-CUA 的 20 活跃 + 10 步折叠、Claude 的 prompt caching）。
2. **官方口径的"每次动作 ≈ 1 张截图"是对的**：一张 1,230 tok 图 + 600 tok 输出 ≈ Sonnet 5 下 $0.008/步 → **1,000 步 ≈ $8~17**。
3. 行业侧的交叉印证：AutomationBench 上 GPT-6 Sol @xhigh **$0.27/任务**、Opus 5.5 max **$1.28/任务**、Astra max **$1.73/任务**；另有测评称"50 步会话约 $1–2"（因多数会话远短于 50 步全开）。
4. **缓存可再降 10×**：Claude 缓存读价 = 输入价的 10%（Opus 5.5 缓存读 $0.20/MTok）。把稳定的系统提示 + 历史折叠放缓存。
5. DashScope 定价**按单请求输入长度分档**（32K/128K/256K），短历史方案能稳在最低档。

### E.2 三套候选架构

---

#### **方案 1 ⭐ 推荐：纯 API 强模型 + 自建 UIA 优先执行层（Windows 原生）**

**组成**
- **大脑**：云端 API。建议 `qwen3-vl-plus`（国内网络/性价比）或 `Claude Sonnet 5`（复杂任务）/ `GPT-6 Sol`
- **执行层（自建，约 800–1500 行 Python）**：
  - 观察：`mss` 或 Windows Graphics Capture 截图 + **UIA 树裁剪**（pywinauto / comtypes + UIAutomation，或 FlaUI for .NET）
  - 动作：优先 UIA Invoke / 键盘+剪贴板 / PowerShell CLI；**坐标点击兜底**
  - 验证：动作后 UIA 读回 + 确定性断言
  - 安全：不可逆动作确认 + 步数预算 + 死循环检测
- **可选增强**：OmniParser YOLO ONNX 标注"可交互区域"给模型当先验（CPU 即可）
- **强烈建议参考现成执行层**：`@midscene/computer`（MIT，Windows/macOS/Linux，多显示器、拖拽、MCP server、语义化 `aiAction` API）已经把截图/输入/多屏这些脏活干完了——**借鉴或直接复用它的输入层，把精力放在 UIA 语义化和验证层上**，比从零写 pyautogui 划算

**代价**：API 费用 $2/千步（qwen3-vl-plus）~ $59/千步（Claude Sonnet 5 全历史）；自建执行层是主要工程量
**延迟**：每步 3–10 s（网络 1–3 s + 推理 1–5 s + 截图 0.1 s）
**稳定性**：**最高**。UIA 通道的误点率远低于纯视觉；官方榜上 UIA 路线在 Windows 上有 UFO² 30.5% 的实测背书
**隐私**：截图出本机（可用自建代理 + 本地裁剪缓解）
**上手速度**：1–2 周做出可用原型

---

#### **方案 2：本地小 VLM + 本地 OCR/检测 + UIA 兜底（离线 / 隐私优先）**

**组成**
- **本地模型**：`Tongyi-MAI/MAI-UI-2B`（Apache-2.0，Qwen3-VL 底座）或 `ByteDance-Seed/UI-TARS-2B-SFT`（Apache-2.0，v1）；走 llama.cpp GGUF Q4_K_M 量化
- **本地辅助**：OmniParser `icon_detect` ONNX（11.6MB，CPU）+ RapidOCR/PaddleOCR
- **主定位通道**：UIA 树（模型只负责"理解意图 + 从 UIA 候选里选"）
- **6GB 可行性**：2B Q4 权重 ~1.2–1.5GB，余量充足；7B Q4 ≈ 4–4.5GB 挤得下但视觉上下文易 OOM

**代价**：0 API 费；电费 + 硬件折旧；**精度显著低于 API**
**延迟**：本地 2B 推理 ~2–6 s/步（**估算**，取决于是否走 llama.cpp 视觉塔 + CPU offload）；加 OCR 0.2–0.5 s
**稳定性**：模型弱 → grounding 错 → **必须靠 UIA 兜住**，否则极易误点
**关键风险**：2B 模型在 ScreenSpot-Pro 级别任务上的精度**没有官方数据**，必须自测；保守估计其 grounding 准确率远低于 7B/8B 家族（MAI-UI 官方 8B 是 ScreenSpot-Pro 73.5，2B 无公开数）
**适合**：离线、涉密、或作为方案 1 的"预筛/降级"通道

---

#### **方案 3：直接用 UI-TARS-desktop / Agent TARS（字节成品）**

**组成**：`bytedance/UI-TARS-desktop`（Apache-2.0，Electron/TS，含 Agent TARS CLI + UI-TARS Desktop）；模型可接本地 `UI-TARS-1.5-7B` 或云端（HF / 火山引擎 Doubao / Anthropic / Seed-VL）

**代价**：低（现成 app + 现成权重）
**延迟**：中（Electron + VLM 往返）
**稳定性**：⚠️ **有明确负面证据**（2026-06 Gigazine 实测）：**官方只支持单显示器**；**远程 Operator 功能已废弃**；README 宣称 "Private and secure - fully local processing" 但**实际四个 provider 全是云端**；**文档维护不佳**
**额外风险**：UI-TARS-1.5-7B 是 2025-04 的模型，OSWorld 只有 42.5 水平，远落后 2026 的 API 强模型
**适合**：快速原型 / 体验 / 参照其坐标归一化与执行层设计，**不建议作为生产底座**

---

### E.3 推荐与理由

> **推荐方案 1（纯 API 强模型 + 自建 UIA 优先执行层），本地 2B 只作为兜底/预筛通道。**

**理由（按权重排序）**

1. **算力硬约束**：6GB 显存装不下任何有竞争力的 GUI 模型。2026 开源 SOTA 全部 ≥8B（MAI-UI-8B 起）乃至 32B/397B。本地 2B 只能做"意图理解"，做不了可靠的 grounding——而 grounding 正是 GUI agent 的全部难点。

2. **2026 的胜负手在 harness，不在 checkpoint**。OpenGCUA 用**同一个模型**（8 个前沿模型）只改 harness，OSWorld-v2 partial **45.3 → 64.9（+19.6）**、步数 **−53%**。这说明 invest 的正确顺序是 **执行层 > 提示/规划/验证层 > 模型**。你的差异化恰恰在执行层（Windows UIA 深度、坐标/焦点/DPI 正确性），而不是买更大模型。

3. **官方榜的结构性证据**：Top 50 里 25 个带代码执行，前 20 名里 10 个带——**"先想办法不用像素"是当前赢家的做法**。Windows 上 UIA + PowerShell + 剪贴板正是"像素之外的腿"。

4. **成本完全可控且远低于误点代价**：qwen3-vl-plus 约 **$2/千步**、Claude Sonnet 5 短历史约 **$17/千步**。而一次误点（发错邮件、删错文件、误付款）的代价不可逆。**用 API 换 grounding 精度是这笔账里最划算的一笔**。

5. **UIA 的 ROI 是 Windows 专属红利**：官方榜 109/111 不用 a11y 树，说明**在 Linux benchmark 上没用**；但 Windows 桌面生态（Office、VS Code、资源管理器、设置）UIA 覆盖度高，UFO² 已证明 WAA 上 30.5% vs Navi 19.5%。这块能力在 Linux 上学不到，只能自己写。

6. **可分阶段降级**：先纯 API 跑通，把执行层做对；再考虑接本地 2B 做"截图预筛/降级通道"降低长尾成本。

**落地路线（建议顺序）**
1. 执行层先行：截图（物理像素）+ UIA 树读取 + 焦点/DPI 正确性 + 动作校验 + 验证断言。**这一步不依赖任何模型，却决定 80% 的稳定性。**
2. 接入 `qwen3-vl-plus` 或 Claude Sonnet 5，做"意图 → UIA 候选选择"（不做坐标），坐标点击仅兜底。
3. 加 CLI/剪贴板/代码执行通道（抄 OpenGCUA 的三决策模型）。
4. 加不可逆动作确认、步数预算、死循环检测、审计轨迹。
5. 再按需接本地 2B/OCR/YOLO。

---

## F. 来源清单（按等级）

### 【仓库 / 官方组织页】
| 内容 | URL |
|---|---|
| 腾讯 TARS（BSD-3，README 原文） | <https://github.com/TarsCloud/Tars> |
| 腾讯 TARS 文档 | <https://tarscloud.github.io/TarsDocs_en/> |
| TarsWeb（运维控制台，非 AI） | <https://github.com/TarsCloud/TarsWeb> |
| 腾讯混元数字人研究仓库 | <https://github.com/Tencent/DigitalHuman> |
| 腾讯 HunyuanVideo-Avatar | <https://github.com/Tencent-Hunyuan/HunyuanVideo-Avatar> |
| 字节 UI-TARS（模型 + 推理代码） | <https://github.com/bytedance/UI-TARS> |
| 字节 UI-TARS README（base64 API，便于程序化核对） | <https://api.github.com/repos/bytedance/UI-TARS/readme> |
| 字节 UI-TARS-desktop / Agent TARS | <https://github.com/bytedance/UI-TARS-desktop> |
| 字节 Seed1.5-VL（仅 cookbook） | <https://github.com/ByteDance-Seed/Seed1.5-VL> |
| 官方请求 UI-TARS-2 权重（issue #213） | <https://github.com/bytedance/UI-TARS/issues/213> |
| 微软 Magentic-UI | <https://github.com/microsoft/magentic-ui> |
| 微软 UFO / UFO² | <https://github.com/microsoft/UFO> · 文档 <https://microsoft.github.io/UFO/> |
| 微软 OmniParser | <https://github.com/microsoft/OmniParser> |
| XLang OpenCUA | <https://github.com/xlang-ai/OpenCUA> |
| XLang Aguvis | <https://github.com/xlang-ai/aguvis> |
| XLang Qwen-CUA | <https://github.com/xlang-ai/Qwen-CUA> |
| Simular Agent-S / S3 | <https://github.com/simular-ai/Agent-S> |
| 阿里 OpenGCUA | <https://github.com/Tongyi-MAI/OpenGCUA> |
| 阿里 MAI-UI | <https://github.com/Tongyi-MAI/MAI-UI> |
| 美团 EvoCUA | <https://github.com/meituan/EvoCUA> |
| NUS ShowUI | <https://github.com/showlab/ShowUI> |
| OS-Copilot OS-Atlas | <https://github.com/OS-Copilot/OS-Atlas> |
| 南大 SeeClick | <https://github.com/njucckevin/SeeClick> |
| 智谱 AutoGLM | <https://github.com/zai-org/AutoGLM> |
| web-infra-dev Midscene（MIT，Web/移动/桌面 GUI agent） | <https://github.com/web-infra-dev/midscene> |
| Midscene 桌面包 `@midscene/computer`（Windows/macOS/Linux） | <https://www.npmjs.com/package/@midscene/computer> · 文档 <https://midscenejs.com/zh/platforms/desktop.md> |
| HF：ByteDance-Seed / xlangai / meituan / Tongyi-MAI / zai-org / showlab / OS-Copilot / moonshotai / Qwen | <https://huggingface.co/ByteDance-Seed> · <https://huggingface.co/xlangai> · <https://huggingface.co/meituan> · <https://huggingface.co/Tongyi-MAI> · <https://huggingface.co/zai-org> · <https://huggingface.co/showlab> · <https://huggingface.co/OS-Copilot> · <https://huggingface.co/moonshotai> · <https://huggingface.co/Qwen> |

### 【官方榜单 / 基准】
| 内容 | URL |
|---|---|
| OSWorld 官方站（Verif 榜 + OSWorld 2.0 入口） | <https://os-world.github.io/> |
| OSWorld-Verified 榜原始数据（xlsx，本报告直接解析） | <https://os-world.github.io/static/data/osworld_verified_results.xlsx> |
| OSWorld 2.0（2026-06-26） | <https://osworld-v2.xlang.ai/> |
| WindowsAgentArena 官方站 | <https://microsoft.github.io/WindowsAgentArena/> |
| WAA 代码与任务集 | <https://github.com/microsoft/WindowsAgentArena> |
| ScreenSpot-Pro 官方项目页 | <https://lucas-sun.github.io/ScreenSpot-Pro/>（建议以该页与官方 repo 为准） |
| Midscene 基准报告 | <https://midscenejs.com/android-world-benchmark-report> |

### 【论文】
| 内容 | URL |
|---|---|
| UI-TARS（v1） | <https://arxiv.org/abs/2501.12326> |
| **UI-TARS-2 技术报告（权重未开源）** | <https://arxiv.org/abs/2509.02544> |
| Magentic-UI | <https://arxiv.org/abs/2507.22358> |
| UFO²: The Desktop AgentOS | <https://arxiv.org/abs/2504.14603> |
| WAA 基准 | <https://arxiv.org/abs/2409.08264> |
| OpenCUA | <https://arxiv.org/abs/2508.09123> |
| Aguvis | <https://arxiv.org/abs/2412.04454> |
| ShowUI | <https://arxiv.org/abs/2411.17465> |
| OS-Atlas | <https://arxiv.org/abs/2410.23218> |
| Agent S（ICLR 2025） | <https://arxiv.org/abs/2410.08164> |
| Agent S3 / Scaling Agents for Computer Use | <https://arxiv.org/abs/2510.02250> |
| **EvoCUA** | <https://arxiv.org/abs/2601.15876> |
| **Qwen-CUA** | <https://arxiv.org/abs/2608.02352> |
| **OpenGCUA** | <https://arxiv.org/abs/2610.22200> |
| MAI-UI | <https://arxiv.org/abs/2512.22047> |
| Seed1.5-VL | <https://arxiv.org/abs/2505.07062> |
| CUA 安全评测（EvoCUA 引用） | <https://arxiv.org/abs/2602.08235> |
| LUMOS（a11y 作为语义层） | <https://arxiv.org/abs/2606.30697> |
| MCP 无障碍树标准化对比 | <https://arxiv.org/abs/2608.24898> |
| OSWorld 2.0（arXiv id 未在本轮核实，官方站 bibtex key：`yuan2026osworld20benchmarkingcomputeruse`） | <https://osworld-v2.xlang.ai/> |

### 【官方文档】
| 内容 | URL |
|---|---|
| Anthropic computer use tool（`computer_toolset_20260801`） | <https://platform.claude.com/docs/en/agents-and-tools/tool-use/computer-use-tool> |
| Anthropic 定价 | <https://platform.claude.com/docs/en/about-claude/pricing> |
| 阿里云百炼模型定价 | <https://www.alibabacloud.com/help/en/model-studio/model-pricing> |

### 【媒体 / 二手（需交叉验证）】
- Gigazine UI-TARS-desktop 实测（2026-06-28，单显示器/远程 operator/云端 provider）——**结论已标为"单一博客来源"**
- ETNews SorTech WAA 64.22%（2026-07-10）——**媒体自报，非榜上验证**
- 第三方 grounding 复现指南（ScreenSpot-Pro 复现 vs 报告差 33 分的案例）——**匿名二手，方法学可信但作者不明**
- Project Mariner 关停（2026-05-04）——多来源一致，但**无 Google 官方公告**
- TokenMix / ossaihub 等"UI-TARS-2 已开源"——**内容农场，与官方冲突，不采信**

---

## G. 明确标注为"未核实 / 存疑"的事项

1. **Ferret-UI 权重**：HF `inclusionAI` org 实测未搜到 Ferret-UI 模型条目，其开源状态与许可**未能确认**。
2. **Aguvis 许可**：`xlangai/Aguvis-7B-720P` 的 HF tags 中**无 license 字段**，商业使用需自行确认。
3. **OpenBMB / THUDM / xlang-ai org 的完整清单**：`hf-mirror` 上部分 org 查询返回空/失败，未能完整枚举。
4. **UI-TARS-2 参数量**：官方未披露，"~72B"为外界推测。
5. **UI-TARS v1 权重许可细则**：HF 卡片标 Apache-2.0，但 UI-TARS README 的 Model Scale Comparison 处另有"只发引用"字样，**商用前应读 LICENSE 全文**。
6. **GPT-6 / Claude Opus 5.5 等 2026 年 9 月后价格**：来自媒体与聚合站，**未在官方定价页逐条核对**（Anthropic Opus 5 / 5.5 / Sonnet 5 已核到官方页）。
7. **本地 2B GUI 模型的实际 grounding 精度**：无官方公开数据，本报告的延迟/精度判断为**推断**。
8. **OpenGCUA 的 Windows 支持**：当前仅 macOS harness + 跨设备 SDK（适配 macOS/Android/浏览器/Linux），**Windows 适配未见**，需自行移植。
9. **OSWorld 2.0 官方 leaderboard 的逐条分数**：页面为 JS 动态加载，本轮只取到站点文案中的关键数字（Claude Opus 4.8 20.6%/54.8%、GPT-5.5 ~14%），未取到完整表格。

---

*报告完成：2026-10-19。所有"官方榜"数据由本轮直接下载 xlsx 解析；所有 HF 清单由本轮实时拉取 API 得出；所有引号内英文为来源原文。*

# Changelog

本项目遵循 [语义化版本](https://semver.org/lang/zh-CN/)。日期为 Asia/Shanghai。

## 未发布（分支 nightly/2026-11-01，未打 tag、未改版本号）

### 安全修复

- **P0 · HTTP serve 写接口信任边界**（`src/minicode/serve/http.py`）：原先只绑回环，
  没有鉴权与来源校验——任意网页可用 `Content-Type: text/plain` 的简单跨域请求
  （免 CORS 预检）驱动这个能真点鼠标的服务，DNS rebinding 后还可伪装同源。
  现在写接口有四道门（全 fail-closed）：`Host` 必须回环；`Content-Type` 必须为
  `application/json`；必须带自定义头 `X-Minicode-Token` 且与服务端
  `MINICODE_SERVE_TOKEN` 环境变量匹配（未配置 token 时写接口全部 403）；
  请求体上限 1 MiB（413）、并发上限 8（503）。CORS 预检不应答（响应永远不带
  `Access-Control-*`）。**有意的契约变更**：既有 HTTP 测试由「无鉴权直连 200」
  改为显式带 token —— 这是安全修复改变了契约，不是测试被删或被跳过。

### 对外表述修正（逐条可核对，未新增任何数字）

- README 数字墙 59.02% 加脚注：该数字为**点式定位路径**（模型直接输出坐标 + 运行时
  标定，`eval/sspro.py` 未传候选源）的成绩；编号候选分支仅在 42 条子样本上量过，
  未在同口径 183 条上复测。依据：审查报告评测方法论 #2。
- README 新增「评测配置」节：判定口径（ScreenSpot-Pro 官方 bbox 命中）、样本量与
  抽样方式（计划 330，本地缺图静默过滤后实际 183，非随机子集）、调参集即评测集
  的披露（超参同批扫描后报同批分数，绝对值带乐观偏差）、以及「该批分数所用模型
  与端点未记录」的复现缺口声明。依据：审查报告评测方法论 #1/#3/#5。
- **删除「同配置两次独立跑批差 ±5.46 pp（mid-p p=0.0989）」**：该数字只存在于
  README 正文，仓库内没有任何产物可复算它（配对明细 JSON 不在库，按 b+c=10 的
  任何组合也复算不出 p=0.0989）。规则：无产物支撑的数字不上 README；
  待配对明细入库后恢复这句话。依据：审查报告评测方法论 #4。
- English Summary 同步补点式路径 caveat 与复现缺口声明。

## v0.1.4 — 2026-10-26

纯文档与视觉资产变更，不含任何代码能力变更（`src/` 零改动，实测数字不变）。

### 变更

- README 改用「产品落地页」版式：首屏 `<picture>` 明暗双版头图、三张能力卡片、
  一眼看完的数字墙、三步上手；正文按「实测 → 短板 → 架构 → 定位差异 → 安装 / 配置 / 用法
  → 目录结构 / 测试 / 隐私」组织，去掉冗长目录与附录说明。
- **全部配图改为手写矢量 SVG**，图片总体积从 2.7 MB 降到约 19 KB：
  `hero-light.svg` / `hero-dark.svg`（首屏，暗色模式自动切换）、`arch.svg`（六层架构图，
  取代原先会横向溢出、文字折行的 mermaid 块）、`chart-overall.svg` / `chart-category.svg` /
  `chart-platform.svg`（数据图）。
- 数据图改由 `scripts/make_figures_svg.py` 生成：纯 Python 拼 SVG，无绘图库依赖；
  数字写死在脚本并逐条对照 `runs/v2-eval-2016-control/sspro-report.json`。
  原 matplotlib 版 `scripts/make_figures.py` 与对应 PNG 删除。
- 配置节从四个并列的 `.env` 代码块压成「一个例子 + 一张端点对照表 + 折叠补充」；
  目录结构由纯文本代码块改为「路径 / 职责」两列表格；删除面向维护者的「附注」节。
- `docs/assets/social-preview.png` 重制为 1280×640、190 KB（GitHub Social preview 有 1 MB
  上限，原图 1.38 MB 传不上去）。

## v0.1.3 — 2026-10-26

纯文档与视觉资产变更，不含任何代码能力变更（src/ 零改动，实测数字不变）。

### 变更

- README 从零重构：第一屏（项目名 + 英文副标题 + 徽章行 + 头图 + 电梯陈述 + 实测数字摘要表）
  → 分维度实测（三张数据图）→ 诚实短板 → 架构（mermaid + 层到包映射 + 三条关键设计 + ADR）
  → 定位差异 → 安装 / 配置 / 用法 → 目录结构 / 测试 / 隐私与安全 → English Summary，全程 TOC。
- 视觉系统统一重做（近黑底 + 青色 / 琥珀点缀）：`docs/assets/banner.png`（头图）、
  `workflow.png`（工作流概念图）、`architecture.png`（架构氛围图）、
  `social-preview.png`（GitHub Social preview 专用，网页设置里上传）；
  旧 `hero.png`、`benchmark.png` 删除。
- 三张真实数据图改由 `scripts/make_figures.py` 生成（`by-overall.png` 带 Wilson 95% CI 误差棒、
  `by-category.png`、`by-platform.png`），数据写死在脚本并标注来源字段，重跑可得同一张图。
- 三段本机真实终端输出移出 README 正文：正文只留真机最小闭环摘录，
  完整输出与采集命令集中在 `docs/EXAMPLES.md`。
- 徽章改为 shields.io 静态徽章（Python 3.11+ / License MIT / Release v0.1.3 / Platform Windows /
  Tests 1326 passed），无 CI、覆盖率、下载量徽章。

## v0.1.1 — 2026-10-26

纯文档与视觉资产变更，不改任何代码能力（实测数字仍是 v0.1.0 那一批）。

### 新增

- README 视觉资产：`docs/assets/hero.png`（首图）、`docs/assets/benchmark.png`
  （能力对比图，配 `scripts/make_benchmark_chart.py` 可复现）、
  `docs/assets/social-preview.png`（只用于 GitHub 仓库 Settings → Social preview，不进正文）。
- README 新增「层 → 包」映射表，mermaid 六层图与旧 ASCII 图合并为一节。

### 修复

- README 里两个不存在的图片引用（`docs/assets/cli-help.png`、`docs/assets/cli-real.png`）
  在 GitHub 上显示破图 → 删除，改为 `minicode --help`、`minicode action --fake --out runs`、
  真机 `minicode action --target "开始"` 三段本机真实终端输出（含落点坐标、回读偏差、
  落点复验、channel、model_calls）。
- README 有两个同名「架构」节 → 合并为一节（mermaid 图 → 层级表 → 三条关键设计），
  旧的 ASCII 层级图由新增的「层 → 包」映射表取代，两个 ADR 链接保留。
- README 里写死的版本号 v0.1.0 更新为 v0.1.1。

## v0.1.0 — 2026-10-26

首个可用版本：把内部验证过的原型整理成可安装、可配置、可评测的公开项目。

### 新增

- **模型客户端支持两种协议**：
  - `anthropic` —— Anthropic Messages 协议（原有实现，保持兼容）；
  - `openai` —— OpenAI 兼容 `/chat/completions`，覆盖 OpenAI、DeepSeek、Moonshot、智谱、硅基流动、
    DashScope compatible-mode、Ollama、vLLM、LM Studio、OpenRouter、OneAPI 等。
  - 请求体形状、响应解析（含跳过 `reasoning_content`）、用量字段、图像 token 反查都按协议分支；
    图像 token 算不出来时如实返回 `None`，不猜。
- **配置键**：`MINICODE_PROVIDER` / `MINICODE_BASE_URL` / `MINICODE_API_KEY` / `MINICODE_MODEL`；
  旧键 `MINICODE_ANTHROPIC_BASE_URL` / `MINICODE_ANTHROPIC_API_KEY` 继续可用（新键优先）。
  启动时在 stderr 打印实际生效的协议、端点与键来源，`api_key` 脱敏。
- **命令行总入口** `minicode`：capability / calib / ground / action / eval / e2e / serve / mcp。
- 对外文档：[README](README.md)、[ARCHITECTURE](ARCHITECTURE.md)、MIT [LICENSE](LICENSE)、
  [.env.example](.env.example)。

### 实测能力（v0.1.0 口径）

- ScreenSpot-Pro 183 条抽样：**59.02%**（108/183），Wilson 95% CI [51.78%, 65.89%]；
  其中文本类 69.79%（67/96）、图标类 47.13%（41/87）。
- 真机端到端：wave1 **3/5**（60%）、wave2 **3/9**（33.3%）。
- UIA 通道控件树唯一命中时：落点误差 **0.0 px**、模型调用 **0 次**。
- 视觉通道准入标定：6/6 会话可用、96 px 方块命中率 1.00。

### 变更与移除

- 移除「`base_url` 必须以 `/go` 结尾」这条内部网关限制：端点改由协议决定
  （anthropic → `/v1/messages`，openai → `/chat/completions`）。
- `MINICODE_SESSION_ID` / `MINICODE_HTTP_USER_AGENT` / `MINICODE_HTTP_CLIENT` 由必填改为可选
  （只有网关类端点需要它们，通用 provider 不再被绑死）。
- 移除内部过程文档（调研底稿、内部评审、工单、agent 协作规范）、内部提示词包，及其配套的
  「引用纪律」内部运维测试；保留 8 条 ADR、产品规格与隐私政策。
- 全历史密钥扫描通过：仓库里没有任何真实密钥（示例一律 `<your-key>`）。

## 更早

v0.1.0 之前的 174 个 commit 是内部开发过程，不作为发行版对外。

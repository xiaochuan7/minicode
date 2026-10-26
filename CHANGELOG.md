# Changelog

本项目遵循 [语义化版本](https://semver.org/lang/zh-CN/)。日期为 Asia/Shanghai。

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

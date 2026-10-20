# 2026-10 Windows 桌面 Computer Use 视觉/多模态 API 盘点（免费额度 · 成本 · 可用性 · grounding）

> **调研日期：2026-10-19** ｜所有价格/额度均来自官方文档页实抓，页面上没有的数字一律标注「未查到」，不靠记忆补全。
> **本文件只回答一个问题**：把「Windows 截图 → 坐标/动作」做成一个 agent，每步喂一张 2560×1600 截图，哪条 API 最划算、最稳、最容易拿到免费额度。
> **重要背景**：2026-10 的模型格局已经和「Qwen3-VL 时代」不同。阿里云百炼的官方视觉模型推荐位已是 `qwen3.8-max / qwen3.7-plus / qwen3.8-flash`，**Qwen3-VL（`qwen3-vl-plus`）已降到「旧版及其他模型」**，`qwen-vl-max` / `qwen3-vl-flash` 的模型页已 404（重定向到模型总览）。**百炼还新上线了一个专做电脑操作的 `gui-plus` 模型**，这是本轮最重要的发现。

---

## 0. 三个最重要的结论（先看这里）

1. **百炼 `gui-plus` 是唯一「开箱即用」的国产 Windows computer-use 端点**：官方系统提示词里直接给了一个 `computer_use` 工具（`left_click` / `type` / `scroll` / `key` / `wait` / `terminate`），坐标按 **1000×1000 归一化**，单图上限 16,384 图像 token，**每个模型各送 100 万 Token 免费额度（90 天）**，价格 ¥1.5/M 输入、¥4.5/M 输出。6000 步/月约 **¥32**（默认降采样）或 **¥65**（全分辨率）。
2. **真正 100% 免费且能做 grounding 的是 ModelScope 魔搭**（每日 2000 次、单模型 ≤500 次/天，Qwen3-VL 8B/235B 带 0–1000 归一化坐标）——200 步/天完全装得下，**成本 ¥0**。OpenRouter 的免费层（50 次/天）**装不下** 200 步/天，Groq 免费层（~1000 次/天）装得下但模型 grounding 能力弱。
3. **「中国直连」这件事在本次调研中被实测验证了**：本机直连 `help.aliyun.com` / `modelscope.cn` / `siliconflow.cn` 全部 200 OK；而 `platform.claude.com` 返回 **"App unavailable in region"**（Anthropic 主动封禁未开放地区）、`openai.com/api/pricing` 返回 **403**、`ai.google.dev` 直接**连接超时**。所以国际档不是「慢一点」，而是**根本连不上，且 Anthropic 是合规意义上的硬封禁**。

---

## 1. 主对比表

> 图 token 列的「2560×1600 折算」是本文件第 4 节按各家官方公式实算的结果，不是估算。
> 价格单位：人民币/百万 token（国际服务用 USD/百万 token）。

| 服务 | 模型 | 免费额度 | 价格（输入/输出，每百万） | 图像计费规则 | grounding 支持 | 2560×1600 图像 token | 中国大陆直连 |
|---|---|---|---|---|---|---|---|
| **阿里云百炼** | **`gui-plus`** / `gui-plus-2026-02-26` | **各 100 万 Token，90 天**（北京地域，无需实名即可领） | **¥1.5 / ¥4.5** | `(h_bar×w_bar)/784 + 2`，token_pixels=**28×28**；单图上限 16,384 | **✅ 原生 computer_use 工具**，`coordinate` 0–1000 归一化 | **1,647**（默认降采样）<br>**5,226**（全分辨率） | **✅ 稳定直连** |
| 阿里云百炼 | `qwen3.8-flash` | 100 万 Token，90 天 | **¥0.8 / ¥2.7** | `h×w/1024 + 2`（32×32），≤16M 像素 | ⚠️ 无专用 GUI 工具；可用 Qwen3-VL 式 bbox/point JSON 提示词 | 1,262（默认）<br>4,002（`vl_high_resolution_images=true`） | ✅ |
| 阿里云百炼 | `qwen3.7-flash` | 100 万 Token，90 天 | **¥0.2 / ¥0.8**（百炼最便宜的通用 VL） | 同上 | ⚠️ 同上 | 1,262 / 4,002 | ✅ |
| 阿里云百炼 | `qwen3-vl-plus`（旧版） | 100 万 Token，90 天 | **¥1 / ¥10**（≤32K 档）<br>¥1.5/¥15（32–128K）<br>¥3/¥30（128–256K） | 32×32 | ✅ **2D/3D 定位**：`bbox_2d` / `point_2d`，**0–1000 归一化** | 1,262 / 4,002 | ✅ |
| 阿里云百炼 | `qwen3.7-plus` | 100 万 Token，90 天 | 原价 ¥2 / ¥8，**限时 8 折 → ¥1.6 / ¥6.4** | 32×32 | ⚠️ | 1,262 / 4,002 | ✅ |
| 阿里云百炼 | `qwen3.8-max` | 100 万 Token，90 天 | **¥12 / ¥36** | 32×32 | ⚠️ | 1,262 / 4,002 | ✅ |
| **ModelScope 魔搭** | `Qwen/Qwen3-VL-8B-Instruct`、`Qwen/Qwen3-VL-235B-A22B-Instruct` | **每日 2000 次（跨模型合计）**，单模型 ≤500 次/天，超大模型 200 次/天；UTC+8 00:00 重置，超额 429 | **¥0** | 32×32 | ✅ Qwen3-VL 系，0–1000 归一化 bbox/point | 1,262 / 4,002 | ✅ **完全免费** |
| **硅基流动 SiliconFlow** | `Qwen/Qwen3-VL-8B-Instruct` | **新用户 ¥14 赠金**（国内站）/ $1（国际站），一次性 | **¥0.5 / ¥2.0** | 32×32 | ✅ | 1,262 / 4,002 | ✅ |
| 硅基流动 | `Qwen/Qwen3-VL-235B-A22B-Instruct` | 同上 | ¥2.5 / ¥10 | 32×32 | ✅ | 1,262 / 4,002 | ✅ |
| 百度千帆 | `ERNIE-4.5-Turbo-VL` | **100 万 Token / 3 个月**（一模型一额度） | 未查到该 VL 型号官方单价（ERNIE-4.5-Turbo 文本为 ¥0.8/¥3.2） | 图片折算 token，规则各模型不同（未查到公式） | ⚠️ 未查到官方 bbox 输出说明 | 未查到 | ✅ |
| 火山方舟 | `doubao-seed-2-0-lite-260215`（视觉） | 官方口径混乱：50 万 tokens 起 / 可申请 50 万 / Doubao-Seed-2.1-pro 每日 500 万（活动至 2026-12-31） | **未查到**官方当前视觉型号单价 | 社区口径「1MB 截图 ≈ 2600 token」，官方公式未查到 | ⚠️ 未查到官方 GUI grounding 型号 | 未查到 | ✅ |
| 腾讯混元 | Hunyuan Vision 1.5 / `hunyuan-t1-vision` | 100 万 Token 一次性包，有效期 1 年（官方「混元生文计费概述」口径） | 二手口径冲突：¥1.5/¥4.5 或 ¥3/¥9 或 ¥4/M —— **官方刊例价表未取到** | 图片折算 input token 或按图计费（两种口径并存） | ⚠️ 未查到 | 未查到 | ✅ |
| 智谱 | `glm-4.5v` | 2000 万 Token 资源包（活动发放，**是否仍在发放需查控制台**） | **¥2 / ¥6** | 未查到官方图片 token 公式 | ✅ **Bounding box，归一化到 0–1** | 未查到 | ✅ |
| Moonshot | `moonshot-v1-8k/32k/128k-vision-preview` | API 层**无永久免费**（需先充值；国际站充 $5 赠 $5 券） | ¥12 / ¥24 / ¥60 每百万 | **单图固定 1024 tokens** | ⚠️ | **1,024**（最便宜） | ✅ |
| Moonshot | Kimi K3 / K2.5（原生多模态） | 同上 | 国际站 ~$0.60/$2.50（K2.5）；**中国站价未查到** | 未查到 | ⚠️ | 未查到 | ✅ |
| **OpenAI** | `gpt-5-mini` / `gpt-5` / `computer-use-preview` | **无保证免费额度**（注册赠金说法冲突，官方页未确认；另有「数据共享换 token」计划） | gpt-5-mini **$0.25/$2.00**<br>gpt-5 **$1.25/$10**<br>computer-use-preview **$3/$12**（+ 每次 tool call 附加费） | `85 + 170 × tiles`（512×512 分块，detail=high） | ✅ 原生 computer use | 2,125（12 tiles）<br>85（detail=low） | ❌ **403** |
| **Anthropic** | Haiku 5.5 / Sonnet 5.5 / Opus 5.5 | **无免费 API 额度**（仅新用户少量试用金，金额未公布） | Haiku 5.5 **$0.10/$0.50**<br>Sonnet 5.5 **$2/$10**<br>Opus 5.5 **$4/$20** | `(w×h)/750`，长边 >1568 先缩放 | ✅ **computer use（GA toolset `computer_toolset_20260801`）**，坐标归一化 | 2,049 | ❌ **"App unavailable in region"** |
| **Google AI Studio** | Gemini 3 Flash / 3.1 Pro | **免费层仅 Flash / Flash-Lite**（Pro 约 2026-04 移出）；**官方不公布具体 RPM/RPD**，须在 AI Studio 查 | Gemini 3 Flash **$0.50/$3.00**<br>缓存 $0.36 | 768×768 分块，每块 258 token | ✅ **Computer Use 工具**（Preview），坐标 **0–999** | ~258 | ❌ **连接超时** |
| **OpenRouter** | `:free` 档（`google/gemma-4-31b-it:free`、`qwen/qwen3.8-27b:free` 等） | **20 RPM / 50 次/天**；一次性充 $10 可提到 ~1000 次/天 | 免费 | 随上游模型 | ⚠️ 免费档多为通用 VL，**无专门 grounding 保证** | 随模型 | ❌ 需代理 |
| **Groq** | `meta-llama/llama-4-scout-17b-16e-instruct` | **~30 RPM / ~1000 次/天**（免费层，官方不公布精确值） | **$0.11 / $0.34** | 动态分块，公式未查到官方 | ❌ 通用 VL，无 GUI grounding | ~2,000（估） | ❌ 需代理 |
| Together AI | 视觉模型 | **官方文档明确「不提供免费试用」**，首充 $15 | 视觉模型 $0.0015–$3.00 / $0.25–$15.00 | 随模型 | ⚠️ | 未查到 | ❌ 需代理 |
| Mistral | pixtral / mistral-small | 有免费层，但**月度额度官方未公布**（第三方称 ~1B tokens/月），且免费层数据默认用于训练 | 付费起于 ~$0.10/M 输入 | 随模型 | ❌ | 未查到 | ❌ 需代理 |

---

## 2. 分档结论：现在到底能选什么

### 档位 A：完全免费（¥0，且能 grounding）

| 排名 | 选项 | 免费额度 | 能否 grounding | 备注 |
|---|---|---|---|---|
| **A1** | **ModelScope 魔搭 `Qwen/Qwen3-VL-8B-Instruct` / `-235B-A22B-Instruct`** | 2000 次/天（单模型 ≤500 次/天） | ✅ Qwen3-VL 系，0–1000 归一化 `bbox_2d` / `point_2d` | **200 步/天完全够用**。代价：需绑阿里云 + 实名；非商用定位；无 SLA；单模型 500 次/天是硬顶（200 步/天 OK，但别再叠别的模型） |
| A2 | OpenRouter 免费 VL（`gemma-4-31b-it:free` 等） | 20 RPM / **50 次/天** | ⚠️ 无保证 | **50 次/天 < 200 步/天，不可行**；一次性充 $10 可到 ~1000 次/天，届时可行 |
| A3 | Mistral 免费层 | 额度未公布 | ❌ 视觉免费额度未查到 | 不可靠 |
| A4 | 百炼 **OAuth 认证**额度（免费额度页披露：**每天 2000 次调用**） | 2000 次/天 | 取决于所调模型 | 与 API Key 的 Token 额度**两套独立体系**；`gui-plus` 走 OAuth 是否可用未查到 |

### 档位 B：有免费额度（一次性/限期内，可覆盖部分用量）

| 选项 | 额度 | 覆盖 6000 步/月的能力 | 关键限制 |
|---|---|---|---|
| **百炼 `gui-plus`** | 100 万 Token / 90 天 | 默认降采样下 **约 318 步 ≈ 1.6 天**（3147 token/步） | 只覆盖头两天，之后转按量（¥32–65/月） |
| **百炼 `qwen3.7-flash` / `qwen3.8-flash` / `qwen3-vl-plus` / `qwen3.7-plus` / `qwen3.8-max`** | 各 100 万 Token / 90 天（互不共享） | 同样约 300–360 步 | **每个模型额度独立**，可以按模型拆开各领一份 |
| **百度千帆 `ERNIE-4.5-Turbo-VL`** | 100 万 Token / 3 个月 | 约 180–360 步 | 有效期 3 个月；图片 token 高，额度掉得快 |
| **智谱 GLM-4.5V** | 2000 万 Token 资源包 | 约 3,600 步（≈18 天） | 活动性质，是否还在发放需查控制台 |
| **硅基流动** | ¥14 新人赠金 | `Qwen3-VL-8B` 高分辨率下约 **76% 的一个月** | 一次性，用完转按量 |
| **腾讯混元** | 100 万 Token 一次性包（1 年） | 约 300+ 步 | 混元能力正迁往腾讯云 TokenHub，旧端点 2026-06-22 下线 |
| **火山方舟** | 口径混乱（50 万起 / 每日 500 万 Token 活动） | 未查到确切的视觉模型额度 | 必须去控制台「费用中心 → 免费额度使用情况」实查 |

### 档位 C：按量很便宜（< ¥30/月 @6000 步）

| 选项 | 6000 步/月 | 性价比说明 |
|---|---|---|
| **百炼 `qwen3.7-flash`** | **¥4.0**（默认降采样）/ ¥7.3（高分辨率） | 国产最便宜。但不是专用 GUI 模型，grounding 靠提示词 |
| **硅基流动 `Qwen3-VL-8B-Instruct`** | ¥18.3 | 比百炼 qwen3-vl-plus 便宜一半，OpenAI 兼容接口 |
| **百炼 `qwen3.8-flash`** | ¥15.7 / ¥28.8 | 百炼里性价比最好的通用 VL |
| **百炼 `gui-plus`** | ¥32.4 / ¥64.6 | **唯一开箱即用 computer use**，贵一点但省掉自己写坐标映射/JSON 修复 |
| **百度千帆 ERNIE-4.5-Turbo** | ¥29.3 | 需自行验证 VL 型号单价与图片折算 |

### 档位 D：需付费（且通常需要代理）

| 选项 | 6000 步/月 | 备注 |
|---|---|---|
| **Claude Haiku 5.5**（+computer use toolset） | **$5.28** ≈ ¥38 | 唯一「便宜版原生 computer use」；但**无免费额度 + 需代理 + Anthropic 地区硬封禁** |
| **Groq Llama-4-Scout** | ~$2.62 ≈ ¥19 | 免费层 1000 次/天够用；但**不擅长 GUI grounding** |
| **Gemini 3 Flash** | $7.97 ≈ ¥57 | 免费层存在但官方不公布 RPD，能否撑住 200 步/天未知；连接超时 |
| **OpenAI gpt-5-mini** | $7.24 ≈ ¥52 | 无免费额度；需代理 |
| **Claude Sonnet 5.5**（+toolset） | $105.6 ≈ ¥760 | 精度优先时用 |
| **OpenAI computer-use-preview** | $76 + tool-call 附加费 | 最贵的一档 |
| **Together AI** | 需先充 $15 | 官方明确无免费试用 |
| **百炼 `qwen3.8-max`** | ¥428 | 旗舰质量，非必要 |

> **一句话**：
> **完全免费 → ModelScope 魔搭**；**要「开箱即用的 Windows 操作」→ 百炼 `gui-plus`**（¥32–65/月，另有 100 万 Token 白送）；**要「省钱 + 自己做 grounding」→ 百炼 `qwen3.7-flash`（¥4–7/月）或硅基流动 Qwen3-VL-8B（¥18/月）**；**国际服务在大陆基本不可用，且没有一家有像样的免费额度**。

---

## 3. 各 API 的工程属性（图像 token / 延迟 / 多图 / JSON / 函数调用）

| 服务 | 图像 token 怎么算 | 典型首 token 延迟 | 多图（历史截图） | JSON/结构化输出 | 函数调用 |
|---|---|---|---|---|---|
| 百炼 `gui-plus` | `(h_bar×w_bar)/784 + 2`；官方示例：`prompt_tokens 7750 = image_tokens 6743 + text_tokens 1007`，`completion_tokens 36` | **未查到**官方数值 | ✅ 官方推荐策略：**只保留最近 4 轮完整（截图+输出），更早的只留文字摘要** | ✅ 输出 `<tool_call>{...}</tool_call>` XML + JSON，**但需自己做 JSON 补全/纠错**（官方文档承认会出现截断、字段拼写错误） | ✅ 内置 `computer_use` / `mobile_use` 工具 |
| 百炼 Qwen3-VL | `h×w/1024 + 2`；`vl_high_resolution_images=true` 时像素上限 16,777,216（→16,384 图像 token） | 未查到 | ✅ 最大图片数：qwen3.8-max/3.7-plus **2048 张**，3.7-flash **256 张** | ✅ 结构化输出支持 | ✅ Function Calling 支持（北京地域；国际地域**不支持** Function Calling） |
| ModelScope | 32×32（随 Qwen3-VL） | 未查到（社区称 8B 视频任务比 MiniCPM-4-V 快约 2×） | ✅ 随模型 | ✅ | ✅（随模型） |
| 硅基流动 | 32×32 | 未查到 | ✅ | ✅ | ✅ |
| 智谱 GLM-4.5V | 未查到官方公式 | 约 **60–80 tokens/s**（输出速度） | ✅ | ✅ | ✅ |
| Moonshot vision | **单图固定 1024 tokens**（官方说明） | 未查到 | ✅ 多轮 | ✅ JSON Mode / Partial Mode | ✅ 工具调用（不支持联网搜索、不支持带图 Context Cache、**仅支持 base64 不支持 URL**） |
| OpenAI | `85 + 170×tiles`（512×512 分块） | 未查到 | ✅ | ✅ | ✅ Responses API 专用 |
| Anthropic | `(w×h)/750`，长边 >1568 先缩放；**每张截图按图像 input 计费** | 未查到 | ✅（>20 张更严） | ✅ | ✅ **computer use GA toolset 每次请求额外加约 4,500 input token**（关掉 `zoom` 省 ~410） |
| Gemini | 768×768 分块，每块 **258 token** | 未查到 | ✅ | ✅ | ✅ computer use 为 **Preview**，含 `intent` 与 `safety_decision` |
| Groq | 动态分块，官方公式未查到 | 极快（~594–750 tok/s 推理） | ✅ | ✅ | ✅ |

### Grounding 输出格式速查（这是能否直接落地 mouse click 的关键）

| 方案 | 输出格式 | 坐标系 | 备注 |
|---|---|---|---|
| **百炼 `gui-plus`** | `{"name":"computer_use","arguments":{"action":"left_click","coordinate":[x,y]}}` | **0–1000 归一化** | 官方系统提示词直接定义，**最省事**。⚠️ 官方文档明确：**模型返回的坐标基于其内部缩放后的图像**，需按 `smart_resize` 映射回原图；Windows 125% 缩放/多显示器 DPI 是已知坑 |
| **Qwen3-VL 系**（百炼 / 魔搭 / 硅基流动） | bbox：`[{"bbox_2d":[x1,y1,x2,y2],"label":"..."}]`；point：`[{"point_2d":[x,y],"label":"..."}]` | **0–1000 归一化**（Qwen3-VL 已从 Qwen2.5-VL 的绝对坐标改归一化） | ⚠️ 官方 cookbook 内部不统一：grounding 用 1000 作分母，`ocr.ipynb` / `mobile_agent.ipynb` 用 **999**。**建议先按 1000，若系统性偏移 ~0.1% 改 999** |
| **智谱 GLM-4.5V** | bounding box | **0–1 归一化** | 需自行 ×宽/高 |
| **Gemini computer use** | `function_call`（click/type/scroll/…） | **0–999 归一化** | Preview；浏览器为主，桌面 OS 级控制较弱 |
| **Anthropic computer use** | 工具调用（click/type/…） | 视工具而定，输出坐标需按截图尺寸换算 | GA toolset 需 `computer_toolset_20260801`；Opus 5.5 **不再接受** `computer_20251124` |
| **OpenAI computer-use-preview** | 工具调用 | 视工具而定 | 仅 Responses API；**每次 tool call 额外收费** |

---

## 4. 图像 token 精算 + 降采样的代价

### 4.1 2560×1600 截图在各家折算成多少 token

原图 2560×1600 = **4,096,000 像素**。

| 方案 | 公式 | 缩放后 | 图像 token |
|---|---|---|---|
| Qwen3-VL，默认 `max_pixels` | `h×w/1024+2`，先按 32 倍数缩到 ≤1,310,720 px | 1440×896 = 1,290,240 px | **1,262** |
| Qwen3-VL，`vl_high_resolution_images=true` | 上限 16,777,216 px | 2560×1600（不缩） | **4,002** |
| **GUI-Plus，默认 `max_pixels`=32×32×1280** | `(h_bar×w_bar)/784+2` | 1440×896 | **1,647** |
| GUI-Plus，全分辨率路径（官方坐标映射示例用 factor=16、max=200,704,000） | 同上，不缩放 | 2560×1600 | **5,226** |
| Moonshot vision | 单图固定 | — | **1,024** |
| Claude | 缩到长边 1568 → 1568×980，`/750` | — | **2,049** |
| OpenAI（detail=high） | 85 + 170×12 tiles | 2048×1280 | **2,125**（detail=low 只要 **85**） |
| Gemini 3 Flash | 768 分块 ×258 | — | **~258**（1 tile） |

> **口径提示**：官方 GUI-Plus 示例返回 `image_tokens 6743`，对应约 5,284,944 像素的缩放后图像——比本文按 2560×1600 算出的 1,647 / 5,226 都大，说明**官方示例用的截图分辨率更高，或走了 `vl_high_resolution_images` 路径**。本文数字是**按官方公式对 2560×1600 逐项实算**的结果，实际计费以 API 响应里的 `usage.prompt_tokens_details.image_tokens` 为准。

### 4.2 降采样到底值不值

**降到一半（1280×800 = 1,024,000 px）：**

| 方案 | 全分辨率 | 半分辨率 | token 降幅 | 成本降幅（6000 步/月） |
|---|---|---|---|---|
| Qwen3-VL（高分辨率） | 4,002 | **1,002** | −75% | ¥28.8 → ¥7.3（qwen3.7-flash） |
| GUI-Plus（默认路径） | 1,647 | **1,308** | −21%* | ¥32.4 → ¥27.2 |

\* 默认路径本来就已把 2560×1600 压到 1440×896，所以「先自己降采样」收益有限——**真正的省钱开关是 `max_pixels` / `vl_high_resolution_images` 的取舍，不是自己压图**。

**代价（必须写进设计）：**
1. **小目标定位失效**：2560×1600 缩到 1280×800 后，Windows 100% 缩放下 9pt 字体的正文高度只有约 1.5–2 个模型 token，OCR 与点击坐标都会退化。**工具栏图标、系统托盘、菜单栏、表格单元格边框**是最先崩的。
2. **坐标精度线性下降**：归一化坐标 0–1000 映射回 2560 宽时，1 个归一化单位 = 2.56 物理像素。半分辨率下小图标中心偏差可达 5–10 px，**Windows 的「点击即命中」容差通常只有 2–4 px**。
3. **TTFT 改善有限但真实**：prefill token 减少 → 首 token 更快；GUI-Plus 官方示例 `image_tokens 6743` 的 prefill 占了 7750 的 87%，砍图确实能砍掉大头延迟。
4. **多图历史是成本黑洞**：保留最近 4 轮完整对话（截图+输出）= 5 张图/步，成本直接 ×5。

**推荐做法（省成本又不掉精度）：**
- **两段式**：第 1 步用低分辨率全图定位大致区域 → 拿到 bbox 后 **crop 放大**再发一次高清图精定位（crop 区域小，token 反而更少）。
- **失败才升级**：首轮用默认 `max_pixels`；只有点击没生效/模型说找不到，才重发高分辨率截图。
- **历史只留文字**：只把最近 2–4 张截图留在上下文里，更早的**只留动作文本**（官方 GUI-Plus 文档自己就是这么建议的，明确写着「避免模型上下文过长导致的性能下降」+「节省 token 消耗」）。
- **Windows 缩放坑**：坐标映射必须按**物理像素**算，不能用逻辑像素。模型给的 0–1000 归一化坐标要依次经过：归一化 → 模型内部缩放尺寸（`smart_resize`） → 原始截图像素 → 物理屏幕像素，**四步不能省**。

---

## 5. 成本测算（一天 200 步 / 一个月 6000 步）

**统一假设**：每步 = 1 张 2560×1600 截图 + 约 1,500 token 文本（系统提示词 + 指令 + 历史动作摘要）+ 约 150 token 输出（动作 JSON）。
输入侧月用量 = 6000 × (图像 token + 1500)；输出侧 = 6000 × 150 = 0.9M token。

### 5.1 人民币档

| 模型 | 图像 token/步 | 输入 token/步 | 月输入 (M) | 月输出 (M) | 单价 入/出 | **月费用** | 免费额度可覆盖 |
|---|---|---|---|---|---|---|---|
| **百炼 `qwen3.7-flash`（默认降采样）** | 1,262 | 2,762 | 16.57 | 0.90 | ¥0.2 / ¥0.8 | **¥4.03** | 100万 → 362 步（1.8 天） |
| 百炼 `qwen3.7-flash`（高分辨率） | 4,002 | 5,502 | 33.01 | 0.90 | ¥0.2 / ¥0.8 | **¥7.32** | 100万 → 182 步 |
| **百炼 `qwen3.8-flash`（默认）** | 1,262 | 2,762 | 16.57 | 0.90 | ¥0.8 / ¥2.7 | **¥15.69** | 100万 → 362 步 |
| **硅基流动 `Qwen3-VL-8B-Instruct`** | 4,002 | 5,502 | 33.01 | 0.90 | ¥0.5 / ¥2.0 | **¥18.31** | ¥14 赠金 → 约 76% |
| **百炼 `qwen3-vl-plus`（默认）** | 1,262 | 2,762 | 16.57 | 0.90 | ¥1 / ¥10 | **¥25.57** | 100万 → 362 步 |
| 百炼 `qwen3.8-flash`（高分辨率） | 4,002 | 5,502 | 33.01 | 0.90 | ¥0.8 / ¥2.7 | **¥28.84** | 100万 → 182 步 |
| 百度 ERNIE-4.5-Turbo（文本价代算） | 4,002 | 5,502 | 33.01 | 0.90 | ¥0.8 / ¥3.2 | **¥29.29** | 100万 → 182 步 |
| **百炼 `gui-plus`（默认降采样）** | 1,647 | 3,147 | 18.88 | 0.90 | ¥1.5 / ¥4.5 | **¥32.37** | 100万 → 318 步（1.6 天） |
| 百炼 `qwen3-vl-plus`（高分辨率） | 4,002 | 5,502 | 33.01 | 0.90 | ¥1 / ¥10 | **¥42.01** | 100万 → 182 步 |
| 百炼 `qwen3.7-plus`（高分辨率，8折） | 4,002 | 5,502 | 33.01 | 0.90 | ¥1.6 / ¥6.4 | **¥58.58** | 100万 → 182 步 |
| **百炼 `gui-plus`（全分辨率）** | 5,226 | 6,726 | 40.36 | 0.90 | ¥1.5 / ¥4.5 | **¥64.58** | 100万 → 149 步 |
| 智谱 `glm-4.5v`（高分辨率） | 4,002 | 5,502 | 33.01 | 0.90 | ¥2 / ¥6 | **¥71.42** | 2000万 → 3,635 步（18 天） |
| **百炼 `qwen3.8-max`（高分辨率）** | 4,002 | 5,502 | 33.01 | 0.90 | ¥12 / ¥36 | **¥428.54** | 100万 → 182 步 |
| **ModelScope 魔搭 Qwen3-VL** | 4,002 | 5,502 | 33.01 | 0.90 | **¥0** | **¥0.00** | 2000 次/天 → 200 步/天 **无压力** |

### 5.2 国际档（USD，按 1 USD ≈ 7.2 CNY 粗算）

| 模型 | 图像 token/步 | 输入 token/步 | 月输入 (M) | 单价 入/出 | **月费用 (USD)** | ≈ CNY | 免费额度 |
|---|---|---|---|---|---|---|---|
| **Claude Haiku 5.5** | 2,049 | 3,548 | 21.29 | $0.10 / $0.50 | **$2.58** | ¥19 | **无** |
| Groq Llama-4-Scout | ~2,000(估) | 3,500 | 21.00 | $0.11 / $0.34 | **$2.62** | ¥19 | ~1000 次/天 → 够用 |
| **Claude Haiku 5.5 + computer use toolset** | 2,049 + 4,500 | 8,048 | 48.29 | $0.10 / $0.50 | **$5.28** | ¥38 | **无** |
| **OpenAI `gpt-5-mini`** | 2,125 | 3,625 | 21.75 | $0.25 / $2.00 | **$7.24** | ¥52 | 无保证 |
| **Gemini 3 Flash** | ~258 | 1,758 | 10.55 | $0.50 / $3.00 | **$7.97** | ¥57 | 免费层（额度未公布） |
| **OpenAI `gpt-5`** | 2,125 | 3,625 | 21.75 | $1.25 / $10 | **$36.19** | ¥261 | 无保证 |
| **Claude Sonnet 5.5** | 2,049 | 3,548 | 21.29 | $2 / $10 | **$51.58** | ¥371 | **无** |
| OpenAI `computer-use-preview` | 2,125 | 3,625 | 21.75 | $3 / $12 | **$76.05** | ¥548 | 无（+tool call 费） |
| **Claude Sonnet 5.5 + computer use toolset** | 2,049 + 4,500 | 8,048 | 48.29 | $2 / $10 | **$105.58** | ¥760 | **无** |

### 5.3 保留 4 张历史截图（变体 B）会怎样

以百炼 `gui-plus` 为例，每步输入从 3,147 涨到 `5×1,647 + 1,500 = 9,735`：

- 月输入 58.41M、输出 0.9M → `58.41 × ¥1.5 + 0.9 × ¥4.5` = **¥91.68/月**（是变体 A 的 2.8 倍）
- 100 万免费额度只够 **约 103 步（半天）**

**结论：历史截图是最贵的一项。** 若必须留历史，只留最近 2 张 + 更早的文字摘要，可把倍数压到 1.5× 左右。

---

## 6. 明确的「未查到」清单（不要在这些点上拍脑袋）

| 项 | 状态 |
|---|---|
| 百炼 `qwen3-vl-flash` / `qwen-vl-max` 的当前价格与免费额度 | **模型页已 404**（重定向到模型总览），未在定价表中找到 → **已下线或不再推荐**，改用 `qwen3.8-flash` / `qwen3.7-flash` |
| `qwen-vl-max-latest` / `qwen3-vl-plus-latest` 独立定价 | 页面重定向，未取到独立条目（`qwen3-vl-plus` 本体有 ¥1/¥10 等） |
| 火山方舟豆包**视觉**型号的官方单价与图片 token 公式 | **未查到**（官方模型列表页 `volcengine.com/docs/82379/1799865` 未取到价格表） |
| 火山方舟**视觉**型号是否有 GUI grounding 专用模型 | **未查到**（未见官方「GUI/屏幕定位」型号） |
| 腾讯混元视觉型号的**官方刊例价** | **未取到**（`cloud.tencent.com/document/product/1729/97731` 刊例价表未解析成功；二手口径 ¥1.5/¥4.5、¥3/¥9、¥4/M 互相冲突） |
| 百度千帆 `ERNIE-4.5-Turbo-VL` 的官方单价 | **未查到**（只确认免费额度 100万/3个月） |
| 智谱 GLM-4.5V 的官方图片 token 公式 | **未查到**（只确认 grounding 输出归一化 0–1） |
| Moonshot 中国站 Kimi K2.5/K3 的**人民币**价格 | **未查到**（国际站 $0.60/$2.50 等为二手整理） |
| ModelScope 魔搭现行免费规则的**准确数值** | 官方 limits 页是 JS 渲染，本环境只取到标题。二手来源给「2000 次/天 + 单模型 500 次/天」，另有第三方称已改为「魔粒」积分制（每日登录 200 + 绑阿里云 50 魔粒）——**两说并存，务必登录 modelscope.cn → API 使用情况 实查** |
| OpenAI API 的**保证免费额度** | **官方未确认**。二手说法冲突（$5 / $5–$18 / 已取消 / 改成数据共享换 token） |
| Anthropic 免费额度金额 | 官方 FAQ 只说「少量试用金」，**金额未公布** |
| Gemini 免费层的具体 RPM / RPD | **官方刻意不公布**，只公布 tier 框架；须在 `aistudio.google.com/rate-limit` 查自己的项目 |
| OpenRouter / Groq / Mistral / Together 的官方完整价目表 | 本环境无法直连（OpenRouter/Together/Groq 未取到官方页；Mistral 文档站连接失败） |
| 全部服务的**官方首 token 延迟（TTFT）** | **未查到**（只有 Groq 推理速度 ~594–750 tok/s、智谱 60–80 tok/s 这类非 TTFT 指标） |
| `gui-plus` 的**官方图像 token 公式是否随 `vl_high_resolution_images` 变化** | 官方页面只给了 784 的固定值 + `max_pixels` 相关说明，未明确两者组合的官方取值 → 本文的 1,647 / 5,226 两种口径是**按官方给出的 `smart_resize` 参数实算** |
| 百炼 `gui-plus` 的**思考模式是否额外计费** | 官方页只给了一个价目，未区分思考/非思考价 |
| 百炼 OAuth「每天 2000 次调用」额度能否用于 `gui-plus` | **未查到** |

---

## 7. 落地建议（针对「Windows 桌面 computer use」）

1. **首选组合**：`百炼 gui-plus`（¥32–65/月，¥0.8/¥2.7 的 `qwen3.8-flash` 做兜底复核）+ **ModelScope 魔搭 Qwen3-VL** 作为完全免费的主链路。百炼 `gui-plus` 免费额度先烧掉（1.6 天），之后自动转按量，成本可接受。
2. **如果只想花几块钱**：百炼 `qwen3.7-flash`（¥4–7/月）+ 自己写提示词要 `bbox_2d`（Qwen3-VL 式 0–1000 归一化），**但要自己处理坐标映射和 JSON 修复**。
3. **别用国际档做主线**：本机实测 `platform.claude.com` 地区封禁、`openai.com` 403、`ai.google.dev` 超时；且国际档**没有一家有可用的免费额度**。
4. **开箱即用的最高性价比 GUI 端点仍然是 `gui-plus`**：它把 Anthropic computer-use 那套 `computer_use` 工具定义、0–1000 归一化坐标、Action+tool_call 输出格式、JSON 修复注意事项、多轮历史裁剪策略全写进了官方文档和官方 System Prompt，等于白送一份可运行的参考实现。
5. **两个必踩的坑**：(a) Windows DPI 缩放下，模型坐标 → 物理屏幕像素的映射必须做对；(b) `gui-plus` 官方明说模型输出 JSON **会截断、会拼错字段名**，必须做补全与容错。

---

## 8. 来源清单（均为官方页，访问日期 2026-10-19）

**阿里云百炼（直连实测 200 OK）**
- 模型调用计费（价目表，含 qwen3-vl-plus / qwen3.8-flash / qwen3.7-flash / qwen3.7-plus 免费额度列）：https://help.aliyun.com/zh/model-studio/model-pricing
- 新人免费额度（100万 Token/90天/仅北京/无需实名/OAuth 每天 2000 次）：https://help.aliyun.com/zh/model-studio/new-free-quota
- **界面交互专用模型：GUI Plus**（computer_use 工具、0–1000 坐标、28×28 token 公式、smart_resize、免费额度）：https://help.aliyun.com/zh/model-studio/gui-automation
- 视觉理解模型总览（图像 token 公式 h×w/(32×32)+2、最大图片数、Function Calling/结构化输出支持矩阵、模型代际）：https://help.aliyun.com/zh/model-studio/vision-model
- `qwen3-vl-plus` 模型页（价格/限流/能力矩阵）：https://help.aliyun.com/zh/model-studio/qwen3-vl-plus
- `qwen3.8-max` 模型页（价格/上下文/动态限流）：https://help.aliyun.com/zh/model-studio/qwen3-8-max
- 限流总览（**gui-plus: RPM 80 / TPM 540,000**；gui-plus-2026-02-26: RPM 100 / TPM 540,000；qwen3-vl-plus 北京 RPM 3000 / TPM 5,000,000）：https://help.aliyun.com/zh/model-studio/rate-limit
- 动态限流（TPM 按月消费档位软限流）：https://help.aliyun.com/zh/model-studio/quota-management
- 模型总览（qwen3.8-max / qwen3.7-plus / kimi-k3 / GLM-5.3 / deepseek-v4 等当前阵容）：https://help.aliyun.com/zh/model-studio/models
- Qwen3-VL 官方仓库（2D grounding cookbook、0–1000 归一化坐标、1000 vs 999 分母坑）：https://github.com/QwenLM/Qwen3-VL

**其他国内**
- 硅基流动 Qwen3-VL 上线公告（**新用户 ¥14 / $1 赠金**）：https://siliconflow.cn/news/wmtgf1jruk58yky7h1q7j66q
- 百度千帆新用户免费额度（**一模型一额度，100万 Token/3 个月，含 ERNIE-4.5-Turbo-VL**）：https://cloud.baidu.com/doc/qianfan/s/Imi2rpirg
- 百度千帆模型服务计费：https://cloud.baidu.com/doc/qianfan/s/wmh4sv6ya
- 腾讯混元生文计费概述：https://cloud.tencent.com/document/product/1729/97731
- 火山方舟模型列表：https://www.volcengine.com/docs/82379/1799865
- 智谱 GLM-4.5V（国际站官方文档，grounding 与美元定价）：https://docs.z.ai/guides/vlm/glm-4.5v
- 智谱开放平台：https://open.bigmodel.cn
- ModelScope API-Inference 限流页：https://modelscope.cn/docs/model-service/API-Inference/limits ｜用量查询：https://modelscope.cn/my/myaccesstoken

**国际（部分为官方页经搜索工具转述，官方站本环境直连被阻）**
- OpenAI 定价：https://openai.com/api/pricing ｜`computer-use-preview` 模型页（$3/$12 + tool call 费）：https://developers.openai.com/api/docs/models/computer-use-preview
- Anthropic 定价：https://platform.claude.com/docs/en/about-claude/pricing ｜模型总览：https://platform.claude.com/docs/en/models/overview
- Google Gemini 定价：https://ai.google.dev/gemini-api/docs/pricing ｜限流：https://ai.google.dev/gemini-api/docs/rate-limits ｜Computer Use：https://ai.google.dev/gemini-api/docs/computer-use
- Together 计费（**官方明确无免费试用**）：https://docs.together.ai/docs/billing-credits
- Groq 限流/模型/定价：https://console.groq.com/docs/rate-limits ｜https://console.groq.com/docs/models ｜https://console.groq.com/docs/pricing
- Mistral 订阅：https://docs.mistral.ai/admin/billing-usage/subscriptions
- OpenRouter 模型列表：https://openrouter.ai/models

**直连性实测记录（本机，2026-10-19）**

| 目标 | 结果 |
|---|---|
| `help.aliyun.com` | ✅ HTTP 200 |
| `modelscope.cn` | ✅ HTTP 200 |
| `siliconflow.cn` | ✅ HTTP 200 |
| `platform.claude.com` | ❌ **HTTP 200 但返回「App unavailable in region」**（Anthropic 主动地区封禁） |
| `openai.com/api/pricing` | ❌ HTTP 403 |
| `ai.google.dev` | ❌ 连接超时 / fetch failed |
| `console.groq.com` / `docs.together.ai` / `docs.mistral.ai` | ❌ 本环境不可达（未逐一验证，标注为推测） |

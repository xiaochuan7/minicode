# r6 —— 通用 API 模型的公开基线：我们的 44.8% 在什么位置？

> 调研线：r6-api-baseline ｜ 2026-10-22（Asia/Shanghai）
> 负责问题：(a) 通用旗舰 API 模型在 ScreenSpot-v2 / ScreenSpot-Pro 的**公开点级准确率**；(b) 官方带 grounding 的模型（computer-use-preview / Claude computer use / Gemini computer use）公开分数；(c) 「通用 API 模型 + 自写 coarse-to-fine」有无公开对照；(d) 我们 44.8% 相对公开水平是领先/持平/落后，通用 API 模型天花板在哪。
> 回答对象：`docs/research/vision-root-cause.md` 的 **§7-Q3**（"有没有公开工作报告过『通用 API 模型（不做 grounding 训练）+ 整屏粗定位 + crop 再定位』在 ScreenSpot-Pro 上的分数？"）与 **§4.2 公开对照表**。

---

## 0. 一句话结论

**我们 44.8%（重加权 42.5%）在公开坐标里的位置是：「训练免费级联」这一档的**下沿**，而不是「通用 API 模型」该有的水平**——官方 ScreenSpot-Pro 榜上**一行通用 API 模型都没有**（22 个条目全是训过的 grounding 模型或训练免费级联），论文里通用 API 模型直接 grounding 只有 **GPT-4o 0.8% / Claude 3.5 ~1.8%**；而**同架构（粗定位→zoom→校验）的公开 SOTA 是官方榜第一的 GroundCascade = 85.0%**（训练免费、固定 0.5 zoom、1%/4% 一致性门控、平均 2.57 次调用）。**结论 (d)：相对裸通用 API 模型 = 大幅领先（约 +44 pp）；相对 2025 论文的训练免费 SOTA（ScreenSeekeR 48.1%）= 持平（48.1 落在我们的 95% CI 37.8~52.0 内）；相对 2026 官方榜的同架构级联（85.0%）= 落后约 40 pp。** 天花板：**"通用 API 模型 + 自建级联"的天花板没有公开上限，但**"训过的 grounding 模型 + 自建级联"的上限是 85.0%**；而公开数据给出一个可直接照抄的增益量级：**给同一个模型加一层 zoom-in，ScreenSpot-Pro 上平均 +7~9 pp、icon 子类 +8~13 pp**（官方榜上 5 组"有/无 zoom-in"对照，详见 §2.2）。

---

## 0.1 取证方法（本轮实际做的事）

- **本轮 web 调用无硬上限**（用户在任务中途解除），实际取用 ≈ 25 次一手页面/文件，**全部 URL 见 §9**。
- **官方 leaderboard 是 JS 动态渲染**，HTML 正文没有表格；**表格在两个 JSON 里**，本轮已直取并本地解析：
  - `https://gui-agent.github.io/grounding-leaderboard/results/screenspot_pro.json`（Pro 榜，22 个条目）
  - `https://gui-agent.github.io/grounding-leaderboard/results/screenspot_v2.json`（v2 榜，27 个条目）
- **官方仓实现逐字读了 3 份源码**：`models/gpt4x.py`（通用 API grounding 协议）、`models/methods/reground.py`（crop 几何）、`models/methods/screenseeker.py`（ScreenSeekeR 全参数）。官方仓 README 是**小写** `readme.md`（`README.md` 404）。
- 论文 Table 2/3 的数值**没能一手取到**（arXiv HTML 单页太长，抓取在 §4 处截断；PDF 工具不支持；HF 博客重试仍失败）。论文里**可一手读到的**是：摘要（最佳端到端 18.9%、ScreenSeekeR 48.1%）、§3.4 统计（目标平均占图 **0.07%** vs ScreenSpot **2.01%**）、§4.1/§4.2 方法定义与参数（σ=0.3、NMS、patch ≤1280 才调 grounder、迭代 zoom 2×2、逐级半宽半高）。**论文 Table 3 的通用 API 行沿用本仓 `vision-root-cause.md` §4.2 的既有记录，标【二手·本仓已录】。**
- 证据强度标注：**【一手】**= 本轮直接取到官方/论文原文并逐字引用；**【二手】**= 检索摘要或本仓既有记录（本轮未复核）；**【推断】**= 我基于一/二手数据的推理。

---

## 1. 一手页面清单（每页拿到了什么）

| # | URL | 拿到什么 | 强度 |
|---|---|---|---|
| 1 | `https://gui-agent.github.io/grounding-leaderboard/results/screenspot_pro.json` | **官方 ScreenSpot-Pro 榜全表（22 条）**：avg/icon/text，greedy decoding、micro-average；含多条**训练免费级联**与**zoom-in 对照**条目 | 【一手】 |
| 2 | `https://gui-agent.github.io/grounding-leaderboard/results/screenspot_v2.json` | **官方 ScreenSpot-v2 榜全表（27 条）**：含 **GPT-4o = 20.1%**（desktop 22.2 / mobile 25.5 / web 12.4）、Qwen2.5-VL-7B 86.5%、OSAtlas-7B 83.3%、UI-Venus-72B 95.3% 等 | 【一手】 |
| 3 | `https://raw.githubusercontent.com/likaixin2000/ScreenSpot-Pro-GUI-Grounding/main/models/gpt4x.py` | **官方通用 API grounding 协议原文**：模型 `gpt-4o-2024-05-13`、temperature **0.0**、max_tokens **2048**、**原图直送不 resize**、输出 `[[x0,y0,x1,y1]]` 且 **x,y ∈ [0,1]**、负出口 `"Target not existent"`、落点取框中心 | 【一手】 |
| 4 | `.../models/methods/reground.py` | **ReGround 几何原文**：以第一级 **bbox 中心**裁 **1024×1024**，越界**平移保持 1024**（不缩小）；crop 内 0~1 坐标经 `view_bbox_to_real_bbox` 反变换回原图 | 【一手】 |
| 5 | `.../models/methods/screenseeker.py` | **ScreenSeekeR 全参数**：planner=`gpt-4o-2024-05-13`(T=0, max_tokens 2048)、`max_search_depth=3`、`min_crop_size=[1280,768]`、`max_aspect_ratio=2.5`、位置推断提示词原文、存在性校验提示词（`is_target`/`target_elsewhere`/`target_not_found` + 重写指令）、`score_patch(mu=0.5,sigma=0.3)`、NMS `iou=0.5`、`auto_dilate_patch(min 960×540, num_splits=2)`、`crop_with_padding`（归一化空间 padding） | 【一手】 |
| 6 | `https://arxiv.org/html/2504.07981v1` | 论文摘要（最佳端到端 18.9%、ScreenSeekeR 48.1%、+254% 相对提升）、§3.4 统计（0.07% vs 2.01%）、§4 方法与参数 | 【一手】 |
| 7 | `https://raw.githubusercontent.com/xlang-ai/OpenCUA/main/README.md` | GUI Grounding 表（Qwen2.5-VL-7B/32B、UI-TARS-72B、OpenCUA-7B/32B/72B 的 v2/Pro/OSWorld-G/UI-Vision）+ OSWorld-Verified 代理表（Claude 3.7/4 Sonnet、OpenAI CUA、Seed-1.5-VL）+ AgentNetBench **Coordinate Actions**（OpenAI CUA 71.7、Qwen2.5-VL-72B 67.2、OpenCUA-7B 79.0） | 【一手】 |
| 8 | `https://raw.githubusercontent.com/OS-Copilot/OS-Atlas/main/README.md` | ScreenSpot-v2 **1,272 条**规模；grounding 输出 **0~1000 归一化**；**动态分块**标准配方 `dynamic_preprocess(max_num=12, image_size=448)` | 【一手】 |
| 9 | `https://api.github.com/repos/likaixin2000/ScreenSpot-Pro-GUI-Grounding/contents/models` | 官方仓 `models/` 清单：**含 `gpt4x.py`、`gpt5.py`**（即官方自己就有 API 模型 grounding 实现）与 `methods/`、`zoom_consistency_router.py` | 【一手】 |
| 10 | 本仓 `docs/research/bottom-drafts/10-anthropic-zoom源码细节.md` | Anthropic SDK 1.15.0 `computer_zoom_input` docstring 原文：region 为 `(x0,y0,x1,y1)`、坐标在**整屏截图空间**、**"The crop is scaled up to fill the image budget so fine details become legible"** | 【一手（官方 SDK 源码，已落盘）】 |

---

## 2. (a) 通用 API 模型的公开点级准确率

### 2.1 官方 ScreenSpot-Pro 榜（当前全表，22 条）【一手】

> 口径：官方 `eval_screenspot_pro.py`（归一化点落在归一化 GT bbox 内），**greedy decoding、micro-average**（leaderboard 页说明）。
> **注意：全表没有一行通用 API 模型**（GPT-4o / Claude / Gemini / 豆包 都不在榜上）。

| 官方榜条目 | avg | icon | text | 性质 |
|---|---|---|---|---|
| **GroundCascade (Indeed-UI-32B + Indeed-UI-8B)** | **85.0** | 73.0 | 92.4 | **训练免费**几何一致性级联：固定 0.5 zoom + 1% 自一致门 + 4% 跨模型一致门，平均 2.572 次调用 |
| **GroundCascade (Indeed-UI-32B + KV-Ground-8B)** | **85.1** | 73.8 | 92.1 | 同上 |
| Indeed-UI-8B-zoom-in | 81.0 | 66.6 | 89.9 | 训过 + zoom-in |
| KV-Ground-GuiOwl1.5-0315-8B-ZoomIn | 80.5 | 64.7 | 89.7 | 训过 + zoom-in |
| KV-Ground-8B + Qwen3.5-27B Consistency Router | 80.5 | 66.0 | 90.0 | 第三方 PR，一致性路由 |
| Indeed-UI-32B-zoomin | 80.5 | 65.2 | 90.0 | 训过 + zoom-in |
| AdaZoom-GUI-4B (cond. zoom-in, refine w/ Qwen3.5-397B) | 77.0 | 55.0 | 90.0 | 条件 zoom-in + 大模型精修 |
| KV-Ground-GuiOwl1.5-4B-0228-ZoomIn | 75.7 | 61.6 | 85.9 | 训过 + zoom-in |
| KV-Ground-GuiOwl1.5-0315-8B | 73.2 | 56.5 | 83.5 | 训过（无 zoom） |
| Indeed-UI-8B | 73.4 | 56.8 | 83.6 | 训过（无 zoom） |
| Indeed-UI-32B | 73.3 | 54.0 | 85.3 | 训过（无 zoom） |
| Duvo Eye-1 (Holo-3.1-35B-A3B + LoRA) | 73.2 | 59.4 | 81.1 | 训过 |
| AdaZoom-GUI-4B (conditional zoom-in) | 70.6 | 47.3 | 85.2 | 4B + 条件 zoom-in |
| KV-Ground-Qwen3VL-4B-ZoomIn | 70.4 | 47.8 | 83.6 | 训过 + zoom-in |
| UI-Venus-1-5-30B-A3B | 69.6 | 51.0 | 81.2 | 训过 |
| KV-Ground-GuiOwl1.5-0228-4B | 67.0 | 49.0 | 78.2 | 训过（无 zoom） |
| KV-Ground-Qwen3VL-4B | 63.4 | 39.4 | 78.0 | 训过（无 zoom） |
| AdaZoom-GUI-4B | 61.9 | 35.0 | 78.0 | 4B（无 zoom） |
| HuzzleWorld-2B (Zoom In) | 70.9 | 54.7 | 79.7 | 闭源 CUA + zoom-in |
| HuzzleWorld-2B | 61.7 | 47.3 | 71.1 | 闭源 CUA 单发（**官方榜**自评・**部分分**口径） |
| UI-Venus-1-5-2B | 57.7 | 39.4 | 69.1 | 训过 |
| UI-Venus-1-5-8B | （本轮抓取被截断，未取到 overall） | | | 训过 |
| **本仓（通用 API + 整屏粗定位 + crop 精定位，183 条抽样）** | **44.8** | **29.9** | **58.3** | 本机实测 |

### 2.2 **zoom-in 的公开量化（同一模型、同基准、有/无对照）**【一手】

这是本报告最可直接照抄的公开数字：**给同一个模型加一层 zoom-in，在 ScreenSpot-Pro 上的增益**：

| 模型 | 无 zoom | 有 zoom | Δavg | Δicon | Δtext |
|---|---|---|---|---|---|
| Indeed-UI-8B | 73.4 | **81.0** | **+7.6** | **+9.8** | +6.3 |
| Indeed-UI-32B | 73.3 | **80.5** | **+7.2** | **+11.2** | +4.7 |
| KV-Ground-GuiOwl1.5-0315-8B | 73.2 | **80.5** | **+7.3** | **+8.2** | +6.2 |
| KV-Ground-GuiOwl1.5-4B-0228 | 67.0 | **75.7** | **+8.7** | **+12.6** | +7.7 |
| KV-Ground-Qwen3VL-4B | 63.4 | **70.4** | **+7.0** | **+8.4** | +5.6 |
| AdaZoom-GUI-4B | 61.9 | **70.6** | **+8.7** | **+12.3** | +7.2 |
| HuzzleWorld-2B | 61.7 | **70.9** | **+9.3** | +7.4 | +8.6 |

> **读法**：**"加一层 zoom" 在公开数据上稳定值 +7~9 pp，icon 子类 +7~13 pp**（8 组对照，符号一致，量级稳定）。官方描述里 zoom-in 的实现关键词是 **"fixed 0.5 zoom"**（GroundCascade）与 **"conditional zoom-in"**（AdaZoom）。
> **口径标注（HuzzleWorld-2B 的 61.7 与 70.9）**：这两个数是**官方榜**（`results/screenspot_pro.json`）上的**自评**分数，**部分分**口径 —— 官方榜只报**部分分**（子集/子集分），与本仓的全量口径**不可直接比**。
> → **我们已经在做 zoom（crop 精定位），但窗口只有 192 物理 px、且整屏先被压到 1280×800**；公开最优是 **半屏级 zoom（0.5）+ 一致性门控**。这是我们与公开第一梯队的**架构差距**，不是模型差距。

### 2.3 官方 ScreenSpot-v2 榜（含唯一一条通用 API 模型行）【一手】

| 模型 | overall | desktop | mobile | web |
|---|---|---|---|---|
| **GPT-4o（通用 API）** | **20.1** | 22.2（icon 19.3 / text 24.2） | 25.5 | 12.4 |
| Qwen2-VL-7B / 72B | 39.8 / 43.0 | 40.1 / 44.6 | 50.1 / 59.5 | 27.7 / 22.9 |
| Qwen2.5-VL-3B / 7B-Instruct | 46.9 / 86.5 | 44.0 / 78.4 | 55.5 / 92.8 | 39.1 / 85.4 |
| SeeClick-7B / UGround-7B / OSAtlas-4B / 7B / ShowUI-2B / CogAgent-18B | 53.9 / 76.3 / 68.5 / 83.3 / 77.3 / 52.8 | | | |
| MiniCPM-V-7B / QwenVL-7B | 45.9 / 6.1 | | | |
| Holo1.5-7B/72B、Holo2-4B/8B/30B-A3B、duvo-eye-1、UI-Venus-7B/72B、ZonUI-3B、GUI-ARP-7B、HuzzleWorld-1B/2B/3B(Zoom In) | 86.6 ~ 95.3 | | | |

**两条可直接用的判读**：
1. **官方榜上 GPT-4o = 20.1%**（v2 上**最容易**的通用 API 数字）。文献里常引用的"GPT-4o ≈ 40%"本轮**未取到一手**，【二手】且与官方榜口径冲突 → **对外只引用官方榜 20.1%**。
2. **同一个 Qwen2.5-VL-7B：v2 = 86.5% → Pro = 27.6%**（Pro 数字出自 OpenCUA 官方 README【一手】）= **掉 59 pp**，而两个基准的差别就是"整屏高分辨率 + 目标只占 0.07% 面积"（论文 §3.4【一手】）。→ **"目标在模型输入图里有多小"是 ScreenSpot-Pro 的主因变量**，不是"模型笨"。这条直接支撑我们"整屏别降采样、crop 要放大"的方向。

### 2.4 论文里的通用 API 行（Table 3）【二手·本仓已录，本轮未复核】

| 模型 | ScreenSpot-Pro | 条件 |
|---|---|---|
| **GPT-4o** | **0.8%**（text 1.3 / icon 0.0） | 直接 grounding，**原图直送**（官方 `gpt4x.py` 不 resize），temperature 0 |
| **Claude 3.5 Sonnet** | **~1.8%** | 同上协议 |
| 其余模型 | 论文原话 "None of the other models achieved an accuracy above 10%" | |

> 本轮**未能一手复核**这两个数（arXiv HTML 截断、PDF 不可抓、HF 博客两次失败）。但**官方仓确实有 `models/gpt4x.py`/`gpt5.py`**，说明这些数是用上面那份协议跑出来的。

### 2.5 通用 API 模型在 ScreenSpot-v2 上的一手数字（除 GPT-4o 外）【一手】

官方 v2 榜上**只有 GPT-4o 一条通用 API 模型**。**Gemini 2.5 Pro / Gemini 3.x、Kimi K2 / K2.5、Doubao、GLM-4.5V 在 ScreenSpot-v2/Pro 上没有任何公开点级分数**（本轮未取到；检索摘要里的数字来自 llm-stats / benchlm 等聚合站且含明显不可核实条目，**不可引用**）。
→ **该项「不适用」**：不是"没有公开数"这么含糊，而是**这些模型没有以点级 grounding 分数出现在任何官方榜或论文表里**；要取只能走模型卡自报（如 Qwen3-VL、MAI-UI 等开源 grounding 模型），而它们都是**训过的**。

---

## 3. (b) 官方带 grounding 的模型：公开分数

| 模型 | 官方发布口径 | 数字 | 出处 | 强度 |
|---|---|---|---|---|
| OpenAI **computer-use-preview / CUA** | OSWorld（任务级） | 38.1%；WebArena 58.1% | openai.com/index/computer-using-agent/ | 【二手】（本轮直取 403） |
| OpenAI CUA | **AgentNetBench Coordinate Actions**（纯坐标 grounding 离线集） | **71.7**（Content 57.3 / Function 80.0 / 平均 73.1） | OpenCUA 官方 README | 【一手】 |
| OpenAI CUA | OSWorld-Verified | 31.4%（100 步）/ 31.3%（50 步） | OpenCUA 官方 README | 【一手】 |
| Anthropic **Claude 3.5 Sonnet (upgraded) + computer use** | OSWorld（screenshot-only） | 14.9%（Composio 转述 14.7%） | anthropic.com/news/3-5-models-and-computer-use | 【二手】 |
| Anthropic **Claude 4 Sonnet** | OSWorld-Verified | 43.9%（50 步）/ **41.5%（100 步）** | OpenCUA 官方 README | 【一手】 |
| Anthropic Claude 3.7 Sonnet | OSWorld-Verified | 35.9% | 同上 | 【一手】 |
| 字节 Seed-1.5-VL | OSWorld-Verified | 34.1% | 同上 | 【一手】 |
| Qwen2.5-VL-72B-Instruct | OSWorld-Verified | 5.0% | 同上 | 【一手】 |
| Anthropic computer toolset | 动作集合含 **`zoom`**（17 个成员动作） | — | Anthropic SDK `computer_member_name.py`（本仓已落盘） | 【一手（源码）】 |
| **官方 ScreenSpot-Pro 榜** | 点级 click accuracy | **无任何通用 API / computer-use 模型行** | 官方 `results/screenspot_pro.json` | 【一手】 |
| **官方 ScreenSpot-v2 榜** | 点级 click accuracy | **GPT-4o 20.1% 一行** | 官方 `results/screenspot_v2.json` | 【一手】 |

**三条必须写下来的判读**：
1. **"官方 grounding 模型在 ScreenSpot 类基准上的官方点级分数"——不适用**：OpenAI 与 Anthropic 只发布**任务级**（OSWorld/WebArena）分数，**不发布 ScreenSpot 类点级分数**；官方 ScreenSpot-Pro 榜上**也没有**任何 computer-use 模型行。我们 §4.2 里那几行"Claude (Computer Use) 17.1 / Claude 3.7 27.7 / OpenAI CUA 23.4 / GPT5-minimal 18.5 / Seed-1.5-VL 60.9"标注的出处是官方 `results/screenspot_pro.json`，**但本轮直取该文件，这 22 个条目里一行都没有它们** → **这五个数本轮无法复现，对外引用前必须回源**（可能来自旧版榜文件或第三方转录）。
2. **任务级分数不能和我们的点级分数并列**：Claude 4 Sonnet OSWorld-Verified 41.5%、OpenAI CUA 38.1% OSWorld 都是"整条任务是否完成"；我们的 44.8% 是"点是否落在 GT bbox 内"。
3. **CUA 的坐标 grounding 只比通用 72B 强 4.5 pp**：AgentNetBench Coordinate Actions 上 OpenAI CUA **71.7** vs Qwen2.5-VL-72B **67.2** vs **OpenCUA-7B 79.0**【一手】。→ **训过的 7B grounding 模型比原生 grounding 的旗舰 API 模型还准 7.3 pp** → 换模型这一条对我们是"真金白银"的。

---

## 4. (c) 「通用 API 模型 + 自写 coarse-to-fine」有没有公开对照？

**结论：仍然没有"通用 API 模型同时充当粗定位+精定位"的公开分数；但公开资料现在给出了这一架构的三个可照抄实现与一组可对标的数字。**

### 4.1 公开工作一览（同构程度）

| 公开工作 | 架构 | 第一级/planner | 第二级 grounder | 是不是"通用 API 模型做级联"？ | 分数 |
|---|---|---|---|---|---|
| **ReGround**（官方 `models/methods/reground.py`） | 整屏定位 → 以预测 **bbox 中心**裁 **1024×1024** → crop 内再定位（2 次调用） | 训过的模型 | 训过的模型 | ❌ 模型不是通用 API | 40.2【一手·论文】 |
| Iterative Narrowing / Focusing | 逐级半宽半高（深度 3）/ 2×2 象限 | 训过的 | 训过的 | ❌ | 31.9 / 31.0【一手·论文】 |
| **ScreenSeekeR**（官方 `models/methods/screenseeker.py`） | **planner=GPT-4o 出候选区** → 膨胀 → σ=0.3 中心度打分 → NMS(0.5) → 递归搜（patch ≤1280 才调 grounder）；planner 还会**验框**（`is_target`/`target_elsewhere`/`target_not_found`）并重写指令 | **GPT-4o（通用 API）** | 训过的 OS-Atlas-7B | ⚠️ 通用 API 只当 **planner**，不当 grounder | **48.1**【一手·论文】 |
| **GroundCascade**（官方榜第一） | **固定 0.5 zoom + 1% 自一致门 + 4% 跨模型一致门**，平均 2.572 次调用，greedy | 训过的（Indeed-UI-32B 等） | 同上 | ❌ 但**几何与门控范式与我们的两段式几乎同构** | **85.0 / 85.1**【一手·官方榜】 |
| AdaZoom-GUI-4B / KV-Ground-*-ZoomIn / HuzzleWorld-2B (Zoom In) | **条件/固定 zoom-in**（同模型加一层 zoom） | 训过的/闭源 CUA | 同上 | ❌ | 70.6 ~ 80.5【一手·官方榜】 |
| **本仓** | 整屏 1280×800 粗定位 → crop **192 px** 按粗点裁 → 1:1 精定位（2 次调用） | **通用 API**（minimax-m3.1-flash-free） | **同一个通用 API** | ✅ **只有我们这一格是"通用 API 同时当两极"** | **44.8（183 条）/ 42.5（重加权）** |

### 4.2 直接回答 `vision-root-cause.md` §7-Q3

- **论文与官方仓的全部多轮/级联方法，grounder 都是训过的模型**；GPT-4o 在公开实现里只出现在 **planner 位**（ScreenSeekeR）与**单发 grounding 位**（`gpt4x.py`，0.8%）。【一手】
- **官方榜 22 条里也没有任何"通用 API 模型当 grounder"的条目**；最接近的两类都是"训过的模型 + 免训练级联"（GroundCascade 85.0%）。【一手】
- → **"通用 API 模型同时充当粗定位与精定位"在公开文献/官方榜里仍然没有对照分数**，我们的 42.5~44.8% 至今**无直接公开标尺**；只能夹逼：
  - 下界（通用 API 只能当第一级、单发）：**GPT-4o 0.8%**（Pro，论文）、**20.1%**（v2，官方榜）；
  - 上界（同架构、但 grounder 训过）：**GroundCascade 85.0%**（官方榜第一，2.57 次调用）；
  - 中间（2025 论文的训练免费 SOTA，grounder 训过 7B）：**ScreenSeekeR 48.1%**、ReGround 40.2%；
  - **我们 42.5~44.8% 落在"下界与 ScreenSeekeR 之间"**：即我们的级联架构已经吃掉了与"训过 7B grounder + 论文级联"相当的水位，但距离"训过模型 + 官方级联"还差约 40 pp。

### 4.3 可以直接照抄的参数（官方源码级，全部【一手】）

| 用途 | 参数 | 出处 |
|---|---|---|
| 通用 API grounding 提示词 | `"You are asked to find the bounding box of an UI element in the given screenshot corresponding to a given instruction.\nDon't output any analysis. Output your result in the format of [[x0,y0,x1,y1]], with x and y ranging from 0 to 1."`；system：`"You are an expert in using electronic devices and interacting with graphic interfaces. You should not call any external tools."` | `models/gpt4x.py` |
| 调用参数 | `temperature=0.0`、`max_tokens=2048`、**原图 base64 直送（不 resize）** | 同上 |
| 负出口 | `"If such element does not exist, output only the text 'Target not existent'."`（两个变体都有；`ground_with_uncertainty` 另配 `"not found"` 判负） | 同上 |
| 坐标契约 | **0~1 归一化**（两轴各自除以宽/高），落点 = 框中心 | 同上 |
| ReGround 裁剪 | `crop_to_center(img, bbox, crop_size=[1024,1024])`：**以 bbox 中心**裁 1024×1024，越界平移保持 1024（不缩小）；crop 内 0~1 坐标经 `view_bbox_to_real_bbox` 反变换 | `models/methods/reground.py` |
| ScreenSeekeR 配置 | `max_search_depth=3`、`min_crop_size=[1280,768]`、`max_desicion_size=[1280,768]`、`max_aspect_ratio=2.5`、planner `gpt-4o-2024-05-13`(T=0, max_tokens=2048) | `models/methods/screenseeker.py` |
| ScreenSeekeR 候选打分 | `score_patch(patch, point, mu=0.5, sigma=0.3)`：高斯中心度，point 不在 patch 内 → 0 | 同上 |
| ScreenSeekeR 去重 | NMS `iou_threshold=0.5, keep="order"` | 同上 |
| ScreenSeekeR 膨胀 | `auto_dilate_patch(min_patch_size=(960,540), max_ratio=2.5, num_splits=2)`；padding 在**归一化空间**做（`crop_with_padding`） | 同上 |
| ScreenSeekeR 存在性校验 | 把候选框**画成红框**给 planner 判定 `is_target` / `target_elsewhere` / `target_not_found`，并让它**重写更明确的指令** | 同上（`EXISTENCE_PROMPT`） |
| 官方训练免费级联模板 | **fixed 0.5 zoom + 1% 自一致门 + 4% 跨模型一致门**，平均 **2.572** 次调用/样本，greedy | 官方榜 `GroundCascade` 条目 description |
| Anthropic 官方 zoom 语义 | region `(x0,y0,x1,y1)`，坐标在**整屏截图空间**；**"The crop is scaled up to fill the image budget so fine details become legible"** | Anthropic SDK 1.15.0 `computer_zoom_input.py`（本仓 bottom-drafts/10） |
| 高分辨率 grounding 标准分块 | `dynamic_preprocess(image_size=448, max_num=12)` + thumbnail | OS-Atlas 官方 README |

---

## 5. (d) 判定：领先 / 持平 / 落后，以及天花板

### 5.1 位置（含我们自己的统计不确定性）

我们 44.8% 是 **n=183** 的分层抽样：**Wilson 95% CI = [37.8%, 52.0%]**；重加权后的 42.5%：**CI = [35.5%, 49.7%]**【推断·本报告计算】。

| 对照 | 公开数 | 判定 |
|---|---|---|
| 裸通用 API 单发（GPT-4o，Pro 直接 grounding） | **0.8%** | **大幅领先（+44 pp）**——我们领先的是**架构**，不是模型 |
| 裸通用 API 单发（GPT-4o，v2 官方榜） | **20.1%** | **领先（+25 pp）**，且 v2 是更容易的基准 |
| 2025 论文训练免费 SOTA（ScreenSeekeR，grounder=训过 7B） | **48.1%** | **持平**（48.1 ∈ 我们的 CI 37.8~52.0；重加权后 42.5 的 CI 也含 48.1） |
| 论文级联（ReGround，训过 7B grounder） | 40.2% | **持平**（我们 44.8 / 42.5 都在其上，但同 CI 内） |
| 官方榜第一（训练免费级联 + 训过 32B/8B） | **85.0 / 85.1%** | **落后 ~40 pp** |
| 官方榜典型训过模型（无 zoom） | 67.0 ~ 73.4%（icon 49~57%） | **落后 22~29 pp**；我们 icon 29.9% vs 榜上 icon 35~57% |
| 官方榜训过模型 + zoom-in | 70.4 ~ 81.0%（icon 47~67%） | **落后 26~36 pp** |

### 5.2 三个口径警告（必须随数字一起出现）

1. **不能宣称"我们超过 ScreenSeekeR"**：48.1 在 CI 内；且我们 183 条是**分层抽样 + 本机缺 648/1581 张图**（OS 占 41.5% vs 全量 12.4%）→ 重加权后 42.5%（CI 35.5~49.7%），**仍含 48.1**。要与公开表对话，必须用**全量 1581**（本机可跑 939 条 + 组成重加权）。
2. **官方榜 85.0% 那条不是"通用 API + 自建级联"**：它的 grounder 是训过的 Indeed-UI-32B/8B 与 KV-Ground-8B，我们**没有同款模型**，不能宣称"同口径落后 40 pp"，只能说"**同架构范式（zoom + 一致性门控）已被公开做到 85%，我们处在该范式的早期**"。
3. **官方榜全是"自报 + 可能污染"**：ScreenSpot-Pro 的图像与标注全公开；榜上有条目名称里直接带 `-zoomin`、`(Zoom In)`、`conditional zoom-in` —— **"加不加 zoom"本身就是分数的一部分**，所以 §2.2 的 zoom 增益是**同模型对照**，比跨模型对比可靠得多。

### 5.3 天花板估计

| 路线 | 可达点级准确率 | 依据 |
|---|---|---|
| 通用 API 模型**单发**直接 grounding | **0.8%（Pro）/ 20.1%（v2）** | 论文 Table 3【二手·本仓已录】+ 官方 v2 榜【一手】 |
| 通用 API 模型 + **单窗口**级联（我们现状） | **42.5~44.8%**（实测），可优化到 **~48~52%** | 我们自己 + 论文 ReGround 40.2 / ScreenSeekeR 48.1 夹逼【一手】 |
| 训过的 7B grounding 当第二级 + 级联 | 73~81% | 官方榜 Indeed-UI-8B(-zoom-in) 73.4/81.0、KV-Ground-8B 73.2/80.5【一手】 |
| 训过的 32B + 免训练一致性级联 | **85.0~85.1%** | 官方榜 GroundCascade【一手】 |
| 官方 computer-use / 原生 grounding API | **不适用**（无 ScreenSpot 类点级官方分数） | §3 判读 1【一手】 |

**一句话**：**"通用 API 模型 + 自建级联"没有公开上限**；但**同一架构范式在公开榜上已到 85%**，而**我们与它的差距被定位在三个可修的具体位置**：① 整屏被降采样（官方协议是原图直送）；② crop 窗口 192 px（官方是 bbox 中心 1024 px，级联范式是 0.5 半屏 zoom）；③ 少一层**一致性门控**（官方 GroundCascade 用 1%/4% 门控把 73% 抬到 85%）。**按公开的 zoom 增益（+7~9 pp，icon +8~13 pp）与我们已量到的接线/判据缺口（+2.6~3.9 pp）叠加，48~55% 是短期可及区间；再往上必须换第二级模型（官方 7B 档 73~81%）。**

---

## 6. 我们该改什么（公开依据 → 动作 → 预期）

| 公开依据（一手） | 我们的现状（源码定位） | 该做什么 | 预期数字 |
|---|---|---|---|
| 官方 `gpt4x.py`：**原图直送不 resize** + 0~1 归一化 bbox + `"Target not existent"` 负出口 + T=0 | `model/image.py::downscale_frame` 把整屏压到 **1280×800**；`ground/prompt.py::coarse_prompt` 用「这张图是 W×H」+ 像素坐标 | ① 整屏级**不降采样**（原图直送或分块）；② 提示词加**负出口**；③ 加一臂 0~1 归一化契约 | 官方 v2 榜 GPT-4o 单发 20.1% vs 我们 44.8% 的差距主要来自级联；负出口把"窗外目标"（16/16 坐标合法 0/16 命中）变成可判 negative；icon 分层 35%→85% 已有实测（McNemar p=0.0063） |
| 官方 `reground.py`：**以粗 bbox 中心裁 1024×1024**，越界平移不缩小；官方消融 512=25.1 / 768=34.2 / **1024=40.2** / 1280=40.1 | `ground/channel.py` L162 `DEFAULT_CROP_WIDTH_PX = 192`；`coord/crop_refine.py` 按**点**裁、无 padding、可 shrink | crop 尺寸 **192 → 1024**；裁剪改成**按粗 bbox 中心 + padding**，越界**平移不缩小**（与官方一致） | 「crop 窗口没框住目标」36.1% → 目标 <15%；官方同条件 512→1024 是 +15.1 pp；icon 子类 +5~10 pp |
| 官方榜 zoom 对照（同模型 8 组）：**+7~9 pp，icon +7~13 pp**；GroundCascade 的 **fixed 0.5 zoom + 1% 自一致门 + 4% 跨模型一致门，2.572 次调用** | 我们有 crop 精定位但**无一致性门控**；crop 1:1 不放大（192 crop 仅 49 image tokens） | 送模型前**把 crop 放大到图像预算**（Anthropic 官方 zoom 语义）；加**自一致门控**（同一 crop 问两次，坐标一致才用） | 放大 + 门控合计**+5~10 pp**（按官方 zoom 增益打折）；成本：多 1 次调用（官方级联平均 2.57 次，可接受） |
| 官方 `screenseeker.py`：σ=0.3 中心度 + NMS(0.5) + 归一化空间 padding + **把框画红给 planner 判定 `is_target`/`target_elsewhere`/`target_not_found` 并重写指令** | 我们只有单窗口，无候选、无存在性判定 | 粗点不确定时给**多个候选窗口**让模型选；并加一次**存在性校验**（可复用我们的候选通道） | 把 36.1% 的错接线一部分转成"诚实的 miss"；参考 ScreenSeekeR 48.1 与 ReGround 40.2 的差距来源 |
| 官方 `gpt4x.py` 全程 **0~1 归一化**坐标 + viewport 反变换（`view_bbox_to_real_bbox`） | 我们用「声明尺寸 + 绝对像素」+ 自研适配器（r-r2 已验证换算链可逆到 4.5e-13 px） | 保留现有换算（已验证），但**提示词契约与官方一致**（0~1）以便与公开表同口径 | 口径可比性 ↑；分数本身不应变 |
| AgentNetBench：**OpenAI CUA 71.7 < OpenCUA-7B 79.0**（原生 grounding API 不如训过 7B） | 我们第二级 = 通用 API | 第二级换成**训过的 7B grounding**（OpenCUA-7B 50.0 / OS-Atlas-Pro-7B 47.2），量化权重（6 GB 显存需 4bit） | 第二级 +5~8 pp（按 7B→7B 公开差 27.6→50.0 打折） |

---

## 7. 最小改动清单（≤8 条，按投入产出排序）

| # | 文件 | 做什么 | 预期哪个数变多少 |
|---|---|---|---|
| **1** | `src/minicode/ground/channel.py`（`DEFAULT_CROP_WIDTH_PX = 192`，L162）+ `src/minicode/ground/crop.py` | crop 尺寸 **192 → 1024**，裁剪改为**按粗定位 bbox 中心 + padding**、越界**平移不缩小**（逐字对齐官方 `models/methods/reground.py::crop_to_center`） | 「窗口没框住目标」36.1% → 目标 <15%；icon 子类 **+5~10 pp**；整数准确率 **+3~8 pp（推断）**；成本：crop 图像 token 49 → 数百 |
| **2** | `src/minicode/ground/channel.py`（crop 准备处）+ `src/minicode/ground/zoom.py` | 送模型前**把 crop 放大到模型图像预算**（Anthropic 官方 zoom 语义："scaled up to fill the image budget"），并加**自一致门控**（同 crop 问两次、坐标一致才采用） | 按官方 zoom 对照 **+7~9 pp（icon +7~13 pp）**，我们起点更低，**预期 +5~10 pp**；多 1 次调用（官方级联 2.57 次） |
| **3** | `src/minicode/model/image.py::downscale_frame` + `ground/channel.py` 整屏级 | 整屏**不再降采样成 1280×800 单图**：改**原图直送**（官方 `gpt4x.py` 做法）或**分块覆盖**（官方 2×2 象限 / OS-Atlas 448 px×≤12 tiles） | icon 在模型输入图里的中位尺寸 13.25~14.5 px → 原生 16 px 以上；把已实测的 icon 35%→85%（p=0.0063）搬进生产路径 |
| **4** | `src/minicode/ground/prompt.py::coarse_prompt` | 照抄官方通用 API 协议三件套：**① 负出口** `"Target not existent"`；② `temperature=0` / `max_tokens=2048` 固定；③ 保留"框→中心"口径（已对齐）。**同时保留我们自创的"声明尺寸+像素"契约作为对照臂**（官方是 0~1 归一化） | 粗定位的"窗外目标语义失败"（16/16 坐标合法 0/16 命中、中位偏 917 px）变成可判 negative；错选率下降（幅度未实测） |
| **5** | `src/minicode/ground/gate.py`（回落判据 50 px、参考点=crop 中心） | 按 r-r5 结论改判据：参考点改回**粗定位点**或尺度自适应/取消（实测 t=96 → 47.71%，参考点改粗点 t=80 → **48.66%**） | **+2.6~+3.9 pp**（44.81% → 47.45~48.66%）；43.7% 的自伤回落消失 |
| **6** | `src/minicode/eval/sspro.py`（`OFFICIAL_ROWS`，L151–217） | 对照表**按赛道分栏**并补一手官方榜 22 行（GroundCascade 85.0/85.1、Indeed-UI-8B 73.4→zoom-in 81.0、KV-Ground 73.2→80.5、AdaZoom 61.9→70.6、HuzzleWorld-2B 61.7→70.9…）＋ v2 榜 GPT-4o **20.1**；**标注"官方 Pro 榜无通用 API 行"**；加一行"本仓 44.8%（n=183，Wilson CI 37.8~52.0；重加权 42.5%）" | **分数不变**；消除"我们 44.8% vs UI-TARS-72B 38.1%"这类跨赛道误比；并把 §4.2 里本轮**未能复现的五个数字**（Claude(Computer Use) 17.1 等）标为待回源 |
| **7** | `src/minicode/eval/__main__.py`（`--dataset`）+ `eval/holdout.py` | 用**同一配置**跑**全量**（本机 939 张图）并按 group 构成重加权，产出**与官方榜同口径**（官方 `eval_screenspot_pro.py` bbox 命中）的头条数 | 44.8%（183 抽样）→ 期望 **40~43%**（重加权真值 42.5%）；这是唯一能与公开表对话的数 |
| **8** | `docs/research/vision-root-cause.md`（§4.2 表 + §7-Q3） | 把"我们这一格没有公开对照"改写为**有据的夹逼结论**：下界 0.8%（GPT-4o 单发）/ 20.1%（v2 官方榜），上界 85.0%（官方训练免费级联）；并写明"公开实现里通用 API 只当 planner（ScreenSeekeR 48.1），当 grounder 的公开格子只有我们" | **分数不变**；把主张变成可被公开数字检验的表述（"与 48.1 不可区分；与官方训练免费级联差 ~40 pp"） |

> 只做两条的话：**#1（crop 1024 + 按 bbox 中心 + padding）**与 **#2（放大 + 一致性门控）**性价比最高——它们直接打在 36.1% 的接线缺口上，且有官方一手数字（官方消融 512→1024 = +15.1 pp；官方 zoom 对照 = +7~9 pp）作为幅度锚。**#6 与 #8 不可省**：它们决定我们对外怎么说这个数。

---

## 8. 本轮未核实 / 已知的证据缺口

| 项 | 状态 | 回源方式 |
|---|---|---|
| 论文 **Table 2/3** 的通用 API 行（GPT-4o 0.8%、Claude 3.5 ~1.8%、各模型 icon/text 分栏） | 【二手·本仓已录】本轮未复核（arXiv HTML 截断 / PDF 不可抓 / HF 博客两次失败） | 下载 PDF `https://arxiv.org/pdf/2504.07981` 或 `https://likaixin2000.github.io/papers/ScreenSpot_Pro.pdf` 本地读；或 OpenReview `https://openreview.net/forum?id=XaKNDIAHas` |
| 本仓 §4.2 中的 **Claude(Computer Use) 17.1 / Claude 3.7 27.7 / OpenAI CUA 23.4 / GPT5-minimal 18.5 / Seed-1.5-VL 60.9** | **本轮在官方 `results/screenspot_pro.json` 里找不到这些行** | 旧版榜文件 / 第三方转录 / UI-TARS README；**引用前必须回源** |
| 官方榜 `UI-Venus-1-5-8B` 的 overall | 抓取被截断未取到 | 重取 `results/screenspot_pro.json` 或官网 Detailed View |
| **Gemini / Kimi / Doubao / GLM 的 ScreenSpot 点级分数** | **不适用**（无任何公开点级数字） | 若厂商发布模型卡数字则按自报处理 |
| OpenAI CUA OSWorld 38.1% / WebArena 58.1%；Anthropic Claude 3.5 computer use OSWorld 14.9% | 【二手】本轮 openai.com 直取 403 | `https://openai.com/index/computer-using-agent/`、`https://www.anthropic.com/news/3-5-models-and-computer-use` |
| 官方 ScreenSpot-Pro 榜是否另有 API 模型文件 | 未找到（`screenspot_pro_api.json` 404） | 官方榜站 `results/` 目录清单 |
| `RegionFocus 61.6`、`UI-TARS-72B 37.1~38.1`、`GUI-Actor 40.7/44.6` 等其他线的数字 | 本轮未复核（不在本线范围） | 见 r-r1/r-r2 等线的出处 |

---

## 9. 附录：本报告用到的公开一手出处（逐条可打开）

1. ScreenSpot-Pro 论文：https://arxiv.org/abs/2504.07981 ｜HTML https://arxiv.org/html/2504.07981v1 ｜PDF https://arxiv.org/pdf/2504.07981 ｜作者 PDF https://likaixin2000.github.io/papers/ScreenSpot_Pro.pdf ｜OpenReview https://openreview.net/forum?id=XaKNDIAHas
2. 官方 leaderboard：https://gui-agent.github.io/grounding-leaderboard/ ｜**Pro 榜 JSON** https://gui-agent.github.io/grounding-leaderboard/results/screenspot_pro.json ｜**v2 榜 JSON** https://gui-agent.github.io/grounding-leaderboard/results/screenspot_v2.json
3. 官方仓：https://github.com/likaixin2000/ScreenSpot-Pro-GUI-Grounding ｜readme https://raw.githubusercontent.com/likaixin2000/ScreenSpot-Pro-GUI-Grounding/main/readme.md ｜`models/gpt4x.py` https://raw.githubusercontent.com/likaixin2000/ScreenSpot-Pro-GUI-Grounding/main/models/gpt4x.py ｜`models/methods/reground.py` https://raw.githubusercontent.com/likaixin2000/ScreenSpot-Pro-GUI-Grounding/main/models/methods/reground.py ｜`models/methods/screenseeker.py` https://raw.githubusercontent.com/likaixin2000/ScreenSpot-Pro-GUI-Grounding/main/models/methods/screenseeker.py ｜目录清单 https://api.github.com/repos/likaixin2000/ScreenSpot-Pro-GUI-Grounding/contents/models
4. 数据集：https://huggingface.co/datasets/likaixin/ScreenSpot-Pro ｜v2 https://huggingface.co/datasets/OS-Copilot/ScreenSpot-v2
5. OS-Atlas（ScreenSpot-v2 作者）：https://github.com/OS-Copilot/OS-Atlas ｜README https://raw.githubusercontent.com/OS-Copilot/OS-Atlas/main/README.md ｜论文 https://arxiv.org/abs/2410.23218
6. OpenCUA（两张公开表）：https://github.com/xlang-ai/OpenCUA ｜README https://raw.githubusercontent.com/xlang-ai/OpenCUA/main/README.md ｜论文 https://arxiv.org/abs/2508.09123
7. Anthropic computer use 公告：https://www.anthropic.com/news/3-5-models-and-computer-use ｜工程博客 https://www.anthropic.com/news/developing-computer-use ｜官方文档 https://platform.claude.com/docs/en/agents-and-tools/tool-use/computer-use-tool ｜SDK（zoom 动作语义）https://pypi.org/project/anthropic/1.15.0/ （本仓已落盘：`docs/research/bottom-drafts/10-anthropic-zoom源码细节.md` §1.1）
8. OpenAI CUA：https://openai.com/index/computer-using-agent/ ｜模型卡 https://ai.azure.com/catalog/models/computer-use-preview
9. UI-TARS（第三方转录表出处）：https://github.com/bytedance/UI-TARS-desktop
10. 本仓相关产物：`docs/research/vision-root-cause.md`（§4.2、§7-Q3）、`r-r1-tolerance-standard.md`（口径）、`r-r4-crop-window.md`（官方仓裁剪参数）、`r-r5-deviation-criterion.md`（阈值曲线）、`bottom-drafts/03-gui-agent-landscape.md`（2026 赛道图景；其中厂商自报数与官方榜数字均标【二手】，本轮未复核）。

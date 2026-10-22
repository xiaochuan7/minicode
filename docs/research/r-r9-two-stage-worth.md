# R9 · 两段式到底值不值？（coarse-to-fine 的公开对照 · 候选编号范式）

> 调研线 R9 · 只读调研，**本轮唯一写盘文件**是本文档（未改任何实现文件，未 commit）。
> 证据强度标注：【一手】= 读到原文正文/表格；【二手】= 只见搜索摘要或第三方转述；【推断】= 我由一手数据推导。
> 一手来源清单见 §8。无法直接读到的表（ScreenSpot-Pro Table 4/5）已用**两条独立 PDF 文本抽取 + 本地仓逐行核过的官方代码行号**交叉确认，仍标【二手】。

---

## 0. 结论（先给判断）

**两段式要保留，但不能按现在的形状保留。我们现在的形状是「无条件 crop + 固定 192 px 窗口 + 50 px 偏离判据」，公开证据显示这三样各自都是错的用法。**

三条支撑：

1. **公开的 coarse-to-fine 增益全部锚在弱基座上。** 官方四方法的单段式基线是 OS-Atlas-7B 的 **18.9%**；我们在 41.5% 粗定位上只量到 **+3.3 pp**（噪声内）。**公开文献里没有任何「≥40% 单段式基座 + crop 精定位」的消融**，我们的处境是数据空白区，不能拿 +21.3 pp 之类当可争取空间。【一手 + 推断】
2. **crop 会真的把分数做低，而且有实测数。** UI-Zoomer 论文 Table 1（一手）：ScreenSpot-v2 上「不裁剪直接定位 **81.84%**」vs「DiMo-GUI 迭代裁剪 **77.20%**」——**裁剪精炼掉了 4.64 pp，延迟从 35:47 涨到 6:43:07（11.4 倍）**。同一篇论文 Table 2（一手）：当基座已经 95.2%（Mobile）时，裁剪精炼净收益 **+0.0**；基座 24~33%（UI-Vision）时净收益 **+9.2 ~ +10.4**。**「基座越强，crop 越没用」有一手数据。**
3. **该改的不是 crop 尺寸，是「什么时候裁」+「裁成多大」+「裁完产出什么」。** 公开的可照抄参数：UI-Zoomer 的 **N=8 次采样 / T=0.9 / S = 两两平均 IoU + 平均 token 置信度 / ScreenSpot-Pro 上 τ=1.0、γ=2.5、最小边长 m=512 px**；ZoomClick 的 **预对齐（2×2 分块 + 与全局预测的距离阈值 τ）+ 最小 crop 尺寸作「上下文地板」+ 直接在原坐标系裁（防漂移）**；GUI-Actor 的 **候选池 K=20 + 标记点 + True/False 验证器 + γ=0.95（ScreenSpot-Pro）**。DiMo-GUI 的 **text/icon 双分支 + 选择** 则是「候选制」里唯一**不需要训练、不需要额外模型、纯 API 模型可实现**的形态。

> ⚠️ **顺带发现一个跨线的警报（比本线结论更重要）**：公开评测里 API 模型在 ScreenSpot-Pro 上的分是 **GPT-4o 0.8%**（论文摘要 + UI-Zoomer Table 3 一手）、**Claude-3.7-Computer-Use 17.1%**（DiMo-GUI Table 一手）。**我们的粗定位是 41.5%。** 要么我们的模型在坐标 grounding 上异常强，要么**我们的评测口径比官方宽松**。这两件事都会影响项目里所有绝对分数，值得单独查一次（见 §6 第 8 条）。

---

## 1. (a) 四个官方 coarse-to-fine 方法各自的单段式基线

### 1.1 四个方法共用同一个弱基座，单段式 = 18.9%

ScreenSpot-Pro 论文摘要原文（**【一手】**，https://arxiv.org/abs/2504.07981 ，HTML https://arxiv.org/html/2504.07981v1 ）：

> "Existing GUI grounding models perform poorly on this dataset, with the best model achieving only **18.9%**."
> "ScreenSeekeR boosts the **OS-Atlas-7B** model's performance **from 18.9% to 48.1%**, achieving a **254% relative improvement**."

正文 §4.1 还写明四个方法是拿同一个 grounder 比的："We compare the four proposed methods, Iterative Zooming, Iterative Narrowing, ReGround and ScreenSeekeR with the same grounding model **OS-Atlas-7B**."【一手】

### 1.2 增益表

| 方法 | 单段式基线 | 两段式 | 增益 | 参数 | 证据 |
|---|---|---|---|---|---|
| OS-Atlas-7B 单段（无视觉搜索） | **18.9** | — | — | 整屏一次 | 【一手】论文摘要 |
| **ReGround** | 18.9 | **40.2** | **+21.3 pp** | 官方仓 `methods/reground.py:151`：**crop 1024×1024**，整屏定位 + crop 内再定位各 1 次 | 数字【二手】Table 4；本地仓 `src/minicode/eval/sspro.py:159-170` 已核代码行号 |
| **Iterative Narrowing** | 18.9 | **31.9** | **+13.0 pp** | `methods/iterative_narrowing.py:189-192`：`max_search_depth=3`，以预测为中心 **±0.25 归一化**裁剪 | 【二手】Table 4；本地仓 `eval/sspro.py:171-181` |
| **Iterative Focusing**（论文 §4.1 叫 Iterative Zooming） | 18.9 | **31.0** | **+12.1 pp** | `methods/iterative_focusing.py:156-161`：`max_search_depth=3`，**2×2 象限**切分 | 【二手】Table 4；本地仓 `eval/sspro.py:182-193`（命名对应关系本地仓已标**推断**） |
| **ScreenSeekeR** | 18.9 | **48.1** | **+29.2 pp（+254% 相对）** | `methods/screenseeker.py:456-467`：planner = **gpt-4o**，`min_crop_size=[1280,768]`，`max_aspect_ratio=2.5`；论文 §4.2：**grounder 仅在 patch ≤1280 px 时调用**，候选打分 **σ=0.3** + NMS + 框膨胀 | 结果【一手】摘要；参数【二手】本地仓 `eval/sspro.py:194-204` |

**ReGround 的 crop 尺寸消融**（论文 Table 5，OpenReview camera-ready 为 Table 6；两条独立 PDF 文本抽取一致：作者站 PDF 片段 + OpenReview PDF 片段）**【二手】**：

| crop 尺寸 | 512 | 768 | **1024** | 1280 |
|---|---|---|---|---|
| OS-Atlas-7B | 25.1 | 34.2 | **40.2** | 40.1 |

→ **512→1024 = +15.1 pp**（crop 终于框住了目标），**1024→1280 = −0.1 pp**（平台期）。【推断，基于上表】

### 1.3 从这些数字里读出来的三条

1. **「一次大 crop」完胜「多轮小 crop」**：+21.3 pp vs +13.0 / +12.1 pp。公开证据支持的是**一轮裁剪 + 一次再定位**，不是「迭代 zoom」。
2. **crop 的作用是「包含目标」，不是「放大」**：官方最优 crop = **1024 px**（≈ 基座原生输入边长），且 512→1024 买的全是「不再漏目标」。
3. **所有公开增益的基座都在 18.9% 档**；**≥40% 档基座 + crop 精定位，公开数据不存在**。→ 我们的 +3.3 pp 是唯一可用的数，+21.3 pp 不是。

### 1.4 我们自己的数字放进来

| | 公开（OS-Atlas-7B） | 我们（通用 API 模型） |
|---|---|---|
| 单段式 | 18.9% | **41.5%**（2.2× 公开基座） |
| 两段式 | 40.2 ~ 48.1% | **44.8%** |
| 增益 | +12.1 ~ +29.2 pp | **+3.3 pp（噪声内）** |

→ 【推断】**公开那套参数是为「粗定位 18.9%」的失效模式设计的；我们的失效模式是「挑错元素」**（自测：挑错时误差中位 226 px、p90 1451 px）。在 1451 px 级的粗误差分布下，**围绕粗点的固定小窗口必然框不住目标**——要覆盖 p90 误差需要 ~2900 px 窗口（≈整屏）。**「小 crop」与「能覆盖的 crop」之间没有中间答案。** 这与我们自测「36.1% 的 crop 窗口压根没框住目标」是同一件事，官方 Table 5 的 512→1024 = +15.1 pp 就是它的官方版本。

---

## 2. (b) 有没有公开结论说「对已经很强的 coarse 定位器，crop 精定位无增益甚至有害」

### 2.1 现在有了一手数据：**有，而且有实测数**

**【一手】UI-Zoomer（arXiv 2604.14113，https://arxiv.org/abs/2604.14113 ，HTML https://arxiv.org/html/2604.14113v1 ，ar5iv https://ar5iv.labs.arxiv.org/html/2604.14113 ）**

引言原文：

> "We show empirically that **unconditional zoom-in on ScreenSpot-v2 degrades accuracy below the direct prediction baseline** while significantly increasing latency (Table 1), **as easy cases lose the global context the model was already exploiting**."

**Table 1（一手原文）**：

| Method | Avg Acc | Time |
|---|---|---|
| w/o DiMo-GUI（不裁剪，直接定位） | **81.84%** | 35:47 |
| w/ DiMo-GUI（迭代裁剪） | **77.20%** | 6:43:07 |

→ **裁剪精炼 = −4.64 pp 准确率 + 11.4× 延迟。** 这就是「crop 精定位可能有害」的一手实测。

**Table 2（一手原文，UI-Venus-7B）**：

| 基准 | 分区 | 基座 avg | +UI-Zoomer avg | Δ |
|---|---|---|---|---|
| ScreenSpot-v2 | Mobile | 95.2 | 95.2 | **+0.0** |
| ScreenSpot-v2 | Desktop | 94.3 | 96.4 | +2.1 |
| ScreenSpot-v2 | Web | 92.5 | 93.4 | +0.9 |
| ScreenSpot-v2 | **Overall** | **94.0** | **94.9** | **+0.9** |
| UI-Vision | Basic | 33.3 | 42.5 | **+9.2** |
| UI-Vision | Functional | 29.7 | 40.1 | **+10.4** |
| UI-Vision | Spatial | 24.4 | 33.7 | **+9.3** |

→ **基座 94~95% 时 crop 只值 +0.0~+0.9 pp；基座 24~33% 时值 +9.2~+10.4 pp。** 这就是「**基座越强，crop 越没用**」的定量曲线，也解释了为什么我们（41.5% 粗定位）量到的只有 +3.3 pp。

**【一手】ZoomClick（arXiv 2512.05941，Princeton AI² Lab/PKU，https://arxiv.org/abs/2512.05941 ，HTML https://arxiv.org/html/2512.05941v1 ，代码 https://github.com/Princeton-AI2-Lab/ZoomClick ）**

> "great **first-step accuracy** leads to massive performance boost in latter steps."（第一步准不准，决定后面能拿多少）
> "Current evaluations do not clarify when zoom improves localization, **when it induces harmful context loss or distraction**, nor why certain models collapse under deeper zoom levels…"
> 最小 crop 尺寸是 "**context floor** … keeps the view within the model's training resolution regime and retains semantic cues (e.g., surrounding text, icon grouping, layout) needed for reliable grounding."

→ 【推断】**我们的第一步只有 41.5% 准，而且错的时候错得很远（p90 1451 px）** → 按 ZoomClick 的规律，后面的 zoom 根本接不上。这不是「crop 参数没调好」，是**第一步的准确率决定了整条链的上限**。

**【一手】DiMo-GUI（arXiv 2507.00008v2，EMNLP 2025 Main，https://arxiv.org/abs/2507.00008 ，HTML https://arxiv.org/html/2507.00008 ）**：其 Table 1 就是「无条件 zoom 在 ScreenSpot-v2 上低于直接预测基线」这组数（UI-Zoomer 转引为 81.84 / 77.20）。

### 2.2 所以 (b) 的答案是

- **不是「无公开结论」**——2025-2026 的文献已经把这条结论做实了（一手）：**无条件/固定比例的裁剪精炼在「基座已经强」时会把分数做低**，而且在 ScreenSpot-v2 上有 −4.64 pp 的实测。
- **公开的修正方案一律是「按不确定度选择性裁剪 + 按方差自适应定尺寸」**（UI-Zoomer）、**「预对齐 + 上下文地板 + 原坐标系裁剪」**（ZoomClick）、**「模态分离 + 选择」**（DiMo-GUI）。
- **仍然没有**「45% 档通用 API 模型 + crop 精定位」的专门消融 → 我们必须自己测（§6 第 8 条）。

---

## 3. (c) 什么条件下两段式明显有用（可照抄的判据）

| 条件 | 公开证据 | 数字 | 我们 |
|---|---|---|---|
| **基座离天花板远** | UI-Zoomer Table 2【一手】 | 基座 24~33% → +9.2~+10.4 pp；基座 94~95% → +0.0~+0.9 pp | 部分（41.5%） |
| **第一步（粗定位）准** | ZoomClick【一手】 | "great first-step accuracy leads to massive performance boost in latter steps" | ✗（p90 错 1451 px） |
| **crop 必须能框住目标** | ScreenSpot-Pro Table 5【二手】 | 512→1024 = +15.1 pp；1024→1280 = −0.1 | ✗（36.1% 窗口没框住） |
| **保留上下文（最小 crop 尺寸）** | UI-Zoomer `m=512`【一手】；ZoomClick context floor【一手】；ScreenSeekeR `min_crop_size=[1280,768]`【二手】；ReGround `1024`【二手】 | 所有公开实现的末段工作面都在 **512~1280 px** | ✗（我们 192 px = 屏宽 7.5%，**低于所有公开下限**） |
| **目标小且语义模糊（icon）** | UI-Zoomer【一手】 | "Icon targets benefit more than text targets on average" | ✓（icon 29.9%） |
| **按不确定度触发** | UI-Zoomer 摘要 + §4.1.3【一手】 | ScreenSpot-Pro **+13.4%** / UI-Vision **+10.3%** / ScreenSpot-v2 **+4.2%**（训练无关） | ✗（我们现在无条件跑） |
| **裁剪不切断相对位置语义** | 【推断】+ GUIZoom-Bench 的 easy-mislead 类【一手】 | 「mislead」样本 = 裁剪后失去参照系 | 部分 |
| **有停手条件** | DiMo-GUI 停机【一手】：相邻两次预测距离 < **1/6 屏对角线** 即停，`max_iters=7`；ZoomClick 停机【一手】：到最小 crop 尺寸即停；UI-Zoomer【一手】：crop 后**只跑一次确定性推理（T=0）** | — | ✗（我们无停手/无自适应） |

→ 【推断】**一句话：两段式在「基座弱 × 第一步准 × crop 真能框住 × 保留上下文 × 只在不确定时触发」同时成立时收益最大。我们目前只满足「目标小」，其余四条全不满足** —— 现在这套两段式恰恰跑在最不利的配置上。

---

## 4. (d) 要不要放弃点式精定位、改走「候选编号」范式

### 4.1 三条公开路线（都有可照抄的实现细节）

**路线 A：检测器 + 描述模型 + SoM 编号（OmniParser）**

- **【一手】OmniParser（arXiv 2408.00203，https://arxiv.org/abs/2408.00203 ）摘要："a **detection model** to parse interactable regions on the screen and a **caption model** to extract the functional semantics of the detected elements … **OmniParser significantly improves GPT-4V's performance on ScreenSpot benchmark**."**
- V2 权重构成：**icon_detector（YOLO）+ icon_caption_florence（Florence-2 large）**（https://huggingface.co/microsoft/OmniParser-v2.0 ，【二手】）；V2 把 caption 图幅缩小，**caption 延迟降 60%**（MSR 博客，【二手】）。
- V2 + GPT-4o 在 ScreenSpot-Pro 上 **39.6**（【二手】，MSR 博客/第三方转述；该博客页面本轮抓取被截断在导航区，未取到正文表格）。
- 代价：**两个额外模型 + 每帧一次检测 + 一次描述 + 一次编号选择**。6 GB 显存只能走 CPU/ONNX。

**路线 B：模态分离 + 选择（DiMo-GUI）—— 唯一「纯 API 模型、无需训练、无需额外模型」的候选制**

- **【一手】DiMo-GUI Algorithm 1：Step 1 text 分支定位 → Step 2 icon 分支定位 → Step 3 `Select(C_text, C_icon, I, Q)` 让模型在两个候选里选。**
- 每个分支内部：crop = **以预测为中心、原图的一半大小**；`max_iters=7`；**停机条件 = 相邻两次预测距离 < 缩放前图像对角线的 1/6**。
- 效果：OS-Atlas-4B 在 ScreenSpot-Pro 上 **3.7 → 10.6**（【一手】Table）；UI-TARS-7B 35.7、UI-TARS-72B 38.1、**Claude Computer Use 17.1**、**GPT-4o 0.8**（【一手】Table）。

**路线 C：可训练的无坐标 grounding + 验证器（GUI-Actor）—— 我们只能借它的「验证器」形态**

- **【一手】GUI-Actor（arXiv 2506.03143，NeurIPS 2025，https://arxiv.org/abs/2506.03143 ，HTML https://arxiv.org/html/2506.03143v1 ）**：
  - 数字：Qwen2-VL 底座 **40.7**、Qwen2.5-VL 底座 3B/7B **42.2 / 44.6**（不带 verifier），**超过 UI-TARS-72B 的 38.1**。
  - 坐标回归的三条结构性缺陷（一手原文）：**空间-语义对齐弱**、**点监督有歧义**、**密集坐标 vs ViT patch 粒度错配** → 这三条正对我们「挑错元素 / 同图两次不一致 / 跨运行翻转」的病。
  - **验证器配方（一手，可照抄）**：在候选位置画 **空心红圈**标记 → 输入 (截图, 指令) → 输出 `True`/`False`；得分 `s = P(True)/(P(True)+P(False))`；候选池 **K=20**（按注意力权重降序）；**置信度阈值 γ=0.95（ScreenSpot-Pro）/ 0.8（ScreenSpot、ScreenSpot-v2）**，取第一个超过阈值的候选。
  - 代价：action head ~100M 参数（要训练）+ verifier（从 UI-TARS-2B-SFT 微调）→ **我们训练不了**；但「标记 + 问 True/False」这一形态可用**提示词自评**替代（M7 已列 RegionFocus 的「is this position correct?」）。

**SoM 的画法规则（一手，https://arxiv.org/abs/2310.11441 ，HTML https://arxiv.org/html/2310.11441v2 ，代码 https://github.com/microsoft/SoM ）**

- 标记类型必须随画面内容变：原文 "given an arithmetic image full of numbers, the **numeric marks should be avoided**, while the **alphabetic characters should be not used for a screenshot of a document**."
  → 【推断】**桌面 UI 满是数字（坐标、表格、工具栏），编号必须用字母，不能用数字**，否则编号本身污染画面。
- **标记不画在区域中心**（§4.4 小节标题："The centered marks are not always the best"）；方法上按面积升序处理、用**距离变换**找「离所有边界最远」的位置；区域太小时把标记**挪到区域外一点**。
- ⚠️ **模型相关警告（一手）**："we find that **only GPT-4V**, when equiped with SoM, shows the emergent grounding ability and significantly outperforms the other LMMs."
  → 【推断】**「模型能不能读懂编号」必须先自测 20 条**，否则整条候选制建在沙上。

### 4.2 我们其实已经有候选制的一半

- `src/minicode/ground/numbered_bench.py`：视觉通道的**编号选择**分支已存在（候选源 = `OcrCandidateSource` / `UiaCandidateSource`），逐样本记 **candidate_recall / IoU@0.3 / IoU@0.5 / 框中心是否落在真目标内 / landing_error_px**，落点 = 候选框物理中心（零坐标换算）。
- `src/minicode/ground/channel.py`：`TwoStageGrounder` 已支持 `candidate_source=`。
- `docs/tickets/M4-candidate-selection.md`：UIA 候选集「模型只选不瞄」规格已写好。

### 4.3 (d) 的诚实结论

**不要把「两段式」整体换成「候选制」——公开的候选制胜利几乎全是被弱基座衬托出来的。**

- OmniParser 的「39.6 vs GPT-4o 0.8%」看起来是 +38.8 pp，但**分母是 0.8%**（GPT-4o 在 ScreenSpot-Pro 上直接 grounding 几乎失效）【一手】。对照我们的 41.5% 粗定位，**候选制相对「强直接定位」有没有增益，公开数据里没有**。
- 候选制对我们的真正价值不是「更高分」，而是**结构上不依赖粗点**：
  1. **绕开第一大根因**：候选集与粗点无关 → p90 1451 px 的粗误差不再是问题（而 crop 的 36.1% 漏框问题**只有**候选制能绕开）。
  2. **把「挑错元素」变成可核的候选召回问题**：候选召回率是这条路的**天花板**，可单独测量、可单独优化（而点式精定位的天花板被粗点锁死）。
  3. **落点精度直接归零**：落点 = 候选框物理中心 → 误差 = 框内取整误差（我们的 UIA 路径已实测 0.0 px）。
- **代价必须写清**：候选召回 = 天花板；OCR 候选对 **text** 类够用、**对 icon 类无效**（图标没有文字，而 icon 恰是最差子集 29.9%）→ 纯视觉候选制要补 **icon 候选源**；若引 OmniParser 的 icon_detector（YOLO，CPU 可跑）就是 +1 个模型 + 每帧一次检测。
- **最省事的中间形态**：**DiMo-GUI 式模态分离**（text 分支 + icon 分支 + 模型 Select）不需要任何新模型、不需要训练，只要我们的模型能对「这张图里的文字元素」和「这张图里的图标元素」分别定位 —— 这可以先用 **OCR 渲染图 vs 原图**两条分支近似实现。

---

## 5. 公开数字速查表（带出处与条件）

| # | 数字 | 条件 / 出处 | 强度 |
|---|---|---|---|
| 1 | 18.9% → 48.1%（+254% 相对） | OS-Atlas-7B 单段 → +ScreenSeekeR，ScreenSpot-Pro 全量 1581，官方 eval 脚本；论文摘要 | 【一手】 |
| 2 | ReGround **40.2** / Iterative Narrowing **31.9** / Iterative Focusing **31.0** / ScreenSeekeR **48.1** | 同上，Table 4；crop 1024×1024 / depth 3 ±0.25 / depth 3 2×2 象限 / min_crop_size=[1280,768] + max_aspect_ratio 2.5 | 【二手】（本地仓已核代码行号） |
| 3 | crop 消融 **512=25.1 / 768=34.2 / 1024=40.2 / 1280=40.1** | OS-Atlas-7B，ReGround crop 尺寸，Table 5/6 | 【二手】（两处独立 PDF 抽取一致） |
| 4 | 目标占图 **0.07%**（ScreenSpot 为 2.01%）；text 占 62.6% | 论文 §3.4 | 【一手】 |
| 5 | **81.84% / 35:47 → 77.20% / 6:43:07** | ScreenSpot-v2，无裁剪直接定位 vs DiMo-GUI 迭代裁剪；UI-Zoomer Table 1 | 【一手】 |
| 6 | UI-Venus-7B + UI-Zoomer：ScreenSpot-v2 overall **94.0 → 94.9（+0.9）**，Mobile **+0.0**；UI-Vision **33.3→42.5 / 29.7→40.1 / 24.4→33.7** | UI-Zoomer Table 2 | 【一手】 |
| 7 | UI-Zoomer 增益 **+13.4% / +10.3% / +4.2%**（ScreenSpot-Pro / UI-Vision / ScreenSpot-v2），训练无关 | UI-Zoomer 摘要 | 【一手】 |
| 8 | UI-Zoomer 参数：**N=8** 采样、**T=0.9**、S = 两两平均 IoU + 平均 token 置信度、ScreenSpot-Pro 上 **τ=1.0、γ=2.5**、**最小边长 m=512 px**、crop 后只跑一次 **T=0** 确定性推理、保留 **top 75%** 候选、**方形** crop、越界向内 shift | UI-Zoomer §3.2–§4.1.3 | 【一手】 |
| 9 | ZoomClick：**UI-Venus-72B 在 ScreenSpot-Pro 达 73.1%**；UI-Venus-7B + ZoomClick 比原 72B 高 **2.2%**；预对齐 = 2×2 分块 + 与全局预测距离 < τ（像素）才采纳局部预测；**最小 crop 尺寸 = 上下文地板**；每次在**原坐标系**裁（防迭代漂移） | ZoomClick 摘要+§3.2 | 【一手】 |
| 10 | GUI-Actor：**40.7**（Qwen2-VL）/ **42.2·44.6**（Qwen2.5-VL 3B/7B，不带 verifier）> UI-TARS-72B **38.1**；候选池 **K=20**；标记 = **空心红圈**；验证器 **True/False**，`s=P(True)/(P(True)+P(False))`，**γ=0.95**（ScreenSpot-Pro）/ **0.8**（ScreenSpot/ScreenSpot-v2）；verifier 从 UI-TARS-2B-SFT 微调；action head ~100M 参数 | GUI-Actor 摘要 + §4 + §5 实现细节 | 【一手】 |
| 11 | GUI-Actor 对坐标回归的三条缺陷：空间-语义对齐弱 / 点监督歧义 / 坐标密度与 ViT patch 粒度错配 | GUI-Actor 摘要+§1 | 【一手】 |
| 12 | DiMo-GUI：crop = **原图一半大小**；`max_iters=7`；停机 = 相邻预测距离 < **1/6 屏对角线**；**text/icon 双分支 + Select**；ScreenSpot-Pro 基线：OS-Atlas-4B 3.7 → **+DiMo-GUI 10.6**；GPT-4o **0.8**、Claude Computer Use **17.1**、UI-TARS-7B 35.7、UI-TARS-72B 38.1 | DiMo-GUI Algorithm 1 + Table | 【一手】 |
| 13 | DRS-GUI：训练无关动态区域搜索，**ScreenSpot-Pro +14%**（Qwen2.5-VL-7B、UGround-V1-7B）；UI Perceptor 三动作 Focus/Shift/Scatter + MCTS Action Planner + 区域质量奖励 | arXiv 2605.15542 摘要 | 【一手】 |
| 14 | OmniParser：**检测模型 + 描述模型**两条腿，显著提升 GPT-4V 在 ScreenSpot 的表现；V2 组成 = YOLO icon_detector + Florence-2 caption，caption 延迟 −60% | OmniParser 摘要 / MSR 博客 | 【一手】摘要；【二手】V2 细节 |
| 15 | OmniParser V2 + GPT-4o 在 ScreenSpot-Pro **39.6** | MSR 博客 / 第三方转述（本轮博客正文被截断，未取到表） | 【二手】 |
| 16 | SoM：字母编号（数字密集画面禁用数字标记）、标记不画中心、距离变换选位、太小的区域把标记挪到区域外；**「只有 GPT-4V 被验证会读标记」** | SoM §2.3 / §4.4 | 【一手】 |
| 17 | ScreenSpot-Pro §2.1 提到 **VisualWebBench 把定位预测改成「给候选的多选」** | 论文 §2.1 | 【一手】 |

---

## 6. ≤8 条最小改动清单（按投入产出排）

> 每条：改哪个文件 → 做什么 → 预期哪个数变多少。**本轮只读，未实施。**
> 标注：【本地实测】= 我们自己已有的数；【公开对照】= 公开论文的同类结果；【推断】= 无直接对应数。

### 1. `src/minicode/ground/prompt.py`（粗定位提示）
**做什么**：粗定位阶段改为**同时输出 bbox**（点 + 框），而不是只输出点。
**为什么**：下游三件事都以框为输入——自适应 crop 尺寸（UI-Zoomer 用框宽当 `v_intra`）、验证器打标记（GUI-Actor）、crop 包含性检查。点式输出拿不到「目标有多大」，自适应裁剪就无从谈起。
**预期**：本身不改分数（0 pp）；但**解锁第 2/4/7 条**。【公开对照】UI-Zoomer 全程用框 `b_i=[x1,y1,x2,y2]`、点击取框中心。

### 2. `src/minicode/ground/channel.py`（`TwoStageGrounder`）+ `src/minicode/ground/gate.py`
**做什么**：把「无条件跑精定位」改成 **UI-Zoomer 式不确定度门控**：粗定位采样 **N 次（T=0.9）** → 计算 `S = 两两平均 IoU + 平均 token 置信度` → `S > τ` 直接用**共识投票**的结果（不做 crop），`S ≤ τ` 才开精定位。照抄参数：**N=8、T=0.9、τ=1.0（ScreenSpot-Pro 档）、保留 top 75% 候选、投票用 IoU>0.5**。
**成本提醒**：N=8 是 8 次模型调用，对付费 API 太贵 → **先用 N=3 起步**，τ 在我们自己的样本上标定（M7 的 AC 已要求阈值必须是运行时参数并记录来源）。
**预期**：模型调用数从「固定 2 次」变成「1 + 命中率×N」；公开对照：基座 94~95% 时裁剪净收益只有 +0.0~+0.9 pp，**说明强基座上「少裁」比「多裁」更接近最优**；对 41.5% 的我们，方向上把 +3.3 pp（噪声内）变成**成本减半且分数不降**。

### 3. `src/minicode/ground/gate.py`（现 50 px 偏离判据，L184-204）
**做什么**：50 px 硬判据**降级为门控信号之一**，不再是「一票否决」；回落目标改成**共识投票结果**（N 个采样里被支持最多的那个），而不是「单个粗点」。
**为什么**：公开侧没有任何「50 px 偏离」这种口径；我们自己回放显示被丢弃的 30 条里精定位命中 18/30、粗点 12/30——是我们自己扔掉了好答案。
**预期**：**【本地实测】放宽判据净 +2.64 pp（44.81% → 47.45%，t=96 峰 47.71%）**；改成共识投票后预期再收一点「单点偶然命中」的噪声。

### 4. `src/minicode/ground/channel.py` + `src/minicode/coord/crop_refine.py`（crop 尺寸）
**做什么**：crop 边长改为**自适应**：`s = max(2γσ, m)`，σ² = 样本间位置散布 + 样本内框尺度（UI-Zoomer 式方差分解），**m = 512 px 作上下文地板**（对应 ZoomClick 的 context floor）；裁剪**在原坐标系**一次性完成（防迭代漂移）；越界向内 shift（现有 `plan_crop` 已是 shift 语义，保留）。
**为什么**：公开所有实现的末段工作面都在 **512~1280 px**（UI-Zoomer m=512、ReGround 1024、ScreenSeekeR min_crop_size=[1280,768]）；我们 192 px **低于所有公开下限**。且官方 512→1024 = +15.1 pp 说明「crop 能不能框住目标」是主项，不是「放大多大」。
**预期**：**我们自测的 36.1% crop 未框住目标应显著下降**（这是官方 +15.1 pp 的同一机制）；但注意**我们本地 crop 尺寸扫描显示 192/512/1024 的命中率差 ≤1 条样本（噪声）**，所以这一条**只有在门控先落地（第 2 条）之后**才看得出差别——否则 crop 再大也只是把噪声放大。

### 5. `src/minicode/ground/numbered_bench.py` + 新增标记绘制模块（候选编号制）
**做什么**：把编号分支从 bench 升格为**可跑的视觉通道路径**：整屏候选 → 画标记（**字母不用数字**、**不画中心**、距离变换选位、太小的区域标记外移）→ 模型**只答编号** → 落点 = 候选框物理中心（零换算）。**先做 go/no-go**：20 条样本测 minimax-m3.1-flash-free 到底能不能读懂编号。
**为什么**：SoM 的编号能力**只在 GPT-4V 上被验证过**（一手）；且候选制**不依赖粗点**，是唯一能绕开 p90 1451 px 粗误差与 36.1% 漏框的结构性办法。
**预期**：**text 子集**上落点误差 → ≈0 px（框中心取整），上限 = OCR 候选召回；**icon 子集**候选召回大概率接近 0（图标无文字）——这一步的产出就是**把「icon 候选从哪来」这个问题量化出来**。

### 6. `src/minicode/ground/candidates.py`（新增 icon 候选源）
**做什么**：给纯视觉通道补 icon 候选：轻量路线 = **OmniParser-v2 的 YOLO icon_detector（CPU/ONNX 可跑）+ caption**（注意 V2 的 caption 延迟已降 60%）；零新模型路线 = **DiMo-GUI 式模态分离**（text 渲染图 / icon 渲染图各定位一次，再让模型 Select）。UIA 候选继续作为首选（M4 已规格化，本地实测 0.0 px）。
**预期**：icon 子集是唯一可能突破 29.9% 的环节；**代价 = +1 模型 + 每帧一次检测**（记进 `bottom-drafts/05-api-cost.md` 的口径）。

### 7. `src/minicode/ground/channel.py`（新增验证步骤）
**做什么**：在最终落点前加**验证器**：把候选位置用可见标记（GUI-Actor 用**空心红圈**）画在图上，问模型「这个位置是不是指令要的那个元素」，答 False 就退到下一个候选 / 重问；用提示词自评替代训练版 verifier（M7 已列 RegionFocus 的同形参照）。
**为什么**：这直接打「挑错元素」这个第一大错误类（自测 101 条未命中里挑错占 107/…，误差中位 226 px）；公开侧 GUI-Actor 的 verifier 阈值 **γ=0.95**（ScreenSpot-Pro）/ 0.8（ScreenSpot）。
**预期**：**错元素被拒掉的收益直接体现在命中率上**；代价 = 每候选 1 次模型调用（**必须先限制候选数上限**，M4 已定 8）。

### 8. `src/minicode/eval/sspro.py`（`OFFICIAL_ROWS` + 评测脚本）
**做什么**：① 把**单段式基线 18.9%** 与 **crop 消融 512/768/1024/1280 = 25.1/34.2/40.2/40.1** 补进 `OFFICIAL_ROWS`（现在只有 5 行两段式结果，读者会误读增益）；② 把评测改成**配对设计**（复用同一次粗定位回答、只换被测那一步）+ 精确 McNemar，让第 2/3/4/5/7 条每条都产出一个**可复核的净增益 + p 值**；③ 加一条**官方口径自检**：用官方 `eval_screenspot_pro.py` 的判定跑我们同一批预测。
**为什么**：现在「两段式值不值」只能靠一次性结论；而且**我们的 41.5% 粗定位 vs 公开 API 模型（GPT-4o 0.8%、Claude Computer Use 17.1%）的差距必须先解释清楚**，否则所有绝对分数都不可比。
**预期**：产出一个明确的门控净收益数（当前 +3.3 pp 噪声内）及其 p 值；以及「我们的 41.5% 是否可信」这一前置问题的答案。

---

## 7. 没能核实的部分（据实标注）

| 想核的 | 现状 | 怎么补 |
|---|---|---|
| ScreenSpot-Pro 论文 Table 4 / Table 5 **正文** | arXiv HTML 抓取在 §4.2 后截断（`#S5.SS2`、`#S5.T3` 锚点返回同一份截断内容，已试 2 次）；PDF 端点返回 `application/pdf` 不可抓；OpenReview 有浏览器校验；`r.jina.ai` 被网络策略拦 | 本地下载 https://arxiv.org/pdf/2504.07981v1 后取表 4/5 原图；或读官方仓 `models/methods/*.py`（本地仓已有行号级记录） |
| UI-Zoomer §5 消融表（门控阈值扫描、固定 vs 自适应 crop 的逐行数） | 只读到 §4.1.3 与 Table 2（ar5iv 版截断在 §4.1.2 之后） | 读 PDF 或 ar5iv 后续节 |
| ZoomClick 的 τ / ρ / m 具体取值与逐基准表 | 只读到方法定义与摘要数字 | 读 https://arxiv.org/html/2512.05941v1 §5.3 或仓库 `config` |
| GUI-Spotlight §5.4「coarse-then-refine 流程」对照表 | 仅搜索摘要（52.8% / 50.6% / 18.5K vs 9.6M） | 读 https://openreview.net/forum?id=ZNAY3ivd62 正文 |
| OmniParser V2 的 **39.6** 与逐单元格表 | MSR 博客抓取被截断在导航区 | 读 https://github.com/microsoft/OmniParser 的 README 表 或 HF 模型卡 |
| InnerZoom / GUI-Lens / MVP / MEGA-GUI 正文表 | 仅搜索摘要 | 各读一次正文表格后再引用 |
| 我们的模型能不能读懂字母编号 | 未测 | 第 5 条的 20 条 A/B |
| 我们的 41.5% 为什么远高于公开 API 模型（0.8% / 17.1%） | 未查 | 第 8 条的官方口径自检 |

---

## 8. 参考 URL（全部可打开）

- ScreenSpot-Pro（基准 + ScreenSeekeR）：https://arxiv.org/abs/2504.07981 · https://arxiv.org/html/2504.07981v1 · https://arxiv.org/pdf/2504.07981v1 · https://likaixin2000.github.io/papers/ScreenSpot_Pro.pdf · https://gui-agent.github.io/grounding-leaderboard/ · https://github.com/likaixin2000/ScreenSpot-Pro-GUI-Grounding
- **UI-Zoomer（选择性 zoom 的主证据）**：https://arxiv.org/abs/2604.14113 · https://arxiv.org/html/2604.14113v1 · https://ar5iv.labs.arxiv.org/html/2604.14113 · https://zju-real.github.io/UI-Zoomer · https://github.com/ZJU-REAL/UI-Zoomer
- **DiMo-GUI（模态分离 + 裁剪有害的一手对照）**：https://arxiv.org/abs/2507.00008 · https://arxiv.org/html/2507.00008v2 · https://aclanthology.org/2025.emnlp-main.1334/
- **ZoomClick（zoom 的四个关键性质 + 上下文地板）**：https://arxiv.org/abs/2512.05941 · https://arxiv.org/html/2512.05941v1 · https://github.com/Princeton-AI2-Lab/ZoomClick
- **GUI-Actor（无坐标 grounding + 验证器配方）**：https://arxiv.org/abs/2506.03143 · https://arxiv.org/html/2506.03143v1 · https://microsoft.github.io/GUI-Actor/
- **OmniParser（检测+描述+SoM 的候选制模板）**：https://arxiv.org/abs/2408.00203 · https://arxiv.org/html/2408.00203v1 · https://github.com/microsoft/OmniParser · https://www.microsoft.com/en-us/research/articles/omniparser-v2-turning-any-llm-into-a-computer-use-agent/ · https://huggingface.co/microsoft/OmniParser-v2.0
- **Set-of-Mark（编号画法规则 + 模型相关警告）**：https://arxiv.org/abs/2310.11441 · https://arxiv.org/html/2310.11441v2 · https://github.com/microsoft/SoM · https://som-gpt4v.github.io/
- **DRS-GUI（训练无关动态区域搜索，+14%）**：https://arxiv.org/abs/2605.15542 · https://arxiv.org/html/2605.15542v1
- GUI-Spotlight（【二手】）：https://arxiv.org/abs/2510.04039 · https://openreview.net/forum?id=ZNAY3ivd62
- Zoom Consistency Loss（【二手】）：https://icml.cc/virtual/2026/69346
- MVP（【二手】）：https://openaccess.thecvf.com/content/CVPR2026/papers/Zhang_MVP_Multiple_View_Prediction_Improves_GUI_Grounding_CVPR_2026_paper.pdf
- MEGA-GUI（【二手】）：https://arxiv.org/abs/2511.13087 · InnerZoom（【二手】）：https://arxiv.org/abs/2606.30084 · GUI-Lens（【二手】）：https://arxiv.org/abs/2608.03270

**本地对应文件**：`src/minicode/eval/sspro.py`（官方 5 行 + 官方仓代码行号；补基线与 crop 消融的位置）· `src/minicode/ground/channel.py`（`DEFAULT_CROP_WIDTH_PX = 192`，L162-163；crop 尺寸扫描表 L145-153；`TwoStageGrounder`）· `src/minicode/coord/crop_refine.py`（crop 规划，默认 512，L58-59；shift 语义 L118-149）· `src/minicode/ground/gate.py`（50 px 偏离判据与 crop 系数换算 L184-204）· `src/minicode/ground/numbered_bench.py`（编号分支 + 候选召回口径；crop 扫描尺寸 L664-669）· `src/minicode/ground/candidates.py`（UIA / OCR 候选源）· `src/minicode/ground/prompt.py`（粗定位提示）· `docs/tickets/M4-candidate-selection.md` · `docs/tickets/M7-refine-gate-and-coord-convention.md`

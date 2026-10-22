# r-s2-false-hit —— 假命中归零：公开实现调研 + 可照抄的判据

> 调研线：**S2 假命中**（crop 里没有目标时，模型仍然给点；20 条 / 19 条）
> 产物：`docs/research/r-s2-false-hit.md` ｜ 日期：2026-10-22 ｜ 模型：`minimax-m3.1-flash-free`（OpenCode zen/go，Anthropic-messages 协议）
> 证据强度标注：**【一手】**=官方仓库/论文原文/本机实测；**【二手】**=检索摘要转述，未逐字核对原文；**【推断】**=由证据推导，需本机跑批验证。

---

## 0. 一句话结论

**「假命中归零」不是一个精度题，是一个正确性题 —— 而且归零的唯一正确姿势是「再搜一次」，不是「回落粗定位」。**
本机实测：**把 20 条假命中全部识别出来并回落到粗定位，命中率一条都不涨（g2 净 Δhit = +0，g3 = −1）**，因为 20 条里 **19 条的粗定位本身也是错的**（95%）—— 回落只是从一个错答案换到另一个错答案。
而精定位整体是**净赚 +27 条（g2）/ +41 条（g3）**（修好 38/47 条，弄坏 11/6 条）→ **砍精定位来归零是净亏**。
要既归零又不误伤正确答案，唯一有公开数字支撑、且**不使用「超阈值就回落」**的机制是：
**① 多视图多候选 + 空间聚类取最密簇（consensus 投票，MVP）→ ② 把候选点在图上标出来让模型自己选（RegionFocus）→ ③ 模型选不出 / 选中的是错元素时，用「上一个候选是错的」提示做整屏再搜（而不是回落粗定位）。**
另有一条硬约束已被本机实测钉死：**这条网关拿不到 logprobs**（两个协议都试了）→ 置信度只能来自**空间自洽性**，不能来自 token 概率。

---

## 1. 先把账算清：假命中到底值多少钱（本机实测）

### 1.1 假命中的构成

来源：`runs/g2-eval-final/sspro-report.json`（1536×960 档）与 `runs/g3-eval-2016/sspro-report.json`（2016×1260 档），`outcomes.always` 臂，判定 = 官方 bbox 命中。

| 量 | g2-1536 | g3-2016 | 口径 |
|---|---:|---:|---|
| 样本 N | 183 | 183 | 同一批 183 条（种子 20251020） |
| 开过 crop 的样本 | 176 | 176 | `crop_contains_target is not None` |
| crop **完整含住** GT 框 | 140 | 152 | 含住率 79.5% / 86.4% |
| crop **没含住**（= 窗口里没有目标） | 36 | 24 | `crop_contains_target is False` |
| └ **假命中**（没含住 **且** 精定位点被采纳） | **20** | **19** | `crop_contains_target is False and landing_source == "refined"` |
| └ 逃逸口正确拒答（没含住 **且** 模型说「没有」） | 16 | 5 | `fallback_cause == "no_output"` 且落在 crop_miss 里 |
| └ 逃逸口**误拒**（含住了 **却** 模型说「没有」） | 9 | 6 | `fallback_cause == "no_output"` 且落在 crop_contains 里 |

**没含住的原因（`crop_miss_reason`）：g2 = {`coarse-picked-wrong`: 35, `out-of-bbox`: 1}；g3 = {`coarse-picked-wrong`: 23, `out-of-bbox`: 1}。**
→ **几乎 100% 的「窗口里没有目标」不是裁剪算错，是粗定位选错了元素。**【一手】

### 1.2 三条决定性数字

1. **假命中里 95% 的粗定位也是错的**（g2：19/20；g3：18/19）→【一手】
2. **「识别出全部假命中 → 回落粗定位」的精度天花板 = 0**（g2 净 Δhit = **+0**，g3 = **−1**；即 g2 是 1 条假命中本身命中、另一条粗定位命中，换来换去抵平；g3 还倒亏一条）→【一手】
3. **精定位整体净赚 +27（g2）/ +41（g3）**（修好 38/47，弄坏 11/6）→【一手】

### 1.3 结论（这决定了后面所有设计）

- 假命中 ≠ 精度损失。**它是「粗定位选错元素」的第二次发作**（模型在错窗口里又自信地指了一个错元素）。
- 因此：**「假命中 → 0」若用「回落粗定位」实现，命中率一条都不涨，还会让 11 条本来被精定位修好的样本重新变错**（因为回落判据必然误伤，见 §3.2 的实测）。
- 假命中的**真正代价**在真机 e2e：点到一个「看起来很像但不是」的控件 → 任务静默走偏（这正是 wave1/wave2 里 C-1/C-2 类失败的机理），而**离线 ScreenSpot-Pro 分数完全看不见这个代价**。
- 归零的正确姿势 = **把「错窗口」变成「再搜一次的机会」**。

### 1.4 本机侧的另一个硬事实：logprobs 拿不到（本轮实测，非引用）

用仓库自己的 `build_headers` 形态直接打网关（`src/minicode/model/client.py:148`）：

| 请求 | HTTP | 返回体里的 logprobs |
|---|---:|---|
| `POST /v1/messages` + `{"logprobs": true, "top_logprobs": 5}` | **200** | **顶层键只有** `content/id/model/role/stop_reason/type/usage` → **无 logprobs 字段**（参数被静默忽略） |
| `POST /v1/messages` 基线（不带 logprobs） | 200 | 同上，用于排除「参数导致报错」 |
| `POST /v1/chat/completions` + `logprobs:true` | **200** | `choices[0]` 的键只有 `finish_reason/index/message` → **`logprobs: null`** |

→ **通用 API 路线上拿不到 token 概率**（与公开事实一致：Anthropic Messages API 从未支持 logprobs，见 §2.5）。
→ 置信度**只能**来自：**多次采样 + 空间自洽性**（下一节 MVP 的做法）。【一手·本机】

---

## 2. (a) 公开方法怎么保证输出「确实落在目标上」

### 2.1 RegionFocus（ICCV 2025）—— **验证 + 候选聚合**，与我们的形状最像 【一手】

论文：[arXiv:2505.00684](https://arxiv.org/abs/2505.00684) ｜ 代码：[github.com/tiangeluo/RegionFocus](https://github.com/tiangeluo/RegionFocus)

原文（§3.2 Trigger Condition，已逐字核对 fetch 到的 HTML）：

> "The second type of trigger occurs in cases where environmental interaction isn't possible and we are dealing with static screenshots (e.g., ScreenSpot-Pro scenario). Here, **the VLM evaluates predicted actions, shifting its role from merely making predictions to explicitly evaluating the correctness of actions.** Although the VLM's judgments aren't always perfectly accurate, they significantly help identify and mitigate errors shown in our experiments."

原文（ScreenSpot-Pro 评测段的 VLM judge，已逐字核对）：

> "when the agent predicts a point, we will **highlight that point in the input screenshot with pink-star landmarks and ask the model itself to assess the correctness of that point**. If deemed incorrect, we initiate the RegionFocus process."

三条可直接照抄的做法：
1. **image-as-map**：把候选点画成**粉红五角星**标在截图上，让模型在**带标记的图**上做选择，而不是让模型在裸图上重新生成坐标（§3.3）。
2. **Action Candidate Prediction**：不是一次问一个点，而是**对每个候选子区域各自独立地问一次**，每个区域独立产候选动作。
3. **Action Aggregation**：把各子区域的候选坐标**画成标记**，再让模型在标记图上**选一个**（原文："we visually mark the candidate action coordinates on the snapshot … this significantly reduces the model's workload"）。

公开数字（README 与论文摘要逐字核对）：
- **ScreenSpot-Pro 新 SOTA 61.6%**（Qwen2.5-VL-72B + RegionFocus）。README 里 `summarize_results.py` 的样例输出最后一列正是 `61.6 1581`。
- 摘要原文："performance gains of **28+%** on Screenspot-pro and **24+%** on WebVoyager"（另检索摘要给出 UI-TARS-72B +31.8% / Qwen2.5-VL-72B +28.9% 的分模型拆分 →【二手】）。
- Table 1 里 UI-TARS-7B + RegionFocus 的 OS 平均 **45.8→55.2**、CAD **63.3→69.5**（逐字核对 fetch 到的表格）。

**为什么它正好命中我们的病**：它把「模型说没找到」换成「**模型看自己标的点，判断对不对**」。这是**换一个任务**（生成 → 评估），不是给生成结果加一个阈值 → **结构上就不会出现「阈值误伤正确答案」**。

### 2.2 MVP（CVPR 2026）—— **多视图 + 坐标聚类**，给「先出框/多候选再取代表」提供算法 【一手】

论文：arXiv **2512.08529**（[ar5iv 全文](https://ar5iv.labs.arxiv.org/html/2512.08529) ｜ [arXiv](https://arxiv.org/abs/2512.08529)）｜ 代码：[github.com/ZJUSCL/MVP](https://github.com/ZJUSCL/MVP)

原文（已逐字核对 ar5iv 全文）核心：

> "while individual view predictions may be unreliable, they usually exhibit spatial patterns that the **incorrect coordinates tend to scatter arbitrarily whereas the correct ones consistently fall within the target bounding box region**. By clustering predictions from diverse views and identifying the densest cluster, MVP effectively distinguishes reliable coordinates from outliers."

**Algorithm 2（可直接照抄的聚类规则）**：
- 距离度量 `d(p_i,p_j) = sqrt((x_i-x_j)² + (y_i-y_j)²)`；
- 贪心聚类：`‖p − center(G)‖₂ ≤ τ` 则并入 G，`center(G) = 簇内均值`；反复直到簇不再变化；
- 最终取**最大簇** `G* = argmax |G_k|`，落点 = `G*` 内所有点的**质心**；大小并列时用视图排名打破。

公开数字（逐字核对）：
- **加 28 像素边框**（远小于原图分辨率）→ **7.3% 的正确→错误、7.8% 的错误→正确**，两次预测**平均坐标位移 193 像素**（"far exceeds the size of typical UI elements"）。→ 单次预测的抖动幅度比目标本身大一个量级。
- 单次 49.8% vs「两次里至少一次对」**57.5%**（GTA1-7B）→ 抖动掩盖了能力。
- ScreenSpot-Pro 提升：**UI-TARS-1.5-7B 56.1%、GTA1-7B 61.7%、Qwen3VL-8B 65.3%、Qwen3VL-32B 74.0%**（32B 从 55.3 → 74.0）。
- 抖动随**分辨率升高**、**目标变小**而加剧。

**与我们的契合点**：我们的模型拿不到 logprobs，但**空间聚类正好是 logprob 的替代品**——而且**它输出的是「最密簇的质心」，不是「离粗定位多远」**，所以它**不会误伤「正确但离粗定位远」的样本**（那正是当前 `deviation` 判据误伤的形态，见 §3.2）。
**本地佐证**：仓库自己的 temperature 探针（`runs/m2-temperature-probe/probe*.json`）显示 —— **原文一致率只有 0.1（10 次 10 个不同答案），但落点的空间自洽性高得多**：
`probe-text.json`：9/10 落在中位数 32 px 内（p50 = 4.8 px）；`probe.json`：7/10 在 32 px 内，但另有 3 个散到 816 px 外（**多峰**）。→ **文本一致率是坏的度量，空间聚类才是好的一致性度量。**【一手·本机】

**MVP 的「Attention-Guided View Proposal」我们用不了**（要 cross-attention，通用 API 拿不到）；但**固定偏移的多视图**是现成替代 —— RegionFocus 原文就是这么做的：*"We employ a heuristic approach, defining bounding boxes with fixed dimensions and aspect ratios (e.g. 0.5×0.5 width by 0.5×0.5 height) centered around the focal point."*【一手】

### 2.3 GUI-HalluBench（CVPR 2026）—— **把「模型对不存在的目标会怎么回答」变成可测量的口径** 【一手（框架）/【二手（数字）】

论文：[CVF Open Access](https://openaccess.thecvf.com/content/CVPR2026/html/Zhang_Exposing_and_Evaluating_Hallucinations_for_GUI_Grounding_CVPR_2026_paper.html) ｜ 代码：[github.com/aibench/GUI-HalluBench](https://github.com/aibench/GUI-HalluBench)

已逐字核对的摘要（fetch 到 HTML）：

> "we expose two major types of hallucinations in GUI grounding: 1) **Confusion Hallucination**, where distractor elements are mistakenly selected, and 2) **Fabricated Hallucination**, where **nonexistent elements are hallucinated with plausible coordinates**."

- 基准：~2000 条中英双语（1000 EN / 1000 ZH），约 **40% confusion / 60% fabrication**；幻觉标签 ≥3 人复核，三方一致率 96.7%。
- **度量**：Fabricated 子集用 **Rejection Rate `RR = N_rej / N_fab`**；Confusion 子集用 Location Accuracy `LA`。
- 公开基线数字（Hallucination Avg）：**GPT-4o (w/ grounding) 57.2%、Claude Computer Use 56.6%、Gemini-2.0 Project Mariner 60.5%**；Qwen3-VL-8B (HFT) 解析 72.3% / 幻觉 73.0%；InternVL3.5-8B (HFT) 71.9% / 69.1%；**HFT 把幻觉均值 66.0% → 73.0%**；**PGP（parsing-guided prompt，纯训练无关）只 +1~2%** →【二手】（PDF 抓取被 content-type 拒绝，数字来自检索摘要转述）。
- 一个反直觉但有用的结论：**闭源大模型（GPT-4o / Claude / Gemini）在「抗幻觉」上并不领先**；抗幻觉主要靠**解析能力**与**专门的拒答训练**。

### 2.4 SafeGround / Lacuna —— 一个适用、一个不适用

- **SafeGround**：推理时**重复采样坐标，样本离散就拒答**。但原文自陈的失败模式是 —— **「模型一致地错」时，低方差的错答案看起来很自信** →【二手】。
  **我们的假命中正是「一致地错」**（模型在错窗口里每次都指同一个错元素）→ **离散度拒答对我们的 20 条基本无效**（这也是为什么 §1.2 的「回落粗定位」收益为 0）。这条公开的失败模式，直接排除了「用离散度当拒答阈值」这条路。
- **Lacuna "Scores That Can Say 'Not Here'"**：**排序损失 + 逐点 BCE** 的混合损失，让 `max_i σ(s_i)` 成为**标定过的 present/absent 概率**，再用单阈值拒答 →【二手】。
  → **不适用**（它是**训练**方法，要求能训 grounding 模型；我们是通用 API 模型，不能训）。
  但**它的评估口径适用**：必须**把「拒答」与「误拒」分开量**（present 目标被误拒 = 拒答侧 FP）。→ 已写进 §7 最小改动第 7 条。

### 2.5 logprobs 不可得（公开佐证）

- Anthropic Messages API **无 logprobs**：`spiceai` 的 Anthropic provider 明确实现 `refuse_unsupported_logprobs()`，注释写 *"Anthropic's Messages API has no `logprobs` equivalent: it returns no per-token probabilities"*，带 `logprobs:true` 直接返回 **400 `invalid_request_error`**；`genaiscript` 的 Anthropic provider 把 logprobs 列为 **ignored**；StackOverflow 79299548 是经典问法 →【二手】。
  → 与 §1.4 的**本机实测一致**：我们这条链路两个协议都拿不到。
- → **路径排除**：想要 token 概率就得换后端（自建 vLLM/Ollama），不是本轮该做的事；**本轮用空间自洽性代替**。

### 2.6 官方 ScreenSpot-Pro 通用 API 协议（负出口的官方写法）

- EvalScope 官方适配器 `evalscope/benchmarks/screenspot_pro/screenspot_pro_adapter.py`（[仓库](https://github.com/modelscope/evalscope) ｜ [文档](https://evalscope.readthedocs.io/en/latest/benchmarks/screenspot_pro.html)）的提示词要求 **单点输出**、*"Do not output a bounding box"*、末行 `Answer: [x, y]`（归一化 0~1）；坐标量纲按 `[0,1] / ≤1000 / 像素` 三档自动判定；**答案行之前被截断 → 视为无预测、得分 0，而不是从推理里捏造坐标** →【二手】。
- 检索到的 Gemini-3-Pro 系统提示词给出了**明确的空输出负出口**："*If you cannot find the element, output an empty list: `[]`*"，"*If no element found, output: `[]`*" →【二手】。
- 仓库自己已经引用了这条协议作一手依据（`src/minicode/ground/prompt.py:517` 的注释指向 evalscope 适配器）。
- **对本线的意义**：**官方协议本身就允许「我不给答案」**，所以「负出口」不是我们的发明，是合规动作；我们要改的是**负出口的精度**（当前 g2 precision 64% / recall 44.4%），不是取消它。

---

## 3. (b)「先出框、再取框中心」这条路的拒答能力

### 3.1 框能不能当「我看到了这个元素」的证据？—— 分三种框分别看

| 框的类型 | 能否当证据 | 依据 |
|---|---|---|
| **粗定位框**（整屏，已出框） | **不能单独当证据** | 我们已让粗定位出框 + 自报文字；它的框在假命中里 95% 也是错的（§1.2） |
| **精定位框**（crop 内） | **目前不存在** | 现在的 refine 只输出点（`refine_prompt` 问 `{"x":..,"y":..}`）；改成出框才能做跨阶段一致性 |
| **框的几何异常**（退化 / 越界 / 面积异常） | **可以当弱证据** | 越界已能查（`FallbackCause.OUT_OF_BOUNDS`）；退化（`bbox=[x,y,x,y]`）仓库已能解析（`prompt.py:_box_of` 注释「允许退化」） |

官方口径：**ScreenSpot-Pro 论文题注 —— "For models inferencing boxes, we consider the center point of the generated box as the prediction"**（仓库 `ground/prompt.py:141-143` 已逐字引用）。→ 出框 → 取框中心，**是官方做法**，不是我们的怪癖。

### 3.2 本机实测：拿框做「硬门」会误伤多少（决定性）

用两批报告离线重放（坐标已统一到物理表面空间：粗定位框在**模型输入空间**，按 `coarse_point / coarse_point_model` 换算到物理），判据 = **精定位落点是否落在粗定位框内**：

| 判据 | g2-1536：抓到假命中 / 弄坏正确精定位 | g3-2016：抓到 / 弄坏 | g2 净 Δhit | g3 净 Δhit |
|---|---:|---:|---:|---:|
| 精定位点**落在**粗定位框内的比例 | 假命中 **25.0%** (5/20) vs 正确精定位 **56.8%** (54/95) | 21.1% vs 53.9% | — | — |
| R1：点**出框**就拒答 | 15/20 ｜ 41/95 | 15/19 ｜ 53/115 | **−26（−14.21 pp）** | **−38（−20.77 pp）** |
| R2：出框 + 50 px 容差 | 12/20 ｜ 19/95 | 8/19 ｜ 25/115 | −13（−7.10 pp） | −20（−10.93 pp） |
| R3：出框 + 150 px 容差 | 9/20 ｜ 11/95 | 6/19 ｜ 13/115 | −7（−3.83 pp） | −11（−6.01 pp） |
| R4：粗定位自报文字为空（仅 text 类） | 6/20 ｜ 5/95 | 1/19 ｜ 6/115 | −3（−1.64 pp） | −5（−2.73 pp） |
| R6 = R3 ∪ R4 | 11/20 ｜ 16/95 | 7/19 ｜ 17/115 | −10（−5.46 pp） | −14（−7.65 pp） |

【一手·本机】

**读法（很重要）**：
- R1 能抓 15/20 假命中，但**弄坏 41 条本来命中的精定位** → **净亏 14.2 pp**。这就是「超阈值就回落会误伤正确答案」的定量版本。
- R2/R3 把容差放大到 50/150 px，误伤降到 19/11 条，但假命中也只抓到 12/9 条 → **净亏 7.1 / 3.8 pp**。**放宽阈值并不能换来净正** —— 因为「假命中」与「正确但离粗定位远」在几何上**本来就重叠**（离框距离 p50：假命中 94.9 px vs 正确命中 0.0 px，但 p90 分别是 1054 px 与 195 px）。
- **结论：几何硬门（框/距离/阈值）在本线上是死路。** 唯一例外是 §1.2 已经算过的：**识别出假命中后不要回落粗定位，而要再搜一次** —— 那时几何判据只用来**触发**，不用来**否决**。

### 3.3 那框到底能怎么用？——「跨阶段一致性」，而且只当**触发器**

粗定位已经出框 + 自报文字；让**精定位也出框 + 自报文字**（与粗定位同构），就能拿到一个**不依赖阈值的二元证据**：

- **跨阶段 IoU**：`IoU(coarse_box, refine_box)`。两者指的是同一个元素时 IoU 高；精定位跑到别的元素上时 IoU ≈ 0。**这是一个「两个独立阶段互相印证」的证据，不是「离粗定位多远」的阈值** —— 它不会惩罚「正确但离得远」的样本（那种情况两个框仍然重合）。
- **跨阶段文字一致**：粗定位自报文字 vs 精定位自报文字（同元素 → 同文字）。
- **退化和面积异常**：框空 / 框退化成点 / 框面积异常小或异常大 → 弱证据。

⚠️ **这条本轮只埋点、不改落点**（与仓库里 deviation 判据「降级为记录器」的既有纪律一致 —— 刚刚实测证明硬门净亏，不该再上一次当门）。**预期本轮 0 分变化**；它的价值是给下一轮（拿到 refine 框后）一个**不误伤**的判据底座。

---

## 4. (c) 通用 API 模型上的 confidence / uncertainty

| 手段 | 在我们这条链路上可用吗 | 依据 |
|---|---|---|
| **token logprob** | ❌ **不可用** | §1.4 本机实测（`/v1/messages` 无字段；`/v1/chat/completions` → `logprobs: null`）；公开佐证见 §2.5 |
| **模型自报 confidence**（问模型「你有多确定」） | ⚠️ **不能当概率** | 公开结论：模型写出来的「confidence」是**文本**，不是 softmax 分数；校准要靠训练（§2.4 Lacuna）→【二手】。我们**不把它当门**，只当**旁证**落盘 |
| **多次采样 + 空间聚类（consensus 投票）** | ✅ **可用，且是主力** | MVP：最密簇质心；本地探针证明「文本一致率 0.1 但空间一致率高」（§2.2）→【一手】 |
| **多视图（不同裁剪窗口）投票** | ✅ **可用** | RegionFocus：固定比例子区域各自独立预测再聚合；MVP：多视图 + 聚类 →【一手】 |
| **采样离散度当拒答阈值** | ❌ **对我们无效** | SafeGround 自陈失败模式「一致地错」；我们的 20 条假命中正是「一致地错」（§2.4）→【二手】 |

**结论**：置信度 = **「多候选的空间聚类紧密度」+「模型在标记图上是否选中它」**。两者都不是「距离阈值」，都不误伤「正确但离得远」的答案。

---

## 5. (d) 公开报告：模型对「不存在的目标」会怎么回答

| 公开事实 | 数字 | 对我们的含义 |
|---|---|---|
| 闭源大模型在 fabricated（目标不存在）子集上的拒绝率只有 **56.6%（Claude Computer Use）/ 57.2%（GPT-4o w/ grounding）/ 60.5%（Mariner）** | GUI-HalluBench Hallucination Avg【二手】 | **别指望「问一句，没找到就说没有」能到 0** |
| 我们的逃逸口在 crop_miss 上的 **recall 只有 44.4%（g2）/ 20.8%（g3）**，precision **64.0% / 45.5%** | 本机【一手】 | 与上面的闭源模型同一量级；**光靠负出口归零不可行** |
| **GPT-4o 在 ScreenSpot-Pro 上只有 0.9~1.3%**、Llama-3.2-11B-Vision 在约 **42%** 的条目上吐「默认坐标」 | 检索转述【二手】 | 通用 API 模型的默认行为就是「总给一个点」；这正是「必须给目标不存在的样本单独造评测集」的原因 |
| 训练无关的解析引导提示（PGP）只带来 **+1~2%**；抗幻觉要靠**解析能力**或**专门的拒答训练**（HFT，66.0→73.0） | GUI-HalluBench【二手】 | 我们改不了训练 → 只能走**推理期机制**（验证 + 投票 + 再搜） |
| 官方通用 API 协议本身允许空输出（`[]` / 截断=0 分） | EvalScope【二手】 | 「拒答」是合规动作，问题只在精度 |

**→ 本轮必须自造「目标不存在」的评测子集**（GUI-HalluBench 的做法），否则「假命中归零」这个目标**在离线评测里根本不可见**（ScreenSpot-Pro 每个样本都保证目标存在）。这是 §7 最小改动第 7 条。

---

## 6. 方案：三段式（全部推理期、训练无关、适配通用 API 模型）

```
                ┌─ 粗定位（整屏，出框 + 文字）────────────────────────┐
  截图 ────────▶│  MVP 式多视图：裁 N 个固定比例窗口各自问一次        │
                │  → 空间聚类 → 取最密簇质心 = 粗定位点 + 粗定位框     │
                └───────────────────────────┬───────────────────────┘
                                            │ 粗定位点（错 → 根因在这里）
                                            ▼
                ┌─ 精定位（crop 内，多候选）────────────────────────┐
                │  对 crop 内的 N 个子区域各自问一次 → N 个候选点     │
                │  → 空间聚类 → 最密簇 + 簇占比（= 置信度）           │
                └───────────────────────────┬───────────────────────┘
                                            │ 候选集 + 簇占比
                                            ▼
                ┌─ 聚合（标记图上让模型选）──────────────────────────┐
                │  把候选点画成标记（RegionFocus 的 image-as-map）      │
                │  → 问「哪个标记是『<目标描述>』？」                  │
                │  · 选中 → 落点 = 该候选的物理坐标（终态）           │
                │  · 选不出/都不像 → 不回落粗定位，改走【再搜】        │
                └───────────────────────────┬───────────────────────┘
                                            │ 判定不可信
                                            ▼
                ┌─ 再搜（整屏第二遍，带「上一个候选是错的」）───────────┐
                │  提示里带上：上一个候选的自报文字 + 目标描述          │
                │  → 得到新落点（不是粗定位点）                        │
                │  → 仍不可信 → 落回粗定位（最后一档）                 │
                └───────────────────────────────────────────────────┘
```

**为什么这个形状不误伤**：
- 「多候选 + 聚类」**取的是质心**，不是「离粗定位多远」→ 正确但离得远的样本不受罚。
- 「标记图上选」**换了任务**（评估而非生成）→ RegionFocus 原文：模型「reliably assess whether the click points are correct」，但「judgments aren't always perfectly accurate」→ 需要**用评测量化它**，不能口头假设。
- 「再搜」**不是回落** → 假命中样本本来就有一次全新的机会（而不是被钉死在错答案上）。
- 每一档都有**可落盘的中间量**（簇占比、被选中的候选编号、再搜是否发生）→ 阈值可以在数据上调，不用猜。

---

## 7. ≤8 条最小改动（按投入产出排）

> 每条：改哪个文件 / 做什么 / **预期哪个数变多少** / 怎么验证。
> 所有「预期」凡标【推断】的，**必须跑批实测**后才能对外报。

| # | 文件 | 做什么 | 预期哪个数变多少 | 怎么验证 |
|---|---|---|---|---|
| 1 | `src/minicode/ground/channel.py`（`_locate_by_point` 精定位段）+ `src/minicode/coord/crop_refine.py` + `src/minicode/ground/prompt.py`（新增 `refine_multi_prompt`） | 精定位从「一次一个点」改成「**N 个固定偏移子区域各问一次 → N 个候选点 → 空间聚类（τ=32 物理 px，MVP Algorithm 2）→ 取最密簇质心**」；把**簇占比**落盘为 `refine_cluster_ratio` | 命中 **+3~+8 条（+1.6~+4.4 pp）**【推断，依据 MVP 的 +6~+18 pp 与本地净 +27 的量级】；`refine_cluster_ratio` 落盘 | 与 g2 **同 183 条同种子**跑新档，落 `runs/g4-eval-183/sspro-report.json`；配对 mid-p McNemar（沿用 `eval/stats.py`）；**簇占比 vs 命中与否**的 AUC 落盘 |
| 2 | `src/minicode/ground/prompt.py`（新增 `aggregate_candidates_prompt`）+ `src/minicode/ground/channel.py` | **RegionFocus 式聚合**：把候选点画成标记标在 crop 上，问模型「哪个标记是『<目标描述>』？答编号，没有就答 0」；**答 0 = 不可信** → 走第 3 条 | 假命中 **20 → ≤5**【推断】；命中 **+1~+3 条**【推断】 | 同上跑批；新增埋点 `refine_chosen_candidate`（编号 / 0）；**答 0 的样本里，真命中率应显著低于选中的样本**（离线可核） |
| 3 | `src/minicode/ground/channel.py`（新增「再搜」分支）+ `src/minicode/ground/prompt.py`（`whole_screen_prompt` 加「上一个候选是错的，请找一个不同的元素」+ 候选自报文字） | **假命中不再回落粗定位**，改为**整屏再搜**（复用已存在的 `whole_screen` 通道与 `LANDING_SOURCE_WHOLE_SCREEN`；落盘新增 `landing_source="rescreen"`） | 假命中 **20 → 0**（走再搜即不再被采纳）；命中 **+2~+5 条（+1.1~+2.7 pp）**【推断，按再搜命中 15~25% 估】 | 单独在 20 条假命中上跑再搜，报告 `rescreen_hit_rate`；跑批里 `landing_source` 分布应出现 `rescreen` |
| 4 | `src/minicode/ground/channel.py`（粗定位段）+ `src/minicode/ground/prompt.py` | **粗定位也上多视图 + 聚类**（MVP 原样搬到粗定位级；固定比例子区域，RegionFocus 的 0.5×0.5 比例） | 粗定位命中 **41% → 48~52%**【推断】；crop 含住率 **79.5% → 85~88%**【推断】；假命中 **20 → 12~15** | 同 183 条配对跑批；`coarse_only` 臂就是粗定位单独指标（现 75/183）→ 直接对比 |
| 5 | `src/minicode/ground/prompt.py`（`refine_prompt` 改为出框 + 自报文字，与 `coarse_prompt` 同构）+ `src/minicode/eval/sspro.py`（新增埋点） | 让精定位**出框 + 文字**，拿到**跨阶段 IoU** 与**跨阶段文字一致性**（§3.3）—— **本轮只埋点、不改落点** | **0 分变化**（刻意不设门）；产出「IoU 分布：假命中 vs 正确精定位」两组分布 | 跑批后离线算两组 IoU 分布 + 分层（icon/text）；**只有两组分布不重叠才允许下一轮当门**（这是 §3.2 教训） |
| 6 | `src/minicode/ground/channel.py`（`fallback_cause == no_output` 分支，`channel.py:720-736`） | **负出口不再直接回落粗定位**：改成「不可信 → 走第 1~3 条的聚类/聚合/再搜」；回落只作最后一档 | 消掉**逃逸口误拒**损失（g2 9 条 / g3 6 条）→ 潜在 **+1~+3 条**【推断】 | 跑批后比 `fallback_cause` 分布：`no_output` 应下降、其对应样本的命中率应上升 |
| 7 | `src/minicode/eval/sspro.py`（`_telemetry_summary`）+ `src/minicode/eval/stats.py` + `src/minicode/eval/sspro.py`（新增「目标不存在」子集构造） | ① 加 **拒答 precision/recall/误拒率**（对齐 GUI-HalluBench 的 RR + FPR 口径）；② 加 **假命中根因拆分**（`crop_miss_reason` 已有：`coarse-picked-wrong` 35/36）；③ **自造「目标不存在」子集**（从同图取错位 crop 作为「目标不在窗内」的负样本） | **0 分变化**；把「假命中」从**看不见的量**变成**可核口径**；③ 才有资格宣称「拒答 precision ≥ X」 | 跑批后报告新增字段；③ 单独产 `runs/s2-refusal-eval/`（负样本子集） |
| 8 | `src/minicode/model/client.py` + `src/minicode/ground/channel.py` + `docs/01-现状.md` | 把「**本网关拿不到 logprobs**」（§1.4 两个协议的实测原文与返回体）写成**明文结论**，并**停用任何 logprob 依赖**的写法 | **0 分变化**；避免下一轮再花时间找不存在的置信度 | 单测：请求体里出现 `logprobs`/`top_logprobs` 时**断言失败**（`test_model_client.py` 加一条）；文档写明实测返回体 |

**优先级说明**：第 1、2、3 条是**归零的主力**（互相咬合）；第 4 条是**根因**（粗定位错了 → 窗口必然错）；第 5、7 条是**下一轮的地基**（把判据从「猜」变成「量」）；第 6、8 条是**止损与记账**。
**明确不做**：不引入「离粗定位多远 → 回落」的硬门（实测净亏 3.8~20.8 pp，§3.2）；不引入「采样离散度 → 拒答」阈值（对「一致地错」无效，§2.4）；不引入 logprob 依赖（§1.4）；不砍精定位（净赚 +27/+41，§1.2）。

---

## 8. 一手来源清单（全部可直接打开）

| # | 来源 | URL | 用到的关键内容 |
|---|---|---|---|
| S1 | RegionFocus 论文（ICCV 2025） | https://arxiv.org/abs/2505.00684 ｜全文 https://arxiv.org/html/2505.00684v2 | VLM judge 触发条件原文；「28+% on Screenspot-pro」；Table 1 |
| S2 | RegionFocus 代码/模型卡 | https://github.com/tiangeluo/RegionFocus ｜ https://huggingface.co/showlab/ShowUI-2B | README 逐字给出 **61.6 / 1581** 的 SOTA 与 `summarize_results.py` 用法 |
| S3 | MVP 论文（CVPR 2026） | https://ar5iv.labs.arxiv.org/html/2512.08529 ｜ https://arxiv.org/abs/2512.08529 ｜ https://github.com/ZJUSCL/MVP | Algorithm 2 聚类规则原文；28-px 边框 → 7.3%/7.8% 翻转、193 px 位移；49.8% vs 57.5%；56.1/61.7/65.3/74.0 |
| S4 | GUI-HalluBench（CVPR 2026） | https://openaccess.thecvf.com/content/CVPR2026/html/Zhang_Exposing_and_Evaluating_Hallucinations_for_GUI_Grounding_CVPR_2026_paper.html ｜ https://github.com/aibench/GUI-HalluBench | Confusion vs Fabricated 分类；RR/LA 口径；基线与 HFT/PGP 数字（数字为【二手】） |
| S5 | Jedi / OSWorld-G | https://arxiv.org/abs/2505.13227 ｜ https://arxiv.org/html/2505.13227v3 ｜ https://osworld-grounding.github.io | 负样本/拒答类数据与 OSWorld-G 评测口径 |
| S6 | EvalScope ScreenSpot-Pro 适配器 | https://github.com/modelscope/evalscope ｜ https://evalscope.readthedocs.io/en/latest/benchmarks/screenspot_pro.html | 官方通用 API 提示词、`Answer:` 行解析、量纲三档判定、截断=0 分、坐标捏造禁令 |
| S7 | logprobs 不可得 | https://github.com/dgfug/spiceai/pull/3 ｜ https://stackoverflow.com/q/79299548 | Anthropic Messages API 无 logprobs 的实现级与社区级佐证 |
| S8 | 本机一手产物（可复核） | `runs/g2-eval-final/sspro-report.json`、`runs/g3-eval-2016/sspro-report.json`、`runs/m2-temperature-probe/probe*.json` | 本文 §1 全部数字、§3.2 规则重放、§1.4 网关探测 |
| S9 | 本机源码落点 | `src/minicode/ground/channel.py`（`:148` headers、`:689` refine 调用、`:720-736` 负出口）、`src/minicode/ground/prompt.py`（`:135` coarse_prompt、`:169` refine_prompt、`:437` `parse_optional_point`、`:502` `parse_coarse_target`）、`src/minicode/eval/sspro.py`（`:714` `_crop_containment`、`:1049` `_telemetry_summary`）、`src/minicode/coord/crop_refine.py` | 改动落点 |

---

## 9. 遗留与不确定

1. **RegionFocus 的 VLM judge 在我们的模型上到底多准，没有公开数字可搬**（原文只说 "not always perfectly accurate"）→ 必须自测（改动 2 的埋点就是为此）。
2. **MVP 的聚类阈值 τ 与视图数 m 的最佳值**，论文给的是算法不是最优参数 → 我们的簇占比分布要先落盘再调（改动 1）。
3. **GUI-HalluBench 的 RR 数字为【二手】**（PDF 直取被 content-type 拒绝，只拿到 HTML 摘要）→ 若要对外引用需再核 PDF。
4. **「再搜」能救回多少假命中，是本轮最大的未知数**（估 15~25%）→ 改动 3 的单独跑批就是用来测它的。
5. **本地数据集缺 648 张图**（948/1581）→ 上述所有数字都只在这 183 条上成立，外推需注明。

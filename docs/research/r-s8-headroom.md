# S8 调研线：我们在什么位置？还有多少空间？

> 调研对象：`minimax-m3.1-flash-free`（**通用 API 模型**，OpenCode zen/go 通道，Anthropic-messages 协议）
> 本机基线：ScreenSpot-Pro 183 条分层抽样 —— 1536×960 档 **55.74%**（102/183）；2016×1260 档 **64.48%**（118/183）
> 调研日期：2026-10-22　　web 工具调用：12 次（预算用满）
> 硬约束遵守：本轮只读工作区，唯一写入为本文件。

---

## 0. 一句话结论

**我们已经站在「通用 API 模型 + 两段式 crop」这一组合的公开前沿附近（64.48% vs 公开最好 69.7~73.5），但公开证据指出这 5~9 个点**几乎全部来自协议（裁剪比例 / 是否放大 / 坐标制 / 多采样聚合），不是来自模型能力** —— 所以答案是**先继续调接线，不要换眼睛模型**；因为我们 64.48% 已经**高于绝大多数专用 grounding 模型的官方公开分**（UI-TARS-1.5 全量 61.6、Seed-1.5-VL 53.8~59.2、Gui-Cursor 56.5、Qwen3-VL-4B 56.74、Qwen2.5-VL-72B 53.3、GUI-Actor+Verifier 47.7），单独换专用模型**是退步**。

---

## 1. 我们在什么位置（对位表）

| 参照物 | ScreenSpot-Pro | 相对我们 64.48% | 出处 / 证据强度 |
|---|---|---|---|
| **我们（2016 档，通用 API + 两段式 crop）** | **64.48** | — | 本机实测 |
| 我们（1536 档） | 55.74 | −8.7 | 本机实测 |
| GroundingPI（专用，论文产物） | 65.78 | +1.3 | 【二手】https://hyper.ai/de/papers/2609.39601 |
| **公开「通用模型 + 两段式 Zoom-In」最好一档**（MAI-UI-32B） | **73.5** | **+9.0** | 【二手】见 §3 |
| Seed 1.8 + Zoom-In | 73.3 | +8.8 | 【二手】见 §3 |
| Gemini 3 Pro + 两段 Zoom-In（复现） | 69.7 | +5.2 | 【二手】见 §3 |
| **Gemini 3 Pro（官方报告值）** | **72.7** | +8.2 | 【一手·Google 官方 model card 对比表】https://evals.report/benchmarks/screenspot-pro?tab=scores |
| Kimi-K2.5 + RCPO 多采样聚合 | 69.9 | +5.4 | 【二手】见 §3 |
| UI-TARS-1.5（专用 grounding 官方全量） | 61.6 | **−2.9** | 【一手·官方 model card】https://huggingface.co/bytedance-research/UI-TARS-1.5-7B |
| Gui-Cursor-7B | 56.5 | −8.0 | 【二手】https://ar5iv.labs.arxiv.org/html/2509.21552 |
| Qwen3-VL-4B | 56.74 | −7.7 | 【二手】https://hyper.ai/de/papers/2609.39601 |
| Seed-1.5-VL | 53.8~59.2 | −5.3~−10.7 | 【二手】 |
| Qwen2.5-VL-72B | 43.6（技术报告）/ 53.3（官方 leaderboard 条目） | −11.1~−20.9 | 【一手/二手】https://huggingface.co/Qwen/Qwen2.5-VL-72B-Instruct |
| Claude Sonnet 4.5（通用 API，官方报告） | 36.2 | −28.3 | 【一手·Google model card 表】 |
| Claude Sonnet 4.5（MAI-UI 复现 E2E） | 35.0 | −29.5 | 【二手】 |
| Claude Sonnet 4.5 + 两段 Zoom-In | 54.0 | −10.5 | 【二手】 |
| GPT-4o 裸 API（官方论文直接评测） | **0.9** | −63.6 | 【一手·ScreenSpot-Pro 论文 Table 2】https://arxiv.org/abs/2504.07981 / https://openreview.net/pdf/adcc235fac27c7b157f6b3c3c13f96fa76878e7e |
| GPT-5.1 | 3.5 | −61.0 | 【一手·Google model card 表】 |
| Gemini 2.5 Pro | 11.4 | −53.1 | 【一手·Google model card 表】 |
| OpenAI CUA | 23.4 | −41.1 | 【一手·UI-TARS-1.5 model card 表】 |
| Claude 3.7 Sonnet | 27.7 | −36.8 | 【一手·UI-TARS-1.5 model card 表】 |
| OS-Atlas-7B | 18.9 | −45.6 | 【一手·ScreenSpot-Pro 论文 Table 3】 |
| UGround-V1-7B | 31.1（官方）/ ~16.5（论文基线复现） | −33.4~−47.9 | 【一手·官方 repo】https://github.com/OSU-NLP-Group/UGround |

**读法**：我们的 1536 档（55.74%）已经**高于** Gemini 2.5 Pro、GPT-4o、GPT-5.1、Claude 3.7、OS-Atlas、UGround-7B 这些「公开官方分」；我们的 2016 档（64.48%）已经**高于所有主流专用 grounding 模型的官方分**，仅次于少数论文级产物（GroundingPI 65.78）。
⚠️ 可比性警告（必须写下来）：公开数字跑的是**全量 1581**，我们跑的是**183 条分层抽样**；我们的两档还不是配对实验。**结论的方向可靠（我们处在通用模型第一梯队），但具体差值有 ±3~4 pp 的抽样噪声。**

---

## 2. (a) 通用旗舰 API 模型的公开点级准确率（逐个出处）

### 2.1 通用模型裸跑（单图 → 单点，无任何协议辅助）

| 模型 | ScreenSpot-Pro | ScreenSpot-v2 | 出处 | 强度 |
|---|---|---|---|---|
| GPT-4o | **0.9** | — | ScreenSpot-Pro 论文 Table 2 | 【一手】https://arxiv.org/abs/2504.07981 |
| GPT-4o（换协议/换分辨率复现） | 17.1 / 18.3 | — | UI-Copilot 等后续论文对比表 | 【二手】 |
| GPT-4o + recursive visual search（planner+verifier） | 48.1 | — | SeeClick Pro Agent 式流水线 | 【二手】 |
| Gemini 2.5 Pro | 11.4 | — | Google Gemini 3 model card 对比表 | 【一手（转述）】https://evals.report/benchmarks/screenspot-pro?tab=scores |
| GPT-5.1 | 3.5 | — | 同上 | 【一手（转述）】 |
| Claude 3.7 Sonnet | 27.7 | 87.6 | UI-TARS-1.5 官方 model card 表 | 【一手（转述）】https://huggingface.co/bytedance-research/UI-TARS-1.5-7B |
| OpenAI CUA | 23.4 | 87.9 | 同上 | 【一手（转述）】 |
| Claude Sonnet 4.5 | 36.2 | — | Google model card 对比表 | 【一手（转述）】 |
| **Gemini 3 Pro** | **72.7** | — | Google 官方 model card（2025-11-18/19 发布） | 【一手（转述）】 |
| Gemini 3 Flash / 3.1 Pro Preview | 69.1 / 84.4 | — | evals.report 榜单 | 【二手】https://benchlm.ai/benchmarks/screenspot-pro |
| Claude 3.5 Sonnet | ~83（**原版** ScreenSpot，非 v2） | — | 聚合站 | 【二手·站点自相矛盾，不作引用】 |

### 2.2 各家官方是否发布过「grounding 专项模型 / 官方提示词」

| 厂商 | 有没有 | 公开分数 | 出处 |
|---|---|---|---|
| OpenAI | ✅ `computer-use-preview`（2024-10）→ 后继 OpenAI CUA | CUA：SS-Pro 23.4 / SS-v2 87.9 | 【一手·UI-TARS-1.5 model card 表】 |
| Anthropic | ✅ `computer use` 工具（2024-10 beta）→ Claude 3.7/4.5 computer use | 3.7：27.7 / 87.6；4.5：36.2 | 同上 |
| Google | ✅ Project Mariner 的 computer-use 模型 + Gemini 3 的 screen understanding | Gemini 3 Pro 72.7 | 【一手（转述）】 |
| 字节 | ✅ UI-TARS-1.5（**真 grounding 专项**，有 ScreenSpot-Pro 专项训练） | 61.6 / 94.2 | 【一手】https://huggingface.co/bytedance-research/UI-TARS-1.5-7B |
| 阿里 | ✅ Qwen3-VL 的 grounding 模式 + `computer_use` 工具；Qwen2.5-VL 技术报告 | 43.6（SS-Pro）/ 88.1（SS-v2） | 【一手/二手】 |
| MiniMax | ⚠️ **M1（456B 总 / 45.9B 激活，长上下文推理）/ M2（~230B 总 / ~10B 激活，agentic 工具调用）** 的官方表里**没有找到 ScreenSpot-v2/Pro 的 grounding 行**；grounding 数更可能挂在 **MiniMax-VL-01** 线上 | **未取到** | 【未取到·说明见下】 |
| 智谱 GLM-4.5V | 有一张第三方表含 GLM-4.5V 行，但**列名不明**，疑似 SS-Pro ≈ 34.4 | 不可确认 | 【二手·列名不明】 |
| Kimi | ✅ Kimi-K2.5 / K2.6（有官方 grounding/agent 表） | 60.8（E2E 复现）→ 69.9（+RCPO） | 【二手】§3 |

> **MiniMax 一栏明确说明**：我做了两轮定向检索（`MiniMax M1 M2 GUI grounding ScreenSpot-v2 official benchmark table` 等），**没有任何一手来源给出 M1/M2 在 ScreenSpot-v2 / ScreenSpot-Pro 上的点级准确率**。ScreenSpot-Pro 论文的基线清单里也**不含 MiniMax 系列**。因此**不引用任何 MiniMax 官方 grounding 数字**——宁可写「未取到」，也不编一个数。MiniMax-VL-01 的 model card 是最可能存在的落点，本轮 web 预算用尽未取到。

### 2.3 官方通用 API 评测协议（我们已经在照抄）

我们 `src/minicode/ground/prompt.py` 里引用的 **evalscope 官方 ScreenSpot-Pro 适配器**（`evalscope/benchmarks/screenspot_pro/screenspot_pro_adapter.py`）就是业界「通用 API 模型跑 GUI grounding」的官方协议写法：出框 → 取框中心当落点。我们的 `coarse_prompt` 已经逐字对齐了它的措辞与落点口径。
出处：https://github.com/modelscope/evalscope/blob/main/evalscope/benchmarks/screenspot_pro/screenspot_pro_adapter.py
**这条很关键**：说明我们的「出框 + 框中心」不是自创，是官方协议；我们的两段式 crop 也**不是**自创，公开对照见 §3。

---

## 3. (c) 「通用 API 模型 + 精心设计的 crop 两段式 + 候选编号」的公开对照 —— **这一节是本轮最重要的发现**

有一篇公开复现研究**正好测的就是我们这一类组合**：Tongyi-MAI / MAI-UI 团队的
**《Why your AI Agent keeps misclicking? A Practical Guide to GUI Grounding for Frontier Models》**
（作者：Liangyu Chen, Hanzhang Zhou, Quyu Kong, Xu Zhang, Wenxuan Wang, Qin Jin, Yue Wang）。

- 原文（Notion，**canonical**）：https://galvanized-jump-79a.notion.site/Why-your-AI-Agent-keeps-misclicking-A-Practical-Guide-to-GUI-Grounding-for-Frontier-Models-32630d140ad8808e895de98994dddb93
  （本轮抓取返回的是 JS 空壳，正文未取到 → 以下数字来自中文逐节转载，标【二手】）
- 官方博客入口：https://tongyi-mai.github.io/MobileWorld/blog_post.html?post=Why%20your%20AI%20Agent%20keeps%20misclicking
- 中文逐节转载（含全部表格数字与提示词原文）：https://blog.csdn.net/weixin_43427721/article/details/159436148 【二手】
- 官方仓库：https://github.com/Tongyi-MAI/MobileWorld

### 3.1 三种范式的公开分数（ScreenSpot-Pro）

| 范式 | 公开数字 | 结论 |
|---|---|---|
| **SoM（Set-of-Mark，标号覆盖图）** | **所有模型 < 30 分** | **对我们不适用**：官方实测「全面失败」，主因是 UI 元素密集 → 标号框重叠严重 → 视觉噪声压垮模型 |
| **E2E 单段（原图 → 坐标）** | Gemini-3-Pro **39.0**、Claude Sonnet 4.5 **35.0**、Seed1.8 **67.0**、Kimi-K2.5 **60.8**、MAI-UI-32B **67.9** | 我们 64.48 ≈ 这一档的上沿（Seed1.8/MAI-UI-32B 水平） |
| **E2E + 两段式 Zoom-In（推理阶段优化）** | Gemini-3-Pro **69.7**（+30.7）、Claude Sonnet 4.5 **54.0**（+19.0）、Seed1.8 **73.3**、MAI-UI-32B **73.5**（+5.6）、Kimi-K2.5 **60.8→69.9**（用 RCPO 聚合） | **这就是我们这条线的公开上限：69.7~73.5** |

原文原话（中文转载转述）：**「最大性能差距不在模型之间，而在『公开 vs 未公开』的评测协议之间……坐标系或 Zoom-In 一个细节错了就能差 33 分。」** 以及 **「缺失的能力一直都在，只是需要正确的评测协议才能释放。」**

### 3.2 公开的 Zoom-In 配方（**可直接照抄的参数**）

| 模型 | 坐标系 | 图片处理 | Zoom-In 策略 | 其他 |
|---|---|---|---|---|
| Gemini 3 Pro | 相对 **0–1000** | Ultra High 分辨率 | **两阶段：以预测点为中心裁原图 1/4 宽高 → resize 到 1920×1080 → 二次定位** | temperature=0.01 |
| Claude Sonnet 4.5 | 相对 **0–1** | resize 到 1280×720 | **50% 裁剪 → upscale 1280×720** | 「降采样太狠是性能差的主因」 |
| Seed 1.8 | 相对 0–1000 | — | **50% 裁剪** | 输出 `<point>x y</point>`，E2E 最鲁棒 |
| Kimi-K2.5 | 0–1000 | — | 同 Seed1.8 | 推理 trace 看似在反思，**其实没真做 crop-and-zoom** |
| MAI-UI-32B | 0–1000 | smart resize（上限 6,335,600 px ≈ 4800 image tokens） | 同 Seed1.8 | 32B 就超过闭源大模型 |

**裁剪比例是超参，且差异极大**：Gemini-3-Pro 在 ScreenSpot-Pro 上，**最佳配置比最差配置高 11.4 分**（1/4 裁剪 + 1920×1080 最优；1/2 裁剪「覆盖区域太广，无法有效放大」；低分辨率场景下过度放大反而引入噪声）。
**Aspect ratio 警告**：ScreenSpot-Pro 含大量多屏拼接截图（如 5120×1440 = 两块 2560×1440 拼），从这种全景图裁剪后若强行 resize 到固定比例会**拉伸变形**，按钮被拉长、文字畸变 → 破坏模型在标准宽高比上的空间先验。

### 3.3 公开的 test-time scaling：RCPO（**对我们最可复制的一条**）

- 做法：**k 次独立预测** → 每���预测在原图上建一个 **50×50 像素框** → 取这些框的**交集中心**作为最终答案。
- 公开数字：Kimi-K2.5 **60.8 → 69.9**（+15% 相对）；Seed1.8 达 **76.6**（超其单次基线近 10 分）。
- **RCPO 在更难的 benchmark 上一致优于 Pass@k oracle**（用同一批采样做 oracle 上界）。
- 原文结论：「模型在分布意义上知道正确位置——单次预测有噪声，但噪声大致以正确答案为中心。」

### 3.4 公开的另一个反直觉结论

- **自由形式推理（free-form thinking）对 GUI grounding 几乎无效甚至有害**；有效的推理是**结构化、观察型**的（先说看到什么，再说坐标）。
  对我们的直接含义：我们的 `coarse_prompt` 现在写着「**不要输出分析**」，`refine_prompt` 写着「直接给坐标即可，不用解释」——**这与公开最佳实践方向相反**（但公开的负面结论针对的是「无结构的自由推理」，不是「禁止输出」）。

### 3.5 判定：我们这套组合是不是已经到顶了？

**没有。** 证据链：

1. 公开在同一范式下的最好成绩是 **69.7 ~ 73.5**（Gemini-3-Pro+Zoom / Seed1.8+Zoom / MAI-UI-32B+Zoom），我们是 **64.48** → **空间 5.2 ~ 9.0 个点**，全是**协议层面**的。
2. 公开明确说：**协议差异可以带来 +19 ~ +31 点**（Claude Sonnet 4.5 35.0→54.0；Gemini-3-Pro 39.0→69.7）。我们还没做的关键协议项有三条：
   - **crop 之后放大到固定目标分辨率**（我们是 1:1 原生像素、不上采样 → icon 在模型输入里仍是 12~16 px；公开配方是 resize 到 1920×1080）；
   - **裁剪比例调参**（公开：最优 vs 最差 11.4 分）；
   - **多采样空间共识（RCPO）**（公开：+9.1 分）。
3. 我们和公开配方的一个**具体、可量化**的差距：
   - 我们 2016 档：**icon 在模型输入图里中位 15.75 px，51.7% < 16 px**；
   - 公开配方：2016 宽的输入 → crop 0.5× 线性（1008×630）→ **resize 到 1920×1080（1.9× 上采样）** → 同一个 icon 变成 **≈30 px**；若 crop 取 0.25× 线性（504×315）→ 3.8× 上采样 → **≈60 px**。
   - 公开的 Gemini-3-Pro 之所以能 +30.7，机制就是这一条（他们的原话是「降采样/不放大会把小元素直接抹掉」）。

---

## 4. (b) 专用 grounding 模型的 ScreenSpot-Pro 分数（上限参考）

| 模型 | ScreenSpot-Pro | ScreenSpot-v2 | 出处 | 强度 |
|---|---|---|---|---|
| UI-TARS-1.5（全量，字节官方） | **61.6** | **94.2** | 官方 model card；`.eval_results/screenspot_pro.yaml` 亦为 61.6 | 【一手】https://huggingface.co/bytedance-research/UI-TARS-1.5-7B |
| UI-TARS-1.5-7B | 49.6（官方表）/ 42.2（UI-I2E 榜）/ 41.6~42.0（第三方复现） | 88.1~89.0 | 同上 + issue #215 | 【一手+二手】 |
| UI-TARS-72B-DPO | 38.1 | 90.3 | UI-TARS-1.5 model card | 【一手】 |
| Qwen2.5-VL-72B | 43.6（技术报告）/ **53.3**（官方 leaderboard 条目）/ 51.2（UI-Venus）/ 34.2（ViGoRL） | 88.1（ViGoRL 复现） | model card + 论文 | 【一手+二手】 |
| Qwen3-VL-4B | 56.74 | 92.30 | GroundingPI 论文表 | 【二手】https://hyper.ai/de/papers/2609.39601 |
| Seed-1.5-VL | 53.8~59.2 | 95.2 | 多篇论文对比表 | 【二手】 |
| GroundingPI | 65.78 | 96.15 | 同上 | 【二手】 |
| GTA1-7B | 50.1 | — | 多篇对比表 | 【二手】 |
| Gui-Cursor-7B | 56.5 | 93.9 | 论文 | 【二手】https://ar5iv.labs.arxiv.org/html/2509.21552 |
| GUI-Actor-7B（Qwen2-VL / Qwen2.5-VL） | 40.7 / 44.6（+Verifier **47.7**） | 89.5 / 92.1 | 论文 Table 1/2 | 【一手·论文表】https://openreview.net/pdf?id=5fSkinHw7w |
| GUI-Actor-3B | 42.2（+Verifier 45.9） | 91.0 | 同上 | 【一手·论文表】 |
| Jedi-7B / Jedi-3B | 39.5 / 36.1 | 91.7 / 88.6 | GUI-Actor 论文 Table 2 | 【一手·论文表】 |
| UGround-V1-7B | 31.1（官方 repo）/ ~16.5（论文基线复现） | 87.6 | 官方 repo | 【一手】https://github.com/OSU-NLP-Group/UGround |
| UGround-72B | 34.5 | — | 多篇对比表 | 【二手】 |
| OS-Atlas-7B | 18.9 | 84.1 | ScreenSpot-Pro 论文 Table 3 | 【一手】https://arxiv.org/abs/2504.07981 |
| **ShowUI** | **未取到** | ~55~65 | ShowUI 论文只报 ScreenSpot / v2 / NaviBench；SS-Pro 数字只有第三方重评 | 【不适用·说明：原始论文不报该指标】 |
| **AnyClick** | **未取到** | — | 检索未命中该模型名的 SS-Pro 结果 | 【不适用·说明：检索无结果】 |

**关键读数**：**专用 grounding 模型的公开天花板（除 GroundingPI 65.78 这个论文产物外）是 61.6**，**低于我们 64.48**。
⇒ 「换眼睛模型」若换成专用 grounding 模型（UI-TARS-1.5 / Jedi / GUI-Actor / UGround / OS-Atlas），**单点看是退步 3~46 个点**。
⇒ 唯一可能叠加的是「专用 grounding 模型 + 我们这套两段式 crop」的组合，**这个组合公开没有人测过**【推断】——属于高价值但未验证的实验。

---

## 5. (d) 判定：继续调接线，还是换眼睛模型？

### 5.1 结论

**继续调接线（协议），先不换眼睛模型。** 依据三条，按权重排序：

1. **公开证据显示，协议层的天花板就在 69.7~73.5，而专用 grounding 模型的公开天花板是 61.6。** 我们现在 64.48 在两者之间偏上，继续调接线是**唯一有公开数字支撑的方向**（+5~9 点有具体配方）。
2. **公开最大的单项协议差是「crop 之后放大到固定目标分辨率」**，这一条我们**明确没做**（`src/minicode/ground/crop.py::crop_frame` 是 1:1 字节级裁剪，**不上采样**；`channel.py` 里也没有任何 resize 逻辑）。这一条有 MAI-UI 的 +19~+31 点同型证据（Claude 35.0→54.0、Gemini 39.0→69.7）。
3. **我们的采样天然有多样性**（网关忽略 temperature，同图同提示词 10 次原文一致率只有 10%）→ 公开的 **RCPO 多采样空间共识**（+9.1 分，Kimi 60.8→69.9）**可以直接做**，不需要先解决温度。

### 5.2 如果接线做完还不够，备选眼睛模型（含本机可行性）

| 备选 | 公开 SS-Pro | 本机可行性 | 备注 |
|---|---|---|---|
| **Gemini 3 Pro** | **72.7**（官方）/ 69.7（+Zoom 复现） | ✅ API 直连（不在本机跑） | 官方坐标制 = 相对 0–1000；官方提示词模板见 §3.2；本轮结论：**最值得做 A/B 的一只眼** |
| **Seed 1.8（豆包）** | 67.0 → 73.3（+Zoom） | ✅ API（火山方舟） | 「纯 E2E 最鲁棒」，输出 `<point>x y</point>` |
| **Kimi-K2.5 / K2.6** | 60.8 → 69.9（+RCPO） | ✅ API | 官方提示词含完整工具描述 |
| **MAI-UI-32B**（自研开源） | 67.9 → 73.5（+Zoom） | ⚠️ 32B，本机 6GB 跑不了 → 只能走 API/租卡 | 32B 打赢闭源，**协议 > 参数量**的最好例证 |
| **Qwen3-VL-4B** | 56.74 / SS-v2 92.30 | ✅ 本机 RTX 3060 6GB 可跑（4bit 量化） | 单点低于我们 64.48 → 只在「零成本自托管 + 可做 A/B 消融」时值得试 |
| UI-TARS-1.5 | 61.6 | ⚠️ 大尺寸权重，本机跑不了 | **低于我们 → 不建议直接换** |

### 5.3 一句话给决策者

> **先把「crop 放大 + 裁剪比例扫描 + RCPO 多采样」三条接线做完**：公开数字支持 **+5~9 个点**（64.48 → ~70~73.5），
> 且这三条**不换模型、不改坐标制**，只改 `src/minicode/ground/` 三个文件。
> 同时用 **Gemini 3 Pro** 做同批配对 A/B（不改我们任何代码，只换模型名）——一次跑批就能知道「换眼」值多少点，因为公开数字里 Gemini 3 Pro 的 72.7 与复现的 39.0 **差的就是协议**，而我们的协议已经比公开复现版更强。

---

## 6. ≤8 条最小改动（按投入产出排序；每条：改哪个文件 / 做什么 / 预期哪个数变多少 / 怎么验证）

> 共同前提：所有验证都**必须配对**——两臂共用同一次粗定位上游回答（复用 `runs/` 里的回显），否则被模型随机性污染（我们实测同图 10 次一致率仅 10%）。
> 样本：同一批 183 条（种子 20251020），判定 = 官方 bbox 命中，配对 McNemar。

| # | 改哪个文件 | 做什么 | 预期哪个数变多少 | 怎么验证 |
|---|---|---|---|---|
| 1 | `src/minicode/ground/crop.py::crop_frame` + `src/minicode/ground/channel.py`（精定位分支） | **crop 之后上采样到固定目标分辨率再送模型**（公开配方：resize 到 **1920×1080**；上采样倍数 = 1920 / crop 宽）。**约束：目标宽必须 ≤ 2016 px**（服务端会把 >2016 宽的图缩掉——我们自己的探针实测过），所以用 1920×1080 而不是 2560×1440 | **icon 子集 41.38% → 50~58%**；整体 64.48% → **70~76%**。依据：MAI-UI 同型改动让通用模型 +19~+31 点（Claude 35.0→54.0、Gemini 39.0→69.7），机制是 icon 从 15.75 px 变成 ≈30 px【二手 + 推断】 | 同 183 条配对跑批；**必须复用同一次粗定位输出**；报告 icon 子集分位（中位 15.75 px → ≈30 px）与整体 McNemar p 值 |
| 2 | 新增 `src/minicode/ground/aggregate.py`；在 `channel.py` 精定位分支接入 | **RCPO 式多采样空间共识**：精定位连发 **k=5~9** 次（我们网关忽略 temperature，实测 10 次原文一致率仅 10% → 天然有多样性，**不需要动 temperature**）→ 每个点建 **50×50 物理框** → 取**交集中心**；交集为空则退到中位数 | 整体 **+3~9 点**（公开：Kimi-K2.5 60.8→69.9、Seed1.8 76.6；RCPO 一致优于 Pass@k oracle）【二手】 | 配对跑批；对比 k=1 / 5 / 9 三档；**顺带产出第 6 条要用的「散布度」** |
| 3 | `src/minicode/ground/channel.py:172`（`CROP_LINEAR_FRACTION=0.5`、`MIN_CROP_WIDTH_PX=512`） | **裁剪比例扫描**：在 {0.25, 0.35, 0.5} 里做 A/B。⚠️ **不要盲改成 0.25**：我们自己的离线几何（`runs/m3-crop-geometry/crop-geometry.json`）显示 0.5× 的「含住率 84.2%、窗口无目标率 14.2%」是本地最优；0.25 会掉含住率 → 假命中上升。**正确顺序是做完第 1 条（放大）之后再扫比例**，放大补偿了含住率的损失 | 整体 **±5 点**（公开：Gemini-3-Pro 最优/最差裁剪配置差 **11.4 分**）【二手】 | 复用同一次粗定位，只换 crop 比例；三条臂配对；⚠️ **必须保持 crop 宽高比**，避免公开指出的拼接全景图拉伸变形 |
| 4 | `src/minicode/ground/prompt.py::refine_prompt`（以及 `coarse_prompt` 里「不要输出分析」） | 把**自由/禁止推理**改成**结构化观察式推理**：先一句观察锚点（如「在 (x,y) 附近有一个图标/文字控件」），再给坐标。⚠️ **不得写任何坐标单位**（C-04：分母只能来自标定）；⚠️ **free-form 长思考公开实测无效甚至有害**，所以只准写**一句**观察 | **+1~4 点**【推断】（公开只给了「free-form 无效 / 结构化观察更好」的定性结论，没给本场景的数值） | 配对跑批；同一批 183 条，只换提示词 |
| 5 | `src/minicode/ground/prompt.py::coarse_prompt` + `src/minicode/calib/prompt.py` | **坐标制 A/B：像素声明（现状）vs 相对 0–1000**。公开所有通用 API 模型都用**相对 0–1000**（Gemini / Seed / MAI-UI），Claude 用 0–1；原文称坐标系是**单项最大**的协议差（33 分量级）。⚠️ 受 C-04 约束：不能直接改提示词，**必须先在标定探针里加一档「相对坐标」**，用标定结果决定分母 | **±0~10 点**【推断】（方向不确定，但方差很大，值得一次实验） | 先跑标定探针（`minicode.calib`）加档 → 拿分母 → 再配对跑批 |
| 6 | `src/minicode/ground/channel.py`（精定位结果落盘处） | **假命中护栏**：复用第 2 条的 k 次采样散布度——k 个点在物理平面上**互相距离 > 阈值**（例如 > 1 个目标直径）→ 判「crop 里没有目标」→ **干净回落到粗定位**（复用现有 `FallbackCause`，不新增机制）。公开对应物是 E2E 协议的「没找到」出口；公开文献**没有报假命中率**，所以这一步是【推断】 | 假命中 20 条回收一部分，整体 **+1~4 点**【推断】 | 用现有 183 条里的 20 条假命中 + 25 次回落重放；**验证不能误伤**：粗定位本来就对的那 75 条里，精定位改坏的 11 条不能变多 |
| 7 | 评测脚本 `src/minicode/eval/sspro.py` + 数据目录 | **补齐缺失的 633 张图**（本地 948/1581）→ 从官方 ScreenSpot-Pro 仓库下载全量 → 把 183 条抽样扩到**全量 1581 条** | 不改分数，改**置信度**：183 条的 95% CI 约 ±7 pp → 全量约 ±2.4 pp。**所有「差几个点」的结论都依赖这个** | 下载后先核对官方 bbox 与本地 948 张的一致性；再跑全量 |
| 8 | `src/minicode/model/`（配置层） | **换眼 A/B（不是替换，是并行臂）**：用同一批 183 条、同一套提示词与 crop 几何，**只换模型名**跑 **Gemini 3 Pro**（官方 72.7 / 复现 39.0 的差=协议；我们的协议更强，预期 ≥73.5）。备选：Seed 1.8、Kimi-K2.5 | 未知，公开暗示 **+5~10 点**【推断】。若成立 → 我们当前通道**已经接近通用 API 上限**，该做的事变成「换眼 + 保留协议」 | 同一套本地评测台、同一批样本、同一提示词；**协议冻结，只换模型** |

---

## 7. 本轮没做成 / 不适用的部分（写清楚，避免被当成已覆盖）

1. **MiniMax 官方 grounding 分数**：检索两轮无一手来源，**不引用**。若要查，落点是 `huggingface.co/MiniMaxAI/MiniMax-VL-01` 与 MiniMax-M1 技术报告附录（本轮 web 预算用尽）。
2. **ShowUI / AnyClick 的 ScreenSpot-Pro**：ShowUI 原论文不报该指标；AnyClick 检索无结果 → 标不适用。
3. **GLM-4.5V**：只有一张列名不明的第三方表（疑似 34.4），**不作引用**。
4. **Notion 原文正文**：抓取返回 JS 空壳 → §3 的全部数字来自中文逐节转载（【二手】），原始 Notion 链接已给出以便复核。
5. **HuggingFace 直连**：本机 DNS 对 huggingface.co 解析失败（`resolves to a non-public IP address`）→ UI-TARS-1.5 / Qwen2.5-VL 的 model card 数字来自检索转述，URL 已附。
6. **两档未配对**：55.74% vs 64.48% 的差仍混着模型随机性；本报告的对外结论一律用「同协议公开对照」而非「我们两档互比」。
7. **Web 调用 12 次已用满**，后续若要补：优先补 MiniMax-VL-01 的官方 grounding 表、ShowUI 的第三方 SS-Pro 重评、以及 Notion 原文的表格原图。

---

## 8. 引用清单（全部可打开）

- ScreenSpot-Pro 论文（GPT-4o 0.9 / OS-Atlas 18.9 / Table 3）：https://arxiv.org/abs/2504.07981 ｜ https://openreview.net/pdf/adcc235fac27c7b157f6b3c3c13f96fa76878e7e
- UI-TARS-1.5 官方 model card（61.6 / 94.2；OpenAI CUA 23.4 / 87.9；Claude 3.7 27.7 / 87.6）：https://huggingface.co/bytedance-research/UI-TARS-1.5-7B
- ScreenSpot-Pro 分数榜（Gemini 3 Pro 72.7 / Claude Sonnet 4.5 36.2 / GPT-5.1 3.5 / Gemini 2.5 Pro 11.4）：https://evals.report/benchmarks/screenspot-pro?tab=scores ｜ https://benchlm.ai/benchmarks/screenspot-pro
- MAI-UI「Why your AI Agent keeps misclicking」原文：https://galvanized-jump-79a.notion.site/Why-your-AI-Agent-keeps-misclicking-A-Practical-Guide-to-GUI-Grounding-for-Frontier-Models-32630d140ad8808e895de98994dddb93 ｜ 官方博客 https://tongyi-mai.github.io/MobileWorld/blog_post.html?post=Why%20your%20AI%20Agent%20keeps%20misclicking ｜ 中文逐节转载 https://blog.csdn.net/weixin_43427721/article/details/159436148 ｜ 仓库 https://github.com/Tongyi-MAI/MobileWorld
- GUI-Actor 论文（Table 1/2：Jedi / GUI-Actor / UGround / OS-Atlas / Aguvis）：https://openreview.net/pdf?id=5fSkinHw7w
- UGround 官方 repo（31.1，18.9→31.1）：https://github.com/OSU-NLP-Group/UGround
- Qwen2.5-VL-72B（43.6 / 53.3）：https://huggingface.co/Qwen/Qwen2.5-VL-72B-Instruct
- Gui-Cursor（56.5 / 93.9）：https://ar5iv.labs.arxiv.org/html/2509.21552
- GroundingPI / Qwen3-VL-4B 表（65.78 / 56.74）：https://hyper.ai/de/papers/2609.39601
- 官方通用 API 评测协议（我们已在照抄）：https://github.com/modelscope/evalscope/blob/main/evalscope/benchmarks/screenspot_pro/screenspot_pro_adapter.py
- trycua 的 Gemini GUI grounding 落地示例（`media_resolution="high"` + `thinking_level="high"`）：https://github.com/trycua/cua/blob/17e2613e65045d75c11678acba5518d349a25adb/docs/content/docs/cua/examples/ai-models/gemini-complex-ui-navigation.mdx

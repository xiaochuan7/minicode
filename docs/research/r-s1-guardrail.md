# S1 护栏调研：粗定位点与精定位点都在时，按什么**可判规则**选？

> 线名 S1 · 调研日期 2026-10-22 · 只读工作区（本文档是唯一写入）
> 目标：在「粗定位点 p₀ 与精定位点 p₁ 同时存在」时，找到**不是又一个阈值**的可判规则。
> 约束：性能/可用性优先；token、调用次数、延迟、费用**不作为取舍理由**（本文所有建议都按"多花多少能多拿多少个点"表达）。

---

## 0. 一句话结论

**不要在两个点之间"选"，要把两个点变成候选集，用「跨视角票数 + 空间一致性」在候选集上做相对比较（argmax），而不是用标量阈值去卡某一个点。**
公开工作里三条独立证据链指向同一个形状：GUI-RC 的空间投票网格（共识区域）、ReClick 的跨视角支持 + 空间一致性（标准化等权求和后 argmax）、FORUM 的跨成员一致性 + medoid（不取坐标平均）。
而"超阈值就回落"失败的根本原因不是阈值不准，是**判据只有一个轴**——我们自己的数据已经证明了这一点：被位移阈值丢弃的 30 条里，精定位命中 18/30、粗点命中 12/30，**精点是否正确不随位移单调**，所以任何只看位移的规则必然两头砍。

---

## 1. 把问题写清楚：这是"二选一"还是"打分"？

我们当前形态是二选一：`channel.py` 里粗定位成功 → 裁 crop → 精定位成功 → 直接用 `LANDING_SOURCE_REFINED`（`src/minicode/ground/channel.py:785`）；精定位失败才回落 `LANDING_SOURCE_COARSE`。
即：**精定位是无条件替换粗定位**，没有护栏。

本机数字（本线自测，非公开来源）：
- 1536 档：粗定位对 75 条 → 最终对 102 条；即"精定位改坏 11 条、在 108 条粗错里救回 38 条"。
- 若改成"位移超阈值就回落粗点"：那 30 条被丢弃的行，最终落点由精点换成粗点，命中从 18 变 12 → **净 −6 条 = −3.28 pp**（96/183 = 52.46%）。位移阈值不只是"没增益"，是**净负**。
- 【本线推导】这说明：**位移与正确性不同向**，所以"把阈值调准"这条路是死的，必须换判据的**轴**。

要用的第二个轴，公开工作给出的答案是：**同一张图、另一个视角，对这个点本身投的票**。

---

## 2. 公开工作里"多候选取舍"的判据先验（5 条）

### 2.1 GUI-RC：空间投票（框怎么投 / 票怎么数 / 怎么出结果）

| 项 | 公开值 | 证据 |
|---|---|---|
| 采样 | K = **64** 次，temperature 0.5、top_p 0.95（论文）；代码默认 `TEMPERATURE` 0.7、`SAMPLE_NUM` 64 | 【一手】 |
| 投框 | 框式模型：预测框 `R_k` 原样投；点式模型：点扩成 **α = 50** 的正方形 `R_k = [x_k−α/2, x_k+α/2] × [y_k−α/2, y_k+α/2]` | 【一手】 |
| 数票 | 投票网格与截图同分辨率，`v[x,y] = Σ_k 1[(x,y) ∈ R_k]` | 【一手】 |
| 出结果 | `v_max = max v`；取所有"每像素都等于 v_max"的连通区域，**选面积最大的那个**作为共识区域，替换单次预测 | 【一手】 |
| 代码位置 | `https://github.com/zju-real/gui-rcpo` → 目录 `TTRL4GUI/GUI-RC/`，文件 `evaluation.py`；可调项 `MODEL_PATH` / `QUESTION_TEMPLATE` / `TEMPERATURE` / `SAMPLE_NUM` / `POINT_EXPAND_SIZE` | 【一手】README |

公开增益（ScreenSpot-Pro，论文 Table 1）：**Qwen2.5-VL-3B 20.18 → 23.59（+3.41）**、**Qwen2.5-VL-7B 19.80 → 23.97（+4.17）**；而 GUI 专用模型 OS-Atlas-Base-7B **18.41 → 19.67（+0.16）**、UI-TARS-1.5-7B **40.92 → 41.18（+0.26）**；SSv2.avg 上 OS-Atlas +2.75、Qwen2.5-VL-3B +2.52。

> **对我们的直接含义**【推断】：我们的眼模型是**通用 API 模型**，不是训过的 grounding 模型 —— 正好落在 GUI-RC 增益最大的一端（+3.41/+4.17），而不是 GUI 专用模型那一端（+0.16/+0.26）。GUI-RC 论文也自述：点式模型因为"扩方块只是模拟注意力区"的粗糙代理，增益小于框式模型。
> **对我们的第二个含义**：我们的粗定位**本来就出框**（`parse_coarse_target` → `raw.bbox`，`channel.py:590` 落盘 `raw_bbox`；`_coarse_physical` 产出 `coarse_radius_px`，`channel.py:595`）→ **投票可以直接用框投，不需要 α 扩展**；α 只需要用于精定位那个点。GUI-RC 里框式优于点式的那条，对我们是免费的。

### 2.2 LFPR：几何护栏 + 坐标中点融合（"回落"与"融合"的正确分工）

来源：`https://arxiv.org/abs/2608.19553`（*Where Grounding Accuracy Lives on the IoU Curve: Label-Free Inference-Time Boundary Refinement*, arXiv 2608.19553, 2026-08-20）。

**流程**：先出 incumbent 框 b₀′ → 按**框边长的 γ = 2.0 倍**开上下文 crop → 同一冻结模型在 crop 内重定位 → 得到候选 b_c → **过三个几何 guard** → 过则**坐标-wise 中点** ½b₀′ + ½b_c，不过则保留 b₀′。

**三个 guard（原文 §3.3，式 (3)(4)(5)(6)）**：
1. zoom-consistency：`z(b_c, b₀′) = ‖c(b_c^C) − a‖₂ / √(1/2) ≤ 0.35`（a = incumbent 中心在 crop 坐标里的位置；分母 √(1/2) 是单位正方形的归一化）
2. `IoU(b_c, b₀′) ≥ 0.25`
3. `0.5 ≤ A(b_c)/A(b₀′) ≤ 2.0`（面积比）

**四个可照抄的数字**【一手】：
- **无 guard 对照（同批候选，只把 guard 去掉）比 incumbent 更差**：Acc@0.5 **−2.926**、Acc@0.75 **−3.969**、Acc@0.9 **−5.748** → 原文结论："the guard is load-bearing"。这与我们的形状一模一样：**无条件用精定位替换粗定位，正是 LFPR 里那个 −5.7 的臂。**
- **guard 接受率 92.5%**（29,523 / 31,920 次 crop 候选被接受并融合）→ **guard 是松的**，不是"卡死大部分"。
- **guard × fusion 因子分解**：**guarded replacement 在 Acc@0.9 与 mAcc 上显著优于中点融合**；而 **中点融合在 Acc@0.5 上优于 guarded replacement**。我们的判定口径是"点落在官方 bbox 内"（= Acc@0.5 型的 hit）→ **按公开数字，我们的口径应该选中点融合**。
- **置信度门控不能替代几何 guard**：原文的同骨干机制对照（"a same-backbone mechanism control substitutes a frozen, label-free confidence threshold for the geometry guard on the same admission point"）结论是**置信度门控连 guard 那点 Acc@0.5 增益都拿不到**。

**增益**【一手】：Ref-L4（31,921 条）mAcc₀.₅:₀.₉₅ **72.947 → 76.013（+3.066）**，Acc@0.5 **88.531 → 89.725（+1.194，95% CI [0.944, 1.439]）**，Acc@0.9 **55.788 → 61.142**；RefCOCO 家族（30,969 条，冻结迁移）Acc@0.5 +0.817、mAcc +0.645、Acc@0.9 −0.029（持平）；Flickr30K（14,481 条，前瞻）mAcc +0.973、Acc@0.9 +1.022；迁移到两个 grounding 专用模型 EGM-4B/8B：Acc@0.9 **+1.569 / +6.716**，代价约 2× 延迟。

**不适用声明**：LFPR 是**指代表达 grounding（自然图像框）**，不是 GUI 点击点；且它的 incumbent 是**框**、crop 候选也是**框**。对我们：incumbent 框我们**有**（粗定位出框），crop 候选只有**点** → 需要把点扩成区域（GUI-RC 的 α 方块是公开认可的同类做法）才能算 IoU 与面积比 guard。**这部分是【推断】。**

### 2.3 ReClick：跨视角支持 + 空间一致性 → argmax（这是最像"可判规则"的一条）

来源：`https://arxiv.org/abs/2610.13268`（*ConClick: Element-wise Contrastive Learning Improves GUI Grounding*, 2026-10-09），代码 `https://github.com/zhangruotian/ConClick`。

**流程**：整屏一次出初始点击 p₀ + token 分数 → 选 **4 个视角**（整屏 + 以 p₀ 为中心的放大 crop + 2 个分数引导的放大 crop）→ 每个放大 crop 解一个点 → 候选集 `P = {p₀, p₁, p₂, p₃}`。

**打分（原文式 (3)(4)(5)，【一手】）**：
- **跨视角支持** `S_cross(p) = (1/|V_p|) Σ_{v∈V_p} ℓ̄_v(p)`：把候选 p 的**坐标序列**放到"每一个包含该位置、且不是它自己生成视角的视角"里算**对数似然**，每个视角内标准化后取均值。
- **空间一致性** `S_agree(p) = −Σ_{p'≠p} ‖p̃ − p̃'‖₂ / max(1,|P'|−1)`：到其他候选的**平均归一化距离取负**。
- **最终**：`p̂ = argmax_p [ Ŝ_cross(p) + Ŝ_agree(p) ]` —— **两个分各自标准化，等权相加，取 argmax**。**没有任何绝对阈值。**

**公开增益**【一手】：ScreenSpot-Pro，Qwen3-VL-8B **54.71 → 66.03（ConClick 训练）→ 72.36（+ReClick）**；ReClick 在 SSPro / MMBench-GUI L2 / OSWorld-G 三集平均 **+2.96 pp**。

**对我们特别相关的一条错误学**【一手】：跨 50 个「模型×数据集」组合，**63.68%–95.43%** 的错误是"点在了非目标元素上"（near-element + far-element）；用 target 中心的 **3w×3h 邻域**区分 near/far；并且"不同模型反复选中同一个 distractor"——有 ≥2 个错误预测的样本里 **55.49%–79.14%** 存在被多个模型共同选中的 distractor。
→ **含义**：错误集中在"选错元素"，不是"偏了几像素"。所以**像素距离类判据天然打不中主要错误**（我们的 11 条改坏大概率就是这一类），必须用"这个点在另一个视角里也成立吗"来判。

**关键不适用点（必须说清）**：S_cross 需要**对数似然**。我们的模型通道是 Anthropic-messages 协议的通用 API 网关，`src/minicode/model/client.py:71 ModelReply` **不暴露 logprobs**（只暴露 `json_payload` / `usage` / `image_tokens` / `stop_reason`）→ **ReClick 的 S_cross 按原样不可实现**。可落地的等价改造见 §4.3（把"算似然"换成"让另一个视角在两个候选里二选一"，即**判别式**版本）。【推断】

### 2.4 FORUM：跨成员一致性 + medoid（不要坐标平均）

来源：`https://arxiv.org/abs/2609.37488`（*FORUM: Frozen Outputs Reconciled Using Model Agreement for Visual Grounding*, 2026-09-27），代码 `https://github.com/Neurogica/FORUM`。

**两条规则**【一手】：
- **区域选择**：候选框按 IoU 单链聚类（**τ = 0.5**，作者明说"intentionally permissive"），保留**被最多个不同模型支持**的簇，同票按簇大小；并强调**按模型身份计数**而非按候选密度计数，"so that a member that contributes several near-duplicate candidates cannot outvote the others"。
- **定位**：**medoid** `b* = argmax_{c∈R*} Σ_{c'∈R*} IoU(c, c')` —— 返回**某个真实成员框**，而不是坐标平均。原文："an arbitrarily loose member changes the output only by switching which real box is selected, never by dragging coordinates continuously off the object."

**三条可引用的数字/结论**【一手】：
- **跨族误差相关 0.47 vs 同族 0.63** → "agreement across different models is a strong label-free signal"；但同时明确：**同一模型的自一致性在干扰项上不可靠**（"a model locked onto a look-alike may repeat the same error under resampling"）。
- **"once a coarse member joins, averaging collapses at strict IoU while the medoid is unaffected"** —— 这正是我们的场景（粗框 + 精框两个精度不同的成员）。
- **τ 几乎不敏感**：τ ∈ [0.3, 0.7] 范围内 Ref-Adv-s 平均准确率变化 **≤0.4 pt**、RefCOCO+ testB **≤1.0 pt**。→ **这是"可判规则 ≠ 又一个阈值"的最好证据**：决策是候选之间的**相对比较**，所以它的那个唯一超参（聚类 IoU）几乎不起作用。

**不适用点**：FORUM 的"不同成员"是**不同模型族**；我们只有一个模型 → "最多个不同模型支持的簇"必须替换成"最多个**不同上游回答/不同视角**支持的簇"。而我们网关的**上游本身不固定**（同图同提示词连发 10 次原文一致率仅 10%，本机实测）——所以重复调用可能天然就是"准不同成员"。**这一条是【推断】，需要一条 30 分钟的实验证**（见 §6 改动 7）。

### 2.5 FSR：多模型提供候选 + "选择"把漏检率从 13.67% 降到 9.33%

来源：`https://arxiv.org/abs/2610.25503`（*From Seeing to Reporting*, 2026-10-18），代码与全部 67,364 份存档回答：`https://github.com/shijunzhe/vlm-set-reporting`。
> ⚠️ 全文两次拉取均超时，本条只拿到**摘要原文**（下面数字与结论均出自摘要），标注【一手（摘要级）】；"删除操作的条件（Proposition 2）"只来自检索摘要，标【二手】。

**摘要原文的数字**【一手（摘要级）】：
- 在 **600 条 ScreenSpot-Pro** 上：GPT-6.1 的两段 zoom 漏检率 **13.67%** → 三模型组合的 GUI-grounding 层 **9.33%**；同样的三个 zoom 用**簇投票**合并是 **12.67%**（成本约相同）；同样三个模型做**朴素投票**是 **12.33%**。
- 摘要结论句："**the other models supply the candidates, and selecting among them turns those candidates into fewer misses**" —— 即：**贡献者提供候选、另一个环节负责选择**，比"投票"更有效。

**对我们的含义**【推断】：在 ScreenSpot-Pro 上，"多源提供候选 + 有判据地选择"这条路的公开量级是**漏检率降 3~4 个点**（13.67 → 9.33）。注意它同时说明**朴素投票（12.33%）比簇投票（12.67%）好**——所以"投票"本身不是重点，**"选择"才是**。

### 2.6 负面证据汇总：**置信度不是判据**

两篇独立一手来源都指向同一结论：
- FORUM §5.6【一手】：即使用户自报置信度/代理置信度可用，它**恰恰是在 distractor 上失效的那个信号**；"we therefore select without confidence rather than approximate one"。正确率随"同意模型数"**单调上升**。
- LFPR 的同骨干机制对照【一手】：把几何 guard 换成**冻结的、无标签的置信度阈值**，**连 guard 那点 Acc@0.5 增益都拿不到**。
→ **推论**：我们的 `GateCondition.LOW_CONFIDENCE`（`src/minicode/ground/gate.py`）这条路在公开证据上是被否掉的，**不要再往"提高置信度阈值"方向投入**。

---

## 3. 公开判据 → 我们的可判规则：形状对照表

| 公开判据 | 出处 | 推理时可得？ | 我们的对应量 |
|---|---|---|---|
| 跨视角支持 S_cross | ReClick 式(3) | ❌ 需 logprobs（我们没有）→ 改判别式版本 | 让另一个视角在 {p₀,p₁} 里二选一 |
| 空间一致性 S_agree | ReClick 式(4) | ✅ | 候选集内平均归一化距离取负 |
| 跨成员一致性（数成员） | FORUM 式(1) | ✅（我们用"不同上游回答/不同视角"代替"不同模型"） | 投票网格里每个簇的票数 |
| medoid（取真实成员，不平均） | FORUM 式(2) | ✅ | 当两个候选属于同一簇时取其中一个（或中点，见 §4.2） |
| 空间投票共识区域 | GUI-RC §3.2 式(4)(5) | ✅ | 全屏统一票表 → 最大票连通区域 |
| 几何 guard（z / IoU / 面积比） | LFPR 式(3)(4)(5)(6) | ✅ | 粗框 vs 精点扩成的方块 |
| 置信度校准 | — | ❌ **公开证据反对用它** | 不用 |
| 到 GT 的距离 `dist_to_bbox` | 我们自己的埋点 | ❌ **只有评测时可用**（推理时没有 GT） | 只能当**评测口径**，不能当运行时判据 |

---

## 4. 推荐规则全文

### 4.0 为什么要"两视图统一票表"（主推方案）

三个公开事实拼起来：

1. GUI-RC 证明：**同一张图多次预测的空间重叠，本身就是置信信号**；共识区域（最大票连通区）比任何单次预测准，在 ScreenSpot-Pro 上对通用模型 +3.41/+4.17。
2. ReClick 证明：**候选在另一个视角里也成立**（跨视角支持）是可判的、比"到 incumbent 的距离"更有效的信号。
3. FORUM 证明：**跨成员一致性是标签无关的正确性信号**，而**同模型自一致性在干扰项上不可靠**。

把它们合成一条：**在同一个全屏坐标系里，对"整屏视角"的 K 次采样与"crop 视角"的 K 次采样**共用**一张投票网格**。
- 真目标在整屏里**看得见** → 整屏采样会给它票；crop 采样也给它票 → **两视角合计票最高**。
- 精定位改坏的那 11 条：精点落在 crop 内某个 distractor 上 → 那个 distractor **只有 crop 视角的票**；真目标拿整屏视角的票 → **共识回到真目标**。
- 位移阈值会误伤的那 18 条（精点远但对）：真目标**同时被整屏视角支持** → 共识仍是真目标，**不需要任何位移阈值**。
- 那 20 条"crop 里没目标、模型照样给了点"的假命中：假目标只有 crop 视角的票 → 被稀释。

**规则里没有绝对阈值**：只有 α（点扩方块的边长）这一个空间尺度参数，与 GUI-RC 同构；且 α 可以用我们数据里的目标元素尺寸直接标定（icon 在模型输入图里中位 12.0 px / 15.75 px ⇒ α 至少要盖住 2×12 ≈ 24 px；按 GUI-RC 的 50 折算到我们的尺度：1536 档（2560→1536，降采样 0.6）≈ 30，2016 档（0.7875）≈ 39）。

### 4.1 伪代码

```
输入：整屏帧 F（物理像素）、描述 d
1. 整屏采样 K 次（同一 coarse_prompt）→ 粗点集 C = {c_i}（物理像素）；粗框 b_0 = 首次粗定位的框
2. 以 c_1 为中心开 crop（沿用现有 plan_crop）→ crop 采样 K 次（同一 refine_prompt）→ 精点集 R = {r_j}
3. 统一票表（全屏物理坐标系，粒度 = 整屏/2 或更粗）：
     对 C 中每个点：投「α 方块」票（我们另有粗框时改投框票）
     对 R 中每个点：投「α 方块」票
   v[p] = 覆盖该像素的票数
4. 共识（GUI-RC §3.2 原文）：v_max = max v；取所有「每像素都 = v_max」的连通区域；选面积最大者 r̂
5. 落点 = r̂ 中心（物理像素）；landing_source = 新增 "vote_consensus"
6. 兜底：C 或 R 为空 → 现行为（粗点 / 整屏第二遍 / 报失败），不合成坐标
```

### 4.2 中点融合：什么时候该用，什么时候绝对不能用

**可证明的部分**【本线推导 + LFPR 佐证】：GT bbox 是凸集，**若 p₀ 与 p₁ 都落在框内，则 ½(p₀+p₁) 必然落在框内**。所以在"两个候选都对"的那 64 条上，**中点融合 0 损失**（可证，不是希望）。而当前 always 臂里被改坏的 11 条**全部**属于"两个候选不一致"的类 → 中点在"同域"分支上不引入新风险。
**LFPR 的因子分解**支持这个读法：按 Acc@0.5（我们的 hit 口径）**中点融合 > guarded replacement**；按 Acc@0.9 才反过来。

**必须设边界**：中点在"粗点错（离真目标很远）+ 精点对"的那 38 条上是**风险区**（两点平均 = 两个证据被平均成噪声）。
→ **所以规则是二分支的**：
- **同域**（两候选指向同一元素、位移小）→ **中点**（0 损失，可证）
- **异域**（两候选指向不同元素）→ **不是融合问题，是选择问题** → 走 §4.1 的两视图票表 / ReClick 式 argmax
- **判据用几何 guard**：LFPR 的 `z ≤ 0.35`（crop 归一化位移）、`IoU ≥ 0.25`、`面积比 ∈ [0.5, 2.0]`，照抄（LFPR 的接受率 92.5%，是"松 guard"）；**guard 的作用是"剔除明显离群"，不是"当决断器"** —— 这正是我们"超阈值就回落"失败的原因（我们把 guard 当成了决断器）。

### 4.3 跨视角支持的无 logprobs 改造（判别式版）

ReClick 的 S_cross 需要 logprob，我们没有。可落地的等价物：**把"在另一个视角里给这个点打分"换成"在另一个视角里让模型在两个候选之间二选一"**。
- 做法：拿**整屏视角**（原始整屏图 + 同一个描述 + 候选标注，例如把两个候选点按坐标写进提示词），问模型"目标是 A 还是 B"；再拿 **crop 视角**做同样的二选一。
- 两个视角的**二选一结果一致**的那个候选拿到跨视角支持票；不一致就是"这个点只在某个视角成立"。
- 这是**判别式**的、只要 chat 接口的版本；【二手/推断】——公开文献里没有"logprob 缺失时的 S_cross 替代物"的评测数字，这一条是我们自己的改造，必须在我们的 183 条上实测。
- **代价**：每个候选多 1 次调用。按"性能优先"的约束这是可接受的。

### 4.4 我们已有埋点能支撑什么

| 我们的埋点 | 能不能当**推理时**判据 | 对应的公开先验 |
|---|---|---|
| `dist_to_bbox`（`eval/sspro.py:267`） | ❌ 需要 GT | 只能当评测口径 |
| `crop 含住率`（`ground/crop_geometry.py`，192→42.1%、512→71.0%） | ❌ 是**评测统计**，推理时无 GT 不可得 | 推理时可用代理：精点离 crop 中心的归一化距离 |
| 粗点与精定位点的相对位置 | ✅ | LFPR 的 z-guard（guard，不是决断器）；ReClick 的 S_agree |
| 粗定位框（`raw_bbox`，`channel.py:590`；`coarse_radius_px`，`channel.py:595`） | ✅ | **LFPR 的 incumbent 框**（IoU / 面积比 guard 直接可算）；GUI-RC 的框投票 |
| 精定位点是否落在 crop 窗口内 | ✅ | LFPR 的"不合成坐标、非法就保留原答案" |
| 精点离 crop 边缘的距离 | ✅ | ConClick 的 near/far 分类（3w×3h 邻域）的无框代理 |
| 模型自报置信度 | 若 API 给 | **公开证据反对**（FORUM §5.6；LFPR 机制对照） |
| 多视角票数（需新增采样） | ✅ | GUI-RC 共识区域；ReClick S_cross；FORUM 跨成员一致性 |

---

## 5. 已知风险与不适用声明

1. **GUI-RC 的两视图扩展是【推断】**：官方只验证了"同一视角 K 次采样"。"整屏票 + crop 票共用一张表"是由 ReClick（跨视角）与 FORUM（跨成员）支撑的组合，**不是 GUI-RC 自己的实验结论**。
2. **只有一个模型**：FORUM 明确说同模型自一致性在干扰项上不可靠；ConClick 测到多模型常选中同一 distractor（55.49%–79.14%）。我们的重复采样是否真的"不同上游"**未经验证** → 必须先做 §6 改动 7 的响应池实验来确认多样性来源。
3. **ReClick 的 S_cross 不可原样实现**（无 logprobs）→ §4.3 的判别式改造是【推断】，没有公开评测数字。
4. **LFPR / FSR / FORUM 都是自然图像的 referring-expression grounding**，不是 GUI 点击点；它们给的是**形状与量级**的先验，不是可直接搬到我们口径的数字。反过来，**GUI-RC、ReClick、ConClick 都是 ScreenSpot 系列**，口径与我们一致（"点落在标注框内"）。
5. **我们本地数据集缺 648 张**，全量 1581 跑不满；所有结论先在 183 条上做**配对**判定。

---

## 6. ≤8 条最小改动（按投入产出排序）

> **前置纪律（用户已给定）**：跨运行分数差混着模型随机性（网关忽略 temperature，10 次连发原文一致率仅 10%）→ **所有对比必须配对**。改动 7 的响应池就是这条纪律的落地。

| # | 改哪个文件 | 做什么 | 预期哪个数变多少 | 怎么验证 |
|---|---|---|---|---|
| **1** | 新增 `src/minicode/eval/fusion_offline.py`（写法照抄 `src/minicode/ground/crop_geometry.py` 的"离线几何重算、零模型调用"） | 读 `runs/g2-eval-final/sspro-report.json`，对每条样本重算 4 种落点的命中：coarse / refined(现状) / 中点(½,½) / 中点+LFPR guard，输出配对 McNemar 与逐条分类表 | **0 次模型调用**。预期：中点在"两者都对"的 64 条上 **0 损失（凸性可证）**；净变化区间 **−6 ~ +10 条（−3.3 ~ +5.5 pp）**，中点大概率取正值，**须实测** | `python -m minicode.eval.fusion_offline --run runs/g2-eval-final/sspro-report.json`；再跑 `--run runs/g3-eval-2016/sspro-report.json` |
| **2** | `src/minicode/eval/sspro.py`（`ArmOutcome`） | 补落两个字段：`coarse_bbox_px`（粗框的物理像素四元组，现在只落了归一化的 `coarse_bbox_model`）与 `crop_rect_px`（crop 窗口物理矩形） | 分数不变；把 LFPR 三个 guard 与 z **从"不可算"变成"可算"** —— 这是后面所有改动的地基 | 重跑一次 183 条评测，报告里出现新字段且与 `channel.py` 的 `plan` 一致 |
| **3** | 新增 `src/minicode/ground/fusion.py`（纯函数、零模型调用） | 实现 LFPR 式 guard + 融合：`decide(coarse_point, coarse_bbox, refined_point, crop_rect) -> (point, reason)`；guard 常量全部做参数（沿用 `ground/gate.py` 的 `GateParams` + `*_source` 来源记录风格） | 离线重算下，**guard+中点 vs 现状 always** 的配对差，**目标 ≥ +5 条（+2.7 pp）** | 改动 1 的工具里加一臂调用它 |
| **4** | `src/minicode/ground/channel.py:785`（`landing_source=LANDING_SOURCE_REFINED` 那一处之前） | 插入决策：guard 通过 → 落融合点；否则 → 落粗点，`landing_source` 记 `coarse`（新增 `FallbackCause.GUARD_REJECTED`） | 把"改坏 11 条"里 guard 能抓到的部分拉回来。公开依据：LFPR 的**无 guard 对照是 −2.926/−3.969/−5.748**，即**有 guard 的替换必须显著优于无 guard**，我们的目标 **≥ 0 净差** | 183 条配对评测 + 真机 wave1/wave2 |
| **5** | `src/minicode/ground/channel.py`（`_ask_text` L868 / `_ask` L881 / `_locate_by_point`） | 多采样：粗定位 K 次 + crop 精定位 K 次，**点集全部落进 `echoes`**（现在每次只留一个点） | 单独上线**不会**提升（只是噪声放大）；必须与改动 6 一起上才有意义 | `echoes` 里能看到 K 个 stage 点；改动 6 的评测 |
| **6** | 新增 `src/minicode/ground/vote_grid.py` | 实现 GUI-RC 式空间投票网格（`v[x,y]=Σ1[(x,y)∈R_k]`，框式直接投框、点式投 α 方块）+ 共识提取（最大票连通区里取面积最大者） | **两视图共用一张票表** vs 现状 always：**+8 ~ +15 条（+4.4 ~ +8.2 pp）**。先验：GUI-RC 在 ScreenSpot-Pro 对**通用模型** +3.41/+4.17（我们是通用 API 模型，取这一档），且它专治的两类幻觉（选错元素 / 粒度偏大）正是我们的两类主要错误。**这是本线最大的单项赌注** | 改动 8 的配对评测；另测 α ∈ {24, 30, 39, 60} 的敏感性 |
| **7** | 新增 `src/minicode/ground/response_pool.py` + `src/minicode/model/client.py:400 complete()` 旁路 | **响应池**：对 (image_hash, prompt, stage) 缓存上游原文；所有臂从同一份池子取 | 分数不变；**把"跨运行分数差混着模型随机性"从评测里彻底剔除**，并且一次预生成、无限次重算 | 同一臂跑两遍，逐条上游原文一致率 **100%**；顺带统计池内 K 次回答的**两两落点一致率**，验证"上游多样性"是否真的存在（FORUM 说的跨成员 vs 同成员差别就靠它） |
| **8** | `src/minicode/eval/__main__.py:424`（写 `sspro-report.json` 处） | 加新臂 `two_view_vote`（含：中点 / guard+中点 / medoid / 两视图投票），**全部用同一份响应池**跑 `coarse_only` / `always` / 4 个新臂；输出配对 McNemar + 逐条 Δ 分类表（保留"11 条改坏 / 38 条救回 / 18 条阈值误伤"三类的标签） | 给出"哪个数变多少"的**实测**数字，替代本报告里的先验区间 | `python -m minicode.eval --run ...`；报告新增 `paired` 段 |

---

## 7. 公开数字汇总（全部可点开）

| 来源 | 指标 | 数字 | URL |
|---|---|---|---|
| GUI-RC【一手】 | ScreenSpot-Pro，Qwen2.5-VL-3B / 7B | +3.41 / +4.17 | https://arxiv.org/abs/2508.05615 |
| GUI-RC【一手】 | ScreenSpot-Pro，OS-Atlas / UI-TARS（GUI 专用） | +0.16 / +0.26 | 同上 |
| GUI-RC【一手】 | 采样数 / 点扩方块 α | K=64 / α=50 | https://github.com/zju-real/gui-rcpo |
| LFPR【一手】 | 无 guard 对照（同批候选） | Acc@0.5 **−2.926**、Acc@0.75 −3.969、Acc@0.9 **−5.748** | https://arxiv.org/abs/2608.19553 |
| LFPR【一手】 | guard 接受率 | 29,523 / 31,920 = **92.5%** | 同上 |
| LFPR【一手】 | guard×fusion 因子分解 | Acc@0.5 **中点 > 替换**；Acc@0.9 / mAcc **替换 > 中点** | 同上 |
| LFPR【一手】 | guard 阈值 | z≤0.35、IoU≥0.25、面积比∈[0.5,2.0]、γ=2.0、router s<128px | 同上 |
| LFPR【一手】 | Ref-L4（31,921） | mAcc₀.₅:₀.₉₅ 72.947→**76.013**；Acc@0.5 88.531→**89.725** | 同上 |
| LFPR【一手】 | 置信度门控机制对照 | 连 guard 的 Acc@0.5 增益都拿不到 | 同上 |
| ReClick【一手】 | ScreenSpot-Pro，Qwen3-VL-8B | 54.71 → 66.03 → **72.36** | https://arxiv.org/abs/2610.13268 |
| ReClick【一手】 | 三集平均增益 | **+2.96 pp** | 同上 |
| ReClick【一手】 | 错误落在非目标元素的比例 | 63.68%–95.43%（50 个模型×数据集组合） | 同上 |
| ReClick【一手】 | 多模型选中同一 distractor | 55.49%–79.14%（≥2 个错误预测的样本） | 同上 |
| FORUM【一手】 | 跨族 vs 同族误差相关 | **0.47 vs 0.63** | https://arxiv.org/abs/2609.37488 |
| FORUM【一手】 | 聚类阈值敏感性 τ∈[0.3,0.7] | Ref-Adv-s ≤**0.4 pt**、RefCOCO+ testB ≤**1.0 pt** | 同上 |
| FORUM【一手】 | 平均 vs medoid | 粗成员一进来，平均在严 IoU 上崩，medoid 不受影响 | 同上 |
| FSR【一手（摘要级）】 | ScreenSpot-Pro 600 条漏检率 | 三模型两段 zoom 13.67% → **9.33%**；簇投票 12.67%；朴素投票 12.33% | https://arxiv.org/abs/2610.25503 |
| ConClick【二手】 | 代码 | `github.com/zhangruotian/ConClick` | https://github.com/zhangruotian/ConClick |
| FSR【二手】 | 代码 + 67,364 份存档回答 | `github.com/shijunzhe/vlm-set-reporting` | https://github.com/shijunzhe/vlm-set-reporting |
| SafeGround【二手】 | 空间密度图 → 分歧/离散/集中度 → execute/abstain/cascade | （本线未取全文，不作判据依据） | https://safeground-ericlab.github.io/ |
| See-Point-Refine【二手】 | 多轮"指→看→修"闭环，MIT/CMU | （本线未取全文，不作判据依据） | https://arxiv.org/abs/2604.13019 |

---

## 8. 不适用 / 未取到一手的事项（明确声明）

1. **FSR 全文**（含"删除操作的条件 / Proposition 2"、 unanimity/majority/intersection/union 四种集合构造）：两次拉取均超时，**只拿到摘要**。相关表述标【一手（摘要级）】或【二手】。
2. **GUI-RC 的 `evaluation.py` 源码**：`raw.githubusercontent.com` 连接超时，代码位置与可调项取自**官方 README**（一手），未逐行核对实现。
3. **ReClick 的 S_cross 在无 logprobs 条件下的等价物**：公开文献**没有**这样的评测；§4.3 的判别式改造是【推断】，必须自行实测。
4. **"两视图统一票表"**：公开工作里**没有**这个精确构造（GUI-RC 只做单视图 K 采样）。它由 ReClick + FORUM 的公开结论支撑，属【推断】。
5. **LFPR / FSR / FORUM 的域不匹配**：它们是自然图像的 referring-expression grounding，我们的口径是 GUI 点击点命中；其数字只能作为**机制与量级**的先验，**不能直接当作我们的预期收益**。

# r-s6 — 多采样 / 多尺度 / 多候选投票：能不能把准确率再推一截

> 调研线：S6 —— 既然性能优先、成本不管，靠「多模型 / 多尺度 / 多候选」投票能不能再拿点。
> 证据等级：**【一手】**= 我这一轮亲自打开并读了原文/官方仓库；**【二手】**= 只拿到搜索摘要或转述，原文没打开；**【推断】**= 我基于一手的数字做的推算/建议。
> 一手来源清单：GUI-RC（arXiv:2508.05615 + 官方仓库 README）、UI-Zoomer（arXiv:2604.14113v1 HTML + 官方仓库 README）、RegionFocus（arXiv:2505.00684）、ScreenSpot-Pro（arXiv:2504.07981）、FSR（arXiv:2610.25503）。
> 未能打开：CU Boulder《Budgeted Multi-Scale Inference》PDF（fetch 失败）、GUI-RC 仓库的 `evaluation.py`（404，该路径下没有这个文件）→ 这两条下面的数字全部按【二手】标。

---

## 0. 一句话结论

**能，但赢的不是「投票」，是「用采样投票造出一个置信度门控 + 一个候选集」**；公开数字里纯多数决只值 ~1 个点，而「投票 + 选择」值 ~4 个点。对我们最直接的三条落点是：① GUI-RC 的空间投票网格在**出框**那一级最有效（我们粗定位正好是出框）；② UI-Zoomer 证明**无条件放大反而降准确率**（81.84%→77.20%），这正好是我们「精定位改坏 11 条」的公开版解释；③ FSR 证明**从候选集里选**远好于**投票/取质心**（miss 9.33% vs 12.33%/12.67%），我们仓库里已经有一条「编号选择、落点取候选框物理中心」的现成通道可以直接复用。

---

## 1. (a) 多次采样 + 投票/多数决在 GUI grounding 上的公开做法与增益

### 1.1 GUI-RC —— 目前最直接对口的一手实现【一手】

来源：<https://arxiv.org/abs/2508.05615> ｜全文 HTML <https://ar5iv.labs.arxiv.org/html/2508.05615> ｜官方仓库 <https://github.com/ZJU-REAL/GUI-RCPO> ｜README <https://raw.githubusercontent.com/ZJU-REAL/GUI-RCPO/main/README.md>（AAAI 2026，浙江大学）

**方法（照抄参数）**：
- 采样 `SAMPLE_NUM = 64`，`top_p = 0.95`，`TEMPERATURE` 论文写 0.5、仓库 README 写默认 0.7（两处不一致，README 是可跑的默认值）。
- 点式模型把每个预测点**扩成边长 α = 50 的正方形**（`POINT_EXPAND_SIZE = 50`，README），在**与截图同分辨率**的投票网格上逐像素累加。
- 取 `v_max = max v_{x,y}` → 找出**每个像素都等于 v_max 的连通区域** → 取**面积最大**的那块作为 consensus region。
- 一手原文：「point-style 预测模型的提升明显小于 bbox-style」—— 因为把点扩成正方形只是**模拟**注意力范围，会引入偏差。这条对我们很关键：我们的粗定位是**出框**的（`ground/prompt.py:135` `coarse_prompt` 问的是 bounding box），正好落在受益的那一档。

**公开增益（Table 1 / README，逐字抄）**：

| 模型 | SSv2.avg | SSv1.avg | **SSPro.avg** |
|---|---|---|---|
| OS-Atlas-Base-7B（框式） | 80.82→83.57 **+2.75** | 79.80→81.45 +1.65 | 18.41→19.67 **+0.16** |
| Qwen2.5-VL-3B-Instruct（框式） | 80.11→82.63 **+2.52** | 76.97→78.46 +1.49 | 20.18→23.59 **+3.41** |
| Qwen2.5-VL-7B-Instruct（框式） | 86.48→88.52 +2.04 | 84.20→85.53 +1.33 | 19.80→23.97 **+4.17** |
| UI-TARS-1.5-7B（点式） | 90.17→91.12 +0.95 | 87.74→88.52 +0.78 | 40.92→41.18 **+0.26** |
| UGround-V1-7B（点式） | 89.62→89.62 +0.00 | 87.11→87.34 +0.23 | 31.50→31.63 **+0.13** |
| InternVL3-8B（点式） | 80.97→81.68 +0.71 | 79.72→80.03 +0.31 | 13.28→12.46 **−0.82（掉了！）** |

**可迁移的数字**：GUI-RC 在 ScreenSpot-Pro 上的框式通用模型增益是 **+3.41 / +4.17 pp**；点在 SSPro 上只有 +0.13~+0.26，甚至有 −0.82 的反例。→ 我们**把投票放在出框的粗定位级**，不要放在出点的 crop 精定位级。

**GUI-RCPO（同一论文的另一半）**：把共识度当 reward 做 GRPO（16 samples, T=0.7, top_p=0.95, lr 1e-6, KL β=0.04, 2 epochs）：Qwen2.5-VL-3B SSv2 80.11→**85.14 (+5.03)**、SSPro 20.18→**24.67 (+4.49)**；Qwen2.5-VL-7B SSPro 19.80→**25.93 (+6.13)**。这条要 TTRL 训练环境，本轮**不适用**（我们是 API 模型、不能改权重），但它说明**共识信号本身还有 4~6 pp 的上限空间**，是给「投票路线」加分的证据。

### 1.2 「多次采样投票」在 grounding 上的收益天花板（有反例，要当心）【二手】

- 【二手】VISTA（arXiv:2606.14579）：同一截图上重复 rollout 会**塌成几乎一样的坐标串**，**有信息的组不到 5%** → 说明**朴素的字符串级 self-consistency 在 grounding 上头部空间有限**，必须做到**区域级**共识（这正是 GUI-RC 用空间网格而不是答案直方图的原因）。
- 【二手】VisConf（arXiv:2610.24526）：VLM 的 best-of-N 光靠「答案频次 + 置信度」不够，要再加**视觉参与度**信号；增益最大的时候正是**正确答案被采到了但不是多数**的时候。
- 【二手】MVP（CVPR 2026，《Multiple View Prediction Improves GUI Grounding》）：多视角聚合用**空间聚类**；引用的 ScreenSpot-Pro 上单次推理 49.8%，「两次里任一次对」是 57.5% → **oracle-of-2 有 +7.7 pp 空间**，且这个空间要靠聚合才能兑现。

### 1.3 区域聚焦式放大（RegionFocus）【一手】

<https://arxiv.org/abs/2505.00684> ｜ <https://github.com/tiangeluo/RegionFocus>（ICCV 2025）
ScreenSpot-pro 上 **+28%+**、WebVoyager **+24%+**，Qwen2.5-VL-72B 上 ScreenSpot-Pro 打到 **61.6%**。→ 佐证「缩放/裁剪」这条线的天花板远高于「再调一次提示词」。

### 1.4 ScreenSpot-Pro 基准本身给的坐标【一手】

<https://arxiv.org/abs/2504.07981>：现有 grounding 模型最好只有 **18.9%**；他们自己的 ScreenSeekeR（免训练、级联视觉搜索）到 **48.1%**；排行榜 <https://gui-agent.github.io/grounding-leaderboard>。→ 官方结论一句话：**「有策略地缩小搜索区域就能提升准确率」**。这是我们「多尺度」路线的公开背书。

---

## 2. (b) 多尺度 / 多裁剪档位各定位一次再投票

### 2.1 有公开数字的两条【二手 / 一手混合】

- 【二手】CU Boulder《Improving GUI Grounding for Computer-Use Agents with Budgeted Multi-Scale Inference》（课程项目 PDF，<https://home.cs.colorado.edu/~DrG/Courses/2026-Spring-NN/FinalProjects/GUIGrounding.pdf>，我这一轮 fetch 失败，只拿到搜索摘要）：对比 B=1（单次）、B=3（中心放大两段）、B=7（**多尺度**：中心 + 4 个 25% 位移的方向邻居共 5 个重叠裁剪，再用几何模型挑「最一致」的那次预测，再加一次 25% 精修裁剪）。报告的甜点是 **B=5（ScreenSpot-Pro ≈38.1%）**，且**免 planner 的多尺度版本拿到了 ScreenSeekER 增益的 ~82%**。要点：**用 4~5 个重叠裁剪当「多视角」，比单点中心放大更能救回「递归放大跑偏」的错误**。
- 【一手】UI-Zoomer（arXiv:2604.14113v1，<https://arxiv.org/html/2604.14113v1>）是**不确定性驱动的自适应缩放**，不是固定多档位：Stage 1 采 **N=8** 个候选框（T=0.9）→ Stage 2 用**空间共识 + token 置信度**打分决定「要不要放大」→ Stage 3 用**方差分解**算每条样本自己的裁剪半径 → 裁剪后**再跑一次确定性（T=0）**推理。它的 SSPro 增益见 §3。

### 2.2 我们已有的两档（crop 192 与 0.5× 当前帧）正好是天然投票源【推断，基于本仓代码】

- 本仓 `ground/crop_geometry.py:197` `candidate_geometries()` 已经列了固定档位 **192 / 512 / 768 / 1024² / 1280×800**；`ground/channel.py:431` `_crop_size_for()` 的生产默认是**当前物理帧 50% 线性尺寸、下限 512**（自适应档）。
- 这两档的**几何性质已经量过**（本仓 `channel.py:151-166` 的离线表）：192×192 含住率 42.1%、窗口无目标率 36.1%；0.5×当前帧含住率 **84.2%**、无目标率 **14.2%**。→ 【推断】**192 档作为「第二意见」的价值不在它自己的精度，而在它与 0.5× 档的一致性**：两者同时指向同一个框 ⇒ 强信号；两者不一致 ⇒ 触发一次更细的判别。
- 官方屏幕尺寸必须分清：crop 那一级是**从物理帧 1:1 裁、不缩放**（`ground/channel.py:899-900`），所以 crop 里的图标是**物理像素**；而粗定位那一级送的是缩到声明尺寸（1536 或 2016）的整屏图。两档坐标**不在同一空间**，投票必须**先各自映射回物理像素**再比。

---

## 3. (d) n 次采样的边际收益曲线长什么样

- 【一手】GUI-RC 用 **K=64**、T=0.5/top_p 0.95（README：SAMPLE_NUM 64、TEMPERATURE 0.7、POINT_EXPAND_SIZE 50）。论文正文（我读到的 §3.2/§4.1/§4.2）**没有给出 n 的消融曲线**，README 也没有。
- 【一手】UI-Zoomer 用 **N=8**、T=0.9（§3.2「We sample N = 8 candidate boxes from M at temperature T = 0.9」）。
- 【二手】UI-Zoomer 项目页/论文 §5.1.3「Analysis of Sampling Number and Temperature」：**准确率随温度单调上升，T=0.1 时 54.46% → T=0.9 时 61.80%**，T=1.0 略降；**N=8 是准确率-开销的甜点，N=12/16 反而略降**（原因：多出来的样本给方差估计灌噪声，而不是给投票灌信息）。
- 【一手】这两组数字不矛盾，因为**两者用 K 的方式不同**：
  - GUI-RC：K=64 用来在**整屏**上把投票网格的 max 投票区**稳定下来**（票越多，偶然重叠越难赢）→ 票数少时噪声候选容易互相「串通」成一个假簇。
  - UI-Zoomer：N=8 用来当**门控信号 + 方差估计**（判断「模型自己都不确定」），最终落点由裁剪后**一次 T=0 确定性推理**给出 → 投票不负责最终落点，就不需要很多票。
  → 【推断】**给我们的直接含义**：想把投票当「最终答案」→ N 要大（≥16，越大越好但边际递减）；想把投票当「置信度门控」→ **N=8 就够**，再多只是浪费（而且我们不心疼 token，但会拉长真机端到端时延，所以门控路线仍取 8）。

**公开世界里没有一张「通用 API 模型 + ScreenSpot-Pro + n=3/5/10」的曲线**（我这一轮只找到 GUI-RC 的 64 与 UI-Zoomer 的 8 两个点）。→ 【推断】**这条曲线必须我们自己测**，而且测法必须是**离线重放**：同一批 N 次采样全部录下来，再离线按 n=1..N 逐级算命中率 —— 这样 n 的曲线和「取质心还是取中位」的选择**都不用重新调模型**（见 §6 改动 #2）。

---

## 4. (e) 投票怎么落点：坐标平均？中位？簇中心？

公开做法分三派，数字指向同一件事：**别平均**。

1. **空间投票网格**（GUI-RC）【一手】：不是对坐标做平均，而是**在网格上找「票数最多的连通区域」**，取面积最大的一块。→ 落点应该是**这块共识区域的位置**，不是所有点的算术平均。原文只写到「consensus region」为止，落点是否取该区域中心 —— 论文没说清，仓库 `evaluation.py` 我没打开（404）→ **【推断】**取区域中心或区域中票最高处，二者在紧簇上差别 < α/2。
2. **IoU 邻居支持 + 置信度 tie-break**（UI-Zoomer Eq. 5）【一手】：`v_i = #{j≠i : IoU(b_i,b_j) > 0.5}`，取 `argmax_i (v_i, c_i)` —— **落在某个真实采样出来的框上**，不是新造一个坐标。配套的两条也直接抄：
   - Eq. 6 **离群过滤**：只留**中心离中位中心最近**的 `K = ⌊0.75N⌋` 个候选（**中位**做锚，不是均值）；消融标题原文「Retaining the top 75% of candidates yields the best accuracy」。
   - Eq. 8-10 **方差分解**：`v_inter`（样本间位置离散）+ `v_intra`（框自身尺寸 /4²），`r = γσ`，边长 `s = max(2r_x, 2r_y, m)`，**正方形**裁剪、越界向内平移；消融原文「Using a square crop improves accuracy」。
3. **质心 / 中点融合在公开实测里是负收益**【二手】：zoom-consistency-routing（<https://github.com/omxyz/zoom-consistency-routing/blob/main/paper.md>）ScreenSpot-Pro 表里，**Vote agree T=50 = 76.9%（比基线 −3.2）**、**Midpoint fusion = 75.7%（−4.4）**、**Step-1 vote centroid = 72.5%（−7.6）**，而它自己的 **Consistency Router = 80.9%（比单模型基线 +0.8）**、Oracle 85.1%。⚠️ 这张表里的绝对值（基线 80.1%）高于同期公开 ScreenSpot-Pro 榜单水平，**怀疑是子集或自定义协议**，所以只采信**相对关系**（质心融合最差、路由最好），不采信绝对数。

**为什么「平均」在我们这里特别危险**：我们的 icon 在模型输入图里中位 12.0 px（1536 档）/ 15.75 px（2016 档）。目标一旦是**双峰**（模型一会儿指 A 图标、一会儿指 B 图标），两个峰的**均值落在两图标之间的空白上** → 必然假命中。**簇内取真实采样点（medoid）不会产生这种空白点。**

**FSR 给了这一节的最终裁决**【一手】（见 §5）：**「选」>「投」>「聚类投票」>「单模型」**。

---

## 5. (c) 跨模型投票：公开数字是**负收益**，赢面在「候选来源 + 选择」

FSR《From Seeing to Reporting》（arXiv:2610.25503，<https://arxiv.org/abs/2610.25503> ｜代码 <https://github.com/shijunzhe/vlm-set-reporting>）【一手】，600 条 ScreenSpot-Pro：

| 方案 | miss rate |
|---|---|
| GPT-6.1 单模型两级放大（baseline） | 13.67% |
| 同样三个模型的**普通投票** | **12.33%** |
| 三个放大结果**按簇投票** | **12.67%** |
| 三个模型的候选 → **选择**（selection tier） | **9.33%** |

→ **投票只买到 1.0~1.3 pp；选择买到 4.34 pp。** 论文原话：「the other models supply the **candidates**, and **selecting among them** turns those candidates into fewer misses.」
→ 配合 §4 的负收益表（朴素投票 −3.2~−7.6 pp），**「跨模型投票」这条路在我们这里是 不适用 / 需改造**：
- 作为**投票器**：不适用（公开数字一致显示负收益）。
- 作为**候选来源 + 让强模型来选**：需改造，且**第二个「模型」不必是另一个 VLM** —— 我们仓库里现成的第二意见是 **OCR / UIA 候选源**（`ground/candidates.py`，`merge_candidate_sources`），而**「编号选择、落点取候选框物理中心」的通道已经写好了**（`ground/channel.py:982` `_numbered()`、`ground/prompt.py:230` `numbered_candidates_prompt`，落点零换算、模型坐标误差不进落点）。
→ 约束提醒：本机 RTX 3060 Laptop **6 GB 显存**，7B 级别的 grounding 模型（UGround/OS-Atlas/UI-Venus-Ground）要作为**常驻第二模型**塞进 6 GB 显存并与桌面同时运行是紧的；但 FSR 的协议只需要第二个模型**产出候选**，不要求它投票 → 【推断】先用现成的 OCR/UIA 候选源当「第二个模型」，成本与显存都是零。

---

## 6. 对我们四个卡点的映射（哪些能直接抄、哪些不适用）

| 我们的卡点 | 公开对策 | 能不能直接抄 |
|---|---|---|
| ① 精定位没护栏（75 条里改坏 11 条） | UI-Zoomer **Table 1**【一手】：无条件迭代裁剪在 ScreenSpot-v2 上把 81.84% **降到** 77.20%（时间 35:47→6:43:07）；门控式则 +4.2（SSv2）/+13.4（SSPro） | **能**。把「阈值判偏离」换成「**采样一致 ⇒ 不开精定位**」 |
| ② 假命中 20 条（crop 里没目标也给了点） | 我们的 crop 提示词**已有** null 负出口（`ground/prompt.py:188`）；缺的是「多次采样里大家都说没有」这种**集体否定** | **能**，且不需要新提示词 |
| ③ icon 子集 41.38%（要 ≥45%） | UI-Zoomer SSPro 表【一手】：Qwen2.5-VL-7B icon **6.6→19.9（+13.3）**、text 40.6→54.0；UI-Venus-7B icon 22.9→35.4 → **icon 永远是放大/聚合收益最大的子集** | **能**，但要配合 §4 的「别平均」 |
| ④ 真机端到端 wave1 0/5 | 本线的投票/多尺度**只修视觉落点**，修不了 UIA 读不到文本、没登录、浏览器没滚动 | **不适用**（属 S5 线） |

---

## 7. ≤8 条最小改动（按投入产出排）

| # | 改哪个文件 | 做什么 | 预期哪个数变多少【推断，锚在公开数字上】 | 怎么验证 |
|---|---|---|---|---|
| 1 | 新增 `src/minicode/ground/vote_probe.py`（不改生产路径） | 在 183 条样本的**粗定位提示词**上跑 N=8 次，逐条落盘 8 个原始 bbox + 两两 IoU 矩阵 | **不改准确率**，产出「坐标方差是否存在 + oracle-of-8」两个决定性数：若 pairwise IoU 中位 >0.5 ⇒ 粗定位投票没肉（省得白做）；若 oracle-of-8 比单次高 ≥10 pp ⇒ 投票/选择路线直接可押 | 产物 `runs/s6-vote-probe/vote-probe.json`；判定 oracle-of-8 ≥ 单次 + 8 pp 才继续 |
| 2 | `src/minicode/eval/sspro.py`（`run_arms` / `ArmOutcome`）＋ `src/minicode/ground/channel.py`（`_locate_by_point`） | 粗定位一次改成**采 N=8 次并把 8 个原始回答全部录进 outcome**；新增一个「投票臂」与现有臂**共用同一批上游回答** | 准确率当轮不变；但把「N 次采样」的成本一次性付掉，之后 n=1..8 的曲线、medoid vs 中位 vs 网格票**全部离线重放**，不再调模型 | `runs/s6-vote-*/sspro-report.json` 里能重放出 n=1 的结果与现有 `always` 臂**逐条相同**（同一批上游回答 ⇒ 零随机性差） |
| 3 | `src/minicode/ground/channel.py`（`_locate_by_point`，接在改动 #2 之后） | **GUI-RC 式空间投票**：把 8 个粗定位 bbox 投到模型输入分辨率的网格上，max 票连通区域取最大面积块，落点取该块位置；框宽不一致时用 IoU>0.5 的邻居支持 + 落在真实采样框上 | 粗定位级 ScreenSpot-Pro 命中 **+1 ~ +4 pp**（公开锚：GUI-RC 框式通用模型 SSPro +3.41/+4.17）；但**只在 vote_probe 证明坐标方差存在时才做** | 2016 档 183 条，**与现有 always 臂配对 McNemar**（`eval/stats.py:73` `mcnemar_midp`），要求 p<0.05 且 Δ>0 |
| 4 | `src/minicode/ground/channel.py`（`_locate_by_point` 门控处）＋ `src/minicode/ground/gate.py`（`GateParams`） | **UI-Zoomer 式置信度门控**：`C_spatial` = 8 个框的**平均两两 IoU**；`C_spatial` 高 ⇒ 粗定位已可靠 ⇒ **跳过 crop 精定位**；低 ⇒ 才开精定位并按方差分解定裁剪半径 | **专治「11 条被改坏」**：公开锚 UI-Zoomer「无条件裁剪 −4.64 pp / 门控式 +4.2~+13.4 pp」。预期把那 11 条里的大部分收回 → **always 臂净 +2 ~ +5 pp** | 同上配对 McNemar；另单独报「精定位改坏条数」从 11 降到几条（`ArmOutcome.fallback` + `refined_point_px` 已够算） |
| 5 | `src/minicode/ground/channel.py`（`_numbered` 已有）＋ `src/minicode/ground/prompt.py`（`numbered_candidates_prompt` 已有） | **把多尺度候选做成「清单」，让模型选编号，落点取候选框物理中心**：候选 = 0.5× 档 crop 与 192 档 crop 各自定位出的框（+ 现成 OCR/UIA 候选），模型只答编号 | **icon 子集 41.38% → 目标 ≥45%**（需 36→39 条，+3.6 pp）。公开锚：FSR「选择」miss 9.33% vs 投票 12.33%；UI-Zoomer icon 增益 +13.3；【二手】CU 多尺度甜点 B=5 | 87 条 icon 子集单列 + 183 条总表，配对 McNemar；**必须同时确认「假命中」不涨**（`crop_contains_target` / `dist_to_bbox` 列已有） |
| 6 | `src/minicode/ground/channel.py`（精定位调用处） | **集体否定投票**：精定位也采 N' 次（同一 crop、同一提示词），统计回答 null 的比例；null 占多数 ⇒ 认定「这个 crop 里没有目标」⇒ **回落到粗定位**而不是硬落一个点 | 直接冲 **20 条假命中**。预期 20 条里多数转为「回落粗定位」，其中一部分粗定位本来是对的 ⇒ **净 +1 ~ +4 pp**；最坏情况是中性（回落不亏） | 落盘「null 票数」新列；核对这批条目最终命中数变化 |
| 7 | `src/minicode/ground/channel.py`（落点计算处）+ `ground/crop.py` | **落点规则写死成「簇内 medoid / 共识区域」，禁止对坐标取算术平均**；裁剪半径用 UI-Zoomer 的 `r = γσ`（σ² = 样本间方差 + (框尺寸/4)²），离群只留离**中位**最近的 75%，裁剪**取正方形**、越界向内平移 | 不单独提准确率；它是 #3/#5/#6 的**正确性前提**，防「双峰均值落在两图标之间的空白」 | 单元测试：双峰输入（两个相距 >3 框宽的点）时落点必须落在其中一个簇内，不得落在两簇之间 |
| 8 | `src/minicode/model/config.py` + `src/minicode/model/client.py` | 把 `n_samples`、`temperature`、`top_p` 提成**运行时参数并逐请求记录**（含「同一提示词的回答一致率」探针） | 不改准确率；但它是 #1/#3/#6 的**可复现前提**：网关当前忽略 temperature，我们只能**观测**方差不能**设定**方差 → 观测值必须落盘 | 记录进 request log；`runs/s6-*/report.json` 每条样本能查到 8 次回答各自原文 |

**顺序说明**：#1 是**决策门**（坐标方差不存在 → #3 直接不做）；#2 是**基础设施**（让 #3/#4/#5/#6 的所有变体都能离线配对重放，避免「跨运行分数差混随机性」）；#3/#4/#5/#6 是三条可并行的收益线；#7 是它们的正确性护栏；#8 让一切可复现。

---

## 8. 明确「不适用 / 不建议」的

1. **朴素多数决本身**（把 N 个点投票取最多的一簇的**均值**）——公开数字是负的：FSR 三个模型普通投票 12.33% miss vs 单模型 13.67%（只 +1.3 pp），簇投票 12.67% 反而更差【一手】；zoom-consistency-routing 质心融合 −7.6 pp【二手】。
2. **「跨模型投票」当主路线**——【一手】FSR +【二手】zoom-consistency-routing 都指向负收益；FSR 的正收益全在「**选择**」上。
3. **把 N 调到 64/100 再采样**——【二手】UI-Zoomer 的 N 消融说 N=8 已是甜点、N=12/16 反而略降（对门控/方差用途而言）；若投票要当最终答案则需要更多票，但那时收益会由「投票网格偶然串通假簇」主导，边际递减【推断】。
4. **GUI-RCPO 的 TTRL 训练**——需要能改权重的本地训练环境，我们是 API 模型，**不适用**。
5. **本轮不解决真机端到端的 UIA 文本不可读 / 浏览器未登录 / 无滚动动作**——那是 S5 线的范围；本线的投票只提升**视觉落点**那一段。

---

## 9. URL 清单（一手 6 条 + 二手 5 条）

一手（我这一轮亲自打开并读过正文/README）：
- https://arxiv.org/abs/2508.05615 ｜ https://ar5iv.labs.arxiv.org/html/2508.05615 ｜ https://github.com/ZJU-REAL/GUI-RCPO ｜ https://raw.githubusercontent.com/ZJU-REAL/GUI-RCPO/main/README.md
- https://arxiv.org/abs/2604.14113 ｜ https://arxiv.org/html/2604.14113v1 ｜ https://raw.githubusercontent.com/ZJU-REAL/UI-Zoomer/main/README.md
- https://arxiv.org/abs/2505.00684 ｜ https://github.com/tiangeluo/RegionFocus
- https://arxiv.org/abs/2504.07981 ｜ https://gui-agent.github.io/grounding-leaderboard
- https://arxiv.org/abs/2610.25503 ｜ https://github.com/shijunzhe/vlm-set-reporting

二手（只拿到摘要/转述，原文未打开）：
- https://home.cs.colorado.edu/~DrG/Courses/2026-Spring-NN/FinalProjects/GUIGrounding.pdf（fetch 失败）
- https://github.com/omxyz/zoom-consistency-routing/blob/main/paper.md
- https://arxiv.org/abs/2610.24526（VisConf）｜ https://arxiv.org/abs/2606.14579（VISTA）｜ MVP《Multiple View Prediction Improves GUI Grounding》(CVPR 2026)

# r8 · 配对评测线：怎么让 3~10 pp 的改进可判定

**调研线**：GUI grounding 评测里如何控制模型随机性 (a)、配对/统计检验的公开用法 (b)、
ScreenSpot-Pro 官方 eval 的抽样与统计口径 (c)、「困难子集放大效应」的先例与偏差 (d)。

**一句话结论**：**183 条 + 端到端重跑 = 只能判定 ≥9~11 pp 的效应，3.3 pp 在数学上不可判定**；
把设计改成「上游冻结 + 全量 1581 条 + 配对 McNemar（mid-p）」后，3.3 pp 的 MDE 降到 **2.2 pp**，
可判定。且**官方 ScreenSpot-Pro 完全没有统计口径可抄**（单次推理、无 CI、无显著性检验）——
这条线没有「官方数字」，我们必须自己立协议并预注册。

**证据强度标注**：【一手】= 我这次实际打开的官方源码/论文正文/文献摘要原文；
【二手】= 检索片段转述（URL 可打开，我未逐字核）；【推断】= 我自己算/推的（给算式）。

---

## 0. 结论速览（可直接照抄的参数）

| 决策项 | 现在 | 建议 | 依据 |
|---|---|---|---|
| 主检验样本量 | 183 条（分层抽样） | **全量 1581 条**（官方全集） | MDE ∝ √m/N；见 §4 |
| 比较方式 | 两臂各自端到端重跑 | **配对**：同一次上游回答复用，只换被测那一步 | §3.2 |
| 检验 | 无 | **McNemar mid-p**（双侧，α=0.05）；备选精确二项 / χ²+连续性校正 | §3.3 |
| 单臂区间 | 无 | **Wilson 95% CI** | §3.4 |
| 随机性控制 | 未钉 temperature（`grep temperature src/` = 0 命中） | **temperature=0 + 固定 seed + 上游冻结重放** | §2.1 |
| 困难子集 | 事后看结果挑 | **预注册**（由冻结基线产物定义），Holm 校正其余分层 | §5 |
| 官方口径 | — | 官方：**无 CI、无重复采样、无检验**；只 `torch.manual_seed(114514)` + `temperature=0` | §4 |

**要判定的效应 ↔ 需要的样本量**（配对，α=0.05 双侧，power 80%）：
`N ≈ 7.849 · π_d / Δ²`（π_d = 不一致对占比）——自算【推断】。

| 待测效应 Δ | π_d=0.05 | π_d=0.10 | π_d=0.20 | π_d=0.30 |
|---|---|---|---|---|
| 3.3 pp | 360 | **721** | 1441 | 2162 |
| 5 pp | 157 | **314** | 628 | 942 |
| 10 pp | 39 | **78** | **157** | 236 |

→ **3.3 pp 要可判定，N 必须 ≥721（π_d=0.10）；183 条需要 π_d ≤ 2.5%（即全批只有 4~5 条不一致对）→ 实务上不可能。**

---

## 1. (c) ScreenSpot-Pro 官方 eval 的抽样与统计口径：**没有统计口径**

### 1.1 官方评测脚本原文【一手】

来源：`likaixin2000/ScreenSpot-Pro-GUI-Grounding` → `eval_screenspot_pro.py`
（https://github.com/likaixin2000/ScreenSpot-Pro-GUI-Grounding/blob/main/eval_screenspot_pro.py ，
raw: https://raw.githubusercontent.com/likaixin2000/ScreenSpot-Pro-GUI-Grounding/main/eval_screenspot_pro.py ）

我逐行读过这个文件，与我们有关的原文事实：

```python
logging.basicConfig(level=logging.INFO)
torch.manual_seed(114514)          # ← 全局 seed，仅一次，仅对本地 torch 模型有效
...
def eval_sample_positive_gt(sample, response):
    bbox = [bbox[0]/img_size[0], bbox[1]/img_size[1], bbox[2]/img_size[0], bbox[3]/img_size[1]]
    click_point = response["point"]            # 模型给的 [0,1] 归一化点
    if (bbox[0] <= click_point[0] <= bbox[2]) and (bbox[1] <= click_point[1] <= bbox[3]):
        return "correct"
...
metrics = {
    "action_acc": correct_num / num_total,      # ← 只有点估计
    "text_acc":   text_correct / text_total,
    "icon_acc":   icon_correct / icon_total,
}
```

逐条结论：

1. **判定口径 = point-in-bbox，零随机性**（我们 `src/minicode/eval/formats.py` 已经逐字对齐，见 `EvalTarget.hit`）。
   → **判定这一层不可能成为噪声源**，任何噪声都来自模型调用。
2. **每个 task 只推理一次**（`model.ground_only_positive(...)` 单次调用），**没有重复采样、没有多 seed、没有 CI、没有显著性检验**。
3. **官方唯一报告的是点估计**（`action_acc` / `text_acc` / `icon_acc`），按
   `group` / `application` / `platform × application × instruction_style × gt_type` 分层切分
   （`leaderboard_simple_style` / `leaderboard_detailed_style` / `fine_grained`）。
   → **官方就是「分层点估计」，从不做检验、不做多重校正**。
4. **任务被笛卡尔展开**：`instruction_style(3) × gt_type(2) × language(2)`。
   → 若我们照抄这个展开，**同一 task 会重复出现最多 12 次**，`n` 就不是独立样本数 →
   CI/检验必须**按 task 聚类**（cluster bootstrap / 聚类稳健）。我们现在的分层抽样是「一 task 一行」，不存在这个问题。
5. 论文口径：**1581 instructions, each in a unique screenshot**（arXiv:2504.07981 摘要与 §1）
   → **每条样本独立**（无同图多条），NIST McNemar 的「配对间相互独立」假设成立【一手 + 推断】。

**官方报的 CI = 没有。**【一手】这一点直接回答了 (c)：我们不能「照抄官方口径」，只能自己立。

### 1.2 官方论文里的相关数字【一手】

来源：https://arxiv.org/abs/2504.07981 ｜正文 https://arxiv.org/html/2504.07981v1

* 1581 条，23 个应用、5 个行业、3 个操作系统；text 占 62.6%，icon 占 37.4%。
* 目标平均占截图面积 **0.07%**（ScreenSpot 是 2.01%）→ 解释 icon 为什么难。
* 「strategically reducing the search area enhances accuracy」→ OS-Atlas-7B **18.9%** → ScreenSeekeR **48.1%**（+254% 相对）。
* 质量控制：每条**至少 2 名标注者复核**，歧义指令在发布前被解决。
  → **这是「预注册子集」的合法先例**：子集在**看到任何模型结果之前**就定义好了（见 §5）。

---

## 2. (a) GUI grounding 评测里怎么控制模型随机性

### 2.1 官方做法 = temperature 0 + 固定 seed + 单次推理【一手】

`model_factory.py`（https://raw.githubusercontent.com/likaixin2000/ScreenSpot-Pro-GUI-Grounding/main/model_factory.py ）
最后一行，对**所有**模型（含 API 模型 GPT-4o/GPT-5/Gemini）统一执行：

```python
model.set_generation_config(temperature=0, max_new_tokens=4096)
```

配合 `eval_screenspot_pro.py` 里的 `torch.manual_seed(114514)`。

**这就是公开的、唯一的官方「控制随机性」做法**：贪心解码 + 固定 seed + 单次推理 + 不报方差。
**官方评测里没有「同一图多次推理取众数」这回事。**

### 2.2 我们的现状与差距【一手，本地】

```
grep -n "temperature|seed" src/  →  src/ 下 temperature 命中数 = 0
```
（命中的只有 `coord/calibrate.py` 的物理像素噪声 seed、`eval/__main__.py` 与
`ground/numbered_bench.py` 的**抽样** seed 20251020、`eval/sspro.py` 的抽样 seed。）

→ **我们的模型请求体没有钉 temperature**。`src/minicode/model/client.py:154 build_request_body(...)`
构造的是 Anthropic 兼容请求体（端点 `https://opencode.ai/zen/go/v1/messages`），
**未显式传 temperature ⇒ 走服务端缺省（常见缺省 1.0）**。

这几乎可以解释我们观察到的「同图同提示词连发两次 0/4 原文相同、跨运行命中翻转 16.7%~26.2%」：
**不是模型不可靠，是我们没关采样。**

**动作（1 行级）**：在 `build_request_body` 里加 `temperature=0`（官方同款），并在
`src/minicode/model/config.py` 里加一个可覆盖的 `temperature` 字段（缺省 0）。
**验收**：同图同提示词连发 10 次，落点/原文一致率应 ≥95%（若仍 <80%，说明网关忽略 temperature → 只能靠上游冻结，见 §2.4）。

### 2.3 「同一图多次推理取众数」在 grounding 上是**有害**的【二手】

ScreenSpot-Pro 论文正文含一处消融，原文大意是：**「a simple majority vote strategy resulted in a
performance drop to 46.8%」**（URL 可打开，我未逐字核到该段，故标【二手】）：
论文 PDF https://likaixin2000.github.io/papers/ScreenSpot_Pro.pdf ｜HTML https://arxiv.org/html/2504.07981v1

**机理**【推断】：票空间是连续坐标，不是离散标签；朴素多数投票要么退化成「同一答案重复 N 次」
（temperature=0 时恒等，无效），要么在不同元素上打平/随机破平 → 把本来答对的**坐标**投票投成错元素。
普通 self-consistency（Wang et al., arXiv:2203.11171）的前提是 **temperature>0 产生多样推理链**，
与 grounding 的坐标输出不对口。

→ **结论：不要把「多次采样取众数」当成提分手段。**
多次采样的**正确用途**是：(1) 估计每题的**成功概率**与全局不一致率 π_d（我们需要的正是这个）；
(2) 另立一个「**稳定性**」指标（同题 k 次落点一致率），这是官方没有、可作为我们自有指标的维度。

### 2.4 真正能把方差压到零的手段 = 上游冻结重放（我们仓库里已经有了）【一手，本地】

`src/minicode/eval/sspro.py` 的 `MemoizingModelCaller`（第 286~339 行）**已经实现**：
键 = `(prompt, 图片 sha256)`，同一次请求只问一次。该类的 docstring 里写着：

> 「模型端**不确定** —— 同一条提示词两次问，答案可能不同。工单 M8 的硬判据要求两臂
> **唯一差异是门控是否恒开**；若两臂各自重问一次粗定位，差异里就混进了模型的随机性。」
> 「端口之后两臂实际只差 **6/42 = 14.3 pp**」

**这就是诊断建议的「复用同一次粗定位回答 + 只换被测那一步」的落地形态**——而且仓库里已有雏形。
缺的是：**① 落盘（跨运行、跨轮次复用）；② 分阶段标记（粗定位 / 整屏第二遍 / 精定位），以便只重放某一段。**

### 2.5 Anthropic 官方方法论（可引用的「一手」依据）

来源：https://www.anthropic.com/research/demystifying-evals-for-ai-agents （原 /engineering/ 路径会 301 到这里）【一手】

原文可引用的三句：

1. 「Each attempt at a task is a **trial**. Because model outputs vary between runs, we run **multiple
   trials** to produce more consistent results.」（→ 多 trial 是标准做法；对应我们的 k 次采样，用来估 π_d。）
2. **pass@k / pass^k**：对同一题 k 次试验，pass@k = 至少一次成功（越高越好），pass^k = 全部成功（越低越稳）。
   → **对我们的意义**：bbox 命中口径对应 pass@1；「稳不稳」对应 pass^k。我们目前两个都没有报。
3. 「In reality, **20-50 simple tasks** … is a great start. After all, in early agent development,
   each change to the system often has a clear, noticeable impact, and **this large effect size means
   small sample sizes suffice**. More mature agents may need **larger, more difficult evals to detect
   smaller effects**.」（→ 直接支持我们的诊断结论：183 条 × 3.3 pp 属于「效应太小」阶段，样本量必须上去。）
4. Step 4：「It's essential that the agent in the eval functions roughly the same as the agent used in
   production, and **that the environment itself doesn't introduce further noise**. Each trial should be
   'isolated' … Unnecessary shared state between runs … can cause **correlated failures** … these trials
   are **not independent** … and the eval results become unreliable」（→ 支持「冻结上游 + 隔离 trial」+ 支持聚类/独立性的论证方式）。

**GUI grounding 专用、且带「多次运行 + 误差棒」要求的公开先例**【二手】：
NeurIPS 2025 的 GUI grounding 论文有专门一节「Multiple Runs and Statistical Consistency」，
并有 reviewer checklist 要求「error bar 是 SD 还是 SEM 必须写清楚」：
https://openreview.net/forum?id=1XLjrmKZ4p ｜
https://proceedings.neurips.cc/paper_files/paper/2025/file/b95c7e24501f5d1dddbc5e8526cda7ae-Paper-Conference.pdf
（我未逐字核到该节，标【二手】。）

---

## 3. (b) 配对检验 / 统计检验的公开用法

### 3.1 GUI grounding 里：**没有先例**【一手（反证）+ 二手（检索）】

官方 `eval_screenspot_pro.py` 里**没有任何统计检验代码**（§1.1 已逐行确认）；
检索「McNemar + GUI grounding」未找到任何把配对检验用于 grounding 评测的一手论文。
→ **可抄的先例不在 GUI grounding，在医学统计与 A/B 实验。**（这点要在报告里写清楚，别假装有同行做法。）

### 3.2 检验的选择与前提【一手】

NIST Dataplot McNemar 参考页（https://www.itl.nist.gov/div898/software/dataplot/refman1/auxillar/mcnemar.htm ）：

* 2×2 表：`a`=都对、`b`=旧对新错、`c`=旧错对新对、`d`=都错；**只有 b、c 携带信息**。
* 检验统计量：`b+c > 20` 用 `T1=(b−c)²/(b+c)`（χ²(1)）；`b+c ≤ 20` 用精确二项 `T2=b`（p=0.5）。
  连续性校正版 `T1'=(|b−c|−1)²/(b+c)`。
* 假设：配对间相互独立；二元结局；差异方向一致。

**我们的场景逐条对上**：同一批 ScreenSpot-Pro 样本、同一条预测 → 配对二元；
每条样本独立（1581 条各自一张不同截图）；差异方向一致（我们只关心新臂是否更好）。

### 3.3 用哪个 McNemar？—— mid-p，不要用普通 exact【一手（摘要原文）】

Fagerland MW, Lydersen S, Laake P. **「The McNemar test for binary matched-pairs data: mid-p and
asymptotic are better than exact conditional.」** BMC Med Res Methodol 2013;13:91.
DOI 10.1186/1471-2288-13-91 ｜摘要原文（Europe PMC REST，我实际取到）：
https://europepmc.org/articles/PMC3716987 ｜ https://doi.org/10.1186/1471-2288-13-91

摘要关键句（原文）：
> 「The mid-p test performs well compared with the asymptotic, asymptotic with continuity correction,
> and exact conditional tests … Even though the mid-p test does not guarantee preservation of the
> significance level, it did not violate the nominal level in any of the **9595 scenarios** considered …
> **The exact conditional test and the asymptotic test with continuity correction did not perform well
> for any of the considered scenarios.**」

→ **结论**：我们的小样本（m 只有几十）场景，**普通精确条件检验最保守、功效最低**；用 **mid-p**。
mid-p 两侧公式（可照抄）【推断】：

```
k = min(b, c);  m = b + c
p_midp = min(1, 2 · ( P(X ≤ k | m, 0.5) − 0.5 · P(X = k | m, 0.5) ) )
```

statsmodels 的 `mcnemar(table, exact=True, correction=True)` 只提供**普通 exact**（不是 mid-p）：
https://www.statsmodels.org/stable/generated/statsmodels.stats.contingency_tables.mcnemar.html 【一手】
→ **所以 mid-p 要自己写（3 行 `scipy.stats.binom`），或者用 `exact=False` 走 χ²+连续性校正当保守下界。**

### 3.4 单臂准确率的区间：Wilson【一手】

`statsmodels.stats.proportion.proportion_confint(count, nobs, method='wilson')`：
https://www.statsmodels.org/stable/generated/statsmodels.stats.proportion.proportion_confint.html 【一手】
方法可选 `normal / agresti_coull / beta / wilson / jeffreys / binom_test`；
文档引用 Brown, Cai & DasGupta (2001) *Interval Estimation for a Binomial Proportion*,
Statistical Science 16(2):101-133（DOI 10.1214/ss/1009213286）。

**自算【推断】**（p̂=44.81%）：
* n=183 → Wilson 95% CI ≈ **[37.7%, 52.0%]**（±7.2 pp）
* n=1581 → Wilson 95% CI ≈ **[42.4%, 47.3%]**（±2.45 pp）

→ **183 条上「44.81%」这个数，单看它就有 ±7.2 pp 的不确定度**；任何 3~5 pp 的差异单靠点数无法判定。
（我们观察到的「分半噪声 ±5~15 pp」与「两独立样本 n=183 的抽样噪声 MDE=14.6 pp」完全一致 —— 见 §4.2。）

### 3.5 Δ 的区间：配对 bootstrap

**做法**【推断】：按样本（有放回）重采样 N 次，每次重算 Δ=(c−b)/N，取 2.5%/97.5% 分位，2000 次重采样。
若采纳官方的多 instruction_style 展开，**必须按 task 聚类重采样**（§1.1 第 4 条）。
多重比较：`statsmodels.stats.multitest.multipletests(..., method='holm')` / `fdrcorrection`【一手（文档存在）】。

---

## 4. 把这套东西算成我们能照抄的数字

### 4.1 配对检验的功效公式【推断（自算，附算式）】

设 N 条样本、两臂配对，b=旧对新错、c=旧错对新对，m=b+c（不一致对数），Δ̂=(c−b)/N。
原假设下 b~Bin(m, 0.5) ⇒ Var(c−b)=m ⇒ **SE(Δ̂)=√m/N = √(π_d/N)**（π_d=m/N）。
双侧 α=0.05、power=1−β 的 MDE：

```
MDE = (z_{0.975} + z_{0.80}) · √m / N = 2.8016 · √m / N = 2.8016 · √(π_d/N)
需要 N ≈ 7.849 · π_d / Δ²
```

**MDE 表（配对，α=0.05 双侧，power 80%）**【自算】：

| N | π_d=0.05 | π_d=0.10 | π_d=0.20 | π_d=0.30 | 备注 |
|---|---|---|---|---|---|
| **183（现状）** | 4.63 pp | 6.55 pp | 9.26 pp | 11.34 pp | 端到端重跑时 π_d≈0.17~0.26 → **只能判定 ≥9~11 pp** |
| 330 | 3.23 pp | 4.56 pp | 6.45 pp | 7.90 pp | 我们自己估的 MDE@330≈4.84 pp ≈ 此表 π_d=0.10 行【自算一致】 |
| 937 | 1.83 pp | 2.59 pp | 3.66 pp | 4.48 pp | 我们自己估的 MDE@937≈2.87 pp ≈ 此表 π_d=0.10 行【自算一致】 |
| **1398（1581 去掉 183）** | 2.09 pp | 2.96 pp | 4.19 pp | 5.13 pp | 确认集（与调参集不重叠） |
| **1581（官方全集）** | 1.58 pp | **2.23 pp** | **3.15 pp** | 3.86 pp | **3.3 pp 可判定（π_d≤0.10）** |

**我们的现状对照**：背景给的「跨运行命中翻转 16.7%~26.2%」就是 π_d ≈ 0.17~0.26，
代进 N=183 → **MDE = 8.6~10.6 pp**。**所以「3.3 pp 不可判定」不是样本量不够大一点，是差了一个数量级。**

**净翻转数（power 80% 时需要的 |c−b|）≈ 2.8·√m**【自算】：
* m=158（N=1581, π_d=0.10）→ 需净翻转 **≥35 条**（=2.2 pp）
* m=37（N=183, π_d=0.20）→ 需净翻转 **≥17 条**（=9.3 pp）

**精确 McNemar 的最小净翻转（双侧 exact，α=0.05，我逐个手算）**【自算】：
m=6 → 净 6（p=0.031）；m=10 → 净 8（p=0.021）；m=12 → 净 8（p=0.039）；m=20 → 净 10（p=0.041）；m=40 → 净 14（p=0.039）。
即 **净翻转 ≈ 2·√m 就能到 p<0.05**（mid-p 会更松一点）。

### 4.2 我们的分半噪声到底是什么【推断（自算）+ 一手（本地报告）】

* 两臂**独立**（不配对）比较、n=183/臂、p≈0.45：
  `SE(Δ)=√(2p(1−p)/n)=5.20 pp` → **MDE(80%) = 14.6 pp**。
  → 观测到的「分半 ±5~15 pp」**完全落在二项抽样噪声的预期范围内**，不必归因于「模型随机」。
* 本仓 M23 留出集实测【一手】：B 半 21 条里「两臂粗定位点不同 3 条（14.3 pp）」、「命中翻转 0 条」。
  → 说明**端到端重跑时的不一致主要发生在「粗定位点」这一层**，而「命中翻转」是它的下游 → **冻结粗定位回答就能砍掉大部分不一致对**。
* 反向验证：配对 + π_d=0.10 时 MDE=6.55 pp，仍远大于 3.3 pp → **还必须把 N 提上去**。两者是乘法关系，缺一不可。

### 4.3 官方口径下「一次评测」的成本量级【推断】

我们 `run_arms` 是 3 臂 ×（整屏粗定位 + crop 精定位）≈ 每样本 4~6 次模型调用；
冻结上游后只重放被测那一步 ≈ 每样本 1~2 次。
→ 全量 1581 条：完全冻结 ≈ 1,600~3,200 次调用；不冻结 ≈ 6,000~9,500 次调用。
（具体单价不引，属另一条线的成本调研。）

---

## 5. (d) 「在困难子集上放大效应」：有先例、有条件、有坑

### 5.1 先例

1. **官方的分层报告**【一手】：`eval_screenspot_pro.py` 按 group / application /
   platform×application×instruction_style×gt_type 切分并逐层报点估计 —— 说明「按层报」是这个基准的常规做法。
2. **官方发布前剔除歧义样本**【一手】：论文 §3.3「每条至少 2 名标注者复核，歧义指令在发布前解决」。
   → **子集在看到模型结果之前定义**，这是唯一安全的「子集」用法。
3. **Anthropic 的 capability vs regression eval 分工**【一手】：能力型 eval「从低通过率起步，专挑模型挣扎的任务」；
   回归型 eval「必须接近 100% 通过率」。
   → 与我们的「困难子集放大」思路一致，但 Anthropic 把它做成**两套独立 eval**，而不是在同一套数据上事后切片。

### 5.2 什么时候「按基线错误切片」是合法的【推断，结构依据一手】

结构判据（Hernán MA, Hernández-Díaz S, Robins JM. 「A structural approach to selection bias.」
Epidemiology 2004;15(5):615-625，DOI 10.1097/01.ede.0000135174.63482.43；
摘要原文我已从 Europe PMC 取到：https://europepmc.org/article/MED/15308962 ）：

> 摘要原句：「conditioning on a **common effect** of 2 variables …」——
> 条件在**共同结果**（collider）上会造出虚假关联（selection bias）。

**对我们的具体结论**：
* 子集「粗定位已错」必须由**冻结的基线产物**定义（我们已有：`MemoizingModelCaller` 缓存的整屏粗点 + 官方口径判定），
  而不是由「被测步骤的结果」定义，也不是「跑完两臂后看谁错得多再挑」。
* 被测步骤必须**严格位于**子集定义之后（下游）。
  → 我们的被测步（crop 窗口/密度、精定位回落判据）都在**整屏粗定位之后** ⇒ **条件化在先于处理的变量上 → 不构成 collider，条件效应合法**。
* 一旦被测步会**反过来改变粗定位结果**（例如改整屏缩放策略），则「粗定位已错」是**处理后变量** ⇒
  该子集分析**只能作探索性**，不能作主结论。

### 5.3 必须同时报告的分解（不能只报子集效应）【推断】

```
总体 Δ = Δ_S · |S|/N + Δ_Sc · |Sc|/N
```
我们 S=107、N=183：若 Δ_S=+11.2 pp、S 外 Δ_Sc=0 ⇒ **总体只有 +6.55 pp**；
若 S 外是负的，总量还会更小。**报告里必须写清「子集效应」与「总体效应」两个数。**

### 5.4 坑：事后切片 = winner's curse【一手（摘要原文）】

Ioannidis JPA. 「Why most discovered true associations are inflated.」 Epidemiology 2008;19(5):640-648.
DOI 10.1097/ede.0b013e31818131e7 ｜摘要原文（Europe PMC 已取）：https://europepmc.org/article/MED/18633328

摘要原句：「when true discovery is claimed based on crossing a **threshold of statistical significance**
and the discovery study is **underpowered**, the observed effects are expected to be **inflated** …」
以及「**flexible analyses coupled with selective reporting** may inflate the published discovered effects」。

→ **我们的「粗定位已错」子集恰好是 underpowered 场景下的事后切片 → 效应会被高估。**
**防线（三条一起）**：
1. **预注册**：在跑之前把「主检验 = 全量 N 的配对 mid-p McNemar；子集 S = 冻结基线粗定位已错（n=…）；
   其余分层为探索性并做 Holm 校正」写进工单（我们仓库用 `docs/tickets/*.md` 作唯一真源，见 ADR 0003）。
2. **确认集与调参集不重叠**：183 条用来选参数，**1398 条**（1581−183）做确认检验 → 避免「用同一批数据选方案又验方案」。
3. **只报一个主检验**：其它分层一律标「探索性」，并对家族做 Holm 校正。

---

## 6. 结论：协议 v1（照抄版）

**主检验**
* 数据：ScreenSpot-Pro **全量 1581 条**（官方全集，unique screenshot，样本独立）。
* 两臂：**配对** —— 同一批样本、同一张整屏图、**同一份冻结的上游回答**（粗定位点 / 整屏第二遍 / crop 框），
  只替换**被测的那一步**（如 crop 窗口策略 / 回落判据）。
* 判定：官方 point-in-bbox（我们 `formats.EvalTarget.hit` 已逐字对齐，零噪声）。
* 统计量：b、c、m、Δ=(c−b)/N；**mid-p McNemar（双侧，α=0.05）**为主报；
  普通精确二项与 χ²+连续性校正作对照；Δ 的 95% CI 用**配对 bootstrap（2000 次，按 task 聚类）**。
* 单臂区间：**Wilson 95% CI**（`proportion_confint(method='wilson')`）。
* 功效：跑之前用 `N ≈ 7.849·π_d/Δ²` 预估；**π_d 由上一轮实测的不一致对占比给出**。

**随机性控制**
* `temperature=0`（官方同款，`build_request_body`）+ 抽样 seed 固定（已有 20251020）+ 模型版本串落进报告。
* 上游冻结重放（`MemoizingModelCaller` **落盘化** + 阶段标签）。
* **不要**用多数投票提分（官方消融掉到 46.8%【二手】）；多次采样只用来估 π_d 与「稳定性」。

**困难子集**
* 主结论 = 全量配对检验；子集 S（冻结基线粗定位已错）为**预注册的次要分析**，报告总体分解式；
* 其余分层 = 探索性 + Holm 校正。

---

## 7. 不适用 / 无法核实 / 需要我们自己立的

| 事项 | 判定 | 原因 |
|---|---|---|
| 「照抄官方 CI 口径」 | **不适用** | 官方不报 CI、不做检验（`eval_screenspot_pro.py` 无相关代码）【一手】 |
| 「多数投票 / self-consistency 提分」 | **不适用（有害）** | 官方消融：多数投票掉到 46.8%【二手】；票空间是连续坐标 |
| 「固定 seed 就能让 API 输出确定」 | **不适用** | 官方 seed 只作用于本地 torch 模型；我们是 API 通道 → 只能靠 temperature=0 + 冻结重放【推断】 |
| 「pass@k / pass^k 直接搬到 grounding」 | **需改造** | 我们的口径是 bbox 命中（=pass@1）；pass^k 要先定义「k 次是否落同一元素」，属新指标 |
| 「官方 1581 条 CI」 | **无此数字** | 官方只有点估计；任何 CI 都是我们自己算的，报告里必须注明「非官方口径」 |
| 「paired bootstrap 在 GUI grounding 的先例」 | **无先例** | 检索未发现；先例在医学统计/A-B 实验（Fagerland、NIST、Anthropic） |
| 「困难子集放大的 GUI 先例」 | **只有预注册式先例** | 官方发布前剔除歧义样本（先于结果定义）= 合法；**事后切片无先例且有 winner's curse 风险** |
| Fagerland 2013 样本量论文（BMJ Open e003816） | **未能核实** | 站点对自动抓取返回 403（用户浏览器可打开）；我用的是自算公式 §4.1 + Fagerland 2013 mid-p 论文【一手摘要】 |

---

## 8. ≤8 条最小改动清单（按投入产出排序）

| # | 改哪个文件 | 做什么 | 预期哪个数变多少 |
|---|---|---|---|
| 1 | `src/minicode/model/client.py:154 build_request_body`（+ `src/minicode/model/config.py` 加 `temperature` 字段） | 请求体显式带 `temperature=0`（照抄官方 `model_factory.py` 的 `set_generation_config(temperature=0, max_new_tokens=4096)`） | 跨运行落点一致率 ↑；π_d 从 16.7~26.2% 预计降到 ~5~12%；183 条 MDE 从 9~11 pp → 6.5~8.2 pp |
| 2 | `src/minicode/eval/sspro.py`（`MemoizingModelCaller`） | 记忆缓存**落盘**（`runs/<x>/memo-cache.json`）+ 每条回答打**阶段标签**（coarse / whole2nd / refine），使「只重放被测那一步」跨运行可复现 | 不一致对 m 再降 ~1/2；183 条 MDE 再 ÷√2（6.55→4.6 pp） |
| 3 | 新建 `src/minicode/eval/stats.py` | 实现 `mcnemar_midp(b,c)`、`wilson_ci(k,n)`、`paired_bootstrap_delta(...)`、`mde(n, pi_d, delta)`（纯函数 + 单测） | 报告从「两个百分比」变成「Δ=+X.XX pp，95% CI [a,b]，mid-p McNemar p=…，m=…」 |
| 4 | `src/minicode/eval/__main__.py` | 新增 `--full`（跑全量 1581）与 `--confirmation-split`（183 调参 / 1398 确认，不重叠）；`sspro-report.json` 落 b/c/m/Δ/p/CI | **主检验 MDE 从 6.55 pp（n=183）→ 2.23 pp（n=1581, π_d=0.10）**；3.3 pp 从「不可判定」变「可判定」 |
| 5 | `src/minicode/eval/holdout.py` | 现有 A/B 切分升级为「A 调参 / B 确认」+ 配对检验；B 半禁止调参（已有），并输出 McNemar 表 | 现在的「+0.0 pp vs 噪声 14.3 pp」这类结论变成带 p 值的可判定结论 |
| 6 | `src/minicode/eval/__main__.py`（`sspro-table.md` 生成处） | 每行加：单臂 Wilson 95% CI、配对 Δ、配对 95% CI、mid-p p、m | 183 条单臂从「无区间」→「±7.2 pp」；1581 条「±2.45 pp」 |
| 7 | 新建 `docs/tickets/M36-paired-eval-protocol.md` | 预注册：主检验（全量配对 mid-p McNemar，α=0.05 双侧）、预注册子集定义（冻结基线粗定位已错）、探索性分层清单 + Holm 校正、功效计算 | 把「事后挑显著子集」变成合规设计，避免 winner's curse（Ioannidis 2008） |
| 8 | `src/minicode/ground/numbered_bench.py` | 复用同一个 memo 缓存与 `stats` 模块，编号基准也走配对口径 | 两个评测台的 Δ 可直接对比；否则编号基准与门控评测的数字不同源、不可合并 |

**验收判据（可写成工单 AC）**：
1. `build_request_body` 含 `temperature=0`，同图同提示词 10 次落点一致率 ≥95%（【推断】验收门槛，需实测校准）。
2. `stats.py` 的 `mcnemar_midp` 与 `scipy.stats.binomtest` 的普通 exact 在手算样例上对齐（差值 <0.01）。
3. 在 183 条上跑一次**配对**对比，报告里的 m 应 ≈ 上一轮 π_d×183（若 m 仍 >30，说明冻结没生效）。
4. 全量 1581 条的主检验能给出「Δ + 95% CI + p」，且 **CI 不跨 0** 才写「改进成立」。

---

## 9. 引用清单（全部可打开）

**官方 ScreenSpot-Pro**
- 评测脚本：https://github.com/likaixin2000/ScreenSpot-Pro-GUI-Grounding/blob/main/eval_screenspot_pro.py ｜raw：https://raw.githubusercontent.com/likaixin2000/ScreenSpot-Pro-GUI-Grounding/main/eval_screenspot_pro.py
- 模型工厂（temperature=0）：https://raw.githubusercontent.com/likaixin2000/ScreenSpot-Pro-GUI-Grounding/main/model_factory.py
- 论文：https://arxiv.org/abs/2504.07981 ｜HTML：https://arxiv.org/html/2504.07981v1 ｜PDF：https://likaixin2000.github.io/papers/ScreenSpot_Pro.pdf
- 排行榜：https://gui-agent.github.io/grounding-leaderboard/

**统计方法**
- NIST Dataplot McNemar：https://www.itl.nist.gov/div898/software/dataplot/refman1/auxillar/mcnemar.htm
- Fagerland 2013 mid-p（摘要原文已取）：https://europepmc.org/articles/PMC3716987 ｜https://doi.org/10.1186/1471-2288-13-91
- statsmodels McNemar：https://www.statsmodels.org/stable/generated/statsmodels.stats.contingency_tables.mcnemar.html
- statsmodels 比例区间（Wilson 等）：https://www.statsmodels.org/stable/generated/statsmodels.stats.proportion.proportion_confint.html
- Hernán 2004 选择偏差（摘要原文已取）：https://europepmc.org/article/MED/15308962 ｜https://doi.org/10.1097/01.ede.0000135174.63482.43
- Ioannidis 2008 winner's curse（摘要原文已取）：https://europepmc.org/article/MED/18633328 ｜https://doi.org/10.1097/ede.0b013e31818131e7
- Brown/Cai/DasGupta 2001：https://doi.org/10.1214/ss/1009213286

**eval 方法论 / GUI grounding 先例**
- Anthropic《Demystifying evals for AI agents》：https://www.anthropic.com/research/demystifying-evals-for-ai-agents
- RegionFocus（视觉 test-time scaling，Qwen2.5-VL-72B 61.6%）：https://arxiv.org/abs/2505.00684 ｜https://github.com/tiangeluo/RegionFocus
- GUI grounding 多次运行 + 误差棒（【二手】）：https://openreview.net/forum?id=1XLjrmKZ4p
- self-consistency（前提 temperature>0）：https://arxiv.org/abs/2203.11171

# S7 评测线：ScreenSpot-Pro 全量数据、官方统计口径、抽样偏差、CI 与分组报告

> 调研日期 2026-10-22。本机实测数字均为只读复算（脚本未落盘、未改动仓库任何文件）。
> 证据强度标注：**【一手】**=官方源码/论文/榜单原文；**【二手】**=第三方镜像或检索摘要转述；**【推断】**=本报告自建的推导或模拟。

---

## 0. 一句话结论

**我们不缺真值，缺的是 644 张 PNG。** 本地 `annotations/` 里 **1581 条标注是完整的**，且与官方论文 Table 1 的逐 app 图标/文本计数 **26/26 完全吻合**；官方口径是 micro-average 且**完全不报 SE/CI**；而我们那 183 条的实际抽样框只有 937 条（缺图的 10 个 app 零覆盖），Linux 被全量普查（占样本权重 27.3% vs 全量 3.2%），**无权重均值对框真值有 ±3.4pp 量级偏差**。补齐 644 张图（≈1.3 GB、一次下载）能把 CI 半宽从 **7.12pp 压到 2.45pp**、把最小可分辨配对效应从 **10.61pp 压到 3.61pp**，并让 CAD/Dev/Office 三组从"不可报"变成"可报"。

---

## 1. (a) 全量数据怎么拿；缺的那 644 张是什么

### 1.1 官方一手来源

| 资源 | URL | 状态 |
|---|---|---|
| 论文（arXiv HTML 全文，Table 1 有逐 app Icons/Texts） | https://arxiv.org/html/2504.07981v1 | ✅ 已读全文 |
| 官方 leaderboard（含同一张 app 表 + "Micro-average numbers are reported"） | https://gui-agent.github.io/grounding-leaderboard/ | ✅ 已读 |
| 官方评测脚本源码 | https://raw.githubusercontent.com/likaixin2000/ScreenSpot-Pro-GUI-Grounding/main/eval_screenspot_pro.py | ✅ 已读全文 |
| 官方代码仓 | https://github.com/likaixin2000/ScreenSpot-Pro-GUI-Grounding | ✅ 已读文件树 |
| 官方数据集（HF） | https://huggingface.co/datasets/likaixin/ScreenSpot-Pro | ⚠️ 本环境直连超时，改用搜索摘要【二手】 |
| parquet 镜像 1,581 条 | https://huggingface.co/datasets/lmms-lab/ScreenSpot-Pro | 【二手】 |
| FiftyOne 镜像（3.36 GB） | https://huggingface.co/datasets/Voxel51/ScreenSpot-Pro | 【二手】 |
| 官方 PDF | https://likaixin2000.github.io/papers/ScreenSpot_Pro.pdf | ⚠️ 抓取工具不支持 PDF |

### 1.2 文件结构与 license

- **布局**：`images/<app_dir>/<screenshot>.png` + `annotations/<app>_<platform>.json`（26 个 json，每个是一个 list，字段 `img_filename / bbox / instruction / instruction_cn / id / application / platform / img_size / ui_type / group`）。**我们本地就是这个布局，26 个 json 全在。**【一手：本地实测 + 论文/榜单表结构一致】
- **HF 数据集卡**："Data viewer is not configured because the large size of the images will trigger loading errors" → 必须用 `hf download` / `snapshot_download`，不能用网页预览。【二手】
- **license**：GitHub 仓有 `LICENSE` 文件（文件树实测 1066 bytes = MIT 全文长度）；arXiv 论文页标 **CC BY 4.0**；**HF 数据集卡本身的 license 字段未明确**（Voxel51 镜像卡写 "License: [More Information Needed]"）。数据集卡列明"out-of-scope use"包含用截图做训练集、再分发。【一手：论文页 CC BY 4.0 标注 + 文件树；【二手】HF 卡 license 字段】
  → 结论：**本机自用评测无碍；不要把 644 张图重新分发/上传到公开仓库。** 我们 `runs/g2-eval-final/sspro-report.json` 里已写 "MIT（官方仓 LICENSE + 数据集卡片 cardData.license）"，建议补一句"数据集图片未声明独立 license，仅限评测用途"。

### 1.3 缺的 644 张到底是什么（本机实测）

**不是"某个平台缺失"，是 10 个 app 的 PNG 没下载全。** Linux 与整个 OS 组是完整的。

| app | group | 官方条数 | 本地有图 | 缺 |
|---|---|---|---|---|
| word_macos | Office | 84 | 0 | 84 |
| pycharm_macos | Dev | 78 | 0 | 78 |
| vivado_windows | CAD | 80 | 0 | 80 |
| solidworks_windows | CAD | 77 | 0 | 77 |
| premiere_windows | Creative | 52 | 0 | 52 |
| vscode_macos | Dev | 55 | 0 | 55 |
| stata_windows | Scientific | 49 | 0 | 49 |
| quartus_windows | Dev | 45 | 0 | 45 |
| vmware_macos | Dev | 41 | 0 | 41 |
| unreal_engine_windows | Creative | 35 | 0 | 35 |
| powerpoint_windows | Office | 82 | 34 | 48 |
| **合计** | | **1581** | **937** | **644** |

按组缺失：Dev 219、CAD 157、Office 132、Creative 87、Scientific 49、**OS 0**。
（用户给的是"缺 648"；本机按"annotations 引用的图"逐条 stat 得到 **644 缺 / 937 在**，磁盘上实际有 939 个 png（2 个孤儿文件）。差异 4 张属计数口径差，结论不变。）

### 1.4 我们的标注是真品（决定性交叉验证）

**官方论文 Table 1 / leaderboard 的逐 app Icons×Texts 与我们本地 annotations 逐条完全一致（26/26）**：VSC 22/33=55、PyC 38/40=78、AS 44/36=80、Qrs 32/13=45、VM 9/32=41、PS 25/26=51、PR 24/28=52、AI 19/12=31、Bl 15/56=71、FL 31/26=57、UE 6/29=35、DR 23/21=44、CAD 7/27=34、SW 14/63=77、Inv 11/59=70、Vvd 32/48=80、MAT 19/74=93、Org 43/19=62、Stt 41/8=49、Evw 7/43=50、Wrd 15/69=84、PPT 25/57=82、Exc 13/51=64、Win 47/34=81、mac 23/42=65、Lnx 19/31=50。逐组合计 Dev 299 / Creative 341 / CAD 261 / Scientific 254 / Office 230 / OS 196 = 1581。【一手：论文 Table 1 + leaderboard 表 vs 本机实测】

**官方 leaderboard 的逐 app 分数分母也与我们的 N 吻合**：autocad 29.4%=10/34、illustrator 25.8%=8/31、vscode 43.6%=24/55、macos_common 55.4%=36/65、windows_common 29.6%=24/81。【一手】

→ **1581 条标注可直接当官方真值用，唯一缺的是图片文件。** 顺带发现论文自身两处不一致：正文说 "23 applications"、"five industries"，Table 1 实际是 26 个 app / 6 类；正文说 "text constitute 62.6%"，但按 Table 1 求和是 977/1581 = **61.8%**（icon 38.2%）——**谁要按 62.6% 做分层权重会算错**。

### 1.5 补齐命令

本机已验证可用的下载通道是 **hf-mirror**（`runs/g2-eval-final/sspro-report.json` 的 `dataset.source` 写着"本次经 hf-mirror 镜像下载"）：

```powershell
$env:HF_ENDPOINT='https://hf-mirror.com'
hf download likaixin/ScreenSpot-Pro --repo-type dataset --local-dir .scratch/sspro --include "images/*"
```

- 体积：官方全量 ≈3.3 GB / 1581 张 → 缺的部分 **≈1.3–1.4 GB**【二手：按公开总量折算】。本机 C: 盘空闲 **57.3 GB**（实测），够。
- 落地后必须核对（一条命令的事）：逐条 `annotations/*.json` 的 `img_filename` 是否都能在 `images/` 下 stat 到。

---

## 2. (b) 官方 eval 脚本的统计口径

**源码级逐条读出**（一手：`eval_screenspot_pro.py` 原文）：

1. **判定**：`eval_sample_positive_gt` → `bbox` 按 `img_size` 归一化到 [0,1]，预测点落在框内 = `correct`；点为 `None` = **`wrong_format`（算错，仍进分母）**。
2. **主指标**：`calc_metric_for_result_list` → `action_acc = correct/num_total`（**micro-average**）；`text_acc` / `icon_acc` 分别在 `ui_type` 子集上算（**子集均值，不是按总体权重加权**）。
3. **四套切分**：
   - `fine_grained`：`plat:{platform} app:{application} inst_style:{...} gt_type:{...}`
   - `seeclick_style`：`plat:{platform} inst_style:... gt_type:...`
   - `leaderboard_detailed_style`：`app:{application}`
   - `leaderboard_simple_style`：**`group:{group}`**（= Dev / Creative / CAD / Scientific / Office / OS 六类）
4. **没有任何 SE / CI / 样本量门槛 / 显著性检验**。leaderboard 页面原文：**"Micro-average numbers are reported."**、"Results are collected using greedy decoding."【一手】
5. 官方跑法是 `instruction_style`(instruction/action/description) × `gt_type`(positive/negative) × `language`(en/cn) 的**笛卡尔积**，同一样本被跑多次；我们只用了 instruction+positive+en。

**能不能直接拿官方脚本跑我们的模型？→ 不能直接，但口径能 1:1 复刻。**

- 不能的原因有二：① 官方脚本 `from model_factory import build_model`，要求 7B 权重 + vLLM 服务（本机 6 GB 显存装不下，本仓 M8 已记录）；② 它要求模型返回**归一化点**（`response["point"]`），我们的模型返回像素点 → 需要薄适配层。
- **能复刻的部分我们已经有了**：`src/minicode/eval/formats.py` 的 `SCREENSPOT_PRO` 口径（归一化 [0,1]）与官方 `eval_sample_positive_gt` 一致；`src/minicode/eval/test_eval_formats.py` 已把官方脚本 + ZoomClick 副本 + ShowUI 三个判定函数钉进单测。
- **建议**：不把官方脚本当运行时依赖（架构约束：本仓核心链路只依赖标准库）；但把官方的**切分键名**（`app:` / `group:` / `plat:`）与**每层样本量 n** 打进我们的 `sspro-report.json`，让我们的行能与 leaderboard 逐行对齐；并**补上官方没有的 SE/CI**（§3）。

---

## 3. (c) 样本量不足时，公开的 GUI grounding 评测怎么做

- **区间方法**：**Wilson score 是默认推荐**（Brown, Cai & DasGupta 2001：小样本推荐 Wilson/Jeffreys、大样本推荐 Wilson/Jeffreys/Agresti–Coull；Agresti & Coull 1998 也推荐 Wilson）；**Clopper–Pearson 更保守**，n 足够大时与 Wilson 差别很小（"little practical difference"），要"保证不低于名义覆盖"才用它；**Wald 不要用**（即使 n 大也因覆盖振荡被明确不推荐）。【一手：Brown–Cai–DasGupta 2001 的结论经多篇独立来源一致转述】
  → 我们已有 `wilson_ci()`（`src/minicode/eval/stats.py:168`），**不需要新写**。
- **分层估计 + 有限总体修正**（Cochran 1977, p.108）：
  `p̂_st = Σ_h W_h p_h`；`V = Σ_h W_h² (1−f_h) p_h q_h /(n_h−1)`，`W_h = N_h/N`、`f_h = n_h/N_h`；`CI = p̂_st ± 1.96 √V`。至少每层 2 个样本才能估层内方差。
- **公开 CUA/grounding 评测的具体做法**：
  - arXiv:2607.17136（"Teach it to stop, not just to click"，CUA 可靠性方法学）：跨 **data-draw × seed** 网格做方差分解 + **bootstrap CI** + **Fisher exact**；给出 grounding 空间点击成功率 **0.53 ± 0.35**（5 seed），最难 cell 上 run-to-run 分布**双峰**，并明确指出"单次跑出来的常见量级改进**约 1/3 概率符号搞反**"。【一手：论文摘要与检索摘要，PDF 未直接读取】
  - arXiv:2606.25760（CUAgent UQ benchmark）Appendix A29：**per-cell top-1 配 bootstrap CI**（500 resamples × 50 seed），95% CI 标在每个 cell 旁；强调要按 dataset×agent×ui_type 分层重抽。
  - MLflow《How to benchmark AI agents》：显式提交 seed 列表、标注 "Runs per config"，并给出 "Full sweep, single run … not recommended for publication; 3 seeds = minimum"。
- **给我们的报告格式**（沿用公开做法，不自创）：① 主指标点估计 + Wilson 95%；② 按 platform×ui_type 与按 6 大 group **各报一份**，每层带 n 与区间；③ 两臂比较用**配对** McNemar mid-p + 配对 bootstrap 区间（仓库已有 `mcnemar_midp` / `paired_bootstrap_delta`）；④ 跨运行比较附 MDE（仓库已有 `mde()`）；⑤ **CI 标在每个 cell 旁边**，不只报一个总体。

---

## 4. (d) "183 条抽样估总体" vs "全量 1581" 的偏差

### 4.1 先说抽样框：183 条只覆盖 16 个 app（本机实测）

`runs/g2-eval-final/sspro-report.json` 的 183 条 outcomes，app 分布 = 本地**有图**的 16 个 app；**缺图的 10 个 app 一条没抽到**（word_macos / pycharm_macos / vscode_macos / vmware_macos / quartus_windows / solidworks_windows / vivado_windows / stata_windows / premiere_windows / unreal_engine_windows 全部为 0）。

各 (platform × ui_type) 层的抽样比：

| 层 | n_h | 框 937 的 N_h | 抽样比 f_h | 样本权重 | 全量 1581 的 W_h |
|---|---|---|---|---|---|
| linux/icon | 19 | 19 | **1.000（普查）** | 10.4% | 1.20% |
| linux/text | 31 | 31 | **1.000（普查）** | 16.9% | 1.96% |
| macos/icon | 34 | 122 | 0.279 | 18.6% | 13.02% |
| macos/text | 30 | 224 | 0.134 | 16.4% | 25.17% |
| windows/icon | 34 | 211 | 0.161 | 18.6% | 23.97% |
| windows/text | 35 | 330 | 0.106 | 19.1% | 34.66% |

**后果：Linux 占样本权重 27.3%，占框（937）5.3%、占全量（1581）3.2% → 过度加权 8.6×。**
组权重更失衡（样本 vs 全量）：CAD **4.9% vs 16.5%（抽稀 3.4×）**、Dev 8.7% vs 18.9%、Office 8.7% vs 14.5%、OS **41.5% vs 12.4%（超配 3.3×）**。

> **附带发现（复现性缺口）**：仓库里的 `src/minicode/eval/sspro.py::stratified_subset` **复现不出这批 183**（k∈{30,32,34,35,36,40} × 框∈{937,1581} 全部不匹配，最好重叠 133/190）。代码注释里"固定 seed → 同一批样本"的承诺目前**不成立**。
> 好消息：`g2-eval-final` / `g3-eval-2016` / `g1-eval-330` 三份报告的 `sample_id` 集合**完全相同**（本机集合相等验证），所以 1536 vs 2016 的**配对比较本身有效**。

### 4.2 蒙特卡洛：用官方 leaderboard 已公开的逐 app 分数当真值【推断·本报告自建】

方法：取 leaderboard 上两个模型公布的**逐 app 分数**（UI-AGILE-3B 总 45.0、GTA1-7B 总 55.5）当作每个 app 的真实命中率，按我们的抽样设计（各层 n_h、层内 SRSWOR）模拟 4000 次，比较两个估计量：E1 = 无权重均值（现在的报法）、E2 = 后分层均值（Cochran 权重）。

| 模型 | 全量 1581 真值 | 框 937 真值 | E1 偏差(vs 937) | E1 SD | P(\|E1−真值\|>5pp) | E1 偏差(vs 1581) | E2 偏差(vs 937) | E2 偏差(vs 1581) |
|---|---|---|---|---|---|---|---|---|
| UI-AGILE-3B | 44.87% | 43.81% | **−0.14pp** | 3.62pp | 17.2% | −1.19pp | +0.04pp | −1.02pp |
| GTA1-7B | 57.04% | 54.32% | **+3.37pp** | 3.58pp | **34.3%** | +0.65pp | +0.07pp | −2.64pp |

读法（三条，都直接可执行）：

1. **无权重均值的偏差方向不确定、量级 ±3pp**（取决于模型在各 app 上的强弱分布）；后分层把"对框的偏差"压到 ≈0（+0.04 / +0.07pp），但**对全量 1581 仍差 −1.0 ~ −2.6pp** —— 这是"框"本身的偏差，**只有补齐 644 张图才能消**。
2. **5pp 的差距在本设计下有 17~34% 概率是抽样随机性造出来的** → 5pp 以内的结论不可信。
3. 我们自己的数：183 条无权重均值 **55.74%**，Wilson 95% = **[48.50%, 62.74%]**（半宽 7.12pp，本机复算复核，与用户给的 CI 一致）。改成后分层（框 937 权重）后点估计 **58.53%**，设计基 SE **3.78pp**，95% CI **[51.12%, 65.95%]**。

### 4.3 跨档比较才是要命的一条（我们自己的两次跑批）

- g2(1536) `always` 102/183 vs g3(2016) `always` 118/183，**同一批 183 个 sample_id**。
- 不一致对：**b=32**（只有 2016 对）、**c=16**（只有 1536 对），共 **48** 条不一致。
- McNemar **mid-p = 0.0213**（exact = 0.0293）；Δ = **+8.74pp**；SE(Δ) = √48/183 = **3.79pp** → 95% CI ≈ **+8.74 ± 7.42pp**。
- 仓库 `mde()`：**MDE(183, discordance=48/183) = 10.61pp**；**MDE(1581, 同不一致率) = 3.61pp**。
  → **这 8.74pp 在 183 条上只有 ~2.3σ，且低于 80% 功效的 MDE**：现在只能说"方向一致、幅度未达可分辨线"。
  → 补到全量 1581 后，同一个效应量 SE = **1.29pp**（≈6.8σ，p<1e-10）——**这就是"多花 1.3 GB 换多少确定性"的定量答案**。
- 复核：同一次跑批内的 `always vs coarse_only` 我们本地也复算过 —— g2 b=38/c=11 mid-p=**9.021e-05**、g3 b=48/c=6 mid-p=**1.823e-09**，与用户给的两个 p 值**完全一致**。

### 4.4 全量 1581 的精度账（"花多少换多少点"）

| 指标 | 现在（183） | 全量（1581） |
|---|---|---|
| 总体 Wilson 95% 半宽 | 7.12pp | **2.45pp**（收紧 2.9×） |
| icon 子集 n / 半宽 | 87 / 10.1pp | **604 / 4.0pp** |
| CAD 组 n / 半宽 | 9 / 26.3pp | **261 / 6.0pp** |
| Dev 组 n / 半宽 | 16 / 21.9pp | **299 / 5.6pp** |
| Office 组 n / 半宽 | 16 / 19.7pp | **230 / 6.4pp** |
| Creative 组 n / 半宽 | 36 / 15.5pp | **341 / 5.3pp** |
| Scientific 组 n / 半宽 | 30 / 16.3pp | **254 / 6.1pp** |
| OS 组 n / 半宽 | 76 / 10.9pp | **196 / 6.9pp** |
| 配对差 MDE | 10.61pp | **3.61pp** |

---

## 5. (e) 要不要把 CAD / OS / Dev / Office / Creative / Scientific 逐组报真值

**要。而且现在必须逐组报——因为 CAD 组现在只有 9 条，而 CAD 恰好是权重最大的组之一（16.5%），我们的总体点估计被它严重欠代表。**

- 官方脚本**本来就有 group 维度**（`evaluate_leaderboard_simple_style` → 键 `group:{group}`，六类）；我们本地 annotations 也带 `group` 字段 → **逐组真值零成本**。【一手】
- 当前 183 条的逐组（`always` 臂，本机实测）：

| group | 全量 N | 183 内 n | 命中率 | Wilson 95% | 半宽 | 样本权重 vs 全量权重 |
|---|---|---|---|---|---|---|
| CAD | 261 | **9** | 33.33% | [12.1%, 64.6%] | 26.3pp | 4.9% vs 16.5% |
| Creative | 341 | 36 | 47.22% | [32.0%, 63.0%] | 15.5pp | 19.7% vs 21.6% |
| Dev | 299 | **16** | 56.25% | [33.2%, 76.9%] | 21.9pp | 8.7% vs 18.9% |
| Office | 230 | **16** | 75.00% | [50.5%, 89.8%] | 19.7pp | 8.7% vs 14.5% |
| Scientific | 254 | 30 | 63.33% | [45.5%, 78.1%] | 16.3pp | 16.4% vs 16.1% |
| OS | 196 | 76 | 55.26% | [44.1%, 65.9%] | 10.9pp | 41.5% vs 12.4% |

→ CAD / Dev / Office 三组的区间宽到 **19~26pp**，组级结论**完全不可用**（连"CAD 是否比 OS 差"都读不出来，两个区间几乎完全重叠）。
→ 同时：**逐组报 + 后分层权重**是唯一能把"抽样偏了"这件事显性化的做法——现在总体 55.74% 里 OS 占 41.5%（真实 12.4%），CAD 只占 4.9%（真实 16.5%）。
→ icon 子集同理：官方论文自己写 "text constitute 62.6%… the remainder being icons"，并在 §5.1 单列 "Icons targets are more difficult to ground than texts"；我们实测 text 68.75% vs icon 41.38% —— **icon 是官方公认最难的一类，不要把 41.38% 当成"我们特别差"**。

---

## 6. ≤8 条最小改动（按投入产出排）

1. **补齐 644 张 PNG**（只动数据，不动代码）：`$env:HF_ENDPOINT='https://hf-mirror.com'; hf download likaixin/ScreenSpot-Pro --repo-type dataset --local-dir .scratch/sspro --include "images/*"`（≈1.3 GB）。
   **预期**：有效 N **183 → 1581**；icon n **87 → 604**；CAD n **9 → 261**；总体 Wilson 半宽 **7.12pp → 2.45pp**；配对 MDE **10.61pp → 3.61pp**。
   **验证**：逐条 stat `annotations/*.json` 的 `img_filename` 全部命中（现在缺 644）。
2. **`src/minicode/eval/sspro.py::stratified_subset`**：加显式 `frame` 参数（= 当前实际可用的样本集），并把选中的 `sample_id` 清单写进 `sspro-report.json` 的 `dataset.manifest`。
   **预期**：抽样**可复现**（现在 k×frame 网格都复现不出 183；"固定 seed → 同一批"目前不成立），且抽样框不再随"图片下载到哪一步"静默漂移。
   **验证**：同 seed 重跑两次，manifest 逐行相等。
3. **`src/minicode/eval/stats.py`**：新增 `post_stratified_estimate()`（Cochran 1977 p.108，含 FPC），复用已有 `wilson_ci` / `mcnemar_midp` / `paired_bootstrap_delta` / `mde`。
   **预期**：总体点估计从"无权重 55.74%"变为**框 937 后分层 58.53%**，设计基 SE **3.78pp**；对框真值的偏差从 **−0.14/+3.37pp → +0.04/+0.07pp**（蒙特卡洛实测）。
   **验证**：对同一批 183 跑新旧两套，差值应 ≈ +2.8pp 且与蒙特卡洛预测一致。
4. **`src/minicode/eval/__main__.py` + `sspro.py::build_report`**：报告里加**逐 group（6 类）真值 + n + Wilson 95%**，并用官方键名 `group:{group}` / `app:{application}` 输出以便与 leaderboard 对齐。
   **预期**：CAD/Dev/Office 从"不可报"（半宽 26.3/21.9/19.7pp）变成"可报"（全量下半宽 6.0/5.6/6.4pp）；每组 n 之和 = 样本数。
   **验证**：报告 JSON 里 6 个 group 键齐全、Σn = N。
5. **评测 runner 的抽样分配**：`--per-stratum` 从"每层固定条数"改为"按层比例分配（n_h ∝ N_h）"或直接全量 1581。
   **预期**：消除 Linux 过度加权 **8.6×**（现在 f=1.000 普查、windows/text 只抽 0.106）；全量跑时 f_h=1，抽样误差归零。
   **验证**：全量跑批时各层 n_h == N_h。
6. **改判据（不是代码）**：183 条上 MDE = **10.61pp**，1536 vs 2016 的 **+8.74pp 低于 MDE**、McNemar mid-p = 0.0213 → 现在只能写"方向一致、幅度未达 80% 功效线"，**不能写成"2016 档显著更准"**。
   **预期**：补全量后同一效应 SE = **1.29pp**、p<1e-10，届时可以写成硬结论。
   **验证**：全量跑批后用仓库现成的 `mcnemar_midp` 重算。
7. **报告里写清"总体真值"是哪个框**：现在 55.74% / 64.48% 都是 **937 框**上的数，而官方 leaderboard 报的是 **1581 框**（两个公开模型在两框上的真值差 **1.06pp / 2.72pp**，蒙特卡洛实测）。
   **预期**：跨项目/对外对比不再系统性错位；补图后与官方逐 app 分数可直接对齐。
   **验证**：报告 JSON 增加 `dataset.frame_size` 与 `dataset.population_size` 两个字段。
8. **icon 子集口径对齐**：官方论文已声明 icon 比 text 难（text 62.6% vs icon 37.4%），我们实测 text 68.75% vs icon 41.38% —— 报告里把 icon 41.38% 的 Wilson 95% **[31.61%, 51.88%]** 与"目标占比中位 12 px"一起给出，**不要再用单点 41.38% 对标"≥45%"的验收线**。
   **预期**：icon 验收线改为"**点估计 ≥ 45% 且 Wilson 下界不塌**"，避免把 10.1pp 的区间宽度当成噪声。
   **验证**：全量后 icon n=604、半宽 4.0pp，验收线才真正有判别力。

---

## 7. 不适用 / 未找到 / 边界

- **(d) "有没有公开工作报过用 183 条抽样估总体 vs 全量 1581 的偏差"** → **未找到**。检索到的公开工作一律是**全量 1581 报点估计**（论文 Table 4、leaderboard 逐 app），或用 bootstrap CI 报不确定性，**没有"子集估总体再与全量对照"的实验**。本报告 §4.2 的蒙特卡洛是自建的【推断】。若要在对外文档里引用，只能说"本项目自测"。
- **官方脚本没有 CI 这件事不是缺陷而是惯例** —— leaderboard 明写 "Micro-average numbers are reported"。我们**不改对外口径**（对外仍报 micro-average），只在**内部决策**里用 CI/显著性。
- **HF 官方仓库本环境直连超时**（README / tree API 均失败），涉及 HF 的事实（镜像列表、下载体积、数据集卡 license 字段、逐 app 计数表）来自检索摘要【二手】；**官方论文、leaderboard、评测脚本源码三项均已直读原文【一手】**，且其逐 app 计数与我们本地标注 26/26 吻合，可互相背书。
- **论文 PDF 抓取工具不支持**（`unsupported content type "application/pdf"`），§3 引用的 arXiv:2607.17136 / 2606.25760 的具体数字来自检索摘要【二手】。
- 本轮**未修改仓库任何文件、未 commit、未执行 gh**；除本报告外未写入任何文件。

---

## 附：关键 URL 清单

- 论文全文（HTML，可开）：https://arxiv.org/html/2504.07981v1
- 官方 leaderboard：https://gui-agent.github.io/grounding-leaderboard/
- 官方评测脚本：https://raw.githubusercontent.com/likaixin2000/ScreenSpot-Pro-GUI-Grounding/main/eval_screenspot_pro.py
- 官方代码仓：https://github.com/likaixin2000/ScreenSpot-Pro-GUI-Grounding
- 官方数据集：https://huggingface.co/datasets/likaixin/ScreenSpot-Pro
- parquet 镜像：https://huggingface.co/datasets/lmms-lab/ScreenSpot-Pro
- FiftyOne 镜像：https://huggingface.co/datasets/Voxel51/ScreenSpot-Pro
- 官方 PDF：https://likaixin2000.github.io/papers/ScreenSpot_Pro.pdf
- 区间方法（Wilson/CP 推荐）：https://doi.org/10.1214/ss/1009213286 （Brown, Cai & DasGupta 2001, *Statistical Science*）
- 分层抽样比例方差与 FPC：Cochran 1977《Sampling Techniques》§5.14（p.108）
- CUA 评测可靠性方法学（bootstrap CI / 多 seed / 双峰）：https://arxiv.org/abs/2607.17136
- CUAgent UQ benchmark（per-cell bootstrap CI）：https://arxiv.org/abs/2606.25760

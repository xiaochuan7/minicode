# 视觉主功能 · 最终收口报告（验收线 8 组 / 施工清单 10 条 · 逐条交账）

> **本文件是最终版**，取代 `docs/research/vision-closure-report.md`（那份保留为历史，**含 8 处未达成写成达成**，
> 见 `docs/research/ralph-round1-review.md`）。
> **施工图**：`docs/research/vision-final-plan.md`（10 条施工清单 + 8 组验收线 + 引用纪律）。
> **根因依据**：`docs/research/vision-root-cause.md`。
> **两遍对抗式复核**：第一遍 `docs/research/ralph-round1-review.md`；第二/第三遍 `docs/research/ralph-round2-review.md`。
> **引用纪律**：对外结论只引 ①本机实测（带样本量、判定口径、产物路径）②公开一手（带 URL）；**8 类禁引数一处未出现**。
> **口径版本**：本轮换过 `landing_error_px` 的算法（到矩形距离，框内 = 0，版本 `rect-distance-v1`）
> → **历史 `landing_error_px` 与本轮不可比**；44.81% 与本轮的 click accuracy **判定口径相同**
> （都是官方 bbox 命中、同一批 183 条、同一条判定脚本 `eval/formats.py::SCREENSPOT_PRO`），但**不是同一跑批**（见 §一）。
>
> **本文件的三条纪律**（前两遍复核用它们否掉了上一版）：
> 1. **不把「未达成」写成「达成」** —— 每条验收线都有判定与产物路径；
> 2. **Δ 必须标口径** —— 产物用「右臂 − 左臂」，本文件的每个 Δ 都写明是哪一种；
> 3. **跨跑批的比较要写明** —— 模型方差没被钉住，只有**同一跑批内**的配对结论可以直接读。

---

## 一、一句话结论

**我们自己的两处病灶（接线、判据）确实拆掉了，量到了数**：同批 183 条 ScreenSpot-Pro 抽样上，
click accuracy **44.81% → 55.74%**（官方 bbox 命中口径，**Wilson 95% CI [48.50%, 62.74%]**）；
精定位相对「只用粗定位」的净胜 **+14.75 pp**（always 102/183 = 55.74% vs coarse_only 75/183 = 40.98%）。

> **头条的四个限定（一条都不能省）**：
> * **① 配对设计**：always 与 coarse_only 两臂**共用同一次上游粗定位回答**
>   （`comparison.coarse_shared.identical = 183/183`）→ 臂间差异**不混模型随机性**；
>   配对 mid-p McNemar **p = 9.02e-05**，Δ 95% CI（产物「右臂 − 左臂」口径，右 = coarse_only）**[−21.86, −7.65] pp**
>   → **CI 不跨 0**，且 |Δ| 14.75 pp ≥ MDE(183, π_d=0.268) = **10.72 pp**。
> * **② 抽样构成**：183 条是**分层抽样**，OS 占 76/183 = **41.5%**（全量 12.4%）、CAD 占 9/183 = **4.9%**（全量 16.5%）
>   → 按全量 group 构成**重加权后是 54.27%**（bootstrap 95% CI [44.57%, 63.45%]，n=183，2000 次，种子 20251020；
>   同一方法跑旧基线复现 42.47% ≈ 文档里的 42.5% → 方法可信）。**55.74% 是抽样构成下的值，不是真值。**
> * **③ CAD 的命中数是本轮的 3/9**（旧基线才是 0/9）—— 上一版报告把旧基线的「CAD 全灭」挂在 55.74% 旁边，已订正。
> * **④ 44.81% → 55.74% 是**两次独立跑批**之间的比较**（`g1-eval-330` vs `g2-eval-final`），
>   模型各自重抽、**方差没被钉住** → 这一步**不能**当成受控实验读。
>   可用的锚点：`coarse_only` 臂跨跑批几乎不动（76/183 → 75/183），
>   说明变化主要发生在「精定位那一级」，而不是粗定位抽样变了。

**但两条端点硬事实吃掉了另外两个目标，且都不是「实现没写对」**：

1. **服务端宽度上限 ≈ 2016 px**：2048×1280 档期望 3404 / 实测 **3242**（差 **−162**）；2044×1260 档期望 3285 / 实测 **3242**（差 **−45**）
   → **2048 档的坐标系不可信**。反过来，**2016×1260 档实测透传**（期望 3240 / 实测 3242，差 +2 = 本端点每张图固定多记的网关开销，
   扣除后差 **0**）。
2. **网关忽略 `temperature`**：3 轮 × 10 次连发，**原文一致率三轮都是 10%**（`text_consistency.modal_rate = 0.1`、`distinct = 10/10`），
   带不带 temperature **没有可测差别**；而请求体**确实显式带了** temperature（`src/minicode/model/client.py:208`）
   → **模型方差钉不住** → 任何跨跑批的分数差都混着随机性。

**端到端两波没有达标**（wave1 0/5、wave2 3/7），但**卡住的不是视觉落点**：wave1 的失败全部来自**环境/树**；
而**落点条款这一轮第一次判出来了** —— wave1 的 W-3 两次落点都是 **A 命中（到矩形 0.0 px，到中心 27.8 px）**，
W-1 的两次视觉定位**明确记了 `undecidable` 并点名缺哪条真值**。
⚠️ 但 W-3 那两步的 `channel` 是「放大」不是视觉通道 → **视觉通道本身的真机落点精度至今零证据**。

---

## 二、两遍复核的合并台账

### 2.1 第一遍（`ralph-round1-review.md`）的 6 条 nextSteps —— 逐条判定

| # | 第一遍点的 | **判定** | 证据 |
|---|---|---|---|
| 1 | 报告 8 处「未达成写成达成」 | **已修（8/8）** | A 组 2.87 px（`denominator_pair_divergence_px`）/ E 组补 2 行并标未达成 / G 组「四档分类率 100% + `visual_step_decidable_rate = 0.0`」/ G 组 27.8 px / ④ 回落率 14.2%（25/176）/ B 组「36 px 不在空白区」/ F 组并列 12.0 px / ⑤ 产物索引 + 离线重算 —— 第二遍逐条独立重算，**8/8 成立** |
| 2 | 9 处口径不一致 | **已修（7 处彻底 + 2 处半修）** | 半修的两处：**Δ 口径**（D 组那格「下界 > 0」与它自己标的「右臂−左臂」打架）与**延迟归属**（16.70 s 取自标定探针，不是评测负载）→ 本文件 §三 / §三-H 已按产物改写 |
| 3 | 7 处引用纪律破口 | **已修（7/7）** | 第二遍把报告里全部 `runs/` 路径跑了一遍存在性检查 → 全在；26 个禁引模式 grep → 只命中本仓自己的数（84.2% / 42.1% / 14.2%），**零违规** |
| 4 | 实现 BLOCKER：`parse_optional_point` 截断检查 | **已修** | `src/minicode/ground/prompt.py:453-458` → `is_truncated(segment)` → 抛 `GroundingParseError`（**不抢救**）；`ground/channel.py:690-704` 接住 → `fallback=True`、`fallback_cause=no_output`、落点 = 粗定位点；回归用例 `ground/test_ground_two_stage.py:388/:594` |
| 5 | 实现 MAJOR ×2：相等断言收紧 + 接生产链；χ²/MDE 接线 | **已修** | `calib/calibrate.py:364`（收紧成相等断言）+ `:287`（旧盒子降级为诊断）+ `e2e/visual.py:110/:362/:376`（生产链接线，**测量口径也绕不过**）；`eval/stats.py:105 mcnemar_exact` / `:135 mcnemar_chisq_cc` / `:259 mde` + `eval/sspro.py:1187/:1204` 接线 |
| 6 | 需人拍板/提供输入的 4 条 | **4 条全部照做** | ① 计价表：H 组标「待用户提供端点计价表」，费用不折钱；② 2016×1260：探针 + 实跑都做了 → **icon 中位 15.75 px 没过 ≥16 → 按规则退回 1536×960 并标未达成**；③ crop192 的 183 条配对：用已有产物**离线重算**（`runs/g2-eval-crop192/paired-recheck.json`）；④ 补跑标定会话：新增 `runs/v1-calib/s4b` → 图像路径 6/7 可用 |

### 2.2 第一遍那 1 处「误报」——**不是误报，是 Lead 的抽验带了时点限定之外的前提**

第二遍与第三遍分别独立复核了这条（文件系统时间戳 + git 时间戳），结论一致：

| 时刻 | 事件 |
|---|---|
| 16:16:48 | `runs/g2-eval-crop192/model-requests.jsonl` **创建** → crop192 评测开跑（请求边跑边写） |
| **16:20:55** | commit `7e74d33`：**收口报告首版提交**，其 `:234` 索引 `runs/g2-eval-crop192/sspro-report.json` |
| 16:34:02 | crop192 请求日志最后一次写入（177 行写完） |
| **16:34:04** | crop192 的 `sspro-report.json`（763576 B）**才生成** |
| 17:40:56 | commit `1945bd0`：第一遍存档 + Lead 抽验（把上面那条判成「复核误报」） |

→ **报告引用的那条路径，在报告提交后 13 分 9 秒里都是不存在的**：第一遍对它审的那一版报告**报得对**；
把它判成「误报」的是 Lead 的抽验（拿 16:20 的报告去对 16:34 之后的文件系统）。
**处置**：`docs/research/ralph-round1-review.md` 的判定已按时间线订正（「成立（带时点限定）」，6/6 成立），
本文件不再把它当误报。**教训**：目录快照必须带时点，否则「路径不存在」这类结论会随跑批进度漂移。

### 2.3 第二遍（N1~N10）与第三遍（P1~P8）的新问题 —— **本文件逐条处置**

| # | 问题 | 处置 |
|---|---|---|
| N1 | ⑥ 行把 2048 档的 token 差写成 −45，实为 **−162** | **已改**（§三-F 与 §四-⑥） |
| N2 | E 组「回落目标 = 精定位点」在产物里是 **25 次 coarse** | **已改**：写成「偏离回落已消除（0 次）；硬失败回落 25 次仍落在粗定位那一级（无精定位点可回落）」→ 该行**判未达成** |
| N3 | 探针自检：B 组写「未改」、⑧ 行写「全部落地 ✅」，两处结论相反 | **已统一**：本文件统一判**未达成**（规格要求改 `verify/` 冻结目录），并列入 §六「与规格的分叉（待人拍板）」 |
| N4 | ⑤ 的「crop 扫描改比例扫描」**未做**（`numbered_bench.py:669 CROP_SCAN_SIZES` 全是固定 px，生产在用的 0.5×当前帧不在扫描集内） | **已改**：⑤ 判 **⚠️ 部分达成**并披露该项未做 |
| N5 | ⑨ 行说「`eval/stats.py` **四个**纯函数」，实际 **六个**；χ²/精确二项/MDE **没交账** | **已改**：交账行补齐（六个函数 + 产物里的三个新键） |
| N6 | §五 漏报 `calib` 的**语义反转型**接口破坏（第三参 `aspect_ratio → declared_size`，返回值语义整个反转） | **已补**（§七） |
| N7 | Δ 的方向在同一格里自相矛盾、⑨ 行干脆不标口径 | **已改**：本文件每个 Δ 都标口径（产物「右臂 − 左臂」），并同时给「精定位净胜」这一读法 |
| N8/P4 | H 组延迟 16.70 s 取自**标定探针**请求；且与 `model_calls.sent = 0` 互斥 | **已改**：三处延迟分别标来源，并写明**生产档（1536）的评测负载延迟本轮未实测**（那一跑批是回放） |
| N9/P3 | 头条的「重加权 42.5%」与「CAD 命中 0/9」**都不是本轮跑批的数** | **已改**：42.5% 改成本轮重加权 **54.27%**（同方法复现旧基线 42.47% 以验证）；CAD 改成本轮 **3/9** |
| N10 | commit `1db1622` 的 message 说改了调用方，实际 diff 里没有 `channel.py` | **已记**：功能成立（`channel.py:690` 既有 `except` 恰好覆盖新抛点），但提交说明与 diff 不符 —— 本文件如实标注 |
| P1 | F 组「**编号分支白进**」一行**整行未交账**且实现未做 | **已补行并判未达成**（入口条件只有 `if candidates:`，`channel.py:497`；全库无覆盖率门槛）+ 列入待拍板 |
| P2 | H 组两行（**无文本率** / **解析失败影响**）被自造行顶替 | **已补两行**并用请求日志的四个字段直读出数 |
| P5 | F 组「现在」列 13.25 px 引的是 m17 里的 **`1280x800_legacy_integer_factor`** 档，却标成「旧档位 1280×800」 | **已改**：档位名写全（同产物里 `1280x800` 是 11.67 px / 78.8%） |
| P6 | ⑥ 行「13.25 → 14.00」没标口径；验收线指定口径（n=87）的「现在」在产物里没有 | **已改**：两套口径并列标样本量；n=87 口径的「现在」= **12.0 px**（`g1-eval-330` 没有这个埋点 → 旧档位的 n=87 值**不存在**，不编） |
| P7 | ⑤ 的「以粗定位框中心为中心」是靠 ⑦ 删 `point` **间接**成立，规格点名的 `coord/plan_crop` 签名未改（冻结） | **已披露**（§四-⑤）：结论成立但耦合未解，附后果说明 |
| P8 | §四「还剩的验收线缺口」表漏了 **B 组**与 **E 组** | **已补**（§六） |

---

## 三、验收线 8 组：现在 → 目标 → 达成与否 → 证据

> **分母统一**：除特别标注外，分母都是 **ScreenSpot-Pro 本机 183 条抽样**（种子 20251020，分层抽样后本地有图的那些），
> 判定 = **官方 bbox 命中**（`eval/formats.py::SCREENSPOT_PRO`）。
> **口径**：产物里的 Δ 一律是「**右臂 − 左臂**」；本文件同时给出「精定位相对只用粗定位的净胜 = −Δ」这一读法。

### A. 视觉通道准入（标定闸门）

| 指标 | 现在 | 目标 | 达成与否 | 证据 |
|---|---|---|---|---|
| `available == True` | **7 次独立会话里图像路径 6 次可用**（s0 0.90、s1 1.00、s2 1.00、s3 0.90、**s4b 1.00**、s5 1.00） | 6 次会话中 ≥ 5 次 | **达成**（6/7 ≥ 5） | `runs/v1-calib/s0..s5/calibration-records.jsonl` + `runs/v1-calib/s4b/calibration-records.jsonl`；对照表 `runs/v1-calib/session-table.md` |
| 等价标度族落点分歧 | **2.87 px**（0~999 vs 0~1000，2560 宽，欧氏） | ≤ 4.0 px | **达成**（2.87 ≤ 4） | 同上 → **`denominator_pair_divergence_px = 2.8676273`**（⚠️ **不是** `divergences_px`：后者数组里存的是 `Infinity`；`0.00` 只来自 SSL 失败的 s4） |
| 96 px 方块命中率（K=5 轮） | **0.90 ~ 1.00** | 每次会话 ≥ 0.6 | **达成** | 同上（`hit_rate`） |
| 1 px 的语义 | 四个常量互不共用（判定 1 px / 越界 2 px / 落点等价 4 px / 探针命中半径 48 px） | 同 | **达成** | `src/minicode/calib/tolerance.py` |
| 落点误差（图像路径，附加观测） | 中位 **1.37 ~ 3.02 px**（补跑的 s4b：2.83），p90 4.85 ~ 29.70 px | — | — | 同上（`median_error_px` / `p90_error_px`） |

### B. 落点口径与阈值

| 指标 | 现在 | 目标 | 达成与否 | 证据 |
|---|---|---|---|---|
| 离线评测口径 | 官方 bbox 命中 | 不动 | **达成** | `src/minicode/eval/formats.py::SCREENSPOT_PRO` |
| 真机 e2e 落点判据 | **三档**：A 命中（落点在目标控件矩形内）/ B 近失（出框但到矩形边界 ≤ 36 px，只记档不判死）/ C 错选（> 36 px，判失败） | 同 | **达成** | `src/minicode/e2e/checks.py::landing_tier` / `landing_in_target_rect`；口径版本 `rect-distance-v1` |
| `landing_error_px` 语义 | 到**矩形**距离（框内 = 0） | 真机侧与浏览器侧统一 | **达成** | `e2e/real.py` + `e2e/apps.py`（都调 `checks.rect_distance_px`） |
| 探针自检 | **未改**（`verify/` 是冻结目录） | PASS = 「全部目标命中（落在方块内）」 | **未达成**（规格与冻结目录冲突 → §六 待拍板） | `src/minicode/verify/` 的 `git diff 3acd5e0...HEAD` 为空 |
| 36 px 近失带的校准 | **36 px 不在空白区**：产物 `band_36px = {hits_failing_band: 0, misses_rescued: 18}`，misses = 81 → **18 条（22.2%）落在带内**；「找对」子集 `dist_to_bbox` p50 = 0（命中 ⟺ 距离 0），「挑错」p50 = 519.2 px | 确认 36 px 落在两峰之间的空白区 | **未达成**（36 px 是「牺牲精度换召回」的折中带，会捞回 22.2% 的错选） | `runs/g2-eval-final/sspro-report.json` → `near_miss_calibration`（纯算术，零模型调用） |

### C. crop 命中率（窗口是否框住目标）

| 指标 | 现在 | 目标 | 达成与否 | 证据 |
|---|---|---|---|---|
| 「完整含住 GT 框」比例 | **离线 84.2%**（154/183，0.5×当前图）；**实跑 79.55%**（140/176 次开窗） | ≥ 75% | **达成**（两个口径都过） | `runs/m3-crop-geometry/crop-geometry.json`；实跑 `runs/g2-eval-final/sspro-report.json` → `telemetry.crop_contains_target_rate` |
| 容忍的粗点误差 | **±839 / ±522 px**（0.5×当前帧，中位） | ≥ ±512 / ±400 | **达成** | 同上 |
| 该指标的常驻性 | 每次评测都报（`crop_contains_target` + `crop_miss_reason` 落盘） | 常驻 | **达成** | `src/minicode/eval/sspro.py` 埋点段 |

> ⚠️ **两个含住率不是同一把尺子**：① 离线脚本 `ground/crop_geometry.py:228` 调 `plan_crop(..., target_radius_px=0.0)`（不知道目标半径），
> 生产链 `ground/channel.py:677` 传的是粗框半径（窗口额外扩一圈）；② 离线用**旧一轮**落盘的粗点，实跑用**新粗点**。
> ⇒ 两列都列，**不可直接相减**。

### D. 精定位产出率

| 指标 | 现在 | 目标 | 达成与否 | 证据 |
|---|---|---|---|---|
| 「窗内无目标仍产出被采纳的点」（假命中） | **20 条**（窗口不含目标却仍被采纳了精定位点） | 归零 | **未达成**（机制有效但模型不总用：精定位的「没找到」逃生口本轮被用了 **25 次**，都干净回落到粗定位） | `runs/g2-eval-final/sspro-report.json` → `telemetry.fake_hits` |
| 被自己判据丢掉的正确答案 | **0 条**（结构性：偏离判据降级为记录器，不再改落点） | 0 条 | **达成** | `src/minicode/ground/channel.py`（第 ④ 条改动）+ `telemetry.landing_source_distribution` |
| 精定位净效应（两段式 vs 只用粗定位） | **精定位净胜 +14.75 pp**（always 102/183 = 55.74% vs coarse_only 75/183 = 40.98%）；配对 mid-p McNemar **p = 9.02e-05**；Δ（右−左）= **−14.754**，95% CI **[−21.86, −7.65] pp**；**CI 不跨 0**，且 \|Δ\| ≥ MDE(183, 0.268) = 10.72 pp | 主口径下配对 Δ 的 95% CI 下界 > 0 | **达成** | 同上 → `comparison.paired.always_vs_coarse_only`（`left`=always、`right`=coarse_only） |
| 解析失败对分数的影响 | **0 pp**：25 条 `no_output` 的样本在两条臂上**同命中**（6/6，25/25 一致） | 保持 0 | **达成**（本轮可直读了） | 同上 → `outcomes` 逐条 + `telemetry.fallback_cause_distribution` |
| 分层（预注册的次要分析） | 粗定位命中那 75 条：两段式比粗定位 **−14.67 pp**（精定位把 11 条本来对的弄坏了）；粗定位没命中那 108 条：**+35.19 pp** | — | — | 同上 → `paired.strata` |

### E. 门控行为

| 指标 | 现在 | 目标 | 达成与否 | 证据 |
|---|---|---|---|---|
| 硬失败回落的原因种类 | **3 种**（`no_output` / `out_of_bounds` / `call_failed`），实跑只出现 `no_output` | 只保留这三种 | **达成** | `ground/gate.py::FallbackCause` + `telemetry.fallback_cause_distribution` |
| 偏离判据的角色 | **记录器**：只落盘 `deviation_exceeded`，不再改落点；量换成 **‖精定位 − 粗定位点‖₂** | 同 | **达成** | `ground/channel.py`（第 ④ 条）、`ground/gate.py` |
| 阈值的表达 | **crop 宽度千分位**（`deviation_c1000 = ‖Δ‖₂ × 1000 / crop 宽`，落盘键 `deviation_tolerance_c1000`） | 同 | **达成** | 同上 |
| 回落目标 | 偏离回落已消除（**0 次**）；但硬失败回落的 **25 次**最终落点来源仍是 `coarse`（没有精定位点可回落） | 回落时不得降级到更差的一级 | **未达成**（25 次仍落在粗定位那一级） | `telemetry.landing_source_distribution = {coarse: 25, refined: 151, whole_screen: 6, none: 1}` |
| **阈值取值** | **5 折全部选中 ∞**（`threshold_distribution` 的 `tolerance_px` / `deviation_px` 的 `values` = 5 个 1e9，中位数 1e9、极差 0）；生产侧读的 `gate.py:230 DEFAULT_DEVIATION_TOLERANCE_PER_MILLE = 260.4` 是**旧量**（旧口径 50 px @192 crop）的历史读数 | 在按 app/platform 分组 K 折上折外选、折内评，报各折阈值分布 | **未达成** | `runs/m9-kfold/kfold.json` → `threshold_distribution`；`src/minicode/ground/gate.py:230` |
| **可迁移性** | K 折的**输入是旧跑批** `runs/g1-eval-330`（`input.recorded_run`），不是本轮 183 条；`replay_check = {on_demand_matches: 165, always_matches: 183, consistent: false}`（重放不自洽） | 在全量 1581 的确认集（1398 条）上复现该方向 | **未达成**（这一步没做） | `runs/m9-kfold/kfold.json` → `input.recorded_run` / `replay_check` |

### F. 图标召回

| 指标 | 现在 | 目标 | 达成与否 | 证据 |
|---|---|---|---|---|
| icon 子集 click accuracy | **41.38%**（36/87，Wilson 95% CI [31.61%, 51.88%]） | ≥ 45% | **未达成**（差 3.6 pp） | `runs/g2-eval-final/sspro-report.json` → `comparison.always.accuracy_by_ui_type` |
| text 子集 | **68.75%**（66/96，Wilson [58.91%, 77.15%]） | — | — | 同上 |
| icon 在**模型输入图**里的像素尺寸 | **两套口径**：评测台口径（验收线「怎么测」指定的）**12.0 px**（n=87，below_16px 73.6%）；全量口径 **14.00 px**（n=604，1536×960 档，62.7%）；旧档位 **13.25 px**（n=604，`1280x800_legacy_integer_factor` 档，65.9% —— ⚠️ 同一产物里的 `1280x800` 档是 11.67 px / 78.8%，不是它） | 中位 ≥ 16 px | **未达成** | `runs/g2-eval-final/sspro-report.json` → `model_input_target_sizes.icon`（n=87）；`runs/m17-icon-size/icon-size.json`（n=604，5 个 tier） |
| **2016×1260 档的实测**（本轮 A6） | 评测台口径 **15.75 px**（n=87，**差 0.25 px 仍未过 ≥16**）、below_16px 51.72%；全量口径 18.38 px（n=604，**过**）—— 两口径分母不同，**不可混用** | 同上 | **未达成 → 按拍板规则退回 1536×960**（`src/minicode/model/image.py` 未改） | `runs/g3-eval-2016/sspro-report.json` → `model_input_target_sizes.icon`；探针 `runs/m6-token-probe-2016/token-probe.json` |
| <16 px 的占比 | 73.6%（64/87，评测台口径）／62.7%（379/604，全量口径） | ≈ 0% | **未达成** | 同上 |
| 纯视觉下的 icon 候选召回 | 3/87 = 3.4% | 本轮不设目标 → 改为常驻指标 | **达成（按「改为常驻指标」读）** | `eval/sspro.py` 埋点段（`crop_miss_reason` / `fake_hits`） |
| **编号分支白进** | **入口条件只有 `if candidates:`**（`ground/channel.py:497`），全库**无任何覆盖率/关键词门槛**（grep `coverage_ratio` / 描述关键词 = 0 命中） | icon 进分支降到 32/87（ratio ≥ 0.3）或 18/87（≥ 0.4），text 仍留 55/96 | **未达成（未做，本轮未测）** | `src/minicode/ground/channel.py:495-500`、`:982-1011`；`ground/candidates.py:344-362` 的 IoU 列只是观测列 |

### G. 端到端（wave1 / wave2）

| 指标 | 现在 | 目标 | 达成与否 | 证据 |
|---|---|---|---|---|
| wave1（Windows 设置 + Chrome） | **0/5**（通过 0 / 失败 3 / infeasible 2 / 人工代跑 1） | ≥ 2/5 | **未达成** | `runs/v1-wave1c/e2e-report.json` |
| wave2（微信 / Electron / cc-switch） | **3/7 = 42.9%**（通过 3 / 失败 2 / infeasible 1 / 合规停手 1 / 人工代跑 2） | ≥ 5/7 | **未达成** | `runs/v1-wave2/e2e-report.json` |
| 视觉步骤的**可判率** | **四档分类率 100%**（wave1 的 2 条视觉步骤每条都落在 hit/near-miss/wrong/undecidable 之一，且点名缺哪条真值）；**但产物字段 `visual_step_decidable_rate = 0.0`（判出档 0/2）** | 100%（判出档） | **未达成**（判出档 0/2） | `runs/v1-wave1c/e2e-report.json` → `landing.visual_step_decidable_rate` / `landing.visual_steps` |
| 落点真值来源（第一次有可判条款） | wave1 的 W-3 两次落点都判 **A 命中（到矩形 0.0 px，到中心 27.8 px）**；wave2 另有 1 条 A 命中。⚠️ W-3 那两步的 `channel` 是「放大」不是视觉通道 → **视觉通道本身的真机落点精度仍零证据** | 真机视觉两段式第一次有可判条款 | **部分达成**（条款有了，视觉通道零证据） | 同上 → `landing.tiers` = A 2 / B 0 / C 0 / 判不了 2（wave1）、A 1 / B 0 / C 0 / 判不了 0（wave2）；到中心距离见 `tasks[].steps[].note` |
| 失败原因里「模型指错」是不是可出现的类别 | 是（落点 C 错选就是「模型指错」；本轮 wave1/wave2 没有 C 档，但三档条款能表达它） | 可出现 | **达成** | `e2e/checks.py::LandingTier.WRONG` |

> ⚠️ **「到中心 13.9 px」是上一轮 wave1b 的数**（`runs/v1-wave1b-console.log:65`）；**wave1c 实测是 27.8 px**
> （`runs/v1-wave1c/e2e-report.json` → W-3.1 / W-3.2 的 `note`）。两个数不可混引。
>
> ⚠️ wave1 的失败全部来自**环境/树**：W-1.1 期望「判不了」、W-1.2 动作失败（`locate_failed`：整屏第二遍也答负出口
> 「这张图里没有对应的元素」，而「屏幕」**就在**设置页里 → **负出口会制造假否定**）、W-3.2 期望不成立（树里没有时钟文本）。

### H. 模型调用数与成本

| 指标 | 现在 | 目标 | 达成与否 | 证据 |
|---|---|---|---|---|
| 每目标模型调用数 | **2.00 / 2.00 / 1.0383**（always / on_demand / coarse_only） | ≤ 2.5 次/目标 | **达成** | `runs/g2-eval-final/sspro-report.json` → `model_calls_per_target` |
| 图像 token / 目标 | **3539.27** = **1788.39（粗定位那一级）** + **1750.89（精定位 crop 那一级）**（平均每目标；⚠️ **不是**「1925 + 自适应 crop」——1925 只是 2560×1600 那一张图的档位 token，实跑 183 条的原图有 14 种尺寸） | 3765（= 2048×1280 整屏 3404 + crop 512 361） | **达成（生产档 1536×960）**；⚠️ **3765 与本轮几何不同源**，不许相减说「省了多少」；**2016×1260 档实测 4989.23 → 那一档不成立**（且已因 icon 中位未过而被退回） | `telemetry.image_tokens.per_target` + `outcomes` 逐条 `image_tokens` 按臂求和 ÷ 183；`runs/g3-eval-2016/sspro-report.json` |
| 4096 档残余无文本率 | **四个字段已落盘可直读**（`stop_reason` / `content_block_types` / `text_len` / `parse_ok`）；**8192 档实测无文本率 0%**（n=417，2016 档实跑；另 n=177 crop192 精定位级、n=40 标定探针，也都是 0%；`stop_reason = max_tokens` 0 次） | 8192 档幂律外推 0.03~0.07%【推断】+ 四个字段直读 | **达成**（直读条件具备，实测 0% 优于外推值；⚠️ **4096 档本身本轮未重测** —— 0.56%（19/3404）是旧跑批历史值） | `runs/g3-eval-2016/model-requests.jsonl`、`runs/g2-eval-crop192/model-requests.jsonl`（逐行直读） |
| 解析失败对分数的影响 | **0 pp**（25 条 `no_output` 在两臂同命中） | 保持 0 | **达成** | 见 §三-D |
| 延迟 | **生产档（1536×960）的评测负载延迟本轮未实测** —— 那一跑批是**回放**（`model_calls.sent = 0`、replayed = 977），一条评测请求都没发出去。可报的三处**不同源**的实测：标定探针 **16.70 s**（n=40，`runs/g2-eval-final/model-requests.jsonl`，image_tokens 是标定档阶梯）、crop192 精定位级 **16.06 s**（n=177）、2016 档整跑 **10.02 s**（n=416 个 HTTP 200；含那条 180 s 超时则是 10.05 s / n=417） | 必须同时报延迟与费用 | **部分达成**（延迟有数但不同源；**费用待用户提供端点计价表** → 本轮只报 token 与延迟） | 同上三个日志 |

---

## 四、施工清单 10 条：预期 vs 实际

| # | 预期 | 实际 | 判定 |
|---|---|---|---|
| ① 埋点 8 个字段 | 落盘 `crop_contains_target` / `crop_miss_reason` / `deviation_px`（两个参考点）/ `refined_point_px` / `fallback_cause` + 落点来源 / 粗定位原始 bbox+point / `dist_to_bbox` / 图像 token | **8 类字段全在**（`eval/sspro.py::ArmOutcome` + `ground/channel.py::_plan_fields` + `action/ports.py::GroundingEcho` → `raw_bbox` / `raw_text` / `landing_source`）；用旧数据反算复现了「36.1% 窗口不含目标」与「43.7% 回落率」两个已知数 | **达成** |
| ② `temperature=0` + 预算/日志 | 显式 `temperature=0`、预算 4096→8192、超时 120→180、日志原子写 + 4 个字段 | **代码全部就位**（请求体显式带 temperature → `model/client.py:208`）；**效果未达成**：同图同提示词连发 10 次，**原文一致率三轮都是 10%**、带不带 temperature 无差别 → **网关忽略 temperature** | **⚠️ 代码达成 / 效果未达成** |
| ③ 离线几何重算脚本 | 192 与 512 两行必须对上 42.1% / 71.0% | **逐字对上**（192 → 42.08% + 缺失 36.07%；512 → 71.04%） | **达成** |
| ④ 判据改口径 | 量 = ‖精定位 − 粗定位‖₂、回落目标 = 精定位、硬失败只剩三种、阈值 = crop 宽度千分位、判据降级为记录器；44.81% → ≥47.45%；**回落率 ≤10%**；被自己判据丢掉的正确答案 → 0 | 44.81% → **55.74%**（n=183，同一批样本，**跨跑批比较**）；**因偏离而回落 43.7% → 0%**；硬失败回落只剩 `no_output` **25 次 / 176 次开窗 = 14.2%**（旧轮 43.7% = 80/183 → **改善 29.5 pp，但未达 ≤10%**）；被自己判据丢掉的正确答案 23 → 0（结构性） | **⚠️ 部分达成**（**回落率未达 ≤10%**；分数含 ⑤ 的共同效果，**不能归给 ④ 单独**） |
| ⑤ crop 窗口换几何 | 以粗定位框中心为中心、当前物理帧 50% 线性尺寸、绝对下限 512、贴边向内平移不缩小、1:1 原生像素；含住率 42.1% → ≥75%；容忍 ±96 → ≥±512；同一批样本上比较两个 crop 尺寸 → 配对 mid-p McNemar | **离线含住率 84.2%**、**实跑 79.55%**（176 次开窗）；**容忍粗点误差 ±839/±522**；**配对对照（离线重算，零模型调用）**：0.5×当前帧那档净胜 **+14.75 pp**（p = 9.02e-05）、crop 192 那档净胜 **+7.65 pp**（p = 0.0107）—— 两档都是正收益，**增益随 crop 变小而变小**；⚠️ 两档的粗定位回答只有 **176/183 条一致**（跨跑批随机性），**不是严格配对** | **⚠️ 部分达成**（几何与配对对照成立；**`numbered_bench.py:669` 的 crop 比例扫描未做** —— `CROP_SCAN_SIZES` 仍是固定 px，生产在用的 0.5×当前帧不在扫描集内；且「框中心」是**靠 ⑦ 删 `point` 间接成立**，规格点名的 `coord/plan_crop` 签名未改（冻结目录）→ 粗定位一旦带回 `point`，窗口中心会静默退回「点」） |
| ⑥ 整屏提到 2048×1280 | **先探针**；图像 token 必须恰为 74×46 = 3404；不过就退回 1536×960 | **探针先做了**：2048×1280 期望 **3404** / 实测 **3242**（差 **−162**）→ **服务端又缩了** → 按清单退回 **1536×960**（期望 1925 / 实测 1927，扣除每张图固定多记的 2 个 token 后**正好相等** ✓）。**服务端宽度上限实测 ≈2016 px**（2044×1260：期望 3285 / 实测 3242，差 −45）。**本轮 A6 又把 2016×1260 档探针 + 实跑了一遍**：探针**通过**（期望 3240 / 实测 3242，差 +2 = 网关开销，扣除后差 0）→ 该档坐标系可信；实跑 183 条：总体 118/183 = **64.48%**、icon 中位（评测台口径 n=87）**15.75 px（差 0.25 px 未过 ≥16）**、每目标图像 token **4989.23**（H 组 3765 **不成立**）→ **按拍板规则退回 1536×960，生产档位不变** | **⚠️ 探针先行的纪律达成；icon 尺寸目标未达成**（**2016 档的备选已完整量过，本轮未采纳**） |
| ⑦ 提示词协议 | 粗定位删 `point`、加 `text` 与负出口；精定位加「没找到」逃生口；答案行优先 + 截断 0 分 + 松散写法仅在答案行接受；同步改标定探针 | 全部落地；**标定探针同步了负出口与解析纪律**（同一份实现 `model/answer_text.py`）。**本轮补**：精定位解析器加**截断检查**（`prompt.py:453-458` → 抛 `GroundingParseError`，**不抢救**；调用方干净回落到粗定位点）＋ 粗定位「答案行非 JSON → 回落扫全文」。⚠️ **副作用**：负出口会制造假否定（wave1c 的 W-1.2 答「没有对应的元素」，而「屏幕」就在设置页里） | **达成**（但引入了负出口的假否定风险） |
| ⑧ 真机落点换三档 | A 命中 / B 近失（≤36 px）/ C 错选；真值扩成「中心 + 矩形」；没有矩形 → 记 `undecidable` 并写清缺哪条真值；标口径版本 | 全部落地：`LANDING_HIT_RULE="bbox"` / `LANDING_NEAR_MISS_PX=36.0` / `LANDING_ERROR_PX_VERSION="rect-distance-v1"`；**W-1 的两次视觉定位明确记了 undecidable 并点名「树里没有「系统」/「屏幕」的控件框」**。⚠️ **探针自检那条没改**（`verify/` 是冻结目录 → 规格与冻结目录冲突，列 §六 待拍板） | **⚠️ 部分达成**（落点三档达成；探针自检未达成） |
| ⑨ 评测台配对设计 | 配对为主检验、mid-p McNemar、配对 bootstrap CI、Wilson 单臂区间、分组 K 折（禁 index%2）、记忆缓存落盘 + 阶段标签、`--full` / `--confirmation-split` | 全部落地（`eval/stats.py` **六个纯函数**：`mcnemar_midp` / `mcnemar_exact`（普通精确二项）/ `mcnemar_chisq_cc`（χ² + 连续性校正）/ `wilson_ci` / `paired_bootstrap_delta` / `mde`；`eval/holdout.py` 分组 K 折；`eval/sspro.py` 落盘缓存与阶段标签；CLI 两个开关）。**主检验已出数**：Δ 95% CI（right−left）[−21.86, −7.65] pp，**不跨 0**；对照与 MDE 也在产物里（`mcnemar_exact_p` / `mcnemar_chisq_cc_p` / `mde_pp = 11.25` / `delta_at_or_above_mde = true`） | **⚠️ 部分达成**（接线与主检验成立；**全量 1581 条那一跑本轮没做** —— 本机只有 939/1581 张图 + 墙钟不够） |
| ⑩ 端到端执行器修 + 重跑 | 修三个执行器问题；wave1 ≥2/5、wave2 ≥5/7 | 前两个执行器问题（`Shell_TrayWnd`、`ActionResult.result`）**在 3acd5e0 之前就修好了**（且 `ActionResult` **至今没有 `result` 字段** —— 修的是**调用方** `e2e/real.py`，不是加属性）；本轮修的是**新暴露的两个**：① 整屏第二遍遇负出口崩在 `box_center`（wave1 的 W-1）② 浏览器 preflight 的竞态异常把整轮 wave 打崩。两波**重跑了**（wave1 0/5、wave2 3/7） | **⚠️ 执行器修完；两波分数未达标**（卡在环境/树，不在视觉落点） |

> **本轮 A 阶段另修的实现缺陷**（两遍复核点名的，不在上面 10 条里，但必须交账）：
> **A4 BLOCKER**（`prompt.py` 截断检查 + 两个回归用例）· **A5 MAJOR ×2**（`calibrate.py` 相等断言收紧成唯一实现 +
> 接进 `e2e/visual.py` 生产链：坐标系不可信 → 准入闸门不放行 + 定位口显式失败，**测量口径也绕不过**；
> `eval/stats.py` 补普通精确二项与 χ²+连续性校正 + `mde()` 接线进评测台输出）。
> ⚠️ commit `1db1622` 的说明写「改了 `ground/channel.py` 的调用方」，实际 diff 里没有该文件 —— 功能成立是因为
> `channel.py:690` **既有**的 `except GroundingParseError` 恰好覆盖了新抛点（提交说明与 diff 不符，如实记）。

---

## 五、被证伪的推断

| 推断 | 结论 |
|---|---|
| 「**显式 `temperature=0` 就能关采样**」 | **被证伪**（本机端点）。3 轮 × 10 次连发，**原文一致率三轮都是 10%**（`distinct = 10/10`），带不带 temperature **没有可测差别**；而请求体**确实带了** temperature（`model/client.py:208`，缺省 `model/config.py:60 = 0.0`）→ **网关忽略 temperature**。落点一致率（`point_consistency.modal_rate`）三轮是 0.2 / 0.1 / 0.1 —— **与原文一致率不是一个量，不许混着报**。→ ② 的「π_d 降到 ≤10%」**在本端点拿不到** |
| 「**2048 宽是安全的**（服务端会透传）」 | **被证伪**。2048×1280 期望 3404 / 实测 **3242**（差 −162）；2044×1260 期望 3285 / 实测 **3242**（差 −45）→ **服务端宽度上限 ≈2016 px**，那两档的坐标系不可信 |
| 「**2016 档能把 icon 中位推过 16 px**」 | **部分证伪**（按验收线的判读口径）。探针**通过**（3242 vs 3240，差 +2 = 网关开销，扣除后差 0）；**但评测台口径的 icon 中位是 15.75 px（n=87），差 0.25 px 没过 ≥16**（全量口径 18.38 px（n=604）过了 —— **分母不同，两个口径不可混用**）→ 本轮按拍板规则**退回 1536×960** |
| 「**`dist_to_bbox` 能复现「8.9 px 与 226 px 两峰」**」 | **口径错**。`dist_to_bbox` 是到 GT **框**的距离（框内 = 0）→ 命中 ⟺ 距离 0，「找对」子集 p50 **恒为 0**；8.9 px 是真机 e2e 的「到控件**中心**」误差。**两个量不许混用** |
| 「**精定位只会帮忙，不会弄坏已对的**」 | **被证伪**（本轮实测）。粗定位命中的 75 条里，精定位**弄坏 11 条**（−14.67 pp）—— 偏离判据降级为记录器后**没有护栏**了。这是 ④ 明写的代价，但值得单独记账 |
| 「**分组 K 折能选出稳定的阈值**」 | **被证伪**（数据决定的）：6 次会话里 consistency **183/183 不可判定**（门控已是记录器），偏离容差只影响「门控关」那一臂而调参规则按「门控开」的下界选 → **5/5 折都选 ∞**（中位数 1e9 px、极差 0）。K 折的净贡献是**暴露**了这一点 |
| 「**服务端的图像 token 就是 `ceil(W/28)×ceil(H/28)`**」 | **不严格成立**：本机端点**每张图固定多记 2 个 token**（网关记账，实测 0~2 抖动）。探针先量出这个开销再比，否则会误判成「服务端缩了图」 |
| 「**2016 档分数更高 = 档位更好**」 | **不成立**（本轮）。2016 档整跑 64.48% vs 1536 档 55.74%，但**两跑批的粗定位回答各自重抽**（图变了 → 回答必然不同）→ **档位与模型随机性没有解耦**；可比的只有同一跑批内的配对结论（2016 档内：精定位净胜 +22.95 pp，Δ（右−左）[−30.05, −15.85]，mid-p 1.82e-09） |

---

## 六、还剩什么 / 下一步从哪接

### 6.1 本轮发现的两个**新风险**（下一轮要先处理）

1. **负出口会制造假否定**：wave1c 的 W-1.2 粗定位答「这张图里没有对应的元素」，而「屏幕」**就在**设置页里 → 这一步直接 `locate_failed`。
   **建议**：负出口只保留在**精定位**那一级；粗定位那一级要么不让模型说「没有」，要么给粗定位的负出口加一层兜底。
2. **精定位没有护栏**：粗定位本来就对的 75 条被**弄坏 11 条**。
   **建议**（下一轮）：把偏离量从「记录器」升级成「**两条都候选** → 用一个**可判**的规则选」（比如 `dist_to_bbox` 更小的那条），
   **不要**回到「超阈值就回落」（那正是本轮拆掉的、43.7% 误伤的那条）。

### 6.2 还剩的验收线缺口（**逐组，含未达成与原因**）

| 组 | 缺口（未达成的原话） | 下一轮从哪接 |
|---|---|---|
| **B** | **36 px 近失带不在空白区**：misses 81 条里 18 条（22.2%）落在带内 → 这条带是「牺牲精度换召回」的折中 | 要么换带宽（用 `dist_to_bbox` 的两峰位置定，而不是 24 CSS px 的外部锚点），要么明确它是「只记档不判死」并接受 22.2% 的捞回率 |
| **E** | **阈值取值未达成**（5 折全选 ∞）、**可迁移性未达成**（K 折输入是旧跑批 `g1-eval-330`、`replay_check.consistent = false`、全量 1581 确认集那一步没做） | 先补齐 648 张图，再在**本轮 183 条**上重跑分组 K 折（折外选、折内评），最后才谈迁移性 |
| **F** | icon 子集 41.38%（要 ≥45%）、icon 中位 12.0 px（评测台口径 n=87）/ 14.00 px（全量 n=604）（要 ≥16 px）、**编号分支白进未做**（入口条件只有 `if candidates:`） | ① icon 密度：2016 档已量过（15.75 px，仍差 0.25 px），**要采纳它得先做同批 1536 全新实跑做对照**（解耦随机性，约 35 分钟 / 417 次请求），并接受 token +41%；② 编号分支收紧是**独立的一行验收线**（见 6.3 待拍板） |
| **D** | 假命中 20 条（要 0） | 精定位的「没找到」逃生口本轮被用了 25 次；剩下 20 次模型照样给了点 → 要在**提示词/问法**上加强（不是加判据） |
| **④** | **回落率 14.2%**（25/176，要 ≤10%） | 2016 档实测 6.25%（11/176）⇒ 与档位有关；若采纳 2016 档，这条顺带达标 |
| **G** | wave1 0/5、wave2 3/7；**视觉步骤可判率 0/2**（四档分类率才是 100%） | 卡在**环境/树**：UWP 树读不到文本、浏览器没登录（C-1/C-2）、浏览器通道没有滚动动作（C-3，M13 未实现）。要再往上走需要**有真值的控件框**（W-1 的「系统」/「屏幕」的 bbox） |
| **H** | **费用没折成钱**；生产档（1536）的**评测负载延迟本轮未实测**（那一跑批是回放） | 费用要**端点计价表**（本轮只报 token 与延迟）；延迟要在**下一轮实跑**时按评测负载单独落一份日志 |
| **⑨** | 全量 1581 条那一跑没做；**分组 K 折的输入是旧跑批** | 先补图（缺 648 张），再跑 `--full` / `--confirmation-split` |
| **A** | — | 7 次会话、图像路径 6 次可用（≥5 ✓）；要更强证据需补齐缺失图后的确认集 |

### 6.3 需要人拍板（本轮不自行决定）

| # | 事项 | 为什么必须由人定 |
|---|---|---|
| 1 | **编号分支白进**（plan:113）：本轮是**放弃**还是**补做**？ | 它是 F 组的一行带数字目标的验收线，但**不在 10 条施工清单里**；入口条件收紧会改变调用分布，属于行为改动 |
| 2 | **探针自检**（plan:76 / plan:231）：规格要求改 `src/minicode/verify/test_01_text_and_probe.py:124`，而 `verify/` 是**冻结目录** | **规格与冻结纪律冲突** —— 上一版报告在两处给了相反结论，本文件统一判「未达成」并停在这里等拍板 |
| 3 | **对外报哪个跑批的重加权值**：本文件报本轮的 **54.27%**（bootstrap CI [44.57%, 63.45%]） | 口径决策（重加权属于「标定/坐标契约」那一类，按 `AGENTS.md` 须人拍板）。方法已用旧基线复现验证（44.81% → 42.47% ≈ 文档里的 42.5%） |
| 4 | **H 组的端点计价表** | 折钱需要端点价格，仓库里没有 |
| 5 | 是否采纳 **2016×1260** 档（分数更高但 icon 中位仍差 0.25 px、token +41%） | 这是一次「拿 token 预算换 icon 密度」的取舍，且档位效果与随机性尚未解耦 |

### 6.4 与规格的分叉（`AGENTS.md`：改验收标准只在 agent 停下之后由人做）

`docs/spec.md` §4 与 `docs/00-现状.md` §3 仍写着「生产默认 **192×192**」「偏离 crop 窗口中心 **> 50 px** 就回落」
—— 这两处**正是施工清单第 ④⑤ 条改掉的行为**。**实现已按施工清单走，规格文档同步要由人统一改**。
另：`docs/tickets/README.md` 的待拍板清单里，**CC-1.A 的措辞**（能力表判 `sparse` 是应用现状，不是能力缺陷）
本轮**已按拍板改实现**（`e2e/wave2.py`），**工单里的 AC 文本仍需人同步**。

---

## 七、接口破坏（本轮 · 含**语义反转**类）

> 公开接口变了 → **下游要改 import / 改调用方式**。仓内调用点已同步改完；仓外调用者请按下表改。

| 改了什么 | 破坏面 | 换成 |
|---|---|---|
| **⚠️ `minicode.calib.calibrate`**：`estimate_model_input_from_tokens(observed, text, aspect_ratio)` → **`(observed, text, declared_size)`** | **第三位参数的语义变了**（float → tuple）→ 旧调用点会 `TypeError`；**更危险的是返回值语义整个反转**：旧 = 「服务端大概缩到多大」的盒子；新 = 相等 → 声明尺寸、不等 → `None`。**同名、同参数个数、结果不同** → 老代码不会崩，会安静地拿到不同的数 | `from minicode.calib.calibrate import estimate_model_input_from_tokens`（新契约：相等断言口径）；要旧的盒子语义请用 **`diagnose_server_model_input_from_tokens`**（新增，已进 `minicode.calib.__all__`） |
| `minicode.eval.stats`：`mde(n, discordance, delta)` → **`mde(n, discordance)`** | 第三个位置参数**被移除** → 再传第三个参数抛 `TypeError` | 「这个样本量够不够判出 Δ」由调用点自己比：`Δ ≥ mde(n, π_d)` 即够 |
| `minicode.eval.holdout`：`HalfResult` → **`FoldResult`**、`evaluate_half()` → **`evaluate_fold()`** | 这两个名字**不再存在** | `from minicode.eval.holdout import FoldResult, evaluate_fold` |
| `minicode.eval.holdout`：原 `FoldResult`（一折 = 训练折选阈 + 留出折评）→ **`FoldOutcome`** | 同名 `FoldResult` 的**含义变了**（现在指「留出折上的**评估结果**」） | `from minicode.eval.holdout import FoldOutcome` |
| 报告键 `comparison.paired.*.strata.coarse_available/coarse_unavailable` | 键名变了（读旧报告的下游脚本要改） | `coarse_hit` / `coarse_miss` |
| **新增**配对输出键（**只增不删**）：`mcnemar_exact_p` / `mcnemar_chisq_cc_p` / `mde_pp` / `delta_at_or_above_mde` | 无破坏 | — |
| 记忆缓存 JSONL 条目 | 多一个 `stage` 字段（旧条目读回记 `unknown`） | 落盘缓存 |

---

## 八、产物索引

| 产物 | 内容 |
|---|---|
| `runs/g2-eval-final/sspro-report.json` | **头条**：183 条三臂 + 埋点 + 配对统计 + 近失带校准 + `model_input_target_sizes` |
| `runs/g2-eval-crop192/sspro-report.json` | ⑤ 的**配对**对照跑批（crop 换成 192 的那一档） |
| `runs/g2-eval-crop192/paired-recheck.json` | ⑤ 的**离线重算**（零模型调用）：两档配对 mid-p McNemar 重算 + 与产物对账（`matches_artifact = true`）+ 粗定位一致率 176/183 |
| `runs/g3-eval-2016/sspro-report.json` | **2016×1260 档的整跑**（183 条，全新实跑 417 次请求）：总体 118/183 = 64.48%、icon 中位 15.75 px、回落 6.25%、每目标 token 4989.23 → **本轮未采纳** |
| `runs/g3-eval-2016/comparison-vs-g2.json`、`g2-vs-g3-paired.json` | 两档并列对比 + 同批样本配对统计（⚠️ **两轮回答不同 → 档位与随机性未解耦**） |
| `runs/g3-eval-2016/model-requests.jsonl` | 2016 档的 417 条真实请求（延迟中位 10.02 s / n=416 HTTP 200；1 条 180 s 超时） |
| `runs/g1-eval-330/sspro-report.json` | 旧基线跑批（44.81% / coarse_only 76） |
| `runs/m3-crop-geometry/crop-geometry.json` | ③ 的几何表（192→42.1%、512→71.0%、0.5×当前图→84.2%） |
| `runs/m6-token-probe-2048exact/token-probe.json` | ⑥ 的 2048 档探针（期望 3404 / 实测 3242 / 差 −162 → **不过**） |
| `runs/m6-token-probe-final*/token-probe.json` | ⑥ 的探针族（2044 档差 −45、1536×960 期望 1925 / 实测 1927 ✓、**服务端上限 ≈2016 px**） |
| `runs/m6-token-probe-2016/token-probe.json` | ⑥ 的 **2016×1260 档**探针（本轮新跑）：期望 3240 / 实测 3242（差 +2 = 网关固定开销）→ **该档透传** |
| `runs/m2-temperature-probe/probe.json` / `probe-no-temperature.json` / `probe-text.json` | ② 的 10 次连发探针（**原文一致率三轮都是 10%**；落点一致率 0.2 / 0.1 / 0.1） |
| `runs/v1-calib/s0..s5/calibration-records.jsonl` | A 组的 6 次独立标定会话（s4 = SSL 传输层失败） |
| `runs/v1-calib/s4b/calibration-records.jsonl` | A 组的**补跑会话**（图像路径 available=True、hit_rate 1.00、median 2.83 px、p90 9.67 px） |
| `runs/v1-calib/session-table.md` / `.json` | A 组 7 次会话的对照表（零模型调用） |
| `runs/v1-wave1c/e2e-report.json`、`runs/v1-wave2/e2e-report.json` | G 组的两波（落点三档 + 口径版本 + `visual_step_decidable_rate`） |
| `runs/v1-wave1b-console.log` | 上一轮 wave1b 的落点记录（**到中心 13.9 px 出自这里**；本文件只引 wave1c 的 27.8 px） |
| `runs/m8-landing-rule/landing-rule-selftest.json` | ⑧ 的判据自检（合成样例，**不是真机实测**） |
| `runs/m9-kfold/kfold.json` | ⑨ 的分组 K 折（各折阈值分布 + 留出集命中率 + `replay_check`） |
| `runs/m17-icon-size/icon-size.json` | F 组的 icon 尺寸（**n=604 全量 icon** + n=977 text，**5 个 tier**：2048 / 2016 / 1536 / 1280×800 / 1280×800 legacy 整数因子） |
| `runs/round3-recheck/headline-reweight.json` | 头条的**重加权值与区间**（本轮算：55.74% → **54.27%**，bootstrap CI [44.57%, 63.45%]；同一方法复现旧基线 42.47%） |
| `runs/round3-recheck/recheck.json` | 两遍复核点名的几处的独立核对（CAD 命中数、三处延迟的来源与分布、无文本率直读、解析失败影响） |

---

## 九、核验记录

```powershell
$env:PYTHONPATH="src"
python -m pytest -q      # 1243 passed, 253 subtests passed in 54.48s
python -m ruff check .   # All checks passed!
```

* **最终核验（本轮收尾实跑）**：`python -m pytest -q` → **1243 passed, 253 subtests passed**（54.48 s）；
  `python -m ruff check .` → **All checks passed!**
  （基线 1227 passed → 本轮 A4/A5 新增 16 个用例；第三遍复核独立复现过同一基线。）
* **两个冻结目录** `src/minicode/coord` / `src/minicode/exec` / `src/minicode/verify`：
  `git diff 3acd5e0...HEAD --stat -- src/minicode/coord src/minicode/exec src/minicode/verify` → **空**（本轮未改）。
* **两遍复核**：第一遍 `ralph-round1-review.md`（含 Lead 抽验与**时点订正**）；第二/第三遍 `ralph-round2-review.md`
  （**只读**，未改实现、未 commit/push、未执行任何 gh）。
* **本文件里的每个数**都能在 `runs/` 的产物里对上；引用纪律自检：
  **8 类禁引数零出现**；本文件引用的 **25 条字面 `runs/` 路径全部存在**（另 2 条是区间/通配写法：
  `runs/v1-calib/s0..s5/…` 与 `runs/m6-token-probe-final*/…`）。

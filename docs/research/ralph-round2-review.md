# ralph 第二遍对抗式复核 · 结论落盘

> **本文件含两遍**：第二遍（§一~§八）+ **第三遍（§九，本轮 Ralph round 2/2 收尾 worker 的独立复核）**。
> **遍次**：Ralph round 2 / 2（第一遍结论存档在 `docs/research/ralph-round1-review.md`）。
> **复核对象**：`docs/research/vision-final-plan.md` 的「施工清单 10 条」+「验收线 8 组」，
> 对照本轮实现（`git diff 3acd5e0...HEAD`）与实测产物（`runs/`）。
> **被交账的报告**：`docs/research/vision-closure-report.md`（HEAD = `7ae9823`）。
> **硬约束遵守**：**只读**——本遍未改任何实现、未 commit、未 push、未执行任何 `gh`；
> 三个冻结目录 `src/minicode/coord` / `src/minicode/exec` / `src/minicode/verify`
> 的 `git diff 3acd5e0...HEAD --stat` **为空**（已核）。**唯一写入的文件就是本文件。**
> **核验环境**：Windows / PowerShell；`$env:PYTHONPATH="src"`；pytest 186 passed（定向四套）、
> ruff `All checks passed!`。

---

## 一、一句话结论

**第一遍点名的 6 类问题，代码层真修了 4 类（④⑤⑥ + ③ 的 K 折口径已披露）、报告层修全了 8 处虚报 / 9 处口径 / 7 处引用破口；但本遍仍查出 1 处数值错、1 处「验收线未达成却判达成」、1 处规格项未做却判 ✅、1 处公开接口破坏漏报、1 处 Δ 口径自相矛盾、2 处延迟/头条口径混读 —— 全部发生在「报告写完了、代码还在改」的缝里。**
**另外：第一遍那唯一一处被 Lead 判成「误报」的条目，Lead 判错了 —— 那不是误读，是时间差。**

---

## 二、第一遍那 6 条，本遍逐条核（「真修了吗」）

### ① 报告 8 处虚报 —— **8/8 已订正**（产物逐条对得上）

| # | 第一遍点的 | 报告现在的写法（file:line） | 产物核对 | 判定 |
|---|---|---|---|---|
| 1 | A 组写成「0.00 px」 | `:54`「**2.87 px ≤ 4 px ✓（达成）**」+ 指向 `denominator_pair_divergence_px` + 注明 0.00 只来自 SSL 失败的 s4 | `runs/v1-calib/s0/calibration-records.jsonl` → `denominator_pair_divergence_px = 2.8676273030872284`；`runs/v1-calib/session-table.md:5-18` 七行同值 | ✅ |
| 2 | E 组漏「阈值取值」「可迁移性」两行 | `:129`「**阈值取值** … **未达成**：5 折全部选中 ∞」；`:130`「**可迁移性** … **未达成**」+ 点名 K 折输入是旧跑批 + `replay_check` 不自洽 | `runs/m9-kfold/kfold.json` → `threshold_distribution.tolerance_px.values = [1e9 ×5]`、`input.recorded_run = runs\\g1-eval-330\\sspro-report.json`、`replay_check = {on_demand_matches:165, always_matches:183, consistent:false}` | ✅ |
| 3 | G 组「可判率 100%」 | `:161`「**四档分类率 100%** … ⚠️ **但产物字段 `visual_step_decidable_rate = 0.0`（判出档 0/2）** → 验收线要的「可判率 100%」**未达成**」 | `runs/v1-wave1c/e2e-report.json` → `landing = {tiers:{hit:2,near_miss:0,wrong:0,undecidable:2}, visual_steps:2, visual_steps_decided:0, visual_step_decidable_rate:0.0}` | ✅ |
| 4 | ④「回落率 ≤10%」被写成达成 | `:203`「**「回落率 ≤10%」未达成**：回落 25 次 / **176 次开窗**（分母 = `refine_enabled`，不是 183）= **14.2%**」 | `runs/g2-eval-final/sspro-report.json` → `comparison.always.gate = {fallbacks:25, refine_enabled:176, fallback_rate:0.14204545454545456}` | ✅ |
| 5 | B 组 36 px 空白区未答 | `:70` + `:77-81`「**未达成：36 px 不在空白区**」+ 产物 `band_36px = {hits_failing_band:0, misses_rescued:18}` + misses=81 → 22.2% | `runs/g2-eval-final/sspro-report.json` → `near_miss_calibration = {hits:102, misses:81, p50_by_subset:{hit:0, miss:519.2}, band_36px:{tau_px:36, hits_failing_band:0, misses_rescued:18}}` | ✅ |
| 6 | F 组 icon 14.00 px | `:138`「**12.0 px**（**n=87** …）**并列 14.00 px**（**n=604**，全量 …）」+ 目标 ≥16 px **未达成** | `runs/g2-eval-final/sspro-report.json` → `model_input_target_sizes.icon = {samples:87, median_px:12.0, below_16px:64, below_16px_rate:0.7356}`；`runs/m17-icon-size/icon-size.json` → `tiers["1536x960"].icon = {samples:604, median_px:14.0, below_16px:379, below_16px_ratio:0.627483}` | ✅ |
| 7 | ⑤ 产物索引指向不存在的路径 | `:286-287` + `:304` 列 `runs/g2-eval-crop192/sspro-report.json` + `paired-recheck.json` | 两个文件都在（763576 / 3344 bytes）。**但这条的「不存在」在第一遍当时是真的 —— 见 §四 专项** | ✅（路径已补） |
| 8 | kfold 的 `consistent=false` 与「输入是旧跑批」没写 | `:130` 两句都写了 | 同上第 2 行 | ✅ |

### ② 9 处口径 —— **7 处真统一，2 处只统一了一半**

| # | 第一遍点的 | 现在的写法 | 核对 | 判定 |
|---|---|---|---|---|
| 1 | Δ 方向与口径 | `:20-24` 立规矩 + `:106` 的 CI 标了「右臂 − 左臂」 | **`:106` 同格里「下界 > 0」与它自己标的口径矛盾；`:208`（⑨ 行）根本不标口径、且符号相反** | ⚠️ **半统一 → 见 N7** |
| 2 | image token 拆账 | `:181`「3539.27 = **1788.39** + **1750.89**」 | 本遍独立重算：`outcomes.coarse_only` 求和 ÷183 = **1788.387978**；`outcomes.always` = **3539.273224**；差 = **1750.885246** | ✅ |
| 3 | 回落率分母 | `:203` 25/176 = 14.2%（并标 183 是样本数、176 是开窗数） | 见上 | ✅ |
| 4 | 延迟 16.70 s | `:183`「中位 **16.70 s**（n=40 条真正发出的请求）」 | **数对、口径标签错**：那 40 行是**标定探针**请求，不是评测负载 → 见 **N8** | ⚠️ **半统一** |
| 5 | 原文一致率 10% | `:33` + `:217` 三轮都是 10%；落点一致率 0.2/0.1/0.1 单独说 | `runs/m2-temperature-probe/probe.json` → `text_consistency.modal_rate = 0.1`、`point_consistency.modal_rate = 0.2`；`probe-no-temperature.json` 0.1/0.1；`probe-text.json` 0.1/0.1 | ✅ |
| 6 | 含住率第二口径 | `:92-98` 写清 `crop_geometry.py:228 target_radius_px=0.0` vs `channel.py:677` 传粗框半径 | 两处行号本遍逐行核对通过 | ✅ |
| 7 | 3765 与本轮几何不同源 | `:185-192` 明写 3765 = 2048 档（3404+361），本轮是 1536×960 + 自适应 crop | 与 `runs/m6-token-probe-2048exact` / `m6-token-probe-final-06` 一致 | ✅ |
| 8 | 「≈15 s/次」「183 条三臂约 70 分钟墙钟」无依据 | `:183` 明写「**已删**」 | 报告全文再无该说法 | ✅ |
| 9 | 18.67 px 出处 | `:153`「**18.67 px 是 2048 档**（n=604，<16 px 196/604 = 32.5%）—— 不是 2016 档的数」 | `runs/m17-icon-size/icon-size.json` → `tiers["2048x1280"].icon = {median_px:18.6667, below_16px:196, below_16px_ratio:0.324503}` | ✅ |

### ③ 7 处引用纪律破口 —— **7/7 已修**（本遍独立复核，不是照抄）

1. **产物路径**：本遍把报告里出现的**全部 22 条 `runs/…` 路径**跑了一遍 → 全部存在（唯一「不存在」的是 `runs/v1-calib/s0..s5/…` 这个区间写法，不是字面路径）。✅
2. **18.67 px 归回 2048 档** ✅（见上表第 9 行）。
3. **icon 尺寸出处** 从 `.scratch/icon_size_report.py` 换成 `runs/m17-icon-size/icon-size.json`（n=604，四个整屏档位）✅。
4. **temperature 出处** 改成 `src/minicode/model/client.py:208`（`body["temperature"] = temperature`）+ `src/minicode/model/config.py:60`（`DEFAULT_TEMPERATURE = 0.0`）—— **两个行号本遍逐行核对通过** ✅。
5. **`divergences_px` 里没有那个数** → 改指 `denominator_pair_divergence_px`，并注明 `divergences_px` 数组里存的是 `Infinity` ✅。
6. **头条缺 Wilson CI 与重加权** → `:16`「Wilson 95% CI [48.50%, 62.74%]」与 `:26`「重加权 42.5%（CI [35.5%, 49.7%]）」已补；CI 与产物 `comparison.always.wilson_left = [0.48496744391744306, 0.6274273111468119]` 一致 ✅。
7. **8 类禁引数**：本遍用 26 个模式独立 grep 全文 → 只命中 `4\.2`（8 处），逐处看全部是本仓自己的数（`84.2%`、`14.2%`、`29.70 px`…），**不是** UI-Zoomer 的 `+4.2%`。**零违规** ✅。

### ④ `ground/prompt.py::parse_optional_point` 截断检查 —— **真加上了，且有用例** ✅

- 实现：`src/minicode/ground/prompt.py:453-458` —— 先取答案行（无则全文）→ `is_truncated(segment)` → 抛 `GroundingParseError`（**不返回 None**：None 的语义是「负出口」，截断是「没读完」，两者被刻意分开）。
- 调用链真的接住了：`src/minicode/ground/channel.py:690-704` 捕获 `GroundingParseError` → `fallback=True`、`fallback_cause=no_output`、落点 = 粗定位点、`landing_source=coarse`。
- 用例（`src/minicode/ground/test_ground_two_stage.py`）：`:388 test_a_truncated_refine_answer_falls_back_to_coarse`、`:594 test_a_truncated_answer_is_not_rescued_into_a_point`、`:605 / :617 / :626` 粗定位答案行非 JSON → 回落扫全文 / 答案行有 JSON 仍只读那一行。**本遍 pytest：186 passed, 5 subtests passed。**
- ⚠️ 一处**事实性瑕疵**（不影响功能）：commit `1db1622` 的 message 写「调用方 `ground/channel.py::_point_path` 捕获该异常 → …」，但**该 commit 的 diff 里根本没有 `channel.py`**（只改了 `prompt.py` + 测试）。实际能接住，是因为 `_ask` 本来就抛 `GroundingParseError`、而 `channel.py:690` 的既有 `except` 恰好覆盖到了新抛点。**结论成立、commit message 与 diff 不符** → 见 N10。

### ⑤ `estimate_model_input_from_tokens` 收紧 + 接进生产链 —— **真做了** ✅

- 收紧：`src/minicode/calib/calibrate.py:364-397` —— `estimate_model_input_from_tokens` 改成**委托** `assert_server_kept_image_tokens` 的相等断言口径：相等 → 返回声明尺寸；不等 → `None`（坐标系不可信）；量不出 → `None`。
- 旧的 ±28 px 盒子降级为诊断：`src/minicode/calib/calibrate.py:287 diagnose_server_model_input_from_tokens`，并在 `src/minicode/calib/__init__.py` 的 `__all__` 里导出。
- **生产链接线**（这是第一遍说「只接进评测台、真机跑时不会被拦」的那半句）：`src/minicode/e2e/visual.py:110 server_image_evidence_from_log`（一次测量 → 盒子 + 相等断言两个结论）→ `:362 admission_port` 外套 `_CoordinateSystemGate` → `:376 / :404` `server_kept_size is False` → 返回显式失败的 `_UntrustedCoordinateSystemLocator` / `_UntrustedZoomLocator`（不按声明尺寸换算）→ `:469 / :526-531` 落 `image_token_assertion` 与 `coordinate_system_trusted`。
- ⚠️ **但报告完全没记这条修复**（见 N5 / N6）。

### ⑥ χ² + 连续性校正 + `mde()` 接线 —— **真做了** ✅

- `src/minicode/eval/stats.py:73 mcnemar_midp` / `:105 mcnemar_exact`（普通精确二项，双侧） / `:135 mcnemar_chisq_cc`（χ² + Yates 校正，erfc 上尾） / `:168 wilson_ci` / `:204 paired_bootstrap_delta` / `:259 mde`。
- 接线：`src/minicode/eval/sspro.py:1187 mde_pp = mde(n, discordance)`、`:1204-1212` 落 `mcnemar_exact_p` / `mcnemar_chisq_cc_p` / `mde_pp` / `delta_at_or_above_mde`（已有键名一个没删）。
- 产物里读得到：`runs/g3-eval-2016/sspro-report.json` → `mcnemar_midp_p = 1.8228e-09`、`mcnemar_exact_p = 3.2565e-09`、`mcnemar_chisq_cc_p = 2.4136e-08`、`mde_pp = 11.2500`、`delta_at_or_above_mde = true`。
- 第一遍说「`plan:246` 的 6.55→2.23pp 在任何产物里读不到」—— 本遍用新函数实算：`mde(183, 0.10) = 6.549`（= plan 的 6.55 pp ✅）、`mde(1581, 0.10) = 2.228`（= plan 的 2.23 pp ✅）。
- 本地三口径自洽（b=38, c=11）：mid-p `9.0215e-05`（与产物一致）、exact `1.4197e-04`、χ²+CC `2.0378e-04`。
- ⚠️ **但报告没交账**（见 N5）。

---

## 三、专项：第一遍那 1 处「误报」，是不是读错了？

**结论：不是读错。第一遍说的「`runs/g2-eval-crop192/sspro-report.json` 不存在」在它说的那个时刻是**真的**；把它判成「误报」的 Lead 抽验（`docs/research/ralph-round1-review.md:22`）本身是错的——那是拿 16:20 的报告去对 16:34 之后的文件系统。**

### 时间线（全部来自文件 mtime + git 时间戳 + 产物内容）

| 时刻 | 事件 | 证据 |
|---|---|---|
| 05:39:33 | `runs/g1-eval-330/sspro-report.json` 落盘（旧跑批，44.81% 基线） | 文件 LastWriteTime |
| **16:16:48** | `runs/g2-eval-crop192/model-requests.jsonl` **创建** → 这一轮 crop192 评测**开始跑** | 文件 CreationTime |
| **16:20:55** | commit `7e74d33`：**收口报告首次提交**，`docs/research/vision-closure-report.md:234` 索引了 `runs/g2-eval-crop192/sspro-report.json` | `git log --format=%ad --date=iso-strict` |
| 16:11:40 | （对照）`runs/g2-eval-final/sspro-report.json` 落盘 —— 头条产物**已经存在** | 文件 LastWriteTime |
| **16:34:02** | crop192 的请求日志**最后一次写入**（177 行写完） | 文件 LastWriteTime |
| **16:34:04** | crop192 的 `sspro-report.json`（763576 B）+ `sspro-table.md` **才生成** | 文件 CreationTime；控制台末行「记录已写入：runs\g2-eval-crop192\sspro-report.json」 |
| 17:40:56 | commit `1945bd0`：第一遍结论存档 + Lead 抽验（§一 #1 判「复核误报」） | git 时间戳 |
| 17:59:28 | `runs/g2-eval-crop192/paired-recheck.json` 生成（⑤ 的离线重算） | 文件 LastWriteTime |

### 三条推论

1. **报告引用的那条路径，在报告提交后 13 分 9 秒里都是不存在的。** 第一遍复核的对象是 `7e74d33` 那一版（第一遍逐字引用了它的 `:234`、`:153`、`:45`、`:60`、`:93-101`、`:108`、`:124`、`:140`、`:142`、`:151`、`:167`——本遍用 `git show 7e74d33:docs/research/vision-closure-report.md` 逐条比对，**行号与内容全部命中这一版**，例如该版 `:45` 正是「**0.00 px ≤ 4 px**（6 次会话全部）」、`:108` 正是「icon … **14.00 px** … `.scratch/icon_size_rep…`」、`:167` 正是「上线字节里确实有 `temperature: 0.0`」），所以「报告索引了一条不存在的产物路径」在那个版本上是**实打实的引用纪律破口**，第一遍报得对。
2. **第一遍观察到的目录状态与「跑批进行中」完全吻合**：`runs/g2-eval-crop192/` 里当时只有 `model-requests.jsonl`（请求**边跑边写**），`sspro-report.json` 是这一轮**最后一步**才写的（控制台末行就是那句「记录已写入」）。177 行 / image_tokens 中位 49 —— 这两个数与最终文件**完全一致**，说明第一遍是在这一轮的最后时刻读的日志，**读到的是收尾前的目录状态**。
3. **Lead 的抽验跑在 16:34:04 之后**，看到的是一个已经收工跑批的目录 → 于是把「当时不存在」判成「误报」。

### 残留不确定性（如实标出）

第一遍读日志的时刻不晚于 16:34:02，而报告文件写入在 16:34:04 —— **中间只有 2 秒窗口**。严格地说，存在「第一遍在 16:34:02~16:34:04 之间列目录、之后才读日志」这一种可能；那种情况下它仍不是「读错文件内容」，而是**在跑批收尾的那 2 秒里做的目录快照**。两种解释都指向同一结论：**第一遍那句话对它所审的那一版报告成立，Lead 的「误报」判定不成立。**

### 连带的两处错

- `docs/research/ralph-round1-review.md:22` 的「❌ **复核误报**」这条判定本身是错的，且它现在正在被第二遍继承。
- 同一文件 `:29`「**结论：5/6 正确，1 处误报。这份复核可信度足够高**」里的「1 处误报」要改成「**6/6 正确**（第 1 条的正确性建立在「按 16:20 的报告版本 + 跑批进行中的目录快照」这两个时点限定上，Lead 的抽验没有带这两个限定）」。
- 同一文件 `:16` 的「Lead 独立核对，2026-10-22 17:5x」与该文件自身的提交时间（17:40:56）矛盾 —— 抽验发生在存档提交**之前**，时间戳写错了。

---

## 四、本遍新发现（4 类）

> 每条 = 「文件:行号 + 证据 + 规格依据」。

### 类别 2 · 验收线未达成却被写成达成 / 数写错

#### N1 【数值错】⑥ 行把 2048 档的 token 差写成 −45，实际是 −162

- **位置**：`docs/research/vision-closure-report.md:205` —— 「**探针先做了**：2048 档实测图像 token **3242 ≠ 3404（差 −45）**」
- **证据**：
  - `runs/m6-token-probe-2048exact/token-probe.json` → `results[0]` = `sent_size:[2048,1280]`、`expected_image_tokens: 3404`、`observed_image_tokens: 3242`、**`overhead_tokens: -162`**。
  - `runs/m6-token-probe-final/token-probe.json` → 2044×1260 那一行 `difference: -45`（3285 − server_kept 3240）。
  - 即：**−45 是 2044×1260 那一档的差，被安到了 2048×1280 头上**；2048 档的真差是 **3404 − 3242 = −162**。
- **规格依据**：`docs/research/vision-final-plan.md:208`（⑥ 的「做什么」①：图像 token 必须**恰为** 74×46 = 3404，不等 ⇒ 服务端缩了）；同 `:332`（「整屏 2048×1280 是否被服务端透传 … 本端点只实测过 2560 宽 → 被缩到约 2048 宽，**从未探过 2048 档**」）。
- **严重度**：低（结论方向不变：探针不过 → 退 1536×960 ✓）。但它是**交账表里的数**，且报告 §三 还在为 2048 档的差做「订正」叙述 —— 同一节里两个数不一致。

#### N2 【验收线未达成却判达成】E 组「回落目标 = 精定位点」在产物里是 25 次粗定位

- **位置**：`docs/research/vision-closure-report.md:128` —— 「回落目标 | 粗定位点 | **精定位点**（判据不改落点 → 不存在「降级到更差的一级」）| 同上」
- **证据**：`runs/g2-eval-final/sspro-report.json` → `comparison.always.telemetry.landing_source_distribution = {"coarse": 25, "refined": 151, "whole_screen": 6, "none": 1}` → **25 次回落的最终落点来源仍是 `coarse`（粗定位那一级）**；同报告 `:108` 自己写「硬失败回落的原因 … **只有 `no_output` 25**」。
- **规格依据**：`docs/research/vision-final-plan.md:102`（E 组「回落目标 → **精定位点**（回落时不得降级到更差的一级）」）。
- **严重度**：中。严格说，25 次 `no_output` 本来就没有精定位点可回落，但**验收线那句话（「不得降级到更差的一级」）在产物里被这 25 条直接否掉**；报告却用「判据不改落点 → 不存在降级」把它记成达成，**回避了真正发生的那 25 次降级**。正确写法应是：「偏离回落已消除（0 次）；硬失败回落 25 次仍落在粗定位那一级（无精定位点可回落）」。

#### N3 【同一规格项两处结论相反】探针自检：B 组写「未改」，⑧ 行写「全部落地 ✅」

- **位置**：`docs/research/vision-closure-report.md:69`（B 组「探针自检 … **未改**（`src/minicode/verify/` 是冻结目录）」）**vs** `:207`（施工清单 ⑧ 行「**全部落地** … ✅（`verify/` 冻结 → 探针 PASS 那条未改，已如实记）」）。
- **证据**：同两个文件（`:69` 与 `:207`）对同一条给出相反结论；`src/minicode/verify/` 的 `git diff 3acd5e0...HEAD --stat` 为空（确实没改）。
- **规格依据**：`docs/research/vision-final-plan.md:76`（B 组「探针自检 … PASS = 全部目标命中」）、`:231`（⑧ 的「改哪些文件」点名 `src/minicode/verify/test_01_text_and_probe.py:124`）、`AGENTS.md`「三个原型已验证、冻结不改」。
- **严重度**：中。规格里这一条**冲突**（plan 要求改冻结目录 vs AGENTS.md 禁止改）——**报告该把它列进 §四「与规格的分叉（待拍板）」而不是两处各写一个结论**。

#### N4 【规格项未做却判 ✅】⑤ 的「crop 扫描改成比例扫描」仍未做

- **位置**：`docs/research/vision-closure-report.md:204` —— ⑤ 行判「**✅**（含住率与配对对照都成立；配对对照的「同一批上游回答」有 7 条例外，已注明）」。
- **证据**：`src/minicode/ground/numbered_bench.py:669` —— `CROP_SCAN_SIZES = (128, 192, 256, 512, 768, 1024, 1280)`，**全是固定像素**；生产在用的「**0.5×当前帧**」那档**不在扫描集合里**。第一遍已点名这条（`docs/research/ralph-round1-review.md:46`：「缺口：⑤ 的 `numbered_bench.py:669` `CROP_SCAN_SIZES` 仍固定 px，生产在用的 0.5×当前帧不在扫描集合」），**本轮未修**，报告里**也没披露**。
- **规格依据**：`docs/research/vision-final-plan.md:195`（⑤ 的「改哪些文件」：`numbered_bench.py`「crop 扫描从固定像素改成**比例扫描**」）。
- **严重度**：中。⑤ 的 ✅ 应改成「⚠️ 部分达成（几何换法与配对对照成立；`numbered_bench` 的比例扫描未做）」。

### 类别 3 · 口径不一致

#### N5 【报告与代码不同步】⑨ 行说「`eval/stats.py` **四个**纯函数」，实际六个；χ²/MDE 没交账

- **位置**：`docs/research/vision-closure-report.md:208`。
- **证据**：`src/minicode/eval/stats.py` 现在有 **6** 个纯函数：`:73 mcnemar_midp`、`:105 mcnemar_exact`、`:135 mcnemar_chisq_cc`、`:168 wilson_ci`、`:204 paired_bootstrap_delta`、`:259 mde`；报告写「四个」。
- **更关键**：⑨ 的验收要求「普通精确二项与 χ²+连续性校正当对照」在报告里**没有任何交账行**；而产物里**已经读得到**（`runs/g3-eval-2016/sspro-report.json` → `mcnemar_exact_p = 3.2565e-09`、`mcnemar_chisq_cc_p = 2.4136e-08`、`mde_pp = 11.25`）。
- **规格依据**：`docs/research/vision-final-plan.md:244`（⑨ 的「做什么」②：检验 = mid-p McNemar，**普通精确二项与 χ²+连续性校正当对照**）、`:246`（预期 MDE 6.55 → 2.23 pp）。
- **严重度**：中。同类问题还有 ⑥ 行没记「相等断言已收紧并接进 `e2e/visual.py` 生产链」——**第一遍的两条 MAJOR 修完之后，报告一个字没提**（读者会以为 ⑥ 的相等断言还只活在评测台里）。

#### N6 【公开接口破坏漏报】§五 自称「都在 `src/minicode/eval/`」，但破坏面最大的一条在 `src/minicode/calib/`

- **位置**：`docs/research/vision-closure-report.md:263-277`（§五 接口破坏）。
- **证据**：commit `3187c02` 改了 **`src/minicode/calib/`** 的公开接口：
  - `src/minicode/calib/calibrate.py:364-397`：`estimate_model_input_from_tokens(observed, text, aspect_ratio)` → **`(observed, text, declared_size)`**，**第三个位置参数换了语义**，而且**返回值语义整个反转**（旧：返回「服务端大概缩到多大」的盒子；新：相等 → 声明尺寸、不等 → `None`）。
  - `src/minicode/calib/calibrate.py:287` 新增 `diagnose_server_model_input_from_tokens`，并已进 `src/minicode/calib/__init__.py` 的 `__all__`。
  - 后果：旧调用点会 `TypeError`（第三参是 float 不是 tuple），**同名函数语义反转更危险** —— 老代码不会崩，会安静地拿到不同的数。
- **规格依据**：报告 §五 自己的契约（「公开接口变了 → 下游要改 import」）；`AGENTS.md`「docs/tickets 是唯一真值来源」要求接口破坏必须显式记录。
- **严重度**：中-高。**改名类破坏好发现，语义反转类破坏不写下来就一定会被下游踩到。**

#### N7 【口径混读】Δ 的方向在同一格里自相矛盾，⑨ 行干脆不标口径

- **位置**：`docs/research/vision-closure-report.md:106`（D 组）与 `:208`（⑨ 行）。
- **证据**：
  - `:106`：「配对 bootstrap **Δ 95% CI [−21.86, −7.65] pp**（产物「右臂 − 左臂」口径，right = coarse_only），**下界 > 0**」—— 在它自己标注的口径下，下界是 **−21.86 < 0**；「下界 > 0」只在**翻转后**的口径成立，而**这一格没有说明翻转**。产物原值：`comparison.paired.always_vs_coarse_only.delta_ci95_pp = [-21.85792349726776, -7.650273224043716]`、`delta_pp = -14.754098360655737`。
  - `:208`：「**Δ 95% CI [+7.65, +21.86] pp，下界 > 0**」—— **不标口径**，且与 `:23` 自己写的「CI 同一口径为 **[−21.86, −7.65] pp**」**符号相反**。
- **规格依据**：`docs/research/vision-final-plan.md:427`（混读禁令 3「口径版本要随数出现」）、`:246`（⑨ 的验收「CI 不跨 0 才写『改进成立』」）；报告 `:24` 自己立的规矩「报告里凡出现 Δ，**必须标明是哪一个口径**」。
- **严重度**：中。数值本身没错，**错在「同一个数在报告里有三种写法、其中一种不标口径、一种和自己的标注打架」**。

#### N8 【口径】H 组的延迟 16.70 s 取自**标定探针**请求，不是评测负载；且与 2016 档的 10.02 s 不同源

- **位置**：`docs/research/vision-closure-report.md:183`（H 组「延迟 / 费用」）与 `:303`（产物索引里的 g3 延迟）。
- **证据**：
  - `runs/g2-eval-final/model-requests.jsonl` 共 40 行，`image_tokens` 分布 = **1705×12 / 2691×10 / 1716×5 / 1925×5 / 880×3 / 2392×3 / 1196×2 …** → 与 `runs/g2-eval-final-console.log:10-21` 的**标定档位**（期望 1820 / 1705 / 1925 / 880 / 1890 …）一一对应 → **这 40 条是标定探针的请求**。中位 `latency_ms = 16845.8` → 报告写的 16.70 s ✓，但**它不是 183 条评测请求的延迟**。
  - 评测负载的真实延迟在别处：`runs/g2-eval-crop192/model-requests.jsonl`（177 条真实精定位请求）**中位 16057 ms**；`runs/g3-eval-2016/model-requests.jsonl` 报告自己写「延迟中位 **10019.9 ms**，n=416」。
- **规格依据**：`docs/research/vision-final-plan.md:130`（H 组「**必须同时报延迟与费用**，不许只报准确率」）、`:335`（「2048 档的成本与延迟 … 延迟/费用未实测」）。
- **严重度**：中。报告把「标定探针延迟」写成「**本轮请求延迟**」并用它给 H 组交账；同一份报告里另一个档位的延迟（10.02 s）来自**评测负载**——**两个数不是同一批请求，不能并排做档位对比**。

#### N9 【口径】头条的「44.81% → 55.74%」是**跨跑批**比较，与报告自己立的纪律冲突

- **位置**：`docs/research/vision-final-plan.md` 无关；**报告** `docs/research/vision-closure-report.md:15-17`（一句话结论）与 `:203`（④ 行「44.81% → 55.74%（n=183，同一批样本）」）。
- **证据**：
  - 44.81% 来自 `runs/g1-eval-330/sspro-report.json`（文件 mtime **05:39:33**，`comparison.always.hits = 82/183`）；
  - 55.74% 来自 `runs/g2-eval-final/sspro-report.json`（mtime **16:11:40**，`comparison.always.hits = 102/183`）→ **两次独立跑批、模型各自重抽**。
  - 报告 `:34-35` 自己写：「模型方差**没被钉住** → **任何跨运行的分数差都混着模型随机性**（本轮的头条数之所以敢报，是因为**两臂共用同一次上游回答**）」—— 这条论证**只覆盖** 55.74% vs 40.98%（同一跑批内），**不覆盖** 44.81% → 55.74%。
- **规格依据**：`docs/research/vision-final-plan.md:244`（「主检验 = **配对**：同批样本、**复用同一次上游回答**」）、`:291`（不一致对占比 π_d 未钉住 → 冻结上游是唯一手段）。
- **严重度**：中。**缓解证据报告没写**：两跑批的 `coarse_only` 臂是 **76/183 vs 75/183**（几乎不动），说明粗定位那一级跨跑批稳定 —— 有了这条锚点，「82 → 102」才读得出是几何改动带来的。这句话该写进报告，而不是留着让读者自己怀疑。

### 类别 4 · 引用纪律违规

#### N10 【引用纪律·commit message 与 diff 不符】`1db1622` 声称改了调用方，实际没改

- **位置**：`docs/research/vision-closure-report.md` 未记；**commit `1db1622` 的 message**：「调用方 `ground/channel.py::_point_path` 捕获该异常 → fallback=True …」。
- **证据**：`git show 1db1622 --stat` → 只有 `src/minicode/ground/prompt.py`（+23/−1）与 `src/minicode/ground/test_ground_two_stage.py`（+59），**没有 `channel.py`**。功能上能接住，是因为 `channel.py:690` 的既有 `except GroundingParseError` 恰好覆盖了新的抛点。
- **规格依据**：报告 `:6` 的引用纪律自述「对外结论只引①本机实测 … 每个数带样本量与判定口径、样本构成说明」—— **提交历史也是引用源**，commit message 里的「改了哪个文件」属于可核事实。
- **严重度**：低-中。功能结论不变，但**这一条正是「实现与规格不符」的典型**：提交说明与 diff 不一致，会让下一个复核者重新查一遍。

### 类别 1 · 实现与规格不符（除上述外，本遍逐条核过、**未发现新的实现偏差**）

已核且**成立**的实现（不是问题，但列出来以免下一遍重查）：

| 规格条 | 实现位置 | 核对结果 |
|---|---|---|
| ① 埋点 8 字段 | `eval/sspro.py` / `ground/channel.py::_plan_fields` / `action/ports.py::GroundingEcho` | 产物 `outcomes.always[0]` 里 8 类字段全在：`crop_contains_target` / `crop_miss_reason` / `deviation_px_crop_center` + `deviation_px_coarse` / `refined_point_px` / `fallback_cause` + `landing_source` / `coarse_bbox_model` + `coarse_point_model` / `dist_to_bbox` / `image_tokens` ✅ |
| ③ 几何脚本 192/512 对上 | `runs/m3-crop-geometry/crop-geometry.json` | 192 → `fully_contains_rate 0.4208` + `missing_target_rate 0.3607`；512 → `0.7104` ✅ 与 `plan:175` 要求一致 |
| ④ 偏离量换口径 + 判据降级 | `ground/channel.py:769-786` | 只落盘 `deviation_c1000`，**不改落点**；`gate.py:230 DEFAULT_DEVIATION_TOLERANCE_PER_MILLE = 260.4` 行号核对通过 ✅ |
| ⑤ crop 几何 | `ground/channel.py:667-678` | 50% 线性 + 512 下限 + 贴边内移 + 1:1 原生 ✅（但 `numbered_bench` 的比例扫描未做 → N4） |
| ⑥ 先探针再改 | `runs/m6-token-probe-*` | 2048 不过 → 退 1536×960 ✅（差值写错 → N1） |
| ⑦ 提示词协议 | `ground/prompt.py` | 负出口 / 答案行优先 / 截断检查 全在 ✅（截断检查 → §二④） |
| ⑧ 三档落点 | `e2e/checks.py::landing_tier` / `landing_in_target_rect` | 波形产物 `landing_error_px_version = rect-distance-v1`、`landing_hit_rule = bbox`、`landing_near_miss_px = 36.0` ✅（探针自检未改 → N3） |
| ⑨ 分组 K 折 | `eval/holdout.py` | `runs/m9-kfold/kfold.json` → 5 折全选 ∞；`input.recorded_run` 是旧跑批（报告已披露 ✅） |
| ⑩ 执行器 | `ground/channel.py` / `e2e` | 负出口不再崩 `box_center`；preflight 异常被兜住 ✅ |

---

## 五、两遍都错 / 被继承下来的错

| # | 错在哪 | 第一遍 | 第二遍 | 处置 |
|---|---|---|---|---|
| **E1** | 「`runs/g2-eval-crop192/sspro-report.json` 不存在」被判成**误报** | 第一遍**说对了**（对它审的那一版报告 + 跑批进行中的目录快照成立） | Lead 抽验（`ralph-round1-review.md:22`）**判错了**（时间差） | **两边都对一半**：报告那条引用在 16:20:55 提交时确实断链；Lead 的抽验要加时点限定才成立。**建议改 `ralph-round1-review.md:22` 与 `:29`**（本遍只读，未改） |
| **E2** | 第一遍的「1 处误报 / 5/6 正确」这个比例 | — | 本遍复核：**6/6 条在它们各自的时点都成立**，比例应改为 6/6 | 同上 |
| **E3** | `ralph-round1-review.md:16` 的抽验时间戳「2026-10-22 17:5x」 | — | 与该文件自身提交时间 17:40:56 矛盾（抽验在存档提交之前） | 低-中，属存档自身的记录错 |
| **E4** | 报告正文对**最后三个 commit 无感知** | — | `1db1622`(18:36) / `3187c02`(18:37) / `7ae9823`(19:29) 全部发生在报告最后一次提交 19:29 之前，但报告正文只反映了前两者的一部分 | → N5 / N6 |

**没有「两遍都错」的实质技术结论**：第一遍 15 条 evidence 里被抽验的 6 条、以及本遍逐条复算的条目，除 E1 的判定归属外**全部成立**；第一遍的 6 条 nextSteps 在代码层**已落地 4 条**（④⑤⑥ + ③ 的旧跑批披露），**未落地 2 条**（⑤ 的 `numbered_bench` 比例扫描 → N4；报告层的完整交账 → N5/N6）。

---

## 六、这一遍没查的（边界，供下一遍接手）

1. **没重跑任何模型调用**（只读约束 + 真机/额度不动）；所有数字均为**对已有产物的独立重算**（`outcomes` 逐条求和、`kfold.json` / `near_miss_calibration` / `model_input_target_sizes` 直接读）。
2. **没核 `runs/` 之外的历史产物**（`g1-eval-330` 之前的 M8~M20 批次），只核了报告引用的那几份。
3. **没审 `src/minicode/action` / `decision` / `exec` 的行为面**（只核了 `ground` / `eval` / `calib` / `e2e` / `model` 这几个本轮改动面）。
4. **没核 `docs/spec.md` / `docs/00-现状.md` 的同步状态**（报告 §四 已把它们列成「与规格的分叉，待人拍板」—— 本遍认同这个处置，不重复列问题）。
5. **只跑了定向四套测试**（ground_two_stage / eval_stats / calib_probe / e2e_visual = 186 passed）；**没跑全量 pytest**（仓库基线 1243 passed 是 `3187c02` 的自述，本遍未独立复现全量）。

---

## 七、给下一轮的建议（按「改一处就少一处破口」排序）

1. **改 N1**（一行数）→ ⑥ 行「差 −45」改「差 −162」。
2. **改 N4 + N5 + N6**（报告的三处交账）→ ⑤ 行降级为「⚠️ 部分达成」并补 `numbered_bench` 比例扫描未做；⑨ 行「四个纯函数」改「六个」并补 χ²/精确二项/MDE 的交账行与产物路径；§五 补 `calib` 的接口破坏（**语义反转**要写明）。
3. **改 N2 + N3 + N7**（口径）→ E 组「回落目标」行按 25 次 `coarse` 改写；探针自检一处结论（并入 §四「与规格的分叉」）；Δ 的方向统一成一种写法并每处标口径。
4. **改 N8 + N9**（延迟 / 头条）→ H 组延迟注明取自哪批请求；头条 44.81% → 55.74% 补上「跨跑批」限定 + `coarse_only` 锚点（76 vs 75）。
5. **改 §四 §一**（E1/E2/E3）→ `ralph-round1-review.md:16/:22/:29` 的判定与时间戳。
6. **不需要人拍板的**：N1、N4、N5、N6、N7、N8、N9 全是「照产物改报告」；**需要人拍板的**仍只有那几条规格冲突：探针自检与冻结目录（N3）、⑧ 是否要改 `verify/`、以及 H 组的端点计价表（费用折算，本轮依然缺）。

---

## 八、本遍的核验命令（可复现）

```powershell
$env:PYTHONPATH="src"
git log --format="%h|%ad|%s" --date=iso-strict -12          # 时间线
git diff 3acd5e0...HEAD --stat -- src/minicode/coord src/minicode/exec src/minicode/verify   # 冻结目录 = 空
Get-ChildItem runs\g2-eval-crop192 -Force | Select Name,Length,CreationTime,LastWriteTime
git show 1db1622 --stat ; git show 3187c02 --stat
python -m pytest src/minicode/ground/test_ground_two_stage.py src/minicode/eval/test_eval_stats.py `
    src/minicode/calib/test_calib_probe.py src/minicode/e2e/test_e2e_visual.py -q     # 186 passed
python -m ruff check src                                                            # All checks passed
python -c "from minicode.eval.stats import mde; print(mde(183,.10), mde(1581,.10))"   # 6.549 / 2.228
```

---

# 九、第三遍（Ralph round 2/2 收尾遍 · 本轮 worker 的独立复核）

> **遍次**：**第三遍**。第一遍 = `docs/research/ralph-round1-review.md`（Lead 存档 + 抽验）；第二遍 = 本文 §一~§八（上一 worker 落盘）。
> **复核对象**：`docs/research/vision-final-plan.md` 的「施工清单 10 条」+「验收线 8 组」，
> 对照本轮实现（`git diff 3acd5e0...HEAD`）与实测产物（`runs/`）。
> **被交账的报告**：`docs/research/vision-closure-report.md`（HEAD = `7ae9823`）。
> **硬约束遵守**：**只读** —— 本遍未改任何实现/报告、未 commit、未 push、未执行任何 `gh`；
> 三个冻结目录 `src/minicode/coord` / `src/minicode/exec` / `src/minicode/verify`
> 的 `git diff 3acd5e0...HEAD --stat` **为空**（已核）；`git status --short` 只有
> `?? docs/research/ralph-round2-review.md`（无 pytest 残留：本遍用 `-p no:cacheprovider` + `PYTHONDONTWRITEBYTECODE=1`）。
> **唯一写入文件 = 本文件。**
> **核验环境**：Windows / PowerShell；`$env:PYTHONPATH="src"`；
> **全量 `python -m pytest` → `1243 passed, 253 subtests passed`**
> （第二遍只跑了定向四套 186 passed；本遍独立复现了 `3187c02` 的基线自述）。

## 九·0 一句话结论

**第二遍的 10 条结论本遍逐条独立复核：9 条成立、1 条要收紧（§九·A）；第二遍自己判「已修」的那几类（报告层 8 处虚报 8/8、7 处引用破口 7/7、④⑤⑥ 四项代码真修）经本遍抽样复核全部成立 —— 但报告里被点名的 10 处问题**一条都没改**（本遍逐处查了当前报告文本，全在原处）。
本遍另查 8 条新问题（§九·B）：3 条验收线整行未交账、2 条口径混读（**挂在头条旁边的两个注脚都不是本轮跑批的数**）、1 条引用档位名错、1 条实现耦合未按规格解掉、1 条「还剩什么」漏两组。**

**专项：先回答被点名的那一问 —— 第一遍那唯一一处被 Lead 判成「误报」的条目，本遍独立复核确认：第一遍没读错；Lead 的抽验把「16:20 跑批进行中」当成了「16:34 收工后」。详见 §九·C。**

## 九·A 对第二遍 10 条结论的独立复核

> 判定口径：**「成立」= 本遍重新从产物/源码算过，不是照抄第二遍**；
> **「未修」= 报告当前文本（HEAD `7ae9823`）里那处字句仍在原处**。

| # | 第二遍结论 | 本遍独立复核 | 报告现状 |
|---|---|---|---|
| N1 | ⑥ 行 token 差写 −45，实为 −162 | **成立**：`runs/m6-token-probe-2048exact/token-probe.json` → `results[0] = {sent_size:[2048,1280], expected_image_tokens: 3404, observed_image_tokens: 3242, overhead_tokens: -162}` | `:205` 仍写「差 −45」→ **未修** |
| N2 | E 组「回落目标 = 精定位点」实为 25 次 coarse | **成立**：`runs/g2-eval-final/sspro-report.json` → `comparison.always.telemetry.landing_source_distribution = {coarse: 25, refined: 151, whole_screen: 6, none: 1}` | `:128` 仍写「精定位点（判据不改落点 → 不存在「降级到更差的一级」）」→ **未修** |
| N3 | 探针自检 `:69` 与 `:207` 结论相反 | **成立**（两处原文仍在；`git diff 3acd5e0...HEAD --stat -- src/minicode/verify` 确为空 → 确实没改） | **未修** |
| N4 | ⑤ 的「比例扫描」未做却判 ✅ | **成立**：`src/minicode/ground/numbered_bench.py:669` `CROP_SCAN_SIZES = (128, 192, 256, 512, 768, 1024, 1280)` 全是固定 px；生产在用的「0.5×当前帧」不在扫描集内 | `:204` 仍判 ✅ → **未修** |
| N5 | ⑨ 行「四个纯函数」实为六个；χ²/MDE 没交账 | **成立**：`src/minicode/eval/stats.py` 六个函数在 `:73 mcnemar_midp` / `:105 mcnemar_exact` / `:135 mcnemar_chisq_cc` / `:168 wilson_ci` / `:204 paired_bootstrap_delta` / `:259 mde`；接线在 `src/minicode/eval/sspro.py:1187 mde_pp` / `:1204 mcnemar_exact_p` / `:1206 mcnemar_chisq_cc_p`；`src/minicode/eval/__main__.py:705-712` 已打印对照；产物 `runs/g3-eval-2016/sspro-report.json` 里 `mcnemar_exact_p=3.2565e-09` / `mcnemar_chisq_cc_p=2.4136e-08` / `mde_pp=11.25` 三个数都在 | `:208` 仍写「四个纯函数」；全文 grep「χ² / 连续性校正」**0 命中** → **未修** |
| N6 | §五 漏报 `calib` 的接口破坏 | **成立**：`src/minicode/calib/calibrate.py:364`（第三参 `aspect_ratio → declared_size`，返回值语义反转）/ `:287`（新增 `diagnose_server_model_input_from_tokens`，已进 `src/minicode/calib/__init__.py` 的 `__all__`）。⚠️ 报告 §五 的标题是「接口破坏（**本轮 · 改名类**）」—— 这条是**语义反转类**，连它自己划的类别都不在 | `:263-277` 仍只列 `src/minicode/eval/` → **未修** |
| N7 | Δ 方向与口径自相矛盾 | **成立**（本遍补一条更硬的同族问题 → **P3**） | `:106` / `:208` 仍在 → **未修** |
| N8 | 延迟 16.70 s 取自标定探针 | **成立且升级** → 见 **P4**（同一格里「`model_calls.sent = 0`」与「n=40 条真正发出的请求」直接打架） | `:183` 仍在 → **未修** |
| N9 | 头条 44.81%→55.74% 是跨跑批比较 | **成立且升级** → 见 **P3**（报告挂在头条旁的「重加权 42.5%」与「CAD 命中 0/9」**都不是本轮跑批的数**） | `:15-17` / `:25-26` / `:203` 仍在 → **未修** |
| N10 | commit `1db1622` 的 message 与 diff 不符 | **成立**：`git show 1db1622 --stat` 只有 `src/minicode/ground/prompt.py`（+23/−1）与 `test_ground_two_stage.py`（+59），**没有 `channel.py`**；功能成立是因为 `src/minicode/ground/channel.py:690` 的既有 `except GroundingParseError` 恰好覆盖了新抛点 `src/minicode/ground/prompt.py:455` | 报告未记 → **未修** |

### 第二遍判「已修」的那几类 —— 本遍抽样复核（成立）

| 第二遍的结论 | 本遍独立抽样复核 |
|---|---|
| 8 处虚报 8/8 已订正 | **成立**（抽 3 条）：`runs/v1-calib/session-table.md` 七行 `denominator_pair_divergence_px = 2.8676273030872284` ✓；`runs/v1-wave1c/e2e-report.json` → `visual_step_decidable_rate = 0.0`、`tiers {hit:2, near_miss:0, wrong:0, undecidable:2}` ✓；`runs/g2-eval-final/sspro-report.json` → `gate = {fallbacks: 25, refine_enabled: 176, fallback_rate: 0.14204545454545456}` ✓ |
| 7 处引用破口 7/7 已修 | **成立**：本遍用 **26 个禁引模式**独立 grep 全文 → 只命中 `4\.2`（8 处），逐处都是本仓自己的数（84.2% / 42.1% / 14.2%），**零违规**；报告里 **25 条 `runs/` 路径**逐条 `os.path.exists` → **全部存在**（含 `runs/g2-eval-crop192/sspro-report.json`） |
| ④⑤⑥ 四项代码真修 | **成立**：`src/minicode/ground/prompt.py:455`（`is_truncated(segment)` → 抛 `GroundingParseError`）+ `src/minicode/ground/channel.py:690-704`（接住 → `fallback=True` / `fallback_cause=no_output` / 落点 = 粗定位点）；`src/minicode/calib/calibrate.py:287/:364` + `src/minicode/e2e/visual.py:110`（`server_image_evidence_from_log`）；`src/minicode/eval/stats.py:105/:135/:259` + `src/minicode/eval/sspro.py:1187/:1204/:1206` |

## 九·B 本遍新发现（8 条 · 每条 = 文件:行号 + 证据 + 规格依据）

### 类别 2 · 验收线未达成 / 未交账，却以「已交账」的形式呈现

#### P1 【验收线整行未交账 + 规格项未做】F 组「编号分支白进」一行在报告里整个消失

- **位置**：`docs/research/vision-closure-report.md:132-143`（F 组表 4 行，**没有**「编号分支白进」这一行）；规格 `docs/research/vision-final-plan.md:113`。
- **证据**：
  - 规格原文（plan:113）：「**编号分支白进** | **61/87 个 icon（70%）** 白进编号分支而命中 0 | 收紧前置条件（候选文字覆盖描述关键词 ≥ 0.3~0.4）；icon 进分支降到 32/87（ratio≥0.3，召回 3/32 = 9.4%）或 18/87（≥0.4），text 仍留 55/96（召回 37/55 = 67.3%）| 候选召回率 + 命中数；**总分不升，省调用**」。
  - 实现：`src/minicode/ground/channel.py:495-500` —— 进编号分支的**唯一**条件是 `if candidates:`（有候选就进）；`_numbered`（`channel.py:982-1011`）也不判任何前置条件。**全库没有任何覆盖率/关键词门槛**：grep `coverage_ratio|描述关键词` 在 `src/minicode/ground/` 下 **0 命中**；`src/minicode/ground/candidates.py:344-362` 与 `numbered_bench.py:72-77` 的 IoU@0.3/0.5 三列只是**观测列**，没有被用作入口条件。
  - 报告全文 grep「编号」→ **只命中「候选」相关句**（`:140` / `:202` / `:237`），**「编号分支」一次都没出现**（`:140` 写的是「纯视觉下的 icon 候选召回」）。
- **规格依据**：plan:113；plan:6（「验收线……**全部写成可判定的数字**」）；plan:112（同组上一行的口径是「编号基准新增两个分离指标」）。
- **严重度**：中。**一行带数字目标的验收线（icon 进分支 ≤32/87、text 仍留 55/96）既没交账、也没做，报告没有任何披露。**

#### P2 【验收线整行未交账】H 组两行（无文本率 / 解析失败影响）被自造的两行顶替

- **位置**：`docs/research/vision-closure-report.md:176-183`（H 组表 4 行）；规格 `docs/research/vision-final-plan.md:131`、`:132`。
- **证据**：
  - plan 的 H 组有 **4 行**：:129 每目标模型调用数、:130 图像 token/目标、:131 **「4096 档残余无文本率」**（目标：8192 档外推 0.03~0.07%；「并加 `stop_reason` / `content_block_types` / `text_len` / `parse_ok` 四个字段后**直读**」）、:132 **「解析失败对分数的影响」**（目标：保持 0 pp）。
  - 报告的 H 组表实际是 4 行，但其中 **2 行是自造的**（`:182`「整屏输入档位」、`:183`「延迟/费用」）→ 被顶掉的正是那两行带数字目标的。**全文 grep「无文本率 / 解析失败 / stop_reason / parse_ok」→ 0 命中。**
  - 而 :131 要求的四个字段**已经在产物里**（本遍逐行核过 key）：`runs/g2-eval-final/model-requests.jsonl` 每行含 `stop_reason` / `content_block_types` / `text_len` / `parse_ok` → **「直读」的条件已具备，报告没读**；:132 的「解析失败对分数的影响」也有对应量（`runs/g2-eval-final/sspro-report.json` → `comparison.always.errors = 1`；`model_calls.sent = 0`）。
- **规格依据**：plan:131、:132、:130（「**必须同时报延迟与费用**，不许只报准确率」）。
- **严重度**：中。

### 类别 3 · 口径不一致

#### P3 【口径混读】挂在头条旁边的两个注脚（「重加权 42.5%」「CAD 命中 0/9」）**都不是本轮跑批的数**

- **位置**：`docs/research/vision-closure-report.md:25-26`（一句话结论的注）。
- **证据**：
  - 报告原文：「⚠️ 183 条是**分层抽样**（OS 41.5% / CAD 4.9%，与全量 1581 条构成不同；**CAD 命中 0/9**），按全量构成**重加权后是 42.5%**（CI [35.5%, 49.7%]）→ **55.74% 是抽样构成下的值，不是真值**。」
  - 「42.5%」的出处是 `docs/research/vision-root-cause.md:51`：「按全量 group 构成重加权后 **44.8% → 42.5%**」→ **它是 44.8%（旧基线）的重加权值，不是 55.74% 的**。报告把它写成 55.74% 的注，读起来就是「55.74% 的重加权 = 42.5%」。
  - 「CAD 命中 0/9」也是旧跑批的事实：旧基线 `runs/g1-eval-330/sspro-report.json` → `comparison.always.accuracy_by_group.CAD = 0.0`；而**本轮头条产物 `runs/g2-eval-final/sspro-report.json` → `comparison.always.accuracy_by_group.CAD = 0.3333333333333333`（3/9）**。报告拿「CAD 全灭」来解释本轮头条的抽样偏高。
  - 本轮 55.74% 的重加权值**任何产物里都没有算过**（`runs/` 下没有重加权产物；report 也没给）。
- **规格依据**：plan:356（「对外只报一个头条数……并同报 Wilson CI 与重加权值」「抽样构成偏高」）；plan:427（混读禁令 3「口径版本要随数出现」）；plan:21（「44.8% 是抽样构成偏高的值，不是真值」）。
- **严重度**：中-高。（第二遍 N9 说的是「跨跑批比较」；这条更硬：**报告里作为注脚的两个数根本不是本轮跑批的数**，而 plan:356 要求「同报重加权值」—— 报的是一个属于别的跑批的重加权值。）

#### P4 【口径·自相矛盾】H 组延迟那一格里，「`sent = 0`」与「40 条真正发出的请求」直接打架

- **位置**：`docs/research/vision-closure-report.md:183`。
- **证据**：
  - 报告原文：「本轮请求延迟**中位 16.70 s**（**n=40 条真正发出的请求** —— 产物 `model_calls.sent = 0`，其余是应答记忆复用，所以延迟样本只有这 40 条）」。
  - 产物：`runs/g2-eval-final/sspro-report.json` → `model_calls = {"sent": 0, "replayed": 977}` → **183 条评测目标一条请求都没发出去**（全走应答记忆复用）。既然 `sent = 0`，那「40 条真正发出的请求」就**不是评测请求**。
  - 那 40 行是什么：`runs/g2-eval-final/model-requests.jsonl` 的 `image_tokens` 分布 = **1705×12 / 2691×10 / 1925×5 / 1716×5 / 880×3 / 2392×3 / 1196×2** → 这是**标定探针的尺寸阶梯**（1925 = 1536×960 整屏档），不是评测负载的「1536 整屏 + crop」。本遍实算其中位 `latency_ms = 16704.42` → 报告的 16.70 s **数值对、口径错**。
  - 真正的评测负载延迟在别处：`runs/g2-eval-crop192/model-requests.jsonl` **177 条、`image_tokens` 全是 49（192 crop 那一级）、中位 16057.4 ms**；`runs/g3-eval-2016/model-requests.jsonl` 报告自己在 `:303` 写「中位 **10019.9 ms**，n=416」。**三个数不同源，报告拿标定探针的 16.70 s 交 H 组的账，同时在产物索引里引评测负载的 10.02 s。**
- **规格依据**：plan:130（「**必须同时报延迟与费用**」）；plan:335（「2048 档的成本与延迟……延迟/费用未实测」→ 延迟必须落在**被测负载**上）。
- **严重度**：中。（第二遍 N8 的加强版：不是「标签错」，是**同一格里两个互斥的事实并列**。）

#### P5 【引用纪律·档位名】F 组「现在」列的 13.25 px / 65.9% 引用了 m17 里的 **legacy 档**，却标成「旧档位 1280×800」

- **位置**：`docs/research/vision-closure-report.md:138-139`。
- **证据**：`runs/m17-icon-size/icon-size.json` 有 **5 个 tier**，其中两个都跟 1280×800 有关：
  - `tiers["1280x800"]` → icon = {samples 604, **median_px 11.6667**, below_16px **476**, ratio **0.788079**}
  - `tiers["1280x800_legacy_integer_factor"]` → icon = {samples 604, **median_px 13.25**, below_16px **398**, ratio **0.65894**}
  - 报告写「现在 中位 **13.25 px**（全量 604 个 icon，旧档位 1280×800）」与「<16 px 的占比 现在 **65.9%（398/604）**」→ 取的是 **legacy 整数因子**那一档，**档位名没写**（同一个产物里「1280×800」指的是 11.67 px / 78.8%）。
- **规格依据**：plan:376（每个数带样本量与**判定口径**）；plan:427。
- **严重度**：低-中。**数本身有出处**（本遍一开始差点把它当成虚报，查产物后确认不是 —— 方法论提醒记在这里）；错在档位名 + 与「达成」列的 1536×960 档（14.00 px / 379）相减时口径没标。

#### P6 【口径】⑥ 行「icon 中位 13.25 → 14.00 px」没说是哪两个口径相减；验收线指定口径上的「现在」在产物里不存在

- **位置**：`docs/research/vision-closure-report.md:205`（⑥ 行）、`:138`（F 组行）。
- **证据**：
  - 13.25 = `1280x800_legacy_integer_factor` 档（**n=604**）；14.00 = `1536x960` 档（**n=604**）→ 同分母同 metric，相减成立（+0.75 px）。
  - 但验收线 F 组「怎么测」指定的口径是**评测台每轮输出的分布**（plan:111），那个口径下本轮实测是 **12.0 px（n=87）**（`runs/g2-eval-final/sspro-report.json` → `model_input_target_sizes.icon = {samples: 87, median_px: 12.0, below_16px: 64, rate: 0.7356}`）。
  - **legacy 档在 n=87 子集上的对应值任何产物里都没有**（`runs/m17-icon-size/icon-size.json` 只有 604 / 977 两个分母；`runs/g1-eval-330/sspro-report.json` 的 `model_input_target_sizes` 是 **null** —— 旧跑批根本没有这个埋点）。
  - ⇒ 「现在 → 达成」在**验收线指定的口径上无法复现**；同一格里并存三套口径（604 旧 / 604 新 / 87 新），没有一处标口径。
- **规格依据**：plan:111（「怎么测」= 评测台随每轮输出的分布）；plan:427；plan:376。
- **严重度**：中。

### 类别 1 · 实现与规格不符（未披露）

#### P7 【实现耦合未按规格解掉】⑤ 的「以粗定位框中心为中心」是靠「粗定位点 = 框中心」间接成立的

- **位置**：`docs/research/vision-closure-report.md:204`（⑤ 行写「以粗定位框中心为中心……✅」）；规格 `docs/research/vision-final-plan.md:195`。
- **证据**：
  - plan:195 点名要改 `src/minicode/coord/crop_refine.py:152::plan_crop` / `_fit_axis`，「把 `CoarseTarget.bbox` **传下去**」；`coord/` 是冻结目录 → **实际没改**（`git diff 3acd5e0...HEAD --stat -- src/minicode/coord` 为空）。
  - 生产链 `src/minicode/ground/channel.py:671-678` 调的是 `plan_crop(coarse[0], coarse[1], surface_rect(...), crop_width=..., crop_height=..., target_radius_px=(coarse_radius_px or self.target_radius_px))` —— **中心传的是「粗定位点」**，不是「粗定位框中心」。
  - 之所以等价：⑦ 把粗定位提示词的 `point` 字段删了（`src/minicode/ground/prompt.py:135-164` 只问 `{"bbox":…, "text":…}`），于是 `box_center(target)`（`channel.py:804`）拿到的就是框中心。**这是「靠另一条改动间接成立」，规格点名的那处解耦没做。**
  - 后果：一旦粗定位再带回 `point`，窗口中心会静默退回「点」；`src/minicode/coord/crop_refine.py:139-148` 的 `target_radius_px` 只保证「窗口尽量盖住半径圈」，**不保证中心是框中心**。
- **规格依据**：plan:195-196；plan:344（「⑦ ↔ 标定链：改 grounding 提示词的措辞**必须同步改**……」）；`AGENTS.md`「三个原型已验证、冻结不改」。
- **严重度**：低-中。**结论成立，但耦合没按规格解掉，报告把它写成 ✅ 且未披露「间接成立」这一层。**

### 类别 2 / 4 · 其余

#### P8 【完整性】§四「还剩的验收线缺口」表漏了 B 组与 E 组

- **位置**：`docs/research/vision-closure-report.md:240-250`（缺口表 7 行：F / ④ / G / H / A / D / ⑨）。
- **证据**：B 组「36 px 近失带**未达成**（36 px 不在空白区）」写在 `:70` + `:77-81`；E 组「**阈值取值 未达成**」「**可迁移性 未达成**」写在 `:129`、`:130` —— **两组都是真缺口，却都没进 §四的缺口表**（C 组不是缺口：离线 84.2% / 实跑 79.5% 均 ≥75% ✓）。
- **规格依据**：plan:6（验收线逐条可判定）；报告自身的 §四 标题「还剩的验收线缺口」。
- **严重度**：低。

## 九·C 专项：先回答被点名的那一问 —— 第一遍那 1 处「误报」，是不是读错了？

**结论：不是读错。第一遍那句「`runs/g2-eval-crop192/sspro-report.json` 不存在」对它所审的那一版报告（`7e74d33`）在提交时刻是**真的**；把它判成「误报」的是 Lead 的抽验 —— 那是拿 16:20 的报告去对 16:34 之后的文件系统。**

### 时间线（本遍独立复核，全部来自文件系统时间戳 + git 时间戳）

| 时刻 | 事件 | 证据 |
|---|---|---|
| 05:39:33 | `runs/g1-eval-330/sspro-report.json` 落盘（旧基线，44.81%） | 文件 LastWriteTime |
| **16:16:48** | `runs/g2-eval-crop192/model-requests.jsonl` **创建** → crop192 评测**开跑**（请求边跑边写） | 文件 CreationTime |
| **16:20:55** | commit `7e74d33`（报告首版）提交，其 `:234` 索引 `runs/g2-eval-crop192/sspro-report.json` | `git log --format=%ad --date=iso-strict` + `git show 7e74d33:docs/research/vision-closure-report.md`（本遍逐行核到 `:234` 正是那一行） |
| 16:11:40 | （对照）`runs/g2-eval-final/sspro-report.json` 落盘 —— **头条产物当时已存在** | 文件 LastWriteTime |
| **16:34:02** | crop192 请求日志**最后一次写入**（177 行写完；本遍核：177 行、`image_tokens` 全为 49） | 文件 LastWriteTime |
| **16:34:04** | crop192 的 `sspro-report.json`（763576 B）+ `sspro-table.md` **才生成**（CreationTime = LastWriteTime = 16:34:04 → 是新建，不是覆盖） | 文件 CreationTime |
| 17:40:56 | commit `1945bd0`（第一遍存档 + Lead 抽验，§一 #1 判「复核误报」） | git 时间戳 |
| 17:59:28 | `runs/g2-eval-crop192/paired-recheck.json` 生成（⑤ 的离线重算） | 文件 LastWriteTime |

### 三条推论

1. **报告引用的那条路径，在报告提交后 13 分 9 秒里都是不存在的。** 第一遍复核的对象是 `7e74d33` 那一版 → 当时「报告索引了一条不存在的产物路径」是实打实的引用纪律破口，**第一遍报得对**。
2. **第一遍看到的目录状态与「跑批进行中」完全吻合**：那会儿目录里只有 `model-requests.jsonl`（177 行、image_tokens 中位 49 —— 与最终文件一致），`sspro-report.json` 是这一轮的**最后一步**才写的。
3. **Lead 的抽验跑在 16:34:04 之后**，看到的是已经收工的目录 → 于是把「当时不存在」判成「误报」。

### 连带要改的两处（第二遍已指出，本遍复核认同）

- `docs/research/ralph-round1-review.md:22` 的「❌ **复核误报**」判定本身是错的 → 应改为「**成立（带时点限定）**」。
- 同文件 `:29`「**结论：5/6 正确，1 处误报**」→ 应改为「**6/6 成立**」，并注明第 1 条的正确性建立在「按 16:20 的报告版本 + 跑批进行中的目录快照」两个时点限定上。
- 同文件 `:16` 的「2026-10-22 17:5x」与该文件自身提交时间 17:40:56 矛盾（抽验发生在存档提交**之前**）→ 时间戳写错。

## 九·D 两遍都错 / 被继承下来的错（更新第二遍 §五）

| # | 错在哪 | 第一遍 | 第二遍 | 第三遍（本遍） |
|---|---|---|---|---|
| **E1** | 「`runs/g2-eval-crop192/sspro-report.json` 不存在」被判成误报 | 报得对（对它审的那一版成立） | Lead 抽验判错（时间差）—— 第二遍已改正 | **独立复核确认第二遍的改正正确**（时间戳本遍重测） |
| **E2** | 「1 处误报 / 5/6 正确」这个比例 | — | 第二遍：应改为 6/6 | **认同**（6 条里第 1 条带时点限定后成立） |
| **E3** | `ralph-round1-review.md:16` 的抽验时间戳 | — | 第二遍指出 | **认同** |
| **E4** | 报告正文对**最后三个 commit 无感知** | — | 第二遍 §五 的表述：「三个 commit 都发生在报告最后一次提交 19:29 之前，但报告正文只反映了前两者的一部分」 | **第二遍的表述可以更准**：`7ae9823`（19:29）**确实改了** `docs/research/vision-closure-report.md`（`git log --name-only` 可证）—— 真正的事实是「**报告最后改的那一版恰恰是在 `1db1622`（18:36 截断检查）与 `3187c02`（18:37 相等断言接生产链 + χ²/MDE）之后改的，却对这两个 commit 零提及**」。本遍全文 grep 佐证：「χ² / 连续性校正」**0 命中**、「`estimate_model_input_from_tokens`」**0 命中** |
| **E5** | 第二遍 §六 第 1 条「没跑全量 pytest」 | — | 未跑 | **本遍跑了：`1243 passed, 253 subtests passed`**（独立复现 `3187c02` 的基线自述）→ 这条边界已结清 |
| **E6** | **方法论**：F 组「现在」列的 13.25 px / 398/604 | — | — | 本遍**初判为「产物里查不到 → 虚报」**（差点写进报告），查 `runs/m17-icon-size/icon-size.json` 后确认：这是 `1280x800_legacy_integer_factor` 档，**数有出处**，只是档位名没写（P5）。**记下来：同一个产物里可以有多个只差一行的 tier 名，判「虚报」前必须把该产物的所有 key 看完。** |

**没有「两遍都错」的实质技术结论**：第一遍被抽验的 6 条、第二遍的 10 条，本遍逐条复算后**除 E1 的判定归属外全部成立**。

## 九·E 把第二遍 §六 的 5 条边界逐条结清

1. **没重跑任何模型调用** —— 本遍同样**没跑**（只读 + 不动真机/额度）。所有数都是**对已有产物的独立重算**（`outcomes` 求和 / `accuracy_by_group` / `model_input_target_sizes` / `model_calls` / `model-requests.jsonl` 逐行统计 / `near_miss_calibration` 直接读）。
2. **没核 `runs/` 之外的历史批次** —— 本遍补核了两份：
   - `runs/g1-eval-330/sspro-report.json` → `always.hits = 82`（44.81%）、`coarse_only = 76`、`gate.fallback_rate = 0.4371584699453552`（**80/183**）→ 报告 `:203` 的「旧轮 43.7%（80/183）」**对得上**；但该产物 `telemetry` 为**空**、`model_input_target_sizes` 为 **null** → 「43.7% 回落率」这个数**只有 `gate.fallback_rate`，没有 `fallback_cause` 归因**，归到「偏离」是来自 plan:48 的旧行为描述（可接受，但值得标一句）。
   - `runs/m17-icon-size/icon-size.json` → 5 个 tier（见 P5）。
3. **没审 `action` / `decision` / `exec` 的行为面** —— 本遍补审：
   - `src/minicode/action/ports.py`（+12 行）：`raw_bbox`（`:679`）/ `raw_text`（`:681`）/ `landing_source`（`:746`）→ ① 埋点第 ⑥ 项「粗定位的**原始 bbox + point**」**确实落盘** ✓。
   - `src/minicode/decision/test_decision_harness.py`（+4/−2）：只动测试，无行为面变更。
   - `src/minicode/exec/`：`git diff 3acd5e0...HEAD --stat` 为空 ✓；`ActionResult`（`src/minicode/action/protocol.py:191-213`）**至今没有 `result` 字段** —— ⑩ 那个 `'ActionResult' object has no attribute 'result'` 的修法是**改调用方**（`src/minicode/e2e/real.py:1053-1055`「形状对齐：……所以要包成缝的形状」），落在 `ee0c624`（2026-10-21 21:21）→ **确实在 `3acd5e0`（10-22 13:19）之前** ✓ 报告 `:209` 的说法成立，但应补一句「修的是调用方、不是给 `ActionResult` 加属性」。
4. **没核 `docs/spec.md` / `docs/00-现状.md` 的同步状态** —— 认同第二遍的处置（报告 §四 已把它们列成「与规格的分叉，待人拍板」），不重复列问题。
5. **只跑了定向四套** —— 本遍跑**全量**：`1243 passed, 253 subtests passed`（54.13 s）✓。

## 九·F 给收尾的建议（按「改一处就少一处破口」排序）

1. **P3 + P6 + P5（头条那一段的三个数）**：① 「重加权 42.5%」要么改成本轮 55.74% 的重加权值（需要先算 —— 产物里没有），要么明确写成「44.8% 的重加权值，属旧基线」；② 「CAD 命中 0/9」改成本轮的 3/9（`accuracy_by_group.CAD = 0.3333`）；③ F 组「现在」列注明 `1280x800_legacy_integer_factor` 档，并把 ⑥ 行的 13.25→14.00 标成「n=604 同口径」＋ 另列评测台口径 12.0 px（n=87）。
2. **P4（H 组延迟）**：改成评测负载的数（`runs/g2-eval-crop192/model-requests.jsonl` 177 条 / 中位 16057 ms），或明确写「16.70 s 是**标定探针**的延迟，评测负载延迟未在本档位实测」。
3. **P1 + P2（三行没交账的验收线）**：F 组补「编号分支白进」一行（现状：入口条件只有 `if candidates:`，未做收紧；本轮未测）；H 组补「4096 档残余无文本率」（用请求日志的 `stop_reason` / `text_len` 直读）与「解析失败对分数的影响」。
4. **第二遍的 N1/N2/N3/N4/N5/N6/N7/N8/N10**（报告里一个字未改的那 10 处）—— 按第二遍 §七 的排序改。
5. **P7 / P8**：⑤ 行补一句「框中心 = 粗定位点（靠 ⑦ 删 `point` 成立；`coord/` 冻结未改 `plan_crop` 签名）」；§四 缺口表补 B 组与 E 组。
6. **需要人拍板的（本遍新增 2 条）**：
   - **P1**：plan:113 的「编号分支白进」本轮是**放弃**还是**补做**？（它不在 10 条施工清单里，但它是 F 组的一行验收线。）
   - **P3**：plan:356 要求的「同报重加权值」，对外到底报**哪一个跑批**的重加权？（这属口径决策，按 `AGENTS.md`「标定 / 坐标契约这类口径变更必须由人拍板」。）
   - 其余（P2/P4/P5/P6/P7/P8 + 第二遍的 N1/N2/N4/N5/N7）**都是照产物改报告**，不需要人拍板。

## 九·G 本遍的核验命令（可复现）

```powershell
$env:PYTHONPATH="src"; $env:PYTHONDONTWRITEBYTECODE="1"
# 1) 全量测试（第二遍只跑了定向四套）
python -m pytest -q -p no:cacheprovider              # 1243 passed, 253 subtests passed
# 2) 冻结目录 = 空 + 工作树只有本文件
git diff 3acd5e0...HEAD --stat -- src/minicode/coord src/minicode/exec src/minicode/verify   # 空
git status --short                                                                    # ?? docs/research/ralph-round2-review.md
# 3) 产物核对（头条 / 2016 档 / crop192 / 标定 / wave / icon 四档 / 基线）
python -c "import json;d=json.load(open('runs/g2-eval-final/sspro-report.json',encoding='utf-8'));print(d['comparison']['always']['accuracy_by_group'],d['model_calls'],d['comparison']['paired']['always_vs_coarse_only']['delta_ci95_pp'])"
python -c "import json;d=json.load(open('runs/g1-eval-330/sspro-report.json',encoding='utf-8'));print(d['comparison']['always']['accuracy_by_group'],d['comparison']['always']['gate']['fallback_rate'],d.get('model_input_target_sizes'))"
# 4) 时间线（专项：那一处「误报」）
Get-ChildItem runs/g2-eval-crop192 -Force | Select Name,Length,CreationTime,LastWriteTime
git show 7e74d33:docs/research/vision-closure-report.md | Select-String "g2-eval-crop192"
# 5) 引用纪律（26 个禁引模式 + 25 条 runs 路径）
python -c "import re,pathlib,os;t=pathlib.Path('docs/research/vision-closure-report.md').read_text(encoding='utf-8');print([p for p in sorted(set(re.findall(r'runs/[A-Za-z0-9._/\-*]+',t))) if '*' not in p and not os.path.exists(p.replace('/',os.sep))])"
```

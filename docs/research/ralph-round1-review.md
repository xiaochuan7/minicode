# ralph 第一遍对抗式复核 · 结论存档（原文 + 抽验）

> **来源**：`session-5adf3a50-0b1f-4bbb-a3e9-bee17ef71e7c` 的 ralph 循环。
> 目标 = 对 `docs/research/vision-final-plan.md` 的「施工清单 10 条」与「验收线 8 组」做对抗式复核
> （`git diff 3acd5e0...HEAD`），`maxRounds=3`。
>
> **第一遍完成**（child 1，26 分钟），**第二遍在产出结构化报告前失败**（child 2），
> 随后整个会话因 **402 Endpoint is unavailable**（MiniMax 额度失效）终止。
> 第一遍的结论原文保存在该会话事件流的 `ralph` 工具结果里（"Last successful handoff"），本文是它的**逐字存档**。
>
> **存档人**：Lead。**存档时机**：agent 已停下（会话终止），符合 `AGENTS.md`「改验收标准只在 agent 停下之后由人做」。
> **本文只存档 + 抽验，不改任何代码与验收线。**

---

## 一、抽验结果（Lead 独立核对，2026-10-22 17:5x）

复核报告共列 15 条 evidence + 6 条 nextSteps。Lead 抽验了其中最狠的 6 条：

| # | 复核的说法 | Lead 核对产物 | 判定 |
|---|---|---|---|
| 1 | `runs/g2-eval-crop192/sspro-report.json` **不存在**，⑨ 的 183 条配对 mid-p McNemar 未做成 | `Get-ChildItem runs\g2-eval-crop192` → `sspro-report.json` **763576 bytes 存在** | ❌ **复核误报** |
| 2 | 报告写「视觉步骤可判率 100%」，产物 `visual_step_decidable_rate = 0.0` | `runs/v1-wave1c/e2e-report.json` → `visual_steps=2, visual_steps_decided=0, visual_step_decidable_rate=0.0`；tiers `{hit:2, near_miss:0, wrong:0, undecidable:2}` | ✅ **复核正确** |
| 3 | 报告写「等价标度族分歧 0.00 px」，产物实为 `2.8676273` | `runs/v1-calib/s0/calibration-records.jsonl` → `denominator_pair_divergence_px = 2.86762730308723`；`divergences_px` 内为 `Infinity` | ✅ **复核正确**（但 2.87 ≤ 4，该项**仍达成**，只是数写错） |
| 4 | 报告写 icon 中位 **14.00 px**，产物实为 **12.0 px** | `runs/g2-eval-final/sspro-report.json` → `model_input_target_sizes.icon = {samples:87, median_px:12.0, below_16px:64, below_16px_rate:0.7356}` | ✅ **复核正确** |
| 5 | **BLOCKER**：`ground/prompt.py:437-455 parse_optional_point` 未调 `is_truncated`，与自己 docstring:445「截断 = 0 分、不抢救」矛盾 | 实测：函数体 437-455 全文**无** `is_truncated` 调用；`is_truncated` 只在 `prompt.py:240`、`prompt.py:515` 两处被用 | ✅ **复核正确（真 BLOCKER）** |
| 6 | 施工清单要求的 χ² + 连续性校正在 `src/` 不存在 | `grep -c "chi2\|chi_square\|chisquare\|continuity"` over `src/minicode/**/*.py` = **0** | ✅ **复核正确** |

**结论：5/6 正确，1 处误报。这份复核可信度足够高，应当复用。**

它自己那句话也站得住：**「报告确把多条未达成/未做/未测写成达成」。**

---

## 二、第一遍复核原文（逐字存档）

### summary

对 docs/research/vision-final-plan.md 的施工清单 10 条与验收线 8 组做了对抗式复核（只读，未改任何文件、未 commit/push、未执行 gh；三个冻结目录 diff 为空）。四类都核过：①实现与规格不符——10 条全核，6 条有偏差（④⑤⑥⑦⑨ 部分实现、⑧ 仅冻结目录一条未做），最严重是 ⑦④「截断=0 分」在精定位解析器 parse_optional_point(prompt.py:437-455) 根本没实现（实测截断串被抢救成坐标 (10.0,20.0)，与自身 docstring:445 矛盾）与 ⑥ 的相等断言只接进评测台、生产链 e2e/visual.py 仍走 ±28px 盒子；②验收线未达成却被写成达成——8 组全核，核到 8 处（A 组等价标度族分歧达成值写成 0.00px 而产物每个成功会话是 2.8676px、0.00 只来自 SSL 失败会话的 null 默认；E 组整组漏交「阈值取值」「可迁移性」两行；G 组「视觉步骤可判率 100%」而产物同名字段 visual_step_decidable_rate=0.0；④行标 ✅ 但「回落率≤10%」实际 14.2%；B 组 36px 校准目标未答「是否空白区」；F 组 icon 14.00px 与产物 12.0px 冲突；G 组「到中心 13.9px」实为上一轮 v1-wave1b 的数、wave1c 是 27.8px；⑤ 的配对对照产物路径 sspro-report.json 根本不存在）；③口径不一致——9 处（Δ 符号报告为正、产物 delta_pp=-14.754；image token 拆账应为 1788.39+1750.89 而非 1925+自适应crop；回落率分母 25/176 vs 25/183；延迟中位实为 16.70s 且「70 分钟墙钟」无依据；原文一致率三轮都是 10%；含住率差还有第二个口径差 target_radius_px=0.0 vs 生产传粗框半径；3765 与本轮几何不同源）；④引用纪律——8 类禁引数经 grep 验证零出现（报告自述成立），但违反「带样本量/口径/产物路径」7 处（产物路径不存在、18.67px 实为 2048 档却安给从未测过的 2016×1260、icon 尺寸出处是 .scratch 脚本不在允许集合、temperature「上线字节」无产物支撑、引的字段里没有那个数、头条缺 Wilson CI 与重加权）。结论：报告确把多条未达成/未做/未测写成达成。

### evidence（15 条）

1. 【实现】BLOCKER `src/minicode/ground/prompt.py:437-455` `parse_optional_point` 无 `is_truncated` 调用，docstring:445 自称「截断=0 分…不抢救」；实测 `is_truncated('{"x": 10, "y": 20} {"x": 30')=True` 而 `parse_optional_point` 返回 `(10.0,20.0)`。规格 `vision-final-plan.md:220` 要求「答案行优先 + 截断 = 0 分 + 松散写法仅在答案行接受」；粗定位路径 `prompt.py:515` 有该检查 → 纪律只落实一半
2. 【实现】MAJOR `plan:207` 点名的 `src/minicode/calib/calibrate.py::estimate_model_input_from_tokens` 未收紧（`calibrate.py:344-346` 仍是 ±28px 盒子）；相等断言是另起的 `assert_server_kept_image_tokens`，且只接进 `eval/__main__.py:654,664,683`，生产链 `e2e/visual.py:90-94,404-411` 仍走盒子反查 → 真机跑时服务端再缩图不会被拦
3. 【实现】MAJOR `plan:244` 要求的 χ²+连续性校正在整个 `src/` 不存在（grep `chi2|chi_square|chisquare|continuity` = 0 命中）；普通精确二项只在单测 `test_eval_stats.py:31-38`；`mde()` 无生产调用点（`sspro.py:1143` 未 import）→ `plan:246` 的「6.55→2.23pp」在任何产物里读不到
4. 【实现】分组 K 折 ✅ `holdout.py:201-236` 按 app×platform、`:139-150` `DEVIATION_CANDIDATES={24,32,40,48,56,64,80,96,128,∞}`；禁 index%2 ✅ 全库 0 命中（`numbered_bench.py:756-803` 已改为同样本两尺寸配对）。缺口：⑤ 的 `numbered_bench.py:669` `CROP_SCAN_SIZES` 仍固定 px，生产在用的 0.5×当前帧不在扫描集合（`plan:195` 要求比例扫描）
5. 【验收线】BLOCKER 报告 `vision-closure-report.md:93-101` 的 E 组只有 4 行，`plan:99-104` 的 6 行里「阈值取值」与「可迁移性」两行缺失；`runs/m9-kfold/kfold.json` 的 `threshold_distribution` 5 折全 1e9、`input.recorded_run` 是旧跑批 `runs/g1-eval-330`，生产读 `gate.py:230 DEFAULT_DEVIATION_TOLERANCE_PER_MILLE=260.4`（旧量「离 crop 中心 50px@192」的等价值）
6. 【验收线】BLOCKER 报告 `:124` 写「视觉步骤可判率 100%」；`runs/v1-wave1c/e2e-report.json` → `landing.visual_step_decidable_rate = 0.0`、`visual_steps_decided = 0`，条款 detail 原文「视觉步骤可判率 = 0/2 = 0%」
7. 【验收线】BLOCKER 报告 `:45` 写「等价标度族分歧 0.00 px ≤ 4 px（6 次会话全部）」并引 `divergences_px`；`runs/v1-calib/s0..s5/calibration-records.jsonl` 的 `denominator_pair_divergence_px = 2.8676273`（每个成功会话），唯一 0.00 在 s4（ModelTransportError: SSL UNEXPECTED_EOF）；`divergences_px` 内为 `Infinity`
8. 【验收线】MAJOR 报告 `:153` ④ 行标 ✅，但其目标「回落率 ≤10%」未达成：产物 `comparison.always.gate.fallback_rate = 0.1420`（25/176），旧轮 `g1-eval-330 = 0.4372`（80/183）；报告把「回落率」换成「因偏离而回落」并给出 13.7%（25/183），分母与产物不一致
9. 【验收线】MAJOR 报告 `:60` B 组：`plan:77` 目标「确认 36px 落在两峰之间的空白区」未答；产物 `near_miss_calibration.band_36px = {hits_failing_band:0, misses_rescued:18}`，misses=81 → 22.2% 的 miss 落在 36px 带内，36px 不在空白区
10. 【验收线】MAJOR 报告 `:108` F 组 icon 尺寸 14.00px(n=604 全量) vs 头条产物 `model_input_target_sizes.icon = {samples:87, median_px:12.0, below_16px:64, below_16px_rate:0.7356}`；报告 `:125` 的「到中心 13.9px」在 wave1c/wave2 均 0 命中，wave1c 实为 27.8px，13.9 出自 `runs/v1-wave1b-console.log:65`
11. 【验收线】MAJOR 报告 `:234` 索引的 `runs/g2-eval-crop192/sspro-report.json` 不存在（该目录只有 model-requests.jsonl 177 行、image_tokens 中位 49）→ `plan:199` 要求的 183 条配对 mid-p McNemar 未做成；`runs/m9-kfold/kfold.json` 的 `replay_check={on_demand_matches:165, consistent:false}` 与其 note 矛盾，报告未提，且该 K 折输入是旧跑批。**⚠️ Lead 抽验：此条误报 —— `runs/g2-eval-crop192/sspro-report.json` 实为 763576 bytes 存在（见 §一 #1）。但「K 折输入是旧跑批」这半句仍需核。**
12. 【口径】产物 `comparison.paired.always_vs_coarse_only.delta_pp = -14.754098360655737`、`delta_ci95_pp = [-21.85792349726776,-7.650273224043716]`、`mcnemar_midp_p = 9.021490107130603e-05`；报告 `:17/:84/:158` 写成「+14.75pp / CI [+7.65,+21.86]」未标方向。image token：always `per_target 3539.273224043716`、coarse_only `1788.3879781420765` → 拆账应为 1788.39+1750.89，报告 `:140` 写「1925+自适应crop」、`:142` 写「crop ≈1334」
13. 【口径】`runs/g2-eval-final/model-requests.jsonl` 仅 40 行、latency 中位 16.70 s（报告 `:142` 写 ≈15 s/次）；「183 条三臂约 70 分钟墙钟」无产物依据（g2-eval-cached 367 行 latency 合计 120.7min、4 并发≈30.2min；g2-eval-final `elapsed_seconds=202.7`）。`runs/m2-temperature-probe` 三轮 `text_consistency.modal_rate` 均为 0.1，报告 `:151` 与 `:167` 对「原文一致率」写法不一致
14. 【引用】8 类禁引数在 `closure-report.md` 全文 grep 无命中（报告 `:6` 自述成立）；违规 7 处：产物路径不存在；18.67px 实为 `.scratch/icon_size_report.py` 的 2048 档行却被安给从未测过的 2016×1260；icon 尺寸出处 `.scratch/` 不在 `plan:376` 允许集合且脚本不落盘；「上线字节含 temperature:0.0」无产物（`runs/` 下含 temperature 的 jsonl = 0）；引用的 `divergences_px` 无 0.00；头条 55.74% 缺 Wilson CI 与重加权（产物有 `wilson_left=[0.484967,0.627427]`）
15. 【产物核对通过项】`g1-eval-330 always=82/183=0.44808743169398907`、`coarse_only=76/183`；`g2-eval-final always/on_demand=102/183=0.5573770491803278`、`coarse_only=75/183`、icon 0.41379/text 0.6875；crop-geometry 192→42.08%(missing 36.07%)、512→71.04%、0.5x→84.15%(missing 14.21%, tol 839/522)；telemetry crop_opened=176/contains=140(79.55%)/fake_hits=20/fallback_cause={no_output:25}；model_calls_per_target 2.0/2.0/1.0383；wave1c auto 0/3/2/0 den5、landing tiers A2/B0/C0/undec2、near_miss band36 rescues 18；wave2 auto 3/2/1/1 den7=42.86%、tiers A1；v1-calib hit_rate 0.9/1.0/1.0/0.9/(s4 SSL 失败)/1.0，median 1.37~3.02、p90 4.85~29.70；token probe 1536×960 exp1925/obs1927/overhead2/equal true、2044×1260 obs3242≠exp3285(server_kept 3240)

### nextSteps（6 条）

1. 修报告的 8 处「未达成写成达成」：E 组补回「阈值取值」「可迁移性」两行并标未达成；A 组改为「2.87 px（≤4 ✓）」并指向 `denominator_pair_divergence_px`；G 组可判率改述为「四档分类率 100%，产物字段 `visual_step_decidable_rate=0.0`」，并把 13.9px 改成 27.8px + 换出处或补 wave1b；④ 行加口径句并标明回落率 14.2% 未达 ≤10%；B 组补「36px 不在空白区（18/81 miss 落在带内）」；F 组并列 12.0px(n=87)；删掉 `runs/g2-eval-crop192/sspro-report.json` 索引或补跑；把 kfold 的 `replay_check.consistent=false` 与「输入是旧跑批 g1-eval-330」写出来
2. 修 9 处口径不一致：Δ 方向按产物口径（右臂−左臂）标注；image token 拆账改 1788.39+1750.89；回落率统一 25/176=14.2%；延迟中位改 16.70s 并删掉无依据的「70 分钟墙钟」；原文一致率统一 10%；含住率差异补第 2 个口径（`crop_geometry.py:228 target_radius_px=0.0` vs `channel.py:677` 传粗框半径）；H 组 3765 注明与本轮几何不同源
3. 修 7 处引用纪律破口：18.67px 归回 2048 档并把 2016×1260 标「推算未测」；3240/4574 标推算来源；icon 尺寸改引产物字段或落一份 `runs/` 产物（替掉 `.scratch/icon_size_report.py`）；temperature 改引 `model/client.py:208`；头条补 Wilson CI [48.50%,62.74%] 与重加权值与抽样构成提示
4. 修 1 个实现 BLOCKER：`ground/prompt.py:437-455` 的 `parse_optional_point` 加 `is_truncated` 检查（截断→显式失败/干净回落），并补「精定位截断→回落粗定位」回归用例；顺带修 `parse_coarse_target` 的「答案行存在但非 JSON 时回落扫全文」
5. 修 2 个实现 MAJOR：`calib/calibrate.py::estimate_model_input_from_tokens` 收紧为相等断言并接进 `e2e/visual.py` 生产链（服务端再缩图要拦）；`holdout.tune_gate_params` 同时写 `deviation_tolerance_per_mille`，或把 260.4 明确标为旧量等价值只作历史参考；`eval/stats.py` 补普通精确二项与 χ²+连续性校正，并在评测台输出 MDE（6.55→2.23pp）
6. 需要人拍板/提供的输入：H 组费用折算用的端点计价表；2016×1260 vs 1536×960 的取舍（icon 密度 vs token 预算）；是否补跑 g2-eval-crop192 的 183 条配对对照（真调模型，约 1 小时墙钟）；是否重跑 v1-calib s4 补齐 6/6

---

## 三、这只复核的边界（读它的人必须知道）

1. **它是只读复核，没有改任何东西。** 上面 6 条 nextSteps **一条都没落地**（会话在第二遍就 402 死了）。
2. **它有 1 处已确认的误报**（§一 #1）。其余 5 条抽验正确，未抽验的 10 条按它给的 file:line 与产物路径**可逐条复核**。
3. **它的头条没问题**：`g2-eval-final` 的 **55.74%（102/183）** 在产物里能对上；`coarse_only 75/183`、`icon 0.41379`、`text 0.6875` 也对得上。**它否掉的是「这 8 组验收线全部达成」这个说法，不是「成绩不存在」。**
4. **它没跑第二遍**（`maxRounds=3` 只完成 1 遍）。按原计划还差 2 遍对抗式复核才算结束。

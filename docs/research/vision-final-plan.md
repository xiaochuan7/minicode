# 视觉主功能 · 最终收口方案

> **范围**：把 `docs/research/` 下 10 份调研报告（r-r1 ~ r-r10）+ `vision-root-cause.md` 的诊断结论，收口成一份**可直接开工**的视觉主功能方案。
> **本文件只写方案**：不建工单、不改代码、不改已有工单的验收标准（遵守 `AGENTS.md`「工单与验收标准的改动时机」）。
> **仓库根**：`C:\Users\22197\Desktop\minicode`（下文所有路径相对它）。
> **产物路径**：`docs/research/vision-final-plan.md`。
>
> **证据强度标注**（沿用 `vision-root-cause.md` 的口径，全文一致）：
> 【实测】= 本机自己量出来的（带样本量与产物路径）·【一手】= 官方仓源码 / 官方 README / 官方榜 JSON / 论文原文正文（带 URL）·【推断】= 基于以上材料的推理，尚未验证。

---

## 一句话结论

**视觉主功能卡在我们自己的三处接线与判据上，不是模型能力上限，也不是环境问题**：接线错 **65.3%**（整屏压到 1280×800 + 固定 192 px 按点裁，183 条里 **66 条 = 36.1%** 的 crop 窗口压根没框住目标）+ 判据错 **22.8%**（离 crop 窗口中心 > 50 px 就回落，**43.7%** 的落点被自己的判据丢掉，其中 23 条是「模型答对了、我们扔掉」）+ 模型上限 **5.0%**（5/101，窗内模型那一指命中 61/73 = 83.6%）。

**改完能到多少（必须分清实测与推断）**：
- **判据这一条是实测**：同一批 183 条上 **44.81% → 47.45%**（取消偏离回落，t=∞ 平台）/ **47.71%**（t=96 峰）/ 换参考点为粗定位点时 **48.66%**（t=80）【实测，d4 40 次重放 + 30 个不同样本】。
- **接线两条是推断**：窗口 512 → 「完整含目标」率 **42.1% → 71.0%**（n=183，**离线反事实，不是实测增益**）；icon 在原生像素密度下 **35%（7/20）→ 85%（17/20）**，配对 McNemar 精确 **p=0.0063**【实测，n=20】。
- **合成目标区间 47~55%**，判定方式不是看分数，是看**配对 mid-p McNemar 的 Δ 95% CI 下界 > 0**（183 条 MDE 6.55 pp，全量 1581 条 MDE 2.23 pp）。
- **口径必须先说清**：对外头条数现在是 **44.8%**（n=183，bbox 命中口径，Wilson 95% CI **[37.8%, 52.0%]**），重加权后 **42.5%**（CI [35.5%, 49.7%]）——**44.8% 是抽样构成偏高的值，不是真值**。
- **真机侧至今零证据**：wave1 **0/5**、wave2 **3/7 = 43%**，且 wave1 的 3 个 fail **没有一个是「模型指错」**（是 `Shell_TrayWnd` 找不到、`ActionResult` 无 `result`、验收期望「判不了」）【实测，`runs/n-wave1/e2e-report.json` / `runs/n-wave2/e2e-report.json`】。真机落点验收口径（`LANDING_TOLERANCE_PX = 2.0`，到控件中心的绝对距离）**在数学上不可能通过它要验收的东西**（找对元素的子集落点误差中位就有 8.9 px）。

---

## 诊断已定的根因

| 层级 | 占 101 个未命中 | 判据（样本量） | 证据强度 |
|---|---|---|---|
| **L2 协议/接线错** | **66/101 = 65.3%** | 183 条里 66 条 crop 窗口完全不含目标（36.1%）；同一粗点把窗口放到 512 → 完整含目标率 42.1% → 71.0%；icon 在模型输入图里中位仅 **13.25~14.5 px**，**216/352 = 61.4%** 的图标 < 16 px；同模型同提示词只改像素密度：35%（7/20）→ 85%（17/20），p=0.0063 | **强**（零调用精确重建 183/183 + 20 条配对 A/B） |
| **L1 判据写错** | **23/101 = 22.8%** | 偏离回落触发 **80/183 = 43.7%**（其中 deviation 78）；重放 18 条可救样本，精定位命中 12/18 = 66.7%，同时 12 条受保护样本里精定位也会命中 6/12 = 50% → 净 **+4.83 条 = +2.64 pp**；crop-scan 的 A/B 半差 7/7 同号、均值 −5.63 pp（SE 0.57 pp），独立交叉验证 −6.29 pp | **强**（40 次重放 + 离线重建 + 独立交叉验证） |
| **L4 模型能力上限** | **5/101 = 5.0%** | 窗内模型那一指命中 41/46 = 89.1%（完整含目标）、20/27 = 74.1%（部分含），合计 61/73 = 83.6%；窗外 0/30、实测 0/16；同提示词同图连发两次 0/4 原文相同、落点差 2.2~129.9 px；跨运行命中翻转 16.7%~26.2%（6 对运行） | **中**（n=73 / n=16 / 6 对运行；无公开对照实验） |
| **L3 环境/数据** | 0/101（不产生 SSPro 未命中） | 本机 1581 条标注只有 **939 张图**（缺 648）；183 条抽样构成与全量不符（OS 41.5% vs 全量 12.4%、CAD 4.9% vs 16.5%，CAD 命中 0/9）→ 重加权 44.8% → 42.5% | **中**（本机日志/产物） |

### 支撑「不是模型弱」的四条互相独立的判据

1. **换算链精确可逆**【实测，n=1581】：把 GT 框按模型输入空间缩放后喂进 `box_center → CoordinateAdapter.to_screen → full_screen_geometry`，1581/1581 条回到 GT 中心，最大误差 **4.5e-13 px** → 不可能有固定偏移或比例误差。
2. **协议 bug 必然是确定性的**【实测，n=4】：逐字相同提示词 + 逐位相同图像连发两次，**0/4** 原文相同，落点差 2.2 / 3.6 / 35.5 / 129.9 px。
3. **跨运行命中会翻转**【实测，6 对运行，n=36~42/对】：翻转率 16.7%~26.2%，落点差中位 26.7~68.2 px、p90 1416~1739 px，> 100 px 的差占 25.0%~40.5%。
4. **只改像素密度就改变结论**【实测，n=20 配对】：同一模型、同一提示词、同一解析器，原生分辨率 crop 17/20 = 85%，整屏缩图 7/20 = 35%，配对 McNemar 精确 **p = 0.0063**。

### 三处病灶的精确形态（决定施工清单）

| 病灶 | 现在的形态（源码位置） | 证据 |
|---|---|---|
| 接线① | 整屏缩到 **1280×800**（`src/minicode/model/image.py::model_input_size_for`），icon 原图物理边长中位 28~29 px → 模型输入图里只剩 13.25~14.5 px | 【实测】216/352 图标 < 16 px |
| 接线② | crop 窗口**固定 192×192 物理 px**、**以粗定位点为中心**、无 padding（`ground/channel.py:162-163`、`coord/crop_refine.py::plan_crop`）→ 容忍粗点误差仅 **±96 / ±96 px**；而粗点误差中位 55.9 px、**p90 1451 px、14.8% > 1024 px、max 3420 px** | 【实测】36.1% 窗口不含目标 |
| 判据 | **离 crop 窗口中心 > 50 px 即回落**（`ground/gate.py:194` `DEFAULT_DEVIATION_TOLERANCE_PX = 50.0`；判定在 `ground/channel.py:635-658`）→ 参考点是几何中心，不是粗定位点 | 【实测】触发 43.7%，其中 23 条是「答对被丢」 |

> ⚠️ **两条已定的口径更正**（写进施工清单时必须一起改，否则依据是错的）：
> ① `50 px` 引自 ZoomClick，但 ZoomClick 的量是 **‖相邻两轮预测之差‖₂**（原图空间），**不是「离 crop 中心多远」**【一手，ZoomClick §3.2.1，https://arxiv.org/html/2512.05941v1#S3.SS2.SSS1】；仓库已经算好的 `zoom_consistency_px`（`channel.py:727-732`）正是这个量，只是**没有被拿去当判据**。
> ② `DEFAULT_CONSISTENCY_TOLERANCE_PX = 128` 因「第二次独立粗定位」调用已被删除而**永远不可判定**，不参与任何判定 —— 别把它当第二个阈值引用。

---

## 最终的验收线

**全部写成可判定的数字。** 「现在」一列是本机实测值 + 判定口径 + 样本量；「目标」一列是这一轮要交的线；「怎么测」一列写清用哪个产物/脚本判。

### A. 视觉通道准入（标定闸门）

| 指标 | 现在 | 目标 | 怎么测 |
|---|---|---|---|
| `admit().available` | **恒 False**（6 次标定的模型落点误差 1.06~67.07 px，全部 > 1 px 硬阈值） | **6 次独立标定会话中 ≥ 5 次 `available == True`** | `python -m minicode.calib --rounds 5` 跑 6 次，落 `runs/calib-*` |
| 等价标度族落点分歧 | **2.87 px**（0~999 vs 0~1000，2560 宽，欧氏；x 轴 2.56 px）【实测，`src/minicode/coord/README.md:243`】 | **≤ 4.0 px**（= 2.87 px 留一倍余量，新增独立常量，不动 1 px） | 标定记录 `divergences_px` / `equivalent_scales` |
| 96 px 方块命中率（K=5 轮） | 无此指标（此前按 `max_error` 判） | **每次会话 ≥ 0.6** | 标定记录新增 `hit_rate` |
| 1 px 容差的语义 | 1 px 同时管「标度可分辨」与「模型指得准」 | **1 px 只管标度可分辨性**（判定容差 1 px / 越界容差 2 px / 落点等价容差 4 px / 探针命中半径 48 px，**四者互不共用**） | 审 `calib/tolerance.py` 常量表 |

### B. 落点口径与阈值（真机 e2e + 探针自检）

| 指标 | 现在 | 目标 | 怎么测 |
|---|---|---|---|
| 离线评测口径 | **已是官方 bbox 命中**（`eval/sspro.py:498` → `SCREENSPOT_PRO.hit` → 官方 `eval_screenspot_pro.py::eval_sample_positive_gt`）【一手，https://github.com/likaixin2000/ScreenSpot-Pro-GUI-Grounding/blob/main/eval_screenspot_pro.py】 | **不动**（44.8% 就是它算的） | — |
| 真机 e2e 落点判据 | `LANDING_TOLERANCE_PX = 2.0`（到控件中心的绝对距离，`e2e/wave1.py:38`、`e2e/wave2.py:55`）→ 找对元素的子集落点误差中位就有 **8.9 px**（n=76），≥50% 的「模型确实指对」会被判失败 | **三档**：A 命中 = 落点在目标控件矩形内（判过）；B 近失 = 出框但到矩形边界 **≤ 36 px**（= 24 CSS px @150%，只记档不判死）；C 错选 = > 36 px（判失败） | `e2e/checks.py::landing_in_target_rect` + wave1/wave2 报告三档分布 |
| `landing_error_px` 语义 | 真机侧是「到中心距离」（`e2e/real.py:489` 只返回中心点）；浏览器侧已经是「到矩形距离，框内 = 0」（`e2e/apps.py:115`） | **真机侧统一成「到矩形距离（框内 = 0）」**，两处口径一致 | 报告里标注口径版本（换了算法，历史 `landing_error_px` 不可比） |
| 探针自检 | `verify/test_01_text_and_probe.py:124` 的 `PASS (<=2px)` 与 `calib/probe.py::probe_hit_radius`（96 方块 → 48）**自相矛盾** | PASS = 「全部目标命中（落在方块内）」，与探针命中半径 48 px 口径一致 | 跑 `python test_01_text_and_probe.py`（真调 API，消耗额度） |
| 36 px 近失带的校准 | 目前只有外部锚点（WCAG 24 CSS px @150%），**未在本机 183 条上验过** | 用第 1 条埋点落下的 `dist_to_bbox` 扫 τ ∈ {5,10,20,50,100}，确认 36 px 落在 **8.9 px（找对）与 226 px（挑错）两峰之间的空白区** | `eval/sspro.py` 的新字段 + 离线扫阈值脚本 |

### C. crop 命中率（窗口是否框住目标）

| 指标 | 现在 | 目标 | 怎么测 |
|---|---|---|---|
| 「完整含住 GT 框」比例 | **42.1%**（= 183 − 66；36.1% 的窗口不含目标） | **≥ 75%**（离线反事实：窗口 512 → 71.0%；1024 与「当前图 50% 线性尺寸」待第 3 条脚本定数） | **零模型调用**：用已落盘的粗点 + GT bbox 直接重算各候选几何 |
| 容忍的粗点误差 | **±96 / ±96 px**（192 窗口） | **≥ ±512 / ±400 px**（1024×1024 / 当前图半宽半高，官方同款几何） | 同上 |
| 该指标的常驻性 | 一次性诊断数字 | **每次评测都报**（`crop_contains_target` + `crop_miss_reason` 落盘） | `eval/sspro.py` 报告字段 |

### D. 精定位产出率

| 指标 | 现在 | 目标 | 怎么测 |
|---|---|---|---|
| 「窗内无目标仍产出被采纳的点」（假命中） | **66 条（36.1%）** 的 crop 窗口不含目标，模型仍被迫给一个点，且只受 50 px 判据约束 | **归零**（模型能答「元素不在这张裁剪图里」→ 干净回落到粗定位） | 按「元素在 crop 内 / 不在」两桶比较「精定位点与真值距离」的 p90：「不在」桶应从 226 px 量级降到粗定位量级 |
| 精定位净效应（两段式 vs 只用粗定位） | **+3.3 pp**（82/183 vs 76/183），配对 McNemar **p = 0.238 → 判不出**；但在「粗定位已偏」的 107 条子集上 **+11.2 pp，p = 0.0005** | **主口径下配对 Δ 的 95% CI 下界 > 0**（不再靠子集才显著） | 全量 1581 条配对 mid-p McNemar + 配对 bootstrap |
| 被自己判据丢掉的正确答案 | 78 条 deviation 里 **23 条** 是「精定位已中、被回落覆盖」 | **0 条**（判据不再改落点） | 落盘「精定位原始落点」+「最终落点」两列，差集归因 |

### E. 门控行为

| 指标 | 现在 | 目标 | 怎么测 |
|---|---|---|---|
| 门控形态 | 2026-10-22 起门控已**降级为记录器 + 回落判据**（`AGENTS.md` 仓库速览）；现行实现是「离 crop 中心 > 50 px 即回落」 | **硬失败回落只保留三种**（`no_output` / `out_of_bounds` / `call_failed`）；**偏离判据降级为记录器**，不再改落点 | `ground/gate.py` + `ground/channel.py:635-658` |
| 判据的**量** | ‖精定位 − **crop 窗口中心**‖ | **‖精定位 − 粗定位点‖₂**（= 已有的 `zoom_consistency_px`），与 ZoomClick ε₁ 同量 | 同上 + 落盘两个量分别算 |
| 阈值的**表达** | 固定 50 px（随 crop 尺寸漂移：192 时 c=260.4、512 时 c=97.7） | **crop 宽度百分比**（`deviation_c1000 = ‖Δ‖₂ × 1000 / crop 宽`），换 crop 不用重调 | 落盘 `deviation_px` + `deviation_c1000` |
| 回落目标 | **粗定位点**（重放：被判丢弃的 30 条里精定位命中 18/30 = 60%、粗定位只有 12/30 = 40% —— 我们在强制使用更差的那一级） | **精定位点**（回落时不得降级到更差的一级） | 同上 |
| 阈值取值 | 50.0 | **t = 96**（曲线峰 47.71%）或 **取消**（平台 47.45%）；参考点换成粗点时 **t = 80 → 48.66%**。**在按 app 分组 K 折上折外选、折内评**，并报各折阈值分布 | A 半调参 / B 半只评一次（禁止 index%2 分半） |
| 可迁移性 | A 半选出的 t=96 在 B 半给 46.11%（= B@∞），比 B@50 的 43.17% 高 **2.9 pp**（**同一批 183 条的分半，不是独立样本**） | 在**全量 1581 的确认集**（1398 条，与调参集不重叠）上复现该方向 | 配对 mid-p McNemar + 配对 bootstrap CI |

### F. 图标召回

| 指标 | 现在 | 目标 | 怎么测 |
|---|---|---|---|
| icon 子集 click accuracy | **29.9%（26/87）**，text **58.3%（56/96）**，差 28.45 pp（16.89 pp 来自「窗口没框住」+ 11.56 pp 来自「框住后更偏」） | **≥ 45%**（n=87，配对判定；参照：同模型同提示词只改密度 7/20 → 17/20，p=0.0063） | 全量 1581 配对 mid-p McNemar，按 `ui_type` 分层 |
| icon 在**模型输入图**里的像素尺寸 | 中位 **13.25~14.5 px**（原图物理中位 28~29 px）；**61.4%（216/352）< 16 px**，最小 6.5 px | 中位 **≥ 16 px**（2048×1280 档 → **21.2~23.2 px**，< 16 px 占比 ≈ 0%） | 评测台随每轮输出「目标在模型输入图里的像素尺寸分布」 |
| 纯视觉下的 icon 候选召回 | 生产口径 3/87 = 3.4%；单词级 3.4%；全量 OCR 词 5/87 = 5.8%；**87 个 icon 里只有 7 个（8.05%）的 GT 区域与任何 OCR 词有像素交集**（text 侧同口径 51.0% → 68.75%） | **本轮不设目标**（结构性死结：目标区域本身没有文字）→ 改为**常驻指标**（recall@k 与「选对率」分开记） | 编号基准新增两个**分离**指标 |
| 编号分支白进 | **61/87 个 icon（70%）** 白进编号分支而命中 0 | 收紧前置条件（候选文字覆盖描述关键词 ≥ 0.3~0.4）；icon 进分支降到 32/87（ratio≥0.3，召回 3/32 = 9.4%）或 18/87（≥0.4），text 仍留 55/96（召回 37/55 = 67.3%） | 候选召回率 + 命中数；**总分不升，省调用** |

### G. 端到端（wave1 / wave2）

| 指标 | 现在 | 目标 | 怎么测 |
|---|---|---|---|
| wave1（Windows 设置 + Chrome） | **0/5**（新分母口径：通过 0 / 失败 2 / infeasible 3 / 人工代跑 1）【实测，`runs/n-wave1/e2e-report.json`】 | **≥ 2/5**，且**至少 1 条验收条款真正判过视觉落点**（用新的 bbox 命中条款，不是 ≤2 px） | 重跑 wave1 |
| wave1 三个 fail 的性质 | 全部**不是「模型指错」**：`Shell_TrayWnd` 找不到（定位耗时 0 ms）/ `ActionResult` 无 `result` / 验收期望「判不了」 | 执行器侧修完后，失败原因里**「模型指错」必须是一个可出现的类别**（现在它连出现的资格都没有） | 逐条读 `reason` |
| wave2（微信 / Electron / cc-switch） | **3/7 = 43%**（通过 3 / 失败 2 / infeasible 1 / 合规停手 1 / 人工代跑 2）【实测，`runs/n-wave2/e2e-report.json`】 | **≥ 5/7** | 重跑 wave2 |
| 视觉步骤的**可判率** | wave1 的 W-1 两步视觉定位 `ok=True`（26.3 s / 51.1 s），但**落点偏差「判不了」（没有控件中心真值）** → 真机视觉落点精度**至今零证据** | **100%**：每条走视觉通道的步骤都要么判出「落点 ∈ 目标矩形」，要么明确记 `undecidable` 并写清缺哪条真值 | wave1/wave2 报告新增「视觉步骤可判率」 |
| 落点真值来源 | 真机走 UIA 候选编号分支实测 **21/21 正确、误差中位 0.0 px / p90 0.5 px**；静态图结论**方向相反** | 真机视觉两段式的落点精度**第一次有可判条款**（不要求这轮就达标） | 同上 |

### H. 模型调用数与成本

| 指标 | 现在 | 目标 | 怎么测 |
|---|---|---|---|
| 每目标模型调用数 | **固定 2 次**（整屏粗定位 + crop 精定位） | **≤ 2.5 次/目标**（不许因为加门控/自一致而超过公开榜首量级：GroundCascade 平均 **2.572 calls/example**【一手，官方榜 Pro JSON】） | 请求日志按目标聚合 |
| 图像 token / 目标 | 整屏 1280×800 = **1334** + crop 192 = **49** → **1383** | 整屏 2048×1280 = **3404** + crop 512 = **361** → **3765（2.72×）**；若 crop 走 768 → 4203（3.04×）。**必须同时报延迟与费用**，不许只报准确率 | 图像 token = `ceil(W/28)×ceil(H/28)`（本机三处实测吻合：192→49、512→361、1280×800→1334） |
| 4096 档残余无文本率 | **0.56%（19/3404）**；2000 档 **8.60%（103/1198）**；编号分支 2000 档 10.87%~21.43% | 8192 档幂律外推 **0.03~0.07%**【推断】；并加 `stop_reason` / `content_block_types` / `text_len` / `parse_ok` 四个字段后**直读** | 请求日志新字段 + `output_tokens == max_tokens` 检测器（当前准确率 99.89% / 召回 98.37%） |
| 解析失败对分数的影响 | **0 pp**（183 条那轮 5 次无响应 → 3 条样本，三臂全是 miss；上界 1.64 pp） | 保持 0（此项**不是** 44.8% 的原因，别再花时间） | — |

---

## 施工清单（10 条，按依赖顺序）

> **每条都写清：改哪个文件 → 做什么 → 依据（引哪条报告的哪个 URL）→ 预期哪个数变多少 → 怎么验证。**
> 「归属票」只写**现有工单**，本轮不建新票（`AGENTS.md`：工单是唯一真值来源，但建票是另一轮的事）。

---

### ①【P0·地基】补埋点：让阈值曲线、crop 命中率、精定位产出率以后都能离线重算

| 项 | 内容 |
|---|---|
| **改哪些文件** | `src/minicode/eval/sspro.py`（`ArmOutcome` / `to_dict` 补字段）· `src/minicode/ground/channel.py::_plan_fields`（已算好但评测台丢掉的字段）· `src/minicode/ground/geometry.py:69::crop_plan_fields` · `src/minicode/eval/__main__.py`（落盘粗定位**原始回答** bbox+point）· `src/minicode/model/request_log.py` |
| **做什么** | 落盘 8 个字段：① `crop_contains_target`（crop 窗是否含住 GT bbox）② `crop_miss_reason`（out-of-frame / coarse-picked-wrong / out-of-bbox）③ `deviation_px`（**两个参考点各一份**：crop 中心 / 粗定位点）④ `refined_point_px`（**精定位原始落点**，此前不可恢复 → 「阈值附近的逐条归属有噪声」正是因此来的）⑤ `fallback_cause` 与「最终落点来自哪一级」⑥ 粗定位的**原始 bbox + point**（此前只有换算后的 `coarse_point`，推不出 `target_radius_px`）⑦ `dist_to_bbox`（落点到 GT bbox 的距离，框内 = 0）⑧ 图像 token 数 |
| **依据** | `vision-root-cause.md` P0-2（埋点不产生任何分数变化，但让 P0-1 的后续微调不再消耗模型调用）；r-r10 §8#6（把 36.1% 从一次性诊断变成**常驻指标**）；r-r2 §5.2（用 token 数反查「模型实际看到多大」，本机三处实测吻合）；r-r1 §4#5（`dist_to_bbox` 与 `hit` 并列落盘） |
| **预期哪个数变多少** | **0 个分数变化**。新增 8 个可常驻监控的指标 |
| **怎么验证** | 重跑一次 183 条三臂；`sspro-report.json` 里能看到新字段；用旧数据反算应复现「36.1% 窗口不含目标」与「43.7% 回落率」两个已知数 |

---

### ②【P0】把模型调用的随机性钉死：显式 `temperature=0` + 预算与日志修缮

| 项 | 内容 |
|---|---|
| **改哪些文件** | `src/minicode/model/client.py:154::build_request_body`（加 `temperature=0`）· `src/minicode/model/config.py`（新增可覆盖的 `temperature` 字段，缺省 0）· `src/minicode/ground/channel.py`（`DEFAULT_MAX_TOKENS` 4096→8192、`DEFAULT_NUMBERED_MAX_TOKENS` 4000→8192）· `src/minicode/model/client.py`（`DEFAULT_TIMEOUT_S` 120→180）· `src/minicode/model/request_log.py`（`JsonlRequestLog.write` 改成单次 write+flush 的原子追加，修 1/5259 的日志行撕裂） |
| **做什么** | 请求体显式带 `temperature=0`。现在 `grep temperature src/` = **0 命中** → 我们走服务端缺省（常见缺省 1.0）；这几乎可以解释「同图同提示词连发两次 0/4 原文相同、跨运行命中翻转 16.7%~26.2%」——**不是模型不可靠，是我们没关采样** |
| **依据** | r-r8 §2.1/§8#1（官方 `model_factory.py` 对**所有**模型统一 `model.set_generation_config(temperature=0, max_new_tokens=4096)`【一手，https://raw.githubusercontent.com/likaixin2000/ScreenSpot-Pro-GUI-Grounding/main/model_factory.py】）；`vision-root-cause.md` P2-8（4096 档无文本率 0.56%、编号分支 2000 档 10.87%~21.43%） |
| **预期哪个数变多少** | **π_d（不一致对占比）目标从 16.7~26.2% 降到 ≤ 10%**【推断】→ 183 条 MDE 从 8.6~10.6 pp 降到 **6.55 pp**；8192 档无文本率外推 0.03~0.07%【推断】。**分数本身预期不变**（这是方差控制，不是提分） |
| **怎么验证** | 同图同提示词连发 10 次：**原文一致率 ≥ 95%**、落点一致率 ≥ 95%（若 < 80%，说明网关忽略 temperature → 只能靠上游冻结，见 ⑨）；重启后日志行不再撕裂 |

---

### ③【P0·零模型调用】离线几何重算脚本：定 crop 窗口档位

| 项 | 内容 |
|---|---|
| **改哪些文件** | 新增 `src/minicode/ground/crop_geometry.py`（或扩 `ground/numbered_bench.py --crop-scan`）；只读第 ① 条落下的粗点与 GT bbox |
| **做什么** | 对候选几何 **192 / 512 / 768 / 1024×1024 / 1280×800 / 0.5×当前图**，直接算「crop 完整含住 GT bbox」的比例 +「两轴都含」的联合判定 + 容忍的粗点误差。**不调模型** |
| **依据** | r-r4 §6#1（这是所有后续改动的地基，零模型调用）；r-r2 §6.2（公开实现的末段工作面都在 **512~1280 px**：RegionFocus 0.5×0.5 屏 / ZoomClick 0.5 / CCF 半屏 / SS-Pro `reground.py` 1024²）；r-r10 §7 表（官方仓四个方法的裁剪参数，全部一手） |
| **预期哪个数变多少** | 把「36.1%」变成**每个候选几何一个实测数**（已知锚点：192 → 42.1% 含住率、512 → **71.0%**）；同时给出 1024 与「当前图 50% 线性尺寸」的数（本条给出后，⑤ 的档位不再靠猜） |
| **怎么验证** | 脚本输出表；192 与 512 两行必须与已知的 42.1% / 71.0% 对上，否则说明重建口径错了 |

---

### ④【P0·实测收益】判据改口径：量换成 ‖精定位 − 粗定位‖₂、回落目标换成精定位、阈值表达换成 crop 宽度百分比

| 项 | 内容 |
|---|---|
| **改哪些文件** | `src/minicode/ground/channel.py:635-658`（判定处）· `src/minicode/ground/gate.py:194-206`（`DEFAULT_DEVIATION_TOLERANCE_PX` 与 `DEFAULT_DEVIATION_SOURCE`）· `src/minicode/ground/channel.py:727-732`（`zoom_consistency_px` 已是这个量，接上即可） |
| **做什么** | ① **量**：`‖refined − coarse‖₂`，参考点从 crop 窗口中心改成**粗定位点**（crop 被 shift 时不再注入最多 ~96 px 的额外偏移）；② **回落目标**：从粗定位点改成**精定位点**（判据降级为**记录器**，只落盘不改落点）；③ **硬失败回落**只保留 `no_output` / `out_of_bounds` / `call_failed` 三种；④ **阈值表达**改成 `deviation_c1000 = ‖Δ‖₂ × 1000 / crop 宽`（192 crop 时 50 px ≡ c≈260.4），换 crop 尺寸不用重调；⑤ **改写 `DEFAULT_DEVIATION_SOURCE`**：注明「ZoomClick 的 50 px 定义的是两次预测的位移，不是离 crop 中心；LFPR 换算在 192 crop 上是 47.5 px（c≈247.5）」 |
| **依据** | r-r5 §1/§6/§8#1-#3（ZoomClick ε₁ 的原始定义与公式【一手，https://arxiv.org/html/2512.05941v1#S3.SS2.SSS1】；LFPR 三道几何护栏与相对量纲【一手，https://arxiv.org/html/2608.19553v1#S3.SS3】）；r-r1 §1.3（ZoomClick 的阈值是**行为一致性**阈值、且是**扫出来的**）；`vision-root-cause.md` P0-1 |
| **预期哪个数变多少** | **实测**：183 条 **44.81% → 47.45%**（t=∞ 平台）/ **47.71%**（t=96 峰）/ **47.53%**（t=80）；参考点换成粗点时 **48.66%**（t=80）。**回落率 43.7% → 目标 ≤ 10%**（其中 34 条白触发随 ⑤ 的窗口放大消失） |
| **怎么验证** | 三臂重跑 183 条，产出新的 t 曲线；**阈值必须在按 app/platform 分组 K 折上折外选、折内评**（禁止 index%2 分半 —— 已知它带 −5.63 pp 的构成差，SE 0.57 pp）；报各折选中阈值的分布（中位数 + 极差），不要只报一个数 |

---

### ⑤【P0·主收益】crop 窗口换几何：官方同款（比例优先 + 绝对下限），越界平移不缩小

| 项 | 内容 |
|---|---|
| **改哪些文件** | `src/minicode/ground/channel.py:162-163`（`DEFAULT_CROP_WIDTH_PX/HEIGHT_PX` = 192）· `src/minicode/coord/crop_refine.py:152::plan_crop` / `_fit_axis`（把 `CoarseTarget.bbox` 传下去）· `src/minicode/ground/numbered_bench.py`（crop 扫描从固定像素改成**比例扫描**） |
| **做什么** | 第一级窗口从固定 192 px 改成：**以粗定位框中心为中心**、窗口 = **当前物理帧的 50% 线性尺寸**、绝对下限 **512 px**（贴边时**向内平移补足，不缩小窗口**，与官方 `crop_to_center` 同语义）；裁剪**从物理帧 1:1 原生像素**取（不做有损缩放）；仍**只做一次**精定位，不迭代 |
| **依据** | r-r4 §1.3/§5（官方仓四个方法的裁剪参数逐字：ReGround `crop_size=[1024,1024]`、Narrowing ±0.25 归一化、Focusing 2×2 象限、ScreenSeekeR `min_crop_size=[1280,768]` + `max_aspect_ratio=2.5`【一手，https://github.com/likaixin2000/ScreenSpot-Pro-GUI-Grounding】）；r-r10 §2.1/§2.2（`view_bbox_to_real_bbox` 归一化 viewport 换算 + 贴边补足代码可直接抄）；r-r2 §6.2（末段工作面下限 512~1280 px）；r-r9 §3（公开实现的末段工作面都在 512~1280 px，**我们 192 px 低于所有公开下限**） |
| **预期哪个数变多少** | crop 含住率 **36.1% → 目标 < 15% 未含**（离线重算由 ③ 给数）；容忍粗点误差 **±96 → ±512/±400**；**总分的增益未实测**（官方同条件 512→1024 = +15.1 pp 是 OS-Atlas-7B 的条件，【二手】Table 5/6，本轮只作幅度参照不作依据） |
| **怎么验证** | 先用 ③ 的脚本定档位，再在 183 条上做**配对**（复用同一次粗定位回答，只换 crop 尺寸）→ 配对 mid-p McNemar。⚠️ **必须与 ④ 一起判**：只放大窗口而不改判据，净收益会被回落吃掉 1~3 pp |

---

### ⑥【P0·icon 主战场】整屏输入提到 2048×1280（**先探针，再改**）

| 项 | 内容 |
|---|---|
| **改哪些文件** | `src/minicode/model/image.py::model_input_size_for`（现在只按宽度挑整数因子，5120×1440 → 1280×360 因子 4）· `src/minicode/calib/calibrate.py::estimate_model_input_from_tokens` · `src/minicode/model/request_log.py`（图像 token 单独落盘） |
| **做什么** | ① **先探针**：把 token 反查从「±28 px 的盒子」收紧成**相等断言** `observed_image_tokens == ceil(W/28) × ceil(H/28)`（相等 ⇒ 服务端没有再缩；不等 ⇒ 该次请求的坐标系不可信）；送一张 **2048×1280** 的图，看图像 token 是否恰为 **74×46 = 3404**。② **再改**：整屏统一缩到 **2048×1280**（`2560/2048 = 1600/1280 = 1.25`，**双轴同为 0.8**，换算链仍是单一常数，16:10 不形变）。③ 探针若显示服务端会二次缩放 → 退回 1536×960，或改 2×2 **原生分块**（每块 1280×800，原生像素、整数偏移） |
| **依据** | r-r2 §3.2/§6.1/§7#1-#2（Anthropic 官方 quickstart 的硬约束：**服务端会再缩 → 模型在一个我们没观察过的空间里出坐标 → ~14% 系统性点击漂移（16:10 MacBook 屏）**【一手，https://github.com/anthropics/claude-quickstarts/blob/main/computer-use-best-practices/computer_use/image.py】；28 px 网格在本端点成立已由本机三处实测升级为一手）；r-r6 §6#3（官方通用 API grounding 协议 `models/gpt4x.py`：**原图 base64 直送、不 resize**、temperature 0、0~1 归一化 bbox【一手，https://raw.githubusercontent.com/likaixin2000/ScreenSpot-Pro-GUI-Grounding/main/models/gpt4x.py】） |
| **预期哪个数变多少** | icon 在模型输入图里的尺寸 **13.25~14.5 px → 21.2~23.2 px**，**< 16 px 占比 61.4% → ≈ 0%**；图像 token 1334 → **3404（2.55×）**；icon 子集 click accuracy **35%（7/20）→ 85%（17/20）**【实测，n=20 配对，p=0.0063】——**总分增益未实测**。⚠️ 85% 含 crop 中心偏置（7 条里 4 条答在中心 5 px 内）→ **是上界** |
| **怎么验证** | 一次探针（期望图像 token 恰 3404）+ 评测台输出「icon 在模型输入图里的像素尺寸分布」；**必须在同 token 预算下重测基线**，否则新旧不可比 |

---

### ⑦【P1】提示词协议：粗定位删 `point`、加 `text` 自报与负出口；精定位加「没找到」逃生口

| 项 | 内容 |
|---|---|
| **改哪些文件** | `src/minicode/ground/prompt.py::coarse_prompt`（L114-138）/ `refine_prompt`（L141-157）· `src/minicode/ground/channel.py::_locate_by_point` 的 refine 解析处（新增 null 判定）· `src/minicode/ground/channel.py::box_center`（L227-246）· **同步** `src/minicode/calib/prompt.py::image_prompt`（L91-115） |
| **做什么** | ① 粗定位**删掉 `point` 字段**（官方全部是「每对象一个几何」；`box_center` 本来就优先 bbox，`point` 是死字段 → 删是纯赚）；② 粗定位**加 `text` 字段**（模型自报它选中元素上的可见文字）+ **负出口**（元素不可见时答 `{"bbox": null, "text": ""}`）；③ 精定位**加 `{"x": null, "y": null}` 逃生口**（元素不在 crop 里时干净回落到粗定位，而不是接受一个假点）；④ 解析器照抄「答案行优先 + 截断 = 0 分 + 松散写法仅在答案行接受」；⑤ **必须同步改标定探针提示词**，否则标定测的就不是生产那条约定，分母来源当场失效（C-04 纪律） |
| **依据** | r-r3 §5.1/§5.2/§6#1-#5（官方 `gpt4x.py` 的负出口 `"Target not existent"` 与 0~1 归一化【一手】；EvalScope 的答案行解析纪律【一手，https://github.com/modelscope/evalscope/blob/main/evalscope/benchmarks/screenspot_pro/screenspot_pro_adapter.py】；**改提示词必须同步改标定探针**）；r-r6 §6#4（负出口把「窗外目标」16/16 坐标合法 0/16 命中变成可判 negative） |
| **预期哪个数变多少** | **精定位「假命中」归零**（36.1% 未框住目标的样本不再产出一个被 50 px 判据误放行的点）；**几何歧义归零**（bbox 与 point 互相矛盾）；`text` 自报把「挑错元素」从静默错变成**可检出的错**（目标：可检出比例 ≥ 50%，**推断**；落点准确率**推断** +3~6 pp）。落点准确率**预期不变或微升**（坏点被换成粗点） |
| **怎么验证** | 183 条配对，分「元素在 crop 内 / 不在」两桶比 p90；`text` 不匹配的样本里「重问后命中」vs「不重问」的对照（配对 McNemar） |

---

### ⑧【P0·真机可判】落点验收口径换 bbox 命中 + 近失带（三档）

| 项 | 内容 |
|---|---|
| **改哪些文件** | `src/minicode/e2e/wave1.py:38` + `wave2.py:55`（`LANDING_TOLERANCE_PX = 2.0` → `LANDING_HIT_RULE = "bbox"` + `LANDING_NEAR_MISS_PX = 36.0`）· `src/minicode/e2e/checks.py`（新增 `landing_in_target_rect(evidence)`，保留 `landing_within` 仅供探针/标定）· `src/minicode/e2e/real.py:489::_resolve_truth` 与 `src/minicode/e2e/tasks.py:72`（真值扩成「中心点 + 控件矩形」）· `src/minicode/e2e/apps.py:115::_landing_error_px`（作为范本，真机侧统一成同一语义）· `src/minicode/verify/test_01_text_and_probe.py:124` |
| **做什么** | 真机 e2e 落点条款从「到中心 ≤ 2 px」换成三档：A 命中（落点在目标控件矩形内，判过）/ B 近失（出框但到矩形边界 ≤ 36 px，只记档不判死）/ C 错选（> 36 px，判失败）。真值从「只有中心点」扩成「中心点 + 矩形」，`landing_error_px` 改成「到矩形距离（框内 = 0）」。探针自检的 PASS 判据改成「全部目标命中（落在 96 px 方块内）」，与 `calib/probe.py::probe_hit_radius` 对齐 |
| **依据** | r-r1 §2.4/§3/§4（官方评测脚本判的是「点在不在目标框里」，**没有任何绝对像素容差**【一手，https://github.com/likaixin2000/ScreenSpot-Pro-GUI-Grounding/blob/main/eval_screenspot_pro.py】；WCAG 2.2 SC 2.5.8 的 24 CSS px（间距例外用的正是 24 CSS px 直径圆）在本机 150% DPI = **36 物理 px**，语义正好是「近到可能激活了隔壁控件」【一手，https://www.w3.org/TR/WCAG22/#target-size-minimum】；本仓 `e2e/apps.py` 的 docstring 已经写明「落点落在目标矩形里这条仍然成立，且才是这里有信息量的检查」）；r-r10 §1.1（官方 bbox 命中是闭区间、无容差） |
| **预期哪个数变多少** | **离线 44.8% 不动**（评测台本来就是 bbox 命中）。真机侧：从「几乎永远判失败」变成 A/B/C 三档**可判**；条款可判率 0 → 100%（每条视觉步骤要么判出、要么明确记 `undecidable`） |
| **怎么验证** | wave1/wave2 重跑，报告出三档分布。⚠️ **换算法后历史 `landing_error_px` 不可比**，报告里必须标口径版本 |

---

### ⑨【P0·让一切可判定】评测台改配对设计 + 统计模块 + 全量/确认集切分

| 项 | 内容 |
|---|---|
| **改哪些文件** | 新建 `src/minicode/eval/stats.py`（`mcnemar_midp` / `wilson_ci` / `paired_bootstrap_delta` / `mde`）· `src/minicode/eval/sspro.py::compare_arms`（加 (coarse_hit, always_hit) 2×2 + 精确 McNemar + bootstrap CI + 不一致率 d + 按 coarse 对/错分层）· `src/minicode/eval/__main__.py`（新增 `--full` / `--confirmation-split`，落 b/c/m/Δ/p/CI）· `src/minicode/eval/holdout.py`（切分从 index%2 改成**按 app/platform 分组 K 折**，`DEVIATION_CANDIDATES` 扩到 {24,32,40,48,56,64,80,96,128,∞}，沿用「悲观下界最大、并列取最大容差」）· `src/minicode/ground/numbered_bench.py`（删掉 `_run_crop_scan()` 的 `half_a/half_b = index%2`，改成**同一条样本上比较两个 crop 尺寸**）· `src/minicode/eval/sspro.py::MemoizingModelCaller`（记忆缓存**落盘** + 每条回答打**阶段标签** coarse / whole2nd / refine） |
| **做什么** | ① 主检验 = **配对**：同批样本、复用**同一次上游回答**、只换被测那一步；② 检验 = **mid-p McNemar**（双侧 α=0.05），普通精确二项与 χ²+连续性校正当对照；③ Δ 的 95% CI 用**配对 bootstrap（2000 次，按 task 聚类）**；单臂区间用 **Wilson 95% CI**；④ 全量 1581 条为确认集（183 条调参、1398 条确认，不重叠） |
| **依据** | r-r8 §4.1/§6（mid-p 优于普通 exact conditional【一手，https://europepmc.org/articles/PMC3716987】；McNemar 的用法与前提【一手，https://www.itl.nist.gov/div898/software/dataplot/refman1/auxillar/mcnemar.htm】；Wilson 区间【一手，https://www.statsmodels.org/stable/generated/statsmodels.stats.proportion.proportion_confint.html】）；r-r5 §5.3（我们现有 index%2 切分带 −5.63 pp 构成差，必须改**分组 K 折**）；`vision-root-cause.md` 评测设计节（**为什么必须换设计**：现状把构成差当成了效应） |
| **预期哪个数变多少** | **主检验 MDE 从 6.55 pp（n=183, π_d=0.10）降到 2.23 pp（n=1581）** → **3.3 pp 从「不可判定」变「可判定」**；crop-scan 的 7 个尺寸从「−5.63 ± 0.57 pp 的构成差」变成**同一条样本上的配对比较**；单臂从「无区间」变成 183 条 **±7.2 pp** / 1581 条 **±2.45 pp** |
| **怎么验证** | ① `mcnemar_midp` 与 `scipy.stats.binomtest` 的普通 exact 在手算样例上对齐（差 < 0.01）；② 在 183 条上跑一次配对，报告里的 m 应 ≈ π_d×183（若 m 仍 > 30，说明冻结没生效）；③ 全量主检验能给出「Δ + 95% CI + p」，**CI 不跨 0 才写「改进成立」** |

---

### ⑩【P1】端到端执行器修 + wave1/wave2 重跑（把「零证据」变成「有条款可判」）

| 项 | 内容 |
|---|---|
| **改哪些文件** | `src/minicode/exec/…`（找任务栏窗口改用仓库现成的 `find_taskbar_hwnd()`，`FindWindowW` 优先）· `ActionResult` 的 `result` 属性（wave2 的 C-2 崩在这）· `src/minicode/e2e/wave1.py` 的落点真值层（树里没有控件框时用什么当真值 → 由 ⑧ 的三档条款给出「无真值 → 记 `undecidable`」） |
| **做什么** | 修掉三个与视觉无关但让视觉无法被验收的执行器问题，然后重跑两波 |
| **依据** | `vision-root-cause.md` §7（wave1 三个 fail 的逐条 reason：W-3 `找不到任务栏窗口（Shell_TrayWnd）`、C-2 `'ActionResult' object has no attribute 'result'`、W-1「验收期望判不了」）；`docs/research/night-handoff.md` §4/§7（落点偏差「没有真值」→ 真值口径要定） |
| **预期哪个数变多少** | wave1 **0/5 → ≥ 2/5**，且**≥ 1 条视觉步骤的落点条款被判过**；wave2 **3/7 → ≥ 5/7**；**「视觉通道真机落点精度」从零证据变成有验收条款可判**（当前**无法给分数预期**） |
| **怎么验证** | 重跑 wave1/wave2，逐条读 `reason`；新增「视觉步骤可判率」= 100% |

> **依赖关系备注**：④ 与 ⑤ **必须一起判**（只放大窗口不改判据，净收益会被回落吃掉 1~3 pp）；⑥ 的探针必须**先于**⑥ 的改动；⑨ 必须**先于**任何对外的「改进成立」结论；⑧ 必须**先于**⑩ 的 wave1/wave2 重跑（否则落点条款还是 ≤2 px，仍然判不了）。

---

## 评测设计

### 1. 为什么必须换设计（现状把构成差当成了效应）

- **构成差 C = −5.63 pp 真实存在**【实测，330 条 × 7 个 crop 尺寸】：7 个尺寸的 A−B 差 = −5.45 / −4.24 / −5.45 / −8.48 / −6.67 / −4.24 / −4.85 pp（**7/7 同号**），mean = −5.63 pp、sd = 1.51、SE(mean) = **0.57 pp** → 95% CI [−6.75, −4.51]。结构原因：7 个尺寸共用**同一次**粗定位回答（应答记忆端口只发一次），所以 C 是常数、只有精定位那级在独立重抽。
- **独立交叉验证**【实测，n=183】：把只由粗定位决定的 `coarse_only` 臂按同一次 index%2 划分投影 → A 半 37/96 = 38.5% vs B 半 39/87 = 44.8%，差 **−6.29 pp** → 与 −5.63 pp 独立吻合。
- **现状分半（作废）**：A 半 165 vs B 半 165，两批不相交、模型各自重抽 → MDE 1581 条也要 **4.96 pp**，永远判不出 3 pp。

### 2. 配对？子集放大？多少样本？MDE 多少？

| 项 | 决定 | 依据与数 |
|---|---|---|
| **比较方式** | **配对**（主力）：同批样本、**复用同一次上游回答**、只换被测的那一步 | 官方 eval 脚本的判定层零随机性（point-in-bbox）；噪声全部来自模型调用【一手，官方 `eval_screenspot_pro.py`】 |
| **检验** | **mid-p McNemar**（双侧，α=0.05）为主报；普通精确二项与 χ²+连续性校正当保守对照 | mid-p 在 9595 个场景里都没违反名义水平，而普通 exact 与渐近+连续性校正表现差【一手，Fagerland 2013】 |
| **区间** | 单臂 **Wilson 95% CI**；Δ 用**配对 bootstrap 2000 次、按 task 聚类**；多重比较用 **Holm 校正** | 【一手，statsmodels 文档】 |
| **主检验样本量** | **全量 1581 条**（本机有图 939 条；补齐缺失 648 张后再上 1581） | 183 条 MDE = 6.55 pp（π_d=0.10）；1581 条 MDE = **2.23 pp** |
| **要判多小的效应** | 配对下 **N ≈ 7.849 · π_d / Δ²** | 判 3.3 pp：π_d=0.05 需 360 条、**π_d=0.10 需 721 条**、π_d=0.20 需 1441 条；判 5 pp 需 314 条（π_d=0.10）；判 10 pp 需 78 条 |
| **子集放大（合法用法）** | **预注册的次要分析**：子集 S = **冻结基线里「粗定位已错」**（n=107/183）；被测步必须**严格位于**子集定义之后（下游） | 条件在**先于处理的变量**上 → 不构成 collider【一手，Hernán 2004，https://europepmc.org/article/MED/15308962】。**必须同时报总体分解**：`Δ = Δ_S·|S|/N + Δ_Sc·|Sc|/N`（若 Δ_S=+11.2 pp、S 外为 0，总体只有 +6.55 pp） |
| **不能做的子集** | **事后**按「跑完两臂看谁错得多再挑」 | winner's curse：低功效 + 柔性分析会系统性抬高效应【一手，Ioannidis 2008，https://europepmc.org/article/MED/18633328】 |
| **切分纪律** | **按 app / platform 分组 K 折**（绝不按 index）；折外选阈值、折内评；报各折阈值分布 | 我们现有 index%2 带 −5.63 pp 构成差；LFPR 的三层证据（回顾性 / 冻结迁移 / 前瞻图像不交）可抄【一手，https://arxiv.org/html/2608.19553v1】 |

### 3. 怎么隔离模型随机性

| 手段 | 做法 | 数 |
|---|---|---|
| **关采样** | 请求体显式 `temperature=0`（官方同款） | 目标：π_d 16.7~26.2% → ≤ 10%【推断】；验收：同图同提示词连发 10 次一致率 ≥ 95% |
| **上游冻结重放** | `MemoizingModelCaller` 缓存**落盘** + 阶段标签（coarse / whole2nd / refine）→ 只重放被测那一步 | 本仓 M23 实测：B 半 21 条里「两臂粗定位点不同 3 条（14.3 pp）」、「命中翻转 0 条」→ 不一致主要发生在粗定位层，冻结它能砍掉大部分不一致对 |
| **不靠固定 seed** | 官方的 `torch.manual_seed(114514)` 只作用于本地 torch 模型，对 API 通道无效 | 别指望 seed 能让 API 输出确定【一手，官方 `eval_screenspot_pro.py`】 |
| **不用多数投票提分** | 官方消融：多数投票把分数**掉到 46.8%**；票空间是连续坐标，打平/破平会把答对的坐标投成错元素 | 【二手，论文正文，未逐字核到该段】→ 只用多次采样**估 π_d 与稳定性**，不用它提分 |
| **样本独立** | 官方 1581 条**每条一张不同截图** → 配对间独立假设成立 | 【一手，论文摘要 + 官方脚本】 |
| **落进报告** | 抽样 seed（20251020）、模型版本串、请求温度、每条回答的阶段标签 | 缺一项，整份报告的数字不可复现 |

### 4. 数据集与成本

- **本地**：1581 条标注齐全（965 文件），但只有 **939/1581 张图**（缺 648；10 个 app 整组缺失 + powerpoint 缺 48）。
- **补齐**：hf-mirror 远端 1587 张全部可达（26/26 app），抽样 HEAD 一个缺失文件 → HTTP 200、**3.14 GB**。
- **成本**【实测】：**4.57 s/调用**（`g1-eval-330`：365 次 / 1668.7 s，workers=4）。补齐后首轮 1581 粗定位 + 2×1581 精定位 = **4743 次 ≈ 6.0 小时**墙钟；之后每个变体 1581 次 ≈ 2.0 小时。
- **前向影响**：④⑤⑥⑦ 全部是配对可复用上游的改动 → 每个变体只需重放被测那一步（1~2 次/样本），不是 6~9 次。

---

## 明确不做

| 这一轮**不做** | 为什么 |
|---|---|
| **引入 GUI 图标检测器**（OmniParser / Grounding DINO / Florence-2 / SoM / 任何分割模型） | ① `docs/spec.md` §12 **架构禁止**；② 唯一有官方 GUI 实测的检测器方案在 Pro 尺度上**自己就崩**：OmniParser 官方 logs **text 58.89% / icon 4.65%**【一手，OmniParser README + `docs/Evaluation.md`】；③ 早期检测器 **AGPL**、上游 YOLOv9 fork 的 LICENSE 未核；④ 6GB 显存 + 目标框平均占屏 **0.07%** 面积【一手，论文 §3.4】。**公开证据不支持「加检测器」这条路线** |
| **把两段式整体换成候选制** | 公开候选制的胜利几乎都被弱基座衬托（分母是 GPT-4o 的 0.8%）；对照我们的 41.5% 粗定位，**候选制相对「强直接定位」有没有增益，公开数据里没有**。候选制对本项目的真正价值是「不依赖粗点」，而不是「更高分」——但那属于下一轮的架构话题 |
| **给纯视觉通道补 icon 候选源**（本轮） | icon 的 GT 区域与任何 OCR 词零交集占 **92%（80/87）** → 文本候选源这条路对 icon 是**结构性死结**；补它需要检测器级候选源（见上一条） |
| **逐坐标中点融合**（LFPR 的 ½+½） | LFPR 的两个框指**同一 referent**，中点安全；我们「挑错元素」的中位误差是 **226 px**，此时中点落在**两个元素之间的空隙**，命中两边皆无 |
| **GUI-RC 式多采样投票当最终答案** | 公开增益**集中在框式模型**（+3.4~4.2 pp），**点式模型 ≈ 0 甚至为负**（UGround +0.00、InternVL3-8B −0.82）【一手，GUI-RC Table 1】→ 只能当参考点，不能当答案 |
| **多数投票 / self-consistency 当提分手段** | 官方消融掉到 46.8%；票空间是连续坐标，与 grounding 不对口 |
| **改坐标空间契约**（绝对像素 + 声明尺寸） | 「声明尺寸 + 绝对像素」正是 Anthropic 官方推荐；三种官方坐标契约在我们现提示词旁无可测差异（n=18，配对 9:9，符号检验 p=1.0）。**换算链已在 1581/1581 条上可逆到 4.5e-13 px** |
| **为「格式不符」加解析容错** | 全库 1115 条臂结果里格式不符 **0 次** |
| **把图上画编号（SoM）当解法** | 选号精度 10/12 vs 文字坐标 11/12、纯路径 31.3% vs 34.4%、icon 0/13 对 0/13，图像 token 相同（**0 额外成本、0 额外收益**）；且「模型能不能读懂编号」只在 GPT-4V 上被验证过 |
| **把 `coarse_prompt` 的 `point` 当信息损失源** | 18/18 条模型自报的 point 恰等于它自己的 bbox 中心（\|Δ\| ≤ 1 px）→ 丢掉 point 是恒等变换（但**留着它**会与 bbox 矛盾，本轮仍按 ⑦ 删掉） |
| **动 MCP / HTTP / CLI 对外形态、安全审批、隐私脱敏** | 瓶颈在感知层，不在对外形态；审批与脱敏的红线不动 |
| **建工单、改代码、改已有工单的验收标准** | 本轮只出方案文档（`AGENTS.md`：改 AC 只能在 agent 停下之后由人统一改） |

---

## 风险与未解

### 推断还没验（写进施工清单时必须标【推断】）

| 项 | 现状 | 怎么验 |
|---|---|---|
| **crop 窗口放大后的总分增益** | 只有离线反事实上界（窗口 512 → 含住率 42.1% → 71.0%），**不是实测增益** | ⑤ 落地后跑 183 条配对 |
| **整屏 2048×1280 是否被服务端透传** | 本端点只实测过「2560 宽 → 被缩到约 2048 宽」，**从未探过 2048 档** | ⑥ 的探针（期望图像 token 恰 3404） |
| **icon 85% 的无偏上限** | 臂 C 的 85% 含 crop 中心偏置（7 条里 4 条答在中心 5 px 内，离中心中位 2.5 px）→ **85% 应读作上界** | 需要「目标故意偏离 crop 中心」或「crop 中心放诱饵图标」的设置重测 |
| **`text` 自报字段的可检出比例** | 目标 ≥ 50% 是**推断**；icon/text 的指令画像几乎相同（功能动词 48.0% vs 40.2%），不保证模型能自报对 | ⑦ 落地后统计 |
| **2048 档的成本与延迟** | 图像 token ×2.55、单目标 token 1383 → 3765（2.72×）已算；**延迟/费用未实测** | 与准确率一起报，不许只报准确率 |
| **硬失败回落（no_output / out_of_bounds / call_failed）是否也会误伤** | 已知 183 条那轮 5 次无响应 → 3 条样本，三臂全 miss（影响 0 pp）；**新判据下这 3 条的表现未测** | ④ 落地后看这三条的落点来源 |
| **「点到相邻同类控件」的真机风险** | 放宽判据的代价里「受保护样本 10.5/21 会从命中变成未命中」已扣除，但真机上「点到邻居」**没有真值可判** | ⑧ 的 B 档（36 px 近失带）就是为这条准备的观测手段 |

### 改动之间可能冲突

1. **判据阈值 ↔ crop 尺寸**：阈值若还用固定 50 px，换 crop 尺寸后系数会漂（192 时 c=260.4、512 时 c=97.7）→ **必须与 ⑤ 同批改**（④ 的第 ④ 条就是为此）。
2. **④ ↔ ⑤**：只放大窗口不改判据 → 净收益被回落吃掉 1~3 pp；只改判据不放大窗口 → 34 条白触发仍在（判据无意义）。**两条必须一起判**。
3. **⑥ ↔ 标定链**：整屏输入尺寸变化会改标定探针的模型输入基准与「声明 = 所见」的断言（C-04 纪律）→ ⑥ 的探针结果必须回写标定记录。
4. **⑦ ↔ 标定探针**：改 grounding 提示词的措辞**必须同步改** `calib/prompt.py::image_prompt`，否则标定测的不是生产那条约定，**分母来源当场失效**。
5. **⑧ ↔ ⑩**：⑧ 换了 `landing_error_px` 的算法 → **历史记录不可比**，报告必须标口径版本；⑩ 的 wave1 验收期望措辞（「目标中心」指哪个中心）要与 ⑧ 的矩形口径一起改。
6. **⑨ ↔ ④**：④ 的阈值必须在 ⑨ 的分组 K 折上选（不是 index%2），否则阈值本身就带着 −5.63 pp 的构成差 → **⑨ 的分组切分要早于 ④ 的阈值定稿**。
7. **① ↔ 全部**：① 不落 `crop_contains_target` 与精定位原始落点，③/④/⑤ 的数都只能靠推断，不能复核。

### 数字可能过拟合的地方

| 风险 | 说明 |
|---|---|
| **+2.64 pp 是在同一批 183 条上算的** | t=96 峰（47.71%）比 t=∞ 平台（47.45%）高 **0.26 pp**，这个差是同批数据的噪声；A/B 半迁移只验证了「t=96 在 B 半高 B@50 **2.9 pp**」——**同一批 183 条的分半，不是独立样本**。对外只报 **t=∞ 的 47.45%**（平台，不挑峰） |
| **逐条归属有噪声** | 被丢弃那 78 条的**原始**那一刀的精定位落点未落盘（重放用的是同 crop 的新抽样）；偏离量跨运行可漂 **45 px** → 阈值附近的逐条归属不可靠 |
| **构成差污染** | 现状 D0 分半带 **−5.63 ± 0.57 pp** 的构成差；不换设计就会把构成差当效应 |
| **抽样构成偏高** | 44.8% 是 OS 41.5% / CAD 4.9%（CAD 命中 0/9）的抽样值；重加权真值 **42.5%**。**对外只报一个头条数**（bbox 命中、n=183），并同报 Wilson CI 与重加权值 |
| **单一模型的风险** | 所有结论都建立在 minimax-m3.1-flash-free **一个模型**上；「只改像素密度 35% → 85%」是 n=20 的配对，样本小；换模型后各条增益的**方向**大概率不变，**幅度**会变 |
| **公开数字的过度外推** | 「zoom-in 对照 +7~9 pp」来自**训过的 grounding 模型**（8 组同模型对照），我们是通用 API 模型 → 只能当**幅度参照**，不能当**收益预测**；官方 ReGround 的 512→1024 = +15.1 pp 条件是 OS-Atlas-7B |

### 至今未解（不是这轮能答的）

1. **粗定位的框为什么偏那么远**：粗点误差中位 55.9 px、p90 1451 px、14.8% > 1024 px、max 3420 px；已排除换算链（103/103 落点落在重建窗口内），但「模型在缩小的整屏上乱框」与「我们对框的解析有系统偏差」之间没有定论。
2. **失败组（107 条）的落点是「视觉相邻同类控件」还是随机位置**：每图只有 1 个标注，可用该判据的样本 **0 条**（无统计效力）。
3. **>100 px 档 dx +128.5 px 的倾向**：72 条样本、4 次独立运行分别是 +128.5 / +173.0 / +18.5 / +225.5（符号检验 p=0.013 / 0.33 / 1.00 / 1.00）→ 需 n > 500 才能判定。
4. **原生分辨率下 icon 命中的无偏上限**（见上表）。
5. **真机走 UIA 候选编号分支（21/21 正确、误差中位 0.0 px）与静态图结论方向相反** —— 真机视觉两段式的行为**完全未验证**，这是 ⑩ 存在的理由。
6. **本地缺失的 648 张图是否系统性缺失**（哪些 app 没下全、缺失样本的难度分布）→ 决定 183 条抽样的偏差能否被外推消除。
7. **「通用 API 模型同时当粗定位与精定位」这一格在公开文献里仍无对照**（官方榜 22 条里没有通用 API 行；GPT-4o 在公开实现里只出现在 planner 位与单发位）→ 我们的数没有公开标尺，只能夹逼。

---

## 引用纪律

### 只许引两组数

**A 组 · 本机实测数**：出处必须是 `docs/research/vision-root-cause.md` 或 `runs/` 下的产物，**每个数带样本量、判定口径、样本构成说明**（本文件里所有标【实测】的数都满足这条）。

**B 组 · 公开对照数**：出处必须是**官方 README / 官方仓源码 / 官方榜 JSON / 论文原文正文**，**每条带 URL**（本文件里所有标【一手】的数都满足这条）。

### 明确禁引（各线标【二手】且本轮未复核，引用前必须回源）

| 禁引的数 | 为什么禁引 |
|---|---|
| **Claude(Computer Use) 17.1 / Claude 3.7 27.7 / OpenAI CUA 23.4 / GPT5-minimal 18.5 / Seed-1.5-VL 60.9** | r-r6 直取官方榜 `results/screenspot_pro.json`（22 个条目）**找不到这五行** → 本仓既有记录来源不可复现 |
| **GPT-4o 0.8%（论文 Table 3）/ Claude 3.5 ~1.8%** | 论文 Table 2/3 本轮未一手取到（arXiv HTML 截断、PDF 不可抓） |
| **OmniParser 39.5% / 39.6%**（ScreenSpot-Pro 端到端） | README News 与 HF 模型卡口径不一，本轮未复核其中一条 |
| **UI-Zoomer 81.84% / 77.20%（+11.4× 延迟）、+13.4% / +10.3% / +4.2%** | 论文表格本轮未逐字核到（HTML 截断）——但「无条件裁剪可能有害」这个**方向**可用一手引文支撑 |
| **LFPR 的全部数字**（72.947% → 76.013%、Acc@0.9 55.788% → 61.142% 等） | 本轮未复核表体；只引其**方法**（护栏、三层证据、图像簇 bootstrap）与**公式**（z ≤ 0.35、γ=2.0） |
| **ZoomClick Table 11 的 50.7 px / 91.8% / 正确对中位 < 10 px** | 表体未读到（HTML 截断、PDF 不支持）；只引 **README 的 `--prezoom_px_thresh 50`**（一手）与 §3.2.1 的**公式**（一手） |
| **A11y-Compressor 的 20.0 px** | 它**不是**验收容差，是观测压缩流水线里「两个控件是否挨着」的空间聚类阈值；论文里没有任何像素级落点指标 |
| **ReGround crop 消融 512=25.1 / 768=34.2 / 1024=40.2 / 1280=40.1** | Table 5/6【二手】，只作幅度参照，不作依据 |

### 可引的 B 组（各线一手读到，附 URL）

| 结论 | URL |
|---|---|
| 官方判定 = 归一化点落在归一化 GT bbox 内（闭区间、无距离容差）；`point` 必须 0~1 | https://github.com/likaixin2000/ScreenSpot-Pro-GUI-Grounding/blob/main/eval_screenspot_pro.py |
| 官方通用 API grounding 协议：原图直送不 resize、temperature 0、max_tokens 2048、0~1 bbox、负出口 `"Target not existent"`、落点取框中心 | https://raw.githubusercontent.com/likaixin2000/ScreenSpot-Pro-GUI-Grounding/main/models/gpt4x.py |
| 官方对**所有**模型统一 `temperature=0, max_new_tokens=4096` | https://raw.githubusercontent.com/likaixin2000/ScreenSpot-Pro-GUI-Grounding/main/model_factory.py |
| 官方裁剪几何：ReGround `crop_size=[1024,1024]` + 贴边平移补足；Narrowing ±0.25 归一化、depth 3；Focusing 2×2 象限；ScreenSeekeR `min_crop_size=[1280,768]` / `max_aspect_ratio=2.5` / σ=0.3 / NMS 0.5 / planner=gpt-4o T=0；归一化 viewport 换算 `view_bbox_to_real_bbox` | https://github.com/likaixin2000/ScreenSpot-Pro-GUI-Grounding |
| 官方 ScreenSeekeR 存在性校验提示词（`is_target` / `target_elsewhere` / `target_not_found` + 重写指令）与 `auto_dilate_patch` | https://raw.githubusercontent.com/likaixin2000/ScreenSpot-Pro-GUI-Grounding/main/models/methods/screenseeker.py |
| 官方免标定置信信号 `‖p2 − crop 中心‖` 与 `CROP_RATIO=0.5` | https://raw.githubusercontent.com/likaixin2000/ScreenSpot-Pro-GUI-Grounding/main/models/zoom_consistency_router.py |
| 官方 ScreenSpot-Pro 榜全表 22 条（GroundCascade **85.0/85.1**、2.572 calls/example；**8 组 zoom-in 对照 +7~9 pp avg、icon +7~13 pp**）；**该榜无任何通用 API 模型行** | https://gui-agent.github.io/grounding-leaderboard/results/screenspot_pro.json |
| 官方 ScreenSpot-v2 榜（GPT-4o **20.1%**，唯一一条通用 API 行） | https://gui-agent.github.io/grounding-leaderboard/results/screenspot_v2.json |
| ScreenSpot-Pro 论文：1581 条、text 62.6%/icon 37.4%、目标平均占屏 **0.07%**、最佳端到端 18.9% → ScreenSeekeR 48.1%（+254%）、四种 coarse-to-fine 的裁剪方式 | https://arxiv.org/html/2504.07981v1 |
| ZoomClick：`--prezoom_px_thresh 50` / `--in_ratio 0.5` / `--in_min_crop 768` / `--in_depth 3` / `--patch_size 2`；UI-Venus-72B + ZoomClick = **73.1%** | https://raw.githubusercontent.com/Princeton-AI2-Lab/ZoomClick/main/README.md ｜ https://arxiv.org/html/2512.05941v1 |
| ZoomClick §3.2.1 的 ε₁ 定义（两次预测的欧氏像素位移、原图空间、k\* = argmin） | https://arxiv.org/html/2512.05941v1#S3.SS2.SSS1 |
| Anthropic：服务端会再缩 → 模型在我们没观察过的空间出坐标 → ~14% 系统性点击漂移（16:10 屏）；长边 ≤1568 / tile ≤1568；「自己预缩 + 记住尺寸」 | https://github.com/anthropics/claude-quickstarts/blob/main/computer-use-best-practices/computer_use/image.py |
| Anthropic 官方 zoom 语义：region 在整屏空间、裁剪放大填满图像预算、zoom 图不是坐标帧 | https://github.com/anthropics/anthropic-sdk-python/blob/main/src/anthropic/types/computer_zoom_input.py |
| WCAG 2.2 SC 2.5.8：目标 ≥ 24 CSS px（间距例外用 24 CSS px 直径圆）；意图是「不误激活相邻目标」，不是「点到中心」 | https://www.w3.org/TR/WCAG22/#target-size-minimum |
| mid-p McNemar 优于普通 exact conditional（9595 个场景未违反名义水平） | https://europepmc.org/articles/PMC3716987 |
| NIST McNemar 参考（b/c 2×2、b+c ≤ 20 用精确二项、连续性校正版） | https://www.itl.nist.gov/div898/software/dataplot/refman1/auxillar/mcnemar.htm |
| Wilson 区间与 mid-p 的实现入口 | https://www.statsmodels.org/stable/generated/statsmodels.stats.proportion.proportion_confint.html |
| 选择偏差的结构判据（条件在共同结果上 = collider） | https://europepmc.org/article/MED/15308962 |
| Winner's curse（低功效 + 柔性分析 → 效应被高估） | https://europepmc.org/article/MED/18633328 |
| GUI-RC：框式 +3.41/+4.17 pp、**点式 ≈ 0 甚至为负**；α=50 px「同一元素」尺度 | https://arxiv.org/html/2508.05615v1 |
| LFPR：三道几何护栏（z ≤ 0.35、IoU ≥ 0.25、面积比 0.5~2.0）、三层证据纪律、图像簇 bootstrap 10,000 次 | https://arxiv.org/html/2608.19553v1 |
| OmniParser 许可与规模：`icon_detect_v3` MIT-based（YOLOv9-E），早期 YOLOv8/Ultralytics 派生检测器 **AGPL**，caption MIT | https://raw.githubusercontent.com/microsoft/OmniParser/master/README.md |
| UFO² 的 CLIP 预筛输入是 **UIA 已给框的裁图**（`control_filter.py` / `system.yaml`），不是像素级候选生成器；`CONTROL_FILTER_TOP_K_ICON: 15` | https://github.com/microsoft/UFO |
| 官方通用 API 答案解析纪律（末行 `Answer: [x, y]`、截断 = 0 分、sent_size 归一化） | https://github.com/modelscope/evalscope/blob/main/evalscope/benchmarks/screenspot_pro/screenspot_pro_adapter.py |
| Anthropic 评测方法论（多 trial、pass@k / pass^k、20~50 个简单任务起步的大效应期） | https://www.anthropic.com/research/demystifying-evals-for-ai-agents |

### 三条混读禁令

1. **任务级分数 ≠ 点级 click accuracy**：OSWorld 的「整条任务是否完成」不能和我们的「点是否落在 GT bbox 内」并列。
2. **官方榜分数（自报）≠ 我们实测**：榜上条目名称里直接带 `-zoomin` / `conditional zoom-in` —— 「加不加 zoom」本身就是分数的一部分。
3. **口径版本要随数出现**：改了 `landing_error_px` 的算法后，历史值不可比；改了阈值口径后，44.8% 与 47.45% 属**不同口径**，不可画在同一根柱子上。

### 对外表述纪律（照抄公开榜首的写法，别自己发明）

- 头条只报一个数：**bbox 命中、n=183、44.8%（Wilson 95% CI [37.8%, 52.0%]）**，并同报**重加权 42.5%（CI [35.5%, 49.7%]）** 与「183 条抽样构成与全量不符」这一句。
- 门控收益的记账方式照公开榜首：`Training-free geometric consistency cascade; fixed 0.5 zoom; 1% self-consistency gate; 4% cross-model agreement gate; 2.574 average model calls/example` → 我们的写法是「**省了 X% 模型调用，精度不掉**」，不是「门控提升了准确率」。
- **不宣称**「我们超过 ScreenSeekeR 48.1%」（它落在我们的 CI 内）；**不宣称**「同口径落后 40 pp」（官方榜第一那行的 grounder 是训过的 32B/8B，我们没有同款模型）；**只说**「同架构范式（zoom + 一致性门控）已被公开做到 85%，我们处在该范式的早期」。

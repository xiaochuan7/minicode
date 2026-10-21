# 三个卡点的收口方案：crop 精定位 / 粗定位精度 / 候选源与门控

> 本文是 6 条调研线（crop-why-negative / coarse-accuracy / candidate-hybrid / gate-signal /
> e2e-multistep / api-vlm-ceiling）的收口。**只写方案，不改代码、不建工单。**
>
> 证据强度标注（与 `core-function-plan.md` 一致）：
> **【实测数字】**= 本机自己量出来的 · **【一手】**= 官方 README / 论文原文 / 官方文档 / 源码 ·
> **【二手】**= 榜单、博客、团队转述 · **【推断】**= 基于以上材料的推理，尚未验证。
>
> 仓库根：`C:\Users\22197\Desktop\minicode`（下文路径相对它）。
> 产物根：`runs/a2-eval-42/`（42 条 ScreenSpot-Pro 抽样，两臂）、`runs/b1-live-zh/`（真机 22 控件）、
> `runs/d6-wave1/`、`runs/d6-wave2/`（端到端）。
>
> ⚠️ **口径纪律（沿用项目规矩）**：「无条件 crop 整体 −0.3 pp」至今没有一手出处，
> 本文**不引用**，也**不引用**任何「−0.3 pp」变体。

---

## 一句话结论

**三个卡点里有两个是我们的接线错误，不是模型能力上限。**

1. **crop 精定位净负** —— 净负的不是 crop，是「**以粗点为中心的 crop + 前置门控**」这个组合。
   门控恰好只在「粗定位已偏」的样本上开精定位（那 10 条两臂落点逐条相同，+0.0 pp），
   却在「粗定位本来就对」的 32 条上**关掉**了本该有效的精定位（13→21 命中，+25.0 pp）。
   【实测数字】同批 42 条：门控开 38.1% / 2.381 次调用；门控关 57.1% / 2.000 次调用。
2. **粗定位中位 61.6 px** —— 42 条里 6 条（14.3%）在**输出契约/解析层**就丢了，
   官方通用 API 协议是**问框、落点取框中心**，我们**问点**；坐标口径本身（声明像素尺寸）已经站在
   Anthropic 官方推荐做法上，不要动。
3. **候选源缺图标** —— 图标召回 0/13 是**候选源选错**，不是模型认不出图标：
   真机 UIA 同一批控件 22/22、落点误差中位 0.0 px / 最大 0.5 px。

**最小可行路径**：去掉前置门控 + crop 512→1024 + 偏离阈值 128→50 px + 粗定位出框 + max_tokens 加倍 +
UIA 候选源接入（UFO² 的 IoU 0.1 合并规则）。前三条**不改一行坐标换算代码**。

---

## 三个卡点的定性

### 卡点 A：crop 精定位净负（38.1% vs 57.1%）

- **卡点是什么**：同一批 42 条 ScreenSpot-Pro 抽样，同模型同口径，门控开 16/42 = **38.1%**，
  门控关 24/42 = **57.1%**；门控开还多花 16 次模型调用（100 vs 84 → 2.381 vs 2.000 次/目标）。
  【实测数字】`runs/a2-eval-42/caliber.json` 与 `caliber-always.json`。
- **根因**：**门控的触发条件与精定位的前提条件恰好相反**。
  门控用「两次独立粗定位不一致」当触发条件，而「不一致」正是「粗点已偏、目标大概率不在 crop 窗内」的
  代理量。按两臂逐条复算：
  - 门控判 `coarse_inconsistent` 的 **10 条**：两臂落点**逐条相同**、都是 3 命中（**+0.0 pp**）
    —— 精定位在这 10 条上被触发了，但产出与「不精定位」一样（目标不在窗内 → 模型盲选 → 5 条回落）；
  - 其余 **32 条**：门控关掉精定位 → **13→21 命中（40.6%→65.6%，+25.0 pp）**。
  【实测数字】本机 `sspro-report.json` 逐条复算（本轮已独立复核）。
  也就是说：**净负的 19.0 pp 全部来自「门控在精定位最可能成功的样本上关掉它」**。
- **这是我们的问题还是模型能力上限**：**我们的问题**（接线错）。
  同一模型、同一 crop 尺寸，关掉门控就 +19.0 pp；且公开消融里粗到细裁剪对**同一个 7B 模型**是
  **+21.3 pp**（OS-Atlas-7B 18.9 → ReGround 40.2）。【一手】ScreenSpot-Pro 论文 Table 4 / Table 5。
- **业界在这个能力上限上怎么绕过**：
  1. **无条件两段式**：整屏定位 1 次 + crop 内再定位 1 次，不做前置判断。
     【一手】官方 `models/methods/reground.py`：`crop_to_center(img, bbox, crop_size=[1024,1024])`，
     `ground_with_grounder` 各调一次；论文 Table 4 同模型 18.9 → 40.2。
  2. **粗到细失败时退回整屏重搜**，而不是退回一个**已知是错的**粗点。
     【一手】GUI-Lens（arXiv:2608.03270）§2.4 原文："Rejected proposals restore B0 and restart
     cropping from the full-screen observation"；论文 Table 1：MiniMax-M3 26.4→47.4、
     Claude Opus 4.7 57.4→82.3、GPT-5.5 74.8→87.9。
  3. **用「区域提议」代替「点」来定 crop 窗**：框比点覆盖面积大、且模型自述框里含目标。
     【一手】R-VLM（arXiv:2507.05673）原文："We then crop a region centered around this initial
     prediction for zooming, assuming this region as a region proposal"；Table 1：ScreenSpot 53.4→66.3。
  4. **几何护栏验收**，而不是再加一个置信门控。
     【一手】LFPR（arXiv:2608.19553）§3.3 三条护栏 + 中点融合；摘要原文："A genuine unguarded control
     ... underperforms the incumbent on every metric, showing the guard is load-bearing"。
- **我们能直接抄哪一段**：**第 1 段（无条件两段式）+ 第 4 段（几何护栏）**。
  第 2 段（退回整屏重搜）需要新增一次调用与一轮重规划，留到第二批。
  第 3 段（区域提议）依赖「粗定位出框」，是卡点 B 的改动，放第二批。

### 卡点 B：粗定位中位 61.6 px（p90 1144.5 / max 2510.6）

- **卡点是什么**：`coarse_error_px` count 只有 **36**（42 条里 **6 条没有粗定位点**），
  中位 **61.6 px**、p90 **1144.5 px**、max **2510.6 px**。【实测数字】`caliber.json`。
- **根因**：三层，按可改性排序：
  1. **输出契约层（14.3%）**：6 条里 2 条报告原文「整屏第二遍失败：HTTP 200 但本次没有文本输出
     （思考链可能把 token 预算吃光了）」——`max_tokens=2000` 被思考链吃光；4 条门控记 `coarse_invalid`。
     4 条门控记 `coarse_invalid`。
     【实测数字】`sspro-report.json` 的 `error` 字段。
  2. **协议层**：我们问「元素【中心】的位置」并**只解析点**；官方通用 API 协议是**问框、落点取框中心**。
     【一手】官方仓 `models/gpt4x.py` 提示词逐字："You are asked to find the bounding box of an UI
     element in the given screenshot corresponding to a given instruction.\nDon't output any analysis.
     Output your result in the format of [[x0,y0,x1,y1]], with x and y ranging from 0 to 1."；
     `click_point = [(bbox[0]+bbox[2])/2, (bbox[1]+bbox[3])/2]`；
     论文 Table 3 题注逐字："For models inferencing boxes, we consider the center point of the
     generated box as the prediction."
  3. **模型整屏定位能力（真上限）**：见 §三 的数字。
- **这是我们的问题还是模型能力上限**：
  - 14.3% 丢样本、问点不问框 → **我们的问题**。
  - 整屏长尾（p90 1144 px / max 2510 px）→ **部分能力上限**，但公开证据说明「减搜索面积」能显著改善它。
    【一手】ScreenSpot-Pro 论文摘要："Our experiments reveal that strategically reducing the search
    area enhances accuracy."
  - **我们的整体水平并不低**：门控关那一臂 57.1%（icon 40% / text 72.7%）**高于公开的通用 API 基线**
    （GPT-4o 0.8%、OpenAI CUA 23.4%、Claude 3.7 27.7%）。所以「通用 API 模型认不出图标」这个前提
    **不成立**，瓶颈在协议与参数，不在模型类别。
- **业界在这个能力上限上怎么绕过**：
  1. **粗到细（减搜索面积）**：ReGround 40.2 / ScreenSeekeR 48.1（同 OS-Atlas-7B 底座 18.9）。
     【一手】Table 4。
  2. **让模型选编号而不是瞄点**：GPT-4V 直接出 xy 在 Desktop Icon 只有 **11.8%**，
     换成「候选框 + 编号 + 局部语义」到 **63.6%**；SeeAssign 探针 0.705→0.938。
     【一手】OmniParser 论文（arXiv:2408.00203）Table 与正文。
  3. **投票取共识**而不是「多问一次然后否决」：GUI-RC（arXiv:2508.05615）K=64 采样、temp 0.5、
     top_p 0.95、点扩成边长 α=50 的方块、取票数最大连通块中面积最大者、落点取其中心；
     框式输出上 Qwen2.5-VL-3B 20.18→23.59、7B 19.80→23.97（**点式模型增益 ≈ 0**，必须与出框配套）。
  4. **降采样声明像素尺寸**（我们已做，不要动）：【一手】Anthropic 官方博客
     "When the coordinate space and the model's perceived image don't match, the model's predicted clicks
     land on a display scale different from the image it's actually seeing. This is the primary cause of
     click inaccuracy at high resolutions. The fix is straightforward: always downscale your screenshots
     to fit within these limits before sending them to the API."；长边 1568 / 1.15 MP 上限
     （Opus 4.7 为 2576 / 3.75 MP），推荐从 **1280×720** 起。
     我们缩到 1280×800（1.02 MP，长边 1280）在限内，且反查图像 token 1334 = 46×29 → 服务端实际看到
     (1288, 812)，没再缩。**这条已站在正确口径上。**

### 卡点 C：候选源缺图标 / 门控净负 / 端到端退步

这是三个独立问题，不要混着谈。

| 子问题 | 定性 | 一句话根因 |
|---|---|---|
| **图标召回 0/13** | **我们的问题**（候选源选错） | 图标没有文字，OCR 词框**原理上召不回**；真机 UIA 同一批控件 22/22、落点误差中位 **0.0 px**。【实测数字】`runs/b1-live-zh/live-report.json` |
| **门控净负 −19.0 pp** | **我们的问题**（判据选错） | 「再问模型一次算一致性」是公开对照实验里唯一被判为不划算的那类；公开同构实现 zoom-consistency 路由本身就是 **null result**（+0.8%，McNemar p=0.19） |
| **端到端退步 wave1 0/3 → wave2 3/6** | **混合**，四类根因各占一部分 | 读值层 1 条、候选层 2 条、执行器 1 条、环境/口径 3 条 —— **不是模型能力问题** |

- **端到端的四类根因（逐条从产物读出来）**【实测数字】`runs/d6-wave1/e2e-report.json`、
  `runs/d6-wave2/e2e-report.json`：
  - **读值层**：W-1.E「验收条款 W-1.E 不成立：scale_value 没有读到值」—— 从 UIA 树里读缩放百分比，
    而 UWP 树是空的（`src/minicode/e2e/real.py:414` `_record_scale`）。
  - **候选层**：E-1.1「覆盖失败：页面结构快照里没有与「低档」匹配的元素」、E-2.1「没有与「打开详情」
    匹配的元素」—— **候选源覆盖不到**，不是模型选错。
  - **执行器层**：W-3.1「动作失败（executor_error）：RuntimeError: 找不到任务栏窗口（Shell_TrayWnd）」——
    这是**我们自己的 bug**，同一台机器的 UIA 通道在 Shell_TrayWnd 上跑通过 22/22。
  - **环境/口径层**：WX-1 微信停在登录界面、W-2.x / CC-3.3 人工代确认、WX-3 模态弹窗。
- **业界在这个能力上限上怎么绕过**（端到端）：
  1. **验收读系统状态，不读界面树**：WAA 官方 5 条 Windows Settings 任务全部用 `exact_match` 读
     注册表 / PowerShell（`desktop_env/evaluators/getters/settings.py`：`Get-TimeZone`、
     `HKCU\...\CloudStore`、`PushNotifications\ToastEnabled`、`Control Panel\Desktop\WallPaper`、
     `StoragePolicy\2048`）。【一手】
  2. **UIA 覆盖不足时加一层像素候选**：UFO² 混合检测，UIA 为主表、视觉检测为附加表、
     IoU>0.1 的附加项丢弃；WAA 上 **>62% 的失败归因 Control Detection Failures**；
     CRR（UIA-only 失败被救回的比例）OmniParser 7.0%→Hybrid 9.9%（o1），OSWorld-W 0%→25.0%。
     【一手】UFO²（arXiv:2504.14603）§3.4/§6.3。
  3. **记分口径跟公开基准对齐**：OSWorld 只有三桶（正常题 / infeasible 题 **进分母**、
     只有 agent 声明 FAIL 才算成功 / 环境不可达按失败计），**不存在「跳过」桶**，
     **不存在「弹窗在场→交人→算通过」的先例**（UFO² `ASK_QUESTION=False`；
     AndroidWorld 声明 FAIL 后在可行任务上 = 0 分）。【一手】

---

## 卡点一：crop 精定位净负

### 为什么净负

见 §一 的分组复算。一句话：**门控把精定位预算花在了它最不可能成功的样本上**。
「两次粗定位不一致」= 粗点已偏 = 目标大概率不在 512 窗内 = 精定位只能盲选。

另一个被掩盖的事实：门控开那一臂的 **refine 只在 10 条上触发、只有 5 条产出落点**，
`fallback_rate = 50%`；门控关那一臂 refine 触发 40 条、产出 32 条落点、`fallback_rate = 20%`，
且 refine 落点误差中位 **12.5 px**（vs 门控开那一臂的 51.0 px，n=5）。
【实测数字】`caliber.json` / `caliber-always.json`。
**精定位在「目标确实在窗内」的样本上是有效的（中位 12.5 px）**，门控把这个预算掐掉了。

### 怎么解（可直接抄的参数）

| # | 改动 | 参数 / 措辞（可照抄） | 出处与强度 |
|---|---|---|---|
| 1 | **去掉前置门控** | `GateMode.ALWAYS` 成默认；门控只做**记录器**（decision 落盘，不再决定是否精定位） | 【实测数字】38.1%→57.1%、2.381→2.000 次/目标；【一手】官方 ReGround 无条件两段式（Table 4：18.9→40.2） |
| 2 | **crop 尺寸 512→1024** | `crop_size = 1024×1024`（物理像素，1:1 裁不缩放） | 【一手】Table 5 逐格：OS-Atlas-7B 512=**25.1**（全表最差）/ 768=34.2 / **1024=40.2** / 1280=40.1；UGround-7B 27.0/28.8/28.2/26.3；原文："OS-Atlas-7B achieves its best score with 1024×1024 crops, while UGround performs optimally with 768×768 crops" |
| 3 | **扫描集合方向反过来** | `docs/research/core-function-plan.md` §C-1 现计划 `{512,256,192,128}` → 改 `{768,1024,1280}` | 【一手】同上 Table 5；【一手】ZoomClick Table 8 通用 VLM 行随 min_crop 单调上升（65.2→72.3） |
| 4 | **偏离阈值 128→50 px** | `DEFAULT_DEVIATION_TOLERANCE_PX = 50.0`；参考点从 `coarse` 改成 **`plan.rect.center`**（crop 走 shift 语义时两者不同） | 【一手】ZoomClick Table 11：最优 **50.7 px**、分类准确 91.8%、正确对中位 **<10 px** / 错误对中位 **>50 px**；原文："motivating our adoption of 50 px as a unified decision boundary" |
| 5 | **回落目标改成整屏重搜** | 精定位失败 → **回到整屏重新定位**，而不是回到已知错的粗点 | 【一手】GUI-Lens §2.4："Rejected proposals restore B0 and restart cropping from the full-screen observation" |
| 6 | **提示词允许「图里没有」** | `refine_prompt` 增加一条可回答项：「这张图里没有名为 X 的元素」→ 触发回落 | 【一手】官方 `ground_allow_negative` 同一思路："If such element does not exist, output only the text 'Target not existent'."（`models/gpt4x.py`） |
| 7 | **严禁注入上下文** | 禁止在 refine 提示词里出现「上次点在哪 / 请修正上次答案」类内容 | 【一手】ZoomClick Table 9：无上下文 **54.1** > 全上下文注入 **19.0** |
| 8 | **越界策略保持 shift** | 越界**平移**不缩放（现有 `plan_crop` 已是 shift，**不要改成 shrink**） | 【一手】ZoomClick Table 10：shift **72.1** > clip 69.6 > shrink **63.6** |

**单位换算必须算清**（否则阈值抄错）：我们在 512 crop 上是 1:1 不上采样（实测 512 crop 的
`image_tokens=361 = 19×19`，若被上采到 1280 宽应为 2116），故公开判据
`c = ‖ε₁‖₂ / r`（r = crop 宽/2）在我们的尺度上 **c = 1.953 × ε₁(物理 px)**。
现阈值 128 px ⇔ c = 250 —— 恰好落在公开分桶里**最差的一档**（c≥250 → 75.6%）。
改成 50 px ⇔ c ≈ 97.6，落进 80–150 桶（76.4%）。
【一手】arXiv:2604.15376 §3.2 + Table 2 分桶（c<30 → 87.1% / 30-80 → 85.1% / 80-150 → 76.4% /
150-250 → 79.8% / ≥250 → 75.6%）；【实测数字】`runs/a2-eval-42/model-requests.jsonl`。

### 验收线怎么定

**先修验收工具，再谈验收线**——42 条分不开 38.1% 与 57.1%（Wilson 95% 区间约 ±15 pp）。
【实测数字】+【一手】ScreenSpot-Pro 全量 1581 条、官方榜按 micro-average。

| 验收项 | 门槛 | 为什么这么定 |
|---|---|---|
| 样本量 | **≥300 条分层抽样**（或全量 1581），固定 seed | 42 条 Wilson 区间 ±15 pp，分不开 38.1 与 57.1，更分不开与公开基线的差距。【一手】官方榜口径 |
| 主指标 | `click_accuracy ≥ 57.1%`（门控关 + crop 512 的**同批实测基线**） | 改动 1~4 之后必须**至少打平**这条基线；打平即证明 crop 不是净负 |
| 调用数 | `≤ 2.05 次/目标` | 门控关那一臂实测 **2.000**；改动不能靠多问模型换分（现验收线 ≤1.5 是另一条线的事） |
| crop 消融 | `{512, 768, 1024, 1280}` 四格，**A 半调 / B 半评** | 【实测】M23 已证明「同批调参 + 同批评估 = 作废」 |
| 落点长尾 | `landing_error_px.p90 ≤ 250 px` | 现值 1191.5；公开分桶里 c≥250 档 75.6% 对应的量级 |
| 精定位产出率 | `refine 产出落点 / refine 触发 ≥ 90%` | 现值 5/10 = 50%（门控关那臂是 32/40 = 80%） |
| 门控可判定条件 | `undecidable` 中 `target_area_ratio` 计数从 42 降到 0 | 出框后第一次可判；现值 42/42 |
| **禁止引用** | 「无条件 crop 整体 −0.3 pp」 | 至今无一手出处 |

---

## 卡点二：粗定位精度 61.6px 中位

### 哪些是 API 模型的能力上限（给数字）

| 模型类别 | ScreenSpot-Pro 公开分 | 出处 |
|---|---|---|
| **通用 API 模型（我们所在的类别）** | GPT-4o **0.8%**（Text 1.3 / Icon **0.0**）；正文："Notably, GPT-4o, despite its advanced capabilities, scored only 0.9%" | 【一手】ScreenSpot-Pro Table 3 |
| **电脑操作专项训练的商用 API** | OpenAI CUA **23.4**、Claude 3.7 **27.7** | 【一手】UI-TARS 官方 README「Grounding Capability Evaluation」 |
| **专用 grounding 7B** | OS-Atlas-7B **18.9**（Icon **4.0**）、UGround-7B **16.5** | 【一手】ScreenSpot-Pro Table 2/3 |
| **专用 grounding 7B + 粗到细** | OS-Atlas-7B → ReGround **40.2**（Icon 15.1）→ ScreenSeekeR **48.1** | 【一手】Table 4 |
| **我们（门控关那一臂）** | **57.1%**（icon 40% / text 72.7%；windows 58.3%） | 【实测数字】`caliber-always.json` |

**结论一：我们的总体水平已经高于所有公开的通用 API 基线**——「我们比不过 GPT-4o 那种通用 API」这个
比较是不成立的（我们是在 ScreenSpot-Pro 抽样上量的、口径与官方不同，但数量级不构成劣势）。

**结论二：图标是全行业都难的那一半**。两篇独立论文给出**完全一致**的分表：
GPT-4o 1.3/0.0/0.8、OS-Atlas-7B 28.1/4.0/18.9、UI-TARS-72B 50.9/17.5/38.1、Qwen2.5-VL-72B 64.9/20.2/47.8
（Text/Icon/Avg）。【一手】ScreenSpot-Pro Table 3 + RegionFocus §4.1 + DiMo-GUI §4.2。
我们的 icon/text 比值 0.367 落在 UI-TARS-72B(0.34) 与 Qwen2.5-VL-72B(0.39) 之间——**形状正常，不是切片异常**。

**结论三：换模型这条路对我们不适用**（不是能力上限问题，是硬件与范式问题）：
- 6 GB 显存上 7B 全都装不下：UGround-V1-7B Q4_K_M 4.68 + mmproj 1.33 = **6.01 GB**；
  OS-Atlas-Base-7B 4.68 + 1.35 = **6.02 GB**；AWQ 的 UI-TARS-1.5-7B 6.92 GB。
  唯一装得下且图标够用的是 Holo1.5-3B（Q4_K_M 2.10 + mmproj 0.85 = **2.95 GB**，ScreenSpot-Pro 51.49 / Icon 29.80），
  但它**不输出编号/索引** → 对我们「模型选编号」的范式无解。
  【一手】HF 文件树实测 + 官方榜 Icon 列。
- 且**全网查不到任何 ≤8 GB 显卡上的实测延迟数字**（官方只在 A100/4090/H200 测过）→ 换模型的延迟账必须自己先测，
  本方案给不出一手数字。

### 哪些是我们能改的

| 改动 | 具体做法 | 预期 | 强度 |
|---|---|---|---|
| **A. 出框而不是出点** | `coarse_prompt` 改成问 `{"bbox":[x1,y1,x2,y2],"point":[x,y]}`；落点取**框中心**；框可退化成点 | 解锁门控 `target_area_ratio`（现 42/42 不可判定）+ R-VLM 的区域提议 | 【一手】官方 `models/gpt4x.py` + Table 3 题注 |
| **B. max_tokens 加倍** | 粗定位 `max_tokens` 2000 → **4096**（编号分支已经给了单独的 4000，粗定位分支仍 2000，**不对称**） | 回收 2 条「HTTP 200 但无文本输出」→ 按门控关 57.1% 估 **+3~8 pp** | 【实测数字】`error` 字段逐字；【推断】回收幅度 |
| **C. 空间投票取代「多问一次否决」** | K 次采样（先 K=2~4）、temp 0.5~0.7、top_p 0.95、点扩成**边长 α=50** 的方块、投票网格=模型输入分辨率、取票数最大连通块中**面积最大者**、落点取其中心 | 框式输出上 +3.4~4.2 pp；**点式模型 ≈0**，必须与 A 配套 | 【一手】GUI-RC（arXiv:2508.05615）§4.1 + 官方代码 |
| **D. 粗点分叉时退回整屏重搜** | 整屏第二遍已有（`STAGE_WHOLE_SCREEN`），把「粗点为空」扩展到「精定位失败」 | 见卡点一 第 5 条 | 【一手】GUI-Lens §2.4 |
| **E. 坐标口径不动** | 继续「自己缩到 1280×800 + 提示词声明尺寸」 | 已验证服务端没再缩 | 【实测数字】反查 image token 1334 = 46×29 → (1288, 812)；【一手】Anthropic 官方长边 1568 / 1.15 MP 上限与推荐 1280×720 起 |

### 明确标为「不适用」的三条线（省时间）

1. **坐标网格 / 切象限 / 切块分别发送**：模型厂商自己内测并否掉。
   【一手】Anthropic 官方博客逐字："Breaking the image into smaller tiles: Splitting a screenshot into
   quadrants or regions and sending them separately did not improve click accuracy." /
   "Overlaying a grid pattern with coordinates: ... did not produce reliable gains." /
   "Resize algorithm choice: PIL LANCZOS, sips, and other common resize algorithms produced identical results."
   另：OS-Atlas 的 Iterative Focusing（2×2 象限）31.0 < ReGround 40.2。
2. **归一化坐标输出（0~1 / 0~1000）**：只换约定，不解决可见性/候选问题；
   【一手】Anthropic 官方："Claude does not work well when you ask for normalized coordinates"（该页本轮**地区不可用**，未能复核）。
3. **换本地 grounding 模型**：见上（6 GB 装不下 + 无编号输出 + 无延迟一手数据）。

---

## 卡点三：候选源缺图标 / 门控净负 / 端到端退步

### 3.1 候选源缺图标（0/13）

**诊断**：OCR 词级框**原理上召不回图标**（图标没有文字）。真机同一批 22 个控件：
UIA 22/22 命中、落点误差中位 **0.0 px** / p90 0.5 / max 0.5；
OCR 15/22、median **18.4 px** / p90 60.0 / **max 219.0**，**崩掉的 7 条全是纯图标控件**
（任务视图 219.01、显示隐藏的图标 33.24、手写笔 42.05、音量 60.03、网络 32.31、Tabbit 69.64、开始 36.14）。
【实测数字】`runs/b1-live-zh/live-report.json`。

⚠️ **两条管线不能混算**：ScreenSpot-Pro 是**静态截图**，没有 UIA 树（UIA 是 client-provider COM 协议，
没有离线快照 API）；所以「静态图 icon 0/13」与「真机 UIA 22/22」不矛盾——
**生产路径的图标解法是 UIA，不是检测器**。

**业界怎么绕过**：
1. **UIA 主表 + 视觉框附加表**（UFO²）：UIA 全留作主表，视觉检测作附加表，
   与任一 UIA 控件 IoU > **0.1** 的附加项**丢弃**；比较在绝对屏幕像素空间。
   【一手】UFO 官方 `config/ufo/system.yaml`：`CONTROL_BACKEND: ["uia"]  # uia, omniparser`、
   `IOU_THRESHOLD_FOR_MERGE: 0.1`、`BOX_THRESHOLD: 0.05`、`CONTROL_FILTER_TOP_K_ICON: 15`、
   `CONTROL_FILTER_MODEL_ICON_NAME: "clip-ViT-B-32"`；论文 §3.4："Visual detections with IoU greater
   than 10% against any UIA-derived control are discarded"；Figure 20："27.9% and 56.7% of
   OmniParser-v2 detections are discarded due to overlap with UIA"。
2. **编号 + 局部语义**（OmniParser/SoM 形态）：GPT-4V 直接出 xy 在 Desktop Icon **11.8%** →
   编号框 + 局部语义 **63.6%**；SeeAssign 探针 **0.705→0.938**。
   【一手】OmniParser 论文 Table 与正文。
3. **两条 GUI 专属的坑必须照抄规避**【一手】SoM（arXiv:2310.11441）§2.3："the mark types should be
   image-dependent to avoid any conflicts with the original image contents"（桌面截图满是数字 → **用字母标
   不用数字标**）；SoM-Bench README："the regions are very dense, so we split the regions into multiple
   groups of size 5"（**每视图 ≤5 个标记**；我们 OCR 源现在是 20）。
4. ⚠️ **OmniParser 自己的合并规则方向是反的，不能照抄**：`util/utils.py remove_overlap_new()` 里
   图标框落在 OCR 框内（is_inside ratio > 0.80）时**图标候选被删**——
   这正好会**精确复现我们的 0/13**。规则可抄，**方向必须反过来**（图标框优先保留，OCR 文字挂成它的标签）。
   【一手】OmniParser 官方源码。

### 3.2 门控净负（+0.38 次调用换 −19.0 pp）

- **符号与公开成交案例相反**：官方榜 GroundCascade 的 description 写明
  "fixed 0.5 zoom; 1% self-consistency gate; 4% cross-model agreement gate; ... **2.574 average model
  calls/example**"，overall **85.14%**；对照 Indeed-UI-32B-zoomin **82.73%** →
  **+0.574 次调用换 +2.41 pp**。【一手】`GUI-Agent/grounding-leaderboard` 的 `results/screenspot_pro.json`
  （⚠️ 该条目无论文无代码、1%/4% 的定义未核实，**只能当方向性证据，不能当参数抄**）。
- **公开同构实现本身就是 null result**：zoom-consistency 路由只 +0.8%（McNemar p=0.19），
  与正确性的相关性 AUC 0.600 / Spearman ρ=−0.14（p<1e-6）。【一手】arXiv:2604.15376 Table 1。
  它只能当「省调用的信号」记账，**不能当精度收益**。
- **公开已经判死「再加一个置信判据」这条路**：LFPR 的同骨干对照把几何护栏换成免标定置信门控，
  "the confidence-gated arm does not recover even the guard's modest Acc@0.5 gain over routing"；
  去掉护栏的对照 "underperforms the incumbent on every metric, showing the guard is load-bearing"。
  【一手】arXiv:2608.19553。
- **所以**：门控**降级为记录器**；删掉「第二次独立粗定位」这条判据（那一次调用正是 −19.0 pp 的来源，
  而且公开同构的它自己是 null result）。要保留的只有**事后几何验收**（偏离量 + zoom consistency），
  且阈值按 ZoomClick 改成 **50 px**、参考点改成 crop 中心。

### 3.3 端到端退步

见 §一 的四类根因。逐条解法：

| 根因 | 解法 | 抄谁 |
|---|---|---|
| **读值层**（W-1.E scale_value 没读到值） | 缩放值改读注册表 `HKCU:\Control Panel\Desktop\LogPixels`（96/120/144 → 100%/125%/150%），树读失败时用状态读数兜底 | 【一手】WAA `desktop_env/evaluators/getters/settings.py`（5 条 Windows Settings 任务全部 `exact_match` 读注册表/PowerShell） |
| **候选层**（E-1.1 / E-2.1 覆盖失败） | 混合候选源（§五）+ 编号清单的候选描述表 | 【一手】UFO² / OmniParser |
| **执行器层**（W-3.1 找不到 Shell_TrayWnd） | 改执行器 —— 同一台机器 UIA 通道在 Shell_TrayWnd 上 22/22 跑通过 | 【实测数字】`runs/b1-live-zh` |
| **环境/口径层** | 见 §六 的口径表 | 【一手】OSWorld / WAA 记分口径 |

**上下文与帧管理**（次要但有公开数字）：Anthropic 官方 "keep the last three screenshots and prune every
25 turns"、单请求 20 图硬红线、每图 1,000–1,800 token；Navi / UFO² 都是「当前帧 + 上一帧」共 2 帧 +
纯文本动作历史；ShowUI 有视觉历史 68.3→70.0（+1.7 pp）。【一手】Anthropic 官方文档 + 各官方仓。
我们每轮重发全部帧会先撞 20 图红线而不是 token 上限。

---

## 混合候选源的最小实现

### 文件与函数级

**主文件：`src/minicode/ground/candidates.py`**（现 393 行，已有 `GroundingCandidate` / `iou` /
`dedupe_by_iou` / `OcrCandidateSource`）

1. **新增 `class UiaCandidateSource`** —— 实现现成的 `CandidateSource` 协议
   （`candidates(frame, description) -> Sequence[GroundingCandidate]`）：
   - 输入：UIA 控件列表（名字 + **物理像素**矩形），来自 `minicode.uia.ports` / `WindowsUiaQueryPort`；
   - 输出：`GroundingCandidate(number=1起, text=控件名或位置型占位描述, box=物理像素矩形, source="uia")`；
   - **不要**再写 `if control.name` 这种静默丢弃（现在 `numbered_bench.py:318` 就是这样，
     无名图标控件被无声丢掉）——无名控件给位置型占位描述（如「任务栏左起第 3 个图标」）。
2. **新增 `def merge_candidate_sources(main, additional, *, iou_threshold=IOU_THRESHOLD_FOR_MERGE)`**：
   - **主表（UIA）全留**；附加表（OCR）中与**任一**主表项 IoU **> 0.1** 的**丢弃**；
   - 其余附加项追加在主表之后；
   - **比较一律在物理像素空间**（红线①，论文没写它在哪个坐标系比，我们定死）。
   - 阈值常量复用现成的 `IOU_THRESHOLD_FOR_MERGE = 0.1`（`candidates.py:50`，已抄 UFO²）。
   - ⚠️ **方向不能反**：UIA 框与 OCR 框重叠时**保留 UIA 框**，把 OCR 文字挂成它的 `text`；
     绝不能学 OmniParser 的「图标落在 OCR 框内就删」。
3. **ControlType 枚举补全**：`src/minicode/ground/numbered_bench.py:398` 现在只枚举
   `Text/Button/Hyperlink/Edit/ListItem` → 加 `Custom(50025)`、`Image(50006)`、`SplitButton`、
   `ToolBar`、`CheckBox`、`MenuItem`、`TabItem`、`Thumb`。
   依据【一手】微软官方文档：可点击的图片应当实现 Invoke pattern 并使用 Button 类控件类型；
   纯装饰性图片可以没有名字。**`Custom(50025)` + 空名控件是关键**（真机 UIA 22/22 里就有空名 Button）。
4. **候选数上限**：OCR 源现在 `max_candidates=20`（`candidates.py:186`）。
   UIA 源按什么截断**未验证**（UIA 在大应用窗口可能上千控件）。
   **保守做法**：先按「名字/描述与目标描述的匹配度」排序，匹配不上的按面积升序补位，截断到 **20**；
   SoM 作者自己的密度处理是 5/视图，但那是**叠框图**的密度，不是清单长度，两件事不要混。
   （若要更狠，可抄 UFO² 的 `CONTROL_FILTER_TOP_K_ICON: 15` + `clip-ViT-B-32` 图标预筛，
   88 M 参数 CPU 可跑 —— 但这是「额外模型」，见 §九 第 3 条。）

### 坐标空间（红线①：唯一真值 = 物理像素 × 虚拟桌面原点）

| 来源 | 原始坐标 | 转物理像素 |
|---|---|---|
| OCR 词框 | 喂进去那张帧的像素 | **加帧原点** `frame.left / frame.top`（现 `candidates.py:201` 已做） |
| UIA `BoundingRectangle` | 已经是物理屏幕像素 | **不要加任何偏移**。【一手】微软 `UIA_BoundingRectanglePropertyId` 文档：矩形 "is expressed in physical screen coordinates"；这正是我们真机 0.0 px 误差的来源 |
| IoU 比较 | — | **只在物理像素空间里比** |

### 接线点

- `src/minicode/ground/channel.py:635` `_candidates()` —— 单源改合并源（UIA 主表 + OCR 附加表）；
  现函数已把「没有候选源」（`no-candidate-source`）与「候选源报错」（`candidate-source-error`）分开，
  **沿用这个纪律**：判不了 ≠ 没有候选。
- `src/minicode/ground/channel.py:652` `_candidate_lines()` —— 不改（它只是把物理框换算成模型图坐标**用于显示**；
  落点仍取物理框中心，零换算）。

### 缺候选时的回落（三级，逐级降级）

1. **UIA 源不可用 / 树为空**（UWP 页面上就是空树）→ 不报错，退回 **OCR 源**；
2. **两源都没有匹配上的候选** → 走**点式路径**，显式记 `STAGE_NO_CANDIDATES`（现成枚举，保持）；
3. **精定位也失败** → 按卡点一 第 5 条退回**整屏重搜**（第二批）。

### 必须补的一列：候选覆盖率

公开纯 CV 方案在 IoU>0.9 口径下 F1 只有 **0.154~0.201**（REMAUI 0.201 / Xianyu 0.154），
所以「图标能不能救」必须**单独报候选覆盖率**（目标中心是否被任一候选覆盖），
不能只看最终命中率。评测台加三列：**IoU@0.3 / IoU@0.5 /「框中心是否落在真图标内」**。

---

## 端到端可达成的现实目标

### 公开基线（同一类任务，用来定天花板）

| 基准 | 最好 agent | 人 | 出处 |
|---|---|---|---|
| **Windows Agent Arena（154 任务）** | **19.5%**（Navi + UIA + OmniParser + GPT-4V-1106）；Windows System 域 **27.3%** | **74.5%**（Windows System 83.3%，1 名参与者 1.5 h / 5 次机会） | 【一手】WAA 论文 Table 4 / §4.2 |
| **UFO²** | WAA **27.9%**（GPT-4o）/ **30.5%**（o1）；Windows System **41.7%/45.8%**；OSWorld-W **28.6%/32.7%** | — | 【一手】UFO² 论文 Table 1/2 |
| 我们的静态定位 | windows **58.3%**（门控关臂） | — | 【实测数字】 |

**公开通用 API 模型在 ScreenSpot-Pro 的 windows_common 上：Claude Computer Use 4.8% / GPT-4o 0.0%**。
我们的静态定位（58.3%）远高于这两家——**静态 grounding 不是端到端瓶颈**。

### 现实目标（诚实版）

| 波次 | 现在 | **现实可达** | 靠什么 | 备注 |
|---|---|---|---|---|
| **wave1**（Windows 设置 / 任务栏 / 浏览器） | **0/3**（denominator=3） | **1/3 ~ 2/3** | ① W-1.E 缩放值改读注册表 → 救 1 条；② W-3.1 执行器 bug → 救 1 条；③ W-1.1/W-1.2 的「判不了」需补验收读数或改口径 | 公开最好 agent 在 WAA 上也只有 19.5% overall / Windows System 27.3%~45.8%——**0/3 是实现差距，不是任务不可解**，但也别按「多步任务 80%」设期望 |
| **wave2**（微信 / cc-switch / 设置） | **3/6**（denominator=6，50%） | **4/6 ~ 5/6** | ① E-1.1 / E-2.1 覆盖失败 → 混合候选源 + 描述规范化；② CC-1.A `capability='sparse'` → 改判据 | WX-1/WX-2 的失败**不是模型问题**（登录态） |

### 哪些票应该改口径而不是改代码

| 票 | 现状 | 公开口径 | 建议 |
|---|---|---|---|
| W-1.1 / W-1.2「期望**判不了**」 | 判不了 ≠ 失败，但也不进分母 | 公开工作没有「判不了」桶 | 单列「判不了」计数；**并给出可判的读数**（读系统状态），否则这两票永远不可判 |
| WX-1 微信登录界面 | **跳过**，不进分母 | OSWorld：infeasible 题 **进分母**，只有 agent 声明 FAIL 才算成功（369 题里 30 条 = 8.1%） | 改成 infeasible 语义：进分母 + 声明 FAIL 才算通过 |
| WX-2.x / CC-3.3 人工代确认 | 单列，不混进自动通过率（**这条已经是对的**） | 同上 | 保持单列，拆成「合规停手」与「人工代跑完成」两个计数 |
| WX-3 模态弹窗 → 交人 | 停止条件命中（`modal_dialog`） | **公开工作没有「交人=通过」的先例**：UFO² `ASK_QUESTION=False`（默认不让问人）；AndroidWorld 声明 FAIL 后可行任务 = 0 分 | 拆成两个计数；**不许**把「交人」计成自动通过 |
| 「跳过（环境不可达）」桶 | **不进分母** | OSWorld/WAA：进分母 + 单列 | ⚠️ 需要拍板（§九 第 4 条） |

**一句话**：wave1/wave2 的 `pass_rate`（0% / 50%）**不能直接与公开数字比**，因为我们的分母口径
（剔除了环境不可达）与公开不同。对外必须标注分母口径。

---

## 最小改动清单

按投入产出排（≤8 条）。「看哪个数变」= 改完该看哪个产物字段。

| # | 改哪个文件 | 做什么 | 看哪个数变 |
|---|---|---|---|
| **1** | `src/minicode/ground/channel.py`（门控分支 L424~460）+ `src/minicode/ground/gate.py` | **门控降级为记录器**：`refine_enabled` 恒真，decision 只落盘；删掉「第二次独立粗定位」这次调用（`second_opinion` 端口不再注入） | `caliber.json`：`click_accuracy` **38.1% → 57.1%**；`model_calls_per_target` **2.381 → 2.000**；`gate.hit_rate` 变成纯记录 |
| **2** | `src/minicode/coord/crop_refine.py:58-59` + `src/minicode/ground/bench.py` | **crop 512 → 1024**（1:1 不缩放）；扫描集合 `{512,256,192,128}` → **`{768,1024,1280}`**（并同步改 `docs/research/core-function-plan.md` §C-1 的方向） | `caliber.json`：`click_accuracy` 再往上走；`refine_error_px.p90`（现 516.5）下降；`refine` 产出率 ≥90% |
| **3** | `src/minicode/ground/gate.py:154` + `src/minicode/ground/channel.py:544` | **偏离阈值 128 → 50 px**（来源串换成 ZoomClick Table 11 出处）；参考点从 `coarse` 改成 **`plan.rect.center`**；落盘 `zoom_consistency_px` 与 `zoom_consistency_c1000` | `caliber.json`：`fallback_rate`（现 0.5 / 0.2）下降；`landing_error_px.p90` ≤ 250 |
| **4** | `src/minicode/ground/prompt.py:88-102`（`coarse_prompt`）+ `src/minicode/model/client.py` | **粗定位改出框**：`{"bbox":[x1,y1,x2,y2],"point":[x,y]}`，落点取框中心（照抄官方 `models/gpt4x.py` 措辞与解析口径）；`max_tokens` **2000 → 4096** | `caliber.json`：`coarse_error_px.count` **36 → 42**；`gate.undecidable.target_area_ratio` **42 → 0**；「HTTP 200 无文本输出」计数归零 |
| **5** | `src/minicode/ground/candidates.py` + `src/minicode/ground/channel.py:635` + `src/minicode/ground/numbered_bench.py:318,398` | **UIA 候选源 + UFO² IoU 0.1 合并**（UIA 主表全留、OCR 附加表 IoU>0.1 丢弃、物理像素空间比较、方向不反）；去掉 `if control.name` 过滤；ControlType 加 `Custom(50025)` / `Image(50006)` 等 | 真机 `runs/b1-live-zh`：图标类控件召回 **0 → 22/22**、落点误差中位 0.0 px；静态：候选覆盖率列 ≥ 目标 |
| **6** | `src/minicode/e2e/real.py:414-445` | **缩放值改读注册表** `HKCU:\Control Panel\Desktop\LogPixels`（96/120/144→100/125/150%），树读失败时用状态读数兜底 | `runs/d6-wave1/e2e-report.json`：W-1.E 从「没有读到值」变成成立 → wave1 **0/3 → 1/3** |
| **7** | `src/minicode/e2e/report.py` + `src/minicode/e2e/runner.py` + `src/minicode/e2e/real.py` | **口径对齐公开基准**：infeasible 进分母 + 只有 agent 声明 FAIL 才算成功；「人工代跑完成」与「合规停手」拆成两个计数；模态弹窗不许计成自动通过 | `e2e-report.json`：`auto.denominator` 与 `human_confirm` 口径写进报告抬头，**对外可比** |
| **8** | `src/minicode/ground/bench.py` | **评测集 42 → ≥300 条分层抽样**（固定 seed）；加第三臂「**只用粗定位**」（1.00 次/目标，我们从未测过）；加候选覆盖率三列 | `caliber.json`：三条臂同批可对比；Wilson 区间从 ±15 pp 收到可判定 |

> 改动 1、4 触及既有红线（`core-function-plan.md` §C-4 红线③「crop 精定位必须门控」、
> `docs/adr/0006-crop-local-coordinate-contract.md` 的坐标契约）→ **需拍板**，见 §九。

---

## 证据强度总表

### 【实测数字】= 本机自己量的（产物可复核）

| 结论 | 数字 | 产物 |
|---|---|---|
| 门控开 vs 门控关 | 38.1%（16/42）vs **57.1%**（24/42）；2.381 vs 2.000 次/目标 | `runs/a2-eval-42/caliber.json` / `caliber-always.json` |
| **门控分组复算（本文核心）** | 判 `coarse_inconsistent` 的 10 条两臂落点**逐条相同**、都是 3 命中（+0.0 pp）；其余 32 条 **13→21**（+25.0 pp） | `runs/a2-eval-42/sspro-report.json`（本轮独立复核） |
| 落点误差 | 门控开 median 72.8 / p90 1191.5 / max 2510.6；门控关 median **43.5** / p90 1191.5 | 同上 |
| 粗定位误差 | count **36**（42 条里 6 条没有粗点）/ median **61.6** / p90 1144.5 | `caliber.json` |
| 精定位误差 | 门控开 count **5** / median 51.0 / p90 768.7；门控关 count **32** / median **12.5** / p90 516.5 | 两个 caliber.json |
| refine 触发与回落 | 门控开 refine 10/42、fallbacks 5（**50%**）；门控关 refine 40/42、fallbacks 8（20%） | 同上 |
| 门控不可判定 | `target_area_ratio` **42/42**、`confidence` **42/42**、`consistency` 9 | `sspro-report.json` |
| 真机 UIA vs OCR | UIA **22/22**、误差 median **0.0** / max 0.5 px；OCR **15/22**、median 18.4 / max 219.0 px（崩的 7 条全是纯图标） | `runs/b1-live-zh/live-report.json` |
| crop 含目标率 | 512 **84.6%** / 768 88.5% / 1024 92.3% / 1280 96.2%（门控干净子集 26 条） | 由 `caliber.json` 复算 |
| crop 图像 token | 512 crop → 361 = 19×19（**1:1 不上采样**） | `runs/a2-eval-42/model-requests.jsonl` |
| 整屏图像 token | 1334 = 46×29 → 服务端实际看到 (1288, 812)，**没再缩** | 同上 |
| 端到端 | wave1 auto passed 0 / failed 3 / skipped 2 / denominator **3** → **0/3**；wave2 passed 3 / failed 3 / skipped 1 / denominator **6** → **3/6** | `runs/d6-wave1/`、`runs/d6-wave2/e2e-report.json` |
| 标定 | 6/6 会话可用，96 px 方块命中率 1.00，等价标度族分歧 0.00 px，探针落点误差 median 0.00 / p90 ≤2.83 px | `runs/a1-calib*` |

### 【一手】= 官方 README / 论文原文 / 官方文档 / 源码（**本轮直接打开复核过**）

| 结论 | 出处 | 本轮复核 |
|---|---|---|
| crop 尺寸消融：OS-Atlas-7B **512=25.1（最差）/768=34.2/1024=40.2/1280=40.1**；UGround-7B 27.0/28.8/28.2/26.3；原文 "OS-Atlas-7B achieves its best score with 1024×1024 crops" | ScreenSpot-Pro（arXiv:2504.07981）Table 5 + §5.2 | ✅ 已抓 HTML 逐字 |
| 四种粗到细同模型对照：OS-Atlas-7B **18.9**（Text 28.1/Icon 4.0）→ Iterative Focusing 31.0 → Iterative Narrowing 31.9 → **ReGround 40.2（Icon 15.1）** → ScreenSeekeR 48.1 | 同上 Table 4 | ✅ |
| 通用 API 天花板：GPT-4o **0.8**（Text 1.3 / Icon **0.0**），正文 "scored only 0.9%"；Table 3 题注 "For models inferencing boxes, we consider the center point of the generated box as the prediction" | 同上 Table 3 | ✅ |
| 目标占比 0.07%（ScreenSpot 2.01%）；text 占 62.6% | 同上 §3.4 | ✅ |
| 官方通用 API 协议：问框 `[[x0,y0,x1,y1]]`，`click_point = 框中心` | 官方仓 `models/gpt4x.py` | ✅ 已抓源码 |
| **偏离阈值 50 px**：最优 50.7 px / 91.8%；正确对中位 <10 px、错误对中位 >50 px；"motivating our adoption of 50 px" | ZoomClick（arXiv:2512.05941）Table 11 | ✅ 已抓 HTML |
| 越界策略 shift 72.1 > clip 69.6 > shrink 63.6 | 同上 Table 10 | ✅ |
| min_crop 消融：Qwen3-VL-32B 65.2→**72.3**（单调上升，通用 VLM 越大越好）；UI-Venus-7B 峰值在 384~512 | 同上 Table 8 | ✅ |
| 两步 ×1/2 + ×1/2 (63.9) > 一步 ×1/4 (62.1) | 同上 Table 6 | ✅ |
| **UFO² 合并规则常量**：`IOU_THRESHOLD_FOR_MERGE: 0.1`、`BOX_THRESHOLD: 0.05`、`CONTROL_BACKEND: ["uia"]`、`CONTROL_FILTER_TOP_K_ICON: 15`、`clip-ViT-B-32` | UFO 官方 `config/ufo/system.yaml` | ✅ 已抓源码 |
| **Anthropic 官方否掉的三条**：切块 "did not improve click accuracy"、网格 "did not produce reliable gains"、缩放算法 "produced identical results"；"Enable enable_zoom: True for dense UIs"；长边 1568 / 1.15 MP 上限、推荐 1280×720 起 | claude.com 官方博客 computer-use best practices | ✅ 已抓页面 |
| arXiv 编号与标题核对：2608.03270 GUI-Lens / 2604.15376 Zoom Consistency / 2608.19553 LFPR / 2507.05673 R-VLM / 2507.00008 DiMo-GUI / 2508.05615 GUI-RC / 2504.14603 UFO2 / 2409.08264 WAA / 2408.00203 OmniParser / 2310.11441 SoM / 2504.07981 ScreenSpot-Pro / 2512.05941 ZoomClick | arxiv.org | ✅ 逐个抓 meta |

### 【一手】（团队调研引用的，本轮**未**逐条复核，标为待复核）

| 结论 | 出处 |
|---|---|
| GUI-Lens Table 1：MiniMax-M3 26.4→47.4、Claude Opus 4.7 57.4→82.3、GPT-5.5 74.8→87.9；§2.4 "Rejected proposals restore B0 and restart cropping from the full-screen observation"；crop 验证重试 ≤6 次 | arXiv:2608.03270 |
| zoom-consistency：`c=‖ε₁‖₂/r`；分桶 c<30→87.1% / ≥250→75.6%；AUC 0.600、ρ=−0.14；路由 +0.8%（McNemar p=0.19） | arXiv:2604.15376 |
| LFPR 三条护栏 `z≤0.35`、`IoU≥0.25`、`0.5≤A/A′≤2.0` + 中点融合 `b̂=½b0′+½bc`；"routing alone gains +1.162, but crop, guards, and fusion give back −1.192" | arXiv:2608.19553 |
| R-VLM "assuming this region as a region proposal"；ScreenSpot 53.4→66.3、AgentStudio 61.1→74.1 | arXiv:2507.05673 |
| DiMo-GUI 停止判据 = zoom 前后预测位移 < zoom 前图对角线 **1/6**；max_iters 7；icon 1.7→4.0 | arXiv:2507.00008 |
| GUI-RC：K=64、temp 0.5、top_p 0.95、α=50 方块、连通块最大面积；框式 +3.4~4.2 pp / 点式 ≈0 | arXiv:2508.05615 |
| UFO²：WAA Hybrid 27.9%（o1）/ CRR 9.9%；OSWorld-W CRR 0%→25.0%；"up to 9.86% of previously irrecoverable cases"；Figure 20 丢弃率 27.9%/56.7%；">62% 的失败归因 Control Detection Failures" | arXiv:2504.14603 |
| WAA：最好 19.5%、Windows System 27.3%、人 74.5%/83.3%；加 UIA markers 让 OmniParser 相对提升 57%；5 条 Settings 任务读注册表/PowerShell | arXiv:2409.08264 + 官方仓 `evaluation_examples_windows/examples/settings/`、`desktop_env/evaluators/getters/settings.py` |
| OmniParser：GPT-4V xy 16.2 / Desktop Icon 11.8 → OmniParser 73.0 / 63.6；SeeAssign 0.705→0.938；V2 官方日志 icon 4.65% vs text 58.89%（55/139=39.57%）；`remove_overlap_new()` 图标落 OCR 框内即删 | arXiv:2408.00203 + 官方仓 `eval/`、`util/utils.py` |
| SoM §2.3 标记类型撞车；SoM-Bench 密度 5/组 | arXiv:2310.11441 + 官方仓 `benchmark/README.md` |
| UI-Zoomer gating：SIGMA=2.5、THRESHOLD=1.5；ScreenSpot-Pro Qwen2.5-VL-7B 27.6→41.0 | 官方仓 `ZJU-REAL/UI-Zoomer` |
| 官方榜 GroundCascade description："fixed 0.5 zoom; 1% self-consistency gate; 4% cross-model agreement gate; ... 2.574 average model calls/example"，overall 85.14% | `GUI-Agent/grounding-leaderboard` 的 `results/screenspot_pro.json`（⚠️ 无代码无论文，1%/4% 定义未核实） |

### 【二手】

| 结论 | 说明 |
|---|---|
| Anthropic 官方 "Claude does not work well when you ask for normalized coordinates" | 来自 `platform.claude.com/docs/en/build-with-claude/vision-coordinates`；**本轮该页返回「App unavailable in region」**，未能复核，引用时须标注 |
| OSWorld infeasible 桶占比 8.1%（369 题 30 条）、现仓 418 文件中 27 个 `func=infeasible` | 团队从官方仓代码数出，本轮未复核 |
| ScreenSpot-Pro 只有 Text/Icon 两类（1581 = 977 text + 604 icon），**没有 widget 子集** | 团队从论文 §3.2/Table 1 数出，本轮未逐条复核（§3.4 的 62.6% text 已复核，与之一致） |
| 6 GB 显存上的 GGUF 体积（UGround-V1-7B 6.01 GB / OS-Atlas-Base-7B 6.02 GB / Holo1.5-3B 2.95 GB） | 团队经 hf-mirror 实测 HF 文件树；本轮未复核 |

### 【推断】

| 结论 | 依据 |
|---|---|
| 门控**反向**的机制解释（触发条件 = 精定位前提条件的反面） | 由分组复算 + 「不一致 ⇒ 粗点已偏 ⇒ 目标不在窗内」的推理得出；已被 5/10 的回落率佐证 |
| crop 1024 会进一步提升命中率 | 由 Table 5 的单调性 + 本机含目标率（84.6%→92.3%）推断；**未实测** |
| max_tokens 4096 可回收 2 条丢失样本 → +3~8 pp | 由 2/42 的样本数与门控关臂 57.1% 推断；**未实测** |
| 混合候选源能把静态 icon 召回从 0/13 拉到接近真机 22/22 | 由真机 UIA 22/22 的实测外推；**未实测**（静态图无 UIA 树） |
| wave1 现实可达 1/3~2/3 | 由四类根因逐条可修性 + 公开基线推断；**未实测** |
| **「无条件 crop 整体 −0.3 pp」** | ❌ **至今没有一手出处，也未找到相反出处 —— 本文不引用** |

---

## 需要人拍板的点（≤4 条）

1. **推翻 `core-function-plan.md` §C-4 红线③**（"crop 精定位必须门控"，并引用 `docs/spec.md` §11 第四条
   "兜底要罕见"）。实测同批两臂：**门控关 57.1% / 2.000 次**，**门控开 38.1% / 2.381 次**——
   门控在准确率与成本两个轴上同时更差。是否把 `GateMode.ALWAYS` 设为生产默认、
   把门控降级为记录器？**（不改这一条，改动 1~3 全部不成立。）**
2. **粗定位出框是否算「改坐标契约」**？落点取框中心会引入一个新的坐标契约（框 → 点），
   与 `docs/adr/0006-crop-local-coordinate-contract.md` 的既有约定相邻但不重合。
   红线①②③（唯一真值 / 不迭代裁剪 / 必须门控）里红线③已被第 1 条覆盖，红线①②不受影响。
   需拍板：**允许 `coarse_prompt` 同时出框出点、落点取框中心**吗？
3. **`docs/spec.md` §12 禁 OmniParser —— 禁的是「产品路线」还是「任何额外模型」？**
   直接决定：候选源能不能接像素级检测器、能不能抄 UFO² 的 `clip-ViT-B-32` 图标预筛
   （`CONTROL_FILTER_TOP_K_ICON: 15`，88 M、CPU 可跑）。**注意**：即使禁检测器，
   §五 的「UIA 主表 + OCR 附加表 + IoU 0.1」规则**照样可抄**（规则是算法，不是那个模型）。
4. **「跳过（环境不可达）」的计数口径**：我们现在是**不进分母**；OSWorld/WAA 是**进分母 + 单列 infeasible**。
   改了之后 wave1/wave2 的 pass_rate 会下降（本机这 6 票里已有 3 票属于这一类），
   但**对外可比**。是否改？（改口径不影响模型能力，只影响对外表述的诚实度。）

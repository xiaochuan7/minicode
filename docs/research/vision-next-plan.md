# 视觉主功能 · 下一轮收口方案

> **本文件是下一轮的施工方案**，不是完成报告。上一轮的完成情况见 `docs/research/vision-final-closure.md`（8 组验收线逐条交账）。
> **调研依据（本轮 8 份）**：`r-s1-guardrail.md`（护栏）· `r-s2-false-hit.md`（假命中）· `r-s3-icon.md`（icon）·
> `r-s4-resolution.md`（分辨率）· `r-s5-windows-e2e.md`（真机 e2e）· `r-s6-multi-vote.md`（多采样投票）·
> `r-s8-headroom.md`（空间与换眼）。（`r-s7` 本轮不存在，未引用。）
> **优化目标（用户给定，优先级最高）**：性能/可用性 > 一切。**token 消耗、调用次数、延迟、费用一律不是约束**，
> 本文件**没有一条**以省 token / 省调用 / 降延迟为理由；所有取舍只按「多花多少能多拿多少个点」表达。
> **真机与权限（用户给定，完全授权）**：真机随便用；占用锁 `.scratch/desktop.lock` **以 agent 身份直接写、直接用、直接释放，不等人工确认**；
> 「需要人工代确认」**不写成阻塞项**（见 §5 T10：改成 agent 代确认并计入分母）。
> **口径纪律**：本文件每个数都带出处与条件；本机实测一律标样本量 + 产物路径；公开结论一律带 URL。
> **本文件不新建工单、不改代码** —— 只写方案。

---

## 一、一句话结论

**下一轮打三件事，每件都有可落地的文件落点：**
① **生产档从 1536×960 改到 2016×1260**（配对 p=0.029，+8.7 pp，已实测，不是推测）；
② **精定位从「1:1 物理 crop（图标只有 20 物理 px = 0.71 个 token 格，亚格）改成「放大到 ~2 格 + 多候选空间聚类 + 标记图让模型选 + 整屏再搜」**；
③ **真机端到端把四个卡点一次打通**（准入门抽签 / XAML root-walk / 浏览器通道无滚动 / 控件框真值读不到）。

**预期能到的数（183 条分层抽样口径，配对判定，不是全量 1581）：**
整体 click accuracy **55.74%（1536 档）→ ≥70%**（硬底线：不低于已实测的 **64.48%**，2016 档）；
icon 子集 **41.38%（1536）/ 43.68%（2016）→ ≥48%**；
假命中 **20 → 0**（口径：`crop_contains_target == False 且 landing_source == "refined"`）；
硬失败回落率 **14.2%（1536）/ 6.25%（2016）→ ≤10%（主口径）/ ≤6%（2016 档）**；
护栏「改坏条数」**11（1536）/ 6（2016）→ ≤3**，护栏取舍正确率 **≥60%**；
端到端 **wave1 0/5 → ≥3/5、wave2 3/7 → ≥5/7**（需要真机跑、锁直接拿）；
视觉步骤可判率 **wave1 0/2 → 100%**（wave2 现为 1/1）。

**同时要认领的一个坏消息**：icon 子集**不是档位问题**。同批 183 条配对 McNemar（我本轮离线算出，零模型调用）：
2016 档在 icon 子集上是 **12 命中 vs 14 命中、exact p = 0.845（不显著）**，整体 +8.7 pp 全部来自 **text 子集**（4 vs 18、p = 0.0043）。
→ **icon 的主战场是 crop 侧放大与候选源，不是把整屏送大。**

---

## 二、四个卡点的解法

### 卡点 ① 护栏：粗定位点与精定位点都在时，按什么**可判**规则选？

#### 现状（为什么不能再用阈值）

* 当前形态是**二选一**：精定位成功就无条件替换粗定位（`src/minicode/ground/channel.py:785` `landing_source=LANDING_SOURCE_REFINED`），**没有护栏**。
* 本机实测：**位移阈值不是「没增益」，是净负** —— 若改成「超阈值就回落粗点」，那 30 条被丢弃的行命中从 18 变 12 → **净 −6 条（−3.28 pp，96/183 = 52.46%）**【一手·本机；`r-s1-guardrail.md` §1】。
* 更狠的一条：**R1 几何硬门（精点出粗框就拒答）抓到 15/20 假命中，却弄坏 41 条本来命中的精定位 → 净 −26 条（−14.21 pp）**；
  放宽到 50 px 容差仍净 −13 条（−7.10 pp），放宽到 150 px 仍净 −7 条（−3.83 pp）【一手·本机；`r-s2-false-hit.md` §3.2】。
  → **「离粗定位多远 → 回落」这条路在几何上是死的**，因为「假命中」与「正确但离得远」在几何上本来就重叠（离框距离 p50：假命中 94.9 px vs 正确命中 0.0 px；p90 分别是 1054 px vs 195 px）。
* 公开侧同向证据：LFPR 的**无 guard 对照**比 incumbent 更差 —— Acc@0.5 **−2.926**、Acc@0.75 −3.969、Acc@0.9 **−5.748**，原文称 "the guard is load-bearing"
  【一手·公开 https://arxiv.org/abs/2608.19553】。
* **公开证据一致反对「置信度」当判据**：FORUM §5.6（置信度恰在 distractor 上失效，"we therefore select without confidence rather than approximate one"）
  【一手·公开 https://arxiv.org/abs/2609.37488】；LFPR 的同骨干机制对照（换成冻结的无标签置信度阈值，连 guard 那点 Acc@0.5 增益都拿不到）【一手·公开 https://arxiv.org/abs/2608.19553】。
  → **本仓 `GateCondition.LOW_CONFIDENCE`（`src/minicode/ground/gate.py`）这条路不再投入**。
* 另一条硬约束：**这条网关拿不到 logprobs** —— `POST /v1/messages` 带 `logprobs:true` 返回体顶层键只有 `content/id/model/role/stop_reason/type/usage`；
  `POST /v1/chat/completions` 带 `logprobs:true` → `choices[0]` 的 `logprobs: null`【一手·本机；`r-s2-false-hit.md` §1.4】。
  → ReClick 的 `S_cross`（跨视角对数似然）**按原样不可实现**。

#### 两条候选

| | **候选 A：LFPR 式几何 guard + 中点融合** | **候选 B：GUI-RC 式两视图空间票表 + argmax** |
|---|---|---|
| 形状 | 粗框 = incumbent，精点扩 α 方块 = 候选；过 guard 则落**中点** ½p₀+½p₁，否则落粗点 | 整屏视角与 crop 视角的采样点投到**同一张全屏物理坐标票表**，取最大票连通区（GUI-RC §3.2） |
| 关键数字 | guard 接受率 **92.5%**（29,523/31,920）；guard 阈值 `z≤0.35`、`IoU≥0.25`、面积比 ∈[0.5,2.0]、γ=2.0；**按 Acc@0.5（=我们的命中口径）中点融合 > guarded replacement**；Ref-L4 mAcc₀.₅:₀.₉₅ 72.947→**76.013**、Acc@0.5 88.531→**89.725**（+1.194，95% CI [0.944,1.439]）【一手·公开 https://arxiv.org/abs/2608.19553】 | GUI-RC：SS-Pro 上**框式**通用模型 **+3.41 / +4.17 pp**（Qwen2.5-VL-3B/7B），点式只有 +0.13~+0.26 甚至 −0.82；K=64、α=50、top_p 0.95【一手·公开 https://arxiv.org/abs/2508.05615 ｜ https://github.com/ZJU-REAL/GUI-RCPO】。ReClick：argmax **无绝对阈值**，SS-Pro Qwen3-VL-8B 54.71→66.03→**72.36**【一手·公开 https://arxiv.org/abs/2610.13268】。FORUM：medoid 取真实成员、τ∈[0.3,0.7] 敏感度 ≤0.4/≤1.0 pt【一手·公开 https://arxiv.org/abs/2609.37488】 |
| 我们的适配难点 | 我们的 incumbent **是框**（`raw_bbox`）、crop 候选**只有点** → 需把点扩成区域才能算 IoU/面积比（α 需标定）【推断·`r-s1-guardrail.md` §2.2】 | ReClick 的 logprob 版不可用 → 只能做**判别式**改造（让另一个视角在两个候选里二选一），公开文献**没有**这个改造的评测数字【推断】 |
| 成本 | **零模型调用**（纯几何，可离线重算） | 需要 K 次采样（K=8）+ 新票表模块 |

#### 可判地选：**预注册的判定门（三步，按顺序短路）**

1. **第一步（零模型调用，本轮就能判）**：用**已有产物**离线重算候选 A，与现状 always 臂配对。
   * 落点：`src/minicode/eval/sspro.py` 新增两个字段 `coarse_bbox_px`（粗框的物理四元组）、`crop_rect_px`（crop 窗口物理矩形）；
     新增 `src/minicode/eval/fusion_offline.py`（照抄 `src/minicode/ground/crop_geometry.py` 的「离线重算、零模型调用」形态）。
   * **判定门**：候选 A 相对现状 always 臂 **Δ ≥ +3 条（+1.64 pp）且 mid-p McNemar p < 0.05** → **采纳 A，候选 B 不做**。
2. **第二步（若 A 不过）**：用 T1 的 K=8 采样探针回答一个决定性问题 —— **「oracle-of-8」比单次高多少？**
   * **判定门**：`oracle-of-8 ≥ 单次 + 8 pp`（即采样里至少有一次命中而单次没命中的样本够多）→ **做 B**；否则 **A/B 都不做**，
     只保留 T4 的「标记图选择 + 整屏再搜」，并把护栏这轮记为「未达成，原因：无空间冗余可挖」。
   * 依据：公开的「多视图收益上界」量级 —— MVP 单图 55.0% → 2 视图聚类 **65.3%**，但视图数消融 2/4/10 → 61.0/61.7/**60.6**（Pass@N 上界 69.0/70.2/73.0）
     【一手·公开 https://arxiv.org/abs/2512.08529 ｜ https://github.com/ZJUSCL/MVP】→ **多给视图只提高上界，不会自动提高命中率**，所以必须先测上界。
3. **第三步（兜底，永不放弃）**：**跨阶段一致性**（让精定位也出框 + 自报文字 → 跨阶段 IoU 与跨阶段文字一致）【一手·公开口径，`r-s2-false-hit.md` §3.3】。
   ⚠️ **本轮只埋点、不当门**：因为 §3.2 已经证明几何硬门净亏；且**在假命中场景里跨阶段 IoU 会很高**（粗定位与精定位都指向同一个错元素）→ 这条判据**大概率抓不到假命中**，
   这一点必须写进 §8 风险表。**唯一当门用的时机是「两组 IoU 分布不重叠」被实测证实之后**。

**硬约束（写进代码评审清单）**：任何候选都**不得**引入「离粗定位多远 → 回落」的标量距离阈值；候选 A 的 guard 只能**剔除明显离群**，
**中点融合只在「两候选同域」时用**（可证：GT bbox 是凸集，p₀ 与 p₁ 都在框内 ⇒ ½(p₀+p₁) 必在框内 ⇒ 「两者都对」那 64 条上中点 0 损失）
【推断·`r-s1-guardrail.md` §4.2】；异域一律走「**选**」不融合 —— FSR：600 条 SS-Pro 上漏检率 单模型两级放大 13.67% → 普通投票 12.33% → 簇投票 12.67% → **「选择」9.33%**
【一手·公开 https://arxiv.org/abs/2610.25503】。

---

### 卡点 ② 假命中 20 条 → 0

#### 先把账算清（这是设计的地基）

* 假命中构成【一手·本机；`runs/g2-eval-final/sspro-report.json` / `runs/g3-eval-2016/sspro-report.json`，同批 183 条，种子 20251020】：

| 量 | 1536×960 | 2016×1260 |
|---|---:|---:|
| crop 完整含住 GT 框 | 140/183 = **76.5%** | 152/183 = **83.1%** |
| crop 没含住（窗口里没有目标） | 36 | 24 |
| └ **假命中**（没含住 且 精定位点被采纳） | **20** | **19** |
| └ 逃逸口正确拒答 | 16 | 5 |
| └ 逃逸口**误拒**（含住了却拒） | 9 | 6 |
| 假命中里**粗定位本身也错**的条数 | **19/20 = 95%** | **18/19** |
| 没含住的原因 | `coarse-picked-wrong` 35、`out-of-bbox` 1 | `coarse-picked-wrong` 23、`out-of-bbox` 1 |

* **「识别出全部假命中 → 回落粗定位」的精度天花板 = 0**：g2 净 Δhit = **+0**、g3 = **−1**【一手·本机；`r-s2-false-hit.md` §1.2】。
  → **回落不是解法**（它是把一个错答案换成另一个错答案）。
* 假命中的**真正代价在真机**：点到一个「看起来像但不是」的控件 → 任务静默走偏（wave1/wave2 的 C-1/C-2 类失败机理），而**离线 ScreenSpot-Pro 完全看不见这个代价**。

#### 解法：三段式，全部推理期、训练无关、适配通用 API 模型

| 段 | 做什么 | 改哪个文件 | 依据 |
|---|---|---|---|
| ① 多候选 + 空间聚类 | 精定位不再「一次一个点」：对 crop 内 **N 个固定偏移子区域各问一次** → N 个候选点 → 贪心聚类（τ = 32 物理 px）→ 取**最密簇质心**，并落盘**簇占比** `refine_cluster_ratio` | `src/minicode/ground/channel.py`（`_locate_by_point` 精定位段）+ `src/minicode/ground/prompt.py`（新增 `refine_multi_prompt`） | MVP Algorithm 2（贪心聚类取最密簇质心）【一手·公开 https://arxiv.org/abs/2512.08529 ｜ https://github.com/ZJUSCL/MVP】；本机佐证：原文一致率只有 0.1（10 次 10 个不同答案），但落点空间自洽性高得多（`runs/m2-temperature-probe/probe-text.json`：9/10 落在中位数 32 px 内，p50 = 4.8 px）【一手·本机】 |
| ② 标记图上让模型选 | 把候选点画成**粉红五角星**标在图上，问「哪个标记是『<目标描述>』？答编号，答 0 = 清单里没有」；落盘 `refine_chosen_candidate`（编号 / 0） | `src/minicode/ground/prompt.py`（新增 `aggregate_candidates_prompt`）+ `src/minicode/ground/channel.py` | RegionFocus：把候选动作画成标记让模型在**标记图**上选（"we visually mark the candidate action coordinates on the snapshot … this significantly reduces the model's workload"），并用 VLM judge 判断对错再触发【一手·公开 https://arxiv.org/abs/2505.00684 ｜ https://github.com/tiangeluo/RegionFocus】 |
| ③ 整屏再搜（**不是回落**） | 选中的是错元素、或模型答 0、或判定不可信 → **整屏第二遍**，提示里带上「上一个候选是错的，请找一个不同的元素」+ 候选自报文字；新增 `landing_source="rescreen"`，**回落粗定位只作最后一档** | `src/minicode/ground/channel.py`（新增分支）+ `src/minicode/ground/prompt.py`（`whole_screen_prompt` 加否定提示） | `r-s2-false-hit.md` §6/§7 改动 3（本线推导）；§2.4 SafeGround 自陈失败模式「模型一致地错时低方差的错答案看起来很自信」→ **离散度拒答对我们的 20 条基本无效**（我们正是「一致地错」）【二手·公开，同上文件引用】 |

#### 预期哪个数变多少 / 怎么验证

* **假命中 20 → 0**（口径：`crop_contains_target == False 且 landing_source == "refined"` 的条数；`rescreen` 不算 refined）。
* **但归零不等于变好** → 验收必须**同时**报两件事：① 这 20 条**最终是否命中**（走再搜的命中率，公开先验估计 15~25%，即 ≥3~5 条）；② 整体 click accuracy **不显著为负**（配对 McNemar）。
  → **防止把指标做没了**：只报「假命中 20→0」而不报「这 20 条的最终命中数」的收口报告，判未达成。
* 逃逸口（负出口）precision/recall 要落盘（对齐 GUI-HalluBench 的 RR 口径）：本机现状 recall 44.4%（g2）/ 20.8%（g3）、precision 64.0%/45.5%
  【一手·本机】；公开基线：闭源模型在 fabricated 子集上的拒绝率只有 56.6%（Claude Computer Use）/ 57.2%（GPT-4o w/ grounding）/ 60.5%（Mariner）
  【二手·公开 https://openaccess.thecvf.com/content/CVPR2026/html/Zhang_Exposing_and_Evaluating_Hallucinations_for_GUI_Grounding_CVPR_2026_paper.html】。
* ⚠️ **离线评测里「假命中」现在不可见**：ScreenSpot-Pro 每条都保证目标存在 → 必须自造「目标不存在」子集（从同图取错位 crop 当负样本），
  否则「假命中归零」在离线口径里是**自证的**【一手·公开口径，`r-s2-false-hit.md` §5】。

---

### 卡点 ③ icon 子集往上推

#### 先看死数据：icon 的病不在档位

* icon 子集 **41.38%（36/87）@1536**、**43.68%（38/87）@2016**；text **68.75%（66/96）@1536**、**83.33%（80/96）@2016**【一手·本机，两份 `sspro-report.json`】。
* 同批 183 条配对 McNemar（**我本轮离线算出，零模型调用**，方法见 §3）：
  **icon：1536 独赢 12 / 2016 独赢 14，exact p = 0.845（不显著）**；text：4 / 18，exact p = 0.0043；整体 always：16 / 32，exact p = 0.0293。
  → **2016 的 +8.7 pp 全部来自 text 子集，icon 子集没有可测收益。**
* icon 在模型输入图里的表观像素【一手·本机，`model_input_target_sizes`】：
  1536 档 **中位 12.0 px**（<16 px 占 73.6%）、2016 档 **中位 15.75 px**（<16 px 占 51.7%）；对照 text 中位 66.4 / 87.15 px。
  → icon 是**亚格**目标：28 px 网格下 12~15.75 px 连**一个视觉 token 格都占不满**。
* 更要紧的一层：crop 是**从物理帧 1:1 裁、不重采样**（`src/minicode/ground/crop.py:22` `crop_frame`；`src/minicode/ground/channel.py:679`），
  所以 crop 里的 icon 仍是**源图的 20.0 物理 px**（12.0 ÷ 0.6 = 15.75 ÷ 0.7875 = 20.0，两档一致）→ **仍是 0.71 个 token 格，亚格**
  【一手·本机 + 推导，`r-s4-resolution.md` §1】。
  → **只换整屏档位，crop 里那个 icon 一点没变大。**

#### 解法（按投入产出）

| 杠杆 | 做什么 | 改哪个文件 | 依据（URL） | 预期哪个数变多少 | 怎么验证 |
|---|---|---|---|---|---|
| **L1（主杠杆）：crop 后上采样** | 把送进精定位的那张 crop **重采样放大**（当前是 1:1 字节级裁剪，完全没有放大路径），使 icon 的表观像素从 20.0 → **30~60 px（1.07~2.14 格）**；放大后的图宽必须 ≤ 2016（服务端上限） | `src/minicode/model/image.py`（**新增 `upscale_frame`** —— 现在只有 `downscale_frame`，且它明确「只支持缩小」）、`src/minicode/ground/crop.py`（新增 `upscale_crop`）、`src/minicode/ground/channel.py:685`（`crop_input` 改用放大后尺寸）+ `src/minicode/ground/prompt.py`（`refine_prompt` 文案：**现在写着「未缩放，1 像素 = 1 屏幕像素」，放大后这句话是假话**） | MAI-UI 公开配方「crop → resize 到 1920×1080 → 二次定位」：Gemini-3-Pro **39.0→69.7**、Claude Sonnet 4.5 **35.0→54.0**【二手·公开（Notion 原文 JS 空壳，数字来自中文逐节转载）https://blog.csdn.net/weixin_43427721/article/details/159436148 ｜ 官方仓库 https://github.com/Tongyi-MAI/MobileWorld】；28 px 网格与「2K 最好、4K 下降」【二手·公开 https://platform.claude.com/docs/en/build-with-claude/vision-coordinates ｜ https://openreview.net/pdf?id=yBFUqdJFZn】；RegionFocus +28.9 pp【二手·公开 https://arxiv.org/abs/2505.00684】 | icon 表观中位 **15.75 → 30~60 px**（n=87 评测台口径）；icon 子集 **43.68% → ≥48%**【推断】；整体 **+2~6 pp**【推断】 | `sspro-report.json → model_input_target_sizes.icon.median_px`（n=87）必须 ≥30；icon 分层配对 McNemar；**同时**报 text 子集不得下降 |
| **L2：crop 比例扫描（必须在 L1 之后）** | 在 {0.5, 0.35, 0.25} 线性比例上 A/B；**保持宽高比**（ScreenSpot-Pro 含多屏拼接全景图，强行固定比例会拉伸变形） | `src/minicode/ground/channel.py:172`（`CROP_LINEAR_FRACTION`）+ `src/minicode/eval/__main__.py`（已有 `--crop-width/--crop-height`） | 本机离线几何：0.5× 当前帧含住率 **84.2%**、192 档 42.1%【一手·本机 `runs/m3-crop-geometry/crop-geometry.json`】；公开：Gemini-3-Pro 最优/最差裁剪配置差 **11.4 分**（1/4 最优）【二手·公开 https://blog.csdn.net/weixin_43427721/article/details/159436148】；ReGround 的 crop size 是**论文级消融变量**【一手·公开 https://arxiv.org/abs/2504.07981】 | icon **±3 pp**【推断】；**硬约束**：crop 含住率不得低于 76.5%（1536 基线），否则假命中会涨 | 配对跑批；**先 L1 后扫比例**（放大补偿含住率损失） |
| **L3：把「无文字图标」变成候选清单里的一行** | 视觉层出框 → 与 UIA 合并（IoU>0.1 丢弃重复）→ 预筛留 TOP_K → 进编号分支 | `src/minicode/ground/candidates.py`（新增 `VisualIconCandidateSource`）+ `src/minicode/ground/icon_filter.py` | UFO² 一手配置：`TOP_K_ICON=15`、`clip-ViT-B-32`、`BOX_THRESHOLD=0.05`、`IMGSZ=640`、`IOU_THRESHOLD_FOR_MERGE=0.1`【一手·公开 https://raw.githubusercontent.com/microsoft/UFO/main/config/ufo/system.yaml】；OmniParser v2 = **MIT** 的 YOLOv9-E `icon_detect_v3` + MIT caption 模型（AGPL 只在旧 v1/v1.5）【一手·公开 https://github.com/microsoft/OmniParser】；SoM 的收益**全部来自候选集**（oracle marking：OS-Atlas 10→52、Qwen2.5-VL 22→57）【二手·公开 https://export.arxiv.org/pdf/2608.21832】 | **本轮不接生产**：先做**离线候选覆盖率测量**（GT bbox 被候选框覆盖的比例），门槛 **≥95%** 才允许接模型；达不到就调 `BOX_THRESHOLD`/`IMGSZ` 再测 | 离线脚本，不调模型、不进主链路 |
| **L4：编号分支白进（本轮不收紧）** | 入口条件现在只有 `if candidates:`（`src/minicode/ground/channel.py:497`），全库无覆盖率门槛 | — | 收紧会改变调用分布，属行为改动 | **本轮明确不做**（理由见 §7） | — |

⚠️ **L3/L4 对 SS-Pro 的 icon 数没有直接贡献**：编号分支**不进 ScreenSpot-Pro 三臂**（`src/minicode/eval/sspro.py` 的臂只有 always/on_demand/coarse_only）
→ L3/L4 服务的是**真机**上的图标控件定位。**SS-Pro icon 子集这轮只靠 L1 + L2。**

---

### 卡点 ④ 真机端到端 wave1（0/5）/ wave2（3/7）

#### 卡点诊断（上一轮的失败**不在视觉落点**）

wave1 的失败全部来自**环境/树**：W-1.1 期望「判不了」、W-1.2 动作失败（`locate_failed`：整屏第二遍答负出口「这张图里没有对应的元素」，而「屏幕」**就在**设置页里 → **负出口制造假否定**）、
W-3.2 期望不成立（树里没有时钟文本）【一手·本机 `runs/v1-wave1c/e2e-report.json`】。
而 wave1 的 W-3 两次落点都是 **A 命中（到矩形 0.0 px，到中心 27.8 px）** —— 但那两步的 `channel` 是「放大」不是视觉通道 → **视觉通道本身的真机落点精度至今零证据**。

#### 四个子卡点 + 一个跨切面

| # | 卡点 | 改哪个文件 | 依据（URL） | 预期哪个数变多少 | 怎么验证 |
|---|---|---|---|---|---|
| ① | **准入门抽签**（跨切面，收益最大）：同一张探针、同一模型，四次跑分别 1.06 / 2.66 / 68.00 / 68.81 px → W-1.2 与 CC-1 都是「视觉通道本来能用，被准入闸门拦成**零动作下发**」 | `src/minicode/calib/tolerance.py:79`（`CALIBRATION_ROUNDS` **5 → 21**）+ `src/minicode/calib/calibrate.py:715-716`（闸门统计改用**已现成的** `median_error_px` / `p90_error_px`，不用 max）+ `src/minicode/calib/test_calib_probe.py:325`（断言 `CALIBRATION_ROUNDS == 5` 要改） | 【一手·本机 `docs/reviews/M15.md` §4⑧】；统计口径【推断，`r-s5-windows-e2e.md` §8.2】：n=20 时 SE≈0.089 → 跨运行翻过 0.6 阈值是常事；K=21 时 n=84 → SE≈0.043 | W-1.2 与 CC-1 从「零动作下发」变成**真跑**；wave1 **+1 票**；wave2 **+1 票** | `python -m minicode.calib` 连跑 8 次看 `hit_rate` 跨运行方差；再跑 e2e 看 `visual_channel_unavailable` 归零。**需要真机跑、锁直接拿** |
| ② | **Windows 设置的 XAML 树不在窗口句柄下**（`CoreWindow` 类应用的树注册在桌面根上、与窗口同 PID） | `src/minicode/uia/windows.py:322-336`（`_find`）、`:472-545`（`_assess`）—— **不改冻结目录**：`exec/uia.py` 已有 `UIA.root()`（`:521`）、`Element.process_id`（`:371`）、`build_condition()` 无参即 TrueCondition（`:600-606`）→ 在 `uia/windows.py` 里做「桌面根 → `find_all(TrueCondition, TreeScope_Descendants)` → 按 `process_id` 过滤 → 复用现有名字过滤」即可 | trycua/cua PR #1606（**一手·公开，标题逐字**）："fix(cua-driver-rs/windows): UIA root-walk fallback for CoreWindow-class apps (Calculator, Settings, older UWPs)"【https://github.com/trycua/cua/pull/1606/files】；取值兜底链 TextPattern → ValuePattern → SelectionPattern → Name【二手·公开 https://learn.microsoft.com/en-us/windows/apps/dev-tools/winapp-cli/ui-automation】；`SelectionItemPattern` **无数值**，应取选中项 `Name`【同上】 | Windows 设置能力表 `sparse` → 可用；W-1.1 / W-1.2 的控件框真值从 undecidable → **可判**；W-1.E「缩放值与界面显示一致」成立 | `python -m minicode.uia.live --target 缩放 --out runs/v2-rootwalk`；看「可用交互控件数」与 W-1.1 的真值来源。**需要真机跑、锁直接拿** |
| ③ | **浏览器通道没有滚动动作**（`ChromiumPage` 只有 `insert_text`/`invoke`/`invoke_box`，`e2e/real.py:939-952` 对 `ActionKind.SCROLL` 显式返回 `channel_unsupported`） | `src/minicode/browser/cdp.py`（`ChromiumPage` 新增 `scroll_lines(element_id, lines)`，与 `invoke` 同构）+ `src/minicode/e2e/real.py:939-952`（换成真实现并写 `scroll_lines` / `scroll_stable` 两个上下文键）+ `src/minicode/e2e/apps.py:2555`（`VerificationPage._html()` 加**可滚列表（>1 屏高）+ 搜索框 + 城市筛选下拉**）+ `src/minicode/e2e/real.py:837-844`（`launch()` + `new_context()` → `launch_persistent_context` 复用登录态） | CDP `Input.dispatchMouseEvent` `type:"mouseWheel"`（delta 单位 = **CSS 像素**，事件落在光标处）【二手·公开 https://chromedevtools.github.io/devtools-protocol/tot/Input/#method-dispatchMouseEvent】；Playwright `mouse.wheel` 原文「does not wait for the scrolling to finish」【一手·公开 https://playwright.dev/docs/api/class-mouse#mouse-wheel】；`launch_persistent_context` 复用 user_data_dir 登录态【二手·公开 https://playwright.dev/docs/api/class-browsertype#browser-type-launch-persistent-context】；WAA 官方最佳配置 `--som-origin mixed-omni --a11y-backend uia`，官方 `oss` baseline 里就带 OCR【一手·公开 https://raw.githubusercontent.com/microsoft/WindowsAgentArena/main/README.md】 | C-1 / C-2 / C-3 从 **SKIP（环境不可达：未登录）→ 可跑**；wave1 可判票 **2 → 5** | `python -m minicode.e2e --tasks C-1,C-2,C-3`；上下文出现 `scroll_lines=3` / `scroll_stable=True` / `city_filter_applied=True`。**需要真机跑、锁直接拿** |
| ④ | **验收要的控件框读不到 → 记 undecidable** | `src/minicode/e2e/real.py:507`（`_resolve_truth()`：树里没有 `truth_element` 时改用 OCR 词级框）+ `src/minicode/verification/ocr_windows.py:203-217`（`WindowsOcrPort.read_words`，**1 像素 = 1 物理像素**，加帧原点即物理表面坐标） | 本仓已有 OCR 词级框【一手·本机 `src/minicode/verification/ocr_windows.py:203-217`】；本仓已确立「画面量的真值」口径（W-3 的 `CLOCK_RECT` 注释写明「**从画面量出来的**」）【一手·本机 `src/minicode/e2e/wave1.py:60`】 | 所有「缺控件框 → undecidable」的落点条款变成 **A/B/C 三档可判**；**视觉步骤可判率 wave1 0/2 → 100%** | `python -m minicode.e2e --tasks W-1,W-3`；`landing_error_px` 不再是「缺真值」。**需要真机跑、锁直接拿** |
| ⑤ | **「人工代确认」不写成阻塞项**（W-2 / WX-2 / WX-3 / CC-1） | `src/minicode/e2e/wave1.py:132-164`（W-2）、`src/minicode/e2e/wave2.py:125-198 / :347-442`（WX-2 / WX-3 / CC-1）的 `human_confirm` 步骤改成 **agent 代确认**（自己执行 + 自己读回验收） | 用户授权（真机完全授权、锁直接拿）；WAA 把「交人」做成显式条款并要求交接产物齐（WX-3.A 的口径本身就是合规停手）【一手·公开 https://raw.githubusercontent.com/microsoft/WindowsAgentArena/main/README.md】 | **主口径**（沿用上一轮，剔除人工代确认票）：wave1 **0/5 → ≥3/5**、wave2 **3/7 → ≥5/7**；**全票口径**（代确认票由 agent 代确认后计入）：wave1 **≥4/6**、wave2 **≥6/9** | `python -m minicode.e2e --tasks ... --out runs/v2-wave1`。**需要真机跑、锁直接拿** |

#### 现实目标

公开任务级成功率用于定目标（WAA 154 票、Navi 最好配置 19.5%、人类基线 74.5%）
【一手/二手·公开 https://arxiv.org/abs/2409.08264 ｜ https://raw.githubusercontent.com/microsoft/WindowsAgentArena/main/README.md】。
⚠️ **不可比**：我们每波只有 5~7 票，且 wave2 跑的是本地验证页 + 自造模态弹窗（已自承削弱代表性）。
→ 目标按「**每条改动各解一个卡点**」定，不按公开比例定：**wave1 ≥3/5、wave2 ≥5/7**（= 准入门修好 +1、根树修好 +1、浏览器三票 +1~3）。

---

## 三、分辨率与档位的决定

### 决定：**生产档改 2016×1260**（性能优先；token 不是约束）

| 候选 | 判定 |
|---|---|
| **2016×1260** | ✅ **采纳**。① 探针**通过**：期望 3240 / 实测 3242（差 +2 = 网关每图固定开销，扣除后差 **0**）→ **坐标系可信**【一手·本机 `runs/m6-token-probe-2016/token-probe.json`】；② 同批 183 条配对 **exact p = 0.0293**（整体）；③ **token 不是约束**（用户给定） |
| 1536×960 | 保留为**对照臂**（不能删：它是配对判定的左臂） |
| 2048×1280 / 2044×1260 / 2560 | ❌ **封死**。服务端宽度上限 **≈2016 px** 实测：2048×1280 期望 3404 / 实测 **3242**（差 −162）、2044×1260 期望 3285 / 实测 3242（差 −45）→ 那两档坐标系不可信【一手·本机 `runs/m6-token-probe-2048exact/token-probe.json`、`runs/m6-token-probe-final*/token-probe.json`】；且公开曲线显示 grounding 精度 **~2K 最好、4K 反而下降**【二手·公开 https://openreview.net/pdf?id=yBFUqdJFZn】 |

**为什么这一轮和上一轮的结论不同（必须写明）**：
上一轮用「**icon 表观中位 ≥16 px**」这个**代理指标**把 2016 档退回 1536（2016 档评测台口径 icon 中位 15.75 px，差 0.25 px）。
这一轮用**配对命中数据**重判：**icon 子集 41.38% → 43.68%，配对 exact p = 0.845（不显著）**；而**整体 +8.7 pp 显著（p = 0.0293）、text 子集 p = 0.0043**。
→ 上一轮那条代理指标规则**没有命中真实收益**；icon 的问题**不在整屏档位**，在 crop 侧（L1）与候选源（L3）。

### 「同批 183 条配对 McNemar」的具体做法（**零模型调用**）

**为什么能零模型调用**：两个档位的跑批产物里**已经有逐样本的命中判定**，配对只需要**按样本 ID 对齐**再算一张 2×2 表。

```
输入（全部是本机已有产物，无需再调模型）：
  runs/g2-eval-final/sspro-report.json    # 1536×960 档，同批 183 条，种子 20251020
  runs/g3-eval-2016/sspro-report.json     # 2016×1260 档，同一批 183 条，同种子
步骤：
  1) 读两份报告的 outcomes.always，按 sample_id 取交集 → 183/183
  2) 建 2×2 表：
       b = #(1536 命中 且 2016 不命中)
       c = #(1536 不命中 且 2016 命中)
  3) McNemar 精确二项检验（等价于「在 b+c 次不一致里，c 次纯属运气」的概率）
       p = min(1, 2 · Σ_{k=0}^{min(b,c)} C(b+c,k) / 2^(b+c))
     （仓内已有实现：src/minicode/eval/stats.py::mcnemar_exact / mcnemar_midp）
  4) 同一套步骤再跑一遍 outcomes.coarse_only → 得到「同一模型、同一提示词、只差整屏分辨率」的**噪声对照**
  5) 分层：把同一套统计在 ui_type ∈ {icon, text} 上各跑一遍
```
**判读门槛（预注册）**：
* **采纳某档** ⇔ `p < 0.05` **且** 净差 `|c − b| / 183 ≥ 3 pp`（≥ 6 条）**且** 该档在 `coarse_only` 层的对照**没有**同等幅度的净差（否则判为随机性）。
* **不做跨档结论**：任何「A 档比 B 档高 X pp」的说法，只在**同跑批内**的配对结论上使用；跨跑批的绝对值比较一律标注「混着模型随机性」。

**本轮已经算出来的结果（我本轮离线算出，零模型调用，可复核）**：

| 比较 | b（1536 独赢） | c（2016 独赢） | n 不一致 | exact p | 判定 |
|---|---:|---:|---:|---:|---|
| overall（always 臂） | 16 | 32 | 48 | **0.0293** | ✅ 显著，净 +16 条（+8.7 pp）→ **采纳 2016** |
| **icon 子集**（n=87） | 12 | 14 | 26 | **0.845** | ❌ 不显著 → **icon 不靠档位** |
| text 子集（n=96） | 4 | 18 | 22 | **0.00434** | ✅ 显著，净 +14 条 → **2016 的收益全部在 text** |
| coarse_only（噪声对照） | 24 | 25 | 49 | **1.0** | 净差 ≈ 0 → 粗定位层**分辨率无收益**，噪声全在重抽里 |

### ⚠️ 配对**消不掉**的那部分噪声（必须写进结论）

* 两次跑批的**粗定位点只有 2/183 完全相同**；`coarse_only` 臂的命中一致率只有 **134/183 = 73.2%**【一手·本机，我本轮离线算出】。
  → 配对消掉的是「**样本难度**」的噪声，**没有**消掉「**模型重抽**」的噪声（网关忽略 temperature：3 轮 × 10 次连发原文一致率三轮都是 **10%**、`distinct = 10/10`
  【一手·本机 `runs/m2-temperature-probe/probe.json`】）。
* 且**档位效应全部发生在粗定位那一级**：crop 与精定位是**从物理帧 1:1 裁出来的**，与档位无关（`src/minicode/ground/crop.py:22`、`channel.py:679/:685`）
  → 两次跑批的精定位差异**全部是「粗定位重抽 → 窗口不同 → 精定位不同」的传递**，不是「档位直接改了精定位」。
* **因此采纳 2016 的同时必须做 T1（K=8 采样池）把噪声钉住**：判据 = **2016 档粗定位命中率的 95% CI 下界 > 1536 档的上界**。做不到就说明那 +8.7 pp 里有相当部分来自运气。
* 本机侧还有一条**可用来判读的口径**：官方 ScreenSpot-Pro 通用 API 协议本身允许空输出（`[]` / 截断=0 分）
  【二手·公开 https://github.com/modelscope/evalscope】→ 「拒答」是合规动作，问题只在精度；这一条也是 T4 再搜机制的合规依据。

---

## 四、下一轮验收线（逐条可判定）

> **分母统一**：除特别标注外，ScreenSpot-Pro 一律用**本机 183 条分层抽样**（种子 20251020，分层后本地有图的），判定 = **官方 bbox 命中**（`src/minicode/eval/formats.py::SCREENSPOT_PRO`）。
> **配对纪律**：所有「A 相对 B」的结论必须**配对**（同批同样本、最好共用同一次上游回答）；绝对值差要标「跨跑批、混随机性」。

| # | 指标 | 现在 | 目标 | 怎么测（可判定） |
|---|---|---|---|---|
| **1** | **整体 click accuracy** | **55.74%**（1536 档，102/183，Wilson 95% CI [48.50%, 62.74%]）；**64.48%**（2016 档，118/183） | **≥70%**（点估计）；**硬底线 ≥64.48%**（不得低于 2016 档实测值） | 同批 183 条、always 臂、官方 bbox 命中；报 Wilson 95% CI + 与 2016 基线的**配对 mid-p McNemar**（Δ 的 95% CI 下界 > 0）。⚠️ 183 条抽样 CI 宽 ±7 pp，**不得与公开全量 1581 直接比** |
| **2** | **icon 子集 click accuracy** | **41.38%**（1536，36/87，Wilson [31.61%, 51.88%]）；**43.68%**（2016，38/87） | **≥48%**（≥42/87） | icon 分层（`comparison.always.accuracy_by_ui_type`）+ 与 2016 基线**配对 McNemar（n=87）**；**同时**报 `model_input_target_sizes.icon.median_px` 必须 **≥30 px**（n=87 评测台口径，现在 15.75） |
| **3** | **假命中** | **20 条**（1536）/ **19 条**（2016）；口径 = `crop_contains_target == False 且 landing_source == "refined"` | **0 条** | `sspro-report.json → outcomes` 逐条判定。**必须同时报两个数**：① 假命中 0 条；② **这 20 条的最终命中数 ≥ 3**（走 `rescreen` 的命中数）—— 只报①不报②判未达成 |
| **4** | **回落目标（硬失败回落率）** | **14.2%**（25/176 开窗，1536 档；全部是 `no_output`）；**6.25%**（11/176，2016 档） | **≤10%**（主口径，1536 档）；**≤6%**（2016 档） | `telemetry.fallback_cause_distribution` ÷ 开窗次数。**附加硬条件**：回落时**不得降级到更差的一级**（落点来源分布里 `landing_source` 不得出现「比回落前更差」的档） |
| **5** | **护栏取舍正确率** | **无护栏**（不可定义）。基线参考：always 臂「精定位改坏 **11** 条（1536）/ **6** 条（2016）」，即分歧里精定位占 22.4%（11/49） | **改坏条数 ≤3**；**护栏取舍正确率 ≥60%**；护栏臂相对 always 臂 **配对 Δ ≥ +3 条且 p<0.05** | **护栏取舍正确率 = |{护栏臂命中 ∧ always 臂不命中}| / |{护栏臂与 always 臂命中结论不同的样本}|**。四臂（always / 候选A / 候选B / 现状）在**同批 183 条、共用同一次粗定位回答**上跑；配对 mid-p McNemar + 配对 bootstrap Δ 的 95% CI |
| **6** | **端到端 wave1** | **0/5**（通过 0 / 失败 3 / infeasible 2 / 人工代跑 1） | **≥3/5**（主口径）；全票口径 **≥4/6** | 真机跑 `python -m minicode.e2e --tasks W-1,W-2,W-3,C-1,C-2,C-3 --out runs/v2-wave1`。**需要真机跑、锁直接拿**。判定读 `e2e-report.json` 的任务级通过数；**「人工代确认」不算失败**（改成 agent 代确认） |
| **7** | **端到端 wave2** | **3/7 = 42.9%**（通过 3 / 失败 2 / infeasible 1 / 合规停手 1 / 人工代跑 2） | **≥5/7**（主口径）；全票口径 **≥6/9** | 真机跑 `--tasks WX-1,WX-2,WX-3,E-1,E-2,E-3,CC-1,CC-2,CC-3 --out runs/v2-wave2`。**需要真机跑、锁直接拿** |
| **8** | **视觉步骤可判率** | wave1 **0/2 = 0%**（`visual_step_decidable_rate = 0.0`；四档分类率才是 100%）；wave2 **1/1**；合计 **1/3 = 33.3%** | **wave1 100%**；合计 **≥90%**；**undecidable 只允许「真值确实拿不到」**（须写明缺哪条真值） | `e2e-report.json → landing.visual_step_decidable_rate` + `landing.visual_steps` 的四档分布。**附带**：真机**视觉通道**落点精度要拿到**首批证据** —— 现在是 **0 证据**（W-3 那两步的 `channel` 是「放大」不是视觉通道）：目标 **≥3 条视觉通道落点真机实测，median 到矩形 = 0 px，A 命中 ≥2/3** |

**新增的一条记录性指标（不设目标，但必须落盘）**：**逃逸口（负出口）precision / recall / 误拒数**（本机现在 recall 44.4%/20.8%、precision 64.0%/45.5%；误拒 9 条/6 条）
【一手·本机】—— 对齐 GUI-HalluBench 的 RR / LA 口径【一手（框架）·公开】。

---

## 五、施工清单（**10 条**，按依赖顺序，可直接交给执行会话开工）

> **依赖链**：T1（采样池）→ T2（档位）/ T3（放大）/ T4（多候选+再搜）→ T5（护栏判定）；T6~T9（真机）→ T10（重跑两波）。
> **T3 / T4 / T5 都要 T1 的采样池**；**T2/T3/T4 的验证都要 §3 的配对 McNemar**。
> **T6~T10 全部需要真机跑、锁直接拿**（`.scratch/desktop.lock`，`src/minicode/lock.py::acquire(ticket)`，agent 身份写、直接用、直接释放）。

### T1 · 采样池 + K 次重复采样（**地基，0 分变化**）
* **改哪个文件**：`src/minicode/ground/channel.py`（`_ask` / `_locate_by_point` 段）、`src/minicode/model/client.py`（`complete()` 旁路）、`src/minicode/eval/sspro.py`（`ArmOutcome` 落 K 条原始回答）、新增 `src/minicode/ground/response_pool.py`。
* **做什么**：粗定位与精定位各采 **K=8** 次；**每次用不同的窗口/措辞**（同提示词重发无效：同图同提示词 10 次原文一致率只有 **10%**）；8 条原文与各自落点**全部落盘**；所有臂从**同一份池子**取。
* **依据**：`r-s6-multi-vote.md` §7 改动 #1/#2（公开锚：MVP τ=14 px 空间聚类、UI-Zoomer N=8 是「门控/方差用途」的甜点、GUI-RC K=64 是「当最终答案」的用法）；
  `r-s1-guardrail.md` §6 改动 7（响应池）；`r-s3-icon.md` 改动 ⑤ 的硬约束（同提示词连发无效，必须靠不同窗口/措辞制造独立样本）。
  URL：https://arxiv.org/abs/2508.05615 ｜ https://arxiv.org/abs/2604.14113 ｜ https://arxiv.org/abs/2512.08529
* **预期哪个数变多少**：**0 分变化**；产出两个决定性数 —— ① **oracle-of-8**（采样里至少一次命中而单次没命中的样本比例）；② **档位噪声基线**（同档 K=8 的命中数分布）。
* **怎么验证**：`runs/s6-vote-probe/vote-probe.json`；判据：**oracle-of-8 ≥ 单次 + 8 pp 才继续做 T5 的候选 B**；同一臂跑两遍逐条上游原文一致率 **100%**（重放即零随机性差）。

### T2 · 生产档改 2016×1260（+8.7 pp，**已实测**）
* **改哪个文件**：`src/minicode/model/image.py:45-46`（`MODEL_INPUT_MAX_WIDTH_PX` 1536→**2016**、`MODEL_INPUT_MAX_HEIGHT_PX` 960→**1260**）、`src/minicode/eval/__main__.py`（加 `--model-input WxH`，让档位是 **CLI 参数**而不是源码常量）。
* **做什么**：生产档切到 2016×1260；评测台同时保留 1536 作为**对照臂**，两臂**共用同一次粗定位回答**（`--memo-cache` 已存在）。
* **依据**：本机配对 McNemar（我本轮离线算出，零模型调用，见 §3）；探针 `runs/m6-token-probe-2016/token-probe.json`（期望 3240 / 实测 3242 = 网关固定开销 → 坐标系可信）。
* **预期哪个数变多少**：整体 **55.74% → 64.48%**（+8.74 pp，配对 exact p = 0.0293）；**icon 41.38% → 43.68%（不显著，p = 0.845）**；text **68.75% → 83.33%（p = 0.0043）**；每目标图像 token 4989.23（2016 档实测）—— **token 不是约束，不作取舍理由**。
* **怎么验证**：同批 183 条、always 臂；Wilson CI + 与 1536 的配对 McNemar；**并按 T1 的 K=8 把「档位效应」与「重抽噪声」解耦**（判据：2016 粗定位命中率 CI 下界 > 1536 上界）。

### T3 · 精定位 crop 放大（icon 的主杠杆）
* **改哪个文件**：`src/minicode/model/image.py`（**新增 `upscale_frame`** —— 现有 `downscale_frame` 明确「只支持缩小」）、`src/minicode/ground/crop.py`（新增 `upscale_crop`）、`src/minicode/ground/channel.py:679-689`（crop 放大 + `crop_input` 用放大后尺寸）、`src/minicode/ground/prompt.py`（`refine_prompt` 文案）。
* **做什么**：crop 从**物理帧 1:1 裁**后**重采样放大**到「目标表观像素 ≥30 px 且图宽 ≤2016」（源 icon 20.0 物理 px → 放大 1.5~3 倍）；**prompt 文案必须同步改**：现在写着「未缩放，1 像素 = 1 屏幕像素」，放大后这句话是假话 → 改成「这张图是由屏幕上一块区域放大 N 倍而来，1 像素 = 1/N 屏幕像素」（坐标契约仍是图内坐标）。
  **换算链不需要改**：`channel.py:747` 的 `crop_frame(plan, model_input_size=crop_input)` 已经把「capture 尺寸」与「model_input 尺寸」分开（`src/minicode/coord/crop_refine.py:268-288`）→ 只改送出的 PNG 尺寸与文案即可。
  ⚠️ **`src/minicode/coord/` 是冻结目录（AGENTS.md:68）→ 一律不改**。
* **依据**：MAI-UI 公开配方「crop → resize 到 1920×1080 → 二次定位」：Gemini-3-Pro **39.0→69.7**、Claude Sonnet 4.5 **35.0→54.0**
  【二手·公开 https://blog.csdn.net/weixin_43427721/article/details/159436148 ｜ https://github.com/Tongyi-MAI/MobileWorld】；
  28 px 网格量化（本机探针已验证：1536×960 → 期望 1925 / 实测 1927）【一手·本机】；RegionFocus +28.9 pp【二手·公开 https://arxiv.org/abs/2505.00684】。
* **预期哪个数变多少**：icon 表观中位 **15.75 → 30~60 px**（n=87 评测台口径）；icon 子集 **43.68% → ≥48%**【推断】；整体 **+2~6 pp**【推断】。
* **怎么验证**：`sspro-report.json → model_input_target_sizes.icon.median_px ≥ 30`；icon 分层配对 McNemar；text 子集不得下降；**单测**：送出的 PNG 的 IHDR 宽高必须等于 `GroundingEcho.sent_width/height`（防静默偏移一个放大倍数）。
* **T3 的第二步（放大做完再做）**：crop 比例扫描 {0.5, 0.35, 0.25}，**保持宽高比**（ScreenSpot-Pro 含多屏拼接全景图，固定比例会拉伸变形）；硬约束：crop 含住率 ≥76.5%。

### T4 · 精定位多候选 + 标记图选择 + 整屏再搜（假命中 20 → 0）
* **改哪个文件**：`src/minicode/ground/channel.py`（`_locate_by_point` 精定位段：N 个候选 → 聚类 → 标记图选择 → 再搜分支）、`src/minicode/ground/prompt.py`（新增 `refine_multi_prompt`、`aggregate_candidates_prompt`，改 `whole_screen_prompt`）、`src/minicode/ground/gate.py`（新增 `FallbackCause.RESCREEN`；**新增 `landing_source="rescreen"`**）。
* **做什么**：① crop 内 N 个固定偏移子区域各问一次 → 候选点集 → 贪心聚类（τ=32 物理 px）→ **最密簇质心** + 落盘 `refine_cluster_ratio`；② 把候选点画成**粉红五角星**标在 crop 上，让模型答编号（**答 0 = 不可信**）；③ 不可信 → **整屏再搜**（提示带「上一个候选是错的」+ 候选自报文字），**不回落粗定位**；回落只作最后一档。
* **依据**：MVP Algorithm 2 + 「28 px 边框 → 7.3% 正确→错误 / 7.8% 错误→正确、平均位移 193 px」【一手·公开 https://arxiv.org/abs/2512.08529 ｜ https://github.com/ZJUSCL/MVP】；
  RegionFocus 的 image-as-map 与动作聚合【一手·公开 https://arxiv.org/abs/2505.00684 ｜ https://github.com/tiangeluo/RegionFocus】；
  FSR「选择 9.33% vs 投票 12.33%」【一手·公开 https://arxiv.org/abs/2610.25503】。
* **预期哪个数变多少**：假命中 **20 → 0**；「精定位改坏」**11（1536）/ 6（2016）→ ≤3**；整体 **+2~5 pp**【推断】；逃逸口误拒（现在 9/6 条）下降。
* **怎么验证**：`telemetry.fake_hits` 计数；配对 McNemar；**新落盘**：`refine_cluster_ratio` 与 `refine_chosen_candidate`（编号/0），并验证「答 0 的样本命中率显著低于选中的样本」（离线可核）—— 这一条是 RegionFocus judge 在我们模型上准不准的唯一测法。

### T5 · 护栏：两条候选离线重算 + 可判选择（**零模型调用**）
* **改哪个文件**：`src/minicode/eval/sspro.py`（`ArmOutcome` 新增 `coarse_bbox_px`、`crop_rect_px` 两个字段 —— 现在只落了归一化的 `coarse_bbox_model`）、新增 `src/minicode/eval/fusion_offline.py`（照抄 `src/minicode/ground/crop_geometry.py` 的离线重算形态）。
* **做什么**：用**已有产物**离线重算 **候选 A（LFPR 式 guard + 中点融合）** 与 **候选 B（两视图空间票表 + argmax）**；按 §2 的**三步判定门**短路选择；**不引入任何「离粗定位多远 → 回落」的标量阈值**。
* **依据**：LFPR guard 阈值与因子分解（guard 接受率 92.5%；Acc@0.5 中点融合 > guarded replacement；无 guard 对照 −2.926/−3.969/−5.748）【一手·公开 https://arxiv.org/abs/2608.19553】；
  GUI-RC（框式通用模型 +3.41/+4.17；K=64、α=50）【一手·公开 https://arxiv.org/abs/2508.05615 ｜ https://github.com/ZJU-REAL/GUI-RCPO】；
  ReClick argmax（无阈值）【一手·公开 https://arxiv.org/abs/2610.13268】；FORUM medoid（τ 不敏感）【一手·公开 https://arxiv.org/abs/2609.37488】。
* **预期哪个数变多少**：候选 A 相对现状 always **Δ ≥ +3 条（+1.64 pp）**；「改坏条数」**11 → ≤3**；护栏取舍正确率 **≥60%**。
* **怎么验证**：`python -m minicode.eval.fusion_offline --run runs/g3-eval-2016/sspro-report.json`（**零模型调用**）；配对 mid-p McNemar；**候选 A 不过门**时才用 T1 的 oracle-of-8 决定是否做 B。

### T6 · 真机：准入门去抽签（`CALIBRATION_ROUNDS` 5 → 21）
* **改哪个文件**：`src/minicode/calib/tolerance.py:79`、`src/minicode/calib/calibrate.py:582/:698-700/:715-716`、`src/minicode/calib/test_calib_probe.py:325`（断言 `CALIBRATION_ROUNDS == 5` 必须同步改）。
* **做什么**：K 5 → **21**；闸门统计从 **max** 改成**已现成的 `median_error_px` / `p90_error_px`**（`calibrate.py:715-716` 已经在算）。
* **依据**：M15 实测四连抽签 **1.06 / 2.66 / 68.00 / 68.81 px**【一手·本机 `docs/reviews/M15.md` §4⑧】；闸门口径 = 「落点等价族最大分歧 ≤4 px 且命中率 ≥0.6」（`src/minicode/calib/admission.py:76-78`）；统计口径【推断，`r-s5-windows-e2e.md` §8.2】：n=20 时 SE≈0.089 → 跨运行翻阈值是常事，K=21 时 n=84 → SE≈0.043。
* **预期哪个数变多少**：**W-1.2 与 CC-1 从「零动作下发」变成真跑**；wave1 **+1 票**；wave2 **+1 票**；准入门跨运行翻转率「常事 → 罕见」。
* **怎么验证**：`python -m minicode.calib` 连跑 **8 次**，看 `hit_rate` 的跨运行方差（M15 的四连抽签消失）；再跑 e2e 确认不再出现 `visual_channel_unavailable`。**需要真机跑、锁直接拿**。

### T7 · 真机：XAML root-walk + 选中项读值（**不改冻结目录**）
* **改哪个文件**：`src/minicode/uia/windows.py:322-336`（`_find`）、`:338-386`（`_query` / `_find_with`）、`:472-545`（`_assess`）、`:154-179`（`_read_value`）。
  **实现路径（避免动冻结目录 `src/minicode/exec/`）**：冻结原型的公开接口已经够用 —— `uia.UIA().root()`（`exec/uia.py:521`）、`Element.process_id`（`:371`）、
  `controller.build_condition()` 无参即 TrueCondition（`:600-606`）→ 在 `uia/windows.py` 里做「桌面根 → `find_all(TrueCondition, TreeScope_Descendants)` → 按 `process_id` 过滤 → 复用现有名字过滤」。
  ⚠️ **只用单条件**（`windows.py:341-343` 注释：冻结原型的 `build_condition` 两个条件并存会走 `CreateAndCondition`，**在本机崩** access violation）。
  ⚠️ 注意：进程 id 常量是 **`UIA_ProcessIdPropertyId = 30002`**（`exec/uia.py:236`）；`30020` 是 **NativeWindowHandle**，别照抄错值。
  取值兜底链：`SelectionPattern.GetSelection()` → 选中项 `Name` → `LegacyIAccessiblePattern.Value` → `TextPattern.DocumentRange.GetText()`（`exec/uia.py:433` 已有 `get_pattern` 样板，**不引入新依赖**）。
* **依据**：trycua/cua PR #1606（**一手·公开，标题逐字**）"fix(cua-driver-rs/windows): UIA root-walk fallback for CoreWindow-class apps (Calculator, Settings, older UWPs)"【https://github.com/trycua/cua/pull/1606/files】；
  取值兜底链【二手·公开 https://learn.microsoft.com/en-us/windows/apps/dev-tools/winapp-cli/ui-automation】；
  `SelectionItemPattern` **无数值**、容器 `SelectionPattern.GetSelection()` 才有【同上】。
* **预期哪个数变多少**：Windows 设置能力表 `sparse` → **可用**（可用交互控件数上升）；W-1.1 / W-1.2 的**控件框真值**从 undecidable → **可判**；**W-1.E「读出的缩放值与界面显示一致」从「判不了」变成成立**（读数源 = 下拉框选中项的 Name，如 `150 ％ (推荐)`，本仓 M27 真机已读到过 `150 ％ 〔 推 荐 ）`）。
* **怎么验证**：`python -m minicode.uia.live --target 缩放 --out runs/v2-rootwalk`；看可用控件数与 W-1.1 的真值来源；`e2e/real.py:627-649` 的 `scale_source` 应变成「下拉框选中项」而不是「注册表」。**需要真机跑、锁直接拿**。

### T8 · 真机：浏览器通道滚动 + 持久化登录态 + 本地验证页三件套
* **改哪个文件**：`src/minicode/browser/cdp.py`（`ChromiumPage` 新增 `scroll_lines(element_id, lines)`，约 20 行、与 `invoke` 同构）、`src/minicode/e2e/real.py:939-952`（把「浏览器通道没有滚动动作（M13 未实现）」那段显式失败换成真实现，并写 `scroll_lines` / `scroll_stable` 两个上下文键）、`src/minicode/e2e/real.py:837-844`（`launch()` + `new_context()` → `launch_persistent_context(user_data_dir=…, channel="chrome", no_viewport=True)`；`self._browser` 置 `None`，`close()` 已有空值保护）、`src/minicode/e2e/apps.py:2555`（`VerificationPage._html()` 加**可滚列表（>1 屏高）+ 搜索框 + 城市筛选下拉**，纯 HTML，不碰登录态）。
* **做什么**：滚动用 CDP `Input.dispatchMouseEvent` `type:"mouseWheel"`；**先 `mouseMoved` 到目标**（事件落在光标处）；**delta 单位是 CSS 像素**（不是 Windows 的 120/格）→ 「1 行 = 多少 CSS px」**必须运行时量**（`getComputedStyle(el).lineHeight` 或相邻列表项的 y 差），**不许硬编码**；滚动后读 `window.scrollY` 校验 `0 < ΔscrollY < 视口高`，Δ=0 则退 `Runtime.evaluate("el.scrollBy(0,dy)")`（Playwright 文档明说 wheel 事件可能被 handler 吃掉）。
  ⚠️ **三条通道的单位必须各自声明**（桌面通道 `SCROLL_DELTA_PER_LINE = 50` 是像素语义，浏览器通道是 CSS 像素）→ `ActionKind.SCROLL` 只有 `lines` 一个参数（`src/minicode/action/protocol.py:75`），要在上下文里写清换算系数。
* **依据**：CDP mouseWheel（delta = CSS 像素、相对主框架 viewport）【二手·公开 https://chromedevtools.github.io/devtools-protocol/tot/Input/#method-dispatchMouseEvent】；
  Playwright `mouse.wheel` 原文（不等待滚动结束）【一手·公开 https://playwright.dev/docs/api/class-mouse#mouse-wheel】；
  `locator.scrollIntoViewIfNeeded()` 是**页内元素级滚动**、不能替代 wheel【二手·公开 https://playwright.dev/docs/api/class-locator#locator-scroll-into-view-if-needed】；
  `launch_persistent_context`【二手·公开 https://playwright.dev/docs/api/class-browsertype#browser-type-launch-persistent-context】；
  Windows `MOUSEINPUT` 的 `WHEEL_DELTA = 120`（桌面通道口径对照）【一手·公开 https://learn.microsoft.com/en-us/windows/win32/api/winuser/ns-winuser-mouseinput】。
* **预期哪个数变多少**：**C-1 / C-2 / C-3 从「SKIP（环境不可达：未登录）」→ 可跑**；wave1 可判票 **2 → 5**；BOSS 直聘这类真实站点在拿到登录态后不再永远 SKIP。
* **怎么验证**：`python -m minicode.e2e --tasks C-1,C-2,C-3 --out runs/v2-browser`；`preflight()` 不再返回「未登录」；上下文出现 `scroll_lines=3`、`scroll_stable=True`、`city_filter_applied=True`。**需要真机跑、锁直接拿**。

### T9 · 真机：OCR 词框当控件框真值（消灭 undecidable）
* **改哪个文件**：`src/minicode/e2e/real.py:507`（`_resolve_truth()`）、`src/minicode/verification/ocr_windows.py:203-217`（`WindowsOcrPort.read_words`，**词级框 = 喂进去那张 PNG 的像素 = 物理像素**，加帧原点即物理表面坐标）。
* **做什么**：树里找不到 `truth_element` 的控件框时 → 取当前帧 → `read_words(frame)` → 在词框里找**文本等于/包含 `truth_element`** 的那个词 → 用它当控件矩形真值，`source` 如实记「**OCR 量的（画面里那个词的框）**」→ 未命中仍记 `undecidable`（**不编真值**）。
* **依据**：本仓已有 OCR 词级框与物理像素口径【一手·本机 `src/minicode/verification/ocr_windows.py:203-217`】；本仓已确立「画面量的真值」口径（W-3 的 `CLOCK_RECT` 注释：**从画面量出来的**）【一手·本机 `src/minicode/e2e/wave1.py:60`】。
* **预期哪个数变多少**：**视觉步骤可判率 wave1 0/2 → 100%**（wave2 保持 1/1）；W-1.C / W-3.C 不再 undecidable；e2e 的 `landing_error_px` 不再是「缺真值」。
* **怎么验证**：`python -m minicode.e2e --tasks W-1,W-3 --out runs/v2-ocr`；条款详情里「判不了」计数下降。**需要真机跑、锁直接拿**。

### T10 · 真机：人工代确认票改 agent 代确认 + 重跑 wave1/wave2
* **改哪个文件**：`src/minicode/e2e/wave1.py:132-164`（W-2）、`src/minicode/e2e/wave2.py:125-198`（WX-2）、`:170-198`（WX-3）、`:347-442`（CC-1）的 `human_confirm` 步骤；`src/minicode/e2e/real.py`（代确认执行器 + 真值来源）。
* **做什么**：把「人工代确认」改成 **agent 代确认**：agent 自己执行 + 自己读回验收（例如 W-2 改缩放到 125% 后**自己重新抓图读回**；WX-2 发消息后**自己读回自己发的消息**）；WX-3 的模态弹窗按 WX-3.A 的口径**零动作下发 + 交接产物齐**（合规停手即通过）。
  **「人工代确认」在本轮不写成阻塞项，也不计入失败。**
* **依据**：用户授权（真机完全授权、占用锁直接拿、不等人工确认）；WAA 把「交人」做成显式条款并要求交接产物齐【一手·公开 https://raw.githubusercontent.com/microsoft/WindowsAgentArena/main/README.md】。
* **预期哪个数变多少**：**wave1 0/5 → ≥3/5**、**wave2 3/7 → ≥5/7**（主口径）；全票口径 wave1 **≥4/6**、wave2 **≥6/9**。
* **怎么验证**：`python -m minicode.e2e --tasks ... --out runs/v2-wave1` 与 `--out runs/v2-wave2`；读 `e2e-report.json` 的任务级通过数与 `landing.visual_step_decidable_rate`。**需要真机跑、锁直接拿**。

---

## 六、评测设计

### 6.1 配对怎么做（三层，配对程度递减）

| 层 | 配对对象 | 做法 | 能回答什么 |
|---|---|---|---|
| **P1 同一跑批内** | 两臂**共用同一次上游粗定位回答** | `--memo-cache` 复用缓存（`src/minicode/eval/__main__.py:177-185` 已实现）→ 臂间差异**不混模型随机性** | 「护栏 / 放大 / 聚类」这类**纯落点算法**改动（上一轮已验证：`comparison.coarse_shared.identical = 183/183`，mid-p McNemar p = 9.02e-05） |
| **P2 同批跨跑批** | 同 183 条、**按 `sample_id` 对齐** | §3 的 2×2 表 + 精确二项；**必须同时跑 `coarse_only` 噪声对照** | 「档位」这类**改了上游输入**的改动（配对消掉样本难度噪声，**消不掉模型重抽噪声** → 必须配合 T1 的 K=8） |
| **P3 跨跑批绝对值** | — | **只报数、不下结论**（上一轮的 44.81% → 55.74% 就是这一层，模型各自重抽） | 头条趋势；所有这类比较都要标「混着随机性」 |

**统计口径**（全部用仓内已有实现，`src/minicode/eval/stats.py`，六个纯函数）：
* 配对主检验：**McNemar mid-p**（`mcnemar_midp`）与**精确二项**（`mcnemar_exact`）；χ²+连续性校正（`mcnemar_chisq_cc`）作为对照。
* 单臂区间：**Wilson 95% CI**（`wilson_ci`）；Δ 的区间：**配对 bootstrap**（`paired_bootstrap_delta`）。
* 样本量够不够判：**`mde(n, π_d)`**（`eval/stats.py::mde`）→ 结论必须满足 `|Δ| ≥ MDE`。
* 分层：`icon` / `text` × `platform` × `group`；**分层结果必须单列**，不许只报总体。
* 重加权：按 ScreenSpot-Pro **全集 group 构成**重加权（本轮 55.74% → **54.27%**，bootstrap 95% CI [44.57%, 63.45%]，n=183，2000 次，种子 20251020；同一方法复现旧基线 42.47% ≈ 文档里的 42.5% 以验证方法可信）【一手·本机 `runs/round3-recheck/headline-reweight.json`】。
* 近失带校准：`near_miss_calibration`（纯算术，零模型调用）。

### 6.2 样本量：**这轮不补到全量 1581**（理由与后续）

* 本机只有 **939/1581** 张图（`.scratch/sspro` 实测计数；**缺 642 张**）。
* **不补的理由**：① 这轮的决策点是「**哪个改动有效**」，不是「我们对外报多少分」→ 判定力由**不一致对数**决定（本轮 always 层 48 对已给出 p = 0.0293），不由样本总量决定；
  ② 183 条的分层构成（OS 76/183 = 41.5%，全集 12.4%）需要**重加权**才能对外报，重加权已经能处理构成偏差；
  ③ 补图是**独立的下载 + bbox 一致性核对**工作，与本轮施工清单正交，**不阻塞** T1~T10。
* **但所有对外报的绝对分必须标口径**：「**183 条分层抽样（种子 20251020），非全量 1581**」。
* **补图怎么做（下一轮的独立项）**：从官方 ScreenSpot-Pro 仓库下载缺失 **642** 张 → 先核对官方 bbox 与本地 939 张的一致性（抽检 IoU 与坐标口径）→ 扩到 `.scratch/sspro` → 跑 `--full`（CI 从 ±7 pp 收到 **±2.4 pp**）。公开数据侧佐证：全集 1581 条、icon 604 / text 977、文本占 62.6% / 图标占 37.4%、目标平均只占屏 **0.07%**【一手·公开 https://arxiv.org/html/2504.07981v1】。
* **必须补的另一个东西（不是补图，是补样本类型）**：**「目标不存在」的负样本子集** —— 从同图取**错位 crop** 当「目标不在窗内」的负样本，产出 `runs/s2-refusal-eval/`。
  没有它，「假命中归零」「拒答 precision」在离线口径里**不可见**（ScreenSpot-Pro 每条都保证目标存在）【一手·公开口径，GUI-HalluBench 的做法：https://openaccess.thecvf.com/content/CVPR2026/html/Zhang_Exposing_and_Evaluating_Hallucinations_for_GUI_Grounding_CVPR_2026_paper.html】。

### 6.3 统计口径的纪律（防过拟合）

1. **超参上限 2 个自由维度**：本轮的潜在超参有放大倍数、crop 比例、聚类 τ、α 方块、view 数、K —— **一轮只放 2 个进决策**（建议：放大倍数 + crop 比例），其余固定为公开默认值（τ=32 物理 px、α 取 GUI-RC 的 50 折算到本档 ≈39、K=8）。
2. **调参集选、确认集评**：`--confirmation-split` 已存在（`src/minicode/eval/__main__.py:197-198`）→ **所有在 183 条上调出来的选择，必须在确认集复核**。
3. **报告必须同时给三档**：单臂 Wilson CI、配对 Δ 的 CI、MDE 判定。
4. **抽样构成要重加权后再对外报**（见 6.1）。
5. **不许把「归零」类指标单独报**：假命中 0 条必须与「这 20 条的最终命中数」一起报（见 §4 第 3 条）。

---

## 七、明确不做（这轮不碰什么、为什么）

| 不做 | 为什么 |
|---|---|
| **换眼睛模型**（UI-TARS / Jedi / UGround / OS-Atlas / Qwen2.5-VL 等专用 grounding） | 专用 grounding 模型的公开天花板是 **61.6**（UI-TARS-1.5 官方全量），**低于我们已实测的 64.48**（Jedi-7B 39.5 / GUI-Actor-7B+Verifier 47.7 / UGround-V1-7B 31.1 / OS-Atlas-7B 18.9 / Qwen2.5-VL-72B 53.3）→ 单点看是退步 3~46 个点【一手/二手·公开 https://huggingface.co/bytedance-research/UI-TARS-1.5-7B ｜ https://openreview.net/pdf?id=5fSkinHw7w ｜ https://github.com/OSU-NLP-Group/UGround ｜ https://arxiv.org/abs/2504.07981】。协议层公开天花板是 69.7~73.5（Gemini-3-Pro+Zoom 69.7 / Seed1.8+Zoom 73.3 / MAI-UI-32B+Zoom 73.5）→ **先把协议做完**。若要做换眼，只做**并行 A/B 臂**（协议冻结、只换模型名），**不作为替换** |
| **「离粗定位多远 → 回落」的标量距离阈值** | 本机实测净负：位移阈值净 **−6 条（−3.28 pp）**；几何硬门 R1/R2/R3 净 **−14.21 / −7.10 / −3.83 pp**【一手·本机】；公开同向：LFPR 无 guard 对照 Acc@0.9 **−5.748**【一手·公开】 |
| **「采样离散度 → 拒答」阈值** | SafeGround 自陈失败模式「模型一致地错时低方差的错答案看起来很自信」→ **我们的 20 条假命中正是「一致地错」**【二手·公开，同 `r-s2-false-hit.md` §2.4 引用】 |
| **任何依赖 logprobs / token 概率 / 模型自报置信度的判据** | 本机两个协议都拿不到（`/v1/messages` 无字段；`/v1/chat/completions` → `logprobs: null`）【一手·本机】；公开佐证：Anthropic Messages API 从未支持 logprobs【二手·公开 https://github.com/dgfug/spiceai/pull/3 ｜ https://stackoverflow.com/q/79299548】；FORUM §5.6 与 LFPR 机制对照都**反对置信度当判据**【一手·公开】 |
| **跨模型投票当主路线 / 对坐标取算术平均或质心** | FSR：投票只买到 1.0~1.3 pp，**选择**买到 4.34 pp【一手·公开 https://arxiv.org/abs/2610.25503】；zoom-consistency-routing 的「Vote agree −3.2 / Midpoint fusion −4.4 / Step-1 vote centroid −7.6 pp」【二手·公开 https://github.com/omxyz/zoom-consistency-routing/blob/main/paper.md】；双峰均值会落在两图标之间的空白上【一手·本机推导】。**我们只有一个模型**，且「不同上游」的多样性来源**未验证**（同提示词 10 次原文一致率 10%） |
| **送 2048 / 2560 整屏** | 服务端宽度上限 **≈2016 px** 实测（2048 档差 −162、2044 档差 −45）→ 坐标系不可信【一手·本机】；公开曲线 2K 最好、4K 下降【二手·公开】 |
| **「一次请求送整屏 + 若干 crop」当主路径** | 多图**不改变 crop 内的目标像素**（crop 仍是 1:1，20 物理 px）【一手·本机推导 + 公开同向 https://arxiv.org/abs/2512.08529】；且本仓 M14 实测该模型在换帧后容易混坐标口径（整屏口径 vs 图内口径差 **14.0 px**）【一手·本机 `src/minicode/ground/zoom.py:10-13`】→ 只能当单独实验臂 |
| **Set-of-Mark / OmniParser 当主链路的 grounding** | 2026 横评：密集 GUI 上 SoM **所有模型 < 30**，E2E 像素坐标明显更优【二手·公开 https://tongyi-mai.github.io/MobileWorld/blog_post.html?post=Why%20your%20AI%20Agent%20keeps%20misclicking】。本轮只做 L3 的**离线覆盖率测量**，不接生产 |
| **编号分支白进收紧（覆盖率门槛）** | 入口条件只有 `if candidates:`（`channel.py:497`），收紧会改变调用分布，属**行为改动**；且它服务的是真机编号分支，**不进 SS-Pro 三臂** → 这轮不碰，留到能判「收紧后 icon 不降」的那一轮 |
| **改 `src/minicode/coord` / `src/minicode/exec` / `src/minicode/verify`** | AGENTS.md:68「三个原型已验证、冻结不改」→ T3 的放大一律做在 `ground/` + `model/`；T7 的 PID root-walk 一律做在 `uia/windows.py`（冻结原型的公开接口已够用） |
| **动工单的 AC / 改验收标准** | AGENTS.md:23-25：改验收标准的动作只在 agent 停下之后由人做。本方案里的「新验收线」是**本轮的收口目标**，工单 AC 的同步由人决定 |
| **把「人工代确认」当成阻塞项** | 用户授权：真机完全授权、锁直接拿、不等人工确认 → 改成 agent 代确认并计入分母（T10） |
| **省 token / 省调用 / 降延迟 / 降成本** | 用户明确：这些**一律不是约束**，不许当取舍理由。本清单里没有一条以它们为理由；K=8 采样、放大、多候选、再搜**都是加法** |

---

## 八、风险与未解

### 8.1 只是推断、还没验的（按可能影响的大小排）

1. **「crop 放大能把 icon 推到 ≥48%」是推断**。公开的同型证据（Gemini 39.0→69.7、Claude 35.0→54.0）来自**基线低得多**的模型，我们基线已经 64.48 → 边际增益会小得多。**T3 必须实测**，不许拿公开数字当我们的预期报出去。
2. **2016 档的 +8.7 pp 里混着模型重抽噪声**：两次跑批的粗定位点只有 **2/183** 相同、`coarse_only` 命中一致率 **134/183**【一手·本机】。配对 McNemar 的 p = 0.0293 检验的是「样本难度」层面的偶然性，**不是**「同档重跑」的偶然性。**T1 的 K=8 是唯一的解法**（判据：2016 粗定位命中率 CI 下界 > 1536 上界）。
3. **RegionFocus 的 VLM judge 在我们的模型上准不准，没有公开数字可搬**（原文只说 "judgments aren't always perfectly accurate"）→ T4 的 `refine_chosen_candidate` 埋点就是测它；**测出来之前不能把「标记图选择」当成可靠机制**。
4. **「再搜能救回多少假命中」是本轮最大的未知数**（估 15~25%）【推断】→ T4 必须单独在假命中样本上跑一次再搜，报告 `rescreen_hit_rate`。
5. **MVP 的聚类阈值 τ 与视图数 m 的最优值**论文没给（只给算法）→ 先落盘 `refine_cluster_ratio` 的分布，再定 τ；**一轮只调 2 个超参**。
6. **真机 icon 的实际像素尺寸**：150% DPI 下同一名义图标在物理截图里应约 **30 物理 px**（ScreenSpot-Pro 关了缩放）【推断，`r-s4-resolution.md` §4】→ 若成立，**真机上的 icon 难度低于 SS-Pro 的 icon 难度**（SS-Pro 的 icon 数是悲观代理）。可执行核对：对同一个图标比较 UIA 给的 rect 尺寸与它在物理截图里的可见像素尺寸（比值 ≈1.5 即证实）。
7. **2016 档在真机上的表现未知**：离线是 SS-Pro 截图，真机是 2560×1600 @150%，**整屏 2016 宽在真机上只覆盖 78.75% 的物理宽度** → 真机整屏的 icon 表观像素会比 SS-Pro 更小（SS-Pro 截图原生更小）→ **真机上 2016 的收益可能小于离线**。

### 8.2 改动之间互相冲突的地方

1. **T2（档位）与 T5（护栏）**：护栏要用「粗框 vs 精点」的几何关系；换档位后粗框的物理尺寸换算了 → **T5 的离线重算必须在 T2 落地后的那一档产物上跑**（`runs/g3-eval-2016/sspro-report.json` 已经是 2016 档，可直接用）。
2. **T3（放大）与 T5（中点融合）**：放大后 crop 内坐标与物理坐标的比例变了 → **中点融合必须在物理空间做**（把精点换算回物理再取中点），否则会取到错误的点。这条必须在 T3 的实现里显式写死。
3. **T3（放大）与 T4（聚类）**：聚类的 τ=32 物理 px 是**物理尺度**；放大后 crop 内的像素尺度变了 → τ 必须定义在**物理空间**（或统一换算），否则同一份代码在 1536/2016/放大三档下行为不同。
4. **T3（放大）与「crop 含住率」口径**：含住率是**物理窗口**的口径，放大不改变窗口 → 含住率不变；但**假命中**会随「crop 里看起来更清楚」而**上升**（模型更愿意在错窗口里给点）→ **T3 与 T4 必须一起上**（单独上 T3 可能让假命中变多）。**顺序上 T3 单独跑一次并单独报假命中数**，若假命中上升则必须同时上 T4。
5. **T4（再搜）与 `whole_screen` 通道**：上一轮 wave1 的 W-1.2 就是**整屏第二遍遇负出口崩在 `box_center`** → 再搜分支必须**复用已修好的 `whole_screen` 通道**并处理负出口（答「没有」→ 回落粗定位，不是崩）。
6. **T6（准入门 K=21）与真机跑批时长**：K=5→21 意味着每次标定的请求数 ×4.2 → 真机跑批变长；**这不是取舍理由**（用户给定），但要把「每次 e2e 跑批前先标定」的顺序写进执行清单，避免标定没收敛就跑 e2e。
7. **T8（持久化 profile）与「用户日常 Chrome」**：默认 profile 被正在运行的 Chrome 锁住 → 要么先关 Chrome，要么复制一份 profile 到临时目录再指过去【二手·公开 Playwright 文档】。**这是执行细节，不是阻塞项**。
8. **T7（root-walk）与遍历性能**：桌面根的 `find_all(TrueCondition, TreeScope_Descendants)` 在真机上**很慢**（桌面树巨大）→ 必须用 `windows.py:237` 的 `_with_deadline`（5 s 超时）包住，并**先按 PID 过滤再进 Python 侧名字匹配**；若超时不可接受，退路是「先取桌面根的直接子节点再逐个下钻」。

### 8.3 可能过拟合的数字（标出来，别当结论报）

* **183 条上的任何 ≥3 条的差**（≈1.6 pp）：接近 Wilson CI 半宽（±7 pp）的一个零头 → **单个改动只动 3~5 条时，不下结论**，要求方向一致 + 配对 p<0.05 + 确认集复核。
* **icon 子集 n=87**：**任何 icon 的结论都要同时给 n**；icon 的 36→42 是 +6 条，处在「够不够大」的边缘。
* **crop 比例 {0.5, 0.35, 0.25}**：论文级消融量级是「最优/最差差 11.4 分」，而我们只有 183 条 → **比例必须在确认集复核**。
* **近失带 36 px**：产物显示 36 px **不在空白区**（misses 81 条里 18 条 = 22.2% 落在带内；「找对」子集 `dist_to_bbox` p50 = 0、「挑错」p50 = 519.2 px）【一手·本机 `runs/g2-eval-final/sspro-report.json → near_miss_calibration`】→ **这轮不改带宽**（它是「只记档不判死」的折中带），但收口报告必须**继续披露**这条 22.2%。
* **重加权值 54.27%**：这是**口径决策**（按 AGENTS.md 属「标定/坐标契约」类，须人拍板），上一轮已列待拍板 → 本轮沿用同一方法与同一种子，别换口径。

---

## 九、引用纪律

### 9.1 允许引用的两类（且只有这两类）

**① 本机实测** —— 必须带**样本量 + 判定口径 + 产物路径**：

| 数 | 样本量 / 口径 | 产物路径 |
|---|---|---|
| always 102/183 = 55.74%（1536×960） | 183 条分层抽样（种子 20251020），官方 bbox 命中 | `runs/g2-eval-final/sspro-report.json` |
| always 118/183 = 64.48%（2016×1260） | 同上 | `runs/g3-eval-2016/sspro-report.json` |
| coarse_only 75/183 = 40.98% | 同上 | 同 g2 |
| icon 36/87 = 41.38% / 38/87 = 43.68%；text 66/96 = 68.75% / 80/96 = 83.33% | 分层子集 | 同上两份 |
| 假命中 20 / 19 条；其中 19/20、18/19 的粗定位也错 | 183 条，`crop_contains_target==False 且 landing_source=="refined"` | 同上两份 |
| crop 含住率 140/183 = 76.5% / 152/183 = 83.1% | 183 条 | 同上两份 |
| 精定位改坏 11 / 6 条，救回 38 / 48 条 | 183 条 | 同上两份 |
| 硬失败回落 25/176 = 14.2% / 11/176 = 6.25%（全部 `no_output`） | 176 次开窗 | 同上两份 |
| icon 表观像素中位 12.0 / 15.75 px（<16 px 占 73.6% / 51.7%）；text 中位 66.4 / 87.15 px | icon n=87、text n=96（评测台口径） | 同上两份 `model_input_target_sizes` |
| **配对 1536 vs 2016**：always 16/32（exact p=0.0293）、icon 12/14（p=0.845）、text 4/18（p=0.0043）、coarse_only 24/25（p=1.0） | 同批 183 条按 `sample_id` 对齐，McNemar 精确二项 | 同上两份（**本轮离线重算，零模型调用**） |
| 粗定位点跨跑批相同 **2/183**；coarse_only 命中一致 **134/183** | 同上 | 同上两份（本轮离线重算） |
| 服务端宽度上限 ≈2016 px（2048 档期望 3404 / 实测 3242；2044 档 3285 / 3242；2016 档 3240 / 3242） | token 探针 | `runs/m6-token-probe-2048exact/`、`runs/m6-token-probe-final*/`、`runs/m6-token-probe-2016/token-probe.json` |
| 网关忽略 temperature：3 轮 × 10 次连发原文一致率三轮都 10% | 40 次请求 | `runs/m2-temperature-probe/probe*.json` |
| 标定 7 次会话图像路径 6 次可用；等价标度族落点分歧 2.87 px | 7 次会话 | `runs/v1-calib/s0..s5/`、`runs/v1-calib/s4b/`、`runs/v1-calib/session-table.md` |
| wave1 0/5、wave2 3/7；落点三档 A 2 / 判不了 2（wave1）、A 1（wave2）；`visual_step_decidable_rate = 0.0` | 5 / 7 张票 | `runs/v1-wave1c/e2e-report.json`、`runs/v1-wave2/e2e-report.json` |
| 准入门四连抽签 1.06 / 2.66 / 68.00 / 68.81 px | 4 次标定 | `docs/reviews/M15.md` §4⑧ |
| 36 px 近失带：misses 81 条里 18 条（22.2%）落在带内 | 81 条 miss | `runs/g2-eval-final/sspro-report.json → near_miss_calibration` |
| 重加权 55.74% → 54.27%（bootstrap 95% CI [44.57%, 63.45%]，n=183，2000 次，种子 20251020） | 183 条 | `runs/round3-recheck/headline-reweight.json` |
| 本机图像 **939/1581** 张 | 文件计数 | `.scratch/sspro` |
| 网关无 logprobs（两个协议） | 各 1 次探测 | `r-s2-false-hit.md` §1.4 引用的实测原文 |

**② 公开一手** —— 必须带 URL，并标明【一手·公开】/【二手·公开】：

* 护栏/仲裁：https://arxiv.org/abs/2608.19553（LFPR）｜ https://arxiv.org/abs/2508.05615 与 https://github.com/ZJU-REAL/GUI-RCPO（GUI-RC）｜
  https://arxiv.org/abs/2610.13268 与 https://github.com/zhangruotian/ConClick（ReClick/ConClick）｜ https://arxiv.org/abs/2609.37488 与 https://github.com/Neurogica/FORUM（FORUM）｜
  https://arxiv.org/abs/2610.25503 与 https://github.com/shijunzhe/vlm-set-reporting（FSR）
* 假命中/多候选：https://arxiv.org/abs/2505.00684 与 https://github.com/tiangeluo/RegionFocus（RegionFocus）｜ https://arxiv.org/abs/2512.08529 与 https://github.com/ZJUSCL/MVP（MVP）｜
  https://arxiv.org/abs/2604.14113 与 https://github.com/ZJU-REAL/UI-Zoomer（UI-Zoomer）｜ https://openaccess.thecvf.com/content/CVPR2026/html/Zhang_Exposing_and_Evaluating_Hallucinations_for_GUI_Grounding_CVPR_2026_paper.html（GUI-HalluBench）｜
  https://github.com/modelscope/evalscope（官方 ScreenSpot-Pro 通用 API 适配器：出框 → 取框中心、`Answer: [x,y]`、截断=0 分）
* icon/候选源：https://arxiv.org/html/2504.07981v1（ScreenSpot-Pro 全文：1581 条、icon 604/text 977、目标占屏 0.07%、ReGround 40.2%、ScreenSeekeR 48.1%）｜
  https://github.com/microsoft/OmniParser（v2 = MIT 的 YOLOv9-E `icon_detect_v3`）｜ https://raw.githubusercontent.com/microsoft/UFO/main/config/ufo/system.yaml（UFO²：`TOP_K_ICON=15`、`clip-ViT-B-32`、`BOX_THRESHOLD=0.05`、`IMGSZ=640`）｜
  https://github.com/IDEA-Research/GroundingDINO（Apache-2.0 备选）
* 分辨率/端点：https://platform.minimax.io/docs/api-reference/text-anthropic-api（MiniMax：`detail` 三档、`count_tokens`、**temperature 被忽略**）｜
  https://platform.claude.com/docs/en/build-with-claude/vision-coordinates（28 px 网格）｜ https://openreview.net/pdf?id=yBFUqdJFZn（2K 最好、4K 下降）｜
  https://blog.csdn.net/weixin_43427721/article/details/159436148 与 https://github.com/Tongyi-MAI/MobileWorld（Zoom-In 配方，**二手**）｜ https://tongyi-mai.github.io/MobileWorld/blog_post.html?post=Why%20your%20AI%20Agent%20keeps%20misclicking（SoM 全线 <30，**二手**）
* 真机 e2e：https://github.com/trycua/cua/pull/1606/files（XAML root-walk）｜ https://raw.githubusercontent.com/microsoft/WindowsAgentArena/main/README.md（`--som-origin` / `--a11y-backend` 矩阵）｜
  https://playwright.dev/docs/api/class-mouse#mouse-wheel ｜ https://chromedevtools.github.io/devtools-protocol/tot/Input/#method-dispatchMouseEvent ｜
  https://playwright.dev/docs/api/class-browsertype#browser-type-launch-persistent-context ｜ https://learn.microsoft.com/en-us/windows/win32/api/winuser/ns-winuser-mouseinput（`WHEEL_DELTA=120`）｜
  https://learn.microsoft.com/en-us/windows/apps/dev-tools/winapp-cli/ui-automation（取值兜底链）｜ https://www.w3.org/TR/WCAG22/#target-size-minimum（36 px 近失带依据）
* 空间/换眼：https://huggingface.co/bytedance-research/UI-TARS-1.5-7B（61.6 全量）｜ https://openreview.net/pdf?id=5fSkinHw7w（GUI-Actor/Jedi）｜ https://github.com/OSU-NLP-Group/UGround（31.1）｜ https://arxiv.org/abs/2409.08264（WAA）｜ https://github.com/dgfug/spiceai/pull/3 ｜ https://stackoverflow.com/q/79299548（logprobs 不可得）

### 9.2 禁引（下一轮同样不得出现）

* 任何**没有产物路径**的「实测数」；
* 任何**没有 URL** 的外部数字；
* 任何**二手转述却不标【二手】**的结论；
* 上一轮已列的 **8 类禁引数**（收口报告 §九 自检：零违规）；
* **任何以「省 token / 省调用 / 降延迟 / 降成本」为理由的建议**；
* **跨跑批的绝对值差**（除非同时标注「混着模型随机性」并给出配对检验）。

### 9.3 本方案自己的数字（供下一轮复核）

* §3 的四个 McNemar 表（1536 vs 2016 的 always / icon / text / coarse_only）与「粗定位点 2/183 相同、coarse_only 一致 134/183」，
  **全部由 `runs/g2-eval-final/sspro-report.json` 与 `runs/g3-eval-2016/sspro-report.json` 离线重算得出，零模型调用**；
  复核方式：按 `sample_id` 对齐两份报告的 `outcomes.always` / `outcomes.coarse_only`，建 2×2 表，跑精确二项。
* 验收线里的「现在」列全部来自上表 §9.1 的产物路径，**不引用任何未列出的数**。

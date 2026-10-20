# DOM/可访问性树路线 vs 纯像素路线：精度与覆盖率量化对比

> 查证日期 2026-10-19。所有数字标注「✓已核原文」/「△二手读取」/「✗未查到」。

## 1. 经典基准：DOM 的定位精度约为像素的 2 倍

**结论**：DOM 路线在**元素定位**上稳定胜出（Mind2Web 离线 Step SR 约 2×，VisualWebArena 纯 DOM 文本 vs 截图+SoM 约 2.3×）；但优势不迁移到整任务成功率。OSWorld 桌面场景是明确反例——纯截图远差于 a11y tree。

| 基准 | 观测方式 | 指标 | 数字 | 出处 |
|---|---|---|---|---|
| Mind2Web Cross-Task | MindAct FLAN-T5-XL（DOM 候选多选） | Ele.Acc / Op.F1 / Step SR | 57.1 / 75.7 / 53.5 | ✓ [SeeAct](https://arxiv.org/abs/2401.01614) Table 2 |
| Mind2Web Cross-Task | SeeAct GPT-4V（Choices = DOM 候选） | Ele.Acc / Step SR | 46.4 / 40.2 | ✓ 同上 |
| Mind2Web Cross-Website | MindAct / SeeAct-GPT4V | Step SR | 41.1 / 32.4 | ✓ 同上 |
| Mind2Web 离线 grounding 消融 Step SR（CT/CW/CD） | DOM 属性 / 截图标注 / **DOM 文本** / 人工 oracle | Step SR | 16.1·12.1·19.0 / 20.3·13.9·23.7 / **39.1·32.7·42.0** / 61.9·65.0·62.1 | ✓ 同上 Table 3（[图](https://osu-nlp-group.github.io/SeeAct/)） |
| 真实网站在线整任务 SR | FLAN-T5 / GPT-4 / SeeAct-Choice / SeeAct-Oracle | SR | 8.9 / 13.3 / 37.8 / 51.1 | ✓ 同上 Table 4 |
| WebArena 812 任务 | GPT-4 + a11y tree + element ID | SR | 14.41%（含 UA 提示）/ ~14.9%（无提示）；人类 78.24% | ✓ [WebArena](https://arxiv.org/abs/2307.13854) §5.1 |
| VisualWebArena 910 任务 | 仅 a11y tree（GPT-4，文本） | SR | **7.25%** | ✓ [VisualWebArena](https://arxiv.org/abs/2401.13649) Table 3 |
| 同上 | a11y tree + BLIP-2 caption（GPT-4） | SR | 12.75% | ✓ 同上 |
| 同上 | 截图 + captions + a11y tree（GPT-4V） | SR | 15.05% | ✓ 同上 |
| 同上 | 截图 + SoM 框标注（GPT-4V） | SR | **16.37%**；人类 88.70% | ✓ 同上 |
| OSWorld | 纯截图 GPT-4V / 纯 a11y tree GPT-4 / 截图+a11y / SoM | SR | **5.26** / **12.24** / 12.17 / 11.77 | ✓ [OSWorld](https://papers.neurips.cc/paper_files/paper/2024/file/5d413e48f84dc61244b6be550f1cd8f5-Paper-Datasets_and_Benchmarks_Track.pdf) Table 5 |
| MiniWoB++ | CC-Net 无 DOM / 有 DOM / Pix2Act 纯像素 | score | 24.1 / 96.3 / 96.2（人类 94.4） | △ [Pix2Act](https://arxiv.org/abs/2306.00245)（摘要已核"纯像素可超人类"；分数为二手读取表格） |
| WebVoyager 真实站 15 站 | 截图 GPT-4V / text-only / GPT-4 | SR | 59.1 / 40.1 / 30.8 | ✓ [WebVoyager](https://aclanthology.org/2024.acl-long.371/) Table 1 |
| WebArena | AgentOccam（紧凑 DOM 观测） | SR | 43.1%（SteP 33.3%，WebPilot 37.2%） | ✓ [AgentOccam](https://arxiv.org/abs/2410.13825) |

- **明确的"DOM 优于像素"原文结论**：SeeAct Table 3 —— DOM 文本变体 Step SR 是截图变体的 1.9–2.4 倍。
- **明确反向结论**：OSWorld Table 5 —— SoM 11.77% < 截图+a11y 12.17%，论文归因于高分辨率下元素过多、坐标级动作无法用 SoM 表达；且纯截图 5.26% 是全场最差配置。
- **注意归因**：VisualWebArena 中 12.24% 属**纯文本 GPT-4 + a11y tree**，非 GPT-4V（部分二手榜单误标）。

## 2. 覆盖率：DOM 看不见什么

**结论**：DOM/a11y tree 的盲区是"渲染出来但不在树里"的内容——canvas/WebGL 自绘控件、图表、地图、视频、广告、图片内文字。Apple 的 4,068 app 人工标注是当前最硬的统计。

- ✓ [Apple Screen Recognition](https://arxiv.org/abs/2101.04893)：77,637 屏幕 / 4,068 iPhone app，**59% 屏幕至少一个可见元素在 a11y tree 中无匹配；4% 屏幕树全空；94% app 至少一个此类屏**。Apple 排除游戏且匹配宽松 → 保守估计。（原文未逐字核，数字经 [Semaloop](https://semaloop.com/blog/why-we-dont-trust-the-accessibility-tree) 引述）
- ✓ [arXiv 2607.04334](https://arxiv.org/abs/2607.04334)（2026-07）引 CLAY：**10.6% 结构节点无有效视觉表示，ghost node 出现在 37.4% 屏幕上**；并从 **3,838 个线上生产网站**零编辑挖出 **225 个"结构值 ≠ 渲染值"**的元素（滚动条/时钟/计数器）。
- ✓ [TDCommons 2025](https://www.tdcommons.org/cgi/viewcontent.cgi?article=9196&context=dpubs_series)：WebGL 只产生位图，无语义数据与可继承交互能力。
- ✓ Playwright MCP 官方文档自认盲区："For pages where visual context matters (**canvas apps, charts, image-heavy layouts**), combine snapshots with screenshots"（[snapshots](https://playwright.dev/mcp/snapshots)）。
- △ 文字变图片：[arXiv 2510.18113](https://arxiv.org/abs/2510.18113) 报告把按钮文字换成图片后 TSR 下降并出现"交互瘫痪"（未逐字核原文）。
- **"WebArena visual obfuscation" → ✗ 未查到以此为名的 WebArena 子基准。** 最接近的三条替代证据：
  1. ✓ [DMAST, arXiv 2603.04364](https://arxiv.org/abs/2603.04364)（Google/UC Berkeley，2026-03）：DOM 注入同时污染截图与 a11y tree（跨模态一致）。MiniWob++ 125 任务 × 8 episode：攻击者成功率 **文本 24.1% / 纯图像 34.4% / 双模 35.7%**；VisualWebArena 上 base model **TSR 6.2%、ASR 41.2%**，防御后 10.2% / 21.4%。
  2. △ TVSC（ACM DL 10.1145/3804601.3804623，**未开原文**）：VisualWebArena 上 GPT-5 clean 18.5% → 视觉扰动 12.1% → **结构扰动 9.4%** → 混合 6.8%（结构扰动伤害 > 视觉扰动）。
  3. ✓ VisualWebArena 自身消融：纯 a11y 文本 GPT-4 7.25% vs GPT-4V+SoM 16.37%；SoM 相对"截图+a11y"只 +1.32pt。

## 3. DOM 路线的失败模式（量化）

- ✓ **过期结构会劫持 agent**（最强证据）：[arXiv 2607.04334](https://arxiv.org/abs/2607.04334) 定义 Perception-Fusion Gap，735 探针（225 个来自线上站点的零编辑分歧）。四家厂商模型"像素读对了却听结构的"比例 >0；在**未编辑的 stale snapshot 上跟随过期结构最高 0.88**；多步任务中首步一次错信念即导致失败，**自恢复率 ≤0.03**。图像单独识读准确率近天花板 → 是融合失败不是感知失败。
- ✓ **ref 漂移**：Playwright MCP 官方文档明确 "Scope: unique within a single snapshot / **Lifetime: Valid until the page changes**"，stale 报错 `Ref <ref> not found in the current page snapshot`（[官方](https://playwright.dev/mcp/snapshots)）。**✗ 未查到任何"MCP ref 漂移率/element handle stale 比例"的量化统计**——官方只描述行为不给比例。
- ✓ **DOM 体积爆炸**：WorkArena L1 单页原始 HTML **40K–500K tokens**；11 种缩减方法 × 32 配置 × 33 任务的端到端评估需 **232.4 小时**（Qwen3.5-122B-A10B）（[NEC, arXiv 2605.29397](https://arxiv.org/abs/2605.29397)）。
- ✓ **同名/歧义元素**：SeeAct 论文称 textual choices 无法区分文本完全相同的元素，故需引入视觉消歧；其图像标注变体 100 样本错误分析为三类：错动作生成、编造 bbox/标签、框标签错配（论文未给各占比）。
- ✓ **结构敏感度依赖场景**：NEC 论文测得 WebLinx 去掉 text 内容使 coverage 掉 **59.5%**，而 WorkArena L1 更依赖 `id`/`class` 等属性。

## 4. 延迟与成本

- ✓ **Playwright MCP 官方数字**："Screenshot: **~10,000–50,000 tokens** (with vision model) / Snapshot: **~500–5,000 tokens** (text-only)"，**"10x–100x reduction in context usage"**；文件大小 100KB–5MB vs 5KB–50KB（[官方文档](https://mintlify.wiki/microsoft/playwright-mcp/concepts/accessibility-snapshots)）。
- ✓ 官方对比表（[snapshots](https://playwright.dev/mcp/snapshots)）：token cost 低/高、precision exact vs coordinate guessing、speed instant vs 慢、reliability deterministic vs layout changes break、vision model not required vs required。
- ✓ **browser-use**：官方文档（[all-parameters](https://docs.browser-use.com/open-source/customize/agent/all-parameters)）只有 `use_vision` / `vision_detail_level`，**没有 DOM vs 截图的 token 对比表**。官方唯一相关数字是 CLI 2.0（v0.12.3, 2026-03）自称 **"2x faster, 50% fewer tokens"**（改用 direct CDP + 编号元素列表替代 Playwright 冗长 a11y 序列化）——这是 **DOM 路线内部优化**，不可当作 DOM vs 截图的对比。
- △ 第三方估算（非官方，慎引）：AXTree ~230 tokens/step vs 截图 ~765；MCP 模式 ~114k vs CLI ~27k tokens/任务。
- ✓ **可验证的降本路径**：NEC 论文用 GEPA 优化剪枝程序，WorkArena L1 每步延迟 **2.2×** 且保留 **84%** 成功率，WebLinx **3.1×** 保留 **89%**；AXTree pruning 仅 0.27s/页（vs FocusAgent 105.69s/页）。

## 5. 2025–2026 趋势

**结论**：未查到任何权威评测/综述给出"DOM 优先 vs 像素优先"的直接胜负统计。2026 年的收敛答案是**分工 + 一致性校验**，而非路线二选一。

- ✓ **产品侧**：Playwright MCP 官方推荐"canvas apps, charts, image-heavy layouts"时快照+截图并用；默认把 screenshot 写入文件只回路径，要做坐标交互须 opt-in `--caps=vision`（[官方](https://raw.githubusercontent.com/microsoft/playwright-mcp/main/README.md)）。
- ✓ **论文侧**：[arXiv 2607.04334](https://arxiv.org/abs/2607.04334) 比较四种缓解措施，**只有 training-free consistency gate**（检测到冲突后重新落地）能同时降低 hijack 与任务错误；prompt 级提示在动作层无效。
- ✓ **观测侧**：[NEC arXiv 2605.29397](https://arxiv.org/abs/2605.29397)（2026-05）系统对比 11 种 HTML 缩减方法，a11y tree 已是标准启发式基线之一。
- △ **成绩侧**（protocol 不同，不可直接比较）：OSWorld-Verified 第三方榜单前列 Qwen3.8 Max 86.1%、Claude Fable 5 / Mythos 5 各 85%（[BenchLM](https://benchlm.ai/benchmarks/osworld-verified)）；UI-TARS-2（截图原生 GUI 模型）Online-Mind2Web 88.2 / OSWorld 47.5 / AndroidWorld 73.3（[arXiv 2509.02544](https://arxiv.org/abs/2509.02544)）；Agent S2（纯截图）OSWorld 27.0%(15 步) / 34.5%(50 步)（[arXiv 2504.00906](https://arxiv.org/abs/2504.00906)）。→ 纯像素路线与 DOM 路线在 OSWorld 上无法直接比较：Agent S2 纯截图 27.0% 低于早期 OSWorld 纯 a11y 文本 12.24% 的量级是不同实验设置（Agent S2 自带行为叙述器与检索）。
- ✗ **未查到**：2025–2026 的 web agent 综述给出 DOM 优先 vs 像素优先的裁决表；也不存在以 "WebArena visual obfuscation" 命名的子基准。

---

## 总结（191 字）

2026 年 DOM 路线的结构性盲区是"渲染出来但不在树里"的内容：canvas/WebGL 自绘控件、图表、地图、视频、图片内文字。Apple 对 4,068 个 app 的人工标注显示 59% 屏幕至少一个可见元素在 a11y tree 中无对应，4% 屏幕树全空。精度优势集中在元素定位：Mind2Web 离线 Step SR 39.1（DOM 文本）对 20.3（截图标注），VisualWebArena 纯 DOM 文本 GPT-4 为 7.25%，同任务像素路线最优 16.37%。但这约 2× 优势在长程成功率上被规划误差吞掉，且结构一旦过期会反客为主（stale 场景跟随率 0.88、自恢复率 0.03）。结论：DOM 负责"找得准"，像素负责"看得全"，必须配一致性校验而非二选一。

# r-s3-icon —— icon 子集 41.38% 怎么再往上推（公开一手来源调研）

调研线：icon 子集（ScreenSpot-Pro 抽样 87/183 条，当前 41.38% = 36/87）
本文件只写「公开来源怎么说 + 我们该怎么改 + 依据 + 数字」。本机实测数字不在此复述。
证据强度标注：**【一手】**= 本轮实际抓取并读到的原始页面内容；**【二手】**= 只拿到搜索综述/二手博客的转述（未逐字核到原文）；**【推断】**= 我自己的计算或推理。

---

## 0. 一句话结论

**icon 子集的主要矛盾不是「模型看不见图标」，而是「送进模型的那张图里，图标占的相对尺寸太小，而我们的候选清单里根本没有无文字图标」。**
公开文献里被反复验证的两条硬事实直接对应：
① 缩小搜索域（crop/zoom）是 GUI grounding 的**第一杠杆**（ScreenSpot-Pro：OS-Atlas-7B 18.9% → ScreenSeekeR 48.1%，+254%）；
② Set-of-Mark 的收益**完全来自候选集质量**，候选集差时 SoM 全线崩（密集 GUI 上所有被测模型 <30），候选集好时（oracle marking）才出现 +35~57 分的跃迁。
我们的代码里**已经有编号选择分支**（模型只答编号、落点 = 候选框物理中心），但候选源只有 UIA 控件名 + OCR 词框 —— **无文字图标根本进不了清单**，这正是 41.38% 的结构性原因。

---

## 1. (a) ScreenSpot-Pro / ScreenSpot-v2 上 icon 子集的公开分数与专门做法

### 1.1 一手（我抓到了论文 HTML 全文）

来源：<https://arxiv.org/html/2504.07981v1>（ScreenSpot-Pro, arXiv 2504.07981, CC BY 4.0）

| 事实 | 数字 | 出处 |
|---|---|---|
| 数据规模 | 1,581 条指令 / 每条一张独立截图 / 23 个应用 / 5 个行业 / 3 个 OS | Abstract、§3.1 |
| 最好的端到端模型 | **18.9%**（OS-Atlas-7B） | Abstract |
| ScreenSeekeR（无训练的级联搜索） | **48.1%**，相对提升 **+254%** | Abstract、§4.2 |
| 文本 / 图标占比 | 文本 **62.6%**，图标 **37.4%** | §3.4 |
| 目标平均占屏面积 | **0.07%**（ScreenSpot 是 2.01%） | §3.4 |
| 图标的判定口径 | 「a target is classified as icon **only when no text hints are present**」 | §3.2 |
| 分辨率天花板 | UGround 最高 **1344×1344**，QwenVL 只有 **448×448** | §2.2 |

**Table 1 逐应用计数（我按论文表格做的算术）**：icon 604 条 / text 977 条 / 合计 1581 → **icon 占 38.2%**【推断（算术），数据一手】。

**icon 比 text 更难**：论文 §5.1.2 专门有一节 “Icons targets are more difficult to ground than texts.”【一手·小节标题+结论方向】，具体逐模型数值在被截断的表格里，我**没有逐字核到** → 不引用具体数。

### 1.2 关键做法（可直接照抄的参数）

**ScreenSeekeR 的级联搜索（§4.2）**【一手】：
- **grounder 触发阈值**：patch 尺寸 ≤ **1280 px** 才调用 grounding 模型（“a hyperparameter set to 1280 pixels”）→ 即「先把搜索域缩到模型能吃下的尺寸再问」。
- **候选区域打分**：对每个候选区域，收集所有 grounding 框投票，权重
  `s = exp( -((x'-0.5)² + (y'-0.5)²) / (2σ²) )`（点落在区域内时），**σ = 0.3**（全实验固定）→ 偏好**靠中心**的小区域，抑制“大区域霸榜”。
- 投票框先做 **box dilation**（把小框扩成候选区，降低漏检风险），再对候选区域做 **NMS**。
- **递归 + 验证**：裁剪后递归搜索，**planner（GPT-4o）负责验证目标是否已找到**。

**ReGround（§4.1，最简单的 planner-free 基线，也是 planner-free 里最好的）**【一手】：
- “crops the region surrounding the initial prediction to re-ground and make a final determination. **The size of the crop can be manually configured based on the optimal input size of the models.**”
- 论文专门有 “Ablations on the **crop size** of ReGround” 一节 → **crop 尺寸是论文级消融变量**，不是实现细节。

### 1.3 二手（未逐字核到原文，引用需复核）

- GPT-4o 直接 grounding 在 ScreenSpot-Pro 上 **0.8%**（text 1.3 / icon 0.0）—— 两个独立搜索摘要一致，且与论文 “planner model GPT-4o scores only 0.8%” 的转述一致【二手】。论文链接同上（数字所在表格我未逐字读到）。
- ScreenSpot-v2 相关：ShowUI-2B **77.3**、OS-Atlas-Base-7B **85.1–87.1**、UGround-7B **76.3**（ScreenSpot-v2 avg）→ 通用 MLLM 在 ScreenSpot-v2 上普遍 70–87%，但掉到 ScreenSpot-Pro 就崩到 20–40%【二手】。这说明**高分辨率小目标**才是icon 子集的真问题，与屏幕小图标同源。

### 1.4 未能核实的一手（重要缺口，明说）

**ScreenSpot-v2 论文（“GUI Grounding in the Wild”）我没抓到**：我按记忆试的 arXiv ID 2402.02390 是另一篇编码理论论文（trifferent codes），抓错；两次搜索也没 surf 到该论文页面。**ScreenSpot-v2 对 icon 专门做过什么（是否用检测器先出框再 zoom、其 icon 子集逐项分数）本轮【未核实】**，不作为依据。若要核，下一步直接抓官方仓库 <https://github.com/rootsautomation/ScreenSpot-V2> 的 README 结果表（按平台 × Text/Icon 列出）。
→ 结论：**ScreenSpot-v2 的 icon 做法本轮只能标「未核实」，不要写进实施依据。**

---

## 2. (b) 公开的 GUI 图标检测/分割：精度 / 速度 / 体积 / 自报 ScreenSpot-Pro

### 2.1 OmniParser v1 / v2（唯一我抓到一手 README 的检测器）

来源：<https://github.com/microsoft/OmniParser>（README 原文）、<https://raw.githubusercontent.com/microsoft/OmniParser/master/docs/Evaluation.md>

| 项 | 内容 | 强度 |
|---|---|---|
| 仓库许可 | README 徽章 **MIT** | 【一手】 |
| **模型权重许可（原文）** | “`icon_detect_v3` is based on the **MIT-licensed YOLOv9** implementation. **Earlier Ultralytics-based icon detectors retain their original AGPL license.** The caption models are under the **MIT** license.” | 【一手·逐字】 |
| 自报 ScreenSpot-Pro | **39.5%**（v2，README 2025/1 新闻；release notes 写 39.6% —— 差 0.1，二手） | 【一手 / 二手】 |
| v2 组成 | 检测器 = YOLOv9-E（`icon_detect_v3`）；caption = Florence-2 系（`icon_caption_florence`）；权重在 HF `microsoft/OmniParser-v2.0` | 【一手】 |
| 新增（2026/7） | YOLOv9-E **交互区域**检测器，推理专用权重在 HF PR #37 | 【一手】 |
| 端到端延迟 | ≈0.6 s/帧（A100）/ ≈0.8 s/帧（RTX 4090） | 【二手】（MSR V2 blog，未抓） |
| 评测可复用物 | `eval/ss_pro_gpt4o_omniv2.py`：**他们用的提示词**，可直接替换官方 ScreenSpot-Pro 的 `models/gpt4x.py`；`eval/logs_sspro_omniv2.json`：他们的推理结果 | 【一手】 |

**这一条直接改写我们 spec §12 的许可结论**（现文：<docs/spec.md:368> 说「检测器是 YOLOv8 导出的 ONNX 而其自身许可未记录」「模型权重许可待查」）：
→ 事实是 **v2 的检测器已是 MIT 的 YOLOv9-E，caption 模型 MIT**；**AGPL 只存在于旧的 v1/v1.5 Ultralytics 检测器**。如果我们只用 `icon_detect_v3` + `icon_caption_florence`，AGPL 风险不存在（**仍需登记 MIT 义务：保留版权与许可声明**）。
**这解锁了「用检测器出候选框、但不让它做 grounding」这条路**（见 §3 UFO² 与 §4 改动 2）。

### 2.2 Iconeek —— 查不到公开来源，**本轮不可用**

两轮搜索（"Iconeek GUI icon detection dataset github" 及其变体）**均返回空结果**；模型综述明确说“无法确认存在公开仓库/数据集”。→ **【不适用】**，本轮不给 Iconeek 任何参数或数字，也不建议基于它做决策。如果它确有其物，请给出论文标题/链接再单独核。

### 2.3 UGround 的检测部分

- UGround 的定位是 **element detection + region selection**，底座是 **Florence-2**（0.23B）+ Qwen2-VL 语言侧；输入分辨率上限 **1344×1344**（ScreenSpot-Pro 论文 §2.2 一手引用）。
- 自报 ScreenSpot-Pro：UGround-V1-**7B 30.93**、**2B 21.32**（二手，来自 Holo1 论文的对照表）→ **一个 2B 的 grounding 模型在 6GB 3060 上 4bit 跑得动，但它在 ScreenSpot-Pro 上只有 21%**，说明“换本地 grounding 模型”这条路对我们 41% 的起点**不是首选**（我们要的是把已有 API 模型用好，不是换模型）。
- 许可：Qwen2-VL 派生权重**继承 Qwen 许可**（不是 Apache-2.0）→ 商业闭源前需核（二手，标注为待核）。

### 2.4 GroundingDINO / YOLO 系 / Faster R-CNN 的体积与速度（全部二手）

| 模型 | 许可 | 参数 / 体积 | 延迟（公开实测转述） | GUI 专用精度 |
|---|---|---|---|---|
| GroundingDINO-T (Swin-T) | **Apache-2.0** | **172M**；ckpt ≈660–690 MB；推理显存 ≈3.8 GB | GPU ≈100–180 ms/图（RTX 3060 ≈100–150 ms；4090 ≈180 ms；L4 base ≈786 ms p50）；CPU 官方支持 `--cpu-only` 但秒级 | **无 GUI 专用数字**（通用开放词表检测器；prompt 可问 “icon / small icon / toolbar button”） |
| YOLOv8/Ultralytics | **AGPL-3.0**（商用需 Enterprise） | — | — | OmniParser 老检测器用的就是它 |
| YOLOv9-E（OmniParser v2 用） | **MIT** | ~57M | OmniParser 端到端 0.6–0.8 s/帧（含 caption，二手） | OmniParser v2 自报 ScreenSpot-Pro **39.5%**（含 caption+GPT-4o） |
| Faster R-CNN | Apache-2.0 系 | — | CPU 可跑 | ScreenSpot-v2 论文是否用过 —— **本轮未核实** |

来源（二手，URL 见 §7）：<https://github.com/IDEA-Research/GroundingDINO>、<https://app.ikomia.ai/hub/algorithms/infer_grounding_dino/>、<https://huggingface.co/microsoft/OmniParser-v2.0>。

**GroundingDINO-T 的 3.8 GB 显存 / 172M / Apache-2.0 / ~100–180 ms** 这一组数字，正好命中我们这台 6GB 3060 的可用区间（【二手】，落地前必须自己测一遍显存峰值与延迟）。

---

## 3. (c) UFO² 的 clip-ViT-B-32 预筛：具体做法与 TOP_K（**这一节是一手配置原文**）

来源：<https://raw.githubusercontent.com/microsoft/UFO/main/config/ufo/system.yaml>（我逐行抓的原文）

```yaml
# Control Backend
CONTROL_BACKEND: ["uia"]          # 可加 "omniparser"
IOU_THRESHOLD_FOR_MERGE: 0.1      # UIA 与视觉框合并的 IoU 阈值

# Omniparser
OMNIPARSER:
  BOX_THRESHOLD: 0.05             # 检测框置信度阈值
  IOU_THRESHOLD: 0.1
  USE_PADDLEOCR: True
  IMGSZ: 640                      # 检测输入边长

# Control Filtering Configuration
CONTROL_FILTER_TYPE: []           # 'TEXT', 'SEMANTIC', 'ICON'
CONTROL_FILTER_TOP_K_PLAN: 2
CONTROL_FILTER_TOP_K_SEMANTIC: 15
CONTROL_FILTER_TOP_K_ICON: 15
CONTROL_FILTER_MODEL_SEMANTIC_NAME: "all-MiniLM-L6-v2"
CONTROL_FILTER_MODEL_ICON_NAME: "clip-ViT-B-32"
```

**「没有文字的图标怎么变成可编号的候选」—— UFO² 的答案是三步，全是配置里能抄的**：
1. **视觉层出框**：OmniParser-v2（YOLOv9-E 检测器 + Florence-2 caption）解析截图，返回每个元素的 **control type / confidence / bbox**（HF PR #37 文档口径，二手）。
2. **与 UIA 去重融合**：视觉框与 UIA 控件框 **IoU > 0.1 的丢弃**（`IOU_THRESHOLD_FOR_MERGE: 0.1`），幸存者**变成伪 UIA 对象**（`UIAWrapper`）→ 于是它们进入统一的「控件表」，也就自然进入编号清单。
3. **语义/图标双路预筛，留 TOP_K**：
   - `SEMANTIC`：SentenceTransformer(`all-MiniLM-L6-v2`，22M 参数、CPU 可跑）把**计划文本**与**控件名**编码后算余弦相似度 → 留 **TOP_K_SEMANTIC = 15**；
   - `ICON`：同一套逻辑但比的是**计划文本**与**控件图标 crop 的视觉特征**，编码器 = **`clip-ViT-B-32`** → 留 **TOP_K_ICON = 15**。
   （类名与实现位置 `ufo/automator/ui_control/control_filter.py`：`TextControlFilter` / `SemanticControlFilter` / `IconControlFilter`、工厂 `ControlFilterFactory` —— 行号与实现细节来自二手（DeepWiki 与第三方配置走查），**配置键值本身是一手**。）

**对我们的直接含义（推断）**：
- `clip-ViT-B-32` 是 **88M 参数、512 维输出、CPU 可跑**的小编码器 → 它不是“找图标”的检测器，而是**给已有候选框做排序/筛除**的排序器。这意味着**只要我们能拿到候选框**（哪怕来自 UIA 的无名控件、OCR 的邻近词框、或任何轻量检测器），就能用同一个 CLIP 把「无文字图标」和「指令文本」对上 —— **不需要一个能认语义的 grounding 模型**。
- UFO² 把「计划」也压到 TOP_K_PLAN = 2，即**只让模型在 2 个候选计划、每个计划 15 个控件里选** → 候选清单规模是刻意压小的。
- **我们的仓库已经有一半**：<src/minicode/ground/channel.py:910-938> 的 `_candidates` 已经实现「主表(UIA) 全留 + 附加表(OCR) 与主表 IoU>0.1 丢弃 + 其余追加」的 UFO² 式合并；<src/minicode/ground/prompt.py:230> 的 `numbered_candidates_prompt` 已经是「模型只答编号、落点取候选框物理中心（零换算）」的 SoM-lite。
  **缺的那一块是：候选源里没有「无文字图标」。** UIA 源给的是控件名（无名控件只得到位置占位名），OCR 源给的是文字框 —— 图标两边都进不去。

---

## 4. (d) Set-of-Mark / SoM 在 GUI 场景的复现效果与数字

### 4.1 二手（重要，分两半看）

**坏消息：密集 GUI 上 SoM 全面失败。**
Tongyi MAI《Why your AI Agent keeps misclicking? A Practical Guide to GUI Grounding for Frontier Models》：
- “**SoM was a comprehensive failure — every model scored below 30**”，原因被归为「密集 UI 元素检测（例如 OmniParser V2）产生大量视觉噪声与重叠框」；
- **E2E 像素坐标预测明显优于 SoM**；
- 真正的准确率杠杆是 **zoom-in / cropping**：ScreenSpot-Pro 上 **Gemini-3-Pro 39.0 → 69.7**、**Claude Sonnet 4.5 35.0 → 54.0**、**Seed1.8 67.0 → 73.3**；
- 掉点主因被归为 **1280×720 极端降采样把小元素抹掉**；
- **crop 比例很关键**：最佳为 **1/4 比例 @1920×1080**，与最差配置差 **11.4 分**；free-form thinking 几乎无益甚至有害。
- 一手链接（**我 fetch 时页面是 JS 动态渲染，只拿到框架，正文数字全部来自搜索综述**）：
  <https://tongyi-mai.github.io/MobileWorld/blog_post.html?post=Why%20your%20AI%20Agent%20keeps%20misclicking>【二手】；论文 <https://arxiv.org/abs/2512.19432>

**好消息：SoM 的收益来自「候选集」，不是来自「标记」这个动作本身。**
诊断研究（oracle two-candidate marking，**二手**）：把标记精确打在目标与干扰项上时
GPT-5 **30% → 87%**（+57.1，p<1e-50）、OS-Atlas-Base-7B **10% → 52%**、Qwen2.5-VL-7B **22% → 57%**、Claude Opus 4.7 31%→40%（Holm p=0.08 不显著）。
作者自陈三点：① 增益**与基线准确率成反比**；② 收益来自「**提供候选集**」（卸掉候选搜索负担）而不是修好了 grounding 技能 → 应读作**上界/诊断**；③ **CoT（+0.5）与 activation steering（+1.5）无显著作用** → 失败是**感知/跨模态绑定**层面的，不是语言推理层面的。
论文：<https://export.arxiv.org/pdf/2608.21832>【二手，未逐字核】

**OS-Atlas 的 agent 实验**：GPT-4o + SoM 的成功率 **4.59**，SeeClick **9.21**，OS-Atlas-Base 更高 → SoM 在那一组里是最弱的一种 grounding 方式【二手】。

### 4.2 对我们的判定（推断）

- **SoM/编号法的价值 = 候选集质量**。我们有高质量候选（UIA 控件 + OCR 词框，落点是物理像素中心，零换算），但**候选集漏掉了全部无文字图标** → SoM 在 text 子集上能吃到好处，在 icon 子集上吃不到，因为**清单里没有那个图标**。
- 结论：**要提升 icon 子集，第一件事不是把编号法做得更花哨，而是把「无文字图标」变成编号清单里的一行。** 这与 §3 的 UFO² 做法（视觉层出框 → 与 UIA 合并 → CLIP 预筛 → TOP_K=15 → 编号）完全一致。
- 同时，文献一致指向 **zoom/crop 是第一杠杆**（ReGround crop-size 消融、ScreenSeekeR 缩小搜索域、Tongyi 的 39.0→69.7 / 35.0→54.0）。我们的 crop 是 **512 物理像素 1:1**（<src/minicode/coord/crop_refine.py:58>、<src/minicode/ground/crop.py:22>），icon 在 crop 里相对宽度只有 **12/512 = 2.3%**（1536 档）；要把它提到 ~6%，crop 需收到 **~192–256 物理像素**。
  ⚠️ 但**不要拍脑袋定 256**：论文专门有 crop size 消融节，Tongyi 也说最优比例随配置变（最佳 1/4、最差差 11.4 分）→ **先扫尺寸再定**（我们的仓库已有 `--crop-width/--crop-height` 与 `--memo-cache` 配对机制，以及 `numbered_bench --crop-scan --crop-sizes` 的 k-fold 选尺寸流程）。

---

## 5. (e) 许可干净、体积小、能在这台 6GB 3060 上跑的替代（spec §12 的替代选项清单）

| 候选 | 许可 | 体积 | 公开延迟 | 公开精度 | 结论 |
|---|---|---|---|---|---|
| **OmniParser-v2 `icon_detect_v3`（YOLOv9-E）** | **MIT**（一手 README 逐字） | YOLOv9-E 级别，检测输入 **IMGSZ 640** | 端到端 0.6–0.8 s/帧（二手）；仅检测器应远小于此 | 自报 ScreenSpot-Pro **39.5%**（含 caption+GPT-4o） | **首选候选源**：只出框不做 grounding，许可干净；先测单帧延迟与显存 |
| **GroundingDINO-T (Swin-T)** | **Apache-2.0** | **172M**；ckpt ≈660–690 MB；推理显存 ≈**3.8 GB** | GPU ≈**100–180 ms/图**；CPU 秒级（二手） | 无 GUI 专用数字 | **许可最干净、体积最小的通用开放词表候选源**；可问 “icon / small icon / toolbar icon” |
| **Florence-2-base**（UGround 的检测底座） | MIT（microsoft） | **0.23B** | 未见公开 GUI 实测（二手称 UGround 用它做 region proposal） | UGround-V1-7B ScreenSpot-Pro 30.93 / 2B 21.32（二手） | 极小；适合做「短语→框」的候选生成 |
| **Holo1.5-7B** | **Apache-2.0**（一手 HF metadata） | 7B → 4bit 约 4–5 GB | — | ScreenSpot-Pro **57.94**（二手） | 6GB 3060 上 4bit 边界；**但那是换模型，不是本线的最小改动** |
| **Holo1.5-3B** | **Qwen 许可（license: other，不是 Apache-2.0）** | 3B | — | ScreenSpot-Pro **51.49**（二手） | 许可不干净 → 不作首选 |
| **ShowUI-2B / OS-Atlas-Base-7B / UGround-7B** | 待核（Qwen 系许可） | 2B / 7B | — | ScreenSpot-v2 77.3 / 85.1–87.1 / 76.3（二手） | 换模型路线，本轮不动 |
| **YOLOv8（Ultralytics）** | **AGPL-3.0** | — | — | — | **避开**：这正是 OmniParser 老检测器的坑 |

**判据建议（写进 spec）**：本项目的“许可干净”= **Apache-2.0 / MIT / 明确可商用**；AGPL、Qwen 许可、许可未记录 → 一律不进主链路。

---

## 6. 我们现有实现的盘点（只读核对，文件:行）

| 能力 | 现状 | 位置 |
|---|---|---|
| 两级 grounding（粗 → crop 精定位） | 有；crop **512×512 物理像素、1:1 不缩放**；crop 内 1 归一化单位 = 0.512 物理 px | <src/minicode/coord/crop_refine.py:58>、<src/minicode/ground/crop.py:22> |
| 送模型尺寸 | 生产档 **1536×960**；服务端宽 > **2016** 会被再缩（该档坐标系不可信） | <src/minicode/model/image.py:32,45> |
| 编号选择（SoM-lite） | **已有**：模型只答编号 → 落点 = 候选框物理中心（零换算）；模型答 0 = 清单里没有 → 回落 | <src/minicode/ground/channel.py:982-1060>、<src/minicode/ground/prompt.py:230-269> |
| 候选源 | 只有 **UIA 控件名**（主表）+ **OCR 词框**（附加表），IoU>0.1 合并 | <src/minicode/ground/candidates.py:219,368>、<src/minicode/ground/channel.py:910-938> |
| **缺失** | **无视觉/图标候选源；无 CLIP 图标预筛；numbered 分支不参与 ScreenSpot-Pro 评测三臂** | — |
| 评测臂 | always / on_demand / coarse_only | <src/minicode/eval/__main__.py>、<src/minicode/eval/sspro.py> |

**由此得到的结构性判断【推断】**：icon 子集低分不是「模型不行」，而是 (i) 送模型的图里 icon 相对尺寸太小（2.3%），(ii) 我们为 icon 准备的唯一一条「落点不经过模型坐标」的通路（编号分支）**里没有 icon**。

---

## 7. ≤8 条最小改动（按投入产出排；每条含：改哪个文件 / 做什么 / 预期哪个数变多少 / 怎么验证）

> 全部遵守「性能优先」：**没有一条以省 token / 省调用 / 降延迟为理由**；多花调用换准确率。

**① 零代码：先扫 crop 尺寸（配对）**
- 文件：不动代码；命令行参数 `--crop-width/--crop-height`（<src/minicode/eval/__main__.py:163>）
- 做什么：用**同一份 `--memo-cache`**（粗定位回答复用 → 真配对）跑 crop 尺寸 ∈ {1024, 768, 512, 384, 256, 192}。
- 预期：icon 子集 41.38% → **45%~50%**（依据：crop 相对尺寸翻倍即 2.3%→4.7%；公开 zoom 增益同向：35.0→54.0、39.0→69.7，二手）；**整体** 55.74% → 58%~62%。
- 验证：每次产出 `sspro-report.json`，比较 icon 子集；用报告自带的配对统计（McNemar mid-p）看显著性；用 `numbered_bench --crop-scan --crop-sizes ...` 的 k-fold 选尺寸流程避免在 183 条上过拟合。

**② 加「视觉图标候选源」，让无文字图标进编号清单**
- 文件：新增 <src/minicode/ground/candidates.py> 里的 `VisualIconCandidateSource`（实现既有 `CandidateSource` 协议，<candidates.py:169>），接到 <channel.py:910> 的 `additional_candidate_source`
- 做什么：用 OmniParser-v2 `icon_detect_v3`（YOLOv9-E，MIT）或 GroundingDINO-T（Apache-2.0）出框 → 转成 `GroundingCandidate`（物理像素）→ 走现有 IoU>0.1 合并规则。
- 预期：icon 子集 **+5~10pp**（41.38% → 46%~51%）；整体 +2~5pp。依据：SoM 收益全部来自候选集（oracle: 10→52、22→57，二手）。
- 验证：**先做离线候选覆盖率测量**（不调模型）：GT bbox 被候选框覆盖的比例必须 ≥95% 再接模型；否则先调 `BOX_THRESHOLD=0.05 / IMGSZ=640`（UFO² 一手配置值）。

**③ CLIP-ViT-B-32 图标预筛，TOP_K=15**
- 文件：新增 <src/minicode/ground/icon_filter.py>（纯本地、CPU）
- 做什么：SentenceTransformer(`clip-ViT-B-32`)（88M、512 维）对**候选图标 crop**与**指令文本**算相似度，保留 **TOP_K_ICON=15**（UFO² 一手配置值）；语义路 `all-MiniLM-L6-v2` TOP_K=15 一并可选。
- 预期：清单规模可控后编号命中率提升；候选多于 15 条时尤其明显（【推断】，未实测）。
- 验证：numbered 臂对比「全量候选 vs TOP_K=15」两跑，配对 McNemar。

**④ 让编号分支进 ScreenSpot-Pro 评测，作为第四臂**
- 文件：<src/minicode/eval/sspro.py>（臂定义与报告聚合处；`_IMAGE_TOKEN_STAGES` 已含 `numbered`，<sspro.py:748>）
- 做什么：加 `numbered` 臂（候选源=①+②，模型只答编号，落点=候选框物理中心）。
- 预期：icon 子集 +4~8pp（把「坐标误差不进落点」这条用在 icon 上）。
- 验证：`sspro-report.json` 里的 icon 子集列；与 always/on_demand 配对比较。

**⑤ 精定位改成「多尺度多窗口投票 + 校验」（不是重试同一提示词）**
- 文件：<src/minicode/ground/channel.py:453-790>（`_locate_by_point`）、<src/minicode/ground/prompt.py:169>（`refine_prompt`）
- 做什么：并行发 **3 个不同窗口**（512 中心 / 256 中心 / 256 偏移 ±32 物理像素）+ **不同措辞**的 refine 提示词变体 → 多数落点；再对落点发一次**校验问**（「这个点所在的元素是不是 X？是/否」），否定则取次高点。
- ⚠️ **关键约束（我们的实测）**：网关忽略 temperature，同提示词连发 10 次原文一致率仅 10% → **重发同一提示词拿多数票是无效的**，必须靠**不同窗口 / 不同措辞**制造独立样本。
- 预期：修掉「粗定位本来对的 75 条里被改坏 11 条」中的一部分 + 部分「假命中 20 条」。依据：ScreenSeekeR 用 planner 验证目标是否找到（一手）。
- 验证：配对统计里 `landing_source=refined` 的命中率 vs `coarse` 的命中率；报告已有 `landing_source` 与 `crop_contains_target` 字段可直接切。

**⑥ 用 ScreenSeekeR 的打分/NMS 替换「拍脑袋 crop」**
- 文件：<src/minicode/coord/crop_refine.py:152>（`plan_crop`）+ <src/minicode/ground/channel.py:818>（`_plan_fields`）
- 做什么：多候选窗时按 **中心度权重** `exp(-((x'-0.5)²+(y'-0.5)²)/(2·0.3²))`（σ=0.3，一手）排序，框先 **dilation** 再 **NMS**；grounder 只在窗口 **≤1280 px** 时调用。
- 预期：多窗口方案的落点更集中；预期 icon +1~3pp（【推断】）。
- 验证：与 ⑤ 同一批跑批对比。

**⑦ 更新 spec §12 的许可结论（解锁 §5 全部选项）**
- 文件：<docs/spec.md:368>、:450>、:480>
- 做什么：把「OmniParser 权重许可待查 / 检测器是 YOLOv8 ONNX 许可未记录」改为**已核实的事实**：v2 = `icon_detect_v3`（**MIT** YOLOv9-E）+ caption 模型 **MIT**；**AGPL 只在 v1/v1.5 的 Ultralytics 检测器**。同步登记 MIT 义务（保留许可声明）。若仍不进主链路，写清「只在离线候选覆盖测量里用」。
- 预期：**0 个准确率点，但解锁改动 ②**（不改 spec ② 就等于没做）。
- 验证：spec diff 自查 + 许可证登记表补一行。

**⑧ 修正对外口径：我们的 icon 占比被抽样抬高了**
- 文件：<src/minicode/eval/sspro.py>（报告渲染处，<sspro.py:1397> 一带）
- 做什么：报告同时给三个数：① 原始 183 条 icon 41.38%；② 按 ScreenSpot-Pro **全集 icon 占比 38.2%**（一手 Table 1 算术）**加权回全集口径**的整体分；③ 说明与公开数字比什么。
- 理由：我们 183 条里 icon **87/183 = 47.5%**，比全集 **38.2%** 高 9.3pp → **我们的 “整体 55.74%” 不能与论文的 overall 直接比**（我们的整体分被 icon 占比稀释得更狠）。
- 预期：**准确率 0 变化**，但避免一次「我们比公开数字高/低多少」的错误结论。
- 验证：报告里三行数自洽；用加权口径重述 g2/g3 结论。

**⑨（可选，若 ②的检测器实测不达标再上）许可干净的第二个候选源：GroundingDINO-T**
- 文件：同 ②（换实现即可，`CandidateSource` 协议不变）
- 做什么：Apache-2.0、172M、≈3.8 GB 显存、GPU ≈100–180 ms/图（二手）；开放词表问 “icon / small icon / toolbar button”。
- 预期：候选覆盖率与 OmniParser 相当或更好（**未验证**）。
- 验证：同一套离线覆盖率脚本对比两者；记录单帧延迟与显存峰值（6 GB 3060 上的真实值）。

---

## 8. 本轮明确的空白 / 不适用（不要当成结论用）

1. **ScreenSpot-v2 论文的一手做法与 icon 子集分数**：本轮**未核实**（arXiv ID 猜错、两次搜索没 surf 到）。下一步抓 <https://github.com/rootsautomation/ScreenSpot-V2> 的 README 表。
2. **Iconeek**：两轮搜索均无公开来源 → **不适用**，无参数可抄。
3. **Tongyi MAI 博客正文**：页面为 JS 动态渲染，我 fetch 只拿到框架；文中所有数字（39.0→69.7、35.0→54.0、67.0→73.3、SoM<30、1/4 crop 差 11.4 分）**全部来自搜索综述** → 二手，落地前应逐字复核原文。
4. **arXiv 2608.21832（oracle marking 诊断）**：二手，未逐字核。
5. **OmniParser 端到端延迟 0.6/0.8 s/帧**：二手（MSR V2 blog 未抓）。
6. **GroundingDINO 显存/延迟、Holo1.5 许可与分数**：二手（HF 模型卡与第三方 bench 转述）。
7. **ScreenSpot-Pro 上 GPT-4o 的 icon 具体数值**：二手（1.3/0.0/0.8，两处搜索一致；论文表格我未逐字读到）。
8. **本地缺 648 张图** → 本轮所有评测数字仍只能基于 183 条抽样；建议改动 ① 的尺寸扫描**只在有图子集上做**，并用 `--confirmation-split` 在确认集复核。

---

## 9. 引用清单（全部可打开）

一手（我本轮实际抓取）：
1. ScreenSpot-Pro 论文全文 HTML — <https://arxiv.org/html/2504.07981v1>（另：<https://arxiv.org/abs/2504.07981>、<https://gui-agent.github.io/grounding-leaderboard/>）
2. OmniParser README（含模型权重许可原文、自报 39.5%） — <https://github.com/microsoft/OmniParser>
3. OmniParser 评测说明（含 `ss_pro_gpt4o_omniv2.py` / `logs_sspro_omniv2.json`） — <https://raw.githubusercontent.com/microsoft/OmniParser/master/docs/Evaluation.md>
4. UFO² 系统配置原文（TOP_K_ICON=15 / clip-ViT-B-32 / BOX_THRESHOLD / IOU_THRESHOLD） — <https://raw.githubusercontent.com/microsoft/UFO/main/config/ufo/system.yaml>

二手（需复核）：
5. Tongyi MAI《Why your AI Agent keeps misclicking?》 — <https://tongyi-mai.github.io/MobileWorld/blog_post.html?post=Why%20your%20AI%20Agent%20keeps%20misclicking>（论文 <https://arxiv.org/abs/2512.19432>）
6. oracle marking 诊断 — <https://export.arxiv.org/pdf/2608.21832>
7. GroundingDINO 官方仓库 — <https://github.com/IDEA-Research/GroundingDINO>；参数/显存/延迟转述 — <https://app.ikomia.ai/hub/algorithms/infer_grounding_dino/>
8. OmniParser v2 权重 — <https://huggingface.co/microsoft/OmniParser-v2.0>；YOLOv9-E 交互区域检测器 PR — <https://huggingface.co/microsoft/OmniParser-v2.0/discussions/37>
9. Holo1.5 模型卡与许可 — <https://huggingface.co/Hcompany/Holo1.5-7B>、<https://huggingface.co/Hcompany/Holo1.5-3B>（3B 为 Qwen 许可）
10. UFO² 视觉检测文档（OmniParser 端点 + 融合规则） — <https://github.com/microsoft/UFO/blob/main/documents/docs/ufo2/core_features/control_detection/visual_detection.md>；过滤实现 `ufo/automator/ui_control/control_filter.py`
11. ScreenSpot-v2 官方仓库（本轮未抓，标为待核） — <https://github.com/rootsautomation/ScreenSpot-V2>
12. OmniParser v2 延迟（MSR blog，未抓） — <https://www.microsoft.com/en-us/research/articles/omniparser-v2-turning-any-llm-into-a-computer-use-agent/>

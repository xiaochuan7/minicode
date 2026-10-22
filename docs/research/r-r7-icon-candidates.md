# R7 调研报告：没有文字的图标，候选从哪来？

> 调研线：R7（纯视觉静态截图里，**无文字图标**的候选来源）
> 日期：2026-10-22
> web 工具调用：12 次（达上限）。抓取失败的 URL 已在文中标注，失败的来源一律降级为【二手】/【推断】，不停留在该步。
> 只读约束：本轮未修改任何文件（除本报告）。

---

## 0. 一句话结论

**公开证据不支持"加一个 GUI 图标检测器"这条路来解我们的 icon 类问题**（唯一有 GUI 实测的检测器方案 OmniParser v2，官方 ScreenSpot-Pro 的 icon 类准确率约 4.65%，而 text 类 58.89%）；**UFO² 的 clip-ViT-B-32 预筛根本不是候选生成器**（它的输入是 UIA 已经给出的控件框裁图），所以它只能救 UIA 通道的"选哪个控件"，救不了"纯静态图的图标从哪来"；**真正被一手论文验证过的路线是 ScreenSeekeR 式的"planner 出候选区域 → crop → 再定位"（18.9% → 48.1%）**，而这正是我们"选编号"范式缺的**候选召回层**。

---

## 1. 检索边界（哪些是一手正文，哪些没抓到）

| 来源 | 本轮是否抓到正文 | 证据强度 |
|---|---|---|
| `raw.githubusercontent.com/microsoft/OmniParser/master/README.md` | ✅ 全文 | 【一手】 |
| `raw.githubusercontent.com/microsoft/OmniParser/master/docs/Evaluation.md` | ✅ 全文 | 【一手】 |
| `arxiv.org/html/2504.07981v1`（ScreenSpot-Pro） | ✅ 到 §4.2 结束（§5.1 表 3 之后被截断） | 【一手】 |
| `arxiv.org/html/2504.14603v1`（UFO²） | ⚠️ 到 §3.3 结束（**§3.4 视觉层增强 / §6.3 融合检测评测被截断**） | 【一手·截断】 |
| `raw.githubusercontent.com/IDEA-Research/GroundingDINO/main/README.md` | ✅ 全文 | 【一手】 |
| `ai.azure.com/catalog/models/microsoft-omniparser-v2-0` | ⚠️ 正文截断（仅标题） | 【二手】 |
| `huggingface.co/microsoft/OmniParser-v2.0` | ❌ 两次 fetch 均 `TypeError: fetch failed`（已按规则各重试 1 次） | 【二手】 |
| `github.com/microsoft/UFO`（`config/ufo/system.yaml`、`ufo/automator/ui_control/control_filter.py`） | 本轮未再抓（用仓库已抓结论） | 【一手·本仓库已抓，见 `docs/research/blocker-resolution.md` L288–289 / L497、`docs/research/bottom-drafts/09-zoom开源实现调研.md` L657] |

---

## 2. 我们的 icon 类不是自创口径（外部对照）

ScreenSpot-Pro 论文正文【一手】<https://arxiv.org/html/2504.07981v1>：

* 全集 1,581 条 / 23 个应用 / 3 个 OS；**text 62.6%、icon 37.4%**（§3.4）。
* **icon 的判定口径 = "no text hints are present"**："If text labels are present, the target is labeled as text, even if an icon is included."（§3.2）
  → 与我们自己的 icon 口径（目标区域与任何 OCR 词零交集）**方向一致**：我们的 icon 类 ≈ 论文的 icon 类，不是自创难度。
* **目标框平均只占屏幕 0.07%**（ScreenSpot 是 2.01%）（§3.4）。
* 截图要求 >1080p 且**关闭显示缩放**（§3.2）。
* 论文时点最佳模型仅 **18.9%**；作者自研的免训练 **ScreenSeekeR = 48.1%**（+254% 相对），机制是 "planner 提议候选区域 → 裁剪 → 再定位"，搜索最小尺寸 1280 px、距离打分 σ=0.3、候选区域间做 NMS（Abstract + §4.2）【一手】。

**含义**：公开文献里"高分辨率小图标 grounding"的成功方案没有一个是"检测器 + 编号"，全部是"**缩小搜索空间**"。我们的 88M/92% 零交集问题本质是**搜索空间没有被有效切分**，不是"缺一个检测器"。

---

## 3. (a) 公开的 GUI 图标检测器：精度 / 速度 / 体积 / 许可

### 3.1 OmniParser v1 / v2 —— 唯一有官方 GUI 实测的那条路

**许可口径（本轮首次核清，直接改我们文档）**【一手，OmniParser README 原文逐字】：

> **Model Weights License**
> `icon_detect_v3` is based on the **MIT-licensed YOLOv9** implementation. Earlier Ultralytics-based icon detectors retain their original **AGPL** license. The caption models are under the **MIT** license.

配套事实【一手，同一 README】：

* `[2026/7] We add a **YOLOv9-E interactive region detector**. Its inference-only weight is available in Hugging Face PR #37.`
  → 权重在**尚未合并的 HF PR**里：<https://huggingface.co/microsoft/OmniParser-v2.0/discussions/37>（供应链需注意）。
* 仓库 License badge = **MIT**；caption 模型 MIT；**早期 YOLOv8/Ultralytics 派生的检测器仍是 AGPL**。
* ⚠️ 上游 caveat【推断·需向上游核】：Ultralytics 官方 `YOLOv9` 仓库本身是 **AGPL-3.0**，而 OmniParser README 自述其 `icon_detect_v3` 基于 "MIT-licensed YOLOv9 implementation"（大概率是 fork）。**闭源商用前必须索取该 fork 的 LICENSE 文件**，不能只看 README 一句话。

**公开数字**：

| 项 | 数值 | 出处 / 条件 |
|---|---|---|
| ScreenSpot-Pro 端到端（GPT-4o + OmniParser v2） | **39.5%** | OmniParser README News（2025/1）【一手】 |
| ScreenSpot-Pro（模型卡/目录口径） | 39.6% | HF 模型卡【二手，本轮抓取失败】/ Azure 目录【二手】 |
| 延迟 | **0.6 s/frame @A100；0.8 s/frame @RTX 4090** | Azure Model Catalog【二手】 |
| ScreenSpot-Pro 子类分布 | **text 58.89% / icon 4.65%**；合计 **55/139 = 39.57%** | 官方 `eval/logs_sspro_omniv2.json`【一手·本仓库上轮抓取，本轮未复核】；文件位置见 `docs/Evaluation.md`【一手，本轮抓到】 |
| OmniParser 论文自身的元素类型消融（ScreenSpot） | GPT-4V **直出 xy**：16.2 / Desktop Icon **11.8** → OmniParser **编号式**：**73.0 / 63.6** | arXiv 2408.00203 Table【一手·本仓库已记，`docs/research/blocker-resolution.md` L513】 |
| 检测器体积 | 早期 YOLOv8 派生 ONNX ≈ **11.6 MB**（第三方实测） | <https://88plug.github.io/screen-mcp/v1.1-grounding-research/>【二手·本仓库底稿 04 L712 已记】 |
| YOLOv9-E 参数量 | ≈ **58 M 参数 / 257 GFLOPs** → fp16 ≈ **58 MB**、fp32 ≈ 116 MB | ultralytics/YOLOv9 表【二手·本轮未抓】；体积换算【推断】 |

**判读**：

1. **"选编号"范式 ≫ "模型直出坐标"**（16.2 → 73.0，Desktop Icon 11.8 → 63.6）【一手·OmniParser 论文 Table】→ 我们坚持"模型只答编号、落点取候选框中心"的范式**有强外部支撑**，不必动摇。
2. **但 OmniParser 这条路在 Pro 级图标上本身是崩的**：官方 logs 里 icon 类 4.65% vs text 58.89%。既然它自己都在 icon 类上崩，**把它当我们的"图标候选源"没有依据**。
3. 它的延迟（0.6–0.8 s/帧 @A100/4090）不是我们 6GB 3060 的问题，**真正的问题是精度/召回，不是算力**。

### 3.2 UGround —— **没有"检测部分"**（这条线的问题本身不成立）

UGround 是**端到端 VLM 直接输出 bbox/点**（Qwen2-VL-7B 底座），**不存在独立检测器**；论文把它的贡献写成"把 grounding 变成专用任务 + 1.3M 截图/10M 元素的监督"，而不是"检测 + 匹配"。
* UGround-V1 ScreenSpot-Pro **31.1%**（发布时宣称相对 SOTA 18.9 → 31.1）【二手·GitHub/项目页摘要】
* 存在 `UGround-V1-2B` 权重（int8 约 2.5–3 GB，**可落 6GB**）【二手】，许可 Apache-2.0【二手·本轮未抓 LICENSE】
* **对本项目的适用性：不适用本轮** —— 它要换掉我们的 grounding 后端并改坐标契约（`docs/spec.md` 已有"不训练/不微调定位模型"的路线约束），且它仍是"模型直出坐标"路线（见 §5.1 的消融）。

URL：<https://github.com/OSU-NLP-Group/UGround> ｜ <https://osu-nlp-group.github.io/UGround/>

### 3.3 Grounding DINO —— 文本提示式，**无法枚举"所有可点图标"**

【一手，官方 README】：

* Swin-T：COCO zero-shot **48.4 AP** / fine-tune 57.2；Swin-B：**56.7 AP**；Highlight 写 COCO zero-shot **52.5 AP**。
* 默认阈值：**`BOX_THRESHOLD = 0.35`、`TEXT_THRESHOLD = 0.25`**，最多输出 **900** 个框。
* 2023/03/27 起**支持 CPU-only 模式**（README News）。
* 常见延迟说法 0.3 s/帧(A100)、~10 s/帧(CPU)【二手·本轮未在 README 中找到该表】。
* 许可：**Apache-2.0**【二手·仓库 LICENSE，本轮未抓】

**为什么它不适合我们的"图标候选"**【推断，基于一手 README 的输入范式】：

1. Grounding DINO 是 **open-vocabulary、文本驱动**的：给什么词就检什么。要覆盖 GUI 里的"全部可点图标"，必须先给出一张**类别词表**（`icon . toolbar button . checkbox . tab . menu .`）——这是**猜测类别**，不是穷举；专业软件里的私有图标（CAD/PS 的工具条）不在任何词表里。
2. 它在自然图像上训练，**没有公开的 GUI 图标召回数字**（检索无一手数据）。
3. 训练输入分辨率远低于 ScreenSpot-Pro（后者目标框平均 0.07% 屏面积）→ 小图标召回无保障。

URL：<https://github.com/IDEA-Research/GroundingDINO> ｜ <https://arxiv.org/abs/2303.05499>

### 3.4 Faster R-CNN / YOLO 系（经典 GUI 元素检测）

* 基准论文：**"Object Detection for Graphical User Interface"**（FSE 2020），Rico/RicoSCA 系数据集的经典基线 <https://arxiv.org/abs/2008.05132>【一手·论文存在；本轮未抓表，**具体 mAP 未取到，不给数字**】。
* 2024 年复现对比：<https://arxiv.org/abs/2408.03507>（YOLO 变体在 GUI 元素上的对比）【二手·仅摘要】。
* **判读**：这条线是 2020 年 RICO 时代的"Web/Android 控件"检测，目标域是手机/网页 UI 控件，**与我们的 Windows 专业软件小图标不同域**，且没有 ScreenSpot-Pro 口径的公开数 → 不作为候选源决策依据。

### 3.5 Iconeek —— **查无此物**（不适用）

两轮公开检索（含 `arxiv`、`github`、通用网页）**均无同名项目/论文/数据集**【一手·检索结论】。可能与以下三者混淆：

| 疑似混淆对象 | 实际是什么 | URL |
|---|---|---|
| **IconNet**（Google Research） | 移动端 UI 的**图标分类**模型，检测 **31 类**图标（计划扩到 70+）；不是桌面 GUI grounding | <https://research.google/blog/improving-mobile-app-accessibility-with-icon-detection/> |
| **Multimodal Icon Annotation** | 截图 + view hierarchy 定位并分类 **29 类**移动端图标 | <https://arxiv.org/abs/2107.04452> |
| **IconFinder** | 图标**素材库**（爬取源），不是检测器 | <https://iconfinder.com/> |

**结论：不适用**。若要"图标库"路线，用 IconFinder 做素材，配合自己的标注，而不是找一个不存在的 Iconeek。

---

## 4. (b) UFO² 的 clip-ViT-B-32 图标预筛：**它不是候选生成器**

这是本线最容易踩的坑，必须写清楚。

### 4.1 官方实现（我们仓库已抓一手源码）

* `ufo/automator/ui_control/control_filter.py` L250–272：`control_filter(control_dicts, cropped_icons_dict, plans, top_k)` —— **用裁出的图标做 CLIP 风格 embedding 匹配，挑 top-k 候选控件**。
* `get_cropped_icons_dict()`：把 **UIA 元素框**裁出来做 CLIP 匹配（全仓 16 处 `crop` 命中**全部**是这一件事，**不是屏幕区域放大**）。
* 官方配置常量（`config/ufo/system.yaml`）：`CONTROL_FILTER_TOP_K_ICON: 15`、`CONTROL_FILTER_MODEL_ICON_NAME: "clip-ViT-B-32"`、`IOU_THRESHOLD_FOR_MERGE: 0.1`、`BOX_THRESHOLD: 0.05`、`CONTROL_BACKEND: ["uia"]`。
* 出处记录见 `docs/research/blocker-resolution.md` L288–289 / L497、`docs/research/bottom-drafts/09-zoom开源实现调研.md` L657 / L673。

### 4.2 关键判读

**UFO² 的 CLIP 预筛，输入是「UIA 已经给出的控件框的裁图」，输出是「top-k 控件」。**
它解决的是**"几十个控件里选哪个"**（语义检索），**不是**"像素里有哪些图标"（候选召回）。

| 我们的场景 | UFO² 的 CLIP 预筛是否适用 | 理由 |
|---|---|---|
| 有 UIA 树，图标控件 `Name` 为空 → 要在 30 个匿名控件里挑对 | ✅ **直接可用** | 输入条件完全一致（UIA 框 → 裁图 → 与指令文本做图像-文本相似度 → top-k） |
| 纯静态截图、无 UIA 树 → 目标是一个没有文字的图标 | ❌ **不适用** | 没有 UIA 框可裁；CLIP 是**检索器不是检测器**，无法凭空产出框 |

【推断】由此可得两条硬结论：
1. 我们 `docs/spec.md` §12 L372–373 留下的待办「候选源能不能接像素级预筛（clip-ViT-B-32, TOP_K_ICON 15）」**问错了问题**：UFO² 那套不是像素级预筛，是控件级检索。**建议改写并关闭该待办**。
2. 它的 `TOP_K_ICON: 15` 是一个**可以直接抄的常量**：我们的候选清单现在是 `MAX_CANDIDATES = 20`（`src/minicode/ground/candidates.py:74`，取自 OCR 档）。UIA 图标档改用 15 与 UFO² 对齐。

### 4.3 未取到的数字（诚实标注）

* UFO² 论文 §3.4「Vision-Layer Augmentation」与 §6.3「Evaluation on Hybrid Control Detection」的**正文与 CLIP 预筛消融数字本轮没抓到**（arXiv HTML 抓取在 §3.3 被截断）【一手·抓取截断】。
* 我们仓库已记录的 UFO² Table 3【一手·论文】：每图平均检出 **UIA 37.3 / OmniParser 127.3 / 合并 97.9**；且 **"27.9% 和 56.7% 的 OmniParser-v2 检出因与 UIA 重叠被丢弃"**（即视觉检测器确实能补 UIA 之外的候选，净增约 70 个/图）——但**"补了候选"≠"补对了目标"**，官方 ScreenSpot-Pro 的 icon 4.65% 就是后者的答案。

URL：<https://arxiv.org/abs/2504.14603> ｜ <https://github.com/microsoft/UFO> ｜ <https://github.com/microsoft/UFO/blob/main/documents/docs/ufo2/overview.md>

---

## 5. (c) "不用任何检测器"的两条路

### 5.1 路 A：让模型直接输出框 —— **已被一手消融否定为"更好的范式"**

OmniParser 论文 Table【一手·本仓库已记，`docs/research/blocker-resolution.md` L513】：

| ScreenSpot | GPT-4o 直出 xy | OmniParser 编号式 |
|---|---|---|
| 总体 | 16.2 | **73.0** |
| Desktop Icon | **11.8** | **63.6** |

→ 在**图标**这一类上，"直出坐标"比"选编号"差 **5.4 倍**。我们的选编号范式方向正确，**不该退回让 API 模型直接报坐标**；要修的是**候选召回**，不是输出形式。

### 5.2 路 B：Set-of-Mark（SoM）—— **公开证据不支持，且与我们的 6GB 约束冲突**

| 证据 | 数值 / 结论 | 强度 |
|---|---|---|
| SoM 原论文（COCO/ADE20K/RefCOCO，非 GUI） | GPT-4V RefCOCOg ACC@0.5 **25.7% → 86.4%**；COCO precision 75.7%；ADE20K 63.4% | 【二手·alphaxiv 摘要，<https://arxiv.org/abs/2310.11441>】 |
| GUI 场景 | **没有公开的 ScreenSpot-Pro / ScreenSpot 上 SoM 复现数**（两轮检索无一手数字） | 【一手·检索结论】 |
| 我们仓库已有的 2026 第三方结论 | "所有被测模型在 **SoM 下 OSWorld-G 均 <30**，E2E 原生 grounding 明确优于 SoM" | 【二手·本仓库底稿 03 L361] |
| 分割模型在 13–16 px 图标上的可用性 | 我们的 icon 在模型输入图里只剩 **13.25–14.5 px**（61.4% = 216/352 小于 16 px）→ SAM 系在这个尺度上分割不可靠 | 【推断 + 本仓库自测数据】 |

**结论：不适用**（至少不适合作主 grounding 通道）。补充一句事实核查：SoM 官方代码库 `microsoft/SoM` 的实现本身**依赖 SAM 做分割**，所以"用 SoM"在工程上就等于"引入一个分割模型"，与我们 §12 的架构纪律直接冲突。

URL：<https://arxiv.org/abs/2310.11441> ｜ <https://github.com/microsoft/SoM> ｜ <https://som-gpt4v.github.io/>

### 5.3 路 C（真正被验证的）：**ScreenSeekeR 式区域搜索** —— 推荐

【一手，ScreenSpot-Pro 论文 Abstract + §4.2】<https://arxiv.org/html/2504.07981v1>：

* OS-Atlas-7B **18.9% → 48.1%**（+254% 相对），**免训练**。
* 机制：planner（GPT-4o）从截图 + 指令**提出若干候选区域** → 候选区域按"各 grounding 框的投票 + 中心性权重（σ=0.3）"打分 → NMS → 递归裁剪 → 区域小到 **1280 px** 以下才调 grounder → planner 判定是否命中。

**为什么这对我们最合适**：
1. 我们已经有 **planner（API 通用模型）**、**裁剪/坐标换算链**、**grounding 通道**（`src/minicode/ground/`），缺的只是"**候选区域从哪来**"这一层。
2. 它不需要任何检测模型 → **不触碰 §12 的架构禁令**，不新增模型体积，6GB 机器零负担。
3. 它直接命中我们 101 条未命中里占比最大的那桶："接线错 66/101 (65.3%)，其中 36.1% 的 crop 窗口压根没框住目标" —— 我们的 crop 窗口是**由一个粗点决定的固定窗**，而 ScreenSeekeR 的窗是**planner 提议的多个区域**。

---

## 6. (d) 许可干净 + 体积小 + 6GB 能跑的替代清单

| 名称 | 许可 | 体积 | 公开延迟 | 公开 GUI 图标精度 | URL |
|---|---|---|---|---|---|
| **OmniParser `icon_detect_v3`**（YOLOv9-E，2026/7 新增） | README 自述 **MIT-based**；上游 YOLOv9 fork 需核【一手 + 推断】 | ~58 M 参数 → **fp16 ≈ 58 MB**【推断】 | 无官方延迟；YOLOv9-E 257 GFLOPs，3060 fp16 应为几十 ms 级【推断】 | **有**：官方 logs icon 类 4.65%（Pro 尺度）【一手·上轮抓】 | <https://github.com/microsoft/OmniParser> |
| OmniParser 早期检测器（YOLOv8 派生 ONNX） | **AGPL-3.0**（Ultralytics 派生）【一手 README】 | ≈ 11.6 MB【二手】 | — | 同上体系 | 同上 |
| **Grounding DINO Swin-T** | Apache-2.0【二手】 | ≈ 690 MB checkpoint【二手】 | 0.3 s/帧 A100【二手】 | **无** | <https://github.com/IDEA-Research/GroundingDINO> |
| **Florence-2**（base 0.23B / large 0.77B） | MIT【二手】 | 0.23B ≈ 0.5 GB fp16 / 0.77B ≈ 1.5 GB fp16【推断】 | 无官方 GUI 延迟【—】 | **无**（官方强调需微调后用） | <https://huggingface.co/microsoft/Florence-2-large> |
| **MobileSAM** | Apache-2.0【二手】 | ≈ 40 MB【二手】 | 10 ms GPU【二手】 | **无** | <https://github.com/ChaoningZhang/MobileSAM> |
| **EfficientSAM**（tiny 9M / small 40M） | Apache-2.0【二手】 | ≈ 10–80 MB【二手】 | 毫秒级【二手】 | **无** | <https://github.com/yformer/EfficientSAM> |
| **SAM 2**（tiny/small/large） | Apache-2.0【二手】 | 38–160 MB【二手】 | 毫秒级【二手】 | **无** | <https://github.com/facebookresearch/sam2> |
| **PP-YOLOE**（PaddleDetection） | Apache-2.0【二手】 | tiny ~3 MB / s ~10 MB【二手】 | CPU 毫秒级【推断】 | **无** | <https://github.com/PaddlePaddle/PaddleDetection> |
| **clip-ViT-B-32**（UFO² 用的那个） | MIT（OpenAI CLIP）【二手】 | **88 M 参数**，CPU 可跑【一手·我们 spec §12 已记】 | CPU 可跑【一手】 | 用途=**检索/筛选**，非检测 | <https://huggingface.co/openai/clip-vit-base-patch32> |

**判读**：真正"许可干净 + 小 + 能上 6GB"的就两类：
* **检测类**：`icon_detect_v3`（MIT，~58 MB）、PP-YOLOE / Grounding DINO / Florence-2 —— 但**没有一个有"GUI 图标召回率"的公开数**，而唯一有官方 GUI 数的（OmniParser）恰恰在 icon 类崩到 4.65%。→ **不能作为决策依据，只能作为实验对象**。
* **检索类**：`clip-ViT-B-32`（MIT，88M，CPU）—— **这个是唯一有明确工程定位的**：给已有候选做语义排序（UFO² 的 `CONTROL_FILTER_TOP_K_ICON: 15`）。

---

## 7. 我们该怎么改（按文件）

### 7.1 立刻可做（零新模型、零性能风险）

1. **把"像素级检测器"从候选源方案里彻底删掉**，理由用一手数字钉死：OmniParser 官方 ScreenSpot-Pro icon 类 4.65%（text 58.89%）、延迟 0.6–0.8 s/帧 @A100/4090、早期检测器 AGPL、Pro 尺度目标框平均 0.07% 屏面积。
2. **UIA 通道接 UFO² 的 CLIP 预筛**（这才是 UFO² 那套的正确用法）：`clip-vit-base-patch32`（MIT，88M，CPU），query = 目标描述文本，对 **UIA 控件框裁图**打分，取 `top_k=15`。
3. **加"候选召回率"指标**，把"候选没召回"与"模型选错"分开（当前我们只有端到端准确率，无法指导任何改动）。
4. **规范里的许可口径更新**：`icon_detect_v3` MIT-based（2026/7 新增，权重在未合并的 HF PR #37）；早期 YOLOv8 派生检测器 AGPL；caption MIT。

### 7.2 需要改架构的（下一轮，本轮只落规格）

5. **无 UIA 时的图标定位，改成 ScreenSeekeR 式两段式**：
   * planner（我们的 API 通用模型）基于**整屏物理帧**提出 N 个候选区域（工具条带 / 侧边栏 / 画布区 / 面板区……）；
   * 对每个区域做**带物理坐标原点的裁剪**（我们的换算链已经有唯一真值，直接复用）；
   * 在区域内做精定位；模型只答编号。
   * 关键：**crop 窗口不再由"一个粗点 ± 固定 192px"决定**——这正是我们 36.1% 未框住目标的来源。
6. **若将来仍要上检测器，输入必须是物理帧/不降采样**：我们自测 icon 在 1280×800 的模型输入图里只剩 13.25–14.5 px，而在**物理 2560×1600 帧里约 26–29 px** —— 检测器若跑在缩图上，与我们的 icon 塌陷是同一个病因。

---

## 8. ≤8 条最小改动清单

| # | 改哪个文件 | 做什么 | 预期哪个数变多少 |
|---|---|---|---|
| 1 | `docs/spec.md` §12（L368–373） | 关闭「候选源接像素级预筛（clip-ViT-B-32, TOP_K_ICON 15）」待办，改写为：**CLIP 预筛只许作用于 UIA 已给框的控件，不得用于从像素生成图标候选**（依据：UFO² `control_filter.py` 输入 = UIA 框裁图） | 待办 1→0；运行指标不变；省一次"加检测器"的讨论 |
| 2 | `docs/spec.md` §12 + `docs/research/01-架构决策备忘.md` C-10（L499） | 许可口径更新为「`icon_detect_v3` = MIT-based YOLOv9-E（2026/7 新增，权重在未合并的 HF PR #37）；早期 YOLOv8/Ultralytics 派生检测器 **仍 AGPL**；caption 模型 MIT；上游 YOLOv9 fork 的 LICENSE 需向上游核」 | 许可风险"未核清"→"已核清（仍需向上游核 fork）"；**仍不做实时件** |
| 3 | `src/minicode/ground/candidates.py` | 对 **UIA 控件框裁图**接 CLIP 语义预筛：`openai/clip-vit-base-patch32`（MIT，88M，CPU），query = 目标描述，**top_k = 15**（抄 UFO² `CONTROL_FILTER_TOP_K_ICON`） | UIA 图标档候选清单 20→**15**；针对我们"挑错元素"桶（101 失中里 n=107、中位误差 226 px）下降；验收口径 = 新增的 recall@15 与「答错编号率」 |
| 4 | `src/minicode/ground/candidates.py` + `src/minicode/ground/test_ground_numbered.py` | 加两个**分离**指标：候选召回率 recall@k（GT 框是否在候选清单里）、模型选对率（选对且落点中位误差） | icon 类候选召回率从当前 OCR 词级口径（**5.8%**、92% 零交集）变成可量化基线；此后所有改动有验收口径 |
| 5 | `docs/spec.md` §11/§12 + `src/minicode/ground/`（下一轮） | 无 UIA 时的 icon 定位改为 **ScreenSeekeR 式**（planner 出候选区域 → crop → 再定位），crop 位置不再由单个粗点决定（依据：论文 18.9%→48.1%） | 命中"接线错 66/101 (65.3%)"这桶里 **36.1% 的 crop 未框住目标**；需与 r-接线线合并定参（本轮不定） |
| 6 | `docs/research/vision-root-cause.md` | 补「外部对照」条目：ScreenSpot-Pro icon 口径 = 无文字提示（与我们口径一致）、目标框平均 **0.07%** 屏面积、OmniParser 官方 logs **text 58.89% / icon 4.65%**、OmniParser 论文 编号式 73.0/63.6 vs 直出 xy 16.2/11.8 | 无运行指标；把"icon 29.9%"从自测黑箱变成**与公开基线对齐**的可辩护结论 |
| 7 | `docs/spec.md` §12 | 把"不做 SoM / 不做像素级检测器做实时件"写成**有据的否**：SoM 无 GUI 公开复现数 + 2026 第三方 OSWorld-G 全 <30 + SAM 系在 13–16 px 图标不可靠 + SoM 实现依赖 SAM（等于引入分割模型） | 一个未拍板项变成有据的否；防止后续重复讨论 |
| 8 | `docs/research/02-方案全景与双打分排名.md`（候选源栏） | 补"许可干净替代清单"：`icon_detect_v3`（MIT，~58M，fp16≈58MB）/ Grounding DINO（Apache-2.0，~690MB）/ Florence-2（MIT）/ PP-YOLOE（Apache-2.0）/ MobileSAM+EfficientSAM（Apache-2.0），每条注明「**公开 GUI 图标精度：无**」 | 无运行指标；避免重复调研、阻止无据引入 |

---

## 9. 证据强度汇总

| 结论 | 强度 | 备注 |
|---|---|---|
| OmniParser README 的许可段（icon_detect_v3 MIT-based YOLOv9；早期检测器 AGPL；caption MIT） | 【一手】 | 本轮抓到 README 全文 |
| OmniParser v2 ScreenSpot-Pro 39.5%（GPT-4o 管线） | 【一手】 | README News |
| OmniParser 延迟 0.6 s/帧@A100、0.8 s/帧@4090 | 【二手】 | Azure 目录，本轮正文截断 |
| OmniParser 官方 logs：text 58.89% / icon 4.65%，55/139=39.57% | 【一手·上轮抓，本轮未复核】 | 文件 `eval/logs_sspro_omniv2.json` |
| OmniParser 论文：直出 xy 16.2 / Desktop Icon 11.8 → 编号式 73.0 / 63.6 | 【一手·论文 Table】 | arXiv 2408.00203 |
| UFO² `CONTROL_FILTER_TOP_K_ICON: 15` + `clip-ViT-B-32` + CLIP 预筛输入是 UIA 框裁图 | 【一手·本仓库已抓官方源码】 | `config/ufo/system.yaml`、`control_filter.py` L250–272 |
| UFO² Table 3：UIA 37.3 / OmniParser 127.3 / 合并 97.9；27.9% & 56.7% 检出因重叠被弃 | 【一手·本仓库已抓论文】 | arXiv 2504.14603 |
| UFO² §3.4 / §6.3 正文与 CLIP 消融数字 | **未取到** | arXiv HTML 抓取在 §3.3 截断 |
| ScreenSpot-Pro：1581 条、text 62.6%/icon 37.4%、icon=无文字提示、目标 0.07% 屏面积、18.9%→48.1%（ScreenSeekeR）、min 1280px、σ=0.3 | 【一手】 | 本轮抓到论文正文 |
| Grounding DINO：48.4/56.7 COCO zero-shot AP、box_threshold 0.35 / text_threshold 0.25、900 框上限、支持 CPU-only | 【一手】 | README 全文 |
| Grounding DINO 许可 Apache-2.0、延迟 0.3s@A100、~690MB | 【二手】 | 本轮未抓 LICENSE / 延迟表 |
| SoM：RefCOCOg 25.7%→86.4%、COCO 75.7%、ADE20K 63.4% | 【二手】 | alphaxiv 摘要 |
| SoM 在 GUI 上无公开 ScreenSpot(-Pro) 复现数 | 【一手·检索结论】 | 两轮检索 |
| "Iconeek" 无同名公开项目 | 【一手·检索结论】 | 两轮检索 |
| UGround-V1 ScreenSpot-Pro 31.1%、UGround-V1-2B 存在、Apache-2.0 | 【二手】 | 未抓一手 |
| 许可干净替代品的体积/延迟（Florence-2 / MobileSAM / EfficientSAM / SAM2 / PP-YOLOE / YOLOv9-E） | 【二手】+【推断】 | 本轮未抓；体积换算为推断 |
| ScreenSeekeR 式两段式可落在我们的 pipeline 上 | 【推断】 | 基于论文机制 + 我们现有 `ground/` 模块 |

## 10. 未解决 / 需要下一轮补的

1. **UFO² §3.4 正文 + §6.3 表**：CLIP 预筛对成功率的**消融数字**未知（本轮截断）→ 若要引用"CLIP 预筛带来多少收益"，必须先补抓 `arxiv.org/html/2504.14603v1` 的 §3.4/§6.3 或论文 PDF。
2. **OmniParser 官方 logs 的 icon 4.65%** 需复核（本轮只拿到 Evaluation.md 的文件说明，未取 JSON）。
3. **`icon_detect_v3` 的上游 MIT fork 是哪一个**（Ultralytics 官方 YOLOv9 为 AGPL-3.0）→ 需向 OmniParser / 上游索取 LICENSE 文件。
4. **我们自己的 icon 候选召回基线**从未量化（只有 OCR 词级 5.8%）→ 改动 #4 落地后才有验收数字。
5. **ScreenSeekeR 的候选区域提示词**（论文 Appendix C）本轮未取到 → 改动 #5 要写提示词时需要补抓。

# R1 线报告：我们的「视觉通道落点验收」口径该怎么定

> 调研线：落点验收口径（bbox 命中 / 20 px 欧氏 / ZoomClick 扫描阈值 / WCAG 24×24）
> 日期：2026-10-22
> web 调用：13 次（11 次唯一调用 + 2 次超时后的合规重试；一次 PDF 抓取因 `unsupported content type "application/pdf"` 失败，已改用可打开的 HTML/原始源码替代）
> 证据强度标注：【一手】= 官方源码/官方文档/论文原文（我直接打开了该 URL）；【二手】= 论文元数据/检索摘要/他人转述（原始文件我没能打开）；【推断】= 我基于一手材料 + 本仓库代码事实做的推导

---

## 0. 一句话结论

**把「≤2 px」从「视觉通道落点的验收线」上撤下来，改成「bbox 命中（口径 = ScreenSpot-Pro 官方脚本）+ 一个只作诊断分档的近失带」。**

理由有三条，全部可验证：

1. **我们的 ≤2 px 线在数学上不可能通过它自己要验收的东西。** 我们自己量过：找对元素的子集（n=76）落点误差中位 **8.9 px**，全体 183 条里落在 `<2 px` 档的只有 **6 条（3.3%）**。也就是说 ≥50% 的「模型确实指对了元素」的落点会被 ≤2 px 判失败。**我们现在的验收线是在专门淘汰答对的样本。**
2. **我们离线的 44.8% 早就是 bbox 命中口径了**（`src/minicode/eval/sspro.py:498` → `SCREENSPOT_PRO.hit` → 官方 `eval_screenspot_pro.py` 的 `eval_sample_positive_gt`）。所以「≤2 px」**不是**我们对外报 44.8% 的口径 —— 它活在另外两个地方：真机 e2e 验收条款（`e2e/wave1.py:38`、`e2e/wave2.py:55` 的 `LANDING_TOLERANCE_PX = 2.0`）和探针自检脚本（`verify/test_01_text_and_probe.py:124` 的 `PASS (<=2px)`）。**该改的是这两处，不是评测台。**
3. **业界没有任何一套口径用绝对像素容差判「点得准不准」。** 官方脚本判的是「点在不在目标框里」（bbox 命中）；A11y-Compressor 的 **20.0 px 根本不是验收容差**（见 §1.2，这是本轮最重要的一处更正）；ZoomClick 的阈值是**行为一致性**阈值且是**扫出来的**、不是抄来的常数；WCAG 2.2 的 24×24 是**目标尺寸/间距**标准，与落点精度无关。人因侧（Fitts / Wobbrock 误差模型）也只谈「给定目标宽度下的错误率」，不谈绝对像素容差。

---

## 1. 四种口径的原始出处与适用场合

### 1.1 ScreenSpot / ScreenSpot-Pro：**bbox 命中**（判定「点没点到目标」）

**【一手】原始出处**：`https://github.com/likaixin2000/ScreenSpot-Pro-GUI-Grounding/blob/main/eval_screenspot_pro.py`
（我打开了 raw 版本：`https://raw.githubusercontent.com/likaixin2000/ScreenSpot-Pro-GUI-Grounding/main/eval_screenspot_pro.py`）

核心判定（原文照抄）：

```python
def eval_sample_positive_gt(sample, response):
    bbox = sample["bbox"]                       # [x1, y1, x2, y2]
    img_size = sample["img_size"]
    bbox = [bbox[0]/img_size[0], bbox[1]/img_size[1],
            bbox[2]/img_size[0], bbox[3]/img_size[1]]   # 归一化
    click_point = response["point"]
    if click_point is None:
        return "wrong_format"
    # Check if the predicted point falls in the ground truth box
    if (bbox[0] <= click_point[0] <= bbox[2]) and (bbox[1] <= click_point[1] <= bbox[3]):
        return "correct"
    else:
        return "wrong"
```

- 指标名：脚本输出 `action_acc`（总体）/ `text_acc` / `icon_acc`（按 `ui_type` 分）。
- **适用场合**：静态截图上的 **grounding**（找元素），不是端到端任务成功率。
- **为什么它是「事实上的行业口径」**：所有 ScreenSpot 排行榜数字（UI-TARS-72B 37.1~38.1、GUI-Actor-7B 40.7/44.6、RegionFocus 61.6、我们的 44.8%）算的都是这个。**我们要跟这些数对话，就必须是这个口径。**
- 我们仓库已经**精确复刻**了它：`src/minicode/eval/formats.py` 的 `SCREENSPOT_PRO` wrapper + `point_in_bbox_xyxy`，`sspro.py:168` 自己写着「判定口径：官方 eval_screenspot_pro.py（归一化点落在归一化 bbox 内）」，还有 `self_check_hit_rate` 防假象自检（拿 GT 中心喂自己，命中率必须≈100%）。

### 1.2 A11y-Compressor：**20.0 px 不是验收容差**（本轮更正）

**【一手】我直接打开了论文全文 HTML**：`https://arxiv.org/html/2605.00551v1`
（ACL Anthology 版：`https://aclanthology.org/2026.acl-srw.50/`；项目页 `https://iyatomilab.github.io/a11y-compressor/`）

**结论：这个口径对我们不适用，而且「A11y-Compressor 在 OSWorld 上用 20.0 px 欧氏距离」这个前提本身是错的。**

论文 §4.4 *Evaluation Metrics* 原文列的评测口径只有三条：

> **Success Rate (SR).** SR is defined as the proportion of tasks successfully completed within a maximum of 15 interaction steps. To mitigate the impact of non-deterministic factors in the OSWorld environment, such as system response delays and variability in MLLM outputs, we conduct **two trials for each task and consider a task successful if at least one trial succeeds.**
> **Average Input Tokens.** …
> **Case Study.** …

**论文里没有任何像素级落点精度指标。** 它的评测是**执行结果导向**的任务成功率（OSWorld 358 条任务，Qwen3-VL-32B，15 步上限，2 次取 1）。它自己报的数（论文 Table 1）：

| 方法 | Chrome | GIMP | Calc | Impress | Writer | Multi Apps | OS | Thunderbird | VLC | VS Code | **Overall** |
|---|---|---|---|---|---|---|---|---|---|---|---|
| Screenshot | .045 | .115 | .000 | .021 | .043 | .108 | .208 | .000 | .118 | .043 | **.070** |
| Linearized a11y tree | .182 | .192 | .000 | .149 | .087 | .108 | .333 | .267 | .294 | .304 | **.156** |
| LineRetriever | .136 | .192 | .022 | .191 | .087 | .108 | .333 | .133 | .176 | .348 | **.151** |
| **Compressed-a11y（本文）** | .250 | .231 | .043 | .191 | .304 | .108 | .375 | .467 | .294 | .348 | **.207** |

（token 压到原来的 22%，成功率 +5.1 pp）

**那 20.0 px 出现在哪？** 在附录 **B.2 Spatial Clustering of Anchor Elements** —— 观测压缩流水线里，判定两个 a11y-tree 锚点元素「位置接近、应当聚成一组（构造 modal candidate region）」的**空间聚类邻近阈值**。ACL PDF 原文（【二手】，我未能打开 PDF，但两处独立检索给出同一句原文）：

> "If this distance is within a predefined threshold (default: **20.0 pixels**), the elements are judged to be in close proximity. Exception: If the name [of the element] …"

即：它是**「这两个控件是不是挨着」的几何常数**（用来避免把相邻控件错并成一块），**跟「模型点得准不准」毫无关系**。把它当验收容差是张冠李戴。

**顺带一个与我们直接相关的旁证**（【一手】，同篇 §3.2 原文）：

> "The linearized a11y tree represents each element using its **top-left coordinate and bounding box size**; however, **the top-left coordinate does not always correspond to an effective interaction point**. To simplify spatial reasoning, this phase converts bounding box representations into **center coordinates**."

—— 同一篇论文也认为「框的中心」才是好用的交互点，而「左上角坐标」不是。这跟我们 M28 的结论方向一致。

**判定**：**不适用**。不能拿 20 px 当我们的落点验收线，也不能拿它当「业界阈值」的依据。

### 1.3 ZoomClick：**扫「最好把正确对与错误对分开」的像素阈值**（而且是行为一致性阈值）

**【一手】原始出处**：官方仓库 README（我打开了 raw：`https://raw.githubusercontent.com/Princeton-AI2-Lab/ZoomClick/main/README.md`）；论文 `https://arxiv.org/html/2512.05941`（arXiv:2512.05941, Princeton AI2 Lab, 2025-12-05）。

README 里官方评测命令行【一手】：

```
python grounding/eval_sspro_zoomclick.py \
    --backend uivenus --model_name_or_path "${MODEL_DIR}" \
    --in_depth 3 --in_ratio 0.5 --in_min_crop 768 \
    --patch_size 2 --center_mode "clip" \
    --prezoom_px_thresh 50
```

- `--prezoom_px_thresh`：README 原文 *"Threshold of pixel distance used in Pre-zoom. Refer to our paper for more details."* → **默认 50**。
- 论文 §6 原文（【二手】，ResearchGate/arXiv HTML 检索片段，两处一致）：
  > "We report the **best pixel threshold that separates correct and error pairs**, along with the resulting accuracy."
- 头条数字（【一手】README）：UI-Venus-72B + ZoomClick 在 ScreenSpot-Pro 上 **73.1%**。

**适用场合 —— 这条最重要，很多人会搞错：**
ZoomClick 的这个像素阈值**不是「落点到 GT 的距离容差」**，而是**行为一致性判据**：比较模型在「放大前」和「放大后」两次点击的像素距离，距离小 ⇒ 模型行为稳定 ⇒ 判为可信；距离大 ⇒ 判为可疑。它是**无标注**的自评信号，评的是「模型有没有被 zoom 带跑」，不是「点得准不准」。

**因此真正可借鉴的是它的「方法」而不是那个数**：
> 阈值不是拍脑袋定的常数，而是**在一批样本上扫出来、挑那个最能分开「正确对」与「错误对」的值**，并连同该值下的准确率一起报。

这个方法我们已经在另一处用过（偏离回落阈值 t：曲线 80~∞ 是平台、峰值在 t=96 47.71%）——**同一套方法应该再用到「落点验收阈值」上**。

**一个具体的单位错误（顺手记下，属于 r4 线但引用源在我这条线）**：
我们 `src/minicode/ground/gate.py` 的 `DEFAULT_DEVIATION_SOURCE` 写着 50 px 来自「ZoomClick 的 ε₁×1000/crop_width 换算式」。**【推断】+【一手】证据表明这个换算不该做**：README 里 `--prezoom_px_thresh 50` 是与 `--in_min_crop 768 --in_ratio 0.5` 并列的**绝对像素距离**，作用在**原图像素空间**；论文把 crop 宽代入 1000 网格是**另一件事**（模型侧坐标归一化）。两者差了一个 1000/crop_width 的因子（crop=512 时 1.95 倍，crop=192 时 5.2 倍）。这条建议交给 r4 线复核，但引用源由我这条线提供。

### 1.4 WCAG 2.2 SC 2.5.8 Target Size (Minimum)：**24×24 CSS px 是「目标多大/间隔多远」，不是「点得准不准」**

**【一手】**我打开了 W3C Understanding 全文：`https://www.w3.org/WAI/WCAG22/Understanding/target-size-minimum.html`（规范正文 `https://www.w3.org/TR/WCAG22/#target-size-minimum`）

- SC 2.5.8（Level AA，WCAG 2.2 新增）原文：*"The size of the target for pointer inputs is at least **24 by 24 CSS pixels**, except when: Spacing / Equivalent / Inline / User Agent Control / Essential."*
- **Spacing 例外的真实几何**：以每个欠尺寸目标的**包围盒中心**为圆心画一个 **24 CSS px 直径的圆**，这些圆彼此不相交、也不与别的目标相交，才算通过。
- **关键的 Note 1（对 (d) 直接有用）**：
  > "Targets that allow for values to be selected **spatially based on position within the target** are considered one target for the purpose of the success criterion. Examples include **sliders, color pickers displaying a gradient of colors, or editable areas where you position the cursor**."
  → **位置本身携带语义的控件（滑块、色盘、需在区域内定位的可编辑区）是特例**；反过来说，普通按钮**不在这条例外里**。
- **Intent 段落的原话（人因口径的核心）**：
  > "The intent of this success criterion is to help ensure targets can be easily activated **without accidentally activating the adjacent target**. … Providing sufficient size, or sufficient spacing between targets, will reduce the likelihood of **accidentally activating the wrong control**."
  > "…it is also useful for mouse or pen users. It reduces the chances of **erroneous activation** due to either a tremor or reduced precision."
  → **人因关心的失败模式是「激活了错误的控件」，不是「没点到控件的正中心」。**
- CSS px ≠ 设备像素：*"The requirement is independent of the zoom factor of the page"*。
- 相关的 AAA 级 SC 2.5.5 Target Size (Enhanced) = **44×44 CSS px**。
- 相关资源里 W3C 自己引的是触屏一手研究：<https://dl.acm.org/doi/10.1145/1152215.1152260>（Target size study for one-handed thumb use on small touchscreen devices）。

**换算到本机（【推断】，可自洽验算）**：本机 2560×1600 物理、150% 系统缩放、逻辑 1707×1067（2560/1.5 = 1706.7 ✓，1600/1.5 = 1066.7 ✓）。Windows 1 DIP(=1 CSS px) = 1.5 物理 px →
**24 CSS px = 36 物理 px**；**44 CSS px = 66 物理 px**。

顺带一个对我们直接不利的数：我们自己的量测里 **icon 目标原图边长中位 28~29 物理 px**（≈19 CSS px）——**低于 WCAG 2.2 的 24 CSS px 最低目标尺寸**。这直接证明：**绝对像素阈值对 icon 毫无意义（图标本身才 28 px 宽，一个「≤2 px」的要求相当于要求 1/14 宽度的精度），任何阈值必须相对目标自身尺寸来定。**

### 1.5 人因侧：Fitts 定律与指向误差模型（**没有任何绝对像素容差**）

- Wobbrock, Cutrell & Russell, CHI 2008, *"An Error Model for Pointing Based on Fitts' Law"*：`https://faculty.washington.edu/wobbrock/pubs/chi-08.01.pdf`（**【二手】**：抓取返回 `unsupported content type "application/pdf"`，我没能打开原文，用检索摘要代替）；Microsoft Research 页 `https://www.microsoft.com/en-us/research/publication/an-error-model-for-pointing-based-on-fitts-law/`。
  - 要点：**Fitts 定律数学上蕴含一个错误率模型** —— 由目标宽度 W、距离 D 推出命中率/错误率；有效宽度采用标准差约定 **W_eff = 4σ（覆盖约 95.4% 的落点分布）**；ID = log₂(2D/W)。
  - 文献里有个惯例值得警惕：**「事后按 4% 错误率反推有效宽度 We」**（Wobbrock 2007 `https://faculty.washington.edu/wobbrock/pubs/assets-07.01.pdf` 批评这种做法统计上站不住）。
- MacKenzie 1992, *"Fitts' Law as a Research and Design Tool in HCI"*（HCI journal）：`https://www.billbuxton.com/fitts91.html`（【二手】）—— 指向（pointing）的错误率低于拖拽（dragging）；鼠标在速度与精度上都优于其他指点设备。
- ISO 9241-9 / ISO 9241-411 的 **2D 多方向敲击测试**（10 个不同宽度/距离的目标）是标准化测法。

**判定**：人因文献的通用表达是「**给定目标宽度下的错误率**」，而不是「落点距离 ≤ N 像素」。这与我们 §2 的结论一致：**要相对目标尺寸，不要绝对像素。**

### 1.6 候选补充口径：EACL 2026 Findings 的「距离阈值」类指标

**【二手】** *Do GUI Grounders Truly Understand UI Elements?*，S. Jandial 等，Findings of EACL 2026：`https://aclanthology.org/2026.findings-eacl.144/`（PDF 镜像 `https://pdfs.semanticscholar.org/8c9c/cbc11ce0ffc66bb5a6424b1bec5360d90780.pdf`）。它提出的正是 **bbox 命中的已知缺陷**：大框命中太宽松，因此引入**距离阈值 d_th**（点到 GT 中心/边缘的欧氏距离）作为补充。

**我没有取到 d_th 的确切取值与公式**（PDF 抓取同样受阻，检索摘要也没暴露数值）→ 标【二手】，**不作为我们定阈值的依据**，只作为「bbox 命中 + 距离阈值」这个**双轨结构有同行评议背书**的佐证。我们的做法可以更直接：在自己 183 条数据上按 ZoomClick 的方法扫出最佳分离点（我们已经在偏离阈值上做过一次同类扫描）。

---

## 2. (b) 我们该用哪种口径验收「视觉通道落点」

### 2.1 我们的数据形状：双峰，且两峰被 bbox 天然劈开

我们自己的量测（`docs/research/vision-root-cause.md` §2）：找对元素 n=76 / 挑错元素 n=107。

| 子集 | 落点误差中位 | \|dx\| 中位 | **bbox 命中率** |
|---|---|---|---|
| 找对元素（n=76） | **8.9 px** | 8.5 | **92.1%** |
| 挑错元素（n=107） | **226 px** | 114 | **11.2%** |

**这是选口径的决定性证据**：

- **bbox 命中**在两个峰之间给出的区分度是 **92.1% vs 11.2%** —— 一个二值口径就把「找对了」和「挑错了」几乎完全分开了，因为它**按目标自身的尺寸自适应**：8.9 px 的误差落在 icon 框内（icon 28~29 px 宽），226 px 的误差落在框外。
- **绝对像素阈值做不到这件事**：同一个 ≤N px 阈值，作用在 28 px 宽的 icon 上和作用在几百 px 宽的 text 目标上，语义完全不同。icon 那侧 N 必须小到 2~4 px，text 那侧 N 可以放到几十 px —— **没有统一的 N**。
- **≤2 px 更糟**：它落在 8.9 px 中位的**一半以下**，会把「模型答对了」的样本大批判失败（6/183 = 3.3% 的样本能过）。

### 2.2 为什么 bbox 命中在「能不能用」的意义上是对的

- **它和真实点击语义一致**：Windows/UIA 里，按钮被点击生效的判定就是「落点落在控件矩形内」。**我们仓库自己已经这么写了** —— `src/minicode/e2e/apps.py::_landing_error_px` 的 docstring：
  > "落点与**目标矩形**的偏差：落在里面 = 0 px，落在外面 = 到最近边的距离。… **「落点落在目标矩形里」**这条仍然成立，且才是这里有信息量的检查。"
  同文件 M28 的说明：「落点必须落在元素矩形里面」这条**才**是有信息量的检查。
  **即：≤2 px 的中心距离口径是 M28 之前的旧判据残留，浏览器通道已经改掉了，真机通道还没改。**
- **它和 OSWorld 一类端到端基准一致**（【二手】，OSWorld 论文 `https://arxiv.org/html/2404.07972v2`）：任务成功与否看**执行后的应用状态**，不看点击落在框内哪个子像素。A11y-Compressor 的 SR 定义（§1.2【一手】）同构。
- **它让我们的数字可对外比较**：公开基线（UI-TARS 37.1~38.1、GUI-Actor 40.7/44.6、RegionFocus 61.6）全用这个口径。

### 2.3 单独保留「距离」的必要性：连续量 + 位置敏感控件

bbox 命中有一个已知弱点（EACL 2026 那篇论文批评的正是它）：**大框太宽松**。所以我们**不能只留一个二值数**，必须同时留一个连续量：

- 连续量取**「到目标 bbox 的距离」**（落在框内记 0），**不是到中心的距离**。这与 `apps.py::_landing_error_px` 已有实现一致，语义也正好对应「差多少才真的落到别的东西上」。
- 报告形态建议：`hit / 命中` + `dist_to_bbox` 的 **median、p90**（按 text/icon 分层）—— 与我们现有的分层报告习惯一致。
- **位置敏感控件单独加一条**（WCAG Note 1 的直接落点）：滑块 / 色盘 / 需要在区域内定位的可编辑区 / 拖拽把手 / 滚动条滑块 —— 这些「框内但位置不对」就是错。对这类目标，「命中矩形」不够，要判「命中矩形的哪个子区域」。

### 2.4 (c) 结论：≤2 px 该不该改，怎么改

**该改。** 具体形态（**bbox 命中为主 + 一个只作诊断、不作判死的近失带**）：

| 档 | 判据（真机 e2e，物理像素） | 含义 | 是否判失败 |
|---|---|---|---|
| **A 命中** | 落点 ∈ 目标控件矩形 | 点到了目标 | 否 ✅ |
| **B 近失** | 落点 ∉ 矩形，但**到矩形边界的距离 ≤ 36 px**（= 24 CSS px @150%） | 可能激活了相邻控件（W3C 关心的那一档） | 否 ⚠️ 记 MARGINAL |
| **C 错选** | 距离 > 36 px | 点到了别的东西 | 是 ❌ |

- **36 px 的依据**：WCAG 2.2 SC 2.5.8 的 24 CSS px（间距例外用的正是 24 CSS px 直径圆）在本机 150% DPI 下 = 36 物理 px。它的语义正好是「近到可能激活了隔壁控件」的那一圈 —— **和 C 档的失败定义对齐**。
- **B 档只作诊断分档，不作判死**。理由：这一档的失败模式是「可能点到邻居」，而不是「点歪了」；判死会让验收线重新变成一个和目标尺寸无关的绝对数（正是 ≤2 px 的病根）。
- **不要给 B 档再叠一个「必须命中」的绝对要求** —— 命中与否由 A 档判，B 档只提供「离边界多近」这一连续信息。

### 2.5 (d) 「点偏了但仍在目标按钮内」算不算失败

**不算失败**（对普通按钮）；**算失败**（对位置敏感控件）。三条独立依据：

1. **操作系统语义【一手，本仓库】**：Windows 里按钮在矩形内被点即触发；我们的 `apps.py::_landing_error_px` 已经把这条写成唯一有信息量的检查，且 M28 已经把浏览器通道从「中心距离」改成「落在矩形里」。**真机通道（`e2e/real.py::_resolve_truth` 仍返回**中心点**、`landing_error_px` 仍是到中心的距离）是漏改的那一处。**
2. **WCAG 2.2 意图【一手】**：该准则的 purpose 是「**不误激活相邻目标**」，以及「减少因手抖/精度不足导致的**错误激活**」。**没有一条要求点到目标中心。** 反过来，Note 1 明文把「值取决于框内位置」的控件列为特例 —— 滑块、色盘、需在区域内定位的可编辑区。
3. **人因/模型侧【二手】**：Fitts/Wobbrock 误差模型把「给定目标宽度下的错误率」当量；OSWorld 类端到端基准按执行后的状态判成败；EACL 2026 那篇甚至认为 bbox 命中太宽松、该加距离阈值 —— 但它加的是**「离目标多远」**的信息，不是「必须等于中心」。

**落地成验收语言**：
- 普通按钮（打开文件、切换开关、点菜单项）：**落点在矩形内 → 通过**，与框内偏移多少无关。
- 位置敏感控件（滑块、色盘、可编辑区定位、滚动条滑块）：**矩形内还要再判位置**；这一类单独一条条款，不混进通用条款。
- **相邻控件误激活**：如果落点在 A 档，但 A 的矩形与另一个可点控件的矩形相交/贴得极近 → 记 B 档（近失）并提示「可能激活了邻居」，但不判死；真要防误激活属于 UI 设计问题，不是我们落点精度的问题。

---

## 3. 我们现在代码里的落点验收现状（本地核查，非推测）

| 位置 | 现状 | 口径 | 是否要改 |
|---|---|---|---|
| `src/minicode/eval/sspro.py:498` | `hit = SCREENSPOT_PRO.hit(prediction, sample.as_eval_sample())` | **官方 bbox 命中** | ❌ 不改（已经是行业口径，44.8% 就是它算的） |
| `src/minicode/eval/formats.py` | `SCREENSPOT_PRO` / `ZOOMCLICK` / `CCF_SCREENSPOT_V2` / `SHOWUI` 四个 wrapper + `self_check_hit_rate` | 官方脚本逐个复刻 | ❌ 不改（可加连续量） |
| `src/minicode/e2e/wave1.py:38`、`wave2.py:55` | `LANDING_TOLERANCE_PX = 2.0` | **到控件中心的绝对距离** | ✅ **主战场** |
| `src/minicode/e2e/checks.py:90 landing_within` | 拿 `landing_error_px` 与上限比 | 同上 | ✅ 加 bbox 命中版条款 |
| `src/minicode/e2e/real.py:489 _resolve_truth` | 树里查到控件框后**只返回中心点** `((l+r)//2, (t+b)//2)` | 中心点真值 | ✅ 改成携带矩形 |
| `src/minicode/e2e/tasks.py:72 truth` | `tuple[int,int] | None` 中心点 | 中心点真值 | ✅ 加矩形真值 |
| `src/minicode/e2e/apps.py:115 _landing_error_px` | 到矩形的距离（框内=0） | **已是 bbox 语义** | ✅ 作为范本，复用到真机 |
| `src/minicode/verify/test_01_text_and_probe.py:124` | `PASS (<=2px)` / `MARGINAL (<=20)` / `FAIL` | 中心点绝对距离 | ✅ 与 `calib/probe.py:probe_hit_radius`（96 方块 → 48）自相矛盾 |
| `src/minicode/calib/tolerance.py` | 1.0 标定容差 / 2.0 越界容差 / 4.0 落点等价容差 / 48 命中半径 | 各管一件事，**不共用** | ❌ 不改（1 px 只管可分辨性，与落点精度无关，已签字） |

**关键发现（必须写下来）**：我们的**离线评测台口径是对的**（bbox 命中、与公开基线同口径），**真机 e2e 条款和探针自检脚本是残留的旧口径**。所以这次改动的收益 **不是** 抬高 44.8%（那个数不会动），而是**让真机视觉通道第一次有一个判得了、且判得对的验收条款** —— 正好对应 `vision-root-cause.md` 的 P3-10「把『视觉通道真机落点精度』从『零证据』变成『有验收条款可判』」。

---

## 4. ≤8 条最小改动清单

按投入产出排序。每条：**改哪个文件 / 做什么 / 预期哪个数变多少**。

1. **`src/minicode/e2e/wave1.py:38` + `wave2.py:55`** —— 把 `LANDING_TOLERANCE_PX = 2.0` 换成两个常量：`LANDING_HIT_RULE = "bbox"`（主判据）+ `LANDING_NEAR_MISS_PX = 36.0`（近失带，物理像素，= 24 CSS px @150%）。**预期**：真机 e2e 条款从「几乎永远判失败」变成「A 命中 / B 近失 / C 错选」三档；44.8% 不动。
2. **`src/minicode/e2e/checks.py`** —— 新增 `landing_in_target_rect(evidence)`（bbox 命中，失败时写明落点与目标矩形），保留 `landing_within` 仅供**探针/标定**用途，并在 docstring 里注明「容差」按 CONTEXT.md 的 avoid 规则改称「上限/命中半径」。**预期**：wave1/wave2 的 `landing_within(LANDING_TOLERANCE_PX)` 两处调用换成新条款；条款数 +1，可判率从 0 变成可判。
3. **`src/minicode/e2e/tasks.py` + `e2e/real.py:489 _resolve_truth`** —— 真值从「只有中心点」扩成「中心点 + 控件矩形」（`truth_rect: tuple[int,int,int,int] | None`；树里查到 `element.rect` 时一并带出，`_resolve_truth` 已持有该 rect）。**预期**：第 1、2 条可实现；落点偏差 `landing_error_px` 语义从「到中心」改为「到矩形（框内=0）」，与 `apps.py::_landing_error_px` 统一。**注意：这一步会让历史记录里的 `landing_error_px` 不可比（换算法），需在报告里标注口径版本。**
4. **`src/minicode/verify/test_01_text_and_probe.py:124`** —— PASS/MARGINAL 阈值从 `(2, 20)` 改为与 `calib/probe.py::probe_hit_radius` 一致的「落在方块内 / 出界但 ≤ 方块半宽」；PASS 判据改为「全部目标命中」而非「最大误差 ≤2 px」。**预期**：探针 PASS 从「几乎不可能」变成模型真实水平（标准探针命中半径 48 px 的量级）。
5. **`src/minicode/eval/sspro.py`** —— 每条样本的记录里加一个连续量 `dist_to_bbox`（落点到 GT bbox 的距离，框内=0），与已有 `hit` 并列落盘。**预期**：44.8% **不变**；产出可做「扫阈值找最佳分离点」（ZoomClick 方法）的原始数据，以及 text/icon 分层的 median/p90。
6. **`src/minicode/eval/holdout.py`（或离线重评脚本）** —— 用第 5 条的数据扫一遍落点距离阈值 τ，报告「τ = 5/10/20/50/100 px 下的命中率」与最佳分离点。**预期**：得到我们自己的、与 226 px 错选峰分离的近失带实测值，用来校验第 1 条里的 36 px 是保守还是偏松（预计 36 px 偏保守，会落在 8.9 px 与 226 px 两峰之间的空白区，安全）。**不追求把 44.8% 抬高 —— 抬高它不是这一轮的目标。**
7. **`CONTEXT.md`** —— 新增术语「**落点命中**（落在目标矩形内，bbox 命中；口径同 ScreenSpot-Pro 官方脚本）」与「**近失带**（到目标矩形边界 ≤ N 物理像素）」，并把「≤2 px 落点验收」列入 avoid。**预期**：0 个数字变化；防止下一轮又有人把 2 px 抄回来当落点精度线。
8. **`src/minicode/ground/gate.py` 的 `DEFAULT_DEVIATION_SOURCE`** —— 改掉「ZoomClick ε₁×1000/crop_width」的换算说法（README 里 `--prezoom_px_thresh 50` 是原图绝对像素，不除 crop 宽）。**预期**：0 个数字变化（这是引用修正，阈值本身由 r4 线复核）；防止我们把一个错的换算式当依据。

---

## 5. 未完成 / 存疑（明确标出，不留白）

- **ZoomClick §6 那张「best pixel threshold」表的逐行数值我没有取到**（论文 HTML 全表未打开；仓库 `results/sspro/*.json` 未逐个下载）。我们引用的是它的**方法**与官方默认 `--prezoom_px_thresh 50`【一手】。如果 r4 线要引用具体数值，需要单独开一次 arXiv HTML 抓取。
- **EACL 2026 Findings 的 d_th 取值与公式未取到**（PDF 抓取受阻）→ 仅作「双轨结构有同行评议背书」的旁证，不作为定阈依据。
- **Wobbrock CHI 2008 原文未打开**（`web_fetch` 对 PDF 返回 unsupported content type）→ 人因数字全部标【二手】；但「人因谈的是给定目标宽度下的错误率，不是绝对像素容差」这个结论由该论文的摘要性描述 + WCAG 一手原文共同支撑，结论不依赖 PDF 细节。
- **OSWorld 官方 evaluator 源码未打开**（`desktop_env/evaluators/metrics.py` 两次 404 / 超时，实际路径未定位）→ 「OSWorld 按执行后状态判成败」标【二手】（论文 `https://arxiv.org/html/2404.07972v2` + A11y-Compressor §4.4 一手交叉印证）。
- **36 px 近失带的值**目前是「WCAG 24 CSS px @150%」的**外部锚定**，尚未在我们自己的 183 条上验证（见改动清单第 6 条）。这不阻塞条款落地（第 6 条是事后校准，不改判据方向）。
- **真机上「相邻控件」的矩形我们目前拿不到**：UIA 通道能拿到目标控件矩形，但「有没有挨着的另一个可点控件」需要遍历同层候选。若要把 B 档做实，需要在 `_resolve_truth` 里多取一次同层元素列表 —— 未验证可行性。

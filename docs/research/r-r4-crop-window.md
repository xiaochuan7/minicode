# R4 · crop 窗口怎么保证框住目标？

> 调研线 R4 | 产物：`docs/research/r-r4-crop-window.md`
> 证据强度标注：**【一手】**= 官方源码/论文原文/官方仓原文；**【二手】**= 检索摘要转述、第三方复述；**【推断】**= 我基于一手事实的推导（可被单次实验证伪）。
> 本轮联网工具调用 13 次（含 1 次超时 + 1 次重试），未超预算上限；全部来源 URL 可点开。

---

## 0. 一句话结论

**把「固定 192 px 按点裁」改成「官方同款：以粗点为中心、窗口尺寸 = 当前图的固定比例（官方 Iterative Narrowing 用 ±0.25 归一化即半宽半高；ZoomClick 用 shrink=0.5 且下限 768 px），迭代 3 轮收窄，并按官方 ReGround 的 1024×1024 量级定第一级窗口」** —— 官方 ScreenSpot-Pro 的四种 coarse-to-fine 实现里，**没有任何一种用「小于 1024 px 的固定小窗」**，最好的两个（ReGround 40.2、ScreenSeekeR 48.1）用的分别是 1024×1024 和 min_crop_size=[1280,768]；ZoomClick 把窗口做成「随图缩、但有 768 px 下限 + 2×2 预放大同意检验」，UI-Venus-72B 在 ScreenSpot-Pro 上做到 **73.1%**【一手】。我们的 192 px 窗口在 2560×1600 屏上只容忍 **±96 px** 的粗点误差，而官方口径容忍 **±512～640 px**。

---

## 1. (a) 官方 coarse-to-fine 到底怎么裁 —— 一手源码 + 论文原文

### 1.1 论文层（ScreenSpot-Pro, arXiv:2504.07981v1, §4.1「Planner-Free Methods」）

| 方法 | 裁剪方式（论文原文） | 出处 |
|---|---|---|
| Iterative Zooming | "first performs grounding directly on the whole screenshot, and splits the screenshot into smaller patches. At each step, it chooses the patch the prediction falls into to continue searching within. We always use a **2 row × 2 column** splitting strategy." | [arXiv HTML §4.1](https://arxiv.org/html/2504.07981v1)【一手】 |
| **Iterative Narrowing** | "the patches are **cropped to center the prediction**. The patch size is set to **half the width and height of the image at each step**." | 同上【一手】 |
| **ReGround** | "**crops the region surrounding the initial prediction** to re-ground and make a final determination. The size of the crop can be manually configured based on the optimal input size of the models." | 同上【一手】 |
| ScreenSeekeR（§4.2） | planner(GPT-4o) 提议候选区域 → "The grounded bounding boxes are often noisy, so we apply **box dilation** to expand smaller ones into larger candidate areas, **reducing the risk of missing the target**"；"The grounder model is invoked **if the patch size is sufficiently small (a hyperparameter set to 1280 pixels)**"；打分用中心性高斯 **σ=0.3** | 同上【一手】 |

论文总论断：**"Our experiments reveal that strategically reducing the search area enhances accuracy."**（同页 Abstract）【一手】
并给出总账：OS-Atlas-7B 端到端 **18.9%** → ScreenSeekeR **48.1%**（254% 相对提升）【一手】。

### 1.2 官方仓源码层（`likaixin2000/ScreenSpot-Pro-GUI-Grounding`）

仓树（GitHub API 一手核实，文件确实存在）：`models/methods/{reground.py, iterative_narrowing.py, iterative_focusing.py, screenseeker.py}`、`models/zoom_consistency_router.py`、`eval_screenspot_pro.py`【一手】
URL: https://github.com/likaixin2000/ScreenSpot-Pro-GUI-Grounding

**本轮新拿到的一手源码（`models/methods/iterative_focusing.py`，逐字）：**

- 裁剪区 = **当前视图的固定 0.5×0.5 归一化象限**，按 **bbox 中心**落在哪个象限来选：
  `areas = {"top_left": (0,0,0.5,0.5), "top_right": (0.5,0,1,0.5), "bottom_left": (0,0.5,0.5,1), "bottom_right": (0.5,0.5,1,1)}`，
  判定用 `view_center_x = (x1+x2)/2` 与 `if ax1 <= view_center_x < ax2 and ay1 <= view_center_y < ay2:`【一手】
  源：https://raw.githubusercontent.com/likaixin2000/ScreenSpot-Pro-GUI-Grounding/main/models/methods/iterative_focusing.py
- **padding = 0**：`_, image = crop_with_padding(image, target_area, padding=0)`；helper 文档串说 padding 是归一化尺度（0.1 = 加 10% 图宽），**此调用点未用**【一手】
- 越界处理：归一化先 `max(0.0, ...)` / `min(1.0, ...)` 夹紧再转像素 → **永不产生空白填充**【一手】
- 深度：`self.configs = {"max_search_depth": 3, "min_crop_size": 768}`；但 **`min_crop_size` 在整个文件里从未被读取**（死配置），实际生效的只有 `max_search_depth`【一手】
- 轮次语义：前 2 轮切分、第 3 轮在最后视图直接出结果 → **每样本 3 次 VLM 调用**，线性缩放 1× → 0.5× → 0.25×（4× 面积）【一手】
- 坐标回映射：**归一化仿射** `real = origin + size × view`（viewport 存 0~1 全图归一化，不是像素）【一手】
- **没有任何「crop 是否含住目标」的校验；没有回落到整屏；没有「保留整屏作第二视图」**【一手】
- 失败路径产出 "negative"（含 bbox 中心恰好落在 x=1.0 / y=1.0 的半开区间漏判）【一手】

**本仓此前已核、但本轮因网络超时未再复核的三条**（来源：本仓 `src/minicode/eval/sspro.py:159-217` 的 `OFFICIAL_ROWS`，已写明官方文件与行号；标注为 **【本仓已核·本次未复核】**）：

| 方法 | 裁剪参数 | 官方自报分（OS-Atlas-7B，1581 条，论文 Table 4） |
|---|---|---|
| ReGround `models/methods/reground.py:151` | **crop 1024×1024**（以初始预测为中心），整屏定位 + crop 内再定位各 1 次 | **40.2** |
| Iterative Narrowing `models/methods/iterative_narrowing.py:189-192` | `max_search_depth=3`，**以预测为中心 ±0.25 归一化裁剪**（= 当前图半宽半高） | **31.9** |
| Iterative Focusing `models/methods/iterative_focusing.py:156-161` | `max_search_depth=3`，2×2 象限切分取含预测的象限 | **31.0** |
| ScreenSeekeR `models/methods/screenseeker.py:456-467` | planner=gpt-4o；**min_crop_size=[1280,768]**、**max_aspect_ratio=2.5** | **48.1** |
| Zoom Consistency Router `models/zoom_consistency_router.py:43,180-203` | **CROP_RATIO=0.5**，2 步 zoom（第三方 PR，论文未评测） | 论文未评测，数字未取得 |

> 注：官方仓 `readme.md` **不含**这些方法的准确率数字，也不含 crop 尺寸 CLI 默认值（只说 `bash run_ss_pro.sh`）；`run_ss_pro.sh` 本轮 3 次连接超时，CLI 默认值**未核实**【一手（readme）/ 未核实（sh）】。

### 1.3 官方口径汇总（一表可抄）

| 维度 | 官方取值 | 来源 |
|---|---|---|
| 裁剪锚点 | **点**（ReGround/Narrowing）或 **bbox 中心**（Focusing）；**从不按 bbox 的尺寸裁** | 1.1 / 1.2【一手】 |
| 第一级窗口尺寸 | 1024×1024（ReGround）／当前图半宽半高（Narrowing）／固定 0.5 象限（Focusing）／min 1280×768（ScreenSeekeR） | 【一手】+【本仓已核】 |
| 迭代深度 | 3（Focusing、Narrowing） | 【一手】 |
| 每轮缩放 | 0.5（线性）/ 0.25（Focusing 两轮） | 【一手】 |
| padding | 0（Focusing）；ScreenSeekeR 走 **box dilation** | 【一手】 |
| 越界 | 归一化夹紧到 [0,1]，**不产生空白像素** | 【一手】 |
| 最小工作块 | 1280 px（ScreenSeekeR 调 grounder 的门槛）；768 px（ZoomClick 的下限） | 【一手】 |
| 含住目标与否 | **官方四种方法都不校验**；靠"大窗口 + 迭代 + 框膨胀"间接保证 | 【一手】 |
| 评测口径 | 预测点落在 GT bbox 内即算对；输出 bbox 的模型取其中心点 | 论文【一手】 |

---

## 2. (b) 按框裁 vs 按点裁 —— **公开对照数字不存在（不适用）**

**结论：没有任何公开论文做过「同一模型、同一提示词，只把裁剪锚点从预测框换成预测点」的对照实验**。检索到的全部是**输出形式**（point vs box）的对照，不是**裁剪锚点**的对照【一手（检索否定）+ 二手（各文说法）】。

可用的锚点：

| 证据 | 内容 | 强度 | URL |
|---|---|---|---|
| SeeClick (ACL 2024) | "SeeClick's predictions **with points were marginally better than bounding boxes**, thus we selected point prediction for final evaluation."（说的是**最终输出**形式） | 【一手·论文引文】 | https://aclanthology.org/2024.acl-long.505.pdf |
| ScreenSpot-Pro 官方评测 | "For models generating bounding box outputs, we calculate the **center point** as its prediction." | 【二手·论文引文转述】 | https://arxiv.org/html/2504.07981v1 |
| CogAgent | 裁剪区由一个**训练出来的 cropping module 回归 bbox** 决定（需要训练数据，我们没有）；裁剪从**原始高分辨率图**取，再与全局 token 做 cross-attention，并加 `cropping_pos` 位置编码 | 【二手】 | https://arxiv.org/html/2312.08914v3 |
| ScreenSeekeR（唯一「按框裁」且拿最高分） | 框来自 **planner（GPT-4o）** 而非 grounder，且强制 **box dilation**；planner 一直看着**整屏** | 【一手】 | https://arxiv.org/html/2504.07981v1 |

**对我们的可执行结论【推断】**：
1. 我们用的是**通用 API 模型**，输出基本只有一个点 → 「按框裁」**不适用**（没有框可按）。CogAgent 式「学一个 cropping module」需要训练语料与权重，**不适用**。
2. 若模型能同时给点与框，官方 Iterative Focusing 的做法值得抄：**用 bbox 中心选象限**（而不是用点），并用**点-框不一致**作为「粗定位不可信」的信号（与 ZoomClick 的 pre-zoom 同意检验同构）。
3. 我们能拿到的「框」只有 ScreenSeekeR 式的 **planner 提议框**（同一次调用里让模型先给候选区域，再给点）。代价 = 多一次模型调用 + 需要把整屏再送一遍。**列为可选项，不进第一版最小改动**。

---

## 3. (c) ZoomClick 的 Pre-Zoom：机制、参数、作用

论文：*Zoom in, Click out: Unlocking and Evaluating the Potential of Zooming for GUI Grounding*，arXiv:2512.05941（Princeton AI Lab / PKU）
URL: https://arxiv.org/abs/2512.05941 ｜ 代码: https://github.com/Princeton-AI2-Lab/ZoomClick ｜ HTML: https://arxiv.org/html/2512.05941v1

### 3.1 Pre-Zoom 机制（论文 §3.2.1 原文 + 代码）【一手】

- 论文原文："a single-shot agreement test between the full image and **K non-overlapping patches (we use K=4 for a 2×2 grid)** in the first iteration"；
  `p^(1) = p^(k*)  if d_k* < τ ; else p^dir`，
  其中 `d_k = ‖p^dir − p^(k)‖₂`（**像素空间**距离）、**τ 是像素阈值**。
- 代码（`grounding/eval_sspro_zoomclick.py`）：把整屏切成 4 个**不重叠**象限各跑一次模型 → 象限内点按 `orig = x1 + px*(x2-x1)` 映回全图 → 与整屏点的**像素距离** `min_distance` 比较：
  `if min_distance < prezoom_px_thresh: 用象限点作为 zoom 起点` 否则 `用整屏点`。
  象限内模型失败时 **fallback 到象限中心**；整屏失败时 fallback 整屏；全失败时 fallback 图中心。

### 3.2 官方默认参数（**照抄表**，来源：README + `run_zoomclick_uivenus.slurm` + argparse 默认值）【一手】

```
--in_depth 3            # 迭代收窄轮数（总 VLM 调用 = 1 整屏 + 4 象限 + (depth-1) = 7）
--in_ratio 0.5          # 每轮收缩比 ρ（线性尺寸 ×0.5）
--in_min_crop 768       # 最小 crop 尺寸 m（同时是「不再缩小」的停止条件）
--patch_size 2          # Pre-Zoom 网格 2×2（可选 3、4）
--center_mode "clip"    # 边界处理：shift(默认) | clip | shrink
--prezoom_px_thresh 50  # Pre-Zoom 像素距离阈值 τ
```

### 3.3 「怎么裁」的确切实现（逐字）【一手】

```python
target_w = max(int(round(curr_W * self.shrink_ratio)), self.min_crop_size)
target_h = max(int(round(curr_H * self.shrink_ratio)), self.min_crop_size)
target_w = min(target_w, curr_W); target_h = min(target_h, curr_H)
center_px = _norm_to_pixel_point(pt_view, (curr_W, curr_H))
curr_img, crop_px = crop_center_px(curr_img, center_px, target_w, target_h, center_mode=self.center_mode)
```
- **按点裁、窗口 = 0.5×当前图、下限 768 px**；`center_mode` 三选一：
  - `shift`：保持窗口尺寸，平移进边界（默认）；
  - `clip`：溢出部分裁掉（窗口在贴边处会变小，**但预测点仍在窗内**）；
  - `shrink`：缩小窗口以保持点居中。
- 坐标回映射：`view_point_to_real_point` = 归一化仿射 `rx = vx1 + (vx2-vx1)*px`，最终 `round(W·pr_x)` 换到原图像素；**最终答案 = 最后一轮 crop 里那次预测映回原图**。
- 终止：`达到 max_depth` / `crop 触到 min_crop`（`window cannot shrink further; stop early.`）/ `模型返回 None`。

### 3.4 Pre-Zoom 对「crop 命中」的作用

- 机制作用【一手】：Pre-Zoom 的产出不是"更准的答案"，而是**给第一轮裁剪换一个更靠谱的中心**（象限里的局部预测点）。因为预放大后第一刀就是以这个点为中心 → 直接抬高「crop 含住目标」的概率。
- 数字：**"across all models, Pre-zoom improves grounding accuracy at all depths"**（论文 Table 4 标题/结论，检索摘要转述）；**具体 pp 数值我这一轮没取到** —— arXiv HTML 在 §4.1 后被截断，PDF 端点返回 `unsupported content type "application/pdf"`，OpenReview PDF 被浏览器校验拦截 → **本条按【二手】标注，数字缺失需下一轮补**【一手（存在性）/ 二手（转述）】。
- 可引用的端点数字：**UI-Venus-72B + ZoomClick = ScreenSpot-Pro 73.1%（SOTA）**；"UI-Venus-7B with ZoomClick outperforms original UI-Venus 72B by 2.2%"【一手（README/Abstract）】。

---

## 4. (d) 有没有公开实现报告「crop 不含目标」的比例？怎么修？

### 4.1 比例：**没有公开数字（不适用）**

检索范围内**没有任何论文报告「crop 窗口没框住目标」的样本占比」**。最接近的三个可引用物：
- ScreenSpot-Pro 论文 **Table 6「ReGround Crop size ablation on ScreenSpot-Pro」**（表存在于论文 §5.2，标题与"crop size 可手工配置"的正文相互印证；**表内数值本轮未取到**，被 HTML 截断 + PDF 不可解析）【一手（表存在）/ 数值缺失】
- ScreenSpot-Pro §5.2 小节标题：**"Ablations on the crop size of ReGround."**【一手】
- **BAMI（CVPR 2026）** 有 "Accuracy (%) vs. Crop-Ratio" 消融表，声称"cropping around these initial predictions at a predefined ratio"【二手，未打开原文，不作为依据】

→ **我们那条「36.1% 的 crop 窗口压根没框住目标」目前是这类量测的**唯一已知实例**（自家测的，不是文献）**。

### 4.2 公开实现给出的四种「怎么修」【一手】

| 修法 | 出处与原文/参数 |
|---|---|
| **把窗口做大（并按图的固定比例走）** | 官方 Iterative Narrowing：patch = 当前图半宽半高；ZoomClick：`max(0.5·W, 768)`【一手】 |
| **框膨胀** | ScreenSeekeR："we apply **box dilation** to expand smaller ones into larger candidate areas, **reducing the risk of missing the target**"【一手】 |
| **边界不产生空白、只夹紧** | 官方 Focusing：`max(0.0,·)`/`min(1.0,·)` 后裁；ZoomClick `center_mode=shift/clip/shrink`【一手】 |
| **保留整屏作为第二视图 / 让另一个更强组件看整屏** | ScreenSeekeR：planner(GPT-4o) 始终看整屏并提议候选区域；grounder 只在 **patch ≤1280 px** 时被调用【一手】 |
| **停止条件** | ZoomClick：depth 用尽 / crop 触到 min_crop / 模型报 negative【一手】 |

### 4.3 官方四种方法都**不**做的事（这点很关键）【一手】

**它们都不检查 crop 是否含住目标，也不在 miss 时回落到整屏**（Focusing 的源码里没有 containment test；ReGround 是"crop around the initial prediction"一次性两段式）。也就是说：**"36.1% miss"这个问题，官方用"窗口开得足够大 + 迭代收窄"绕过去，而不是"检测到 miss 再补救"。**
→ **【推断】** 我们若照抄"大窗口 + 迭代收窄"，36.1% 会大幅下降，但不会归零；剩下的长尾（我们已测：14.8% 的粗点误差 > 1024 px）必须靠 pre-zoom 同意检验 / 第二视图来兜。

---

## 5. 换算到我们这台机器（2560×1600 物理像素）

我们的 crop 判定：`src/minicode/coord/crop_refine.py`（`plan_crop` / `_fit_axis`，越界用 **shift/shrink** 且保证粗点仍在窗内 —— 与 ZoomClick 的 `shift`/`shrink` 同构，已经是对的）。

**crop 含住目标的条件 = 粗点误差同时满足 |Δx| ≤ W/2 且 |Δy| ≤ H/2**（两轴各自判定，不能只看一轴）。

| 方案 | 窗口（本机） | 容忍的粗点误差 | 出处 |
|---|---|---|---|
| **现状** | 192×192 | **±96 / ±96 px** | 本仓 `src/minicode/ground/channel.py:162-163`（`DEFAULT_CROP_WIDTH_PX = 192`）【一手·本仓】 |
| 官方 ReGround | 1024×1024 | ±512 / ±512 | reground.py:151【本仓已核】 |
| 官方 Iterative Narrowing（±0.25 归一化） | 1280×800 | ±640 / ±400 | iterative_narrowing.py:189-192【本仓已核】 |
| ZoomClick 第 1 轮 | max(0.5·2560,768)=1280 × max(0.5·1600,768)=800 | ±640 / ±400 | 【一手】 |
| ZoomClick 第 2 轮 | max(0.5·1280,768)=768 × max(0.5·800,768)=768 | ±384 / ±384 | 【一手】 |
| ZoomClick 第 3 轮 | 768×768 → `window cannot shrink further; stop early` | — | 【一手】 |

对照我们已测的粗点误差分布（自家数据）：**挑错元素 n=107 误差中位 226 px、p90 1451 px、14.8% > 1024 px**。
→ **【推断】** 只把 192 换成 1024（±512）可把"窗口没框住"的中位段全部救回；**但 14.8% 的 >1024 px 长尾依然会 miss**，必须叠加 pre-zoom / 第二视图。
→ **【推断】** 恰好落在 ±512 之外的概率不能用中位数估，必须用**已记录的粗点**离线重算（见改动 #1，零模型调用）。

---

## 6. ≤8 条最小改动清单（按投入产出排序）

> 每条：改哪个文件 → 做什么 → 预期哪个数变多少。括号里是**验收口径**。所有"预期"均为【推断】，需按项目既有配对评测口径复核。

| # | 改哪里 | 做什么 | 预期哪个数变多少 |
|---|---|---|---|
| **1** | 新增 `src/minicode/ground/crop_geometry.py`（或扩 `numbered_bench.py` 的 `--crop-scan`） | **离线重算**：用已有记录里的粗点 + GT bbox，对候选几何（192 / 512 / 1024 / 1280×800 / 768×768）直接算「crop 含住 GT bbox」的比例与「两轴都含」的联合判定，不调模型 | 把「36.1% miss」变成**每个候选几何一个实测数**（预期 192→1024 后 miss 从 36.1% 降到 ~10% 量级；精确值由本步给出）。**这是所有后续改动的地基，零模型调用** |
| **2** | `src/minicode/ground/channel.py:162-163`（`DEFAULT_CROP_*`） | 把第一级 crop 从**固定像素 192** 改成**按物理帧比例**（默认 0.5 宽高，官方 Iterative Narrowing 的 ±0.25 归一化口径；下限 768、贴边沿用现有 shift/shrink 语义）→ 本机第一级 = 1280×800 | 裁剪容忍度 ±96 → ±640/±400；配合 #1 的数，预期总体命中率 **+2～+6 pp**（长尾 miss 的那一段）；单样本 crop token 49 → 视模型输入上限而定（**注意成本，见 #7**） |
| **3** | `src/minicode/ground/channel.py`（`TwoStageGrounder` 精定位段） | 加**第二级收窄**：以第 1 级精定位结果为中心再裁 768×768（= ZoomClick 第 2 轮），最终答案取第 2 级 | 命中段（找对元素 n=76）精定位质量再提升；预期 **icon 分层**受益最大（16 px 图标在 768 窗口里相对占比 ×3.3）；代价 = 每样本 +1 次模型调用 |
| **4** | 新增 `src/minicode/ground/prezoom.py` | 实现 ZoomClick Pre-Zoom 同意检验：整屏 + 2×2 象限各 1 次，**像素距离 < 50 px** 才用象限点作裁剪中心（否则用整屏点） | 直接攻击「挑错元素」：预期把**落在错误象限**的样本救回，预期 **+2～+5 pp**；代价 = 每样本 +4～5 次模型调用（**建议只在 #2 之后仍有 miss 时启用**） |
| **5** | `src/minicode/ground/gate.py`（偏离/一致性阈值） | 现阈值是按 192 crop 标定的（注释写明"192 时 50 px ⇔ c ≈ 260.4"）；crop 尺寸改成 1280/768 后**系数会变**，必须按 `c = ε₁ × 1000 / crop 宽` 重标，否则回落判据会误杀 | 预期**回收一部分"判据错"样本**（我们自测：放宽判据净 +2.64 pp 是同口径的先例）；若不重标，预期 **净收益被回落吃掉 1～3 pp** |
| **6** | `src/minicode/eval/sspro.py`（`ArmOutcome` / 记录字段）+ `src/minicode/eval/formats.py` | 在评测记录里**新增两个可监控字段**：`crop_contains_target`（crop 窗口是否含住 GT bbox）、`crop_miss_reason`（out-of-frame / coarse-picked-wrong / out-of-bbox） | 让「36.1%」**从一次性诊断变成常驻指标**；后续任何改动都能立刻看到 miss 结构（这是本线唯一能长期自我验证的钩子） |
| **7** | `src/minicode/ground/channel.py`（`model_calls` 埋点）+ `src/minicode/eval/__main__.py` | 记录/限制每样本模型调用数（现状 2 次 → #3 后 3 次 → #4 后 7～8 次），并把**成本**纳入门控（例如：只有粗点离画面中心很远、或粗点与象限点不一致时才跑 pre-zoom） | 不改命中率，但防止 #4 把**延迟/费用**推到不可用；预期 token/延迟上升控制在 ×2 以内 |
| **8** | `src/minicode/eval/sspro.py:159-217`（`OFFICIAL_ROWS`） | 把本报告核实的官方行补齐并加出处：ReGround 1024×1024→40.2、Narrowing ±0.25→31.9、Focusing 2×2 象限→31.0、ScreenSeekeR min_crop[1280,768]+aspect 2.5→48.1，并新增 **ZoomClick 行：1280×800→768→768、pre-zoom 2×2/τ=50px → 73.1%（UI-Venus-72B）** | 不改分数；把「我们 44.8% vs 官方同款几何」放在同一张表里，**避免拿自造几何跟官方数字对比**（现在表里的官方行没有一条与我们的 192 crop 同构） |

---

## 7. 未解决 / 下一轮要补的（不要当成已解决）

1. **ScreenSpot-Pro 论文 Table 6「ReGround crop size ablation」的数值** —— 本轮 arXiv HTML 在 §4.2 后被截断、PDF 端点不支持解析、OpenReview 被浏览器校验拦截。下一轮建议：`web_fetch` `https://arxiv.org/html/2504.07981v1` 后按 anchor 定向取 §5.2，或用 ar5iv 镜像。**这是"crop 尺寸 → 准确率"的唯一官方曲线**，优先级最高。
2. **ZoomClick 的 pre-zoom 增益 pp 与 depth/shrink/min-crop 消融数值** —— 同因截断未取到；论文里有 Table 3/4（"Effect of Pre-zoom across Zoom Depths"）。
3. **官方 `reground.py` / `iterative_narrowing.py` / `screenseeker.py` 的逐行复核** —— 本轮该子代理 3 次连接超时；本报告里这几行来自**本仓既有记录**（带官方文件与行号），本轮未复核。
4. **我们自己的"crop 含住目标"精确曲线** —— 必须由改动 #1 用已记录粗点离线算出，不许用中位数外推。

---

## 8. 来源清单（全部可打开）

- ScreenSpot-Pro 论文 HTML（方法 §4.1/§4.2、评测口径）：https://arxiv.org/html/2504.07981v1 【一手】
- ScreenSpot-Pro 论文 PDF（备用，本轮不可解析）：https://arxiv.org/pdf/2504.07981
- 官方代码仓（tree 列表已一手核实）：https://github.com/likaixin2000/ScreenSpot-Pro-GUI-Grounding
- 官方 `models/methods/iterative_focusing.py`（本轮一手逐行核实）：https://raw.githubusercontent.com/likaixin2000/ScreenSpot-Pro-GUI-Grounding/main/models/methods/iterative_focusing.py
- 官方 `models/methods/reground.py`（本轮超时，内容引用自本仓记录）：https://raw.githubusercontent.com/likaixin2000/ScreenSpot-Pro-GUI-Grounding/main/models/methods/reground.py
- 官方 `models/methods/iterative_narrowing.py`（同上）：https://raw.githubusercontent.com/likaixin2000/ScreenSpot-Pro-GUI-Grounding/main/models/methods/iterative_narrowing.py
- 官方 `models/methods/screenseeker.py`（同上）：https://raw.githubusercontent.com/likaixin2000/ScreenSpot-Pro-GUI-Grounding/main/models/methods/screenseeker.py
- 官方 `models/zoom_consistency_router.py`（同上）：https://raw.githubusercontent.com/likaixin2000/ScreenSpot-Pro-GUI-Grounding/main/models/zoom_consistency_router.py
- 官方 readme（无方法准确率、无 crop CLI 默认值）：https://raw.githubusercontent.com/likaixin2000/ScreenSpot-Pro-GUI-Grounding/main/readme.md
- ZoomClick 论文 HTML（§3.1–§3.3 机制、默认超参、边界处理、终止条件）：https://arxiv.org/html/2512.05941v1 【一手】
- ZoomClick README（官方 CLI 默认值 + 73.1%）：https://raw.githubusercontent.com/Princeton-AI2-Lab/ZoomClick/main/README.md 【一手】
- ZoomClick 官方运行脚本（产出 73.1% 那次的真实参数）：https://raw.githubusercontent.com/Princeton-AI2-Lab/ZoomClick/main/scripts/run_zoomclick_uivenus.slurm 【一手】
- ZoomClick 评测实现（裁剪几何、pre-zoom、坐标回映射）：https://raw.githubusercontent.com/Princeton-AI2-Lab/ZoomClick/main/grounding/eval_sspro_zoomclick.py 【一手】
- SeeClick（point vs box 的输出形式对照）：https://aclanthology.org/2024.acl-long.505.pdf 【一手（引文）】
- CogAgent（训练式 bbox cropping module）：https://arxiv.org/html/2312.08914v3 【二手】
- RegionFocus（arXiv:2505.00684，61.6%@Qwen2.5-VL-72B）：https://arxiv.org/abs/2505.00684 【二手】
- GUI grounding leaderboard：https://gui-agent.github.io/grounding-leaderboard/ 【二手】

### 本轮检索到但**未采信**的条目（避免以后被误引）

- 各种 2026 年 arXiv 编号（2605.30884「crop ratio 40%」、2608.03270「GUI-Lens」、2609.18690「RankGround」、2606.30084「InnerZoom」、CVPR 2026 BAMI、AAAI 2026 等）：本轮**只出现在检索摘要里、未打开原文**，一律按【二手/未核实】处理，**不作为本报告任何建议的依据**。
- 「OpenAdapt grounding 裁剪使 UI-TARS 提升 +96%」：未核实，不引用。

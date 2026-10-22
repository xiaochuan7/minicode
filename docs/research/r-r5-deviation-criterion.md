# r5 —— 「精定位离粗定位多远算选错」这条判据：公开一手来源核查与改法

> 调研线：deviation criterion（偏离容差 / 回落判据）
> 轮次：2026-10-22　方法：联网查一手来源（arXiv HTML、官方 GitHub README、官方文档）
> web 工具调用：**12 次**（预算上限 12）。未能直接读到的内容一律标【二手】/【推断】，见 §9。
> 硬约束：本轮**未修改任何仓库文件**（只读）；本文件是唯一产物。

---

## 0. 一句话结论

**问题不在 50 这个数，在「量」和「回落目标」两处：我们拿 ZoomClick 的 50 px 去卡一个它并不定义的量（离 crop 窗口中心），然后在判据触发时回落到统计上更差的答案（粗定位点，12/30）——公开文献给出的做法是「用 ‖相邻两次预测之差‖₂ 做一致性检测（ZoomClick），用与 incumbent 的几何护栏 + 中点融合做回落（LFPR），用多采样投票解决『挑错元素』（GUI-RC）」。**

三个可照抄的数：

| 用途 | 值 | 出处 |
|---|---|---|
| 一致性判据的**量** | ‖精定位 − 粗定位‖₂（物理像素，**原图坐标系**） | ZoomClick §3.2.1【一手】 |
| 该量的默认阈值 | **50 px**（最优 50.7 px） | ZoomClick README `--prezoom_px_thresh 50`【一手】/ Table 11 数值【二手】 |
| 同一尺度下的独立第二锚点 | **47.5 px**（= 192 px crop 上 LFPR 的 z ≤ 0.35 护栏） | LFPR §3.3【一手】 + 本仓换算【推断】 |

---

## 1. 我们现在这条判据到底在量什么（代码口径）

先把口径钉死，否则下面的对照无从谈起。

**判据位置**：`src/minicode/ground/channel.py` L635–658

```python
# -- 偏离量（工单 M7 AC 3）：选到相邻的同类控件 → 回落 --------------
# ⚠️ **参考点是 crop 窗口的中心**（不是粗定位点：crop 走 **shift**
# 语义时两者不同 —— 目标贴着屏幕边时窗口被平移过）。
deviation = math.hypot(refined[0] - plan.center[0], refined[1] - plan.center[1])
if deviation > self.gate_params.deviation_tolerance_px:   # 50.0 px
    ... landing_point = coarse ...  fallback_cause = FallbackCause.DEVIATION
```

* 阈值：`src/minicode/ground/gate.py` L194 `DEFAULT_DEVIATION_TOLERANCE_PX = 50.0`
* 取值来源（已写进代码）：`gate.py` L195–206，声称出自 **ZoomClick arXiv:2512.05941 Table 11**（最优 50.7 px / 分类准确 91.8% / 正确对中位 <10 px / 错误对中位 >50 px）。
* crop 尺寸：`src/minicode/ground/channel.py` L162–163 `DEFAULT_CROP_WIDTH_PX = 192`、`DEFAULT_CROP_HEIGHT_PX = 192`（**正方形**，物理像素）。
* crop 窗口是**以粗定位点为中心**规划的：`src/minicode/coord/crop_refine.py` L162–181（`center_x/center_y` = 粗定位点；只有越界时才 shift / shrink）。
* **关键**：仓库**已经算好了**我们真正需要的那个量 —— `channel.py` L727–732
  `zoom_consistency_px = ‖refined − coarse‖₂`，并有相对刻度 `zoom_consistency_c1000 = consistency × 1000 / plan.width`（192 crop 下系数 5.208）。
  **它只是没有被拿去当门控判据。**

**由此得到三个事实：**

1. **量的口径错配。** 门控量 = ‖精定位 − crop 窗口中心‖；我们引用的 ZoomClick 那个 50 px 定义的是**相邻两轮预测之间的位移**（= ‖精定位 − 粗定位‖）。二者只在 crop 被 shift 时不同，而 shift 只发生在目标贴屏幕边时（192 px 窗口最多平移约 96 px）——**量级与阈值本身相当**。也就是说，贴边目标上我们的判据被额外注入了最多 ~96 px 的偏移。
2. **回落目标选错。** 触发回落时我们回落到**粗定位点**；自家重放显示这批样本里**精定位命中 18/30、粗定位只有 12/30**。门控在把更好的答案扔掉。
3. **数值本身其实无关紧要。** 既然换量之后统计关系会变，"50" 必须重新扫，不该直接沿用。

---

## 2. (a) ZoomClick §3.2.1 的原始定义：它量的到底是什么

论文：**"Zoom in, Click out: Unlocking and Evaluating the Potential of Zooming for GUI Grounding"**（Jiang et al., Princeton AI² Lab / PKU, 2025-12-05）
<https://arxiv.org/abs/2512.05941> ｜ HTML 全本 <https://arxiv.org/html/2512.05941v1> ｜ 代码 <https://github.com/Princeton-AI2-Lab/ZoomClick>

### 2.1 §3.2.1 "When to Zoom in" 的原文定义【一手】

我直接读了 arXiv HTML 的 §3.2.1，原文公式（逐字）：

```
p^dir = G(I, q),   p^(k) = G(I^(k), q)        k = 1..K，K=4（2×2 网格切块）
d_k  = ‖p^dir − p^(k)‖₂ ,   k* = argmin_k d_k
p^(1) = p^(k*)   if d_{k*} < τ
       = p^dir   otherwise
```

要点：

* 量的是 **两个不同视角下同一目标的预测之间的欧氏像素距离**（`p^dir` = 整图预测，`p^(k)` = 第 k 块切片的预测），**在原图坐标系**上算（`p^(k)` 先按 viewport 映射回原图）。
* `k* = argmin_k d_k` —— 取**四个切片里与全局预测最接近的那一个**，不是平均、不是全部一致才用。这是「有没有任何一个独立视角同意全局预测」的检验。
* τ 单位是**像素**，含义是：局部预测与全局预测**一致**（距离 < τ）才用局部那个作为缩放起点，否则保留全局视图「避免过早收窄」。
* 论文自述动机（§3.2.1 前后文）：**首步准确率决定后续步骤的上限**（Fig. 6），Pre-Zoom 是一次性的 agreement test。

### 2.2 公开的阈值与取值【一手 + 二手】

| 项 | 值 | 强度 | 出处 |
|---|---|---|---|
| 官方默认 τ | **50 px**（命令行 `--prezoom_px_thresh 50`） | 【一手】 | ZoomClick README 原文：`--prezoom_px_thresh: Threshold of pixel distance used in Pre-zoom.` |
| §6.2 附录章节名 | "The Selection of Distance Threshold" | 【一手】 | arXiv HTML 目录（§6.2 "More Ablations"） |
| 阈值选取方法的原文措辞 | "We report the **best pixel threshold that separates correct and error pairs**, along with the resulting accuracy." | 【二手】（检索片段逐字引用，未见表格本体） | arXiv:2512.05941 §6.2 |
| 该距离的原文定义（片段） | "the Euclidean pixel displacement between **two consecutive predicted click locations produced in adjacent zoom rounds**, measured in the **original image space**" | 【二手】 | 同上 |
| 最优阈值 50.7 px、分离准确率 91.8%、正确对中位 <10 px、错误对中位 >50 px | 见本仓 `gate.py` L196–198 的既有引用 | 【二手】（我**未能**读到 Table 11 表体，见 §9） | 本仓引用 + 检索片段佐证定义 |

### 2.3 对我们最重要的一条推论【推断，证据强】

**ZoomClick 的 50 px 是「两个独立视角的预测之间的位移」，不是「精定位相对某个几何中心跑了多远」。**

我们把 50 px 套在「离 crop 窗口中心多远」上，是**跨口径套用**。这解释了我们自己的诊断为什么会打架：

* ZoomClick 里距离大 = 两个视角**看到了不同的东西** → 丢掉局部预测、保留全局预测是安全的（全局有完整上下文）。
* 我们这里距离大 = **同一个模型在同一张 crop 上换了目标** → 而我们换完的目标（精定位）反而**比粗定位更准**（18/30 vs 12/30）。此时回落到粗定位点就是**主动降级**。

→ **所以「量」和「回落目标」必须一起改，单调阈值解决不了。** 这也是 ZoomClick 原文「Distance-based Pre-zoom yields better performance」不能直接搬过来的原因：我们的粗定位没有 ZoomClick 的"全局视图"那种信息优势（我们的粗定位也是同一个 API 模型、同一张缩图）。

---

## 3. (b) GUI-RC 的空间投票：机制、公开增益、以及它对我们**不适用**的部分

论文：**"Test-Time Reinforcement Learning for GUI Grounding via Region Consistency"**（Du et al., 浙大 / 中南大学，2025-08-07）
<https://arxiv.org/abs/2508.05615> ｜ HTML 全本 <https://arxiv.org/html/2508.05615v1> ｜ 代码 <https://github.com/zju-real/gui-rcpo>

### 3.1 机制【一手】（我直接读了 §3.2 与 §4.1）

```
① 多采样：{M_k(s,i)}_{k=1..K} ~ p_θ(·|s,i)
② 空间投票网格 v ∈ R^{H×W}：
   v_{x,y} = Σ_k 1[(x,y) ∈ R_k]
   区域式模型：R_k = 预测框
   点式模型：R_k = [x_k − α/2, x_k + α/2] × [y_k − α/2, y_k + α/2]   ← α 是"同一元素"的尺度
③ 共识提取：v_max = max v；取所有「每点票数都等于 v_max」的连通区域，
   选面积最大的那个作为 r̂_cons
```

公开超参（§4.2 逐字）：**K = 64 次采样，temperature = 0.5，top_p = 0.95，α = 50**。

判定口径（§4.1 逐字）："a prediction is considered correct if the predicted point **or the center of the predicted bounding box** falls within the ground-truth bounding box" —— 与 ScreenSpot/ScreenSpot-Pro 一致，**落点判命中 = 落在 GT 框内**，不是"离 GT 中心多近"。

### 3.2 公开增益【一手】（Table 1，我读到的行）

| 模型（输出式） | 基准 | 基线 → GUI-RC | Δ |
|---|---|---|---|
| Qwen2.5-VL-3B-Instruct（**框式**） | ScreenSpot-Pro | 20.18 → 23.59 | **+3.41** |
| Qwen2.5-VL-7B-Instruct（**框式**） | ScreenSpot-Pro | 19.80 → 23.97 | **+4.17** |
| InternVL3-2B-Instruct（**点式**） | ScreenSpot-Pro | 1.03 → 1.33 | **+0.30** |
| InternVL3-8B-Instruct（**点式**） | ScreenSpot-Pro | 13.28 → 12.46 | **−0.82** |
| UGround-V1-7B（**点式**） | ScreenSpot-v2 avg | 89.62 → 89.62 | **+0.00** |
| UGround-V1-7B（**点式**） | ScreenSpot-v1 avg | 87.11 → 87.34 | +0.23 |

论文自述："GUI-RC improves accuracy by **2–3%** across various architectures"，且 **"achieves greater improvements when applied to bbox-style prediction models"**（§4.2.1 小标题即为此意）。
其它公开数：Qwen2.5-VL-3B-Instruct ScreenSpot-v2 80.11 → 83.57（GUI-RC）→ 85.14（GUI-RCPO）。

### 3.3 对我们的判定【推断，基于上表一手数字】

* **增益集中在框式模型**（+3.4~4.2 pp），**点式模型≈0 甚至为负**。我们的"眼睛模型"输出的是**点**（`channel.py` 走 `_ask` 单点/框解析），所以**"直接照抄 GUI-RC 替换最终答案"没有公开支撑** → 不适用。
* **但 α = 50 这个数有直接价值**：它是公开文献里"两个点预测算不算同一个元素"的尺度（GUI-RC 把每个点扩张成 50 px 见方再投票）。与 ZoomClick 的 τ = 50 px 独立收敛在同一个数上 —— **50 px 是当前公开文献里"GUI 上同一元素"的常用尺度**（【一手】两个独立来源）。
* **真正能治我们"挑错元素"（中位 226 px）的公开手段就是投票**（GUI-RC 的立论：多次采样的一致区域 = 高置信区）。它在我们这里**最合适的用法是当"参考点"而不是当"最终答案"**：拿共识点替代 crop 窗口中心作一致性参考，再让距离判据去问"精定位是否与共识一致"。

---

## 4. (c) LFPR：几何护栏 + 中点融合 —— 这条线最该抄的一篇

论文：**"Where Grounding Accuracy Lives on the IoU Curve: Label-Free Inference-Time Boundary Refinement"**（Bo Ma, Auckland University of Technology, 2026-08-20）
<https://arxiv.org/abs/2608.19553> ｜ HTML 全本 <https://arxiv.org/html/2608.19553v1> ｜ OpenReview <https://openreview.net/forum?id=VzKjKDO8Ov>

（这就是任务里点名的 **LFPR = Label-Free Precision Refinement**。）

### 4.1 方法【一手】（§3.2 / §3.3 逐字）

* **分辨率路由（用预测自己的框，不用标注）**：把 incumbent 框换算成像素，`s(b0) = sqrt(w·h)`；
  `s < 128 px` = small，`128 ≤ s ≤ 256` = medium，否则 large；**只有 small 档**才走第二次高分辨率推理（像素预算下限 4,194,304 = 2048²）。
  路由与"按 GT 尺寸分桶"的一致率：**pilot 行 88.85%、official 行 89.83%**。
* **上下文裁剪重定位**：`C_γ(b0')`，宽 `min{1, γ·w}`、高 `min{1, γ·h}`，**γ = 2.0**，中心必要时平移以留在图内；把表达式在 crop 内重新定位，仿射映射回原图得候选 `b_c`。
* **三道几何护栏**（全部通过才接受候选）：

```
z(b_c, b0') = ‖c(b_c^C) − a‖₂ / √(1/2) ≤ 0.35      ← crop 归一化坐标下的中心距
IoU(b_c, b0') ≥ 0.25
0.5 ≤ A(b_c)/A(b0') ≤ 2.0
```

  （`a` = incumbent 映射进 crop 后的中心；`√(1/2)` = 单位正方形对角线长度 → **z 是"占 crop 对角线的比例"**。）
* **融合（回落方式）**：`b̂ = ½·b0' + ½·b_c`（**逐坐标中点**），护栏不过就保留 `b0'`。原文明确："That midpoint is a **fixed convex combination chosen before the reported run**: it lets the crop adjust boundaries without letting one noisy second answer replace a strong first one."
* **guard 是 load-bearing**：同一批候选**去掉 guard** 的消融在**每个指标上都不如** incumbent（"A genuine unguarded control … underperforms the incumbent on every metric"）。同段还报：一个用"冻结置信度阈值"替代几何 guard 的同骨干对照，**连 guard 的这点 Acc@0.5 增益都拿不回来** → 增益来自**几何判据本身**。

### 4.2 公开增益【一手】（摘要数字）

| 评估 | 样本 | 指标变化 |
|---|---|---|
| Ref-L4 测试集（**回顾性**，测试分布曾参与方法开发） | 31,921 表达式 | mAcc₀.₅:₀.₉₅ **72.947% → 76.013%**；Acc@0.5 **88.531% → 89.725%**；Acc@0.9 **55.788% → 61.142%** |
| RefCOCO/+/g **冻结迁移**（未重调任何参数） | 30,969 表达式 | pooled mAcc **+0.645**；Acc@0.5 **+0.817**；**Acc@0.9 总体不变**（路由 +1.162，crop+guard+fusion 归还 −1.192） |
| Flickr30K Entities **前瞻性、图像不交**确认 | — | mAcc **+0.973**；Acc@0.9 **+1.022**；单框变体 mAcc **+2.575** / Acc@0.9 **+3.689** |
| 两个已发布专用模型 EGM-4B / 8B | — | Acc@0.9 **+1.569 / +6.716**，代价约 **2× 延迟** |

### 4.3 对我们的适配性判定

| LFPR 组件 | 我们的可适配性 | 说明 |
|---|---|---|
| **分辨率/尺寸路由**（按预测框大小决定要不要第二次高分辨率） | **需改造** | 我们是「先整屏缩到 1280×800 再按点裁 192 px」，**图标在模型输入图里只剩 13~14 px**（自家量到的）；LFPR 的思路是**小目标才给高像素预算**。这与另一条线（缩放/接线）耦合，本线只登记依赖。 |
| **crop 内重定位（γ=2.0）** | **需改造** | 我们已经有 crop（192 px），但 crop 尺寸固定；LFPR 的 crop 尺寸是 incumbent 框的 2 倍。 |
| **z ≤ 0.35 中心距护栏** | **需改造 → 可直接用** | 我们只出点，没有框 → 用 `‖精定位 − 粗定位‖₂` 代替中心距；把 crop 归一化距离换算回物理像素（见 §6）。 |
| **IoU ≥ 0.25 护栏** | **需改造** | 我们没有候选框。可用**点版包含护栏**：精定位点必须落在粗定位框（物理像素）内，或距框边 ≤ margin（见改动 #4）。粗定位**只给点不给框**时 → **不可判定**（沿用现有 `unknown` 口径记录，不可判定率必须落盘）。 |
| **面积比 ∈ [0.5, 2.0]** | **不适用** | 点的面积为 0，比值无定义。 |
| **逐坐标中点融合（½+½）** | **不适用（危险）** | LFPR 的两个框**指同一 referent**，中点安全；我们诊断出"挑错元素"的中位误差 **226 px**，此时中点落在**两个元素之间的空隙**，命中两边皆无。**不做。** |
| **证据分层 + 冻结后再确认** | **直接可用**（见 §5） | 这是本轮对我们最实用的部分。 |

---

## 5. 阈值怎么选才不是在评测集上"调优" —— 公开做法

### 5.1 ZoomClick 的做法【一手 + 二手】：**一个固定常数，跨模型跨基准不重调**

* τ = 50 px 是**官方仓库的默认命令行值**，被 UI-Venus-7B / Qwen3-VL 等多个骨干、ScreenSpot-Pro / UI-Vision 两个基准共用 —— **不是每个模型重新调的**。
* 它的**论证**方式（§6.2）：扫描像素阈值，取**最能分开"正确对"与"错误对"**的那一个，报告对应准确率；"正确对"= 两点落在同一正确区域，"错误对"= 落到错误元素。
  → 这是一个**事后分离度分析**，用于给那个**固定常数**找依据；它本身就是在评测集上算的，所以只能当**论证**，不能当**无偏调参**。

**可直接抄的做法**：**阈值是常数，不随模型/基准重调；用"正确对/错误对的距离分布"给它做论证，而不是在基准上做搜索。**

### 5.2 LFPR 的做法【一手】：**三层证据（§4.1 / §4.4）** —— 我们最该学的纪律

| 层 | 含义 | 论文自述 |
|---|---|---|
| **Tier 1 回顾性 retrospective** | 已发布测试分布**参与过**方法开发 | "the same published test distribution informed diagnostics and method development … we treat that estimate and its interval as **retrospective**"；且共享的 2,000 行 pilot "is **not an untouched confirmation set**" |
| **Tier 2 冻结迁移 frozen transfer** | 换数据集，**一个参数都不重调** | RefCOCO/+/g 上 "retuned nothing" |
| **Tier 3 前瞻性、图像不交 prospective** | 图与标注**全程没参与**开发 | Flickr30K Entities "whose images and annotations played no role in method development" |

其它可抄的纪律：
* **参数在最终整批跑之前就冻结**（"the fusion weight, crop context, resolution setting, and guards were fixed before the final full-split run"），但作者同时诚实指出"broader data-access chronology is not independently auditable" → **我们自己必须把冻结时刻写进产物**。
* **统计单位 = 图像簇**：对每张图的表达式一起做 **image-cluster bootstrap，10,000 次重采样，双侧 95% 百分位区间**；每一张表保留**逐行 win/loss/tie**计数；无效输出**计数而不是丢弃**。
* 明确区分 **confirmatory（可对外声明）** 与 **exploratory（探索性）**，并对同一比较族内的多指标做 **Holm 校正**。

### 5.3 我们自己的 A/B 切分不够用（本轮再确认一次，并给出替代）

* 我们已有 `src/minicode/eval/holdout.py` 的 A（调参）/B（评估）机制与 `tune_gate_params()`（悲观下界选参，B 一条不进调参），以及 `DEVIATION_CANDIDATES` 网格。
* 但我们的切分是 **index%2**，自家已量到两半构成差 **C = −5.63 pp（SE 0.57）** → 分半本身不可靠；且 A、B **都在 ScreenSpot-Pro 上** → 按 LFPR 的分层，这连 Tier 1 都算不上（同一发布测试分布）。
* **替代做法（可照抄）**：
  1. **按 app / platform 分组**做 K 折（`sklearn.model_selection.GroupKFold` 语义），**绝不按 index**；同屏截图必须是同一组（相关样本会造成信息泄漏）。
  2. 阈值在**折外**选（沿用 `tune_gate_params` 的"悲观下界最大、并列取最大容差"规则），折内只评；**报告各折选出阈值的分布**（中位数 + 极差），不要只报一个数。
  3. 把最终阈值**冻结**，再在一批**新采、图像不交**的真机截图上做一次 **Tier 3 前瞻确认**。
  4. 产物里**明确标注 tier**（retrospective / frozen-transfer / prospective），不要让回顾性数字以"验证过的阈值"的形式流通。

### 5.4 通用方法学锚点【二手】

`scikit-learn` 的 `TunedThresholdClassifierCV`（官方文档 <https://scikit-learn.org/stable/modules/generated/sklearn.model_selection.TunedThresholdClassifierCV.html>）是"**阈值不在测试集上调**"的标准范式：用内层交叉验证在折外预测上选阈值，再用外层评估。（本次 web fetch 只拿到页面导航、正文被截断，故具体措辞标【二手】；但"阈值应当由折内交叉验证选出、而不是在最终评估集上选"是该 API 的既定语义。）

---

## 6. 那到底该设多少：把公开锚点换算到我们的刻度

### 6.1 换算前提（已从代码确认）

* 我们的屏幕：**2560 × 1600 物理像素**；crop 窗口 **192 × 192 物理像素**（正方形），**以粗定位点为中心**，越界才 shift。
* ScreenSpot-Pro 图像为高分辨率桌面截图（与我们的 2560 宽屏**同一量级**）→ **绝对像素阈值在两个体系间可比**。
* 相对刻度：`c = ‖Δ‖₂ / crop 宽 × 1000`（仓库已有 `zoom_consistency_c1000`，192 crop 下系数 **5.208**）。

### 6.2 两个公开锚点，换算到同一把尺子上

| 锚点 | 原文 | 换算到我们的 192 px crop | 强度 |
|---|---|---|---|
| **ZoomClick τ** | 50 px（默认）/ 50.7 px（最优）= ‖相邻两轮预测位移‖₂，原图空间 | 原图空间，**不随 crop 大小变** → 直接取 **50 px** | 【一手】(README 默认值) / 【二手】(Table 11) |
| **LFPR z 护栏** | `z = ‖Δ‖₂/√(1/2) ≤ 0.35`（crop 归一化） | ‖Δ_norm‖₂ ≤ 0.35×0.7071 = **0.2475（crop 边长的比例）** → 0.2475 × 192 px = **47.5 px** | 【一手】(公式) + 【推断】(换算) |
| GUI-RC α | 50 px 见方扩张（"同一元素"尺度） | 原图空间 → **50 px** | 【一手】 |

**结论：三个互不相关的一手来源收敛到 47.5~50 px —— 这是我们这条判据在 192 px crop 上的公开锚点，取 50 px（对应 c ≈ 260.4），LFPR 的 47.5 px 给出同量级佐证（c ≈ 247.5）。**

### 6.3 但「50 px」不是我们的最终答案 —— 量换对之后必须重扫

* 我们自己的数据说：当前量（离 crop 中心）在 50 px 上丢弃 **43.7%**，放宽到 **t = 80~∞ 是平台**、峰在 **96**，净 **+2.64 pp**；重放 30 条被丢弃样本，**精定位命中 18/30、粗定位只有 12/30**。
* 这与公开证据一致地指向同一件事：**「两个预测差得远」在我们这套设置里 ≠ 「细定位选错了」**。
  ZoomClick 的距离判据之所以有效，是因为它的两个预测来自**两个独立视角**（整图 vs 2×2 切片），差得远确实意味着看到了不同的东西；我们的两个预测来自**同一模型、同一个 crop**，差得远更可能是"模型换了个目标"，而**换的那个往往更准**（18/30）。
* 所以最终取值应该是：**量换成 ‖精定位 − 粗定位‖₂，τ 在 A 半用分组 CV 重扫候选 {24, 32, 40, 48, 56, 64, 80, 96, 128, ∞}，B 半只评一次**；
  **先验上应落在 80~128 一侧**（平台段），而不是照抄 50。
* 另外必须记住一条公开的反向教训：LFPR 证明 **"放宽阈值"不是免费的**——他们的 guard 与中点融合**主动把一部分严格 IoU 精度还回去**（Acc@0.9：路由 +1.162，crop+guard+fusion −1.192），净效应是"精度被**重分配**"而不是全面变好。我们只要落点命中（bbox 命中），所以对我们有利；但**对外报告时不能只报 Acc@0.5**。

### 6.4 公开的「预测与参考点距离分布」—— 能拿到手的那一份

【二手】ZoomClick Table 11（经本仓 `gate.py` L196–198 引用 + 检索片段佐证定义）：

* **正确对**（两次预测指向同一正确元素）距离**中位 < 10 px**；
* **错误对**（落到错误元素）距离**中位 > 50 px**；
* 最优分割阈值 **50.7 px**，分离准确率 **91.8%**。

**这正是我们要的先验**：正常情况下，同一元素的两级预测差 **< 10 px**；差 **> 50 px** 时八成是换了元素。→ 它同时说明：
1. **50~80 px 是"两预测仍属同一元素"的合理上界**（比 96/128 保守）；
2. 我们在 43.7% 的样本上触发回落，**远超公开的 ~8% 错误率** → 我们的判据在**量**上就不对（贴边 shift 注入偏移 + 口径错配），而不是"阈值定得太紧"。

---

## 7. 回答任务的四个子问（速查）

| 子问 | 结论 | 强度 |
|---|---|---|
| (a) ZoomClick §3.2.1 的 ε₁ 定义 | 量的是**相邻两轮预测的欧氏像素位移**（原图空间），取 4 个切片中**与全局预测最接近**的那个；阈值 τ 单位像素，默认 **50 px**（最优 50.7）。**不是**"离 crop 中心多远" | 定义/默认值【一手】；Table 11 数值【二手】 |
| (b) GUI-RC 空间投票 | K=64 采样（T=0.5/top_p=0.95），点扩张成 **α=50 px** 方框投票，取最高票连通区最大者为共识。**+2~3% 平均**，但**框式 +3.4~4.2 pp、点式 ≈0（UGround +0.00 / InternVL3-8B −0.82）** | 【一手】 |
| (c) LFPR / 几何护栏 / 中点融合 | 三道护栏（中心距 ≤ crop 对角线 0.35、IoU ≥ 0.25、面积比 0.5~2.0）+ **½–½ 逐坐标中点融合**；护栏是 load-bearing（去掉它全线变差）；**中点融合对我们不适用**（226 px 错选时中点落在空隙） | 【一手】 |
| (d) 公开的距离分布先验 | ZoomClick Table 11：正确对中位 <10 px、错误对中位 >50 px、分离准确 91.8% @ 50.7 px。GUI-RC 的 α=50 px 是"同一元素"的公开尺度。**LFPR 的 guard 是相对量（crop 比例），换 crop 宽需换算** | 数值【二手】/尺度【一手】 |
| 阈值选择纪律 | ZoomClick：固定常数 + 分离度论证；LFPR：三层证据（回顾性/冻结迁移/前瞻图像不交）+ 参数先冻结 + 图像簇 bootstrap 10,000 次；**我们**：分组 K 折替代 index%2，折外选、折内评，报各折阈值分布 | 【一手】 |

---

## 8. ≤8 条最小改动清单（按投入产出排）

> 前置：以下改动**只动判据与记录**，不新增模型调用；改动 8 需要额外 API 调用，单列。
> 预期数一律标注**依据**；标"待测"的一律是**必须在 A 半重扫**才能填的，不允许先写进对外数字。

| # | 改哪个文件 | 做什么 | 预期哪个数变多少 |
|---|---|---|---|
| 1 | `src/minicode/ground/channel.py` L638–658 | **把门控量从 ‖精定位 − `plan.center`‖ 换成 ‖精定位 − `coarse`‖₂**（= 已有的 `zoom_consistency_px`，`channel.py` L727–732）。参考点从"crop 窗口中心"改成"粗定位点"，crop 被 shift 时不再把偏移算进偏离 | 贴边（shift）样本的误回落消失；回落率从 **43.7%** 下降（幅度待 A 半重扫）。**阈值口径与 ZoomClick ε₁ 对齐** |
| 2 | `src/minicode/ground/gate.py` L194–206 | `DEFAULT_DEVIATION_TOLERANCE_PX` 保持 **50.0**，但 `DEFAULT_DEVIATION_SOURCE` **改写**：注明「ZoomClick 50.7 px 定义的是 ‖粗−精‖₂、不是离 crop 中心」，并附 LFPR 换算 47.5 px（192 crop，c≈247.5）与本仓 c=260.4 | 不改数值；**改的是依据的真实性**。避免"引 A 的数卡 B 的量" |
| 3 | `src/minicode/ground/channel.py` L642–658 + `src/minicode/eval/holdout.py` | **回落目标由 `coarse` 改为 `refined`**（即该门控**降级为记录器**：判据继续落盘，但不再改落点）。依据本仓重放：被判丢弃的 30 条里**精定位 18/30 命中、粗定位仅 12/30** | 净 **≈ +2.6 pp**（与自家 t=∞ 平台一致）；精定位命中数不减 |
| 4 | `src/minicode/ground/channel.py`（新函数）+ `src/minicode/ground/gate.py`（新参数 `refine_containment_margin_px`） | 加**点版包含护栏**（LFPR IoU≥0.25 的点式替身）：粗定位**出框**时，精定位点必须落在粗定位框内（物理像素）或距框边 ≤ margin，否则判"选错元素"；粗定位只给点 → **不可判定**，按现有 `unknown` 口径落盘 | 把"挑错元素"（中位 **226 px**）拦下、让"找对元素"（中位 **8.9 px**）通过 → 净正；**具体 pp 待测**（需要新的记录批次） |
| 5 | `src/minicode/eval/holdout.py`（`Split` / `tune_gate_params` / `DEVIATION_CANDIDATES`） | 切分从 **index%2** 改为**按 app/platform 分组 K 折**；候选网格扩到 {24,32,40,48,56,64,80,96,128,∞}；**沿用现有"悲观下界最大、并列取最大容差"规则**折外选阈；**报告各折选出的阈值分布**（中位数+极差） | 阈值估计的偏倚从"构成差 −5.63 pp"降到一个可报的小量；**阈值具体值待测** |
| 6 | `src/minicode/eval/holdout.py` + `src/minicode/ground/caliber.py` | 引入 **LFPR 式证据分层标签**（`retrospective` / `frozen-transfer` / `prospective`）：A 半调完即冻结 → 产物写明冻结时刻 → 在**新采、图像不交**的真机截图上做一次前瞻确认；统计单位改**图像簇**（同屏多图归一组） | 把现在"零验收证据"变成**可对外引用的一层证据**；不改变命中率 |
| 7 | `src/minicode/ground/channel.py` `_plan_fields`（L717–733） | 落盘时给**新判据量**也补一个相对刻度 `deviation_c1000 = ‖精定位−粗定位‖₂ × 1000 / crop 宽`（192 crop 系数 5.208；50 px ⇔ c≈260.4，LFPR 47.5 px ⇔ c≈247.5），并在 `gate.py` 里把 c 刻度写进 `*_source` | 换 crop 尺寸时**不用重调阈值**（按 c 换算）；为 §6.3 的跨尺寸可比提供底座 |
| 8 | `src/minicode/ground/channel.py`（新增采样分支，**需额外 API 调用**） | **GUI-RC 式多采样共识作参考点**：在 crop 上 T=0.5 采 K=8~16 次（不必 64），每个点扩张成 **50 px** 见方投票，取最高票连通区中心作**参考点**，再让距离判据去问"精定位是否与共识一致" | 治"挑错元素"的公开手段；**但公开增益在点式模型上 ≈0（GUI-RC 自己的 Table 1），故只当参考点、不当最终答案**；成本 +8~16 次调用/样本 → **仅在 A 半小批量实测后再决定是否上生产** |

**不建议做**（有公开反证或口径不匹配）：
* **逐坐标中点融合**（LFPR 的 ½+½）：我们的两候选可能分属两个元素（错选中位 226 px），中点会落在空隙，命中两边皆无。
* **照抄 GUI-RC 用投票直接替换最终答案**：公开数字显示点式模型上增益 ≈0（UGround SSv2 +0.00、InternVL3-8B SSPro −0.82）。
* **直接把 50 px 当最终答案**：那是 ZoomClick 在"两个独立视角"上的量；我们的两个预测同源，且自家数据说 50 px 在当前量上丢弃 43.7%。

---

## 9. 证据强度与**未能证实**清单（诚实交代）

**【一手】**（我实际打开并读到的）
* ZoomClick §3.2.1/§3.2.2/§3.2.3 公式与文字；ZoomClick README 的 `--prezoom_px_thresh 50`、`--in_ratio 0.5`、`--in_depth 3`、`--in_min_crop 768`、`--patch_size 2`、`--center_mode clip`。
* GUI-RC §3.2 投票机制（含 α 展开式 (5)、共识提取式）、§4.1 命中口径、§4.2 超参（K=64, T=0.5, top_p=0.95, α=50）、Table 1 的框式/点式行。
* LFPR §3.2 路由分桶（s<128 / 128–256 / >256；路由一致率 88.85%/89.83%）、§3.3 三道护栏与中点融合式 (4)(5)(6)(7)、γ=2.0、§4.1 三层证据与"参数先冻结"、§4.4 图像簇 bootstrap 10,000 次、摘要全部数值。
* 本仓代码：`ground/channel.py`（判据实现与 `zoom_consistency_px`/`zoom_consistency_c1000`）、`ground/gate.py`（50 px 常量与既有来源串）、`coord/crop_refine.py`（crop 以粗定位点为中心）、`ground/channel.py` L162–163（192×192）、`eval/holdout.py`（A/B 切分、`tune_gate_params`、`DEVIATION_CANDIDATES`）。

**【二手】**（未能直读表体，依赖二手转述）
* ZoomClick **Table 11** 的具体数值（50.7 px / 91.8% / 正确对中位 <10 px / 错误对中位 >50 px）——**这是本仓 `gate.py` 既有引用**，检索片段只佐证了"距离定义"与"best pixel threshold that separates correct and error pairs"两处措辞，**表体我没有读到**（arXiv HTML 在 §6.2 之前被截断；PDF 不被本环境 web_fetch 支持，见 §9 末）。
* ZoomClick 附录 §6.2 的**扫描取值网格**与**最优值表格**（未取到）。
* sklearn `TunedThresholdClassifierCV` 正文（只拿到导航页）。
* 检索中出现但**我未核验**的相关工作（仅作线索，不作依据）：SafeGround arXiv:2602.02419（"阈值以 FDR 控制校准"）、MHVF arXiv:2610.17792（验证-精化）、UI-Zoomer arXiv:2604.14113（不确定性驱动缩放）、Element-wise Contrastive Learning arXiv:2610.13268（错误落点大多落在非目标元素上）。

**【推断】**（我基于一手材料做的换算/适配）
* LFPR 的 z ≤ 0.35 → 我们 192 px crop 上 **47.5 px**（z 定义为 crop 归一化距离 ÷ 单位正方形对角线；假设我们的 crop 为正方形且参考点为 crop 中心时的等价换算）。
* ZoomClick 的 50 px 可跨 crop 尺寸搬运（因为它定义在**原图空间**的两次预测之差上，受 crop 大小影响的是"元素占比"而非距离刻度）。
* "我们的两个预测同源 → 距离判据的判别力弱于 ZoomClick"这一推论（由 ZoomClick 的两视角定义 + 本仓 18/30 vs 12/30 的重放共同支持）。

**环境限制**：`web_fetch` 对 `arxiv.org/pdf/...` 返回 `unsupported content type "application/pdf"`，对长 HTML 在附录前截断（arXiv HTML 与 ar5iv 两个镜像都在 §5.2/§5.3 之前截断）。因此 ZoomClick 的 §5.3 消融（with/without pre-zoom 的具体数值）与 §6.2 的阈值扫描表**没有拿到**。

---

## 10. 一句话给决策者

**别再调那个数了，先把"量的口径"和"回落目标"改对**：判据量改成 ‖精定位 − 粗定位‖₂（代码里已经算好，只是没用），阈值先验 80~128（公开锚点 50 只适用于"两个独立视角"的情形），回落不要再退到粗定位点（那一批里精定位 18/30 反而更准），另加一条"精定位点是否落在粗定位框内"的包含护栏来真正拦住"挑错元素"；阈值用**按 app 分组的 K 折**在 A 半选、B 半只评一次，冻结后再在一批新采图上做一次前瞻确认，最后产物按 LFPR 的三层证据打标签。

---

## 11. 参考 URL 清单

**一手（论文 / 官方仓库 / 官方文档）**
1. ZoomClick 论文 HTML：<https://arxiv.org/html/2512.05941v1>（§3.2.1 见 <https://arxiv.org/html/2512.05941v1#S3.SS2.SSS1>）
2. ZoomClick 摘要页：<https://arxiv.org/abs/2512.05941>
3. ZoomClick 官方仓库（README 含 `--prezoom_px_thresh 50`）：<https://github.com/Princeton-AI2-Lab/ZoomClick>
4. GUI-RC 论文 HTML：<https://arxiv.org/html/2508.05615v1>（§3.2 空间投票见 <https://arxiv.org/html/2508.05615v1#S3.SS2>）
5. GUI-RC 摘要页：<https://arxiv.org/abs/2508.05615>
6. GUI-RC 官方仓库：<https://github.com/zju-real/gui-rcpo>
7. LFPR 论文 HTML：<https://arxiv.org/html/2608.19553v1>（§3.3 护栏与融合见 <https://arxiv.org/html/2608.19553v1#S3.SS3>）
8. LFPR 摘要页：<https://arxiv.org/abs/2608.19553>
9. LFPR OpenReview：<https://openreview.net/forum?id=VzKjKDO8Ov>
10. scikit-learn `TunedThresholdClassifierCV` 官方文档：<https://scikit-learn.org/stable/modules/generated/sklearn.model_selection.TunedThresholdClassifierCV.html>
11. ScreenSpot-Pro 官方仓库（评测口径/数据）：<https://github.com/likaixin2000/ScreenSpot-Pro-GUI-Grounding>

**二手线索（未核验，仅供后续轮次）**
12. SafeGround：<https://arxiv.org/abs/2602.02419>
13. MHVF：<https://arxiv.org/abs/2610.17792>
14. UI-Zoomer：<https://arxiv.org/abs/2604.14113>
15. Element-wise Contrastive Learning Improves GUI Grounding：<https://arxiv.org/abs/2610.13268>
16. ScreenSpot-Pro 论文：<https://arxiv.org/abs/2504.07981>

**本仓相关文件（只读引用，未修改）**
- `src/minicode/ground/channel.py`（判据 L635–658；`zoom_consistency_px` L727–732；crop 尺寸 L162–163）
- `src/minicode/ground/gate.py`（`DEFAULT_DEVIATION_TOLERANCE_PX` L194；来源串 L195–206）
- `src/minicode/coord/crop_refine.py`（crop 以粗定位点为中心，shift/shrink 语义 L162–230）
- `src/minicode/eval/holdout.py`（A/B 切分、`DEVIATION_CANDIDATES` L115–131、`tune_gate_params` L771–854）

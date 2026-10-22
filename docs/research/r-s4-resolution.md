# S4 · 分辨率与多尺度（性能优先，token 不设上限）

调研线：**(a)** 各家 API 端点的图像缩放规则 · **(b)** 一次请求多图 · **(c)** 原生分块/多尺度 ·
**(d)** 高 DPI 屏上的 GUI grounding · **(e)** 除整屏分辨率外，怎么把模型眼里的目标变大。

证据强度标注：
**【一手】** = 本次直接打开官方页面/源码读到 · **【二手】** = 由检索引擎摘要转述的一手页面（附可打开 URL）·
**【推断】** = 本报告基于公开数字 + 本仓代码推导，未实测。

> web 工具调用预算 12 次已用尽（12/12）。本轮未直连成功的两个 Anthropic 文档页（跨域重定向 + 404）已改用
> 检索引擎对其正文的逐段转述，并标注为【二手】。其余全部为一手或带 URL 的二手。

---

## 0. 一句话结论

**我们这条线真正能动的是「模型眼里的目标像素数」，不是「整屏分辨率」**：公开曲线显示 grounding 精度在
**~2K 最好、4K 反而下降**，服务端又把宽 >2016 的图缩到 2016，所以「送更大的屏」这条路已经封死；
唯一还能拿到确定增益的是 **crop 放大（把目标从 0.71 个 token 格放大到 2~4 格）+ 用多次采样的一致性
（spatial consensus）当护栏**，两者都有公开数字支撑（ReGround 18.9%→40.2%、ScreenSeeker 48.1%、
RegionFocus +28.9pp、UI-Zoomer +13.4pp、MVP 单图 55.0%→多视图聚类 65.3%）。

---

## 1. 推导底座：我们这条线的「分辨率」到底被什么量化

Anthropic 的视觉 token 规则是 **28 px 网格**：`tokens = ⌈W/28⌉ × ⌈H/28⌉`
【二手·https://platform.claude.com/docs/en/build-with-claude/vision-coordinates】。
本仓自己的探针已经验证了这个网格在本端点成立：

| 送出的图 | 期望图像 token = ⌈W/28⌉×⌈H/28⌉ | 实测（本仓产物） |
|---|---|---|
| 1536×960 | 55×35 = **1925** | 1927（`model/image.py:41-42` 注释） |
| 2044×1260 | 73×45 = 3285 | **3240** = 72×45 → 服务端缩到 **2016×1260**（`model/image.py:27-29`） |
| 2016×1260 | 72×45 = **3240** | 3240 |

> 2016 = 72×28，1260 = 45×28 → **服务端的缩放是 28 px 对齐的**，且它的上限恰好是
> 「长边 2016 / 约 3240 token」。**这不是 Anthropic 的规则**（见 §2）。

由此得到本条线的**唯一量化判据**（【推断】，但锚点是一手的 token 公式）：

> **目标在「送出去那张图」里的像素高度 H_target 决定它值不值一个 token 格。**
> - H_target < 28 px → 目标落不满一个视觉 token，模型看到的是「一个格子里的一团糊」；
> - H_target ≥ 28 px → 至少占满一格；≥ 56 px → 占满两格。

代入我们自己的量（用户实测）：源图标中位 **20.0 px**（1536 档 12.0 px ÷ 0.6；2016 档 15.75 px ÷ 0.7875，
两者一致）。于是：

| 送模型的那张图 | 图标中位像素 | 占 token 格 | 判断 |
|---|---|---|---|
| 整屏 1536 档 | 12.0 px | **0.43 格** | 亚格 |
| 整屏 2016 档 | 15.75 px | **0.56 格** | 亚格 |
| **当前 crop（1:1 原生 1280×800）** | **20.0 px** | **0.71 格** | 仍亚格 |
| crop ×1.5 → 1920×1200 | 30.0 px | **1.07 格** | 刚过线 |
| **crop ×3 → 1920×1200（窗口 640×400）** | **60.0 px** | **2.14 格** | 建议目标 |
| crop ×5.6 → 1920×1200（窗口 360×225） | 112 px | **4 格** | 上限 |

**关键结论：只把 crop 窗口缩小是没用���**——1:1 的 crop 不改变目标像素数，
**必须重采样放大**才买得到 token 预算。这解释了为什么「送 2560 整屏」也只能把 0.56 格抬到 0.71 格，
而 crop×3 能到 2.14 格。

---

## 2. (a) 各家 API 端点的图像缩放规则

| 提供方 | 规则原文要点 | 能否「不缩放」/ 可指定上限 | 证据 |
|---|---|---|---|
| **Anthropic（Claude 文档）** | 缩到**同时**满足 `max_edge` 与 token 预算的最大等比尺寸；token = ⌈w/28⌉×⌈h/28⌉；缩放后**右侧/下侧补齐到 28 的倍数**；**标准档** max_edge=1568 / max_tokens=1568；**Claude 4.7+ 高分辨率档** max_edge=2576 / max_tokens=4784。官方例子：1920×1080 → **1456×819**（不是 1568 宽）；A4@130dpi 1075×1520 → 924×1307（token 预算先绑）。文档明确说：**截图类图片几乎总是被 token 预算而不是边长约束**；官方给的是 `resized_size()` 参考实现，**不要手工按边长缩放** | **可以「指定上限」**（高分辨率档 2576/4784），但**不能要原生不缩放**；坐标要按**缩放后**尺寸归一化 | 【二手·https://platform.claude.com/docs/en/build-with-claude/vision-coordinates 、 https://platform.claude.com/docs/en/build-with-claude/vision 】（本次直连被跨域重定向挡住，正文经检索引擎转述） |
| **OpenAI（GPT-4o/4.1 视觉）** | 三步：①缩进 **2048×2048** 方框 → ②再缩到**短边 768** → ③切 **512×512** tile；计费 `85 + 170×tiles`。**GPT-5 家族改成 patch 预算**（32×32 patch，GPT-5.x @ detail=high 预算 2500） | **永远会缩**，没有「不缩」开关；`detail` 只有 low/high/auto | 【二手·https://platform.openai.com/docs/guides/vision 】 |
| **Qwen2.5-VL** | 客户端 `min_pixels` / `max_pixels`（默认 28×28×16 ~ 28×28×576）**或** `resized_height/width` 精确指定；原生动态分辨率 ViT；grounding 输出**绝对像素**（按实际输入尺寸） | **完全客户端可控**（可选精确尺寸、可指定上限）；官方强调「默认走原生分辨率，更高分辨率更好」 | 【二手·https://github.com/QwenLM/Qwen2.5-VL 】 |
| **Qwen3-VL** | 原生动态分辨率（不强制缩放），但 grounding 坐标回到 **0~1000 归一化** | 不缩放 ✓，坐标是归一化 | 【二手·https://github.com/QwenLM/Qwen3-VL 】 |
| **MiniMax（Anthropic 兼容端点）——我们真正用的那个** | 官方文档**没有公布任何单边像素上限**；只公布：图片 ≤10 MB、请求体 ≤64 MB、支持 JPEG/PNG/GIF/WEBP；**`detail` 三档 token 预算**：`low` 几百（≤~600）、`default` ~1k–3k（≤~5k）、`high` 几千（可达 ~15k+）；提供 **`POST /anthropic/v1/messages/count_tokens`** 做精确 token 预估；**`temperature` / `top_p` / `top_k` 明确「Ignored」**（M3.1-Flash 恒为 1） | **官方层面没有像素上限** → 说明我们观察到的 **2016 上限来自网关/供应商内部**，不是公开契约 | **【一手】**·https://platform.minimax.io/docs/api-reference/text-anthropic-api （本次直接读到全文 .md） |

**本条线的两个直接推论：**

1. **2016 不是任何一家的公开规则**（Anthropic 是 1568 或 2576，OpenAI 是 2048 盒，Qwen 是客户端可控）。
   MiniMax 官方文档里**唯一**能改变图像 token 预算的旋钮是 **`detail`**。
   → **可执行探针（3 次请求、零生成、零随机性）**：用 `POST /anthropic/v1/messages/count_tokens`
   （或本仓 `calib/token_probe.py` 的判定口径）分别送 **1792 / 2016 / 2048 / 2560** 宽，
   `detail` 各跑 `default` 与 `high`，看**图像 token 是否等于 ⌈W/28⌉×⌈H/28⌉**。
   - 若 `detail=high` 下 2560 宽**不被缩**（期望 92×58 = **5336** token）→ 整屏可以按 **1:1 物理像素**送，
     图标中位 15.75 → **20.0 px**（0.56 → 0.71 格）。
   - 若 `detail=high` 也不解锁 → 2016 就是硬上限，本条线的全部希望就压在 crop 放大上。
   - 官方 `count_tokens` 端点让这件事**不花钱、不花时间**。
2. **MiniMax 官方文档写明 `temperature` 被忽略**——与我们「同图同提示词连发 10 次只有 10% 原文一致」互相印证
   【一手 + 自测】。这直接支持本线的核心建议：**用「多次采样的一致性」代替「距离阈值」做护栏**（§3.4）。

---

## 3. (b)(c)(e) 多图 / 分块 / 放大：公开实现与数字

### 3.1 官方协议层面：一个请求里能放几张图？

- Anthropic 与 MiniMax 的 Anthropic-兼容端点都接受**多个 `type="image"` 块**（URL 或 base64），
  实用上限是 **64 MB 请求体**；没有「每请求图片数」的限制条款。
  【一手（MiniMax 文档全文）·https://platform.minimax.io/docs/api-reference/text-anthropic-api 】【二手（Anthropic 多图输入）·https://platform.claude.com/docs/en/build-with-claude/vision-coordinates 】

- **但多图 ≠ 更高分辨率**：多送几张图只会增加上下文，**不会把 crop 里的目标像素变大**。
  公开的多视图方法（MVP、RegionFocus）之所以有效，靠的是**crop 本身被放大**或**多视图聚合**，
  不是「同请求里多图」这件事本身。→ **「一次送整屏 + 若干 crop」不能解决 icon 子集问题**（§5 的取舍表）。

### 3.2 MVP（CVPR 2026）· 多视图 + 坐标聚类 —— 最值得照抄的一份

**MVP: Multiple View Prediction Improves GUI Grounding**（Zhang et al., CVPR 2026, arXiv 2512.08529, code `github.com/ZJUSCL/MVP`）
【二手·https://openaccess.thecvf.com/content/CVPR2026/papers/Zhang_MVP_Multiple_View_Prediction_Improves_GUI_Grounding_CVPR_2026_paper.pdf 】

- 做法（**训练无关，推理期**）：用「指令-视觉注意力」挑 **m 个子视图**，与**整屏图**一起问模型，
  得到 m+1 个坐标；用 **Multi-Coordinates Clustering** 取**最密簇的质心**作为最终答案，**τ = 14 px**。
- 公开数字（ScreenSpot-Pro）：

| 配置 | SS-Pro |
|---|---|
| Qwen3VL-8B 单张整屏 | **55.0%** |
| 坐标平均聚合（2 视图） | 61.0% |
| 随机选择（2 视图） | 60.1% |
| **多坐标聚类（2 视图）** | **65.3%** |
| 视图提案：Attention-Guided | 65.3% |
| 视图提案：Border Padding | 60.2% |

- **视图数消融（补充材料 Table 10）**：聚类准确率 **2 视图 61.0 → 4 视图 61.7 → 10 视图 60.6**，
  而 **Pass@N 上界 69.0 → 70.2 → 73.0** 持续上升。
  → **关键洞见：多视图的瓶颈不是「看得清」，是「选得对」**；多给视图只会提高上界，不会自动提高命中率。
  → 对我们的用法：**τ = 14 px 的空间聚类**是一条可以直接抄的「一致性护栏」判据。

### 3.3 ScreenSpot-Pro 官方基线（我们自己的评测集）—— 裁剪/缩放这条路的公开数字

**ScreenSpot-Pro**（arXiv 2504.07981, ACM MM'25 Oral）§4.3 给的四个「多轮缩小搜索」基线
【二手·https://arxiv.org/abs/2504.07981 ；ACM 版 https://dl.acm.org/doi/10.1145/3746027.3755688 】

| 基线 | 机制（可照抄的细节） | 公开数字 |
|---|---|---|
| **Iterative Zooming** | 先整屏定位 → **2 行×2 列切块** → 取含预测的那块递归 | 表 2（本次未取到逐项数值） |
| **Iterative Narrowing** | 同上但**以预测点为中心裁半尺寸**，**迭代 3 次** | 表 2 |
| **ReGround** | **围绕第一次预测裁一块再重新定位**，**裁剪尺寸手工调到「模型的最佳输入尺寸」** | **40.2%**（基座 OS-Atlas-7B 单次 18.9%） |
| **ScreenSeeker** | GPT-4o planner 推最可能区域 → 逐步裁掉干扰（训练无关） | **48.1%** |

- 数据侧的关键事实：目标 bbox 平均只占截图 **0.07%** 面积（原 ScreenSpot 是 2.01%，约 29~30× 更小）；
  截图分辨率 >1920×1080 且**关闭了系统缩放**；构成为 2560×1440 32.4%、3840×2160 17.0%、3840×1080 11.4%、
  3456×2234 10.2%、5120×2880 4.4%、5120×1440 3.9%。
  → **本机 2560 宽 + 150% DPI 的图标像素尺寸天然比这个基准大**（§6）。

### 3.4 放大（zoom）类方法：公开增益与「什么时候别放大」

| 方法 | 做法 | 公开数字 | 出处 |
|---|---|---|---|
| **RegionFocus**（ICCV'25） | 先出一个**焦点**，再围绕焦点生成**候选裁剪框**（固定尺寸/宽高比启发式：0.5×0.5、0.3×0.3、0.4×0.8、0.8×0.4），失败时用 VLM self-judge 触发；历史焦点用粉色星标画回图上 | **Qwen2.5-VL-72B → 61.6%** SS-Pro（**+28.9pp**）；UI-TARS-72B **+31.7pp**；UI-TARS-7B+RegionFocus 超过裸 UI-TARS-72B | 【二手·https://arxiv.org/abs/2505.00684 ；CVF https://openaccess.thecvf.com/content/ICCV2025/papers/Luo_Visual_Test-time_Scaling_for_GUI_Agent_Grounding_ICCV_2025_paper.pdf ；code https://github.com/tiangeluo/regionfocus 】 |
| **UI-Zoomer** | **训练无关**；置信度门控（**空间一致性 spatial consensus + token 级置信度**）决定是否放大；裁剪尺寸由**全方差定律**推出；专攻「小 icon + 密集布局」 | **+13.4pp** SS-Pro、+10.3pp UI-Vision、+4.2pp SS-v2 | 【二手·https://huggingface.co/papers/2604.14113 ；http://zjureal.com/UI-Zoomer/ 】 |
| **AdaZoom-GUI** | 指令改写 + **条件放大**（只对「预测为小元素」的做第二遍） | **无条件放大反而掉分**：SS-v2 0.943 → **0.916**；条件放大 0.945 | 【二手·https://huggingface.co/papers/2603.17441 】 |
| **DiMo-GUI** | 动态迭代放大 + 模态解耦（文本 vs icon 分开看） | 放大量过头会回落；模态感知放大对「歧义 icon」帮助最大 | 【二手·https://ar5iv.labs.arxiv.org/html/2507.00008 】 |
| **GUI-Spotlight**（ICML'26） | 训练模型学会**自己决定裁剪/四分/按颜色搜** | 52.8% SS-Pro（仅 18.5K 样本） | 【二手·https://arxiv.org/abs/2510.04039 】 |
| **「GUI Grounding 实践指南」**（第三方横评） | 横评 Gemini-3-Pro / Claude-Sonnet-4.5 / Seed1.8 / Kimi-K2.5 / MAI-UI | **Zoom-In 把 ScreenSpot-Pro 从 35.0 → 54.0（Claude）、39.0 → 69.7（Gemini）**；同文指出 **SoM 在密集 UI 上全线 <30** | 【二手·未取到稳定 URL（检索引擎转述的 CSDN 综述）—— **弱证据，落地前需自行复核**】 |

### 3.5 分辨率-精度曲线：为什么「送更大的屏」是死路

- 综述型论文的原文表述：**「OSWorld-G 用严格标准化的 1080p，提到 1080p 以上就饱和；而 ScreenSpot-Pro
  含大量原生 4K 截图，**分辨率提升到 2K 有收益，但在 4K 会下降**。整体上分辨率对 grounding 的影响
  很大程度上取决于基准的数据分布。」**
  【二手·https://openreview.net/pdf?id=yBFUqdJFZn 】
- MEGA-GUI 的**按分辨率分桶消融**（ScreenSpot-Pro）：极端 4K+ 桶（3840×2160 / 5120×2880 / 6016×3384）
  平均 **9.96 / 17.80 / 80.06**（原文三列口径）——4K 档明显塌陷。
  【二手·https://arxiv.org/abs/2511.13087 】

→ **我们已经在 ~2K 附近（2016），继续送 2560/4K 是逆着曲线走；唯一还能拿的是「把目标放大」而不是「把屏放大」。**

---

## 4. (d) 高 DPI（150%）屏上的 GUI grounding

- **Windows 坐标规则（公开、可照抄）**：进程设 `PER_MONITOR_AWARE_V2` 后，
  `GetWindowRect` / `BitBlt` / **UIA 的 bounding rectangle 全部是物理像素**；
  `scale = GetDpiForWindow(hwnd)/96`（150% → 1.5）。
  **经典坑**：把「UIA 的物理矩形」和「逻辑分辨率截图」混用 → 落点偏几百像素；
  或对已经是物理的矩形**再乘一次 dpi/96**。
  【二手·https://github.com/trycua/cua/commit/fbc4a21b7f0dd6a7340a94d4c70d067b3f0ef96f 、
  https://github.com/trycua/cua/commit/b286d9ccd8ea23d40da9e211dd5ad67fa69cd6a8 、
  https://pypi.org/project/precision-desktop/ 】

- **本仓现状（已合规）**：`exec/dpi.py` 导入即 `SetProcessDpiAwarenessContext(PER_MONITOR_AWARE_V2)`；
  `action/surface.py` 对 `capture_to_physical_scale != 1.0` 直接告警；
  `coord/test_coordinates.py:213-215` 有测试「PMv2 路径：截图就是物理像素，scale 必须恒为 1.0」。
  → **DPI 这一层的坑本仓已经踩不到了，本轮不需要改 DPI 代码。**

- **150% 对我们的净影响（【推断】，可实测）**：Windows 在 150% 下把同样的名义尺寸元素**画成 1.5× 物理像素**，
  而 ScreenSpot-Pro 的截图**关闭了系统缩放**。所以：
  - 在 SS-Pro 上源图标中位 20.0 px；
  - **在本机（150%）同一名义图标，物理截图里应约 30.0 px**；
  - 也就是说 **真机上的 icon 难度低于 SS-Pro 的 icon 难度**，
    SS-Pro 的 icon 子集数（41.38%）**对真机是悲观代理**。
  - **可执行的核对（一次，10 分钟）**：对同一个图标，比较 UIA 给的 rect 尺寸与它在物理截图里的可见像素尺寸；
    比值 ≈1.5 就证实了上面的推断（并顺带验证没有混用逻辑/物理坐标）。
  - **未验证的副作用**：DPI-unaware 的老应用会被 Windows **位图拉伸** → 边缘模糊的 icon 对模型更难
    （【推断】，无公开数字）。若要排除，可在任务栏图标与某个 per-monitor-v2 应用图标之间比对锐度。

- **公开的「高 DPI grounding」专门方法**：**没有找到**任何专门针对「150% 及以上 DPI 屏的 GUI grounding」的
  公开工作或基准（【二手】——检索未命中）。最接近的公开基准 ScreenSpot-Pro 明确**关闭缩放**，
  因此「高 DPI 下的 grounding 精度」在公开文献里基本是空白 —— **这一项属于我们自己的原创测量点**，
  建议在本轮把它测出来（见 §7 最小改动 #8）。

---

## 5. (e) 除了整屏分辨率，怎么把「模型眼里的目标」变大 —— 结论表

| 手段 | 目标像素会变大吗 | 公开支撑 | 对我们**是否适用** |
|---|---|---|---|
| 整屏送更大（2560 / 4K） | 是（15.75→20 px），但**仍 <28 px 一格**，且撞 2016 上限、逆着分辨率曲线 | 2K 最好、4K 下降（OpenReview 综述） | **不适用**（上限 + 曲线） |
| **crop 后重采样放大**（窗口按粗框定，×2~5） | **是（20 → 60~112 px，2~4 格）** | ReGround 40.2%、ScreenSeeker 48.1%、RegionFocus 61.6%、UI-Zoomer +13.4pp、Zoom-In 35.0→54.0 | **直接可用**（首选） |
| 只把 crop 窗口缩小（1:1 不放大） | **否**（仍是 20 px） | — | **无效**（§1 的关键推导） |
| 一个请求里送整屏 + 若干 crop | **否**（crop 仍是 1:1，20 px） | MVP 65.3% 的增益来自**多视图聚合**而非多图本身 | **不解决 icon**，只解决「选哪个视图」 |
| 多 crop（2×/4×/8× 粗框）+ 聚类 | **是**（每个 crop 各自放大） | MVP：单图 55.0 → 聚类 65.3；4 视图 61.7 | **直接可用**（第二优先） |
| 2×2 原生分块（2560×1600 → 4 块 1280×800，1:1） | 20 px（0.71 格）—— 只比整屏 2016 档好 27% | ScreenSpot-Pro 的 Iterative Zooming 就是 2×2；MVP 多视图 +10.3pp | **可做一臂**，但性价比低于「crop 放大」 |
| Qwen 的 `min_pixels/max_pixels`、OpenAI `detail` | — | Qwen 客户端可控 | **不适用**（我们控制不了对方的编码器；且模型已定） |
| Set-of-Mark / OmniParser | 不解决「目标太小」，改造成「编号多选」 | OmniParser V2 39.5% SS-Pro；但 2026 横评称 SoM 在密集 UI 全线 <30 | **不适用**（我们没有元素检测器，且模型是通用 API 模型） |

---

## 6. 落到本仓的最小改动（≤8 条，按投入产出排序）

> 验证命令沿用本仓现有 CLI 形态：`python -m minicode.eval --dataset .scratch/sspro --out runs/<名字>
> --seed 20251020 --per-stratum 55`；`--crop-width/--crop-height` 已存在，`--memo-cache` 已存在
> （可让两轮**共用同一次粗定位回答** → 真配对）。

| # | 改哪个文件 | 做什么（可直接照抄的参数） | 预期哪个数变多少 | 怎么验证 |
|---|---|---|---|---|
| **1** | 新增 `src/minicode/calib/acuity_probe.py`（或 `.scratch/acuity_probe.py`） | **测模型的目标像素 acuity**：在仿真截图上画 12/16/20/24/28/36/48/64/96 px 的图标，每尺寸 n=10 次，问「点这个图标」→ 得到「最小可靠尺寸 K(px)」 | 0 准确率变化，但**决定 #2 的放大倍率取值**（K=32 → ×1.6 够；K=64 → 需 ×3.2）。【推断】 | 落盘 `runs/acuity/k.json`；K 取 90% 命中率的下界 |
| **2** | `src/minicode/model/image.py` + `src/minicode/ground/channel.py`（`_locate_by_point` 裁剪段） | 新增 `upscale_frame()`（`downscale_frame` 现在**只许缩**，见 `image.py:86-89`）；裁剪几何改为：**窗口 = clamp(max(4×粗框宽, 4×粗框高), 320, 1280) 物理 px**（且 ≥2×目标半径+64），**upscale = clamp(2016/窗口, 1, 6)**，且**目标表观像素 ≤ 112**；送出的图宽 ≤2016（与 `SERVER_MAX_INPUT_WIDTH_PX` 对齐，服务端不再缩） | icon 中位 **15.75→60 px（0.56→2.14 格）**；**icon 子集 41.38% → 45~60%**、整体 64.48% → **70~80%**【推断；锚点 ReGround 18.9→40.2 / ScreenSeeker 48.1 / RegionFocus +28.9pp / UI-Zoomer +13.4pp】 | `runs/g4-eval-zoom/sspro-report.json` 的 icon 分层 + 整体；crop 含住率 79.5% → **≥85%**（窗口按粗框定，应更高） |
| **3** | `src/minicode/ground/channel.py`（精定位段） | **自洽投票当护栏**：同一张（放大的）crop **问 N=5 次**（effort=low），把 5 个点做**空间聚类**（τ 起点 28px = 1 格，**先用 183×1 次探针量抖动 p90 再定**）；最大簇 ≥3/5 → 用**簇心**；否则**干净回落到粗定位**（沿用现有 `FallbackCause` 通道） | **假命中 20 → 5~10**；「粗定位本来对却被改坏的 11 条」→ **3~5**；整体 **+3~8pp**【推断；锚点 MVP τ=14px 聚类、UI-Zoomer 的 spatial consensus 门控】 | 同上报告的 fake-hit 计数 + **配对 McNemar**（两臂共用 `--memo-cache` 的同一次粗定位回答） |
| **4** | `src/minicode/ground/prompt.py`（`refine_prompt`） | 现有文案「（**未缩放**，1 像素 = 1 屏幕像素）」在放大后**变成假话** → 改成「这张图是 W×H（由屏幕上一块区域**放大 N 倍**而来，1 像素 = 1/N 屏幕像素）」；坐标契约仍是「图内坐标」，由 `capture_scale_x = model_input_width/capture_width` 承担换算（`coord/coordinates.py:269-276`） | 0（**正确性护栏**：不改这行 = 静默偏移一个放大倍数的系统性误差） | 落盘 `GroundingEcho.sent_width/height` 必须**等于实际送出的 PNG 的 IHDR 宽高**；在 `eval/__main__.py` 里加一条断言（它已经记录 `server_model_input_size`） |
| **5** | `src/minicode/eval/__main__.py` | 加 `--model-input WxH`（照抄 `ground/__main__.py:75` 的写法），让**档位是 CLI 参数而不是源码常量** `MODEL_INPUT_MAX_WIDTH_PX`（`model/image.py:45`）；配合已有的 `--memo-cache` 让不同档位**共用同一次粗定位回答** | 0 准确率变化；把「两次独立跑批」变成**真配对**，这是本轮所有 A/B 的可信前提（用户已指出 64.48% vs 55.74% 混着随机性） | 报告里 paired 段（McNemar）从「不可判定」变成可判定 |
| **6** | `src/minicode/calib/token_probe.py` | 加 `detail` 扫描：宽 {1792, 2016, 2048, 2560} × `detail ∈ {default, high}`，判定口径沿用探针现有准则（**图像 token 必须等于 ⌈W/28⌉×⌈H/28⌉**）；MiniMax 官方还提供 `POST /anthropic/v1/messages/count_tokens`（零生成零费用） | 若 `detail=high` 解锁 **2560 1:1**：icon 中位 **15.75→20.0 px**、icon 子集 **+2~6pp**【推断；且公开曲线提示 2K 之后收益递减】 | `runs/m6-token-probe-sweep2/token-probe.json` 同格式产物；判定「相等 ⇒ 该档可信」 |
| **7** | `src/minicode/ground/channel.py` | **多尺度 crop 聚合**：对粗框生成 **2×/4×/8×** 三档窗口，各放大到 ≤2016 宽，各问 3 次 → **9 个点一起聚类**（τ 同 #3）→ 簇心为最终落点；无簇则回落粗定位 | 整体 **+2~5pp**【推断；锚点 MVP 单图 55.0→65.3、4 视图 61.7】 | 同一份报告里作为第三个 arm（`always_zoom3`） |
| **8** | `src/minicode/exec/` 或新脚本（真机侧） | 在真机上量一次「150% DPI 下图标在物理截图里的实际像素尺寸」（对比 UIA rect 尺寸 vs 截图可见尺寸，比值≈1.5 即证实） | 0 准确率变化；**决定 SS-Pro 的 icon 数能不能代表真机**（若真机图标 ≈30px，则 icon 优化在真机上的收益远小于在 SS-Pro 上） | 落盘一条 `dpi_icon_scale` 记录；与 SS-Pro 的 20.0 px 中位并列进报告 |

---

## 7. 明确**不**建议的做法（附理由）

1. **送更大的整屏（2560 / 4K）**：撞 2016 上限（服务端缩放 → 坐标契约断），且公开曲线显示 2K 之后收益递减、4K 下降。
2. **只把 crop 窗口缩小、不放大**：目标像素数不变（1:1），只有噪声变少 —— 与「让目标更大」无关。
3. **一个请求里送整屏 + 若干 crop 来解决 icon**：多图不改变 crop 内的目标像素；且本仓 M14 实测已证明该模型在
   换帧后**容易把坐标口径搞混**（整屏口径 vs 图内口径差 14.0 px）。多图会加剧这个问题。
   → 建议把「多图一次问」当作**单独一个实验臂**（答案 schema 改成 `{"image": k, "bbox": [...]}`），
   **不要**作为主路径。
4. **Set-of-Mark / OmniParser**：需要额外的检测器，且 2026 横评在密集 UI 上 SoM 全线 <30。
5. **继续用「偏离阈值」当护栏**：用户已实测该阈值会误伤 43.7% 的正确答案 → 已被降级为记录器是对的；
   本报告建议用**一致性（聚类投票）**替代「距离」作为判据，两者正交。

---

## 8. 参考 URL 清单（全部可打开）

**一手（本次直接打开）**
- MiniMax Anthropic SDK 文档（`detail` 三档 / `count_tokens` / `temperature` 被忽略）：
  https://platform.minimax.io/docs/api-reference/text-anthropic-api
  （markdown 原文：https://platform.minimax.io/docs/api-reference/text-anthropic-api.md ）

**二手（官方/论文页面，经检索引擎转述；直连 Anthropic 文档时被跨域重定向挡住）**
- Anthropic 坐标与缩放规则：https://platform.claude.com/docs/en/build-with-claude/vision-coordinates
- Anthropic 视觉 token 与高分辨率档：https://platform.claude.com/docs/en/build-with-claude/vision
- OpenAI 视觉缩放/tile 计费：https://platform.openai.com/docs/guides/vision
- Qwen2.5-VL（min_pixels/max_pixels，绝对坐标）：https://github.com/QwenLM/Qwen2.5-VL
- Qwen3-VL（原生分辨率 + 0~1000 归一化）：https://github.com/QwenLM/Qwen3-VL
- ScreenSpot-Pro（论文 / 榜单 / ACM 版）：https://arxiv.org/abs/2504.07981 ·
  https://gui-agent.github.io/grounding-leaderboard · https://dl.acm.org/doi/10.1145/3746027.3755688
- MVP（CVPR 2026，代码+补充材料表 10）：https://openaccess.thecvf.com/content/CVPR2026/papers/Zhang_MVP_Multiple_View_Prediction_Improves_GUI_Grounding_CVPR_2026_paper.pdf ·
  https://openaccess.thecvf.com/content/CVPR2026/supplemental/Zhang_MVP_Multiple_View_CVPR_2026_supplemental.pdf ·
  https://github.com/ZJUSCL/MVP
- RegionFocus（ICCV 2025，代码）：https://arxiv.org/abs/2505.00684 ·
  https://github.com/tiangeluo/regionfocus ·
  https://openaccess.thecvf.com/content/ICCV2025/papers/Luo_Visual_Test-time_Scaling_for_GUI_Agent_Grounding_ICCV_2025_paper.pdf
- UI-Zoomer：https://huggingface.co/papers/2604.14113 · http://zjureal.com/UI-Zoomer/
- AdaZoom-GUI：https://huggingface.co/papers/2603.17441 · https://ar5iv.labs.arxiv.org/html/2603.17441
- DiMo-GUI：https://ar5iv.labs.arxiv.org/html/2507.00008
- GUI-Spotlight：https://arxiv.org/abs/2510.04039
- 分辨率消融（2K 最好 / 4K 下降）：https://openreview.net/pdf?id=yBFUqdJFZn
- MEGA-GUI 分辨率分桶：https://arxiv.org/abs/2511.13087
- Windows DPI（PMv2 / 物理像素陷阱）：https://github.com/trycua/cua/commit/fbc4a21b7f0dd6a7340a94d4c70d067b3f0ef96f ·
  https://github.com/trycua/cua/commit/b286d9ccd8ea23d40da9e211dd5ad67fa69cd6a8 · https://pypi.org/project/precision-desktop/
- OmniParser / SoM（作为「不采用」的依据）：https://github.com/microsoft/OmniParser · https://arxiv.org/abs/2408.00203

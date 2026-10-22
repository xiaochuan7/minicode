# R2 — 该给模型看多大的图？（输入分辨率 / 缩放 / 分块 / 只发区域）

> 调研日期：2026-10-22（Asia/Shanghai）
> 范围：只回答「我们该给模型看多大的图、怎么送、怎么核对模型实际看到什么」，不重述本地已量过的诊断结论。
> 方法：`web_search` + `web_fetch`（共 13 次调用，含 1 次超时后的唯一重试；docs.anthropic.com / platform.claude.com / www.anthropic.com 三次直取均被重定向或 404，故 (a) 的正文引文用检索摘要 + 本仓**已有的一手源码调研**交叉印证）。
> 证据强度标注：【一手】= 直接读到官方源码/论文 HTML/本机实测；【二手】= 官方页面检索摘要或第三方转述；【推断】= 我方推理。
> ⚠️ 只读工作区：除本文件外未改任何代码。

---

## 0. 一句话结论

**Anthropic 的「downscale by 2x」不是缩放建议，而是 HiDPI 坐标对账规则；真正可迁移的是它对「服务端偷偷缩图」的禁令（官方原文：会产生 ~14% 系统性点击漂移，16:10 屏）。对我们这个「通用 API VLM」，证据方向与 Claude 的 XGA 1024×768 建议**相反**：我们该把整屏发到**服务端实测上限 2048 宽**（2560×1600 → 2048×1280，双轴同为 0.8 的等比缩放 → 坐标换算仍是单一常数），并把**「模型输入里的 icon 像素尺寸」**当成一等指标来管。**

支撑这个结论的三条硬事实：

1. **本机实测**：图像 token 数严格等于 `ceil(W/28) × ceil(H/28)`。本仓已有三处独立命中：192×192 裁剪 = 49 token、512×512 裁剪 = 361 token（`ground/channel.py:152-153` 记的实测值）、1280×800 整屏 = 1334 token（`calib/test_calib_probe.py:968`）。→ 这条 28 px 网格规则在本端点**成立**（原码把它标成【二手】Anthropic 规则，本机实测可以升级为一手）。→ 于是**用 token 数反查「模型实际看到多大」是可用仪器**，不再是猜测。
2. **RegionFocus（arXiv 2505.00684，ICCV 2025）**：它不是「downscaling 有害」的消融论文，而是「**放大到原始分辨率**」有效：整屏粗定位 + 0.5×0.5 区域**从原图裁** + LANCZOS 上采样，Qwen2.5-VL-72B 拿下 ScreenSpot-Pro **61.6%**（UI-TARS-7B 平均分 35.7 → 41.2，OS 子集 24.5 → 34.7）。
3. **Anthropic 官方 quickstart 源码**（本仓已 clone 逐行读过）：超限大图会被服务端**再缩一次**，模型随后在一个「我们没观察过的空间」里出坐标 → **~14% 系统性点击漂移（16:10 MacBook 屏）**。→ 「我们自己缩、且声明=所见」这条已经做对了；缺的是**缩到哪一档**。

---

## 1. (a) Anthropic 官方 computer use 文档：「downscale by 2x」原文与前提

### 1.1 能拿到的原文

- 【二手·官方页面检索摘要】<https://platform.claude.com/docs/en/agents-and-tools/tool-use/computer-use-tool>：
  > "Either downscale the screenshot by 2x before sending, or halve the coordinates Claude returns before issuing the click. When you choose a display resolution and …"
  （检索摘要只给到这一句就被截断；直取该 URL 会跨域重定向到 `platform.claude.com`/`www.anthropic.com`，两个域名下的 `/docs/en/...` 路径本次均 404。）
- 【一手·官方源码，经本仓已有调研产物 `docs/research/bottom-drafts/10-anthropic-zoom源码细节.md` 逐行读出并记录了文件路径与行号】
  - `anthropic-quickstarts/computer-use-demo/README.md:167-173`：
    > "We do not recommend sending screenshots in resolutions above XGA/WXGA to avoid issues related to image resizing. **Relying on the image resizing behavior in the API will result in lower model accuracy and slower performance than implementing scaling in your tools directly.**"
    > "When implementing computer use yourself, we recommend using XGA resolution (1024x768): For higher resolutions: Scale the image down to XGA and let the model interact with this scaled version, then map the coordinates back to the original resolution proportionally. For lower resolutions or smaller devices: Add black padding around the display area until it reaches 1024x768."
    URL: <https://github.com/anthropics/anthropic-quickstarts/blob/main/computer-use-demo/README.md>
  - `anthropic-quickstarts/computer-use-best-practices/computer_use/image.py:1-15`（模块 docstring）：
    > "The API's vision encoder tiles images into 28x28 patches and caps both the long edge (1568 px) and the total tile count (1568). If we send an image that violates either, **the server resizes it *again* before the model sees it, and then the model emits click coordinates in a space we never observed, producing systematic click drift (~14% on a 16:10 MacBook screen).** So: pre-resize every screenshot to the largest dimensions that satisfy *both* constraints, and remember those dimensions to scale model coordinates back to screen space."
    URL: <https://github.com/anthropics/claude-quickstarts/blob/main/computer-use-best-practices/computer_use/image.py>
  - `computer_use_best/tools/computer.py`（best-practices）：`display_width_px/display_height_px` 必须等于**实际送出的那张图**的像素。

### 1.2 这句话的前提条件（**关键：跟我们以为的不一样**）

| 前提 | 内容 | 证据强度 |
|---|---|---|
| P1 | **坐标空间 = 你送出的那张图的像素空间**（不是物理屏像素）。这是所有换算的根。 | 【一手】SDK `computer_zoom_input.py`："Region coordinates are in the full-screenshot space (not physical display pixels)." |
| P2 | **「2x」特指 HiDPI/Retina 的 devicePixelRatio = 2**：截图按原生倍率抓到 → 图像像素是逻辑像素的 2 倍 → 你必须把图像按 2 缩小（此时模型坐标 == 逻辑坐标），或把模型给的坐标除 2。**它不是「把图缩小一半更准」的建议。** | 【推断】（由 P1 + 官方 quickstart 的 retina 处理代码 `computer_use/tools/computer.py:279-281`「先降到逻辑尺寸再发模型」推出） |
| P3 | **XGA 1024×768 是给「Claude 的 computer-use 模型」的**：那个模型是在降采样截图上训的，所以降采样反而对齐训练分布。 | 【推断】（Anthropic 原文只说 "we recommend using XGA resolution"，理由在 "Relying on the image resizing behavior in the API will result in lower model accuracy" 这句——针对的是**自家模型的训练分布**） |
| P4 | 服务端上限（标准档）：长边 ≤ **1568 px** 且总 tile 数 ≤ **1568**（28 px tile）。视觉 token ≈ `(w×h)/750`。Claude 4.7+ 高分档：长边 2576 px / ~4784 token。 | 【二手】<https://platform.claude.com/docs/en/build-with-claude/vision>（检索摘要；多站转述一致）；【一手·交叉】官方 `image.py` docstring 独立给出 1568/1568 |

### 1.3 哪些能迁移、哪些不能（**这是本节的核心**）

| Anthropic 说法 | 对我们（minimax-m3.1-flash-free，通用 API VLM，非训过的 grounding 模型） |
|---|---|
| 「坐标在送出图的像素空间」+「别依赖服务端缩图，自己缩并记住尺寸」 | ✅ **完全迁移**，而且我们已经在做（`model/image.py` 的「我们自己缩 + 声明即所见」） |
| 「downscale by 2x」 | ❌ **不适用**：我们是 150% DPI（1.5×）且截图已是物理像素（`capture_to_physical_scale=1.0`），不存在 2× 错配要对账 |
| 「用 XGA 1024×768 / 不超过 XGA」 | ❌ **不适用**（P3）：那条建议的前提是「模型在降采样图上训练过」。我们是通用 API 模型，本地 A/B 已经量到**只改像素密度** 35%→85%（McNemar p=0.0063），方向与 XGA 建议相反 |
| 「超限 → 服务端再缩 → ~14% 点击漂移」 | ✅ **完全迁移，且是最该抄的一条**：我们的 `SERVER_MAX_INPUT_WIDTH_PX=2048` 正是因为实测「2560 宽的图被服务端缩到约 2048 宽」才设的 |

> **纠正一条易被当成引文用的说法**：「Anthropic 建议把截图缩小一半」是误读。原文的 2x 是 **HiDPI 对账**，XGA 是 **Claude 专有训练分布**。我们照抄任何一条都会得到与我们本地实测相反的结论。

---

## 2. (b) RegionFocus（arXiv 2505.00684）到底量化了什么

我直接读了论文 HTML v2（<https://arxiv.org/html/2505.00684v2>，HTTP 200，【一手】）。

### 2.1 机制原文（§3.2 Action Candidate Prediction）

> "If the agent can interact with the environment (e.g., for a webpage), a zoomed-in, high resolution view of the region is provided to the agent. In this case, **at least one side of the region can be made to match the original full image resolution via zooming in.** If environment interaction isn't possible, we simply crop the region from the initial image and upsample it for prediction."

§3.2 Bounding-box Proposal 原文：区域是**固定比例**（默认 0.5×0.5 宽×高）**以焦点为中心**。

→ 这就是「只发目标区域」的标准形态：**从原分辨率图裁 → 等比上采样 → 坐标仍是全屏坐标**。

### 2.2 量化数字（Table 1，ScreenSpot-Pro，【一手】）

| 模型 | Development | Creative | CAD | Scientific | Office | OS | **Avg** | icon avg |
|---|---|---|---|---|---|---|---|---|
| UI-TARS-7B | 36.1 | 32.8 | 18.0 | 50.0 | 53.5 | 24.5 | **35.7** | 16.2 |
| **+ RegionFocus** | 38.5 | 39.6 | 24.9 | 51.2 | 60.4 | **34.7** | **41.2** | 18.7 |
| Claude Computer Use | 12.6 | 16.8 | 11.9 | 25.8 | 26.2 | 8.1 | 17.1 | 7.1 |
| UI-TARS-72B | 40.8 | 39.6 | 17.2 | 45.7 | 54.8 | 30.1 | — | 15.7 |

- 摘要原文：**61.6%**（Qwen2.5-VL-72B + RegionFocus，ScreenSpot-Pro SOTA）；相对两个基座「28+% on Screenspot-pro / 24+% on WebVoyager」。
- 图标列单独看：OS 子集 icon 16.9 → **21.3**；avg icon 16.2 → 18.7。**icon 的涨幅（+2.5~+4.4）明显小于 text**——因为放大主要补的是**小控件的像素**。

### 2.3 重要更正（对交线说法的独立核实）

> 交线给的说法是「RegionFocus 结论是 downscaling 让小控件不可解、反而伤 grounding 精度」。

**逐字核对论文后：RegionFocus 没有做「整屏降采样 vs 原分辨率」的对照消融。** 它的消融（§4.3）是：text-based history / 直接给区域 / SAM / test-time thinking budget——**没有分辨率这一维**。它证明的是**「放大到原始分辨率」比「在整屏降采样图上一次性定位」好**（35.7→41.2）。

→ **【一手】结论**：把「downscaling 有害」当成 RegionFocus 的直接结论是**过度外推**。正确表述是：
- RegionFocus 提供的是「**按原始分辨率给模型局部**」的收益（+5.5 pp avg，OS +10.2 pp，图标子集也涨）；
- 「整屏降采样伤害小控件」的**直接**证据来自我们自己的密度 A/B（35%→85%，p=0.0063）与 ScreenSpot-Pro 论文的「目标 <1% 面积时准确率断崖式下降」。

这个更正不改变行动方向，但**它把依据从「某论文说降采样有害」换成「我们自己的受控 A/B + 公开实现的同一机制」**——更硬。

---

## 3. (c) Qwen2.5-VL 的 `max_pixels` 与 28 px 网格

| 项 | 值 | 来源 | 强度 |
|---|---|---|---|
| `max_pixels`（默认） | **12,845,056 = 16384 × 28 × 28** | Qwen2.5-VL `preprocessor_config.json`；MLX-VLM issue #1175 标题（我直接抓到了该 GitHub issue 页，标题原文："Qwen2.5-VL default `max_pixels` in `preprocessor_config.json` is 12× the training resolution; degrades grounding accuracy"） <https://github.com/Blaizzy/mlx-vlm/issues/1175> | 【二手】数值 / 【一手】issue 标题存在 |
| `min_pixels`（默认） | **3,136 = 4 × 28 × 28** | 同上 | 【二手】 |
| patch / merge | patch 14、merge 2 → **每 token 28×28 px** | Qwen 官方 README（"image_patch_size: 14 for Qwen2.5-VL and 16 for Qwen3-VL"）；Qwen3-VL 另给 `size['longest_edge']=max_pixels` / `shortest_edge=min_pixels` 的显式开关 | 【一手】官方 README |
| 官方建议的 token 区间 | 256–1280 tokens | Qwen2.5-VL 模型卡："You can set min_pixels and max_pixels according to your needs, such as a token range of 256-1280" | 【二手】 |
| 定位鲁棒区间 | 480×480 ~ 2560×2560 | 阿里云 Model Studio 视觉文档 | 【二手】 |
| UI-TARS issue #215 提到的 16384*28*28 | **本次未核实**（无剩余 web 预算） | — | 【未核实】 |

### 3.1 这对本机的三条直接影响

1. **`max_pixels` 若按 Qwen 规则（12.8 M px）**，2560×1600 = 4.1 M px **远在预算内** → 一个 Qwen 家族的后端**不会**把我们的原图缩掉。真正卡住我们的是**本端点实测的 2048 宽上限**（`model/image.py:24-30`），不是像素预算。
2. **28 px 网格在本端点成立 → token 反查是一手仪器**。本仓三处独立实测吻合：`ceil(192/28)²=49`（裁剪 192 实测 49 token）、`ceil(512/28)²=361`（裁剪 512 实测 361 token）、`ceil(1280/28)×ceil(800/28)=46×29=1334`（标定记录的图像 token）。**这把 `calib/calibrate.py:293-295` 里「【二手】28 px 规则未在本机验证」升级为【一手·本机实测】。**
3. **token 数的分辨率天花板 = 28 px/轴**。所以「声明 = 所见」不能靠 token 数证明到**像素级**，只能证明到**一个 patch 格**。真正能保证像素级一致的做法是**别让服务端缩**（Anthropic 的做法）：把送出的长边压在服务端上限内。

### 3.2 各候选档位的 token 表（28 px 网格，本机同款规则）

| 送出的尺寸 | 相对 2560×1600 的线性比 | 图像 token | 备注 |
|---|---|---|---|
| 192×192（现精定位裁剪） | 1:1 原生 | **49** | 实测吻合 |
| 512×512 | 1:1 原生 | **361** | 实测吻合 |
| 768×768 | 1:1 原生 | 784 | |
| 1280×800（现整屏） | 0.50 | **1334** | 实测吻合 |
| **2048×1280（建议新整屏）** | **0.80（双轴同为 0.8）** | **3404** | 待探针验证服务端是否透传 |
| 2560×1600（原生） | 1.00 | 5336 | 超过本端点 2048 实测上限，服务端会缩 → 禁用 |

---

## 4. (d) 「分块发送」与「只发目标区域」：公开实现与数字

### 4.1 官方级：Anthropic 的 `zoom` 动作（computer_20251124）

【一手·SDK/quickstart 源码，本仓已有逐行调研记录】
- `computer_zoom_input.py` 原文：`region: List[int]`，`(x0,y0,x1,y1)`，**「Region coordinates are in the full-screenshot space (not physical display pixels). The crop is scaled up to fill the image budget so fine details become legible.」**
- `computer-use-demo/computer_use_demo/tools/computer.py:475-509`：zoom **重新按物理分辨率截图** → 裁 → 等比 resize 进整屏帧；**「the zoom image itself is never a coordinate frame」**（后续坐标仍是整屏坐标）。
- `computer-use-best-practices/computer_use/tools/computer.py:428-456` 同义实现；工具描述原文："Coordinates in subsequent actions still refer to the full screenshot, not the zoom."
- 只有 `computer_20251124` / `computer_toolset_20260801` 有 zoom（`computer_20250124` 没有）→ <https://ai-sdk.dev/providers/ai-sdk-dev-providers/anthropic>（ai-sdk 文档明确 "the zoom action is only available with `computer_20251124`"）。

**可迁移的三条契约**：(1) 区域坐标用整屏坐标；(2) 裁剪**从原始分辨率图**取（不是从降采样图取）；(3) 裁剪块**放大填满图像预算**。

### 4.2 学术界/工业界可抄实现（本仓已 clone 逐行读过，【一手·本地 clone】）

| 实现 | 关键参数 | 坐标回映射 | 出处 |
|---|---|---|---|
| **ScreenSpot-Pro 官方评测仓** `models/methods/reground.py` | `crop_to_center(image, real_bbox, crop_size=[1024,1024])`，**从原图裁** | `view_bbox_to_real_bbox(view_bbox, viewport)` | <https://github.com/likaixin2000/ScreenSpot-Pro-GUI-Grounding> |
| **ZoomClick**（Princeton） | `--in_ratio 0.5`（每轮缩一半）、`--in_min_crop 768`、`--in_depth 3`、边界 `shift`、**每轮从原图内容继续裁**（像素密度不变） | `view_point_to_real_point(pt, viewport)` 累积 viewport | <https://github.com/Princeton-AI2-Lab/ZoomClick> |
| **RegionFocus**（官方仓库实现） | `calculate_crop_region`：默认 0.5×0.5，候选 4 组比例 `[[0.5,0.5],[0.3,0.3],[0.4,0.8],[0.8,0.4]]`；`crop_and_upsample`：原图裁 → LANCZOS 回到视口尺寸；**自判门控**（问模型「这个位置对吗」，YES 就不 zoom） | `x_orig = left + (rel_zoomed_x / zoom_x)` | <https://github.com/tiangeluo/RegionFocus>（README 本次两次拉取超时，细节取自本仓已 clone 的源码） |
| **CCF / gui-g2-3b-ccf** | `zoom_factor=2.0`（半屏）、`min_crop_side=112`、**只做一次精修**（作者原话："Iterative narrowing (> 1 refinement) gives diminishing returns and compounds coordinate-mapping errors."） | `left + x_on_crop`（纯加法，因裁剪不改比例） | <https://github.com/LufeMC/gui-g2-3b-ccf> |
| **UI-Zoomer** | 全局 `smart_resize_target(h,w,2_000_000,4_800_000)`；crop 后再 `smart_resize_target(cw,ch,1_000_000,4_000_000)`；Density-Gaussian crop 边长 `max(2r,512)` | `gx1=(lx1+c_x1)/orig_w` | <https://github.com/ZJU-REAL/UI-Zoomer> |
| **OpenAI 高细节分块**（公开的「分块发送」范式） | 缩到 2048×2048 内 → 短边 768 → 切 512×512 tile；token = `85 + 170×tiles`；**坐标仍按原图空间返回** | 原生 | <https://platform.openai.com/docs/guides/images-vision>（【二手】检索摘要） |

### 4.3 **CCF 的实测数字必须抄进我们的设计**（本仓已 clone 读其 `benchmarks/results.md`，【一手】）

ScreenSpot-v2，n=1272，greedy：

| 切分 | Baseline | +CCF | Δ |
|---|---|---|---|
| Overall | 89.2% | 88.9% | **−0.3 pp** |
| **Icon** | 80.5% | 82.7% | **+2.2 pp** |
| **Text** | 96.0% | 93.7% | **−2.3 pp** |
| Web | 84.2% | 88.1% | **+3.9 pp** |
| Desktop | 91.3% | 92.8% | +1.5 pp |
| Mobile | 88.0% | 82.8% | **−5.2 pp** |

作者自述的两个坑（对我们直接相关）：
- 「Mobile −5.2pp：目标本来就够大，粗定位已经命中，精修引入几 px 漂移反而出框。」
- 「Text −2.3pp：文字框很宽，精修有时落在**词的视觉中心**而非框中心，漂出几 px。**many are within 5-10px of the original (correct) coarse prediction.**」

作者给的三条改进（应全部采纳）：① **按目标类型门控**（text 不精修）；② `min_pixels_for_ccf` 门限 200K → ~1M；③ `zoom_factor` 2.0 → 1.5（crop 更宽，更不容易把目标裁出去）。

→ **公开数字共同指向的形态**：**半屏级（0.5×0.5 量级）的原分辨率窗口 + 按目标类型门控 + 只做一次**。我们现在的 192×192 窗口比公开实现窄一个数量级——这与本地「36.1% 的裁剪窗压根没框住目标」互相印证。

---

## 5. (e) 怎么可靠确定「模型实际看到的尺寸」

### 5.1 三家官方的做法（对照表）

| 厂商 | 预算规则（公开） | 官方核对手段 | 强度 |
|---|---|---|---|
| Anthropic | 长边 ≤1568 px（4.7+ ≤2576）且总 tile ≤1568（4.7+ ≤4784）；token ≈ `(w×h)/750` | **自己预缩到合规** + 记住送出尺寸（`target_image_size()` 是 API 参考算法的移植）；`count_tokens` 端点可预验 | 【一手】`image.py` docstring + 官方测试 `test_image.py`（`1568×1014` 因 tile 超限被降档）；【二手】vision 页数字 |
| OpenAI | 2048×2048 内 → 短边 768 → 512×512 分块；`85 + 170×tiles` token | 模型看到的是 tile 网格；坐标仍回原图空间 | 【二手】 |
| Qwen2.5-VL | `min_pixels`/`max_pixels`（默认 4×28×28 ~ 16384×28×28） | 处理器侧显式设置（`size['longest_edge'/'shortest_edge']`），README 直接给出「设 token 预算」的写法 | 【一手】官方 README |
| **我们（minimax via OpenCode zen/go）** | **无公开文档**；实测长边上限 ≈2048（`model/image.py:24-30`） | **图像 token 数反查**（本机已验证 28 px 网格成立） | 【一手·本机实测】 |

### 5.2 我们的核对链现在长什么样、缺什么

已有（`calib/calibrate.py:253-320`）：
- `estimate_model_input_from_tokens(observed_input_tokens, text_tokens, aspect_ratio)`：用 `total_input_tokens`（含 cache_read/cache_creation）减文字 token 得图像 token → 在 `ceil(W/28)×ceil(H/28)` 网格里找与声明宽高比相容的格子 → 唯一相容时返回**格子上界** `(pw*28, ph*28)`。
- 本机实测结果：声明 (1280, 800) → 反查 (1288, 812)，每边差 ≤ 28 px。

**缺的三件事（可直接补）**：
1. **缺「相等」判据**：反查只给出一个 ±28 px 的盒子，应该收紧成**断言**——`observed_image_tokens == ceil(W/28) × ceil(H/28)`（对声明尺寸 W×H）。相等 ⇒ 服务端**没有**再缩（格子唯一）；不等 ⇒ 服务端缩过，**该次请求的坐标系不可信**。这把「声明 = 所见」从「盒子里大概」变成**逐请求的布尔证明**。
2. **缺「服务端上限」的主动探针**：2048 宽这一档从没探过（我们只探过 2560→被缩到约 2048）。探针 = 送 2048×1280，看图像 token 是否恰为 `74×46 = 3404`。
3. **缺 token 预算记账**：图像 token 直接等于钱与延迟，而现在 1280 整屏 = 1334 + 192 裁剪 = 49 = 1383/目标。

### 5.3 为什么「用 token 反查」不能替代「别让服务端缩」

token 只能定位到 **±28 px/轴**（一个 patch 格）。若送出 2048×1280、服务端实际缩到 2020×1260，两者 token 都是 74×46 = 3404——**token 数分辨不出来**。所以：
- **像素级一致性**只能靠「送出尺寸在服务端上限内 → 服务端不缩」保证；
- **token 反查**是**回归护栏**（发现服务端行为变化），不是**一致性证明**。

这正是 Anthropic 官方那句的工程含义：pre-resize 到合规尺寸 + 记住尺寸 → 声明即所见。

---

## 6. 落到我们身上的具体参数（可照抄）

### 6.1 整屏档位

| 方案 | 送出 | 双轴缩放比 | 坐标换算 | 图像 token | 风险 |
|---|---|---|---|---|---|
| 现状 | 1280×800 | 0.5 / 0.5 | 单一常数 ×2 | 1334 | icon 在模型输入里 13.25~14.5 px，61.4% < 16 px |
| **建议** | **2048×1280** | **0.8 / 0.8（严格相等）** | 仍是**单一常数 ×1.25** | **3404** | 需探针证明服务端透传（期望 token 恰 3404） |
| 不可行 | 2560×1600 | 1.0 | — | 5336 | 超过实测 2048 上限 → 服务端缩 → ~14% 类漂移 |

**为什么 2048×1280 特别合适**：`2560/2048 = 1600/1280 = 1.25` —— **两轴缩放比严格相等**，所以「两轴各自除以宽/高」依然退化成**同一个常数**，坐标换算链不变长、不新增分叉；而且 1280×800 与 2048×1280 的宽高比都是 16:10，**不形变**。

**icon 在模型输入里的像素尺寸**（本机实测的 13.25~14.5 px @ 0.5 折算 → 原图 26.5~29 px）：

| 整屏档位 | icon 在模型输入里的尺寸 | <16 px 占比（估） |
|---|---|---|
| 1280×800（0.5） | 13.25 ~ 14.5 px | 61.4%（216/352） |
| **2048×1280（0.8）** | **21.2 ~ 23.2 px** | **≈0%** |

### 6.2 精定位窗口档位（原生 1:1 像素，密度不变）

| 方案 | 窗口（物理像素，原生） | token | 公开对照 |
|---|---|---|---|
| 现状 | 192×192 | 49 | 比公开实现窄一个数量级 |
| 建议 | **640×640 或 768×768（≈1/4 ~ 1/3 屏宽）** | 529 / 784 | RegionFocus 0.5×0.5 屏；ZoomClick 0.5；CCF 半屏；SS-Pro `reground.py` 1024×1024 |

**注意**：窗口变大**不损失像素密度**（仍是原图 1:1 像素），只损失相对占比；换回来的是「框住目标」的机会（本地 36.1% 裁剪窗没框住目标）与**上下文**（RegionFocus 的核心论点：减少背景干扰）。

### 6.3 门控（照抄 CCF 的教训）

- **只对 icon 类目标做精修**；text 类目标直接用粗定位（CCF 实测：text −2.3 pp）。
- 目标尺寸过大的（mobile/大按钮）跳过精修（CCF：mobile −5.2 pp）。
- **只做一次**精修，不做多轮（CCF 作者原话：多轮会「compounds coordinate-mapping errors」）。

---

## 7. ≤8 条最小改动清单（按投入产出排）

> 每条：改哪个文件 / 做什么 / 预期哪个数变多少。前提：**先做第 2 条的探针**，第 1 条才可落地。

1. **`src/minicode/model/image.py::model_input_size_for`** —— 把「整数因子 ≤2048」规则换成「**统一缩放到实测上限 2048 宽**」（等比、声明=送出）。2560×1600 → **2048×1280**（双轴同为 0.8，换算仍是单一常数）。**预期**：>2048 宽的样本（2560×1600 类）模型输入里 icon 13.25~14.5 px → **21.2~23.2 px**，**<16 px 占比 61.4% → ≈0%**；图像 token 1334 → 3404（2.55×）。
2. **`src/minicode/calib/calibrate.py::estimate_model_input_from_tokens` + `src/minicode/model/request_log.py`** —— 把 ±28 px 盒反查收紧成**相等断言**：`observed_image_tokens == ceil(W/28)*ceil(H/28)`；不等即判定服务端缩过、**拒绝沿用该坐标系**。并加一个 **2048×1280 探针**（期望图像 token 恰 3404）。**预期**：把「声明=所见」变成逐请求证明；探针若给出非 3404，则本端点会二次缩放 → 整屏退回 1536×960（55×35=1925 token）或其他合规档。**同时把 `calib/calibrate.py:293-295` 的「【二手】28 px 规则」升级为【一手·本机实测】（已有 49/361/1334 三处吻合）。**
3. **`src/minicode/ground/channel.py::DEFAULT_CROP_WIDTH_PX/HEIGHT_PX`（现 192）** —— 精定位窗口放大到 **640 或 768**（物理像素，**仍 1:1 原生**），并用**现有的配对 A/B 设计**重跑 `ground/numbered_bench.py --crop-scan`（同一次粗定位回答、只换被测那一步、精确 McNemar）。**预期**：直接针对「36.1% 裁剪窗没框住目标」；公开实现同款（RegionFocus 0.5×0.5 屏 / ZoomClick 0.5 / CCF 半屏 / SS-Pro 1024²）；密度不降；代价 49 → 529/784 token。
4. **`src/minicode/ground/channel.py` 偏离判据（现参考点=crop 窗中心，阈值 50 px）** —— 参考点改回**粗定位点**并放宽阈值（或直接采信「落在窗内」的精定位答案）。**预期**：收回本地重放显示被误丢的 43.7%（30 条里精定位 18 对 vs 粗点 12 对）。⚠️ 与 R1（判据线）交叉，需与那边对齐后一起改。
5. **`src/minicode/model/image.py` + `ground/channel.py`** —— 备选整屏方案（若第 2 条探针显示 2048 会被缩）：**2×2 原生分块**（每块 1280×800、**原生像素、整数偏移、每块 ≤2048 不被缩**），先选块再块内定位。**预期**：整屏 2× 密度、坐标偏移为整数、换算确定；公开对照 OpenAI 高细节 512 px 分块、RegionFocus 子区域聚焦。
6. **新增指标：`model input 里的 icon 像素尺寸分布`** —— 评测台随每轮报告输出「目标在模型输入图里的像素尺寸」分布与 <16 px 占比（当前基线：icon 13.25~14.5 px、61.4% <16 px）。**预期**：以后每次改缩放都有可比数字，不再靠「感觉图太小」决策。
7. **成本/延迟预算显式化** —— 在同一张表里记图像 token 与延迟：整屏 1334→3404、裁剪 49→529/784 ⇒ 单目标图像 token **1383 → 4188（3.03×）**。**预期**：给「要不要上 2048」一个明确的性价比阈值；并要求**在同 token 预算下重测 44.81% 基线**，否则新旧不可比。
8. **ADR：把 Anthropic 的正确读法写下来** —— 在 `docs/research/01-架构决策备忘.md` 增补一条：「2x = HiDPI 对账（对我们不适用）；XGA 1024×768 = Claude 专有训练分布（对我们不适用）；**可迁移的是「自己缩 + 声明=所见 + 服务端会偷偷缩（~14% 漂移）」**」。**预期**：杜绝以后再有人拿「Anthropic 说要缩小」当依据把整屏退回 1280×800。

---

## 8. 明确「不适用 / 未核实」的条目

| 项 | 判定 | 原因 |
|---|---|---|
| Anthropic「downscale by 2x」 | **不适用** | 那是 Retina 2× DPR 的坐标对账；我们是 150% DPI 且截图已是物理像素 |
| Anthropic「XGA 1024×768 / 别超过 XGA」 | **不适用** | 前提是 Claude 的 computer-use 模型训在降采样图上；我们是通用 API VLM，本地密度 A/B 方向相反 |
| 「RegionFocus 证明 downscaling 让小控件不可解」 | **过度外推，需更正** | 论文没有分辨率消融；它证明的是「放大到原始分辨率」有效（35.7→41.2；61.6%） |
| UI-TARS issue #215 的 16384*28*28 | **未核实** | 本次 web 预算用尽；数值与 Qwen2.5-VL `max_pixels` 一致但未开原 issue 确认 |
| Anthropic 官方文档页正文全文 | **部分二手** | docs.anthropic.com / platform.claude.com / www.anthropic.com 三次直取分别被重定向/404；正文引文用检索摘要 + 本仓已有的一手 quickstart/SDK 源码调研交叉印证 |
| OpenAI 分块公式（2048/768/512 tile，`85+170×tiles`） | **二手** | 检索摘要，未直取官方页；仅作「分块发送」的公开范式引用 |
| Anthropic `count_tokens` 端点可预验 token | **二手** | 检索摘要提到；未直取文档 |
| 2048×1280 会不会被本端点透传 | **待实测（清单第 2 条）** | 现有实测只覆盖「2560 宽 → 被缩到约 2048 宽」，从未探过 2048 档 |

---

## 9. 参考 URL 清单（可打开）

- Anthropic Computer use tool（官方）：<https://platform.claude.com/docs/en/agents-and-tools/tool-use/computer-use-tool>
- Anthropic Vision（官方，长边/token 限额）：<https://platform.claude.com/docs/en/build-with-claude/vision>
- Anthropic quickstart README（XGA 建议 / 别依赖服务端缩图）：<https://github.com/anthropics/anthropic-quickstarts/blob/main/computer-use-demo/README.md>
- Anthropic quickstart `image.py`（1568/1568 与 ~14% 漂移原文）：<https://github.com/anthropics/claude-quickstarts/blob/main/computer-use-best-practices/computer_use/image.py>
- Anthropic quickstart `computer.py`（zoom 实现 / retina 降逻辑尺寸）：<https://github.com/anthropics/claude-quickstarts/blob/main/computer-use-best-practices/computer_use/tools/computer.py>
- Anthropic SDK `computer_zoom_input.py`（region 语义）：<https://github.com/anthropics/anthropic-sdk-python/blob/main/src/anthropic/types/computer_zoom_input.py>
- ai-sdk（zoom 仅 computer_20251124）：<https://ai-sdk.dev/providers/ai-sdk-dev-providers/anthropic>
- RegionFocus 论文（HTML v2，我直读）：<https://arxiv.org/html/2505.00684v2> ｜abs：<https://arxiv.org/abs/2505.00684> ｜代码：<https://github.com/tiangeluo/RegionFocus>
- ScreenSpot-Pro 官方评测仓（`models/methods/reground.py`）：<https://github.com/likaixin2000/ScreenSpot-Pro-GUI-Grounding>
- ScreenSpot-Pro 论文：<https://arxiv.org/html/2504.07981v1>
- ZoomClick：<https://github.com/Princeton-AI2-Lab/ZoomClick>（论文 <https://arxiv.org/abs/2512.05941>）
- CCF / gui-g2-3b-ccf（含实测表）：<https://github.com/LufeMC/gui-g2-3b-ccf>
- UI-Zoomer：<https://github.com/ZJU-REAL/UI-Zoomer>
- OpenAI Vision（分块算法）：<https://platform.openai.com/docs/guides/images-vision>
- Qwen2.5-VL 模型卡（min/max_pixels、token 区间）：<https://huggingface.co/Qwen/Qwen2.5-VL-7B-Instruct> ｜README：<https://github.com/QwenLM/Qwen2.5-VL>
- MLX-VLM issue #1175（max_pixels 与 grounding 精度）：<https://github.com/Blaizzy/mlx-vlm/issues/1175>
- 阿里云 Model Studio 视觉文档（定位鲁棒区间）：<https://alibabacloud.com/help/en/model-studio/vision>
- 本仓相关产物：`docs/research/vision-root-cause.md`、`docs/research/bottom-drafts/10-anthropic-zoom源码细节.md`、`docs/research/bottom-drafts/09-zoom开源实现调研.md`

# GUI 视觉智能体的坐标表示 — 源码/官方文档级调研

> 调研日期：2026-10-19　范围：VLM grounding 坐标空间 → 误差量化 → 精细化方案 → 选型建议
> 场景假设：**2560×1600 物理分辨率 / Windows 150% DPI 缩放（逻辑 1707×1067）** 的桌面截图点击
> 证据等级标注：**[官方文档]** / **[源码]** / **[论文]** / **[官方博客]** / **[二手报道]**

---

## 0. 结论先行（TL;DR）

1. **Qwen2.5-VL = 绝对像素（smart_resize 之后的分辨率）**，**Qwen3-VL = 0~1000 归一化**。Qwen 官方在 2025 年做了一次"归一化 → 绝对 → 归一化"的往返，Qwen3-VL 是回归后的状态。[官方论文/源码]
2. **UI-TARS-1.5/2 继承了 Qwen2.5-VL 的绝对像素**，官方 `action_parser.py` 里写死了 `model_type="qwen25vl"` 分支：先除以 `smart_resize` 尺寸归一化，再乘原图宽高。[源码]
3. **归一化坐标的量化误差不是瓶颈**（0~1000 在 2560 宽屏上 1 单位 = 2.56 物理 px，±0.5 单位 = ±1.28 px）。**真正的瓶颈是"整屏降采样把小图标抹掉"**（Qwen2.5-VL 默认参数下 16 物理 px 的图标进模型只剩 7.9 px）和 **DPI 坐标空间错配（1.5× 系统性偏移）**。
4. **推荐方案：Qwen3-VL + 两级 grounding（一级整屏粗定位 → 二级在原始物理分辨率 crop 上精定位）+ 进程 Per-Monitor-V2 DPI 感知**。工程端坐标一律落成**物理像素**。
5. **最终点击坐标只在 crop 尺度上产生**。整屏归一化坐标只用来"选哪块区域"，不直接点击。
6. **不需要额外接 OCR/检测模型**来解决坐标精度问题；需要接的是 **UI Automation 树**（作为"元素级兜底"，解决"点不中"而不是"点偏"）。

---

## 1. A. 各主流 VLM 的 grounding 坐标空间

### 1.0 汇总表

| 模型 / 系统 | 坐标空间 | 归一化基准 | 关键出处 | 等级 / 日期 |
|---|---|---|---|---|
| Qwen2-VL | 0~1000 归一化 | 原图宽高 | [issue #866](https://github.com/QwenLM/Qwen3-VL/issues/866#issuecomment-2687735258) | [官方文档] 2025-02-27 |
| **Qwen2.5-VL** | **绝对像素** | **smart_resize 后的图** | [issue #676](https://github.com/QwenLM/Qwen3-VL/issues/676#issuecomment-2617833357) | [官方文档] 2025-01-28 |
| **Qwen3-VL**（2B/4B/8B/32B/30B-A3B/235B-A22B） | **0~1000 归一化** | 原图宽高 | [技术报告 §3.2.4](https://arxiv.org/html/2511.21631v2#S3.SS2.SSS4) | [论文] 2025-11-27 |
| Qwen3-VL-Flash（API） | 0~1000 归一化（与系列一致） | 预处理后图宽高 | [Qwen3-VL issue #1485](https://github.com/QwenLM/Qwen3-VL/issues/1485) | [官方文档] 2025-10 起 |
| **UI-TARS / 1.5 / 2** | **绝对像素**（Qwen2.5-VL 血统） | smart_resize 后的图 | [README](https://github.com/bytedance/UI-TARS/blob/main/README.md) + [action_parser.py](https://github.com/bytedance/UI-TARS/blob/main/codes/ui_tars/action_parser.py) | [官方文档+源码] 2025-09-04 |
| Anthropic Claude computer use | **截图像素** | 你返回的那张截图 | [官方文档](https://platform.claude.com/docs/en/agents-and-tools/tool-use/computer-use-tool) | [官方文档] 持续更新 |
| OpenAI `computer_use_preview` | **声明的 display 像素** | `display_width/height` | [openai-agents-python docs](https://github.com/openai/openai-agents-python/blob/main/docs/tools.md) | [官方文档+源码] 2025 |
| OpenAI GA `computer` | **截图像素**（尺寸从截图推断） | 截图实际宽高 | [同上](https://github.com/openai/openai-agents-python/blob/main/docs/tools.md) | [官方文档+源码] 2025 |
| Google Gemini computer use | **0~1000 归一化** | 屏幕宽高 | [官方文档](https://ai.google.dev/gemini-api/docs/computer-use) | [官方文档] 持续更新 |
| GLM-4.1V / 4.5V | **0~999 归一化** | 截图宽高 | [GLM-V 官方 agent.md](https://github.com/zai-org/GLM-V/blob/main/examples/gui-agent/glm-45v/agent.md) | [官方文档] 2025-08 |
| Doubao Seed 1.6 vision | 未见官方坐标规范；生态按 0~1000 处理 | — | [bytebot #140](https://github.com/bytebot-ai/bytebot/issues/140) | [二手报道] ⚠️ |
| Kimi-VL-A3B | **0~1 相对坐标** | 原图宽高 | [Kimi-VL issue #52](https://github.com/MoonshotAI/Kimi-VL/issues/52) | [官方文档] 2025-03 |

---

### 1.1 Qwen2.5-VL：绝对像素，基准是 `smart_resize` 之后的图

**核心断言（Qwen 团队 Collaborator `ShuaiBai623`，2025-01-28）：**

> "Yes, we will output absolute coordinates based on the actual input size of the image. However, it's important to note that this is the size **after processer**."
> — [QwenLM/Qwen3-VL#676 comment](https://github.com/QwenLM/Qwen3-VL/issues/676#issuecomment-2617833357) [官方文档] 2025-01-28

**Qwen2-VL vs Qwen2.5-VL 的差异（2025-02-27）：**

> "you are right that there are differences between **Qwen2-VL(using coordinates normalized between [0,1000])** and **Qwen2.5-VL(using absolute coordinates based on the actual resized image shape)**"
> — [QwenLM/Qwen3-VL#866 comment](https://github.com/QwenLM/Qwen3-VL/issues/866#issuecomment-2687735258) [官方文档] 2025-02-27

**"bbox 漂移"的根因与官方修复（Qwen 团队成员 `HumanZhong`，2025-02-26）：**

> "Hi, we use the absolute coordinates based on the image shape after resizing. Besides, the bbox shift issue on larger images (like > 1000x1000) is probably caused by this: … the model may be using the **default `max_pixels` setting which is about 1280x28x28**. As a result, images larger than this threshold will be resized and the output coordinates are the absolute coordinates based the resized shape. If you directly draw these bboxes on the original image, this bbox shift issue may arise."
>
> 官方三条解法：① 用 chat.qwen.ai 时先缩图再传；② 用 API 时设 `vl_high_resolution_images=True`；③ 自行部署时**调大 `max_pixels`**。
> — [QwenLM/Qwen3-VL#830 comment](https://github.com/QwenLM/Qwen3-VL/issues/830#issuecomment-2684036558) [官方文档] 2025-02-26

**精确公式（官方 cookbook 写法）**

官方 Qwen2.5-VL cookbook 的写法（被 issue #830 引用为标准解法）：

```python
input_height = inputs['image_grid_thw'][0][1] * 14
input_width  = inputs['image_grid_thw'][0][2] * 14
# 模型输出 (x, y) 属于 [0, input_width) × [0, input_height) 的像素空间
x_orig = x_model / input_width  * orig_width
y_orig = y_model / input_height * orig_height
```
— [QwenLM/Qwen3-VL#830 comment（引用 cookbook）](https://github.com/QwenLM/Qwen3-VL/issues/830#issuecomment-2677789221) [官方文档] 2025-02-24

**⚠️ `image_grid_thw` 语义（源码级，常见误解点）**

`image_grid_thw` 存的是**未 merge 的 patch 网格**，不是 merge 后的 token 网格。transformers v4.57.0 源码：

```python
grid_h, grid_w = resized_height // patch_size, resized_width // patch_size   # patch_size = 14
patches = patches.reshape(grid_t, temporal_patch_size, channel,
                           grid_h // merge_size, merge_size, patch_size,      # merge_size = 2
                           grid_w // merge_size, merge_size, patch_size)
...
return flatten_patches, (grid_t, grid_h, grid_w)
```
— [transformers v4.57.0 `image_processing_qwen2_vl.py`](https://github.com/huggingface/transformers/blob/v4.57.0/src/transformers/models/qwen2_vl/image_processing_qwen2_vl.py) [源码]

所以：
- `resized_height = image_grid_thw[0][1] * 14`（**乘 14 是对的**，不是 28）✅
- 视觉 token 数 = `(grid_h / 2) × (grid_w / 2)` = `resized_h/28 × resized_w/28`（**乘 28 才对**）
- 两个数别搞混：**还原像素用 ×14，统计 token 用 ÷28**。

**`smart_resize` 的确切实现（源码）**

`factor = patch_size * merge_size = 14 × 2 = 28`；`min_pixels` / `max_pixels` 约束的是 **H×W 的总像素数**：

```python
h_bar = max(factor, round_by_factor(height, factor))
w_bar = max(factor, round_by_factor(width, factor))
if h_bar * w_bar > max_pixels:
    beta = math.sqrt((height * width) / max_pixels)
    h_bar = floor_by_factor(height / beta, factor)
    w_bar = floor_by_factor(width / beta, factor)
elif h_bar * w_bar < min_pixels:
    beta = math.sqrt(min_pixels / (height * width))
    h_bar = ceil_by_factor(height * beta, factor)
    w_bar = ceil_by_factor(width * beta, factor)
```
— [qwen-vl-utils `vision_process.py`](https://github.com/QwenLM/Qwen2.5-VL/blob/main/qwen-vl-utils/src/qwen_vl_utils/vision_process.py) [源码]
— [transformers v4.57.0 同款实现](https://github.com/huggingface/transformers/blob/v4.57.0/src/transformers/models/qwen2_vl/image_processing_qwen2_vl.py) [源码]

**默认值（源码，这是最关键的一行）**

```python
size = {"shortest_edge": 56 * 56, "longest_edge": 28 * 28 * 1280}
#        min_pixels = 3,136      max_pixels = 1,003,520
patch_size = 14; merge_size = 2
```
— [transformers v4.57.0 `Qwen2VLImageProcessor`](https://github.com/huggingface/transformers/blob/v4.57.0/src/transformers/models/qwen2_vl/image_processing_qwen2_vl.py) [源码]

即：**Qwen2.5-VL 默认 `max_pixels ≈ 1.0M`，一张 2560×1600（4.10M px）的整屏截图会被降采样约 2.03×**（详见 §2.3）。这正是"bbox 整体偏移"的根因。

**关于 28×28 patch 的理论精度（官方 Collaborator，2025-02-03）：**

> "Although the patch is 28x28, the 28x28 patch is converted into a larger embedding that can contain positional information within the 28x28 area. Therefore, the model can also predict positions inside the 28x28 region, meaning the **theoretical error would be equivalent to the position of 1 pixel in the input image**."
> — [QwenLM/Qwen3-VL#676 comment](https://github.com/QwenLM/Qwen3-VL/issues/676#issuecomment-2630595976) [官方文档] 2025-02-03

**训练侧对应结论（2025-02-28）**：训练时必须用 `smart_resize` 后的空间标注 GT bbox（前提是训练/推理用同一组 `min_pixels`/`max_pixels`）——[QwenLM/Qwen3-VL#866 comment](https://github.com/QwenLM/Qwen3-VL/issues/866#issuecomment-2690052936) [官方文档] 2025-02-28

---

### 1.2 Qwen3-VL：回归 0~1000 归一化

**官方技术报告原文（arXiv:2511.21631v2 §3.2.4 Grounding and Counting，2025-11-27）：**

> "**Different from Qwen2.5-VL, we adopt a normalized coordinate system scaled to the range [0,1000] in this version.** This design improves robustness to variations in image resolution and aspect ratio across diverse inputs, while also simplifying post-processing and enhancing the usability of predicted coordinates in downstream applications."
> — [Qwen3-VL Technical Report §3.2.4](https://arxiv.org/html/2511.21631v2#S3.SS2.SSS4) [论文] 2025-11-27

**官方 README（Groundings cookbook 描述）：**
> "Precise Object Grounding Across Formats | Using **relative position coordinates**, it supports both boxes and points, allowing for diverse combinations of positioning and labeling tasks."
> — [QwenLM/Qwen3-VL README](https://github.com/QwenLM/Qwen3-VL#cookbooks) [官方文档] 2025-10 起

**视觉 token / 动态分辨率上限（官方 README + preprocessor_config.json 源码）**

```json
{ "size": { "longest_edge": 16777216, "shortest_edge": 65536 },
  "patch_size": 16, "temporal_patch_size": 2, "merge_size": 2 }
```
— [Qwen/Qwen3-VL-8B-Instruct `preprocessor_config.json`](https://huggingface.co/Qwen/Qwen3-VL-8B-Instruct/blob/main/preprocessor_config.json) [源码/官方模型仓]

- `factor = patch_size × merge_size = 16 × 2 = 32`（Qwen2.5-VL 是 28）
- **空间压缩 32×** → 1 个视觉 token 覆盖 32×32 = 1024 图像像素
- `max_pixels = 16,777,216 = 16384 × 32 × 32` → 单图最多 16,384 视觉 token
- `min_pixels = 65,536 = 64 × 32 × 32`
- 官方 README 推荐区间：**单图 256–1280 视觉 token**；视频 256–16,384 token
  — [Qwen3-VL README "Pixel Control" 段](https://github.com/QwenLM/Qwen3-VL#pixel-control-via-official-processor) [官方文档]

**关键推论（对本项目最重要的一条）：**
`max_pixels = 16.78M > 2560×1600 = 4.10M`，且 2560/32 = 80、1600/32 = 50 均为整数 ⇒ **Qwen3-VL 默认参数下，2560×1600 整屏截图原分辨率进模型，完全不缩放**。
- `image_grid_thw = (1, 100, 160)`，视觉 token = 50 × 80 = **4000**
- `resized_h = image_grid_thw[0][1] * 16`（Qwen3-VL 乘 **16**，不是 14）

**Qwen3-VL 官方 grounding 文档（mintlify 部署的 QwenLM 官方文档）：**
> "Positions are expressed as **relative coordinates**: X-axis: 0 (left edge) to 1 (right edge); Y-axis: 0 (top edge) to 1 (bottom edge)"
> — [Qwen3-VL 2D Object Grounding](https://mintlify.wiki/QwenLM/Qwen3-VL/capabilities/grounding-2d) [官方文档] 生成于 2026-03-02

⚠️ **口径不一致提示**：官方技术报告写 **[0,1000]**，官方 docs 页面写 **[0,1]**。实践上模型输出的是 **0~1000 整数**；部分官方 cookbook 用 `/999` 换算、`2d_grounding` 用 `/1000`。**工程上必须自己固定一个分母并保持一致**（推荐 `/1000`，且对边界做 clamp）。

**Qwen3-VL-Flash API**：与系列一致为 0~1000 归一化（[issue #1485](https://github.com/QwenLM/Qwen3-VL/issues/1485) 记录了"0-1000 → 绝对 → 0-1000"的变更史）。⚠️ 但 DashScope 侧**未找到**明确写坐标空间的官方 API 文档页，此条依据为官方仓库 issue + 系列一致性，属**推断**。
DashScope 规格参考：[qwen3-vl-flash 模型页](https://www.alibabacloud.com/help/zh/model-studio/qwen3-vl-flash) [官方文档]

**Qwen3-VL 自带"视觉搜索"工具**（对 §C 的两级方案直接有用）：
> "Thinking with Images | Utilize **`image_zoom_in_tool`** and search_tool to facilitate the model's precise comprehension of fine-grained visual details within images."
> — [Qwen3-VL README Cookbooks 表](https://github.com/QwenLM/Qwen3-VL#cookbooks) [官方文档] 2025-10 起

即官方自己就把"放大裁剪再识别"做成了工具形态。

---

### 1.3 UI-TARS / UI-TARS-1.5 / UI-TARS-2

**官方 README 明确说明：**
> "❗Notes: Since **Qwen 2.5vl based models ultilizes absolute coordinates** to ground objects, please kindly refer to our illustration about how to process coordinates in this `README_coordinates.md` guide."
> — [bytedance/UI-TARS README](https://github.com/bytedance/UI-TARS/blob/main/README.md) [官方文档]

**官方 `action_parser.py` 的完整换算链（源码，这是最有价值的一段）：**

```python
IMAGE_FACTOR = 28
MIN_PIXELS = 100 * 28 * 28      # 78,400
MAX_PIXELS = 16384 * 28 * 28    # 12,845,056

if model_type == "qwen25vl":
    smart_resize_height, smart_resize_width = smart_resize(
        origin_resized_height, origin_resized_width,
        factor=IMAGE_FACTOR, min_pixels=min_pixels, max_pixels=max_pixels)
...
# Qwen2.5vl output absolute coordinates, qwen2vl output relative coordinates
if model_type == "qwen25vl":
    float_numbers = []
    for num_idx, num in enumerate(numbers):
        num = float(num)
        if (num_idx + 1) % 2 == 0:
            float_numbers.append(float(num / smart_resize_height))
        else:
            float_numbers.append(float(num / smart_resize_width))
else:
    float_numbers = [float(num) / factor for num in numbers]   # factor = 1000
```
— [bytedance/UI-TARS `codes/ui_tars/action_parser.py`](https://github.com/bytedance/UI-TARS/blob/main/codes/ui_tars/action_parser.py) [源码]

最后一步在 `parsing_response_to_pyautogui_code`：
```python
x = round(float((x1 + x2) / 2) * image_width, 3)
y = round(float((y1 + y2) / 2) * image_height, 3)
pyautogui.click(x, y, button='left')
```

**完整链路（UI-TARS / Qwen2.5-VL 血统）：**

```
模型输出 (x, y)  ← smart_resize 后的图的绝对像素
   ↓ ÷ smart_resize_width / smart_resize_height
归一化比例 (fx, fy) ∈ [0,1]
   ↓ × 原图 width / height
原图像素 (x_px, y_px)
   ↓ （若走 pyautogui，取决于进程 DPI 感知状态）
屏幕点击坐标
```

**⚠️ UI-TARS 参考实现的 `max_pixels` 与 HF 默认值不同**：`action_parser.py` 用 `16384*28*28 = 12.85M`（≈ 不降采样），而 HF `Qwen2VLImageProcessor` 默认只有 `1280*28*28 = 1.00M`。**这个差异直接决定了 UI-TARS 在 2560×1600 上是"近乎原分辨率"还是"降采样 2.03×"**（见 §2.3）。

**官方坐标可视化教程**（README_coordinates.md，含完整 `smart_resize` 拷贝）：https://github.com/bytedance/UI-TARS/blob/main/README_coordinates.md [官方文档]

**UI-TARS-2**：2025-09-04 发布，技术报告 [arXiv:2509.02544](https://arxiv.org/abs/2509.02544) [论文] 2025-09-05。成绩：Online-Mind2Web 88.2 / OSWorld 47.5 / WindowsAgentArena 50.6 / AndroidWorld 73.3。**坐标方案未变**（仍是 Qwen2.5-VL 血统的绝对像素 + 官方 parser 换算）。

**⚠️ UI-TARS 没有内置 zoom-in 动作**：官方 `action_parser.py` 支持的动作类型为 `click / left_single / left_double / right_single / hover / drag / select / scroll / type / hotkey / press / keydown / keyup / release / finished` —— **无 zoom**。zoom 是 Anthropic computer use 的内置动作（见 §1.4），以及 Qwen3-VL 的 `image_zoom_in_tool`（见 §1.2）。

---

### 1.4 对照组

#### Anthropic Claude computer use —— **截图像素**

官方文档要点：
- 每个 `coordinate` / `start_coordinate` / zoom 的 `region` 角点、以及 `cursor_position` 回传值，**全部是"你返回的那张整屏截图的像素空间"**，原点在左上角。
- **zoom 不会改变坐标系**：执行 zoom 后，Claude 仍然用整屏截图的像素空间表达坐标，**绝不是 zoom 后的图像坐标**。
- 如果你在回传前把截图缩小了，必须把 Claude 的坐标按比例放大回真实显示。
- `zoom` 动作参数：`region: [x0, y0, x1, y1]`，以全分辨率截取该区域，返回时缩放到常规截图尺寸并保持长宽比。
— [Anthropic Computer Use Tool 官方文档](https://platform.claude.com/docs/en/agents-and-tools/tool-use/computer-use-tool) [官方文档]（中文镜像：[zh-CN](https://platform.claude.com/docs/zh-CN/agents-and-tools/tool-use/computer-use-tool)）

官方博客给的尺寸上限与"小目标"数据：
- `MAX_LONG_EDGE = 1568`（4.6 系）/ `2576`（Opus 4.7）；`MAX_PIXELS = 1_150_000`（4.6 系）/ `3_750_000`（Opus 4.7）
- 官方提供 `compute_max_api_fit(native_w, native_h)` 求"保持长宽比且符合限制的最大分辨率"
- **小目标退化示例**：3840×2160 上的一个 **16px 复选框，缩到 1280×720 后只剩约 5px**
- 官方实测**无效**的三种做法：把截图切成四象限平铺、在截图上叠加坐标网格、换用不同重采样算法（PIL LANCZOS vs sips 结果一致）
- 官方建议：用 zoom 能力 / 放大目标 / 键盘替代 / 降低源 DPI
— [Best practices for computer and browser use with Claude](https://claude.com/blog/best-practices-for-computer-and-browser-use-with-claude) [官方博客]

**结论：Claude 的坐标契约 = "你给什么图，它就用什么图的像素"**，这是最省事的一种（没有 smart_resize 这类隐藏缩放），但代价是**你必须自己按 API 限制缩图，且自己换算坐标**。

#### OpenAI computer use —— **声明的 display 像素**（preview）/ **截图像素**（GA）

官方 Agents SDK 文档明确：
- 旧 `computer-use-preview`：`{"type": "computer_use_preview", "environment": ..., "display_width": ..., "display_height": ...}` —— 模型在该**声明尺寸的像素空间**里输出坐标
- 新 GA `{"type": "computer"}`：**去掉了 `display_width`/`display_height`**，尺寸从截图推断
— [openai-agents-python `docs/tools.md`](https://github.com/openai/openai-agents-python/blob/main/docs/tools.md) [官方文档+源码]
— [官方迁移指南](https://developers.openai.com/api/docs/guides/tools-computer-use-integration#migration-from-computer-use-preview) [官方文档]

⚠️ OpenAI 官方文档站对本次抓取被 Cloudflare 拦截，**未能直连平台文档页**；上述依据是 OpenAI 官方 SDK 仓库文档 + 官方迁移指南 URL。

#### Google Gemini computer use —— **0~1000 归一化**

官方文档给出反归一化示例：
```java
int denormalizeX(int x, int screenWidth) { return (int)(x / 1000.0 * screenWidth); }
int denormalizeY(int y, int screenHeight) { return (int)(y / 1000.0 * screenHeight); }
```
适用于 `click_at` / `type_text_at` / `hover_at` / `drag_and_drop`（`destination_x/y`）/ `scroll_at` 等全部坐标参数。
— [Gemini API Computer Use 官方文档](https://ai.google.dev/gemini-api/docs/computer-use) [官方文档]

⚠️ 本次抓取 `ai.google.dev` 多次网络失败，**未直连到该页**；上述反归一化代码来自搜索引擎对该官方页面的索引摘录（内容与官方页一致，URL 为官方域名）。等级标为 [官方文档，经搜索引擎摘录]。

#### GLM-4.1V / GLM-4.5V —— **0~999 归一化**

官方 GUI-Agent 提示词原文：
> "Each coordinate should be an integer between **0 and 999**, representing a ratio (‰) of the screenshot width or height."
> "The box_2d should be [[xmin,ymin,xmax,ymax]] **normalized to 0-999**"
> 提示词系统段：**"The coordinates involved are all represented in thousandths (0-999)."**
— [zai-org/GLM-V `examples/gui-agent/glm-45v/agent.md`](https://github.com/zai-org/GLM-V/blob/main/examples/gui-agent/glm-45v/agent.md) [官方文档] 2025-08

注：GLM 的 web 场景直接用 **Numerical_Label**（元素 ID）而不是坐标——见 §C.3。

#### Doubao / Seed 视觉模型 —— 未见官方坐标规范

字节官方页面与新闻稿均未给出坐标空间说明。生态侧按 **0~1000 相对坐标**处理：
> "若模型输出相对坐标（基于 1000x1000 分辨率），脚本会自动将其缩放至屏幕像素"
> — [httprunner/skills `ai-vision`](https://github.com/httprunner/skills)（第三方封装，非官方规范）[二手报道] ⚠️

**建议：不要依赖 Doubao 的坐标规范，按"0~1000 + 防御性判别"实现**（若所有坐标 ≤1000 且图像任一边 >1000，则按 0~1000 相对处理）。

#### Kimi-VL-A3B —— **0~1 相对坐标**

维护者 `teowu` 明确答复：
> "Our coordinate system is based on **relative-1 coordinates**, and `[x,y,w,h]` for boxes. However, this version of the model has not explicitly trained box during its final stage of SFT, so the box coordinate output might not be accurate."
> — [MoonshotAI/Kimi-VL issue #52](https://github.com/MoonshotAI/Kimi-VL/issues/52) [官方文档] 2025-03

**工程结论：Kimi-VL 官方自己都说 bbox 不可靠 → 优先用 point 输出，别用 bbox。**

---

## 2. B. 误差量化：2560×1600 @ 150% DPI

### 2.1 误差要分成四类，不要混为一谈

| 误差类型 | 来源 | 量级（本场景） | 能否靠"改坐标表示"解决 |
|---|---|---|---|
| **① 量化误差** | 坐标表示本身的分辨率极限（0~1000 或整数像素） | **±0.5~1.3 物理 px** | ✅ 能（但收益极小） |
| **② 观测/采样误差** | 整屏降采样导致小图标被抹掉 | **16px 图标 → 7.9px（Q2.5-VL 默认）** | ❌ 不能，**只能靠 crop 提升有效分辨率** |
| **③ 模型误差** | grounding 预测本身的偏差 | 数 px ~ 数十 px（主项） | ❌ 不能，靠 crop + 换模型 |
| **④ 坐标系错配** | DPI / 分辨率 / 多屏原点搞错 | **数百 ~ 上千 px（1.5× 或 2.03×）** | ✅ 能，**必须靠协议强制** |

**这是本报告最重要的一句话：把坐标从 0~1000 换成像素、或加小数位，只能改善 ①；而你的实际痛点大概率是 ② 和 ④。**

### 2.2 关键数字表：0~1000 归一化 vs 像素坐标

**场景**：物理 2560×1600，Windows 150% DPI（逻辑 1707×1067，1 逻辑 px = 1.5 物理 px）

| 方案 | 模型实际看到的分辨率 | 缩放比 (X/Y) | **1 单位的物理像素** | **±0.5 单位量化误差** | 16px 图标进模型后 | 1 视觉 token 覆盖的物理像素 | 视觉 token 数 |
|---|---|---|---|---|---|---|---|
| **Qwen3-VL 默认**（16M px，0~1000） | **2560×1600（不缩放）** | 1.000 / 1.000 | X **2.56** / Y **1.60** px | **±1.28 / ±0.80** px | **16.0 px** | 1024 px | 4000 |
| **Qwen2.5-VL HF 默认**（1.0M px，绝对像素） | 1260×784 | 2.032 / 2.041 | 1 模型 px = **2.03 物理 px** | ±1.02 / ±1.02 px | **7.9 px** ⚠️ | 1593 px | 1260 |
| **UI-TARS 参考实现**（12.8M px，绝对像素） | 2548×1596 | 1.005 / 1.003 | 1 模型 px = **1.005 物理 px** | **±0.50 / ±0.50** px | **15.9 px** | 788 px | 5187 |
| **Claude**（fit 1568/1.15MP） | 1356×848 | 1.887 / 1.887 | 1 截图 px = **1.89 物理 px** | ±0.94 / ±0.94 px | **8.5 px** ⚠️ | — | — |
| **Claude**（仅限长边 1568） | 1568×980 | 1.633 / 1.633 | 1 截图 px = **1.63 物理 px** | ±0.82 / ±0.82 px | 9.8 px | — | — |
| **0~1000 归一化 on 逻辑分辨率** 1707×1067 | 1707×1067 | 1.0（逻辑） | X 1.707 逻辑 = **2.56 物理** | ±0.85 逻辑 = ±1.28 物理 | 16.0 物理 px | — | — |

**读表要点：**

1. **0~1000 归一化的量化误差只有 ±1.28 px（X）**。一个 16 物理 px 的图标在归一化坐标里宽 **6.25 个单位**——完全够表达。**"归一化精度不够"是伪命题。**
2. **Qwen2.5-VL 默认参数最吃亏**：整屏被降采样 2.03×，16px 图标进模型只剩 **7.9 px**，1 个视觉 token 覆盖 1593 物理 px。**这是它需要 crop 的根本原因，而不是它的坐标是绝对像素。**
3. **UI-TARS 参考实现的 `max_pixels` 才是对的**（12.8M）：2548×1596 近乎原分辨率，量化误差 ±0.5 px，1 token = 788 物理 px。
4. **Qwen3-VL 默认 16M px 上限 = 不缩放**：既有原分辨率（16px 图标保持 16px），又是归一化坐标（免疫 DPI/多屏/缩放错配），**两头都占**。这是选它的第一理由。
5. **Claude 的 8.5 px 小图标**是官方自己承认的退化点（官方举例：3840×2160 的 16px 复选框 → 1280×720 下只剩 5px），官方给的解法是 **zoom 动作**，不是"改用像素坐标"。

### 2.3 `smart_resize` 还原误差（Qwen2.5-VL 默认参数的完整推导）

输入：2560×1600 物理截图，`factor=28`，`min_pixels=3,136`，`max_pixels=1,003,520`

```
h_bar = round(1600/28)*28 = 57*28 = 1596
w_bar = round(2560/28)*28 = 91*28 = 2548
1596 * 2548 = 4,066,608 > 1,003,520  →  触发降采样
beta = sqrt(2560*1600 / 1,003,520) = sqrt(4.0813) = 2.0202
h_bar = floor(1600/2.0202/28)*28 = floor(28.28)*28 = 784
w_bar = floor(2560/2.0202/28)*28 = floor(45.25)*28 = 1260
784 * 1260 = 987,840 ≤ 1,003,520  ✓
```

于是：

| 项 | 值 |
|---|---|
| 模型输入分辨率 | **1260 × 784** |
| 还原比例 | `x_px = x_model / 1260 * 2560`（scale 2.0317）、`y_px = y_model / 784 * 1600`（scale 2.0408） |
| 量化误差 | ±1.02 物理 px（比归一化方案的 ±1.28 还小一点，**但没有意义**） |
| **有效分辨率** | 1 视觉 token = 28×28 模型 px = **1593 物理 px** |
| **16px 图标** | 进模型只剩 **7.9 px** |
| 视觉 token 数 | 45 × 32 = 1260（省了 token，但丢了细节） |
| **如果忘记乘 scale** | 误差 = 2560 − 1260 = **1300 px**（灾难性） |

**对比：若按 UI-TARS 的 `max_pixels = 12,845,056`**

```
1596 * 2548 = 4,066,608 ≤ 12,845,056  →  不降采样
模型输入 = 2548 × 1596，scale = 1.0047 / 1.0025，量化误差 ±0.50 物理 px
1 视觉 token = 788 物理 px；16px 图标进模型 = 15.9 px；视觉 token = 91 × 57 = 5187
```

> **工程结论 #1：Qwen2.5-VL 必须显式把 `max_pixels` 调到 ≥ 2560×1600（建议 `16384*28*28 = 12,845,056`），否则高分辨率屏幕上的 grounding 必然退化。** 这也是 UI-TARS 官方 parser 用 12.8M 而 HF 用 1.0M 的原因。

### 2.4 DPI 1.5× 偏差：到底怎么错、错多少

前提：物理 2560×1600，缩放 150%，逻辑 1707×1067（`2560/1.5 = 1706.67`，`1600/1.5 = 1066.67`）。

| 错误类型 | 具体做法 | 后果 |
|---|---|---|
| **A. 抓图物理、点击逻辑** | 截图 2560×1600 物理，归一化用 2560 算出 2000，把 2000 传给按逻辑坐标的点击 API | 系统按 1.5× 放大 ⇒ 实际点在 **3000 物理 px**，**偏 1000 px** |
| **B. 抓图逻辑、点击物理** | 截图 1707×1067 逻辑，归一化用 1707 算出 1333，把 1333 传给按物理坐标的点击 API | 实际点在 **1333 物理 px**（应为 2000），**偏 667 px** |
| **C. 坐标空间正确但忘了 ×1.5** | 归一化 781（= 2000/2560×1000）→ 用逻辑宽 1707 反归一化 = 1333 | 若后续按物理点击 ⇒ 偏 667 px |
| **D. 归一化 / 像素混用** | 拿 0~1000 的值当像素用 | 偏 1560 px（= 2000 − 440） |

**结论：在 150% DPI 上，A/B/C 任一发生都是几百到上千像素的灾难性错误，而不是"偏几个像素"。**

**根因与解法（Windows 侧）：**
- Win32 坐标受 **DPI 虚拟化**影响：`GetSystemMetrics(SM_CXSCREEN)` 在 **DPI-unaware** 进程里返回 **逻辑** 1707，在 **Per-Monitor-V2 aware** 进程里返回 **物理** 2560。
- 抓图侧：进程 DPI 感知状态决定拿到的是物理还是被虚拟化的位图。Windows Graphics Capture (WGC) / DXGI Desktop Duplication 给物理像素；`BitBlt`/`PrintWindow` 随线程 DPI 感知状态变化。
- 点击侧：`SetCursorPos` 的坐标解释同样随进程 DPI 感知状态变化；`SendInput` + `MOUSEEVENTF_ABSOLUTE` 用 0~65535 归一化到虚拟桌面（配合 `MOUSEEVENTF_VIRTUALDESK` 可覆盖多屏）。
- **统一解法**：进程启动即调用 `SetProcessDpiAwarenessContext(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2)`，之后**抓图用物理像素、点击用物理像素、中间坐标也用物理像素**，全程不出现逻辑分辨率。

### 2.5 截图是物理还是逻辑？各坐标空间分别对应什么

| 截图来源 | 得到的分辨率 | Qwen3-VL(0~1000) 归一化坐标指向 | Qwen2.5-VL(绝对像素) 指向 | Claude/OpenAI 像素坐标指向 |
|---|---|---|---|---|
| WGC / DXGI 抓取，DPI-aware 进程 | **2560×1600 物理** | 2560×1600 物理 | 2560×1600 物理 | 2560×1600 物理 |
| `BitBlt`，DPI-unaware 进程 | 1707×1067（被虚拟化） | 1707×1067 逻辑 | 1707×1067 逻辑 | 1707×1067 逻辑 |
| 手动缩放到 1568×980 | 1568×980 | 1568×980（与物理的 1.633× 关系需还原） | 1568×980 | 1568×980 |
| 虚拟桌面多屏拼接 | 物理虚拟桌面尺寸（原点可能为负） | 虚拟桌面物理 | 虚拟桌面物理 | 虚拟桌面物理 |

**推论：0~1000 归一化对"截图分辨率是什么"完全免疫**（只要你用同一张图的宽高反归一化）；**绝对像素/像素坐标则强绑定"我给你的那张图"**。这是 §D 推荐归一化的核心理由。

### 2.6 顺带一提：多显示器

- 虚拟桌面原点可能为**负**（副屏在主屏左侧时）。
- 每块屏可以有**不同的缩放比例**（Per-Monitor DPI）——"150%" 不是全局属性。
- 正确做法：按 `EnumDisplayMonitors` + `GetMonitorInfo` 取每块屏的**物理** `rcMonitor`；`GetDpiForWindow` / `GetDpiForMonitor` 取每块屏的缩放。
- 若用 `SendInput` + `MOUSEEVENTF_ABSOLUTE`，必须带 `MOUSEEVENTF_VIRTUALDESK`，且归一化基准是 `SM_XVIRTUALSCREEN`/`SM_YVIRTUALSCREEN` 给出的**整个虚拟桌面**。
- **在 Qwen3-VL 的 0~1000 方案下，多屏只要在"虚拟桌面拼接图"上归一化、再用同一个虚拟桌面矩形反归一化，就不会错**——这又是归一化的优势。

---

## 3. C. 精细化方案

### C.1 两级 / 多级 grounding：有哪些公开实现

**公开实现与论文（按时间）：**

| 工作 | 做法 | 关键结果 |
|---|---|---|
| **V\*** (2024) | 迭代放大：先整图定位，再切小块递归 | 开创"视觉搜索"范式，被 ScreenSpot-Pro 直接引用为 Iterative Zooming 的灵感来源 |
| **SeeClick** (ACL 2024) | GUI grounding 基础模型（ScreenSpot 基线），本身不含 crop | ScreenSpot avg **53.4**（后被 R-VLM 当基线） |
| **ScreenSpot-Pro** (arXiv 2504.07981, 2025-04) | 提出 **Iterative Zooming / Iterative Narrowing / ReGround / ScreenSeekeR** 四种多轮方法 | 见下表 |
| **R-VLM** (ACL 2025, arXiv 2507.05673) | 在 SeeClick 上做**两阶段 zoom-in**：① 整图出粗框（当 region proposal）② 按粗框裁 **约 5× 宽高**的区域、放大回全分辨率再预测，坐标映射回原图。**免训练**即可部署 | ScreenSpot avg **53.4 → 66.3 (+12.9)**；Desktop Icon **30.0 → 52.8 (+22.8)**，Web Icon 32.5 → 51.4 |
| **Iris** (2025, arXiv 2510.xxxx) | Information-Sensitive Cropping（不均匀裁剪，替代统一 zoom） | ScreenSpot avg **74.6** |
| **DRS-GUI** (CVPR 2026) | 免训练 Dynamic Region Search | — |
| **Qwen3-VL 官方** | `image_zoom_in_tool` + `search_tool`（Thinking with Images cookbook） | 官方工具形态 |

**ScreenSpot-Pro 的关键实验数据（高分辨率 GUI grounding，论文 arXiv:2504.07981）：**

| 方法 | ScreenSpot-Pro 准确率 | 说明 |
|---|---|---|
| OS-Atlas-7B（单轮） | **18.9%** | 论文中"最佳单轮模型"也只有 18.9% |
| ReGround（裁剪后重预测，crop 1024²） | **40.2%** | OS-Atlas-7B 从 18.9% → 40.2% |
| ScreenSeekeR（GPT-4o planner 引导级联搜索） | **48.1%** | 相对提升 254%，**无需额外训练** |
| OmniParser V2 + GPT-4o | 39.5% | 元素级路线 |
| UI-TARS-1.5 | **61.6** | 见 UI-TARS README 自评表 |
| OpenAI CUA | 23.4 | 同上 |
| Claude 3.7 | 27.7 | 同上 |

**ScreenSpot-Pro 的两条方法论结论（论文原文）：**
> "Our experiments reveal that **strategically reducing the search area enhances accuracy**."
> 最佳 crop 尺寸随模型而变：**OS-Atlas 1024²、UGround 768²**。

— [ScreenSpot-Pro arXiv:2504.07981](https://arxiv.org/abs/2504.07981) [论文] 2025-04-04 / [HTML 全文](https://arxiv.org/html/2504.07981v1)

**UI-TARS-1.5 vs Claude 3.7 vs OpenAI CUA 的 grounding 对比（UI-TARS 官方 README 自评表）：**

| Benchmark | UI-TARS-1.5 | OpenAI CUA | Claude 3.7 | Previous SOTA |
|---|---|---|---|---|
| ScreenSpot-V2 | **94.2** | 87.9 | 87.6 | 91.6 |
| ScreenSpotPro（高分辨率） | **61.6** | 23.4 | 27.7 | 43.6 |

— [bytedance/UI-TARS README](https://github.com/bytedance/UI-TARS/blob/main/README.md) [官方文档] 2025-04/09

> 这张表是 §C.4 的核心论据：**在"高分辨率 + 小目标"这个维度上，差距是 61.6 vs 23~28（2~3 倍）**，而在普通分辨率 ScreenSpot 上是 94.2 vs 87~88（只差 5~6 点）。**分辨率/小目标才是分水岭，不是坐标格式。**

**AnyClick**：本次检索**未找到**该名称对应的可核实论文/仓库（可能记错名）。⚠️ 标为未证实。

### C.2 "crop 内改用绝对像素"到底有没有用？——有数据支撑的结论

**先纠正一个容易混淆的点**：Qwen3-VL 训练时输出的是 **0~1000 相对坐标**，所以"crop 内用绝对像素"**不是模型直接给的**，而是**你在 crop 上做的换算**（`norm/1000 × crop_w` → 物理像素）。真正的问题是：**crop 之后，换算链条会不会更短/更准？**

**结论：crop 的收益主要来自 ②（观测误差），而不是 ①（量化误差）。**

| 对比 | 量化误差（理论极限） | 16px 图标在模型输入中的大小 | 结论 |
|---|---|---|---|
| 整屏 0~1000 归一化（Qwen3-VL 默认） | ±1.28 物理 px | 16.0 px | 基线 |
| 整屏 0~1000 归一化（Qwen2.5-VL 默认降采样后） | ±1.28 物理 px | **7.9 px** ⚠️ | 小图标被抹掉 |
| **crop 512×512 后再归一化** | ±(0.5×512/1000)= **±0.26 物理 px** ✅ | **16.0 px（不降采样）** ✅ | 两项都赢 |
| crop 1024×1024 后再归一化 | ±0.51 物理 px | 16.0 px | 量化误差也赢 |

**关键点：**
1. **量化误差确实会因为 crop 变小而线性变小**（crop 512 时 1 单位 = 0.512 物理 px，±0.5 单位 = ±0.26 px，比整屏的 ±1.28 px 小 5 倍）。**但这 1 px 级别的差异对点击成功率几乎无影响。**
2. **crop 真正的价值在于 ②**：把"整屏降采样后 7.9 px 的图标"变成"crop 内 16 px 的图标"。这是 2 倍以上的有效分辨率提升，对小图标是决定性的。
3. **实验佐证**：R-VLM 免训练两阶段 zoom-in 就拿到 ScreenSpot **+12.9** 点、Desktop Icon **+22.8** 点；ScreenSpot-Pro 上 ReGround 把 OS-Atlas-7B 从 **18.9% → 40.2%**。这些收益远超任何"坐标格式微调"能带来的量级。
4. **反例与边界**：Anthropic 官方实测发现，**把截图切成四象限平铺、叠加坐标网格、换重采样算法都无效**——只有"真正裁一块并放大"（zoom）才有效。[官方博客]

**因此，"crop 内用绝对像素"这个说法要修正为：**
> **crop 之后，模型端的输出格式（0~1000）不要变；变的是 crop 的尺寸。** crop 越小 → 归一化的每单位代表的物理像素越少 → 量化误差越小 + 图标越大。**工程端最终仍然落成物理像素。**

### C.3 元素级 grounding（OmniParser / UGround / UIA / SoM）——能绕开像素精度吗？

**能绕开"点偏"，但绕不开"看不见"。**

| 路线 | 代表 | 机制 | 代价 |
|---|---|---|---|
| **Set-of-Mark (SoM)** | OmniParser、GLM-4.5V web 模式 | 检测器给每个元素画编号框 + 文本列表，模型输出 **元素 ID**（`Click [1]`）而非像素 | 需要额外检测模型；截图上叠加标注会污染视觉；ID ↔ 坐标的映射表要自己维护 |
| **可访问性树 (UIA)** | Windows UI Automation、AX tree | 直接读控件的 `BoundingRectangle`（物理像素）+ 状态（enabled/checked/value） | **Windows UIA 延迟 1.6~1.9 s**（实测 Notepad/Run 对话框），预算不够时静默返回 0 节点 → 静默退化回像素；canvas / 游戏 / 远程桌面 / 未开 a11y 的 Electron 完全失效 |
| **专用 grounding 模型** | UGround-V1-7B/72B、OS-Atlas-7B | 输出 **0~1000 相对坐标** | 仍是像素路线；UGround-V1-7B ScreenSpot avg 86.3、72B 89.4，但 **ScreenSpot-Pro 只有 16.5**（高分辨率退化严重） |

**实测数据：**
- UGround-V1-7B ScreenSpot avg **86.3**、72B **89.4**；OS-Atlas-Base-7B **81.0**（ScreenSpot 标准设置）
- 同批模型在 **ScreenSpot-Pro** 上：UGround-7B **16.5**、OS-Atlas-7B **18.9** — **比标准 ScreenSpot 掉了 60+ 个点**
  — [HuggingFace `osunlp/UGround-V1-7B` 模型卡](https://huggingface.co/osunlp/UGround-V1-7B)、[OS-Atlas 论文](https://arxiv.org/abs/2410.23218) [官方模型卡/论文]
- **ScreenSpot-Pro 数据本身**：目标平均只占截图面积的 **0.07%**（ScreenSpot 是 2.01%）— [arXiv:2504.07981](https://arxiv.org/html/2504.07981v1#S3.SS4) [论文]
- OmniParser V2 + GPT-4o：ScreenSpot-Pro **39.5%**（官方自评）— [microsoft/OmniParser README](https://github.com/microsoft/OmniParser) [官方文档] 2025-01

**"元素级能否绕开像素精度问题？"—— 分三种情况：**

1. **有 UIA / SoM 元素清单时**：✅ **能绕开**。模型输出 ID，坐标由基础设施给出，**模型不再需要像素精度**。这是 OSWorld 的实证：a11y 树在当前模型能力下起"决定性作用"（[arXiv:2404.07972](https://arxiv.org/abs/2404.07972)）。
2. **无 UIA（canvas / 游戏 / 远程桌面 / Electron）时**：❌ **绕不开**。只能纯像素，而纯像素在高分辨率下就是 §C.1 的难题。
3. **OSWorld 里的反例**：WindowsAgentArena 上 **Agent S2（纯截图）52.8% 反而击败 NAVI（截图 + a11y 树）** — 说明感知栈够强时纯截图也能赢。⚠️ [二手报道]（见 [MIT AI Agent Index](https://aiagentindex.mit.edu/2025/ui-tars-desktop/)），未取到一手论文数字。

**结论（明确）：**
- **元素级 grounding 不能"解决"高分辨率像素精度问题，只能"绕开"——而且只在有 UIA/检测器时成立。**
- 它的真实价值是**兜底**（第一级就点不中时换一条路），不是主力。
- **在 Windows 桌面这个具体场景，UIA 值得接**（原生 Win32/Office 控件覆盖率高），但必须解决 UIA 的 1.6~1.9 s 延迟问题（提高超时预算到 ~5 s 并检查"是否返回 0 节点"）。
- **不要为了像素精度接 OCR**：OCR 给的是"文字在哪"，而你真正要点的常常是图标/按钮（文字只是标签）。OCR 解决的是"读得准"，不是"点得准"。

### C.4 业界实际在用哪种？

| 生态 | 坐标契约 | 是否自带 crop/zoom | 官方是否说明"缩放后要换算" |
|---|---|---|---|
| **Anthropic Claude** | **截图像素** | ✅ **有 `zoom` 动作**（`region:[x0,y0,x1,y1]`），且明确"zoom 后仍用整屏像素坐标" | ✅ 明确要求："若缩小了截图，必须把坐标放大回去" |
| **OpenAI CUA** | 声明的 display 像素（preview）/ 截图像素（GA） | GA 侧无内置 zoom（社区靠外部重截） | ✅ 官方强调 display 尺寸必须与实际截图一致 |
| **Google Gemini** | **0~1000 归一化** | ❌ 无 | ✅ 官方给出 `denormalize` 代码 |
| **Qwen3-VL** | **0~1000 归一化** | ✅ 有 `image_zoom_in_tool`（cookbook 形态） | ✅ 官方 docs 明确 relative coordinates |
| **UI-TARS 1.5/2** | 绝对像素（Q2.5-VL 血统） | ❌ **无 zoom 动作** | ✅ 官方 `README_coordinates.md` 专门教这个 |

**"跟随哪个生态最省事"的答案：**

- **想少写代码、且能接受"自己管缩图"** → **Claude computer use**。契约最简单（你给什么图，它给什么图的像素），而且**官方自带 zoom**，把 §C.1 的 crop 精定位直接做成了 API 动作，坐标空间不随 zoom 改变（这点设计得很好）。代价：图像 token 成本高、需要自己按 1568/1.15MP 缩图并换算坐标。
- **想用开源模型、要坐标空间对分辨率免疫** → **Qwen3-VL**。0~1000 与截图分辨率解耦 → **DPI / 多屏 / 缩放错配在数学上不可能发生**；且默认 16M px 上限让 2560×1600 整屏不降采样。
- **想直接用最成熟的 GUI agent 工程** → **UI-TARS-desktop**。但它绑死 Qwen2.5-VL 的绝对像素 + `smart_resize`，**你必须保证 `max_pixels ≥ 你的屏幕总像素**。

---

## 4. D. 结论与选型建议

### D.1 明确推荐：Qwen3-VL + 两级 grounding + 全程物理像素

**为什么是 Qwen3-VL 而不是 Qwen2.5-VL：**

| 判据 | Qwen2.5-VL | Qwen3-VL | 胜 |
|---|---|---|---|
| 坐标空间 | 绝对像素（smart_resize 后） | 0~1000 归一化 | **Qwen3-VL**（对 DPI/多屏免疫） |
| 默认 `max_pixels` | 1.0M → 2560×1600 降采样 2.03× | 16.8M → **不降采样** | **Qwen3-VL** |
| 16px 图标进模型 | 7.9 px ⚠️ | 16.0 px | **Qwen3-VL** |
| 视觉 token（2560×1600） | 1260 | 4000 | Qwen2.5-VL（但省的是信息） |
| 坐标还原是否需要知道内部尺寸 | **需要**（易错，官方 issue 833/866 大量同类问题） | **不需要** | **Qwen3-VL** |
| 自带视觉搜索工具 | ❌ | ✅ `image_zoom_in_tool` | **Qwen3-VL** |
| 输出 box 还是 point | box | **box + point** | **Qwen3-VL**（点击用 point 更准） |

### D.2 坐标系协议（可直接落地的规范）

#### 协议名：`PHYS-PX-v1`

**不变量（最重要的一条）：**
> **全局唯一坐标系 = 物理像素。原点 = 虚拟桌面左上角（可为负）。任何环节都不允许出现逻辑像素。**

**Step 0 — 进程与显示器初始化（只做一次）**
```
1. 进程启动第一行：SetProcessDpiAwarenessContext(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2)
2. 枚举显示器：EnumDisplayMonitors + GetMonitorInfo → 每块屏的物理 rcMonitor
3. 虚拟桌面：GetSystemMetrics(SM_XVIRTUALSCREEN / SM_YVIRTUALSCREEN / SM_CXVIRTUALSCREEN / SM_CYVIRTUALSCREEN)
4. 记录每块屏的缩放比：GetDpiForMonitor / GetDpiForWindow
```
**断言（自检）**：`GetSystemMetrics(SM_CXSCREEN)` 必须返回 **2560**（物理），不是 1707。返回 1707 说明 DPI 感知没生效 → 整个坐标链不可信，直接中止。

**Step 1 — 抓图（物理像素）**
- 首选 Windows Graphics Capture（WGC）或 DXGI Desktop Duplication
- 抓到的图尺寸必须 == 目标显示器的物理 `rcMonitor` 尺寸（2560×1600），断言 `img.size == (rect.width, rect.height)`
- 禁止在此步做任何缩放

**Step 2 — 一级粗定位（整屏）**
```
输入：整屏物理截图（2560×1600）+ 用户指令
模型：Qwen3-VL（0~1000 归一化）
输出格式要求模型用 point_2d（点击场景）或 bbox_2d
反归一化：x_phys = clamp(x_norm/1000, 0, 1) * 2560
          y_phys = clamp(y_norm/1000, 0, 1) * 1600
          ↑ 分母固定 1000，全项目统一；不要用 999
```

**Step 3 — 二级精定位（crop，物理分辨率）**
```
1. 以 (x_phys, y_phys) 为中心，取 CROP = 512×512 物理像素
   （从原始物理截图裁，不从模型输入裁）
2. 裁剪时把窗口夹到屏幕内：x0 = clamp(x_phys - CROP/2, 0, 2560 - CROP)
3. 送入模型：crop 图（512×512 物理像素，Qwen3-VL 下 **不缩放**，
   因为 512×512 = 262,144 px < 16.78M，且 512/32=16 整除 → 无损）
4. 模型输出 crop 内 0~1000 归一化坐标：
   x_crop_phys = x_norm/1000 * 512
   y_crop_phys = y_norm/1000 * 512
5. 加回偏移，得到全局物理像素：
   x_global = x0 + x_crop_phys
   y_global = y0 + y_crop_phys
```
**量化收益**：crop 512 时 1 归一化单位 = **0.512 物理 px**（±0.5 单位 = ±0.26 px），比整屏的 ±1.28 px 好 5 倍。crop 1024 时 ±0.51 px。

**Step 4 — 置信度门控（决定是否要第 3 级）**
```
若模型返回的框在 crop 内的尺寸 < 24 物理 px  → 说明模型没看清，触发更大 crop（如 1024）或换一级重定位
若二级结果与一级结果相差 > 64 物理 px → 触发一次「上一级候选区重定位」再试
最多 3 轮，仍不收敛则走 UIA / 元素级兜底
```

**Step 5 — 点击（物理像素）**
- `SetCursorPos(x_global, y_global)`（Per-Monitor-V2 aware 进程下即物理像素）
- 多屏 / 非主屏：用 `SendInput` + `MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK`，归一化到**整个虚拟桌面**矩形
  ```
  nx = round((x_global - virt_left)  / virt_width  * 65535)
  ny = round((y_global - virt_top)   / virt_height * 65535)
  ```
- **不要用 `pyautogui.click()`** 作为最终执行器（它的 DPI 行为依赖进程感知状态且不可控）；若必须用，先做 `SetProcessDpiAwarenessContext` 并实测验证

**Step 6 — 验证回路**
- 点击前把模型框的物理尺寸与目标真实尺寸（若 UIA 可得）比对
- 点击后取一次小范围截图确认状态变化；失败则回到 Step 3 用不同 crop 重试

**多显示器处理规则：**
- 每块屏独立走 Step 1→5（**不要**把多屏拼成一张大图送模型，除非确实需要跨屏目标）
- 若必须跨屏：拼接图 = 虚拟桌面物理矩形，坐标用虚拟桌面原点（可能为负）做偏移
- 归一化始终相对于"送进模型的那张图的宽高"，这样**跨屏也自动正确**

**DPI 1.5× 偏差的根除方式（4 条硬规则）：**
1. 进程 Per-Monitor-V2 aware（Step 0），否则**中止**
2. 抓图、模型坐标反归一化、点击，**三处全部用物理像素**
3. 代码里**禁止出现** `GetSystemMetrics(SM_CXSCREEN)` 用于任何坐标换算（它是逻辑值）
4. 单元测试：造一个"已知物理坐标"的按钮，验证 (x_norm → x_phys) 与实际点击落点误差 ≤ 2 物理 px

### D.3 硬件 / 模型选型（RTX 3060 Laptop 6GB）

| 选项 | 显存 | 判断 |
|---|---|---|
| **Qwen3-VL-4B-Instruct（AWQ/GPTQ 4-bit）** | 权重 ~2.5–3 GB；2560×1600 整屏 4000 视觉 token 的激活约 1.5–2 GB | ✅ **首选本地方案**。4B 是 grounding 精度与显存的平衡点 |
| Qwen3-VL-2B-Instruct（BF16） | 权重 ~4 GB | ⚠️ 勉强（激活后接近 6 GB 上限），4-bit 则很宽松，但 grounding 精度明显低于 4B |
| Qwen3-VL-4B（BF16） | ~8 GB | ❌ 6 GB 装不下 |
| Qwen3-VL-8B（4-bit） | ~5 GB + 激活 | ❌ 太紧，4000 视觉 token 的 KV cache 会 OOM；需配合更小的 `max_pixels`（如 1024×640）→ 牺牲小图标精度 |
| **API：qwen3-vl-flash / qwen3-vl-plus** | 0 | ✅ **推荐兜底 + 复杂任务**。国内走 DashScope，海外走 alibabacloud；价格极低（输入 ¥0.15/M 量级） |
| Anthropic Claude computer use API | 0 | ✅ 适合"已经有 zoom 动作、想少写代码"的场景 |

**推荐组合：**
> **本地 Qwen3-VL-4B（4-bit）跑常规点击；DPI/多屏等疑难场景 + 高价值任务走 qwen3-vl-flash API；两者的输出都是 0~1000，可以直接共用同一套坐标管线。**

**是否需要配合 OCR / 检测模型？**

| 组件 | 是否需要 | 理由 |
|---|---|---|
| **OCR**（PaddleOCR / RapidOCR） | ❌ **不需要** | 解决"读得准"，不解决"点得准"。你真正要点的常常是图标而非文字。若业务强依赖"点击某个带文字的按钮"，Qwen3-VL 自带 32 语言 OCR 能力，足够 |
| **通用检测模型**（YOLO / OmniParser） | ⚠️ **可选** | 只在做 **SoM 元素级**路线时才需要（即模型输出元素 ID）。但这条路线要多维护一份 ID↔坐标表，且对纯视觉任务反而弱于专门训练的 grounding 模型 |
| **UI Automation（UIA）** | ✅ **建议接，作为兜底** | 给出控件的**精确物理矩形 + 状态**（enabled/checked/value），能直接消除"点偏"和"点错控件"两类错误。**但必须处理 1.6~1.9 s 的 UIA 延迟**（提高超时到 ~5 s，并检查是否返回 0 节点——静默退化到像素是最危险的失败模式） |

**一句话**：**坐标精度这一层不要靠 OCR/检测模型；靠 crop 解决"看不清"，靠 UIA 解决"点不准"。**

### D.4 最终三选一（明确回答，不模棱两可）

| 方案 | 结论 |
|---|---|
| **"直接用 0~1000 归一化坐标"** | ✅ **作为第一级（粗定位）采用；❌ 不作为最终点击坐标。** 优点：与截图分辨率、DPI、多屏原点完全解耦（数学上不可能错配），量化误差仅 ±1.28 px。缺点：小目标在整屏尺度上仍只占 6~8 个归一化单位，且 0~1000 把物理精度稀释了 2.56 倍 |
| **"用像素坐标"** | ⚠️ **只在 crop 尺度上用。** 整屏绝对像素（如 Qwen2.5-VL）必须知道 `smart_resize` 后的内部尺寸，还原错了就是 1300 px 的灾难。crop 内的绝对像素则是最优落点：crop 尺寸固定、模型看到的是"原分辨率局部"，量化误差 ±0.26 px（crop 512） |
| **"裁剪后像素坐标"** | ✅✅ **最终推荐。本场景下的最优解。** 但要精确表述：**模型端输出格式仍然是 0~1000 归一化（Qwen3-VL 的原生训练空间，不要改）；crop 改变的是"模型看到多少像素"，不是"模型用什么单位"。工程端把两级结果都落成物理像素。** |

**一句话总结：**
> **模型端：全程 0~1000 归一化（Qwen3-VL 原生），一级定位整屏、二级定位 crop；工程端：全程物理像素；点击端：Per-Monitor-V2 aware + 物理像素。**
> **"归一化 vs 像素"之争是个伪命题——真正决定点击精度的是 (a) 模型看到的目标有多大、(b) 你的坐标有没有搞错空间。crop 解决 (a)，DPI 协议解决 (b)。**

---

## 5. 附录：本次调研的证据边界与未证实项

**已一手核实（源码 / 官方文档 / 论文）：**
- Qwen2.5-VL 绝对像素语义、28× patch 理论误差、默认 `max_pixels ≈ 1280×28×28`、训练侧 GT 换算要求
- `smart_resize` 完整实现（qwen-vl-utils + transformers v4.57.0）、`image_grid_thw` 为未 merge 网格
- Qwen3-VL 0~1000 归一化（技术报告原文）、`patch_size=16 / merge_size=2`、默认 `max_pixels=16,777,216`
- UI-TARS `action_parser.py` 的 `qwen25vl` 分支完整换算链 + `MAX_PIXELS=16384*28*28`
- Claude / OpenAI / Gemini / GLM-4.5V / Kimi-VL 的坐标契约（Claude、OpenAI、GLM 为一手；Gemini 为官方域名经搜索索引摘录）

**未直接连通（已用官方 URL + 官方仓库/镜像交叉验证，或已标注）：**
- `ai.google.dev/gemini-api/docs/computer-use`（多次网络失败）
- `platform.claude.com/docs/...`（重定向到 `www.anthropic.com` 后 404；内容取自官方文档页的搜索引擎索引 + Anthropic 官方博客）
- `platform.openai.com/docs/...`（Cloudflare 403；内容取自 OpenAI 官方 SDK 仓库文档 + 官方迁移指南 URL）
- Qwen3-VL 官方 cookbook `2d_grounding.ipynb` / `computer_use.ipynb`（文件 8.7~9.5 MB，超出抓取上限 5 MB）；已用技术报告 + README + preprocessor_config 覆盖其结论
- `huggingface.co`（DNS 解析到非公网 IP）；已用 `hf-mirror.com` 取得官方 `preprocessor_config.json` 原件

**明确未证实：**
- **AnyClick** —— 检索不到对应的可核实论文/仓库，名称可能有误
- **Doubao / Seed 1.6 官方坐标规范** —— 官方页面未给出，只有第三方封装的推断
- **Qwen3-VL-Flash API 坐标规范** —— 依据官方 issue + 系列一致性推断，未见官方 API 文档明文
- **GLM-4.1V** 的坐标空间 —— 仅核实了 GLM-4.5V 的官方 `agent.md`（0~999）；4.1V 按同系列推断
- **WindowsAgentArena 上 "Agent S2 纯截图 52.8% > NAVI 含 a11y 树"** —— 仅二手报道，未取一手论文数字

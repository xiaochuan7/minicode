# R10 — 有什么能直接抄的代码？（公开仓一手源码核查）

调研线：R10（reusable code）。对象：`likaixin2000/ScreenSpot-Pro-GUI-Grounding`（ScreenSpot-Pro 论文官方代码发布，ICLR 2025，arXiv:2504.07981）、`anthropics/anthropic-quickstarts`（computer-use-demo）、`microsoft/UFO`（UFO²/UFO³）、`modelscope/evalscope`。
取证方式：本轮共 **12 次 web 调用**（web_search ×1 + web_fetch ×11），全部命中一手源码；`screenseeker.py`（44.9 KB）已完整读取。Anthropic 的 computer tool 实现文件与 UFO 的 IoU 阈值**未取到**（web 预算耗尽），已在下文明确标注为未完成项。

---

## 0. 一句话结论

官方仓里**有 4 段可以直接抄的代码**，其中最值钱的两段恰好对准我们最大的两个漏斗漏洞：

1. **判定口径**：`eval_screenspot_pro.py::eval_sample_positive_gt` —— 命中 = 预测点落在 GT bbox 内（闭区间），**没有任何距离容差**，bbox 是像素 `[x1,y1,x2,y2]`，模型返回的 point 必须是 **0~1 归一化**。我们的「落点 ≤2 px」确实是自创口径，可以直接换成官方口径做主指标。
2. **裁剪窗口**：`models/methods/iterative_narrowing.py::visual_search` —— 窗口 = **当前 viewport 的 50% 线性尺寸**，以模型自己给的 bbox 中心为中心（不是固定的 192 px）。这一条把「crop 框住目标」的容差从我们的 **±96 px** 抬到 **±640 px（x 轴）**，正是「36.1% crop 没框住目标」的正面解法。

外加两个可抄件：`zoom_consistency_router.py` 给出**免标定置信信号**的官方定义（精定位点到 crop 中心的欧氏距离，在模型坐标帧里算），`screenseeker.py` 给出**「让模型自己确认框选对不对」**的官方验证提示词（is_target / target_elsewhere / target_not_found）——这正是我们「模型在挑错元素」那 43.7% 的公开对应物。

---

## 1. (c) 官方判定口径 —— `eval_screenspot_pro.py`

**仓库**：<https://github.com/likaixin2000/ScreenSpot-Pro-GUI-Grounding>
**文件**：`eval_screenspot_pro.py`（16,666 B，根目录）
**原始文件**：<https://raw.githubusercontent.com/likaixin2000/ScreenSpot-Pro-GUI-Grounding/main/eval_screenspot_pro.py>
**证据强度**：**【一手】**（完整读到了源码）

### 1.1 bbox 命中怎么算（可直接抄）

```python
def eval_sample_positive_gt(sample, response):
    bbox = sample["bbox"]
    bbox = [bbox[0], bbox[1], bbox[2], bbox[3]]  # x1, y1, x2, y2
    # bbox = [bbox[0], bbox[1], bbox[0]+bbox[2], bbox[1]+bbox[3]]  # x1,y1,w,h （官方注释掉的备选）
    img_size = sample["img_size"]
    bbox = [bbox[0]/img_size[0], bbox[1]/img_size[1],
            bbox[2]/img_size[0], bbox[3]/img_size[1]]     # ← 两轴各自除以宽/高
    click_point = response["point"]                       # may be None
    if click_point is None:
        return "wrong_format"
    if (bbox[0] <= click_point[0] <= bbox[2]) and (bbox[1] <= click_point[1] <= bbox[3]):
        return "correct"
    else:
        return "wrong"
```

抄下来的四条硬事实：

| 事实 | 官方写法 | 对我们的意义 |
|---|---|---|
| GT bbox 形状 | 像素 `[x1, y1, x2, y2]`（w,h 版本被注释保留） | 我们的 `sample.bbox` 已是 `(x1,y1,x2,y2)`，**形状不用改** |
| GT 归一化 | x 除 `img_size[0]`、y 除 `img_size[1]`，**两轴分别除** | 官方自己就是两轴分别除（与「不能用一个标量」一致） |
| 命中判据 | `bbox[0] <= px <= bbox[2] and bbox[1] <= py <= bbox[3]`，闭区间 | **无距离容差、无 ±px**；我们那条 ≤2 px 是自创的 |
| 解析失败 | 返回 `"wrong_format"`，计入 `num_total` 但不计 correct | 格式错 = 算错，不是「跳过」 |

指标聚合（`calc_metric_for_result_list`）：`action_acc`（总体）、`text_acc`（`ui_type=="text"`）、`icon_acc`（`ui_type=="icon"`）——**我们已经在报 icon/text 分桶，口径天然对齐**。
分组：`evaluate_fine_grained`（platform × application × instruction_style × gt_type）、`evaluate_seeclick_paper_style`、`evaluate_leaderboard_simple_style`（按 `group`）、`evaluate_leaderboard_detailed_style`（按 `application`）。

### 1.2 模型侧契约（我们做 adapter 时必须对上）

`main()` 里：

```python
point = response["point"]
point_in_pixel = [point[0]*img_size[0], point[1]*img_size[1]] if point else None
```

→ **模型的 `point` 必须是 0~1 归一化**（官方自己再乘回像素）。
→ **模型的 `bbox` 是 0~1 归一化**（`view_bbox_to_real_bbox` 全程归一化，见 §2）。
→ 负样本走 `ground_allow_negative`，判 `response["result"] in {"positive","negative"}`。

**我们能不能直接抄这一段？** 能，而且应该：
`src/minicode/ground/caliber.py` 现在的判定口径直接换成上面这段 6 行，保留旧口径作为对照列。这样我们报的数就是**官方榜单同口径**，可以和 UI-TARS-72B 37.1~38.1、GUI-Actor-44.6、RegionFocus 61.6 直接对比，不用再解释「我们的口径是自创的」。

### 1.3 顺带确认的「模型必须声明自己看到的图」

`main()` 里 `img_size` 取自标注（原始截图像素）。官方**不**在 prompt 里告诉模型图像尺寸，而是要求模型输出归一化坐标 —— 这比我们「在 prompt 里写『这张图是 1280×800』」的做法更稳（因为我们的图可能被服务端二次缩放）。**建议照抄：契约用归一化 0~1，prompt 里不写像素尺寸。**

---

## 2. (a) 官方三种「缩小搜索区」方法 —— `models/methods/`

目录：<https://github.com/likaixin2000/ScreenSpot-Pro-GUI-Grounding/tree/main/models/methods>
三个文件共享同一套坐标换算（**这是最该抄的一段**）：

```python
def view_bbox_to_real_bbox(view_bbox, viewport):
    view_x1, view_y1, view_x2, view_y2 = view_bbox
    x1, y1, x2, y2 = viewport
    w = x2 - x1; h = y2 - y1
    return [x1 + w*view_x1, y1 + h*view_y1, x1 + w*view_x2, y1 + h*view_y2]
```

`viewport` 是当前视窗在**整图归一化坐标**里的矩形；`view_bbox` 是模型在**当前视窗内**给的框。**全程归一化、从不混用像素**。
配套：

```python
def crop_with_padding(image, bbox, padding):   # padding 是归一化单位
    x1 = max(0.0, bbox[0]-padding); y1 = max(0.0, bbox[1]-padding)
    x2 = min(1.0, bbox[2]+padding); y2 = min(1.0, bbox[3]+padding)
    ... return (x1,y1,x2,y2), image.crop((int(x1*W), int(y1*H), int(x2*W), int(y2*H)))
```

### 2.1 `iterative_narrowing.py` —— **本次最高价值可抄件**

**文件**：`models/methods/iterative_narrowing.py`（7,636 B）
**URL**：<https://raw.githubusercontent.com/likaixin2000/ScreenSpot-Pro-GUI-Grounding/main/models/methods/iterative_narrowing.py>
**证据强度**：**【一手】**

关键参数（原文）：

```python
self.configs = configs if configs else {
    "max_search_depth": 3,
    "min_crop_size": 768,          # ⚠️ 此键在文件里从未被使用（死配置）
}
...
x1, y1, x2, y2 = view_bbox
view_center_x = (x1 + x2) / 2
view_center_y = (y1 + y2) / 2
ax1 = max(0, view_center_x - 0.25); ay1 = max(0, view_center_y - 0.25)
ax2 = min(1, view_center_x + 0.25); ay2 = min(1, view_center_y + 0.25)
_, image = crop_with_padding(image, target_area, padding=0)
viewport = view_bbox_to_real_bbox(target_area, viewport=viewport)
```

**语义（可直接抄的参数）**：
- 裁剪窗口 = **当前 viewport 的 50% 线性尺寸**（中心 ±0.25 归一化），**以模型自己刚给出的 bbox 中心**为中心。
- `padding=0`（不额外留边）。
- 迭代 3 次 = **3 次 grounder 调用**；前 2 次裁剪，第 3 次（depth=2）直接出答案。
- 落点 = **最终 bbox 的中心** `[(bbox[0]+bbox[2])/2, (bbox[1]+bbox[3])/2]`。

**换算到我们的机器（【推断】，基于上面的一手代码 + 我们的物理分辨率 2560×1600）**：

| | 官方 narrowing | 我们现在（`DEFAULT_CROP_WIDTH_PX=192`） |
|---|---|---|
| 第 1 步裁剪窗口 | 1280×800 物理 px（viewport 的 50%） | 192×192 物理 px |
| 目标被框住的容差 | ±640 px (x) / ±400 px (y) | ±96 px (x) / ±96 px (y) |
| 容差比 | 6.7× / 4.2× | 1× |

我们的粗点 p90 误差是 1451 px，落在 192 px 窗口外是必然事件 —— 官方这套窗口正好覆盖 p75~p90 区间。**这是「36.1% crop 没框住目标」最直接的一行改动。**

### 2.2 `reground.py` —— 固定 1024×1024 裁剪

**文件**：`models/methods/reground.py`（5,868 B）
**URL**：<https://raw.githubusercontent.com/likaixin2000/ScreenSpot-Pro-GUI-Grounding/main/models/methods/reground.py>
**证据强度**：**【一手】**

```python
def visual_search(self, instruction, image):
    view_bbox, real_bbox = self.ground_with_grounder(instruction, image, viewport=(0, 0, 1, 1))
    if not real_bbox: return False, None
    cropped_box, focused_image = crop_to_center(image, real_bbox, crop_size=[1024, 1024])
    view_bbox, real_bbox = self.ground_with_grounder(instruction, focused_image, viewport=cropped_box)
    return True, real_bbox
```

`crop_to_center` 的边界处理可直接抄：先按 bbox 中心裁 1024×1024，贴边时**向反方向平移补足**（贴左则向右补、贴右则向左补），保证窗口**永远是 1024×1024**（不缩水）：

```python
if new_x1_pixel == 0:  new_x2_pixel = min(img_width,  new_x1_pixel + crop_width)
if new_x2_pixel == img_width: new_x1_pixel = max(0, new_x2_pixel - crop_width)
```

**关键参数值**：`crop_size = [1024, 1024]`（像素），**从原始分辨率图上裁**（不缩图），共 2 次 grounder 调用，落点取最终 bbox 中心。
在 2560×1600 上：1024×1024 = 宽 40%、高 64%，容差 ±512 px；且**图标以原生像素进模型**（2560 宽的图里 27 px 的图标 → 1024 宽的 crop 里仍是 27 px，而粗定位图里被缩到 13.5 px）。**我们现在的 192 px crop 也是原生物理像素，这一条我们做对了；差别只在窗口大小。**

### 2.3 `iterative_focusing.py` —— 四象限网格搜索

**文件**：`models/methods/iterative_focusing.py`（7,096 B）
**URL**：<https://raw.githubusercontent.com/likaixin2000/ScreenSpot-Pro-GUI-Grounding/main/models/methods/iterative_focusing.py>
**证据强度**：**【一手】**

每步取 bbox 中心所在的象限（0.5×0.5 归一化）裁剪，3 步 = 线性 4× 放大（面积 16×）。
**不适用/风险**：象限选择是**离散**的，粗定位偏一点就会选错象限且无法回退；narrowing 是**连续**的（以中心 ±0.25 重定位），粗定位偏了仍然收敛。**我们应抄 narrowing，不抄 focusing。**

### 2.4 `screenseeker.py` —— 级联 + 多候选 + 自验证（完整读过）

**文件**：`models/methods/screenseeker.py`（44,922 B，官方仓最大文件）
**URL**：<https://raw.githubusercontent.com/likaixin2000/ScreenSpot-Pro-GUI-Grounding/main/models/methods/screenseeker.py>
**证据强度**：**【一手】**

可抄参数一览：

| 参数 | 值 | 出处（函数） |
|---|---|---|
| 搜索深度 | `max_search_depth = 3` | `ScreenSeekeRMethod.__init__` |
| 最小裁剪尺寸 | `min_crop_size = [1280, 768]`（像素） | `__init__` |
| 最大决策尺寸 | `max_desicion_size = [1280, 768]` | `__init__` |
| 长宽比上限 | `max_aspect_ratio = 2.5` | `__init__` / `auto_dilate_patch` |
| 目标长宽比 | `target_ratio = 16/9`，`num_splits = 2` | `auto_dilate_patch` |
| 投票打分 | 高斯核 `mu=0.5, sigma=0.3`（patch 内相对坐标） | `score_patch` |
| 去重 | NMS，`iou_threshold = 0.5`，`keep="order"` | `non_maximum_suppression` |
| 规划器 | `gpt-4o-2024-05-13`，`temperature=0.0`，`max_tokens=2048` | `__init__` / `chat` |

**四个可抄件，按性价比排序**：

1. **自验证提示词（最该抄）** `EXISTENCE_PROMPT`：把候选框**画成红框**喂回模型，问三选一
   `is_target` / `target_elsewhere` / `target_not_found`，且 `target_elsewhere` 时**要求改写成更具体的指令**再在**同一视窗**里重问一次（`try_ground_in_patch`）。
   → 这就是「模型在挑错元素」的**官方检出机制**。我们的 43.7% 回落里「挑错元素」占 22.8 pp，这段提示词可以直接搬。
2. **区域膨胀策略** `auto_dilate_patch`：候选框不足 `min_crop_size`（1280×768 像素）就**补边**，长宽比超过 2.5 就沿长边切成 2 份。→ 保证裁剪窗口永远 ≥ 1280×768 物理像素，是 §2.1 固定比例窗口的**绝对下限版**。
3. **高斯投票** `score_patch`（mu=0.5, sigma=0.3）：多个候选框用「离中心的距离」加权打分，而不是取第一个命中。→ 我们的「固定 50 px 判据」本质是 sigma=0 的特例，可换成连续打分。
4. **NMS iou=0.5**：候选区域去重。
   （`smart_crop`、`dilate_box`、`plot_annotated_image`（红框宽 5 px、红点半径 10 px）可作为画框实现参考。）

**不适用**：ScreenSeekeR 的 planner 需要**第二个模型**（gpt-4o）每层做「哪里可能有目标」的推理，成本和我们的 API 通道预算不符；`POSITION_INFERENCE_PROMPT_TOP_LEVEL` 也要求模型用 `<element>/<area>/<neighbor>` 结构化描述，通用 API 模型遵循度未验证。→ **只抄第 1、2、3 条，不抄 planner 流程。**

---

## 3. (b) 免标定置信信号 —— `models/zoom_consistency_router.py`

**文件**：`models/zoom_consistency_router.py`（10,116 B）
**URL**：<https://raw.githubusercontent.com/likaixin2000/ScreenSpot-Pro-GUI-Grounding/main/models/zoom_consistency_router.py>
**证据强度**：**【一手】**（完整读到源码）

官方定义与参数：

```python
CROP_RATIO = 0.5          # 裁剪窗口 = 图像宽/高的 50%

def _compute_crop_box(self, abs_x, abs_y, img_w, img_h):
    crop_w = int(img_w * CROP_RATIO); crop_h = int(img_h * CROP_RATIO)
    x1 = max(0, cx - crop_w // 2); y1 = max(0, cy - crop_h // 2)
    x2 = min(img_w, x1 + crop_w); y2 = min(img_h, y1 + crop_h)
    if x2 - x1 < crop_w: x1 = max(0, x2 - crop_w)      # 贴边时向内平移补足
    if y2 - y1 < crop_h: y1 = max(0, y2 - crop_h)
    return x1, y1, x2, y2

def _zoom_consistency(self, step2):
    dx = step2[0] - 500.0; dy = step2[1] - 500.0     # 1000×1000 帧的中心
    return math.sqrt(dx*dx + dy*dy)                   # 精定位点到 crop 中心的欧氏距离
```

坐标帧声明（写在 SYSTEM_PROMPT 里）：`"* The screen's resolution is 1000x1000."`，模型回 `coordinate: [x, y]`，换算 `abs_x = step1[0]/1000.0*orig_w`、**`abs_y = step1[1]/1000.0*orig_h`（两轴分别除）**。
zoom 步骤：`cropped = image.crop(box).resize((orig_w, orig_h), Image.LANCZOS)` —— **裁剪后放大回原图尺寸**，所以送进模型的图尺寸不变、目标相对放大 2×。
路由规则：两个模型各跑一遍 2-step zoom，**选 zoom consistency 更小（更靠近中心）的那个**；某一路解析失败（cons=None）就用另一路。

**我们能不能直接抄？**
- **能抄信号本体**：`zoom_consistency = ‖精定位点 − crop 中心‖`，在**模型坐标帧**里算，与标定无关。我们现有的 `ground/gate.py::DEFAULT_DEVIATION_TOLERANCE_PX = 50.0` 判据（参考点=crop 中心）**测的就是同一个量**，只是阈值和量纲是自己定的。→ 把 50 px 换算成「crop 宽度的百分比」（官方量纲是 1000 帧，crop 中心=500），阈值就能跨分辨率复用，不用再随 crop 尺寸变（我们 192 crop 时系数是 5.208、512 crop 时是 1.953，这个系数漂移正是我们自己踩的坑）。
- **不能照抄路由部分**：官方是在**两个模型**之间选，我们需要的是**同一模型两次输出之间**的置信度（例如「粗定位 bbox 中心」vs「精定位点」的一致性），这属于改造，不属于照抄。
- **可直接抄的裁剪几何**：`_compute_crop_box` 的「50% 比例 + 贴边向内平移补足」与我们 §2.1 的连续窗口是同一族；差别是官方按**比例**（抗分辨率变化），我们按**固定像素**（分辨率一变就失配）。

---

## 4. (d) Anthropic computer use quickstart —— **未完成（web 预算耗尽）**

**仓库**：<https://github.com/anthropics/anthropic-quickstarts/tree/main/computer-use-demo>
**证据强度**：**【一手】**（仅 `loop.py`）／**【推断】**（tools 文件路径与 zoom 实现）

本轮实际抓到的一手内容：`computer-use-demo/computer_use_demo/loop.py`（HTTP 200）。
- 它**不含任何坐标换算**：只负责采样循环、prompt caching 断点注入（`_inject_prompt_caching`，3 个断点）、老截图裁剪（`_maybe_filter_to_n_most_recent_images`）。
- 它从 `.tools` 包导入 `TOOL_GROUPS_BY_VERSION / ToolCollection / ToolResult / ToolVersion`，从 `.tools.base` 导入 `ToolFailure` → **说明 computer tool 的实现已从单文件 `tools.py` 拆成 `tools/` 包**。我按旧路径试 `computer_use_demo/tools.py` → **HTTP 404**。

**因此本轮没能给出**：坐标帧声明原文、缩放实现、越界检查、`zoom` 动作实现。**下一步该读的确切位置**（我没有打开，不给行号）：
`https://github.com/anthropics/anthropic-quickstarts/tree/main/computer-use-demo/computer_use_demo/tools/`
（找 `computer.py` / `base.py`；`base.py` 里应有 `ToolCollection` 与版本号定义。）

**可作为坐标口径的官方文档页（本轮未抓，标【二手】）**：
<https://docs.claude.com/en/docs/agents-and-tools/tool-use/computer-use-tool>
（该页给出「downscale 2× 后发送 + 坐标减半」的做法，与我们 §5 的结论方向一致；注意 RegionFocus 的反证：downscaling 会伤小控件 grounding。）

**对本线的实际影响**：Anthropic 官方做法是**「图像缩放 + 坐标减半」**，即**接受有损缩放**；而 ScreenSpot-Pro 官方仓的 ReGround/narrowing 是**「裁剪 + 原生像素」**，即**避免有损缩放**。两者的交集做法是：**保留原始像素，只在坐标上做无损线性变换**。对我们（通用 API 模型 + 真实 2560×1600 桌面）应该走后者。

---

## 5. (e) UFO² hybrid detection —— **部分不适用**

**文档**：<https://github.com/microsoft/UFO/blob/main/documents/docs/ufo2/core_features/control_detection/overview.md>
**证据强度**：**【一手】**（文档原文）／**【推断】**（IoU 阈值与坐标空间未取到）

一手拿到的原文与配置：

| 方法 | 描述 |
|---|---|
| **UIA** | Windows UI Automation 检测标准控件 |
| **Visual (OmniParser)** | 视觉检测自定义控件/图标 |
| **Hybrid** | 「Combines both UIA and OmniParser detection methods. Merges results from both approaches, **removing duplicates based on IoU overlap**」 |

```yaml
# config/ufo/system.yaml
CONTROL_BACKEND: ["uia"]                    # 默认、官方推荐
CONTROL_BACKEND: ["omniparser"]
CONTROL_BACKEND: ["uia", "omniparser"]      # hybrid
```

**没拿到的**：IoU 去重的**具体阈值**、合并后的坐标空间（物理 vs 逻辑像素）、以及实现文件（`documents/docs/ufo2/core_features/control_detection/hybrid_detection.md` 与 `ufo/agents/ufo2/**`）。下一轮应直接读这两个位置。

**适用性判断**：**对本线（ScreenSpot-Pro 抽样 grounding）不适用** —— ScreenSpot-Pro 是 Linux/macOS/Windows 静态截图，**没有 UIA 可用**，UFO 的 hybrid 在这个 bench 上无法复现。
**对产品侧（Windows 真机）适用**：UIA 的「结构化 bbox + 语义」正好能给我们提供**第二个候选来源**，用 IoU 去重后与 VLM 的落点做**二选一投票**——这与我们「挑错元素」占 22.8 pp 的根因对得上（UIA 给的是控件矩形，VLM 给的是像素点，两者不一致本身就是信号）。但**这属于另一条线（真机执行/UIA 层）**，本线不展开。

---

## 6. (f) 能直接跑我们 ScreenSpot-Pro 抽样的现成评测脚本

### 6.1 首选：官方 `eval_screenspot_pro.py` + 我们写一个 30 行 adapter

**文件**：<https://github.com/likaixin2000/ScreenSpot-Pro-GUI-Grounding/blob/main/eval_screenspot_pro.py>
**并行版**：<https://github.com/likaixin2000/ScreenSpot-Pro-GUI-Grounding/blob/main/eval_screenspot_pro_parallel.py>（14,540 B，官方为提速另写的版本；本轮未读，标【推断】存在）
**证据强度**：**【一手】**（主脚本）

官方脚本对模型的接口就是两个方法（`model_factory.py` 负责实例化）：

```python
model.ground_only_positive(instruction=..., image=img_path)
    -> {"result": "positive", "bbox": <归一化 bbox>, "point": <归一化点>, "raw_response": ...}
model.ground_allow_negative(instruction=..., image=img_path)
```

**我们的接入方式（不复制官方代码，只写 adapter）**：
新建 `src/minicode/eval/sspro_adapter.py`，实现一个类，两个方法内部调用我们现成的 `ground/channel.py::TwoStageGrounder`，把物理像素落点转成 0~1 归一化返回。官方脚本自带：
- 指标计算（action/text/icon acc）、分组（fine-grained / seeclick-style / leaderboard style）；
- **逐样本 `details`（含 `correctness`、`pred`、`bbox`、`gt_type`、`ui_type`、`application`）写进 JSON** → **我们的 McNemar 配对检验可以直接吃这个 JSON**，不必再自己写一套统计。
- 命令行：`--model_type ... --screenspot_imgs ... --screenspot_test ... --task ... --inst_style ... --language ... --gt_type ... --log_path ...`。

**注意**：官方脚本**要求整份标注跑**（`--task all` 会遍历目录里所有 json）。要跑我们的 183 条抽样，需要先把抽样写成官方格式的 json（`bbox`/`img_size`/`instruction`/`ui_type`/`application`/`platform`/`group` 字段），放一个临时 annotations 目录即可 —— 这比改官方脚本更省事。

### 6.2 备选：EvalScope 的 ScreenSpot-Pro 适配器（答案解析口径可单独抄）

**文件**：<https://github.com/modelscope/evalscope/blob/main/evalscope/benchmarks/screenspot_pro/screenspot_pro_adapter.py>
**原始文件**：<https://raw.githubusercontent.com/modelscope/evalscope/main/evalscope/benchmarks/screenspot_pro/screenspot_pro_adapter.py>
**证据强度**：**【一手】**

可抄的四段：

**(1) 提示词模板**（官方等价物，normalized 口径 + 强制答案行）：
```python
PROMPT_TEMPLATE = (
    'Identify the UI element for the instruction and give a single click point. '
    'Coordinates must be normalized to the range 0 to 1 relative to the image size. '
    'Do not output a bounding box.\n'
    'Instruction: {instruction}\n'
    'End your reply with the final answer on its own last line, formatted exactly as: Answer: [x, y]'
)
```

**(2) 答案解析口径**（`extract_answer` + `utils.parse_point`）：优先读**最后一行 `Answer: [x, y]`**；退化时扫 `[x, y]` 对或 `<bbox>` 标签；`x=.., y=..` 这类松散写法**只允许在答案行上**接受（散文里会误抓版面边界和序数）；**回复被截断（没走到答案行）→ 判 0 分，不许从推理过程里编一个点出来**。→ 这条对通用 API 模型特别重要，我们的 `ground/prompt.py` 解析器应当照抄这个「答案行 + 截断=0 分」的纪律。

**(3) 归一化的量级规则**（`utils.to_normalized_point`）：
> 值在 `[0,1]` → 视为归一化；值 `≤1000` → 视为**千分格**（很多 VLM 的 0~1000 输出）；更大 → 视为**模型实际收到的那张图的像素**。

配套铁律：**「pixel-space predictions are normalized with the size of the image actually sent」** —— 用 **sent_size**（真的发出去的那张图的尺寸）归一化，不是原图尺寸。我们如果在服务端把 2560×1600 缩到 1280×800 再发，模型若回的是**发送图的像素**，必须除以 1280×800；回的是**原图像素**就必须除以 2560×1600。**这两个量纲必须由我们在请求里声明清楚**（官方建议在 prompt 里显式说明坐标帧，或干脆只接受 0~1）。

**(4) 判定**：`point_in_bbox(point, bbox_norm)`，即 bbox 命中，与 §1.1 一致；`ui_type` 决定 `text_acc` / `icon_acc`；subset = 6 个 group（CAD/Creative/Dev/OS/Office/Scientific），数据集 `lmms-lab/ScreenSpot-Pro`，split 名为 `train`（当作评测集用）。

**能不能直接跑？** EvalScope 是**独立框架**（需要 `pip install evalscope` + 它自己的 model 接入层），直接跑我们自研 API 通道要写它的 model adapter，成本高于 §6.1。
→ **建议：拿它的「答案行解析 + 量级归一化 + sent_size 归一化」三段规则，不拿它的框架。** 评测执行走 §6.1 的官方脚本。

### 6.3 明确「不适用」的
- `arthurkatcher/jev-click-bench`（<https://github.com/arthurkatcher/jev-click-bench>）：第三方做的「多次 zoom 逐次提升命中率」的 bench（自述 ScreenSpot-Pro 普通屏 84%、4K 专业软件 19%，二次 zoom 43%、三次 60%）。**数字来自其自述、未核实，且其口径是自建 bench，不适合当我们的 A/B 判据**（标【二手】）。

---

## 7. 汇总表：哪些能直接抄

| # | 抄什么 | 来源文件（URL） | 关键参数 | 我们的文件 | fit |
|---|---|---|---|---|---|
| 1 | bbox 命中判定（无容差） | `eval_screenspot_pro.py::eval_sample_positive_gt` | 闭区间 `x1<=px<=x2 and y1<=py<=y2`；GT 像素 `[x1,y1,x2,y2]`；point 须 0~1 | `src/minicode/ground/caliber.py` | 直接可用 |
| 2 | 归一化 viewport 换算 | `models/methods/*.py::view_bbox_to_real_bbox` | `real = origin + size*view`（全程归一化） | `src/minicode/coord/crop_refine.py` / `ground/geometry.py` | 直接可用 |
| 3 | **裁剪窗口 = viewport 的 50%，以 bbox 中心为准** | `models/methods/iterative_narrowing.py::visual_search` | ±0.25 归一化、padding=0、depth=3、落点=bbox 中心 | `src/minicode/ground/channel.py`（`DEFAULT_CROP_WIDTH_PX=192`） | 直接可用 |
| 4 | 固定 1024×1024 原生裁剪 + 贴边补足 | `models/methods/reground.py::crop_to_center` / `visual_search` | `crop_size=[1024,1024]`，2 次调用 | 同上 | 需改造（窗口大小改按比例） |
| 5 | 免标定置信信号 | `models/zoom_consistency_router.py::_zoom_consistency` | `‖p2−(500,500)‖`（1000×1000 帧）；CROP_RATIO=0.5；crop→resize 回原尺寸 | `src/minicode/ground/gate.py`（`DEFAULT_DEVIATION_TOLERANCE_PX=50.0`） | 需改造（量纲改成 crop 宽度百分比） |
| 6 | 自验证提示词 + 改写重问 | `models/methods/screenseeker.py::EXISTENCE_PROMPT` / `try_ground_in_patch` | 三选一 is_target / target_elsewhere / target_not_found + new_instruction；红框宽 5 px、红点半径 10 px | `src/minicode/ground/prompt.py` | 直接可用（新增一条自检调用） |
| 7 | 候选框补边到 ≥1280×768、长宽比 ≤2.5 切 2 份 | `models/methods/screenseeker.py::auto_dilate_patch` | `min_crop_size=[1280,768]`, `max_aspect_ratio=2.5`, `target_ratio=16/9`, `num_splits=2` | `src/minicode/ground/channel.py` | 直接可用（作为窗口下限） |
| 8 | 高斯投票 + NMS 去重 | `screenseeker.py::score_patch` / `non_maximum_suppression` | `mu=0.5, sigma=0.3`；`iou_threshold=0.5`, `keep="order"` | `src/minicode/ground/gate.py` | 需改造 |
| 9 | 答案行解析 + 量级归一化 + sent_size 归一化 | `evalscope/.../screenspot_pro_adapter.py` + `utils.py` | `Answer: [x, y]` 末行；≤1 归一 / ≤1000 千分格 / 否则 sent_size 像素；截断=0 分 | `src/minicode/ground/prompt.py` | 直接可用 |
| 10 | 官方评测 harness + 逐样本 details JSON | `eval_screenspot_pro.py` | `--model_type/--task/--inst_style/--language/--gt_type/--log_path` | 新增 `src/minicode/eval/sspro_adapter.py` | 需改造（写 adapter） |
| 11 | 四象限网格搜索 | `models/methods/iterative_focusing.py` | 4 象限 0.5×0.5，depth=3 | — | **不适用**（离散象限，粗定位偏一点就不可恢复；narrowing 更好） |
| 12 | ScreenSeekeR planner 流程 | `screenseeker.py::POSITION_INFERENCE_PROMPT_TOP_LEVEL` | gpt-4o planner + `<element>/<area>/<neighbor>` | — | **不适用**（要第二个模型 + 结构化输出，通用 API 模型遵循度未验证） |
| 13 | UFO hybrid（UIA+视觉 IoU 去重） | `documents/.../control_detection/overview.md` | `CONTROL_BACKEND: ["uia","omniparser"]`；按 IoU 去重（阈值未取到） | `src/minicode/uia/*`（另一条线） | **本线不适用**（ScreenSpot-Pro 无 UIA）；产品侧适用 |

---

## 8. ≤8 条最小改动清单（按投入产出排序）

| # | 改哪个文件 | 做什么（抄哪一段） | 预期哪个数变多少 |
|---|---|---|---|
| 1 | `src/minicode/ground/channel.py`（`DEFAULT_CROP_WIDTH_PX/HEIGHT_PX` = 192，crop 规划处） | 裁剪窗口从**固定 192 px** 改为**当前 viewport 的 50% 线性尺寸、以粗定位 bbox 中心为中心、从物理帧裁**（抄 `iterative_narrowing.py::visual_search` 的 ±0.25 归一化 + `view_bbox_to_real_bbox`）。同时把 `crop_width/height` 参数语义从「像素」改成「比例」，保留绝对下限 512 px | 「crop 没框住目标」占比 36.1% → 目标 <10%（容差 ±96 px → ±640 px）；预计接线错 66/101 的那一层里大部分转为命中，总分目标 **44.8% → 50%±**（需按配对设计实测） |
| 2 | `src/minicode/ground/caliber.py` | 判定改成官方 bbox 命中（闭区间，**不加距离容差**），GT 用像素 `[x1,y1,x2,y2]`，point 先归一化 0~1 再比（旧口径保留为 `legacy_*` 列做对照） | 我们的数字变为**官方榜单同口径**，可直接对表 UI-TARS 37.1~38.1 / GUI-Actor 44.6 / RegionFocus 61.6；预期 icon/text 分桶数值向官方重算（不产生命中率变化，只是口径对齐） |
| 3 | `src/minicode/ground/prompt.py`（粗定位提示 + `parse_coarse_target`） | 按 EvalScope 口径改造：提示词要求 **0~1 归一化坐标**、**末行 `Answer: [x, y]`**、**不要输出 bbox**（若要 bbox 也需归一化）；解析器照抄 `extract_answer` 的「答案行优先 + 截断=0 分 + 松散写法仅在答案行接受」 | 格式错样本归零、跨运行答案格式漂移下降（官方口径下 `wrong_format` 计入分母，直接拉低总分）；预计 +1~3 pp 且方差下降 |
| 4 | `src/minicode/ground/prompt.py` / `channel.py`（请求侧） | 显式声明**发送图尺寸**并按 sent_size 归一化回物理像素（抄 EvalScope「用实际发送尺寸归一化」）；或者坚持 0~1 契约、禁止模型回像素（官方 `eval_screenspot_pro.py` 的契约） | 消除「0~1000 千分格 vs 0~1 归一 vs 像素」三义性导致的系统性偏移；预计粗点 p90 误差 1451 px 中属于**量纲错配**的那部分归零（量级需实测） |
| 5 | `src/minicode/ground/gate.py`（`DEFAULT_DEVIATION_TOLERANCE_PX = 50.0`） | 判据从「固定 50 px」改成**crop 宽度百分比**（官方量纲：`‖p2 − crop 中心‖` 在模型坐标帧里算，除以 crop 归一化宽度）；50 px 保留为 192-crop 的等价特例 | 阈值不再随 crop 尺寸漂移（192 时系数 5.208、512 时 1.953 这个坑消失）；放宽判据的净收益此前测为 +2.64 pp，新口径下预期同量级或略高，且**跨 crop 尺寸可比** |
| 6 | `src/minicode/ground/channel.py` + `ground/prompt.py` | 新增**可选的自验证调用**（抄 `screenseeker.py::EXISTENCE_PROMPT`：把候选框红框标注回传，问 is_target/target_elsewhere/target_not_found；答 target_elsewhere 时用 `new_instruction` 在同视窗重问一次）。做成开关，默认关，bench 里开 | 针对「挑错元素 22.8 pp」：预期在**粗定位已错**子集上再回收一部分（官方 ScreenSeekeR 消融方向一致）；成本 +1~2 次模型调用/样本，需在 MDE@330=4.84 pp 的预算下单独验证 |
| 7 | 新增 `src/minicode/eval/sspro_adapter.py` | 实现官方 model 接口 `ground_only_positive` / `ground_allow_negative`，返回 `{"result","bbox"(归一化),"point"(归一化),"raw_response"}`；再准备一份 183 条抽样、官方 json 格式的 annotations 目录 | 直接跑官方 `eval_screenspot_pro.py` → 拿到官方口径的 `action_acc/text_acc/icon_acc` **和逐样本 details JSON**；预期：**我们与官方 harness 的数字差 <0.5 pp**（口径对齐验收项），并直接拿到 McNemar 所需的逐样本正确性 |
| 8 | `src/minicode/ground/bench.py` / `numbered_bench.py`（`CROP_SCAN_SIZES = (128,192,256,512,768,1024,1280)`） | 裁剪扫描从**固定像素扫描**改成**比例扫描**：`ratio ∈ {0.5, 0.25, 0.125} × 绝对下限 ∈ {512, 1024}`（下限取自官方 `min_crop_size=[1280,768]` 与 ReGround `1024×1024`） | 选尺寸不再依赖「本机 150% DPI 下的经验值」，换机器/换分辨率可直接复用；预期在真实机 2560×1600 上最优档位落在 **ratio 0.25~0.5 / 下限 1024**（对应 640~1280 px），与本轮官方参数一致 |

---

## 9. 未完成 / 存疑（下一轮补）

1. **Anthropic computer tool 实现**：只拿到 `loop.py`（无坐标逻辑），旧路径 `tools.py` 已 404，tools 已拆成包。下一轮读 <https://github.com/anthropics/anthropic-quickstarts/tree/main/computer-use-demo/computer_use_demo/tools/>，取：坐标帧声明原文、缩放/越界检查、是否有 `zoom` 动作及其实现。**在此之前，关于 Anthropic 缩放建议的所有说法都标【二手】。**
2. **UFO² hybrid 的 IoU 阈值与坐标空间**：读 <https://github.com/microsoft/UFO/blob/main/documents/docs/ufo2/core_features/control_detection/hybrid_detection.md> 与 `ufo/agents/ufo2/**` 的合并实现。文档只确认「按 IoU 去重」这一句，阈值未取到。
3. **官方论文里的方法对比表**：本轮未读 arXiv:2504.07981 的表格，因此报告里**没有引用**「ReGround / Narrowing / ScreenSeekeR 各自提升了多少 pp」这类论文数字；报告里出现的所有参数值都来自**源码常量**（可核）。下一轮可补 <https://arxiv.org/abs/2504.07981> 的 Table（搜索区缩小方法对比）作为公开数字锚点。
4. **`eval_screenspot_pro_parallel.py`**：官方并行版存在（14,540 B），本轮未读内容，仅确认路径存在；跑 183 条抽样时是否需要它，待定。
5. **官方标注格式细节**：官方 json 的 `bbox`/`img_size` 字段名与我们的 `sample.bbox` 对齐情况，本轮只从代码侧确认，未抽样核对（改第 7 条时一次性核）。

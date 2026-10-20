# 09 — GUI 智能体「zoom / crop 精定位动作」开源实现调研

> 调研日期：2026-10-19 / 2026-10-20
> 调研人：调研子智能体（research）
> 方法：`web_search` + `web_fetch` + **直接 `git clone` 拉仓库读源码**（不是只读 README）
> 本地克隆副本位置：**`DSH-work/repos/`** —— 全部被核查的仓库都已 clone 到这里，可直接翻源码复核：
> `anthropic-quickstarts` / `anthropic-sdk-python` / `cua` / `openclaw` / `sspro`（ScreenSpot-Pro）/ `zoomclick` / `gui_spotlight` / `ccf_gh`（CCF）/ `ui_zoomer` / `regionfocus` / `iter_narrowing` / `bami` / `ReGround` / `ufo` / `midscene` / `skyvern` / `browser-use` / `magentic-ui` / `autoglm` / `opencua` / `agent-s` / `ui-tars` / `ui-tars-desktop` / `uground` / `showui` / `jedi` / `tis`

---

## 0. 速览（TL;DR）

| 问题 | 结论 |
|---|---|
| 有没有现成开源实现？ | **有，而且分两派**。(1) Anthropic 官方 quickstart 有原生分辨率重裁的实现，但**不含坐标映射回全局**；(2) 学术界 + trycua 有**含坐标映射**的完整实现（ScreenSpot-Pro `models/methods/`、ZoomClick、GUI-Spotlight、CCF、Cua Driver） |
| Anthropic 的 zoom 是坐标坐标系吗？ | **不是。** 官方原文："the zoom image itself is never a coordinate frame"。zoom 只是"读图放大镜"，模型后续仍按全屏坐标输出 |
| Anthropic 是逆向还是原创？ | **判定 (b) 为主**：产品先于开源参考实现，官方从未引用任何论文/开源项目；但**存在 2024-11 ~ 2025-10 的公开前例**（Iterative Narrowing / RegionFocus / CCF / Ferret-UI Lite / GUI-Spotlight），所以严格说是 "(b) 独立原创 + 独立收敛，且紧跟公开先例"，不是 (a) |
| 框架里谁真有 zoom？ | 12 个框架里只有 **3 个**有真正的 zoom 动作：**trycua Cua Driver**、**OpenClaw**（转调 trycua）、**Anthropic quickstarts**。**midscene** 有内部 DeepLocate（裁同一帧 + 2× 上采样 + `x/scale+offset` 回映射）。**browser-use** 的 zoom 只存在于它的 hosted-model driver 里。**UI-TARS / skyvern / Magentic-UI / OpenCUA / Open-AutoGLM / UFO 全部没有** |
| UFO²/UFO³ 有 crop/zoom 吗？ | **没有。** 全仓库 `grep -i "zoom\|crop"` 只命中 16 处，全部是"把 UIA 元素图标裁出来做 CLIP 匹配"，不是屏幕区域放大。精定位靠 **UIA 元素自身的 bounding rect** + SoM 标注图 + LLM 选控件 ID |
| 我们最少要自己写哪几段？ | **4 段**：① 原图重裁管线 ② crop↔全局双向坐标映射 ③ 多尺度/终止/门控策略 ④ 评测适配。裁剪本身有 ImageMagick/PIL 现成代码可抄 |

---

## A. 现成开源实现逐个盘点

### A1. Anthropic 官方（重点）

#### A1.1 `anthropics/claude-quickstarts` — computer-use-demo

仓库：<https://github.com/anthropics/claude-quickstarts>（目录 `computer-use-demo/`）

**关键文件**：`computer-use-demo/computer_use_demo/tools/computer.py`

| 位置 | 内容 |
|---|---|
| L46 | `Action_20251124 = Action_20250124 \| Literal["zoom"]` |
| L428-433 | `class ComputerTool20251124(ComputerTool20250124)`，`options` 里返回 `{**super().options, "enable_zoom": True}` |
| L435-473 | `__call__` 里 `action == "zoom"` 分支：校验 region 是 4 个非负 int、且 `x0<x1 and y0<y1`，否则 `ToolError` |
| **L475-509** | **`async def zoom(self, region)` —— 核心实现** |
| L244-250 | `screenshot_size()`：模型看到的截图尺寸（= 坐标空间） |
| L279-302 | `scale_coordinates(source, x, y)`：API 空间 ↔ 屏幕空间 |
| L257-265 | `_capture(path)`：**物理分辨率**截图（gnome-screenshot / scrot） |
| `tests/tools/computer_test.py` L276-302 | 两个关键回归测试 |

**L475-509 的实际代码（已逐行读）**：

```python
async def zoom(self, region: tuple[int, int, int, int]) -> ToolResult:
    """Return a magnified capture of `region`, given in screenshot pixels.

    The region is mapped onto a physical-resolution capture (so the crop
    carries more detail than the same area of a scaled-down screenshot) and
    the crop is then resized, aspect preserved, to fit within the
    screenshot frame: one axis fills it, the other is at most as large.
    The model keeps emitting coordinates in screenshot pixels afterwards;
    the zoom image itself is never a coordinate frame.
    """
    # scale_coordinates(API, ...) also bounds-checks against the display.
    x0, y0 = self.scale_coordinates(ScalingSource.API, region[0], region[1])
    x1, y1 = self.scale_coordinates(ScalingSource.API, region[2], region[3])
    frame_w, frame_h = self.screenshot_size()

    raw_path = await self._new_output_path("zoom_raw")
    zoom_path = await self._new_output_path("zoom")
    capture = await self._capture(raw_path)          # ← 物理分辨率重截
    if not raw_path.exists():
        raise ToolError(f"Failed to take screenshot for zoom: {capture.error}")
    try:
        # +repage drops the crop offset so the resize sees a plain image;
        # a bare WxH geometry resizes to fit within the frame, aspect kept.
        await run(
            f"convert {raw_path} -crop {x1 - x0}x{y1 - y0}+{x0}+{y0} +repage "
            f"-resize {frame_w}x{frame_h} {zoom_path}"
        )
        ...
        return ToolResult(base64_image=base64.b64encode(zoom_path.read_bytes()).decode())
    finally:
        raw_path.unlink(missing_ok=True); zoom_path.unlink(missing_ok=True)
```

**要点回答「zoom 到底怎么实现的」**：

1. **谁请求 region**：模型发出 `action="zoom", region=[x0,y0,x1,y1]`（工具调用）。
2. **region 在哪个空间**：**全屏截图空间**（screenshot pixel space），**不是**物理显示像素。官方 SDK 类型定义 `anthropic-sdk-python/src/anthropic/types/computer_zoom_input.py` 原文：
   ```
   """Take a screenshot of a rectangular region.

   Region coordinates are in the
   full-screenshot space (not physical display pixels). The crop is scaled up to
   fill the image budget so fine details become legible.
   """
   region: List[int]
   """(x0, y0, x1, y1): The region to capture."""
   ```
   第三方 Go SDK（charmbracelet/fantasy）也写明："Region: [x1, y1, x2, y2] top-left and bottom-right"，"Response: return a screenshot of the zoomed region at full resolution"。
3. **从哪裁**：**从原始物理分辨率截图裁**（`_capture()`），不是从降采样后的图裁。
   ⚠️ 官方第一版**踩过这个坑**（见 §B 时间线）：2025-11-24 的初版是从 `await self.screenshot()`（已降采样）裁的，坐标却按屏幕空间换算 → 只要发生缩放，裁剪就完全错位。2026-08-21 才修。
4. **放大倍数怎么定**：**不固定**。用 `-resize {frame_w}x{frame_h}`（ImageMagick 的"装进框内"语义，等比缩放，一轴填满、另一轴不超过），即"填满模型截图框"这个上限。crop 尺寸由 region 决定，region 由模型给。
5. **谁负责把坐标映射回全屏**：**没有人，也不该有人**。Anthropic 的设计里 zoom 图像**不是坐标系**，模型继续在全屏坐标系里输出坐标。官方 quickstart 的 tool schema 里明确写死：
   > `* zoom: return a cropped, higher-detail view of the screen region `region` = [x1, y1, x2, y2]. Use this to read small text or inspect fine detail. Coordinates in subsequent actions still refer to the full screenshot, not the zoom.`
   > —— `computer-use-best-practices/computer_use/tools/computer.py:190-193`

   ⚠️ **对我们的含义**：Anthropic 的 zoom **不能**直接满足"crop 内精定位 → 映射回物理像素"。它解决的是"读不清小字"，不是"点不准"。要做得对，必须学 ZoomClick / CCF / Cua Driver 那套。

#### A1.2 `anthropics/claude-quickstarts` — computer-use-best-practices（**PIL 版，跨平台，最干净**）

仓库目录：`computer-use-best-practices/`

**关键文件**：`computer-use-best-practices/computer_use/tools/computer.py`

- L423-424：`if action == "zoom": return self._zoom(kwargs.get("region"))`
- **L428-456：`def _zoom(self, region)`**
- L255-268：`_scale_to_screen()` / `_scale_to_image()`（截图空间 ↔ 屏幕空间）
- L224-230：`__init__` 里 `self.sent_w, self.sent_h = target_image_size(self.screen_w, self.screen_h)`

**L428-456 实际代码**：

```python
def _zoom(self, region: list[int] | None) -> ToolResult:
    if not region or len(region) != 4:
        return ToolResult(error="zoom requires `region` = [x1, y1, x2, y2]")
    x1, y1, x2, y2 = region
    if x2 <= x1 or y2 <= y1:
        return ToolResult(error="zoom region must have x2 > x1 and y2 > y1")
    # Capture at full physical resolution (retina) so the crop carries more
    # detail than the same area did in the downscaled full-screen shot.
    full = pyautogui.screenshot()                      # ← 物理分辨率重截
    fx, fy = full.width / self.sent_w, full.height / self.sent_h   # ← 截图空间→物理空间
    box = (
        max(0, round(x1 * fx)),
        max(0, round(y1 * fy)),
        min(full.width, round(x2 * fx)),
        min(full.height, round(y2 * fy)),
    )
    crop = full.crop(box)                              # ← 从原图裁
    try:
        b64, (cw, ch) = resize_and_encode(crop, min_bytes=0)
    except ValueError as e:
        return ToolResult(error=str(e))
    return ToolResult(
        output=(
            f"zoom of ({x1},{y1})-({x2},{y2}) in {self.sent_w}x{self.sent_h} image, "
            f"shown at {cw}x{ch}. Subsequent coordinates still refer to the "
            f"full {self.sent_w}x{self.sent_h} screenshot, not this crop."
        ),
        base64_image=b64,
    )
```

**这段是 Anthropic 侧"截图空间 → 物理空间"换算的最干净写法**（`fx = full.width / sent_w`），可以直接抄。

#### A1.3 `image.py` / `target_image_size()` —— 和 zoom 的关系

**关键文件**：`computer-use-best-practices/computer_use/image.py`（102 行）

```python
"""
Image sizing and encoding.

The API's vision encoder tiles images into 28x28 patches and caps both the
long edge (1568 px) and the total tile count (1568). If we send an image that
violates either, the server resizes it *again* before the model sees it, and
then the model emits click coordinates in a space we never observed, producing
systematic click drift (~14% on a 16:10 MacBook screen).

So: pre-resize every screenshot to the largest dimensions that satisfy *both*
constraints, and remember those dimensions to scale model coordinates back to
screen space.

`target_image_size` is a direct port of the API's reference algorithm.
"""
```

- L38-75：`target_image_size(width, height, *, px_per_token, max_edge_px, max_tokens)` → 保持长宽比、在"长边 ≤ max_edge_px"和"tile 数 ≤ max_tokens"双约束下的最大尺寸（二分搜索）。
- L78-102：`resize_and_encode(img, *, min_bytes)` → 缩到目标尺寸 + JPEG 编码 + 返回 `(base64, (sent_w, sent_h))`。

**它和 zoom 的关系（这是很多人搞混的点）**：

| | `target_image_size` / `resize_and_encode` | `zoom` |
|---|---|---|
| 作用 | **把图压小**到模型能吃下的尺寸 | **把图放大**（裁小块再放大） |
| 方向 | 全屏 → 小图 | 大图 → 小区域 → 放大 |
| 解决的问题 | 省 token + 避免服务端二次缩放导致的坐标漂移 | 看清小字/小图标 |
| 坐标影响 | **有**：模型在缩后图里出坐标，必须按 `sent_w/sent_h` 反算回物理像素 | **无**：Anthropic 明确 zoom 不是坐标系 |
| 关系 | `target_image_size` 决定了"模型看到的截图坐标系"；`zoom` 的 region **就在这个坐标系里** | |

一句话：**`image.py` 管"全局缩放 + 坐标反算"，`zoom` 管"局部放大"。两者叠加时，zoom 的 region 先按 `target_image_size` 定的截图坐标系解释，再按 `scale_coordinates` 换到物理像素。**

#### A1.4 `anthropics/anthropic-sdk-python` — 类型定义

| 文件 | 内容 |
|---|---|
| `src/anthropic/types/computer_zoom_input.py` | `region: List[int]`，(x0,y0,x1,y1)，注释写明"full-screenshot space (not physical display pixels)" |
| `src/anthropic/types/computer_zoom_tool_use_block.py` | `name: Literal["zoom"]`、`toolset_name: Literal["computer"]`、`input: ComputerZoomInput` |
| `src/anthropic/types/computer_zoom_config_param.py` | `enabled` / `defer_loading`，注释："A member whose enabled resolves false is withheld from the served schema." |
| `src/anthropic/types/computer_toolset_20260801_param.py` | "Every member is enabled by default, zoom included... zoom is controlled via `configs.zoom.enabled`." |
| `src/anthropic/types/computer_member_name.py` | 成员名列表含 `"zoom"` |
| `src/anthropic/types/beta/beta_tool_computer_use_20251124_param.py` L41-42 | `enable_zoom: bool` — "Whether to enable an action to take a zoomed-in screenshot of the screen." |
| `src/anthropic/types/browser_zoom_input.py` | 浏览器版：`region` = "[x0, y0, x1, y2] in viewport pixels"，`tab_id` 可选 |

#### A1.5 `anthropics/claude-quickstarts` — browser-use-demo（浏览器版）

`browser-use-demo/browser_use_demo/tools/browser.py` L374-395 `_zoom_screenshot(x, y, width, height)`：用 Playwright 的 `page.screenshot(clip={...})`，region 格式是 **(x, y, width, height)**（注意和 computer 的 (x0,y0,x1,y1) 不同），天然就是**原始设备像素**裁剪，无需缩放换算。

#### A1.6 `computer-toolset` quickstart

新 quickstart（`computer-toolset/python/vnc_computer.py`）：走 VNC 读屏，**不缩放**，超过 1920×1200 直接拒绝。**它没有实现 zoom**（README L87 明确列出 "every other tool: `zoom`, ..." 为未实现项）。

---

### A2. 学术界 / 开源模型（有坐标映射的真正完整实现）

#### A2.0 ★★★★★★ **ScreenSpot-Pro 官方评测仓库里的 `models/methods/`** —— **本次调研最有用的一处发现**

仓库：<https://github.com/likaixin2000/ScreenSpot-Pro-GUI-Grounding>（首 commit 2025-01-03）
**这个仓库不只是一个评测脚本，它把 4 种 coarse-to-fine 精定位方法都实现成了可运行的 model wrapper**，可以直接当我们的实现底座：

| 文件 | 类 / 函数 | 机制 | 坐标映射回全局 |
|---|---|---|---|
| `models/methods/reground.py` | `ReGroundMethod.visual_search`（L146-153） | **整屏 1 遍 → `crop_to_center(image, real_bbox, crop_size=[1024,1024])` → 在 1024×1024 crop 上第 2 遍** | `view_bbox_to_real_bbox(view_bbox, viewport=cropped_box)`（L18-36） |
| `models/methods/iterative_narrowing.py` | `IterativeNarrowingMethod.visual_search`（L169-203） | 最多 `max_search_depth=3` 轮；每轮取上次预测中心 **±0.25 的归一化窗口**（即线性 50%）；`crop_with_padding(image, target_area, padding=0)` | `viewport = view_bbox_to_real_bbox(target_area, viewport=viewport)`（L201） |
| `models/methods/iterative_focusing.py` | `IterativeFocusingMethod.visual_search`（L140-181） | 最多 3 轮；每轮裁**上次 bbox 中心所在的四象限**（`areas = {"top_left": (0,0,0.5,0.5), "top_right": (0.5,0,1,0.5), "bottom_left": (0,0.5,0.5,1), "bottom_right": (0.5,0.5,1,1)}`） | 同上 viewport 累积 |
| `models/methods/screenseeker.py` | `ScreenSeekeRMethod`（复现 ScreenSeekeR 的"提议 patch → 打分 → 膨胀 → 递归"搜索） | L456-465 `min_crop_size = [1280, 768]`；`smart_crop()` L539-590（长宽比过极端时沿长边切 n_crops 份）；`auto_dilate_patch()` L824+（把 patch 膨胀到最小尺寸并按 16:9 切分）；`visual_search()` L936-1048 **递归**（depth+1），终止条件是图已小于 `min_crop_size` | 同一套 `view_bbox_to_real_bbox()`（L349）+ `crop_with_padding()`（L370） |
| `models/zoom_consistency_router.py` | `ZoomConsistencyRouter._predict_2step`（L180-203） | 2 步 zoom + **双模型一致性路由**；`CROP_RATIO=0.5`；crop 后 `.resize((orig_w, orig_h), LANCZOS)` 再喂模型；用两个模型在 crop 上的预测与 crop 中心的距离差决定用谁的 | `abs2_x = x1 + (step2[0]/1000.0) * (x2-x1)`（L200-202） |

**★ `reground.py` 就是任务里说的"ReGround"的可运行参考实现**（arXiv 2511.21631 是 Qwen3-VL 报告；ReGround 论文是 arXiv 2608.04385，但代码在这个评测仓库里）。

**`reground.py` 的完整关键代码（已逐行读）**：

```python
# L18-36  viewport 内的归一化框 → 全图归一化框
def view_bbox_to_real_bbox(view_bbox, viewport):
    view_x1, view_y1, view_x2, view_y2 = view_bbox
    x1, y1, x2, y2 = viewport
    w = x2 - x1; h = y2 - y1
    return [x1 + w*view_x1, y1 + h*view_y1, x1 + w*view_x2, y1 + h*view_y2]

# L39-93  以 bbox 中心裁固定尺寸窗口，撞边时向反方向扩张以保住尺寸
def crop_to_center(img, bbox, crop_size):
    img_width, img_height = img.size
    crop_width, crop_height = crop_size
    x1, y1, x2, y2 = bbox[0]*img_width, bbox[1]*img_height, bbox[2]*img_width, bbox[3]*img_height
    cx, cy = (x1+x2)/2, (y1+y2)/2
    new_x1_pixel = max(0, cx - crop_width/2);  new_y1_pixel = max(0, cy - crop_height/2)
    new_x2_pixel = min(img_width, cx + crop_width/2); new_y2_pixel = min(img_height, cy + crop_height/2)
    if new_x1_pixel == 0: new_x2_pixel = min(img_width, new_x1_pixel + crop_width)   # 向右扩
    if new_y1_pixel == 0: new_y2_pixel = min(img_height, new_y1_pixel + crop_height) # 向下扩
    if new_x2_pixel == img_width: new_x1_pixel = max(0, new_x2_pixel - crop_width)  # 向左扩
    if new_y2_pixel == img_height: new_y1_pixel = max(0, new_y2_pixel - crop_height) # 向上扩
    ...
    return cropped_box, focused_image      # cropped_box 是 crop 在原图中的归一化框

# L146-153  两步法
def visual_search(self, instruction, image):
    view_bbox, real_bbox = self.ground_with_grounder(instruction, image, viewport=(0, 0, 1, 1))
    if not real_bbox: return False, None
    cropped_box, focused_image = crop_to_center(image, real_bbox, crop_size=[1024, 1024])
    view_bbox, real_bbox = self.ground_with_grounder(instruction, focused_image, viewport=cropped_box)
    return True, real_bbox
```

**为什么它对我们最有用**：
- 它的形状 = **我们要的形状**（整屏粗定位 → 固定尺寸 crop → crop 内精定位 → 映射回全局），只是把 1024 换成 512 即可
- 它的裁剪**从原图裁**（`image` 参数一路传下去，从不降采样）；多轮版本里 `crop_with_padding(image, target_area, padding=0)` 也是在原分辨率内容上继续裁，**像素密度始终不变**
- 它**已经接好了官方 eval 脚本**（`eval_screenspot_pro.py` 的 `model_factory.build_model(args)` 只需一个 `ground_only_positive(instruction, image) -> {"point": [nx, ny], "bbox": [...], "raw_response": ...}`）
- 它的坐标用**归一化**，和 ScreenSpot-Pro 的评测口径天然一致
- 同目录的 `iterative_narrowing.py` / `iterative_focusing.py` 给出**多轮**版本，`zoom_consistency_router.py` 给出**双模型一致性路由**版本（自报 ScreenSpot-Pro 80.9%）

**⚠️ 一个设计差异要注意**：`iterative_focusing.py` 裁的是**固定象限**（目标可能在象限外），而 `reground.py` / CCF 裁的是**围绕预测中心的固定窗口**。后者明显更稳 —— **我们应当选"围绕预测中心"的方案，不要抄象限方案**。

#### A2.1 ★★★★★ ZoomClick — `Princeton-AI2-Lab/ZoomClick`

- 论文：*Zoom in, Click out: Unlocking and Evaluating the Potential of Zooming for GUI Grounding*，arXiv **2512.05941**（2025-12-05）
- 仓库：<https://github.com/Princeton-AI2-Lab/ZoomClick>（**首 commit 2025-12-04**）
- 核心文件：`grounding/eval_sspro_zoomclick.py`（732 行，**单文件包含全部逻辑**）

**实现要点**：
- **有可运行代码** ✅
- **crop 比例**：`--in_ratio` 默认 **0.5**（每轮缩一半），`--in_min_crop` 默认 **768**（最小裁剪边长，保上下文）
- **从哪裁**：**从原始分辨率图裁**（`curr_img` 初始就是原图，每轮在上一次的 crop 结果上再 crop，所以内容像素一直是原图分辨率）
- **终止条件**：`--in_depth` 默认 **3** 轮；且若 `target_w == curr_W and target_h == curr_H`（已无法再缩）提前停止；模型返回 None 也停止
- **边界策略**：`--center_mode` ∈ {`shift`(默认), `clip`, `shrink`}，`shift` = 保持窗口大小、贴边平移

**坐标映射回全局（这是核心可抄部分）**：

```python
# L47-53  crop 内归一化坐标 → 原图归一化坐标
def view_point_to_real_point(view_pt, viewport):
    vx1, vy1, vx2, vy2 = viewport
    vw, vh = max(1e-8, vx2 - vx1), max(1e-8, vy2 - vy1)
    px, py = view_pt
    rx = vx1 + vw * px
    ry = vy1 + vh * py
    return (_clamp(rx), _clamp(ry))

# L55-66  crop 的归一化框 → 原图的归一化框（下一轮 viewport）
def view_box_to_real_box(view_box, viewport):
    vx1, vy1, vx2, vy2 = viewport
    vw, vh = max(1e-8, vx2 - vx1), max(1e-8, vy2 - vy1)
    x1, y1, x2, y2 = view_box
    rx1 = vx1 + vw * x1;  ry1 = vy1 + vh * y1
    rx2 = vx1 + vw * x2;  ry2 = vy1 + vh * y2
    ...
```

主循环 `grounding/eval_sspro_zoomclick.py` L410-490：

```python
orig_W, orig_H = image.size
viewport = (0.0, 0.0, 1.0, 1.0)      # 当前 crop 在原图中的归一化框
curr_img = image                      # 当前视图（原始分辨率内容）
last_pt_real = None

for it in range(self.max_search_depth):
    ...
    pt_view, _ = self.backend.infer_norm_point(instruction, curr_img)   # crop 内归一化坐标
    pt_real = view_point_to_real_point(pt_view, viewport)               # ← 映射回原图归一化
    last_pt_real = pt_real
    if it == self.max_search_depth - 1: break

    curr_W, curr_H = curr_img.size
    target_w = max(int(round(curr_W * self.shrink_ratio)), self.min_crop_size)
    target_h = max(int(round(curr_H * self.shrink_ratio)), self.min_crop_size)
    target_w = min(target_w, curr_W); target_h = min(target_h, curr_H)
    if target_w == curr_W and target_h == curr_H: break     # 无法再缩，提前停

    center_px = _norm_to_pixel_point(pt_view, (curr_W, curr_H))
    curr_img, crop_px = crop_center_px(curr_img, center_px, target_w, target_h, center_mode=self.center_mode)
    norm_box = _px_box_to_norm_box(crop_px, (curr_W, curr_H))
    viewport = view_box_to_real_box(norm_box, viewport)     # ← 累积更新 viewport

click_px = _norm_to_pixel_point(last_pt_real, (orig_W, orig_H))   # ← 最终落到原图像素
return {"result": "positive", "point": [click_px[0], click_px[1]], ...}
```

**其它可复用件**：
- `crop_center_px(image, center_px, target_w_px, target_h_px, center_mode)` L68-115：三种边界策略的完整实现
- `generate_patches(patch_size)` L117：2×2 / 3×3 / 4×4 patch 网格（Pre-zoom 阶段用）
- `prezoom_first_iteration_distance(...)` L260：先切 patch 跑模型、量像素距离决定要不要 zoom
- `GUIZoom-Bench/build_guizoom.py`、`GUIZoom-Bench/collect_guizoom_accuracy.py`：从 ScreenSpot-Pro 派生一个"zoom 能力"benchmark

#### A2.2 ★★★★☆ GUI-Spotlight — `bin123apple/GUI_Spotlight`

- 论文：*GUI-Spotlight: Adaptive Iterative Focus Refinement for Enhanced GUI Visual Grounding*，arXiv **2510.04039**（2025-10-05），ICML 2026
- 仓库：<https://github.com/bin123apple/GUI_Spotlight>（**首 commit 2025-08-04** —— 比 Anthropic 的 zoom 早 3.5 个月）
- 有可运行代码 ✅（含 `screenspot_pro_evaluation.py` / `osworld_g_evaluation.py` / `uivision_evaluation.py`）

**实现要点**：模型自己输出工具调用 `crop(Image_0,(x1,y1),(x2,y2))`；工具有 4 个：`crop` / `extract`（切 1/4 象限）/ `find_color`（10×10 滑窗找颜色，返回 200×200 窗口）/ `scan`（未实现）。

**关键文件**：
- `spotlight/tools.py` L6-88：`crop(img, top_left, bottom_right)` → 返回 `(png_bytes, message, top_left)`；**第三返回值就是 offset**，注释写明 "For calculating the coordinates relative to the original image"。含完整边界校验（x1<0 / x2>width / x2<=x1 / 最小边长 28px / 宽高恰为 3 时微调）
- `spotlight/tools.py` L108-166：`extract()` 切 ½ 宽 × ½ 高 的象限，返回 `(x0, y0)` offset
- `spotlight/tools_envs/tool_env.py` L150-203：`call_crop()` 解析工具调用字符串 `Image_0,(10,20),(110,100)` 并调用 `crop()`
- `spotlight/tools_envs/tool_env.py` L383-398：**offset 累积**
  ```python
  crop_bytes, info_message, off_set, id_num = self.call_tool(category, tool_cmd, images)
  # Calculate and add offset
  dx = off_set[0]; dy = off_set[1]
  x_off_set = images_offset[id_num][0] + dx     # ← 嵌套 crop 的偏移累加
  y_off_set = images_offset[id_num][1] + dy
  off_set = (x_off_set, y_off_set)
  images_offset.append(off_set)
  ```
- `screenspot_pro_evaluation.py` L302-354：**最终坐标映射 + point-in-bbox 评测**
  ```python
  m = pattern.search(raw)   # <answer>Image_0,(x, y)</answer>
  if m:
      id_num, x, y = m.groups()
      id_num = int(id_num); x, y = int(x), int(y)
      x = x + images_offset[id_num][0]     # ← crop 内坐标 + 偏移 = 原图坐标
      y = y + images_offset[id_num][1]
  x1, y1, x2, y2 = box
  reward = 1.0 if (x1 <= x <= x2 and y1 <= y <= y2) else 0.0
  ```
- `spotlight/reward/tool_rubric.py` L226+：`correct_crop_func`（crop 框是否覆盖 GT 框）

**注意**：这里的 offset 是**纯加法**（因为 crop 不改变像素尺度），比 ZoomClick 的归一化 viewport 简单，但只在"crop 不缩放"的前提下成立。

#### A2.3 ★★★★☆ CCF（Cursor-Centric Focusing）— `LufeMC/gui-g2-3b-ccf`（**GitHub 有完整仓库**）

- **GitHub 仓库**：<https://github.com/LufeMC/gui-g2-3b-ccf>（**首 commit 2026-04-20 "Initial release: GUI-G2-3B + CCF inference wrapper"**）
  - ⚠️ 比 HF 镜像好用得多：HF 上的 `luisf-mc/gui-g2-3b-ccf` 只有模型权重；GitHub 这个仓库有 **220 个单测**、`src/eval.py --ccf --ccf-type-gate` 评测脚本、部署脚本，以及一份**诚实的 9 阶段复盘** `benchmarks/results.md`
  - ⚠️ huggingface.co 在本机不可达，已用 `https://hf-mirror.com/luisf-mc/gui-g2-3b-ccf/raw/main/cursor_ccf.py` 拿到源码；GitHub 仓库的 `src/cursor_ccf.py` 是同一份（165 行）
- 论文：*Learning GUI Grounding with Spatial Reasoning from ...*（GUI-Cursor），arXiv **2509.21552**（2025-09-25），ICML 2026 / OpenReview `kNAQMZf53k`
- 模型卡：<https://huggingface.co/luisf-mc/gui-g2-3b-ccf>
- **代码文件**：`src/cursor_ccf.py`（纯 Python + PIL，无 torch 依赖，**最干净可直接抄**）

**实现要点**：
- **有可运行代码** ✅（`make_ccf_eval_adapter` 直接挂到 `src/eval.py --ccf`）
- **crop 比例**：`zoom_factor` 默认 **2.0**（裁 `img_w/2 × img_h/2`），`min_crop_side` 默认 **112**（= 4×28，Qwen2.5-VL 最小 patch 网格）
- **从哪裁**：**从原图裁**（`image.crop(crop_window)`）
- **终止条件**：**只做一次精修**。文件头注释写得很清楚：
  > "Single refinement pass. Iterative narrowing (> 1 refinement) gives diminishing returns and compounds coordinate-mapping errors."
  > "Fall back to coarse if the refined pass fails to parse -- we never return None if the coarse pass succeeded."
  > "Crop shifts near the boundary instead of shrinking, so the model always sees a fixed-size region at its native resolution budget."

**坐标映射回全局（可直接抄）**：

```python
# 计算裁剪窗口：围绕粗定位，贴边时平移而非缩小
def compute_crop_window(center, image_size, zoom_factor=DEFAULT_ZOOM_FACTOR,
                        min_crop_side=DEFAULT_MIN_CROP_SIDE) -> Bbox:
    img_w, img_h = image_size
    target_w = max(min_crop_side, int(img_w / zoom_factor))
    target_h = max(min_crop_side, int(img_h / zoom_factor))
    target_w = min(target_w, img_w); target_h = min(target_h, img_h)
    cx = int(round(center[0])); cy = int(round(center[1]))
    left = cx - target_w // 2; top = cy - target_h // 2
    if left < 0: left = 0
    elif left + target_w > img_w: left = img_w - target_w
    if top < 0: top = 0
    elif top + target_h > img_h: top = img_h - target_h
    return left, top, left + target_w, top + target_h

# crop 内像素 → 原图像素
def map_crop_to_orig(x_on_crop: float, y_on_crop: float, crop_window: Bbox) -> Point:
    left, top, _right, _bottom = crop_window
    return left + float(x_on_crop), top + float(y_on_crop)

# 粗定位阶段降采样 → 坐标放大回原图
def _maybe_downsize_for_coarse(image, max_pixels):
    ...
    scale_down = math.sqrt(max_pixels / px)
    new_w = max(1, int(round(w * scale_down))); new_h = max(1, int(round(h * scale_down)))
    downsized = image.resize((new_w, new_h), Image.BILINEAR)
    scale_up = w / new_w          # 降采样图坐标 × scale_up → 原图坐标
    return downsized, scale_up
```

**额外的"要不要 zoom"启发式**（很有参考价值）：
- `_should_skip_ccf()`：图像像素数 < `min_pixels_for_ccf`（默认 **200,000**，≈450×450）就不 zoom —— "crop won't help"
- `classify_instruction()`：按 ICON_KEYWORDS / TEXT_KEYWORDS 判断目标是"图标"还是"文字"；目标是**文字**时**跳过精修**（实测文字 -2.3pp，图标 +2.2pp）

#### A2.3b ⭐⭐⭐ CCF 仓库里的**实测数据与踩坑**（对我们最有指导价值的部分）

`benchmarks/results.md` 里 CCF 的 Phase 4 复盘（ScreenSpot-v2，n=1272，greedy，H200）：

| 切分 | Baseline | +CCF | Δ | 结论 |
|---|---|---|---|---|
| Overall | 89.2% | 88.9% | **-0.3pp** | 整体持平 |
| **Icon** | 80.5% | **82.7%** | **+2.2pp** | ✅ 小图标受益明显 |
| **Text** | 96.0% | 93.7% | **-2.3pp** | ⚠️ 文字受损 |
| **Web** | 84.2% | **88.1%** | **+3.9pp** | ✅ 密集小图标页面受益最大 |
| Desktop | 91.3% | 92.8% | +1.5pp | ✅ |
| **Mobile** | 88.0% | 82.8% | **-5.2pp** | ⚠️ 最大回退 |

**失败原因诊断（作者原话，值得逐条抄进我们的设计文档）**：
- **Mobile -5.2pp**："Mobile screenshots are tablets (2360x1640) and modern phones (1170x2532) -- targets are usually large enough that the coarse pass already nails them, and the refined pass on the crop introduces small drift errors that miss the bbox by a few pixels."
- **Text -2.3pp**："text bounding boxes are wide (e.g. a button label spans 200px). The refined pass gets a tighter crop and sometimes predicts a point near the *visual center of the word* rather than the original bbox center, drifting outside the bbox by a few px. **Confirmed by spot-checking misses: many are within 5-10px of the original (correct) coarse prediction.**"

**作者给出的 3 条改进（我们应当全部采纳）**：
1. **Type-aware CCF**：先分类指令（text vs icon），只对 icon 目标做精修
2. **收紧 `min_pixels_for_ccf` 门限**：从 200K 提到 **~1M**，让 mobile 整张跳过精修
3. **减小 `zoom_factor`**：从 2.0 降到 **1.5**，crop 更宽 → 更不容易把目标裁出去、减少文字漂移

**另一个可直接抄的工程优化（性能）**：
> "Coarse downsize knob (`--coarse-max-pixels`): the GUI-Cursor paper's actual implementation runs coarse at low res. We added this after profiling showed the coarse pass on 1920x1080 web pages took **10.6s** with flash_attn vs **4.3s** on 960x540. Cut wall time roughly in half on web/desktop without measurable accuracy impact."

**⚠️ 对我们的直接警示**：**"crop 精定位"不是无脑增益**。在这个受控评测里它让整体分数**略微下降**（-0.3pp），只在 icon / web / desktop 这些"目标本来就小"的切分上赚。**我们的实现必须带上"门控 + 失败回退"，否则大概率做出一个"平均更差"的精定位层。**

#### A2.4 ✗ BAMI — `Neur-IO/BAMI`（**没有可运行代码**）

- 论文：*BAMI: Training-Free Bias Mitigation in GUI Grounding*，arXiv **2605.06664**（2026-05-07），CVPR 2026
- 仓库：<https://github.com/Neur-IO/BAMI>（首 commit **2025-11-21**）
- **代码状态：仓库里只有 `README.md`，没有任何 `.py`**。README 原文：
  > "**[Coming Soon]** The inference code and evaluation scripts will be released soon. Stay tuned!"
  > "The code for BAMI is currently being organized and will be open-sourced shortly."
- **方法（论文 §4.1）**：Coarse-to-Fine Focus —— 先用 grounding 模型出一个粗坐标 `(x_t, y_t)`，然后 **"crop the original image to a scale of λ<1"**，把 crop 再喂回模型得 `(x_{t+1}, y_{t+1})`，可迭代 N 次。算法 1 的第 9 行：`I^{t+1} = CROP(I^t, b̃^t)`。
- **消融**：有 "Impact of Crop Ratio and Iteration Number" 一节，说明 λ 和 N 是两个关键超参。
- 相关工作里提到 **ScreenSeekeR**、**R-VLM** 也是"先区域定位再局部精修"的两阶段流水线。**全文没有引用 Anthropic 的 computer use zoom。**

#### A2.5 ⚠️ 任务里给的三个条目需要更正

| 任务给的 | 实际情况 |
|---|---|
| **ReGround（arXiv 2511.21631v2）** | ❌ **arXiv 2511.21631v2 是《Qwen3-VL Technical Report》**（Qwen 团队，2025-11-26/27），不是 ReGround。名字对得上的是 `sespoir/ReGround`（arXiv **2608.04385**，ACM MM 2026，"Restoring Visual Grounding in Multi-Step Reasoning"），机制是 **SFT+GRPO 训练模型自己判断何时"重看一眼"（`<reground>` token 触发一次额外视觉前传）**，**全仓 `.py` 里 0 个 zoom/crop 命中**（已 clone 核实）。另有 `re-ground.github.io`（GLIGEN 图像生成方向），与 GUI 无关。**真正的"coarse-to-fine GUI grounding + 开源代码"是 GUI-C²**（arXiv 2605.30884，`github.com/z1oong/GUI-C2`）**以及 ScreenSpot-Pro 的 `models/methods/reground.py`**（见 A2.0） |
| **CCF Cursor-Centric Focusing** | ✅ 存在，见 A2.3。论文是 arXiv 2509.21552（*Learning GUI Grounding with Spatial Reasoning from...*），**比 Anthropic 的 zoom 早 2 个月** |
| **Simona0212/thinking-in-space** | ⚠️ `thinking-in-space` 实际是 <https://github.com/vision-x-nyu/thinking-in-space> = **VSI-Bench**（*Thinking in Space: How MLLMs See, Remember and Recall Spaces*, arXiv 2412.14171），**是空间智能 benchmark，不是 GUI grounding 的 crop/zoom**。`Simona0212`（Zimeng Huang）的 "THEORY OF SPACE" 是另一个 benchmark |

#### A2.6 其它**已核实有可运行代码**的学术实现

| 工作 | 仓库 | 实现要点 | 坐标映射回全局 |
|---|---|---|---|
| **UI-Zoomer**（不确定性驱动自适应 zoom-in） | <https://github.com/ZJU-REAL/UI-Zoomer>（首 commit **2026-03-19**），代码 `UI-Zoomer/uizoomer.py`（496 行） | `process_single_image` L272-370 三步：① **全局采样**——把原图 resize 到 `smart_resize_target(h, w, 2_000_000, 4_800_000)`（约 2.0M–4.8M 像素），以 temperature 0.9 采样 **k=8** 个候选框；② **打分**——`c_spatial = calculate_spatial_consistency(boxes)`（两两 IoU 均值，L139-150）+ `avg_conf`（exp(平均 token logprob)），`S = c_spatial + avg_conf`（L301-304）；③ **分支**——`S > gating_threshold(默认 1.5)` 就**不 zoom**，直接 IoU 投票；否则走 **Density-Gaussian crop**：`get_density_gaussian_crop_box` L153-227（保留离中位数最近的 75% 候选中心 → `sigma_total = sqrt(var_inter + mean(var_intra))` → `radius = sigma_scale(3.5) * sigma` → crop 边长 `max(2*radius, min_crop_size=512)`，正方形，贴边平移）。crop 从**原图**裁（L352 `raw_img.crop(...)`），再 resize 到 `smart_resize_target(ch, cw, 1_000_000, 4_000_000)` 喂模型 | L359-366：`gx1 = (lx1 + c_x1)/orig_w` → 取中心 |
| **RegionFocus**（*Visual Test-time Scaling for GUI Agent Grounding*，arXiv **2505.00684**） | <https://github.com/tiangeluo/RegionFocus>（首 commit **2025-05-01**，SS-Pro 代码 2025-07-03） | `models/qwen25vl_RegionFocus.py`：`calculate_crop_region` L414-499（crop = 全图的 `ratio_x × ratio_y`，默认 **0.5×0.5**；共试 4 种比例 `[[0.5,0.5],[0.3,0.3],[0.4,0.8],[0.8,0.4]]`，贴边平移）；`crop_and_upsample` L579-655（从**原图**裁 → LANCZOS 上采样回原视口尺寸，`zoom_x = viewport_width/w`、`zoom_y = viewport_height/h`，或保比例的 `zoom_factor = min(zoom_x, zoom_y)` L631）；**自判门控** `judge_inference` L501-577 直接问 VLM "is this pink star position correct?"，判 YES 就**不 zoom** 直接返回初始点（L1017-1021），否则跑 4 种 zoom 变体再 `next_action_regionfocus_aggregation` L1115 聚合 | L796-808：`x_orig = left + (rel_zoomed_x / zoom_x)`（越界则 clamp） |
| **Iterative Narrowing** | <https://github.com/ant-8/GUI-Grounding-via-Iterative-Narrowing>（首 commit **2024-11-14**，★ 最早） | `region_traverser.py::RegionTraverser.consume_coordinate` L25-75：下一区域 = 当前区域的一半（桌面 `width/2, height/2`）或 `width/1.2 × height/2`（移动，L31-36）；**crop 永远从 `self.original_image` 裁**（L75），所以全局像素坐标天然不变 | `_convert_coordinate_relative_to_region` L15-23：`scaled_x = self.x1 + (x/999)*region_width`（模型坐标是 /999 网格） |
| **DiMo-GUI**（第三方复现） | <https://github.com/amey-gupt/DiMo-GUI-Implementation>（2025-10-20，Jupyter Notebook；原论文 arXiv 2507.00008） | "dynamic zooming and modality decoupling" 的 notebook 复现 | 论文级复现，非作者代码 |
| **RegionFocus**（第三方复现） | <https://github.com/amey-gupt/RegionFocus-Implementation>（2025-10-28，notebook） | 同上 | 论文级复现 |
| **Attention-based GUI-Actor** | <https://github.com/Rashid0002/Attention-based-GUI-Actor>（2026-05-03） | `scripts/integrate_with_gui_actor.py`（GUI-Actor 注意力图之上的 zoom 精修模块）+ `src/eval/{eval_screenspot.py, metrics.py, eval_venusbench.py}` | ⚠️ **文件内容未读到**（raw.githubusercontent 拉取失败），坐标映射未核实 |
| **Ferret-UI Lite** | arXiv 2509.26539（2025-09-30） | "Visual Tool-Use Integration"：把 **zoom-in cropping** 作为视觉工具使用教给模型 | 论文级，未核实仓库 |
| **ScaleCUA** | ICLR 2026 | 迭代 crop-and-zoom；point / bbox / action grounding 三分类 | 论文级 |
| **GUI-AIMA** | ECCV 2026 | 两步 crop→zoom 自纠错 | 论文级 |
| **See, Point, Refine**（Microsoft） | arXiv 2604.13019 / `microsoft/precision-cua-bench` | 多轮 + 视觉反馈自纠偏 | ✅ 有 |
| **C3VG** / **SE-GUI** / **GUI-Actor** | `github.com/Dmmm1997/C3VG` / `YXB-NKU/SE-GUI` / `microsoft/GUI-Actor` | 一致性 / 自进化 RL / 无坐标 grounding | ✅ 均有（但非 crop 型） |
| **V\* / SEAL** | arXiv 2312.04558 | MLLM 的"裁剪+再问"，**最早的公开先例** | 论文级 |

#### A2.7 其它**只有论文、没有代码**的（不要指望能抄）

| 工作 | 出处 | 状态 |
|---|---|---|
| **BAMI** | `Neur-IO/BAMI`（2 commits，均 2025-11-21） | 仓库里**只有** `README.md` + `asset/bami-v2.pdf` + `asset/teaser.png` + `.gitignore`，**无任何 `.py`**。README 明说 "[Coming Soon] The inference code and evaluation scripts will be released soon." 方法是 Coarse-to-Fine Focus（按 λ 比例裁原图喂回模型）+ Candidate Selection + MPD 归因，报告 TianXi-Action-7B 在 ScreenSpot-Pro 上 51.9% → **57.8%**、UI-TARS-1.5-7B 40.8% → 51.9%、OS-Atlas-7B 18.9% → 41.6% |
| **InnerZoom**（arXiv 2606.30084） | <https://github.com/YenanLiu/InnerZoom>（2026-08-26 创建） | ⚠️ **仓库存在但只有 2 个文件**：`LICENSE` + `README.md`（396 字节）。README 原文："The code and pretrained models associated with this work are planned for public release. **Release is currently pending completion of the required internal approval process at Alibaba.**" ⇒ **无可运行代码** |
| **ReGround 论文（arXiv 2608.04385, ACM MM 2026）** | <https://github.com/sespoir/ReGround> | ✅ **仓库确实存在**（已 clone 核实），但 `grep -i "zoom\|crop"` 在全部 `.py` 里 **0 命中**。它是**训练型**方案（SFT + GRPO，`<reground>` token 触发一次额外视觉前传），文件是 `reground_agent_loop.py` / `reward.py` / `generate_sft.py` 等，**没有任何 crop 实现**。真正可运行的 "ReGround" 参考实现在 ScreenSpot-Pro 的 `models/methods/reground.py`（见 A2.0） |
| **GUI-Lens**（arXiv 2608.03270，"Coarse-to-Fine Cropping for GUI Grounding with Multimodal LLMs"，OCR+检测引导的 crop） | — | **未找到仓库** |
| **MHVF**（arXiv 2610.17792，Training-Free GUI Grounding via Verification） | — | **未找到仓库** |
| **AutoFocus**（arXiv 2605.02630，"Uncertainty-Aware Active Visual Search for GUI Grounding"，Shape-Aware Zooming） | — | **未找到仓库**（GitHub 上按 "zoom GUI grounding" 搜只有 5 个仓：ZoomClick / DiMo-GUI-Implementation / InnerZoom / Attention-based-GUI-Actor / RegionFocus-Implementation，没有 AutoFocus）。本轮**只从搜索引擎摘要见过，未直接 fetch arXiv 页面**，连标题本身都应视为未核实 |
| **ShowUI / OS-Atlas / AnyClick / ConClick / Seg2Click / Groma / Jedi** | — | 本轮**未 clone 成功**（网络不通），其"无 crop/zoom"是**基于方法知识 + 检索，不是读源码核实**。按方法性质：ShowUI / OS-Atlas / AnyClick / ConClick 都是**单次出点**的 grounding 模型；Seg2Click 走 **SAM 分割掩码**而非 crop；Groma 是通用开放集 grounding；Jedi 是数据集发布。**建议在网络正常时补 clone 复核** |
| **UGround** | `osunlp/UGround`（ICLR 2025 Oral） | ❌ **推理路径里没有 crop**。已 clone 核实：`grep -i "zoom\|crop"` 在全部 `.py` 里只有 3 处命中，全在 **`offline_evaluation/Multimodal-Mind2Web/make_blocks.py`**（把整屏切 tile **构造 Mind2Web 数据**），`grounding/uground_qwen2vl.py` 推理脚本里一处都没有。UGround 的"放大"能力是**烤进 RL 训练过的模型里**的，不是可读代码里的机制 |

---

### A3. 现成 agent 框架里的 zoom 实现

#### A3.1 ★★★★★ trycua / cua —— **Rust Cua Driver（本次找到的最完整实现）**

仓库：<https://github.com/trycua/cua>

**关键文件**：
- `libs/cua-driver/rust/crates/platform-macos/src/tools/zoom.rs` —— `impl Tool for ZoomTool::invoke`（L55-163）
- `libs/cua-driver/rust/crates/platform-windows/src/tools/impl_.rs` L9046-9071（Windows）
- `libs/cua-driver/rust/crates/platform-linux/src/tools/impl_.rs` L13619-13676（Linux）
- **`libs/cua-driver/rust/crates/cursor-overlay/src/capture_utils.rs` L38-93 —— `fn crop_png_to_jpeg`**
- **`libs/cua-driver/rust/crates/cua-driver-core/src/snapshot_store.rs` L58-73 —— `struct ZoomContext` + `zoom_to_window()`**
- `libs/cua-driver/rust/crates/platform-macos/src/tools/click.rs` L1022-1039 —— 消费端
- `libs/cua-driver/rust/crates/cua-driver/tests/protocol_media_test.rs` L173 —— `zoom_from_zoom_click_round_trip` 往返测试

**裁剪（macOS，L110-120）**：
```rust
let (x1, y1, x2, y2) = (
    x1 * screenshot.scale, y1 * screenshot.scale,   // ← 降采样空间 → 原生像素
    x2 * screenshot.scale, y2 * screenshot.scale,
);
let result = tokio::task::spawn_blocking(move || {
    let png_bytes = crate::capture::screenshot_window_bytes(window_id)?;   // ← 全新原生分辨率截图
    cursor_overlay::capture_utils::crop_png_to_jpeg(&png_bytes, x1, y1, x2, y2, 500)
}).await;
```

Windows 端的注释把关键点讲得很清楚（`platform-windows/src/tools/impl_.rs` L9046-9053）：
```rust
// Issue #1880: the caller's coordinates are in the (possibly downscaled)
// screenshot space of the last `get_window_state` call, but the crop below
// runs on a fresh native-resolution capture. Scale by the stored resize
// ratio so the crop lands on the intended region; the zoom context then
// holds native-pixel values, which is what `from_zoom` clicks expect.
```

**裁剪 + padding + 输出尺度**（`capture_utils.rs` L38-93）：
```rust
let pad_x = rw * 0.20; let pad_y = rh * 0.20;      // 四边各留 20% padding
cx1 = ((x1 - pad_x).max(0.0) as u32).min(img_w);  ...
let cropped = img.crop_imm(cx1, cy1, cw, ch);
if cw > max_width {                                // 输出宽度封顶 500px，Lanczos3
    let scale = max_width as f64 / cw as f64;
    cropped.resize_exact(max_width, new_h, FilterType::Lanczos3)
}
let scale_inv = cw as f64 / out_w as f64;            // 1.0 表示没缩放
CropResult { jpeg_bytes, out_w, out_h, origin_x: cx1 as f64, origin_y: cy1 as f64, scale_inv }
```
> `origin_x/origin_y` = **padding 后 crop 原点在全窗口像素空间（原生像素）的位置**；`scale_inv` = 原生 crop 宽 / 输出图宽。

**坐标映射回全局（`snapshot_store.rs` L58-73）**：
```rust
pub struct ZoomContext { pub screenshot: ScreenshotContext, pub origin_x: f64, pub origin_y: f64, pub scale_inv: f64 }
impl ZoomContext {
    pub fn zoom_to_window(&self, x: f64, y: f64) -> (f64, f64) {
        (self.origin_x + x * self.scale_inv, self.origin_y + y * self.scale_inv)
    }
}
```

**消费端（`platform-macos/src/tools/click.rs` L1022-1039）**：
```rust
} else if from_zoom {
    match super::zoom_context(&self.state, &args, pid, window_id) {
        Ok(ctx) => { let (wx, wy) = ctx.zoom_to_window(cx, cy); cx = wx; cy = wy; }
        Err(refusal) => return refusal,
    }
} else {
    let ratio = match super::screenshot_scale(&self.state, &args, pid, window_id) { ... };
    cx *= ratio; cy *= ratio;   // 降采样图空间 → 原生像素
}
```

**其它要点**：
- 裁剪输出 **≤ 500px 宽**（不是填满模型框）
- **20% padding** 保留上下文
- `ZoomContext` 绑定 (pid, session, window)；新快照或 session 结束会让它失效（返回 `"zoom_context_missing"` 拒绝）
- 往返测试在 `protocol_media_test.rs::zoom_from_zoom_click_round_trip`
- ⚠️ **trycua 的 Python SDK（`libs/cua/python/src/cua/`）没有 zoom**，zoom 只在 Rust Cua Driver 的 MCP 工具里

#### A3.2 ★★★★☆ OpenClaw —— computer tool v2 的 zoom（**是 trycua 的薄封装**）

仓库：<https://github.com/openclaw/openclaw>

| 文件 | 位置 | 内容 |
|---|---|---|
| `src/plugins/computer-use-contract.ts` | L33 | `"zoom",` 在 `COMPUTER_USE_V2_ACTION_NAMES` 里 |
| 同上 | L199-206 | wire schema：`actionObject(["zoom"], { windowRef, observationId, x1, y1, x2, y2 })` —— **窗口级**，且**强制带 `observationId`（防陈旧观测）** |
| `src/agents/tools/computer-tool-request.ts` | L151 | `zoom: ["windowRef", "observationId"],`（必填字符串参数） |
| 同上 | L283-292 | `case "zoom":` 校验四个数 `>= 0`，缺一个就抛错 |
| `src/agents/tools/computer-tool-schema.ts` | L166-169 | 模型可见 schema 的 `x1/y1/x2/y2` |
| `extensions/cua-computer/src/window-actions.ts` | L344-353 | **实现**：转发给 Cua Driver 的 zoom 工具，并把结果标成 `fromZoom: true` |
| `extensions/cua-computer/src/action-targets.ts` | L110-126 | `windowPointArgs`：跟随动作打上 `from_zoom: true` 标记 |
| `extensions/cua-computer/src/frame.ts` | L170-185 | `issueObservation(state, windowRef, {fromZoom})` |
| `apps/macos/Sources/OpenClaw/ComputerWindowActionExecutor.swift` | L156-160 | **原生 macOS 节点不支持 zoom**（抛 `unsupportedAction`） |

**结论**：OpenClaw **自己不写裁剪数学**，它把 zoom 转给 trycua Cua Driver，只在后续动作上打 `from_zoom: true` 标记，由驱动完成映射。**⇒ 抄 OpenClaw 等于抄 trycua，没有额外价值。**

#### A3.3 其它框架（逐仓 clone + grep 核查结论）

| 框架 | 仓库 | zoom / crop 结论 | 定位精度靠什么 |
|---|---|---|---|
| **midscene** | web-infra-dev/midscene | ⚠️ **有，但是内部机制**（DeepLocate），不是 agent 可调用的动作。**裁的是"已经截好的那一帧"，不是重截** | `packages/core/src/ai-model/workflows/grounding/search-area.ts` L93-122：`expandSearchArea()`（四边 +100px，再从中心放大到至少 400×400）→ `cropByRect()` → **`scaleImage(scaleRatio=2)`**；第 2 轮定位跑在 crop 上。映射：`search-area-mapping.ts` L19-25 `mapSearchAreaPointToOriginalPoint` = `[x/scale + offset.x, y/scale + offset.y]`。默认在 locate 失败后自动开启（`element-describer.ts` L206-218） |
| **browser-use** | browser-use/browser-use | ⚠️ **agent 本身没有 zoom 动作**，但它的 **hosted-model driver（OpenAI / Anthropic）里实现了 zoom** | OpenAI driver `integrations/openai/browser_use.py` L374-384：`zoom()` 先**重新 `page.screenshot()`**，再 `image.crop(region)`，**不做上采样、不做坐标重映射**（docstring: "Crop a screenshot without changing the pointer coordinate origin"）。Anthropic driver `integrations/anthropic/browser_use.py` L662-669：重截 → crop → `factor = min(2, 2000/max(cropped.size))` 上采样，**同样不重映射坐标** |
| **skyvern** | Skyvern-AI/skyvern | ❌ 无 zoom | DOM 优先：scraper 打 `data-skyvern-id`，LLM 回 `element_id`，执行时查 CSS 字典。CUA 路径走 Anthropic computer-use，`skyvern/utils/image_resizer.py` L53-61 `scale_coordinates()` 是**均匀缩放**（代码注释直接致谢 anthropic-quickstarts 的 computer.py） |
| **Magentic-UI** | microsoft/magentic-ui | ❌ 无 zoom | VLM 在**固定 1000×1000 display space** 出坐标，`agents/web_surfer/fara/_fara_qwen3.py` L134 `DISPLAY_SIZE = 1000`，L519-525 `proc_coords()` 均匀缩放到 viewport |
| **OpenCUA** | xlang-ai/OpenCUA | ❌ 运行时无 zoom；crop 只存在于 **CoT 训练数据生成**（`data/cot-generate/utils.py` L140-168 `crop_image_patch`） | 模型直接出点 |
| **Open-AutoGLM** | **zai-org/Open-AutoGLM**（注意：`zai-org/AutoGLM` 已不存在） | ❌ 无 zoom（grep `zoom\|crop` 在 `*.py` 里 0 命中） | 模型出**归一化 0–1000** 坐标，`phone_agent/actions/handler.py` L110-115 `_convert_relative_to_absolute()` 换算成像素；截图**不降采样**直接送 |
| **bytedance/UI-TARS** | bytedance/UI-TARS | ❌ 无（grep 0 命中） | 模型出点 |
| **UI-TARS-desktop** | bytedance/UI-TARS-desktop | ❌ **无 zoom 动作**。`finished` 是"任务完成"不是 zoom（`multimodal/gui-agent/shared/src/types/actions.ts` L259-267）；`NutJSOperator.ts` L50-69 的 `supportedActions()` 无 zoom | grounding 模型出**归一化 [0,1]** 点/框；`packages/ui-tars/sdk/src/utils.ts` L29-56 `parseBoxToScreenCoords` 换算到屏幕像素 |
| **Agent-S2** | **simular-ai/Agent-S**（`xlang-ai/Agent-S` 已迁移） | ⚠️ 无 zoom **动作**；但有 `get_zoomed_image()`（300×300 居中裁 + LANCZOS 4× 上采样），用途是 **Behavior Narrator 复盘"动作后那小块变了什么"**，**不是 grounding**。裁的是已截好的 after 帧，**不重截**，且**在原图上画红框**让模型自己定位 | `gui_agents/s3/bbon/behavior_narrator.py` L106-168；prompt 契约见 `gui_agents/s3/memory/procedural_memory.py` L306-319 |

#### A3.4 跨框架的**三种裁剪来源**（这是选型时最该看的维度）

| 裁剪来源 | 能否真的增加细节 | 代表实现 |
|---|---|---|
| **① 重截 + 原生分辨率裁**（re-capture at native resolution） | ✅ **唯一真正能增加细节的方式** | trycua Cua Driver（mac/win/linux 三平台，ScreenCaptureKit / DXGI / Wayland 全新截图）<br>Anthropic computer-use-demo（`_capture()` → gnome-screenshot/scrot） |
| **② 同一帧裁剪 + 上采样**（crop of same capture + upscale） | ❌ 不增加任何新像素，只是插值放大 | midscene DeepLocate（2×）<br>Agent-S Behavior Narrator（4×） |
| **③ 降采样图上裁**（crop of a downsampled screenshot） | ❌ 细节上限被降采样卡死 | browser-use 的 OpenAI driver<br>**Anthropic 官方 2025-11-24 初版 zoom（错版）** |

**⇒ 我们的实现必须走 ①。** Anthropic 官方初版踩的正是 ③ 的坑（2026-08-21 才改成 ①）。

#### A3.5 跨框架的**坐标映射**分野

| 做法 | 代表实现 | 说明 |
|---|---|---|
| **显式映射**（zoom 图 = 坐标系） | trycua Cua Driver：`ZoomContext { origin_x, origin_y, scale_inv }` + `zoom_to_window(x,y) = origin + x*scale_inv`，绑定 (pid, session, window)，新快照即失效（返回 `"zoom_context_missing"`）<br>midscene：`x/scale + offset` | 我们的目标形态 |
| **不映射**（zoom 图 = 放大镜） | Anthropic computer-use-demo："the zoom image itself is never a coordinate frame"<br>browser-use 的 OpenAI/Anthropic driver："without changing the pointer coordinate origin"<br>Agent-S：在原图上画红框 | Anthropic 的形态 |

**⇒ 两种形态各有适用面**：Anthropic 形态适合"读小字"，映射形态适合"点得准"。**我们要做精定位 ⇒ 必须走映射形态；但可以同时保留 Anthropic 形态作为"读小字"的补充。**

---

### A4. UFO² / UFO³ 有没有做 crop/zoom 精定位？—— **没有**

仓库：<https://github.com/microsoft/UFO>（README 抬头就是 **UFO³**，目录 `ufo/` = UFO²，`galaxy/` = UFO³ 银河）
论文：arXiv **2511.11332**（UFO³）、arXiv **2504.14603**（UFO²）

**逐文件核查结果**（`grep -rn -i "zoom\|crop" --include=*.py` 全仓库 **16 处命中**，全部如下）：

| 文件 | 行 | 是什么 |
|---|---|---|
| `ufo/automator/ui_control/screenshot.py` | 646-663 | `get_cropped_icons_dict()`：**把每个 UIA 元素的图标区域裁出来** |
| `ufo/automator/ui_control/screenshot.py` | 1145-1158 | 上面那个的 Agent 侧包装 |
| `ufo/automator/ui_control/screenshot.py` | 1187-1188 | 两张图对齐时裁顶部 |
| `ufo/automator/ui_control/control_filter.py` | 250-272 | `control_filter(control_dicts, cropped_icons_dict, plans, top_k)`：**用裁出的图标做 CLIP 风格 embedding 匹配**，挑 top-k 候选控件 |

`get_cropped_icons_dict` 原文：
```python
def get_cropped_icons_dict(self, annotation_dict: Dict[str, UIAWrapper]) -> Dict[str, Image.Image]:
    cropped_icons_dict = {}
    image = self.photographer.capture()
    window_rect = self.photographer.control.rectangle()
    for label_text, control in annotation_dict.items():
        control_rect = control.rectangle()
        cropped_icons_dict[label_text] = image.crop(
            self.coordinate_adjusted(window_rect, control_rect)   # ← UIA 元素自己的 rect
        )
    return cropped_icons_dict
```

**⇒ 这不是"屏幕区域放大"。** 它是**在 UIA 已经给出元素框之后**，把该框里的像素裁出来做一个图像相似度检索（把候选控件从几十个缩到 top-k），**用于解决"选哪个控件"的问题，而不是"坐标精确到哪"的问题**。

**那 UFO 的 UIA 元素框精度靠什么兜底？**

链路（`ufo/automator/ui_control/inspector.py` + `ufo/agents/processors/strategies/app_agent_processing_strategy.py`）：

1. **UIA COM API 遍历控件**，用 cache request 预取 bounding box：
   `inspector.py` L206-309 `UIABackendStrategy.find_control_elements_in_descendants`：
   ```python
   com_elem_array = window_elem_com_ref.FindAllBuildCache(scope=iuia_dll.TreeScope_Descendants,
                                                          condition=condition, cacheRequest=cache_request)
   elem_info_list = [(elem, elem.CachedControlType, elem.CachedName, elem.CachedBoundingRectangle) for elem in ...]
   rect = pywinauto.win32structures.RECT(); rect.left = elem_rect.left; rect.top = elem_rect.top; ...
   element_info._cached_rect = rect
   ```
   L330 `cache_request.AddProperty(iuia_dll.UIA_BoundingRectanglePropertyId)`
   （单元素路径 L150-158 `_get_current_rectangle` → `self._element.CurrentBoundingRectangle`）
2. **可选的视觉 grounding 后端**：OmniParser，`ufo/automator/ui_control/grounding/omniparser.py` L160-195 `_calculate_absolute_coordinates`：
   ```python
   control_left   = int(app_left   + control_box[0] * app_width)
   control_top    = int(app_top    + control_box[1] * app_height)
   control_right  = int(app_left   + control_box[2] * app_width)
   control_bottom = int(app_top    + control_box[3] * app_height)
   ```
   即 OmniParser 返回**相对 app 窗口的 bbox 分数**，用 UIA 的窗口矩形换算成绝对屏幕矩形。**但它吃的仍是同一张全屏图，没有任何 crop。**
3. **合并 + 打标**：`app_agent_processing_strategy.py` L500-527（UIA 控件）→ L628-657（OmniParser 控件）→ L660-709（merge）→ L544/L577-585 `annotation_dict = {control.id: TargetInfo}`；L907-943 构造 prompt 时同时给**控件列表**和 **SoM 标注截图**
4. **LLM 选控件 ID**：L1533-1576 `_create_action_info` → `control_id = action.arguments["id"]; target_control = annotation_dict.get(control_id)`
5. **执行** → 用该 UIA 控件的 `rectangle()` 中心点击

目标结构：`ufo/agents/processors/schemas/target.py` L18-29 `TargetInfo { kind, name, id, type, rect: Optional[List[int]] }`（rect = [left, top, right, bottom]）；`TargetRegistry` L32-164 分配 LLM 选择的数字 ID。

**结论：UFO 的精度完全依赖 Windows UI Automation 给出的控件 bounding rectangle + "LLM 在编号列表里选对编号"。它没有任何像素级精定位层，也不存在 crop-local 坐标系。**

**⇒ 对我们自研的直接含义**：
> 如果拿 UFO 当骨架，**必须自己补这一层**。UFO 提供的是"控件级"的定位（选对哪个控件），不是"像素级"的定位（点在控件内哪个像素）。UFO 的坐标系是 UIA 物理像素，正好可以作为我们 zoom 裁剪的**目标坐标系**（region 天然就在物理像素空间，不需要额外的缩放换算）。
> 另外注意：UFO 的 OmniParser 路径已经能把"视觉 bbox"接进 UIA 体系 —— **我们的 zoom 精定位完全可以照这个模式接进去**（把"裁剪后精定位出的点"当成另一个 grounding 后端，产出 `TargetInfo`），而不是去替换 UFO 的整套选择机制。

---

## B. 溯源：Anthropic 的 zoom 是逆向自公开方案，还是自己原创？

### B.1 硬时间线（全部可查证）

| 时间 | 事件 | 证据 |
|---|---|---|
| **2024-10-22** | Claude 3.5 Sonnet + computer use public beta 发布（`computer_20241022`），**无 zoom** | <https://www.anthropic.com/news/3-5-models-and-computer-use> |
| **2024-10-22 07:42 PT** | claude-quickstarts 的 **computer-use-demo 首次提交**（`init computer-use-demo`）—— **与产品发布同日** | claude-quickstarts commit **`2539a18`**（仓库首个 commit 是 2024-09-02 的 `1bbfe4a` "initial"） |
| **2024-11-15** | ShowLab/NUS 论文《The Dawn of GUI Agent》分析 Claude 3.5 CU（**第三方，不是 Anthropic**） | arXiv 2411.10323 |
| **2024-02** | V\* / SEAL《Guided Visual Search as a Core Mechanism in Multimodal LLMs》—— **最早的"crop 后再看"公开先例** | arXiv 2312.04558 |
| **2024-11-14** | **Iterative Narrowing**（`ant-8/GUI-Grounding-via-Iterative-Narrowing` 首 commit）：GUI grounding 的多轮区域收窄，**crop 永远从原图裁** | 仓库首 commit |
| **2025-05-01** | **RegionFocus**（`tiangeluo/RegionFocus` 首 commit）：0.5×0.5 裁剪 + LANCZOS 上采样 + `x_orig = left + rel/zoom` 回映射 | 仓库首 commit |
| **2025-08-04** | **GUI-Spotlight 仓库首 commit**（比 Anthropic 的 zoom 早 **3.5 个月**） | 仓库首 commit `49630f9` |
| **2025-08-06** | **ScreenSpot-Pro 仓库加入 `models/methods/reground.py`**（"Add method code"）—— **这就是一个可运行的"整屏→1024² crop→crop 内精定位→映射回全局"实现，比 Anthropic 的 zoom 早 3.5 个月** | ScreenSpot-Pro commit **`8390c0c`** |
| **2025-09-25** | **CCF / GUI-Cursor** 论文：coarse step → crop around initial click → fine steps | arXiv 2509.21552 |
| **2025-09-30** | **Ferret-UI Lite**：把 zoom-in cropping 作为 "visual tool-use" 教给模型 | arXiv 2509.26539 |
| **2025-10-05** | **GUI-Spotlight** 论文（crop/extract/find_color 工具 + offset 映射） | arXiv 2510.04039 |
| 2025-10-02 | `computer_20250124`（仍无 zoom） | SDK commit `38391da` |
| **2025-11-21** | BAMI 技术报告 repo 建立（**只有 README，无代码**） | `Neur-IO/BAMI` 首 commit |
| **2025-11-24** | ⭐ **`computer_20251124` 上线，`enable_zoom` 首次进入 SDK**（随 Claude Opus 4.5 同日发布） | SDK commit **`9843a8e6`** "feat(api): adds support for Claude Opus 4.5, Effort, Advance Tool Use Features, Autocompaction, and **Computer Use v5**" |
| **2025-11-24（11:58 -0700）** | claude-quickstarts PR #309 "Adds zoom tool for Opus 4.5" —— **开源参考实现与 API 同日**（晚于 SDK 发布数小时） | commit **`5a3c6f8`** |
| **2025-12-04/05** | **ZoomClick** 论文 + 代码 | arXiv 2512.05941 / `Princeton-AI2-Lab/ZoomClick` |
| **2026-01** | Vercel AI SDK 提 issue 要求支持 zoom + 新 beta flag | vercel/ai#11971 |
| **2026-05-13** | claude-quickstarts 新增 `computer-use-best-practices`，带一份**正确的** PIL 版 zoom | commit `b03d42c` (PR #402) |
| **2026-08-19** | SDK 加 computer/browser **toolset**（`computer_toolset_20260801`），zoom 变成默认启用的成员 | SDK commit `f51102c3` |
| **2026-08-21** | ⭐ **修掉 zoom 的经典 bug**：改为裁物理截图 | commit **`5264b72`** (PR #452) |
| **2026-10-07** | SDK 里才补上**类型化的** `ComputerZoomInput` / `BrowserZoomInput` | commit `f6346e6` |

### B.2 关键 commit 原文

**① SDK 引入 zoom（2025-11-24）** —— `anthropic-sdk-python` commit `9843a8e6`：
```
feat(api): adds support for Claude Opus 4.5, Effort, Advance Tool Use Features,
Autocompaction, and Computer Use v5
```
该 commit 里 `beta_tool_computer_use_20251124_param.py` 新增：
```python
enable_zoom: bool
"""Whether to enable an action to take a zoomed-in screenshot of the screen."""
```
注意：**当时还没有 `beta_computer_zoom_input.py` 这个类型文件**（我们用 `git show 9843a8e6:.../beta_computer_zoom_input.py` 验证过 → `fatal: path ... exists on disk, but not in '9843a8e6'`）。也就是说 **2025-11-24 时 zoom 的 input 结构是没有类型定义的裸 dict**。

**② quickstart 的 zoom 实现（2025-11-24，同日）** —— claude-quickstarts commit `5a3c6f8` 的 diff 关键段：
```python
+            x0, y0, x1, y1 = region
+            # Scale coordinates from API space to screen space
+            x0, y0 = self.scale_coordinates(ScalingSource.API, x0, y0)
+            x1, y1 = self.scale_coordinates(ScalingSource.API, x1, y1)
+
+            # Take a screenshot and crop to the specified region
+            screenshot_result = await self.screenshot()      # ← 已经是降采样过的图！
+            ...
+            crop_cmd = f"convert {temp_path} -crop {width}x{height}+{x0}+{y0} +repage {cropped_path}"
```
**⇒ 初版是从"降采样后的截图"裁、却用"屏幕物理坐标"算偏移 —— 缩放一开就必然错位。**

**③ 修 bug（2026-08-21）** —— claude-quickstarts commit `5264b72` (PR #452) 的 commit message 原文：
```
- zoom cropped the already-downscaled screenshot with physical-pixel
  coordinates, so crops landed off-region whenever scaling applied. It now
  crops the physical capture and fits the crop to the screenshot frame,
  and rejects empty or inverted regions.
```

### B.3 Anthropic 有没有引用过公开方案？

- **2025-11-24 的官方工程博客**（<https://www.anthropic.com/engineering/advanced-tool-use>）**完全没提 zoom**。它讲的是 Tool Search Tool / Programmatic Tool Calling / Tool Use Examples 三件 beta。
- Anthropic 官方 computer use 文档对 zoom 的表述只有一句：
  > "Claude uses the zoom action to inspect a region at full resolution when asked about small text or specific UI elements that aren't legible at the screenshot's default resolution."
  > —— <https://platform.claude.com/docs/en/agents-and-tools/tool-use/computer-use-tool>
- **没有任何 commit / issue / 博客 / 论文引用提到 zoom 借鉴了某篇论文或某个开源项目。**
- 反过来看，**学术界也没引用 Anthropic 的 zoom**：BAMI 的 Related Work §2.3 列举了 ScreenSeekeR、R-VLM、DiMo-GUI、GUI-RC，**没有 Anthropic computer use**。ZoomClick（2025-12）发布时 Anthropic 的 zoom 才存在 11 天。

### B.4 判定

> **(b) Anthropic 独立原创，学术界后来独立收敛到同一套路。**
> 更精确地说：**产品能力先于开源参考实现（同期同日），官方从未声称基于任何公开实现；且公开先例（V\*, Ferret-UI Lite, GUI-Cursor/CCF, GUI-Spotlight）在 2024–2025.10 确实已存在，但学界把它们当作"训练/推理技巧"，Anthropic 把同一机制提升为"agent 工具动作（tool action）"，并做了关键简化 —— zoom 不是坐标系。**

**依据（可逐条核验）**：
1. **产品先行**：`computer_20251124` + `enable_zoom` 是随 Claude Opus 4.5 在 **2025-11-24** 作为 API 能力上线的（SDK commit `9843a8e6`）；开源参考实现是**同一天稍后**的 PR #309（`5a3c6f8`，11:58 -0700）。**先有产品，后有开源原型**，不是"先有公开原型"。
   > **同一模式在 computer use 本体上已经复现过一次**：claude-quickstarts 的 computer-use-demo 首次提交是 **2024-10-22 07:42 PT**（commit `2539a18` "init computer-use-demo"），而 Anthropic 的 computer use 发布博客也是 **2024-10-22**。⇒ Anthropic 的做法一直是**"产品与开源参考实现同日发布"，从不先发原型再发产品**。
2. **无引用**：官方博客、文档、commit message 里**零引用**；没有任何 issue/blog 说"inspired by X"。
3. **机制上不等价**：Anthropic 的 zoom 与所有学术实现的**关键区别**是"crop 不是坐标系，坐标仍属全屏"。学术实现（ZoomClick/GUI-Spotlight/CCF/Cua Driver）的核心恰恰是"crop 是坐标系，要映射回去"。所以 Anthropic **不是逆向**这些实现的 —— 它做的是另一件事（读图），只是用了同一个"裁一块区域放大"的物理动作。
4. **先例存在但层级不同**：**2024-02 的 V\*（SEAL）已公开"裁剪+再问"的 MLLM 机制；GUI 场景的公开先例也有 4 个连续命中**：
   - `ant-8` Iterative Narrowing（**2024-11-14**，比 Anthropic 早 12.5 个月）
   - `tiangeluo` RegionFocus（**2025-05-01**，早 6.8 个月）
   - `bin123apple` GUI-Spotlight（**2025-08-04**，早 3.5 个月）
   - `likaixin2000` ScreenSpot-Pro 的 `models/methods/reground.py`（**2025-08-06**，早 3.5 个月；且形状与 Anthropic 的 zoom 最接近）
   - `luisf-mc/LufeMC` GUI-Cursor/CCF（**2025-09-25**，早 2 个月）

   **但这些都是"模型自己决定要不要裁"的推理技巧**（改训练/改推理，不改工具 schema）；Anthropic 把它做成了**模型可调用的工具动作**（schema 里有 `region` 参数、有 `enable_zoom` 开关、有独立的 tool version `computer_20251124`）。这是产品化层面的原创。
   ⇒ 严格说：**"crop-then-refine"这个套路不是 Anthropic 原创的**；Anthropic 原创的是"把它变成 agent 工具动作"这一步。
5. **反向证据**：学界后续工作（ZoomClick 2025-12、BAMI 2026-05、GUI-C² 2026）**都不引用 Anthropic 的 zoom**，各自独立演进；而 Anthropic 侧的 `zoom` 也从不引用它们。⇒ 双向独立收敛。

**⚠️ 一句话风险提示**：如果我们对外宣称"我们的 zoom 精定位是原创"，要小心 —— 学术界这套路（crop + 坐标映射回全局）**有大量先行者**，最接近的是 GUI-Spotlight（2025-08 就有代码）和 trycua Cua Driver。建议我们把差异点打在"**多尺度预算分配 / 终止策略 / 与 UFO 的 UIA 坐标系对齐**"上，而不是"我们也会 crop"。

---

## C. 对我们自研的直接结论

### C.1 目标流水线与现成零件的对应

目标：**整屏粗定位 → 原图裁 512×512 → crop 内精定位 → 映射回物理像素**

| 环节 | 现成代码 | 抄哪里 | 备注 |
|---|---|---|---|
| ① 整屏粗定位 | 我们自己的 grounding 模型 | — | UI-TARS / UGround / Qwen3-VL 系任意 grounding 模型 |
| ② **原图裁 512×512** | ✅ 有 | **ScreenSpot-Pro `models/methods/reground.py::crop_to_center()`**（最贴切：居中裁固定尺寸窗口，撞边反向扩张）<br>**CCF `cursor_ccf.py::compute_crop_window()`**（贴边平移不缩小）<br>**Cua Driver `capture_utils.rs::crop_png_to_jpeg()`**（含 20% padding + 输出封顶 500px）<br>**ZoomClick `crop_center_px()`**（三种边界策略）<br>**RegionFocus `crop_and_upsample()`**（裁完 LANCZOS 上采样回视口） | ⚠️ 必须从**原始分辨率**截图裁，不能从降采样图裁（Anthropic 踩过这个坑） |
| ③ crop 内精定位 | ✅ 有 | 直接复用我们的 grounding 模型（输入换成 crop） | CCF 明确：**只做一次精修**，多次迭代会累积映射误差 |
| ④ **映射回物理像素** | ✅ 有 | **CCF `map_crop_to_orig()`**（`left + x, top + y`，最简）<br>**ScreenSpot-Pro `reground.py::view_bbox_to_real_bbox()`**（viewport 归一化版）<br>**Cua Driver `ZoomContext::zoom_to_window()`**（`origin + x*scale_inv`，处理了 crop 被缩小的情况，最完整）<br>**RegionFocus `x_orig = left + (rel_zoomed_x / zoom_x)`** | 若 crop 不改变像素尺度 → 加法即可；若 crop 会被缩小 → 必须乘 scale_inv |
| ⑤ 降采样坐标反算 | ✅ 有 | `anthropics/claude-quickstarts` `computer-use-best-practices/computer_use/image.py::target_image_size()` + `resize_and_encode()`<br>CCF `_maybe_downsize_for_coarse()` | Anthropic 的 image.py 直接给出"服务端会二次缩放导致 ~14% 点击漂移"的警告，值得直接采纳其尺寸策略 |
| ⑥ 要不要 zoom 的门控 | ✅ 有 | CCF `_should_skip_ccf()`（像素数 < 200k 不 zoom）+ `classify_instruction()`（文字目标不 zoom）<br>ZoomClick `prezoom_first_iteration_distance()` | 直接抄，能省大量延迟 |
| ⑦ 终止条件 | ✅ 有 | CCF：**单次精修 + 失败回退粗定位**<br>ZoomClick：`in_depth=3` + 无法再缩则停 | 建议抄 CCF 的"单次精修 + 回退" |
| ⑧ 评测 | ✅ 有 | `likaixin2000/ScreenSpot-Pro-GUI-Grounding/eval_screenspot_pro.py` | 见 C.4 |

### C.2 至少 2 个可直接参考的**完整实现**

#### ★★ 完整实现 #0（**首选**）：**ScreenSpot-Pro `models/methods/reground.py`**

- **仓库**：<https://github.com/likaixin2000/ScreenSpot-Pro-GUI-Grounding>，`models/methods/reground.py`（153 行）
- **形状与我们完全一致**：整屏 1 遍 → 以粗 bbox 中心裁 **1024×1024** → crop 内第 2 遍 → 映射回全图归一化坐标
- **改动成本极小**：把 `crop_size=[1024, 1024]` 改成 `[512, 512]` 即满足我们的目标
- **已经接好官方评测**：`ground_only_positive(instruction, image) -> {"result", "bbox", "point", "raw_response"}`，`point` 是**归一化**坐标，正好是 `eval_screenspot_pro.py` 需要的格式
- **可直接复用的函数**：
  - `crop_to_center(img, bbox, crop_size)` —— 固定尺寸窗口居中裁，**撞边时向反方向扩张**以保住窗口尺寸（比"裁小了"更稳）
  - `view_bbox_to_real_bbox(view_bbox, viewport)` —— viewport 内的归一化框 → 全图归一化框
  - 同目录 `iterative_narrowing.py`（多轮 ±0.25 归一化窗口）、`iterative_focusing.py`（多轮四象限）、`screenseeker.py`
  - `models/zoom_consistency_router.py` —— 双模型一致性路由版（自带 `CROP_RATIO=0.5`、crop 后 resize 回原尺寸、`abs2_x = x1 + (step2[0]/1000.0)*(x2-x1)` 的回映射）
- **同仓还有 4 个可复用变体**，说明这条路线是"被反复实现过的成熟套路"，不是孤例

#### ★ 完整实现 #1：**CCF `src/cursor_ccf.py`**（`LufeMC/gui-g2-3b-ccf`）

- **仓库**：<https://github.com/LufeMC/gui-g2-3b-ccf>（文件 `src/cursor_ccf.py`，165 行；模型卡 <https://huggingface.co/luisf-mc/gui-g2-3b-ccf>）
- **论文**：arXiv 2509.21552
- **完整度**：单文件、纯 PIL、无 torch 依赖、带 `make_ccf_eval_adapter()` 挂到 eval 脚本、**220 个单测**、**一份诚实的 9 阶段复盘 `benchmarks/results.md`**
- **可直接复用的环节**：
  - `compute_crop_window(center, image_size, zoom_factor=2.0, min_crop_side=112)` → **512×512 裁剪窗口怎么算**
  - `map_crop_to_orig(x_on_crop, y_on_crop, crop_window)` → **crop 坐标映射回原图**
  - `_maybe_downsize_for_coarse(image, max_pixels)` → **粗定位阶段降采样 + 坐标放大回去**
  - `_should_skip_ccf()` + `classify_instruction()` → **要不要 zoom 的门控**
  - `ccf_predict_bbox()` → **粗→精→映射的完整编排**
- **对我们 512×512 目标**：把 `zoom_factor=2.0` 换成"固定 512 宽"（`compute_crop_window` 里把 `target_w = max(min_crop_side, int(img_w/zoom_factor))` 改成 `target_w = 512` 即可，其余逻辑不变）
- **可直接抄进我们的理由**：它是**唯一一份"不训练、纯推理时、包装任意 grounding 模型"**的实现，接口就是 `predict_fn(image, instruction) -> (x, y)`，和我们的调用方式完全一致。

#### ★ 完整实现 #2：**trycua Cua Driver（Rust）**

- **仓库**：<https://github.com/trycua/cua>，路径 `libs/cua-driver/rust/crates/`
- **完整度**：跨 macOS/Windows/Linux 三平台；**有"裁剪 → 缩放 → 坐标映射"的完整闭环**，还有往返测试
- **可直接复用的环节**：
  - `cursor-overlay/src/capture_utils.rs::crop_png_to_jpeg()` → **裁剪 + 20% padding + 输出封顶 + 返回 `origin_x/origin_y/scale_inv`**
  - `cua-driver-core/src/snapshot_store.rs::ZoomContext::zoom_to_window()` → **crop 坐标 → 窗口原生像素**
  - `platform-macos/src/tools/click.rs` L1022-1039 → **有 zoom 上下文 / 无 zoom 上下文两条分支的统一消费**
  - `cua-driver/tests/protocol_media_test.rs::zoom_from_zoom_click_round_trip` → **往返一致性测试模板**
- **为什么值得抄**：它是全场唯一处理了"**裁剪结果本身被缩放过**"的实现 —— 我们如果把 512×512 crop 缩到模型输入尺寸（多数 grounding 模型会），那 crop 内坐标 × scale_inv 才等于原图像素，**这一段纯加法会算错**。CCF / ZoomClick 都假设 crop 不缩放。

#### ☆ 补充完整实现 #3：**ZoomClick `grounding/eval_sspro_zoomclick.py`**（多轮迭代版）

- **仓库**：<https://github.com/Princeton-AI2-Lab/ZoomClick>
- **完整度**：多轮迭代（`in_depth`）+ Pre-zoom patch 网格 + 三种边界策略 + GUIZoom-Bench 构建脚本
- **可直接复用**：`view_point_to_real_point()` / `view_box_to_real_box()`（**支持任意深度嵌套 crop 的 viewport 映射**）、`crop_center_px()`、`prezoom_first_iteration_distance()`、`GUIZoom-Bench/build_guizoom.py`（做 zoom 能力 benchmark）
- **注意**：ZoomClick 的映射用**归一化 viewport**，数学上更通用（可同时处理缩放），但代码比 CCF 绕

#### ☆ 补充完整实现 #4：**GUI-Spotlight `spotlight/tools.py` + `tool_env.py`**

- **仓库**：<https://github.com/bin123apple/GUI_Spotlight>
- **可复用**：`crop()` 返回 offset 的接口设计（`return data, message, top_left`）、**嵌套 crop 的 offset 累加**（`x_off_set = images_offset[id_num][0] + dx`）、`extract()` 象限裁剪、`correct_crop_func` 奖励函数
- **注意**：它的 offset 是**纯加法**（假设 crop 不缩放），且把 offset **编码进给模型的文本消息**（`[Image_1 is displayed above, offset: (x, y)]`）让模型自己知道自己在哪个坐标系 —— 这个设计对"多轮 crop + 模型自述坐标归属"很有参考价值

#### ☆ 补充实现 #5（Anthropic 侧）：**`computer-use-best-practices/computer_use/tools/computer.py::_zoom()`**

- **可复用**：**物理分辨率重截 + 截图空间→物理空间换算（`fx = full.width / self.sent_w`）+ 裁剪 + 等比缩回模型框** 的完整闭环
- **不可复用**：它**没有坐标映射**（设计如此），所以只能抄"裁剪管线"，不能抄"映射"
- **配套**：`computer-use-best-practices/computer_use/image.py::target_image_size()/resize_and_encode()` —— **强烈建议直接抄的"截图尺寸策略"**

### C.3 我们**必须自己写**的部分（Anthropic 不提供、学术界实现也需要改造）

| # | 必须自己写的段 | 为什么 | 可参考 |
|---|---|---|---|
| **1** | **crop ↔ 全局 的双向坐标映射**（含 crop 被缩放时的 scale） | **Anthropic 明确不做**（zoom 不是坐标系）。CCF/ZoomClick 只在"crop 不缩放"下成立 | Cua Driver `ZoomContext::zoom_to_window()`（最完整）；CCF `map_crop_to_orig()`（最简） |
| **2** | **多尺度 / 终止策略**（何时停、裁几次、要不要退粗定位） | Anthropic 完全不做（模型自己决定要不要再 zoom）；CCF 是"单次精修"，ZoomClick 是"固定 3 轮" | CCF 的 `fallback_to_coarse_on_invalid` + `_should_skip_ccf` + `classify_instruction` 门控；ZoomClick 的 `in_depth`/`min_crop_size` |
| **3** | **region 的坐标系定义与契约**（region 用物理像素还是模型截图像素） | Anthropic 用**模型截图像素**（我们若用 UFO 骨架，UIA 给的是**物理像素** —— **两者不一致，必须显式选一个并在 prompt/schema 里写死**） | Anthropic SDK `ComputerZoomInput` 的 docstring 就是模板："Region coordinates are in the full-screenshot space (not physical display pixels)" |
| **4** | **原始分辨率重截 + 裁剪的执行层** | 这是**工程活**不是算法活：保证裁的是**没降采样的图**、保证 crop 尺寸固定、保证坐标不随 resize 漂移 | Anthropic `_capture()` + `convert -crop ... +repage -resize ...`；或 PIL `img.crop()`（CCF/GUI-Spotlight/ZoomClick 都用 PIL，更可控） |
| **5** | **评测适配层** | 见 C.4 | `eval_screenspot_pro.py` |

> **一句话**："裁剪"本身不用自己写（ImageMagick / PIL 都现成），**真正必须自己写的是坐标系契约 + 映射函数 + 终止/门控策略**。

### C.4 现成测试集 / 评测脚本

#### ★ ScreenSpot-Pro（首选）

- 仓库：<https://github.com/likaixin2000/ScreenSpot-Pro-GUI-Grounding>
- **评测脚本**：`eval_screenspot_pro.py`（我们已完整读过源码）
- **核心判据**（point-in-bbox）：
  ```python
  def eval_sample_positive_gt(sample, response):
      bbox = sample["bbox"]                       # [x1, y1, x2, y2]
      img_size = sample["img_size"]
      bbox = [bbox[0]/img_size[0], bbox[1]/img_size[1], bbox[2]/img_size[0], bbox[3]/img_size[1]]
      click_point = response["point"]
      if click_point is None: return "wrong_format"
      if (bbox[0] <= click_point[0] <= bbox[2]) and (bbox[1] <= click_point[1] <= bbox[3]):
          return "correct"
      return "wrong"
  ```
  ⚠️ **注意**：脚本内部把 bbox 归一化到 [0,1]，而 `response["point"]` **必须是归一化坐标**；出报告时用 `point_in_pixel = [point[0]*img_size[0], point[1]*img_size[1]]`。我们的精定位输出必须统一到**归一化**或**原图像素**其中之一，并做适配。
- 指标：`action_acc` / `text_acc` / `icon_acc`，并按 platform / application / group / instruction_style / gt_type 多维细分
- **接入方式**：把我们的精定位包装成 `model_factory.build_model(args)` 要求的接口，只需实现 `ground_only_positive(instruction, image_path) -> {"point": [nx, ny], "raw_response": str}`。**ZoomClick 的 `PrezoomIterativeNarrowingRunner` 和 ScreenSpot-Pro 自带的 `models/methods/reground.py` 都是这么接的**，可以直接当模板（后者还额外实现 `ground_allow_negative` 以跑负样本）。
- ⭐ **同一个仓库的 `models/methods/` 目录就是现成的基线实现**：`reground.py`（2 步，1024²）、`iterative_narrowing.py`（3 轮 ±0.25）、`iterative_focusing.py`（3 轮四象限）、`screenseeker.py`、`models/zoom_consistency_router.py`（双模型一致性路由，自报 ScreenSpot-Pro 80.9%）。**⇒ 我们的实现可以直接和这 5 个基线在同一套评测脚本上跑表。**
- **这 5 个方法已在 `model_factory.py` L140-172 注册**，可直接跑（默认 grounder 是 `OSAtlas7BVLLMModel`，换成我们的 grounding 模型只需实现同一个 `ground_only_positive` 接口）：
  ```bash
  python eval_screenspot_pro.py \
      --model_type reground \
      --model_name_or_path Qwen/Qwen3-VL-8B-Instruct \
      --screenspot_imgs "../data/ScreenSpot-Pro/images" \
      --screenspot_test "../data/ScreenSpot-Pro/annotations" \
      --task "all" --language "en" --gt_type "positive" \
      --inst_style "instruction" \
      --log_path "./results/reground.json"
  ```
  可用 `--model_type`：`screenseeker` / `reground` / `iterative_narrowing` / `iterative_focusing` / `zoom_consistency_router`
  （仓库另带 `run_ss_pro.sh`、`run_ss_pro_cn.sh` 两个批量脚本，以及 `eval_screenspot_pro_parallel.py` 多卡版）
- 数据：<https://github.com/likaixin2000/ScreenSpot-Pro-GUI-Grounding> 官方发布，1581 条指令，每条独立截图（HF: `likaixin/ScreenSpot-Pro`）

#### ★★ ⚠️ 坐标口径陷阱（**抄代码时最容易踩的坑**）

各家 eval 脚本对 "point" 和 "bbox" 的口径**不统一**，混用会得到**全错的分数**：

| 评测脚本 | GT bbox 格式 | 期望的 point 格式 |
|---|---|---|
| ScreenSpot-Pro 官方 `eval_screenspot_pro.py` | 像素 `[x1,y1,x2,y2]`，脚本内部按 `img_size` 归一化 | **归一化 [0,1]** |
| ZoomClick 的副本 `eval_sspro_zoomclick.py` | 像素 `[x1,y1,x2,y2]`，**不归一化** | **像素**（用 `_norm_to_pixel_point(last_pt_real, (orig_W, orig_H))` 转） |
| CCF 仓的 ScreenSpot-v2 `src/eval.py` | **`[x, y, W, H]`**（宽高，不是 x2/y2！） | 像素 |
| GUI-Spotlight `screenspot_pro_evaluation.py` | 像素 `[x1,y1,x2,y2]` | 像素 |
| ShowUI `main/eval_screenspot.py` | 归一化后转 **0–999/1000 网格** | 0–999 网格 |
| ant-8 Iterative Narrowing `utils.py::is_in_bbox` | **`[x, y, W, H]`** | 像素（模型出 /999） |
| UI-Zoomer `eval_sample_positive_gt` | 像素 | 像素 |
| RegionFocus `eval_screenspot_pro_RegionFocus.py` | 像素 | 像素 |

**⇒ 我们必须在自己的 model wrapper 里把坐标口径统一并显式声明**（建议：**内部一律用原图像素**，只在 `ground_only_positive` 的返回前按各脚本要求转一次），否则会出现"接了官方脚本但分数是 0"的假象。

#### ★ GUIZoom-Bench（专门评 zoom 能力）

- 仓库：<https://github.com/Princeton-AI2-Lab/ZoomClick> 的 `GUIZoom-Bench/` 目录
- 构建：`python GUIZoom-Bench/build_guizoom.py --src_dataset_root <ScreenSpot-Pro> --depth1 ... --depth4 ... --out_dir <GUIZoom-Bench>`
- 评分：`python GUIZoom-Bench/collect_guizoom_accuracy.py --results <sspro结果.json> --dataset <GUIZoom-Bench> --output <out.json>`
- **特点**：不用重新跑推理，直接从 ScreenSpot-Pro 的结果重组成"模型 zoom 能力"评测 —— **我们如果做了 zoom，直接复用这套就能出"zoom 有没有用"的对比数字**

#### ★ 其它可用的

| 名称 | 出处 | 备注 |
|---|---|---|
| **ScreenSpot-v2**（自包含 eval） | `LufeMC/gui-g2-3b-ccf` → `src/eval.py` | CLI：`--base-model --data --ccf --ccf-type-gate --coarse-max-pixels --attn-impl`；命中判定 L554-556（**bbox 是 x,y,W,H**）。数据 HF `HongxinLi/ScreenSpot_v2` / `lmms-lab/ScreenSpot-v2` |
| ScreenSpot-v2（原始 benchmark） | <https://github.com/njucckevin/SeeClick> | 原始 ScreenSpot 评测代码；HF 数据集 `Voxel51/ScreenSpot-v2` |
| **ShowUI** | <https://github.com/showlab/ShowUI> → `main/eval_screenspot.py` | `get_bbox()` L28-40（w,h → x1,y1,x2,y2，再归一化到 0–999/1000 网格）、`pointinbbox()` L42-48；另有 `main/evaluator.py`、`data/dset_screenspot.py` |
| **UI-Zoomer** | `ZJU-REAL/UI-Zoomer/UI-Zoomer/uizoomer.py` | `eval_sample_positive_gt` L392-399（像素 bbox vs 像素 point）+ `Baseline/run_baseline_{sspro,ssv2,uivision}.py` |
| **RegionFocus** | `tiangeluo/RegionFocus/eval_screenspot_pro_RegionFocus.py` | + `summarize_results.py`，由 `run_ss_pro_{qwen25vl,uitars}_RegionFocus[_7b].sh` 启动 |
| **Iterative Narrowing** | `ant-8/.../eval-{intern-vl,os-atlas-7b,qwen,show-ui}.ipynb` | 标注用 ScreenSpot v1（`screenspot/screenspot_{desktop,mobile,web}.json`）；命中判定 `utils.py::is_in_bbox` L69-73（**bbox 是 x,y,W,H**，与 ScreenSpot-Pro 不同！） |
| **GUI-Spotlight**（ScreenSpot-Pro + OSWorld-G + UI-Vision） | `bin123apple/GUI_Spotlight/` | `screenspot_pro_evaluation.py`（从 HF 下 `likaixin/ScreenSpot-Pro`，`ToolEnv(..., tools=[crop], max_steps=5)`）、`osworld_g_evaluation.py`（`--model Bin12345/GUI-Spotlight --dataset_json OSWorld-G_refined.json --images_dir OSWorld-G/benchmark/images --batch_size 1`）、`uivision_evaluation.py`；奖励 `vg_reward_func` L302-354：`reward = 1.0 if (x1 <= x <= x2 and y1 <= y <= y2) else 0.0`；`spotlight/reward/tool_rubric.py` 还有 `correct_crop_func` L226+ / `correct_crop_ratio` L280+ 可复用的 **crop 质量奖励** |
| **OSWorld / OSWorld-G** | <https://github.com/xlang-ai/OSWorld>（`evaluation_examples/examples/{chrome,gimp,libreoffice_*,multi_apps,os,thunderbird,vlc,vs_code}`、`run.py`、`metrics/`）<br><https://github.com/xlang-ai/OSWorld-G>（`OSWorld-G_refined.json` + `benchmark/images`） | grounding 子集；GUI-Spotlight 的 `osworld_g_evaluation.py` 直接消费 |
| 多轮 grounding benchmark | `microsoft/precision-cua-bench`（arXiv 2604.13019） | "See, Point, Refine" 的评测 |
| ScreenSuite | <https://github.com/huggingface/screensuite> | 综合 harness |
| grounding leaderboard | <https://gui-agent.github.io/grounding-leaderboard/> | 对比各家数字 |

**通用 point-vs-bbox 判定（5 行，各仓都有，最干净的三个版本）**：
- CCF `src/eval.py` L554-556（x,y,W,H）
- GUI-Spotlight `screenspot_pro_evaluation.py` L345-347（x1,y1,x2,y2）
- ShowUI `main/eval_screenspot.py` L42-48（归一化）
- ScreenSpot-Pro `eval_screenspot_pro.py` L141-152（bbox 按 img_size 归一化，point 期望归一化）

### C.5 落地建议 + 参考实现骨架

**1) 坐标系契约**（必须最先定死）
明确 region 用 **UIA 物理像素**（配合 UFO 骨架）或**模型截图像素**（配合 Anthropic 风格），**并在 schema 里写死**。不要混用。
建议直接抄 Anthropic SDK 的措辞作模板：
```
"""Region coordinates are in the full-screenshot space (not physical display pixels).
The crop is scaled up to fill the image budget so fine details become legible."""
```

**2) 裁剪源**：永远从**未降采样的原始截图**裁；物理分辨率图与给模型的降采样图**分开保存**。

**3) 映射**：抄 Cua Driver 的 `origin + x*scale_inv` 形式（能同时覆盖"crop 不缩放"和"crop 被缩放"两种情况）。若确定 crop 永远不缩放，抄 CCF 的 `left + x, top + y` 即可。

**4) 策略**：抄 CCF 的 **单次精修 + 失败回退粗定位 + 小图不 zoom + 文字目标不 zoom**，并按 CCF 复盘（`benchmarks/results.md`）的三条改进调整：
   - `min_pixels_for_ccf` 门限从 200K **提到 ~1M**（让 mobile/平板整张跳过精修）
   - `zoom_factor` 从 2.0 **降到 1.5**（crop 更宽，减少文字漂移和"把目标裁出去"）
   - 保留 type-aware gate（文字目标不精修）
   - 粗定位阶段走 `--coarse-max-pixels` 降采样（实测把 1920×1080 的粗定位从 10.6s 降到 4.3s，精度无损）

**5) 评测**：把精定位包装成 `ground_only_positive()`，直接跑 ScreenSpot-Pro 的 `eval_screenspot_pro.py`；再叠 GUIZoom-Bench 出 zoom 增益对比。

---

**参考实现骨架**（把 `reground.py` 的形状 + CCF 的门控 + Cua Driver 的 scale 处理合起来）：

```python
# 坐标契约：region = 原图物理像素 (x0, y0, x1, y1)；crop 从原图裁，不缩放
CROP_SIZE = 512          # 我们要的 512×512
MIN_PIXELS_FOR_ZOOM = 200_000   # 抄 CCF：小于 ~450×450 不 zoom

def zoom_locate(grounder, orig_img: Image.Image, instruction: str):
    """grounder(image) -> {'bbox': [x1,y1,x2,y2] in [0,1] of that image} 或 {'point': (nx, ny)}"""
    W, H = orig_img.size

    # ── 第 1 步：整屏粗定位 ──────────────────────────────
    coarse = grounder(orig_img, instruction)
    if coarse is None:
        return None
    cb = coarse.get("bbox")
    if cb is None:
        p = coarse["point"]; cb = [p[0], p[1], p[0], p[1]]

    # ── 门控：图太小 / 目标是文字 → 不 zoom，直接用粗定位 ──
    if W * H < MIN_PIXELS_FOR_ZOOM:
        return center_of(cb, W, H)

    # ── 第 2 步：以粗 bbox 中心裁固定尺寸窗口（抄 reground.crop_to_center）──
    cx = (cb[0] + cb[2]) / 2 * W
    cy = (cb[1] + cb[3]) / 2 * H
    left, top = crop_window(cx, cy, W, H, CROP_SIZE)   # 撞边时反向扩张，保住尺寸
    right, bottom = left + CROP_SIZE, top + CROP_SIZE
    crop = orig_img.crop((left, top, right, bottom))   # ← 从原图裁

    # ── 第 3 步：crop 内精定位 ──────────────────────────
    fine = grounder(crop, instruction)
    if fine is None:
        return center_of(cb, W, H)                     # ← 失败回退粗定位（抄 CCF）
    fb = fine.get("bbox")
    if fb is None:
        p = fine["point"]; fb = [p[0], p[1], p[0], p[1]]

    # ── 第 4 步：映射回全图物理像素 ──────────────────────
    #   fb 是 crop 内归一化坐标；crop 若被缩放过还要乘 scale_inv
    scale_inv = 1.0                                    # crop 不缩放时为 1
    fx = left + (fb[0] + fb[2]) / 2 * CROP_SIZE * scale_inv
    fy = top  + (fb[1] + fb[3]) / 2 * CROP_SIZE * scale_inv
    return (fx, fy)
```

**这段骨架里每一行都对应一个可抄的出处**：

| 骨架片段 | 出处 |
|---|---|
| `crop_window(cx, cy, W, H, 512)` 撞边反向扩张 | ScreenSpot-Pro `models/methods/reground.py::crop_to_center()` L39-93 |
| `left + (fb[0]+fb[2])/2 * CROP_SIZE * scale_inv` | `reground.py::view_bbox_to_real_bbox()` L18-36 / Cua Driver `ZoomContext::zoom_to_window()` |
| `scale_inv`（crop 被缩放时） | Cua Driver `capture_utils.rs::crop_png_to_jpeg()` + `snapshot_store.rs::ZoomContext` |
| `MIN_PIXELS_FOR_ZOOM` 门控 | CCF `cursor_ccf.py::_should_skip_ccf()` |
| "失败回退粗定位" | CCF `cursor_ccf.py::ccf_predict_bbox()` 的 `fallback_to_coarse_on_invalid` |
| "只做一次精修，不做多轮迭代" | CCF 文件头注释：`Single refinement pass. Iterative narrowing (> 1 refinement) gives diminishing returns and compounds coordinate-mapping errors.` |
| "目标是文字就不 zoom" | CCF `classify_instruction()`（实测文字 -2.3pp / 图标 +2.2pp） |
| `grounder(image) -> {'bbox'|'point'}` 接口 | ScreenSpot-Pro `model_factory.py` 的 model 接口约定 |
| `target_image_size()` 截图尺寸策略 | Anthropic `computer-use-best-practices/computer_use/image.py` |

---

## 附录：本次调研的坑（都踩过）

1. **Anthropic 官方参考实现的第一版是错的**（从降采样图裁 + 用物理坐标偏移），2026-08-21 才修。**不要抄 2025-11-24 那版。**
2. **Anthropic 的 zoom 与学术界同名但不同义**：Anthropic = "读图"；学术界 = "精定位"。我们的目标（映射回物理像素）**属于后者**，不能直接抄 Anthropic。
3. **huggingface.co 在本机不可达**，用 `https://hf-mirror.com/<repo>/raw/main/<file>` 代替。
4. **arXiv ID 张冠李戴**：`2511.21631` = Qwen3-VL Technical Report，不是 ReGround；`thinking-in-space` = VSI-Bench 空间智能 benchmark，不是 GUI grounding。
5. **BAMI 仓库没有代码**（只有 README），别当可抄实现。
6. **trycua 的 Python SDK 没有 zoom**，zoom 只在 Rust Cua Driver。
7. **OpenClaw 的 zoom 不是自己实现的**，是 trycua 的薄封装。
8. **UFO²/UFO³ 没有 zoom**，`crop` 只用于图标级 CLIP 匹配。
9. **ScreenSpot-Pro 的 eval 脚本里 bbox 被归一化到 [0,1]**，输出的 point 也必须是归一化坐标，否则判据全错。
10. **别被 claude-quickstarts 里的 "zoom" 字样误导**：2025-04-21 之前 quickstart 里就有 "zoom" 字样，但那是 ① CSS 里的 `zoom-out-95` ② computer-use-demo 系统提示里的 "it can be helpful to **zoom out** so that you can see everything on the page"（指浏览器 ctrl+- 缩放）。**这两个都不是 zoom 动作。** 我们用 `git log -S 'zoom' --pickaxe-regex` 全历史扫过，2025-11-24 之前**没有任何 zoom 动作实现**。
11. **InnerZoom 仓库存在但只有 LICENSE + README**（"pending Alibaba approval"），别当成可抄实现。
12. **坐标口径不统一**（见 C.4 的对照表）：`[x1,y1,x2,y2]` vs `[x,y,W,H]`、归一化 vs 像素 vs 0–999 网格，混用会得到"接了官方脚本但分数全 0"的假象。

---

## 附录二：**本次调研没有核实的部分**（明确声明，别当已证事实）

| 项 | 状态 |
|---|---|
| **Jedi / AnyClick / ConClick / Seg2Click / Groma / OS-Atlas** | ⚠️ **clone 因网络失败**（`github.com:443` 不通），其"无 crop/zoom"是**基于方法知识 + 检索，不是读源码核实**。建议在网络正常时补 clone 复核。按方法性质它们分别是：Jedi=数据集发布；AnyClick/ConClick=单次出点的 click-anywhere grounding；Seg2Click=走 **SAM 分割掩码**而非 crop；Groma=通用开放集 grounding；OS-Atlas=grounding 预训练模型（单次出点） |
| **huggingface.co 全程不可达** | CCF 的 HF 仓库 `predict.py` 从未读到；GitHub 镜像 `LufeMC/gui-g2-3b-ccf` 有同一份 `src/cursor_ccf.py`（已核实） |
| **AutoFocus（arXiv 2605.02630）** | ⚠️ **从未 fetch arXiv 页面**，只从搜索引擎摘要见过，连标题本身都应视为未核实 |
| **GUI-Lens（2608.03270）/ MHVF（2610.17792）** | ⚠️ 未 fetch arXiv 页面；GitHub 上按 "zoom GUI grounding" 搜只有 5 个仓（ZoomClick / DiMo-GUI-Implementation / InnerZoom / Attention-based-GUI-Actor / RegionFocus-Implementation），**均无** GUI-Lens / MHVF |
| **Rashid0002/Attention-based-GUI-Actor** | 仓库树已用 GitHub API 核实（含 `scripts/integrate_with_gui_actor.py`），但**文件内容未读** ⇒ crop 比例 / 回映射未核实 |
| **BAMI 的 `asset/bami-v2.pdf`** | 未解析 ⇒ BAMI 的 Coarse-to-Fine Focus 超参（crop size、阈值）只有 README 层面的描述 |
| **Anthropic 官方文档原文** | `platform.claude.com/docs/...` 会跨域重定向到 `www.anthropic.com`，web_fetch 不跟随 ⇒ 文档里的 zoom 描述是**通过搜索摘要引用**得到的（"Claude uses the zoom action to inspect a region at full resolution when asked about small text or specific UI elements that aren't legible at the screenshot's default resolution."）。**但 SDK 类型定义的 docstring 是直接读源码得到的，权威性更高** |
| **UFO 的 OmniParser 路径** | 读的是 `ufo/automator/ui_control/grounding/omniparser.py` 的坐标换算代码；未实际运行 |


---

## 参考链接汇总

**Anthropic 官方**
- claude-quickstarts：<https://github.com/anthropics/claude-quickstarts>
  - computer-use-demo zoom：<https://github.com/anthropics/claude-quickstarts/blob/main/computer-use-demo/computer_use_demo/tools/computer.py>
  - computer-use-best-practices zoom：<https://github.com/anthropics/claude-quickstarts/blob/main/computer-use-best-practices/computer_use/tools/computer.py>
  - image.py / target_image_size：<https://github.com/anthropics/claude-quickstarts/blob/main/computer-use-best-practices/computer_use/image.py>
- anthropic-sdk-python：<https://github.com/anthropics/anthropic-sdk-python>
  - `computer_zoom_input.py` / `computer_zoom_config_param.py` / `computer_toolset_20260801_param.py`
- 文档：<https://platform.claude.com/docs/en/agents-and-tools/tool-use/computer-use-tool>
- 工程博客（advanced tool use，2025-11-24，未提 zoom）：<https://www.anthropic.com/engineering/advanced-tool-use>
- computer use 发布（2024-10-22）：<https://www.anthropic.com/news/3-5-models-and-computer-use>

**开源实现（可直接抄）**
- **ScreenSpot-Pro 官方评测仓的 `models/methods/`**（首选）：<https://github.com/likaixin2000/ScreenSpot-Pro-GUI-Grounding>
  - `models/methods/reground.py`（2 步 1024²）、`iterative_narrowing.py`（3 轮 ±0.25）、`iterative_focusing.py`（3 轮四象限）、`screenseeker.py`
  - `models/zoom_consistency_router.py`（双模型一致性路由）
  - `model_factory.py` L140-172 已注册：`--model_type screenseeker|reground|iterative_narrowing|iterative_focusing|zoom_consistency_router`
- CCF：<https://github.com/LufeMC/gui-g2-3b-ccf>（`src/cursor_ccf.py` + `benchmarks/results.md` 复盘；模型卡 <https://huggingface.co/luisf-mc/gui-g2-3b-ccf>，镜像 <https://hf-mirror.com/luisf-mc/gui-g2-3b-ccf/raw/main/cursor_ccf.py>）｜论文 arXiv 2509.21552
- ZoomClick：<https://github.com/Princeton-AI2-Lab/ZoomClick>｜论文 arXiv 2512.05941
- GUI-Spotlight：<https://github.com/bin123apple/GUI_Spotlight>｜论文 arXiv 2510.04039
- trycua/cua（Cua Driver）：<https://github.com/trycua/cua>
- OpenClaw：<https://github.com/openclaw/openclaw>
- UI-Zoomer：<https://github.com/ZJU-REAL/UI-Zoomer>
- RegionFocus：<https://github.com/tiangeluo/RegionFocus>
- Iterative Narrowing：<https://github.com/ant-8/GUI-Grounding-via-Iterative-Narrowing>
- BAMI（**无代码**）：<https://github.com/Neur-IO/BAMI>｜论文 arXiv 2605.06664
- GUI-C²：<https://github.com/z1oong/GUI-C2>｜论文 arXiv 2605.30884
- UFO³：<https://github.com/microsoft/UFO>｜论文 arXiv 2511.11332 / 2504.14603
- midscene DeepLocate：<https://github.com/web-infra-dev/midscene>（`packages/core/src/ai-model/workflows/grounding/search-area.ts`）
- browser-use 的 hosted-model driver：<https://github.com/browser-use/browser-use>（`browser_use/integrations/{openai,anthropic}/browser_use.py`）

**评测**
- ScreenSpot-Pro：<https://github.com/likaixin2000/ScreenSpot-Pro-GUI-Grounding>（`eval_screenspot_pro.py` / `eval_screenspot_pro_parallel.py` / `run_ss_pro.sh`）｜论文 arXiv 2504.07981
- ScreenSpot-v2：<https://github.com/njucckevin/SeeClick>
- grounding leaderboard：<https://gui-agent.github.io/grounding-leaderboard/>

**其它先例**
- V\* / SEAL：arXiv 2312.04558
- Ferret-UI Lite：arXiv 2509.26539
- The Dawn of GUI Agent（第三方分析 Claude 3.5 CU）：arXiv 2411.10323
- GUI-Cursor / CCF：arXiv 2509.21552
- See, Point, Refine（Microsoft）：arXiv 2604.13019 / <https://github.com/microsoft/precision-cua-bench>

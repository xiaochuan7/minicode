# 06 · Computer Use：动作协议（action space）与循环范式

> **调研智能体 B** · 基准日期 **2026-10-19**
> 目标：把「computer use」的**动作协议 + 循环范式**查到能直接写代码的程度。
> 坐标系/精度的数学推导见同目录 [`02-coordinate-systems.md`](./02-coordinate-systems.md)（`PHYS-PX-v1` 协议），本文只补**动作层与循环层**，不重复推导。
> 证据分级标注：**[一手]** = 我直接抓取到官方原文/官方代码；**[二手]** = 官方文档经搜索引擎摘录或第三方转述；**[推断]** = 我的工程判断，非原文。

---

## 0. 结论先行（TL;DR）

**1. 坐标格式一句话对照**

| 厂商 / 模型 | 坐标空间 | 单位 | 原点 | 备注 |
|---|---|---|---|---|
| **Anthropic Claude**（`computer_*` / `computer_toolset_*`） | **截图像素** | 整数 px | 全屏截图左上角 | 缩放/zoom **不改变**坐标系；换算责任在调用方 |
| **OpenAI**（`computer_use_preview`） | **`display_width/height` 像素** | 整数 px | 截图左上角 | 必须让 `display_*` 与实际截图尺寸一致 |
| **Google Gemini**（`computer_use`） | **0~1000 归一化虚拟网格** | 归一化 | 左上角 (0,0)，右下 (999,999) | 需 `x/1000*screenW` 反归一化 |
| **Qwen2.5-VL**（grounding） | **绝对像素 @ smart_resize 后的图** | 整数 px | 缩放后图左上角 | 需 `image_grid_thw*14` 还原，最易错 |
| **Qwen2.5-VL**（agent function-call） | **0~1000 归一化**（官方 `rescale_coordinates` 除以 999） | 归一化 | 同上 | 与 grounding 任务**不是同一套** |
| **Qwen3-VL** | **0~1000 相对归一化** | 归一化 | 同上 | 分母 1000（部分 cookbook 写 999） |
| **UI-TARS / 1.5**（Qwen2.5-VL 底座） | **绝对像素 @ smart_resize 后的图**（factor 28） | 整数 px | 缩放后图左上角 | 官方 `README_coordinates.md` 给了还原公式 |

**2. 官方关于"降精度"的共同结论（可作为我们方案背书）**
Anthropic 官方最佳实践博客明确写：超过 API 图像上限的图会被**服务端静默降采样**，模型在"降级后的图"上给坐标，调用方却按原分辨率执行 → **系统性整体偏移**，这是高分辨率下点击不准的**首要原因**。官方给的基线是 **1280×720**（Opus 4.7 用 1080p），下限 960×540，**避免原生分辨率**。附一条硬数字：4K 屏上的 16px 复选框压到 1280×720 只剩约 **5px**，"目标越小误差越大"。Qwen/UI-TARS 侧的等价结论是：**必须按模型实际看到的（smart_resize 后）尺寸还原坐标**，否则偏移是几百像素级别。

**3. 我给出的最小动作协议要点**（详见 §5）
- **单一坐标空间：截图坐标系（frame pixel space）**，整数 px，左上原点，模型只跟这个空间打交道；
- **物理像素只在执行器里出现**，由 `frame` 块携带一个仿射变换 `origin_px + scale`，把模型坐标映射到 Windows 虚拟桌面物理像素，**DPI/多屏问题在抓图层一次性解决**；
- **12 个动作**：`click / double_click / right_click / middle_click / move / drag / type / key / scroll / wait / screenshot / zoom`；
- **`zoom` 必须 `source:"native"`**（回源原生分辨率重抓），从已降采样的帧里裁剪只会得到糊图；
- **越界不静默 clamp**，返回 `coordinate_out_of_bounds` 并要求重新定位；只在 ≤2px 边缘容差内夹到边界；
- **每个动作可带 `expect`**（效果断言），把"执行后验证"内建进协议；
- **危险动作带 `requires_approval`**，与 OpenAI `pending_safety_checks` / Anthropic human-in-the-loop 对齐。

---

## 1. 各家动作协议逐项

### 1.1 Anthropic Claude computer use

**工具形态的两次演进** **[一手]**

来自 Anthropic 官方 quickstart 仓库 README（`anthropics/anthropic-quickstarts/computer-use-demo/README.md`）：

> "The most recent shape, `computer_toolset_20260801`, declares the computer tool as a *toolset*: a single nameless entry in `tools[]` that declares one member tool per computer action. The model then calls each action as its own tool — `tool_use.name` is the member name (`left_click`, `screenshot`, ...), the block carries `toolset_name: "computer"`, and the input is that action's parameters with no `action` discriminator. Every `tool_result` answering a member call carries the same `toolset_name`, member calls in a turn run sequentially and stop on the first failure, and the toolset requires no `anthropic-beta` header. Earlier dated versions of the tool (API types `computer_20241022` through `computer_20251124`, the sidebar's `computer_use_*` entries) keep the single `computer` tool with an `action` parameter and their respective beta headers."

| 版本 | 形态 | 备注 |
|---|---|---|
| `computer_20241022` | 单个 `computer` 工具 + `action` 字符串 + `display_width_px/display_height_px/display_number` | beta header `computer-use-2024-10-22` |
| `computer_20250124` | 同上 | beta header `computer-use-2025-01-24`；**无 zoom** |
| `computer_20251124` | 同上 | beta header `computer-use-2025-11-24`；**新增 zoom**（`enable_zoom: true`） |
| `computer_toolset_20260801` | 一个无名 toolset，每个 action 一个 member tool，`toolset_name:"computer"`，无 `action` 字段，**不需要 beta header，也不需要 display size** | 坐标仍是截图像素，"the screenshots you send establish that frame" |

**动作清单** **[一手，Anthropic 官方参考实现 `computer_use/tools/computer.py` 的 `Action` Literal]**

```python
Action = Literal[
    "screenshot", "left_click", "double_click", "triple_click", "right_click",
    "middle_click", "mouse_move", "left_click_drag", "scroll", "type", "key",
    "hold_key", "left_mouse_down", "left_mouse_up", "cursor_position",
    "read_clipboard", "write_clipboard", "wait", "zoom",
]
```

注意：`read_clipboard` / `write_clipboard` 是**本地参考实现自加**的，hosted toolset 的 17 个 member 是其子集（hosted 集合 = 上表去掉 read/write_clipboard）。

**逐项参数（hosted toolset 的 17 个 member）** **[二手，来自官方文档页经搜索引擎的完整摘录 + 一手参考实现 schema]**

| 动作 | 参数 | 语义 |
|---|---|---|
| `left_click` | `coordinate?: [x,y]`、`text?: "shift"\|"ctrl"\|"alt"\|"super"\|"ctrl+shift"` | 省略 coordinate = 点击当前光标处；`text` 是按住���修饰键 |
| `right_click` / `middle_click` / `double_click` / `triple_click` | 同上 | |
| `left_click_drag` | `start_coordinate: [x,y]`、`coordinate: [x,y]`、`text?` | 从 start 按下拖到 coordinate 释放 |
| `mouse_move` | `coordinate: [x,y]` | 悬停 |
| `left_mouse_down` / `left_mouse_up` | `{}` | 在当前（可先用 mouse_move 移动的）位置按下/释放，用于手动拖拽或长按 |
| `cursor_position` | `{}` | 返回当前光标 [x,y] 文本 |
| `scroll` | `scroll_direction: up\|down\|left\|right`、`scroll_amount`（滚轮格数）、`coordinate?`、`text?` | |
| `type` | `text: string` | 在当前焦点逐字输入 |
| `key` | `text`（单键或 `+` 组合，如 `"Return"`、`"ctrl+s"`、`"alt+Tab"`）、`repeat?`（1~100） | |
| `hold_key` | `text`、`duration`（秒，≤300） | |
| `wait` | `duration`（秒，≤300） | |
| `screenshot` | `{}` | 返回全屏截图 |
| `zoom` | `region: [x1,y1,x2,y1..y2]` | 返回该区域的**全分辨率**裁剪图 |

**坐标格式：截图像素，不是归一化** **[一手 + 二手]**

官方文档的关键语义（官方页 `platform.claude.com/docs/en/agents-and-tools/tool-use/computer-use-tool`，该页在我这边被地域屏蔽，引用经搜索引擎逐字摘录）：

> 坐标（`coordinate` / `start_coordinate` / `region`，以及 `cursor_position` 返回值）都活在**你返回的那张全屏截图的像素空间**里，原点左上角。
> "Zooming doesn't change this: after `zoom`, Claude still expresses coordinates in full-screenshot space, never relative to the zoomed image. If you downscale before returning a screenshot, scale Claude's coordinates back up before applying them to the real display."

Anthropic 官方参考实现里的坐标缩放代码 **[一手]**（`computer_use/tools/computer.py`）：

```python
def _scale_to_screen(self, coord):        # 模型空间 -> 屏幕空间
    x, y = coord
    sx = round(x * self.screen_w / self.sent_w)
    sy = round(y * self.screen_h / self.sent_h)
    return (max(0, min(sx, self.screen_w - 1)), max(0, min(sy, self.screen_h - 1)))
```

> ⚠️ 这行 `max(0, min(..., w-1))` 就是**静默 clamp**——多屏负坐标场景会踩坑（见 §4.2）。

**官方关于分辨率/降精度的原文** **[一手]**（`claude.com/blog/best-practices-for-computer-and-browser-use-with-claude`）

> "When you send a screenshot to Claude's Computer Use API, the model sees it and returns click coordinates in the `display_width_px` / `display_height_px` coordinate space you specified. But there's an important constraint: **the API has internal processing limits on image size. Images that exceed these limits get downscaled before the model sees them**, which means the model is clicking based on a degraded version of the image while your harness expects coordinates aligned to the original resolution."

> "When the coordinate space and the model's perceived image don't match, the model's predicted clicks land on a display scale different from the image it's actually seeing. **This is the primary cause of click inaccuracy at high resolutions.** The fix is straightforward: always downscale your screenshots to fit within these limits before sending them to the API. We consistently observe significant accuracy degradation when images exceed the limits, and this single change is worth more than almost any other optimization."

限额（4.6 家族 / Opus 4.7）：

| | 长边 | 总像素 |
|---|---|---|
| Claude 4.6 家族（Opus 4.6 / Sonnet 4.6 / Haiku 4.5） | **1568 px** | **1.15 MP** |
| Opus 4.7 | **2576 px** | **3.75 MP** |

> "Start with **1280x720**. This is a safe, practical default for most use cases. It uses about 80% of the pixel budget, stays well within both the long edge and total pixel limits, and is a standard resolution that models have seen during training."
> "If you are using Opus 4.7, we recommend starting with **1080p**."
> 应避免：**原生分辨率**（"the most common cause of poor click accuracy"）、**低于 960×540**、4.6 家族上的 **1920×1080 及以上**（会被静默降采样）。

坐标回缩原文 + 代码：

```
scale_x = screen_w / display_w
scale_y = screen_h / display_h
screen_x = int(api_returned_x * scale_x)
screen_y = int(api_returned_y * scale_y)
```

> "if you forget to scale or `display_width_px` / `display_height_px` don't match the actual dimensions of the image you sent, **every click will be consistently offset**."

小目标与 zoom 的原文：

> "Click accuracy degrades as targets get smaller. Large and medium UI elements (buttons, input fields, and standard menu items) are reliable across all resolutions within the safe zone. The challenge is with small and tiny targets, like checkboxes, system tray icons, dropdown arrows, small toggle switches, and tree view expand/collapse buttons."
> "Screenshots from 4K+ displays that get compressed down to 720p lose significant detail (for example, **a 16px checkbox at 3840x2160 native becomes roughly 5px at 1280x720 display resolution**, which makes the target much smaller and therefore more difficult to hit)."
> "**Use zoom for dense UIs.** Claude 4.6 and 4.7 models support a zoom capability that lets the model inspect specific screen regions at higher resolution before clicking."
> "**Make targets larger.** If you control the UI being automated, increasing the size of click targets (even modestly) has a disproportionate impact on reliability. This might mean using a lower system DPI, zooming in the browser, or adjusting UI scaling settings."
> "**Use keyboard alternatives for tiny targets.** For very small elements, such as system tray icons or tiny checkboxes, keyboard shortcuts or tab-based navigation can be more reliable than clicking."

**两个"试过但没用"的结论**（对我们设计有直接价值）**[一手]**：

> "**Breaking the image into smaller tiles**: Splitting a screenshot into quadrants or regions and sending them separately did not improve click accuracy."
> "**Overlaying a grid pattern with coordinates**: Adding a visual coordinate grid to screenshots to help the model localize targets did not produce reliable gains."

> ⚠️ 注意这与 §02 文档里"crop 精定位"的结论**不矛盾**：官方这里否定的是"把整屏切成几块分别发"，我们做的是"先整屏粗定位、再围绕候选点裁一个小 crop 高分辨率重定位"，是串行两级，不是并行分块。

**内容顺序** **[一手]**：

> "When constructing your messages content array, **place the text instruction before the image** … This lets the model know what it's looking for as it processes the screenshot, which improves click accuracy."

**其他官方工程建议** **[一手，博客全文]**
- 思维强度（adaptive thinking）：`low` 性价比最高；**不推荐 `max`**（"provides no accuracy benefit over high"）。
- 批处理工具 `computer_batch`：一次调用跑多个子动作；**仅用于"子动作之间互不依赖视觉结果"**的场景；探索性导航/错误恢复**不要**批。
- 提示词要明确写："After each step, take a screenshot and carefully evaluate if you have achieved the right outcome… Only when you confirm a step was executed correctly should you move on." **[二手，官方 prompting 章节]**
- 下拉框/滚动条对模型很难用鼠标操作 → **优先让模型用键盘快捷键** **[二手]**
- 系统提示词开销：computer use beta 增加 **466~499 tokens** **[二手，官方 limitations]**

**Anthropic 官方给的分辨率协议（旧参考实现，仍值得抄）** **[一手，`computer-use-demo/README.md`]**

> "We do not recommend sending screenshots in resolutions above XGA/WXGA to avoid issues related to image resizing. Relying on the image resizing behavior in the API will result in **lower model accuracy and slower performance** than implementing scaling in your tools directly. The `computer` tool implementation in this project demonstrates how to scale both images and coordinates from higher resolutions to the suggested resolutions."
> "When implementing computer use yourself, we recommend using **XGA resolution (1024x768)**:
> - For higher resolutions: Scale the image down to XGA and let the model interact with this scaled version, then map the coordinates back to the original resolution proportionally.
> - For lower resolutions or smaller devices: **Add black padding** around the display area until it reaches 1024x768."

**Anthropic 官方参考实现的 resize 算法**（可直接抄）**[一手，`computer_use/image.py`]**

> "The API's vision encoder tiles images into 28x28 patches and caps both the long edge (1568 px) and the total tile count (1568). If we send an image that violates either, the server resizes it *again* before the model sees it, and then the model emits click coordinates in a space we never observed, producing **systematic click drift (~14% on a 16:10 MacBook screen)**."

```python
def target_image_size(width, height, *, px_per_token=28, max_edge_px=1568, max_tokens=1568):
    """Largest (w, h) preserving aspect ratio with long-edge <= max_edge_px
    and tile-count <= max_tokens. Returns the input unchanged if already valid."""
    ...
```

> 👉 这条 **~14% 系统性漂移** 是我找到的最直接的量化证据：**"服务端降采样"不是精度损失，是坐标空间错配**。

**Prompting 章节（官方）** **[二手]**
- 给简单、明确、逐步的任务；给每步显式指令。
- 每步之后截图并**显式验证**结果再继续。
- 对可重复交互，**给示例截图 + 示例 tool call**。
- 下拉框/滚动条用键盘替代。
- 文字指令放在图像**前面**。

**"Claude 3.5 在 4K 上的错误率数据"——查证结果** **[事实]**
Anthropic 2024-10-22 的官方发布文 [Developing a computer use model](https://www.anthropic.com/news/developing-computer-use) **没有给出 4K 下的点击错误率数字**。原文只给了：
> "On one evaluation created to test developers' attempts to have models use computers, [OSWorld](https://os-world.github.io/), Claude currently gets **14.9%**. That's nowhere near human-level skill (which is generally **70-75%**), but it's far higher than the **7.7%** obtained by the next-best AI model in the same category."

同一篇还给了一条对我们非常关键的定性结论：
> "Claude looks at screenshots of what is visible to the user, then **counts how many pixels** vertically or horizontally it needs to move a cursor in order to click in the correct place. **Training Claude to count pixels accurately was critical.**"

> ⚠️ **明确纠正一个常见误传**：不存在"Claude 3.5 在 4K 上错误率 XX%"的官方数字。官方唯一的量化替代物是 4.6 家族的 **"16px 复选框 → 5px"** 例子，和官方参考实现注释里的 **"~14% 漂移"**。请勿在后续文档里编造百分比。

---

### 1.2 OpenAI computer use

**工具声明** **[一手，`openai-python` 的 `ComputerUsePreviewToolParam`]**

```python
class ComputerUsePreviewToolParam(TypedDict, total=False):
    display_height: Required[int]        # The height of the computer display.
    display_width: Required[int]         # The width of the computer display.
    environment: Required[Literal["windows", "mac", "linux", "ubuntu", "browser"]]
    type: Required[Literal["computer_use_preview"]]
```

**动作联合类型** **[一手，`openai-python/src/openai/types/responses/computer_action.py`]**

| `type` | 字段 |
|---|---|
| `click` | `button: left\|right\|wheel\|back\|forward`、`x: int`、`y: int`、`keys?: list[str]` |
| `double_click` | `x: int`、`y: int`、`keys?` |
| `drag` | `path: list[{x:int, y:int}]`（可多点路径）、`keys?` |
| `keypress` | `keys: list[str]` |
| `move` | `x: int`、`y: int`、`keys?` |
| `screenshot` | 无 |
| `scroll` | `scroll_x: int`、`scroll_y: int`、`x: int`、`y: int`、`keys?` |
| `type` | `text: str` |
| `wait` | 无 |

（`ComputerAction` = 以上 9 个的 `Annotated[Union[...], PropertyInfo(discriminator="type")]`。）

**坐标空间**：**`display_width` × `display_height` 声明的像素空间**，整数像素，原点左上。**没有 zoom 动作**（`computer_use_preview` 里没有 `zoom`；新一代 GA `computer` 工具的 SDK driver 才有 `zoom(region=[left,top,right,bottom])`）。

**官方循环定义**（Microsoft Foundry 文档完整镜像了 OpenAI 的这套工具）**[一手，`learn.microsoft.com/en-us/azure/foundry/agents/how-to/tools/computer-use`]**

> 1. Send a request to the model that includes a call to the computer use tool, the display size, and the environment. You can also include a screenshot of the initial state of the environment in the first API request.
> 2. Receive a response from the model. If the response has action items, those items contain suggested actions …
> 3. Execute the action by using your application code on your computer or browser environment.
> 4. After executing the action, capture the updated state of the environment as a screenshot.
> 5. Send a new request with the updated state as a `tool_call_output`, and repeat this loop until the model stops requesting actions or you decide to stop.

官方示例代码里的循环骨架 **[一手]**：

```python
max_iterations = 10
while True:
    if iteration >= max_iterations: break
    computer_calls = [item for item in response.output if item.type == "computer_call"]
    if not computer_calls: break
    computer_call = computer_calls[0]
    action, call_id = computer_call.action, computer_call.call_id

    # Never execute an action with pending safety checks without user approval.
    safety_checks = computer_call.pending_safety_checks or []
    if safety_checks:
        for check in safety_checks: print(f"Safety check: {check.code}: {check.message}")
        if input("Approve this action? Type yes to continue: ").lower() != "yes": break

    screenshot_info, current_state = handle_computer_action_and_take_screenshot(action, current_state, screenshots)
    response = openai.responses.create(
        previous_response_id=response.id,
        input=[{"call_id": call_id, "type": "computer_call_output",
                "acknowledged_safety_checks": safety_checks,
                "output": {"type": "computer_screenshot", "image_url": screenshot_info["url"]}}],
        truncation="auto",
    )
```

**安全检查（关键工程件）** **[一手]**
三类：`malicious_instructions`（截图内容含对抗指令）、`irrelevant_domain`（当前域名与任务无关）、`sensitive_domain`（当前在敏感域名）。
模型把它们放在 `computer_call.pending_safety_checks`，**必须**在下一个 `computer_call_output` 里用 `acknowledged_safety_checks` 原样回传才能继续；官方措辞：
> "In all cases where `pending_safety_checks` are returned, hand over actions to the end user to confirm proper model behavior and accuracy."

**官方 Troubleshooting 表**（直接可抄成我们 agent 的自检表）**[一手]**

| 现象 | 原因 | 官方给的解法 |
|---|---|---|
| 响应里没有 `computer_call` | 工具没配 / 不是 CUA 模型 / 提示词不要求 UI 交互 | 确认 `computer_use_preview` 工具 + `computer-use-preview` 模型 + 提示词要求 UI 动作 |
| 循环在迭代上限停 | 任务需要更多轮，或应用没执行动作 | 提高迭代上限；确认每轮都执行了动作并回传新截图 |
| 收到 `pending_safety_checks` | 检测到安全风险 | 暂停，人工复核，`acknowledged_safety_checks` 回传后继续 |
| **模型反复 "take a screenshot" 但没进展** | 截图没更新 / 质量低 / 没拍到相关 UI | 每个动作后发新截图；必要时提高 detail；确保截图包含相关 UI |
| **动作在错误坐标上执行** | **截图与实际显示分辨率不匹配** | **确保 `display_width`/`display_height` 与实际屏幕分辨率一致** |
| 模型幻觉 UI 元素 | 截图质量太低或轮次间 UI 变了 | 用更高分辨率截图；动作后立即发新截图；缩短动作到截图的延迟 |

**关于分辨率与坐标精度的官方说明** **[二手]**
- OpenAI 官方指南（`developers.openai.com/api/docs/guides/tools-computer-use`）：
  > "Keep images in memory and use `detail: "original"` to preserve resolution. **If you downscale a screenshot, map the model's coordinates back to the environment's coordinate space before executing actions.**"
- SDK driver 文档（`.../tools-computer-use-sdk-drivers`）：
  > "For `zoom`, region is `[left, top, right, bottom]`. Coordinates must be nonnegative safe integers with positive width and height. The driver owns cropping and bounds checks. **All later coordinates still refer to the full visible area.**"
- 社区共识（多家一致，**非官方**）：桌面建议 **1440×900 或 1600×900**；"bigger is not better — 你为像素付了 token，然后在下采样时把它们扔掉"；`detail` 必须 `"original"`，`"high"`/`"low"` 都会降低点击精度。

**preview 与 GA 的差异** **[二手]**：preview 工具**每次请求只返回一个动作**，且必须 `truncation: "auto"`；新一代 GA 工具改为 `actions[]` 批量、去掉 `display_*` / `environment`（改由截图推断尺寸）。

---

### 1.3 Google Gemini computer use

**坐标格式：0~1000 归一化虚拟网格** **[二手，官方文档多语言页经搜索引擎逐字摘录]**

官方 `ai.google.dev/gemini-api/docs/computer-use` 给的反归一化代码：

```java
int denormalizeX(int x, int screenWidth)  { return (int)(x / 1000.0 * screenWidth); }
int denormalizeY(int y, int screenHeight){ return (int)(y / 1000.0 * screenHeight); }
```

Python / Go 版本同理。要点：
- **分母是 1000，不是 999**（尽管取值范围是 0~999）。
- **所有坐标字段都要反归一化**：`x` / `y`，拖拽还有 `start_x`/`start_y`/`end_x`/`end_y`（或 `destination_x`/`destination_y`）。
- 必须用**你实际喂给模型的那张图/viewport 的宽高**，用错 `SCREEN_WIDTH`/`SCREEN_HEIGHT` 会"silently misplace every click"。
- 官方示例用 `SCREEN_WIDTH = 1440, SCREEN_HEIGHT = 900` + Playwright `viewport` 对齐。

**动作集**

*遗留模型 `gemini-2.5-computer-use-preview-10-2025`（13 个预定义动作）* **[二手]**：

| 动作 | 作用 |
|---|---|
| `open_web_browser` | 打开浏览器 |
| `wait_5_seconds` | 暂停 5 秒 |
| `go_back` / `go_forward` | 后退 / 前进 |
| `search` | 在地址栏搜索 |
| `navigate` | 跳 URL |
| `click_at` | 在归一化 (x,y) 点击 |
| `hover_at` | 悬停 |
| `type_text_at` | 在位置输入文字 |
| `key_combination` | 按组合键（如 `"Control+Enter"`） |
| `scroll_document` | 滚动页面 |
| `scroll_at` | 在某坐标滚动 |
| `drag_and_drop` | 拖放 |

*新模型（Gemini 3.x Flash 系列）* **[二手]**：`click`、`double_click`、`right_click`、`middle_click`、`move`、`type`、`press_key`、`hotkey`、`scroll`、`drag_and_drop`、`navigate`、`wait`。
> 坐标处理**完全一致**（0~1000 归一化），只是 action 命名现代化了。

**两个重要的实现细节** **[二手，社区实现 + 官方语义]**
- `type_text_at` 是**复合动作**，客户端本地展开为：click → Ctrl+A → Backspace → type → (可选 Enter) → wait → screenshot。参数 `press_enter` 与 `clear_before_typing` 默认 `true`。
- `key_combination` 形如 `"Control+Enter"`，客户端拆分并归一化同义词（`CTRL`→`Control`、`CMD`→`Meta`）。

**安全模型** **[二手]**：官方安全系统提示词有 `RULE 1: USER_CONFIRMATION` / `RULE 2: ACTUATE`；`USER_CONFIRMATION` 覆盖 CAPTCHA、金融交易、发消息、敏感数据等类别。

**其它事实** **[二手]**
- Google 明确说该模型 **"optimized for browsers"**，不是桌面 OS 级控制；桌面需自定义 function（如 `open_app`、`long_press_at`、`go_home`）。
- 有新的 **Interactions API**（`client.interactions.create` + `previous_interaction_id`）在服务端管状态，0~1000 反归一化规则不变。

---

### 1.4 Qwen2.5-VL / Qwen3-VL 原生 grounding

> ⚠️ **最容易踩坑的一节：同一个 Qwen2.5-VL，"grounding 任务"和"agent function-call 任务"用的是两套完全不同的坐标体系。**

#### (a) Qwen2.5-VL **grounding**（bbox_2d / point）——绝对像素 @ smart_resize 后的图

**`smart_resize` 原文** **[一手，`qwen-vl-utils/src/qwen_vl_utils/vision_process.py`]**

```python
MAX_RATIO = 200
SPATIAL_MERGE_SIZE = 2
IMAGE_MIN_TOKEN_NUM = 4
IMAGE_MAX_TOKEN_NUM = 16384

def smart_resize(height, width, factor, min_pixels=None, max_pixels=None):
    """
    1. Both dimensions (height and width) are divisible by 'factor'.
    2. The total number of pixels is within the range ['min_pixels', 'max_pixels'].
    3. The aspect ratio of the image is maintained as closely as possible.
    """
    max_pixels = max_pixels if max_pixels is not None else (IMAGE_MAX_TOKEN_NUM * factor ** 2)
    min_pixels = min_pixels if min_pixels is not None else (IMAGE_MIN_TOKEN_NUM * factor ** 2)
    if max(height, width) / min(height, width) > MAX_RATIO:
        raise ValueError("absolute aspect ratio must be smaller than %d" % MAX_RATIO)
    h_bar = max(factor, round_by_factor(height, factor))
    w_bar = max(factor, round_by_factor(width, factor))
    if h_bar * w_bar > max_pixels:
        beta = math.sqrt((height * width) / max_pixels)
        h_bar = floor_by_factor(height / beta, factor)
        w_bar = floor_by_factor(width / beta, factor)
    elif h_bar * w_bar < min_pixels:
        beta = math.sqrt(min_pixels / (height * width))
        h_bar = ceil_by_factor(height * beta, factor)
        w_bar = ceil_by_factor(width / beta, factor)
    return h_bar, w_bar
```

- `factor = patch_size(14) × SPATIAL_MERGE_SIZE(2) = 28`。
- `fetch_image` 里：先 `smart_resize`，再 `image.resize((resized_width, resized_height))`。

**还原公式** **[一手（UI-TARS 官方 `README_coordinates.md`）+ 二手（Qwen cookbook）]**

```python
# 模型实际看到的尺寸
input_width  = inputs['image_grid_thw'][0][2] * 14   # 网格每格 14px
input_height = inputs['image_grid_thw'][0][1] * 14

# 模型输出（绝对像素，在 resized 空间） -> 原图像素
abs_x = int(model_x / input_width  * orig_width)
abs_y = int(model_y / input_height * orig_height)
```

UI-TARS 官方 `README_coordinates.md` 给的是同构版本 **[一手]**：

```python
new_height, new_width = smart_resize(height, width)
new_coordinate = (int(model_output_width  / new_width  * width),
                  int(model_output_height / new_height * height))
```

其中 `IMAGE_FACTOR = 28`、`MIN_PIXELS = 100*28*28`、`MAX_PIXELS = 16384*28*28`、`MAX_RATIO = 200`。

**输出格式** **[一手，Qwen 官方博客]**

```json
[
  {"bbox_2d": [341, 258, 397, 360], "label": "motorcyclist", "sub_label": "not wearing helmat"},
  {"bbox_2d": [212, 332, 274, 448], "label": "motorcyclist", "sub_label": "not wearing helmat"}
]
```

点格式（XML 变体）：`<ref x1="546" y1="1204" alt="point description">Description</ref>`。
旧训练格式：`<|object_ref_start|>一只狗<|object_ref_end|><|box_start|>(432,991),(1111,2077)<|box_end|>`。

**Qwen2.5-VL 的 bbox 偏移问题** **[二手，QwenLM issue #830/#866/#1030]**：默认 `max_pixels` 会在内部静默降采样，导致框整体偏移；解法是调大 `max_pixels`、API 侧开 `vl_high_resolution_images=True`、或自己预缩放。**根因与 Anthropic 完全同构：模型在 A 空间给坐标，你按 B 空间执行。**

#### (b) Qwen2.5-VL **agent / function-call**（computer_use cookbook）——0~1000 归一化

**[二手，DeepWiki 对 `cookbooks/computer_use.ipynb` 的还原]**

- 模型输出 0~1000 归一化网格；官方工具函数 `rescale_coordinates`：
  ```python
  def rescale_coordinates(point, width, height):
      point = [round(point[0]/999*width), round(point[1]/999*height)]
      return point
  ```
  （注意这里除以 **999**，与 grounding 的绝对像素是两回事。）
- tool call 形如：`{"name": "mobile_use", "arguments": {"action": "click", "coordinate": [447, 81]}}`。
- 官方 cookbook **没有** crop/zoom 工具（DeepWiki 明确指出 "does not describe a crop/zoom tool in the cookbook"）。

#### (c) Qwen3-VL —— 回归 0~1000 相对归一化

**[二手]**
- 官方 grounding cookbook `cookbooks/2d_grounding.ipynb` 文档的是 **0~1000**。
- 但官方部分 cookbook 工具函数（`ocr.ipynb`、`mobile_agent.ipynb`）除以 **999**；EvalScope 的 Ref-Adv-s 基准明确命名为 `norm_1000_xyxy`。
- **建议全项目统一除以 1000**（与 Gemini 一致），别混用。
- 输出格式：
  ```json
  [{"bbox_2d": [x1,y1,x2,y2], "label": "cat"}]
  [{"point_2d": [500,300], "label": "search bar"}]
  ```
  OCR 变体用 `"text_content"` 代替 `"label"`。
- 官方 2D 定位 prompt：
  ```
  Locate every object that matches the description "{ref_sentence}" in the image. Report bbox coordinates in JSON format.
  ```
- 已知问题：**极端宽高比下 bbox 偏移更明显**（issue #2020/#1871）；**反归一化必须用模型实际看到的那张图的尺寸**，不是原图（vLLM 论坛 #2648）。
- 模型输出常被 ```json 围栏包住，解析器要剥围栏。

**Qwen 侧"降精度"结论小结**：官方没有"把图裁小再点"的建议（cookbook 无 zoom 工具），但**通过调大 `max_pixels` 避免静默降采样**是官方 issue 里反复出现的正解——与 Anthropic 的"要么自己在工具层缩放、要么接受服务端缩放+坐标回缩"是同一个道理。

---

### 1.5 UI-TARS / UI-TARS-1.5 / UI-TARS-2

#### UI-TARS 1.0（arXiv:2501.12326）**[一手，论文全文]**

- 统一动作空间：把跨平台语义等价的动作抽象成公共操作集 `click / type / scroll / drag`，再按平台加可选动作。
- **两个终止动作**：`Finished()`（任务完成）、`CallUser()`（需要人工介入，如登录/认证）。
- Grounding 训练目标原文：
  > "The model is tasked with outputting **relative coordinates normalized to the dimensions of the screen**, ensuring consistency across devices with varying resolutions."
- 坐标取 bbox 四角平均 → 单点（框中心）。
- **反思训练（reflection tuning）**原文：
  > "In realistic online deployments, agents often encounter situations where they get stuck due to a lack of self-reflection and error correction capabilities. For example, an agent might repeatedly click on an unresponsive button or attempt invalid operations due to misinterpretation of the interface."
  > "if the previous step intended to add a webpage to bookmarks but mistakenly clicked the close button, the next step should involve **reopening the recently closed webpage to re-attempt clicking the bookmark button**."

#### UI-TARS-1.5（arXiv:2504.12679）**[一手，论文 Table 7 + Appendix A.2]**

| 环境 | 动作 | 定义 |
|---|---|---|
| Desktop | `Click(x,y)` | 左键单击元素 |
| | `Input(value,x,y)` | 在某元素输入字符串 |
| | `Scroll(direction,x,y)` | 四方向滚动，x/y 为起点 |
| | `LeftClickDouble(x,y)` | 双击 |
| | `RightClickSingle(x,y)` | 右键单击 |
| | `Drag(x1,y1,x2,y2)` | 按住左键拖到目标 |
| | `HotKey(value)` | 按组合键 |
| | `Hover(x,y)` | 悬停 |
| | `Finish()` | 结束任务 |
| Mobile | `Tap(x,y)` / `Input(value,x,y)` / `Swipe(x1,y1,x2,y2)` / `LongPress(duration,x,y)` / `PressHome()` / `PressBack()` / `Finish()` | |

**思考-动作输出结构（GUI-Net agent 官方 prompt 原文）** **[一手]**：

```
You are an assistant trained to navigate the computer / mobile screen.
Given a task instruction, a screen observation, and an action history sequence,
output the next action and wait for the next observation.

Here is the action space: {action_space}

Format your response as
Thought: <your reasoning process>
Action: <the next action>

Format the action as a JSON object with the following keys:
{"action": "ACTION_TYPE", "value": "element", "position": [x, y]}

You can output multiple actions at once, and use JSON array to represent multiple actions.
If value or position is not applicable, set it as 'None'.
Position might be [[x1,y1],[x2,y2]] if the action requires a start and end position.
**Position represents the relative coordinates on the screenshot and should be scaled to a range of 0-1.**
```

桌面动作空间列举（同节原文）：`CLICK / INPUT / SCROLL / LEFT_CLICK_DOUBLE / RIGHT_CLICK_SINGLE / DRAG / HOT_KEY / WAIT / FINISH`；`WAIT` 语义是 "Wait for 5 seconds, and take a screenshot to check for any changes."

#### UI-TARS-1.5-7B / UI-TARS-2 的 model card prompt（COMPUTER_USE 模板）**[二手，官方 model card]**

```
click(start_box='<|box_start|>(x1,y1)<|box_end|>')
left_double(start_box='<|box_start|>(x1,y1)<|box_end|>')
right_single(start_box='<|box_start|>(x1,y1)<|box_end|>')
drag(start_box='<|box_start|>(x1,y1)<|box_end|>', end_box='<|box_start|>(x3,y3)<|box_end|>')
hotkey(key='')
type(content='')  # Use "\n" at the end of content to submit
scroll(start_box='<|box_start|>(x1,y1)<|box_end|>', direction='down or up or right or left')
wait()  # Sleep for 5s and take a screenshot to check for any changes.
call_user()  # Submit the task and call the user when unsolvable / help needed
```

- 思考-动作结构同 GUI-Net：`Thought: ...` / `Action: ...`。
- `GROUNDING` 模板只输出 `Action`，无 `Thought`。
- **无 zoom 动作**（1.5/2 的 action space 里没有 zoom；也没有 scroll 之外的精细视口操作）。

**坐标处理** **[一手，`README_coordinates.md`]**：模型输出 `click(start_box='(197,525)')` → `smart_resize(height,width)` → `int(model_x / new_width * width)` 还原到原图。
- 模型类型决定语义：`qwen25vl` → 绝对坐标 / smart_resize 尺寸；`doubao` → 相对坐标 / factor；最终统一归一化到 `[0,1]` 再乘屏幕尺寸。
- 解析器把单点 `[x,y]` 展开成退化框 `[x,y,x,y]`，click/hover/scroll 统一取**框中心**。
- 完整动作解析参考 OSWorld 的 `mm_agents/uitars_agent.py`；官方 PyPI 包 `ui-tars` 提供 `parse_action_to_structure_output` + `parsing_response_to_pyautogui_code`（用 AST 安全解析，不用正则硬啃）。

**分辨率设置** **[二手，UI-TARS-desktop 源码分析]**
- `MAX_PIXELS_V1_0 = 1080 × 1920` → 1.0 会把截图压进 1080p 盒。
- `MAX_PIXELS_V1_5 = 16384 × 16384` → 1.5 **基本不降采样**，只保证 factor-28 对齐与 min/max 边界。
- 部署指南设 `PAYLOAD_LIMIT = 8000000`（大图会被请求层拒），建议压缩/裁剪/只截活动窗口。
- grounding 空间常声明为 **1920×1080**，且**明确警告不要在下采样后再喂给 grounding 模型**（会重新引入偏移）。

---

## 2. 官方给出的精度建议（摘录 + URL）

| 厂商 | 关键句（节选） | URL |
|---|---|---|
| Anthropic | "Images that exceed these limits get downscaled before the model sees them … **This is the primary cause of click inaccuracy at high resolutions.**" | [Best practices blog](https://claude.com/blog/best-practices-for-computer-and-browser-use-with-claude) |
| Anthropic | "Start with **1280x720** … It uses about 80% of the pixel budget" | 同上 |
| Anthropic | "avoid **native resolution**（the most common cause of poor click accuracy）"、"avoid **below 960x540**"、"4.6 家族避免 1920×1080 及以上" | 同上 |
| Anthropic | "a **16px checkbox at 3840x2160 native becomes roughly 5px at 1280x720**" | 同上 |
| Anthropic | "**Use zoom for dense UIs**" / "**Make targets larger**" / "**Use keyboard alternatives for tiny targets**" | 同上 |
| Anthropic | "place the **text instruction before the image**" | 同上 |
| Anthropic | "We do not recommend sending screenshots in resolutions above **XGA/WXGA** … implement scaling in your tools directly" | [computer-use-demo README](https://github.com/anthropics/anthropic-quickstarts/blob/main/computer-use-demo/README.md) |
| Anthropic | "systematic click drift (**~14% on a 16:10 MacBook screen**)"（服务端二次缩放所致） | [computer-use-best-practices `image.py`](https://github.com/anthropics/anthropic-quickstarts/blob/main/computer-use-best-practices/computer_use/image.py) |
| OpenAI | "Keep images in memory and use `detail: "original"` … **If you downscale a screenshot, map the model's coordinates back to the environment's coordinate space**" | [OpenAI computer use guide](https://developers.openai.com/api/docs/guides/tools-computer-use) |
| OpenAI | "**All later coordinates still refer to the full visible area.**"（zoom 之后） | [SDK drivers doc](https://developers.openai.com/api/docs/guides/tools-computer-use-sdk-drivers) |
| OpenAI | "**Actions execute on wrong coordinates** → Screen resolution mismatch … ensure `display_width`/`display_height` match your actual screen resolution" | [Microsoft Foundry computer use](https://learn.microsoft.com/en-us/azure/foundry/agents/how-to/tools/computer-use) |
| Google | `denormalizeX(x, screenWidth) = (int)(x / 1000.0 * screenWidth)` | [Gemini computer use](https://ai.google.dev/gemini-api/docs/computer-use) |
| Qwen | `smart_resize` 后的绝对像素；`abs = int(model_x / input_width * orig_width)` | [qwen-vl-utils](https://github.com/QwenLM/Qwen2.5-VL/blob/main/qwen-vl-utils/src/qwen_vl_utils/vision_process.py) · [cookbooks](https://github.com/QwenLM/Qwen2.5-VL/tree/main/cookbooks) |
| Qwen | 调大 `max_pixels` / `vl_high_resolution_images=True` 避免静默降采样导致的 bbox 偏移 | [Qwen3-VL issue #830](https://github.com/QwenLM/Qwen3-VL/issues/830) · [#866](https://github.com/QwenLM/Qwen3-VL/issues/866) |
| UI-TARS | `int(model_x / new_width * width)`，`new_*` 来自 `smart_resize` | [README_coordinates.md](https://github.com/bytedance/UI-TARS/blob/main/README_coordinates.md) |
| UI-TARS | V1.0 压到 1080p 盒；V1.5 不降采样；"不要在下采样后再喂 grounding 模型" | [UI-TARS-desktop 架构分析](https://xuqi2024.github.io/2026/09/10/2026-09-10-ui-tars-desktop-multimodal-agent-stack-architecture-deep-dive/) |

**跨厂商共识（可以直接写进我们的设计文档）**
1. **自己缩放，别让服务端缩**（Anthropic/UI-TARS 明确；OpenAI 隐含）。
2. **坐标一律按"模型看到的那张图"解释**，换算责任在 harness，不在模型。
3. **目标越小越难点** → 优先放大目标 / 降 DPI / 用键盘 / 用 zoom 二次定位。
4. **文字指令放图像前面。**
5. **缩放后必须把坐标映射回执行空间**，否则是"每次都偏同样多"的系统性误差。

---

## 3. 循环范式

### 3.1 一个健壮循环的阶段（业界共识 + 官方要求）

```
[0] 初始化 / 预检
      · DPI 感知、显示器枚举、截图后端可用性、权限（Windows: 无 UAC 弹窗；macOS: 屏幕录制+辅助功能）
      · 声明 display 尺寸 / 坐标契约，写进系统提示词
        ↓
[1] 计划 (plan)
      · 长任务先拆步；必要时用"规划模型"产出步骤，执行模型只管机械点击（Anthropic: orchestrator + sub-agent）
        ↓
[2] 观察 (observe)
      · 截屏（物理像素，不缩放）→ 按预算缩放 → 发给模型
      · 文字指令放在图像前
        ↓
[3] 定位 (ground)
      · 一级：整屏 0~1000 归一化粗定位（Qwen3-VL / Gemini 原生空间）
      · 二级：围绕候选点裁 crop（物理分辨率）再定位 → 落到物理像素
      · 可选三级：UIA 元素矩形兜底
        ↓
[4] 动作 (act)
      · 执行 1 个（或安全的若干个）动作
      · 执行前做安全门（见 3.4）
        ↓
[5] 效果验证 (verify)  ← 最容易被忽略、也最能决定成败
      · 见 3.2
        ↓
[6] 反思 / 纠错 (reflect & recover)
      · 失败时：换 crop 尺寸、换定位级别、改用键盘、回退到上一步、换策略
      · UI-TARS 的 reflection tuning 就是把这套东西训进模型
        ↓
[7] 停止条件 (stop)
      · 见 3.5
```

**官方要求的最小循环**（OpenAI/Microsoft）：

```
screenshot → action → screenshot → ... （每轮一张新截图；previous_response_id 串联；每轮回传 tool_call_output）
```

**Anthropic 官方要求的"每步验证"**（prompting 章节）**[二手]**：
> "After each step, take a screenshot and carefully evaluate if you have achieved the right outcome… Only when you confirm a step was executed correctly should you move on."

### 3.2 效果验证怎么做

**三种手段，按可靠性排序**：

| 手段 | 做什么 | 能检出什么 | 代价 | 来源 |
|---|---|---|---|---|
| **UIA / 可访问性树** | 查目标控件的 `enabled/checked/value`、是否存在、矩形是否变化 | 状态型错误（勾没勾上、按钮是否 disabled、文本框内容对不对） | 1.6~1.9 s 延迟；**静默退化成返回 0 节点**是最危险的失败模式（见 `02-coordinate-systems.md` D.2） | 02 文档 + UIA 通用实践 |
| **OCR / 文本比对** | 对目标区域做 OCR，比对期望字符串 | 文本型错误（"已保存" vs "保存失败"） | 慢；中文/小字易错 | 通用 |
| **像素差分（frame diff）** | 动作前后截图做区域差分 | "画面到底变了没有" | 最快最通用；**变化 ≠ 成功** | [settled-computer](https://pypi.org/project/settled-computer/) |

**像素差分的最佳实践** **[二手，settled-computer（Windows 10/11 实测）]**：
- 动作前抓 **baseline** 帧（快速反应不会漏）。
- 动作后轮询：首次立即 + 100 ms 内 15 ms 节奏 + 之后 20 Hz；用 **8×8 像素块 + uint32 精确比较**，1080p 静态帧约 1 ms。
- 判定顺序：**reaction（有任何变化）→ stillness（`quiet_time` 内不再变化）→ 返回**。
- **"无可见变化" ≠ "失败"**：可能本来就已聚焦/已置位。必须把这个区分写进模型看到的提示词。
- 动画/视频区域：约 **1.5 s 残余 bail** 并给出该区域的 bbox；连续两次 bail 后自动忽略该区域（带过期时间）。
- 闪烁的光标会被判成"有反应"→ 文本框里"无变化"判定不可靠。

**Anthropic 官方"无进展"诊断** **[一手，Microsoft 官方 troubleshooting 同源]**
> "The model repeats 'take a screenshot' without making progress." → 截图没更新 / 质量低 / 没拍到相关 UI → 每动作后发新截图 + 必要时提 detail + 确保截图包含相关 UI。

**社区沉淀的失败模式清单** **[二手，`mjmirza/patterns` computer-use.md]**：
- "Agent clicks repeatedly in roughly the right region but never lands" → 工具声明里的 display 尺寸与实际截图分辨率已经不一致。
- "Agent reports success while the next screenshot shows an error / the form still open" → "Claude sometimes assumes outcomes of its actions without explicitly checking their results." → **要求在推进前对下一张截图做显式验证**。
- "20+ turns and still doesn't finish" → 小错累积、无检查点。

**反模式**：只做"动作 → 截图 → 交给模型"而**不做任何机器侧断言**。arXiv 2606.31270 指出：
> "**redundant action loops are the most common failure mode identified by the LLM**, arising when the agent fails to recognize its action had no effect."

**结论（可落地的做法）**：
> **在 agent 侧做"确定性验证"，不把验证全交给模型。** 至少实现：(a) 帧差分判"有没有反应"；(b) 目标区域 OCR/文本比对判"是不是想要的结果"；(c) UIA 判"状态对不对"。三者的 OR 判据与停止条件联动。
> `veltiq/groundtruth` 的 `verify-loop.md` 给了一个好范式：**纯确定性门控 + 轮次计数（保证循环不会永续）+ 达到 max 轮就放行停止**（"never trapped"），并且**刻意不让 LLM 当裁判**（避免假阳性）。

### 3.3 循环卡死检测

**四类检测器**（可全部实现）：

1. **帧无变化检测**：连续 N 次动作后，帧差分变化率 < 阈值（settled-computer 用 1% 像素变化；gptme `wait_for_change` 用 1% 阈值、0.5 s 轮询）→ 判定"动作无效"。
   > 坑：光标闪烁、动画、时钟会造成假阳性 → 需要"区域"级别的差分 + 连续多次。
2. **重复动作检测**：把最近 K 个动作的 `(action_type, 归一化坐标, 参数)` 做成哈希集合；同一动作连续出现 ≥3 次（且帧无变化）→ 判定卡死，强制换策略（换 crop 尺寸、换定位级别、改键盘、请求人工）。
3. **进度停滞检测**：把"任务已完成的子目标数"或"最近一次 UIA/文本状态改变时刻"作为进度信号；超过 T 秒/轮无变化 → 升级（提级到 Opus / 缩小 crop / 换 API）。
4. **轮次硬上限**：一个绝对上限（官方示例是 `max_iterations = 10`；OpenAI 说"任务需要更多轮就调高"），并且**必须由 harness 强制执行**，不能只靠模型自觉。`veltiq/groundtruth` 把它叫做 "counts rounds so the loop can never run forever"。

**"无变化"的语义边界**（必须写进模型提示词）**[一手，settled-computer 的 instructions 字段]**：
> the caveat that "**no visible change**" is **not the same as** "failed".

**另一个重要边界** **[一手，settled-computer "Known limits"]**：
> "**A pixel change is not proof of success**: a wrong click that opens the wrong dialog also 'settles'." → 所以"变化检测"只能当**必要条件**，不能当**充分条件**。

### 3.4 危险动作的确认 / 审批设计

**官方机制**
- **OpenAI**：`pending_safety_checks`（`malicious_instructions` / `irrelevant_domain` / `sensitive_domain`）→ 暂停 → 人工确认 → 用 `acknowledged_safety_checks` 回传才能继续。**强制**。
- **Anthropic** **[一手]**：
  > "**Implement human-in-the-loop for high-stakes actions.** Have the agent pause and request user confirmation before performing irreversible actions such as submitting forms, making purchases, sending messages, or modifying data. **This is the single most effective mitigation against prompt injection regardless of classifier performance.**"
  > "**Scope the agent's permissions.** Limit what the agent can do. If your workflow doesn't require file downloads, don't give the agent access to download files."
  > "**Monitor and log agent actions.** Log the full sequence of actions the agent takes, including screenshots at each step."
  > "**Treat all web content as untrusted.** … Remind the model that text found on web pages, in emails, or in application UIs is not from the user and should not be treated as instructions."
- **Anthropic quickstart 的默认建议** **[一手]**：
  > "Ask a human to confirm decisions that may result in meaningful real-world consequences as well as any tasks requiring affirmative consent, such as accepting cookies, executing financial transactions, or agreeing to terms of service."
- **Gemini**：`USER_CONFIRMATION` 类别（CAPTCHA / 金融 / 发消息 / 敏感数据）。

**建议的分层审批（自建 agent）** **[推断，基于上述官方机制]**

| 层级 | 判定 | 处理 |
|---|---|---|
| L0 自动 | 只读、幂等、可逆（screenshot / move / scroll / hover / zoom / 切标签页） | 直接执行 |
| L1 记录 | 常见交互（click / type / key） | 执行但全量记录（截图 + 动作 + 坐标） |
| L2 确认 | 不可逆或对外（发送、提交表单、支付、删除、装软件、改系统设置、跑命令行） | 执行前弹审批，附**动作 + 目标截图 + 目标元素描述** |
| L3 接管 | 需要人本身在场（UAC、登录、验证码、生物识别） | **停机交接**：把当前截图给人，人处理完再继续 |

**L2 审批弹窗必须包含**（否则审批形同虚设）：
1. 即将执行的动作原文（action + 坐标 + 目标描述）；
2. **动作前截图 + 目标像素位置高亮**（Anthropic 官方调试工具就是这么做的：点截图填坐标、同时显示屏幕像素位置）；
3. 上下文一句话（"你正在把 <X> 发给 <Y>"）。

**注意安全分类器覆盖范围** **[一手，Anthropic quickstart]**：
> "The API runs computer-use-specific safety classifiers (including prompt-injection detection on screenshot content) **only when the request declares the Anthropic-defined computer tool**. With the explicit schema, your request is on the generic safety path and you must rely on your own prompt-injection mitigations."

→ **自建 agent 用显式 schema，就等于放弃了服务端分类器**，必须自己做"屏幕内容不可信"的提示词隔离 + 权限收缩 + 审计日志。

### 3.5 上下文 / 历史管理

**规模事实** **[一手，Anthropic 博客]**：
> "screenshots accumulate fast. Every action generates a new image, and each image consumes roughly **1,000–1,800 tokens** depending on resolution. After accounting for the system prompt, tool definitions, and text content, **a 200k context window can fill up in well under 100 screenshots**."

**三层组合** **[一手，Anthropic 博客 + quickstart README]**

1. **缓存断点**：API 共 4 个 breakpoint。
   - 1 个放在系统提示词 / 工具定义尾部（会话内稳定）。
   - 最多 3 个放在**最近的 tool_result** 上，每轮推进，并清掉上一轮旧标记。
   - 理由："Spreading breakpoints across recent positions gives you graceful degradation."
2. **缓存友好的滚动缓冲（批量剪枝）**：
   - 朴素做法"每轮丢一张"会**每轮都改前缀 → 每轮 cache miss**。
   - 正确做法：**保留最近 `keep_n` 张全分辨率；总数超过 `keep_n + interval` 时，一次性把最老的 `interval` 张替换成占位符**。两次剪枝之间前缀字节级不变。
   - 官方推荐默认：`keep_n = 3`，`interval = 25`。
   - quickstart 实现里默认值：`image_prune_min=3`、`image_prune_interval=40`（策略名 `interval`；另有 `simple`（缓存不友好）和 `none`）。
3. **摘要压缩（compaction）**：
   - 官方给了完整的 `COMPACT_PROMPT` 模板（8 个小节）：
     `USER INSTRUCTIONS`（**逐字保留，防止任务漂移**）、`TASK TEMPLATE`、`CONSTRAINTS AND RULES`、`ACTIONS TAKEN`、**`ERRORS AND FIXES`（防止重试已失败的路径）**、`PROGRESS TRACKING`、`CURRENT STATE`、`NEXT STEP`。
   - 服务端版：`context_management={"edits":[{"type":"compact_20260112","trigger":{"type":"input_tokens","value":150_000},"instructions":COMPACT_PROMPT}]}`，beta `compact-2026-01-12`。
   - **剪枝 vs 摘要的取舍**（官方原文要点）：
     - 摘要是**更贵**的事件（多一次完整 API 往返 + 输出 token + 下一轮冷缓存）；
     - 摘要**保留更多信息**（保留文本叙事），剪枝直接丢图；
     - "**Pruning is only worth it if it actually prevents a summarization**"——已缓存的图再带一轮几乎免费，丢它反而要重缓存后面所有内容；
     - **长 + 图多**的任务：剪枝赢；**短**任务：`image_prune_strategy="none"` 最便宜，摘要当罕见的安全网。

**三种历史策略的取舍（我们的选型建议）** **[推断，基于上述]**

| 策略 | 优点 | 缺点 | 建议 |
|---|---|---|---|
| **全量堆叠截图** | 视觉上下文最全 | 100 张就爆 200k；token 成本线性增长 | ❌ 只适合极短任务 |
| **纯文本摘要** | 便宜 | 丢失视觉细节，模型无法回看"当时屏幕长啥样" | ⚠️ 只能配合保留少量近期截图 |
| **裁剪保留（推荐）** | 保留最近 N 张全分辨率 + 老图占位 + 周期性摘要 | 需要维护缓存前缀稳定 | ✅ 主策略 |

**额外要点**
- Anthropic 建议 **200k 上下文 + 保留 3 张全分辨率图**，其余占位。
- 摘要里的 `ERRORS AND FIXES` 一节是**防重复失败**的关键，别省。
- 官方 quickstart 的 `LatencyBook`/自适应 `max_wait` 也说明：**等待时间应被学习/测量，而不是猜**。

### 3.6 失败重试与回退策略

**回退阶梯**（建议实现，标注每级的依据）：

| 级别 | 场景 | 动作 | 依据 |
|---|---|---|---|
| L0 | 坐标越界 / 坐标空间不匹配 | 报错并**要求重新定位**（不要 clamp） | Anthropic: "every click will be consistently offset"；clamp 会掩盖 bug |
| L1 | 点击没反应 | 重新截图 → 换 crop 尺寸（512→1024）→ 重新定位 | 官方"crop 精定位"链 |
| L2 | 定位到但点不中（目标太小） | 提高截图 DPI / 降系统缩放 / 改用键盘 Tab+Enter | Anthropic: "Make targets larger" / "Use keyboard alternatives" |
| L3 | 同一动作重复失败 | 换定位级别（crop → UIA 元素矩形） | 02 文档: "靠 crop 解决看不清，靠 UIA 解决点不准" |
| L4 | 仍失败 | 升级模型（Opus / advisor）或切 API | Anthropic: "orchestrator + sub-agent" |
| L5 | 任务不可解（登录/验证码/授权） | `CallUser()` / 停机交接 | UI-TARS 终止动作 `CallUser()`；Gemini `USER_CONFIRMATION` |

**批处理的边界** **[一手，Anthropic 博客]**：
> "The risk is compounding error, if action 2 depends on visual state that action 1 changed, and action 1 misses, the rest of the batch operates on stale assumptions and the agent can drift without ever seeing a screenshot of the actual state."
> "We'd avoid them in exploratory navigation, error-recovery sequences, or any workflow where 'if action 1 fails I need to re-plan' is a real state."

→ **批处理只用于"子动作互不依赖视觉结果"的场景**（填多个表单字段、连续快捷键、滚动后点已知目标）。Anthropic 的 toolset 形态原生支持**一个 turn 多个 member call**，并在第一个失败时用 `is_error: true` + `"Not executed: …"` 终止后续——这正是我们要的批处理语义。

---

## 4. Windows 特有

### 4.1 UAC / Secure Desktop

- UAC 弹窗在 **Secure Desktop**（winlogon 拥有）上，普通完整性级别的进程（pyautogui/mss）**截不到图**（黑屏）也**点不到**。只有 SYSTEM 进程能访问。
  - 现象与官方诊断：[Microsoft Copilot Studio 故障排除](https://learn.microsoft.com/en-us/troubleshoot/power-platform/copilot-studio/actions/computer-use-screenshot-error) — "`FailedToTakeScreenshot`"，原因之一是 "UAC prompt displayed"，建议"RDP 会话启动时避免 UAC 弹窗"、不要把 UAC 设为"始终通知"。
  - 技术机制：[Stack Overflow](https://stackoverflow.com/questions/74970034/detecting-capturing-uac-prompt-in-windows) — `pyautogui`/`locateOnScreen` 报 `pywintypes.error: (1400, 'GetClassName', 'Invalid window handle.')`。
- 提权窗口（High integrity）同理：标准用户进程受 **UIPI** 限制**不能向高完整性窗口发送输入**，但**截图可能仍然成功** → 表现为"点了没反应"（最阴险的一种失败）。
  - [codenote.net 分析](https://codenote.net/en/posts/windows-admin-elevation-blocks-chatgpt-computer-use-uipi/)
- **Workaround**：
  1. 正规做法：agent 遇到 UAC / 提权窗口 → **检测到后停机交接给人**（这是 UI-TARS `CallUser()` / Gemini `USER_CONFIRMATION` 的标准用法），不要试图绕过。
  2. 想"看得见" UAC：只能签 `uiAccess="true"` 的可执行文件并放在 `C:\Program Files`（屏幕阅读器级别的特权）——需要代码签名证书，工程成本高。
  3. 降低 UAC 提示行为（不推荐，安全风险）。
  4. **别用 SSH/服务账户跑 GUI agent**：没有交互式登录会话时截图全黑（`PyAutoGUI.screenshot throws OSError: screen grab failed`），workaround 是用 `schtasks` 以登录用户身份起计划任务。
  - 另注：[Desktop Control MCP](https://lobehub.com/ar/mcp/acarioti4-desktop-control-mcp) 直接写明 "Cannot interact with UAC prompts (secure desktop)."

### 4.2 DPI 缩放（物理 vs 逻辑像素）

**pyautogui 的四函数错配表**（社区经典结论）**[二手]**：

| 函数 | 返回/使用什么空间 |
|---|---|
| `pyautogui.size()` | **逻辑**分辨率（1920×1080 @125% → 1536×864） |
| `pyautogui.screenshot()` | **物理**像素（1920×1080 或 4K@200%） |
| `locateOnScreen()` | 截图（物理）空间 |
| `moveTo()` / `click()` | 按**逻辑**空间解释 |

→ "clicks at exactly the wrong spot"；200% 时点到目标位置的 1/4。

**正解**（与 `02-coordinate-systems.md` 的 `PHYS-PX-v1` 一致）：
1. 进程启动第一行调用 `SetProcessDpiAwarenessContext(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2)`（或 Win8.1+ 的 `SetProcessDpiAwareness(2)` / 旧版 `SetProcessDPIAware()`）。**必须在 import pyautogui 之前，且每进程只能设一次**。
2. 或者在 exe 里嵌 DPI awareness manifest（trycua/cua 提交 `b286d9c` 就是这么修 125/150/200% 的）。
3. 截图后端换 **`mss`**（原生支持高 DPI，永远返回真实物理像素）。
4. 绝不要把 `locateOnScreen()` 的结果直接喂给 `click()`。
5. 自检断言：`GetSystemMetrics(SM_CXSCREEN)` 必须返回物理值（2560），返回 1707 说明 DPI 感知没生效 → **整条坐标链不可信，直接中止**。

> ⚠️ pyautogui ≥ 0.9.53 **对越界点击静默 clamp**（不报 IndexError），clamp 到 `max(0, coord)` 会**破坏多屏负坐标**。[theneuralbase 归纳](https://theneuralbase.com/computer-use/errors/computer-use-mouse-click-out-of-bounds-error/)

### 4.3 多显示器

- 虚拟桌面坐标系：原点是主屏左上角 (0,0)，**左侧/上方的屏有负坐标**（左侧 1920px 屏 → x 起点 -1920）。
  - `SM_XVIRTUALSCREEN=76`、`SM_YVIRTUALSCREEN=77`、`SM_CXVIRTUALSCREEN=78`、`SM_CYVIRTUALSCREEN=79`。
- **正确发鼠标事件**：`SendInput` + `MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK`，并归一化到**整个虚拟桌面**：
  ```
  nx = round((x_global - virt_left)   / virt_width  * 65535)
  ny = round((y_global - virt_top)    / virt_height * 65535)
  ```
  - ⚠️ 归一化要**除以 (dimension - 1)** 而不是 dimension，否则最后一个像素永远点不到（`pixelactions-core/virtualdesk.rs` 明确指出这个 off-by-one）。
  - 用 64 位算术（宽桌面上分子能到 4.3e9）。
  - 只按主屏归一化（enigo 的做法）会把 0 送到副屏最左边缘。
- **已知坑**：
  - `MAKELPARAM` 把坐标打包成**无符号 16 位** → 负的窗口局部坐标回绕成 ~65500，点击落到右下角而不是左上（trycua/cua #1979 / PR #1980、#1983）。
  - **`-32000` 哨兵值**：UIA 元素 bounds 在副屏上返回 `-32000`（Win32 里 `GetWindowRect` 对最小化窗口的哨兵值），点击"成功"但落空（trycua/cua #2015）。根因疑似在 `IsIconic(hwnd)` 短路或拿哨兵 HWND 调 `GetWindowRect`。
  - **Indirect Display Driver (IDD) 虚拟显示器**（华为、向日葵/OrayIddDriver）会让 D3D/WGC 抓到 **0×0** 截图并返回 -32000 坐标（trycua/cua #2218）。workaround：跳过 IDD adapter，或改用 UIA `Invoke`/`set_value`/`key`（不需要坐标）。
  - **VLM 与负坐标天然冲突**：模型只能输出正数，映射回负偏移时符号处理必然出错 → 我们的协议规定**模型坐标永远在 frame 内（正数）**，负坐标只存在于执行层。
- **拼接图的"空洞"**：屏高不一致时虚拟包围盒里有大片无映射区域，drag 事件落进去会丢失。解法：按屏切片发送，或建立每屏变换矩阵 `X_global = X_offset_i + u·W_i`。
- **推荐**：**每块屏独立走一遍 截屏→定位→点击**；跨屏目标才拼图。

### 4.4 Electron / GPU 加速窗口黑屏

- GDI `BitBlt` / `PrintWindow` 对 **GPU 加速窗口（Electron / Chromium / WebView2 / 游戏 / 播放器）** 常返回**黑帧**或**陈旧缓存帧**。
- 解法优先级：
  1. **Windows Graphics Capture (WGC)**（或 DXGI Desktop Duplication）；按窗口标题模式自动切换 WGC（`COMPUTER_CONTROL_MCP_WGC_PATTERNS="obs,discord,game,steam,..."`）。
  2. `PrintWindow` 用 `PW_RENDERFULLCONTENT` 全内容标志，**别用 `BitBlt`**。
  3. 单窗口黑 → **改抓整屏**。
  4. 最后一招：Electron `--disable-gpu`（会改变应用行为，通常不推荐）。
  - 来源：[computer-control-mcp](https://pypi.org/project/computer-control-mcp/)、[win32-ui-automation skill](https://skillsmp.com/es/creators/nzh63/ame/agents-skills-win32-ui-automation)

### 4.5 输入法（IME）

- `pyautogui.write()/typewrite()` 走 **SendInput 键码**，**绕过 IME**，直接输中文 → 乱码/拼音/无输出；候选窗无法控制。
- 解法优先级：
  1. **剪贴板 + Ctrl+V**（`pyperclip.copy(text)` → `hotkey('ctrl','v')`）：跨平台最稳；必须**先备份剪贴板、保证目标窗口有焦点、最后恢复剪贴板**（`finally` 里恢复）。
  2. 系统命令（macOS `pbcopy`/AppleScript；Windows 可用 Win32 剪贴板 API）。
  3. Windows TSF/ITF IME API（精确但复杂，仅 Windows）。
  4. UIA `set_value`（不依赖键盘，最稳但只对支持 ValuePattern 的控件）。
- **焦点问题**：输入会发给当前焦点窗口；agent 自己的悬浮窗/通知都可能抢焦点 → 先显式激活目标窗口（`getWindowsWithTitle(...)[0].activate()`）再等焦点稳定。
- **IME 被 agent 弄坏的已知 bug**：`anthropics/claude-code` issue #97444 —— Windows 11 + 微软拼音下，computer use 会话之后候选窗不再出现（`ctfmon.exe`/`ChsIME.exe`/`TextInputHost.exe` 进程还在）。workaround：`Stop-Process -Name TextInputHost -Force`（Windows 会自动重启它）。

### 4.6 其它 Windows 坑

- **Wayland 之外，Windows 也有 session 问题**：服务/计划任务下运行 → 截图黑。必须跑在**交互式登录会话**里。
- **pyautogui failsafe**：默认把鼠标甩到左上角即中止——**保留它**，它是最后一道刹车（settled-computer 也强调"keep the pyautogui fail-safe on"）。
- **IME/键盘布局**：`pyautogui.write()` 发的是**物理键位**，非美式布局（Dvorak/AZERTY）会错；Anthropic 官方 macOS 实现改用 `CGEventKeyboardSetUnicodeString` 逐字符发 Unicode。Windows 上对应做法是发 `KEYEVENTF_UNICODE`。
- **超时**：UIA 查询 1.6~1.9 s 延迟，超时要放宽到 ~5 s，并检查"是否返回 0 节点"（静默退化到像素是最危险的失败模式）。

---

## 5. 最小动作协议（可直接实现）

### 5.1 设计原则

1. **模型侧只有一个坐标空间：截图坐标系（frame pixel space）**
   - 整数像素，原点左上，x→右，y→下，合法范围 `0 ≤ x < frame.width`、`0 ≤ y < frame.height`。
   - **不用浮点**（避免亚像素歧义）、**不用归一化**（避免每家分母 999/1000 的歧义）。
   - 模型**永远看不到**物理像素、逻辑像素、DPI、显示器编号。
2. **物理像素只存在于执行器**，由 `frame` 携带一个仿射变换把模型坐标映射到 Windows 虚拟桌面物理像素。
   → DPI / 多屏 / 负坐标问题在**抓图层一次性解决**，协议层零感知。
3. **越界不静默 clamp**：返回结构化错误 `coordinate_out_of_bounds`，要求重新定位。
4. **每个动作可选带 `expect`**：把"执行后验证"内建进协议。
5. **危险动作可选带 `requires_approval`**，与 OpenAI `pending_safety_checks` / Anthropic HITL 对齐。

### 5.2 坐标系定义（规范性）

```
模型空间 (frame space)
    单位：整数像素
    原点：frame 图像左上角
    范围：x ∈ [0, frame.width-1]，y ∈ [0, frame.height-1]
    ↓ 变换（由执行器唯一持有）
执行空间 (physical desktop space)
    单位：物理像素（Windows 虚拟桌面，可为负）
    原点：虚拟桌面左上角（x/y 可为负）
    公式：
        physical_x = frame.origin_px[0] + round(x * frame.scale_x)
        physical_y = frame.origin_px[1] + round(y * frame.scale_y)
```

**约束（执行器必须校验）**
- `frame.scale_x == frame.scale_y`（等比缩放），容差 `1e-6`；不等 → 拒绝该 frame（会产生形变）。
- `frame.origin_px` 是该 frame 在虚拟桌面上的**物理像素原点**（可能为负）。
- **陈旧几何拒绝**：执行前用 `frame.physical_rect` 与当前显示器几何比对，不一致 → 报 `stale_frame`，要求重新截图。
  （对应 trycua 的 "reject stale capture geometry instead of resampling"。）

### 5.3 动作清单与语义

| 动作 | 必需字段 | 可选字段 | 语义 | 单位 |
|---|---|---|---|---|
| `click` | `frame`,`x`,`y` | `modifiers`,`duration_ms` | 左键单击 | frame px |
| `double_click` | `frame`,`x`,`y` | `modifiers` | 左键双击（两次 down/up，间隔 ≥40 ms） | frame px |
| `right_click` | `frame`,`x`,`y` | `modifiers` | 右键 | frame px |
| `middle_click` | `frame`,`x`,`y` | — | 中键 | frame px |
| `move` | `frame`,`x`,`y` | — | 移动光标，不按键（hover） | frame px |
| `drag` | `frame`,`from`,`to` | `duration_ms`（默认 300）,`modifiers` | 按下→移动→释放 | frame px |
| `type` | `text` | `method`(`keystrokes`\|`clipboard`),`interval_ms` | 在当前焦点输入文字 | — |
| `key` | `keys[]` | `repeat`（1..100） | 按组合键 | — |
| `scroll` | `direction` | `x`,`y`（目标点）,`amount`（格数，默认 3）,`modifiers` | 滚动 | frame px |
| `wait` | `duration_ms` | `until`（条件，见 5.5） | 等待 | ms |
| `screenshot` | — | `region`,`full` | 截屏；`full=true` 强制重新抓全屏 | frame px（region） |
| `zoom` | `region` | `source`(`native`\|`frame`) | 区域高分辨率重看 | frame px |

**`zoom` 的 `source` 是本次调研最重要的发现之一**：
- `source:"native"`（**默认**）：**回源**按物理像素重新截取该区域 → 真的更清晰。Anthropic 官方实现就是这么做的（`pyautogui.screenshot()` → 按比例换算 → `crop`）。
- `source:"frame"`：从**已经降采样过的**那张图里裁 → **不会更清晰**。
- 已知 bug：[claude-code #82437] 报告 `zoom` 在高分辨率屏上返回的是**从降采样图插值放大**的糊图，12px 的 UI 文字完全不可读。**根因就是 zoom 复用了已降采样的帧**。→ **协议层必须强制 `native`，并在实现上保证"重新抓图"而不是"裁旧图"。**
- **无论哪种 zoom，后续动作的坐标仍指全屏 frame**（Anthropic/OpenAI 官方都明确这一点）→ 协议里 `zoom` **不改变坐标系**。

### 5.4 JSON Schema（normative）

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "https://dsh.local/schemas/computer-use-action-v1.json",
  "title": "Computer Use Action Protocol v1",
  "description": "最小可实现的 computer-use 动作协议。所有坐标为 frame 像素空间整数坐标，原点左上。",
  "type": "object",
  "required": ["protocol", "action"],
  "additionalProperties": false,
  "properties": {
    "protocol": { "const": "cua.action/1" },
    "action": {
      "type": "string",
      "enum": ["click","double_click","right_click","middle_click","move","drag",
               "type","key","scroll","wait","screenshot","zoom"]
    },
    "id": { "type": "string", "description": "动作 id，用于把执行结果与动作对应" },
    "frame": { "$ref": "#/$defs/frame" },
    "x": { "type": "integer", "minimum": 0 },
    "y": { "type": "integer", "minimum": 0 },
    "from": { "$ref": "#/$defs/point" },
    "to":   { "$ref": "#/$defs/point" },
    "region": {
      "type": "array", "items": { "type": "integer" }, "minItems": 4, "maxItems": 4,
      "description": "[x1, y1, x2, y2]，frame 像素空间，x2>x1 且 y2>y1"
    },
    "text": { "type": "string", "maxLength": 20000 },
    "method": { "enum": ["keystrokes", "clipboard"] },
    "interval_ms": { "type": "integer", "minimum": 0, "maximum": 1000 },
    "keys": {
      "type": "array", "minItems": 1, "maxItems": 8, "items": { "type": "string" },
      "description": "键名数组，组合键按数组顺序，如 [\"ctrl\",\"shift\",\"t\"]"
    },
    "repeat": { "type": "integer", "minimum": 1, "maximum": 100 },
    "direction": { "enum": ["up","down","left","right"] },
    "amount": { "type": "integer", "minimum": 1, "maximum": 1000, "description": "滚轮格数" },
    "duration_ms": { "type": "integer", "minimum": 0, "maximum": 300000 },
    "modifiers": {
      "type": "array", "maxItems": 4, "items": { "enum": ["shift","ctrl","alt","meta"] }
    },
    "source": { "enum": ["native","frame"] },
    "full": { "type": "boolean" },
    "until": { "$ref": "#/$defs/until" },
    "expect": { "$ref": "#/$defs/expect" },
    "requires_approval": { "type": "boolean" },
    "reason": { "type": "string", "maxLength": 500 }
  },
  "allOf": [
    { "if":   { "properties": { "action": { "enum": ["click","double_click","right_click","middle_click","move"] } }, "required": ["action"] },
      "then": { "required": ["frame","x","y"] } },
    { "if":   { "properties": { "action": { "const": "drag" } }, "required": ["action"] },
      "then": { "required": ["frame","from","to"] } },
    { "if":   { "properties": { "action": { "const": "type" } }, "required": ["action"] },
      "then": { "required": ["text"] } },
    { "if":   { "properties": { "action": { "const": "key" } }, "required": ["action"] },
      "then": { "required": ["keys"] } },
    { "if":   { "properties": { "action": { "const": "scroll" } }, "required": ["action"] },
      "then": { "required": ["direction"] } },
    { "if":   { "properties": { "action": { "const": "wait" } }, "required": ["action"] },
      "then": { "required": ["duration_ms"] } },
    { "if":   { "properties": { "action": { "const": "zoom" } }, "required": ["action"] },
      "then": { "required": ["region"] } }
  ],
  "$defs": {
    "point": {
      "type": "array", "items": { "type": "integer" }, "minItems": 2, "maxItems": 2,
      "description": "[x, y]，frame 像素空间"
    },
    "frame": {
      "type": "object",
      "description": "参考帧几何。模型坐标的基准。所有像素均为 frame 像素。",
      "required": ["width","height"],
      "additionalProperties": false,
      "properties": {
        "id":   { "type": "string" },
        "width":  { "type": "integer", "minimum": 1, "maximum": 8192 },
        "height": { "type": "integer", "minimum": 1, "maximum": 8192 },
        "scale_x": { "type": "number", "exclusiveMinimum": 0,
                     "description": "frame 像素 → 物理像素 的 x 比例；必须与 scale_y 相等" },
        "scale_y": { "type": "number", "exclusiveMinimum": 0 },
        "origin_px": { "$ref": "#/$defs/point",
                       "description": "frame 左上角在虚拟桌面物理像素中的坐标，可为负" },
        "physical_rect": {
          "type": "array", "items": { "type": "integer" }, "minItems": 4, "maxItems": 4,
          "description": "[x, y, w, h]，虚拟桌面物理像素；用于陈旧几何检测"
        },
        "safe_area": {
          "type": "array", "items": { "type": "integer" }, "minItems": 4, "maxItems": 4,
          "description": "[x1,y1,x2,y2]，frame 像素空间；点击落在此矩形外视为越界（默认整帧）"
        }
      }
    },
    "until": {
      "type": "object",
      "description": "wait 的结束条件（可选；给出时优先于固定时长）",
      "additionalProperties": false,
      "properties": {
        "kind": { "enum": ["screen_stable","text_present","text_absent","ui_state","elapsed"] },
        "value": { "type": "string" },
        "region": { "type": "array", "items": { "type": "integer" }, "minItems": 4, "maxItems": 4 },
        "timeout_ms": { "type": "integer", "minimum": 0, "maximum": 300000 }
      }
    },
    "expect": {
      "type": "object",
      "description": "执行后的效果断言（确定性校验）。任一断言失败即判定动作失败。",
      "additionalProperties": false,
      "properties": {
        "kind": { "enum": ["screen_changed","text_present","text_absent","ui_state","no_change"] },
        "value": { "type": "string" },
        "region": { "type": "array", "items": { "type": "integer" }, "minItems": 4, "maxItems": 4 },
        "timeout_ms": { "type": "integer", "minimum": 0, "maximum": 60000 }
      }
    }
  }
}
```

**配套的执行结果 schema**（简版，用于把验证结果回灌给模型）：

```json
{
  "protocol": "cua.result/1",
  "id": "<动作 id>",
  "status": "ok" | "error" | "needs_approval" | "rejected",
  "error": {
    "code": "coordinate_out_of_bounds" | "stale_frame" | "no_visible_change" |
             "expectation_failed" | "unsupported_action" | "not_ready" | "timeout" |
             "clipped_by_desktop" | "uac_or_secure_desktop" | "injected_instruction",
    "message": "...",
    "detail": {}
  },
  "screen": { "id": "...", "width": 1280, "height": 720, "changed": true, "diff_ratio": 0.013 },
  "observed": { "text_found": "Saved", "ui_state": {} },
  "screenshot": "<base64 或 file_id，可选>"
}
```

### 5.5 坐标越界与安全区处理规则（规范性）

**规则 1 — 越界不 clamp，要报错。**
```
if x < 0 or x >= frame.width or y < 0 or y >= frame.height:
    return error("coordinate_out_of_bounds")
```
理由：clamp 会把"模型算错了"变成"模型点了个莫名其妙的地方"，而且**每一帧偏得一样**（系统性错误），最难排查。Anthropic 官方也明说 "every click will be consistently offset" 是最常见的错法。

**规则 2 — 边缘容差只有 2 px。**
```
if 0 <= x < frame.width but x >= frame.width - 2:  # 几乎出界
    x = frame.width - 1   # 仅在容差内夹到边界
```
即：**只在距边界 ≤2 px 时夹到边界像素**；真正越界（≥2 px 出界）一律报错。

**规则 3 — 安全区（safe_area）。**
- `frame.safe_area`（可选）默认 = 整帧。
- 点击落在 safe_area 外 → 报 `coordinate_out_of_bounds`，附带 `detail.safe_area` 让模型重新定位。
- **典型 safe_area 收窄场景**：
  - 屏幕上有 agent 自己的悬浮窗/调试面板 → 把那块区域挖掉；
  - 任务栏/托盘的自动隐藏区域；
  - 全屏游戏/视频等"任何像素都可能有意义"的场景 → 不设 safe_area。
- **注意**：safe_area 是**协议层**概念，与"坐标是否越界"是两件事：越界是硬错误，safe_area 是策略性禁区。

**规则 4 — 物理层越界（负坐标 / 超出虚拟桌面）。**
- 允许负坐标（副屏在左/上时合法）。
- 若换算后的物理坐标落在**任何显示器之外** → 报 `clipped_by_desktop`，**不要**自动挪到最近的显示器（那是"点到别的窗口"，可能造成不可逆后果）。
- 执行前校验 `frame.physical_rect` 与当前显示器几何一致，否则报 `stale_frame`。

**规则 5 — 物理层坐标的最终兜底。**
- 用 `SendInput` + `MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK`，归一化到整个虚拟桌面：
  ```
  nx = round((px - virt_left)   / (virt_width  - 1) * 65535)
  ny = round((py - virt_top)    / (virt_height - 1) * 65535)
  ```
  （除以 `dimension - 1`，否则最后一个像素点不到。）
- **不要用 `pyautogui.click()` 作最终执行器**（DPI 行为不可控 + 静默 clamp）。

**规则 6 — 动作前置校验（执行前，不是执行后）。**
1. frame 存在且 `scale_x ≈ scale_y`；
2. frame 未过期（`physical_rect` 与当前几何一致）；
3. 坐标在 frame 内且在 safe_area 内；
4. 若 `requires_approval` 或命中危险动作规则 → 先返回 `needs_approval`，**不要执行**；
5. 若检测到 UAC / secure desktop / 权限对话框 → 返回 `uac_or_secure_desktop`，交人处理。

**规则 7 — 效果验证内建。**
- 动作执行后：按 `expect` 做确定性校验（帧差分 / OCR 文本 / UIA 状态）。
- **无 `expect` 时也要做默认校验**：帧差分（变化率 < 1% 且持续 2 帧 → `no_visible_change`），但**注意"无变化 ≠ 失败"**（可能本来就已聚焦），所以 `no_visible_change` 的语义是"提示模型重新确认"，不是"判定失败"；除非 `expect.kind == "screen_changed"`，那时才算硬失败。

**规则 8 — 停止条件（harness 强制，不靠模型自觉）。**
- 任务完成信号（模型显式 `finish` / 文本结论）。
- 轮次硬上限（参考 OpenAI 示例 `max_iterations = 10`，长任务可调高）。
- 同一动作哈希连续 ≥3 次且帧无变化 → 强制换策略（升级定位级别 / 换键盘 / 请求人工）。
- 审批超时未响应 → 停止并保留轨迹。
- 达到任何上限 → **必须停机并输出轨迹**，不允许"再试一次"无限循环。

### 5.6 各家协议 → 本协议的映射表

| 来源 | 原动作 | → 本协议 | 坐标换算 |
|---|---|---|---|
| Anthropic `left_click(coordinate=[x,y])` | 截图 px | `click{frame,x,y}` | 直接用（前提：frame 就是发给它的那张） |
| Anthropic `left_click_drag(start_coordinate, coordinate)` | | `drag{frame,from,to}` | 直接用 |
| Anthropic `scroll(scroll_direction, scroll_amount, coordinate?)` | | `scroll{direction,amount,x?,y?}` | 直接用 |
| Anthropic `key(text, repeat?)` | `"ctrl+shift+t"` | `key{keys:["ctrl","shift","t"],repeat}` | 拆 `+` |
| Anthropic `zoom(region)` | | `zoom{region,source:"native"}` | 直接用；坐标空间不变 |
| OpenAI `click{button,x,y}` | display px | `click`/`right_click`/`middle_click` | 直接用（frame 宽高 = display_*） |
| OpenAI `drag{path:[{x,y}...]}` | 路径 | `drag{from:path[0],to:path[-1]}` | 只取首尾（多段路径超出最小协议，需要时扩 `path`） |
| OpenAI `scroll{scroll_x,scroll_y,x,y}` | 轴向距离 | `scroll{direction,amount,x,y}` | `scroll_y>0` → `down`；`amount=abs(scroll_y)` |
| OpenAI `keypress{keys:[...]}` | | `key{keys}` | 直接用 |
| Gemini `click_at(x,y)` | 0~1000 | `click{x:round(x/1000*W), y:round(y/1000*H)}` | **必须反归一化，且除以 1000** |
| Gemini `drag_and_drop(start_x,start_y,end_x,end_y)` | 0~1000 | `drag{from,to}` | 四个都反归一化 |
| Gemini `key_combination("Control+Enter")` | | `key{keys:["ctrl","enter"]}` | 拆 `+` + 同义词归一化 |
| Gemini `type_text_at(x,y,text)` | | `type{text}` + 前面单独一个 `click` | 官方语义是复合动作 |
| Qwen2.5-VL grounding `bbox_2d` | resized 绝对 px | `click`（取中心） | `x = int(bx / input_width * orig_width)`，`input_width = image_grid_thw[2]*14` |
| Qwen3-VL `point_2d` / `bbox_2d` | 0~1000 | `click` | `x = round(nx/1000*W)` |
| UI-TARS `click(start_box='(x,y)')` | resized 绝对 px | `click` | `x = int(mx / new_width * width)`，`new_*` 来自 `smart_resize` |
| UI-TARS `drag(start_box,end_box)` | | `drag` | 两点各自换算 |
| UI-TARS `wait()` | 睡 5s 截图 | `wait{duration_ms:5000,until:{kind:"screen_stable"}}` | 语义升级：等到稳定而不是固定睡 |
| UI-TARS `call_user()` | | `status:"needs_approval"` / 交人 | 终止并交接 |

**统一换算后，全项目只有一种坐标进协议**：frame 像素整数。0~1000、绝对像素、display 像素这三套在**适配层**解决，协议层不感知。

---

## 6. 证据边界

**一手核实（直接抓到官方原文/官方代码）**
- Anthropic 官方 quickstart 两个仓库的 README 与 `computer_use/tools/computer.py`、`computer_use/image.py`（动作枚举、参数、坐标缩放代码、resize 算法、~14% 漂移注释）。
- Anthropic 官方博客《Best practices for computer and browser use with Claude》全文（分辨率限额、1280×720、坐标回缩、16px→5px、zoom、上下文三层、COMPACT_PROMPT、安全四原则）。
- Anthropic 官方发布文《Developing a computer use model》（OSWorld 14.9% / 人类 70-75%、"counting pixels"论、flipbook 局限）。
- OpenAI 官方 SDK 类型定义（`computer_action.py` 9 个动作、`computer_use_preview_tool_param.py` 三个参数）。
- Microsoft Foundry 官方文档（完整镜像 OpenAI computer use：循环五步、safety checks、troubleshooting 表、REST/TS/C#/Java/Python 示例）。
- Qwen 官方 `qwen-vl-utils/vision_process.py`（`smart_resize` 全文）；Qwen 官方博客（bbox_2d 输出示例）。
- UI-TARS 官方 `README_coordinates.md`（smart_resize + 还原公式全文）。
- UI-TARS / UI-TARS-1.5 论文全文（arXiv 2501.12326 / 2504.12679）里的动作空间表与 GUI-Net prompt 原文。
- settled-computer PyPI 文档（帧差分/settling 算法、阈值、"no visible change ≠ failed"）。

**二手（官方文档经搜索引擎摘录，或第三方转述）**
- Anthropic 官方文档站 `platform.claude.com/docs/.../computer-use-tool` 的 member 工具表与坐标语义（该站对本机**地域屏蔽**，直接访问返回 "App unavailable in region"；内容经搜索引擎逐字摘录 + 与官方参考实现代码交叉验证一致）。
- Gemini 官方文档的动作清单与反归一化代码（`ai.google.dev` 在本机**网络不可达**，同上）。
- OpenAI 官方指南的 `detail: "original"` 与 zoom 坐标语义（`developers.openai.com` 返回 403，内容经搜索引擎摘录）。
- Qwen3-VL 的 0~1000 约定、cookbook 除以 999 vs 1000 的分歧、issue #830/#866/#2020/#1871。
- UI-TARS-desktop 的 `MAX_PIXELS_V1_0/V1_5` 常量、UI-TARS-2 发布信息。
- Windows 部分（DPI 四函数错配、`-32000` 哨兵、MAKELPARAM 16 位回绕、IDD 虚拟屏、IME/TextInputHost bug、Electron 黑屏与 WGC）——全部来自公开 issue 与工具文档，**未在 Windows 实机复现**。

**明确未证实 / 不存在**
- ❌ **"Claude 3.5 在 4K 上的错误率数据"** —— 官方从未发布该数字。若后续文档出现此类数字，应视为讹传。
- ❌ Anthropic 是否在**任何**官方文档中给出过"把图缩到 ≤ 某分辨率"的**硬性**上限 —— 有（1568px / 1.15MP 的**图像处理**上限），但"推荐分辨率"是 1280×720 这一**经验值**，不是硬上限。
- ⚠️ 官方并未提供 UI-TARS-2 开放权重的确认；open-sourced 的是 UI-TARS-1.5-7B。
- ⚠️ OpenAI `computer-use-preview` 的**分辨率推荐（1440×900 / 1600×900）**目前只见于社区文档，未在官方页面核实到。

---

## 7. 参考链接汇总

**Anthropic**
- Computer use tool（官方文档，地域屏蔽）：https://platform.claude.com/docs/en/agents-and-tools/tool-use/computer-use-tool
- 最佳实践博客：https://claude.com/blog/best-practices-for-computer-and-browser-use-with-claude
- 发布文：https://www.anthropic.com/news/developing-computer-use
- quickstart demo：https://github.com/anthropics/anthropic-quickstarts/tree/main/computer-use-demo
- 最佳实践 quickstart：https://github.com/anthropics/anthropic-quickstarts/tree/main/computer-use-best-practices
- 动作 schema 源码：https://github.com/anthropics/anthropic-quickstarts/blob/main/computer-use-best-practices/computer_use/tools/computer.py
- resize 算法：https://github.com/anthropics/anthropic-quickstarts/blob/main/computer-use-best-practices/computer_use/image.py

**OpenAI**
- Computer use 指南：https://developers.openai.com/api/docs/guides/tools-computer-use
- SDK drivers：https://developers.openai.com/api/docs/guides/tools-computer-use-sdk-drivers
- 动作类型：https://github.com/openai/openai-python/blob/main/src/openai/types/responses/computer_action.py
- 工具参数：https://github.com/openai/openai-python/blob/main/src/openai/types/responses/computer_use_preview_tool_param.py
- 官方循环/安全/排错（Microsoft 镜像）：https://learn.microsoft.com/en-us/azure/foundry/agents/how-to/tools/computer-use

**Google**
- Gemini computer use：https://ai.google.dev/gemini-api/docs/computer-use
- 遗留版：https://ai.google.dev/gemini-api/docs/generate-content/computer-use

**Qwen**
- qwen-vl-utils：https://github.com/QwenLM/Qwen2.5-VL/blob/main/qwen-vl-utils/src/qwen_vl_utils/vision_process.py
- cookbooks：https://github.com/QwenLM/Qwen2.5-VL/tree/main/cookbooks
- Qwen2.5-VL 博客：https://qwenlm.github.io/blog/qwen2.5-vl/
- Qwen3-VL cookbooks：https://github.com/QwenLM/Qwen3-VL/blob/main/cookbooks/2d_grounding.ipynb
- bbox 偏移 issue：https://github.com/QwenLM/Qwen3-VL/issues/830 · https://github.com/QwenLM/Qwen3-VL/issues/866

**UI-TARS**
- 坐标教程：https://github.com/bytedance/UI-TARS/blob/main/README_coordinates.md
- 论文：https://arxiv.org/abs/2501.12326（1.0）· https://arxiv.org/abs/2504.12679（1.5）
- 解析器（OSWorld）：https://github.com/xlang-ai/OSWorld/blob/main/mm_agents/uitars_agent.py
- PyPI ui-tars：https://pypi.org/project/ui-tars/

**Windows / 工程**
- DPI/多屏：https://github.com/trycua/cua/issues/1979 · https://github.com/trycua/cua/issues/2015 · https://github.com/trycua/cua/issues/2218
- UAC secure desktop：https://stackoverflow.com/questions/74970034/detecting-capturing-uac-prompt-in-windows
- Copilot Studio 截图故障：https://learn.microsoft.com/en-us/troubleshoot/power-platform/copilot-studio/actions/computer-use-screenshot-error
- 像素差分 settling：https://pypi.org/project/settled-computer/
- zoom 糊图 bug：https://github.com/anthropics/claude-code/issues/82437

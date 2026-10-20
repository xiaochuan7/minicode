# Anthropic computer use —— zoom 动作源码级调研

> 调研日期：2026-10-19 / 2026-10-20（会话时区 Asia/Shanghai）
> 目的：把 Anthropic computer use 的 `zoom` 动作抠到源码级，用于指导我们自建等价的 "crop 精定位" 实现。
> 全部结论均标注**证据来源**（官方 SDK 源码 / 官方 quickstart 仓库 / 官方测试断言）。**推断**与**我们的设计选择**单独标记。

---

## 0. 素材清单与可复现方法

| 素材 | 获取方式 | 本地路径 |
|---|---|---|
| Anthropic Python SDK **1.15.0** | `pip download anthropic --no-deps` → 解包 wheel | `C:\Users\22197\Desktop\DSH-work\.tmp\anthropic_sdk\extracted\anthropic\` |
| `anthropics/claude-quickstarts` @ `main` | GitHub raw / tree API | `C:\Users\22197\Desktop\DSH-work\.tmp\quickstart\`（computer-use-demo）<br>`C:\Users\22197\Desktop\DSH-work\.tmp\bestpractices\`（computer-use-best-practices） |

- PyPI：<https://pypi.org/project/anthropic/1.15.0/>
- 仓库：<https://github.com/anthropics/claude-quickstarts>
- raw 前缀：`https://raw.githubusercontent.com/anthropics/claude-quickstarts/main/<path>`
- 官方文档站 <https://platform.claude.com/docs/...> 本次**从本机访问被地域屏蔽**（返回 "App unavailable in region"），因此第 1 节的"官方文档"证据全部替换为**官方 SDK 类型/docstring**与**官方 quickstart 代码**——这两者同属 Anthropic 官方一手物，且包含 API schema 原文。

### 0.1 一个必须先纠正的前提偏差

任务描述里的 "**computer-use-demo/image.py**" 与 "**computer-use-demo 下的 best-practices 目录**" 在上游并不存在：

- `computer-use-demo/image/` 是一个**纯 Docker/X11 目录**（`xvfb_startup.sh`、`x11vnc_startup.sh`、`mutter_startup.sh`、`tint2_startup.sh`、`http_server.py`、`index.html`、`.config/tint2/*`），**没有任何 Python 源码**，更没有 `target_image_size()`。
- `image.py`（含 `target_image_size()`）实际在**另一个 quickstart**：`computer-use-best-practices/computer_use/image.py`。
- `computer-use-demo` 与 `computer-use-best-practices` 是**并列的两个目录**，前者是 Linux 容器版（`computer_use_demo/`），后者是 macOS 原生版（`computer_use/`）。

本报告覆盖**两个**目录，因为 zoom 的两套官方实现恰好各在一个里，且行为有实质差异（见 §3）。

---

## 1. SDK 类型定义原文

> 出处：`anthropic-1.15.0-py3-none-any.whl` → `anthropic/types/…`
> 对应 GitHub 源码路径：`anthropics/anthropic-sdk-python/src/anthropic/types/…`

### 1.1 `computer_zoom_input` —— 完整字段定义

文件：`anthropic/types/computer_zoom_input.py`（全文 17 行）
beta 版同源：`anthropic/types/beta/beta_computer_zoom_input.py`

```python
from typing import List

from .._models import BaseModel

__all__ = ["ComputerZoomInput"]


class ComputerZoomInput(BaseModel):
    """Take a screenshot of a rectangular region.

    Region coordinates are in the
    full-screenshot space (not physical display pixels). The crop is scaled up to
    fill the image budget so fine details become legible.
    """

    region: List[int]
    """(x0, y0, x1, y1): The region to capture."""
```

**逐条解读**

| 项 | 原文 | 含义 |
|---|---|---|
| 类型名 | `ComputerZoomInput` / `BetaComputerZoomInput` | 唯一字段就是 `region` |
| 字段 | `region: List[int]` | **恰好 4 个整数**，语义 `(x0, y0, x1, y1)`，是**闭区间端点**（不是 x/y/w/h） |
| 坐标单位 | `Region coordinates are in the full-screenshot space (not physical display pixels)` | region 用的是**你发给模型的那张整屏图的像素空间**，**不是**操作系统的物理显示像素 |
| 裁剪行为 | `The crop is scaled up to fill the image budget so fine details become legible.` | 裁剪块会被**放大到模型的图像预算内**再送回，细节因此变清晰 |

同一段 docstring 也逐字出现在响应侧：
`anthropic/types/computer_zoom_tool_use_block.py:19-25`

```python
    input: ComputerZoomInput
    """Take a screenshot of a rectangular region.

    Region coordinates are in the full-screenshot space (not physical display
    pixels). The crop is scaled up to fill the image budget so fine details become
    legible.
    """

    name: Literal["zoom"]

    toolset_name: Literal["computer"]
```

> `name: Literal["zoom"]` + `toolset_name: Literal["computer"]` 是 toolset 形态下 zoom 的判别式（见 §1.4）。

### 1.2 `computer_member_name` —— 全部成员动作

文件：`anthropic/types/computer_member_name.py:5-23`

```python
ComputerMemberName: TypeAlias = Literal[
    "key",
    "hold_key",
    "type",
    "cursor_position",
    "mouse_move",
    "left_mouse_down",
    "left_mouse_up",
    "left_click",
    "left_click_drag",
    "right_click",
    "middle_click",
    "double_click",
    "triple_click",
    "scroll",
    "wait",
    "screenshot",
    "zoom",
]
```

共 **17** 个成员。`zoom` 排在**最后**，是相对靠后新增的成员。
`COMPUTER_MEMBER_NAME_VALUES`（同文件 `:25-43`）是同顺序的 tuple。

### 1.3 三代 API type 的差别 —— **zoom 是什么时候引入的**

| API type | 单 tool 还是 toolset | 有无 zoom | 开关字段 | 证据 |
|---|---|---|---|---|
| `computer_20241022` | 单 tool（`action` 判别式） | **无** | — | `anthropic/types/beta/beta_tool_computer_use_20241022_param.py`（字段表内无 zoom 相关项） |
| `computer_20250124` | 单 tool | **无** | — | `anthropic/types/beta/beta_tool_computer_use_20250124_param.py`（全文 45 行，无 `enable_zoom`） |
| `computer_20251124` | 单 tool | **有** | `enable_zoom: bool` | `anthropic/types/beta/beta_tool_computer_use_20251124_param.py:42-43` |
| `computer_toolset_20260801` | **toolset**（成员工具） | **有，默认开启** | `configs.zoom.enabled` | `anthropic/types/computer_toolset_20260801_param.py:12-34` |

**结论：zoom 随 `computer_20251124` 引入**，且在单 tool 形态下是** opt-in 开关**：

`anthropic/types/beta/beta_tool_computer_use_20251124_param.py:42-43`
```python
    enable_zoom: bool
    """Whether to enable an action to take a zoomed-in screenshot of the screen."""
```

（非 param 版 `beta_tool_computer_use_20251124.py:41` 为 `enable_zoom: Optional[bool] = None`，即**默认关**。）

对照 `computer_20250124_param.py` 的完整字段集，可确认其**没有** `enable_zoom`：
`display_height_px` / `display_width_px` / `name` / `type` / `allowed_callers` / `cache_control` / `defer_loading` / `display_number` / `input_examples` / `strict`。

quickstart 侧同样如此（`computer-use-demo/computer_use_demo/tools/computer.py`）：

```python
21: Action_20241022 = Literal[
22:     "key",
23:     "type",
24:     "mouse_move",
25:     "left_click",
26:     "left_click_drag",
27:     "right_click",
28:     "middle_click",
29:     "double_click",
30:     "screenshot",
31:     "cursor_position",
32: ]
33:
34: Action_20250124 = (
35:     Action_20241022
36:     | Literal[
37:         "left_mouse_down",
38:         "left_mouse_up",
39:         "scroll",
40:         "hold_key",
41:         "wait",
42:         "triple_click",
43:     ]
44: )
45:
46: Action_20251124 = Action_20250124 | Literal["zoom"]
```

```python
428: class ComputerTool20251124(ComputerTool20250124):
429:     api_type: Literal["computer_20251124"] = "computer_20251124"
430:
431:     @property
432:     def options(self) -> ComputerToolOptions:
433:         return {**super().options, "enable_zoom": True}
```

### 1.4 toolset vs 单 tool：zoom 的调用方式差异

`anthropic/types/computer_toolset_20260801_param.py:12-22`（docstring 原文）：

```python
class ComputerToolset20260801Param(TypedDict, total=False):
    """
    The computer toolset: a single ``tools[]`` entry (carrying no
    ``name``) that declares the computer tool family. The model is
    served the family's tool with any members disabled via ``configs``
    removed from its schema. Every member is enabled by default, zoom
    included. The single-tool options ``display_number`` and
    ``enable_zoom`` are not fields of a toolset entry — it carries only
    ``type``, ``configs``, and ``cache_control``; zoom is controlled
    via ``configs.zoom.enabled``.
    """
```

| 维度 | 单 tool（`computer_20251124`） | toolset（`computer_toolset_20260801`） |
|---|---|---|
| `tools[]` 条目 | `{"name": "computer", "type": "computer_20251124", "display_width_px":…, "display_height_px":…, "enable_zoom": true}` | `{"type": "computer_toolset_20260801"}` —— **无名、无 display 尺寸、无 enable_zoom** |
| 模型如何调用 zoom | 一个叫 `computer` 的工具，input 里带 **`action: "zoom"`** 判别式 + `region` | zoom 是**独立成员工具**：`tool_use.name == "zoom"`，`tool_use.toolset_name == "computer"`，input **没有** `action` 字段，只有 `{"region": [...]}` |
| zoom 开关 | `enable_zoom: bool`（默认 None=关） | `configs.zoom.enabled`（默认开） |
| 返回 | 普通 `tool_result` | `tool_result` **必须**带同样的 `toolset_name: "computer"` |
| 一次多调用 | 不适用 | 一轮可含多个成员调用，**按块顺序串行**，首个失败后其余回答 `"Not executed: an earlier computer action in this turn failed."` |
| beta header | 需要 | **不需要** |

quickstart 的官方类 docstring（`computer-use-demo/computer_use_demo/tools/computer.py:543-566`）：

```python
class ComputerToolset20260801(ComputerTool20251124):
    """The computer toolset (`computer_toolset_20260801`).

    Where earlier dated versions declare a single "computer" tool and receive
    every action through its `action` parameter, the toolset is declared as
    one nameless `tools[]` entry and the model calls each action as its own
    member tool: `tool_use.name` is the member name (`left_click`,
    `screenshot`, ...), the block carries `toolset_name: "computer"`, and the
    input is that action's parameter set with no `action` discriminator.
    Every tool_result answering a member call must carry the same
    `toolset_name` (see `loop._make_api_tool_result`).

    Member semantics match the corresponding actions of
    `computer_20251124`, with three differences:

    * `zoom` is an always-declared member (enabled by default) instead of an
      `enable_zoom` opt-in flag.
    * `key` accepts an optional `repeat` count (1-100).
    * Pointer members (clicks, drags) spell a held modifier chord `text`,
      where `computer_20250124` spelled it `key`.

    A turn may carry several member tool_use blocks; the caller executes them
    sequentially in block order and stops on the first failure (see
    `loop.sampling_loop`). Coordinates stay in screenshot pixels, as before.
    """
```

路由逻辑（`computer-use-demo/computer_use_demo/tools/collection.py:26-31, 45-55`）：

```python
            else:
                # An Anthropic-defined toolset: a nameless tools[] entry that
                # declares a family of member tools. Member calls arrive as
                # tool_use blocks whose name is the member name and whose
                # toolset_name is the family, so they are routed by family.
                self.toolset_map[cast(Any, tool).toolset_name] = tool
...
        if toolset_name is not None:
            # A member tool call: (toolset_name, name) identifies the tool.
            # The member name is the trained action name, so it dispatches as
            # the action parameter of the family's tool implementation.
            toolset = self.toolset_map.get(toolset_name)
            if not toolset:
                return ToolFailure(error=f"Toolset {toolset_name} is invalid")
            try:
                return await toolset(action=name, **tool_input)
```

> 关键：toolset 形态下 **harness 仍把 member name 当作 `action` 传给自己的实现**（`toolset(action=name, **tool_input)`），也就是说成员工具在 harness 内部被折回 action 判别式，两种形态共用同一份执行代码。

`tool_result` 盖章（`computer-use-demo/computer_use_demo/loop.py:342-382`，节选）：

```python
    A tool_result answering a toolset member call must carry the same
    toolset_name as its paired tool_use block, so it is stamped here.
...
    if toolset_name is not None:
        tool_result_block["toolset_name"] = toolset_name
```

### 1.5 SDK 侧对 zoom 的其余约束（原文）

`anthropic/lib/tools/_toolsets/_computer.py:392`（列表字段的形状约定）：

```python
SHAPES = (("coordinate", "[x, y]", 2), ("start_coordinate", "[x, y]", 2), ("region", "[x0, y0, x1, y1]", 4))
"""The list-valued input fields, the shape the model is told to send, and the length that shape has."""
```

`anthropic/lib/tools/_toolsets/_computer.py:396-407`（SDK 唯一的 "校验"）：

```python
def parse_computer_input(member: ComputerMember, raw: Mapping[str, object]) -> BetaComputerMemberInput:
    """`parse_input` for a computer member, plus the shape of each coordinate list: the generated models type them
    as lists of integers of any length, and a driver indexes them."""
    parsed = parse_input(member, raw, family=FAMILY)
    problems = [
        f"{field}: expected {shape}"
        for field, shape, length in SHAPES
        if (value := getattr(parsed, field, None)) is not None and len(value) != length
    ]
    if problems:
        raise InvalidMemberInputError(member.name, "; ".join(problems), family=FAMILY)
    return parsed
```

`anthropic/lib/tools/_toolsets/_computer.py:145`（成员返回值的约定）：

```python
    - `screenshot` and `zoom` return an image that already fits the model's image limits.
    - `cursor_position` returns the cursor in screenshot pixels.
```

`anthropic/lib/tools/_toolsets/_computer.py:256-257`（driver 必须覆写的方法）：

```python
    def zoom(self, context: BetaToolsetCallContext, input: BetaComputerZoomInput) -> BetaScreenshotResult:
        raise UnavailableMemberError("zoom", family=FAMILY)
```

`anthropic/lib/tools/_toolsets/_base.py:86-94`：

```python
class BetaScreenshotResult(BaseModel):
    """A screenshot or zoom image from a browser or computer toolset, rendered as one image block. The image must
    already fit the model's image limits: the API rejects an oversized one rather than scaling it down."""

    data: str
    """Base64 image bytes, without a `data:` prefix."""

    media_type: Literal["image/png", "image/jpeg", "image/gif", "image/webp"] = "image/png"
```

`anthropic/lib/tools/_toolsets/_computer_results.py:26-31`：

```python
class BetaComputerCursorPositionResult(BaseModel):
    """Where the cursor is, in screenshot pixels; rendered as `X={x},Y={y}`."""
```

**要点**：SDK **不做任何坐标换算**，也不做裁剪。它只做 (a) 形状校验，(b) 把 `region` 原样交给 driver。裁剪、缩放、坐标映射**全部是 harness 的责任**。

---

## 2. quickstart 里的实现

### 2.1 `computer-use-demo` 目录结构

来源：<https://github.com/anthropics/claude-quickstarts/tree/main/computer-use-demo>

```
computer-use-demo/
├── README.md                     # 官方说明：工具版本、屏幕尺寸与 image resizing 警告
├── CONTRIBUTING.md  LICENSE  Dockerfile  setup.sh  pyproject.toml  ruff.toml
├── dev-requirements.txt
├── .claude/skills/verify/SKILL.md # 官方自测流程：docker build + Playwright 驱动 Streamlit
├── .zed/settings.json
├── computer_use_demo/            # ★ Python 包（Linux/X11 容器版）
│   ├── __init__.py
│   ├── loop.py                   # 采样循环：调 API、解析 tool_use、执行、回填 tool_result
│   ├── streamlit.py              # Streamlit UI：选模型/工具版本、聊天、HTTP exchange 日志
│   └── tools/
│       ├── __init__.py  base.py  collection.py
│       ├── computer.py           # ★ 核心：BaseComputerTool + 三个 dated 版本 + toolset + zoom
│       ├── bash.py  edit.py  run.py  groups.py
├── image/                        # ★ Docker 镜像的 X11/VNC 桌面环境配置（无 Python 业务代码）
│   ├── entrypoint.sh  start_all.sh
│   ├── xvfb_startup.sh  x11vnc_startup.sh  mutter_startup.sh  tint2_startup.sh
│   ├── http_server.py  index.html  static_content/index.html
│   ├── .streamlit/config.toml
│   └── .config/tint2/{tint2rc, applications/*.desktop}
└── tests/
    ├── conftest.py  loop_test.py  streamlit_test.py
    └── tools/{bash_test.py, computer_test.py, edit_test.py}
```

`computer-use-best-practices` 目录结构（macOS 原生版，含 `image.py`）：

```
computer-use-best-practices/
├── README.md                     # ★ 官方说明（含 toolset 与坐标空间的说明）
├── constants.py                  # Config（cfg.px_per_token / max_edge_px / max_tokens …）
├── computer_use/
│   ├── __init__.py  __main__.py  formatters.py
│   ├── image.py                  # ★ target_image_size() / resize_and_encode()  —— "~14% 漂移"原文在此
│   ├── loop.py  preflight.py  render.py  trajectory.py
│   └── tools/{base.py, batch.py, browser.py, computer.py, editor.py, open_app.py, result.py, shell.py}
├── dev_ui/{localization_demo, tool_panel, trajectory_viewer}
├── sandbox/default.sb
└── tests/{test_image.py, test_zoom.py, test_computer.py, …}
```

> `constants.py` 在本次抓取时无法从 raw / contents API 取到（raw 404、blob API 404，且同一分钟前后两次 tree 查询结果不一致——上游正在改动）。其关键取值（`px_per_token=28`、`max_edge_px=1568`、`max_tokens=1568`）由 `image.py` 自身的 docstring 明确写出（见 §2.2），且被 `tests/test_image.py` 的 `cfg.max_edge_px` / `cfg.max_tokens` 断言间接证实。

### 2.2 `image.py` 全文要点 —— `target_image_size()` 到底算了什么

文件：`computer-use-best-practices/computer_use/image.py`（全文 102 行），<https://github.com/anthropics/claude-quickstarts/blob/main/computer-use-best-practices/computer_use/image.py>

**模块 docstring 原文（`:1-15`）—— 这是 §3(c) 那个 ~14% 漂移的出处**：

```python
"""
Image sizing and encoding.

The API's vision encoder tiles images into 28x28 patches and caps both the long
edge (1568 px) and the total tile count (1568). If we send an image that
violates either, the server resizes it *again* before the model sees it, and
then the model emits click coordinates in a space we never observed, producing
systematic click drift (~14% on a 16:10 MacBook screen).

So: pre-resize every screenshot to the largest dimensions that satisfy *both*
constraints, and remember those dimensions to scale model coordinates back to
screen space.

`target_image_size` is a direct port of the API's reference algorithm.
"""
```

**`target_image_size()`（`:38-75`）**：

```python
def target_image_size(
    width: int,
    height: int,
    *,
    px_per_token: int = cfg.px_per_token,
    max_edge_px: int = cfg.max_edge_px,
    max_tokens: int = cfg.max_tokens,
) -> tuple[int, int]:
    """
    Largest (w, h) preserving aspect ratio with long-edge <= max_edge_px and
    tile-count <= max_tokens. Returns the input unchanged if already valid.
    """
    if (
        width <= max_edge_px
        and height <= max_edge_px
        and n_tokens_for_img(width, height, px_per_token) <= max_tokens
    ):
        return width, height

    # Normalize to landscape for the search; transpose result back.
    if height > width:
        w, h = target_image_size(
            height, width, px_per_token=px_per_token, max_edge_px=max_edge_px, max_tokens=max_tokens
        )
        return h, w

    aspect = width / height
    lo, hi = 1, width  # lo always valid, hi always invalid

    while True:
        if lo + 1 == hi:
            return lo, max(round(lo / aspect), 1)
        mid_w = (lo + hi) // 2
        mid_h = max(round(mid_w / aspect), 1)
        if mid_w <= max_edge_px and n_tokens_for_img(mid_w, mid_h, px_per_token) <= max_tokens:
            lo = mid_w
        else:
            hi = mid_w
```

**它到底算了什么（逐条）**

1. **两个硬约束，同时满足**
   - 长边上限：`w <= 1568` 且 `h <= 1568`（`max_edge_px`）
   - **总 token（tile）数上限**：`ceil(w/28) * ceil(h/28) <= 1568`（`max_tokens`）
   - `n_tokens_for_px(px) = (px - 1) // px_per_token + 1`，即 `ceil(px/28)`；`n_tokens_for_img(w,h) = n_tokens_for_px(w) * n_tokens_for_px(h)`（`:29-35`）
2. **不是"总像素上限"**，而是**长边 + tile 数**两个约束。tile 数 ≈ `(w/28)*(h/28)`，所以等效于面积上限 ~1568*784 ≈ 1.23 M px，但因向上取整，比纯面积约束更严。
3. **比例保持**：以 `aspect = width/height` 为准，高度由 `round(mid_w / aspect)` 推出（**取整**，非精确有理比例；误差 < 0.5 px）。
4. **求最大可行尺寸**：先归一到横版（竖版转置后结果再转回），再对**宽度做二分查找**，取满足双约束的最大宽度。
5. **不放大**：已合规时**原样返回**（`return width, height`），绝不把 800×600 拉到 1568。
6. **它处理的是"喂给模型的图太大"，不是 zoom。**
   `resize_and_encode()`（`:78-102`）是它的唯一调用点：

```python
def resize_and_encode(
    img: Image.Image, *, min_bytes: int = cfg.min_screenshot_bytes
) -> tuple[str, tuple[int, int]]:
    """
    Resize `img` to its target size (no-op if already within budget), encode as
    JPEG, and return (base64_string, (sent_width, sent_height)).

    `min_bytes` defaults to the screenshot sanity threshold; pass 0 when
    encoding arbitrary images (e.g. the editor `view` command) where a small
    file is legitimate rather than a sign of a failed capture.
    """
    tw, th = target_image_size(img.width, img.height)
    if (tw, th) != (img.width, img.height):
        img = img.resize((tw, th), Image.Resampling.LANCZOS)
    if img.mode != "RGB":
        img = img.convert("RGB")
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=cfg.jpeg_quality)
    data = buf.getvalue()
    if len(data) < min_bytes:
        raise ScreenshotTooSmall(...)
    return base64.standard_b64encode(data).decode("ascii"), (tw, th)
```

  - 全屏截图走 `resize_and_encode(img)`；zoom 的 crop 也走 `resize_and_encode(crop, min_bytes=0)`（见 §2.4）。
  - 所以准确说法是：**`target_image_size()` 是"送图预算"机制**（保证服务端不会再偷偷缩一次，从而避免坐标空间错配）；它是 zoom 的**最后一道保险**（crop 也得合规），但它**不是** zoom 机制本身。真正的 zoom 机制是"从原图裁一块"。

**官方测试印证**（`computer-use-best-practices/tests/test_image.py`）：

```python
21: def test_small_image_unchanged():
22:     assert target_image_size(800, 600) == (800, 600)
25: def test_long_edge_only_is_insufficient():
26:     # 1568x1014 has long-edge == cap but 56*37 = 2072 tokens > 1568.
27:     w, h = target_image_size(1568, 1014)
28:     assert (w, h) != (1568, 1014)
34: def test_retina_mbp():
35:     w, h = target_image_size(3456, 2234)
36:     assert _valid(w, h)
37:     assert abs(w / h - 3456 / 2234) < 0.01
40: def test_portrait_transposed():
41:     lw, lh = target_image_size(3000, 2000)
42:     pw, ph = target_image_size(2000, 3000)
43:     assert (pw, ph) == (lh, lw)
```

> `56*37 = 2072` 印证了 tile 数 = `ceil(w/28) * ceil(h/28)`：1568/28 = 56，1014/28 = 36.21 → 37。

### 2.3 best-practices 目录里对 zoom 的官方用法说明原文

**官方给模型看的工具描述**（`computer-use-best-practices/computer_use/tools/computer.py:190-194`，`input_schema.properties.action.description` 内）：

```python
                    "* zoom: return a cropped, higher-detail view of the screen "
                    "region `region` = [x1, y1, x2, y2]. Use this to read small "
                    "text or inspect fine detail. Coordinates in subsequent "
                    "actions still refer to the full screenshot, not the zoom."
```

**`region` 字段的 schema 描述**（同文件 `:213-219`）：

```python
            "region": {
                "type": "array",
                "items": {"type": "integer"},
                "minItems": 4,
                "maxItems": 4,
                "description": "[x1, y1, x2, y2] in the same image space as `coordinate`.",
            },
```

**模块 docstring**（同文件 `:1-9`）：

```python
"""
Full-screen computer control via pyautogui.

The model is shown a *resized* screenshot (see image.py). When it emits a
coordinate we scale it back to logical screen pixels using the dimensions of
the most recently sent screenshot. On retina displays pyautogui's screenshot
is in physical pixels while its mouse functions take logical pixels, so we
downscale to logical first.
"""
```

**README 中的 toolset 说明**（`computer-use-best-practices/README.md:304-329`，节选）：

> On the first-party API the hosted declaration is the computer **toolset**, `{"type": "computer_toolset_20260801"}` — a single nameless entry that declares one member tool per action, with no beta header and no display size (coordinates are in screenshot pixels either way, and the screenshots you send establish that frame).
>
> - **Member calls are ordinary `tool_use` blocks named after the action** (`left_click`, `zoom`, …) with `toolset_name: "computer"` and no `action` field in the input. `ToolCollection.run` routes on `(toolset_name, name)` and folds the name back into `action`, so the explicit `ComputerTool` serves both declarations. Every member — including `zoom`, which the toolset enables by default — is implemented there.

### 2.4 官方对"坐标映射回全屏"的处理写在哪个文件哪几行

**两处官方实现都明确写了"zoom 图不是坐标系"**：

| 文件 | 行号 | 原文 |
|---|---|---|
| `computer-use-demo/computer_use_demo/tools/computer.py` | **476-484**（`zoom()` 的 docstring） | 见下 |
| `computer-use-best-practices/computer_use/tools/computer.py` | **190-194**（工具描述） | 见 §2.3 |
| `computer-use-best-practices/computer_use/tools/computer.py` | **450-454**（zoom 的 tool_result 文案） | 见下 |

**`computer-use-demo` 的 `zoom()` 全文（`:475-509`）**：

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
        capture = await self._capture(raw_path)
        if not raw_path.exists():
            raise ToolError(f"Failed to take screenshot for zoom: {capture.error}")
        try:
            # +repage drops the crop offset so the resize sees a plain image;
            # a bare WxH geometry resizes to fit within the frame, aspect kept.
            await run(
                f"convert {raw_path} -crop {x1 - x0}x{y1 - y0}+{x0}+{y0} +repage "
                f"-resize {frame_w}x{frame_h} {zoom_path}"
            )
            if not zoom_path.exists():
                raise ToolError("Failed to crop screenshot for zoom")
            return ToolResult(
                base64_image=base64.b64encode(zoom_path.read_bytes()).decode()
            )
        finally:
            raw_path.unlink(missing_ok=True)
            zoom_path.unlink(missing_ok=True)
```

**best-practices 的 `_zoom()` 全文（`:428-456`）**：

```python
    def _zoom(self, region: list[int] | None) -> ToolResult:
        if not region or len(region) != 4:
            return ToolResult(error="zoom requires `region` = [x1, y1, x2, y2]")
        x1, y1, x2, y2 = region
        if x2 <= x1 or y2 <= y1:
            return ToolResult(error="zoom region must have x2 > x1 and y2 > y1")
        # Capture at full physical resolution (retina) so the crop carries more
        # detail than the same area did in the downscaled full-screen shot.
        full = pyautogui.screenshot()
        fx, fy = full.width / self.sent_w, full.height / self.sent_h
        box = (
            max(0, round(x1 * fx)),
            max(0, round(y1 * fy)),
            min(full.width, round(x2 * fx)),
            min(full.height, round(y2 * fy)),
        )
        crop = full.crop(box)
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

**真正做坐标换算的是这两处（不是 zoom 专属，而是全局统一的缩放层）**：

- `computer-use-demo`：`tools/computer.py:220-226`（`validate_and_get_coordinates`）+ `:279-302`（`scale_coordinates`）

```python
    def validate_and_get_coordinates(self, coordinate: tuple[int, int] | None = None):
        if not isinstance(coordinate, list) or len(coordinate) != 2:
            raise ToolError(f"{coordinate} must be a tuple of length 2")
        if not all(isinstance(i, int) and i >= 0 for i in coordinate):
            raise ToolError(f"{coordinate} must be a tuple of non-negative ints")

        return self.scale_coordinates(ScalingSource.API, coordinate[0], coordinate[1])
```

```python
    def scale_coordinates(self, source: ScalingSource, x: int, y: int):
        """Scale coordinates to a target maximum resolution."""
        if not self._scaling_enabled:
            return x, y
        ratio = self.width / self.height
        target_dimension = None
        for dimension in MAX_SCALING_TARGETS.values():
            # allow some error in the aspect ratio - not ratios are exactly 16:9
            if abs(dimension["width"] / dimension["height"] - ratio) < 0.02:
                if dimension["width"] < self.width:
                    target_dimension = dimension
                break
        if target_dimension is None:
            return x, y
        # should be less than 1
        x_scaling_factor = target_dimension["width"] / self.width
        y_scaling_factor = target_dimension["height"] / self.height
        if source == ScalingSource.API:
            if x > self.width or y > self.height:
                raise ToolError(f"Coordinates {x}, {y} are out of bounds")
            # scale up
            return round(x / x_scaling_factor), round(y / y_scaling_factor)
        # scale down
        return round(x * x_scaling_factor), round(y * y_scaling_factor)
```

- `computer-use-best-practices`：`computer_use/tools/computer.py:255-268`

```python
    def _scale_to_screen(self, coord: list[int]) -> tuple[int, int]:
        x, y = coord
        sx = round(x * self.screen_w / self.sent_w)
        sy = round(y * self.screen_h / self.sent_h)
        return (
            max(0, min(sx, self.screen_w - 1)),
            max(0, min(sy, self.screen_h - 1)),
        )

    def _scale_to_image(self, screen_x: int, screen_y: int) -> tuple[int, int]:
        return (
            round(screen_x * self.sent_w / self.screen_w),
            round(screen_y * self.sent_h / self.screen_h),
        )
```

**官方测试给出的确定裁剪数学**（`computer-use-demo/tests/tools/computer_test.py:276-311`）：

```python
async def test_zoom_crops_the_physical_capture_and_fits_the_screenshot_frame(
    zoom_capture,
):
    tool = ComputerTool20251124()
    # 2048x1536 scales down to XGA (1024x768), so screenshot pixels are half
    # of physical pixels: the crop must be taken in physical pixels and the
    # result fitted back into the 1024x768 frame the model sees.
    tool.width, tool.height = 2048, 1536

    result = await tool(action="zoom", region=(100, 100, 300, 200))

    assert result.base64_image == base64.b64encode(b"zoomed").decode()
    (command,) = zoom_capture
    assert "-crop 400x200+200+200 +repage" in command
    assert "-resize 1024x768 " in command


async def test_zoom_without_scaling_uses_screenshot_pixels_directly(zoom_capture):
    tool = ComputerTool20251124()  # 1024x768 from conftest: no scaling target

    await tool(action="zoom", region=(10, 20, 110, 70))

    (command,) = zoom_capture
    assert "-crop 100x50+10+20 +repage" in command
    assert "-resize 1024x768 " in command


async def test_zoom_is_a_toolset_member(zoom_capture, computer_toolset):
    result = await ToolCollection(computer_toolset).run(
        name="zoom", tool_input={"region": [0, 0, 50, 50]}, toolset_name="computer"
    )
    assert result.base64_image
    assert "-crop 50x50+0+0" in zoom_capture[0]
```

校验规则（`:314-328`）：

```python
@pytest.mark.parametrize(
    "region,message",
    [
        (None, "must be a tuple of 4 coordinates"),
        ([0, 0, 10], "must be a tuple of 4 coordinates"),
        ([0, 0, -1, 10], "non-negative integers"),
        ([50, 0, 50, 10], "x0 < x1 and y0 < y1"),
        ([0, 60, 10, 20], "x0 < x1 and y0 < y1"),
    ],
)
```

---

## 3. 三个工程问题的明确回答

### (a) Anthropic 的 zoom 裁剪是**从原始截图裁**还是从**降采样后的图裁**？

**答：从原始（物理分辨率）截图裁。两套官方实现都明确如此，且都有代码注释与测试断言佐证。**

**证据 1 —— `computer-use-demo`（`computer_use_demo/tools/computer.py`）**
- `:490-492`：zoom 走的是**自己新拍的一张** `capture = await self._capture(raw_path)`，不是 `screenshot()` 产出的那张降采样图。
- `:257-258`：`_capture` 的 docstring —— `"""Capture the display at physical resolution into `path`."""`
- `:232-236` 对比：喂给模型的那张图是**另一次** `screenshot()`，里面才有 `convert {path} -resize {x}x{y}! {path}` 的降采样。zoom **从不接触**这个降采样产物。
- `:476-479` docstring：`The region is mapped onto a physical-resolution capture (so the crop carries more detail than the same area of a scaled-down screenshot)`

**证据 2 —— `computer-use-best-practices`（`computer_use/tools/computer.py`）**
- `:434-436`：`# Capture at full physical resolution (retina) so the crop carries more detail than the same area did in the downscaled full-screen shot.` → `full = pyautogui.screenshot()`
- `:444`：`crop = full.crop(box)`

**证据 3 —— 官方测试断言**（`computer-use-demo/tests/tools/computer_test.py:276-291`）
> `# 2048x1536 scales down to XGA (1024x768), so screenshot pixels are half of physical pixels: the crop must be taken in physical pixels and the result fitted back into the 1024x768 frame the model sees.`
> 断言 `-crop 400x200+200+200 +repage`：region (100,100,300,200)（sent 像素）→ 物理 (200,200)-(600,400)，**乘 2** 放大后才裁。

**证据 4 —— SDK docstring**（`anthropic/types/computer_zoom_input.py:9-14`）
> `The crop is scaled up to fill the image budget so fine details become legible.`

**推论（重要）**：降采样只定义**模型说话的坐标系**，不参与裁剪。所以"裁剪源"永远是物理原图，"坐标系"永远是降采样图的像素。

---

### (b) 模型在 zoom 图里给出的坐标，**由谁**换算回全屏坐标？

**答：换算分两段，责任方不同。**

| 段 | 谁做 | 内容 |
|---|---|---|
| **crop 内 → 整屏（sent 图像）坐标** | **模型**（语义上） | 模型被**明确告知** zoom 图不是坐标系，必须继续在整屏坐标里作答。harness **不做**任何 crop-offset 变换。 |
| **整屏（sent 图像）坐标 → 物理屏幕坐标** | **harness** | 统一的缩放层：`scale_coordinates(ScalingSource.API, …)` / `_scale_to_screen()`。 |
| **API / SDK** | **不做任何事** | SDK 只校验 `region` 长度=4（`_computer.py:392, 396-407`），然后把 input 原样交给 driver。 |

**证据（模型负责 crop→整屏）**

- `computer-use-demo/computer_use_demo/tools/computer.py:482-483`：
  > `The model keeps emitting coordinates in screenshot pixels afterwards; the zoom image itself is never a coordinate frame.`
- `computer-use-best-practices/computer_use/tools/computer.py:192-193`：
  > `Coordinates in subsequent actions still refer to the full screenshot, not the zoom.`
- `computer-use-best-practices/computer_use/tools/computer.py:451-454`（zoom 的 tool_result 文案，回灌给模型）：
  > `Subsequent coordinates still refer to the full {sent_w}x{sent_h} screenshot, not this crop.`
- `computer-use-best-practices/computer_use/tools/computer.py:218`（region 的单位定义）：
  > `"[x1, y1, x2, y2] in the same image space as `coordinate`."`
- `anthropic/types/computer_zoom_input.py:11-12`：
  > `Region coordinates are in the full-screenshot space (not physical display pixels).`

**证据（harness 负责 sent→物理）**
- `computer-use-demo`：`tools/computer.py:296-300`（`source == ScalingSource.API` 分支 → `round(x / x_scaling_factor)`）
- `computer-use-best-practices`：`computer_use/tools/computer.py:255-262`（`_scale_to_screen`）

**证据（SDK 不负责）**
- `anthropic/lib/tools/_toolsets/_computer.py:396-407` + `:256-257`：SDK 只 parse + dispatch，zoom 的实现由 driver 提供。

**⚠️ 一个必须注意的坑（我们自己的实现要避开）**
`computer-use-demo` 的 `scale_coordinates`（`:296-298`）在 API 方向做边界检查时用的是 **`self.width` / `self.height`（物理尺寸）**，而不是模型看到的 sent 尺寸：

```python
        if source == ScalingSource.API:
            if x > self.width or y > self.height:
                raise ToolError(f"Coordinates {x}, {y} are out of bounds")
```

也就是说，**降采样生效时，模型给一个超出 sent 图、但小于物理图的坐标，是不会报错的**，会被静默放大。相对地，`computer-use-best-practices` 的 `_zoom`（`:438-443`）走的是**静默 clamp**（`max(0, …)` / `min(full.width, …)`）。**两套官方实现行为不一致**——见 §4 中标为"我们的设计选择"的一条。

---

### (c) 官方对"超限大图被服务端静默降采样"是怎么说的？

**原文出处：`computer-use-best-practices/computer_use/image.py:1-15`（模块 docstring，英文原句）**

```python
"""
Image sizing and encoding.

The API's vision encoder tiles images into 28x28 patches and caps both the long
edge (1568 px) and the total tile count (1568). If we send an image that
violates either, the server resizes it *again* before the model sees it, and
then the model emits click coordinates in a space we never observed, producing
systematic click drift (~14% on a 16:10 MacBook screen).

So: pre-resize every screenshot to the largest dimensions that satisfy *both*
constraints, and remember those dimensions to scale model coordinates back to
screen space.

`target_image_size` is a direct port of the API's reference algorithm.
"""
```

**要点拆解**
- **系统误差百分比**：`~14%`（原文 `systematic click drift (~14% on a 16:10 MacBook screen)`）
- **适用屏幕比例**：`16:10 MacBook screen`
- **机制**：vision encoder 以 28×28 patch 分块；同时限制**长边 1568 px** 与**总 tile 数 1568**。任一违反 → **服务端再缩一次** → 模型在一个"我们没观察过的空间"里输出坐标 → 系统性点击漂移。
- **对策**：预缩放到同时满足两个约束的最大尺寸，并**记住该尺寸**以便把模型坐标换算回屏幕空间。

**第二处官方原文（`computer-use-demo/README.md:167-168`）**：

> We do not recommend sending screenshots in resolutions above [XGA/WXGA](https://en.wikipedia.org/wiki/Display_resolution_standards#XGA) to avoid issues related to [image resizing](https://docs.claude.com/en/docs/build-with-claude/vision#evaluate-image-size).
> Relying on the image resizing behavior in the API will result in lower model accuracy and slower performance than implementing scaling in your tools directly. The `computer` tool implementation in this project demonstrates how to scale both images and coordinates from higher resolutions to the suggested resolutions.

**第三处（`computer-use-demo/README.md:170-173`）**：

> When implementing computer use yourself, we recommend using XGA resolution (1024x768):
>
> - For higher resolutions: Scale the image down to XGA and let the model interact with this scaled version, then map the coordinates back to the original resolution proportionally.
> - For lower resolutions or smaller devices (e.g. mobile devices): Add black padding around the display area until it reaches 1024x768.

**⚠️ 与 SDK 表述的一处张力（我们的解读，标注为推断）**
SDK 的 `BetaScreenshotResult`（`anthropic/lib/tools/_toolsets/_base.py:86-88`）说：

```python
    """A screenshot or zoom image from a browser or computer toolset, rendered as one image block. The image must
    already fit the model's image limits: the API rejects an oversized one rather than scaling it down."""
```

`image.py` 说的是"服务端会再缩一次"。两者可以同时成立，但对应**不同层**的限额——**推断**：
- `image.py` 讲的是**视觉编码器**的分块上限（1568 长边 / 1568 tile），触发**静默再缩**；
- `_base.py` 讲的是 **image block 的硬限额**（例如 Claude 4.7+ 的 8000 px），触发**报错拒绝**。
我们在实现时**两条都要防**：先按 `target_image_size()` 预缩（消除静默再缩），同时确保不触碰硬限额（否则直接 400）。

---

## 4. 我们可移植的最小实现规格 —— "自建 crop 精定位"行为契约

**记号**
- `Pw × Ph`：整屏**物理像素**尺寸（Windows 上 = 桌面分辨率，如 2560×1600）
- `Fw × Fh`：**送给模型的**整屏图实际像素尺寸（= `target_image_size(Pw, Ph)` 的结果，或官方 XGA 约定值）
- `s = Fw/Pw`（x 向缩放比）、`t = Fh/Ph`（y 向缩放比）
- 模型粗定位点 `p = (u, v)`，单位 = **F 空间**像素
- 模型给出的 region `r = (x0, y0, x1, y1)`，单位 = **F 空间**像素

---

### 契约 C1 —— 输入

| # | 条款 | 依据 |
|---|---|---|
| C1.1 | 每次精定位必须拿到**整屏物理像素**原图 `Pw×Ph`，**不能**只拿降采样图。 | 官方 §3(a)：两套实现都从物理分辨率图裁剪 |
| C1.2 | 必须持久记录**最近一次送给模型的整屏图的实际尺寸** `Fw×Fh`（不是标称分辨率）。 | `image.py:10-12`：`remember those dimensions to scale model coordinates back to screen space` |
| C1.3 | 粗定位点与 region 都以 **F 空间**像素表达；region 语义为 `(x0,y0,x1,y1)` 闭区间端点，非 x/y/w/h。 | `ComputerZoomInput.region` docstring；`best-practices` schema `"[x1, y1, x2, y2] in the same image space as `coordinate`"` |
| C1.4 | 送图前必须跑 `target_image_size()`，并让实际发送尺寸 **==** 记录下来的 `Fw×Fh`。 | `image.py:38-75, 89-91`；`test_image.py` |

### 契约 C2 —— 边界处理

| # | 条款 | 依据 |
|---|---|---|
| C2.1 | region 必须是**恰好 4 个非负整数**，且 `x0 < x1`、`y0 < y1`；否则**直接拒绝**（不裁图）。 | 官方 `computer.py:448-461`（demo）+ `computer.py:429-433`（best-practices）；测试 `test_zoom_rejects_malformed_regions` |
| C2.2 | 越界（region 超出整屏）时：**clamp**（截断到屏幕边界），**不 shift**（不平移窗口）、**不 shrink**（不缩小窗口）。 | best-practices `computer.py:438-443`：`max(0, …)` / `min(full.width, …)` |
| C2.3 | 我们的设计选择（官方两套实现不一致）：**采用 clamp**。理由：(1) best-practices 是较新、面向生产的实现；(2) demo 的 `scale_coordinates` 越界检查用的是**物理**尺寸（`self.width`），本身就偏松，不构成"严格拒绝"的一致语义。**同时**我们**不**照抄 demo 的宽松边界检查——在 F 空间做严格边界检查并在越界时**记录告警**。 | 我们的设计选择；不一致证据见 §3(b) ⚠️ |
| C2.4 | 目标**必须完整落在 crop 内吗**？—— **官方语义下不需要**：region 就是 crop 本身，模型指定什么就裁什么；crop 是一个"用户选定的观察窗口"，官方**没有**"围绕目标自动取景"的概念。 | 官方两个 `zoom()` 实现都直接用 region 当 crop 几何，无任何居中/扩边逻辑 |
| C2.5 | 我们的设计选择：提供**两种模式**——(A) **忠实模式**（默认，严格对齐官方）：直接裁 region；(B) **自动取景模式**：以粗定位点 `p` 为中心生成固定尺寸窗口，**越界时 shift**（而非 shrink）以保证目标完整在内。 | 我们的设计选择（官方无对应物） |

### 契约 C3 —— 放大倍数与等比

| # | 条款 | 依据 |
|---|---|---|
| C3.1 | 放大倍数 = **把 crop 等比 fit 进"模型看整屏时用的那张图的尺寸" `Fw×Fh` 之内**。 | demo `computer.py:499-501`：`-resize {frame_w}x{frame_h}`；docstring `:479-481` `resized, aspect preserved, to fit within the screenshot frame: one axis fills it, the other is at most as large.` |
| C3.2 | **必须等比**（两套官方实现都等比）。demo 用 ImageMagick `-resize WxH`（**不带** `!`，故等比 fit；带 `!` 才是强制拉伸）。best-practices 用 `target_image_size()` + `LANCZOS`。 | demo `computer.py:499-500` 注释：`a bare WxH geometry resizes to fit within the frame, aspect kept.`；`image.py:91` |
| C3.3 | 放大倍数**不是固定值**，取决于 region 大小与 `Fw×Fh` 之比：官方测试里 2048×1536 物理 → 1024×768 帧，region 200×100(sent) = 400×200(物理) → `-resize 1024x768` → 实际 1024×512。**相对模型整屏图的净放大 = 1024/200 = 5.12×**；相对物理像素 = 2.56×。 | `tests/tools/computer_test.py:277-291`（`-crop 400x200+200+200 +repage` + `-resize 1024x768 `） |
| C3.4 | 我们的设计选择：额外暴露"**目标放大倍数**"参数（默认 = 撑满帧），因为官方策略对**小 region** 会做超采样（物理像素不足时纯放大，得到的只是模糊图）。自建实现可改为"只放大到物理分辨率上限（1×物理像素即止）"，把上限交给模型预算。 | 我们的设计选择 |
| C3.5 | 裁剪产生的 crop 图**也必须**过一遍 `target_image_size()`（或等价的预算检查），保证不触发服务端静默再缩。 | `image.py:89`（`resize_and_encode` 是唯一出口，zoom crop 走同一函数，best-practices `computer.py:446`） |

### 契约 C4 —— crop 内坐标 → 全局物理坐标的换算公式

| # | 条款 | 依据 |
|---|---|---|
| C4.1 | **crop 内 → 整屏（F 空间）**：由**模型**完成。harness **不做**任何 crop-offset 变换；模型被明确要求继续用整屏坐标作答。 | demo `computer.py:482-483`；best-practices `computer.py:192-193, 451-454`；SDK `computer_zoom_input.py:11-12` |
| C4.2 | **整屏（F 空间）→ 全局物理**：`U = round(u · Pw / Fw)`，`V = round(v · Ph / Fh)`。等价形式（demo）：`x_scaling_factor = target_width / self.width`，`U = round(u / x_scaling_factor)`。 | demo `computer.py:294-300`；best-practices `computer.py:255-262` |
| C4.3 | **region（F 空间）→ 物理 crop 框**：`box = (round(x0·Pw/Fw), round(y0·Ph/Fh), round(x1·Pw/Fw), round(y1·Ph/Fh))`，再 clamp 到 `[0,Pw]×[0,Ph]`。 | best-practices `computer.py:437-443`；demo `computer.py:486-487` + `:499` |
| C4.4 | **DPI 感知前提**：上面所有换算只把 **F 空间**和**物理像素**联系起来；**操作系统层面的 DPI 缩放（Windows 的 100%/125%/150%）是另一层**。要落到真实鼠标事件，需要：
  - 物理像素 `Pw×Ph`（截图 API 得到的真实分辨率）
  - 逻辑像素 `Lw×Lh`（`GetSystemMetrics(SM_CXSCREEN)` / pyautogui.size() 之类）
  - `d = Pw / Lw`（Windows 上 = `GetDpiForSystem()/96`，Retina 上 = 2）
  - 鼠标事件坐标 = **逻辑像素** = `U / d`（Windows 下 `SetCursorPos`/`SendInput` 走逻辑坐标）
  - best-practices 正是这么做的：`computer.py:279-281` `if img.width != self.screen_w or img.height != self.screen_h: img = img.resize((self.screen_w, self.screen_h))` —— **先降到逻辑尺寸再发模型**，这样 F 空间直接等于逻辑空间，`_scale_to_screen` 只需一次除法。 | best-practices `computer.py:1-9`（模块 docstring 明确讲 retina 物理/逻辑之分）、`:225, 279-281`；demo 侧 X11 无此层（`WIDTH/HEIGHT` 即物理） |
| C4.5 | 我们的设计选择：**在 Windows 上建议照 best-practices 的做法**——先统一到逻辑像素再送模型，使 F 空间 == 逻辑空间，避免每次点击都多一层 DPI 换算；但**保留** `Pw×Ph` 原图用于 zoom 裁剪，以换取 zoom 的额外细节。 | 我们的设计选择（依据 C4.4 的官方分层） |

### 契约 C5 —— 判定"精定位失败"并回落

**先说官方事实**：**官方没有"精定位失败"这个概念，也没有任何回落机制。**
- 失败只表现为把错误文本回给模型，由模型自行决定下一步（demo 抛 `ToolError` → `ToolFailure` → `is_error: true`；best-practices 返回 `ToolResult(error=...)`）。
- 唯一的"重试"是模型自己再发一次 zoom（best-practices 的 `zoom` 成员可重复调用；demo 的 toolset 允许一轮多成员调用）。

| # | 条款 | 依据 |
|---|---|---|
| C5.1 | 我们的设计选择——判定精定位**失败**并回落到粗定位的条件（全部为我们的设计选择，官方无对应物）：
 1. region 非法（长度≠4 / 非整数 / 负数 / `x0≥x1` / `y0≥y1`）→ 不裁图，直接回落
 2. clamp 后 crop 的物理尺寸为 0（目标在屏幕外）→ 回落
 3. crop 的物理面积过小（如 < 16×16 物理像素）→ 无有效细节，回落
 4. **无净增细节**：`(crop 物理宽 / Fw) < 1` 且 crop 未落在高 DPI 区域 → 本次 zoom 不会比整屏更清楚，回落
 5. 模型在 zoom 之后给出的坐标离粗定位点**超过阈值**（说明模型看错了区域）→ 回落
 6. 连续 N 次 zoom 仍未收敛（坐标方差不下降）→ 回落 | 我们的设计选择 |
| C5.2 | 回落动作 = 直接用粗定位点在**物理坐标**上执行动作，不做二次确认。回落时**必须**使用与 C4.2 相同的 F→物理换算（因为粗定位点也是 F 空间坐标）。 | 依据 C4.2；官方"回落后继续用整屏坐标"的语义（§3(b)）保证这种回落是安全的 |
| C5.3 | 失败信息**回灌给模型**（而不是静默吞掉），措辞要明确告知"这是回退后的粗定位结果"。官方把错误直接交给模型处理（demo `ToolError` → `is_error`；best-practices `ToolResult(error=...)`），我们沿用同一模式。 | demo `tools/collection.py:54-55, 61-62`；best-practices `computer.py:430, 433, 447-448` |
| C5.4 | 我们的设计选择：回落时**在日志里记录**触发原因（C5.1 的第几条），便于调参。 | 我们的设计选择 |

### 契约 C6 —— 调用形态（toolset vs 单 tool）

| # | 条款 | 依据 |
|---|---|---|
| C6.1 | 若我们使用 Anthropic 官方 toolset（`computer_toolset_20260801`）：zoom 是**独立成员工具**，`tool_use.name == "zoom"`、`tool_use.toolset_name == "computer"`、input **无** `action` 字段。 | SDK `computer_zoom_tool_use_block.py:19-29`；`computer_toolset_20260801_param.py:12-22` |
| C6.2 | 回给模型的 `tool_result` **必须**带 `toolset_name: "computer"`。 | demo `loop.py:342-382`；README `:322-324` |
| C6.3 | 一轮多个成员调用：**按块顺序串行**，首个失败后其余用 `is_error: true` + `"Not executed: an earlier computer action in this turn failed."` 回答，**不执行**。 | demo `loop.py:41-44, 216-220`；README `:317-321` |
| C6.4 | 我们自建等价实现时，可在内部**统一折回 action 判别式**（官方 quickstart 正是这么做的：`toolset(action=name, **tool_input)`），从而一套执行代码同时支持两种形态。 | demo `tools/collection.py:45-55`；README `:311-316` |

---

## 5. 存疑与未覆盖项

1. **官方文档站本机不可达**（地域屏蔽，返回 "App unavailable in region"），因此本报告的"官方文档"证据全部来自**官方 SDK 源码/docstring**与**官方 quickstart 代码/测试**。若需引用平台文档原文（`platform.claude.com/docs/en/agents-and-tools/tool-use/computer-use-tool`）中的措辞，需在可访问网络的环境补抓。
2. **`computer-use-best-practices/constants.py` 未能取得**（raw 404 / contents API 404 / blob API 404，且同分钟两次 tree 查询结果不一致——上游正在改动）。`px_per_token=28`、`max_edge_px=1568`、`max_tokens=1568` 三个值来自 `image.py` 自身 docstring，并由 `tests/test_image.py` 的 `cfg.max_edge_px` / `cfg.max_tokens` 断言间接证实，但**未直接读到常量定义行**。
3. **API type 的发布日期**（`computer_20251124` / `computer_toolset_20260801`）来自类型名中的日期串，**未**从发布说明独立核实。
4. **`_base.py` 的 "rejects an oversized one rather than scaling it down"** 与 `image.py` 的 "the server resizes it *again*" 的关系，本文按"分块上限（静默缩） vs image block 硬限额（报错）"来解读，**属推断**，见 §3(c) 末尾。
5. **Windows/DPI 侧**完全无官方实现可参照（官方两套实现分别面向 X11/Linux 与 macOS）。契约 C4.4 / C4.5 的 DPI 分层是**我们的设计选择**，只借鉴了 best-practices 的 retina 处理思路。

---

## 6. 引用索引

**SDK（anthropic 1.15.0，PyPI）**
- `anthropic/types/computer_zoom_input.py`（全文 17 行）— zoom input 与 docstring
- `anthropic/types/beta/beta_computer_zoom_input.py`（同源）
- `anthropic/types/computer_zoom_tool_use_block.py:19-29` — `name`/`toolset_name` 判别式
- `anthropic/types/computer_member_name.py:5-43` — 17 个成员动作
- `anthropic/types/computer_member_input.py:24-42` — 成员 input 联合类型
- `anthropic/types/computer_toolset_20260801_param.py:12-34` — toolset docstring
- `anthropic/types/beta/beta_computer_toolset_20260801.py:11-33` — 同上（model 版）
- `anthropic/types/computer_toolset_configs_param.py:27-86` — `configs.zoom` 等逐成员配置
- `anthropic/types/computer_zoom_config_param.py:9-23` — `zoom.enabled` / `defer_loading`
- `anthropic/types/beta/beta_tool_computer_use_20251124_param.py:42-43` — `enable_zoom`
- `anthropic/types/beta/beta_tool_computer_use_20250124_param.py`（全文 45 行）— 无 `enable_zoom`
- `anthropic/lib/tools/_toolsets/_computer.py:145, 253-257, 392, 396-407` — 成员约定 / zoom 抽象方法 / 形状校验
- `anthropic/lib/tools/_toolsets/_base.py:86-94` — `BetaScreenshotResult`
- `anthropic/lib/tools/_toolsets/_computer_results.py:26-31` — cursor in screenshot pixels
- `anthropic/tools/computer.py:1-6` — 指向 `computer-toolset.md` 指南（该 md 未随 wheel 分发）

**claude-quickstarts @ main**
- `computer-use-demo/computer_use_demo/tools/computer.py:21-46, 56-62, 220-226, 228-250, 257-265, 279-302, 428-433, 435-473, 475-509, 512-537, 543-579`
- `computer-use-demo/computer_use_demo/tools/collection.py:26-31, 45-55`
- `computer-use-demo/computer_use_demo/loop.py:41-44, 210-235, 342-382`
- `computer-use-demo/tests/tools/computer_test.py:252-328`
- `computer-use-demo/README.md:40, 167-173`
- `computer-use-best-practices/computer_use/image.py:1-15, 29-35, 38-75, 78-102`
- `computer-use-best-practices/computer_use/tools/computer.py:1-9, 190-194, 213-219, 225-230, 255-275, 277-294, 423-456`
- `computer-use-best-practices/tests/test_image.py:15-49`
- `computer-use-best-practices/tests/test_zoom.py:1-23`
- `computer-use-best-practices/README.md:40-42, 199-206, 304-329`

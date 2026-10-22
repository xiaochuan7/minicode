# r-r3 · 提示词协议（prompt protocol）调研

> 调研线：**提示词该怎么写？**
> 仓库：`C:\Users\22197\Desktop\minicode`
> 日期：2026-10-22
> 产出：本文件是唯一交付物；未修改任何其他文件（只读工作区）。

---

## 0. 结论先行（一句话版）

**我们的提示词在"格式"上基本没错（问框、落点取框中心、坐标是这张图的像素 —— 与 ScreenSpot-Pro 官方协议一致），缺的是三样东西：① 模型自报"它选中的是哪个元素"的文本字段（用来把 58.5% 的"挑错元素"变成可检出的错）；② crop 内精定位的"没找到"逃生口（36.1% 的窗口压根没框住目标，现在仍被逼出一个假点）；③ 指令要指向"屏幕上的原文"而不是用户的转述（M14 已实测这类偏 27~30 px）。** 另外一条必须记住的**接线约束**：任何改 grounding 提示词的措辞，都必须同步改 `calib/prompt.py` 的标定探针提示词 —— 否则标定测的就不是生产那条约定，分母来源当场失效。

---

## 1. 方法与证据边界（请先读这一节）

### 1.1 web 工具调用清单（合计 13 次，其中 4 次是超时/失败后的重试）

| # | 工具 | 目标 | 结果 |
|---|---|---|---|
| 1 | web_search | OpenAI computer-use 坐标口径 / Qwen2.5-VL grounding 提示词 / Anthropic zoom | 摘要可用（URL 已记） |
| 2 | web_fetch | `raw.githubusercontent.com/QwenLM/Qwen3-VL/master/README.md` | **HTTP 200，全文已读** |
| 3 | web_fetch | Qwen3-VL `cookbooks/computer_use.ipynb` | **超时**（30 s） |
| 4 | web_fetch | `platform.claude.com/docs/.../computer-use-tool` | 跨源跳转错误（→www.anthropic.com） |
| 5 | web_search | Qwen3-VL issue #2020 / UI-TARS 模板 / OS-Atlas·ShowUI 模板 / 框 vs 点对比 | 摘要可用 |
| 6 | web_fetch | `raw.githubusercontent.com/bytedance/ui-tars/main/codes/ui_tars/prompt.py` | **超时**（8 s connect） |
| 7 | web_fetch | 同上（重试 1 次） | **超时** |
| 8 | web_fetch | `www.anthropic.com/docs/en/.../computer-use-tool` | **404** |
| 9 | web_fetch | `docs.claude.com/en/docs/.../computer-use-tool` | 跨源跳转错误（→platform.claude.com） |
| 10 | web_fetch | `developers.openai.com/api/docs/guides/tools-computer-use` | **403 Forbidden** |
| 11 | web_fetch | `api.github.com/repos/QwenLM/Qwen3-VL/issues/2020` | **HTTP 200，JSON 全文已读** |
| 12 | web_fetch | `platform.claude.com/docs/...`（重试 1 次） | 跨源跳转错误 |
| 13 | web_search | gpt4x.py 原文 / Qwen2.5-VL 官方模板 / 候选编号消歧 / 框 vs 点 | 摘要可用 |

**未能取到一手原文的三处（因此下文只能标【二手】）**：Anthropic computer use 官方文档页、OpenAI computer use 官方文档页、UI-TARS `prompt.py` 原文。它们的**内容**仍可从别处交叉印证（下文逐条标了出处），但**逐字原文**请以下列 URL 为准：
- Anthropic：`https://platform.claude.com/docs/en/agents-and-tools/tool-use/computer-use-tool`（本次 3 次跳转失败；浏览器直接打开即可）
- OpenAI：`https://developers.openai.com/api/docs/guides/tools-computer-use`（本次 403；浏览器可开）
- UI-TARS：`https://github.com/bytedance/ui-tars/blob/main/codes/ui_tars/prompt.py`

### 1.2 证据强度标记约定

- **【一手】** = 本轮**实际 fetch 到 HTTP 200 并逐字读过**。本报告只有两个来源够格：Qwen3-VL README、Qwen3-VL issue #2020。其余全部是【二手】（搜索引擎摘要 / 论文引用，未逐字验证），或【推断】（我的推理，无来源直接支持）。

---

## 2. 各家官方 grounding 提示词原文与格式（对照表）

| 来源 | 提示词原文 / 动作格式 | 坐标空间 | 输出几何 | 证据 |
|---|---|---|---|---|
| **Qwen2.5-VL**（官方 blog + finetune README + Ground-R1 引用） | `Locate {ref} in this image and output the bbox coordinates in JSON format.` | 绝对像素（相对**resize 后**的图） | `[{"bbox_2d":[x1,y1,x2,y2],"label":"..."}]`，**每对象只给一个几何字段** | 【二手】`https://qwenlm.github.io/blog/qwen2.5-vl/` ；`https://github.com/QwenLM/Qwen2.5-VL/blob/main/qwen-vl-finetune/README.md` |
| **Qwen3-VL**（官方 cookbook + README） | `Locate every instance that belongs to {category} in this image and output the bbox coordinates in JSON format.`；2d_grounding 教程明确写「默认坐标系已从 Qwen2.5-VL 的绝对坐标改成 **0~1000 相对坐标**」 | **相对 0~1000** | 同时支持 box 与 point：「Using relative position coordinates, it supports both boxes and points」 | 【一手】README（cookbooks 表 + pixel budget 段）；【二手】cookbook 引文 `https://github.com/QwenLM/Qwen3-VL/blob/main/cookbooks/2d_grounding.ipynb` |
| **UI-TARS**（ByteDance） | 1.0：`click(start_box='<\|box_start\|>(x1,y1),(x2,y2)<\|box_end\|>')`；1.5/2.x 改成**单点** `(x,y)`；三套 prompt 模板在 `codes/ui_tars/prompt.py` | 相对 0~1000（相对输入图宽高分别换算） | 1 个几何值（点） | 【二手】`https://github.com/bytedance/ui-tars` ；`https://seed-tars.com/1/` ；`https://arxiv.org/abs/2501.12326`；实测复现失败的 issue `https://github.com/bytedance/UI-TARS/issues/215` |
| **OS-Atlas**（上海 AI Lab） | Grounding 模式：由指令预测元素坐标 | — | bbox = `[left, top, right, bottom]` | 【二手】`https://github.com/OS-Copilot/OS-Atlas` ；数据字段 `https://huggingface.co/datasets/OS-Copilot/OS-Atlas-data` |
| **OpenAI computer use** | 工具 `environment` 块声明 `display_width`/`display_height`；动作 `{"type":"click","x":..,"y":..}`；官方建议在提示词里写明视口尺寸与坐标范围（例："all click coordinates must be within 0–1024 (x) and 0–768 (y)"） | 声明的 display 空间（**两轴各自**） | 单点 | 【二手】`https://developers.openai.com/api/docs/guides/tools-computer-use`；社区帖 `https://community.openai.com/t/how-are-the-computer-use-api-coordinates-calculated/1143891`（建议沿用文档示例 1024×768） |
| **Anthropic computer use** | `computer_20250124` 起有 **`zoom`** 动作：`coordinate`(x,y) 或 `region`(x1,y1,x2,y2)，**返回放大后的图**；系统提示词引导"要看小字/细节就用 zoom" | 仍报**整屏**坐标 | 单点 | 【二手】`https://platform.claude.com/docs/en/agents-and-tools/tool-use/computer-use-tool`；quickstarts `https://github.com/anthropics/anthropic-quickstarts` |
| **GUIRILLA**（复述式 grounding） | `Your task is to describe the action needed to click this element. Guidelines: 0. If the element is not perfectly selected (ex. partially), the box is …` | — | bbox | 【二手】`https://openreview.net/pdf/f554c631613faba03725824ba5ef437a37bd4bab.pdf` |
| **ScreenSpot-Pro（评测协议，非提示词）** | Table 3 题注逐字：*"For models inferencing boxes, we consider the center point of the generated box as the prediction"*；判定 = **落点在 GT bbox 内**即算命中 | — | — | 【二手】`https://arxiv.org/abs/2504.07981`（PDF `https://likaixin2000.github.io/papers/ScreenSpot_Pro.pdf`）；同款约定见 Phi-Ground `https://arxiv.org/abs/2507.23779` |

### 2.1 我们现在用的提示词（逐字，源码为准）

`src/minicode/ground/prompt.py`

- **coarse_prompt**（L114-138，问框 + 问点，两个字段）：
  ```
  这是一张 Windows 桌面截图。
  这张图是 {W}×{H}。
  请在图里找到与下面这个指令对应的界面元素，并给出它的外接矩形（bounding box）。
  指令：{target_description}
  用 JSON 回答，格式：{"bbox": [左, 上, 右, 下], "point": [x, y]}（坐标是这张图的像素）。
  bbox 是这个元素在图里的外接矩形的左上角与右下角，point 是它的中心点。
  不要输出分析。
  ```
- **refine_prompt**（L141-157，crop 内，问点）：
  ```
  这是一张从 Windows 桌面截图里裁出的小区域（未缩放，1 像素 = 1 屏幕像素）。
  这张图是 {W}×{H}。
  请给出这张图里名为「{target}」的元素【中心】的位置。
  用 JSON 回答，格式：{"x": 数字, "y": 数字}
  请给这张图的像素坐标（直接给坐标即可，不用解释你的坐标范围）。
  ```
- **whole_screen_prompt**（L160-192）：粗定位重试，带 `retry_reason`。
- **numbered_candidates_prompt**（L195-234）：模型**只答编号**，落点取候选框物理中心（已有"清单里没有 → 答 0"的逃生口）。
- `src/minicode/ground/zoom.py::zoom_region_prompt`（L79-100）：zoom 的图内坐标契约。
- `src/minicode/calib/prompt.py::image_prompt`（L91-115）：标定探针（4 个方块中心）。

**代码里已有的权威转化**：`src/minicode/ground/channel.py::box_center`（L227-246）——**有 bbox 就取框中心，没有 bbox 才用 point**。也就是说：**`coarse_prompt` 里那个 `point` 字段在生产路径上其实是死字段**（只在模型没给框时才被用到）。

---

## 3. 逐条对比：官方有、我们没有的

| # | 维度 | 官方做法 | 我们的现状 | 缺口判定 | 证据 |
|---|---|---|---|---|---|
| A | **坐标空间声明** | OpenAI/Anthropic：工具 schema 里声明 display_width/height，坐标就在该空间；提示词里写明范围（0–W / 0–H） | 写了「这张图是 W×H」+「坐标是这张图的像素」 | **基本有**；缺"原点左上、x 右、y 下"这类朝向声明。但朝向错误我们的标定探针已能抓（四角目标，翻转会产生大误差）→ **低价值** | 【二手】OpenAI 文档 |
| B | **输出几何：只给一个还是给两个** | Qwen2.5-VL/3-VL：每对象**一个** `bbox_2d`；UI-TARS 1.5/2.x：**一个**点；OS-Atlas：**一个** bbox | coarse **同时要 bbox 和 point** | **缺口（真）**。两个权威几何可以互相矛盾，而代码只信 bbox；等于让模型做两遍题、还要自己消歧 | 【一手】Qwen3-VL README（cookbooks 表）；【二手】Qwen2.5-VL/UI-TARS/OS-Atlas |
| C | **"不要点错元素"的约束** | **没有任何一家的官方 grounding 提示词包含否定式约束**（"不要选错"之类）。它们都是正向、祈使、单句 | 我们也没写否定式约束 | **一致，不需要补**。若要治"挑错元素"，正向做法是给消歧信息（surface text / 候选清单），不是加禁令 | 【二手】全部官方模板 |
| D | **候选编号** | grounding 模型不用编号；但 UI grounding 研究里"先枚举再选择"是标准消歧范式（UI-E2I-Synth；VenusBench-GD 报告**候选集缩小 25×–50× 时 grounding 准确率的公开数字**） | 我们**已有** `numbered_candidates_prompt`（模型只答编号、落点取候选框中心、答 0 表示没有）——但**只在 UIA 候选源存在时走**；crop 那一级没有候选 | **部分有，缺"crop 级的候选编号"**；不过 crop 级候选要 OCR，代价大 → 排在后面 | 【二手】`https://aclanthology.org/2025.findings-acl.809.pdf`；`https://arxiv.org/html/2512.16501v1` |
| E | **单 token / 严格 JSON 输出** | 官方 grounding 模型用专用 token（`<\|box_start\|>`），训练模型不产散文 | 我们要求 JSON + "不要输出分析" | **已对齐**（对一个通用 API 模型这是对的） | — |
| F | **指称表达（referring expression）的质量** | 官方评测用的是**人写、确认无歧义**的指称表达；UI-E2I-Synth 指出 RE 带"视角"，同一 widget 会有多个合法 RE | 我们传的是**用户转述**（M14 已实测：工单写「显示」而本机卡片叫「屏幕」→ 偏 27~30 px） | **缺口（真，且本地已验证）**。提示词应指向**屏幕上的原文** | 【二手】UI-E2I-Synth；本地 M14 实测（见 `ground/prompt.py` docstring） |
| G | **模型自报"我选的是哪个"** | 官方 grounding 模型不需要（它们是训出来的） | 无 | **缺口（真，对通用 API 模型尤其关键）**：我们的头号错因是"挑错元素"，但**当前答案里没有任何可校验的字段** | 【推断】+ 本地错因分层（挑错元素 107/183） |
| H | **"没找到"的逃生口** | 官方 grounding 模板没有（它们必须给答案）；但候选清单范式有（我们自己的 `numbered_candidates_prompt` 就让模型答 0） | coarse/refine **都没有**：模型必须给一个坐标 | **缺口（真）**：36.1% 的 crop 窗口没框住目标，此时模型被迫在错图上指一个点 → 制造"看起来精定位成功"的假命中 | 【一手】本地诊断（36.1%）；【二手】候选范式 |
| I | **语言与句式** | 官方 grounding 提示词几乎全是**英文单句祈使**（"Locate … in this image and output the bbox coordinates in JSON format."） | 全中文、多行、带 schema 说明 | **待验证**，【推断】：脚手架英文化可能更贴合 grounding 训练分布，但目标短语必须保持原文 | 【二手】Qwen2.5-VL/3-VL 模板 |
| J | **坐标量纲不得由提示词决定** | 官方模型量纲是训练决定的（Qwen2.5-VL 绝对、Qwen3-VL 相对 0~1000） | 我们用**运行时标定**决定（`coord/calibrate.py`，候选集 `RELATIVE_0_1 / RELATIVE_0_1000 / RELATIVE_0_999 / ABSOLUTE_CAPTURE / ABSOLUTE_MODEL_INPUT`） | **我们比官方更严谨，保留**。但因此：提示词里**一个字都不能改量纲** | 【一手】`src/minicode/coord/coordinates.py:91-122`；【一手】Qwen3-VL README（Qwen3-VL 默认坐标系变更） |
| K | **标定探针与生产提示词的措辞耦合** | — | 探针问的是"4 个方块中心"（**显著物**的坐标），生产问的是"按指称表达定位"（**语义绑定**）。探针能把 tl/tr/bl/br 讲清 → 探针比生产**容易**，探针过 ≠ 绑定对 | **缺口（真）**：探针不测"描述→元素"的绑定，只测"显著物→坐标"的量纲 | 【推断】+ 本地源码 |

---

## 4. 「问框（返回 bbox）+ 落点取框中心」vs「问点」：公开对比数字

### 4.1 先说结论

**没有"同一个通用 API 模型、只改输出格式、其余不变"的公开 A/B。** 最接近的三类证据都不直接可比，逐条给出并说明为什么不直接可比：

| 公开数字 | 出处 | 它回答的是什么 | 为什么不能直接套到我们身上 |
|---|---|---|---|
| "For models inferencing boxes, we consider the center point of the generated box as the prediction"（Table 3 题注） | ScreenSpot-Pro `https://arxiv.org/abs/2504.07981` | **评分口径**：框模型按框中心算落点 | 这是"怎么算分"，不是"该问框还是问点" |
| GUI-Actor 消融：**用坐标生成（bbox 与 point 两种监督）训练的模型一致地不如**坐标无关的注意力指针模型；ScreenSpot-Pro：GUI-Actor-7B **40.7**（Qwen2-VL）/**44.6**（Qwen2.5-VL） | `https://arxiv.org/abs/2506.03143` | **训练目标层面**点 vs 框 | 混淆了"格式"与"机制"（指针 vs 文本坐标回归），且是**训过的模型**；我们是通用 API 模型 |
| UI-TARS **1.0 → 1.5/2.x** 从 bbox 改成**单点**；72B ScreenSpot-Pro **37.1~38.1** | `https://github.com/bytedance/ui-tars`；`https://arxiv.org/abs/2501.12326` | 官方 agent 把 bbox 换成点 | 是**模型版本**的升级（底模更强），不是受控的提示词实验；且是 ReAct agent 场景 |

**因此 →「问框 vs 问点」的公开受控数字 = 不适用（不存在）。** 我们要拿到这个数字只能自己测，且必须按现有评测台的口径做（见 §6 第 8 条）。

### 4.2 但有三条**间接但可用**的公开证据

1. **【一手】Qwen3-VL issue #2020**（`https://github.com/QwenLM/Qwen3-VL/issues/2020`，2026-02-02 开、2026-02-06 关，state_reason=completed，4 条评论）——标题："The bounding box coordinates can sometimes be significantly off."。报告者用 0~1000 相对坐标还原绝对坐标后，框**显著偏移**；给出的失败样例里，模型在 1380×245 的宽图上返回 4 个 **`[323,118,444,825]`、`[444,118,565,825]`、`[565,118,686,825]`、`[686,118,807,825]`** —— 即**y 区间与宽度完全相同、沿 x 机械平铺**的退化输出。报告者结论："it tends to happen more often with images whose aspect ratio is extremely far from square, such as very wide or very tall ones."
   → **对我们的意义（【推断】）**：① "框"这种四参数输出在**长宽比极端**时更容易整片退化，而"点"只有两参数、更难整片错；② 我们的整屏图 2560×1600（1.6:1）、模型输入 1280×800（1.6:1），crop 是 192×192（1:1），**都不极端**，所以这条对我们**不构成直接风险**；③ 但它提示了一个**必须守住的纪律**：两轴必须各自按宽/高换算，任何用单一标量的实现都会在 1.6:1 的图上系统性偏移 —— 我们的 `CoordinateAdapter.model_to_relative`（`coord/coordinates.py:350-363`）对相对标度是 `mx/denom, my/denom` 后**分别**乘 `capture_width`/`capture_height`，**没有单一标量**，✅ 这一条我们天然是对的。
2. **【一手】Qwen3-VL README 的像素预算**：官方写明 Qwen3-VL 压缩比 32（patch 16），单图视觉 token 可设 256~1280（`processor.image_processor.size = {"longest_edge": 1280*32*32, "shortest_edge": 256*32*32}`）。→ 1280 token ≈ 1,310,720 像素预算。**对我们的意义（【推断】）**：我们送整屏 1280×800 = 1,024,000 像素，正落在这个量级内 —— 说明"整屏 1280×800"不是一个随便定的数，它是 Qwen 系视觉 token 预算的自然上限档。通用 API 模型没有这个约束，但**这条给了我们一个对外解释口径**（我们的 1280×800 与 Qwen3-VL 的 1280-token 预算同量级）。
3. **【二手】ScreenSpot-Pro 视觉搜索法**：OS-Atlas-7B **18.9% → 48.1%**（不训练，靠视觉搜索）；RegionFocus（Qwen2.5-VL-72B）**61.6%**。→ **"先粗定位、再在小区域里定位"这个两段式架构有公开数字支撑**（我们的 crop 那一级方向正确），且**收益主要来自"缩小候选空间"而不是"换输出格式"**。

### 4.3 所以：框 vs 点，我们的判断

- **粗定位（整屏）继续问框**：框还携带**目标半径**（`channel.py` 里 `coarse_radius_px` → `plan_crop(target_radius_px=…)` → crop 窗口"不裁切目标"），这个信号**只问点是拿不到的**，而 36.1% 的窗口没框住目标正说明半径信息值钱。**保留框。**
- **粗定位不要同时问点**：改成**只问一个几何**（官方全部如此），消掉 bbox/point 互相矛盾这一类歧义。代码侧 `box_center` 本来就优先 bbox，`point` 是死字段 → **删字段是纯赚**。
- **精定位（crop 内）继续问点**：已经是问点，且 crop 内元素小、框的 4 条边比中心点更难摆准（【推断】+ 【二手】issue #2020 的退化证据）。**不动。**
- **真要回答"问框 vs 问点"的净收益，只能自测**，测法见 §6 第 8 条。

---

## 5. 可直接照抄的提示词模板

> 约定：**量纲一个字都不写**（分母只能来自 `coord/calibrate.py` 的标定结果，架构备忘 C-04）。
> 只写三件与量纲无关的事：**图是谁**（哪张图、多大）、**要什么**（一个几何 + 一个可选的表面文字）、**格式**（JSON，字段名固定）。
> 任何改动**必须同步改** `src/minicode/calib/prompt.py::image_prompt`（否则标定测的不是生产那条约定）。

### 5.1 粗定位模板 v3（英文原文 + 中文对照）

**English (verbatim, ready to paste)**

```
This is a screenshot of a Windows desktop. The image is 1280x800 pixels.
The origin (0, 0) is the top-left corner; x increases to the right, y increases downward.

Find the UI element that matches the instruction below and return its bounding box.
Instruction: {target_description}
Visible text on that element (if you can read it): {surface_text_or_empty}

Answer with a single JSON object and nothing else:
{"bbox": [left, top, right, bottom], "text": "<the visible text on the element, empty string if none>"}

- bbox is the tight axis-aligned rectangle around the element itself, not its container.
- text is the text you can actually read inside that rectangle.
- If the element is not visible in this image, answer {"bbox": null, "text": ""}.
```

**中文对照**

```
这是一张 Windows 桌面截图。这张图是 1280×800 像素。
原点 (0,0) 在左上角；x 向右增大，y 向下增大。

请找到与下面指令对应的界面元素，给出它的外接矩形。
指令：{target_description}
该元素上可见的文字（读得到就填）：{surface_text_or_empty}

只回答一个 JSON 对象，不要任何其他内容：
{"bbox": [左, 上, 右, 下], "text": "<元素上读得到的文字，没有就空字符串>"}

- bbox 是元素**自身**的紧致外接矩形，不是它所在的容器。
- text 是那个矩形里**实际读得到**的文字。
- 如果这张图里根本没有这个元素，回答 {"bbox": null, "text": ""}。
```

**相对我们现在的三处实质改动**：
1. `point` 字段**删除** → 只剩一个几何权威（官方格式：B）。
2. 新增 `text` 字段 → 模型自报它选中的元素上的文字（缺口 G）。
3. 新增 `{"bbox": null, "text": ""}` 逃生口 → 元素不可见时不必硬编一个框（缺口 H）。

### 5.2 精定位模板 v3（crop 内，英文原文 + 中文对照）

**English (verbatim)**

```
This is a small region cropped from a Windows desktop screenshot, shown at 1:1 (1 pixel here = 1 screen pixel).
The image is 192x192 pixels. The origin (0, 0) is the top-left corner of THIS crop; x increases to the right, y increases downward.

Locate the element named "{target_description}" in this image and return its click point.
Visible text on that element (if you can read it): {surface_text_or_empty}

Answer with a single JSON object and nothing else:
{"x": number, "y": number, "text": "<the visible text on the element, empty string if none>"}

- x, y is the center of the element itself.
- text is the text you can actually read on that element.
- If the element is NOT in this crop, answer {"x": null, "y": null, "text": ""}.
```

**中文对照**

```
这是一张从 Windows 桌面截图里裁出的小区域，未缩放（这里 1 像素 = 1 屏幕像素）。
这张图是 192×192 像素。原点 (0,0) 在**这张裁剪图**的左上角；x 向右增大，y 向下增大。

请在这张图里找到名为「{target_description}」的元素，给出它的点击点。
该元素上可见的文字（读得到就填）：{surface_text_or_empty}

只回答一个 JSON 对象，不要任何其他内容：
{"x": 数字, "y": 数字, "text": "<元素上读得到的文字，没有就空字符串>"}

- x, y 是元素**自身**的中心。
- text 是那个元素上**实际读得到**的文字。
- 如果这个元素**不在这张裁剪图里**，回答 {"x": null, "y": null, "text": ""}。
```

**相对我们现在的三处实质改动**：
1. 新增 `text` 字段（缺口 G）。
2. 新增 `{"x": null, "y": null}` **逃生口**（缺口 H）—— 这是本条里**最重要**的一处：36.1% 的窗口没框住目标，现在模型仍被迫给一个点，那个点会被当成"精定位成功"接受（只受 50 px 判据约束），从而把粗定位的正确答案覆盖掉。
3. 显式声明"原点在这张裁剪图的左上角" —— 与 `ground/prompt.py` docstring 里"不许提裁剪原点"**不冲突**：我们禁的是**物理原点数值**（提了模型会改报整屏坐标），这里给的是**这张图自己的**朝向，属于图内几何。

### 5.3 粗定位失败重问模板（接 `whole_screen_prompt` 的 `retry_reason` 槽）

```
This is a screenshot of a Windows desktop. The image is 1280x800 pixels.
The origin (0, 0) is the top-left corner; x increases to the right, y increases downward.

Previous attempt: {retry_reason}
Surface text you read on the element you picked last time: {previous_text_or_empty}

Find the UI element that matches the instruction below and return its bounding box.
Instruction: {target_description}
Visible text on that element (if you can read it): {surface_text_or_empty}

Answer with a single JSON object and nothing else:
{"bbox": [left, top, right, bottom], "text": "<the visible text on the element, empty string if none>"}

- If the element is not visible in this image, answer {"bbox": null, "text": ""}.
```

**中文对照**

```
这是一张 Windows 桌面截图。这张图是 1280×800 像素。
原点 (0,0) 在左上角；x 向右增大，y 向下增大。

上一次的结果：{retry_reason}
上一次你选中的元素上读到的文字：{previous_text_or_empty}

请找到与下面指令对应的界面元素，给出它的外接矩形。
指令：{target_description}
该元素上可见的文字（读得到就填）：{surface_text_or_empty}

只回答一个 JSON 对象，不要任何其他内容：
{"bbox": [左, 上, 右, 下], "text": "<元素上读得到的文字，没有就空字符串>"}

- 如果这张图里没有这个元素，回答 {"bbox": null, "text": ""}。
```

**为什么值得单独一条**：这是把"模型自报的 `text`"变成**闭环**的地方 —— 上一次报的文字和我们想要的不一致，就把不一致本身喂回去让它重找。`ground/prompt.py::whole_screen_prompt` 已经有 `retry_reason` 参数，**不需要新接口**，只要把 `previous_text` 填进去。

---

## 6. 最小改动清单（≤8 条，按投入产出排序）

> 每条：**改哪个文件** → **做什么** → **预期哪个数变多少** → **怎么验**。
> "预期"一栏凡属未实测的，一律标【推断】；验收口径统一用现有 ScreenSpot-Pro 抽样台（`minicode/ground/numbered_bench.py`，含"七个尺寸共用同一次粗定位回答"的复用机制，可直接支撑配对 A/B）。

| # | 文件 | 做什么 | 预期（口径见括号） | 怎么验 |
|---|---|---|---|---|
| **1** | `src/minicode/ground/prompt.py::refine_prompt`（L141）+ `ground/channel.py::_locate_by_point` 的 refine 解析处 | 加 `{"x": null, "y": null, "text": ""}` **逃生口**：模型判定"元素不在 crop 里"时，**放弃精定位、干净回落粗定位**（而不是接受一个假点）。解析器需要新增 null 判定 | **精定位"假命中"归零**：36.1% 未框住目标的样本不再产出一个被 50 px 判据误放行的点。落点正确率【推断】不变或微升（坏点被换成粗点，粗点中位误差 < 假点） | 在 183 条 ScreenSpot-Pro 抽样上重跑：分「元素在 crop 内 / 不在」两桶，比较"精定位点与真值距离"的 p90；预期"不在"桶从"中位 226 px 级"降到粗定位的量级 |
| **2** | `src/minicode/ground/prompt.py::coarse_prompt`（L114）+ `refine_prompt` + `ground/channel.py::box_center`（L227） | **删掉 `point` 字段**，粗定位只问 `bbox`（官方全部是"每对象一个几何"）。解析器已支持只有 bbox（`parse_coarse_target` 的 `{"bbox":[...]}` 分支），**零解析改动**；`box_center` 本来就优先 bbox | **几何歧义归零**（现状模型可同时给两个互相矛盾的数）。落点准确率**预期不变**（因为我们一直只用 bbox 中心）；但**粗定位"框的宽度/高度"质量**可能改善（少一个要摆准的量）→ 目标半径估计更准 → crop 窗口"没框住目标"比例**【推断】从 36.1% 降 3~8 pp** | 对同一批样本跑 prompt 变体 A/B，**复用同一次粗定位回答**（台子已支持），比较 bbox 面积中位数与"crop 框住目标"比例 |
| **3** | `src/minicode/ground/prompt.py::coarse_prompt` / `refine_prompt` / `whole_screen_prompt` | 加 `text` 字段（模型自报选中元素上的可见文字）+ **校验闭环**：粗定位的 `text` 与目标描述/OCR 词表不匹配 → 触发 `whole_screen_prompt` 重问，并把 `previous_text` 喂回去（§5.3） | **这是唯一直接打"挑错元素"（107/183 = 58.5%）的改动**。目标：把不可见的错变成可检出的错 —— **预期可检出比例 ≥ 50%**（即 107 里至少一半能从"静默错"变成"显式重问后被纠正"），落点准确率**【推断】+3~6 pp**（重问成功的那部分） | 落盘字段 `grounding_echoes` 里加 `model_text`；统计"text 与目标不匹配"的样本中，重问后命中比例 vs 不重问的对照（同一次粗定位回答复用，McNemar 精确检验） |
| **4** | `src/minicode/ground/prompt.py`（三处提示词同改）+ `src/minicode/ground/channel.py::_normalize_description` | 把**屏幕上的原文**（`surface_text`）传进提示词，而不是只传用户转述。我们已有 `ground/prompt.py::normalize_target`（B-4）与 `MAX_TARGET_NAMES=5` 的名字清单端口 | 直接治 M14 已实测的那类偏（**27~30 px**）。命中提升**【推断】+1~3 pp**；成本：提示词变长、token +20~40 | 只改提示词填充、不改换算；用现成 ScreenSpot-Pro 抽样做配对 A/B |
| **5** | `src/minicode/calib/prompt.py::image_prompt`（L91）+ `src/minicode/coord/calibrate.py` | **同步纪律**：改 grounding 提示词的任何措辞，必须同步改标定探针提示词（否则标定测的不是生产那条约定，分母来源当场失效，C-04）。具体动作：把探针的提问句式改成与 v3 生产模板**同构**（同样的 JSON 字段名、同样的"原点左上"句），并给探针**加一个"按描述定位"的维度**（不只问"显著方块中心"，还要问"用一句话描述它"的定位） | **不直接改命中率**，但它把"标定通过"从"量纲对"升级为"量纲 + 绑定都对"；预期能提前暴露**描述→元素绑定**的失败（当前探针四目标全靠方位描述，**比生产容易**） | 标定 CLI 跑一遍，要求新探针在桩模型上仍能唯一判定 5 个候选标度（现有 `calibrate.py --all-scales` 已覆盖） |
| **6** | `src/minicode/ground/prompt.py` | **语言/句式 A/B**（不改语义，只换脚手架）：A = 现状中文多行；B = §5.1 英文祈使 + 中文目标短语；C = 中文祈使单句（去掉 schema 说明行） | 【推断】B 或 C 对"绑定"更稳（官方 grounding 提示词几乎全是英文单句祈使）。**预期幅度不确定（±2 pp）**，必须实测 | 同一批样本、复用同一次粗定位回答，三臂配对；主指标 = 命中数差 + McNemar p 值 |
| **7** | `src/minicode/ground/channel.py`（粗定位调用处，`effort` 参数） | 粗定位这一级的 `effort` 从 `low` 提到 `medium` 跑一臂（decision/harness.py L71 已把定位类调用钉在 `low`） | 【推断】thinking 更充分的通用模型在"挑错元素"上更稳，**预期 +1~4 pp**，代价是延迟与 token 上升约 1 倍 | 同批样本配对 A/B；**必须同时报** 平均耗时/目标、token/目标，否则会用一个换来的指标掩盖另一个 |
| **8** | `src/minicode/ground/numbered_bench.py`（评测台）+ `ground/caliber.py` | 落点判据改成**"落在 GT bbox 内即算命中"**（与 ScreenSpot / ScreenSpot-Pro / Phi-Ground 一致），并加一臂"**问框+框中心** vs **只问点**"的受控对比 | 这是"公开数字没有、必须自测"的那一条（§4.1）。预期结论（【推断】）：**两臂差 ≤ 1 pp**，真正的大头在"选没选对元素"（58.5%）而不在输出格式 —— 这会直接决定我们**不**要把精力花在框/点之争上 | 直接给出那个公开不存在的受控数字；与 ScreenSpot-Pro 官方口径一致后，我们的分数才能和 UI-TARS 37~38 / GUI-Actor 40.7~44.6 / RegionFocus 61.6 同表比较 |

**关于第 8 条的口径提醒**：我们当前用"落点 ≤ 2 px / 50 px 偏离"判命中（架构备忘 §2.6 C-16 那条 50 px 判据），而 **ScreenSpot/ScreenSpot-Pro 判的是"落点在 GT bbox 内"**（见 §2 表最后一行与 §4.1）。【二手】证据。**改判据会让我们自己的分数整体上升**，所以**改口径的那一轮必须与其它 A/B 分开记录**，否则新旧数字不可比 —— 这也是评估线（r-r1）已经提的口径问题，此处只做提示。

---

## 7. 一句话给决策者的版本

1. **不要改的**：量纲（标定说了算）、"问框 + 框中心"这个协议本身（与官方一致）、不要加"不要点错"这种否定约束（官方模板里根本没有，加了大概率无效）。
2. **要改的**（按性价比）：crop 内加"没找到"逃生口（#1）→ 粗定位删掉 `point` 字段（#2）→ 加 `text` 自报字段并接成重问闭环（#3）→ 指令改指屏幕原文（#4）。
3. **要同步的**：标定探针提示词必须跟着改（#5），否则上面全白改。
4. **要自测的**："问框 vs 问点"的受控数字公开不存在（#8），自己测，预期结论是**这不是主战场**，主战场是"挑错元素"（58.5%）。

---

## 8. 引用清单（含证据强度）

**【一手】本轮实际 fetch 并逐字读过**
- Qwen3-VL README（cookbooks 表：2d_grounding / computer_use / mobile_agent；像素预算 256~1280 token、压缩比 32、patch 16；评测框架 VLMEvalKit + lmms-eval；Instruct 生成超参 temperature 0.7 / top_p 0.8 / top_k 20 / presence_penalty 1.5 / seed 3407）：`https://raw.githubusercontent.com/QwenLM/Qwen3-VL/master/README.md`
- Qwen3-VL issue #2020 "The bounding box coordinates can sometimes be significantly off."（退化样例 `[323,118,444,825]` 等 4 框沿 x 平铺；长宽比极端时更易发生；2026-02-06 closed/completed）：`https://github.com/QwenLM/Qwen3-VL/issues/2020`（JSON：`https://api.github.com/repos/QwenLM/Qwen3-VL/issues/2020`）

**【二手】本轮经搜索摘要/引用交叉印证，未逐字验证原文**
- Qwen2.5-VL 官方 grounding 提示词与 `bbox_2d` 输出：`https://qwenlm.github.io/blog/qwen2.5-vl/` ；`https://github.com/QwenLM/Qwen2.5-VL/blob/main/qwen-vl-finetune/README.md` ；`https://arxiv.org/abs/2505.20272`（Ground-R1 原文引用）
- Qwen3-VL 相对 0~1000 坐标系与 cookbook：`https://github.com/QwenLM/Qwen3-VL/blob/main/cookbooks/2d_grounding.ipynb` ；issue `#1560`、`#1576`
- UI-TARS 模板与动作格式：`https://github.com/bytedance/ui-tars`（`codes/ui_tars/prompt.py`）`https://seed-tars.com/1/` `https://arxiv.org/abs/2501.12326` ；复现失败 issue `https://github.com/bytedance/UI-TARS/issues/215`
- OS-Atlas grounding 输出：`https://github.com/OS-Copilot/OS-Atlas` ；`https://huggingface.co/datasets/OS-Copilot/OS-Atlas-data` ；`https://arxiv.org/abs/2410.23218`
- ScreenSpot-Pro（视觉搜索 18.9→48.1；Table 3 框中心题注；bbox 命中口径）：`https://arxiv.org/abs/2504.07981` `https://likaixin2000.github.io/papers/ScreenSpot_Pro.pdf` `https://github.com/likaixin2000/screenspot-pro-gui-grounding`
- GUI-Actor（坐标生成 vs 坐标无关指针；40.7/44.6）：`https://arxiv.org/abs/2506.03143` `https://microsoft.github.io/GUI-Actor/`
- RegionFocus（61.6）：`https://arxiv.org/abs/2507.00008`
- Phi-Ground（框中心当点击点、bbox 命中口径）：`https://arxiv.org/abs/2507.23779`
- 框内命中口径：`https://arxiv.org/abs/2406.03679`
- UI-E2I-Synth（RE 视角、同一 widget 多 RE）：`https://aclanthology.org/2025.findings-acl.809.pdf`
- VenusBench-GD（候选集缩小 25×~50×）：`https://arxiv.org/html/2512.16501v1`
- Anthropic computer use（zoom 动作、仍报整屏坐标、系统提示词引导）：`https://platform.claude.com/docs/en/agents-and-tools/tool-use/computer-use-tool` ；`https://github.com/anthropics/anthropic-quickstarts`
- OpenAI computer use（display_width/height、坐标范围提示、1024×768/1280×720 建议）：`https://developers.openai.com/api/docs/guides/tools-computer-use` ；`https://community.openai.com/t/how-are-the-computer-use-api-coordinates-calculated/1143891`
- GUIRILLA 提示词片段：`https://openreview.net/pdf/f554c631613faba03725824ba5ef437a37bd4bab.pdf`

**本地一手（源码逐行核对）**
- `src/minicode/ground/prompt.py`（coarse/refine/whole_screen/numbered 四条提示词；`box_center` 优先级规则写在 docstring L74-78）
- `src/minicode/ground/channel.py::box_center`（L227-246：有 bbox 用框中心，无 bbox 才用 point ⇒ **coarse 的 `point` 是死字段**）、`DEFAULT_CROP_WIDTH_PX=192`（L162）
- `src/minicode/ground/zoom.py::zoom_region_prompt`（L79-100，图内坐标契约）
- `src/minicode/calib/prompt.py::image_prompt`（L91-115，标定探针）
- `src/minicode/coord/coordinates.py::ModelScale`（L91-138，5 个候选标度；官方文档自相矛盾故必须运行时标定）、`CoordinateAdapter.model_to_relative`（L350-363，两轴各自换算、无单一标量）
- `src/minicode/coord/calibrate.py::CANDIDATES`（L433-438）

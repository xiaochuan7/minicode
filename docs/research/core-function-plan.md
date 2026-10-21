# 核心功能收口方案：视觉通道真的能用 + 多步任务真的能跑

> 本文是 8 条调研线（scale-calibration / vlm-precision-tactics / crop-refine / grounding-models /
> harness-multistep / windows-components / failure-modes / metrics）的收口。
> **只写方案，不改代码、不建工单。**
>
> 证据强度标注：
> **【实测】**= 本机/本项目自己量出来的数字 · **【一手】**= 官方 README / 论文原文 / 官方文档 / 源码 ·
> **【二手】**= 团队转述、榜单、博客 · **【推断】**= 本方案基于以上材料的推理，尚未验证。
>
> 仓库根：`C:\Users\22197\Desktop\minicode`（下文所有路径相对它）。

---

## 一句话结论

**视觉通道现在不是"模型看不清"，而是准入门用了一个物理上不可达的判据。**
探针把「模型落点误差」和「标度可分辨性」两件事混成了一个 `max_error ≤ 1 px` 来卡，
而前者实测 1.06~67.07 px（6 次标定）、**后者是纯几何量、只有 2.87 px**——
于是 `visual_channel_unavailable` + 零动作成了常态（【实测】M9/M14/M15）。

**离终点最近的路是三步**：

1. **准入门换判据**（不动 1 px 本身）：把 1 px 退回去只管"两个候选标度能不能分开"，
   准入改成「等价标度族落点分歧 ≤ 4 px（实测 2.87 px）∧ K 轮 96 px 方块命中率 ≥ 阈值」→
   视觉通道从"恒不可用"变成"由命中率决定"。
2. **落点从"模型瞄"改成"模型选"**：模型只答候选编号，落点取候选框物理中心 →
   沿用 M4 已实测的 **0.0 px**；候选源用 Windows 自带 OCR 词级框（零新依赖、零显存、物理像素）。
3. **多步侧把"每轮固定截图"交给模型**：加 `observe()`，程序能自己决定要不要新画面、
   并能读到校验层读数（OpenGCUA 官方 README：**换 harness +19.6 分 / 步数 −53.0%**、
   **代码执行 vs 同等 GUI+CLI 权限 +6.1 分 / 步数 −14.8%**）。

**这一轮不做**：换本地 grounding 模型（6GB 上无一手延迟数据）、引入 OmniParser（`docs/spec.md` §12 架构禁止）、
动 MCP/HTTP/CLI 对外形态、建 WAA 沙箱。

---

## 视觉通道：让它真的能用

### 先把病灶说准：探针量了两件事，只有一件该卡门

`src/minicode/calib/calibrate.py` 的 `CalibrationProbe.run()` 一轮问四个目标，
`CandidateEvaluation.max_error_px` 记的是**模型指得准不准**；
`CalibrationOutcome.divergences_px` 记的是**两个候选标度落点差多少**——后者与模型无关，是纯几何量。

- 模型落点误差（当前门卡的就是它）：**1.06~67.07 px**（【实测】6 次标定：66.28 / 1.90 / 28.61 / 1.06 / 2.66 / 68.00 / 68.81 px）→ **1 px 物理不可达**。
- 标度可分辨性（`0~999` vs `0~1000`）：2560 宽上 x 轴分歧 2.56 px、欧氏 **2.87 px**
  （【实测】`src/minicode/calib/tolerance.py` 文件头与 `docs/spec.md:37` 均写明这个数）。

`docs/spec.md:37` 写"判定容差必须是 1 px"，它的**理由是"0~999 与 0~1000 分不开"**——
这个理由成立，但结论被用错了地方：**1 px 该管"两个标度能不能分开"，不该管"模型指得准不准"。**
生态里也没有"1 px 绝对像素精度"这个判据：GUI grounding 全用 **click accuracy（点落在 GT 框内）**
（【一手】ScreenSpot-v2 https://arxiv.org/abs/2404.07990 ；ScreenSpot-Pro https://arxiv.org/abs/2504.07981）。

> **本方案的核心主张**：不放松 1 px，而是**把 1 px 归位**。
> 标度等价族在 96 px 探针方块内必然等价（2.87 px ≪ 96 px），所以「分不清 999/1000」这件事
> **对落点没有影响**——这正是代码里已有的 `landing_equivalence` / `equivalent_scales` 概念该干的活。

---

### A. 标定 / 尺度判定

#### A-1【P0】准入门换成「落点等价 + 命中率」，1 px 归位

| 文件 | 改动 |
|---|---|
| `src/minicode/calib/tolerance.py` | **保留** `CALIBRATION_TOLERANCE_PX = 1.0`（继续管标度可分辨性）与 `OUT_OF_BOUNDS_TOLERANCE_PX`（越界容差，两者互不共用不变）。**新增第三个独立常量** `SCALE_EQUIVALENCE_TOLERANCE_PX = 4.0`（来源：999/1000 在 2560 宽上的欧氏分歧 **2.87 px**，留一倍余量）、`PROBE_HIT_RADIUS_PX = 48`（探针方块 96 px 的一半）、`CALIBRATION_ROUNDS = 5`、`CALIBRATION_MIN_HIT_RATE = 0.6`。三条容差**互不共用**（沿用 C-03 纪律）。 |
| `src/minicode/calib/calibrate.py` | `CalibrationProbe.run()` 从「一轮问一次」改成 **K 轮独立问**（K=`CALIBRATION_ROUNDS`，每轮重发同一张探针图，注意必须是**另一次问**，逐字重发在确定性模型下会拿到同一答案——`ground/channel.py` 的 `second_opinion` 注释已记过这个坑）。`CalibrationOutcome` 新增 `rounds: int`、`hit_rate: float`、`median_error_px: float`、`p90_error_px: float`；`calibration_ok` 的语义改为「**落点等价族判定成功**」，不再表示"模型误差 ≤ 1 px"。 |
| `src/minicode/calib/admission.py` | `visual_channel_available()` 的入参换成 `equivalent_scales / divergences_px / equivalence_tolerance_px / hit_rate / min_hit_rate`；判定顺序：① 等价族最大分歧 ≤ 4 px **且** ② K 轮命中率 ≥ 0.6 → **放行**；否则拒绝，理由要人可读（例："候选标度分歧 2.87 px ≤ 4 px，但 5 轮命中率 0.4 < 0.6"）。`CalibrationAdmission.admit()` 的两个 `VisualAdmission` 分支同步改写。**分母来源仍只有 `calib/record.py` 一处**（红线①不动）。 |
| `src/minicode/calib/probe.py` | `TARGET_SIZE_RATIO = 0.0375`（96 px）**不动**；新增 `probe_hit_radius(spec) -> float` 供命中判定用；探针图本身**不改**（它已经是对的：四目标贴四角、最右那个 ≥ 0.95×宽，命中区 96 px 让 0~999/0~1000 自动等价）。 |
| `src/minicode/calib/record.py` | `CalibrationRecord` 落盘字段加 `rounds / hit_rate / median_error_px / p90_error_px / equivalence_tolerance_px / scale_equivalence_verdict`。 |
| `src/minicode/calib/__main__.py` | 加 `--rounds`、`--min-hit-rate` 参数。 |
| `src/minicode/e2e/visual.py` | `_calibrate()` 把新 outcome 透传；`admission_port()` 的生产/测量双口径**不变**（测量口径 `bypass_admission=True` 保留，它仍有价值：量"真机上落多准"）。 |

**命中判据**（照抄生态口径，【一手】ScreenSpot-Pro 官方 `eval_screenspot_pro.py`：
预测点按图宽高归一化后落在 GT 框内即命中）：预测物理点落在探针目标的 96 px 方块内 = 命中。

**为什么不直接改成 2 px 落点容差**：M22 那条路只救 ~10% 的任务（【一手】UFO² 论文 Table 3：
混合检测把 control-detection 类失败救回 ~9.86%），而我们 M14/M15 两波失败的直接原因就是这道闸门。
**先开闸、再谈精度**。

> ⚠️ **需人拍板的一处**：`docs/spec.md:37` 那句"判定容差必须是 1 px"要补一句
> 「1 px 只管标度可分辨性，不管模型落点误差」；`docs/tickets/M5-calibration-probe.md` 的 AC 同步改写。
> **本方案不要求放宽 1 px，只要求把它归位**——这是全篇唯一需要人点头的地方。

#### A-2【P0】模型输入尺寸：从"手填"改成"声明 + token 数反查"

现在有三处手填/猜测：

- `src/minicode/e2e/visual.py:32` —— `model_input: tuple[int, int] = (2048, 1280)`（默认值写死）。
- `src/minicode/calib/calibrate.py` —— `model_input_size` 由调用方传进来，是**基准**，错了就静默偏移
  （代码注释里记着"实测误差 571.79 px → 2.66 px"）。
- `src/minicode/eval/__main__.py:61` —— `MODEL_INPUT_CANDIDATE_WIDTHS = (2048, 1600, 1280, 1024)`：**猜一串候选**。

**改成两件事**：

1. **声明**：我们**自己**把 2560×1600 缩到 **1280×800**（正好 ×2，16:10 不变形）再送模型，
   并在提示词里写明「这张图是 1280×800，请给这张图的像素坐标」。
   这正是 Anthropic 的坐标契约：`display_width_px / display_height_px` **声明**发出去那张图的尺寸，
   回程换算是已知比例（【一手源码】https://github.com/anthropics/anthropic-quickstarts/blob/main/computer-use-demo/computer_use_demo/tools/computer.py
   —— `screenshot_size()` 注释 "This is the coordinate frame the model emits coordinates in"，
   `scale_coordinates()` 里还有免费的越界检查 `raise ToolError("Coordinates x, y are out of bounds")`）。
   → **我们改成"主动声明"之后，变换是一个常数，探针整个不需要猜 999/1000。**
2. **token 数反查**：请求日志里每帧的图像 token 数能反推服务端实际看到的尺寸
   （【二手】Anthropic 的 28 px patch 规则：`ceil(2048/28) × ceil(1280/28) = 74×46 = 3404`，
   而我们请求日志实测 3417/3437 token，差 ≤13 token → 服务端确实缩到 2048×1280）。
   **【推断】** 该规则是否适用于 MiniMax 端点**未验证**——它是 Anthropic 的规则。

**改动点**：

| 文件 | 改动 |
|---|---|
| `src/minicode/model/request_log.py` | 请求日志新增 `image_tokens` 字段（与 `input_tokens` 分开记），供反查。 |
| `src/minicode/e2e/visual.py` | `model_input` 默认改 `None` = "由标定记录决定"；删掉写死的 `(2048, 1280)`。 |
| `src/minicode/calib/calibrate.py` | `model_input_size` 支持从请求日志反查（`estimate_model_input_from_tokens()`），反查不出时**如实标 `unknown`**，不许猜。 |
| `src/minicode/eval/__main__.py` | 删掉 `MODEL_INPUT_CANDIDATE_WIDTHS` 猜测循环，改用反查结果。 |
| `src/minicode/ground/prompt.py` | `coarse_prompt()` / `refine_prompt()` 加「这张图 W×H，坐标以这张图的像素为准」。 |
| `src/minicode/calib/prompt.py` | `image_prompt()` 同上加声明。 |

> ⚠️ **不适用**：Anthropic 的 **zoom 契约**（放大后模型仍报整屏坐标）**不适用我们**。
> 【一手源码】同一份 `computer.py` 的 `zoom()` docstring 原文："The model keeps emitting coordinates
> in screenshot pixels afterwards; the zoom image itself is never a coordinate frame."
> 我们 M14 §4.1 已实测：Anthropic 式契约下模型答 (2126,1273) 越界被拒；换成 crop 契约后偏差 14.0 px
> （对包围盒中心 1.1 px）。**我们的 crop 契约是对的，不要改。**

#### A-3【P1】候选标度族：把"分不清"变成显式结论

`CalibrationOutcome.equivalent_scales` / `landing_equivalence` 这套概念代码里已经有了，
但被 `max_error ≤ 1 px` 挡在门外。改完 A-1 后它会自然生效：

- `0~999` 与 `0~1000` 落点分歧 **2.87 px** ≤ 4 px → 判**等价**，不阻断，记"落点等价 + 等价列表"。
- 等价族分歧 > 4 px → **拒绝（不猜）**，走 UIA 或报明确错误码。
- 【二手】生态里没人问"是 999 还是 1000"：Qwen3-VL 把坐标改成相对 0~1000、
  分母写死在模型卡里（【一手】官方 cookbook https://github.com/QwenLM/Qwen3-VL/blob/main/cookbooks/2d_grounding.ipynb
  原文 "relative coordinates ranging from 0 to 1000"）；UI-TARS 全家桶按 0~1000 归一化
  （【一手论文】https://arxiv.org/abs/2501.12326）。**1000 是事实标准，999 不是**——
  我们探针里那个 0~999 候选应当降级为"等价标度"，而不是"必须排除的候选"。

---

### B. 落点精度

#### B-1【P0】落点从「模型瞄点」改成「模型选编号」

这是**全篇唯一能把误差变成 0.0 px 的做法**（不是把 6 px 降到 2 px），而且**零新概念**：
我们 UIA 通道已经跑通了同一范式——【实测】M4 §6.6：模型只答行号 → 落点 = 控件框物理像素中心 →
**落点偏差 0.0 px、端到端 434.3 ms、模型调用 1 次**（`src/minicode/uia/pointer.py`）。

**视觉通道缺的不是范式，是候选源。** 候选源用 **Windows 自带 OCR 的词级框**：

- 【一手】`OcrWord.BoundingRect`："Gets the position and size in pixels of the recognized word from the top left corner of image"
  （https://learn.microsoft.com/en-us/uwp/api/windows.media.ocr.ocrword?view=winrt-26100）。
  我们喂进去的帧保持原始物理分辨率 → **框天然落在物理像素空间，零换算**（红线①②③全满足）。
- 【实测】M27 真机三处全跑通（Windows 设置缩放值 / 微信聊天区标题 / 任务栏时钟）。
- 现状：`src/minicode/verification/ocr_windows.py` **已经在用 Windows.Media.Ocr**，
  但脚本最后只取 `$result.Text`，**词级框全扔了**。

**改动点**：

| 文件 | 改动 |
|---|---|
| `src/minicode/verification/ocr_windows.py` | PowerShell 脚本改成遍历 `$result.Lines[].Words[]`，输出 `text,x,y,w,h`（物理像素）；新增 `read_words(frame) -> list[OcrWord]`，`read()` 保留（不破坏现有调用方）。 |
| `src/minicode/ground/candidates.py`（**新建**） | `OcrCandidateSource`：按目标描述的文本子串匹配 OCR 词 → 候选框列表（物理像素）；提供 `IoU` 去重（抄 UFO² 的 **IoU 0.1** 规则，见下）。 |
| `src/minicode/ground/prompt.py` | 新增 `numbered_candidates_prompt(description, candidates)`：给每个候选框编号，要求模型**只答编号**；`parse_chosen_numbers()` 复用 `src/minicode/uia/pointer.py:78`。 |
| `src/minicode/ground/channel.py` | `TwoStageGrounder.locate()` 加一条分支：**有候选 → 编号选择 → 落点取该编号对应框的物理中心**（零换算）；**无候选 → 走现有点式路径**（显式记录"无候选"，不猜）。 |

**候选合并口径（抄 UFO² 的现成规则）**：UIA 控件为主表，OCR 框与任一 UIA 控件 **IoU > 0.1** 则丢弃
（【一手】https://microsoft.github.io/UFO/ufo2/core_features/control_detection/hybrid_detection/ 官方文档给出
`IOU_THRESHOLD_FOR_MERGE: 0.1` 与去重算法；论文 Table 3：UIA 37.3 / OmniParser 127.3 / 合并 97.9 个检出，
"27.9% and 56.7% of OmniParser-v2 detections are discarded due to overlap with UIA"）。
⚠️ **论文没写 IoU 比较发生在哪个坐标系**——我们照抄时必须自己定死：**唯一真值 = 物理像素 × 虚拟桌面原点**（红线①）。

**SoM / OmniParser 只借交互形态，不借分割模型**：
SoM（【一手】https://arxiv.org/abs/2310.11441）证明「叠编号 + 模型只答编号」比直接给坐标强，
但那是 COCO referring 不是 GUI；**OmniParser 架构禁止**（`docs/spec.md` §12 明写"不引入 OmniParser 做实时 grounding"），
且【一手】其官方模型卡自报 ScreenSpot-Pro 仅 39.6%、0.6~0.8 s/帧、许可三方打架（仓库 CC-BY-4.0 / README MIT 徽章 / 旧检测器 AGPL）。

#### B-2【P1】粗定位从「出点」改成「出框」→ 门控四条条件第一次全部可判定

【一手论文】R-VLM（https://arxiv.org/abs/2507.05673）：整图出**粗框**当 region proposal →
裁约 5×宽高区域 → 放大重预测，ScreenSpot/AgentStudio **+13%**、AITW/Mind2Web +3.2~9.7%。
反证据也要记：SeeClick 附录 B.4 "predictions with points were marginally better than bounding boxes"
（【二手转述】经 R-VLM 引述）——**收益不在"框比点准"，在"框给裁剪一个区域提议"**。

**改动点**：

| 文件 | 改动 |
|---|---|
| `src/minicode/ground/prompt.py::coarse_prompt()` | 改成要 `{"bbox":[x1,y1,x2,y2],"point":[x,y]}`；框可退化成点（点=框退化）。 |
| `src/minicode/ground/channel.py` | 解析框 → 推出 `target_radius_px` → 喂 `GateInputs.target_radius_px`（**现在恒为 None**，`gate.py` 的 `_target_area_ratio` 因此永远返回 `None`）。 |
| `src/minicode/ground/gate.py` | 判定逻辑不动，只多一个可判定的输入。`DEFAULT_AREA_RATIO_SOURCE` 的来源串补上论文出处：ScreenSpot-Pro §3.4 "targets in ScreenSpot-Pro occupy 0.07% of the screenshot area on average"（对比 ScreenSpot 2.01%）。 |

**顺带解决**：`src/minicode/ground/gate.py` 的四条条件里，现状只有 `coarse_validity` 可判定
（【实测】M7 §6.9：17/17 样本另外三条判不了 → "门控在生产接线上基本是哑的"）。出框之后
`target_area_ratio` 第一次可判定；`confidence` 与 `consistency` 由 B-3 补齐。

#### B-3【P1】Pre-Zoom 一致性探针 → 门控的 confidence / consistency 变可判定

【一手论文】ZoomClick §3.2.1（https://arxiv.org/abs/2512.05941）：Pre-Zoom = 整屏预测 `p_dir` 与
K=4 个 2×2 分块预测 `p^(k)` 的像素距离 `d_k`，取 argmin 且 `d < τ` 的局部预测作起点，
否则保留整屏视图（"ensuring early correction with full context still available"）。

**改动点**：

| 文件 | 改动 |
|---|---|
| `src/minicode/ground/channel.py::_second_coarse_point()` | 端口形状不变（**复用现成的 `second_opinion` 端口**，不新增依赖），实现改成"2×2 分块各自预测"，取 argmin 且 `d < τ` 者；`τ` 取 `DEFAULT_CONSISTENCY_TOLERANCE_PX`（现 128 px）的一半并实测复核——换裁剪尺寸时这个阈值要跟着复核（源码注释已提醒）。 |
| `src/minicode/ground/gate.py` | `GateCondition.CONFIDENCE` 的语义从"模型自报置信度"（模型不报 → 永远不可判定）改为"**自洽度**"：用整屏点与分块点的一致性填。枚举改名要**保留旧值兼容落盘**。 |

**这是唯一一条能把"门控哑掉"变成"门控有信号"的低成本改动**：多 4 次只发 1/4 屏的模型调用，
换来 (a) 一致性信号、(b) 首次能检出"**粗定位本身就偏**"的判据
（【实测】M7 §4.3 记着"粗定位本身就偏时两者会同向漂移，回落判据抓不住"）。

#### B-4【P1】目标描述规范化（零训练成本，先对齐名字再定位）

【一手论文】UI-Ins（https://arxiv.org/abs/2510.20286）：既有 GUI grounding 数据集指令 **23.3% 缺陷率**；
"inference-time exploitation of instruction diversity yields up to a substantial 76% relative performance improvement"。
【实测】我们 M14 §4.2/§4.6：工单写「显示」，本机卡片实为「屏幕」，模型按「显示」点到副标题
「显示器、亮度、夜间模式…」→ **落点偏 27~30 px**；M9 §6.12：描述含糊（「用户头像」）→ **偏差 1270.9 px**。
**这两个都不是坐标精度问题**，是描述与界面不一致 / 语义歧义。

**改动点**：`src/minicode/ground/prompt.py` 前置 `normalize_target(description, screen_names)`——
名字来源 = OCR 词表 + **脱敏后**的 UIA 控件名（红线④：UIA 树送模型前必须脱敏，这里送进模型的也必须脱敏）；
模型侧给 2~3 个更具体的表述让它选。

#### B-5【P2】输出格式的量化地板

0~1000 整数在 2560 宽上 **1 单位 = 2.56 物理 px → ±1.28 px 地板**；抬到 0~10000 或 [0,1] 带 3 位小数 → **±0.128 px**。
【推断，基于我们自己的算术（`docs/research/bottom-drafts/02-coordinate-systems.md` L330/L339）】。
【二手】行业口径：UI-TARS 0~1000（x_px = x_model/1000×原图宽）、ShowUI 相对 [0,1]、UGround 论文版用绝对像素。

**改动点**：`src/minicode/ground/prompt.py::_answer_format()`、`src/minicode/calib/prompt.py::_answer_format()`
（0~1000 → 0~10000）；`src/minicode/coord/coordinates.py::ModelScale` 加 `RELATIVE_0_10000`。
**分母仍来自运行时标定，代码里不写死**（红线①）。
⚠️ 风险：模型不一定照做 → 标定记录里**同时**记"要求的分母"与"实测的分母"，两者不符时以实测为准。

#### B-6【P2】本地 grounding 支线 —— **本轮不做，只留接口位**

见 §4 组件表与 §6。理由：6GB 显存上**没有任何一个候选模型有一手的实测延迟**
（官方只在 A100/4090/H200 测过）；换模型会改动坐标契约与标定链路，必须另开工单。

---

### C. crop 策略

#### C-1【P1】crop 尺寸：两组证据冲突，**必须自己扫**

| 证据 | 指向 | 强度 |
|---|---|---|
| ScreenSpot-Pro 论文 Table 5 消融：OS-Atlas-7B **512²=25.1 / 768²=34.2 / 1024²=40.2 / 1280²=40.1**；UGround-7B **512²=27.0 / 768²=28.8 / 1024²=28.2 / 1280²=26.3** —— "when images are too small, crucial context is lost" | **调大**（512 是全表最差格，1024 对 OS-Atlas +15.1 pp） | 【一手】https://arxiv.org/html/2504.07981v1 |
| 我们自己的实测：误差 **∝ 模型视图宽度** —— L0 @2048 宽 **3.4~7.6 px**，L1 @512 宽 **0.8~2.3 px** | **调小**（按 0.2%~1.9%×视图宽推算，128 宽 crop → 0.26~2.4 px） | 【实测】本机；【一手】ZoomClick 同源结论（UI-Venus-72B ScreenSpot-Pro 73.1%） |

**两组证据不矛盾但适用对象不同**：论文是 **7B 本地模型在 448/1344 训练分辨率下**，
我们是 **API 模型 + 高分屏**。**所以不能照抄，只能扫。**

**改动点**：

- `src/minicode/coord/crop_refine.py`：`DEFAULT_CROP_WIDTH_PX/HEIGHT_PX = 512` **默认值不动**
  （`ground/channel.py` 的 `crop_width/crop_height` **已经是运行时参数**，直接可用）。
- `src/minicode/ground/bench.py`：加 **sweep 模式**，在同一批 ScreenSpot-Pro 抽样上跑
  **{512, 256, 192, 128}**，输出「误差-尺寸曲线」，定 `m`（最小 crop 边长）。
  **A 半调、B 半评**（【实测】M23 刚证明"同批调参 + 同批评估 = 作废"）。
- ⚠️ 我们文档里的「无条件做 crop 整体 −0.3 pp」**至今没有定位到一手出处**（多条线都记了这一条）——
  在拿到一手数字前，**不许把 −0.3 pp 写进任何对外结论**。

#### C-2【P1】迭代 zoom（多步收窄），每一级都从**原始物理分辨率帧**裁

【一手论文】ZoomClick（https://arxiv.org/abs/2512.05941）§3.2 给了四个可抄的参数与三条终止条件：

- 收缩比 `ρ` + 最小尺寸 `m`：`W̃ = max(⌊ρ·W_t⌋, m)`
- "Each iteration crops a region of the specified shrink size **directly in the original coordinate system**,
  preventing iterative drift from relative cropping" ← **与我们的红线②完全同构，等于给红线找到外部背书**
- 三条终止条件：**没检出目标 / 达到深度 T / 裁剪达最小尺寸**
- 越界默认 **shift**
- 效果：UI-Venus-72B 在 ScreenSpot-Pro 达 **73.1%**（当时 SOTA）；UI-Venus-7B + ZoomClick 超原版 72B **+2.2%**

**改动点**：

| 文件 | 改动 |
|---|---|
| `src/minicode/ground/channel.py` | 新增 `_iterative_refine()`：ρ=0.5、depth=3、m 取 C-1 扫出来的值、三条终止条件、越界 shift；**每一级都从原始帧裁**（`crop_frame(frame, plan)`，不裁上一级的 crop）→ 红线②不动。 |
| `src/minicode/ground/crop.py` | 复用现有的 `crop_frame(frame, plan)`，不新增裁剪入口（避免第二个真值来源）。 |
| `src/minicode/ground/prompt.py` | 新增 `zoom_in_prompt()`：告诉模型"这块区域里有什么"，措辞对齐 `refine_prompt()`。 |
| ⚠️ `src/minicode/ground/zoom.py` **不要动** | 它是**动作协议里的"放大"动作**（回源重抓 + 模型报**整屏**坐标），与"内部取景的迭代收窄"是**两套契约**，源码头注释已明确警告混用会**静默偏移一整个裁剪窗口**。 |

#### C-3【P1】门控信号换成公开同构实现：zoom-consistency

【一手】ScreenSpot-Pro 官方仓的 `models/zoom_consistency_router.py`：
**step-2 预测点与 crop 中心的距离** = 免标定的置信信号，跨架构 VLM 无需标定即可比。
【一手论文】https://arxiv.org/abs/2604.15376：与正确性相关性 AUC 0.60、Spearman rho −0.14 / −0.11（p<1e-6 / p=0.0003）；
路由实验捕获 16.5% oracle headroom、**+0.8%（McNemar p=0.19，不显著）**。
官方榜：KV-Ground-8B + Qwen3.5-27B Consistency Router overall **80.90**、windows_common 67.90
（【一手】https://gui-agent.github.io/grounding-leaderboard/）。

**改动点**：`src/minicode/ground/gate.py` 新增一个门控信号（判据 = step-2 点到 crop 中心的距离），
**用它替换我们自己拍的「一致性容差 16 px」启发式**。
⚠️ **官方结论本身也是个不显著的 null result（+0.8%，p=0.19）**——
与【实测】M23「门控可归因准确率收益 = 0，唯一确定收益是省模型调用」方向一致。
**所以这条只能当"省调用"的信号记账，不许当精度收益报。**

#### C-4【明确不做】无条件 crop

红线③（crop 精定位必须门控）+ `docs/spec.md` §11 第四条同源规则（"兜底要罕见"）不变。
`GateMode.ALWAYS` 只保留为评测台对照组。

---

## 多步任务：让它真的能跑

### D-1【P0】加 `observe()`：让模型自己决定要不要新画面

**现状**：`src/minicode/decision/harness.py:286` 每轮结束**无条件** `frame = self.api.capture_frame()`，
`harness.py:274` 每轮**无条件** `images_base64=self._images(context)`。
每张 2560×1600 完整图 ≈ **3417 token**（【实测】`docs/00-现状.md`）→ **每轮固定 +3417 token**。

**参考实现**：OpenGCUA（【一手论文】https://arxiv.org/abs/2610.22200）。
按 `docs/spec.md` §12 的引用纪律，**只引用官方 README 正文里的两组数**：
换 harness **+19.6 分 / 步数 −53.0%**；代码执行 vs 同等 GUI+CLI 权限 **+6.1 分 / 步数 −14.8%**。
同文另有一组"按需观察让缓存命中率 +10.5/+14.4 pp 且任务分不降"（【一手论文】正文）。

**改动点**：

| 文件 | 改动 |
|---|---|
| `src/minicode/action/program.py`（`PROGRAM_API_NAMES`，L47） | 白名单加 `"observe"`（白名单**只有一份**，`decision/api.py::as_mapping()` 会校验两边一致，多一个名字就炸——那个校验要留着）。 |
| `src/minicode/decision/api.py` | 新增 `observe()`：返回 `{"changed": bool, "text": str, "size": [w,h]}`；底层复用 `_frame()` / `capture_frame()`（**只观察、不注入输入**，与 `zoom` 动作同性质）。 |
| `src/minicode/decision/harness.py:286` | 改成：**本轮程序调用过 `observe()`/`screenshot()`，或上轮发生了动作**才抓新帧；否则沿用上一帧（`ContextWindow.add_round(frame=None)` **已经支持不淘汰旧帧**）。 |
| `src/minicode/decision/prompt.py` | `PROGRAM_API_DOC` + `_PLAN_RULES` 里说明「要看画面自己调 `observe()`」。 |

⚠️ **兜底纪律**：模型忘了调 `observe()` 就成了盲写 → **本轮程序做过任何定位/点击动作时强制抓一帧**
（不让"省 token"换来"看不见"）。这条兜底写进 `harness.py` 的注释。

### D-2【P0】校验层读数进程序作用域 → 程序能分支

现在程序只能"做动作"，不能"看结果"，所以只能线性往下走。

**改动点**：

| 文件 | 改动 |
|---|---|
| `src/minicode/action/program.py` | `PROGRAM_API_NAMES` 加 `"last_check"`。 |
| `src/minicode/decision/api.py` | 新增 `last_check()`：返回上一次动作的校验读数 —— `changed` / `expectation_held` / `landing_deviation_px` / `error_code`，数据来自 `src/minicode/verification/layer.py`（期望断言 + 界面无变化检测，我们已经有）。 |
| `src/minicode/decision/prompt.py` | 告诉模型：`last_check()` 告诉你上一步到底成没成。 |

**为什么值得做**：OpenGCUA 的"代码执行单独 +6.1 分、步数 −14.8%"（【一手】官方 README 两组数之一）
说明**分支能力**本身就是收益；而且【实测】M10 跑出来"模型连续三轮自称完成却不停止"，
正是因为程序**读不到"没变化"这个事实**。

### D-3【P1】上下文裁剪：改成 Anthropic 官方 tool-clearing 语义

【一手】AWS Bedrock 官方文档（Anthropic 协议镜像）`clear_tool_uses_20250919` 参数表：
`trigger` 默认 **100000 input tokens**；`keep` 默认 **3** tool uses（保留最近几组 tool_use/tool_result，从最旧删）；
`clear_at_least`；`exclude_tools`；**`clear_tool_inputs` 默认 False（只清结果、保留调用**——
模型压缩后仍看得见**做过哪些操作**，而不是只看到一串摘要）。
（https://docs.aws.amazon.com/bedrock/latest/userguide/model-parameters-anthropic-claude-messages-tool-use.html）

**现状对照**：`src/minicode/decision/context.py::ContextWindow(keep_frames=3)` —— **keep=3 恰好一致**，
但缺「按 token 触发」和「清结果保调用」两条语义。

**改动点**：

- `src/minicode/decision/context.py`：新增 `trim_to_tokens(max_tokens)`，触发阈值按实测 3417 token/帧 算
  （例如 `max_tokens = 6 × 3417` → 保留 2 轮完整图 + 全部结构摘要）。
- `src/minicode/model/client.py`：**这一轮先不接**服务端的 `clear_tool_uses_20250919`——
  我们现在走的是纯文本 + 图片，**没有用 tool 协议**；等真的用上 tool 协议再接（避免给一个用不到的参数写适配层）。

### D-4【P1】停止条件：按 OSWorld 2.0 的公开失败分类补齐四条

【一手论文】OSWorld 2.0（https://arxiv.org/abs/2606.29537）摘要原文的失败归因：
**"lose track of constraints, miss information that arrives mid-task, guess rather than ask the user, and skip verification"**。
同文：108 个长程工作流、人中位 1.6 小时；500 步下 Claude Opus 4.8 **二值 20.6% / 部分分 54.8%**。

**改动点**：`src/minicode/reflection/stop.py` + `src/minicode/reflection/gate.py`
——把现有 4 个检测器**逐条**对到这四类上（不是另起一套），重点补**「跳过验证」**
（我们现在没有判死这一条：程序做完了但没有校验记录 → 记一条）。
另外**「部分分检查点」**的思路可以抄：OSWorld 2.0 平均 27.25 个检查点 ——
对应我们的"期望断言"能不能从"整任务一个断言"改成"每个关键步一个断言"（【一手】同文）。

### D-5【P1】上下文加窗口标题

【一手】WindowsAgentArena 官方页（https://microsoft.github.io/WindowsAgentArena/）原文：
**"Agents that rely only on pixel-based OCR and icon detection achieve lower performance than those that also use the UIA tree."**
同页：**154 个 Windows 任务**（LibreOffice Calc/Writer、Edge、Chrome、文件资源管理器、设置、VS Code、VLC、记事本、时钟、画图）；
Navi **19.5%** vs 人 **74.5%**。
【一手】WAA 的 SoM = 脱敏 UIA 树 + OCR + 图标检测。

**改动点**：

- `src/minicode/decision/prompt.py::build_plan_prompt(goal, context)`：加一段
  「前台窗口标题 + 其他窗口/浏览器标签标题」。
- 取标题：`src/minicode/exec/uia.py:680 find_window()`（已有，按标题子串找可见窗口）、
  `src/minicode/e2e/real.py:87 window_title_of(hwnd)`（已有）。
- ⚠️ **红线④：窗口标题也要过脱敏器**（`src/minicode/privacy/guard.py` / `src/minicode/redact/redactor.py`）——
  标题里可能有人名、消息预览。

**这是"UIA 优先、视觉兜底"路线目前最硬的公开背书**，也直接支持我们的 UIA 通道
（【实测】落点 0.0 px、端到端 434 ms、模型调用 0 次）。

### D-6【P0】端到端：cc-switch 改走 CDP（零模型调用、零标定）

【实测】M15：E-1~E-3 走 browser 通道**落点偏差 0.0 px**；M15 §4.4 记 **cc-switch 树 usable
但供应商条目不在当前页** → 覆盖失败 → 切视觉 → 标定 68.00 px > 1.0 px → **零动作**。
**这不是感知问题，是"页面不在当前页"。**

**做法**（cc-switch 是 Tauri + WebView2）：
【一手】Microsoft Learn（https://learn.microsoft.com/en-us/microsoft-edge/webview2/how-to/debug-visual-studio-code）：
`$env:WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS = "--remote-debugging-port=9222"`，**必须先于 WebView2 环境创建设置**；
Tauri 侧 `app.windows[].additionalBrowserArgs`。

**改动点**：`src/minicode/e2e/apps.py` 的 cc-switch 执行器（`CCSwitch` 类）改走 CDP，
复用现成的 `src/minicode/browser/cdp.py`（与 Electron 同一条路）。
**安全前提**：远程调试端口 = 允许在 webview 上下文执行任意代码 → **只绑 127.0.0.1、只本机调试用、绝不入生产**。

### D-7【P1】上下文窗口预算记账

`src/minicode/decision/harness.py` 已有 `token_growth`；把**每轮的图像 token 数**单列成一列
（现在只有总 input_tokens），这样"observe() 改造省了多少"能被直接读出来，而不是靠感觉。

---

## 可复用的开源组件

> 体积分两类：**权重**（要占显存/磁盘）与**代码/基准**（不占显存）。
> 「公开精度」一律是 **click accuracy（点落在 GT 框内）**，**不能**与我们的像素距离直接对比。
> HF 权重许可与坐标输出格式本机**未能一手核实**（`huggingface.co` DNS 不可达）→ 标【二手】。

| 组件 | 用途 | 许可证 | 体积 | 本机 6GB 3060 能不能跑 | 坐标口径 | 公开精度 | 来源 URL |
|---|---|---|---|---|---|---|---|
| **Windows.Media.Ocr**（系统自带） | 视觉通道的**候选框来源**（词级框 → 模型选编号 → 落点取框中心） | Windows 系统组件 | 0 字节 | ✅ 能（CPU，零显存） | **物理像素**（框在所喂帧的像素空间） | 无公开精度（我们是第一个量化它的） | https://learn.microsoft.com/en-us/uwp/api/windows.media.ocr.ocrword?view=winrt-26100 |
| **WebView2 远程调试（CDP）** | cc-switch / Electron 的 DOM 精定位 | 免费系统组件 | 0 字节 | ✅ 能 | DOM 元素坐标（需换算到物理像素） | 我们自己已实测 **0.0 px**（E-1~E-3） | https://learn.microsoft.com/en-us/microsoft-edge/webview2/how-to/debug-visual-studio-code |
| **UGround-V1-2B**（GGUF） | 本地 grounding 备选（**本轮不做**） | Apache-2.0【二手】 | GGUF Q4_K_M 986 MB + mmproj-fp16 1.33 GB = **2.32 GB** | ✅ 能（llama.cpp；**官方 README 推的 vLLM 路线不可行**） | 相对 **0~1000**（官方 README："compatible with the original Qwen2-VL"，x/1000×width） | ScreenSpot avg **77.7**（官方 README 表）；ScreenSpot-Pro 数字**一手未取到**（README 写 UGround-V1 18.9→31.1，论文 Table 2 写 7B=16.5，口径冲突） | https://huggingface.co/osunlp/UGround-V1-2B · https://github.com/OSU-NLP-Group/UGround |
| **Jedi-3B-1080p**（GGUF） | 本地 grounding 备选（**本轮不做**） | Apache-2.0【一手·HF API】 | GGUF Q4_K_M 1.93 GB + mmproj 1.34 GB = **3.27 GB**（全量 7.51 GB） | ⚠️ 勉强（需 4bit + 短上下文） | **绝对像素 @ smart_resize 后的模型输入图**（factor = 14×2 = 28）——**恰好是我们已判出的 `absolute_model_input` 标度** | ScreenSpot-v2 **88.6** / ScreenSpot-Pro **36.1** / OSWorld-G **50.9**；WAA 15 步 SR **29.1±0.57**（配 GPT-4o） | https://osworld-grounding.github.io/ · https://huggingface.co/xlangai/Jedi-3B-1080p |
| **ShowUI-2B** | 本地 grounding 兜底（**本轮不做**） | 代码 Apache-2.0 / 权重 MIT【二手】 | bf16 ~4.4 GB | ⚠️ 顶格，需量化 | 相对 **[0,1]** | 零样本截图 grounding **75.1%**（论文摘要） | https://github.com/showlab/ShowUI |
| **Qwen2.5-VL-3B-Instruct-AWQ** | 本地 grounding（**商用阻断**） | **qwen-research（非商用）**【一手·HF 卡】 | 3.40 GB | ✅ 能 | **绝对像素**（smart_resize 空间） | ScreenSpot-v2 ≈91.6%【二手·官方榜】 | https://huggingface.co/Qwen/Qwen2.5-VL-3B-Instruct-AWQ |
| **qwen-vl-utils**（pip） | `smart_resize()` —— 算出模型实际输入尺寸 | Apache-2.0（Qwen3-VL 仓） | ~几十 KB | ✅ 纯 Python | — | — | https://github.com/QwenLM/Qwen3-VL/blob/main/qwen-vl-utils/src/qwen_vl_utils/vision_process.py |
| **ScreenSpot-Pro**（基准 + 官方仓） | 评测底座：本地已有 **1581 条**（Windows **927 条**占 58.6%）；官方仓有 18 个 per-model 坐标适配器 + 5 种 coarse-to-fine 方法 | 见仓库 | 数据集已在本地 | ✅（纯 CPU 判分） | 官方 `eval_screenspot_pro.py`：点入框即命中 | — | https://github.com/likaixin2000/ScreenSpot-Pro-GUI-Grounding · https://arxiv.org/abs/2504.07981 |
| **WindowsAgentArena**（基准） | Windows 端到端外部成绩单：154 任务 + BYOA 接口 | MIT | 镜像 ~6 GB + 黄金镜像 30 GB | ⚠️ 需 Docker/WSL2 + 嵌套虚拟化 | — | Navi **19.5%** vs 人 **74.5%** | https://microsoft.github.io/WindowsAgentArena/ |
| **OSWorld / OSWorld 2.0**（基准 + 协议） | 只借**评测协议**（集中重跑、公开轨迹、部分分检查点）与 agent 接口契约（`predict()`/`reset()`/`observation_type ∈ {screenshot, a11y_tree, som}`、`--max_steps 15`） | MIT（mm_agents） | — | ❌ 基准本体不跑（VM/AWS 基础设施） | — | OSWorld 2.0：Claude Opus 4.8 @500 步 **20.6%** 二值 / **54.8%** 部分分 | https://timothyxxx.github.io/OSWorld/user_guides/add_new_agent.html · https://arxiv.org/abs/2606.29537 |
| **UFO² Hybrid Control Detection** | UIA + 视觉框**合并成一张控件表**的现成规则（**IoU 0.1**） | MIT | — | ✅ 只抄规则 | 物理像素（论文未写坐标系，**我们必须自己定死**） | WAA：UIA-only 23.4% → Hybrid 26.6%（o1：25.3% → 27.9%） | https://microsoft.github.io/UFO/ufo2/core_features/control_detection/hybrid_detection/ |
| **winapp CLI**（微软） | 只当**权威对照实现**（ambiguous_selector 不取第一个、不可调用的祖先回退、注入前重解析 + target_moved、WinUI3 windowless 控件 + UIPI 阻断） | MIT | MSIX x64 27.6 MB | ✅ 能 | 官方只写 "screen coordinates"，**物理/逻辑待本机实测** | — | https://learn.microsoft.com/en-us/windows/apps/dev-tools/winapp-cli/ui-automation |
| **Anthropic computer-use demo** | 坐标**契约**参考（声明 screenshot 尺寸 + 越界检查）；zoom 契约**不适用我们** | 见仓库 | — | ✅ 只抄代码 | 截图像素 | — | https://github.com/anthropics/anthropic-quickstarts/blob/main/computer-use-demo/computer_use_demo/tools/computer.py |
| ~~OmniParser v2~~ | ❌ **架构禁止**（`docs/spec.md` §12）：ScreenSpot-Pro 39.6%、0.6~0.8 s/帧、许可三方打架（仓库 CC-BY-4.0 / README MIT / 旧检测器 AGPL） | 含糊 | 1.37 GB | — | 归一化 0~1 xyxy | 39.6% | https://github.com/microsoft/OmniParser |
| ~~mcp-windows / windows-mcp / WinAppDriver / pywinauto / uiautomation~~ | ❌ **本轮不接**：只解决"怎么读 UIA 树"，不解决"树里没有这个元素"；且 mcp-windows 有**已确认的 150% DPI 坐标 bug**（issue #218，根因 app.manifest 无 DPI 声明 + GetWindowRect 被虚拟化）—— 正是红线①要防的那类 | MIT / BSD-3 / Apache-2.0 | — | — | 物理像素相对该显示器（**有已知 bug**） | controls-first 8/8 vs screenshot-only 7/8、token 少 34.9% | https://github.com/sbroenne/mcp-windows/issues/218 · https://github.com/cursortouch/windows-mcp |

**下载注意**：本机 `huggingface.co` **DNS 不可达**（curl 000），`hf-mirror.com` / `modelscope.cn` 返回 200 → 拉权重走镜像。

---

## 收口后的验收线

**全部写成可判定的数字。** 基线列是**我们自己的实测**，右侧是本方案要达到的线。

### A. 视觉通道准入（`src/minicode/calib/admission.py`）

| 指标 | 当前实测基线 | 验收线 |
|---|---|---|
| 标定准入门 `admit().available` | **恒 False**（6 次标定全部 `max_error` > 1 px） | **6 次独立标定会话中 ≥ 5 次 `available == True`** |
| 等价标度族落点分歧 | 2.87 px（0~999 vs 0~1000，实测） | **≤ 4.0 px**（= 新常量 `SCALE_EQUIVALENCE_TOLERANCE_PX`） |
| 96 px 方块命中率（K=5 轮） | 无此指标（此前按 max_error 判） | **每次会话 ≥ 0.6**；6 次会话 ≥ 5 次达标 |
| 单轮落点误差 p90 | 68.81 px（6 次标定的上界） | **≤ 70 px（不许回归）**；改善目标放到下一轮 |

### B. 视觉通道落点（crop 精定位后的最终物理落点）

| 指标 | 当前实测基线 | 验收线 |
|---|---|---|
| L0 整屏粗定位误差 | 3.4~7.6 px | 不追求改善（B-2 出框后它只用来提议区域） |
| L1 crop 精定位落点误差 | **2.2~6.7 px**（另一处口径记的是中位 5.83~15.03 px、p90 51.48~202.01 px） | **统一口径后**：同一批 42 条 ScreenSpot-Pro 抽样上，**中位 ≤ 8 px、p90 ≤ 25 px** |
| "选编号"分支落点偏差 | M4 UIA 通道实测 **0.0 px** | **视觉通道选编号分支 ≤ 1.0 px**（取框中心，理论 0.0，允许 1 px 整数取整） |
| 门控可判定条件数 | **3/4 不可判定**（17/17 样本，M7 §6.9） | **`undecidable` 数 = 0**（同一批抽样） |
| 门控命中率 | 0/17 | **≥ 60%** |
| 平均模型调用数/目标 | 未记账 | **≤ 1.5**（按公开榜首的记账方式报，见 https://gui-agent.github.io/grounding-leaderboard/ 的 2.574 calls/example 口径） |

> ⚠️ **前置动作**：我们内部对"落点误差"有**两套口径**（M7 §3.2 的 L0/L1 vs M9 §6.12 的落点偏差），
> 必须先在一个脚本里统一出数，否则下面的线不可判。**这是 D-0，第一天做。**

### C. 多步任务（真机 e2e）

| 工单 | 当前实测基线 | 验收线 |
|---|---|---|
| Windows 设置 wave1 | **0%**（第一波全灭，UWP 树全空） | **2/2 票通过** |
| wave2（含微信 / Electron / cc-switch） | **4/6 = 67%**（失败的全是"树里没有 → 切视觉 → 标定过不了 → 零动作"） | **≥ 5/6**；且 **cc-switch 两票走 CDP 后 2/2、模型调用 = 0** |
| 微信发消息票 | 人工代确认 | **保持**（审批闸门不放松） |
| 每轮输入 token | 3417 token/轮（每轮固定带一帧）+ 历史帧 | **中位数下降 ≥ 30%**（observe() 改造后） |
| 停止行为 | M10：连续 3 轮自称完成却不停止 | **0 次**；且「跳过验证」这一类**能被检测器点名**（OSWorld 2.0 四类失败分类） |

### D. 工程量

| 批次 | 内容 | 预算 |
|---|---|---|
| **P0** | A-1 准入门 · A-2 输入尺寸声明+反查 · B-1 选编号落点 · B-4 描述规范化 · D-1 observe() · D-2 校验读数 · D-6 cc-switch CDP | **≤ 5 个工作日** |
| **P1** | B-2 出框 · B-3 Pre-Zoom · C-1 crop 扫描 · C-2 迭代 zoom · C-3 zoom-consistency · D-3 上下文裁剪 · D-4 停止条件 · D-5 窗口标题 | **≤ 8 个工作日** |
| **P2** | B-5 输出格式 · B-6 本地模型支线 · §4 组件表里的模型选型 | **另开工单** |

### E. 门控收益怎么记账（照公开榜首的写法，别自己发明）

【实测】M23 的结论是"门控可归因准确率收益 = 0，唯一确定收益是省模型调用"。
公开榜首 GroundCascade 的描述字段就是这么写的：
`Training-free geometric consistency cascade; fixed 0.5 zoom; 1% self-consistency gate; 4% cross-model agreement gate; 2.574 average model calls/example`。
**所以我们的对外结论写成：「省了 X% 模型调用，精度不掉」**，而不是"门控提升了准确率"。
【一手】另有一篇同方向的 null result：zoom-consistency 路由 **+0.8%（McNemar p=0.19，不显著）**
（https://arxiv.org/abs/2604.15376）—— 与我们的结论互相印证。

---

## 不做的事

| 这一轮**不做** | 为什么可以先放着 |
|---|---|
| **换本地 grounding 模型**（UGround-V1-2B / Jedi-3B-1080p / ShowUI-2B / Qwen-GUI-3B） | ① 6GB 显存上**没有任何一个候选有一手的实测延迟**（官方只在 A100/4090/H200 测过：OmniParser 0.6 s/帧 @A100、Seg2Click-3B 42.49~63.59 ms @H200）；② 换模型会改坐标契约与标定链路 = 另一个工单；③ **最关键的收益（标定抽签消失）已经由 A-1 用零成本拿到了**。 |
| **引入 OmniParser / 任何 SoM 分割模型** | `docs/spec.md` §12 **架构禁止**；且许可三方打架、旧检测器 AGPL、部署要 A100 80GB 独立 VM。 |
| **动安全 / 审批 / 审计 / 脱敏** | 现有实现已满足红线④；本轮新增的"OCR 词表 / 窗口标题"都**必须过现有脱敏器**，但**不新造脱敏机制**。 |
| **动 MCP / HTTP / CLI 对外形态** | 对外形态不是瓶颈（瓶颈在感知层）；改了还要重新写契约测试。 |
| **建评测基建**（ScreenSpot-Pro 全量 1581 条跑批、WAA 沙箱、OSWorld 环境） | WAA 要 30 GB 镜像 + 嵌套虚拟化 + Docker/WSL2；**我们把 WAA 定位成"抽 2~3 条做 sanity"**，不在日常回归里。但 **1581 条的本地数据已经在手**（`.scratch/sspro`），把它接进 `src/minicode/eval/sspro.py` 是一行的事，值得顺手做。 |
| **接 winapp CLI / pywinauto / uiautomation / mcp-windows** | 接进来会变成**第二套 UIA 真值来源**（违红线①）。winapp CLI 只当**权威对照实现**（抄 4 条设计）。 |
| **无条件 crop（每步必做精定位）** | 红线③ + `docs/spec.md` §11 第四条同源规则（"兜底要罕见"）。`GateMode.ALWAYS` 只留给评测台当对照组。 |
| **建工单 / 改代码 / 改工单系统** | 本轮只出方案文档。**但落地时 P0/P1 的每一项都应各开一张工单**，且工单里必须带上本文件的验收线数字。 |
| **「分母到底是 999 还是 1000」的争论** | 2.87 px ≪ 96 px 命中区 → **在命中判据下自动等价**，不必分清。等价列表照记（`landing_equivalence`）。 |
| **「标定容差从 1 px 改成 2 px」** | 那条路只救 ~10% 的任务（UFO² Table 3），而我们卡住的是**闸门本身**。A-1 不放松 1 px，只把它**归位**。 |

---

## 证据强度总表

### 有实测数字支撑（我们自己在本机量出来的）

| 结论 | 数字 | 谁量的 |
|---|---|---|
| 标定误差物理上到不了 1 px | 1.06~67.07 px（6 次标定：66.28 / 1.90 / 28.61 / 1.06 / 2.66 / 68.00 / 68.81） | 本项目 M9 / M14 / M15 |
| 0~999 与 0~1000 的落点分歧 | 2.56 px（x 轴）/ **2.87 px**（欧氏），2560 宽 | 本项目 `calib/tolerance.py` 头注释 + `docs/spec.md:37` |
| L0 整屏粗定位误差 | 3.4~7.6 px | 本项目 M6/M7 |
| L1 crop 精定位落点误差 | 2.2~6.7 px（另有口径：0.8~2.3 px；中位 5.83~15.03、p90 51.48~202.01） | 本项目 M9 / 底稿 |
| UIA 通道「模型只答行号 → 落点取框中心」 | **0.0 px、434.3 ms、模型调用 1 次** | 本项目 M4 §6.6 |
| UIA 通道端到端 | 落点 0.0 px / 434 ms / 模型调用 **0** 次 | 本项目 M3 |
| 门控四条条件 | 3/4 不可判定，17/17 样本 | 本项目 M7 §6.9 |
| 同一 prompt 两次粗定位 | 35/42 不同；14.3 pp 噪声面 | 本项目 M6 / M8 / M23 |
| Windows.Media.Ocr 真机可跑 | 三处全通（缩放值 / 微信标题 / 时钟） | 本项目 M27 |
| 每张整屏图的 token | ≈ 3417（请求日志 3417/3437） | 本项目请求日志 |
| e2e 通过率 | wave1 **0%**；wave2 **4/6 = 67%** | 本项目 M14 / M15 |
| cc-switch 走 browser 通道 | E-1~E-3 落点偏差 **0.0 px** | 本项目 M15 |
| 同批调参 + 同批评估 = 作废 | M23 直接证明 | 本项目 M23 |

### 一手来源（官方 README / 论文原文 / 官方文档 / 源码，可复核）

| 结论 | 出处 |
|---|---|
| 坐标契约 = **声明**发出去那张图的尺寸（`display_width_px/display_height_px`）+ 免费越界检查；zoom 图**不是**坐标帧 | https://github.com/anthropics/anthropic-quickstarts/blob/main/computer-use-demo/computer_use_demo/tools/computer.py |
| 生态判据是 **click accuracy**，不是像素误差；ScreenSpot-v2 1581 任务 / 23 应用 | https://arxiv.org/abs/2404.07990 |
| ScreenSpot-Pro：1581 指令 / 23 应用 / 3 OS；目标平均占图面积 **0.07%**；text 62.6% / icon 37.4%；UGround 分辨率上限 1344×1344；crop 尺寸消融（512 最差格）；5 个 coarse-to-fine 方法 | https://arxiv.org/abs/2504.07981 · https://github.com/likaixin2000/ScreenSpot-Pro-GUI-Grounding |
| ZoomClick：ρ / m / 三条终止条件 / 越界 shift / **每轮从原坐标系裁**；UI-Venus-72B ScreenSpot-Pro **73.1%** | https://arxiv.org/abs/2512.05941 |
| R-VLM：整图粗框当 region proposal，ScreenSpot/AgentStudio **+13%** | https://arxiv.org/abs/2507.05673 |
| UI-Ins：既有 GUI grounding 指令 **23.3%** 缺陷率；推理期多视角改写最高 **+76% 相对提升** | https://arxiv.org/abs/2510.20286 |
| GUI-RC：点式模型增益仅 **+0.13~+0.26**（点式上限），bbox 式 +2.5~+4.2 | https://arxiv.org/abs/2508.05615 |
| Qwen3-VL：坐标从绝对改成相对 **0~1000**（分母写死在模型卡里） | https://github.com/QwenLM/Qwen3-VL/blob/main/cookbooks/2d_grounding.ipynb |
| Qwen2.5-VL：`smart_resize()` 可本地算出模型实际输入尺寸（绝对像素坐标） | https://github.com/QwenLM/Qwen3-VL/blob/main/qwen-vl-utils/src/qwen_vl_utils/vision_process.py |
| UI-TARS：坐标归一化到 [0,1000]；ScreenSpot-Pro 38.1 | https://arxiv.org/abs/2501.12326 |
| SoM：叠编号 + 模型只答编号（交互形态可抄，GUI 精度未知） | https://arxiv.org/abs/2310.11441 |
| GUI-Actor：坐标无关输出 / grounding verifier；7B ScreenSpot-Pro 40.7/44.6 | https://arxiv.org/abs/2506.03143 |
| OpenGCUA：**换 harness +19.6 分 / 步数 −53.0%**；**代码执行 vs 同等 GUI+CLI 权限 +6.1 分 / 步数 −14.8%**；按需观察缓存命中 +10.5/+14.4 pp | https://arxiv.org/abs/2610.22200 |
| OSWorld 2.0：失败归因四类原句；108 任务；500 步 Claude Opus 4.8 **20.6% / 54.8%**；平均 27.25 个检查点 | https://arxiv.org/abs/2606.29537 |
| OSWorld agent 接口契约（`predict()`/`reset()`/`observation_type`/`--max_steps 15`） | https://timothyxxx.github.io/OSWorld/user_guides/add_new_agent.html |
| WindowsAgentArena：154 任务；**只用像素 OCR+图标 的 agent 成绩低于同时用 UIA 树的**；Navi 19.5% vs 人 74.5% | https://microsoft.github.io/WindowsAgentArena/ |
| UFO² Hybrid Control Detection：`IOU_THRESHOLD_FOR_MERGE = 0.1`；WAA UIA 23.4% → Hybrid 26.6%（o1 25.3% → 27.9%）；救回 ~9.86% | https://microsoft.github.io/UFO/ufo2/core_features/control_detection/hybrid_detection/ |
| Anthropic tool-clearing：`clear_tool_uses_20250919`（trigger 100000 / keep 3 / clear_tool_inputs=False 只清结果保调用） | https://docs.aws.amazon.com/bedrock/latest/userguide/model-parameters-anthropic-claude-messages-tool-use.html |
| zoom-consistency：step-2 点到 crop 中心距离；AUC 0.60、rho −0.14/−0.11；路由 +0.8%（McNemar **p=0.19 不显著**） | https://arxiv.org/abs/2604.15376 · https://gui-agent.github.io/grounding-leaderboard/ |
| Windows.Media.Ocr `OcrWord.BoundingRect` = 识别词的像素位置 | https://learn.microsoft.com/en-us/uwp/api/windows.media.ocr.ocrword?view=winrt-26100 |
| WebView2 远程调试参数（须先于环境创建设置） | https://learn.microsoft.com/en-us/microsoft-edge/webview2/how-to/debug-visual-studio-code |
| winapp CLI 的 selectors / arbitration / 错误码；MIT；v0.7.1 | https://learn.microsoft.com/en-us/windows/apps/dev-tools/winapp-cli/ui-automation |

### 二手转述（本轮未取到一手，必须在落地前复核）

| 结论 | 缺什么 |
|---|---|
| Anthropic 28 px patch token 规则（`ceil(W/28)×ceil(H/28)`） | `platform.claude.com` 在本环境被重定向抓不到 → **该规则是否适用于 MiniMax 端点未验证** |
| UGround-V1-2B：Apache-2.0 / 4.4 GB bf16 / ScreenSpot-Pro 数字 | HF 直连不可达（DNS 解析失败），**体积分许可均二手**；ScreenSpot-Pro 官方 README（18.9→31.1）与论文 Table 2（7B=16.5）**口径冲突** |
| Jedi-3B-1080p 的 ScreenSpot-v2 88.6 / SS-Pro 36.1 / OSWorld-G 50.9 / WAA 29.1±0.57 | 项目页数字，未经本地复现 |
| Qwen2.5-VL-3B 权重 license = qwen-research（非商用） | HF 卡 front matter 二手（商用阻断是硬事实，落地前必须再核） |
| ShowUI-2B 权重 MIT / 2.2B / bf16 4.4 GB | 二手 |
| ScreenSpot-Pro-CN 的具体分数 | **未取到** —— 而我们的目标描述**全是中文**，这条直接相关 |
| 6GB 显存上各候选模型的延迟 | **完全没有一手数据**（官方只在 A100/4090/H200 测过） |

### 推断（本方案的推理，尚未验证 —— 落地时逐条验）

| 推断 | 怎么验 |
|---|---|
| 0~999 与 0~1000 在 96 px 命中区内**自动等价** | 跑 5 轮探针，比对两族落点是否都落在同一方块内 |
| 请求日志的图像 token 数可反推服务端实际输入尺寸 | 用一次已知尺寸的请求校准反查函数 |
| 「模型只答编号 + 落点取框中心」在视觉通道也能做到 ≈0 px | 在 42 条 ScreenSpot-Pro 抽样上跑，量落点偏差 |
| 每轮固定带帧改成按需 observe() 能降 ≥30% token | 同一批任务 A/B，比 `token_growth` 中位数 |
| 迭代 zoom（ρ=0.5、depth=3）在我们的模型上能压 p90 到 ≤25 px | C-1 的 crop 扫描 + C-2 一起跑 |
| MiniMax 端点接受「这张图是 1280×800，给像素坐标」的声明 | 标定记录里记「要求的分母」vs「实测的分母」，两者不符即证伪 |
| 门控四条条件能全部变可判定 | B-2 + B-3 落地后，同一批 42 条抽样上数 `undecidable` |
| 窗口标题进上下文能降多步失败率 | 在 wave2 的 6 票上 A/B |

### 已知口径混乱（落地第一天要先统一，否则验收线不可判）

| 冲突 | 出处 |
|---|---|
| 「落点误差」两套口径：L0 3.4/7.6 px → L1 2.3/0.8 px **vs** 落点偏差 2.2/6.7/6.1 px | `docs/01-现状.md` §3.2 **vs** `docs/reviews/M9.md:21` |
| 「模型坐标 = 0~1000」与标定判出的 `absolute_model_input` 两种口径并存 | `docs/00-现状.md` §3 **vs** §6.8 第 1 条 → **对外结论统一写「量纲由运行时标定判定」** |
| wave2 分母：`4/7 = 57%` **vs** `4/6 = 67%` | `docs/reviews/M15.md:38` |
| 「无条件做 crop 整体 −0.3 pp」**至今没有一手出处** | 多条调研线均记 → **在拿到一手数字前不许写进对外结论** |
| 「官方仓自带 5 个 coarse-to-fine 实现」未一手核实（GitHub API 403） | 论文明确的是 3 个 planner-free 方法 + ScreenSeekeR |
| `docs/00-现状.md:42` 与 `docs/spec.md:175` 把「Windows-MCP / sbroenne/mcp-windows」写成一个项目 | 实际是**两个**项目（cursortouch/windows-mcp 与 sbroenne/mcp-windows），后者有已确认的 150% DPI 坐标 bug（issue #218）→ 建议把 issue 号补进文档 |

---

## 落地顺序（第一周就能看到东西）

| 天 | 做什么 | 看什么 |
|---|---|---|
| D0 | 统一"落点误差"口径（一个脚本）；跑一次 6 轮标定，测出命中率先验 | 命中率基线数字 |
| D1-D2 | A-1 准入门换判据（tolerance / calibrate / admission / record / `__main__`） | 6 次标定里 ≥5 次 `available == True` |
| D3 | A-2 输入尺寸声明 + token 反查；提示词加尺寸声明 | 请求日志里图像 token 与反查一致 |
| D4 | B-1 OCR 词级框 → 候选源 → 编号选择 → 框中心落点 | 42 条抽样上落点偏差 ≤1 px |
| D5 | B-4 描述规范化 + D-6 cc-switch 走 CDP | 「显示/屏幕」用例命中；CC 两票通过 |
| D6-D7 | D-1 observe() + D-2 校验读数进程序 | 每轮 token 中位数 −30%；分支能跑 |
| D8+ | B-2 出框 → B-3 Pre-Zoom → C-1 crop 扫描 → C-2 迭代 zoom → C-3 zoom-consistency | 门控 `undecidable = 0`、落点 p90 ≤25 px |
| 待人拍板 | `docs/spec.md:37` + M5 工单口径同步（1 px 归位，不放松） | 签字 |

---

## 引用纪律提醒（来自 `docs/spec.md` §12，本方案已遵守）

1. OpenGCUA 的数字**只引用官方 README 正文里的两组数**（+19.6 / −53.0%；+6.1 / −14.8%）。
2. **不引入 OmniParser 做实时 grounding**（架构禁止）。
3. 可商用许可已核清：UFO、winapp CLI、mcp-windows（MIT）、Playwright MCP、OpenGCUA（Apache-2.0）、pywinauto（BSD-3）。
4. 评分是人工评分不是实测分；同一行里的"官方榜分数"才是实测/自报量纲，两者不可混读。
5. **公开分数一律是 click accuracy，不是像素误差** —— 本文件里所有"公开精度"列都不能与我们的像素距离直接比较。

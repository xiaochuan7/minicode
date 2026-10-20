# proto/coord — 坐标换算与运行时标定最小原型

> 目标：验证架构备忘 `docs/01-架构决策备忘.md` 里**最不确定的那块**——坐标换算链与运行时标定。
> 范围：纯标准库（无第三方依赖），**不调用任何付费 API**，**不碰鼠标/屏幕**（纯计算 + 合成图）。
> 关联调研底稿：`docs/research/bottom-drafts/02-coordinate-systems.md`（坐标体系查证，690 行）

---

## 1. 怎么跑

```bash
cd src\minicode\coord

# 1) 跑全部单元测试（49 个）
python -m unittest discover -s . -p "test_*.py" -v
#    等价写法：
python test_coordinates.py

# 2) 跑一次标定探针（默认桩模拟 0~1000，离线，无网络）
python calibrate.py

# 3) 遍历所有标度，验证检测器
python calibrate.py --all-scales

# 4) 模拟"模型输入被 smart_resize 缩放过"的绝对像素场景
python calibrate.py --scale absolute_model_input --model-input 1260x784

# 5) 把探针 PNG 导出来看一眼
python calibrate.py --write-probe probe.png
```

**Windows 控制台中文乱码**时先设编码（不影响结果，只是显示）：

```powershell
$env:PYTHONIOENCODING="utf-8"; $env:PYTHONUTF8="1"
```

### 已验证的运行结果

```
Ran 49 tests in 0.25s

OK
```

```
$ python calibrate.py
=== 桩标度: relative_0_1000 ===
标定结果: relative_0_1000  (max_error=0.00px, tol=1.0px, confident=True, unique=True)
候选明细:
  relative_0_1000          max_err=      0.00px mean=      0.00px <= 选中
  relative_0_999           max_err=      2.86px mean=      1.74px
  absolute_capture         max_err=   1587.08px mean=    930.07px
  absolute_model_input     max_err=   1587.08px mean=    930.07px
  relative_0_1             max_err=       infpx mean=       infpx
  ...
  note: 探针宽度 2560px：0~1000 与 0~999 的最大物理分歧约 2.86px
```

---

## 2. 坐标系图

```
                          ┌──────────────────────────────────────────┐
                          │  物理表面空间 (physical surface space)    │
                          │  ★ 唯一真值空间                            │
                          │  原点 = 虚拟桌面左上角（多屏时可为负）      │
                          │  单位 = 真实显示器物理像素                  │
                          │  目标机：2560×1600 物理 / 150% DPI        │
                          └──────────────▲───────────────────────────┘
                                         │  capture_to_physical()
                                         │  物理 = 截图像素 × scale + 虚拟桌面原点
                                         │  （PMv2 感知路径下 scale ≡ 1.0）
                          ┌──────────────┴───────────────────────────┐
                          │  截图像素空间 (capture space)              │
                          │  本次截图这张图的像素，左上原点            │
                          │  2560×1600 物理截图  或  1707×1067 逻辑截图 │
                          └──────────────▲───────────────────────────┘
                                         │  relative_to_capture()
                                         │  截图像素 = 相对坐标 × 截图宽高
                          ┌──────────────┴───────────────────────────┐
                          │  相对空间 (relative space)                 │
                          │  归一化到 [0,1]×[0,1]，与分辨率无关        │
                          │  ★ 换算链的中枢                            │
                          └──────────────▲───────────────────────────┘
                                         │  model_to_relative()
                                         │  相对 = 模型原值 / 名义分母
                          ┌──────────────┴───────────────────────────┐
                          │  模型空间 (model space)                   │
                          │  ★ 量纲由 ModelScale 显式配置，不写死      │
                          │  0~1 | 0~999 | 0~1000 | 绝对像素(截图像素) │
                          │                           | 绝对像素(模型输入)│
                          └──────────────────────────┴────────────────┘
```

**旁路（仅绝对像素标度需要）：**

```
  截图像素 ──capture_to_model_input()──▶ 模型输入像素
      ▲                                       │
      └────────── model_input_to_capture() ◀──┘
      （Qwen2.5-VL / UI-TARS 的 smart_resize 缩放就在这一段）
```

**crop 精定位的坐标系复用（关键设计）：**

```
  全屏物理表面                    crop 自己的 FrameGeometry
  ┌────────────────────┐         ┌────────────────────┐
  │        ·p0         │  ────▶  │  虚拟桌面原点 = crop 原点 │
  │   ┌────────────┐   │  plan   │  capture 1:1 物理    │
  │   │  crop 512  │   │         │  surface = crop 自身  │
  │   │  目标 T    │   │         └────────────────────┘
  │   └────────────┘   │                    │
  └────────────────────┘                    │ 同一个 CoordinateAdapter
                                             ▼
                       crop 内坐标 ──▶ 全屏物理像素（无需另写换算）
```

---

## 3. 文件与函数语义

### 3.1 `coordinates.py` — 换算层

| 符号 | 语义 |
|---|---|
| `ModelScale` (Enum) | **模型输出量纲**。`RELATIVE_0_1` / `RELATIVE_0_999` / `RELATIVE_0_1000` / `ABSOLUTE_CAPTURE`（截图像素）/ `ABSOLUTE_MODEL_INPUT`（模型输入像素）。`ModelScale.parse("0-1000")` 等宽松解析。 |
| `Rect` | 物理表面上的矩形，用于 `safe_area` 白名单。`contains()` / `clamp_point()`。 |
| `FrameGeometry` | 一帧的元数据（不可变 dataclass）：`capture_width/height`（截图宽高）、`model_input_width/height`（模型输入宽高）、`capture_to_physical_scale`（物理缩放因子）、`virtual_origin_x/y`（虚拟桌面原点）、`surface_width/height`（物理坐标面尺寸，用于越界判定）。 |
| `CoordinateAdapter` | 换算器。构造参数 `model_scale` + `out_of_bounds_tolerance_px`（默认 2.0）+ `strict_model_range`。 |

**`CoordinateAdapter` 的方法（每层都可单独调用、单独断言）**

| 方法 | 输入坐标系 → 输出坐标系 |
|---|---|
| `capture_to_model_input(x, y, frame)` | 截图像素 → 模型输入像素 |
| `model_input_to_capture(x, y, frame)` | 模型输入像素 → 截图像素 |
| `model_to_relative(x, y, frame)` | 模型空间 → 相对空间 [0,1] |
| `relative_to_capture(x, y, frame)` | 相对空间 [0,1] → 截图像素 |
| `capture_to_physical(x, y, frame)` | 截图像素 → 物理表面像素 |
| `physical_to_capture(x, y, frame)` | 物理表面像素 → 截图像素 |
| `model_to_capture(x, y, frame)` | 模型空间 → 截图像素（第 1、2 层合并） |
| **`to_screen(x, y, frame, safe_area=None)`** | **模型空间 → 物理表面像素**（完整链路 + 越界 + safe_area 判定） |
| `from_screen(x, y, frame)` | 物理表面像素 → 模型空间（`to_screen` 的逆，用于编码真值 / 往返自检） |
| `model_output_bounds(frame)` | 该帧下模型空间的合法数值区间 |
| `assert_model_output_in_range(...)` / `assert_within_surface(...)` | 越界判定（抛异常，不 clamp） |
| `in_safe_area(...)` / `assert_safe_area(...)` | safe_area 白名单校验 |

**异常**

| 异常 | 何时抛 |
|---|---|
| `CoordinateOutOfBounds` | 模型输出越界，或换算后物理坐标落在表面之外。**不 clamp**。 |
| `SafeAreaViolation` | 物理坐标不在 safe_area 白名单内。 |
| `ModelScaleError` | 标度缺失或与帧元数据冲突。 |

### 3.2 `calibrate.py` — 运行时标定探针

| 符号 | 语义 |
|---|---|
| `ProbeTarget` | 探针上的一个高对比目标（物理像素坐标）。 |
| `default_targets(w, h, margin, size)` | 四角各放一个目标。**刻意贴近角落**——靠右的目标是区分 0~999 / 0~1000 的关键。 |
| `build_probe_image(...)` / `encode_png(...)` | 纯标准库 PNG 编码（`zlib` + `struct`），生成的探针图可直接喂给真实 VLM。 |
| `RawPoint` | 模型返回的**原始**数值，**不做任何解释**——解释正是标定要判定的事。 |
| `GroundingResult` | 一次 grounding 的结果：点列表 + 模型实际看到的图像尺寸。 |
| `GroundModel` | **模型接口**。只有 `ground(image, instruction) -> GroundingResult` 一个方法。 |
| `StubGroundModel` | 离线桩：按指定标度把真值编码成模型输出，可加确定性噪声。 |
| `CallableGroundModel` | 把任意 `callable(image, instruction) -> GroundingResult` 包成模型。**接真实模型的替换点。** |
| `CalibrationProbe` | 探针主体。`run()` → `CalibrationResult`。 |
| `CalibrationResult` | `detected_scale` / `candidates` / `confident` / **`unique`** / `equivalent_scales` / `divergence_px` / `roundtrip_ok` / `notes`。 |

### 3.3 `crop_refine.py` — 第二级 grounding

| 符号 | 语义 |
|---|---|
| `plan_crop(center, surface, crop_width, crop_height, target_radius_px, upscale)` | 物理表面空间 → `CropPlan`。越界时 **shift**（平移，尺寸不变）或 **shrink**（表面比 crop 还小时缩到表面大小），**不裁切目标**。 |
| `CropPlan` | crop 窗口的物理位置与尺寸。`shifted` / `shrunk` / `target_fully_contained` / `upscale`。 |
| `CropRefiner.crop_frame(plan, model_input_size)` | 构造 crop 的 `FrameGeometry`：**虚拟桌面原点 = crop 原点**，于是同一个适配器直接给出全屏物理坐标。 |
| `CropRefiner.crop_to_physical(x, y, plan)` | crop 像素 → 物理表面像素。 |
| `CropRefiner.physical_to_crop(x, y, plan)` | 物理表面像素 → crop 像素。 |
| `CropRefiner.plan(coarse, surface, target_radius_px)` | 物理表面空间 → `CropPlan`。 |
| `CropRefiner.refine(coarse, raw_output, surface, ...)` | **模型空间（crop 内）→ 全屏物理像素**。主入口。 |
| `CropRefiner.refine_relative(coarse, rel_output, surface)` | 便捷版，直接给 crop 内 0~1 相对坐标。 |

---

## 4. 接真实模型时替换哪个桩

**只改一处**：`calibrate.py` 的 `CallableGroundModel`。

```python
from calibrate import CalibrationProbe, CallableGroundModel, GroundingResult, RawPoint
from coordinates import CoordinateAdapter

class MyVlmClient:
    def ground(self, image: bytes, instruction: str) -> tuple[float, float]:
        """调你的 API（百炼 gui-plus / qwen3-vl-flash / 本地 vLLM ...），
        返回模型**原样**的数值，不要提前换算。"""
        ...

def make_probe(client: MyVlmClient) -> CalibrationProbe:
    def call(image: bytes, instruction: str) -> GroundingResult:
        x, y = client.ground(image, instruction)
        # input_width/height 填模型**实际看到**的图尺寸
        # （被 smart_resize 缩放过就填缩放后的，否则填截图尺寸）
        return GroundingResult(points=(RawPoint(x, y),),
                               input_width=..., input_height=..., model_id="my-vlm")
    return CalibrationProbe(CallableGroundModel(call), width=2560, height=1600)

result = make_probe(MyVlmClient()).run()
print(result.summary())

adapter = CoordinateAdapter(result.detected_scale)   # ← 标定结果直接进适配器
```

三条注意事项：

1. **`RawPoint` 里放模型原样返回的数值**，不要提前换算——换算正是标定要判定的事。
2. `input_width/height` 要填**模型实际看到的**尺寸。若模型内部把图缩到 1260×784，
   就填 1260×784，否则 `ABSOLUTE_MODEL_INPUT` 会被误判成 `ABSOLUTE_CAPTURE`。
3. **探针宽度要够**。低于约 2000 px 时分不出 0~999 与 0~1000（见下节）。
   探针图用 `build_probe_image()` 生成，是标准 PNG，可直接 base64 发给模型。

---

## 5. 实现中发现的技术陷阱（与文档不一致 / 需修正之处）

### 5.1 探针宽度决定能不能区分 0~999 与 0~1000（**文档未提**）

> ⚠️ **2026-10-20 更新**：架构备忘 §1.4 现已明确要求**探针用 2560×1600、判定容差 1 px**（本节原文写的"架构备忘 §1.4 建议探针用 1280×720"已过时）。
> ✅ **2026-10-20 已修**：本原型的 `CalibrationProbe.tolerance_px` 默认值已从 **2.0 改为 1.0**（CLI `--tolerance` 默认同步改为 1.0），与架构备忘 §1.4 规格一致；新增测试 `test_default_tolerance_separates_999_from_1000_on_full_width_probe` 钉住"2560 宽 + 1px 容差必须能分开 0~999/0~1000"，并把窄探针测试改为显式传 `tolerance_px=2.0` 以保留"容差取 2px 时窄探针静默判错"这一失败模式的覆盖。当前 50 个测试全绿。
> **注意两种容差语义不同，不要再混用**：`CalibrationProbe.tolerance_px`（默认 **1.0**）判"能否区分标度"；`CoordinateAdapter.out_of_bounds_tolerance_px`（默认 **2.0**）判"是否越界"，对应行动协议里的 2 px 越界容差。见 `docs/01-架构决策备忘.md` §1.4 与 §2.6 检查项 C-03。

架构备忘 §1.4 要求探针用 **2560×1600**。但 0~999 与 0~1000 对**同一原始数值**的解释差：

```
|物理x / 999|  （推导：原始值 r = 物理x/W*1000，两解释差 = r*W/999000 = 物理x/999）
```

- **x 轴单轴分歧**（最右目标贴边、n=1000 时）：1280 宽 → **1.28 px**；2560 宽 → **2.56 px**
- **欧氏距离**（本原型 `calibrate.py` 的分歧估算函数取右下角目标的 `hypot`，margin=96、目标 64 px，即 x=2432 / y=1472）：1280 宽 → ≈1.30 px；**2560 宽 → ≈2.86 px**（与下方桩运行输出一致）
- 2 px 容差下 1280 宽的 1.30 px **与容差同量级 → 检测不出来**；1 px 容差下 2560 宽的 2.86 px **留有 2~3 倍余量 → 稳健可辨**

**结论：标定探针至少要用 2000 px 以上的宽度**（实际取目标机物理宽 2560），否则 §1.4 的标定对
0~999 / 0~1000 这一族是无效的。本原型默认 2560×1600，并在
`CalibrationResult.unique` 里显式暴露"是否唯一可判定"。

### 5.2 `capture_to_physical_scale` 与文档"不要写 dpi_scale"存在张力

架构备忘 §1.5 明确写"**不要写 `dpi_scale: 1.25` 这种配置项**（DPI 感知之后，
进程内部拿到的就是物理像素，整套换算里根本不需要出现缩放系数）"。

但任务要求元数据里带"物理缩放因子"。本原型的处理：

- `capture_to_physical_scale` **默认 1.0**，并保留该字段；
- 文档字符串与 README 明确标注：**在 PMv2 感知进程里它恒为 1.0**，
  只有走 **DPI-unaware 抓图路径**（位图被虚拟化成逻辑像素）才需要非 1.0；
- 测试 `test_dpi_aware_capture_needs_no_scale_factor` 把这个约束钉住。

**即：保留字段是为了兜住"抓图路径不统一"这个真实的失败模式，而不是把它当成常规配置项。
建议接入 L0 时直接把该字段固定为 1.0，并对非 1.0 的情况打警告日志。**

### 5.3 `ABSOLUTE_CAPTURE` 与 `ABSOLUTE_MODEL_INPUT` 在"模型输入==截图"时不可区分

若模型输入尺寸 == 截图尺寸（例如 Claude/OpenAI 这种直接吃原图的），
两种绝对标度解释的是**同一组数字**，误差都是 0，检测器只能任选其一。

本原型的处理：候选顺序固定 → 稳定选 `ABSOLUTE_CAPTURE`；同时把另一种放进
`equivalent_scales`、把 `unique` 置 False，并在 CLI 里打印"不可区分"而不是报失败。
**这不影响正确性**（两者算出的物理坐标完全相同），但调用方必须知道"这个标度是二选一的"。

### 5.4 越界判定会先在"模型空间"触发，再在"物理空间"触发

原以为只有物理空间需要判越界。实际实现里**两层都要判**，且模型空间那层更早触发：
把 0~1000 的 953 当 0~1 读时，`model_output_bounds` 直接判定越界并抛异常。
若不做这层，错误会一路带到物理空间才暴露，误差被放大到上千 px 才看得见。

标定器因此必须**捕获** `CoordinateError` 并记为 `inf` 误差，否则第一个候选就会中断整个标定流程。

### 5.5 分歧度要按"物理坐标"算，不是按"模型单位"算

区分两个标度是否"要较真"，正确的度量是**两个解释在同一目标上落点差多少物理像素**，
不是模型单位差多少。标定器额外输出 `divergence_px`（每个候选与选中候选的最大物理分歧），
调用方据此判断"选错要紧不要紧"。

---

## 6. 本原型**没有**覆盖的部分（留给后续）

- **真实抓图 / 真实 DPI 感知 / 真实鼠标下发** —— 本原型纯计算，不碰设备。
  `FrameGeometry` 的字段就是为接 L0 预留的。
- **真实模型调用** —— 只留了 `CallableGroundModel` 接口与 `RawPoint` 原样透传约定。
- **多屏实测** —— 只用合成帧验证了"虚拟桌面原点为负"的换算，没有真的插两块屏。
- **crop 放大（upscale ≠ 1.0）** —— 参数与换算已就位（Lanczos 放大到 1024 的场景），
  但没有做像素级回归测试。
- **safe_area 的实际白名单来源** —— 目前只能手工构造 `Rect`，
  真实应由 L0 从显示器布局推导。

---
status: accepted
---

# 评测目标脚本的坐标口径：内部一律原图像素，出口按目标脚本转一次

评测侧（ScreenSpot-Pro 同表跑这类活）的坐标口径统一为：**内部一律用原图像素**，各家评测脚本互不相同的坐标期望（归一化 [0,1] / 原图像素 / 0~1000 网格 / `[x,y,W,H]` 宽高形式 bbox）**只在 wrapper 返回前按目标脚本转一次**，转一次就够，不在两处各转一次。理由：各家的 GT 与 point 口径互不相同且**混用不报错**——把像素点当归一化喂给 ScreenSpot-Pro 官方脚本，命中率是 0 而不是异常；把 `[x,y,W,H]` 当 `[x1,y1,x2,y2]` 读，同一个点的命中结论会反过来。内部口径只有一套（物理像素 / 原图像素），口径差异集中在出口一处，才谈得上「事后能核对」。

## 两套命名的边界

**模型侧**叫「量纲分母」（0~1 / 0~999 / 0~1000 / 绝对像素），只能来自**运行时标定**（C-03 / C-04），代码里不许写死；**评测脚本侧**的是**输出格式**（目标脚本要的形状），不是分母。两套命名分属不同域、来源可区分：`minicode.eval` 包**不 import** `minicode.coord`，格式常量一律叫 `EvalPointFormat` / `EvalBBoxFormat`，不叫 scale / denominator。

## 四个目标脚本的实测口径（以实际脚本为准）

| 目标脚本 | point 期望 | GT bbox 格式 | 出处 |
|---|---|---|---|
| ScreenSpot-Pro 官方 | 归一化 [0,1] | `[x1,y1,x2,y2]` 像素 | `eval_screenspot_pro.py::eval_sample_positive_gt` |
| ZoomClick 副本 | 原图像素 | `[x1,y1,x2,y2]` 像素 | `grounding/eval_sspro_zoomclick.py::eval_sample_positive_gt` |
| CCF 仓 ScreenSpot-v2 | 原图像素 | `[x,y,W,H]` 宽高形式 | `src/eval.py`（ScreenSpot-v2 分支） |
| ShowUI | **0~1000 网格** | `[x,y,W,H]` 宽高形式 | `main/eval_screenspot.py::get_bbox` |

⚠️ **ShowUI 的网格值 = 1000，不是 999**：`get_bbox` 里写的是 `int(item * 1000)`，系统提示词也写「scaled from 1 to 1000」。底稿里「0–999/1000 两说」的传闻到此为止——**以实际脚本为准**。0–999 与 0–1000 在 2560 宽的图上只差 2.56 px，**落错一档不报错**，只会让压在边界上的样本悄悄判错，所以它被单测钉死。

## Considered Options

- **每个目标脚本各写一份 wrapper**：口径散在多处，改一个脚本要改几处，且「转了几次」没人说得清。
- **直接把内部坐标当评测口径**：只有当所有脚本都恰好吃原图像素时才成立，而它们不恰好（官方脚本吃归一化）。

## Consequences

- `minicode.eval.formats` 是**口径转换的唯一一处**：`wrap_point`（原图像素 → 目标格式）、`bbox_xyxy_to_xywh` / `bbox_xywh_to_xyxy`、`point_in_bbox`（目标脚本的命中判定）、`self_check_hit_rate`（防假象自检：GT 中心当预测点，命中率必须接近 100%，不是 0）。
- 任何新接的评测脚本都要在 `minicode.eval.formats` 里登记一个 `EvalTarget`（口径 + 出处），不许在调用点现算。
- 转换**只转一次**：GT 的脚本侧格式由判定处转一次，预测点由 wrapper 转一次；两边各转一次就会抵消或错位。
- 口径转换与**模型量纲**无关：模型给的数值先由标定换算到物理表面空间，再由 wrapper 转成脚本要的形状。

# V5 视觉必需任务集说明

> 状态行：runner 与任务定义已入库（`scripts/v5_vision_tasks.py`）；
> **实测数字见 `docs/v6-abc-comparison.md`**（A/B/C 三组同台跑出后回填口径）。

## 为什么需要这个台

W4 的 30 条任务级评测台是 **UIA 主导**的：30 条任务的每一步都走 UIA 通道
（trace 里 `model_calls=0`）—— 视觉通道从未被任务级评测触达。
「99.6% success@1」因此**不证明视觉能用**。本台补 8 条**结构通道原理上
做不到**的任务：目标全部画在 `<canvas>` 里，DOM 无对应节点，UIA 树里只有
一个不透明的画布元素 —— UIA 定位必然 `TARGET_NOT_FOUND`（覆盖失败，
唯一允许切视觉的情形）→ 视觉通道兜底点击。

## 任务集（4 页 × 2 条 = 8 条）

| id | 页面 | 目标形态 | 覆盖的「视觉必需」类别 |
| --- | --- | --- | --- |
| w01 | canvas-text-buttons | 画布里写「删除」的文字按钮 | Canvas 页面里的文字目标 |
| w02 | canvas-text-buttons | 画布里写「取消」的文字按钮 | 同上 |
| w03 | canvas-icon-buttons | 实心三角形（播放）**无字**图形按钮 | 无 automation_id 的图标按钮 |
| w04 | canvas-icon-buttons | 实心正方形（停止）**无字**图形按钮 | 同上 |
| w05 | canvas-palette | 第一行最左的红色**无字色块** | 无字图标 |
| w06 | canvas-palette | 第二行最左的蓝色**无字色块** | 同上 |
| w07 | canvas-scene | 左侧红色圆形（**图片里的目标**） | 图片/图形内容目标 |
| w08 | canvas-scene | 右侧黄色三角形 | 同上 |

页面由 `scripts/v5_vision_tasks.py::_write_pages` 生成（`runs/v5/pages/`，
gitignored），点击命中后页面把 `V5HIT:<名>` 写进 `document.title` 并在
canvas 上画出高亮框 —— 命中位置因此可事后核对。

## 判分口径（铁律：只读状态）

judge 读**窗口标题**（ctypes `GetWindowTextW`），要求包含
`V5HIT:<期望名>` —— **不读截图**。标题是浏览器窗口状态，与 W4 的
「UIA 控件值 / 文件内容 / 窗口标题」同一族状态源。

## 步骤与通道

每条任务一个 click 步骤（`target=自然语言描述`），走带护栏的接线
（L1 白名单 Edge/msedge/Chrome + 预算 + 急停 + 录制）。
UIA 找不到 → 覆盖失败 → 视觉准入闸门 → 两级 grounding → 物理落点 → 注入点击。
trace 里 `channel == "visual"`、`model_calls ≥ 1` 是「视觉通道真的被用了」
的直读证据（汇总里单列 `vision_channel_used`）。

## 真机纪律（与 W3/W4 同一套）

- `hold_desktop_lock` 包住整轮（拿不到锁拒绝动真机）；
- 只操作自己 spawn 的实例（差分法找窗口 + 类名白名单 `Chrome_WidgetWin_1` /
  `ApplicationFrameWindow`）；
- spawn 后**前台断言**（拿不到前台拒绝盲注）；
- 关窗三件套：WM_CLOSE → 存活验证 → terminate 进程；
- 标定一次共用整批（`CalibrationProbe` → `CalibrationAdmission`），
  full_screen_size 用**虚拟桌面口径**（抓帧口径，非主屏口径）。

## 分组（V6 复用同一台）

- `--group a`：现状（无候选源、无重排）；
- `--group b`：+ 图标候选源 —— ScreenParser（Apache-2.0）优先，
  环境没有 torch → 零模型图标提议回退（用了哪个如实打印）；
- `--group c`：B + d3 重排（`D3CandidateReranker`，gap<0.1 才调、
  confidence<0.5 不点）。

## 结果

见 [`docs/v6-abc-comparison.md`](v6-abc-comparison.md)（三组 A/B/C 同台对照、
success@1、视觉通道使用次数、d3 调用数与低置信拒绝数）。
本文件只描述任务集设计与纪律；所有实测数字只在配对/对比 JSON 与
V6 报告里，此处不复写（避免两处数字漂移）。

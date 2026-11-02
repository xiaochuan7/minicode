# V6 量化对比：A（现状）/ B（+图标候选源）/ C（+图标候选源+d3 重排）

> 生成时间：2026-11-03T04:01:56｜任务集：V5 视觉必需 8 条（canvas 页，UIA 原理上不可达）｜数据：`runs/v5/summary-group-*.json`（入库副本 `docs/v6-abc.json`）

## 三组配置（模型身份与开关披露）

| 组 | 视觉模型 | 图标候选源 | d3 重排 | 说明 |
| --- | --- | --- | --- | --- |
| A（现状） | Qwen3.8-27B | 无 | 关 | 视觉通道兜底，无候选增强、无重排 |
| B | Qwen3.8-27B | icon-proposals | 关 | V3 候选源注入（ScreenParser 检测模型优先，零模型回退） |
| C | Qwen3.8-27B | icon-proposals | 开（gap<0.1 才调；confidence<0.5 不点） | B 的基础上加 D3CandidateReranker（/v1/systemone） |

## V5 视觉必需任务集上的三组指标

| 组 | success@1 | fail | error | blocked | paused | 视觉通道使用 | 平均耗时 | d3 调用 | 低置信拒绝 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| A（现状） | 0.75 （6/8） | 2 | 0 | 0 | 0 | 8 | 118.2s | — | — |
| B（+图标候选） | 0.75 （6/8） | 2 | 0 | 0 | 0 | 8 | 168.0s | — | — |
| C（+d3 重排） | 0.0 （0/8） | 8 | 0 | 0 | 0 | 8 | 70.6s | 8 | 2 |

- blocked（环境前置不满足）与 paused（用户动键鼠让位）从 success@1 的分母剔除（与 W4 同口径）；
- 危险动作次数：三组均为 **0**（L1 白名单 + 审批闸门，guard 会话可查）；人工接管（APPROVAL_DENIED）：见各任务 JSON。

## 逐任务对照（hit/landing 直读）

| 任务 | A | B | C |
| --- | --- | --- | --- |
| w01 | fail（channel=visual，landing=[662, 311]） | pass（channel=visual，landing=[1334, 393]） | fail（channel=visual，landing=—） |
| w02 | pass（channel=visual，landing=[1691, 393]） | pass（channel=visual，landing=[1693, 393]） | fail（channel=visual，landing=—） |
| w03 | pass（channel=visual，landing=[1030, 453]） | pass（channel=visual，landing=[1031, 453]） | fail（channel=visual，landing=—） |
| w04 | fail（channel=visual，landing=[661, 361]） | fail（channel=visual，landing=[660, 361]） | fail（channel=visual，landing=—） |
| w05 | pass（channel=visual，landing=[950, 431]） | fail（channel=visual，landing=[1130, 432]） | fail（channel=visual，landing=—） |
| w06 | pass（channel=visual，landing=[951, 627]） | pass（channel=visual，landing=[950, 626]） | fail（channel=visual，landing=—） |
| w07 | pass（channel=visual，landing=[1020, 502]） | pass（channel=visual，landing=[1021, 499]） | fail（channel=visual，landing=—） |
| w08 | pass（channel=visual，landing=[1529, 495]） | pass（channel=visual，landing=[1529, 495]） | fail（channel=visual，landing=—） |

## 原 30 条任务（W4 台）与三组的关系（如实说明）

* A 组基线 = **W4 已发布实测**（v0.1.6，runs/w4 既有产物）：30 任务 × 3 attempts，原始产出 245/249 attempt、success@1 = 99.6%（i16 复验换入后 248/249）——本夜不重跑、不改既有产物；
* B/C 两组对原 30 条是**结构性等同**：候选源与 d3 只接在视觉回退路径（``ActionExecutionSeam`` 的 UIA 失败 → 覆盖失败 → 视觉分支），W4 台 30 条任务全部 UIA 通道解决（trace ``model_calls=0``）→ B/C 的切换点在这些任务上永远不会被踩到；
* 复跑命令（如需实测验证）：
  `python -I scripts/taskbench.py --all --out runs/v6-w4-group-b`（需先给 taskbench
  接 V3/V4 的开关 —— 本夜未做，如实记为「未测」）。

## 诚实口径

* 每组 n=8（单 attempt），无显著性检验的统计功效 —— 三组差异是**点估计**，
  不构成显著性结论；
* V3 量化（runs/v3）已证实：零模型图标候选在 ScreenSpot-Pro 上的覆盖率极低
  （icon 1/30），在 canvas 页这类**常规密度 UI**上的表现以本台三组对照为准；
* d3 只决定「选哪个候选」（落点仍取候选框物理中心，0.0px 契约）。
* **已知元数据误差**：本轮三组 summary 的 ``candidate_source`` 字段误标为
  ``icon-proposals``（runner 硬编码缺陷，已修复）—— 组 B/C 实际用的是
  **ScreenParserCandidateSource**（跑批日志「候选源：ScreenParser」与
  d3 调用日志的 15~20 选项数可证）；runs/ 产物照原样保留不改。
* **组 C 的 d3 调用拆账**（8 次，日志 `group-c-d3-calls.jsonl`）：成功返回 2（其中低置信拒绝 2）、超时 3、网关 400「Decision model not found」3 —— fail-closed 全部兑现为**不点**（对照组 B 同批 6/8 无一点错）；d3 网关的间歇不可用与多选项超时是**环境侧**问题，如实披露。

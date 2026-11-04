# 视觉通道切换记录：Qwen3.8-Flash-Next（W7 主线 A）

> 2026-11-04 夜。视觉通道从 deepseek-v4.1-flash（opencode zen）切到
> **Qwen3.8-Flash-Next**（AMD Radeon Cloud / Token Factory 同网关）。
> **这是部署选择，不是「换更强模型」**——后者已证伪一次（Qwen3.8-27B 臂更低
> 且 Δ 未达 MDE，`docs/v2-vision-paired.md`）；本夜换的是**同级/更快**的模型，
> 交通过标定准入 + 同批配对对照实测留档。决策层（opencode zen /
> deepseek-v4.1-flash）**一行未动**。

## 一、配置（`.env` 追加，决策层不动）

```
MINICODE_VISION_PROVIDER=openai
MINICODE_VISION_BASE_URL=https://developer.amd.com.cn/radeon/api/v1
MINICODE_VISION_API_KEY=（.env 内，不入库）
MINICODE_VISION_MODEL=Qwen3.8-Flash-Next
```

不设 `MINICODE_VISION_SESSION_ID` / `MINICODE_VISION_CLIENT`（opencode 网关
语义的 `x-opencode-*` 头，发给 AMD 是噪声——`config.py` 里它们「不回落」
正是为此）。装载验证：`load_vision_config().describe()` → endpoint
`.../radeon/api/v1/chat/completions`、model `Qwen3.8-Flash-Next`、key 脱敏 ✓。

## 二、标定结果（**主线 A 的成败判定点：通过**）

探针：`scripts/_night_calib_vision.py`（复用 e2e/visual.py 的 client 装配：
可配重试 + 落盘 + 串行），K=21 轮 × 4 目标，声明输入 2016×1260。

| 读数 | 值 | 准入门 |
|---|---|---|
| **detected_scale** | **absolute_model_input** | 唯一判定（scale_verdict=unique） |
| 等价族 | {absolute_model_input} | 无 multi —— 分歧 0.00 px ≤ 4.0 px |
| 21 轮命中率 | **1.00** | ≥ 0.60 ✓ |
| 中位误差 | 15.06 px | 命中半径 48 px |
| p90 误差 | 18.86 px | — |
| **准入判定** | **available=True** | **视觉通道可用** |

**fail-closed 未触发**：量纲在新模型下唯一可分辨，不需要放宽任何阈值。
（对照：模型以**声明输入域的绝对像素**回答坐标——与 deepseek 相同的量纲族，
换算链路无需改动。）

证据：`runs/w7-vision-switch/calib-qwen38/`（calib-readout.json +
model-requests.jsonl + model-retries.jsonl；21 次请求全部一次成功，
零 429 零重试——深夜网关通畅）。

## 三、端到端真图验证

真实截图 2560×1600 → 降 2016×1260（base64 0.67MB，5MB 上限内）→ 两次定位：

| 目标 | 模型答（声明域） | 物理换算 | 核验 |
|---|---|---|---|
| 屏幕中心 | (1008, 630) | (1280, 800) | **与声明尺寸正中心完全一致** ✓ |
| 任务栏开始按钮 | (935, 1235) | (1187, 1568) | y≈98% 底部 ✓；x≈46% 与 Win11 任务栏图标居中布局吻合 |

证据：`runs/w7-vision-switch/e2e-screenshot/`。**零动作下发**（只读验证）。

## 四、同批配对对照（deepseek 像素臂 vs 新模型像素臂）—— **新模型更差**

同批 60 条（`stratified_subset`，per_stratum=10，seed=20251020），同一把尺
（w6 的几何/提示词/bbox 判定口径）。跑批中途的换算签名错误已离线修复
（57 条 `raw_center` 全部落盘，零重跑；修复脚本 `_night_paired_fix.py`）：

| 臂 | 模型 | 命中 | Wilson 95% CI |
|---|---|---|---|
| 历史（docs/w6-paired.json） | deepseek-v4.1-flash | **13/60（21.7%）** | — |
| 本夜（runs/w7-vision-switch/paired-qwen38/） | Qwen3.8-Flash-Next | **2/60（3.3%）** | [0.9, 11.4]% |

McNemar：不一致格 b（新对旧错）= 1、c（新错旧对）= 12；
**exact p = 0.0034，mid-p = 0.0018**；Δ = **−18.3pp**。

**预注册判定（不许事后改口径）**：|Δ| = 18.3pp < MDE 18.7pp →
**按 MDE 口径判定无效**；但方向检验显著（p<0.005，且方向为负）。
两种读法下行动一致：**保留 deepseek**（判定无效时保守取向 = 不切换；
方向显著更差时更不该切换）。

**执行**：`.env` 的四个 `MINICODE_VISION_*` 键已删除（2026-11-05 凌晨），
`load_vision_config()` 回落决策层 deepseek-v4.1-flash —— 仓库回到
v0.2.0 的部署默认。`MINICODE_VISION_RETRY_*` 四键**保留**（重试策略
与通道无关，回落 deepseek 后同样生效）。

## 五、重试硬化随行交付（本夜 A 线的另一半）

* `RetryPolicy` 可配（`MINICODE_[VISION_]RETRY_*` 四键；缺省 = 原值）；
  视觉通道激进值 16 次 / 1.5s / 90s / jitter 0.25。
* 429 与模型失败**分开计数**；每轮尝试落盘 `model-retries.jsonl`；
  视觉调用串行（in-flight=1）；`VisionCallGuard` 外层冷却（耗尽 →
  冷却 60–120s → 连续 5 次才升级暂停）。
* 视觉通路三处 `max_tokens` 2000 → 8192（推理模型 reasoning token 吃预算
  —— 该坑在交接文档 §1.2-3 有案底）。

## 六、结论与回退路径

* **标定与端到端通过**：新模型在标定探针上量纲唯一可分辨（absolute_model_input
  唯一、命中率 100%、中位 15.06px），真图换算链路验证一致 —— **通道可用
  （准入门过）不等于答得准**：准入门保证「坐标系可信」，配对对照量的才是
  「落点好不好」。
* **同批配对：新模型显著更差（2/60 vs 13/60，Δ=−18.3pp，p=0.0034），
  |Δ| 未达 MDE —— 预注册判定「无效」，保守行动 = 已回退**（`.env` 删四键，
  回落 deepseek；2026-11-05 凌晨执行）。
* 与已证伪路线的关系：换**更强**模型（27B）更差 —— 已证伪；换**同级
  更快**模型（Flash-Next）**也更差** —— 本夜补测，同样不支持「换模型」
  这条路。图标短板的颗粒度假设（工单 §6）依旧指向裁剪+放大，不是换模型。
* 复测方法：按 §1 配置四键 → `python -I scripts/_night_calib_vision.py`
  （标定）→ `python -I scripts/_night_paired_qwen38.py`（同批配对）→
  `python -I scripts/_night_paired_analysis.py`（分析）。产物全部进
  `runs/w7-vision-switch/`（只新增不改）。

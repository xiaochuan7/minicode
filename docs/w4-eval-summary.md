# W4 任务级评测汇总（脱敏）

生成时间：2026-11-01 23:59；生成器：`scripts/make_eval_table.py`（从 attempt 明细重算，可复现）。

## 口径

* attempt 状态五类：**pass** / **fail** / **error**（步骤或 judge 崩溃，系统在任务中失败）/ **blocked**（环境前置不满足：桌面锁、reset、起实例）/ **paused**（护栏 6 空闲让位：检测到真实用户键鼠输入，本轮未跑）。
* **success@1** = pass / (pass + fail + error)，attempt 级按组池化；**paused 与 blocked 从分母剔除**（用户让位与环境失败不是被测系统的失败）。
* **success@3** = 一条任务全部 attempts 中至少 1 次 pass；overall 行按「轮×任务」对池化。
* 单独计数：paused 次数、blocked 次数、人工接管次数（走到审批人面前的请求）、危险动作触发次数（影响面判定命中且被放行执行，**必须 0**）。
* 全部 attempt 被 paused/blocked 吃掉的任务**不计入分母**（如实单独可见）；没跑的任务写「未跑」，不估算。judge 只读状态（UIA 控件值/文件内容/窗口标题/磁盘），不读截图；判分器负向对照见文末。

代码版本注记：round1/round2 跑于 3e03f88（VK 修复后、W7 前）；round3-redo 与负向对照跑于最终 HEAD（判分三洞修复后）

**作废轮次**：runs/w4/round3：桌面被用户占用（真实键鼠输入），paused 级联，作废；另 runs/w4-redo/round1-i16fix（首次 i16 复验，判分缺陷未修，作废）

**单任务替换**：round3-redo 的 i16 换入 runs/w4-redo/round1-i16fix2/round1/i16-summary.json——redo 轮中 i16 的 fail 是判分自身缺陷（mid 行文本在 name 而非 value，判分解析错），修复判分后单任务复验 3/3 pass，换入复验数据

## 轮次覆盖

| 轮次 | 任务覆盖 | pass | fail | error | paused | blocked | 危险动作 | 人工接管 |
|---|---|---|---|---|---|---|---|---|
| round1 | 30/30 | 89 | 0 | 0 | 1 | 0 | 0 | 0 |
| round2 | 30/30 | 69 | 1 | 0 | 19 | 1 | 0 | 0 |
| round3-redo | 30/30 | 90 | 0 | 0 | 0 | 0 | 0 | 0 |

## 汇总（含类别与通道拆分）

| 组 | n(轮×任务) | 可判分 | success@1 | success@3 | 平均步数 | 平均耗时(s) | 危险动作 | 人工接管 | paused | blocked |
|---|---|---|---|---|---|---|---|---|---|---|
| **整体** | 90 | 87 | 248/249 = 0.996 | 86/87 = 0.989 | 3.54 | 3.1 | 0 | 0 | 20 | 1 |
| 文件类 | 24 | 24 | 72/72 = 1.000 | 24/24 = 1.000 | 3.25 | 2.7 | 0 | 0 | 0 | 0 |
| 计算类 | 12 | 12 | 36/36 = 1.000 | 12/12 = 1.000 | 6.25 | 2.9 | 0 | 0 | 0 | 0 |
| 浏览器类 | 9 | 9 | 27/27 = 1.000 | 9/9 = 1.000 | 1.0 | 3.8 | 0 | 0 | 0 | 0 |
| 界面类 | 21 | 21 | 61/61 = 1.000 | 21/21 = 1.000 | 4.1 | 2.4 | 0 | 0 | 2 | 0 |
| 复合类 | 12 | 11 | 26/27 = 0.963 | 10/11 = 0.909 | 4.36 | 4.8 | 0 | 0 | 9 | 0 |
| 反例类 | 12 | 10 | 26/26 = 1.000 | 10/10 = 1.000 | 1.2 | 3.6 | 0 | 0 | 9 | 1 |

注：success@1/success@3 的 `x/y = z.zzz` 中，x/y 为原始计数、z 为比率；「无可判分 attempt」表示该组全部 attempt 都是 paused/blocked。

### 通道拆分（步骤级）

| 通道 | 步数 | 步骤成功 | 步骤成功率 |
|---|---|---|---|
| keyboard | 247 | 247 | 1.000 |
| raw | 15 | 15 | 1.000 |
| uia | 416 | 385 | 0.925 |
| uia-query | 64 | 64 | 1.000 |
| wait | 139 | 139 | 1.000 |

注：本评测台的任务步骤走 UIA / 键盘 / 窗口标题通道；视觉通道不在 W4 任务里（视觉配对实验见 W6 报告）。

## 每任务明细（attempt 状态原样列出）

| 任务 | 类别 | round1 | round2 | round3-redo | s@3（池化） |
|---|---|---|---|---|---|
| f01 | 文件类 | pass,pass,pass | pass,pass,pass | pass,pass,pass | ✓ |
| f02 | 文件类 | pass,pass,pass | pass,pass,pass | pass,pass,pass | ✓ |
| f03 | 文件类 | pass,pass,pass | pass,pass,pass | pass,pass,pass | ✓ |
| f04 | 文件类 | pass,pass,pass | pass,pass,pass | pass,pass,pass | ✓ |
| f05 | 文件类 | pass,pass,pass | pass,pass,pass | pass,pass,pass | ✓ |
| f06 | 文件类 | pass,pass,pass | pass,pass,pass | pass,pass,pass | ✓ |
| f07 | 文件类 | pass,pass,pass | pass,pass,pass | pass,pass,pass | ✓ |
| f08 | 文件类 | pass,pass,pass | pass,pass,pass | pass,pass,pass | ✓ |
| c09 | 计算类 | pass,pass,pass | pass,pass,pass | pass,pass,pass | ✓ |
| c10 | 计算类 | pass,pass,pass | pass,pass,pass | pass,pass,pass | ✓ |
| c11 | 计算类 | pass,pass,pass | pass,pass,pass | pass,pass,pass | ✓ |
| c12 | 计算类 | pass,pass,pass | pass,pass,pass | pass,pass,pass | ✓ |
| b13 | 浏览器类 | pass,pass,pass | pass,pass,pass | pass,pass,pass | ✓ |
| b14 | 浏览器类 | pass,pass,pass | pass,pass,pass | pass,pass,pass | ✓ |
| b15 | 浏览器类 | pass,pass,pass | pass,pass,pass | pass,pass,pass | ✓ |
| i16 | 界面类 | pass,pass,pass | pass,pass,pass | pass,pass,pass | ✓ |
| i17 | 界面类 | pass,pass,pass | pass,pass,pass | pass,pass,pass | ✓ |
| i18 | 界面类 | pass,pass,pass | pass,pass,pass | pass,pass,pass | ✓ |
| i19 | 界面类 | pass,pass,pass | pass,pass,pass | pass,pass,pass | ✓ |
| i20 | 界面类 | pass,pass,pass | pass,pass,pass | pass,pass,pass | ✓ |
| i21 | 界面类 | pass,pass,pass | pass,pass,pass | pass,pass,pass | ✓ |
| i22 | 界面类 | pass,pass,pass | pass,paused,paused | pass,pass,pass | ✓ |
| x23 | 复合类 | pass,pass,pass | paused,paused,fail | pass,pass,pass | ✓ |
| x24 | 复合类 | pass,pass,pass | paused,pass,pass | pass,pass,pass | ✓ |
| x25 | 复合类 | pass,pass,pass | pass,paused,paused | pass,pass,pass | ✓ |
| x26 | 复合类 | pass,pass,paused | paused,paused,paused | pass,pass,pass | ✓ |
| n27 | 反例类 | pass,pass,pass | paused,paused,paused | pass,pass,pass | ✓ |
| n28 | 反例类 | pass,pass,pass | pass,paused,paused | pass,pass,pass | ✓ |
| n29 | 反例类 | pass,pass,pass | paused,paused,paused | pass,pass,pass | ✓ |
| n30 | 反例类 | pass,pass,pass | blocked,pass,paused | pass,pass,pass | ✓ |

注：状态串如 `pass,pass,paused` = 该轮 3 次 attempt 的原样状态；「未跑」= 该轮没有这条任务的产物（不估算）。

## 判分器负向对照

**判分器负向对照：3/3 通过**

对照内容：① judge 断言改必然不成立（c09 期望 392，实况 391）→ 必报 fail；② 脏状态（reset 写错内容 / reset 不跑）→ 必报 fail 或 blocked；③ 伪造错误状态（计算器打到 999，judge 期望 391）→ 必须拒绝。驱动：`scripts/_w4_negative_control.py`，产物 `runs/w4-negative/`（gitignored）。


# AGENTS.md

Working agreements for agents on this repo.

## Agent skills

### Issue tracker

Issues live in GitHub Issues for `xiaochuan7/minicode` (private), driven through the `gh` CLI — which is **not on PATH** here, call it as `& "$env:ProgramFiles\GitHub CLI\gh.exe"`. See `docs/agents/issue-tracker.md`.

### Triage labels

The five default roles, used verbatim: `needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix` — already created in the repo. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: one root `CONTEXT.md` plus `docs/adr/`. See `docs/agents/domain.md`.

**Naming a domain concept? Use the term `CONTEXT.md` defines it.** Read it before writing an issue title, a test name, or any prose that names a layer, a channel, a coordinate space, or a workflow object.

### 工单与验收标准的改动时机

**改需求 / 改验收标准的动作，只在 agent 停下之后做；agent 运行期间只做实现与记录，不动工单的验收标准。**

`docs/tickets/` 里的工单是**唯一真值来源**。agent 跑一轮票时，它读工单、按工单实现、把自己的发现写进 `docs/reviews/M<N>.md` §5（待拍板清单）—— 但**它不回头改工单的 AC**，也不自行给待拍板项拍板。

理由：agent 运行期间改 AC，等于**用实现去追认规格**，规格就不再是判据；两份真值会在同一轮里互相漂移。

- agent 发现工单字面与实测/实现不符 → **记进 §5**，不改 AC。
- agent 认为某条口径该改 → **写进 §5 的待拍板清单**，由人决定；人决定后**在 agent 停下之后**统一改 AC 并同步文档。
- 例外：**标定 / 坐标契约这类口径变更必须由人拍板**（见 `docs/tickets/README.md` 的「待拍板清单」）。

## 真机占用锁（人机互斥）

**本项目唯一的人机互斥约定。** 任何要动真机的动作（真机截图 / 真机点击 / 真机读控件树 / 真机跑端到端任务）**之前**必须先拿锁；做完必须释放。

锁文件固定在 `.scratch/desktop.lock`（`.scratch/` 已 gitignore，**锁文件永远不进 git**），内容是一行 JSON：

```json
{"owner": "agent", "ticket": "M3", "since": "2026-10-20T15:31:59+08:00"}
```

```powershell
python scripts/desktop_lock.py status                  # 看当前占用
python scripts/desktop_lock.py acquire --ticket M3     # 动真机前：拿锁
python scripts/desktop_lock.py release                 # 做完：释放
python scripts/desktop_lock.py human                   # 人类要用电脑：占锁
python scripts/desktop_lock.py release --owner human   # 人类用完：释放
```

判定规则：

| 锁文件状态 | 本轮能不能动真机 |
|---|---|
| 不存在 | **能** —— 先原子写入 `{"owner":"agent","ticket":"<本轮票号>","since":"<now>"}` |
| `owner == "human"` | **不能** —— 跳过所有真机票，改做一张纯软件票（若还有），并在报告里写明「因人类占用真机而跳过」 |
| `owner == "agent"` 且 `since` 距今 **< 30 分钟** | **能** —— 视为本进程自己在用 |
| `owner == "agent"` 且 `since` 距今 **≥ 30 分钟** | **能** —— 视为上次崩溃残留，允许抢占（覆盖写入） |

写锁一律「先写临时文件再原子改名」，避免读到半个文件。

## 仓库速览

minicode = Windows 桌面 computer-use agent。架构定稿见 `docs/research/01-架构决策备忘.md`，本机实测事实见 `docs/01-现状.md`，现状总览见 `docs/00-现状.md`。

- 坐标契约：模型端 0~1000 归一化，工程端全程物理像素；**视觉通道内**最终落点默认在 crop 上产生（**例外**：精定位失败时**回落**到整屏粗定位点，见 `docs/00-现状.md` §3），**UIA 通道的落点直接取控件框的物理像素中心**（`docs/00-现状.md` §3）。**crop 精定位默认无条件执行**；门控已于 2026-10-22 降级为**记录器 + 回落判据**（不再决定开不开）。
- **评测脚本的坐标口径**：内部一律原图像素，出口按目标脚本转一次；口径表与出处见 `docs/adr/0008-eval-script-coordinate-formats.md`（转换的唯一一处是 `src/minicode/eval/formats.py`）。
- 三个原型已验证、冻结不改：`src/minicode/coord`、`src/minicode/exec`、`src/minicode/verify`。
- 业务代码在 `src/minicode/action`（M0 新增）：动作协议 v1、动作执行缝、动作记录。
- **决策层在 `src/minicode/decision`（M10 新增）**：产出形态是**一段动作程序**（不是逐条动作），在**受控环境**里执行（`src/minicode/action/program.py`：程序只能调用动作协议暴露的动作，任意系统调用与文件写入在执行前被拒）。**协议里没有「完成」**——终止只由 harness 侧的停止条件承载。

```powershell
cd src/minicode\coord  && python -m unittest test_coordinates
cd ..\exec             && python selftest.py
cd ..\verify           && python test_01_text_and_probe.py   # 真调 API，消耗额度

# 全量单测（仓库根目录）
python -m pytest

# M0 最小闭环（真机一条命令）
$env:PYTHONPATH="src"; python -m minicode.action --target "开始" --out runs
# 同一套接线走假实现（CI 可跑，不碰真桌面）
$env:PYTHONPATH="src"; python -m minicode.action --fake --out runs

# M10 决策层 harness 的端到端评测（真模型 + 假桌面，不碰真桌面）
$env:PYTHONPATH="src"; python -m minicode.decision --goal "点开名为「开始」的控件" --capture-size 2560x1600 --out runs/decision
```

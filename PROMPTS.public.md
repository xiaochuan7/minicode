# minicode — Matt Pocock 技能包全流程提示词包（去密钥版）
> **本文件不含任何密钥**（真实值在 `.env`，已被 .gitignore 排除）。带密钥的本地版在 `PROMPTS.md`（同样 gitignore）。节点 0 已在建仓时完成（见 git 历史 `b70dc0b` / `3c49100`），当前进度见 `docs/00-现状.md`。

> 用途：在一个**全新的 DSH 会话**里，从零把 minicode 建起来。每个节点一条提示词，直接复制粘贴。
> 约定：每条提示词都在 `C:\Users\22197\Desktop\minicode` 作为工作目录下发出。
> 提示词之间**不要合并**——技能包的设计前提就是"每个工单一个干净上下文"。
>
> **v2（2026-10-20）**：模型 id 修正为 `minimax-m3.1-flash-free`（不是 `minimax-m3`，那是另一个模型）；
> 新增"已实测验证"一节，所有数字都是本机跑出来的。

---

## ⛔ 当前运行模式：本地优先（无 VPN 不推送）

> **历史（2026-10-20 上午）**：这台机器到 `github.com:443` 的路由不通，节点 0 推送时**前 9 次全部失败**。
> **当前状态（2026-10-20 中午）**：网络**时通时不通**——开了 VPN 时 `github.com` 解析到 TUN 的假 IP `198.18.0.7`，不开 VPN 解析到真实 IP `20.205.243.166`，两种情况都试过成功也试过失败。
> **⚠️ 关键：agent 无法判断当前网络状态，也无法知道你是不是刚开了 VPN。所以统一按"本地开发模式"跑：只做本地 commit，一律不尝试 push、不尝试 `gh`。**

| 动作 | 现在怎么做 |
|---|---|
| **本地 `git commit`** | ✅ **每个可验证的小块都提交**——进度在 git 里，不在上下文里。**这一步任何时候都要做** |
| **`git push`** | ⛔ **不要尝试**。当前是**本地开发模式**：本机到 `github.com:443` 的路由时通时不通（开了 VPN 走 TUN 的 198.18.0.7 假 IP，不开 VPN 走真实 IP 20.205.243.166 但也不稳），**agent 无法判断当前网络状态**，所以统一不推 |
| **`gh issue create` / `gh api`** | ⛔ **不要尝试**。同理——凡是要发到 GitHub 的东西，**一律先写本地真值副本**，等用户说"可以发了"再发 |
| **报告怎么写** | "已本地提交 <sha>，待推送" + 用 `git log --oneline origin/main..HEAD` 列出待推送的 commit |

**任务完成的判据是本地状态正确，不是远端同步。** 任何时候都不许因为"推不上去"而中断任务、卡住、或反复重试——**更不许反复重试推送**：本机网络状态对 agent 不可见，重试只是浪费时间。

**每个节点结束时的收尾动作固定为**：

1. `git status -sb` 看一眼
2. 本地 `git add -A && git commit`（commit message 用中文）
3. **不推**。用 `git log --oneline origin/main..HEAD` 列出待推送的 commit，报告里写明
4. `git log --oneline origin/main..HEAD` 列出还没推的 commit（空 = 已同步）

**`gh` 不在 PATH**：`C:\Program Files\GitHub CLI\gh.exe`（已 keyring 登录 xiaochuan7）。裸敲 `gh` 会报"无法识别"。
用全路径：`& "$env:ProgramFiles\GitHub CLI\gh.exe" ...`。

---

## 已实测验证（2026-10-20，本机真跑）

### 模型接入配方（踩过坑，照抄）

```
POST https://opencode.ai/zen/go/v1/messages      # 注意：必须是 /go，不能是 /zen
headers:
  x-api-key:  <OPENCODE_GO_API_KEY>
  anthropic-version: 2023-06-01
  x-opencode-session: dsh-union-alpha            # 缺了 → 400 MissingSessionID
  user-agent: opencode/1.0.0                     # 缺了 → Cloudflare 1010
  x-opencode-client: cli                         # 缺了 → Cloudflare 1010
  content-type: application/json
body: { "model": "minimax-m3.1-flash-free", "max_tokens": N, "messages": [...] }
```

**三个已实测的坑：**

| 坑 | 现象 | 结论 |
|---|---|---|
| 不带 `user-agent` / `x-opencode-client` | HTTP 403 `Cloudflare Error 1010`（按浏览器指纹封禁） | **必须带**这两个头 |
| 走 `https://opencode.ai/zen/v1`（非 go） | HTTP 403 `FreeTierError: free tier can only be used from within OpenCode` | free 模型**只能走 `/zen/go`** |
| `"thinking": {"type": "disabled"}` | HTTP 400 `invalid_request_error` | **思考关不掉**，只能用 `output_config.effort` 降档 |

### 实测结果

| 项目 | 结果 |
|---|---|
| 文本调用 | HTTP 200，模型回显 `minimax-m3.1-flash-free` ✅ |
| 图像 grounding（合成探针 800×600，4 个黑方块） | **0~1000 归一化**（不是 0~1），4 个目标误差 **0.2 px** ✅ |
| **真实桌面**（2560×1600，UIA 真值对照，目标「开始」按钮 67×72px） | L0 整屏粗定位 **3.4 px** → L1 原图 crop 精定位 **2.3 px**，两次都命中 ✅ |
| 坐标契约 | 模型输出 0~1000 → 乘模型输入尺寸 → 除降采样比 → 物理像素 ✅ 链路验证通过 |
| **延迟** | 默认（thinking=max）**10.6s**；**`output_config:{"effort":"low"}` → 5.9s**（思考 token 368→191，精度不变 3.6px）✅ |
| 分辨率 | 1568 长边 vs 1344 长边：延迟与精度**无明显差异**，保持 1568 |

**可复现脚本**：`C:\Users\22197\Desktop\gui-cu\proto\verify\`（4 个，标准库 + Pillow）

### 由实测得出的三条设计定论

1. **坐标契约落地了**：这个模型用 **0~1000**，所以 §1.4 的标定探针是必需的（官方文档说 0~1，cookbook 说 0~1000，**实测是 0~1000**）。
2. **两级 grounding 在真实桌面上有效**：3.4px → 2.3px。crop 必须从**原图**裁（我们就是这么做的）。
3. **延迟控制点唯一**：`output_config.effort`。动作步一律用 `low`；思考关不掉，所以别指望关。

### 后续补充（2026-10-20 晚，同一把 key 继续实测）

**① `jev-1.13-free` 不可用，不要引入。**
`/zen/go/v1/models` 的 37 个模型里**根本没有** `jev-1.13-free`（它只在 `/zen` 非 go 端点的 85 个里）。实测：走 `/zen/go` → 400 `Model is unavailable`；走 `/zen` → 403 `FreeTierError`（free 档只能从 OpenCode 内部调用）。带图调用同样 400。

**② 一把 key 就够，主线不需要第二个模型。**
`/zen/go` 的 37 个模型里，多模态可用的是 `minimax-m3.1-flash-free` 和 `qwen3.8-flash`；而 `minimax-m3`、`deepseek-v4-flash` 是**纯文本模型**（带图调用时 input_tokens 显示图片被忽略，返回的坐标明显是瞎猜：x=986 贴最右、x=16 贴最左）。
**→ 主线模型栈 = `minimax-m3.1-flash-free` 一个，兼任大脑和眼睛。** 不引入 qwen3.8（它没有压倒性优势，多一路配置只增加故障面）；百炼 `gui-plus` / `qwen3-vl-plus` 只在你想要"更便宜的眼睛"时才加（¥1.5/¥4.5 每百万）。

**③ 诊断手法：看 `input_tokens` 就能判断模型有没有真的看图。** 纯文本模型带图调用时 input_tokens 只有 ~1000~2000 且坐标明显贴边；真视觉模型坐标落在画面中部。

**④ 坐标口径陷阱（写进代码规范）。**
各家 eval 脚本口径不统一，混用会得到"接了官方脚本但分数全 0"的假象：
- ScreenSpot-Pro 官方：bbox 像素 `[x1,y1,x2,y2]`，**point 期望归一化 [0,1]**
- ZoomClick 副本：同样 bbox，但 **point 期望像素**
- CCF 仓的 ScreenSpot-v2 `src/eval.py`：**bbox 是 `[x,y,W,H]`（宽高！）不是 `[x1,y1,x2,y2]`**
- ShowUI `main/eval_screenspot.py`：**0–999 网格**
→ **内部一律用原图像素，只在 wrapper 返回前按目标脚本转一次。**

**⑤ 门控的三个可抄范式（crop 精定位不是无脑增益）。**
- **CCF** `LufeMC/gui-g2-3b-ccf` → `src/cursor_ccf.py`（333 行 + **220 个单测** + 9 阶段复盘）：门控 + 失败回退
- **UI-Zoomer** `uizoomer.py::process_single_image` L272-370：全局采 k=8 候选 → 打分 `S = 空间一致性(IoU均值) + avg_conf`，**S > 1.5 就不 zoom**，否则 Density-Gaussian crop（σ=3.5，最小 512px）
- **RegionFocus** `judge_inference` L501-577：**自判门控**——直接问 VLM "is this ... position correct?"，判 YES 就不 zoom
→ CCF 受控评测（ScreenSpot-v2, n=1272）：整体 **−0.3pp**，Icon +2.2 / Web +3.9 / Desktop +1.5，但 **Text −2.3 / Mobile −5.2**。**必须带 type-aware gate + 失败回退 + `min_pixels` 门限。**

**⑥ 有公开代码的 crop 实现只有 7 个**（全部 clone 读源码核实）：ZoomClick、GUI-Spotlight、CCF、UI-Zoomer、RegionFocus、Iterative-Narrowing(ant-8)、ScreenSpot-Pro 的 `models/methods/*`。
明确无代码：BAMI（README "coming soon"）、InnerZoom（仓库仅 LICENSE+README，"pending Alibaba approval"）、ReGround-as-paper（训练型，全仓 0 个 crop）、GUI-Lens、MHVF、AutoFocus。
12 个 agent 框架里**只有 3 个**有真 zoom 动作：trycua Cua Driver、OpenClaw（转调 trycua）、Anthropic quickstarts。

**⑦ 调研边界（诚实声明）**：Jedi / AnyClick / ConClick / Seg2Click / Groma / OS-Atlas 因网络不通**未 clone 成功**，其"无 crop/zoom"是方法知识推断而非读源码；AutoFocus / GUI-Lens / MHVF 连 arXiv 页面都没取到。Anthropic 官方文档页因跨域重定向读不到，但其 SDK 类型 docstring 是读源码得到的（权威性更高）。

### 节点 0 复跑（2026-10-20，在 minicode 仓库内重跑）

| 项目 | 结果 |
|---|---|
| `coord` 单测 | **Ran 50 tests / OK** |
| `exec` 自检 | **5 PASS / 0 FAIL / 0 SKIP**（DPI PMv2 生效、`sizeof(INPUT)=40`、截图 2560×1600、UIA 命中任务栏、点击时序达标） |
| `verify/test_01` | HTTP 200 / 文本 1.65s + 图像 10.97s / **输出最大值 900.00 → 0~1000 归一化** / 平均误差 **0.2 px** |
| `verify/test_02` 真实桌面 | 目标「开始」(67×72px)，UIA 真值中心 (1049.5, 1564.0)：**L0 整屏 7.6 px → L1 crop 0.8 px**，判定命中 |
| `verify/test_03` 延迟 | `effort=low` 比默认快（1568 宽 16.68s vs 19.31s）；`thinking disabled` → **HTTP 400** |

**本轮新增的三条事实（上一轮没测到）**：

1. **默认 effort（=max）在 `max_tokens=1200` 时，思考链会把预算吃光 → 模型根本不返回 JSON**。三个"默认"档全部解析失败，只有 `effort=low` 档正常返回坐标。
   → **动作步必须显式 `output_config:{"effort":"low"}`，且 `max_tokens` 要给思考链留够余量**，否则模型"HTTP 200 成功"了但你拿不到坐标。
2. **`reasoning_effort: "low"` 是无效参数**——被忽略，行为与默认一致（本轮 21.38s，反而最慢）。
3. **同一目标的 L0 误差在 3.4~7.6 px 之间浮动**（两轮）→ **单次数字不能当验收线**，验收必须用一组目标的分布。

- 真实**小图标**（≤16px）的定位精度（本次目标是 67×72px 的开始按钮，太大）
- **UWP/WinUI 应用**（Windows 设置无 UIA 树，纯视觉路径）上的表现
- 多个元素同名时的歧义处理
- 剪贴板/中文输入链路

---

## DSH 能力地图（2026-10-20 实测当前安装的版本）

> **这一节决定你（agent）怎么干活。** DSH 现在有团队、workflow、ralph 三种并发手段，但**它们不是默认选项**——用错比不用更慢、更容易炸。下面是当前安装版本的实际能力与限制（都是从 DSH 实现里读出来的，不是猜的）。

| 工具 | 干什么 | 什么时候才用 | 硬限制（撞到就报错） |
|---|---|---|---|
| `spawn_teammate` + 共享任务板 | 建一支**持久团队**并发干活，成员之间用 `send_message` 沟通 | 任务能切成**互不依赖**的块，且**合并成本 < 并行收益** | **只有 Team Lead 能调**；成员数有上限（`maxMembers`，默认 16，配置里可能是 8），超了抛 `Team member limit N reached`；名字不能重复；单条消息 ≤ 65536 字节 |
| `subagent` | 委派**一个**聚焦子任务，从零开始 | 单个独立调研/实现块 | 子代理并发池有上限（当前 16），超了抛 `subagent limit reached (active child limit: N)` |
| `subagent_fork` | 同上，但**继承本会话已完成的轮次** | 紧接上文、需要上下文延续时 | 同上；fork 会带上全部历史，注意上下文成本 |
| `workflow` | 写一段 JS 脚本批量编排 | 「几十个同类条目各查一遍再汇总」这种**规模化 fan-out** | 脚本里**没有 fs / 网络 / Node API**，只有 agent 能干活；**schema 只能用下面那 12 个关键字**（见下）；并发总数受子代理上限约束 |
| `ralph` | 针对**一个不可变目标**的 **fresh-agent 迭代循环**：每轮开一个全新子代理，工作区当长期记忆，只回传结构化报告 | **审核 / 验证 / 收敛**类任务 | 工具描述原文：**"Use only when the direct human explicitly asks for Ralph"**；`maxRounds` 默认 256（自己给 3~5）；状态 `complete` / `blocked` / `budget-limited`；每轮子代理**没有**上一轮上下文 |
| `create_goal` / `update_goal` | 跨自动续跑轮次的**持久目标**，同一会话继续，上下文不丢 | **长任务的驱动机制**（一张工单的实现） | `phase` ∈ `active/paused/blocked/complete`；`update_goal` 必须带 `id`+`revision`（CAS 令牌）；**`blocked` 需同一条件连续 3 轮**才允许；会话 resume/fork 后 `activation` 变 `disarmed`，需 `action: resume` 重新上膛；**不要用 subagent/workflow/ralph 去"继续 goal"** |
| `todo_write` | 步骤看板 | 多步任务 | 描述里写"恰好一个 in_progress"还是"允许多个"，取决于 `allowParallelInProgress` 配置 |
| `skill` | 加载技能正文 | 每个 Matt Pocock 节点本身就是 skill | — |
| `present` | 交付文件卡片 | 收尾 | 每次最多 4 个文件 |

### 并发纪律（4 条，违反必炸或返工）

1. **先看再派。** 派团队前先 `list_agents` 查当前占用。**达到上限就不要继续创建**——会抛 `Team member limit N reached` 或 `subagent limit reached (active child limit: N)`，整个调用直接失败。
2. **workflow 分波，不要一次开满。** 单波 ≤ 5~8 个 agent，总并发不超过当前上限；**每波跑完再启下一波**。并在脚本里对 `agent()` 的失败做兜底（`agent()` 返回 `null` 时过滤掉，别让一个失败拖垮整波）。
3. **workflow 的 schema 只能用这 12 个关键字**：`type` `oneOf` `properties` `required` `additionalProperties` `items` `enum` `const` + 注解 `description` `title` `default` `examples`。**写 `maxItems` / `minimum` / `pattern` / `format` 等会让整个脚本直接失败**（错误信息：`is not a supported keyword (subset: ...)`）——因为校验是"遇到一个不支持的关键字就整脚本挂"，不是跳过。
4. **团队不是默认选项。** 小任务、顺序性强的任务、需要一问一答的技能（`/setup-matt-pocock-skills`）→ **单干更快**。只有"能切成互不依赖的块"且"合并成本低于并行收益"才建团队。

### ralph 使用规范（审核/验证专用）

- 目标必须**不可变**、一句话、**含验收判据**（"什么算通过"要写死）。
- `maxRounds` 给 **3~5**，不要用默认 256。循环是用来"再查一遍"的，不是用来"跑很久"的。
- **每轮是全新代理，没有上一轮上下文** → 目标里必须写全：文件路径、检查清单、输出写到哪、怎么判断这轮做完了。
- 适合：**对抗式审核**（找漏洞）、**验证收敛**（反复查一个判据直到通过）。**不适合写代码**——写代码请用 `/implement` + `tdd`。
- 工具的前置条件是"人类明确要求"→ **提示词里必须写明"用户明确要求使用 ralph 循环"**，否则等于违反工具契约。

### 各节点的并发策略（**条件式，按任务性质选，不是全上**）

| 节点 | 建议 | 理由 |
|---|---|---|
| 0 脚手架 | **单干** | 复制文件 + 跑测试是顺序任务，派团队反而更慢更乱 |
| 1 `/setup-matt-pocock-skills` | **单干** | 技能本身要**一问一答**，分身没法替用户回答 |
| 2 `/to-spec` | **可派 3~4 个 agent 并行核对底稿** | 底稿有 8~10 份，可按「坐标协议 / Windows 栈 / 方案格局 / API 成本」切四块各查一遍，再由主会话合并成一份 spec |
| 2.5 `/domain-modeling` | 单干，或 1~2 个 agent 起草词汇表后主会话定稿 | 词汇必须全局一致，分头写会漂 |
| 3 `/to-tickets` | **适合派团队** | 每个候选垂直切片独立分析（交付什么 / 被谁挡 / 验收标准），合并后由用户批准 |
| 4 `/implement` | **主会话单干**；**收尾的 code-review 用 ralph 循环** | 「一个工单一个干净上下文」是技能的设计前提；但审查阶段多轮对抗很值 |
| 5 `/handoff` | 单干 | 一次性文档产出 |

---

## 机器与架构前提（每条提示词都已内嵌）

- 机器：Windows 11，物理 2560×1600，**150% DPI 缩放**（逻辑 1707×1067），RTX 3060 Laptop 6GB
- 架构方案：**B —— 拼装现成骨架 + 自建两个缺口**
  - 骨架：`microsoft/UFO`（MIT，Windows 原生，UIA + Win32/WinCOM/MCP 混合动作）
  - UIA 通道：`Windows-MCP`（MIT）或微软 `winapp CLI`（MIT，public preview）
  - 浏览器那一半：`microsoft/playwright-mcp`（综合分 9.10，全表第一）
  - 大脑 + 眼睛：**同一个 `minimax-m3.1-flash-free`**（经 `https://opencode.ai/zen/go/v1/messages`，Anthropic-messages 协议，一把 key 全够）
  - **自建缺口 1**：两级 grounding 的 crop 精定位回路（UFO / UI-TARS 都没有这层）
  - **自建缺口 2**：校验与反思层（expect 断言、无变化检测、循环熔断、三段耗时埋点）
- **坐标系契约（不可混用）**：Anthropic 的 zoom 是"放大镜"，模型仍报**整屏坐标**；**MiniMax M3.1 / Qwen3-VL 实测用 0~1000，在 crop 内则是 crop 相对坐标**，所以走 **crop 内坐标 + 原点偏移**
- **三条硬红线**（违反即验收不过）：
  1. crop 精定位**必须从原始物理分辨率截图裁**，不能从降采样后的图裁
  2. crop 精定位**必须门控**（实测无条件做整体 −0.3pp：Text −2.3 / Mobile −5.2）
  3. **UIA 树原文进模型上下文前必须脱敏**（czdcode 实测从微信草稿捞到过 API key）
- 首批目标应用（**已定，别再问用户**）：**Windows 设置**（UWP/WinUI，**无 UIA 树，只能走视觉** → 视觉兜底层的活体测试）→ **Chrome**（BOSS直聘页，扁平 DOM）→ **微信**（UIA 可用但有弹窗门控）→ **DSH / Tabbit**（Electron）→ **cc-switch**
- **Issue tracker：GitHub Issues（原生）** —— 仓库 https://github.com/xiaochuan7/minicode （private，main 分支）；`gh` CLI v2.102.0 已装并已认证（keyring，账号 xiaochuan7）；5 个 triage 标签已建好；**`/repos/.../issues/{n}/sub_issues` 端点实测 200 → 可用原生阻塞关系**
  - ⚠️ **`gh` 不在 PATH**，用全路径 `C:\Program Files\GitHub CLI\gh.exe`（或 `& "$env:ProgramFiles\GitHub CLI\gh.exe"`）
  - ⚠️ **网络可能随时断**：`gh issue create` / `git push` 前先确认可达；**本地真值副本永远先写好**（见「本地优先」一节）
- 调研底稿位置：`docs/research/`（架构备忘 + 方案排名 + 10 份调研底稿）
- 已验证原型位置：`src/minicode/coord/`（50 单测全绿）、`src/minicode/exec/`（本机自检 5 PASS / 0 FAIL）、`src/minicode/verify/`（4 个实测脚本）

---

## 节点 0 — 物理脚手架 ✅ **已完成 2026-10-20，HEAD = `e4d1830`**

> **不用再跑了。** 当时的原文已归档在 git 历史里（\70dc0b\ / c49100\）。实际产出见 `docs/00-现状.md`、`docs/01-现状.md`、`docs/research/`。
>
> **新会话直接继承这些结论，不要重新讨论、也不要重新验证**：
>
> - 原型已搬进 `src/minicode/{coord,exec,verify}`，**冻结不改**（ruff 对这三个目录整体豁免）
> - **`verify` 脚本读的是 `~/.dsh/.credentials.yaml` 的 `OPENCODE_GO_API_KEY`，不是仓库 `.env`** → 必须有张工单把模型客户端统一到读 `.env`
> - 底稿只搬了架构备忘「证据出处」表登记的那 **11 份**（`docs/research/bottom-drafts/`）；`DSH-work/research/` 下同号的其他文件属**八字择日**与 **DSH 移动端**两套无关调研，**没搬、也不要用**
> - **首批目标应用已定**（见「机器与架构前提」），**脚手架不拆分**（不 force push，已推送的历史不动）
> - `mss 10.2.0` / `ruff 0.17.1` / `pytest 9.1.1` 已装进默认 Python 3.11.5，`python selftest.py` 可直接跑

---

## 节点 1 — `/setup-matt-pocock-skills`（仓库骨架） ✅ **已完成 2026-10-20，HEAD = `55d5408`**

> **不用再跑了。** setup 已执行完，产出 4 个文件：
>
> | 文件 | 内容 |
> |---|---|
> | `AGENTS.md` | 仓库约定入口（议题跟踪器 / 分诊标签 / 领域文档三节 + 「仓库速览」段）——**选了 `AGENTS.md` 而不是 `CLAUDE.md`**（DSH 两个都读，但以 AGENTS.md 为主，且它是厂商中立标准） |
> | `docs/agents/issue-tracker.md` | GitHub 跟踪器配置 + 本机事实（`gh` 不在 PATH 要用全路径、`sub_issues` 实测 200、PRs as a request surface: no） |
> | `docs/agents/triage-labels.md` | 默认五个标签的映射表（**已存在，别重建别改名**） |
> | `docs/agents/domain.md` | 领域文档消费规则（single-context：根 `CONTEXT.md` + `docs/adr/`） |
>
> **没有建** `CONTEXT.md` 和 `docs/adr/`——技能规定这两样由 `/domain-modeling` 按需懒建，不提前占位。
>
> ⚠️ **技能模板里藏着一条本项目要用的关键机制**（`docs/agents/issue-tracker.md` 的 "Wayfinding operations" 段）：
> **GitHub 阻塞边用 `issues/<child>/dependencies/blocked_by`，参数是阻塞者的数据库 id，不是 `#number`**：
> ```
> gh api --method POST repos/xiaochuan7/minicode/issues/<子单号>/dependencies/blocked_by -F issue_id=<阻塞者的数据库id>
> # 数据库 id 的取法：gh api repos/xiaochuan7/minicode/issues/<阻塞者号> --jq .id
> ```
> **`sub_issues` 表达的是"父单-子单"（这张工单属于哪个里程碑），不是阻塞关系。两件事别混。**

**这一节点在教什么**：`setup` 技能是**一次性的、prompt-driven 的**，不是脚本。它会先探索仓库再分节问你，每节都带推荐答案让你一个词就能接受。三个决策（issue tracker / triage 标签 / domain docs）不是随便选的——它们决定了后面 `to-spec`、`to-tickets`、`triage` 这几个技能往哪儿写、读什么。**选 GitHub Issues 的代价**：要装 `gh`、要认证、离线时发不了 issue（所以本项目额外约定"本地留真值副本 + 联网后补发"）；**收益**：原生依赖边（UI 里直接看得见的阻塞）、PR 作为请求面、多个会话/多个 agent 共享同一份工单池——这正是 tracer-bullet 工单流需要的。
---

## 节点 2 — `/to-spec`（把讨论变成 spec，跳过拷问）

```
/to-spec

这是 minicode——Windows 桌面 computer-use agent。规格要覆盖**整个产品**，不只第一个里程碑。

不要访谈我，直接综合下面的材料产出 spec：
- AGENTS.md 与 docs/agents/（节点 1 的产物：仓库约定的入口，别忽略）
- docs/00-现状.md（现状总览：机器参数、方案 B 组成、坐标契约、三条红线）
- docs/01-现状.md（**本机实测**的接入配方与实测数字、还没测的四项边界）
- docs/research/01-架构决策备忘.md（完整的技术方案、坐标协议、动作协议、检查项 C-01~C-16）
- docs/research/03-选型决策与推荐.md（方案 B 的组成、三个候选方案、为什么不选另外两个）
- docs/research/02-方案全景与双打分排名.md（59 个方案的双打分，用来解释选型理由）
- docs/research/bottom-drafts/ 下的 11 份底稿（01-czdcode-review / 02-coordinate-systems / 03-gui-agent-landscape / 04-windows-stack / 05-api-cost / 06-action-protocol / 07-继承清单 / 08-文档复核 / 09-zoom开源实现调研 / 10-anthropic-zoom源码细节 / dom-vs-pixel-2026）
- src/minicode/coord/、src/minicode/exec/、src/minicode/verify/（已通过测试的原型与实测脚本）

**产出的形状必须照抄技能的 spec 模板，小节名与标题级别一字不改**（将来发布成 GitHub Issue 时要能直接贴）：
`## Problem Statement` / `## Solution` / `## User Stories` / `## Implementation Decisions` / `## Testing Decisions` / `## Out of Scope` / `## Further Notes`。
- **User Stories 必须是编号列表，每条写成 `1. As an <actor>, I want a <feature>, so that <benefit>`**，而且要**极其详尽**，覆盖全部方面，不止第一个里程碑。
- **不要写具体文件路径和代码片段**（技能明写：它们很快会过期）。例外：原型产出的、编码了一个决定而非一段演示的片段（状态机、reducer、schema、类型形状）可以内联，并注明来自哪个原型。
- 词汇用 `docs/00-现状.md` 的口径（`CONTEXT.md` 要到节点 2.5 才懒建，此刻还不存在）。

**引证纪律（重要，硬要求）。**
- 凡是要引到底稿或外部来源的数字，**必须给一个能在仓库里打开核对的行号**。核对不上的数字**不许出现在 spec 里**。
- 核对不上时正确做法是：写明"未核"，并给出**可核的替代证据**；不是把原数字悄悄删掉就算完。
- **已知的一处坏引证，spec 必须改**：架构备忘 §39 与 选型推荐 L161 都引了「动作类型正确率 85.9% / grounding 正确率 52.4%」，标注【底稿 03 L317/L361】——但**底稿 03 的 L317/L361 根本没有这组数**（L317 讲 ScreenSpot-Pro 精度衰减，L361 讲 SoM 下 OSWorld-G <30），全库只有这两个衍生文档在引它，**没有任何一手出处**。
  → **改成引真正支撑这个决策的证据**：底稿 03 的 L317（ScreenSpot-Pro 分数从 v2 的 ~90% 掉到 40–60%）、L320（像素路线的 grounding 错误是**静默的**）、L361–L362（所有模型 SoM 下 OSWorld-G <30；E2E 跨基准方差巨大，Claude Sonnet 4.5 OSWorld-G 69.0 → ScreenSpot-Pro 35.0），**加上我们自己的实测**（真实桌面 L0 整屏粗定位 7.6px → L1 crop 精定位 0.8px，模型就是 M3.1 Flash）。这比"官方榜天花板"更直接，因为它量的是我们要用的那个模型。
  → **决策本身不变**：「不让 M3.1 Flash 兼任 grounding，大脑用 M3.1、定位用 UIA + crop 精定位」仍然成立，只是引证换成能核对的。

**发布方式：双写（重要）。**
1. 先把 spec 正文写到仓库内 **`docs/spec.md`**——这是**本地真值来源**，要提交。
2. **发布**：**当前是本地开发模式，不要执行 `gh issue create`**。spec 正文写到 `docs/spec.md` 顶部留一行 `对应 Issue 号：<待回填>`，等用户说"可以发了"再发。**本地 `docs/spec.md` 永远是真值。**
3. 报告里写清"spec 已写入 docs/spec.md，待网络恢复后发布为 Issue"。

**工作方式：可以用团队并行核对底稿，但只在下面这个切法下用。** 底稿有 11 份，可按四块切开各派一个 agent 核对：**①坐标协议与 crop 精定位（底稿 02 + 09 + 10 + dom-vs-pixel-2026）②Windows 栈 / DPI / UIA（底稿 04）③方案格局与选型（底稿 03 + 01）④API 成本与模型接入（底稿 05）**。每个 agent 回报"这一块里哪些结论能进 spec、哪些不能进（附底稿行号）"，**由主会话合并成一份 spec**。
- 派之前先 `list_agents` 查占用，**成员数不要超过当前上限**（`maxMembers`）。
- 派 3~4 个就够，**不要为了"并行"而并行**——如果四块里有一块内容很少，就只派三个。
- **不要开 ralph**：写 spec 是综合判断，不是反复验证。
- **如果这一轮明显做不完**（底稿多、要核对的地方多），先 `create_goal` 把目标设成「产出符合本规范的 spec 并写入 docs/spec.md」，让会话跨自动续跑轮次接着走，别停在半路。（`update_goal` 的 `blocked` 需要同一条件连续 3 轮才允许，"还没干完"不算。）
- 如果你判断这次综合根本不需要并行（底稿已经读过），**就单干**，别为了用工具而用工具。

写 spec 前，按技能流程的第 2 步先向我确认**测试缝（seams）**。我的答案已经定了，按这个写进 spec 的 Testing Decisions，不要再来问：

**① 唯一的主缝 = 动作执行缝。**
- 形态：输入 = 目标描述 + 一帧截图 + UIA 快照；输出 = 动作 + 最终落点 + 意图。
- 缝下全部假实现：假截图源、假 UIA 树、假 SendInput、假模型响应。**整套逻辑不需要真桌面、不需要 API key 就能测。**
- **模型规划（L3 决策层）不在缝内。** 理由：把它放进来就必须把模型也假造一遍，那是在测我的假模型而不是测系统，还要额外维护一套假循环/假重试。
- **代价我认**：L3 决策层没有单测覆盖。分工是——**单测负责感知→定位→执行→校验各层（L0/L1/L2/L4）的确定性行为，端到端评测（ScreenSpot-Pro 同表跑 + 真实任务回放）负责全链路 L0–L5**。这两层分工必须写进 Testing Decisions。
- `src/minicode/coord/` 现有的 50 个单测**保留**，它们测的是纯函数换算、不引入假实现，**不算新增接缝**，是更低层的补充而不是第二道缝。

**② 缝的返回值要带出「定位证据链」，但走旁路，不塞进主返回类型。**
- 主返回值保持窄：**动作 + 最终落点 + 意图**。这样以后换定位后端（UIA → 纯视觉）时上层调用方不用改。
- 定位过程产生一条 `GroundingTrace`，通过**可注入的 trace sink 旁路输出**，内容含：UIA 候选集、选中项、标定标度、粗定位点、crop 计划、最终落点、三段耗时、三类失败类型。
- 为什么要带：架构备忘 §2.5 第 3 条硬规则要求"三段耗时 + 三类失败拆开埋点"，每步日志必须同时记定位/决策/执行三项——没有证据链这条硬规则根本落不了地；C-15（UIA 脱敏）要能被测就需要一个可断言的面（否则只能靠"链路没崩"间接推断，是弱测试）；C-16（crop 门控）要能被测就需要门控为什么触发/不触发在 trace 里可见。
- **trace 是观测数据，不是控制流数据**——它不参与调用方的决策路径，只被 L4 校验层和 L5 审计日志消费。
- **断言只针对契约字段，不针对内部算法。** 可以断言："最终落点必须落在选中的 UIA 候选框内"、"标定不唯一时 trace 必须标记视觉通道不可用"、"门控未触发时 trace 里不能有 crop 记录"、"trace 里不得出现 password 字段的值"。**不许断言**："必须用 LANCZOS 缩放"、"必须先取整数倍裁剪"——那是实现细节。

spec 内容上务必覆盖：
- Problem / Solution 写**用户视角**的痛点：Windows 桌面 GUI 操作没有统一接口，坐标会漂移，而现有方案要么闭源要么接不上
- User stories 要**长且全**，覆盖：坐标协议、UIA 通道、视觉兜底、crop 精定位、校验层、安全审批、动作日志、MCP 暴露、跨应用流程、失败恢复
- Implementation Decisions 里必须包含：
  · 坐标系契约（crop 内坐标 + 原点偏移）与 Anthropic 契约（整屏坐标）的差异
  · 标定探针必须是 2560×1600、判定容差 1px（因为 0~999 与 0~1000 的物理分歧 = 物理x/999）
  · crop 必须从原始物理分辨率图裁
  · crop 精定位必须门控（引 CCF 实测 −0.3pp）
  · UIA 树脱敏必须在所有通道之前
  · 模型接入的三个坑（Cloudflare 1010 / FreeTierError / thinking 关不掉）+ **第 4 条：默认 effort 会吃光 max_tokens 导致无 JSON 输出，动作步必须显式 effort=low**
  · **模型客户端统一读仓库 `.env`**（原型现在读的是 `~/.dsh/.credentials.yaml`，要改）
  · 延迟预算：每步 UIA 命中 0 成本；视觉兜底单次约 5.9~16.7s（effort=low，图片越大越慢）
  · 三条硬红线
- Testing Decisions 写明"只测外部行为"的标准、哪些模块被测、已有先例（coord 的 50 个 unittest、exec 的 selftest、verify 的 4 个实测脚本就是要复用的先例）；**并写清上面定的分工：单测打感知→校验各层（L0/L1/L2/L4）、端到端评测打全链路 L0–L5、trace 走旁路 sink、断言只碰契约字段**
- Out of Scope 明确写：UAC Secure Desktop 控制、全盘录制回放、自研 grounding 模型、跨平台（仅 Windows）、多显示器热插拔

写完后：**本地 `git add -A && git commit`（中文 message）**，**不要 push**（当前是本地开发模式，推送由用户统一安排）。汇报时用 `git log --oneline origin/main..HEAD` 列出待推送的 commit。汇报后停下。
```

**这一节点在教什么**：`to-spec` 的 description 明确写着 **"no interview, just synthesis of what you've already discussed"**——它就是为你这种"已经讨论透了、只想沉淀成文档"的场景设计的。所以提示词的重点不是"告诉我你要做什么"，而是**把材料的地图指给它，并规定产出里必须包含哪些决策**。注意它会先问你测试缝在哪——**这个问题的答案是整个项目质量的决定点**：缝定高了，测试就测实现细节；缝定低了，Windows 依赖让 CI 跑不起来。技能同时规定 spec 里**不许写文件路径和代码片段**（会过期），这跟"spec 是要活过很多轮上下文的长期文档"是同一个道理。
---

## 节点 2.5 — `/domain-modeling`（可选，但强烈建议：把词汇钉死）

```
/domain-modeling

minicode 的领域词汇现在还是散的，spec 里已经出现了一批含义可能漂移的词。请帮我把 CONTEXT.md 建成这个项目的词汇表，并记录需要 ADR 的不可逆决策。

**先读 `docs/spec.md`（已发布为 GitHub Issue #2）——它是你这次工作的唯一输入源。**
spec 有 87 条 user stories 和一整节 Implementation Decisions，**我下面列的术语只是重点，不是清单**：
你要以 spec 全文实际用到的词为准建词汇表，把 spec 里出现但含义还可能漂移的词**全部**收进来，不要只处理我点名的这几个。
（spec 已发布，任何改动都要同步回 `docs/spec.md`；若某个词在 spec 里的用法本身就有歧义，写进 CONTEXT.md 的同时在 spec 对应处标注。）

**先读技能自带的两个格式文件**（路径以 `dsh-mattpocock-skills/skills/domain-modeling/` 为基准）：
- `CONTEXT-FORMAT.md` —— CONTEXT.md 的条目格式
- `ADR-FORMAT.md` —— ADR 的格式
照它们的格式写，不要自创。

**CONTEXT.md 必须是纯词汇表，一行实现细节都不许有**——它不是 spec、不是草稿本、不是实现决策的仓库。每个词条只说"这个词在本项目里指什么"，不写文件路径、不写函数名、不写怎么实现。

请重点处理这些术语的歧义（这些词在 spec 里被反复用到，含义必须唯一）：
- **坐标系**：物理像素 / 截图像素 / 模型输入像素 / 归一化(0~1000) / crop 内 / 整屏 —— 六个空间各叫什么，转换关系怎么表述
- **定位（grounding）**：模型输出坐标 vs 系统换算坐标 vs UIA 控件框，三者怎么区分
- **通道（channel）**：UIA 通道 / 视觉通道 / 浏览器通道 / 混合动作通道
- **裁剪（crop）**：什么算 crop、什么算 zoom、"精定位"和"放大"差在哪
- **兜底（fallback）**：UIA→视觉、视觉→UIA、crop→粗定位，三种回退方向各叫什么
- **缝（seam）**：本项目里唯一的那个测试缝正式叫什么；**主缝 = 动作执行缝**（输入目标描述+截图+UIA 快照，输出动作+落点+意图），**L3 决策层**不在缝内
- **定位证据链（grounding trace）**：定位过程产生的观测数据，含 UIA 候选集/选中项/标定标度/粗定位点/crop 计划/最终落点/三段耗时/三类失败；**走可注入的 trace sink 旁路输出，不进主返回类型**——它是观测数据不是控制流数据
- **动作（action）**：click / type / scroll / drag / inspect / execute_code 各自的边界
- **思考预算（effort）**：本项目里 effort 各档用在哪（动作步 low / 规划步 high）
- **真值来源（source of truth）**：本地工单文件 `docs/tickets/` 与 GitHub Issue 谁是权威、坐标换算链路的唯一真值在哪
- **节点 1 引入的这批词也一并钉死**（它们现在同时出现在 `AGENTS.md`、`docs/agents/*.md` 和后面每一张工单里，不钉住必然漂移）：
  · **议题跟踪器（issue tracker）** / **跟踪器配置**（= `docs/agents/issue-tracker.md`）
  · **分诊标签（triage label）** / **就绪（ready-for-agent）** 到底意味着什么
  · **领域文档（domain docs）** / **单上下文（single-context）** / **ADR**
  · **工单（ticket）** / **阻塞边（blocking edge）** / **就绪边（frontier）** / **曳光弹（tracer bullet）**
  · **父单（parent）** 与 **阻塞者（blocker）** 的区别——**这是本项目最容易混的一对**，GitHub 上是两个不同的原生关系
  · **本地真值副本**（离线期工单落地的那个文件）与 **GitHub 镜像** 的区别

以及这些不可逆决策。**每一条先按技能的三条件筛一遍再决定要不要写成 ADR**：①难逆转 ②没有上下文时反直觉 ③真实权衡的结果。**三条全中才写 ADR，不满足就别写。**
- 语言选 Python 而非 TypeScript（理由：UFO² 是 Python、UIA 绑定只有 Python 成熟、三个原型已在 Python）——**三条全中，写**
- **issue tracker 选 GitHub Issues，不是 local markdown**——**三条全中，写**
- **工单真值来源选本地 `docs/tickets/` 文件，GitHub Issue 作为联网后的镜像**——**三条全中，写**
- 坐标系契约选"crop 内坐标 + 偏移"而非 Anthropic 的"整屏坐标"（理由：实测 MiniMax M3.1 用 0~1000 且未被训练过 Anthropic 契约）——**三条全中，写**
- 决策层用"代码执行 harness"而非"逐条吐 action"（理由只许用 README 可核的两组数：换 harness **+19.6 分 / 步数 −53%**，代码执行 vs 同等 GUI+CLI **+6.1 分 / 步数 −14.8%**）——**三条全中，写**。**⚠️ 不许引"+15.5~+33.6 / −55~63%"那组区间——它是架构备忘 C-13 检查项的禁止引用档（不在 OpenGCUA README 正文，要引必须先回查 arXiv 2610.22200 消融表并标出表号）**
- 架构主线选"结构优先、视觉兜底"而非"视觉优先"——**三条全中，写**

ADR 文件名按 `ADR-FORMAT.md` 的约定（`docs/adr/0001-*.md` 这样的编号序列）。

写 CONTEXT.md 时用 `/writing-for-agents` 的规范（这是给 agent 读的文档，不是给人读的）。
```

**这一节点在教什么**：这是技能包里**"跑在底下"的词汇层**，不产出工单，只产出词汇。为什么要单独做？因为坐标这个词在项目里至少有六个含义，**如果 agent 在写第 30 张工单时把"物理像素"和"截图像素"混了，bug 会以最贵的方式暴露**。CONTEXT.md 是"防止术语漂移"的第一道闸门。技能自己定的规矩是**只写词汇、不写实现**，并且 **ADR 要克制**——难逆转 + 反直觉 + 真权衡，三条全中才配得上一个 ADR，否则它只是一条笔记。
---

## 节点 3 — `/to-tickets`（拆 tracer-bullet 工单）

```
/to-tickets

把 **`docs/spec.md` 里的 spec**（已发布为 GitHub Issue #2）拆成 tracer-bullet 工单。

**你要读的输入（自己读，不用我贴；文件都在仓库里）：**
- `docs/spec.md` —— 要拆的那份 spec（87 条 user stories + 七节决策）
- `CONTEXT.md` —— 105 个词条的领域词汇表，**术语一律按它来**
- `docs/adr/0001`~`0007` —— 7 条已接受的不可逆决策，拆工单时不能违背
- `docs/00-现状.md`、`docs/01-现状.md` —— 机器参数、方案 B 组成、本机实测数字
- `docs/research/01-架构决策备忘.md` —— §2.6 检查项表 C-01~C-16 是验收标准的来源
- `AGENTS.md`、`docs/agents/` —— 仓库约定（议题跟踪器、分诊标签、领域文档怎么用）

**发布方式：本地双写 + 联网后补发（当前无网络）。**
1. **本地真值来源**：每张工单写成仓库内一个文件 **`docs/tickets/M<N>-<slug>.md`**（tracked，要提交）。正文照抄技能的 issue 模板，字段一字不改：
   ```
   ## What to build
   ## Acceptance criteria
   ## Blocked by
   ```
   文件顶部再加一行元信息：`M<N> | <标题> | Blocked by: M<a>, M<b> | Status: ready-for-agent`。
   **编号从 M0 开始，按就绪边顺序**（没有阻塞的先编号）。
2. **GitHub 是镜像，不是真值**：等我说"可以发 issue 了"，再按 `docs/agents/issue-tracker.md` 的流程发 issue（**`gh` 用全路径** `& "$env:ProgramFiles\GitHub CLI\gh.exe"`），标题前缀 `M<N> `，打 `ready-for-agent` 标签。
   **阻塞边用 GitHub 原生依赖关系，命令是这个**（注意参数是**阻塞者的数据库 id**，不是 `#number`、不是 `node_id`）：
   ```
   gh api --method POST repos/xiaochuan7/minicode/issues/<子单号>/dependencies/blocked_by -F issue_id=<阻塞者的数据库id>
   # 数据库 id 的取法：gh api repos/xiaochuan7/minicode/issues/<阻塞者号> --jq .id
   ```
   **`sub_issues` 端点表达的是"父单-子单"（这张工单属于哪个里程碑），不是阻塞关系**，两件事别混；要表达父子关系时才用 sub_issues。
3. **发 Issue 的时机**：**当前是本地开发模式，不要执行任何 `gh` 命令**。只写本地文件，Issue 号先留 `<待回填>`，等用户说"可以发了"再补发。**本地文件是唯一真值，GitHub 是镜像。**

4. **先补一份缺的东西：`docs/target-tasks.md`。**
   spec 里「首批目标应用」只定了**应用**，没定**任务**——每一步的期望结果没有，端到端用例就写不出来，**M3 的验收标准会落空**。
   请写这个文件：每个目标应用 2~3 个具体任务，每个任务写清「做什么」+「**可判定的**期望结果」。
   例：Windows 设置 → 打开「系统 → 显示」并把缩放改成 125%，期望：150% 下任务栏落点不漂移、UIA 树为空时走视觉通道且落点误差 ≤2px。
   范围就用这 5 个应用：Windows 设置 → Chrome（BOSS直聘页）→ 微信 → DSH / Tabbit（Electron）→ cc-switch。
   **这是给 M3 用的输入，不是工单**——不要在这里拆工单。工单在第 4 步之后才拆。

关键约束（务必遵守）：
1. **每张工单必须是可独立验证的垂直切片**——不是"实现 L1 层"这种水平切片。理想是一张工单就能 demo。
2. **每张工单要装得下一个全新的上下文窗口**。一张工单 ≈ 一次新会话。
3. **按"就绪边"（frontier）顺序编号**：没有阻塞的先编号。
4. **工单正文里不要写具体文件路径和代码片段**（技能明写：会很快过期）。要写清"交付什么行为"和"怎么验收"，不是"改哪个文件"。
5. **坐标契约的红线要落成可执行验收标准**，不能只写一句"注意精度"。可用的验收项（来自 docs/research/01-架构决策备忘.md 的 §2.6 检查项表）：
   - C-03：标定探针 2560×1600、容差 1px、含最右边缘目标点；标定不唯一则**视觉通道不可用**
   - C-06：crop 必须从原始物理分辨率截图裁
   - C-16：crop 精定位必须门控；在 ScreenSpot-Pro 上与 5 个基线同表跑，整体分不得低于无条件基线
   - C-15：UIA 树脱敏器单测全绿，未上线则禁止启用任何把 UIA 树送模型的通道
6. **坐标口径统一（写进代码规范）**：各家 eval 脚本口径不统一，混用会出现"接了官方脚本但分数全 0"的假象——ScreenSpot-Pro 官方 point 期望归一化 [0,1]、ZoomClick 副本期望像素、CCF 仓的 ScreenSpot-v2 脚本 bbox 是 `[x,y,W,H]`（宽高！）不是 `[x1,y1,x2,y2]`、ShowUI 用 0–999 网格。**内部一律用原图像素，只在 wrapper 返回前按目标脚本转一次。**
7. **门控要有可抄的参照**：CCF `LufeMC/gui-g2-3b-ccf` 的 `src/cursor_ccf.py`（220 单测）、UI-Zoomer 的 `S = IoU均值 + avg_conf > 1.5 就不 zoom`、RegionFocus 的"问模型 is this position correct? 判 YES 就不 zoom"。三选一或融合，但**必须有失败回退路径**（CCF 实测无条件做整体 −0.3pp：Text −2.3 / Mobile −5.2）。
8. **里程碑顺序：先做主线，治理类后置。**
   - M0 截图 → SendInput → 点中一个已知按钮，误差 <=2px
   - M1 定位层：UIA 通道 + 两级 grounding + 标定探针
   - M2 决策层：代码执行 harness + expect 校验 + 反思
   - M3 泛化：4 个目标应用跑通多步任务
   - **UIA 脱敏器放在 M1 一起做**（不是后置）：czdcode 实测从微信输入框的 UIA 树里捞到过 API key，而本架构明确要把 UIA 树送进模型上下文；成本只是一个正则 + 一个单测 + 密码框 Value 替换，不做等于拿已知会发生的事故去赌
   - **模型客户端统一读仓库 `.env`** 放进 M0/M1（原型现在读 `~/.dsh/.credentials.yaml`，仓库 `.env` 目前是摆设）
   - **后置**：危险动作审批、动作日志回放、MCP 对外暴露、隐私策略
9. **模型客户端工单必须包含那四个坑的回归测试**：① 不带 user-agent + x-opencode-client → 期望 Cloudflare 1010；② 走 /zen → 期望 FreeTierError；③ thinking disabled → 期望 400；④ **默认 effort → 期望"无 JSON 输出"**（思考链吃光 max_tokens）。这四个都要有测试，否则换网关就会静默失败。
10. **首批目标应用按这个顺序推进（已定，别再问）**：**Windows 设置**（UWP，完全无 UIA 树 → 强制走视觉，是视觉兜底层的活体测试）→ **Chrome**（BOSS直聘页，扁平 DOM）→ **微信**（UIA 可用但有弹窗门控）→ **DSH / Tabbit**（Electron）→ **cc-switch**。
11. **不要用宽重构**去切分；如果某步只能横向铺开（UIA 封装层、动作协议层），按 expand–contract 排成三张工单。
12. **M0 的原型已经存在**：`src/minicode/exec/` 已经实现这一层的绝大部分（本机 5 PASS / 0 FAIL），工单是"把它组装成可运行的最小闭环并补上缺失的接线"，不是"从零实现"。

**工作方式：这一节点适合派团队。** 每个候选垂直切片可以派一个 agent 独立分析三件事——**交付什么（用户视角的端到端行为）/ 被谁挡（阻塞边）/ 验收标准怎么写才可执行**。分析完由主会话合并成一份清单给我批准。
- 派之前先 `list_agents` 查占用；**成员数不要超过当前上限**（`maxMembers`，默认 16）。
- 切片的数量就是团队规模，**不要超过切片数**（8~12 张工单就派 8~12 个；更多就分波）。
- 合并时要**统一坐标口径与红线**（第 6、7 条），不能让各 agent 各写一套。
- **不要开 ralph**：工单拆分是一次性综合判断，不是收敛问题。
- **如果这一轮明显做不完**，先 `create_goal` 把目标设成「产出工单清单并等用户批准后写入 docs/tickets/」，让会话跨自动续跑轮次接着走。

按技能流程第 4 步，先把工单清单（编号 / 标题 / 阻塞边 / 交付什么）列给我，问粒度对不对、阻塞边对不对，我批准后再写入 `docs/tickets/`。
写完：**本地 `git add -A && git commit`（中文 message）**，**不要 push**（当前是本地开发模式，推送由用户统一安排）。汇报时用 `git log --oneline origin/main..HEAD` 列出待推送的 commit。汇报后停下。
```

**这一节点在教什么**：`to-tickets` 的核心是 **tracer bullet（曳光弹）**——每张工单切一条窄但完整的路径，单独就能演示。它是全流程里**最值钱的一次人工干预**：第 4 步一定要你批准清单才发布，因为切错粒度后面每张工单都受影响。工单正文照抄技能的 issue 模板（What to build / Acceptance criteria / Blocked by），且**不许写文件路径**——工单要活过好几轮上下文，写死路径就会过期。本项目额外踩过一个坑：**阻塞边用 `dependencies/blocked_by`（参数是数据库 id），父子关系才用 `sub_issues`**，混了就会挂错边。
---

## 节点 4 — `/implement`（每张工单一个全新会话）

```
/implement

工单：**M<NN>** —— 正文在本地文件 `docs/tickets/M<NN>-<slug>.md`（这是真值来源）；若已联网发布过，同一份内容也是 GitHub Issue（**本轮不要去联网取**）。

## ⛔ 开工第一件事：建立目标模式（goal）

**用户明确要求这张工单以「目标模式」执行**——实现过程可能跨多个自动续跑轮次，不设目标就可能在半路停掉。

开工第一件事（在做任何别的事之前）：

```
create_goal(
  objective = "完成工单 <NN> 的全部验收标准。具体：<逐条抄工单的 Acceptance criteria>。"
              "约束：只改 <允许的文件范围>；坐标换算链路只能有一个真值来源；"
              "遇到与工单假设冲突的现实必须停下汇报，不得自行改需求。",
  max_goal_rounds = 40
)
```

- `max_goal_rounds` 给 **40**：给上限是为了防止无限续跑烧钱；一张工单正常 5~15 轮够用。
- 目标里**必须抄全验收标准**——目标是跨轮次的唯一权威，不能只写"完成工单 03"。
- 建完目标后**先 `get_goal()` 确认**它已 armed，再开始干活。

### goal / ralph / subagent / workflow 是四件不同的事，不要混用

| 机制 | 干什么 | 用在哪 |
|---|---|---|
| **goal** | 同一会话**跨自动续跑轮次**继续，上下文不丢 | **驱动这张工单的实现**（就是本次的目标模式） |
| **ralph** | 每轮**换一个新代理**，只做审核/验证 | 收尾的 code-review 循环 |
| **subagent** / `workflow` | 派出去的**可切分子块** | 确实能切开的独立调研/实现块 |

**不要用 subagent、workflow 或 ralph 去"继续 goal"**——那是续跑机制自己的职责，绕开它会丢掉连续上下文。

### 每轮续跑前后怎么做

1. **开工/续跑先 `get_goal()`**：读出 `id`、`revision`（CAS 令牌）、`phase`、`roundsStarted`、`activation`。
2. **如果 `activation == "disarmed"`**：会话 resume 或 fork 之后目标会解除上膛。用户说"继续/接着做"时，**用 `update_goal` action `resume` 重新上膛**，否则不会自动续跑。
3. **所有 `update_goal` 调用都必须带上 `goal_id` 和 `revision`**（`revision` 用 `get_goal()` 读到的最新值，不要猜）。revision 不匹配会被拒。
4. 动作语义（**别记错**）：

| action | 什么时候用 | 限制 |
|---|---|---|
| `edit` | 目标真的变了（改 objective） | 只在人类明确要求时改 |
| `pause` | 人类要求暂停 | **需要人类直接要求**，不能自己决定 |
| `resume` | 人类要求继续，且 `activation` 是 `disarmed` | **需要人类直接要求** |
| `complete` | 验收标准**真的全部达成** | 自动续跑中也允许 |
| `blocked` | 真的卡死 | **同一个阻塞条件连续 3 轮**才允许，否则被拒；必须在 `blocked_reason` 里写 `{code, message}` 说清具体条件 |

5. **`blocked` 的门槛**：`blockedAfterConsecutiveRounds` 默认 **3**。**"难""不确定""还有活没干完""需要更多信息"都不算 blocked**——那是 `complete` 之前该继续干的事。真的卡了（缺一个必须由人提供的凭据、依赖的外部服务挂了且短期修不了）就如实标 `blocked` 并把条件写清楚。

### 中断自保（重要）

- **每完成一个可验证的小块就 commit**。这样即使会话被打断，进度在 git 里，**不在上下文里**——上下文会丢，git 不会。
- 目标里写全工单号与工单路径，续跑时以它为准。
- `max_goal_rounds` 走完会停；停之前主动汇报：已完成到哪、测试状态、下一步是什么、要不要续。

背景（这个新会话没有之前的所有上下文，请先读）：
- **AGENTS.md 与 docs/agents/**（仓库约定：议题跟踪器、分诊标签、领域文档怎么用）——**先读这个**
- docs/00-现状.md 与 docs/01-现状.md（机器参数、接入配方与三个坑、方案 B、三条红线、首批目标应用、已实测数字）
- docs/research/01-架构决策备忘.md（尤其 §1 坐标协议、§2 分层架构、§3 动作协议、§2.6 检查项表）
- **本工单正文 `docs/tickets/M<NN>-<slug>.md`（真值来源，先读这个）**
- 该工单引用的上游工单（Blocked by 里的那些），以及它们已经在仓库里留下的代码

要求：
1. 严格按工单的验收标准做，**验收标准里凡提到编号（C-03 / C-06 / C-15 / C-16 等）的，必须真去查 docs/research/01-架构决策备忘.md §2.6 那一行确认判据**。
2. 按技能要求驱动 `tdd`：先写会失败的测试，再让它通过。**只测外部行为，不测实现细节。**
3. 频繁跑类型检查、频繁跑单个测试文件，最后跑全量测试套件。
4. 完工后做审查，**两件事、按这个顺序**：
   a. **先按技能跑 `code-review`**（它自己会并行发 Standards + Spec 两个 subagent）。⚠️ **但 Spec 轴的工单来源必须指向本地 `docs/tickets/M<NN>-<slug>.md`，不许走 `gh issue view`**——当前无网络，联网取工单必挂。技能的 spec 查找顺序是"commit message 里的 `#123` → 路径 → `docs/` 下的 spec 文件"，所以：
      - **commit message 里不要写 `#123` 这类 GitHub issue 号**，工单号一律写 `M<NN>`
      - 调用 code-review 时把 fixed point 与 spec 路径**显式传给它**（fixed point = 本轮开始前的 commit，spec 路径 = `docs/tickets/M<NN>-<slug>.md`）
   b. **再开 ralph 循环做一次对抗式复核**（用户明确要求使用 ralph 循环做审核验证）：目标写成一句不可变的话，例如「对本次改动做两轴对抗式审查，产出 `docs/reviews/<工单号>.md`，必须覆盖 C-03/C-06/C-15/C-16 四条红线、坐标换算链路是否单一真值源、UIA 树是否在脱敏后才进模型上下文、标定失败时视觉通道是否真的被禁用、密钥是否只从仓库 `.env` 读；每条结论必须给出文件与行号，找不到问题也要写明查过哪些」。
      - `maxRounds` 给 **3**，不要用默认 256。
      - 目标里必须写全：审哪个分支、改动范围、输出写到哪、什么算通过——**每轮是全新代理，没有你的上下文**。
      - **不许联网**：不要用 `gh`，工单只从 `docs/tickets/` 读。
      - **审查用的 ralph 只看 diff 与文件，不许改代码**；发现问题写进报告，由你（主会话）决定要不要改。
      - **顺序不能反**：先 `update_goal` action `complete`（目标达成），**再**开 ralph 做审查。**ralph 不是用来完成目标的。**
5. 提交：**本地 `git add -A && git commit`（commit message 用中文，格式：`feat(M<NN>): <一句话>`，**不要带 `#123`**）**。
   **不要 push**（当前是本地开发模式，推送由用户统一安排）。
6. 汇报时列出：改了哪些模块、测试全绿输出、code-review 的结论、以及**有没有遇到与工单假设不符的现实**（这比"做完了"更重要）。
7. 如果这张工单大到一次会话做不完（超出上下文窗口），用 `create_goal` 建立持久目标，让本会话跨自动续跑轮次接着做——**不要用 ralph 写代码**（ralph 每轮换新代理，写代码需要连续上下文）。

约束：
- **Windows 相关的逻辑必须可测**：真实截图源/真实 UIA 树/真实 SendInput/真实模型响应都要有注入式假实现，CI 里不碰真桌面、不花钱。
- **坐标换算链路只能有一个真值来源**，任何地方都不许出现第二份缩放或标度逻辑。
- **模型客户端必须显式带 user-agent + x-opencode-client**，且 base URL 与模型 id 集中在配置里，禁止散落在代码中。
- **密钥只从仓库 `.env` 读**（`MINICODE_ANTHROPIC_API_KEY` 等），不许再从 `~/.dsh/.credentials.yaml` 读，也不许硬编码。
- 遇到与工单假设冲突的发现，**不要自己改需求**，停下来写进汇报，由我决定。
```

**这一节点在教什么**：`implement` 的定义只有 15 行，但它是**唯一真正写代码的节点**，它内部会自己调 `tdd`（纪律层）和 `code-review`（收尾）。技能包的设计哲学是：**每个工单一个全新会话**——因为工单是自包含的，上一张的上下文对下一张是负担。`ask-matt` 原文说得很直白：*"Each ticket is self-contained, so the last one's context is disposable."*
---

## 节点 5 — `/handoff`（会话之间搬运，必要时才用）

```
/handoff

我要换一个会话（换 harness 或换目录），把当前进展压成交接文档。

请写一份交接文档，包含：
- 已完成到哪张工单、当前分支与提交状态
- 关键实现决策及其理由（尤其是与调研底稿冲突后被修正过的那些）
- 三个原型（src/minicode/coord/、exec/、verify/）当前的状态与测试输出
- 已知未解问题、以及下一个会话必须先读的文件清单
- **踩过的坑**（尤其是那些静默失败的：ctypes INPUT union、SendInput 返回 0、DPI 感知顺序、UIA 脱敏、crop 裁剪来源、Cloudflare 1010、FreeTierError）

写到 .scratch/handoff/<日期>-<主题>.md（**这是本地文件，不进 GitHub**；`.scratch/handoff/` 已在 .gitignore）。
```

**这一节点在教什么**：`handoff` 是**阶段边界**的五种选择之一（Continue / New session / handoff / Subagent / compact）里的第三种。它不是必须的——如果只是换会话且工单是自包含的，**直接开新会话读工单就行**。`handoff` 的真正价值在于：换 harness、换目录、或者中途分叉一条支线时，它能把"只有这个会话知道的东西"搬过去。

---

## 附加：`/code-review` 单独用（完工后自查）

```
/code-review

**用 ralph 循环做这次审查**（用户明确要求使用 ralph 循环做审核验证）。`maxRounds` 给 3。每轮一个全新代理，**只读不写**，输出一份 `docs/reviews/<日期>-<主题>.md`。

审一下当前分支相对 main 的改动，双轴审：Standards（是否符合 AGENTS.md、docs/00-现状.md 与 docs/research/01-架构决策备忘.md 里已定下的约定）和 Spec（是否符合 `docs/tickets/` 下对应工单的验收标准）。

⚠️ **不许联网**：Spec 轴的工单来源直接给本地文件路径 `docs/tickets/M<NN>-<slug>.md`，不要用 `gh issue view` 去取（当前无网络，挂了会一直等）。fixed point 也显式给出，别让它自己猜。

重点盯这几条红线有没有被绕过：
- 坐标换算链路是否只有单一真值来源
- crop 是否从原始物理分辨率图裁
- crop 精定位是否有门控
- UIA 树是否在脱敏之后才进模型上下文
- 标定失败时视觉通道是否真的被禁用
- 模型客户端是否带 user-agent + x-opencode-client，base URL 与 model id 是否集中配置
- 密钥是否只从仓库 `.env` 读（不许再从 `~/.dsh/.credentials.yaml` 读、不许硬编码）

审完只写报告，**不要 commit、不要 push**。
```

---

## 附：全流程一张图

```
节点 0  物理脚手架   ✅ 已完成 (e4d1830)          ← 不用技能
   ↓
节点 1  /setup-matt-pocock-skills  → AGENTS.md + docs/agents/  ✅ 已完成 (55d5408)  【单干：一问一答】
   ↓
节点 2  /to-spec                   → docs/spec.md（本地真值）→ 联网后发 Issue  【可派 3~4 agent 核对四块底稿】
   ↓
节点 2.5 /domain-modeling          → CONTEXT.md 词汇表 + docs/adr/*.md   【单干/1~2 agent】
   ↓
节点 3  /to-tickets                → docs/tickets/M<N>-<slug>.md（本地真值）→ 联网后发 Issue + 原生依赖边
   ↓                                                【适合派团队：每切片一个 agent】
节点 4  /implement  ←── 每张工单一个【全新会话】──┐
   │  ① create_goal（目标模式，驱动跨轮次续跑）              │
   │  ② tdd 实现 → 每块 commit → goal complete               │
   │  ③ code-review（技能双 subagent）+ ralph 复核 → 改 → commit │
   ↓                                    【主会话单干 + goal 驱动】
   └── 下一张工单（工单自包含，上下文可弃）────────┘

⛔ 当前模式：本地优先——**每个节点结束都本地 commit，一律不 push**（无 VPN）。
   **一律只做本地 commit，不推不发**；报告里列 `git log --oneline origin/main..HEAD`。

跳过：/grill-me、/grill-with-docs（本项目已讨论透，直接进 to-spec）
可选：/handoff（换 harness/换目录时）、/research（要再查资料时）、
      /diagnosing-bugs（遇到难 bug 时）、/triage（处理非自建 issue 时）

════ 并发工具速查（详见「DSH 能力地图」）════
团队 spawn_teammate  → 派前先 list_agents 查占用；成员数 ≤ maxMembers（默认16）
workflow            → 规模化 fan-out；schema 只能用 12 个关键字；分波 ≤5~8 个
ralph               → 审核/验证专用；用户必须明确要求；maxRounds 给 3~5
goal                → 同一会话的长任务；写代码用这个，不可用 ralph
```

---

## 附：当前已定的决策（**别再问用户了**）

| # | 决策 | 结论 | 依据 |
|---|---|---|---|
| 1 | 首批目标应用 | Windows 设置 → Chrome（BOSS直聘页）→ 微信 → DSH / Tabbit（Electron）→ cc-switch | 用户已指定；按「UIA 覆盖度差 → 扁平 DOM → UIA 可用 → Electron」排 |
| 2 | 脚手架要不要从 `b70dc0b` 拆出来 | **不拆** | 已推送；force push 会动另一个会话的改动，收益只是 commit message 好看 |
| 3 | 推送策略 | **本地优先**：本地 commit 必须做，push 等用户开 VPN | 实测前 9 次推送全败，第 10 次靠 VPN |
| 4 | `gh` 调用方式 | 用**全路径** `C:\Program Files\GitHub CLI\gh.exe` | 实测不在 PATH |
| 5 | issue tracker | **GitHub Issues**（不是 local markdown） | 仓库已建、gh 已认证、依赖端点实测 200 |
| 6 | 本地工单真值来源 | **`docs/tickets/M<N>-<slug>.md`** | 离线时 GitHub 发不了，本地必须有一份能读能改的 |
| 7 | GitHub 阻塞边 | **`issues/<child>/dependencies/blocked_by` + 阻塞者的 database id**；`sub_issues` 只管父子 | 技能模板 issue-tracker-github.md 的 Wayfinding 段 |
| 8 | 模型客户端密钥来源 | **统一读仓库 `.env`** | 原型读的是 `~/.dsh/.credentials.yaml`，`.env` 目前是摆设 |
| 9 | 主线模型栈 | **`minimax-m3.1-flash-free` 一个**（兼任大脑和眼睛） | 已实测；`minimax-m3` / `deepseek-v4-flash` 是纯文本，带图时瞎猜 |
| 10 | 语言 / 架构 | **Python** / **方案 B**（拼装骨架 + 自建两级 grounding 与校验反思层） | UIA 绑定只有 Python 成熟；UFO / UI-TARS 都没有 crop 精定位层 |
| 11 | agent 约定文件 | **AGENTS.md**（不是 CLAUDE.md）+ `docs/agents/` 三件套 | 节点 1 已建；DSH 两个都读但以 AGENTS.md 为主 |
| 12 | 哪些是已知未测 | 小图标(≤16px)、UWP 纯视觉路径、同名歧义、中文输入 | 架构备忘 §9 与 `docs/01-现状.md` §4 |
| 13 | 唯一的测试缝 | **动作执行缝**：输入目标描述+截图+UIA 快照，输出动作+最终落点+意图；缝下全假实现 | C-03/C-06/C-15/C-16 四条红线全落在感知→定位段（L1 结构感知 / L2 视觉定位）；放任务级缝就得假造模型，等于测假模型 |
| 14 | 缝要不要带定位证据链 | **要，但走可注入的 trace sink 旁路**，主返回类型保持窄 | 架构备忘 §2.5 第 3 条硬规则要三段耗时+三类失败分开埋点；C-15/C-16 需要可断言的面 |
| 15 | L3 决策层谁测 | **单测不覆盖**，由端到端评测（ScreenSpot-Pro 同表跑 + 真实任务回放）兜底 | 决策层实质是模型本身；假造模型只能测到自己的假脚本 |
| 16 | 引证纪律 | spec/文档引底稿必须给**能打开核对的行号**；核对不上的数字写"未核"并给可核替代证据 | 已知坏引证：架构备忘 §39 / 选型推荐 L161 的「85.9% / 52.4%」指向底稿 03 L317/L361，那里没有这组数 |
| 17 | 首批任务清单 | **`docs/target-tasks.md`**：每个目标应用 2~3 个任务 + 可判定期望结果 | spec 只定了应用没定任务，端到端用例写不出来、M3 验收会落空 |

**最后一条纪律：这些是工具，不是流程。** 每个节点先判断「任务能不能切成互不依赖的块」——
能切且合并成本低 → 派团队；不能切 → 单干。**setup 这种一问一答的技能派团队是负收益。**
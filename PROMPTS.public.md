# minicode — Matt Pocock 技能包全流程提示词包（去密钥版）

> **本文件不含任何密钥**（真实值在 `.env`，已被 .gitignore 排除）。
> 带密钥的本地版本在 `PROMPTS.md`（同样 gitignore）。

> 用途：在一个**全新的 DSH 会话**里，从零把 minicode 建起来。每个节点一条提示词，直接复制粘贴。
> 约定：每条提示词都在 `C:\Users\22197\Desktop\minicode` 作为工作目录下发出。
> 提示词之间**不要合并**——技能包的设计前提就是"每个工单一个干净上下文"。
>
> **v2（2026-10-20）**：模型 id 修正为 `minimax-m3.1-flash-free`（不是 `minimax-m3`，那是另一个模型）；
> 新增"已实测验证"一节，所有数字都是本机跑出来的。

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

### 还没测的（留给实施阶段）

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
| `create_goal` / `update_goal` | 跨自动续跑轮次的**持久目标**，同一会话继续 | 同一会话内的长任务 | 与 ralph 互补：**ralph = 每轮换新代理，goal = 同一会话接着干** |
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
- 首批目标应用：Chrome（BOSS直聘页）、微信、Windows 设置（UWP/WinUI，**无 UIA 树，只能走视觉**）、DSH / Tabbit（Electron）、cc-switch
- **Issue tracker：GitHub Issues（原生）** —— 仓库 https://github.com/xiaochuan7/minicode （private，main 分支）；`gh` CLI v2.102.0 已装并已认证（keyring，账号 xiaochuan7）；5 个 triage 标签已建好；**`/repos/.../issues/{n}/sub_issues` 端点实测 200 → 可用原生阻塞关系**
- 调研底稿位置：`docs/research/`（架构备忘 + 方案排名 + 10 份调研底稿）
- 已验证原型位置：`src/minicode/coord/`（50 单测全绿）、`src/minicode/exec/`（本机自检 5 PASS / 0 FAIL）、`src/minicode/verify/`（4 个实测脚本）

---

## 节点 0 — 物理脚手架（不用技能，纯指令）

```
在 C:\Users\22197\Desktop\minicode 做一次性脚手架准备，做完停下等我确认，不要开始写业务代码。

**工作方式：这一节点单干，不要建团队、不要开 workflow、不要开 ralph。** 复制文件、装依赖、跑测试是顺序任务，派并发只会更慢更乱。

背景：这里要从零建一个 Windows 桌面 computer-use agent。架构已定稿（方案 B：拼装现成骨架 + 自建两个缺口）。调研底稿和三个已在本机验证过的原型/脚本都在别的目录，这一步把它们搬进来当起点——不要重写它们。

请做：
1. **git 与远端我已经全部就绪**：仓库 https://github.com/xiaochuan7/minicode （private）、分支 main（已 push 且跟踪 origin/main）、user.name=xiaochuan7、user.email=2219783254cf@gmail.com、`gh` CLI 已认证。你只要 `git status -sb` 确认干净即可，**不要重新 init、不要改 remote**。
2. 复制已验证资产进 src/（保留全部测试与 README，不要重写、不要改逻辑）：
   - C:\Users\22197\Desktop\gui-cu\proto\coord\   → src/minicode/coord/
   - C:\Users\22197\Desktop\gui-cu\proto\exec\    → src/minicode/exec/
   - C:\Users\22197\Desktop\gui-cu\proto\verify\  → src/minicode/verify/
3. 复制文档进 docs/research/：
   - C:\Users\22197\Desktop\gui-cu\docs\01-架构决策备忘.md
   - C:\Users\22197\Desktop\gui-cu\docs\02-方案全景与双打分排名.md
   - C:\Users\22197\Desktop\gui-cu\docs\03-选型决策与推荐.md
   - C:\Users\22197\Desktop\DSH-work\research\01~10 全部 → docs/research/bottom-drafts/
4. **`.env` 我已经写好了，不要覆盖、不要回显里面的 key。** 它已包含：
     MINICODE_ANTHROPIC_BASE_URL=https://opencode.ai/zen/go
     MINICODE_ANTHROPIC_API_KEY=<你的 OPENCODE_GO_API_KEY>
     MINICODE_MODEL=minimax-m3.1-flash-free
     MINICODE_SESSION_ID=dsh-union-alpha
     MINICODE_HTTP_USER_AGENT=opencode/1.0.0
     MINICODE_HTTP_CLIENT=cli
     （另有空的百炼/zen 备用项，主线不用）
   你的任务只是**校验**这三项存在且值正确（MODEL / BASE_URL / SESSION_ID），并确认 `.gitignore` 已排除 `.env`。
   **读 .env 时不要把 key 打印到终端或写进任何提交的文件。**

   GitHub 相关（也已经配好，**不要再问我要 token**）：
     账号 xiaochuan7 / 仓库 https://github.com/xiaochuan7/minicode （private）
     GITHUB_TOKEN=<你的 GITHUB_TOKEN>
     `gh` CLI v2.102.0 已装（`C:\Program Files\GitHub CLI\gh.exe`，已在机器 PATH），已 `gh auth login`（keyring，账号 xiaochuan7）
     5 个 triage 标签已建：needs-triage / needs-info / ready-for-agent / ready-for-human / wontfix
     `sub_issues` 端点实测 200 → 阻塞关系用原生 sub-issue
   **token 也不要打印到终端或写进任何提交的文件**（`.env` 已有 `GH_TOKEN` / `GITHUB_TOKEN` 两项）。
5. 写 pyproject.toml：requires-python >= 3.11；dev 依赖 ruff + pytest；core 依赖标注为可选（core 必须只靠标准库可跑，Pillow/numpy/httpx 按需）
6. 把「已实测验证」写进 docs/01-现状.md，务必包含这三条（都是本机跑出来的，别写成推测）：
   - 接入配方与三个坑：① 必须带 user-agent: opencode/1.0.0 + x-opencode-client: cli，否则 Cloudflare 1010；② free 模型只能走 /zen/go，走 /zen 会 FreeTierError；③ thinking 关不掉（400），只能用 output_config.effort=low 降延迟（10.6s → 5.9s）
   - 坐标量纲实测是 0~1000；真实桌面两级 grounding：L0 3.4px → L1 2.3px（目标「开始」按钮，UIA 真值对照）
   - 还没测的：小图标(≤16px)、UWP 纯视觉路径、同名歧义、中文输入
7. 验证搬进来的东西仍然通过：
   cd src/minicode/coord && python -m unittest test_coordinates
   cd ../exec && python selftest.py
   cd ../verify && python test_01_text_and_probe.py     ← 这个会真的调 API（.env 里已有 key）
   把**实际输出**贴给我。第 7 步如果失败就停下报告，**不要自己造 key、不要改模型 id**。
8. 提交并推送：`git add -A && git commit -m "chore: 脚手架（搬入原型与底稿、建 pyproject、实测通过）" && git push`。**确认 .env 没有被 add 进去**（.gitignore 已挡，但你要 `git status` 看一眼 staged 清单）。
8. 再写一份 docs/00-现状.md：机器参数、方案 B 组成、坐标契约、三条红线、首批目标应用、三个原型的位置与测试状态。

汇报后停下。
```

**这一节点在教什么**：`setup-matt-pocock-skills` 只管"仓库的配置与文档布局"，不管"把已有的东西搬进来"。从零项目最容易犯的错是先写代码后补配置。这里反过来——**先把已知正确的资产放进去，再让技能包去配置仓库**。第 7 步尤其重要：**让新会话自己跑一遍实测脚本**，它才知道这些数字是真的、不是文档里抄的。

---

## 节点 1 — `/setup-matt-pocock-skills`（仓库骨架）

```
/setup-matt-pocock-skills

**工作方式：这一节点单干，不要建团队、不要开 workflow、不要开 ralph。** 这个技能的核心是"分节问用户、每节带推荐答案"，分身没法替你回答，必须主会话自己来。

这是 minicode——一个 Windows 桌面 computer-use agent 的新仓库，目标是控制鼠标键盘操作 GUI 应用。

请先读 docs/01-现状.md 和 docs/research/01-架构决策备忘.md 的第 0 节和第 9 节，了解项目是什么，再执行 setup。

我已知的信息（供你判断，不要重复问我）：
- issue tracker：选 **GitHub Issues**。仓库 https://github.com/xiaochuan7/minicode 已建好（private），`gh` CLI v2.102.0 已装并已认证（账号 xiaochuan7，keyring），5 个 triage 标签已建好，`gh issue create` 可直接用。**`sub_issues` 端点实测 200，所以阻塞关系用 GitHub 原生 sub-issue 表达。** 记录时把「PRs as a request surface」保持关闭。，工单放 .scratch/<feature-slug>/issues/<NN>-<slug>.md。理由：这是从零的新项目、还没有 GitHub remote，先不引入 gh 认证和远程依赖。将来要切 GitHub 时重跑这个技能即可。
- triage 标签：**用默认五个**（needs-triage / needs-info / ready-for-agent / ready-for-human / wontfix），不要覆盖——这五个标签**我已经在仓库里建好了**，你只需确认存在。
- domain docs：**single-context** —— 根目录一个 CONTEXT.md + docs/adr/。
- 仓库根没有 CLAUDE.md 也没有 AGENTS.md：按技能规则应该问我要建哪个。请问我。
- 项目是单包 Python，不是 monorepo。

请按技能流程执行：探索 → 分节问我（每节带推荐答案）→ 给我看草稿 → 确认后写入。
```

**这一节点在教什么**：`setup` 技能是**一次性的、prompt-driven 的**，不是脚本。它会先探索仓库再分节问你，每节都带推荐答案让你一个词就能接受。三个决策（issue tracker / triage 标签 / domain docs）不是随便选的——它们决定了后面 `to-spec`、`to-tickets`、`triage` 这几个技能往哪儿写、读什么。**选 local markdown 的代价**：没有远程协作、没有 PR 作为请求面；**收益**：零认证、零远程依赖、可以先跑通全流程。

---

## 节点 2 — `/to-spec`（把讨论变成 spec，跳过拷问）

```
/to-spec

这是 minicode——Windows 桌面 computer-use agent。规格要覆盖**整个产品**，不只第一个里程碑。

不要访谈我，直接综合下面的材料产出 spec：
- docs/01-现状.md（含本机实测的接入配方与实测数字）
- docs/research/01-架构决策备忘.md（完整的技术方案、坐标协议、动作协议、检查项 C-01~C-16）
- docs/research/03-选型决策与推荐.md（方案 B 的组成、三个候选方案、为什么不选另外两个）
- docs/research/02-方案全景与双打分排名.md（59 个方案的双打分，用来解释选型理由）
- docs/research/bottom-drafts/ 下的 10 份调研底稿（事实与出处来源）
- src/minicode/coord/、src/minicode/exec/、src/minicode/verify/（已通过测试的原型与实测脚本）

**工作方式：可以用团队并行核对底稿，但只在下面这个切法下用。** 底稿有 8~10 份，可按四块切开各派一个 agent 核对：**①坐标协议与 crop 精定位（底稿 02 + 09 + 10）②Windows 栈 / DPI / UIA（底稿 04）③方案格局与选型（底稿 03）④API 成本与模型接入（底稿 05）**。每个 agent 回报"这一块里哪些结论能进 spec、哪些不能进（附底稿行号）"，**由主会话合并成一份 spec**。
- 派之前先 `list_agents` 查占用，**成员数不要超过当前上限**（`maxMembers`）。
- 派 3~4 个就够，**不要为了"并行"而并行**——如果四块里有一块内容很少，就只派三个。
- **不要开 ralph**：写 spec 是综合判断，不是反复验证。
- 如果你判断这次综合根本不需要并行（底稿已经读过），**就单干**，别为了用工具而用工具。

写 spec 前，按技能流程的第 2 步先向我确认**测试缝（seams）**。我的倾向是：唯一的缝在「动作执行层」——即"给定一个目标描述与一帧截图，产出坐标与动作"这一层，测试全部打在这一层；Windows 相关的部分用注入式假实现（假截图源、假 UIA 树、假 SendInput、假模型响应），让整套逻辑**不需要真桌面、不需要 API key 就能测**。请就这个缝的位置跟我确认。

spec 要点（务必覆盖）：
- Problem / Solution 写**用户视角**的痛点：Windows 桌面 GUI 操作没有统一接口，坐标会漂移，而现有方案要么闭源要么接不上
- User stories 要**长且全**，覆盖：坐标协议、UIA 通道、视觉兜底、crop 精定位、校验层、安全审批、动作日志、MCP 暴露、跨应用流程、失败恢复
- Implementation Decisions 里必须包含：
  · 坐标系契约（crop 内坐标 + 原点偏移）与 Anthropic 契约（整屏坐标）的差异
  · 标定探针必须是 2560×1600、判定容差 1px（因为 0~999 与 0~1000 的物理分歧 = 物理x/999）
  · crop 必须从原始物理分辨率图裁
  · crop 精定位必须门控（引 CCF 实测 −0.3pp）
  · UIA 树脱敏必须在所有通道之前
  · 模型接入的三个坑（Cloudflare 1010 / FreeTierError / thinking 关不掉）
  · 延迟预算：每步 UIA 命中 0 成本；视觉兜底单次约 5.9s（effort=low）
  · 三条硬红线
- Testing Decisions 写明"只测外部行为"的标准、哪些模块被测、已有先例（coord 的 50 个 unittest、exec 的 selftest、verify 的 4 个实测脚本就是要复用的先例）
- Out of Scope 明确写：UAC Secure Desktop 控制、全盘录制回放、自研 grounding 模型、跨平台（仅 Windows）、多显示器热插拔

按技能要求把 spec 发布到 issue tracker 并打 ready-for-agent 标签。
```

**这一节点在教什么**：`to-spec` 的 description 明确写着 **"no interview, just synthesis of what you've already discussed"**——它就是为你这种"已经讨论透了、只想沉淀成文档"的场景设计的。所以提示词的重点不是"告诉我你要做什么"，而是**把材料的地图指给它，并规定产出里必须包含哪些决策**。注意它会先问你测试缝在哪——**这个问题的答案是整个项目质量的决定点**：缝定高了，测试就测实现细节；缝定低了，Windows 依赖让 CI 跑不起来。

---

## 节点 2.5 — `/domain-modeling`（可选，但强烈建议：把词汇钉死）

```
/domain-modeling

minicode 的领域词汇现在还是散的，spec 里已经出现了一批含义可能漂移的词。请帮我把 CONTEXT.md 建成这个项目的词汇表，并记录需要 ADR 的不可逆决策。

请重点处理这些术语的歧义（这些词在 spec 里被反复用到，含义必须唯一）：
- **坐标系**：物理像素 / 截图像素 / 模型输入像素 / 归一化(0~1000) / crop 内 / 整屏 —— 六个空间各叫什么，转换关系怎么表述
- **定位（grounding）**：模型输出坐标 vs 系统换算坐标 vs UIA 控件框，三者怎么区分
- **通道（channel）**：UIA 通道 / 视觉通道 / 浏览器通道 / 混合动作通道
- **裁剪（crop）**：什么算 crop、什么算 zoom、"精定位"和"放大"差在哪
- **兜底（fallback）**：UIA→视觉、视觉→UIA、crop→粗定位，三种回退方向各叫什么
- **缝（seam）**：本项目里唯一的那个测试缝正式叫什么
- **动作（action）**：click / type / scroll / drag / inspect / execute_code 各自的边界
- **思考预算（effort）**：本项目里 effort 各档用在哪（动作步 low / 规划步 high）

以及这些不可逆决策（应为 ADR）：
- 语言选 Python 而非 TypeScript（理由：UFO² 是 Python、UIA 绑定只有 Python 成熟、三个原型已在 Python）
- issue tracker 选 local markdown
- 坐标系契约选"crop 内坐标 + 偏移"而非 Anthropic 的"整屏坐标"（理由：实测 MiniMax M3.1 用 0~1000 且未被训练过 Anthropic 契约）
- 决策层用"代码执行 harness"而非"逐条吐 action"（理由：OpenGCUA 消融 +15.5~33.6 分、步数 −55~63%）
- 架构主线选"结构优先、视觉兜底"而非"视觉优先"

写 CONTEXT.md 时用 `/writing-for-agents` 的规范（这是给 agent 读的文档，不是给人读的）。
```

**这一节点在教什么**：这是技能包里**"跑在底下"的词汇层**，不产出工单，只产出词汇。为什么要单独做？因为坐标这个词在项目里至少有六个含义，**如果 agent 在写第 30 张工单时把"物理像素"和"截图像素"混了，bug 会以最贵的方式暴露**。CONTEXT.md 是"防止术语漂移"的第一道闸门。

---

## 节点 3 — `/to-tickets`（拆 tracer-bullet 工单）

```
/to-tickets

把刚才的 spec 拆成 tracer-bullet 工单，发布到 **GitHub Issues**（仓库 xiaochuan7/minicode）。用 `gh issue create`，标题前缀 `M<N> `，正文写「What to build / Acceptance criteria / Blocked by」。**阻塞关系用 GitHub 原生 sub-issue**（`gh api -X POST repos/xiaochuan7/minicode/issues/<父号>/sub_issues -f sub_issue_id=<子号>`），因为 `sub_issues` 端点实测可用。**先建阻塞者再建被阻塞者**，编号顺序即依赖顺序。每个工单打 `ready-for-agent` 标签。

关键约束（务必遵守）：
1. **每张工单必须是可独立验证的垂直切片**——不是"实现 L1 层"这种水平切片。理想是一张工单就能 demo。
2. **每张工单要装得下一个全新的上下文窗口**。一张工单 ≈ 一次新会话。
3. **按"就绪边"（frontier）顺序编号**：没有阻塞的先编号。
4. **坐标契约的红线要落成可执行验收标准**，不能只写一句"注意精度"。可用的验收项（来自 docs/research/01-架构决策备忘.md 的 §2.6 检查项表）：
   - C-03：标定探针 2560×1600、容差 1px、含最右边缘目标点；标定不唯一则**视觉通道不可用**
   - C-06：crop 必须从原始物理分辨率截图裁
   - C-16：crop 精定位必须门控；在 ScreenSpot-Pro 上与 5 个基线同表跑，整体分不得低于无条件基线
   - C-15：UIA 树脱敏器单测全绿，未上线则禁止启用任何把 UIA 树送模型的通道
5. **坐标口径统一（写进代码规范）**：各家 eval 脚本口径不统一，混用会出现"接了官方脚本但分数全 0"的假象——ScreenSpot-Pro 官方 point 期望归一化 [0,1]、ZoomClick 副本期望像素、CCF 仓的 ScreenSpot-v2 脚本 bbox 是 `[x,y,W,H]`（宽高！）不是 `[x1,y1,x2,y2]`、ShowUI 用 0–999 网格。**内部一律用原图像素，只在 wrapper 返回前按目标脚本转一次。**
6. **门控要有可抄的参照**：CCF `LufeMC/gui-g2-3b-ccf` 的 `src/cursor_ccf.py`（220 单测）、UI-Zoomer 的 `S = IoU均值 + avg_conf > 1.5 就不 zoom`、RegionFocus 的"问模型 is this position correct? 判 YES 就不 zoom"。三选一或融合，但**必须有失败回退路径**（CCF 实测无条件做整体 −0.3pp：Text −2.3 / Mobile −5.2）。
7. **里程碑顺序：先做主线，治理类后置。**
   - M0 截图 → SendInput → 点中一个已知按钮，误差 <=2px
   - M1 定位层：UIA 通道 + 两级 grounding + 标定探针
   - M2 决策层：代码执行 harness + expect 校验 + 反思
   - M3 泛化：4 个目标应用跑通多步任务
   - **UIA 脱敏器放在 M1 一起做**（不是后置）：czdcode 实测从微信输入框的 UIA 树里捞到过 API key，而本架构明确要把 UIA 树送进模型上下文；成本只是一个正则 + 一个单测 + 密码框 Value 替换，不做等于拿已知会发生的事故去赌
   - **后置**：危险动作审批、动作日志回放、MCP 对外暴露、隐私策略
8. **模型客户端工单必须包含那三个坑的回归测试**：① 不带 user-agent + x-opencode-client → 期望 Cloudflare 1010；② 走 /zen → 期望 FreeTierError；③ thinking disabled → 期望 400。这三个都要有测试，否则换网关就会静默失败。
9. 首批目标应用按这个顺序推进：**Windows 设置**（UWP，完全无 UIA 树 → 强制走视觉，是视觉兜底层的活体测试）→ **Chrome**（有扁平 DOM 树）→ **微信**（UIA 可用但有弹窗门控）→ **DSH / Tabbit**（Electron）。
10. **不要用宽重构**去切分；如果某步只能横向铺开（UIA 封装层、动作协议层），按 expand–contract 排成三张工单。
11. **M0 的原型已经存在**：`src/minicode/exec/` 已经实现这一层的绝大部分（本机 5 PASS / 0 FAIL），工单是"把它组装成可运行的最小闭环并补上缺失的接线"，不是"从零实现"。

**工作方式：这一节点适合派团队。** 每个候选垂直切片可以派一个 agent 独立分析三件事——**交付什么（用户视角的端到端行为）/ 被谁挡（阻塞边）/ 验收标准怎么写才可执行**。分析完由主会话合并成一份清单给我批准。
- 派之前先 `list_agents` 查占用；**成员数不要超过当前上限**（`maxMembers`，默认 16）。
- 切片的数量就是团队规模，**不要超过切片数**（8~12 张工单就派 8~12 个；更多就分波）。
- 合并时要**统一坐标口径与红线**（第 5、6 条），不能让各 agent 各写一套。
- **不要开 ralph**：工单拆分是一次性综合判断，不是收敛问题。

按技能流程第 4 步，先把工单清单（编号 / 标题 / 阻塞边 / 交付什么）列给我，问粒度对不对、阻塞边对不对，我批准后再发布。
```

**这一节点在教什么**：`to-tickets` 的核心是 **tracer bullet（曳光弹）**——每张工单切一条窄但完整的路径，单独就能演示。它还强制一个**编号即依赖序**的约定：本地 tracker 下没有原生的 blocking link，所以靠编号和 "Blocked by" 字段表达依赖，**阻塞者必须排在前面**。第 4 步它一定要你批准清单才发布——**这个批准点是全流程里最值钱的一次人工干预**，因为切错了粒度，后面每张工单都会受影响。

---

## 节点 4 — `/implement`（每张工单一个全新会话）

```
/implement

工单：.scratch/minicode/issues/<把工单号填这里>.md

背景（这个新会话没有之前的所有上下文，请先读）：
- docs/00-现状.md 与 docs/01-现状.md（机器参数、接入配方与三个坑、方案 B、三条红线、首批目标应用、已实测数字）
- docs/research/01-架构决策备忘.md（尤其 §1 坐标协议、§2 分层架构、§3 动作协议、§2.6 检查项表）
- 该工单引用的上游工单（Blocked by 里的那些），以及它们已经在仓库里留下的代码

要求：
1. 严格按工单的验收标准做，**验收标准里凡提到编号（C-03 / C-06 / C-15 / C-16 等）的，必须真去查 docs/research/01-架构决策备忘.md §2.6 那一行确认判据**。
2. 按技能要求驱动 `tdd`：先写会失败的测试，再让它通过。**只测外部行为，不测实现细节。**
3. 频繁跑类型检查、频繁跑单个测试文件，最后跑全量测试套件。
4. 完工后按技能要求调用 `code-review` 审一遍（Standards + Spec 两轴）。
   **审核用 ralph 循环**（用户明确要求使用 ralph 循环做审核验证）：目标写成一句不可变的话，例如「对本次改动做两轴对抗式审查，产出 `docs/reviews/<工单号>.md`，必须覆盖 C-03/C-06/C-15/C-16 四条红线、坐标换算链路是否单一真值源、UIA 树是否在脱敏后才进模型上下文、标定失败时视觉通道是否真的被禁用；每条结论必须给出文件与行号，找不到问题也要写明查过哪些」。
   - `maxRounds` 给 **3**，不要用默认 256。
   - 目标里必须写全：审哪个分支、改动范围、输出写到哪、什么算通过——**每轮是全新代理，没有你的上下文**。
   - **审查用的 ralph 只看 diff 与文件，不许改代码**；发现问题写进报告，由你（主会话）决定要不要改。
5. 提交到当前分支（commit message 用中文，格式：`feat(<工单号>): <一句话>`）。
6. 汇报时列出：改了哪些模块、测试全绿输出、code-review 的结论、以及**有没有遇到与工单假设不符的现实**（这比"做完了"更重要）。
7. 如果这张工单大到一次会话做不完（超出上下文窗口），用 `create_goal` 建立持久目标，让本会话跨自动续跑轮次接着做——**不要用 ralph 写代码**（ralph 每轮换新代理，写代码需要连续上下文）。

约束：
- **Windows 相关的逻辑必须可测**：真实截图源/真实 UIA 树/真实 SendInput/真实模型响应都要有注入式假实现，CI 里不碰真桌面、不花钱。
- **坐标换算链路只能有一个真值来源**，任何地方都不许出现第二份缩放或标度逻辑。
- **模型客户端必须显式带 user-agent + x-opencode-client**，且 base URL 与模型 id 集中在配置里，禁止散落在代码中。
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

审一下当前分支相对 main 的改动，双轴审：Standards（是否符合 docs/00-现状.md 与 docs/research/01-架构决策备忘.md 里已定下的约定）和 Spec（是否符合 .scratch/minicode/issues/ 下对应工单的验收标准）。

重点盯这几条红线有没有被绕过：
- 坐标换算链路是否只有单一真值来源
- crop 是否从原始物理分辨率图裁
- crop 精定位是否有门控
- UIA 树是否在脱敏之后才进模型上下文
- 标定失败时视觉通道是否真的被禁用
- 模型客户端是否带 user-agent + x-opencode-client，base URL 与 model id 是否集中配置
```

---

## 附：全流程一张图

```
节点 0  物理脚手架（git + 搬原型/底稿 + .env.example + pyproject + 跑实测脚本）  ← 不用技能
   ↓                                                                    【单干】
节点 1  /setup-matt-pocock-skills   → docs/agents/ + CONTEXT.md 布局       【单干：一问一答】
   ↓
节点 2  /to-spec                    → spec（GitHub Issue，标 ready-for-agent）
   ↓                                              【可派 3~4 agent 并行核对四块底稿】
节点 2.5 /domain-modeling            → CONTEXT.md 词汇表 + docs/adr/*.md    【单干/1~2 agent】
   ↓
节点 3  /to-tickets                 → GitHub Issues + 原生 sub-issue 阻塞边
   ↓                                              【适合派团队：每切片一个 agent】
节点 4  /implement  ←── 每张工单一个【全新会话】──┐
   │   内部：tdd → code-review(ralph 循环,3 轮) → commit → push │
   ↓                                                       【主会话单干】
   └── 下一张工单（工单自包含，上下文可弃）────────┘

跳过：/grill-me、/grill-with-docs（本项目已讨论透，直接进 to-spec）
可选：/handoff（换 harness/换目录时）、/research（要再查资料时）、
      /diagnosing-bugs（遇到难 bug 时）、/triage（处理非自建 issue 时）

════ 并发工具速查（详见「DSH 能力地图」）════
团队 spawn_teammate  → 派前先 list_agents 查占用；成员数 ≤ maxMembers（默认16）
workflow            → 规模化 fan-out；schema 只能用 12 个关键字；分波 ≤5~8 个
ralph               → 审核/验证专用；用户必须明确要求；maxRounds 给 3~5
goal                → 同一会话的长任务；写代码用这个，不可用 ralph
```

**最后一条纪律：这些是工具，不是流程。** 每个节点先判断「任务能不能切成互不依赖的块」——
能切且合并成本低 → 派团队；不能切 → 单干。**setup 这种一问一答的技能派团队是负收益。**

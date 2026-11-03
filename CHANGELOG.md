# Changelog

本项目遵循 [语义化版本](https://semver.org/lang/zh-CN/)。日期为 Asia/Shanghai。

## v0.2.0 — 2026-11-04

本条目对应 v0.1.7 之后的交付（通宵任务 W1–W6）：**视觉配置收口**（W1）、
**精定位专项量化**（W2）、**d3 逐候选重排**（W3）、**长任务评测集与自愈层**
（W4/W5）。**为什么是 minor（0.2.0）而不是 patch**：两处行为契约变化——
① d3 重排的降级语义从「d3 不可用 = 不点（fail-closed）」改为「跳过重排，
用启发式 top1 继续」（W3.6，多选项模式已证伪后的逐候选改制配套）；② 视觉
通道的部署默认从「`.env` 可配第二模型」收口为「默认且唯一 deepseek-v4.1-flash」
（换模型已证伪一次，见 V2 报告）。`MINICODE_VISION_*` 可插拔机制与测试全部
保留——已部署的配置不受影响。

### 变更

- **视觉配置收口（W1）**：视觉通道默认且唯一使用决策层同款
  deepseek-v4.1-flash（仓库 `.env` 不再配置 `MINICODE_VISION_*` 键组，
  `load_vision_config` 回落决策层）；**删除** V2 的双模型跑批脚本
  `scripts/v2_paired_models.py`（防误用；报告生成器与配对 JSON 保留可复算）；
  README / V2 报告 / `load_vision_config` docstring 三处写明
  「换模型已证伪一次（见 V2 报告）」。
- **d3 逐候选重排（W3）**：V4 的「16~20 选项一次问」（整包评分，实测
  3 超时 + 3 网关 400 + 2 低置信）**已证伪并替换**为逐候选 `noul` yes/no——
  新增 `D3Client.decide_noul`（noul 请求/响应形状实测探明：无 criteria、
  yes/no 概率、无 choice 字段）与 `D3PerCandidateReranker`（启发式前 K=3
  逐个问，yes 最高者 < 0.6 不点转人工；不可用 → 启发式 top1 继续；
  429 退避 60s 单独计数；逐候选判定 JSONL 日志）。旧 `D3CandidateReranker`
  保留（接口不变，调用方择一注入）。

### 新增

- **精定位专项量化（W2）**：`scripts/w2_refine_quant.py`（S-Pro 同批 60 条
  注入式三臂：真实粗点 / GT 中心不放大 / GT 中心+生产 zoom；基线复用 W6
  pixel 臂零新调用）+ `scripts/w2_refine_canvas.py`（自建 canvas 22 目标
  × 粗点偏移 0/50/100/200px 扫描，GT 从截图品红条实测）+ 离线 crop 几何
  与重试效果分析。**结论：精定位一级有独立、统计显著的价值**——粗定位
  单独 13/46 vs 粗+精 22/46（Δ=+19.6pp，p=0.0063，CI 排 0）；GT 粗点下
  73.3%（Δ=+51.7pp 达 MDE）；中位误差 41px→5.7px。生产参数（crop 1×/
  取整/zoom 规则）经 A/B 全部维持原状。报告：
  `docs/w2-refine-quant.md`（原始 JSON 四份入库可复算）。
- **长任务评测集 + 自愈层（W4/W5）**：`scripts/w4_long_tasks.py`——
  10~30 步跨应用任务的 reset/步骤+故障注入/judge/budget 三件套，
  四类确定性故障注入器（对话框/遮挡失焦/位置漂移/元素禁用），每步包
  W5 自愈包装（五类失败分类 → 策略 → 触发/救回落盘 `heal-log.jsonl`）。
  首轮 T1–T4（覆盖全部四类故障）4/4 pass（全 clean，零自愈触发——确定性
  步骤序列对这四类故障天然耐受；T4 带诚实注记，见结果文档）。
  `docs/w4-long-tasks.md`（10 条全量设计）+ `docs/w4-long-results.md`。
- **真机让位友好包装**：`scripts/wait_idle_then_run.py`（等桌面持续空闲
  N 秒再启动真机任务，用户活跃超时则如实放弃）。

## v0.1.7 — 2026-11-03

本条目对应 v0.1.6 之后的交付（通宵任务 V1–V7）：**视觉通道可插拔**（V1）、
**换模型视觉配对实验**（V2）、**候选源补齐**（V3：零模型图标提议 + ScreenParser
检测模型）、**d3 候选重排与置信度闸门**（V4）、**视觉必需任务集**（V5）、
**A/B/C 三组量化对比**（V6）。
无破坏性变更：`MINICODE_VISION_*` / `MINICODE_D3_*` 全部缺省回落到既有
`MINICODE_*` 行为（只配一组键时逐字节同从前）；请求日志新增字段全部可空。

### 新增

- **视觉通道可插拔（V1）**：`MINICODE_VISION_{PROVIDER,BASE_URL,API_KEY,MODEL}`
  配置组逐字段覆盖决策层、缺省逐字段回落；`load_vision_config()`；Radeon 网关
  （OpenAI 兼容 + image_url data URL）实测可直用；请求日志新增
  `channel / image_width_px / image_height_px / rate_limited / endpoint_host`
  五个字段 —— 视觉调用可直读、429 与模型失败分开计数。
- **d3 决策模型客户端（V4）**：`/v1/systemone` 唯一入口（`/chat/completions`
  实测 400）；`D3Config / load_d3_config`（d3 键 → 视觉键 → 决策键回落）；
  每次调用落一行 JSONL（选项数/概率/confidence/耗时/HTTP/429 标记，不含 key）。
- **d3 候选重排 + 置信度闸门（V4）**：`D3CandidateReranker` —— 候选得分差
  < 0.1（配置项）才调 d3（锦标赛），confidence < 0.5（配置项）**不点转人工**
  （fail-closed），d3 失败降级 = 不点（不是猜）；**0.0px 契约**：只选候选编号，
  落点永远取候选框物理中心；`TwoStageGrounder` 新增 `reranker` 注入口，
  重排的 fail-closed 是**终态**（不回落点式路径 —— 回落等于绕过闸门）；
  累计统计 `stats` 直读 d3 调用数 / 低置信拒绝数。
- **零模型图标候选源（V3）**：`IconProposalCandidateSource` —— 梯度 + Otsu +
  连通域的图标区域提议，IoU+pHash 去重（不同位置的相似图标不合并），OCR 邻近词
  或位置型占位描述；住在 model 包（ground 有只许标准库的源码级红线）。
- **ScreenParser 检测模型候选源（V3）**：`ScreenParserCandidateSource`
  （docling-project/ScreenParser，Apache-2.0，YOLO UI 检测）—— 物理像素候选 +
  检测标签描述 + OCR 邻近词；torch/ultralytics 懒加载，环境缺失抛带指引的
  RuntimeError（调用方退回零模型源并如实记录）；**不引入** OmniParser
  icon_detect（AGPL）。
- **视觉必需任务集（V5）**：`scripts/v5_vision_tasks.py` —— 8 条 canvas 任务
  （文字按钮 / 无字图形按钮 / 无字色块 / 图形场景），DOM 无节点、UIA 原理上
  不可达 → 视觉通道兜底；judge 只读窗口标题（`V5HIT:` 标记，不读截图）；
  `--group a|b|c` 一台三用。

### 评测与文档

- **V2 视觉配对实验**：`scripts/v2_paired_models.py`（臂 A 复用 W6 同批 60 条、
  臂 B Qwen3.8-27B；两臂提示词逐字相同；429/传输失败单独计数；增量落盘 +
  断点续跑）+ `scripts/make_v2_report.py`（报告 + 配对 JSON 入库）。
- **V3 覆盖率量化**：`scripts/v3_icon_coverage.py`（OCR / 零模型 / 检测模型
  三源对照，candidate_coverage 三列口径）。
- **V6 A/B/C 对比**：`scripts/make_v6_report.py`（三组 summary → 对比表 +
  JSON 入库；原 30 条的结构性等同论证如实写明）。
- 视觉必需任务集说明：`docs/v5-vision-required.md`。

## v0.1.6 — 2026-11-02

本条目对应 v0.1.5 之后的交付：W5 可靠性四件套、W7 全部七条 P2 修复、W4 真机任务级
评测台与判分管线硬化、W6 视觉坐标契约配对实验、W8 文档与真机护栏演示。
无破坏性变更；协议只加不改（`ActionRequest.request_id` 为可选字段）。

### 新增（W5 可靠性四件套）

- **模型调用重试缝**：`RetryingModelCaller`——指数退避 + 抖动；可重试 = 传输错误 /
  429 / 5xx / 空文本，不可重试 = 其余 4xx / 配置错（快速失败）；每次调用记录 attempts。
  decision / uia / ground / e2e 四处 CLI 接线。
- **幂等键**：`ActionRequest.request_id`（协议只加不改）——同键重放返回首次响应
  （进程内 LRU，512 条），不二次执行。
- **断点续跑**：DecisionHarness 每轮追加 `checkpoint.jsonl`（摘要 / 笔记 / 步记录），
  `--resume` 回放文本账本 + 重抓帧续跑，轮次上限按总轮数计。
- **定位失败自愈**：RetryingSeam 三钩子（均可选注入，默认行为不变）——找不到目标 →
  重述一次；界面没变 → 等待 + 变化检测；点不动 → 换通道兜底。

### 正确性修复（W7 P2 七条 + W4 真机评测台踩出）

- **UIA 查询池饱和与熔断**：挂死的 COM 调用会永久占住 worker，旧实现既不挡池满也不熔断
  ——8 线程占满后通道永久退化为「每次白等一个超时预算」。现在占满立即失败、连续 3 次
  超时熔断 30s、成功复位。
- **`_publish` 释放刚定位的元素**（一代宽限）：校验层再查一次树会把上一次观测的 COM
  元素全部 release，Invoke 按元素 id 拿到已释放指针，InvokePattern 静默降级。现在旧代
  退位保活一代（`live_element` 查两代），下下次发布才释放。
- **浏览器窗口矩形读取静默失败**：`GetWindowRect` 失败返回 `(0,0,0,0)` → 视口比例 0 →
  元素全零框被静默丢弃，看起来像「页面没元素」。现在显式返回 `None`，视口量不出 → 空快照。
- **grounding 负出口误判**：`parse_optional_point` 把 payload 里**缺 x/y** 也当「明确负回答」
  ——现在必须 x/y 显式为 null 才算负出口。
- **混合方向缩放**：先降后升的路径遇到一维超限一维不足时走错分支，产生非期望尺寸。
- **落点取整三套口径**：banker's round / 纯 floor / floor(x+0.5) 在半点与负数上分歧
  ±1px——收敛为 `to_physical_point` = floor(x+0.5) 单一口径（4 处旧期望值按契约修正）。
- **键盘注入「子串假成功」判定**：before/after 差分改为「after 变化 且 包含注入文本」。
- **键名表字母键映射错误**：`_VK_NAMES` 把 `a`~`z` 映到 ASCII 小写（0x61~0x7A，
  实为 VK_NUMPAD1~VK_F24 区段）而非 VK 码（0x41~0x5A）——「ctrl+s」实际发出
  **Ctrl+F4（关闭标签）**、「ctrl+v」是 Ctrl+F7，全部字母组合键静默打偏。
  功能键/Enter/Tab 不受影响。配红测试（字母必须映射 0x41+i）。
- **guard GuardPolicy 字符串级别 fail-open**：`GuardPolicy(level="L1")` 存了字符串，
  `policy.level is AuthLevel.L1` 恒 False，L1 强制**静默失效**。现在 `__post_init__`
  收敛枚举、非法值报错。配红测试。
- **guard L1 空白名单 fail-open 堵成 fail-closed**：旧实现空白名单跳过白名单
  检查（fail-open）；现在想用 L1 必须显式给窗口集合，空集合 = 无可操作窗口。
  配红测试。

### 评测（W4 / W6）

- **任务级评测台**：30 条本机可复现任务（reset / steps / judge 三件套），attempt 五态
  口径（pass/fail/error/blocked/paused，后两者剔出分母），三有效轮 90 轮×任务对：
  **s@1 = 248/249 = 99.6%，s@3 = 86/87 = 98.9%，危险动作 0、人工接管 0**；
  判分器负向对照 3/3。汇总表 `docs/w4-eval-summary.md` 由 attempt 明细重算可复现。
- **视觉坐标契约配对实验**（ScreenSpot-Pro，60 样本分层，同模型同提示词逐条配对）：
  臂 A 像素契约 vs 臂 B 0–1000 归一化，唯一差异是提示词的坐标声明行。
  **Δ = −10.0 百分点（0-1000 相对像素）**，配对 bootstrap 95% CI [−18.3, −1.7]（不含 0），
  mid-p McNemar p=0.039（保守对照 exact p=0.070 不显著；|Δ| 未达 MDE 13.2pp）——
  **方向性证据：像素契约不劣于且可能优于归一化契约**，维持现状。模型 deepseek-v4.1-flash
  身份与采样参数如实披露于 `docs/w6-vision-paired.md`；配对数据入库 `docs/w6-paired.json`。

## v0.1.5 — 2026-11-02

本条目对应 v0.1.4 之后的发布收口：两个**破坏性变更**（见 MIGRATION）、四个 P1 正确性修复、
传输层性能、CI 上线与对外表述修正。

### MIGRATION（破坏性变更，升级前必读）

1. **本地 HTTP serve 写接口必须带 token，并校验 `Host` / `Content-Type`**。
   升级后，调用 `minicode serve` 写接口（动作类）的客户端必须：
   - 请求头带 `X-Minicode-Token`，值与服务端环境变量 `MINICODE_SERVE_TOKEN` 一致
     （服务端未配置该变量时，写接口一律 403 —— fail-closed，不会「无 token 照常工作」）；
   - `Content-Type: application/json`（此前 `text/plain` 的简单请求不再被接受）；
   - 目标 Host 必须是回环地址（DNS rebinding 域名将被拒）。
   影响面：任何把 serve 当「本地无鉴权 RPC」用的脚本。GET 类只读接口不受影响。
2. **MCP 键盘输入错误码 `approval_denied` → `injection_blocked`，非交互前端默认拒绝键盘输入**。
   升级后，HTTP / MCP 这类非交互前端的 `type_text` / `press_key` 一律直接拒绝
   （错误码 `injection_blocked`），不再进入审批流程；交互式 CLI 行为不变。
   影响面：依赖旧错误码分支的前端需要改判 `injection_blocked`；期望「弹审批再放行」的
   调用方需要改用交互式前端。

### 安全修复

- **P0 · HTTP serve 写接口信任边界**（`src/minicode/serve/http.py`）：原先只绑回环，
  没有鉴权与来源校验——任意网页可用 `Content-Type: text/plain` 的简单跨域请求
  （免 CORS 预检）驱动这个能真点鼠标的服务，DNS rebinding 后还可伪装同源。
  现在写接口有四道门（全 fail-closed）：`Host` 必须回环；`Content-Type` 必须为
  `application/json`；必须带自定义头 `X-Minicode-Token` 且与服务端
  `MINICODE_SERVE_TOKEN` 环境变量匹配（未配置 token 时写接口全部 403）；
  请求体上限 1 MiB（413）、并发上限 8（503）。CORS 预检不应答（响应永远不带
  `Access-Control-*`）。**有意的契约变更**：既有 HTTP 测试由「无鉴权直连 200」
  改为显式带 token —— 这是安全修复改变了契约，不是测试被删或被跳过。

### 正确性修复（P1）

- **P1-1 · 多显示器视觉通道**（审查 Bug#1）：真机接线把主屏口径传给视觉通道的
  `full_screen_size`，抓帧却抓整个虚拟桌面 —— 多显示器下每次视觉定位入口即
  `LOCATE_FAILED`（单屏两口径恰好重合所以测不出）。接线统一为**抓帧口径**
  （`uia/live.py` 用 `current_virtual_desktop()`；`e2e/visual.py` 的
  `VisualChannel` 新增显式参数，缺省运行时量虚拟桌面）。新增合成双屏单测。
- **P1-2 · BGRA 帧标记**（审查 Bug#2）：`draw_stars` 按 4 字节切片赋 3 字节颜色，
  BGRA 帧整帧损坏（bytearray 切片赋值会收缩）。颜色按通道数构造（alpha=255）+
  等长防御。新增 marker 单测 6 条（先红后绿）。
- **P1-3 · 审批分类纳入真实元素**（审查安全#2）：分类原来只扫模型自报措辞 ——
  写「确定」实际点「删除」零审批。现在 UIA **真实控件名与窗口标题**一起进分类
  文本；COMMAND_LINE 词表补高危命令形态（`rm -`、`shutdown`、`taskkill` 等）。
  **契约变更**：非交互前端（HTTP / MCP 动作程序）的键盘输入（type_text /
  press_key）**一律拒绝**（`injection_blocked`，连审批都不问）；交互前端不变。
- **P1-4 · PII 脱敏与落盘**（审查安全#3#4#5）：脱敏正则新增手机号 / 身份证 /
  邮箱 / 银行卡四类（与密钥类同一套规则）；`observe()` 的整屏 OCR 全文**默认
  脱敏后**才回灌/落盘（`MINICODE_OCR_REDACT=0` 显式关闭）；decision-report.json
  落盘前过 `sanitize_record`（与动作记录同一套）。隐私策略文档按生成器重新生成。
- **P1-5 · 标定恒真判定**（审查 Bug#4，**冻结原型 coord/ 的唯一例外**，单列）：
  `CandidateResult.acceptable` 原实现恒为 True（`<= 0.0 or True`），且全仓零
  调用方。改为显式容差判定 `acceptable(tolerance_px)`。理由与验证见该 commit。

### 性能（P2，均含改前/改后量化）

- **传输层 keep-alive**（性能#1）：`urllib` 每请求新建 TCP+TLS → `http.client`
  长连接复用 + 陈旧连接重建重试。量化：实测端点 TCP+TLS 握手中位 **459 ms/请求**
  （本机回环差值 0.03 ms 为诚实下界）。
- **帧级编码缓存**（性能#2）：上下文里不变的帧每轮重新「缩放+PNG+base64」→
  按帧对象身份 memo（每轮裁剪，不无界增长）。量化：2560×1600→1280×800 全量
  编码中位 **438.4 ms/帧**（同机 5 次取中位），命中后 ≈0.002 ms/帧；
  3 张不变帧每轮省约 **1.32 s** 纯 CPU。

### 工程化

- **CI 上线**（评测#6）：`.github/workflows/ci.yml` —— windows-latest + Python 3.11，
  每次 push/PR 跑全量 pytest + ruff。README 的「Tests 1326 passed」静态徽章
  （人工数 `def test_` 的结果，任何提交即失真）换成 **CI 状态徽章**；数字墙与
  测试节不再写死人工计数。
- 删除死代码 `scripts/make_benchmark_chart.py`（它生成的图在 v0.1.3 已删，
  且依赖项目未安装的 matplotlib）。

### 对外表述修正（逐条可核对，未新增任何数字）

- README 数字墙 59.02% 加脚注：该数字为**点式定位路径**（模型直接输出坐标 + 运行时
  标定，`eval/sspro.py` 未传候选源）的成绩；编号候选分支仅在 42 条子样本上量过，
  未在同口径 183 条上复测。依据：审查报告评测方法论 #2。
- README 新增「评测配置」节：判定口径（ScreenSpot-Pro 官方 bbox 命中）、样本量与
  抽样方式（计划 330，本地缺图静默过滤后实际 183，非随机子集）、调参集即评测集
  的披露（超参同批扫描后报同批分数，绝对值带乐观偏差）、以及「该批分数所用模型
  与端点未记录」的复现缺口声明。依据：审查报告评测方法论 #1/#3/#5。
- **删除「同配置两次独立跑批差 ±5.46 pp（mid-p p=0.0989）」**：该数字只存在于
  README 正文，仓库内没有任何产物可复算它（配对明细 JSON 不在库，按 b+c=10 的
  任何组合也复算不出 p=0.0989）。规则：无产物支撑的数字不上 README；
  待配对明细入库后恢复这句话。依据：审查报告评测方法论 #4。
- English Summary 同步补点式路径 caveat 与复现缺口声明。

## v0.1.4 — 2026-10-26

纯文档与视觉资产变更，不含任何代码能力变更（`src/` 零改动，实测数字不变）。

### 变更

- README 改用「产品落地页」版式：首屏 `<picture>` 明暗双版头图、三张能力卡片、
  一眼看完的数字墙、三步上手；正文按「实测 → 短板 → 架构 → 定位差异 → 安装 / 配置 / 用法
  → 目录结构 / 测试 / 隐私」组织，去掉冗长目录与附录说明。
- **全部配图改为手写矢量 SVG**，图片总体积从 2.7 MB 降到约 19 KB：
  `hero-light.svg` / `hero-dark.svg`（首屏，暗色模式自动切换）、`arch.svg`（六层架构图，
  取代原先会横向溢出、文字折行的 mermaid 块）、`chart-overall.svg` / `chart-category.svg` /
  `chart-platform.svg`（数据图）。
- 数据图改由 `scripts/make_figures_svg.py` 生成：纯 Python 拼 SVG，无绘图库依赖；
  数字写死在脚本并逐条对照 `runs/v2-eval-2016-control/sspro-report.json`。
  原 matplotlib 版 `scripts/make_figures.py` 与对应 PNG 删除。
- 配置节从四个并列的 `.env` 代码块压成「一个例子 + 一张端点对照表 + 折叠补充」；
  目录结构由纯文本代码块改为「路径 / 职责」两列表格；删除面向维护者的「附注」节。
- `docs/assets/social-preview.png` 重制为 1280×640、190 KB（GitHub Social preview 有 1 MB
  上限，原图 1.38 MB 传不上去）。

## v0.1.3 — 2026-10-26

纯文档与视觉资产变更，不含任何代码能力变更（src/ 零改动，实测数字不变）。

### 变更

- README 从零重构：第一屏（项目名 + 英文副标题 + 徽章行 + 头图 + 电梯陈述 + 实测数字摘要表）
  → 分维度实测（三张数据图）→ 诚实短板 → 架构（mermaid + 层到包映射 + 三条关键设计 + ADR）
  → 定位差异 → 安装 / 配置 / 用法 → 目录结构 / 测试 / 隐私与安全 → English Summary，全程 TOC。
- 视觉系统统一重做（近黑底 + 青色 / 琥珀点缀）：`docs/assets/banner.png`（头图）、
  `workflow.png`（工作流概念图）、`architecture.png`（架构氛围图）、
  `social-preview.png`（GitHub Social preview 专用，网页设置里上传）；
  旧 `hero.png`、`benchmark.png` 删除。
- 三张真实数据图改由 `scripts/make_figures.py` 生成（`by-overall.png` 带 Wilson 95% CI 误差棒、
  `by-category.png`、`by-platform.png`），数据写死在脚本并标注来源字段，重跑可得同一张图。
- 三段本机真实终端输出移出 README 正文：正文只留真机最小闭环摘录，
  完整输出与采集命令集中在 `docs/EXAMPLES.md`。
- 徽章改为 shields.io 静态徽章（Python 3.11+ / License MIT / Release v0.1.3 / Platform Windows /
  Tests 1326 passed），无 CI、覆盖率、下载量徽章。

## v0.1.1 — 2026-10-26

纯文档与视觉资产变更，不改任何代码能力（实测数字仍是 v0.1.0 那一批）。

### 新增

- README 视觉资产：`docs/assets/hero.png`（首图）、`docs/assets/benchmark.png`
  （能力对比图，配 `scripts/make_benchmark_chart.py` 可复现）、
  `docs/assets/social-preview.png`（只用于 GitHub 仓库 Settings → Social preview，不进正文）。
- README 新增「层 → 包」映射表，mermaid 六层图与旧 ASCII 图合并为一节。

### 修复

- README 里两个不存在的图片引用（`docs/assets/cli-help.png`、`docs/assets/cli-real.png`）
  在 GitHub 上显示破图 → 删除，改为 `minicode --help`、`minicode action --fake --out runs`、
  真机 `minicode action --target "开始"` 三段本机真实终端输出（含落点坐标、回读偏差、
  落点复验、channel、model_calls）。
- README 有两个同名「架构」节 → 合并为一节（mermaid 图 → 层级表 → 三条关键设计），
  旧的 ASCII 层级图由新增的「层 → 包」映射表取代，两个 ADR 链接保留。
- README 里写死的版本号 v0.1.0 更新为 v0.1.1。

## v0.1.0 — 2026-10-26

首个可用版本：把内部验证过的原型整理成可安装、可配置、可评测的公开项目。

### 新增

- **模型客户端支持两种协议**：
  - `anthropic` —— Anthropic Messages 协议（原有实现，保持兼容）；
  - `openai` —— OpenAI 兼容 `/chat/completions`，覆盖 OpenAI、DeepSeek、Moonshot、智谱、硅基流动、
    DashScope compatible-mode、Ollama、vLLM、LM Studio、OpenRouter、OneAPI 等。
  - 请求体形状、响应解析（含跳过 `reasoning_content`）、用量字段、图像 token 反查都按协议分支；
    图像 token 算不出来时如实返回 `None`，不猜。
- **配置键**：`MINICODE_PROVIDER` / `MINICODE_BASE_URL` / `MINICODE_API_KEY` / `MINICODE_MODEL`；
  旧键 `MINICODE_ANTHROPIC_BASE_URL` / `MINICODE_ANTHROPIC_API_KEY` 继续可用（新键优先）。
  启动时在 stderr 打印实际生效的协议、端点与键来源，`api_key` 脱敏。
- **命令行总入口** `minicode`：capability / calib / ground / action / eval / e2e / serve / mcp。
- 对外文档：[README](README.md)、[ARCHITECTURE](ARCHITECTURE.md)、MIT [LICENSE](LICENSE)、
  [.env.example](.env.example)。

### 实测能力（v0.1.0 口径）

- ScreenSpot-Pro 183 条抽样：**59.02%**（108/183），Wilson 95% CI [51.78%, 65.89%]；
  其中文本类 69.79%（67/96）、图标类 47.13%（41/87）。
- 真机端到端：wave1 **3/5**（60%）、wave2 **3/9**（33.3%）。
- UIA 通道控件树唯一命中时：落点误差 **0.0 px**、模型调用 **0 次**。
- 视觉通道准入标定：6/6 会话可用、96 px 方块命中率 1.00。

### 变更与移除

- 移除「`base_url` 必须以 `/go` 结尾」这条内部网关限制：端点改由协议决定
  （anthropic → `/v1/messages`，openai → `/chat/completions`）。
- `MINICODE_SESSION_ID` / `MINICODE_HTTP_USER_AGENT` / `MINICODE_HTTP_CLIENT` 由必填改为可选
  （只有网关类端点需要它们，通用 provider 不再被绑死）。
- 移除内部过程文档（调研底稿、内部评审、工单、agent 协作规范）、内部提示词包，及其配套的
  「引用纪律」内部运维测试；保留 8 条 ADR、产品规格与隐私政策。
- 全历史密钥扫描通过：仓库里没有任何真实密钥（示例一律 `<your-key>`）。

## 更早

v0.1.0 之前的 174 个 commit 是内部开发过程，不作为发行版对外。

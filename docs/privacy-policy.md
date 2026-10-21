# 隐私策略（工单 M20）

> ⚠️ **本文件由 `python -m minicode.privacy` 从实现里生成 —— 不要手改。**
> 每一行都指向实现里的一个位置（末列），可以逐条对照（工单 M20 AC 3）。
> 改了实现里的规则，重跑那个命令就能把本文更新到一致。
>
> 涉及**脱敏**的条款引用的是脱敏器自己描述的那份边界
> （`minicode.redact.redactor:redaction_boundary`）—— **同一处规则，不写两套**
> （工单 M20 AC 4）。

## 1. 送进模型的内容

| 条款 | 说明 | 实现位置 |
|---|---|---|
| **控件树（脱敏后）** | 模型侧文本只有一份出口（抬头「控件树（已脱敏）」）：白名单字段 + 正则脱敏 + 密码框占位符 + 正文类控件的值不外发。脱敏没上线 → 这一条通道被拒绝启用，**一个字符都不给**。 | `minicode.redact.redactor:TreeRedactor.render` |
| **截图（那一帧画面）** | 视觉通道把整屏帧降采样成粗定位图、把裁剪窗与放大的区域图送给模型；裁剪与放大都从**原始物理分辨率**的帧上取景。 | `minicode.ground.channel:TwoStageGrounder` |
| **每步回显** | 回显给模型的是**它自己发出的东西**（坐标 / 消歧行号）+ 实际发送的图片尺寸。 | `minicode.action.ports:GroundingEcho` |

## 2. 不送的内容

| 条款 | 说明 | 实现位置 |
|---|---|---|
| **密码** | 密码框（IsPassword=True）的值一律是占位符 `***`，明文不外发。 | `minicode.redact.redactor:TreeRedactor.render` |
| **草稿正文 / 正文类控件的值** | 正文类控件（edit, document, text）的值一律不外发（聊天草稿、编辑框正文都在这一类）。 | `minicode.redact.redactor:TreeRedactor.render` |
| **密钥形态的片段** | 5 类敏感模式一律换成占位符 `[REDACTED]`：私钥头、密钥前缀形态、JWT 三段、长十六进制串、长 base64。 | `minicode.redact.patterns:redact_secrets` |
| **控件树里未登记的字段** | fail-closed：未登记的原始属性连**字段名**都不出现。 | `minicode.redact.redactor:TreeRedactor.render` |
| **敏感窗口的画面** | 黑名单窗口（标题命中敏感词表或对话框类名）**根本不许拍**：抓帧之前就被拦下，画面不进模型上下文，动作记录里只记「这个窗口被跳过了」。这一层在**审批之前**跑 —— 命中就**根本走不到**审批那一层。 | `minicode.privacy.guard:SensitiveWindowGuard.check` |

## 3. 落盘的内容

| 条款 | 说明 | 实现位置 |
|---|---|---|
| **截图只落哈希** | 落盘的是截图哈希（SHA-256 十六进制），**不落原图**；抓图尺寸与图像来源一起落。 | `minicode.action.records:frame_fields` |
| **动作记录** | 动作种类、目标、控件框、落点（物理表面空间）、截图哈希、错误码、三段耗时与三类失败；**输入文本的正文不落**。 | `minicode.action.records:ActionRecord.to_dict` |
| **落盘前脱敏** | 落盘前用**脱敏器同一套规则**把密钥形态的明文换成占位符；摘要字段（截图哈希）放行（它是摘要，不是明文）。 | `minicode.redact.log:sanitize_record` |
| **被敏感窗口策略拦下** | 黑名单窗口被拦下时，动作记录里记一笔（窗口标题 + 命中的判据 + 判据来源）；**画面一个字都不落**。 | `minicode.action.records:build_record` |

## 4. 敏感窗口黑名单（发送前）

* **标题命中敏感词表**（命中即拉黑）：`密码管理器`、`密码保险箱`、`密码库`、`password manager`、`1password`、`bitwarden`、`lastpass`、`keepass`、`dashlane`、`银行`、`网银`、`网上银行`、`手机银行`、`bank`、`支付`、`付款`、`收银台`、`结账`、`payment`、`checkout`、`paypal`、`凭据`、`口令`、`credential`、`解锁`、`unlock`。
* **对话框类名**（精确匹配，不做前缀猜）：`#32770`、`credential dialog xaml host`、`credential dialog`、`credentialdialogxamlhost`。
* **取值来源**：工单 M20 AC 1 点名的「标题命中敏感词表或是对话框类名的窗口」（密码管理器 / 银行 / 支付 / 凭据四组，中英文；对话框类名含 Win32 标准对话框 #32770 与 Windows 凭据对话框）；**工程阈值·未验证**（没有语料统计过误报率）。
* 被拉黑的窗口**根本不许拍、不许发**：抓帧之前就被拦下，画面不进模型上下文，动作记录里记一笔「该窗口被跳过」。

### 4.1 两层闸门：黑名单先行、审批二次确认

| 条款 | 说明 | 实现位置 |
|---|---|---|
| **第一层：黑名单先行（硬闸门）** | 敏感窗口黑名单命中 → **在抓帧之前**就拒绝：零抓帧、零像素产生，动作记录里如实记「被敏感窗口策略拦下」（错误码 `sensitive_window_blocked`）。判的是**这一帧里看得见的全部窗口**。 | `minicode.privacy.guard:SensitiveWindowGuard.check` |
| **第二层：审批二次确认（M16）** | 黑名单**没**命中、但请求属于十一类里的「截含敏感窗口的屏」→ 仍然产生审批请求（带意图 + 目标）；**没批则零动作下发**（错误码 `approval_denied`）。判的是**当前前台窗口**，判据来源就是第一层那份黑名单（M16 的 `SensitiveWindowPolicy` 端口由它提供）—— **词表只有一份**。 | `minicode.approval.gate:ApprovalGate.check_capture` |

## 5. 剪贴板

| 条款 | 说明 | 实现位置 |
|---|---|---|
| **读剪贴板要审批** | 读剪贴板内容前产生审批请求（类别 = 读剪贴板，与危险动作审批**同一套机制**）；**未获批不读取**，改走 Unicode 注入通道（压根不碰剪贴板），并记一笔退化。 | `minicode.action.executor:ActionExecutor._type` |
| **剪贴板用完要还原** | 粘贴通道把剪贴板内容备份下来、用完**原样写回**（finally 路径）；剪贴板里有非文本内容时压根不走粘贴通道。 | `minicode.action.executor:ActionExecutor._type` |

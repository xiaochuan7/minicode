"""**隐私策略**（工单 M20）。

三件事在这里成文，且只在这里成文：

1. **敏感窗口黑名单**（发送前）：标题命中敏感词表 **或** 对话框类名的窗口
   **根本不许拍、不许发** —— 它的画面不进模型上下文（工单 M20 AC 1）。
2. **送模型 / 不送 / 落盘** 三组条款：每一条都指向实现里的一个位置
   （:attr:`PolicyClause.implementation`），可以逐条对照（工单 M20 AC 3）。
3. **剪贴板**：读剪贴板内容要审批，与危险动作审批**同一套机制**
   （工单 M20 AC 2；机制由 M16 给，策略由本单给）。

⚠️ **规则只有一份**（工单 M20 AC 4）：涉及脱敏的条款**直接引用脱敏器自己描述的边界**
（:func:`minicode.redact.redactor.redaction_boundary` ——
谁定义规则，谁描述规则）——
文档里写的边界就是脱敏器的实际行为，不是另抄一份。

⚠️ **这一层与 M16 的分工**：M16 只给**判据的位置**
（``minicode.approval.ports.SensitiveWindowPolicy`` 端口）与**审批请求的生成**）；
**哪些窗口算敏感由本单的黑名单决定**（``docs/tickets/README.md`` 的口径）。
本单的黑名单是**硬闸门**（根本不许拍），M16 的机制是**人工放行**（批了就拍）——
两者互斥，因此本单的黑名单**故意不接**进 M16 的闸门
（接上会出现「人点了头就能拍敏感窗口」的路）。见 ``docs/reviews/M20.md`` §5 ①。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from minicode.approval.ports import WindowInfo
from minicode.redact.redactor import redaction_boundary

__all__ = [
    "SENSITIVE_TITLE_KEYWORDS",
    "SENSITIVE_WINDOW_CLASSES",
    "SENSITIVE_WINDOW_SOURCE",
    "SensitiveWindowMatch",
    "SensitiveWindowBlacklist",
    "PolicyClause",
    "PrivacyPolicy",
    "default_policy",
]

#: **脱敏的边界**（由脱敏器自己描述 —— 谁定义规则，谁描述规则）。
#:
#: ⚠️ 本模块**不自己写**脱敏规则，也不自己写那两个占位符：
#: 它**引用** :func:`minicode.redact.redactor.redaction_boundary` 给的那一份
#: （工单 M20 AC 4：同一处规则，不写两套）。
_BOUNDARY = redaction_boundary()


#: **敏感词表**（窗口标题命中即拉黑）。
#:
#: 分四组：密码管理器 / 银行 / 支付 / 凭据。
#: ⚠️ **中英文都要有**：应用界面与窗口标题两种都有，只收中文等于放行英文的同类窗口。
#: ⚠️ **词表是工程阈值·未验证**：没有语料统计过误报率（命中即拉黑 = 宁可不做）。
SENSITIVE_TITLE_KEYWORDS: tuple[str, ...] = (
    # 密码管理器
    "密码管理器",
    "密码保险箱",
    "密码库",
    "password manager",
    "1password",
    "bitwarden",
    "lastpass",
    "keepass",
    "dashlane",
    # 银行
    "银行",
    "网银",
    "网上银行",
    "手机银行",
    "bank",
    # 支付
    "支付",
    "付款",
    "收银台",
    "结账",
    "payment",
    "checkout",
    "paypal",
    # 凭据
    "凭据",
    "口令",
    "credential",
    "解锁",
    "unlock",
)

#: **对话框类名**（窗口类名命中即拉黑）。
#:
#: * ``#32770`` —— Win32 **标准对话框**的类名（所有标准对话框都是它）；
#: * 凭据对话框（Windows 自带的凭据输入框）的那几个类名。
#:
#: ⚠️ **精确匹配**（不做前缀猜）：``#3277`` 不是 ``#32770``。
SENSITIVE_WINDOW_CLASSES: tuple[str, ...] = (
    "#32770",
    "credential dialog xaml host",
    "credential dialog",
    "credentialdialogxamlhost",
)

#: 整份黑名单的**取值来源**（随判定落盘，仓库规矩：词表要能说清词是哪来的）。
SENSITIVE_WINDOW_SOURCE = (
    "工单 M20 AC 1 点名的「标题命中敏感词表或是对话框类名的窗口」"
    "（密码管理器 / 银行 / 支付 / 凭据四组，中英文；"
    "对话框类名含 Win32 标准对话框 #32770 与 Windows 凭据对话框）"
    "；**工程阈值·未验证**（没有语料统计过误报率）"
)


@dataclass(frozen=True)
class SensitiveWindowMatch:
    """一次命中：**哪条判据**命中了哪个窗口。"""

    #: 判据类别：``"title_keyword"``（标题命中词表）/ ``"window_class"``（类名命中）。
    kind: str
    #: 命中的那一条判据（词 / 类名）—— 人读。
    value: str
    #: 窗口标题（**身份**，不是内容）。
    window_title: str = ""
    #: 窗口类名。
    window_class: str = ""
    #: 判据的取值来源。
    source: str = ""

    def describe(self) -> str:
        """人读的一句话（进错误消息与动作记录）。"""
        if self.kind == "window_class":
            return f"窗口类名命中敏感类名「{self.value}」（对话框类名）"
        return f"窗口标题命中敏感词表「{self.value}」"

    def to_dict(self) -> dict[str, str]:
        return {
            "kind": self.kind,
            "matched": self.value,
            "window_title": self.window_title,
            "window_class": self.window_class,
            "source": self.source,
        }


@dataclass(frozen=True)
class SensitiveWindowBlacklist:
    """**敏感窗口黑名单**（工单 M20 AC 1）。

    判据只有两条，**命中即生效**（fail-closed）：

    * **标题命中敏感词表**（:data:`SENSITIVE_TITLE_KEYWORDS`）；
    * **窗口类名命中对话框类名**（:data:`SENSITIVE_WINDOW_CLASSES`，精确匹配）。

    ⚠️ **不接** M16 的 ``SensitiveWindowPolicy`` 端口 —— 本单是**硬闸门**
    （根本不许拍），M16 是**人工放行**（批了就拍）；接上会出现
    「人点了头就能拍敏感窗口」的路，与工单 M20 AC 1 互斥。见模块说明。
    """

    title_keywords: tuple[str, ...] = SENSITIVE_TITLE_KEYWORDS
    window_classes: tuple[str, ...] = SENSITIVE_WINDOW_CLASSES
    #: 这份词表的**取值来源**（随判定落盘）。
    word_list_source: str = SENSITIVE_WINDOW_SOURCE

    def match(self, window: WindowInfo) -> SensitiveWindowMatch | None:
        """这个窗口命中了吗（没命中返回 ``None``）。"""
        title = (window.title or "").lower()
        for keyword in self.title_keywords:
            if keyword.lower() in title:
                return SensitiveWindowMatch(
                    kind="title_keyword",
                    value=keyword,
                    window_title=window.title or "",
                    window_class=window.class_name or "",
                    source=self.word_list_source,
                )
        class_name = (window.class_name or "").strip().lower()
        for candidate in self.window_classes:
            if candidate.lower() == class_name:
                return SensitiveWindowMatch(
                    kind="window_class",
                    value=candidate,
                    window_title=window.title or "",
                    window_class=window.class_name or "",
                    source=self.word_list_source,
                )
        return None

    def source(self) -> str:
        """词表的取值来源（随判定落盘，仓库规矩：词表要能说清词是哪来的）。"""
        return self.word_list_source


@dataclass(frozen=True)
class PolicyClause:
    """策略文档里的**一条**。

    :attr:`implementation` 指向实现里的一个位置（``模块:符号``）——
    文档里写的每一条都能在实现里找到对应行为（工单 M20 AC 3）。
    """

    #: 机器可读的键（同一份策略里唯一）。
    key: str
    #: 人读的标题。
    title: str
    #: 人读的说明。
    detail: str
    #: 实现位置（``模块:符号``）。
    implementation: str


@dataclass(frozen=True)
class PrivacyPolicy:
    """**隐私策略**（一份）。"""

    #: 敏感窗口黑名单（发送前）。
    sensitive_windows: SensitiveWindowBlacklist = field(
        default_factory=SensitiveWindowBlacklist
    )
    #: **送进模型**的内容范围。
    sent_to_model: tuple[PolicyClause, ...] = ()
    #: **不送**的内容。
    withheld: tuple[PolicyClause, ...] = ()
    #: **落盘**的内容。
    persisted: tuple[PolicyClause, ...] = ()
    #: **剪贴板**的规则。
    clipboard: tuple[PolicyClause, ...] = ()


def _sent_clauses() -> tuple[PolicyClause, ...]:
    """送进模型的内容范围。"""
    return (
        PolicyClause(
            key="model_side_tree",
            title="控件树（脱敏后）",
            detail=(
                f"模型侧文本只有一份出口（抬头「{_BOUNDARY['model_side_header']}」）："
                "白名单字段 + 正则脱敏"
                " + 密码框占位符 + 正文类控件的值不外发。"
                "脱敏没上线 → 这一条通道被拒绝启用，**一个字符都不给**。"
            ),
            implementation="minicode.redact.redactor:TreeRedactor.render",
        ),
        PolicyClause(
            key="frame",
            title="截图（那一帧画面）",
            detail=(
                "视觉通道把整屏帧降采样成粗定位图、把裁剪窗与放大的区域图送给模型；"
                "裁剪与放大都从**原始物理分辨率**的帧上取景。"
            ),
            implementation="minicode.ground.channel:TwoStageGrounder",
        ),
        PolicyClause(
            key="echo",
            title="每步回显",
            detail=(
                "回显给模型的是**它自己发出的东西**（坐标 / 消歧行号）"
                "+ 实际发送的图片尺寸。"
            ),
            implementation="minicode.action.ports:GroundingEcho",
        ),
    )


def _withheld_clauses() -> tuple[PolicyClause, ...]:
    """不送进模型的内容。"""
    return (
        PolicyClause(
            key="password",
            title="密码",
            detail=(
                "密码框（IsPassword=True）的值一律是占位符 "
                f"`{_BOUNDARY['password_placeholder']}`，明文不外发。"
            ),
            implementation="minicode.redact.redactor:TreeRedactor.render",
        ),
        PolicyClause(
            key="body_text",
            title="草稿正文 / 正文类控件的值",
            detail=(
                "正文类控件（"
                + ", ".join(_BOUNDARY["body_control_types"])
                + "）的值一律不外发（聊天草稿、编辑框正文都在这一类）。"
            ),
            implementation="minicode.redact.redactor:TreeRedactor.render",
        ),
        PolicyClause(
            key="secrets",
            title="密钥形态的片段",
            detail=(
                f"{len(_BOUNDARY['secret_pattern_labels'])} 类敏感模式一律换成占位符 `"
                + _BOUNDARY["secret_placeholder"]
                + "`："
                + "、".join(_BOUNDARY["secret_pattern_labels"])
                + "。"
            ),
            implementation="minicode.redact.patterns:redact_secrets",
        ),
        PolicyClause(
            key="unknown_fields",
            title="控件树里未登记的字段",
            detail="fail-closed：未登记的原始属性连**字段名**都不出现。",
            implementation="minicode.redact.redactor:TreeRedactor.render",
        ),
        PolicyClause(
            key="sensitive_window",
            title="敏感窗口的画面",
            detail=(
                "黑名单窗口（标题命中敏感词表或对话框类名）**根本不许拍**："
                "抓帧之前就被拦下，画面不进模型上下文，"
                "动作记录里只记「这个窗口被跳过了」。"
            ),
            implementation="minicode.privacy.guard:SensitiveWindowGuard.check",
        ),
    )


def _persisted_clauses() -> tuple[PolicyClause, ...]:
    """落盘的内容。"""
    return (
        PolicyClause(
            key="screenshot_hash",
            title="截图只落哈希",
            detail=(
                "落盘的是截图哈希（SHA-256 十六进制），**不落原图**；"
                "抓图尺寸与图像来源一起落。"
            ),
            implementation="minicode.action.records:frame_fields",
        ),
        PolicyClause(
            key="action_record",
            title="动作记录",
            detail=(
                "动作种类、目标、控件框、落点（物理表面空间）、截图哈希、错误码、"
                "三段耗时与三类失败；**输入文本的正文不落**。"
            ),
            implementation="minicode.action.records:ActionRecord.to_dict",
        ),
        PolicyClause(
            key="redact_on_disk",
            title="落盘前脱敏",
            detail=(
                "落盘前用**脱敏器同一套规则**把密钥形态的明文换成占位符；"
                "摘要字段（截图哈希）放行（它是摘要，不是明文）。"
            ),
            implementation="minicode.redact.log:sanitize_record",
        ),
        PolicyClause(
            key="skipped_window",
            title="敏感窗口被跳过",
            detail=(
                "黑名单窗口被跳过时，动作记录里记一笔（窗口标题 + 命中的判据 + "
                "判据来源）；**画面一个字都不落**。"
            ),
            implementation="minicode.action.records:build_record",
        ),
    )


def _clipboard_clauses() -> tuple[PolicyClause, ...]:
    """剪贴板的规则。"""
    return (
        PolicyClause(
            key="clipboard_read",
            title="读剪贴板要审批",
            detail=(
                "读剪贴板内容前产生审批请求（类别 = 读剪贴板，与危险动作审批"
                "**同一套机制**）；**未获批不读取**，改走 Unicode 注入通道"
                "（压根不碰剪贴板），并记一笔退化。"
            ),
            implementation="minicode.action.executor:ActionExecutor._type",
        ),
        PolicyClause(
            key="clipboard_restore",
            title="剪贴板用完要还原",
            detail=(
                "粘贴通道把剪贴板内容备份下来、用完**原样写回**（finally 路径）；"
                "剪贴板里有非文本内容时压根不走粘贴通道。"
            ),
            implementation="minicode.action.executor:ActionExecutor._type",
        ),
    )


def default_policy() -> PrivacyPolicy:
    """**默认的隐私策略**（仓库那一份）。

    条款里的脱敏边界**引用脱敏器自己的常量** —— 文档与实现因此不会各说各话
    （工单 M20 AC 4）。
    """
    return PrivacyPolicy(
        sensitive_windows=SensitiveWindowBlacklist(),
        sent_to_model=_sent_clauses(),
        withheld=_withheld_clauses(),
        persisted=_persisted_clauses(),
        clipboard=_clipboard_clauses(),
    )

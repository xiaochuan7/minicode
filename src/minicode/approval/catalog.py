"""**危险动作清单**（工单 M16 AC 1）。

工单点名的十一类：删除、支付、发消息、注册、改系统设置、运行命令行、
安装软件、改注册表、提权弹窗、读剪贴板、截含敏感窗口的屏。

**每类在执行前都要产生审批请求** —— 不存在静默执行的类别。
这张表是**判定「一件事属于哪一类」的唯一真值来源**：
分类只做**命中即生效**（fail-closed），不做语义理解。

## 与 M12 那份词表的关系

M12 的**不可逆词表**（:data:`minicode.reflection.keywords.IRREVERSIBLE_KEYWORDS`）
是本表的**子集** —— 发消息 / 支付 / 删除三类既是不可逆（不许自动重试），
也是危险（要人工审批）。单测 ``approval/test_approval.py`` 把这个包含关系
钉死：M12 的词落进本表时，本表的判定必须更严（同样命中 → 同样要审批），
不会出现「一个判危险、另一个判不危险」。

## 优先级

清单里每一类都带一份**关键词**；**同一段文字命中多类时按本表的顺序取第一个**
（:data:`DANGER_RULES` 的顺序即优先级）。顺序不是随意的：

* **改注册表**排在**注册**前面 —— 「注册表」里含「注册」；
* **安装软件**排在**运行命令行**前面 —— 「用 winget 安装软件」两样都沾；
* **运行命令行**排在**改系统设置**前面 —— 「在系统设置里运行命令」两样都沾。

## 关键词判据的边界

* **关键词是兜底，不是全部**：「提权弹窗」与「读剪贴板」的**主判据是状态**
  （检测到提权 / 安全桌面；读剪贴板这个动作本身）—— 关键词只是补充
  （模型写「以管理员身份运行」「读剪贴板」时也要拦得住）。
* **中英文都要有**：模型有时写英文意图（"click the Delete button"），
  只收中文就等于**放行英文的同类动作** —— 那是静默执行。
* ⚠️ **M12 的 `reflection.gate.DangerGate` 已被本清单取代**
  （工单 M16 落地）：**新代码不要再接它** —— 两套判据并存会各说各话。
  它的词表是本表的**子集**（单测 `approval/test_approval.py` 把住这个关系），
  所以它只会**更宽松**，不会更严。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from minicode.reflection.keywords import IRREVERSIBLE_KEYWORDS_SOURCE

__all__ = [
    "DangerCategory",
    "DangerRule",
    "DangerMatch",
    "DANGER_RULES",
    "CATEGORY_TITLES",
    "DANGER_CATALOG_SOURCE",
    "classify",
    "match_category",
]


class DangerCategory(StrEnum):
    """**危险动作的类别**（工单 M16 AC 1 点名的十一类）。"""

    #: 删除。
    DELETE = "delete"
    #: 支付。
    PAYMENT = "payment"
    #: 发消息。
    SEND_MESSAGE = "send_message"
    #: 注册。
    REGISTER = "register"
    #: 改系统设置。
    SYSTEM_SETTINGS = "system_settings"
    #: 运行命令行。
    COMMAND_LINE = "command_line"
    #: 安装软件。
    INSTALL_SOFTWARE = "install_software"
    #: 改注册表。
    REGISTRY = "registry"
    #: 提权弹窗（**由状态触发**：检测到提权 / 安全桌面）。
    ELEVATION = "elevation"
    #: 读剪贴板（**由动作触发**：读剪贴板内容这件事本身）。
    CLIPBOARD_READ = "clipboard_read"
    #: 截含敏感窗口的屏（**由状态触发**：前台窗口命中敏感词表）。
    SENSITIVE_SCREENSHOT = "sensitive_screenshot"


#: 每一类的**人读名**（审批请求与日志里给人看的）。
CATEGORY_TITLES: dict[DangerCategory, str] = {
    DangerCategory.DELETE: "删除",
    DangerCategory.PAYMENT: "支付",
    DangerCategory.SEND_MESSAGE: "发消息",
    DangerCategory.REGISTER: "注册",
    DangerCategory.SYSTEM_SETTINGS: "改系统设置",
    DangerCategory.COMMAND_LINE: "运行命令行",
    DangerCategory.INSTALL_SOFTWARE: "安装软件",
    DangerCategory.REGISTRY: "改注册表",
    DangerCategory.ELEVATION: "提权弹窗",
    DangerCategory.CLIPBOARD_READ: "读剪贴板",
    DangerCategory.SENSITIVE_SCREENSHOT: "截含敏感窗口的屏",
}


@dataclass(frozen=True)
class DangerRule:
    """一类的判定规则：命中哪些词 + 这些词是哪来的。"""

    category: DangerCategory
    keywords: tuple[str, ...]
    #: **取值来源**（随判定落盘 —— 仓库规矩：词表要能说清词是哪来的）。
    source: str


#: **危险动作清单**（唯一真值来源）。顺序 = 优先级。
DANGER_RULES: tuple[DangerRule, ...] = (
    DangerRule(
        category=DangerCategory.REGISTRY,
        keywords=(
            "注册表",
            "registry",
            "regedit",
            "reg add",
            "reg delete",
            "hkey_",
        ),
        source="工单 M16 AC 1 点名的「改注册表」+ 同义说法",
    ),
    DangerRule(
        category=DangerCategory.INSTALL_SOFTWARE,
        keywords=(
            "安装",
            "卸载",
            "install",
            "uninstall",
            "installer",
            "setup.exe",
            "msiexec",
            "安装包",
        ),
        source="工单 M16 AC 1 点名的「安装软件」+ 同义说法",
    ),
    DangerRule(
        category=DangerCategory.COMMAND_LINE,
        keywords=(
            "命令行",
            "命令提示符",
            "运行命令",
            "执行命令",
            "command line",
            "powershell",
            "cmd.exe",
            "cmd /",
            "shell",
            # 审查安全 #2：**输入正文里的高危命令形态**——这些词不是「意图措辞」，
            # 是防 ``type_text("rm -rf C:\\")`` 这类零审批键盘注入的。
            # 只收高信号形态（"rm -" 而不是 "rm "，免得误伤 warm/arm 一类英文词）。
            "rm -",
            "rmdir",
            "deltree",
            "format c",
            "format d",
            "shutdown",
            "taskkill",
            "diskpart",
            "mkfs",
            "sudo",
        ),
        source=(
            "工单 M16 AC 1 点名的「运行命令行」+ 同义说法；"
            "审查安全#2 补输入正文里的高危命令形态"
        ),
    ),
    DangerRule(
        category=DangerCategory.SYSTEM_SETTINGS,
        keywords=(
            "系统设置",
            "控制面板",
            "系统属性",
            "system settings",
            "control panel",
            "防火墙",
            "默认浏览器",
            "开机启动",
            "开机自启",
            "系统更新",
            "缩放",
        ),
        source="工单 M16 AC 1 点名的「改系统设置」+ 同义说法",
    ),
    DangerRule(
        category=DangerCategory.PAYMENT,
        keywords=(
            "支付",
            "付款",
            "购买",
            "下单",
            "转账",
            "充值",
            "扣款",
            "结账",
            "pay",
            "payment",
            "purchase",
            "checkout",
        ),
        source="工单 M16 AC 1 点名的「支付」+ 同义说法",
    ),
    DangerRule(
        category=DangerCategory.SEND_MESSAGE,
        keywords=(
            "发送",
            "发消息",
            "发表",
            "投递",
            "send",
            "post message",
        ),
        source="工单 M16 AC 1 点名的「发消息」+ 同义说法",
    ),
    DangerRule(
        category=DangerCategory.DELETE,
        keywords=(
            "删除",
            "删掉",
            "移除",
            "清空",
            "格式化",
            "delete",
            "remove",
            "erase",
            "format disk",
        ),
        source="工单 M16 AC 1 点名的「删除」+ 同义说法",
    ),
    DangerRule(
        category=DangerCategory.REGISTER,
        keywords=(
            "注册",
            "报名",
            "开通账号",
            "创建账号",
            "开户",
            "sign up",
            "signup",
            "register",
        ),
        source="工单 M16 AC 1 点名的「注册」+ 同义说法",
    ),
    DangerRule(
        category=DangerCategory.ELEVATION,
        keywords=(
            "提权",
            "管理员权限",
            "以管理员身份",
            "管理员运行",
            "uac",
            "runas",
            "elevate",
            "admin",
        ),
        source=(
            "工单 M16 AC 1 点名的「提权弹窗」"
            "（**主判据是状态**：检测到提权 / 安全桌面；关键词只是补充）"
        ),
    ),
    DangerRule(
        category=DangerCategory.CLIPBOARD_READ,
        keywords=("剪贴板", "clipboard"),
        source=(
            "工单 M16 AC 1 / AC 6 点名的「读剪贴板」"
            "（**主判据是动作**：读剪贴板内容这件事本身；关键词只是补充）"
        ),
    ),
)

#: 清单整体**取值来源**（随判定落盘）。
DANGER_CATALOG_SOURCE = (
    "工单 M16 AC 1 点名的十一类（删除 / 支付 / 发消息 / 注册 / 改系统设置 / "
    "运行命令行 / 安装软件 / 改注册表 / 提权弹窗 / 读剪贴板 / 截含敏感窗口的屏）"
    f"+ M12 不可逆词表（{IRREVERSIBLE_KEYWORDS_SOURCE}）；命中即生效（fail-closed）"
)


@dataclass(frozen=True)
class DangerMatch:
    """一次命中：哪一类 + 命中了哪个词 + 依据来自哪。"""

    category: DangerCategory
    title: str
    keyword: str
    source: str

    def to_dict(self) -> dict[str, str]:
        return {
            "category": self.category.value,
            "title": self.title,
            "keyword": self.keyword,
            "source": self.source,
        }


def match_category(
    *,
    intent: str = "",
    target_description: str = "",
    extra_text: str = "",
    real_element_text: str = "",
) -> DangerMatch | None:
    """这段文字命中了哪一类危险动作（没命中返回 ``None``）。

    **命中即生效**（fail-closed）：不判断语义、不判断轻重。

    ⚠️ ``real_element_text``（审查安全 #2）：**真实作用的元素**的文本
    （UIA 控件名 / 窗口标题）。模型自报的措辞可能与实际作用的元素不一致
    （目标描述写「确定」、实际点在名为「删除」的按钮上）——
    分类必须两个都看，**任何一边命中都要审批**。
    """
    text = f"{intent} {target_description} {extra_text} {real_element_text}".lower()
    if not text.strip():
        return None
    for rule in DANGER_RULES:
        for keyword in rule.keywords:
            if keyword.lower() in text:
                return DangerMatch(
                    category=rule.category,
                    title=CATEGORY_TITLES[rule.category],
                    keyword=keyword,
                    source=rule.source,
                )
    return None


def classify(
    *,
    intent: str = "",
    target_description: str = "",
    extra_text: str = "",
    real_element_text: str = "",
    category: DangerCategory | str | None = None,
) -> DangerMatch | None:
    """判定「这件事属于哪一类危险动作」。

    给了 ``category`` 就**直接认它**（由状态触发的三类：提权 / 读剪贴板 /
    截含敏感窗口的屏 —— 它们不由关键词判定）。
    """
    if category is not None:
        key = DangerCategory(category)
        return DangerMatch(
            category=key,
            title=CATEGORY_TITLES[key],
            keyword="（由状态触发）",
            source=DANGER_CATALOG_SOURCE,
        )
    return match_category(
        intent=intent,
        target_description=target_description,
        extra_text=extra_text,
        real_element_text=real_element_text,
    )

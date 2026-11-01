"""**不可逆动作判定**（护栏 3）：按「影响面」分类，而不是按措辞。

分类维度是**影响面**：这个动作做下去，世界上有什么东西会变、且变不回来。
与审批目录（:mod:`minicode.approval.catalog`）的关系：

* 审批目录回答「这是不是危险动作」（十一类，供 M16 审批闸门用）；
* 本模块回答「这个动作的影响面属哪一类」，并给出**授权级别 → 处置**的映射：
  L1（没审批人在场）→ **拒绝**；L2/L3 → L2 走审批、L3 记录放行。

⚠️ 词表与审批目录**同源复用**：重叠的类别（删除 / 支付 / 发消息 / 改系统设置）
直接映射审批目录的判定结果，本模块只补「影响面」特有的四类
（写文件 / 提交表单 / 关闭未保存窗口 / 发送）。词是**高信号形态**，
宁缺勿滥 —— 漏判由审批闸门兜底，误判会让 L1 干不了活。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from minicode.approval.catalog import DangerCategory, classify

from .levels import AuthLevel

__all__ = ["ImpactCategory", "ImpactVerdict", "classify_impact", "impact_verdict"]


class ImpactCategory(StrEnum):
    """动作的**影响面**分类（护栏 3 的判定维度）。"""

    #: 只读 / 可撤销（截图、悬停、滚动、等待——界面可恢复）。
    REVERSIBLE = "reversible"
    #: 写文件（保存 / 另存为 / 写入）。
    WRITE_FILE = "write_file"
    #: 发送（发消息 / 发邮件——出了本机就收不回）。
    SEND = "send"
    #: 删除（文件 / 内容 / 记录）。
    DELETE = "delete"
    #: 提交表单（下单 / 注册 / 确认——把数据交给别的系统）。
    SUBMIT_FORM = "submit_form"
    #: 关闭未保存窗口（丢工作成果）。
    CLOSE_UNSAVED = "close_unsaved"
    #: 支付 / 购买。
    PAYMENT = "payment"
    #: 改系统（注册表 / 系统设置 / 安装 / 命令行）。
    SYSTEM_CHANGE = "system_change"


#: **不可逆类别集合**：命中任意一类，L1 拒绝、L2 审批、L3 记录放行。
IRREVERSIBLE_CATEGORIES: frozenset[ImpactCategory] = frozenset(
    {
        ImpactCategory.WRITE_FILE,
        ImpactCategory.SEND,
        ImpactCategory.DELETE,
        ImpactCategory.SUBMIT_FORM,
        ImpactCategory.CLOSE_UNSAVED,
        ImpactCategory.PAYMENT,
        ImpactCategory.SYSTEM_CHANGE,
    }
)


@dataclass(frozen=True)
class ImpactVerdict:
    """影响面判定 + 授权级别给出的处置。"""

    #: 影响面类别。
    category: ImpactCategory
    #: 处置：``allow`` / ``approval_required`` / ``deny``。
    verdict: str
    #: 人读理由（进错误信息与记录）。
    reason: str = ""

    @property
    def irreversible(self) -> bool:
        return self.category in IRREVERSIBLE_CATEGORIES


# -- 影响面特有词表（审批目录没有的维度）---------------------------------
# 取值来源：W2 工单点名的影响面五类 + 同义高信号形态。顺序即优先级。

_WRITE_FILE_KEYWORDS: tuple[str, ...] = (
    "保存",
    "另存",
    "写入",
    "write ",
    "save ",
    "save as",
)

_SEND_KEYWORDS: tuple[str, ...] = (
    "发送",
    "发消息",
    "发邮件",
    "send ",
    "send message",
    "send email",
    "回复",
    "reply",
)

_DELETE_KEYWORDS: tuple[str, ...] = (
    "删除",
    "清空",
    "移除",
    "delete",
    "remove",
    "清回收站",
    "empty recycle",
)

_SUBMIT_KEYWORDS: tuple[str, ...] = (
    "提交",
    "下单",
    "确认订单",
    "submit",
    "checkout",
    "confirm order",
    # ⚠️ 只收「注册账号」这种高信号形态：「注册」单字会撞「注册表」
    # （后者归 SYSTEM_CHANGE，注册表比注册账号危险得多，不能被吃掉）。
    "注册账号",
    "注册用户",
)

_CLOSE_KEYWORDS: tuple[str, ...] = (
    "关闭",
    "退出",
    "close ",
    "quit ",
    "exit ",
)

#: 需要精确词边界的英文词（避免 ``close `` 误伤 ``closet`` 之类由本表自己引入
#: 的问题——中文没有这个词边界问题）。
_PAYMENT_KEYWORDS: tuple[str, ...] = ("支付", "转账", "购买", "付款", "pay ", "checkout")


def _hit(text: str, keywords: tuple[str, ...]) -> bool:
    return any(keyword in text for keyword in keywords)


def classify_impact(
    *,
    intent: str = "",
    target_description: str = "",
    params_text: str = "",
    real_element_text: str = "",
    action_kind: str = "",
) -> ImpactCategory:
    """按影响面分类（词表判定，全部文本拼接后匹配）。

    :param intent: 动作意图（模型的措辞）。
    :param target_description: 目标描述。
    :param params_text: 动作参数的可读串（如键盘输入的正文）。
    :param real_element_text: **真实控件名与窗口标题**（UIA 读出的事实，
        与审批目录同一条纪律：模型措辞可能与之不符，事实一起进判定）。
    :param action_kind: 动作种类（``"close"`` 类目前不在协议种类里，占位）。
    """
    text = " ".join(
        part.strip()
        for part in (intent, target_description, params_text, real_element_text)
        if part
    ).lower()
    if not text:
        return ImpactCategory.REVERSIBLE
    # 顺序即优先级：支付 > 删除 > 发送 > 提交 > 关闭 > 写文件 > 改系统。
    # ⚠️ 「关闭」在「写文件」之前：「关闭未保存的窗口」同时含「关闭」与
    # 「保存」，影响面按动作本体（关窗口）算，不按窗口标题里出现过的词算。
    if _hit(text, _PAYMENT_KEYWORDS):
        return ImpactCategory.PAYMENT
    if _hit(text, _DELETE_KEYWORDS):
        return ImpactCategory.DELETE
    if _hit(text, _SEND_KEYWORDS):
        return ImpactCategory.SEND
    if _hit(text, _SUBMIT_KEYWORDS):
        return ImpactCategory.SUBMIT_FORM
    if _hit(text, _CLOSE_KEYWORDS):
        return ImpactCategory.CLOSE_UNSAVED
    if _hit(text, _WRITE_FILE_KEYWORDS):
        return ImpactCategory.WRITE_FILE
    # 与审批目录同源：目录命中的类别映射到影响面（改系统一类的大盘）。
    match = classify(
        intent=intent,
        target_description=target_description,
        extra_text=params_text,
        real_element_text=real_element_text,
    )
    if match is not None:
        if match.category in (
            DangerCategory.DELETE,
        ):
            return ImpactCategory.DELETE
        if match.category in (
            DangerCategory.REGISTRY,
            DangerCategory.SYSTEM_SETTINGS,
            DangerCategory.COMMAND_LINE,
            DangerCategory.INSTALL_SOFTWARE,
        ):
            return ImpactCategory.SYSTEM_CHANGE
        if match.category is DangerCategory.PAYMENT:
            return ImpactCategory.PAYMENT
        if match.category is DangerCategory.SEND_MESSAGE:
            return ImpactCategory.SEND
        return ImpactCategory.SYSTEM_CHANGE
    return ImpactCategory.REVERSIBLE


def impact_verdict(category: ImpactCategory, level: AuthLevel) -> ImpactVerdict:
    """影响面 + 授权级别 → 处置。

    * L0：任何输入都不该发生（授权层在更早处拦）——这里对不可逆同样是拒；
    * L1：不可逆**一律拒绝**（没有审批人在场，宁可不干）；
    * L2：不可逆**必须人工确认**（``approval_required``，交给审批闸门）；
    * L3：放行但**记录**（无人值守实验，事后可审计）。
    """
    if category not in IRREVERSIBLE_CATEGORIES:
        return ImpactVerdict(
            category=category, verdict="allow", reason="影响面可逆（只读/可恢复）"
        )
    if level in (AuthLevel.L0, AuthLevel.L1):
        return ImpactVerdict(
            category=category,
            verdict="deny",
            reason=f"L{level.value[-1]} 拒绝不可逆动作（影响面={category.value}，"
            "没有审批人在场）",
        )
    if level is AuthLevel.L2:
        return ImpactVerdict(
            category=category,
            verdict="approval_required",
            reason=f"影响面={category.value}，L2 必须人工确认",
        )
    return ImpactVerdict(
        category=category,
        verdict="allow",
        reason=f"影响面={category.value}，L3 无人值守放行（记录在案）",
    )

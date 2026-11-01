"""**窗口黑名单**（护栏 2）：命中即拒，日志写明原因。

黑名单是一票否决：不管授权级别是多少，这些窗口**不许操作也不许打字**——
它们要么是用户在管理系统（任务管理器 / 注册表编辑器），要么是高价值目标
（密码管理器），要么是「打字就可能执行任意命令」的界面（终端），
要么是用户正在浏览自己文件的地方（资源管理器）。

⚠️ 与隐私黑名单（:mod:`minicode.privacy`，M20）是**两道不同的闸**：
M20 管「**不许拍**」（画面不进模型上下文），本模块管「**不许动**」。
窗口在 M20 名单里时抓帧都到不了这里；这里拦的是「能拍但不该动」的。

UAC 的完整防护在安全桌面检测（C-07，提权弹窗在安全桌面上，根本看不到）；
本名单把 Consent UI 托管窗口也列上，作为纵深防御。
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["BlacklistEntry", "DEFAULT_BLACKLIST", "match_blacklist"]


@dataclass(frozen=True)
class BlacklistEntry:
    """一条黑名单：类名子串 / 标题子串（大小写不敏感）+ 人读原因。"""

    #: 窗口**类名**子串（Win32 ``GetClassNameW`` 口径）。
    class_name: str
    #: 窗口**标题**子串（``GetWindowTextW`` 口径）。
    title: str
    #: 人读原因（进拒绝日志）。
    reason: str


#: **窗口黑名单**（唯一真值来源）。判定 = 类名**或**标题命中任一条目。
DEFAULT_BLACKLIST: tuple[BlacklistEntry, ...] = (
    BlacklistEntry("TaskmgrWindow", "", "任务管理器：用户正在管理系统进程"),
    BlacklistEntry("", "任务管理器", "任务管理器：用户正在管理系统进程"),
    BlacklistEntry("regedit_regedit", "", "注册表编辑器：改错一键系统就坏"),
    BlacklistEntry("", "注册表编辑器", "注册表编辑器：改错一键系统就坏"),
    BlacklistEntry("", "注册表编辑", "注册表编辑器：改错一键系统就坏"),
    # UAC（Consent.exe 跑在安全桌面上，正常桌面看不到它；这是纵深防御条目）。
    BlacklistEntry("Credential Dialog Xaml Host", "", "UAC / 凭据对话框：绝不代操作"),
    BlacklistEntry("", "用户帐户控制", "UAC / 凭据对话框：绝不代操作"),
    # 密码管理器：主窗口与解锁框都在名单里（标题形态各家不同，收主家）。
    BlacklistEntry("", "keepass", "密码管理器：绝不触碰"),
    BlacklistEntry("", "1password", "密码管理器：绝不触碰"),
    BlacklistEntry("", "bitwarden", "密码管理器：绝不触碰"),
    BlacklistEntry("", "enpass", "密码管理器：绝不触碰"),
    BlacklistEntry("", "dashlane", "密码管理器：绝不触碰"),
    BlacklistEntry("", "密码管理", "密码管理器：绝不触碰"),
    # 终端：打字 = 可能执行任意命令。
    BlacklistEntry("CASCADIA_HOSTING_WINDOW_CLASS", "", "终端：打字可能执行任意命令"),
    BlacklistEntry("ConsoleWindowClass", "", "终端：打字可能执行任意命令"),
    BlacklistEntry("", "命令提示符", "终端：打字可能执行任意命令"),
    BlacklistEntry("", "powershell", "终端：打字可能执行任意命令"),
    BlacklistEntry("", "pwsh", "终端：打字可能执行任意命令"),
    BlacklistEntry("", "windows terminal", "终端：打字可能执行任意命令"),
    BlacklistEntry("", "cmd.exe", "终端：打字可能执行任意命令"),
    # 资源管理器：用户正在浏览自己的文件（删除/重命名类操作零容忍）。
    BlacklistEntry("CabinetWClass", "", "资源管理器：用户文件浏览现场，不许动"),
    BlacklistEntry("ExploreWClass", "", "资源管理器：用户文件浏览现场，不许动"),
    BlacklistEntry("", "文件资源管理器", "资源管理器：用户文件浏览现场，不许动"),
)


def match_blacklist(window_class: str, window_title: str) -> str | None:
    """查黑名单。命中返回**原因**（进日志），没命中返回 ``None``。"""
    cls = (window_class or "").strip().lower()
    title = (window_title or "").strip().lower()
    for entry in DEFAULT_BLACKLIST:
        if entry.class_name and entry.class_name.lower() in cls:
            return entry.reason
        if entry.title and entry.title.lower() in title:
            return entry.reason
    return None

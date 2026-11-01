"""**四级授权**（W2 真机执行层）。

目标：把「能不能真的操控桌面」变成**可配置、可审计、可急停**的产品能力，
而不是一个危险开关。授权级别是护栏层的第一个输入：级别定了，
哪些动作放行、哪些要审批、哪些一律拒绝就定了。

四级（默认 **L0** —— fail-closed：没显式给级别，就什么都不许动）：

* **L0 只读** —— 截图、读控件树、算落点；**不下发任何输入**。
* **L1 白名单** —— 只在指定窗口集合内操作，窗口外一律拒绝；
  不可逆动作**一律拒绝**（L1 没有审批人在场）。
* **L2 审批** —— 全桌面可动；不可逆动作必须**人工确认**（沿用审批闸门）。
* **L3 无人值守** —— 全桌面、无人工确认。只允许在明确指定的实验里
  单次不超过 20 分钟使用；护栏层对它照样记预算、记录制、可急停。

⚠️ **级别不改变坐标口径、动作语义与审批闸门的判定规则** ——
它只是决定「闸门组合」；判定规则仍然只有审批目录那一份。
"""

from __future__ import annotations

from enum import StrEnum

__all__ = ["AuthLevel", "parse_level"]


class AuthLevel(StrEnum):
    """四级授权（默认 L0）。"""

    #: 只读：截图 / 读树 / 算落点，不下发输入。
    L0 = "L0"
    #: 白名单：只在指定窗口集合内操作；不可逆动作一律拒绝。
    L1 = "L1"
    #: 审批：全桌面可动，不可逆动作必须人工确认。
    L2 = "L2"
    #: 无人值守：全桌面、无人工确认（仅限明确指定的实验，单次 ≤ 20 分钟）。
    L3 = "L3"


def parse_level(raw: str) -> AuthLevel:
    """解析授权级别（大小写不敏感；非法值明确报错 —— 不猜）。"""
    value = raw.strip().upper()
    try:
        return AuthLevel(value)
    except ValueError:
        allowed = ", ".join(level.value for level in AuthLevel)
        raise ValueError(f"非法授权级别 {raw!r}（可选：{allowed}）") from None

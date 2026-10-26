"""模型上下文的开关与**启用期校验**（工单 M2 AC 5）。

两个开关：

* ``send_tree_to_model`` —— 「把控件树送进模型上下文」这条通道启用了吗；
* ``redaction_enabled`` —— 脱敏上线了吗。

**非法组合**：``send_tree_to_model=True`` 且 ``redaction_enabled=False``。
脱敏没上线就把树送进模型上下文，等于带着凭据去问模型 —— 这一组合在**配置层**
就被明确报错拦下，不静默放过。

两个开关**默认都是关**（fail-closed）：默认配置下模型侧出口直接拒绝。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .errors import RedactionConfigError

__all__ = ["ModelContextConfig", "load_model_context_config", "CONFIG_KEYS"]

#: 两个开关在**仓库 ``.env``** 里的键名。
CONFIG_KEYS = {
    "send_tree_to_model": "MINICODE_SEND_TREE_TO_MODEL",
    "redaction_enabled": "MINICODE_REDACTION_ENABLED",
}

#: 布尔取值为真的写法。
_TRUE_VALUES = ("1", "true", "yes", "on")


@dataclass(frozen=True)
class ModelContextConfig:
    """模型上下文的开关。"""

    #: 「送树进模型上下文」已启用。
    send_tree_to_model: bool = False
    #: 脱敏已启用。
    redaction_enabled: bool = False

    def __post_init__(self) -> None:
        if self.send_tree_to_model and not self.redaction_enabled:
            raise RedactionConfigError(
                "「送树进模型上下文已启用」与「脱敏未启用」不能同时成立："
                "脱敏没上线，任何把控件树送进模型上下文的通道都启用不了"
            )


def load_model_context_config(env_file: Path | None = None) -> ModelContextConfig:
    """从**仓库 ``.env``** 读这两个开关。

    两个开关都是**可选**的，缺省都是关（fail-closed）：没配 = 脱敏没上线 = 通道不可用。
    键名见 :data:`CONFIG_KEYS`（``.env.example`` 里有模板）。

    仓库 ``.env`` 的解析复用 M1 的那一份（:mod:`minicode.model.config`），
    不另起一个解析器 —— **配置只有一个来源**。
    """
    from minicode.model.config import find_env_file, parse_env_file

    path = Path(env_file) if env_file is not None else find_env_file()
    values = parse_env_file(path) if path.is_file() else {}
    return ModelContextConfig(
        send_tree_to_model=_flag(values.get(CONFIG_KEYS["send_tree_to_model"])),
        redaction_enabled=_flag(values.get(CONFIG_KEYS["redaction_enabled"])),
    )


def _flag(raw: str | None) -> bool:
    """配置里的布尔值。只有明确写成真的那几个值才算真。"""
    return (raw or "").strip().lower() in _TRUE_VALUES

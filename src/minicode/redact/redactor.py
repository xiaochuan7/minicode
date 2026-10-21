"""把控件树变成**模型侧文本**的唯一出口（工单 M2）。

三件事在这一处发生，且只在这一处发生：

1. **字段白名单** —— 模型侧文本只含控件类型 / 控件名 / 自动化标识 / 控件框 / 帮助文本；
   **值字段是条件出现的**：密码框 → 固定占位符；正文类控件 → **不出现**；
   其余控件 → 过正则之后才出现（工单第 20 行的两句话就是这么写的：
   「只含白名单字段（…五项…）」+「正文类控件的值字段不出现」）；
2. **正则脱敏** —— 名字、帮助文本、自动化标识、值里的敏感片段一律换成固定占位符；
3. **密码框替换** —— ``IsPassword=True`` 的控件，值是固定占位符。

外加两条硬规矩：

* **fail-closed**：快照里任何**未登记**的字段一律不外发（连字段名都不出现）；
* **硬闸门**：脱敏没上线，这个出口直接拒绝（通道不可用），**不产生任何模型侧文本**。

脱敏**只作用于「要外发的表示」**：用于定位的原始数据（控件名原值、控件框物理像素）
一个字节都不改 —— 名字被替换成占位符会让两个不同控件同名，触发「唯一性拒绝」误报。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any

from .config import ModelContextConfig
from .errors import ChannelUnavailableError
from .patterns import (
    PASSWORD_PLACEHOLDER,
    SECRET_PATTERNS,
    SECRET_PLACEHOLDER,
    redact_secrets,
)
from .report import RedactionReport

if TYPE_CHECKING:  # pragma: no cover - 只为类型标注，运行期不依赖 action 包（避免两个业务包互相咬住）
    from minicode.action.ports import ControlCandidate

__all__ = [
    "MODEL_SIDE_HEADER",
    "BODY_CONTROL_TYPES",
    "ModelTreeView",
    "TreeRedactor",
    "redaction_boundary",
]

#: 模型侧文本的抬头。**全仓只有这一个模块产出模型侧文本**（单点出口）。
MODEL_SIDE_HEADER = "控件树（已脱敏）"

#: 正文类控件（值一律不外发）：编辑框 / 文档 / 文本控件。
#: 架构决策备忘 §2 L5 ①：正文类文本（Edit 控件内容、Document/Text 控件长文本、聊天草稿）默认不外发。
BODY_CONTROL_TYPES = ("edit", "document", "text")


@dataclass(frozen=True)
class ModelTreeView:
    """送进模型上下文的那份界面状态（已脱敏）。

    * ``text`` —— 模型侧文本（要发出去的就是它）；
    * ``controls`` —— 同一份「处理过的界面状态」的结构化形状，**与输入候选同序**
      （后续工单可按下标回到原始控件）；
    * ``report`` —— 脱敏计数。

    ⚠️ **脱敏后的控件名不能用来做定位或唯一性判定**：两个不同控件的名字可能
    都被替换成同一个占位符（工单 M2 正文第 14 行就是这条警告）。
    定位与「唯一性拒绝」必须用**原始控件**（脱敏只改要外发的副本）。
    """

    text: str
    controls: tuple[ControlCandidate, ...]
    report: RedactionReport


class TreeRedactor:
    """控件树脱敏器。"""

    def __init__(self, config: ModelContextConfig):
        self.config = config

    def render(self, candidates: Sequence[ControlCandidate]) -> ModelTreeView:
        """把控件树渲染成模型侧视图。

        **硬闸门**：脱敏没启用 → 抛 :class:`ChannelUnavailableError`，
        一个字符的模型侧文本都不产生。
        """
        if not self.config.redaction_enabled:
            raise ChannelUnavailableError(
                "脱敏未上线，禁止把控件树送进模型上下文"
                "（架构决策备忘 §2.6 C-15：脱敏必须排在所有通道之前）"
            )
        if not self.config.send_tree_to_model:
            raise ChannelUnavailableError("「送树进模型上下文」未启用，该通道不可用")

        controls: list[ControlCandidate] = []
        password_replacements = 0
        regex_hits = 0
        dropped_body_fields = 0
        dropped_unknown_fields = 0

        for candidate in candidates:
            name, hits = redact_secrets(candidate.name)
            regex_hits += hits
            help_text, hits = redact_secrets(candidate.help_text)
            regex_hits += hits
            automation_id, hits = redact_secrets(candidate.automation_id or "")
            regex_hits += hits

            if candidate.is_password:
                # 密码框：值一律是占位符，明文不外发，也不做正则处理。
                value = PASSWORD_PLACEHOLDER
                password_replacements += 1
            elif _is_body_control(candidate.control_type):
                # 正文类控件：值不外发（聊天草稿、编辑框正文都在这一类）。
                value = ""
                dropped_body_fields += 1
            else:
                value, hits = redact_secrets(candidate.value)
                regex_hits += hits

            # fail-closed：未登记的字段连字段名都不出现。
            dropped_unknown_fields += len(candidate.extra)
            controls.append(
                replace(
                    candidate,
                    name=name,
                    help_text=help_text,
                    automation_id=automation_id or None,
                    value=value,
                    extra={},
                )
            )

        text = _render_text(controls)
        report = RedactionReport(
            password_replacements=password_replacements,
            regex_hits=regex_hits,
            dropped_body_fields=dropped_body_fields,
            dropped_unknown_fields=dropped_unknown_fields,
            output_length=len(text),
        )
        return ModelTreeView(text=text, controls=tuple(controls), report=report)


def _is_body_control(control_type: str) -> bool:
    """是不是正文类控件（值不外发）。"""
    lowered = (control_type or "").strip().lower()
    return any(kind in lowered for kind in BODY_CONTROL_TYPES)


def redaction_boundary() -> Mapping[str, Any]:
    """**脱敏的边界**（一份，可被引用）。

    隐私策略文档（工单 M20）要**引用**这份边界，而不是另抄一遍
    （工单 M20 AC 4：同一处规则，不写两套）——
    所以它住在**脱敏器这一侧**：**谁定义规则，谁描述规则**。

    字段：

    * ``model_side_header`` —— 模型侧文本的抬头；
    * ``password_placeholder`` —— 密码框的值在模型侧是什么；
    * ``secret_placeholder`` —— 正则命中的敏感片段在模型侧是什么；
    * ``body_control_types`` —— 正文类控件（值不外发）有哪些；
    * ``secret_pattern_labels`` —— 五类敏感模式的类目。
    """
    return {
        "model_side_header": MODEL_SIDE_HEADER,
        "password_placeholder": PASSWORD_PLACEHOLDER,
        "secret_placeholder": SECRET_PLACEHOLDER,
        "body_control_types": tuple(BODY_CONTROL_TYPES),
        "secret_pattern_labels": tuple(label for label, _pattern in SECRET_PATTERNS),
    }


def _render_text(controls: Sequence[ControlCandidate]) -> str:
    """把脱敏后的控件渲染成模型侧文本。"""
    lines = [MODEL_SIDE_HEADER]
    for control in controls:
        parts = [
            f'type="{control.control_type}"',
            f'name="{control.name}"',
            f'automation_id="{control.automation_id or ""}"',
            f"rect={_render_rect(control.rect)}",
            f'help="{control.help_text}"',
        ]
        if control.value:
            parts.append(f'value="{control.value}"')
        lines.append("- " + " ".join(parts))
    return "\n".join(lines)


def _render_rect(rect: tuple[int, int, int, int]) -> str:
    """控件框（物理像素）渲染成 ``(left,top,right,bottom)``。"""
    return "(" + ",".join(str(int(value)) for value in rect) + ")"

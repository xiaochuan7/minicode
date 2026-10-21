"""**MCP 工具目录**（工单 M19）。

三个工具 = 桌面控制能力在 MCP 这一层的**薄适配**：

======================  ==========================================  ==========================
工具                    能力                                        对应的服务调用
======================  ==========================================  ==========================
``minicode_action``     发起**一次动作**（目标描述 → 落点）          ``ActionService.execute``
``minicode_program``    执行**一段动作程序**（受控环境里跑）        ``ActionService.execute_program``
``minicode_observe``    **读观察**（截图摘要 + 已脱敏的控件树）      ``ActionService.observe``
======================  ==========================================  ==========================

⚠️ **不新增动作语义**：每个工具都只是「解析 → 调服务 → 渲染」。
坐标口径只有动作执行缝那一份（架构备忘 §2.5 ②），错误码只有动作协议那一份。

⚠️ **动作程序由调用方给**（宿主的模型写那段程序）——
本层**不生成**程序，也**不调模型**。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from minicode.action.program import PROGRAM_API_NAMES
from minicode.action.protocol import SEAM_ACTION_KINDS
from minicode.serve.protocol import PROTOCOL_VERSION
from minicode.serve.service import ActionService

__all__ = [
    "TOOL_ACTION",
    "TOOL_OBSERVE",
    "TOOL_PROGRAM",
    "ToolSpec",
    "build_tools",
]

#: 一次动作。
TOOL_ACTION = "minicode_action"

#: 一段动作程序。
TOOL_PROGRAM = "minicode_program"

#: 读观察。
TOOL_OBSERVE = "minicode_observe"

#: 本层接的**动作种类**（**取自协议层那一份** —— 同一个集合不许写两遍：
#: 真值在 :data:`minicode.action.protocol.SEAM_ACTION_KINDS`）。
_SEAM_KIND_NAMES = " / ".join(sorted(kind.value for kind in SEAM_ACTION_KINDS))

#: 动作程序**能调用的接口**（**取自受控环境那一份白名单**：
#: 真值在 :data:`minicode.action.program.PROGRAM_API_NAMES`）。
_PROGRAM_API_NAMES = " / ".join(sorted(PROGRAM_API_NAMES))

#: 协议版本号这个参数的说明（三个工具共用一句话）。
_PROTOCOL_PROPERTY = {
    "type": "string",
    "description": f"动作协议版本号（{PROTOCOL_VERSION}）；对不上会明确报错",
}

_ACTION_SCHEMA: Mapping[str, Any] = {
    "type": "object",
    "properties": {
        "protocol": _PROTOCOL_PROPERTY,
        "action": {
            "type": "string",
            "description": f"动作种类（本层接：{_SEAM_KIND_NAMES}）",
        },
        "target": {
            "type": "string",
            "description": "目标描述（自然语言，如「开始」）；截图/等待不需要",
        },
        "params": {
            "type": "object",
            "description": "动作参数（如 click_count=2）",
        },
    },
    "required": ["protocol"],
}

_PROGRAM_SCHEMA: Mapping[str, Any] = {
    "type": "object",
    "properties": {
        "protocol": _PROTOCOL_PROPERTY,
        "program": {
            "type": "string",
            "description": (
                "动作程序源码（在受控环境里跑；只能调用 "
                f"{_PROGRAM_API_NAMES}，系统调用与文件写入会被拒）"
            ),
        },
    },
    "required": ["protocol", "program"],
}

_OBSERVE_SCHEMA: Mapping[str, Any] = {
    "type": "object",
    "properties": {
        "protocol": _PROTOCOL_PROPERTY,
        "name": {
            "type": "string",
            "description": (
                "控件名条件（观察是**定点**的，必须带属性条件；禁止全树 dump）"
            ),
        },
    },
    "required": ["protocol", "name"],
}


@dataclass(frozen=True)
class ToolSpec:
    """一个工具：名字、说明、入参形状与**怎么跑**。"""

    name: str
    description: str
    input_schema: Mapping[str, Any]
    run: Callable[[Mapping[str, Any]], Mapping[str, Any]]

    def to_dict(self) -> dict[str, Any]:
        """MCP ``tools/list`` 里的形状。"""
        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": dict(self.input_schema),
        }


def build_tools(service: ActionService) -> tuple[ToolSpec, ...]:
    """把**动作服务**暴露成三个 MCP 工具。

    ⚠️ **薄适配**：每个工具只做「取参数 → 调服务 → 交出那份载荷」。
    """
    return (
        ToolSpec(
            name=TOOL_ACTION,
            description=(
                "在 Windows 桌面上发起一次动作（点击 / 移动 / 拖拽 / 滚动 / 截图 / 等待 / 放大）。"
                "给的是**目标描述**，不是坐标 —— 落点由定位通道决定。"
            ),
            input_schema=_ACTION_SCHEMA,
            run=lambda arguments: service.execute(arguments).to_dict(),
        ),
        ToolSpec(
            name=TOOL_PROGRAM,
            description=(
                "执行一段动作程序（一段 Python 源码，在**受控环境**里跑）。"
                "程序只能调用动作协议暴露的动作；系统调用与文件写入在执行前就被拒。"
                "程序自己写好，minicode 只负责执行。"
            ),
            input_schema=_PROGRAM_SCHEMA,
            run=lambda arguments: service.execute_program(arguments).to_dict(),
        ),
        ToolSpec(
            name=TOOL_OBSERVE,
            description=(
                "读观察：截一帧的**摘要**（尺寸 + 哈希）+ 按名字定点读到的"
                "**已脱敏**控件树（脱敏器是模型侧文本的唯一出口）。"
            ),
            input_schema=_OBSERVE_SCHEMA,
            run=lambda arguments: service.observe(arguments).to_dict(),
        ),
    )

"""**消歧指认**的实现：模型只负责「选」，不负责「瞄」（工单 M4）。

三件事在这里发生，且只在这里发生：

1. **把候选清单列给模型** —— 清单是**已脱敏**的模型侧文本（脱敏器是模型侧文本的
   唯一出口，本模块**不自己渲染**控件文本）；
2. **只问「选哪一个」** —— 请求里没有「输出坐标」这类措辞，也没有坐标换算提示；
   模型回答**行号**（1 起），行号 → 元素 id 的映射在本地做；
3. **回显** —— 模型发出的选择结果 + **实际发送**的图片尺寸（**不含坐标**：
   这一条通道的模型不产坐标，不许伪造成坐标回显）。

**模型坐标不参与定位**：落点始终来自控件框的物理像素中心（UIA 通道的最终落点直接取控件框中心，不经过 crop）。
"""

from __future__ import annotations

import json
import re

from minicode.action.image import encode_frame_base64
from minicode.action.ports import SelectionEcho
from minicode.model.effort import Effort
from minicode.model.ports import ModelCaller

from .ports import PointerRequest, PointerResult

__all__ = [
    "DEFAULT_EFFORT",
    "DEFAULT_MAX_TOKENS",
    "ModelCandidatePointer",
    "build_disambiguation_prompt",
    "parse_chosen_numbers",
]

#: 消歧是**动作步** → 思考预算取最低档（「动作步必须显式设 effort=low 或 medium」）。
DEFAULT_EFFORT = Effort.LOW

#: 一次消歧的输出预算：模型只回答一个行号。
#:
#: ⚠️ **不能太小** —— 服务端的思考链与输出**共用**这份预算，
#: 预算太小会出现「HTTP 200 但没有文本输出」（思考链把预算吃光，
#: 模型客户端的坑③）。这里取模型客户端的同一个默认值。
DEFAULT_MAX_TOKENS = 2000

#: 模型回答里可能出现的「行号」字段名（提示词要求的就是 ``chosen``）。
_CHOSEN_KEYS = ("chosen",)

#: 模型直接写行号时的两种常见写法（「2」/「第 2 项」）。
_PLAIN_NUMBER = re.compile(r"^\s*(\d{1,3})\s*$")
_ORDINAL_NUMBER = re.compile(r"第\s*(\d{1,3})\s*[项个]")


def build_disambiguation_prompt(
    target_description: str,
    model_view: str,
    candidate_count: int,
) -> str:
    """构造消歧请求的正文（**纯函数**）。

    措辞只有一件事：**从清单里选出哪一项**。
    刻意**不出现**「坐标」「归一化」「分母」「像素」这类词 ——
    这一条通道的模型不产坐标，提示词里也不该有坐标换算的暗示（工单 M4 AC 2）。
    """
    return (
        "下面是一份界面控件清单（已脱敏）。清单里每一行是一个候选项，"
        "按行号从 1 开始编号。\n"
        "附上的图片是当前屏幕画面，清单里每项后面的矩形是它在画面上的位置。\n"
        f"目标描述：{target_description}\n"
        f"任务：从清单里选出与目标描述对应的那一项，"
        f"只回答它的行号（1~{candidate_count} 之间的一个整数）。\n"
        '只回答一个 JSON 对象，不要任何解释：{"chosen": <行号>}\n'
        f"{model_view}\n"
    )


def parse_chosen_numbers(text: str) -> tuple[int, ...]:
    """从模型回复里取出**行号**（1 起）。

    * 优先读 JSON（``{"chosen": 2}``，提示词要求的格式）；
    * 其次读「光秃秃一个整数」或「第 N 项」。

    ⚠️ **不做「文本里随便找个整数」的兜底** —— 那会把模型回复里的
    别的数字（例如坐标）当成行号，**静默选错候选**（工单 M4 AC 2：
    模型坐标不参与）。读不出就是读不出（返回空元组）—— **不猜**。
    """
    payload = _json_payload(text)
    if payload is not None:
        numbers = _numbers_from_json(payload)
        if numbers is not None:
            return numbers
    plain = _PLAIN_NUMBER.match(text or "")
    if plain is not None:
        return (int(plain.group(1)),)
    ordinal = _ORDINAL_NUMBER.search(text or "")
    if ordinal is not None:
        return (int(ordinal.group(1)),)
    return ()


class ModelCandidatePointer:
    """消歧指认（模型）。

    端口形状与假实现一致：回答**必须落在候选集内且恰好一个**才算指认成功
    （判定在通道里做，本实现只负责把模型的回答映射回元素 id）。
    """

    def __init__(
        self,
        caller: ModelCaller,
        *,
        effort: Effort | str = DEFAULT_EFFORT,
        max_tokens: int = DEFAULT_MAX_TOKENS,
    ):
        self.caller = caller
        self.effort = effort
        self.max_tokens = int(max_tokens)

    def point(self, request: PointerRequest) -> PointerResult:
        """问模型「选哪一个」，把回答映射回候选集里的元素 id。"""
        prompt = build_disambiguation_prompt(
            request.target_description,
            request.model_view,
            len(request.candidates),
        )
        image_base64 = encode_frame_base64(request.frame)
        reply = self.caller.complete(
            prompt,
            effort=self.effort,
            max_tokens=self.max_tokens,
            image_base64=image_base64,
        )
        numbers = parse_chosen_numbers(reply.text)
        chosen_ids: list[str] = []
        chosen_names: list[str] = []
        for number in numbers:
            index = number - 1
            if not 0 <= index < len(request.candidates):
                # 指到候选集之外 → 不算数（消歧失败由通道判定）。
                continue
            chosen_ids.append(request.candidates[index].element_id)
            chosen_names.append(request.candidates[index].name)
        echo = SelectionEcho(
            chosen_numbers=tuple(numbers),
            chosen_names=tuple(chosen_names),
            # **实际发送**的图片尺寸：**没发图就是空的**（不许拿抓图尺寸冒充）。
            sent_width=request.frame.width if image_base64 is not None else None,
            sent_height=request.frame.height if image_base64 is not None else None,
            model_calls=1,
        )
        return PointerResult(chosen_ids=tuple(chosen_ids), echo=echo)


def _json_payload(text: str):
    """回复里的第一个 JSON 对象；没有就不是 ``None`` 判空，是 ``None`` 返回。"""
    start = (text or "").find("{")
    end = (text or "").rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        payload = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def _numbers_from_json(payload: dict) -> tuple[int, ...] | None:
    """JSON 里的「行号」字段 → 行号元组；没有可用字段时返回 ``None``。"""
    for key in _CHOSEN_KEYS:
        if key not in payload:
            continue
        value = payload[key]
        if isinstance(value, bool):
            continue
        if isinstance(value, int):
            return (value,)
        if isinstance(value, str) and value.strip().isdigit():
            return (int(value.strip()),)
        if isinstance(value, list):
            numbers = [
                int(item)
                for item in value
                if isinstance(item, int) and not isinstance(item, bool)
            ]
            if numbers:
                return tuple(numbers)
    return None

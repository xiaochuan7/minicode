"""模型客户端：主链路**唯一**的模型调用入口。

四件事：

1. **凭据单一来源**：请求头、端点、模型、会话标识全部来自仓库配置
   （见 :mod:`minicode.model.config`），客户端内不存在第二个凭据来源；
2. **思考预算（effort）显式**：调用入口必须给档位（五档），省略即报错，
   绝不悄悄按服务端缺省（最高档）跑；
3. **采样温度显式**（缺省 0）：不显式给就走服务端缺省（常见缺省 1.0）→ 有采样 →
   同图同提示词两次回答可能不同（见 :func:`build_request_body`）；
4. **失败如实回传**：模型侧错误原样带回；HTTP 200 但**没有文本输出**判显式失败，
   不呈现成「成功但坐标为空」。

**模型坐标原样透传**：客户端不解释模型给的数值 —— 不乘分母、不按数值大小猜量纲、
不裁剪到某个区间。量纲由标定探针解释，不在本模块。

**两套协议**：``anthropic``（Anthropic Messages，原实现）与 ``openai``
（OpenAI 兼容 ``/chat/completions``，覆盖 OpenAI / DeepSeek / Moonshot / 智谱 /
硅基流动 / DashScope compatible-mode / Ollama / vLLM / LM Studio / OpenRouter / OneAPI）。
走哪套由 :attr:`minicode.model.config.ModelConfig.protocol` 决定，调用入口不变。

> 词汇：本仓把服务端「思考预算」叫 **effort**；工单里说的「思考档位」= effort 的**取值**
> （low/medium/high/xhigh/max）。
"""

from __future__ import annotations

import base64
import json
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Any

from .config import (
    DEFAULT_TEMPERATURE,
    PROTOCOL_ANTHROPIC,
    PROTOCOL_OPENAI,
    ModelConfig,
)
from .effort import EFFORT_VALUES, Effort
from .errors import (
    EffortRequiredError,
    ModelError,
    ModelSideError,
    ModelTransportError,
    NoTextOutputError,
)
from .request_log import (
    RequestLogSink,
    build_request_log,
    estimate_image_tokens,
    estimate_openai_image_tokens,
    png_image_size,
)

__all__ = [
    "ANTHROPIC_VERSION",
    "ModelReply",
    "ModelClient",
    "build_headers",
    "build_request_body",
    "content_block_types_of",
    "image_tokens_of",
    "parse_response",
    "require_effort",
    "stop_reason_of",
    "text_of",
    "usage_of",
]

#: Anthropic Messages 协议版本（协议常量，不是凭据，不进仓库配置）。
ANTHROPIC_VERSION = "2023-06-01"

#: 请求超时（秒）。
DEFAULT_TIMEOUT_S = 180.0


@dataclass(frozen=True)
class ModelReply:
    """一次模型调用的结果。

    ``text`` 是模型给的文本；``json_payload()`` 从文本里取出 JSON 对象，
    **模型坐标数值原样返回**（客户端不解释坐标）。
    """

    text: str
    model: str
    endpoint: str
    effort: Effort
    http_status: int
    latency_ms: float
    input_tokens: int | None = None
    output_tokens: int | None = None
    thinking_tokens: int | None = None
    #: **图像占的输入 token 数**（按 28 px patch 规则对**实际发出的那张图**的估计）。
    #:
    #: ⚠️ 这是**估计**，不是服务端报出来的数 —— 服务端若把图缩小过，实际占用会小于它。
    #: 它与 :attr:`input_tokens` **分开记**，就是为了让「每轮图像 token」能被单独读出来
    #: （上下文预算记账：改完按需观察之后到底省了多少，要从这个数直接读）。
    image_tokens: int | None = None
    #: **命中提示词缓存**的那部分输入 token（服务端把它与 ``input_tokens`` 分开报）。
    #: 上下文总大小 = ``input_tokens`` + ``cache_read_input_tokens``
    #: + ``cache_creation_input_tokens``（工单 M10 的成本曲线按这个口径看）。
    cache_read_input_tokens: int | None = None
    cache_creation_input_tokens: int | None = None
    raw_content: tuple[Mapping[str, Any], ...] = field(default_factory=tuple)
    #: 响应体里的 ``stop_reason``（Anthropic 协议字段：``end_turn`` / ``max_tokens`` …）。
    #:
    #: **只在响应体里有** —— 请求日志靠它把「输出预算被吃光」（``max_tokens``）
    #: 与「正常结束」分开（不再靠 ``output_tokens == max_tokens`` 反推）。
    stop_reason: str | None = None
    #: 响应体 ``content[]`` 里各块的 ``type``（按顺序，如 ``("thinking", "text")``）。
    #:
    #: 形态判定直读它：只有 ``thinking`` 块 → 思考链吃光了预算（**无文本**）。
    content_block_types: tuple[str, ...] = ()

    def json_payload(self) -> Any:
        """从文本里取出第一个 JSON 对象；**数值原样返回**，不乘任何分母。

        文本里没有 JSON 对象 → 抛 :class:`~minicode.model.errors.ModelError`：
        「模型给了话、但没有可解析的坐标」不能被当成成功。
        """
        start = self.text.find("{")
        end = self.text.rfind("}")
        if start < 0 or end <= start:
            raise ModelError(f"模型回复里没有 JSON 对象：{self.text[:120]!r}")
        try:
            return json.loads(self.text[start : end + 1])
        except json.JSONDecodeError as exc:
            raise ModelError(f"模型回复里的 JSON 解析失败：{exc}") from exc


def require_effort(effort: Effort | str | None) -> Effort:
    """档位必须是五档里的一个；**省略即报错**。

    服务端的缺省是最高档，所以「没给」不等于「给个低的」——直接报错。
    """
    if effort is None:
        raise EffortRequiredError(
            "思考预算（effort）必须显式给出（五档："
            + "/".join(EFFORT_VALUES)
            + "）；省略会按服务端缺省的最高档跑，因此直接报错"
        )
    if isinstance(effort, Effort):
        return effort
    try:
        return Effort(str(effort))
    except ValueError as exc:
        raise EffortRequiredError(
            f"思考预算（effort）{effort!r} 不在五档里（"
            + "/".join(EFFORT_VALUES)
            + "）"
        ) from exc


def build_headers(config: ModelConfig) -> dict[str, str]:
    """构造请求头（按协议分支）。

    * ``anthropic``：``x-api-key`` + ``anthropic-version``（维持原实现）；
    * ``openai``：``authorization: Bearer {api_key}``（OpenAI 兼容生态的标准头）。

    两个兼容头 ``x-opencode-session`` / ``x-opencode-client`` **仅当配置非空才发**：
    通用 provider 不需要它们（内部网关才要），空值也不该出现在请求里。
    2026-10-20 实测（内部网关）：**只有 ``user-agent`` 被边缘层强制**（去掉它 →
    Cloudflare ``Error 1010``，HTTP 403）；``x-opencode-client`` 去掉后服务端照收
    （HTTP 200）。另一个头仍然照发，是为了换网关时不丢。
    """
    headers: dict[str, str] = {
        "content-type": "application/json",
        "accept": "application/json",
        "user-agent": config.user_agent,
    }
    if config.protocol == PROTOCOL_OPENAI:
        headers["authorization"] = f"Bearer {config.api_key}"
    else:
        headers["x-api-key"] = config.api_key
        headers["anthropic-version"] = ANTHROPIC_VERSION
    if config.session_id:
        headers["x-opencode-session"] = config.session_id
    if config.client_header:
        headers["x-opencode-client"] = config.client_header
    return headers


def build_request_body(
    *,
    model: str,
    prompt: str,
    effort: Effort | str | None,
    max_tokens: int,
    image_base64: str | None = None,
    images_base64: Sequence[str] = (),
    thinking_disabled: bool = False,
    temperature: float = DEFAULT_TEMPERATURE,
    protocol: str = PROTOCOL_ANTHROPIC,
) -> dict[str, Any]:
    """构造请求体（纯函数）。

    **anthropic**（逐字保持原实现）：

    * 思考预算用服务端**真正支持**的写法：``output_config.effort``；
      另一种别名写法实测被服务端忽略且更慢，不作为正式开关；
    * ``effort=None`` 时**不带** ``output_config`` —— 这是「省略档位」的形态，
      只给诊断用的回归脚本用；客户端的调用入口不允许走到这里；
    * ``thinking_disabled=True`` 会带上 ``{"thinking": {"type": "disabled"}}``，
      **只为回归坑③存在**（服务端会回 HTTP 400，客户端必须如实回传）；
    * **``temperature`` 显式写进请求体**（缺省 0）—— 不写就走服务端缺省
      （常见缺省 1.0 → 有采样）：同一张图 + 逐字相同的提示词，两次回答可能不同。
      官方通用 API grounding 协议也是这么做的（``models/gpt4x.py``：temperature 0）。

    **openai**：``messages[0].content`` 是「**文本块在前、图片块在后**」的数组
    （图片用 ``image_url`` + base64 data URL）；**不写** ``output_config`` /
    ``thinking`` —— OpenAI 兼容生态没有统一字段，不猜。

    **图片可以有多张**（工单 M10 的上下文窗口要把最近几张完整图一起带上）：
    ``image_base64`` 是那张主图，``images_base64`` 是追加的那几张，
    顺序即发送顺序。
    """
    images = [data for data in (image_base64, *images_base64) if data]
    if protocol == PROTOCOL_OPENAI:
        content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
        content.extend(
            {
                "type": "image_url",
                "image_url": {"url": f"data:image/png;base64,{data}"},
            }
            for data in images
        )
        return {
            "model": model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": [{"role": "user", "content": content}],
        }

    body: dict[str, Any] = {}
    content = []
    for data in images:
        content.append(
            {
                "type": "image",
                "source": {"type": "base64", "media_type": "image/png", "data": data},
            }
        )
    content.append({"type": "text", "text": prompt})
    body["model"] = model
    body["max_tokens"] = max_tokens
    body["temperature"] = temperature
    body["messages"] = [{"role": "user", "content": content}]
    if effort is not None:
        body["output_config"] = {"effort": str(effort)}
    if thinking_disabled:
        body["thinking"] = {"type": "disabled"}
    return body


def text_of(data: Mapping[str, Any]) -> str:
    """响应里的文本（``content[]`` 里的 ``text`` 块拼起来）。**文本解析的唯一一处定义。**"""
    blocks = data.get("content") or []
    return "\n".join(
        str(block.get("text", "")) for block in blocks if block.get("type") == "text"
    ).strip()


def stop_reason_of(data: Mapping[str, Any]) -> str | None:
    """响应体里的 ``stop_reason``。**读取的唯一一处。**

    没有这个字段 → ``None``（**不猜**、不拿 ``output_tokens`` 反推）。
    """
    value = data.get("stop_reason")
    return None if value is None else str(value)


def content_block_types_of(data: Mapping[str, Any]) -> tuple[str, ...]:
    """响应体 ``content[]`` 里各块的 ``type``（按顺序）。**读取的唯一一处。**

    形态判定直读它：只有 ``thinking`` 块 → 思考链吃光了预算（**没有文本**）。
    """
    blocks = data.get("content") or []
    return tuple(
        str(block.get("type"))
        for block in blocks
        if isinstance(block, Mapping) and block.get("type")
    )


def _openai_choice_of(data: Mapping[str, Any]) -> Mapping[str, Any]:
    """OpenAI 兼容响应的 ``choices[0]``；没有就是空映射（**不猜**）。"""
    choices = data.get("choices")
    if isinstance(choices, Sequence) and not isinstance(choices, (str, bytes)) and choices:
        first = choices[0]
        if isinstance(first, Mapping):
            return first
    return {}


def _openai_message_of(data: Mapping[str, Any]) -> Mapping[str, Any]:
    """OpenAI 兼容响应的 ``choices[0].message``；没有就是空映射。"""
    message = _openai_choice_of(data).get("message")
    return message if isinstance(message, Mapping) else {}


def _openai_text_of(message: Mapping[str, Any]) -> str:
    """OpenAI 兼容响应的文本：``content`` 是 str 直接用；是 list 就拼各 ``text`` 片段。

    ⚠️ **``reasoning_content`` 不是回答**（思考链），这里**不读它**。
    """
    content = message.get("content")
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, Sequence) and not isinstance(content, (str, bytes)):
        parts = [
            str(part.get("text", ""))
            for part in content
            if isinstance(part, Mapping) and part.get("type") == "text"
        ]
        return "\n".join(parts).strip()
    return ""


def _openai_reasoning_of(message: Mapping[str, Any]) -> str | None:
    """OpenAI 兼容响应的思考链（``reasoning_content``）；没有就是 ``None``。"""
    value = message.get("reasoning_content")
    return None if not value else str(value)


def _openai_stop_reason_of(data: Mapping[str, Any]) -> str | None:
    """OpenAI 兼容响应的 ``choices[0].finish_reason`` → ``stop_reason``。"""
    value = _openai_choice_of(data).get("finish_reason")
    return None if value is None else str(value)


def _openai_content_block_types_of(message: Mapping[str, Any]) -> tuple[str, ...]:
    """OpenAI 兼容响应的形态字段（与 Anthropic 的 ``content_block_types`` 同口径）。

    有文本 → ``("text",)``；只有思考链 → ``("reasoning",)``；否则 ``()``。
    """
    if _openai_text_of(message):
        return ("text",)
    if _openai_reasoning_of(message):
        return ("reasoning",)
    return ()


def _response_shape(
    body_bytes: bytes, protocol: str = PROTOCOL_ANTHROPIC
) -> tuple[str | None, tuple[str, ...]]:
    """从**原始响应体**里取 ``(stop_reason, content_block_types)``。

    只用在**失败路径**上（成功路径由 :func:`parse_response` 直接填进
    :class:`ModelReply`）—— 失败路径上响应体照样带着这两个字段：
    「思考链吃光预算 → 没有文本」那类形态正是靠 ``stop_reason=max_tokens``
    （OpenAI 兼容是 ``finish_reason=length``）看出来的。
    读不出来 → ``(None, ())``（**不猜**）。
    """
    try:
        data = json.loads(body_bytes.decode("utf-8", errors="replace"))
    except json.JSONDecodeError:
        return None, ()
    if not isinstance(data, Mapping):
        return None, ()
    if protocol == PROTOCOL_OPENAI:
        return _openai_stop_reason_of(data), _openai_content_block_types_of(
            _openai_message_of(data)
        )
    return stop_reason_of(data), content_block_types_of(data)


def usage_of(
    data: Mapping[str, Any], *, protocol: str = PROTOCOL_ANTHROPIC
) -> dict[str, Any]:
    """用量明细：输入 / 输出 / 思考 token。**用量解析的唯一一处定义。**

    * ``anthropic``：``input_tokens`` / ``output_tokens`` /
      ``output_tokens_details.thinking_tokens``，缓存命中与写入分开报；
    * ``openai``：``prompt_tokens`` → 输入、``completion_tokens`` → 输出、
      ``completion_tokens_details.reasoning_tokens`` → 思考；
      **``cache_*`` 保持 ``None``** —— OpenAI 的 ``cached_tokens`` 与 Anthropic
      的「可加性」口径不同，不硬映射（不猜）。

    提示词缓存的命中与写入**分开报**（``cache_read_input_tokens`` /
    ``cache_creation_input_tokens``）——上下文总大小要三个数加起来看，
    只看 ``input_tokens`` 会把「上下文变大了」看漏（工单 M10 AC 5）。
    """
    usage = data.get("usage") or {}
    if not isinstance(usage, Mapping):
        usage = {}
    if protocol == PROTOCOL_OPENAI:
        details = usage.get("completion_tokens_details") or {}
        if not isinstance(details, Mapping):
            details = {}
        return {
            "input_tokens": usage.get("prompt_tokens"),
            "output_tokens": usage.get("completion_tokens"),
            "thinking_tokens": details.get("reasoning_tokens"),
            "cache_read_input_tokens": None,
            "cache_creation_input_tokens": None,
        }
    details = usage.get("output_tokens_details") or {}
    if not isinstance(details, Mapping):
        details = {}
    return {
        "input_tokens": usage.get("input_tokens"),
        "output_tokens": usage.get("output_tokens"),
        "thinking_tokens": details.get("thinking_tokens"),
        "cache_read_input_tokens": usage.get("cache_read_input_tokens"),
        "cache_creation_input_tokens": usage.get("cache_creation_input_tokens"),
    }


def image_tokens_of(
    *images_base64: str | None, protocol: str = PROTOCOL_ANTHROPIC
) -> int | None:
    """这次请求里图像占的输入 token 数（**估计值**，按协议选规则）。

    * ``anthropic``：28 px patch 规则（:func:`estimate_image_tokens`）；
    * ``openai``：512 tile 规则（:func:`estimate_openai_image_tokens`）。

    没有图 → ``None``（不是 0：没有图与「图占了 0 个 token」是两件事）。
    图解不出 PNG 尺寸 → ``None``（**不猜**）。
    """
    estimate = (
        estimate_openai_image_tokens
        if protocol == PROTOCOL_OPENAI
        else estimate_image_tokens
    )
    total = 0
    seen = False
    for data in images_base64:
        if not data:
            continue
        try:
            raw = base64.b64decode(data)
        except (ValueError, TypeError):
            return None
        size = png_image_size(raw)
        if size is None:
            return None
        total += estimate(*size)
        seen = True
    return total if seen else None


def parse_response(
    *,
    status: int,
    body_bytes: bytes,
    model: str,
    endpoint: str,
    effort: Effort,
    latency_ms: float,
    protocol: str = PROTOCOL_ANTHROPIC,
) -> ModelReply:
    """解析响应（纯函数；按协议分支）。

    * 状态码不是 200 → :class:`ModelSideError`（状态、错误类型、错误文本如实回传；
      两个协议的 ``{"error": {"type", "message"}}`` 形状共用这条路径）；
    * 200 但响应体不是 JSON → :class:`ModelSideError`（协议被违反，不给裸异常）；
    * 200 但没有文本输出 → :class:`NoTextOutputError`（显式失败）。
    """
    raw = body_bytes.decode("utf-8", errors="replace")
    if status != 200:
        error_type = None
        message = raw.strip()[:500] or f"HTTP {status}"
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            data = {}
        if isinstance(data, Mapping):
            error = data.get("error")
            if isinstance(error, Mapping):
                error_type = error.get("type")
                message = str(error.get("message") or message)
        raise ModelSideError(
            status,
            message,
            error_type=error_type,
            body=raw[:500],
            usage=(
                usage_of(data, protocol=protocol) if isinstance(data, Mapping) else None
            ),
        )

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ModelSideError(
            status,
            f"响应体不是 JSON：{exc}（前 200 字：{raw[:200]!r}）",
            body=raw[:500],
        ) from exc
    if not isinstance(data, Mapping):
        raise ModelSideError(status, f"响应体不是 JSON 对象：{raw[:200]!r}", body=raw[:500])

    if protocol == PROTOCOL_OPENAI:
        return _parse_openai_reply(
            status=status,
            data=data,
            model=model,
            endpoint=endpoint,
            effort=effort,
            latency_ms=latency_ms,
        )

    blocks = data.get("content") or []
    text = text_of(data)
    usage = usage_of(data)
    if not text:
        raise NoTextOutputError(
            "HTTP 200 但本次没有文本输出（思考链可能把 token 预算吃光了）",
            usage=usage,
        )
    return ModelReply(
        text=text,
        model=str(data.get("model") or model),
        endpoint=endpoint,
        effort=effort,
        http_status=status,
        latency_ms=latency_ms,
        input_tokens=usage["input_tokens"],
        output_tokens=usage["output_tokens"],
        thinking_tokens=usage["thinking_tokens"],
        cache_read_input_tokens=usage["cache_read_input_tokens"],
        cache_creation_input_tokens=usage["cache_creation_input_tokens"],
        raw_content=tuple(blocks),
        stop_reason=stop_reason_of(data),
        content_block_types=content_block_types_of(data),
    )


def _parse_openai_reply(
    *,
    status: int,
    data: Mapping[str, Any],
    model: str,
    endpoint: str,
    effort: Effort,
    latency_ms: float,
) -> ModelReply:
    """解析 OpenAI 兼容的 200 响应（:func:`parse_response` 的分支实现）。

    文本只读 ``choices[0].message.content``（str 或 text 片段列表）；
    ``reasoning_content`` **只用于形态字段，不当作回答**。
    """
    message = _openai_message_of(data)
    text = _openai_text_of(message)
    usage = usage_of(data, protocol=PROTOCOL_OPENAI)
    if not text:
        raise NoTextOutputError(
            "HTTP 200 但本次没有文本输出（OpenAI 兼容响应的 "
            "choices[0].message.content 为空；思考链可能把 token 预算吃光了）",
            usage=usage,
        )
    return ModelReply(
        text=text,
        model=str(data.get("model") or model),
        endpoint=endpoint,
        effort=effort,
        http_status=status,
        latency_ms=latency_ms,
        input_tokens=usage["input_tokens"],
        output_tokens=usage["output_tokens"],
        thinking_tokens=usage["thinking_tokens"],
        cache_read_input_tokens=None,
        cache_creation_input_tokens=None,
        stop_reason=_openai_stop_reason_of(data),
        content_block_types=_openai_content_block_types_of(message),
    )


class ModelClient:
    """模型客户端。

    用法::

        client = ModelClient(load_config(), JsonlRequestLog("runs/model-requests.jsonl"))
        reply = client.complete("只回复两个字：就绪", effort=Effort.LOW)

    协议（``anthropic`` / ``openai``）由 :class:`~minicode.model.config.ModelConfig`
    的 ``protocol`` 决定，调用入口与返回类型不随协议变化。
    """

    def __init__(
        self,
        config: ModelConfig,
        request_log: RequestLogSink,
        *,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        clock: Callable[[], float] = time.perf_counter,
    ):
        self.config = config
        self.request_log = request_log
        self.timeout_s = timeout_s
        self._clock = clock
        # 启动日志：**构造时打一次**（不是每次请求），说清协议 / 端点 / 模型，
        # 以及密钥到底从哪个 env 键来；api_key 已脱敏（只留前 4 后 4）。
        print(config.describe(), file=sys.stderr)

    def complete(
        self,
        prompt: str,
        *,
        effort: Effort | str | None = None,
        max_tokens: int = 2000,
        image_base64: str | None = None,
        images_base64: Sequence[str] = (),
        thinking_disabled: bool = False,
        parse_ok: bool | None = None,
    ) -> ModelReply:
        """发一次请求并返回结果。

        ``effort`` **必须显式给**；省略直接报错（不按服务端缺省的最高档跑）。

        ``parse_ok``（这次回答是否解析得出坐标）**由调用方给**：客户端层
        **不知道解析规则** → 缺省 ``None``，如实落盘（不猜）。
        """
        selected = require_effort(effort)
        endpoint = self.config.endpoint
        headers = build_headers(self.config)
        body = build_request_body(
            model=self.config.model,
            prompt=prompt,
            effort=selected,
            max_tokens=max_tokens,
            image_base64=image_base64,
            images_base64=images_base64,
            thinking_disabled=thinking_disabled,
            temperature=self.config.temperature,
            protocol=self.config.protocol,
        )

        started = self._clock()
        image_tokens = image_tokens_of(
            image_base64, *images_base64, protocol=self.config.protocol
        )
        try:
            status, raw = self._post(endpoint, headers, body)
        except ModelTransportError as exc:
            self._log(
                effort=selected,
                endpoint=endpoint,
                http_status=None,
                latency_ms=(self._clock() - started) * 1000.0,
                image_tokens=image_tokens,
                parse_ok=parse_ok,
                error=str(exc),
            )
            raise
        latency_ms = (self._clock() - started) * 1000.0

        try:
            reply = parse_response(
                status=status,
                body_bytes=raw,
                model=self.config.model,
                endpoint=endpoint,
                effort=selected,
                latency_ms=latency_ms,
                protocol=self.config.protocol,
            )
        except (ModelSideError, NoTextOutputError) as exc:
            usage = exc.usage or {}
            # 失败路径上响应体**照样带着**这两个字段 —— 「思考链吃光预算 →
            # 没有文本」那类形态正是靠它们看出来的（成功路径由 parse_response 填）。
            stop_reason, block_types = _response_shape(raw, self.config.protocol)
            self._log(
                effort=selected,
                endpoint=endpoint,
                http_status=status,
                latency_ms=latency_ms,
                input_tokens=usage.get("input_tokens"),
                output_tokens=usage.get("output_tokens"),
                thinking_tokens=usage.get("thinking_tokens"),
                cache_read_input_tokens=usage.get("cache_read_input_tokens"),
                cache_creation_input_tokens=usage.get("cache_creation_input_tokens"),
                image_tokens=image_tokens,
                stop_reason=stop_reason,
                content_block_types=block_types,
                # 「没有文本输出」是**确知的 0**；其它失败形态不知道 → ``None``。
                text_len=0 if isinstance(exc, NoTextOutputError) else None,
                parse_ok=parse_ok,
                error=exc.message,
            )
            raise

        self._log(
            effort=selected,
            endpoint=endpoint,
            http_status=reply.http_status,
            latency_ms=reply.latency_ms,
            input_tokens=reply.input_tokens,
            output_tokens=reply.output_tokens,
            thinking_tokens=reply.thinking_tokens,
            cache_read_input_tokens=reply.cache_read_input_tokens,
            cache_creation_input_tokens=reply.cache_creation_input_tokens,
            image_tokens=image_tokens,
            stop_reason=reply.stop_reason,
            content_block_types=reply.content_block_types,
            text_len=len(reply.text),
            parse_ok=parse_ok,
            error=None,
        )
        # 图像 token 数回灌进回复（调用方要能直接读到，不必去翻日志）。
        return replace(reply, image_tokens=image_tokens)

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------

    def _post(
        self,
        endpoint: str,
        headers: Mapping[str, str],
        body: Mapping[str, Any],
    ) -> tuple[int, bytes]:
        request = urllib.request.Request(
            endpoint,
            data=json.dumps(body).encode("utf-8"),
            method="POST",
            headers=dict(headers),
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_s) as response:
                return int(response.status), response.read()
        except urllib.error.HTTPError as exc:
            # HTTP 错误也是一次请求的**结果**（状态码 + 响应体），不是传输失败。
            return int(exc.code), exc.read()
        except urllib.error.URLError as exc:
            raise ModelTransportError(f"请求 {endpoint} 失败：{exc.reason}") from exc
        except TimeoutError as exc:
            raise ModelTransportError(f"请求 {endpoint} 超时（{self.timeout_s}s）") from exc

    def _log(
        self,
        *,
        effort: Effort,
        endpoint: str,
        http_status: int | None,
        latency_ms: float,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        thinking_tokens: int | None = None,
        image_tokens: int | None = None,
        cache_read_input_tokens: int | None = None,
        cache_creation_input_tokens: int | None = None,
        stop_reason: str | None = None,
        content_block_types: tuple[str, ...] = (),
        text_len: int | None = None,
        parse_ok: bool | None = None,
        error: str | None = None,
    ) -> None:
        self.request_log.write(
            build_request_log(
                effort=effort,
                model=self.config.model,
                endpoint=endpoint,
                http_status=http_status,
                latency_ms=latency_ms,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                thinking_tokens=thinking_tokens,
                image_tokens=image_tokens,
                cache_read_input_tokens=cache_read_input_tokens,
                cache_creation_input_tokens=cache_creation_input_tokens,
                stop_reason=stop_reason,
                content_block_types=content_block_types,
                text_len=text_len,
                parse_ok=parse_ok,
                error=error,
            )
        )

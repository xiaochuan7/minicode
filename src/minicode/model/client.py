"""模型客户端：主链路**唯一**的模型调用入口。

三件事：

1. **凭据单一来源**：请求头、端点、模型、会话标识全部来自仓库配置
   （见 :mod:`minicode.model.config`），客户端内不存在第二个凭据来源；
2. **思考预算（effort）显式**：调用入口必须给档位（五档），省略即报错，
   绝不悄悄按服务端缺省（最高档）跑；
3. **失败如实回传**：模型侧错误原样带回；HTTP 200 但**没有文本输出**判显式失败，
   不呈现成「成功但坐标为空」。

**模型坐标原样透传**：客户端不解释模型给的数值 —— 不乘分母、不按数值大小猜量纲、
不裁剪到某个区间。量纲由标定探针解释，不在本模块。

> 词汇：CONTEXT.md 收的是「**思考预算（effort）**」；工单里说的「思考档位」= effort 的**取值**。
> 本文凡是讲「档位」的地方，都是在讲那个**取值**（low/medium/high/xhigh/max）。
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from .config import ModelConfig
from .effort import EFFORT_VALUES, Effort
from .errors import (
    EffortRequiredError,
    ModelError,
    ModelSideError,
    ModelTransportError,
    NoTextOutputError,
)
from .request_log import RequestLogSink, build_request_log

__all__ = [
    "ANTHROPIC_VERSION",
    "ModelReply",
    "ModelClient",
    "build_headers",
    "build_request_body",
    "parse_response",
    "require_effort",
    "text_of",
    "usage_of",
]

#: Anthropic Messages 协议版本（协议常量，不是凭据，不进仓库配置）。
ANTHROPIC_VERSION = "2023-06-01"

#: 请求超时（秒）。
DEFAULT_TIMEOUT_S = 120.0


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
    #: **命中提示词缓存**的那部分输入 token（服务端把它与 ``input_tokens`` 分开报）。
    #: 上下文总大小 = ``input_tokens`` + ``cache_read_input_tokens``
    #: + ``cache_creation_input_tokens``（工单 M10 的成本曲线按这个口径看）。
    cache_read_input_tokens: int | None = None
    cache_creation_input_tokens: int | None = None
    raw_content: tuple[Mapping[str, Any], ...] = field(default_factory=tuple)

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
    """构造请求头。

    两个头都发（``user-agent`` 与 ``x-opencode-client``）——
    2026-10-20 实测：**只有 ``user-agent`` 被边缘层强制**（去掉它 → Cloudflare
    ``Error 1010`` 拦截，HTTP 状态码是 403）；``x-opencode-client`` 去掉后服务端照收
    （HTTP 200）。另一个头仍然照发，是为了换网关时不丢（详见 ``docs/01-现状.md`` §2 坑 1）。
    """
    return {
        "content-type": "application/json",
        "accept": "application/json",
        "x-api-key": config.api_key,
        "anthropic-version": ANTHROPIC_VERSION,
        "x-opencode-session": config.session_id,
        "user-agent": config.user_agent,
        "x-opencode-client": config.client_header,
    }


def build_request_body(
    *,
    model: str,
    prompt: str,
    effort: Effort | str | None,
    max_tokens: int,
    image_base64: str | None = None,
    images_base64: Sequence[str] = (),
    thinking_disabled: bool = False,
) -> dict[str, Any]:
    """构造请求体（纯函数）。

    * 思考预算用服务端**真正支持**的写法：``output_config.effort``；
      另一种别名写法实测被服务端忽略且更慢，不作为正式开关；
    * ``effort=None`` 时**不带** ``output_config`` —— 这是「省略档位」的形态，
      只给诊断用的回归脚本用；客户端的调用入口不允许走到这里；
    * ``thinking_disabled=True`` 会带上 ``{"thinking": {"type": "disabled"}}``，
      **只为回归坑③存在**（服务端会回 HTTP 400，客户端必须如实回传）。

    **图片可以有多张**（工单 M10 的上下文窗口要把最近几张完整图一起带上）：
    ``image_base64`` 是那张主图，``images_base64`` 是追加的那几张，
    顺序即发送顺序。
    """
    body: dict[str, Any] = {}
    content: list[dict[str, Any]] = []
    for data in (image_base64, *images_base64):
        if not data:
            continue
        content.append(
            {
                "type": "image",
                "source": {"type": "base64", "media_type": "image/png", "data": data},
            }
        )
    content.append({"type": "text", "text": prompt})
    body["model"] = model
    body["max_tokens"] = max_tokens
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


def usage_of(data: Mapping[str, Any]) -> dict[str, Any]:
    """用量明细：输入 / 输出 / 思考 token。**用量解析的唯一一处定义。**

    提示词缓存的命中与写入**分开报**（``cache_read_input_tokens`` /
    ``cache_creation_input_tokens``）——上下文总大小要三个数加起来看，
    只看 ``input_tokens`` 会把「上下文变大了」看漏（工单 M10 AC 5）。
    """
    usage = data.get("usage") or {}
    details = usage.get("output_tokens_details") or {}
    return {
        "input_tokens": usage.get("input_tokens"),
        "output_tokens": usage.get("output_tokens"),
        "thinking_tokens": details.get("thinking_tokens"),
        "cache_read_input_tokens": usage.get("cache_read_input_tokens"),
        "cache_creation_input_tokens": usage.get("cache_creation_input_tokens"),
    }


def parse_response(
    *,
    status: int,
    body_bytes: bytes,
    model: str,
    endpoint: str,
    effort: Effort,
    latency_ms: float,
) -> ModelReply:
    """解析响应（纯函数）。

    * 状态码不是 200 → :class:`ModelSideError`（状态、错误类型、错误文本如实回传）；
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
            usage=usage_of(data) if isinstance(data, Mapping) else None,
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
    )


class ModelClient:
    """模型客户端。

    用法::

        client = ModelClient(load_config(), JsonlRequestLog("runs/model-requests.jsonl"))
        reply = client.complete("只回复两个字：就绪", effort=Effort.LOW)
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

    def complete(
        self,
        prompt: str,
        *,
        effort: Effort | str | None = None,
        max_tokens: int = 2000,
        image_base64: str | None = None,
        images_base64: Sequence[str] = (),
        thinking_disabled: bool = False,
    ) -> ModelReply:
        """发一次请求并返回结果。

        ``effort`` **必须显式给**；省略直接报错（不按服务端缺省的最高档跑）。
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
        )

        started = self._clock()
        try:
            status, raw = self._post(endpoint, headers, body)
        except ModelTransportError as exc:
            self._log(
                effort=selected,
                endpoint=endpoint,
                http_status=None,
                latency_ms=(self._clock() - started) * 1000.0,
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
            )
        except (ModelSideError, NoTextOutputError) as exc:
            usage = exc.usage or {}
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
            error=None,
        )
        return reply

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
        cache_read_input_tokens: int | None = None,
        cache_creation_input_tokens: int | None = None,
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
                cache_read_input_tokens=cache_read_input_tokens,
                cache_creation_input_tokens=cache_creation_input_tokens,
                error=error,
            )
        )

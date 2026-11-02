"""**d3 决策模型客户端**（V4 候选重排）：``/v1/systemone`` 的唯一入口。

d3 与聊天模型是**两种东西**：它不生成文本，只对给定的选项返回
**每个选项的概率 + confidence**（结构化决策）。因此它**不进**
:class:`minicode.model.client.ModelClient`（那是聊天协议的客户端），
这里有它自己的最小客户端。

⚠️ **铁律（实测，2026-11-02）**：d3 用 ``/chat/completions`` 调用会直接 400 ——
必须走 ``{base_url}/systemone``。

**请求形状（实测探明，探针 ``scripts/_v1_radeon_probe.py``）**::

    POST {base_url}/systemone
    {"model": "d3",
     "state": "任务与候选清单的文本",
     "questions": {"<题名>": {"type": "choice",
                              "criteria": {"选项名": "描述或 null", …}}}}

**响应形状（实测）**::

    {"answers": {"<题名>": {"type": "choice", "choice": "<选项名>",
                            "probabilities": {"<选项名>": 0.66, …},
                            "confidence": 0.66}},
     "usage": {...}, "model": "d3", "object": "structured_decision"}

**日志**：每次调用落一行 JSONL（候选数、各选项概率、confidence、耗时、
HTTP 状态、是否 429、错误）—— d3 必须**可审计**，不能是又一个黑盒。
日志里**不含 key**（key 只在请求头里，从不进 URL / state / 日志）。
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from .config import D3Config, _mask_secret

__all__ = [
    "D3CallRecord",
    "D3Client",
    "D3Error",
    "D3Reply",
    "D3LogSink",
    "JsonlD3Log",
]

#: 请求超时（秒）。d3 是小输入小输出的决策调用，比聊天快得多。
DEFAULT_D3_TIMEOUT_S = 120.0


class D3Error(Exception):
    """d3 调用失败（HTTP 非 200 / 响应形状不对 / 传输层坏）。

    ``status`` —— HTTP 状态码（传输层失败 = ``None``）；
    ``rate_limited`` —— 429 与其它失败**分开计数**（配额问题不是模型失败）。
    """

    def __init__(self, message: str, *, status: int | None = None):
        super().__init__(message)
        self.status = status

    @property
    def rate_limited(self) -> bool:
        return self.status == 429


@dataclass(frozen=True)
class D3Reply:
    """一次 d3 决策的结果（概率原样带回，**不解释、不再归一**）。"""

    #: 选项名 → 概率（服务端原样返回的映射）。
    probabilities: dict[str, float]
    #: 服务端的 confidence（原样）。
    confidence: float
    #: 服务端选的选项名（argmax；判断胜负仍以概率为准 —— 见重排层）。
    chosen: str
    latency_ms: float
    http_status: int


class D3LogSink(Protocol):
    """d3 调用日志的出口。"""

    def write(self, record: "D3CallRecord") -> None: ...


@dataclass(frozen=True)
class D3CallRecord:
    """一条 d3 调用日志（**可审计**：V4 的验收就是看它）。"""

    model: str
    endpoint: str
    question: str
    #: 选项数（= 候选数；**选项 ≤ 26** 是 d3 的约束，超了要在调用前裁）。
    option_count: int
    probabilities: dict[str, float]
    confidence: float | None
    chosen: str | None
    latency_ms: float
    http_status: int | None
    #: 429 单独标记（配额问题不与模型失败混算）。
    rate_limited: bool | None
    #: 失败原因（成功 = 空串）。
    error: str
    #: state 的**前 200 字**（排查用；不含 key —— key 不进 state）。
    state_preview: str

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


class JsonlD3Log:
    """落盘实现：每行一条 JSON（**整行一次写 + flush**，与请求日志同一纪律）。"""

    def __init__(self, path):
        self.path = path

    def write(self, record: D3CallRecord) -> None:
        from pathlib import Path

        path = Path(self.path)
        path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(record.to_dict(), ensure_ascii=False) + "\n"
        with path.open("a", encoding="utf-8") as handle:
            handle.write(line)
            handle.flush()


class D3Client:
    """d3 的最小客户端：一次 ``decide`` = 一次 ``/v1/systemone`` POST。"""

    def __init__(
        self,
        config: D3Config,
        log: D3LogSink | None = None,
        *,
        timeout_s: float = DEFAULT_D3_TIMEOUT_S,
        clock=time.perf_counter,
        user_agent: str = (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            " (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
        ),
    ):
        self.config = config
        self.log = log
        self.timeout_s = float(timeout_s)
        self._clock = clock
        self._user_agent = user_agent

    def decide(
        self,
        state: str,
        question: str,
        criteria: Mapping[str, str | None],
    ) -> D3Reply:
        """问一道 choice 题：``criteria`` = 选项名 → 描述（``None`` = 无描述）。

        失败一律 :class:`D3Error`（调用方据此**降级**，不许猜）。
        """
        payload = {
            "model": self.config.model,
            "state": state,
            "questions": {question: {"type": "choice", "criteria": dict(criteria)}},
        }
        headers = {
            "content-type": "application/json",
            "accept": "application/json",
            "authorization": f"Bearer {self.config.api_key}",
            "user-agent": self._user_agent,
        }
        request = urllib.request.Request(
            self.config.endpoint,
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        started = self._clock()
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_s) as response:
                status = int(response.status)
                body = response.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            latency = (self._clock() - started) * 1000.0
            message = exc.read().decode("utf-8", errors="replace")[:500]
            self._log_failure(question, len(criteria), latency, exc.code, message)
            raise D3Error(
                f"d3 HTTP {exc.code}：{message}", status=exc.code
            ) from exc
        except (urllib.error.URLError, OSError, TimeoutError) as exc:
            latency = (self._clock() - started) * 1000.0
            self._log_failure(question, len(criteria), latency, None, str(exc))
            raise D3Error(f"d3 传输失败：{exc}", status=None) from exc
        latency = (self._clock() - started) * 1000.0

        try:
            data = json.loads(body)
            answer = data["answers"][question]
            probabilities = {
                str(option): float(value)
                for option, value in answer["probabilities"].items()
            }
            confidence = float(answer["confidence"])
            chosen = str(answer.get("choice", ""))
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            self._log_failure(question, len(criteria), latency, status, f"响应形状不对：{exc}")
            raise D3Error(
                f"d3 响应形状不对（缺 answers/{question} 或字段类型意外）：{exc}",
                status=status,
            ) from exc
        if self.log is not None:
            self.log.write(
                D3CallRecord(
                    model=self.config.model,
                    endpoint=self.config.endpoint,
                    question=question,
                    option_count=len(criteria),
                    probabilities=probabilities,
                    confidence=confidence,
                    chosen=chosen,
                    latency_ms=round(latency, 1),
                    http_status=status,
                    rate_limited=False,
                    error="",
                    state_preview=state[:200],
                )
            )
        return D3Reply(
            probabilities=probabilities,
            confidence=confidence,
            chosen=chosen,
            latency_ms=latency,
            http_status=status,
        )

    def _log_failure(
        self,
        question: str,
        option_count: int,
        latency_ms: float,
        status: int | None,
        error: str,
    ) -> None:
        if self.log is None:
            return
        self.log.write(
            D3CallRecord(
                model=self.config.model,
                endpoint=self.config.endpoint,
                question=question,
                option_count=option_count,
                probabilities={},
                confidence=None,
                chosen=None,
                latency_ms=round(latency_ms, 1),
                http_status=status,
                rate_limited=(status == 429) if status is not None else None,
                error=error[:500],
                state_preview="",
            )
        )


def describe_config(config: D3Config) -> str:
    """脱敏的一行描述（启动日志用；借 :func:`_mask_secret`）。"""
    return (
        f"d3 配置：endpoint={config.endpoint} model={config.model} "
        f"api_key={_mask_secret(config.api_key)}（来源 {config.source}）"
    )

"""四个坑的**真调 API** 集成回归（单独入口，不被单测命令带跑）。

跑法::

    $env:PYTHONPATH="src"; python -m minicode.model.regressions

这**不是**单测：它真的发请求、真的消耗额度、真的依赖网络与凭据。
单测只在动作执行缝上跑，零网络零费用。

结论口径（工单 M1 的硬要求）：

* 每条回归记 **PASS / FAIL / SKIP**；
* **SKIP 只允许三种环境原因**：网络不可达、凭据缺失或被拒、额度耗尽或限流，
  且必须写明具体原因；其他任何原因一律记 **FAIL**；
* 四条里任何一条 SKIP，整轮结论必须标「**未完成（部分 SKIP）**」，不得记「通过」；
* 不许因为额度紧张就只跑其中一条 —— 四条**全跑**；
* 收尾还要扫一遍请求日志：**「未显式设档位」的调用数必须为 0**，否则整轮记 FAIL。

> 诊断用的「原始请求」（故意少头 / 故意走错通道 / 故意省略档位）**绕开客户端**：
> 客户端的保证正是「不许少头、不许走错通道、不许省略档位」，
> 所以这些症状只能由原始请求复现。它们**不进请求日志**——
> 请求日志记的是**客户端**的请求，那里的档位字段恒非空。
"""

from __future__ import annotations

import base64
import json
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

from minicode.coord.calibrate import build_probe_image, default_targets

from .client import ModelClient, build_headers, build_request_body, text_of, usage_of
from .config import FREE_TIER_PATH_SUFFIX, ModelConfig, load_config, resolve_endpoint
from .effort import Effort
from .errors import (
    ModelConfigError,
    ModelSideError,
    ModelTransportError,
    NoTextOutputError,
)
from .ports import ModelCaller
from .request_log import JsonlRequestLog, RequestLog, count_unset_effort

__all__ = ["Outcome", "Verdict", "main", "run_all"]

#: 请求日志落盘位置（``runs/`` 已被 gitignore）。
LOG_PATH = Path("runs/model-requests.jsonl")

#: 探针图尺寸（纯合成图，不需要真桌面）。
PROBE_WIDTH = 800
PROBE_HEIGHT = 600

#: 「预算偏小」的 token 数：思考链会把它吃光，于是没有文本输出。
#: 实测（2026-10-20）：这张探针图的思考链 ≥127 token，预算 ≤64 时**稳定**被吃光
#: （预算 128 附近会抖：有时思考链刚好写完，输出就出来了）。
TIGHT_MAX_TOKENS = 64
#: 「给思考链留足余量」的 token 数。
ROOMY_MAX_TOKENS = 2000

#: 四个坑的名字（只在这里写一次）。
PIT_MISSING_HEADERS = "坑① 缺必需头"
PIT_WRONG_CHANNEL = "坑② 走错通道"
PIT_DISABLE_THINKING = "坑③ 试图关思考"
PIT_THINKING_BUDGET = "坑④ 缺省档吃光预算"

_PROBE_PROMPT = (
    "这是一张 {w}x{h} 的图片，白底上有 4 个黑色方块，分别位于左上、右上、左下、右下。\n"
    "请给出【右下角】那个方块【中心】的坐标，坐标用 0~1000 归一化（左上角为原点，右下角为 1000）。\n"
    '只输出一个 JSON 对象，不要任何解释：{{"br":[x,y]}}'
)


class Verdict(StrEnum):
    """一条回归的判定。"""

    PASS = "pass"
    FAIL = "fail"
    SKIP = "skip"


@dataclass(frozen=True)
class Outcome:
    """一条回归的结果。"""

    pit: str
    status: Verdict
    detail: str
    evidence: dict[str, Any] = field(default_factory=dict)


class _TeeLog:
    """同时写文件与留内存（用来核对「每发一次请求写一条日志」）。"""

    def __init__(self, *sinks: Any):
        self.sinks = sinks
        self.records: list[RequestLog] = []

    def write(self, record: RequestLog) -> None:
        self.records.append(record)
        for sink in self.sinks:
            sink.write(record)


def _probe_image_base64() -> str:
    """合成探针图（纯标准库，不碰真桌面）。

    复用冻结原型的纯标准库 PNG 编码（``minicode.coord.calibrate``）——
    **只读不改**，冻结原型不改逻辑也不改数值。
    """
    targets = default_targets(PROBE_WIDTH, PROBE_HEIGHT, margin=60, size=40)
    png = build_probe_image(PROBE_WIDTH, PROBE_HEIGHT, targets)
    return base64.b64encode(png).decode()


def _raw_call(
    endpoint: str,
    headers: dict[str, str],
    body: dict[str, Any],
    timeout: float = 120.0,
) -> tuple[int, str]:
    """发一次**原始**请求，返回 (状态码, 响应体文本)。

    HTTP 错误也是一次请求的结果（状态码 + 响应体），不算传输失败；
    网络不可达 / 超时抛 :class:`ModelTransportError`（可记 SKIP 的那类）。
    """
    request = urllib.request.Request(
        endpoint,
        data=json.dumps(body).encode("utf-8"),
        method="POST",
        headers=dict(headers),
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return int(response.status), response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        return int(exc.code), exc.read().decode("utf-8", errors="replace")
    except urllib.error.URLError as exc:
        raise ModelTransportError(f"请求 {endpoint} 失败：{exc.reason}") from exc
    except TimeoutError as exc:
        raise ModelTransportError(f"请求 {endpoint} 超时（{timeout}s）") from exc


def _is_edge_interception(status: int, raw: str) -> bool:
    """这是不是**边缘层拦截**（Cloudflare），而不是模型侧错误体？

    实测（2026-10-20）：边缘拦截的 **HTTP 状态码是 403**，响应体里带
    Cloudflare 的 ``Error 1010``（工单原文写的是「HTTP 1010」——那是**错误编号**，
    不是状态码）。判定按**错误编号 1010** 认，不按状态码认。
    """
    if status == 1010:
        # 历史形态：直接拿 1010 当状态码返回。留着以防网关换实现。
        return True
    lowered = raw.lower()
    return "1010" in raw and ("cloudflare" in lowered or "error 1010" in lowered)


def _environment_skip(status: int, raw: str) -> str | None:
    """这是不是**允许 SKIP 的环境原因**（凭据被拒 / 额度耗尽或限流）？

    ⚠️ **边缘拦截不算环境原因**：实测边缘拦截就是 HTTP 403，
    它是**服务端行为**，必须按 FAIL 记，不能降级成 SKIP。
    """
    if _is_edge_interception(status, raw):
        return None
    if status in (401, 403):
        return f"凭据缺失或被拒：HTTP {status}（{raw[:120]}）"
    if status == 429:
        return f"额度耗尽或限流：HTTP {status}（{raw[:120]}）"
    lowered = raw.lower()
    if "quota" in lowered or "rate limit" in lowered or "insufficient balance" in lowered:
        return f"额度耗尽或限流：{raw[:120]}"
    return None


def _network_skip(exc: Exception) -> str | None:
    """网络不可达（可 SKIP 的第三种环境原因）。"""
    if isinstance(exc, urllib.error.URLError):
        return f"网络不可达：{exc.reason}"
    if isinstance(exc, ModelTransportError):
        return f"网络不可达：{exc}"
    return None


# ----------------------------------------------------------------------
# 坑① 缺必需头 → 边缘层拦截
# ----------------------------------------------------------------------


def pit_missing_required_headers(config: ModelConfig, client: ModelCaller) -> Outcome:
    headers = build_headers(config)
    body = build_request_body(
        model=config.model,
        prompt="只回复两个字：就绪",
        effort=Effort.LOW,
        max_tokens=64,
    )
    evidence: dict[str, Any] = {}

    # (1) 少 user-agent → 必须被边缘层拦截（不是模型侧错误体）。
    broken = dict(headers)
    broken.pop("user-agent", None)
    status, raw = _raw_call(config.endpoint, broken, body)
    intercepted = _is_edge_interception(status, raw)
    evidence["without_user_agent"] = {
        "status": status,
        "edge_intercepted": intercepted,
        "body": raw[:200],
    }
    if not intercepted:
        reason = _environment_skip(status, raw)
        if reason is not None:
            return Outcome(PIT_MISSING_HEADERS, Verdict.SKIP, reason, evidence)
        return Outcome(
            PIT_MISSING_HEADERS,
            Verdict.FAIL,
            f"去掉 user-agent 后返回 HTTP {status}，期望被边缘层拦截（Cloudflare Error 1010）",
            evidence,
        )

    # (2) 少 x-opencode-client —— 实测服务端**不强制**这个头（HTTP 200）。
    #     这里只记录，不作为 PASS 条件（工单 AC2 的「两次都 1010」与实测不符，见 review）。
    broken = dict(headers)
    broken.pop("x-opencode-client", None)
    status, raw = _raw_call(config.endpoint, broken, body)
    evidence["without_x_opencode_client"] = {
        "status": status,
        "edge_intercepted": _is_edge_interception(status, raw),
        "body": raw[:200],
    }

    # (3) 两个头都在 → HTTP 200 且拿到正常文本输出。
    reply = client.complete("只回复两个字：就绪", effort=Effort.LOW, max_tokens=64)
    evidence["with_both_headers"] = {
        "status": reply.http_status,
        "text": reply.text[:80],
    }
    if not reply.text:
        return Outcome(
            PIT_MISSING_HEADERS, Verdict.FAIL, "两个头都在时没有拿到文本输出", evidence
        )
    return Outcome(
        PIT_MISSING_HEADERS,
        Verdict.PASS,
        "少 user-agent → 边缘拦截（Cloudflare Error 1010）；两个头都在 → HTTP 200 且有文本输出。"
        "（少 x-opencode-client 实测 HTTP 200，服务端当前不强制它；工单 AC2 措辞与此不符）",
        evidence,
    )


# ----------------------------------------------------------------------
# 坑② 走错通道 → FreeTierError
# ----------------------------------------------------------------------


def pit_wrong_channel(config: ModelConfig, client: ModelCaller) -> Outcome:
    evidence: dict[str, Any] = {}
    base = config.base_url.rstrip("/")
    if not base.endswith(FREE_TIER_PATH_SUFFIX):
        return Outcome(
            PIT_WRONG_CHANNEL, Verdict.FAIL, f"配置的基础地址 {base} 不是免费档通道", evidence
        )
    wrong_base = base[: -len(FREE_TIER_PATH_SUFFIX)]

    # 「选哪条」必须是可测行为：客户端**不许**放行非免费档地址。
    try:
        resolve_endpoint(wrong_base)
    except ModelConfigError as exc:
        evidence["client_refuses_wrong_channel"] = str(exc)
    else:
        return Outcome(
            PIT_WRONG_CHANNEL,
            Verdict.FAIL,
            f"客户端竟然放行了非免费档地址 {wrong_base}",
            evidence,
        )

    headers = build_headers(config)
    body = build_request_body(
        model=config.model,
        prompt="只回复两个字：就绪",
        effort=Effort.LOW,
        max_tokens=64,
    )
    status, raw = _raw_call(f"{wrong_base}/v1/messages", headers, body)
    evidence["wrong_channel"] = {"status": status, "body": raw[:300]}
    if "FreeTierError" not in raw:
        reason = _environment_skip(status, raw)
        if reason is not None:
            return Outcome(PIT_WRONG_CHANNEL, Verdict.SKIP, reason, evidence)
        return Outcome(
            PIT_WRONG_CHANNEL,
            Verdict.FAIL,
            f"打非免费档通道 {wrong_base} 返回 HTTP {status}，响应体里没有 FreeTierError",
            evidence,
        )

    try:
        reply = client.complete("只回复两个字：就绪", effort=Effort.LOW, max_tokens=64)
    except ModelSideError as exc:
        reason = _environment_skip(exc.status, exc.body or exc.message)
        if reason is not None:
            return Outcome(PIT_WRONG_CHANNEL, Verdict.SKIP, reason, evidence)
        raise
    evidence["free_tier_channel"] = {"status": reply.http_status, "text": reply.text[:80]}
    if reply.http_status != 200:
        return Outcome(
            PIT_WRONG_CHANNEL, Verdict.FAIL, "换回免费档通道没有拿到 HTTP 200", evidence
        )
    return Outcome(
        PIT_WRONG_CHANNEL,
        Verdict.PASS,
        "非免费档 → FreeTierError；免费档 → HTTP 200",
        evidence,
    )


# ----------------------------------------------------------------------
# 坑③ 试图关思考 → HTTP 400
# ----------------------------------------------------------------------


def pit_disable_thinking(config: ModelConfig, client: ModelCaller) -> Outcome:
    evidence: dict[str, Any] = {}
    try:
        client.complete(
            "只回复两个字：就绪",
            effort=Effort.LOW,
            max_tokens=64,
            thinking_disabled=True,
        )
    except ModelSideError as exc:
        evidence["model_side_error"] = {
            "status": exc.status,
            "error_type": exc.error_type,
            "message": exc.message[:300],
        }
        reason = _environment_skip(exc.status, exc.body or exc.message)
        if reason is not None:
            return Outcome(PIT_DISABLE_THINKING, Verdict.SKIP, reason, evidence)
        if exc.status != 400:
            return Outcome(
                PIT_DISABLE_THINKING, Verdict.FAIL, f"期望 HTTP 400，实际 {exc.status}", evidence
            )
        if exc.error_type != "invalid_request_error":
            return Outcome(
                PIT_DISABLE_THINKING,
                Verdict.FAIL,
                f"期望错误类型 invalid_request_error，实际 {exc.error_type}",
                evidence,
            )
        return Outcome(
            PIT_DISABLE_THINKING,
            Verdict.PASS,
            "HTTP 400 / invalid_request_error，客户端如实回传（未改写参数重试）",
            evidence,
        )
    return Outcome(
        PIT_DISABLE_THINKING, Verdict.FAIL, "发关闭思考参数居然没有报错", evidence
    )


# ----------------------------------------------------------------------
# 坑④ 缺省档吃光 token 预算 → 没有坐标输出
# ----------------------------------------------------------------------


def pit_thinking_budget(config: ModelConfig, client: ModelCaller) -> Outcome:
    evidence: dict[str, Any] = {}
    image_base64 = _probe_image_base64()
    prompt = _PROBE_PROMPT.format(w=PROBE_WIDTH, h=PROBE_HEIGHT)

    # (1) 省略档位 + 预算偏小 → HTTP 200 但没有文本输出（原始请求，故意绕开客户端）。
    body = build_request_body(
        model=config.model,
        prompt=prompt,
        effort=None,
        max_tokens=TIGHT_MAX_TOKENS,
        image_base64=image_base64,
    )
    status, raw = _raw_call(config.endpoint, build_headers(config), body)
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        parsed = {}
    text = text_of(parsed) if isinstance(parsed, dict) else ""
    evidence["omitted_effort"] = {
        "status": status,
        "max_tokens": TIGHT_MAX_TOKENS,
        "text": text[:120],
        "usage": usage_of(parsed) if isinstance(parsed, dict) else {},
        "body": raw[:300],
    }
    reason = _environment_skip(status, raw)
    if reason is not None:
        return Outcome(PIT_THINKING_BUDGET, Verdict.SKIP, reason, evidence)
    if status != 200:
        return Outcome(
            PIT_THINKING_BUDGET,
            Verdict.FAIL,
            f"省略档位 + 小预算没有拿到 HTTP 200（实际 {status}）",
            evidence,
        )
    if text:
        return Outcome(
            PIT_THINKING_BUDGET,
            Verdict.FAIL,
            "省略档位 + 小预算竟然拿到了文本输出，症状没复现（预算不够小？）",
            evidence,
        )

    # (2) 客户端显式给最高档 + 小预算 → 必须显式失败（不是「成功但坐标为空」）。
    try:
        client.complete(
            prompt,
            effort=Effort.MAX,
            max_tokens=TIGHT_MAX_TOKENS,
            image_base64=image_base64,
        )
    except NoTextOutputError as exc:
        evidence["client_no_text_output"] = {"usage": exc.usage, "message": exc.message}
        thinking = (exc.usage or {}).get("thinking_tokens") or 0
        if not thinking:
            return Outcome(
                PIT_THINKING_BUDGET,
                Verdict.FAIL,
                "客户端显式失败了，但用量明细里看不到思考 token 吃预算",
                evidence,
            )
    else:
        return Outcome(
            PIT_THINKING_BUDGET,
            Verdict.FAIL,
            "客户端在「没有文本输出」时没有显式失败",
            evidence,
        )

    # (3) 客户端显式低档 + 留足余量 → HTTP 200 且拿到可解析坐标。
    reply = client.complete(
        prompt,
        effort=Effort.LOW,
        max_tokens=ROOMY_MAX_TOKENS,
        image_base64=image_base64,
    )
    try:
        payload = reply.json_payload()
    except Exception as exc:  # 模型没给可解析坐标 → 显式失败，不当成功
        evidence["explicit_low_effort"] = {"status": reply.http_status, "text": reply.text[:120]}
        return Outcome(
            PIT_THINKING_BUDGET,
            Verdict.FAIL,
            f"显式低档 + 留足余量后没有拿到可解析的坐标 JSON：{exc}",
            evidence,
        )
    evidence["explicit_low_effort"] = {
        "status": reply.http_status,
        "text": reply.text[:120],
        "payload": payload,
    }
    if not isinstance(payload, dict) or "br" not in payload:
        return Outcome(
            PIT_THINKING_BUDGET,
            Verdict.FAIL,
            "显式低档 + 留足余量后没有拿到可解析的坐标 JSON",
            evidence,
        )
    return Outcome(
        PIT_THINKING_BUDGET,
        Verdict.PASS,
        "省略档位 → 没有文本输出（客户端显式失败）；显式低档 + 留余量 → 拿到可解析坐标",
        evidence,
    )


# ----------------------------------------------------------------------
# 跑一轮
# ----------------------------------------------------------------------

_PITS = (
    (PIT_MISSING_HEADERS, pit_missing_required_headers),
    (PIT_WRONG_CHANNEL, pit_wrong_channel),
    (PIT_DISABLE_THINKING, pit_disable_thinking),
    (PIT_THINKING_BUDGET, pit_thinking_budget),
)


def run_all(config: ModelConfig, client: ModelCaller) -> list[Outcome]:
    """四条回归**全跑**（不许只跑其中一条）。"""
    outcomes: list[Outcome] = []
    for name, pit in _PITS:
        try:
            outcomes.append(pit(config, client))
        except Exception as exc:  # 任何意外都如实记下来，不吞
            reason = _network_skip(exc)
            if reason is not None:
                outcomes.append(Outcome(name, Verdict.SKIP, reason))
            else:
                outcomes.append(Outcome(name, Verdict.FAIL, f"回归执行异常：{exc!r}"))
    return outcomes


def _log_summary(path: Path) -> dict[str, Any]:
    """扫请求日志：档位字段恒非空、恒是五档之一。"""
    if not path.is_file():
        return {"lines": 0, "unset_effort": 0, "note": "日志文件不存在"}
    records = [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    return {"lines": len(records), "unset_effort": count_unset_effort(records)}


def main(argv: list[str] | None = None) -> int:
    """跑一轮四条回归，打印结论。

    退出码：0 = 全 PASS，1 = 有 FAIL，2 = 有 SKIP。
    """
    try:
        config = load_config()
    except ModelConfigError as exc:
        print(f"[SKIP] 全部四条：{exc}")
        print("结论：未完成（部分 SKIP）")
        return 2

    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    tee = _TeeLog(JsonlRequestLog(LOG_PATH))
    client = ModelClient(config, tee)

    print("=" * 72)
    print("M1 · 四个坑的真调 API 集成回归")
    print("=" * 72)
    print(f"配置来源：{config.source}")
    print(f"端点：{config.endpoint} | 模型：{config.model}")
    print(f"请求日志：{LOG_PATH}")
    print()

    outcomes = run_all(config, client)
    for outcome in outcomes:
        print(f"[{outcome.status.value.upper()}] {outcome.pit} —— {outcome.detail}")
        for key, value in outcome.evidence.items():
            print(f"        {key}: {value}")
    print()

    summary = _log_summary(LOG_PATH)
    print(f"请求日志：{summary}")
    print(f"客户端请求数：{len(tee.records)}")

    statuses = [outcome.status for outcome in outcomes]
    if Verdict.FAIL in statuses:
        print("结论：FAIL")
        return 1
    if Verdict.SKIP in statuses:
        print("结论：未完成（部分 SKIP）")
        return 2
    if summary["unset_effort"]:
        print("结论：FAIL（请求日志里有未显式设档位的调用）")
        return 1
    print("结论：通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

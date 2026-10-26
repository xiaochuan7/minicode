"""模型请求日志：**每发一次请求写一条**。

字段（工单 M1 要求的那几项一个不少）：档位、模型、端点、HTTP 状态、耗时、
输入 token、输出 token、思考 token 数。

**形态字段**（回答可不可用）：``stop_reason`` / ``content_block_types`` /
``text_len`` / ``parse_ok`` —— 前两个来自**响应体**（只有响应体里有），
后两个让「这次回答能不能用」从**推断**变成**直读**。
它们**默认可空**（``None`` = 不知道 / 没人给），不改变既有字段的语义。

「档位字段值是五档之一且非空」是**可判定**的：扫全部日志，
未显式设档位的调用数必须是 0（见 :func:`count_unset_effort`）。
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass
from fractions import Fraction
from math import ceil
from pathlib import Path
from typing import Any, Protocol

from .effort import EFFORT_VALUES, Effort

__all__ = [
    "RequestLog",
    "RequestLogSink",
    "InMemoryRequestLog",
    "JsonlRequestLog",
    "build_request_log",
    "count_unset_effort",
    "estimate_image_tokens",
    "estimate_openai_image_tokens",
    "png_image_size",
    "total_input_tokens",
]

#: 图像 token 的 patch 边长（**像素**）。
#:
#: 来源：Anthropic 的图像分块规则（官方 quickstarts 里的 computer-use demo 走的就是
#: 这套切块）—— ``ceil(W/28) × ceil(H/28)``。
#: ⚠️ **【二手】这条规则是否适用于本机用的 MiniMax 端点尚未验证**：
#: 它是**反查服务端实际尺寸**时用的**模型**，不是真值来源 ——
#: 反查不出就如实记 ``unknown``，**不许拿它当已知的分母**。
IMAGE_PATCH_PX = 28

#: OpenAI 兼容视觉的 tile 边长（**像素**）。
OPENAI_IMAGE_TILE_PX = 512
#: 每个 tile 的 token 数。
OPENAI_IMAGE_TILE_TOKENS = 170
#: 基础 token 数（每张图都要加一次）。
OPENAI_IMAGE_BASE_TOKENS = 85
#: 缩图方框的边长（像素）：图先缩进 2048×2048。
OPENAI_IMAGE_MAX_EDGE_PX = 2048
#: 第二段缩图的目标短边（像素）。
OPENAI_IMAGE_SHORT_EDGE_PX = 768
#: 两边都 ≤ 这个像素数的图按最小档计（只有基础 token）。
OPENAI_IMAGE_SMALL_EDGE_PX = 512


@dataclass(frozen=True)
class RequestLog:
    """一条模型请求的日志。"""

    effort: str
    model: str
    endpoint: str
    http_status: int | None
    latency_ms: float
    input_tokens: int | None = None
    output_tokens: int | None = None
    thinking_tokens: int | None = None
    #: **图像占的输入 token 数**（与 ``input_tokens`` 分开记）。
    #:
    #: 它按 28 px patch 规则对**实际发出的那张图**算出（见 :func:`estimate_image_tokens`）——
    #: **这是估计值，不是服务端报出来的数**。服务端若把图缩小过，实际占用会**小于**它，
    #: 差值正是「服务端缩图」的证据（反查用）。
    image_tokens: int | None = None
    #: 提示词缓存：命中（读）与写入（建）分别多少输入 token。
    #: 上下文总大小 = 三个输入 token 相加（工单 M10 的成本曲线按这个口径看）。
    cache_read_input_tokens: int | None = None
    cache_creation_input_tokens: int | None = None
    error: str | None = None
    #: 响应体里的 ``stop_reason``（Anthropic 协议字段：``end_turn`` / ``max_tokens`` …）。
    #:
    #: **形态判定直读它** —— 以前只能用 ``output_tokens == max_tokens`` 反推
    #: 「这次没有文本输出」（反推有误差，且认不出「正常结束但文本为空」）。
    stop_reason: str | None = None
    #: 响应体 ``content[]`` 里各块的 ``type``（按顺序，如 ``["thinking"]``）。
    #:
    #: 只有 ``thinking`` 块 → 思考链吃光了预算 → **这次没有文本**。
    content_block_types: tuple[str, ...] = ()
    #: 回答文本的**长度**：0 = 没有文本；``None`` = 不知道（没拿到回答）。
    text_len: int | None = None
    #: 这次回答**是否解析得出坐标**。
    #:
    #: **由调用方给** —— 客户端层不知道解析规则（那是 ground 层的事）→ 缺省 ``None``。
    #: ``None`` 是**合法值**：``text_len > 0`` 且 ``parse_ok is None`` 照样落盘
    #: （「有文本但没人判定过能不能解析」与「解析失败」是两件事）。
    parse_ok: bool | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def total_input_tokens(self) -> int | None:
        """上下文总大小（见 :func:`total_input_tokens`）。"""
        return total_input_tokens(
            self.input_tokens,
            self.cache_read_input_tokens,
            self.cache_creation_input_tokens,
        )


def total_input_tokens(
    input_tokens: int | None,
    cache_read_input_tokens: int | None,
    cache_creation_input_tokens: int | None,
) -> int | None:
    """上下文总大小 = 三个输入 token 相加。**本仓的口径定义**（唯一一处）。

    服务端把「本次新算的」「命中缓存读到的」「写进缓存的」分开报；
    只看其中一个会看漏「上下文变大了」（工单 M10 AC 5 的成本曲线按总大小看）。
    三个都没报 → ``None``（不编一个 0）。
    """
    parts = (input_tokens, cache_read_input_tokens, cache_creation_input_tokens)
    if all(part is None for part in parts):
        return None
    return sum(part or 0 for part in parts)


class RequestLogSink(Protocol):
    """请求日志的出口（旁路 sink，不进主返回类型）。"""

    def write(self, record: RequestLog) -> None:
        """写一条请求日志。"""
        ...


class InMemoryRequestLog:
    """内存实现：单测与回归用。"""

    def __init__(self) -> None:
        self.records: list[RequestLog] = []

    def write(self, record: RequestLog) -> None:
        self.records.append(record)


class JsonlRequestLog:
    """落盘实现：每行一条 JSON。"""

    def __init__(self, path: Path | str):
        self.path = Path(path)

    def write(self, record: RequestLog) -> None:
        """追加一条日志：**整行一次写 + 立刻 flush**（单次 write + flush）。

        ⚠️ **为什么要显式 flush**：一行日志可以很长（``error`` 里可能带整段响应体），
        文本模式的写缓冲会把它切成几块**分次**写出去 —— 两个进程同时写同一个文件时，
        行与行就会**交错**（本仓历史上 5259 条里撕过 1 条，差 1 就是因此）。
        这里把**整行**先拼成一个字符串，再**一次** ``write`` 出去并立刻 ``flush``
        → 一行要么完整落盘，要么没落。

        ⚠️ **不要改成二进制写**：本机实测（8 线程 × 20 行 × 100 KB，同一个文件）
        二进制追加**丢整行**丢得更多（96/160 行，其中还有 2 行撕裂），
        文本追加是 156/160 行 —— Windows 上 ``O_APPEND`` 在两种模式下的语义不一样。
        """
        self.path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(record.to_dict(), ensure_ascii=False) + "\n"
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(line)
            handle.flush()


def estimate_image_tokens(width: int, height: int, *, patch_px: int = IMAGE_PATCH_PX) -> int:
    """按 28 px patch 规则估一张图的 token 数（``ceil(W/28) × ceil(H/28)``）。

    ⚠️ 这是**估计**，不是真值：规则本身来自 Anthropic 的实现，
    **是否适用于本机用的 MiniMax 端点未验证**。它的用途是**反查**
    （「服务端实际看到的那张图有多大」），反查不出就如实记 ``unknown``。
    """
    if width <= 0 or height <= 0:
        return 0
    return -(-int(width) // patch_px) * -(-int(height) // patch_px)


def estimate_openai_image_tokens(width: int, height: int) -> int:
    """按 OpenAI 兼容视觉的 **tile 规则**估一张图的 token 数。

    规则（OpenAI 官方文档的计费口径，**估计**不是真值）：

    1. 先把图缩进 2048×2048 的方框（保持长宽比，只缩不放）；
    2. 再把**短边**缩到 768 px（只缩不放：短边本来就 ≤768 就不动）；
    3. 按 512×512 的 tile 数计费：``85 + 170 × tiles``；
    4. 两边都 ≤512 的小图直接按最小档 85 计。

    缩放用 :class:`fractions.Fraction` 做精确分数运算 —— 浮点会把
    ``2048 / 512 = 4`` 算成 4.0000000001，向上取整就多算一个 tile。
    """
    if width <= 0 or height <= 0:
        return 0
    if width <= OPENAI_IMAGE_SMALL_EDGE_PX and height <= OPENAI_IMAGE_SMALL_EDGE_PX:
        return OPENAI_IMAGE_BASE_TOKENS
    scaled_w = Fraction(int(width))
    scaled_h = Fraction(int(height))
    scale = min(
        Fraction(1),
        Fraction(OPENAI_IMAGE_MAX_EDGE_PX) / max(scaled_w, scaled_h),
    )
    scaled_w *= scale
    scaled_h *= scale
    scale = min(
        Fraction(1),
        Fraction(OPENAI_IMAGE_SHORT_EDGE_PX) / min(scaled_w, scaled_h),
    )
    scaled_w *= scale
    scaled_h *= scale
    tiles = ceil(scaled_w / OPENAI_IMAGE_TILE_PX) * ceil(
        scaled_h / OPENAI_IMAGE_TILE_PX
    )
    return OPENAI_IMAGE_BASE_TOKENS + OPENAI_IMAGE_TILE_TOKENS * tiles


def png_image_size(png_bytes: bytes) -> tuple[int, int] | None:
    """从 PNG 字节里读出尺寸（读不出就 ``None`` —— 不猜）。

    PNG 的 IHDR 一定在文件头：签名 8 字节 + 长度 4 + ``"IHDR"`` 4 + 宽 4 + 高 4。
    """
    if len(png_bytes) < 24 or not png_bytes.startswith(b"\x89PNG\r\n\x1a\n"):
        return None
    if png_bytes[12:16] != b"IHDR":
        return None
    width = int.from_bytes(png_bytes[16:20], "big")
    height = int.from_bytes(png_bytes[20:24], "big")
    if width <= 0 or height <= 0:
        return None
    return width, height


def build_request_log(
    *,
    effort: Effort | str,
    model: str,
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
) -> RequestLog:
    """构造一条请求日志（纯函数）。"""
    return RequestLog(
        effort=str(effort),
        model=model,
        endpoint=endpoint,
        http_status=http_status,
        latency_ms=latency_ms,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        thinking_tokens=thinking_tokens,
        image_tokens=image_tokens,
        cache_read_input_tokens=cache_read_input_tokens,
        cache_creation_input_tokens=cache_creation_input_tokens,
        error=error,
        stop_reason=stop_reason,
        content_block_types=content_block_types,
        text_len=text_len,
        parse_ok=parse_ok,
    )


def count_unset_effort(records: Iterable[RequestLog | Mapping[str, Any]]) -> int:
    """统计「未显式设档位」的调用数。

    判据：档位字段为空，或取值不在五档之内。工单要求这个数**恒为 0**。
    """
    count = 0
    for record in records:
        effort = record["effort"] if isinstance(record, Mapping) else record.effort
        if not effort or effort not in EFFORT_VALUES:
            count += 1
    return count

"""V1 探针：Radeon 网关（视觉臂 Qwen3.8-27B）与 d3 /v1/systemone 的**连通性实测**。

用法（仓库根目录）::

    $env:PYTHONPATH="src"; python -I scripts/_v1_radeon_probe.py --qwen
    $env:PYTHONPATH="src"; python -I scripts/_v1_radeon_probe.py --d3

凭据只从仓库 ``.env`` 读（``MINICODE_VISION_*`` / ``MINICODE_D3_*``）；
本脚本与它的输出**不含任何 key**。

实测目标（V1 验收：图片入参格式要实测，不照抄 opencode 的形状）：

* Qwen 臂：OpenAI 兼容 ``/chat/completions`` + ``image_url``（base64 data URL），
  小图先通、再试真实截图像素级回复，确认返回结构与文本可读；
* d3 臂：``/v1/systemone`` 的 ``{model, state, questions}``，确认概率返回形状。
"""

from __future__ import annotations

import argparse
import base64
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "src"))

from minicode.model.client import ModelClient  # noqa: E402
from minicode.model.config import parse_env_file  # noqa: E402
from minicode.model.request_log import InMemoryRequestLog  # noqa: E402

sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def _env() -> dict[str, str]:
    return parse_env_file(_ROOT / ".env")


def _tiny_png_base64() -> str:
    """一张 64×64 的实心 PNG（真实 PNG 字节，能被服务端解码）。"""
    import struct
    import zlib

    width = height = 64
    raw = b"".join(
        b"\x00" + bytes([200, 30, 90] * width) for _ in range(height)
    )

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    png = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )
    return base64.b64encode(png).decode()


def probe_qwen() -> int:
    values = _env()
    base = values.get("MINICODE_VISION_BASE_URL", "")
    model = values.get("MINICODE_VISION_MODEL", "")
    print(f"=== Qwen 臂探针：base={base} model={model} ===")
    client = ModelClient(
        load_vision(values), InMemoryRequestLog(), channel="vision"
    )

    # ① 纯文本（确认网关、鉴权、头都对）。
    started = time.time()
    reply = client.complete(
        "只回复两个字：就绪",
        effort="low",
        max_tokens=8192,
    )
    print(f"[① 纯文本] {time.time() - started:.1f}s 文本={reply.text!r}")

    # ② 小图（实测 image_url / data URL 形状是否被接受）。
    started = time.time()
    reply = client.complete(
        "这张图是什么颜色？用一句话回答（图里只有一个纯色方块）。",
        effort="low",
        max_tokens=8192,
        image_base64=_tiny_png_base64(),
    )
    print(f"[② 小图] {time.time() - started:.1f}s 文本={reply.text!r}")

    # ③ 坐标语义探针（grounding 提示词的最小形态，看回答是否可解析）。
    started = time.time()
    reply = client.complete(
        "在这张 64x64 像素的图里，红色方块的中心坐标是多少？"
        "（坐标是这张图的像素）只回复 JSON：{\"x\": 数字, \"y\": 数字}",
        effort="low",
        max_tokens=8192,
        image_base64=_tiny_png_base64(),
    )
    print(f"[③ 坐标] {time.time() - started:.1f}s 文本={reply.text!r}")
    print("=== Qwen 臂探针完成：三连全通 ===")
    return 0


def load_vision(values: dict[str, str]):
    from minicode.model.config import ModelConfig

    return ModelConfig(
        base_url=values["MINICODE_VISION_BASE_URL"],
        api_key=values["MINICODE_VISION_API_KEY"],
        model=values["MINICODE_VISION_MODEL"],
        protocol=values.get("MINICODE_VISION_PROVIDER", "openai"),
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            " (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
        ),
        origins={"api_key": "MINICODE_VISION_API_KEY"},
        source=str(_ROOT / ".env"),
    )


def _d3_post(values: dict[str, str], questions: dict, state: str) -> dict:
    """对 /v1/systemone 发一次请求，返回解析后的 JSON（HTTP 错误如实抛出）。"""
    base = values.get("MINICODE_D3_BASE_URL", "").rstrip("/")
    model = values.get("MINICODE_D3_MODEL", "d3")
    url = f"{base}/systemone"
    headers = {
        "content-type": "application/json",
        "accept": "application/json",
        "authorization": f"Bearer {values['MINICODE_D3_API_KEY']}",
        "user-agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            " (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
        ),
    }
    payload = {"model": model, "state": state, "questions": questions}
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    started = time.time()
    with urllib.request.urlopen(request, timeout=120) as response:
        body = json.loads(response.read().decode("utf-8", errors="replace"))
    print(f"[d3] {time.time() - started:.1f}s 返回：")
    print(json.dumps(body, ensure_ascii=False, indent=2)[:3000])
    return body


def probe_d3(questions_json: str = "") -> int:
    """d3 /v1/systemone：确认 ``questions`` 的真实形状与返回结构。

    默认按三种候选形状依次试（首轮实测：array → Zod 400；对象缺
    ``choice.type`` → 400 "Field required" → 形状是
    ``questions.choice.type`` 且必填）。
    给了 ``questions_json``（JSON 字面量）就只试那一种。
    """
    values = _env()
    state = (
        "任务：点击屏幕上的「设置」图标。"
        "候选清单：1. 文字为 Settings 的按钮（左上）；"
        "2. 无文字的齿轮图标（右下）；3. 文字为 Help 的按钮（中部）。"
    )
    if questions_json:
        shapes = [("自定义", json.loads(questions_json))]
    else:
        # **实测确认的形状**（2026-11-02，逐层 Zod/BadRequest 400 探出来）：
        # ``questions`` 是 object（键 = 题名）；每题 ``{"type": "choice",
        # "criteria": {选项名: 描述|null}}`` —— 选项由 criteria 的键给出，
        # 不存在单独的 options/question 字段。
        shapes = [
            (
                "questions.choice.type=choice+criteria（实测确认）",
                {
                    "choice": {
                        "type": "choice",
                        "criteria": {
                            "1": "文字为 Settings 的按钮，位于左上",
                            "2": "无文字的齿轮图标，位于右下",
                            "3": "文字为 Help 的按钮，位于中部",
                        },
                    }
                },
            ),
        ]
    for label, questions in shapes:
        print(f"=== d3 探针（{label}）===")
        try:
            _d3_post(values, questions, state)
        except urllib.error.HTTPError as exc:
            print(
                f"[d3/{label}] HTTP {exc.code}："
                f"{exc.read().decode('utf-8', errors='replace')[:400]}"
            )
            continue
        except urllib.error.URLError as exc:
            print(f"[d3/{label}] 传输失败：{exc}")
            continue
        return 0
    print("=== d3 探针：全部形状失败 ===")
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(description="V1 Radeon/d3 连通性探针")
    parser.add_argument("--qwen", action="store_true", help="探测 Qwen3.8-27B 视觉臂")
    parser.add_argument("--d3", action="store_true", help="探测 d3 /v1/systemone")
    parser.add_argument(
        "--d3-questions",
        default="",
        help="d3 的 questions 形状（JSON 字面量；不给就按内置候选形状依次试）",
    )
    args = parser.parse_args()
    if not args.qwen and not args.d3:
        args.qwen = True
    code = 0
    if args.qwen:
        code = probe_qwen()
    if args.d3:
        code = probe_d3(args.d3_questions) or code
    return code


if __name__ == "__main__":
    raise SystemExit(main())

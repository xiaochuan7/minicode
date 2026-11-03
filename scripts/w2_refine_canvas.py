"""W2.2 精定位量化（自建 canvas 页，真值可控）：粗点偏移 0/50/100/200 px 的救回扫描。

**页面**：本地 HTML，<canvas> 1000×700 CSS px，22 个目标分三档密度：

* ``icon_small`` ×8 —— 12/16 px 的小图标（≤16px 档）；
* ``button`` ×8 —— 140×44 的文字按钮（中等档）；
* ``list_row`` ×6 —— 26 px 行高的密集清单里的指定行（密集档）。

**真值从截图里量出来**（不信窗口定位）：画布顶边/左边各画一条 2 CSS px 的
品红条 → 从整屏物理帧里扫描品红行程 → 画布原点与缩放（scale_x/scale_y）
全部**实测**；每个目标的 GT（物理像素）= 实测原点 + CSS 坐标 × 实测缩放。

**实验**：每个目标 × 粗点偏移 {0, 50, 100, 200} px（>0 的方向按目标轮转 8
方位）→ 生产两级链路（``TwoStageGrounder``）跑精定位一级（粗定位一级被注入
应答替换，同一缝隙见 ``w2_refine_quant.py``）→ 量落点。

**判据**（主口径 = 官方 bbox 包含，副口径 = bbox+8px 可点容差，V5 同款）：

* ``hit_strict`` —— 落点落在目标 bbox 内；
* ``hit_pad8`` —— 落点落在 bbox 外扩 8px 内；
* 「救回」—— 偏移 >0 时，粗点本身在 bbox 外，精定位落点回到 bbox（或 8px
  容差）内的比例；
* 误差 —— 落点与 GT 中心的距离（中位/均值/p90）。

**真机纪律**：桌面锁只包「spawn 页面 → 抓帧 → 关窗」那一段（标定探针自建
探针图不需要真机；模型调用只吃 CPU/网络）；只操作自己 spawn 的实例；
关窗三道闸（同类过滤 / 类名∈预期集合 / 关前复核类名）。

用法（仓库根目录）::

    python -I scripts/w2_refine_canvas.py --out runs/w2
"""

from __future__ import annotations

import argparse
import base64
import ctypes
import json
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "src"))
sys.path.insert(0, str(_ROOT / "scripts"))

from w2_refine_quant import (  # noqa: E402
    CoarseInjectingClient,
    _canned_coarse,
    _ThreadLocalRealClient,
)
from w3_probe import find_new_window, visible_windows  # noqa: E402

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

WM_CLOSE = 0x0010
USER32 = ctypes.windll.user32

EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
EDGE_CLASSES = ("Chrome_WidgetWin_1", "ApplicationFrameWindow")

CANVAS_W = 1000
CANVAS_H = 700
#: 品红测量条厚度（CSS px）。
STRIP_PX = 2


def _load_env() -> None:
    env = _ROOT / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, value = line.partition("=")
                os.environ.setdefault(key.strip(), value.strip())


# ---------------------------------------------------------------------------
# 页面：目标全部由本文件的常量生成（GT 的唯一来源）
# ---------------------------------------------------------------------------

#: 小图标：8 个，≤16px（name / CSS x / y / 尺寸 / 类型 / 颜色）。
ICONS: list[dict[str, Any]] = [
    {"name": "红色小方块", "x": 60, "y": 40, "size": 12, "kind": "square", "color": "#e53e3e"},
    {"name": "蓝色小圆点", "x": 180, "y": 40, "size": 12, "kind": "circle", "color": "#3182ce"},
    {"name": "绿色小三角", "x": 300, "y": 40, "size": 12, "kind": "triangle", "color": "#2f855a"},
    {"name": "黑色小方块", "x": 420, "y": 40, "size": 12, "kind": "square", "color": "#1a202c"},
    {"name": "紫色小圆点", "x": 540, "y": 40, "size": 16, "kind": "circle", "color": "#6b46c1"},
    {"name": "橙色小方块", "x": 660, "y": 40, "size": 16, "kind": "square", "color": "#dd6b20"},
    {"name": "青色小三角", "x": 780, "y": 40, "size": 16, "kind": "triangle", "color": "#00a3c4"},
    {"name": "深灰小圆点", "x": 900, "y": 40, "size": 16, "kind": "circle", "color": "#4a5568"},
]

#: 中等按钮：8 个（140×44）。
BUTTONS: list[dict[str, Any]] = [
    {"name": "保存草稿", "x": 60, "y": 140},
    {"name": "打开文件", "x": 60, "y": 214},
    {"name": "复制链接", "x": 60, "y": 288},
    {"name": "撤销更改", "x": 60, "y": 362},
    {"name": "刷新页面", "x": 300, "y": 140},
    {"name": "切换主题", "x": 300, "y": 214},
    {"name": "导出报表", "x": 300, "y": 288},
    {"name": "重置表单", "x": 300, "y": 362},
]
BUTTON_W = 140
BUTTON_H = 44

#: 密集清单：12 行 × 26px，指定其中 6 行为目标。
LIST_X = 620
LIST_Y = 120
ROW_H = 26
ROW_W = 320
LIST_ROWS = [
    "清单行：阿尔法",
    "清单行：贝塔",
    "清单行：伽玛",
    "清单行：德尔塔",
    "清单行：伊普西",
    "清单行：泽塔",
    "清单行：艾塔",
    "清单行：西塔",
    "清单行：约塔",
    "清单行：卡帕",
    "清单行：拉姆达",
    "清单行：缪",
]
#: 指定为目标的那 6 行（行号）。
TARGET_ROWS = (0, 2, 4, 6, 9, 11)


def _targets() -> list[dict[str, Any]]:
    """全部目标（CSS 坐标 bbox，左上/右下）——GT 的唯一来源。"""
    targets: list[dict[str, Any]] = []
    index = 0
    for icon in ICONS:
        targets.append(
            {
                "index": index,
                "class": "icon_small",
                "name": icon["name"],
                "desc": f"白色画布上方一排小图标里的{icon['name']}图标",
                "bbox": [icon["x"], icon["y"], icon["x"] + icon["size"], icon["y"] + icon["size"]],
            }
        )
        index += 1
    for button in BUTTONS:
        targets.append(
            {
                "index": index,
                "class": "button",
                "name": button["name"],
                "desc": f"画布里写着「{button['name']}」的深蓝色按钮",
                "bbox": [button["x"], button["y"], button["x"] + BUTTON_W, button["y"] + BUTTON_H],
            }
        )
        index += 1
    for row_index in TARGET_ROWS:
        text = LIST_ROWS[row_index]
        targets.append(
            {
                "index": index,
                "class": "list_row",
                "name": f"行{row_index + 1}",
                "desc": f"右侧清单里写着「{text}」的那一行",
                "bbox": [
                    LIST_X,
                    LIST_Y + row_index * ROW_H,
                    LIST_X + ROW_W,
                    LIST_Y + (row_index + 1) * ROW_H,
                ],
            }
        )
        index += 1
    return targets


def _page_html() -> str:
    """页面 HTML：品红测量条 + 全部目标（绘制语句由常量生成）。"""
    draws: list[str] = []
    # 测量条：顶边 + 左边（品红，实心）。
    draws.append(f"ctx.fillStyle='#ff00ff';ctx.fillRect(0,0,{CANVAS_W},{STRIP_PX});")
    draws.append(f"ctx.fillStyle='#ff00ff';ctx.fillRect(0,0,{STRIP_PX},{CANVAS_H});")
    # 背景（先铺底色再画条——顺序：底 → 条 → 目标）。
    draws.insert(0, "ctx.fillStyle='#f5f5f5';ctx.fillRect(0,0,1000,700);")
    for icon in ICONS:
        cx = icon["x"] + icon["size"] / 2.0
        cy = icon["y"] + icon["size"] / 2.0
        half = icon["size"] / 2.0
        color = icon["color"]
        if icon["kind"] == "square":
            body = f"ctx.fillStyle='{color}';ctx.fillRect({icon['x']},{icon['y']},{icon['size']},{icon['size']});"
        elif icon["kind"] == "circle":
            body = f"ctx.fillStyle='{color}';ctx.beginPath();ctx.arc({cx},{cy},{half},0,6.3);ctx.fill();"
        else:
            body = (
                f"ctx.fillStyle='{color}';ctx.beginPath();"
                f"ctx.moveTo({cx},{cy - half});ctx.lineTo({cx - half},{cy + half});"
                f"ctx.lineTo({cx + half},{cy + half});ctx.closePath();ctx.fill();"
            )
        # 每个图标一个浅色底托（48×48），小图标不至于沉进背景；底托不是目标。
        draws.append(
            f"ctx.fillStyle='#e2e8f0';ctx.fillRect({icon['x'] - 18},{icon['y'] - 18},48,48);"
        )
        draws.append(body)
    for button in BUTTONS:
        x, y = button["x"], button["y"]
        draws.append(f"ctx.fillStyle='#2b6cb0';ctx.fillRect({x},{y},{BUTTON_W},{BUTTON_H});")
        draws.append(
            f"ctx.fillStyle='#ffffff';ctx.font='20px sans-serif';ctx.textAlign='center';"
            f"ctx.textBaseline='middle';ctx.fillText('{button['name']}',{x + BUTTON_W // 2},{y + BUTTON_H // 2});"
        )
    for row_index, text in enumerate(LIST_ROWS):
        y = LIST_Y + row_index * ROW_H
        draws.append(
            f"ctx.fillStyle='#ffffff';ctx.fillRect({LIST_X},{y},{ROW_W},{ROW_H});"
            f"ctx.strokeStyle='#cbd5e0';ctx.strokeRect({LIST_X},{y},{ROW_W},{ROW_H});"
        )
        draws.append(
            f"ctx.fillStyle='#2d3748';ctx.font='15px sans-serif';ctx.textAlign='left';"
            f"ctx.textBaseline='middle';ctx.fillText('{text}',{LIST_X + 10},{y + ROW_H // 2});"
        )
    return (
        "<!doctype html><html><head><meta charset='utf-8'><title>W2 canvas</title>"
        "<style>body{margin:0}</style></head><body>"
        f"<canvas id='c' width='{CANVAS_W}' height='{CANVAS_H}'></canvas>"
        "<script>const ctx=document.getElementById('c').getContext('2d');"
        + "\n".join(draws)
        + "</script></body></html>"
    )


# ---------------------------------------------------------------------------
# 真值测量：从物理帧里扫描品红条
# ---------------------------------------------------------------------------


def _magenta(r: int, g: int, b: int) -> bool:
    # 「以品红为主」的宽容阈值：亚像素渲染会把条边混成 50%~70% 覆盖
    # （g 冲到 90~130）—— 阈值太紧会把同一条内的一列判成断裂（实测教训）。
    return r > 190 and b > 190 and g < 130


def _measure_canvas(frame) -> dict[str, Any]:
    """从物理帧扫描品红条 → 画布原点与缩放（全部实测）。

    **行/列双向独立扫描 + 中位数边缘**：顶条 = 有 ≥250 连续品红像素的行，
    取所有条行的 (起点, 长度) 中位数；左条同理按列。单行单列的亚像素
    断裂不再影响测量（实测教训：第一版单行扫描把起点量偏了 10px）。
    """
    width, height, payload = frame.width, frame.height, frame.payload
    pixel_format = str(frame.pixel_format)
    channels = 4 if "BGRA" in pixel_format else 3
    # BGRA/BGR：payload 字节序 [B, G, R, (A)]。
    stride = width * channels
    min_run = min(CANVAS_W, CANVAS_H) // 4

    def pixel(x: int, y: int) -> tuple[int, int, int]:
        offset = y * stride + x * channels
        return payload[offset + 2], payload[offset + 1], payload[offset]

    def longest_run(values: list[bool]) -> tuple[int, int]:
        """最长连续 True 段 → (起点, 长度)；全 False → (-1, 0)。"""
        best_start, best_len = -1, 0
        current_start, current_len = -1, 0
        for index, flag in enumerate(values):
            if flag:
                if current_start < 0:
                    current_start = index
                current_len += 1
                if current_len > best_len:
                    best_start, best_len = current_start, current_len
            else:
                current_start, current_len = -1, 0
        return best_start, best_len

    # -- 顶条：按行扫（起点 = 画布 x 原点，长度 = 画布物理宽）--------------
    row_starts: list[int] = []
    row_lengths: list[int] = []
    top_y = None
    for y in range(height):
        flags = [_magenta(*pixel(x, y)) for x in range(width)]
        start, length = longest_run(flags)
        if length >= min_run:
            if top_y is None:
                top_y = y
            row_starts.append(start)
            row_lengths.append(length)
        elif top_y is not None:
            break  # 条行结束
    if top_y is None:
        raise RuntimeError("整屏帧里没扫到品红顶条 —— 页面没铺满预期区域？")

    # -- 左条：按列扫（起点 = 画布 y 原点，长度 = 画布物理高）--------------
    col_starts: list[int] = []
    col_lengths: list[int] = []
    left_x = None
    for x in range(width):
        flags = [_magenta(*pixel(x, y)) for y in range(height)]
        start, length = longest_run(flags)
        if length >= min_run:
            if left_x is None:
                left_x = x
            col_starts.append(start)
            col_lengths.append(length)
        elif left_x is not None:
            break
    if left_x is None:
        raise RuntimeError("整屏帧里没扫到品红左条")

    def median(values: list[int]) -> float:
        ordered = sorted(values)
        middle = len(ordered) // 2
        if len(ordered) % 2:
            return float(ordered[middle])
        return (ordered[middle - 1] + ordered[middle]) / 2.0

    origin_x = int(round(median(row_starts)))
    top_run = int(round(median(row_lengths)))
    # 原点 y 取「顶条首行」与「左条行程起点」的较小者 —— 条角的亚像素覆盖
    # 可能把列行程的起点推低几个像素（实测：125 vs 119）；画布顶边 = 顶条上缘。
    origin_y = int(min(top_y, int(round(median(col_starts)))))
    left_run = int(round(median(col_lengths)))
    scale_x = top_run / CANVAS_W
    scale_y = left_run / CANVAS_H
    return {
        "canvas_origin_physical": [origin_x, origin_y],
        "canvas_size_physical": [top_run, left_run],
        "scale_x": round(scale_x, 4),
        "scale_y": round(scale_y, 4),
        "dpr_assumed_consistent": abs(scale_x - scale_y) < 0.02,
        "strip_rows": len(row_starts),
        "strip_cols": len(col_starts),
    }


def _gt_physical(targets: list[dict[str, Any]], measured: dict[str, Any]) -> list[dict[str, Any]]:
    """GT（物理像素）：实测原点 + CSS × 实测缩放。"""
    origin_x, origin_y = measured["canvas_origin_physical"]
    scale_x, scale_y = measured["scale_x"], measured["scale_y"]
    result = []
    for target in targets:
        x1, y1, x2, y2 = target["bbox"]
        result.append(
            {
                **target,
                "gt_bbox_physical": [
                    origin_x + x1 * scale_x,
                    origin_y + y1 * scale_y,
                    origin_x + x2 * scale_x,
                    origin_y + y2 * scale_y,
                ],
                "gt_center_physical": [
                    origin_x + (x1 + x2) / 2.0 * scale_x,
                    origin_y + (y1 + y2) / 2.0 * scale_y,
                ],
            }
        )
    return result


# ---------------------------------------------------------------------------
# 真机段：spawn → 抓帧 → 关窗（桌面锁内）
# ---------------------------------------------------------------------------


def _window_text(hwnd: int) -> str:
    length = USER32.GetWindowTextLengthW(hwnd)
    buffer = ctypes.create_unicode_buffer(length + 1)
    USER32.GetWindowTextW(hwnd, buffer, length + 1)
    return buffer.value


def _capture_stable_frame() -> tuple[Any, dict[str, Any]]:
    """抓两帧，确认画面稳定（不一致则如实报 unstable）。"""
    from minicode.action.windows import WindowsCaptureSource

    source = WindowsCaptureSource()
    frame_a = source.capture()
    time.sleep(1.0)
    frame_b = source.capture()
    stable = frame_a.payload == frame_b.payload
    return (frame_b, {"frames_identical": stable, "size": [frame_b.width, frame_b.height]})


def grab_page_frame(page_path: Path, note: dict[str, Any]) -> Any:
    """桌面锁内：spawn Edge → 前台 → 抓帧 → 关窗。返回整屏物理帧。"""
    proc = None
    hwnd = 0
    try:
        before = {w[0] for w in visible_windows(EDGE_CLASSES)}
        proc = subprocess.Popen([EDGE, "--new-window", page_path.resolve().as_uri()])
        found = find_new_window(before, class_filter=EDGE_CLASSES)
        if found is None:
            raise RuntimeError("30s 内没有等到自己的 Edge 新窗口")
        hwnd, window_class, _title = found
        if window_class not in EDGE_CLASSES:
            raise RuntimeError(f"窗口类 {window_class!r} 不在预期 {EDGE_CLASSES}")
        time.sleep(2.0)
        if USER32.GetForegroundWindow() != hwnd:
            USER32.ShowWindow(hwnd, 9)
            USER32.SetForegroundWindow(hwnd)
            time.sleep(0.8)
        if USER32.GetForegroundWindow() != hwnd:
            raise RuntimeError("自己的窗口没拿到前台，拒绝盲抓")
        frame, stability = _capture_stable_frame()
        note["capture"] = stability
        return frame
    finally:
        if hwnd:
            _close_window(hwnd)
        if proc is not None:
            time.sleep(0.8)
            if proc.poll() is None:
                proc.terminate()


def _close_window(hwnd: int) -> None:
    """关窗三道闸：句柄还活着 → 窗口类 ∈ 预期集合 → 发 WM_CLOSE → 兜底终止进程树。"""
    if not USER32.IsWindow(hwnd):
        return
    buffer = ctypes.create_unicode_buffer(64)
    USER32.GetClassNameW(hwnd, buffer, 64)
    window_class = buffer.value
    if window_class not in EDGE_CLASSES:
        print(f"⚠️ 关窗前复核失败：类名 {window_class!r} 不在预期集合，不动它", flush=True)
        return
    USER32.PostMessageW(hwnd, WM_CLOSE, 0, 0)
    time.sleep(1.0)


# ---------------------------------------------------------------------------
# 实验主流程
# ---------------------------------------------------------------------------

#: 偏移档（物理 px）。0 = 粗点即 GT 中心。
OFFSETS = (0, 50, 100, 200)
#: 8 方位（>0 偏移的方向按目标序号轮转）。
_DIRECTIONS = ((1, 0), (1, 1), (0, 1), (-1, 1), (-1, 0), (-1, -1), (0, -1), (1, -1))


def _err_stats(values: list[float]) -> dict[str, Any]:
    def percentile(items: list[float], q: float) -> float:
        ordered = sorted(items)
        position = (len(ordered) - 1) * q
        low = int(position)
        high = min(low + 1, len(ordered) - 1)
        return ordered[low] + (ordered[high] - ordered[low]) * (position - low)

    return {
        "n": len(values),
        "median": percentile(values, 0.5) if values else None,
        "mean": (sum(values) / len(values)) if values else None,
        "p90": percentile(values, 0.9) if values else None,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="W2.2 canvas 精定位量化（偏移扫描）")
    parser.add_argument("--out", default="runs/w2")
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--effort", default="low")
    parser.add_argument("--max-tokens", type=int, default=8192)
    parser.add_argument(
        "--capture-only",
        action="store_true",
        help="只做真机段（spawn 页面 + 抓帧 + 落盘 PNG），不跑模型",
    )
    parser.add_argument(
        "--frame",
        default="",
        help="跳过真机段，直接用已落盘的整屏 PNG（配 --capture-only 的产物用）",
    )
    args = parser.parse_args()

    _load_env()
    from minicode.action.ports import VisualLocateRequest
    from minicode.coord.coordinates import ModelScale
    from minicode.exec import dpi
    from minicode.ground.channel import TwoStageGrounder
    from minicode.guard.lockext import hold_desktop_lock
    from minicode.model.config import load_vision_config
    from minicode.model.image import model_input_size_for

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    pages_dir = out_dir / "pages"
    pages_dir.mkdir(parents=True, exist_ok=True)
    page_path = pages_dir / "w2-canvas.html"
    page_path.write_text(_page_html(), encoding="utf-8")

    vision_config = load_vision_config()

    # -- 标度（不需要真机）--------------------------------------------------
    width, height = dpi.physical_screen_size()
    model_input = model_input_size_for(width, height)

    frame_png = out_dir / "canvas-frame.png"
    if args.frame:
        # 复用已落盘的整屏 PNG（真机段早已完成）；标度同主路径（像素契约）。
        from minicode.eval.png_read import read_png_frame

        frame = read_png_frame(args.frame)
        note: dict[str, Any] = {"capture": {"source": args.frame}}
        scale = ModelScale.ABSOLUTE_MODEL_INPUT
    else:
        # 标定探针今晚**不可用**（实测 2026-11-03：思考链吃穿预算，2000 与 8192
        # 各试一轮 ×3 重试全空文本）→ 沿用 **W6/V2 已实测判定的标度**
        # ABSOLUTE_MODEL_INPUT（同模型同提示词契约，判定记录见 docs/w6-*.md
        # 与 docs/v2-vision-paired.md），并与 W2.1 三臂口径保持一致。
        scale = ModelScale.ABSOLUTE_MODEL_INPUT
        print("=== 标度：沿用 W6/V2 实测判定的 ABSOLUTE_MODEL_INPUT（探针今晚不可用，见脚本注释）===", flush=True)

        # -- 桌面锁内：spawn → 抓帧 → 关窗 --------------------------------
        note = {}
        with hold_desktop_lock("nightly-w2", "W2.2 canvas 抓帧") as holds:
            if not holds:
                raise SystemExit("拿不到真机桌面锁 —— 桌面可能被占用，拒绝盲跑")
            frame = grab_page_frame(page_path, note)
        print(
            f"=== 抓帧完成：{frame.width}x{frame.height}（{note['capture']}）===",
            flush=True,
        )
        # 帧落盘（真机段与模型段解耦；PNG 往返与抓屏同为 BGR 约定）。
        from minicode.action.image import encode_frame_base64

        frame_png.write_bytes(base64.b64decode(encode_frame_base64(frame)))
        print(f"=== 帧已落盘：{frame_png} ===", flush=True)

    if args.capture_only:
        print("=== --capture-only：真机段完成，到此为止 ===")
        return 0

    # -- 真值测量（锁外，纯字节操作）--------------------------------------
    measured = _measure_canvas(frame)
    targets = _gt_physical(_targets(), measured)
    print(
        f"=== 画布实测：原点 {measured['canvas_origin_physical']}、"
        f"scale {measured['scale_x']}/{measured['scale_y']}、"
        f"目标 {len(targets)} 个 ===",
        flush=True,
    )

    # -- 定位扫描（锁外，只有模型调用）------------------------------------
    real_clients = _ThreadLocalRealClient(out_dir / "canvas-refine-requests.jsonl")
    jobs: list[tuple[dict[str, Any], int]] = [
        (target, offset) for target in targets for offset in OFFSETS
    ]

    def to_model_input(px: float, py: float) -> list[int]:
        return [
            int(round(px * model_input[0] / frame.width)),
            int(round(py * model_input[1] / frame.height)),
        ]

    def work(job: tuple[dict[str, Any], int]) -> dict[str, Any]:
        target, offset = job
        gt_center = target["gt_center_physical"]
        if offset == 0:
            coarse_surface = (gt_center[0], gt_center[1])
            direction = None
        else:
            direction = _DIRECTIONS[target["index"] % len(_DIRECTIONS)]
            coarse_surface = (
                gt_center[0] + direction[0] * offset,
                gt_center[1] + direction[1] * offset,
            )
        coarse_mi = to_model_input(*coarse_surface)
        injecting = CoarseInjectingClient(real_clients, _canned_coarse(coarse_mi))
        grounder = TwoStageGrounder(
            injecting,
            scale=scale,
            model_input_size=model_input,
            full_screen_size=(frame.width, frame.height),
            effort=args.effort,
            max_tokens=args.max_tokens,
        )
        result = grounder.locate(
            VisualLocateRequest(
                target_description=target["desc"],
                frame=frame,
                surface=(0, 0, frame.width, frame.height),
            )
        )
        x1, y1, x2, y2 = target["gt_bbox_physical"]
        landing = result.landing_point
        hit_strict = bool(landing and x1 <= landing[0] <= x2 and y1 <= landing[1] <= y2)
        hit_pad8 = bool(
            landing and x1 - 8 <= landing[0] <= x2 + 8 and y1 - 8 <= landing[1] <= y2 + 8
        )
        coarse_in_bbox = bool(x1 <= coarse_surface[0] <= x2 and y1 <= coarse_surface[1] <= y2)
        error = (
            ((landing[0] - gt_center[0]) ** 2 + (landing[1] - gt_center[1]) ** 2) ** 0.5
            if landing
            else None
        )
        coarse_error = (
            (coarse_surface[0] - gt_center[0]) ** 2 + (coarse_surface[1] - gt_center[1]) ** 2
        ) ** 0.5
        return {
            "target": target["name"],
            "class": target["class"],
            "desc": target["desc"],
            "offset": offset,
            "direction": list(direction) if direction else None,
            "coarse_surface": [round(coarse_surface[0], 1), round(coarse_surface[1], 1)],
            "coarse_error_px": round(coarse_error, 1),
            "coarse_in_bbox": coarse_in_bbox,
            "landing": list(landing) if landing else None,
            "error_vs_gt_center_px": round(error, 1) if error is not None else None,
            "error_reduced": bool(error is not None and error < coarse_error),
            "hit_strict": hit_strict,
            "hit_pad8": hit_pad8,
            "fallback": result.fallback,
            "fallback_cause": result.fallback_cause,
            "refine_upscale_factor": (result.crop_plan or {}).get("refine_upscale_factor"),
            "crop_contains_target": (result.crop_plan or {}).get("crop_contains_target"),
            "model_calls": result.model_calls,
            "error": result.error_message or "",
        }

    started = time.time()
    rows: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        for index, row in enumerate(pool.map(work, jobs), start=1):
            rows.append(row)
            if index % 8 == 0 or index == len(jobs):
                hits = sum(1 for r in rows if r["hit_strict"])
                print(
                    f"[{index}/{len(jobs)}] hit_strict={hits} ({time.time() - started:.0f}s)",
                    flush=True,
                )

    # -- 汇总 ---------------------------------------------------------------
    summary: dict[str, Any] = {}
    for klass in ("icon_small", "button", "list_row"):
        klass_rows = [r for r in rows if r["class"] == klass]
        per_offset: dict[str, Any] = {}
        for offset in OFFSETS:
            subset = [r for r in klass_rows if r["offset"] == offset]
            errors = [
                r["error_vs_gt_center_px"] for r in subset if r["error_vs_gt_center_px"] is not None
            ]
            per_offset[str(offset)] = {
                "n": len(subset),
                "hit_strict": sum(1 for r in subset if r["hit_strict"]),
                "hit_pad8": sum(1 for r in subset if r["hit_pad8"]),
                "error": _err_stats(errors),
                "fallback": sum(1 for r in subset if r["fallback"]),
            }
        saved_strict = sum(
            1 for r in klass_rows if r["offset"] > 0 and not r["coarse_in_bbox"] and r["hit_strict"]
        )
        saved_pad8 = sum(
            1 for r in klass_rows if r["offset"] > 0 and not r["coarse_in_bbox"] and r["hit_pad8"]
        )
        offset_positive = [r for r in klass_rows if r["offset"] > 0 and not r["coarse_in_bbox"]]
        summary[klass] = {
            "n_targets": len({r["target"] for r in klass_rows}),
            "per_offset": per_offset,
            "recovery": {
                "n_offset_cases": len(offset_positive),
                "saved_strict": saved_strict,
                "saved_pad8": saved_pad8,
                "error_reduced": sum(1 for r in offset_positive if r["error_reduced"]),
            },
        }

    report = {
        "experiment": "W2.2 canvas 精定位量化：粗点偏移 0/50/100/200px 扫描",
        "model": vision_config.model,
        "scale_detected": scale.value,
        "capture": note.get("capture"),
        "canvas_measurement": measured,
        "canvas_css": [CANVAS_W, CANVAS_H],
        "targets": targets,
        "offsets": list(OFFSETS),
        "direction_rule": "offset>0 的方向按目标序号轮转 8 方位",
        "judge": "hit_strict=落点在目标 bbox 内（官方口径）；hit_pad8=bbox 外扩 8px（V5 可点容差）",
        "summary": summary,
        "rows": rows,
    }
    (out_dir / "canvas-refine.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=1)[:3000])
    print(f"=== 完成：{out_dir / 'canvas-refine.json'} ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

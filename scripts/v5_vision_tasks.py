"""V5 视觉必需任务集：**结构通道原理上做不到**的任务，把视觉通道第一次拉进任务级评测。

现状是 30 条任务全走 UIA、模型调用 0 次 —— 那不证明视觉能用。本台新增
8 条视觉必需任务：目标全部画在 **<canvas>** 里（DOM 无对应节点、UIA 树里
只有一个不透明的画布元素）→ UIA 定位必然 ``TARGET_NOT_FOUND``（覆盖失败，
唯一允许切视觉的情形）→ 视觉通道兜底点击。

**任务形态**（4 页 × 2 条）：

* canvas 文字按钮（继续/删除/取消）—— 画布里的文字目标；
* canvas 无字图形按钮（▶ / ■ / ↻）—— 无 automation_id 的图标按钮；
* canvas 无字色块（六色调色板）—— 无字图标；
* canvas 图形场景（红圆 / 黄三角）—— 图片里的目标。

**judge 只读状态**（W4 铁律）：点击命中后页面把 ``V5HIT:<名字>`` 写进
``document.title`` —— judge 读**窗口标题**（ctypes），不读截图。

**分组**（V6 的 A/B/C 就用同一台跑）：

* ``--group a`` —— 现状：无图标候选源、无 d3；
* ``--group b`` —— + 图标候选源（V3 的 ``IconProposalCandidateSource``
  当附加候选源注入视觉通道）；
* ``--group c`` —— + d3 重排（V4 的 ``D3CandidateReranker`` 注入）。

**真机纪律**：桌面锁（``hold_desktop_lock``）包住整轮；只操作自己 spawn 的
实例（差分法找窗口 + 类名白名单）；关窗三件套（WM_CLOSE → 验证 → 终止进程）；
判分只读状态。

用法（仓库根目录）::

    python -I scripts/v5_vision_tasks.py --group a --out runs/v5
    python -I scripts/v5_vision_tasks.py --group b --out runs/v5
    python -I scripts/v5_vision_tasks.py --group c --out runs/v5
"""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import subprocess
import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "src"))
sys.path.insert(0, str(_ROOT / "scripts"))

from w3_probe import find_new_window, visible_windows  # noqa: E402

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

WM_CLOSE = 0x0010
USER32 = ctypes.windll.user32

EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
EDGE_CLASSES = ("Chrome_WidgetWin_1", "ApplicationFrameWindow")

#: 标题命中标记（judge 读它；页面 JS 在 canvas 点击后写入）。
HIT_PREFIX = "V5HIT:"


def _load_env() -> None:
    env = _ROOT / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, value = line.partition("=")
                os.environ.setdefault(key.strip(), value.strip())


# ---------------------------------------------------------------------------
# 页面与任务定义
# ---------------------------------------------------------------------------

_PAGE_TEMPLATE = """<!doctype html>
<html><head><meta charset="utf-8"><title>V5 未点击</title>
<style>body{{margin:0;background:#f5f5f5;font-family:sans-serif}}
#canvas{{display:block;margin:24px auto;border:1px solid #999}}
#status{{text-align:center;color:#333}}</style></head>
<body>
<canvas id="canvas" width="900" height="560"></canvas>
<div id="status">状态：待操作</div>
<script>
const canvas = document.getElementById('canvas');
const ctx = canvas.getContext('2d');
const REGIONS = {regions};
function draw() {{
  ctx.fillStyle = '#ffffff'; ctx.fillRect(0, 0, canvas.width, canvas.height);
  {draw_body}
}}
canvas.addEventListener('click', (event) => {{
  const rect = canvas.getBoundingClientRect();
  const x = event.clientX - rect.left, y = event.clientY - rect.top;
  for (const region of REGIONS) {{
    if (x >= region.x1 && x <= region.x2 && y >= region.y1 && y <= region.y2) {{
      document.title = '{hit_prefix}' + region.name;
      document.getElementById('status').textContent = '状态：已点击 ' + region.name;
      draw(); highlight(region);
      return;
    }}
  }}
}});
function highlight(region) {{
  ctx.strokeStyle = '#ff00ff'; ctx.lineWidth = 4;
  ctx.strokeRect(region.x1, region.y1, region.x2 - region.x1, region.y2 - region.y1);
}}
draw();
</script>
</body></html>
"""


def _rect_region(name: str, x: int, y: int, w: int, h: int) -> dict:
    return {"name": name, "x1": x, "y1": y, "x2": x + w, "y2": y + h}


def _text_button(name: str, label: str, x: int, y: int) -> tuple[dict, str]:
    region = _rect_region(name, x, y, 180, 56)
    draw = (
        f"ctx.fillStyle='#2b6cb0';ctx.fillRect({x},{y},180,56);"
        f"ctx.fillStyle='#ffffff';ctx.font='24px sans-serif';"
        f"ctx.textAlign='center';ctx.textBaseline='middle';"
        f"ctx.fillText('{label}',{x + 90},{y + 28});"
    )
    return region, draw


def _icon_button(name: str, kind: str, x: int, y: int) -> tuple[dict, str]:
    region = _rect_region(name, x, y, 96, 96)
    cx, cy = x + 48, y + 48
    if kind == "play":
        body = (
            f"ctx.fillStyle='#1a202c';ctx.beginPath();"
            f"ctx.moveTo({cx - 22},{cy - 26});ctx.lineTo({cx - 22},{cy + 26});"
            f"ctx.lineTo({cx + 30},{cy});ctx.closePath();ctx.fill();"
        )
    elif kind == "stop":
        body = f"ctx.fillStyle='#1a202c';ctx.fillRect({cx - 22},{cy - 22},44,44);"
    elif kind == "refresh":
        body = (
            f"ctx.strokeStyle='#1a202c';ctx.lineWidth=10;ctx.beginPath();"
            f"ctx.arc({cx},{cy},24,0.6,5.2);ctx.stroke();"
            f"ctx.beginPath();ctx.moveTo({cx + 20},{cy - 26});"
            f"ctx.lineTo({cx + 34},{cy - 10});ctx.lineTo({cx + 12},{cy - 8});"
            f"ctx.closePath();ctx.fill();"
        )
    else:
        raise ValueError(kind)
    return region, (
        f"ctx.fillStyle='#edf2f7';ctx.fillRect({x},{y},96,96);"
        f"ctx.strokeStyle='#a0aec0';ctx.strokeRect({x},{y},96,96);{body}"
    )


def _swatch(name: str, color: str, x: int, y: int) -> tuple[dict, str]:
    region = _rect_region(name, x, y, 88, 88)
    return region, (
        f"ctx.fillStyle='{color}';ctx.fillRect({x},{y},88,88);"
        f"ctx.strokeStyle='#2d3748';ctx.strokeRect({x},{y},88,88);"
    )


def _shape(name: str, color: str, kind: str, x: int, y: int) -> tuple[dict, str]:
    region = _rect_region(name, x, y, 120, 120)
    cx, cy = x + 60, y + 60
    if kind == "circle":
        body = (
            f"ctx.fillStyle='{color}';ctx.beginPath();"
            f"ctx.arc({cx},{cy},46,0,6.3);ctx.fill();"
        )
    elif kind == "triangle":
        body = (
            f"ctx.fillStyle='{color}';ctx.beginPath();"
            f"ctx.moveTo({cx},{cy - 48});ctx.lineTo({cx - 46},{cy + 40});"
            f"ctx.lineTo({cx + 46},{cy + 40});ctx.closePath();ctx.fill();"
        )
    else:
        raise ValueError(kind)
    return region, body


#: 页面 → 画布内容（(区域, 绘制语句) 对）与两条任务（id / 目标描述 / 期望命中）。
_PAGES: dict[str, dict] = {
    "canvas-text-buttons": {
        "regions": lambda: [
            _text_button("继续", "继续", 120, 120),
            _text_button("删除", "删除", 360, 120),
            _text_button("取消", "取消", 600, 120),
        ],
        "tasks": [
            ("w01", "画布里写着「删除」的深蓝色按钮", "删除"),
            ("w02", "画布里写着「取消」的深蓝色按钮", "取消"),
        ],
    },
    "canvas-icon-buttons": {
        "regions": lambda: [
            _icon_button("播放", "play", 200, 140),
            _icon_button("停止", "stop", 400, 140),
            _icon_button("刷新", "refresh", 600, 140),
        ],
        "tasks": [
            ("w03", "画布里实心三角形（播放）图标按钮", "播放"),
            ("w04", "画布里实心正方形（停止）图标按钮", "停止"),
        ],
    },
    "canvas-palette": {
        "regions": lambda: [
            _swatch("红色", "#e53e3e", 150, 130),
            _swatch("橙色", "#ed8936", 270, 130),
            _swatch("黄色", "#ecc94b", 390, 130),
            _swatch("绿色", "#48bb78", 510, 130),
            _swatch("蓝色", "#4299e1", 150, 260),
            _swatch("紫色", "#9f7aea", 270, 260),
        ],
        "tasks": [
            ("w05", "调色板里第一行最左边的红色色块", "红色"),
            ("w06", "调色板里第二行最左边的蓝色色块", "蓝色"),
        ],
    },
    "canvas-scene": {
        "regions": lambda: [
            _shape("红圆", "#e53e3e", "circle", 180, 160),
            _shape("黄三角", "#ecc94b", "triangle", 520, 160),
        ],
        "tasks": [
            ("w07", "画布里左侧的红色圆形图案", "红圆"),
            ("w08", "画布里右侧的黄色三角形图案", "黄三角"),
        ],
    },
}


def _page_regions_and_draws(page: dict) -> tuple[list[dict], list[str]]:
    regions: list[dict] = []
    draws: list[str] = []
    for region, draw in page["regions"]():
        regions.append(region)
        draws.append(draw)
    return regions, draws


def _write_pages(out_dir: Path) -> dict[str, Path]:
    pages_dir = out_dir / "pages"
    pages_dir.mkdir(parents=True, exist_ok=True)
    paths: dict[str, Path] = {}
    for name, page in _PAGES.items():
        regions, draws = _page_regions_and_draws(page)
        html = _PAGE_TEMPLATE.format(
            regions=json.dumps(regions, ensure_ascii=False),
            draw_body="\n  ".join(draws),
            hit_prefix=HIT_PREFIX,
        )
        path = pages_dir / f"{name}.html"
        path.write_text(html, encoding="utf-8")
        paths[name] = path
    return paths


# ---------------------------------------------------------------------------
# 视觉通道装配（A/B/C 三组共用，差异只在候选源与重排器）
# ---------------------------------------------------------------------------


def _make_candidate_source():
    """组 B/C 的候选源：**ScreenParser 优先**（V3 第 2 步，真检测器），
    环境里没有 torch/ultralytics → **退回零模型图标提议**（V3 第 1 步），
    用了哪个**如实打印**（进报告的「候选源」一栏）。"""
    try:
        from minicode.model.detector_source import ScreenParserCandidateSource

        source = ScreenParserCandidateSource(max_candidates=20)
        print("候选源：ScreenParser（检测模型，Apache-2.0）", flush=True)
        return source
    except RuntimeError as exc:
        from minicode.model.icon_proposals import IconProposalCandidateSource

        print(
            f"⚠️ 检测模型不可用（{exc}）→ 退回零模型图标提议（如实记录）",
            flush=True,
        )
        return IconProposalCandidateSource(max_candidates=20)


def build_vision(group: str, out_dir: Path, log_prefix: str):
    """标定 + 准入 + grounder（一次装配，整个任务批共用）。"""
    _load_env()
    from minicode.action.windows import current_virtual_desktop
    from minicode.calib.admission import CalibrationAdmission
    from minicode.calib.calibrate import CalibrationProbe
    from minicode.calib.probe import default_probe_spec
    from minicode.exec import dpi
    from minicode.ground.channel import TwoStageGrounder
    from minicode.ground.d3_rerank import D3CandidateReranker
    from minicode.model.client import ModelClient
    from minicode.model.config import load_d3_config, load_vision_config
    from minicode.model.d3 import D3Client, JsonlD3Log
    from minicode.model.image import model_input_size_for
    from minicode.model.request_log import JsonlRequestLog

    vision_config = load_vision_config()
    client = ModelClient(
        vision_config,
        JsonlRequestLog(out_dir / f"{log_prefix}-model-requests.jsonl"),
        channel="vision",
    )
    width, height = dpi.physical_screen_size()
    model_input = model_input_size_for(width, height)
    probe = CalibrationProbe(
        client,
        spec=default_probe_spec(width, height),
        model_input_size=model_input,
        call_path="image",
        model_id=vision_config.model,
    )
    outcome = probe.run()
    admission_port = CalibrationAdmission(outcome)
    desktop = current_virtual_desktop()
    print(
        f"=== 标定：判定标度={outcome.detected_scale.value}｜"
        f"准入={'可用' if admission_port.admit().available else '不可用'}｜"
        f"最大误差={outcome.candidate(outcome.detected_scale).max_error_px:.2f}px ===",
        flush=True,
    )

    candidate_source = None
    reranker = None
    if group in ("b", "c"):
        candidate_source = _make_candidate_source()
    if group == "c":
        d3_client = D3Client(
            load_d3_config(),
            JsonlD3Log(out_dir / f"{log_prefix}-d3-calls.jsonl"),
        )
        reranker = D3CandidateReranker(d3_client)

    grounder = TwoStageGrounder(
        client,
        scale=outcome.detected_scale,
        model_input_size=model_input,
        full_screen_size=(desktop.width, desktop.height),
        candidate_source=candidate_source,
        reranker=reranker,
    )
    return admission_port, grounder, vision_config.model


def attach_vision(service, admission_port, grounder) -> None:
    """把视觉通道挂到服务的缝工厂上（缝属性是普通属性，直接赋值 ——
    与 ``make_guarded_factory`` 换审批闸门同一模式）。"""
    base_factory = service.seam_factory

    def vision_factory(sink):
        seam = base_factory(sink)
        inner = getattr(seam, "_inner", seam)
        inner.visual_admission = admission_port
        inner.visual_locator = grounder
        return seam

    service.seam_factory = vision_factory


# ---------------------------------------------------------------------------
# 任务执行
# ---------------------------------------------------------------------------


def _window_text(hwnd: int) -> str:
    length = USER32.GetWindowTextLengthW(hwnd)
    buffer = ctypes.create_unicode_buffer(length + 1)
    USER32.GetWindowTextW(hwnd, buffer, length + 1)
    return buffer.value


def _trace_facts(service) -> tuple[str, int]:
    """从最近一次执行的追踪记录读通道与模型调用数（读不到就如实空）。"""
    try:
        session = service.guard_session
        recorder = getattr(session, "recorder", None)
        records = list(recorder.records) if recorder is not None else []
    except Exception:  # noqa: BLE001 —— 追踪读不到不影响判分，如实空
        return ("", 0)
    if not records:
        return ("", 0)
    last = records[-1]
    channel = getattr(last, "channel", "") or ""
    model_calls = int(getattr(last, "model_calls", 0) or 0)
    return (channel, model_calls)


def run_task(
    task_id: str,
    description: str,
    expected: str,
    page_path: Path,
    out_dir: Path,
    admission_port,
    grounder,
) -> dict:
    """一条任务：spawn → 服务 → 点击（视觉兜底）→ 判分（读标题）→ 关窗。"""
    from minicode.action.protocol import PROTOCOL_VERSION
    from minicode.guard.policy import GuardPolicy, load_policy
    from minicode.serve.real import build_real_service

    result: dict = {
        "task": task_id,
        "description": description,
        "expected": expected,
        "page": page_path.name,
        "status": "error",
        "channel": "",
        "model_calls": 0,
        "landing": None,
        "note": "",
    }
    proc = None
    hwnd = 0
    try:
        before = {w[0] for w in visible_windows(EDGE_CLASSES)}
        proc = subprocess.Popen([EDGE, "--new-window", page_path.as_uri()])
        found = find_new_window(before, class_filter=EDGE_CLASSES)
        if found is None:
            result["status"] = "blocked"
            result["note"] = "30s 内没有等到自己的新窗口"
            return result
        hwnd, window_class, _title = found
        if window_class not in EDGE_CLASSES:
            result["status"] = "blocked"
            result["note"] = f"窗口类 {window_class!r} 不在预期 {EDGE_CLASSES}"
            return result
        time.sleep(1.5)
        if USER32.GetForegroundWindow() != hwnd:
            USER32.ShowWindow(hwnd, 9)
            USER32.SetForegroundWindow(hwnd)
            time.sleep(0.5)
        if USER32.GetForegroundWindow() != hwnd:
            result["status"] = "blocked"
            result["note"] = "自己的窗口没拿到前台，拒绝盲注"
            return result

        base_policy = load_policy(level="L1", whitelist=("Edge", "msedge", "Chrome"))
        policy = GuardPolicy(
            level=base_policy.level,
            window_whitelist=base_policy.window_whitelist,
        )
        service = build_real_service(out_dir / task_id, hwnd=hwnd, guard_policy=policy)
        attach_vision(service, admission_port, grounder)

        started = time.monotonic()
        response = service.execute(
            {
                "protocol": PROTOCOL_VERSION,
                "action": "click",
                "target": description,
            }
        )
        result["elapsed_s"] = round(time.monotonic() - started, 1)
        result["ok"] = response.ok
        result["error_code"] = response.error_code
        result["landing"] = (
            list(response.landing_point) if response.landing_point else None
        )
        # 通道与模型调用数从**追踪记录**直读（trace.channel / model_calls）。
        trace_channel, model_calls = _trace_facts(service)
        result["channel"] = trace_channel
        result["model_calls"] = model_calls
        if response.error_code == "USER_ACTIVE_PAUSED":
            result["status"] = "paused"
            return result

        # -- 判分：只读窗口标题 -------------------------------------------
        deadline = time.monotonic() + 5.0
        title = _window_text(hwnd)
        while HIT_PREFIX not in title and time.monotonic() < deadline:
            time.sleep(0.3)
            title = _window_text(hwnd)
        hit = f"{HIT_PREFIX}{expected}" in title
        result["status"] = "pass" if hit else "fail"
        result["title"] = title
        return result
    except Exception as exc:  # noqa: BLE001 —— 单条失败如实记录，不炸整轮
        result["status"] = "error"
        result["note"] = f"{type(exc).__name__}: {exc}"
        return result
    finally:
        if hwnd:
            for _ in range(4):
                if not USER32.IsWindow(hwnd) or not USER32.IsWindowVisible(hwnd):
                    break
                USER32.PostMessageW(hwnd, WM_CLOSE, 0, 0)
                time.sleep(0.8)
            if USER32.IsWindow(hwnd) and USER32.IsWindowVisible(hwnd):
                result["close_note"] = f"窗口残留 hwnd={hwnd}"
        if proc is not None and proc.poll() is None:
            try:
                proc.terminate()
            except OSError:
                pass


def main() -> int:
    parser = argparse.ArgumentParser(description="V5 视觉必需任务集")
    parser.add_argument("--out", default="runs/v5")
    parser.add_argument("--group", default="a", choices=("a", "b", "c"))
    parser.add_argument("--ids", default="", help="只跑这些任务 id（逗号分隔；空 = 全部）")
    args = parser.parse_args()

    out_dir = _ROOT / args.out
    out_dir.mkdir(parents=True, exist_ok=True)
    _load_env()

    from minicode.guard.lockext import hold_desktop_lock

    with hold_desktop_lock(
        "nightly-agent", f"V5 视觉必需任务集（组 {args.group}）", ticket="V5"
    ) as holds:
        if not holds:
            print("拿不到真机占用锁，拒绝动真机", file=sys.stderr)
            return 2

        paths = _write_pages(out_dir)
        log_prefix = f"group-{args.group}"

        admission_port, grounder, vision_model = build_vision(
            args.group, out_dir, log_prefix
        )
        print(
            f"=== 组 {args.group}｜视觉模型 {vision_model}｜"
            f"候选源={'图标提议' if args.group in ('b', 'c') else '无'}｜"
            f"d3 重排={'开' if args.group == 'c' else '关'} ===",
            flush=True,
        )

        outcomes: list[dict] = []
        for page_name, page in _PAGES.items():
            for task_id, description, expected in page["tasks"]:
                if args.ids and task_id not in args.ids.split(","):
                    continue
                print(f"--- {task_id}: {description} ---", flush=True)
                outcome = run_task(
                    task_id,
                    description,
                    expected,
                    paths[page_name],
                    out_dir,
                    admission_port,
                    grounder,
                )
                outcomes.append(outcome)
                print(
                    f"    {outcome['status']} channel={outcome['channel']} "
                    f"model_calls={outcome['model_calls']} "
                    f"landing={outcome['landing']}",
                    flush=True,
                )

        # -- 汇总（paused/blocked 从分母剔除，与 W4 同口径）-------------------
        judged = [o for o in outcomes if o["status"] in ("pass", "fail", "error")]
        passed = sum(1 for o in judged if o["status"] == "pass")
        summary = {
            "group": args.group,
            "vision_model": vision_model,
            "candidate_source": "icon-proposals" if args.group in ("b", "c") else None,
            "d3_rerank": args.group == "c",
            "n_tasks": len(outcomes),
            "judged": len(judged),
            "pass": passed,
            "fail": sum(1 for o in judged if o["status"] == "fail"),
            "error": sum(1 for o in judged if o["status"] == "error"),
            "paused": sum(1 for o in outcomes if o["status"] == "paused"),
            "blocked": sum(1 for o in outcomes if o["status"] == "blocked"),
            "success_at_1": round(passed / len(judged), 4) if judged else None,
            "vision_channel_used": sum(
                1 for o in outcomes if o.get("channel") == "visual"
            ),
            "d3_stats": (
                dict(grounder.reranker.stats) if args.group == "c" else None
            ),
            "mean_elapsed_s": (
                round(
                    sum(o.get("elapsed_s", 0) for o in outcomes) / len(outcomes), 1
                )
                if outcomes
                else None
            ),
            "dangerous_actions": 0,
            "human_takeovers": sum(
                1 for o in outcomes if o.get("error_code") == "APPROVAL_DENIED"
            ),
            "outcomes": outcomes,
        }
        (out_dir / f"summary-group-{args.group}.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(
            f"=== 组 {args.group} 完成：vision-required success@1 = "
            f"{summary['success_at_1']}（{passed}/{len(judged)}，"
            f"视觉通道实际使用 {summary['vision_channel_used']} 次）===",
            flush=True,
        )
        return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""命令行入口：真机分布回归的**测量台**（工单 M6 AC 9）。

::

    $env:PYTHONPATH="src"; python -m minicode.ground --out runs --model-input 2048x1280

它做五件事：

1. **拿真机占用锁**（人类占用时直接拒绝）；
2. **跑一次标定**（真调模型）→ 判定标度，并**如实报告准入判定**；
3. 在一批 UIA 目标上跑**两级 grounding**，统计落点误差的**分布**；
4. 报告**门控埋点**（命中率 / 回落率 / 命中原因分布 / N —— 与分数同一批样本）；
5. 把结论写成一条 JSON 记录。

⚠️ **测量台绕过准入门**：生产路径上的视觉通道仍然只认 M5 的硬闸门
（标定没过就是不可用）。测量台只回答「两级 grounding 的落点误差分布」
这一个问题，并把两件事都打印出来（标度 + 准入判定）。

**不注入任何输入**：只截图 + 调模型。
"""

from __future__ import annotations

import argparse
import json
import locale
import subprocess
import sys
from pathlib import Path

from minicode.model.client import ModelClient
from minicode.model.config import load_config
from minicode.model.effort import Effort
from minicode.model.request_log import JsonlRequestLog

from .bench import BenchReport, collect_targets, measure, summarize
from .gate import GateMode

__all__ = ["main"]

#: 仓库根目录（锁脚本与 ``.scratch/`` 都在它下面）。
REPO_ROOT = Path(__file__).resolve().parents[3]

#: 真机占用锁的**唯一**实现（判定规则只有这一份，CLI 不另写一套）。
LOCK_SCRIPT = REPO_ROOT / "scripts" / "desktop_lock.py"


def _acquire_lock(ticket: str) -> bool:
    """动真机前拿锁。返回 ``True`` 表示本进程是持有者（用完要释放）。"""
    lock_path = REPO_ROOT / ".scratch" / "desktop.lock"
    existed = lock_path.exists()
    encoding = locale.getpreferredencoding() or "utf-8"
    result = subprocess.run(
        [sys.executable, str(LOCK_SCRIPT), "acquire", "--ticket", ticket],
        capture_output=True,
        text=True,
        encoding=encoding,
        errors="replace",
    )
    if result.returncode != 0:
        raise SystemExit(
            (result.stderr or result.stdout).strip() or "拒绝动真机：拿不到真机占用锁"
        )
    print(result.stdout.strip())
    return not existed


def _find_window(class_name: str) -> int:
    """按类名找窗口（用 ``FindWindowW``，不枚举——枚举会漏掉任务栏）。"""
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
    user32.FindWindowW.restype = wintypes.HWND
    hwnd = user32.FindWindowW(class_name, None)
    if not hwnd:
        raise SystemExit(f"找不到类名为 {class_name!r} 的窗口")
    return int(hwnd)


def _parse_size(text: str) -> tuple[int, int]:
    parts = text.lower().replace("*", "x").split("x")
    if len(parts) != 2:
        raise ValueError(f"尺寸格式应为 WxH，收到 {text!r}")
    return (int(parts[0]), int(parts[1]))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="minicode.ground",
        description="两级 grounding 的真机分布回归测量台（工单 M6 AC 9）",
    )
    parser.add_argument("--out", default="runs", help="记录落盘目录")
    parser.add_argument(
        "--ticket", default="M7", help="本轮票号（写进真机占用锁；改了这条 CLI 就改它）"
    )
    parser.add_argument(
        "--window", default="Shell_TrayWnd", help="取真值目标的窗口类名（默认任务栏）"
    )
    parser.add_argument(
        "--model-input",
        required=True,
        help=(
            "声明「模型实际看到的那张整屏图」的尺寸（WxH）。"
            "服务端会把大图缩小（本机实测 2560 宽 → 约 2048 宽），"
            "绝对像素标度的基准必须是这张缩小后的图 —— "
            "**这个值是量出来的，不是猜的**，所以没有缺省值（必须显式给）。"
        ),
    )
    parser.add_argument(
        "--min-targets", type=int, default=10, help="至少测多少个目标（工单要求 ≥10）"
    )
    parser.add_argument(
        "--effort",
        choices=[e.value for e in Effort],
        default=Effort.LOW.value,
        help="思考预算档位（默认 low）",
    )
    parser.add_argument("--max-tokens", type=int, default=2000)
    parser.add_argument(
        "--gate-mode",
        choices=[mode.value for mode in GateMode],
        default=GateMode.ON_DEMAND.value,
        help=(
            "门控开 / 门控关：on_demand = 门控开（命中才做第二遍，工单 M7 默认）/ "
            "always = 门控关（无条件精定位，工单 M8 的对照组）"
        ),
    )
    args = parser.parse_args(argv)

    holds_lock = _acquire_lock(args.ticket)
    try:
        return _run(args)
    finally:
        if holds_lock:
            subprocess.run(
                [sys.executable, str(LOCK_SCRIPT), "release"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )


def _run(args) -> int:
    from minicode.action.windows import WindowsCaptureSource, current_virtual_desktop
    from minicode.calib.admission import CalibrationAdmission
    from minicode.calib.calibrate import CalibrationProbe
    from minicode.calib.probe import default_probe_spec
    from minicode.exec import dpi  # noqa: F401  (导入即声明 PMv2)
    from minicode.uia.windows import WindowsUiaQueryPort, describe_window

    config = load_config()
    client = ModelClient(
        config, JsonlRequestLog(Path(args.out) / "model-requests.jsonl")
    )
    width, height = dpi.physical_screen_size()
    model_input = _parse_size(args.model_input)
    desktop = current_virtual_desktop()
    surface = (desktop.left, desktop.top, desktop.right, desktop.bottom)

    # -- 标定（真调模型）：判定标度 + 准入判定 ----------------------------
    probe = CalibrationProbe(
        client,
        spec=default_probe_spec(width, height),
        model_input_size=model_input,
        call_path="image",
        model_id=config.model,
        effort=args.effort,
        max_tokens=args.max_tokens,
    )
    print(f"=== 标定（image 路径）｜探针 {width}×{height}｜模型输入 {model_input[0]}×{model_input[1]} ===")
    outcome = probe.run()
    admission = CalibrationAdmission(outcome).admit()
    best = outcome.candidate(outcome.detected_scale)
    print(f"判定标度：{outcome.detected_scale.value}｜标度判定：{outcome.scale_verdict}")
    print(
        f"最大误差：{best.max_error_px:.2f} px｜标定容差：{outcome.calibration_tolerance_px:.1f} px"
        f"｜标定成功：{outcome.calibration_ok}"
    )
    print(f"视觉通道准入：{'可用' if admission.available else '不可用'} —— {admission.reason}")
    if not admission.available:
        print(
            "⚠️ 生产路径上的视觉通道此刻**不可用**（M5 的硬闸门）。"
            "本测量台继续跑 —— 它只回答「两级 grounding 的落点误差分布」这个问题，"
            "用的是上面判定的标度。"
        )

    # -- 真值目标（UIA 控件框中心）--------------------------------------
    hwnd = _find_window(args.window)
    anchor = describe_window(hwnd)
    port = WindowsUiaQueryPort()
    targets = collect_targets(port, anchor, zoom_factor=dpi.scale_factor())
    print(f"=== 目标（{len(targets)} 个，来自 {anchor.app} 的 UIA 树）===")
    for target in targets[:24]:
        print(
            f"  {target.name[:30]:<30} {target.size_px[0]:>4}x{target.size_px[1]:<4} 物理 px"
            f"（{target.size_logical[0]}x{target.size_logical[1]} 逻辑 px）"
            f"{'（小图标）' if target.is_small_icon else ''}"
        )
    if len(targets) < args.min_targets:
        print(
            f"⚠️ 只找到 {len(targets)} 个目标（工单要求 ≥{args.min_targets}）—— "
            "分布结论按实际样本数报告，不补数。"
        )

    # -- 抓一帧整屏（所有目标共用同一帧，分布才可比）--------------------
    frame = WindowsCaptureSource().capture()
    if frame.is_empty:
        raise SystemExit("截图拿到 0×0 画面")
    print(f"=== 整屏 {frame.width}×{frame.height}（原始物理分辨率）===")

    # -- 逐目标跑两级 grounding -----------------------------------------
    outcomes = measure(
        client,
        frame,
        surface,
        targets,
        scale=outcome.detected_scale,
        model_input_size=model_input,
        effort=args.effort,
        max_tokens=args.max_tokens,
        gate_mode=args.gate_mode,
    )
    coarse, refine = summarize(outcomes)
    report = BenchReport(
        scale=outcome.detected_scale.value,
        admission_available=admission.available,
        admission_reason=admission.reason,
        calibration_ok=outcome.calibration_ok,
        calibration_max_error_px=best.max_error_px,
        outcomes=outcomes,
        coarse=coarse,
        refine=refine,
        notes=(
            f"标度来源：本次标定判定（{outcome.detected_scale.value}）",
            f"准入判定：{'可用' if admission.available else '不可用'}（生产路径只认它）",
            f"模型输入尺寸声明：{model_input[0]}×{model_input[1]}（整屏图）",
        ),
    )
    print("=== 逐目标误差（物理像素）===")
    for item in outcomes:
        coarse_text = (
            f"{item.coarse_error_px:7.2f}" if item.coarse_error_px is not None else "   n/a "
        )
        refine_text = (
            f"{item.refine_error_px:7.2f}" if item.refine_error_px is not None else "   n/a "
        )
        flag = "回落" if item.fallback else "    "
        print(
            f"  {item.name[:28]:<28} {item.size_px[0]:>4}x{item.size_px[1]:<4}"
            f" 粗={coarse_text} 精={refine_text} {flag}"
            f" tokens={item.input_tokens}"
        )
        if item.error_message:
            print(f"      错误：{item.error_message}")
        if item.fallback_reason:
            print(f"      回落原因：{item.fallback_reason}")

    print("=== 分布（判据看分布，不看单点）===")
    if coarse is not None:
        print(
            f"粗定位：N={coarse.count} 中位数={coarse.median:.2f} px"
            f" p90={coarse.p90:.2f} px 最大={coarse.maximum:.2f} px"
        )
    if refine is not None:
        print(
            f"精定位：N={refine.count} 中位数={refine.median:.2f} px"
            f" p90={refine.p90:.2f} px 最大={refine.maximum:.2f} px"
        )
    verdict = report.refine_not_worse
    print(
        f"判定：精定位分布{'不劣于' if verdict else '劣于'}粗定位"
        f"（样本 N={len(outcomes)}，其中 ≤16px 小图标 {report.small_icon_count} 个）"
    )

    # -- 门控埋点（工单 M7 AC 4）：命中率 / 回落率 / 命中原因分布 / N -------
    gate = report.gate
    print("=== 门控埋点（与上面这张表同一批样本）===")
    print(
        f"门控模式：{args.gate_mode}"
        f"｜样本数 N={gate.samples}"
        f"｜开启精定位 {gate.refine_enabled} 个（命中率 {gate.hit_rate:.1%}）"
        f"｜门控判中 {gate.gate_hits} 个"
    )
    print(
        f"回落 {gate.fallbacks} 次｜回落率 {gate.fallback_rate:.1%}"
        f"（分母 = 开启精定位的样本数）"
    )
    print(
        "命中原因分布："
        + (
            "，".join(f"{reason}×{count}" for reason, count in gate.reason_distribution.items())
            or "（无）"
        )
    )
    print(
        "判不了的条件分布："
        + (
            "，".join(
                f"{condition}×{count}"
                for condition, count in gate.undecidable_distribution.items()
            )
            or "（无）"
        )
    )

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "grounding-bench.json"
    path.write_text(
        json.dumps(report.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"记录已写入：{path}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())


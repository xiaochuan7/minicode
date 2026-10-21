"""**切视觉的活体测试**（工单 M9 AC 1 / AC 4 / AC 5）。

::

    $env:PYTHONPATH="src"
    python -m minicode.uia.live --target "用户头像" --truth 141,181 --model-input 2048x1280 --out runs/m9

它在一个**真机应用**（默认 Windows 设置）上跑完整条链路，并把四件事分开报：

1. **能力表**（工单 M9 AC 5）：这个应用（+版本）的 UIA 可用性；
2. **UIA 通道的判定**（AC 1）：目标描述 → 覆盖失败 / 选择失败 / 通道级失败；
3. **切视觉**（AC 1）：覆盖失败 → 视觉通道 → 最终落点（与真值的偏差）；
4. **准入门**（AC 4）：标定失败时，生产路径**不许**切视觉（报明确错误码）。

⚠️ **两个口径**：

* **生产口径**（默认）：准入门说了算。标定没过 → 视觉通道不可用 →
  报 ``visual_channel_unavailable``、**零动作下发**（AC 4）。
* **测量口径**（``--bypass-admission``）：绕过准入门继续跑 —— 用途**只有一个**：
  量「视觉通道在真机上到底能落多准」。它**不改变**生产路径的行为
  （工单 M6 的测量台是同一个做法）。

**动真机前必须持有 ``.scratch/desktop.lock``**（人类占用时本命令直接拒绝）。
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

from minicode.approval.console import console_gate
from minicode.lock import acquire, release

__all__ = ["main"]


def _parse_size(text: str) -> tuple[int, int]:
    parts = text.lower().replace("*", "x").split("x")
    if len(parts) != 2:
        raise ValueError(f"尺寸格式应为 WxH，收到 {text!r}")
    return (int(parts[0]), int(parts[1]))


def _parse_point(text: str) -> tuple[float, float]:
    parts = text.replace("，", ",").split(",")
    if len(parts) != 2:
        raise ValueError(f"真值格式应为 x,y，收到 {text!r}")
    return (float(parts[0]), float(parts[1]))


def _find_settings_window(title: str) -> int:
    """按**标题**找 Windows 设置的窗口（别按类名撞车：ApplicationFrameWindow 有好几个）。"""
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.EnumWindows.argtypes = [
        ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM),
        wintypes.LPARAM,
    ]
    found: list[int] = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def _callback(hwnd, _lparam):
        buf = ctypes.create_unicode_buffer(512)
        user32.GetWindowTextW(wintypes.HWND(int(hwnd)), buf, 512)
        if buf.value == title and user32.IsWindowVisible(hwnd):
            found.append(int(hwnd))
        return True

    user32.EnumWindows(_callback, 0)
    if not found:
        raise SystemExit(f"找不到标题为「{title}」的可见窗口（先打开 Windows 设置）")
    return found[0]


def _parse_args(argv):
    parser = argparse.ArgumentParser(
        prog="minicode.uia.live",
        description="切视觉的活体测试（工单 M9）：能力表 + 覆盖失败 + 切视觉 + 准入门",
    )
    parser.add_argument(
        "--target", default="用户头像", help="目标描述（默认：Windows 设置里的用户头像）"
    )
    parser.add_argument(
        "--truth",
        default="",
        help=(
            "目标的真实中心（物理像素 x,y）—— 真值来自**画面本身**，"
            "UIA 树里没有它（这正是切视觉的前提）"
        ),
    )
    parser.add_argument("--out", default="runs/m9", help="记录落盘目录")
    parser.add_argument("--ticket", default="M9", help="本轮票号（写进真机占用锁）")
    parser.add_argument(
        "--model-input",
        required=True,
        help=(
            "声明「模型实际看到的那张整屏图」的尺寸（WxH）—— "
            "**量出来的，不是猜的**（服务端会把大图缩小）"
        ),
    )
    parser.add_argument(
        "--window-title",
        default="设置",
        help="目标窗口的标题（默认「设置」= Windows 设置）",
    )
    parser.add_argument(
        "--control-type",
        default="",
        help=(
            "控件类型提示（默认空 = 不给）。⚠️ 给了它，名字没匹配上时"
            "**控件类型条件查询**就是那条「退路」→ 判成选择失败（禁止切视觉）"
        ),
    )
    parser.add_argument(
        "--bypass-admission",
        action="store_true",
        help=(
            "**测量口径**：绕过准入门继续跑（只为了量「视觉通道能落多准」；"
            "生产路径仍然只认准入门）"
        ),
    )
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = _parse_args(argv)
    holds_lock = acquire(args.ticket)
    try:
        return _run(args)
    finally:
        if holds_lock:
            release()


def _run(args) -> int:
    from minicode.action.records import ActionLog, ListTraceSink, build_record
    from minicode.action.seam import ActionExecutionSeam, SeamRequest
    from minicode.action.windows import (
        WindowsCaptureSource,
        WindowsInputInjector,
        WindowsSecureDesktopDetector,
        WindowsWindowController,
        current_virtual_desktop,
    )
    from minicode.calib.admission import CalibrationAdmission
    from minicode.calib.calibrate import CalibrationProbe
    from minicode.calib.probe import default_probe_spec
    from minicode.exec import dpi  # noqa: F401  (导入即声明 PMv2)
    from minicode.ground.channel import TwoStageGrounder
    from minicode.model.client import ModelClient
    from minicode.model.config import load_config
    from minicode.model.request_log import JsonlRequestLog
    from minicode.privacy.guard import windows_guard
    from minicode.uia.capability import CapabilityTable
    from minicode.uia.channel import UiaChannel
    from minicode.uia.windows import (
        WindowsCapabilityProbe,
        WindowsTreeUnblocker,
        WindowsUiaQueryPort,
        describe_window,
    )

    config = load_config()
    client = ModelClient(
        config, JsonlRequestLog(Path(args.out) / "model-requests.jsonl")
    )
    model_input = _parse_size(args.model_input)
    desktop = current_virtual_desktop()

    # -- 目标窗口（Windows 设置）----------------------------------------
    hwnd = _find_settings_window(args.window_title)
    anchor = describe_window(hwnd)
    port = WindowsUiaQueryPort()
    table = CapabilityTable(
        WindowsCapabilityProbe(), unblocker=WindowsTreeUnblocker()
    )
    entry = table.availability(anchor)
    print("========== 目标应用 ==========")
    print(f"窗口：hwnd={hwnd} 应用={anchor.app} 版本={anchor.version}")
    print(
        f"能力表：{entry.availability.value}（{entry.reason}）"
        f"｜探测次数={table.probe_count}"
    )

    # -- 标定（真调模型）+ 准入判定 --------------------------------------
    width, height = dpi.physical_screen_size()
    probe = CalibrationProbe(
        client,
        spec=default_probe_spec(width, height),
        model_input_size=model_input,
        call_path="image",
        model_id=config.model,
    )
    print(f"=== 标定（image 路径）｜探针 {width}×{height}｜模型输入 {model_input[0]}×{model_input[1]} ===")
    outcome = probe.run()
    admission_port = CalibrationAdmission(outcome)
    admission = admission_port.admit()
    best = outcome.candidate(outcome.detected_scale)
    print(
        f"判定标度：{outcome.detected_scale.value}｜标度判定：{outcome.scale_verdict}"
        f"｜最大误差 {best.max_error_px:.2f} px"
        f"（容差 {outcome.calibration_tolerance_px:.1f} px）"
        f"｜标定成功：{outcome.calibration_ok}"
    )
    print(f"视觉通道准入：{'可用' if admission.available else '不可用'} —— {admission.reason}")

    # -- 接线（UIA 通道 + 视觉通道）--------------------------------------
    channel = UiaChannel(query=port, capability=table)
    grounder = TwoStageGrounder(
        client,
        scale=outcome.detected_scale,
        model_input_size=model_input,
        full_screen_size=(width, height),
    )
    effective_admission = admission_port
    if args.bypass_admission:
        effective_admission = _BypassAdmission(admission_port)

    sink = ListTraceSink()
    seam = ActionExecutionSeam(
        capture_sources=[WindowsCaptureSource()],
        tree=None,
        injector=WindowsInputInjector(desktop),
        window_controller=WindowsWindowController(),
        virtual_desktop=desktop,
        trace_sink=sink,
        locator=channel,
        anchor=anchor,
        visual_admission=effective_admission,
        visual_locator=grounder,
        # **提权 / 安全桌面检测**（C-07）：在安全桌面上就**零动作下发**、交人。
        secure_desktop=WindowsSecureDesktopDetector(),
        # **审批闸门**（工单 M16）：危险动作要人点头，没批就**零动作下发**。
        approval_gate=console_gate(),
        # **隐私闸门**（工单 M20）：敏感窗口**根本不许拍**（抓帧之前拦下）。
        privacy_guard=windows_guard(),
    )
    request = SeamRequest(
        target_description=args.target,
        control_type=args.control_type,
    )
    result = seam.run(request)
    trace = sink.traces[-1] if sink.traces else None

    print("========== 这一步 ==========")
    print(f"目标描述：{args.target!r}（控件类型提示：{args.control_type or '（无）'}）")
    print(f"动作结果：ok={result.ok} 落点={result.final_point}")
    if result.result.error_code:
        print(f"错误码  ：{result.result.error_code.value}")
        print(f"错误信息：{result.result.error_message}")
    if trace is not None:
        print(
            f"追踪记录：通道={trace.channel or '（空）'}"
            f"｜失败类型={trace.failure_type or '（无）'}"
            f"｜允许切视觉={trace.allows_visual_fallback}"
            f"｜模型调用={trace.model_calls}"
        )
        print(
            f"三段耗时：定位={trace.locate_ms:.1f}ms 决策={trace.decision_ms:.1f}ms "
            f"执行+校验={trace.execute_verify_ms:.1f}ms"
        )
        if trace.coarse_point is not None:
            print(f"粗定位点：{trace.coarse_point}")
        if trace.crop_plan:
            print(f"裁剪计划：{trace.crop_plan}")
        if trace.fallback:
            print(f"回落：{trace.fallback_reason}")
        for echo in trace.grounding_echoes:
            print(
                f"每步回显：{echo.stage} 模型发出=({echo.raw_x}, {echo.raw_y})"
                f" 实际发送={echo.sent_width}x{echo.sent_height}"
            )

    # -- 落点偏差（真值来自画面）-----------------------------------------
    error_px = None
    if args.truth and result.final_point is not None:
        truth = _parse_point(args.truth)
        error_px = math.dist(
            (float(result.final_point[0]), float(result.final_point[1])), truth
        )
        print(
            f"落点偏差：{error_px:.1f} px（真值 {truth} → 落点 {result.final_point}；"
            "工单验收线 ≤2 px）"
        )

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    record = {
        "app": anchor.app,
        "version": anchor.version,
        "capability": {
            "availability": entry.availability.value,
            "reason": entry.reason,
        },
        "target": args.target,
        "control_type": args.control_type,
        "calibration": {
            "scale": outcome.detected_scale.value,
            "scale_verdict": outcome.scale_verdict,
            "max_error_px": best.max_error_px,
            "ok": outcome.calibration_ok,
            "admission_available": admission.available,
            "admission_reason": admission.reason,
            "bypassed": bool(args.bypass_admission),
        },
        "step": {
            "ok": result.ok,
            "landing_point": list(result.final_point) if result.final_point else None,
            "error_code": result.result.error_code.value if result.result.error_code else None,
            "error_message": result.result.error_message,
            "channel": trace.channel if trace else "",
            "failure_type": trace.failure_type if trace else "",
            "allows_visual_fallback": trace.allows_visual_fallback if trace else False,
            "model_calls": trace.model_calls if trace else 0,
            "locate_ms": trace.locate_ms if trace else 0.0,
            "execute_verify_ms": trace.execute_verify_ms if trace else 0.0,
        },
        "truth": args.truth,
        "landing_error_px": error_px,
    }
    path = out_dir / "live-switch.json"
    path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    if trace is not None:
        ActionLog(out_dir).append(build_record(trace))
    print(f"记录已写入：{path}")
    return 0 if result.ok else 1


class _BypassAdmission:
    """**测量口径**：绕过准入门（M6 的测量台同一个做法）。

    它**不改变**生产路径：生产路径上的视觉通道仍然只认 M5 的硬闸门。
    这里只是让「视觉通道在真机上能落多准」这件事能被量出来，
    并且**把真实判定如实带进追踪记录**。
    """

    def __init__(self, real):
        self.real = real

    def admit(self):
        from minicode.action.ports import VisualAdmission

        verdict = self.real.admit()
        return VisualAdmission(
            available=True,
            reason=(
                f"⚠️ 测量口径：绕过准入门（真实判定："
                f"{'可用' if verdict.available else '不可用'} —— {verdict.reason}）"
            ),
            scale=verdict.scale,
            scale_verdict=verdict.scale_verdict,
            landing_equivalence=verdict.landing_equivalence,
            equivalent_scales=verdict.equivalent_scales,
        )


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())

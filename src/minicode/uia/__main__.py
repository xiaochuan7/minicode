"""命令行入口：UIA 通道在真机上跑一遍。

::

    $env:PYTHONPATH = "src"
    python -m minicode.uia --target "开始" --window Shell_TrayWnd --out runs

**动真机前必须持有 ``.scratch/desktop.lock``**（人类占用时本命令直接拒绝）。

加 ``--disambiguate`` 时**接上模型**（工单 M4）：候选不唯一就在候选集内让模型
只做指认（消歧），模型**不产坐标**。凭据与两个脱敏开关都只从**仓库 ``.env``** 读。
"""

from __future__ import annotations

import argparse
import locale
import subprocess
import sys
from pathlib import Path

__all__ = ["main"]

#: 仓库根目录（锁脚本与 ``.scratch/`` 都在它下面）。
REPO_ROOT = Path(__file__).resolve().parents[3]

#: 真机占用锁的**唯一**实现（判定规则只有这一份，CLI 不另写一套）。
LOCK_SCRIPT = REPO_ROOT / "scripts" / "desktop_lock.py"


def _parse_args(argv):
    parser = argparse.ArgumentParser(
        prog="minicode.uia",
        description=(
            "UIA 通道（M3/M4）：定点查询 → 唯一定位 → 执行前重读 → 点中；"
            "加 --disambiguate 时候选不唯一就让模型在候选集内指认"
        ),
    )
    parser.add_argument("--target", required=True, help="目标控件名（例：开始）")
    parser.add_argument(
        "--window",
        default="Shell_TrayWnd",
        help="窗口类名（用 FindWindowW 命中；默认任务栏）",
    )
    parser.add_argument(
        "--control-type",
        default="Button",
        help="控件类型提示（默认 Button）：精确名字查不到时，靠它发一次"
        "「控件类型条件」的定点查询；目标不是按钮时改成别的类型",
    )
    parser.add_argument("--out", default="runs", help="动作记录落盘目录")
    parser.add_argument(
        "--ticket",
        default="M4",
        help="本轮票号（写进真机占用锁；默认 M4 —— 改了这条 CLI 的票就改它）",
    )
    parser.add_argument(
        "--disambiguate",
        action="store_true",
        help="接上模型做消歧（工单 M4）：候选不唯一时让模型在候选集内指认",
    )
    return parser.parse_args(argv)


def _acquire_lock(ticket: str) -> bool:
    """动真机前拿锁：**复用** ``scripts/desktop_lock.py``（判定规则只有那一份）。

    返回 ``True`` 表示**本进程是锁的持有者**（原来没有锁 → 本进程拿的 →
    用完要释放）；原来就有锁（agent 持有）→ 不是本进程拿的，不释放。
    """
    lock_path = REPO_ROOT / ".scratch" / "desktop.lock"
    existed = lock_path.exists()
    # 锁脚本的控制台输出按**本机代码页**编码（中文 Windows 是 GBK），
    # 这里跟着本机走，别写死 UTF-8（写死会把中文读成乱码）。
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
            (result.stderr or result.stdout).strip()
            or "拒绝动真机：拿不到真机占用锁"
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


def main(argv=None) -> int:
    args = _parse_args(argv)
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
    from minicode.action.records import ActionLog, ListTraceSink, build_record
    from minicode.action.seam import ActionExecutionSeam, SeamRequest
    from minicode.action.windows import (
        WindowsCaptureSource,
        WindowsInputInjector,
        WindowsWindowController,
        current_virtual_desktop,
    )
    from minicode.exec import dpi  # noqa: F401  (导入即声明 PMv2)
    from minicode.uia.capability import CapabilityTable
    from minicode.uia.channel import UiaChannel
    from minicode.uia.elements import rect_center
    from minicode.uia.windows import (
        WindowsCapabilityProbe,
        WindowsElementInvoker,
        WindowsTreeUnblocker,
        WindowsUiaQueryPort,
        describe_window,
    )

    hwnd = _find_window(args.window)
    anchor = describe_window(hwnd)
    port = WindowsUiaQueryPort()
    table = CapabilityTable(
        WindowsCapabilityProbe(), unblocker=WindowsTreeUnblocker()
    )
    pointer, redactor = _disambiguation_ends(args)
    channel = UiaChannel(
        query=port,
        capability=table,
        pointer=pointer,
        redactor=redactor,
    )
    sink = ListTraceSink()
    desktop = current_virtual_desktop()
    seam = ActionExecutionSeam(
        capture_sources=[WindowsCaptureSource()],
        tree=None,
        injector=WindowsInputInjector(desktop),
        window_controller=WindowsWindowController(),
        virtual_desktop=desktop,
        trace_sink=sink,
        locator=channel,
        invoker=WindowsElementInvoker(port),
        anchor=anchor,
    )
    result = seam.run(
        SeamRequest(
            target_description=args.target, control_type=args.control_type
        )
    )
    trace = sink.traces[-1] if sink.traces else None
    entry = table.availability(anchor)

    print("========== UIA 通道 ==========")
    print(f"窗口锚点：hwnd={hwnd} 应用={anchor.app} 版本={anchor.version}")
    print(
        f"能力表  ：{entry.availability.value}（{entry.reason}）"
        f"｜探测次数={table.probe_count}"
    )
    print(f"动作结果：ok={result.ok} 落点={result.final_point}")
    print(f"定点查询：最近一次 {port.last_query_ms:.1f} ms（要求 < 1000 ms）")
    print(
        f"候选上限：{channel.max_candidates}（来源：{channel.candidate_limit_source}）"
    )
    if result.result.error_code:
        print(f"错误码  ：{result.result.error_code.value}")
        print(f"错误信息：{result.result.error_message}")
    if result.rejected_candidates:
        print(f"候选集  ：{len(result.rejected_candidates)} 个（拒绝执行时回报）")
    if trace is not None:
        print(
            f"三段耗时：定位={trace.locate_ms:.1f}ms 决策={trace.decision_ms:.1f}ms "
            f"执行+校验={trace.execute_verify_ms:.1f}ms"
        )
        print(f"说明    ：{'；'.join(trace.result.notes) or '（无）'}")
        print(
            f"允许切视觉：{trace.allows_visual_fallback}"
            "（只有覆盖失败为真；本单不实现切视觉）"
        )
        if trace.selection_echo is not None:
            echo = trace.selection_echo
            print(
                f"每步回显：模型选择={list(echo.chosen_numbers)}"
                f" 名字={list(echo.chosen_names)}"
                f" 实际发送图片尺寸={echo.sent_width}x{echo.sent_height}"
                f" 模型调用={echo.model_calls}"
            )
        if trace.selected is not None and result.final_point is not None:
            left, top, right, bottom = trace.selected.rect
            center = rect_center(trace.selected.rect)
            delta = (
                (result.final_point[0] - center[0]) ** 2
                + (result.final_point[1] - center[1]) ** 2
            ) ** 0.5
            inside = (
                left <= result.final_point[0] < right
                and top <= result.final_point[1] < bottom
            )
            print(
                f"落点复验：与控件框中心偏差 {delta:.1f} px（要求 ≤2）"
                f"｜落点落在控件框内={inside}"
            )
        path = ActionLog(args.out).append(build_record(trace))
        print(f"动作记录：{path}")
    print("================================")
    return 0 if result.ok else 1


def _disambiguation_ends(args):
    """消歧的两端：指认端（模型）与脱敏器。

    没给 ``--disambiguate`` → 两端都是 ``None``（**零模型调用**的 M3 形态）。
    给了 → 凭据与两个脱敏开关都只从**仓库 ``.env``** 读；
    脱敏开关没开时**明确拒绝**（fail-closed，架构决策备忘 §2.6 C-15）。
    """
    if not args.disambiguate:
        return None, None

    from minicode.model.client import ModelClient
    from minicode.model.config import load_config
    from minicode.model.request_log import JsonlRequestLog
    from minicode.redact.config import load_model_context_config
    from minicode.redact.redactor import TreeRedactor
    from minicode.uia.pointer import ModelCandidatePointer

    config = load_config()
    client = ModelClient(config, JsonlRequestLog(Path(args.out) / "model-requests.jsonl"))
    context = load_model_context_config()
    if not (context.send_tree_to_model and context.redaction_enabled):
        raise SystemExit(
            "脱敏未上线：仓库 .env 里 MINICODE_SEND_TREE_TO_MODEL 与 "
            "MINICODE_REDACTION_ENABLED 都得打开，才允许把控件树送模型"
            "（架构决策备忘 §2.6 C-15）"
        )
    return ModelCandidatePointer(client), TreeRedactor(context)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())

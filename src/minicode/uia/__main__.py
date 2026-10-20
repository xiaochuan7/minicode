"""命令行入口：UIA 通道在真机上跑一遍（**零模型调用、零费用**）。

::

    $env:PYTHONPATH = "src"
    python -m minicode.uia --target "开始" --window Shell_TrayWnd --out runs

**动真机前必须持有 ``.scratch/desktop.lock``**（人类占用时本命令直接拒绝）。
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
        description="M3 UIA 通道：定点查询 → 唯一定位 → 执行前重读 → 点中（零模型调用）",
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
        "--ticket", default="M3", help="本轮票号（写进真机占用锁；默认 M3）"
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
    channel = UiaChannel(query=port, capability=table)
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

    print("========== M3 UIA 通道 ==========")
    print(f"窗口锚点：hwnd={hwnd} 应用={anchor.app} 版本={anchor.version}")
    print(
        f"能力表  ：{entry.availability.value}（{entry.reason}）"
        f"｜探测次数={table.probe_count}"
    )
    print(f"动作结果：ok={result.ok} 落点={result.final_point}")
    print(f"定点查询：最近一次 {port.last_query_ms:.1f} ms（要求 < 1000 ms）")
    if result.result.error_code:
        print(f"错误码  ：{result.result.error_code.value}")
        print(f"错误信息：{result.result.error_message}")
    if result.rejected_candidates:
        print(f"候选集  ：{len(result.rejected_candidates)} 个（唯一性拒绝时回报）")
    if trace is not None:
        print(
            f"三段耗时：定位={trace.locate_ms:.1f}ms 决策={trace.decision_ms:.1f}ms "
            f"执行+校验={trace.execute_verify_ms:.1f}ms"
        )
        print(f"说明    ：{'；'.join(trace.result.notes) or '（无）'}")
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


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())

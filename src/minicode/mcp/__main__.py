"""``python -m minicode.mcp`` —— MCP server（stdio 传输，工单 M19）。

宿主（DSH / Claude Code）把它当子进程拉起来，之后走标准输入输出说 MCP。

⚠️ **动真机前必须持有真机占用锁**（真机占用锁：人机互斥）：
真机模式下**服务运行期间一直占着**真机 —— 人要用电脑就得先停服务
（与 HTTP 前端同一套规矩）。
"""

from __future__ import annotations

import argparse
import contextlib
import sys
from typing import Any

from .server import McpServer, run_stdio
from .tools import build_tools

__all__ = ["main"]


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="minicode.mcp",
        description="桌面控制的 MCP server（stdio 传输，工单 M19）",
    )
    parser.add_argument(
        "--out",
        default="runs/mcp",
        help="动作日志目录（动作记录追加到这里）",
    )
    parser.add_argument(
        "--window-class",
        default="Shell_TrayWnd",
        help="目标窗口的类名（真机模式；默认任务栏）",
    )
    parser.add_argument(
        "--fake",
        action="store_true",
        help="用假桌面跑（不碰真桌面、不注入任何输入）",
    )
    return parser


def main(argv: Any = None) -> int:
    """起 MCP server（跑到标准输入流结束）。"""
    args = _build_parser().parse_args(argv)

    # ⚠️ **标准输出只走协议消息**：拿锁与接线的横幅都打到标准输出
    # （`lock.acquire` 与真机接线都会 print）—— 在这里把它们**引到标准错误**，
    # 否则宿主读到的是一行乱码而不是 JSON。
    #
    # ⚠️ **真机接线要在这个重定向里造**：审批人（`approval/console.py`）
    # 在**构造时**抓一份 `sys.stdout`（它要把审批请求给人看）——
    # 在这里造，它抓到的就是标准错误，审批请求不会混进协议流。
    with contextlib.redirect_stdout(sys.stderr):
        holds_lock = False
        if not args.fake:
            from minicode.lock import acquire

            # **持锁到服务停**（真机模式下服务一直占着真机）。
            holds_lock = acquire("M19")
        try:
            service = _build_service(args)
        except BaseException:
            if holds_lock:
                from minicode.lock import release

                release()
            raise
    try:
        server = McpServer(build_tools(service))
        # ⚠️ **横幅走标准错误**：标准输出只走协议消息。
        print(
            "========== 对外形态 · MCP ==========\n"
            "传输：stdio（宿主把本进程当 MCP server 拉起）\n"
            "工具：minicode_action / minicode_program / minicode_observe\n"
            "=====================================",
            file=sys.stderr,
        )
        return run_stdio(server)
    finally:
        if holds_lock:
            from minicode.lock import release

            release()


def _build_service(args: argparse.Namespace) -> Any:
    """造动作服务（假桌面 / 真机）。"""
    if args.fake:
        from minicode.serve.fakes import fake_service

        return fake_service(args.out)
    from minicode.serve.real import build_real_service

    return build_real_service(args.out, window_class=args.window_class)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())

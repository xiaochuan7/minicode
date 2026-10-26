"""**对外形态的命令行前端**（工单 M18）。

三个子命令：

* ``action`` —— 发起一次动作，打印结果（可 ``--json`` 打印机器可读形状），
  **退出码区分成功与失败**（AC1）；
* ``log`` —— 读回动作日志（动作记录一行一条）；
* ``http`` —— 起一个 HTTP 服务（``POST /v1/actions``）。

⚠️ **本模块只做「解析 → 调服务 → 渲染」**：动作语义在
:mod:`minicode.serve.service` 里，与 HTTP 前端**共用**（AC3）。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from minicode.action.protocol import PROTOCOL_VERSION

from .service import ActionService

__all__ = ["main", "build_service", "EXIT_OK", "EXIT_FAILED"]

#: 动作成功。
EXIT_OK = 0
#: 动作失败（协议错误码在输出里）。
EXIT_FAILED = 1


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="minicode.serve",
        description="桌面控制的独立命令行前端（工单 M18）",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    act = sub.add_parser("action", help="发起一次动作")
    act.add_argument(
        "--target",
        default="",
        help="目标描述（自然语言，如「开始」）；截图/等待这类动作不需要",
    )
    act.add_argument(
        "--action",
        default="click",
        help="动作种类（本层接：需要落点的动作、截图/等待、放大；默认 click）",
    )
    act.add_argument(
        "--param",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="动作参数（可重复，如 --param click_count=2）",
    )
    act.add_argument(
        "--protocol",
        default=PROTOCOL_VERSION,
        help=f"动作协议版本号（默认 {PROTOCOL_VERSION}；不匹配会明确报错）",
    )
    act.add_argument("--out", default="runs/serve", help="动作日志目录")
    act.add_argument("--json", action="store_true", help="按机器可读形状打印响应")
    act.add_argument(
        "--fake",
        action="store_true",
        help="用假桌面跑一遍（不碰真桌面、不注入任何输入）",
    )

    log = sub.add_parser("log", help="读回动作日志")
    log.add_argument("--out", default="runs/serve", help="动作日志目录")
    log.add_argument("--json", action="store_true", help="按机器可读形状打印")

    http = sub.add_parser("http", help="起一个 HTTP 服务（POST /v1/actions）")
    http.add_argument("--host", default="127.0.0.1", help="监听地址（默认只听回环）")
    http.add_argument("--port", type=int, default=8765, help="监听端口")
    http.add_argument("--out", default="runs/serve", help="动作日志目录")
    http.add_argument(
        "--fake",
        action="store_true",
        help="用假桌面跑（不碰真桌面、不注入任何输入）",
    )
    return parser


def _parse_params(pairs: list[str]) -> dict[str, Any]:
    """``KEY=VALUE`` → 键值表（值按 JSON 解析，解析不了就当字符串）。"""
    params: dict[str, Any] = {}
    for pair in pairs:
        key, _, raw = pair.partition("=")
        if not key:
            continue
        try:
            params[key] = json.loads(raw)
        except json.JSONDecodeError:
            params[key] = raw
    return params


def build_service(*, log_directory: str | Path, fake: bool) -> ActionService:
    """造一个动作服务。

    ``fake=True`` → **假桌面**（假截图源 / 假控件树 / 假注入器）：
    不碰真桌面、不注入任何输入、不花钱。
    ``fake=False`` → 真机接线（UIA 通道 + 真机注入 + 审批闸门 + 提权检测）。
    """
    if fake:
        return _fake_service(log_directory)
    return _real_service(log_directory)


def _fake_service(log_directory: str | Path) -> ActionService:
    """假桌面的服务（**接线只有一份** —— 与单测跑的是同一份）。"""
    from .fakes import fake_service

    return fake_service(log_directory)


def _real_service(log_directory: str | Path) -> ActionService:
    """真机接线（**不碰锁** —— 锁由 :func:`_run_act` 持有到动作执行完）。

    ⚠️ **动真机前必须拿锁**（真机占用锁：人机互斥）：人类在用电脑时
    本命令**拒绝动真机**，不与人类抢鼠标。
    """
    from .real import build_real_service

    return build_real_service(log_directory)


def _print_action(response, as_json: bool) -> None:
    if as_json:
        print(json.dumps(response.to_dict(), ensure_ascii=False, indent=2))
        return
    print("========== 对外形态 · 一次动作 ==========")
    print(f"协议版本：{response.protocol_version}")
    print(f"动作结果：ok={response.ok}")
    if response.error_code:
        print(f"错误码  ：{response.error_code}")
    if response.error_message:
        print(f"错误信息：{response.error_message}")
    if response.action:
        print(f"动作    ：{response.action.get('intent') or response.action.get('kind')}")
    if response.landing_point:
        print(f"最终落点：{response.landing_point}")
    print("========================================")


def _run_act(args) -> int:
    """发起一次动作。

    ⚠️ **真机路径的锁持有到动作执行完**（真机占用锁：人机互斥）：
    锁只在**接线**的时候拿着是不够的 —— 真正的点击发生在
    :meth:`ActionService.execute` 里，那时锁必须还在。
    """
    payload = {
        "protocol": args.protocol,
        "action": args.action,
        "target": args.target,
        "params": _parse_params(args.param),
    }
    if args.fake:
        service = build_service(log_directory=args.out, fake=True)
        response = service.execute(payload)
    else:
        from minicode.lock import acquire, release

        holds_lock = acquire("M18")
        try:
            service = build_service(log_directory=args.out, fake=False)
            response = service.execute(payload)
        finally:
            if holds_lock:
                release()
    _print_action(response, args.json)
    return EXIT_OK if response.ok else EXIT_FAILED


def _run_log(args) -> int:
    """读回动作日志（**只读文件** —— 不造桌面、不下发任何动作）。"""
    from minicode.action.records import ActionLog

    records = ActionLog(args.out).records()
    if args.json:
        print(json.dumps(records, ensure_ascii=False, indent=2))
        return EXIT_OK
    print(f"========== 对外形态 · 动作日志（{len(records)} 条）==========")
    for record in records:
        target = (record.get("target") or {}).get("name") or ""
        point = record.get("point") or {}
        print(
            f"{record.get('timestamp', '')} | {record.get('action', ''):<10} | "
            f"{target:<12} | ok={record.get('result', {}).get('ok')} | "
            f"落点=({point.get('x')}, {point.get('y')})"
        )
    print("============================================")
    return EXIT_OK


def main(argv=None) -> int:
    args = _build_parser().parse_args(argv)
    if args.command == "action":
        return _run_act(args)
    if args.command == "http":
        from .http import serve_forever

        return serve_forever(args.out, host=args.host, port=args.port, fake=args.fake)
    return _run_log(args)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())

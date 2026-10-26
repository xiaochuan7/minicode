"""``minicode`` 命令行总入口。

装完之后（``pip install -e ".[vision,exec]"``）::

    minicode --help
    minicode action --target "开始"     # 真机最小闭环：截图 → 定位 → 点击 → 校验
    minicode ground --help              # 视觉定位通道
    minicode calib --help               # 视觉通道标定与准入

每个子命令都是对 ``python -m minicode.<包>`` 的转发：参数原样透传，
退出码原样返回。
"""

from __future__ import annotations

import importlib
import sys

__all__ = ["SUBCOMMANDS", "main"]

#: 子命令 → (子包, 一句话说明)；顺序即 ``--help`` 的展示顺序。
#:
#: ``capability`` 是 UIA 通道的能力探测入口（能力表按「应用 + 版本」记录
#: 通道可用性，见 ``src/minicode/uia/capability.py``）；其余命令一一对应同名子包。
SUBCOMMANDS: tuple[tuple[str, str, str], ...] = (
    ("capability", "minicode.uia", "UIA 通道能力探测：读控件树并回报候选"),
    ("calib", "minicode.calib", "视觉通道标定：探针图 → 判定标度 → 准入判定"),
    ("ground", "minicode.ground", "视觉定位：截图 → 候选表 → 模型选编号 → 落点"),
    ("action", "minicode.action", "最小闭环：截图 → 定位 → 点击 → 校验"),
    ("eval", "minicode.eval", "评测：数据集跑批与统计（ScreenSpot-Pro 等）"),
    ("e2e", "minicode.e2e", "端到端任务跑批（真机；动真机前先拿桌面锁）"),
    ("serve", "minicode.serve", "本地 HTTP 服务 / 动作记录查询"),
    ("mcp", "minicode.mcp", "MCP server：把动作能力暴露给外部 agent"),
)


def _help_text() -> str:
    """总入口的帮助文本（只列命令，不复制各子命令的参数）。"""
    width = max(len(name) for name, _, _ in SUBCOMMANDS)
    lines = [
        "minicode —— Windows 桌面 computer-use agent",
        "",
        "用法：minicode <命令> [参数]",
        "",
        "命令：",
    ]
    lines += [f"  {name.ljust(width)}  {blurb}" for name, _, blurb in SUBCOMMANDS]
    lines += [
        "",
        "各命令的完整参数：minicode <命令> --help",
        "两条最常见的用法：",
        "  minicode --help",
        '  minicode action --target "开始"',
    ]
    return "\n".join(lines)


def _dispatch(name: str, argv: list[str]) -> int:
    """把子命令转发到对应子包的 ``main``（参数原样透传）。"""
    module_name = next(module for command, module, _ in SUBCOMMANDS if command == name)
    module = importlib.import_module(f"{module_name}.__main__")
    result = module.main(argv)
    return 0 if result is None else int(result)


def main(argv: list[str] | None = None) -> int:
    """入口：``--help`` 列子命令，其余原样转发。"""
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] in ("-h", "--help"):
        print(_help_text())
        return 0
    name = args[0]
    if name not in [command for command, _, _ in SUBCOMMANDS]:
        print(f"minicode: 未知命令 {name!r}", file=sys.stderr)
        print(file=sys.stderr)
        print(_help_text(), file=sys.stderr)
        return 2
    return _dispatch(name, args[1:])


if __name__ == "__main__":
    sys.exit(main())

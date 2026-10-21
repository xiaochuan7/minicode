"""``python -m minicode.privacy`` —— 生成 / 核对**策略文档**（工单 M20 AC 3）。

文档**由实现生成**：改了实现里的规则，重跑这个命令就能把
``docs/privacy-policy.md`` 更新到一致（单测还会钉住「仓库里那份 = 现在渲染的那份」）。

::

    $env:PYTHONPATH="src"; python -m minicode.privacy            # 写回 docs/privacy-policy.md
    $env:PYTHONPATH="src"; python -m minicode.privacy --check    # 只核对，不写
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .document import render_policy_document

__all__ = ["main"]

#: 仓库里那份策略文档的默认位置（相对仓库根）。
DEFAULT_DOC = Path("docs") / "privacy-policy.md"


def _repo_root() -> Path:
    """仓库根（``src/minicode/privacy/__main__.py`` 往上三层）。"""
    return Path(__file__).resolve().parents[3]


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="minicode.privacy",
        description="生成 / 核对隐私策略文档（工单 M20）",
    )
    parser.add_argument(
        "--out",
        default=None,
        help=f"文档路径（默认 {DEFAULT_DOC}）",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="只核对（文档与实现不一致时退出码非 0），不写文件",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    path = (
        Path(args.out)
        if args.out is not None
        else _repo_root() / DEFAULT_DOC
    )
    document = render_policy_document()
    if args.check:
        current = path.read_text(encoding="utf-8") if path.is_file() else ""
        if current != document:
            print(f"策略文档与实现不一致：{path}（重跑 python -m minicode.privacy 更新）")
            return 1
        print(f"策略文档与实现一致：{path}")
        return 0
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(document, encoding="utf-8")
    print(f"已写出策略文档：{path}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())

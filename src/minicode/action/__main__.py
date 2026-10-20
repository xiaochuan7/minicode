"""``python -m minicode.action`` —— 一条命令跑完最小闭环。"""

from __future__ import annotations

import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())

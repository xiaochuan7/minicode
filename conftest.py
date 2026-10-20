"""pytest 配置：把 ``src`` 放进 ``sys.path``。

本仓库是 src-layout，``minicode`` 包没有安装进 site-packages；
测试与命令行入口（``python -m minicode.action.cli``）都依赖这条路径。
"""

from __future__ import annotations

import os
import sys

_SRC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

"""**真机接线**（工单 M18）。

命令行与 HTTP 两个前端在真机上跑的时候，**接线只有这一份**：
:func:`minicode.uia.desktop.build_desktop`（UIA 通道 + 真机注入 + 审批闸门 +
提权检测）—— 与 :mod:`minicode.uia` 的命令行**同一套口径**。

⚠️ **动真机前必须持有真机占用锁**（AGENTS.md「真机占用锁」）——
调用方（:mod:`minicode.serve.cli` / :mod:`minicode.serve.http`）负责拿锁与放锁，
本模块只管接线。
"""

from __future__ import annotations

from pathlib import Path

from minicode.action.records import ListTraceSink

from .service import ActionService

__all__ = ["build_real_service"]


def build_real_service(
    log_directory: str | Path,
    *,
    window_class: str = "Shell_TrayWnd",
) -> ActionService:
    """真机上的动作服务。

    :param window_class: 目标窗口的类名（``FindWindowW`` 找）。
    """
    # ⚠️ **DPI 感知在 `uia/desktop.py::build_desktop` 里声明**（那里是真机接线的
    # 第一行）—— 本层不重复声明，两条入口走的是**同一份**接线。
    from minicode.uia.desktop import build_desktop

    desktop = build_desktop(window_class=window_class)

    def factory(sink: ListTraceSink):
        return desktop.new_seam(sink)

    return ActionService(seam_factory=factory, log_directory=log_directory)

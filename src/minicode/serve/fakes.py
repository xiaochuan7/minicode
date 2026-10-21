"""**假桌面接线**（工单 M18）。

对外形态的两个前端（命令行 / HTTP）在 CI 与单测里都跑在**假桌面**上：
假截图源 / 假控件树 / 假注入器 —— **不碰真桌面、不注入任何输入、不花钱**。

⚠️ **假桌面只是把缝以下的依赖换掉**：动作语义、坐标口径、错误码
**一个字都不改**（那正是本项目「缝只有一个」的纪律）。
"""

from __future__ import annotations

import math

from minicode.action.fakes import (
    FakeCaptureSource,
    FakeControlTree,
    FakeInputInjector,
    FakeWindowController,
    VirtualClock,
)
from minicode.action.geometry import VirtualDesktop, rect_center
from minicode.action.ports import ControlCandidate
from minicode.action.records import ListTraceSink
from minicode.action.seam import ActionExecutionSeam

__all__ = ["FAKE_LANDING", "FAKE_TARGET", "fake_wiring"]

#: 假桌面上那个按钮的名字。
FAKE_TARGET = "开始"

#: 它的控件框（物理像素）→ 落点取框中心。
_FAKE_RECT = (1016, 1528, 1083, 1600)

#: 假桌面上那次点击的落点（物理像素）。
#:
#: ⚠️ **框 → 落点的换算只有一份**（`action/geometry.py::rect_center`）——
#: 这里不自己算第二遍。取整用 ``math.floor``，与假桌面实际走的那条路径**逐字一致**
#: （M0 老路径：截图像素空间取中心再 `floor`，见 `action/seam.py`）。
#: ⚠️ 用 `floor` 而不是 `int()`：后者是**向零截断**（负坐标不同 ——
#: 副屏在左时虚拟桌面原点为负）。
#:
#: ⚠️ **假桌面与真机的取整口径差 1 px**：真机走 UIA 通道，落点取**就近**取整
#: （`center_point`）—— 同一块控件，假桌面给 1049、真机给 1050。见
#: `docs/reviews/M18.md` §4。
_FAKE_LANDING = rect_center(_FAKE_RECT)
FAKE_LANDING = (math.floor(_FAKE_LANDING[0]), math.floor(_FAKE_LANDING[1]))


def fake_wiring(
    *,
    injector: FakeInputInjector | None = None,
) -> tuple[object, FakeInputInjector]:
    """假桌面的接线工厂（返回（造缝的工厂，注入器））。

    注入器一并返回，是为了**断言「没有动作下发」**（版本对不上时）。
    """
    clock = VirtualClock()
    candidate = ControlCandidate(
        name=FAKE_TARGET,
        control_type="Button",
        rect=_FAKE_RECT,
        window_handle=65784,
        window_title="任务栏",
    )
    real_injector = injector if injector is not None else FakeInputInjector()
    controller = FakeWindowController(foreground=65784)

    def factory(sink: ListTraceSink) -> ActionExecutionSeam:
        return ActionExecutionSeam(
            capture_sources=(FakeCaptureSource(width=2560, height=1600),),
            tree=FakeControlTree((candidate,)),
            injector=real_injector,
            window_controller=controller,
            virtual_desktop=VirtualDesktop(0, 0, 2560, 1600),
            sleeper=clock.sleep,
            clock=clock.time,
            trace_sink=sink,
        )

    return factory, real_injector

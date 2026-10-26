"""**假桌面接线**（工单 M18 / M19）。

对外形态的三个前端（命令行 / HTTP / MCP）在 CI 与单测里都跑在**假桌面**上：
假截图源 / 假控件树 / 假注入器 —— **不碰真桌面、不注入任何输入、不花钱**。

⚠️ **假桌面只是把缝以下的依赖换掉**：动作语义、坐标口径、错误码
**一个字都不改**（那正是本项目「缝只有一个」的纪律）。
"""

from __future__ import annotations

import math
from pathlib import Path

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
from minicode.redact.config import ModelContextConfig
from minicode.redact.redactor import TreeRedactor
from minicode.verification.fakes import FakeStateObserver

from .service import ActionService

__all__ = [
    "FAKE_LANDING",
    "FAKE_TARGET",
    "fake_candidate",
    "fake_observer",
    "fake_redactor",
    "fake_service",
    "fake_wiring",
]

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
#: （`center_point`）—— 同一块控件，假桌面给 1049、真机给 1050。
_FAKE_LANDING = rect_center(_FAKE_RECT)
FAKE_LANDING = (math.floor(_FAKE_LANDING[0]), math.floor(_FAKE_LANDING[1]))


def fake_candidate() -> ControlCandidate:
    """假桌面上那个按钮的**控件候选**（观察与定位用的是同一个）。"""
    return ControlCandidate(
        name=FAKE_TARGET,
        control_type="Button",
        rect=_FAKE_RECT,
        window_handle=65784,
        window_title="任务栏",
    )


def fake_observer() -> FakeStateObserver:
    """假桌面的**定点观察**端口（按名字给控件）。

    ⚠️ 形状与真机那一路**同一个**
    （:class:`minicode.verification.layer.StateObserver`）——
    换实现不改端口形状。
    """
    return FakeStateObserver({FAKE_TARGET: (fake_candidate(),)})


def fake_redactor() -> TreeRedactor:
    """假桌面的脱敏器（**两个开关都开着**）。

    ⚠️ 假桌面的控件树是**合成的**（没有真实敏感内容），
    但**脱敏这一关照样要过** —— 单测要能断言「出去的是已脱敏的文本」。
    真机那一路读**仓库 ``.env``** 的两个开关（缺省都关，fail-closed）。
    """
    return TreeRedactor(
        ModelContextConfig(send_tree_to_model=True, redaction_enabled=True)
    )


def fake_wiring(
    *,
    injector: FakeInputInjector | None = None,
) -> tuple[object, FakeInputInjector]:
    """假桌面的接线工厂（返回（造缝的工厂，注入器））。

    注入器一并返回，是为了**断言「没有动作下发」**（版本对不上时）。
    """
    clock = VirtualClock()
    candidate = fake_candidate()
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


def fake_service(
    log_directory: str | Path,
    *,
    injector: FakeInputInjector | None = None,
) -> ActionService:
    """假桌面上的动作服务（**接线只有一份** —— 与单测跑的是同一份）。"""
    wiring, _injector = fake_wiring(injector=injector)
    return ActionService(
        seam_factory=wiring,
        log_directory=log_directory,
        observer=fake_observer(),
        redactor=fake_redactor(),
    )

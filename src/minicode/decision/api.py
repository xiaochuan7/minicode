"""动作程序能看到的接口：**动作协议暴露的动作** + 定位。

模型写的程序调的就是这里（工单 M10 AC 6：程序只能调用动作协议暴露的动作）。

* 走**动作执行缝**的动作（点击）由缝负责定位与执行；
* 文本 / 按键 / 等待 / 截图走**动作执行器**（协议里那些不需要定位的动作）；
* ``find`` 只定位不动作（``ControlLocator`` 端口）；
* ``screenshot`` **只给摘要**（宽高 + 截图哈希）—— 与「截图只落哈希」同一条红线，
  程序拿不到图像数据。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from minicode.action.ports import (
    CapturedFrame,
    ControlLocator,
    LocateRequest,
    WindowAnchor,
)
from minicode.action.program import PROGRAM_API_NAMES
from minicode.action.protocol import Action, ActionKind
from minicode.action.seam import ActionExecutionSeam, SeamRequest

__all__ = ["ProtocolApi"]


class ProtocolApi:
    """动作程序的接口实现（记录每一次调用，供回灌与审计）。"""

    def __init__(
        self,
        *,
        seam: ActionExecutionSeam,
        locator: ControlLocator | None = None,
        anchor: WindowAnchor | None = None,
        frame_provider: Callable[[], CapturedFrame | None] | None = None,
    ):
        self._seam = seam
        self._locator = locator
        self._anchor = anchor or WindowAnchor(hwnd=0)
        self._frame_provider = frame_provider
        #: 程序每一次接口调用的记录。
        self.calls: list[dict[str, Any]] = []
        #: 程序请求交还给人了吗。
        self.asked_human = False
        self.asked_human_reason = ""
        #: 最近一次截图拿到的帧（供上下文窗口留完整图）。
        self.last_frame: CapturedFrame | None = None

    # ------------------------------------------------------------------
    # 暴露给程序的接口
    # ------------------------------------------------------------------

    def as_mapping(self) -> dict[str, Callable[..., Any]]:
        """程序作用域里能看到的名字（**必须与协议暴露的那份白名单一致**）。"""
        mapping: dict[str, Callable[..., Any]] = {
            "click": self.click,
            "find": self.find,
            "type_text": self.type_text,
            "press_key": self.press_key,
            "wait": self.wait,
            "screenshot": self.screenshot,
            "ask_user": self.ask_user,
        }
        if set(mapping) != set(PROGRAM_API_NAMES):
            # 白名单只有一份（在 action/program.py）；这里对不上就直接炸，
            # 不让「多一个能调的名字」悄悄进程序作用域。
            raise ValueError(
                "动作程序的接口面与白名单不一致："
                f"{sorted(mapping)} ≠ {sorted(PROGRAM_API_NAMES)}"
            )
        return mapping

    def click(self, target: str) -> dict[str, Any]:
        """按目标描述定位并点击（走动作执行缝）。"""
        result = self._seam.run(
            SeamRequest(
                target_description=str(target),
                action_kind=ActionKind.CLICK,
            )
        )
        record: dict[str, Any] = {
            "kind": "click",
            "target": str(target),
            "ok": result.ok,
            "point": list(result.final_point) if result.final_point else None,
            "error": (
                result.result.error_code.value if result.result.error_code else None
            ),
        }
        self.calls.append(record)
        return record

    def find(self, target: str) -> dict[str, Any]:
        """只定位，不动作。"""
        if self._locator is None:
            record = {
                "kind": "find",
                "target": str(target),
                "ok": False,
                "point": None,
                "error": "没有定位通道",
            }
            self.calls.append(record)
            return record
        located = self._locator.locate(
            LocateRequest(
                target_description=str(target),
                anchor=self._anchor,
                frame=self._frame(),
            )
        )
        record = {
            "kind": "find",
            "target": str(target),
            "ok": located.ok,
            "point": (
                list(located.landing_point) if located.landing_point else None
            ),
            "error": located.error_code.value if located.error_code else None,
        }
        self.calls.append(record)
        return record

    def type_text(self, text: str) -> dict[str, Any]:
        """输入文本。

        ⚠️ **文本与按键不走动作执行缝**：缝的契约里这两种动作是
        「请直接用动作执行器」（`seam.py` 的 ``SeamRequest`` 明确不收它们）。
        因此这两种动作**不产追踪记录** —— 动作日志的欠账记在
        ``docs/reviews/M10.md``（M11/M17 接手动作日志时补齐）。
        """
        result = self._seam.executor.execute(
            Action(ActionKind.TYPE, {"text": str(text)}, intent="输入文本")
        )
        return self._record_action("type_text", result, detail=str(text))

    def press_key(self, *keys: str) -> dict[str, Any]:
        """按键（``press_key('ctrl', 'c')``）。同 :meth:`type_text`：不走缝。"""
        combo = tuple(str(key) for key in keys)
        result = self._seam.executor.execute(
            Action(ActionKind.KEY, {"combo": combo}, intent="按键")
        )
        return self._record_action("press_key", result, detail=",".join(combo))

    def wait(self, ms: int) -> dict[str, Any]:
        """等待若干毫秒（**走动作执行缝**：等待是「不需要定位目标」的动作）。"""
        result = self._seam.run(
            SeamRequest(
                target_description="等待",
                action_kind=ActionKind.WAIT,
                params={"ms": int(ms)},
            )
        )
        return self._record_action("wait", result.result, detail=str(ms))

    def screenshot(self) -> dict[str, Any]:
        """截图：**只给摘要**，不给图像数据（**走动作执行缝**）。"""
        result = self._seam.run(
            SeamRequest(target_description="截图", action_kind=ActionKind.SCREENSHOT)
        )
        frame = result.result.captured
        if frame is not None:
            self.last_frame = frame
        record = self._record_action("screenshot", result.result)
        record["width"] = frame.width if frame is not None else None
        record["height"] = frame.height if frame is not None else None
        record["sha256"] = frame.digest() if frame is not None else None
        return record

    def capture_frame(self) -> CapturedFrame | None:
        """抓一帧新画面（harness 每轮结束时取它当**新观察**）。

        走的是 ``screenshot`` 动作 —— 与程序里调 ``screenshot()`` 同一个端口，
        同样经过动作执行缝（因此有追踪记录）。
        """
        result = self._seam.run(
            SeamRequest(target_description="截图", action_kind=ActionKind.SCREENSHOT)
        )
        frame = result.result.captured
        if frame is not None:
            self.last_frame = frame
            return frame
        return None

    def ask_user(self, reason: str) -> dict[str, Any]:
        """把任务交还给人（harness 侧的停止条件承载终止）。"""
        self.asked_human = True
        self.asked_human_reason = str(reason)
        record = {
            "kind": "ask_user",
            "target": str(reason),
            "ok": True,
            "point": None,
            "error": None,
        }
        self.calls.append(record)
        return record

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------

    def _record_action(
        self, kind: str, result: Any, *, detail: str = ""
    ) -> dict[str, Any]:
        record = {
            "kind": kind,
            "target": detail,
            "ok": bool(result.ok),
            "point": (
                list(result.landing_point) if result.landing_point else None
            ),
            "error": result.error_code.value if result.error_code else None,
        }
        self.calls.append(record)
        return record

    def _frame(self) -> CapturedFrame | None:
        if self._frame_provider is None:
            return None
        return self._frame_provider()

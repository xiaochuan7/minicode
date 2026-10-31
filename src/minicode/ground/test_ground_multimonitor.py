"""**多显示器口径**的单测（审查报告 Bug #1）。

场景：双屏虚拟桌面 —— 主屏 1920×1080 @ (0, 0)，副屏 1920×1080 @ (-1920, 0)
→ 虚拟桌面 3840×1080，原点 (-1920, 0)。

单屏机器上「主屏 SM_CXSCREEN」与「整个虚拟桌面」两个口径恰好重合，所以
既有测试全绿也测不出接线的错位；这里用**合成帧**把两个口径撕开：

* 整屏帧按**抓帧口径**（虚拟桌面 3840×1080、原点 -1920）给；
* 通道的 ``full_screen_size`` 若仍接**主屏口径**（1920×1080），
  ``locate()`` 必须在入口被尺寸门拒掉（这是修掉的 Bug 的形状）；
* 接对口径时，负原点副屏上的落点换算必须正确。
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from minicode.action.errors import ErrorCode
from minicode.action.ports import CapturedFrame, PixelFormat
from minicode.coord.coordinates import ModelScale
from minicode.ground import TwoStageGrounder, VisualLocateRequest
from minicode.model.client import ModelReply
from minicode.model.effort import Effort

#: 本文件顶上的双屏场景（主屏 + 左侧副屏 → 虚拟桌面原点为负）。
_VIRTUAL_W, _VIRTUAL_H = 3840, 1080
_VIRTUAL_ORIGIN = (-1920, 0)
_PRIMARY_W, _PRIMARY_H = 1920, 1080


def _reply(x: float, y: float) -> ModelReply:
    return ModelReply(
        text=json.dumps({"x": x, "y": y}),
        model="stub",
        endpoint="stub://",
        effort=Effort.LOW,
        http_status=200,
        latency_ms=1.0,
    )


class _StubModel:
    def __init__(self, *outcomes: ModelReply):
        self._outcomes = list(outcomes)

    def complete(
        self,
        prompt: str,
        *,
        effort: Effort | str | None = None,
        max_tokens: int = 2000,
        image_base64: str | None = None,
        thinking_disabled: bool = False,
    ) -> ModelReply:
        if not self._outcomes:
            raise AssertionError("桩模型没有预置回答")
        return self._outcomes.pop(0)


def _virtual_frame() -> CapturedFrame:
    """一张 3840×1080、原点 (-1920, 0) 的合成整屏帧（抓帧口径）。"""
    width, height = _VIRTUAL_W, _VIRTUAL_H
    # 「一行字节重复 + 逐行错位」的快路径（细节足够，又不至于造 12 MB 随机数）。
    row = bytes((index * 37) % 256 for index in range(width * 3))
    rows = []
    for y in range(height):
        shift = (y % 64) * 3
        rows.append(row[shift:] + row[:shift])
    return CapturedFrame(
        width=width,
        height=height,
        left=_VIRTUAL_ORIGIN[0],
        top=_VIRTUAL_ORIGIN[1],
        payload=b"".join(rows),
        backend="fake",
        source="synthetic-virtual-desktop",
        pixel_format=PixelFormat.BGR,
    )


class MultiMonitorContractTests(unittest.TestCase):
    """整屏口径 = 抓帧口径（虚拟桌面），负原点换算正确。"""

    def test_a_primary_screen_size_gate_rejects_a_virtual_desktop_frame(self):
        """**修掉的 Bug 的形状**：通道接主屏口径 → 虚拟桌面帧在入口被拒。

        修复前（审查 Bug #1）：真机接线把主屏 SM_CXSCREEN 传给
        ``full_screen_size``，而抓帧抓整个虚拟桌面 —— 多显示器下每次
        视觉定位入口即 ``LOCATE_FAILED``，视觉通道整条不可用。
        本测试把这一错位钉死为**显式失败**（不许静默换算）。
        """
        grounder = TwoStageGrounder(
            _StubModel(_reply(500.0, 500.0)),
            scale=ModelScale.RELATIVE_0_1000,
            model_input_size=(_PRIMARY_W, _PRIMARY_H),
            full_screen_size=(_PRIMARY_W, _PRIMARY_H),  # ← 主屏口径（错的接线）
            coarse_only=True,
            effort=Effort.LOW,
        )
        result = grounder.locate(
            VisualLocateRequest(
                target_description="开始",
                frame=_virtual_frame(),
                surface=(-1920, 0, 1920, 1080),
            )
        )
        self.assertFalse(result.ok)
        self.assertEqual(result.error_code, ErrorCode.LOCATE_FAILED)

    def test_a_virtual_desktop_frame_lands_with_the_negative_origin_applied(self):
        """接**抓帧口径**时：副屏上的目标落点 = 虚拟桌面原点 + 帧内点。

        模型按 RELATIVE_0_1000 答 (500, 500) → 输入空间 (1920, 540)
        → 物理落点 = (-1920 + 1920, 0 + 540) = (0, 540) —— 恰好骑在
        两块屏的交界上，负原点少算 1 px 都会暴露。
        """
        grounder = TwoStageGrounder(
            _StubModel(_reply(500.0, 500.0)),
            scale=ModelScale.RELATIVE_0_1000,
            model_input_size=(_VIRTUAL_W, _VIRTUAL_H),
            full_screen_size=(_VIRTUAL_W, _VIRTUAL_H),  # ← 抓帧口径（对的接线）
            coarse_only=True,
            effort=Effort.LOW,
        )
        result = grounder.locate(
            VisualLocateRequest(
                target_description="开始",
                frame=_virtual_frame(),
                surface=(-1920, 0, 1920, 1080),
            )
        )
        self.assertTrue(result.ok, result.error_message)
        self.assertEqual(result.landing_point, (0, 540))
        self.assertEqual(result.coarse_point, (0.0, 540.0))

    def test_the_real_machine_wiring_declares_the_capture_size_not_the_primary_screen(self):
        """**源码级**：真机接线的 ``full_screen_size`` 必须来自虚拟桌面度量。

        与工单 AC 5/7/11 的源码级判据同风格：这一条错位的接线单屏测不出来，
        只能把契约钉在源码上。
        """
        live_src = (
            Path(__file__).resolve().parents[1] / "uia" / "live.py"
        ).read_text(encoding="utf-8")
        self.assertIn(
            "full_screen_size=(desktop.width, desktop.height)",
            live_src,
            "uia/live.py 的视觉通道必须声明抓帧口径（虚拟桌面），不是主屏口径",
        )
        self.assertIn(
            "desktop = current_virtual_desktop()",
            live_src,
            "uia/live.py 必须先量虚拟桌面（current_virtual_desktop）",
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

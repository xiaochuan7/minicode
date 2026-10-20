"""**视觉通道的接线**（标定 → 准入门 → 两级 grounding → 放大）。

M14 的 Windows 设置执行器与 M15 的微信执行器都要用它：
**标定与准入门只有一处** —— 坐标换算链路的真值来源不许有两个。

两种口径（与 M6/M9 的测量台同一个做法）：

* **生产口径**（默认）：准入门说了算。标定没过 → 视觉通道不可用 →
  报 ``visual_channel_unavailable``、**零动作下发**。
* **测量口径**（``bypass_admission=True``）：绕过准入门继续跑 ——
  用途**只有一个**：量「视觉通道在真机上到底能落多准」。
  **真实判定照样记录在追踪记录里**（生产路径仍然只认准入门）。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

__all__ = ["VisualChannel"]


class VisualChannel:
    """**视觉通道**的一次接线（标定只跑一次，之后复用）。"""

    def __init__(
        self,
        *,
        out_dir: str | Path,
        gate_mode: str = "on_demand",
        bypass_admission: bool = False,
        model_input: tuple[int, int] = (2048, 1280),
    ):
        self.out_dir = Path(out_dir)
        self.gate_mode = gate_mode
        self.bypass_admission = bypass_admission
        self.model_input = model_input
        self._calib: dict[str, Any] = {}
        self._grounder = None
        self._context: dict[str, Any] = {}

    # -- 读数 -------------------------------------------------------------

    @property
    def context(self) -> dict[str, Any]:
        """标定的读数（进任务上下文，报告里读得到）。"""
        return dict(self._context)

    # -- 端口 -------------------------------------------------------------

    def admission_port(self):
        """视觉通道的**准入判定端口**（生产口径 = 准入门说了算）。"""
        from minicode.calib.admission import CalibrationAdmission

        calibration = self._calibrate()
        real = CalibrationAdmission(calibration["outcome"])
        if not self.bypass_admission:
            return real
        return _BypassAdmission(real)

    def locator(self):
        """两级 grounding（视觉通道）。"""
        if self._grounder is not None:
            return self._grounder
        from minicode.ground.channel import TwoStageGrounder

        calibration = self._calibrate()
        self._grounder = TwoStageGrounder(
            calibration["client"],
            scale=calibration["scale"],
            model_input_size=self.model_input,
            full_screen_size=(calibration["width"], calibration["height"]),
            gate_mode=self.gate_mode,
        )
        return self._grounder

    def zoom_locator(self):
        """放大动作的端口（回源重抓 + 模型报整屏坐标）。"""
        from minicode.ground.zoom import zoom_locate

        calibration = self._calibrate()
        client = calibration["client"]
        scale = calibration["scale"]
        model_input = self.model_input

        class _ZoomLocator:
            def locate(self, request):
                return zoom_locate(
                    client,
                    request,
                    scale=scale,
                    model_input_size=model_input,
                )

        return _ZoomLocator()

    # -- 内部 -------------------------------------------------------------

    def _calibrate(self) -> dict[str, Any]:
        """标定（真调模型）+ 准入判定。**只跑一次**。"""
        if self._calib:
            return self._calib
        from minicode.calib.calibrate import CalibrationProbe
        from minicode.calib.probe import default_probe_spec
        from minicode.exec import dpi
        from minicode.model.client import ModelClient
        from minicode.model.config import load_config
        from minicode.model.request_log import JsonlRequestLog

        config = load_config()
        client = ModelClient(config, JsonlRequestLog(self.out_dir / "model-requests.jsonl"))
        width, height = dpi.physical_screen_size()
        probe = CalibrationProbe(
            client,
            spec=default_probe_spec(width, height),
            model_input_size=self.model_input,
            call_path="image",
            model_id=config.model,
        )
        outcome = probe.run()
        best = outcome.candidate(outcome.detected_scale)
        self._calib = {
            "outcome": outcome,
            "best": best,
            "client": client,
            "scale": outcome.detected_scale,
            "width": width,
            "height": height,
        }
        self._context["calibration"] = {
            "scale": outcome.detected_scale.value,
            "scale_verdict": outcome.scale_verdict,
            "max_error_px": round(best.max_error_px, 2),
            "ok": outcome.calibration_ok,
            "model_input": list(self.model_input),
        }
        return self._calib


class _BypassAdmission:
    """**测量口径**：绕过准入门（真实判定照样记在上下文里）。"""

    def __init__(self, real):
        self.real = real

    def admit(self):
        from minicode.action.ports import VisualAdmission

        verdict = self.real.admit()
        return VisualAdmission(
            available=True,
            reason=(
                f"⚠️ 测量口径：绕过准入门（真实判定："
                f"{'可用' if verdict.available else '不可用'} —— {verdict.reason}）"
            ),
            scale=verdict.scale,
            scale_verdict=verdict.scale_verdict,
            landing_equivalence=verdict.landing_equivalence,
            equivalent_scales=verdict.equivalent_scales,
        )

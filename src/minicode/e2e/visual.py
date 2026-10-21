"""**视觉通道的接线**（标定 → 准入门 → 两级 grounding → 放大）。

M14 的 Windows 设置执行器与 M15 的微信执行器都要用它：
**标定与准入门只有一处** —— 坐标换算链路的真值来源不许有两个。

两种口径（与 M6/M9 的测量台同一个做法）：

* **生产口径**（默认）：准入门说了算。标定没过 → 视觉通道不可用 →
  报 ``visual_channel_unavailable``、**零动作下发**。
* **测量口径**（``bypass_admission=True``）：绕过准入门继续跑 ——
  用途**只有一个**：量「视觉通道在真机上到底能落多准」。
  **真实判定照样记录在追踪记录里**（生产路径仍然只认准入门）。

**模型输入尺寸的口径**（工单 A-2）：

* **声明**：我们自己把整屏图缩到**物理表面的一半**再送模型，
  并在提示词里声明那张图的尺寸 —— 声明值**从量出来的表面尺寸算**，
  **源码里不写死 1280×800**（表面变了，声明跟着变）。
* **反查**：请求日志里的 token 数能反推**服务端实际看到的那张图**有多大
  （28 px patch 规则，**【二手】未在本机端点验证**）。
  **反查不出就如实记 ``unknown``，不许填一个猜的数**。
* 声明值**只有这一个来源**：标定探针与两级 grounding 拿的是**同一个值**
  （换算链路的分母不许有两个）。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from minicode.model.image import model_input_size_for

__all__ = [
    "VisualChannel",
    "PromptCapturingCaller",
    "RecordingSink",
    "measure_text_tokens",
    "reverse_lookup_model_input",
]


def _total_input_tokens(reply: Any) -> int | None:
    """一次请求的**总**输入 token 数（本次新算 + 缓存读 + 缓存写）。

    服务端把三样分开报；只看 ``input_tokens`` 会把「上下文变大了」看漏。
    三样都没报 → ``None``（不编一个 0）。
    """
    parts = (
        reply.input_tokens,
        reply.cache_read_input_tokens,
        reply.cache_creation_input_tokens,
    )
    if all(part is None for part in parts):
        return None
    return sum(part or 0 for part in parts)


def measure_text_tokens(
    client: Any, prompt: str, *, effort: Any, max_tokens: int
) -> int | None:
    """**纯文字**那部分占的 token 数（**量出来的**，不是服务端单列的）。

    做法：把**逐字相同**的提示词**不带图**再发一次 → 服务端报的总输入 token
    就是文字那部分（图的贡献是 0）。

    ⚠️ 提示词必须**逐字相同** —— 差一个字，token 数就不同，
    相减出来的图像贡献会偏。
    量不出来（服务端没报 token）→ ``None`` → 反查结果是 ``unknown``（不猜）。
    """
    reply = client.complete(prompt, effort=effort, max_tokens=max_tokens)
    return _total_input_tokens(reply)


def reverse_lookup_model_input(
    observed_input_tokens: int | None,
    text_tokens: int | None,
    aspect_ratio: float,
) -> tuple[int, int] | None:
    """**用 token 数反查**服务端实际看到的那张图（反查不出 → ``None``）。

    实现在 :func:`minicode.calib.calibrate.estimate_model_input_from_tokens`
    （calib 包，**唯一一处**）。

    参数（calib 那份的契约）：

    * ``observed_input_tokens`` —— 服务端报的这次请求的**总**输入 token
      （文字 + 图像）；
    * ``text_tokens`` —— 文字部分的 token 数（调用方量出来的；``None`` = 未知）；
    * ``aspect_ratio`` —— **发送尺寸**的宽高比（w/h）。

    返回 ``(W, H)`` 或 ``None``（**unknown**：不猜、不返回「最接近的」）。
    ⚠️ 返回的是 **28 px patch 网格的上界**（每边误差 ≤ 28 px）——
    它是「服务端确实缩到多大了」的**证据**，**不是坐标基准**
    （坐标基准仍用我们自己缩放后声明的那个尺寸）。
    """
    from minicode.calib.calibrate import estimate_model_input_from_tokens

    return estimate_model_input_from_tokens(
        observed_input_tokens, text_tokens, aspect_ratio
    )


class RecordingSink:
    """请求日志的出口：**落盘 + 记住最后一条**（反查服务端实际尺寸要用）。

    ⚠️ ``last`` **不是线程安全的** —— 只在**单线程**的标定阶段读它
    （评测的两臂并行阶段不读）。
    """

    def __init__(self, inner: Any):
        self._inner = inner
        self.last: Any = None

    def write(self, record: Any) -> None:
        self.last = record
        self._inner.write(record)


class PromptCapturingCaller:
    """**模型调用端口的包装**：记住最后一次用的提示词。

    反查服务端实际尺寸时，要用**逐字相同**的那条提示词再发一次**不带图**的请求
    （量出纯文字那部分的 token 数）—— 提示词从调用方手里拿不到，
    就从端口上截下来。
    """

    def __init__(self, inner: Any):
        self._inner = inner
        self.last_prompt = ""

    def complete(self, prompt: str, **kwargs: Any) -> Any:
        self.last_prompt = prompt
        return self._inner.complete(prompt, **kwargs)


class VisualChannel:
    """**视觉通道**的一次接线（标定只跑一次，之后复用）。"""

    def __init__(
        self,
        *,
        out_dir: str | Path,
        gate_mode: str = "on_demand",
        bypass_admission: bool = False,
        model_input: tuple[int, int] | None = None,
    ):
        """
        ``model_input``
            **声明的模型输入尺寸**；``None`` = **由标定记录决定**
            （= 从量出来的物理表面尺寸算，见
            :func:`minicode.model.image.model_input_size_for`；本机 2560×1600
            → 1280×800）。
            ⚠️ 源码里**不写死** ``(2048, 1280)`` 那样手填的值
            （工单 A-2：手填的尺寸错了就静默偏移）。
        """
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
            #: **与标定探针同一个声明值**（换算链路的分母只有一个来源）。
            model_input_size=calibration["model_input_size"],
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
        #: **与标定探针同一个声明值**（换算链路的分母只有一个来源）。
        model_input = calibration["model_input_size"]

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
        sink = RecordingSink(JsonlRequestLog(self.out_dir / "model-requests.jsonl"))
        client = PromptCapturingCaller(ModelClient(config, sink))
        width, height = dpi.physical_screen_size()
        #: **声明的模型输入尺寸**：从**量出来的表面尺寸**算
        #: （:func:`minicode.model.image.model_input_size_for` —— 声明尺寸只有这一个来源；
        #: 本机 2560×1600 → 1280×800，正好 ×2、16:10 不变形）。
        declared = self.model_input or model_input_size_for(width, height)
        probe = CalibrationProbe(
            client,
            spec=default_probe_spec(width, height),
            model_input_size=declared,
            call_path="image",
            model_id=config.model,
        )
        outcome = probe.run()
        best = outcome.candidate(outcome.detected_scale)
        #: **反查服务端实际看到的那张图**（反查不出 → ``unknown``，不猜）。
        server_input = self._reverse_lookup(client, sink, declared)
        self._calib = {
            "outcome": outcome,
            "best": best,
            "client": client,
            "scale": outcome.detected_scale,
            "width": width,
            "height": height,
            "model_input_size": declared,
            "server_model_input": server_input,
        }
        self._context["calibration"] = {
            "scale": outcome.detected_scale.value,
            "scale_verdict": outcome.scale_verdict,
            "max_error_px": round(best.max_error_px, 2),
            "ok": outcome.calibration_ok,
            "model_input": list(declared),
            #: **声明的模型输入尺寸**（我们自己缩到表面的一半再送模型）。
            "declared_model_input": list(declared),
            #: **反查出来的服务端实际尺寸**（反查不出 → ``"unknown"``）。
            "server_model_input": (
                list(server_input) if server_input is not None else "unknown"
            ),
        }
        return self._calib

    def _reverse_lookup(
        self, client: Any, sink: RecordingSink, declared: tuple[int, int]
    ) -> tuple[int, int] | None:
        """用**请求日志里的 token 数**反查服务端实际看到的那张图。

        * 图像那一部分的 token 数 = 服务端报的总输入 token − 纯文字那部分；
        * 纯文字那部分**量出来**：把**逐字相同**的提示词不带图再发一次。
        * **反查不出就返回 ``None``**（调用方记 ``unknown``，不许填一个猜的数）。
        """
        record = sink.last
        prompt = getattr(client, "last_prompt", "")
        if record is None or not prompt:
            return None
        observed = record.total_input_tokens
        if observed is None:
            return None
        text_tokens = measure_text_tokens(
            client, prompt, effort=record.effort, max_tokens=2000
        )
        if text_tokens is None:
            return None
        return reverse_lookup_model_input(
            observed, text_tokens, declared[0] / declared[1] if declared[1] else 0.0
        )


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

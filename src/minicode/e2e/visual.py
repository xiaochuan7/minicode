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
* **相等断言**（**闸门**）：观测图像 token vs 期望值
  ``ceil(W/28) × ceil(H/28)``（容差 ±2）——
  **不等 ⇒ 服务端再缩了那张图 ⇒ 该次请求的坐标系不可信**
  → 视觉通道**不可用**（零动作下发），**不许**继续按声明尺寸换算。
  ⚠️ **它不被「测量口径」绕过**（坐标系不可信不是测量旋钮）。
* **反查**（**诊断/证据**）：请求日志里的 token 数能反推**服务端大概**缩到了
  多大（28 px patch 规则，**【二手】未在本机端点验证**）。
  **反查不出就如实记 ``unknown``，不许填一个猜的数**。
  ⚠️ **它不作准入依据**（盒子给的是**区间**，区间落在声明尺寸附近
  **不代表**服务端没缩过）。
* 声明值**只有这一个来源**：标定探针与两级 grounding 拿的是**同一个值**
  （换算链路的分母不许有两个）。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from minicode.model.image import model_input_size_for
from minicode.model.request_log import total_input_tokens

__all__ = [
    "VisualChannel",
    "PromptCapturingCaller",
    "RecordingSink",
    "LazyUiaCandidateSource",
    "MergedCandidateSource",
    "measure_text_tokens",
    "merged_candidate_source",
    "ocr_candidate_source",
    "reverse_lookup_model_input",
    "reverse_lookup_from_log",
    "server_image_evidence_from_log",
    "untrusted_coordinate_system_reason",
]


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
    return total_input_tokens(
        reply.input_tokens,
        reply.cache_read_input_tokens,
        reply.cache_creation_input_tokens,
    )


def reverse_lookup_model_input(
    observed_input_tokens: int | None,
    text_tokens: int | None,
    aspect_ratio: float,
) -> tuple[int, int] | None:
    """**诊断**：用 token 数反查服务端**大概**缩到了多大（反查不出 → ``None``）。

    实现在 :func:`minicode.calib.calibrate.diagnose_server_model_input_from_tokens`
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

    ⚠️ **它只是「证据」，不作准入依据**：「能不能信这次请求的坐标系」
    由**相等断言**判（:func:`minicode.calib.calibrate.assert_server_kept_image_tokens`）
    —— 盒子给的是**区间**，区间落在声明尺寸附近**不代表**服务端没缩过。
    """
    from minicode.calib.calibrate import diagnose_server_model_input_from_tokens

    return diagnose_server_model_input_from_tokens(
        observed_input_tokens, text_tokens, aspect_ratio
    )


def server_image_evidence_from_log(
    client: Any,
    sink: Any,
    declared: tuple[int, int],
    *,
    max_tokens: int = 2000,
) -> tuple[tuple[int, int] | None, Any]:
    """一次测量 → **两个结论**：**相等断言**（闸门）与**盒子反查**（诊断）。

    * 图像那一部分的 token 数 = 服务端报的总输入 token − 纯文字那部分；
    * 纯文字那部分**量出来**：把**逐字相同**的提示词不带图再发一次；
    * **量不出来 → 两个结论都是 unknown**（**不猜**、不填一个猜的数）。

    返回 ``(盒子, 相等断言)``：

    * **盒子** —— 服务端**大概**缩到了多大（``None`` = 反查不出）；
    * **相等断言** —— 服务端**有没有**再缩那张图
      （``server_kept_size``：``True`` = 没再缩（坐标系**可信**）/
      ``False`` = 缩过（**不可信**）/ ``None`` = 量不出来（**不猜**））。

    ⚠️ **总**输入 token 的口径走
    :func:`minicode.model.request_log.total_input_tokens`（本仓的唯一一处）——
    服务端开了提示词缓存时，只看 ``input_tokens`` 会小到几十（图像 token 进了
    ``cache_read``）→ 相减出来是负数。

    ⚠️ **先取这条请求日志**，再量纯文字 —— 量纯文字会再发一次请求，
    把 ``sink.last`` 覆盖掉。
    """
    from minicode.calib.calibrate import (
        assert_server_kept_image_tokens,
        observed_image_tokens,
    )

    unknown = assert_server_kept_image_tokens(None, declared)
    record = getattr(sink, "last", None)
    prompt = getattr(client, "last_prompt", "")
    if record is None or not prompt:
        return (None, unknown)
    observed_total = getattr(record, "total_input_tokens", None)
    if observed_total is None:
        return (None, unknown)
    text_tokens = measure_text_tokens(
        client, prompt, effort=record.effort, max_tokens=max_tokens
    )
    if text_tokens is None:
        return (None, unknown)
    observed = observed_image_tokens(observed_total, text_tokens)
    assertion = assert_server_kept_image_tokens(observed, declared)
    box = reverse_lookup_model_input(
        observed_total,
        text_tokens,
        declared[0] / declared[1] if declared[1] else 0.0,
    )
    return (box, assertion)


def reverse_lookup_from_log(
    client: Any,
    sink: Any,
    declared: tuple[int, int],
    *,
    max_tokens: int = 2000,
) -> tuple[int, int] | None:
    """**诊断**（盒子反查）—— **不作准入依据**。

    ⚠️ 「能不能信这次请求的坐标系」由**相等断言**判 →
    :func:`server_image_evidence_from_log`（一次测量 → 两个结论）。
    """
    box, _assertion = server_image_evidence_from_log(
        client, sink, declared, max_tokens=max_tokens
    )
    return box


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


def ocr_candidate_source(language: str = "zh-Hans-CN") -> Any:
    """用 **Windows 自带的 OCR 词级框**造一个候选源（B-1 的视觉通道那一半）。

    * 候选框在**物理像素**（帧像素 + 帧原点），落点直接取框中心 → **零换算**；
    * **只读**：OCR 不注入任何输入；
    * ⚠️ **召回是瓶颈**：图标类目标没有文字可匹配（实测评测集上图标召回 0/13），
      方案原文的「**UIA 控件为主表**」那一半靠 :func:`merged_candidate_source` 补上
      （真机实测 `runs/b1-live-zh/live-report.json`：UIA 主表命中 **22/22**、
      落点误差中位 0.0 px；同一批的 OCR 附加表 **15/22** —— 崩掉的都是纯图标控件）。
    """
    from minicode.ground.candidates import OcrCandidateSource
    from minicode.verification.ocr_windows import WindowsOcrPort

    port = WindowsOcrPort(language)
    return OcrCandidateSource(port.read_words)


class LazyUiaCandidateSource:
    """**UIA 控件**当候选源（UFO² 的「UIA 主表」那一半）——**每次调用现读树**。

    * ``controls_provider`` —— 一个**无参可调用**，返回一批 UIA 控件
      （名字 + **物理像素**矩形；形状见 :class:`minicode.ground.candidates.UiaControlLike`）；
    * ⚠️ **UIA 的框不要加任何偏移**：`BoundingRectangle` 已经是物理屏幕坐标；
    * ⚠️ **无名控件不丢**（给位置型占位描述）—— 静默丢弃就是「图标召回 0」的成因；
    * ⚠️ **读树失败 → 异常往上抛**（不吞）：上层据此记「候选源报错」。
      「树是空的」（UWP 页面）与「树读不出来」是**两件事** ——
      **判不了 ≠ 没有候选**（方案原文「接线点」一节的要求）。
    """

    def __init__(self, controls_provider: Any, *, max_candidates: int = 20):
        self._provider = controls_provider
        self.max_candidates = int(max_candidates)

    def candidates(self, frame: Any, description: str) -> list[Any]:
        from minicode.ground.candidates import UiaCandidateSource

        controls = list(self._provider())
        return UiaCandidateSource(
            controls, max_candidates=self.max_candidates
        ).candidates(frame, description)


class MergedCandidateSource:
    """**UIA 主表 + OCR 附加表**的合并源（UFO² 的 IoU 0.1 规则）。

    * **主表（UIA）全留**；附加表（OCR）中与**任一**主表项 IoU **> 0.1** 的**丢弃**；
    * **比较一律在物理像素空间**（红线①）；
    * ⚠️ **方向不能反**：UIA 框与 OCR 框重叠时**保留 UIA 框**。

    ⚠️ **「判不了 ≠ 没有候选」**（方案原文「接线点」一节）：
    * **一源报错、另一源有货** → 用有货的那一源（**主表不可用 → 退回附加表**）；
    * **两源都报错** → **异常往上抛** → 上层记「候选源报错」，
      **不许**被当成「没有候选」（那会让「读不出来」看起来像「界面上没有」）。
    """

    def __init__(self, main: Any, additional: Any):
        self._main = main
        self._additional = additional

    def candidates(self, frame: Any, description: str) -> list[Any]:
        from minicode.ground.candidates import merge_candidate_sources

        errors: list[Exception] = []
        try:
            main_rows = list(self._main.candidates(frame, description))
        except Exception as exc:
            main_rows = []
            errors.append(exc)
        try:
            additional_rows = list(self._additional.candidates(frame, description))
        except Exception as exc:
            additional_rows = []
            errors.append(exc)
        if not main_rows and not additional_rows and errors:
            # 两源都读不出来 → **源报错**（抛出去）：上层记「候选源报错」，
            # 不许被当成「没有候选」。
            raise errors[0]
        return merge_candidate_sources(main_rows, additional_rows)


def merged_candidate_source(
    controls_provider: Any, *, language: str = "zh-Hans-CN"
) -> Any:
    """生产接线用的**合并候选源**（UIA 主表 + OCR 附加表）。"""
    return MergedCandidateSource(
        LazyUiaCandidateSource(controls_provider),
        ocr_candidate_source(language),
    )


class VisualChannel:
    """**视觉通道**的一次接线（标定只跑一次，之后复用）。"""

    def __init__(
        self,
        *,
        out_dir: str | Path,
        gate_mode: str = "on_demand",
        bypass_admission: bool = False,
        model_input: tuple[int, int] | None = None,
        candidate_source: Any | None = None,
        full_screen_size: tuple[int, int] | None = None,
    ):
        """
        ``model_input``
            **声明的模型输入尺寸**；``None`` = **由标定记录决定**
            （= 从量出来的物理表面尺寸算，见
            :func:`minicode.model.image.model_input_size_for`；本机 2560×1600
            → 1280×800）。
            ⚠️ 源码里**不写死** ``(2048, 1280)`` 那样手填的值
            （工单 A-2：手填的尺寸错了就静默偏移）。
        ``full_screen_size``
            **整屏帧的口径**（审查 Bug#1）：视觉通道收到的整屏帧**实际是多大**，
            换算链路就按多大声明。⚠️ 它与标定探针的尺寸是**两个来源**——
            抓帧抓的是**整个虚拟桌面**（``exec.capture.grab_frame`` 缺省），
            探针画在**主屏**上；单屏机器两者恰好重合，多显示器下用主屏口径
            会让 ``locate()`` 在入口被尺寸门拒掉。``None``（缺省）= 运行时按
            虚拟桌面量（量不到时回落标定记录，保持旧行为）。
        ``candidate_source``
            **候选源**（B-1 的「模型选编号」分支，端口形状见
            :class:`minicode.ground.candidates.CandidateSource`）。
            ``None``（缺省）= **这条分支不存在** → 视觉通道的行为与从前**完全一致**。
            要接它就传 :func:`ocr_candidate_source` 造的那个。
        """
        self.out_dir = Path(out_dir)
        self.gate_mode = gate_mode
        self.bypass_admission = bypass_admission
        self.model_input = model_input
        self.candidate_source = candidate_source
        self._full_screen_size = full_screen_size
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
        """视觉通道的**准入判定端口**（生产口径 = 准入门说了算）。

        ⚠️ **坐标系可信性**（相等断言）是**闸门**，且**不被「测量口径」绕过** ——
        坐标系不可信不是测量旋钮：在一个**没观察过的空间**里换算出来的
        落点**一定是错的**。
        """
        calibration = self._calibrate()
        real = calibration["admission"]
        if self.bypass_admission:
            real = _BypassAdmission(real)
        return _CoordinateSystemGate(real, calibration["image_token_assertion"])

    def locator(self):
        """两级 grounding（视觉通道）。

        ⚠️ **坐标系不可信 → 显式失败**：服务端再缩了那张图时，
        **不许**继续按声明尺寸换算（那是在一个没观察过的空间里换算）。
        """
        if self._grounder is not None:
            return self._grounder
        from minicode.ground.channel import TwoStageGrounder

        calibration = self._calibrate()
        assertion = calibration["image_token_assertion"]
        if assertion.server_kept_size is False:
            return _UntrustedCoordinateSystemLocator(assertion)
        self._grounder = TwoStageGrounder(
            calibration["client"],
            scale=calibration["scale"],
            #: **与标定探针同一个声明值**（换算链路的分母只有一个来源）。
            model_input_size=calibration["model_input_size"],
            #: ⚠️ **整屏口径 = 抓帧口径**（审查 Bug#1）：整屏帧抓的是**整个虚拟
            #: 桌面**，full_screen_size 必须同口径 —— 标定探针（主屏）的宽高
            #: 在多显示器下与它必然错位，单屏恰好重合所以测试测不出来。
            full_screen_size=self._frame_size(calibration),
            gate_mode=self.gate_mode,
            #: **候选源**（B-1 的「模型选编号」分支）。给了它：匹配得上候选就
            #: 走编号选择（落点 = 候选框物理中心）；匹配不上或挑不出编号 →
            #: 回落点式路径（记 ``fallback_cause``）。
            #: ⚠️ **缺省没有** —— 候选源的**召回**是瓶颈（图标类目标 OCR 读不出
            #: 文字；方案原文的「UIA 控件为主表」那一半还没接，见 P1）。
            #: 默认关 = 生产路径的行为与从前**完全一致**。
            candidate_source=self.candidate_source,
        )
        return self._grounder

    def zoom_locator(self):
        """放大动作的端口（回源重抓 + 模型报整屏坐标）。

        ⚠️ **坐标系不可信 → 显式失败**（同上：放大那一级也按声明尺寸换算）。
        """
        from minicode.ground.zoom import zoom_locate

        calibration = self._calibrate()
        assertion = calibration["image_token_assertion"]
        if assertion.server_kept_size is False:
            return _UntrustedZoomLocator(assertion)
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

    def _frame_size(self, calibration: dict[str, Any]) -> tuple[int, int]:
        """**整屏帧的口径**：显式给的 > 运行时量虚拟桌面 > 标定记录（旧行为）。

        ⚠️ 抓帧（``exec.capture.grab_frame`` 缺省）抓的是**整个虚拟桌面**，
        所以默认按虚拟桌面量；非 Windows 上量不到（Win32 不在）才回落
        标定记录 —— 那是「行为与从前一致」的保底，不是正确口径。
        """
        if self._full_screen_size is not None:
            return (int(self._full_screen_size[0]), int(self._full_screen_size[1]))
        try:
            from minicode.action.windows import current_virtual_desktop

            desktop = current_virtual_desktop()
            return (int(desktop.width), int(desktop.height))
        except Exception:  # noqa: BLE001 —— 非 Windows / Win32 不可用：回落旧口径
            return (int(calibration["width"]), int(calibration["height"]))

    def _calibrate(self) -> dict[str, Any]:
        """标定（真调模型）+ 准入判定。**只跑一次**。"""
        if self._calib:
            return self._calib
        from minicode.calib.admission import CalibrationAdmission
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
        #: **准入判定**（A-1 的真闸门：**等价族分歧 ≤ 4 px 且 K 轮命中率 ≥ 0.6**）。
        #: 算**一次**（准入规则只有那一处），端口与读数共用它。
        admission = CalibrationAdmission(outcome).admit()
        #: **服务端实际看到的那张图**（盒子 = **诊断**）+ **相等断言**（**闸门**）。
        #: 一次测量 → 两个结论（**不额外发模型调用**）。
        server_input, assertion = self._server_evidence(client, sink, declared)
        self._calib = {
            "outcome": outcome,
            "admission": CalibrationAdmission(outcome),
            "best": best,
            "client": client,
            "scale": outcome.detected_scale,
            "width": width,
            "height": height,
            "model_input_size": declared,
            "server_model_input": server_input,
            "image_token_assertion": assertion,
        }
        self._context["calibration"] = calibration_readout(
            outcome, admission, best, declared, server_input, assertion
        )
        return self._calib

    def _server_evidence(
        self, client: Any, sink: RecordingSink, declared: tuple[int, int]
    ) -> tuple[tuple[int, int] | None, Any]:
        """**服务端实际看到的那张图**（盒子，诊断）+ **相等断言**（闸门）。

        实现只有一处：:func:`server_image_evidence_from_log`。
        """
        return server_image_evidence_from_log(client, sink, declared)


def calibration_readout(
    outcome: Any,
    admission: Any,
    best: Any,
    declared: tuple[int, int],
    server_input: tuple[int, int] | None,
    assertion: Any = None,
) -> dict[str, Any]:
    """**标定的读数**（进任务上下文 / 报告）。

    ⚠️ **``ok`` 不是闸门**：它的语义是「落点等价族判定成功」，而那个族
    **按 4 px 定义** → **按定义恒为真**（结构事实，见
    :mod:`minicode.calib.calibrate`）。真正的闸门是
    ``visual_channel_available``（**等价族分歧 ≤ 4 px 且 K 轮命中率 ≥ 0.6**）。

    ⚠️ **两者都在这里** —— 只报 ``ok`` 会让人以为「标定通过」，
    而通道**其实**可能被命中率那道闸门挡下来。

    ⚠️ **第三道闸门**在这里也报出来：**相等断言**
    （``image_token_assertion`` / ``coordinate_system_trusted``）——
    服务端再缩了那张图 → **该次请求的坐标系不可信** →
    视觉通道不可用（**零动作下发**）。
    """
    from minicode.calib.tolerance import CALIBRATION_MIN_HIT_RATE

    return {
        "scale": outcome.detected_scale.value,
        "scale_verdict": outcome.scale_verdict,
        "max_error_px": round(best.max_error_px, 2),
        #: **真闸门的那两个读数**（K 轮命中率 + 准入判定）。
        "rounds": outcome.rounds,
        "hit_rate": round(outcome.hit_rate, 4),
        "min_hit_rate": CALIBRATION_MIN_HIT_RATE,
        "visual_channel_available": admission.available,
        "visual_channel_reason": admission.reason,
        "model_input": list(declared),
        #: **声明的模型输入尺寸**（我们自己缩到表面的一半再送模型）。
        "declared_model_input": list(declared),
        #: **相等断言**（闸门）：观测图像 token vs 期望值
        #: ``ceil(W/28) × ceil(H/28)``（容差 ±2）。
        "image_token_assertion": (
            assertion.to_dict() if assertion is not None else None
        ),
        #: **这次请求的坐标系可信吗**（``False`` = 服务端再缩了那张图 →
        #: **视觉通道不可用**；``None`` = 量不出来，**不猜**）。
        "coordinate_system_trusted": (
            assertion.server_kept_size if assertion is not None else None
        ),
        #: **反查出来的服务端实际尺寸**（反查不出 → ``"unknown"``）。
        "server_model_input": (
            list(server_input) if server_input is not None else "unknown"
        ),
        #: ⚠️ **``ok`` 不是闸门**（恒真）；真正的绿灯是
        #: ``visual_channel_available``。
        "ok": outcome.calibration_ok,
        "ok_is_not_the_gate": (
            "calibration_ok = 「落点等价族判定成功」，那个族按 4 px 定义 → "
            "按定义恒为真；闸门是 visual_channel_available"
        ),
    }


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


def untrusted_coordinate_system_reason(assertion: Any) -> str:
    """「**坐标系不可信**」的人读理由（相等断言不通过时用）。"""
    return (
        f"服务端再缩了那张图（观测图像 token {assertion.observed_image_tokens}"
        f" ≠ 期望 {assertion.expected_image_tokens}，差 "
        f"{assertion.difference_tokens} > 容差 {assertion.tolerance_tokens}）"
        " → 该次请求的坐标系不可信 → 视觉通道不可用"
    )


class _CoordinateSystemGate:
    """**坐标系可信性**的闸门（**相等断言**）。

    ⚠️ **它不被「测量口径」绕过** —— 坐标系不可信**不是**测量旋钮：
    服务端再缩了那张图，模型就在一个**我们没观察过的空间**里出坐标 →
    按声明尺寸换算出来的落点**一定是错的**。
    """

    def __init__(self, inner: Any, assertion: Any):
        self.inner = inner
        self.assertion = assertion

    def admit(self):
        from minicode.action.ports import VisualAdmission

        verdict = self.inner.admit()
        if self.assertion.server_kept_size is False:
            return VisualAdmission(
                available=False,
                reason=untrusted_coordinate_system_reason(self.assertion),
                scale=verdict.scale,
                scale_verdict=verdict.scale_verdict,
                landing_equivalence=verdict.landing_equivalence,
                equivalent_scales=verdict.equivalent_scales,
            )
        return verdict


class _UntrustedCoordinateSystemLocator:
    """**坐标系不可信**时的视觉定位口 —— **显式失败**，不换算。

    ⚠️ 它**不抛异常**：抛异常会打断整轮真机任务（连 UIA 能做的步也做不了）。
    这里返回一条**带错误码的失败结果** → 上层记「视觉通道不可用」。
    """

    def __init__(self, assertion: Any):
        self.assertion = assertion

    def locate(self, request: Any) -> Any:
        from minicode.action.errors import ErrorCode
        from minicode.action.ports import VisualLocateResult

        return VisualLocateResult(
            error_code=ErrorCode.VISUAL_CHANNEL_UNAVAILABLE,
            error_message=untrusted_coordinate_system_reason(self.assertion),
        )


class _UntrustedZoomLocator:
    """**坐标系不可信**时的放大口 —— **显式失败**，不换算。"""

    def __init__(self, assertion: Any):
        self.assertion = assertion

    def locate(self, request: Any) -> Any:
        from minicode.action.errors import ErrorCode
        from minicode.action.ports import ZoomResult

        return ZoomResult(
            error_code=ErrorCode.VISUAL_CHANNEL_UNAVAILABLE,
            error_message=untrusted_coordinate_system_reason(self.assertion),
        )

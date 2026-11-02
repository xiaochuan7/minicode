"""**检测模型候选源**（V3 第 2 步）：ScreenParser（YOLO UI 元素检测）。

**许可与身份（V3 要求核实并在报告写明）**：`docling-project/ScreenParser`
（IBM docling 系，Hugging Face），**Apache-2.0**；YOLO 架构，在 ScreenParse
（77.1 万张网页截图、2100 万 UI 元素标注）上微调。
**禁止 OmniParser 的 icon_detect（AGPL）** —— 本模块不引入。

**为什么需要它**（V3 量化的结论）：OCR 词框对 icon 目标覆盖 0/30（根因坐实）、
零模型启发式 1/30（召回不足）—— 「补候选」要走**真检测器**。

**接线纪律**：实现 :class:`minicode.ground.candidates.CandidateSource` 协议
（鸭子类型，调用方注入）；候选框 = **物理像素**（帧像素 + 帧原点）；
描述 = 检测器的语义标签 + **可选**的 OCR 邻近词（注入 word_reader 时）；
去重复用 :func:`minicode.ground.candidates.dedupe_by_iou`（物理像素空间）。

**依赖都是懒加载**：ultralytics/torch 只在真正构造默认 predictor 时才 import
（单测注入假 predictor，零 torch 依赖）；numpy/PIL 沿用 vision 附加项。
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from minicode.action.ports import CapturedFrame
from minicode.ground.candidates import (
    GroundingCandidate,
    OcrWordLike,
    dedupe_by_iou,
)

__all__ = [
    "DETECTOR_SOURCE",
    "DEFAULT_CONFIDENCE",
    "DEFAULT_MODEL_ID",
    "Detection",
    "ScreenParserCandidateSource",
]

#: 候选源的 ``source`` 标签。
DETECTOR_SOURCE = "screenparser"

#: Hugging Face 上的模型 id（Apache-2.0；离线环境可用 hf-mirror 环境变量）。
DEFAULT_MODEL_ID = "docling-project/ScreenParser"

#: 检测置信度阈值（缺省 0.25 —— UI 检测的常规档；宁多勿漏，重排层再筛）。
DEFAULT_CONFIDENCE = 0.25


@dataclass(frozen=True)
class Detection:
    """一次检测结果（**帧像素**坐标）。"""

    box: tuple[int, int, int, int]
    label: str
    confidence: float


#: predictor 端口：PIL 图像 → 检测列表（真实现包 ultralytics；单测注入假货）。
Predictor = Callable[[object], Sequence[Detection]]


def _frame_to_image(frame: CapturedFrame):
    """帧的 BGR(A) 字节流 → PIL Image（RGB）。"""
    import numpy
    from PIL import Image

    channels = 3 if frame.pixel_format.value == "bgr" else 4
    pixels = numpy.frombuffer(frame.payload, dtype=numpy.uint8)
    pixels = pixels[: frame.width * frame.height * channels].reshape(
        frame.height, frame.width, channels
    )
    rgb = pixels[:, :, :3][:, :, ::-1]
    return Image.fromarray(rgb.copy())


def _ultralytics_predictor(model_id: str, confidence: float, device: str) -> Predictor:
    """默认 predictor：ultralytics YOLO 加载 HF 上的 ScreenParser 权重。

    ⚠️ torch/ultralytics 是**重量级可选项**（不在任何 extras 里）——
    环境里没装就抛出带指引的 RuntimeError，**不悄悄降级**（调用方决定退回
    零模型源，并在报告里写明「检测器不可用」）。
    ⚠️ 权重用 :func:`huggingface_hub.hf_hub_download` 拉（``HF_ENDPOINT``
    指到镜像即可）—— ultralytics 的 ``YOLO(<repo id>)`` **不会**自动从
    HF hub 下载（实测 FileNotFoundError）。
    """
    try:
        from ultralytics import YOLO
    except ImportError as exc:
        raise RuntimeError(
            "ScreenParser 需要 ultralytics+torch（pip install torch --index-url "
            "https://download.pytorch.org/whl/cpu && pip install ultralytics）；"
            "当前环境没有 —— 调用方应退回零模型候选源并如实记录检测器不可用"
        ) from exc
    import os

    if os.path.exists(model_id):
        weights_path = model_id
    else:
        from huggingface_hub import hf_hub_download

        weights_path = hf_hub_download(model_id, "best.pt")
    model = YOLO(weights_path)

    def predict(image) -> list[Detection]:
        results = model.predict(image, conf=confidence, device=device, verbose=False)
        detections: list[Detection] = []
        for result in results:
            names = result.names or {}
            for box in result.boxes:
                x1, y1, x2, y2 = (float(v) for v in box.xyxy[0].tolist())
                label = str(names.get(int(box.cls[0]), int(box.cls[0])))
                detections.append(
                    Detection(
                        box=(int(x1), int(y1), int(x2), int(y2)),
                        label=label,
                        confidence=float(box.conf[0]),
                    )
                )
        return detections

    return predict


class ScreenParserCandidateSource:
    """**ScreenParser 检测模型**当候选源（V3 的第三候选源）。

    参数
    ----
    model_id
        HF 模型 id（或本地权重路径）。
    confidence
        检测置信度阈值。
    max_candidates
        清单长度上限（与其它源同档，缺省 20）。
    word_reader
        **可选** OCR 词读数端口：把检测框**最近的词**挂进描述
        （检测标签是英文类名，界面词更可指认）。
    predictor
        **注入**的预测函数（单测用；缺省 = ultralytics 真实现）。
    """

    def __init__(
        self,
        *,
        model_id: str = DEFAULT_MODEL_ID,
        confidence: float = DEFAULT_CONFIDENCE,
        max_candidates: int = 20,
        device: str = "cpu",
        word_reader: Callable[[CapturedFrame], Sequence[OcrWordLike]] | None = None,
        predictor: Predictor | None = None,
    ):
        self.max_candidates = int(max_candidates)
        self.confidence = float(confidence)
        self.word_reader = word_reader
        self._predict = predictor or _ultralytics_predictor(
            model_id, confidence, device
        )

    def candidates(
        self, frame: CapturedFrame, description: str
    ) -> list[GroundingCandidate]:
        """这一帧上的检测候选（**物理像素**，编号 1 起）。

        ⚠️ ``description`` 不参与过滤 —— 检测源是**召回**用途：
        检出的元素全进清单，编号分支的模型/d3 去选。
        ⚠️ 检测器不可用（没装 torch / 权重拿不到）→ 异常**往上抛**：
        上层判「判不了 ≠ 没有候选」，调用方决定退回零模型源。
        """
        if frame.is_empty:
            return []
        image = _frame_to_image(frame)
        detections = list(self._predict(image))
        words = self._words(frame)
        rows: list[GroundingCandidate] = []
        for detection in detections:
            x1, y1, x2, y2 = detection.box
            if x2 <= x1 or y2 <= y1:
                continue
            text = self._describe(detection, words, frame)
            rows.append(
                GroundingCandidate(
                    number=0,
                    text=text,
                    box=(
                        x1 + frame.left,
                        y1 + frame.top,
                        x2 + frame.left,
                        y2 + frame.top,
                    ),
                    source=DETECTOR_SOURCE,
                )
            )
        # 检测置信度高在前（同框去重时保留更可信的那个），再按既有 IoU 去重。
        rows.sort(key=lambda candidate: -len(candidate.text))
        kept = dedupe_by_iou(rows, threshold=0.5)[: self.max_candidates]
        return [
            GroundingCandidate(
                number=index + 1,
                text=candidate.text,
                box=candidate.box,
                source=candidate.source,
            )
            for index, candidate in enumerate(kept)
        ]

    def _words(self, frame: CapturedFrame) -> list[OcrWordLike]:
        if self.word_reader is None:
            return []
        try:
            return list(self.word_reader(frame))
        except Exception:  # noqa: BLE001 —— OCR 不可用只影响描述质量
            return []

    def _describe(
        self,
        detection: Detection,
        words: Sequence[OcrWordLike],
        frame: CapturedFrame,
    ) -> str:
        """描述 = 检测标签 + **可选**邻近 OCR 词（检测框在帧像素，词也在帧像素）。"""
        nearest = _nearest_word(detection.box, words) if words else ""
        if nearest:
            return f"{nearest}（{detection.label}）"
        return f"界面{detection.label}（检测）"


def _nearest_word(box: tuple[int, int, int, int], words: Sequence[OcrWordLike]) -> str:
    """检测框附近的词（纵向邻接优先，横向距离最近 —— 与图标源同一规则）。"""
    left, top, right, bottom = box
    height = max(1, bottom - top)
    best: tuple[float, str] | None = None
    for word in words:
        word_left = int(word.x)
        word_top = int(word.y)
        word_right = word_left + int(word.w)
        word_bottom = word_top + int(word.h)
        if word_bottom < top - 2 * height or word_top > bottom + 2 * height:
            continue
        gap = max(word_left - right, left - word_right, 0)
        if best is None or gap < best[0]:
            best = (gap, str(word.text))
    if best is None:
        return ""
    return best[1].strip()

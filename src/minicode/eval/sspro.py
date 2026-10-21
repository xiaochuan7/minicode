"""**ScreenSpot-Pro 门控评测**（工单 M8）。

这张表要证明一件事：**门控开（按需触发）不低于门控关（无条件精定位）**。
判据只判我们自己的两行 —— 官方 5 个 coarse-to-fine 方法那一行行是
**官方自报**（未在同模型下重跑，**不作硬对比**）。

## 口径（不许混用）

* **内部一律原图像素**：GT bbox 与预测落点都在这张图的像素空间里比；
* **出口按目标脚本转一次**：ScreenSpot-Pro 官方脚本要的是**归一化 [0,1] 的点**
  与**归一化后的 bbox**（`eval_screenspot_pro.py::eval_sample_positive_gt`），
  转换在 :data:`minicode.eval.formats.SCREENSPOT_PRO` 这一个地方做
  （工单 M7 的口径纪律）；
* **模型侧量纲**（0~1 / 0~999 / 0~1000 / 绝对像素）**只来自运行时标定**，
  本模块**不碰**它（本仓自有代码里不出现把 999/1000 当量纲分母的写法）。

## 两臂的差别只有一处

* **门控开**（``on_demand``）：先粗定位 → 门控判定 → 命中才做 crop 精定位；
* **门控关**（``always``）：粗定位 → 无条件做 crop 精定位。

**同一批样本、同一模型、同一裁剪参数、同一判定口径**。

## 门控输入的诚实说明

* **目标占比**：**判不了** —— 粗定位只给点不给框（工单 M6 的口径），
  而用数据集的 GT 框去喂门控是**拿答案当判据**（不作数），所以不做；
* **置信度**：**判不了** —— 模型不报置信度；
* **粗定位是否有效**：**判得了**（模型自己的输出）；
* **两次粗定位是否一致**：**判得了** —— 另问一次（**不是逐字重发**），
  两次结果差得远说明这个目标小或含糊，值不值得再看一眼。

因此本次启用的门控条件是 **COARSE_INVALID** 与 **COARSE_INCONSISTENT** 两条，
另外两条记为**不可判定**（显式落盘，不静默）。
"""

from __future__ import annotations

import hashlib
import json
import random
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from minicode.action.ports import CapturedFrame
from minicode.eval.formats import SCREENSPOT_PRO, EvalSample
from minicode.ground.gate import (
    GateMode,
    GateReport,
    GateSample,
    build_gate_report,
    default_gate_params,
)
from minicode.model.effort import Effort
from minicode.model.ports import ModelCaller

from .png_read import read_png_frame

__all__ = [
    "ArmOutcome",
    "SsproSample",
    "compare_arms",
    "load_samples",
    "run_arms",
    "stratified_subset",
    "summarize",
    "table_rows",
]


@dataclass(frozen=True)
class SsproSample:
    """一条 ScreenSpot-Pro 样本（GT 全部是**原图像素**）。"""

    sample_id: str
    image_path: Path
    #: GT bbox，``[x1, y1, x2, y2]``（原图像素）。
    bbox: tuple[int, int, int, int]
    image_size: tuple[int, int]
    instruction: str
    #: ``"text"`` / ``"icon"``（官方脚本的 ``ui_type`` 切分）。
    ui_type: str
    platform: str
    group: str
    application: str

    def as_eval_sample(self) -> EvalSample:
        """转成评测口径的样本（GT bbox + 图像尺寸）。"""
        return EvalSample(bbox=self.bbox, image_size=self.image_size)


def load_samples(dataset_root: Path) -> list[SsproSample]:
    """读 ScreenSpot-Pro 的标注目录 → 样本清单。

    ``dataset_root`` 是数据集根（里面有 ``annotations/`` 与 ``images/``）。
    字段与官方脚本一致（``eval_screenspot_pro.py`` 里读的正是这些键）。
    """
    annotations = Path(dataset_root) / "annotations"
    images = Path(dataset_root) / "images"
    samples: list[SsproSample] = []
    for path in sorted(annotations.glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        for item in payload:
            width, height = (int(value) for value in item["img_size"])
            x1, y1, x2, y2 = (int(value) for value in item["bbox"])
            samples.append(
                SsproSample(
                    sample_id=str(item["id"]),
                    image_path=images / item["img_filename"],
                    bbox=(x1, y1, x2, y2),
                    image_size=(width, height),
                    instruction=str(item["instruction"]),
                    ui_type=str(item["ui_type"]),
                    platform=str(item["platform"]),
                    group=str(item["group"]),
                    application=str(item["application"]),
                )
            )
    return samples


def stratified_subset(
    samples: Sequence[SsproSample], per_stratum: int, *, seed: int = 20251020
) -> list[SsproSample]:
    """**分层抽样**：按 (平台 × 文本/图标) 分层，每层取固定条数。

    抽样是**确定的**（同一个种子 → 同一批样本）—— 报告里的 N 与分数
    必须来自同一批样本，重跑要能复现。
    """
    buckets: dict[tuple[str, str], list[SsproSample]] = {}
    for sample in samples:
        buckets.setdefault((sample.platform, sample.ui_type), []).append(sample)
    chosen: list[SsproSample] = []
    for key in sorted(buckets):
        bucket = sorted(buckets[key], key=lambda item: item.sample_id)
        random.Random(f"{seed}:{key}").shuffle(bucket)
        chosen.extend(bucket[:per_stratum])
    return sorted(chosen, key=lambda item: item.sample_id)


#: **官方 5 行**（ScreenSpot-Pro 官方仓的 5 个 coarse-to-fine 实现）。
#:
#: 分数一律标「**官方自报，仅供观感，不作硬对比**」—— 我们**没有在同模型下
#: 重跑**它们（官方方法要 OS-Atlas-7B 权重 + vLLM 服务，本机跑不了；
#: 6 GB 显存也放不下 7B BF16）。**硬判据只判我们自己的两行。**
#:
#: 分数出处：论文 Table 4（arXiv:2504.07981v1，基准模型 OS-Atlas-7B）。
#: 官方仓代码出处：``models/methods/*.py``、``models/zoom_consistency_router.py``。
OFFICIAL_ROWS: tuple[dict[str, str], ...] = (
    {
        "method": "ReGround（官方仓 methods/reground.py）",
        "model": "OS-Atlas-7B（OS-Copilot/OS-Atlas-Base-7B）",
        "dataset": "ScreenSpot-Pro 全量 1581 条",
        "crop": "crop 1024×1024（reground.py:151），整屏定位 + crop 内再定位各 1 次",
        "score": "40.2",
        "source": (
            "官方自报，仅供观感，不作硬对比（论文 Table 4，arXiv:2504.07981v1）；"
            "判定口径：官方 eval_screenspot_pro.py（归一化点落在归一化 bbox 内）"
        ),
    },
    {
        "method": "Iterative Narrowing（官方仓 methods/iterative_narrowing.py）",
        "model": "OS-Atlas-7B（OS-Copilot/OS-Atlas-Base-7B）",
        "dataset": "ScreenSpot-Pro 全量 1581 条",
        "crop": "max_search_depth=3，以预测为中心 ±0.25 归一化裁剪（iterative_narrowing.py:189-192）",
        "score": "31.9",
        "source": (
            "官方自报，仅供观感，不作硬对比（论文 Table 4，arXiv:2504.07981v1）；"
            "判定口径：官方 eval_screenspot_pro.py"
        ),
    },
    {
        "method": "Iterative Focusing（官方仓 methods/iterative_focusing.py）",
        "model": "OS-Atlas-7B（OS-Copilot/OS-Atlas-Base-7B）",
        "dataset": "ScreenSpot-Pro 全量 1581 条",
        "crop": "max_search_depth=3，2×2 象限切分取含预测的象限（iterative_focusing.py:156-161）",
        "score": "31.0",
        "source": (
            "官方自报，仅供观感，不作硬对比（论文 Table 4，arXiv:2504.07981v1；"
            "论文 §4.1 把同一方法称作 Iterative Zooming，对应关系为**推断**）；"
            "判定口径：官方 eval_screenspot_pro.py"
        ),
    },
    {
        "method": "ScreenSeekeR（官方仓 methods/screenseeker.py）",
        "model": "OS-Atlas-7B + planner gpt-4o（screenseeker.py:456）",
        "dataset": "ScreenSpot-Pro 全量 1581 条",
        "crop": "min_crop_size=[1280,768]、max_aspect_ratio=2.5（screenseeker.py:461-467）",
        "score": "48.1",
        "source": (
            "官方自报，仅供观感，不作硬对比（论文 Table 4，arXiv:2504.07981v1）；"
            "判定口径：官方 eval_screenspot_pro.py"
        ),
    },
    {
        "method": "Zoom Consistency Router（官方仓 models/zoom_consistency_router.py）",
        "model": "KV-Ground-8B + Qwen3.5-27B-AWQ（第三方 PR，非论文基线）",
        "dataset": "ScreenSpot-Pro（官方脚本口径）",
        "crop": "CROP_RATIO=0.5，2 步 zoom（zoom_consistency_router.py:43,180-203）",
        "score": "未取得",
        "source": (
            "**论文未评测该方法**（论文只有四个 coarse-to-fine 方法）；"
            "官方仓里它是第三方 PR，其数字只出现在 commit message 里，**未核实故不引用**；"
            "本机无 27B 权重，未在同模型下重跑 —— 仅供观感，不作硬对比"
        ),
    },
)


@dataclass(frozen=True)
class ArmOutcome:
    """一条样本在某一臂上的结果。"""

    sample_id: str
    ui_type: str
    platform: str
    group: str
    application: str
    hit: bool
    landing_point: tuple[int, int] | None
    coarse_point: tuple[float, float] | None
    gate_hit: bool
    gate_reasons: tuple[str, ...]
    gate_undecidable: tuple[str, ...]
    refine_enabled: bool
    fallback: bool
    fallback_cause: str
    model_calls: int
    error: str = ""

    @property
    def stratum(self) -> tuple[str, str]:
        """分层键（平台 × 文本/图标）。"""
        return (self.platform, self.ui_type)

    def to_dict(self) -> dict[str, Any]:
        return {
            "sample_id": self.sample_id,
            "ui_type": self.ui_type,
            "platform": self.platform,
            "group": self.group,
            "application": self.application,
            "hit": self.hit,
            "landing_point": list(self.landing_point) if self.landing_point else None,
            "coarse_point": list(self.coarse_point) if self.coarse_point else None,
            "gate_hit": self.gate_hit,
            "gate_reasons": list(self.gate_reasons),
            "gate_undecidable": list(self.gate_undecidable),
            "refine_enabled": self.refine_enabled,
            "fallback": self.fallback,
            "fallback_cause": self.fallback_cause,
            "model_calls": self.model_calls,
            "error": self.error,
        }


#: **门控输入的来源**（哪些条件可判定、哪些不可判定）—— 落进报告。
GATE_INPUT_SOURCES: dict[str, str] = {
    "coarse_validity": "可判定（模型自己的输出：粗定位为空或越界）",
    "consistency": "可判定（另问一次的第二次独立粗定位；不是逐字重发）",
    "target_area_ratio": "不可判定（粗定位只给点不给框；用 GT 框喂门控是拿答案当判据）",
    "confidence": "不可判定（模型不报置信度）",
}


class MemoizingModelCaller:
    """**应答记忆**的模型端口包装（评测侧）：同一次请求（提示词 + 画面）只问一次。

    ⚠️ **为什么需要它**：模型端**不确定** —— 同一条提示词两次问，答案可能不同。
    工单 M8 的硬判据要求两臂「**唯一差异是门控是否恒开**」；
    若两臂各自重问一次粗定位，差异里就混进了模型的随机性。
    ⚠️ **「42 条里 35 条两臂的粗定位点不同，最大差 3062 px，其中 11 条命中与否翻转」
    是**加这个端口之前**那一轮的数**（M8.md §3.1 自己标注了「第一轮的数作废、不引用」）；
    **端口之后两臂实际只差 6/42**（都是「一臂的模型调用失败 → 走了另一条路径」造成的），
    **噪声量级 = 6/42 = 14.3 pp** —— 留出集重评见工单 **M23**。

    记忆端口让两臂**共用同一次粗定位回答**（以及同一次整屏第二遍 / 精定位回答），
    两臂之间剩下的差异就**只有门控**。

    键 = (提示词, 画面哈希) —— 只有**完全相同**的请求才复用。
    """

    def __init__(self, inner: ModelCaller):
        self._inner = inner
        self._cache: dict[tuple[str, str], Any] = {}
        self._lock = threading.Lock()
        #: 真正发出去的请求数。
        self.calls = 0
        #: 复用（没再问）的回答数。
        self.replays = 0

    def complete(
        self,
        prompt: str,
        *,
        effort: Effort | str | None = None,
        max_tokens: int = 2000,
        image_base64: str | None = None,
        images_base64: Sequence[str] = (),
        thinking_disabled: bool = False,
    ) -> Any:
        key = (prompt, _image_key(image_base64, images_base64))
        with self._lock:
            cached = self._cache.get(key)
            if cached is not None:
                self.replays += 1
                return cached
        reply = self._inner.complete(
            prompt,
            effort=effort,
            max_tokens=max_tokens,
            image_base64=image_base64,
            images_base64=images_base64,
            thinking_disabled=thinking_disabled,
        )
        with self._lock:
            self._cache.setdefault(key, reply)
            self.calls += 1
        return reply


def _image_key(image_base64: str | None, images_base64: Sequence[str]) -> str:
    """画面的键（base64 的哈希 —— 键里不放几 MB 的原文）。"""
    digest = hashlib.sha256()
    if image_base64:
        digest.update(image_base64.encode("ascii"))
    for item in images_base64:
        digest.update(item.encode("ascii"))
    return digest.hexdigest()


def run_arms(
    samples: Sequence[SsproSample],
    client: ModelCaller,
    *,
    scale_by_size: dict[tuple[int, int], Any],
    model_input_by_size: dict[tuple[int, int], tuple[int, int]],
    effort: Effort | str = Effort.LOW,
    max_tokens: int = 2000,
    second_opinion: Callable[[CapturedFrame, str], tuple[float, float] | None] | None = None,
    progress: Callable[[str], None] | None = None,
    workers: int = 1,
    gate_params: Any = None,
) -> tuple[list[ArmOutcome], list[ArmOutcome]]:
    """跑两臂（门控开 / 门控关），返回 ``(门控开, 门控关)`` 两组结果。

    **同一条样本的两臂共用同一帧画面**（同一批样本、同一张图 —— 两臂可比）。

    ``workers > 1`` 时**按样本并行**（每条样本自己 decode、自己跑两臂）——
    模型调用是网络往返，并行只缩短墙钟时间，不改变任何判定。

    ``gate_params`` 是**门控参数**（运行时参数，工单 M7 的口径）：评测里
    调它就是「调门控」，不改编码。
    """

    def run_sample(sample: SsproSample) -> tuple[ArmOutcome, ArmOutcome]:
        frame = read_png_frame(sample.image_path)
        surface = (0, 0, frame.width, frame.height)
        scale = scale_by_size[sample.image_size]
        model_input = model_input_by_size[sample.image_size]
        on_demand = _run_one(
            sample,
            frame,
            surface,
            scale,
            model_input,
            GateMode.ON_DEMAND,
            effort,
            max_tokens,
            client,
            second_opinion,
            gate_params,
        )
        always = _run_one(
            sample,
            frame,
            surface,
            scale,
            model_input,
            GateMode.ALWAYS,
            effort,
            max_tokens,
            client,
            None,
            gate_params,
        )
        return on_demand, always

    pairs: list[tuple[ArmOutcome, ArmOutcome]] = []
    if workers <= 1:
        for index, sample in enumerate(samples, start=1):
            pair = run_sample(sample)
            pairs.append(pair)
            if progress is not None:
                progress(
                    f"[{index}/{len(samples)}] {sample.sample_id} "
                    f"开={pair[0].hit} 关={pair[1].hit}"
                )
    else:
        from concurrent.futures import ThreadPoolExecutor

        with ThreadPoolExecutor(max_workers=workers) as pool:
            for index, pair in enumerate(pool.map(run_sample, samples), start=1):
                pairs.append(pair)
                if progress is not None:
                    progress(
                        f"[{index}/{len(samples)}] {pairs[-1][0].sample_id} "
                        f"开={pair[0].hit} 关={pair[1].hit}"
                    )
    return [pair[0] for pair in pairs], [pair[1] for pair in pairs]


def _run_one(
    sample: SsproSample,
    frame: CapturedFrame,
    surface: tuple[int, int, int, int],
    scale: Any,
    model_input: tuple[int, int],
    gate_mode: GateMode,
    effort: Effort | str,
    max_tokens: int,
    client: ModelCaller,
    second_opinion,
    gate_params: Any = None,
) -> ArmOutcome:
    """跑一条样本的一个臂，判命中（**按官方脚本的判定口径**）。"""
    from minicode.action.ports import VisualLocateRequest
    from minicode.ground.channel import TwoStageGrounder

    grounder = TwoStageGrounder(
        client,
        scale=scale,
        model_input_size=model_input,
        full_screen_size=(frame.width, frame.height),
        effort=effort,
        max_tokens=max_tokens,
        gate_mode=gate_mode,
        second_opinion=second_opinion,
        gate_params=gate_params,
    )
    result = grounder.locate(
        VisualLocateRequest(
            target_description=sample.instruction,
            frame=frame,
            surface=surface,
        )
    )
    hit = False
    if result.landing_point is not None:
        prediction = SCREENSPOT_PRO.wrap(result.landing_point, sample.image_size)
        hit = SCREENSPOT_PRO.hit(prediction, sample.as_eval_sample())
    return ArmOutcome(
        sample_id=sample.sample_id,
        ui_type=sample.ui_type,
        platform=sample.platform,
        group=sample.group,
        application=sample.application,
        hit=hit,
        landing_point=result.landing_point,
        coarse_point=result.coarse_point,
        gate_hit=result.gate_hit,
        gate_reasons=tuple(result.gate_reasons),
        gate_undecidable=tuple(result.gate_undecidable),
        refine_enabled=result.refine_enabled,
        fallback=result.fallback,
        fallback_cause=result.fallback_cause,
        model_calls=result.model_calls,
        error=result.error_message,
    )


def _accuracy(outcomes: Sequence[ArmOutcome]) -> float:
    if not outcomes:
        return 0.0
    return sum(1 for outcome in outcomes if outcome.hit) / len(outcomes)


def summarize(outcomes: Sequence[ArmOutcome]) -> dict[str, Any]:
    """一批结果的汇总（整体 / 文本 / 图标 / 平台 / 分组 + 门控埋点）。"""
    gate_samples = [
        GateSample(
            gate_hit=outcome.gate_hit,
            gate_reasons=outcome.gate_reasons,
            gate_undecidable=outcome.gate_undecidable,
            refine_enabled=outcome.refine_enabled,
            fallback=outcome.fallback,
            fallback_cause=outcome.fallback_cause,
        )
        for outcome in outcomes
    ]
    gate: GateReport = build_gate_report(gate_samples)
    by_ui = {
        ui_type: _accuracy([o for o in outcomes if o.ui_type == ui_type])
        for ui_type in sorted({o.ui_type for o in outcomes})
    }
    by_platform = {
        platform: _accuracy([o for o in outcomes if o.platform == platform])
        for platform in sorted({o.platform for o in outcomes})
    }
    by_group = {
        group: _accuracy([o for o in outcomes if o.group == group])
        for group in sorted({o.group for o in outcomes})
    }
    return {
        "samples": len(outcomes),
        "accuracy": _accuracy(outcomes),
        "hits": sum(1 for outcome in outcomes if outcome.hit),
        "accuracy_by_ui_type": by_ui,
        "accuracy_by_platform": by_platform,
        "accuracy_by_group": by_group,
        "model_calls": sum(outcome.model_calls for outcome in outcomes),
        "errors": sum(1 for outcome in outcomes if outcome.error),
        "gate": gate.to_dict(),
    }


#: **防掩盖的子集容忍度**（百分点）：任何单一子集不得比门控关低超过它。
SUBSET_TOLERANCE_PP = 1.0


def compare_arms(
    on_demand: Sequence[ArmOutcome],
    always: Sequence[ArmOutcome],
    *,
    tolerance_pp: float = SUBSET_TOLERANCE_PP,
) -> dict[str, Any]:
    """两臂的对比：**硬判据** + **防掩盖**的逐子集对比。

    * 硬判据：**门控开的整体分 ≥ 门控关的整体分**；
    * 防掩盖：按 (平台 × 文本/图标) 分层逐项对比，任何单一子集不得比门控关
      低超过 ``tolerance_pp`` 个百分点。
    """
    on_demand_summary = summarize(on_demand)
    always_summary = summarize(always)
    overall_ok = on_demand_summary["accuracy"] >= always_summary["accuracy"]
    # **两臂的粗定位结果必须一致**（唯一差异是门控）—— 这一项是「可比性」的体检。
    always_by_id = {outcome.sample_id: outcome for outcome in always}
    shared = [
        outcome
        for outcome in on_demand
        if outcome.sample_id in always_by_id
        and outcome.coarse_point == always_by_id[outcome.sample_id].coarse_point
    ]
    strata: list[dict[str, Any]] = []
    for key in sorted({outcome.stratum for outcome in on_demand}):
        on_rate = _accuracy([o for o in on_demand if o.stratum == key])
        off_rate = _accuracy([o for o in always if o.stratum == key])
        strata.append(
            {
                "stratum": f"{key[0]}/{key[1]}",
                "on_demand": on_rate,
                "always": off_rate,
                "delta_pp": (on_rate - off_rate) * 100,
                "within_tolerance": (on_rate - off_rate) * 100 >= -tolerance_pp,
            }
        )
    return {
        "overall": {
            "on_demand": on_demand_summary["accuracy"],
            "always": always_summary["accuracy"],
            "delta_pp": (
                on_demand_summary["accuracy"] - always_summary["accuracy"]
            )
            * 100,
            "criterion_met": overall_ok,
        },
        "strata": strata,
        "anti_masking_met": all(item["within_tolerance"] for item in strata),
        "tolerance_pp": tolerance_pp,
        "coarse_shared": {
            "samples": len(on_demand),
            "identical": len(shared),
            "fraction": len(shared) / len(on_demand) if on_demand else 0.0,
            "note": (
                "两臂的粗定位点完全相同的条数 —— "
                "应当是 100%（唯一差异只能是门控）"
            ),
        },
        "on_demand": on_demand_summary,
        "always": always_summary,
    }


def table_rows(
    *,
    comparison: dict[str, Any],
    official: Sequence[dict[str, str]],
    model_id: str,
    crop_size: tuple[int, int],
) -> list[dict[str, str]]:
    """那张 **7 行表**（官方 5 行 + 本方案 2 行）。

    官方行逐行标「模型 / 数据集 / 裁剪参数 / 判定口径来源」；
    **未在同模型下重跑的必须标「官方自报，仅供观感，不作硬对比」**。
    """
    rows = [dict(row) for row in official]
    for mode, label in (("on_demand", "本方案 · 门控开"), ("always", "本方案 · 门控关")):
        summary = comparison[mode]
        rows.append(
            {
                "method": label,
                "model": f"{model_id}（本次实测）",
                "dataset": "ScreenSpot-Pro（本轮抽样，见报告 N）",
                "crop": f"crop {crop_size[0]}×{crop_size[1]}",
                "score": f"{summary['accuracy'] * 100:.1f}",
                "source": (
                    "本仓实测（同一批样本、同一模型、同一裁剪参数、同一判定口径）"
                ),
            }
        )
    return rows


def render_table(rows: Sequence[dict[str, str]]) -> str:
    """把那 7 行渲染成 markdown 表格。"""
    header = "| 方法 | 模型 | 数据集 | 裁剪参数 | 整体分 | 来源与口径 |"
    divider = "|---|---|---|---|---|---|"
    body = [
        "| {method} | {model} | {dataset} | {crop} | {score} | {source} |".format(**row)
        for row in rows
    ]
    return "\n".join([header, divider, *body])


def second_opinion_prompt(
    instruction: str, image_size: tuple[int, int] | None = None
) -> str:
    """**第二次独立粗定位**的提示词。

    ⚠️ 提示词本体在 :func:`minicode.ground.prompt.second_opinion_prompt`
    （grounding 的提示词只有那一处）；这里只是转出去，**不另写一份**。

    ``image_size`` = **送模型的那张图**的尺寸（声明它，模型答的像素才是
    那张图的像素 —— 两次粗定位要比的是同一个量）。
    """
    from minicode.ground.prompt import second_opinion_prompt as _prompt

    return _prompt(instruction, image_size=image_size)


def make_second_opinion(
    client: ModelCaller,
    *,
    effort: Effort | str = Effort.LOW,
    max_tokens: int = 2000,
) -> Callable[[CapturedFrame, str], tuple[float, float] | None]:
    """造一个「第二次独立粗定位」的端口（给 :class:`TwoStageGrounder` 用）。

    拿不到坐标（调用失败 / 回答里没有坐标）→ 返回 ``None``
    → 一致性条件**不可判定**（照旧显式落盘）。

    「问模型要一个点」走 :func:`minicode.ground.channel.ask_model_point`
    （**只有那一处实现**）；提示词走 grounding 提示词那一处。

    ⚠️ **端口收到的那张图就是送模型的那张图**（整屏那一级缩到声明尺寸的那张），
    提示词里也**声明它的尺寸** → 第二次的答案与第一次的答案在**同一个空间**里，
    「两次是否一致」这条门控条件才判得了。
    """

    from minicode.ground.channel import ask_model_point
    from minicode.ground.prompt import second_opinion_prompt

    def second_opinion(frame: CapturedFrame, target: str) -> tuple[float, float] | None:
        try:
            return ask_model_point(
                client,
                second_opinion_prompt(
                    target, image_size=(frame.width, frame.height)
                ),
                frame,
                effort=effort,
                max_tokens=max_tokens,
            )
        except Exception:
            return None

    return second_opinion


def gate_params_snapshot(gate_params: Any = None) -> dict[str, Any]:
    """门控参数（连同取值来源）—— 落进报告，便于事后核对。

    ⚠️ **报的是本次实际用的那一套**（不是默认值）—— 工单 M7 的口径：
    「门控参数是运行时参数，取值来源必须被记录」。
    """

    return (gate_params or default_gate_params()).to_dict()


def elapsed(started: float) -> float:
    """已经过去的秒数。"""
    return time.time() - started

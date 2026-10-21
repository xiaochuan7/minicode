"""**门控结论的留出集重评**（工单 M23）。

M8 的门控结论（门控开 59.5% ≥ 门控关 57.1%）是**在同一批样本上调参数**得到的
（一致性容差 128 px → 16 px）—— **同批调参 + 同批评估 = 过拟合**。
本模块把判定改成**留出集**判定：**阈值只在样本 A 上调，结论只看样本 B**。

## 输入：已记录的跑批产物（**不重调模型**）

* ``--run``：M8 那轮评测的 ``sspro-report.json``（**逐样本两臂的记录**）；
* ``--dataset``：ScreenSpot-Pro 数据集根（取 GT bbox 与图像尺寸）。

**为什么可以不重调模型**：两臂的**粗定位回答与精定位回答是同一次**
（应答记忆端口，见 :class:`minicode.eval.sspro.MemoizingModelCaller`），
所以记录里已经同时有「开了精定位」与「没开精定位」两种结局各自的结果；
换掉门控阈值之后该走哪条路，只剩两件事记录里没有：

* **一致性距离** vs 一致性容差；
* **精定位偏离粗定位的距离** vs 偏离容差。

两件事记录里都只留了**判定**（在记录容差下命中与否），由判定能推出**区间**。

## 区间：记录能判多少，就报多少

设记录那一轮的容差为 ``T_rec``，则每条样本的一致性距离 ``d`` 满足：

============================  ==========================
记录里的判定                  能推出的 ``d`` 的范围
============================  ==========================
``coarse_inconsistent``        ``d > T_rec``
（两次粗定位不一致）            → 容差 ≤ ``T_rec`` 时**必然**仍判不一致
``coarse_inconsistent``        ``d > T_rec``
且候选容差 > ``T_rec``          → ``d`` 判不了 → **给区间**
（一致）                       ``d ≤ T_rec``
                              → 容差 ≥ ``T_rec`` 时**必然**仍判一致
（一致）且候选容差 < ``T_rec``   → ``d`` 判不了 → **给区间**
``consistency`` 不可判定        → 一致性条件**任何容差下都不触发**（恒定）
============================  ==========================

偏离容差同理：记录里的回落只说明「偏离 > 记录容差」，
**记录容差以下**必然仍回落，**以上**判不了。

「判不了」的那部分**不给点估计** —— 命中数报成 ``[下界, 上界]`` 区间，
调参按**悲观下界**选（宁可低估，不许高估）。工单 M23 AC 4 的
「差值必须超过噪声量级」因此仍然是硬判据。

⚠️ **本模块只重评，不改判定口径**：命中判定仍走
:data:`minicode.eval.formats.SCREENSPOT_PRO`（工单 M7 的口径纪律）。
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from minicode.ground.gate import GateParams, default_gate_params

from .formats import SCREENSPOT_PRO
from .sspro import ArmOutcome, load_samples

__all__ = [
    "ArmScore",
    "HalfResult",
    "HoldoutEvaluation",
    "HoldoutSample",
    "RefinePath",
    "RecordedRun",
    "Split",
    "Tuning",
    "build_report",
    "evaluate_half",
    "gate_opens_at",
    "load_recorded_run",
    "main",
    "reconstruct",
    "render_report",
    "replay_check",
    "run_holdout",
    "score_always",
    "score_on_demand",
    "split_samples",
    "tune_gate_params",
]

#: **两半的名字**：``A`` = 调参半，``B`` = 评估半（结论只看 B）。
SPLIT_TUNING = "A"
SPLIT_EVAL = "B"

#: **一致性容差的候选网格**（物理像素）。
#:
#: 取值覆盖 M8 用过的两档（16 与 128）并在 16~128 之间加密；
#: 末尾的 ``1e9`` = 「等于把一致性条件关掉」（距离再大也不算不一致）。
TOLERANCE_CANDIDATES: tuple[float, ...] = (
    0.0,
    4.0,
    8.0,
    16.0,
    24.0,
    32.0,
    48.0,
    64.0,
    96.0,
    128.0,
    192.0,
    256.0,
    512.0,
    1e9,
)

#: **偏离容差的候选网格**（物理像素）。
#:
#: 覆盖记录里的那一档（128）与其下；末尾的 ``1e9`` = 「永不因偏离而回落」。
DEVIATION_CANDIDATES: tuple[float, ...] = (
    0.0,
    8.0,
    16.0,
    32.0,
    48.0,
    64.0,
    96.0,
    128.0,
    192.0,
    256.0,
    512.0,
    1e9,
)

#: **门控关闭率判据**（工单 M8 AC 4）：门控不是恒开 → 关闭率必须 > 0。
GATE_OFF_RATE_MIN = 0.0

#: **防掩盖的子集容忍度**（百分点）—— 沿用 M8 的口径（工单 M8 AC 3）。
SUBSET_TOLERANCE_PP = 1.0

#: **一致性状态**的三种取值。
CONSISTENT = "consistent"
INCONSISTENT = "inconsistent"
UNDECIDABLE = "undecidable"

#: **精定位路径的三种形态**。
PATH_WHOLE_SCREEN = "whole_screen"
PATH_REFINE_FAILED = "refine_failed"
PATH_REFINE = "refine"


# ---------------------------------------------------------------------------
# 切分
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Split:
    """**两半的切分结果**。"""

    #: 样本 ID → ``"A"``（调参）/ ``"B"``（评估）。
    assignment: Mapping[str, str]
    rule: str

    def ids(self, half: str) -> list[str]:
        """某一半的样本 ID（升序）。"""
        return sorted(sid for sid, value in self.assignment.items() if value == half)

    def of(self, sample_id: str) -> str:
        """某条样本属于哪一半。"""
        return self.assignment[sample_id]


#: **切分规则**（写进代码，不是手挑 —— 工单 M23 AC 1）。
SPLIT_RULE = (
    "按 (平台 × 文本/图标) 分层；层内按**样本 ID 字典序**排序；"
    "排序后**交替**分配 —— 第 1、3、5… 条进 A（调参），第 2、4、6… 条进 B（评估）。"
    "→ 两半的层构成完全相同、条数差 ≤ 1，且归属**只由样本 ID 决定**"
    "（同一个 ID 集合永远得到同一个归属）。"
)


def split_samples(sample_ids_by_stratum: Mapping[str, Sequence[str]]) -> Split:
    """把样本切成 A（调参）/ B（评估）两半 —— **规则见** :data:`SPLIT_RULE`。

    ``sample_ids_by_stratum`` 是「层 → 该层的样本 ID 列表」。
    """
    assignment: dict[str, str] = {}
    for stratum in sorted(sample_ids_by_stratum):
        ordered = sorted(sample_ids_by_stratum[stratum])
        for index, sample_id in enumerate(ordered):
            assignment[sample_id] = SPLIT_TUNING if index % 2 == 0 else SPLIT_EVAL
    return Split(assignment=assignment, rule=SPLIT_RULE)


# ---------------------------------------------------------------------------
# 记录
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RecordedRun:
    """**已记录的跑批产物**（M8 那轮的 ``sspro-report.json``）。"""

    path: Path
    #: 门控开那一臂，逐样本。
    on_demand: Mapping[str, ArmOutcome]
    #: 门控关那一臂，逐样本。
    always: Mapping[str, ArmOutcome]
    #: 记录那一轮的一致性容差（物理像素）—— 重建「一致性距离区间」的锚点。
    recorded_tolerance_px: float
    #: 记录那一轮的偏离容差（物理像素）—— 重建「偏离区间」的锚点。
    recorded_deviation_px: float
    #: 记录里的门控参数（含取值来源串）。
    gate_params: Mapping[str, Any]
    #: 记录里的可比性体检（``model_calls.sent`` / ``model_calls.replayed``）。
    model_calls: Mapping[str, Any]
    #: 记录里的模型与数据集信息。
    dataset: Mapping[str, Any]
    model: Mapping[str, Any]


def _as_point(value: Any) -> tuple[float, float] | None:
    """记录里的点（list）→ 元组；没有就是 ``None``。"""
    if not value:
        return None
    return (float(value[0]), float(value[1]))


def _as_outcome(payload: Mapping[str, Any]) -> ArmOutcome:
    """记录里的一条 outcome → :class:`minicode.eval.sspro.ArmOutcome`。"""
    landing = payload.get("landing_point")
    return ArmOutcome(
        sample_id=str(payload["sample_id"]),
        ui_type=str(payload["ui_type"]),
        platform=str(payload["platform"]),
        group=str(payload.get("group", "")),
        application=str(payload.get("application", "")),
        hit=bool(payload["hit"]),
        landing_point=None if not landing else (int(landing[0]), int(landing[1])),
        coarse_point=_as_point(payload.get("coarse_point")),
        gate_hit=bool(payload["gate_hit"]),
        gate_reasons=tuple(payload.get("gate_reasons", ())),
        gate_undecidable=tuple(payload.get("gate_undecidable", ())),
        refine_enabled=bool(payload["refine_enabled"]),
        fallback=bool(payload.get("fallback", False)),
        fallback_cause=str(payload.get("fallback_cause", "")),
        model_calls=int(payload.get("model_calls", 0)),
        error=str(payload.get("error", "")),
    )


def load_recorded_run(path: str | Path) -> RecordedRun:
    """读 M8 那轮的 ``sspro-report.json``。"""
    report_path = Path(path)
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    defaults = default_gate_params()
    gate_params = payload.get("gate", {}).get("params", {})
    return RecordedRun(
        path=report_path,
        on_demand={
            str(item["sample_id"]): _as_outcome(item)
            for item in payload["outcomes"]["on_demand"]
        },
        always={
            str(item["sample_id"]): _as_outcome(item)
            for item in payload["outcomes"]["always"]
        },
        recorded_tolerance_px=float(
            gate_params.get("consistency_tolerance_px", defaults.consistency_tolerance_px)
        ),
        recorded_deviation_px=float(
            gate_params.get("deviation_tolerance_px", defaults.deviation_tolerance_px)
        ),
        gate_params=gate_params,
        model_calls=payload.get("model_calls", {}),
        dataset=payload.get("dataset", {}),
        model=payload.get("model", {}),
    )


# ---------------------------------------------------------------------------
# 重建
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RefinePath:
    """一条样本**走精定位路径**的结果（相对偏离容差可变）。

    * ``kind`` —— :data:`PATH_WHOLE_SCREEN`（粗定位为空 → 开的是整屏第二遍，
      与偏离容差无关）/ :data:`PATH_REFINE_FAILED`（精定位失败，与偏离容差无关）/
      :data:`PATH_REFINE`（crop 精定位，**可能因偏离而回落**）；
    * ``hit`` —— 精定位落点的命中（``None`` = 记录里判不了）；
    * ``coarse_hit`` —— 回落到粗定位点时的命中；
    * ``deviation`` —— 精定位落点与粗定位点的距离（``None`` = 判不了）。
    """

    kind: str
    hit: bool | None
    coarse_hit: bool | None
    deviation: float | None


@dataclass(frozen=True)
class HoldoutSample:
    """**一条样本在留出集重评里的全部事实**。

    * ``coarse_hit`` —— 门控关掉精定位时**直接用粗定位点**落点的命中；
    * ``od_path`` / ``always_path`` —— 两臂**走精定位路径**时的结果；
    * ``consistency`` —— 记录那一轮的一致性判定（``d`` 的区间由它给出）。
    """

    sample_id: str
    stratum: str
    split: str
    coarse_point: tuple[float, float] | None
    coarse_hit: bool | None
    always_coarse_hit: bool | None
    od_path: RefinePath
    always_path: RefinePath
    recorded_hit: bool
    recorded_always_hit: bool
    recorded_gate_hit: bool
    consistency: str
    coarse_invalid: bool
    coarse_differs: bool
    flip: bool
    area_ratio: float
    gate_reasons: tuple[str, ...]
    model_calls: int

    def to_dict(self) -> dict[str, Any]:
        """落盘形状（每条样本属于哪一半 —— 工单 M23 AC 1）。"""
        return {
            "sample_id": self.sample_id,
            "stratum": self.stratum,
            "split": self.split,
            "coarse_point": list(self.coarse_point) if self.coarse_point else None,
            "coarse_hit": self.coarse_hit,
            "always_coarse_hit": self.always_coarse_hit,
            "od_path": {
                "kind": self.od_path.kind,
                "hit": self.od_path.hit,
                "deviation": self.od_path.deviation,
            },
            "always_path": {
                "kind": self.always_path.kind,
                "hit": self.always_path.hit,
                "deviation": self.always_path.deviation,
            },
            "recorded_hit": self.recorded_hit,
            "recorded_always_hit": self.recorded_always_hit,
            "recorded_gate_hit": self.recorded_gate_hit,
            "consistency": self.consistency,
            "coarse_invalid": self.coarse_invalid,
            "coarse_differs": self.coarse_differs,
            "flip": self.flip,
            "area_ratio": self.area_ratio,
            "gate_reasons": list(self.gate_reasons),
            "model_calls": self.model_calls,
        }


def _consistency_state(outcome: ArmOutcome) -> str:
    """记录那一轮的一致性判定 → 三态。

    * ``coarse_inconsistent`` → 距离 **>** 记录容差；
    * ``consistency`` 不可判定 → 距离**判不了**（任何容差下都不触发）；
    * 其余（一致性可判定且判一致）→ 距离 **≤** 记录容差。
    """
    if "coarse_inconsistent" in outcome.gate_reasons:
        return INCONSISTENT
    if "consistency" in outcome.gate_undecidable:
        return UNDECIDABLE
    return CONSISTENT


def _refine_path(
    outcome: ArmOutcome, coarse_hit: bool | None
) -> RefinePath:
    """把一条臂的记录转成**精定位路径**的事实（相对偏离容差可变）。"""
    if "coarse_invalid" in outcome.gate_reasons:
        # 粗定位为空 / 越界 → 开的是**整屏第二遍**（与偏离容差无关）。
        return RefinePath(
            kind=PATH_WHOLE_SCREEN, hit=outcome.hit, coarse_hit=coarse_hit, deviation=None
        )
    if outcome.fallback_cause == "deviation":
        # 偏离过大而回落 → 精定位落点**判不了**（只知道偏离 > 记录容差）。
        return RefinePath(kind=PATH_REFINE, hit=None, coarse_hit=coarse_hit, deviation=None)
    if outcome.fallback_cause:
        # 精定位失败（无输出 / 越界 / 调用失败）→ 与偏离容差无关，必定回落。
        return RefinePath(
            kind=PATH_REFINE_FAILED, hit=coarse_hit, coarse_hit=coarse_hit, deviation=None
        )
    deviation = None
    if outcome.landing_point is not None and outcome.coarse_point is not None:
        deviation = math.hypot(
            outcome.landing_point[0] - outcome.coarse_point[0],
            outcome.landing_point[1] - outcome.coarse_point[1],
        )
    return RefinePath(
        kind=PATH_REFINE, hit=outcome.hit, coarse_hit=coarse_hit, deviation=deviation
    )


def _coarse_hit(
    outcome: ArmOutcome, truth: Any
) -> bool | None:
    """粗定位点**直接**落点是否命中（离线按官方口径算；没有粗定位点就是 ``None``）。"""
    if truth is None or outcome.coarse_point is None:
        return None
    point = (
        int(round(outcome.coarse_point[0])),
        int(round(outcome.coarse_point[1])),
    )
    prediction = SCREENSPOT_PRO.wrap(point, truth.image_size)
    return SCREENSPOT_PRO.hit(prediction, truth.as_eval_sample())


def reconstruct(
    run: RecordedRun,
    dataset_root: str | Path,
    split: Split,
) -> list[HoldoutSample]:
    """把记录重建成**逐样本的事实表**（不留一手猜测）。

    * 命中一律**离线**按官方口径算（内部原图像素，出口只经
      :data:`minicode.eval.formats.SCREENSPOT_PRO` 转一次）；
    * 两臂**共用同一次精定位回答**（应答记忆端口），所以「走精定位路径」的
      结果两臂一致（落点逐条相同，见 ``coarse_shared`` 体检）。
    """
    ground_truth = {sample.sample_id: sample for sample in load_samples(Path(dataset_root))}
    samples: list[HoldoutSample] = []
    for sample_id in sorted(run.on_demand):
        on_demand = run.on_demand[sample_id]
        always = run.always.get(sample_id)
        truth = ground_truth.get(sample_id)
        area_ratio = 0.0
        if truth is not None:
            width, height = truth.image_size
            area_ratio = (
                (truth.bbox[2] - truth.bbox[0]) * (truth.bbox[3] - truth.bbox[1])
            ) / float(width * height)
        coarse_hit = _coarse_hit(on_demand, truth)
        always_coarse_hit = _coarse_hit(always, truth) if always is not None else None
        always_path = (
            _refine_path(always, always_coarse_hit)
            if always is not None
            else RefinePath(
                kind=PATH_REFINE_FAILED, hit=False, coarse_hit=None, deviation=None
            )
        )
        # 门控**没开**的那几条：这一臂的落点就是粗定位点（不是精定位落点）。
        # 若门控开了，它走的就是**精定位路径** —— 与门控关那一臂同一条
        # （同一个粗定位点 → 同一块 crop → 同一次应答记忆里的回答）。
        od_path = always_path if not on_demand.gate_hit else _refine_path(
            on_demand, coarse_hit
        )
        samples.append(
            HoldoutSample(
                sample_id=sample_id,
                stratum=f"{on_demand.platform}/{on_demand.ui_type}",
                split=split.of(sample_id),
                coarse_point=on_demand.coarse_point,
                coarse_hit=coarse_hit,
                always_coarse_hit=always_coarse_hit,
                od_path=od_path,
                always_path=always_path,
                recorded_hit=on_demand.hit,
                recorded_always_hit=bool(always.hit if always else False),
                recorded_gate_hit=on_demand.gate_hit,
                consistency=_consistency_state(on_demand),
                coarse_invalid="coarse_invalid" in on_demand.gate_reasons,
                coarse_differs=(
                    always is not None and always.coarse_point != on_demand.coarse_point
                ),
                flip=always is not None and on_demand.hit != always.hit,
                area_ratio=area_ratio,
                gate_reasons=on_demand.gate_reasons,
                model_calls=on_demand.model_calls,
            )
        )
    return samples


# ---------------------------------------------------------------------------
# 门控判定与打分
# ---------------------------------------------------------------------------


def gate_opens_at(
    sample: HoldoutSample, tolerance_px: float, recorded_tolerance_px: float
) -> bool | None:
    """容差 ``tolerance_px`` 下门控是否开精定位。

    ``None`` = **记录判不了**（一致性距离落在记录给不出的区间里）
    → 上层只能给区间，不给点估计。
    """
    if sample.coarse_invalid:
        # 粗定位本身为空/越界 → 与容差无关，**必然**开。
        return True
    if sample.consistency == INCONSISTENT:
        # 距离 > 记录容差 → 容差不更大时**必然**仍判不一致。
        return True if tolerance_px <= recorded_tolerance_px else None
    if sample.consistency == CONSISTENT:
        # 距离 ≤ 记录容差 → 容差不更小时**必然**仍判一致。
        return False if tolerance_px >= recorded_tolerance_px else None
    # 一致性不可判定 → 该条件**任何容差下都不触发**。
    return False


def refine_hit_at(
    path: RefinePath, deviation_tolerance_px: float, recorded_deviation_px: float
) -> bool | None:
    """偏离容差下，走精定位路径的落点是否命中（``None`` = 判不了）。"""
    if path.kind == PATH_WHOLE_SCREEN:
        # 整屏第二遍：与偏离容差无关。
        return path.hit
    if path.kind == PATH_REFINE_FAILED:
        # 精定位失败：与偏离容差无关，必定回落到粗定位点。
        return path.coarse_hit
    if path.deviation is None:
        # 记录只知道「偏离 > 记录容差」→ 记录容差以下**必然**仍回落，以上判不了。
        return path.coarse_hit if deviation_tolerance_px <= recorded_deviation_px else None
    if path.deviation > deviation_tolerance_px:
        # 偏离超过容差 → 回落。
        return path.coarse_hit
    return path.hit


@dataclass(frozen=True)
class ArmScore:
    """一臂在某组参数下的命中数（可能是区间）。"""

    samples: int
    hits_lower: int
    hits_upper: int
    #: 门控开启精定位的条数区间。
    refine_lower: int
    refine_upper: int
    #: 记录判不了的条数（区间不是点的那部分）。
    undetermined: int

    @property
    def determined(self) -> bool:
        """命中数是不是**点估计**（区间退化成一点）。"""
        return self.hits_lower == self.hits_upper

    @property
    def accuracy(self) -> float:
        """命中率（区间取**悲观下界** —— 宁可低估，不许高估）。"""
        if self.samples <= 0:
            return 0.0
        return self.hits_lower / self.samples

    def to_dict(self) -> dict[str, Any]:
        """落盘形状。"""
        return {
            "samples": self.samples,
            "hits_lower": self.hits_lower,
            "hits_upper": self.hits_upper,
            "accuracy_lower_bound": self.accuracy,
            "accuracy_upper_bound": (
                self.hits_upper / self.samples if self.samples > 0 else 0.0
            ),
            "determined": self.determined,
            "refine_enabled_lower": self.refine_lower,
            "refine_enabled_upper": self.refine_upper,
            "gate_off_rate_lower": (
                1 - self.refine_upper / self.samples if self.samples > 0 else 0.0
            ),
            "undetermined_samples": self.undetermined,
        }


def score_on_demand(
    samples: Sequence[HoldoutSample],
    tolerance_px: float,
    recorded_tolerance_px: float,
    deviation_tolerance_px: float,
    recorded_deviation_px: float,
) -> ArmScore:
    """**门控开**那一臂在某组参数下的命中数（区间）。

    * 门控**开** → 走精定位路径 → 命中数取 ``refine_hit_at``；
    * 门控**关** → 直接用粗定位点落点 → 命中数取 ``coarse_hit``；
    * 判不了 → 两种可能都给，合成区间。
    """
    hits_lower = hits_upper = 0
    refine_lower = refine_upper = 0
    undetermined = 0
    for sample in samples:
        opens = gate_opens_at(sample, tolerance_px, recorded_tolerance_px)
        if opens is False:
            hits_lower += int(bool(sample.coarse_hit))
            hits_upper += int(bool(sample.coarse_hit))
            continue
        refined = refine_hit_at(
            sample.od_path, deviation_tolerance_px, recorded_deviation_px
        )
        if opens is True:
            if refined is None:
                undetermined += 1
                hits_lower += 0
                hits_upper += 1
                refine_lower += 1
                refine_upper += 1
                continue
            hits_lower += int(refined)
            hits_upper += int(refined)
            refine_lower += 1
            refine_upper += 1
            continue
        # 门控开不开判不了 → 两条路都给。
        undetermined += 1
        skip_hit = int(bool(sample.coarse_hit))
        if refined is None:
            # 精定位落点判不了 → 悲观下界按「没打中」算。
            hits_lower += min(skip_hit, 0)
            hits_upper += max(skip_hit, 1)
        else:
            hits_lower += min(int(refined), skip_hit)
            hits_upper += max(int(refined), skip_hit)
        refine_lower += 0
        refine_upper += 1
    return ArmScore(
        samples=len(samples),
        hits_lower=hits_lower,
        hits_upper=hits_upper,
        refine_lower=refine_lower,
        refine_upper=refine_upper,
        undetermined=undetermined,
    )


def score_always(
    samples: Sequence[HoldoutSample],
    deviation_tolerance_px: float,
    recorded_deviation_px: float,
) -> ArmScore:
    """**门控关**那一臂（无条件精定位）在某组参数下的命中数（区间）。"""
    hits_lower = hits_upper = 0
    undetermined = 0
    for sample in samples:
        refined = refine_hit_at(
            sample.always_path, deviation_tolerance_px, recorded_deviation_px
        )
        if refined is None:
            undetermined += 1
            hits_lower += 0
            hits_upper += 1
            continue
        hits_lower += int(refined)
        hits_upper += int(refined)
    return ArmScore(
        samples=len(samples),
        hits_lower=hits_lower,
        hits_upper=hits_upper,
        refine_lower=len(samples),
        refine_upper=len(samples),
        undetermined=undetermined,
    )


def replay_check(
    samples: Sequence[HoldoutSample],
    recorded_tolerance_px: float,
    recorded_deviation_px: float,
) -> dict[str, Any]:
    """**重建的自检**：用记录里的参数重放，必须复现记录里的命中。

    这是整个离线重建的护栏：两臂的命中都逐条复现 → 重建与当时的跑批是同一回事。
    """
    on_demand_matches = sum(
        1
        for sample in samples
        if _replayed_on_demand(sample, recorded_tolerance_px, recorded_deviation_px)
        == sample.recorded_hit
    )
    always_matches = sum(
        1
        for sample in samples
        if _replayed_always(sample, recorded_deviation_px) == sample.recorded_always_hit
    )
    return {
        "on_demand_matches": on_demand_matches,
        "always_matches": always_matches,
        "samples": len(samples),
        "consistent": on_demand_matches == len(samples)
        and always_matches == len(samples),
        "note": (
            "用记录里的参数（一致性容差 + 偏离容差）重放："
            "两臂的命中逐条复现记录 → 离线重建与当时的跑批是同一回事"
        ),
    }


def _replayed_on_demand(
    sample: HoldoutSample, recorded_tolerance_px: float, recorded_deviation_px: float
) -> bool | None:
    """门控开那一臂在**记录参数**下的命中（单条）。"""
    opens = gate_opens_at(sample, recorded_tolerance_px, recorded_tolerance_px)
    if opens is False:
        return bool(sample.coarse_hit)
    return refine_hit_at(sample.od_path, recorded_deviation_px, recorded_deviation_px)


def _replayed_always(sample: HoldoutSample, recorded_deviation_px: float) -> bool | None:
    """门控关那一臂在**记录参数**下的命中（单条）。"""
    return refine_hit_at(sample.always_path, recorded_deviation_px, recorded_deviation_px)


# ---------------------------------------------------------------------------
# 调参（只在 A 上）与评估
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ParameterCandidate:
    """一组候选门控参数在 **A 半**上的表现。"""

    tolerance_px: float
    deviation_px: float
    on_demand: ArmScore
    always: ArmScore
    delta_pp_lower: float
    delta_pp_upper: float

    def to_dict(self) -> dict[str, Any]:
        """落盘形状。"""
        return {
            "tolerance_px": self.tolerance_px,
            "deviation_px": self.deviation_px,
            "on_demand": self.on_demand.to_dict(),
            "always": self.always.to_dict(),
            "delta_pp_lower": self.delta_pp_lower,
            "delta_pp_upper": self.delta_pp_upper,
        }


@dataclass(frozen=True)
class Tuning:
    """**在 A 上调出来的门控参数**（含取值来源串）。"""

    tolerance_px: float
    deviation_px: float
    params: GateParams
    candidates: tuple[ParameterCandidate, ...]
    rule: str

    def to_dict(self) -> dict[str, Any]:
        """落盘形状（参数 + 取值来源 + 扫参过程）。"""
        return {
            "tolerance_px": self.tolerance_px,
            "deviation_px": self.deviation_px,
            "params": self.params.to_dict(),
            "rule": self.rule,
            "candidates": [candidate.to_dict() for candidate in self.candidates],
        }


#: **调参规则**（写进代码，不许手挑 —— 工单 M23 AC 2）。
TUNING_RULE = (
    "只在 A（调参半）上扫「一致性容差 × 偏离容差」的候选网格；"
    "每个候选用**悲观下界**算 A 的「门控开」命中率；"
    "取下界最大者；并列时取**判不了的条数最少**的（结论要可复核），"
    "再并列取**一致性容差最大**的（门控开得最少 = 省模型调用），"
    "再并列取**偏离容差最大**的。B 上不许再调任何参数。"
)


def tune_gate_params(
    samples_a: Sequence[HoldoutSample],
    recorded_tolerance_px: float,
    recorded_deviation_px: float,
    tolerance_candidates: Sequence[float] = TOLERANCE_CANDIDATES,
    deviation_candidates: Sequence[float] = DEVIATION_CANDIDATES,
) -> Tuning:
    """**在 A 上调**门控参数（返回选中的那一组 + 整个扫参过程）。

    ⚠️ **B 的样本一条都不进这里** —— 调参只看 A（工单 M23 AC 2）。

    「目标占比」与「置信度」两条条件**不在网格里**：它们在本评测台上
    **不可判定**（粗定位只给点不给框；模型不报置信度），门控根本不会因为
    它们触发 → 无从调（见 :data:`minicode.eval.sspro.GATE_INPUT_SOURCES`）。
    """
    scored: list[ParameterCandidate] = []
    for tolerance in tolerance_candidates:
        for deviation in deviation_candidates:
            on_demand = score_on_demand(
                samples_a,
                tolerance,
                recorded_tolerance_px,
                deviation,
                recorded_deviation_px,
            )
            always = score_always(samples_a, deviation, recorded_deviation_px)
            scored.append(
                ParameterCandidate(
                    tolerance_px=tolerance,
                    deviation_px=deviation,
                    on_demand=on_demand,
                    always=always,
                    delta_pp_lower=(on_demand.accuracy - always.accuracy) * 100,
                    delta_pp_upper=(
                        (on_demand.hits_upper - always.hits_upper) / len(samples_a) * 100
                        if samples_a
                        else 0.0
                    ),
                )
            )
    best = max(
        scored,
        key=lambda item: (
            item.on_demand.hits_lower,
            -item.on_demand.undetermined,
            item.tolerance_px,
            item.deviation_px,
        ),
    )
    defaults = default_gate_params()
    params = GateParams(
        area_ratio_threshold=defaults.area_ratio_threshold,
        area_ratio_source=defaults.area_ratio_source,
        confidence_threshold=defaults.confidence_threshold,
        confidence_source=defaults.confidence_source,
        consistency_tolerance_px=best.tolerance_px,
        consistency_source=(
            f"工单 M23 留出集重评：在**调参半 A** 上按「A 半命中率（悲观下界）最大、"
            f"并列取最大容差」的规则选出 = {best.tolerance_px:g} px；"
            f"**未在评估半 B 上调**；**未经独立校准** → 工程阈值·未验证"
        ),
        deviation_tolerance_px=best.deviation_px,
        deviation_source=(
            f"工单 M23 留出集重评：在**调参半 A** 上按同一规则选出 = "
            f"{best.deviation_px:g} px（两臂共用同一条回落规则 → 对「门控开 vs 门控关」"
            f"的差值只经由「门控关掉了精定位」的那几条起作用）；"
            f"**未在评估半 B 上调**；**未经独立校准** → 工程阈值·未验证"
        ),
    )
    return Tuning(
        tolerance_px=best.tolerance_px,
        deviation_px=best.deviation_px,
        params=params,
        candidates=tuple(scored),
        rule=TUNING_RULE,
    )


@dataclass(frozen=True)
class HalfResult:
    """**某一半**（A 或 B）的评估结果。"""

    half: str
    samples: int
    on_demand: ArmScore
    always: ArmScore
    always_hits: int
    always_accuracy: float
    delta_pp: float
    delta_is_point: bool
    gate_off_rate: float
    flips: int
    coarse_differs: int
    noise_flip_pp: float
    noise_coarse_differs_pp: float
    noise_magnitude_pp: float
    flips_gate_attributable: int
    flips_noise_attributable: int
    hard_criterion_met: bool
    noise_criterion_met: bool
    criterion_met: bool
    conclusion: str
    #: **第三条臂**：粗定位**直接**落点（不做第二遍精定位）——
    #: M8 那个 38.1% 的基线，以及「crop 精定位 胜/负」条数。
    coarse_only_hits: int
    coarse_only_accuracy: float
    refine_wins: int
    refine_losses: int
    strata: tuple[dict[str, Any], ...]
    anti_masking_met: bool
    coarse_shared: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        """落盘形状。"""
        return {
            "half": self.half,
            "samples": self.samples,
            "on_demand": self.on_demand.to_dict(),
            "always_hits": self.always_hits,
            "always_accuracy": self.always_accuracy,
            "delta_pp": self.delta_pp,
            "delta_is_point_estimate": self.delta_is_point,
            "gate_off_rate": self.gate_off_rate,
            "gate_off_rate_definition": "门控**未触发**的条数 / 样本数（≠ M8 的「没开 crop 精定位」）",
            "flips": self.flips,
            "coarse_differs": self.coarse_differs,
            "noise": {
                "flip_count": self.flips,
                "flip_pp": self.noise_flip_pp,
                "flips_gate_attributable": self.flips_gate_attributable,
                "flips_noise_attributable": self.flips_noise_attributable,
                "coarse_differs_count": self.coarse_differs,
                "coarse_differs_ratio_pp": self.noise_coarse_differs_pp,
                "magnitude_pp": self.noise_magnitude_pp,
            },
            "criteria": {
                "hard_criterion_met": self.hard_criterion_met,
                "noise_criterion_met": self.noise_criterion_met,
                "criterion_met": self.criterion_met,
            },
            "criterion_met": self.criterion_met,
            "conclusion": self.conclusion,
            "coarse_only": {
                "hits": self.coarse_only_hits,
                "accuracy": self.coarse_only_accuracy,
                "note": "粗定位**直接**落点（不做第二遍精定位）—— M8 那个 38.1% 的基线",
            },
            "refine_vs_coarse": {
                "wins": self.refine_wins,
                "losses": self.refine_losses,
                "note": "crop 精定位相对粗定位：胜 = 粗定位没中而精定位中了；负 = 反过来",
            },
            "strata": [dict(item) for item in self.strata],
            "anti_masking_met": self.anti_masking_met,
            "coarse_shared": dict(self.coarse_shared),
        }


def evaluate_half(
    half: str,
    samples: Sequence[HoldoutSample],
    tolerance_px: float,
    recorded_tolerance_px: float,
    deviation_tolerance_px: float,
    recorded_deviation_px: float,
    *,
    tolerance_pp: float = SUBSET_TOLERANCE_PP,
) -> HalfResult:
    """**在给定参数下评估某一半**（结论按工单 M23 AC 3/AC 4 判）。

    * 硬判据：门控开 ≥ 门控关；
    * 噪声判据：**差值必须超过噪声量级**（否则判「不成立」）——
      噪声量级取「两臂粗定位点不同的比例」与「翻转比例」里**较大**的一个
      （保守：宁可低估效应，不许高估）。
    """
    score = score_on_demand(
        samples,
        tolerance_px,
        recorded_tolerance_px,
        deviation_tolerance_px,
        recorded_deviation_px,
    )
    always = score_always(samples, deviation_tolerance_px, recorded_deviation_px)
    always_hits = always.hits_lower
    always_accuracy = always.accuracy
    delta_pp = (score.accuracy - always_accuracy) * 100
    flips = sum(1 for sample in samples if sample.flip)
    coarse_differs = sum(1 for sample in samples if sample.coarse_differs)
    # 翻转的归因：两臂的粗定位点**相同**的翻转只能来自门控；
    # 粗定位点**不同**的翻转来自「一臂的模型调用失败 → 走了另一条路径」，
    # 那不是门控的功劳，是噪声（两臂压根不可比）。
    flips_gate = sum(1 for sample in samples if sample.flip and not sample.coarse_differs)
    flips_noise = sum(1 for sample in samples if sample.flip and sample.coarse_differs)
    total = len(samples)
    noise_flip_pp = (flips / total * 100) if total else 0.0
    noise_coarse_pp = (coarse_differs / total * 100) if total else 0.0
    noise_pp = max(noise_flip_pp, noise_coarse_pp)
    hard_ok = score.hits_lower >= always_hits
    noise_ok = delta_pp > noise_pp
    strata: list[dict[str, Any]] = []
    for stratum in sorted({sample.stratum for sample in samples}):
        members = [sample for sample in samples if sample.stratum == stratum]
        stratum_score = score_on_demand(
            members,
            tolerance_px,
            recorded_tolerance_px,
            deviation_tolerance_px,
            recorded_deviation_px,
        )
        stratum_always = score_always(members, deviation_tolerance_px, recorded_deviation_px)
        on_rate = stratum_score.accuracy
        off_rate = stratum_always.accuracy
        strata.append(
            {
                "stratum": stratum,
                "samples": len(members),
                "on_demand": on_rate,
                "always": off_rate,
                "delta_pp": (on_rate - off_rate) * 100,
                "within_tolerance": (on_rate - off_rate) * 100 >= -tolerance_pp,
            }
        )
    shared = sum(1 for sample in samples if not sample.coarse_differs)
    # -- 第三条臂：粗定位**直接**落点（不做第二遍精定位）--------------------
    # M8 那个 38.1% 的基线；以及「crop 精定位 胜/负」条数（M8 的 8 胜 0 负）。
    # ⚠️ 这两个数**不是**门控的效果，是「精定位值不值得做」的依据 ——
    # D-02（按需触发要不要退役）归 M29，它要的就是这个区分。
    coarse_only_hits = sum(1 for sample in samples if sample.always_coarse_hit is True)
    refine_wins = sum(
        1
        for sample in samples
        if sample.always_coarse_hit is False and sample.always_path.hit is True
    )
    refine_losses = sum(
        1
        for sample in samples
        if sample.always_coarse_hit is True and sample.always_path.hit is False
    )
    return HalfResult(
        half=half,
        samples=total,
        on_demand=score,
        always=always,
        always_hits=always_hits,
        always_accuracy=always_accuracy,
        delta_pp=delta_pp,
        delta_is_point=score.determined and always.determined,
        gate_off_rate=(1 - score.refine_upper / total if total else 0.0),
        flips=flips,
        coarse_differs=coarse_differs,
        noise_flip_pp=noise_flip_pp,
        noise_coarse_differs_pp=noise_coarse_pp,
        noise_magnitude_pp=noise_pp,
        flips_gate_attributable=flips_gate,
        flips_noise_attributable=flips_noise,
        hard_criterion_met=hard_ok,
        noise_criterion_met=noise_ok,
        criterion_met=hard_ok and noise_ok,
        conclusion="成立" if (hard_ok and noise_ok) else "不成立",
        coarse_only_hits=coarse_only_hits,
        coarse_only_accuracy=(coarse_only_hits / total if total else 0.0),
        refine_wins=refine_wins,
        refine_losses=refine_losses,
        strata=tuple(strata),
        anti_masking_met=all(item["within_tolerance"] for item in strata),
        coarse_shared={
            "samples": total,
            "identical": shared,
            "fraction": shared / total if total else 0.0,
            "note": "两臂的粗定位点完全相同的条数 —— 应当是 100%（唯一差异只能是门控）",
        },
    )


# ---------------------------------------------------------------------------
# 报告
# ---------------------------------------------------------------------------


def _area_stats(samples: Sequence[HoldoutSample]) -> dict[str, Any]:
    """**目标占比分布**（数据集 GT 框的统计，聚合量不是逐样本真值）。"""
    values = sorted(sample.area_ratio for sample in samples if sample.area_ratio > 0)
    if not values:
        return {"samples": 0}
    count = len(values)
    p90_index = min(count - 1, math.ceil(0.9 * count) - 1)
    return {
        "samples": count,
        "median_pct": values[count // 2] * 100,
        "p90_pct": values[p90_index] * 100,
        "mean_pct": sum(values) / count * 100,
        "share_le_default_threshold_pct": (
            sum(1 for value in values if value <= default_gate_params().area_ratio_threshold)
            / count
            * 100
        ),
        "default_threshold_pct": default_gate_params().area_ratio_threshold * 100,
        "note": "GT 框占图面积比（数据集统计；门控的占比条件本身仍判不了 —— 粗定位只给点不给框）",
    }


def half_samples(samples: Sequence[HoldoutSample], half: str) -> list[HoldoutSample]:
    """某一半的样本。"""
    return [sample for sample in samples if sample.split == half]


def build_report(
    run: RecordedRun,
    dataset_root: str | Path,
    split: Split,
    samples: Sequence[HoldoutSample],
    tuning: Tuning,
    result_a: HalfResult,
    result_b: HalfResult,
) -> dict[str, Any]:
    """**落盘用的报告**（工单 M23 AC 1~AC 6 的每一项都有落点）。"""
    return {
        "ticket": "M23",
        "purpose": "把 C-16 的门控判定改成**留出集**判定：阈值在 A 上调，结论只看 B",
        "input": {
            "recorded_run": str(run.path),
            "dataset": str(dataset_root),
            "recorded_tolerance_px": run.recorded_tolerance_px,
            "recorded_deviation_px": run.recorded_deviation_px,
            "recorded_model": dict(run.model),
            "recorded_dataset": dict(run.dataset),
            "note": (
                "**不重调模型**：两臂的粗定位/整屏第二遍/精定位回答是同一次"
                "（应答记忆端口），记录里已含两种结局各自的结果"
            ),
        },
        "split": {
            "rule": split.rule,
            "counts": {half: len(split.ids(half)) for half in (SPLIT_TUNING, SPLIT_EVAL)},
            "strata": {
                half: {
                    stratum: sum(
                        1
                        for sample in samples
                        if sample.split == half and sample.stratum == stratum
                    )
                    for stratum in sorted({sample.stratum for sample in samples})
                }
                for half in (SPLIT_TUNING, SPLIT_EVAL)
            },
            "target_area_ratio": {
                half: _area_stats(half_samples(samples, half))
                for half in (SPLIT_TUNING, SPLIT_EVAL)
            },
            "assignment": {sample.sample_id: sample.split for sample in samples},
        },
        "tuning": tuning.to_dict(),
        "result_a": result_a.to_dict(),
        "result_b": result_b.to_dict(),
        "conclusion": {
            "verdict": result_b.conclusion,
            "delta_pp_on_b": result_b.delta_pp,
            "noise_magnitude_pp_on_b": result_b.noise_magnitude_pp,
            "rule": (
                "B 上的差值是**结论**；A 上的差值只是**调参过程量**。"
                "判据：B 上门控开 ≥ 门控关，**且** B 上的差值 > 噪声量级；"
                "否则判「不成立」。"
            ),
            "delta_pp_on_a_is_tuning_process_only": True,
            "delta_pp_on_a": result_a.delta_pp,
            "gate_off_rate_on_b": result_b.gate_off_rate,
            "gate_off_rate_gt_zero_on_b": result_b.gate_off_rate > GATE_OFF_RATE_MIN,
        },
        "comparability": {
            "coarse_shared_a": result_a.coarse_shared,
            "coarse_shared_b": result_b.coarse_shared,
            "model_calls": dict(run.model_calls),
            "note": (
                "`coarse_shared` = 两臂粗定位点完全相同的条数；"
                "`model_calls.replayed` = 应答记忆端口复用了多少条回答"
                "（整轮的值；两臂共用同一次模型应答就是靠它）"
            ),
        },
        "replay_check": replay_check(
            samples, run.recorded_tolerance_px, run.recorded_deviation_px
        ),
        "samples": [sample.to_dict() for sample in samples],
    }


def render_report(report: Mapping[str, Any]) -> str:
    """把报告渲染成 markdown（人读的版本）。"""
    split = report["split"]
    tuning = report["tuning"]
    result_a = report["result_a"]
    result_b = report["result_b"]
    conclusion = report["conclusion"]
    lines: list[str] = []
    lines.append("# M23 · 门控结论按留出集重评")
    lines.append("")
    lines.append(
        f"**结论：B 上的差值 {conclusion['delta_pp_on_b']:+.1f} pp "
        f"vs 噪声量级 {conclusion['noise_magnitude_pp_on_b']:.1f} pp → "
        f"{conclusion['verdict']}**"
    )
    lines.append("")
    lines.append(
        f"- A（调参）{result_a['samples']} 条，B（评估）{result_b['samples']} 条"
    )
    lines.append(
        f"- A 上：门控开 {result_a['on_demand']['accuracy_lower_bound'] * 100:.1f}% ｜"
        f"门控关 {result_a['always_accuracy'] * 100:.1f}% ｜"
        f"差 **{result_a['delta_pp']:+.1f} pp**（**调参过程量**，不是结论）"
    )
    lines.append(
        f"- B 上：门控开 {result_b['on_demand']['accuracy_lower_bound'] * 100:.1f}% ｜"
        f"门控关 {result_b['always_accuracy'] * 100:.1f}% ｜"
        f"差 **{result_b['delta_pp']:+.1f} pp**（**这是结论**）"
    )
    lines.append(
        f"- B 上噪声量级：翻转 {result_b['noise']['flip_count']} 条"
        f"（{result_b['noise']['flip_pp']:.1f} pp；其中门控可归因 "
        f"{result_b['noise']['flips_gate_attributable']} 条、"
        f"噪声可归因 {result_b['noise']['flips_noise_attributable']} 条）｜"
        f"两臂粗定位点不同 {result_b['noise']['coarse_differs_count']} 条"
        f"（{result_b['noise']['coarse_differs_ratio_pp']:.1f} pp）"
    )
    lines.append(
        f"- B 上门控关闭率 {conclusion['gate_off_rate_on_b'] * 100:.1f}%"
        f"（> 0：{'是' if conclusion['gate_off_rate_gt_zero_on_b'] else '否'}）"
    )
    lines.append("")
    lines.append("## 切分")
    lines.append("")
    lines.append(f"规则：{split['rule']}")
    lines.append("")
    lines.append("| 半 | 条数 | 目标占比中位数 | 目标占比 p90 | ≤ 默认阈值的占比 |")
    lines.append("|---|---|---|---|---|")
    for half, label in ((SPLIT_TUNING, "A（调参）"), (SPLIT_EVAL, "B（评估）")):
        area = split["target_area_ratio"][half]
        lines.append(
            f"| {label} | {split['counts'][half]} | {area.get('median_pct', 0):.4f}% | "
            f"{area.get('p90_pct', 0):.4f}% | "
            f"{area.get('share_le_default_threshold_pct', 0):.1f}% |"
        )
    lines.append("")
    lines.append("## 调参（只在 A 上）")
    lines.append("")
    lines.append(f"规则：{tuning['rule']}")
    lines.append("")
    lines.append(
        f"**选中：一致性容差 {tuning['tolerance_px']:g} px ／ "
        f"偏离容差 {tuning['deviation_px']:g} px**"
    )
    lines.append("")
    lines.append(
        "一致性容差（在选中的偏离容差下扫）："
    )
    lines.append("")
    lines.append(
        "| 容差 (px) | A 的门控开命中率（下界） | A 的门控关 | 差 (pp) | 判不了的条数 |"
    )
    lines.append("|---|---|---|---|---|")
    for candidate in _candidates_at(tuning, deviation_px=tuning["deviation_px"]):
        lines.append(
            f"| {candidate['tolerance_px']:g} | "
            f"{candidate['on_demand']['accuracy_lower_bound'] * 100:.1f}% | "
            f"{candidate['always']['accuracy_lower_bound'] * 100:.1f}% | "
            f"{candidate['delta_pp_lower']:+.1f} | "
            f"{candidate['on_demand']['undetermined_samples']} |"
        )
    lines.append("")
    lines.append(
        "偏离容差（在选中的一致性容差下扫）："
    )
    lines.append("")
    lines.append(
        "| 偏离容差 (px) | A 的门控开命中率（下界） | A 的门控关 | 差 (pp) | 判不了的条数 |"
    )
    lines.append("|---|---|---|---|---|")
    for candidate in _candidates_at(tuning, tolerance_px=tuning["tolerance_px"]):
        lines.append(
            f"| {candidate['deviation_px']:g} | "
            f"{candidate['on_demand']['accuracy_lower_bound'] * 100:.1f}% | "
            f"{candidate['always']['accuracy_lower_bound'] * 100:.1f}% | "
            f"{candidate['delta_pp_lower']:+.1f} | "
            f"{candidate['on_demand']['undetermined_samples']} |"
        )
    lines.append("")
    lines.append("| 参数 | 取值 | 取值来源 |")
    lines.append("|---|---|---|")
    for key, source_key, label in (
        ("area_ratio_threshold", "area_ratio_source", "目标占比阈值"),
        ("confidence_threshold", "confidence_source", "置信度阈值"),
        ("consistency_tolerance_px", "consistency_source", "一致性容差"),
        ("deviation_tolerance_px", "deviation_source", "偏离容差"),
    ):
        lines.append(
            f"| {label} | {tuning['params'][key]:g} | {tuning['params'][source_key]} |"
        )
    lines.append("")
    lines.append("## 逐样本")
    lines.append("")
    lines.append(
        "| 样本 | 层 | 半 | 粗定位命中 | 门控关命中 | 记录命中 | "
        "一致性 | 精定位路径 | 偏离 (px) | 粗定位点 | 命中翻转 |"
    )
    lines.append("|---|---|---|---|---|---|---|---|---|---|---|")
    for sample in report["samples"]:
        deviation = sample["od_path"]["deviation"]
        lines.append(
            f"| {sample['sample_id']} | {sample['stratum']} | {sample['split']} | "
            f"{_mark(sample['coarse_hit'])} | {_mark(sample['always_coarse_hit'])} | "
            f"{_mark(sample['recorded_hit'])} | "
            f"{sample['consistency']} | {sample['od_path']['kind']} | "
            f"{'—' if deviation is None else f'{deviation:.1f}'} | "
            f"{'不同' if sample['coarse_differs'] else '相同'} | "
            f"{_mark(sample['flip'])} |"
        )
    lines.append("")
    return "\n".join(lines)


def _candidates_at(tuning: Mapping[str, Any], **match: float) -> list[dict[str, Any]]:
    """扫参表里符合给定参数的那一列（一维切片）。"""
    return [
        candidate
        for candidate in tuning["candidates"]
        if all(candidate[key] == value for key, value in match.items())
    ]


def _mark(value: Any) -> str:
    """表格里的勾叉（``None`` 记「—」）。"""
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "✓" if value else "✗"
    return str(value)


@dataclass(frozen=True)
class HoldoutEvaluation:
    """**留出集重评**的完整结果。"""

    run: RecordedRun
    dataset_root: str
    split: Split
    samples: tuple[HoldoutSample, ...]
    tuning: Tuning
    result_a: HalfResult
    result_b: HalfResult
    report: dict[str, Any]


def run_holdout(run: RecordedRun, dataset_root: str | Path) -> HoldoutEvaluation:
    """**跑一遍留出集重评**：切分 → 重建 → **只在 A 上调参** → 两半各自评估。

    ⚠️ **调参那一步只看 A**（:func:`tune_gate_params` 的入参是 A 半的样本），
    B 半在参数定下来之后才被评估 —— 这是工单 M23 的核心纪律。
    """
    strata: dict[str, list[str]] = {}
    for outcome in run.on_demand.values():
        strata.setdefault(f"{outcome.platform}/{outcome.ui_type}", []).append(outcome.sample_id)
    split = split_samples(strata)
    samples = reconstruct(run, dataset_root, split=split)
    samples_a = half_samples(samples, SPLIT_TUNING)
    samples_b = half_samples(samples, SPLIT_EVAL)

    tuning = tune_gate_params(samples_a, run.recorded_tolerance_px, run.recorded_deviation_px)
    result_a = evaluate_half(
        SPLIT_TUNING,
        samples_a,
        tuning.tolerance_px,
        run.recorded_tolerance_px,
        tuning.deviation_px,
        run.recorded_deviation_px,
    )
    result_b = evaluate_half(
        SPLIT_EVAL,
        samples_b,
        tuning.tolerance_px,
        run.recorded_tolerance_px,
        tuning.deviation_px,
        run.recorded_deviation_px,
    )
    report = build_report(run, dataset_root, split, samples, tuning, result_a, result_b)
    return HoldoutEvaluation(
        run=run,
        dataset_root=str(dataset_root),
        split=split,
        samples=tuple(samples),
        tuning=tuning,
        result_a=result_a,
        result_b=result_b,
        report=report,
    )


# ---------------------------------------------------------------------------
# 命令行
# ---------------------------------------------------------------------------


def main(argv=None) -> int:
    """``python -m minicode.eval.holdout`` —— 留出集重评（不重调模型）。"""
    parser = argparse.ArgumentParser(
        prog="minicode.eval.holdout",
        description="ScreenSpot-Pro 门控结论的留出集重评（工单 M23）",
    )
    parser.add_argument("--run", required=True, help="M8 那轮的 sspro-report.json")
    parser.add_argument("--dataset", required=True, help="ScreenSpot-Pro 数据集根目录")
    parser.add_argument("--out", required=True, help="报告落盘目录")
    args = parser.parse_args(argv)

    run = load_recorded_run(args.run)
    evaluation = run_holdout(run, args.dataset)
    samples = list(evaluation.samples)
    tuning = evaluation.tuning
    result_a = evaluation.result_a
    result_b = evaluation.result_b
    report = evaluation.report
    samples_a = half_samples(samples, SPLIT_TUNING)
    samples_b = half_samples(samples, SPLIT_EVAL)

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "holdout-report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (out_dir / "holdout-report.md").write_text(render_report(report) + "\n", encoding="utf-8")

    print(f"切分：A {len(samples_a)} 条 / B {len(samples_b)} 条")
    print(
        f"调参（只在 A 上）：一致性容差 {tuning.tolerance_px:g} px ／"
        f"偏离容差 {tuning.deviation_px:g} px"
    )
    print(
        f"A：门控开 {result_a.on_demand.accuracy * 100:.1f}% ｜"
        f"门控关 {result_a.always_accuracy * 100:.1f}% ｜差 {result_a.delta_pp:+.1f} pp"
        "（调参过程量）"
    )
    print(
        f"B：门控开 {result_b.on_demand.accuracy * 100:.1f}% ｜"
        f"门控关 {result_b.always_accuracy * 100:.1f}% ｜差 {result_b.delta_pp:+.1f} pp（结论）"
    )
    print(
        f"B 的噪声量级：翻转 {result_b.flips} 条（{result_b.noise_flip_pp:.1f} pp）｜"
        f"两臂粗定位点不同 {result_b.coarse_differs} 条"
        f"（{result_b.noise_coarse_differs_pp:.1f} pp）"
    )
    print(f"判定：**{result_b.conclusion}**")
    print(f"报告已写入：{out_dir / 'holdout-report.json'}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())

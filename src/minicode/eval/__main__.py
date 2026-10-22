"""命令行入口：**ScreenSpot-Pro 门控评测**（工单 M8）。

::

    $env:PYTHONPATH="src"; python -m minicode.eval --dataset .scratch/sspro --out runs/m8 --per-stratum 10

它做五件事：

1. **读数据集**（ScreenSpot-Pro 的标注 + 图片）→ 按 (平台 × 文本/图标)
   **分层抽样**（确定性的，同一个种子 → 同一批样本）；
2. **每个图像尺寸各标定一次**（真调模型）→ 判定标度与「模型实际看到的那张图」
   的尺寸（**量出来的，不是猜的**）；
3. 跑**三臂**（门控记录器 / 无条件两段式 / 只用粗定位基线），**同一条样本的三臂共用同一帧画面**；
4. 判**硬判据**（门控开 ≥ 门控关）与**防掩盖**（逐子集对比），
   出那张 **7 行表**；
5. 把结论写成一份 JSON 记录 + 一份 markdown 表格。

⚠️ **不碰真桌面**：数据来自数据集文件，不需要真机占用锁。
"""

from __future__ import annotations

import argparse
import json
import math
import platform
import sys
import threading
import time
from dataclasses import replace
from pathlib import Path
from typing import Any

#: **模型输入尺寸的口径**（工单 A-2）—— 生产（``e2e/visual.py``）与评测**共用一处**：
#: 「声明尺寸怎么算」+「token 数怎么反查服务端实际尺寸」。
#: ⚠️ 反查的实现住在 :mod:`minicode.calib.calibrate`（calib 包，唯一一处）；
#: 这里只调用它，**不抄第二份**。
from minicode.e2e.visual import (
    PromptCapturingCaller,
    RecordingSink,
    measure_text_tokens,
    reverse_lookup_model_input,
)
from minicode.ground.gate import default_gate_params
from minicode.model.client import ModelClient
from minicode.model.config import load_config
from minicode.model.effort import Effort
from minicode.model.image import model_input_size_for
from minicode.model.request_log import JsonlRequestLog

from .formats import SCREENSPOT_PRO, self_check_hit_rate
from .sspro import (
    GATE_INPUT_SOURCES,
    OFFICIAL_ROWS,
    ArmOutcome,
    MemoizingModelCaller,
    compare_arms,
    gate_params_snapshot,
    load_samples,
    model_input_target_sizes,
    near_miss_calibration,
    paired_comparison,
    render_table,
    run_arms,
    stratified_subset,
    table_rows,
)

__all__ = ["main"]


def _crop_label() -> str:
    """**实际用的裁剪几何的口径**（自适应 → 报口径，不报一个固定数）。"""
    from minicode.ground.channel import crop_geometry_label

    return crop_geometry_label()


def _parse_size(text: str) -> tuple[int, int]:
    parts = text.lower().replace("*", "x").split("x")
    if len(parts) != 2:
        raise ValueError(f"尺寸格式应为 WxH，收到 {text!r}")
    return (int(parts[0]), int(parts[1]))


def _reconfigure_output() -> None:
    """控制台按 UTF-8 输出（默认代码页是 GBK，中文与符号会变成乱码）。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):  # pragma: no cover - 老解释器没有这个方法
            pass


class _LockedSink:
    """**线程安全**的请求日志出口（评测按样本并行调用模型 → 落盘不能交错）。"""

    def __init__(self, inner):
        self._inner = inner
        self._lock = threading.Lock()

    def write(self, record) -> None:
        with self._lock:
            self._inner.write(record)


def main(argv=None) -> int:
    _reconfigure_output()
    parser = argparse.ArgumentParser(
        prog="minicode.eval",
        description="ScreenSpot-Pro 门控评测：门控开 vs 门控关（工单 M8）",
    )
    parser.add_argument(
        "--dataset", default=".scratch/sspro", help="数据集根目录（里面有 annotations/ 与 images/）"
    )
    parser.add_argument("--out", default="runs/m8", help="记录落盘目录")
    parser.add_argument(
        "--per-stratum",
        type=int,
        default=55,
        help=(
            "每个 (平台 × 文本/图标) 分层取几条（默认 55 → 共 330 条）"
            "—— 42 条的 Wilson 区间约 ±15 pp，分不开 38.1%% 与 57.1%%；"
            "方案改动 8：≥300 条分层抽样（固定 seed）"
        ),
    )
    parser.add_argument("--seed", type=int, default=20251020, help="抽样种子（决定性）")
    parser.add_argument(
        "--effort",
        choices=[effort.value for effort in Effort],
        default=Effort.LOW.value,
        help="思考预算档位（默认 low）",
    )
    parser.add_argument(
        "--max-tokens",
        type=int,
        default=8192,
        help=(
            "输出预算（默认 8192）—— 粗定位那一级**曾经**用 2000，"
            "被思考链吃光（「HTTP 200 但没有文本输出」丢样本）；"
            "4096 档残余无文本率 0.56%%（19/3404），"
            "8192 档按幂律外推 0.03~0.07%%（施工清单第 ② 条）"
        ),
    )
    parser.add_argument(
        "--consistency-tolerance",
        type=float,
        default=0.0,
        help=(
            "「两次粗定位一致性」容差（物理像素）的覆盖值；0 = 用门控自带值。"
            "⚠️ **门控降级为记录器之后，这个参数不再改变任何行为** —— "
            "「第二次独立粗定位」那次调用已被删掉，一致性这条**永远不可判定**；"
            "它只是被**记进报告的 ``gate.params``**（事后复盘用）"
        ),
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=4,
        help="按样本并行的线程数（只缩短墙钟时间，不改变任何判定）",
    )
    parser.add_argument(
        "--crop-width",
        type=int,
        default=0,
        help=(
            "裁剪窗口宽（物理像素）；0 = **自适应**"
            "（当前物理帧的 50%% 线性尺寸、绝对下限 512 —— 施工清单第 ⑤ 条）"
        ),
    )
    parser.add_argument(
        "--crop-height",
        type=int,
        default=0,
        help="裁剪窗口高（物理像素）；0 = 自适应（同上）",
    )
    parser.add_argument(
        "--memo-cache",
        default="",
        help=(
            "**应答记忆的落盘缓存**（施工清单第 ⑨ 条）：给了就把每次回答写进这个"
            " JSONL，下一轮运行**先读缓存** → 跨进程的两轮（例如「只换 crop 尺寸」）"
            "共用同一次粗定位回答 → 那一对是**真配对**"
        ),
    )
    parser.add_argument(
        "--limit", type=int, default=0, help="最多跑多少条样本（0 = 不限；调试用）"
    )
    parser.add_argument(
        "--full",
        action="store_true",
        help=(
            "跑**全量**样本（本地有图的那些），**不做**分层抽样"
            "（施工清单第 ⑨ 条）"
        ),
    )
    parser.add_argument(
        "--confirmation-split",
        action="store_true",
        help=(
            "把样本切成「**调参集 / 确认集**」两段（**不重叠**），"
            "报告里分别给 Δ / p / CI（施工清单第 ⑨ 条）。"
            "调参集 = 分层抽样的那一批；确认集 = 其余**本地有图**的样本。"
            "⚠️ **它要跑全量**（确认集不在抽样里）—— "
            "**本轮不要求跑全量**，跑不跑由调用方决定"
        ),
    )
    args = parser.parse_args(argv)

    started = time.time()
    dataset = Path(args.dataset)
    samples = load_samples(dataset)
    print(f"=== 数据集：{len(samples)} 条（{dataset}）===")
    #: **分层抽样**的那一批 = **调参集**（施工清单第 ⑨ 条）。
    tuning = stratified_subset(samples, args.per_stratum, seed=args.seed)
    local_ids = {sample.sample_id for sample in samples if sample.image_path.exists()}
    tuning = [sample for sample in tuning if sample.sample_id in local_ids]
    if args.full or args.confirmation_split:
        #: **全量** = 本地**有图**的全部样本（不做抽样）。
        chosen = [sample for sample in samples if sample.sample_id in local_ids]
    else:
        chosen = list(tuning)
    missing = len(samples) - len(chosen)
    if missing:
        print(f"⚠️ 有 {missing} 条样本的图片不在本地（数据集没下全），跳过它们")
    if args.limit:
        chosen = chosen[: args.limit]
    if args.full or args.confirmation_split:
        print(f"全量：{len(chosen)} 条（本地有图的全部样本，不做抽样）")
    else:
        print(f"分层抽样后：{len(chosen)} 条（种子 {args.seed}）")
    for key in sorted({(s.platform, s.ui_type) for s in chosen}):
        count = sum(1 for s in chosen if (s.platform, s.ui_type) == key)
        print(f"  {key[0]}/{key[1]}: {count} 条")

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    config = load_config()
    #: 请求日志：落盘 + **记住最后一条**（反查服务端实际尺寸要用）。
    sink = RecordingSink(_LockedSink(JsonlRequestLog(out_dir / "model-requests.jsonl")))
    #: 模型调用端口：**记住最后一次用的提示词**（反查时要逐字相同）。
    client = PromptCapturingCaller(ModelClient(config, sink))
    #: **应答记忆端口**（施工清单第 ⑨ 条）：记忆缓存**落盘**，
    #: 跨进程的两轮（例如「只换 crop 尺寸」）共用同一次上游回答 → 那一对**真配对**。
    memoized = MemoizingModelCaller(
        client, cache_path=args.memo_cache or None
    )

    # -- 标定：每个图像尺寸各标一次（真调模型）--------------------------
    sizes = sorted({sample.image_size for sample in chosen})
    scale_by_size: dict[tuple[int, int], Any] = {}
    model_input_by_size: dict[tuple[int, int], tuple[int, int]] = {}
    calibrations: list[dict[str, object]] = []
    for width, height in sizes:
        record = _calibrate(memoized, sink, config.model, width, height, args)
        scale_by_size[(width, height)] = record["scale"]
        #: **声明的模型输入尺寸** = 坐标换算的分母（两臂共用同一个）。
        model_input_by_size[(width, height)] = tuple(record["declared_model_input_size"])
        calibrations.append(record)

    # -- 跑两臂 ----------------------------------------------------------
    # ⚠️ **两臂共用同一次粗定位回答**（应答记忆端口）：模型端不确定，
    # 两臂各自重问会把随机性混进「唯一差异是门控」这个对比里。
    #: ⚠️ **不再注入 `second_opinion`**（用户 2026-10-21 拍板）：
    #: 门控降级为**记录器**，「第二次独立粗定位」那一次调用被删掉了 ——
    #: 实测它正是 −19.0 pp 的来源（触发条件恰好是精定位前提条件的反面）。
    #: 一致性那条条件从此**永远不可判定**，如实记进 `undecidable`。
    gate_params = default_gate_params()
    if args.consistency_tolerance:
        gate_params = replace(
            gate_params, consistency_tolerance_px=args.consistency_tolerance
        )
        print(
            f"门控参数覆盖：一致性容差 {args.consistency_tolerance} px"
            f"（自带值 {default_gate_params().consistency_tolerance_px} px）"
        )
    crop_size = (
        (args.crop_width, args.crop_height)
        if args.crop_width and args.crop_height
        else None
    )
    print(
        "=== 跑三臂（门控记录器 / 无条件两段式 / 只用粗定位），"
        f"N={len(chosen)}｜裁剪几何：{_crop_label()} ==="
    )
    on_demand, always, coarse_only = run_arms(
        chosen,
        memoized,
        scale_by_size=scale_by_size,
        model_input_by_size=model_input_by_size,
        effort=args.effort,
        max_tokens=args.max_tokens,
        progress=lambda line: print(line, flush=True),
        workers=args.workers,
        gate_params=gate_params,
        crop_size=crop_size,
    )

    comparison = compare_arms(on_demand, always, coarse_only=coarse_only)
    crop_label = _crop_label()
    #: **平均模型调用数/目标**（两条验收线判的都是这个数）。
    #:
    #: ⚠️ **口径只有这一处**：逐条累加**那一臂**的 ``model_calls`` ÷ 样本数。
    #: 报告里另有一个 ``model_calls.sent`` —— **那是另一个口径**
    #: （三臂合计、去掉应答记忆复用后真正发出去的请求数）→ **两个数不能互相换算**。
    sample_count = len(chosen)
    per_target = {
        mode: (
            comparison[mode]["model_calls"] / sample_count if sample_count else 0.0
        )
        for mode in ("on_demand", "always", "coarse_only")
        if mode in comparison
    }
    rows = table_rows(
        comparison=comparison,
        official=OFFICIAL_ROWS,
        model_id=config.model,
        crop_label=crop_label,
    )
    table = render_table(rows)

    # -- 防假象自检（GT 中心当预测点，命中率必须 ≈100%）-------------------
    self_check = self_check_hit_rate(
        [sample.as_eval_sample() for sample in chosen], SCREENSPOT_PRO
    )

    report = {
        "dataset": {
            "root": str(dataset),
            "source": "Hugging Face 数据集 likaixin/ScreenSpot-Pro（本次经 hf-mirror 镜像下载）",
            "license": "MIT（官方仓 LICENSE + 数据集卡片 cardData.license）",
            "total_samples": len(samples),
            "evaluated_samples": len(chosen),
            "seed": args.seed,
            "sampling": (
                "全量（本地有图的样本，不做抽样）"
                if args.full or args.confirmation_split
                else "按 (平台 × 文本/图标) 分层，每层固定条数（确定性）"
            ),
        },
        "model": {
            "id": config.model,
            "endpoint": config.base_url,
            "effort": args.effort,
            "max_tokens": args.max_tokens,
            #: **裁剪几何的口径**（自适应 → 报口径，不报一个固定数；
            #: 显式指定固定尺寸时报那一对像素）。
            "crop_geometry": crop_label,
            "crop_size": list(crop_size) if crop_size else None,
        },
        "calibration": [
            {**record, "scale": record["scale"].value} for record in calibrations
        ],
        "gate": {
            "params": gate_params_snapshot(gate_params),
            "input_sources": GATE_INPUT_SOURCES,
        },
        "self_check_hit_rate": self_check,
        #: **目标在「模型输入图」里的像素尺寸**（验收线 F 组看这个数）。
        "model_input_target_sizes": model_input_target_sizes(
            chosen, model_input_by_size
        ),
        "comparison": comparison,
        "model_calls": {
            "sent": memoized.calls,
            "replayed": memoized.replays,
            #: **阶段标签**（施工清单第 ⑨ 条）：每条回答属于哪一级
            #: （coarse / whole2nd / refine / calib）→ **事后**分辨哪一级被复用。
            #: ⚠️ **只是标签**：**不参与任何坐标换算**，也不改变任何判定。
            "stages": memoized.stage_report(),
            "note": (
                "应答记忆端口：两臂**共用同一次粗定位回答**"
                "（模型端不确定，各自重问会把随机性混进对比里）。"
                "⚠️ **这是另一个口径**：`sent` = 两臂合计、去掉复用后**真正发出去的请求数**"
                "（含门控关那一臂多出来的那几次精定位）；"
                "它**不是**「平均模型调用数/目标」—— 那个数看下面 "
                "`model_calls_per_target`"
            ),
        },
        #: **平均模型调用数/目标** —— 两条验收线**判的都是这一项**。
        "model_calls_per_target": {
            "samples": sample_count,
            **{mode: round(value, 4) for mode, value in per_target.items()},
            "note": (
                "两条验收线判的都是**这一项**：「平均模型调用数/目标 ≤ 2.05」（本轮方案：同批无条件两段式基线）与「≤ 1.5」（上一轮的线）"
                "（逐条累加的 `model_calls` ÷ 样本数，每一臂各一个数）。"
                "⚠️ 与上面 `model_calls.sent` **不是一个口径**，两者不能互相换算"
                "（`sent` = 三臂合计、去掉应答记忆复用后真正发出去的请求数）"
            ),
        },
        "official_rows": list(OFFICIAL_ROWS),
        "table_markdown": table,
        #: **近失带的离线校准**（验收线 B 组）—— 纯算术，**不发模型调用**。
        "near_miss_calibration": {
            arm: near_miss_calibration(outcomes)
            for arm, outcomes in (
                ("on_demand", on_demand),
                ("always", always),
                ("coarse_only", coarse_only),
            )
        },
        "outcomes": {
            "on_demand": [outcome.to_dict() for outcome in on_demand],
            "always": [outcome.to_dict() for outcome in always],
            "coarse_only": [outcome.to_dict() for outcome in coarse_only],
        },
        "environment": {
            "python": platform.python_version(),
            "platform": platform.platform(),
        },
        "elapsed_seconds": round(time.time() - started, 1),
    }
    if args.confirmation_split:
        #: **调参集 / 确认集**两段（**不重叠**）—— 施工清单第 ⑨ 条。
        report["confirmation_split"] = _confirmation_split_report(
            tuning_ids={sample.sample_id for sample in tuning},
            run_ids={sample.sample_id for sample in chosen},
            arms={
                "on_demand": on_demand,
                "always": always,
                "coarse_only": coarse_only,
            },
        )
    path = out_dir / "sspro-report.json"
    path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (out_dir / "sspro-table.md").write_text(table + "\n", encoding="utf-8")

    _print_summary(report, comparison)
    print(f"记录已写入：{path}")
    return 0


def _segment_report(
    sample_ids: set[str],
    arms: dict[str, list[ArmOutcome]],
    *,
    definition: str,
) -> dict[str, Any]:
    """一段样本（调参集 / 确认集）的 **N / 命中率 / Δ / p / 95% CI**。

    * ``arms`` —— 每一段里**每一臂**的 ``N``、命中条数、命中率；
    * ``paired`` —— 段内的**配对比较**（mid-p McNemar + 配对 bootstrap CI）。

    ⚠️ **口径**：段内仍然是**配对**（同一条样本的两臂不可拆开）；
    Δ = 右臂 − 左臂；CI 跨 0 就**不许**写「改进成立」。
    """
    subset = {
        arm: [outcome for outcome in outcomes if outcome.sample_id in sample_ids]
        for arm, outcomes in arms.items()
    }
    arm_stats: dict[str, Any] = {}
    for arm, items in sorted(subset.items()):
        hits = sum(1 for outcome in items if outcome.hit)
        arm_stats[arm] = {
            "samples": len(items),
            "hits": hits,
            "hit_rate": (hits / len(items)) if items else None,
        }
    paired: dict[str, Any] = {}
    for left, right in (("on_demand", "always"), ("always", "coarse_only")):
        if left not in subset or right not in subset or not subset[left]:
            continue
        paired[f"{left}_vs_{right}"] = paired_comparison(
            subset[left],
            subset[right],
            label_left=left,
            label_right=right,
            coarse_arm=subset.get("coarse_only", ()),
        )
    return {"definition": definition, "arms": arm_stats, "paired": paired}


def _confirmation_split_report(
    *,
    tuning_ids: set[str],
    run_ids: set[str],
    arms: dict[str, list[ArmOutcome]],
) -> dict[str, Any]:
    """**调参集 / 确认集**两段（**不重叠**）的统计（施工清单第 ⑨ 条）。

    * **调参集** = 分层抽样的那一批（阈值在那批上选）；
    * **确认集** = 本轮跑的样本里**其余**的那些（阈值不在它们身上选过）。

    ⚠️ **两段不许重叠** → ``overlap_samples`` 必须为 0（落进报告，可核对）。
    """
    confirmation_ids = run_ids - tuning_ids
    return {
        "overlap_samples": len(tuning_ids & confirmation_ids),
        "tuning": _segment_report(
            tuning_ids,
            arms,
            definition="分层抽样的那一批（--per-stratum / --seed 决定）",
        ),
        "confirmation": _segment_report(
            confirmation_ids,
            arms,
            definition="其余**本地有图**的样本（与调参集**不重叠**）",
        ),
    }


def _calibrate(
    client, sink, model_id: str, width: int, height: int, args
) -> dict[str, object]:
    """给一个图像尺寸跑一次标定（真调模型）。

    「模型实际看到的那张图」的尺寸**量出来**，**不猜**（工单 A-2）：

    1. **声明** —— 与生产**同一条口径**：
       :func:`minicode.model.image.model_input_size_for`（**声明尺寸只有一个来源**）。
       评测里每张图尺寸不同（ScreenSpot-Pro 的图有各种尺寸），
       所以**按每张图的尺寸算**，不是一个固定值。
    2. **反查** —— 用请求日志里的 **token 数**反查服务端实际看到的尺寸
       （28 px patch 规则，**【二手】未在本机端点验证**）：
       图像那部分的 token 数 = 服务端报的总输入 token − 纯文字那部分
       （纯文字那部分**量出来**：同一条提示词不带图再发一次）。
       **反查不出就如实记 ``unknown``，不许填一个猜的数。**
    """
    from minicode.calib.admission import CalibrationAdmission
    from minicode.calib.calibrate import CalibrationProbe
    from minicode.calib.probe import default_probe_spec
    from minicode.calib.tolerance import CALIBRATION_MIN_HIT_RATE

    #: **声明的模型输入尺寸**（按**这张图**的尺寸算）。
    declared = model_input_size_for(width, height)
    try:
        outcome = CalibrationProbe(
            client,
            spec=default_probe_spec(width, height),
            model_input_size=declared,
            call_path="image",
            model_id=model_id,
            effort=args.effort,
            max_tokens=args.max_tokens,
        ).run()
    except Exception as exc:
        # ⚠️ **标定失败不许让整轮评测炸掉**（模型侧失败是常态：
        # 「HTTP 200 但没有文本输出」= 思考链把预算吃光，实测 1.2% 的请求）。
        # 这里**如实记失败**（不编一个标度），让这一批样本**跳过视觉臂**，
        # 其余尺寸的标定照跑 —— **分母不许悄悄变小**（失败的尺寸会进报告）。
        print(f"标定 {width}×{height}：**失败**（{type(exc).__name__}: {exc}）")
        raise
    best = outcome.candidate(outcome.detected_scale)
    scale = outcome.detected_scale
    #: **准入判定**（A-1 的真闸门：等价族分歧 ≤ 4 px **且** K 轮命中率 ≥ 0.6）。
    admission = CalibrationAdmission(outcome).admit()
    #: **服务端实际看到的那张图**：盒子反查（**诊断**）+ 相等断言（**闸门**）。
    server = _server_scale_evidence(client, sink, declared, args)
    server_input = server["server_model_input_size"]
    kept = server["image_token_assertion"]["server_kept_size"]
    print(
        f"标定 {width}×{height}：标度 {scale.value}（判定 {outcome.scale_verdict}）"
        f"｜声明的模型输入 {declared[0]}×{declared[1]}"
        f"｜服务端实际 "
        f"{f'{server_input[0]}×{server_input[1]}' if server_input else 'unknown'}"
        f"｜图像 token 观测 {server['observed_image_tokens']}"
        f"｜期望 {server['expected_image_tokens']}"
        f"｜**服务端没再缩："
        f"{'是' if kept else ('否' if kept is False else 'unknown')}**"
        f"｜最大误差 {best.max_error_px:.2f} px"
        f"｜{outcome.rounds} 轮命中率 {outcome.hit_rate:.2f}"
        f"｜视觉通道准入 {'可用' if admission.available else '不可用'}"
    )
    # 逐目标的误差（**物理像素** = ‖落点 − 真值‖，与 D-0 口径一致）：
    # 用**判定标度换算出来的物理落点**（outcome 里已经算好），**不拿模型原始数值**
    # 去比「模型输入空间的像素」—— 那是**两个空间**：标度一旦不是绝对像素
    # （0~1 / 0~999 / 0~1000），比出来的就是一个**废数**。
    truth = {name: (x, y) for name, x, y in outcome.targets}
    point_errors = []
    for index, (name, _raw_x, _raw_y) in enumerate(outcome.raw_points):
        if name not in truth or index >= len(best.landing_points):
            continue
        px, py = best.landing_points[index]
        if not (math.isfinite(px) and math.isfinite(py)):
            # 越界的那个预测**没有物理落点** → 如实记 ``None``（不编一个数）。
            point_errors.append({"target": name, "error_px": None})
            continue
        point_errors.append(
            {
                "target": name,
                "error_px": round(
                    math.hypot(px - truth[name][0], py - truth[name][1]), 2
                ),
            }
        )
    return {
        "image_size": [width, height],
        "scale": scale,
        "scale_verdict": outcome.scale_verdict,
        "landing_equivalence": outcome.landing_equivalence,
        #: **声明尺寸**（我们自己缩到一半再送模型的那张图 —— 坐标换算的分母）。
        "declared_model_input_size": list(declared),
        #: **反查出来的服务端实际尺寸**（反查不出 → ``"unknown"``，不填猜的数）。
        #: ⚠️ 这是**诊断**（「服务端**大概**缩到了多大」，±28 px 的盒子）。
        "server_model_input_size": (
            list(server_input) if server_input is not None else "unknown"
        ),
        #: **相等断言**（**闸门**，与上面的**诊断**分开）：
        #: 观测到的图像 token 数 vs 期望值 ``ceil(W/28) × ceil(H/28)``。
        #: 相等（容差 ±2）⇒ 服务端**没有**再缩 ⇒ 该次请求的坐标系**可信**。
        "observed_image_tokens": server["observed_image_tokens"],
        "expected_image_tokens": server["expected_image_tokens"],
        "image_token_assertion": server["image_token_assertion"],
        "max_error_px": round(best.max_error_px, 3),
        #: **A-1 的真闸门**（准入门看的就是这两条）：
        #:
        #: * ``hit_rate`` —— **K 轮独立问的 96 px 方块命中率**；
        #: * ``visual_channel_available`` —— 准入门**放行 / 不放行**。
        #:
        #: ⚠️ 下面那个 ``calibration_ok`` **不是**闸门：它的语义是
        #: 「落点等价族判定成功」，而那个族**按 4 px 定义** → **按定义恒为真**
        #: （见 :mod:`minicode.calib.calibrate` 的说明）。它**不能**当成
        #: 「标定通过」的绿灯 —— 真正的绿灯是 ``visual_channel_available``。
        "rounds": outcome.rounds,
        "hit_rate": round(outcome.hit_rate, 4),
        "min_hit_rate": CALIBRATION_MIN_HIT_RATE,
        "median_error_px": round(outcome.median_error_px, 3),
        "p90_error_px": round(outcome.p90_error_px, 3),
        "equivalence_tolerance_px": outcome.equivalence_tolerance_px,
        "visual_channel_available": admission.available,
        "visual_channel_reason": admission.reason,
        "calibration_ok": outcome.calibration_ok,
        "point_errors_px": point_errors,
        "probe_digest": outcome.probe_digest,
        "probe_size": list(outcome.probe_size),
    }


def _server_scale_evidence(
    client, sink, declared: tuple[int, int], args
) -> dict[str, Any]:
    """服务端实际看到的那张图：**盒子反查**（诊断）+ **相等断言**（闸门）。

    * **盒子**（``±28 px``，:func:`minicode.calib.calibrate.diagnose_server_model_input_from_tokens`）
      = 「服务端**大概**缩到了多大」→ **诊断**；
    * **相等断言**（:func:`minicode.calib.calibrate.assert_server_kept_image_tokens`）
      = 「服务端**有没有**再缩」→ **闸门**：相等 ⇒ 该次请求的坐标系**可信**，
      不等 ⇒ **不可信**。

    ⚠️ **两件事分开**：盒子回答「大概多大」，断言回答「能不能信」——
    盒子的结果**不能**拿来当闸门。

    * 图像那部分的 token 数 = 服务端报的总输入 token − 纯文字那部分；
    * 纯文字那部分**量出来**：把**逐字相同**的提示词不带图再发一次
      （**与反查共用同一次测量** → **不额外发模型调用**）；
    * 量不出来 → 全部记 ``unknown``（**不许填一个猜的数**）。

    反查的实现只有一处：:func:`minicode.e2e.visual.reverse_lookup_model_input`
    （它转调 :mod:`minicode.calib.calibrate` 里那一份）。
    """
    from minicode.calib.calibrate import (
        assert_server_kept_image_tokens,
        expected_image_tokens,
        observed_image_tokens,
    )

    expected = expected_image_tokens(declared)
    unknown: dict[str, Any] = {
        "server_model_input_size": None,
        "observed_image_tokens": None,
        "expected_image_tokens": expected,
        "image_token_assertion": assert_server_kept_image_tokens(
            None, declared
        ).to_dict(),
    }
    #: ⚠️ **先取这条请求日志**，再量纯文字 ——
    #: 量纯文字会再发一次请求，把 ``sink.last`` 覆盖掉。
    record = getattr(sink, "last", None)
    prompt = getattr(client, "last_prompt", "")
    if record is None or not prompt:
        return unknown
    observed_total = getattr(record, "total_input_tokens", None)
    if observed_total is None:
        return unknown
    text_tokens = measure_text_tokens(
        client, prompt, effort=record.effort, max_tokens=args.max_tokens
    )
    if text_tokens is None:
        return unknown
    observed = observed_image_tokens(observed_total, text_tokens)
    assertion = assert_server_kept_image_tokens(observed, declared)
    size = reverse_lookup_model_input(
        observed_total,
        text_tokens,
        declared[0] / declared[1] if declared[1] else 0.0,
    )
    return {
        "server_model_input_size": size,
        "observed_image_tokens": observed,
        "expected_image_tokens": expected,
        "image_token_assertion": assertion.to_dict(),
    }


def _paired_line(item: dict) -> str:
    """配对比较那一行（Δ / CI / **三个 p** / **MDE**）。

    ⚠️ **主报是 mid-p**；普通精确二项与 χ²+连续性校正是**保守对照**
    （施工清单第 ⑨ 条）。
    ⚠️ **MDE** = 这个样本量下能判出的最小效应（百分点）——
    「|Δ| ≥ MDE 即够」；达不到是**样本量不够**，不是「没效果」。
    """
    mde_pp = item.get("mde_pp")
    mde_text = f"{mde_pp:.2f} pp" if mde_pp is not None else "unknown"
    return (
        f"Δ {item['delta_pp']:+.2f} pp"
        f"｜95% CI [{item['delta_ci95_pp'][0]:+.2f}, {item['delta_ci95_pp'][1]:+.2f}] pp"
        f"｜mid-p p={item['mcnemar_midp_p']:.4f}"
        f"｜对照：精确二项 p={item['mcnemar_exact_p']:.4f}"
        f"｜χ²+CC p={item['mcnemar_chisq_cc_p']:.4f}"
        f"｜不一致对占比 {item['discordance'] * 100:.1f}%"
        f"｜MDE {mde_text}"
        f"｜Δ ≥ MDE：{'是' if item['delta_at_or_above_mde'] else '否'}"
        f"｜CI 不跨 0：{'是' if item['ci_excludes_zero'] else '否'}"
    )


def _print_summary(report: dict, comparison: dict) -> None:
    overall = comparison["overall"]
    print("=== 硬判据（门控记录器 ≥ 无条件两段式）===")
    print(
        f"门控记录器 {overall['on_demand'] * 100:.1f}%"
        f" ｜无条件两段式 {overall['always'] * 100:.1f}%"
        f" ｜差 {overall['delta_pp']:+.1f} pp"
        f" ｜{'成立' if overall['criterion_met'] else '不成立'}"
    )
    if "coarse_only" in comparison:
        print(
            f"只用粗定位（基线，1.00 次/目标）："
            f"{comparison['coarse_only']['accuracy'] * 100:.1f}%"
        )
    landing_same = comparison.get("landing_same")
    if landing_same:
        print(
            f"=== 门控降级体检：记录器与无条件两段式的**最终落点**逐条相同的比例 "
            f"{landing_same['fraction'] * 100:.1f}%（应当 100%）==="
        )
    print("=== 防掩盖（逐子集，任何单一子集不得比门控关低 1 pp 以上）===")
    for item in comparison["strata"]:
        print(
            f"  {item['stratum']:<18} 记录器 {item['on_demand'] * 100:5.1f}%"
            f" ｜两段式 {item['always'] * 100:5.1f}% ｜差 {item['delta_pp']:+.1f} pp"
            f" {'✓' if item['within_tolerance'] else '✗'}"
        )
    print(f"防掩盖：{'通过' if comparison['anti_masking_met'] else '未通过'}")
    gate = comparison["on_demand"]["gate"]
    print(
        f"=== 门控埋点（与分数同一批样本 N={gate['samples']}；"
        f"**门控已降级为记录器**，不再决定开不开）==="
    )
    print(
        f"记录器这一臂：开启精定位 {gate['refine_enabled']} 次"
        f"｜门控判中 {gate['gate_hits']} 次"
        f"｜**没做精定位的比例** {1 - gate['hit_rate']:.1%}"
        f"（⚠️ 门控不再决定开不开 → 这一列只是记录，"
        f"**不是**「门控关掉的比例」）"
    )
    print(f"命中原因：{gate['reason_distribution']}")
    print(f"判不了的条件：{gate['undecidable_distribution']}")
    print(
        f"模型调用次数：记录器 {comparison['on_demand']['model_calls']} 次"
        f"｜两段式 {comparison['always']['model_calls']} 次"
        + (
            f"｜只用粗定位 {comparison['coarse_only']['model_calls']} 次"
            if "coarse_only" in comparison
            else ""
        )
    )
    per_target = report["model_calls_per_target"]
    print(
        "平均模型调用数/目标（验收线「≤ 2.05」/「≤ 1.5」判的都是这个数）："
        + "｜".join(
            f"{name} {per_target[name]:.2f}"
            for name in ("on_demand", "always", "coarse_only")
            if name in per_target
        )
        + f"（N={per_target['samples']}）"
    )
    print(f"防假象自检（GT 中心当预测点）：{report['self_check_hit_rate'] * 100:.1f}%")
    paired = comparison.get("paired") or {}
    if paired:
        print("=== 配对比较（mid-p McNemar + 配对 bootstrap 95% CI）===")
        for key in ("on_demand_vs_always", "always_vs_coarse_only"):
            item = paired.get(key)
            if not item:
                continue
            print(
                f"  {item['left']} → {item['right']}（n={item['samples']}）"
                f"：{_paired_line(item)}"
            )
    print("=== 那张 7 行表 ===")
    print(report["table_markdown"])
    _print_near_miss(report)
    _print_confirmation_split(report)


def _print_near_miss(report: dict) -> None:
    """**近失带的离线校准**（验收线 B 组）—— 纯算术，不发模型调用。"""
    calibration = (report.get("near_miss_calibration") or {}).get("always")
    if not calibration:
        return
    print("=== 近失带的离线校准（验收线 B 组；纯算术，不发模型调用）===")
    distance = calibration["dist_to_bbox"]
    subsets = calibration["p50_by_subset"]
    print(
        f"dist_to_bbox：p50 {distance['p50']}｜p90 {distance['p90']}"
        f"｜「找对」p50 {subsets['hit']}｜「挑错」p50 {subsets['miss']}"
    )
    for item in calibration["taus"]:
        print(
            f"  τ={item['tau_px']:>5} px：判失败 {item['hits_failing_band']} 条"
            f"｜捞回来 {item['misses_rescued']} 条"
        )
    band = calibration["band_36px"]
    print(
        f"  36 px 近失带：判失败 {band['hits_failing_band']} 条"
        f"｜捞回来 {band['misses_rescued']} 条"
    )


def _print_confirmation_split(report: dict) -> None:
    """**调参集 / 确认集**两段的 Δ / p / CI。"""
    split = report.get("confirmation_split")
    if not split:
        return
    print(
        "=== 调参集 / 确认集（两段不重叠；重叠条数 "
        f"{split['overlap_samples']}）==="
    )
    for name, label in (("tuning", "调参集"), ("confirmation", "确认集")):
        segment = split[name]
        arms = segment["arms"]
        rates = "｜".join(
            f"{arm} {item['hit_rate'] * 100:.1f}%（{item['hits']}/{item['samples']}）"
            for arm, item in arms.items()
            if item["hit_rate"] is not None
        )
        print(f"  {label}：{rates}")
        for key, item in segment["paired"].items():
            print(f"    {key}（n={item['samples']}）：{_paired_line(item)}")


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())

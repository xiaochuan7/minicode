"""命令行入口：**ScreenSpot-Pro 门控评测**（工单 M8）。

::

    $env:PYTHONPATH="src"; python -m minicode.eval --dataset .scratch/sspro --out runs/m8 --per-stratum 10

它做五件事：

1. **读数据集**（ScreenSpot-Pro 的标注 + 图片）→ 按 (平台 × 文本/图标)
   **分层抽样**（确定性的，同一个种子 → 同一批样本）；
2. **每个图像尺寸各标定一次**（真调模型）→ 判定标度与「模型实际看到的那张图」
   的尺寸（**量出来的，不是猜的**）；
3. 跑**两臂**（门控开 / 门控关），**同一条样本的两臂共用同一帧画面**；
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
    reverse_lookup_from_log,
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
    MemoizingModelCaller,
    compare_arms,
    gate_params_snapshot,
    load_samples,
    make_second_opinion,
    render_table,
    run_arms,
    stratified_subset,
    table_rows,
)

__all__ = ["main"]


def _crop_size() -> tuple[int, int]:
    """**实际用的裁剪窗口尺寸**（取冻结原型那份默认值，不另写常量）。"""
    from minicode.ground.channel import default_crop_size

    return default_crop_size()


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
        default=10,
        help="每个 (平台 × 文本/图标) 分层取几条（默认 10）",
    )
    parser.add_argument("--seed", type=int, default=20251020, help="抽样种子（决定性）")
    parser.add_argument(
        "--effort",
        choices=[effort.value for effort in Effort],
        default=Effort.LOW.value,
        help="思考预算档位（默认 low）",
    )
    parser.add_argument("--max-tokens", type=int, default=2000)
    parser.add_argument(
        "--consistency-tolerance",
        type=float,
        default=0.0,
        help=(
            "「两次粗定位一致性」容差（物理像素）的覆盖值；0 = 用门控自带值。"
            "**调它就是调门控**（工单 M8：硬判据不满足时调门控，不改判据）"
        ),
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=4,
        help="按样本并行的线程数（只缩短墙钟时间，不改变任何判定）",
    )
    parser.add_argument(
        "--limit", type=int, default=0, help="最多跑多少条样本（0 = 不限；调试用）"
    )
    args = parser.parse_args(argv)

    started = time.time()
    dataset = Path(args.dataset)
    samples = load_samples(dataset)
    print(f"=== 数据集：{len(samples)} 条（{dataset}）===")
    chosen = stratified_subset(samples, args.per_stratum, seed=args.seed)
    missing = [sample for sample in chosen if not sample.image_path.exists()]
    if missing:
        print(f"⚠️ 有 {len(missing)} 条样本的图片不在本地（数据集没下全），跳过它们")
        chosen = [sample for sample in chosen if sample.image_path.exists()]
    if args.limit:
        chosen = chosen[: args.limit]
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

    # -- 标定：每个图像尺寸各标一次（真调模型）--------------------------
    sizes = sorted({sample.image_size for sample in chosen})
    scale_by_size: dict[tuple[int, int], Any] = {}
    model_input_by_size: dict[tuple[int, int], tuple[int, int]] = {}
    calibrations: list[dict[str, object]] = []
    for width, height in sizes:
        record = _calibrate(client, sink, config.model, width, height, args)
        scale_by_size[(width, height)] = record["scale"]
        #: **声明的模型输入尺寸** = 坐标换算的分母（两臂共用同一个）。
        model_input_by_size[(width, height)] = tuple(record["declared_model_input_size"])
        calibrations.append(record)

    # -- 跑两臂 ----------------------------------------------------------
    # ⚠️ **两臂共用同一次粗定位回答**（应答记忆端口）：模型端不确定，
    # 两臂各自重问会把随机性混进「唯一差异是门控」这个对比里。
    memoized = MemoizingModelCaller(client)
    second_opinion = make_second_opinion(
        memoized, effort=args.effort, max_tokens=args.max_tokens
    )
    gate_params = default_gate_params()
    if args.consistency_tolerance:
        gate_params = replace(
            gate_params, consistency_tolerance_px=args.consistency_tolerance
        )
        print(
            f"门控参数覆盖：一致性容差 {args.consistency_tolerance} px"
            f"（自带值 {default_gate_params().consistency_tolerance_px} px）"
        )
    print(f"=== 跑两臂（门控开 / 门控关），N={len(chosen)} ===")
    on_demand, always = run_arms(
        chosen,
        memoized,
        scale_by_size=scale_by_size,
        model_input_by_size=model_input_by_size,
        effort=args.effort,
        max_tokens=args.max_tokens,
        second_opinion=second_opinion,
        progress=lambda line: print(line, flush=True),
        workers=args.workers,
        gate_params=gate_params,
    )

    comparison = compare_arms(on_demand, always)
    crop_size = _crop_size()
    #: **平均模型调用数/目标**（验收线「≤ 1.5」判的就是这个数）。
    #:
    #: ⚠️ **口径只有这一处**：逐条累加**那一臂**的 ``model_calls`` ÷ 样本数。
    #: 报告里另有一个 ``model_calls.sent`` —— **那是另一个口径**
    #: （两臂合计、去掉应答记忆复用后真正发出去的请求数）→ **两个数不能互相换算**。
    samples = len(chosen)
    per_target = {
        "on_demand": (
            comparison["on_demand"]["model_calls"] / samples if samples else 0.0
        ),
        "always": (
            comparison["always"]["model_calls"] / samples if samples else 0.0
        ),
    }
    rows = table_rows(
        comparison=comparison,
        official=OFFICIAL_ROWS,
        model_id=config.model,
        crop_size=crop_size,
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
            "sampling": "按 (平台 × 文本/图标) 分层，每层固定条数（确定性）",
        },
        "model": {
            "id": config.model,
            "endpoint": config.base_url,
            "effort": args.effort,
            "max_tokens": args.max_tokens,
            "crop_size": list(crop_size),
        },
        "calibration": [
            {**record, "scale": record["scale"].value} for record in calibrations
        ],
        "gate": {
            "params": gate_params_snapshot(gate_params),
            "input_sources": GATE_INPUT_SOURCES,
        },
        "self_check_hit_rate": self_check,
        "comparison": comparison,
        "model_calls": {
            "sent": memoized.calls,
            "replayed": memoized.replays,
            "note": (
                "应答记忆端口：两臂**共用同一次粗定位回答**"
                "（模型端不确定，各自重问会把随机性混进对比里）。"
                "⚠️ **这是另一个口径**：`sent` = 两臂合计、去掉复用后**真正发出去的请求数**"
                "（含门控关那一臂多出来的那几次精定位）；"
                "它**不是**「平均模型调用数/目标」—— 那个数看下面 "
                "`model_calls_per_target`"
            ),
        },
        #: **平均模型调用数/目标** —— 验收线「≤ 1.5」**判的就是这一项**。
        "model_calls_per_target": {
            "samples": samples,
            "on_demand": round(per_target["on_demand"], 4),
            "always": round(per_target["always"], 4),
            "note": (
                "验收线「平均模型调用数/目标 ≤ 1.5」判的是**这一项**"
                "（门控开那一臂：逐条累加的 `model_calls` ÷ 样本数）。"
                "⚠️ 与上面 `model_calls.sent` **不是一个口径**，两者不能互相换算"
            ),
        },
        "official_rows": list(OFFICIAL_ROWS),
        "table_markdown": table,
        "outcomes": {
            "on_demand": [outcome.to_dict() for outcome in on_demand],
            "always": [outcome.to_dict() for outcome in always],
        },
        "environment": {
            "python": platform.python_version(),
            "platform": platform.platform(),
        },
        "elapsed_seconds": round(time.time() - started, 1),
    }
    path = out_dir / "sspro-report.json"
    path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (out_dir / "sspro-table.md").write_text(table + "\n", encoding="utf-8")

    _print_summary(report, comparison)
    print(f"记录已写入：{path}")
    return 0


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
    outcome = CalibrationProbe(
        client,
        spec=default_probe_spec(width, height),
        model_input_size=declared,
        call_path="image",
        model_id=model_id,
        effort=args.effort,
        max_tokens=args.max_tokens,
    ).run()
    best = outcome.candidate(outcome.detected_scale)
    scale = outcome.detected_scale
    #: **准入判定**（A-1 的真闸门：等价族分歧 ≤ 4 px **且** K 轮命中率 ≥ 0.6）。
    admission = CalibrationAdmission(outcome).admit()
    #: **反查服务端实际看到的那张图**（反查不出 → ``None`` → 报告里写 unknown）。
    server_input = _reverse_lookup(client, sink, declared, args)
    print(
        f"标定 {width}×{height}：标度 {scale.value}（判定 {outcome.scale_verdict}）"
        f"｜声明的模型输入 {declared[0]}×{declared[1]}"
        f"｜服务端实际 "
        f"{f'{server_input[0]}×{server_input[1]}' if server_input else 'unknown'}"
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
        "server_model_input_size": (
            list(server_input) if server_input is not None else "unknown"
        ),
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


def _reverse_lookup(client, sink, declared: tuple[int, int], args) -> tuple[int, int] | None:
    """**用 token 数反查**服务端实际看到的那张图（反查不出 → ``None``）。

    * 图像那部分的 token 数 = 服务端报的总输入 token − 纯文字那部分；
    * 纯文字那部分**量出来**：把**逐字相同**的提示词不带图再发一次；
    * 反查不出（token 数对不上任何尺寸 / 文字那部分量不出来）→ ``None``
      —— 调用方记 ``unknown``，**不许填一个猜的数**。

    实现只有一处：:func:`minicode.e2e.visual.reverse_lookup_from_log`
    （反查口径不许有第二份）。
    """
    return reverse_lookup_from_log(
        client, sink, declared, max_tokens=args.max_tokens
    )


def _print_summary(report: dict, comparison: dict) -> None:
    overall = comparison["overall"]
    print("=== 硬判据（门控开 ≥ 门控关）===")
    print(
        f"门控开 {overall['on_demand'] * 100:.1f}% ｜门控关 {overall['always'] * 100:.1f}%"
        f" ｜差 {overall['delta_pp']:+.1f} pp"
        f" ｜{'成立' if overall['criterion_met'] else '不成立'}"
    )
    print("=== 防掩盖（逐子集，任何单一子集不得比门控关低 1 pp 以上）===")
    for item in comparison["strata"]:
        print(
            f"  {item['stratum']:<18} 开 {item['on_demand'] * 100:5.1f}%"
            f" ｜关 {item['always'] * 100:5.1f}% ｜差 {item['delta_pp']:+.1f} pp"
            f" {'✓' if item['within_tolerance'] else '✗'}"
        )
    print(f"防掩盖：{'通过' if comparison['anti_masking_met'] else '未通过'}")
    gate = comparison["on_demand"]["gate"]
    print(
        f"=== 门控埋点（与分数同一批样本 N={gate['samples']}）==="
    )
    print(
        f"门控开这一臂：开启精定位 {gate['refine_enabled']} 次"
        f"｜门控判中 {gate['gate_hits']} 次"
        f"｜**门控关闭率** {1 - gate['hit_rate']:.1%}"
    )
    print(f"命中原因：{gate['reason_distribution']}")
    print(f"判不了的条件：{gate['undecidable_distribution']}")
    print(
        f"模型调用次数：门控开 {comparison['on_demand']['model_calls']} 次"
        f"｜门控关 {comparison['always']['model_calls']} 次"
    )
    print(
        f"平均模型调用数/目标（验收线「≤ 1.5」判的是这个数）："
        f"门控开 {report['model_calls_per_target']['on_demand']:.2f}"
        f"｜门控关 {report['model_calls_per_target']['always']:.2f}"
        f"（N={report['model_calls_per_target']['samples']}）"
    )
    print(f"防假象自检（GT 中心当预测点）：{report['self_check_hit_rate'] * 100:.1f}%")
    print("=== 那张 7 行表 ===")
    print(report["table_markdown"])


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())

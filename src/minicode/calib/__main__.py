"""命令行入口：跑一次标定探针（真调模型）。

    $env:PYTHONPATH="src"; python -m minicode.calib --out runs

**两条调用路径各标定一次**（工单 M5 AC 9）：图像调用（发探针图）与
文本调用（只发文字描述），两条路径的**标度**各自独立、互不借用。

**每次标定问 K 轮**（K = 21，``--rounds`` 可改）：每轮是**另一次问**
（提示词随轮次轮换 —— 逐字重发在确定性模型下会拿到同一答案）。
``--session`` 让不同次独立标定会话的**轮换起点不同** → 6 次会话**相互独立**。

探针尺寸 = **本机物理表面尺寸**（DPI 感知后的度量，不是写死的值）；
**送模型前**缩到声明尺寸（:func:`minicode.model.image.model_input_size_for`
—— **声明尺寸只有这一个来源**；我们**自己**缩 → 声明即服务端看到的大小），
提示词里写明那张图的尺寸；
凭据只从仓库 ``.env`` 读（:mod:`minicode.model.config`）。

**不碰真机**：只发 API 请求 + 量一下屏幕尺寸，不截图不点击。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from minicode.model.client import ModelClient
from minicode.model.config import load_config
from minicode.model.effort import Effort
from minicode.model.image import model_input_size_for
from minicode.model.request_log import JsonlRequestLog

from .admission import CalibrationAdmission
from .calibrate import CalibrationProbe
from .probe import default_probe_spec
from .record import CalibrationLog, CalibrationRecord, session_verdicts
from .tolerance import CALIBRATION_MIN_HIT_RATE, CALIBRATION_ROUNDS

__all__ = ["main", "physical_surface_size"]


def physical_surface_size() -> tuple[int, int]:
    """本机物理表面尺寸（物理像素）。

    DPI 感知在导入冻结原型的 ``dpi`` 模块时**即生效**（PMv2），
    所以这里量到的是物理像素，不是逻辑像素。
    """
    from minicode.exec import dpi

    return dpi.physical_screen_size()


def _describe(outcome, admission) -> str:
    family_divergence = max(
        (value for scale, value in outcome.divergences_px.items() if scale in outcome.equivalent_scales),
        default=0.0,
    )
    lines = [
        f"判定标度：{outcome.detected_scale.value}",
        f"标度判定：{outcome.scale_verdict}｜落点等价：{outcome.landing_equivalence}"
        f"｜落点等价判定：{outcome.scale_equivalence_verdict}",
        f"标定容差：{outcome.calibration_tolerance_px} px｜越界容差："
        f"{outcome.out_of_bounds_tolerance_px} px｜落点等价容差："
        f"{outcome.equivalence_tolerance_px} px",
        f"标定成功（落点等价族判定成功）：{outcome.calibration_ok}",
        f"轮数：{outcome.rounds}｜命中率：{outcome.hit_rate:.2f}"
        f"（{outcome.rounds} 轮 × {len(outcome.targets)} 个目标）",
        f"落点误差：中位数 {outcome.median_error_px:.2f} px｜p90 {outcome.p90_error_px:.2f} px",
        f"等价族最大分歧：{family_divergence:.2f} px",
        f"两个带分母的相对标度（0~999 / 0~1000）之间的分歧（探针几何常量）："
        f"{outcome.denominator_pair_divergence_px:.2f} px"
        f"（落点等价容差 {outcome.equivalence_tolerance_px:.1f} px 的基线）",
        f"等价标度列表：{'、'.join(s.value for s in outcome.equivalent_scales) or '（无）'}",
        f"视觉通道准入：{'可用' if admission.available else '不可用'} —— {admission.reason}",
        "各候选标度：",
    ]
    for candidate in outcome.candidates:
        lines.append(
            f"  {candidate.scale.value:<22} max_err={candidate.max_error_px:12.2f} px"
            f"｜中位 {candidate.median_error_px:8.2f} px"
            f"｜p90 {candidate.p90_error_px:8.2f} px"
            f"{'（越界）' if candidate.out_of_bounds else ''}"
        )
    return "\n".join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="minicode.calib",
        description="运行时标定探针：判定模型输出的量纲（真调模型）",
    )
    parser.add_argument("--out", default="runs", help="标定记录落盘目录")
    parser.add_argument(
        "--path",
        choices=("image", "text", "both"),
        default="both",
        help="标定哪条调用路径（默认两条都跑）",
    )
    parser.add_argument(
        "--effort",
        choices=[e.value for e in Effort],
        default=Effort.LOW.value,
        help="思考预算档位（默认 low）",
    )
    parser.add_argument("--max-tokens", type=int, default=2000)
    parser.add_argument(
        "--rounds",
        type=int,
        default=CALIBRATION_ROUNDS,
        help=f"一次标定问几轮（K 轮独立问，默认 {CALIBRATION_ROUNDS}）",
    )
    parser.add_argument(
        "--min-hit-rate",
        type=float,
        default=CALIBRATION_MIN_HIT_RATE,
        help=(
            "命中率下限：K 轮命中率低于它 → 视觉通道不可用"
            f"（默认 {CALIBRATION_MIN_HIT_RATE}）"
        ),
    )
    parser.add_argument(
        "--session",
        type=int,
        default=0,
        help=(
            "会话号：让不同次独立标定会话的提示词轮换起点不同"
            "（6 次会话因此相互独立，不是逐字重发同一份请求）"
        ),
    )
    parser.add_argument(
        "--model-input",
        default=None,
        help=(
            "声明「模型实际看到的那张图」的尺寸（格式 WxH）。"
            "默认 = :func:`minicode.model.image.model_input_size_for` 给的那一档"
            "（我们**自己**缩到那个尺寸再送，声明即服务端看到的大小）。"
        ),
    )
    args = parser.parse_args(argv)

    config = load_config()
    client = ModelClient(
        config, JsonlRequestLog(Path(args.out) / "model-requests.jsonl")
    )
    width, height = physical_surface_size()
    spec = default_probe_spec(width, height)
    model_input = model_input_size_for(width, height)
    if args.model_input:
        parts = args.model_input.lower().replace("*", "x").split("x")
        if len(parts) != 2:
            parser.error("--model-input 格式应为 WxH")
        model_input = (int(parts[0]), int(parts[1]))
    paths = ("image", "text") if args.path == "both" else (args.path,)

    log = CalibrationLog(args.out)
    failures = 0
    run_records: list[CalibrationRecord] = []
    for path in paths:
        probe = CalibrationProbe(
            client,
            spec=spec,
            model_input_size=model_input,
            call_path=path,
            model_id=config.model,
            effort=args.effort,
            max_tokens=args.max_tokens,
            rounds=args.rounds,
            session=args.session,
        )
        print(
            f"=== 标定（{path} 路径）｜探针 {width}×{height}"
            f"｜送模型 {model_input[0]}×{model_input[1]}"
            f"｜{args.rounds} 轮｜会话 {args.session} ==="
        )
        try:
            outcome = probe.run()
        except Exception as exc:  # 模型侧失败如实报出，不静默
            failures += 1
            print(f"标定失败：{type(exc).__name__}: {exc}")
            # ⚠️ **失败的会话也要留一条记录** —— 验收线
            # 「6 次会话中 ≥ 5 次可用」的**分母**是「跑了几次会话」；
            # 失败的不留痕 → 分母**静默变小**（跑 6 次炸 2 次 → 日志里只剩
            # 4 条 → 读成「4/4 = 100% 达成」，真相是 4/6 = 67% → **不达成**）。
            failed = CalibrationRecord.failed(
                error=f"{type(exc).__name__}: {exc}",
                probe_size=(width, height),
                model_input_size=model_input,
                model_id=config.model,
                call_path=path,
                rounds=args.rounds,
            )
            log.append(failed)
            run_records.append(failed)
            print(f"标定失败记录已写入：{log.path}")
            continue
        record = CalibrationRecord.from_outcome(outcome)
        log.append(record)
        run_records.append(record)
        admission = CalibrationAdmission(
            outcome, min_hit_rate=args.min_hit_rate
        ).admit()
        print(_describe(outcome, admission))
        print(f"标定记录已写入：{log.path}")

    # -- 本次运行的会话汇总（验收线「N 次会话中 ≥ M 次可用」数这两行）-------
    verdicts = session_verdicts(run_records, min_hit_rate=args.min_hit_rate)
    available = sum(1 for verdict in verdicts if verdict["available"])
    print(
        f"=== 本次运行的标定会话：共 {len(verdicts)} 次"
        f"｜准入可用 {available} 次｜失败 {failures} 次 ==="
    )
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

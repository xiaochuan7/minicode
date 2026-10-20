"""命令行入口：跑一次标定探针（真调模型）。

    $env:PYTHONPATH="src"; python -m minicode.calib --out runs

**两条调用路径各标定一次**（工单 M5 AC 9）：图像调用（发探针图）与
文本调用（只发文字描述），两条路径的**标度**各自独立、互不借用。

探针尺寸 = **本机物理表面尺寸**（DPI 感知后的度量，不是写死的值）；
凭据只从仓库 ``.env`` 读（:mod:`minicode.model.config`）。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from minicode.model.client import ModelClient
from minicode.model.config import load_config
from minicode.model.effort import Effort
from minicode.model.request_log import JsonlRequestLog

from .admission import CalibrationAdmission
from .calibrate import CalibrationProbe
from .probe import default_probe_spec
from .record import CalibrationLog, CalibrationRecord

__all__ = ["main", "physical_surface_size"]


def physical_surface_size() -> tuple[int, int]:
    """本机物理表面尺寸（物理像素）。

    DPI 感知在导入冻结原型的 ``dpi`` 模块时**即生效**（PMv2），
    所以这里量到的是物理像素，不是逻辑像素。
    """
    from minicode.exec import dpi

    return dpi.physical_screen_size()


def _describe(outcome, admission) -> str:
    lines = [
        f"判定标度：{outcome.detected_scale.value}",
        f"标度判定：{outcome.scale_verdict}｜落点等价：{outcome.landing_equivalence}",
        f"标定容差：{outcome.calibration_tolerance_px} px｜越界容差："
        f"{outcome.out_of_bounds_tolerance_px} px｜标定成功：{outcome.calibration_ok}",
        f"等价标度列表：{'、'.join(s.value for s in outcome.equivalent_scales) or '（无）'}",
        f"视觉通道准入：{'可用' if admission.available else '不可用'} —— {admission.reason}",
        "各候选标度：",
    ]
    for candidate in outcome.candidates:
        lines.append(
            f"  {candidate.scale.value:<22} max_err={candidate.max_error_px:12.2f} px"
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
        "--model-input",
        default=None,
        help=(
            "声明「模型实际看到的那张图」的尺寸（格式 WxH）。"
            "默认 = 实际发出的那张图的尺寸（M1 的口径）。"
            "有些服务端会在**服务端**把大图缩小，这时模型的绝对像素坐标"
            "落在缩小后的那张图上，用发送尺寸当基准会判不出来 —— "
            "把缩小后的尺寸声明进来即可（这是量出来的，不是猜的）。"
        ),
    )
    args = parser.parse_args(argv)

    config = load_config()
    client = ModelClient(
        config, JsonlRequestLog(Path(args.out) / "model-requests.jsonl")
    )
    width, height = physical_surface_size()
    spec = default_probe_spec(width, height)
    model_input = (width, height)
    if args.model_input:
        parts = args.model_input.lower().replace("*", "x").split("x")
        if len(parts) != 2:
            parser.error("--model-input 格式应为 WxH")
        model_input = (int(parts[0]), int(parts[1]))
    paths = ("image", "text") if args.path == "both" else (args.path,)

    log = CalibrationLog(args.out)
    failures = 0
    for path in paths:
        probe = CalibrationProbe(
            client,
            spec=spec,
            model_input_size=model_input,
            call_path=path,
            model_id=config.model,
            effort=args.effort,
            max_tokens=args.max_tokens,
        )
        print(f"=== 标定（{path} 路径）｜探针 {width}×{height} ===")
        try:
            outcome = probe.run()
        except Exception as exc:  # 模型侧失败如实报出，不静默
            failures += 1
            print(f"标定失败：{type(exc).__name__}: {exc}")
            continue
        record = CalibrationRecord.from_outcome(outcome)
        log.append(record)
        admission = CalibrationAdmission(outcome).admit()
        print(_describe(outcome, admission))
        print(f"标定记录已写入：{log.path}")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

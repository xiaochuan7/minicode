"""决策层的**端到端评测**（工单 M10 AC 1 / AC 7）。

::

    $env:PYTHONPATH = "src"
    python -m minicode.decision --goal "在示例界面上点开「开始」" --out runs/m10

**不碰真桌面**：截图源、控件树、输入注入都是假实现；
**模型是真的**（凭据与脱敏开关只从仓库 ``.env`` 读）——
所以这条命令既不点真屏幕，也不花钱之外的东西，但模型调用是真的。

跑完落两样东西：

* ``decision-report.json`` —— 轮次、每一步的档位与 token、程序源码、
  标准输出、动作记录、停止原因；
* ``model-requests.jsonl`` —— 每发一次请求一条（带显式档位字段）。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from minicode.action.fakes import FakeCaptureSource, FakeInputInjector
from minicode.action.ports import WindowAnchor
from minicode.action.records import ListTraceSink, build_record
from minicode.action.seam import ActionExecutionSeam
from minicode.model.client import ModelClient
from minicode.model.config import load_config
from minicode.model.request_log import InMemoryRequestLog
from minicode.redact.config import load_model_context_config
from minicode.redact.redactor import TreeRedactor
from minicode.uia.capability import CapabilityTable
from minicode.uia.channel import UiaChannel
from minicode.uia.elements import UiaElement
from minicode.uia.fakes import (
    FakeCandidatePointer,
    FakeCapabilityProbe,
    FakeUiaQueryPort,
    FakeUnblocker,
    ScriptedRead,
)
from minicode.uia.pointer import ModelCandidatePointer
from minicode.uia.ports import TreeAssessment

from .api import ProtocolApi
from .harness import DecisionHarness

__all__ = ["main"]


def _parse_args(argv):
    parser = argparse.ArgumentParser(
        prog="minicode.decision",
        description="决策层 harness 的端到端评测（真模型 + 假桌面）",
    )
    parser.add_argument("--goal", required=True, help="多步任务目标（自然语言）")
    parser.add_argument("--out", default="runs/decision", help="落盘目录")
    parser.add_argument("--rounds", type=int, default=3, help="最多跑几轮")
    parser.add_argument("--keep-frames", type=int, default=3, help="上下文保留几张完整图")
    parser.add_argument(
        "--capture-size",
        required=True,
        help=(
            "假桌面的画面尺寸，例 2560x1600（**必须显式给**："
            "不写死图像像素常量 —— 架构备忘 C-05）"
        ),
    )
    parser.add_argument(
        "--no-disambiguate",
        action="store_true",
        help="消歧不接模型（用假指认；默认接真模型，动作步的档位证据靠它）",
    )
    return parser.parse_args(argv)


def _fake_tree(target: str) -> tuple[UiaElement, ...]:
    """假控件树：**两个同名控件** → 逼出消歧（动作步的模型请求）。"""
    return tuple(
        UiaElement(
            name=target,
            control_type="Button",
            class_name="Button",
            automation_id=f"btn-{index}",
            rect=(100 + 200 * index, 100, 180 + 200 * index, 140),
            window_handle=0,
        )
        for index in range(2)
    )


def main(argv=None) -> int:
    args = _parse_args(argv)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    config = load_config()
    request_log = InMemoryRequestLog()
    client = ModelClient(config, request_log)

    context_config = load_model_context_config()
    if not (context_config.send_tree_to_model and context_config.redaction_enabled):
        raise SystemExit(
            "脱敏未上线：仓库 .env 里 MINICODE_SEND_TREE_TO_MODEL 与 "
            "MINICODE_REDACTION_ENABLED 都得打开，才允许把控件树送模型"
            "（架构决策备忘 §2.6 C-15）"
        )

    injector = FakeInputInjector()
    width, _, height = args.capture_size.partition("x")
    capture = FakeCaptureSource(width=int(width), height=int(height))
    #: 动作记录（决策层做的每个动作一条 —— M0 的动作记录形状）。
    trace_sink = ListTraceSink()
    anchor = WindowAnchor(hwnd=0, app="fake", version="0")
    tree = _fake_tree("开始")
    query = FakeUiaQueryPort(
        reads=[ScriptedRead(elements=tree) for _ in range(8)],
    )
    pointer = (
        ModelCandidatePointer(client)
        if not args.no_disambiguate
        else FakeCandidatePointer()
    )
    channel = UiaChannel(
        query=query,
        capability=CapabilityTable(
            FakeCapabilityProbe(
                assessments=[
                    TreeAssessment(
                        has_tree=True,
                        class_names=("Button",),
                        interactive_class_names=("Button",),
                        usable_controls=2,
                        has_automation_id=True,
                        name_collisions=2,
                        child_windows=1,
                    )
                ]
            ),
            unblocker=FakeUnblocker(),
        ),
        pointer=pointer,
        redactor=TreeRedactor(context_config),
    )

    seam = ActionExecutionSeam(
        capture_sources=[capture],
        tree=None,
        injector=injector,
        anchor=anchor,
        locator=channel,
        model_context_config=context_config,
        trace_sink=trace_sink,
    )
    api = ProtocolApi(seam=seam, locator=channel, anchor=anchor)
    harness = DecisionHarness(
        caller=client,
        api=api,
        max_rounds=args.rounds,
        keep_frames=args.keep_frames,
        request_log=request_log,
    )

    report = harness.run(args.goal)

    (out_dir / "decision-report.json").write_text(
        json.dumps(report.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    with (out_dir / "model-requests.jsonl").open("w", encoding="utf-8") as handle:
        for record in request_log.records:
            handle.write(json.dumps(record.to_dict(), ensure_ascii=False) + "\n")
    # 动作记录：决策层做的每个动作都留一条（M0 的动作记录形状）。
    records = [build_record(trace).to_dict() for trace in trace_sink.traces]
    (out_dir / "action-records.json").write_text(
        json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print("========== 决策层 harness（端到端） ==========")
    print(f"目标      ：{report.goal}")
    print(f"轮次      ：{report.rounds}｜停止原因={report.stop_reason}")
    print(f"档位分布  ：{report.effort_histogram}｜未传档位={report.unset_effort_calls}")
    print(f"完整图    ：保留 {report.kept_frames} 张｜淘汰成摘要 {report.dropped_frames} 张")
    print(f"自称完成  ：{report.completion_claims_ignored} 次（**不据此停止**）")
    print("token 曲线（步序, 上下文总输入 token）：")
    for index, tokens in report.token_growth:
        print(f"  {index:>2}  {tokens}")
    print("每一步：")
    for step in report.steps:
        print(
            f"  [{step.kind}] 档位={step.effort} ok={step.ok} "
            f"输入token={step.total_input_tokens} 错误={step.error_code or '无'}"
        )
        if step.stdout:
            print(f"      标准输出：{step.stdout.strip()[:200]}")
    print("接口调用：")
    for call in report.calls:
        print(f"  {call}")
    print(f"注入事件  ：{injector.events}")
    print("========================================")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())

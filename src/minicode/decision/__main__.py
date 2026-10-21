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

from minicode.action.capture import CapturePort
from minicode.action.fakes import FakeCaptureSource, FakeInputInjector
from minicode.action.ports import WindowAnchor
from minicode.action.records import ListTraceSink, MultiTraceSink, build_record
from minicode.action.seam import ActionExecutionSeam
from minicode.approval.console import console_gate
from minicode.model.client import ModelClient
from minicode.model.config import load_config
from minicode.model.request_log import InMemoryRequestLog
from minicode.redact.config import load_model_context_config
from minicode.redact.redactor import TreeRedactor
from minicode.reflection.retry import RetryingSeam
from minicode.reflection.stop import StopPolicy
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
from minicode.uia.observer import UiaStateObserver
from minicode.uia.pointer import ModelCandidatePointer
from minicode.uia.ports import TreeAssessment
from minicode.verification.expect import Expectation, parse_expectation
from minicode.verification.layer import VerificationLayer
from minicode.verification.ocr_windows import WindowsOcrPort
from minicode.verification.telemetry import TelemetryAccumulator

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
        "--expect",
        default="",
        help=(
            "**目标级期望**（工单 M12）：`text:已保存` / `text:已保存:消失` / "
            "`state:保存:不可用` —— 用户对整个目标写的一句期望"
        ),
    )
    parser.add_argument(
        "--no-disambiguate",
        action="store_true",
        help="消歧不接模型（用假指认；默认接真模型，动作步的档位证据靠它）",
    )
    parser.add_argument(
        "--ocr",
        action="store_true",
        help=(
            "observe() 从画面上读文字（Windows 自带 OCR；**默认不接** —— "
            "接了才给得出 text，别的轮次 text_available 是 false）"
        ),
    )
    parser.add_argument(
        "--tree-target",
        default="开始",
        help=(
            "假桌面上那个按钮的**名字**（默认「开始」）。"
            "填「发送消息」这类危险名字，就能看见**审批闸门**把动作拦下来"
        ),
    )
    return parser.parse_args(argv)


def _parse_expectation(text: str) -> Expectation | None:
    """解析 ``--expect``（**唯一一处**：`minicode.verification.expect.parse_expectation`）。"""
    try:
        return parse_expectation(text)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc


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


def _latest_verification(trace_sink: ListTraceSink):
    """**最新一条追踪记录**里的校验读数（工单 D-2 的旁路出口）。

    * 校验结果**只走追踪记录**（动作协议保持窄返回类型）；
    * 程序要读「上一步的校验读数」，就从这里读 —— **不重跑校验**；
    * 还没有追踪记录 / 这一步没做校验 → ``None``（**不编一个数**）。
    """

    def read() -> dict | None:
        if not trace_sink.traces:
            return None
        return trace_sink.traces[-1].verification_result

    return read


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
    #: **停止条件**（工单 M12）：四类卡死检测器 + 熔断 + 轮次上限。
    #: 它也是追踪记录的旁路出口 —— 与动作日志看的是**同一份数据**。
    stop_policy = StopPolicy(max_rounds=args.rounds)
    anchor = WindowAnchor(hwnd=0, app="fake", version="0")
    tree = _fake_tree(args.tree_target)
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

    #: **审批闸门**（工单 M16）：危险动作要人点头，没批就**零动作下发**。
    #: 假桌面上拍的是假画面 → **不问真窗口**（只接审批人）。
    #: ⚠️ **一个闸门，两处接线**（缝 + 接口面）—— 「谁被问过」只有一份流水。
    gate = console_gate(read_foreground_window=False)
    seam = ActionExecutionSeam(
        capture_sources=[capture],
        tree=None,
        injector=injector,
        anchor=anchor,
        locator=channel,
        model_context_config=context_config,
        # 追踪记录**扇出**：动作日志一份、停止条件一份（同一份数据）。
        trace_sink=MultiTraceSink(trace_sink, stop_policy),
        # **校验层**（工单 M11）：动作之后跑一次校验
        # （没有期望 → 默认校验：界面无变化检测）。
        verifier=VerificationLayer(
            screenshot_port=CapturePort([capture]),
            observer=UiaStateObserver(query, anchor),
        ),
        # **审批闸门**（工单 M16）：危险动作要人点头，没批就**零动作下发**。
        approval_gate=gate,
    )
    #: **重试**（工单 M12 AC 3 / AC 4）：套在缝外面 ——
    #: 只重试可逆动作，退避 200 / 500 / 1200 ms，**每次重试前重新感知**。
    retrying_seam = RetryingSeam(seam)
    api = ProtocolApi(
        seam=retrying_seam,
        locator=channel,
        anchor=anchor,
        goal_expectation=_parse_expectation(args.expect),
        # **审批闸门**（工单 M16）：命中危险动作清单的动作**要人点头**。
        # 缝里那一份管点击/截图，这里这一份管**不走缝**的输入文本/按键。
        approval_gate=gate,
        # **OCR 端口**（工单 D-1）：``observe()`` 用它从画面上读文字。
        # ⚠️ 默认**不接** —— 不接时 ``observe()`` 如实说「读不到」
        # （``text_available=false``、``text`` 是空串），**不假装读到**。
        ocr_port=WindowsOcrPort() if args.ocr else None,
        # **校验读数的旁路出口**（工单 D-2）：校验结果**只走追踪记录**
        # （动作协议保持窄返回类型），程序要读「上一步的校验读数」
        # 就从**最新一条追踪记录**里读 —— **不重跑校验**（校验只有一处）。
        verification_reader=_latest_verification(trace_sink),
    )
    #: **三段耗时与三类失败**的累加器（工单 M11 AC 4 / AC 5）。
    telemetry = TelemetryAccumulator()
    harness = DecisionHarness(
        caller=client,
        api=api,
        max_rounds=args.rounds,
        keep_frames=args.keep_frames,
        request_log=request_log,
        telemetry=telemetry,
        stop_policy=stop_policy,
    )

    report = harness.run(args.goal)
    # 动作层的埋点（定位段 / 执行+校验段 / 定位失败 / 执行失败）从追踪记录里取。
    for trace in trace_sink.traces:
        telemetry.add_trace(trace)
    telemetry_report = telemetry.report()

    (out_dir / "decision-report.json").write_text(
        json.dumps(
            {**report.to_dict(), "telemetry": telemetry_report.to_dict()},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    with (out_dir / "model-requests.jsonl").open("w", encoding="utf-8") as handle:
        for record in request_log.records:
            handle.write(json.dumps(record.to_dict(), ensure_ascii=False) + "\n")
    # 动作记录：决策层做的每个动作都留一条（M0 的动作记录形状）。
    records = [build_record(trace).to_dict() for trace in trace_sink.traces]
    (out_dir / "action-records.json").write_text(
        json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    # **停止条件**的判定（工单 M12）：四类卡死检测器 + 换策略记录。
    (out_dir / "stop-conditions.json").write_text(
        json.dumps(
            {
                "detectors": list(report.detectors),
                "strategy_changes": list(report.strategy_changes),
                "stop_reason": report.stop_reason,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    # **交接产物**（工单 M12 AC 7）：交给人时落一份人能读懂的。
    if report.handoff is not None:
        (out_dir / "handoff.json").write_text(
            json.dumps(report.handoff.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        (out_dir / "handoff.txt").write_text(
            report.handoff.to_text(), encoding="utf-8"
        )

    print("========== 决策层 harness（端到端） ==========")
    print(f"目标      ：{report.goal}")
    print(f"轮次      ：{report.rounds}｜停止原因={report.stop_reason}")
    print(f"档位分布  ：{report.effort_histogram}｜未传档位={report.unset_effort_calls}")
    print(f"完整图    ：保留 {report.kept_frames} 张｜淘汰成摘要 {report.dropped_frames} 张")
    print(f"自称完成  ：{report.completion_claims_ignored} 次（**不据此停止**）")
    print("token 曲线（步序, 上下文总输入 token, 图像 token）：")
    image_tokens_by_step = dict(report.image_token_growth)
    for index, tokens in report.token_growth:
        print(f"  {index:>2}  {tokens}  图像={image_tokens_by_step.get(index)}")
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
    print(
        "三段耗时  ："
        f"定位={telemetry_report.stages_ms['locate']:.1f}ms "
        f"决策={telemetry_report.stages_ms['decision']:.1f}ms "
        f"执行+校验={telemetry_report.stages_ms['execute_verify']:.1f}ms｜占比="
        + "、".join(
            f"{name}={share:.0%}" for name, share in telemetry_report.stage_share.items()
        )
    )
    print(
        "三类失败  ："
        + "、".join(
            f"{name}={count}" for name, count in telemetry_report.failures.items()
        )
        + "｜占比="
        + "、".join(
            f"{name}={share:.0%}"
            for name, share in telemetry_report.failure_share.items()
        )
    )
    print("卡死检测器：")
    for verdict in report.detectors:
        print(
            f"  {verdict['detector']}：命中={verdict['triggered']} "
            f"计数={verdict['count']}｜{verdict['reason']}"
        )
    if report.strategy_changes:
        print("换策略：")
        for change in report.strategy_changes:
            print(f"  {change['description']}")
    if report.handoff is not None:
        print("交接产物：")
        for line in report.handoff.to_text().splitlines():
            print(f"  {line}")
    print("========================================")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())

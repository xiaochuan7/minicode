"""命令行入口：**一条命令跑完最小闭环**。

真机::

    $env:PYTHONPATH = "src"; python -m minicode.action --target "开始" --out runs

不碰真桌面（假实现，CI 可跑）::

    $env:PYTHONPATH = "src"; python -m minicode.action --fake --out runs

全程零模型调用、零费用、不需要任何凭据。
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import replace
from pathlib import Path

__all__ = ["main", "LANDING_TOLERANCE_PX"]

#: 落点与控件框中心的允许偏差（物理像素）。验收项 6：≤2 px。
LANDING_TOLERANCE_PX = 2

#: 默认验收目标：任务栏「开始」按钮。
#: 它的**名称稳定**（不像任务栏天气控件的名字会随天气变），
#: 副作用也**可逆**（点开后弹出的开始菜单按 Esc 即可关闭）。
DEFAULT_TARGET = "开始"


def _parse_args(argv):
    parser = argparse.ArgumentParser(
        prog="minicode.action",
        description="M0 最小闭环：DPI 自检 → 抓屏 → 唯一定位 → 点击 → 回读 → 一条动作记录",
    )
    parser.add_argument(
        "--target",
        default=DEFAULT_TARGET,
        help="验收目标按钮的控件名（默认：任务栏「开始」按钮）",
    )
    parser.add_argument("--out", default="runs", help="动作记录落盘目录")
    parser.add_argument(
        "--fake",
        action="store_true",
        help="用假实现跑一遍（不碰真桌面、不注入任何输入）",
    )
    return parser.parse_args(argv)


def _run_fake(args):
    """用假实现跑一遍：证明缝零依赖、可在 CI 上跑。"""
    from minicode.action.fakes import (
        FakeCaptureSource,
        FakeControlTree,
        FakeInputInjector,
        FakeMetrics,
        FakeWindowController,
    )
    from minicode.action.geometry import VirtualDesktop
    from minicode.action.ports import ControlCandidate
    from minicode.action.runner import run_minimal_loop

    desktop = VirtualDesktop(0, 0, 2560, 1600)
    candidate = ControlCandidate(
        name=args.target,
        control_type="Button",
        rect=(1016, 1528, 1083, 1600),
        window_handle=65784,
    )
    return run_minimal_loop(
        target_description=args.target,
        capture_sources=(FakeCaptureSource(width=2560, height=1600),),
        tree=FakeControlTree((candidate,)),
        injector=FakeInputInjector(),
        window_controller=FakeWindowController(foreground=65784),
        virtual_desktop=desktop,
        metrics=FakeMetrics(),
        log_directory=args.out,
    )


def _run_real(args):
    """真机跑一遍：进程第一行声明 per-monitor v2 感知。"""
    # ↓↓↓ 进程第一行：声明 DPI 感知，必须早于任何依赖它的 API ↓↓↓
    # ↑↑↑
    from minicode.action.runner import run_minimal_loop
    from minicode.action.windows import (
        WindowsCaptureSource,
        WindowsControlTree,
        WindowsInputInjector,
        WindowsMetrics,
        WindowsWindowController,
        current_virtual_desktop,
        system_double_click_params,
    )
    from minicode.exec import dpi  # noqa: F401  (导入即声明 PMv2)

    desktop = current_virtual_desktop()
    tree = WindowsControlTree()
    outcome = run_minimal_loop(
        target_description=args.target,
        capture_sources=(WindowsCaptureSource(),),
        tree=tree,
        injector=WindowsInputInjector(desktop),
        window_controller=WindowsWindowController(),
        virtual_desktop=desktop,
        metrics=WindowsMetrics(),
        log_directory=args.out,
        double_click_params=system_double_click_params(),
    )
    # 独立复验：动作之后再读一次控件树，落点与复验框中心的距离必须 ≤2 物理像素。
    # **这一步是判定，不是打印**——超差就不能声称闭环打通。
    if outcome.ok and outcome.result and outcome.result.final_point:
        landing = outcome.result.final_point
        snapshot = tree.snapshot()
        for candidate in snapshot.candidates:
            if candidate.name.strip() == args.target.strip():
                cx, cy = candidate.center
                delta = max(abs(landing[0] - cx), abs(landing[1] - cy))
                verdict = "通过" if delta <= LANDING_TOLERANCE_PX else "超差"
                print(
                    f"落点复验：落点={landing} 复验框中心=({cx}, {cy}) "
                    f"偏差={delta:.1f} px（要求 ≤{LANDING_TOLERANCE_PX} px）→ {verdict}"
                )
                if delta > LANDING_TOLERANCE_PX:
                    outcome = replace(outcome, ok=False, message=f"落点复验超差：偏差 {delta:.1f} px")
                break
    return outcome


def main(argv=None) -> int:
    args = _parse_args(argv)
    outcome = _run_fake(args) if args.fake else _run_real(args)

    print("========== M0 最小闭环 ==========")
    print(f"启动自检：{outcome.startup.describe()}")
    if outcome.injection:
        print(f"注入自检：{outcome.injection.describe()}")
    if outcome.result:
        print(f"动作结果：ok={outcome.result.ok} 落点={outcome.result.final_point}")
        print(f"意图    ：{outcome.result.intent}")
        if outcome.result.result.error_code:
            print(f"错误码  ：{outcome.result.result.error_code.value}")
            print(f"错误信息：{outcome.result.result.error_message}")
    if outcome.record:
        print(f"动作记录：{Path(args.out) / 'action-records.jsonl'}")
        print(json.dumps(outcome.record.to_dict(), ensure_ascii=False, indent=2))
    else:
        print("动作记录：未产出（自检未通过或动作未执行）")
    if outcome.message:
        print(f"说明    ：{outcome.message}")
    print("==================================")
    return 0 if outcome.ok else 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())

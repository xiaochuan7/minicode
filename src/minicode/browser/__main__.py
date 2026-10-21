"""浏览器通道的命令行：在真实页面上按可见文本定位并操作一个元素。

```powershell
$env:PYTHONPATH="src"; python -m minicode.browser --url "file:///C:/tmp/page.html" `
    --target "提交订单" --click --out runs/m13
```

⚠️ **动真机**（开浏览器窗口、点页面）—— 必须先按 AGENTS.md 拿
``.scratch/desktop.lock`` 占用锁。
"""

from __future__ import annotations

import argparse
import sys

from minicode.action.fakes import FakeControlTree, FakeInputInjector
from minicode.action.ports import LocateRequest, WindowAnchor
from minicode.action.protocol import ActionKind
from minicode.action.records import ActionLog, build_record
from minicode.action.seam import ActionExecutionSeam, SeamRequest
from minicode.action.windows import WindowsCaptureSource
from minicode.exec import dpi
from minicode.lock import acquire, release
from minicode.privacy.guard import windows_guard

from .capability import BrowserCapabilityTable
from .cdp import ChromiumCapabilityProbe, ChromiumPage, find_browser_window
from .channel import BrowserChannel


class _LogSink:
    """把追踪记录写成**动作记录**（JSONL）。"""

    def __init__(self, log: ActionLog):
        self.log = log

    def emit(self, trace) -> None:
        self.log.append(build_record(trace))


def _open_page(url: str, *, channel: str, headless: bool):
    """打开一个 Chromium 窗口并接上 CDP。返回 ``(playwright, browser, page, cdp)``。"""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:  # pragma: no cover - 可选依赖缺失
        raise SystemExit(
            "真机浏览器通道需要 Playwright（可选依赖）：pip install playwright"
        ) from exc
    playwright = sync_playwright().start()
    browser = playwright.chromium.launch(
        channel=channel,
        headless=headless,
        args=["--window-size=1280,900", "--window-position=120,80"],
    )
    context = browser.new_context(no_viewport=True)
    page = context.new_page()
    page.goto(url)
    page.wait_for_timeout(800)
    cdp = context.new_cdp_session(page)
    return playwright, browser, page, cdp


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="浏览器通道：按页面结构定位并操作元素")
    parser.add_argument("--url", required=True, help="要打开的页面（file:// 或 http://）")
    parser.add_argument("--target", required=True, help="目标描述（可见文本 / 角色）")
    parser.add_argument("--role", default="", help="角色提示（如 button / textbox）")
    parser.add_argument(
        "--browser", default="chrome", help="Chromium 系通道名（chrome / msedge）"
    )
    parser.add_argument(
        "--click", action="store_true", help="定位成功后用浏览器自己的输入通道点它"
    )
    parser.add_argument("--headless", action="store_true", help="无头模式（默认有头）")
    parser.add_argument("--out", default="runs/m13", help="动作记录落盘目录")
    parser.add_argument(
        "--ticket", default="M13", help="真机占用锁的票号（AGENTS.md：动真机前先拿锁）"
    )
    args = parser.parse_args(argv)
    # **动真机前先拿锁**（AGENTS.md「真机占用锁」）：本命令会开真实浏览器窗口、
    # 点真实页面 —— 人类在用电脑时脚本直接拒绝（退出码 3）。
    holds_lock = acquire(args.ticket)
    try:
        return _run(args)
    finally:
        if holds_lock:
            release()


def _run(args) -> int:
    # 窗口矩形要按**物理**像素读 → 进程必须 PMv2 感知（先于任何窗口创建）。
    dpi.enable_dpi_awareness()

    playwright, browser, page, cdp = _open_page(
        args.url, channel=args.browser, headless=args.headless
    )
    try:
        title = page.title()
        hwnd = find_browser_window(title)
        source = ChromiumPage(page, cdp, hwnd)
        table = BrowserCapabilityTable(ChromiumCapabilityProbe(source))
        anchor = WindowAnchor(
            hwnd=hwnd, app=args.browser, version=str(browser.version)
        )
        entry = table.availability(anchor)
        print(f"页面标题：{title}｜窗口句柄：{hwnd}")
        print(f"能力表：{entry.availability.value}（{entry.app} {entry.version}）｜{entry.reason}")
        print(
            f"实测读数：有可见文本 {entry.named_elements} 个"
            f"｜可交互但无可见文本 {entry.unnamed_interactive} 个"
            f"｜撞车 {entry.duplicate_names} 处"
        )
        channel = BrowserChannel(structure=source, capability=table)
        result = channel.locate(
            LocateRequest(
                target_description=args.target,
                anchor=anchor,
                control_type=args.role,
            )
        )
        if result.error_code is not None:
            print(
                f"定位失败：{result.error_code.value}｜{result.error_message}"
                f"｜失败类型 {result.failure_type}"
            )
            return 1
        selected = result.selected
        print(
            f"选中项：{selected.name!r}（角色 {selected.control_type}）"
            f"｜落点 {result.landing_point}｜查询口径 {result.query_scope}"
        )
        if not args.click:
            return 0
        log = ActionLog(args.out)
        seam = ActionExecutionSeam(
            capture_sources=[WindowsCaptureSource()],
            tree=FakeControlTree(()),
            injector=FakeInputInjector(),
            locator=channel,
            invoker=source,
            anchor=anchor,
            trace_sink=_LogSink(log),
            # **隐私闸门**（工单 M20）：敏感窗口**根本不许拍**（抓帧之前拦下）。
            privacy_guard=windows_guard(),
        )
        seam_result = seam.run(
            SeamRequest(
                target_description=args.target,
                action_kind=ActionKind.CLICK,
                control_type=args.role,
            )
        )
        print(f"动作结果：ok={seam_result.ok}｜{seam_result.intent}")
        for note in seam_result.result.notes or ():
            print(f"  说明：{note}")
        print(f"动作记录：{log.path}")
        return 0 if seam_result.ok else 1
    finally:
        browser.close()
        playwright.stop()


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())

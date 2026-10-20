"""M13 真机验证：真实页面上的结构定位 / 操作 / 引用时效 / 键盘退化 / 剪贴板。

用法（**必须先拿真机占用锁**，AGENTS.md）::

    python scripts/desktop_lock.py acquire --ticket M13
    $env:PYTHONPATH="src"; python -m minicode.browser.verify_real
    python scripts/desktop_lock.py release

页面是仓库里的固定夹具（``fixtures/flat_page.html``：扁平 DOM）——
所以这条真机证据**可复现**，不依赖临时文件。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from minicode.action.executor import ActionExecutor  # noqa: E402
from minicode.action.ports import LocateRequest, WindowAnchor  # noqa: E402
from minicode.action.windows import WindowsInputInjector  # noqa: E402
from minicode.browser.capability import BrowserCapabilityTable  # noqa: E402
from minicode.browser.cdp import ChromiumCapabilityProbe, ChromiumPage, find_browser_window  # noqa: E402
from minicode.browser.channel import BrowserChannel  # noqa: E402
from minicode.browser.typing import BrowserTyping  # noqa: E402
from minicode.exec import dpi  # noqa: E402
from minicode.lock import acquire, release  # noqa: E402


def main() -> None:
    # **动真机前先拿锁**（AGENTS.md「真机占用锁」）：人类在用电脑时脚本直接拒绝。
    holds_lock = acquire("M13")
    try:
        _run()
    finally:
        if holds_lock:
            release()


def _run() -> None:
    dpi.enable_dpi_awareness()
    url = (
        "file:///"
        + str((Path(__file__).with_name("fixtures") / "flat_page.html").resolve()).replace(
            "\\", "/"
        )
    )
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(
            channel="chrome",
            headless=False,
            args=["--window-size=1280,900", "--window-position=120,80"],
        )
        context = browser.new_context(no_viewport=True)
        page = context.new_page()
        page.goto(url)
        page.wait_for_timeout(800)
        cdp = context.new_cdp_session(page)
        hwnd = find_browser_window(page.title())
        source = ChromiumPage(page, cdp, hwnd)
        table = BrowserCapabilityTable(ChromiumCapabilityProbe(source))
        anchor = WindowAnchor(hwnd=hwnd, app="chrome", version=str(browser.version))
        entry = table.availability(anchor)
        print(f"[能力表] {entry.availability.value}（{entry.app} {entry.version}）｜{entry.reason}")
        print(
            f"[实测读数] 有可见文本 {entry.named_elements}"
            f"｜可交互但无可见文本 {entry.unnamed_interactive}"
            f"｜撞车 {entry.duplicate_names}"
        )
        channel = BrowserChannel(structure=source, capability=table)

        # ---- AC 1：按可见文本定位并操作 ---------------------------------
        result = channel.locate(LocateRequest(target_description="提交订单", anchor=anchor))
        print(f"[AC1] 错误码={result.error_code} 选中项={result.selected.name!r} "
              f"角色={result.selected.control_type!r} 落点={result.landing_point} "
              f"查询口径={result.query_scope} 模型调用={result.model_calls}")
        print(f"[AC1] 物理矩形={result.selected.rect}")
        clicked = source.invoke(result.selected.element_id)
        page.wait_for_timeout(300)
        status = page.inner_text("#status")
        print(f"[AC1] 浏览器自己的输入通道点击={clicked} 页面状态={status!r}")

        # ---- 落点精度交叉核对（浏览器通道 vs UIA 树的真值）--------------
        from minicode.action.ports import WindowAnchor as WA
        from minicode.uia.ports import UiaFindRequest
        from minicode.uia.windows import WindowsUiaQueryPort

        port = WindowsUiaQueryPort(timeout_s=8.0)
        web = channel.locate(LocateRequest(target_description="姓名", anchor=anchor))
        try:
            found = port.find(UiaFindRequest(anchor=WA(hwnd=hwnd), control_type="Edit"))
        except Exception as exc:  # noqa: BLE001
            found = None
            print("[对照] UIA 查询失败:", exc)
        if found is not None:
            for el in found.elements:
                print(f"[对照] UIA Edit 元素 name={el.name!r} rect={el.rect} class={el.class_name!r}")
            for el in found.elements:
                if el.name == "姓名":
                    wb = web.selected.rect if web.selected else None
                    uia = el.rect
                    print(f"[对照] 浏览器通道矩形={wb} 中心="
                          f"{( (wb[0]+wb[2])/2, (wb[1]+wb[3])/2 ) if wb else None}")
                    print(f"[对照] UIA 树矩形={uia} 中心="
                          f"{((uia[0]+uia[2])/2, (uia[1]+uia[3])/2)}")
                    if wb:
                        dx = abs((wb[0] + wb[2]) / 2 - (uia[0] + uia[2]) / 2)
                        dy = abs((wb[1] + wb[3]) / 2 - (uia[1] + uia[3]) / 2)
                        print(f"[对照] 两个中心偏差 = ({dx:.1f}, {dy:.1f}) px")

        # ---- AC 3：元素引用有时效 ---------------------------------------
        old_id = result.selected.element_id
        page.reload()
        page.wait_for_timeout(500)
        stale_ok = True
        try:
            source.cdp.send("DOM.resolveNode", {"backendNodeId": int(old_id)})
        except Exception as exc:  # noqa: BLE001
            stale_ok = False
            print(f"[AC3] 旧引用 {old_id} 已失效：{type(exc).__name__}")
        again = channel.locate(LocateRequest(target_description="提交订单", anchor=anchor))
        print(f"[AC3] 刷新后重新定位：错误码={again.error_code} "
              f"新引用={again.selected.element_id if again.selected else None} "
              f"落点={again.landing_point}（旧引用 {old_id} 可用={stale_ok}）")

        # ---- AC 4 / AC 5：文本输入 ---------------------------------------
        page.click("#name")
        page.wait_for_timeout(200)
        # 把浏览器窗口**置前**（桌面注入要打在前景窗口上）。
        from minicode.action.windows import WindowsWindowController

        page.bring_to_front()
        page.wait_for_timeout(300)
        controller = WindowsWindowController()
        raised = controller.bring_to_front(hwnd)
        if not raised:
            # Windows 的前台锁：后台进程直接 SetForegroundWindow 会失败。
            # 经典的绕法：先发一个 ALT 键（脚本层 workaround，不进仓库代码）。
            import ctypes

            ctypes.windll.user32.keybd_event(0x12, 0, 0, 0)
            ctypes.windll.user32.keybd_event(0x12, 0, 2, 0)
            raised = controller.bring_to_front(hwnd)
            print(f"[诊断] SetForegroundWindow 被拒 → 用 ALT 绕法再试：raised={raised}")
        page.wait_for_timeout(400)
        print("[诊断] activeElement =", page.evaluate(
            "() => document.activeElement ? document.activeElement.tagName + '/' + document.activeElement.id : null"))
        print("[诊断] 前景窗口 =", WindowsWindowController().foreground_window(), "目标 hwnd =", hwnd)
        injector = WindowsInputInjector()
        before = injector.read_clipboard()
        typing = BrowserTyping(
            executor=ActionExecutor(injector),
            structure=source,
            browser_input=source,
        )
        typed = typing.type_text("张三")
        page.wait_for_timeout(300)
        value = page.input_value("#name")
        after = injector.read_clipboard()
        print(f"[AC4] 输入结果 ok={typed.ok} 页面收到={value!r} "
              f"退化={any('输入通道退化' in n for n in (typed.notes or ()))} "
              f"判不了={any('判不了' in n for n in (typed.notes or ()))}")
        print(f"[AC4] notes={typed.notes}")
        print(f"[AC5] 剪贴板动作前后一致={before == after}（内容不打印）")
        print("[诊断] last_error =", source.last_error)
        print("[诊断] focused() =", source._focused())
        print("[诊断] 输入框当前值 =", repr(page.input_value("#name")))
        browser.close()


if __name__ == "__main__":
    main()

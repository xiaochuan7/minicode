"""M13 真机验证：真实页面上的结构定位 / 操作 / 引用时效 / 键盘退化 / 剪贴板。

**M28 追加**：落点取**可见文本的实际区域** —— 量「落点 vs 文字实际区域中心」
的偏差（两遍：页面自己报的文本区域 / 从画面量的墨迹包围盒）。

用法（**必须先拿真机占用锁**）::

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
from minicode.action.geometry import center_point  # noqa: E402
from minicode.action.ports import LocateRequest, WindowAnchor  # noqa: E402
from minicode.action.windows import WindowsInputInjector  # noqa: E402
from minicode.browser.capability import BrowserCapabilityTable  # noqa: E402
from minicode.browser.cdp import ChromiumCapabilityProbe, ChromiumPage, find_browser_window  # noqa: E402
from minicode.browser.channel import BrowserChannel  # noqa: E402
from minicode.browser.geometry import css_rect_to_physical  # noqa: E402
from minicode.browser.typing import BrowserTyping  # noqa: E402
from minicode.exec import dpi  # noqa: E402
from minicode.lock import acquire, release  # noqa: E402


def _ink_bbox(image, threshold: int = 200):
    """画面里「有墨」的像素的包围盒（图片自身的像素坐标，左上角原点）。

    真值**从画面量**（不采信 DOM 的几何读数）：只看渲染出来的像素。
    找不到墨迹 → ``None``。
    """
    import numpy as np

    array = np.asarray(image.convert("RGB"))
    ink = array.min(axis=2) < threshold
    ys, xs = np.nonzero(ink)
    if len(xs) == 0:
        return None
    return (int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1)


def _text_landing_deviation(page, source, element_id: str, landing) -> dict:
    """量「落点 vs 文字实际区域中心」的偏差（工单 M28 AC 2）。

    量两遍：

    1. **页面自己报的**可见文本区域（``Range.getClientRects`` 那一层）——
       换算到物理表面空间后与落点比；
    2. **从画面量**的：把元素那块画面截下来，找「有墨的像素」的包围盒
       —— 这是**独立**的一条核对（不采信 DOM 的几何读数）。
    """
    viewport = source.viewport()
    css_text = source._css_text_rects.get(element_id)
    css_box = source._css_rects.get(element_id)
    report: dict = {
        "scale": viewport.scale,
        "origin": viewport.origin_physical,
        "css_text_rect": css_text,
        "css_box_rect": css_box,
    }
    if css_text is None:
        report["error"] = "没有可见文本的区域（判不了）"
        return report
    text_center_css = (css_text[0] + css_text[2] / 2.0, css_text[1] + css_text[3] / 2.0)
    # ⚠️ **换算必须走落地路径那一处**（工单 M28 AC 2 + 坐标换算单一真值源）：
    # 落地路径是 `textmetrics.measure_visible_text` → `css_rect_to_physical`
    # （**逐边取整**）→ `channel` 的 `center_point`。这里**再写一遍公式**的话，
    # 两处的取整方式不同 → 量出来的是「取整差」，不是落点误差。
    text_center_physical = center_point(css_rect_to_physical(css_text, viewport))
    report["text_center_physical"] = text_center_physical
    report["text_center_physical_unrounded"] = (
        viewport.origin_physical[0] + text_center_css[0] * viewport.scale,
        viewport.origin_physical[1] + text_center_css[1] * viewport.scale,
    )
    report["deviation_page_reported"] = (
        abs(landing[0] - text_center_physical[0]),
        abs(landing[1] - text_center_physical[1]),
    )
    if css_box is None:
        return report
    # -- 独立的那一遍：从画面量 ------------------------------------------
    try:
        import io

        from PIL import Image

        scroll = page.evaluate("() => [window.scrollX, window.scrollY]")
        clip = {
            "x": css_box[0] + float(scroll[0]),
            "y": css_box[1] + float(scroll[1]),
            "width": css_box[2],
            "height": css_box[3],
        }
        shot = page.screenshot(clip=clip)
        image = Image.open(io.BytesIO(shot))
        ink = _ink_bbox(image)
        if ink is None:
            report["error"] = "截下来的画面里没有墨迹（量不到文字）"
            return report
        pixels_per_css = image.width / css_box[2] if css_box[2] else 1.0
        ink_center_css = (
            css_box[0] + (ink[0] + ink[2]) / 2.0 / pixels_per_css,
            css_box[1] + (ink[1] + ink[3]) / 2.0 / pixels_per_css,
        )
        ink_center_physical = (
            viewport.origin_physical[0] + ink_center_css[0] * viewport.scale,
            viewport.origin_physical[1] + ink_center_css[1] * viewport.scale,
        )
        report["ink_center_physical"] = ink_center_physical
        report["ink_bbox_pixels"] = ink
        report["pixels_per_css"] = pixels_per_css
        report["ink_bbox_css"] = (
            ink[0] / pixels_per_css,
            ink[1] / pixels_per_css,
            ink[2] / pixels_per_css,
            ink[3] / pixels_per_css,
        )
        report["ink_center_physical"] = ink_center_physical
        report["deviation_from_picture"] = (
            abs(landing[0] - ink_center_physical[0]),
            abs(landing[1] - ink_center_physical[1]),
        )
    except Exception as exc:  # noqa: BLE001 —— 量不到就如实说量不到
        report["error"] = f"画面核对失败：{type(exc).__name__}: {exc}"
    return report


def main() -> None:
    # **动真机前先拿锁**：人类在用电脑时脚本直接拒绝。
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

        # ---- AC 2（工单 M28）：落点与文字实际区域中心的偏差 ----------------
        deviation = _text_landing_deviation(
            page, source, result.selected.element_id, result.landing_point
        )
        print(f"[AC2] 落点={result.landing_point}｜比例={deviation.get('scale')} "
              f"｜原点={deviation.get('origin')}")
        print(f"[AC2] 可见文本区域(CSS)={deviation.get('css_text_rect')} "
              f"｜元素盒子(CSS)={deviation.get('css_box_rect')}")
        print(f"[AC2] 文字实际区域中心(物理)={deviation.get('text_center_physical')} "
              f"｜偏差={deviation.get('deviation_page_reported')} 物理像素")
        print(f"[AC2] 从画面量的墨迹包围盒(截图像素)={deviation.get('ink_bbox_pixels')} "
              f"｜(CSS 像素)={deviation.get('ink_bbox_css')} "
              f"｜墨迹中心(物理)={deviation.get('ink_center_physical')} "
              f"｜偏差={deviation.get('deviation_from_picture')} 物理像素")
        if deviation.get("error"):
            print(f"[AC2] 画面核对：{deviation['error']}")

        # ---- AC 6（工单 M28，口径 2026-10-21）：没有文本子节点 → 自己的矩形中心 ----
        # 夹具里的输入框「姓名」：可访问性名来自 **placeholder**，它**不是文本节点**
        # → **元素框本身就是它的真值** → 落点取它自己的矩形中心（**不是**判不了）。
        # ⚠️ 这**不是**「静默回落到矩形中心」—— 那是「有文本子节点却量不到」时禁止的事。
        undecidable = channel.locate(
            LocateRequest(target_description="姓名", anchor=anchor)
        )
        print(f"[AC6] 「姓名」错误码={undecidable.error_code} "
              f"失败类型={undecidable.failure_type} "
              f"落点={undecidable.landing_point} "
              f"原因={undecidable.error_message}")
        print("[AC6] 期望：错误码=None（不是判不了）、落点=该输入框自己的矩形中心")

        # ---- AC 5（工单 M28）：**有**文本子节点却量不到 → 判不了（不回落）----
        # 夹具里的「被裁掉的文字」：文字**在 DOM 里**，但被 overflow:hidden 裁到
        # 可见区之外 → 文本度量判「clipped」→ **判不了**，
        # **不静默回落到元素矩形中心**。
        clipped = channel.locate(
            LocateRequest(target_description="被裁掉的文字", anchor=anchor)
        )
        print(f"[AC5] 「被裁掉的文字」错误码={clipped.error_code} "
              f"失败类型={clipped.failure_type} "
              f"落点={clipped.landing_point} "
              f"原因={clipped.error_message}")
        print("[AC5] 期望：错误码=invalid_geometry、失败类型=selection、落点=None")

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

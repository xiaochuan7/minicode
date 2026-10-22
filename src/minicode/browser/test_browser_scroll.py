"""**浏览器通道的滚动**（工单 T8）的单测。

只测**外部可观察**：发了什么鼠标事件、滚了多少 CSS 像素、读数是什么。
不碰真桌面（假 CDP 会话 + 假页面）。
"""

from __future__ import annotations

import pytest

from minicode.browser.cdp import SCROLL_UNIT, ChromiumPage


class _FakeCdp:
    """假的 CDP 会话：按脚本回应那几类调用。"""

    def __init__(self, *, per_line: float = 32.0, wheel_moves: bool = True):
        self.calls: list[tuple[str, dict]] = []
        self.per_line = per_line
        self.wheel_moves = wheel_moves
        self.scroll_y = 0.0
        self.element_top = 0.0

    def send(self, method, params=None):
        params = dict(params or {})
        self.calls.append((method, params))
        if method == "DOM.getDocument":
            return {}
        if method == "DOM.resolveNode":
            return {"object": {"objectId": "obj-1"}}
        if method == "Runtime.callFunctionOn":
            declaration = params.get("functionDeclaration", "")
            if "childGap" in declaration:
                return {
                    "result": {
                        "value": {
                            "childGap": self.per_line,
                            "lineHeight": 0.0,
                            "fontSize": 16.0,
                        }
                    }
                }
            if "scrollBy" in declaration:
                delta = float(
                    declaration.split("scrollBy(0, ")[1].split(")")[0].rstrip("}")
                )
                self.element_top += delta
                return {
                    "result": {
                        "value": {"windowY": self.scroll_y, "elementTop": self.element_top}
                    }
                }
            return {
                "result": {"value": {"windowY": self.scroll_y, "elementTop": self.element_top}}
            }
        if method == "Input.dispatchMouseEvent":
            if params.get("type") == "mouseWheel" and self.wheel_moves:
                self.scroll_y += float(params.get("deltaY") or 0.0)
            return {}
        return {}

    def of_type(self, event_type: str) -> list[dict]:
        """某一种鼠标事件的参数（按发出的顺序）。"""
        return [
            params
            for method, params in self.calls
            if method == "Input.dispatchMouseEvent" and params.get("type") == event_type
        ]


class _FakePage:
    """假的页面：只答视口尺寸。"""

    def __init__(self, viewport=(1280, 780)):
        self.viewport = viewport

    def evaluate(self, script):
        if "innerWidth" in script:
            return {"width": self.viewport[0], "height": self.viewport[1]}
        return {}


def _page(css_rect=(100.0, 120.0, 600.0, 900.0), *, per_line=32.0, wheel_moves=True):
    page = _FakePage()
    cdp = _FakeCdp(per_line=per_line, wheel_moves=wheel_moves)
    source = ChromiumPage(page, cdp, hwnd=1)
    source._css_rects["42"] = css_rect
    return source, cdp


def test_the_scroll_goes_through_the_browser_input_channel():
    """滚动走**浏览器自己的输入通道**（CDP ``mouseWheel``）。"""
    source, cdp = _page()
    result = source.scroll_lines("42", 3)
    assert result["ok"] is True
    wheels = cdp.of_type("mouseWheel")
    assert len(wheels) == 1
    assert wheels[0]["deltaX"] == 0.0
    assert wheels[0]["deltaY"] == pytest.approx(96.0)


def test_the_mouse_moves_to_the_target_before_the_wheel():
    """**先 mouseMoved 再 wheel** —— wheel 事件落在光标处。"""
    source, cdp = _page()
    source.scroll_lines("42", 1)
    types = [
        params.get("type")
        for method, params in cdp.calls
        if method == "Input.dispatchMouseEvent"
    ]
    assert types.index("mouseMoved") < types.index("mouseWheel")


def test_one_line_is_measured_at_runtime_not_hardcoded():
    """「1 行 = 多少 CSS px」**运行时量** —— 换一行高就换一次 delta。"""
    source, cdp = _page(per_line=37.5)
    result = source.scroll_lines("42", 2)
    assert result["css_px_per_line"] == pytest.approx(37.5)
    assert cdp.of_type("mouseWheel")[0]["deltaY"] == pytest.approx(75.0)


def test_the_unit_is_declared_as_css_pixels():
    """**浏览器通道的单位是 CSS 像素**（与桌面通道各自声明）。"""
    source, _cdp = _page()
    assert source.scroll_lines("42", 1)["unit"] == SCROLL_UNIT
    assert SCROLL_UNIT == "css-px"


def test_the_scrolled_amount_is_verified_against_the_viewport_height():
    """滚完读 ``window.scrollY``：``0 < Δ < 视口高`` 才算滚到了。"""
    source, _cdp = _page()
    result = source.scroll_lines("42", 3)
    assert result["scrolled_px"] == pytest.approx(96.0)
    assert result["within_viewport"] is True
    assert result["viewport_height_css_px"] == pytest.approx(780.0)


def test_the_scroll_falls_back_to_scroll_by_when_the_wheel_is_eaten():
    """**wheel 事件被页面 handler 吃掉** → 退 ``el.scrollBy(0, dy)``。"""
    source, cdp = _page(wheel_moves=False)
    result = source.scroll_lines("42", 3)
    assert result["fallback_used"] is True
    assert result["scrolled_px"] == pytest.approx(96.0)
    assert result["ok"] is True
    assert any("scrollBy" in params.get("functionDeclaration", "") for _, params in cdp.calls)


def test_the_hover_point_stays_inside_the_viewport_for_a_tall_list():
    """可滚列表**比视口还高** → 光标要落在**看得见的那一块**上。"""
    source, cdp = _page(css_rect=(100.0, 120.0, 600.0, 4000.0))
    source.scroll_lines("42", 1)
    moved = cdp.of_type("mouseMoved")[0]
    assert 0.0 <= moved["y"] <= 780.0
    assert moved["y"] < 120.0 + 4000.0 / 2.0


def test_an_unknown_element_is_reported_instead_of_guessed():
    """元素不在快照里 → **如实报读不出来**，不猜一个位置。"""
    source, _cdp = _page()
    result = source.scroll_lines("99", 3)
    assert result["ok"] is False
    assert "没有这个元素" in result["error"]

"""**浏览器通道那几步**（工单 T8）的单测。

只测**外部可观察**：上下文里写了哪些读数（工单的验收条款就靠它们判）。
不碰真桌面（假页面结构 + 假滚动）。
"""

from __future__ import annotations

from minicode.action.protocol import ActionKind
from minicode.browser.elements import BrowserElement, TextMeasurement
from minicode.browser.ports import PageSnapshot
from minicode.e2e.apps import VerificationPage
from minicode.e2e.real import ChromeExecutor
from minicode.verification.expect import Expectation, ExpectationKind

from .tasks import Step


def _element(name: str, role: str, element_id: str) -> BrowserElement:
    rect = (100, 200, 300, 240)
    return BrowserElement(
        name=name,
        role=role,
        rect=rect,
        element_id=element_id,
        text=TextMeasurement(rect=rect),
    )


class _FakeSource:
    """假的页面结构 + 假的浏览器输入通道。"""

    def __init__(self, elements=(), scroll_result=None, items=()):
        self.elements = list(elements)
        self.items = list(items)
        self.scroll_result = scroll_result or {
            "ok": True,
            "unit": "css-px",
            "lines": 3,
            "css_px_per_line": 32.0,
            "delta_css_px": 96.0,
            "scrolled_px": 96.0,
            "viewport_height_css_px": 780.0,
            "within_viewport": True,
            "fallback_used": False,
            "stable": True,
            "error": "",
        }
        self.invoked: list[str] = []
        self.scrolled: list[tuple[str, int]] = []

    def snapshot(self) -> PageSnapshot:
        elements = list(self.elements) + [
            _element(name, "listitem", f"i{index}")
            for index, name in enumerate(self.items)
        ]
        return PageSnapshot(elements=tuple(elements))

    def scroll_lines(self, element_id: str, lines: int):
        self.scrolled.append((element_id, lines))
        return dict(self.scroll_result)

    def invoke(self, element_id: str) -> bool:
        self.invoked.append(element_id)
        return True


def _executor(tmp_path, source: _FakeSource) -> ChromeExecutor:
    executor = ChromeExecutor(out_dir=tmp_path)
    executor._source = source
    return executor


def _scroll_step() -> Step:
    return Step(
        id="C-3.1",
        description="向下滚动 3 行",
        target=VerificationPage.JOB_LIST,
        action_kind=ActionKind.SCROLL,
        params={"lines": 3},
        expectation=Expectation(ExpectationKind.TEXT_APPEARS, VerificationPage.BACK_TO_TOP),
    )


def test_the_scroll_writes_the_readings_the_clauses_judge(tmp_path):
    """**C-3.A / C-3.B** 的读数：滚了几行 + 滚完稳不稳 + **每行多少 CSS px**。"""
    source = _FakeSource(
        elements=[_element(VerificationPage.JOB_LIST, "list", "42")]
    )
    executor = _executor(tmp_path, source)
    outcome = executor.execute(_scroll_step())
    context = executor.task_context()
    assert context["scroll_lines"] == 3
    assert context["scroll_stable"] is True
    assert context["scroll_px_per_line"] == 32.0
    assert context["scroll_unit"] == "css-px"
    assert source.scrolled == [("42", 3)]
    assert outcome.channel == "browser"


def test_the_scroll_reports_a_failed_scroll_instead_of_pretending(tmp_path):
    """滚不动 → **如实报失败**（不假装成功）。"""
    source = _FakeSource(
        elements=[_element(VerificationPage.JOB_LIST, "list", "42")],
        scroll_result={
            "ok": False,
            "unit": "css-px",
            "css_px_per_line": 32.0,
            "delta_css_px": 96.0,
            "scrolled_px": 0.0,
            "stable": True,
            "fallback_used": True,
            "error": "滚了之后位置没变",
        },
    )
    executor = _executor(tmp_path, source)
    outcome = executor.execute(_scroll_step())
    assert outcome.ok is False
    assert "位置没变" in outcome.error_message
    assert executor.task_context()["scroll_stable"] is True


def test_the_city_filter_is_verified_by_reading_the_list_back(tmp_path):
    """**C-3.C**：筛选生效 = 列表项集合变了 **且** 每项的城市字段 == 那座城市。"""
    before = ("职位 · 上海 · 30-50K", "职位 · 北京 · 20-30K")
    after = ("职位 · 上海 · 30-50K", "职位 · 上海 · 25-40K")
    source = _FakeSource(
        elements=[
            _element(VerificationPage.CITY_FILTER, "button", "7"),
            _element("上海", "button", "8"),
        ],
        items=before,
    )
    executor = _executor(tmp_path, source)
    #: **点完选项之后**列表才变成筛选后的那一批（页面自己的行为）。
    original_invoke = source.invoke

    def _invoke(element_id):
        source.items = list(after)
        return original_invoke(element_id)

    source.invoke = _invoke  # type: ignore[method-assign]
    executor._apply_city_filter("上海", before)
    context = executor.task_context()
    assert context["city_filter_applied"] is True
    assert "8" in source.invoked


def test_the_city_filter_is_not_claimed_when_the_list_did_not_change(tmp_path):
    """列表没变 → **不认**筛选生效（**不编真值**）。"""
    items = ("职位 · 上海 · 30-50K",)
    source = _FakeSource(
        elements=[_element("上海", "button", "8")],
        items=items,
    )
    executor = _executor(tmp_path, source)
    executor._apply_city_filter("上海", items)
    assert executor.task_context()["city_filter_applied"] is False


def test_the_search_results_are_read_back_from_the_list(tmp_path):
    """**C-2.B**：结果列表每条含该关键词（**结构断言**，不是「画面变了」）。"""
    source = _FakeSource(items=("高级前端工程师 · 上海 · 25-40K",))
    executor = _executor(tmp_path, source)
    executor._context_data["input_value"] = "前端工程师"
    executor._record_search_results()
    assert executor.task_context()["search_results_ok"] is True


def test_the_search_results_accept_an_explicit_empty_result(tmp_path):
    """页面明确显示「无结果」→ **也算**（工单的口径）。"""
    source = _FakeSource(
        elements=[_element("搜索结果：无结果", "paragraph", "p0")]
    )
    executor = _executor(tmp_path, source)
    executor._context_data["input_value"] = "前端工程师"
    executor._record_search_results()
    assert executor.task_context()["search_results_ok"] is True


def test_the_search_results_are_not_claimed_when_a_row_lacks_the_keyword(tmp_path):
    """有一条不含关键词 → **不认**（**不编真值**）。"""
    source = _FakeSource(
        items=(
            "高级前端工程师 · 上海 · 25-40K",
            "测试开发工程师 · 北京 · 20-30K",
        )
    )
    executor = _executor(tmp_path, source)
    executor._context_data["input_value"] = "前端工程师"
    executor._record_search_results()
    assert executor.task_context()["search_results_ok"] is False

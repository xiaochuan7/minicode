"""工单 M14 的**外部行为**单测：判定口径、报告口径与工单形状。

只测**外部可观察**的东西（工单能不能建出来、判定是什么、报告里写了什么），
不测内部算法。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from minicode.action.protocol import ActionKind
from minicode.e2e import real as real_module
from minicode.e2e.real import (
    WindowsDesktopExecutor,
    read_scale_from_registry,
    scale_percent_from_log_pixels,
)
from minicode.verification.expect import Expectation, ExpectationKind

from .checks import (
    capability_is,
    channel_visual,
    context_value,
    failure_type_coverage,
    landing_within,
)
from .report import build_report, render_markdown
from .runner import (
    VERDICT_FAIL,
    VERDICT_PASS,
    VERDICT_PENDING_HUMAN,
    VERDICT_SKIP,
    StepOutcome,
    TaskEvidence,
    TaskOutcome,
    TaskRunner,
)
from .tasks import Clause, Step, Task
from .wave1 import WAVE1_TASKS, task_by_id

# ---------------------------------------------------------------------------
# 假执行器
# ---------------------------------------------------------------------------


class FakeExecutor:
    """假的执行器：按脚本给出每一步的结果。"""

    def __init__(self, outcomes=None, preflight="", context=None, stop=""):
        self.outcomes = list(outcomes or [])
        self._preflight = preflight
        self._context = dict(context or {})
        self._stop = stop
        self.executed: list[str] = []

    def execute(self, step):
        self.executed.append(step.id)
        if self.outcomes:
            return self.outcomes.pop(0)
        return StepOutcome(step_id=step.id, description=step.description, ok=True, expectation_met=True)

    def preflight(self):
        return self._preflight

    def task_context(self):
        return dict(self._context)

    def stop_reason(self):
        return self._stop

    def handoff(self):
        if not self._stop:
            return None
        return {"reason": self._stop, "window_title": "（假执行器）"}


def _expectation(target="屏幕"):
    return Expectation(ExpectationKind.TEXT_APPEARS, target)


def _step(step_id="S-1", **kwargs):
    kwargs.setdefault("description", "做一步")
    kwargs.setdefault("target", "目标")
    kwargs.setdefault("expectation", _expectation())
    return Step(id=step_id, **kwargs)


def _task(task_id="T-1", **kwargs):
    kwargs.setdefault("title", "一张票")
    kwargs.setdefault("app", "Windows 设置")
    kwargs.setdefault("steps", (_step(),))
    return Task(id=task_id, **kwargs)


# ---------------------------------------------------------------------------
# 工单的形状：每一步都必须有结构断言
# ---------------------------------------------------------------------------


def test_step_without_structural_assertion_is_rejected():
    """没有结构断言的步骤**建不出来**（不许只用「画面变了」当判据）。"""
    with pytest.raises(ValueError, match="结构断言"):
        Step(id="S-1", description="做一步", target="目标", expectation=None)


def test_task_without_structural_assertion_is_rejected():
    """工单这一层也守着同一条不变量（步建得出来，工单也得建不出来）。"""
    bad_step = object.__new__(Step)
    object.__setattr__(bad_step, "id", "S-1")
    object.__setattr__(bad_step, "expectation", None)
    with pytest.raises(ValueError, match="结构断言"):
        Task(id="T-1", title="一张票", app="Windows 设置", steps=(bad_step,))


def test_task_without_steps_is_rejected():
    with pytest.raises(ValueError, match="一步都没有"):
        Task(id="T-1", title="一张票", app="Windows 设置", steps=())


# ---------------------------------------------------------------------------
# 判定口径
# ---------------------------------------------------------------------------


def test_all_steps_ok_and_clauses_ok_passes():
    clause = Clause("C-1", "一条条款", lambda evidence: (True, "成立"))
    task = _task(clauses=(clause,))
    outcome = TaskRunner(FakeExecutor()).run(task)
    assert outcome.verdict == VERDICT_PASS
    assert outcome.reason == ""


def test_failed_step_fails_the_task():
    executor = FakeExecutor(
        outcomes=[
            StepOutcome(
                step_id="S-1",
                description="做一步",
                ok=False,
                error_code="expectation_failed",
                error_message="期望断言失败",
            )
        ]
    )
    outcome = TaskRunner(executor).run(_task())
    assert outcome.verdict == VERDICT_FAIL
    assert "S-1" in outcome.reason


def test_expectation_not_met_fails_the_task():
    executor = FakeExecutor(
        outcomes=[
            StepOutcome(
                step_id="S-1",
                description="做一步",
                ok=True,
                expectation="文本「屏幕」出现",
                expectation_met=False,
                expectation_detail="期望：文本「屏幕」出现；实际：（空）",
            )
        ]
    )
    outcome = TaskRunner(executor).run(_task())
    assert outcome.verdict == VERDICT_FAIL
    assert "期望不成立" in outcome.reason


def test_undecidable_expectation_does_not_count_as_pass():
    """**判不了 ≠ 成立** —— 判不了的步骤不能把票算成通过。"""
    executor = FakeExecutor(
        outcomes=[
            StepOutcome(
                step_id="S-1",
                description="做一步",
                ok=True,
                expectation="文本「屏幕」出现",
                expectation_met=None,
                expectation_detail="判不了（没有能判它的手段）",
            )
        ]
    )
    outcome = TaskRunner(executor).run(_task())
    assert outcome.verdict == VERDICT_FAIL
    assert "判不了" in outcome.reason


def test_failing_clause_fails_the_task():
    clause = Clause("C-1", "一条条款", lambda evidence: (False, "差 3 px"))
    outcome = TaskRunner(FakeExecutor()).run(_task(clauses=(clause,)))
    assert outcome.verdict == VERDICT_FAIL
    assert "C-1" in outcome.reason
    assert "差 3 px" in outcome.reason


def test_human_confirm_task_is_not_executed():
    """**人工代确认的任务不自动执行**。"""
    executor = FakeExecutor()
    outcome = TaskRunner(executor).run(_task(human_confirm=True))
    assert outcome.verdict == VERDICT_PENDING_HUMAN
    assert executor.executed == []


def test_step_level_human_confirm_is_not_executed():
    """**步级**的人工代确认同样不执行（不许只拦任务级）。"""
    executor = FakeExecutor()
    task = _task(steps=(_step(step_id="S-1", human_confirm=True),))
    outcome = TaskRunner(executor).run(task)
    assert outcome.verdict == VERDICT_PENDING_HUMAN
    assert executor.executed == []
    assert "S-1" in outcome.reason


def test_manual_result_is_recorded_but_never_counted_as_auto_pass():
    """人代跑的结果**带进报告**，但**不混进自动通过率**。"""
    executor = FakeExecutor()
    runner = TaskRunner(
        executor,
        manual_results={
            "T-1": {"verdict": "pass", "note": "人代跑了", "context": {"scale_value": "125%"}}
        },
    )
    outcome = runner.run(_task(human_confirm=True))
    assert outcome.verdict == VERDICT_PENDING_HUMAN
    assert outcome.manual["verdict"] == "pass"
    assert "人代跑了" in outcome.reason
    report = build_report([outcome])
    assert report["auto"]["passed"] == 0
    assert report["auto"]["denominator"] == 0
    assert report["human_confirm"]["results"][0]["verdict"] == "pass"


def test_stop_condition_stops_the_task_and_hands_off():
    """**停止条件**命中就停下来，并交出**交接产物**（工单 M14 的阻塞边 M12）。"""
    executor = FakeExecutor(stop="asked_human")
    task = _task(steps=(_step(step_id="S-1"), _step(step_id="S-2")))
    outcome = TaskRunner(executor).run(task)
    assert executor.executed == ["S-1"]
    assert "停止条件命中（asked_human）" in outcome.reason
    assert outcome.handoff is not None
    assert outcome.handoff["reason"] == "asked_human"


def test_static_skip_reason_skips_without_executing():
    executor = FakeExecutor()
    outcome = TaskRunner(executor).run(_task(skip_reason="环境不可达：应用未装"))
    assert outcome.verdict == VERDICT_SKIP
    assert outcome.skip_reason == "环境不可达：应用未装"
    assert executor.executed == []


def test_preflight_failure_skips_with_environment_reason():
    executor = FakeExecutor(preflight="环境不可达：未登录")
    outcome = TaskRunner(executor).run(_task())
    assert outcome.verdict == VERDICT_SKIP
    assert outcome.skip_reason == "环境不可达：未登录"
    assert executor.executed == []


# ---------------------------------------------------------------------------
# 缩放值：先读树，读不到就用**系统状态读数**兜底（改动 6）
# ---------------------------------------------------------------------------


class _FakeUiaPort:
    """假的 UIA 查询端口：每次查询都返回同一组控件。"""

    def __init__(self, elements=()):
        self._elements = list(elements)

    def find(self, request):
        return SimpleNamespace(elements=list(self._elements))


def _scale_executor(tmp_path, elements=()):
    """一台只用来读缩放值的执行器（**不碰真桌面**）。"""
    executor = WindowsDesktopExecutor(out_dir=tmp_path)
    executor._port = _FakeUiaPort(elements)
    return executor


def test_log_pixels_maps_to_the_documented_scale_values():
    """注册表里的 DPI → 缩放百分比（96/120/144 → 100/125/150%）。"""
    assert scale_percent_from_log_pixels(96) == "100%"
    assert scale_percent_from_log_pixels(120) == "125%"
    assert scale_percent_from_log_pixels(144) == "150%"
    assert scale_percent_from_log_pixels(108) == "112.5%"


def test_the_registry_reading_is_a_percentage_or_nothing():
    """真机读注册表：要么读到「N%」，要么读不到（**不许读出别的东西**）。"""
    reading = read_scale_from_registry()
    assert reading is None or reading[0].endswith("%")
    assert reading is None or "注册表" in reading[1]


def test_scale_is_read_from_the_tree_when_the_tree_has_it(tmp_path):
    """树里读得到就用树里的（**来源如实记**）。"""
    executor = _scale_executor(
        tmp_path, [SimpleNamespace(name="缩放 150%", value="", automation_id="")]
    )
    executor._record_scale(SimpleNamespace(anchor=None))
    context = executor.task_context()
    assert context["scale_value"] == "150%"
    assert context["scale_percent"] == 150
    assert "控件名" in context["scale_source"]


def test_scale_falls_back_to_the_system_reading_when_the_tree_is_empty(
    tmp_path, monkeypatch
):
    """**树读失败 → 用系统状态读数兜底**（UWP 的树里没有那个百分比）。"""
    monkeypatch.setattr(
        real_module,
        "read_scale_from_registry",
        lambda: ("125%", "注册表 HKCU\\Control Panel\\Desktop\\LogPixels（120 → 125%）"),
    )
    executor = _scale_executor(tmp_path)
    executor._record_scale(SimpleNamespace(anchor=None))
    context = executor.task_context()
    assert context["scale_value"] == "125%"
    assert context["scale_percent"] == 125
    assert "注册表" in context["scale_source"]
    assert "读不到" in context["scale_note"]


def test_scale_falls_back_to_the_system_dpi_when_the_registry_has_nothing(
    tmp_path, monkeypatch
):
    """注册表读不到 → 再退到系统 DPI 读数（还是读不到就**如实记空**）。"""
    monkeypatch.setattr(real_module, "read_scale_from_registry", lambda: None)
    monkeypatch.setattr(
        real_module, "read_scale_from_system_dpi", lambda: ("150%", "系统 DPI 读数")
    )
    executor = _scale_executor(tmp_path)
    executor._record_scale(SimpleNamespace(anchor=None))
    assert executor.task_context()["scale_value"] == "150%"
    assert "系统 DPI" in executor.task_context()["scale_source"]


def test_scale_is_recorded_empty_when_nothing_can_be_read(tmp_path, monkeypatch):
    """**都读不到就如实记空** —— 判不了 ≠ 不成立，也不许拿别处的值充数。"""
    monkeypatch.setattr(real_module, "read_scale_from_registry", lambda: None)
    monkeypatch.setattr(real_module, "read_scale_from_system_dpi", lambda: None)
    executor = _scale_executor(tmp_path)
    executor._record_scale(SimpleNamespace(anchor=None))
    context = executor.task_context()
    assert context["scale_value"] == ""
    assert "读不到" in context["scale_note"]


# ---------------------------------------------------------------------------
# 报告口径
# ---------------------------------------------------------------------------


def test_report_excludes_skipped_and_pending_from_pass_rate():
    passed = TaskOutcome(task_id="T-1", title="过", verdict=VERDICT_PASS)
    failed = TaskOutcome(task_id="T-2", title="败", verdict=VERDICT_FAIL, reason="差")
    skipped = TaskOutcome(
        task_id="T-3",
        title="跳",
        verdict=VERDICT_SKIP,
        reason="环境不可达：未登录",
        skip_reason="环境不可达：未登录",
    )
    pending = TaskOutcome(
        task_id="T-4", title="待", verdict=VERDICT_PENDING_HUMAN, human_confirm=True
    )
    report = build_report([passed, failed, skipped, pending])
    auto = report["auto"]
    assert auto["passed"] == 1
    assert auto["failed"] == 1
    assert auto["skipped"] == 1
    assert auto["denominator"] == 2
    assert auto["pass_rate"] == 0.5
    assert report["human_confirm"]["count"] == 1
    assert report["human_confirm"]["ids"] == ["T-4"]
    assert report["skipped"][0]["reason"] == "环境不可达：未登录"


def test_report_never_counts_skip_as_pass():
    skipped = TaskOutcome(
        task_id="T-3",
        title="跳",
        verdict=VERDICT_SKIP,
        reason="环境不可达：应用未装",
        skip_reason="环境不可达：应用未装",
    )
    report = build_report([skipped])
    assert report["auto"]["passed"] == 0
    assert report["auto"]["pass_rate"] is None
    assert all(task["verdict"] != VERDICT_PASS for task in report["tasks"])


def test_report_records_channel_timings_and_landing_error():
    outcome = TaskOutcome(
        task_id="T-1",
        title="一张票",
        verdict=VERDICT_FAIL,
        reason="差",
        steps=(
            StepOutcome(
                step_id="S-1",
                description="做一步",
                ok=True,
                channel="visual",
                failure_type="coverage",
                failures={"locate": None, "decision": None, "execute": None},
                expectation="文本「屏幕」出现",
                expectation_met=True,
                locate_ms=12.0,
                decision_ms=0.0,
                execute_verify_ms=34.0,
                landing_point=(667, 432),
                landing_error_px=1.5,
                truth_source="树里的控件框「屏幕」",
            ),
        ),
    )
    report = build_report([outcome])
    step = report["tasks"][0]["steps"][0]
    assert step["channel"] == "visual"
    assert step["failure_type"] == "coverage"
    assert step["failures"] == {"locate": None, "decision": None, "execute": None}
    assert step["locate_ms"] == 12.0
    assert step["execute_verify_ms"] == 34.0
    assert step["landing_error_px"] == 1.5
    assert step["truth_source"] == "树里的控件框「屏幕」"
    assert "visual" in render_markdown(report)


# ---------------------------------------------------------------------------
# 验收条款的判据
# ---------------------------------------------------------------------------


def _evidence(steps, context=None):
    return TaskEvidence(task=_task(), steps=tuple(steps), context=dict(context or {}))


def test_channel_visual_clause_needs_a_visual_step():
    ok, detail = channel_visual(_evidence([StepOutcome(step_id="S-1", channel="uia")]))
    assert not ok
    assert "视觉通道" in detail
    ok, _ = channel_visual(_evidence([StepOutcome(step_id="S-1", channel="visual")]))
    assert ok


def test_failure_type_coverage_clause_rejects_other_failure_types():
    ok, detail = failure_type_coverage(
        _evidence([StepOutcome(step_id="S-1", channel="uia", failure_type="selection")])
    )
    assert not ok
    assert "selection" in detail
    ok, _ = failure_type_coverage(
        _evidence([StepOutcome(step_id="S-1", channel="uia", failure_type="coverage")])
    )
    assert ok
    ok, detail = failure_type_coverage(_evidence([StepOutcome(step_id="S-1", channel="uia")]))
    assert not ok
    assert "没有" in detail


def test_landing_within_clause_is_undecidable_without_truth():
    ok, detail = landing_within(2.0)(
        _evidence([StepOutcome(step_id="S-1", landing_point=(10, 10), landing_error_px=None)])
    )
    assert not ok
    assert "判不了" in detail
    ok, _ = landing_within(2.0)(
        _evidence([StepOutcome(step_id="S-1", landing_point=(10, 10), landing_error_px=1.9)])
    )
    assert ok
    ok, detail = landing_within(2.0)(
        _evidence([StepOutcome(step_id="S-1", landing_point=(10, 10), landing_error_px=2.1)])
    )
    assert not ok
    assert "2.1" in detail


def test_capability_clause_compares_the_capability_table():
    ok, _ = capability_is("unavailable")(_evidence([], {"capability": "unavailable"}))
    assert ok
    ok, detail = capability_is("unavailable")(_evidence([], {"capability": "sparse"}))
    assert not ok
    assert "sparse" in detail


def test_context_clause_reports_unmeasured_as_undecidable():
    """**缺测量 ≠ 不成立**：上下文里没有那个键 → 判「判不了」。"""
    ok, detail = context_value("clipboard_restored", True)(_evidence([]))
    assert not ok
    assert "没测过" in detail
    ok, _ = context_value("clipboard_restored", True)(
        _evidence([], {"clipboard_restored": True})
    )
    assert ok
    ok, detail = context_value("clipboard_restored", True)(
        _evidence([], {"clipboard_restored": False})
    )
    assert not ok
    assert "False" in detail


# ---------------------------------------------------------------------------
# 第一波的六张票
# ---------------------------------------------------------------------------


def test_wave1_has_six_tasks_with_the_expected_ids():
    assert [task.id for task in WAVE1_TASKS] == ["W-1", "W-2", "W-3", "C-1", "C-2", "C-3"]


def test_every_wave1_step_has_a_structural_assertion():
    for task in WAVE1_TASKS:
        assert task.steps, task.id
        for step in task.steps:
            assert step.expectation is not None, f"{task.id}/{step.id}"


def test_w2_is_human_confirm():
    """改系统缩放那张票是**人工代确认**（不许自动执行）。"""
    task = task_by_id("W-2")
    assert task.human_confirm is True
    assert task.steps[0].human_confirm is True
    assert TaskRunner(FakeExecutor()).run(task).verdict == VERDICT_PENDING_HUMAN


def test_w3_zoom_step_uses_the_zoom_action_and_a_region():
    step = task_by_id("W-3").steps[0]
    assert step.action_kind is ActionKind.ZOOM
    assert len(step.params["region"]) == 4

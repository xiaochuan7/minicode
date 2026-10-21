"""**第一波端到端工单**（工单 M14）：Windows 设置（W-1~W-3）+ Chrome（C-1~C-3）。

期望结果的**真值来源**是 :file:`docs/target-tasks.md`（目标应用任务清单）——
这里只把它拆成「一步 + 一条结构断言 + 若干验收条款」，**不复制口径**。

> ⚠️ **两条现实**（2026-10-21 在本机实测，详见 :file:`docs/reviews/M14.md`）：
>
> 1. 本机这个 Windows 11 build（26100）里「显示」那张卡片叫 **「屏幕」**
>    （副标题：显示器、亮度、夜间模式、显示器配置文件）。工单字面写「显示」，
>    这里**照工单字面**写目标描述（「显示」不在 UIA 树里 → 覆盖失败 → 切视觉，
>    这正是 W-1 要验的那条路径），真值则取「屏幕」那张卡片的标题文本中心。
> 2. **C-1~C-3 需要 BOSS 直聘的登录态**。浏览器通道起的是**全新会话**
>    （Playwright 的新 context），拿不到登录态 → 那三张票按
>    :file:`docs/target-tasks.md` §7.1 记 **SKIP** 并写明原因。
"""

from __future__ import annotations

from minicode.action.ports import CHANNEL_BROWSER
from minicode.action.protocol import ActionKind
from minicode.verification.expect import Expectation, ExpectationKind

from .checks import (
    capability_is,
    channel_is,
    channel_visual,
    context_value,
    failure_type_coverage,
    landing_within,
    zoom_fresh_capture,
    zoom_landing_inside_region,
)
from .tasks import Clause, Step, Task

__all__ = ["WAVE1_TASKS", "task_by_id"]

#: 落点偏差的验收线（物理像素）。目标应用任务清单 §0 的通用判据。
LANDING_TOLERANCE_PX = 2.0

#: 屏幕右下角那块放大区域（物理像素）：任务栏的时钟所在处。
TRAY_REGION = (1900, 1400, 2560, 1600)

#: 时钟控件的中心（**从画面量出来的**：那块区域里亮像素的包围盒中心，
#: 2026-10-21 04:35 实测；包围盒是 x 2476~2529 / y 1545~1583 ——
#: 时分两行文字合起来才是那个可点的时钟控件）。
CLOCK_CENTER = (2502, 1564)


def _text(target: str) -> Expectation:
    """一段文本**出现**（结构断言的一种）。"""
    return Expectation(ExpectationKind.TEXT_APPEARS, target)


# ---------------------------------------------------------------------------
# W-1 ~ W-3：Windows 设置
# ---------------------------------------------------------------------------


W1 = Task(
    id="W-1",
    title="打开 Windows 设置，进入「系统 → 显示」，读出当前缩放值并回显",
    app="Windows 设置",
    steps=(
        Step(
            id="W-1.1",
            description="在设置左侧导航点「系统」",
            target="系统",
            expectation=_text("屏幕"),
            truth_element="系统",
            truth_control_type="ListItem",
        ),
        Step(
            id="W-1.2",
            description="点「系统」页里的显示设置卡片（本机叫「屏幕」，工单字面写「显示」）",
            target="显示",
            expectation=_text("缩放"),
            truth_element="屏幕",
            truth_control_type="Text",
        ),
    ),
    clauses=(
        Clause(
            "W-1.A",
            "追踪记录里通道 = 视觉通道",
            channel_visual,
        ),
        Clause(
            "W-1.B",
            "UIA 侧失败类型 = 覆盖失败",
            failure_type_coverage,
        ),
        Clause(
            "W-1.C",
            f"落点与目标控件中心偏差 ≤{LANDING_TOLERANCE_PX:.0f} px",
            landing_within(LANDING_TOLERANCE_PX),
        ),
        Clause(
            "W-1.D",
            "能力表里 Windows 设置 = 不可用",
            capability_is("unavailable"),
        ),
        Clause(
            "W-1.E",
            "读出的缩放值与界面显示一致",
            context_value("scale_value", nonempty=True),
        ),    ),
)


W2 = Task(
    id="W-2",
    title="把缩放从当前值改成 125%（改系统设置 → 人工代确认）",
    app="Windows 设置",
    human_confirm=True,
    steps=(
        Step(
            id="W-2.1",
            description="在显示设置里选 125% 那一档",
            target="125%",
            expectation=_text("125%"),
            truth_element="125%",
            human_confirm=True,
        ),
    ),
    clauses=(
        Clause(
            "W-2.A",
            "改动后重新抓图能读到 125%",
            context_value("scale_value", "125%"),
        ),
        Clause(
            "W-2.B",
            "全程没有把坐标挪到最近显示器（越界一律报错，不夹取）",
            context_value("coordinate_clamped", False),
        ),
        Clause(
            "W-2.C",
            "缩放变更后要么重新自检后落点仍 ≤2 px，要么明确报错要求重新自检",
            context_value("reselfcheck_required", False),
        ),
    ),
)


W3 = Task(
    id="W-3",
    title="对屏幕右下角一块区域执行放大动作，再在其中点击一个已知控件",
    app="Windows 设置 / 任务栏",
    steps=(
        Step(
            id="W-3.1",
            description=f"放大屏幕右下角 {TRAY_REGION} 这一块，找里面的时钟",
            target="时钟",
            action_kind=ActionKind.ZOOM,
            params={"region": list(TRAY_REGION)},
            expectation=_text("时钟"),
            truth=CLOCK_CENTER,
            window="taskbar",
        ),
        Step(
            id="W-3.2",
            description="在放大的那一块里点击时钟（用放大给出的整屏坐标）",
            target="时钟",
            expectation=_text("2026/10/21"),
            truth=CLOCK_CENTER,
            window="taskbar",
            use_previous_landing=True,
        ),
    ),
    clauses=(
        Clause("W-3.A", "放大是回源重抓（不是把旧图放大）", zoom_fresh_capture),
        Clause(
            "W-3.B",
            "放大后模型报的是**图内坐标**、系统加这块区域的物理原点映射回整屏"
            "（落点必须落在这块区域内）",
            zoom_landing_inside_region,
        ),
        Clause(
            "W-3.C",
            f"落点映射回整屏后与控件中心偏差 ≤{LANDING_TOLERANCE_PX:.0f} px",
            landing_within(LANDING_TOLERANCE_PX),
        ),
    ),
)


# ---------------------------------------------------------------------------
# C-1 ~ C-3：Chrome（BOSS 直聘）
# ---------------------------------------------------------------------------


C1 = Task(
    id="C-1",
    title="打开 BOSS 直聘职位列表页，按职位名点开第一屏的一个职位详情",
    app="Chrome",
    steps=(
        Step(
            id="C-1.1",
            description="点开第一屏的一个职位链接",
            target="职位名称链接",
            expectation=_text("职位描述"),
            window="browser",
        ),
    ),
    clauses=(
        Clause(
            "C-1.A",
            "动作前后的 DOM 快照差异里目标链接消失且详情块出现",
            context_value("page_snapshot_diff_ok", True),
        ),
        Clause(
            "C-1.B",
            "定位依据是页面结构（走浏览器通道）",
            channel_is(CHANNEL_BROWSER),
        ),
    ),
)


C2 = Task(
    id="C-2",
    title="在搜索框输入中文关键词「前端工程师」并提交",
    app="Chrome",
    steps=(
        Step(
            id="C-2.1",
            description="在搜索框输入「前端工程师」",
            target="搜索框",
            action_kind=ActionKind.TYPE,
            params={"text": "前端工程师"},
            expectation=_text("前端工程师"),
            window="browser",
        ),
        Step(
            id="C-2.2",
            description="提交搜索",
            target="搜索",
            expectation=_text("前端工程师"),
            window="browser",
        ),
    ),
    clauses=(
        Clause("C-2.A", "输入框最终值 == 目标中文关键词", context_value("input_value", "前端工程师")),
        Clause(
            "C-2.B",
            "提交后结果列表每条含该关键词，或页面明确显示「无结果」",
            context_value("search_results_ok", True),
        ),
        Clause("C-2.C", "剪贴板用过后被恢复", context_value("clipboard_restored", True)),
    ),
)


C3 = Task(
    id="C-3",
    title="向下滚动 3 格并把筛选条件设为「城市 = 上海」",
    app="Chrome",
    steps=(
        Step(
            id="C-3.1",
            description="向下滚动 3 行",
            target="职位列表",
            action_kind=ActionKind.SCROLL,
            params={"lines": 3},
            expectation=_text("职位描述"),
            window="browser",
        ),
        Step(
            id="C-3.2",
            description="把筛选条件设为「城市 = 上海」",
            target="城市",
            expectation=_text("上海"),
            window="browser",
        ),
    ),
    clauses=(
        Clause("C-3.A", "滚动以行为单位（3 行滚动不跳过整屏）", context_value("scroll_lines", 3)),
        Clause(
            "C-3.B",
            "滚动后界面已稳定再继续（惯性未结束时不再取位置）",
            context_value("scroll_stable", True),
        ),
        Clause("C-3.C", "筛选生效后列表项集合变化（城市 == 上海）", context_value("city_filter_applied", True)),
    ),
)


#: 第一波的全部工单（**就绪边上的六张**）。
WAVE1_TASKS: tuple[Task, ...] = (W1, W2, W3, C1, C2, C3)


def task_by_id(task_id: str) -> Task:
    """按编号取一张工单。"""
    for task in WAVE1_TASKS:
        if task.id == task_id:
            return task
    raise KeyError(f"没有编号为 {task_id!r} 的工单")

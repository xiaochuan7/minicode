"""**第一波端到端工单**（工单 M14）：Windows 设置（W-1~W-3）+ Chrome（C-1~C-3）。

期望结果的**真值来源**是目标应用任务清单 ——
这里只把它拆成「一步 + 一条结构断言 + 若干验收条款」，**不复制口径**。

> ⚠️ **两条现实**（2026-10-21 在本机实测）：
>
> 1. 本机这个 Windows 11 build（26100）里「显示」那张卡片叫 **「屏幕」**
>    （副标题：显示器、亮度、夜间模式、显示器配置文件）。工单字面写「显示」，
>    这里**照工单字面**写目标描述（「显示」不在 UIA 树里 → 覆盖失败 → 切视觉，
>    这正是 W-1 要验的那条路径），真值则取「屏幕」那张卡片的标题文本中心。
> 2. **C-1~C-3 原本需要 BOSS 直聘的登录态**（2026-10-21 实测：浏览器通道起的是
>    **全新会话** → 那三张票记 **SKIP**）。
>    **工单 T8 之后**：浏览器通道改走**持久化上下文**（复用登录态），
>    而这三张票的**运行环境**换成了**本地验证页**（浏览器通道按**可见文本**
>    定位 —— 「职位名称链接」那种角色描述在真实站点上没有对应的可见文本）。
>    工单的**验收条款一个字没改**。
>
> **落点那一条是三档**（A 命中 / B 近失 / C 错选）：口径与常量在
> :mod:`minicode.e2e.checks`（:data:`LANDING_HIT_RULE` /
> :data:`LANDING_NEAR_MISS_PX` / :data:`LANDING_ERROR_PX_VERSION`）——
> 真值是「**中心点 + 控件矩形**」；树里没有那个控件的框 → 记
> ``undecidable``（**不编一个真值**）。
"""

from __future__ import annotations

from minicode.action.ports import CHANNEL_BROWSER
from minicode.action.protocol import ActionKind
from minicode.verification.expect import Expectation, ExpectationKind

from .apps import VerificationPage
from .checks import (
    LANDING_ERROR_PX_VERSION,
    LANDING_HIT_RULE,
    LANDING_NEAR_MISS_PX,
    capability_is,
    channel_is,
    channel_visual,
    context_value,
    failure_type_coverage,
    landing_in_target_rect,
    landing_rule_summary,
    zoom_fresh_capture,
    zoom_landing_inside_region,
)
from .tasks import Clause, Step, Task

__all__ = [
    "WAVE1_TASKS",
    "task_by_id",
    "LANDING_HIT_RULE",
    "LANDING_NEAR_MISS_PX",
    "LANDING_ERROR_PX_VERSION",
    "landing_rule_summary",
]

#: 屏幕右下角那块放大区域（物理像素）：任务栏的时钟所在处。
TRAY_REGION = (1900, 1400, 2560, 1600)

#: 时钟控件的**包围盒**（**从画面量出来的**：那块区域里亮像素的包围盒，
#: 2026-10-21 04:35 实测；x 2476~2529 / y 1545~1583 ——
#: 时分两行文字合起来才是那个可点的时钟控件）。
CLOCK_RECT = (2476, 1545, 2529, 1583)

#: 时钟控件的**中心** = **包围盒中心**（W-3 的「目标中心」口径：
#: 用包围盒中心，**不是**语义中心）。
CLOCK_CENTER = (
    (CLOCK_RECT[0] + CLOCK_RECT[2]) // 2,
    (CLOCK_RECT[1] + CLOCK_RECT[3]) // 2,
)


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
            f"落点在目标控件矩形内（出框 ≤{LANDING_NEAR_MISS_PX:.0f} px 记近失，"
            "更远判失败；没有控件框真值就记判不了）",
            landing_in_target_rect,
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
    title="把缩放从当前值改成 125%（改系统设置 → agent 代确认：自己改 + 自己读回）",
    app="Windows 设置",
    #: ⚠️ **原来这张票是「人工代确认」**（不改系统设置）→
    #: 改成 **agent 代确认**：agent **自己改 + 自己读回**
    #: （**不写成阻塞项，也不计入失败**）。
    #:
    #: ⚠️ **与工单 M14 的 AC 有冲突**：那张 AC 写着「任何情况下不得修改系统显示
    #: 缩放」——那是**上一轮**的策略（人工代确认）。本轮按用户的授权改成
    #: agent 代确认；**工单文本一个字没动**（改 AC 要等 agent 停下后由人做）。
    human_confirm=True,
    steps=(
        Step(
            id="W-2.1",
            description="点开「缩放」那一下拉（选项要出来）",
            target="缩放",
            expectation=_text("125%"),
            window="settings",
        ),
        Step(
            id="W-2.2",
            description="选 125% 那一档（改完**自己重新抓图**读回）",
            target="125%",
            #: **结构断言 = agent 自己重新抓图 + OCR 读回的缩放值**
            #: （工单 M14 的 W-2.A：「改动后重新抓图能读到 125%」）。
            expect_context=("scale_value", "125%"),
            truth_element="125%",
            human_confirm=True,
            window="settings",
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
            truth_rect=CLOCK_RECT,
            window="taskbar",
        ),
        Step(
            id="W-3.2",
            description="在放大的那一块里点击时钟（用放大给出的整屏坐标）",
            target="时钟",
            expectation=_text("2026/10/21"),
            truth=CLOCK_CENTER,
            truth_rect=CLOCK_RECT,
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
            f"落点映射回整屏后落在时钟的包围盒内（出框 ≤{LANDING_NEAR_MISS_PX:.0f} px "
            "记近失，更远判失败）",
            landing_in_target_rect,
        ),
    ),
)


# ---------------------------------------------------------------------------
# C-1 ~ C-3：Chrome（浏览器通道）
# ---------------------------------------------------------------------------
#
# ⚠️ **目标应用从 BOSS 直聘换成了「本地验证页」**（工单 T8）：
#
# * 浏览器通道**按可见文本定位**（``action/text.py::name_matches``）——
#   「职位名称链接」这种**角色描述**在页面上**没有对应的可见文本** →
#   在任何真实站点上都定位不到（BOSS 直聘的职位链接叫「资深后端工程师」之类）；
# * 本地验证页（:mod:`minicode.e2e.apps`）里有**可滚的职位列表 + 搜索框 +
#   城市筛选下拉**（工单 T8 的三件套）→ 这三张票有一个**确定的**运行环境，
#   不再因为「未登录」永远 SKIP；
# * 工单的**验收条款一个字没改**：条款判的仍是
#   「结构断言」（快照差异 / 输入框取值 / 列表项集合变化）。
#:
# ⚠️ **期望（结构断言）跟着页面结构走**：工单原话里那几个「职位描述」类的
# 断言在这里对应到页面上**动作之后才出现**的元素（搜索建议 / 搜索结果 /
# 回到顶部 / 选择城市）—— 判据的**形状**没变（还是「文本出现」）。


C1 = Task(
    id="C-1",
    title="打开 BOSS 直聘职位列表页，按职位名点开第一屏的一个职位详情",
    app="Chrome",
    steps=(
        Step(
            id="C-1.1",
            description="点开第一屏的一个职位链接",
            #: **按职位名**点（工单原话「按职位名点开第一屏的一个职位详情」）。
            target=VerificationPage.JOBS[0][0],
            #: 角色 = 链接（列表项本身也叫这个名字 → 按角色分开）。
            control_type="link",
            expectation=_text(VerificationPage.JOB_DETAIL),
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
            target=VerificationPage.SEARCH_BOX,
            action_kind=ActionKind.TYPE,
            params={"text": "前端工程师"},
            expectation=_text(VerificationPage.SUGGESTION),
            window="browser",
        ),
        Step(
            id="C-2.2",
            description="提交搜索",
            target=VerificationPage.SEARCH_BUTTON,
            expectation=_text(VerificationPage.RESULT_SUMMARY),
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
            target=VerificationPage.JOB_LIST,
            action_kind=ActionKind.SCROLL,
            params={"lines": 3},
            expectation=_text(VerificationPage.BACK_TO_TOP),
            window="browser",
        ),
        Step(
            id="C-3.2",
            description="把筛选条件设为「城市 = 上海」",
            target=VerificationPage.CITY_FILTER,
            expectation=_text(VerificationPage.CITY_PANEL),
            #: **筛选要设成哪座城市**（执行器按它去点下拉框里的选项）。
            params={"city": "上海"},
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

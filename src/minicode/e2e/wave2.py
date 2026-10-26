"""**第二波端到端工单**（工单 M15）：微信（WX-1~WX-3）+ DSH/Tabbit（E-1~E-3）
+ cc-switch（CC-1~CC-3）。

期望结果的**真值来源**是目标应用任务清单 ——
这里只把它拆成「一步 + 一条结构断言 + 若干验收条款」，**不复制口径**。

> ⚠️ **四条现实**（2026-10-21 在本机实测）：
>
> 1. **本机微信 4.1.15 的聊天内容不在可访问性树里** —— 树只给外壳
>    （导航、会话列表、标题栏），聊天区与输入框都没有；**窗口标题也不随
>    联系人变**（进入聊天后仍是「微信」）。→ WX-1 的第三条断言按
>    **窗口标题**写（本机**不成立**，如实记）。
> 2. **本机微信的会话条目名字是多行大 blob**，干净的人名只在
>    ``automation_id = session_item_<人名>`` 里；裸名字查询**必然 0 命中**
>    （而且 ``unmatched`` 也是 0 → 会被误判成「覆盖失败」）。
>    → 选联系人走 **automation_id**，定位走「名字 + 控件类型」。
> 3. **本机 cc-switch 的应用窗口标题是「CC Switch」**（工单里
>    写的 ``com.ccswitch.desktop-siw`` 是那个 22×22 的**托盘辅助窗**，
>    树里 0 个节点）；**选中态在 UIA 里读不出来**（只有 class_name 里的
>    ``bg-blue-500 text-white`` 是弱信号）→ 「当前配置」的读数取**应用
>    自己的状态库**（``providers.is_current``）。
> 4. **DSH Web GUI 需要本机鉴权令牌**（明文 URL 是 401）→ E-1/E-3 用
>    **评测台自带的本地验证页**（同样是 Electron/Chromium 形态）跑，
>    DSH 那个应用本身本轮没驱动（如实记录）。
"""

from __future__ import annotations

from minicode.action.protocol import ActionKind
from minicode.verification.expect import Expectation, ExpectationKind

from .apps import PICKED, VerificationPage
from .checks import (
    LANDING_ERROR_PX_VERSION,
    LANDING_HIT_RULE,
    LANDING_NEAR_MISS_PX,
    capability_usable_or_cut_visual,
    channel_and_failure_readable,
    context_matches_picked,
    context_value,
    coordinates_stayed_in_window,
    cross_stack_semantics_stable,
    failure_types_separated,
    gate_unlocked_before_unavailable,
    input_channel_recorded,
    landing_in_target_rect,
    landing_rule_summary,
    modal_handoff_ok,
    page_title_is,
    structure_channel_preferred,
    verification_source_is_structure,
    window_title_matches_picked,
)
from .runner import TaskEvidence
from .tasks import Clause, Step, Task

__all__ = [
    "WAVE2_TASKS",
    "task_by_id",
    "LANDING_HIT_RULE",
    "LANDING_NEAR_MISS_PX",
    "LANDING_ERROR_PX_VERSION",
    "landing_rule_summary",
    "capability_recorded_and_landing_in_picked",
]

#: E-3 往会话输入框里打的那段中文（含 emoji —— 代理对要正确）。
E3_TEXT = "端到端评测第二波😀"


def _text(target: str) -> Expectation:
    """一段文本**出现**（结构断言的一种）。"""
    return Expectation(ExpectationKind.TEXT_APPEARS, target)


# ---------------------------------------------------------------------------
# WX-1 ~ WX-3：微信
# ---------------------------------------------------------------------------

WX1 = Task(
    id="WX-1",
    title="打开微信，在搜索框输入联系人名并进入聊天",
    app="微信",
    steps=(
        Step(
            id="WX-1.1",
            description="在搜索框里输入联系人名",
            target="搜索",
            control_type="Edit",
            action_kind=ActionKind.TYPE,
            params={"text": PICKED, "focus_target": True},
            expect_control_value=PICKED,
            note="联系人名在跑的时候从会话列表里挑（工单不钉具体人）",
        ),
        Step(
            id="WX-1.2",
            description="点开那个联系人，进入聊天",
            target=PICKED,
            control_type="ListItem",
            expect_window_title=PICKED,
            note="会话条目的名字是多行 blob，定位靠「名字 + 控件类型」",
        ),
    ),
    clauses=(
        Clause(
            "WX-1.A",
            "树被门控时先尝试解开 + 退避重试，再判定不可用",
            gate_unlocked_before_unavailable,
        ),
        Clause(
            "WX-1.B",
            "重试成功记为可用；仍不可用记为不可用并按覆盖失败切视觉",
            capability_usable_or_cut_visual,
        ),
        Clause(
            "WX-1.C",
            "进入聊天后窗口标题 == 联系人名",
            window_title_matches_picked,
        ),
    ),
)


WX2 = Task(
    id="WX-2",
    title="在聊天输入框输入一段中文（含 emoji）并发送（发消息 → 人工代确认）",
    app="微信",
    human_confirm=True,
    steps=(
        Step(
            id="WX-2.1",
            description="在聊天输入框输入中文（含 emoji）",
            target="聊天输入框",
            action_kind=ActionKind.TYPE,
            params={"text": E3_TEXT, "focus_target": True},
            expect_control_value=E3_TEXT,
            human_confirm=True,
        ),
        Step(
            id="WX-2.2",
            description="发送",
            target="聊天输入框",
            action_kind=ActionKind.KEY,
            params={"combo": ["enter"]},
            expectation=_text(E3_TEXT),
            human_confirm=True,
        ),
    ),
    clauses=(
        Clause(
            "WX-2.A",
            "中文与 emoji 正确注入（代理对正确）",
            context_value("unicode_injection_ok", True),
        ),
        Clause(
            "WX-2.B",
            "发送后该文本出现在会话里",
            context_value("message_visible", True),
        ),
        Clause(
            "WX-2.C",
            "一次发送只产生一条消息（不可逆动作没有被自动重试）",
            context_value("message_count", 1),
        ),
    ),
)


WX3 = Task(
    id="WX-3",
    title="微信弹出模态弹窗时：零动作下发 + 交接给人",
    app="微信",
    note=(
        "**本轮的模态弹窗是评测台自己造的**（系统消息框，挂在微信窗口上 →"
        "宿主窗口被禁用 = 真模态）；微信自己的「更新提示 / 登录失效」弹窗属于"
        "**人工代确认**步骤，本轮未执行。"
    ),
    steps=(
        Step(
            id="WX-3.1",
            description="在模态弹窗在场的情况下执行一步动作（不许有像素被点）",
            target="搜索",
            control_type="Edit",
            expectation=_text("搜索"),
        ),
    ),
    clauses=(
        Clause("WX-3.A", "检测到模态弹窗后零动作下发 + 交人 + 交接产物齐", modal_handoff_ok),
        Clause("WX-3.B", "停止原因是「模态弹窗」", context_value("stop_reason", "modal_dialog")),
    ),
)


# ---------------------------------------------------------------------------
# E-1 ~ E-3：DSH / Tabbit（Electron）
# ---------------------------------------------------------------------------

E1 = Task(
    id="E-1",
    title="在设置面板里把一项设置改成低档并保存",
    app="DSH/Tabbit",
    note=(
        "**本轮跑在评测台自带的本地验证页上**（同样是 Chromium/Electron 形态："
        "扁平 DOM、页面结构定位、浏览器自己的输入通道）；**DSH Web GUI 本体没驱动**"
        "（它的本地服务要本机鉴权令牌）→ 这一票验的是**通道与断言机制**，"
        "不是 DSH 这个应用本身。"
    ),
    steps=(
        Step(
            id="E-1.1",
            description="在设置面板里选「低档」",
            target="低档",
            expectation=_text("思考档位：低"),
        ),
        Step(
            id="E-1.2",
            description="保存",
            target="保存",
            expectation=_text("已保存：低"),
        ),
    ),
    clauses=(
        Clause(
            "E-1.A",
            f"落点落在该控件框内（出框 ≤{LANDING_NEAR_MISS_PX:.0f} px 记近失，"
            "更远判失败）",
            landing_in_target_rect,
        ),
        Clause(
            "E-1.B",
            "保存后设置面板里该项的值 == 目标值",
            context_value("setting_value", "低档"),
        ),
        Clause("E-1.C", "追踪记录能读到通道与失败类型", channel_and_failure_readable),
    ),
)


E2 = Task(
    id="E-2",
    title="导航到一个已知地址，核对页面标题",
    app="DSH/Tabbit",
    note=(
        "**本轮导航到的是评测台自带的本地验证页**（已知地址 "
        "``http://127.0.0.1:8791/``）；**DSH Web GUI 本体没驱动**（它要本机鉴权令牌）。"
    ),
    steps=(
        Step(
            id="E-2.1",
            description="在目标页面上点「打开详情」",
            target="打开详情",
            expectation=_text(VerificationPage.DETAIL),
        ),
    ),
    clauses=(
        Clause(
            "E-2.A",
            "导航后页面标题 == 目标标题",
            page_title_is(VerificationPage.TITLE),
        ),
        Clause(
            "E-2.B",
            "结构与视觉都可用时选结构那条，并把选择理由记进追踪记录",
            structure_channel_preferred,
        ),
        Clause(
            "E-2.C",
            "坐标与动作语义跨技术栈不变",
            cross_stack_semantics_stable,
        ),
    ),
)


E3 = Task(
    id="E-3",
    title="在会话输入框输入一段中文",
    app="DSH/Tabbit",
    note=(
        "**本轮跑在评测台自带的本地验证页上**（同样是 Chromium/Electron 形态）；"
        "**DSH Web GUI 本体没驱动**（它要本机鉴权令牌）。"
    ),
    steps=(
        Step(
            id="E-3.1",
            description="在会话输入框里输入一段中文",
            target="会话输入框",
            action_kind=ActionKind.TYPE,
            params={"text": E3_TEXT, "focus_target": True},
            # ⚠️ **打进去的字不在可访问性树里**（contenteditable 的内容不是
            # 控件名）→ 结构断言取**输入框的当前取值**（执行器读）。
            expect_context=("input_value", E3_TEXT),
        ),
    ),
    clauses=(
        Clause(
            "E-3.A",
            "输入成功且输入框内容正确",
            context_value("input_value", E3_TEXT),
        ),
        Clause(
            "E-3.B",
            "输入通道失效时自动退到另一条并记录这次退化",
            input_channel_recorded,
        ),
        Clause("E-3.C", "剪贴板用过后被恢复", context_value("clipboard_restored", True)),
    ),
)


# ---------------------------------------------------------------------------
# CC-1 ~ CC-3：cc-switch（原生窗口，完整树）
# ---------------------------------------------------------------------------


def capability_recorded_and_landing_in_picked(
    evidence: TaskEvidence,
) -> tuple[bool, str]:
    """CC-1.A：**能力表如实记录**（取什么值就记什么）**且**落点落在**选中框**内。

    ⚠️ **2026-10-22 用户拍板**：本机 cc-switch 的树**同名撞车 6 处** →
    能力表判 ``sparse`` 是**应用现状**，不是能力缺陷 → **不许因为 ``sparse``
    判失败**（原条款硬要求 ``usable``，已去掉）。

    条款**仍然要判**（可判、可过可不过）：

    * **能力表如实记录** —— 上下文里得**有**这条读数（记的是哪个值就是哪个值）；
      **没有读数 = 没测过** → 判不了，不许当成「测了，结果不成立」；
    * **落点落在选中框内** —— 复用**同一套三档口径**
      （:func:`minicode.e2e.checks.landing_in_target_rect`）：
      A 命中 / B 近失**只记档不判死** / C 错选判失败 / 缺真值判不了。
    """
    capability = str(evidence.context.get("capability", "") or "")
    if not capability:
        return False, "能力表里没有这一条（读数是空的）—— **没测过**，判不了"
    ok, detail = landing_in_target_rect(evidence)
    if not ok:
        return (
            False,
            f"能力表记的是 {capability!r}（如实记录），但落点没落在选中框内：{detail}",
        )
    return True, f"能力表记的是 {capability!r}（如实记录）｜落点：{detail}"


CC1 = Task(
    id="CC-1",
    title="在 cc-switch 里把当前配置切到另一个供应商条目",
    app="cc-switch",
    steps=(
        Step(
            id="CC-1.1",
            description="在供应商列表里点另一个条目",
            target=PICKED,
            expect_context=("current_provider", PICKED),
        ),
    ),
    clauses=(
        Clause(
            "CC-1.A",
            "能力表如实记录（记成 sparse 也算如实 —— 那是应用现状，不是能力缺陷）"
            "且落点落在选中框内",
            capability_recorded_and_landing_in_picked,
        ),
        Clause(
            "CC-1.B",
            f"落点落在该条目的控件框内（出框 ≤{LANDING_NEAR_MISS_PX:.0f} px 记近失，"
            "更远判失败）",
            landing_in_target_rect,
        ),
        Clause(
            "CC-1.C",
            "切换后「当前配置」显示为新条目",
            context_matches_picked("current_provider"),
        ),
    ),
)


CC2 = Task(
    id="CC-2",
    title="改完配置之后核对结果",
    app="cc-switch",
    note=(
        "⚠️ 本机 cc-switch 的**选中态在可访问性树里读不出来**（没有 Toggle / "
        "SelectionItem pattern）→「当前配置」的真值取**应用自己的状态库**"
        "（``providers.is_current``），**不是界面上的文本**。"
    ),
    steps=(
        Step(
            id="CC-2.1",
            description="读当前配置（读应用自己的状态，不是看画面变了没有）",
            target="当前配置",
            action_kind=ActionKind.INSPECT,
            expect_context=("current_provider", PICKED),
        ),
    ),
    clauses=(
        Clause(
            "CC-2.A",
            "核对用可访问性树 / 文本比对，不是靠画面变了",
            verification_source_is_structure,
        ),
        Clause(
            "CC-2.B",
            "核对不通过时判执行失败并回报",
            context_value("verification_failure", "execution_failed"),
        ),
    ),
)


CC3 = Task(
    id="CC-3",
    title="跨应用：改完 cc-switch 的配置后，到浏览器打开依赖该配置的页面验证生效",
    app="cc-switch → 浏览器",
    note=(
        "**「跨应用确认」是人工代确认** → "
        "CC-3.3 **不自动执行**，这张票记 **待人工确认**。"
    ),
    steps=(
        Step(
            id="CC-3.1",
            description="在 cc-switch 里切到另一个供应商条目",
            target=PICKED,
            expect_context=("current_provider", PICKED),
        ),
        Step(
            id="CC-3.2",
            description="在浏览器里打开依赖该配置的页面",
            target="当前配置",
            window="browser",
            expectation=_text("当前配置："),
        ),
        Step(
            id="CC-3.3",
            description="核对页面上显示的当前配置",
            target="当前配置",
            window="browser",
            action_kind=ActionKind.INSPECT,
            expect_context=("page_provider", PICKED),
            #: **「跨应用确认」是人工代确认** → 这一步**不自动执行**。
            human_confirm=True,
        ),
    ),
    clauses=(
        Clause(
            "CC-3.A",
            "三步各自有断言，任一步失败能定位到哪一步（三类失败分开统计）",
            failure_types_separated,
        ),
        Clause(
            "CC-3.B",
            "全程没有把坐标挪到别的窗口（目标窗口被遮挡时先置前再操作）",
            coordinates_stayed_in_window,
        ),
    ),
)


#: 第二波的全部工单（就绪边上的九张）。
WAVE2_TASKS: tuple[Task, ...] = (WX1, WX2, WX3, E1, E2, E3, CC1, CC2, CC3)


def task_by_id(task_id: str) -> Task:
    """按编号取一张工单。"""
    for task in WAVE2_TASKS:
        if task.id == task_id:
            return task
    raise KeyError(f"没有编号为 {task_id!r} 的工单")

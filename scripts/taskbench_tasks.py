"""W4 评测台的任务定义：30 条本机可复现任务（reset / steps / judge 三件套）。

分布：文件类 8 · 计算类 4 · 浏览器类 3 · 界面类 7 · 复合类 4 · 反例类 4。

judge 的口径（铁律）：**只读状态** —— UIA 控件值 / 文件内容 / 磁盘 / 窗口标题，
**不读截图**。反例类验证「该失败时会失败」。

notepad 任务**一律文件承载**（runner 带 `file_path` 参数起实例——打开即聚焦
本任务的标签，关窗前 Ctrl+S 弄干净再关）：diag 对照实验证实，无文件 spawn
会把 TabState 里的旧会话恢复成标签并可能劫持焦点，输入会落进恢复标签、
Ctrl+S 会对无文件标签弹 Save-As 模态框卡死后续全部任务。
Unicode 注入的控制字符（\\r\\n）在 Win11 记事本上**不产生换行**（diag 实测，
UIA 读回 lines=1），换行一律走 Enter 键。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from taskbench import _APPS, TASK_TMP
from w3_probe import find_new_window, visible_windows

from minicode.action.protocol import Action, ActionKind
from minicode.action.records import ListTraceSink
from minicode.guard.policy import GuardBudget, GuardPolicy
from minicode.uia.desktop import build_desktop
from minicode.uia.observer import UiaStateObserver
from minicode.uia.ports import UiaFindRequest

WM_CLOSE = 0x0010


@dataclass(frozen=True)
class TaskSpec:
    """一条评测任务的完整定义。"""

    id: str
    category: str
    description: str
    app: str | None
    steps: tuple[dict, ...]
    judge: object  # Callable[[TaskRun], dict]
    reset: object = lambda run: None  # noqa: E731
    #: 覆盖默认护栏策略（反例类用：空白名单 / 最小预算）。
    override_policy: GuardPolicy | None = None
    budget: GuardBudget = field(default_factory=GuardBudget)


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------


def _tmpfile(name: str, content: str = "") -> object:
    path = TASK_TMP / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def _read(run, name: str) -> str:
    path = TASK_TMP / name
    return path.read_text(encoding="utf-8") if path.exists() else ""


def _joined_value(rows: list[dict]) -> str:
    return "".join((row.get("value") or "") for row in rows)


def _editor_value(run) -> str:
    return _joined_value(run.query(control_type="Document"))


def judge_value(*needles: str):
    """UIA 读回 Document 的 value，要求包含全部片段。"""

    def judge(run) -> dict:
        value = _editor_value(run)
        return {"ok": all(n in value for n in needles), "value": value[:300]}

    return judge


def judge_file_contains(name: str, *needles: str):
    """磁盘文件内容判定（写操作落盘才算成功）。"""

    def judge(run) -> dict:
        content = _read(run, name)
        return {"ok": all(n in content for n in needles), "content": content[:300]}

    return judge


def judge_display(*needles: str):
    """计算器显示屏判定（automation_id=CalculatorResults）。"""

    def judge(run) -> dict:
        display = _calc_display(run)
        return {"ok": all(n in display for n in needles), "display": display}

    return judge


def _calc_display(run) -> str:
    """计算器显示屏当前文本（窗口作用域过滤由 run.query 兜底）。"""
    return _display_text(run.query(automation_id="CalculatorResults"))


def _display_text(rows: list[dict]) -> str:
    """value-or-name 合并（automation_id find 通道的文本常在 name）。"""
    return " ".join(
        (row.get("value") or row.get("name") or "") for row in rows
    )


def judge_error_code(step_index: int, expected_code: str):
    """反例类：第 N 步必须报**指定错误码**（零注入由护栏记录佐证）。"""

    def judge(run) -> dict:
        if len(run.steps) < step_index:
            return {"ok": False, "error": f"步骤数不足：{len(run.steps)}"}
        step = run.steps[step_index - 1]
        code = step.get("error_code")
        return {"ok": code == expected_code, "expected": expected_code, "got": code}

    return judge


def judge_title(*needles: str):
    """窗口标题判定（浏览器类）。"""

    def judge(run) -> dict:
        import ctypes

        buf = ctypes.create_unicode_buffer(512)
        ctypes.windll.user32.GetWindowTextW(run.hwnd, buf, 512)
        title = buf.value
        return {"ok": all(n in title for n in needles), "title": title}

    return judge


def _notepad_open_file(file_name: str, content: str):
    """文件类任务的 reset：造文件 + 记录路径（runner 起实例时带上它）。"""

    def reset(run) -> None:
        path = _tmpfile(file_name, content)
        run.vars["file_path"] = str(path)

    return reset


def _title_of(hwnd: int) -> str:
    """窗口标题（物理缓冲区读法）。"""
    import ctypes

    buf = ctypes.create_unicode_buffer(512)
    ctypes.windll.user32.GetWindowTextW(hwnd, buf, 512)
    return buf.value


def _rect_in(hwnd: int, rect) -> bool:
    """元素矩形中心是否落在这个窗口里（跨窗口/恢复标签的读回过滤）。"""
    import ctypes
    import ctypes.wintypes

    if not rect or tuple(rect) == (0, 0, 0, 0):
        return False
    left, top, right, bottom = (int(v) for v in rect)
    wr = ctypes.wintypes.RECT()
    ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(wr))
    cx, cy = (left + right) / 2.0, (top + bottom) / 2.0
    return wr.left <= cx <= wr.right and wr.top <= cy <= wr.bottom


def _notepad_close_discard(hwnd: int, desktop) -> None:
    """关闭一个改过的无标题记事本：WM_CLOSE → 精确名「不保存」→ rect 点击。"""
    import ctypes
    import time

    ctypes.windll.user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)
    time.sleep(1.0)
    if not ctypes.windll.user32.IsWindow(hwnd):
        return
    result = desktop.port.find(UiaFindRequest(anchor=desktop.anchor, name="不保存"))
    for element in result.elements:
        if getattr(element, "name", "") == "不保存":
            rect = element.rect
            injector = desktop.injector
            cx, cy = (rect[0] + rect[2]) // 2, (rect[1] + rect[3]) // 2
            injector.move(cx, cy)
            injector.button_down("left")
            injector.button_up("left")
            time.sleep(0.8)
            return


def _click_buttons(run, names: tuple[str, ...]) -> str | None:
    """依次点击按钮，失败返回失败目标名。"""
    for name in names:
        response = run.service.execute(
            {"protocol": run.protocol_version, "action": "click", "target": name}
        )
        if not response.ok:
            return name
    return None


ALL_TASKS: list[TaskSpec] = []


def _t(task_id: str, category: str, description: str, app: str | None,
       steps: tuple[dict, ...], judge, reset=lambda run: None,
       budget: GuardBudget | None = None,
       override_policy: GuardPolicy | None = None) -> None:
    ALL_TASKS.append(
        TaskSpec(id=task_id, category=category, description=description,
                 app=app, steps=steps, judge=judge, reset=reset,
                 budget=budget or GuardBudget(), override_policy=override_policy)
    )


# ===========================================================================
# 文件类（8）
# ===========================================================================

f01_text = "minicode f01 第一行"
_t(
    "f01", "文件类", "打开已有文件 → UIA 读回与文件内容一致",
    "notepad",
    ({"do": "query", "control_type": "Document", "save_as": "doc"},),
    lambda run: (lambda value: {"ok": f01_text in value, "value": value[:200]})(
        _joined_value(run.vars.get("doc") or [])),
    reset=_notepad_open_file("f01.txt", f01_text),
)

f02_line = "f02 追加的这一行"
_t(
    "f02", "文件类", "打开已有文件 → 追加一行（输入→Ctrl+S）→ 磁盘含新行",
    "notepad",
    (
        {"do": "type", "text": f02_line},
        {"do": "key", "combo": ["ctrl", "s"], "intent": "保存"},
        {"do": "wait", "ms": 800},
    ),
    judge_file_contains("f02.txt", f02_line),
    reset=_notepad_open_file("f02.txt", "f02 基础内容\n"),
)

f03_new = "f03 全新内容"
_t(
    "f03", "文件类", "全选重写（Ctrl+A→输入→Ctrl+S）→ 磁盘 == 新文本",
    "notepad",
    (
        {"do": "key", "combo": ["ctrl", "a"], "intent": "全选"},
        {"do": "type", "text": f03_new},
        {"do": "key", "combo": ["ctrl", "s"], "intent": "保存"},
        {"do": "wait", "ms": 800},
    ),
    judge_file_contains("f03.txt", f03_new),
    reset=_notepad_open_file("f03.txt", "f03 旧内容将被整体替换\r\n"),
)

f04_text = "f04 混合串 abc-123_标点，。！xyz"
_t(
    "f04", "文件类", "中英标点混合串输入 → Ctrl+S → 磁盘逐字符一致（注入保真度）",
    "notepad",
    (
        {"do": "key", "combo": ["ctrl", "a"], "intent": "全选"},
        {"do": "type", "text": f04_text},
        {"do": "key", "combo": ["ctrl", "s"], "intent": "保存"},
        {"do": "wait", "ms": 800},
    ),
    judge_file_contains("f04.txt", f04_text),
    reset=_notepad_open_file("f04.txt", ""),
)

_t(
    "f05", "文件类", "打开文件 → 输入一行 → UIA 读回包含该行",
    "notepad",
    (
        {"do": "type", "text": "f05 的这一行"},
        {"do": "wait", "ms": 300},
        {"do": "query", "control_type": "Document", "save_as": "doc"},
    ),
    judge_value("f05 的这一行"),
    reset=_notepad_open_file("f05.txt", ""),
)

_t(
    "f06", "文件类", "输入三行（Enter 换行）→ UIA 读回行数 = 3",
    "notepad",
    (
        {"do": "type", "text": "f06 L1"},
        {"do": "key", "combo": ["enter"], "intent": "换行"},
        {"do": "type", "text": "f06 L2"},
        {"do": "key", "combo": ["enter"], "intent": "换行"},
        {"do": "type", "text": "f06 L3"},
        {"do": "wait", "ms": 300},
        {"do": "query", "control_type": "Document", "save_as": "doc"},
    ),
    lambda run: (lambda value: {
        "ok": (value.count("\n") + value.count("\r")) >= 2,
        "lines": value.count("\n") + value.count("\r") + 1,
        "value": value[:200]})(  # UIA 把换行报成 \r（diag 实测），两种都数
        _joined_value(run.vars.get("doc") or [])),
    reset=_notepad_open_file("f06.txt", ""),
)

_t(
    "f07", "文件类", "输入含 emoji（UTF-16 代理对）→ UIA 读回含 🎉",
    "notepad",
    (
        {"do": "type", "text": "f07 emoji 🎉 done"},
        {"do": "wait", "ms": 300},
        {"do": "query", "control_type": "Document", "save_as": "doc"},
    ),
    judge_value("🎉"),
    reset=_notepad_open_file("f07.txt", ""),
)

_t(
    "f08", "文件类", "输入后窗口标题带修改标记 *（窗口标题判定）",
    "notepad",
    ({"do": "type", "text": "f08 修改标记"},),
    judge_title("*"),
    reset=_notepad_open_file("f08.txt", ""),
)

# ===========================================================================
# 计算类（4）
# ===========================================================================

_t(
    "c09", "计算类", "17×23 → 显示 391（W3 首跑同款）",
    "calc",
    tuple({"do": "click", "target": t} for t in
          ("一", "七", "乘以", "二", "三", "等于")),
    judge_display("391"),
)

_t(
    "c10", "计算类", "123+456 → 显示 579",
    "calc",
    tuple({"do": "click", "target": t} for t in
          ("一", "二", "三", "加", "四", "五", "六", "等于")),
    judge_display("579"),
)

_t(
    "c11", "计算类", "1024÷8 → 显示 128",
    "calc",
    tuple({"do": "click", "target": t} for t in
          ("一", "零", "二", "四", "除以", "八", "等于")),
    judge_display("128"),
)

_t(
    "c12", "计算类", "144 → 平方根 → 显示 12",
    "calc",
    tuple({"do": "click", "target": t} for t in
          ("一", "四", "四", "平方根")),
    judge_display("12"),
)

# ===========================================================================
# 浏览器类（3）
# ===========================================================================

_t(
    "b13", "浏览器类", "bing 搜索 minicode → 标题含查询词",
    "edge",
    ({"do": "wait", "ms": 2500},),
    judge_title("minicode"),
    reset=lambda run: run.vars.update(
        url="https://www.bing.com/search?q=minicode+desktop+agent"),
)

_t(
    "b14", "浏览器类", "打开 example.com → 标题 Example",
    "edge",
    ({"do": "wait", "ms": 2500},),
    judge_title("Example"),
    reset=lambda run: run.vars.update(url="https://example.com/"),
)

_t(
    "b15", "浏览器类", "打开 bing 首页 → 标题含 必应",
    "edge",
    ({"do": "wait", "ms": 2500},),
    judge_title("必应"),
    reset=lambda run: run.vars.update(url="https://www.bing.com/"),
)

# ===========================================================================
# 界面类（7）
# ===========================================================================

def _steps_ok(run, *kinds: str) -> bool:
    """指定类型的步骤全部 ok=True（判分防「动作没生效照样 pass」的佐证）。

    侦察 A 发现的判分洞：期望态==初始态或子串平凡满足时，动作全失败也能
    pass。这两类任务的 judge 必须同时核对步骤确实执行成功。
    """
    targets = set(kinds) or {"click", "type", "key"}
    relevant = [s for s in run.steps if s.get("do") in targets]
    return bool(relevant) and all(s.get("ok") for s in relevant)


_t(
    "i16", "界面类", "计算器清除：8×7= → 清除 → 显示 0",
    "calc",
    tuple({"do": "click", "target": t} for t in
          ("八", "乘以", "七", "等于"))
    # ⚠️ 「等于」后计算器要重算并重建显示元素——立刻查 UIA 会拿到瞬态空树
    # （redo 实测 mid 三连空）。等 800ms 再读（与 judge 的读取时序对齐）。
    + ({"do": "wait", "ms": 800},
       {"do": "query", "automation_id": "CalculatorResults", "save_as": "mid"},
       {"do": "click", "target": "清除"}),
    # 判分洞修复（侦察 A）：显示 0 是初始态，光看终态恒真——必须先证到
    # 「等于之后是 56」（动作真的执行了），再证清除回 0。
    # ⚠️ mid 行的文本可能在 name 而非 value（automation_id find 通道实测
    # value 空）——与 _calc_display 同口径 value-or-name。
    lambda run: (lambda mid, final: {
        "ok": "56" in mid and "0" in final,
        "mid": mid[:100], "display": final[:100]})(
        _display_text(run.vars.get("mid") or []), _calc_display(run)),
)

_t(
    "i17", "界面类", "退格：一二三 → Backspace → 显示 12",
    "calc",
    tuple({"do": "click", "target": t} for t in
          ("一", "二", "三", "Backspace")),
    # 判分洞修复（侦察 A）：「12」in「123」子串平凡满足——要求 Backspace
    # 步骤真成功，且「123」不再出现（退格真的发生）。
    lambda run: (lambda display, clicks_ok: {
        "ok": clicks_ok and "12" in display and "123" not in display,
        "display": display[:100], "clicks_ok": clicks_ok})(
        _calc_display(run), _steps_ok(run, "click")),
)

_t(
    "i18", "界面类", "负数：0−5= → 显示 -5",
    "calc",
    tuple({"do": "click", "target": t} for t in
          ("零", "减", "五", "等于")),
    judge_display("-5"),
)

_t(
    "i19", "界面类", "撤销：输入 → Ctrl+Z → UIA 读回回到基础内容",
    "notepad",
    (
        {"do": "type", "text": "i19 会被撤销的内容"},
        {"do": "wait", "ms": 300},
        {"do": "key", "combo": ["ctrl", "z"], "intent": "撤销"},
        {"do": "wait", "ms": 300},
        {"do": "query", "control_type": "Document", "save_as": "doc"},
    ),
    # 判分洞修复（侦察 A）：期望值恰等于 reset 基础内容——type 步被拒时
    # 内容本来就等于基础行，撤销行为完全没被验证。必须证到输入与撤销
    # 两个步骤都真实执行成功，终值回到基础行才算过。
    lambda run: (lambda value, typed, undone: {
        "ok": typed and undone and value.strip() == "i19 基础行",
        "value": value[:200], "typed_ok": typed, "undo_ok": undone})(
        _joined_value(run.vars.get("doc") or []),
        _steps_ok(run, "type"), _steps_ok(run, "key")),
    reset=_notepad_open_file("i19.txt", "i19 基础行\n"),
)

_t(
    "i20", "界面类", "F5 插入时间/日期 → UIA 读回含 2026",
    "notepad",
    (
        {"do": "key", "combo": ["f5"], "intent": "插入时间日期"},
        {"do": "wait", "ms": 300},
        {"do": "query", "control_type": "Document", "save_as": "doc"},
    ),
    judge_value("2026"),
    reset=_notepad_open_file("i20.txt", ""),
)

_t(
    "i21", "界面类", "键盘通道向计算器输入数字 → 显示 12",
    "calc",
    (
        {"do": "type", "text": "12"},
        {"do": "wait", "ms": 300},
    ),
    judge_display("12"),
)

_t(
    "i22", "界面类", "Tab 键输入制表符 → UIA 读回 i22 与 after 分隔",
    "notepad",
    (
        {"do": "type", "text": "i22"},
        {"do": "key", "combo": ["tab"], "intent": "制表符"},
        {"do": "type", "text": "after"},
        {"do": "wait", "ms": 300},
        {"do": "query", "control_type": "Document", "save_as": "doc"},
    ),
    lambda run: (lambda value: {"ok": "i22\t" in value,
                                "value": value[:200]})(
        _joined_value(run.vars.get("doc") or [])),
    reset=_notepad_open_file("i22.txt", ""),
)

# ===========================================================================
# 复合类（4）
# ===========================================================================

_t(
    "x23", "复合类", "连续运算 2+3×4（标准模式逐级执行）→ 显示 20",
    "calc",
    tuple({"do": "click", "target": t} for t in
          ("二", "加", "三", "乘以", "四", "等于")),
    judge_display("20"),
)


def _x24_judge(run) -> dict:
    return {"ok": run.vars.get("cross_ok") is True,
            "display": run.vars.get("cross_display", ""),
            "notepad_value": run.vars.get("cross_notepad_value", "")}


def _x24_reset(run) -> None:
    run.vars.update(cross_ok=False, cross_display="", cross_notepad_value="")


def _x24_steps(run) -> dict:
    """跨应用复合任务（raw_fn 编排）：notepad 输入 17 → 读回 → calc 键盘输入 ×23。"""
    import ctypes
    import subprocess
    import time

    from w3_probe import find_new_window, visible_windows  # noqa: PLC0415

    protocol = run.protocol_version
    # -- 段 1：notepad（自己的实例，**文件承载**）读回 17 -------------------
    # （diag 实验：无文件 spawn 会把 TabState 旧会话恢复成标签并可能劫持焦点；
    #   文件承载 + 标题守卫才敢注入。本段只读不写。）
    cfg = _APPS["notepad"]
    src1 = TASK_TMP / "x24-notepad.txt"
    src1.parent.mkdir(parents=True, exist_ok=True)
    src1.write_text("17", encoding="utf-8")
    before = {w[0] for w in visible_windows(cfg["classes"])}
    proc1 = subprocess.Popen([cfg["command"][0], str(src1)])
    found = find_new_window(before, class_filter=cfg["classes"])
    if found is None:
        proc1.terminate()
        return {"ok": False, "error": "notepad 没等到窗口"}
    hwnd1, _cls1, _title1 = found
    time.sleep(1.2)
    if ctypes.windll.user32.GetForegroundWindow() != hwnd1:
        _notepad_close_discard(hwnd1, build_desktop(hwnd=hwnd1))
        return {"ok": False, "error": "notepad 没拿到前台"}
    title1 = _title_of(hwnd1)
    if "x24-notepad.txt" not in title1:
        _notepad_close_discard(hwnd1, build_desktop(hwnd=hwnd1))
        return {"ok": False, "error": f"活动标签不是本任务（标题 {title1!r}）"}
    desktop1 = build_desktop(hwnd=hwnd1)
    observer1 = UiaStateObserver(desktop1.port, desktop1.anchor)
    rows1 = observer1.query.find(
        UiaFindRequest(anchor=desktop1.anchor, control_type="Document")
    )
    value = "".join(
        getattr(el, "value", "") for el in rows1.elements
        if _rect_in(hwnd1, getattr(el, "rect", ()))
    ).strip()
    _notepad_close_discard(hwnd1, desktop1)
    if value != "17":
        return {"ok": False, "error": f"notepad 读回 {value!r} != '17'"}

    # -- 段 2：calc 键盘输入 17 ×23 = ----------------------------------------
    # ⚠️ x24 的 app=None → run.service 是 None（外层没有实例）；calc 必须配
    # **自己的**带护栏服务（评测台纪律：每实例一份接线，跨窗口借用必炸）。
    cfg2 = _APPS["calc"]
    before2 = {w[0] for w in visible_windows(cfg2["classes"])}
    proc2 = subprocess.Popen(cfg2["command"])
    hwnd2 = 0
    svc2 = None
    try:
        found2 = find_new_window(before2, class_filter=cfg2["classes"])
        if found2 is None:
            proc2.terminate()
            return {"ok": False, "error": "calc 没等到窗口", "notepad_value": value}
        hwnd2, _cls2, _title2 = found2
        time.sleep(1.2)
        from minicode.guard.policy import load_policy  # noqa: PLC0415
        from minicode.serve.real import build_real_service  # noqa: PLC0415

        svc2 = build_real_service(
            run.out_dir, hwnd=hwnd2,
            guard_policy=load_policy(level="L1", whitelist=cfg2["whitelist"]),
            session_directory=run.out_dir / "guard",
        )
        # 键盘输入被乘数（走 calc 自己的护栏通道，与 do_step 的 type 同口径）。
        guarded2 = svc2._seam_factory(ListTraceSink())  # noqa: SLF001 - 评测台脚本
        outcome = guarded2.executor.execute(
            Action(kind=ActionKind.TYPE, params={"text": value},
                   intent="calc 输入被乘数")
        )
        svc2.guard_session.ledger.record_step(model_calls=0)
        if not outcome.ok:
            return {"ok": False,
                    "error": f"calc 输入失败：{outcome.error_code}",
                    "notepad_value": value}
        time.sleep(0.3)
        for name in ("乘以", "二", "三", "等于"):
            response = svc2.execute(
                {"protocol": protocol, "action": "click", "target": name}
            )
            if not response.ok:
                return {"ok": False,
                        "error": f"calc 点击 {name} 失败：{response.error_code}"}
        time.sleep(0.5)
        # 显示屏读数：calc 自己的观察者（run.service 为 None，不能借）。
        # **矩形过滤**（侦察 A 发现的跨任务污染洞）：查询端口在窗口路查不到
        # 时会按 PID 桌面根遍历，上一个 calc 残留窗口的 CalculatorResults
        # 会进同一结果集——c09 残留的「391」能直接满足本判分。只认落点在
        # 自己 calc 窗口矩形内的元素。
        desktop2 = build_desktop(hwnd=hwnd2)
        observer2 = UiaStateObserver(desktop2.port, desktop2.anchor)
        result2 = observer2.query.find(
            UiaFindRequest(anchor=desktop2.anchor,
                           automation_id="CalculatorResults")
        )
        display = " ".join(
            (getattr(el, "value", "") or getattr(el, "name", ""))
            for el in result2.elements
            if _rect_in(hwnd2, getattr(el, "rect", ()))
        )
        run.vars["cross_ok"] = "391" in display
        run.vars["cross_display"] = display
        run.vars["cross_notepad_value"] = value
        return {"ok": "391" in display, "display": display}
    finally:
        estop2 = getattr(getattr(svc2, "guard_session", None), "estop", None)
        if estop2 is not None:
            try:
                estop2.stop()
            except Exception:
                pass
        # 收尾三道闸的最后一道：只关**自己差分找到的**窗口句柄。
        if hwnd2 and ctypes.windll.user32.IsWindow(hwnd2):
            ctypes.windll.user32.PostMessageW(hwnd2, WM_CLOSE, 0, 0)


_t(
    "x24", "复合类",
    "notepad 输入 17 → 读回 → calc 键盘输入该值 ×23 → 391（跨应用数据流，双实例编排）",
    None,
    ({"do": "raw_fn", "fn": _x24_steps},),
    _x24_judge,
    reset=_x24_reset,
)


def _x25_reopen_and_read(run) -> dict:
    """复合任务第 5 步：把刚保存的文件用**第二个实例**再打开，UIA 读回一致。

    ⚠️ Win11 记事本是**单实例接管**：第一实例还开着时 spawn 同一文件**不会**
    出新窗口（30s 超时，W4 正式跑 9/9 实录）——所以这里先把**已保存干净的**
    第一实例关掉，再开第二实例读回。
    """
    import ctypes
    import subprocess
    import time

    file_path = run.vars.get("file_path")
    if not file_path:
        return {"ok": False, "error": "file_path 缺失"}
    # -- 先关第一实例（步骤 3 已 Ctrl+S，标题应无 *；有 * 就如实报）--------
    from taskbench import _window_text  # noqa: PLC0415

    first_hwnd = run.hwnd
    if first_hwnd and ctypes.windll.user32.IsWindow(first_hwnd):
        title = _window_text(first_hwnd)
        if title.startswith("*"):
            return {"ok": False, "error": f"第一实例未保存（标题 {title!r}）"}
        ctypes.windll.user32.PostMessageW(first_hwnd, WM_CLOSE, 0, 0)
        time.sleep(1.2)
        if ctypes.windll.user32.IsWindow(first_hwnd):
            return {"ok": False, "error": "第一实例关不掉（不该有对话框）"}
    # -- 开第二实例读回 -----------------------------------------------------
    cfg = _APPS["notepad"]
    before = {w[0] for w in visible_windows(cfg["classes"])}
    proc = subprocess.Popen([cfg["command"][0], file_path])
    found = find_new_window(before, class_filter=cfg["classes"])
    if found is None:
        proc.terminate()
        return {"ok": False, "error": "第二个实例没等到窗口"}
    hwnd, _cls, _title = found
    time.sleep(1.2)
    desktop = build_desktop(hwnd=hwnd)
    observer = UiaStateObserver(desktop.port, desktop.anchor)
    rows = observer.query.find(
        UiaFindRequest(anchor=desktop.anchor, control_type="Document")
    )
    value = "".join(
        getattr(el, "value", "") for el in rows.elements
        if _rect_in(hwnd, getattr(el, "rect", ()))
    )
    ctypes.windll.user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)
    time.sleep(0.6)
    run.vars["x25_ok"] = "x25 持久化内容" in value
    run.vars["x25_value"] = value[:200]
    return {"ok": run.vars["x25_ok"], "value": value[:200]}


_t(
    "x25", "复合类",
    "输入 → Ctrl+S → 第二个实例重新打开 → UIA 读回一致（两阶段持久化往返）",
    "notepad",
    (
        {"do": "key", "combo": ["ctrl", "a"], "intent": "全选"},
        {"do": "type", "text": "x25 持久化内容"},
        {"do": "key", "combo": ["ctrl", "s"], "intent": "保存"},
        {"do": "wait", "ms": 800},
        {"do": "raw_fn", "fn": _x25_reopen_and_read},
    ),
    lambda run: {"ok": run.vars.get("x25_ok") is True,
                 "value": run.vars.get("x25_value", "")},
    reset=_notepad_open_file("x25.txt", ""),
)

x26_name = "x26.txt"
x26_mid = "x26 中间插入"
x26_end = "x26 末尾追加"
_t(
    "x26", "复合类",
    "两步修改（整体重写为两行）→ Ctrl+S → 磁盘同时含两处（两步依赖）",
    "notepad",
    (
        {"do": "key", "combo": ["ctrl", "a"], "intent": "全选"},
        {"do": "type", "text": x26_mid},
        {"do": "key", "combo": ["enter"], "intent": "换行"},
        {"do": "type", "text": x26_end},
        {"do": "key", "combo": ["ctrl", "s"], "intent": "保存"},
        {"do": "wait", "ms": 800},
    ),
    judge_file_contains(x26_name, x26_mid, x26_end),
    reset=_notepad_open_file(x26_name, "x26 第一行\r\n"),
)

# ===========================================================================
# 反例类（4）
# ===========================================================================

_t(
    "n27", "反例类",
    "点击不存在的按钮 → 必须 target_not_found（该失败时失败）",
    "notepad",
    ({"do": "click", "target": "导出为不可编辑幻灯片"},),
    judge_error_code(1, "target_not_found"),
)

_t(
    "n28", "反例类",
    "计算器点击「月球」→ 必须 target_not_found",
    "calc",
    ({"do": "click", "target": "月球"},),
    judge_error_code(1, "target_not_found"),
)

_t(
    "n29", "反例类",
    "预算正例：步数预算=1，第二次点击必须被 budget_exceeded 拒绝（护栏 4）",
    "notepad",
    (
        {"do": "click", "target": "文本编辑器"},
        {"do": "click", "target": "文本编辑器"},
    ),
    judge_error_code(2, "budget_exceeded"),
    budget=GuardBudget(max_steps=1),
)

_t(
    "n30", "反例类",
    "L1 空白名单 fail-closed：点击必须被 window_whitelist_mismatch 拒绝（授权层）",
    "notepad",
    ({"do": "click", "target": "文本编辑器"},),
    judge_error_code(1, "window_whitelist_mismatch"),
    override_policy=GuardPolicy(level="L1", window_whitelist=()),
)

TASKS_BY_ID = {spec.id: spec for spec in ALL_TASKS}

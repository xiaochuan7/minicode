"""W4 评测台的任务定义：30 条本机可复现任务（reset / steps / judge 三件套）。

分布：文件类 8 · 计算类 4 · 浏览器类 3 · 界面类 7 · 复合类 4 · 反例类 4。

judge 的口径（铁律）：**只读状态** —— UIA 控件值 / 文件内容 / 磁盘 / 窗口标题，
**不读截图**。反例类验证「该失败时会失败」。

notepad 任务分两种：**文件任务**（runner 带 `file_path` 参数起实例——
打开即聚焦本任务的标签，避免 Win11 会话恢复把旧标签顶成活动标签）
与 **无标题任务**（不保存，结束走「不保存」清理）。
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
        rows = run.query(automation_id="CalculatorResults")
        display = " ".join(
            (row.get("value") or row.get("name") or "") for row in rows
        )
        return {"ok": all(n in display for n in needles), "display": display}

    return judge


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
    "f02", "文件类", "文件末尾追加一行（Ctrl+End→输入→Ctrl+S）→ 磁盘含新行",
    "notepad",
    (
        {"do": "key", "combo": ["ctrl", "end"], "intent": "光标到末尾"},
        {"do": "type", "text": f02_line + "\r\n"},
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
    "f05", "文件类", "无标题记事本输入一行 → UIA 读回（不保存）",
    "notepad",
    (
        {"do": "type", "text": "f05 的这一行"},
        {"do": "wait", "ms": 300},
        {"do": "query", "control_type": "Document", "save_as": "doc"},
    ),
    judge_value("f05 的这一行"),
)

_t(
    "f06", "文件类", "输入三行 → UIA 读回行数 = 3",
    "notepad",
    (
        {"do": "type", "text": "f06 L1\r\nf06 L2\r\nf06 L3"},
        {"do": "wait", "ms": 300},
        {"do": "query", "control_type": "Document", "save_as": "doc"},
    ),
    lambda run: (lambda value: {"ok": value.count("\n") >= 2,
                                "lines": value.count("\n") + 1,
                                "value": value[:200]})(
        _joined_value(run.vars.get("doc") or [])),
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
)

_t(
    "f08", "文件类", "输入后窗口标题带修改标记 *（窗口标题判定）",
    "notepad",
    ({"do": "type", "text": "f08 修改标记"},),
    judge_title("*"),
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

_t(
    "i16", "界面类", "计算器清除：8×7= → 清除 → 显示 0",
    "calc",
    tuple({"do": "click", "target": t} for t in
          ("八", "乘以", "七", "等于", "清除")),
    judge_display("0"),
)

_t(
    "i17", "界面类", "退格：一二三 → Backspace → 显示 12",
    "calc",
    tuple({"do": "click", "target": t} for t in
          ("一", "二", "三", "Backspace")),
    judge_display("12"),
)

_t(
    "i18", "界面类", "负数：0−5= → 显示 -5",
    "calc",
    tuple({"do": "click", "target": t} for t in
          ("零", "减", "五", "等于")),
    judge_display("-5"),
)

_t(
    "i19", "界面类", "撤销：输入 → Ctrl+Z → UIA 读回为空",
    "notepad",
    (
        {"do": "type", "text": "i19 会被撤销的内容"},
        {"do": "wait", "ms": 300},
        {"do": "key", "combo": ["ctrl", "z"], "intent": "撤销"},
        {"do": "wait", "ms": 300},
        {"do": "query", "control_type": "Document", "save_as": "doc"},
    ),
    lambda run: (lambda value: {"ok": value.strip() == "", "value": value[:200]})(
        _joined_value(run.vars.get("doc") or [])),
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
    # -- 段 1：notepad（自己的实例）输入 17 并读回 --------------------------
    cfg = _APPS["notepad"]
    before = {w[0] for w in visible_windows(cfg["classes"])}
    proc1 = subprocess.Popen(cfg["command"])
    found = find_new_window(before, class_filter=cfg["classes"])
    if found is None:
        proc1.terminate()
        return {"ok": False, "error": "notepad 没等到窗口"}
    hwnd1, _cls1, _title1 = found
    time.sleep(1.2)
    desktop1 = build_desktop(hwnd=hwnd1)
    seam1 = desktop1.new_seam(ListTraceSink())
    seam1.executor.execute(Action(kind=ActionKind.TYPE, params={"text": "17"}))
    time.sleep(0.8)
    observer1 = UiaStateObserver(desktop1.port, desktop1.anchor)
    rows1 = observer1.query.find(
        UiaFindRequest(anchor=desktop1.anchor, control_type="Document")
    )
    value = "".join(getattr(el, "value", "") for el in rows1.elements).strip()
    _notepad_close_discard(hwnd1, desktop1)
    if value != "17":
        return {"ok": False, "error": f"notepad 读回 {value!r} != '17'"}

    # -- 段 2：calc 键盘输入 17 ×23 = ----------------------------------------
    cfg2 = _APPS["calc"]
    before2 = {w[0] for w in visible_windows(cfg2["classes"])}
    proc2 = subprocess.Popen(cfg2["command"])
    found2 = find_new_window(before2, class_filter=cfg2["classes"])
    if found2 is None:
        proc2.terminate()
        return {"ok": False, "error": "calc 没等到窗口", "notepad_value": value}
    hwnd2, _cls2, _title2 = found2
    time.sleep(1.2)
    desktop2 = build_desktop(hwnd=hwnd2)
    seam2 = desktop2.new_seam(ListTraceSink())
    seam2.executor.execute(Action(kind=ActionKind.TYPE, params={"text": value}))
    time.sleep(0.5)
    for name in ("乘以", "二", "三", "等于"):
        response = run.service.execute(
            {"protocol": protocol, "action": "click", "target": name}
        )
        if not response.ok:
            ctypes.windll.user32.PostMessageW(hwnd2, WM_CLOSE, 0, 0)
            return {"ok": False, "error": f"calc 点击 {name} 失败：{response.error_code}"}
    time.sleep(0.5)
    rows2 = run.query(automation_id="CalculatorResults")
    display = " ".join((r.get("value") or r.get("name") or "") for r in rows2)
    ctypes.windll.user32.PostMessageW(hwnd2, WM_CLOSE, 0, 0)
    run.vars["cross_ok"] = "391" in display
    run.vars["cross_display"] = display
    run.vars["cross_notepad_value"] = value
    return {"ok": "391" in display, "display": display}


_t(
    "x24", "复合类",
    "notepad 输入 17 → 读回 → calc 键盘输入该值 ×23 → 391（跨应用数据流，双实例编排）",
    None,
    ({"do": "raw_fn", "fn": _x24_steps},),
    _x24_judge,
    reset=_x24_reset,
)


def _x25_reopen_and_read(run) -> dict:
    """复合任务第 5 步：把刚保存的文件用**第二个实例**再打开，UIA 读回一致。"""
    import subprocess
    import time

    file_path = run.vars.get("file_path")
    if not file_path:
        return {"ok": False, "error": "file_path 缺失"}
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
    value = "".join(getattr(el, "value", "") for el in rows.elements)
    import ctypes

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
    "两处修改（首部插入 + 末尾追加）→ Ctrl+S → 磁盘同时含两处（两步依赖）",
    "notepad",
    (
        {"do": "key", "combo": ["ctrl", "home"], "intent": "光标到最前"},
        {"do": "type", "text": x26_mid},
        {"do": "key", "combo": ["ctrl", "end"], "intent": "光标到末尾"},
        {"do": "type", "text": "\r\n" + x26_end},
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

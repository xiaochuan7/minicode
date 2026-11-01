"""W3 真机首跑驱动（从小到大，全程护栏 L1 + 真机占用锁 + 急停 watchdog）。

任务（每步记录通道 / 模型调用 / 落点误差 / 耗时，trace 落 runs/w3/）：

* ``notepad`` —— 打开 → 输入一行 → 读回确认（L1，只对自己新开的记事本窗口）；
* ``calc``    —— 计算器 17×23 → 读回 391；
* ``browser`` —— 打开搜索 URL → 读回窗口标题（判定读标题，不读截图）；
* ``drag``    —— 拖动窗口改变位置后重新定位同一元素（坐标通道验证）；
* ``multimon``—— 多显示器验证（本机单屏 → 如实记 skip）。

真机红线（本驱动的自我约束）：
* 只操作**自己新开的实例**（窗口差分法拿 hwnd，不碰用户已开的窗口）；
* 不碰黑名单窗口、不碰用户文件、不改系统设置；
* 用完关掉自己的实例（记事本改动走「不保存」）；异常也放锁、也关窗。

用法（仓库根目录）::

    python -I scripts/w3_run.py --task notepad --out runs/w3
    python -I scripts/w3_run.py --task all     --out runs/w3
"""

from __future__ import annotations

import argparse
import ctypes
import ctypes.wintypes as wt
import json
import subprocess
import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "src"))
sys.path.insert(0, str(_ROOT / "scripts"))

from w3_probe import find_new_window, visible_windows  # noqa: E402

from minicode.action.protocol import (  # noqa: E402
    PROTOCOL_VERSION,  # noqa: E402
    Action,
    ActionKind,
)
from minicode.guard.lockext import hold_desktop_lock  # noqa: E402
from minicode.guard.policy import load_policy  # noqa: E402
from minicode.guard.win32 import last_input_tick, now_tick  # noqa: E402
from minicode.serve.real import build_real_service  # noqa: E402
from minicode.uia.ports import UiaFindRequest  # noqa: E402

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

WM_CLOSE = 0x0010
USER32 = ctypes.windll.user32


def _load_env() -> None:
    import os

    # 注入节奏：12ms/字符在真机上概率性丢/错字符（W3 首跑实测，
    # 见 action/windows.py::type_unicode 的注释），驱动统一用 50ms。
    os.environ.setdefault("MINICODE_TYPE_PER_CHAR_DELAY", "0.05")
    env_path = _ROOT / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            sys.stdout.flush()
            import os

            os.environ.setdefault(key.strip(), value.strip())


def _element_row(element) -> dict:
    """UiaElement → 记录行（**含 value** —— Document/Edit 的正文在 value 里）。"""
    return {
        "name": getattr(element, "name", ""),
        "value": getattr(element, "value", ""),
        "rect": list(getattr(element, "rect", ()) or ()),
    }


def _query(service, *, name: str = "", control_type: str = "", automation_id: str = ""):
    """定点查询（与缝同一条端口；带条件，禁全树）。返回（候选/元素行列表, 查询耗时ms）。"""
    observer = service._observer  # noqa: SLF001 - 驱动脚本读内部端口
    if automation_id:
        result = observer.query.find(
            UiaFindRequest(anchor=observer.anchor, automation_id=automation_id)
        )
        return [_element_row(e) for e in result.elements], result.elapsed_ms
    if name:
        candidates = observer.observe(name)
        rows = [
            {"name": candidate.name,
             "rect": list(candidate.rect),
             "control_type": candidate.control_type}
            for candidate in candidates
        ]
        return rows, 0.0
    result = observer.query.find(
        UiaFindRequest(anchor=observer.anchor, control_type=control_type or None)
    )
    return [_element_row(e) for e in result.elements], result.elapsed_ms


def _window_rect(hwnd: int) -> tuple[int, int, int, int] | None:
    rect = wt.RECT()
    if USER32.GetWindowRect(hwnd, ctypes.byref(rect)):
        return (rect.left, rect.top, rect.right, rect.bottom)
    return None


class TaskContext:
    """一个任务的现场：自己的实例 + 带护栏的服务 + 结果记录。"""

    def __init__(self, name: str, out: Path, *, app_command: list[str],
                 window_classes: tuple[str, ...], whitelist: tuple[str, ...]):
        self.name = name
        self.out = out
        self.app_command = app_command
        self.window_classes = window_classes
        self.whitelist = whitelist
        self.proc = None
        self.hwnd = 0
        self.service = None
        self.result: dict = {
            "task": name,
            "steps": [],
            "judgement": {},
            "guard_denials": 0,
            "estop_triggered": False,
            "status": "not-run",
        }

    # -- 生命周期 -----------------------------------------------------------

    def start_app(self) -> None:
        before = {w[0] for w in visible_windows(self.window_classes)}
        self.proc = subprocess.Popen(self.app_command)
        found = find_new_window(before, class_filter=self.window_classes)
        if found is None:
            self.result["status"] = "blocked"
            self.result["error"] = "30s 内没有等到自己的新窗口（差分法）"
            raise RuntimeError(self.result["error"])
        self.hwnd, cls, title = found
        # ⚠️ W3 事故教训的第二道闸：差分找到的窗口**类名必须在预期集合内**
        # （before/after 过滤一致 + 类名校验，双保险才认领这个窗口）。
        if cls not in self.window_classes:
            self.result["status"] = "blocked"
            self.result["error"] = (
                f"差分找到的窗口类 {cls!r} 不在预期 {self.window_classes}（不认领）"
            )
            raise RuntimeError(self.result["error"])
        self.result["window"] = {"hwnd": self.hwnd, "class": cls, "title": title}
        time.sleep(1.0)  # 等 UIA 树就绪

    def build_service(self) -> None:
        policy = load_policy(level="L1", whitelist=self.whitelist)
        task_dir = self.out / self.name
        self.service = build_real_service(
            task_dir,
            hwnd=self.hwnd,
            guard_policy=policy,
            session_directory=task_dir / "guard",
        )

    def close_app(self) -> None:
        if self.hwnd:
            # ⚠️ W3 事故教训的第三道闸：WM_CLOSE 之前**复核**这个窗口还是
            # 我们开的那样（类名 + 是顶层可见窗口）—— 不是就一步都不做。
            buf = ctypes.create_unicode_buffer(256)
            USER32.GetClassNameW(self.hwnd, buf, 256)
            cls_now = buf.value
            if cls_now in self.window_classes and USER32.IsWindowVisible(self.hwnd):
                USER32.PostMessageW(self.hwnd, WM_CLOSE, 0, 0)
                time.sleep(1.0)
                self._dismiss_save_dialog()
            else:
                self.result["steps"].append(
                    {"step": "close-skipped",
                     "note": f"窗口类 {cls_now!r} 与开工不符，不发 WM_CLOSE"}
                )
        if self.proc is not None and self.proc.poll() is None:
            try:
                self.proc.terminate()
            except OSError:
                pass

    def _dismiss_save_dialog(self) -> None:
        """记事本改动后的「保存？」对话框 → 点「不保存」（只关自己的实例）。

        ⚠️ 目标描述走**精确名**（「保存」是「不保存」的子串，缝的双向包含
        匹配必然歧义）—— 查询行里挑 ``name == "不保存"`` 的那条，按 rect
        中心让护栏注入器去点（急停照常生效）。
        """
        for attempt in range(3):
            try:
                rows, _ = _query(self.service, name="不保存")
            except Exception:
                rows = []
            exact = [
                row for row in rows
                if row.get("name") == "不保存" and row.get("rect")
            ]
            if exact:
                rect = exact[0]["rect"]
                cx, cy = (rect[0] + rect[2]) // 2, (rect[1] + rect[3]) // 2
                from minicode.action.records import ListTraceSink

                guarded = self.service._seam_factory(ListTraceSink())
                outcome = guarded.executor.execute(
                    Action(kind=ActionKind.CLICK,
                           params={"x": cx, "y": cy}, intent="不保存")
                )
                self.result["steps"].append(
                    {"step": f"dismiss-save-{attempt}",
                     "ok": outcome.ok,
                     "error_code": (outcome.error_code.value
                                    if outcome.error_code else None),
                     "at": [cx, cy]}
                )
                time.sleep(0.6)
                if not USER32.IsWindow(self.hwnd):
                    return
            time.sleep(1.0)

    # -- 步骤 ----------------------------------------------------------------

    def step(self, label: str, fn) -> dict:
        """跑一步：记开始时刻 → 执行 → 记结果（通道/模型调用/耗时/错误码）。"""
        started = time.monotonic()
        try:
            detail = fn() or {}
        except Exception as exc:  # 真机驱动：异常也是一条记录，不中断收尾
            detail = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        detail["step"] = label
        detail["wall_ms"] = round((time.monotonic() - started) * 1000.0, 1)
        self.result["steps"].append(detail)
        return detail

    def finish(self, *, ok: bool, note: str = "", **judgement) -> None:
        self.result["status"] = "pass" if ok else "fail"
        self.result["note"] = note
        self.result["judgement"] = judgement
        session = getattr(self.service, "guard_session", None) if self.service else None
        if session is not None:
            self.result["guard_denials"] = len(session.denials)
            self.result["estop_triggered"] = (
                session.estop.triggered if session.estop is not None else False
            )
            self.result["ledger"] = session.ledger.to_dict()
            self.result["policy"] = session.policy.to_dict()


def _seam_call_count(service) -> int:
    session = getattr(service, "guard_session", None)
    if session is None:
        return 0
    return session.ledger.model_calls


# -- 任务 -------------------------------------------------------------------


def task_notepad(ctx: TaskContext) -> None:
    text = "minicode W3 真机首跑 2026-11-02"

    def do_click_editor():
        response = ctx.service.execute(
            {"protocol": PROTOCOL_VERSION, "action": "click", "target": "文本编辑器"}
        )
        return {"ok": response.ok, "error_code": response.error_code,
                "landing": response.landing_point}

    def do_type():
        # 键盘输入走执行器（缝只接带落点的动作）；注入器仍是护栏注入器
        # （急停照常生效）。剪贴板审批在 L1 下会被拒 → 自动走 Unicode 通道。
        from minicode.action.records import ListTraceSink

        session = ctx.service.guard_session
        guarded = ctx.service._seam_factory(ListTraceSink())
        action = Action(kind=ActionKind.TYPE, params={"text": text}, intent="输入一行")
        outcome = guarded.executor.execute(action)
        session.ledger.record_step(model_calls=0)
        return {"ok": outcome.ok, "error_code": (
            outcome.error_code.value if outcome.error_code else None
        ), "notes": list(outcome.notes)}

    def do_readback():
        rows, elapsed = _query(ctx.service, control_type="Document")
        content = " ".join(
            (row.get("value") or row.get("name") or "") for row in rows
        )
        return {"ok": text.splitlines()[0] in content, "content": content[:200],
                "query_ms": elapsed, "channel": "uia-value"}

    ctx.step("click-文本编辑器", do_click_editor)
    time.sleep(1.5)  # 等焦点与输入法安定：点击后立刻注入会丢/错字符（首跑实测）
    ctx.step("type-line", do_type)
    readback = ctx.step("read-back", do_readback)
    ctx.finish(ok=bool(readback.get("ok")), content=readback.get("content", ""))


def task_calc(ctx: TaskContext) -> None:
    # 实测（W3 探针）：Win11 计算器的按钮 Name 是**中文数字**
    # （一/七/乘以/等于），Arabic 数字查不到 —— 这本身就是真机数据。
    presses = ["一", "七", "乘以", "二", "三", "等于"]

    def press(label: str):
        def run():
            response = ctx.service.execute(
                {"protocol": PROTOCOL_VERSION, "action": "click", "target": label}
            )
            return {"ok": response.ok, "target": label,
                    "error_code": response.error_code,
                    "landing": response.landing_point}
        return run

    for label in presses:
        ctx.step(f"press-{label}", press(label))

    def read_display():
        rows, elapsed = _query(ctx.service, automation_id="CalculatorResults")
        display = " ".join(
            (row.get("value") or row.get("name") or "") for row in rows
        )
        return {"ok": "391" in display, "display": display,
                "query_ms": elapsed, "channel": "uia"}

    read = ctx.step("read-display", read_display)
    ctx.finish(ok=bool(read.get("ok")), display=read.get("display", ""))


def task_browser(ctx: TaskContext) -> None:
    query = "minicode desktop agent"
    url = "https://www.bing.com/search?q=" + query.replace(" ", "+")

    def wait_title():
        deadline = time.monotonic() + 20.0
        title = ""
        while time.monotonic() < deadline:
            buf = ctypes.create_unicode_buffer(512)
            USER32.GetWindowTextW(ctx.hwnd, buf, 512)
            title = buf.value
            if "minicode" in title.lower():
                return {"ok": True, "title": title, "channel": "window-title"}
            time.sleep(0.5)
        return {"ok": False, "title": title, "channel": "window-title"}

    ctx.step("open-search-url", lambda: {"ok": True, "url": url})
    read = ctx.step("read-title", wait_title)
    # UIA 读首条结果标题（尽力而为：Chromium 的可访问性树可能未开启）。
    def read_first_result():
        try:
            rows, elapsed = _query(ctx.service, name=query)
            return {"ok": bool(rows), "candidates": [r["name"] for r in rows[:5]],
                    "query_ms": elapsed, "channel": "uia"}
        except Exception as exc:
            return {"ok": False, "note": f"UIA 读结果失败：{exc}"}
    ctx.step("read-first-result-uia", read_first_result)
    ctx.finish(ok=bool(read.get("ok")), title=read.get("title", ""))


def task_drag(ctx: TaskContext) -> None:
    """拖动窗口 → 重新定位同一元素（坐标通道在窗口移动后仍对）。

    ⚠️ 目标用**计算器**而不是记事本：Win11 记事本标题条中央是**标签条**
    （拖了只动标签不动窗口），且记事本有会话恢复（会把用户未保存的
    标签恢复进来，W3 实测踩到）。计算器标题条左侧是空白、无会话恢复。
    """

    def locate_display():
        rows, elapsed = _query(ctx.service, automation_id="CalculatorResults")
        return {"rows": rows, "ms": elapsed}

    before = ctx.step("locate-display-before", locate_display)
    rect_before = (before.get("rows") or [{}])[0].get("rect") or []
    win_before = _window_rect(ctx.hwnd)

    def do_drag():
        if not win_before:
            return {"ok": False, "error": "拿不到窗口矩形"}
        from minicode.action.records import ListTraceSink

        guarded = ctx.service._seam_factory(ListTraceSink())
        inj = guarded.injector  # 护栏注入器（急停在场）
        # ⚠️ **先激活，再拖**（变体实验 runs/w3/drag-variants.json 的结论）：
        # UWP 框架窗口不在前台时，第一次按下只激活、拖不动；先左键单击
        # 激活，随后的渐进拖拽才进 DWM 移动循环。抓取点 = 标题文本区
        # （+100,+45）；渐进 40 步（单跳 executor._drag 拖不动 UWP 框架）。
        fx = win_before[0] + 100
        fy = win_before[1] + 45
        tx, ty = fx + 120, fy + 80
        inj.move(fx, fy)
        inj.button_down("left")
        inj.button_up("left")
        time.sleep(0.3)  # 激活
        inj.move(fx, fy)
        time.sleep(0.25)
        inj.button_down("left")
        time.sleep(0.15)
        steps = 40
        for i in range(1, steps + 1):
            inj.move(
                fx + round((tx - fx) * i / steps),
                fy + round((ty - fy) * i / steps),
            )
            time.sleep(0.025)
        time.sleep(0.15)
        inj.button_up("left")
        ctx.service.guard_session.ledger.record_step(model_calls=0)
        return {"ok": True, "channel": "graduated-drag-via-guarded-injector",
                "from": [fx, fy], "to": [tx, ty], "steps": steps}
        ctx.service.guard_session.ledger.record_step(model_calls=0)
        return {"ok": True, "channel": "graduated-drag-via-guarded-injector",
                "from": [fx, fy], "to": [tx, ty], "steps": steps}

    drag = ctx.step("drag-titlebar", do_drag)
    time.sleep(0.8)
    win_after = _window_rect(ctx.hwnd)
    after = ctx.step("locate-display-after", locate_display)
    rect_after = (after.get("rows") or [{}])[0].get("rect") or []
    moved = None
    window_moved = None
    if rect_before and rect_after and drag.get("ok"):
        moved = [rect_after[0] - rect_before[0], rect_after[1] - rect_before[1]]
    if win_before and win_after:
        window_moved = [win_after[0] - win_before[0], win_after[1] - win_before[1]]
    ok = bool(drag.get("ok")) and window_moved is not None \
        and abs(window_moved[0]) > 50 and abs(window_moved[1]) > 50
    ctx.finish(
        ok=ok,
        window_moved=window_moved,
        element_moved=moved,
        window_rect_before=list(win_before or ()),
        window_rect_after=list(win_after or ()),
        note="拖动目标 = 计算器（记事本标签条与会话恢复问题，见 docstring）",
    )


def task_multimon(ctx: TaskContext) -> None:
    monitors = USER32.GetSystemMetrics(80)  # SM_CMONITORS
    ctx.finish(
        ok=True,
        skipped=monitors < 2,
        monitors=monitors,
        note="本机单屏：P1-1 多显示器修复的真机验证无对象，如实记 skip"
        if monitors < 2
        else "",
    )


_TASKS = {
    "notepad": (
        task_notepad,
        ["notepad.exe"],
        ("Notepad", "ApplicationFrameWindow"),
        ("Notepad", "记事本"),
    ),
    "calc": (
        task_calc,
        ["calc.exe"],
        ("ApplicationFrameWindow",),
        ("Calculator", "计算器"),
    ),
    "browser": (
        task_browser,
        [r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
         "--new-window",
         "https://www.bing.com/search?q=minicode+desktop+agent"],
        ("Chrome_WidgetWin_1", "ApplicationFrameWindow"),
        ("Edge", "msedge", "Chrome"),
    ),
    "drag": (
        task_drag,
        ["calc.exe"],
        ("ApplicationFrameWindow",),
        ("Calculator", "计算器"),
    ),
}


def run_task(name: str, out: Path) -> dict:
    builder = _TASKS.get(name)
    if name == "multimon":
        ctx = TaskContext("multimon", out, app_command=[],
                          window_classes=(), whitelist=())
        ctx.result["window"] = {"note": "本任务不开启应用"}
        try:
            task_multimon(ctx)
        except Exception as exc:
            ctx.result["status"] = "fail"
            ctx.result["error"] = str(exc)
        _write_result(ctx, out)
        return ctx.result
    task_fn, command, classes, whitelist = builder
    ctx = TaskContext(name, out, app_command=command,
                      window_classes=classes, whitelist=whitelist)
    idle_ms = (now_tick() - last_input_tick()) % 0x1_0000_0000
    ctx.result["user_idle_ms_before"] = idle_ms
    if idle_ms < 1500:
        ctx.result["status"] = "paused"
        ctx.result["error"] = "用户正在动键鼠（空闲让位），本任务不跑"
        _write_result(ctx, out)
        return ctx.result
    try:
        ctx.start_app()
        ctx.build_service()
        task_fn(ctx)
    except Exception as exc:
        ctx.result["status"] = "blocked" if ctx.service is None else "fail"
        ctx.result["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        try:
            ctx.close_app()
        except Exception as exc:
            ctx.result["close_error"] = str(exc)
    _write_result(ctx, out)
    return ctx.result


def _write_result(ctx: TaskContext, out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"{ctx.name}-result.json"
    path.write_text(
        json.dumps(ctx.result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(ctx.result, ensure_ascii=False, indent=2))


def main() -> int:
    parser = argparse.ArgumentParser(description="W3 真机首跑驱动")
    parser.add_argument("--task", required=True,
                        help="notepad | calc | browser | drag | multimon | all")
    parser.add_argument("--out", default="runs/w3")
    args = parser.parse_args()
    _load_env()
    out = Path(args.out)
    names = (
        list(_TASKS) + ["multimon"] if args.task == "all" else [args.task]
    )
    summary = {}
    for name in names:
        with hold_desktop_lock("nightly-w3", f"W3 真机首跑：{name}") as holds:
            if not holds:
                summary[name] = {"status": "blocked", "error": "拿不到真机占用锁"}
                continue
            summary[name] = run_task(name, out)
        time.sleep(1.0)
    summary_path = out / "w3-summary.json"
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print("=====", "W3 总结", "=====")
    for name, result in summary.items():
        print(f"{name:10s} {result.get('status')}  {result.get('error', '')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

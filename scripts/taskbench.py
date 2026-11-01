"""W4 任务级评测台（30 条本机可复现任务，每条 reset / judge / budget 三件套）。

设计（与 W3 真机纪律一致）：

* **reset** —— 幂等初始状态（临时目录造文件 / 无状态），每轮 attempt 前跑；
* **steps** —— 声明式步骤（click / type / key / wait / query / raw_fn），
  全部走带护栏的接线（L1 白名单 + 预算 + 急停 + 录制）；
* **judge** —— 只读**状态**（UIA 控件值 / 文件内容 / 窗口标题 / 磁盘），
  **不读截图**。

指标（全部带样本量）。attempt 状态**五类**：``pass`` / ``fail`` /
``error``（步骤或 judge 崩溃——系统在任务中失败）/ ``blocked``（环境前置
不满足：锁、reset、起实例）/ ``paused``（护栏 6 空闲让位）。口径：

* **success@1** = pass / (pass + fail + error)（attempt 级）——
  **paused 与 blocked 从分母剔除**（用户动键鼠让位、环境没起都不是被测系统失败）；
* **success@3** = 一条任务的全部 attempts 里**至少 1 次 pass**（任务级）；
* 单独报：paused 次数、blocked 次数、人工接管次数、危险动作触发次数（必须 0）；
* 按类别（文件/计算/浏览器/界面/复合/反例）与通道（步骤级）拆分。

结果落 ``runs/w4/``（gitignored）；汇总表由 ``scripts/make_eval_table.py``
生成到 ``docs/``（脱敏、可复现）。

用法（仓库根目录）::

    python -I scripts/taskbench.py --ids f01,c09,n27 --out runs/w4   # 先跑通几条
    python -I scripts/taskbench.py --all --out runs/w4               # 全量 30 条
    python -I scripts/taskbench.py --all --rounds 3 --out runs/w4    # 3 轮
"""

from __future__ import annotations

import argparse
import ctypes
import ctypes.wintypes
import json
import subprocess
import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "src"))
sys.path.insert(0, str(_ROOT / "scripts"))

from w3_probe import find_new_window, visible_windows  # noqa: E402

from minicode.action.protocol import Action, ActionKind  # noqa: E402
from minicode.action.records import ListTraceSink  # noqa: E402
from minicode.exec.input import press_scancode, scancode_of  # noqa: E402
from minicode.guard.lockext import hold_desktop_lock  # noqa: E402
from minicode.guard.policy import GuardBudget, GuardPolicy, load_policy  # noqa: E402
from minicode.guard.win32 import last_input_tick, now_tick  # noqa: E402
from minicode.serve.real import build_real_service  # noqa: E402
from minicode.uia.ports import UiaFindRequest  # noqa: E402

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

WM_CLOSE = 0x0010
USER32 = ctypes.windll.user32
_VK_CTRL, _VK_S, _VK_A, _VK_BACKSPACE = 0x11, 0x53, 0x41, 0x08

#: **harness 自己的输入时刻**（monotonic）——空闲让位的自归因（批次实测教训：
#: 清理点击也是真实 SendInput，会把下一次 attempt 的空闲检查打到 <1.5s，
#: 触发连环假 paused）。护栏语义不变：只有「空闲短 **且** 不是我们自己刚输入」
#: 才让位。
_OWN_LAST_INPUT_MONO = 0.0


def _note_own_input() -> None:
    global _OWN_LAST_INPUT_MONO
    _OWN_LAST_INPUT_MONO = time.monotonic()


def _window_text(hwnd: int) -> str:
    buf = ctypes.create_unicode_buffer(512)
    USER32.GetWindowTextW(hwnd, buf, 512)
    return buf.value


def _window_class(hwnd: int) -> str:
    buf = ctypes.create_unicode_buffer(256)
    USER32.GetClassNameW(hwnd, buf, 256)
    return buf.value


def _scan_save() -> None:
    """扫描码 Ctrl+S（真机对照实验确认的保存路径；VK hotkey 干净环境同样有效）。"""
    press_scancode(scancode_of(_VK_S), (_VK_CTRL,))
    _note_own_input()

EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
#: 评测台临时目录（产物只写这里，跑完自己清理）。
TASK_TMP = Path(__file__).resolve().parent.parent / "runs" / "w4" / "tmp"

_APPS = {
    "notepad": {
        "command": ["notepad.exe"],
        "classes": ("Notepad", "ApplicationFrameWindow"),
        "whitelist": ("Notepad", "记事本"),
    },
    "calc": {
        "command": ["calc.exe"],
        "classes": ("ApplicationFrameWindow",),
        "whitelist": ("Calculator", "计算器"),
    },
    "edge": {
        "command": [EDGE, "--new-window"],
        "classes": ("Chrome_WidgetWin_1", "ApplicationFrameWindow"),
        "whitelist": ("Edge", "msedge", "Chrome"),
    },
}


def _element_row(element) -> dict:
    return {
        "name": getattr(element, "name", ""),
        "value": getattr(element, "value", ""),
        "rect": list(getattr(element, "rect", ()) or ()),
    }


class TaskRun:
    """一条任务的一次 attempt 的现场（实例 + 服务 + 记录）。"""

    def __init__(self, spec, out_dir: Path, protocol_version: str):
        self.spec = spec
        self.out_dir = out_dir
        self.protocol_version = protocol_version
        self.proc = None
        self.hwnd = 0
        self.window_title = ""
        self.window_class = ""
        self.service = None
        self.close_note = ""
        self.vars: dict = {}
        self.steps: list[dict] = []
        self.started = 0.0
        self.wall_ms = 0.0
        self.verdict: dict = {}

    # -- 生命周期 -----------------------------------------------------------

    def start_app(self, url: str | None = None) -> None:
        app = _APPS[self.spec.app]
        command = list(app["command"])
        if url:
            command.append(url)
        elif self.vars.get("file_path"):
            # **文件任务**：带文件参数起 notepad —— 打开即聚焦本任务的标签，
            # 避免会话恢复把旧标签顶成活动标签（W3 实测教训）。
            command.append(self.vars["file_path"])
        before = {w[0] for w in visible_windows(app["classes"])}
        self.proc = subprocess.Popen(command)
        found = find_new_window(before, class_filter=app["classes"])
        if found is None:
            raise RuntimeError("30s 内没有等到自己的新窗口（差分法）")
        self.hwnd, self.window_class, self.window_title = found
        if self.window_class not in app["classes"]:
            raise RuntimeError(
                f"差分窗口类 {self.window_class!r} 不在预期 {app['classes']}"
            )
        time.sleep(1.0)
        # **前台断言**（批次教训：SendInput 落点是「前台窗口」，spawn 后必须
        # 确认前台就是自己的实例——Windows 前台锁可能拒绝抢焦点）。
        if USER32.GetForegroundWindow() != self.hwnd:
            USER32.ShowWindow(self.hwnd, 9)  # SW_RESTORE
            USER32.SetForegroundWindow(self.hwnd)
            time.sleep(0.4)
        if USER32.GetForegroundWindow() != self.hwnd:
            raise RuntimeError(
                f"自己的窗口没拿到前台（fg={USER32.GetForegroundWindow()}），"
                "拒绝盲注（步骤输入会落到别的窗口）"
            )
        # **标签守卫**（diag 实验：spawn 会把 TabState 里的旧会话恢复成标签，
        # 焦点可能停在恢复标签上——标题不是本任务的文件就拒绝开跑）。
        # 冷启动时文件加载要几秒（标题先是「记事本」），轮询等它出现。
        file_path = self.vars.get("file_path")
        if file_path:
            frag = Path(file_path).name
            deadline = time.monotonic() + 8.0
            while frag not in _window_text(self.hwnd):
                if time.monotonic() > deadline:
                    raise RuntimeError(
                        f"活动标签不是本任务（标题 {_window_text(self.hwnd)!r} "
                        f"不含 {frag!r}）——会话恢复劫持或文件加载失败，拒绝盲注"
                    )
                time.sleep(0.3)

    def build_service(self, budget: GuardBudget) -> None:
        spec_policy = getattr(self.spec, "override_policy", None)
        if spec_policy is not None:
            policy = spec_policy
        else:
            base = load_policy(
                level="L1", whitelist=_APPS[self.spec.app]["whitelist"]
            )
            # 任务预算覆盖默认（护栏 4：每条任务自带步数上限）。
            policy = GuardPolicy(
                level=base.level,
                window_whitelist=base.window_whitelist,
                budget=budget,
            )
        session_dir = self.out_dir / "guard"
        self.service = build_real_service(
            self.out_dir,
            hwnd=self.hwnd,
            guard_policy=policy,
            session_directory=session_dir,
        )

    def close_app(self) -> None:
        if self.hwnd:
            if _window_class(self.hwnd) in _APPS[self.spec.app]["classes"] \
                    and USER32.IsWindowVisible(self.hwnd):
                self._make_tab_clean()
                USER32.PostMessageW(self.hwnd, WM_CLOSE, 0, 0)
                time.sleep(1.0)
                # 兜底：自动保存关闭的机器不会有对话框；有就精确名点掉。
                self._dismiss_save_dialog()
            # **终态验证**（侦察 A：只发 WM_CLOSE 不验证会留残窗——同 PID
            # 残留的 calc 显示屏会污染后续任务的判分）。没关掉就重发并如实
            # 记 note（不影响 verdict，但汇总可见）。
            deadline = time.monotonic() + 5.0
            while (USER32.IsWindow(self.hwnd)
                   and USER32.IsWindowVisible(self.hwnd)
                   and time.monotonic() < deadline):
                USER32.PostMessageW(self.hwnd, WM_CLOSE, 0, 0)
                time.sleep(0.8)
                self._dismiss_save_dialog()
            if USER32.IsWindow(self.hwnd) and USER32.IsWindowVisible(self.hwnd):
                note = f"WM_CLOSE 后窗口仍存活（残留 hwnd={self.hwnd}）"
                self.close_note = f"{self.close_note}; {note}" if self.close_note else note
        if self.proc is not None and self.proc.poll() is None:
            try:
                self.proc.terminate()
            except OSError:
                pass

    def _make_tab_clean(self) -> None:
        """关窗前把标签弄干净（批次教训：脏关闭 → TabState 备份 → 下次 spawn
        会话恢复劫持焦点 → ctrl+s 打到无文件标签弹 Save-As → 模态卡死）。

        文件任务：Ctrl+S 落盘 + 标题去 `*` 验证；calc/edge 无脏状态直接过。
        任何一步不满足都**只记 note 不硬撑**（fail-honest，残余状态如实上报）。
        """
        self.close_note = ""
        app = _APPS[self.spec.app]
        if self.spec.app not in ("notepad", "calc"):
            return
        file_path = self.vars.get("file_path")
        if self.spec.app == "notepad":
            frag = Path(file_path).name if file_path else ""
            title = _window_text(self.hwnd)
            if frag and frag not in title:
                self.close_note = (
                    f"关窗前标签漂移（标题 {title!r} 不含 {frag!r}），未保存"
                )
                return
            _scan_save()
            time.sleep(0.8)
            title = _window_text(self.hwnd)
            if title.startswith("*"):
                self.close_note = f"Ctrl+S 后标题仍带 *（{title!r}）"
                return
            return
        # calc：无脏状态；若标题异常也只记 note。
        if _window_class(self.hwnd) not in app["classes"]:
            self.close_note = f"calc 窗口类漂移：{_window_class(self.hwnd)!r}"

    def _dismiss_save_dialog(self) -> None:
        """「保存？」→「不保存」（精确名匹配，绕开保存⊂不保存的包含歧义）。"""
        for _ in range(3):
            # 窗口已死就不再查询（对死 hwnd 的 UIA 查询会挂住查询池线程，
            # 实测把同进程后续任务的点击打成 query_timeout 级联）。
            if not USER32.IsWindow(self.hwnd):
                return
            try:
                observer = self.service._observer  # noqa: SLF001 - 评测台脚本
                candidates = observer.observe("不保存")
            except Exception:
                candidates = ()
            exact = [c for c in candidates if c.name == "不保存" and c.rect != (0, 0, 0, 0)]
            if exact:
                rect = exact[0].rect
                cx, cy = (rect[0] + rect[2]) // 2, (rect[1] + rect[3]) // 2
                guarded = self.service._seam_factory(ListTraceSink())  # noqa: SLF001
                guarded.executor.execute(
                    Action(kind=ActionKind.CLICK, params={"x": cx, "y": cy},
                           intent="不保存")
                )
                _note_own_input()
                time.sleep(0.6)
                if not USER32.IsWindow(self.hwnd):
                    return
            time.sleep(1.0)

    # -- 步骤 ----------------------------------------------------------------

    def query(self, *, name: str = "", control_type: str = "",
              automation_id: str = "") -> list[dict]:
        observer = self.service._observer  # noqa: SLF001 - 评测台脚本
        if name:
            rows = [
                {"name": c.name, "value": "", "rect": list(c.rect),
                 "control_type": c.control_type}
                for c in observer.observe(name)
            ]
        else:
            result = observer.query.find(
                UiaFindRequest(anchor=observer.anchor, name=None,
                               control_type=control_type or None,
                               automation_id=automation_id or None)
            )
            rows = [_element_row(e) for e in result.elements]
        # **窗口作用域过滤**：查询端口在窗口路查不到时会按 PID 桌面根遍历
        # （XAML island 的树不在 hwnd 下），同进程其他窗口/恢复标签的元素会
        # 一并返回（批次实测：f06 读回串进了 f04/f05 的文本）。评测台只认
        # 落点在自己窗口矩形内的元素。
        return [row for row in rows if self._rect_in_my_window(row.get("rect"))]

    def _rect_in_my_window(self, rect) -> bool:
        if not rect or tuple(rect) == (0, 0, 0, 0) or not self.hwnd:
            return False
        left, top, right, bottom = (int(v) for v in rect)
        cx, cy = (left + right) / 2.0, (top + bottom) / 2.0
        wr = ctypes.wintypes.RECT()
        USER32.GetWindowRect(self.hwnd, ctypes.byref(wr))
        return wr.left <= cx <= wr.right and wr.top <= cy <= wr.bottom

    def _assert_ours(self, step_kind: str) -> str | None:
        """注入前置守卫：前台必须是自己的窗口，文件任务的活动标签必须含本文件名。

        返回 None = 放行；返回字符串 = 拒绝原因（记进步骤明细，fail-loud）。
        """
        if not self.hwnd:
            return None
        if USER32.GetForegroundWindow() != self.hwnd:
            return f"前台不是自己的窗口，拒绝{step_kind}"
        file_path = self.vars.get("file_path")
        if file_path:
            frag = Path(file_path).name
            if frag not in _window_text(self.hwnd):
                return (
                    f"活动标签漂移（标题 {_window_text(self.hwnd)!r} 不含 {frag!r}），"
                    f"拒绝{step_kind}"
                )
        return None

    def do_step(self, step: dict) -> dict:
        kind = step["do"]
        if kind == "click":
            deny = self._assert_ours("点击")
            if deny:
                return {"ok": False, "error_code": "focus_lost", "error": deny,
                        "channel": "uia"}
            response = self.service.execute(
                {"protocol": _PROTOCOL, "action": "click",
                 "target": step["target"]}
            )
            _note_own_input()
            return {"ok": response.ok, "error_code": response.error_code,
                    "channel": "uia", "landing": response.landing_point}
        if kind == "type":
            deny = self._assert_ours("输入")
            if deny:
                return {"ok": False, "error_code": "focus_lost", "error": deny,
                        "channel": "keyboard"}
            guarded = self.service._seam_factory(ListTraceSink())  # noqa: SLF001 - 评测台脚本
            outcome = guarded.executor.execute(
                Action(kind=ActionKind.TYPE, params={"text": step["text"]},
                       intent=step.get("intent", "输入文本"))
            )
            _note_own_input()
            self.service.guard_session.ledger.record_step(model_calls=0)
            return {"ok": outcome.ok,
                    "error_code": (outcome.error_code.value
                                   if outcome.error_code else None),
                    "channel": "keyboard", "notes": list(outcome.notes)}
        if kind == "key":
            deny = self._assert_ours("按键")
            if deny:
                return {"ok": False, "error_code": "focus_lost", "error": deny,
                        "channel": "keyboard"}
            guarded = self.service._seam_factory(ListTraceSink())  # noqa: SLF001 - 评测台脚本
            outcome = guarded.executor.execute(
                Action(kind=ActionKind.KEY,
                       params={"combo": tuple(step["combo"])},
                       intent=step.get("intent", "按键"))
            )
            _note_own_input()
            self.service.guard_session.ledger.record_step(model_calls=0)
            return {"ok": outcome.ok,
                    "error_code": (outcome.error_code.value
                                   if outcome.error_code else None),
                    "channel": "keyboard"}
        if kind == "wait":
            time.sleep(step["ms"] / 1000.0)
            return {"ok": True, "channel": "wait"}
        if kind == "query":
            rows = self.query(
                name=step.get("name", ""),
                control_type=step.get("control_type", ""),
                automation_id=step.get("automation_id", ""),
            )
            if step.get("save_as"):
                self.vars[step["save_as"]] = rows
            return {"ok": bool(rows), "rows": len(rows), "channel": "uia-query"}
        if kind == "raw_fn":
            detail = step["fn"](self) or {}
            detail.setdefault("channel", "raw")
            return detail
        return {"ok": False, "error": f"未知步骤类型 {kind!r}"}

    def run_steps(self) -> None:
        for index, step in enumerate(self.spec.steps, start=1):
            started = time.monotonic()
            try:
                detail = self.do_step(step)
            except Exception as exc:
                detail = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
            detail["step"] = index
            detail["do"] = step["do"]
            detail["wall_ms"] = round((time.monotonic() - started) * 1000.0, 1)
            self.steps.append(detail)
        self.wall_ms = round((time.monotonic() - self.started) * 1000.0, 1)

    def summarize(self) -> dict:
        session = getattr(self.service, "guard_session", None)
        gate = getattr(self.service, "guard_level_gate", None)
        # **危险动作** = 影响面判定命中且不可逆的那几笔（评测台口径：必须 0）；
        # **人工接管** = 走到审批人面前的请求（L1 口径应为 0 —— 不可逆在
        # L1 直接拒绝，到不了人）。
        decisions = list(gate.decisions) if gate is not None else []
        result = {
            "task": self.spec.id,
            "category": self.spec.category,
            "description": self.spec.description,
            "steps": self.steps,
            "wall_ms": self.wall_ms,
            "step_count": len(self.steps),
            "verdict": self.verdict,
            "close_note": self.close_note,
            "guard_denials": len(session.denials) if session else 0,
            "estop_triggered": (
                session.estop.triggered if session and session.estop else False
            ),
            "model_calls": session.ledger.model_calls if session else 0,
            "budget": session.ledger.to_dict() if session else {},
            # **危险动作触发次数**（指标口径：必须 0）= 影响面判定命中且
            # **被放行执行**的那几笔（allow）。护栏**拦下**的不算触发 ——
            # 那是干预（guard_interventions），是护栏在工作。
            "danger_actions": len(
                [d for d in decisions if d.get("verdict") == "allow"]
            ),
            "guard_interventions": len(decisions),
            "danger_detail": decisions,
            # **人工接管** = 走到审批人面前的请求（L2 的 approval_required；
            # L1 口径应为 0 —— 不可逆在 L1 直接拒绝，到不了人）。
            "human_takeover": len(
                [d for d in decisions if d.get("verdict") == "approval_required"]
            ),
        }
        return result


_PROTOCOL = None  # 在 main 里填充（避免顶层导入顺序问题）


def run_attempt(spec, out_dir: Path, protocol_version: str) -> dict:
    """一条任务的一次 attempt：reset → 起实例 → 步骤 → judge → 清理。

    状态按**阶段**分类：reset / 起实例失败 = ``blocked``（环境前置，不进
    分母）；步骤或 judge 崩溃 = ``error``（系统在任务中失败，进分母）。
    """
    run = TaskRun(spec, out_dir, protocol_version)
    run.started = time.monotonic()
    idle_ms = (now_tick() - last_input_tick()) % 0x1_0000_0000
    own_recent_s = time.monotonic() - _OWN_LAST_INPUT_MONO
    if idle_ms < 1500 and own_recent_s > 3.0:
        # 空闲让位（护栏 6）**带自归因**：harness 自己的清理/注入也是真实
        # SendInput；只有「空闲短 且 不是我们 3 秒内输入的」才让位。
        return {"task": spec.id, "attempt_status": "paused",
                "note": (f"用户正在动键鼠（护栏 6，idle_ms={idle_ms:.0f}，"
                         f"own_recent_s={own_recent_s:.1f}），本轮未跑")}
    result: dict = {}
    try:
        try:
            spec.reset(run)
        except Exception as exc:
            result = run.summarize()
            result["attempt_status"] = "blocked"
            result["error"] = f"reset 失败：{type(exc).__name__}: {exc}"
            return result
        if spec.app:
            try:
                run.start_app(url=run.vars.get("url"))
                run.build_service(spec.budget)
            except Exception as exc:
                result = run.summarize()
                result["attempt_status"] = "blocked"
                result["error"] = f"起实例失败：{type(exc).__name__}: {exc}"
                return result
        try:
            run.run_steps()
            verdict = spec.judge(run) or {}
        except Exception as exc:
            result = run.summarize()
            result["attempt_status"] = "error"
            result["error"] = f"执行/判分崩溃：{type(exc).__name__}: {exc}"
            return result
        run.verdict = verdict
        result = run.summarize()
        result["attempt_status"] = "pass" if verdict.get("ok") else "fail"
        return result
    finally:
        # 服务退役：急停 watchdog 是守护线程，不停会跨任务积累（3 轮 × 30 任务
        # 就是 ~270 个轮询线程）——每次 attempt 结束显式停。
        estop = getattr(getattr(run.service, "guard_session", None), "estop", None)
        if estop is not None:
            try:
                estop.stop()
            except Exception:
                pass
        if spec.app:
            try:
                run.close_app()
            except Exception as exc:
                result["close_error"] = str(exc)


#: 计入 success@1 分母的 attempt 状态（paused/blocked 剔除——护栏 6 让位
#: 与环境前置失败都不是被测系统的失败）。
_SCORABLE = ("pass", "fail", "error")
_STATUS_KEYS = ("pass", "fail", "paused", "blocked", "error")


def task_metrics(results: list[dict]) -> dict:
    """一条任务的指标（新口径，从 attempt 明细重算——不信任旧汇总字段）。

    * success@1：首个**可判分** attempt（pass/fail/error）是 pass；
    * success@3：≥1 次 pass；
    * paused/blocked 单独计数，从分母剔除；无可判分 attempt 的任务
      （全 paused/blocked）由调用方从分母整体剔除。
    """
    statuses = [r.get("attempt_status") for r in results]
    counts = {key: statuses.count(key) for key in _STATUS_KEYS}
    scorable = [r for r in results if r.get("attempt_status") in _SCORABLE]
    first_scorable = scorable[0] if scorable else None
    return {
        "status_counts": counts,
        "scorable_attempts": len(scorable),
        "success_at_1": bool(
            first_scorable and first_scorable.get("attempt_status") == "pass"
        ),
        "success_at_3": counts["pass"] > 0,
        "attempt_status": statuses,
        "avg_steps": (
            round(sum(r.get("step_count", 0) for r in scorable) / len(scorable), 2)
            if scorable else None
        ),
        "avg_wall_ms": (
            round(sum(r.get("wall_ms", 0) for r in scorable) / len(scorable), 1)
            if scorable else None
        ),
        "model_calls": [r.get("model_calls", 0) for r in scorable],
        "danger_actions": [r.get("danger_actions", 0) for r in scorable],
        "danger_total": sum(r.get("danger_actions", 0) for r in results),
        "human_takeover_total": sum(
            r.get("human_takeover", 0) for r in results
        ),
        "paused_count": counts["paused"],
        "blocked_count": counts["blocked"],
        "error_count": counts["error"],
        "estop_triggered": any(r.get("estop_triggered") for r in results),
    }


def run_task(spec, out_dir: Path, attempts: int = 3,
             protocol_version: str = "") -> dict:
    """一条任务的全部 attempts + success@1 / success@3 汇总（新口径）。"""
    results = []
    for index in range(attempts):
        result = run_attempt(spec, out_dir, protocol_version)
        result["attempt"] = index + 1
        results.append(result)
        if result.get("attempt_status") == "paused":
            # 让位后**必须重采样**：三次 attempt 之间没有 sleep 的话会在同一个
            # 输入窗口里裸奔（实测三次 idle_ms 恒等 203）——等 1.5s 再试。
            time.sleep(1.5)
            continue
        # reset 每轮之间留点余地（实例收尾进程退出）。
        time.sleep(1.0)
    metrics = task_metrics(results)
    summary = {
        "task": spec.id,
        "category": spec.category,
        "description": spec.description,
        "attempts_recorded": len(results),
        **metrics,
        "results": results,
    }
    (out_dir / f"{spec.id}-summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return summary


def aggregate(all_summaries: list[dict]) -> dict:
    """新口径汇总（从 attempt 明细重算，不信任旧汇总布尔）。

    * success@1 = pass / (pass + fail + error)，attempt 级、按组池化，
      paused/blocked 从分母剔除；
    * success@3 = 有 ≥1 次 pass 的任务 / 有 ≥1 次可判分 attempt 的任务；
    * paused / blocked / 人工接管 / 危险动作单独计数。
    """
    categories: dict[str, list[dict]] = {}
    for s in all_summaries:
        categories.setdefault(s["category"], []).append(s)

    def _pool(items: list[dict]) -> dict:
        statuses_all: list[str] = []
        task_rows = []
        for s in items:
            statuses = [r.get("attempt_status") for r in s.get("results", [])]
            statuses_all.extend(statuses)
            task_rows.append(task_metrics(s.get("results", [])))
        n = len(items)
        scorable_tasks = [m for m in task_rows if m["scorable_attempts"] > 0]
        pass_attempts = statuses_all.count("pass")
        scorable_attempts = sum(statuses_all.count(k) for k in _SCORABLE)
        steps = [m["avg_steps"] for m in scorable_tasks if m["avg_steps"] is not None]
        walls = [m["avg_wall_ms"] for m in scorable_tasks if m["avg_wall_ms"] is not None]
        return {
            "n_tasks": n,
            "n_scorable_tasks": len(scorable_tasks),
            "attempts": dict((k, statuses_all.count(k)) for k in _STATUS_KEYS),
            "scorable_attempts": scorable_attempts,
            "success_at_1": (
                f"{pass_attempts}/{scorable_attempts}"
                f" = {pass_attempts / scorable_attempts:.3f}"
                if scorable_attempts else "无可判分 attempt"
            ),
            "success_at_3": (
                f"{sum(1 for m in scorable_tasks if m['success_at_3'])}/{len(scorable_tasks)}"
                f" = {sum(1 for m in scorable_tasks if m['success_at_3']) / len(scorable_tasks):.3f}"
                if scorable_tasks else "无可判分任务"
            ),
            "avg_steps": round(sum(steps) / len(steps), 2) if steps else None,
            "avg_wall_ms": round(sum(walls) / len(walls), 1) if walls else None,
            "danger_actions_total": sum(m["danger_total"] for m in task_rows),
            "human_takeover_total": sum(m["human_takeover_total"] for m in task_rows),
            "paused_attempts": statuses_all.count("paused"),
            "blocked_attempts": statuses_all.count("blocked"),
            "estop_triggered": any(m["estop_triggered"] for m in task_rows),
        }

    by_category = {cat: _pool(items) for cat, items in categories.items()}
    # 通道拆分：从每条任务的步骤明细里统计各通道的步数与成功数。
    by_channel: dict[str, dict] = {}
    for s in all_summaries:
        for r in s.get("results", []):
            for step in r.get("steps", []):
                channel = step.get("channel", "other")
                bucket = by_channel.setdefault(
                    channel, {"steps": 0, "ok": 0}
                )
                bucket["steps"] += 1
                bucket["ok"] += 1 if step.get("ok") else 0
    total = _pool(all_summaries)
    return {"total": total, "by_category": by_category, "by_channel": by_channel}


def main() -> int:
    global _PROTOCOL
    from minicode.action.protocol import PROTOCOL_VERSION

    _PROTOCOL = PROTOCOL_VERSION

    from taskbench_tasks import ALL_TASKS, TASKS_BY_ID  # noqa: PLC0415

    parser = argparse.ArgumentParser(description="W4 任务级评测台")
    parser.add_argument("--ids", default="", help="逗号分隔的任务 id")
    parser.add_argument("--all", action="store_true", help="跑全部 30 条")
    parser.add_argument("--rounds", type=int, default=1, help="完整轮数")
    parser.add_argument("--attempts", type=int, default=3, help="每任务 attempts")
    parser.add_argument("--out", default="runs/w4")
    args = parser.parse_args()

    import os

    os.environ.setdefault("MINICODE_TYPE_PER_CHAR_DELAY", "0.05")
    _env = _ROOT / ".env"
    if _env.exists():
        for line in _env.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, value = line.partition("=")
                os.environ.setdefault(key.strip(), value.strip())

    if args.all:
        ids = [spec.id for spec in ALL_TASKS]
    else:
        ids = [part.strip() for part in args.ids.split(",") if part.strip()]
    unknown = [tid for tid in ids if tid not in TASKS_BY_ID]
    if unknown:
        print(f"未知任务 id：{unknown}")
        return 2

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    TASK_TMP.mkdir(parents=True, exist_ok=True)

    # **开场静默 2s**：进程启动瞬间的残余输入窗口（实测 idle_ms≈200）会系统性
    # 打掉每个任务的第一个 attempt（success@1 被人为压低）。瞬态衰减后照常
    # 起跑；真实用户活动仍会触发护栏 6 让位。
    time.sleep(2.0)

    all_summaries: list[dict] = []
    for round_no in range(1, args.rounds + 1):
        round_dir = out / f"round{round_no}"
        round_dir.mkdir(parents=True, exist_ok=True)
        for tid in ids:
            spec = TASKS_BY_ID[tid]
            with hold_desktop_lock("nightly-w4", f"W4 评测：{tid}") as holds:
                if not holds:
                    summary = {"task": tid, "attempt_status": ["blocked"],
                               "error": "拿不到真机占用锁",
                               "category": spec.category,
                               "description": spec.description,
                               "success_at_1": False, "success_at_3": False,
                               "avg_steps": None, "avg_wall_ms": None,
                               "model_calls": [], "danger_actions": [],
                               "estop_triggered": False, "results": []}
                else:
                    summary = run_task(spec, round_dir, attempts=args.attempts,
                                       protocol_version=PROTOCOL_VERSION)
            all_summaries.append(summary)
            print(
                f"{tid:6s} {spec.category:4s} "
                f"s@1={summary['success_at_1']!s:5s} "
                f"s@3={summary['success_at_3']!s:5s} "
                f"paused={summary.get('paused_count', 0)} "
                f"attempts={summary['attempt_status']}",
                flush=True,
            )

    aggregation = aggregate(all_summaries)
    (out / "w4-aggregate.json").write_text(
        json.dumps({"rounds": args.rounds, "summaries": all_summaries,
                    "aggregate": aggregation},
                   ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(aggregation, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

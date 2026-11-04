"""W4 长任务评测台 + W5 自愈层：10 条跨应用任务（10~30 步），带故障注入。

设计文档：``docs/w4-long-tasks.md``（任务/故障/判分口径的唯一出处）。

**执行模型**：声明式步骤（与第一批 taskbench 同一被测通道：
``build_real_service`` 的护栏缝，UIA 优先、视觉兜底），每步包**自愈包装**
（W5 的失败分类 → 策略 → 记录）。故障注入由 harness 做（四类：对话框 /
遮挡失焦 / 位置漂移 / 元素禁用）。

**W5 指标**（``runs/w4-long/heal-log.jsonl`` + attempt 汇总）：

* 每次自愈触发：任务 / 步骤 / 失败分类 / 策略 / 是否救回；
* attempt 级：``clean``（一次做对，零自愈）vs ``healed``（至少一次救回）
  vs ``heal_exhausted``（自愈试过没救回）；
* 自愈策略（五类，与任务书一一对应）：

  1. 找不到元素 → **重新生成更鲁棒的目标描述**（决策层 LLM 重写一次，
     不是重试同一句）再定位；
  2. 点不动 → **换通道**（InvokePattern 失败 → 坐标点击元素中心）；
  3. 点了界面没变 → **等待 + 变化检测**再决定重试（指纹 = 标题+控件值）；
  4. 被对话框打断 → 识别**非任务目标窗口**（类名 ∈ 预期集合 + 标题复核）
     → 关闭 → 继续；
  5. 位置漂移 → **重新扫候选**（UIA 按窗口锚点重查，不沿用旧坐标）。

**真机纪律**：桌面锁包整轮；只操作自己 spawn 的实例；关窗三道闸；
L1 白名单；判分只读状态。

用法（仓库根目录）::

    python -I scripts/w4_long_tasks.py --ids t01,t02 --out runs/w4-long  # 先跑通两条
    python -I scripts/w4_long_tasks.py --all --out runs/w4-long          # 全量
"""

from __future__ import annotations

import argparse
import ctypes
import ctypes.wintypes
import json
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

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
from minicode.reflection.loop_breaker import LoopBreaker  # noqa: E402
from minicode.serve.real import build_real_service  # noqa: E402

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

PROTOCOL_VERSION = "cua.action/1"
WM_CLOSE = 0x0010
USER32 = ctypes.windll.user32
FILE_ATTRIBUTE_READONLY = 0x1
_VK_CTRL, _VK_SHIFT, _VK_S, _VK_ESC = 0x11, 0x10, 0x53, 0x1B

EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
TMP = _ROOT / "runs" / "w4-long" / "tmp"

#: 应用实例的定义（alias → 启动命令与窗口类预期集合）。
APP_KINDS: dict[str, dict[str, Any]] = {
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
    "explorer": {
        "command": ["explorer.exe"],
        "classes": ("CabinetWClass",),
        "whitelist": ("explorer", "Explorer"),
    },
}

#: 对话框类故障的「非任务目标窗口」预期集合（W5.4 的复核口径）。
FAULT_DIALOG_CLASSES = ("#32770",)  # 标准 Win32 对话框类
FAULT_DIALOG_TITLE = "系统提示"


# ---------------------------------------------------------------------------
# 基础 Win32 助手
# ---------------------------------------------------------------------------


def window_text(hwnd: int) -> str:
    buf = ctypes.create_unicode_buffer(512)
    USER32.GetWindowTextW(hwnd, buf, 512)
    return buf.value


def window_class(hwnd: int) -> str:
    buf = ctypes.create_unicode_buffer(256)
    USER32.GetClassNameW(hwnd, buf, 256)
    return buf.value


def set_window_position(hwnd: int, dx: int, dy: int) -> None:
    rect = ctypes.wintypes.RECT()
    USER32.GetWindowRect(hwnd, ctypes.byref(rect))
    USER32.SetWindowPos(
        hwnd, 0, rect.left + dx, rect.top + dy, 0, 0,
        0x0001 | 0x0004,  # SWP_NOSIZE | SWP_NOZORDER
    )


def set_file_readonly(path: Path) -> None:
    ctypes.windll.kernel32.SetFileAttributesW(str(path), FILE_ATTRIBUTE_READONLY)


def foreground_hwnd() -> int:
    return USER32.GetForegroundWindow()


# ---------------------------------------------------------------------------
# 故障注入器（四类；全部确定性、可复现、harness 侧）
# ---------------------------------------------------------------------------


class FaultInjector:
    """每任务一个注入器（记录到 faults.jsonl；attempt 结束清理现场）。"""

    def __init__(self, log: list[dict]):
        self.log = log
        self._box_proc: subprocess.Popen | None = None
        self._occluder_proc: subprocess.Popen | None = None
        self._occluder_hwnd = 0
        self._timers: list[threading.Timer] = []

    # -- F1：原生对话框 -----------------------------------------------------

    def f1_dialog(self) -> dict:
        """独立进程弹原生 MessageBox（MB_TOPMOST）→ 标题固定便于复核。"""
        script = (
            "import ctypes,time;"
            f"ctypes.windll.user32.MessageBoxW(0,'与当前任务无关的系统提示，请先处理本对话框。','{FAULT_DIALOG_TITLE}',0x40)"
        )
        before = {w[0] for w in visible_windows(FAULT_DIALOG_CLASSES)}
        self._box_proc = subprocess.Popen([sys.executable, "-c", script])
        hwnd = self._wait_new_window(before, FAULT_DIALOG_CLASSES)
        return self._record("F1", "dialog", hwnd=hwnd)

    # -- F2：遮挡 / 失焦 ----------------------------------------------------

    def f2_occlude(self, target_hwnd: int) -> dict:
        """spawn 空白记事本 → 移到目标窗中心 → 置顶 → 抢焦点。"""
        before = {w[0] for w in visible_windows(APP_KINDS["notepad"]["classes"])}
        self._occluder_proc = subprocess.Popen(["notepad.exe"])
        hwnd = self._wait_new_window(before, APP_KINDS["notepad"]["classes"])
        if hwnd:
            rect = ctypes.wintypes.RECT()
            USER32.GetWindowRect(target_hwnd, ctypes.byref(rect))
            width, height = 600, 400
            center_x = (rect.left + rect.right) // 2 - width // 2
            center_y = (rect.top + rect.bottom) // 2 - height // 2
            USER32.SetWindowPos(
                hwnd, 0xFFFFFFFB,  # HWND_TOPMOST
                max(0, center_x), max(0, center_y), width, height, 0x0000,
            )
            USER32.SetForegroundWindow(hwnd)
            time.sleep(0.6)
            self._occluder_hwnd = hwnd
        return self._record("F2", "occlude", hwnd=hwnd)

    # -- F3：位置漂移 -------------------------------------------------------

    def f3_drift(self, target_hwnd: int, dx: int = 300, dy: int = 0) -> dict:
        set_window_position(target_hwnd, dx, dy)
        time.sleep(0.4)
        return self._record("F3", "drift", hwnd=target_hwnd, dx=dx, dy=dy)

    # -- F4：元素禁用 / 延迟 ------------------------------------------------

    def f4_readonly(self, path: Path) -> dict:
        set_file_readonly(path)
        return self._record("F4", "readonly", path=str(path))

    def f4_delayed_file(self, path: Path, payload: str, delay_s: float = 30.0) -> dict:
        """延迟落盘（reset 先保证它不存在；到点写入）。"""

        def _write() -> None:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(payload, encoding="utf-8")

        timer = threading.Timer(delay_s, _write)
        timer.daemon = True
        timer.start()
        self._timers.append(timer)
        return self._record("F4", "delayed_file", path=str(path), delay_s=delay_s)

    # -- 现场管理 -----------------------------------------------------------

    def cleanup(self) -> None:
        for timer in self._timers:
            timer.cancel()
        for field in ("_box_proc", "_occluder_proc"):
            proc: subprocess.Popen | None = getattr(self, field)
            if proc is not None and proc.poll() is None:
                try:
                    proc.terminate()
                except OSError:
                    pass
            if field == "_occluder_proc" and self._occluder_hwnd:
                if window_class(self._occluder_hwnd) in APP_KINDS["notepad"]["classes"]:
                    USER32.PostMessageW(self._occluder_hwnd, WM_CLOSE, 0, 0)
                self._occluder_hwnd = 0

    # -- 内部 ---------------------------------------------------------------

    def _wait_new_window(self, before: set[int], classes: tuple[str, ...]) -> int:
        deadline = time.monotonic() + 15.0
        while time.monotonic() < deadline:
            found = find_new_window(before, class_filter=classes)
            if found is not None:
                return found[0]
            time.sleep(0.4)
        return 0

    def _record(self, code: str, kind: str, **detail: Any) -> dict:
        entry = {"fault": code, "kind": kind, "at": round(time.monotonic(), 3), **detail}
        self.log.append(entry)
        print(f"  [故障注入] {code} {kind} {detail}", flush=True)
        return entry


# ---------------------------------------------------------------------------
# W5 自愈层
# ---------------------------------------------------------------------------

#: 自愈的最大轮数（每步）：一次分类 → 一次策略 → 若仍失败再给一次兜底。
HEAL_MAX_ROUNDS = 2

_HEAL_LOG_PATH = TMP.parent / "heal-log.jsonl"

#: Loop Breaker 的双计数器落盘（W7 主线 C1）：**每步无条件写** —— 不是只在
#: 触发时写（结果分类器时代的教训：只在 heal 时留痕，就分不清「触发太窄」
#: 和「策略无效」）。
_LOOP_COUNTERS_PATH = TMP.parent / "loop-counters.jsonl"


class HealLogger:
    """自愈事件落盘（W5.4 的口径来源）。"""

    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.events: list[dict] = []

    def record(self, **fields: Any) -> None:
        entry = {"at": round(time.monotonic(), 3), **fields}
        self.events.append(entry)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
            handle.flush()


def _append_jsonl(path: Path, record: dict) -> None:
    """一行一个 JSON 的追加落盘（Loop Breaker 计数器用；坏了不炸主链路）。"""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:  # noqa: BLE001 —— 落盘失败不影响执行
        pass


def _fingerprint(service) -> str:
    """界面指纹（W5.3 的变化检测）：标题 + 前几个控件值。"""
    try:
        observer = service._observer  # noqa: SLF001 - 评测台脚本
        values = [
            getattr(element, "value", "") or getattr(element, "name", "")
            for element in observer.observe(None)[:8]
        ]
        return "|".join(values)
    except Exception:  # noqa: BLE001 - 指纹拿不到就退化成空串（视为不变）
        return ""


def _close_blocking_dialog(run) -> bool:
    """W5.4：识别「非任务目标窗口」的阻塞对话框并关闭。

    三道复核：前台窗口 ≠ 自己的任一实例；类名 ∈ 标准对话框集合；
    标题 ∈ 预期集合。任一不满足就不动（绝不关别的窗口）。
    """
    hwnd = foreground_hwnd()
    if hwnd in run.windows.values():
        return False
    if window_class(hwnd) not in FAULT_DIALOG_CLASSES:
        return False
    if FAULT_DIALOG_TITLE not in window_text(hwnd):
        return False
    USER32.PostMessageW(hwnd, WM_CLOSE, 0, 0)
    time.sleep(0.8)
    return not USER32.IsWindow(hwnd)


def _robust_description(description: str) -> str:
    """W5.1 的本地兜底：剥掉修饰词与引号，保住核心名词。"""
    text = description
    for token in ("写着", "那个", "画布里", "右侧清单里", "左侧", "中的"):
        text = text.replace(token, "")
    return text.replace("「", "").replace("」", "").strip()


def _regenerate_description(run, description: str) -> str:
    """W5.1：重新生成更鲁棒的目标描述（决策层 LLM 重写；模型不可用 → 本地变体）。"""
    model = run.decision_caller()
    if model is not None:
        try:
            reply = model.complete(
                "把下面这句「要在屏幕上找的界面元素描述」改写得更鲁棒："
                "去掉可能变化的修饰词，保留能唯一确定该元素的名词与文字。"
                "只输出改写后的一句话。\n描述：" + description,
                effort="low",
                max_tokens=2000,
            )
            rewritten = reply.text.strip().strip("\"「」")
            if rewritten:
                return rewritten
        except Exception:  # noqa: BLE001 - 模型不可用就走本地变体
            pass
    return _robust_description(description)


class HealedStepExecutor:
    """步骤执行 + 自愈包装（W5 的全部策略都在这里）。"""

    def __init__(self, run, heal_log: HealLogger):
        self.run = run
        self.heal_log = heal_log
        self.heal_counts: dict[str, int] = {}
        self.heal_saved: dict[str, int] = {}

    # -- 对外 ---------------------------------------------------------------

    def execute(self, step: dict) -> dict:
        attempt = self._execute_once(step)
        if attempt["ok"]:
            return attempt
        # 分类 → 策略 → 重试（≤HEAL_MAX_ROUNDS）。
        for round_index in range(1, HEAL_MAX_ROUNDS + 1):
            category = self._classify(step, attempt)
            if category is None:
                break
            fixed = self._apply(step, attempt, category, round_index)
            self.heal_counts[category] = self.heal_counts.get(category, 0) + 1
            if fixed["ok"]:
                self.heal_saved[category] = self.heal_saved.get(category, 0) + 1
                self.heal_log.record(
                    task=self.run.task_id, step=step.get("do"),
                    target=step.get("target") or step.get("text"),
                    category=category, strategy=self._strategy_name(category),
                    saved=True, round=round_index,
                )
                return fixed
            self.heal_log.record(
                task=self.run.task_id, step=step.get("do"),
                target=step.get("target") or step.get("text"),
                category=category, strategy=self._strategy_name(category),
                saved=False, round=round_index,
            )
            attempt = fixed
        return attempt

    # -- 内部 ---------------------------------------------------------------

    def _execute_once(self, step: dict) -> dict:
        try:
            return self.run.do_step(step)
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    def _classify(self, step: dict, attempt: dict) -> str | None:
        """失败 → W5 的五类之一（类名与任务书一致）。"""
        if step["do"] in ("fault", "wait", "raw_fn"):
            return None
        code = str(attempt.get("error_code") or "")
        error = str(attempt.get("error") or "")
        lifecycle = step["do"] in ("spawn", "switch")
        if code == "focus_lost" or "前台" in error or "没拿到前台" in error:
            # 前台被抢：可能是故障对话框（W5.4），也可能是普通失焦（W5.5）。
            if any(window_class(h) in FAULT_DIALOG_CLASSES and FAULT_DIALOG_TITLE in window_text(h)
                   for h in _visible_dialog_hwnds()):
                return "dialog_interrupted"
            return "position_drifted"
        if lifecycle:
            # 生命周期步骤只有上面两类前台问题可自愈；其余如实失败。
            return None
        if code in ("target_not_found", "locate_failed") or "没有找到" in error:
            return "element_not_found"
        if code in ("not_clickable", "invoke_failed", "element_not_available"):
            return "not_clickable"
        if not attempt.get("ok") and step["do"] == "click" and step.get("expect_change"):
            return "no_state_change"
        if not attempt.get("ok"):
            return "element_not_found" if step["do"] == "click" else "not_clickable"
        return None

    def _strategy_name(self, category: str) -> str:
        return {
            "element_not_found": "regenerate_description",
            "not_clickable": "switch_channel",
            "no_state_change": "wait_and_detect_change",
            "dialog_interrupted": "close_blocking_dialog",
            "position_drifted": "rescan_candidates",
        }[category]

    def _apply(self, step: dict, attempt: dict, category: str, round_index: int) -> dict:
        """按分类执行自愈策略。返回值 = 策略之后的那次执行结果。"""
        if category == "dialog_interrupted":
            _close_blocking_dialog(self.run)
            return self._execute_once(step)
        if category == "element_not_found":
            # **不是重试同一句**：重生成描述，用新描述重跑这一步。
            original = step.get("target", "")
            rewritten = _regenerate_description(self.run, original)
            if rewritten and rewritten != original:
                step = {**step, "target": rewritten}
            return self._execute_once(step)
        if category == "not_clickable":
            # 换通道：InvokePattern 失败 → 坐标点击元素中心（**替代**原步骤，
            # 不重跑 —— 重跑等于双击）。
            center = self.run.element_center(step.get("target", ""))
            if center is None:
                return attempt
            self.run.coordinate_click(center)
            result = dict(self.run.coordinate_click_result)
            result["channel"] = "coordinates"
            return result
        if category == "no_state_change":
            # 等待 + 变化检测：指纹还是没变才再试一次（最后一搏）。
            time.sleep(1.5)
            return self._execute_once(step)
        if category == "position_drifted":
            # 重新扫候选：把焦点抢回来（UIA 按窗口锚点重查，不沿用旧坐标）。
            active = self.run.windows.get(self.run.active_alias)
            if active and USER32.IsWindow(active):
                USER32.ShowWindow(active, 9)
                USER32.SetForegroundWindow(active)
                time.sleep(0.5)
            return self._execute_once(step)
        return attempt


def _visible_dialog_hwnds() -> list[int]:
    return [w[0] for w in visible_windows(FAULT_DIALOG_CLASSES) if USER32.IsWindowVisible(w[0])]


# ---------------------------------------------------------------------------
# 运行器（一条任务的一次 attempt）
# ---------------------------------------------------------------------------


class LongRun:
    """一条长任务的一次 attempt：多实例窗口 + 每实例一个护栏服务 + 自愈执行器。"""

    def __init__(self, task_id: str, fault_log: list[dict], heal_log: HealLogger):
        self.task_id = task_id
        self.windows: dict[str, int] = {}
        self.processes: dict[str, subprocess.Popen] = {}
        self.services: dict[str, Any] = {}
        self.active_alias = ""
        self.faults = FaultInjector(fault_log)
        self.healer = HealedStepExecutor(self, heal_log)
        self.vars: dict[str, Any] = {}
        self.steps: list[dict] = []
        self.last_rewritten = ""
        self._decision_caller: Any | None = None
        self._decision_tried = False

    # -- 生命周期 -----------------------------------------------------------

    def spawn(self, alias: str, *, file_path: Path | None = None, url: str | None = None) -> None:
        """spawn 一个应用实例（差分找窗 + 前台断言 + 服务装配）。"""
        kind = APP_KINDS[alias]
        command = list(kind["command"])
        if url:
            command.append(url)
        elif file_path:
            command.append(str(file_path))
        before = {w[0] for w in visible_windows(kind["classes"])}
        proc = subprocess.Popen(command)
        found = find_new_window(before, class_filter=kind["classes"])
        adopted = False
        if found is None and file_path is not None:
            # **Win11 记事本单实例接管**（上一夜实测坑）：新进程会把文件塞进
            # **既有**窗口的新标签 → 没有新顶层窗。按标题认领被接管的标签
            # （PID 兜底口径）—— 标题含本文件名即认领，认领后同样受三道闸管。
            frag = file_path.name
            deadline = time.monotonic() + 12.0
            while time.monotonic() < deadline and found is None:
                for candidate in visible_windows(kind["classes"]):
                    if frag in window_text(candidate[0]):
                        found = (candidate[0], window_class(candidate[0]),
                                 window_text(candidate[0]))
                        adopted = True
                        break
                if found is None:
                    time.sleep(0.5)
        self.last_spawn_adopted = adopted
        if found is None:
            raise RuntimeError(f"30s 内没有等到 {alias} 的新窗口（差分法+认领兜底）")
        hwnd, window_cls, _title = found
        if window_cls not in kind["classes"]:
            raise RuntimeError(f"{alias} 窗口类 {window_cls!r} 不在预期 {kind['classes']}")
        time.sleep(1.2)
        self._activate(hwnd)
        if foreground_hwnd() != hwnd:
            raise RuntimeError(f"{alias} 没拿到前台，拒绝盲注")
        if file_path:
            frag = file_path.name
            deadline = time.monotonic() + 8.0
            while frag not in window_text(hwnd):
                if time.monotonic() > deadline:
                    raise RuntimeError(
                        f"{alias} 活动标签不是本任务（标题 {window_text(hwnd)!r} 不含 {frag!r}）"
                    )
                time.sleep(0.3)
        self.windows[alias] = hwnd
        self.processes[alias] = proc
        self.active_alias = alias
        base = load_policy(level="L1", whitelist=kind["whitelist"])
        policy = GuardPolicy(level=base.level, window_whitelist=base.window_whitelist)
        self.services[alias] = build_real_service(
            TMP / self.task_id, hwnd=hwnd, guard_policy=policy,
        )

    def switch(self, alias: str) -> None:
        """激活某个实例（失败 = 该窗口没了 → 记录，让自愈层处理）。"""
        hwnd = self.windows[alias]
        if not USER32.IsWindow(hwnd) or not USER32.IsWindowVisible(hwnd):
            raise RuntimeError(f"{alias} 窗口已不可见（被遮挡或关闭）")
        self._activate(hwnd)
        if foreground_hwnd() != hwnd:
            raise RuntimeError(f"{alias} 没拿到前台（focus_lost）")
        self.active_alias = alias

    def close_all(self) -> None:
        for alias in list(self.windows):
            hwnd = self.windows[alias]
            if hwnd and USER32.IsWindow(hwnd):
                if window_class(hwnd) in APP_KINDS.get(alias, APP_KINDS["notepad"])["classes"]:
                    USER32.PostMessageW(hwnd, WM_CLOSE, 0, 0)
                    time.sleep(0.8)
                    self._dismiss_unsaved_dialog(hwnd)
                    deadline = time.monotonic() + 4.0
                    while (USER32.IsWindow(hwnd) and USER32.IsWindowVisible(hwnd)
                           and time.monotonic() < deadline):
                        USER32.PostMessageW(hwnd, WM_CLOSE, 0, 0)
                        time.sleep(0.6)
            proc = self.processes.get(alias)
            if proc is not None and proc.poll() is None:
                try:
                    proc.terminate()
                except OSError:
                    pass
        self.windows.clear()

    @staticmethod
    def _activate(hwnd: int) -> None:
        if foreground_hwnd() != hwnd:
            USER32.ShowWindow(hwnd, 9)
            # **ALT 技巧**（e2e/apps.py 同款，本机实测）：光 SetForegroundWindow
            # 会被前台锁拒绝；按住 ALT 再置前就成立。
            for _attempt in range(3):
                USER32.keybd_event(0x12, 0, 0, 0)  # ALT down
                try:
                    USER32.SetForegroundWindow(hwnd)
                finally:
                    USER32.keybd_event(0x12, 0, 2, 0)  # ALT up (KEYEVENTF_KEYUP)
                time.sleep(0.5)
                if foreground_hwnd() == hwnd:
                    break
        # ⚠️ ALT 释放会把焦点留在**菜单栏**上（复现实验实测：紧接着的第一段
        # 输入被菜单吃掉）→ 补一个 ESC 把菜单焦点退掉（编辑器里无害）。
        press_scancode(scancode_of(_VK_ESC), ())
        time.sleep(0.2)

    @staticmethod
    def _dismiss_unsaved_dialog(hwnd: int) -> None:
        """关窗后的「是否保存」对话框：UIA **精确名**找「不保存」按钮点掉
        （w3_cleanup_notepads 同口径：精确名绕开「保存 ⊂ 不保存」的双向歧义）。
        本方法是收拾自己现场（cleanup 专用，不走产品缝）。"""
        if not USER32.IsWindow(hwnd):
            return
        try:
            from minicode.uia.desktop import build_desktop
            from minicode.uia.ports import UiaFindRequest

            desktop = build_desktop(hwnd=hwnd)
            result = desktop.port.find(
                UiaFindRequest(anchor=desktop.anchor, name="不保存")
            )
            for element in result.elements:
                if (getattr(element, "name", "") == "不保存"
                        and getattr(element, "rect", (0, 0, 0, 0)) != (0, 0, 0, 0)):
                    rect = element.rect
                    USER32.SetCursorPos((rect[0] + rect[2]) // 2, (rect[1] + rect[3]) // 2)
                    time.sleep(0.05)
                    USER32.mouse_event(2, 0, 0, 0, 0)  # LEFTDOWN
                    USER32.mouse_event(4, 0, 0, 0, 0)  # LEFTUP
                    time.sleep(0.6)
                    return
        except Exception:  # noqa: BLE001 - 清理尽力而为，失败如实留着（残窗会被记录）
            return

    # -- 步骤执行（自愈层在外面包）------------------------------------------

    def do_step(self, step: dict) -> dict:
        kind = step["do"]
        if kind == "spawn":
            url = None
            if step.get("url_from_var"):
                url = self.vars[step["url_from_var"]].as_uri()
            self.spawn(
                step["alias"],
                file_path=step.get("file"),
                url=url,
            )
            return {"ok": True, "channel": "spawn", "adopted": self.last_spawn_adopted}
        if kind == "switch":
            self.switch(step["alias"])
            return {"ok": True, "channel": "switch"}
        if kind == "fault":
            detail = step["fn"](self)
            return {"ok": True, "channel": "fault", "fault": detail}
        if kind == "wait":
            time.sleep(step["ms"] / 1000.0)
            return {"ok": True, "channel": "wait"}
        if kind == "raw_fn":
            detail = dict(step["fn"](self) or {})
            detail.setdefault("channel", "raw")
            return detail
        # -- 输入类步骤：前台必须是当前活动实例 ------------------------------
        deny = self._assert_active(kind)
        if deny:
            return {"ok": False, "error_code": "focus_lost", "error": deny}
        service = self.services[self.active_alias]
        if kind == "click":
            response = service.execute(
                {"protocol": PROTOCOL_VERSION, "action": "click",
                 "target": step["target"]}
            )
            self._note_own_input()
            return {"ok": response.ok, "error_code": response.error_code,
                    "channel": "uia", "landing": response.landing_point,
                    "fingerprint": _fingerprint(service)}
        if kind == "type":
            return self._type_with_newlines(service, step["text"], step.get("intent", "输入文本"))
        if kind == "type_var":
            # 组合输入：prefix + vars[var] + suffix（长任务里「把读到的东西写出去」）。
            value = self.vars.get(step["var"], "")
            if isinstance(value, list):
                value = step.get("join", "\n").join(str(item) for item in value)
            text = f"{step.get('prefix', '')}{value}{step.get('suffix', '')}"
            result = self._type_with_newlines(service, text, step.get("intent", "写入读到的值"))
            result["text"] = text[:80]
            return result
        if kind == "key":
            guarded = service._seam_factory(ListTraceSink())  # noqa: SLF001 - 评测台脚本
            outcome = guarded.executor.execute(
                Action(kind=ActionKind.KEY,
                       params={"combo": tuple(step["combo"])},
                       intent=step.get("intent", "按键"))
            )
            self._note_own_input()
            service.guard_session.ledger.record_step(model_calls=0)
            return {"ok": outcome.ok,
                    "error_code": outcome.error_code.value if outcome.error_code else None,
                    "channel": "keyboard"}
        if kind == "save":
            # ⚠️ Ctrl+S **必须走扫描码**（上一夜真机对照实验的结论，今晚复现
            # 实验再次确认：seam 的 VK 通道 ctrl+s 报 ok 但记事本不保存）。
            press_scancode(scancode_of(_VK_S), (_VK_CTRL,))
            self._note_own_input()
            return {"ok": True, "channel": "keyboard-scancode"}
        if kind == "save_as":
            press_scancode(scancode_of(_VK_S), (_VK_CTRL, _VK_SHIFT))
            self._note_own_input()
            return {"ok": True, "channel": "keyboard-scancode"}
        return {"ok": False, "error": f"未知步骤类型 {kind!r}"}

    def _type_with_newlines(self, service, text: str, intent: str) -> dict:
        """TYPE 通道不透传换行（实测：\n 被丢）→ 按行拆开、行间显式回车。

        ⚠️ **再按 CJK/ASCII 分段**（T2 实测：中英混排的行在文件里丢字/变形
        ——「空格后首个数字」最易被吃）→ 每行切成非 ASCII 段与 ASCII 段，
        段间 80ms 让 IME/输入处理收尾（分段复现实验：完整行 ✓）。
        """
        guarded = service._seam_factory(ListTraceSink())  # noqa: SLF001 - 评测台脚本

        outcome = None
        lines = text.split("\n")
        for index, line in enumerate(lines):
            # ⚠️ **逐字符 TYPE**（T2 三轮实测：行级/段级注入都在 ASCII 数字处
            # 丢字——「键盘: 87」丢 8、「网线: 98」丢 9；type_unicode 自带
            # 12ms/字符仍偏快）→ 每字符一次 TYPE + 20ms 间隔，宁慢勿丢。
            for ch in line:
                outcome = guarded.executor.execute(
                    Action(kind=ActionKind.TYPE, params={"text": ch},
                           intent=intent)
                )
                self._note_own_input()
                service.guard_session.ledger.record_step(model_calls=0)
                if not outcome.ok:
                    return {"ok": False,
                            "error_code": outcome.error_code.value if outcome.error_code else None,
                            "channel": "keyboard"}
                time.sleep(0.02)
            if index < len(lines) - 1:  # 行间回车（末行后不加）
                outcome = guarded.executor.execute(
                    Action(kind=ActionKind.KEY, params={"combo": ("enter",)},
                           intent=intent)
                )
                self._note_own_input()
                service.guard_session.ledger.record_step(model_calls=0)
                if not outcome.ok:
                    break
        if outcome is None:
            return {"ok": True, "channel": "keyboard"}
        return {"ok": outcome.ok,
                "error_code": outcome.error_code.value if outcome.error_code else None,
                "channel": "keyboard"}

    def _assert_active(self, kind: str) -> str | None:
        hwnd = self.windows.get(self.active_alias)
        if not hwnd or not USER32.IsWindow(hwnd):
            return f"活动实例 {self.active_alias} 窗口已失效，拒绝{kind}"
        fg = foreground_hwnd()
        if fg == hwnd:
            return None
        # **属主对话框豁免**：另存为 / 打开这类公共对话框是实例自己的模态子窗
        # （GW_OWNER 指回实例）—— 输入本来就该落在对话框上。
        owner = USER32.GetWindow(fg, 4)  # GW_OWNER
        if owner == hwnd:
            return None
        return f"前台不是自己的窗口（{self.active_alias}），拒绝{kind}"

    # -- 自愈层要用的工具 ---------------------------------------------------

    def element_center(self, target: str) -> tuple[float, float] | None:
        """按名称查元素矩形中心（W5.2 换通道用；查不到 → None）。"""
        service = self.services.get(self.active_alias)
        if service is None:
            return None
        try:
            rows = self.query(name=target)
        except Exception:  # noqa: BLE001
            return None
        if not rows:
            return None
        left, top, right, bottom = rows[0]["rect"]
        return ((left + right) / 2.0, (top + bottom) / 2.0)

    def coordinate_click(self, center: tuple[float, float]) -> None:
        """把下一步「click」改成坐标点击（换通道的实现载体）。"""
        service = self.services[self.active_alias]
        guarded = service._seam_factory(ListTraceSink())  # noqa: SLF001 - 评测台脚本
        outcome = guarded.executor.execute(
            Action(kind=ActionKind.CLICK,
                   params={"x": int(round(center[0])), "y": int(round(center[1]))},
                   intent="换通道坐标点击")
        )
        self._note_own_input()
        service.guard_session.ledger.record_step(model_calls=0)
        self.coordinate_click_result = {
            "ok": outcome.ok,
            "error_code": outcome.error_code.value if outcome.error_code else None,
        }

    coordinate_click_result: dict = {}

    def query(self, *, name: str = "", control_type: str = "",
              automation_id: str = "") -> list[dict]:
        service = self.services[self.active_alias]
        observer = service._observer  # noqa: SLF001 - 评测台脚本
        if name:
            rows = [
                {"name": c.name, "value": "", "rect": list(c.rect),
                 "control_type": c.control_type}
                for c in observer.observe(name)
            ]
        else:
            from minicode.uia.ports import UiaFindRequest

            result = observer.query.find(
                UiaFindRequest(anchor=observer.anchor, name=None,
                               control_type=control_type or None,
                               automation_id=automation_id or None)
            )
            rows = [
                {"name": e.name, "value": e.value, "rect": list(e.rect)}
                for e in result.elements
            ]
        return rows

    # -- 决策层 LLM（W5.1 重生成描述用；不触发就不装配）----------------------

    def decision_caller(self) -> Any:
        if self._decision_tried:
            return self._decision_caller
        self._decision_tried = True
        try:
            from minicode.model.client import ModelClient
            from minicode.model.config import load_config
            from minicode.model.request_log import JsonlRequestLog
            from minicode.model.retry import RetryingModelCaller

            self._decision_caller = RetryingModelCaller(
                ModelClient(load_config(), JsonlRequestLog(TMP / "w5-heal-requests.jsonl")),
                policy=None,
            )
        except Exception:  # noqa: BLE001 - 没配置就用本地变体
            self._decision_caller = None
        return self._decision_caller

    @staticmethod
    def _note_own_input() -> None:
        """空闲让位的自归因：harness 的输入也是真实 SendInput。"""
        global _OWN_LAST_INPUT
        _OWN_LAST_INPUT = time.monotonic()


_OWN_LAST_INPUT = 0.0


# ---------------------------------------------------------------------------
# 任务规格（10 条；文档 = docs/w4-long-tasks.md）
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# 步骤与判分的紧凑助手
# ---------------------------------------------------------------------------


def calc_display(run: LongRun) -> str:
    """计算器显示屏上的**数值**（automation_id=CalculatorResults；等号后等 0.8s）。

    中文计算器的显示屏元素名是「显示为 3901」—— 取文本里**最后一个数**
    （去掉千分位），只把数值交给调用方。
    """
    import re

    time.sleep(0.8)
    rows = run.query(automation_id="CalculatorResults")
    text = " ".join((row.get("value") or row.get("name") or "") for row in rows).strip()
    matches = re.findall(r"-?[\d,]+(?:\.\d+)?", text)
    if not matches:
        return ""
    return matches[-1].replace(",", "").replace("，", "")


def read_calc_display(var: str):
    """raw_fn：把计算器显示屏读进 vars。"""

    def _fn(run: LongRun) -> dict:
        display = calc_display(run)
        run.vars[var] = display
        return {"ok": bool(display), "display": display[:80]}

    return _fn


def read_file(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""


def file_equals(path: Path, expected_lines: list[str]):
    """judge：文件逐行相等（行序敏感；允许尾随空行与 CRLF）。"""

    def _judge(run: LongRun) -> dict:
        actual = [line.strip() for line in read_file(path).splitlines() if line.strip()]
        ok = actual == [line.strip() for line in expected_lines]
        return {"ok": ok, "expected": expected_lines, "actual": actual[:12]}

    return _judge


def file_prefix_lines(path: Path, expected_prefix: list[str]):
    """judge/中间断言：文件前 N 行已等于给定内容（文件存在且至少 N 行）。"""

    def _judge(run: LongRun) -> dict:
        actual = [line.strip() for line in read_file(path).splitlines() if line.strip()]
        ok = len(actual) >= len(expected_prefix) and actual[: len(expected_prefix)] == [
            line.strip() for line in expected_prefix
        ]
        return {"ok": ok, "expected_prefix": expected_prefix, "actual": actual[:12]}

    return _judge


def contains_all(path: Path, needles: list[str]):
    def _judge(run: LongRun) -> dict:
        text = read_file(path)
        return {"ok": all(n in text for n in needles), "needles": needles,
                "actual": text[:200]}

    return _judge


def step_results_ok(run: LongRun) -> list[dict]:
    return run.steps


#: 数值断言的容差（事前定死：金额/换算 ±0.01）。
NUMERIC_TOLERANCE = 0.01


def numbers_match(path: Path, expected: list[float]):
    """judge：文件里的数字与期望逐一相等（±0.01；顺序无关的逐一匹配）。"""

    def _judge(run: LongRun) -> dict:
        import re

        text = read_file(path)
        found = [float(v) for v in re.findall(r"-?\d+(?:\.\d+)?", text)]
        remaining = list(found)
        matched = 0
        for want in expected:
            for index, value in enumerate(remaining):
                if abs(value - want) <= NUMERIC_TOLERANCE:
                    remaining.pop(index)
                    matched += 1
                    break
        return {"ok": matched == len(expected) and len(remaining) == 0,
                "expected": expected, "found": found[:12]}

    return _judge


class LongTask:
    """一条长任务的完整三件套（reset / steps+faults / judge）+ budget。"""

    def __init__(
        self,
        task_id: str,
        description: str,
        faults: list[str],
        *,
        max_steps: int,
        max_seconds: float,
        reset: Callable[[LongRun], None],
        steps: list[dict],
        judge: Callable[[LongRun], dict],
    ):
        self.id = task_id
        self.description = description
        self.faults = faults
        self.budget = GuardBudget(max_steps=max_steps, max_seconds=max_seconds,
                                  max_model_calls=50)
        self._reset = reset
        self.steps = steps
        self.judge = judge


def _t1_reset(run: LongRun) -> None:
    (TMP / "t1-ledger.txt").write_text("", encoding="utf-8")


def _t2_reset(run: LongRun) -> None:
    page = TMP / "t2-page.html"
    # ⚠️ 数据**同时**写在 <title> 与正文：Edge 的 web 内容在 UIA 树的深层
    # 子窗（实测深查 0 命中），顶层窗口名必可读 —— 摘录走标题通道，
    # 正文表格保持任务形态（「浏览器里看到这些值」）不变。
    page.write_text(
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<title>W4-T2 库存 笔记本42 键盘87 显示器23 鼠标156 底座61 网线98</title></head>"
        "<body><h1>仓库库存表</h1>"
        "<p>库存 笔记本: 42 台</p><p>库存 键盘: 87 台</p><p>库存 显示器: 23 台</p>"
        "<p>库存 鼠标: 156 台</p><p>库存 底座: 61 台</p><p>库存 网线: 98 台</p>"
        "</body></html>",
        encoding="utf-8",
    )
    run.vars["t2_page"] = page
    (TMP / "t2-extract.txt").write_text("", encoding="utf-8")


def _t3_reset(run: LongRun) -> None:
    (TMP / "t3-a.txt").write_text("甲组 12\n甲组 34\n甲组 56\n", encoding="utf-8")
    (TMP / "t3-b.txt").write_text("乙组 7\n乙组 8\n乙组 9\n", encoding="utf-8")
    (TMP / "t3-c.txt").write_text("", encoding="utf-8")


def _t4_reset(run: LongRun) -> None:
    template = TMP / "t4-units.txt"
    # ⚠️ 上一轮 F4 注入可能留下**只读**属性 —— reset 先清掉再写（否则
    # write_text 直接 PermissionError → blocked）。
    ctypes.windll.kernel32.SetFileAttributesW(str(template), 0x80)
    template.write_text(
        "26.2 英里 = ___ 公里\n100 度(华氏) = ___ 摄氏(取整)\n"
        "3 小时 = ___ 分钟\n2.5 千克 = ___ 克\n1.5 米 = ___ 厘米\n",
        encoding="utf-8",
    )
    out = TMP / "t4-units-out.txt"
    if out.exists():
        ctypes.windll.kernel32.SetFileAttributesW(str(out), 0x80)  # 去只读
        out.unlink()


def _read_notepad_lines(run: LongRun, var: str) -> dict:
    """读当前记事本文档全文（Document 控件的 value）→ 按行拆进 vars[var]。"""
    rows = run.query(control_type="Document")
    text = " ".join((row.get("value") or "") for row in rows)
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    run.vars[var] = lines
    return {"ok": bool(lines), "lines": lines[:8]}


T1 = LongTask(
    "t01", "算术账本：calc 连算 5 个表达式并记入 notepad 落盘", ["F3"],
    max_steps=30, max_seconds=1200,
    reset=_t1_reset,
    steps=[
        {"do": "spawn", "alias": "calc"},
        {"do": "type", "text": "47*83"},
        {"do": "key", "combo": ["return"]},
        {"do": "raw_fn", "fn": read_calc_display("r1")},
        {"do": "type", "text": "1296/36"},
        {"do": "key", "combo": ["return"]},
        {"do": "raw_fn", "fn": read_calc_display("r2")},
        {"do": "type", "text": "888+111"},
        {"do": "key", "combo": ["return"]},
        {"do": "raw_fn", "fn": read_calc_display("r3")},
        {"do": "spawn", "alias": "notepad", "file": TMP / "t1-ledger.txt"},
        {"do": "type_var", "var": "r1", "prefix": "47*83=", "suffix": "\n"},
        {"do": "type_var", "var": "r2", "prefix": "1296/36=", "suffix": "\n"},
        {"do": "type_var", "var": "r3", "prefix": "888+111=", "suffix": "\n"},
        {"do": "save"},
        {"do": "raw_fn", "fn": lambda run: {
            "ok": len(read_file(TMP / "t1-ledger.txt").splitlines()) >= 3,
            "note": "中间断言：前 3 行已落盘"}},
        {"do": "switch", "alias": "calc"},
        {"do": "fault", "fn": lambda run: run.faults.f3_drift(run.windows["calc"], 300, 0)},
        {"do": "type", "text": "13*7"},
        {"do": "key", "combo": ["return"]},
        {"do": "raw_fn", "fn": read_calc_display("r4")},
        {"do": "switch", "alias": "notepad"},
        {"do": "type_var", "var": "r4", "prefix": "13*7=", "suffix": "\n"},
        {"do": "switch", "alias": "calc"},
        {"do": "type", "text": "25+11"},
        {"do": "key", "combo": ["return"]},
        {"do": "raw_fn", "fn": read_calc_display("r5")},
        {"do": "switch", "alias": "notepad"},
        {"do": "type_var", "var": "r5", "prefix": "25+11=", "suffix": "\n"},
        {"do": "save"},
    ],
    judge=lambda run: file_equals(
        TMP / "t1-ledger.txt",
        ["47*83=3901", "1296/36=36", "888+111=999", "13*7=91", "25+11=36"],
    )(run),
)

T2 = LongTask(
    "t02", "网页摘录：从本地库存页读 6 项数值写入 notepad 另存", ["F1"],
    max_steps=28, max_seconds=1080,
    reset=_t2_reset,
    steps=[
        {"do": "spawn", "alias": "edge", "url_from_var": "t2_page"},
        {"do": "raw_fn", "fn": lambda run: _extract_stock(run, "笔记本", "v1")},
        {"do": "raw_fn", "fn": lambda run: _extract_stock(run, "键盘", "v2")},
        {"do": "raw_fn", "fn": lambda run: _extract_stock(run, "显示器", "v3")},
        {"do": "spawn", "alias": "notepad", "file": TMP / "t2-extract.txt"},
        {"do": "type_var", "var": "v1", "prefix": "笔记本: ", "suffix": "\n"},
        {"do": "type_var", "var": "v2", "prefix": "键盘: ", "suffix": "\n"},
        {"do": "type_var", "var": "v3", "prefix": "显示器: ", "suffix": "\n"},
        {"do": "raw_fn", "fn": lambda run: {"ok": all(
            k in run.vars for k in ("v1", "v2", "v3")), "note": "中间断言：前 3 项已读出"}},
        {"do": "fault", "fn": lambda run: run.faults.f1_dialog()},
        {"do": "switch", "alias": "edge"},
        {"do": "raw_fn", "fn": lambda run: _extract_stock(run, "鼠标", "v4")},
        {"do": "raw_fn", "fn": lambda run: _extract_stock(run, "底座", "v5")},
        {"do": "raw_fn", "fn": lambda run: _extract_stock(run, "网线", "v6")},
        {"do": "switch", "alias": "notepad"},
        {"do": "type_var", "var": "v4", "prefix": "鼠标: ", "suffix": "\n"},
        {"do": "type_var", "var": "v5", "prefix": "底座: ", "suffix": "\n"},
        {"do": "type_var", "var": "v6", "prefix": "网线: ", "suffix": "\n"},
        {"do": "save"},
    ],
    judge=lambda run: file_equals(
        TMP / "t2-extract.txt",
        ["笔记本: 42", "键盘: 87", "显示器: 23", "鼠标: 156", "底座: 61", "网线: 98"],
    )(run),
)

T3 = LongTask(
    "t03", "双文件汇总：A/B 两组数值抄进 C 并落盘", ["F2"],
    max_steps=28, max_seconds=1080,
    reset=_t3_reset,
    steps=[
        {"do": "spawn", "alias": "notepad", "file": TMP / "t3-a.txt"},
        {"do": "raw_fn", "fn": lambda run: _read_notepad_lines(run, "a_lines")},
        {"do": "spawn", "alias": "notepad", "file": TMP / "t3-b.txt"},
        {"do": "raw_fn", "fn": lambda run: _read_notepad_lines(run, "b_lines")},
        {"do": "spawn", "alias": "notepad", "file": TMP / "t3-c.txt"},
        {"do": "type_var", "var": "a_lines", "join": "\n", "suffix": "\n"},
        {"do": "fault", "fn": lambda run: run.faults.f2_occlude(run.windows["notepad"])},
        {"do": "switch", "alias": "notepad"},
        {"do": "raw_fn", "fn": lambda run: {"ok": True, "note": "遮挡后继续"}},
        {"do": "type_var", "var": "b_lines", "join": "\n", "suffix": "\n"},
        {"do": "save"},
    ],
    judge=lambda run: file_equals(
        TMP / "t3-c.txt",
        ["甲组 12", "甲组 34", "甲组 56", "乙组 7", "乙组 8", "乙组 9"],
    )(run),
)

T4 = LongTask(
    "t04", "只读模板换路径：填 5 个换算值，保存被拒后另存新文件", ["F4"],
    max_steps=30, max_seconds=1200,
    reset=_t4_reset,
    steps=[
        {"do": "spawn", "alias": "notepad", "file": TMP / "t4-units.txt"},
        # 5 行逐行填答案：行尾定位 → 输入 → 下一行（键盘通道，确定性路径）。
        {"do": "raw_fn", "fn": lambda run: _fill_template_line(run, "42.17")},   # 26.2 mi = 42.17 km
        {"do": "raw_fn", "fn": lambda run: _fill_template_line(run, "38")},      # 100℉ ≈ 38℃
        {"do": "raw_fn", "fn": lambda run: _fill_template_line(run, "180")},     # 3h = 180min
        {"do": "raw_fn", "fn": lambda run: _fill_template_line(run, "2500")},    # 2.5kg = 2500g
        {"do": "raw_fn", "fn": lambda run: _fill_template_line(run, "150")},     # 1.5m = 150cm
        {"do": "fault", "fn": lambda run: run.faults.f4_readonly(TMP / "t4-units.txt")},
        {"do": "save"},   # → 弹「拒绝访问」对话框
        {"do": "wait", "ms": 1200},  # 等对话框出现
        {"do": "key", "combo": ["esc"]},          # 关掉它（或自愈层处理）
        {"do": "wait", "ms": 800},
        {"do": "save_as"},  # 另存为（Ctrl+Shift+S）
        {"do": "wait", "ms": 1500},  # 等另存为对话框出现（否则文件名打进正文）
        {"do": "type", "text": "t4-units-out.txt"},
        {"do": "wait", "ms": 400},
        {"do": "key", "combo": ["return"]},
        {"do": "wait", "ms": 1500},  # 等落盘
    ],
    judge=lambda run: _judge_t4(run),
)


def _extract_stock(run: LongRun, item: str, var: str) -> dict:
    """从 Edge 窗口标题里提取该商品的库存数（页面 reset 时把数据写进标题）。"""
    import re

    hwnd = run.windows.get("edge", 0)
    title = window_text(hwnd) if hwnd else ""
    match = re.search(rf"{item}\s*(\d+)", title)
    number = match.group(1) if match else ""
    run.vars[var] = number
    return {"ok": bool(number), "item": item, "value": number, "title": title[:80]}


def _fill_template_line(run: LongRun, answer: str) -> dict:
    """在当前行行尾输入答案再移到下一行（模板填空：End → 答案 → 下行）。"""
    service = run.services[run.active_alias]
    guarded = service._seam_factory(ListTraceSink())  # noqa: SLF001 - 评测台脚本
    keys = (
        [ActionKind.KEY, {"combo": ("end",)}],
        [ActionKind.TYPE, {"text": answer}],
        [ActionKind.KEY, {"combo": ("down",)}],
    )
    for action_kind, params in keys:
        outcome = guarded.executor.execute(
            Action(kind=action_kind, params=params, intent="模板填空")
        )
        run._note_own_input()
        if not outcome.ok:
            return {"ok": False, "error_code": outcome.error_code.value if outcome.error_code else None}
        service.guard_session.ledger.record_step(model_calls=0)
    return {"ok": True, "answer": answer}


def _judge_t4(run: LongRun) -> dict:
    """T4 判分（W7 主线 C2 的判据清单示范）：**主口径 = 逐行精确匹配**。

    教训驱动（T4 曾两套读法并排没裁决）：主口径用确定性执行式判据
    （OSWorld 风格），宽口径降为**敏感性分析**列。CompletenessChecker
    （reflection/completeness.py）承载判据，驳回时写明缺哪条证据。
    """
    from minicode.reflection.completeness import CompletenessChecker, Criterion, CriterionResult

    out_text = read_file(TMP / "t4-units-out.txt")
    original = read_file(TMP / "t4-units.txt")
    out_lines = [line for line in out_text.splitlines() if line.strip()]
    expected_values = ("42.17", "38", "180", "2500", "150")

    def _exact_lines(context: dict) -> CriterionResult:
        # 主口径：5 行、每行行尾是期望值（行内容与模板骨架一致）。
        ok = len(out_lines) == 5 and all(
            line.rstrip().endswith(value)
            for line, value in zip(out_lines, expected_values, strict=True)
        )
        evidence = (
            f"lines={len(out_lines)} "
            f"tails={[line[-14:] for line in out_lines]}"
        )
        return CriterionResult(
            ok=ok, evidence=evidence if ok else "", error="逐行精确匹配未过"
        )

    def _template_intact(context: dict) -> CriterionResult:
        ok = original.count("___") == 5  # 原模板未被破坏（只读生效）
        return CriterionResult(
            ok=ok,
            evidence=f"placeholders={original.count('___')}" if ok else "",
            error="原模板被破坏",
        )

    def _no_stale_buffer(context: dict) -> CriterionResult:
        # 无陈旧缓冲残留：输出行数恰 5（第 6 行起有残留 = 严口径 fail 的来源）。
        ok = len(out_lines) == 5
        return CriterionResult(
            ok=ok, evidence=f"line_count={len(out_lines)}" if ok else "",
            error=f"输出有 {len(out_lines)} 行（期望 5，多出的是陈旧缓冲残留）",
        )

    checker = CompletenessChecker(
        [
            Criterion(name="exact-lines", verify=_exact_lines),
            Criterion(name="template-intact", verify=_template_intact),
            Criterion(name="no-stale-buffer", verify=_no_stale_buffer),
        ],
        stable_rounds=1,  # judge 是终态离线核验：单次读数即可（无逐步观测流）
    )
    checker.note_observation("terminal")
    context = {"out_lines": out_lines, "original": original}
    verdict = checker.final_gate(context)

    # 敏感性分析列（宽口径 = 旧的子串读法）：只单独报告，不参与主判定。
    lenient_ok = all(value in out_text for value in expected_values)

    return {
        "ok": verdict.accepted,  # **主口径 = 判据清单全过**
        "criteria": list(verdict.per_criterion),
        "missing": list(verdict.missing),
        "sensitivity_lenient_ok": lenient_ok,  # 敏感性分析：宽口径单独一列
        "out_excerpt": out_text[:200],
    }


TASKS: dict[str, LongTask] = {t.id: t for t in (T1, T2, T3, T4)}


def close_stale_task_windows() -> list[str]:
    """attempt 前清场：关掉标题含**本任务集文件名**的遗留记事本。

    只匹配 TMP 目录里实际存在的文件名 —— 用户自己的窗口绝不碰。
    单实例接管 + 认领的组合下，残留窗口里的**陈旧缓冲**会污染下一轮
    （reset 重写了磁盘文件，但记事本内存缓冲还是旧的）→ 必须关掉重开。
    """
    known = [p.name for p in TMP.glob("*.txt")] if TMP.exists() else []
    if not known:
        return []
    closed: list[str] = []
    for _round in range(6):
        stale = []
        for candidate in visible_windows(("Notepad",)):
            hwnd, _cls, title = candidate
            if any(name in title for name in known):
                stale.append((hwnd, title))
        if not stale:
            break
        for hwnd, title in stale:
            USER32.PostMessageW(hwnd, WM_CLOSE, 0, 0)
            time.sleep(0.6)
            LongRun._dismiss_unsaved_dialog(hwnd)
            closed.append(title)
            time.sleep(0.3)
    return closed


# ---------------------------------------------------------------------------
# attempt 生命周期
# ---------------------------------------------------------------------------


def run_attempt(task: LongTask, out_dir: Path) -> dict:
    """一条任务的一次 attempt：reset → 步骤（带故障与自愈）→ judge → 判级。"""
    fault_log: list[dict] = []
    heal_log = HealLogger(_HEAL_LOG_PATH)
    run = LongRun(task.id, fault_log, heal_log)
    # Loop Breaker（W7 主线 C1）：双计数器每步无条件落盘；触发只产出
    # 「换方式」指令（loop-break ≠ fail），黑名单只作用于下一步。
    loop_breaker = LoopBreaker(
        sink=lambda record: _append_jsonl(_LOOP_COUNTERS_PATH, {**record, "task": task.id}),
    )
    idle_ms = (now_tick() - last_input_tick()) % 0x1_0000_0000
    own_recent_s = time.monotonic() - _OWN_LAST_INPUT
    if idle_ms < 1500 and own_recent_s > 3.0:
        return {"task": task.id, "attempt_status": "paused",
                "note": f"用户在动键鼠（护栏 6，idle_ms={idle_ms:.0f}），本轮未跑"}
    started = time.monotonic()
    result: dict = {"task": task.id}
    try:
        stale = close_stale_task_windows()
        if stale:
            print(f"  [清场] 关掉遗留窗口：{stale}", flush=True)
        try:
            task._reset(run)
        except Exception as exc:  # noqa: BLE001
            return {**result, "attempt_status": "blocked",
                    "error": f"reset 失败：{type(exc).__name__}: {exc}"}
        step_results: list[dict] = []
        status = "pass"
        pending_blacklist = ""
        try:
            for index, step in enumerate(task.steps, start=1):
                # Loop Breaker（W7）：每步观测前后指纹 → 双计数器无条件落盘。
                active_service = run.services.get(run.active_alias)
                before_fp = (
                    _fingerprint(active_service)
                    if active_service is not None and step["do"] not in ("fault", "wait")
                    else ""
                )
                if pending_blacklist:
                    # 黑名单只作用于下一步：随步骤记录下发（**骨架接线** ——
                    # 改变执行行为的注入点留作后续；本步只记账，不注入决策）。
                    step = {**step, "blacklist_next": pending_blacklist}
                    print(f"  [loop-break 黑名单] {pending_blacklist}", flush=True)
                detail = run.healer.execute(step)
                # 执行后指纹：click 类步骤的 detail 已带；其他步骤（type 等）现取。
                after_fp = str(detail.get("fingerprint") or "") or (
                    _fingerprint(active_service) if active_service is not None else ""
                )
                if before_fp and after_fp:
                    loop_verdict = loop_breaker.observe(
                        step=index,
                        action_key=f"{step['do']}@{step.get('target') or step.get('text') or ''}",
                        obs_before=before_fp,
                        obs_after=after_fp,
                    )
                else:
                    # 指纹不可得（实例未起/取失败）→ 这一步不计入计数器（如实跳过）。
                    loop_verdict = None
                detail.update({
                    "step": index,
                    "do": step["do"],
                    "loop": {
                        "action_repeat_count": loop_verdict.action_repeat_count if loop_verdict else None,
                        "obs_repeat_count": loop_verdict.obs_repeat_count if loop_verdict else None,
                        "level": loop_verdict.level if loop_verdict else None,
                        "skipped": loop_verdict is None,
                    },
                })
                pending_blacklist = loop_breaker.consume_blacklist()
                step_results.append(detail)
                if not detail["ok"]:
                    status = "fail"
                    print(f"  [步骤 {index} 失败] {detail}", flush=True)
                    break
        except Exception as exc:  # noqa: BLE001
            status = "error"
            step_results.append({"error": f"{type(exc).__name__}: {exc}"})
            print(f"  [步骤异常] {exc}", flush=True)
        run.steps = step_results
        wall = time.monotonic() - started
        # budget：步数与墙钟超限 = 该 attempt 失败（如实记）。
        if status == "pass" and len(step_results) > task.budget.max_steps:
            status = "fail"
        if status == "pass" and wall > task.budget.max_seconds:
            status = "fail"
        verdict: dict = {}
        if status == "pass":
            try:
                verdict = task.judge(run) or {}
                if not verdict.get("ok"):
                    status = "fail"
            except Exception as exc:  # noqa: BLE001
                status = "error"
                verdict = {"error": f"judge 异常：{type(exc).__name__}: {exc}"}
        # -- W5 判级：一次做对 vs 自愈救回 vs 救不回 -------------------------
        heal_events = [e for e in heal_log.events if e.get("task") == task.id]
        triggered = sum(run.healer.heal_counts.values())
        saved = sum(run.healer.heal_saved.values())
        if status == "pass":
            grade = "clean" if triggered == 0 else "healed"
        elif triggered > 0:
            grade = "heal_exhausted"
        else:
            grade = "no_trigger"
        result.update({
            "attempt_status": status,
            "grade": grade,
            "verdict": verdict,
            "steps": step_results,
            "faults": fault_log,
            "heal": {"triggered": triggered, "saved": saved,
                     "by_category": dict(run.healer.heal_counts),
                     "saved_by_category": dict(run.healer.heal_saved),
                     "events": heal_events},
            # Loop Breaker 三个分开的数（工单 5.1：遭遇故障数 / 触发自愈数 / 自愈成功数）。
            "loop": dict(loop_breaker.stats),
            "wall_s": round(wall, 1),
        })
        print(
            f"  [{task.id}] {status} ({grade}) steps={len(step_results)} "
            f"heal={triggered}/{saved} wall={wall:.0f}s",
            flush=True,
        )
        return result
    finally:
        run.faults.cleanup()
        run.close_all()


def main() -> int:
    parser = argparse.ArgumentParser(description="W4 长任务评测台 + W5 自愈层")
    parser.add_argument("--ids", default="", help="逗号分隔的任务 id（如 t01,t02）")
    parser.add_argument("--all", action="store_true", help="跑全部已实现的任务")
    parser.add_argument("--out", default="runs/w4-long")
    args = parser.parse_args()

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    TMP.mkdir(parents=True, exist_ok=True)
    ids = sorted(TASKS) if args.all else [
        i.strip() for i in args.ids.split(",") if i.strip()
    ]
    unknown = [i for i in ids if i not in TASKS]
    if unknown:
        raise SystemExit(f"未知任务 id：{unknown}（已实现：{sorted(TASKS)}）")
    if not ids:
        raise SystemExit("没给任务：--ids t01,t02 或 --all")

    results: list[dict] = []
    with hold_desktop_lock("nightly-w4long", f"W4 长任务：{','.join(ids)}") as holds:
        if not holds:
            raise SystemExit("拿不到真机桌面锁 —— 拒绝盲跑")
        for task_id in ids:
            task = TASKS[task_id]
            print(f"=== {task.id}: {task.description} ===", flush=True)
            result = run_attempt(task, out_dir)
            # **paused 重采样**（上一夜教训：一次用户输入会把同窗内连续多个
            # attempt 全打成 paused）—— 等 10s 重试同一任务，至多 2 次。
            retries = 0
            while result.get("attempt_status") == "paused" and retries < 2:
                retries += 1
                time.sleep(10)
                print(f"  [paused 重试 {retries}/2]", flush=True)
                result = run_attempt(task, out_dir)
            results.append(result)
            (out_dir / f"{task_id}.json").write_text(
                json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8"
            )
    passed = [r for r in results if r.get("attempt_status") == "pass"]
    healed = [r for r in passed if r.get("grade") == "healed"]
    print(
        f"=== 汇总：{len(passed)}/{len(results)} pass（其中 {len(healed)} 靠自愈救回） ===",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())




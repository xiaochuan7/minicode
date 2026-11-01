"""W4 任务级评测台（30 条本机可复现任务，每条 reset / judge / budget 三件套）。

设计（与 W3 真机纪律一致）：

* **reset** —— 幂等初始状态（临时目录造文件 / 无状态），每轮 attempt 前跑；
* **steps** —— 声明式步骤（click / type / key / wait / query / raw_fn），
  全部走带护栏的接线（L1 白名单 + 预算 + 急停 + 录制）；
* **judge** —— 只读**状态**（UIA 控件值 / 文件内容 / 窗口标题 / 磁盘），
  **不读截图**。

指标（全部带样本量）：success@1、success@3、平均步数、平均耗时、
危险动作触发次数（必须 0）、人工接管次数（L1 口径 = 0）；
按类别（文件/计算/浏览器/界面/复合/反例）与通道（UIA / 键盘 / 窗口标题）拆分。

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
from minicode.guard.lockext import hold_desktop_lock  # noqa: E402
from minicode.guard.policy import GuardBudget, GuardPolicy, load_policy  # noqa: E402
from minicode.guard.win32 import last_input_tick, now_tick  # noqa: E402
from minicode.serve.real import build_real_service  # noqa: E402
from minicode.uia.ports import UiaFindRequest  # noqa: E402

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

WM_CLOSE = 0x0010
USER32 = ctypes.windll.user32

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
            buf = ctypes.create_unicode_buffer(256)
            USER32.GetClassNameW(self.hwnd, buf, 256)
            if buf.value in _APPS[self.spec.app]["classes"] \
                    and USER32.IsWindowVisible(self.hwnd):
                USER32.PostMessageW(self.hwnd, WM_CLOSE, 0, 0)
                time.sleep(1.0)
                self._dismiss_save_dialog()
        if self.proc is not None and self.proc.poll() is None:
            try:
                self.proc.terminate()
            except OSError:
                pass

    def _dismiss_save_dialog(self) -> None:
        """「保存？」→「不保存」（精确名匹配，绕开保存⊂不保存的包含歧义）。"""
        for _ in range(3):
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
                time.sleep(0.6)
                if not USER32.IsWindow(self.hwnd):
                    return
            time.sleep(1.0)

    # -- 步骤 ----------------------------------------------------------------

    def query(self, *, name: str = "", control_type: str = "",
              automation_id: str = "") -> list[dict]:
        observer = self.service._observer  # noqa: SLF001 - 评测台脚本
        if name:
            return [
                {"name": c.name, "value": "", "rect": list(c.rect),
                 "control_type": c.control_type}
                for c in observer.observe(name)
            ]
        result = observer.query.find(
            UiaFindRequest(anchor=observer.anchor, name=None,
                           control_type=control_type or None,
                           automation_id=automation_id or None)
        )
        return [_element_row(e) for e in result.elements]

    def do_step(self, step: dict) -> dict:
        kind = step["do"]
        if kind == "click":
            response = self.service.execute(
                {"protocol": _PROTOCOL, "action": "click",
                 "target": step["target"]}
            )
            return {"ok": response.ok, "error_code": response.error_code,
                    "channel": "uia", "landing": response.landing_point}
        if kind == "type":
            guarded = self.service._seam_factory(ListTraceSink())  # noqa: SLF001
            outcome = guarded.executor.execute(
                Action(kind=ActionKind.TYPE, params={"text": step["text"]},
                       intent=step.get("intent", "输入文本"))
            )
            self.service.guard_session.ledger.record_step(model_calls=0)
            return {"ok": outcome.ok,
                    "error_code": (outcome.error_code.value
                                   if outcome.error_code else None),
                    "channel": "keyboard", "notes": list(outcome.notes)}
        if kind == "key":
            guarded = self.service._seam_factory(ListTraceSink())  # noqa: SLF001
            outcome = guarded.executor.execute(
                Action(kind=ActionKind.KEY,
                       params={"combo": tuple(step["combo"])},
                       intent=step.get("intent", "按键"))
            )
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
    """一条任务的一次 attempt：reset → 起实例 → 步骤 → judge → 清理。"""
    run = TaskRun(spec, out_dir, protocol_version)
    run.started = time.monotonic()
    idle_ms = (now_tick() - last_input_tick()) % 0x1_0000_0000
    if idle_ms < 1500:
        return {"task": spec.id, "attempt_status": "paused",
                "note": "用户正在动键鼠（护栏 6），本轮未跑"}
    result: dict = {}
    try:
        spec.reset(run)
        if spec.app:
            run.start_app(url=run.vars.get("url"))
            run.build_service(spec.budget)
        run.run_steps()
        verdict = spec.judge(run) or {}
        run.verdict = verdict
        result = run.summarize()
        result["attempt_status"] = "pass" if verdict.get("ok") else "fail"
        return result
    except Exception as exc:
        result = run.summarize()
        result["attempt_status"] = "blocked"
        result["error"] = f"{type(exc).__name__}: {exc}"
        return result
    finally:
        if spec.app:
            try:
                run.close_app()
            except Exception as exc:
                result["close_error"] = str(exc)


def run_task(spec, out_dir: Path, attempts: int = 3,
             protocol_version: str = "") -> dict:
    """一条任务的全部 attempts + success@1 / success@3 汇总。"""
    results = []
    for index in range(attempts):
        result = run_attempt(spec, out_dir, protocol_version)
        result["attempt"] = index + 1
        results.append(result)
        if result.get("attempt_status") == "paused":
            continue
        # reset 每轮之间留点余地（实例收尾进程退出）。
        time.sleep(1.0)
    completed = [r for r in results if r.get("attempt_status") in ("pass", "fail")]
    successes = [r for r in completed if r["attempt_status"] == "pass"]
    summary = {
        "task": spec.id,
        "category": spec.category,
        "description": spec.description,
        "attempts_recorded": len(results),
        "success_at_1": bool(results and results[0].get("attempt_status") == "pass"),
        "success_at_3": bool(successes),
        "attempt_status": [r.get("attempt_status") for r in results],
        "avg_steps": (
            round(sum(r.get("step_count", 0) for r in completed) / len(completed), 2)
            if completed else None
        ),
        "avg_wall_ms": (
            round(sum(r.get("wall_ms", 0) for r in completed) / len(completed), 1)
            if completed else None
        ),
        "model_calls": [r.get("model_calls", 0) for r in completed],
        "danger_actions": [
            r.get("danger_actions", 0) for r in completed
        ],
        "estop_triggered": any(r.get("estop_triggered") for r in results),
        "results": results,
    }
    (out_dir / f"{spec.id}-summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return summary


def aggregate(all_summaries: list[dict]) -> dict:
    """按类别与通道拆分的汇总（指标全部带样本量）。"""
    categories: dict[str, list[dict]] = {}
    for s in all_summaries:
        categories.setdefault(s["category"], []).append(s)
    by_category = {}
    for category, items in categories.items():
        passed1 = sum(1 for s in items if s["success_at_1"])
        passed3 = sum(1 for s in items if s["success_at_3"])
        steps = [s["avg_steps"] for s in items if s["avg_steps"] is not None]
        walls = [s["avg_wall_ms"] for s in items if s["avg_wall_ms"] is not None]
        by_category[category] = {
            "n": len(items),
            "success_at_1": f"{passed1}/{len(items)}",
            "success_at_3": f"{passed3}/{len(items)}",
            "avg_steps": round(sum(steps) / len(steps), 2) if steps else None,
            "avg_wall_ms": round(sum(walls) / len(walls), 1) if walls else None,
            "danger_actions_total": sum(
                sum(s.get("danger_actions", [])) for s in items
            ),
        }
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
    total = {
        "n_tasks": len(all_summaries),
        "success_at_1": sum(1 for s in all_summaries if s["success_at_1"]),
        "success_at_3": sum(1 for s in all_summaries if s["success_at_3"]),
        "danger_actions_total": sum(
            sum(s.get("danger_actions", [])) for s in all_summaries
        ),
        "estop_triggered": any(s.get("estop_triggered") for s in all_summaries),
    }
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

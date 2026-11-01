"""判分器负向对照（防止 30/30 是橡皮图章）。

三个对照，每个都要求「伪造状态下 judge 必须**不**给 pass」：

1. **judge 断言必然不成立**：c09（17×23=391）的 judge 换成期望「392」，
   真机跑一遍 → attempt 必须 fail；
2. **脏状态**：f01 的 reset 换成 (a) 写入**错误内容** (b) 完全不跑——
   任务照常执行，judge 读回对不上 → 必须 fail（或 blocked），不许 pass；
3. **伪造错误状态**：真机把计算器打到 999，再跑 c09 的 judge（期望 391）
   → 必须 ok=False 且读到的就是 999。

产物落 ``runs/w4-negative/``（新增目录，不碰既有轮次）。
退出码 0 = 全部对照成立（判分器能失败）；非 0 = 有对照翻车 = judge 有洞。
"""

from __future__ import annotations

import dataclasses
import json
import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "src"))
sys.path.insert(0, str(_ROOT / "scripts"))

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from taskbench import TASK_TMP, TaskRun, run_attempt  # noqa: E402
from taskbench_tasks import TASKS_BY_ID  # noqa: E402

from minicode.guard.lockext import hold_desktop_lock  # noqa: E402
from minicode.guard.policy import GuardBudget  # noqa: E402

OUT_DIR = _ROOT / "runs" / "w4-negative"
_PROTOCOL = ""


def _attempt_with_pause_retry(spec, tries: int = 4) -> dict:
    """跑一个 attempt；被护栏 6 让位就等 2s 重试（对照也要让位真人）。"""
    result: dict = {"attempt_status": "paused"}
    for _ in range(tries):
        result = run_attempt(spec, OUT_DIR, _PROTOCOL)
        if result.get("attempt_status") != "paused":
            return result
        time.sleep(2.0)
    # 步骤级证据（诊断用：judge 失败时要能看出动作层发生了什么）。
    result["steps_summary"] = [
        {k: s.get(k) for k in ("do", "ok", "error_code")}
        for s in result.get("steps", [])
    ]
    return result


def _dirty_reset_wrong_content(run) -> None:
    """对照 2a：reset 写入**错误**内容（模拟上一次残留/脏状态）。"""
    path = TASK_TMP / "f01.txt"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("负向对照：这是脏内容，不是 f01 的行\n", encoding="utf-8")
    run.vars["file_path"] = str(path)


def _no_reset(run) -> None:
    """对照 2b：reset 完全不跑（vars 里没有 file_path）。"""


def control_1() -> dict:
    """judge 断言必然不成立：c09 期望 392，实况 391 → 必须 fail。"""
    spec = TASKS_BY_ID["c09"]
    doctored = dataclasses.replace(spec, judge=_doctored_c09)
    result = _attempt_with_pause_retry(doctored)
    status = result.get("attempt_status")
    ok = status == "fail"
    return {"control": "1_judge_impossible", "ok": ok,
            "expect": "fail", "got": status,
            "detail": {k: result.get(k) for k in
                       ("verdict", "error", "note") if result.get(k) is not None}}


def _doctored_c09(run) -> dict:
    verdict = TASKS_BY_ID["c09"].judge(run)
    verdict["doctored_expect"] = "392"
    verdict["ok"] = all(n in verdict.get("display", "") for n in ("392",))
    return verdict


def control_2() -> dict:
    """脏状态：f01 的 reset 换成错内容 / 不跑 → 必须 fail 或 blocked。"""
    spec = TASKS_BY_ID["f01"]
    rows = []
    for label, bad_reset in (("2a_wrong_content", _dirty_reset_wrong_content),
                             ("2b_reset_skipped", _no_reset)):
        doctored = dataclasses.replace(spec, reset=bad_reset)
        result = _attempt_with_pause_retry(doctored)
        status = result.get("attempt_status")
        rows.append({
            "control": label, "expect": "fail 或 blocked", "got": status,
            "ok": status in ("fail", "blocked"),
            "detail": {k: result.get(k) for k in
                       ("verdict", "error", "note") if result.get(k) is not None},
        })
    return {"control": "2_dirty_state", "ok": all(r["ok"] for r in rows),
            "sub": rows}


def control_3() -> dict:
    """伪造错误状态：真机把计算器打到 999，c09 的 judge（期望 391）必须拒绝。"""
    spec = TASKS_BY_ID["c09"]
    run = TaskRun(spec, OUT_DIR, _PROTOCOL)
    run.started = time.monotonic()
    detail: dict = {"control": "3_fake_state_999", "expect": "judge ok=False 且 display 含 999"}
    try:
        run.start_app()
        run.build_service(GuardBudget())
        detail["clicks"] = []
        for _ in range(3):  # 打出 999
            response = run.service.execute(
                {"protocol": _PROTOCOL, "action": "click", "target": "九"}
            )
            detail["clicks"].append(
                {"ok": response.ok, "error_code": response.error_code}
            )
            if not response.ok:
                detail["error"] = f"打 999 失败：{response.error_code}"
                detail["ok"] = None  # 环境问题，不算对照失败也不算通过
                return detail
            time.sleep(0.3)
        verdict = spec.judge(run) or {}
        display = verdict.get("display", "")
        detail["display"] = display
        detail["judge_ok"] = verdict.get("ok")
        detail["ok"] = (verdict.get("ok") is False and "999" in display
                        and "391" not in display)
        return detail
    except Exception as exc:  # noqa: BLE001
        detail["error"] = f"{type(exc).__name__}: {exc}"
        detail["ok"] = None
        return detail
    finally:
        estop = getattr(getattr(run.service, "guard_session", None), "estop", None)
        if estop is not None:
            try:
                estop.stop()
            except Exception:
                pass
        try:
            run.close_app()
        except Exception:
            pass


def main() -> int:
    global _PROTOCOL
    from minicode.action.protocol import PROTOCOL_VERSION

    _PROTOCOL = PROTOCOL_VERSION
    import argparse
    import os

    parser = argparse.ArgumentParser(description="判分器负向对照")
    parser.add_argument("--only", type=int, default=0,
                        help="只跑第 N 个对照（1-3）；0=全部")
    only = parser.parse_args().only

    # 同进程跑多个对照会互相污染（对死窗口的 UIA 查询挂住查询池线程，
    # 后续对照被守卫快速拒绝）——每个对照独立进程执行。
    if only:
        os.environ.setdefault("MINICODE_TYPE_PER_CHAR_DELAY", "0.05")
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        TASK_TMP.mkdir(parents=True, exist_ok=True)
        time.sleep(2.0)
        controls = [ (control_1, control_2, control_3)[only - 1]() ]
        (OUT_DIR / f"negative-control-{only}.json").write_text(
            json.dumps({"controls": controls}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(json.dumps(controls[0], ensure_ascii=False)[:500])
        return 0 if controls[0].get("ok") is True else 1

    import subprocess

    os.environ.setdefault("MINICODE_TYPE_PER_CHAR_DELAY", "0.05")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    TASK_TMP.mkdir(parents=True, exist_ok=True)
    time.sleep(2.0)  # 开场静默（同评测台：躲开进程启动的残余输入窗口）

    controls = []
    for index, name in ((1, "control_1"), (2, "control_2"), (3, "control_3")):
        with hold_desktop_lock("nightly-w4-negative", f"负向对照：{name}") as holds:
            if not holds:
                controls.append({"control": name, "ok": None,
                                 "error": "拿不到真机占用锁"})
                continue
            print(f"--- {name} ---", flush=True)
            result = subprocess.run(
                [sys.executable, "-I", str(Path(__file__).resolve()),
                 "--only", str(index)],
                capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=420,
            )
            print(result.stdout[-600:], flush=True)
            if result.returncode != 0:
                print(result.stderr[-400:], flush=True)
            part_file = OUT_DIR / f"negative-control-{index}.json"
            entry = (json.loads(part_file.read_text(encoding="utf-8"))
                     ["controls"][0] if part_file.exists()
                     else {"control": name, "ok": None,
                           "error": f"子进程退出码 {result.returncode}"})
            controls.append(entry)
        time.sleep(1.0)

    judged = [c for c in controls if c.get("ok") is not None]
    passed = sum(1 for c in judged if c["ok"])
    summary = {
        "判分器负向对照": f"{passed}/{len(judged)} 通过"
        f"（{'全部成立' if len(judged) == 3 and passed == 3 else '见明细'}）",
        "controls": controls,
    }
    (OUT_DIR / "negative-control.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"判分器负向对照：{passed}/{len(judged)} 通过")
    return 0 if len(judged) == 3 and passed == 3 else 1


if __name__ == "__main__":
    raise SystemExit(main())

"""判分洞修复的合成自检（不启真机）：伪造 run 状态，修复后的 judge 必须拒判。

侦察 A 发现的三个洞（i16 恒真 / i17 子串平凡满足 / i19 期望==初始态），
这里用假 run 直接打 judge 谓词：**洞场景必须 fail，正常场景必须 pass**。
真机的负向对照（scripts/_w4_negative_control.py）再打完整管线。
"""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "src"))
sys.path.insert(0, str(_ROOT / "scripts"))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import taskbench_tasks as tt  # noqa: E402

_SPECS = {spec.id: spec for spec in tt.ALL_TASKS}


class FakeRun:
    """judge 需要的最小 run：steps + vars + query（回预置行）。"""

    def __init__(self, steps, vars_, display_rows):
        self.steps = steps
        self.vars = vars_
        self._rows = display_rows

    def query(self, **_kw):
        return self._rows


def rows(display: str):
    return [{"name": display, "value": display, "rect": [0, 0, 10, 10]}]


def check(name: str, judge, run, expect_ok: bool) -> bool:
    verdict = judge(run)
    ok = bool(verdict.get("ok"))
    flag = "PASS" if ok == expect_ok else "**SELF-CHECK FAIL**"
    print(f"{flag} {name} -> judge_ok={ok} (expect {expect_ok}) :: "
          f"{str(verdict)[:140]}")
    return ok == expect_ok


def main() -> int:
    all_ok = True

    # -- i16：清除回 0（中间必须证到 56） -----------------------------------
    judge16 = _SPECS["i16"].judge
    all_ok &= check(
        "i16 洞场景：动作全失败、显示停在初始 0",
        judge16,
        FakeRun(steps=[{"do": "click", "ok": False}], vars_={},
                display_rows=rows("显示为 0")),
        expect_ok=False)
    all_ok &= check(
        "i16 正常场景：mid=56、final=0",
        judge16,
        FakeRun(steps=[{"do": "click", "ok": True}] * 6,
                vars_={"mid": rows("显示为 56")},
                display_rows=rows("显示为 0")),
        expect_ok=True)
    all_ok &= check(
        "i16 失败场景：清除没生效（final 仍是 56）",
        judge16,
        FakeRun(steps=[{"do": "click", "ok": True}] * 6,
                vars_={"mid": rows("显示为 56")},
                display_rows=rows("显示为 56")),
        expect_ok=False)

    # -- i17：退格（「12」不得来自「123」的平凡子串） ------------------------
    judge17 = _SPECS["i17"].judge
    all_ok &= check(
        "i17 洞场景：Backspace 失败、显示仍是 123（旧 judge 在这里假过）",
        judge17,
        FakeRun(steps=[{"do": "click", "ok": True}] * 3
                + [{"do": "click", "ok": False}],
                vars_={}, display_rows=rows("显示为 123")),
        expect_ok=False)
    all_ok &= check(
        "i17 正常场景：退格后显示 12",
        judge17,
        FakeRun(steps=[{"do": "click", "ok": True}] * 4,
                vars_={}, display_rows=rows("显示为 12")),
        expect_ok=True)

    # -- i19：撤销（输入与撤销两步都必须真实成功） ---------------------------
    judge19 = _SPECS["i19"].judge
    all_ok &= check(
        "i19 洞场景：type 被拒、内容本就是基础行（旧 judge 在这里假过）",
        judge19,
        FakeRun(steps=[{"do": "type", "ok": False},
                       {"do": "key", "ok": True},
                       {"do": "query", "ok": True}],
                vars_={"doc": rows("i19 基础行")},
                display_rows=[]),
        expect_ok=False)
    all_ok &= check(
        "i19 正常场景：输入成功+撤销成功+读回基础行",
        judge19,
        FakeRun(steps=[{"do": "type", "ok": True},
                       {"do": "key", "ok": True},
                       {"do": "query", "ok": True}],
                vars_={"doc": rows("i19 基础行")},
                display_rows=[]),
        expect_ok=True)
    all_ok &= check(
        "i19 失败场景：撤销没生效（内容还是输入后的）",
        judge19,
        FakeRun(steps=[{"do": "type", "ok": True},
                       {"do": "key", "ok": False},
                       {"do": "query", "ok": True}],
                vars_={"doc": rows("i19 基础行i19 会被撤销的内容")},
                display_rows=[]),
        expect_ok=False)

    print("SELF-CHECK:", "ALL PASS" if all_ok else "HAS FAILURES")
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

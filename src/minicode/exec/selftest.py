"""selftest.py -- 一条命令跑完的执行层自检。

    python selftest.py                 # 全跑
    python selftest.py --skip-click    # 跳过真实点击（其余照跑）
    python selftest.py -v              # 打印每项细节

检查项
------
1. DPI 感知生效：GetSystemMetrics 与 GetDeviceCaps 一致，且都是物理像素
2. SendInput 自检：sizeof(INPUT)==40 且 SendInput 返回非 0
3. 截图分辨率是物理像素（且不等于逻辑分辨率）
4. UIA 定点查询：能在已知窗口里按名字查到控件，且坐标是物理像素
5. 点击序列时序：move -> 60ms -> down -> 40ms -> up
"""

from __future__ import annotations

import argparse
import os
import sys
import time

# 自检输出要干净，先静音 dpi.py 的启动报告（下面会自己打一份结构化的）
os.environ.setdefault("GUI_CU_DPI_QUIET", "1")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import capture  # noqa: E402
import dpi  # noqa: E402
import input as win_input  # noqa: E402
import uia  # noqa: E402

PASS, FAIL, SKIP = "PASS", "FAIL", "SKIP"


class Check:
    __slots__ = ("name", "status", "detail")

    def __init__(self, name, status, detail=""):
        self.name = name
        self.status = status
        self.detail = detail

    def __repr__(self):
        return f"[{self.status}] {self.name}" + (f" -- {self.detail}" if self.detail else "")


# ---------------------------------------------------------------------------
# 1. DPI 感知
# ---------------------------------------------------------------------------


def check_dpi_awareness() -> Check:
    name = "DPI 感知生效 (GetSystemMetrics vs GetDeviceCaps)"
    try:
        r = dpi.scale_report()
    except Exception as exc:
        return Check(name, FAIL, f"scale_report() 抛异常: {exc}")

    pw, ph = r["physical"]
    lw, lh = r["logical"]
    caps_match = (r["device_caps_horzres"] == pw and r["device_caps_vertres"] == ph)
    ok = r["awareness_ok"] and caps_match and pw > 0 and ph > 0

    detail = (f"awareness={r['awareness']} physical={pw}x{ph} logical={lw}x{lh} "
              f"scale={r['scale']:.3g} GetDeviceCaps={r['device_caps_horzres']}"
              f"x{r['device_caps_vertres']}@{r['device_caps_dpi']}dpi")
    if not r["awareness_ok"]:
        detail += " | 感知模式不是 PER_MONITOR_AWARE_V2"
    if not caps_match:
        detail += " | GetSystemMetrics 与 GetDeviceCaps 不一致(可能被虚拟化)"
    # 缩放确实生效时，物理尺寸必须大于逻辑尺寸
    if r["scale"] > 1.01 and (pw <= lw or ph <= lh):
        ok = False
        detail += " | 有缩放但物理尺寸未大于逻辑尺寸"
    return Check(name, PASS if ok else FAIL, detail)


# ---------------------------------------------------------------------------
# 2. SendInput 自检
# ---------------------------------------------------------------------------


def check_sendinput() -> Check:
    name = "SendInput 自检 (sizeof(INPUT)==40 且返回值非 0)"
    try:
        result = win_input.self_check()
    except win_input.InputSelfCheckError as exc:
        return Check(name, FAIL, str(exc))
    except Exception as exc:
        return Check(name, FAIL, f"自检抛异常: {exc}")

    ok = (result["sizeof_ok"] and result["send_input_ok"]
          and result["sizeof_input"] == result["sizeof_input_expected"])
    delta = result.get("readback_delta")
    detail = (f"sizeof(INPUT)={result['sizeof_input']} (期望 {result['sizeof_input_expected']}) "
              f"send_input_ok={result['send_input_ok']} 回读偏差={delta}")
    if ok and delta and (abs(delta[0]) > 1 or abs(delta[1]) > 1):
        # 偏差大不一定是 bug（真实用户可能同时在动鼠标），只提示
        detail += " | 回读有偏差（可能有人同时在动鼠标）"
    return Check(name, PASS if ok else FAIL, detail)


# ---------------------------------------------------------------------------
# 3. 截图是物理像素
# ---------------------------------------------------------------------------


def check_capture_physical() -> Check:
    name = "截图分辨率是物理像素"
    try:
        frame, meta = capture.grab_frame()
    except Exception as exc:
        return Check(name, FAIL, f"抓图抛异常: {exc}")

    pw, ph = dpi.physical_screen_size()
    lw, lh = dpi.logical_screen_size()
    ok = (meta.width == pw and meta.height == ph and meta.physical_pixels)

    shape = getattr(frame, "shape", None)
    detail = (f"抓到 {meta.width}x{meta.height} 物理={pw}x{ph} 逻辑={lw}x{lh} "
              f"backend={meta.backend} 用时={meta.capture_ms:.1f}ms shape={shape}")
    if pw != lw:
        detail += f" | 物理≠逻辑，已确认不是虚拟化尺寸"
    else:
        detail += " | 本机缩放=1，物理与逻辑相同（无法据此区分）"
    if shape is not None and tuple(shape[:2]) != (meta.height, meta.width):
        ok = False
        detail += " | 数组形状与元数据不一致"
    return Check(name, PASS if ok else FAIL, detail)


# ---------------------------------------------------------------------------
# 4. UIA 定点查询
# ---------------------------------------------------------------------------


def _candidate_windows():
    """按可靠性排序的候选窗口。"""
    wins = uia.enumerate_windows()
    out = []
    taskbar = [w for w in wins if w.class_name == "Shell_TrayWnd"]
    out.extend(("任务栏 Shell_TrayWnd", w) for w in taskbar)
    fg = win_input.user32.GetForegroundWindow() if hasattr(win_input, "user32") else 0
    for w in wins:
        if w.hwnd == fg:
            out.append((f"前台窗口 {w.class_name}", w))
    for w in wins:
        if w.title and w.class_name not in ("Shell_TrayWnd",):
            out.append((f"{w.class_name} / {w.title[:24]}", w))
    return out


def check_uia_point_query(verbose: bool = False) -> Check:
    name = "UIA 定点查询（按名字查控件 + 物理像素坐标）"
    try:
        u = uia.UIA()
    except Exception as exc:
        return Check(name, FAIL, f"UIA 初始化失败: {exc}")

    errors = []
    tried = 0
    for label, w in _candidate_windows():
        if tried >= 6:
            break
        tried += 1
        try:
            els = uia.find_controls(hwnd=w.hwnd, uia=u)
        except Exception as exc:
            errors.append(f"{label}: find 失败 {exc}")
            continue
        if not els:
            errors.append(f"{label}: 无元素")
            continue

        usable = []
        for e in els:
            ok, why = uia.is_usable(e, interactive_only=True)
            if ok:
                usable.append(e)
            else:
                if verbose:
                    errors.append(f"    不可用: {e.control_type_name} {e.name[:16]!r} ({why})")

        if not usable:
            errors.append(f"{label}: {len(els)} 个元素但没有一个通过可用性判定")
            continue

        # 拿一个可用的元素，验证"按名字定点查"能查回同一个东西
        target = usable[0]
        t_name = target.name
        t_rect = target.bounding_rect
        again = uia.find_one(hwnd=w.hwnd, name=t_name, uia=u)
        if again is None:
            errors.append(f"{label}: 按名字复查失败 ({t_name!r})")
            continue
        r2 = again.bounding_rect
        if (r2.left, r2.top) != (t_rect.left, t_rect.top):
            errors.append(f"{label}: 复查坐标不一致 {t_rect.left},{t_rect.top} vs {r2.left},{r2.top}")
            continue

        # 物理像素判据：任务栏元素的下沿应贴近物理屏底
        vh = dpi.virtual_desktop_size()[1]
        phys_ok = (t_rect.bottom <= vh + 1 and t_rect.right <= dpi.virtual_desktop_size()[0] + 1)
        bottom_near_screen = t_rect.bottom > vh * 0.85
        detail = (f"命中窗口 {label} (hwnd={w.hwnd})；控件 {target.control_type_name} "
                  f"name={t_name[:20]!r} class={target.class_name[:20]!r} "
                  f"rect=({t_rect.left},{t_rect.top},{t_rect.right},{t_rect.bottom}) "
                  f"物理像素判定={'OK' if phys_ok and bottom_near_screen else '存疑'}；"
                  f"该窗口可用交互控件 {len(usable)}/{len(els)}")
        if not (phys_ok and bottom_near_screen):
            errors.append(f"{label}: 坐标不像物理像素 (bottom={t_rect.bottom}, vh={vh})")
            continue
        return Check(name, PASS, detail)

    return Check(name, FAIL, " | ".join(errors[:6]) or "没有可用的候选窗口")


# ---------------------------------------------------------------------------
# 5. 点击序列时序
# ---------------------------------------------------------------------------


def check_click_timing(skip: bool = False) -> Check:
    name = "点击序列时序 (move -> 60ms -> down -> 40ms -> up)"
    if skip:
        return Check(name, SKIP, "按 --skip-click 跳过（未做真实点击）")
    try:
        x, y = win_input.cursor_position()
        t = win_input.click(x, y)   # 点击光标当前所在处，扰动最小
    except Exception as exc:
        return Check(name, FAIL, f"点击抛异常: {exc}")

    # Windows 默认定时器精度 15.6ms，sleep 只会向上取整，所以用 >= 判定
    settle_ok = t.pre_move_ms >= win_input.CLICK_SETTLE * 1000 * 0.95
    hold_ok = t.hold_ms >= win_input.CLICK_HOLD * 1000 * 0.95
    ok = settle_ok and hold_ok and t.down_sent and t.up_sent
    detail = (f"实测 pre_move={t.pre_move_ms:.1f}ms (要求>={win_input.CLICK_SETTLE*1000:.0f}ms) "
              f"hold={t.hold_ms:.1f}ms (要求>={win_input.CLICK_HOLD*1000:.0f}ms) "
              f"total={t.total_ms:.1f}ms 目标=({t.target[0]},{t.target[1]})")
    if not settle_ok:
        detail += " | 移动到按下之间停顿不足"
    if not hold_ok:
        detail += " | 按住时长不足"
    return Check(name, PASS if ok else FAIL, detail)


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------


def run_all(skip_click: bool = False, verbose: bool = False) -> list[Check]:
    checks = [
        check_dpi_awareness(),
        check_sendinput(),
        check_capture_physical(),
        check_uia_point_query(verbose=verbose),
        check_click_timing(skip=skip_click),
    ]
    return checks


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Windows GUI agent 执行层自检")
    ap.add_argument("--skip-click", action="store_true",
                    help="跳过真实点击（会点击光标当前所在处）")
    ap.add_argument("-v", "--verbose", action="store_true", help="打印更多细节")
    args = ap.parse_args(argv)

    print("=" * 72)
    print("Windows GUI agent - 执行层自检")
    print("=" * 72)

    t0 = time.perf_counter()
    checks = run_all(skip_click=args.skip_click, verbose=args.verbose)
    elapsed = time.perf_counter() - t0

    print()
    for c in checks:
        print(f"[{c.status}] {c.name}")
        if c.detail:
            for i, line in enumerate(c.detail.split(" | ")):
                prefix = "       " if i == 0 else "       | "
                print(prefix + line)
        print()

    n_pass = sum(1 for c in checks if c.status == PASS)
    n_fail = sum(1 for c in checks if c.status == FAIL)
    n_skip = sum(1 for c in checks if c.status == SKIP)
    print("-" * 72)
    print(f"合计: {n_pass} PASS / {n_fail} FAIL / {n_skip} SKIP   "
          f"耗时 {elapsed:.1f}s")
    print("-" * 72)
    return 1 if n_fail else 0


if __name__ == "__main__":
    sys.exit(main())

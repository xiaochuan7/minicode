"""V5 落点诊断：起页面 → 窗口矩形 → 全屏截图 + 落点 crop（诊断脚本，不入交付）。"""
import ctypes
import ctypes.wintypes
import subprocess
import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "scripts"))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from v5_vision_tasks import EDGE, EDGE_CLASSES, _write_pages  # noqa: E402
from w3_probe import find_new_window, visible_windows  # noqa: E402

USER32 = ctypes.windll.user32
WM_CLOSE = 0x0010

out = _ROOT / "runs" / "v5-smoke"
pages = _write_pages(out)
url = pages["canvas-text-buttons"].as_uri()

before = {w[0] for w in visible_windows(EDGE_CLASSES)}
proc = subprocess.Popen([EDGE, "--new-window", url])
found = find_new_window(before, class_filter=EDGE_CLASSES)
if found is None:
    print("no window")
    sys.exit(1)
hwnd, wclass, title = found
print("hwnd", hwnd, "class", wclass, "title", title)
time.sleep(3.0)
rect = ctypes.wintypes.RECT()
USER32.GetWindowRect(hwnd, ctypes.byref(rect))
print("window rect:", rect.left, rect.top, rect.right, rect.bottom)

from PIL import ImageGrab  # noqa: E402

screen = ImageGrab.grab(all_screens=True)
print("screen size:", screen.size)
screen.save(out / "diag-screen.png")
crop = screen.crop((661 - 220, 311 - 160, 661 + 220, 311 + 160))
crop.save(out / "diag-landing-crop.png")
print("saved diag-screen.png + diag-landing-crop.png")

for _ in range(4):
    if not USER32.IsWindow(hwnd) or not USER32.IsWindowVisible(hwnd):
        break
    USER32.PostMessageW(hwnd, WM_CLOSE, 0, 0)
    time.sleep(0.8)
if proc.poll() is None:
    proc.terminate()
print("closed")

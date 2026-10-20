"""真实桌面上的两级 grounding 实测：minimax-m3.1-flash-free。

L0: 真实截图(物理像素) -> 降采样到 1568 长边 -> 模型粗定位
L1: 以粗点为中心从【原图】裁 512x512 -> 模型精定位 -> 映射回物理像素
真值: UIA 的控件 bounding rect 中心
只用 stdlib + Pillow（降采样/裁剪/编码）。
"""
import base64, io, json, re, sys, time, urllib.request, urllib.error
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "exec"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "coord"))

import dpi  # noqa: E402
import capture  # noqa: E402
import uia  # noqa: E402
from PIL import Image  # noqa: E402

CRED = Path(r"C:\Users\22197\.dsh\.credentials.yaml")
KEY = next(re.match(r"\s*OPENCODE_GO_API_KEY:\s*(\S+)", l).group(1)
           for l in CRED.read_text(encoding="utf-8", errors="ignore").splitlines()
           if re.match(r"\s*OPENCODE_GO_API_KEY:\s*", l))
BASE = "https://opencode.ai/zen/go/v1/messages"
MODEL = "minimax-m3.1-flash-free"
HEADERS = {
    "content-type": "application/json", "accept": "application/json",
    "x-api-key": KEY, "anthropic-version": "2023-06-01",
    "x-opencode-session": "dsh-union-alpha",
    "user-agent": "opencode/1.0.0", "x-opencode-client": "cli",
}
LAT = []

def call(text, png_bytes, max_tokens=1500, timeout=120, extra_body=None):
    body = {"model": MODEL, "max_tokens": max_tokens, "messages": [
        {"role": "user", "content": [
            {"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                         "data": base64.b64encode(png_bytes).decode()}},
            {"type": "text", "text": text}]}]}
    if extra_body:
        body.update(extra_body)
    req = urllib.request.Request(BASE, data=json.dumps(body).encode(), method="POST", headers=HEADERS)
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            d = json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return None, e.code, e.read().decode()[:200], time.time() - t0
    dt = time.time() - t0
    LAT.append(dt)
    txt = "\n".join(b.get("text", "") for b in d.get("content", []) if b.get("type") == "text")
    return txt, r.status, d.get("usage", {}), dt

dpi.enable_dpi_awareness()
print("DPI: %s | 物理 %sx%s | 缩放 %sx | 虚拟桌面原点 %s" % (
    dpi.dpi_awareness_context(), dpi.physical_screen_size()[0], dpi.physical_screen_size()[1],
    dpi.scale_factor(), dpi.virtual_desktop_origin()))

frame, meta = capture.grab_frame()
W, H = meta.width, meta.height
if hasattr(frame, "bgra"):
    img = Image.frombytes("RGBA", (W, H), frame.bgra).convert("RGB")
else:  # numpy (H, W, 3) BGR
    import numpy as np
    img = Image.fromarray(np.asarray(frame)[:, :, ::-1]).convert("RGB")
print("截图: %dx%d 物理像素  backend=%s" % (W, H, meta.backend))

# ---- 选一个 UIA 可定位的真值目标（任务栏上的小按钮） ----
u = uia.UIA()
wins = uia.find_window(class_substr="Shell_TrayWnd")
win = wins[0] if isinstance(wins, list) and wins else (wins or None)
print("任务栏 hwnd:", getattr(win, "hwnd", None))
cands = []
if win:
    for el in uia.find_controls(hwnd=win.hwnd, control_type=50000, limit=80, uia=u):
        try:
            if el.is_offscreen: continue
            nm = (el.name or "").strip()
            r = el.bounding_rect
            if not nm or (r.right - r.left) > 400 or (r.bottom - r.top) > 120: continue
            cands.append((nm, el.class_name, r.left, r.top, r.right, r.bottom))
        except Exception:
            pass
print("候选控件 %d 个:" % len(cands))
for c in cands[:14]:
    print("   %-28s %-18s (%d,%d)-(%d,%d)" % (c[0][:28], c[1], c[2], c[3], c[4], c[5]))
if not cands:
    raise SystemExit("没有候选")
# 选名字最短且唯一的
short = sorted(cands, key=lambda c: len(c[0]))
target = next((c for c in short if sum(1 for x in cands if x[0] == c[0]) == 1), short[0])
NAME, CLS, L, T, R, B = target
GT = ((L + R) / 2.0, (T + B) / 2.0)
print("\n选定目标: name=%r class=%s rect=(%d,%d)-(%d,%d) 中心=(%.1f,%.1f) 尺寸=%dx%d"
      % (NAME, CLS, L, T, R, B, GT[0], GT[1], R - L, B - T))

# ================= L0 整屏粗定位 =================
LONG = 1568
scale = LONG / max(W, H)
small = img.resize((int(W * scale), int(H * scale)), Image.LANCZOS)
buf = io.BytesIO(); small.save(buf, "PNG"); small_png = buf.getvalue()
print("\n[L0] 整屏 %dx%d -> %dx%d (%.3fx 降采样), png %d bytes"
      % (W, H, small.width, small.height, 1 / scale, len(small_png)))

p0 = (
    "这是一张 Windows 桌面截图（%dx%d，已缩放）。\n"
    "请找出界面上名为 %r 的元素，给出它的【中心】坐标。\n"
    "坐标用 0~1000 归一化（左上角为 0,0，右下角为 1000,1000）。\n"
    "只输出 JSON：{\"x\": 数字, \"y\": 数字}\n"
) % (small.width, small.height, NAME)

txt, st, usage, dt = call(p0, small_png)
print("[L0] HTTP %s %.2fs usage=%s" % (st, dt, json.dumps(usage, ensure_ascii=False)))
print("[L0] reply:", (txt or "")[:300].replace("\n", " "))
m = re.search(r"\{[^}]*\}", txt or "")
if not m:
    raise SystemExit("L0 解析失败")
d = json.loads(m.group(0))
coarse_small = (d["x"] / 1000.0 * small.width, d["y"] / 1000.0 * small.height)
coarse_phys = (coarse_small[0] / scale, coarse_small[1] / scale)
err0 = ((coarse_phys[0] - GT[0]) ** 2 + (coarse_phys[1] - GT[1]) ** 2) ** 0.5
print("[L0] 粗点(物理)=%.1f,%.1f  真值=%.1f,%.1f  误差=%.1f px"
      % (coarse_phys[0], coarse_phys[1], GT[0], GT[1], err0))

# ================= L1 原图 crop 精定位 =================
CS = 512
cx = int(round(coarse_phys[0])); cy = int(round(coarse_phys[1]))
x0 = max(0, min(W - CS, cx - CS // 2)); y0 = max(0, min(H - CS, cy - CS // 2))
crop = img.crop((x0, y0, x0 + CS, y0 + CS))
buf = io.BytesIO(); crop.save(buf, "PNG"); crop_png = buf.getvalue()
print("\n[L1] 从【原图】裁 %dx%d 区域 (x0=%d,y0=%d), png %d bytes" % (CS, CS, x0, y0, len(crop_png)))

p1 = (
    "这是一张从 Windows 桌面截图中裁出的 %dx%d 区域（未缩放，1 像素 = 1 屏幕像素）。\n"
    "原图左上角在整屏的坐标是 (%d, %d)。\n"
    "请在这张图里找出名为 %r 的元素，给出它在这张图里的【中心】坐标。\n"
    "坐标用 0~1000 归一化（相对这张 %dx%d 的图，左上角为 0,0）。\n"
    "只输出 JSON：{\"x\": 数字, \"y\": 数字}\n"
) % (CS, CS, x0, y0, NAME, CS, CS)

txt, st, usage, dt = call(p1, crop_png)
print("[L1] HTTP %s %.2fs usage=%s" % (st, dt, json.dumps(usage, ensure_ascii=False)))
print("[L1] reply:", (txt or "")[:300].replace("\n", " "))
m = re.search(r"\{[^}]*\}", txt or "")
if not m:
    raise SystemExit("L1 解析失败")
d = json.loads(m.group(0))
fine_local = (d["x"] / 1000.0 * CS, d["y"] / 1000.0 * CS)
fine_phys = (x0 + fine_local[0], y0 + fine_local[1])
err1 = ((fine_phys[0] - GT[0]) ** 2 + (fine_phys[1] - GT[1]) ** 2) ** 0.5
print("[L1] 细点(物理)=%.1f,%.1f  真值=%.1f,%.1f  误差=%.1f px"
      % (fine_phys[0], fine_phys[1], GT[0], GT[1], err1))

print("\n" + "=" * 66)
print("真实桌面两级 grounding 结果")
print("=" * 66)
print("目标          : %r (%dx%d px)" % (NAME, R - L, B - T))
print("UIA 真值中心  : (%.1f, %.1f) 物理像素" % GT)
print("L0 整屏粗定位 : 误差 %.1f px" % err0)
print("L1 crop 精定位: 误差 %.1f px" % err1)
print("两次调用延迟  : %.2fs / %.2fs  (合计 %.2fs)" % (LAT[0], LAT[1], sum(LAT)))
hit = "命中" if err1 <= 8 else ("接近" if err1 <= 30 else "未命中")
print("判定          : %s (目标中心 8px 内算命中)" % hit)

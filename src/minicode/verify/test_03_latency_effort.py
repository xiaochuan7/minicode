"""测 minimax-m3.1-flash-free 的思考开关 / effort / 分辨率 对延迟与精度的影响。"""
import base64, io, json, re, sys, time, urllib.request, urllib.error
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "exec"))
import dpi, capture, uia  # noqa: E402
from PIL import Image  # noqa: E402

CRED = Path(r"C:\Users\22197\.dsh\.credentials.yaml")
KEY = next(re.match(r"\s*OPENCODE_GO_API_KEY:\s*(\S+)", l).group(1)
           for l in CRED.read_text(encoding="utf-8", errors="ignore").splitlines()
           if re.match(r"\s*OPENCODE_GO_API_KEY:\s*", l))
BASE = "https://opencode.ai/zen/go/v1/messages"
HEADERS = {
    "content-type": "application/json", "accept": "application/json",
    "x-api-key": KEY, "anthropic-version": "2023-06-01",
    "x-opencode-session": "dsh-union-alpha",
    "user-agent": "opencode/1.0.0", "x-opencode-client": "cli",
}

def call(text, png, extra=None, max_tokens=1200, timeout=120):
    body = {"model": "minimax-m3.1-flash-free", "max_tokens": max_tokens, "messages": [
        {"role": "user", "content": [
            {"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                         "data": base64.b64encode(png).decode()}},
            {"type": "text", "text": text}]}]}
    if extra: body.update(extra)
    req = urllib.request.Request(BASE, data=json.dumps(body).encode(), method="POST", headers=HEADERS)
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            d = json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return None, e.code, e.read().decode()[:160], time.time() - t0, {}
    except Exception as e:
        return None, -1, repr(e)[:160], time.time() - t0, {}
    dt = time.time() - t0
    txt = "\n".join(b.get("text", "") for b in d.get("content", []) if b.get("type") == "text")
    u = d.get("usage", {})
    return txt, r.status, "", dt, u

dpi.enable_dpi_awareness()
frame, meta = capture.grab_frame()
W, H = meta.width, meta.height
try:
    import numpy as np
    img = Image.fromarray(np.asarray(frame)[:, :, ::-1]).convert("RGB")
except Exception:
    img = Image.frombytes("RGBA", (W, H), frame.bgra).convert("RGB")

u = uia.UIA()
wins = uia.find_window(class_substr="Shell_TrayWnd")
win = wins[0] if isinstance(wins, list) and wins else wins
NAME, GT = None, None
for el in uia.find_controls(hwnd=win.hwnd, control_type=50000, limit=80, uia=u):
    try:
        if el.is_offscreen: continue
        nm = (el.name or "").strip()
        if nm != "开始": continue
        r = el.bounding_rect
        NAME, GT = nm, ((r.left + r.right) / 2.0, (r.top + r.bottom) / 2.0)
        break
    except Exception: pass
if not GT: raise SystemExit("找不到目标")
print("目标 %r 真值 (%.1f, %.1f)" % (NAME, GT[0], GT[1]))

def png_of(im):
    b = io.BytesIO(); im.save(b, "PNG"); return b.getvalue()

PROMPT = (
    "这是一张 Windows 桌面截图（%dx%d，已缩放）。\n"
    "请找出界面上名为 %r 的元素，给出它的【中心】坐标。\n"
    "坐标用 0~1000 归一化（左上角为 0,0，右下角为 1000,1000）。\n"
    "只输出 JSON：{\"x\": 数字, \"y\": 数字}\n")

VARIANTS = [
    ("默认(thinking=max)", 1568, None),
    ("effort=low",         1568, {"output_config": {"effort": "low"}}),
    ("thinking disabled",  1568, {"thinking": {"type": "disabled"}}),
    ("reasoning_effort=low",1568, {"reasoning_effort": "low"}),
    ("默认 @1344宽",       1344, None),
    ("effort=low @1344宽", 1344, {"output_config": {"effort": "low"}}),
]

print("\n%-24s %-8s %7s %8s %9s %9s %s" % ("变体", "尺寸", "HTTP", "延迟s", "in_tok", "思考tok", "误差px"))
print("-" * 88)
for label, long_edge, extra in VARIANTS:
    s = long_edge / max(W, H)
    im = img.resize((int(W * s), int(H * s)), Image.LANCZOS)
    png = png_of(im)
    txt, st, err, dt, usage = call(PROMPT % (im.width, im.height, NAME), png, extra)
    if st != 200:
        print("%-24s %-8s %7s %7.2fs  -> %s" % (label, "%dx%d" % (im.width, im.height), st, dt, err[:90]))
        continue
    m = re.search(r"\{[^}]*\}", txt or "")
    if not m:
        print("%-24s %-8s %7s %7.2fs  -> 解析失败: %s" % (label, "%dx%d" % (im.width, im.height), st, dt, (txt or "")[:80]))
        continue
    d = json.loads(m.group(0))
    px = d["x"] / 1000.0 * im.width / s
    py = d["y"] / 1000.0 * im.height / s
    e = ((px - GT[0]) ** 2 + (py - GT[1]) ** 2) ** 0.5
    th = (usage.get("output_tokens_details") or {}).get("thinking_tokens", "-")
    print("%-24s %-8s %7s %7.2fs %9s %9s %9.1f" % (
        label, "%dx%d" % (im.width, im.height), st, dt,
        usage.get("input_tokens", "-"), th, e))

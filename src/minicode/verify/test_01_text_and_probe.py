"""实测 minimax-m3.1-flash-free：文本 + 图像 grounding + 标度判定。

只用标准库。密钥从 ~/.dsh/.credentials.yaml 读，不硬编码、不打印。
"""
import base64, json, re, sys, time, urllib.request, urllib.error
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "coord"))
from calibrate import build_probe_image, default_targets  # noqa: E402

CRED = Path(r"C:\Users\22197\.dsh\.credentials.yaml")
BASE = "https://opencode.ai/zen/go/v1/messages"
MODEL = "minimax-m3.1-flash-free"
SESSION = "dsh-union-alpha"

def read_key():
    for line in CRED.read_text(encoding="utf-8", errors="ignore").splitlines():
        m = re.match(r"\s*OPENCODE_GO_API_KEY:\s*(\S+)", line)
        if m:
            return m.group(1)
    raise SystemExit("OPENCODE_GO_API_KEY not found")

KEY = read_key()

def call(messages, max_tokens=2000, timeout=120):
    body = json.dumps({
        "model": MODEL, "max_tokens": max_tokens,
        "messages": messages,
    }).encode()
    req = urllib.request.Request(BASE, data=body, method="POST", headers={
        "content-type": "application/json",
        "accept": "application/json",
        "x-api-key": KEY,
        "anthropic-version": "2023-06-01",
        "x-opencode-session": SESSION,
        # 这两个头是必须的：缺 user-agent 会被 Cloudflare 1010 拦掉
        "user-agent": "opencode/1.0.0",
        "x-opencode-client": "cli",
    })
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read().decode())
            return r.status, data, time.time() - t0
    except urllib.error.HTTPError as e:
        return e.code, {"error": e.read().decode()[:500]}, time.time() - t0
    except Exception as e:
        return -1, {"error": repr(e)}, time.time() - t0

def text_of(data):
    out = []
    for b in data.get("content", []):
        if b.get("type") == "text":
            out.append(b.get("text", ""))
    return "\n".join(out)

print("=" * 70)
print("TEST 1  纯文本调用")
print("=" * 70)
st, data, dt = call([{"role": "user", "content": "只回复两个字：就绪"}], max_tokens=200)
print("HTTP", st, "| %.2fs" % dt)
if st != 200:
    print(json.dumps(data, ensure_ascii=False)[:600]); raise SystemExit(1)
print("reply:", text_of(data)[:120].replace("\n", " "))
print("usage:", json.dumps(data.get("usage", {}), ensure_ascii=False))
print("model echo:", data.get("model"))

print()
print("=" * 70)
print("TEST 2  图像 grounding（真实探针图）")
print("=" * 70)
W, H = 800, 600
targets = default_targets(W, H, margin=60, size=40)
png = build_probe_image(W, H, targets)
b64 = base64.b64encode(png).decode()
print("probe image: %dx%d, %d bytes, targets=%s" % (
    W, H, len(png), [(t.name, t.x, t.y) for t in targets]))

prompt = (
    "这是一张 %dx%d 的图片，白底上有 4 个黑色方块，分别位于左上、右上、左下、右下。\n"
    "请给出每个方块【中心】的坐标，坐标用 0~1000 归一化（左上角为原点，右下角为 1000）。\n"
    "只输出一个 JSON 对象，不要任何解释：{\"tl\":[x,y],\"tr\":[x,y],\"bl\":[x,y],\"br\":[x,y]}\n"
) % (W, H)

st, data, dt = call([{"role": "user", "content": [
    {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": b64}},
    {"type": "text", "text": prompt},
]}], max_tokens=2000)
print("HTTP", st, "| %.2fs" % dt)
if st != 200:
    print(json.dumps(data, ensure_ascii=False)[:800]); raise SystemExit(1)
raw = text_of(data)
print("reply:", raw[:900])
print("usage:", json.dumps(data.get("usage", {}), ensure_ascii=False))

m = re.search(r"\{.*\}", raw, re.S)
if not m:
    print("!! 没解析出 JSON")
    raise SystemExit(0)
pts = json.loads(m.group(0))
vals = [v for p in pts.values() for v in p]
print()
print("原始输出:", pts)
print("输出最大值: %.2f  ->  %s" % (
    max(vals),
    "0~1 相对坐标" if max(vals) <= 1.5 else ("0~1000 归一化" if max(vals) <= 1000 else "绝对像素?")))
print()
print("%-5s %-14s %-16s %-16s %s" % ("目标", "真值(像素)", "模型(像素)", "模型(归一化)", "误差(px)"))
errs = []
for t in targets:
    key = t.name
    if key not in pts:
        print("%-5s MISSING" % key); continue
    nx, ny = pts[key]
    px, py = nx / 1000.0 * W, ny / 1000.0 * H
    e = ((px - t.x) ** 2 + (py - t.y) ** 2) ** 0.5
    errs.append(e)
    print("%-5s (%4d,%4d)     (%7.1f,%7.1f)     (%6.1f,%6.1f)     %6.1f" % (
        key, t.x, t.y, px, py, nx, ny, e))
if errs:
    print()
    print("平均误差 %.1f px | 最大 %.1f px | %s" % (
        sum(errs)/len(errs), max(errs),
        "PASS (<=2px)" if max(errs) <= 2 else ("MARGINAL" if max(errs) <= 20 else "FAIL")))

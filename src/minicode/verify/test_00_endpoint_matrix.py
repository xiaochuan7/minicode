import json, re, sys, time, urllib.request, urllib.error
from pathlib import Path

CRED = Path(r"C:\Users\22197\.dsh\.credentials.yaml")
KEY = next(re.match(r"\s*OPENCODE_GO_API_KEY:\s*(\S+)", l).group(1)
           for l in CRED.read_text(encoding="utf-8", errors="ignore").splitlines()
           if re.match(r"\s*OPENCODE_GO_API_KEY:\s*", l))
MODEL = "minimax-m3.1-flash-free"

BASE_HEADERS = {
    "content-type": "application/json",
    "x-api-key": KEY,
    "anthropic-version": "2023-06-01",
    "x-opencode-session": "dsh-union-alpha",
    "accept": "application/json",
}

UA_SETS = {
    "none": {},
    "python-urllib": {"user-agent": "Python-urllib/3.12"},
    "opencode-cli": {"user-agent": "opencode/1.0.0", "x-opencode-client": "cli"},
    "browser-ish": {"user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"},
}

ENDPOINTS = [
    ("anthropic /zen/go", "https://opencode.ai/zen/go/v1/messages", "anthropic"),
    ("anthropic /zen",   "https://opencode.ai/zen/v1/messages",   "anthropic"),
]

def try_call(url, kind, extra, body):
    h = dict(BASE_HEADERS); h.update(extra)
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST", headers=h)
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=90) as r:
            raw = r.read().decode()
            return r.status, raw[:400], time.time() - t0
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()[:300], time.time() - t0
    except Exception as e:
        return -1, repr(e)[:200], time.time() - t0

msg = [{"role": "user", "content": "只回复两个字：就绪"}]

for name, url, kind in ENDPOINTS:
    for hname, extra in UA_SETS.items():
        body = {"model": MODEL, "max_tokens": 64, "messages": msg}
        st, out, dt = try_call(url, kind, extra, body)
        print("[%-18s] %-14s -> HTTP %-3s  %.2fs  %s" % (name, hname, st, dt, out.replace("\n", " ")[:150]))
    print()

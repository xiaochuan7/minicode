"""把 guard session 的 ``trace.jsonl`` 渲染成**自包含 HTML 回放**（W8 演示）。

输入是**真实真机 trace**（runs/ 下，gitignored）；输出 docs/ 下的单文件
HTML（无 JS、无外链），每一步展示：时刻、动作、目标、通道、落点、结果、
（脱敏口径的）控件树摘要与截图哈希前缀。可随时重跑复现。

用法（仓库根目录）::

    python -I scripts/render_trace_demo.py ^
        --out docs/trace-replay-demo.html ^
        --trace "W3 计算器 17×23=391=runs/w3/calc/guard/trace.jsonl" ^
        --trace "W3 记事本读回=runs/w3/notepad/guard/trace.jsonl"
"""

from __future__ import annotations

import argparse
import html
import json
import sys
from datetime import datetime
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

_CSS = """
body{font-family:ui-monospace,Consolas,monospace;max-width:1100px;margin:2rem auto;
     padding:0 1rem;color:#1a1a2e;background:#fafafa;line-height:1.55}
h1{font-size:1.4rem;border-bottom:2px solid #4361ee;padding-bottom:.4rem}
h2{font-size:1.15rem;margin-top:2rem;background:#eef2ff;padding:.4rem .6rem;
   border-left:4px solid #4361ee}
table{border-collapse:collapse;width:100%;font-size:.82rem;margin:.6rem 0}
th,td{border:1px solid #d0d4dc;padding:.3rem .5rem;text-align:left;vertical-align:top}
th{background:#e9ecf2}
td.ok{color:#0a7d33} td.bad{color:#c1121f}
.meta{color:#555;font-size:.8rem}
.note{background:#fff8e1;border:1px solid #e0c96b;padding:.5rem .7rem;font-size:.82rem}
"""

_NOTE = (
    "本页由 <code>scripts/render_trace_demo.py</code> 从真实真机 trace（runs/，"
    "gitignored）渲染生成：每行是动作执行缝的一次执行记录——落点是物理像素，"
    "控件树摘要是脱敏口径，截图只记 SHA-256 不落图。数据可重跑复现，非手写。"
)


def _load(label_path: str) -> tuple[str, list[dict]]:
    # 标签里可能含「=」（如 17×23=391）——按**最后一个**等号切。
    label, _, path = label_path.rpartition("=")
    rows = [
        json.loads(line)
        for line in Path(path).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    return label.strip() or path, rows


def _denial_note(rows: list[dict]) -> str:
    """护栏拦截（denial 行）摘要——回放要如实展示「护栏在工作」。"""
    denials = [r for r in rows if r.get("type") == "denial"]
    if not denials:
        return ""
    by_guard: dict[str, int] = {}
    for d in denials:
        by_guard[d.get("guard", "?")] = by_guard.get(d.get("guard", "?"), 0) + 1
    detail = "；".join(f"{k}×{v}" for k, v in sorted(by_guard.items()))
    examples = "<br>".join(
        f"<span class='meta'>拦截：{html.escape(str(d.get('message', ''))[:110])}</span>"
        for d in denials[:3]
    )
    return (
        f"<p class='note'><strong>护栏拦截 {len(denials)} 次</strong>（{detail}，"
        f"全部未产生输入）：<br>{examples}</p>"
    )


def _cell(row: dict) -> str:
    result = row.get("result") or {}
    ok = result.get("ok")
    cls = "ok" if ok else "bad"
    status = "ok" if ok else f"fail:{result.get('error_code') or result.get('error_message') or '?'}"
    landing = row.get("landing_point")
    landing_text = f"({landing[0]}, {landing[1]})" if landing else "—"
    tree = row.get("tree") or []
    tree_text = "; ".join(
        f"{node.get('name', '') or '∅'}"
        for node in tree[:3]
    ) + (f"…(+{len(tree) - 3})" if len(tree) > 3 else "")
    sha = (row.get("screenshot_sha256") or "")[:12]
    ts = (row.get("ts") or "")[11:23]
    verification = row.get("verification")
    return (
        f"<td>{html.escape(str(row.get('step', '')))}</td>"
        f"<td>{html.escape(ts)}</td>"
        f"<td>{html.escape(str((row.get('action') or {}).get('kind') or ''))}</td>"
        f"<td>{html.escape(str((row.get('action') or {}).get('intent') or ''))}</td>"
        f"<td>{html.escape(str(row.get('target') or ''))}</td>"
        f"<td>{html.escape(str(row.get('channel') or ''))}</td>"
        f"<td>{landing_text}</td>"
        f"<td class='{cls}'>{html.escape(status)}</td>"
        f"<td>{row.get('model_calls', 0)}</td>"
        f"<td class='meta'>{html.escape(tree_text)}<br>sha:{sha}"
        f"{'<br>verify:' + html.escape(str(verification)) if verification else ''}</td>"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="trace.jsonl → 自包含 HTML 回放")
    parser.add_argument("--trace", action="append", required=True,
                        help="label=path/to/trace.jsonl（可多次）")
    parser.add_argument("--out", default="docs/trace-replay-demo.html")
    parser.add_argument("--title", default="minicode 真机 trace 回放演示")
    args = parser.parse_args()

    sections: list[tuple[str, list[dict]]] = [_load(t) for t in args.trace]

    parts = [
        "<!doctype html><meta charset='utf-8'>",
        f"<title>{html.escape(args.title)}</title><style>{_CSS}</style>",
        f"<h1>{html.escape(args.title)}</h1>",
        f"<p class='note'>{_NOTE}</p>",
        f"<p class='meta'>渲染时间：{datetime.now().strftime('%Y-%m-%d %H:%M')}</p>",
    ]
    for label, rows in sections:
        steps = [r for r in rows if r.get("type") == "step"]
        ok_count = sum(1 for r in steps if (r.get("result") or {}).get("ok"))
        parts.append(
            f"<h2>{html.escape(label)}</h2>"
            f"<p class='meta'>{len(steps)} 步 · {ok_count} ok · "
            f"capture {steps[0].get('capture_size') if steps else '—'}</p>"
        )
        parts.append(_denial_note(rows))
        parts.append(
            "<table><tr><th>#</th><th>时刻(UTC)</th><th>动作</th><th>意图</th>"
            "<th>目标</th><th>通道</th><th>落点(px)</th><th>结果</th>"
            "<th>模型调用</th><th>树摘要 / 截图哈希</th></tr>"
            + "".join(f"<tr>{_cell(r)}</tr>" for r in steps)
            + "</table>"
        )
    out_path = _ROOT / args.out
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(parts) + "\n", encoding="utf-8")
    print(f"已写 {out_path}（{len(sections)} 段 trace）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

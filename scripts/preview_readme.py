#!/usr/bin/env python3
"""把 README.md 渲染成接近 GitHub 阅读样式的本地预览页，用于截图闭环。

用法：
    python scripts/preview_readme.py README.md out.html
然后用无头浏览器对 out.html 截图即可。
"""

from __future__ import annotations

import sys
from pathlib import Path

import markdown

CSS = """
* { box-sizing: border-box; }
body { margin:0; background:#fff; color:#1f2328;
  font-family:-apple-system,"Segoe UI","Noto Sans","Microsoft YaHei",Helvetica,Arial,sans-serif;
  font-size:16px; line-height:1.5; }
.wrap { max-width:1012px; margin:0 auto; padding:32px 32px 96px; }
.article { max-width:880px; margin:0 auto; }
h1 { font-size:2em; font-weight:600; margin:.67em 0; padding-bottom:.3em; border-bottom:1px solid #d1d9e0; }
h2 { font-size:1.5em; font-weight:600; margin:24px 0 16px; padding-bottom:.3em; border-bottom:1px solid #d1d9e0; }
h3 { font-size:1.25em; font-weight:600; margin:24px 0 16px; }
p { margin:0 0 16px; }
a { color:#0969da; text-decoration:none; }
ul, ol { margin:0 0 16px; padding-left:2em; }
li { margin:.25em 0; }
img { max-width:100%; border:0; }
table { border-collapse:collapse; margin:0 0 16px; display:block; overflow:auto; }
table th, table td { border:1px solid #d1d9e0; padding:6px 13px; }
table th { background:#f6f8fa; font-weight:600; }
table tr:nth-child(2n) td { background:#f6f8fa55; }
code { background:#eff1f3; padding:.2em .4em; border-radius:6px; font-size:85%;
  font-family:ui-monospace,SFMono-Regular,Consolas,"Liberation Mono",Menlo,monospace; }
pre { background:#f6f8fa; padding:16px; border-radius:6px; overflow:auto; margin:0 0 16px; }
pre code { background:transparent; padding:0; font-size:85%; }
details { margin:0 0 16px; }
summary { cursor:pointer; font-weight:600; }
blockquote { margin:0 0 16px; padding:0 1em; color:#59636e; border-left:.25em solid #d1d9e0; }
hr { border:0; border-top:1px solid #d1d9e0; margin:24px 0; }
.center { text-align:center; }
"""


def render(src: Path, dst: Path) -> None:
    text = src.read_text(encoding="utf-8")
    # GitHub 会把 <div align="center"> 之后（空行隔开）的 markdown 正常渲染并放进 div 内；
    # python-markdown 需要 markdown="1" 才等价，这里做预览侧的等价替换。
    text = text.replace('<div align="center">', '<div align="center" markdown="1">')
    html = markdown.markdown(
        text,
        extensions=["tables", "fenced_code", "md_in_html", "sane_lists"],
        output_format="html5",
    )
    page = f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<base href="{src.parent.resolve().as_uri()}/">
<title>README preview</title><style>{CSS}</style></head>
<body><div class="wrap"><div class="article">
{html}
</div></div></body></html>
"""
    dst.write_text(page, encoding="utf-8")
    print(f"wrote {dst} ({len(page) // 1024} KB)")


if __name__ == "__main__":
    src = Path(sys.argv[1] if len(sys.argv) > 1 else "README.md")
    dst = Path(sys.argv[2] if len(sys.argv) > 2 else "preview.html")
    render(src, dst)

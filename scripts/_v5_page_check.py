"""V5 页面生成自检（诊断脚本，不入交付）。"""
import sys
from pathlib import Path

sys.path.insert(0, "scripts")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import v5_vision_tasks as v  # noqa: E402

pages = v._write_pages(Path("runs/v5"))
print("pages:", sorted(p.name for p in pages.values()))
html = pages["canvas-text-buttons"].read_text(encoding="utf-8")
print("has_regions_js=", "REGIONS" in html)
print("has_hit_prefix=", "V5HIT:" in html)
print("has_delete_button=", "删除" in html)
print("has_click_handler=", "addEventListener" in html)

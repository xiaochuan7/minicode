"""V5 页面结构干燥检查（诊断脚本）。"""
import sys

sys.path.insert(0, "scripts")
sys.path.insert(0, "src")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import v5_vision_tasks as v  # noqa: E402

print("import ok")
for name, page in v._PAGES.items():
    regions, draws = v._page_regions_and_draws(page)
    print(name, "regions=", len(regions), "tasks=", len(page["tasks"]))
    for r in regions:
        assert r["x2"] > r["x1"] and r["y2"] > r["y1"], r
print("all pages ok")

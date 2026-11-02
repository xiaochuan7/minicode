"""V3 三源覆盖率摘要（诊断脚本）。"""
import json
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
j = json.load(open("runs/v3/icon-coverage.json", encoding="utf-8"))
print("sources:", list(j["summary"].keys()))
for name, s in j["summary"].items():
    icon = s.get("icon", {})
    print(
        f"{name}: icon覆盖 IoU@0.3={icon.get('coverage_iou_0_3')} "
        f"@0.5={icon.get('coverage_iou_0_5')} center={icon.get('coverage_center_inside')} "
        f"(n={icon.get('samples')}, 均值候选={icon.get('candidates_mean')}, {icon.get('elapsed_s_mean')}s/图)"
    )

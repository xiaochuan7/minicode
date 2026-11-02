"""ScreenParser 权重下载与推理探针（诊断脚本，不入交付）。"""
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
from ultralytics import YOLO  # noqa: E402

model = YOLO("docling-project/ScreenParser")
print("model loaded", flush=True)
from PIL import Image  # noqa: E402

img = Image.new("RGB", (640, 480), (240, 240, 240))
for x in range(50, 200):
    for y in range(50, 120):
        img.putpixel((x, y), (40, 40, 40))
results = model.predict(img, conf=0.25, device="cpu", verbose=False)
for r in results:
    print("classes_sample:", dict(list(r.names.items())[:12]))
    print("boxes:", len(r.boxes))
print("PROBE OK")

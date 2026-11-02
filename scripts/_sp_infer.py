"""ScreenParser 推理探针（诊断脚本，不入交付）：类名表 + 一张真实截图的检出。"""
import os
import sys

os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "src"))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from minicode.eval.png_read import read_png_frame  # noqa: E402
from minicode.model.detector_source import ScreenParserCandidateSource  # noqa: E402

if len(sys.argv) > 1:
    image_path = sys.argv[1]
else:
    # 默认拿 W6 批的第一条（cmd 传中文路径会被码页毁掉 —— 路径进脚本不进 argv）。
    from pathlib import Path  # noqa: E402

    from minicode.eval.sspro import load_samples, stratified_subset  # noqa: E402

    samples = load_samples(Path(r"C:\Users\22197\Desktop\ik评测\sspro-dataset"))
    subset = stratified_subset(samples, 10, seed=20251020)
    image_path = str(subset[0].image_path)
frame = read_png_frame(image_path)
source = ScreenParserCandidateSource(max_candidates=25)
import time  # noqa: E402

started = time.time()
candidates = source.candidates(frame, "任意目标")
print(f"检出 {len(candidates)} 个候选（{time.time() - started:.1f}s）")
for candidate in candidates[:15]:
    print("  ", candidate.number, candidate.text, candidate.box)

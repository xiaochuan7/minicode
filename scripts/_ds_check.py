"""数据集路径自检（诊断脚本）。"""
import sys
from pathlib import Path

sys.path.insert(0, "src")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
from minicode.eval.sspro import load_samples  # noqa: E402

samples = load_samples(Path(r"C:\Users\22197\Desktop\ik评测\sspro-dataset"))
print(len(samples), samples[0].sample_id, samples[0].image_path)

"""W6 数据准备：分层结构盘点 + 需要的图片清单（临时脚本，跑完删）。"""
from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from minicode.eval.sspro import load_samples, stratified_subset  # noqa: E402

DATA = Path(r"C:\Users\22197\Desktop\ik评测\sspro-dataset")


def main() -> int:
    samples = load_samples(DATA)
    print(f"全量样本: {len(samples)}")
    strata = Counter((s.platform, s.ui_type) for s in samples)
    for key, count in sorted(strata.items()):
        print(f"  {key}: {count}")
    for per in (10, 15):
        subset = stratified_subset(samples, per)
        images = {s.image_path.name for s in subset}
        print(f"per_stratum={per}: {len(subset)} 条样本, {len(images)} 张唯一图")
    subset = stratified_subset(samples, 10)
    images = sorted({s.image_path.name for s in subset})
    (ROOT / ".scratch" / "w6_images.txt").write_text(
        "\n".join(images), encoding="utf-8"
    )
    sizes = Counter(s.image_size for s in subset)
    print("图片尺寸分布:", dict(sizes))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

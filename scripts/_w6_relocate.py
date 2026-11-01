"""把平铺的已下载图归位到 images\\<分组>\\ 子目录（临时脚本）。

侦察 B 发现：标注的 img_filename 带「分组/」前缀，load_samples 按官方布局
拼路径；而 .scratch/w6_images.txt 只有 basename，早期下载因此平铺。
本脚本用标注建立 basename→分组 映射，把已有图挪到官方布局，并把
w6_images.txt 重写为「分组/文件名」行（下载脚本原样支持子目录目标）。
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "src"))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

DATA = Path(r"C:\Users\22197\Desktop\ik评测\sspro-dataset")


def main() -> int:
    from minicode.eval.sspro import load_samples

    samples = load_samples(DATA)
    by_basename: dict[str, str] = {}
    for sample in samples:
        rel = sample.image_path.parent.name + "/" + sample.image_path.name
        by_basename[sample.image_path.name] = rel

    plan_lines = [
        line.strip()
        for line in (_ROOT / ".scratch" / "w6_images.txt")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
    ]
    # 计划的 60 条样本 → 官方相对路径（分组/文件名）。
    planned_rel = []
    missing_key = []
    for basename in plan_lines:
        rel = by_basename.get(basename)
        if rel is None:
            missing_key.append(basename)
        else:
            planned_rel.append(rel)
    if missing_key:
        print(f"⚠️ {len(missing_key)} 个 basename 在标注里找不到（不动它们）：")
        for name in missing_key:
            print(f"   {name}")

    moved = 0
    for rel in planned_rel:
        dest = DATA / "images" / rel
        flat = DATA / "images" / Path(rel).name
        if dest.exists() and dest.stat().st_size > 10000:
            continue
        if flat.exists() and flat.stat().st_size > 10000:
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(flat), str(dest))
            moved += 1
    print(f"moved={moved}（计划 {len(planned_rel)} 张）")

    # 重写计划文件为官方相对路径（下载脚本按 images/<行> 落盘）。
    (_ROOT / ".scratch" / "w6_images.txt").write_text(
        "\n".join(planned_rel) + "\n", encoding="utf-8"
    )
    print(f"w6_images.txt 已重写为 {len(planned_rel)} 行（分组/文件名）")
    return 0 if not missing_key else 2


if __name__ == "__main__":
    raise SystemExit(main())

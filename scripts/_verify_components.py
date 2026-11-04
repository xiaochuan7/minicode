"""对照验证：新连通域（两趟并查集）vs 旧 BFS —— 随机 mask 上逐块等价。

旧 BFS 逻辑内嵌重写（不 import 改后的实现，避免「自己比自己」）。
跑完即删。
"""

from __future__ import annotations

import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

import numpy as np

from minicode.model.icon_proposals import _connected_components


def _old_bfs(mask):
    height, width = mask.shape
    visited = np.zeros_like(mask, dtype=bool)
    components = []
    for start_y, start_x in zip(*np.nonzero(mask), strict=True):
        if visited[start_y, start_x]:
            continue
        stack = [(int(start_y), int(start_x))]
        visited[start_y, start_x] = True
        left = right = int(start_x)
        top = bottom = int(start_y)
        area = 0
        while stack:
            y, x = stack.pop()
            area += 1
            left = min(left, x)
            right = max(right, x)
            top = min(top, y)
            bottom = max(bottom, y)
            for ny, nx in ((y - 1, x), (y + 1, x), (y, x - 1), (y, x + 1)):
                if (
                    0 <= ny < height
                    and 0 <= nx < width
                    and mask[ny, nx]
                    and not visited[ny, nx]
                ):
                    visited[ny, nx] = True
                    stack.append((ny, nx))
        components.append((left, top, right + 1, bottom + 1, area))
    return sorted(components)


def _norm(components):
    return sorted(components)


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    rng = np.random.default_rng(42)
    # 多组随机密度 + 形状（含细长条、孤立点、全空、全满）
    cases = [
        ("empty", np.zeros((50, 80), dtype=bool)),
        ("full", np.ones((50, 80), dtype=bool)),
        ("single", np.pad(np.ones((1, 1), dtype=bool), ((10, 0), (20, 0)))),
        ("l_shape", None),
        ("diagonal", None),
    ]
    l_shape = np.zeros((10, 10), dtype=bool)
    l_shape[:, 0] = True
    l_shape[9, :] = True
    cases[3] = ("l_shape", l_shape)
    diag = np.zeros((10, 10), dtype=bool)
    for i in range(10):
        diag[i, i] = True
    cases[4] = ("diagonal", diag)
    for density in (0.05, 0.15, 0.35, 0.6):
        for size in ((40, 60), (120, 200)):
            cases.append(
                (f"rand_d{density}_{size[0]}x{size[1]}",
                 rng.random(size) < density)
            )
    ok = True
    for name, mask in cases:
        old = _norm(_old_bfs(mask))
        new = _norm(_connected_components(mask))
        status = "OK " if old == new else "DIFF"
        if old != new:
            ok = False
            print(f"[{status}] {name}: old {len(old)} vs new {len(new)}")
            for a, b in zip(old, new, strict=False):
                if a != b:
                    print("   old", a, "new", b)
        else:
            print(f"[{status}] {name}: {len(old)} components")
    # 2000 行真实尺寸的性能参考
    big = rng.random((1260, 2016)) < 0.12
    begin = time.time()
    comps = _connected_components(big)
    elapsed = time.time() - begin
    print(f"[perf] 1260x2016 d=0.12: {len(comps)} components in {elapsed:.2f}s")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

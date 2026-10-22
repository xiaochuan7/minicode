"""**采样池**（施工清单 T1）与**空间聚类**（T4 的第一步）。

## 这层解决什么

模型端**不确定**：同一条提示词两次问，答案可能不同。本机实测**同图同提示词
10 次连发，原文一致率只有 10%**【一手·本机 ``runs/m2-temperature-probe/probe.json``】
→ **「同提示词重发 K 次」拿不到独立样本**，采样必须是**换了窗口或换了措辞**的。

所以这一层提供两样东西：

1. **措辞变体**（:data:`COARSE_PROMPT_VARIANTS` / :data:`REFINE_PROMPT_VARIANTS`）——
   同一个契约、**换着问**；变体 0 的提示词与改造前**逐字相同**（默认值不变）。
2. **窗口变体**（:func:`refine_windows`）—— crop 内 N 个**固定偏移子区域**，
   每个子区域**单独问一次** → 空间上互相独立。

**采样池**（:class:`ResponsePool`）把每次采样的**原文与落点全部落盘**，
并让**所有臂从同一份池子取**（否则「臂间差异」里混着模型随机性）。

## 依据

* MVP Algorithm 2（**贪心聚类取最密簇质心**）【一手·公开
  https://arxiv.org/abs/2512.08529 ｜ https://github.com/ZJUSCL/MVP】；
* UI-Zoomer 的 N=8 是「门控/方差用途」的甜点【一手·公开
  https://arxiv.org/abs/2604.14113】；
* GUI-RC 的 K=64 是「当最终答案」的用法【一手·公开 https://arxiv.org/abs/2508.05615】。
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

__all__ = [
    "COARSE_PROMPT_VARIANTS",
    "REFINE_PROMPT_VARIANTS",
    "Cluster",
    "PooledAnswer",
    "ResponsePool",
    "cluster_candidates",
    "refine_windows",
]

#: **粗定位那一级的措辞变体**（K=8）。
#:
#: ⚠️ **变体 0 必须是改造前那句**（逐字相同）—— 默认行为不许变，
#: 既有单测与旧缓存都靠它。
#:
#: 变的只是**问法**：同一个契约（外接矩形 + 自报文字 + 负出口），
#: 换八种说法 → 八次采样互相独立（不同措辞 → 不同注意力分布）。
COARSE_PROMPT_VARIANTS: tuple[str, ...] = (
    "请在图里找到与下面这个指令对应的界面元素，"
    "并给出它的外接矩形（bounding box）。",
    "请在图里定位下面这个指令所指的界面元素，"
    "给出它的外接矩形（bounding box）。",
    "下面这个指令说的是图里的哪个界面元素？请给出那个元素的外接矩形（bounding box）。",
    "请找出图里满足下面这条指令的控件，并给出它的外接矩形（bounding box）。",
    "请在图里找出下面这个指令对应的那个元素"
    "（不要选长得相似的其它元素），给出它的外接矩形（bounding box）。",
    "请在图里找出下面这条指令所描述的界面元素，并给出它的外接矩形（bounding box）。",
    "按下面这条指令在图里找出目标界面元素，标出它的外接矩形（bounding box）。",
    "下面这个指令对应的界面元素在图里的哪个位置？请给出它的外接矩形（bounding box）。",
)

#: **精定位那一级的措辞变体**（K=8）。
#:
#: ⚠️ **变体 0 必须是改造前那句**（逐字相同）。
REFINE_PROMPT_VARIANTS: tuple[str, ...] = (
    "请给出这张图里名为「{desc}」的元素【中心】的位置。",
    "请指出这张图里「{desc}」这个元素【中心】的像素位置。",
    "这张图里「{desc}」在哪里？请给出它【中心】的位置。",
    "请在这张图里找到「{desc}」，并给出它【中心】的位置。",
    "在下面这张图里，「{desc}」这个元素【中心】的坐标是多少？",
    "请给出这张图里那个叫「{desc}」的元素【中心】的坐标。",
    "这张图里的「{desc}」是哪个位置？请给出它【中心】的坐标。",
    "请在这张图里标出「{desc}」的位置（给出它【中心】的坐标）。",
)

#: **空间聚类的半径**（**物理像素**）。
#:
#: ⚠️ **它必须定义在物理空间**（不是 crop 内的像素）—— 放大之后
#: crop 内的像素尺度变了，同一个 τ 在 1536 / 2016 / 放大三档下
#: 行为会不同（施工清单 §8.2 第 3 条）。
#:
#: 取值依据：MVP 的 τ=14 px 是**另一套刻度**（模型输入空间）→ 换算到本仓的
#: 物理尺度取 **32 px**（施工清单 §六 6.3 定的默认值）。
CLUSTER_TAU_PX = 32.0


def refine_windows(
    width: int, height: int, count: int = 8
) -> list[tuple[int, int, int, int]]:
    """crop 内 ``count`` 个**固定偏移子区域**（``(左, 上, 右, 下)``，图内像素）。

    * 子区域**保持宽高比**（各子区域的高宽比 = crop 的高宽比）——
      ScreenSpot-Pro 含多屏拼接全景图，**固定比例会拉伸变形**；
    * 子区域**互相重叠**（相邻错开半个窗口）→ 目标通常落在 **2~3 个**窗口里
      （不是只落在一个）→ 聚类时「对的那个簇」才有**不止一个成员**；
    * **并起来盖住整个 crop**（每个像素至少落在一个窗口里）。
    """
    if width <= 0 or height <= 0:
        raise ValueError(f"crop 尺寸必须为正数，收到 {width}x{height}")
    if count <= 0:
        raise ValueError(f"窗口数必须为正数，收到 {count}")
    if count == 1:
        return [(0, 0, width, height)]
    #: 列多行少（crop 通常比屏幕扁）—— 列 = ceil(sqrt(count * width / height))。
    columns = max(1, round(math.sqrt(count * width / max(1, height))))
    columns = min(columns, count)
    rows = math.ceil(count / columns)
    window_w = max(1, width // 2)
    window_h = max(1, height // 2)
    step_x = (width - window_w) // max(1, columns - 1) if columns > 1 else 0
    step_y = (height - window_h) // max(1, rows - 1) if rows > 1 else 0
    windows: list[tuple[int, int, int, int]] = []
    for index in range(count):
        column = index % columns
        row = index // columns
        left = min(column * step_x, width - window_w)
        top = min(row * step_y, height - window_h)
        windows.append((left, top, left + window_w, top + window_h))
    return windows


@dataclass(frozen=True)
class Cluster:
    """一个**空间簇**（物理像素口径）。

    * ``centroid`` —— 簇内成员的**质心**（物理像素）；
    * ``size`` —— 成员数；
    * ``ratio`` —— ``size / 全部候选数``（**簇占比**，落盘字段
      ``refine_cluster_ratio`` 用的是最密簇的那一个）。
    """

    centroid: tuple[float, float]
    size: int
    ratio: float = 0.0


def cluster_candidates(
    points: list[tuple[float, float]], tau_px: float = CLUSTER_TAU_PX
) -> list[Cluster]:
    """**贪心空间聚类**（MVP Algorithm 2）—— 按簇大小降序返回。

    * 逐个点走一遍：落进**第一个**「质心在 ``tau_px`` 内」的簇；否则**新开一簇**；
    * 返回**按簇大小降序**的簇列表（第一个就是**最密簇**）。

    ⚠️ ``tau_px`` 是**物理像素** —— 放大之后 crop 内的像素尺度变了，
    聚类必须留在物理空间里做（施工清单 §8.2 第 3 条）。
    """
    if not points:
        return []
    clusters: list[list[tuple[float, float]]] = []
    for point in points:
        for cluster in clusters:
            cx = sum(item[0] for item in cluster) / len(cluster)
            cy = sum(item[1] for item in cluster) / len(cluster)
            if math.hypot(point[0] - cx, point[1] - cy) <= tau_px:
                cluster.append(point)
                break
        else:
            clusters.append([point])
    total = float(len(points))
    result = [
        Cluster(
            centroid=(
                sum(item[0] for item in cluster) / len(cluster),
                sum(item[1] for item in cluster) / len(cluster),
            ),
            size=len(cluster),
            ratio=len(cluster) / total,
        )
        for cluster in clusters
    ]
    result.sort(key=lambda item: (-item.size, item.centroid[0], item.centroid[1]))
    return result


@dataclass(frozen=True)
class PooledAnswer:
    """**一次采样**的原文与落点（全部落盘）。

    * ``stage`` —— ``"coarse"`` / ``"refine"``；
    * ``variant`` —— 第几次采样（0 起）；
    * ``key`` —— **样本键**（评测台里是 ``sample_id``；真机里是画面的哈希）；
    * ``prompt`` —— **逐字的提示词**（重放即零随机性差）；
    * ``raw_text`` —— **模型的原文**（不解析、不改写）；
    * ``point`` —— 解析出来的落点（**图内坐标**；没有就是 ``None``）；
    * ``window`` —— 那次采样用的**窗口**（子区域，图内像素；整屏就是空）。
    """

    stage: str
    variant: int
    key: str
    prompt: str
    raw_text: str
    point: tuple[float, float] | None = None
    window: tuple[int, int, int, int] | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "stage": self.stage,
            "variant": self.variant,
            "key": self.key,
            "prompt": self.prompt,
            "raw_text": self.raw_text,
            "point": list(self.point) if self.point else None,
            "window": list(self.window) if self.window else None,
            "extra": dict(self.extra),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PooledAnswer:
        point = data.get("point")
        window = data.get("window")
        return cls(
            stage=str(data.get("stage", "")),
            variant=int(data.get("variant", 0)),
            key=str(data.get("key", "")),
            prompt=str(data.get("prompt", "")),
            raw_text=str(data.get("raw_text", "")),
            point=(float(point[0]), float(point[1])) if point else None,
            window=(
                (int(window[0]), int(window[1]), int(window[2]), int(window[3]))
                if window
                else None
            ),
            extra=dict(data.get("extra") or {}),
        )


class ResponsePool:
    """**采样池** —— 每次采样的原文与落点都落在这里。

    * **所有臂从同一份池子取**（否则臂间差异里混着模型随机性）；
    * 池子**可落盘、可读回**（JSONL，一行一条）→ 重放即零随机性差。
    """

    def __init__(self, path: Path | str | None = None):
        self.path = Path(path) if path else None
        self._entries: list[PooledAnswer] = []
        self._by_key: dict[tuple[str, str, int], PooledAnswer] = {}

    def record(
        self,
        *,
        stage: str,
        variant: int,
        key: str,
        prompt: str,
        raw_text: str,
        point: tuple[float, float] | None = None,
        window: tuple[int, int, int, int] | None = None,
        extra: dict[str, Any] | None = None,
    ) -> PooledAnswer:
        """记一条采样。**同一个键只记一次**（重放时先查池子 → 不重复问模型）。"""
        cache_key = (stage, key, variant)
        existing = self._by_key.get(cache_key)
        if existing is not None:
            return existing
        entry = PooledAnswer(
            stage=stage,
            variant=variant,
            key=key,
            prompt=prompt,
            raw_text=raw_text,
            point=point,
            window=window,
            extra=dict(extra or {}),
        )
        self._entries.append(entry)
        self._by_key[cache_key] = entry
        return entry

    def get(self, stage: str, key: str, variant: int) -> PooledAnswer | None:
        """池子里有没有这一条（**重放**用）。"""
        return self._by_key.get((stage, key, variant))

    def entries(self, stage: str = "", key: str = "") -> list[PooledAnswer]:
        """按阶段 / 样本键过滤。"""
        return [
            entry
            for entry in self._entries
            if (not stage or entry.stage == stage) and (not key or entry.key == key)
        ]

    def to_dict(self) -> dict[str, Any]:
        return {
            "samples": len(self._entries),
            "entries": [entry.to_dict() for entry in self._entries],
        }

    def save(self, path: Path | str | None = None) -> Path:
        """落盘（JSONL，一行一条）。"""
        target = Path(path) if path else self.path
        if target is None:
            raise ValueError("没有指定落盘路径")
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("w", encoding="utf-8") as handle:
            for entry in self._entries:
                handle.write(json.dumps(entry.to_dict(), ensure_ascii=False) + "\n")
        return target

    @classmethod
    def load(cls, path: Path | str) -> ResponsePool:
        """读回一个池子（**重放即零随机性差**）。"""
        pool = cls(path)
        for line in Path(path).read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            data = json.loads(line)
            entry = PooledAnswer.from_dict(data)
            pool._entries.append(entry)
            pool._by_key[(entry.stage, entry.key, entry.variant)] = entry
        return pool

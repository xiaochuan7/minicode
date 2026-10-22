"""**ScreenSpot-Pro 门控评测**（工单 M8）。

这张表要证明一件事：**门控记录器不低于无条件两段式**（降级后两者**行为完全
相同**，落点应当逐条相同 —— 那是体检项）。
判据只判我们自己的行 —— 官方 5 个 coarse-to-fine 方法那一行行是
**官方自报**（未在同模型下重跑，**不作硬对比**）。

## 口径（不许混用）

* **内部一律原图像素**：GT bbox 与预测落点都在这张图的像素空间里比；
* **出口按目标脚本转一次**：ScreenSpot-Pro 官方脚本要的是**归一化 [0,1] 的点**
  与**归一化后的 bbox**（`eval_screenspot_pro.py::eval_sample_positive_gt`），
  转换在 :data:`minicode.eval.formats.SCREENSPOT_PRO` 这一个地方做
  （工单 M7 的口径纪律）；
* **模型侧量纲**（0~1 / 0~999 / 0~1000 / 绝对像素）**只来自运行时标定**，
  本模块**不碰**它（本仓自有代码里不出现把 999/1000 当量纲分母的写法）。

## 三臂的差别

* **门控记录器**（``on_demand``）：粗定位 → crop 精定位；门控**只落盘**，
  **不再决定开不开**（2026-10-21 拍板：门控降级为记录器，生产默认无条件精定位）；
* **无条件两段式**（``always``）：粗定位 → crop 精定位（**生产默认**）；
* **只用粗定位**（``coarse_only``）：粗定位之后**直接返回**
  （落点 = 粗点、**1.00 次模型调用/目标**）—— **从来没量过的基线臂**。

**同一批样本、同一模型、同一裁剪参数、同一判定口径**。

## 门控输入的诚实说明

* **目标占比**：**可判定** —— 粗定位**出框**（`docs/adr/0006-crop-local-coordinate-contract.md`
  的补充条目：人已拍板允许出框出点、落点取框中心），用框推出半径 → 占比第一次可判
  （模型只给点不给框时仍**不可判定**）；
* **置信度**：**判不了** —— 模型不报置信度；
* **粗定位是否有效**：**判得了**（模型自己的输出）；
* **两次粗定位是否一致**：**永远判不了** —— 门控降级为记录器之后，
  「第二次独立粗定位」那次调用**被删掉了**（实测它正是 −19.0 pp 的来源），
  判据留着、端口不再注入 → 每次都照实记成**不可判定**。

因此本评测台上**可判定**的是 **COARSE_INVALID** 与 **TARGET_TINY** 两条，
另外两条（**置信度**、**一致性**）记为**不可判定**（显式落盘，不静默）。
"""

from __future__ import annotations

import hashlib
import json
import random
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from minicode.action.ports import CapturedFrame
from minicode.eval.formats import SCREENSPOT_PRO, EvalSample
from minicode.ground.gate import (
    GateMode,
    GateReport,
    GateSample,
    build_gate_report,
    default_gate_params,
)
from minicode.model.effort import Effort
from minicode.model.ports import ModelCaller

from .png_read import read_png_frame

__all__ = [
    "ArmOutcome",
    "SsproSample",
    "compare_arms",
    "load_samples",
    "near_miss_calibration",
    "paired_comparison",
    "prompt_stage",
    "run_arms",
    "stratified_subset",
    "summarize",
    "table_rows",
]


@dataclass(frozen=True)
class SsproSample:
    """一条 ScreenSpot-Pro 样本（GT 全部是**原图像素**）。"""

    sample_id: str
    image_path: Path
    #: GT bbox，``[x1, y1, x2, y2]``（原图像素）。
    bbox: tuple[int, int, int, int]
    image_size: tuple[int, int]
    instruction: str
    #: ``"text"`` / ``"icon"``（官方脚本的 ``ui_type`` 切分）。
    ui_type: str
    platform: str
    group: str
    application: str

    def as_eval_sample(self) -> EvalSample:
        """转成评测口径的样本（GT bbox + 图像尺寸）。"""
        return EvalSample(bbox=self.bbox, image_size=self.image_size)


def load_samples(dataset_root: Path) -> list[SsproSample]:
    """读 ScreenSpot-Pro 的标注目录 → 样本清单。

    ``dataset_root`` 是数据集根（里面有 ``annotations/`` 与 ``images/``）。
    字段与官方脚本一致（``eval_screenspot_pro.py`` 里读的正是这些键）。
    """
    annotations = Path(dataset_root) / "annotations"
    images = Path(dataset_root) / "images"
    samples: list[SsproSample] = []
    for path in sorted(annotations.glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        for item in payload:
            width, height = (int(value) for value in item["img_size"])
            x1, y1, x2, y2 = (int(value) for value in item["bbox"])
            samples.append(
                SsproSample(
                    sample_id=str(item["id"]),
                    image_path=images / item["img_filename"],
                    bbox=(x1, y1, x2, y2),
                    image_size=(width, height),
                    instruction=str(item["instruction"]),
                    ui_type=str(item["ui_type"]),
                    platform=str(item["platform"]),
                    group=str(item["group"]),
                    application=str(item["application"]),
                )
            )
    return samples


def stratified_subset(
    samples: Sequence[SsproSample], per_stratum: int, *, seed: int = 20251020
) -> list[SsproSample]:
    """**分层抽样**：按 (平台 × 文本/图标) 分层，每层取固定条数。

    抽样是**确定的**（同一个种子 → 同一批样本）—— 报告里的 N 与分数
    必须来自同一批样本，重跑要能复现。
    """
    buckets: dict[tuple[str, str], list[SsproSample]] = {}
    for sample in samples:
        buckets.setdefault((sample.platform, sample.ui_type), []).append(sample)
    chosen: list[SsproSample] = []
    for key in sorted(buckets):
        bucket = sorted(buckets[key], key=lambda item: item.sample_id)
        random.Random(f"{seed}:{key}").shuffle(bucket)
        chosen.extend(bucket[:per_stratum])
    return sorted(chosen, key=lambda item: item.sample_id)


#: **官方 5 行**（ScreenSpot-Pro 官方仓的 5 个 coarse-to-fine 实现）。
#:
#: 分数一律标「**官方自报，仅供观感，不作硬对比**」—— 我们**没有在同模型下
#: 重跑**它们（官方方法要 OS-Atlas-7B 权重 + vLLM 服务，本机跑不了；
#: 6 GB 显存也放不下 7B BF16）。**硬判据只判我们自己的两行。**
#:
#: 分数出处：论文 Table 4（arXiv:2504.07981v1，基准模型 OS-Atlas-7B）。
#: 官方仓代码出处：``models/methods/*.py``、``models/zoom_consistency_router.py``。
OFFICIAL_ROWS: tuple[dict[str, str], ...] = (
    {
        "method": "ReGround（官方仓 methods/reground.py）",
        "model": "OS-Atlas-7B（OS-Copilot/OS-Atlas-Base-7B）",
        "dataset": "ScreenSpot-Pro 全量 1581 条",
        "crop": "crop 1024×1024（reground.py:151），整屏定位 + crop 内再定位各 1 次",
        "score": "40.2",
        "source": (
            "官方自报，仅供观感，不作硬对比（论文 Table 4，arXiv:2504.07981v1）；"
            "判定口径：官方 eval_screenspot_pro.py（归一化点落在归一化 bbox 内）"
        ),
    },
    {
        "method": "Iterative Narrowing（官方仓 methods/iterative_narrowing.py）",
        "model": "OS-Atlas-7B（OS-Copilot/OS-Atlas-Base-7B）",
        "dataset": "ScreenSpot-Pro 全量 1581 条",
        "crop": "max_search_depth=3，以预测为中心 ±0.25 归一化裁剪（iterative_narrowing.py:189-192）",
        "score": "31.9",
        "source": (
            "官方自报，仅供观感，不作硬对比（论文 Table 4，arXiv:2504.07981v1）；"
            "判定口径：官方 eval_screenspot_pro.py"
        ),
    },
    {
        "method": "Iterative Focusing（官方仓 methods/iterative_focusing.py）",
        "model": "OS-Atlas-7B（OS-Copilot/OS-Atlas-Base-7B）",
        "dataset": "ScreenSpot-Pro 全量 1581 条",
        "crop": "max_search_depth=3，2×2 象限切分取含预测的象限（iterative_focusing.py:156-161）",
        "score": "31.0",
        "source": (
            "官方自报，仅供观感，不作硬对比（论文 Table 4，arXiv:2504.07981v1；"
            "论文 §4.1 把同一方法称作 Iterative Zooming，对应关系为**推断**）；"
            "判定口径：官方 eval_screenspot_pro.py"
        ),
    },
    {
        "method": "ScreenSeekeR（官方仓 methods/screenseeker.py）",
        "model": "OS-Atlas-7B + planner gpt-4o（screenseeker.py:456）",
        "dataset": "ScreenSpot-Pro 全量 1581 条",
        "crop": "min_crop_size=[1280,768]、max_aspect_ratio=2.5（screenseeker.py:461-467）",
        "score": "48.1",
        "source": (
            "官方自报，仅供观感，不作硬对比（论文 Table 4，arXiv:2504.07981v1）；"
            "判定口径：官方 eval_screenspot_pro.py"
        ),
    },
    {
        "method": "Zoom Consistency Router（官方仓 models/zoom_consistency_router.py）",
        "model": "KV-Ground-8B + Qwen3.5-27B-AWQ（第三方 PR，非论文基线）",
        "dataset": "ScreenSpot-Pro（官方脚本口径）",
        "crop": "CROP_RATIO=0.5，2 步 zoom（zoom_consistency_router.py:43,180-203）",
        "score": "未取得",
        "source": (
            "**论文未评测该方法**（论文只有四个 coarse-to-fine 方法）；"
            "官方仓里它是第三方 PR，其数字只出现在 commit message 里，**未核实故不引用**；"
            "本机无 27B 权重，未在同模型下重跑 —— 仅供观感，不作硬对比"
        ),
    },
)


@dataclass(frozen=True)
class ArmOutcome:
    """一条样本在某一臂上的结果。"""

    sample_id: str
    ui_type: str
    platform: str
    group: str
    application: str
    hit: bool
    landing_point: tuple[int, int] | None
    coarse_point: tuple[float, float] | None
    gate_hit: bool
    gate_reasons: tuple[str, ...]
    gate_undecidable: tuple[str, ...]
    refine_enabled: bool
    fallback: bool
    fallback_cause: str
    model_calls: int
    error: str = ""
    # -- 施工清单第 ① 条埋点（8 个字段）---------------------------------
    #: **crop 窗口有没有完整含住 GT 框**（``None`` = 这一臂没开过窗）。
    crop_contains_target: bool | None = None
    #: **没含住的原因**：``out-of-frame`` / ``coarse-picked-wrong`` /
    #: ``out-of-bbox``；含住了就是空串。
    crop_miss_reason: str = ""
    #: 精定位落点离 **crop 窗口中心**多远（历史口径，留着做对比）。
    deviation_px_crop_center: float | None = None
    #: 精定位落点离 **粗定位点**多远（**当前口径的量**：‖精定位 − 粗定位‖₂）。
    deviation_px_coarse: float | None = None
    #: 同一个量的 **crop 宽度千分位**刻度（阈值用它 → 换 crop 不用重调）。
    deviation_c1000: float | None = None
    #: **精定位的原始落点**（物理像素）—— 此前**不可恢复**。
    refined_point_px: tuple[float, float] | None = None
    #: **最终落点来自哪一级**（``refined`` / ``coarse`` / ``whole_screen`` /
    #: ``numbered`` / ``none``）。
    landing_source: str = ""
    #: **粗定位的原始回答**：框（模型输入空间）。
    coarse_bbox_model: list[float] | None = None
    #: 粗定位的原始中心点（模型输入空间）。
    coarse_point_model: list[float] | None = None
    #: 粗定位**模型自报的元素文字**。
    coarse_text: str = ""
    #: **落点到 GT bbox 的距离**（框内 = 0）。
    dist_to_bbox: float | None = None
    #: 这一臂的**图像 token**（按 28 px patch 规则对**实际发出的那张图**估计）。
    image_tokens: int | None = None
    # -- 施工清单 T3 / T4 / T5 的埋点 -------------------------------------
    #: **crop 放大的倍数**（T3）—— 1 = 没放大。
    refine_upscale_factor: float | None = None
    #: **精定位的候选数**（T4：K 次采样里解析出可用落点的次数）。
    refine_candidate_count: int | None = None
    #: **最密簇的簇占比**（T4：``refine_cluster_ratio``）。
    refine_cluster_ratio: float | None = None
    #: **标记图上模型选的编号**（T4：``refine_chosen_candidate``）。
    #: 0 = 模型答「清单里没有可信的」；``None`` = 没有标记图这一步
    #: （或它的回答读不出来）。
    refine_chosen_candidate: int | None = None
    #: **粗定位的原始框（物理像素四元组）**（T5：护栏离线重算要它）。
    coarse_bbox_px: list[float] | None = None
    #: **crop 窗口的物理矩形**（T5：``(左, 上, 右, 下)``）。
    crop_rect_px: list[float] | None = None

    @property
    def stratum(self) -> tuple[str, str]:
        """分层键（平台 × 文本/图标）。"""
        return (self.platform, self.ui_type)

    def to_dict(self) -> dict[str, Any]:
        return {
            "sample_id": self.sample_id,
            "ui_type": self.ui_type,
            "platform": self.platform,
            "group": self.group,
            "application": self.application,
            "hit": self.hit,
            "landing_point": list(self.landing_point) if self.landing_point else None,
            "coarse_point": list(self.coarse_point) if self.coarse_point else None,
            "gate_hit": self.gate_hit,
            "gate_reasons": list(self.gate_reasons),
            "gate_undecidable": list(self.gate_undecidable),
            "refine_enabled": self.refine_enabled,
            "fallback": self.fallback,
            "fallback_cause": self.fallback_cause,
            "model_calls": self.model_calls,
            "error": self.error,
            "crop_contains_target": self.crop_contains_target,
            "crop_miss_reason": self.crop_miss_reason,
            "deviation_px_crop_center": self.deviation_px_crop_center,
            "deviation_px_coarse": self.deviation_px_coarse,
            "deviation_c1000": self.deviation_c1000,
            "refined_point_px": (
                list(self.refined_point_px) if self.refined_point_px else None
            ),
            "landing_source": self.landing_source,
            "coarse_bbox_model": (
                list(self.coarse_bbox_model) if self.coarse_bbox_model else None
            ),
            "coarse_point_model": (
                list(self.coarse_point_model) if self.coarse_point_model else None
            ),
            "coarse_text": self.coarse_text,
            "dist_to_bbox": self.dist_to_bbox,
            "image_tokens": self.image_tokens,
            "refine_upscale_factor": self.refine_upscale_factor,
            "refine_candidate_count": self.refine_candidate_count,
            "refine_cluster_ratio": self.refine_cluster_ratio,
            "refine_chosen_candidate": self.refine_chosen_candidate,
            "coarse_bbox_px": (
                list(self.coarse_bbox_px) if self.coarse_bbox_px else None
            ),
            "crop_rect_px": list(self.crop_rect_px) if self.crop_rect_px else None,
        }


#: **门控输入的来源**（哪些条件可判定、哪些不可判定）—— 落进报告。
#:
#: ⚠️ **这段说明必须与代码一致** —— 它进 ``sspro-report.json`` 的
#: ``gate.input_sources``，读报告的人**照着它判断「哪条条件当时判了」**。
#: 口径变过时（门控降级为记录器、粗定位出框）**必须回来改这里**。
GATE_INPUT_SOURCES: dict[str, str] = {
    "coarse_validity": "可判定（模型自己的输出：粗定位为空或越界）",
    "target_area_ratio": (
        "可判定（粗定位**出框** → 用框推出半径 → 目标占比第一次可判；"
        "模型只给点不给框时仍然不可判定）"
    ),
    "confidence": "不可判定（模型不报置信度）",
    "consistency": (
        "不可判定（门控降级为记录器之后，「第二次独立粗定位」那次调用"
        "已被删掉 → 判据留着、永远不可判定）"
    ),
}


#: **阶段标签**（施工清单第 ⑨ 条：「每条回答打阶段标签 coarse / whole2nd / refine」）。
#:
#: ⚠️ **这只是标签**：用来**事后分辨哪一级被复用了**。
#: 它**不参与任何坐标换算**，也不改变任何判定 —— 换算链的唯一真值来源
#: 仍然是标定结果（架构决策备忘 §2.6 C-04）。
STAGE_COARSE = "coarse"
STAGE_WHOLE_SECOND = "whole2nd"
STAGE_REFINE = "refine"
STAGE_CALIB = "calib"
STAGE_UNKNOWN = "unknown"

#: **提示词 → 阶段标签**的映射表（**前缀**匹配，**顺序即优先级**）。
#:
#: * ``refine`` —— crop 内精定位（「这是一张从 Windows 桌面截图里裁出的小区域…」）；
#: * ``calib`` —— 标定探针（「这是一张标定图…」）；
#: * ``coarse`` —— 整屏粗定位（「这是一张 Windows 桌面截图。」）。
#:
#: ⚠️ **整屏第二遍**（``whole2nd``）**也**以「这是一张 Windows 桌面截图。」开头，
#: 但它**带着上一次失败的原因** → 判它要**先**看那个标志（见
#: :func:`prompt_stage`），不能只按前缀判。
_STAGE_BY_PROMPT_PREFIX: tuple[tuple[str, str], ...] = (
    ("这是一张从 Windows 桌面截图里裁出的小区域", STAGE_REFINE),
    ("这是一张标定图", STAGE_CALIB),
    ("这是一张 Windows 桌面截图。", STAGE_COARSE),
)

#: **整屏第二遍**的标志：它的提示词里带着上一次失败的原因
#: （``ground/prompt.py::whole_screen_prompt`` 的「上一次没能给出可用的位置」）。
_WHOLE_SECOND_MARKER = "上一次没能给出可用的位置"


def prompt_stage(prompt: str) -> str:
    """**从提示词判定这一条回答属于哪一级**（阶段标签）。

    * ``coarse`` —— 整屏粗定位；
    * ``whole2nd`` —— 整屏第二遍（**带着上一次没能给出可用的位置**）；
    * ``refine`` —— crop 内的精定位；
    * ``calib`` —— 标定探针；
    * ``unknown`` —— 认不出来（**旧缓存没有标签**时也是它）。

    ⚠️ **判「整屏第二遍」要**先**看标志、再看前缀** —— 两者的开头一样。
    ⚠️ **这只是标签**：**不参与任何坐标换算**，也不改变任何判定。
    """
    text = prompt or ""
    if _WHOLE_SECOND_MARKER in text:
        return STAGE_WHOLE_SECOND
    for prefix, stage in _STAGE_BY_PROMPT_PREFIX:
        if text.startswith(prefix):
            return stage
    return STAGE_UNKNOWN


class MemoizingModelCaller:
    """**应答记忆**的模型端口包装（评测侧）：同一次请求（提示词 + 画面）只问一次。

    ⚠️ **为什么需要它**：模型端**不确定** —— 同一条提示词两次问，答案可能不同。
    工单 M8 的硬判据要求两臂「**唯一差异是门控是否恒开**」；
    若两臂各自重问一次粗定位，差异里就混进了模型的随机性。
    ⚠️ **「42 条里 35 条两臂的粗定位点不同，最大差 3062 px，其中 11 条命中与否翻转」
    是**加这个端口之前**那一轮的数**（M8.md §3.1 自己标注了「第一轮的数作废、不引用」）；
    **端口之后两臂实际只差 6/42**（都是「一臂的模型调用失败 → 走了另一条路径」造成的），
    **噪声量级 = 6/42 = 14.3 pp** —— 留出集重评见工单 **M23**。

    记忆端口让两臂**共用同一次粗定位回答**（以及同一次整屏第二遍 / 精定位回答），
    两臂之间剩下的差异就**只有门控**。

    键 = (提示词, 画面哈希) —— 只有**完全相同**的请求才复用。

    **落盘缓存**（施工清单第 ⑨ 条）：``cache_path`` 给了就把回答写进
    JSONL 缓存、下次运行**先读缓存**。这样**跨进程**的两轮（例如
    「只换 crop 尺寸」）也能**共用同一次粗定位回答** → 那一对是**真配对**，
    差异里**不混模型随机性**。

    **阶段标签**（施工清单第 ⑨ 条：「每条回答打阶段标签 coarse / whole2nd /
    refine」）：缓存条目里落一个 ``stage`` 字段（由 :func:`prompt_stage`
    从**提示词**判定）→ **事后**能分辨「哪一级被复用了」。
    ⚠️ **它只是标签**：**不参与任何坐标换算**，也不改变任何判定。
    ⚠️ **旧缓存没有标签** → 读回来记 ``unknown``（**不猜**）。
    """

    def __init__(self, inner: ModelCaller, *, cache_path: Path | str | None = None):
        self._inner = inner
        self._cache: dict[tuple[str, str], Any] = {}
        #: **每条缓存条目的阶段标签**（请求键 → 标签）。
        self._stage_by_key: dict[tuple[str, str], str] = {}
        self._lock = threading.Lock()
        #: 真正发出去的请求数。
        self.calls = 0
        #: 复用（没再问）的回答数。
        self.replays = 0
        #: **按阶段统计**：发出去的请求数。
        self.sent_by_stage: dict[str, int] = {}
        #: **按阶段统计**：复用（没再问）的条数。
        self.replayed_by_stage: dict[str, int] = {}
        #: **最后一次用的提示词**（反查服务端实际尺寸时要用 —— 逐字相同的那条）。
        self.last_prompt = ""
        #: **落盘缓存**（跨进程复用）。
        self._cache_path = Path(cache_path) if cache_path else None
        self._disk: dict[str, Any] = {}
        if self._cache_path is not None and self._cache_path.is_file():
            for line in self._cache_path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                key = record.get("key")
                reply = record.get("reply")
                if key and reply is not None:
                    #: **旧缓存没有标签** → 记 ``unknown``（**不猜**）。
                    self._disk[str(key)] = {
                        "reply": reply,
                        "stage": str(record.get("stage") or STAGE_UNKNOWN),
                    }

    @staticmethod
    def _disk_key(prompt: str, image_key: str) -> str:
        return hashlib.sha256(f"{prompt}\n{image_key}".encode()).hexdigest()

    def complete(
        self,
        prompt: str,
        *,
        effort: Effort | str | None = None,
        max_tokens: int = 2000,
        image_base64: str | None = None,
        images_base64: Sequence[str] = (),
        thinking_disabled: bool = False,
    ) -> Any:
        key = (prompt, _image_key(image_base64, images_base64))
        #: **阶段标签**（从提示词判定）—— 只是标签，不参与任何坐标换算。
        stage = prompt_stage(prompt)
        with self._lock:
            self.last_prompt = prompt
            cached = self._cache.get(key)
            if cached is not None:
                self.replays += 1
                self._bump(
                    self.replayed_by_stage, self._stage_by_key.get(key, stage)
                )
                return cached
        if self._cache_path is not None:
            entry = self._disk.get(self._disk_key(*key))
            if entry is not None:
                reply = _reply_from_dict(entry["reply"])
                with self._lock:
                    self._cache.setdefault(key, reply)
                    #: **读缓存也带上标签**（旧缓存没有 → ``unknown``）。
                    label = str(entry.get("stage") or STAGE_UNKNOWN)
                    self._stage_by_key.setdefault(key, label)
                    self.replays += 1
                    self._bump(self.replayed_by_stage, label)
                return reply
        reply = self._inner.complete(
            prompt,
            effort=effort,
            max_tokens=max_tokens,
            image_base64=image_base64,
            images_base64=images_base64,
            thinking_disabled=thinking_disabled,
        )
        with self._lock:
            self._cache.setdefault(key, reply)
            self._stage_by_key.setdefault(key, stage)
            self.calls += 1
            self._bump(self.sent_by_stage, stage)
        if self._cache_path is not None:
            self._write_disk(key, reply, stage)
        return reply

    @staticmethod
    def _bump(counter: dict[str, int], stage: str) -> None:
        counter[stage] = counter.get(stage, 0) + 1

    def stage_report(self) -> dict[str, Any]:
        """**阶段标签的分布**（发出去的 / 复用的各按阶段计数）。"""
        return {
            "sent": dict(sorted(self.sent_by_stage.items())),
            "replayed": dict(sorted(self.replayed_by_stage.items())),
            "note": (
                "阶段标签**只用于事后分辨哪一级被复用**；"
                "它**不参与任何坐标换算**，也不改变任何判定。"
                "旧缓存没有标签 → 记 ``unknown``"
            ),
        }

    def _write_disk(self, key: tuple[str, str], reply: Any, stage: str) -> None:
        """把这一条回答写进落盘缓存（**单次 write + flush**，行不撕裂）。"""
        assert self._cache_path is not None
        payload = {
            "key": self._disk_key(*key),
            #: **阶段标签**（事后分辨哪一级被复用了；不参与任何坐标换算）。
            "stage": stage,
            "reply": _reply_to_dict(reply),
        }
        self._cache_path.parent.mkdir(parents=True, exist_ok=True)
        with self._cache_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
            handle.flush()


def _reply_to_dict(reply: Any) -> dict[str, Any]:
    """把一条模型回答落成**可复原**的字典（只留读得出来的字段）。"""
    return {
        "text": reply.text,
        "model": reply.model,
        "endpoint": reply.endpoint,
        "effort": str(reply.effort),
        "http_status": reply.http_status,
        "latency_ms": reply.latency_ms,
        "input_tokens": reply.input_tokens,
        "output_tokens": reply.output_tokens,
        "thinking_tokens": reply.thinking_tokens,
        "image_tokens": reply.image_tokens,
        "cache_read_input_tokens": reply.cache_read_input_tokens,
        "cache_creation_input_tokens": reply.cache_creation_input_tokens,
        "raw_content": [dict(block) for block in reply.raw_content],
    }


def _reply_from_dict(record: dict[str, Any]) -> Any:
    """把落盘的回答**复原**成一条 :class:`minicode.model.client.ModelReply`。"""
    from minicode.model.client import ModelReply

    return ModelReply(
        text=record.get("text", ""),
        model=record.get("model", ""),
        endpoint=record.get("endpoint", ""),
        effort=record.get("effort", "low"),
        http_status=int(record.get("http_status") or 0),
        latency_ms=float(record.get("latency_ms") or 0.0),
        input_tokens=record.get("input_tokens"),
        output_tokens=record.get("output_tokens"),
        thinking_tokens=record.get("thinking_tokens"),
        image_tokens=record.get("image_tokens"),
        cache_read_input_tokens=record.get("cache_read_input_tokens"),
        cache_creation_input_tokens=record.get("cache_creation_input_tokens"),
        raw_content=tuple(record.get("raw_content") or ()),
    )


def _image_key(image_base64: str | None, images_base64: Sequence[str]) -> str:
    """画面的键（base64 的哈希 —— 键里不放几 MB 的原文）。"""
    digest = hashlib.sha256()
    if image_base64:
        digest.update(image_base64.encode("ascii"))
    for item in images_base64:
        digest.update(item.encode("ascii"))
    return digest.hexdigest()


def run_arms(
    samples: Sequence[SsproSample],
    client: ModelCaller,
    *,
    scale_by_size: dict[tuple[int, int], Any],
    model_input_by_size: dict[tuple[int, int], tuple[int, int]],
    effort: Effort | str = Effort.LOW,
    max_tokens: int = 4096,
    progress: Callable[[str], None] | None = None,
    workers: int = 1,
    gate_params: Any = None,
    crop_size: tuple[int, int] | None = None,
    variants: int = 1,
    coarse_variants: int = 1,
    pool: Any = None,
    upscale_target_px: float = 30.0,
    upscale_max_factor: float = 3.0,
    marked_candidates: int = 6,
) -> tuple[list[ArmOutcome], list[ArmOutcome], list[ArmOutcome]]:
    """跑**三臂**，返回 ``(门控记录器, 无条件两段式, 只用粗定位)`` 三组结果。

    **同一条样本的三臂共用同一帧画面**（同一批样本、同一张图 —— 三臂可比）。

    * ``on_demand`` —— **门控记录器**：门控**只落盘**，不再决定开不开精定位
      （用户 2026-10-21 拍板：门控降级为记录器，生产默认无条件精定位）。
    * ``always`` —— **无条件两段式**：粗定位 + crop 精定位（**生产默认**）。
    * ``coarse_only`` —— **只用粗定位**：落点取粗点，**1.00 次/目标**。
      这是**从来没量过的基线** —— 没有它就说不清「精定位到底有没有用、
      值不值那多出来的那一次调用」。

    ⚠️ 改完门控降级之后，``on_demand`` 与 ``always`` 的**行为应当完全相同**
    （差别只在落盘的 decision）→ 两者落点**应当逐条相同**，这是体检项。

    ``workers > 1`` 时**按样本并行**（每条样本自己 decode、自己跑三臂）——
    模型调用是网络往返，并行只缩短墙钟时间，不改变任何判定。

    ``gate_params`` 是**门控参数**（运行时参数，工单 M7 的口径）：评测里
    调它就是「调门控」，不改编码。
    """

    def run_sample(
        sample: SsproSample,
    ) -> tuple[ArmOutcome, ArmOutcome, ArmOutcome]:
        frame = read_png_frame(sample.image_path)
        surface = (0, 0, frame.width, frame.height)
        scale = scale_by_size[sample.image_size]
        model_input = model_input_by_size[sample.image_size]
        on_demand = _run_one(
            sample,
            frame,
            surface,
            scale,
            model_input,
            GateMode.ON_DEMAND,
            effort,
            max_tokens,
            client,
            gate_params=gate_params,
            crop_size=crop_size,
            variants=variants,
            coarse_variants=coarse_variants,
            pool=pool,
            upscale_target_px=upscale_target_px,
            upscale_max_factor=upscale_max_factor,
            marked_candidates=marked_candidates,
        )
        always = _run_one(
            sample,
            frame,
            surface,
            scale,
            model_input,
            GateMode.ALWAYS,
            effort,
            max_tokens,
            client,
            gate_params=gate_params,
            crop_size=crop_size,
            variants=variants,
            coarse_variants=coarse_variants,
            pool=pool,
            upscale_target_px=upscale_target_px,
            upscale_max_factor=upscale_max_factor,
            marked_candidates=marked_candidates,
        )
        coarse_only = _run_one(
            sample,
            frame,
            surface,
            scale,
            model_input,
            GateMode.ALWAYS,
            effort,
            max_tokens,
            client,
            gate_params=gate_params,
            coarse_only=True,
            crop_size=crop_size,
            variants=variants,
            coarse_variants=coarse_variants,
            pool=pool,
            upscale_target_px=upscale_target_px,
            upscale_max_factor=upscale_max_factor,
            marked_candidates=marked_candidates,
        )
        return on_demand, always, coarse_only

    triples: list[tuple[ArmOutcome, ArmOutcome, ArmOutcome]] = []
    if workers <= 1:
        for index, sample in enumerate(samples, start=1):
            triple = run_sample(sample)
            triples.append(triple)
            if progress is not None:
                progress(
                    f"[{index}/{len(samples)}] {sample.sample_id} "
                    f"记录器={triple[0].hit} 两段式={triple[1].hit} 只有粗={triple[2].hit}"
                )
    else:
        from concurrent.futures import ThreadPoolExecutor

        with ThreadPoolExecutor(max_workers=workers) as executor:
            for index, triple in enumerate(executor.map(run_sample, samples), start=1):
                triples.append(triple)
                if progress is not None:
                    progress(
                        f"[{index}/{len(samples)}] {triples[-1][0].sample_id} "
                        f"记录器={triples[-1][0].hit} 两段式={triples[-1][1].hit} "
                        f"只有粗={triples[-1][2].hit}"
                    )
    return (
        [triple[0] for triple in triples],
        [triple[1] for triple in triples],
        [triple[2] for triple in triples],
    )


def _dist_to_bbox(
    point: tuple[int, int] | tuple[float, float] | None,
    bbox: tuple[int, int, int, int],
) -> float | None:
    """点到 GT bbox 的距离（**框内 = 0**）。

    与浏览器侧 ``e2e/apps.py`` 的「到矩形距离」同一语义。
    """
    if point is None:
        return None
    x1, y1, x2, y2 = bbox
    dx = max(x1 - point[0], 0.0, point[0] - x2)
    dy = max(y1 - point[1], 0.0, point[1] - y2)
    return float((dx * dx + dy * dy) ** 0.5)


def _crop_containment(
    crop_plan: Any, bbox: tuple[int, int, int, int], image_size: tuple[int, int]
) -> tuple[bool | None, str]:
    """crop 窗口**有没有完整含住 GT 框**，以及没含住的**原因**。

    原因三选一（施工清单第 ① 条）：

    * ``out-of-frame`` —— GT 框本身就出了这张图（**不可能**含住）；
    * ``coarse-picked-wrong`` —— GT 框的中心**不在** crop 窗内
      （粗点指到了别的地方）；
    * ``out-of-bbox`` —— 中心在窗内，但框**没被完整含住**（部分含）。
    """
    if not crop_plan:
        return (None, "")
    try:
        left = float(crop_plan["origin_x"])
        top = float(crop_plan["origin_y"])
        right = left + float(crop_plan["width"])
        bottom = top + float(crop_plan["height"])
    except (KeyError, TypeError, ValueError):
        return (None, "")
    x1, y1, x2, y2 = bbox
    if x1 < 0 or y1 < 0 or x2 > image_size[0] or y2 > image_size[1]:
        return (False, "out-of-frame")
    center_x = (x1 + x2) / 2.0
    center_y = (y1 + y2) / 2.0
    if not (left <= center_x <= right and top <= center_y <= bottom):
        return (False, "coarse-picked-wrong")
    if left <= x1 and top <= y1 and right >= x2 and bottom >= y2:
        return (True, "")
    return (False, "out-of-bbox")


#: **算图像 token 的那一级**（``coarse_only`` / ``no_candidates`` 不是模型调用）。
_IMAGE_TOKEN_STAGES = ("coarse", "whole_screen", "refine", "numbered")


def _image_tokens_of_echoes(echoes: Any) -> int | None:
    """这一臂的**图像 token**（28 px patch 规则，对**实际发出的那张图**估计）。"""
    from minicode.model.request_log import estimate_image_tokens

    total = 0
    seen = False
    for echo in echoes or ():
        if echo.stage not in _IMAGE_TOKEN_STAGES:
            continue
        if not echo.sent_width or not echo.sent_height:
            continue
        total += estimate_image_tokens(int(echo.sent_width), int(echo.sent_height))
        seen = True
    return total if seen else None


def _run_one(
    sample: SsproSample,
    frame: CapturedFrame,
    surface: tuple[int, int, int, int],
    scale: Any,
    model_input: tuple[int, int],
    gate_mode: GateMode,
    effort: Effort | str,
    max_tokens: int,
    client: ModelCaller,
    gate_params: Any = None,
    coarse_only: bool = False,
    crop_size: tuple[int, int] | None = None,
    variants: int = 1,
    coarse_variants: int = 1,
    pool: Any = None,
    upscale_target_px: float = 30.0,
    upscale_max_factor: float = 3.0,
    marked_candidates: int = 6,
) -> ArmOutcome:
    """跑一条样本的一个臂，判命中（**按官方脚本的判定口径**）。"""
    from minicode.action.ports import VisualLocateRequest
    from minicode.ground.channel import TwoStageGrounder

    grounder = TwoStageGrounder(
        client,
        scale=scale,
        model_input_size=model_input,
        full_screen_size=(frame.width, frame.height),
        effort=effort,
        max_tokens=max_tokens,
        gate_mode=gate_mode,
        gate_params=gate_params,
        coarse_only=coarse_only,
        crop_width=crop_size[0] if crop_size else None,
        crop_height=crop_size[1] if crop_size else None,
        variants=variants,
        coarse_variants=coarse_variants,
        pool=pool,
        upscale_target_px=upscale_target_px,
        upscale_max_factor=upscale_max_factor,
        marked_candidates=marked_candidates,
    )
    result = grounder.locate(
        VisualLocateRequest(
            target_description=sample.instruction,
            frame=frame,
            surface=surface,
        )
    )
    hit = False
    if result.landing_point is not None:
        prediction = SCREENSPOT_PRO.wrap(result.landing_point, sample.image_size)
        hit = SCREENSPOT_PRO.hit(prediction, sample.as_eval_sample())
    crop_plan = result.crop_plan or {}
    contains, miss_reason = _crop_containment(
        crop_plan, sample.bbox, sample.image_size
    )
    coarse_echo = next(
        (echo for echo in result.echoes if echo.stage == "coarse"), None
    )
    refined_point = None
    if crop_plan.get("refined_point_x") is not None:
        refined_point = (
            float(crop_plan["refined_point_x"]),
            float(crop_plan["refined_point_y"]),
        )
    return ArmOutcome(
        sample_id=sample.sample_id,
        ui_type=sample.ui_type,
        platform=sample.platform,
        group=sample.group,
        application=sample.application,
        hit=hit,
        landing_point=result.landing_point,
        coarse_point=result.coarse_point,
        gate_hit=result.gate_hit,
        gate_reasons=tuple(result.gate_reasons),
        gate_undecidable=tuple(result.gate_undecidable),
        refine_enabled=result.refine_enabled,
        fallback=result.fallback,
        fallback_cause=result.fallback_cause,
        model_calls=result.model_calls,
        error=result.error_message,
        crop_contains_target=contains,
        crop_miss_reason=miss_reason,
        deviation_px_crop_center=crop_plan.get("deviation_px"),
        deviation_px_coarse=crop_plan.get("deviation_px_coarse"),
        deviation_c1000=crop_plan.get("deviation_c1000"),
        refined_point_px=refined_point,
        landing_source=result.landing_source,
        coarse_bbox_model=(
            list(coarse_echo.raw_bbox)
            if coarse_echo is not None and coarse_echo.raw_bbox
            else None
        ),
        coarse_point_model=(
            [coarse_echo.raw_x, coarse_echo.raw_y]
            if coarse_echo is not None and coarse_echo.raw_x is not None
            else None
        ),
        coarse_text=coarse_echo.raw_text if coarse_echo is not None else "",
        dist_to_bbox=_dist_to_bbox(result.landing_point, sample.bbox),
        image_tokens=_image_tokens_of_echoes(result.echoes),
        refine_upscale_factor=crop_plan.get("refine_upscale_factor"),
        refine_candidate_count=crop_plan.get("refine_candidate_count"),
        refine_cluster_ratio=crop_plan.get("refine_cluster_ratio"),
        refine_chosen_candidate=crop_plan.get("refine_chosen_candidate"),
        coarse_bbox_px=crop_plan.get("coarse_bbox_px"),
        crop_rect_px=crop_plan.get("crop_rect_px"),
    )


def _accuracy(outcomes: Sequence[ArmOutcome]) -> float:
    if not outcomes:
        return 0.0
    return sum(1 for outcome in outcomes if outcome.hit) / len(outcomes)


def model_input_target_sizes(
    samples: Sequence[SsproSample],
    model_input_by_size: dict[tuple[int, int], tuple[int, int]],
) -> dict[str, Any]:
    """**目标在「模型输入图」里的像素尺寸**分布（按 ui_type 分层）。

    验收线 F 组要看的就是这个数：整屏那一级**缩过图**，icon 在模型看到的那张图里
    会变成几个像素（施工清单第 ⑥ 条：icon 中位要 ≥ 16 px）。

    口径：目标 GT 框的**较大边**（物理像素）× ``模型输入宽 / 原图宽``
    —— 与「模型看到的那张图」同一个空间。
    """
    by_type: dict[str, list[float]] = {}
    for sample in samples:
        model_input = model_input_by_size.get(sample.image_size)
        if model_input is None:
            continue
        scale = model_input[0] / sample.image_size[0]
        side = max(
            sample.bbox[2] - sample.bbox[0], sample.bbox[3] - sample.bbox[1]
        ) * scale
        by_type.setdefault(sample.ui_type, []).append(side)
    summary: dict[str, Any] = {}
    for ui_type, sizes in sorted(by_type.items()):
        ordered = sorted(sizes)
        middle = len(ordered) // 2
        summary[ui_type] = {
            "samples": len(ordered),
            "median_px": round(ordered[middle], 2),
            "min_px": round(ordered[0], 2),
            "p10_px": round(ordered[max(0, int(len(ordered) * 0.1) - 1)], 2),
            "max_px": round(ordered[-1], 2),
            "below_16px": sum(1 for value in ordered if value < 16.0),
            "below_16px_rate": round(
                sum(1 for value in ordered if value < 16.0) / len(ordered), 4
            ),
        }
    return summary


def summarize(outcomes: Sequence[ArmOutcome]) -> dict[str, Any]:
    """一批结果的汇总（整体 / 文本 / 图标 / 平台 / 分组 + 门控埋点）。"""
    gate_samples = [
        GateSample(
            gate_hit=outcome.gate_hit,
            gate_reasons=outcome.gate_reasons,
            gate_undecidable=outcome.gate_undecidable,
            refine_enabled=outcome.refine_enabled,
            fallback=outcome.fallback,
            fallback_cause=outcome.fallback_cause,
        )
        for outcome in outcomes
    ]
    gate: GateReport = build_gate_report(gate_samples)
    by_ui = {
        ui_type: _accuracy([o for o in outcomes if o.ui_type == ui_type])
        for ui_type in sorted({o.ui_type for o in outcomes})
    }
    by_platform = {
        platform: _accuracy([o for o in outcomes if o.platform == platform])
        for platform in sorted({o.platform for o in outcomes})
    }
    by_group = {
        group: _accuracy([o for o in outcomes if o.group == group])
        for group in sorted({o.group for o in outcomes})
    }
    return {
        "samples": len(outcomes),
        "accuracy": _accuracy(outcomes),
        "hits": sum(1 for outcome in outcomes if outcome.hit),
        "accuracy_by_ui_type": by_ui,
        "accuracy_by_platform": by_platform,
        "accuracy_by_group": by_group,
        "model_calls": sum(outcome.model_calls for outcome in outcomes),
        "errors": sum(1 for outcome in outcomes if outcome.error),
        "gate": gate.to_dict(),
        **_telemetry_summary(outcomes),
    }


#: **近失带的宽度**（物理像素）。
#:
#: WCAG 2.2 SC 2.5.8 的 24 CSS px @150% DPI = **36 物理 px**
#: （语义是「近到可能激活了隔壁控件」）—— 验收线 B 组要校准的就是这一档。
NEAR_MISS_BAND_PX = 36.0


def _percentile(sorted_values: Sequence[float], fraction: float) -> float | None:
    """**一个**百分位（最近秩口径，与本模块 telemetry 里的 p50 / p90 同一套）。

    ⚠️ 与 :mod:`minicode.eval.stats` 的**插值**口径不同 —— 那里那个是
    「统计模块的口径」，这里这个是「埋点汇总的口径」→ **不跨口径对账**。
    """
    if not sorted_values:
        return None
    index = min(len(sorted_values) - 1, int(fraction * len(sorted_values)))
    return sorted_values[index]


def near_miss_calibration(
    outcomes: Sequence[ArmOutcome],
    taus: Sequence[float] = (5, 10, 20, 50, 100),
) -> dict[str, Any]:
    """**近失带的离线校准**（验收线 B 组 / 施工清单第 ① 条埋点的下游用法）。

    对每个 τ 给两个数：

    * ``hits_failing_band`` —— **本来命中**（``hit=True``）的样本里
      ``dist_to_bbox > τ`` 的条数（= 会被这个近失带**判失败**的条数）；
    * ``misses_rescued`` —— **本来没命中**（``hit=False``）的样本里
      ``dist_to_bbox <= τ`` 的条数（= 会被这个近失带**捞回来**的条数）。

    另外给 ``dist_to_bbox`` 的 p50 / p90，以及「**找对**」（命中）与
    「**挑错**」（没命中）两个子集各自的 p50。

    ⚠️ **口径**：``dist_to_bbox`` 是**到 GT 框的距离**（框内 = 0）→
    **命中 ⟺ 距离为 0**（框边界上的舍入例外除外）→
    「找对」子集的 p50 **恒为 0**。
    ⚠️ 它**不是**真机 e2e 那个「到控件**中心**的落点误差中位」——
    一个是**到矩形**、一个是**到中心**，**两个量不许混用**。

    **纯函数**：只用已经落盘的 ``dist_to_bbox``，**不额外发模型调用**。
    """
    hits = [outcome for outcome in outcomes if outcome.hit]
    misses = [outcome for outcome in outcomes if not outcome.hit]
    distances = sorted(
        outcome.dist_to_bbox for outcome in outcomes if outcome.dist_to_bbox is not None
    )
    hit_distances = sorted(
        outcome.dist_to_bbox for outcome in hits if outcome.dist_to_bbox is not None
    )
    miss_distances = sorted(
        outcome.dist_to_bbox for outcome in misses if outcome.dist_to_bbox is not None
    )

    def _sweep(tau: float) -> dict[str, Any]:
        return {
            "tau_px": tau,
            "hits_failing_band": sum(
                1
                for outcome in hits
                if outcome.dist_to_bbox is not None and outcome.dist_to_bbox > tau
            ),
            "misses_rescued": sum(
                1
                for outcome in misses
                if outcome.dist_to_bbox is not None and outcome.dist_to_bbox <= tau
            ),
        }

    p50 = _percentile(distances, 0.5)
    p90 = _percentile(distances, 0.9)
    hit_p50 = _percentile(hit_distances, 0.5)
    miss_p50 = _percentile(miss_distances, 0.5)
    return {
        "samples": len(outcomes),
        "hits": len(hits),
        "misses": len(misses),
        "dist_to_bbox": {
            "n": len(distances),
            "p50": round(p50, 1) if p50 is not None else None,
            "p90": round(p90, 1) if p90 is not None else None,
            "max": round(distances[-1], 1) if distances else None,
        },
        #: 「找对」子集（命中）与「挑错」子集（没命中）各自的 p50。
        "p50_by_subset": {
            "hit": round(hit_p50, 1) if hit_p50 is not None else None,
            "miss": round(miss_p50, 1) if miss_p50 is not None else None,
        },
        #: 逐 τ 的扫描（**判失败** / **捞回来** 各多少条）。
        "taus": [_sweep(tau) for tau in taus],
        #: **验收的那一档**（36 px 近失带）。
        "band_36px": _sweep(NEAR_MISS_BAND_PX),
        "note": (
            "dist_to_bbox = 到 GT 框的距离（框内 = 0）→ **命中 ⟺ 距离为 0**；"
            "「找对」子集的 p50 恒为 0，**不是**真机 e2e 那个"
            "「到控件中心的落点误差」—— 两个量不许混用"
        ),
    }


def _telemetry_summary(outcomes: Sequence[ArmOutcome]) -> dict[str, Any]:
    """**第 ① 条埋点**的汇总（crop 含住率 / 假命中 / 落点来源 / 图像 token）。"""
    opened = [outcome for outcome in outcomes if outcome.crop_contains_target is not None]
    contains = [outcome for outcome in opened if outcome.crop_contains_target]
    miss_reasons: dict[str, int] = {}
    for outcome in opened:
        if outcome.crop_miss_reason:
            miss_reasons[outcome.crop_miss_reason] = (
                miss_reasons.get(outcome.crop_miss_reason, 0) + 1
            )
    fallback_causes: dict[str, int] = {}
    for outcome in outcomes:
        if outcome.fallback_cause:
            fallback_causes[outcome.fallback_cause] = (
                fallback_causes.get(outcome.fallback_cause, 0) + 1
            )
    landing_sources: dict[str, int] = {}
    for outcome in outcomes:
        key = outcome.landing_source or "none"
        landing_sources[key] = landing_sources.get(key, 0) + 1
    distances = sorted(
        outcome.dist_to_bbox for outcome in outcomes if outcome.dist_to_bbox is not None
    )
    image_tokens = [
        outcome.image_tokens for outcome in outcomes if outcome.image_tokens is not None
    ]
    return {
        "telemetry": {
            "crop_opened": len(opened),
            "crop_contains_target": len(contains),
            "crop_contains_target_rate": (
                len(contains) / len(opened) if opened else 0.0
            ),
            "crop_miss_reason_distribution": miss_reasons,
            #: **假命中**：窗口里没有目标，模型**仍然产出了被采纳的精定位点**。
            "fake_hits": sum(
                1
                for outcome in opened
                if outcome.crop_contains_target is False
                and outcome.landing_source == "refined"
            ),
            "fallback_cause_distribution": fallback_causes,
            "landing_source_distribution": landing_sources,
            "dist_to_bbox": {
                "n": len(distances),
                "p50": round(distances[len(distances) // 2], 1) if distances else None,
                "p90": (
                    round(distances[min(len(distances) - 1, int(len(distances) * 0.9))], 1)
                    if distances
                    else None
                ),
                "max": round(distances[-1], 1) if distances else None,
            },
            "image_tokens": {
                "total": sum(image_tokens),
                "per_target": (
                    sum(image_tokens) / len(image_tokens) if image_tokens else 0.0
                ),
            },
            #: **近失带的离线校准**（验收线 B 组）—— 纯算术，**不发模型调用**。
            "near_miss_calibration": near_miss_calibration(outcomes),
        }
    }


#: **防掩盖的子集容忍度**（百分点）：任何单一子集不得比门控关低超过它。
SUBSET_TOLERANCE_PP = 1.0


def _paired(
    left: Sequence[ArmOutcome],
    right: Sequence[ArmOutcome],
    *,
    label_left: str,
    label_right: str,
    coarse_arm: Sequence[ArmOutcome] = (),
) -> dict[str, Any]:
    """**配对比较**（施工清单第 ⑨ 条）：同批样本、复用同一次上游回答。

    * ``b`` / ``c`` —— 2×2 表里两个不一致格的计数；
    * ``p`` —— **mid-p McNemar**（双侧，α=0.05）**主报**，
      另附**保守对照**：普通精确二项与 χ²+连续性校正；
    * ``delta`` 与 95% CI —— **配对 bootstrap**（2000 次，按 task 聚类）；
    * ``d`` —— 不一致对占比（噪声地板）；
    * ``mde_pp`` —— **这个样本量下能判出的最小效应**（百分点）；
    * 按「**粗定位命中 / 没命中**」**分层**（预注册的次要分析）。

    ⚠️ **MDE 的读法**：``|Δ| ≥ mde_pp`` 才算「这个样本量判得出」——
    达不到就是**样本量不够**，不是「没效果」（判据由调用点自己比）。

    ⚠️ **分层键 = 粗定位那一臂的命中与否**（``coarse_arm`` 里那条的 ``hit``），
    **不是**「有没有粗点」—— 「有没有粗点」判的是「模型答没答」，
    不是「模型答对没答对」，用它分层得到的结论**不可用**。

    ⚠️ **分层变量必须先于被测步确定**（否则是 collider 偏差）：
    粗定位那一级在被测的 crop 精定位**之前**就定了 →
    条件在**先于处理的变量**上 → 不构成 collider
    （依据见 ``docs/research/vision-final-plan.md``「评测设计」§2）。
    """
    from .stats import (
        mcnemar_chisq_cc,
        mcnemar_exact,
        mcnemar_midp,
        mde,
        paired_bootstrap_delta,
        wilson_ci,
    )

    right_by_id = {outcome.sample_id: outcome for outcome in right}
    coarse_by_id = {outcome.sample_id: outcome for outcome in coarse_arm}
    pairs: list[tuple[bool, bool]] = []
    coarse_hit_pairs: list[tuple[bool, bool]] = []
    coarse_miss_pairs: list[tuple[bool, bool]] = []
    unclassified = 0
    for outcome in left:
        peer = right_by_id.get(outcome.sample_id)
        if peer is None:
            continue
        pair = (outcome.hit, peer.hit)
        pairs.append(pair)
        coarse_outcome = coarse_by_id.get(outcome.sample_id)
        if coarse_outcome is None:
            #: 粗定位臂里没有这一条 → **分不了层**（不塞进任何一桶）。
            unclassified += 1
        elif coarse_outcome.hit:
            coarse_hit_pairs.append(pair)
        else:
            coarse_miss_pairs.append(pair)
    b = sum(1 for first, second in pairs if first and not second)
    c = sum(1 for first, second in pairs if not first and second)
    both = sum(1 for first, second in pairs if first and second)
    neither = sum(1 for first, second in pairs if not first and not second)
    bootstrap = paired_bootstrap_delta(pairs)
    n = len(pairs)
    #: **不一致对占比**（π_d 的估计 —— 噪声地板）。
    discordance = ((b + c) / n) if n else 0.0
    #: **这个样本量下能判出的最小效应**（百分点）——
    #: 配对近似 ``mde(n, π_d)``（π_d = 这批配对的不一致率）。
    mde_pp = mde(n, discordance) if n else None
    delta_pp = bootstrap.delta * 100
    return {
        "left": label_left,
        "right": label_right,
        "samples": n,
        "table": {
            "both_hit": both,
            "left_only": b,
            "right_only": c,
            "both_miss": neither,
        },
        #: **不一致对占比**（π_d 的估计 —— 噪声地板）。
        "discordance": discordance,
        #: mid-p McNemar（双侧）—— **主报**。
        "mcnemar_midp_p": mcnemar_midp(b, c),
        #: **保守对照**：普通精确二项（McNemar 的 exact conditional）。
        "mcnemar_exact_p": mcnemar_exact(b, c),
        #: **保守对照**：χ² + 连续性校正（Yates）。
        "mcnemar_chisq_cc_p": mcnemar_chisq_cc(b, c),
        #: **最小可检测效应**（百分点）：这个样本量下能判出的最小 Δ。
        "mde_pp": mde_pp,
        #: 「Δ ≥ MDE 即够」的口径（**由调用点自己比**）：判不出就是
        #: **样本量不够**，不是「没效果」。
        "delta_at_or_above_mde": (
            bool(mde_pp is not None and abs(delta_pp) >= mde_pp)
        ),
        #: Δ = 右臂命中率 − 左臂命中率，配对 bootstrap 的 95% CI。
        "delta_pp": delta_pp,
        "delta_ci95_pp": [bootstrap.ci_low * 100, bootstrap.ci_high * 100],
        "ci_excludes_zero": (
            bootstrap.ci_low > 0 or bootstrap.ci_high < 0
        ),
        "wilson_left": list(wilson_ci(sum(1 for pair in pairs if pair[0]), n)),
        "wilson_right": list(wilson_ci(sum(1 for pair in pairs if pair[1]), n)),
        #: **按「粗定位命中 / 没命中」分层**（预注册的次要分析）。
        "strata": {
            "variable": "coarse_only 臂的 hit（粗定位命中与否）",
            "note": (
                "分层变量必须**先于被测步**确定（粗定位在精定位之前）"
                "→ 不构成 collider 偏差"
            ),
            "unclassified_samples": unclassified,
            "coarse_hit": {
                "samples": len(coarse_hit_pairs),
                "delta_pp": (
                    paired_bootstrap_delta(coarse_hit_pairs).delta * 100
                    if coarse_hit_pairs
                    else None
                ),
            },
            "coarse_miss": {
                "samples": len(coarse_miss_pairs),
                "delta_pp": (
                    paired_bootstrap_delta(coarse_miss_pairs).delta * 100
                    if coarse_miss_pairs
                    else None
                ),
            },
        },
    }


def paired_comparison(
    left: Sequence[ArmOutcome],
    right: Sequence[ArmOutcome],
    *,
    label_left: str,
    label_right: str,
    coarse_arm: Sequence[ArmOutcome] = (),
) -> dict[str, Any]:
    """**配对比较**（对外口径的入口）—— 报告里「Δ / p / CI」这一段的唯一来源。

    实现住在 :func:`_paired`（本模块内部）；这个函数是它的**公开外壳**，
    给别的模块（命令行报告的切分统计）用。
    """
    return _paired(
        left,
        right,
        label_left=label_left,
        label_right=label_right,
        coarse_arm=coarse_arm,
    )


def compare_arms(
    on_demand: Sequence[ArmOutcome],
    always: Sequence[ArmOutcome],
    *,
    coarse_only: Sequence[ArmOutcome] = (),
    tolerance_pp: float = SUBSET_TOLERANCE_PP,
) -> dict[str, Any]:
    """两臂的对比：**硬判据** + **防掩盖**的逐子集对比。

    * 硬判据：**门控记录器的整体分 ≥ 无条件两段式的整体分**；
    * 防掩盖：按 (平台 × 文本/图标) 分层逐项对比，任何单一子集不得比门控关
      低超过 ``tolerance_pp`` 个百分点。

    ⚠️ 门控降级之后，``on_demand``（记录器）与 ``always``（无条件两段式）
    的**行为应当完全相同** —— 两臂落点**应当逐条相同**（``coarse_shared``
    这一项就是体检；也顺带比一下两臂的**落点**是否逐条相同）。
    ``coarse_only``（只用粗定位，1.00 次/目标）是**基线臂**。
    """
    on_demand_summary = summarize(on_demand)
    always_summary = summarize(always)
    overall_ok = on_demand_summary["accuracy"] >= always_summary["accuracy"]
    # **两臂的粗定位结果必须一致**（唯一差异是门控）—— 这一项是「可比性」的体检。
    always_by_id = {outcome.sample_id: outcome for outcome in always}
    shared = [
        outcome
        for outcome in on_demand
        if outcome.sample_id in always_by_id
        and outcome.coarse_point == always_by_id[outcome.sample_id].coarse_point
    ]
    landing_same = [
        outcome
        for outcome in on_demand
        if outcome.sample_id in always_by_id
        and outcome.landing_point == always_by_id[outcome.sample_id].landing_point
    ]
    strata: list[dict[str, Any]] = []
    for key in sorted({outcome.stratum for outcome in on_demand}):
        on_rate = _accuracy([o for o in on_demand if o.stratum == key])
        off_rate = _accuracy([o for o in always if o.stratum == key])
        strata.append(
            {
                "stratum": f"{key[0]}/{key[1]}",
                "on_demand": on_rate,
                "always": off_rate,
                "delta_pp": (on_rate - off_rate) * 100,
                "within_tolerance": (on_rate - off_rate) * 100 >= -tolerance_pp,
            }
        )
    return {
        "overall": {
            "on_demand": on_demand_summary["accuracy"],
            "always": always_summary["accuracy"],
            "delta_pp": (
                on_demand_summary["accuracy"] - always_summary["accuracy"]
            )
            * 100,
            "criterion_met": overall_ok,
        },
        "strata": strata,
        "anti_masking_met": all(item["within_tolerance"] for item in strata),
        "tolerance_pp": tolerance_pp,
        "coarse_shared": {
            "samples": len(on_demand),
            "identical": len(shared),
            "fraction": len(shared) / len(on_demand) if on_demand else 0.0,
            "note": (
                "两臂的粗定位点完全相同的条数 —— "
                "应当是 100%（唯一差异只能是门控）"
            ),
        },
        #: **门控降级后的体检**：记录器与无条件两段式的**落点**应当逐条相同
        #: （门控不再决定开不开精定位 → 行为必须一致）。
        "landing_same": {
            "samples": len(on_demand),
            "identical": len(landing_same),
            "fraction": len(landing_same) / len(on_demand) if on_demand else 0.0,
            "note": (
                "两臂的**最终落点**完全相同的条数 —— "
                "门控降级为记录器之后应当是 100%（不一致 = 门控还在影响行为）"
            ),
        },
        "on_demand": on_demand_summary,
        "always": always_summary,
        **(
            {"coarse_only": summarize(coarse_only)} if coarse_only else {}
        ),
        #: **配对比较**（施工清单第 ⑨ 条）：主检验 = 配对 mid-p McNemar +
        #: 配对 bootstrap 的 Δ 95% CI。**CI 跨 0 就不能写「改进成立」。**
        "paired": {
            "on_demand_vs_always": _paired(
                on_demand,
                always,
                label_left="on_demand",
                label_right="always",
                coarse_arm=coarse_only,
            ),
            **(
                {
                    "always_vs_coarse_only": _paired(
                        always,
                        coarse_only,
                        label_left="always",
                        label_right="coarse_only",
                        coarse_arm=coarse_only,
                    )
                }
                if coarse_only
                else {}
            ),
            "note": (
                "配对 = 同批样本、复用同一次上游回答；"
                "Δ = 右臂 − 左臂；CI 是配对 bootstrap（2000 次，按 task 聚类）。"
                "主检验 = mid-p McNemar（另附普通精确二项与 χ²+连续性校正作对照）；"
                "MDE = 这个样本量下能判出的最小效应（|Δ| ≥ MDE 才算判得出）。"
                "⚠️ **CI 跨 0 就不许写「改进成立」**"
            ),
        },
    }


def table_rows(
    *,
    comparison: dict[str, Any],
    official: Sequence[dict[str, str]],
    model_id: str,
    crop_label: str,
) -> list[dict[str, str]]:
    """那张 **7 行表**（官方 5 行 + 本方案 2 行）。

    官方行逐行标「模型 / 数据集 / 裁剪参数 / 判定口径来源」；
    **未在同模型下重跑的必须标「官方自报，仅供观感，不作硬对比」**。
    """
    rows = [dict(row) for row in official]
    for mode, label in (
        ("on_demand", "本方案 · 门控记录器"),
        ("always", "本方案 · 无条件两段式"),
        ("coarse_only", "本方案 · 只用粗定位（基线）"),
    ):
        summary = comparison.get(mode)
        if not summary:
            continue
        rows.append(
            {
                "method": label,
                "model": f"{model_id}（本次实测）",
                "dataset": "ScreenSpot-Pro（本轮抽样，见报告 N）",
                "crop": (
                    crop_label
                    if mode != "coarse_only"
                    else "无 crop（只有整屏粗定位）"
                ),
                "score": f"{summary['accuracy'] * 100:.1f}",
                "source": (
                    "本仓实测（同一批样本、同一模型、同一裁剪参数、同一判定口径）"
                ),
            }
        )
    return rows


def render_table(rows: Sequence[dict[str, str]]) -> str:
    """把那 7 行渲染成 markdown 表格。"""
    header = "| 方法 | 模型 | 数据集 | 裁剪参数 | 整体分 | 来源与口径 |"
    divider = "|---|---|---|---|---|---|"
    body = [
        "| {method} | {model} | {dataset} | {crop} | {score} | {source} |".format(**row)
        for row in rows
    ]
    return "\n".join([header, divider, *body])


def gate_params_snapshot(gate_params: Any = None) -> dict[str, Any]:
    """门控参数（连同取值来源）—— 落进报告，便于事后核对。

    ⚠️ **报的是本次实际用的那一套**（不是默认值）—— 工单 M7 的口径：
    「门控参数是运行时参数，取值来源必须被记录」。
    """

    return (gate_params or default_gate_params()).to_dict()


def elapsed(started: float) -> float:
    """已经过去的秒数。"""
    return time.time() - started

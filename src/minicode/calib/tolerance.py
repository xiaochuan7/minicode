"""标定用的常量：容差、可分辨安全系数、轮数与命中率阈值（工单 M5 / A-1）。

**1 px 的归位**（A-1 口径归位，用户已签字）
------------------------------------------------
:data:`CALIBRATION_TOLERANCE_PX` 仍是 **1.0 px**，**一个数都不许改** ——
但它**退回去只管一件事**：「两个候选标度能不能分开」（**可分辨性**）。
它**不再管**模型落点误差（那是命中率的事，见 :data:`PROBE_HIT_RADIUS_PX`）。

于是四组量各管一件事，**互不共用**：

* :data:`CALIBRATION_TOLERANCE_PX` —— **可分辨容差**（1.0 px）：
  判定标度时允许的最大误差。它回答「能不能分辨两种量纲」。
  配 :data:`DISCRIMINABILITY_SAFETY_FACTOR` 用（落点分歧要达到它的
  ``安全系数 × 容差`` 倍，才算探针**真能**把这个替代标度排除掉）。
* :data:`SCALE_EQUIVALENCE_TOLERANCE_PX` —— **落点等价容差**（4.0 px）：
  「换一个标度解释，落点会挪多远」的容差。它回答「选错标度会不会把落点挪出可接受范围」。
  取值来源：0~999 与 0~1000 在 2560 宽探针上的欧氏分歧 **2.87 px**，
  留**一倍余量**取 4.0 px。
* :data:`PROBE_HIT_RADIUS_PX` —— **命中半径**（48 px）：
  探针目标方块边长（96 px）的一半。预测的物理落点落在目标方块内即**命中**。
* :data:`OUT_OF_BOUNDS_TOLERANCE_PX` —— **越界容差**（2.0 px）：
  允许落点偏离物理表面边界的最大距离。它回答「点有没有超出桌面」。
  取值来自动作协议的唯一真值来源（冻结原型
  :data:`minicode.coord.coordinates.DEFAULT_OUT_OF_BOUNDS_TOLERANCE_PX`）。

再加两个**跑批口径**的常量（与上面四条互不共用）：

* :data:`CALIBRATION_ROUNDS` —— 一次标定问几轮（K 轮**独立**问）。
* :data:`CALIBRATION_MIN_HIT_RATE` —— K 轮命中率的下限（准入闸门用）。

工单 M5 AC 1 要求真实探针的余量 ≥ 2 倍（2560 宽探针上 0~999 与 0~1000
的欧氏分歧 2.87 px 对 1 px 容差），:data:`DISCRIMINABILITY_SAFETY_FACTOR`
就是把那条要求固化成判据。
"""

from __future__ import annotations

from minicode.coord.coordinates import DEFAULT_OUT_OF_BOUNDS_TOLERANCE_PX

__all__ = [
    "CALIBRATION_TOLERANCE_PX",
    "OUT_OF_BOUNDS_TOLERANCE_PX",
    "DISCRIMINABILITY_SAFETY_FACTOR",
    "SCALE_EQUIVALENCE_TOLERANCE_PX",
    "PROBE_HIT_RADIUS_PX",
    "CALIBRATION_ROUNDS",
    "CALIBRATION_MIN_HIT_RATE",
]

#: **可分辨容差**（物理像素）：判定标度时允许的最大误差。
#:
#: ⚠️ 它**只管可分辨性**（「两个候选标度能不能分开」），
#: **不管模型落点误差** —— 落点误差由 :data:`PROBE_HIT_RADIUS_PX` +
#: :data:`CALIBRATION_MIN_HIT_RATE` 判。**1.0 px 不许放松**。
CALIBRATION_TOLERANCE_PX = 1.0

#: **越界容差**（物理像素）：落点允许偏离物理表面边界的最大距离。
#: 与上面的标定容差是两个独立常量，不许共用。
OUT_OF_BOUNDS_TOLERANCE_PX = DEFAULT_OUT_OF_BOUNDS_TOLERANCE_PX

#: **可分辨安全系数**：落点分歧 ≥ 该系数 × 标定容差 才算可分辨。
DISCRIMINABILITY_SAFETY_FACTOR = 2.0

#: **落点等价容差**（物理像素）：换一个标度解释、落点最多挪多远仍算等价。
#:
#: 来源：0~999 与 0~1000 在 2560 宽探针上的欧氏分歧 **2.87 px**，
#: 留**一倍余量**取 4.0 px。与 :data:`CALIBRATION_TOLERANCE_PX` 互不共用。
SCALE_EQUIVALENCE_TOLERANCE_PX = 4.0

#: **命中半径**（物理像素）：探针目标方块边长（96 px）的一半。
#:
#: 命中判据：预测的物理落点与目标中心的距离 ≤ 它 → 命中。
PROBE_HIT_RADIUS_PX = 48

#: **一次标定问几轮**（K 轮独立问）。
#:
#: 逐字重发同一条请求在确定性模型下会拿到同一答案，所以每轮的提示词不同
#: （见 :mod:`minicode.calib.prompt` 的轮次轮换）。
#:
#: ⚠️ **K = 21**（A-1 原为 5）：准入门用的是**命中率**，而命中率是
#: **K 轮 × 目标数** 个预测估出来的比率 —— 分母小的时候它抖得厉害：
#: K=5 时 n=20，在真实命中率 p≈0.8 处标准误 ≈ **0.089** →
#: **跨运行翻过 0.6 阈值是常事**（M15 四连抽签：1.06 / 2.66 / 68.00 / 68.81 px）。
#: K=21 时 n=84 → 标准误 ≈ **0.043** → 翻转率从「常事」降到「罕见」。
#: 判据本身（命中率下限 0.6）**不许因为这个而放松** —— 修的是**估不稳**，
#: 不是**判太严**。
CALIBRATION_ROUNDS = 21

#: **K 轮命中率下限**：命中数 /（K 轮 × 目标数）低于它 → 视觉通道不可用。
CALIBRATION_MIN_HIT_RATE = 0.6

"""标定用的三个常量：两个容差 + 可分辨安全系数（工单 M5）。

**两个容差互不共用**（架构决策备忘 §1.4 与 §2.6 C-03）：

* :data:`CALIBRATION_TOLERANCE_PX` —— **标定容差**（1.0 px）：
  判定标度时允许的最大误差。它回答「能不能分辨两种量纲」。
* :data:`OUT_OF_BOUNDS_TOLERANCE_PX` —— **越界容差**（2.0 px）：
  允许落点偏离物理表面边界的最大距离。它回答「点有没有超出桌面」。
  取值来自动作协议的唯一真值来源（冻结原型
  :data:`minicode.coord.coordinates.DEFAULT_OUT_OF_BOUNDS_TOLERANCE_PX`）。

:data:`DISCRIMINABILITY_SAFETY_FACTOR` 是**可分辨安全系数**：
落点分歧要达到标定容差的这么多倍，才算探针**真能**把这个替代标度排除掉。
理由：模型自身的定位抖动就有 ±1 px 量级，分歧只比容差大一点点时，
探针并没有真的分辨出两种量纲 —— 这种情形按「等价」处理（进等价标度列表、
如实报「多解」），而不是假装能分辨。

工单 M5 AC 1 要求真实探针的余量 ≥ 2 倍（2560 宽探针上 0~999 与 0~1000
的欧氏分歧 2.87 px 对 1 px 容差），本常量就是把那条要求固化成判据。
"""

from __future__ import annotations

from minicode.coord.coordinates import DEFAULT_OUT_OF_BOUNDS_TOLERANCE_PX

__all__ = [
    "CALIBRATION_TOLERANCE_PX",
    "OUT_OF_BOUNDS_TOLERANCE_PX",
    "DISCRIMINABILITY_SAFETY_FACTOR",
]

#: **标定容差**（物理像素）：判定标度时允许的最大误差。
CALIBRATION_TOLERANCE_PX = 1.0

#: **越界容差**（物理像素）：落点允许偏离物理表面边界的最大距离。
#: 与上面的标定容差是两个独立常量，不许共用（架构备忘 §2.6 C-03）。
OUT_OF_BOUNDS_TOLERANCE_PX = DEFAULT_OUT_OF_BOUNDS_TOLERANCE_PX

#: **可分辨安全系数**：落点分歧 ≥ 该系数 × 标定容差 才算可分辨。
DISCRIMINABILITY_SAFETY_FACTOR = 2.0

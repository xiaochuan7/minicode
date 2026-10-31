"""``CandidateResult.acceptable`` 的单测（审查报告 Bug #4 / P1-5）。

原状（恒真 Bug）：``return self.max_error_px <= 0.0 or True`` ——
**永远为 True**，标定层最忌讳的静默放行（ruff 对 coord/ 整体豁免，
静态检查拦不住；全仓又没有任何调用方 → 从未被暴露）。

修法：把「接受与否」改成**显式给容差**的判定 ——
``acceptable(tolerance_px)`` = ``max_error_px <= tolerance_px``。
判定依据（容差）由调用方给，这个属性/方法自己**不再藏一个结论**。

⚠️ 这是 coord/ 冻结原型唯一的逻辑改动（夜班例外，报告 P1-5 单列）：
原代码语义上就是坏的（恒真），且无任何调用方，改动不影响既有行为。
"""

from __future__ import annotations

import unittest

from minicode.coord.calibrate import CandidateResult
from minicode.coord.coordinates import ModelScale


def _candidate(max_error_px: float) -> CandidateResult:
    return CandidateResult(
        scale=ModelScale.RELATIVE_0_1000,
        max_error_px=max_error_px,
        mean_error_px=max_error_px / 2.0,
        errors_px=(max_error_px,),
        predictions=((0.0, 0.0),),
    )


class AcceptableTests(unittest.TestCase):
    """超阈值必须拒绝（恒真 Bug 的回归钉子）。"""

    def test_error_within_tolerance_is_acceptable(self):
        self.assertTrue(_candidate(1.9).acceptable(2.0))

    def test_error_at_tolerance_is_acceptable(self):
        self.assertTrue(_candidate(2.0).acceptable(2.0))

    def test_error_beyond_tolerance_is_rejected(self):
        """修之前的形状：无论误差多大都返回 True。"""
        self.assertFalse(_candidate(2.1).acceptable(2.0))
        self.assertFalse(_candidate(9999.0).acceptable(2.0))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

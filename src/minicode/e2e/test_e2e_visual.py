"""**视觉通道的标定读数**（工单 A-1 的闸门读数）。

断言的对象是**读数里有没有真正的闸门**：``calibration_ok`` 的语义是
「落点等价族判定成功」，而那个族**按 4 px 定义** → **按定义恒为真**
（见 :mod:`minicode.calib.calibrate`）—— 只报它，等于报了一个**假绿灯**。
真正的闸门是 **K 轮命中率 ≥ 0.6**（准入判定）。

⚠️ 这些读数会进**任务上下文与端到端报告** —— 人据此判断「标定过没过」，
所以口径必须**在读数里就说清楚**。
"""

from __future__ import annotations

import unittest

from minicode.calib.admission import CalibrationAdmission
from minicode.calib.calibrate import (
    EQUIVALENCE_EQUIVALENT,
    SCALE_VERDICT_UNIQUE,
    CalibrationOutcome,
    CandidateEvaluation,
)
from minicode.coord.coordinates import ModelScale
from minicode.e2e.visual import calibration_readout


def _outcome(*, hit_rate: float) -> CalibrationOutcome:
    """一份标定结论（**只填读数用得到的字段**）。"""
    return CalibrationOutcome(
        detected_scale=ModelScale.ABSOLUTE_MODEL_INPUT,
        scale_verdict=SCALE_VERDICT_UNIQUE,
        landing_equivalence=EQUIVALENCE_EQUIVALENT,
        equivalent_scales=(ModelScale.ABSOLUTE_MODEL_INPUT,),
        candidates=(
            CandidateEvaluation(
                scale=ModelScale.ABSOLUTE_MODEL_INPUT,
                max_error_px=1.5,
                mean_error_px=0.5,
                out_of_bounds=False,
            ),
        ),
        rounds=5,
        hit_rate=hit_rate,
        #: ⚠️ **按定义恒为真**（族按 4 px 定义）—— 这正是它**不能当绿灯**的原因。
        calibration_ok=True,
    )


def _readout(hit_rate: float) -> dict:
    outcome = _outcome(hit_rate=hit_rate)
    admission = CalibrationAdmission(outcome).admit()
    best = outcome.candidate(outcome.detected_scale)
    return calibration_readout(outcome, admission, best, (1280, 800), None)


class CalibrationReadoutTests(unittest.TestCase):
    """读数里**必须**有真闸门，不能只有那个恒真的 ``ok``。"""

    def test_the_real_gate_is_in_the_readout(self):
        readout = _readout(1.0)
        self.assertTrue(readout["visual_channel_available"])
        self.assertTrue(readout["visual_channel_reason"])
        self.assertEqual(readout["rounds"], 5)
        self.assertEqual(readout["hit_rate"], 1.0)
        self.assertEqual(readout["min_hit_rate"], 0.6)

    def test_a_low_hit_rate_blocks_the_channel_and_is_visible(self):
        """命中率不达标 → **不可用**；读数里看得见（不是只报恒真的 ``ok``）。"""
        readout = _readout(0.2)
        self.assertFalse(readout["visual_channel_available"])
        self.assertIn("命中率", readout["visual_channel_reason"])
        # ⚠️ ``ok`` 仍然是 True —— 这正是它**不能当绿灯**的原因。
        self.assertTrue(readout["ok"])
        self.assertIn("按定义恒为真", readout["ok_is_not_the_gate"])

    def test_the_server_size_is_unknown_when_the_lookup_fails(self):
        """反查不出 → ``unknown``（**不填一个猜的数**）。"""
        readout = _readout(1.0)
        self.assertEqual(readout["server_model_input"], "unknown")
        self.assertEqual(readout["declared_model_input"], [1280, 800])

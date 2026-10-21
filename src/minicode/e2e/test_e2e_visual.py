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
from collections.abc import Sequence

from minicode.calib.admission import CalibrationAdmission
from minicode.calib.calibrate import (
    EQUIVALENCE_EQUIVALENT,
    SCALE_VERDICT_UNIQUE,
    CalibrationOutcome,
    CandidateEvaluation,
)
from minicode.coord.coordinates import ModelScale
from minicode.e2e.real import read_window_controls
from minicode.e2e.visual import (
    LazyUiaCandidateSource,
    MergedCandidateSource,
    calibration_readout,
)
from minicode.ground.candidates import GroundingCandidate


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


class MergedCandidateSourceTests(unittest.TestCase):
    """生产接线的**合并候选源**：**「判不了」≠「没有候选」**。

    ⚠️ 方案原文「接线点」一节的纪律：*判不了 ≠ 没有候选*。
    通道侧那两个口（主表 / 附加表）已经各自单测过；
    这里钉的是**生产接线用的那一个合并源** —— 它把两源包在一起，
    最容易把「读不出来」悄悄吞成「界面上没有」。
    """

    class _Source:
        def __init__(self, rows):
            self._rows = list(rows)

        def candidates(self, frame, description):
            return list(self._rows)

    class _Broken:
        def candidates(self, frame, description):
            raise OSError("源读不出来")

    def test_a_broken_main_source_falls_back_to_the_additional_one(self):
        """主表读不出来 → **退回附加表**（不抛、不假装没有候选）。"""
        additional = self._Source(
            [GroundingCandidate(number=1, text="甲", box=(0, 0, 20, 20), source="ocr")]
        )
        source = MergedCandidateSource(self._Broken(), additional)
        rows = source.candidates(None, "甲")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].source, "ocr")

    def test_both_sources_broken_is_a_source_error_not_no_candidates(self):
        """两源都读不出来 → **源报错**（异常往上抛）。

        ⚠️ 不许被吞成空列表：空列表在上层是「**没有候选**」，
        而真相是「**判不了**」—— 两者在追踪记录里必须分得开。
        """
        source = MergedCandidateSource(self._Broken(), self._Broken())
        with self.assertRaises(OSError):
            source.candidates(None, "甲")

    def test_the_lazy_uia_source_does_not_swallow_the_tree_error(self):
        """读树失败 → 异常往上抛（「树是空的」与「树读不出来」是两件事）。"""

        def _boom():
            raise OSError("UIA 树读不出来")

        source = LazyUiaCandidateSource(_boom)
        with self.assertRaises(OSError):
            source.candidates(None, "甲")

    def test_an_empty_but_readable_source_is_no_candidates(self):
        """两源都**读出来了**、只是没匹配上 → 空列表（不是源报错）。"""
        source = MergedCandidateSource(self._Source([]), self._Source([]))
        self.assertEqual(source.candidates(None, "甲"), [])


class _UiaElement:
    """一个 UIA 元素（只要名字，够用了）。"""

    def __init__(self, name: str):
        self.name = name


class ReadWindowControlsTests(unittest.TestCase):
    """**UIA 主表的读取口**：「树读不出来」与「树是空的」必须分得开。

    ⚠️ 这一层在**生产接线**上（:func:`minicode.e2e.real.read_window_controls`
    ← ``WindowsDesktopExecutor._uia_controls`` → 合并候选源）。
    读树失败若在这里被吞成空列表 → 上层收到的是「**没有候选**」，
    而真相是「**判不了**」—— 方案原文「接线点」一节明令禁止。
    """

    class _Port:
        """按控件类型给读数；``broken`` 里的类型读不出来，``empty`` = 树是空的。"""

        def __init__(self, *, broken: Sequence[str] = (), empty: bool = False):
            self.broken = set(broken)
            self.empty = empty
            self.asked: list[str] = []

        def find(self, request):
            control_type = request.control_type
            self.asked.append(control_type)
            if control_type in self.broken:
                raise OSError("UIA 查询超时")
            elements = () if self.empty else (_UiaElement(control_type),)
            return type("_Result", (), {"elements": elements})()

    def test_a_readable_but_empty_tree_is_no_candidates(self):
        """树**读出来了**、里面没有控件 → 空列表（不是源报错）。"""
        port = self._Port(empty=True)
        self.assertEqual(read_window_controls(port, object(), ("Button",)), [])

    def test_a_tree_that_cannot_be_read_raises(self):
        """树**读不出来** → 异常往上抛（上层据此记「候选源报错」）。"""
        port = self._Port(broken=("Button", "Custom"))
        with self.assertRaises(OSError):
            read_window_controls(port, object(), ("Button", "Custom"))

    def test_one_broken_control_type_does_not_hide_the_others(self):
        """局部失败**容忍**：其余类型照读（只是那一类没进来）。"""
        port = self._Port(broken=("Image",))
        controls = read_window_controls(port, object(), ("Button", "Image", "Custom"))
        self.assertEqual([control.name for control in controls], ["Button", "Custom"])


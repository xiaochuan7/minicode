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
    assert_server_kept_image_tokens,
)
from minicode.coord.coordinates import ModelScale
from minicode.e2e.real import read_window_controls
from minicode.e2e.visual import (
    LazyUiaCandidateSource,
    MergedCandidateSource,
    VisualChannel,
    calibration_readout,
    server_image_evidence_from_log,
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


class _AlwaysAvailable:
    """假准入端口（永远放行）。"""

    def admit(self):
        from minicode.action.ports import VisualAdmission

        return VisualAdmission(available=True, reason="测试替身：放行")


class _FakeRecord:
    """假请求日志（只有这条链路要用的字段）。"""

    def __init__(self, total_input_tokens: int):
        self.total_input_tokens = total_input_tokens
        self.effort = "low"


class _FakeClient:
    """假模型端口：只回 token 数（纯文字那一遍）。"""

    def __init__(self, input_tokens: int | None):
        self.last_prompt = "这是一张 Windows 桌面截图。"
        self._input_tokens = input_tokens
        self.calls = 0

    def complete(self, prompt, *, effort=None, max_tokens=2000, image_base64=None):
        self.calls += 1
        from minicode.model.client import ModelReply
        from minicode.model.effort import Effort

        return ModelReply(
            text="",
            model="fake",
            endpoint="fake://",
            effort=Effort.LOW,
            http_status=200,
            latency_ms=1.0,
            input_tokens=self._input_tokens,
            output_tokens=0,
        )


class _FakeSink:
    """假请求日志出口（只留最后一条）。"""

    def __init__(self, record):
        self.last = record


class CoordinateSystemTrustTests(unittest.TestCase):
    """**相等断言**接进生产链：服务端再缩了那张图 → **坐标系不可信**。

    ⚠️ 「能不能信这次请求的坐标系」由**相等断言**判（唯一真值来源）；
    **盒子反查只是证据**，不作准入依据。
    """

    #: 声明 1280×800 → 期望图像 token = 46×29 = 1334。
    DECLARED = (1280, 800)

    def test_equal_tokens_mean_the_server_kept_the_size(self):
        """观测 == 期望 → 相等断言通过 → 坐标系可信。"""
        client = _FakeClient(349)
        sink = _FakeSink(_FakeRecord(349 + 1334))
        box, assertion = server_image_evidence_from_log(client, sink, self.DECLARED)
        self.assertTrue(assertion.server_kept_size)
        self.assertEqual(assertion.observed_image_tokens, 1334)
        self.assertEqual(assertion.expected_image_tokens, 1334)
        # 盒子只是**证据**（诊断），与闸门分开。
        self.assertIsNotNone(box)

    def test_a_shrunk_image_makes_the_coordinate_system_untrustworthy(self):
        """服务端再缩了那张图 → 坐标系不可信 → **视觉通道不可用**。"""
        client = _FakeClient(349)
        sink = _FakeSink(_FakeRecord(349 + 851))
        _box, assertion = server_image_evidence_from_log(client, sink, self.DECLARED)
        self.assertFalse(assertion.server_kept_size)

    def test_an_unmeasured_request_stays_unknown(self):
        """量不出来 → ``None``（**不猜**）→ 不拦（不静默判成不可信）。"""
        client = _FakeClient(None)
        sink = _FakeSink(_FakeRecord(1683))
        _box, assertion = server_image_evidence_from_log(client, sink, self.DECLARED)
        self.assertIsNone(assertion.server_kept_size)

    def _channel(self, assertion) -> VisualChannel:
        """一个**标定已就绪**的视觉通道（不真调模型）。"""
        channel = VisualChannel(out_dir=".")
        channel._calib = {
            "admission": _AlwaysAvailable(),
            "image_token_assertion": assertion,
        }
        return channel

    def test_the_admission_gate_blocks_an_untrustworthy_coordinate_system(self):
        """坐标系不可信 → 准入判定**不放行**（零动作下发）。"""
        channel = self._channel(
            assert_server_kept_image_tokens(851, self.DECLARED)
        )
        verdict = channel.admission_port().admit()
        self.assertFalse(verdict.available)
        self.assertIn("坐标系不可信", verdict.reason)

    def test_the_gate_lets_a_trustworthy_coordinate_system_through(self):
        """坐标系可信 → 准入判定照常（这里放行）。"""
        channel = self._channel(
            assert_server_kept_image_tokens(1334, self.DECLARED)
        )
        self.assertTrue(channel.admission_port().admit().available)

    def test_the_locator_fails_explicitly_when_untrustworthy(self):
        """坐标系不可信 → 视觉定位口**显式失败**（不按声明尺寸换算）。"""
        channel = self._channel(
            assert_server_kept_image_tokens(851, self.DECLARED)
        )
        result = channel.locator().locate(None)
        self.assertFalse(result.ok)
        self.assertIn("坐标系不可信", result.error_message)

    def test_the_readout_records_the_assertion(self):
        """读数里看得见「坐标系可信吗」（报告据此判断）。"""
        outcome = _outcome(hit_rate=1.0)
        admission = CalibrationAdmission(outcome).admit()
        best = outcome.candidate(outcome.detected_scale)
        assertion = assert_server_kept_image_tokens(851, self.DECLARED)
        readout = calibration_readout(
            outcome, admission, best, self.DECLARED, None, assertion
        )
        self.assertFalse(readout["coordinate_system_trusted"])
        self.assertEqual(readout["image_token_assertion"]["observed_image_tokens"], 851)
        self.assertEqual(
            readout["image_token_assertion"]["expected_image_tokens"], 1334
        )


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


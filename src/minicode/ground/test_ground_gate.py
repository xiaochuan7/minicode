"""**精定位门控、失败回落与坐标口径统一**的外部行为单测（工单 M7）。

断言的对象是**对外可见的契约字段**：门控判定（命中 / 命中原因 / 不可判定）、
是否开启 crop 精定位、是否回落与回落原因、追踪记录里的四个埋点字段、
评测报告的命中率与回落率，以及各家评测脚本的坐标口径转换。

判据来源：架构决策备忘 §2.6 的 **C-16**（crop 精定位必须门控 + 必须有回落到
粗定位结果的路径）、**C-04**（分母来自标定，两套命名不许混用）、
工单 M7 的 AC 1~AC 10。
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

from minicode.action.errors import ErrorCode
from minicode.ground.gate import (
    FallbackCause,
    GateCondition,
    GateInputs,
    GateMode,
    GateParams,
    GateReason,
    build_gate_report,
    default_gate_params,
    evaluate_gate,
)
from minicode.ground.test_ground_two_stage import (
    _detail_frame,
    _reply,
    _StubModel,
    _text_reply,
)

#: 被测包源码目录（源码级判据用）。
_GROUND_DIR = Path(__file__).resolve().parent

#: 仓库根（源码级判据用）。
_REPO_ROOT = Path(__file__).resolve().parents[3]


def _inputs(**overrides) -> GateInputs:
    """一份「四条条件都不满足」的夹具（各条只被单独改动一次）。"""
    base = {
        "coarse_point": (1000.0, 800.0),
        "coarse_out_of_bounds": False,
        "target_radius_px": None,
        "coarse_confidence": None,
        "second_coarse_point": None,
        "surface_size": (2560, 1600),
    }
    base.update(overrides)
    return GateInputs(**base)


def _grounder(model, *, gate_mode: GateMode = GateMode.ON_DEMAND, **kwargs):
    from minicode.coord.coordinates import ModelScale
    from minicode.ground.channel import TwoStageGrounder

    params = {
        "scale": kwargs.pop("scale", ModelScale.RELATIVE_0_1000),
        "model_input_size": kwargs.pop("model_input_size", (2560, 1600)),
        "full_screen_size": kwargs.pop("full_screen_size", (2560, 1600)),
        "effort": kwargs.pop("effort", "low"),
    }
    params.update(kwargs)
    return TwoStageGrounder(model, gate_mode=gate_mode, **params)


def _request(frame, *, description: str = "开始"):
    from minicode.action.ports import VisualLocateRequest

    return VisualLocateRequest(
        target_description=description,
        frame=frame,
        surface=(0, 0, 2560, 1600),
    )


class GateDecisionTests(unittest.TestCase):
    """AC 1 · 四条触发条件各自独立可触发；都不满足时不开启。"""

    def test_each_condition_triggers_the_gate_on_its_own(self):
        cases = {
            # 只满足「目标占比极小」：目标 16×16 px，占 2560×1600 的 0.006%。
            GateReason.TARGET_TINY: _inputs(target_radius_px=8.0),
            # 只满足「粗定位输出为空或越界」。
            GateReason.COARSE_INVALID: _inputs(coarse_point=None, target_radius_px=100.0),
            # 只满足「粗定位置信度低」。
            GateReason.LOW_CONFIDENCE: _inputs(
                coarse_confidence=0.2, target_radius_px=100.0
            ),
            # 只满足「两次独立粗定位互相不一致」（间距 200 px > 阈值）。
            GateReason.COARSE_INCONSISTENT: _inputs(
                second_coarse_point=(1200.0, 800.0), target_radius_px=100.0
            ),
        }
        for reason, inputs in cases.items():
            with self.subTest(reason=reason):
                decision = evaluate_gate(inputs)
                self.assertTrue(decision.hit)
                self.assertIn(reason, decision.reasons)

    def test_gate_does_not_hit_when_no_condition_is_met(self):
        decision = evaluate_gate(
            _inputs(
                target_radius_px=100.0,
                coarse_confidence=0.9,
                second_coarse_point=(1005.0, 803.0),
            )
        )
        self.assertFalse(decision.hit)
        self.assertEqual(decision.reasons, ())
        self.assertEqual(decision.undecidable, ())

    def test_area_ratio_is_undecidable_when_coarse_gives_only_a_point(self):
        """只给点不给框 → 占比**不可判定**：不触发、也不否决其余三条。"""
        decision = evaluate_gate(_inputs(target_radius_px=None))
        self.assertFalse(decision.hit)
        self.assertIn(GateCondition.TARGET_AREA_RATIO, decision.undecidable)
        self.assertNotIn(GateReason.TARGET_TINY, decision.reasons)

    def test_undecidable_area_ratio_does_not_veto_the_other_conditions(self):
        """不可判定的占比**不否决**其余三条：置信度低照样开门控。"""
        decision = evaluate_gate(_inputs(coarse_confidence=0.1))
        self.assertTrue(decision.hit)
        self.assertEqual(decision.reasons, (GateReason.LOW_CONFIDENCE,))
        self.assertIn(GateCondition.TARGET_AREA_RATIO, decision.undecidable)

    def test_decision_is_pure_and_repeatable(self):
        inputs = _inputs(target_radius_px=8.0)
        first = evaluate_gate(inputs)
        second = evaluate_gate(inputs)
        self.assertEqual(first, second)


class GateParamsTests(unittest.TestCase):
    """AC 2 · 门控参数是运行时参数，取值来源被记录。"""

    def test_default_area_ratio_carries_the_dataset_statistic_label(self):
        params = default_gate_params()
        self.assertIn("数据集统计", params.area_ratio_source)
        self.assertIn("未验证", params.area_ratio_source)
        self.assertIn("ScreenSpot-Pro", params.area_ratio_source)
        self.assertEqual(params.area_ratio_threshold, 0.0007)

    def test_threshold_is_a_runtime_parameter(self):
        """改阈值就改判定 —— 阈值不是写死在校验逻辑里的常量。"""
        loose = GateParams(area_ratio_threshold=0.5)
        decision = evaluate_gate(_inputs(target_radius_px=100.0), loose)
        self.assertTrue(decision.hit)
        self.assertIn(GateReason.TARGET_TINY, decision.reasons)

    def test_every_occurrence_of_the_area_ratio_stat_is_labelled(self):
        """凡出现 0.07% 必须标注它的来源（工单 M7 AC 2）。

        两档判据：

        * **本项目自己的口径文档与代码**（阈值住在的地方）：必须同时带
          「数据集统计」与「未验证」；
        * **派生研究文档**：必须带「数据集统计」（那里它是描述性的统计，
          不是阈值 —— 写成「未验证的阈值」反而是错的）。

        ⚠️ `docs/research/bottom-drafts/` **不在扫描范围内**：那些底稿的行号
        被全仓逐行引用（`底稿 NN Lnnn`），改一个字就断链。
        """
        strict = [
            *sorted(_GROUND_DIR.glob("*.py")),
            *sorted((_REPO_ROOT / "docs" / "tickets").glob("*.md")),
            _REPO_ROOT / "docs" / "00-现状.md",
            *sorted((_REPO_ROOT / "docs" / "reviews").glob("*.md")),
            *sorted((_REPO_ROOT / "docs" / "adr").glob("*.md")),
        ]
        loose = [
            _REPO_ROOT / "docs" / "spec.md",
            *sorted((_REPO_ROOT / "docs" / "research").glob("0*.md")),
        ]
        offenders: list[str] = []
        for path, markers in [(path, ("数据集统计", "未验证")) for path in strict] + [
            (path, ("数据集统计",)) for path in loose
        ]:
            if path.name.startswith("test_") or not path.exists():
                continue
            text = path.read_text(encoding="utf-8")
            for match in re.finditer(r"0\.07\s*%", text):
                window = text[max(0, match.start() - 200) : match.end() + 200]
                if any(marker not in window for marker in markers):
                    offenders.append(f"{path.relative_to(_REPO_ROOT)}: {match.group(0)}")
        self.assertEqual(offenders, [])


class GatePipelineTests(unittest.TestCase):
    """AC 1 / AC 3 · 门控在通道里的行为：不开就不开、开就开、失败必回落。"""

    def test_gate_miss_skips_the_refinement_and_saves_the_model_call(self):
        model = _StubModel(_reply(390.625, 500.0))
        grounder = _grounder(model)
        result = grounder.locate(_request(_detail_frame(2560, 1600)))
        self.assertTrue(result.ok)
        self.assertEqual(len(model.calls), 1)
        self.assertFalse(result.gate_hit)
        self.assertFalse(result.refine_enabled)
        self.assertIsNone(result.crop_plan)
        self.assertIsNotNone(result.landing_point)

    def test_gate_hit_runs_the_refinement(self):
        """门控命中 → 开启 crop 精定位（裁剪窗口 + crop 内精定位）。"""
        model = _StubModel(_reply(390.625, 500.0), _reply(500.0, 500.0))
        grounder = _grounder(model, target_radius_px=8.0)
        result = grounder.locate(_request(_detail_frame(2560, 1600)))
        self.assertTrue(result.gate_hit)
        self.assertTrue(result.refine_enabled)
        self.assertEqual(result.gate_reasons, (GateReason.TARGET_TINY.value,))
        self.assertIsNotNone(result.crop_plan)
        self.assertEqual(len(model.calls), 2)

    def test_coarse_empty_opens_a_whole_screen_second_pass(self):
        """粗定位输出为空 → 开的是**整屏第二遍**，不是围绕粗点的裁剪窗口。"""
        model = _StubModel(_text_reply("我看不清那个按钮"), _reply(390.625, 500.0))
        grounder = _grounder(model)
        result = grounder.locate(_request(_detail_frame(2560, 1600)))
        self.assertEqual(len(model.calls), 2)
        # 第二次送的还是**整屏**（送裁剪窗口的话像素必然不同）。
        self.assertEqual(model.calls[1]["image_base64"], model.calls[0]["image_base64"])
        # 但**不是逐字重发**同一条请求（带上了「上一次没能给出可用的位置」）。
        self.assertNotEqual(model.calls[1]["prompt"], model.calls[0]["prompt"])
        self.assertIsNone(result.crop_plan)
        self.assertIn(GateReason.COARSE_INVALID.value, result.gate_reasons)
        self.assertEqual([echo.stage for echo in result.echoes], ["whole_screen"])
        self.assertIsNotNone(result.landing_point)

    def test_coarse_out_of_bounds_opens_a_whole_screen_second_pass(self):
        model = _StubModel(_reply(5000.0, 5000.0), _reply(390.625, 500.0))
        grounder = _grounder(model)
        result = grounder.locate(_request(_detail_frame(2560, 1600)))
        self.assertEqual(len(model.calls), 2)
        self.assertEqual(model.calls[1]["image_base64"], model.calls[0]["image_base64"])
        self.assertIsNone(result.crop_plan)
        self.assertIn(GateReason.COARSE_INVALID.value, result.gate_reasons)
        self.assertEqual([echo.stage for echo in result.echoes], ["coarse", "whole_screen"])

    def test_whole_screen_second_pass_is_not_counted_as_refinement(self):
        """整屏第二遍**不是 crop 精定位** → 不进「开启精定位的样本数」。"""
        model = _StubModel(_text_reply("我看不清那个按钮"), _reply(390.625, 500.0))
        grounder = _grounder(model)
        result = grounder.locate(_request(_detail_frame(2560, 1600)))
        self.assertTrue(result.gate_hit)
        self.assertFalse(result.refine_enabled)

    def test_coarse_unusable_and_second_pass_failed_reports_locate_failed(self):
        """粗定位本身不可用 → 没有可回落的结果 → **报定位失败**，不落点。"""
        model = _StubModel(_text_reply("我看不清"), _text_reply("还是看不清"))
        grounder = _grounder(model)
        result = grounder.locate(_request(_detail_frame(2560, 1600)))
        self.assertFalse(result.ok)
        self.assertEqual(result.error_code, ErrorCode.LOCATE_FAILED)
        self.assertIsNone(result.landing_point)
        self.assertFalse(result.fallback)

    def test_refine_failure_falls_back_to_coarse_and_records_the_cause(self):
        model = _StubModel(_reply(390.625, 500.0), _text_reply("我看不清那个按钮"))
        grounder = _grounder(model, target_radius_px=8.0)
        result = grounder.locate(_request(_detail_frame(2560, 1600)))
        self.assertTrue(result.ok)
        self.assertTrue(result.fallback)
        self.assertEqual(result.fallback_cause, FallbackCause.NO_OUTPUT.value)
        self.assertTrue(result.fallback_reason)
        self.assertAlmostEqual(
            result.landing_point[0], result.coarse_point[0], places=6
        )

    def test_refine_out_of_bounds_falls_back_to_coarse(self):
        model = _StubModel(_reply(390.625, 500.0), _reply(9000.0, 9000.0))
        grounder = _grounder(model, target_radius_px=8.0)
        result = grounder.locate(_request(_detail_frame(2560, 1600)))
        self.assertTrue(result.fallback)
        self.assertEqual(result.fallback_cause, FallbackCause.OUT_OF_BOUNDS.value)

    def test_refine_deviating_from_coarse_falls_back_to_coarse(self):
        """精定位结果**明显偏离粗定位** → 回落（治「选中相邻同类控件」）。"""
        model = _StubModel(_reply(390.625, 500.0), _reply(900.0, 500.0))
        grounder = _grounder(model, target_radius_px=8.0)
        result = grounder.locate(_request(_detail_frame(2560, 1600)))
        self.assertTrue(result.fallback)
        self.assertEqual(result.fallback_cause, FallbackCause.DEVIATION.value)
        self.assertIn("偏离", result.fallback_reason)
        self.assertAlmostEqual(
            result.landing_point[0], result.coarse_point[0], places=6
        )

    def test_refine_close_to_coarse_is_kept(self):
        """偏离在阈值内 → **不回落**（精定位的收益要留住）。"""
        model = _StubModel(_reply(390.625, 500.0), _reply(520.0, 500.0))
        grounder = _grounder(model, target_radius_px=8.0)
        result = grounder.locate(_request(_detail_frame(2560, 1600)))
        self.assertFalse(result.fallback)
        self.assertEqual(result.fallback_cause, "")
        self.assertNotAlmostEqual(
            result.landing_point[0], result.coarse_point[0], places=3
        )

    def test_gate_off_always_refines(self):
        """门控关（工单 M8 的对照组）：无条件做第二遍精定位。"""
        model = _StubModel(_reply(390.625, 500.0), _reply(500.0, 500.0))
        grounder = _grounder(model, gate_mode=GateMode.ALWAYS)
        result = grounder.locate(_request(_detail_frame(2560, 1600)))
        self.assertEqual(len(model.calls), 2)
        self.assertTrue(result.refine_enabled)
        self.assertIsNotNone(result.crop_plan)


class SecondOpinionTests(unittest.TestCase):
    """C-16 的「两次粗定位不一致」条件：**给了端口才判得了**。

    工单 M7 记下的现状是「生产接线拿不到第二个粗定位结果 → 该条件判不了」；
    工单 M8 的评测需要一个**可判定**的一致性条件，于是给通道加了一个
    **可选的第二次独立粗定位**端口（工单 M8）。
    """

    def test_without_the_hook_the_condition_is_undecidable(self):
        model = _StubModel(_reply(390.625, 500.0))
        grounder = _grounder(model, gate_mode=GateMode.ON_DEMAND)
        result = grounder.locate(_request(_detail_frame(2560, 1600)))
        self.assertIn("consistency", result.gate_undecidable)
        self.assertNotIn("coarse_inconsistent", result.gate_reasons)

    def test_the_second_opinion_counts_as_a_model_call(self):
        """第二次独立粗定位**本身就是一次模型调用** —— 必须记进 ``model_calls``。

        ⚠️ 不记它 →「平均模型调用数/目标」这条验收线**少算一次**
        （同一份报告里两个「模型调用数」对不上 → 两个数没法比）。
        """
        model = _StubModel(_reply(390.625, 500.0))
        grounder = _grounder(
            model,
            gate_mode=GateMode.ON_DEMAND,
            second_opinion=lambda frame, target: (390.625, 500.0),
        )
        result = grounder.locate(_request(_detail_frame(2560, 1600)))
        # 粗定位 1 次 + 第二次独立粗定位 1 次（门控没命中 → 没有精定位）。
        self.assertEqual(result.model_calls, 2)

    def test_without_the_hook_no_extra_model_call_is_counted(self):
        """没给端口 → **没有**第二次粗定位 → 不多记调用数。"""
        model = _StubModel(_reply(390.625, 500.0))
        grounder = _grounder(model, gate_mode=GateMode.ON_DEMAND)
        result = grounder.locate(_request(_detail_frame(2560, 1600)))
        self.assertEqual(result.model_calls, 1)

    def test_disagreeing_second_opinion_hits_the_gate(self):
        """第二次粗定位与第一次差得远 → 门控命中（开精定位）。

        第一次粗定位给的是 0~1000 量纲下的 390.625（= 1000 物理 px），
        第二次给 700（= 1792 物理 px）→ 相距 **792 物理 px** > 容差 128 px
        → 命中。**距离是通道换算出来的**（端口给的是模型自己的量纲）。
        """
        model = _StubModel(_reply(390.625, 500.0), _reply(500.0, 500.0))
        grounder = _grounder(
            model,
            gate_mode=GateMode.ON_DEMAND,
            second_opinion=lambda frame, target: (700.0, 500.0),
        )
        result = grounder.locate(_request(_detail_frame(2560, 1600)))
        self.assertIn("coarse_inconsistent", result.gate_reasons)
        self.assertNotIn("consistency", result.gate_undecidable)
        self.assertTrue(result.refine_enabled)

    def test_disagreement_just_under_the_tolerance_does_not_hit(self):
        """相距刚好在容差以内 → 不命中（**换算归通道**这条契约被钉住）。"""
        model = _StubModel(_reply(390.625, 500.0))
        # 390.625 + 50（0~1000 量纲）= 440.625 → 相距 50/1000×2560 = 128 物理 px
        # = 容差 → 不大于容差 → 不命中。
        grounder = _grounder(
            model,
            gate_mode=GateMode.ON_DEMAND,
            second_opinion=lambda frame, target: (440.625, 500.0),
        )
        result = grounder.locate(_request(_detail_frame(2560, 1600)))
        self.assertNotIn("coarse_inconsistent", result.gate_reasons)
        self.assertNotIn("consistency", result.gate_undecidable)

    def test_agreeing_second_opinion_closes_the_gate(self):
        """两次一致 → 这条条件不触发（门控仍可由别的条件命中）。"""
        model = _StubModel(_reply(390.625, 500.0))
        grounder = _grounder(
            model,
            gate_mode=GateMode.ON_DEMAND,
            second_opinion=lambda frame, target: (390.625, 500.0),
        )
        result = grounder.locate(_request(_detail_frame(2560, 1600)))
        self.assertNotIn("coarse_inconsistent", result.gate_reasons)
        self.assertNotIn("consistency", result.gate_undecidable)
        self.assertFalse(result.refine_enabled)

    def test_failing_second_opinion_leaves_it_undecidable(self):
        """第二次粗定位失败 → 照旧记「不可判定」（不静默当成一致）。"""

        def _boom(frame, target):
            raise RuntimeError("第二次粗定位炸了")

        model = _StubModel(_reply(390.625, 500.0))
        grounder = _grounder(
            model, gate_mode=GateMode.ON_DEMAND, second_opinion=_boom
        )
        result = grounder.locate(_request(_detail_frame(2560, 1600)))
        self.assertIn("consistency", result.gate_undecidable)

    def test_hook_returns_raw_model_coordinates(self):
        """端口给的是**模型自己那个坐标系**里的原始坐标 → 换算由通道做。

        端口收到的是**那一帧画面**与**目标描述**；它给的数值不被端口自己换算
        （换算在通道的适配器里做，**坐标换算链路只有一个真值来源**）。
        """
        seen: list[tuple] = []

        def _hook(frame, target):
            seen.append((frame.width, frame.height, target))
            return (500.0, 500.0)

        model = _StubModel(_reply(390.625, 500.0))
        grounder = _grounder(
            model, gate_mode=GateMode.ON_DEMAND, second_opinion=_hook
        )
        grounder.locate(_request(_detail_frame(2560, 1600)))
        self.assertEqual(len(seen), 1)
        self.assertEqual(seen[0][:2], (2560, 1600))
        self.assertEqual(seen[0][2], "开始")

    def test_second_opinion_sees_the_same_image_as_the_coarse_level(self):
        """第二次粗定位必须看到**同一张图**（整屏那一级送模型的那张）。

        ⚠️ 否则两次的数在**不同的空间**里：第一次问的是**缩到声明尺寸**那张图，
        第二次问的是**整屏原图**，而换算用的是同一套几何 → 「两次是否一致」
        比的是**两个没法比的数**（这条门控条件就成了空判）。
        """
        seen: list[tuple[int, int]] = []

        def _hook(frame, target):
            seen.append((frame.width, frame.height))
            return (390.625, 500.0)

        model = _StubModel(_reply(390.625, 500.0))
        grounder = _grounder(
            model,
            gate_mode=GateMode.ON_DEMAND,
            second_opinion=_hook,
            model_input_size=(1280, 800),
        )
        result = grounder.locate(_request(_detail_frame(2560, 1600)))
        self.assertEqual(len(seen), 1)
        coarse = next(echo for echo in result.echoes if echo.stage == "coarse")
        self.assertEqual(seen[0], (coarse.sent_width, coarse.sent_height))
        self.assertEqual(seen[0], (1280, 800))

    def test_second_opinion_prompt_asks_for_the_same_quantity(self):
        """两次粗定位必须问**同一个量**（这张图的像素坐标），否则没法比。"""
        from minicode.ground.prompt import second_opinion_prompt

        prompt = second_opinion_prompt("开始", image_size=(1280, 800))
        self.assertIn("1280×800", prompt)
        self.assertIn("像素坐标", prompt)

    def test_second_opinion_prompt_without_a_size_still_asks_for_pixels(self):
        """不给尺寸时也要问像素坐标（**问的量不能变**）。"""
        from minicode.ground.prompt import second_opinion_prompt

        prompt = second_opinion_prompt("开始")
        self.assertIn("像素坐标", prompt)

    def test_second_point_without_a_coarse_point_is_undecidable(self):
        """第一次粗定位为空、第二次有点 → **比不了** → 记「不可判定」（不静默）。

        ⚠️ 这一支以前是静默的（既不记命中也不记不可判定）；工单 M8 的端口
        让它可达，按 README 的口径必须显式记。
        """
        model = _StubModel(_reply(5000.0, 5000.0))
        grounder = _grounder(
            model,
            gate_mode=GateMode.ON_DEMAND,
            second_opinion=lambda frame, target: (500.0, 500.0),
        )
        # 粗定位**越界** → coarse_point 为 None（有第二次结果也比不了）。
        result = grounder.locate(_request(_detail_frame(2560, 1600)))
        self.assertIn("consistency", result.gate_undecidable)


class GateInstrumentationTests(unittest.TestCase):
    """AC 4 · 埋点：四个字段齐 + 报告口径写死 + 与分数同一批样本。"""

    def test_trace_carries_the_four_gate_fields(self):
        from minicode.action.fakes import (
            FakeCaptureSource,
            FakeControlTree,
            FakeInputInjector,
            FakeWindowController,
        )
        from minicode.action.ports import LocateResult, VisualAdmission
        from minicode.action.records import ListTraceSink, build_record
        from minicode.action.seam import ActionExecutionSeam, SeamRequest

        class _CoverageFailureLocator:
            def locate(self, request):
                return LocateResult(
                    error_code=ErrorCode.TARGET_NOT_FOUND,
                    error_message="控件树里找不到这个目标",
                    failure_type="coverage",
                )

        class _AllowingAdmission:
            def admit(self):
                return VisualAdmission(available=True, reason="测试替身：放行")

        model = _StubModel(_reply(390.625, 500.0))
        sink = ListTraceSink()
        seam = ActionExecutionSeam(
            capture_sources=[FakeCaptureSource(2560, 1600)],
            tree=FakeControlTree(),
            injector=FakeInputInjector(),
            window_controller=FakeWindowController(),
            trace_sink=sink,
            locator=_CoverageFailureLocator(),
            visual_admission=_AllowingAdmission(),
            visual_locator=_grounder(model),
        )
        result = seam.run(SeamRequest(target_description="开始"))
        self.assertTrue(result.ok, msg=result.result.error_message)
        trace = sink.traces[0]
        # 门控命中（是 / 否）+ 命中原因 + 是否回落 + 回落原因：四个字段都在。
        self.assertFalse(trace.gate_hit)
        self.assertEqual(trace.gate_reasons, ())
        # 占比不可判定要**显式可见**（不是一个空元组蒙混过去）。
        self.assertIn("target_area_ratio", trace.gate_undecidable)
        self.assertFalse(trace.fallback)
        self.assertEqual(trace.fallback_reason, "")
        payload = build_record(trace).to_dict()
        for key in (
            "gate_hit",
            "gate_reasons",
            "gate_undecidable",
            "refine_enabled",
            "fallback",
            "fallback_reason",
            "fallback_cause",
        ):
            self.assertIn(key, payload)
        self.assertFalse(payload["refine_enabled"])

    def test_gate_report_uses_the_same_sample_set_as_the_score(self):
        from minicode.ground.bench import gate_report

        outcomes = [
            _outcome(gate_hit=True, refine_enabled=True, fallback=False, reasons=("target_tiny",)),
            _outcome(gate_hit=True, refine_enabled=True, fallback=True, reasons=("target_tiny",)),
            _outcome(gate_hit=False, refine_enabled=False, fallback=False, reasons=()),
        ]
        report = gate_report(outcomes)
        self.assertEqual(report.samples, len(outcomes))
        self.assertEqual(report.refine_enabled, 2)
        self.assertAlmostEqual(report.hit_rate, 2 / 3)
        self.assertEqual(report.fallbacks, 1)
        self.assertAlmostEqual(report.fallback_rate, 1 / 2)
        self.assertEqual(report.reason_distribution, {"target_tiny": 2})


def _outcome(*, gate_hit, refine_enabled, fallback, reasons):
    from minicode.ground.bench import TargetOutcome

    return TargetOutcome(
        name="目标",
        size_px=(10, 10),
        gate_hit=gate_hit,
        gate_reasons=tuple(reasons),
        refine_enabled=refine_enabled,
        fallback=fallback,
        fallback_cause=FallbackCause.DEVIATION.value if fallback else "",
    )


class GateReportTests(unittest.TestCase):
    """AC 4 · 报告本身的口径。"""

    def test_report_exposes_the_four_numbers(self):
        from minicode.ground.bench import gate_report

        outcomes = [
            _outcome(gate_hit=True, refine_enabled=True, fallback=False, reasons=("target_tiny",))
        ]
        report = gate_report(outcomes)
        payload = report.to_dict()
        self.assertEqual(
            set(payload) >= {"samples", "hit_rate", "fallback_rate", "reason_distribution"},
            True,
        )

    def test_empty_sample_set_has_no_rate(self):
        report = build_gate_report([])
        self.assertEqual(report.samples, 0)
        self.assertEqual(report.hit_rate, 0.0)
        self.assertEqual(report.fallback_rate, 0.0)


class ChannelSwitchTests(unittest.TestCase):
    """AC 10 · 门控不是切视觉的理由：切视觉仍只认覆盖失败。"""

    def test_gate_never_switches_channel(self):
        """UIA 通道**选择失败**（不是覆盖失败）时，视觉通道一次都不许被调用。"""
        from minicode.action.fakes import (
            FakeCaptureSource,
            FakeControlTree,
            FakeInputInjector,
            FakeWindowController,
        )
        from minicode.action.ports import LocateResult
        from minicode.action.seam import ActionExecutionSeam, SeamRequest

        class _SelectionFailureLocator:
            def locate(self, request):
                return LocateResult(
                    error_code=ErrorCode.AMBIGUOUS_TARGET,
                    error_message="候选不唯一",
                    failure_type="selection",
                )

        class _ExplodingVisualLocator:
            def locate(self, request):  # pragma: no cover - 不许被调用
                raise AssertionError("门控不是切视觉的理由：视觉通道不该被调用")

        seam = ActionExecutionSeam(
            capture_sources=[FakeCaptureSource(2560, 1600)],
            tree=FakeControlTree(),
            injector=FakeInputInjector(),
            window_controller=FakeWindowController(),
            locator=_SelectionFailureLocator(),
            visual_locator=_ExplodingVisualLocator(),
        )
        result = seam.run(SeamRequest(target_description="开始"))
        self.assertFalse(result.ok)
        self.assertEqual(result.result.error_code, ErrorCode.AMBIGUOUS_TARGET)


class GateSourceTests(unittest.TestCase):
    """AC 9 · 门控的可核参照与失败回落路径。"""

    def test_gate_module_cites_the_reference_implementation(self):
        """实现说明里必须点名可核参照（AC 9）。"""
        text = (_GROUND_DIR / "gate.py").read_text(encoding="utf-8")
        self.assertIn("cursor_ccf.py", text)
        self.assertIn("LufeMC/gui-g2-3b-ccf", text)

    def test_fallback_path_exists(self):
        """失败回落路径：精定位失败必回落，且回落原因可判定。"""
        model = _StubModel(_reply(390.625, 500.0), _text_reply("我看不清那个按钮"))
        grounder = _grounder(model, target_radius_px=8.0)
        result = grounder.locate(_request(_detail_frame(2560, 1600)))
        self.assertTrue(result.fallback)
        self.assertEqual(result.fallback_cause, "no_output")

    def test_gate_module_has_no_hardcoded_denominators(self):
        """C-04：门控包不许出现写死的 1000 / 999（那是模型量纲分母，不是门控的东西）。"""
        offenders: list[str] = []
        for path in sorted(_GROUND_DIR.glob("*.py")):
            if path.name.startswith("test_"):
                continue
            text = _strip_comments_and_strings(path.read_text(encoding="utf-8"))
            for literal in ("1000", "999"):
                if literal in text:
                    offenders.append(f"{path.name}: {literal}")
        self.assertEqual(offenders, [])


def _strip_comments_and_strings(text: str) -> str:
    text = re.sub(r"\"\"\"(?:.|\n)*?\"\"\"", "", text)
    text = re.sub(r"'''(?:.|\n)*?'''", "", text)
    text = re.sub(r"#.*", "", text)
    text = re.sub(r"\"(?:\\.|[^\"\\])*\"", '""', text)
    text = re.sub(r"'(?:\\.|[^'\\])*'", "''", text)
    return text


if __name__ == "__main__":
    unittest.main()

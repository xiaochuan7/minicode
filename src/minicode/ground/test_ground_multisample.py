"""**多采样 + 放大 + 标记图**的外部行为单测。

盯的是**对外可见的契约**：

* **T3**：送模型那张图的 **IHDR 宽高 == 回显的 ``sent_width/sent_height``**
  （防「静默偏移一个放大倍数」）；放大倍数按**目标的表观像素**算；
  提示词里那句「1 像素 = 1/N 屏幕像素」**必须跟着变**（否则是假话）。
* **T4**：K 个候选 → 空间聚类 → **标记图**上模型答编号 → 落点取那个候选；
  答 **0** → **整屏再搜**（``landing_source="rescreen"``，**不是回落**）。
* **T1**：每次采样的**原文与落点**都进采样池（所有臂从同一份池子取）。
"""

from __future__ import annotations

import base64
import json
import unittest

from minicode.action.ports import CapturedFrame, PixelFormat
from minicode.coord.coordinates import ModelScale
from minicode.ground.channel import TwoStageGrounder
from minicode.ground.gate import FallbackCause, GateMode
from minicode.ground.response_pool import (
    ResponsePool,
    cluster_candidates,
    refine_windows,
)
from minicode.model.client import ModelReply
from minicode.model.effort import Effort

_SCREEN = (2560, 1600)
_MODEL_INPUT = (2016, 1260)


def _reply(text: str) -> ModelReply:
    return ModelReply(
        text=text,
        model="stub",
        endpoint="stub://",
        effort=Effort.LOW,
        http_status=200,
        latency_ms=1.0,
    )


def _point(x: float, y: float) -> ModelReply:
    return _reply(json.dumps({"x": x, "y": y}))


def _bbox(x1: float, y1: float, x2: float, y2: float, text: str = "按钮") -> ModelReply:
    return _reply(
        json.dumps({"bbox": [x1, y1, x2, y2], "text": text}, ensure_ascii=False)
    )


class _StubModel:
    """按调用顺序给出预置回答的桩模型。"""

    def __init__(self, *outcomes):
        self._outcomes = list(outcomes)
        self.calls: list[dict] = []

    def complete(
        self,
        prompt: str,
        *,
        effort: Effort | str | None = None,
        max_tokens: int = 2000,
        image_base64: str | None = None,
        thinking_disabled: bool = False,
    ) -> ModelReply:
        self.calls.append({"prompt": prompt, "image_base64": image_base64})
        if not self._outcomes:
            raise AssertionError("桩模型没有预置回答")
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def _frame(width: int, height: int) -> CapturedFrame:
    row = bytes((index * 37) % 256 for index in range(width * 3))
    rows = []
    for y in range(height):
        shift = (y % 64) * 3
        rows.append(row[shift:] + row[:shift])
    return CapturedFrame(
        width=width,
        height=height,
        left=0,
        top=0,
        payload=b"".join(rows),
        backend="fake",
        source="synthetic",
        pixel_format=PixelFormat.BGR,
    )


def _ihdr_size(image_base64: str) -> tuple[int, int]:
    """**PNG 的 IHDR 宽高**（IHDR 的数据段是第 16~24 字节，大端）。"""
    data = base64.b64decode(image_base64)
    if data[:8] != b"\x89PNG\r\n\x1a\n" or data[12:16] != b"IHDR":
        raise AssertionError("这不是一张 PNG")
    width = int.from_bytes(data[16:20], "big")
    height = int.from_bytes(data[20:24], "big")
    return width, height


def _grounder(model, **kwargs) -> TwoStageGrounder:
    return TwoStageGrounder(
        model,
        scale=ModelScale.ABSOLUTE_MODEL_INPUT,
        model_input_size=_MODEL_INPUT,
        full_screen_size=_SCREEN,
        effort=Effort.LOW,
        gate_mode=GateMode.ALWAYS,
        **kwargs,
    )


def _request(frame):
    from minicode.action.ports import VisualLocateRequest

    return VisualLocateRequest(
        target_description="开始",
        frame=frame,
        surface=(0, 0, frame.width, frame.height),
    )


class UpscaleTests(unittest.TestCase):
    """**T3**：crop 放大。"""

    def test_the_sent_png_matches_the_echo_sent_size(self):
        """**送出的 PNG 的 IHDR 宽高 == 回显的 sent_width/sent_height**。

        ⚠️ 回归守卫：放大后**声明尺寸**与**实际发出的图**
        必须一致 —— 不一致就是**静默偏移一个放大倍数**。
        """
        model = _StubModel(
            _bbox(1000.0, 600.0, 1016.0, 616.0),
            _point(100.0, 100.0),
        )
        grounder = _grounder(model)
        result = grounder.locate(_request(_frame(*_SCREEN)))
        self.assertTrue(result.ok, msg=result.error_message)
        echoes = [echo for echo in result.echoes if echo.stage == "refine"]
        self.assertEqual(len(echoes), 1)
        sent = model.calls[1]["image_base64"]
        self.assertEqual(_ihdr_size(sent), (echoes[0].sent_width, echoes[0].sent_height))

    def test_the_upscale_factor_follows_the_target_apparent_size(self):
        """放大倍数按**目标的表观像素**算（目标 ~20 物理 px → 放大到 ≥30 px）。"""
        #: 16 个模型输入像素 ≈ 20 物理像素（2560/2016 = 1.27）。
        model = _StubModel(
            _bbox(1000.0, 600.0, 1016.0, 616.0),
            _point(100.0, 100.0),
        )
        grounder = _grounder(model)
        result = grounder.locate(_request(_frame(*_SCREEN)))
        plan = result.crop_plan
        self.assertIsNotNone(plan)
        factor = plan["refine_upscale_factor"]
        self.assertGreater(factor, 1.0, msg="20 物理 px 的目标应当被放大")
        self.assertLessEqual(factor, 3.0)
        self.assertGreaterEqual(plan["refine_input_width"], 1)

    def test_the_prompt_says_the_image_was_upscaled(self):
        """放大后那句「1 像素 = 1/N 屏幕像素」**必须跟着变**（否则是假话）。"""
        model = _StubModel(
            _bbox(1000.0, 600.0, 1016.0, 616.0),
            _point(100.0, 100.0),
        )
        grounder = _grounder(model)
        grounder.locate(_request(_frame(*_SCREEN)))
        refine_prompt = model.calls[1]["prompt"]
        self.assertIn("已放大约", refine_prompt)
        self.assertIn("1 像素 = 1/", refine_prompt)
        self.assertNotIn("未缩放", refine_prompt)

    def test_no_upscale_keeps_the_old_wording(self):
        """``upscale_target_px=0``（对照臂）→ 文案与尺寸**原样**。"""
        model = _StubModel(
            _bbox(1000.0, 600.0, 1016.0, 616.0),
            _point(100.0, 100.0),
        )
        grounder = _grounder(model, upscale_target_px=0.0)
        result = grounder.locate(_request(_frame(*_SCREEN)))
        refine_prompt = model.calls[1]["prompt"]
        self.assertIn("未缩放", refine_prompt)
        self.assertEqual(result.crop_plan["refine_upscale_factor"], 1.0)


class MultiCandidateTests(unittest.TestCase):
    """**T4**：多候选 + 标记图选择 + 整屏再搜。"""

    def test_the_marked_image_choice_decides_the_landing(self):
        """模型在**标记图**上答的编号 → 落点取那个候选。"""
        model = _StubModel(
            _bbox(1000.0, 600.0, 1016.0, 616.0),
            _point(100.0, 100.0),
            _point(200.0, 200.0),
            _point(300.0, 300.0),
            _point(400.0, 400.0),
            _reply(json.dumps({"chosen": 2})),
        )
        grounder = _grounder(model, variants=4)
        result = grounder.locate(_request(_frame(*_SCREEN)))
        self.assertTrue(result.ok, msg=result.error_message)
        self.assertEqual(result.landing_source, "refined")
        self.assertEqual(result.crop_plan["refine_chosen_candidate"], 2)
        self.assertEqual(result.crop_plan["refine_candidate_count"], 4)
        #: 标记图那一遍**也**记了回显。
        self.assertTrue(any(echo.stage == "marked" for echo in result.echoes))

    def test_answering_zero_triggers_the_whole_screen_rescreen(self):
        """答 **0**（清单里没有可信的）→ **整屏再搜**（**不是回落**）。"""
        model = _StubModel(
            _bbox(1000.0, 600.0, 1016.0, 616.0),
            _point(100.0, 100.0),
            _point(200.0, 200.0),
            _point(300.0, 300.0),
            _reply(json.dumps({"chosen": 0})),
            _bbox(1200.0, 700.0, 1230.0, 730.0, text="别的按钮"),
        )
        grounder = _grounder(model, variants=3)
        result = grounder.locate(_request(_frame(*_SCREEN)))
        self.assertTrue(result.ok, msg=result.error_message)
        self.assertEqual(result.landing_source, "rescreen")
        self.assertEqual(result.fallback_cause, FallbackCause.RESCREEN.value)
        #: 再搜那一遍的提示词里带着「上一个候选是错的」。
        self.assertIn("上一个候选是错的", model.calls[-1]["prompt"])

    def test_a_single_variant_keeps_the_old_behaviour(self):
        """``variants=1`` → **一次一问**、**不画标记图**（现状不变）。"""
        model = _StubModel(
            _bbox(1000.0, 600.0, 1016.0, 616.0),
            _point(100.0, 100.0),
        )
        grounder = _grounder(model, variants=1)
        result = grounder.locate(_request(_frame(*_SCREEN)))
        self.assertTrue(result.ok, msg=result.error_message)
        self.assertEqual(len(model.calls), 2)
        self.assertIsNone(result.crop_plan["refine_chosen_candidate"])


class ResponsePoolTests(unittest.TestCase):
    """**T1**：采样池。"""

    def test_every_sample_is_recorded_with_its_raw_text(self):
        pool = ResponsePool()
        model = _StubModel(
            _bbox(1000.0, 600.0, 1016.0, 616.0),
            _point(100.0, 100.0),
            _point(200.0, 200.0),
            _reply(json.dumps({"chosen": 1})),
        )
        grounder = _grounder(model, variants=3, pool=pool)
        grounder.locate(_request(_frame(*_SCREEN)))
        entries = pool.entries()
        self.assertTrue(entries)
        stages = {entry.stage for entry in entries}
        self.assertIn("refine", stages)
        #: 每一条都带**原文**与**提示词**（重放即零随机性差）。
        for entry in entries:
            self.assertTrue(entry.raw_text)
            self.assertTrue(entry.prompt)

    def test_the_pool_round_trips_through_disk(self):
        pool = ResponsePool()
        pool.record(
            stage="refine",
            variant=0,
            key="abc",
            prompt="p",
            raw_text="t",
            point=(1.0, 2.0),
        )
        with self.subTest("落盘再读回"):
            import tempfile
            from pathlib import Path

            with tempfile.TemporaryDirectory() as folder:
                path = pool.save(Path(folder) / "pool.jsonl")
                again = ResponsePool.load(path)
                self.assertEqual(len(again.entries()), 1)
                self.assertEqual(again.entries()[0].point, (1.0, 2.0))


class ClusteringTests(unittest.TestCase):
    """**T4** 的空间聚类（τ 定义在**物理空间**）。"""

    def test_the_densest_cluster_wins(self):
        clusters = cluster_candidates(
            [(100.0, 100.0), (105.0, 102.0), (400.0, 400.0)], tau_px=32.0
        )
        self.assertEqual(clusters[0].size, 2)
        self.assertAlmostEqual(clusters[0].centroid[0], 102.5)
        self.assertAlmostEqual(clusters[0].ratio, 2 / 3)

    def test_the_windows_cover_the_whole_crop(self):
        windows = refine_windows(1000, 600, 7)
        self.assertEqual(len(windows), 7)
        covered_x = []
        for left, top, right, bottom in windows:
            self.assertGreaterEqual(left, 0)
            self.assertGreaterEqual(top, 0)
            self.assertLessEqual(right, 1000)
            self.assertLessEqual(bottom, 600)
            covered_x.append((left, right))
        #: 角与边都被某个窗口盖住（并起来盖住整个 crop）。
        self.assertEqual(max(right for _left, right in covered_x), 1000)
        self.assertEqual(min(left for left, _right in covered_x), 0)
        self.assertEqual(max(bottom for _t, _l, _r, bottom in windows), 600)
        self.assertEqual(min(top for _t, top, _r, _b in windows), 0)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

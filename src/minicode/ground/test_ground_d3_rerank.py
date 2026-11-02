"""d3 候选重排 + 置信度闸门（V4）的单测：锦标赛触发、低置信拒绝、降级、0.0px 契约。

覆盖（任务书 V4-6 点名的路径）：

* 候选并列 → 调 d3 → 选 argmax（落点 = 候选框**物理中心**，偏差 0）；
* 分数拉开 → **不调** d3（省调用的口径）；
* 低置信 → **不点**（``low_confidence``，转人工的口径）；
* d3 失败（429 / 500 / 格式错）→ 降级 = **不点**（不是猜）；
* 重排结果不在候选清单 → 降级不点。
"""

from __future__ import annotations

import json
import unittest

from minicode.action.ports import CapturedFrame, PixelFormat, VisualLocateRequest
from minicode.coord.coordinates import ModelScale
from minicode.ground.candidates import GroundingCandidate
from minicode.ground.channel import TwoStageGrounder
from minicode.ground.d3_rerank import (
    DEFAULT_MIN_CONFIDENCE,
    D3CandidateReranker,
    D3RerankGate,
    RerankAction,
)
from minicode.model.d3 import D3Error, D3Reply
from minicode.model.effort import Effort

_FRAME_WIDTH = 64
_FRAME_HEIGHT = 48


class _FakeD3:
    """预置结果的桩 d3 客户端（记录调用参数）。"""

    def __init__(self, reply: D3Reply | Exception):
        self._reply = reply
        self.calls: list[dict] = []

    def decide(self, state, question, criteria):
        self.calls.append({"state": state, "question": question, "criteria": criteria})
        if isinstance(self._reply, Exception):
            raise self._reply
        return self._reply


def _reply(confidence, probabilities=None, chosen=""):
    return D3Reply(
        probabilities=probabilities or {"1": 1.0 - confidence, "2": confidence},
        confidence=confidence,
        chosen=chosen or "2",
        latency_ms=10.0,
        http_status=200,
    )


def _candidates():
    return [
        GroundingCandidate(number=1, text="保存", box=(0, 0, 20, 20), source="uia"),
        GroundingCandidate(number=2, text="另存", box=(30, 0, 50, 20), source="uia"),
    ]


class RerankerTests(unittest.TestCase):
    """重排器本体（不经过 grounder）。"""

    def test_tie_calls_d3_and_takes_argmax(self):
        """并列 → 问 d3 → 选概率最大的候选。"""
        d3 = _FakeD3(_reply(0.9, probabilities={"1": 0.1, "2": 0.9}))
        reranker = D3CandidateReranker(d3, gate=D3RerankGate(min_confidence=0.5))
        outcome = reranker.rerank("点击按钮", _candidates())
        self.assertEqual(outcome.action, RerankAction.RERANKED)
        self.assertEqual(outcome.chosen_number, 2)
        self.assertTrue(outcome.called_d3)
        self.assertEqual(outcome.confidence, 0.9)
        # state 里有候选清单与描述（d3 的输入契约）。
        self.assertIn("另存", d3.calls[0]["state"])
        self.assertEqual(d3.calls[0]["criteria"]["2"], "另存")

    def test_clear_gap_skips_d3(self):
        """分数拉开（唯一匹配）→ **不调** d3，直接选 top1。"""
        d3 = _FakeD3(_reply(0.9))
        reranker = D3CandidateReranker(d3)
        outcome = reranker.rerank("点击保存", _candidates())
        self.assertEqual(outcome.action, RerankAction.DIRECT)
        self.assertEqual(outcome.chosen_number, 1)  # 「保存」匹配描述，「另存」不匹配
        self.assertFalse(outcome.called_d3)
        self.assertEqual(d3.calls, [])

    def test_low_confidence_refuses_to_click(self):
        """置信度不够 → **不点**（fail-closed），并给原因。"""
        d3 = _FakeD3(_reply(0.3, probabilities={"1": 0.5, "2": 0.5}))
        reranker = D3CandidateReranker(d3, gate=D3RerankGate(min_confidence=0.5))
        outcome = reranker.rerank("点击按钮", _candidates())
        self.assertEqual(outcome.action, RerankAction.LOW_CONFIDENCE)
        self.assertIsNone(outcome.chosen_number)
        self.assertIn("0.3", outcome.reason)

    def test_d3_rate_limited_degrades_to_no_click(self):
        """429 → 降级 = **不点**（不是猜），且如实记 d3 已被调用。"""
        d3 = _FakeD3(D3Error("d3 HTTP 429：慢一点", status=429))
        reranker = D3CandidateReranker(d3)
        outcome = reranker.rerank("点击按钮", _candidates())
        self.assertEqual(outcome.action, RerankAction.D3_UNAVAILABLE)
        self.assertIsNone(outcome.chosen_number)
        self.assertTrue(outcome.called_d3)
        self.assertIn("429", outcome.reason)

    def test_d3_server_error_degrades_to_no_click(self):
        d3 = _FakeD3(D3Error("d3 HTTP 500：boom", status=500))
        reranker = D3CandidateReranker(d3)
        outcome = reranker.rerank("点击按钮", _candidates())
        self.assertEqual(outcome.action, RerankAction.D3_UNAVAILABLE)
        self.assertIsNone(outcome.chosen_number)

    def test_d3_argmax_outside_candidates_degrades(self):
        """d3 给的 argmax 不在候选清单里 → 降级不点（形状级意外）。"""
        d3 = _FakeD3(_reply(0.9, probabilities={"1": 0.05, "2": 0.05, "9": 0.9}, chosen="9"))
        reranker = D3CandidateReranker(d3)
        outcome = reranker.rerank("点击按钮", _candidates())
        self.assertEqual(outcome.action, RerankAction.D3_UNAVAILABLE)
        self.assertIsNone(outcome.chosen_number)

    def test_single_candidate_never_calls_d3(self):
        d3 = _FakeD3(_reply(0.9))
        reranker = D3CandidateReranker(d3)
        outcome = reranker.rerank(
            "点击保存", _candidates()[:1]
        )
        self.assertEqual(outcome.action, RerankAction.NOT_APPLICABLE)
        self.assertEqual(outcome.chosen_number, 1)
        self.assertEqual(d3.calls, [])


class _Source:
    def __init__(self, items):
        self._items = list(items)

    def candidates(self, frame, description):
        return list(self._items)


class _NoModel:
    """视觉模型桩：重排路径**根本不该调它**（调了就记下来）。"""

    def __init__(self):
        self.calls = 0

    def complete(self, *args, **kwargs):
        self.calls += 1
        raise AssertionError("重排路径不应调用视觉模型")


def _frame() -> CapturedFrame:
    payload = bytes(
        (x * 7 + y * 3) % 256
        for y in range(_FRAME_HEIGHT)
        for x in range(_FRAME_WIDTH)
        for _ in range(3)
    )
    return CapturedFrame(
        width=_FRAME_WIDTH,
        height=_FRAME_HEIGHT,
        payload=payload,
        pixel_format=PixelFormat.BGR,
    )


def _request(target: str, frame: CapturedFrame) -> VisualLocateRequest:
    return VisualLocateRequest(
        target_description=target,
        surface=(0, 0, _FRAME_WIDTH, _FRAME_HEIGHT),
        frame=frame,
    )


def _grounder(model, reranker) -> TwoStageGrounder:
    return TwoStageGrounder(
        model,
        scale=ModelScale.RELATIVE_0_1000,
        model_input_size=(_FRAME_WIDTH, _FRAME_HEIGHT),
        full_screen_size=(_FRAME_WIDTH, _FRAME_HEIGHT),
        effort=Effort.LOW,
        candidate_source=_Source(_candidates()),
        reranker=reranker,
    )


class GrounderIntegrationTests(unittest.TestCase):
    """接线契约：落点 = 候选框物理中心；低置信 = 显式失败；视觉模型零调用。"""

    def test_reranked_landing_is_the_candidate_center(self):
        """0.0px 契约：落点 = 2 号候选框的物理中心，一次视觉模型调用都没有。"""
        d3 = _FakeD3(_reply(0.9, probabilities={"1": 0.1, "2": 0.9}))
        model = _NoModel()
        grounder = _grounder(model, D3CandidateReranker(d3))
        result = grounder.locate(_request("点击按钮", _frame()))
        self.assertTrue(result.ok, msg=result.error_message)
        self.assertEqual(result.landing_point, (40, 10))  # (30,0,50,20) 的中心
        self.assertEqual(result.landing_source, "numbered")
        self.assertEqual(model.calls, 0)
        # 回显携带重排结论（可审计）。
        numbered = [echo for echo in result.echoes if echo.stage == "numbered"]
        self.assertTrue(numbered)
        self.assertIn("rerank:reranked", numbered[-1].image_source)
        self.assertIn("confidence=0.900", numbered[-1].image_source)

    def test_low_confidence_is_an_explicit_failure(self):
        """低置信 → 不点：显式 LOCATE_FAILED，信息里有 ``low_confidence``。"""
        d3 = _FakeD3(_reply(0.3, probabilities={"1": 0.5, "2": 0.5}))
        grounder = _grounder(_NoModel(), D3CandidateReranker(d3))
        result = grounder.locate(_request("点击按钮", _frame()))
        self.assertFalse(result.ok)
        self.assertIn("locate_failed", result.error_code.value.lower())
        self.assertIn("low_confidence", result.error_message)
        self.assertIn("不点", result.error_message)

    def test_d3_outage_is_an_explicit_failure_not_a_guess(self):
        """d3 挂了 → 降级 = 不点（不是猜一个点）。"""
        d3 = _FakeD3(D3Error("d3 HTTP 500：boom", status=500))
        grounder = _grounder(_NoModel(), D3CandidateReranker(d3))
        result = grounder.locate(_request("点击按钮", _frame()))
        self.assertFalse(result.ok)
        self.assertIn("d3_unavailable", result.error_message)

    def test_without_reranker_the_model_picks_the_number(self):
        """不接重排器 → 行为与从前完全一致（视觉模型挑号）。"""

        class _Model:
            def __init__(self):
                self.calls = 0

            def complete(self, prompt, **kwargs):
                self.calls += 1
                from minicode.model.client import ModelReply

                return ModelReply(
                    text=json.dumps({"chosen": 1}),
                    model="stub",
                    endpoint="stub://",
                    effort=Effort.LOW,
                    http_status=200,
                    latency_ms=1.0,
                )

        model = _Model()
        grounder = _grounder(model, None)
        result = grounder.locate(_request("点击保存", _frame()))
        self.assertTrue(result.ok, msg=result.error_message)
        self.assertEqual(result.landing_point, (10, 10))
        self.assertEqual(model.calls, 1)
        self.assertEqual(DEFAULT_MIN_CONFIDENCE, 0.5)  # 缺省阈值写死在文档口径里


if __name__ == "__main__":
    unittest.main()

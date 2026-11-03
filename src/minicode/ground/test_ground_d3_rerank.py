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
    D3PerCandidateReranker,
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


class _FakeD3Noul:
    """逐候选模式的桩 d3 客户端：候选序号 → yes 概率（或异常）。"""

    def __init__(self, yes_by_call, sleeps=None):
        # yes_by_call：每次调用的结果（float 概率 / Exception），按调用顺序消费。
        self._results = list(yes_by_call)
        self.calls: list[dict] = []
        self.sleeps = sleeps if sleeps is not None else []
        self.sleep_calls: list[float] = []

    def decide_noul(self, state, question="judge"):
        self.calls.append({"state": state, "question": question})
        result = self._results.pop(0)
        if isinstance(result, Exception):
            raise result
        yes = float(result)
        from minicode.model.d3 import D3Reply

        return D3Reply(
            probabilities={"yes": yes, "no": 1.0 - yes},
            confidence=yes,
            chosen="yes" if yes >= 0.5 else "no",
            latency_ms=10.0,
            http_status=200,
        )

    def sleep(self, seconds):
        self.sleep_calls.append(seconds)


def _four_candidates():
    return [
        GroundingCandidate(number=1, text="保存", box=(0, 0, 20, 20), source="uia"),
        GroundingCandidate(number=2, text="另存", box=(30, 0, 50, 20), source="uia"),
        GroundingCandidate(number=3, text="存档", box=(60, 0, 80, 20), source="uia"),
        GroundingCandidate(number=4, text="导出", box=(90, 0, 110, 20), source="uia"),
    ]


class PerCandidateRerankerTests(unittest.TestCase):
    """W3 逐候选 yes/no 重排：K 截断、argmax、低置信、降级、429 退避。"""

    def test_asks_only_top_k_candidates(self):
        """4 个候选并列 → 只问启发式前 K（默认 3）个，第 4 个不问。"""
        d3 = _FakeD3Noul([0.7, 0.8, 0.6])
        reranker = D3PerCandidateReranker(d3)
        outcome = reranker.rerank("点击按钮", _four_candidates())
        self.assertEqual(len(d3.calls), 3)
        self.assertEqual(outcome.action, RerankAction.RERANKED)
        self.assertEqual(outcome.chosen_number, 2)  # yes 最高者
        # probabilities 只含被问过的候选。
        self.assertEqual(sorted(outcome.probabilities), ["1", "2", "3"])

    def test_state_contains_description_and_candidate(self):
        d3 = _FakeD3Noul([0.9, 0.5, 0.5])
        reranker = D3PerCandidateReranker(d3)
        reranker.rerank("点击保存按钮", _four_candidates())
        first_state = d3.calls[0]["state"]
        self.assertIn("点击保存按钮", first_state)
        self.assertIn("保存", first_state)
        self.assertIn("left=", first_state)  # 位置也在 state 里

    def test_below_threshold_refuses_to_click(self):
        """最高 yes < 阈值 → 不点（fail-closed，转人工）。"""
        d3 = _FakeD3Noul([0.4, 0.55, 0.45])
        reranker = D3PerCandidateReranker(d3, min_yes_probability=0.6)
        outcome = reranker.rerank("点击按钮", _four_candidates())
        self.assertEqual(outcome.action, RerankAction.LOW_CONFIDENCE)
        self.assertIsNone(outcome.chosen_number)
        self.assertIn("0.550", outcome.reason)

    def test_any_candidate_unavailable_falls_back_to_heuristic_top1(self):
        """一个候选挂了 → 整次重排不可用 → **启发式 top1 继续**（不整条任务失败）。"""
        d3 = _FakeD3Noul([0.7, D3Error("d3 HTTP 400：boom", status=400)])
        reranker = D3PerCandidateReranker(d3)
        outcome = reranker.rerank("点击保存", _four_candidates())
        self.assertEqual(outcome.action, RerankAction.RERANK_UNAVAILABLE)
        # 「保存」唯一匹配描述 → 启发式 top1 = 1 号；沿用它，返回非 None。
        self.assertEqual(outcome.chosen_number, 1)
        self.assertEqual(outcome.heuristic_number, 1)

    def test_429_backs_off_then_recovers(self):
        """429 → 60s 退避重试（sleep 注入），第二次成功；退避单独计数。"""
        d3 = _FakeD3Noul(
            [D3Error("d3 HTTP 429：慢", status=429), 0.75, 0.5, 0.5],
        )
        reranker = D3PerCandidateReranker(d3, sleep=d3.sleep)
        outcome = reranker.rerank("点击按钮", _four_candidates())
        self.assertEqual(d3.sleep_calls, [60.0])
        self.assertEqual(outcome.action, RerankAction.RERANKED)
        self.assertEqual(reranker.stats["rate_limited"], 1)

    def test_429_exhausted_degrades_to_heuristic(self):
        """429 重试耗尽 → 不可用 → 启发式 top1；429 次数单独计数。"""
        d3 = _FakeD3Noul(
            [D3Error("429", status=429), D3Error("429", status=429), D3Error("429", status=429)],
        )
        reranker = D3PerCandidateReranker(d3, sleep=d3.sleep)
        outcome = reranker.rerank("点击保存", _four_candidates())
        self.assertEqual(outcome.action, RerankAction.RERANK_UNAVAILABLE)
        self.assertEqual(outcome.chosen_number, 1)
        self.assertEqual(len(d3.sleep_calls), 2)  # 3 次尝试之间只退避 2 次
        self.assertEqual(reranker.stats["rate_limited"], 3)

    def test_single_candidate_never_calls_d3(self):
        d3 = _FakeD3Noul([0.9])
        reranker = D3PerCandidateReranker(d3)
        outcome = reranker.rerank("点击保存", _four_candidates()[:1])
        self.assertEqual(outcome.action, RerankAction.NOT_APPLICABLE)
        self.assertEqual(outcome.chosen_number, 1)
        self.assertEqual(d3.calls, [])

    def test_call_log_records_every_judgement(self):
        """逐候选判定日志：候选序号 / yes 概率 / 状态码都落。"""
        d3 = _FakeD3Noul([0.7, 0.8, 0.6])
        entries: list[dict] = []
        reranker = D3PerCandidateReranker(d3, call_log=entries.append)
        reranker.rerank("点击按钮", _four_candidates())
        self.assertEqual(len(entries), 3)
        self.assertEqual([e["candidate_number"] for e in entries], [1, 2, 3])
        self.assertEqual(entries[1]["yes_probability"], 0.8)
        self.assertEqual(entries[1]["http_status"], 200)


class PerCandidateGrounderIntegrationTests(unittest.TestCase):
    """接线契约：rerank_unavailable 落在启发式 top1 上；低置信是显式失败。"""

    def test_rerank_unavailable_lands_on_heuristic_top1(self):
        """d3 挂 → 启发式 top1 继续：落点 = top1 候选框中心，任务不失败。"""
        d3 = _FakeD3Noul([D3Error("d3 HTTP 502：网关", status=502)])
        model = _NoModel()
        grounder = _grounder(model, D3PerCandidateReranker(d3))
        result = grounder.locate(_request("点击保存", _frame()))
        self.assertTrue(result.ok, msg=result.error_message)
        self.assertEqual(result.landing_point, (10, 10))  # 1 号候选框中心
        numbered = [echo for echo in result.echoes if echo.stage == "numbered"]
        self.assertTrue(numbered)
        self.assertIn("rerank:rerank_unavailable", numbered[-1].image_source)

    def test_low_confidence_is_an_explicit_failure(self):
        d3 = _FakeD3Noul([0.4, 0.55, 0.45])
        grounder = _grounder(_NoModel(), D3PerCandidateReranker(d3, min_yes_probability=0.6))
        result = grounder.locate(_request("点击按钮", _frame()))
        self.assertFalse(result.ok)
        self.assertIn("low_confidence", result.error_message)

    def test_reranked_landing_is_the_candidate_center(self):
        """0.0px 契约：yes 最高者胜，落点 = 该候选框物理中心，视觉模型零调用。"""
        d3 = _FakeD3Noul([0.2, 0.9, 0.3])
        model = _NoModel()
        grounder = _grounder(model, D3PerCandidateReranker(d3))
        result = grounder.locate(_request("点击按钮", _frame()))
        self.assertTrue(result.ok, msg=result.error_message)
        self.assertEqual(result.landing_point, (40, 10))  # 2 号候选框中心
        self.assertEqual(model.calls, 0)


if __name__ == "__main__":
    unittest.main()

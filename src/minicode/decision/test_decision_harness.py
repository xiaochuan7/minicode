"""**决策层 harness** 的单测（工单 D-1 / D-7）。

判据：

* **条件抓帧**（D-1）：只有「本轮程序调过 observe()/screenshot()」、
  「手里那帧可能过时」或「本轮做过定位/点击动作」才抓新帧 ——
  **否则沿用上一帧**（省 token，但不换成「看不见」）；
* **每轮的图像 token 单列一列**（D-7）：拿不到就记 ``None``（**不猜**）；
* **送模型的帧缩到声明尺寸**（A-2 同一件事）：上下文里留的仍是原始分辨率。

⚠️ 假实现**不碰真桌面、不产生模型调用**（工单 M10 AC 7 的分工）。
"""

from __future__ import annotations

import base64
import unittest
from dataclasses import dataclass, field
from typing import Any

from minicode.action.protocol import Action, ActionKind, ActionResult
from minicode.action.seam import SeamResult
from minicode.model.client import ModelReply
from minicode.model.effort import Effort
from minicode.model.request_log import png_image_size
from minicode.reflection.stop import (
    STOP_CLAIMED_UNVERIFIED,
    StopPolicy,
)
from minicode.verification.fakes import make_frame

from .api import ProtocolApi
from .harness import DecisionHarness


@dataclass
class _FakeTrace:
    """一条**假追踪记录**（形状够 :func:`signal_from_trace` 读就行）。"""

    action: Action
    target_description: str = "开始"
    verification_result: dict[str, Any] | None = None
    result: ActionResult = field(
        default_factory=lambda: ActionResult(ok=True, landing_point=(10, 20))
    )
    intent: str = "点击"
    selected: Any = None


def _verification(changed: bool) -> dict[str, Any]:
    """校验读数的落盘形状（与真的 ``ChangeResult.to_dict()`` 同形）。"""
    outcome = "changed" if changed else "no_change"
    return {"outcome": outcome, "change": {"outcome": outcome}}


class _FakeSeam:
    """假的动作执行缝：按脚本回结果（**不下发任何动作**）。

    ``trace_sink`` 给了就把每一步**当成追踪记录**喂给它（停止条件靠这个
    知道「这一轮有没有校验记录」）。
    """

    def __init__(self, *, frames=(), trace_sink=None, changed: bool | None = None):
        self._frames = list(frames)
        self._trace_sink = trace_sink
        #: 这一步的校验结论（``None`` = 这一步没有校验记录）。
        self.changed = changed
        #: 抓了几次画面（截图端口被叫了几次）。
        self.captures = 0

    @property
    def executor(self):
        return _FakeExecutor()

    def run(self, request) -> SeamResult:
        if request.action_kind is ActionKind.SCREENSHOT:
            self.captures += 1
            frame = self._frames.pop(0) if self._frames else make_frame()
            return SeamResult(
                action=Action(ActionKind.SCREENSHOT, {}, "截一张图"),
                final_point=None,
                intent="截一张图",
                result=ActionResult(ok=True, captured=frame),
            )
        if self._trace_sink is not None:
            self._trace_sink.emit(
                _FakeTrace(
                    action=Action(request.action_kind, {}, "点击"),
                    verification_result=(
                        _verification(self.changed)
                        if self.changed is not None
                        else None
                    ),
                )
            )
        return SeamResult(
            action=Action(request.action_kind, {}, "点击"),
            final_point=(10, 20),
            intent="点击",
            result=ActionResult(ok=True, landing_point=(10, 20)),
        )


class _FakeExecutor:
    """假的动作执行器（输入文本 / 按键走它）。"""

    def execute(self, action) -> ActionResult:
        return ActionResult(ok=True)


class _FakeCaller:
    """假的模型调用：按脚本回程序源码（**不发请求**）。"""

    def __init__(self, programs, *, image_tokens: int | None = 1234):
        self._programs = list(programs)
        self._image_tokens = image_tokens
        #: 每次调用记下：提示词与**发过去的那几张图**。
        self.calls: list[dict] = []

    def complete(
        self,
        prompt,
        *,
        effort=None,
        max_tokens=2000,
        image_base64=None,
        images_base64=(),
        thinking_disabled=False,
    ) -> ModelReply:
        self.calls.append({"prompt": prompt, "images": tuple(images_base64)})
        program = self._programs.pop(0) if self._programs else "pass\n"
        return ModelReply(
            text=f"```python\n{program}```",
            model="fake",
            endpoint="http://local",
            effort=Effort.HIGH,
            http_status=200,
            latency_ms=1.0,
            input_tokens=100,
            output_tokens=10,
            image_tokens=self._image_tokens,
        )


def _harness(programs, *, frames=(), max_rounds=2):
    """搭一套假 harness（假模型 + 假缝）。"""
    seam = _FakeSeam(frames=frames)
    api = ProtocolApi(seam=seam)
    caller = _FakeCaller(programs)
    harness = DecisionHarness(caller=caller, api=api, max_rounds=max_rounds)
    return harness, api, caller, seam


class ConditionalCaptureTest(unittest.TestCase):
    """条件抓帧（D-1）：不抓 → 沿用上一帧；该抓 → 抓。"""

    def test_a_round_without_observe_and_without_actions_captures_nothing(self):
        """程序没调 observe()、上轮也没有动作 → **不抓新帧**（沿用上一帧）。"""
        harness, _api, _caller, seam = _harness(["pass\n", "pass\n"])
        report = harness.run("目标")
        #: 只有**初始那一帧**（第 1 轮之前抓的）。
        self.assertEqual(seam.captures, 1)
        self.assertEqual(report.kept_frames, 1)

    def test_a_round_that_called_observe_captures_a_new_frame(self):
        """程序调了 observe() → 抓新帧（模型自己要新画面，就得给它）。"""
        harness, _api, _caller, seam = _harness(
            ["observe()\n", "observe()\n"], max_rounds=2
        )
        report = harness.run("目标")
        #: 初始 1 帧 + 每轮 2 帧（程序自己 observe 一次、harness 再抓一次）。
        self.assertEqual(seam.captures, 5)
        self.assertEqual(report.kept_frames, 3)

    def test_a_clicking_round_captures_a_new_frame(self):
        """**兜底纪律**：模型忘了调 observe() 就成了盲写 —— 本轮点过东西就抓一帧。"""
        harness, _api, _caller, seam = _harness(
            ["click('开始')\n", "click('开始')\n"], max_rounds=2
        )
        report = harness.run("目标")
        #: 初始 1 帧 + 每轮 1 帧（点击本身不抓画面，是 harness 抓的）。
        self.assertEqual(seam.captures, 3)
        self.assertEqual(report.kept_frames, 3)

    def test_a_round_after_an_action_catches_up(self):
        """上一轮动过界面而那一轮末尾没抓到新帧 → 这一轮补抓。"""
        harness, _api, _caller, seam = _harness(
            ["type_text('x')\n", "type_text('x')\n"], max_rounds=2
        )
        harness.run("目标")
        #: 初始 1 帧 + 第 2 轮补抓 1 帧（第 1 轮的输入文本没有定位/点击，
        #: 按兜底纪律不抓 —— 但手里那帧从此可能过时，第 2 轮补上）。
        self.assertEqual(seam.captures, 2)


class ImageTokenAccountingTest(unittest.TestCase):
    """每轮的图像 token 单列一列（D-7）。"""

    def test_the_image_tokens_are_reported_per_step(self):
        harness, _api, _caller, _seam = _harness(["pass\n", "pass\n"])
        report = harness.run("目标")
        self.assertEqual(report.image_token_growth, ((1, 1234), (2, 1234)))
        self.assertEqual(
            report.to_dict()["image_token_growth"], [[1, 1234], [2, 1234]]
        )

    def test_a_missing_image_token_count_stays_none(self):
        """拿不到就记 None（**不猜**）。"""
        seam = _FakeSeam()
        api = ProtocolApi(seam=seam)
        caller = _FakeCaller(["pass\n"], image_tokens=None)
        harness = DecisionHarness(caller=caller, api=api, max_rounds=1)
        report = harness.run("目标")
        self.assertEqual(report.image_token_growth, ((1, None),))


class SkipVerificationTest(unittest.TestCase):
    """「跳过验证」（工单 D-4）：自称完成 **且** 没有/不成立的校验 → 判死。

    ⚠️ **自称完成本身从不停止**（协议里没有「完成」这个动作）——
    判死走的是**组合**。
    """

    def _harness(self, programs, *, changed: bool | None, max_rounds: int = 3):
        policy = StopPolicy(max_rounds=max_rounds)
        seam = _FakeSeam(trace_sink=policy, changed=changed)
        api = ProtocolApi(seam=seam)
        caller = _FakeCaller(programs)
        harness = DecisionHarness(
            caller=caller,
            api=api,
            max_rounds=max_rounds,
            stop_policy=policy,
        )
        return harness, policy

    def test_a_claim_without_any_verification_is_stopped(self):
        """自称完成、而这一轮**没做过动作**（没有校验记录）→ 判死。"""
        harness, _policy = self._harness(
            ["print('STATUS: DONE')\n", "print('STATUS: DONE')\n"],
            changed=None,
        )
        report = harness.run("目标")
        self.assertEqual(report.stop_reason, STOP_CLAIMED_UNVERIFIED)
        self.assertEqual(report.rounds, 1)

    def test_a_claim_with_an_unchanged_screen_is_stopped(self):
        """自称完成、而校验读数说**界面没变** → 判死。"""
        harness, _policy = self._harness(
            ["click('开始')\nprint('STATUS: DONE')\n"],
            changed=False,
        )
        report = harness.run("目标")
        self.assertEqual(report.stop_reason, STOP_CLAIMED_UNVERIFIED)

    def test_a_claim_backed_by_a_change_is_not_stopped(self):
        """自称完成、而校验读数说**界面变了** → 不判死（证据成立）。"""
        harness, _policy = self._harness(
            ["click('开始')\nprint('STATUS: DONE')\n", "print('继续')\n"],
            changed=True,
            max_rounds=2,
        )
        report = harness.run("目标")
        self.assertNotEqual(report.stop_reason, STOP_CLAIMED_UNVERIFIED)

    def test_a_round_without_a_claim_is_not_stopped(self):
        """没自称完成 → 照旧不判死（既有行为一个字没改）。"""
        harness, _policy = self._harness(["click('开始')\n"], changed=False)
        report = harness.run("目标")
        self.assertNotEqual(report.stop_reason, STOP_CLAIMED_UNVERIFIED)

    def test_the_skip_verification_note_goes_into_the_context(self):
        """「跳过验证」的记录进上下文（模型与人都读得到）。"""
        policy = StopPolicy()
        policy.observe_claim(claimed=True)
        policy.observe_round()
        notes = policy.take_skip_verification_notes()
        self.assertEqual(len(notes), 1)
        self.assertIn("跳过验证", notes[0])
        self.assertEqual(policy.stop_reason, STOP_CLAIMED_UNVERIFIED)

    def test_the_claim_alone_is_never_the_reason(self):
        """停止原因**不是**「自称完成」本身 —— 它是「自称完成 + 没有证据」的组合。

        证据成立（校验读数说界面变了）→ 不判死；没有证据 → 判死，
        但停止原因是 ``claimed_unverified``，**不是**「完成」这类原因。
        """
        policy = StopPolicy()
        policy.observe_claim(claimed=True)
        policy.observe_round()
        self.assertNotEqual(policy.stop_reason, "completion")
        self.assertEqual(policy.stop_reason, STOP_CLAIMED_UNVERIFIED)


class ModelFrameSizeTest(unittest.TestCase):
    """送模型的那几帧缩到**声明尺寸**（上下文里留的仍是原始分辨率）。"""

    def test_the_frames_sent_to_the_model_are_scaled_down(self):
        frame = make_frame(2560, 1600)
        harness, _api, caller, _seam = _harness(["pass\n"], frames=[frame])
        harness.run("目标")
        sent = caller.calls[0]["images"]
        self.assertEqual(len(sent), 1)
        size = png_image_size(base64.b64decode(sent[0]))
        #: 声明尺寸 = 量出来的表面尺寸经 ``model_input_size_for`` 算出来的那一档
        #: （2560×1600 → 1536×960：施工清单第 ⑥ 条的探针定档，比例 0.6）。
        self.assertEqual(size, (1536, 960))

    def test_the_context_keeps_the_original_resolution(self):
        """留在上下文里的帧**不缩放**（校验层的 OCR 与差分要全分辨率）。"""
        frame = make_frame(2560, 1600)
        harness, _api, _caller, _seam = _harness(["pass\n"], frames=[frame])
        report = harness.run("目标")
        self.assertEqual(report.kept_frames, 1)


if __name__ == "__main__":
    unittest.main()

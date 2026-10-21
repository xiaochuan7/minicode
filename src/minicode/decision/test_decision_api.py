"""**动作程序能看到的接口面**的单测（工单 D-1 / D-2）。

四条判据：

* **白名单只有一份**：程序作用域里的名字与
  :data:`minicode.action.program.PROGRAM_API_NAMES` **必须一致** ——
  那个校验**不许被绕过**（多一个能调的名字就炸）；
* ``observe()`` 的**返回形状**（含「判不判得出来」的标记）；
* ``last_check()`` 的**三种情形**（还没动作 / 有动作 / 期望不成立）；
* **不许假装读到**：OCR 不可用时 ``text`` 是空串且 ``text_available`` 是 false。

⚠️ 假实现**不碰真桌面、不产生模型调用**（工单 M10 AC 7 的分工）。
"""

from __future__ import annotations

import unittest
from unittest import mock

from minicode.action.errors import ErrorCode
from minicode.action.program import PROGRAM_API_NAMES, run_program
from minicode.action.protocol import Action, ActionKind, ActionResult
from minicode.action.seam import SeamResult
from minicode.verification.diff import OUTCOME_CHANGED, OUTCOME_NO_CHANGE, ChangeResult
from minicode.verification.fakes import FakeOcrPort, make_frame
from minicode.verification.layer import VerificationResult

from .api import ProtocolApi


class _FakeExecutor:
    """假的动作执行器（输入文本 / 按键走它）。"""

    def execute(self, action) -> ActionResult:
        return ActionResult(ok=True)


class _FakeSeam:
    """假的动作执行缝：按脚本回结果（**不下发任何动作**）。

    ``frames`` 是截图端口的脚本（用完重复最后一个 —— 画面不再变化）。
    """

    def __init__(self, *, frames=(), error_code=None):
        self._frames = list(frames)
        self.error_code = error_code
        #: 抓了几次画面（截图端口被叫了几次）。
        self.captures = 0
        #: 收到过哪些请求。
        self.runs: list[ActionKind] = []

    @property
    def executor(self):
        return _FakeExecutor()

    def run(self, request) -> SeamResult:
        self.runs.append(request.action_kind)
        if request.action_kind is ActionKind.SCREENSHOT:
            self.captures += 1
            frame = self._frames.pop(0) if self._frames else make_frame()
            return SeamResult(
                action=Action(ActionKind.SCREENSHOT, {}, "截一张图"),
                final_point=None,
                intent="截一张图",
                result=ActionResult(ok=True, captured=frame),
            )
        return SeamResult(
            action=Action(request.action_kind, {}, "点击"),
            final_point=(10, 20),
            intent="点击",
            result=ActionResult(
                ok=self.error_code is None,
                error_code=self.error_code,
                landing_point=(10, 20),
            ),
        )


def _reader(reading: dict | None):
    """假的**旁路出口**：返回最新一条追踪记录里的校验读数。"""

    def read() -> dict | None:
        return reading

    return read


def _verification(*, changed: bool, satisfied: bool | None) -> dict:
    """一次**真的**校验结果（用校验层自己的类型造 —— 形状不另写一份）。"""
    outcome = OUTCOME_CHANGED if changed else OUTCOME_NO_CHANGE
    return VerificationResult(
        outcome=outcome,
        satisfied=satisfied,
        change=ChangeResult(outcome=outcome),
    ).to_dict()


class WhitelistTest(unittest.TestCase):
    """白名单只有一份 —— 那个校验不许被绕过。"""

    def test_the_program_scope_is_exactly_the_whitelist(self):
        api = ProtocolApi(seam=_FakeSeam())
        self.assertEqual(set(api.as_mapping()), set(PROGRAM_API_NAMES))
        self.assertIn("observe", PROGRAM_API_NAMES)
        self.assertIn("last_check", PROGRAM_API_NAMES)

    def test_an_extra_name_in_the_scope_is_rejected(self):
        """多一个能调的名字 → **直接炸**（不让它悄悄进程序作用域）。"""
        api = ProtocolApi(seam=_FakeSeam())
        with mock.patch(
            "minicode.decision.api.PROGRAM_API_NAMES",
            frozenset(set(PROGRAM_API_NAMES) | {"偷偷摸摸"}),
        ):
            with self.assertRaises(ValueError):
                api.as_mapping()

    def test_a_name_missing_from_the_whitelist_is_rejected(self):
        """少一个 → 也炸（白名单与接口面必须**一致**，不是「包含就行」）。"""
        api = ProtocolApi(seam=_FakeSeam())
        with mock.patch(
            "minicode.decision.api.PROGRAM_API_NAMES",
            frozenset(set(PROGRAM_API_NAMES) - {"observe"}),
        ):
            with self.assertRaises(ValueError):
                api.as_mapping()

    def test_the_program_can_actually_call_the_new_names(self):
        api = ProtocolApi(seam=_FakeSeam())
        outcome = run_program(
            "reading = observe()\n"
            "print(reading['changed'])\n"
            "print(last_check()['available'])\n",
            api.as_mapping(),
        )
        self.assertTrue(outcome.ok, outcome.error_message)
        self.assertIn("False", outcome.stdout)


class ObserveTest(unittest.TestCase):
    """``observe()``：让模型自己决定要不要新画面。"""

    def test_the_return_shape(self):
        api = ProtocolApi(seam=_FakeSeam(frames=[make_frame(64, 48)]))
        result = api.observe()
        self.assertTrue(result["ok"])
        self.assertEqual(result["size"], [64, 48])
        self.assertIsInstance(result["changed"], bool)
        self.assertIsInstance(result["text"], str)

    def test_the_first_observation_cannot_say_whether_it_changed(self):
        """第一次观察**没有「上一次」**可比 → 如实说判不了。"""
        api = ProtocolApi(seam=_FakeSeam(frames=[make_frame(64, 48)]))
        result = api.observe()
        self.assertFalse(result["changed_available"])
        self.assertFalse(result["changed"])

    def test_the_second_observation_reports_the_change(self):
        seam = _FakeSeam(
            frames=[make_frame(64, 48), make_frame(64, 48, changed_blocks=[0])]
        )
        api = ProtocolApi(seam=seam)
        api.observe()
        result = api.observe()
        self.assertTrue(result["changed_available"])
        self.assertTrue(result["changed"])

    def test_an_unchanged_screen_is_reported_as_unchanged(self):
        seam = _FakeSeam(frames=[make_frame(64, 48), make_frame(64, 48)])
        api = ProtocolApi(seam=seam)
        api.observe()
        result = api.observe()
        self.assertTrue(result["changed_available"])
        self.assertFalse(result["changed"])

    def test_without_an_ocr_port_the_text_is_not_faked(self):
        """没接 OCR → ``text`` 空串 + ``text_available`` false（**不假装读到**）。"""
        api = ProtocolApi(seam=_FakeSeam())
        result = api.observe()
        self.assertEqual(result["text"], "")
        self.assertFalse(result["text_available"])

    def test_the_ocr_reading_is_handed_over(self):
        api = ProtocolApi(seam=_FakeSeam(), ocr_port=FakeOcrPort(text="开始"))
        result = api.observe()
        self.assertEqual(result["text"], "开始")
        self.assertTrue(result["text_available"])

    def test_an_unreadable_ocr_is_not_faked(self):
        """OCR 读不出来 → 空串 + false（读不到 ≠ 读到空）。"""
        api = ProtocolApi(
            seam=_FakeSeam(),
            ocr_port=FakeOcrPort(error=OSError("没有 OCR 后端")),
        )
        result = api.observe()
        self.assertEqual(result["text"], "")
        self.assertFalse(result["text_available"])

    def test_observe_does_not_inject_anything(self):
        """观察是**只看不动作**：它只走截图端口。"""
        seam = _FakeSeam()
        api = ProtocolApi(seam=seam)
        api.observe()
        self.assertEqual(seam.runs, [ActionKind.SCREENSHOT])


class LastCheckTest(unittest.TestCase):
    """``last_check()``：上一次动作的校验读数进程序作用域。

    校验读数从**旁路出口**（追踪记录）读 —— 动作协议保持窄返回类型，
    这里**不重跑校验**。
    """

    def test_before_any_action_there_is_no_reading(self):
        """还没做过任何动作 → 明确的「还没有读数」，**不返回空字典冒充成功**。"""
        api = ProtocolApi(seam=_FakeSeam())
        result = api.last_check()
        self.assertFalse(result["available"])
        self.assertIsNone(result["changed"])
        self.assertIsNone(result["expectation_held"])
        self.assertIsNone(result["landing_deviation_px"])
        self.assertIsNone(result["error_code"])
        self.assertTrue(result["reason"])

    def test_after_an_action_the_reading_is_there(self):
        api = ProtocolApi(
            seam=_FakeSeam(),
            verification_reader=_reader(_verification(changed=True, satisfied=None)),
        )
        api.click("开始")
        result = api.last_check()
        self.assertTrue(result["available"])
        self.assertTrue(result["changed"])
        #: **没有期望时是 None**，不是 False。
        self.assertIsNone(result["expectation_held"])
        self.assertIsNone(result["error_code"])

    def test_a_failed_expectation_is_reported_as_not_held(self):
        api = ProtocolApi(
            seam=_FakeSeam(error_code=ErrorCode.EXPECTATION_FAILED),
            verification_reader=_reader(
                _verification(changed=False, satisfied=False)
            ),
        )
        api.click("开始")
        result = api.last_check()
        self.assertTrue(result["available"])
        self.assertFalse(result["changed"])
        self.assertIs(result["expectation_held"], False)
        self.assertEqual(result["error_code"], ErrorCode.EXPECTATION_FAILED.value)

    def test_the_landing_deviation_is_not_made_up(self):
        """校验层**没有**落点偏离量这个读数 → 如实给 None（**不编一个数**）。"""
        api = ProtocolApi(
            seam=_FakeSeam(),
            verification_reader=_reader(_verification(changed=True, satisfied=None)),
        )
        api.click("开始")
        result = api.last_check()
        self.assertIsNone(result["landing_deviation_px"])
        self.assertIn("落点偏离量", result["reason"])

    def test_an_action_without_a_check_says_so(self):
        """输入文本 / 按键这一类不产生校验读数 → 明确说「没有读数」。"""
        api = ProtocolApi(
            seam=_FakeSeam(),
            verification_reader=_reader(_verification(changed=True, satisfied=None)),
        )
        api.type_text("你好")
        result = api.last_check()
        self.assertFalse(result["available"])
        self.assertIsNone(result["changed"])
        self.assertTrue(result["reason"])

    def test_an_older_reading_is_not_reported_as_the_last_one(self):
        """上一次动作没有读数时，**不拿更早那一步的读数冒充**。"""
        api = ProtocolApi(
            seam=_FakeSeam(),
            verification_reader=_reader(_verification(changed=True, satisfied=None)),
        )
        api.click("开始")
        self.assertTrue(api.last_check()["available"])
        api.type_text("你好")
        self.assertFalse(api.last_check()["available"])


if __name__ == "__main__":
    unittest.main()

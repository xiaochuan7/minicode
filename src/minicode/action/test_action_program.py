"""**受控执行环境**的外部行为单测（工单 M10 AC 6）。

只断言对外可见的契约：

* 动作程序**只能调用动作协议暴露的动作**（多给一个名字就拒）；
* **任意系统调用与任意文件写入被拒** —— 被拒时报错，**不静默放过**，
  而且**一个动作都不下发**（执行前就拒）；
* 跑通时标准输出 / 标准错误 / 异常都如实回传（决策层要回灌给模型）；
* 死循环有规模上限兜底。

⚠️ **决策层（L3）不在单测覆盖范围内**（工单 M10 AC 7）：
本文件测的是**动作协议里 ``execute_code`` 这一类动作的执行契约**，
不是决策层的行为；决策层的验收落在端到端评测上。
"""

from __future__ import annotations

import unittest

from minicode.action.errors import ErrorCode
from minicode.action.program import (
    PROGRAM_API_NAMES,
    ProgramLimits,
    run_program,
    scan_program,
)


def _api(recorder: list[str] | None = None):
    """一套假接口：记录被调用过，不真做任何事。"""
    calls = recorder if recorder is not None else []

    def click(target: str) -> dict:
        calls.append(f"click:{target}")
        return {"ok": True, "point": [10, 20]}

    def find(target: str) -> dict:
        calls.append(f"find:{target}")
        return {"ok": True, "point": [10, 20]}

    def type_text(text: str) -> dict:
        calls.append(f"type_text:{text}")
        return {"ok": True}

    def press_key(*keys: str) -> dict:
        calls.append(f"press_key:{','.join(keys)}")
        return {"ok": True}

    def wait(ms: int) -> dict:
        calls.append(f"wait:{ms}")
        return {"ok": True}

    def screenshot() -> dict:
        calls.append("screenshot")
        return {"ok": True, "width": 640, "height": 480, "sha256": "deadbeef"}

    def ask_user(reason: str) -> dict:
        calls.append(f"ask_user:{reason}")
        return {"ok": True}

    return {
        "click": click,
        "find": find,
        "type_text": type_text,
        "press_key": press_key,
        "wait": wait,
        "screenshot": screenshot,
        "ask_user": ask_user,
    }


class ProgramApiSurfaceTest(unittest.TestCase):
    """程序能看到的接口面就是协议暴露的那几个。"""

    def test_api_names_are_exactly_the_protocol_actions(self):
        self.assertEqual(
            set(PROGRAM_API_NAMES),
            {"click", "find", "type_text", "press_key", "wait", "screenshot", "ask_user"},
        )

    def test_extra_api_name_is_rejected(self):
        outcome = run_program("pass", {"click": lambda target: None, "os": print})
        self.assertFalse(outcome.ok)
        self.assertEqual(outcome.error_code, ErrorCode.PROGRAM_REJECTED)
        self.assertIn("os", outcome.error_message or "")

    def test_program_can_call_the_protocol_actions(self):
        calls: list[str] = []
        outcome = run_program(
            "click('开始')\ntype_text('你好')\npress_key('enter')\n",
            _api(calls),
        )
        self.assertTrue(outcome.ok, outcome.error_message)
        self.assertEqual(calls, ["click:开始", "type_text:你好", "press_key:enter"])


class ForbiddenCallsTest(unittest.TestCase):
    """任意系统调用与任意文件写入被拒。"""

    def test_import_is_rejected(self):
        outcome = run_program("import os\nos.system('dir')\n", _api())
        self.assertFalse(outcome.ok)
        self.assertEqual(outcome.error_code, ErrorCode.PROGRAM_REJECTED)
        self.assertIn("import", outcome.error_message or "")

    def test_from_import_is_rejected(self):
        outcome = run_program("from subprocess import run\n", _api())
        self.assertFalse(outcome.ok)
        self.assertEqual(outcome.error_code, ErrorCode.PROGRAM_REJECTED)

    def test_open_for_writing_is_rejected(self):
        outcome = run_program("open('C:/tmp/x.txt', 'w').write('hi')\n", _api())
        self.assertFalse(outcome.ok)
        self.assertEqual(outcome.error_code, ErrorCode.PROGRAM_REJECTED)
        self.assertIn("open", outcome.error_message or "")

    def test_eval_and_exec_are_rejected(self):
        for source in ("eval('1+1')\n", "exec('x=1')\n", "__import__('os')\n"):
            with self.subTest(source=source):
                outcome = run_program(source, _api())
                self.assertFalse(outcome.ok)
                self.assertEqual(outcome.error_code, ErrorCode.PROGRAM_REJECTED)

    def test_dunder_attribute_access_is_rejected(self):
        outcome = run_program("print((1).__class__.__base__)\n", _api())
        self.assertFalse(outcome.ok)
        self.assertEqual(outcome.error_code, ErrorCode.PROGRAM_REJECTED)

    def test_dunder_in_a_format_string_is_rejected(self):
        """属性访问的**运行期**写法也堵掉。

        ``"{0.__class__}".format(x)`` 在 AST 里看不见属性节点，
        但运行期会解析出 ``__class__`` —— 因此**含双下划线的字符串常量**
        同样被拒。
        """
        outcome = run_program("x = 1\nprint('{0.__class__}'.format(x))\n", _api())
        self.assertFalse(outcome.ok)
        self.assertEqual(outcome.error_code, ErrorCode.PROGRAM_REJECTED)
        self.assertIn("双下划线", outcome.error_message or "")

    def test_rejected_program_performs_no_action(self):
        calls: list[str] = []
        outcome = run_program(
            "click('开始')\nopen('C:/tmp/x.txt', 'w')\n",
            _api(calls),
        )
        self.assertFalse(outcome.ok)
        self.assertEqual(calls, [], "被拒的程序不许下发任何动作")

    def test_scan_reports_every_reason(self):
        problems = scan_program("import os\nopen('a')\n")
        self.assertGreaterEqual(len(problems), 2)


class ProgramOutcomeTest(unittest.TestCase):
    """跑通与跑挂时结果都如实回传。"""

    def test_stdout_is_captured(self):
        outcome = run_program("print('hello')\n", _api())
        self.assertTrue(outcome.ok)
        self.assertIn("hello", outcome.stdout)

    def test_exception_is_reported_not_swallowed(self):
        outcome = run_program("raise ValueError('boom')\n", _api())
        self.assertFalse(outcome.ok)
        self.assertEqual(outcome.error_code, ErrorCode.EXECUTE_FAILED)
        self.assertIn("boom", outcome.error_message or "")

    def test_syntax_error_is_reported(self):
        outcome = run_program("def (:", _api())
        self.assertFalse(outcome.ok)
        self.assertEqual(outcome.error_code, ErrorCode.PROGRAM_REJECTED)

    def test_infinite_loop_is_stopped_by_the_line_limit(self):
        outcome = run_program(
            "while True:\n    pass\n",
            _api(),
            limits=ProgramLimits(max_lines=500),
        )
        self.assertFalse(outcome.ok)
        self.assertEqual(outcome.error_code, ErrorCode.PROGRAM_REJECTED)
        self.assertIn("规模上限", outcome.error_message or "")

    def test_control_flow_is_allowed(self):
        """动作程序**可以**含 if/for —— 这正是它相对逐条吐动作的收益。"""
        calls: list[str] = []
        outcome = run_program(
            "for name in ['甲', '乙']:\n"
            "    if find(name)['ok']:\n"
            "        click(name)\n"
            "        break\n",
            _api(calls),
        )
        self.assertTrue(outcome.ok, outcome.error_message)
        self.assertEqual(calls, ["find:甲", "click:甲"])


if __name__ == "__main__":
    unittest.main()

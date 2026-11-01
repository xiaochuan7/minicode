"""四级授权、护栏策略、不可逆判定、窗口黑名单的**纯逻辑单测**（W2）。

覆盖护栏 2（黑名单）、护栏 3（不可逆按影响面分类）与授权级别语义；
每条判定都给「命中」与「不命中」两面。
"""

import unittest

from minicode.guard.blacklist import match_blacklist
from minicode.guard.irreversible import (
    ImpactCategory,
    classify_impact,
    impact_verdict,
)
from minicode.guard.levels import AuthLevel, parse_level
from minicode.guard.policy import ESTOP_VKEYS, GuardPolicy, load_policy


class AuthLevelTest(unittest.TestCase):
    """四级授权的解析与缺省。"""

    def test_parse_level_accepts_all_four_levels_case_insensitively(self):
        for raw, expected in (
            ("L0", AuthLevel.L0),
            ("l1", AuthLevel.L1),
            (" L2 ", AuthLevel.L2),
            ("l3", AuthLevel.L3),
        ):
            with self.subTest(raw=raw):
                self.assertEqual(parse_level(raw), expected)

    def test_an_unknown_level_is_rejected_not_guessed(self):
        with self.assertRaises(ValueError):
            parse_level("L4")
        with self.assertRaises(ValueError):
            parse_level("")

    def test_default_policy_is_l0_readonly_with_default_budget(self):
        policy = GuardPolicy()
        self.assertEqual(policy.level, AuthLevel.L0)
        self.assertEqual(policy.window_whitelist, ())
        self.assertEqual(policy.budget.max_steps, 30)
        self.assertEqual(policy.budget.max_seconds, 300.0)
        self.assertEqual(policy.budget.max_model_calls, 50)
        self.assertTrue(policy.estop_enabled)
        self.assertEqual(policy.estop_vkeys, ESTOP_VKEYS)

    def test_load_policy_reads_budget_overrides_from_environment(self):
        import os

        from minicode.guard.policy import GuardBudget

        env = {
            "MINICODE_BUDGET_STEPS": "7",
            "MINICODE_BUDGET_SECONDS": "12.5",
            "MINICODE_BUDGET_MODEL_CALLS": "9",
        }
        saved = {key: os.environ.get(key) for key in env}
        try:
            os.environ.update(env)
            policy = load_policy(level="L1", whitelist=(" 记事本 ", ""))
        finally:
            for key, value in saved.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
        self.assertEqual(policy.level, AuthLevel.L1)
        self.assertEqual(policy.window_whitelist, ("记事本",))
        self.assertEqual(policy.budget, GuardBudget(max_steps=7, max_seconds=12.5, max_model_calls=9))


class ImpactClassificationTest(unittest.TestCase):
    """护栏 3：影响面分类（词表判定，真实元素文本一起进判定）。"""

    def test_irreversible_impacts_are_detected_from_wording(self):
        cases = [
            ("保存文件", ImpactCategory.WRITE_FILE),
            ("另存为到桌面", ImpactCategory.WRITE_FILE),
            ("发送这条消息", ImpactCategory.SEND),
            ("删除选中的文件", ImpactCategory.DELETE),
            ("清空回收站", ImpactCategory.DELETE),
            ("提交表单", ImpactCategory.SUBMIT_FORM),
            ("确认订单", ImpactCategory.SUBMIT_FORM),
            ("关闭未保存的窗口", ImpactCategory.CLOSE_UNSAVED),
            ("支付订单", ImpactCategory.PAYMENT),
            ("打开注册表编辑器", ImpactCategory.SYSTEM_CHANGE),
        ]
        for text, expected in cases:
            with self.subTest(text=text):
                self.assertEqual(classify_impact(target_description=text), expected)

    def test_real_element_text_overrides_benign_wording(self):
        # 审查安全 #2 同款纪律：模型说「确定」，实际控件叫「删除」——
        # 事实（真实控件名）一起进判定。
        self.assertEqual(
            classify_impact(target_description="确定", real_element_text="删除"),
            ImpactCategory.DELETE,
        )

    def test_benign_wording_is_reversible(self):
        for text in ("开始", "搜索", "截图", "点击搜索框", ""):
            with self.subTest(text=text):
                self.assertEqual(
                    classify_impact(target_description=text),
                    ImpactCategory.REVERSIBLE,
                )

    def test_approval_catalog_categories_map_into_impacts(self):
        # 与审批目录同源：目录命中的「运行命令行」映射到 SYSTEM_CHANGE。
        self.assertEqual(
            classify_impact(params_text="shutdown /s"),
            ImpactCategory.SYSTEM_CHANGE,
        )


class ImpactVerdictTest(unittest.TestCase):
    """护栏 3 的处置矩阵：影响面 × 授权级别。"""

    def test_irreversible_actions_are_denied_on_l0_and_l1(self):
        for level in (AuthLevel.L0, AuthLevel.L1):
            for category in (
                ImpactCategory.WRITE_FILE,
                ImpactCategory.SEND,
                ImpactCategory.DELETE,
                ImpactCategory.SUBMIT_FORM,
                ImpactCategory.CLOSE_UNSAVED,
                ImpactCategory.PAYMENT,
                ImpactCategory.SYSTEM_CHANGE,
            ):
                with self.subTest(level=level, category=category):
                    verdict = impact_verdict(category, level)
                    self.assertEqual(verdict.verdict, "deny")
                    self.assertTrue(verdict.irreversible)

    def test_l2_requires_human_approval_for_irreversible_actions(self):
        verdict = impact_verdict(ImpactCategory.DELETE, AuthLevel.L2)
        self.assertEqual(verdict.verdict, "approval_required")

    def test_l3_allows_but_records_irreversible_actions(self):
        verdict = impact_verdict(ImpactCategory.DELETE, AuthLevel.L3)
        self.assertEqual(verdict.verdict, "allow")
        self.assertTrue(verdict.irreversible)

    def test_reversible_actions_are_allowed_on_every_level(self):
        for level in AuthLevel:
            with self.subTest(level=level):
                verdict = impact_verdict(ImpactCategory.REVERSIBLE, level)
                self.assertEqual(verdict.verdict, "allow")
                self.assertFalse(verdict.irreversible)


class WindowBlacklistTest(unittest.TestCase):
    """护栏 2：窗口黑名单命中即拒（类名或标题，大小写不敏感）。"""

    def test_blacklisted_windows_hit_by_class_or_title(self):
        cases = [
            ("TaskmgrWindow", "任务管理器"),  # 类名命中
            ("Notepad", "任务管理器"),  # 标题命中
            ("regedit_regedit", "注册表编辑器"),
            ("KeePass", "KeePass Password Safe"),  # 密码管理器标题
            ("CASCADIA_HOSTING_WINDOW_CLASS", "Windows Terminal"),
            ("ConsoleWindowClass", "C:\\Windows\\system32\\cmd.exe"),
            ("CabinetWClass", "文档"),
        ]
        for cls, title in cases:
            with self.subTest(cls=cls, title=title):
                reason = match_blacklist(cls, title)
                self.assertIsNotNone(reason, f"{cls!r}/{title!r} 应命中黑名单")

    def test_benign_windows_do_not_hit_the_blacklist(self):
        for cls, title in (
            ("Notepad", "无标题 - 记事本"),
            ("Chrome_WidgetWin_1", "Google Chrome"),
            ("Shell_TrayWnd", "任务栏"),
        ):
            with self.subTest(cls=cls, title=title):
                self.assertIsNone(match_blacklist(cls, title))

    def test_the_hit_reason_names_why(self):
        reason = match_blacklist("TaskmgrWindow", "")
        self.assertIn("任务管理器", reason)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

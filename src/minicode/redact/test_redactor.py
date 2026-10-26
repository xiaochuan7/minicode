"""控件树脱敏器（M2）的**纯函数单测**（工单 AC 1~AC 3）。

只测外部行为：模型侧文本里出现什么 / 不出现什么、计数对不对。
纯函数单测：不引入假实现，也不构成第二条缝。

工单 M2 的口径：

* AC 1 —— 五类敏感模式（密钥前缀形态 / JWT 三段 / 长 base64 / ≥32 位十六进制串 / 私钥头）
  逐类不出现，命中处出现固定占位符；
* AC 2 —— 密码框（``IsPassword=True``）的值是固定占位符，明文不出现在任何输出里；
* AC 3 —— 字段白名单；正文类控件的值字段不出现；未登记的新字段一律不外发（fail-closed）。
"""

from __future__ import annotations

import unittest

from minicode.action.ports import ControlCandidate
from minicode.redact.config import ModelContextConfig
from minicode.redact.patterns import PASSWORD_PLACEHOLDER, SECRET_PLACEHOLDER
from minicode.redact.redactor import ModelTreeView, TreeRedactor

#: 工单 AC 1 点名的五类敏感模式。
SECRET_CASES: dict[str, str] = {
    "密钥前缀形态": "sk-abcdef0123456789abcdef",
    "JWT 三段": (
        "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
        ".eyJzdWIiOiIxMjM0NTY3ODkwIn0"
        ".dBjftJeZ4CVPmB92K27uhbUJU1p1r_wW1gFWFOEjXk"
    ),
    "长 base64": (
        "QUJDREVGR0hJSktMTU5PUFFSU1RVVldYWVphYmNkZWZnaGlqa2xtbm9wcXJzdHV2d3h5eg=="
    ),
    "长十六进制串": "0123456789abcdef0123456789abcdef",
    "私钥头": "-----BEGIN RSA PRIVATE KEY-----",
}


def _candidate(**overrides) -> ControlCandidate:
    base = {"name": "控件", "control_type": "Button", "rect": (0, 0, 100, 30)}
    base.update(overrides)
    return ControlCandidate(**base)


def _render(candidates) -> ModelTreeView:
    """用**合法**配置（送树进模型上下文已启用 + 脱敏已启用）渲染一次。"""
    config = ModelContextConfig(send_tree_to_model=True, redaction_enabled=True)
    return TreeRedactor(config).render(candidates)


class SecretPatternTest(unittest.TestCase):
    """AC 1：五类敏感模式逐类被替换成固定占位符。"""

    def test_each_secret_class_is_removed_from_the_model_side_text(self):
        for label, secret in SECRET_CASES.items():
            with self.subTest(模式=label):
                view = _render([_candidate(name=f"标题 {secret} 尾巴")])
                self.assertNotIn(secret, view.text)
                self.assertIn(SECRET_PLACEHOLDER, view.text)

    def test_one_snapshot_with_all_five_classes_is_clean(self):
        # 工单 AC 1 的字面形态：一份**含五类敏感模式**的树快照。
        candidates = [
            _candidate(name=f"第 {index} 个 {secret}")
            for index, secret in enumerate(SECRET_CASES.values(), start=1)
        ]
        view = _render(candidates)
        for label, secret in SECRET_CASES.items():
            with self.subTest(模式=label):
                self.assertNotIn(secret, view.text)
        self.assertEqual(view.text.count(SECRET_PLACEHOLDER), len(SECRET_CASES))
        self.assertEqual(view.report.regex_hits, len(SECRET_CASES))

    def test_the_hit_is_replaced_by_one_fixed_placeholder(self):
        for label, secret in SECRET_CASES.items():
            with self.subTest(模式=label):
                view = _render([_candidate(name=secret)])
                self.assertEqual(view.text.count(SECRET_PLACEHOLDER), 1)
                self.assertNotIn(secret, view.text)

    def test_a_secret_in_the_help_text_is_removed_too(self):
        secret = SECRET_CASES["JWT 三段"]
        view = _render([_candidate(help_text=f"说明 {secret}")])
        self.assertNotIn(secret, view.text)

    def test_a_secret_in_a_non_body_value_is_removed(self):
        secret = SECRET_CASES["密钥前缀形态"]
        view = _render([_candidate(control_type="ComboBox", value=secret)])
        self.assertNotIn(secret, view.text)

    def test_the_hit_count_matches_the_number_of_hits(self):
        view = _render(
            [
                _candidate(name=f"a{SECRET_CASES['密钥前缀形态']}b"),
                _candidate(name=f"c{SECRET_CASES['私钥头']}d"),
            ]
        )
        self.assertEqual(view.report.regex_hits, 2)


class PasswordControlTest(unittest.TestCase):
    """AC 2：密码框的值是固定占位符，明文不出现在任何输出里。"""

    PLAINTEXT = "hunter2-correct-horse"

    def test_the_password_value_is_a_fixed_placeholder(self):
        view = _render(
            [_candidate(control_type="Edit", name="密码", value=self.PLAINTEXT, is_password=True)]
        )
        self.assertNotIn(self.PLAINTEXT, view.text)
        self.assertIn(PASSWORD_PLACEHOLDER, view.text)
        self.assertEqual(view.controls[0].value, PASSWORD_PLACEHOLDER)

    def test_the_plaintext_appears_in_no_output(self):
        view = _render(
            [_candidate(control_type="Edit", name="密码", value=self.PLAINTEXT, is_password=True)]
        )
        outputs = (
            view.text,
            "|".join(f"{c.name}|{c.value}|{c.help_text}" for c in view.controls),
            repr(view.report),
        )
        for output in outputs:
            self.assertNotIn(self.PLAINTEXT, output)

    def test_the_raw_value_does_not_leak_through_the_repr(self):
        # 值不进 repr：异常信息、日志、`print` 都算输出。
        candidate = _candidate(control_type="Edit", name="密码", value=self.PLAINTEXT, is_password=True)
        self.assertNotIn(self.PLAINTEXT, repr(candidate))

    def test_the_password_replacement_is_counted(self):
        view = _render(
            [_candidate(control_type="Edit", name="密码", value=self.PLAINTEXT, is_password=True)]
        )
        self.assertEqual(view.report.password_replacements, 1)


class FieldWhitelistTest(unittest.TestCase):
    """AC 3：字段白名单 + 正文类控件的值不外发 + 未登记字段 fail-closed。"""

    def test_only_the_whitelisted_fields_are_emitted(self):
        view = _render(
            [
                _candidate(
                    name="确定",
                    control_type="Button",
                    automation_id="okBtn",
                    rect=(10, 20, 110, 60),
                    help_text="按下确定",
                )
            ]
        )
        line = view.text.splitlines()[-1]
        self.assertIn('type="Button"', line)
        self.assertIn('name="确定"', line)
        self.assertIn('automation_id="okBtn"', line)
        self.assertIn("rect=(10,20,110,60)", line)
        self.assertIn('help="按下确定"', line)

    def test_body_controls_do_not_expose_their_value(self):
        for control_type in ("Edit", "Document", "Text"):
            with self.subTest(控件类型=control_type):
                view = _render(
                    [_candidate(control_type=control_type, name="正文", value="我正在输入的草稿")]
                )
                self.assertNotIn("我正在输入的草稿", view.text)
                self.assertNotIn("value=", view.text)
                self.assertEqual(view.report.dropped_body_fields, 1)

    def test_unregistered_fields_are_never_emitted(self):
        view = _render(
            [
                _candidate(
                    extra={
                        "未登记字段": "别的秘密",
                        "Password": SECRET_CASES["密钥前缀形态"],
                    }
                )
            ]
        )
        self.assertNotIn("别的秘密", view.text)
        self.assertNotIn(SECRET_CASES["密钥前缀形态"], view.text)
        self.assertEqual(view.report.dropped_unknown_fields, 2)

    def test_the_report_carries_the_four_counters_and_the_output_length(self):
        view = _render(
            [
                _candidate(name=f"按钮 {SECRET_CASES['密钥前缀形态']}"),
                _candidate(control_type="Edit", name="密码", value="x", is_password=True),
                _candidate(control_type="Edit", name="草稿", value="正文"),
                _candidate(name="别的", extra={"未知": "1"}),
            ]
        )
        self.assertEqual(view.report.regex_hits, 1)
        self.assertEqual(view.report.password_replacements, 1)
        self.assertEqual(view.report.dropped_body_fields, 1)
        self.assertEqual(view.report.dropped_unknown_fields, 1)
        self.assertEqual(view.report.output_length, len(view.text))


class RawDataTest(unittest.TestCase):
    """脱敏只作用于「要外发的表示」：用于定位的原始数据一个字节都不改。"""

    def test_the_raw_candidate_is_not_modified(self):
        secret = SECRET_CASES["密钥前缀形态"]
        candidate = _candidate(name=f"标题 {secret}", value="原值")
        view = _render([candidate])
        self.assertEqual(candidate.name, f"标题 {secret}")
        self.assertEqual(candidate.value, "原值")
        self.assertNotEqual(view.controls[0].name, candidate.name)

    def test_the_rect_is_kept_as_physical_pixels(self):
        view = _render([_candidate(rect=(11, 22, 133, 66))])
        self.assertEqual(view.controls[0].rect, (11, 22, 133, 66))


if __name__ == "__main__":
    unittest.main()

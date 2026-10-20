"""脱敏闸门与启用期校验的单测（工单 AC 4、AC 5）。

* AC 4 —— **单点出口 + 硬闸门**：全仓只有一个把树变成模型侧文本的出口；
  该出口在「脱敏未启用」时拒绝（返回通道不可用）且**不产生任何模型侧文本**；
  默认配置（脱敏关闭）下调用该出口 → 被拒且输出为空。
* AC 5 —— **启用期校验**：配置层拒绝「送树进模型上下文已启用 + 脱敏未启用」这一组合。
"""

from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

import minicode.redact
from minicode.action.ports import ControlCandidate
from minicode.redact.config import ModelContextConfig, load_model_context_config
from minicode.redact.errors import ChannelUnavailableError, RedactionConfigError
from minicode.redact.redactor import MODEL_SIDE_HEADER, TreeRedactor

_CANDIDATE = ControlCandidate(name="确定", control_type="Button", rect=(0, 0, 100, 30))


class HardGateTest(unittest.TestCase):
    """AC 4：脱敏未启用时，出口拒绝且不产生模型侧文本。"""

    def test_the_default_config_refuses_to_render(self):
        redactor = TreeRedactor(ModelContextConfig())
        with self.assertRaises(ChannelUnavailableError) as ctx:
            redactor.render([_CANDIDATE])
        self.assertIn("脱敏", str(ctx.exception))

    def test_no_model_side_text_is_produced_when_refused(self):
        redactor = TreeRedactor(ModelContextConfig())
        produced: list[str] = []
        try:
            produced.append(redactor.render([_CANDIDATE]).text)
        except ChannelUnavailableError:
            pass
        self.assertEqual(produced, [])

    def test_a_redaction_off_channel_is_refused_even_when_the_flag_is_on(self):
        # 合法配置（两个开关不冲突），但「送树进模型上下文」没开 → 通道不可用。
        redactor = TreeRedactor(ModelContextConfig(redaction_enabled=True))
        with self.assertRaises(ChannelUnavailableError):
            redactor.render([_CANDIDATE])

    def test_the_legal_combination_renders(self):
        redactor = TreeRedactor(
            ModelContextConfig(send_tree_to_model=True, redaction_enabled=True)
        )
        view = redactor.render([_CANDIDATE])
        self.assertIn("确定", view.text)


class EnableTimeValidationTest(unittest.TestCase):
    """AC 5：配置层拒绝非法组合（明确报错，不静默放过）。"""

    def test_sending_the_tree_without_redaction_is_rejected(self):
        with self.assertRaises(RedactionConfigError) as ctx:
            ModelContextConfig(send_tree_to_model=True, redaction_enabled=False)
        self.assertIn("脱敏", str(ctx.exception))

    def test_the_error_names_both_sides_of_the_combination(self):
        with self.assertRaises(RedactionConfigError) as ctx:
            ModelContextConfig(send_tree_to_model=True, redaction_enabled=False)
        message = str(ctx.exception)
        self.assertIn("送树进模型上下文", message)
        self.assertIn("脱敏", message)

    def test_both_switches_off_is_legal(self):
        config = ModelContextConfig()
        self.assertFalse(config.send_tree_to_model)
        self.assertFalse(config.redaction_enabled)

    def test_both_switches_on_is_legal(self):
        config = ModelContextConfig(send_tree_to_model=True, redaction_enabled=True)
        self.assertTrue(config.send_tree_to_model)
        self.assertTrue(config.redaction_enabled)


class RepoConfigLoadingTest(unittest.TestCase):
    """AC 5 的接线：这两个开关从**仓库 ``.env``** 读（唯一配置源）。"""

    def _env(self, body: str) -> Path:
        directory = Path(tempfile.mkdtemp())
        path = directory / ".env"
        path.write_text(body, encoding="utf-8")
        self.addCleanup(shutil.rmtree, directory, ignore_errors=True)
        return path

    def test_the_switches_are_read_from_the_env_file(self):
        path = self._env(
            "MINICODE_SEND_TREE_TO_MODEL=true\nMINICODE_REDACTION_ENABLED=1\n"
        )
        config = load_model_context_config(path)
        self.assertTrue(config.send_tree_to_model)
        self.assertTrue(config.redaction_enabled)

    def test_missing_keys_mean_both_switches_are_off(self):
        config = load_model_context_config(self._env("MINICODE_MODEL=minimax-m3.1-flash-free\n"))
        self.assertFalse(config.send_tree_to_model)
        self.assertFalse(config.redaction_enabled)

    def test_loading_an_illegal_combination_from_the_env_file_is_rejected(self):
        path = self._env("MINICODE_SEND_TREE_TO_MODEL=true\n")
        with self.assertRaises(RedactionConfigError):
            load_model_context_config(path)


class SingleExitTest(unittest.TestCase):
    """AC 4：全仓只有一个把树变成模型侧文本的出口。"""

    def _modules(self) -> list[Path]:
        package_dir = Path(minicode.redact.__file__).resolve().parent
        return [
            path
            for path in sorted(package_dir.parent.rglob("*.py"))
            if not path.name.startswith("test_")
        ]

    def test_only_one_module_produces_model_side_text(self):
        hits = [
            path
            for path in self._modules()
            if MODEL_SIDE_HEADER in path.read_text(encoding="utf-8")
        ]
        self.assertEqual([path.name for path in hits], ["redactor.py"])

    def test_the_placeholders_only_live_in_three_known_modules(self):
        # 第二个出口必然要用这两个占位符 → 这条断言把它也一起看住。
        # 多出第四个模块时，先问一句「它是不是又一个出口」。
        hits = [
            path
            for path in self._modules()
            if "SECRET_PLACEHOLDER" in path.read_text(encoding="utf-8")
            or "PASSWORD_PLACEHOLDER" in path.read_text(encoding="utf-8")
        ]
        self.assertEqual(
            [path.name for path in hits], ["records.py", "patterns.py", "redactor.py"]
        )


if __name__ == "__main__":
    unittest.main()

"""**PII 脱敏**的单测（审查报告安全 #3）。

原状：脱敏只有 5 类**密钥形态**正则，PII 零覆盖 —— 手机号 / 身份证 /
邮箱 / 银行卡原样进模型上下文与落盘日志。

新增四类（与密钥类同一套规则、同一个占位符：``[REDACTED]``）：

* 手机号（1[3-9] 开头的 11 位）；
* 身份证（18 位，末位可为 X）；
* 邮箱；
* 银行卡 / 长数字串（16~19 位数字 —— 16/19 位卡常见长度；
  **13 位 epoch 时间戳与 10 位订单号不误伤**）。

⚠️ 如实边界：这四类只防「**形态可识别**」的明文 ——
不是「已脱敏所有 PII」的承诺（README 隐私节已按此收窄）。
"""

from __future__ import annotations

import unittest

from minicode.redact.log import sanitize_record, scan_record
from minicode.redact.patterns import redact_secrets


class PiiPatternTests(unittest.TestCase):
    """四类 PII 各自命中、替换成占位符。"""

    def test_a_mobile_number_is_redacted(self):
        cleaned, hits = redact_secrets("联系 13812345678 确认")
        self.assertEqual(cleaned, "联系 [REDACTED] 确认")
        self.assertEqual(hits, 1)

    def test_an_id_card_number_is_redacted(self):
        cleaned, hits = redact_secrets("身份证 11010519491231002X 登记的")
        self.assertEqual(cleaned, "身份证 [REDACTED] 登记的")
        self.assertEqual(hits, 1)

    def test_an_email_address_is_redacted(self):
        cleaned, hits = redact_secrets("发给 zhang.san+work@example.com.cn 即可")
        self.assertEqual(cleaned, "发给 [REDACTED] 即可")
        self.assertEqual(hits, 1)

    def test_a_bank_card_number_is_redacted(self):
        cleaned, hits = redact_secrets("卡号 6222020200112233456 请核对")
        self.assertEqual(cleaned, "卡号 [REDACTED] 请核对")
        self.assertEqual(hits, 1)


class PiiFalsePositiveGuardTests(unittest.TestCase):
    """**不误伤**：普通数字与普通词不许被当 PII 抹掉。"""

    def test_a_13_digit_epoch_timestamp_survives(self):
        """13 位 epoch（毫秒时间戳）不是卡号 —— 不抹。"""
        cleaned, hits = redact_secrets("时间戳 1761955200000 记录")
        self.assertEqual(hits, 0)
        self.assertIn("1761955200000", cleaned)

    def test_a_short_order_number_survives(self):
        cleaned, hits = redact_secrets("订单 20250101 出库")
        self.assertEqual(hits, 0)

    def test_ordinary_words_containing_pii_like_fragments_survive(self):
        """英文普通词里的碎片（warm / model）不许被命令形态或 PII 规则误伤。"""
        cleaned, hits = redact_secrets("warm up the model please")
        self.assertEqual(hits, 0)
        self.assertEqual(cleaned, "warm up the model please")


class PiiThroughRecordSanitizeTests(unittest.TestCase):
    """PII 规则走的是**同一套** sanitize_record（摘要字段仍放行）。"""

    def test_a_record_with_pii_comes_out_clean(self):
        record = {
            "intent": "联系 13812345678",
            "result": {"error_message": "邮箱 zhang@example.com 不对"},
            "screenshot_sha256": "a" * 64,
        }
        sanitized = sanitize_record(record)
        self.assertNotIn("13812345678", str(sanitized))
        self.assertNotIn("zhang@example.com", str(sanitized))
        # 摘要字段（键名 + 64 位十六进制值）原样放行。
        self.assertEqual(sanitized["screenshot_sha256"], "a" * 64)
        self.assertEqual(scan_record(sanitized), [])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

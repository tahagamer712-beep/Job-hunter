import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import Settings  # noqa: E402


class TestScannerRequiresTosAcknowledgment(unittest.TestCase):
    def test_credentials_alone_are_not_enough(self):
        settings = Settings(telegram_api_id="123", telegram_api_hash="abc",
                             telegram_tos_acknowledged=False)
        self.assertFalse(settings.scanner_enabled,
                          "Scanner must not be enabled without explicit ToS acknowledgment")

    def test_acknowledgment_alone_without_credentials_is_not_enough(self):
        settings = Settings(telegram_api_id="", telegram_api_hash="",
                             telegram_tos_acknowledged=True)
        self.assertFalse(settings.scanner_enabled)

    def test_both_together_enable_scanner(self):
        settings = Settings(telegram_api_id="123", telegram_api_hash="abc",
                             telegram_tos_acknowledged=True)
        self.assertTrue(settings.scanner_enabled)

    def test_default_is_disabled(self):
        settings = Settings()
        self.assertFalse(settings.scanner_enabled)


if __name__ == "__main__":
    unittest.main()

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.source_tester import check_source, validate_identifier_format  # noqa: E402


class TestValidateIdentifierFormat(unittest.TestCase):
    def test_valid_username(self):
        result = validate_identifier_format("@Medical_Field")
        self.assertTrue(result.format_ok)

    def test_valid_t_me_link(self):
        result = validate_identifier_format("https://t.me/Medical_Field")
        self.assertTrue(result.format_ok)

    def test_invalid_format(self):
        result = validate_identifier_format("not a channel")
        self.assertFalse(result.format_ok)

    def test_too_short_username_invalid(self):
        result = validate_identifier_format("@ab")
        self.assertFalse(result.format_ok)


class TestSourceTester(unittest.IsolatedAsyncioTestCase):
    async def test_invalid_format_short_circuits_without_settings(self):
        result = await check_source("garbage")
        self.assertFalse(result.format_ok)
        self.assertFalse(result.live_checked)
        self.assertFalse(result.overall_ok)

    async def test_valid_format_no_settings_reports_untested(self):
        result = await check_source("@Medical_Field", settings=None)
        self.assertTrue(result.format_ok)
        self.assertFalse(result.live_checked)
        self.assertIsNone(result.live_ok)
        self.assertIn("not tested", result.message.lower())

    async def test_valid_format_scanner_disabled_reports_untested(self):
        class FakeSettings:
            scanner_enabled = False
        result = await check_source("@Medical_Field", settings=FakeSettings())
        self.assertTrue(result.format_ok)
        self.assertFalse(result.live_checked)

    async def test_untested_is_not_the_same_as_confirmed_ok(self):
        """
        M05 regression test: previously a single `ok=True` boolean was set
        even when live connectivity was never actually attempted, which
        could be misread as a confirmed-working source. live_ok=None now
        makes "untested" structurally distinct from "confirmed True".
        """
        result = await check_source("@Medical_Field", settings=None)
        self.assertIsNone(result.live_ok)
        self.assertNotEqual(result.live_ok, True)


if __name__ == "__main__":
    unittest.main()

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.contact_links import build_whatsapp_link, normalize_iraqi_number  # noqa: E402


class TestWhatsAppLink(unittest.TestCase):
    def test_local_format(self):
        self.assertEqual(normalize_iraqi_number("07838299611"), "9647838299611")

    def test_with_spaces_and_dashes(self):
        self.assertEqual(normalize_iraqi_number("0783 829-9611"), "9647838299611")

    def test_already_international(self):
        self.assertEqual(normalize_iraqi_number("+9647838299611"), "9647838299611")

    def test_with_leading_00(self):
        self.assertEqual(normalize_iraqi_number("00964 7838299611"), "9647838299611")

    def test_implausible_number_returns_none(self):
        self.assertIsNone(normalize_iraqi_number("12345"))
        self.assertIsNone(normalize_iraqi_number("0212345678"))  # doesn't start with 7 after 0

    def test_build_link_basic(self):
        link = build_whatsapp_link("07838299611")
        self.assertEqual(link, "https://wa.me/9647838299611")

    def test_build_link_with_prefilled_text(self):
        link = build_whatsapp_link("07838299611", prefilled_text="مرحباً، أرغب بالتقديم على الوظيفة")
        self.assertTrue(link.startswith("https://wa.me/9647838299611?text="))

    def test_build_link_returns_none_for_bad_number(self):
        self.assertIsNone(build_whatsapp_link("notanumber"))


if __name__ == "__main__":
    unittest.main()

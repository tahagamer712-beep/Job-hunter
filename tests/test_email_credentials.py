import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ.setdefault("ENCRYPTION_KEY", __import__("base64").urlsafe_b64encode(b"0" * 32).decode())

from src.email_credentials import (  # noqa: E402
    app_password_setup_link, decrypt_password, detect_smtp_provider,
    encrypt_password, looks_like_app_password,
)


class TestSmtpProviderDetection(unittest.TestCase):
    def test_gmail_detected(self):
        self.assertEqual(detect_smtp_provider("me@gmail.com"), ("smtp.gmail.com", 587))

    def test_outlook_detected(self):
        self.assertEqual(detect_smtp_provider("me@outlook.com"), ("smtp-mail.outlook.com", 587))

    def test_unknown_domain_returns_none(self):
        self.assertIsNone(detect_smtp_provider("me@somecustomdomain.iq"))

    def test_setup_link_for_known_host(self):
        self.assertIn("google.com", app_password_setup_link("smtp.gmail.com"))

    def test_setup_link_for_unknown_host_is_none(self):
        self.assertIsNone(app_password_setup_link("smtp.random.com"))


class TestPasswordEncryption(unittest.TestCase):
    def test_encrypt_decrypt_roundtrip(self):
        encrypted = encrypt_password("abcd efgh ijkl mnop")
        self.assertNotEqual(encrypted, "abcd efgh ijkl mnop")
        decrypted = decrypt_password(encrypted)
        self.assertEqual(decrypted, "abcd efgh ijkl mnop")

    def test_encrypted_value_is_not_plaintext_substring(self):
        encrypted = encrypt_password("mysecretapppassword")
        self.assertNotIn("mysecretapppassword", encrypted)


class TestAppPasswordShapeCheck(unittest.TestCase):
    def test_typical_app_password_shape_accepted(self):
        self.assertTrue(looks_like_app_password("abcd efgh ijkl mnop"))

    def test_too_short_rejected(self):
        self.assertFalse(looks_like_app_password("abc"))

    def test_contains_symbols_rejected(self):
        self.assertFalse(looks_like_app_password("my@normal!password#123"))


if __name__ == "__main__":
    unittest.main()

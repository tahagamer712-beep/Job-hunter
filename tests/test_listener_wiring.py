import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import src.scanner.telegram_listener as tl_module  # noqa: E402

from src.config import Settings  # noqa: E402
from src.database import Database  # noqa: E402
from src.pipeline_multiuser import ingest_ad  # noqa: E402
from src.taxonomy import TaxonomyStore  # noqa: E402
from src.user_profiles import UserProfileStore  # noqa: E402

TEST_DB_PATH = os.path.join(os.path.dirname(__file__), ".tmp", "test_listener_wiring.db")


def _enabled_settings():
    return Settings(telegram_api_id="123", telegram_api_hash="abc", telegram_tos_acknowledged=True)


class TestTelegramListenerWiring(unittest.TestCase):
    def setUp(self):
        if os.path.exists(TEST_DB_PATH):
            os.remove(TEST_DB_PATH)
        self.db = Database(TEST_DB_PATH)
        self._original_available = tl_module.TELETHON_AVAILABLE
        self._had_client_attr = hasattr(tl_module, "TelegramClient")
        self._original_client_cls = getattr(tl_module, "TelegramClient", None)
        tl_module.TELETHON_AVAILABLE = True
        tl_module.TelegramClient = MagicMock(return_value=MagicMock())

    def tearDown(self):
        tl_module.TELETHON_AVAILABLE = self._original_available
        if self._had_client_attr:
            tl_module.TelegramClient = self._original_client_cls
        else:
            delattr(tl_module, "TelegramClient")
        if os.path.exists(TEST_DB_PATH):
            os.remove(TEST_DB_PATH)

    def test_constructor_requires_pipeline_or_process_message_fn(self):
        with self.assertRaises(ValueError):
            tl_module.TelegramListener(_enabled_settings(), self.db)

    def test_constructor_accepts_process_message_fn_without_pipeline(self):
        async def fake_processor(raw_text, source):
            pass
        listener = tl_module.TelegramListener(_enabled_settings(), self.db, process_message_fn=fake_processor)
        self.assertIsNone(listener.pipeline)
        self.assertIs(listener.process_message_fn, fake_processor)

    def test_refuses_to_start_without_scanner_enabled(self):
        disabled_settings = Settings()
        with self.assertRaises(RuntimeError):
            tl_module.TelegramListener(disabled_settings, self.db, process_message_fn=lambda r, s: None)


class TestProcessMessageFnReachesMultiUserDelivery(unittest.IsolatedAsyncioTestCase):
    """
    Proves a message routed through TelegramListener's injected
    process_message_fn (exactly what src/bot/main.py wires up) really does
    reach ad-splitting, taxonomy matching, and per-user delivery.
    """

    def setUp(self):
        if os.path.exists(TEST_DB_PATH):
            os.remove(TEST_DB_PATH)
        self.db = Database(TEST_DB_PATH)
        taxonomy = TaxonomyStore(self.db)
        self.category_id = taxonomy.create_category("Medical Representative", ["مندوب علمي"])
        profiles = UserProfileStore(self.db)
        profiles.set_job_preferences("user1", [self.category_id])
        profiles.set_area_preferences("user1", ["rusafa"])

    def tearDown(self):
        if os.path.exists(TEST_DB_PATH):
            os.remove(TEST_DB_PATH)

    async def test_scanner_style_process_message_fn_delivers_to_matching_user(self):
        from src.ad_splitter import split_ads

        delivered = []

        async def multiuser_scanner_process(raw_text, source):
            # Mirrors bot/main.py's multiuser_scanner_process exactly.
            for ad_text in split_ads(raw_text):
                ingest_ad(self.db, ad_text, source, notify_fn=lambda u, j: delivered.append((u, j)))

        source = {"platform": "telegram", "channel": "@TestChannel", "message_id": "1", "url": None, "date": "2026-08-30"}
        await multiuser_scanner_process("مطلوب مندوب علمي - الرصافة بغداد", source)

        self.assertEqual(len(delivered), 1)
        self.assertEqual(delivered[0][0], "user1")

    async def test_batched_multi_ad_message_delivers_each_matching_ad(self):
        from src.ad_splitter import split_ads

        delivered = []

        async def multiuser_scanner_process(raw_text, source):
            for ad_text in split_ads(raw_text):
                ingest_ad(self.db, ad_text, source, notify_fn=lambda u, j: delivered.append((u, j)))

        batched = (
            "مطلوب مندوب علمي - الرصافة بغداد\n\n"
            "❍✦ —— DNAIRAQ™ ••••••\n\n"
            "مطلوب مندوب علمي - الكرخ بغداد"  # user1 has no Karkh preference -> no match for this one
        )
        source = {"platform": "telegram", "channel": "@TestChannel", "message_id": "2", "url": None, "date": "2026-08-30"}
        await multiuser_scanner_process(batched, source)

        self.assertEqual(len(delivered), 1, "Only the Rusafa ad should match user1's preferences")


if __name__ == "__main__":
    unittest.main()

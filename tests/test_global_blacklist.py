import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.database import Database  # noqa: E402
from src.pipeline_multiuser import ingest_ad  # noqa: E402
from src.taxonomy import TaxonomyStore  # noqa: E402
from src.user_profiles import UserProfileStore  # noqa: E402

TEST_DB_PATH = "/tmp/test_global_blacklist.db"
SOURCE = {"platform": "manual", "channel": None, "message_id": "1", "url": None, "date": "2026-08-30"}


class TestGlobalBlacklistEnforced(unittest.TestCase):
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

    def test_admin_global_blacklist_blocks_delivery_to_everyone(self):
        self.db.add_ignored_company("Scam Corp")
        result = ingest_ad(self.db, "مطلوب مندوب علمي - الرصافة بغداد. Company: Scam Corp", SOURCE)
        self.assertTrue(result.category_matched)
        self.assertEqual(result.delivered_to, [])

    def test_job_still_stored_for_audit_even_when_globally_blocked(self):
        self.db.add_ignored_company("Scam Corp")
        result = ingest_ad(self.db, "مطلوب مندوب علمي - الرصافة بغداد. Company: Scam Corp", SOURCE)
        self.assertIsNotNone(result.job_id)
        row = self.db.get_job(result.job_id)
        self.assertIsNotNone(row)

    def test_non_blacklisted_company_still_delivers(self):
        result = ingest_ad(self.db, "مطلوب مندوب علمي - الرصافة بغداد. Company: Good Corp", SOURCE)
        self.assertEqual(result.delivered_to, ["user1"])


if __name__ == "__main__":
    unittest.main()

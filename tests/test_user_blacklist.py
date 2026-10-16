import os
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.database import Database  # noqa: E402
from src.pipeline_multiuser import ingest_ad  # noqa: E402
from src.taxonomy import TaxonomyStore  # noqa: E402
from src.user_profiles import UserProfileStore  # noqa: E402

TEST_DB_PATH = os.path.join(os.path.dirname(__file__), ".tmp", "test_user_blacklist.db")
SOURCE = {"platform": "manual", "channel": None, "message_id": "1", "url": None, "date": "2026-08-30", "posted_at": datetime.now(timezone.utc).isoformat()}


class TestPerUserBlacklist(unittest.TestCase):
    def setUp(self):
        if os.path.exists(TEST_DB_PATH):
            os.remove(TEST_DB_PATH)
        self.db = Database(TEST_DB_PATH)
        self.taxonomy = TaxonomyStore(self.db)
        self.profiles = UserProfileStore(self.db)
        self.category_id = self.taxonomy.create_category("Medical Representative", ["مندوب علمي", "Medical Rep"])

    def tearDown(self):
        if os.path.exists(TEST_DB_PATH):
            os.remove(TEST_DB_PATH)

    def test_blacklist_is_personal_not_shared(self):
        self.profiles.set_job_preferences("user1", [self.category_id])
        self.profiles.set_area_preferences("user1", ["rusafa"])
        self.profiles.set_job_preferences("user2", [self.category_id])
        self.profiles.set_area_preferences("user2", ["rusafa"])

        self.db.add_user_ignored_company("user1", "Bad Company LLC")

        result = ingest_ad(
            self.db, "مطلوب مندوب علمي - الرصافة بغداد. Company: Bad Company LLC", SOURCE
        )
        self.assertNotIn("user1", result.delivered_to)
        self.assertIn("user2", result.delivered_to)

    def test_removing_from_blacklist_restores_delivery_on_new_ads(self):
        self.profiles.set_job_preferences("user1", [self.category_id])
        self.profiles.set_area_preferences("user1", ["rusafa"])
        self.db.add_user_ignored_company("user1", "Some Company")

        result1 = ingest_ad(self.db, "مطلوب مندوب علمي - الرصافة بغداد. Company: Some Company", SOURCE)
        self.assertNotIn("user1", result1.delivered_to)

        self.db.remove_user_ignored_company("user1", "Some Company")
        result2 = ingest_ad(
            self.db, "Medical Rep position open now in Al Rusafa, Baghdad. Employer: Some Company Two Ltd.", SOURCE
        )
        self.assertIn("user1", result2.delivered_to)

    def test_list_user_ignored_companies(self):
        self.db.add_user_ignored_company("user1", "Company A")
        self.db.add_user_ignored_company("user1", "Company B")
        self.db.add_user_ignored_company("user2", "Company C")
        self.assertEqual(set(self.db.list_user_ignored_companies("user1")), {"Company A", "Company B"})
        self.assertEqual(self.db.list_user_ignored_companies("user2"), ["Company C"])

    def test_is_company_ignored_by_user(self):
        self.db.add_user_ignored_company("user1", "Company A")
        self.assertTrue(self.db.is_company_ignored_by_user("user1", "Company A"))
        self.assertFalse(self.db.is_company_ignored_by_user("user2", "Company A"))
        self.assertFalse(self.db.is_company_ignored_by_user("user1", None))


if __name__ == "__main__":
    unittest.main()

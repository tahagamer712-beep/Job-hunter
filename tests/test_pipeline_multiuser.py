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

TEST_DB_PATH = os.path.join(os.path.dirname(__file__), ".tmp", "test_pipeline_multiuser.db")
SOURCE = {"platform": "manual", "channel": None, "message_id": "1", "url": None, "date": "2026-08-30", "posted_at": datetime.now(timezone.utc).isoformat()}


class TestMultiUserIngestion(unittest.TestCase):
    def setUp(self):
        if os.path.exists(TEST_DB_PATH):
            os.remove(TEST_DB_PATH)
        self.db = Database(TEST_DB_PATH)
        self.taxonomy = TaxonomyStore(self.db)
        self.profiles = UserProfileStore(self.db)

    def tearDown(self):
        if os.path.exists(TEST_DB_PATH):
            os.remove(TEST_DB_PATH)

    def test_unrecognized_title_queued_not_delivered(self):
        result = ingest_ad(self.db, "مطلوب صيدلي تواجد لصيدلية في الكرخ - واتساب 07711112222", SOURCE)
        self.assertFalse(result.category_matched)
        self.assertIsNotNone(result.pending_title_id)
        self.assertIsNone(result.delivered_to)

    def test_recognized_category_and_area_delivers_to_matching_user(self):
        category_id = self.taxonomy.create_category("Medical Representative", ["مندوب علمي"])
        self.profiles.set_job_preferences("user1", [category_id])
        self.profiles.set_area_preferences("user1", ["karkh"])

        result = ingest_ad(self.db, "مطلوب مندوب علمي - الكرخ بغداد. واتساب 07711112222", SOURCE)
        self.assertTrue(result.category_matched)
        self.assertEqual(result.delivered_to, ["user1"])

    def test_karkh_is_not_globally_excluded_anymore(self):
        """Core architecture change: Karkh must be deliverable if a user chose it."""
        category_id = self.taxonomy.create_category("Medical Representative", ["مندوب علمي"])
        self.profiles.set_job_preferences("karkh_fan", [category_id])
        self.profiles.set_area_preferences("karkh_fan", ["karkh"])
        self.profiles.set_job_preferences("rusafa_fan", [category_id])
        self.profiles.set_area_preferences("rusafa_fan", ["rusafa"])

        result = ingest_ad(self.db, "مطلوب مندوب علمي - الكرخ بغداد", SOURCE)
        self.assertIn("karkh_fan", result.delivered_to)
        self.assertNotIn("rusafa_fan", result.delivered_to)

    def test_duplicate_ad_not_reingested(self):
        category_id = self.taxonomy.create_category("Medical Representative", ["مندوب علمي"])
        text = "مطلوب مندوب علمي - الرصافة بغداد"
        ingest_ad(self.db, text, SOURCE)
        result2 = ingest_ad(self.db, text, SOURCE)
        self.assertIsNotNone(result2.duplicate_of)
        self.assertIsNone(result2.job_id)

    def test_no_notification_without_matching_area(self):
        category_id = self.taxonomy.create_category("Medical Representative", ["مندوب علمي"])
        self.profiles.set_job_preferences("user1", [category_id])
        self.profiles.set_area_preferences("user1", ["karkh"])

        result = ingest_ad(self.db, "مطلوب مندوب علمي - الرصافة بغداد", SOURCE)  # different side
        self.assertEqual(result.delivered_to, [])

    def test_notify_fn_invoked(self):
        category_id = self.taxonomy.create_category("Medical Representative", ["مندوب علمي"])
        self.profiles.set_job_preferences("user1", [category_id])
        self.profiles.set_area_preferences("user1", ["rusafa"])

        calls = []
        ingest_ad(self.db, "مطلوب مندوب علمي - الرصافة بغداد", SOURCE,
                  notify_fn=lambda uid, jid: calls.append((uid, jid)))
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0], "user1")


if __name__ == "__main__":
    unittest.main()

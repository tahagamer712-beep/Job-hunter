import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.database import Database  # noqa: E402
from src.pipeline_multiuser import ingest_ad  # noqa: E402
from src.taxonomy import TaxonomyStore  # noqa: E402

TEST_DB_PATH = "/tmp/test_per_user_save_ignore.db"
SOURCE = {"platform": "manual", "channel": None, "message_id": "1", "url": None, "date": "2026-08-30"}


class TestPerUserSaveIgnore(unittest.TestCase):
    def setUp(self):
        if os.path.exists(TEST_DB_PATH):
            os.remove(TEST_DB_PATH)
        self.db = Database(TEST_DB_PATH)
        self.db.upsert_user("user1")
        self.db.upsert_user("user2")
        result = ingest_ad(self.db, "مطلوب مندوب علمي - الرصافة بغداد", SOURCE)
        self.job_id = result.job_id

    def tearDown(self):
        if os.path.exists(TEST_DB_PATH):
            os.remove(TEST_DB_PATH)

    def test_saving_a_job_does_not_affect_other_users(self):
        self.db.save_job_for_user("user1", self.job_id)
        self.assertTrue(self.db.is_job_saved_by_user("user1", self.job_id))
        self.assertFalse(self.db.is_job_saved_by_user("user2", self.job_id))

    def test_ignoring_a_job_persists_and_is_personal(self):
        self.db.ignore_job_for_user("user1", self.job_id)
        self.assertTrue(self.db.is_job_ignored_by_user("user1", self.job_id))
        self.assertFalse(self.db.is_job_ignored_by_user("user2", self.job_id))

    def test_unsave_removes_it(self):
        self.db.save_job_for_user("user1", self.job_id)
        self.db.unsave_job_for_user("user1", self.job_id)
        self.assertFalse(self.db.is_job_saved_by_user("user1", self.job_id))

    def test_list_saved_jobs_for_user(self):
        self.db.save_job_for_user("user1", self.job_id)
        rows = self.db.list_saved_jobs_for_user("user1")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["id"], self.job_id)


class TestApplicationJoinColumnCollision(unittest.TestCase):
    """
    Regression test for a real bug found while fixing C01/C02: the original
    `SELECT a.*, j.*` join put the application's own `id` ahead of the
    job's `id` in column order, so row['id'] silently returned the
    APPLICATION id instead of the JOB id.
    """

    def setUp(self):
        if os.path.exists(TEST_DB_PATH):
            os.remove(TEST_DB_PATH)
        self.db = Database(TEST_DB_PATH)
        self.db.upsert_user("user1")
        taxonomy = TaxonomyStore(self.db)
        taxonomy.create_category("X", ["مندوب علمي"])

    def tearDown(self):
        if os.path.exists(TEST_DB_PATH):
            os.remove(TEST_DB_PATH)

    def test_row_id_is_always_the_job_id_not_the_application_id(self):
        r1 = ingest_ad(self.db, "مطلوب مندوب علمي - الرصافة بغداد", SOURCE)
        r2 = ingest_ad(
            self.db,
            "Completely different pharmaceutical sales opportunity available now urgently hiring today",
            SOURCE,
        )
        app_id = self.db.create_or_get_application(r2.job_id, "user1")
        self.assertNotEqual(r2.job_id, app_id, "test setup must have differing ids to catch the bug")
        self.db.update_application_status(app_id, "SENT")

        rows = self.db.list_user_applications("user1", status="SENT")
        self.assertEqual(rows[0]["id"], r2.job_id)
        self.assertEqual(rows[0]["application_id"], app_id)


if __name__ == "__main__":
    unittest.main()

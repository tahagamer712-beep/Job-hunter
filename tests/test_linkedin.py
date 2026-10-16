import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.applications import ApplicationStateError, transition  # noqa: E402
from src.database import Database  # noqa: E402
from src.linkedin import (  # noqa: E402
    LinkedInLayerAUnavailable, is_valid_linkedin_job_url, mark_linkedin_applied,
    official_api_search, submit_manual_linkedin_url,
)
from src.taxonomy import TaxonomyStore  # noqa: E402
from src.user_profiles import UserProfileStore  # noqa: E402

TEST_DB_PATH = "/tmp/test_linkedin.db"


class TestLinkedInLayers(unittest.TestCase):
    def setUp(self):
        if os.path.exists(TEST_DB_PATH):
            os.remove(TEST_DB_PATH)
        self.db = Database(TEST_DB_PATH)
        self.taxonomy = TaxonomyStore(self.db)
        self.profiles = UserProfileStore(self.db)
        self.db.upsert_user("user1")
        self.db.upsert_user("user2")

    def tearDown(self):
        if os.path.exists(TEST_DB_PATH):
            os.remove(TEST_DB_PATH)

    def test_layer_a_is_inactive_by_design(self):
        with self.assertRaises(LinkedInLayerAUnavailable):
            official_api_search()

    def test_invalid_url_rejected(self):
        self.assertFalse(is_valid_linkedin_job_url("https://example.com/not-linkedin"))
        self.assertTrue(is_valid_linkedin_job_url("https://www.linkedin.com/jobs/view/12345"))

    def test_manual_url_without_text_needs_text_and_queues_pending_title(self):
        result = submit_manual_linkedin_url(self.db, "https://www.linkedin.com/jobs/view/12345")
        self.assertTrue(result.needs_text)
        self.assertFalse(result.category_matched)
        self.assertEqual(len(self.taxonomy.list_pending_titles()), 1)

        row = self.db.get_job(result.job_id)
        self.assertEqual(row["linkedin_url"], "https://www.linkedin.com/jobs/view/12345")

    def test_manual_url_with_text_runs_full_multiuser_pipeline(self):
        category_id = self.taxonomy.create_category("Medical Representative", ["مندوب علمي"])
        self.profiles.set_job_preferences("user1", [category_id])
        self.profiles.set_area_preferences("user1", ["rusafa"])

        result = submit_manual_linkedin_url(
            self.db, "https://www.linkedin.com/jobs/view/99999",
            pipeline_context="مطلوب مندوب علمي - الرصافة بغداد",
        )
        self.assertFalse(result.needs_text)
        self.assertTrue(result.category_matched)
        self.assertEqual(result.delivered_to, ["user1"])

        row = self.db.get_job(result.job_id)
        self.assertEqual(row["linkedin_url"], "https://www.linkedin.com/jobs/view/99999")

    def test_mark_applied_uses_per_user_application_not_shared_status(self):
        result = submit_manual_linkedin_url(self.db, "https://www.linkedin.com/jobs/view/55555")
        mark_linkedin_applied(self.db, result.job_id, "user1")

        app = self.db.get_application_by_job_and_user(result.job_id, "user1")
        self.assertEqual(app["status"], "APPLIED_EXTERNALLY")

        job_row = self.db.get_job(result.job_id)
        self.assertEqual(job_row["status"], "PENDING_APPROVAL")

    def test_two_users_can_independently_mark_applied_on_same_linkedin_job(self):
        result = submit_manual_linkedin_url(self.db, "https://www.linkedin.com/jobs/view/77777")
        mark_linkedin_applied(self.db, result.job_id, "user1")

        app2_id = self.db.create_or_get_application(result.job_id, "user2")
        app2 = self.db.get_application(app2_id)
        self.assertEqual(app2["status"], "PENDING_APPROVAL", "user2's application must be unaffected by user1's")

    def test_applied_externally_is_terminal(self):
        result = submit_manual_linkedin_url(self.db, "https://www.linkedin.com/jobs/view/88888")
        mark_linkedin_applied(self.db, result.job_id, "user1")
        app = self.db.get_application_by_job_and_user(result.job_id, "user1")
        with self.assertRaises(ApplicationStateError):
            transition(self.db, app["id"], "APPLIED_EXTERNALLY", "SENT")


if __name__ == "__main__":
    unittest.main()

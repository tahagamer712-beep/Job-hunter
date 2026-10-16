import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.database import Database  # noqa: E402
from src.taxonomy import TaxonomyStore  # noqa: E402
from src.user_profiles import UserProfileStore, is_valid_email  # noqa: E402

TEST_DB_PATH = "/tmp/test_user_profiles.db"


class TestUserProfiles(unittest.TestCase):
    def setUp(self):
        if os.path.exists(TEST_DB_PATH):
            os.remove(TEST_DB_PATH)
        self.db = Database(TEST_DB_PATH)
        self.store = UserProfileStore(self.db)
        self.taxonomy = TaxonomyStore(self.db)

    def tearDown(self):
        if os.path.exists(TEST_DB_PATH):
            os.remove(TEST_DB_PATH)

    def test_get_or_create_new_user(self):
        profile = self.store.get_or_create("user1")
        self.assertEqual(profile.telegram_user_id, "user1")
        self.assertFalse(profile.is_complete)

    def test_email_validation(self):
        self.assertTrue(is_valid_email("test@example.com"))
        self.assertFalse(is_valid_email("not-an-email"))

    def test_set_invalid_email_rejected(self):
        ok = self.store.set_email("user1", "garbage")
        self.assertFalse(ok)

    def test_set_valid_email_accepted(self):
        ok = self.store.set_email("user1", "me@example.com")
        self.assertTrue(ok)
        profile = self.store.get_or_create("user1")
        self.assertEqual(profile.email, "me@example.com")

    def test_toggle_job_preference(self):
        category_id = self.taxonomy.create_category("Test Category")
        self.store.toggle_job_preference("user1", category_id)
        profile = self.store.get_or_create("user1")
        self.assertIn(category_id, profile.job_category_ids)
        self.store.toggle_job_preference("user1", category_id)
        profile2 = self.store.get_or_create("user1")
        self.assertNotIn(category_id, profile2.job_category_ids)

    def test_toggle_area_preference(self):
        self.store.toggle_area_preference("user1", "rusafa")
        profile = self.store.get_or_create("user1")
        self.assertIn("rusafa", profile.area_node_ids)

    def test_profile_complete_requires_category_and_area(self):
        category_id = self.taxonomy.create_category("Test Category")
        self.store.toggle_job_preference("user1", category_id)
        profile = self.store.get_or_create("user1")
        self.assertFalse(profile.is_complete)  # no area yet
        self.store.toggle_area_preference("user1", "rusafa")
        profile2 = self.store.get_or_create("user1")
        self.assertTrue(profile2.is_complete)

    def test_can_apply_by_email_requires_email_and_cv(self):
        profile = self.store.get_or_create("user1")
        self.assertFalse(profile.can_apply_by_email)
        self.store.set_email("user1", "me@example.com")
        self.store.set_cv("user1", "/path/to/cv.pdf")
        profile2 = self.store.get_or_create("user1")
        self.assertTrue(profile2.can_apply_by_email)


if __name__ == "__main__":
    unittest.main()

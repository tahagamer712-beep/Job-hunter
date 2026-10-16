import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.database import Database  # noqa: E402
from src.taxonomy import TaxonomyStore  # noqa: E402

TEST_DB_PATH = os.path.join(os.path.dirname(__file__), ".tmp", "test_taxonomy.db")


class TestTaxonomy(unittest.TestCase):
    def setUp(self):
        if os.path.exists(TEST_DB_PATH):
            os.remove(TEST_DB_PATH)
        self.db = Database(TEST_DB_PATH)
        self.taxonomy = TaxonomyStore(self.db)

    def tearDown(self):
        if os.path.exists(TEST_DB_PATH):
            os.remove(TEST_DB_PATH)

    def test_unmatched_title_has_no_category(self):
        result = self.taxonomy.match_category("مطلوب صيدلي تواجد لصيدلية في الكرخ")
        self.assertIsNone(result)

    def test_pending_title_queued_and_listed(self):
        pending_id = self.taxonomy.queue_pending_title("صيدلي تواجد", source_job_id=None)
        pending = self.taxonomy.list_pending_titles()
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["id"], pending_id)
        self.assertEqual(pending[0]["status"], "pending")

    def test_admin_resolves_pending_title_as_new_category_with_synonyms(self):
        """
        Locks in the exact real-world example the owner gave: تواجد /
        صيدلي تواجد / sub staff / substaff should all resolve to ONE category
        once the admin creates it and adds the synonyms as aliases.
        """
        pending_id = self.taxonomy.queue_pending_title("صيدلي تواجد")
        category_id = self.taxonomy.resolve_as_new_category(pending_id, "Pharmacist Coverage")

        # Admin adds the other known synonyms manually:
        self.taxonomy.add_alias(category_id, "تواجد")
        self.taxonomy.add_alias(category_id, "sub staff")
        self.taxonomy.add_alias(category_id, "substaff")

        for text in [
            "مطلوب صيدلي تواجد لصيدلية في المنصور",
            "نحتاج تواجد يومي بصيدلية",
            "Looking for a sub staff pharmacist",
            "Need a substaff for weekend shifts",
        ]:
            match = self.taxonomy.match_category(text)
            self.assertIsNotNone(match, f"Failed to match: {text}")
            self.assertEqual(match.canonical_name, "Pharmacist Coverage")

        # The pending title is no longer "pending":
        remaining_pending = self.taxonomy.list_pending_titles()
        self.assertEqual(len(remaining_pending), 0)

    def test_admin_resolves_pending_title_as_alias_of_existing_category(self):
        med_rep_id = self.taxonomy.create_category("Medical Representative", ["مندوب علمي", "Medical Rep"])
        pending_id = self.taxonomy.queue_pending_title("مندوب إعلام دوائي")
        self.taxonomy.resolve_as_alias(pending_id, med_rep_id)

        match = self.taxonomy.match_category("مطلوب مندوب إعلام دوائي للعمل في بغداد")
        self.assertIsNotNone(match)
        self.assertEqual(match.id, med_rep_id)

    def test_reject_pending_title_creates_no_category(self):
        pending_id = self.taxonomy.queue_pending_title("irrelevant spam text")
        self.taxonomy.reject_pending_title(pending_id)
        self.assertEqual(len(self.taxonomy.list_categories()), 0)
        self.assertEqual(len(self.taxonomy.list_pending_titles()), 0)

    def test_delete_category_removes_its_aliases(self):
        cat_id = self.taxonomy.create_category("Temp Category", ["temp alias"])
        self.taxonomy.delete_category(cat_id)
        self.assertEqual(self.taxonomy.list_categories(), [])


if __name__ == "__main__":
    unittest.main()

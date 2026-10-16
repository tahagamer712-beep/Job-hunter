import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.database import Database  # noqa: E402
from src.duplicate_detector import text_hash  # noqa: E402
from src.pipeline import JobHunterPipeline  # noqa: E402
from src.reprocessor import reprocess_archive  # noqa: E402
from src.taxonomy import TaxonomyStore  # noqa: E402
from src.user_profiles import UserProfileStore  # noqa: E402

TEST_DB_PATH = os.path.join(os.path.dirname(__file__), ".tmp", "test_reprocessor.db")
SOURCE = {"platform": "manual", "channel": None, "message_id": "1", "url": None, "date": "2026-08-30"}


class TestReprocessor(unittest.TestCase):
    def setUp(self):
        if os.path.exists(TEST_DB_PATH):
            os.remove(TEST_DB_PATH)
        self.db = Database(TEST_DB_PATH)
        self.taxonomy = TaxonomyStore(self.db)
        self.profiles = UserProfileStore(self.db)
        self.pipeline = JobHunterPipeline()

    def tearDown(self):
        if os.path.exists(TEST_DB_PATH):
            os.remove(TEST_DB_PATH)

    def test_job_with_no_matching_category_stays_undelivered(self):
        record = self.pipeline.process("Some ad with an unrecognized job title, Rusafa, Baghdad", SOURCE)
        self.db.insert_job(record, text_hash(record.raw_text))

        result = reprocess_archive(self.db)
        self.assertEqual(result.new_deliveries, 0)

    def test_adding_category_then_reprocessing_delivers_to_matching_user(self):
        record = self.pipeline.process("مطلوب مندوب علمي - الرصافة بغداد", SOURCE)
        job_id = self.db.insert_job(record, text_hash(record.raw_text))

        # No category exists yet -> user with prefs still gets nothing:
        self.profiles.set_area_preferences("user1", ["rusafa"])
        result1 = reprocess_archive(self.db)
        self.assertEqual(result1.new_deliveries, 0)

        # Admin now creates the category with the matching alias:
        category_id = self.taxonomy.create_category("Medical Representative", ["مندوب علمي"])
        self.profiles.set_job_preferences("user1", [category_id])

        result2 = reprocess_archive(self.db)
        self.assertEqual(result2.new_deliveries, 1)
        self.assertTrue(self.db.has_delivery_record(job_id, "user1"))

    def test_reprocessing_twice_never_double_delivers_once_send_is_confirmed(self):
        """
        With a notify_fn that succeeds (i.e. the send is confirmed), the
        delivery is marked SENT and must never be retried. Without a
        notify_fn at all (nothing attempted a real send), the record stays
        PENDING and SHOULD be retried on a later reprocess — that's the
        C03 fix working as intended, not a bug.
        """
        record = self.pipeline.process("مطلوب مندوب علمي - الرصافة بغداد", SOURCE)
        self.db.insert_job(record, text_hash(record.raw_text))
        category_id = self.taxonomy.create_category("Medical Representative", ["مندوب علمي"])
        self.profiles.set_job_preferences("user1", [category_id])
        self.profiles.set_area_preferences("user1", ["rusafa"])

        result1 = reprocess_archive(self.db, notify_fn=lambda uid, row: None)  # succeeds -> marked SENT
        result2 = reprocess_archive(self.db, notify_fn=lambda uid, row: None)
        self.assertEqual(result1.new_deliveries, 1)
        self.assertEqual(result2.new_deliveries, 0, "A confirmed (SENT) delivery must never be re-notified")

    def test_unconfirmed_pending_delivery_is_retried_on_next_reprocess(self):
        """C03: if nothing ever confirms the send (no notify_fn, or it
        fails), the delivery stays PENDING and is correctly retried."""
        record = self.pipeline.process("مطلوب مندوب علمي - الرصافة بغداد", SOURCE)
        self.db.insert_job(record, text_hash(record.raw_text))
        category_id = self.taxonomy.create_category("Medical Representative", ["مندوب علمي"])
        self.profiles.set_job_preferences("user1", [category_id])
        self.profiles.set_area_preferences("user1", ["rusafa"])

        result1 = reprocess_archive(self.db)  # no notify_fn -> stays PENDING
        result2 = reprocess_archive(self.db)
        self.assertEqual(result1.new_deliveries, 1)
        self.assertEqual(result2.new_deliveries, 1, "An unconfirmed PENDING delivery must be retried")

    def test_failed_notify_fn_keeps_delivery_pending_for_retry(self):
        record = self.pipeline.process("مطلوب مندوب علمي - الرصافة بغداد", SOURCE)
        self.db.insert_job(record, text_hash(record.raw_text))
        category_id = self.taxonomy.create_category("Medical Representative", ["مندوب علمي"])
        self.profiles.set_job_preferences("user1", [category_id])
        self.profiles.set_area_preferences("user1", ["rusafa"])

        def failing_notify(uid, row):
            raise RuntimeError("simulated send failure")

        reprocess_archive(self.db, notify_fn=failing_notify)
        result2 = reprocess_archive(self.db, notify_fn=lambda uid, row: None)  # this one succeeds
        self.assertEqual(result2.new_deliveries, 1, "A failed send must not block a later successful retry")

    def test_notify_fn_called_for_new_matches(self):
        record = self.pipeline.process("مطلوب مندوب علمي - الرصافة بغداد", SOURCE)
        self.db.insert_job(record, text_hash(record.raw_text))
        category_id = self.taxonomy.create_category("Medical Representative", ["مندوب علمي"])
        self.profiles.set_job_preferences("user1", [category_id])
        self.profiles.set_area_preferences("user1", ["rusafa"])

        notified = []
        reprocess_archive(self.db, notify_fn=lambda uid, row: notified.append(uid))
        self.assertEqual(notified, ["user1"])

    def test_reprocess_persists_category_and_areas_onto_job_row(self):
        """
        C09 regression test: reprocessing previously computed the category
        and detected areas transiently for matching only, never writing
        them back onto the job row — so the stored data stayed stale/wrong
        even after a successful reprocess.
        """
        import json as json_module

        record = self.pipeline.process("مطلوب مندوب علمي - الرصافة بغداد", SOURCE)
        job_id = self.db.insert_job(record, text_hash(record.raw_text))
        row_before = self.db.get_job(job_id)
        self.assertIsNone(row_before["category_id"])

        category_id = self.taxonomy.create_category("Medical Representative", ["مندوب علمي"])
        reprocess_archive(self.db)

        row_after = self.db.get_job(job_id)
        self.assertEqual(row_after["category_id"], category_id)
        self.assertIn("rusafa", json_module.loads(row_after["detected_area_ids"]))


if __name__ == "__main__":
    unittest.main()

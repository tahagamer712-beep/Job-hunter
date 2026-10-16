import base64
import os
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ.setdefault("ENCRYPTION_KEY", base64.urlsafe_b64encode(b"1" * 32).decode())

from src.applications import ApplicationStateError, send_application_email, transition  # noqa: E402
from src.database import Database  # noqa: E402
from src.duplicate_detector import text_hash  # noqa: E402
from src.email_credentials import encrypt_password  # noqa: E402
from src.pipeline_multiuser import ingest_ad  # noqa: E402
from src.taxonomy import TaxonomyStore  # noqa: E402

TEST_DB_PATH = os.path.join(os.path.dirname(__file__), ".tmp", "test_applications.db")
SOURCE = {"platform": "manual", "channel": None, "message_id": "1", "url": None, "date": "2026-08-30", "posted_at": datetime.now(timezone.utc).isoformat()}


class TestPerUserApplications(unittest.TestCase):
    def setUp(self):
        if os.path.exists(TEST_DB_PATH):
            os.remove(TEST_DB_PATH)
        self.db = Database(TEST_DB_PATH)
        taxonomy = TaxonomyStore(self.db)
        category_id = taxonomy.create_category("Medical Representative", ["مندوب علمي"])
        result = ingest_ad(self.db, "مطلوب مندوب علمي - الرصافة بغداد. test@company.com", SOURCE)
        self.job_id = result.job_id
        self.db.upsert_user("user1")
        self.db.upsert_user("user2")

    def tearDown(self):
        if os.path.exists(TEST_DB_PATH):
            os.remove(TEST_DB_PATH)

    def test_two_users_applying_to_same_job_have_independent_status(self):
        app1_id = self.db.create_or_get_application(self.job_id, "user1")
        app2_id = self.db.create_or_get_application(self.job_id, "user2")

        transition(self.db, app1_id, "DRAFT", "PREVIEW")
        transition(self.db, app1_id, "PREVIEW", "FIRST_CONFIRMED")

        # user2's application must be completely unaffected:
        app2 = self.db.get_application(app2_id)
        self.assertEqual(app2["status"], "DRAFT")

        app1 = self.db.get_application(app1_id)
        self.assertEqual(app1["status"], "FIRST_CONFIRMED")

    def test_create_or_get_application_is_idempotent(self):
        id1 = self.db.create_or_get_application(self.job_id, "user1")
        id2 = self.db.create_or_get_application(self.job_id, "user1")
        self.assertEqual(id1, id2)

    def test_direct_pending_to_sent_is_allowed_by_design(self):
        """
        Owner's explicit decision: one button tap IS the approval for email
        P0 (2026-10-16): one-tap send REMOVED. This test now verifies the
        full legal chain: DRAFT -> PREVIEW -> FIRST_CONFIRMED ->
        FINAL_CONFIRMED -> SENDING -> SENT.
        """
        app_id = self.db.create_or_get_application(self.job_id, "user1")
        transition(self.db, app_id, "DRAFT", "PREVIEW")
        transition(self.db, app_id, "PREVIEW", "FIRST_CONFIRMED")
        transition(self.db, app_id, "FIRST_CONFIRMED", "FINAL_CONFIRMED")
        transition(self.db, app_id, "FINAL_CONFIRMED", "SENDING")
        transition(self.db, app_id, "SENDING", "SENT")  # must not raise  # P0 chain
        app = self.db.get_application(app_id)
        self.assertEqual(app["status"], "SENT")

    def test_illegal_transition_still_rejected(self):
        # P0: PENDING_APPROVAL -> SENT shortcut is now ILLEGAL (was the security hole)
        app_id = self.db.create_or_get_application(self.job_id, "user1")
        with self.assertRaises(ApplicationStateError):
            transition(self.db, app_id, "PENDING_APPROVAL", "SENT")
        # Full legal chain still works
        transition(self.db, app_id, "DRAFT", "PREVIEW")
        transition(self.db, app_id, "PREVIEW", "FIRST_CONFIRMED")
        transition(self.db, app_id, "FIRST_CONFIRMED", "FINAL_CONFIRMED")
        transition(self.db, app_id, "FINAL_CONFIRMED", "SENDING")
        transition(self.db, app_id, "SENDING", "SENT")
        with self.assertRaises(ApplicationStateError):
            transition(self.db, app_id, "SENT", "DRAFT")  # SENT is terminal

    def test_send_refuses_without_smtp_credentials(self):
        app_id = self.db.create_or_get_application(self.job_id, "user1")
        transition(self.db, app_id, "DRAFT", "PREVIEW")
        transition(self.db, app_id, "PREVIEW", "FIRST_CONFIRMED")
        transition(self.db, app_id, "FIRST_CONFIRMED", "FINAL_CONFIRMED")
        self.db.upsert_user("user1", email="user1@gmail.com")  # no SMTP creds set

        with self.assertRaises(ApplicationStateError) as ctx:
            send_application_email(self.db, app_id, "user1", "company@example.com", "Subj", "Body")
        self.assertIn("No personal email credentials", str(ctx.exception))

    def test_send_refuses_from_terminal_sent_state(self):
        """Once SENT, an application can never be sent again (SENT is terminal)."""
        app_id = self.db.create_or_get_application(self.job_id, "user1")
        self.db.upsert_user("user1", email="user1@gmail.com")
        self.db.set_smtp_credentials("user1", "smtp.gmail.com", 587, encrypt_password("app password here"))

        # Force status to SENT directly (simulating an already-completed send):
        self.db.update_application_status(app_id, "SENT")

        with self.assertRaises(ApplicationStateError) as ctx:
            send_application_email(self.db, app_id, "user1", "company@example.com", "Subj", "Body")
        self.assertIn("already SENT", str(ctx.exception))

    def test_send_refuses_if_application_belongs_to_different_user(self):
        app_id = self.db.create_or_get_application(self.job_id, "user1")
        transition(self.db, app_id, "DRAFT", "PREVIEW")
        transition(self.db, app_id, "PREVIEW", "FIRST_CONFIRMED")
        self.db.upsert_user("user2", email="user2@gmail.com")
        self.db.set_smtp_credentials("user2", "smtp.gmail.com", 587, encrypt_password("pw"))

        with self.assertRaises(ApplicationStateError):
            send_application_email(self.db, app_id, "user2", "company@example.com", "Subj", "Body")

    def test_never_shares_credentials_between_users(self):
        """Structural guarantee: send_application_email always reads THIS
        user's own row from `users`, never a global/shared settings object."""
        import inspect
        from src import applications
        source = inspect.getsource(applications)
        self.assertNotIn("Settings", source)
        self.assertNotIn("settings.email", source)


if __name__ == "__main__":
    unittest.main()

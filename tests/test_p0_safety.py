# -*- coding: utf-8 -*-
"""P0 safety tests: two-step approval, pause modes, content invalidation."""
import unittest
import tempfile
import os

from src.database import Database
from src.applications import (
    ApplicationStateError, transition, content_hash, verify_approvals_intact,
    VALID_TRANSITIONS,
)


class TestTwoStepApproval(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = Database(os.path.join(self.tmp, "test.db"))
        self.db.migrate_p0_safety()
        # minimal job + user
        import sqlite3
        conn = sqlite3.connect(os.path.join(self.tmp, "test.db"))
        conn.execute("INSERT INTO jobs (id, raw_text, text_hash) VALUES (1, 'test', 'hash1')")
        conn.execute("INSERT INTO users (telegram_user_id) VALUES ('u1')")
        conn.commit()
        conn.close()
        self.app_id = self.db.create_or_get_application(1, "u1")

    def test_no_shortcut_draft_to_sent(self):
        with self.assertRaises(ApplicationStateError):
            transition(self.db, self.app_id, "DRAFT", "SENT")

    def test_no_shortcut_preview_to_sent(self):
        transition(self.db, self.app_id, "DRAFT", "PREVIEW")
        with self.assertRaises(ApplicationStateError):
            transition(self.db, self.app_id, "PREVIEW", "SENT")

    def test_no_shortcut_first_confirmed_to_sent(self):
        transition(self.db, self.app_id, "DRAFT", "PREVIEW")
        transition(self.db, self.app_id, "PREVIEW", "FIRST_CONFIRMED")
        with self.assertRaises(ApplicationStateError):
            transition(self.db, self.app_id, "FIRST_CONFIRMED", "SENT")

    def test_full_legal_chain(self):
        for a, b in [("DRAFT", "PREVIEW"), ("PREVIEW", "FIRST_CONFIRMED"),
                     ("FIRST_CONFIRMED", "FINAL_CONFIRMED"),
                     ("FINAL_CONFIRMED", "SENDING"), ("SENDING", "SENT")]:
            transition(self.db, self.app_id, a, b)
        row = self.db.get_application(self.app_id)
        self.assertEqual(row["status"], "SENT")

    def test_content_hash_invalidates_approvals(self):
        self.db.set_application_draft(self.app_id, "a@b.com", "Subj", "Body", "/cv.pdf",
                                      content_hash("a@b.com", "Subj", "Body", "/cv.pdf"))
        # Same content -> intact
        self.assertTrue(verify_approvals_intact(self.db, self.app_id, "a@b.com", "Subj", "Body", "/cv.pdf"))
        # Changed body -> not intact
        self.assertFalse(verify_approvals_intact(self.db, self.app_id, "a@b.com", "Subj", "CHANGED", "/cv.pdf"))

    def test_duplicate_send_refused(self):
        # Simulate SENT state
        self.db.update_application_status(self.app_id, "SENT")
        row = self.db.get_application(self.app_id)
        self.assertEqual(row["status"], "SENT")
        # transition SENT -> anything must fail
        with self.assertRaises(ApplicationStateError):
            transition(self.db, self.app_id, "SENT", "SENDING")


class TestPauseModes(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = Database(os.path.join(self.tmp, "test.db"))
        self.db.migrate_p0_safety()

    def test_pause_flags_default_off(self):
        self.assertFalse(self.db.is_paused("pause_scanning"))
        self.assertFalse(self.db.is_paused("pause_applications"))

    def test_pause_scanning_independent(self):
        self.db.set_setting("pause_scanning", "1")
        self.assertTrue(self.db.is_paused("pause_scanning"))
        self.assertFalse(self.db.is_paused("pause_applications"))

    def test_pause_applications_independent(self):
        self.db.set_setting("pause_applications", "1")
        self.assertTrue(self.db.is_paused("pause_applications"))
        self.assertFalse(self.db.is_paused("pause_scanning"))

    def test_both_pauses_together(self):
        self.db.set_setting("pause_scanning", "1")
        self.db.set_setting("pause_applications", "1")
        self.assertTrue(self.db.is_paused("pause_scanning"))
        self.assertTrue(self.db.is_paused("pause_applications"))
        # Unpause one
        self.db.set_setting("pause_scanning", "0")
        self.assertFalse(self.db.is_paused("pause_scanning"))
        self.assertTrue(self.db.is_paused("pause_applications"))


if __name__ == "__main__":
    unittest.main()

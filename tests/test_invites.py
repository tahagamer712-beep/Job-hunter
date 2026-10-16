import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.database import Database  # noqa: E402
from src.invites import AccessDecision, InviteManager  # noqa: E402

TEST_DB_PATH = os.path.join(os.path.dirname(__file__), ".tmp", "test_invites.db")


class TestInviteSystem(unittest.TestCase):
    def setUp(self):
        if os.path.exists(TEST_DB_PATH):
            os.remove(TEST_DB_PATH)
        self.db = Database(TEST_DB_PATH)
        self.manager = InviteManager(self.db)

    def tearDown(self):
        if os.path.exists(TEST_DB_PATH):
            os.remove(TEST_DB_PATH)

    def test_invite_only_mode_defaults_off(self):
        self.assertFalse(self.manager.is_invite_only_mode())

    def test_open_mode_allows_anyone(self):
        decision = self.manager.evaluate_access("user1", is_known_user=False)
        self.assertEqual(decision, AccessDecision.ALLOW)

    def test_known_user_always_allowed_regardless_of_mode(self):
        self.manager.set_invite_only_mode(True)
        decision = self.manager.evaluate_access("user1", is_known_user=True)
        self.assertEqual(decision, AccessDecision.ALLOW)

    def test_invite_only_mode_without_token_goes_pending(self):
        self.manager.set_invite_only_mode(True)
        decision = self.manager.evaluate_access("user1", is_known_user=False)
        self.assertEqual(decision, AccessDecision.PENDING_APPROVAL)
        pending = self.manager.list_pending()
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["telegram_user_id"], "user1")

    def test_invite_link_grants_immediate_access(self):
        self.manager.set_invite_only_mode(True)
        invite = self.manager.create_invite("JobHunterBot", created_by="admin1")
        self.assertIn("t.me/JobHunterBot?start=", invite.link)

        decision = self.manager.evaluate_access("user2", is_known_user=False, start_payload=invite.token)
        self.assertEqual(decision, AccessDecision.ALLOW)

    def test_invite_link_single_use_exhausted_after_use(self):
        self.manager.set_invite_only_mode(True)
        invite = self.manager.create_invite("JobHunterBot", created_by="admin1", max_uses=1)

        first = self.manager.evaluate_access("user2", is_known_user=False, start_payload=invite.token)
        self.assertEqual(first, AccessDecision.ALLOW)

        second = self.manager.evaluate_access("user3", is_known_user=False, start_payload=invite.token)
        self.assertEqual(second, AccessDecision.INVALID_INVITE)

    def test_revoked_invite_rejected(self):
        self.manager.set_invite_only_mode(True)
        invite = self.manager.create_invite("JobHunterBot", created_by="admin1")
        self.manager.revoke_invite(invite.token)
        decision = self.manager.evaluate_access("user2", is_known_user=False, start_payload=invite.token)
        self.assertEqual(decision, AccessDecision.INVALID_INVITE)

    def test_admin_approve_pending_registration(self):
        self.manager.set_invite_only_mode(True)
        self.manager.evaluate_access("user1", is_known_user=False)
        self.manager.approve("user1")
        pending = self.manager.list_pending()
        self.assertEqual(len(pending), 0)

    def test_approved_user_does_not_loop_back_to_pending_on_next_start(self):
        """
        Regression test for C06: approve() previously only updated
        pending_registrations, never created a `users` row, so the
        person's next /start (still with invite_only_mode on, no token)
        would see is_known_user=False again and loop straight back to
        PENDING_APPROVAL forever.
        """
        self.manager.set_invite_only_mode(True)
        self.manager.evaluate_access("user1", is_known_user=False)
        self.manager.approve("user1")

        is_known_user_now = self.db.get_user("user1") is not None
        self.assertTrue(is_known_user_now, "approve() must create a users row")

        decision = self.manager.evaluate_access("user1", is_known_user=is_known_user_now)
        self.assertEqual(decision, AccessDecision.ALLOW)

    def test_toggle_off_after_being_on(self):
        self.manager.set_invite_only_mode(True)
        self.manager.set_invite_only_mode(False)
        decision = self.manager.evaluate_access("user1", is_known_user=False)
        self.assertEqual(decision, AccessDecision.ALLOW)


if __name__ == "__main__":
    unittest.main()

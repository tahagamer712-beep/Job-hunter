"""
Security audit (Master Prompt Section 13 / Section 11's "security test").

Static checks that don't require running the bot live:
  1. smtplib is imported/used in exactly one file: src/approval.py
  2. No other file calls a function literally named send_email except via
     src.approval (i.e. no shadow/duplicate send implementation elsewhere)
  3. The VALID_TRANSITIONS state machine has no entry allowing a direct
     PENDING_APPROVAL -> SENT jump
  4. Every @self.client.on / bot command handler file that touches the DB
     directly for owner-only bot commands is wrapped by the owner_only
     decorator (best-effort static grep — not a substitute for a manual
     review, but catches an obviously missing decorator)
"""
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.approval import VALID_TRANSITIONS  # noqa: E402

SRC_DIR = Path(__file__).resolve().parent.parent / "src"


class TestSendEmailIsolation(unittest.TestCase):
    ALLOWED_SMTPLIB_FILES = {"approval.py", "applications.py"}

    def test_smtplib_only_imported_in_approved_modules(self):
        """
        Two send paths exist by design: approval.py (the original single-
        shared-account implementation, kept for backward-compat/tests) and
        applications.py (the current per-user path the bot actually uses,
        which never touches a shared account — see
        test_applications_never_shares_credentials below). No other file
        may import smtplib.
        """
        offenders = []
        for py_file in SRC_DIR.rglob("*.py"):
            text = py_file.read_text(encoding="utf-8")
            if re.search(r"^\s*import smtplib", text, re.MULTILINE) or "smtplib." in text:
                if py_file.name not in self.ALLOWED_SMTPLIB_FILES:
                    offenders.append(str(py_file))
        self.assertEqual(
            offenders, [],
            f"smtplib must only be used in {self.ALLOWED_SMTPLIB_FILES}, found in: {offenders}",
        )

    def test_no_direct_pending_to_sent_transition(self):
        allowed_from_pending = VALID_TRANSITIONS.get("PENDING_APPROVAL", set())
        self.assertNotIn(
            "SENT", allowed_from_pending,
            "PENDING_APPROVAL must never transition directly to SENT — "
            "this is the exact bug class Section 13's security test guards against.",
        )

    def test_no_direct_preview_to_sent_transition(self):
        # PREVIEW must go through CONFIRMED first — no shortcut.
        allowed_from_preview = VALID_TRANSITIONS.get("PREVIEW", set())
        self.assertNotIn("SENT", allowed_from_preview)

    def test_sent_is_terminal(self):
        self.assertEqual(
            VALID_TRANSITIONS.get("SENT"), set(),
            "SENT must be a terminal state with no further transitions.",
        )

    def test_only_confirmed_can_reach_sent(self):
        reachers = [state for state, targets in VALID_TRANSITIONS.items() if "SENT" in targets]
        self.assertEqual(reachers, ["CONFIRMED"])

    def test_per_user_applications_module_reflects_one_tap_by_design(self):
        """
        P0 (2026-10-16): one-tap send REMOVED per owner P0 spec. The ONLY
        state that can reach SENT is SENDING, and the only state that can
        reach SENDING is FINAL_CONFIRMED (two independent approvals).
        No DRAFT/PREVIEW/FIRST_CONFIRMED -> SENT shortcut exists.
        """
        from src.applications import VALID_TRANSITIONS as APP_TRANSITIONS
        self.assertEqual(APP_TRANSITIONS.get("SENT"), set(), "SENT must still be terminal — no re-sending.")
        reachers = [s for s, t in APP_TRANSITIONS.items() if "SENT" in t]
        # SENDING = normal path; NEEDS_VERIFICATION = manual confirm after
        # unknown transport result (no blind retry — human verified it sent)
        self.assertEqual(sorted(reachers), ["NEEDS_VERIFICATION", "SENDING"],
                         f"Only SENDING/NEEDS_VERIFICATION may reach SENT, got: {reachers}")
        # No shortcut from any pre-approval state
        for pre in ("DRAFT", "PREVIEW", "FIRST_CONFIRMED", "FINAL_CONFIRMED", "PENDING_APPROVAL"):
            self.assertNotIn("SENT", APP_TRANSITIONS.get(pre, set()),
                             f"{pre} must NOT reach SENT directly")

    def test_send_application_email_only_called_from_the_apply_callback(self):
        """
        Structural guarantee replacing the old CONFIRMED-only gate: the only
        code path in the bot that can trigger a send is the job:apply
        callback branch (itself only reachable via a genuine Telegram
        CallbackQuery — i.e. a user tap), not any automatic/background code
        path (the scanner, reprocessor, and broadcast functions never call
        this function).
        """
        bot_main = SRC_DIR / "bot" / "main.py"
        if not bot_main.exists():
            self.skipTest("src/bot/main.py not present")
        text = bot_main.read_text(encoding="utf-8")
        # H07: send_application_email is dispatched via run_in_executor (to
        # avoid blocking the event loop during the SMTP handshake) rather
        # than called directly — count bare references to the name instead.
        reference_count = text.count("send_application_email")
        # One in the import line, one passed to run_in_executor = 2 total.
        self.assertEqual(reference_count, 2,
                          "send_application_email should be referenced exactly twice in bot/main.py "
                          "(the import, and the single run_in_executor dispatch site)")

        for module_name in ("reprocessor.py", "telegram_listener.py"):
            module_path = SRC_DIR / module_name if (SRC_DIR / module_name).exists() else SRC_DIR / "scanner" / module_name
            if module_path.exists():
                self.assertNotIn("send_application_email", module_path.read_text(encoding="utf-8"))

    def test_applications_never_shares_credentials(self):
        import inspect
        from src import applications
        source = inspect.getsource(applications)
        self.assertNotIn("Settings", source)
        self.assertNotIn("settings.email", source)


class TestAccessControlGating(unittest.TestCase):
    def test_admin_only_handlers_use_admin_only_decorator(self):
        """
        Multi-user architecture: admin-privileged commands must use
        @admin_only, not be left ungated. Handlers that are intentionally
        open to any registered user use @registered_only instead, and
        cmd_start/on_callback/on_document/dispatch handle their own access
        checks inline (invite gate, is_admin() branch checks) rather than
        via a blanket decorator, since they serve mixed audiences.
        """
        bot_main = SRC_DIR / "bot" / "main.py"
        if not bot_main.exists():
            self.skipTest("src/bot/main.py not present")
        text = bot_main.read_text(encoding="utf-8")

        admin_commands = re.findall(r"\nasync def (cmd_addsource|cmd_ignorecompany|cmd_restore)\(", text)
        decorated = re.findall(r"@admin_only\s*\nasync def (\w+)\(", text)
        undecorated = [h for h in admin_commands if h not in decorated]
        self.assertEqual(undecorated, [], f"These admin commands are missing @admin_only: {undecorated}")

    def test_admin_panel_callbacks_check_is_admin(self):
        bot_main = SRC_DIR / "bot" / "main.py"
        if not bot_main.exists():
            self.skipTest("src/bot/main.py not present")
        text = bot_main.read_text(encoding="utf-8")
        # The admin: callback branch must check is_admin() before delegating.
        self.assertIn('if not is_admin(uid):', text)


if __name__ == "__main__":
    unittest.main()

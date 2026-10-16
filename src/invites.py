"""
Invite system + access control (owner request: toggleable invite-only mode,
with a generated deep-link that auto-approves whoever clicks it, and a
manual admin-approval queue for anyone who reaches the bot without a token
while invite-only mode is on).
"""
from __future__ import annotations

import secrets
from dataclasses import dataclass
from enum import Enum
from typing import Optional

INVITE_ONLY_SETTING_KEY = "invite_only_mode"


class AccessDecision(str, Enum):
    ALLOW = "ALLOW"                    # can use the bot immediately
    PENDING_APPROVAL = "PENDING_APPROVAL"   # must wait for admin approval
    INVALID_INVITE = "INVALID_INVITE"       # token given but expired/revoked/exhausted


def generate_invite_token() -> str:
    return secrets.token_urlsafe(12)


def build_invite_link(bot_username: str, token: str) -> str:
    """
    A Telegram deep link: opening it takes the person straight into the bot
    and passes `token` to /start via context.args, so they can register
    immediately without a separate approval step.
    """
    return f"https://t.me/{bot_username}?start={token}"


@dataclass
class InviteResult:
    token: str
    link: str


class InviteManager:
    def __init__(self, db):
        self.db = db

    def is_invite_only_mode(self) -> bool:
        return self.db.get_setting(INVITE_ONLY_SETTING_KEY, "0") == "1"

    def set_invite_only_mode(self, enabled: bool):
        self.db.set_setting(INVITE_ONLY_SETTING_KEY, "1" if enabled else "0")

    def create_invite(self, bot_username: str, created_by: str, max_uses: int = 0) -> InviteResult:
        token = generate_invite_token()
        self.db.create_invite(token, created_by, max_uses=max_uses)
        return InviteResult(token=token, link=build_invite_link(bot_username, token))

    def revoke_invite(self, token: str):
        self.db.revoke_invite(token)

    def evaluate_access(self, telegram_user_id: str, is_known_user: bool,
                         start_payload: Optional[str] = None) -> AccessDecision:
        """
        Call this on every /start. `is_known_user`: True if this person is
        already registered (always ALLOW — this gate is only for first
        contact). `start_payload`: the token from a deep link, if any.
        """
        if is_known_user:
            return AccessDecision.ALLOW

        if not self.is_invite_only_mode():
            return AccessDecision.ALLOW

        if start_payload:
            if self.db.consume_invite(start_payload):
                return AccessDecision.ALLOW
            return AccessDecision.INVALID_INVITE

        self.db.add_pending_registration(telegram_user_id)
        return AccessDecision.PENDING_APPROVAL

    def approve(self, telegram_user_id: str):
        # Must create the users row here — evaluate_access()'s is_known_user
        # check is based on db.get_user() returning non-None. Without this,
        # an approved user's *next* /start (with no invite token) would find
        # is_known_user=False again, invite_only_mode still True, and loop
        # straight back into PENDING_APPROVAL forever (C06).
        self.db.upsert_user(telegram_user_id)
        self.db.resolve_pending_registration(telegram_user_id, "approved")

    def reject(self, telegram_user_id: str):
        self.db.resolve_pending_registration(telegram_user_id, "rejected")

    def list_pending(self):
        return self.db.list_pending_registrations()

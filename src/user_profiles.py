"""
User profiles (multi-user redesign). Every Telegram user who talks to the
bot gets a profile: registered job-category preferences (from the admin-
controlled taxonomy), area preferences (from the location tree — at ANY
level: a whole side, a major area, or one specific sub-neighborhood), an
email, and an optional CV.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Optional

_EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")


@dataclass
class UserProfile:
    telegram_user_id: str
    email: Optional[str]
    cv_file_path: Optional[str]
    is_admin: bool
    job_category_ids: List[int] = field(default_factory=list)
    area_node_ids: List[str] = field(default_factory=list)

    @property
    def is_complete(self) -> bool:
        """Minimum viable profile to start receiving matches: at least one
        category, at least one area. Email/CV are needed only to apply."""
        return bool(self.job_category_ids) and bool(self.area_node_ids)

    @property
    def can_apply_by_email(self) -> bool:
        return bool(self.email and self.cv_file_path)


def is_valid_email(email: str) -> bool:
    return bool(_EMAIL_RE.match(email.strip()))


class UserProfileStore:
    def __init__(self, db):
        self.db = db

    def get_or_create(self, telegram_user_id: str) -> UserProfile:
        row = self.db.get_user(telegram_user_id)
        if row is None:
            self.db.upsert_user(telegram_user_id)
            row = self.db.get_user(telegram_user_id)
        return self._to_profile(row)

    def _to_profile(self, row) -> UserProfile:
        return UserProfile(
            telegram_user_id=row["telegram_user_id"],
            email=row["email"],
            cv_file_path=row["cv_file_path"],
            is_admin=bool(row["is_admin"]),
            job_category_ids=self.db.get_user_job_preferences(row["telegram_user_id"]),
            area_node_ids=self.db.get_user_area_preferences(row["telegram_user_id"]),
        )

    def set_email(self, telegram_user_id: str, email: str) -> bool:
        if not is_valid_email(email):
            return False
        self.db.upsert_user(telegram_user_id, email=email.strip())
        return True

    def set_cv(self, telegram_user_id: str, cv_file_path: str):
        self.db.upsert_user(telegram_user_id, cv_file_path=cv_file_path)

    def set_admin(self, telegram_user_id: str, is_admin: bool = True):
        self.db.upsert_user(telegram_user_id, is_admin=is_admin)

    def set_job_preferences(self, telegram_user_id: str, category_ids: List[int]):
        self.get_or_create(telegram_user_id)  # ensure row exists
        self.db.set_user_job_preferences(telegram_user_id, category_ids)

    def set_area_preferences(self, telegram_user_id: str, area_node_ids: List[str]):
        self.get_or_create(telegram_user_id)
        self.db.set_user_area_preferences(telegram_user_id, area_node_ids)

    def toggle_job_preference(self, telegram_user_id: str, category_id: int) -> List[int]:
        profile = self.get_or_create(telegram_user_id)
        current = set(profile.job_category_ids)
        if category_id in current:
            current.discard(category_id)
        else:
            current.add(category_id)
        self.set_job_preferences(telegram_user_id, list(current))
        return list(current)

    def toggle_area_preference(self, telegram_user_id: str, area_node_id: str) -> List[str]:
        profile = self.get_or_create(telegram_user_id)
        current = set(profile.area_node_ids)
        if area_node_id in current:
            current.discard(area_node_id)
        else:
            current.add(area_node_id)
        self.set_area_preferences(telegram_user_id, list(current))
        return list(current)

    def list_all(self) -> List[UserProfile]:
        return [self._to_profile(row) for row in self.db.list_users()]

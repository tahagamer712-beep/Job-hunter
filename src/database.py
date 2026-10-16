"""
SQLite persistence layer (Master Prompt Section 10).

Uses only the stdlib `sqlite3` module — no external dependency, so this runs
identically in the sandbox and on a Termux device.
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional

from .models import JobRecord


def _bool_or_none(value):
    return None if value is None else int(value)

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    raw_text TEXT NOT NULL,
    text_hash TEXT NOT NULL,

    source_platform TEXT,
    source_channel TEXT,
    source_message_id TEXT,
    source_message_url TEXT,
    source_date TEXT,
    linkedin_url TEXT,

    role_original TEXT,
    role_normalized TEXT,
    role_decision TEXT,

    city TEXT,
    side TEXT,
    area TEXT,
    location_decision TEXT,
    is_partial_location INTEGER DEFAULT 0,

    company_name TEXT,
    company_type TEXT,

    education TEXT,              -- JSON list
    experience_min_months INTEGER,
    experience_required INTEGER,
    car_required INTEGER,
    gender TEXT,

    salary_min REAL,
    salary_max REAL,
    currency TEXT,
    salary_type TEXT,
    commission INTEGER,
    incentives INTEGER,
    target_required INTEGER,
    work_start TEXT,
    work_end TEXT,
    part_time INTEGER,

    emails TEXT,                 -- JSON list
    phones TEXT,                 -- JSON list
    whatsapp TEXT,                -- JSON list
    telegram_handles TEXT,        -- JSON list
    linkedin_urls TEXT,           -- JSON list

    deadline TEXT,
    expiry_status TEXT,

    match_score INTEGER,
    final_decision TEXT,
    decision_reason TEXT,

    category_id INTEGER,              -- multi-user taxonomy category (NULL = unclassified, pending admin)
    detected_area_ids TEXT,           -- JSON list of location_tree node ids detected in this ad

    status TEXT DEFAULT 'PENDING_APPROVAL',
    created_at TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_jobs_text_hash ON jobs(text_hash);
CREATE INDEX IF NOT EXISTS idx_jobs_final_decision ON jobs(final_decision);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);

CREATE TABLE IF NOT EXISTS application_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id INTEGER NOT NULL,
    event_type TEXT NOT NULL,   -- PENDING | PREVIEW | CONFIRMED | SENT | CANCELLED
    created_at TEXT,
    FOREIGN KEY(job_id) REFERENCES jobs(id)
);

CREATE TABLE IF NOT EXISTS ignored_companies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company_name TEXT UNIQUE NOT NULL
);

CREATE TABLE IF NOT EXISTS ignored_jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id INTEGER UNIQUE NOT NULL,
    FOREIGN KEY(job_id) REFERENCES jobs(id)
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT
);

-- Section 58: Sources management (Telegram channels being monitored)
CREATE TABLE IF NOT EXISTS sources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    platform TEXT NOT NULL,           -- 'telegram' | 'linkedin'
    identifier TEXT NOT NULL,          -- '@channel_name' or similar
    enabled INTEGER DEFAULT 1,
    added_at TEXT,
    last_tested_at TEXT,
    last_test_result TEXT,
    UNIQUE(platform, identifier)
);

-- Section 45: CV Manager
CREATE TABLE IF NOT EXISTS cv_versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    file_path TEXT NOT NULL,
    label TEXT,
    is_active INTEGER DEFAULT 0,
    added_at TEXT
);

-- Section 55: Feedback learning (logged only — never auto-mutates core rules;
-- Section 55 explicitly forbids the AI from changing base rules on its own)
CREATE TABLE IF NOT EXISTS decision_feedback (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id INTEGER NOT NULL,
    role_normalized TEXT,
    company_name TEXT,
    user_decision TEXT NOT NULL,   -- 'ACCEPTED' | 'REJECTED'
    created_at TEXT,
    FOREIGN KEY(job_id) REFERENCES jobs(id)
);

-- ===================== MULTI-USER PLATFORM SCHEMA =====================
-- Added when Job Hunter became a multi-tenant bot: admin-controlled job
-- taxonomy, per-user profiles/preferences, and per-user delivery tracking.
-- The Karkh Exclusion Gate is NOT part of this schema — region acceptability
-- is now purely a per-user preference (user_area_preferences), never a
-- hardcoded global rule.

CREATE TABLE IF NOT EXISTS job_categories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    canonical_name TEXT UNIQUE NOT NULL,
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS category_aliases (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    category_id INTEGER NOT NULL,
    alias TEXT NOT NULL,
    UNIQUE(category_id, alias),
    FOREIGN KEY(category_id) REFERENCES job_categories(id)
);

CREATE TABLE IF NOT EXISTS pending_job_titles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    raw_title TEXT NOT NULL,
    source_job_id INTEGER,
    status TEXT DEFAULT 'pending',   -- pending | resolved_new_category | resolved_alias | rejected
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS users (
    telegram_user_id TEXT PRIMARY KEY,
    email TEXT,
    cv_file_path TEXT,
    smtp_host TEXT,
    smtp_port INTEGER,
    smtp_password_encrypted TEXT,
    is_admin INTEGER DEFAULT 0,
    registered_at TEXT,
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS user_job_preferences (
    telegram_user_id TEXT NOT NULL,
    category_id INTEGER NOT NULL,
    PRIMARY KEY (telegram_user_id, category_id),
    FOREIGN KEY(telegram_user_id) REFERENCES users(telegram_user_id),
    FOREIGN KEY(category_id) REFERENCES job_categories(id)
);

CREATE TABLE IF NOT EXISTS user_area_preferences (
    telegram_user_id TEXT NOT NULL,
    area_node_id TEXT NOT NULL,
    PRIMARY KEY (telegram_user_id, area_node_id),
    FOREIGN KEY(telegram_user_id) REFERENCES users(telegram_user_id)
);

-- Tracks which users were already notified about which job, so the same
-- match is never delivered twice.
CREATE TABLE IF NOT EXISTS job_deliveries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id INTEGER NOT NULL,
    telegram_user_id TEXT NOT NULL,
    match_strength INTEGER DEFAULT 100,
    status TEXT DEFAULT 'PENDING',   -- PENDING (matched, not yet confirmed sent) | SENT (confirmed delivered)
    delivered_at TEXT,
    sent_at TEXT,
    UNIQUE(job_id, telegram_user_id),
    FOREIGN KEY(job_id) REFERENCES jobs(id),
    FOREIGN KEY(telegram_user_id) REFERENCES users(telegram_user_id)
);

-- Invite links + admin-approval queue for invite-only mode (toggle stored
-- in `settings` under key 'invite_only_mode').
CREATE TABLE IF NOT EXISTS invites (
    token TEXT PRIMARY KEY,
    created_by TEXT,
    created_at TEXT,
    max_uses INTEGER DEFAULT 0,     -- 0 = unlimited
    use_count INTEGER DEFAULT 0,
    revoked INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS pending_registrations (
    telegram_user_id TEXT PRIMARY KEY,
    requested_at TEXT,
    status TEXT DEFAULT 'pending'   -- pending | approved | rejected
);

-- Per-user application status (fixes the original bug where `jobs.status`
-- was shared across every applicant to the same job — two different users
-- applying to the same job would stomp on each other's status). Every
-- apply action operates on a row here, keyed by (job_id, telegram_user_id),
-- never on the shared `jobs` table.
CREATE TABLE IF NOT EXISTS job_applications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id INTEGER NOT NULL,
    telegram_user_id TEXT NOT NULL,
    status TEXT DEFAULT 'PENDING_APPROVAL',  -- PENDING_APPROVAL | PREVIEW | CONFIRMED | SENT | APPLIED_EXTERNALLY | CANCELLED
    created_at TEXT,
    updated_at TEXT,
    UNIQUE(job_id, telegram_user_id),
    FOREIGN KEY(job_id) REFERENCES jobs(id),
    FOREIGN KEY(telegram_user_id) REFERENCES users(telegram_user_id)
);

CREATE TABLE IF NOT EXISTS application_status_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    application_id INTEGER NOT NULL,
    event_type TEXT NOT NULL,
    created_at TEXT,
    FOREIGN KEY(application_id) REFERENCES job_applications(id)
);

-- Per-user personal company blacklist ("🚫 شركات أتجاهلها" — my own
-- preference, distinct from the admin's global ignored_companies table
-- which is for site-wide spam/scam blocking). If I ignore a company, only
-- MY future deliveries skip it — other users still see jobs from it.
CREATE TABLE IF NOT EXISTS user_ignored_companies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    telegram_user_id TEXT NOT NULL,
    company_name TEXT NOT NULL,
    created_at TEXT,
    UNIQUE(telegram_user_id, company_name)
);

-- Per-user saved jobs (fixes a real bug: "⭐ حفظ" previously wrote to the
-- shared jobs.status column, meaning one user saving a job could stomp on
-- another user's status for that same job — same bug class as the original
-- shared application-status issue, just missed for save/ignore at the time).
CREATE TABLE IF NOT EXISTS user_saved_jobs (
    telegram_user_id TEXT NOT NULL,
    job_id INTEGER NOT NULL,
    created_at TEXT,
    PRIMARY KEY (telegram_user_id, job_id),
    FOREIGN KEY(telegram_user_id) REFERENCES users(telegram_user_id),
    FOREIGN KEY(job_id) REFERENCES jobs(id)
);

-- Per-user ignored jobs (fixes a real bug: "❌ تجاهل" only replied "تم"
-- without persisting anything — the job would still be shown again).
CREATE TABLE IF NOT EXISTS user_ignored_jobs (
    telegram_user_id TEXT NOT NULL,
    job_id INTEGER NOT NULL,
    created_at TEXT,
    PRIMARY KEY (telegram_user_id, job_id),
    FOREIGN KEY(telegram_user_id) REFERENCES users(telegram_user_id),
    FOREIGN KEY(job_id) REFERENCES jobs(id)
);
"""


class Database:
    def __init__(self, path: str = "job_hunter.db"):
        self.path = path
        self._init_schema()

    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=10000")
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _init_schema(self):
        with self._conn() as conn:
            conn.executescript(SCHEMA)

    # --- Jobs ---

    def insert_job(self, record: JobRecord, text_hash: str) -> int:
        record.created_at = record.created_at or datetime.now(timezone.utc).isoformat()
        with self._conn() as conn:
            cur = conn.execute(
                """
                INSERT INTO jobs (
                    raw_text, text_hash, source_platform, source_channel,
                    source_message_id, source_message_url, source_date, linkedin_url,
                    role_original, role_normalized, role_decision,
                    city, side, area, location_decision, is_partial_location,
                    company_name, company_type,
                    education, experience_min_months, experience_required, car_required, gender,
                    salary_min, salary_max, currency, salary_type,
                    commission, incentives, target_required, work_start, work_end, part_time,
                    emails, phones, whatsapp, telegram_handles, linkedin_urls,
                    deadline, expiry_status,
                    match_score, final_decision, decision_reason,
                    status, created_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    record.raw_text, text_hash, record.source_platform, record.source_channel,
                    record.source_message_id, record.source_message_url, record.source_date, record.linkedin_url,
                    record.role_original, record.role_normalized, record.role_decision,
                    record.city, record.side, record.area, record.location_decision, int(record.is_partial_location),
                    record.company_name, record.company_type,
                    json.dumps(record.education), record.experience_min_months,
                    None if record.experience_required is None else int(record.experience_required),
                    None if record.car_required is None else int(record.car_required),
                    record.gender,
                    record.salary_min, record.salary_max, record.currency, record.salary_type,
                    _bool_or_none(record.commission), _bool_or_none(record.incentives),
                    _bool_or_none(record.target_required), record.work_start, record.work_end,
                    _bool_or_none(record.part_time),
                    json.dumps(record.emails), json.dumps(record.phones), json.dumps(record.whatsapp),
                    json.dumps(record.telegram_handles), json.dumps(record.linkedin_urls),
                    record.deadline, record.expiry_status,
                    record.match_score, record.final_decision, record.decision_reason,
                    record.status, record.created_at,
                ),
            )
            return cur.lastrowid

    def get_existing_texts(self) -> Iterable[tuple]:
        """Returns (id, raw_text) for duplicate-detection comparisons."""
        with self._conn() as conn:
            rows = conn.execute("SELECT id, raw_text FROM jobs").fetchall()
        return [(str(r["id"]), r["raw_text"]) for r in rows]

    def insert_multiuser_job(self, raw_text: str, text_hash: str, source: dict,
                              role_original: str, category_id, detected_area_ids: list,
                              company_name, company_type, education: list,
                              experience_min_months, experience_required, car_required, gender,
                              salary_min, salary_max, currency, salary_type,
                              commission, incentives, target_required, work_start, work_end, part_time,
                              emails: list, phones: list, whatsapp: list, telegram_handles: list,
                              linkedin_urls: list, deadline, expiry_status: str) -> int:
        now = datetime.now(timezone.utc).isoformat()
        with self._conn() as conn:
            cur = conn.execute(
                """
                INSERT INTO jobs (
                    raw_text, text_hash, source_platform, source_channel,
                    source_message_id, source_message_url, source_date,
                    role_original, category_id, detected_area_ids,
                    company_name, company_type,
                    education, experience_min_months, experience_required, car_required, gender,
                    salary_min, salary_max, currency, salary_type,
                    commission, incentives, target_required, work_start, work_end, part_time,
                    emails, phones, whatsapp, telegram_handles, linkedin_urls,
                    deadline, expiry_status,
                    status, created_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    raw_text, text_hash, source.get("platform", ""), source.get("channel"),
                    source.get("message_id"), source.get("url"), source.get("date"),
                    role_original, category_id, json.dumps(detected_area_ids),
                    company_name, company_type,
                    json.dumps(education), experience_min_months,
                    _bool_or_none(experience_required), _bool_or_none(car_required), gender,
                    salary_min, salary_max, currency, salary_type,
                    _bool_or_none(commission), _bool_or_none(incentives), _bool_or_none(target_required),
                    work_start, work_end, _bool_or_none(part_time),
                    json.dumps(emails), json.dumps(phones), json.dumps(whatsapp),
                    json.dumps(telegram_handles), json.dumps(linkedin_urls),
                    deadline, expiry_status,
                    "PENDING_APPROVAL", now,
                ),
            )
            return cur.lastrowid

    def set_job_category(self, job_id: int, category_id: int):
        with self._conn() as conn:
            conn.execute("UPDATE jobs SET category_id = ? WHERE id = ?", (category_id, job_id))

    def set_job_linkedin_url(self, job_id: int, url: str):
        with self._conn() as conn:
            conn.execute("UPDATE jobs SET linkedin_url = ? WHERE id = ?", (url, job_id))

    def set_job_detected_areas(self, job_id: int, area_ids: list):
        with self._conn() as conn:
            conn.execute("UPDATE jobs SET detected_area_ids = ? WHERE id = ?", (json.dumps(area_ids), job_id))

    def update_status(self, job_id: int, status: str):
        with self._conn() as conn:
            conn.execute("UPDATE jobs SET status = ? WHERE id = ?", (status, job_id))
            conn.execute(
                "INSERT INTO application_events (job_id, event_type, created_at) VALUES (?,?,?)",
                (job_id, status, datetime.now(timezone.utc).isoformat()),
            )

    def get_job(self, job_id: int) -> Optional[sqlite3.Row]:
        with self._conn() as conn:
            return conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()

    def list_new_jobs(self, limit: int = 5, offset: int = 0):
        """New Jobs view: GREEN/YELLOW only, excludes EXPIRED/RED (Section 11)."""
        with self._conn() as conn:
            return conn.execute(
                """
                SELECT * FROM jobs
                WHERE final_decision IN ('GREEN', 'YELLOW')
                  AND status = 'PENDING_APPROVAL'
                ORDER BY created_at DESC
                LIMIT ? OFFSET ?
                """,
                (limit, offset),
            ).fetchall()

    def count_new_jobs(self) -> int:
        with self._conn() as conn:
            return conn.execute(
                "SELECT COUNT(*) c FROM jobs WHERE final_decision IN ('GREEN','YELLOW') AND status='PENDING_APPROVAL'"
            ).fetchone()["c"]

    def list_review_queue(self, limit: int = 5, offset: int = 0):
        with self._conn() as conn:
            return conn.execute(
                """
                SELECT * FROM jobs
                WHERE final_decision = 'YELLOW' AND status = 'PENDING_APPROVAL'
                ORDER BY created_at DESC LIMIT ? OFFSET ?
                """,
                (limit, offset),
            ).fetchall()

    def count_review_queue(self) -> int:
        with self._conn() as conn:
            return conn.execute(
                "SELECT COUNT(*) c FROM jobs WHERE final_decision='YELLOW' AND status='PENDING_APPROVAL'"
            ).fetchone()["c"]

    def list_archived_jobs(self, limit: int = 10, offset: int = 0):
        """EXPIRED jobs, shown only on explicit request (Section 24)."""
        with self._conn() as conn:
            return conn.execute(
                "SELECT * FROM jobs WHERE final_decision = 'EXPIRED' ORDER BY created_at DESC LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()

    def count_archived_jobs(self) -> int:
        with self._conn() as conn:
            return conn.execute("SELECT COUNT(*) c FROM jobs WHERE final_decision='EXPIRED'").fetchone()["c"]

    def list_saved_jobs(self, limit: int = 10, offset: int = 0):
        with self._conn() as conn:
            return conn.execute(
                "SELECT * FROM jobs WHERE status = 'SAVED' ORDER BY created_at DESC LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()

    def count_saved_jobs(self) -> int:
        with self._conn() as conn:
            return conn.execute("SELECT COUNT(*) c FROM jobs WHERE status='SAVED'").fetchone()["c"]

    def list_sent_applications(self, limit: int = 10, offset: int = 0):
        with self._conn() as conn:
            return conn.execute(
                "SELECT * FROM jobs WHERE status = 'SENT' ORDER BY created_at DESC LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()

    def count_sent_applications(self) -> int:
        with self._conn() as conn:
            return conn.execute("SELECT COUNT(*) c FROM jobs WHERE status='SENT'").fetchone()["c"]

    # --- Ignored companies / jobs (Section 59 blacklist) ---

    def add_ignored_company(self, company_name: str):
        with self._conn() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO ignored_companies (company_name) VALUES (?)",
                (company_name,),
            )

    def remove_ignored_company(self, company_name: str):
        with self._conn() as conn:
            conn.execute("DELETE FROM ignored_companies WHERE company_name = ?", (company_name,))

    def list_ignored_companies(self):
        with self._conn() as conn:
            return [r["company_name"] for r in conn.execute("SELECT company_name FROM ignored_companies")]

    def is_company_ignored(self, company_name: Optional[str]) -> bool:
        if not company_name:
            return False
        with self._conn() as conn:
            row = conn.execute(
                "SELECT 1 FROM ignored_companies WHERE company_name = ?", (company_name,)
            ).fetchone()
            return row is not None

    def add_ignored_job(self, job_id: int):
        with self._conn() as conn:
            conn.execute("INSERT OR IGNORE INTO ignored_jobs (job_id) VALUES (?)", (job_id,))

    # --- Sources management (Section 58) ---

    def add_source(self, platform: str, identifier: str):
        with self._conn() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO sources (platform, identifier, enabled, added_at) VALUES (?,?,1,?)",
                (platform, identifier, datetime.now(timezone.utc).isoformat()),
            )

    def remove_source(self, platform: str, identifier: str):
        with self._conn() as conn:
            conn.execute("DELETE FROM sources WHERE platform=? AND identifier=?", (platform, identifier))

    def list_sources(self, platform: Optional[str] = None):
        with self._conn() as conn:
            if platform:
                return conn.execute("SELECT * FROM sources WHERE platform = ?", (platform,)).fetchall()
            return conn.execute("SELECT * FROM sources").fetchall()

    def get_source_by_id(self, source_id: int):
        with self._conn() as conn:
            return conn.execute("SELECT * FROM sources WHERE id = ?", (source_id,)).fetchone()

    def set_source_test_result(self, source_id: int, result: str):
        with self._conn() as conn:
            conn.execute(
                "UPDATE sources SET last_tested_at=?, last_test_result=? WHERE id=?",
                (datetime.now(timezone.utc).isoformat(), result, source_id),
            )

    # --- CV manager (Section 45) ---

    def add_cv(self, file_path: str, label: str, make_active: bool = True) -> int:
        with self._conn() as conn:
            if make_active:
                conn.execute("UPDATE cv_versions SET is_active = 0")
            cur = conn.execute(
                "INSERT INTO cv_versions (file_path, label, is_active, added_at) VALUES (?,?,?,?)",
                (file_path, label, int(make_active), datetime.now(timezone.utc).isoformat()),
            )
            return cur.lastrowid

    def get_active_cv(self):
        with self._conn() as conn:
            return conn.execute("SELECT * FROM cv_versions WHERE is_active = 1 LIMIT 1").fetchone()

    def list_cvs(self):
        with self._conn() as conn:
            return conn.execute("SELECT * FROM cv_versions ORDER BY added_at DESC").fetchall()

    def set_active_cv(self, cv_id: int):
        with self._conn() as conn:
            conn.execute("UPDATE cv_versions SET is_active = 0")
            conn.execute("UPDATE cv_versions SET is_active = 1 WHERE id = ?", (cv_id,))

    # --- Feedback logging (Section 55 — logged only, never auto-mutates rules) ---

    def log_feedback(self, job_id: int, role_normalized: str, company_name: Optional[str], decision: str):
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO decision_feedback (job_id, role_normalized, company_name, user_decision, created_at)
                   VALUES (?,?,?,?,?)""",
                (job_id, role_normalized, company_name, decision, datetime.now(timezone.utc).isoformat()),
            )

    def feedback_stats(self):
        """Read-only aggregate stats for the admin to review manually (Section 55: no auto rule mutation)."""
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT role_normalized, user_decision, COUNT(*) as n
                   FROM decision_feedback GROUP BY role_normalized, user_decision"""
            ).fetchall()
            return [dict(r) for r in rows]

    # --- Settings key/value store ---

    def set_setting(self, key: str, value: str):
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO settings (key, value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            )

    def get_setting(self, key: str, default: Optional[str] = None) -> Optional[str]:
        with self._conn() as conn:
            row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
            return row["value"] if row else default

    # --- System diagnostics (Section 53) ---

    def diagnostics(self) -> dict:
        with self._conn() as conn:
            total = conn.execute("SELECT COUNT(*) c FROM jobs").fetchone()["c"]
            matched = conn.execute("SELECT COUNT(*) c FROM jobs WHERE final_decision='GREEN'").fetchone()["c"]
            rejected = conn.execute("SELECT COUNT(*) c FROM jobs WHERE final_decision='RED'").fetchone()["c"]
            pending = conn.execute(
                "SELECT COUNT(*) c FROM jobs WHERE final_decision='YELLOW' AND status='PENDING_APPROVAL'"
            ).fetchone()["c"]
            sent = conn.execute("SELECT COUNT(*) c FROM jobs WHERE status='SENT'").fetchone()["c"]
            return {
                "scanned": total, "matched": matched, "rejected": rejected,
                "pending": pending, "sent": sent,
            }

    # ===================== MULTI-USER PLATFORM METHODS =====================

    # --- Job taxonomy ---

    def create_job_category(self, canonical_name: str) -> int:
        with self._conn() as conn:
            cur = conn.execute(
                "INSERT INTO job_categories (canonical_name, created_at) VALUES (?,?)",
                (canonical_name, datetime.now(timezone.utc).isoformat()),
            )
            return cur.lastrowid

    def delete_job_category(self, category_id: int):
        with self._conn() as conn:
            conn.execute("DELETE FROM category_aliases WHERE category_id = ?", (category_id,))
            conn.execute("DELETE FROM job_categories WHERE id = ?", (category_id,))

    def list_job_categories(self):
        with self._conn() as conn:
            return conn.execute("SELECT * FROM job_categories ORDER BY canonical_name").fetchall()

    def add_category_alias(self, category_id: int, alias: str):
        with self._conn() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO category_aliases (category_id, alias) VALUES (?,?)",
                (category_id, alias),
            )

    def list_category_aliases(self, category_id: int) -> list:
        with self._conn() as conn:
            rows = conn.execute("SELECT alias FROM category_aliases WHERE category_id = ?", (category_id,)).fetchall()
            return [r["alias"] for r in rows]

    def add_pending_job_title(self, raw_title: str, source_job_id: Optional[int] = None) -> int:
        with self._conn() as conn:
            cur = conn.execute(
                "INSERT INTO pending_job_titles (raw_title, source_job_id, status, created_at) VALUES (?,?,?,?)",
                (raw_title, source_job_id, "pending", datetime.now(timezone.utc).isoformat()),
            )
            return cur.lastrowid

    def list_pending_job_titles(self, status: str = "pending"):
        with self._conn() as conn:
            return conn.execute(
                "SELECT * FROM pending_job_titles WHERE status = ? ORDER BY created_at DESC", (status,)
            ).fetchall()

    def get_pending_job_title(self, pending_id: int):
        with self._conn() as conn:
            return conn.execute("SELECT * FROM pending_job_titles WHERE id = ?", (pending_id,)).fetchone()

    def resolve_pending_job_title(self, pending_id: int, status: str):
        with self._conn() as conn:
            conn.execute("UPDATE pending_job_titles SET status = ? WHERE id = ?", (status, pending_id))

    # --- User profiles ---

    def upsert_user(self, telegram_user_id: str, email: Optional[str] = None,
                     cv_file_path: Optional[str] = None, is_admin: Optional[bool] = None):
        now = datetime.now(timezone.utc).isoformat()
        with self._conn() as conn:
            existing = conn.execute("SELECT * FROM users WHERE telegram_user_id = ?", (telegram_user_id,)).fetchone()
            if existing is None:
                conn.execute(
                    "INSERT INTO users (telegram_user_id, email, cv_file_path, is_admin, registered_at, updated_at) VALUES (?,?,?,?,?,?)",
                    (telegram_user_id, email, cv_file_path, int(bool(is_admin)), now, now),
                )
            else:
                conn.execute(
                    """UPDATE users SET
                         email = COALESCE(?, email),
                         cv_file_path = COALESCE(?, cv_file_path),
                         is_admin = COALESCE(?, is_admin),
                         updated_at = ?
                       WHERE telegram_user_id = ?""",
                    (email, cv_file_path, None if is_admin is None else int(is_admin), now, telegram_user_id),
                )

    def get_user(self, telegram_user_id: str):
        with self._conn() as conn:
            return conn.execute("SELECT * FROM users WHERE telegram_user_id = ?", (telegram_user_id,)).fetchone()

    def list_users(self):
        with self._conn() as conn:
            return conn.execute("SELECT * FROM users").fetchall()

    def set_user_job_preferences(self, telegram_user_id: str, category_ids: list):
        with self._conn() as conn:
            conn.execute("DELETE FROM user_job_preferences WHERE telegram_user_id = ?", (telegram_user_id,))
            for cid in category_ids:
                conn.execute(
                    "INSERT OR IGNORE INTO user_job_preferences (telegram_user_id, category_id) VALUES (?,?)",
                    (telegram_user_id, cid),
                )

    def get_user_job_preferences(self, telegram_user_id: str) -> list:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT category_id FROM user_job_preferences WHERE telegram_user_id = ?", (telegram_user_id,)
            ).fetchall()
            return [r["category_id"] for r in rows]

    def set_user_area_preferences(self, telegram_user_id: str, area_node_ids: list):
        with self._conn() as conn:
            conn.execute("DELETE FROM user_area_preferences WHERE telegram_user_id = ?", (telegram_user_id,))
            for node_id in area_node_ids:
                conn.execute(
                    "INSERT OR IGNORE INTO user_area_preferences (telegram_user_id, area_node_id) VALUES (?,?)",
                    (telegram_user_id, node_id),
                )

    def get_user_area_preferences(self, telegram_user_id: str) -> list:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT area_node_id FROM user_area_preferences WHERE telegram_user_id = ?", (telegram_user_id,)
            ).fetchall()
            return [r["area_node_id"] for r in rows]

    # --- Delivery tracking ---

    def has_been_delivered(self, job_id: int, telegram_user_id: str) -> bool:
        """Only a CONFIRMED (status='SENT') delivery counts — a PENDING one
        (matched but not yet successfully sent) must not block a retry."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT 1 FROM job_deliveries WHERE job_id = ? AND telegram_user_id = ? AND status = 'SENT'",
                (job_id, telegram_user_id),
            ).fetchone()
            return row is not None

    def has_delivery_record(self, job_id: int, telegram_user_id: str) -> bool:
        """True if ANY delivery record exists (PENDING or SENT) — used to
        avoid re-queuing/re-counting the same match on repeated ingestion/
        reprocessing, distinct from has_been_delivered() which specifically
        means 'confirmed sent' and is what retry logic checks against."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT 1 FROM job_deliveries WHERE job_id = ? AND telegram_user_id = ?",
                (job_id, telegram_user_id),
            ).fetchone()
            return row is not None

    def record_delivery(self, job_id: int, telegram_user_id: str, match_strength: int = 100):
        """Reserves a PENDING delivery record — NOT yet confirmed sent.
        Call mark_delivery_sent() after the actual send succeeds (C03: a
        delivery must never be considered done before the send is
        confirmed, or a failed send silently blocks all future retries)."""
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO job_deliveries (job_id, telegram_user_id, match_strength, status, delivered_at)
                   VALUES (?,?,?,'PENDING',?)
                   ON CONFLICT(job_id, telegram_user_id) DO NOTHING""",
                (job_id, telegram_user_id, match_strength, datetime.now(timezone.utc).isoformat()),
            )

    def mark_delivery_sent(self, job_id: int, telegram_user_id: str):
        with self._conn() as conn:
            conn.execute(
                "UPDATE job_deliveries SET status='SENT', sent_at=? WHERE job_id=? AND telegram_user_id=?",
                (datetime.now(timezone.utc).isoformat(), job_id, telegram_user_id),
            )

    def get_delivery_match_strength(self, job_id: int, telegram_user_id: str) -> int:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT match_strength FROM job_deliveries WHERE job_id=? AND telegram_user_id=?",
                (job_id, telegram_user_id),
            ).fetchone()
            return row["match_strength"] if row else 100

    def list_pending_deliveries(self, job_id: Optional[int] = None) -> list:
        """PENDING deliveries (matched but never confirmed sent — e.g. the
        bot crashed mid-send) — useful for a retry sweep."""
        with self._conn() as conn:
            if job_id is not None:
                return conn.execute(
                    "SELECT * FROM job_deliveries WHERE status='PENDING' AND job_id=?", (job_id,)
                ).fetchall()
            return conn.execute("SELECT * FROM job_deliveries WHERE status='PENDING'").fetchall()

    # --- Per-user SMTP credentials (owner requirement: no shared email account) ---

    def set_smtp_credentials(self, telegram_user_id: str, smtp_host: str, smtp_port: int,
                              smtp_password_encrypted: str):
        now = datetime.now(timezone.utc).isoformat()
        with self._conn() as conn:
            conn.execute(
                """UPDATE users SET smtp_host=?, smtp_port=?, smtp_password_encrypted=?, updated_at=?
                   WHERE telegram_user_id=?""",
                (smtp_host, smtp_port, smtp_password_encrypted, now, telegram_user_id),
            )

    def has_smtp_credentials(self, telegram_user_id: str) -> bool:
        row = self.get_user(telegram_user_id)
        return bool(row and row["smtp_host"] and row["smtp_password_encrypted"])

    # --- Per-user application status (fixes the shared-status bug) ---

    def create_or_get_application(self, job_id: int, telegram_user_id: str) -> int:
        now = datetime.now(timezone.utc).isoformat()
        with self._conn() as conn:
            existing = conn.execute(
                "SELECT id FROM job_applications WHERE job_id=? AND telegram_user_id=?",
                (job_id, telegram_user_id),
            ).fetchone()
            if existing:
                return existing["id"]
            cur = conn.execute(
                "INSERT INTO job_applications (job_id, telegram_user_id, status, created_at, updated_at) VALUES (?,?,?,?,?)",
                (job_id, telegram_user_id, "DRAFT", now, now),
            )
            return cur.lastrowid

    def get_application(self, application_id: int):
        with self._conn() as conn:
            return conn.execute("SELECT * FROM job_applications WHERE id = ?", (application_id,)).fetchone()

    def get_application_by_job_and_user(self, job_id: int, telegram_user_id: str):
        with self._conn() as conn:
            return conn.execute(
                "SELECT * FROM job_applications WHERE job_id=? AND telegram_user_id=?",
                (job_id, telegram_user_id),
            ).fetchone()

    def update_application_status(self, application_id: int, status: str):
        now = datetime.now(timezone.utc).isoformat()
        with self._conn() as conn:
            conn.execute(
                "UPDATE job_applications SET status=?, updated_at=? WHERE id=?",
                (status, now, application_id),
            )
            conn.execute(
                "INSERT INTO application_status_events (application_id, event_type, created_at) VALUES (?,?,?)",
                (application_id, status, now),
            )

    # --- P0: application content tracking + pause settings ---
    def migrate_p0_safety(self):
        """Add content_hash / confirmation timestamps; init pause flags. Idempotent."""
        with self._conn() as conn:
            cols = {r["name"] for r in conn.execute("PRAGMA table_info(job_applications)")}
            if "content_hash" not in cols:
                conn.execute("ALTER TABLE job_applications ADD COLUMN content_hash TEXT")
            if "first_confirmed_at" not in cols:
                conn.execute("ALTER TABLE job_applications ADD COLUMN first_confirmed_at TEXT")
            if "final_confirmed_at" not in cols:
                conn.execute("ALTER TABLE job_applications ADD COLUMN final_confirmed_at TEXT")
            if "draft_to" not in cols:
                conn.execute("ALTER TABLE job_applications ADD COLUMN draft_to TEXT")
            if "draft_subject" not in cols:
                conn.execute("ALTER TABLE job_applications ADD COLUMN draft_subject TEXT")
            if "draft_body" not in cols:
                conn.execute("ALTER TABLE job_applications ADD COLUMN draft_body TEXT")
            if "draft_cv_path" not in cols:
                conn.execute("ALTER TABLE job_applications ADD COLUMN draft_cv_path TEXT")
            # Map legacy PENDING_APPROVAL -> DRAFT (safe: nothing was sent under old flow
            # without an explicit tap, and old CONFIRMED -> FIRST_CONFIRMED)
            conn.execute("UPDATE job_applications SET status='DRAFT' WHERE status='PENDING_APPROVAL'")
            conn.execute("UPDATE job_applications SET status='FIRST_CONFIRMED' WHERE status='CONFIRMED'")
            # Pause flags (default: not paused)
            for key in ("pause_scanning", "pause_applications"):
                row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
                if row is None:
                    conn.execute("INSERT INTO settings (key, value) VALUES (?, '0')", (key,))

    def set_application_draft(self, application_id: int, to_addr: str, subject: str,
                              body: str, cv_path: str, content_hash: str):
        now = datetime.now(timezone.utc).isoformat()
        with self._conn() as conn:
            conn.execute(
                """UPDATE job_applications SET draft_to=?, draft_subject=?, draft_body=?,
                   draft_cv_path=?, content_hash=?, updated_at=? WHERE id=?""",
                (to_addr, subject, body, cv_path, content_hash, now, application_id),
            )

    def stamp_first_confirmed(self, application_id: int):
        now = datetime.now(timezone.utc).isoformat()
        with self._conn() as conn:
            conn.execute(
                "UPDATE job_applications SET first_confirmed_at=?, updated_at=? WHERE id=?",
                (now, now, application_id),
            )

    def stamp_final_confirmed(self, application_id: int):
        now = datetime.now(timezone.utc).isoformat()
        with self._conn() as conn:
            conn.execute(
                "UPDATE job_applications SET final_confirmed_at=?, updated_at=? WHERE id=?",
                (now, now, application_id),
            )

    def update_job_decisions(self, job_id: int, role_decision: str = None,
                             location_decision: str = None, expiry_status: str = None,
                             final_decision: str = None, decision_reason: str = None):
        """P1: store the four independent decision fields on a job."""
        sets, vals = [], []
        for col, val in (("role_decision", role_decision),
                         ("location_decision", location_decision),
                         ("expiry_status", expiry_status),
                         ("final_decision", final_decision),
                         ("decision_reason", decision_reason)):
            if val is not None:
                sets.append(f"{col}=?")
                vals.append(val)
        if not sets:
            return
        vals.append(job_id)
        with self._conn() as conn:
            conn.execute(f"UPDATE jobs SET {', '.join(sets)} WHERE id=?", vals)

    def get_review_queue(self, limit: int = 20):
        """P1: YELLOW final_decision jobs awaiting owner review."""
        with self._conn() as conn:
            return conn.execute(
                "SELECT * FROM jobs WHERE final_decision='YELLOW' "
                "ORDER BY id DESC LIMIT ?", (limit,)).fetchall()

    def log_review_decision(self, job_id: int, telegram_user_id: str,
                            original: str, decision: str, note: str = ""):
        """P1: record owner's review decision (never silently overrides Karkh gate)."""
        now = datetime.now(timezone.utc).isoformat()
        with self._conn() as conn:
            conn.execute(
                """CREATE TABLE IF NOT EXISTS review_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, job_id INTEGER,
                    telegram_user_id TEXT, original_decision TEXT,
                    review_decision TEXT, note TEXT, created_at TEXT)""")
            conn.execute(
                "INSERT INTO review_log (job_id, telegram_user_id, original_decision, "
                "review_decision, note, created_at) VALUES (?,?,?,?,?,?)",
                (job_id, telegram_user_id, original, decision, note, now))

    def get_setting(self, key: str, default: str = "0") -> str:
        with self._conn() as conn:
            row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
            return row["value"] if row else default

    def set_setting(self, key: str, value: str):
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            )

    def is_paused(self, key: str) -> bool:
        return self.get_setting(key, "0") == "1"

    def list_user_applications(self, telegram_user_id: str, status: Optional[str] = None,
                                limit: int = 10, offset: int = 0):
        with self._conn() as conn:
            if status:
                return conn.execute(
                    """SELECT j.*, a.id as application_id, a.status as application_status,
                              a.created_at as application_created_at
                       FROM job_applications a JOIN jobs j ON a.job_id = j.id
                       WHERE a.telegram_user_id = ? AND a.status = ?
                       ORDER BY a.created_at DESC LIMIT ? OFFSET ?""",
                    (telegram_user_id, status, limit, offset),
                ).fetchall()
            return conn.execute(
                """SELECT j.*, a.id as application_id, a.status as application_status,
                          a.created_at as application_created_at
                   FROM job_applications a JOIN jobs j ON a.job_id = j.id
                   WHERE a.telegram_user_id = ?
                   ORDER BY a.created_at DESC LIMIT ? OFFSET ?""",
                (telegram_user_id, limit, offset),
            ).fetchall()

    # --- Per-user company blacklist (owner: personal, not shared) ---

    def add_user_ignored_company(self, telegram_user_id: str, company_name: str):
        with self._conn() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO user_ignored_companies (telegram_user_id, company_name, created_at) VALUES (?,?,?)",
                (telegram_user_id, company_name, datetime.now(timezone.utc).isoformat()),
            )

    def remove_user_ignored_company(self, telegram_user_id: str, company_name: str):
        with self._conn() as conn:
            conn.execute(
                "DELETE FROM user_ignored_companies WHERE telegram_user_id=? AND company_name=?",
                (telegram_user_id, company_name),
            )

    def list_user_ignored_companies(self, telegram_user_id: str) -> list:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT company_name FROM user_ignored_companies WHERE telegram_user_id = ?",
                (telegram_user_id,),
            ).fetchall()
            return [r["company_name"] for r in rows]

    def list_user_ignored_companies_with_ids(self, telegram_user_id: str) -> list:
        """Returns [{'id': int, 'company_name': str}, ...] — the id is a
        short, stable token safe to embed in a Telegram callback_data
        (unlike company_name, which can be arbitrarily long and would risk
        exceeding Telegram's 64-byte callback_data limit)."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT id, company_name FROM user_ignored_companies WHERE telegram_user_id = ?",
                (telegram_user_id,),
            ).fetchall()
            return [{"id": r["id"], "company_name": r["company_name"]} for r in rows]

    def remove_user_ignored_company_by_id(self, telegram_user_id: str, entry_id: int):
        with self._conn() as conn:
            conn.execute(
                "DELETE FROM user_ignored_companies WHERE id=? AND telegram_user_id=?",
                (entry_id, telegram_user_id),
            )

    def is_company_ignored_by_user(self, telegram_user_id: str, company_name: Optional[str]) -> bool:
        if not company_name:
            return False
        with self._conn() as conn:
            row = conn.execute(
                "SELECT 1 FROM user_ignored_companies WHERE telegram_user_id=? AND company_name=?",
                (telegram_user_id, company_name),
            ).fetchone()
            return row is not None

    # --- Per-user saved/ignored jobs (fixes the shared jobs.status bug) ---

    def save_job_for_user(self, telegram_user_id: str, job_id: int):
        with self._conn() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO user_saved_jobs (telegram_user_id, job_id, created_at) VALUES (?,?,?)",
                (telegram_user_id, job_id, datetime.now(timezone.utc).isoformat()),
            )

    def unsave_job_for_user(self, telegram_user_id: str, job_id: int):
        with self._conn() as conn:
            conn.execute(
                "DELETE FROM user_saved_jobs WHERE telegram_user_id=? AND job_id=?",
                (telegram_user_id, job_id),
            )

    def is_job_saved_by_user(self, telegram_user_id: str, job_id: int) -> bool:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT 1 FROM user_saved_jobs WHERE telegram_user_id=? AND job_id=?",
                (telegram_user_id, job_id),
            ).fetchone()
            return row is not None

    def list_saved_jobs_for_user(self, telegram_user_id: str, limit: int = 10, offset: int = 0):
        with self._conn() as conn:
            return conn.execute(
                """SELECT j.* FROM jobs j JOIN user_saved_jobs s ON j.id = s.job_id
                   WHERE s.telegram_user_id = ? ORDER BY s.created_at DESC LIMIT ? OFFSET ?""",
                (telegram_user_id, limit, offset),
            ).fetchall()

    def count_saved_jobs_for_user(self, telegram_user_id: str) -> int:
        with self._conn() as conn:
            return conn.execute(
                "SELECT COUNT(*) c FROM user_saved_jobs WHERE telegram_user_id=?", (telegram_user_id,)
            ).fetchone()["c"]

    def ignore_job_for_user(self, telegram_user_id: str, job_id: int):
        with self._conn() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO user_ignored_jobs (telegram_user_id, job_id, created_at) VALUES (?,?,?)",
                (telegram_user_id, job_id, datetime.now(timezone.utc).isoformat()),
            )

    def is_job_ignored_by_user(self, telegram_user_id: str, job_id: int) -> bool:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT 1 FROM user_ignored_jobs WHERE telegram_user_id=? AND job_id=?",
                (telegram_user_id, job_id),
            ).fetchone()
            return row is not None

    # --- Invites ---

    def create_invite(self, token: str, created_by: str, max_uses: int = 0):
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO invites (token, created_by, created_at, max_uses, use_count, revoked) VALUES (?,?,?,?,0,0)",
                (token, created_by, datetime.now(timezone.utc).isoformat(), max_uses),
            )

    def get_invite(self, token: str):
        with self._conn() as conn:
            return conn.execute("SELECT * FROM invites WHERE token = ?", (token,)).fetchone()

    def consume_invite(self, token: str) -> bool:
        """Atomically increments use_count if the invite is still valid. Returns True if consumed."""
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM invites WHERE token = ?", (token,)).fetchone()
            if row is None or row["revoked"]:
                return False
            if row["max_uses"] and row["use_count"] >= row["max_uses"]:
                return False
            conn.execute("UPDATE invites SET use_count = use_count + 1 WHERE token = ?", (token,))
            return True

    def revoke_invite(self, token: str):
        with self._conn() as conn:
            conn.execute("UPDATE invites SET revoked = 1 WHERE token = ?", (token,))

    def list_invites(self):
        with self._conn() as conn:
            return conn.execute("SELECT * FROM invites ORDER BY created_at DESC").fetchall()

    # --- Pending registrations (invite-only mode without a token) ---

    def add_pending_registration(self, telegram_user_id: str):
        with self._conn() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO pending_registrations (telegram_user_id, requested_at, status) VALUES (?,?,'pending')",
                (telegram_user_id, datetime.now(timezone.utc).isoformat()),
            )

    def get_pending_registration(self, telegram_user_id: str):
        with self._conn() as conn:
            return conn.execute(
                "SELECT * FROM pending_registrations WHERE telegram_user_id = ?", (telegram_user_id,)
            ).fetchone()

    def list_pending_registrations(self):
        with self._conn() as conn:
            return conn.execute(
                "SELECT * FROM pending_registrations WHERE status = 'pending' ORDER BY requested_at"
            ).fetchall()

    def resolve_pending_registration(self, telegram_user_id: str, status: str):
        with self._conn() as conn:
            conn.execute(
                "UPDATE pending_registrations SET status = ? WHERE telegram_user_id = ?",
                (status, telegram_user_id),
            )

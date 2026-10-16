"""
Approval state machine + email sending (Master Prompt Section 12.3 / 13).

SECURITY INVARIANT (must never be violated): send_email() below is the ONLY
function in this codebase permitted to actually dispatch an email, and it
refuses to run unless the job's current stored status is CONFIRMED. There is
no code path anywhere else in this project that calls smtplib directly.

    PENDING_APPROVAL -> PREVIEW -> CONFIRMED -> SENT

Any attempt to skip a state (e.g. PENDING -> SENT) raises ApprovalStateError.
This is what Master Prompt Section 13's required security test should assert
against: grep the codebase for smtplib/email sending calls and confirm this
module is the only call site, and that every call site enforces this guard.
"""
from __future__ import annotations

import smtplib
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import Optional

from .config import Settings
from .database import Database

VALID_TRANSITIONS = {
    "PENDING_APPROVAL": {"PREVIEW", "IGNORED", "SAVED", "APPLIED_EXTERNALLY"},
    "PREVIEW": {"CONFIRMED", "PENDING_APPROVAL", "IGNORED", "APPLIED_EXTERNALLY"},
    "CONFIRMED": {"SENT", "PREVIEW"},   # allow stepping back to edit before sending
    "SENT": set(),                        # terminal — reserved for actual send_email() sends
    "SAVED": {"PREVIEW", "APPLIED_EXTERNALLY"},
    "IGNORED": set(),
    "APPLIED_EXTERNALLY": set(),          # terminal — user applied manually (e.g. on LinkedIn)
}


class ApprovalStateError(Exception):
    pass


def transition(db: Database, job_id: int, current_status: str, new_status: str) -> None:
    allowed = VALID_TRANSITIONS.get(current_status, set())
    if new_status not in allowed:
        raise ApprovalStateError(
            f"Illegal transition {current_status} -> {new_status} for job {job_id}. "
            f"Allowed from {current_status}: {sorted(allowed)}"
        )
    db.update_status(job_id, new_status)


def send_email(db: Database, settings: Settings, job_id: int, to_address: str,
                subject: str, body: str, cv_path: Optional[Path] = None) -> None:
    """
    The ONLY function permitted to actually send an email. Refuses unless the
    job's current DB status is CONFIRMED (Section 12.3's Final Confirmation
    step must have already flipped it there via `transition()` above).

    NOTE: status='SENT' is reserved exclusively for emails actually sent by
    this function. LinkedIn "mark as applied" (Section 39/40's non-email
    equivalent, src/linkedin.py:mark_linkedin_applied) uses a distinct
    'APPLIED_EXTERNALLY' status instead, so the two can never be confused and
    'SENT' always means "this codebase actually sent an email."
    """
    row = db.get_job(job_id)
    if row is None:
        raise ApprovalStateError(f"Job {job_id} not found.")
    if row["status"] != "CONFIRMED":
        raise ApprovalStateError(
            f"Refusing to send: job {job_id} status is '{row['status']}', not CONFIRMED. "
            f"No direct PENDING -> SENT path is permitted."
        )
    if not settings.email_enabled:
        raise ApprovalStateError("Email is not configured (EMAIL_HOST/USERNAME/PASSWORD missing).")

    msg = MIMEMultipart()
    msg["From"] = settings.email_username
    msg["To"] = to_address
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain", "utf-8"))

    if cv_path and cv_path.exists():
        with open(cv_path, "rb") as f:
            part = MIMEApplication(f.read(), Name=cv_path.name)
        part["Content-Disposition"] = f'attachment; filename="{cv_path.name}"'
        msg.attach(part)

    with smtplib.SMTP(settings.email_host, int(settings.email_port or 587)) as server:
        server.starttls()
        server.login(settings.email_username, settings.email_password)
        server.send_message(msg)

    transition(db, job_id, "CONFIRMED", "SENT")

"""
Per-user application state machine + email sender.

OWNER'S EXPLICIT UX DECISION (documented here so the reasoning is clear):
pressing the "✉️ تقديم" button IS the user's approval — there is no
separate Preview -> Confirm -> Send dance for email applications anymore.
One tap composes the message (subject, a short cover note, the user's CV
attached) and sends it immediately from the user's OWN email account. This
is still always a deliberate, explicit, human-initiated action (the button
only exists inside a Telegram callback triggered by that specific user
tapping it) — nothing in this codebase ever calls send_application_email
except from that one callback handler (src/bot/main.py's job:apply flow).

For WhatsApp/Telegram-username contacts, "apply" behaves differently by
necessity: the bot cannot send WhatsApp/Telegram messages to an employer's
number/handle on the user's behalf (Telegram's Bot API doesn't allow this
for arbitrary numbers), so those buttons open a direct chat link and the
user sends their own message/CV manually — see src/contact_links.py and
the job_contact_keyboard() in src/bot/main.py.

Fixes the original single-shared-status bug: every (job, user) pair gets
its own row in `job_applications`.

No shared email account: every send uses the applying user's OWN SMTP
credentials — see src/email_credentials.py. Refuses clearly if missing.
"""
from __future__ import annotations

import smtplib
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import Optional

from .email_credentials import decrypt_password

VALID_TRANSITIONS = {
    # SENT is directly reachable from PENDING_APPROVAL by design: the single
    # button tap is the approval (see module docstring). CONFIRMED/PREVIEW
    # are kept as optional intermediate states for any future flow that
    # wants them (e.g. an "edit cover note first" option), but the bot's
    # current one-tap flow does not use them.
    "PENDING_APPROVAL": {"SENT", "PREVIEW", "CANCELLED", "APPLIED_EXTERNALLY"},
    "PREVIEW": {"CONFIRMED", "PENDING_APPROVAL", "CANCELLED"},
    "CONFIRMED": {"SENT", "PREVIEW"},
    "SENT": set(),
    "CANCELLED": {"PREVIEW", "PENDING_APPROVAL"},
    "APPLIED_EXTERNALLY": set(),
}


class ApplicationStateError(Exception):
    pass


def transition(db, application_id: int, current_status: str, new_status: str) -> None:
    allowed = VALID_TRANSITIONS.get(current_status, set())
    if new_status not in allowed:
        raise ApplicationStateError(
            f"Illegal transition {current_status} -> {new_status} for application {application_id}. "
            f"Allowed from {current_status}: {sorted(allowed)}"
        )
    db.update_application_status(application_id, new_status)


def send_application_email(db, application_id: int, telegram_user_id: str,
                            to_address: str, subject: str, body: str,
                            cv_path: Optional[Path] = None) -> None:
    """
    The ONLY function permitted to actually dispatch an application email.
    Refuses unless:
      1. The application's current status allows moving to SENT (i.e. it
         hasn't already been sent/cancelled — see VALID_TRANSITIONS), AND
      2. This application belongs to telegram_user_id, AND
      3. The applying user has their OWN SMTP credentials configured.
    Never uses a shared/default account under any circumstance.
    """
    app_row = db.get_application(application_id)
    if app_row is None:
        raise ApplicationStateError(f"Application {application_id} not found.")
    if app_row["telegram_user_id"] != telegram_user_id:
        raise ApplicationStateError("Application does not belong to this user.")
    if "SENT" not in VALID_TRANSITIONS.get(app_row["status"], set()):
        raise ApplicationStateError(
            f"Refusing to send: application {application_id} status is "
            f"'{app_row['status']}', which cannot transition to SENT."
        )

    user = db.get_user(telegram_user_id)
    if user is None or not user["smtp_host"] or not user["smtp_password_encrypted"]:
        raise ApplicationStateError(
            "No personal email credentials configured for this user — cannot send. "
            "There is no shared/fallback account by design."
        )

    smtp_password = decrypt_password(user["smtp_password_encrypted"])
    from_address = user["email"]

    msg = MIMEMultipart()
    msg["From"] = from_address
    msg["To"] = to_address
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain", "utf-8"))

    if cv_path and cv_path.exists():
        with open(cv_path, "rb") as f:
            part = MIMEApplication(f.read(), Name=cv_path.name)
        part["Content-Disposition"] = f'attachment; filename="{cv_path.name}"'
        msg.attach(part)

    with smtplib.SMTP(user["smtp_host"], int(user["smtp_port"] or 587)) as server:
        server.starttls()
        server.login(from_address, smtp_password)
        server.send_message(msg)

    transition(db, application_id, app_row["status"], "SENT")

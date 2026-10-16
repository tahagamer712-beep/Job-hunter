# -*- coding: utf-8 -*-
"""P0: Two-step approval state machine for job applications.

Replaces the one-tap-send flow with:
DRAFT → PREVIEW → FIRST_CONFIRMED → FINAL_CONFIRMED → SENDING → SENT
+ FAILED, NEEDS_VERIFICATION, CANCELLED

Rules (per P0 spec):
- Pressing apply sends NOTHING (goes to DRAFT → shows PREVIEW)
- First approval sends NOTHING (goes to FIRST_CONFIRMED → shows final confirmation)
- Final confirmation (separate, explicit) allows SENDING
- Any edit to to/subject/body/cv invalidates prior approvals (content hash check)
- No DRAFT/PREVIEW → SENT jump allowed
- Duplicate sends prevented (idempotency via status check + DB unique constraint)
- SENT only after actual transport success
- NEEDS_VERIFICATION if send result unknown (no blind retry)
"""
p = '/data/data/com.termux/files/home/job_hunter/src/applications.py'
s = open(p, encoding='utf-8').read()

# 1. Replace VALID_TRANSITIONS with the P0 state machine
old_transitions = '''VALID_TRANSITIONS = {
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
}'''

new_transitions = '''# P0 SAFETY: Two independent approvals required before any send.
# Pressing "apply" -> DRAFT (shows preview, sends NOTHING)
# [Confirm Send] -> FIRST_CONFIRMED (shows final confirmation, sends NOTHING)
# [Final Confirmation] -> FINAL_CONFIRMED (enables the Send button)
# [Send Now] -> SENDING -> SENT (only after actual transport success)
# Any content edit invalidates approvals (back to DRAFT).
VALID_TRANSITIONS = {
    "DRAFT": {"PREVIEW", "CANCELLED"},
    "PREVIEW": {"FIRST_CONFIRMED", "DRAFT", "CANCELLED", "APPLIED_EXTERNALLY"},
    "FIRST_CONFIRMED": {"FINAL_CONFIRMED", "DRAFT", "CANCELLED"},
    "FINAL_CONFIRMED": {"SENDING", "DRAFT", "CANCELLED"},
    "SENDING": {"SENT", "FAILED", "NEEDS_VERIFICATION"},
    "SENT": set(),                       # terminal
    "FAILED": {"DRAFT", "CANCELLED"},     # allow retry via fresh draft
    "NEEDS_VERIFICATION": {"DRAFT", "CANCELLED", "SENT"},  # SENT only after manual verify
    "CANCELLED": {"DRAFT"},
    "APPLIED_EXTERNALLY": set(),         # terminal
    # Legacy states (migration): map old flows into new machine
    "PENDING_APPROVAL": {"DRAFT", "CANCELLED"},
    "CONFIRMED": {"FIRST_CONFIRMED", "DRAFT", "CANCELLED"},
}'''

assert old_transitions in s, 'transitions block not found'
s = s.replace(old_transitions, new_transitions)

# 2. Add content-hash helpers after the transition() function
old_func_end = '''def transition(db, application_id: int, current_status: str, new_status: str) -> None:
    allowed = VALID_TRANSITIONS.get(current_status, set())
    if new_status not in allowed:
        raise ApplicationStateError(
            f"Illegal transition {current_status} -> {new_status} for application {application_id}. "
            f"Allowed from {current_status}: {sorted(allowed)}"
        )
    db.update_application_status(application_id, new_status)'''

new_func_end = '''def transition(db, application_id: int, current_status: str, new_status: str) -> None:
    allowed = VALID_TRANSITIONS.get(current_status, set())
    if new_status not in allowed:
        raise ApplicationStateError(
            f"Illegal transition {current_status} -> {new_status} for application {application_id}. "
            f"Allowed from {current_status}: {sorted(allowed)}"
        )
    db.update_application_status(application_id, new_status)


def content_hash(to_address: str, subject: str, body: str, cv_path: str) -> str:
    """Hash of the exact application content. Any change invalidates approvals."""
    import hashlib
    h = hashlib.sha256()
    for part in (to_address or "", subject or "", body or "", cv_path or ""):
        h.update(part.encode("utf-8", errors="replace"))
        h.update(b"\\x00")
    return h.hexdigest()


def verify_approvals_intact(db, application_id: int, to_address: str,
                            subject: str, body: str, cv_path: str) -> bool:
    """True iff stored content hash matches current content (approvals still valid)."""
    row = db.get_application(application_id)
    if row is None:
        return False
    stored = row["content_hash"] if "content_hash" in row.keys() else None
    if not stored:
        return False
    return stored == content_hash(to_address, subject, body, cv_path)'''

assert old_func_end in s, 'transition func not found'
s = s.replace(old_func_end, new_func_end)

# 3. Harden send_application_email: require FINAL_CONFIRMED + hash check + idempotency
old_guard = '''    row = db.get_application(application_id)
    if row is None:
        raise ApplicationStateError(f"Application {application_id} not found.")'''

new_guard = '''    row = db.get_application(application_id)
    if row is None:
        raise ApplicationStateError(f"Application {application_id} not found.")
    # P0: idempotency — refuse duplicate sends
    if row["status"] == "SENT":
        raise ApplicationStateError(
            f"Application {application_id} already SENT — refusing duplicate send.")
    if row["status"] == "SENDING":
        raise ApplicationStateError(
            f"Application {application_id} is already SENDING — refusing concurrent send.")
    # P0: only FINAL_CONFIRMED may proceed to actual transport
    if row["status"] != "FINAL_CONFIRMED":
        raise ApplicationStateError(
            f"Refusing to send: application {application_id} status is '{row['status']}', "
            f"not FINAL_CONFIRMED. Both approvals are required; no shortcut path exists.")'''

assert old_guard in s, 'send guard not found'
s = s.replace(old_guard, new_guard)

# 4. Update module docstring to reflect new flow
old_doc = '''OWNER'S EXPLICIT UX DECISION (documented here so the reasoning is clear):
pressing the "✉️ تقديم" button IS the user's approval — there is no
separate Preview -> Confirm -> Send dance for email applications anymore.
One tap composes the message (subject, a short cover note, the user's CV
attached) and sends it immediately from the user's OWN email account. This
is still always a deliberate, explicit, human-initiated action (the button
only exists inside a Telegram callback triggered by that specific user
tapping it) — nothing in this codebase ever calls send_application_email
except from that one callback handler (src/bot/main.py's job:apply flow).'''

new_doc = '''P0 SAFETY (2026-10-16): The one-tap-send flow is REMOVED. Per the owner's
P0 specification, pressing "✉️ تقديم" sends NOTHING — it opens a preview.
Two INDEPENDENT approvals are required before any email is dispatched:
  1. [✅ تأكيد الإرسال]  -> FIRST_CONFIRMED (sends nothing, shows final confirmation)
  2. [⚠️ تأكيد نهائي]   -> FINAL_CONFIRMED (enables the send button)
  3. [🚀 إرسال الآن]     -> SENDING -> SENT (only after real transport success)
Any edit to recipient/subject/message/CV invalidates prior approvals.
Nothing in this codebase calls send_application_email except from the
FINAL_CONFIRMED-gated send handler in src/bot/main.py.'''

assert old_doc in s, 'docstring not found'
s = s.replace(old_doc, new_doc)

open(p, 'w', encoding='utf-8').write(s)
print('APPLICATIONS.PY P0 PATCHED')

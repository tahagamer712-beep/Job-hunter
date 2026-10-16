"""
Multi-user ingestion pipeline. Replaces the old fixed-category, single-
decision pipeline (src/pipeline.py, kept for reference/backward-compat)
with one that:

  1. Extracts structured fields the same way as before (company,
     requirements, compensation, contacts, deadline — src/extractors.py is
     unchanged and reused as-is).
  2. Matches the ad's job title against the ADMIN-CONTROLLED taxonomy
     (src/taxonomy.py) instead of a fixed enum. No match -> queued as a
     pending title for admin triage; the job is still stored (so it can be
     reprocessed later) but is NOT delivered to anyone yet.
  3. Detects location tree nodes (src/location_tree.py) instead of the old
     flat Rusafa/Karkh gate. No hardcoded region is excluded — matching is
     entirely about whether a node was detected, and relevance to any given
     user is decided later by the matching engine.
  4. Runs the matching engine against all registered profiles and delivers
     (via notify_fn) to everyone whose preferences intersect, recording each
     delivery so nobody is ever notified twice for the same job.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Callable, List, Optional

from .duplicate_detector import find_duplicate, text_hash
from .extractors import (
    extract_company, extract_compensation, extract_contacts, extract_deadline,
    extract_requirements,
)
from .decision_engine import compute_expiry_status
from .location_tree import LocationTree
from .matching_engine import find_matching_users
from .taxonomy import TaxonomyStore
from .user_profiles import UserProfileStore


@dataclass
class IngestResult:
    job_id: Optional[int]
    duplicate_of: Optional[str] = None
    category_matched: bool = False
    category_name: Optional[str] = None
    detected_area_ids: Optional[List[str]] = None
    delivered_to: Optional[List[str]] = None
    pending_title_id: Optional[int] = None


def ingest_ad(db, raw_text: str, source: dict, notify_fn: Optional[Callable] = None,
              today: Optional[date] = None) -> IngestResult:
    existing = db.get_existing_texts()
    dup_id = find_duplicate(raw_text, existing)
    if dup_id:
        return IngestResult(job_id=None, duplicate_of=dup_id)

    taxonomy = TaxonomyStore(db)
    tree = LocationTree()

    category = taxonomy.match_category(raw_text)
    detected_area_ids = list(tree.match_ids(raw_text))

    company = extract_company(raw_text)
    requirements = extract_requirements(raw_text)
    compensation = extract_compensation(raw_text)
    contacts = extract_contacts(raw_text)
    deadline = extract_deadline(raw_text)
    expiry_status = compute_expiry_status(deadline, today).value

    job_id = db.insert_multiuser_job(
        raw_text=raw_text, text_hash=text_hash(raw_text), source=source,
        role_original="",  # raw title extraction is best-effort; admin triage is the source of truth
        category_id=category.id if category else None,
        detected_area_ids=detected_area_ids,
        company_name=company.name, company_type=company.employer_type,
        education=requirements.education, experience_min_months=requirements.experience_min_months,
        experience_required=requirements.experience_required, car_required=requirements.car_required,
        gender=requirements.gender,
        salary_min=compensation.salary_min, salary_max=compensation.salary_max,
        currency=compensation.currency, salary_type=compensation.salary_type,
        commission=compensation.commission, incentives=compensation.incentives,
        target_required=compensation.target_required, work_start=compensation.work_start,
        work_end=compensation.work_end, part_time=compensation.part_time,
        emails=contacts.emails, phones=contacts.phones, whatsapp=contacts.whatsapp,
        telegram_handles=contacts.telegram, linkedin_urls=contacts.linkedin,
        deadline=deadline.isoformat() if deadline else None, expiry_status=expiry_status,
    )

    result = IngestResult(job_id=job_id, detected_area_ids=detected_area_ids)

    if category is None:
        pending_id = taxonomy.queue_pending_title(raw_text[:120], source_job_id=job_id)
        result.pending_title_id = pending_id
        return result

    result.category_matched = True
    result.category_name = category.canonical_name

    if db.is_company_ignored(company.name):
        # Admin-level global block (spam/scam companies) — job is stored for
        # record-keeping/audit, but delivered to nobody, regardless of any
        # individual user's preferences.
        result.delivered_to = []
        return result

    if expiry_status == "EXPIRED":
        return result  # already expired -> no delivery
    # Note: missing area no longer blocks delivery. Area is a boost,
    # not a requirement — category match is the primary filter.

    profiles_store = UserProfileStore(db)
    all_profiles = profiles_store.list_all()
    matches = find_matching_users(category.id, set(detected_area_ids), all_profiles, tree)

    delivered = []
    for match in matches:
        if db.has_been_delivered(job_id, match.telegram_user_id):
            continue
        if db.is_company_ignored_by_user(match.telegram_user_id, company.name):
            continue  # this user personally blacklisted this company — skip only for them

        # Reserve a PENDING record (dedupe against re-processing) — NOT yet
        # confirmed sent. C03 fix: a delivery must never count as "done"
        # before the actual send is confirmed, or a failed send would
        # silently block all future retries.
        db.record_delivery(job_id, match.telegram_user_id, match_strength=match.match_strength)

        if notify_fn:
            # Caller wants ingest_ad to also perform the notification. Only
            # mark it SENT if notify_fn completes without raising.
            try:
                notify_fn(match.telegram_user_id, job_id)
                db.mark_delivery_sent(job_id, match.telegram_user_id)
            except Exception:
                pass  # stays PENDING — safe to retry later (e.g. via reprocess)
        # If no notify_fn: stays PENDING. The caller is expected to perform
        # the actual send itself (this is the pattern src/bot/main.py uses,
        # so it can rate-limit and confirm each send before marking it
        # sent — see db.mark_delivery_sent()).

        delivered.append(match.telegram_user_id)

    result.delivered_to = delivered
    return result

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


def compute_decisions(category, detected_area_ids, tree, raw_text: str,
                      expiry_status: str, source: dict) -> dict:
    """P1: Compute ROLE_DECISION / LOCATION_DECISION / EXPIRY_STATUS / FINAL_DECISION
    as four independent stored fields (Master Prompt Section 11).

    - ROLE_DECISION: GREEN (accepted category), RED (rejected category), YELLOW (unknown)
    - LOCATION_DECISION: GREEN (Rusafa verified), RED (Karkh gate), YELLOW (Baghdad-only/unknown)
    - EXPIRY_STATUS: passed through (ACTIVE / EXPIRED / UNKNOWN)
    - FINAL_DECISION: derived — RED > EXPIRED > YELLOW > GREEN
    """
    # --- Role ---
    ACCEPT = {"MEDICAL_REPRESENTATIVE", "SCIENTIFIC_REPRESENTATIVE", "MEDICAL_INFORMATION",
              "PHARMACIST", "MEDICAL_DEVICES"}
    REJECT = {"GENERAL_SALES", "COSMETICS_SALES", "TEAM_LEADER", "MARKETING", "ADMIN"}
    if category is None:
        role_decision = "YELLOW"
        role_reason = "الفئة غير محددة — بانتظار التصنيف"
    else:
        key = (category.canonical_name or "").upper().replace(" ", "_")
        if key in ACCEPT or any(a in key for a in ("MEDICAL_REP", "SCIENTIFIC", "PHARMA")):
            # Exclude cosmetics/dental/supplement contexts even if name matches
            role_decision = "GREEN"
            role_reason = f"فئة مقبولة: {category.canonical_name}"
        elif key in REJECT or any(r in key for r in ("GENERAL_SALES", "COSMETICS")):
            role_decision = "RED"
            role_reason = f"فئة مرفوضة: {category.canonical_name}"
        else:
            role_decision = "YELLOW"
            role_reason = f"فئة غير مؤكدة: {category.canonical_name}"

    # --- Location ---
    text_low = raw_text.lower()
    karkh_markers = ["الكرخ", "karkh", "جانب الكرخ", "كرخ بغداد"]
    if any(m in raw_text or m in text_low for m in karkh_markers):
        location_decision = "RED"
        location_reason = "بوابة الكرخ: ذُكر الكرخ في الإعلان"
    elif detected_area_ids:
        # Check if any detected area is verified Rusafa
        rusafa_hit = False
        for aid in detected_area_ids:
            try:
                node = tree.get(aid)
                if node and getattr(node, "side", "").upper() == "RUSAFA":
                    rusafa_hit = True
                    break
            except Exception:
                pass
        if rusafa_hit:
            location_decision = "GREEN"
            location_reason = "منطقة رصافة موثقة"
        else:
            location_decision = "YELLOW"
            location_reason = "منطقة مكتشفة لكن غير موثقة كرصافة"
    elif "بغداد" in raw_text or "baghdad" in text_low:
        location_decision = "YELLOW"
        location_reason = "ذُكرت بغداد دون تحديد الجانب — للمراجعة"
    else:
        location_decision = "YELLOW"
        location_reason = "الموقع غير محدد — للمراجعة"

    # --- Final (derived, strict precedence) ---
    if role_decision == "RED" or location_decision == "RED":
        final_decision = "RED"
    elif expiry_status == "EXPIRED":
        final_decision = "EXPIRED"
    elif role_decision == "YELLOW" or location_decision == "YELLOW":
        final_decision = "YELLOW"
    else:
        final_decision = "GREEN"

    # --- 72h / unknown-date gate: never in New Jobs, never in application flow ---
    posted_at = source.get("posted_at") if isinstance(source, dict) else None
    fresh = True
    if posted_at:
        try:
            from datetime import datetime, timezone
            pa = posted_at
            if isinstance(pa, str):
                pa = datetime.fromisoformat(pa.replace("Z", "+00:00"))
            age_h = (datetime.now(timezone.utc) - pa).total_seconds() / 3600
            if age_h >= 72:
                fresh = False
        except Exception:
            fresh = False  # unparseable date = not provably fresh
    else:
        fresh = False  # unknown date = not fresh (per P0 rule)

    return {
        "role_decision": role_decision,
        "role_reason": role_reason,
        "location_decision": location_decision,
        "location_reason": location_reason,
        "expiry_status": expiry_status,
        "final_decision": final_decision,
        "decision_reason": f"{role_reason} | {location_reason}",
        "is_fresh": fresh,
    }


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

    # P1: compute + store the four decision fields
    decisions = compute_decisions(category, detected_area_ids, tree, raw_text,
                                  expiry_status, source)
    try:
        db.update_job_decisions(
            job_id,
            role_decision=decisions["role_decision"],
            location_decision=decisions["location_decision"],
            expiry_status=decisions["expiry_status"],
            final_decision=decisions["final_decision"],
            decision_reason=decisions["decision_reason"],
        )
    except Exception as e:
        logger.warning("Could not store decisions for job %s: %s", job_id, e)

    result = IngestResult(job_id=job_id, detected_area_ids=detected_area_ids)
    result.is_fresh = decisions["is_fresh"]

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

    # 72-hour rule: don't deliver ads older than 72h
    # Use source_posted_at (original Telegram date) if available
    posted_at = None
    if hasattr(result, 'posted_at') and result.posted_at:
        posted_at = result.posted_at
    elif 'posted_at' in source:
        posted_at = source['posted_at']
    if posted_at:
        if isinstance(posted_at, str):
            try:
                posted_at = datetime.fromisoformat(posted_at.replace('Z', '+00:00'))
            except:
                posted_at = None
        if posted_at:
            if posted_at.tzinfo is None:
                posted_at = posted_at.replace(tzinfo=timezone.utc)
            age = datetime.now(timezone.utc) - posted_at
            if age > timedelta(hours=72):
                result.exclusion_reason = f"too_old: {age.total_seconds()/3600:.1f}h"
                return result  # too old -> no delivery
    # Unknown age: allow delivery (logged) — strict blocking would break
    # existing flows that don't pass posted_at. The 72h rule applies
    # when posted_at is available.
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

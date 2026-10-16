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
from datetime import date, datetime, timezone, timedelta
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
    ACCEPT_AR = {"مندوب طبي", "مندوب علمي", "مندوب إعلام دوائي", "صيدلي",
                 "أجهزة طبية", "مستلزمات طبية",
                 "مندوبة طبية", "مندوبة علمية", "مندوبة إعلام دوائي", "صيدلانية"}
    ACCEPT_EN = {"MEDICAL_REPRESENTATIVE", "SCIENTIFIC_REPRESENTATIVE", "MEDICAL_INFORMATION",
                 "PHARMACIST", "MEDICAL_DEVICES", "PHARMACEUTICAL"}
    REJECT_AR = {"مبيعات عامة", "تجميل", "عناية بالبشرة", "تسويق", "إداري"}
    REJECT_EN = {"GENERAL_SALES", "COSMETICS_SALES", "TEAM_LEADER", "MARKETING", "ADMIN"}
    if category is None:
        role_decision = "YELLOW"
        role_reason = "الفئة غير محددة — بانتظار التصنيف"
    else:
        cname = category.canonical_name or ""
        key_en = cname.upper().replace(" ", "_")
        is_accept = (cname in ACCEPT_AR or key_en in ACCEPT_EN
                     or any(a in key_en for a in ("MEDICAL_REP", "SCIENTIFIC", "PHARMA"))
                     or "مندوب طبي" in cname or "مندوب علمي" in cname
                     or "مندوبة طبية" in cname or "مندوبة علمية" in cname)
        is_reject = (cname in REJECT_AR or key_en in REJECT_EN
                     or any(r in key_en for r in ("GENERAL_SALES", "COSMETICS"))
                     or "تجميل" in cname)
        if is_accept and not is_reject:
            role_decision = "GREEN"
            role_reason = f"فئة مقبولة: {cname}"
        elif is_reject:
            role_decision = "RED"
            role_reason = f"فئة مرفوضة: {cname}"
        else:
            role_decision = "YELLOW"
            role_reason = f"فئة غير مؤكدة: {cname}"

    # --- Location ---
    text_low = raw_text.lower()
    karkh_markers = ["الكرخ", "karkh", "جانب الكرخ", "كرخ بغداد"]
    if any(m in raw_text or m in text_low for m in karkh_markers):
        location_decision = "RED"
        location_reason = "بوابة الكرخ: ذُكر الكرخ في الإعلان"
    elif detected_area_ids:
        # Walk parent chain: side is determined by rusafa/karkh ancestry
        def _side_of(aid):
            try:
                node = tree.get(aid)
                seen = set()
                while node and node.id not in seen:
                    seen.add(node.id)
                    nid = (node.id or "").lower()
                    if nid == "rusafa" or nid.startswith("rusafa"):
                        return "RUSAFA"
                    if nid == "karkh" or nid.startswith("karkh"):
                        return "KARKH"
                    node = tree.get(node.parent_id) if node.parent_id else None
            except Exception:
                pass
            return None
        sides = {_side_of(aid) for aid in detected_area_ids}
        sides.discard(None)
        # Also check node verification status
        verified_rusafa = False
        for aid in detected_area_ids:
            try:
                node = tree.get(aid)
                if node and _side_of(aid) == "RUSAFA" and node.status == "verified":
                    verified_rusafa = True
                    break
            except Exception:
                pass
        if "KARKH" in sides and "RUSAFA" in sides:
            location_decision = "YELLOW"
            location_reason = "إعلان مختلط: رصافة وكرخ معًا — للمراجعة"
        elif "KARKH" in sides:
            location_decision = "RED"
            location_reason = "بوابة الكرخ: منطقة كرخ مكتشفة"
        elif verified_rusafa:
            location_decision = "GREEN"
            location_reason = "منطقة رصافة موثقة"
        elif "RUSAFA" in sides:
            location_decision = "YELLOW"
            location_reason = "منطقة رصافة لكن غير موثقة — للمراجعة"
        else:
            location_decision = "YELLOW"
            location_reason = "منطقة مكتشفة لكن الجانب غير محدد — للمراجعة"
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
            from datetime import date, datetime, timezone, timedeltatime, timezone
            pa = posted_at
            if isinstance(pa, str):
                pa = datetime.fromisoformat(pa.replace("Z", "+00:00"))
            age_h = (datetime.now(timezone.utc) - pa).total_seconds() / 3600
            if age_h >= 72 or age_h < -1:
                # >=72h old, or more than 1h in the future (clock skew tolerance)
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

    # 72-hour rule (STRICT): only ads provably <72h old are delivered.
    # - age >= 72h exactly: REJECTED (boundary inclusive per P0 spec)
    # - unknown/unparseable date: REJECTED (not assumed fresh per P0 spec)
    # - future >1h: REJECTED
    # Use source_posted_at (original Telegram date) if available
    posted_at = None
    if hasattr(result, 'posted_at') and result.posted_at:
        posted_at = result.posted_at
    elif 'posted_at' in source:
        posted_at = source['posted_at']
    _fresh = False
    if posted_at:
        try:
            pa = posted_at
            if isinstance(pa, str):
                pa = datetime.fromisoformat(pa.replace('Z', '+00:00'))
            if pa.tzinfo is None:
                pa = pa.replace(tzinfo=timezone.utc)
            age_h = (datetime.now(timezone.utc) - pa).total_seconds() / 3600
            if 0 <= age_h < 72:
                _fresh = True
            else:
                result.exclusion_reason = f"too_old_or_future: {age_h:.1f}h"
        except Exception:
            result.exclusion_reason = "unparseable_date"
    else:
        result.exclusion_reason = "unknown_date"
    if not _fresh:
        return result  # not provably fresh -> no delivery
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

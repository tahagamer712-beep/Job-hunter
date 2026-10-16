"""
LinkedIn integration — 3-layer design (Master Prompt Section 6), rewired to
the multi-user pipeline (previously this module used the old single-user
role_classifier/location_engine/decision_engine system, which was fully
disconnected from the admin's taxonomy and the location tree — LinkedIn
jobs could never actually match or deliver to anyone, and the bot would
crash trying to render one; fixed here).

Layer A: official API, if available. NOT implemented — stays inactive.
Layer B: LinkedIn URLs extracted from ad text (extractors.py -> ContactInfo.linkedin).
Layer C: user manually submits a LinkedIn job URL, optionally with the
         visible job text pasted alongside it.

NEVER: scraping, browser automation, or automated submission on LinkedIn.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import List, Optional

from .database import Database
from .pipeline_multiuser import ingest_ad

_LINKEDIN_JOB_URL_RE = re.compile(r"^https?://(?:www\.)?linkedin\.com/jobs/", re.IGNORECASE)


class LinkedInLayerAUnavailable(Exception):
    """Raised by any attempt to use Layer A when no official access is configured."""


def official_api_search(*args, **kwargs):
    raise LinkedInLayerAUnavailable(
        "No official LinkedIn API access configured. Use Layer B (Telegram-"
        "sourced links) or Layer C (manual URL submission) instead."
    )


def is_valid_linkedin_job_url(url: str) -> bool:
    return bool(_LINKEDIN_JOB_URL_RE.match(url.strip()))


@dataclass
class LinkedInSubmitResult:
    job_id: int
    category_matched: bool
    category_name: Optional[str]
    delivered_to: List[str]
    needs_text: bool  # True if submitted with no ad text — nothing to classify yet


def _stub_hash(url: str) -> str:
    from .duplicate_detector import text_hash
    return text_hash(f"linkedin-url-stub:{url}")


def submit_manual_linkedin_url(db: Database, url: str, pipeline_context: Optional[str] = None,
                                today: Optional[date] = None) -> LinkedInSubmitResult:
    """
    Layer C. We do NOT fetch/scrape the LinkedIn page — if the user also
    pastes the visible job text, it goes through the EXACT same multi-user
    pipeline as any other ad (src/pipeline_multiuser.py:ingest_ad), so
    LinkedIn jobs classify and deliver just like Telegram/manual ones.
    Without pasted text, we store a URL-only stub (category unclassified)
    that the user can still open manually and mark as applied.
    """
    if not is_valid_linkedin_job_url(url):
        raise ValueError("Not a recognized LinkedIn job URL (expected linkedin.com/jobs/...).")

    source = {"platform": "linkedin", "channel": None, "message_id": None, "url": url, "date": None}

    if pipeline_context:
        result = ingest_ad(db, pipeline_context, source, today=today)
        if result.job_id is not None:
            db.set_job_linkedin_url(result.job_id, url)
        return LinkedInSubmitResult(
            job_id=result.job_id, category_matched=result.category_matched,
            category_name=result.category_name, delivered_to=result.delivered_to or [],
            needs_text=False,
        )

    raw_text = f"[LinkedIn job — no ad text provided] {url}"
    job_id = db.insert_multiuser_job(
        raw_text=raw_text, text_hash=_stub_hash(url), source=source,
        role_original="", category_id=None, detected_area_ids=[],
        company_name=None, company_type=None, education=[],
        experience_min_months=None, experience_required=None, car_required=None, gender=None,
        salary_min=None, salary_max=None, currency=None, salary_type=None,
        commission=None, incentives=None, target_required=None, work_start=None, work_end=None, part_time=None,
        emails=[], phones=[], whatsapp=[], telegram_handles=[], linkedin_urls=[url],
        deadline=None, expiry_status="UNKNOWN",
    )
    db.set_job_linkedin_url(job_id, url)
    from .taxonomy import TaxonomyStore
    TaxonomyStore(db).queue_pending_title(f"[LinkedIn, no text] {url[:80]}", source_job_id=job_id)

    return LinkedInSubmitResult(job_id=job_id, category_matched=False, category_name=None,
                                 delivered_to=[], needs_text=True)


def mark_linkedin_applied(db: Database, job_id: int, telegram_user_id: str):
    """
    User applied manually on LinkedIn; bot never applies on their behalf.
    Uses the per-user application table (src/applications.py) — the same
    one email applications use — so this is properly scoped to just this
    user, not any shared status column.
    """
    from .applications import transition as app_transition

    app_id = db.create_or_get_application(job_id, telegram_user_id)
    app_row = db.get_application(app_id)
    app_transition(db, app_id, app_row["status"], "APPLIED_EXTERNALLY")

"""
⚠️ LEGACY / REFERENCE ONLY — NOT part of the live bot path.

This is the original single-owner, single-decision pipeline (fixed
RoleCategory enum, global Rusafa/Karkh gate). The live multi-user bot uses
src/pipeline_multiuser.py instead (admin-defined taxonomy, per-user region
preferences, no global region exclusion). This file — along with
role_classifier.py, location_engine.py, decision_engine.py (partially
reused), and approval.py — is kept because it's still fully tested and
useful as a reference/comparison point, and telegram_listener.py's __main__
demo block still uses it as a default. If you're extending the live bot,
you almost certainly want src/pipeline_multiuser.py, not this file.

Pipeline orchestrator — RAW POST -> ... -> DECISION (Master Prompt Section 2).

This is the single entry point used identically by the Telegram scanner,
Manual Forward Intake, and LinkedIn Layer B/C sources (Master Prompt
Section 7.2: manual ads must go through the exact same pipeline).
"""
from __future__ import annotations

from datetime import date
from typing import Optional

from .decision_engine import decide
from .extractors import (
    extract_company, extract_compensation, extract_contacts,
    extract_deadline, extract_requirements,
)
from .location_engine import LocationEngine
from .models import JobRecord
from .normalizer import NormalizedPost
from .role_classifier import CombinedRoleClassifier


class JobHunterPipeline:
    def __init__(self, location_engine: LocationEngine = None,
                 role_classifier: CombinedRoleClassifier = None):
        self.location_engine = location_engine or LocationEngine()
        self.role_classifier = role_classifier or CombinedRoleClassifier()

    def process(self, raw_text: str, source: dict, today: Optional[date] = None) -> JobRecord:
        post = NormalizedPost(raw_text, source)

        role_result = self.role_classifier.classify(post.raw_text)
        location_result = self.location_engine.evaluate(post.raw_text)

        company = extract_company(post.raw_text)
        requirements = extract_requirements(post.raw_text)
        compensation = extract_compensation(post.raw_text)
        contacts = extract_contacts(post.raw_text)
        deadline = extract_deadline(post.raw_text)

        decision = decide(
            role_result, location_result,
            deadline=deadline, today=today,
            has_contact=contacts.has_any,
        )

        side = None
        area = None
        if location_result.rusafa_matches:
            side = "RUSAFA"
            area = location_result.rusafa_matches[0].area_name_en
        elif location_result.karkh_matches:
            side = "KARKH"
            area = location_result.karkh_matches[0].area_name_en

        record = JobRecord(
            raw_text=post.raw_text,
            source_platform=source.get("platform", ""),
            source_channel=source.get("channel"),
            source_message_id=source.get("message_id"),
            source_message_url=source.get("url"),
            source_date=source.get("date"),
            linkedin_url=source.get("linkedin_url") or (contacts.linkedin[0] if contacts.linkedin else None),

            role_original=role_result.role_original,
            role_normalized=role_result.role_normalized.value,
            role_decision=role_result.decision.value,

            city="Baghdad" if location_result.baghdad_mentioned or location_result.city_mentioned else None,
            side=side,
            area=area,
            location_decision=location_result.decision.value,
            is_partial_location=location_result.is_partial,

            company_name=company.name,
            company_type=company.employer_type,

            education=requirements.education,
            experience_min_months=requirements.experience_min_months,
            experience_required=requirements.experience_required,
            car_required=requirements.car_required,
            gender=requirements.gender,

            emails=contacts.emails,
            phones=contacts.phones,
            whatsapp=contacts.whatsapp,
            telegram_handles=contacts.telegram,
            linkedin_urls=contacts.linkedin,

            salary_min=compensation.salary_min,
            salary_max=compensation.salary_max,
            currency=compensation.currency,
            salary_type=compensation.salary_type,
            commission=compensation.commission,
            incentives=compensation.incentives,
            target_required=compensation.target_required,
            work_start=compensation.work_start,
            work_end=compensation.work_end,
            part_time=compensation.part_time,

            deadline=deadline.isoformat() if deadline else None,
            expiry_status=decision.expiry_status.value,

            match_score=decision.match_score,
            final_decision=decision.final_decision.value,
            decision_reason=decision.reason,

            status="PENDING_APPROVAL",
        )
        return record

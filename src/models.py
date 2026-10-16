"""Job record data model — mirrors the SQLite schema in database.py."""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import date, datetime
from typing import List, Optional


@dataclass
class JobRecord:
    id: Optional[int] = None

    raw_text: str = ""

    # Source
    source_platform: str = ""          # telegram | linkedin | manual
    source_channel: Optional[str] = None
    source_message_id: Optional[str] = None
    source_message_url: Optional[str] = None
    source_date: Optional[str] = None
    linkedin_url: Optional[str] = None

    # Role
    role_original: str = ""
    role_normalized: str = "UNKNOWN"
    role_decision: str = "YELLOW"

    # Location
    city: Optional[str] = None
    side: Optional[str] = None
    area: Optional[str] = None
    location_decision: str = "YELLOW"
    is_partial_location: bool = False

    # Company
    company_name: Optional[str] = None
    company_type: Optional[str] = None

    # Requirements
    education: List[str] = field(default_factory=list)
    experience_min_months: Optional[int] = None
    experience_required: Optional[bool] = None
    car_required: Optional[bool] = None
    gender: Optional[str] = None

    # Compensation
    salary_min: Optional[float] = None
    salary_max: Optional[float] = None
    currency: Optional[str] = None
    salary_type: Optional[str] = None
    commission: Optional[bool] = None
    incentives: Optional[bool] = None
    target_required: Optional[bool] = None
    work_start: Optional[str] = None
    work_end: Optional[str] = None
    part_time: Optional[bool] = None

    # Contacts
    emails: List[str] = field(default_factory=list)
    phones: List[str] = field(default_factory=list)
    whatsapp: List[str] = field(default_factory=list)
    telegram_handles: List[str] = field(default_factory=list)
    linkedin_urls: List[str] = field(default_factory=list)

    # Deadline / status
    deadline: Optional[str] = None       # ISO date string
    expiry_status: str = "UNKNOWN"

    # Decision
    match_score: int = 0
    final_decision: str = "YELLOW"
    decision_reason: str = ""

    # Workflow status
    status: str = "PENDING_APPROVAL"     # PENDING_APPROVAL | PREVIEW | CONFIRMED | SENT | SAVED | IGNORED
    created_at: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)

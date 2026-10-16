"""
Structured extraction for company, requirements, contacts, and deadline.

Golden rule (Master Prompt Section 5 / 25): never invent a value. Anything
not clearly present in the ad is left as None/UNKNOWN — the UI is responsible
for displaying "UNKNOWN", not this module.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, date
from typing import List, Optional

from .normalizer import normalize_for_matching, normalize_digits

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
_PHONE_RE = re.compile(r"(?:\+?964|0)?7\d{2}[\s\-]?\d{3}[\s\-]?\d{4}")
_TELEGRAM_USERNAME_RE = re.compile(r"(?:@|t\.me/)([A-Za-z0-9_]{4,})")
_LINKEDIN_URL_RE = re.compile(r"https?://(?:www\.)?linkedin\.com/\S+", re.IGNORECASE)

_SCIENTIFIC_BUREAU_TERMS = ["مكتب علمي", "scientific bureau", "مكتب علمي وكيل"]

_DATE_PATTERNS = [
    # 18/10/2025 or 18-10-2025
    (re.compile(r"\b(\d{1,2})[/\-](\d{1,2})[/\-](\d{4})\b"), "dmy"),
    # 2026/03/20 or 2026-03-20
    (re.compile(r"\b(\d{4})[/\-](\d{1,2})[/\-](\d{1,2})\b"), "ymd"),
]

_MONTHS = {
    "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3,
    "apr": 4, "april": 4, "may": 5, "jun": 6, "june": 6, "jul": 7, "july": 7,
    "aug": 8, "august": 8, "sep": 9, "sept": 9, "september": 9, "oct": 10,
    "october": 10, "nov": 11, "november": 11, "dec": 12, "december": 12,
}
_TEXT_DATE_RE = re.compile(
    r"\b([A-Za-z]{3,9})\s+(\d{1,2}),?\s+(\d{4})\b|\b(\d{1,2})\s+([A-Za-z]{3,9})\b"
)


@dataclass
class ContactInfo:
    emails: List[str] = field(default_factory=list)
    phones: List[str] = field(default_factory=list)
    whatsapp: List[str] = field(default_factory=list)
    telegram: List[str] = field(default_factory=list)
    linkedin: List[str] = field(default_factory=list)

    @property
    def has_email(self) -> bool:
        return bool(self.emails)

    @property
    def has_any(self) -> bool:
        return bool(self.emails or self.phones or self.whatsapp or self.telegram)


@dataclass
class CompanyInfo:
    name: Optional[str] = None            # None => UNKNOWN, never invented
    employer_type: Optional[str] = None   # "COMPANY" | "SCIENTIFIC_BUREAU" | None


@dataclass
class RequirementsInfo:
    education: List[str] = field(default_factory=list)
    experience_required: Optional[bool] = None
    experience_min_months: Optional[int] = None
    experience_preferred: Optional[bool] = None
    car_required: Optional[bool] = None
    car_preferred: Optional[bool] = None
    gender: Optional[str] = None  # male | female | both | None(unknown)


@dataclass
class CompensationInfo:
    salary_min: Optional[float] = None
    salary_max: Optional[float] = None
    currency: Optional[str] = None
    salary_type: Optional[str] = None       # "fixed" | "negotiated" | None
    commission: Optional[bool] = None
    incentives: Optional[bool] = None
    target_required: Optional[bool] = None
    work_start: Optional[str] = None         # "HH:MM"
    work_end: Optional[str] = None
    part_time: Optional[bool] = None


_CURRENCY_SYMBOLS = {"$": "USD", "usd": "USD", "iqd": "IQD", "دينار": "IQD", "دولار": "USD"}
_SALARY_RANGE_RE = re.compile(
    r"(?:(\$|usd|دولار)\s*)?(\d{2,6})\s*(\$|usd|دولار|iqd|دينار)?"
    r"\s*(?:-|–|to|الى|إلى)\s*"
    r"(?:(\$|usd|دولار)\s*)?(\d{2,6})\s*(\$|usd|دولار|iqd|دينار)?",
    re.IGNORECASE,
)
_TIME_RANGE_RE = re.compile(
    r"(?<!\d)(\d{1,2})(?::(\d{2}))?\s*(am|pm|صباحا|مساء)?\s*(?:-|–|to|الى|الي)\s*"
    r"(\d{1,2})(?::(\d{2}))?\s*(am|pm|صباحا|مساء)?(?!\d)"
)


def _valid_time_component(hour: int, minute: Optional[str]) -> bool:
    if not (0 <= hour <= 23):
        return False
    if minute is not None and not (0 <= int(minute) <= 59):
        return False
    return True


def _to_24h(hour: int, minute: Optional[str], meridiem: Optional[str]) -> str:
    minute = minute or "00"
    if meridiem:
        meridiem = meridiem.lower()
        if meridiem in ("pm", "مساء") and hour != 12:
            hour += 12
        if meridiem in ("am", "صباحا") and hour == 12:
            hour = 0
    return f"{hour:02d}:{minute}"


def extract_compensation(raw_text: str) -> CompensationInfo:
    match_text = normalize_for_matching(raw_text)
    normalized_digits_text = normalize_digits(match_text)
    comp = CompensationInfo()
    # Arabic IQD salary: "الراتب: 1,500,000 دينار" or "1500000 دينار"
    m = re.search(r"(\d[\d,]*)\s*دينار", normalized_digits_text)
    if m:
        try:
            val = int(m.group(1).replace(",", ""))
            if 100000 <= val <= 50000000:  # sane IQD monthly range
                comp.salary_min = comp.salary_max = float(val)
                comp.currency = "IQD"
                comp.salary_type = "fixed"
        except ValueError:
            pass

    m = _SALARY_RANGE_RE.search(normalized_digits_text)
    if m:
        cur_before_1, lo, cur_after_1, cur_before_2, hi, cur_after_2 = m.groups()
        cur_token = next(
            (t for t in [cur_before_1, cur_after_1, cur_before_2, cur_after_2] if t), None
        )
        if cur_token is not None:
            # Only accept as a salary if a currency token was actually
            # captured — without one, "25-30" (an age range, a phone
            # fragment, etc.) would otherwise be misread as a salary.
            comp.salary_min = float(lo)
            comp.salary_max = float(hi)
            comp.currency = _CURRENCY_SYMBOLS.get(cur_token.strip().lower())
            comp.salary_type = "fixed"

    if any(p in match_text for p in ["negotiable", "يحدد اثناء المقابلة", "الراتب يحدد", "قابل للتفاوض"]):
        comp.salary_type = "negotiated"

    if "commission" in match_text or "عمولة" in match_text or "عمولات" in match_text:
        comp.commission = True
    if "incentive" in match_text or "حوافز" in match_text or "حافز" in match_text:
        comp.incentives = True
    if "target" in match_text or "تارگت" in match_text or "هدف مبيعات" in match_text:
        comp.target_required = True

    # Work hours: only extract with explicit time format or work context.
    # Never extract from bare number ranges (phones, salaries, etc.)
    work_context = any(w in match_text for w in [
        "دوام", "working hours", "shift", "وقت العمل", "ساعات العمل",
        "full time", "part time", "دوام كامل", "دوام جزئي",
    ])
    tm = _TIME_RANGE_RE.search(normalized_digits_text)
    if tm:
        h1, m1, mer1, h2, m2, mer2 = tm.groups()
        # Require explicit time indicators OR work context
        has_time_fmt = bool(m1 or m2 or mer1 or mer2 or ":" in tm.group(0))
        if not (has_time_fmt or work_context):
            tm = None  # reject: likely phone/salary/other numbers
        # Reject if looks like phone fragment (starts with 07)
        if tm and tm.group(0).strip().startswith("07"):
            tm = None
    if tm:
        if _valid_time_component(int(h1), m1) and _valid_time_component(int(h2), m2):
            comp.work_start = _to_24h(int(h1), m1, mer1 or mer2)
            comp.work_end = _to_24h(int(h2), m2, mer2 or mer1)
            try:
                start_hour = int(comp.work_start.split(":")[0])
                end_hour = int(comp.work_end.split(":")[0])
                span = (end_hour - start_hour) % 24
                comp.part_time = span <= 6
            except (ValueError, IndexError):
                pass

    return comp


def extract_contacts(raw_text: str) -> ContactInfo:
    emails = list(dict.fromkeys(_EMAIL_RE.findall(raw_text)))
    linkedin = list(dict.fromkeys(_LINKEDIN_URL_RE.findall(raw_text)))

    normalized_digits_text = normalize_digits(raw_text)
    all_phones = list(dict.fromkeys(_PHONE_RE.findall(normalized_digits_text)))

    # WhatsApp numbers = phone-like numbers appearing near a WhatsApp mention.
    whatsapp = []
    for marker in ["whatsapp", "واتساب", "whats up", "whats-up"]:
        for m in re.finditer(re.escape(marker), normalized_digits_text, re.IGNORECASE):
            window = normalized_digits_text[max(0, m.start() - 40): m.end() + 40]
            for phone in _PHONE_RE.findall(window):
                if phone not in whatsapp:
                    whatsapp.append(phone)

    phones = [p for p in all_phones if p not in whatsapp]

    telegram_handles = list(dict.fromkeys(_TELEGRAM_USERNAME_RE.findall(raw_text)))

    return ContactInfo(
        emails=emails, phones=phones, whatsapp=whatsapp,
        telegram=telegram_handles, linkedin=linkedin,
    )


def extract_company(raw_text: str, known_company_hint: Optional[str] = None) -> CompanyInfo:
    match_text = normalize_for_matching(raw_text)
    for term in _SCIENTIFIC_BUREAU_TERMS:
        if normalize_for_matching(term) in match_text:
            return CompanyInfo(name=None, employer_type="SCIENTIFIC_BUREAU")

    m = re.search(r"(?:company|Company)\s*[:\-]\s*([^\n]+)", raw_text)
    if m:
        name = m.group(1).strip()
        if name:
            return CompanyInfo(name=name, employer_type="COMPANY")

    m = re.search(r"(?:الشركة)\s*[:\-]\s*([^\n]+)", raw_text)
    if m:
        name = m.group(1).strip()
        if name:
            return CompanyInfo(name=name, employer_type="COMPANY")

    m = re.search(r"شركة\s+([\u0600-\u06FFa-zA-Z0-9\s]{3,40})", raw_text)
    if m:
        name = m.group(1).strip().split("\n")[0].strip()
        if name and len(name) > 2:
            return CompanyInfo(name="شركة " + name, employer_type="COMPANY")

    return CompanyInfo(name=None, employer_type=None)


def extract_requirements(raw_text: str) -> RequirementsInfo:
    match_text = normalize_for_matching(raw_text)
    req = RequirementsInfo()
    if "بكالوريوس صيدله" in match_text or "بكالوريوس صيدلة" in raw_text:
        req.education.append("بكالوريوس صيدلة")
    elif "بكالوريوس" in match_text:
        req.education.append("بكالوريوس")
    if "ماجستير" in match_text:
        req.education.append("ماجستير")
    if "دبلوم" in match_text:
        req.education.append("دبلوم")
    m = re.search(r"(\d+)\s*سنوات?", match_text)
    if m and any(w in match_text for w in ["خبره", "خبرة", "experience"]):
        req.experience_min_months = int(m.group(1)) * 12
        req.experience_required = True
    if "سياره" in match_text or "سيارة" in raw_text:
        req.car_required = True

    if "pharmacist" in match_text or "صيدلاني" in match_text or "صيدلانية" in match_text:
        req.education.append("PHARMACY")
    if "dentist" in match_text or "طبيب اسنان" in match_text:
        req.education.append("DENTISTRY")
    if "physician" in match_text or "طبيب" in match_text:
        req.education.append("MEDICINE")

    m = re.search(r"(\d+)\s*(?:months?|شهر|اشهر)", match_text)
    if m:
        req.experience_min_months = int(m.group(1))
        req.experience_required = "preferred" not in match_text[max(0, m.start() - 20):m.start()]
    if "experience preferred" in match_text or "خبرة مفضلة" in match_text or "يفضل خبرة" in match_text:
        req.experience_preferred = True
        req.experience_required = False
    elif "experience required" in match_text or "يشترط خبرة" in match_text:
        req.experience_required = True

    if "own a car" in match_text or "يمتلك سيارة" in match_text or "car required" in match_text:
        req.car_required = True
    elif "car preferred" in match_text or "يفضل سيارة" in match_text:
        req.car_preferred = True

    if "للجنسين" in match_text or "male or female" in match_text or "both" in match_text:
        req.gender = "both"
    elif "ذكر" in match_text or re.search(r"\bmale\b", match_text):
        req.gender = "male"
    elif "انثى" in match_text or "أنثى" in match_text or "female" in match_text:
        req.gender = "female"

    return req


def _make_date(y: int, mth: int, d: int) -> Optional[date]:
    try:
        return date(y, mth, d)
    except ValueError:
        return None


def extract_deadline(raw_text: str) -> Optional[date]:
    """Parse deadline into a date object. Returns None if not found (never guessed)."""
    normalized_text = normalize_digits(raw_text)

    for pattern, order in _DATE_PATTERNS:
        m = pattern.search(normalized_text)
        if m:
            if order == "dmy":
                d, mth, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
            else:
                y, mth, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
            parsed = _make_date(y, mth, d)
            if parsed:
                return parsed

    m = re.search(r"([A-Za-z]{3,9})\s+(\d{1,2}),?\s+(\d{4})", normalized_text)
    if m:
        month_name, day_str, year_str = m.group(1).lower(), m.group(2), m.group(3)
        month = _MONTHS.get(month_name)
        if month:
            parsed = _make_date(int(year_str), month, int(day_str))
            if parsed:
                return parsed

    return None


def is_expired(deadline: Optional[date], today: Optional[date] = None) -> Optional[bool]:
    """
    Returns True/False if a deadline exists, or None if UNKNOWN (no deadline
    found in the ad — this is NOT the same as "not expired", callers must
    treat None as EXPIRY_STATUS = UNKNOWN, not ACTIVE).
    """
    if deadline is None:
        return None
    today = today or date.today()
    return deadline < today

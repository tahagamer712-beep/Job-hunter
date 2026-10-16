"""
Direct WhatsApp link generation (wa.me), so a tapped button opens the chat
directly instead of the user having to copy/dial the number manually.

Handles Iraqi local number formats (07XXXXXXXXX) by converting to the
international form (+964 7XXXXXXXXX) that wa.me requires.
"""
from __future__ import annotations

import re
from urllib.parse import quote

IRAQ_COUNTRY_CODE = "964"


def normalize_iraqi_number(raw_number: str) -> str | None:
    """
    Returns digits-only international-format number (no +, no spaces),
    e.g. "07838299611" -> "9647838299611". Returns None if it doesn't look
    like a plausible Iraqi mobile number.
    """
    digits = re.sub(r"\D", "", raw_number)

    if digits.startswith("00964"):
        digits = digits[2:]
    if digits.startswith("964"):
        rest = digits[3:]
    elif digits.startswith("0"):
        rest = digits[1:]
    else:
        rest = digits

    if len(rest) != 10 or not rest.startswith("7"):
        return None  # not a recognizable Iraqi mobile number — don't guess

    return IRAQ_COUNTRY_CODE + rest


def build_whatsapp_link(raw_number: str, prefilled_text: str | None = None) -> str | None:
    """
    Returns a https://wa.me/... URL that opens the chat directly when tapped,
    or None if the number couldn't be confidently normalized (never guess —
    Section 25: don't invent/repair data we're not sure about).
    """
    normalized = normalize_iraqi_number(raw_number)
    if normalized is None:
        return None
    url = f"https://wa.me/{normalized}"
    if prefilled_text:
        url += f"?text={quote(prefilled_text)}"
    return url

"""
Text normalization for job ads (Arabic / English / mixed).

Pure stdlib, no network or third-party dependencies — this stage runs
identically whether the ad came from the Telegram scanner, LinkedIn, or a
manual forward.
"""
from __future__ import annotations

import re
import unicodedata

# Arabic diacritics (tashkeel) range
_ARABIC_DIACRITICS = re.compile(r"[\u064B-\u065F\u0670\u06D6-\u06ED]")

# Arabic-Indic / Eastern Arabic digits -> ASCII digits
_ARABIC_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")
_PERSIAN_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789")

# Common alef/yeh/teh-marbuta normalization for looser matching.
# Applied only inside `normalize_for_matching`, NEVER applied to raw_text
# that gets stored/displayed (Section 8: raw_text must be preserved verbatim).
_ALEF_VARIANTS = re.compile(r"[إأآا]")
_YEH_VARIANTS = re.compile(r"[يى]")
_TEH_MARBUTA = re.compile(r"ة")


def normalize_whitespace(text: str) -> str:
    """Collapse repeated whitespace/newlines, strip control chars, keep line breaks."""
    text = unicodedata.normalize("NFKC", text)
    # Normalize CRLF / stray control chars but keep single newlines for readability.
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t\u200f\u200e]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def strip_diacritics(text: str) -> str:
    return _ARABIC_DIACRITICS.sub("", text)


def normalize_digits(text: str) -> str:
    return text.translate(_ARABIC_DIGITS).translate(_PERSIAN_DIGITS)


def normalize_for_matching(text: str) -> str:
    """
    Aggressive normalization used ONLY for classification/matching
    (role detection, location detection, duplicate hashing).
    Never use this output as `raw_text` for storage/display.
    """
    text = normalize_whitespace(text)
    text = strip_diacritics(text)
    text = normalize_digits(text)
    text = _ALEF_VARIANTS.sub("ا", text)
    text = _YEH_VARIANTS.sub("ي", text)
    text = _TEH_MARBUTA.sub("ه", text)
    text = text.lower()
    return text


def detect_language(text: str) -> str:
    """Rough language detection: arabic / english / mixed."""
    arabic_chars = len(re.findall(r"[\u0600-\u06FF]", text))
    latin_chars = len(re.findall(r"[A-Za-z]", text))
    if arabic_chars == 0 and latin_chars == 0:
        return "unknown"
    if arabic_chars > 0 and latin_chars > 0:
        # Only call it "mixed" if both scripts have meaningful presence.
        minority = min(arabic_chars, latin_chars)
        total = arabic_chars + latin_chars
        if minority / total > 0.15:
            return "mixed"
    return "arabic" if arabic_chars >= latin_chars else "english"


class NormalizedPost:
    """Output of the normalization stage, fed into the rest of the pipeline."""

    def __init__(self, raw_text: str, source: dict):
        self.raw_text = raw_text                      # verbatim, never altered
        self.display_text = normalize_whitespace(raw_text)  # whitespace-cleaned only
        self.match_text = normalize_for_matching(raw_text)  # for classification
        self.language = detect_language(raw_text)
        self.source = source                            # dict: platform, channel, message_id, url, date

    def __repr__(self):
        return f"<NormalizedPost lang={self.language} len={len(self.raw_text)}>"

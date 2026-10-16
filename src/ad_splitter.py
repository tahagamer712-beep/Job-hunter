"""
Multi-ad message splitter.

Real Telegram channels frequently batch several job ads into ONE message,
separated by a repeating banner line (e.g. "❍✦ —— DNAIRAQ™ ••••••"). If we
feed the whole batched message into the pipeline as a single "ad", role/
location extraction gets confused (mixed roles, mixed cities, wrong company
attribution). This module splits a raw message into individual ad chunks
BEFORE normalization, so each ad is classified independently — this is
required for both Manual Forward Intake and the Telegram scanner.
"""
from __future__ import annotations

import re
from typing import List

# Lines that are pure decoration/branding, not part of any ad's content.
# Matched against a whole line (after stripping whitespace).
_SEPARATOR_LINE_PATTERNS = [
    r"^[❍✦•\-—–\s]{6,}.*(DNAIRAQ|™).*[❍✦•\-—–\s]*$",   # "❍✦ —— DNAIRAQ™ ••••••"
    r"^[❍✦•\-—–]{4,}$",                                   # bare decorative separator lines
    r"^[=~_*]{6,}$",                                        # markdown-style dividers
]
_SEPARATOR_RE = re.compile("|".join(_SEPARATOR_LINE_PATTERNS), re.IGNORECASE)

MIN_AD_LENGTH_CHARS = 25  # drop fragments too short to be a real ad (stray banner remnants)


def split_ads(raw_message: str) -> List[str]:
    """
    Returns a list of individual ad texts. If no separator is found, returns
    a single-item list containing the original message unchanged (the common
    case — most forwarded/manual ads are already single ads).
    """
    lines = raw_message.split("\n")
    chunks: List[List[str]] = [[]]

    for line in lines:
        if _SEPARATOR_RE.match(line.strip()):
            chunks.append([])
            continue
        chunks[-1].append(line)

    ads = []
    for chunk_lines in chunks:
        text = "\n".join(chunk_lines).strip()
        text = re.sub(r"\n{3,}", "\n\n", text)
        if len(text) >= MIN_AD_LENGTH_CHARS:
            ads.append(text)

    return ads if ads else [raw_message.strip()]

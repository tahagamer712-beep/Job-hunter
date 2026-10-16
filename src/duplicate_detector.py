"""
Duplicate / near-duplicate detection (Master Prompt Section 9).

Exact duplicates: normalized-text hash comparison.
Near-duplicates: token-overlap similarity (Jaccard on word shingles) — no
external ML dependency needed, keeps this deterministic and testable offline.
"""
from __future__ import annotations

import hashlib
import re
from typing import Iterable, List, Tuple

from .normalizer import normalize_for_matching

NEAR_DUPLICATE_THRESHOLD = 0.35  # word-level Jaccard; tune against real data

_STOPWORDS = {
    "the", "a", "an", "in", "at", "on", "for", "to", "of", "and", "with",
    "needed", "required", "now", "today", "apply", "please", "send",
    "في", "الى", "إلى", "و", "من", "على", "مطلوب", "للعمل", "يرجى", "ارسال",
}


def text_hash(raw_text: str) -> str:
    normalized = normalize_for_matching(raw_text)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _word_tokens(text: str) -> set:
    normalized = normalize_for_matching(text)
    normalized = re.sub(r"[^\w\s]", " ", normalized)
    return {t for t in normalized.split() if t and t not in _STOPWORDS and len(t) > 1}


def similarity(text_a: str, text_b: str) -> float:
    """
    Word-level Jaccard similarity, stopword-filtered. This intentionally does
    NOT require phrase adjacency, unlike n-gram shingles — reworded ads (e.g.
    "Medical Representative - Russafa" vs "Medical Rep needed in Baghdad
    Russafa") share individual words even when sentence structure changes.

    KNOWN LIMITATION: pure lexical overlap still misses synonym pairs like
    "Rep" vs "Representative" with no shared string. A production system
    should extend this with a small synonym/abbreviation map (rep<->
    representative, mr<->medical representative, etc.) or combine this score
    with secondary signals such as matching extracted company name or
    contact info (email/phone) from extractors.py, which are much stronger
    duplicate signals than word overlap alone when available.
    """
    a, b = _word_tokens(text_a), _word_tokens(text_b)
    if not a or not b:
        return 0.0
    intersection = len(a & b)
    union = len(a | b)
    return intersection / union if union else 0.0


def find_duplicate(raw_text: str, existing_jobs: Iterable[Tuple[str, str]]) -> str | None:
    """
    existing_jobs: iterable of (job_id, raw_text) for already-stored jobs.
    Returns the matching job_id if this is an exact or near-duplicate, else None.
    """
    new_hash = text_hash(raw_text)
    near_matches: List[Tuple[str, float]] = []

    for job_id, existing_text in existing_jobs:
        if text_hash(existing_text) == new_hash:
            return job_id
        sim = similarity(raw_text, existing_text)
        if sim >= NEAR_DUPLICATE_THRESHOLD:
            near_matches.append((job_id, sim))

    if near_matches:
        near_matches.sort(key=lambda x: x[1], reverse=True)
        return near_matches[0][0]

    return None

# -*- coding: utf-8 -*-
"""
Multi-job splitter: detect and split multiple jobs in one message.
Each split part is classified independently.
"""
import re


def split_jobs(raw_text: str) -> list[str]:
    """
    Split a message into potential job segments.
    Returns list of segments (1 = single job, >1 = multiple).
    """
    text = raw_text.strip()
    if not text:
        return []

    # Pattern 1: Numbered lists (1. 2. 3. or ١. ٢. ٣.)
    numbered = re.split(r'\n\s*(?:\d+[\.\)\-]\s*|[١٢٣٤٥٦٧٨٩٠]+[\.\)\-]\s*)', '\n' + text)
    numbered = [s.strip() for s in numbered if s.strip() and len(s.strip()) > 40]
    if len(numbered) >= 2:
        return numbered

    # Pattern 2: Bullet points
    bullets = re.split(r'\n\s*[•\-\*▪]\s*', '\n' + text)
    bullets = [s.strip() for s in bullets if s.strip() and len(s.strip()) > 40]
    if len(bullets) >= 2:
        # Only split if bullets look like jobs (contain job keywords)
        job_kw = sum(1 for b in bullets if any(w in b for w in
                      ['مطلوب', 'وظيفة', 'راتب', 'خبرة', 'job', 'salary', 'hiring']))
        if job_kw >= 2:
            return bullets

    # Pattern 3: Multiple "مطلوب" occurrences (separate paragraphs)
    parts = re.split(r'\n(?=مطلوب|مطلوبة|WANTED|VACANCY)', text, flags=re.IGNORECASE)
    parts = [s.strip() for s in parts if s.strip() and len(s.strip()) > 40]
    if len(parts) >= 2:
        return parts

    # Pattern 4: Double newline separated blocks, each with job signals
    blocks = [b.strip() for b in text.split('\n\n') if b.strip() and len(b.strip()) > 60]
    if len(blocks) >= 2:
        job_blocks = [b for b in blocks if any(w in b for w in
                       ['مطلوب', 'وظيفة', 'راتب', 'شركة', 'job', 'company', 'hiring'])]
        if len(job_blocks) >= 2:
            return job_blocks

    return [text]


def count_jobs_in_text(raw_text: str, classify_fn) -> tuple[int, list]:
    """
    Split and classify each segment.
    Returns (job_count, [(segment, category), ...]).
    classify_fn: function(text) -> category or None
    """
    segments = split_jobs(raw_text)
    results = []
    for seg in segments:
        cat = classify_fn(seg)
        if cat:
            results.append((seg, cat))
    return len(results), results

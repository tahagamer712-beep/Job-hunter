# -*- coding: utf-8 -*-
"""Multi-job splitter v2: fixed patterns."""
import re


def split_jobs(raw_text: str) -> list[str]:
    text = raw_text.strip()
    if not text:
        return []

    # Pattern 1: Numbered items on separate lines
    lines = text.split('\n')
    numbered = []
    current = []
    for line in lines:
        ls = line.strip()
        if re.match(r'^(\d+[\.\)\-]\s*|[١٢٣٤٥٦٧٨٩٠]+[\.\)]\s*)', ls):
            if current:
                numbered.append('\n'.join(current))
            current = [re.sub(r'^(\d+[\.\)\-]\s*|[١٢٣٤٥٦٧٨٩٠]+[\.\)]\s*)', '', ls)]
        elif current:
            current.append(ls)
    if current:
        numbered.append('\n'.join(current))
    numbered = [s.strip() for s in numbered if len(s.strip()) > 15]
    if len(numbered) >= 2:
        return numbered

    # Pattern 2: Multiple مطلوب blocks
    parts = re.split(r'\n(?=مطلوب)', text)
    parts = [s.strip() for s in parts if len(s.strip()) > 15]
    if len(parts) >= 2:
        return parts

    # Pattern 3: Double-newline blocks with job signals
    blocks = [b.strip() for b in text.split('\n\n') if len(b.strip()) > 50]
    if len(blocks) >= 2:
        jb = [b for b in blocks if any(w in b for w in
              ['مطلوب', 'وظيفة', 'راتب', 'شركة', 'job', 'hiring'])]
        if len(jb) >= 2:
            return jb

    return [text]


def count_jobs_in_text(raw_text: str, classify_fn) -> tuple[int, list]:
    segments = split_jobs(raw_text)
    results = []
    for seg in segments:
        cat = classify_fn(seg)
        if cat:
            results.append((seg, cat))
    return len(results), results

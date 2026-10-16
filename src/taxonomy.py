"""
Job Taxonomy (multi-user redesign).

Unlike the original fixed RoleCategory enum (MEDICAL_REPRESENTATIVE,
GENERAL_SALES, ...), job categories are now admin-defined and open-ended —
the owner explicitly asked to be able to add job types beyond Medical
Representative. Structure:

  - job_categories: canonical categories the admin has approved
  - category_aliases: alternate names/spellings that resolve to a category
    (e.g. "تواجد", "صيدلي تواجد", "sub staff", "substaff" -> one category)
  - pending_job_titles: titles seen in real ads that matched NO known alias —
    queued for the admin to resolve as (a) a new category, (b) an alias of
    an existing category, or (c) rejected/ignored. The admin has final say;
    nothing here auto-creates a category on its own.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import List, Optional

from .normalizer import normalize_for_matching


@dataclass
class JobCategory:
    id: int
    canonical_name: str
    aliases: List[str] = field(default_factory=list)


@dataclass
class PendingTitle:
    id: int
    raw_title: str
    source_job_id: Optional[int]
    status: str  # "pending" | "resolved_new_category" | "resolved_alias" | "rejected"
    created_at: str


# Patterns that disqualify a text from being a job ad at all.
# Checked (normalized) before any category matching.
_EXCLUSION_PATTERNS = [
    "كورس", "دوره تدريبيه", "دورة تدريبية",
    "اعلان تسويقي", "إعلان تسويقي",
    "خصم", "عرض خاص", "تخفيضات",
    "منحه دراسيه", "منحة دراسية", "للبيع", "for sale",
]

class TaxonomyStore:
    """
    Thin wrapper around Database's taxonomy tables (see database.py additions).
    Kept as a separate module so the matching/classification logic doesn't
    need to know SQL — it just calls these methods.
    """

    def __init__(self, db):
        self.db = db

    # --- Category management (admin actions) ---

    def create_category(self, canonical_name: str, initial_aliases: Optional[List[str]] = None) -> int:
        category_id = self.db.create_job_category(canonical_name)
        for alias in (initial_aliases or []) + [canonical_name]:
            self.db.add_category_alias(category_id, alias)
        return category_id

    def add_alias(self, category_id: int, alias: str):
        self.db.add_category_alias(category_id, alias)

    def list_categories(self) -> List[JobCategory]:
        rows = self.db.list_job_categories()
        result = []
        for row in rows:
            aliases = self.db.list_category_aliases(row["id"])
            result.append(JobCategory(id=row["id"], canonical_name=row["canonical_name"], aliases=aliases))
        return result

    def delete_category(self, category_id: int):
        self.db.delete_job_category(category_id)

    # --- Classification against the taxonomy ---

    def match_category(self, raw_text: str) -> Optional[JobCategory]:
        """
        Returns the category with the LONGEST matching alias in raw_text.
        Longest-match wins so "assistant pharmacist" beats "pharmacist".
        Returns None if nothing matches (caller queues a pending title).
        """
        match_text = normalize_for_matching(raw_text)
        # Exclusion: course/training/marketing ads are not jobs
        for excl in _EXCLUSION_PATTERNS:
            if excl in match_text:
                return None
        best = None
        best_len = 0
        for category in self.list_categories():
            for alias in category.aliases:
                alias_norm = normalize_for_matching(alias).strip()
                if alias_norm and alias_norm in match_text:
                    if len(alias_norm) > best_len:
                        best_len = len(alias_norm)
                        best = category
        return best

    # --- Pending title triage (admin queue) ---

    def queue_pending_title(self, raw_title: str, source_job_id: Optional[int] = None) -> int:
        return self.db.add_pending_job_title(raw_title, source_job_id)

    def list_pending_titles(self) -> List[dict]:
        return self.db.list_pending_job_titles()

    def resolve_as_new_category(self, pending_id: int, canonical_name: str) -> int:
        pending = self.db.get_pending_job_title(pending_id)
        category_id = self.create_category(canonical_name, initial_aliases=[pending["raw_title"]])
        self.db.resolve_pending_job_title(pending_id, "resolved_new_category")
        return category_id

    def resolve_as_alias(self, pending_id: int, category_id: int):
        pending = self.db.get_pending_job_title(pending_id)
        self.add_alias(category_id, pending["raw_title"])
        self.db.resolve_pending_job_title(pending_id, "resolved_alias")

    def reject_pending_title(self, pending_id: int):
        self.db.resolve_pending_job_title(pending_id, "rejected")

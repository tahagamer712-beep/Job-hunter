"""
Archive reprocessing (owner request: "🔄 إعادة فحص الأرشيف"). When the admin
adds/changes a job category or reorganizes the location tree, previously
stored jobs don't automatically re-classify — this re-runs classification
against stored raw_text and re-evaluates delivery to users, WITHOUT ever
double-notifying someone who already got a delivery for that job.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List

from .location_tree import LocationTree
from .matching_engine import find_matching_users
from .taxonomy import TaxonomyStore
from .user_profiles import UserProfileStore


@dataclass
class ReprocessResult:
    jobs_reprocessed: int
    new_deliveries: int


def reprocess_archive(db, notify_fn=None) -> ReprocessResult:
    """
    notify_fn: optional callable(telegram_user_id, job_row) — called for
    each NEW match found (i.e. not already in job_deliveries). In the bot,
    this is wired to actually send the Telegram message; in tests, it's
    just recorded.
    """
    taxonomy = TaxonomyStore(db)
    profiles_store = UserProfileStore(db)
    tree = LocationTree()

    all_profiles = profiles_store.list_all()
    jobs_reprocessed = 0
    new_deliveries = 0

    with db._conn() as conn:
        rows = conn.execute("SELECT * FROM jobs").fetchall()

    for row in rows:
        jobs_reprocessed += 1
        category = taxonomy.match_category(row["raw_text"])
        if category is None:
            continue  # still unclassified — stays out of delivery until admin resolves it

        job_area_ids = tree.match_ids(row["raw_text"])
        if row["category_id"] != category.id:
            db.set_job_category(row["id"], category.id)
        db.set_job_detected_areas(row["id"], list(job_area_ids))
        if not job_area_ids:
            continue

        matches = find_matching_users(category.id, job_area_ids, all_profiles, tree)
        for match in matches:
            if db.has_been_delivered(row["id"], match.telegram_user_id):
                continue
            if db.is_company_ignored_by_user(match.telegram_user_id, row["company_name"]):
                continue
            db.record_delivery(row["id"], match.telegram_user_id, match_strength=match.match_strength)
            new_deliveries += 1
            if notify_fn:
                try:
                    notify_fn(match.telegram_user_id, row)
                    db.mark_delivery_sent(row["id"], match.telegram_user_id)
                except Exception:
                    pass  # stays PENDING — safe to retry on a future reprocess

    return ReprocessResult(jobs_reprocessed=jobs_reprocessed, new_deliveries=new_deliveries)

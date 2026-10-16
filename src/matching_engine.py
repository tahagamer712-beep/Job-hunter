"""
Per-user matching engine (multi-user redesign). A job is delivered to a
user if BOTH:
  - the job's classified category is in the user's selected categories
  - at least one of the job's detected verified area(s) is_related() to at
    least one of the user's selected area node(s) (same node, ancestor, or
    descendant — Section: location_tree.LocationTree.is_related)

No global GREEN/RED decision is stored per job anymore — relevance is
always computed relative to a specific user's preferences.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Set

from .location_tree import LocationTree


def _tree_distance(node_a: str, node_b: str, tree: LocationTree) -> int:
    """Number of tree hops between two related nodes (0 if identical)."""
    if node_a == node_b:
        return 0
    ancestors_a = tree.ancestors(node_a)
    if node_b in ancestors_a:
        return ancestors_a.index(node_b) + 1
    ancestors_b = tree.ancestors(node_b)
    if node_a in ancestors_b:
        return ancestors_b.index(node_a) + 1
    return 99  # unrelated — caller should not reach this for actual matches


def compute_match_strength(job_area_ids: Set[str], user_area_ids: List[str], tree: LocationTree) -> int:
    """
    Simple, explainable match percentage for display (Section: owner asked
    for a visible 'درجة التطابق' / match score per job card). Exact area
    match = 100%. Each tree hop of distance (parent/child, grandparent/
    grandchild, ...) reduces the score, floored at 50% for any confirmed
    match at all (this function is only meaningful when a match already
    exists — see matching_engine.user_matches_job).
    """
    best_distance = 99
    for job_area in job_area_ids:
        for user_area in user_area_ids:
            if tree.is_related(job_area, user_area):
                best_distance = min(best_distance, _tree_distance(job_area, user_area, tree))
    if best_distance == 99:
        return 0
    return max(50, 100 - best_distance * 15)


@dataclass
class MatchResult:
    telegram_user_id: str
    matched_areas: Set[str]
    match_strength: int = 100


def user_matches_job(user_category_ids: List[int], user_area_ids: List[str],
                      job_category_id: int, job_area_ids: Set[str],
                      tree: LocationTree) -> bool:
    if job_category_id not in user_category_ids:
        return False
    if not job_area_ids or not user_area_ids:
        return False
    return any(
        tree.is_related(job_area, user_area)
        for job_area in job_area_ids
        for user_area in user_area_ids
    )


def find_matching_users(job_category_id: int, job_area_ids: Set[str],
                          all_user_profiles: list, tree: LocationTree) -> List[MatchResult]:
    """
    all_user_profiles: list of UserProfile (src/user_profiles.py). Returns
    the subset that should be notified about this job, with which of their
    areas actually matched (useful for the "🤔 ليش وصلتني هذي؟" explanation).
    """
    matches = []
    for profile in all_user_profiles:
        if job_category_id not in profile.job_category_ids:
            continue
        # Area logic:
        # - User has no area prefs -> match on category (no area filter)
        # - Job has no area -> match on category (can't filter)
        # - Both have areas -> require overlap
        if not profile.area_node_ids or not job_area_ids:
            matched_areas = set()  # no area constraint
        else:
            matched_areas = {
                user_area for user_area in profile.area_node_ids
                for job_area in job_area_ids
                if tree.is_related(job_area, user_area)
            }
            if not matched_areas:
                continue  # explicit area prefs don't overlap -> skip
        # Category matched (checked above); area is boost not gate
            strength = compute_match_strength(job_area_ids, list(matched_areas), tree)
            matches.append(MatchResult(telegram_user_id=profile.telegram_user_id,
                                        matched_areas=matched_areas, match_strength=strength))
    return matches

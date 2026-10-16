"""
Feedback insights (Master Prompt Section 55). Reads the `decision_feedback`
log and surfaces plain suggestions for the owner to act on manually. This
module NEVER changes role_accept_categories, the Location Knowledge Base, or
any other core rule on its own — Section 55 is explicit that the AI/system
must not mutate base rules by itself. It only produces text the owner reads
and decides on.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List

REJECTION_RATE_SUGGESTION_THRESHOLD = 0.8   # e.g. rejected 80%+ of the time
MIN_SAMPLES_FOR_SUGGESTION = 3              # don't suggest from one or two data points


@dataclass
class FeedbackSuggestion:
    role_normalized: str
    accepted: int
    rejected: int

    @property
    def total(self) -> int:
        return self.accepted + self.rejected

    @property
    def rejection_rate(self) -> float:
        return self.rejected / self.total if self.total else 0.0


def build_suggestions(feedback_stats: List[dict]) -> List[str]:
    """
    feedback_stats: the output of Database.feedback_stats() — rows of
    {role_normalized, user_decision, n}. Returns human-readable suggestion
    strings, or an empty list if there isn't enough data yet.
    """
    by_role: dict[str, FeedbackSuggestion] = {}
    for row in feedback_stats:
        role = row["role_normalized"] or "UNKNOWN"
        entry = by_role.setdefault(role, FeedbackSuggestion(role, 0, 0))
        if row["user_decision"] == "ACCEPTED":
            entry.accepted += row["n"]
        elif row["user_decision"] == "REJECTED":
            entry.rejected += row["n"]

    suggestions = []
    for entry in by_role.values():
        if entry.total < MIN_SAMPLES_FOR_SUGGESTION:
            continue
        if entry.rejection_rate >= REJECTION_RATE_SUGGESTION_THRESHOLD:
            suggestions.append(
                f"رفضت {entry.rejected} من أصل {entry.total} إعلان بفئة "
                f"{entry.role_normalized} — قد ترغب بتعطيل هذه الفئة من الإعدادات "
                f"(/toggleRole {entry.role_normalized})."
            )
        elif entry.rejection_rate <= (1 - REJECTION_RATE_SUGGESTION_THRESHOLD) and entry.accepted >= MIN_SAMPLES_FOR_SUGGESTION:
            suggestions.append(
                f"قبلت {entry.accepted} من أصل {entry.total} إعلان بفئة "
                f"{entry.role_normalized} بشكل شبه دائم — تبدو فئة موثوقة لك."
            )

    return suggestions

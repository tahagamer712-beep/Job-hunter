"""
Decision Engine — combines Role, Location, and Expiry into the four
separate axes required by Master Prompt Section 11:

    ROLE_DECISION      GREEN / YELLOW / RED
    LOCATION_DECISION  GREEN / YELLOW / RED
    EXPIRY_STATUS       ACTIVE / EXPIRED / UNKNOWN
    FINAL_DECISION      GREEN / YELLOW / RED / EXPIRED

These are computed here and never collapsed into a single score by any
upstream module — see Governing Resolution Rule 3.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import Enum
from typing import Optional

from .extractors import is_expired
from .location_engine import LocationDecision, LocationResult
from .role_classifier import RoleDecision, RoleResult


class ExpiryStatus(str, Enum):
    ACTIVE = "ACTIVE"
    EXPIRED = "EXPIRED"
    UNKNOWN = "UNKNOWN"


class FinalDecision(str, Enum):
    GREEN = "GREEN"
    YELLOW = "YELLOW"
    RED = "RED"
    EXPIRED = "EXPIRED"


@dataclass
class DecisionResult:
    role_decision: RoleDecision
    location_decision: LocationDecision
    expiry_status: ExpiryStatus
    final_decision: FinalDecision
    reason: str
    match_score: int


def compute_expiry_status(deadline: Optional[date], today: Optional[date] = None) -> ExpiryStatus:
    expired = is_expired(deadline, today)
    if expired is None:
        return ExpiryStatus.UNKNOWN
    return ExpiryStatus.EXPIRED if expired else ExpiryStatus.ACTIVE


def compute_match_score(role_result: RoleResult, location_result: LocationResult,
                         has_contact: bool = False, pharmacist_required: bool = False,
                         has_relevant_experience: bool = False) -> int:
    score = 0

    if role_result.role_normalized.value == "MEDICAL_REPRESENTATIVE":
        score += 30
    elif role_result.decision == RoleDecision.RED:
        score -= 50

    if location_result.baghdad_mentioned or location_result.city_mentioned:
        score += 15
    if location_result.decision == LocationDecision.GREEN:
        score += 30
        if location_result.rusafa_matches and location_result.rusafa_matches[0].confidence >= 0.9:
            score += 15

    if location_result.karkh_matches and not location_result.rusafa_matches:
        score -= 100
    if not location_result.city_mentioned:
        score -= 100

    if pharmacist_required:
        score += 5
    if has_relevant_experience:
        score += 3
    if has_contact:
        score += 2

    return score


def decide(role_result: RoleResult, location_result: LocationResult,
           deadline: Optional[date] = None, today: Optional[date] = None,
           has_contact: bool = False) -> DecisionResult:

    expiry_status = compute_expiry_status(deadline, today)
    score = compute_match_score(role_result, location_result, has_contact=has_contact)

    # --- RED short-circuit: role or location hard-rejected ---
    if role_result.decision == RoleDecision.RED or location_result.decision == LocationDecision.RED:
        reasons = []
        if role_result.decision == RoleDecision.RED:
            reasons.append(f"role: {role_result.reason}")
        if location_result.decision == LocationDecision.RED:
            reasons.append(f"location: {location_result.reason}")
        return DecisionResult(
            role_decision=role_result.decision,
            location_decision=location_result.decision,
            expiry_status=expiry_status,
            final_decision=FinalDecision.RED,
            reason="; ".join(reasons),
            match_score=score,
        )

    # --- Both role and location are GREEN-or-YELLOW from here on ---
    if role_result.decision == RoleDecision.GREEN and location_result.decision == LocationDecision.GREEN:
        if expiry_status == ExpiryStatus.EXPIRED:
            return DecisionResult(
                role_decision=role_result.decision,
                location_decision=location_result.decision,
                expiry_status=expiry_status,
                final_decision=FinalDecision.EXPIRED,
                reason="Role and location match, but the ad's deadline has passed.",
                match_score=score,
            )
        return DecisionResult(
            role_decision=role_result.decision,
            location_decision=location_result.decision,
            expiry_status=expiry_status,
            final_decision=FinalDecision.GREEN,
            reason="Role and location both confirmed.",
            match_score=score,
        )

    # --- Anything else with no RED (i.e. some YELLOW involved) -> YELLOW/review ---
    reasons = []
    if role_result.decision == RoleDecision.YELLOW:
        reasons.append(f"role: {role_result.reason}")
    if location_result.decision == LocationDecision.YELLOW:
        reasons.append(f"location: {location_result.reason}")
    return DecisionResult(
        role_decision=role_result.decision,
        location_decision=location_result.decision,
        expiry_status=expiry_status,
        final_decision=FinalDecision.YELLOW,
        reason="; ".join(reasons) or "Needs manual review.",
        match_score=score,
    )

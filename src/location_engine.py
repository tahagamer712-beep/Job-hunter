"""
Location Engine — the single most safety-critical module in Job Hunter.

Golden rules enforced here (see Master Prompt Section 3):
  1. "Baghdad" alone is NEVER auto-classified as Rusafa.
  2. Karkh Exclusion Gate is deterministic code, evaluated before/independent
     of any AI opinion, and cannot be overridden by it.
  3. Only entries with status == "verified" in the Location Knowledge Base
     can produce a GREEN Rusafa match. Everything else is AMBIGUOUS -> YELLOW.
  4. An ad naming both a Rusafa-side and a Karkh-side area is PARTIAL, not
     force-resolved to a single side.

This module has zero network or AI dependency — it is pure deterministic
logic over the ad text and the Location Knowledge Base, by design, so its
behavior is fully covered by the fixture test suite.

KNOWN LIMITATION (documented deliberately, not silently): alias matching here
is substring/word-boundary based, with no semantic context awareness. If an
alias word (e.g. "Rusafa") appears ANYWHERE in the ad text — including inside
an unrelated sentence, a quoted instruction, someone's name, or adversarial
injected text — it will register as a location signal. This is a distinct
risk from prompt injection (Section 13/47): prompt injection is about text
trying to change the AI's *behavior*; this is about the deterministic engine's
own keyword matching being too literal. Mitigations to consider in a future
iteration: proximity/window-based extraction (as already used for WhatsApp
number extraction in extractors.py), or cross-checking against the AI
classifier's location opinion when the two deterministic axes (role vs
location) disagree sharply. Until then, be aware that adversarial ads
containing real alias words in unrelated context can still produce a
misleading LocationMatch — treat any GREEN location result on suspicious text
with the same scrutiny as the role classifier's disagreement-handling in
role_classifier.CombinedRoleClassifier.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import List

from .normalizer import normalize_for_matching

DEFAULT_LOCATIONS_PATH = Path(__file__).resolve().parent.parent / "data" / "baghdad_locations.json"

BAGHDAD_ALIASES = ["baghdad", "بغداد"]


class LocationDecision(str, Enum):
    GREEN = "GREEN"        # confirmed Rusafa
    RED = "RED"             # confirmed NOT Rusafa (Karkh / outside Baghdad)
    YELLOW = "YELLOW"       # Baghdad mentioned, side unknown/ambiguous, or PARTIAL


@dataclass
class LocationMatch:
    area_name_en: str
    area_name_ar: str
    side: str               # "RUSAFA" | "KARKH"
    confidence: float
    status: str              # "verified" | "likely" | "ambiguous" | "unknown"
    matched_alias: str


@dataclass
class LocationResult:
    decision: LocationDecision
    city_mentioned: bool
    baghdad_mentioned: bool
    rusafa_matches: List[LocationMatch] = field(default_factory=list)
    karkh_matches: List[LocationMatch] = field(default_factory=list)
    is_partial: bool = False
    reason: str = ""


class LocationEngine:
    def __init__(self, locations_path: Path = DEFAULT_LOCATIONS_PATH):
        self.locations_path = locations_path
        self._areas = self._load(locations_path)

    @staticmethod
    def _load(path: Path) -> list:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data.get("areas", [])

    def reload(self):
        """Admin panel calls this after editing the Location Knowledge Base."""
        self._areas = self._load(self.locations_path)

    def _find_area_matches(self, match_text: str) -> List[LocationMatch]:
        found = []
        for area in self._areas:
            for alias in area.get("aliases", []):
                alias_norm = normalize_for_matching(alias)
                if not alias_norm:
                    continue
                if self._alias_present(alias_norm, match_text):
                    found.append(LocationMatch(
                        area_name_en=area["name_en"],
                        area_name_ar=area["name_ar"],
                        side=area["side"],
                        confidence=area.get("confidence", 0.5),
                        status=area.get("status", "unknown"),
                        matched_alias=alias,
                    ))
                    break  # one match per area is enough
        return found

    @staticmethod
    def _alias_present(alias_norm: str, match_text: str) -> bool:
        # Use word-boundary-ish matching for Latin text; substring for Arabic
        # (Arabic script has no reliable \b word boundaries in re with this data).
        if re.fullmatch(r"[a-z0-9\s\-]+", alias_norm):
            pattern = r"(?<![a-z0-9])" + re.escape(alias_norm) + r"(?![a-z0-9])"
            return re.search(pattern, match_text) is not None
        return alias_norm in match_text

    def evaluate(self, raw_text: str) -> LocationResult:
        match_text = normalize_for_matching(raw_text)

        baghdad_mentioned = any(self._alias_present(b, match_text) for b in BAGHDAD_ALIASES)

        all_matches = self._find_area_matches(match_text)
        rusafa_matches = [m for m in all_matches if m.side == "RUSAFA" and m.status == "verified"]
        karkh_matches = [m for m in all_matches if m.side == "KARKH" and m.status == "verified"]
        ambiguous_matches = [m for m in all_matches if m.status != "verified"]

        city_mentioned = baghdad_mentioned or bool(all_matches)

        # --- Rule 1: Karkh Exclusion Gate (highest precedence, deterministic) ---
        if karkh_matches and not rusafa_matches:
            return LocationResult(
                decision=LocationDecision.RED,
                city_mentioned=city_mentioned,
                baghdad_mentioned=baghdad_mentioned,
                rusafa_matches=rusafa_matches,
                karkh_matches=karkh_matches,
                reason=f"Karkh Exclusion Gate triggered by: {karkh_matches[0].matched_alias}",
            )

        # --- Rule 2: mixed Rusafa + Karkh -> PARTIAL / YELLOW, never forced ---
        if karkh_matches and rusafa_matches:
            return LocationResult(
                decision=LocationDecision.YELLOW,
                city_mentioned=city_mentioned,
                baghdad_mentioned=baghdad_mentioned,
                rusafa_matches=rusafa_matches,
                karkh_matches=karkh_matches,
                is_partial=True,
                reason="Ad references both a verified Rusafa area and a verified Karkh area.",
            )

        # --- Rule 3: verified Rusafa match, no Karkh -> GREEN ---
        if rusafa_matches:
            return LocationResult(
                decision=LocationDecision.GREEN,
                city_mentioned=city_mentioned,
                baghdad_mentioned=baghdad_mentioned,
                rusafa_matches=rusafa_matches,
                karkh_matches=karkh_matches,
                reason=f"Verified Rusafa match: {rusafa_matches[0].matched_alias}",
            )

        # --- Rule 4: no verified match at all, no Baghdad/city mention -> RED (out of scope) ---
        if not city_mentioned:
            return LocationResult(
                decision=LocationDecision.RED,
                city_mentioned=False,
                baghdad_mentioned=False,
                reason="No Baghdad or known area mentioned — outside scope.",
            )

        # --- Rule 5: Baghdad/city mentioned but no verified side -> YELLOW (never GREEN) ---
        reason = "Baghdad mentioned but side not verified — requires manual review."
        if ambiguous_matches:
            reason = (
                f"Ambiguous/unverified area mentioned "
                f"('{ambiguous_matches[0].matched_alias}') — requires manual review "
                f"before it can be promoted to a verified Rusafa/Karkh entry."
            )
        return LocationResult(
            decision=LocationDecision.YELLOW,
            city_mentioned=city_mentioned,
            baghdad_mentioned=baghdad_mentioned,
            rusafa_matches=rusafa_matches,
            karkh_matches=karkh_matches,
            reason=reason,
        )

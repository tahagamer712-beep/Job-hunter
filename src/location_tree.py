"""
Location Tree Engine (multi-user redesign).

IMPORTANT ARCHITECTURE CHANGE from the original single-owner design: there is
no longer a global "Karkh = always rejected" rule baked in here. Region
acceptability is now a per-user preference (see src/matching_engine.py) —
this module's job is ONLY to detect which known area(s) an ad mentions, with
their full ancestor path, and to answer "does area A relate to area B"
(same node, ancestor, or descendant). It does not decide accept/reject for
anyone.

The Karkh Exclusion Gate from the original single-owner mode is preserved
as an OPTIONAL default users can still choose (e.g. a user simply never
selects any Karkh-side node), but nothing here enforces it globally anymore.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set

from .normalizer import normalize_for_matching

DEFAULT_TREE_PATH = Path(__file__).resolve().parent.parent / "data" / "location_tree.json"

BAGHDAD_ALIASES = ["baghdad", "بغداد"]


@dataclass
class AreaNode:
    id: str
    parent_id: Optional[str]
    name_ar: str
    name_en: str
    aliases: List[str]
    status: str
    confidence: float = 0.5


@dataclass
class AreaMatch:
    node: AreaNode
    matched_alias: str


@dataclass
class DetectionResult:
    baghdad_mentioned: bool
    verified_matches: List[AreaMatch] = field(default_factory=list)
    ambiguous_matches: List[AreaMatch] = field(default_factory=list)

    @property
    def has_any_match(self) -> bool:
        return bool(self.verified_matches or self.ambiguous_matches)

    @property
    def is_resolvable(self) -> bool:
        """At least one confirmed (verified) area was detected."""
        return bool(self.verified_matches)


class LocationTree:
    def __init__(self, path: Path = DEFAULT_TREE_PATH):
        self.path = path
        self._nodes_by_id: Dict[str, AreaNode] = {}
        self.reload()

    def reload(self):
        with open(self.path, "r", encoding="utf-8") as f:
            data = json.load(f)
        self._nodes_by_id = {
            n["id"]: AreaNode(
                id=n["id"], parent_id=n.get("parent_id"), name_ar=n["name_ar"],
                name_en=n["name_en"], aliases=n.get("aliases", []),
                status=n.get("status", "unknown"), confidence=n.get("confidence", 0.5),
            )
            for n in data.get("nodes", [])
        }

    # --- Tree navigation ---

    def get(self, node_id: str) -> Optional[AreaNode]:
        return self._nodes_by_id.get(node_id)

    def ancestors(self, node_id: str) -> List[str]:
        """Returns ancestor ids from immediate parent up to the root side, exclusive of node_id itself."""
        result = []
        node = self._nodes_by_id.get(node_id)
        while node and node.parent_id:
            result.append(node.parent_id)
            node = self._nodes_by_id.get(node.parent_id)
        return result

    def descendants(self, node_id: str) -> List[str]:
        result = []
        for n in self._nodes_by_id.values():
            if node_id in self.ancestors(n.id):
                result.append(n.id)
        return result

    def children(self, node_id: Optional[str]) -> List[AreaNode]:
        return [n for n in self._nodes_by_id.values() if n.parent_id == node_id]

    def is_related(self, node_id_a: str, node_id_b: str) -> bool:
        """True if A == B, A is an ancestor of B, or B is an ancestor of A."""
        if node_id_a == node_id_b:
            return True
        if node_id_b in self.ancestors(node_id_a):
            return True
        if node_id_a in self.ancestors(node_id_b):
            return True
        return False

    # --- Detection ---

    @staticmethod
    def _alias_present(alias_norm: str, match_text: str) -> bool:
        if re.fullmatch(r"[a-z0-9\s\-]+", alias_norm):
            pattern = r"(?<![a-z0-9])" + re.escape(alias_norm) + r"(?![a-z0-9])"
            return re.search(pattern, match_text) is not None
        return alias_norm in match_text

    def detect(self, raw_text: str) -> DetectionResult:
        match_text = normalize_for_matching(raw_text)
        baghdad_mentioned = any(self._alias_present(b, match_text) for b in BAGHDAD_ALIASES)

        verified, ambiguous = [], []
        for node in self._nodes_by_id.values():
            for alias in node.aliases:
                alias_norm = normalize_for_matching(alias)
                if not alias_norm.strip():
                    continue
                if self._alias_present(alias_norm, match_text):
                    match = AreaMatch(node=node, matched_alias=alias)
                    (verified if node.status == "verified" else ambiguous).append(match)
                    break

        return DetectionResult(
            baghdad_mentioned=baghdad_mentioned or bool(verified or ambiguous),
            verified_matches=verified, ambiguous_matches=ambiguous,
        )

    def match_ids(self, raw_text: str) -> Set[str]:
        """Convenience: just the set of verified node ids detected in the text."""
        return {m.node.id for m in self.detect(raw_text).verified_matches}

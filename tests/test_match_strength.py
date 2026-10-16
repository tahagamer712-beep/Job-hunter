import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.location_tree import LocationTree  # noqa: E402
from src.matching_engine import compute_match_strength  # noqa: E402


class TestMatchStrength(unittest.TestCase):
    def setUp(self):
        fd, self.tmp_path = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        self.path = Path(self.tmp_path)
        self.path.write_text(json.dumps({"nodes": [
            {"id": "rusafa", "parent_id": None, "name_ar": "ر", "name_en": "R", "aliases": [], "status": "verified", "confidence": 1.0},
            {"id": "sadr_city", "parent_id": "rusafa", "name_ar": "ص", "name_en": "S", "aliases": [], "status": "verified", "confidence": 1.0},
            {"id": "al_falah", "parent_id": "sadr_city", "name_ar": "ف", "name_en": "F", "aliases": [], "status": "verified", "confidence": 1.0},
        ]}, ensure_ascii=False), encoding="utf-8")
        self.tree = LocationTree(path=self.path)

    def tearDown(self):
        os.remove(self.tmp_path)

    def test_exact_match_is_100_percent(self):
        score = compute_match_strength({"al_falah"}, ["al_falah"], self.tree)
        self.assertEqual(score, 100)

    def test_one_hop_distance_lower_than_exact(self):
        score = compute_match_strength({"al_falah"}, ["sadr_city"], self.tree)
        self.assertLess(score, 100)
        self.assertGreaterEqual(score, 50)

    def test_two_hop_distance_lower_still(self):
        near = compute_match_strength({"al_falah"}, ["sadr_city"], self.tree)
        far = compute_match_strength({"al_falah"}, ["rusafa"], self.tree)
        self.assertLess(far, near)

    def test_score_never_below_floor(self):
        score = compute_match_strength({"al_falah"}, ["rusafa"], self.tree)
        self.assertGreaterEqual(score, 50)

    def test_unrelated_areas_score_zero(self):
        score = compute_match_strength({"al_falah"}, ["nonexistent"], self.tree)
        self.assertEqual(score, 0)


if __name__ == "__main__":
    unittest.main()

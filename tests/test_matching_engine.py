import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.location_tree import LocationTree  # noqa: E402
from src.matching_engine import find_matching_users, user_matches_job  # noqa: E402
from src.user_profiles import UserProfile  # noqa: E402


class TestMatchingEngine(unittest.TestCase):
    def setUp(self):
        fd, self.tmp_path = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        self.path = Path(self.tmp_path)
        self.path.write_text(json.dumps({"nodes": [
            {"id": "rusafa", "parent_id": None, "name_ar": "الرصافة", "name_en": "Al-Rusafa",
             "aliases": [], "status": "verified", "confidence": 1.0},
            {"id": "sadr_city", "parent_id": "rusafa", "name_ar": "الصدر", "name_en": "Sadr City",
             "aliases": [], "status": "verified", "confidence": 1.0},
            {"id": "al_falah", "parent_id": "sadr_city", "name_ar": "الفلاح", "name_en": "Al-Falah",
             "aliases": [], "status": "verified", "confidence": 1.0},
            {"id": "karkh", "parent_id": None, "name_ar": "الكرخ", "name_en": "Al-Karkh",
             "aliases": [], "status": "verified", "confidence": 1.0},
        ]}, ensure_ascii=False), encoding="utf-8")
        self.tree = LocationTree(path=self.path)

    def tearDown(self):
        os.remove(self.tmp_path)

    def test_user_matches_when_category_and_area_both_align(self):
        result = user_matches_job(
            user_category_ids=[1], user_area_ids=["rusafa"],
            job_category_id=1, job_area_ids={"al_falah"}, tree=self.tree,
        )
        self.assertTrue(result, "User picked broad Rusafa; job is in a Rusafa sub-area — should match")

    def test_no_match_if_category_differs(self):
        result = user_matches_job(
            user_category_ids=[2], user_area_ids=["rusafa"],
            job_category_id=1, job_area_ids={"al_falah"}, tree=self.tree,
        )
        self.assertFalse(result)

    def test_no_match_if_areas_unrelated(self):
        result = user_matches_job(
            user_category_ids=[1], user_area_ids=["karkh"],
            job_category_id=1, job_area_ids={"al_falah"}, tree=self.tree,
        )
        self.assertFalse(result)

    def test_specific_sub_area_user_matches_broader_job_area(self):
        # User picked the specific sub-neighborhood; job only mentions the
        # broader major area — should still match (ancestor relation).
        result = user_matches_job(
            user_category_ids=[1], user_area_ids=["al_falah"],
            job_category_id=1, job_area_ids={"sadr_city"}, tree=self.tree,
        )
        self.assertTrue(result)

    def test_find_matching_users_across_multiple_profiles(self):
        profiles = [
            UserProfile("user1", None, None, False, job_category_ids=[1], area_node_ids=["rusafa"]),
            UserProfile("user2", None, None, False, job_category_ids=[1], area_node_ids=["karkh"]),
            UserProfile("user3", None, None, False, job_category_ids=[2], area_node_ids=["rusafa"]),
        ]
        matches = find_matching_users(job_category_id=1, job_area_ids={"al_falah"},
                                       all_user_profiles=profiles, tree=self.tree)
        matched_ids = {m.telegram_user_id for m in matches}
        self.assertEqual(matched_ids, {"user1"})  # user2 wrong area, user3 wrong category

    def test_no_area_preference_never_matches(self):
        result = user_matches_job(
            user_category_ids=[1], user_area_ids=[],
            job_category_id=1, job_area_ids={"al_falah"}, tree=self.tree,
        )
        self.assertFalse(result)


if __name__ == "__main__":
    unittest.main()

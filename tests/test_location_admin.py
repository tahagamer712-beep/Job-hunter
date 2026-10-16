import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import location_admin  # noqa: E402
from src.location_tree import LocationTree  # noqa: E402


class TestLocationAdminTree(unittest.TestCase):
    def setUp(self):
        fd, self.tmp_path = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        self.path = Path(self.tmp_path)
        self.path.write_text(json.dumps({"nodes": [
            {"id": "rusafa", "parent_id": None, "name_ar": "الرصافة", "name_en": "Al-Rusafa",
             "aliases": ["Rusafa", "الرصافة"], "status": "verified", "confidence": 1.0},
            {"id": "sadr_city", "parent_id": "rusafa", "name_ar": "مدينة الصدر", "name_en": "Sadr City",
             "aliases": ["Sadr City"], "status": "verified", "confidence": 1.0},
        ]}, ensure_ascii=False), encoding="utf-8")

    def tearDown(self):
        os.remove(self.tmp_path)

    def test_add_node_defaults_ambiguous(self):
        entry = location_admin.add_node("al_fadhil", "sadr_city", "Al-Fadhil", "الفضل", ["الفضل"], path=self.path)
        self.assertEqual(entry["status"], "ambiguous")
        self.assertEqual(entry["parent_id"], "sadr_city")

    def test_add_node_rejects_unknown_parent(self):
        with self.assertRaises(ValueError):
            location_admin.add_node("x", "nonexistent", "X", "X", [], path=self.path)

    def test_add_duplicate_id_rejected(self):
        with self.assertRaises(ValueError):
            location_admin.add_node("rusafa", None, "X", "X", [], path=self.path)

    def test_set_status_promotes_and_engine_resolves_it(self):
        location_admin.add_node("al_fadhil", "sadr_city", "Al-Fadhil", "الفضل", ["الفضل"], path=self.path)
        location_admin.set_status("al_fadhil", "verified", path=self.path)
        tree = LocationTree(path=self.path)
        result = tree.detect("Job needed near Al-Fadhil, Baghdad")
        ids = {m.node.id for m in result.verified_matches}
        self.assertIn("al_fadhil", ids)

    def test_new_node_not_verified_until_promoted(self):
        location_admin.add_node("al_fadhil", "sadr_city", "Al-Fadhil", "الفضل", ["الفضل"], path=self.path)
        tree = LocationTree(path=self.path)
        result = tree.detect("Job needed near Al-Fadhil only")
        ids = {m.node.id for m in result.verified_matches}
        self.assertNotIn("al_fadhil", ids)

    def test_move_node_reparents(self):
        location_admin.add_node("al_fadhil", "sadr_city", "Al-Fadhil", "الفضل", ["الفضل"], path=self.path)
        location_admin.move_node("al_fadhil", "rusafa", path=self.path)
        node = location_admin.find_node("al_fadhil", path=self.path)
        self.assertEqual(node["parent_id"], "rusafa")

    def test_delete_leaf_node(self):
        location_admin.add_node("al_fadhil", "sadr_city", "Al-Fadhil", "الفضل", ["الفضل"], path=self.path)
        self.assertTrue(location_admin.delete_node("al_fadhil", path=self.path))
        self.assertIsNone(location_admin.find_node("al_fadhil", path=self.path))

    def test_delete_node_with_children_refused_without_reparent(self):
        location_admin.add_node("al_fadhil", "sadr_city", "Al-Fadhil", "الفضل", ["الفضل"], path=self.path)
        with self.assertRaises(ValueError):
            location_admin.delete_node("sadr_city", path=self.path)

    def test_delete_node_with_children_reparents_when_given(self):
        location_admin.add_node("al_fadhil", "sadr_city", "Al-Fadhil", "الفضل", ["الفضل"], path=self.path)
        location_admin.delete_node("sadr_city", reparent_children_to="rusafa", path=self.path)
        node = location_admin.find_node("al_fadhil", path=self.path)
        self.assertEqual(node["parent_id"], "rusafa")
        self.assertIsNone(location_admin.find_node("sadr_city", path=self.path))

    def test_merge_nodes_combines_aliases_and_reparents_children(self):
        location_admin.add_node("sadr_city_dup", "rusafa", "Sadr City Duplicate", "الصدر مكرر",
                                 ["الصدر مكرر"], path=self.path)
        location_admin.add_node("child_of_dup", "sadr_city_dup", "Child", "فرعي", [], path=self.path)
        merged = location_admin.merge_nodes("sadr_city_dup", "sadr_city", path=self.path)
        self.assertIn("الصدر مكرر", merged["aliases"])
        child = location_admin.find_node("child_of_dup", path=self.path)
        self.assertEqual(child["parent_id"], "sadr_city")
        self.assertIsNone(location_admin.find_node("sadr_city_dup", path=self.path))

    def test_move_node_rejects_cycle(self):
        """C07: moving a node under its own descendant must be rejected."""
        location_admin.add_node("al_fadhil", "sadr_city", "Al-Fadhil", "الفضل", [], path=self.path)
        with self.assertRaises(ValueError):
            location_admin.move_node("sadr_city", "al_fadhil", path=self.path)  # sadr_city -> its own child

    def test_move_node_rejects_self_parent(self):
        with self.assertRaises(ValueError):
            location_admin.move_node("sadr_city", "sadr_city", path=self.path)

    def test_merge_nodes_rejects_cycle(self):
        """C07: merging a node into its own descendant must be rejected."""
        location_admin.add_node("al_fadhil", "sadr_city", "Al-Fadhil", "الفضل", [], path=self.path)
        with self.assertRaises(ValueError):
            location_admin.merge_nodes("sadr_city", "al_fadhil", path=self.path)

    def test_merge_nodes_rejects_self_merge(self):
        with self.assertRaises(ValueError):
            location_admin.merge_nodes("sadr_city", "sadr_city", path=self.path)


if __name__ == "__main__":
    unittest.main()

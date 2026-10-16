import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.location_tree import LocationTree  # noqa: E402


class TestLocationTreeStructure(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tree = LocationTree()

    def test_sadr_city_children_include_sub_neighborhoods(self):
        children = {c.id for c in self.tree.children("sadr_city")}
        self.assertEqual(children, {"al_falah", "al_dakhil", "orzdi"})

    def test_ancestors_of_sub_neighborhood_include_major_area_and_side(self):
        ancestors = self.tree.ancestors("al_falah")
        self.assertEqual(ancestors, ["sadr_city", "rusafa_north", "rusafa"])

    def test_descendants_of_sadr_city(self):
        descendants = set(self.tree.descendants("sadr_city"))
        self.assertEqual(descendants, {"al_falah", "al_dakhil", "orzdi"})

    def test_descendants_of_rusafa_include_sadr_city_and_its_children(self):
        descendants = set(self.tree.descendants("rusafa"))
        self.assertIn("sadr_city", descendants)
        self.assertIn("al_falah", descendants)
        self.assertNotIn("karkh", descendants)
        self.assertNotIn("al_mansour", descendants)


class TestIsRelated(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tree = LocationTree()

    def test_same_node_is_related(self):
        self.assertTrue(self.tree.is_related("al_falah", "al_falah"))

    def test_parent_and_child_are_related(self):
        self.assertTrue(self.tree.is_related("sadr_city", "al_falah"))
        self.assertTrue(self.tree.is_related("al_falah", "sadr_city"))  # symmetric

    def test_grandparent_and_grandchild_are_related(self):
        self.assertTrue(self.tree.is_related("rusafa", "al_falah"))

    def test_siblings_are_not_related(self):
        self.assertFalse(self.tree.is_related("al_falah", "al_dakhil"))

    def test_different_sides_are_not_related(self):
        self.assertFalse(self.tree.is_related("rusafa", "karkh"))
        self.assertFalse(self.tree.is_related("al_falah", "al_mansour"))


class TestDetection(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tree = LocationTree()

    def test_detects_sub_neighborhood(self):
        result = self.tree.detect("Medical Representative needed in Al-Falah, Sadr City, Baghdad")
        ids = {m.node.id for m in result.verified_matches}
        self.assertIn("al_falah", ids)
        self.assertIn("sadr_city", ids)  # "Sadr City" also literally appears

    def test_detects_karkh_misspelling(self):
        result = self.tree.detect("Medical Representative — South Al Karhk, Baghdad")
        ids = {m.node.id for m in result.verified_matches}
        self.assertIn("karkh", ids)

    def test_beirut_only_matches_specific_alias(self):
        result = self.tree.detect("Job opportunity near Beirut Square, Baghdad")
        ids = {m.node.id for m in result.verified_matches}
        self.assertIn("beirut_square", ids)

        result2 = self.tree.detect("I love visiting Beirut on vacation")
        ids2 = {m.node.id for m in result2.verified_matches}
        self.assertNotIn("beirut_square", ids2)

    def test_ambiguous_area_not_in_verified_matches(self):
        result = self.tree.detect("Job in Al Maghrib, Baghdad")
        verified_ids = {m.node.id for m in result.verified_matches}
        ambiguous_ids = {m.node.id for m in result.ambiguous_matches}
        self.assertNotIn("al_maghrib", verified_ids)
        self.assertIn("al_maghrib", ambiguous_ids)

    def test_no_match_baghdad_only(self):
        result = self.tree.detect("Medical Representative needed in Baghdad")
        self.assertTrue(result.baghdad_mentioned)
        self.assertFalse(result.is_resolvable)

    def test_match_ids_convenience(self):
        ids = self.tree.match_ids("Job in Al-Mansour, Karkh, Baghdad")
        self.assertIn("al_mansour", ids)
        self.assertIn("karkh", ids)


if __name__ == "__main__":
    unittest.main()

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.feedback_insights import build_suggestions  # noqa: E402


class TestFeedbackInsights(unittest.TestCase):
    def test_no_suggestions_with_insufficient_data(self):
        stats = [{"role_normalized": "COSMETICS_SALES", "user_decision": "REJECTED", "n": 2}]
        self.assertEqual(build_suggestions(stats), [])

    def test_high_rejection_rate_suggests_disabling(self):
        stats = [{"role_normalized": "COSMETICS_SALES", "user_decision": "REJECTED", "n": 5}]
        suggestions = build_suggestions(stats)
        self.assertEqual(len(suggestions), 1)
        self.assertIn("COSMETICS_SALES", suggestions[0])
        self.assertIn("toggleRole", suggestions[0])

    def test_high_acceptance_rate_suggests_nothing_alarming(self):
        stats = [{"role_normalized": "MEDICAL_REPRESENTATIVE", "user_decision": "ACCEPTED", "n": 10}]
        suggestions = build_suggestions(stats)
        self.assertEqual(len(suggestions), 1)
        self.assertIn("موثوقة", suggestions[0])

    def test_mixed_feedback_produces_no_suggestion(self):
        stats = [
            {"role_normalized": "MEDICAL_INFORMATION", "user_decision": "ACCEPTED", "n": 4},
            {"role_normalized": "MEDICAL_INFORMATION", "user_decision": "REJECTED", "n": 4},
        ]
        self.assertEqual(build_suggestions(stats), [])

    def test_never_returns_a_rule_mutation_only_text(self):
        # Structural guarantee: the function's return type is List[str] —
        # it cannot possibly mutate role_accept_categories or the location
        # DB, since it has no access to Database or the JSON file at all.
        import inspect
        from src import feedback_insights
        source = inspect.getsource(feedback_insights)
        self.assertNotIn("set_setting", source)
        self.assertNotIn("baghdad_locations.json", source)


if __name__ == "__main__":
    unittest.main()

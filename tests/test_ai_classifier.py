import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.role_classifier import (  # noqa: E402
    AIRoleClassifier, CombinedRoleClassifier, RoleCategory, RoleDecision,
    RuleBasedRoleClassifier,
)


def _mock_client_returning(json_text: str):
    client = MagicMock()
    block = MagicMock()
    block.text = json_text
    response = MagicMock()
    response.content = [block]
    client.messages.create.return_value = response
    return client


class TestAIRoleClassifierParsing(unittest.TestCase):
    def test_parses_valid_json_response(self):
        client = _mock_client_returning(
            '{"role_original": "Medical Representative", '
            '"role_normalized": "MEDICAL_REPRESENTATIVE", '
            '"confidence": 0.9, "reason": "explicit title"}'
        )
        classifier = AIRoleClassifier(api_client=client)
        result = classifier.classify("Medical Representative needed in Rusafa")
        self.assertEqual(result.role_normalized, RoleCategory.MEDICAL_REPRESENTATIVE)
        self.assertEqual(result.decision, RoleDecision.GREEN)
        self.assertAlmostEqual(result.confidence, 0.9)

    def test_strips_markdown_code_fences(self):
        client = _mock_client_returning(
            '```json\n{"role_original": "", "role_normalized": "GENERAL_SALES", '
            '"confidence": 0.7, "reason": "plain sales title"}\n```'
        )
        classifier = AIRoleClassifier(api_client=client)
        result = classifier.classify("Sales Representative needed")
        self.assertEqual(result.role_normalized, RoleCategory.GENERAL_SALES)
        self.assertEqual(result.decision, RoleDecision.RED)

    def test_malformed_json_returns_none_not_crash(self):
        client = _mock_client_returning("I think this is a medical rep job, roughly.")
        classifier = AIRoleClassifier(api_client=client)
        result = classifier.classify("some ad text")
        self.assertIsNone(result)

    def test_unknown_category_from_ai_falls_back_to_unknown(self):
        client = _mock_client_returning(
            '{"role_original": "x", "role_normalized": "NOT_A_REAL_CATEGORY", '
            '"confidence": 0.5, "reason": "y"}'
        )
        classifier = AIRoleClassifier(api_client=client)
        result = classifier.classify("ad text")
        self.assertEqual(result.role_normalized, RoleCategory.UNKNOWN)


class TestCombinedClassifierReconciliation(unittest.TestCase):
    def test_agreement_keeps_result(self):
        rule = RuleBasedRoleClassifier()
        client = _mock_client_returning(
            '{"role_original": "Medical Representative", '
            '"role_normalized": "MEDICAL_REPRESENTATIVE", '
            '"confidence": 0.95, "reason": "agrees"}'
        )
        ai = AIRoleClassifier(api_client=client)
        combined = CombinedRoleClassifier(rule_classifier=rule, ai_classifier=ai)
        result = combined.classify("Medical Representative needed in Rusafa, visit doctors and pharmacies")
        self.assertEqual(result.role_normalized, RoleCategory.MEDICAL_REPRESENTATIVE)

    def test_disagreement_downgrades_to_yellow_never_invents_green(self):
        rule = RuleBasedRoleClassifier()
        # AI disagrees wildly with the rule engine's clear GENERAL_SALES read.
        client = _mock_client_returning(
            '{"role_original": "x", "role_normalized": "MEDICAL_REPRESENTATIVE", '
            '"confidence": 0.6, "reason": "disagrees"}'
        )
        ai = AIRoleClassifier(api_client=client)
        combined = CombinedRoleClassifier(rule_classifier=rule, ai_classifier=ai)
        result = combined.classify("مطلوب مندوب مبيعات لشركة الكترونيات")
        self.assertEqual(result.decision, RoleDecision.YELLOW)
        self.assertNotEqual(result.role_normalized, RoleCategory.MEDICAL_REPRESENTATIVE)

    def test_no_ai_classifier_configured_uses_rule_only(self):
        combined = CombinedRoleClassifier()  # no AI wired
        result = combined.classify("Medical Representative — Rusafa, Baghdad")
        self.assertEqual(result.role_normalized, RoleCategory.MEDICAL_REPRESENTATIVE)


if __name__ == "__main__":
    unittest.main()

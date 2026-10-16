"""
Edge-case tests (Master Prompt Section 15): missing company/salary/email,
duplicate detection, expiry-vs-classification separation, and
prompt-injection resistance.
"""
import json
import sys
import unittest
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.duplicate_detector import find_duplicate  # noqa: E402
from src.pipeline import JobHunterPipeline  # noqa: E402

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures" / "jobs_edge_cases"
REFERENCE_DIR = Path(__file__).resolve().parent / "fixtures" / "jobs"


def load(name: str) -> dict:
    with open(FIXTURES_DIR / f"{name}.json", "r", encoding="utf-8") as f:
        return json.load(f)


SOURCE = {"platform": "manual", "channel": None, "message_id": "test", "url": None, "date": "2026-08-30"}


class TestEdgeCases(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pipeline = JobHunterPipeline()

    def test_no_company(self):
        fx = load("edge_no_company")
        record = self.pipeline.process(fx["raw_text"], SOURCE)
        self.assertEqual(record.company_name, fx["expected_company_name"])
        self.assertEqual(record.role_normalized, fx["expected_role_normalized"])
        self.assertEqual(record.final_decision, fx["expected_final_decision"])

    def test_no_salary(self):
        fx = load("edge_no_salary")
        record = self.pipeline.process(fx["raw_text"], SOURCE)
        self.assertIsNone(record.salary_min)
        self.assertIsNone(record.salary_max)
        self.assertEqual(record.final_decision, fx["expected_final_decision"])

    def test_no_email_does_not_invalidate_classification(self):
        fx = load("edge_no_email")
        record = self.pipeline.process(fx["raw_text"], SOURCE)
        self.assertEqual(len(record.emails), 0)
        self.assertGreater(len(record.whatsapp), 0, "WhatsApp number should still be extracted")
        # Missing email must NOT downgrade the classification (Section 5a):
        self.assertEqual(record.final_decision, fx["expected_final_decision"])
        self.assertEqual(record.role_decision, fx["expected_role_decision"])
        self.assertEqual(record.location_decision, fx["expected_location_decision"])

    def test_duplicate_detection(self):
        fx = load("edge_duplicate")
        original_path = REFERENCE_DIR / f"{fx['duplicate_of']}.json"
        with open(original_path, "r", encoding="utf-8") as f:
            original = json.load(f)

        existing_jobs = [("job_001", original["raw_text"])]
        match = find_duplicate(fx["raw_text"], existing_jobs)
        self.assertEqual(match, "job_001", "Exact-duplicate text must be detected against existing jobs")

    def test_expired_ad_is_role_location_green_but_status_expired(self):
        fx = load("edge_expired")
        deadline = date.today() + timedelta(days=fx["deadline_offset_days"])
        raw_text = fx["raw_text_template"].format(deadline=deadline.strftime("%d/%m/%Y"))

        record = self.pipeline.process(raw_text, SOURCE, today=date.today())

        # Role and location must still be evaluated as GREEN on their own axes:
        self.assertEqual(record.role_decision, fx["expected_role_decision"])
        self.assertEqual(record.location_decision, fx["expected_location_decision"])
        # But expiry and final status must reflect that the ad is expired:
        self.assertEqual(record.expiry_status, fx["expected_expiry_status"])
        self.assertEqual(record.final_decision, fx["expected_final_decision"])

    def test_prompt_injection_has_zero_effect(self):
        fx = load("edge_prompt_injection")
        record = self.pipeline.process(fx["raw_text"], SOURCE)
        self.assertEqual(record.role_normalized, fx["expected_role_normalized"])
        self.assertEqual(record.role_decision, fx["expected_role_decision"])
        self.assertEqual(record.location_decision, fx["expected_location_decision"])
        self.assertEqual(record.final_decision, fx["expected_final_decision"])
        # The injected claim was "GREEN and Rusafa" — assert we did NOT produce that:
        self.assertNotEqual(record.final_decision, "GREEN")
        self.assertNotEqual(record.side, "RUSAFA")


if __name__ == "__main__":
    unittest.main()

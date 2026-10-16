"""
Runs the 11 reference fixtures (Master Prompt Section 15) through the full
pipeline and checks every expected field, not just final_decision — a test
that only checks the final decision can pass "by accident" while the
underlying parser is broken.
"""
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.pipeline import JobHunterPipeline  # noqa: E402

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures" / "jobs"


def load_fixtures():
    fixtures = []
    for path in sorted(FIXTURES_DIR.glob("*.json")):
        with open(path, "r", encoding="utf-8") as f:
            fixtures.append(json.load(f))
    return fixtures


class TestReferenceFixtures(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pipeline = JobHunterPipeline()
        cls.fixtures = load_fixtures()

    def test_all_eleven_fixtures_loaded(self):
        self.assertEqual(len(self.fixtures), 11, "Expected exactly 11 reference fixtures")

    def test_fixtures(self):
        source = {"platform": "manual", "channel": None, "message_id": "test",
                   "url": None, "date": "2026-08-30"}

        failures = []
        for fx in self.fixtures:
            record = self.pipeline.process(fx["raw_text"], source)

            checks = [
                ("role_normalized", fx.get("expected_role_normalized"), record.role_normalized),
                ("role_decision", fx.get("expected_role_decision"), record.role_decision),
                ("location_decision", fx.get("expected_location_decision"), record.location_decision),
                ("final_decision", fx.get("expected_final_decision"), record.final_decision),
            ]
            if "expected_side" in fx:
                checks.append(("side", fx["expected_side"], record.side))
            if "expected_is_partial_location" in fx:
                checks.append(("is_partial_location", fx["expected_is_partial_location"], record.is_partial_location))

            for field_name, expected, actual in checks:
                if expected != actual:
                    failures.append(
                        f"[{fx['id']}] {field_name}: expected={expected!r} actual={actual!r}"
                    )

        if failures:
            self.fail("Fixture mismatches:\n" + "\n".join(failures))


if __name__ == "__main__":
    unittest.main()

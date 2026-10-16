"""
Regression test against a REAL multi-ad batch (Master Prompt Section 68:
"test as a real user"). This fixture is an actual forwarded message
containing 7 distinct ads, batched together the way real channels post them.
Locks in two bugs found and fixed against this exact data:
  - "Karhk" (misspelling of Karkh) not matching the Karkh Exclusion Gate
  - Arabic plural/feminine inflections of مندوب (e.g. "مندوبي مبيعات",
    "مندوبة أعلام") not matching the literal role-term list
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.ad_splitter import split_ads  # noqa: E402
from src.pipeline import JobHunterPipeline  # noqa: E402

FIXTURE_PATH = Path(__file__).resolve().parent / "fixtures" / "real_world" / "dnairaq_batch_2026_08_30.txt"

SOURCE = {"platform": "manual", "channel": "@DNAIRAQ", "message_id": "batch1", "url": None, "date": "2026-08-30"}

# (final_decision, role_normalized, location_decision) expected per ad, in order.
EXPECTED = [
    ("RED", None, "RED"),                        # ad1: Karkh zone -> Karkh gate
    ("YELLOW", "MEDICAL_REPRESENTATIVE", "YELLOW"),  # ad2: Baghdad only, side unstated
    ("RED", "MEDICAL_REPRESENTATIVE", "RED"),        # ad3: "South Al Karhk" misspelling -> Karkh gate
    ("RED", "GENERAL_SALES", None),                   # ad4: rejected role category (default settings)
    ("RED", None, "RED"),                            # ad5: outside Baghdad (multiple other governorates)
    ("RED", "MEDICAL_REPRESENTATIVE", "RED"),        # ad6: Karbala & Babylon, outside Baghdad
    ("RED", "GENERAL_SALES", "RED"),                  # ad7: general sales + outside Baghdad (Basra/Kirkuk)
]


class TestRealWorldBatch(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pipeline = JobHunterPipeline()
        cls.raw = FIXTURE_PATH.read_text(encoding="utf-8")
        cls.ads = split_ads(cls.raw)

    def test_splitter_finds_all_seven_ads(self):
        self.assertEqual(len(self.ads), 7)

    def test_no_karkh_ad_is_ever_green(self):
        """The single most important regression: Karkh (even misspelled) must never be GREEN."""
        for ad in self.ads:
            record = self.pipeline.process(ad, SOURCE)
            if "كرخ" in ad or "karkh" in ad.lower() or "karhk" in ad.lower():
                self.assertNotEqual(
                    record.final_decision, "GREEN",
                    f"An ad mentioning Karkh was classified GREEN:\n{ad[:200]}",
                )

    def test_expected_final_decisions(self):
        failures = []
        for i, (ad, (expected_final, expected_role, expected_loc)) in enumerate(zip(self.ads, EXPECTED), 1):
            record = self.pipeline.process(ad, SOURCE)
            if record.final_decision != expected_final:
                failures.append(f"ad{i}: final_decision expected={expected_final} actual={record.final_decision}")
            if expected_role and record.role_normalized != expected_role:
                failures.append(f"ad{i}: role_normalized expected={expected_role} actual={record.role_normalized}")
            if expected_loc and record.location_decision != expected_loc:
                failures.append(f"ad{i}: location_decision expected={expected_loc} actual={record.location_decision}")
        if failures:
            self.fail("Real-world batch mismatches:\n" + "\n".join(failures))

    def test_whatsapp_only_ad_still_has_extractable_contact(self):
        # ad1 has no email, only a WhatsApp number — must still be extracted.
        record = self.pipeline.process(self.ads[0], SOURCE)
        self.assertEqual(len(record.emails), 0)
        self.assertGreater(len(record.whatsapp), 0)


if __name__ == "__main__":
    unittest.main()

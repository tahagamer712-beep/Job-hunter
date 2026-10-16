import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.duplicate_detector import similarity, find_duplicate, NEAR_DUPLICATE_THRESHOLD  # noqa: E402
from src.extractors import extract_compensation  # noqa: E402


class TestCompensationExtraction(unittest.TestCase):
    def test_salary_range_usd(self):
        comp = extract_compensation("Salary: 800$ – 1000$ monthly, plus commission and quarterly incentives.")
        self.assertEqual(comp.salary_min, 800.0)
        self.assertEqual(comp.salary_max, 1000.0)
        self.assertEqual(comp.currency, "USD")
        self.assertTrue(comp.commission)
        self.assertTrue(comp.incentives)

    def test_salary_negotiated_arabic(self):
        comp = extract_compensation("الراتب يحدد أثناء المقابلة، حسب الخبرة.")
        self.assertIsNone(comp.salary_min)
        self.assertEqual(comp.salary_type, "negotiated")

    def test_working_hours_part_time(self):
        comp = extract_compensation("Working hours: 4 PM – 9 PM daily.")
        self.assertEqual(comp.work_start, "16:00")
        self.assertEqual(comp.work_end, "21:00")
        self.assertTrue(comp.part_time)

    def test_no_salary_mentioned_stays_none(self):
        comp = extract_compensation("Medical Representative needed in Rusafa. Send your CV.")
        self.assertIsNone(comp.salary_min)
        self.assertIsNone(comp.salary_max)
        self.assertIsNone(comp.salary_type)

    def test_age_range_not_misread_as_salary(self):
        """C05 regression: a bare number range with no currency (e.g. an
        age range) must never be reported as a salary."""
        comp = extract_compensation("العمر من 25-30 سنة، الاتصال على 07701234567")
        self.assertIsNone(comp.salary_min)
        self.assertIsNone(comp.salary_max)

    def test_invalid_hour_range_not_reported_as_working_hours(self):
        """C05 regression: an out-of-range 'hour' (e.g. from a stray digit
        sequence) must not be accepted as working hours."""
        comp = extract_compensation("رقم الطلب 45-99 للمراجعة")
        self.assertIsNone(comp.work_start)
        self.assertIsNone(comp.work_end)


class TestNearDuplicateDetection(unittest.TestCase):
    def test_canonical_spec_example_is_near_duplicate(self):
        # This exact pair is the master spec's own example (Section 30).
        original = "Medical Representative - Russafa"
        reworded = "Medical Rep needed in Baghdad Russafa"
        sim = similarity(original, reworded)
        self.assertGreaterEqual(sim, NEAR_DUPLICATE_THRESHOLD)
        match = find_duplicate(reworded, [("job_1", original)])
        self.assertEqual(match, "job_1")

    def test_reworded_ad_with_shared_company_detected_as_near_duplicate(self):
        original = "Medical Representative - Russafa. Company: Webber Naturals. Apply now."
        reworded = "Medical Rep needed in Baghdad Russafa area. Company: Webber Naturals. Apply today."
        match = find_duplicate(reworded, [("job_1", original)])
        self.assertEqual(match, "job_1")

    def test_unrelated_ads_are_not_duplicates(self):
        ad_a = "Medical Representative - Rusafa, pharmaceutical company, apply via email."
        ad_b = "مطلوب محاسب للعمل في شركة مقاولات - الكرخ."
        match = find_duplicate(ad_b, [("job_1", ad_a)])
        self.assertIsNone(match)


if __name__ == "__main__":
    unittest.main()

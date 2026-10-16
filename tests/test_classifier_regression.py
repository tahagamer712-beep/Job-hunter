# -*- coding: utf-8 -*-
"""Regression tests for classifier context awareness."""
import unittest
import sys, os
sys.path.insert(0, os.path.expanduser('~/job_hunter'))
os.chdir(os.path.expanduser('~/job_hunter'))

from src.taxonomy import TaxonomyStore
from src.database import Database
from src.config import Settings


class TestClassifierRegression(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        s = Settings.load()
        cls.db = Database(s.database_path)
        cls.store = TaxonomyStore(cls.db)

    def cat_name(self, text):
        c = self.store.match_category(text)
        return c.canonical_name if c else None

    def test_cleaning_sales_not_medical_rep(self):
        self.assertNotEqual(self.cat_name("مطلوب مندوب مبيعات (كاش فان) لشركة منظفات"), "مندوب طبي")

    def test_juice_sales_not_medical_rep(self):
        self.assertNotEqual(self.cat_name("مطلوب مندوب مبيعات عصائر"), "مندوب طبي")

    def test_shipping_not_medical_rep(self):
        self.assertNotEqual(self.cat_name("مطلوب مندوب مبيعات لشركة شحن"), "مندوب طبي")

    def test_real_medical_rep(self):
        self.assertEqual(
            self.cat_name("مطلوب مندوب طبي لشركة أدوية - زيارة أطباء"),
            "مندوب طبي")

    def test_teachers_not_pharmacist(self):
        self.assertIsNone(self.cat_name("مطلوب مدرسين ومعاون مدير وحراس لمدرسة"))

    def test_female_doctor_not_pharmacist(self):
        self.assertNotEqual(self.cat_name("مطلوب طبيبة نسائية لمجمع طبي"), "صيدلي")

    def test_real_pharmacist(self):
        self.assertEqual(
            self.cat_name("مطلوب صيدلي لصيدلية في بغداد"),
            "صيدلي")


if __name__ == '__main__':
    unittest.main()

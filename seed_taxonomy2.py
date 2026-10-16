# -*- coding: utf-8 -*-
"""Seed extended taxonomy: labs, hospitals, clinics for Iraqi job market.
Safe to re-run: skips existing categories."""
import sys
sys.path.insert(0, '.')
from src.database import Database
from src.config import Settings
from src.taxonomy import TaxonomyStore

TAXONOMY2 = [
    ("فني مختبر", [
        "lab technician", "laboratory technician", "medical lab tech",
        "فني مختبرات", "تقني مختبر", "فني تحليلات", "محلل مختبر",
        "mlt", "lab tech",
    ]),
    ("ممرض", [
        "nurse", "nursing", "ممرضة", "ممرضات", "staff nurse",
        "registered nurse",
    ]),
    ("طبيب", [
        "doctor", "physician", "دكتور", "طبيبة", "general practitioner",
        "gp doctor",
    ]),
    ("طبيب أسنان", [
        "dentist", "dental", "طبيب اسنان", "طبيبة اسنان",
    ]),
    ("مدير صيدلية", [
        "pharmacy manager", "chief pharmacist", "مدير الصيدلية",
        "صيدلي اول", "head pharmacist",
    ]),
    ("موظف استقبال عيادة", [
        "clinic receptionist", "medical receptionist",
        "موظف استقبال", "موظفة استقبال", "استقبال عيادة",
    ]),
    ("سكرتير طبي", [
        "medical secretary", "سكرتيرة طبية", "سكرتير عيادة",
    ]),
    ("فني أشعة", [
        "radiology technician", "x-ray technician", "فني اشعة",
        "تقني أشعة",
    ]),
    ("معالج طبيعي", [
        "physiotherapist", "physical therapist", "علاج طبيعي",
        "معالجة طبيعية",
    ]),
    ("مندوب علاقات طبية", [
        "medical relations", "key account medical", "مندوب علاقات",
        "kam pharma", "key account manager pharma",
    ]),
    ("مشرف صيدلية", [
        "pharmacy supervisor", "مشرف صيدليات",
    ]),
    ("مندوب مشتريات طبية", [
        "medical procurement", "procurement pharmacist",
        "مندوب مشتريات", "مشتريات طبية",
    ]),
]

def main():
    s = Settings.load()
    db = Database(s.database_path)
    store = TaxonomyStore(db)
    existing = {c.canonical_name for c in store.list_categories()}
    added = 0
    for name, aliases in TAXONOMY2:
        if name in existing:
            print(f'SKIP: {name}')
            continue
        store.create_category(name, aliases)
        added += 1
        print(f'ADDED: {name} ({len(aliases)} aliases)')
    cats = store.list_categories()
    total_aliases = sum(len(c.aliases) for c in cats)
    print(f'\nTotal: {len(cats)} categories, {total_aliases} aliases')

if __name__ == '__main__':
    main()

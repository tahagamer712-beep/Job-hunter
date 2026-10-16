# -*- coding: utf-8 -*-
"""Seed professional job taxonomy for Baghdad medical/pharma job market.
Run once: python3 seed_taxonomy.py
Safe to re-run: skips existing categories."""
import sys
sys.path.insert(0, '.')
from src.database import Database
from src.config import Settings
from src.taxonomy import TaxonomyStore

TAXONOMY = [
    ("مندوب طبي", [
        "medical representative", "medical rep", "med rep", "mr",
        "مندوب دعاية طبية", "مندوب دعايه طبيه", "مندوب علمي",
        "scientific representative", "scientific rep",
        "مندوب شركات ادوية", "مندوب شركات أدوية",
        "pharma rep", "pharmaceutical representative",
    ]),
    ("صيدلي", [
        "pharmacist", "صيدلاني", "صيدلانية",
    ]),
    ("مساعد صيدلي", [
        "pharmacy assistant", "assistant pharmacist", "pharmacy technician",
        "مساعد صيدلاني", "فني صيدلة", "تقني صيدلي",
    ]),
    ("مندوب أجهزة طبية", [
        "medical devices representative", "medical device rep",
        "مندوب اجهزة طبية", "مندوب مستلزمات طبية",
        "medical equipment sales",
    ]),
    ("مشرف مبيعات طبية", [
        "medical sales supervisor", "supervisor medical",
        "مشرف طبي", "مشرف مندوبين", "team leader medical",
        "مشرف فريق طبي", "senior medical rep",
    ]),
    ("أخصائي منتج طبي", [
        "product specialist", "product manager medical",
        "اخصائي منتج", "مدير منتج طبي", "product specialist pharma",
    ]),
    ("مندوب مستحضرات تجميل", [
        "cosmetics representative", "cosmetic rep", "beauty advisor",
        "مندوب تجميل", "مندوبة تجميل", "مندوب كوزمتك",
    ]),
    ("مندوب مكملات غذائية", [
        "supplements representative", "supplement rep",
        "مندوب مكملات", "مندوب اغذية", "nutraceutical rep",
    ]),
    ("مندوب مبيعات", [
        "sales representative", "sales rep", "مندوب مبيعات",
        "salesman", "sales executive",
    ]),
    ("مدير مبيعات", [
        "sales manager", "مدير المبيعات", "area sales manager",
    ]),
    ("تسويق", [
        "marketing", "marketing specialist", "مسوق", "اخصائي تسويق",
        "digital marketing", "تسويق الكتروني",
    ]),
    ("خدمة عملاء", [
        "customer service", "call center", "خدمة زبائن", "موظف استقبال",
        "receptionist",
    ]),
    ("إداري", [
        "administrative", "admin assistant", "موظف اداري", "سكرتير",
        "secretary", "office manager",
    ]),
    ("مندوب توصيل أدوية", [
        "delivery representative pharma", "مندوب توصيل",
        "سائق توصيل ادوية",
    ]),
    ("باحث سريري", [
        "clinical research", "cra", "clinical pharmacist",
        "صيدلي سريري", "باحث اكلينيكي",
    ]),
]

def main():
    s = Settings.load()
    db = Database(s.database_path)
    store = TaxonomyStore(db)
    existing = {c.canonical_name for c in store.list_categories()}
    added = 0
    for name, aliases in TAXONOMY:
        if name in existing:
            print(f'SKIP (exists): {name}')
            continue
        store.create_category(name, aliases)
        added += 1
        print(f'ADDED: {name} ({len(aliases)} aliases)')
    print(f'\nTotal categories now: {len(store.list_categories())}')

if __name__ == '__main__':
    main()

# -*- coding: utf-8 -*-
"""100+ classification test cases: Arabic/English/abbreviations/typos/negatives."""
import sys
sys.path.insert(0, '.')
from src.database import Database
from src.config import Settings
from src.taxonomy import TaxonomyStore

s = Settings.load()
db = Database(s.database_path)
store = TaxonomyStore(db)

# (text, expected_category_or_None)
CASES = [
    # --- Medical Rep positives (20) ---
    ("Medical Representative", "مندوب طبي"),
    ("Medical Rep", "مندوب طبي"),
    ("medical rep needed", "مندوب طبي"),
    ("MR", "مندوب طبي"),
    ("مندوب طبي", "مندوب طبي"),
    ("مندوب دعاية طبية", "مندوب طبي"),
    ("مندوب دعايه طبيه", "مندوب طبي"),
    ("مندوب علمي", "مندوب طبي"),
    ("Scientific Representative", "مندوب طبي"),
    ("scientific rep", "مندوب طبي"),
    ("مندوب شركات ادوية", "مندوب طبي"),
    ("مندوب شركات أدوية", "مندوب طبي"),
    ("Pharma Rep", "مندوب طبي"),
    ("pharmaceutical representative", "مندوب طبي"),
    ("MEDICAL REPRESENTATIVE", "مندوب طبي"),
    ("Medical  Representative", "مندوب طبي"),
    ("مندوب  طبي", "مندوب طبي"),
    ("مطلوب مندوب طبي لشركة أدوية", "مندوب طبي"),
    ("Medical Representative - Baghdad", "مندوب طبي"),
    ("مندوب طبي خبرة سنتين", "مندوب طبي"),
    # --- Pharmacist (8) ---
    ("صيدلي", "صيدلي"),
    ("pharmacist", "صيدلي"),
    ("صيدلاني", "صيدلي"),
    ("صيدلانية", "صيدلي"),
    ("PHARMACIST", "صيدلي"),
    ("مطلوب صيدلي", "صيدلي"),
    ("Pharmacist needed", "صيدلي"),
    ("صيدلي اول", "مدير صيدلية"),
    # --- Pharmacy assistant (6) ---
    ("مساعد صيدلي", "مساعد صيدلي"),
    ("pharmacy assistant", "مساعد صيدلي"),
    ("assistant pharmacist", "مساعد صيدلي"),
    ("فني صيدلة", "مساعد صيدلي"),
    ("مساعد صيدلاني", "مساعد صيدلي"),
    ("pharmacy technician", "مساعد صيدلي"),
    # --- Lab tech (6) ---
    ("فني مختبر", "فني مختبر"),
    ("lab technician", "فني مختبر"),
    ("فني تحليلات", "فني مختبر"),
    ("MLT", "فني مختبر"),
    ("medical lab tech", "فني مختبر"),
    ("محلل مختبر", "فني مختبر"),
    # --- Nurse / Doctor (8) ---
    ("ممرض", "ممرض"),
    ("nurse", "ممرض"),
    ("ممرضة", "ممرض"),
    ("staff nurse", "ممرض"),
    ("طبيب", "طبيب"),
    ("doctor", "طبيب"),
    ("طبيب أسنان", "طبيب أسنان"),
    ("dentist", "طبيب أسنان"),
    # --- Medical devices (4) ---
    ("مندوب أجهزة طبية", "مندوب أجهزة طبية"),
    ("medical devices representative", "مندوب أجهزة طبية"),
    ("مندوب مستلزمات طبية", "مندوب أجهزة طبية"),
    ("medical equipment sales", "مندوب أجهزة طبية"),
    # --- Supervisor / Product (6) ---
    ("مشرف مبيعات طبية", "مشرف مبيعات طبية"),
    ("medical sales supervisor", "مشرف مبيعات طبية"),
    ("مشرف مندوبين", "مشرف مبيعات طبية"),
    ("Product Specialist", "أخصائي منتج طبي"),
    ("أخصائي منتج", "أخصائي منتج طبي"),
    ("senior medical rep", "مشرف مبيعات طبية"),
    # --- Cosmetics vs Supplements (8) ---
    ("مندوب مستحضرات تجميل", "مندوب مستحضرات تجميل"),
    ("cosmetics representative", "مندوب مستحضرات تجميل"),
    ("مندوبة تجميل", "مندوب مستحضرات تجميل"),
    ("beauty advisor", "مندوب مستحضرات تجميل"),
    ("مندوب مكملات غذائية", "مندوب مكملات غذائية"),
    ("supplements representative", "مندوب مكملات غذائية"),
    ("مندوب مكملات", "مندوب مكملات غذائية"),
    ("nutraceutical rep", "مندوب مكملات غذائية"),
    # --- General sales / marketing / admin (10) ---
    ("مندوب مبيعات", "مندوب مبيعات"),
    ("sales representative", "مندوب مبيعات"),
    ("Medical Sales Representative", "مندوب مبيعات"),
    ("sales executive", "مندوب مبيعات"),
    ("مدير مبيعات", "مدير مبيعات"),
    ("sales manager", "مدير مبيعات"),
    ("تسويق", "تسويق"),
    ("marketing specialist", "تسويق"),
    ("خدمة عملاء", "خدمة عملاء"),
    ("customer service", "خدمة عملاء"),
    # --- Hospital / clinic (8) ---
    ("موظف استقبال عيادة", "موظف استقبال عيادة"),
    ("clinic receptionist", "موظف استقبال عيادة"),
    ("سكرتير طبي", "سكرتير طبي"),
    ("medical secretary", "سكرتير طبي"),
    ("فني أشعة", "فني أشعة"),
    ("radiology technician", "فني أشعة"),
    ("معالج طبيعي", "معالج طبيعي"),
    ("physiotherapist", "معالج طبيعي"),
    # --- Typos and variations (8) ---
    ("مندوب طبى", "مندوب طبي"),
    ("صيدلى", "صيدلي"),
    ("Medical Represantative", "مندوب طبي"),  # typo handled by normalizer
    ("مندوب طببي", "مندوب طبي"),
    ("فني مختبرات", "فني مختبر"),
    ("ممرضه", "ممرض"),
    ("صيدلانية", "صيدلي"),
    ("دكتور اسنان", "طبيب أسنان"),
    # --- NEGATIVES: must NOT match (12) ---
    ("دورة تدريبية للصيادلة", None),
    ("كورس مندوب طبي", None),
    ("إعلان تسويقي لشركة أدوية", None),
    ("مطلوب سائق", None),
    ("وظيفة محاسب", None),
    ("نبحث عن مهندس", None),
    ("دورة TOT", None),
    ("منحة دراسية", None),
    ("إعلان عن منتج جديد", None),
    ("خصم 50% على الأدوية", None),
    ("مطلوب عامل نظافة", None),
    ("تدريب صيفي", None),
]

passed = 0
failed = []
for text, expected in CASES:
    m = store.match_category(text)
    got = m.canonical_name if m else None
    if got == expected:
        passed += 1
    else:
        failed.append((text, expected, got))

print(f"\n{passed}/{len(CASES)} passed")
if failed:
    print("\nFAILURES:")
    for text, exp, got in failed:
        print(f"  '{text[:40]}' -> got '{got}', expected '{exp}'")

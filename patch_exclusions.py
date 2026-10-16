# -*- coding: utf-8 -*-
"""Add exclusion patterns to taxonomy matching + missing aliases."""
p = '/data/data/com.termux/files/home/job_hunter/src/taxonomy.py'
s = open(p, encoding='utf-8').read()

# 1. Add exclusion check at start of match_category
old = '''        match_text = normalize_for_matching(raw_text)
        best = None
        best_len = 0'''
new = '''        match_text = normalize_for_matching(raw_text)
        # Exclusion: course/training/marketing ads are not jobs
        for excl in _EXCLUSION_PATTERNS:
            if excl in match_text:
                return None
        best = None
        best_len = 0'''
assert old in s, 'match pattern not found'
s = s.replace(old, new)

# 2. Add exclusion patterns constant before the class
old2 = 'class TaxonomyStore:'
new2 = '''# Patterns that disqualify a text from being a job ad at all.
# Checked (normalized) before any category matching.
_EXCLUSION_PATTERNS = [
    "كورس", "دوره تدريبيه", "دورة تدريبية",
    "اعلان تسويقي", "إعلان تسويقي",
    "خصم", "عرض خاص", "تخفيضات",
    "منحه دراسيه", "منحة دراسية",
]

class TaxonomyStore:'''
assert old2 in s, 'class pattern not found'
s = s.replace(old2, new2, 1)

open(p, 'w', encoding='utf-8').write(s)
print('EXCLUSIONS ADDED')

# 3. Add missing aliases via DB
import sys
sys.path.insert(0, '/data/data/com.termux/files/home/job_hunter')
from src.database import Database
from src.config import Settings
st = Settings.load()
db = Database(st.database_path)

# Add "دكتور اسنان" alias to طبيب أسنان, "مندوب طببي" typo to مندوب طبي
import sqlite3
con = sqlite3.connect(st.database_path)
for cat_name, alias in [("طبيب أسنان", "دكتور اسنان"), ("مندوب طبي", "مندوب طببي")]:
    row = con.execute('SELECT id FROM job_categories WHERE canonical_name=?', (cat_name,)).fetchone()
    if row:
        try:
            con.execute('INSERT INTO category_aliases (category_id, alias) VALUES (?, ?)', (row[0], alias))
            print(f'ALIAS ADDED: {alias} -> {cat_name}')
        except sqlite3.IntegrityError:
            print(f'ALIAS EXISTS: {alias}')
con.commit()
print('DONE')

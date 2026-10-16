# -*- coding: utf-8 -*-
"""Clean improvements to extractors: company, requirements, salary."""
import re

p = '/data/data/com.termux/files/home/job_hunter/src/extractors.py'
s = open(p, encoding='utf-8').read()

# --- 1. Company: add "شركة NAME" pattern ---
old = '''    m = re.search(r"(?:الشركة)\\s*[:\\-]\\s*([^\\n]+)", raw_text)
    if m:
        name = m.group(1).strip()
        if name:
            return CompanyInfo(name=name, employer_type="COMPANY")

    return CompanyInfo(name=None, employer_type=None)'''
new = '''    m = re.search(r"(?:الشركة)\\s*[:\\-]\\s*([^\\n]+)", raw_text)
    if m:
        name = m.group(1).strip()
        if name:
            return CompanyInfo(name=name, employer_type="COMPANY")

    m = re.search(r"شركة\\s+([\\u0600-\\u06FFa-zA-Z0-9\\s]{3,40})", raw_text)
    if m:
        name = m.group(1).strip().split("\\n")[0].strip()
        if name and len(name) > 2:
            return CompanyInfo(name="شركة " + name, employer_type="COMPANY")

    return CompanyInfo(name=None, employer_type=None)'''
assert old in s
s = s.replace(old, new)
print('1. Company OK')

# --- 2. Requirements: education, experience, car ---
old = '''def extract_requirements(raw_text: str) -> RequirementsInfo:
    match_text = normalize_for_matching(raw_text)
    req = RequirementsInfo()'''
new = '''def extract_requirements(raw_text: str) -> RequirementsInfo:
    match_text = normalize_for_matching(raw_text)
    req = RequirementsInfo()
    if "بكالوريوس صيدله" in match_text or "بكالوريوس صيدلة" in raw_text:
        req.education.append("بكالوريوس صيدلة")
    elif "بكالوريوس" in match_text:
        req.education.append("بكالوريوس")
    if "ماجستير" in match_text:
        req.education.append("ماجستير")
    if "دبلوم" in match_text:
        req.education.append("دبلوم")
    m = re.search(r"(\\d+)\\s*سنوات?", match_text)
    if m and any(w in match_text for w in ["خبره", "خبرة", "experience"]):
        req.experience_min_months = int(m.group(1)) * 12
        req.experience_required = True
    if "سياره" in match_text or "سيارة" in raw_text:
        req.car_required = True'''
assert old in s
s = s.replace(old, new)
print('2. Requirements OK')

# --- 3. Salary: Arabic dinar pattern ---
old = '''def extract_compensation(raw_text: str) -> CompensationInfo:
    match_text = normalize_for_matching(raw_text)
    normalized_digits_text = normalize_digits(match_text)
    comp = CompensationInfo()'''
new = '''def extract_compensation(raw_text: str) -> CompensationInfo:
    match_text = normalize_for_matching(raw_text)
    normalized_digits_text = normalize_digits(match_text)
    comp = CompensationInfo()
    # Arabic IQD salary: "الراتب: 1,500,000 دينار" or "1500000 دينار"
    m = re.search(r"(\\d[\\d,]*)\\s*دينار", normalized_digits_text)
    if m:
        try:
            val = int(m.group(1).replace(",", ""))
            if 100000 <= val <= 50000000:  # sane IQD monthly range
                comp.salary_min = comp.salary_max = float(val)
                comp.currency = "IQD"
                comp.salary_type = "fixed"
        except ValueError:
            pass'''
assert old in s
s = s.replace(old, new)
print('3. Compensation OK')

open(p, 'w', encoding='utf-8').write(s)
print('ALL DONE')

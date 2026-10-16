# -*- coding: utf-8 -*-
"""Fix: allow delivery when area is missing (user or job side)."""

# Fix 1: pipeline_multiuser.py - don't block on missing area
p1 = '/data/data/com.termux/files/home/job_hunter/src/pipeline_multiuser.py'
s1 = open(p1, encoding='utf-8').read()

old1 = '''    if not detected_area_ids or expiry_status == "EXPIRED":
        return result  # classified but no confirmed area, or already expired -> no delivery'''
new1 = '''    if expiry_status == "EXPIRED":
        return result  # already expired -> no delivery
    # Note: missing area no longer blocks delivery. Area is a boost,
    # not a requirement — category match is the primary filter.'''
assert old1 in s1, 'pipeline pattern not found'
s1 = s1.replace(old1, new1)
open(p1, 'w', encoding='utf-8').write(s1)
print('1. Pipeline fixed: area no longer blocks')

# Fix 2: matching_engine.py - match on category if no area prefs
p2 = '/data/data/com.termux/files/home/job_hunter/src/matching_engine.py'
s2 = open(p2, encoding='utf-8').read()

old2 = '''    matches = []
    for profile in all_user_profiles:
        if job_category_id not in profile.job_category_ids:
            continue
        matched_areas = {
            user_area for user_area in profile.area_node_ids
            for job_area in job_area_ids
            if tree.is_related(job_area, user_area)
        }
        if matched_areas:'''
new2 = '''    matches = []
    for profile in all_user_profiles:
        if job_category_id not in profile.job_category_ids:
            continue
        # Area matching: if user has no area prefs OR job has no area,
        # match on category alone (area is a boost, not a gate).
        if not profile.area_node_ids or not job_area_ids:
            matched_areas = set()
        else:
            matched_areas = {
                user_area for user_area in profile.area_node_ids
                for job_area in job_area_ids
                if tree.is_related(job_area, user_area)
            }
        # Category match is sufficient; area match boosts strength
        if True:'''
assert old2 in s2, 'matching pattern not found'
s2 = s2.replace(old2, new2)
open(p2, 'w', encoding='utf-8').write(s2)
print('2. Matching fixed: category sufficient')

print('DONE')

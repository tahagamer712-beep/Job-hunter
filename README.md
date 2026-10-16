# Job Hunter

**Multi-user Telegram bot** for job matching in Baghdad, built around an
admin-controlled job taxonomy and a hierarchical, fully user-selectable
region tree. This replaced the original single-owner Medical-Rep-only /
Rusafa-only design — see `job-hunter-master-prompt.md` for that original
spec (kept for historical reference; large parts of it, especially the
Karkh Exclusion Gate, are superseded by the architecture described here).

## Architecture (current)

- **No hardcoded region exclusion.** `src/location_tree.py` only *detects*
  which known area a job mentions — Karkh, Rusafa, doesn't matter. Whether
  a region is acceptable is 100% a per-user preference
  (`src/matching_engine.py`), proven in `tests/test_pipeline_multiuser.py`.
- **Admin-controlled job taxonomy**, not a fixed enum. `src/taxonomy.py`
  lets the admin define categories and merge synonyms (تواجد / صيدلي تواجد
  / sub staff / substaff → one category) via a pending-title triage queue —
  proven against that exact example in `tests/test_taxonomy.py`.
- **Hierarchical region tree**: side → sub-region (north/south/central) →
  major area → sub-neighborhood (e.g. Sadr City → Al-Falah/Al-Dakhil/Orzdi).
  Fully admin-editable (add/move/merge/delete nodes at any level) via
  `src/location_admin.py`.
- **Per-user profiles**: job-category preferences, area preferences (at any
  tree level), email, CV — `src/user_profiles.py`.
- **Invite system**: toggleable invite-only mode, deep-link invites that
  grant immediate access, and an admin approval queue for anyone else —
  `src/invites.py`.
- **Archive reprocessing**: re-classify stored ads after taxonomy/location
  changes without ever double-notifying anyone — `src/reprocessor.py`.
- **Backup/restore**: `.db` (full round-trip restore) + `.xlsx` (all tables,
  human-readable, view-only) — `src/backup.py`, proven end-to-end including
  a real Excel export in `tests/test_backup.py`.

## What's built and proven (152 tests, all passing)

```bash
cd job_hunter
python3 -m unittest discover tests -v
```

Every module above is pure Python (stdlib + openpyxl for Excel), independent
of any network access, and has a dedicated test file. The Telegram bot
(`src/bot/main.py`) wires all of it together — registration wizard (category
picker → hierarchical area picker → email → CV), a full user panel, and an
expanded admin panel (review/classify, categories, locations, users,
broadcast, sources, blacklist, invite management, settings, analytics,
backup, archive reprocessing).

## What still needs YOUR environment (not this sandbox)

No network access here — nothing below has run against a real Telegram/SMTP/
Anthropic connection; it's syntax-checked (`py_compile`) and every module it
calls is unit-tested, but the bot itself has never executed live:

- `src/bot/main.py` — needs `python-telegram-bot` + a real bot token
- `src/scanner/telegram_listener.py` (Telethon) — needs `telethon` +
  `TELEGRAM_API_ID`/`TELEGRAM_API_HASH`/`TELEGRAM_TOS_ACKNOWLEDGED`. This is
  fully wired to the multi-user pipeline now (`TelegramListener` accepts a
  pluggable `process_message_fn`; `bot/main.py` passes one that runs
  ad-splitting → taxonomy matching → per-user delivery, same as Manual
  Forward Intake) — proven in `tests/test_listener_wiring.py`, including a
  batched multi-ad channel message routing only the matching ad to the
  right user. It has never connected to real Telegram servers, though.
- `AIRoleClassifier` (`src/role_classifier.py`) — still exists from the
  original design but is NOT wired into the multi-user taxonomy path; the
  taxonomy matcher (`taxonomy.match_category`) is alias-based only for now

## Known limitations in this cut (be aware before relying on them)

- **Per-application status is now per-user** (fixed): every (job, user) pair
  has its own row in `job_applications`, so two different users applying to
  the same job never interfere with each other — proven in
  `tests/test_applications.py`. Every send uses that specific user's own
  email/App Password (`src/email_credentials.py`, `src/applications.py`) —
  there is no shared/fallback email account anywhere in this codebase.
- **Personal company blacklist** ("🚫 شركات أتجاهلها"): each user's ignore
  list is theirs alone (`user_ignored_companies` table) — ignoring a company
  only stops deliveries to that one person, proven in
  `tests/test_user_blacklist.py`. This is separate from the admin's global
  `ignored_companies` list, which is for site-wide spam/scam blocking and
  still affects everyone.
- **Registration/admin conversation state** lives in `context.user_data`,
  which does not survive a bot restart. For production durability, add
  `PicklePersistence` (see python-telegram-bot's docs) — not wired here to
  keep the dependency footprint minimal; not adding it just means someone
  mid-registration when the bot restarts has to start that step over.
- The original single-owner pipeline (`src/pipeline.py`,
  `src/role_classifier.py`'s fixed `RoleCategory`, `src/decision_engine.py`'s
  GREEN/YELLOW/RED) is kept in the codebase and still fully tested (that's
  most of the original ~90 tests), but the bot no longer calls it — it's
  there for reference and in case you want to compare behavior, not because
  it's part of the live path anymore.
- By owner's choice: notifications are instant-only (no digest/vacation
  mode), the interface is Arabic-only (no language toggle), and there is no
  "follow this specific company" feature — all deliberately out of scope.

## Setup (on your own machine or Termux)

```bash
cd job_hunter
pip install -r requirements.txt --break-system-packages
cp .env.example .env
# edit .env: TELEGRAM_BOT_TOKEN and OWNER_ID at minimum
python3 src/bot/main.py
```

The first person to `/start` with `OWNER_ID` matching becomes admin
automatically. Everyone else registers through the category/area wizard and
becomes a regular user (or gets admin-promoted later via a DB update — a
dedicated "promote to admin" button is a natural next addition).

## Running the test suite after any change

```bash
python3 -m unittest discover tests -v
```

## 24/7 supervision on Termux

`run_forever.sh` at the project root — restart loop with exponential backoff
plus periodic log rotation (`src/log_rotation.py`):

```bash
chmod +x run_forever.sh
termux-wake-lock
nohup ./run_forever.sh &
```

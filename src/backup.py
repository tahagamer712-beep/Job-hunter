"""
Backup / restore (owner request: "توليد قاعدة بيانات" button producing both
.db and .xlsx, plus a restore button so moving the bot to a new device loses
nothing — including user settings).

IMPORTANT ASYMMETRY, stated plainly: the .db export/restore round-trips
perfectly (it's just the SQLite file). The .xlsx export is for human viewing
and analysis — restoring FROM an .xlsx is not supported, because
reconstructing a relational SQLite database reliably from spreadsheet sheets
(foreign keys, types, etc.) is error-prone and risks silent data loss, which
would violate Section 31's "database survives restart" guarantee. If you
need to restore, always use the .db backup.
"""
from __future__ import annotations

import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

BACKUP_DIR = Path("backups")

ALL_TABLES = [
    "jobs", "application_events", "ignored_companies", "ignored_jobs", "settings",
    "sources", "cv_versions", "decision_feedback",
    "job_categories", "category_aliases", "pending_job_titles",
    "users", "user_job_preferences", "user_area_preferences", "job_deliveries",
    "invites", "pending_registrations",
    "job_applications", "application_status_events",
    "user_ignored_companies", "user_saved_jobs", "user_ignored_jobs",
]


def _discover_tables(conn: sqlite3.Connection) -> list:
    """
    Queries sqlite_master directly rather than relying solely on the static
    ALL_TABLES list above — a table added to the schema later but forgotten
    here (H09: several tables were missing from this list) will now still
    be included automatically. ALL_TABLES is kept as a fallback/reference
    for environments where sqlite_master isn't queryable for some reason.
    """
    try:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
        discovered = [r[0] for r in rows]
        return discovered if discovered else ALL_TABLES
    except sqlite3.Error:
        return ALL_TABLES


def backup_to_db(source_db_path: str, backup_dir: Path = BACKUP_DIR) -> Path:
    """Copies the live SQLite file to a timestamped backup — the ONLY
    export format that can be used for a full restore."""
    backup_dir.mkdir(exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    dest = backup_dir / f"job_hunter_backup_{timestamp}.db"

    # Use sqlite3's backup API rather than a raw file copy, so an in-progress
    # write on the live DB can't produce a corrupt backup.
    src_conn = sqlite3.connect(source_db_path)
    dest_conn = sqlite3.connect(str(dest))
    with dest_conn:
        src_conn.backup(dest_conn)
    src_conn.close()
    dest_conn.close()
    return dest


def restore_from_db(backup_db_path: str, target_db_path: str, keep_previous: bool = True) -> Optional[Path]:
    """
    Replaces target_db_path's contents with backup_db_path's, via the same
    safe backup API (not a raw overwrite while target might be in use).
    Returns the path of the previous-state safety copy if keep_previous=True.
    """
    previous_copy = None
    if keep_previous and Path(target_db_path).exists():
        previous_copy = backup_to_db(target_db_path)  # snapshot before overwriting, just in case

    src_conn = sqlite3.connect(backup_db_path)
    dest_conn = sqlite3.connect(target_db_path)
    with dest_conn:
        src_conn.backup(dest_conn)
    src_conn.close()
    dest_conn.close()
    return previous_copy


def export_to_xlsx(source_db_path: str, output_path: Path) -> Path:
    """
    Exports every table to its own sheet in one .xlsx workbook, for viewing/
    analysis in Excel — NOT a restore source (see module docstring).
    """
    try:
        from openpyxl import Workbook
    except ImportError as e:
        raise RuntimeError(
            "openpyxl is not installed. Run `pip install openpyxl --break-system-packages`."
        ) from e

    conn = sqlite3.connect(source_db_path)
    conn.row_factory = sqlite3.Row

    wb = Workbook()
    wb.remove(wb.active)  # drop the default blank sheet

    for table in _discover_tables(conn):
        try:
            rows = conn.execute(f"SELECT * FROM {table}").fetchall()
        except sqlite3.OperationalError:
            continue  # table doesn't exist in this DB version — skip gracefully

        ws = wb.create_sheet(title=table[:31])  # Excel sheet name limit
        if rows:
            ws.append(list(rows[0].keys()))
            for row in rows:
                ws.append([str(v) if v is not None else "" for v in row])
        else:
            ws.append(["(no rows)"])

    conn.close()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(output_path)
    return output_path


def generate_full_backup(source_db_path: str, backup_dir: Path = BACKUP_DIR) -> dict:
    """Convenience: produces both formats in one call, returns their paths."""
    db_path = backup_to_db(source_db_path, backup_dir)
    xlsx_path = backup_dir / db_path.name.replace(".db", ".xlsx")
    export_to_xlsx(source_db_path, xlsx_path)
    return {"db": db_path, "xlsx": xlsx_path}

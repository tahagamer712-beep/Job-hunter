"""
CV Manager (Master Prompt Section 45). Stores CV file paths + labels in the
`cv_versions` table (src/database.py); one CV is "active" at a time and is
what gets attached in send_email() unless the user picks another during
the Application Preview step.
"""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import Optional

from .database import Database

DEFAULT_CV_DIR = Path("cv_files")

ALLOWED_CV_EXTENSIONS = {".pdf", ".doc", ".docx"}


def is_allowed_cv_file(filename: str) -> bool:
    return Path(filename).suffix.lower() in ALLOWED_CV_EXTENSIONS


def add_cv(db: Database, source_path: Path, label: str, make_active: bool = True,
           cv_dir: Path = DEFAULT_CV_DIR) -> int:
    if not is_allowed_cv_file(source_path.name):
        raise ValueError(
            f"'{source_path.name}' is not an allowed CV file type — only "
            f"{', '.join(sorted(ALLOWED_CV_EXTENSIONS))} are accepted."
        )
    cv_dir.mkdir(parents=True, exist_ok=True)
    dest = cv_dir / source_path.name
    if source_path.resolve() != dest.resolve():
        shutil.copy2(source_path, dest)
    return db.add_cv(str(dest), label, make_active=make_active)


def get_active_cv_path(db: Database) -> Optional[Path]:
    row = db.get_active_cv()
    if row is None:
        return None
    path = Path(row["file_path"])
    return path if path.exists() else None


def list_cvs(db: Database):
    return db.list_cvs()


def set_active(db: Database, cv_id: int):
    db.set_active_cv(cv_id)

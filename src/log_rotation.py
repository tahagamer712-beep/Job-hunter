"""
Simple size-based log rotation (Master Prompt Section 52 mentions per-
subsystem log files but not rotation; real 24/7 operation needs it or logs
grow unbounded). No external dependency — plain file operations.

Usage: run this module directly (e.g. from cron or the supervision loop)
    python3 -m src.log_rotation
or import `rotate_all_logs()` / `rotate_file()` programmatically.
"""
from __future__ import annotations

import gzip
import shutil
from datetime import datetime, timezone
from pathlib import Path

LOG_DIR = Path("logs")
DEFAULT_MAX_BYTES = 5 * 1024 * 1024   # 5 MB per file before rotation
DEFAULT_KEEP_ROTATED = 5              # keep this many old compressed files per log


def rotate_file(path: Path, max_bytes: int = DEFAULT_MAX_BYTES, keep: int = DEFAULT_KEEP_ROTATED) -> bool:
    """
    If `path` exceeds max_bytes, compresses it to `<name>.<timestamp>.gz` and
    truncates the original to empty (so the running process's open file
    handle keeps writing to the same path). Returns True if rotation happened.
    Also prunes old rotated files beyond `keep`.
    """
    if not path.exists() or path.stat().st_size < max_bytes:
        return False

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    rotated_path = path.with_name(f"{path.name}.{timestamp}.gz")

    with open(path, "rb") as f_in, gzip.open(rotated_path, "wb") as f_out:
        shutil.copyfileobj(f_in, f_out)

    # Truncate in place (don't delete+recreate — the process's file handle stays valid)
    with open(path, "w", encoding="utf-8"):
        pass

    _prune_old_rotations(path, keep)
    return True


def _prune_old_rotations(original_path: Path, keep: int):
    pattern = f"{original_path.name}.*.gz"
    rotated = sorted(original_path.parent.glob(pattern), key=lambda p: p.stat().st_mtime, reverse=True)
    for old_file in rotated[keep:]:
        old_file.unlink(missing_ok=True)


def rotate_all_logs(log_dir: Path = LOG_DIR, max_bytes: int = DEFAULT_MAX_BYTES,
                     keep: int = DEFAULT_KEEP_ROTATED) -> list:
    if not log_dir.exists():
        return []
    rotated = []
    for log_file in log_dir.glob("*.log"):
        if rotate_file(log_file, max_bytes=max_bytes, keep=keep):
            rotated.append(str(log_file))
    return rotated


if __name__ == "__main__":
    rotated = rotate_all_logs()
    if rotated:
        print(f"Rotated {len(rotated)} log file(s): {', '.join(rotated)}")
    else:
        print("No log files needed rotation.")

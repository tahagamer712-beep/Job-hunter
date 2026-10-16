"""
Logging configuration (Master Prompt Section 52) — separate log files per
subsystem, all under logs/.
"""
from __future__ import annotations

import logging
from pathlib import Path

LOG_DIR = Path("logs")


def _make_logger(name: str, filename: str) -> logging.Logger:
    LOG_DIR.mkdir(exist_ok=True)
    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)
    if not logger.handlers:  # avoid duplicate handlers on re-import
        fh = logging.FileHandler(LOG_DIR / filename, encoding="utf-8")
        fh.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s"))
        logger.addHandler(fh)
        sh = logging.StreamHandler()
        sh.setFormatter(logging.Formatter("%(asctime)s - %(name)s - %(message)s"))
        logger.addHandler(sh)
    return logger


bot_logger = _make_logger("job_hunter.bot", "bot.log")
scanner_logger = _make_logger("job_hunter.scanner", "scanner.log")
classifier_logger = _make_logger("job_hunter.classifier", "classifier.log")
email_logger = _make_logger("job_hunter.email", "email.log")
error_logger = _make_logger("job_hunter.errors", "errors.log")


def log_classification(source_channel, decision_result, role_result, location_result):
    classifier_logger.info(
        "Job detected | Source: %s | Role: %s (%s) | Location: %s (%s) | Final: %s",
        source_channel, role_result.role_normalized.value, role_result.decision.value,
        location_result.decision.value,
        "PARTIAL" if location_result.is_partial else "-",
        decision_result.final_decision.value,
    )


def log_error(subsystem: str, exc: Exception):
    error_logger.error("[%s] %s: %s", subsystem, type(exc).__name__, exc)

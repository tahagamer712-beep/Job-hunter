"""
"Test Sources" logic (Master Prompt Section 58). Two tiers:
  1. Format validation — always runs, no dependency, no network.
  2. Live connectivity check — only attempted if telethon is installed AND
     scanner credentials are configured; otherwise clearly reported as
     untested rather than silently skipped or falsely marked OK.

M05 fix: the result no longer has a single ambiguous `ok` boolean (which
could be misread as "confirmed working" even when only the format was
checked and live connectivity was never attempted). `format_ok` and
`live_ok` are now separate — `live_ok=None` explicitly means "not tested",
distinct from True/False.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

_CHANNEL_USERNAME_RE = re.compile(r"^@[A-Za-z][A-Za-z0-9_]{4,31}$")
_T_ME_URL_RE = re.compile(r"^https?://t\.me/([A-Za-z][A-Za-z0-9_]{4,31})/?$")


@dataclass
class SourceTestResult:
    format_ok: bool
    live_ok: Optional[bool]   # None = not tested, True/False = actual result
    message: str

    @property
    def live_checked(self) -> bool:
        return self.live_ok is not None

    @property
    def overall_ok(self) -> bool:
        """True only if format is valid AND (untested OR confirmed live)."""
        return self.format_ok and self.live_ok is not False


def validate_identifier_format(identifier: str) -> SourceTestResult:
    identifier = identifier.strip()
    if _CHANNEL_USERNAME_RE.match(identifier):
        return SourceTestResult(format_ok=True, live_ok=None, message="Format valid (@username).")
    m = _T_ME_URL_RE.match(identifier)
    if m:
        return SourceTestResult(format_ok=True, live_ok=None,
                                 message=f"Format valid (t.me link, username={m.group(1)}).")
    return SourceTestResult(
        format_ok=False, live_ok=None,
        message="Format invalid — expected '@channel_username' or 'https://t.me/channel_username'.",
    )


async def check_source(identifier: str, settings=None) -> SourceTestResult:
    """
    Runs format validation always. If `settings` is provided and the scanner
    is configured with telethon available, also attempts a live entity
    lookup — but never crashes the caller if that fails; it just reports the
    failure clearly, with live_ok=False (not the ambiguous format_ok=True).
    """
    format_result = validate_identifier_format(identifier)
    if not format_result.format_ok:
        return format_result

    if settings is None or not getattr(settings, "scanner_enabled", False):
        return SourceTestResult(
            format_ok=True, live_ok=None,
            message=format_result.message + " Live connectivity NOT tested (scanner not configured).",
        )

    try:
        from telethon import TelegramClient
    except ImportError:
        return SourceTestResult(
            format_ok=True, live_ok=None,
            message=format_result.message + " Live connectivity NOT tested (telethon not installed).",
        )

    try:
        client = TelegramClient("job_hunter_session", int(settings.telegram_api_id), settings.telegram_api_hash)
        async with client:
            entity = await client.get_entity(identifier)
            return SourceTestResult(
                format_ok=True, live_ok=True,
                message=f"Live check OK — resolved to: {getattr(entity, 'title', identifier)}",
            )
    except Exception as e:  # noqa: BLE001
        return SourceTestResult(
            format_ok=True, live_ok=False,
            message=f"Live check failed: {type(e).__name__}: {e}",
        )

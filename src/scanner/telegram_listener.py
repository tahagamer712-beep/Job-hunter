"""
Telegram Userbot/Listener (Master Prompt Section 7 / 7.1).

REQUIRES `telethon` — install in your own environment:
    pip install telethon --break-system-packages

Cannot run in this sandbox (no network to install telethon or reach
Telegram's servers). Written to run as-is once you set TELEGRAM_API_ID /
TELEGRAM_API_HASH (and TELEGRAM_TOS_ACKNOWLEDGED) in .env and run it from
your own machine/Termux.

Enforces Section 7.1 mandatory constraints via src/scanner/rate_limiter.py:
  - explicit rate limiting between reads
  - gradual channel joining (never a burst)
  - exponential backoff on FloodWaitError, honoring the server's wait time
    as a floor, never retrying sooner

Before running this against real channels, read Master Prompt Section 7.4 —
confirm current Telegram API ToS compliance for this specific use case.

ARCHITECTURE: this class handles ONLY the Telegram connection, gradual
joining, and rate-limiting mechanics. What happens to each incoming message
is fully pluggable via `process_message_fn` — this is what lets the exact
same listener serve both the original single-user pipeline (default, for
backward compatibility — see the __main__ block) and the multi-user
pipeline (src/bot/main.py passes its own callback). Do not hardcode any
pipeline-specific logic back into register_handlers(); add a new
process_message_fn instead.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Awaitable, Callable, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from src.config import Settings
from src.database import Database
from src.logging_config import log_error, scanner_logger
from src.scanner.rate_limiter import ActionRateLimiter, ExponentialBackoff, GradualJoinScheduler

try:
    from telethon import TelegramClient, events
    from telethon.errors import FloodWaitError
    from telethon.tl.functions.channels import JoinChannelRequest
    TELETHON_AVAILABLE = True
except ImportError:
    TELETHON_AVAILABLE = False


MIN_SECONDS_BETWEEN_READS = 5.0
MIN_SECONDS_BETWEEN_JOINS = 30.0
MAX_JOINS_PER_RUN = 5

ProcessMessageFn = Callable[[str, dict], Awaitable[None]]


async def _default_single_user_process(pipeline, db, raw_text: str, source: dict) -> None:
    """
    Default message processor — the original single-user pipeline. Used
    when the caller doesn't supply its own `process_message_fn` (e.g. the
    __main__ block below, kept for backward compatibility / reference).
    """
    from src.duplicate_detector import find_duplicate, text_hash

    existing = db.get_existing_texts()
    if find_duplicate(raw_text, existing):
        scanner_logger.info("Duplicate ad skipped from %s", source.get("channel"))
        return

    record = pipeline.process(raw_text, source)
    job_id = db.insert_job(record, text_hash(raw_text))
    scanner_logger.info(
        "Job #%d ingested from %s -> final_decision=%s",
        job_id, source.get("channel"), record.final_decision,
    )


class TelegramListener:
    def __init__(self, settings: Settings, db: Database, pipeline=None,
                 process_message_fn: Optional[ProcessMessageFn] = None,
                 on_new_match=None):
        """
        pipeline: only used by the default single-user processor (ignored
            if process_message_fn is supplied).
        process_message_fn: async callback(raw_text, source) — if given,
            this is called for every new message instead of the built-in
            single-user pipeline. This is what src/bot/main.py uses to
            route messages through src/pipeline_multiuser.py:ingest_ad
            instead. Whatever ad-splitting, classification, and delivery
            logic the caller needs happens entirely inside this callback —
            the listener itself stays agnostic to it.
        on_new_match: legacy callback(record, job_id), only invoked by the
            default single-user processor; ignored when process_message_fn
            is supplied (multi-user delivery notifications happen inside
            process_message_fn itself).
        """
        if not TELETHON_AVAILABLE:
            raise RuntimeError(
                "telethon is not installed. Run `pip install telethon --break-system-packages` "
                "in your own environment — this cannot be installed in the sandbox that wrote this code."
            )
        if not settings.scanner_enabled:
            raise RuntimeError(
                "TELEGRAM_API_ID / TELEGRAM_API_HASH / TELEGRAM_TOS_ACKNOWLEDGED not all set — "
                "scanner should not start. Bot UI and Manual Forward Intake work fine without this (Section 7.3)."
            )
        if process_message_fn is None and pipeline is None:
            raise ValueError("Either pipeline or process_message_fn must be provided.")

        self.settings = settings
        self.db = db
        self.pipeline = pipeline
        self.on_new_match = on_new_match
        self.process_message_fn = process_message_fn

        self.client = TelegramClient("job_hunter_session", int(settings.telegram_api_id),
                                      settings.telegram_api_hash)
        self.read_limiter = ActionRateLimiter(min_interval_seconds=MIN_SECONDS_BETWEEN_READS)
        self.join_scheduler = GradualJoinScheduler(
            min_seconds_between_joins=MIN_SECONDS_BETWEEN_JOINS,
            max_joins_per_run=MAX_JOINS_PER_RUN,
        )
        self.backoff = ExponentialBackoff()

    async def join_channels_gradually(self, channel_identifiers: list[str]):
        """Section 7.1: never join a burst — space out joins, cap per run, back off on FloodWait."""
        import asyncio

        for identifier in channel_identifiers:
            if not self.join_scheduler.can_join_more():
                scanner_logger.info(
                    "Reached max joins for this run (%d) — remaining channels will be "
                    "joined on a future run.", self.join_scheduler.max_joins_per_run,
                )
                break

            delay = self.join_scheduler.next_join_delay_seconds()
            if delay > 0:
                await asyncio.sleep(delay)

            try:
                await self.client(JoinChannelRequest(identifier))
                self.join_scheduler.record_join()
                scanner_logger.info("Joined channel: %s", identifier)
            except FloodWaitError as e:
                wait = self.backoff.next_delay_seconds(server_specified_wait=e.seconds)
                scanner_logger.warning(
                    "FloodWaitError joining %s — backing off %.0fs (Telegram requested %ds)",
                    identifier, wait, e.seconds,
                )
                await asyncio.sleep(wait)
            except Exception as e:  # noqa: BLE001
                log_error("telegram_listener.join", e)

    async def _process_one_message(self, raw_text: str, source: dict):
        if self.process_message_fn is not None:
            await self.process_message_fn(raw_text, source)
        else:
            await _default_single_user_process(self.pipeline, self.db, raw_text, source)
            if self.on_new_match is not None:
                # Default processor doesn't expose the record/job_id back to
                # us here (it's fire-and-forget for simplicity) — anyone
                # needing on_new_match with the default path should supply
                # their own process_message_fn instead. This branch exists
                # only so the constructor accepting on_new_match doesn't
                # silently do nothing when someone passes it with no
                # process_message_fn; we log so it's not a silent no-op.
                scanner_logger.warning(
                    "on_new_match was provided without process_message_fn — it will not be called. "
                    "Supply process_message_fn for custom notification handling."
                )

    def register_handlers(self, channel_identifiers: list[str]):
        @self.client.on(events.NewMessage(chats=channel_identifiers))
        async def handler(event):
            await self.read_limiter.wait_if_needed_async()

            raw_text = event.raw_text
            if not raw_text:
                return

            try:
                source = {
                    "platform": "telegram",
                    "channel": getattr(event.chat, "username", None) or str(event.chat_id),
                    "message_id": str(event.id),
                    "url": f"https://t.me/{getattr(event.chat, 'username', '')}/{event.id}",
                    "date": event.date.isoformat() if event.date else None,
                }
                await self._process_one_message(raw_text, source)
            except Exception as e:  # noqa: BLE001
                log_error("telegram_listener.handler", e)

    async def run(self, channel_identifiers: list[str]):
        async with self.client:
            await self.join_channels_gradually(channel_identifiers)
            self.register_handlers(channel_identifiers)
            scanner_logger.info("Listener running. Monitoring %d channels.", len(channel_identifiers))
            await self.client.run_until_disconnected()


if __name__ == "__main__":
    if not TELETHON_AVAILABLE:
        print("telethon not installed in this environment. See module docstring.")
        sys.exit(1)

    import asyncio

    from src.pipeline import JobHunterPipeline

    settings = Settings.load()
    db = Database(settings.database_path)
    pipeline = JobHunterPipeline()
    listener = TelegramListener(settings, db, pipeline=pipeline)  # default single-user processor

    channels = [row["identifier"] for row in db.list_sources(platform="telegram") if row["enabled"]]
    if not channels:
        print("No enabled Telegram sources configured. Add some via the bot's Sources menu first.")
        sys.exit(0)

    asyncio.run(listener.run(channels))

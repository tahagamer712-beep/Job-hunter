"""
Rate-limited notification delivery — protects against Telegram's rate
limits (roughly 30 messages/second globally, ~1/second sustained per chat)
when a job matches many users at once, or an admin broadcasts.

Handles telegram.error.RetryAfter gracefully: waits exactly as long as
Telegram says, never guesses a shorter wait, and continues rather than
aborting the whole batch over one throttled recipient.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Awaitable, Callable, List, Tuple

DEFAULT_MIN_INTERVAL_SECONDS = 0.05   # ~20 messages/sec — safely under Telegram's global cap


@dataclass
class DeliveryOutcome:
    succeeded: List[str]
    failed: List[Tuple[str, str]]  # (recipient, error message)


async def deliver_with_rate_limit(
    recipients: List[str],
    send_fn: Callable[[str], Awaitable[None]],
    min_interval_seconds: float = DEFAULT_MIN_INTERVAL_SECONDS,
    sleep_fn: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> DeliveryOutcome:
    """
    send_fn(recipient) does the actual telegram send for one recipient and
    should raise on failure. RetryAfter-style exceptions (anything with a
    `.retry_after` attribute, matching python-telegram-bot's RetryAfter) are
    honored by waiting that long before retrying that same recipient once.
    """
    succeeded, failed = [], []

    for recipient in recipients:
        try:
            await send_fn(recipient)
            succeeded.append(recipient)
        except Exception as e:  # noqa: BLE001
            retry_after = getattr(e, "retry_after", None)
            if retry_after:
                await sleep_fn(float(retry_after))
                try:
                    await send_fn(recipient)
                    succeeded.append(recipient)
                except Exception as e2:  # noqa: BLE001
                    failed.append((recipient, f"{type(e2).__name__}: {e2}"))
            else:
                failed.append((recipient, f"{type(e).__name__}: {e}"))

        await sleep_fn(min_interval_seconds)

    return DeliveryOutcome(succeeded=succeeded, failed=failed)

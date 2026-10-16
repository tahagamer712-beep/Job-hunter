"""
Rate-limiting logic for the Telegram Userbot/Listener (Master Prompt
Section 7.1). Deliberately has ZERO dependency on telethon or any network
library, so it is fully unit-testable in this sandbox even though the
listener itself (telegram_listener.py) cannot run here.

Enforced constraints:
  - Explicit rate limiting on read/join actions (min interval between actions)
  - Gradual channel joining (never join a burst of channels at once)
  - Exponential backoff on FloodWait / any platform-imposed restriction
  - No retry-immediately / ignore-the-wait behavior
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable, List, Optional


@dataclass
class GradualJoinScheduler:
    """
    Spaces out channel joins instead of joining in a burst. Call
    `next_join_delay_seconds()` before each join attempt and sleep that long.
    """
    min_seconds_between_joins: float = 30.0
    max_joins_per_run: int = 5
    _joins_this_run: int = field(default=0, init=False)

    def can_join_more(self) -> bool:
        return self._joins_this_run < self.max_joins_per_run

    def record_join(self) -> None:
        self._joins_this_run += 1

    def next_join_delay_seconds(self) -> float:
        """Delay to wait before the NEXT join attempt (0 for the very first)."""
        return 0.0 if self._joins_this_run == 0 else self.min_seconds_between_joins


@dataclass
class ExponentialBackoff:
    """
    Exponential backoff for FloodWaitError / rate-limit responses. Never
    retries immediately and never ignores a server-specified wait time —
    when the platform tells you how long to wait (e.g. FloodWaitError.seconds
    in telethon), that value must be honored as a FLOOR, not overridden by a
    shorter computed backoff.
    """
    base_seconds: float = 5.0
    max_seconds: float = 900.0     # 15 minutes ceiling
    multiplier: float = 2.0
    _attempt: int = field(default=0, init=False)

    def reset(self) -> None:
        self._attempt = 0

    def next_delay_seconds(self, server_specified_wait: float = 0.0) -> float:
        computed = min(self.base_seconds * (self.multiplier ** self._attempt), self.max_seconds)
        self._attempt += 1
        return max(computed, server_specified_wait)


@dataclass
class ActionRateLimiter:
    """Generic minimum-interval-between-actions limiter for polling/reads."""
    min_interval_seconds: float
    _last_action_time: Optional[float] = field(default=None, init=False)

    def wait_if_needed(self, sleep_fn: Callable[[float], None] = time.sleep,
                        now_fn: Callable[[], float] = time.monotonic) -> float:
        now = now_fn()
        if self._last_action_time is None:
            remaining = 0.0
        else:
            elapsed = now - self._last_action_time
            remaining = max(0.0, self.min_interval_seconds - elapsed)
        if remaining > 0:
            sleep_fn(remaining)
        self._last_action_time = now_fn()
        return remaining

    async def wait_if_needed_async(self, now_fn: Callable[[], float] = time.monotonic) -> float:
        """
        Async counterpart of wait_if_needed — actually awaits the remaining
        interval via asyncio.sleep, instead of relying on a caller-supplied
        sleep_fn (which, if passed a no-op like `lambda s: None`, silently
        defeats the whole rate limiter — this was a real bug in the
        Telegram listener that this method fixes).
        """
        import asyncio
        now = now_fn()
        if self._last_action_time is None:
            remaining = 0.0
        else:
            elapsed = now - self._last_action_time
            remaining = max(0.0, self.min_interval_seconds - elapsed)
        if remaining > 0:
            await asyncio.sleep(remaining)
        self._last_action_time = now_fn()
        return remaining
